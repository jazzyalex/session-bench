"""Separate stdout and native projections for bounded DSH v4 captures.

The native decoder never receives observer text, expected events or filesystem
hashes. All physical records remain available with locators. Lifecycle features
not yet implemented make the decode unsupported, rather than disappearing.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shlex

from .adapters.deepseek_harness import _parse_v4_plain
from .dsh_native import read_physical
from .workload_instance import inspect_checkout_source


class DSHSemanticError(ValueError):
    pass


def helper_command(command):
    """Recognize only a literal helper invocation plus the exit-display wrapper."""
    if not isinstance(command, str):
        return None
    match = re.fullmatch(r'(python3 bench_check\.py (?:inspect|baseline|final) --run-canary SB_SURVIVAL_V1_RUN_[A-Za-z0-9_-]+)(?:;\s*echo "(?:\[exit code: \$\?\]|exit=\$\?)")?', command)
    return match.group(1) if match else None


def helper_exit(output):
    match = re.search(r'^(?:\[exit code: (-?\d+)\]|exit=(-?\d+))\s*$', output, re.MULTILINE)
    return int(match.group(1) or match.group(2)) if match else None


def text_blocks(message):
    blocks = message.get('content')
    if not isinstance(blocks, list) or any(not isinstance(b, dict) for b in blocks):
        raise DSHSemanticError('message content must be an array of objects')
    for block in blocks:
        if block.get('type') not in {'text', 'reasoning', 'image', 'file', 'tool-call', 'tool-result'}:
            raise DSHSemanticError('unsupported content block')
        if block['type'] in {'text', 'reasoning'} and not isinstance(block.get('text'), str):
            raise DSHSemanticError('text block missing text')
    return ''.join(b['text'] for b in blocks if b.get('type') == 'text' and isinstance(b.get('text'), str))


def strict_json(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise DSHSemanticError('duplicate JSON key')
            out[k] = v
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(DSHSemanticError('non-finite JSON')))


def stdout_projection(raw):
    """Translate independently captured stdout; never consult native records."""
    rows = [strict_json(line) for line in raw.splitlines()]
    if not rows or rows[0].get('type') != 'session' or rows[-1].get('type') != 'final':
        raise DSHSemanticError('incomplete stdout session/final boundary')
    session = rows[0].get('sessionId')
    if not isinstance(session, str) or not session:
        raise DSHSemanticError('missing stdout session identity')
    if sum(r.get('type') == 'session' for r in rows) != 1:
        raise DSHSemanticError('ambiguous stdout session identity')
    if sum(r.get('type') == 'final' for r in rows) != 1:
        raise DSHSemanticError('ambiguous stdout final boundary')
    calls, outputs = {}, []
    for row in rows:
        if row.get('truncated') or row.get('type') == 'error':
            raise DSHSemanticError('truncated or failed stdout projection')
        kind = row.get('type')
        if kind == 'tool_call':
            call_id = row.get('callId')
            if not isinstance(call_id, str) or call_id in calls:
                raise DSHSemanticError('missing or reused stdout call identity')
            calls[call_id] = row
        elif kind == 'tool_result':
            if row.get('status') not in {'completed', 'error'} or not isinstance(row.get('result'), str):
                raise DSHSemanticError('unsupported stdout tool-result outcome')
            call = calls.pop(row.get('callId'), None)
            if call is None:
                raise DSHSemanticError('stdout result has no matching call')
            args = dict(call['input']) if isinstance(call['input'], dict) else call['input']
            command = helper_command(args.get('command')) if isinstance(args, dict) else None
            exit_code = 1 if row.get('status') == 'error' else 0
            if command:
                wrapped = args['command'] != command
                args['command'] = command
                explicit_exit = helper_exit(row.get('result', ''))
                if explicit_exit is not None or wrapped:
                    exit_code = explicit_exit
            outputs.append({'type': 'tool_use', 'sessionID': session, 'callID': row['callId'],
                            'tool': call['tool'], 'input': args,
                            'state': {'status': 'completed', 'output': row.get('result', ''),
                                      'metadata': {'exit': exit_code}}})
        elif kind == 'final':
            outputs.append({'type': 'text', 'sessionID': session, 'text': row.get('text', '')})
        elif kind == 'status' and row.get('phase') == 'step_end' and isinstance(row.get('usage'), dict):
            u = row['usage']
            outputs.append({'type': 'step_finish', 'sessionID': session, 'reason': 'stop', 'tokens': {
                'input_tokens': u.get('inputTokens'), 'output_tokens': u.get('outputTokens'),
                'cache_read_tokens': u.get('cacheReadTokens'), 'cache_write_tokens': u.get('cacheWriteTokens'),
                'reasoning_tokens': u.get('reasoningTokens')}})
    if calls:
        raise DSHSemanticError('stdout ends with unsettled tool calls')
    return {'session_id': session, 'jsonl': '\n'.join(json.dumps(r, ensure_ascii=False) for r in outputs) + '\n'}


def _relative(path, cwd):
    if not isinstance(path, str):
        return None
    if path.startswith(cwd.rstrip('/') + '/'):
        return path[len(cwd.rstrip('/')) + 1:]
    return path


def _action(data, *, turn_id, locator, sequence, cwd):
    arguments = data.get('arguments')
    if not isinstance(arguments, str):
        raise DSHSemanticError('tool arguments must retain native JSON text')
    args = strict_json(arguments) if arguments else {}
    if not isinstance(args, dict):
        raise DSHSemanticError('non-object tool arguments are not qualified')
    name, call_id = data.get('name'), data.get('callId')
    if not isinstance(name, str) or not isinstance(call_id, str) or not call_id:
        raise DSHSemanticError('missing tool call identity')
    value = {'id': call_id, 'call_id': call_id, 'name': name, 'tool_name': name, 'input': args,
             'turn_id': turn_id, 'sequence': sequence, 'locator': locator}
    command = args.get('command')
    if isinstance(command, str):
        value['command'] = command
        normalized = helper_command(command)
        value['argv'] = shlex.split(normalized or command)
        if normalized:
            value['target'] = 'fixture_project/checkout.py'
    target = _relative(args.get('file_path', args.get('path')), cwd)
    if target is not None:
        value['target'] = target
    workdir = _relative(args.get('workdir', args.get('cwd')), cwd)
    if workdir is not None:
        value['cwd'] = workdir
    return value


def decode_dsh_native(path: Path):
    logical, physical = read_physical(path)
    inventory = _parse_v4_plain(path, path.name, logical)
    if inventory.header is None:
        raise DSHSemanticError('v4 header required')
    header = inventory.header
    if not isinstance(header.get('cwd'), str) or not header['cwd'].strip():
        raise DSHSemanticError('native header lacks an explicit working directory')
    diagnostics = [{'code': 'unsupported_event_type', 'detail': issue, 'severity': 'error'} for issue in inventory.issues]
    if header.get('isSeeded') or header.get('parentSession') or header.get('delegationDepth') != 0:
        diagnostics.append({'code': 'unsupported_seed_or_subagent', 'severity': 'error'})
    out = {key: [] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'records')}
    out.update({'schema_version': 'session-bench-dsh-decoded-v1', 'session_id': header['id'],
                'format': 'dsh-native-v4', 'physical': physical, 'diagnostics': diagnostics})
    active_turn = None
    turn_ids, open_calls, seen_ids, declared_calls = {}, {}, set(), {}
    hash_observations, completed_edits = {}, []
    inspect_sources = {}
    passive = {'approval/policy', 'permission/preset', 'sandbox/mode', 'request/header', 'request/context',
               'system/message', 'developer/message', 'session-log-deepseek/delivery-accepted',
               'session/title', 'session/title-llm-request', 'step/start', 'step/end', 'agent/inbox/spliced'}
    lines = logical.splitlines()
    for index, row in enumerate(inventory.raw_records[1:], 1):
        kind, data = row['type'], row['data']
        loc = {'artifact': path.name, 'line': index + 1, 'seq': row['seq'],
               'raw_sha256': hashlib.sha256(lines[index]).hexdigest(),
               'physical_sha256': physical['physical_sha256']}
        out['records'].append({'type': kind, 'sequence': row['seq'], 'timestamp_ms': row['time'], 'locator': loc, 'raw': row})
        if kind == 'turn/start':
            if active_turn is not None or type(data.get('turn')) is not int:
                raise DSHSemanticError('invalid nested turn lifecycle')
            active_turn = data['turn']
        elif kind == 'turn/end':
            if active_turn is None or data.get('turn') != active_turn:
                raise DSHSemanticError('turn end identity mismatch')
            if not isinstance(data.get('reason'), dict) or data['reason'].get('kind') != 'completed':
                raise DSHSemanticError('aborted or unsupported turn completion')
            turn_id = turn_ids.get(active_turn)
            responses = [r for r in out['responses'] if r['turn_id'] == turn_id]
            if responses:
                # Installed DSH headless selects the last nonempty committed
                # assistant text in the completed turn as its final response.
                for response in responses:
                    response['phase'] = 'commentary'
                responses[-1]['phase'] = 'final_answer'
                canary = re.search(r'(SB_SURVIVAL_V1_RESPONSE_[^\s]+)\s*$', responses[-1]['text'])
                if canary:
                    responses[-1]['canary'] = canary.group(1)
            active_turn = None
        elif kind == 'session/end-seed':
            if data or active_turn is not None:
                raise DSHSemanticError('unsupported seed boundary payload or location')
        elif kind == 'user/message':
            if data.get('source', {}).get('kind') != 'user':
                continue  # Runtime/skill context stays in records, never a user turn.
            if active_turn is None or active_turn in turn_ids:
                raise DSHSemanticError('unbound or duplicate user turn')
            native_id = data.get('id')
            if not isinstance(native_id, str) or not native_id.strip() or native_id in seen_ids:
                raise DSHSemanticError('duplicate or absent message identity')
            seen_ids.add(native_id)
            turn_ids[active_turn] = native_id
            out['turns'].append({'id': native_id, 'role': 'user', 'text': text_blocks(data),
                                 'turn_id': native_id, 'sequence': row['seq'], 'timestamp_ms': row['time'], 'locator': loc})
        elif kind == 'assistant/message':
            message = data.get('message', {})
            turn = turn_ids.get(data.get('turn'))
            if active_turn is None or data.get('turn') != active_turn or turn is None or row.get('surfaceOp') != 'append':
                raise DSHSemanticError('unbound or rewritten assistant message')
            native_id = message.get('id')
            if not isinstance(native_id, str) or not native_id.strip() or native_id in seen_ids:
                raise DSHSemanticError('duplicate or absent assistant identity')
            seen_ids.add(native_id)
            text = text_blocks(message)
            for block in message['content']:
                if block.get('type') != 'tool-call':
                    continue
                block_id, name, arguments = block.get('id'), block.get('name'), block.get('arguments')
                if not isinstance(block_id, str) or not block_id.strip() or not isinstance(name, str) or not name.strip() or not isinstance(arguments, str):
                    raise DSHSemanticError('invalid assistant tool-call declaration')
                parsed_arguments = strict_json(arguments) if arguments else {}
                key = (data.get('turn'), data.get('step'), block_id)
                if not isinstance(parsed_arguments, dict) or key in declared_calls:
                    raise DSHSemanticError('ambiguous assistant tool-call declaration')
                declared_calls[key] = (name, parsed_arguments)
            source = message.get('source', {})
            if text:
                out['responses'].append({'id': native_id, 'turn_id': turn, 'role': 'assistant', 'text': text,
                                         'model': source.get('model'), 'configuration': source.get('model'),
                                         'sequence': row['seq'], 'timestamp_ms': row['time'], 'locator': loc})
                out['relations'].append({'id': f'turn-response:{native_id}', 'kind': 'turn_response',
                                         'from_id': turn, 'to_id': native_id, 'locator': loc})
            if isinstance(data.get('usage'), dict):
                out['usage'].append({'id': f'usage:{native_id}', 'response_id': native_id, 'turn_id': turn,
                                     'scope': 'response', 'tokens': data['usage'], 'model': source.get('model'), 'locator': loc})
        elif kind == 'tool/call':
            turn = turn_ids.get(data.get('turn'))
            if active_turn is None or data.get('turn') != active_turn or turn is None:
                raise DSHSemanticError('unbound tool call')
            action = _action(data, turn_id=turn, locator=loc, sequence=row['seq'], cwd=header.get('cwd', ''))
            scoped = (data.get('turn'), data.get('step'), action['call_id'])
            declaration = declared_calls.pop(scoped, None)
            if declaration is not None and declaration != (action['name'], action['input']):
                raise DSHSemanticError('assistant tool-call and native tool/call disagree')
            if action['id'] in seen_ids:
                raise DSHSemanticError('reused call identity requires lifecycle-qualified decoder')
            seen_ids.add(action['id'])
            open_calls[scoped] = action
            out['actions'].append(action)
        elif kind == 'tool/result':
            message = data.get('message', {})
            call_id = message.get('toolCallId')
            if active_turn is None or data.get('turn') != active_turn:
                raise DSHSemanticError('tool result outside active turn')
            if type(message.get('isError')) is not bool or message.get('source', {}).get('callId') != call_id:
                raise DSHSemanticError('invalid tool error status or source identity')
            scoped = (data.get('turn'), data.get('step'), call_id)
            action = open_calls.pop(scoped, None)
            if action is None or row.get('surfaceOp') != 'append':
                raise DSHSemanticError('unmatched or rewritten result')
            result_id = message.get('id')
            if not isinstance(result_id, str) or not result_id.strip() or result_id in seen_ids:
                raise DSHSemanticError('duplicate or absent result identity')
            seen_ids.add(result_id)
            value = {'id': result_id, 'call_id': call_id, 'action_id': action['id'],
                     'turn_id': action['turn_id'], 'output': text_blocks(message),
                     'status': 'failure' if message.get('isError') is True else 'success',
                     'sequence': row['seq'], 'timestamp_ms': row['time'], 'locator': loc}
            nonce = re.search(r'^SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)', value['output'], re.MULTILINE)
            if nonce:
                value['helper_nonce'] = nonce.group(1)
            exit_code = helper_exit(value['output']) if helper_command(action.get('command')) else None
            if exit_code is not None:
                value.update(exit_code=exit_code, status='success' if exit_code == 0 else 'failure')
            elif helper_command(action.get('command')) == action.get('command') and action.get('command'):
                value.update(exit_code=1 if message.get('isError') is True else 0,
                             exit_semantics='known_helper_binary_success_or_failure')
            elif action['name'] != 'bash':
                value.update(exit_code=1 if message.get('isError') is True else 0,
                             exit_semantics='logical_tool_error_status')
            out['results'].append(value)
            inspected = inspect_checkout_source(value['output'])
            if inspected is not None:
                inspect_sources[inspected] = loc
            if value['status'] == 'success' and action['name'] == 'edit' and action.get('target'):
                completed_edits.append(action)
            if action['name'] == 'bash' and isinstance(action.get('command'), str):
                # Admit only an explicit native shasum command and its native
                # output. Never import controller before/after hashes.
                command = action['command']
                if command.startswith('shasum -a 256 checkout.py bench_check.py;') and action.get('cwd') == 'fixture_project':
                    hashes = re.findall(r'^([a-f0-9]{64})  checkout\.py$', value['output'], re.MULTILINE)
                    if len(hashes) == 1:
                        hash_observations.setdefault(action['turn_id'], []).append((hashes[0], loc))
            out['relations'].append({'id': f'action-result:{message["id"]}', 'kind': 'action_result',
                                     'from_id': action['id'], 'to_id': message['id'], 'locator': loc})
        elif kind not in passive:
            diagnostics.append({'code': 'unsupported_semantics', 'event_type': kind, 'sequence': row['seq'], 'severity': 'error'})
    if active_turn is not None or open_calls or declared_calls:
        diagnostics.append({'code': 'incomplete_lifecycle', 'severity': 'error'})
    if len(out['turns']) == 2:
        before = hash_observations.get(out['turns'][0]['id'], [])
        after = hash_observations.get(out['turns'][1]['id'], [])
        for action in completed_edits:
            if action['target'] != 'fixture_project/checkout.py':
                continue
            change = {'id': 'change:' + action['id'], 'path': action['target'], 'action_id': action['id'],
                      'turn_id': action['turn_id'], 'sequence': action['sequence'], 'locator': action['locator']}
            # The native inspect result retains the whole pre-edit source, and
            # the native edit call retains the exact replacement. Both whole-
            # file hashes follow from those two records alone.
            old, new = action['input'].get('old_string'), action['input'].get('new_string')
            source = next(iter(inspect_sources)) if len(inspect_sources) == 1 else None
            if (len(completed_edits) == 1 and source is not None and isinstance(old, str) and old
                    and isinstance(new, str) and source.count(old) == 1):
                change.update(before_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest(),
                              after_sha256=hashlib.sha256(source.replace(old, new).encode('utf-8')).hexdigest(),
                              digest_locators=[inspect_sources[source], action['locator']])
            elif before and after and len(completed_edits) == 1:
                change.update(before_sha256=before[-1][0], after_sha256=after[-1][0],
                              digest_locators=[before[-1][1], after[-1][1]])
            out['file_changes'].append(change)
    out['usage_steps'] = out['usage']
    out['usage'] = []
    final_ids = {r['id'] for r in out['responses'] if r.get('phase') == 'final_answer'}
    out['commentary_relations'] = [r for r in out['relations'] if r['kind'] == 'turn_response' and r['to_id'] not in final_ids]
    out['relations'] = [r for r in out['relations'] if r['kind'] != 'turn_response' or r['to_id'] in final_ids]
    for action in out['actions']:
        if not helper_command(action.get('command')) or 'final' not in action.get('argv', []):
            continue
        edits = [e for e in completed_edits if e['turn_id'] == action['turn_id'] and e['sequence'] < action['sequence']]
        results = [r for r in out['results'] if r['action_id'] == action['id'] and r.get('exit_code') == 0]
        responses = [r for r in out['responses'] if r['id'] in final_ids and r['turn_id'] == action['turn_id']]
        if edits and len(results) == 1 and len(responses) == 1 and action['sequence'] < results[0]['sequence'] < responses[0]['sequence']:
            out['relations'].append({'id': 'final-chain:' + action['id'], 'kind': 'final_after',
                                     'from_id': action['turn_id'], 'to_id': action['id'],
                                     'source_locators': [edits[-1]['locator'], action['locator'], results[0]['locator'], responses[0]['locator']]})
    for response in out['responses']:
        if response.get('phase') != 'final_answer':
            continue
        samples = [x for x in out['usage_steps'] if x['response_id'] == response['id']]
        aliases = {'input': 'inputTokens', 'output': 'outputTokens', 'cache_read': 'cacheReadTokens',
                   'cache_write': 'cacheWriteTokens', 'reasoning': 'reasoningTokens'}
        buckets = {name: sum(x['tokens'][key] for x in samples) for name, key in aliases.items()
                   if samples and all(type(x['tokens'].get(key)) is int and x['tokens'][key] >= 0 for x in samples)}
        if samples:
            out['usage'].append({'id': 'turn-usage:' + response['id'], 'response_id': response['id'],
                                 'turn_id': response['turn_id'], 'scope': 'response', 'tokens': buckets,
                                 'source_locators': [x['locator'] for x in samples]})
    out['status'] = 'unsupported' if diagnostics else 'ok'
    return out
