"""Independent OpenClaw observer from the ACP stream of each turn (route ``acp``).

``openclaw acp --session KEY --no-prefix-cwd`` speaks the Agent Client
Protocol on stdio: one JSON-RPC message per line. The controller is the
client. It writes every line that it sends or receives to
``turn-rN/acp-stream.jsonl``, one wrapper object per line::

    {"t_ns": <receive or send time>, "dir": "send" | "recv", "raw": "<the line>"}

A received line carries its arrival time. The file order is the order in
which the controller handled the lines, so a notification that arrived just
before a prompt was sent can stand after that prompt.

Messages of one turn (OpenClaw 2026.9.8, probe ``openclaw-2026-10-07-probe-07-acp``
and the captures of 2026-10-07):

- ``session/prompt`` (sent): the prompt text. Its response (received):
  ``{"stopReason": "end_turn"}``.
- ``session/update`` notifications (received), by ``update.sessionUpdate``:
  ``tool_call`` (``toolCallId``, ``title``, ``kind``, ``status``,
  ``rawInput``), ``tool_call_update`` (``toolCallId``, ``status``,
  ``rawOutput`` with ``status``, ``exitCode``, ``durationMs``),
  ``agent_message_chunk`` (``content.text``), ``usage_update`` (``used``,
  ``size``: context tokens, approximate), ``session_info_update``
  (``_meta.sessionKey``), ``available_commands_update``.
- ``session/request_permission`` (received, a request): the controller answers
  with the allow option. Both lines are in the stream.

The observer never reads a native row. Its inputs are the two streams, the
helper ledger after each turn, and the SHA-256 of the workload file before
and after.

What the stream gives and what it does not:

- A tool call: call id, tool title, kind, the arguments (``rawInput``), order,
  and time. Its end: status and, for a shell call, the exit code.
- **No output text of a tool call.** The helper ledger gives the output line of
  each helper process; the ledger line is bound to the call by the helper
  command in ``rawInput``, the turn, and the exit code.
- No model name, no provider, and no token counts per request. ``usage_update``
  is the context size of the session, marked approximate. The gateway prints
  one line at its start, ``agent model: <provider>/<model>``; the controller
  keeps it, and the observer takes the model from it when it is given
  (one model per gateway process, not per response).
- ``apply_patch`` arrives as a tool call with ``rawInput.changes``: per file
  the path, the kind and the diff. Its end has a status and no exit code.

A scored action is a frozen helper invocation or the edit of the workload
file (``apply_patch`` with a change of ``checkout.py``). A shell call with two
or more scored segments is one action per segment (compound shell call rule).
When several calls edit the workload file, the last successful one is the
scored edit. Every other call is an unscored action.

A shell command arrives as ``/bin/zsh -lc '<command>'``. The observer takes
the inner command of exactly this wrapper form and nothing else.
"""
import json
import shlex

from .hermes_stream_observer import _scored
from .live_metric_comparator import _validate_observer

TARGET = 'fixture_project/checkout.py'
UPDATE_KINDS = ('tool_call', 'tool_call_update', 'agent_message_chunk', 'agent_thought_chunk', 'usage_update', 'session_info_update',
                'available_commands_update', 'current_mode_update', 'config_option_update', 'plan')
_SHELLS = ('/bin/zsh', '/bin/bash', '/bin/sh', 'zsh', 'bash', 'sh')
_DONE = ('completed', 'failed')


class OpenClawAcpError(ValueError):
    """The ACP stream is not a complete record of one successful turn, or it is ambiguous."""


def stream_line(direction, raw, t_ns):
    """One wrapper line of ``acp-stream.jsonl`` for one raw stdio line (without its line end)."""
    return (json.dumps({'t_ns': t_ns, 'dir': direction, 'raw': raw}, ensure_ascii=False) + '\n').encode('utf-8')


def parse_acp_stream(raw, label='ACP stream'):
    """Every message of one stream file, in order: ``{'t_ns', 'dir', 'message'}``. Anything else fails closed."""
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise OpenClawAcpError(f'{label} is not UTF-8') from error
    records = []
    for number, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        try:
            wrapper = json.loads(line)
            message = json.loads(wrapper['raw'])
        except (ValueError, KeyError, TypeError) as error:
            raise OpenClawAcpError(f'{label} line {number} is not a wrapped JSON-RPC message') from error
        if (set(wrapper) != {'t_ns', 'dir', 'raw'} or type(wrapper['t_ns']) is not int or wrapper['dir'] not in ('send', 'recv')
                or not isinstance(message, dict) or message.get('jsonrpc') != '2.0'):
            raise OpenClawAcpError(f'{label} line {number} is not a wrapped JSON-RPC message')
        records.append({'t_ns': wrapper['t_ns'], 'dir': wrapper['dir'], 'message': message})
    return records


