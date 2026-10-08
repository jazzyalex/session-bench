"""Decoder for the native record of one Kimi Code session (CLI ``kimi`` 2.1.1).

Kimi writes one directory per session:
``KIMI_CODE_HOME/sessions/<workspace-id>/<session-id>/``. Its files:

- ``agents/main/wire.jsonl``: the wire log. One JSON object per line, written
  in order and never rewritten. Every line has ``type`` and ``time`` (Unix
  milliseconds, UTC). The first line is ``metadata`` with ``protocol_version``.
- ``state.json``: one JSON object: ``id`` (the session id), ``version``,
  ``cwd``, ``createdAt``, ``updatedAt``, ``lastTurnReason``.
- ``logs/kimi-code.log`` (request timing lines), ``notify/state.json`` and
  ``agents/main/file-history/<key>@v<n>`` (whole copies of an edited file).

The read (rubric, "Duplicate-safety read") is two containers: ``wire.jsonl``
(every event, order, relation, model, usage, the changed file) and
``state.json`` (the session id). The decoder opens no other file. The log,
the notify state and the file copies are outside the read; density counts
them by size.

What the wire log holds, by record type:

- ``turn.prompt``: a submitted prompt: ``turnId`` (a number from 0),
  ``promptId``, ``input`` (text parts), ``origin.kind`` ``user``.
- ``context.append_message``: a message put into the model context: the
  prompt again (``origin.kind`` ``user``), or text that the harness injects
  (``origin.kind`` ``injection``; not a prompt).
- ``context.append_loop_event``: one event of the agent loop in ``event``:
  ``step.begin`` and ``step.end`` (``uuid`` of the step, ``turnId``, ``step``;
  the end holds ``finishReason``, ``usage`` and the provider ``messageId``),
  ``content.part`` (``think`` or ``text``, with ``stepUuid``), ``tool.call``
  (``toolCallId``, ``name``, ``args``, ``stepUuid``) and ``tool.result``
  (``toolCallId``, ``parentUuid`` = the ``uuid`` of its call, ``result`` with
  ``output`` and, only on a failure, ``isError: true``).
- ``llm.request``: one model request: ``turnStep`` (``<turnId>.<step>``),
  ``model``, ``modelAlias``, ``provider``.
- ``agent.message.appended``: written at the end of a turn: each message of
  the turn again (the prompt; each assistant message with its think and text
  parts and its tool calls; each tool result).
- ``file_history.tracked`` and ``file_history.checkpoint``: the path of an
  edited file (relative to the session ``cwd``) with the ``contentHash``
  (SHA-256) of its copy before the first edit and at the end of the turn.
- ``turn.step.retrying``: a failed model request that Kimi tries again (a
  provider rate limit). The failed attempt is a step of its own: ``step.begin``,
  ``llm.request``, ``step.end`` with ``finishReason`` ``error``. It holds no
  message, call or result.
- ``turn.step.interrupted``: written once when a turn ends with an error
  (``turnId``, ``step``, ``reason``, ``message`` = the error text). The turn
  then has ``turn.ended`` with ``reason`` ``failed`` and an ``error`` object,
  and no response. The record states no event.

Facts and their native keys:

- A turn is ``turn.prompt``. The response of a turn is the ``text`` part of
  the step that ends with ``finishReason`` ``end_turn``; it joins the turn by
  ``turnId``.
- A tool result joins its call by ``toolCallId`` and ``parentUuid``.
- A shell result holds no exit code field. A failed call has ``isError: true``
  and the last output line ``Command failed with exit code: N.``; the exit
  code is N and the output is the text before that line. A result without
  ``isError`` is a success: status ``success``, exit code 0 (see the adapter
  document for this reading).
- Model: ``llm.request`` of the response step (``turnStep``). Usage:
  ``usage`` of the ``step.end`` of the response step: ``inputOther``,
  ``output``, ``inputCacheRead``, ``inputCacheCreation``.
- No record declares a session total or a turn total of usage, so no
  reconciliation is stated.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex

from .adapters.claude_code_decoder import (
    classify_shell_segment, compound_shell_segments, helper_segment_results, split_shell_segments,
)
from .native_density import NativeDensityInventory, _unresolved, forward_occurrences, holds_call_arguments, roles_after_repeats, string_leaves

FORMAT = 'kimi-wire-jsonl-v1'
PROTOCOL_VERSION = '1.5'
STATE_VERSION = 2
SESSION_DIR = 'session'
WIRE = 'agents/main/wire.jsonl'
STATE = 'state.json'
READ = (WIRE, STATE)
TARGET = 'fixture_project/checkout.py'
SHELL_TOOL = 'Bash'
EDIT_TOOLS = ('Edit', 'Write')
USAGE_KEYS = (('inputOther', 'input_tokens'), ('output', 'output_tokens'), ('inputCacheRead', 'cache_read_tokens'),
              ('inputCacheCreation', 'cache_write_tokens'))
RECORD_TYPES = frozenset({
    'metadata', 'runtime.set_binding', 'profile.bind', 'permission.set_mode', 'turn.prompt', 'context.append_message',
    'agent.message.appended', 'agent.turn.started', 'plugin.session_start', 'context.append_loop_event', 'llm.tools_snapshot',
    'llm.request', 'usage.record', 'token_counting.measured', 'agent.turn.ended', 'turn.ended', 'token_counting.turn_recorded',
    'prompt.completed', 'turn.step.retrying', 'turn.step.interrupted', 'file_history.tracked', 'file_history.checkpoint',
})
LOOP_TYPES = frozenset({'step.begin', 'step.end', 'content.part', 'tool.call', 'tool.result'})
_HARNESS = re.compile(r'You are (?P<name>[A-Za-z][A-Za-z0-9 ]{0,40}?) (?P<surface>CLI)\b')
_FAILED = re.compile(r'\nCommand failed with exit code: (-?\d+)\.\Z')
_CANARY = re.compile(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+')
_NONCE = re.compile(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)')
_REVISION = re.compile(r'\b(?:Requirement|Correction) R([12])\b')
_MAX_BYTES = 64 * 1024 * 1024
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'explanation', 'file_change'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}


class KimiWireError(ValueError):
    """The bytes are not a wire log and a state file of one Kimi session."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _strict(data, label):
    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                raise KimiWireError(f'{label} holds a duplicate key')
            found[key] = value
        return found

    def constant(value):
        raise KimiWireError(f'{label} holds a non-finite number')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, ValueError) as error:
        raise KimiWireError(f'{label} is not JSON') from error


