#!/usr/bin/env python3
"""Build new Claude packets with verified retained three-root provenance."""
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
from session_bench.claude_root_evidence import FILES, verify_claude_root_evidence
from session_bench.native_replay import canonical
from session_bench.score_replay import build_score_replay_package, replay_score_package


def build(output):
    output = Path(output)
    if output.exists():
        raise ValueError('new successor destination required')
    supporting = {}
    with tempfile.TemporaryDirectory(prefix='claude-root-proof-') as folder:
        proof = Path(folder)
        for repetition in (1, 2, 3):
            source = ROOT / f'artifacts/survival-v1-runs/claude-cli-eval-{repetition}'
            for name in FILES:
                relative = f'repetition-{repetition}/{name}'
                data = (source / name).read_bytes()
                target = proof / relative; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                supporting['root-evidence/' + relative] = data
        repetitions = verify_claude_root_evidence(proof)
    rows = []
    for repetition in (1, 2, 3):
        run = ROOT / f'artifacts/survival-v1-runs/claude-cli-eval-{repetition}'
        native, inputs = retained_inputs(run)
        context = json.loads(inputs['context_document'])
        context['root_repetitions'] = repetitions
        inputs['context_document'] = canonical(context)
        inputs['supporting_documents'].update(supporting)
        packet = output / run.name
        manifest = build_score_replay_package(native, packet, **inputs)
        pin = hashlib.sha256((packet / 'manifest.json').read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        (output / f'{run.name}-receipt.json').write_bytes(canonical(receipt))
        metrics = receipt['diagnostics']['intact']['metrics']
        rows.append({'packet': run.name, 'manifest_sha256': pin, 'metric_count': len(metrics),
                     'unresolved_metric_ids': [m['id'] for m in metrics if m['state'] in ('unresolved', 'decoder_unsupported', 'invalid_capture')]})
    summary = {'runs': rows, 'public_safe': False, 'independent_reproduction': False,
               'root_provenance': 'retained metadata-only capture receipts; not newly reacquired'}
    (output / 'summary.json').write_bytes(canonical(summary))
    print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