def inner_command(command):
    """The command inside ``<shell> -lc '<command>'``; the command itself when there is no such wrapper."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    if len(words) == 3 and words[0] in _SHELLS and words[1] in ('-lc', '-c'):
        return words[2]
    return command


def turn_summary(raw, label='ACP stream'):
    """The facts of one turn: prompt, final text, tool calls in order, permissions; raises unless the turn ended.

    The stream must hold exactly one sent ``session/prompt`` and its response
    with ``stopReason`` ``end_turn``. Every update after the prompt must
    belong to the session of the prompt. Every tool call must have ended.
    """
    records = parse_acp_stream(raw, label)
    prompts = [row for row in records if row['dir'] == 'send' and row['message'].get('method') == 'session/prompt']
    if len(prompts) != 1:
        raise OpenClawAcpError(f'{label} does not hold exactly one prompt')
    prompt = prompts[0]
    params = prompt['message'].get('params')
    blocks = params.get('prompt') if isinstance(params, dict) else None
    if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict) or blocks[0].get('type') != 'text'
            or not isinstance(blocks[0].get('text'), str) or not isinstance(params.get('sessionId'), str)):
        raise OpenClawAcpError(f'{label} holds a prompt that is not one text block')
    session, identity = params['sessionId'], prompt['message'].get('id')
    after = records[records.index(prompt) + 1:]
    ends = [row for row in after if row['dir'] == 'recv' and 'method' not in row['message'] and row['message'].get('id') == identity]
    if len(ends) != 1 or after[-1] is not ends[0]:
        raise OpenClawAcpError(f'{label} does not close with the one response of its prompt')
    result = ends[0]['message'].get('result')
    if 'error' in ends[0]['message'] or not isinstance(result, dict) or result.get('stopReason') != 'end_turn':
        raise OpenClawAcpError(f'{label} reports a turn that did not end normally')
    calls, order, chunks, keys, usage, permissions = {}, [], [], [], None, []
    for row in after[:-1]:
        message = row['message']
        method = message.get('method')
        if row['dir'] == 'send':
            if 'method' in message or not any(item['request_id'] == message.get('id') and item['answer'] is None for item in permissions):
                raise OpenClawAcpError(f'{label} holds a sent message that answers no permission request')
            item = next(item for item in permissions if item['request_id'] == message.get('id') and item['answer'] is None)
            outcome = (message.get('result') or {}).get('outcome') if isinstance(message.get('result'), dict) else None
            if not isinstance(outcome, dict) or outcome.get('outcome') != 'selected' or outcome.get('optionId') not in item['options']:
                raise OpenClawAcpError(f'{label} holds a permission answer that selects no offered option')
            item.update(answer=outcome['optionId'], answer_kind=item['options'][outcome['optionId']], answered_ns=row['t_ns'])
            continue
        body = message.get('params')
        if not isinstance(body, dict) or body.get('sessionId') != session:
            raise OpenClawAcpError(f'{label} holds a message of another session or without parameters')
        if method == 'session/request_permission':
            options = body.get('options')
            call = body.get('toolCall') if isinstance(body.get('toolCall'), dict) else {}
            if 'id' not in message or not isinstance(options, list) or not all(isinstance(o, dict) and isinstance(o.get('optionId'), str) for o in options):
                raise OpenClawAcpError(f'{label} holds a permission request that is not understood')
            permissions.append({'request_id': message['id'], 'tool_call_id': call.get('toolCallId'), 'requested_ns': row['t_ns'],
                                'options': {option['optionId']: option.get('kind') for option in options}, 'answer': None})
            continue
        update = body.get('update')
        kind = update.get('sessionUpdate') if isinstance(update, dict) else None
        if method != 'session/update' or kind not in UPDATE_KINDS:
            raise OpenClawAcpError(f'{label} holds a message that this observer does not know: {method} / {kind}')
        if kind == 'tool_call':
            call_id = update.get('toolCallId')
            if not isinstance(call_id, str) or not call_id or call_id in calls or not isinstance(update.get('title'), str):
                raise OpenClawAcpError(f'{label} holds a tool call without a new call id or without a title')
            calls[call_id] = {'call_id': call_id, 'title': update['title'], 'kind': update.get('kind'), 'raw_input': update.get('rawInput'),
                              'status': update.get('status'), 'raw_output': update.get('rawOutput'), 'locations': update.get('locations'),
                              'content': update.get('content'), 'called_ns': row['t_ns'], 'ended_ns': None, 'updates': 0}
            order.append(call_id)
        elif kind == 'tool_call_update':
            call = calls.get(update.get('toolCallId'))
            if call is None or call['status'] in _DONE:
                raise OpenClawAcpError(f'{label} holds a tool call update for no open call')
            call['updates'] += 1
            for source, target in (('status', 'status'), ('rawOutput', 'raw_output'), ('rawInput', 'raw_input'), ('title', 'title'),
                                   ('kind', 'kind'), ('locations', 'locations'), ('content', 'content')):
                if update.get(source) is not None:
                    call[target] = update[source]
            if call['status'] in _DONE:
                call['ended_ns'] = row['t_ns']
        elif kind == 'agent_message_chunk':
            content = update.get('content')
            if not isinstance(content, dict) or content.get('type') != 'text' or not isinstance(content.get('text'), str):
                raise OpenClawAcpError(f'{label} holds a message chunk that is not text')
            chunks.append(content['text'])
        elif kind == 'session_info_update':
            meta = update.get('_meta')
            if isinstance(meta, dict) and isinstance(meta.get('sessionKey'), str):
                keys.append(meta['sessionKey'])
        elif kind == 'usage_update':
            usage = {key: update.get(key) for key in ('used', 'size')} | {'meta': update.get('_meta')}
    for row in records[:records.index(prompt)]:   # the session key may be announced before the prompt
        update = (row['message'].get('params') or {}).get('update') if row['dir'] == 'recv' and isinstance(row['message'].get('params'), dict) else None
        meta = update.get('_meta') if isinstance(update, dict) and update.get('sessionUpdate') == 'session_info_update' else None
        if isinstance(meta, dict) and isinstance(meta.get('sessionKey'), str):
            keys.append(meta['sessionKey'])
    if any(calls[call_id]['status'] not in _DONE for call_id in order):
        raise OpenClawAcpError(f'{label} holds a tool call that did not end')
    if any(item['answer'] is None for item in permissions):
        raise OpenClawAcpError(f'{label} holds a permission request without an answer')
    return {'acp_session_id': session, 'prompt': blocks[0]['text'], 'text': ''.join(chunks), 'chunks': len(chunks),
            'calls': [calls[call_id] for call_id in order], 'permissions': permissions, 'session_keys': sorted(set(keys)),
            'context_usage': usage, 'stop_reason': result['stopReason'], 'messages': len(records),
            'prompt_ns': prompt['t_ns'], 'ended_ns': ends[0]['t_ns']}


def _shell(call):
    """The command of a shell call, or None when the call is not one."""
    raw = call['raw_input']
    command = raw.get('command') if isinstance(raw, dict) else None
    return command if isinstance(command, str) and (call['kind'] == 'execute' or call['title'].startswith('bash')) else None


def edits_target(call):
    """True when a call is ``apply_patch`` with a change of the workload file."""
    raw = call['raw_input']
    changes = raw.get('changes') if isinstance(raw, dict) else None
    if not call['title'].startswith('apply_patch') or not isinstance(changes, list):
        return False
    paths = [change.get('path').replace('\\', '/') for change in changes if isinstance(change, dict) and isinstance(change.get('path'), str)]
    return any(path == TARGET or path.endswith('/' + TARGET) for path in paths)


def _exit(call, label):
    output = call['raw_output']
    code = output.get('exitCode') if isinstance(output, dict) else None
    if type(code) is not int or (call['status'] == 'completed') != (code == 0):
        raise OpenClawAcpError(f'{label} holds a shell call whose end has no exit code, or a status that differs from it')
    return code


def build_openclaw_acp_observer(*, workload, stream_by_turn, helper_by_turn, before_sha256, after_sha256, session_key, gateway_model=None):
    """Build the observer from the ACP stream of each turn, the helper ledger after each turn and the file hashes.

    ``session_key`` is the key that the controller gave to ``openclaw acp``; the
    streams must name it and no other. ``gateway_model`` is
    ``{'provider', 'model'}`` from the start line of the gateway, or None.
    Raises ``OpenClawAcpError`` when a stream is not a complete turn, when a
    helper of the ledger is not a call of the stream in its turn, when an exit
    code differs from the ledger, or when the four scored actions are not all observed.
    """
    try:
        ledgers = {turn: [json.loads(line) for line in helper_by_turn[turn].decode('utf-8').splitlines() if line.strip()] for turn in (1, 2)}
    except (UnicodeDecodeError, ValueError) as error:
        raise OpenClawAcpError('helper ledger is not JSON lines') from error
    last = ledgers[2]
    if [row.get('phase') if isinstance(row, dict) else None for row in last] != ['inspect', 'baseline', 'final'] or last[:2] != ledgers[1]:
        raise OpenClawAcpError('helper population incomplete, or the ledger of turn 1 is not the start of the ledger of turn 2')
    ledger = {row['phase']: row for row in last}
    phase_turn = {'inspect': 1, 'baseline': 1, 'final': 2}
    events, relations, emitted, turn_ids, sessions = [], [], {}, {}, set()

    def add(identity, kind, fields, metrics, *, source='harness_stdout', role='primary_scored', observed_at=None):
        row = {'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
               'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': session_key}
        if observed_at is not None:
            row['observed_at'] = observed_at // 1_000_000
        events.append(row)
        return identity

    def link(action, result):
        relations.append({'id': action + ':relation', 'kind': 'action_result', 'from_id': action, 'to_id': result, 'sequence': len(relations) + 1})

    def helper(identity, phase, command, call, turn_id, number, label, *, exit_code, segment=None):
        row = ledger[phase]
        if phase in emitted or phase_turn[phase] != number or row['exit_code'] != exit_code:
            raise OpenClawAcpError(f'{label}: the {phase} helper call differs from the helper ledger, is repeated, or is in another turn')
        fields = {'name': 'shell', 'command': command, 'argv': shlex.split(command), 'turn_id': turn_id,
                  'action_kind': 'inspect' if phase == 'inspect' else 'test', 'call_id': call['call_id'], 'tool': call['title'].split(':', 1)[0]}
        if segment is not None:
            fields['segment_index'] = segment
        emitted[phase] = add(identity, 'action', fields, ['work.actions', 'causal.action_result'], observed_at=call['called_ns'])
        # Status and exit code are in the stream. The stream has no output text: the output line is the one of the helper ledger.
        add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'helper_nonce': row['helper_nonce'], 'exit_code': exit_code,
                                             'status': 'failure' if exit_code else 'success', 'output': row['output'],
                                             'output_source': 'helper_ledger', 'call_id': call['call_id']},
            ['work.results', 'causal.action_result'], observed_at=call['ended_ns'])
        link(identity, identity + ':result')

    for number in (1, 2):
        label = f'ACP stream R{number}'
        summary = turn_summary(stream_by_turn[number], label)
        sessions.add(summary['acp_session_id'])
        turn = workload['turns'][number - 1]
        if summary['prompt'] != turn['text']:
            raise OpenClawAcpError(f'{label} holds another prompt than the workload')
        if any(key != session_key for key in summary['session_keys']) or (number == 1 and not summary['session_keys']):
            raise OpenClawAcpError(f'{label} does not name the session key of the capture, or names another')
        turn_id = turn_ids[number] = turn['id']
        add(turn_id, 'user_turn', {'text': turn['text'], 'role': 'user', 'turn_id': turn_id, 'revision': f'r{number}',
                                   'run_canary': workload['run_canary']}, ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'],
            source='submitted_input', observed_at=summary['prompt_ns'])
        calls = summary['calls']
        edits = [index for index, call in enumerate(calls) if edits_target(call) and call['status'] == 'completed']
        scored_edit = edits[-1] if number == 2 and edits else None
        for index, call in enumerate(calls):
            identity = f'r{number}-call-{index + 1}'
            raw_command = _shell(call)
            command = inner_command(raw_command) if raw_command is not None else None
            compound, count, single = _scored(command) if command is not None else (None, 0, None)
            if compound is not None:
                code = _exit(call, label)
                for item in compound:
                    if item['kind'] != 'helper':
                        raise OpenClawAcpError('a shell write of the workload file inside a compound call is not an observed population of this row')
                    # The call has one exit code. A helper that is not the last segment takes its exit code from the ledger.
                    final = item['index'] == compound[-1]['index']
                    helper(f"{identity}:segment-{item['index']}", item['phase'], item['text'], call, turn_id, number, label,
                           exit_code=code if final else ledger[item['phase']]['exit_code'], segment=item['index'])
                continue
            if single is not None:
                code = _exit(call, label)
                helper(identity, single['phase'], command, call, turn_id, number, label,
                       exit_code=code if single['last'] else ledger[single['phase']]['exit_code'])
                continue
            if count:
                raise OpenClawAcpError(f'{label} holds a shell call with a scored segment that this observer cannot place')
            if index == scored_edit:
                changes = [{'path': TARGET, 'kind': change.get('kind'), 'diff': change.get('diff')} for change in call['raw_input']['changes']
                           if isinstance(change, dict) and isinstance(change.get('path'), str) and change['path'].replace('\\', '/').endswith(TARGET)]
                emitted['edit'] = add(identity, 'action', {'name': 'file_edit', 'tool': 'apply_patch', 'target': TARGET, 'action_kind': 'edit',
                                                           'turn_id': turn_id, 'call_id': call['call_id'], 'changes': changes},
                                      ['work.actions', 'causal.action_result'], observed_at=call['called_ns'])
                add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'status': 'success', 'call_id': call['call_id'],
                                                     # The stream gives the result of a patch as an object. Its declared text is
                                                     # key-sorted JSON with the default separators.
                                                     'output': json.dumps(call['raw_output'], ensure_ascii=False, sort_keys=True)},
                    ['work.results', 'causal.action_result'], observed_at=call['ended_ns'])
                link(identity, identity + ':result')
                continue
            fields = {'name': call['title'].split(':', 1)[0], 'turn_id': turn_id, 'input': call['raw_input'], 'call_id': call['call_id'],
                      'status': call['status']}
            if command is not None:
                fields['command'] = command
            elif edits_target(call):
                fields['target'] = TARGET
            add(identity, 'action', fields, [], role='unscored', observed_at=call['called_ns'])
        canary = turn['response_canary']
        if not summary['text'].rstrip().endswith(canary) or summary['text'].count(canary) != 1:
            raise OpenClawAcpError(f'{label} final response lacks the exact canary')
        fields = {'text': summary['text'], 'canary': canary, 'turn_id': turn_id, 'role': 'assistant', 'status': 'completed'}
        if gateway_model is not None:
            fields.update(model_id=gateway_model['model'], configuration={'provider': gateway_model['provider']},
                          model_source='start line of the gateway process (one model per process)')
        response = f'response-r{number}'
        add(response, 'assistant_response', fields,
            ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'],
            observed_at=summary['ended_ns'])
        relations.append({'id': f'relation-turn-{number}', 'kind': 'turn_response', 'from_id': turn_id, 'to_id': response, 'sequence': len(relations) + 1})
    if len(sessions) != 1:
        raise OpenClawAcpError('the two streams name different ACP sessions')
    if set(emitted) != {'inspect', 'baseline', 'edit', 'final'}:
        missing = sorted({'inspect', 'baseline', 'edit', 'final'} - set(emitted))
        raise OpenClawAcpError('ACP stream primary population incomplete: no observed ' + ', '.join(missing))
    if before_sha256 == after_sha256:
        raise OpenClawAcpError('the workload file did not change: no observed edit')
    for row in last:
        phase = row['phase']
        add('helper-' + phase, 'helper', {'phase': phase, 'action_id': emitted[phase], 'helper_nonce': row['helper_nonce'],
                                          'argv': row['argv'], 'exit_code': row['exit_code'], 'output': row['output']},
            [], source='helper_ledger', role='supporting')
        relations.append({'id': 'relation-helper-' + phase, 'kind': 'helper_for', 'from_id': 'helper-' + phase, 'to_id': emitted[phase],
                          'sequence': len(relations) + 1})
    add('change-checkout', 'file_change', {'path': TARGET, 'before_sha256': before_sha256, 'after_sha256': after_sha256,
                                           'turn_id': turn_ids[2], 'action_id': emitted['edit']}, ['work.changed_files'], source='filesystem_observer')
    relations.append({'id': 'relation-final-after-r2', 'kind': 'final_after', 'from_id': turn_ids[2], 'to_id': emitted['final'], 'sequence': len(relations) + 1})
    relations.append({'id': 'relation-r1-r2', 'kind': 'supersedes', 'from_id': turn_ids[1], 'to_id': turn_ids[2], 'sequence': len(relations) + 1})
    observer = {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
                'run_id': workload['run_id'], 'independent': True,
                'method': ('exact submitted prompts; tool call, tool call update, message chunk and response messages of the ACP stream of each '
                           'turn; helper ledger for the helper output lines; filesystem hashes; no native input'),
                'tool_events_observed': True, 'acp_session_id': sessions.pop(), 'session_key': session_key,
                'limits': ['The stream holds no output text of a tool call; helper output lines come from the helper ledger.',
                           'The stream holds no model per response and no token counts per request.',
                           'The observer session id is the session key: the stream does not name the OpenClaw session id.'],
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer
