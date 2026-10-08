"""Copilot native events and independent stdout/helper/filesystem observations.

Native decoding never receives observer bytes. Selected model auto and actual
model IDs remain separate. Session cumulative usage is retained as diagnostics
and never manufactured into response-scoped token records.

Native schema version: ``session.start.data.version``. The decoder contract
names version 1 and refuses any other value. ``copilotVersion`` is the
application build and is not used as a schema version.

Usage: ``assistant.message`` holds no token counts. The per-call usage records
are in the session store (see ``copilot_session_store``) and are attached by
the replay when the store is part of the copied root. ``session.usage_checkpoint``
holds, per model, the prompt-cache state of the last model call
(``prompt_tokens``, ``cache_read``, ``cache_write``; no output count). It is
kept as ``prompt_cache_fragments``, joined by ``model_call_id`` = ``apiCallId``,
and is a second witness for that call, not a usage record. ``session.shutdown``
holds cumulative session totals (``session_totals``).
"""
from __future__ import annotations
import hashlib
import json
import re
import shlex
from pathlib import Path

from .copilot_capture_qualification import _stream, _json_bytes
from .adapters.agent_session_native_decoder import decode_agent_native_bytes
from .live_metric_comparator import _validate_observer


def sha(data): return hashlib.sha256(data).hexdigest()
def relative(path):
    return 'fixture_project/' + path.split('/fixture_project/', 1)[1] if isinstance(path, str) and '/fixture_project/' in path else path