def read_wire(data):
    """``[(line number, record)]`` of a wire log; a blank line is skipped, any other non-object line raises."""
    if len(data) > _MAX_BYTES:
        raise KimiWireError('wire log exceeds the byte limit')
    rows = []
    for number, line in enumerate(data.split(b'\n'), 1):
        raw = line.rstrip(b'\r')
        if not raw.strip():
            continue
        record = _strict(raw, f'wire line {number}')
        if not isinstance(record, dict):
            raise KimiWireError(f'wire line {number} is not an object')
        rows.append((number, record))
    return rows


def record_bytes(record):
    """Logical bytes of one record: compact key-sorted UTF-8 JSON (``canonical-native-json-record-utf8-v1``)."""
    return len(json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _text_parts(content):
    """The joined text of the ``text`` parts of a content list; None when there is none."""
    if not isinstance(content, list):
        return None
    parts = [part['text'] for part in content if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)]
    return ''.join(parts) if parts else None


def targets_workload_file(path):
    if not isinstance(path, str):
        return False
    path = path.replace('\\', '/')
    return (path == 'checkout.py' or path.endswith('/checkout.py')) and '/snapshots/' not in '/' + path


def shell_outcome(result):
    """(output text, status, exit code) of one shell ``tool.result``.

    A failure: ``isError`` is true and the output ends with the line
    ``Command failed with exit code: N.``; the code is N and the text is the
    output before that line. An error without that line has no exit code. A
    result without ``isError`` is a success with exit code 0.
    """
    output = result.get('output') if isinstance(result.get('output'), str) else ''
    if result.get('isError') is True:
        failed = _FAILED.search(output)
        return (output[:failed.start()], 'failure', int(failed.group(1))) if failed else (output, 'failure', None)
    return output, 'success', 0


def harness_identity(prompt):
    """(harness, surface) named by the first sentence of the system prompt, or (None, None)."""
    named = _HARNESS.match(prompt) if isinstance(prompt, str) else None
    return (named.group('name'), named.group('surface').lower()) if named else (None, None)


def _usage(native):
    """The named counts of one ``usage`` object. A missing key is not a zero: it is left out."""
    if not isinstance(native, dict):
        return None
    counts = {name: native[key] for key, name in USAGE_KEYS if type(native.get(key)) is int and native[key] >= 0}
    return counts or None


