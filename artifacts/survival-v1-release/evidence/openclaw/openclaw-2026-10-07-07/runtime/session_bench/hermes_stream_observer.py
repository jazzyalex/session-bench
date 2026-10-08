"""Independent Hermes observer from the ``stream-json`` stdout of each turn.

``hermes chat -q PROMPT --format stream-json`` writes one JSON object per
stdout line (Hermes source ``hermes_cli/stream_json.py``, build
``v0.21.5+4668.gdccb84b``). Every event has ``type`` and ``timestamp`` (Unix
milliseconds). The event types:

- ``system`` with ``subtype`` ``init``: ``model``, ``session_id``. First line.
- ``text``: one ``text`` delta of model output.
- ``tool_use``: ``name`` and ``input`` (the arguments, an object). It has a
  ``tool_call_id`` only when the agent passes one; the tool executor of this
  build does not.
- ``tool_result``: ``name``, ``output`` (the tool result as text, cut after
  5000 characters with ``...``), ``duration_ms``, ``is_error``. A
  ``tool_call_id`` as above.
- ``result``: ``session_id``, ``exit_code``, ``text`` (the final response),
  ``tokens`` (``input``, ``output``, ``total``, ``cache_read``,
  ``cache_write`` of the turn), ``duration_ms``, and ``error`` on a failure.
  Last line.

The observer never reads a native row. Its inputs are the two stdout
streams, the helper ledger that the frozen helper writes itself, and the
SHA-256 of the workload file before and after.

What the stream gives and what it does not:

- A tool call: name and arguments. A tool result: output text and the error
  flag. The result of the ``terminal`` tool is JSON text with ``output`` and
  ``exit_code``, so the exit status is observed. A result cut at 5000
  characters is not valid JSON; the observer then fails closed.
- No call id (in this build). A result is paired with its call by order: the
  stream must hold call, result, call, result. Two open calls without ids
  cannot be paired, and the observer fails closed. So the observer's action
  ids are positions in the stream (``r1-call-1``), not native ids.
- No model name per response and no usage per request; one model name per
  process (``init``) and token totals per turn (``result``).

A scored action is a frozen helper invocation or the edit of the workload
file (``write_file`` or ``patch`` on ``checkout.py``). A terminal call with
two or more scored segments is observed as one action per segment (compound
shell call rule): the helper ledger gives the exit code and the output line
of each helper process, and that line must be in the stream output of the
call. When several calls edit the workload file, the last successful one is
the scored edit. Every other call is an unscored action.
"""
import json
import shlex

from .adapters.claude_code_decoder import classify_shell_segment, compound_shell_segments, split_shell_segments
from .live_metric_comparator import _validate_observer

TARGET = 'fixture_project/checkout.py'
EVENT_TYPES = ('system', 'text', 'tool_use', 'tool_result', 'result')
EDIT_TOOLS = ('write_file', 'patch')
OUTPUT_CAP = 5000


class HermesStreamError(ValueError):
    """The stdout stream is not a complete ``stream-json`` record of one successful turn."""


def parse_stream(raw, label='stdout'):
    """Every event of one stdout stream, in order. Any line outside the protocol fails closed."""
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise HermesStreamError(f'{label} is not UTF-8') from error
    rows = []
    for number, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise HermesStreamError(f'{label} line {number} is not JSON') from error
        if (not isinstance(row, dict) or row.get('type') not in EVENT_TYPES or type(row.get('timestamp')) is not int):
            raise HermesStreamError(f'{label} line {number} is not a stream-json event')
        rows.append(row)
    return rows


