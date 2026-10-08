#!/usr/bin/env python3
"""Build the three private OpenClaw score replay packets from the whole-home ACP captures.

The record of an OpenClaw session is its rows in the shared agent store
``openclaw-agent.sqlite``. Each packet holds the row export and the schema
export of the capture (``r2-native/``), the independent capture documents (the
submitted prompts, the ACP stream of each turn, the helper ledger and the
workspace copies, the gateway and fixture receipts), and the whole-home
receipt with its two metadata inventories. The observer is built from the ACP
streams. Every packet is replayed under the OS sandbox and passes the tamper
controls. The packets are private.

The files of the Codex thread (rollout, shell snapshot, lock) are outside the
read and are not in the packets. The builder checks them against the receipt
in the capture directory and never parses them.

Designated runs: the first three captures on the route ``acp`` that the
controller completed in one pass with the final controller code
(``openclaw-2026-10-07-04``, ``-07``, ``-06`` as repetitions 1, 2, 3). Decided
before scoring. Disclosed and not used: ``-03`` (both turns complete; the
native bracket was refused by a guard fault and taken again after the fix),
``-05`` (turn 1 ended without a response; one model turn), ``-02`` (route
``local``: no tool events for the observer).
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
from session_bench.native_replay import canonical
from session_bench.openclaw_score_inputs import (
    AFTER, ASSERTION_SCHEMA, BEFORE, CAPTURE_DOCUMENTS, OBSERVER_KIND, RECEIPT, REQUIRED_COMPANIONS, family_findings,
    observer_from_capture_documents, state_root_row,
)
from session_bench.openclaw_session_rows import ROWS_FILE, SCHEMA_FILE, read_rows
from session_bench.openclaw_state_capture import verify_openclaw_state_capture
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ('openclaw-2026-10-07-04', 'openclaw-2026-10-07-07', 'openclaw-2026-10-07-06')
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
HELPER = ROOT / 'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
RESOLVED = ('measured', 'native_absent', 'contradiction')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_documents(capture: Path) -> dict[str, bytes]:
    """The capture documents of one run, after the offline check of the whole-home receipt."""
    documents = {name: (capture / name).read_bytes() for name in CAPTURE_DOCUMENTS}
    if documents['turn-r2/workspace/fixture_project/bench_check.py'] != HELPER.read_bytes():
        raise ValueError('captured helper differs from the frozen helper')
    # The receipt is checked against its inventories, its copies of the owned files, its row export and the deleted store copies.
    verify_openclaw_state_capture(json.loads(documents[RECEIPT]), capture / 'r2-native', before=json.loads(documents[BEFORE]),
                                  after=json.loads(documents[AFTER]))
    return documents


def build(output: Path, *, captures: Path = CAPTURES, runs=RUNS, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new destination required')
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(runs, 1):
        capture = captures / run
        documents = capture_documents(capture)
        plan = json.loads(documents['plan.json'])
        if plan['repetition'] != repetition or plan['attempt_id'] != run:
            raise ValueError('capture is not the designated repetition')
        native_documents = {'capture/' + name: (capture / 'r2-native' / name).read_bytes() for name in (ROWS_FILE, SCHEMA_FILE)}
        read, _, _, _, _ = read_rows(native_documents['capture/' + ROWS_FILE], native_documents['capture/' + SCHEMA_FILE])
        observer_bytes = canonical(observer_from_capture_documents(documents))
        # The observation date is the UTC date of the native session start (session_windows.created_at, Unix milliseconds).
        collected_on = datetime.fromtimestamp(read['session_windows'][0]['values']['created_at'] / 1000, tz=timezone.utc).date().isoformat()
        inventory = {'artifacts': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(inventory) + b'\n'
        assertion = {'schema_version': ASSERTION_SCHEMA, 'run_id': run, 'repetition': repetition,
                     'native_inventory_sha256': sha(inventory_bytes),
                     'input_sha256': {'workload.json': sha(documents['workload-instance.json']), 'observer.json': sha(observer_bytes)},
                     'family_limits': family_findings(json.loads(documents[RECEIPT])),
                     'capture_documents': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(documents.items())]}
        supporting = {'capture-assertion.json': canonical(assertion) + b'\n'}
        supporting.update({'capture/' + name: data for name, data in documents.items()})
        context = {'schema_version': SCHEMA, 'configuration_id': 'openclaw', 'repetition': repetition, 'run_id': run,
                   'build': json.loads(documents['preflight.json'])['version'], 'collected_on': collected_on,
                   'result_id': run + '-native-score-replay', 'observer_kind': OBSERVER_KIND,
                   # The stores hold other sessions and cannot be copied whole: the family is bound, the root is not complete.
                   'complete_record_family': True, 'complete_root': False,
                   'required_companions': REQUIRED_COMPANIONS,
                   'root_repetitions': [state_root_row(repetition)],
                   'capture_assertion_path': 'inputs/capture-assertion.json', 'claude_projection': None}
        with tempfile.TemporaryDirectory(prefix='bench-openclaw-native-') as directory:
            native = Path(directory).resolve()
            (native / 'decode.json').write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(native, packet, workload_document=documents['workload-instance.json'],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / 'manifest.json').read_bytes())
        replayed = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls['status'] != 'passed':
            raise ValueError('OpenClaw packet tamper controls failed')
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
    result = {'schema_version': 'session-bench-openclaw-score-replay-v1', 'scope': 'private_native_to_score_diagnostics',
              'public_safe': False, 'independent_reproduction': False, 'overall_rank': None, 'runs': summary}
    (output / 'summary.json').write_bytes(canonical(result) + b'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