def decode_kimi_session(wire, state, *, artifact=SESSION_DIR + '/' + WIRE):
    """Decode one session from the bytes of ``wire.jsonl`` and ``state.json``. Nothing else is read.

    Returns the native result that the comparator reads. A record of an
    unknown type, a result without a call, or a failed step that holds content
    is a diagnostic; the decode is then not clean.
    """
    out = {'status': 'ok', 'format': FORMAT, 'session_id': None, 'diagnostics': [], 'turns': [], 'responses': [], 'actions': [],
           'results': [], 'file_changes': [], 'relations': [], 'reconciliation': [], 'thread': [], 'commentary': [],
           'protocol_version': None, 'state_version': None, 'harness': None, 'surface': None, 'workdir': None, 'retried_requests': 0, 'interrupted_steps': 0}

    def problem(code, **detail):
        out['diagnostics'].append({'code': code, **detail})

    try:
        rows = read_wire(wire)
        document = _strict(state, 'state.json')
    except KimiWireError as error:
        out['status'] = 'unsupported'
        problem('decode_error', detail=str(error))
        return out
    if isinstance(document, dict) and isinstance(document.get('id'), str) and document['id']:
        out['session_id'] = document['id']
        out['state_version'] = document.get('version') if type(document.get('version')) is int else None
    else:
        problem('malformed_record', record=STATE)
    if not rows or rows[0][1].get('type') != 'metadata' or not isinstance(rows[0][1].get('protocol_version'), str):
        problem('unsupported_schema', detail='the wire log does not open with a metadata record')
    else:
        out['protocol_version'] = rows[0][1]['protocol_version']

    def locator(number):
        return {'artifact': artifact, 'line': number}

    turns, steps, requests, calls, pending, tracked, ended = {}, {}, {}, {}, {}, {}, {}
    for number, record in rows:
        kind, time = record.get('type'), record.get('time')
        if kind not in RECORD_TYPES or (number > 1 and type(time) is not int):
            problem('unknown_record', line=number, type=str(kind))
            continue
        base = {'sequence': number, 'timestamp': time, 'locator': locator(number)}
        if kind == 'profile.bind':
            out['harness'], out['surface'] = harness_identity(record.get('systemPrompt'))
            disclosure = record.get('environmentDisclosure')
            out['workdir'] = disclosure.get('cwd') if isinstance(disclosure, dict) and isinstance(disclosure.get('cwd'), str) else None
        elif kind == 'turn.prompt':
            text, turn, prompt = _text_parts(record.get('input')), record.get('turnId'), record.get('promptId')
            origin = record.get('origin') if isinstance(record.get('origin'), dict) else {}
            if type(turn) is not int or not isinstance(prompt, str) or text is None or origin.get('kind') != 'user' or turn in turns:
                problem('malformed_record', line=number, type=kind)
                continue
            revision = _REVISION.search(text)
            turns[turn] = {**base, 'id': prompt, 'turn_id': prompt, 'native_turn': turn, 'role': 'user', 'text': text,
                           'revision': 'r' + revision.group(1) if revision else None}
            out['turns'].append(turns[turn])
            out['thread'].append({'id': prompt, 'role': 'user', 'ordinal': number, 'parent_id': None})
        elif kind == 'llm.request':
            if isinstance(record.get('turnStep'), str):
                requests[record['turnStep']] = record
        elif kind == 'turn.ended':
            ended[record.get('turnId')] = record.get('reason')
        elif kind == 'turn.step.retrying':
            out['retried_requests'] += 1
        elif kind == 'turn.step.interrupted':
            # A turn that ended with an error. It has no response; the events before the error stay.
            out['interrupted_steps'] += 1
        elif kind == 'file_history.tracked':
            entry = record.get('entry') if isinstance(record.get('entry'), dict) else {}
            if isinstance(record.get('path'), str) and isinstance(entry.get('contentHash'), str) and type(entry.get('version')) is int:
                tracked.setdefault((record.get('turnId'), record['path']), {**base, 'hash': entry['contentHash'], 'version': entry['version']})
        elif kind == 'file_history.checkpoint':
            entries = record.get('entries') if isinstance(record.get('entries'), dict) else {}
            for path, entry in entries.items():
                before = tracked.get((record.get('turnId'), path))
                turn = turns.get(record.get('turnId'))
                if (before is None or turn is None or not isinstance(entry, dict) or not isinstance(entry.get('contentHash'), str)
                        or type(entry.get('version')) is not int or entry['version'] <= before['version']
                        or entry['contentHash'] == before['hash'] or record.get('phase') != 'end'):
                    continue
                absolute = (out['workdir'].rstrip('/') + '/' + path) if isinstance(out['workdir'], str) and not path.startswith('/') else path
                out['file_changes'].append({**base, 'id': f'file-history:{path}@v{entry["version"]}', 'path': absolute, 'native_path': path,
                                            'before_sha256': before['hash'], 'after_sha256': entry['contentHash'],
                                            'hash_source': 'native_explicit_hashes', 'turn_id': turn['id'],
                                            'preimage_locator': before['locator']})
        elif kind == 'context.append_loop_event':
            event = record.get('event') if isinstance(record.get('event'), dict) else {}
            inner = event.get('type')
            if inner not in LOOP_TYPES:
                problem('unknown_record', line=number, type=f'{kind}:{inner}')
                continue
            if inner == 'step.begin':
                steps[event.get('uuid')] = {'turn': event.get('turnId'), 'step': event.get('step'), 'begin': number, 'texts': [], 'calls': [],
                                            'thinks': 0, 'end': None}
                continue
            if inner == 'step.end':
                step = steps.get(event.get('uuid'))
                if step is None:
                    problem('malformed_record', line=number, type=inner)
                    continue
                step['end'] = {**base, 'event': event}
                if event.get('finishReason') == 'error' and (step['texts'] or step['calls'] or step['thinks']):
                    problem('unjoined_execution', line=number, detail='a failed step holds content')
                continue
            if inner == 'tool.result':
                call = calls.get(event.get('toolCallId'))
                result = event.get('result')
                if call is None or call['uuid'] != event.get('parentUuid') or not isinstance(result, dict) or event['toolCallId'] in pending:
                    problem('unjoined_execution', line=number, detail='a tool result names no open call')
                    continue
                pending[event['toolCallId']] = True
                _add_result(out, {**base, 'turn_id': call['turn_id']}, event['toolCallId'], call, result)
                out['thread'].append({'id': event['toolCallId'] + ':result', 'role': 'tool', 'ordinal': number, 'parent_id': call['uuid']})
                continue
            step = steps.get(event.get('stepUuid'))
            turn = turns.get(int(step['turn'])) if step is not None and str(step['turn']).isdigit() else None
            if step is None or turn is None or str(event.get('turnId')) != str(step['turn']):
                problem('unjoined_execution', line=number, detail='a loop event names no step of a submitted turn')
                continue
            if inner == 'content.part':
                part = event.get('part') if isinstance(event.get('part'), dict) else {}
                if part.get('type') == 'think' and isinstance(part.get('think'), str):
                    step['thinks'] += 1
                elif part.get('type') == 'text' and isinstance(part.get('text'), str) and isinstance(event.get('uuid'), str):
                    step['texts'].append({**base, 'id': event['uuid'], 'turn_id': turn['id'], 'text': part['text'], 'step': event['stepUuid']})
                    out['thread'].append({'id': event['uuid'], 'role': 'assistant', 'ordinal': number, 'parent_id': turn['id']})
                else:
                    problem('unknown_record', line=number, type=f'content.part:{part.get("type")}')
                continue
            call, name, arguments = event.get('toolCallId'), event.get('name'), event.get('args')
            if not isinstance(call, str) or not call or call in calls or not isinstance(name, str) or not isinstance(arguments, dict):
                problem('malformed_record', line=number, type=inner)
                continue
            calls[call] = {'uuid': event.get('uuid'), 'turn_id': turn['id'], 'name': name, 'arguments': arguments, 'step': event['stepUuid']}
            step['calls'].append(call)
            calls[call]['pending'] = _add_call(out, {**base, 'turn_id': turn['id']}, call, name, arguments)
            out['thread'].append({'id': event.get('uuid') or call, 'role': 'assistant', 'ordinal': number, 'parent_id': turn['id']})
    for call in calls:
        if call not in pending:
            problem('unjoined_execution', detail='a tool call has no result', call=call)
    # A response: the text of the step that ends the turn. Its model is the request of that step, its usage the step end.
    for identity, step in steps.items():
        end = step['end']['event'] if step['end'] else {}
        for text in step['texts']:
            if end.get('finishReason') != 'end_turn' or step['calls']:
                out['commentary'].append(text)
                continue
            request = requests.get(f"{step['turn']}.{step['step']}") or {}
            response = {**{key: text[key] for key in ('sequence', 'timestamp', 'locator', 'id', 'turn_id', 'text')}, 'role': 'assistant',
                        'native_step': identity, 'finish_reason': end.get('finishReason'), 'usage_locator': step['end']['locator']}
            if ended.get(int(step['turn'])) == 'completed':
                response['status'] = 'completed'
            # The response marker of the text. A response can also name the run canary of a command; that is no response marker.
            markers = _CANARY.findall(text['text'])
            if len(markers) == 1 and text['text'].rstrip().endswith(markers[0]):
                response['canary'] = markers[0]
            if isinstance(request.get('model'), str) and isinstance(request.get('modelAlias'), str):
                response.update(model_id=request['model'], configuration={'model_alias': request['modelAlias']})
            usage = _usage(end.get('usage'))
            if usage is not None:
                response['usage'] = usage
            out['responses'].append(response)
            out['relations'].append({'kind': 'turn_response', 'from_id': text['turn_id'], 'to_id': text['id'], 'locator': text['locator'],
                                     'native_key': 'turnId of the text part and of turn.prompt'})
    out['responses'].sort(key=lambda row: row['sequence'])
    if any(row['code'] in ('unknown_record', 'malformed_record', 'unsupported_schema') for row in out['diagnostics']):
        out['status'] = 'unsupported'
    return out