def stream_summary(raw, label='stdout'):
    """Session id, model, final text and exit code of one stream; raises unless the turn completed.

    The stream must open with one ``system``/``init`` event and close with
    one ``result`` event of the same session, exit code 0 and no ``error``.
    """
    rows = parse_stream(raw, label)
    starts = [row for row in rows if row['type'] == 'system']
    ends = [row for row in rows if row['type'] == 'result']
    if (len(starts) != 1 or rows[0] is not starts[0] or starts[0].get('subtype') != 'init' or len(ends) != 1 or rows[-1] is not ends[0]):
        raise HermesStreamError(f'{label} does not open with one init event and close with one result event')
    start, end = starts[0], ends[0]
    session = start.get('session_id')
    if not isinstance(session, str) or not session or end.get('session_id') != session:
        raise HermesStreamError(f'{label} does not name one session')
    if type(end.get('exit_code')) is not int or end['exit_code'] != 0 or end.get('error'):
        raise HermesStreamError(f'{label} reports a failed turn')
    if not isinstance(end.get('text'), str) or not isinstance(start.get('model'), str) or not start['model']:
        raise HermesStreamError(f'{label} lacks the final text or the model')
    return {'session_id': session, 'model': start['model'], 'text': end['text'], 'exit_code': end['exit_code'],
            'tokens': end.get('tokens'), 'events': len(rows), 'rows': rows, 'observed_at': end['timestamp']}


def paired_calls(rows, label='stdout'):
    """The tool calls of one stream with their results: ``[{'use', 'result', 'call_id'}]``, in call order.

    With ids on both events the pair is made by id. Without ids the stream
    must hold each result directly after its call, with the same tool name.
    """
    calls, waiting = [], []
    for row in rows:
        if row['type'] == 'tool_use':
            if not isinstance(row.get('name'), str) or not isinstance(row.get('input'), dict):
                raise HermesStreamError(f'{label} holds a tool call without a name or arguments')
            if waiting and (row.get('tool_call_id') is None or any(item['call_id'] is None for item in waiting)):
                raise HermesStreamError(f'{label} holds two open tool calls without call ids')
            entry = {'use': row, 'result': None, 'call_id': row.get('tool_call_id')}
            calls.append(entry)
            waiting.append(entry)
        elif row['type'] == 'tool_result':
            identity = row.get('tool_call_id')
            match = next((item for item in waiting if item['call_id'] == identity and item['use']['name'] == row.get('name')), None)
            if match is None or not isinstance(row.get('output'), str) or type(row.get('is_error')) is not bool:
                raise HermesStreamError(f'{label} holds a tool result that pairs with no open call')
            match['result'] = row
            waiting.remove(match)
    if waiting:
        raise HermesStreamError(f'{label} holds a tool call without a result')
    return calls


def terminal_result(row, label='stdout'):
    """Output text and exit code of one ``terminal`` result event. A cut or malformed result fails closed."""
    try:
        body = json.loads(row['output'])
    except ValueError as error:
        cut = ' (cut at the output limit)' if len(row['output']) >= OUTPUT_CAP else ''
        raise HermesStreamError(f'{label} holds a terminal result that is not JSON{cut}') from error
    if not isinstance(body, dict) or not isinstance(body.get('output'), str) or type(body.get('exit_code')) is not int:
        raise HermesStreamError(f'{label} holds a terminal result without output or exit code')
    return body['output'], body['exit_code']


def edits_target(arguments):
    """True when the ``path`` argument of a file tool names the workload file."""
    path = arguments.get('path')
    if not isinstance(path, str):
        return False
    path = path.replace('\\', '/')
    return (path == 'checkout.py' or path.endswith('/checkout.py')) and '/snapshots/' not in '/' + path


def _scored(command):
    """(compound segments or None, number of scored segments, the one helper segment or None)."""
    compound = compound_shell_segments('terminal', {'command': command})
    if compound is not None:
        return compound, len(compound), None
    segments = split_shell_segments(command) or []
    scored = [(segment, classify_shell_segment(segment)) for segment in segments]
    helpers = [{**segment, **role, 'last': segment['index'] == segments[-1]['index']} for segment, role in scored
               if role is not None and role['kind'] == 'helper']
    count = sum(role is not None for _, role in scored)
    return None, count, helpers[0] if count == 1 and len(helpers) == 1 else None


