#!/usr/bin/env python3
"""Create private successor packets from three exact retained Desktop families."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from build_retained_score_replays import retained_inputs
from session_bench.claude_desktop_root_evidence import FILES, verify_claude_desktop_root_evidence
from session_bench.native_replay import canonical
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('claude-desktop-eval-1-correction-1', 'claude-desktop-eval-2-correction-1', 'claude-desktop-eval-3')


def build(output):
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('new successor destination required')
    supporting = {}
    with tempfile.TemporaryDirectory(prefix='claude-desktop-root-proof-') as folder:
        proof = Path(folder).resolve()
        for repetition, name in enumerate(RUNS, 1):
            source = ROOT / 'artifacts/survival-v1-runs' / name
            for relative in FILES:
                data = (source / relative).read_bytes()
                path = f'repetition-{repetition}/{relative}'
                target = proof / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
                supporting['root-evidence/' + path] = data
        repetitions = verify_claude_desktop_root_evidence(proof)
    rows = []
    for repetition, name in enumerate(RUNS, 1):
        native, inputs = retained_inputs(ROOT / 'artifacts/survival-v1-runs' / name)
        context = json.loads(inputs['context_document'])
        context['root_repetitions'] = repetitions
        # The Desktop observer independently pins submitted turn boundaries and
        # visible responses. The native transcript retains response-scoped
        # usage with stable message/turn joins, so use the comparator's existing
        # retention/token-semantics contract without borrowing token values
        # from cumulative Desktop state.
        context['claude_projection']['usage_mode'] = 'full'
        inputs['context_document'] = canonical(context)
        inputs['supporting_documents'].update(supporting)
        packet = output / name
        manifest = build_score_replay_package(native, packet, **inputs)
        pin = hashlib.sha256((packet / 'manifest.json').read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        receipt['tamper_controls'] = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
        (output / f'{name}-receipt.json').write_bytes(canonical(receipt))
        metrics = receipt['diagnostics']['intact']['metrics']
        rows.append({'configuration_id': 'claude-desktop', 'repetition': repetition, 'packet': name, 'manifest_sha256': pin,
                     'metric_count': len(metrics), 'diagnostics_sha256': manifest['expected_diagnostics_sha256'],
                     'unresolved_metric_ids': [m['id'] for m in metrics if m['state'] in ('unresolved', 'decoder_unsupported', 'invalid_capture')],
                     'selected_loss_detected': True})
    summary = {'runs': rows, 'public_safe': False, 'independent_reproduction': False, 'historical_inputs_overwritten': False,
               'root_provenance': 'measured from each metadata-only inventory window and the native session identifiers; the source paths of the selected files were not recorded',
               'supersedes': 'claude-desktop-root-score-replay-v5 (invented compound-edit action and result; changed files and readable rationale reported absent)'}
    (output / 'summary.json').write_bytes(canonical(summary))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))