def _add_call(out, base, call, name, arguments):
    """Add the action, or the segment actions, of one tool call. Returns what its result needs."""
    action = {**base, 'id': call, 'call_id': call, 'name': name, 'input': arguments}
    if isinstance(arguments.get('cwd'), str):
        action['cwd'] = arguments['cwd']
    command = arguments.get('command')
    if name != SHELL_TOOL or not isinstance(command, str):
        path = arguments.get('path')
        if name in EDIT_TOOLS and targets_workload_file(path):
            action.update(target=TARGET, action_kind='edit')
        elif isinstance(path, str):
            action['target'] = path
        out['actions'].append(action)
        return {'kind': 'plain', 'tool': name}
    action['command'] = command
    compound = compound_shell_segments('bash', {'command': command})
    if compound is not None:
        # Compound shell call rule: the call is the native record of each scored segment. Each segment keeps the call id.
        parts = []
        for segment in compound:
            part_id = f"{call}:segment-{segment['index']}"
            part = {**base, 'id': part_id, 'call_id': call, 'parent_call_id': call, 'segment_index': segment['index'], 'name': name,
                    'input': {'command': segment['text']}, 'command': segment['text']}
            if 'cwd' in action:
                part['cwd'] = action['cwd']
            if segment['kind'] == 'helper':
                part.update(argv=shlex.split(segment['text']), action_kind='inspect' if segment['phase'] == 'inspect' else 'test')
            else:
                part.update(action_kind='edit', target=TARGET)
            out['actions'].append(part)
            parts.append(part_id)
        return {'kind': 'compound', 'segments': compound, 'parts': parts}
    segments = split_shell_segments(command)
    scored = [(segment, classify_shell_segment(segment)) for segment in segments or []]
    helpers = [(segment, role) for segment, role in scored if role is not None and role['kind'] == 'helper']
    last = None
    if len(helpers) == 1 and sum(role is not None for _, role in scored) == 1:
        segment, role = helpers[0]
        action.update(argv=shlex.split(segment['text']), action_kind='inspect' if role['phase'] == 'inspect' else 'test',
                      scored_segment_index=segment['index'])
        last = segment['index'] == segments[-1]['index']
    else:
        try:
            action['argv'] = shlex.split(command)
        except ValueError:
            pass
    out['actions'].append(action)
    return {'kind': 'plain', 'helper_is_last': last}