def build_hermes_stream_observer(*, workload, stdout_by_turn, helper_document, before_sha256, after_sha256, launches):
    """Build the observer from the stdout stream of each turn, the helper ledger and the file hashes.

    ``launches`` are the two launch receipts of the controller (requested
    provider and model). Raises ``HermesStreamError`` when a stream is not a
    complete record, when a helper of the ledger is not in the stream, or
    when the four scored actions are not all observed.
    """
    try:
        helpers = [json.loads(line) for line in helper_document.decode('utf-8').splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as error:
        raise HermesStreamError('helper ledger is not JSON lines') from error
    if [row.get('phase') if isinstance(row, dict) else None for row in helpers] != ['inspect', 'baseline', 'final']:
        raise HermesStreamError('helper population incomplete')
    ledger = {row['phase']: row for row in helpers}
    events, relations, sessions, emitted, turn_ids = [], [], set(), {}, {}
    state = {'session': None}

    def add(identity, kind, fields, metrics, *, source='harness_stdout', role='primary_scored', observed_at=None):
        row = {'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
               'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': state['session']}
        if observed_at is not None:
            row['observed_at'] = observed_at
        events.append(row)
        return identity

    def link(action, result):
        relations.append({'id': action + ':relation', 'kind': 'action_result', 'from_id': action, 'to_id': result, 'sequence': len(relations) + 1})

    def helper_fields(phase, command, turn_id, call):
        return {'name': 'shell', 'command': command, 'argv': shlex.split(command), 'turn_id': turn_id,
                'action_kind': 'inspect' if phase == 'inspect' else 'test', **call}

    for number in (1, 2):
        label = f'stdout R{number}'
        summary = stream_summary(stdout_by_turn[number], label)
        state['session'] = summary['session_id']
        sessions.add(summary['session_id'])
        turn, launch = workload['turns'][number - 1], launches[number - 1]
        if summary['model'] != launch.get('model'):
            raise HermesStreamError(f'{label} names another model than the launch')
        turn_id = turn_ids[number] = turn['id']
        add(turn_id, 'user_turn', {'text': turn['text'], 'role': 'user', 'turn_id': turn_id, 'revision': f'r{number}',
                                   'run_canary': workload['run_canary']}, ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'],
            source='submitted_input', observed_at=summary['rows'][0]['timestamp'])
        calls = paired_calls(summary['rows'], label)
        # The scored edit is the last successful edit of the workload file in the second turn.
        edits = [index for index, call in enumerate(calls) if call['use']['name'] in EDIT_TOOLS and edits_target(call['use']['input'])
                 and call['result']['is_error'] is False]
        scored_edit = edits[-1] if number == 2 and edits else None
        for index, call in enumerate(calls):
            use, done = call['use'], call['result']
            identity = f'r{number}-call-{index + 1}'
            stream_id = {'stream_call_id': call['call_id']} if call['call_id'] is not None else {}
            command = use['input'].get('command') if use['name'] == 'terminal' else None
            compound, count, single = _scored(command) if isinstance(command, str) else (None, 0, None)
            if compound is not None:
                output, _ = terminal_result(done, label)
                lines = output.split('\n')
                for item in compound:
                    if item['kind'] != 'helper':
                        raise HermesStreamError('a shell write of the workload file inside a compound call is not an observed population of this row')
                    helper = ledger[item['phase']]
                    if helper['output'] not in lines or item['phase'] in emitted:
                        raise HermesStreamError('helper ledger line is not in the stream output of its call')
                    part = f"{identity}:segment-{item['index']}"
                    emitted[item['phase']] = add(part, 'action', {**helper_fields(item['phase'], item['text'], turn_id, stream_id),
                                                                  'segment_index': item['index']},
                                                 ['work.actions', 'causal.action_result'], observed_at=use['timestamp'])
                    add(part + ':result', 'result', {'action_id': part, 'turn_id': turn_id, 'helper_nonce': helper['helper_nonce'],
                                                     'exit_code': helper['exit_code'], 'status': 'failure' if helper['exit_code'] else 'success',
                                                     'output': helper['output'], **stream_id},
                        ['work.results', 'causal.action_result'], source='helper_ledger', observed_at=done['timestamp'])
                    link(part, part + ':result')
                continue
            if single is not None:
                output, code = terminal_result(done, label)
                helper = ledger[single['phase']]
                if helper['output'] not in output.split('\n') or single['phase'] in emitted or (single['last'] and helper['exit_code'] != code):
                    raise HermesStreamError('stream helper call differs from the helper ledger')
                emitted[single['phase']] = add(identity, 'action', helper_fields(single['phase'], command, turn_id, stream_id),
                                               ['work.actions', 'causal.action_result'], observed_at=use['timestamp'])
                # The call's own exit code is the helper's only when the helper is the last segment; else the ledger gives it.
                add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'helper_nonce': helper['helper_nonce'],
                                                     'exit_code': code if single['last'] else helper['exit_code'],
                                                     'status': 'failure' if (code if single['last'] else helper['exit_code']) else 'success',
                                                     'output': output, **stream_id},
                    ['work.results', 'causal.action_result'], observed_at=done['timestamp'])
                link(identity, identity + ':result')
                continue
            if index == scored_edit:
                emitted['edit'] = add(identity, 'action', {'name': 'file_edit', 'tool': use['name'], 'target': TARGET, 'action_kind': 'edit',
                                                           'turn_id': turn_id, **stream_id},
                                      ['work.actions', 'causal.action_result'], observed_at=use['timestamp'])
                add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'status': 'success', 'output': done['output'], **stream_id},
                    ['work.results', 'causal.action_result'], observed_at=done['timestamp'])
                link(identity, identity + ':result')
                continue
            # The arguments stay on an unscored action: the native call with the same tool name and arguments
            # in the same turn pairs with it (the stream has no call id).
            fields = {'name': use['name'], 'turn_id': turn_id, 'input': use['input']}
            if call['call_id'] is not None:
                fields['call_id'] = call['call_id']
            if isinstance(command, str):
                fields['command'] = command
            elif isinstance(use['input'].get('path'), str):
                fields['target'] = TARGET if edits_target(use['input']) else use['input']['path']
            add(identity, 'action', fields, [], role='unscored', observed_at=use['timestamp'])
        canary = turn['response_canary']
        if not summary['text'].rstrip().endswith(canary) or summary['text'].count(canary) != 1:
            raise HermesStreamError(f'{label} final response lacks the exact canary')
        response_id = f'response-r{number}'
        add(response_id, 'assistant_response', {'text': summary['text'], 'canary': canary, 'turn_id': turn_id, 'role': 'assistant',
                                                'status': 'completed', 'model_id': summary['model'],
                                                'configuration': {'provider': launch.get('provider')}},
            ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'],
            observed_at=summary['observed_at'])
        relations.append({'id': f'relation-turn-{number}', 'kind': 'turn_response', 'from_id': turn_id, 'to_id': response_id, 'sequence': len(relations) + 1})
    if len(sessions) != 1:
        raise HermesStreamError('the two streams name different sessions')
    if set(emitted) != {'inspect', 'baseline', 'edit', 'final'}:
        missing = sorted({'inspect', 'baseline', 'edit', 'final'} - set(emitted))
        raise HermesStreamError('stdout and helper primary population incomplete: no observed ' + ', '.join(missing))
    for helper in helpers:
        phase = helper['phase']
        add('helper-' + phase, 'helper', {'phase': phase, 'action_id': emitted[phase], 'helper_nonce': helper['helper_nonce'],
                                          'argv': helper['argv'], 'exit_code': helper['exit_code'], 'output': helper['output']},
            [], source='helper_ledger', role='supporting')
        relations.append({'id': 'relation-helper-' + phase, 'kind': 'helper_for', 'from_id': 'helper-' + phase, 'to_id': emitted[phase],
                          'sequence': len(relations) + 1})
    add('change-checkout', 'file_change', {'path': TARGET, 'before_sha256': before_sha256, 'after_sha256': after_sha256,
                                           'turn_id': turn_ids[2], 'action_id': emitted['edit']}, ['work.changed_files'], source='filesystem_observer')
    relations.append({'id': 'relation-final-after-r2', 'kind': 'final_after', 'from_id': turn_ids[2], 'to_id': emitted['final'], 'sequence': len(relations) + 1})
    relations.append({'id': 'relation-r1-r2', 'kind': 'supersedes', 'from_id': turn_ids[1], 'to_id': turn_ids[2], 'sequence': len(relations) + 1})
    observer = {'schema_version': '1.0-survival-observer', 'protocol_version': '1.0-survival', 'scenario_id': 'survival-v1-repair',
                'run_id': workload['run_id'], 'independent': True,
                'method': ('exact submitted prompts; tool call, tool result and result events of the stream-json stdout of each turn; '
                           'helper ledger; filesystem hashes; no native input'),
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer
