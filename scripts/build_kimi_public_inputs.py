#!/usr/bin/env python3
"""Prepare Kimi public-input candidates; independent review remains mandatory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.kimi_wire_records import SESSION_DIR, STATE, WIRE, decode_kimi_native
from session_bench.native_replay import canonical
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA
from session_bench.release_score import score_release_run

RUNS = ('kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-06')
# The capture ran in a scratch directory under the temporary directory layout of the operating system.
OS_FRAGMENT = '/private/tmp/'
_LOCATIONS = {
    WIRE: 'the wire log of the session (one JSON object per line); the comparator joins by turnId, stepUuid, toolCallId and turnStep',
    STATE: 'the state file of the session: the session id and the version of the state file',
}


def _observer_ids(observer, metric_id):
    events = lambda kind: [row['id'] for row in observer['events'] if row['kind'] == kind and row['population_role'] == 'primary_scored']
    relations = lambda kind: [row['id'] for row in observer['relations'] if row['kind'] == kind]
    turns, responses = events('user_turn'), events('assistant_response')
    if metric_id.startswith('portable.'):
        # The root boundary is a capture receipt, not an observed event.
        return ['kimi-isolated-home-receipts', 'closed-replay-observer']
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


def build(source, output, *, runs=RUNS):
    source, output = Path(source), Path(output)
    pairs = []
    for repetition, run in enumerate(runs, 1):
        packet = source / run
        actual = json.loads((source / f'{run}-receipt.json').read_bytes())['diagnostics']['intact']
        observer = json.loads((packet / 'inputs/observer.json').read_bytes())
        native = json.loads((packet / 'native/decode.json').read_bytes())
        plan = json.loads((packet / 'inputs/capture/plan.json').read_bytes())
        decoded = decode_kimi_native(packet / 'native')
        models = {(row.get('model_id'), (row.get('configuration') or {}).get('model_alias')) for row in decoded['responses']}
        announced = (plan['model_alias'][len(plan['provider']) + 1:], plan['model_alias'])
        if decoded['status'] != 'ok' or models != {announced}:
            raise ValueError('unstable captured model identity')
        if not str(plan.get('kimi_code_home')).startswith(OS_FRAGMENT):
            raise ValueError('captured OS not established')
        locators = [{'artifact_id': 'native/' + row['path'], 'artifact_sha256': row['sha256'],
                     'record_location': _LOCATIONS.get(row['path'][len(SESSION_DIR) + 1:], 'a file of the session directory outside the read; counted by size')}
                    for row in native['artifacts']]
        read = [row for row in locators if row['artifact_id'][len('native/' + SESSION_DIR) + 1:] in _LOCATIONS]
        refs = [{'metric_id': metric['id'], 'observer_ids': _observer_ids(observer, metric['id']),
                 'native_locators': locators if metric['id'].startswith('portable.') else read}
                for metric in actual['metrics'] if not metric['id'].startswith('broad.')]
        profile = actual['format_evidence']
        survival = {'schema_version': RELEASE_EVIDENCE_SCHEMA_VERSION, 'protocol_version': '1.0-survival', 'workload_version': '1.0-survival-workload', 'rubric_version': '1.0-survival-rubric',
                    'run_id': actual['run_id'], 'configuration_id': 'kimi', 'repetition': repetition, 'capture_id': packet.name, 'evaluation_id': 'native-score-replay:' + packet.name,
                    'observer': profile['observer'], 'native_manifest': profile['native_manifest'],
                    'decoder': {'id': decoded['format'], 'sha256': hashlib.sha256((packet / 'runtime/session_bench/kimi_wire_records.py').read_bytes()).hexdigest()},
                    'identity': {'provider': plan['provider'], 'harness': 'kimi', 'surface': 'cli', 'execution_mode': 'headless', 'os': 'macOS', 'build': profile['build'],
                                 'model': announced[0], 'configuration': announced[1], 'observer_schema_version': observer['schema_version']},
                    'measurement': actual['measurement'], 'metric_evidence': refs}
        score_release_run(survival, profile)
        pairs.append({'survival': survival, 'format': profile})
    bundle = canonical({'schema_version': PUBLIC_BUNDLE_SCHEMA, 'configuration_id': 'kimi', 'pairs': pairs})
    with output.open('xb') as stream:
        stream.write(bundle)
    return {'output': str(output), 'sha256': hashlib.sha256(bundle).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.source, arguments.output)))
