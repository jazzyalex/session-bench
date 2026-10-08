#!/usr/bin/env python3
"""Prepare Cursor CLI public-input candidates; independent review remains mandatory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.cursor_cli_live import decode_cursor_cli_native
from session_bench.native_replay import canonical
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA
from session_bench.release_score import score_release_run

RUNS = ('cursor-cli-2026-10-06-r1', 'cursor-cli-2026-10-06-r2', 'cursor-cli-2026-10-06-r3')
# The injected context message of the store names the operating system. This fragment is kept by the sanitizer.
OS_FRAGMENT = 'OS Version: darwin'


def _observer_ids(observer, metric_id):
    events = lambda kind: [row['id'] for row in observer['events'] if row['kind'] == kind and row['population_role'] == 'primary_scored']
    relations = lambda kind: [row['id'] for row in observer['relations'] if row['kind'] == kind]
    turns, responses = events('user_turn'), events('assistant_response')
    if metric_id.startswith('portable.'):
        # The root boundary is a capture receipt, not a stream event.
        return ['cursor-isolated-root-receipts', 'closed-replay-observer']
    ids = {'work.submitted_turns': turns, 'work.visible_responses': responses, 'work.actions': events('action'),
           'work.results': events('result'), 'work.changed_files': events('file_change'),
           'causal.action_result': relations('action_result'), 'causal.turn_response': relations('turn_response'),
           'revision.r1': turns[:1], 'revision.r2': turns[1:2],
           'revision.r1_r2_order': relations('supersedes'), 'revision.final_after_r2': relations('final_after'),
           # Native-attested metrics: the observer supplies the displayed responses they join to.
           'attribution.model_config': responses, 'attribution.usage': responses,
           'attribution.token_semantics': responses, 'attribution.reconciliation': responses}.get(metric_id)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('missing or ambiguous observer locator ' + metric_id)
    return ids


def build(source, output):
    source, output = Path(source), Path(output)
    pairs = []
    for repetition, run in enumerate(RUNS, 1):
        packet = source / run
        actual = json.loads((source / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']
        observer = json.loads((packet / 'inputs/observer.json').read_bytes())
        native = json.loads((packet / 'native/decode.json').read_bytes())
        decoded = decode_cursor_cli_native(packet / 'native')
        models = {row.get('model_id') for row in decoded['responses'] if row.get('phase') == 'final_answer'}
        route = json.loads((packet / 'inputs/capture/plan.json').read_bytes()).get('model')
        if decoded['status'] != 'ok' or len(models) != 1 or None in models or not isinstance(route, str) or not route:
            raise ValueError('unstable captured model identity')
        store = next(row for row in native['artifacts'] if row['path'].endswith('/store.db'))
        if OS_FRAGMENT.encode() not in (packet / 'native' / store['path']).read_bytes():
            raise ValueError('captured OS not established')
        locators = [{'artifact_id': 'native/' + row['path'], 'artifact_sha256': row['sha256'],
                     'record_location': 'chat store: meta row and content-addressed blobs; the comparator joins by user message id, step blob id and tool call id' if row is store
                     else 'sidecar of the chat store: declared schema version and session times' if row['path'].endswith('/meta.json')
                     else 'agent transcript of the session; a second copy, read only by the absence scan'} for row in native['artifacts']]

        def cited(metric_id):
            if metric_id.startswith('portable.'):
                return locators
            return [row for row in locators if row['artifact_id'].endswith(('/store.db', '/meta.json'))]
        refs = [{'metric_id': metric['id'], 'observer_ids': _observer_ids(observer, metric['id']), 'native_locators': cited(metric['id'])}
                for metric in actual['metrics'] if not metric['id'].startswith('broad.')]
        profile = actual['format_evidence']
        survival = {'schema_version': RELEASE_EVIDENCE_SCHEMA_VERSION, 'protocol_version': '1.0-survival', 'workload_version': '1.0-survival-workload', 'rubric_version': '1.0-survival-rubric',
                    'run_id': actual['run_id'], 'configuration_id': 'cursor-cli', 'repetition': repetition, 'capture_id': packet.name, 'evaluation_id': 'native-score-replay:' + packet.name,
                    'observer': profile['observer'], 'native_manifest': profile['native_manifest'],
                    'decoder': {'id': decoded['format'], 'sha256': hashlib.sha256((packet / 'runtime/session_bench/cursor_cli_live.py').read_bytes()).hexdigest()},
                    'identity': {'provider': 'cursor', 'harness': 'cursor', 'surface': 'cli', 'execution_mode': 'headless', 'os': 'macOS', 'build': profile['build'],
                                 'model': next(iter(models)), 'configuration': route, 'observer_schema_version': observer['schema_version']},
                    'measurement': actual['measurement'], 'metric_evidence': refs}
        score_release_run(survival, profile)
        pairs.append({'survival': survival, 'format': profile})
    bundle = canonical({'schema_version': PUBLIC_BUNDLE_SCHEMA, 'configuration_id': 'cursor-cli', 'pairs': pairs})
    with output.open('xb') as stream:
        stream.write(bundle)
    return {'output': str(output), 'sha256': hashlib.sha256(bundle).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.source, arguments.output)))
