"""Independent Kimi observer from the ``stream-json`` stdout of each turn.

``kimi -p PROMPT --output-format stream-json`` (CLI 2.1.1) writes one JSON
object per stdout line. The lines, by ``role``:

- ``meta``: ``system.version`` (``version``; first line),
  ``session.resume_hint`` (``session_id``; last line) and
  ``turn.step.retrying`` (a model request that Kimi tries again after a
  provider error).
- ``assistant`` with ``tool_calls``: each call has ``id``, ``function.name``
  and ``function.arguments`` (JSON text).
- ``tool``: ``tool_call_id`` and ``content`` (the result text).
- ``assistant`` with ``content``: a visible message. The last one is the
  final response.

The observer never reads a native file. Its inputs are the two stdout
streams, the helper ledger that the frozen helper writes itself, the SHA-256
of the workload file before and after, and the launch receipts.

What the stream holds and what it lacks:

- It holds each tool call (id, name, arguments), each result text bound to its
  call by id, the order of all of them, and the final response text.
- It has no status field and no exit code field. A failed shell call ends
  with the text line ``Command failed with exit code: N.``; a successful one
  has no such line. The exit code of a helper comes from the helper ledger
  (the helper process writes it); the observer checks that the stream text
  agrees.
- It has no time, no model name, no usage and no reasoning text. The prompt
  is not in the stream: it is the ``-p`` argument of the launch receipt. The
  model is the ``-m`` argument of the launch receipt.

A scored action is a frozen helper invocation or the edit of the workload
file (``Edit`` or ``Write`` on ``checkout.py``). A shell call with two or more
scored segments is observed as one action per segment (compound shell call
rule): the ledger gives the exit code and the output line of each helper, and
that line must be in the stream output of the call. Every other call is an
unscored action. The call ids of the stream are the ``toolCallId`` values of
the native record, so an observed action names its native call by id.
"""
import json
import re
import shlex

from .adapters.claude_code_decoder import classify_shell_segment, compound_shell_segments, split_shell_segments
from .live_metric_comparator import _validate_observer

TARGET = 'fixture_project/checkout.py'
SHELL_TOOL = 'Bash'
EDIT_TOOLS = ('Edit', 'Write')
META_TYPES = ('system.version', 'session.resume_hint', 'turn.step.retrying')
_FAILED = re.compile(r'\nCommand failed with exit code: (-?\d+)\.\Z')


class KimiStreamError(ValueError):
    """The stdout stream is not a complete ``stream-json`` record of one successful turn."""


