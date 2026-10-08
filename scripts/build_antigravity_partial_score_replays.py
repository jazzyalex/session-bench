#!/usr/bin/env python3
"""Build closed private score successors for the three retained Antigravity captures."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.antigravity_score_inputs import ASSERTION_SCHEMA, observer_from_capture_documents
from session_bench.native_replay import canonical
from session_bench.score_replay import SCHEMA, build_score_replay_package, replay_score_package


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError('successor output must be new')
    output.mkdir(parents=True)
    runs = []
    for repetition, suffix in enumerate(('02', '03', '04'), 1):
        capture = ROOT / 'artifacts/v1-expanded-preparation/live-captures' / f'antigravity-2026-09-29-{suffix}'
        qualified = capture / 'qualification-v4'
        documents = {p.relative_to(qualified).as_posix(): p.read_bytes() for p in qualified.rglob('*') if p.is_file() and not p.is_symlink()}
        documents['workspace/fixture_project/checkout.py'] = (capture / 'workspace/fixture_project/checkout.py').read_bytes()
        state = json.loads(documents['capture-result.json'])
        native_documents = {name: data for name, data in documents.items() if name.startswith('native/')}
        if not native_documents or 'native/' + state['native_primary'] not in native_documents:
            raise ValueError('selected Antigravity native source absent')
        workspace = str(capture / 'workspace')
        observer = canonical(observer_from_capture_documents(documents, workspace))
        native_inventory = {'artifacts': [{'path': 'capture/' + name.removeprefix('native/'), 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(native_inventory) + b'\n'
        assertion = {'schema_version': ASSERTION_SCHEMA, 'run_id': state['attempt_id'], 'repetition': repetition,
                     'native_inventory_sha256': sha(inventory_bytes), 'captured_workspace': workspace,
                     'input_sha256': {'workload.json': sha(documents['workload.json']), 'observer.json': sha(observer)},
                     'capture_documents': [{'path': name, 'sha256': sha(data), 'size_bytes': len(data)} for name, data in sorted(documents.items())]}
        supporting = {'capture-assertion.json': canonical(assertion) + b'\n'}
        supporting.update({'capture/' + name: data for name, data in documents.items()})
        context = {'schema_version': SCHEMA, 'configuration_id': 'antigravity', 'repetition': repetition,
                   'run_id': state['attempt_id'], 'build': state['version'], 'collected_on': '2026-09-29',
                   'result_id': capture.name + '-partial-current-source-score-replay',
                   'observer_kind': 'antigravity-capture-v1', 'complete_record_family': False, 'complete_root': False,
                   'required_companions': [], 'root_repetitions': None, 'capture_assertion_path': 'inputs/capture-assertion.json',
                   'claude_projection': None}
        with tempfile.TemporaryDirectory(prefix='bench-antigravity-native-', dir=output) as temp:
            native = Path(temp)
            (native / 'decode.json').write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                path = native / 'capture' / name.removeprefix('native/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            packet = output / capture.name
            manifest = build_score_replay_package(native, packet, workload_document=documents['workload.json'],
                observer_document=observer, context_document=canonical(context), supporting_documents=supporting)
        pin = sha((packet / 'manifest.json').read_bytes())
        receipt = replay_score_package(packet, expected_manifest_sha256=pin)
        (output / (capture.name + '-receipt.json')).write_bytes(canonical(receipt) + b'\n')
        metrics = receipt['diagnostics']['intact']['metrics']
        runs.append({'run_id': state['attempt_id'], 'repetition': repetition, 'packet': packet.name,
                     'manifest_sha256': pin, 'diagnostics_sha256': manifest['expected_diagnostics_sha256'],
                     'metric_count': len(metrics), 'resolved_metric_count': sum(row['state'] not in ('unresolved', 'decoder_unsupported', 'invalid_capture') for row in metrics),
                     'unresolved_metric_ids': [row['id'] for row in metrics if row['state'] in ('unresolved', 'decoder_unsupported', 'invalid_capture')],
                     'selected_loss_detected': receipt['diagnostics']['selected_loss']['response_correctness_reduced']})
    summary = {'schema_version': 'session-bench-antigravity-partial-score-successor-v1',
               'scope': 'private_native_to_score_diagnostics', 'public_safe': False, 'independent_reproduction': False,
               'historical_inputs_overwritten': False, 'overall_rank': None, 'runs': runs}
    (output / 'summary.json').write_bytes(canonical(summary) + b'\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2, sort_keys=True))
