#!/usr/bin/env python3
"""Build private offline response usage evidence from retained Antigravity captures."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.antigravity_response_usage import project_final_response_usage


def build(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError('output must be a new path')
    projections = []
    for suffix in ('02', '03', '04'):
        capture = ROOT / 'artifacts/v1-expanded-preparation/live-captures' / f'antigravity-2026-09-29-{suffix}' / 'qualification-v4'
        manifest = json.loads((capture / 'manifest.json').read_bytes())
        indexed = {entry['path']: entry for entry in manifest['files']}
        observer_bytes = (capture / 'observer.json').read_bytes()
        if hashlib.sha256(observer_bytes).hexdigest() != indexed['observer.json']['sha256']:
            raise ValueError('retained observer changed')
        observer = json.loads(observer_bytes)
        responses = {event['id']: event['fields']['text'] for event in observer['events']
                     if event['kind'] == 'assistant_response'}
        if set(responses) != {'response-r1', 'response-r2'}:
            raise ValueError('retained observer responses ambiguous')
        state = json.loads((capture / 'capture-result.json').read_bytes())
        session_id = state['native_primary'].split('/', 1)[0]
        for turn in (1, 2):
            name = f'r{turn}.stdout.jsonl'
            raw = (capture / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != indexed[name]['sha256'] or len(raw) != indexed[name]['size_bytes']:
                raise ValueError('retained stdout changed')
            projected = project_final_response_usage(raw, expected_response=responses[f'response-r{turn}'],
                                                      expected_session_id=session_id)
            projected.update(run_id=manifest['attempt_id'], repetition=manifest['repetition'], turn=f'r{turn}',
                             response_id=f'response-r{turn}', observer_sha256=hashlib.sha256(observer_bytes).hexdigest())
            projections.append(projected)
    report = {
        'schema_version': 'session-bench-antigravity-response-usage-projection-v1',
        'scope': 'private_offline_source_bound', 'score_replay_changed': False,
        'metric_assessment': {
            'attribution.usage': 'response-scoped usage boundary established for all six displayed responses; frozen score replay remains unchanged',
            'attribution.token_semantics': 'unresolved: cache_write missing and provider token semantics not independently established',
            'attribution.model_config': 'unresolved: selected model identity not observed',
            'attribution.reconciliation': 'unresolved: result usage is cumulative and no independent response-to-session reconciliation is proven',
        },
        'projections': projections,
    }
    output.mkdir(parents=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps({'output': str(args.output), 'responses': len(result['projections'])}))