def _add_result(out, base, identity, call, native):
    pending = call['pending']
    if call['name'] == SHELL_TOOL:
        output, status, code = shell_outcome(native)
    else:
        output = native.get('output') if isinstance(native.get('output'), str) else ''
        status, code = ('failure' if native.get('isError') is True else 'success'), None
    relation = {'kind': 'action_result', 'locator': base['locator'], 'native_key': 'toolCallId, and parentUuid = uuid of the call'}
    if pending['kind'] == 'compound':
        found = helper_segment_results(pending['segments'], output, status == 'failure')
        for segment, part_id in zip(pending['segments'], pending['parts']):
            line = found.get(segment['index']) if segment['kind'] == 'helper' else None
            if line is None:
                continue
            # The call's own exit code counts only for the last segment of the call.
            value = code if segment['follows'] is None else line['exit_code']
            outcome = {**base, 'id': part_id + ':result', 'call_id': identity, 'parent_call_id': identity, 'action_id': part_id, 'output': line['output']}
            if type(value) is int:
                outcome.update(exit_code=value, status='failure' if value else 'success')
            nonces = _NONCE.findall(line['output'])
            if len(nonces) == 1:
                outcome['helper_nonce'] = nonces[0]
            out['results'].append(outcome)
            out['relations'].append({**relation, 'from_id': part_id, 'to_id': outcome['id']})
        return
    result = {**base, 'id': identity + ':result', 'call_id': identity, 'action_id': identity, 'output': output}
    if pending.get('tool') is not None:
        result['status'] = status
    elif pending.get('helper_is_last') is not False:
        # A call with one helper segment: the call's status is the helper's only when the helper is the last segment.
        result['status'] = status
        if type(code) is int:
            result['exit_code'] = code
        # The helper output line without the line end of the shell output, as the helper ledger states it.
        lines = [line for line in output.split('\n') if _NONCE.match(line)]
        if len(lines) == 1:
            result['output'] = lines[0]
    nonces = _NONCE.findall(output)
    if len(nonces) == 1:
        result['helper_nonce'] = nonces[0]
    out['results'].append(result)
    out['relations'].append({**relation, 'from_id': identity, 'to_id': result['id']})