def parse_stream(raw, label='stdout'):
    """Every line of one stdout stream, in order. Any line outside the protocol fails closed."""
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise KimiStreamError(f'{label} is not UTF-8') from error
    rows = []
    for number, line in enumerate(text.split('\n'), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as error:
            raise KimiStreamError(f'{label} line {number} is not JSON') from error
        role = row.get('role') if isinstance(row, dict) else None
        known = (role == 'meta' and row.get('type') in META_TYPES
                 or role == 'tool' and isinstance(row.get('tool_call_id'), str) and isinstance(row.get('content'), str)
                 or role == 'assistant' and (isinstance(row.get('tool_calls'), list) and 'content' not in row
                                             or isinstance(row.get('content'), str) and 'tool_calls' not in row))
        if not known:
            raise KimiStreamError(f'{label} line {number} is not a stream-json line of this build')
        rows.append(row)
    return rows


def stream_summary(raw, label='stdout'):
    """Version, session id, tool calls with results, final text and retry count of one stream; raises unless the turn completed.

    The stream must open with one ``system.version`` line and close with one
    ``session.resume_hint`` line. Each call must have one result with its id.
    The last assistant text is the final response and nothing follows it but
    the resume hint.
    """
    rows = parse_stream(raw, label)
    versions = [row for row in rows if row.get('type') == 'system.version']
    hints = [row for row in rows if row.get('type') == 'session.resume_hint']
    if (len(versions) != 1 or rows[0] is not versions[0] or not isinstance(versions[0].get('version'), str) or len(hints) != 1
            or rows[-1] is not hints[0] or not isinstance(hints[0].get('session_id'), str) or not hints[0]['session_id']):
        raise KimiStreamError(f'{label} does not open with one version line and close with one resume hint')
    calls, open_calls, texts = [], {}, []
    for position, row in enumerate(rows):
        if row['role'] == 'assistant' and 'tool_calls' in row:
            for item in row['tool_calls']:
                function = item.get('function') if isinstance(item, dict) else None
                try:
                    arguments = json.loads(function['arguments'])
                except (TypeError, KeyError, ValueError) as error:
                    raise KimiStreamError(f'{label} holds a tool call without JSON arguments') from error
                identity = item.get('id')
                if (not isinstance(identity, str) or not identity or identity in open_calls or any(call['id'] == identity for call in calls)
                        or not isinstance(function.get('name'), str) or not isinstance(arguments, dict)):
                    raise KimiStreamError(f'{label} holds a tool call without a unique id, a name or arguments')
                entry = {'id': identity, 'name': function['name'], 'arguments': arguments, 'content': None}
                calls.append(entry)
                open_calls[identity] = entry
        elif row['role'] == 'tool':
            entry = open_calls.pop(row['tool_call_id'], None)
            if entry is None:
                raise KimiStreamError(f'{label} holds a tool result that pairs with no open call')
            entry['content'] = row['content']
        elif row['role'] == 'assistant':
            texts.append((position, row['content']))
    if open_calls:
        raise KimiStreamError(f'{label} holds a tool call without a result')
    if not texts or texts[-1][0] != len(rows) - 2:
        raise KimiStreamError(f'{label} does not end with a final assistant text')
    return {'version': versions[0]['version'], 'session_id': hints[0]['session_id'], 'calls': calls, 'text': texts[-1][1],
            'retries': sum(row.get('type') == 'turn.step.retrying' for row in rows), 'lines': len(rows)}


def edits_target(arguments):
    """True when the ``path`` argument of a file tool names the workload file."""
    path = arguments.get('path')
    if not isinstance(path, str):
        return False
    path = path.replace('\\', '/')
    return (path == 'checkout.py' or path.endswith('/checkout.py')) and '/snapshots/' not in '/' + path


def stream_exit_code(content):
    """The exit code that the result text of a shell call states: N of the failure line, else 0."""
    failed = _FAILED.search(content)
    return int(failed.group(1)) if failed else 0


def _scored(command):
    """(compound segments or None, number of scored segments, the one helper segment or None)."""
    compound = compound_shell_segments('bash', {'command': command})
    if compound is not None:
        return compound, len(compound), None
    segments = split_shell_segments(command) or []
    scored = [(segment, classify_shell_segment(segment)) for segment in segments]
    helpers = [{**segment, **role, 'last': segment['index'] == segments[-1]['index']} for segment, role in scored
               if role is not None and role['kind'] == 'helper']
    count = sum(role is not None for _, role in scored)
    return None, count, helpers[0] if count == 1 and len(helpers) == 1 else None


def build_kimi_stream_observer(*, workload, stdout_by_turn, helper_document, before_sha256, after_sha256, models):
    """Build the observer from the stdout stream of each turn, the helper ledger and the file hashes.

    ``models`` are the model identities of the two launch receipts
    (``{'model_id', 'configuration'}``). Raises ``KimiStreamError`` when a
    stream is not a complete record, when a helper of the ledger is not in
    the stream, or when the four scored actions are not all observed.
    """
    try:
        helpers = [json.loads(line) for line in helper_document.decode('utf-8').splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as error:
        raise KimiStreamError('helper ledger is not JSON lines') from error
    if [row.get('phase') if isinstance(row, dict) else None for row in helpers] != ['inspect', 'baseline', 'final']:
        raise KimiStreamError('helper population incomplete')
    ledger = {row['phase']: row for row in helpers}
    events, relations, sessions, emitted, turn_ids, versions = [], [], set(), {}, {}, set()
    state = {'session': None}

    def add(identity, kind, fields, metrics, *, source='harness_stdout', role='primary_scored'):
        events.append({'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
                       'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': state['session']})
        return identity

    def link(action, result):
        relations.append({'id': action + ':relation', 'kind': 'action_result', 'from_id': action, 'to_id': result, 'sequence': len(relations) + 1})

    def helper_fields(phase, command, turn_id, call):
        fields = {'name': 'shell', 'tool': call['name'], 'command': command, 'argv': shlex.split(command), 'turn_id': turn_id,
                  'action_kind': 'inspect' if phase == 'inspect' else 'test', 'call_id': call['id']}
        if isinstance(call['arguments'].get('cwd'), str):
            fields['cwd'] = call['arguments']['cwd']
        return fields

    for number in (1, 2):
        label = f'stdout R{number}'
        summary = stream_summary(stdout_by_turn[number], label)
        state['session'] = summary['session_id']
        sessions.add(summary['session_id'])
        versions.add(summary['version'])
        turn, model = workload['turns'][number - 1], models[number - 1]
        turn_id = turn_ids[number] = turn['id']
        add(turn_id, 'user_turn', {'text': turn['text'], 'role': 'user', 'turn_id': turn_id, 'revision': f'r{number}',
                                   'run_canary': workload['run_canary']}, ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'],
            source='submitted_input')
        edits = [index for index, call in enumerate(summary['calls']) if call['name'] in EDIT_TOOLS and edits_target(call['arguments'])]
        if len(edits) > 1 or (edits and number != 2):
            raise KimiStreamError(f'{label} holds an edit of the workload file that this observer has no rule for')
        for index, call in enumerate(summary['calls']):
            identity = 'action-' + call['id']
            command = call['arguments'].get('command') if call['name'] == SHELL_TOOL else None
            compound, count, single = _scored(command) if isinstance(command, str) else (None, 0, None)
            if compound is not None:
                lines = call['content'].split('\n')
                for item in compound:
                    if item['kind'] != 'helper':
                        raise KimiStreamError('a shell write of the workload file inside a compound call is not an observed population of this row')
                    helper = ledger[item['phase']]
                    if helper['output'] not in lines or item['phase'] in emitted:
                        raise KimiStreamError('helper ledger line is not in the stream output of its call')
                    part = f"{identity}:segment-{item['index']}"
                    emitted[item['phase']] = add(part, 'action', {**helper_fields(item['phase'], item['text'], turn_id, call),
                                                                  'segment_index': item['index']}, ['work.actions', 'causal.action_result'])
                    add(part + ':result', 'result', {'action_id': part, 'turn_id': turn_id, 'helper_nonce': helper['helper_nonce'],
                                                     'exit_code': helper['exit_code'], 'status': 'failure' if helper['exit_code'] else 'success',
                                                     'output': helper['output'], 'call_id': call['id']},
                        ['work.results', 'causal.action_result'], source='helper_ledger')
                    link(part, part + ':result')
                continue
            if single is not None:
                helper = ledger[single['phase']]
                stated = stream_exit_code(call['content'])
                # The text of the stream must agree with the ledger: the helper line, and the exit status when the helper ends the call.
                if helper['output'] not in call['content'].split('\n') or single['phase'] in emitted or (single['last'] and helper['exit_code'] != stated):
                    raise KimiStreamError('stream helper call differs from the helper ledger')
                emitted[single['phase']] = add(identity, 'action', helper_fields(single['phase'], command, turn_id, call),
                                               ['work.actions', 'causal.action_result'])
                add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'helper_nonce': helper['helper_nonce'],
                                                     'exit_code': helper['exit_code'], 'status': 'failure' if helper['exit_code'] else 'success',
                                                     'output': helper['output'], 'call_id': call['id']},
                    ['work.results', 'causal.action_result'], source='helper_ledger')
                link(identity, identity + ':result')
                continue
            if index in edits:
                # The arguments of the edit (path, old and new text) stay in the observer document. The shared comparator does not
                # compare them; ``kimi_score_inputs`` checks them against the native record of the same call id.
                emitted['edit'] = add(identity, 'action', {'name': 'file_edit', 'tool': call['name'], 'target': TARGET, 'action_kind': 'edit',
                                                           'turn_id': turn_id, 'call_id': call['id'], 'input': call['arguments']},
                                      ['work.actions', 'causal.action_result'])
                # The stream has no status field. The edit succeeded: the workload file has another hash after the turn.
                add(identity + ':result', 'result', {'action_id': identity, 'turn_id': turn_id, 'status': 'success', 'output': call['content'],
                                                     'call_id': call['id']}, ['work.results', 'causal.action_result'])
                link(identity, identity + ':result')
                continue
            fields = {'name': call['name'], 'turn_id': turn_id, 'input': call['arguments'], 'call_id': call['id']}
            if isinstance(command, str):
                fields['command'] = command
            elif isinstance(call['arguments'].get('path'), str):
                fields['target'] = TARGET if edits_target(call['arguments']) else call['arguments']['path']
            add(identity, 'action', fields, [], role='unscored')
        canary = turn['response_canary']
        if not summary['text'].rstrip().endswith(canary) or summary['text'].count(canary) != 1:
            raise KimiStreamError(f'{label} final response lacks the exact canary')
        response_id = f'response-r{number}'
        add(response_id, 'assistant_response', {'text': summary['text'], 'canary': canary, 'turn_id': turn_id, 'role': 'assistant',
                                                'status': 'completed', 'model_id': model['model_id'], 'configuration': model['configuration']},
            ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'])
        relations.append({'id': f'relation-turn-{number}', 'kind': 'turn_response', 'from_id': turn_id, 'to_id': response_id, 'sequence': len(relations) + 1})
    if len(sessions) != 1 or len(versions) != 1:
        raise KimiStreamError('the two streams name different sessions or builds')
    if set(emitted) != {'inspect', 'baseline', 'edit', 'final'}:
        missing = sorted({'inspect', 'baseline', 'edit', 'final'} - set(emitted))
        raise KimiStreamError('stdout and helper primary population incomplete: no observed ' + ', '.join(missing))
    if before_sha256 == after_sha256:
        raise KimiStreamError('the workload file did not change')
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
                'method': ('exact submitted prompts; tool call, tool result and final text lines of the stream-json stdout of each turn; '
                           'helper ledger; filesystem hashes; no native input'),
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer
