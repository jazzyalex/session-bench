#!/usr/bin/env python3
"""Build the three private Antigravity CLI score replay packets from the whole-state captures.

Each packet holds every file whose path carries the conversation id (the
conversation database, the ``brain/<id>/`` directory, the annotation and the
presence lock), the independent stdout streams, the helper ledger, the edited
file, the whole-state receipt of the second turn and its two metadata
inventories. The run-owned log and implicit files stay in the capture
directory; no metric reads them. Every packet is replayed under the OS sandbox
and passes the tamper controls. The packets are private.

Designated runs: the first three captures that the whole-state controller
completed (``antigravity-2026-10-05-02``, ``-03``, ``-04``). ``-01`` of that
day stopped closed after turn 1 on a controller rule gap. The attempts of
2026-10-04 listed ``brain/`` only and their packets were rejected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.antigravity_root_evidence import verify_state_capture
from session_bench.antigravity_score_inputs import observer_from_capture_documents
from session_bench.antigravity_state_inputs import ASSERTION_SCHEMA_V3, EXTRACT, OBSERVER_KIND_V3, state_root_row
from session_bench.native_replay import canonical
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ('antigravity-2026-10-05-02', 'antigravity-2026-10-05-03', 'antigravity-2026-10-05-04')
CAPTURES = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
HELPER = ROOT / 'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
COLLECTED_ON = '2026-10-05'
RETAINED = ('workload.json', 'capture-start.json', 'capture-result.json', 'r1.stdout.jsonl', 'r2.stdout.jsonl',
            'r1.stderr.txt', 'r2.stderr.txt', 'r2-native-receipt.json', 'state-before.json', 'r2-state-after.json')
RESOLVED = ('measured', 'native_absent', 'contradiction')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_documents(capture: Path) -> dict[str, bytes]:
    """The capture documents of one run, after the offline checks of the receipts."""
    documents = {name: (capture / name).read_bytes() for name in RETAINED}
    fixture = capture / 'workspace/fixture_project'
    documents['helper-ledger.jsonl'] = (fixture / '.survival-observer.jsonl').read_bytes()
    documents['bench_check.py'] = (fixture / 'bench_check.py').read_bytes()
    documents['workspace/fixture_project/checkout.py'] = (fixture / 'checkout.py').read_bytes()
    # The shared-store rows of this conversation, read after the capture. Stored in canonical form.
    documents[EXTRACT] = canonical(json.loads((capture / 'shared-store-extract/extract.json').read_bytes()))
    if documents['bench_check.py'] != HELPER.read_bytes():
        raise ValueError('captured helper differs from the frozen helper')
    before = json.loads(documents['state-before.json'])
    receipts = {}
    for turn in (1, 2):
        # Each receipt is checked against its inventories and its private copy, run-owned files included.
        receipts[turn] = json.loads((capture / f'r{turn}-native-receipt.json').read_bytes())
        verify_state_capture(receipts[turn], capture / f'r{turn}-native', before=before,
                             after=json.loads((capture / f'r{turn}-state-after.json').read_bytes()))
    if receipts[1]['conversation_id'] != receipts[2]['conversation_id']:
        raise ValueError('the two turns used different conversations')
    return documents


def build(output: Path, *, captures: Path = CAPTURES, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new destination required')
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(RUNS, 1):
        capture = captures / run
        documents = capture_documents(capture)
        state = json.loads(documents['capture-result.json'])
        receipt = json.loads(documents['r2-native-receipt.json'])
        primary = f"conversations/{state['conversation_id']}.db"
        native_documents = {row['relative_path']: (capture / 'r2-native' / row['copied_path']).read_bytes()
                            for row in receipt['artifacts'] if row['class'] == 'session_owned'}
        workspace = str(capture / 'workspace')
        observer_bytes = canonical(observer_from_capture_documents(documents, workspace, step_bound=True))
        inventory = {'artifacts': [{'path': 'capture/' + name, 'sha256': sha(data), 'size_bytes': len(data)}
                                   for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(inventory) + b'\n'
        assertion = {'schema_version': ASSERTION_SCHEMA_V3, 'run_id': state['attempt_id'], 'repetition': repetition,
                     'native_inventory_sha256': sha(inventory_bytes), 'captured_workspace': workspace,
                     'input_sha256': {'workload.json': sha(documents['workload.json']), 'observer.json': sha(observer_bytes)},
                     'capture_documents': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)}
                                           for name, data in sorted(documents.items())]}
        supporting = {'capture-assertion.json': canonical(assertion) + b'\n'}
        supporting.update({'capture/' + name: data for name, data in documents.items()})
        context = {'schema_version': SCHEMA, 'configuration_id': 'antigravity', 'repetition': repetition,
                   'run_id': state['attempt_id'], 'build': state['version'], 'collected_on': COLLECTED_ON,
                   'result_id': run + '-native-score-replay', 'observer_kind': OBSERVER_KIND_V3,
                   # Shared stores changed and were not read: the family is complete, the root is not.
                   'complete_record_family': True, 'complete_root': False,
                   'required_companions': sorted('capture/' + name for name in native_documents if name != primary),
                   'root_repetitions': [state_root_row(repetition)],
                   'capture_assertion_path': 'inputs/capture-assertion.json', 'claude_projection': None}
        with tempfile.TemporaryDirectory(prefix='bench-antigravity-native-') as directory:
            native = Path(directory).resolve()
            (native / 'decode.json').write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / 'capture' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(native, packet, workload_document=documents['workload.json'],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / 'manifest.json').read_bytes())
        replayed = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls['status'] != 'passed':
            raise ValueError('Antigravity packet tamper controls failed')
        (output / f'{run}-receipt.json').write_bytes(canonical(replayed) + b'\n')
        (output / f'{run}-tamper.json').write_bytes(canonical(controls) + b'\n')
        metrics = replayed['diagnostics']['intact']['metrics']
        unresolved = [row['id'] for row in metrics if row['state'] not in RESOLVED]
        summary.append({'run_id': state['attempt_id'], 'repetition': repetition, 'packet': packet.name,
                        'manifest_sha256': pinned, 'diagnostics_sha256': manifest['expected_diagnostics_sha256'],
                        'metric_count': len(metrics), 'resolved_metric_count': len(metrics) - len(unresolved),
                        'unresolved_metric_ids': unresolved, 'os_sandboxed': replayed['os_sandboxed'],
                        'tamper_controls': controls['status'],
                        'selected_loss_detected': replayed['diagnostics']['selected_loss']['response_correctness_reduced']})
    result = {'schema_version': 'session-bench-antigravity-score-replay-v1', 'scope': 'private_native_to_score_diagnostics',
              'public_safe': False, 'independent_reproduction': False, 'overall_rank': None, 'runs': summary}
    (output / 'summary.json').write_bytes(canonical(result) + b'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