def decode_kimi_native(native):
    """Decode the one session of a packet's native directory. Opened: ``wire.jsonl`` and ``state.json``."""
    native = Path(native)
    files = {}
    for name in READ:
        path = native / SESSION_DIR / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError('unsafe Kimi native input')
        files[name] = path.read_bytes()
    return decode_kimi_session(files[WIRE], files[STATE])


def add_native_final_after_chain(decoded, instance):
    """Add the final-after relation when R2, edit, edit result, final test, its result and the R2 response rise in line order.

    The keys are native: the line order of the append-only wire log, the
    ``turnId`` of every event of the R2 turn, and ``toolCallId``, which binds
    each result to its call. No time is used. The edit must be one edit of the
    workload file with a successful result, and the final test must have exit
    code 0.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    inside = lambda rows: [row for row in rows if row.get('turn_id') == r2['id']]  # noqa: E731
    canary = instance['turns'][1]['response_canary']
    responses = [row for row in inside(decoded['responses']) if row.get('canary') == canary]
    edits = [row for row in inside(decoded['actions']) if row.get('action_kind') == 'edit' and row.get('target') == TARGET]
    finals = [row for row in inside(decoded['actions']) if row.get('action_kind') == 'test' and 'final' in row.get('argv', [])]
    results = {row['action_id']: row for row in decoded['results']}
    if len(responses) != 1 or not edits or len(finals) != 1:
        return
    edit = edits[-1]
    edit_result, final_result = results.get(edit['id']), results.get(finals[0]['id'])
    if (not edit_result or edit_result.get('status') != 'success' or not final_result or final_result.get('status') != 'success'
            or final_result.get('exit_code') != 0):
        return
    order = [r2['sequence'], edit['sequence'], edit_result['sequence'], finals[0]['sequence'], final_result['sequence'], responses[0]['sequence']]
    if order != sorted(order) or len(set(order)) != len(order):
        return
    decoded['relations'].append({'kind': 'final_after', 'from_id': r2['id'], 'to_id': finals[0]['id'], 'locator': finals[0]['locator'],
                                 'native_order_lines': order, 'native_call_ids': [edit['call_id'], finals[0]['call_id']],
                                 'method': 'rising line order in the append-only wire log inside the R2 turn (turnId); each result bound to its call by toolCallId'})


# --- Statements and logical records (duplicate safety and density) ---

def wire_statements(rows):
    """(base roles, statements) for the records of a wire log, in file order. The count is made on the raw records.

    - ``turn.prompt`` states its prompt. ``context.append_message`` with
      ``origin.kind`` ``user`` holds the same text under the same id and
      states the prompt again. A message with ``origin.kind`` ``injection``
      is context of the harness, marked by that native field: no event.
    - A ``text`` part states an assistant message, a ``tool.call`` its call
      and a ``tool.result`` its result. A ``think`` part is model reasoning in
      clear text; it is no message event.
    - ``agent.message.appended`` states again: the prompt (same ``promptId``
      and text); an assistant text of the turn (equal text); a call that it
      names by ``id`` when it holds all text arguments of the call; a result
      that it names by ``toolCallId`` when it holds the full output.

    No field marks a record as superseded, so every statement is active.
    """
    prompts, texts, calls, names, outputs = {}, {}, {}, {}, {}
    for number, record in rows:
        event = record.get('event') if record.get('type') == 'context.append_loop_event' and isinstance(record.get('event'), dict) else {}
        if record.get('type') == 'turn.prompt' and isinstance(record.get('promptId'), str):
            prompts[record['promptId']] = _text_parts(record.get('input'))
        elif event.get('type') == 'content.part' and isinstance(event.get('part'), dict) and event['part'].get('type') == 'text':
            texts.setdefault(event['part'].get('text'), []).append(event.get('uuid') or f'line-{number}')
        elif event.get('type') == 'tool.call' and isinstance(event.get('toolCallId'), str):
            calls[event['toolCallId']] = event.get('args')
            names[event['toolCallId']] = event.get('name')
        elif event.get('type') == 'tool.result' and isinstance(event.get('toolCallId'), str) and isinstance(event.get('result'), dict):
            outputs[event['toolCallId']] = event['result'].get('output')
    restated = {text: list(ids) for text, ids in texts.items()}
    roles, statements = [], []
    for number, record in rows:
        kind, events, role = record.get('type'), [], 'metadata'
        if kind not in RECORD_TYPES:
            role = 'unknown'
        elif kind == 'metadata':
            role = 'session'
        elif kind in ('profile.bind', 'llm.tools_snapshot'):
            role = 'system'
        elif kind in ('file_history.tracked', 'file_history.checkpoint'):
            role = 'file_change'
        elif kind == 'turn.prompt':
            origin = record.get('origin') if isinstance(record.get('origin'), dict) else {}
            if origin.get('kind') == 'user' and isinstance(record.get('promptId'), str):
                role, events = 'user_message', [f"message:{record['promptId']}"]
        elif kind == 'context.append_message':
            message = record.get('message') if isinstance(record.get('message'), dict) else {}
            origin = message.get('origin') if isinstance(message.get('origin'), dict) else {}
            role = 'system'
            if origin.get('kind') == 'user' and message.get('role') == 'user':
                role = 'user_message'
                text = _text_parts(message.get('content'))
                if message.get('id') in prompts and text == prompts[message['id']]:
                    events = [f"message:{message['id']}"]
                elif text is not None:
                    events = [f'message:line-{number}']
            elif origin.get('kind') != 'injection':
                role = 'unknown'
        elif kind == 'context.append_loop_event':
            event = record.get('event') if isinstance(record.get('event'), dict) else {}
            inner, part = event.get('type'), event.get('part') if isinstance(event.get('part'), dict) else {}
            if inner == 'content.part' and part.get('type') == 'text':
                role, events = 'assistant_message', [f"message:{event.get('uuid') or 'line-%d' % number}"]
            elif inner == 'content.part' and part.get('type') == 'think':
                role = 'explanation'
            elif inner == 'tool.call' and isinstance(event.get('toolCallId'), str):
                role, events = 'tool_call', [f"call:{event['toolCallId']}"]
            elif inner == 'tool.result' and isinstance(event.get('toolCallId'), str):
                role, events = 'tool_result', [f"result:{event['toolCallId']}"]
                if event['toolCallId'] in calls and holds_call_arguments(calls[event['toolCallId']], event.get('result')):
                    events.append(f"call:{event['toolCallId']}")
            elif inner not in LOOP_TYPES:
                role = 'unknown'
        elif kind == 'agent.message.appended':
            wrapper = record.get('message') if isinstance(record.get('message'), dict) else {}
            message = wrapper.get('message') if isinstance(wrapper.get('message'), dict) else {}
            meta = wrapper.get('meta') if isinstance(wrapper.get('meta'), dict) else {}
            text = _text_parts(message.get('content'))
            if message.get('role') == 'user':
                role = 'user_message'
                if meta.get('promptId') in prompts and text == prompts[meta['promptId']]:
                    events = [f"message:{meta['promptId']}"]
                elif text is not None:
                    events = [f'message:line-{number}']
            elif message.get('role') == 'assistant':
                role = 'assistant_message'
                for part in message.get('content') if isinstance(message.get('content'), list) else []:
                    if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str):
                        waiting = restated.get(part['text'])
                        events.append(f'message:{waiting.pop(0)}' if waiting else f'message:line-{number}')
                listed = message.get('toolCalls') if isinstance(message.get('toolCalls'), list) else []
                for item in listed:
                    identity = item.get('id') if isinstance(item, dict) else None
                    # A call whose only arguments name its target is restated only by a record that also holds the tool name.
                    if identity in calls and holds_call_arguments(calls[identity], string_leaves(item), names_tool=item.get('name') == names[identity]):
                        events.append(f'call:{identity}')
                    elif isinstance(identity, str):
                        events.append(f'call:line-{number}:{identity}')
                if listed and not any(event.startswith('message:') for event in events):
                    role = 'tool_call'
            elif message.get('role') == 'tool' and isinstance(message.get('toolCallId'), str):
                role, identity = 'tool_result', message['toolCallId']
                held = isinstance(outputs.get(identity), str) and bool(outputs[identity]) and outputs[identity] in string_leaves(message.get('content'))
                events = [f'result:{identity}' if held else f'result:line-{number}:{identity}']
            else:
                role = 'unknown'
        roles.append(role)
        statements.append(tuple(dict.fromkeys(events)))
    return roles, statements


def read_kimi_family(native, *, artifacts):
    """Return (records, locators, exceptions, occurrences) for the session directory of a packet.

    ``artifacts`` is the packet inventory (path, sha256, size). The read is
    the wire log and the state file. Every wire line is one record, counted
    as compact key-sorted JSON; the state file is one record counted the same
    way. A file outside the read is one record of its size with the fixed
    role ``metadata``; its payload is not opened.
    """
    native = Path(native)
    expected = {row['path']: row for row in artifacts}
    prefix = SESSION_DIR + '/'
    if len(expected) != len(artifacts) or not {prefix + name for name in READ} <= set(expected) or any(not name.startswith(prefix) for name in expected):
        raise ValueError('Kimi family inventory is not one session directory with a wire log and a state file')
    data = {}
    for name in READ:
        path = native / prefix / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Kimi family member is missing')
        data[name] = path.read_bytes()
        if len(data[name]) > _MAX_BYTES or sha(data[name]) != expected[prefix + name]['sha256']:
            raise ValueError('Kimi family member differs from its inventory')
    records, locators, exceptions = [], [], []

    def add(record_id, kind, logical_bytes, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': logical_bytes,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': proof})

    rows = read_wire(data[WIRE])
    lines = {number: line.rstrip(b'\r') for number, line in enumerate(data[WIRE].split(b'\n'), 1)}
    base, statements = wire_statements(rows)
    ids = [f'{prefix}{WIRE}:line-{number}' for number, _ in rows]
    for record_id, role, (number, record) in zip(ids, roles_after_repeats(base, statements), rows, strict=True):
        if role == 'unknown':
            exceptions.append({'code': 'unknown_record', 'record': record_id})
        add(record_id, role, record_bytes(record), sha(lines[number]))
    occurrences = forward_occurrences(ids, statements)
    state = _strict(data[STATE], 'state.json')
    add(prefix + STATE, 'session', record_bytes(state), sha(data[STATE]))
    for name in sorted(set(expected) - {prefix + item for item in READ}):
        size = expected[name].get('size_bytes')
        if type(size) is not int or size < 0:
            raise ValueError('Kimi outside file entry is malformed')
        add('outside-the-read:' + name, 'metadata', size, sha(json.dumps([name, size], separators=(',', ':')).encode('utf-8')))
    return records, locators, exceptions, occurrences


def build_kimi_density(native, *, artifacts, complete_record_family=False):
    """Density evidence for the session directory; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _, _ = read_kimi_family(native, artifacts=artifacts)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Kimi density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Kimi density inventory is empty')
    locators.append({'id': 'rule:canonical-native-json-record-utf8-v1:session_bench/kimi_wire_records.py', 'sha256': sha(Path(__file__).read_bytes())})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())


