#!/usr/bin/env python3
"""Build the three private Kimi score replay packets from the isolated captures.

The record of a Kimi session is its session directory in the isolated home.
Each packet holds every file of that directory as copied after turn 2
(``native/session/``), the independent capture documents (launch and exit
receipts, the stdout stream and the stderr of each turn, the helper ledger and
the workspace copies) and the proof of the three index files of the home. The
observer is built from the stdout streams. Every packet is replayed under the
OS sandbox and passes the tamper controls. The packets are private: a failed
model request in the native record names the provider organisation.

Designated runs: the three captures that the controller completed with both
turns valid (``kimi-2026-10-08-01``, ``-02``, ``-06`` as repetitions 1, 2, 3).
Not used: ``kimi-2026-10-07-01`` and ``-02`` (the controller forced one attempt
per step and a provider rate limit ended turn 2), ``kimi-2026-10-08-03`` and
``-04`` (the rate limit outlasted Kimi's retries) and ``kimi-2026-10-08-05``
(the final response of turn 1 has no canary). See the adapter document.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.kimi_score_inputs import (
    ASSERTION_SCHEMA, CAPTURE_DOCUMENTS, CONTROLLER_SOURCE, HOME_PROOF, OBSERVER_KIND, capture_build, home_files_not_copied, home_index_proof,
    observer_from_capture_documents, root_row,
)
from session_bench.kimi_wire_records import SESSION_DIR, WIRE, read_wire
from session_bench.native_replay import canonical
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ('kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-06')
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
CONTROLLER = ROOT / 'session_bench/kimi_survival_capture.py'
HELPER = ROOT / 'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
RESOLVED = ('measured', 'native_absent', 'contradiction')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def native_documents(capture: Path) -> dict[str, bytes]:
    """Every file of the session directory as copied after turn 2; turn 1 must be a prefix of it."""
    source = capture / 'turn-r2/native-session'
    files = {SESSION_DIR + '/' + path.relative_to(source).as_posix(): path.read_bytes()
             for path in sorted(source.rglob('*')) if path.is_file() and not path.is_symlink()}
    first = (capture / 'turn-r1/native-session' / WIRE).read_bytes()
    if not files[SESSION_DIR + '/' + WIRE].startswith(first) or len(first) >= len(files[SESSION_DIR + '/' + WIRE]):
        raise ValueError('the wire log of turn 2 does not continue the wire log of turn 1')
    return files


def build(output: Path, *, captures: Path = CAPTURES, runs=RUNS, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new destination required')
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(runs, 1):
        capture = captures / run
        # The controller source is not in the capture directory. The plan holds its SHA-256; the replay checks it.
        documents = {name: (CONTROLLER if name == CONTROLLER_SOURCE else capture / name).read_bytes() for name in CAPTURE_DOCUMENTS}
        if documents['turn-r2/workspace/bench_check.py'] != HELPER.read_bytes():
            raise ValueError('captured helper differs from the frozen helper')
        plan = json.loads(documents['plan.json'])
        if plan['attempt_id'] != run:
            raise ValueError('capture is not the designated run')
        native = native_documents(capture)
        wire = native[SESSION_DIR + '/' + WIRE]
        observer_bytes = canonical(observer_from_capture_documents(documents))
        proof = home_index_proof(documents, wire)
        proof_bytes = canonical(proof) + b'\n'
        # The observation date is the UTC date of the native session start (created_at of the first wire record, Unix milliseconds).
        collected_on = datetime.fromtimestamp(read_wire(wire)[0][1]['created_at'] / 1000, tz=timezone.utc).date().isoformat()
        inventory = {'artifacts': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(native.items())]}
        inventory_bytes = canonical(inventory) + b'\n'
        listing = json.loads(documents['turn-r2/exit.json'])['kimi_home_inventory_after']
        assertion = {'schema_version': ASSERTION_SCHEMA, 'run_id': run, 'repetition': repetition,
                     'native_inventory_sha256': sha(inventory_bytes),
                     'input_sha256': {'workload.json': sha(documents['workload-instance.json']), 'observer.json': sha(observer_bytes),
                                      HOME_PROOF: sha(proof_bytes)},
                     'home_files_not_copied': home_files_not_copied(listing, plan, proof['session_id']),
                     'capture_documents': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(documents.items())]}
        supporting = {'capture-assertion.json': canonical(assertion) + b'\n', HOME_PROOF: proof_bytes}
        supporting.update({'capture/' + name: data for name, data in documents.items()})
        context = {'schema_version': SCHEMA, 'configuration_id': 'kimi', 'repetition': repetition, 'run_id': run,
                   'build': capture_build(documents), 'collected_on': collected_on,
                   'result_id': run + '-native-score-replay', 'observer_kind': OBSERVER_KIND,
                   # The root is the session directory of an isolated home; every file of it is in the packet.
                   'complete_record_family': True, 'complete_root': True,
                   'required_companions': sorted(name for name in native if name != SESSION_DIR + '/' + WIRE),
                   'root_repetitions': [root_row(repetition)],
                   'capture_assertion_path': 'inputs/capture-assertion.json', 'claude_projection': None}
        with tempfile.TemporaryDirectory(prefix='bench-kimi-native-') as directory:
            folder = Path(directory).resolve()
            (folder / 'decode.json').write_bytes(inventory_bytes)
            for name, data in native.items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(folder, packet, workload_document=documents['workload-instance.json'],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / 'manifest.json').read_bytes())
        replayed = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls['status'] != 'passed':
            raise ValueError('Kimi packet tamper controls failed')
        (output / f'{run}-receipt.json').write_bytes(canonical(replayed) + b'\n')
        (output / f'{run}-tamper.json').write_bytes(canonical(controls) + b'\n')
        metrics = replayed['diagnostics']['intact']['metrics']
        unresolved = [row['id'] for row in metrics if row['state'] not in RESOLVED]
        summary.append({'run_id': run, 'repetition': repetition, 'packet': packet.name,
                        'manifest_sha256': pinned, 'diagnostics_sha256': manifest['expected_diagnostics_sha256'],
                        'metric_count': len(metrics), 'resolved_metric_count': len(metrics) - len(unresolved),
                        'unresolved_metric_ids': unresolved, 'os_sandboxed': replayed['os_sandboxed'],
                        'tamper_controls': controls['status'],
                        'selected_loss_detected': replayed['diagnostics']['selected_loss']['response_correctness_reduced']})
    result = {'schema_version': 'session-bench-kimi-score-replay-v1', 'scope': 'private_native_to_score_diagnostics',
              'public_safe': False, 'independent_reproduction': False, 'overall_rank': None, 'runs': summary}
    (output / 'summary.json').write_bytes(canonical(result) + b'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