def action_fields(name, args):
    out = {'name': name, 'input': args}
    if isinstance(args, dict) and isinstance(args.get('command'), str):
        out['command'] = args['command']; out['argv'] = shlex.split(args['command'])
        out['action_kind'] = 'inspect' if 'bench_check.py inspect' in args['command'] else 'test' if re.search(r'bench_check\.py (?:baseline|final)', args['command']) else name
    if name == 'apply_patch' and isinstance(args, str):
        paths = re.findall(r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', args, re.M)
        if len(paths) == 1: out['target'] = relative(paths[0]); out['patch'] = args; out['action_kind'] = 'edit'
    elif isinstance(args, dict) and isinstance(args.get('path'), str): out['target'] = relative(args['path'])
    return out


NATIVE_SCHEMA_VERSION = 1
DECODER_CONTRACT = 'copilot-session-events-jsonl-v1'
_PROMPT_CACHE_FIELDS = ('prompt_tokens', 'cache_read', 'cache_write')


def decode_copilot_native_bytes(raw, *, artifact='events.jsonl'):
    inspected = decode_agent_native_bytes('copilot', raw)
    rows = _stream(raw, artifact)
    out = {key: [] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'records', 'session_totals', 'prompt_cache_fragments')}
    start = rows[0].get('data', {}) if rows and rows[0].get('type') == 'session.start' and isinstance(rows[0].get('data'), dict) else {}
    native_version = start.get('version') if type(start.get('version')) is int else None
    out.update(status='ok' if inspected.status == 'complete' else 'unsupported', format=DECODER_CONTRACT,
               session_id=inspected.session_id, version=inspected.version, physical_sha256=sha(raw), diagnostics=[],
               native_schema_version=native_version, producer=start.get('producer') if isinstance(start.get('producer'), str) else None)
    if inspected.status != 'complete': out['diagnostics'].append({'code': 'unsupported_native_records', 'severity': 'error'})
    if native_version != NATIVE_SCHEMA_VERSION:
        # The contract is bound to the declared native schema version.
        out['status'] = 'unsupported'
        out['diagnostics'].append({'code': 'unsupported_native_schema_version', 'severity': 'error'})
    turn = None; requested = {}; started = set(); completed = set(); selected = start.get('selectedModel'); api_calls = {}
    for line, row in enumerate(rows, 1):
        data = row.get('data', {}); kind = row.get('type'); identity = row.get('id')
        loc = {'artifact': artifact, 'line': line, 'physical_sha256': sha(raw)}
        out['records'].append({'raw': row, 'locator': loc, 'timestamp': row.get('timestamp')})
        base = {'id': identity, 'sequence': line, 'locator': loc, 'timestamp': row.get('timestamp')}
        if kind == 'session.resume' and isinstance(data.get('selectedModel'), str):
            selected = data['selectedModel']
        elif kind == 'user.message':
            turn = identity; out['turns'].append({**base, 'role': 'user', 'text': data.get('content')})
        elif kind == 'assistant.message':
            content = data.get('content')
            if isinstance(data.get('apiCallId'), str) and data['apiCallId']:
                api_calls.setdefault(data['apiCallId'], []).append((identity, turn))
            if isinstance(content, str) and content:
                response = {**base, 'role': 'assistant', 'text': content, 'turn_id': turn, 'model_id': data.get('model'),
                            'configuration': selected, 'message_id': data.get('messageId')}
                if data.get('phase') is not None: response['phase'] = data['phase']
                markers = re.findall(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+', content)
                if len(markers) == 1 and content.rstrip().endswith(markers[0]): response['canary'] = markers[0]
                out['responses'].append(response)
                out['relations'].append({'kind': 'turn_response', 'from_id': turn, 'to_id': identity, 'locator': loc})
            for request in data.get('toolRequests') or []:
                call = request.get('toolCallId')
                if call in requested: raise ValueError('duplicate native tool request')
                requested[call] = True
                out['actions'].append({**base, **action_fields(request.get('name'), request.get('arguments')),
                                       'id': call, 'call_id': call, 'turn_id': turn})
        elif kind == 'tool.execution_start':
            call = data.get('toolCallId')
            if call not in requested or call in started: raise ValueError('unjoined or duplicate native tool start')
            started.add(call)
        elif kind == 'tool.execution_complete':
            call = data.get('toolCallId')
            if call not in started or call in completed: raise ValueError('unjoined or duplicate native tool completion')
            completed.add(call)
            result = data.get('result', {}); output = result.get('content')
            item = {**base, 'id': call + ':result', 'call_id': call, 'action_id': call, 'turn_id': turn,
                    'output': output, 'status': 'success' if data.get('success') is True else 'failure'}
            code = data.get('shellExecution', {}).get('exitCode')
            if type(code) is int: item['exit_code'] = code
            nonces = re.findall(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)', output or '')
            if len(nonces) == 1: item['helper_nonce'] = nonces[0]
            out['results'].append(item)
            out['relations'].append({'kind': 'action_result', 'from_id': call, 'to_id': item['id'], 'locator': loc})
        elif kind == 'session.usage_checkpoint':
            # Prompt-cache state of the last model call per model. It has no
            # output count, so it is a fragment and not a usage record.
            for state in data.get('promptCacheBreakState') or []:
                models = state.get('models') if isinstance(state, dict) else None
                for model, value in (models.items() if isinstance(models, dict) else ()):
                    if not isinstance(value, dict): continue
                    joined = api_calls.get(value.get('model_call_id'), [])
                    fragment = {'id': f'{identity}:{model}', 'sequence': line, 'locator': loc, 'timestamp': row.get('timestamp'),
                                'scope': 'prompt_cache_state_of_last_model_call', 'model_id': model,
                                'prompt_cache_state': {key: value[key] for key in _PROMPT_CACHE_FIELDS if key in value}}
                    if len(joined) == 1:
                        fragment.update(response_id=joined[0][0], turn_id=joined[0][1])
                    out['prompt_cache_fragments'].append(fragment)
        elif kind == 'session.shutdown':
            # Cumulative session totals. No per-response record sums to them.
            out['session_totals'].append({'id': identity, 'sequence': line, 'locator': loc, 'timestamp': row.get('timestamp'),
                                          'token_details': data.get('tokenDetails'), 'model_metrics': data.get('modelMetrics')})
    if set(requested) != started or started != completed:
        raise ValueError('incomplete native tool lifecycle')
    return out


def decode_copilot_native(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024: raise ValueError('unsafe Copilot native input')
    return decode_copilot_native_bytes(path.read_bytes(), artifact=path.name)


def build_copilot_observer(*, workload, stdout_by_turn, helper_document, before_sha256, after_sha256):
    """Build primary populations from exact stdout and independent helper/files."""
    helpers = _stream(helper_document, 'independent helper ledger')
    if [row.get('phase') for row in helpers] != ['inspect', 'baseline', 'final']: raise ValueError('helper population incomplete')
    events = []; relations = []; sessions = set(); response_ids = {}; turn_ids = {}; calls = {}; emitted = {}
    def add(identity, kind, fields, metrics, *, source='harness_stdout', role='primary_scored', observed_at=None):
        row = {'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
               'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': session}
        if observed_at is not None: row['observed_at'] = observed_at
        events.append(row); return identity
    for number in (1, 2):
        rows = _stream(stdout_by_turn[number], f'independent stdout R{number}')
        finals = [r for r in rows if r.get('type') == 'result' and r.get('exitCode') == 0]
        if len(finals) != 1: raise ValueError('stdout completion missing or duplicate')
        session = finals[0].get('sessionId'); sessions.add(session)
        submitted = [r for r in rows if r.get('type') == 'user.message']
        if len(submitted) != 1 or submitted[0]['data'].get('content') != workload['turns'][number-1]['text']:
            raise ValueError('stdout submitted input mismatch')
        raw_turn = submitted[0]; turn_id = workload['turns'][number-1]['id']; turn_ids[number] = turn_id
        add(turn_id, 'user_turn', {'text': raw_turn['data']['content'], 'role': 'user', 'turn_id': turn_id,
            'revision': f'r{number}', 'run_canary': workload['run_canary']}, ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'], observed_at=raw_turn.get('timestamp'))
        completion_rows = [row for row in rows if row.get('type') == 'tool.execution_complete']
        completion_ids = [row['data']['toolCallId'] for row in completion_rows]
        if len(completion_ids) != len(set(completion_ids)):
            raise ValueError('duplicate stdout completion')
        completed = {row['data']['toolCallId']: row for row in completion_rows}
        starts = [row for row in rows if row.get('type') == 'tool.execution_start']
        if len({r['data']['toolCallId'] for r in starts}) != len(starts): raise ValueError('duplicate stdout action')
        if {r['data']['toolCallId'] for r in starts} != set(completed):
            raise ValueError('stdout action/completion populations differ')
        for start in starts:
            data = start['data']; call = data['toolCallId']; args = data.get('arguments'); name = data.get('toolName'); result = completed.get(call)
            if result is None: raise ValueError('stdout action has no completion')
            output = result['data'].get('result', {}).get('content', '')
            primary = name == 'apply_patch' or 'SB_SURVIVAL_V1_HELPER_' in output
            fields = {**action_fields(name, args), 'turn_id': turn_id, 'call_id': call, 'native_action_id': call}
            if name == 'apply_patch': emitted['edit'] = call
            calls[call] = fields
            add(call, 'action', fields, ['work.actions', 'causal.action_result'], role='primary_scored' if primary else 'unscored', observed_at=start.get('timestamp'))
            rf = {'call_id': call, 'action_id': call, 'turn_id': turn_id, 'output': output,
                  'status': 'success' if result['data'].get('success') is True else 'failure'}
            code = result['data'].get('shellExecution', {}).get('exitCode')
            if type(code) is int: rf['exit_code'] = code
            nonces = re.findall(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)', output)
            if len(nonces) == 1: rf['helper_nonce'] = nonces[0]
            add(call + ':result', 'result', rf, ['work.results', 'causal.action_result'], role='primary_scored' if primary else 'unscored', observed_at=result.get('timestamp'))
            relations.append({'id': call + ':relation', 'kind': 'action_result', 'from_id': call, 'to_id': call + ':result', 'sequence': len(relations)+1})
            for helper in helpers:
                if helper.get('output') and helper['output'] in output:
                    emitted[helper['phase']] = call
        canary = workload['turns'][number-1]['response_canary']
        responses = [r for r in rows if r.get('type') == 'assistant.message' and isinstance(r.get('data', {}).get('content'), str) and r['data']['content'].rstrip().endswith(canary)]
        if len(responses) != 1: raise ValueError('independent final response population ambiguous')
        raw_response = responses[0]; response_ids[number] = raw_response['id']
        add(raw_response['id'], 'assistant_response', {'text': raw_response['data']['content'], 'canary': canary, 'turn_id': turn_id,
            'model_id': raw_response['data'].get('model'), 'configuration': 'auto'}, ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'], observed_at=raw_response.get('timestamp'))
        relations.append({'id': f'relation-turn-{number}', 'kind': 'turn_response', 'from_id': turn_id, 'to_id': raw_response['id'], 'sequence': len(relations)+1})
    if len(sessions) != 1 or None in sessions: raise ValueError('stdout session mismatch')
    if set(emitted) != {'inspect', 'baseline', 'edit', 'final'}: raise ValueError('stdout/helper primary population incomplete')
    if emitted['inspect'] == emitted['baseline']:
        baseline = next(row for row in helpers if row['phase'] == 'baseline')
        identity = 'helper-process-baseline'
        add(identity, 'action', {'argv': baseline['argv'], 'turn_id': turn_ids[1], 'action_kind': 'test', 'name': 'bash'},
            ['work.actions', 'causal.action_result'], source='helper_ledger')
        add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_ids[1], 'helper_nonce': baseline['helper_nonce'],
            'exit_code': baseline['exit_code'], 'output': baseline['output']}, ['work.results', 'causal.action_result'], source='helper_ledger')
        relations.append({'id': identity + ':relation', 'kind': 'action_result', 'from_id': identity, 'to_id': identity + ':result', 'sequence': len(relations)+1})
        emitted['baseline'] = identity
    # All helper invocations remain supporting independent records. A compound
    # command stays one observed harness action; it is not split into two calls.
    for helper in helpers:
        phase = helper['phase']; call = emitted[phase]
        add('helper-' + phase, 'helper', {'phase': phase, 'action_id': call, 'helper_nonce': helper['helper_nonce'],
            'argv': helper['argv'], 'exit_code': helper['exit_code'], 'output': helper['output']}, [], source='helper_ledger', role='supporting')
        relations.append({'id': 'relation-helper-' + phase, 'kind': 'helper_for', 'from_id': 'helper-' + phase, 'to_id': call, 'sequence': len(relations)+1})
    add('change-checkout', 'file_change', {'path': 'fixture_project/checkout.py', 'before_sha256': before_sha256,
        'after_sha256': after_sha256, 'turn_id': turn_ids[2], 'action_id': emitted['edit'], 'call_id': emitted['edit']}, ['work.changed_files'], source='filesystem_observer')
    relations.append({'id': 'relation-final-after-r2', 'kind': 'final_after', 'from_id': turn_ids[2], 'to_id': emitted['final'], 'sequence': len(relations)+1})
    relations.append({'id': 'relation-r1-r2', 'kind': 'supersedes', 'from_id': turn_ids[1], 'to_id': turn_ids[2], 'sequence': len(relations)+1})
    observer = {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
        'run_id': workload['run_id'], 'independent': True, 'method': 'exact submitted/response/tool stdout, helper ledger and filesystem hashes; no native input or response token synthesis', 'events': events, 'relations': relations}
    primary = {event['id'] for event in events if event['kind'] == 'action' and event['population_role'] == 'primary_scored'}
    observer['relations'] = [relation for relation in relations if relation['kind'] != 'action_result' or relation['from_id'] in primary]
    _validate_observer(observer)
    return observer