def remove_final_response(wire, canary):
    """Selected-loss control: the wire log without every record that holds the response text ending with ``canary``.

    Removed: the ``text`` part of the response and its copy in
    ``agent.message.appended``. Returns ``(new bytes, removed records)``.
    """
    kept, removed = [], []
    for number, line in enumerate(wire.split(b'\n'), 1):
        raw = line.rstrip(b'\r')
        if not raw.strip():
            kept.append(line)
            continue
        record = _strict(raw, f'wire line {number}')
        event = record.get('event') if isinstance(record.get('event'), dict) else {}
        part = event.get('part') if isinstance(event.get('part'), dict) else {}
        wrapper = record.get('message') if record.get('type') == 'agent.message.appended' and isinstance(record.get('message'), dict) else {}
        message = wrapper.get('message') if isinstance(wrapper.get('message'), dict) else {}
        text = part.get('text') if event.get('type') == 'content.part' and part.get('type') == 'text' else (
            _text_parts(message.get('content')) if message.get('role') == 'assistant' else None)
        if isinstance(text, str) and text.rstrip().endswith(canary):
            removed.append({'path': SESSION_DIR + '/' + WIRE, 'line': number, 'sha256': sha(raw), 'type': record.get('type'),
                            'control_transformation': 'remove the selected response text record and its end-of-turn copy only'})
        else:
            kept.append(line)
    if len([row for row in removed if row['type'] == 'context.append_loop_event']) != 1:
        raise ValueError('Kimi loss control requires one final response text part')
    return b'\n'.join(kept), removed
