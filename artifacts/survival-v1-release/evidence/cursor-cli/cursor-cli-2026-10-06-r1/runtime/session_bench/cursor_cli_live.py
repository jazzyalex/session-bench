"""Cursor CLI native facts from the chat store, and the independent stream observer.

Native decoding never receives observer bytes. The decoder opens two files of
the isolated root: ``store.db`` (tables ``meta`` and ``blobs``) and the sidecar
``meta.json`` (declared schema version). It never opens the agent transcript
JSONL, ``cli-config.json`` or ``statsig-cache.json``.

The store states the conversation twice. The turn tree (root field 8) holds
each prompt as the user typed it and each step with its own times and call id.
The message list (root field 1) holds the same events as JSON messages, as
they are sent to the model, with the model name. A fact is taken from the
turn tree. The model name of a response comes from the JSON message that
states the same response: the two statements are bound by their position in
the turn and by equal text (a tool call also by its call id). A turn whose two
statements do not agree is a contract exception.

Decoder contract ``cursor-cli-chat-store-v1``: ``PRAGMA user_version`` 1,
sidecar ``schemaVersion`` 1, the field maps of ``cursor_cli_store.SCHEMA``.
Any other value makes the decode unsupported.

The store holds no token count of a request: no input, output or cache count
and no session total. Root field 5 is an estimate of the context-window size.
It is kept as ``context_window`` and is never used as a usage record.
"""
import json
from pathlib import Path
import re
import shlex

from .adapters.claude_code_decoder import (
    classify_shell_segment, compound_shell_segments, helper_segment_results, split_shell_segments,
)
from .cursor_cli_store import (
    SIDECAR_SCHEMA_VERSION, STORE_FORMAT, TOOLS, USER_VERSION, CursorStoreError, classify_blobs, contract_exceptions,
    first, read_meta, read_store, sha,
)
from .live_metric_comparator import _validate_observer

TARGET = 'fixture_project/checkout.py'
_CANARY = re.compile(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+')
_NONCE = re.compile(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)')
_MAX_BYTES = 64 * 1024 * 1024
_STREAM_TOOLS = {'shellToolCall': 'Shell', 'globToolCall': 'Glob', 'readToolCall': 'Read', 'editToolCall': 'Write'}


def relative(path):
    return 'fixture_project/' + path.split('/fixture_project/', 1)[1] if isinstance(path, str) and '/fixture_project/' in path else path


def find_store(native):
    """(relative path of the session directory, session id) of the one chat store of a copied root."""
    native = Path(native)
    found = sorted(path for path in native.glob('cursor-config/chats/*/*/store.db') if path.is_file() and not path.is_symlink())
    if len(found) != 1:
        raise ValueError('copied root must hold exactly one chat store')
    directory = found[0].parent
    return directory.relative_to(native).as_posix(), directory.name


def _helper_kind(command):
    return 'inspect' if 'bench_check.py inspect' in command else 'test' if re.search(r'bench_check\.py (?:baseline|final)', command) else None


def scored_segments(command):
    """(compound segments or None, number of scored segments) of one shell command."""
    compound = compound_shell_segments('shell', {'command': command})
    if compound is not None:
        return compound, len(compound)
    segments = split_shell_segments(command)
    return None, 0 if segments is None else sum(classify_shell_segment(segment) is not None for segment in segments)


def _tool_fields(tool):
    """Name, input, output, status and exit code of one tool step; None for a shape outside the contract."""
    kinds = [number for number in TOOLS if number in tool]
    if len(kinds) != 1 or len(tool[kinds[0]]) != 1:
        return None
    name, payload = TOOLS[kinds[0]], tool[kinds[0]][0]
    args, result = first(payload, 1), first(payload, 2)
    if not isinstance(args, dict):
        return None
    out = {'name': name, 'status': None, 'output': None}
    if name == 'Shell':
        success, failure = first(result, 1), first(result, 2)
        out['input'] = {'command': first(args, 1), 'working_directory': first(args, 2), 'description': first(args, 15)}
        if (success is None) == (failure is None):
            return out
        body = success if success is not None else failure
        # A success holds no exit code field: the value is zero.
        out.update(status='success' if success is not None else 'failure', output=first(body, 5) or '',
                   exit_code=0 if success is not None else first(failure, 3))
    elif name == 'Glob':
        out['input'] = {'target_directory': first(args, 1), 'glob_pattern': first(args, 2)}
        out['status'] = 'success' if first(result, 1) is not None else None
    elif name == 'Read':
        out['input'] = {'path': first(args, 1)}
        if first(result, 1) is not None:
            out.update(status='success', output=first(result, 1, 1) or '')
    else:
        body = first(result, 1)
        out['input'] = {'path': first(args, 1), 'contents': first(args, 6)}
        if body is not None:
            out.update(status='success', output=first(body, 8) or '', before=first(body, 6) or '', after=first(body, 7) or '')
    out['input'] = {key: value for key, value in out['input'].items() if value is not None}
    return out


def message_parts(message):
    """(kind, value) of the text and tool-call parts of one JSON assistant message, in order."""
    parts = []
    for part in message.get('content') if isinstance(message.get('content'), list) else []:
        if isinstance(part, dict) and part.get('type') == 'text':
            parts.append(('text', part.get('text')))
        elif isinstance(part, dict) and part.get('type') == 'tool-call':
            parts.append(('call', part))
    return parts


def message_model(message):
    """The one model name that the reasoning parts of a JSON assistant message state, or None."""
    names = set()
    for part in message.get('content') if isinstance(message.get('content'), list) else []:
        options = part.get('providerOptions') if isinstance(part, dict) else None
        cursor = options.get('cursor') if isinstance(options, dict) else None
        if isinstance(cursor, dict) and isinstance(cursor.get('modelName'), str) and cursor['modelName']:
            names.add(cursor['modelName'])
    return next(iter(names)) if len(names) == 1 else None


def prompt_text(message):
    """The joined text of a JSON user message with parts (a submitted prompt); None for any other message."""
    content = message.get('content')
    if message.get('role') != 'user' or not isinstance(content, list):
        return None
    return ''.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')


def decode_cursor_cli_store(database, sidecar, *, session_id, artifact='store.db'):
    """Decode the store bytes and the sidecar bytes of one session into comparator facts."""
    out = {key: [] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'records')}
    store = read_store(database)
    blobs, exceptions = classify_blobs(store)
    exceptions = contract_exceptions(store, sidecar, session_id) + exceptions
    out.update(format=STORE_FORMAT, status='ok', session_id=session_id, physical_sha256=store['physical_sha256'],
               user_version=store['user_version'], sidecar_schema_version=None, harness=None, surface=None,
               context_window=None, message_bindings=[], diagnostics=[{**item, 'severity': 'error'} for item in exceptions])
    try:
        version = json.loads(sidecar).get('schemaVersion')
        out['sidecar_schema_version'] = version if type(version) is int else None
    except (ValueError, AttributeError):
        pass

    def locator(identity):
        return {'artifact': artifact, 'table': 'blobs', 'line': blobs[identity]['rowid'], 'blob_id': identity,
                'physical_sha256': store['physical_sha256']}

    def problem(code, **detail):
        out['diagnostics'].append({'code': code, 'severity': 'error', **detail})

    for identity, entry in blobs.items():
        out['records'].append({'raw': {'class': entry['class'], 'current': entry['current']}, 'locator': locator(identity), 'timestamp': None})
    stamps = {row['locator']['blob_id']: row for row in out['records']}
    try:
        latest = read_meta(store).get('latestRootBlobId')
    except CursorStoreError:
        latest = None
    root = blobs.get(latest, {}).get('value') if blobs.get(latest, {}).get('class') == 'root' else None
    if root is None:
        out['status'] = 'unsupported'
        return out
    out['surface'] = first(root, 22)
    used, window = first(root, 5, 1), first(root, 5, 2)
    if used is not None or window is not None:
        out['context_window'] = {'estimated_tokens': used, 'window_tokens': window, 'locator': locator(latest),
                                 'scope': 'estimate of the next request size; not a usage record'}
    messages = [(identity, blobs[identity]['value']) for identity in root.get(1, []) if blobs.get(identity, {}).get('class') == 'message']
    if any(isinstance(message.get('providerOptions'), dict) and 'cursor' in message['providerOptions'] for _, message in messages):
        out['harness'] = 'cursor'
    prompts = [(index, prompt_text(message)) for index, (_, message) in enumerate(messages) if prompt_text(message) is not None]
    sequence = 0
    changes = []
    turn_ids = [identity for identity in root.get(8, []) if blobs.get(identity, {}).get('class') == 'turn']
    for number, turn_blob in enumerate(turn_ids):
        body = first(blobs[turn_blob]['value'], 1) or {}
        user_id = first(body, 1)
        user = blobs.get(user_id, {}).get('value') if blobs.get(user_id, {}).get('class') == 'user_message' else None
        text, turn = first(user, 1), first(user, 2)
        if not isinstance(text, str) or not isinstance(turn, str) or not turn:
            problem('unknown_turn_shape', record=f"blobs:{blobs[turn_blob]['rowid']}")
            continue
        sequence += 1
        stamps[user_id]['timestamp'] = first(user, 25)
        out['turns'].append({'id': turn, 'role': 'user', 'text': text, 'sequence': sequence, 'timestamp': first(user, 25),
                             'locator': locator(user_id), 'turn_blob_id': turn_blob, 'request_id': first(body, 3)})
        # The JSON messages of this turn: from its prompt message to the next prompt message.
        segment, bound = [], len(prompts) == len(turn_ids) and f'<user_query>\n{text}\n</user_query>' in prompts[number][1]
        if bound:
            end = prompts[number + 1][0] if number + 1 < len(prompts) else len(messages)
            segment = messages[prompts[number][0] + 1:end]
            out['message_bindings'].append({'event_id': f'message:{turn}', 'message_blob_id': messages[prompts[number][0]][0], 'part': 0})
        stated = [(identity, kind, value, part) for identity, message in segment if message.get('role') == 'assistant'
                  for part, (kind, value) in enumerate(message_parts(message))]
        models = {identity: message_model(message) for identity, message in segment if message.get('role') == 'assistant'}
        steps = [identity for identity in body.get(2, []) if blobs.get(identity, {}).get('class') == 'step']
        if len(steps) != len(body.get(2, [])):
            problem('turn_step_missing', record=f"blobs:{blobs[turn_blob]['rowid']}")
        visible = [identity for identity in steps if first(blobs[identity]['value'], 3) is None]
        aligned = bound and len(stated) == len(visible)
        for position, identity in enumerate(steps):
            step = blobs[identity]['value']
            said, tool, thought = first(step, 1), first(step, 2), first(step, 3)
            if sum(item is not None for item in (said, tool, thought)) != 1 or sum(len(step.get(key, [])) for key in (1, 2, 3)) != 1:
                problem('unknown_step_shape', record=f"blobs:{blobs[identity]['rowid']}")
                continue
            sequence += 1
            partner = stated[visible.index(identity)] if aligned and identity in visible else None
            base = {'sequence': sequence, 'turn_id': turn, 'locator': locator(identity)}
            if thought is not None:
                stamps[identity]['timestamp'] = first(thought, 3)
                continue
            if said is not None:
                content = first(said, 1) or ''
                stamps[identity]['timestamp'] = first(said, 2)
                if partner is not None and (partner[1] != 'text' or partner[2] != content):
                    aligned, partner = False, None
                later = [other for other in steps[position + 1:] if first(blobs[other]['value'], 3) is None]
                response = {**base, 'id': identity, 'role': 'assistant', 'text': content, 'status': 'completed',
                            'timestamp': first(said, 2), 'phase': 'commentary' if later else 'final_answer'}
                markers = _CANARY.findall(content)
                if len(markers) == 1 and content.rstrip().endswith(markers[0]):
                    response['canary'] = markers[0]
                if partner is not None:
                    out['message_bindings'].append({'event_id': f'message:{identity}', 'message_blob_id': partner[0], 'part': partner[3]})
                    if models.get(partner[0]):
                        # The actual model of the response. The selected route is not in the store.
                        response.update(model_id=models[partner[0]], configuration=models[partner[0]],
                                        model_locator=locator(partner[0]))
                if content:
                    out['responses'].append(response)
                    if response['phase'] == 'final_answer':
                        out['relations'].append({'kind': 'turn_response', 'from_id': turn, 'to_id': identity, 'locator': base['locator'],
                                                 'native_key': 'step reference of the turn blob'})
                continue
            call, fields = first(tool, 57), _tool_fields(tool)
            if not isinstance(call, str) or not call or fields is None or fields['status'] is None:
                problem('unknown_tool_step_shape', record=f"blobs:{blobs[identity]['rowid']}")
                continue
            stamps[identity]['timestamp'] = first(tool, 59)
            if partner is not None:
                part = partner[2] if partner[1] == 'call' else {}
                if part.get('toolCallId') != call or part.get('toolName') != fields['name'] or part.get('args') != fields['input']:
                    aligned, partner = False, None
                else:
                    out['message_bindings'].append({'event_id': f'call:{call}', 'message_blob_id': partner[0], 'part': partner[3]})
            action = {**base, 'id': call, 'call_id': call, 'name': fields['name'], 'input': fields['input'], 'timestamp': first(tool, 59)}
            result = {**base, 'id': call + ':result', 'call_id': call, 'action_id': call, 'output': fields['output'],
                      'status': fields['status'], 'timestamp': first(tool, 60)}
            if type(fields.get('exit_code')) is int:
                result['exit_code'] = fields['exit_code']
            command = fields['input'].get('command')
            segments = None
            if fields['name'] == 'Shell' and isinstance(command, str):
                try:
                    action.update(command=command, argv=shlex.split(command))
                except ValueError:
                    action['command'] = command
                kind = _helper_kind(command)
                if kind is not None:
                    action['action_kind'] = kind
                segments, _ = scored_segments(command)
            elif isinstance(fields['input'].get('path'), str):
                action['target'] = relative(fields['input']['path'])
                if fields['name'] == 'Write':
                    action['action_kind'] = 'edit'
            if segments is None:
                nonces = _NONCE.findall(fields['output'] or '')
                if len(nonces) == 1:
                    result['helper_nonce'] = nonces[0]
                out['actions'].append(action)
                out['results'].append(result)
                out['relations'].append({'kind': 'action_result', 'from_id': call, 'to_id': result['id'], 'locator': base['locator'],
                                         'native_key': 'call id of the step record'})
            else:
                # Compound shell call rule: the call is the native record of each scored segment.
                found = helper_segment_results(segments, fields['output'] or '', fields['status'] == 'failure')
                for item in segments:
                    part_id = f"{call}:segment-{item['index']}"
                    part = {**base, 'id': part_id, 'call_id': part_id, 'parent_call_id': call, 'segment_index': item['index'],
                            'name': fields['name'], 'input': {'command': item['text']}, 'command': item['text'], 'timestamp': first(tool, 59)}
                    if item['kind'] == 'helper':
                        part.update(argv=shlex.split(item['text']), action_kind='inspect' if item['phase'] == 'inspect' else 'test')
                    else:
                        part.update(action_kind='edit', target=relative(item['path']))
                    out['actions'].append(part)
                    line = found.get(item['index']) if item['kind'] == 'helper' else None
                    if line is None:
                        continue
                    # The call's own exit code counts only for the last segment of the call.
                    code = fields.get('exit_code') if item['follows'] is None else line['exit_code']
                    outcome = {**base, 'id': part_id + ':result', 'call_id': part_id, 'parent_call_id': call, 'action_id': part_id,
                               'output': line['output'], 'timestamp': first(tool, 60)}
                    if type(code) is int:
                        outcome.update(exit_code=code, status='failure' if code else 'success')
                    nonces = _NONCE.findall(line['output'])
                    if len(nonces) == 1:
                        outcome['helper_nonce'] = nonces[0]
                    out['results'].append(outcome)
                    out['relations'].append({'kind': 'action_result', 'from_id': part_id, 'to_id': outcome['id'], 'locator': base['locator'],
                                             'native_key': 'call id of the step record and segment index'})
            if fields['name'] == 'Write' and fields['status'] == 'success' and action.get('target'):
                changes.append({'call': call, 'turn': turn, 'path': fields['input']['path'], 'target': action['target'],
                                'before': fields['before'], 'after': fields['after'], 'written': fields['input'].get('contents'),
                                'timestamp': first(tool, 60), 'locator': base['locator']})
        if not aligned:
            problem('message_list_differs_from_turn', turn=turn)
    _add_file_changes(out, changes, root, blobs, locator)
    if out['diagnostics']:
        out['status'] = 'unsupported'
    return out


def _add_file_changes(out, changes, root, blobs, locator):
    """One changed-file fact per path, from the native edit steps and the content-addressed file blobs.

    A write step holds the whole file before and after the edit. Both hashes
    are computed from these native texts. When the current root holds a file
    state for the path, its two content blobs are explicit hashes (a blob id is
    the SHA-256 of the file bytes): they must equal the computed pair, or no
    fact is made. Several edits of one path must form a chain.
    """
    states = {first(state, 1): (first(state, 2, 1), first(state, 2, 2)) for state in root.get(15, [])}
    for path in dict.fromkeys(item['path'] for item in changes):
        chain = [item for item in changes if item['path'] == path]
        if any(left['after'] != right['before'] for left, right in zip(chain, chain[1:])) or any(
                item['written'] is not None and item['written'] != item['after'] for item in chain):
            continue
        before, after = sha(chain[0]['before'].encode('utf-8')), sha(chain[-1]['after'].encode('utf-8'))
        fact = {'id': chain[-1]['call'] + ':change', 'path': chain[-1]['target'], 'before_sha256': before, 'after_sha256': after,
                'hash_source': 'native_preimage_and_edit', 'turn_id': chain[-1]['turn'], 'action_id': chain[-1]['call'],
                'call_id': chain[-1]['call'], 'timestamp': chain[-1]['timestamp'], 'locator': chain[-1]['locator'],
                'sequence': len(out['file_changes']) + 1}
        if path in states:
            current, original = states[path]
            if (current, original) != (after, before) or any(blobs.get(item, {}).get('class') != 'file_content' for item in (current, original)):
                continue
            fact.update(hash_source='native_content_addressed_file_blobs', file_blob_locators=[locator(original), locator(current)])
        out['file_changes'].append(fact)


def decode_cursor_cli_native(native):
    """Decode the one session of a copied isolated root. Only ``store.db`` and ``meta.json`` are opened."""
    native = Path(native)
    directory, session = find_store(native)
    files = {}
    for name in ('store.db', 'meta.json'):
        path = native / directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError('unsafe Cursor native input')
        files[name] = path.read_bytes()
    decoded = decode_cursor_cli_store(files['store.db'], files['meta.json'], session_id=session, artifact=directory + '/store.db')
    if any((native / directory / name).exists() for name in ('store.db-wal', 'store.db-shm')):
        # A write-ahead log would hold rows that the copied main file does not.
        decoded['diagnostics'].append({'code': 'store_wal_present', 'severity': 'error'})
        decoded['status'] = 'unsupported'
    return decoded


def add_native_final_after_chain(decoded, instance):
    """Add the final-after relation when the R2 turn blob lists edit, final test and final response in order.

    The key is native: the ordered step references of one turn blob. The edit
    and the test must have succeeded and the last step must be the R2 response.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    inside = lambda rows: [row for row in rows if row.get('turn_id') == r2['id']]
    responses = [row for row in inside(decoded['responses']) if row.get('canary') == instance['turns'][1]['response_canary']
                 and row.get('phase') == 'final_answer']
    edits = [row for row in inside(decoded['actions']) if row.get('action_kind') == 'edit' and row.get('target') == TARGET]
    finals = [row for row in inside(decoded['actions']) if row.get('action_kind') == 'test' and 'final' in row.get('argv', [])]
    if len(responses) != 1 or len(edits) != 1 or len(finals) != 1:
        return
    results = {row['action_id']: row for row in decoded['results']}
    edit_result, final_result = results.get(edits[0]['id']), results.get(finals[0]['id'])
    if (not edit_result or not final_result or edit_result.get('status') != 'success' or final_result.get('status') != 'success'
            or final_result.get('exit_code') != 0):
        return
    order = [r2['sequence'], edits[0]['sequence'], finals[0]['sequence'], responses[0]['sequence']]
    if order != sorted(set(order)):
        return
    decoded['relations'].append({'kind': 'final_after', 'from_id': r2['id'], 'to_id': finals[0]['id'], 'locator': finals[0]['locator'],
                                 'native_chain_blob_ids': [r2['turn_blob_id'], edits[0]['locator']['blob_id'],
                                                           finals[0]['locator']['blob_id'], responses[0]['locator']['blob_id']]})


def _stream(raw, label):
    rows = []
    for line in raw.decode('utf-8').splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f'{label} holds a non-object line')
            rows.append(row)
    return rows


def build_cursor_cli_observer(*, workload, stdout_by_turn, helper_document, before_sha256, after_sha256):
    """Build the observer from the stdout stream of each turn, the helper ledger and the file hashes.

    A scored action is a frozen helper invocation or the edit of the workload
    file. A stream shell call with two or more scored segments is observed as
    one action per segment (compound shell call rule): the helper ledger gives
    the exit code and the output line of each helper process, and that line
    must be in the stream output of the call. Every other stream call is an
    unscored action; it keeps its stream call id so that the native call with
    the same id leaves the scored population.
    """
    helpers = _stream(helper_document, 'helper ledger')
    if [row.get('phase') for row in helpers] != ['inspect', 'baseline', 'final']:
        raise ValueError('helper population incomplete')
    ledger = {row['phase']: row for row in helpers}
    events, relations, sessions, emitted, turn_ids = [], [], set(), {}, {}

    def add(identity, kind, fields, metrics, *, source='harness_stdout', role='primary_scored', observed_at=None):
        row = {'id': identity, 'sequence': len(events) + 1, 'kind': kind, 'fields': fields, 'metric_ids': metrics,
               'boundary': 'captured', 'population_role': role, 'source': source, 'session_id': session}
        if observed_at is not None:
            row['observed_at'] = observed_at
        events.append(row)
        return identity

    def link(action, result):
        relations.append({'id': action + ':relation', 'kind': 'action_result', 'from_id': action, 'to_id': result, 'sequence': len(relations) + 1})

    for number in (1, 2):
        rows = _stream(stdout_by_turn[number], f'stdout R{number}')
        finals = [row for row in rows if row.get('type') == 'result']
        if len(finals) != 1 or finals[0].get('subtype') != 'success' or finals[0].get('is_error') is not False:
            raise ValueError('stdout completion missing or duplicate')
        found = {row.get('session_id') for row in rows if row.get('session_id') is not None}
        if len(found) != 1:
            raise ValueError('stdout session mismatch')
        session = found.pop(); sessions.add(session)
        submitted = [row for row in rows if row.get('type') == 'user']
        turn = workload['turns'][number - 1]
        if len(submitted) != 1 or ''.join(part.get('text', '') for part in submitted[0]['message']['content']) != turn['text']:
            raise ValueError('stdout submitted input mismatch')
        turn_id = turn_ids[number] = turn['id']
        add(turn_id, 'user_turn', {'text': turn['text'], 'role': 'user', 'turn_id': turn_id, 'revision': f'r{number}',
                                   'run_canary': workload['run_canary']}, ['work.submitted_turns', f'revision.r{number}', 'revision.r1_r2_order'])
        calls = [row for row in rows if row.get('type') == 'tool_call']
        started = [row for row in calls if row.get('subtype') == 'started']
        completed = {row.get('call_id'): row for row in calls if row.get('subtype') == 'completed'}
        if (len({row.get('call_id') for row in started}) != len(started) or {row.get('call_id') for row in started} != set(completed)
                or len(completed) * 2 != len(calls)):
            raise ValueError('stdout action and completion populations differ')
        for start in started:
            call, done = start['call_id'], completed[start['call_id']]['tool_call']
            kinds = [key for key in done if key.endswith('ToolCall')]
            if len(kinds) != 1:
                raise ValueError('stdout tool call shape')
            name, args, result = _STREAM_TOOLS.get(kinds[0], kinds[0]), done[kinds[0]].get('args', {}), done[kinds[0]].get('result', {})
            ok, body = ('success' in result), result.get('success', result.get('failure', {}))
            observed_at = start.get('timestamp_ms')
            command = args.get('command') if name == 'Shell' else None
            segments, scored = scored_segments(command) if isinstance(command, str) else (None, 0)
            if segments is not None:
                lines = (body.get('stdout') or '').split('\n')
                for item in segments:
                    if item['kind'] != 'helper':
                        raise ValueError('a shell write of the workload file is not an observed population of this row')
                    helper = ledger[item['phase']]
                    if helper['output'] not in lines or item['phase'] in emitted:
                        raise ValueError('helper ledger line is not in the stream output of its call')
                    identity = f"{call}:segment-{item['index']}"
                    emitted[item['phase']] = add(identity, 'action', {
                        'name': 'shell', 'command': item['text'], 'argv': shlex.split(item['text']), 'turn_id': turn_id,
                        'action_kind': 'inspect' if item['phase'] == 'inspect' else 'test', 'stream_call_id': call,
                        'segment_index': item['index']}, ['work.actions', 'causal.action_result'], observed_at=observed_at)
                    add(identity + ':result', 'result', {
                        'action_id': identity, 'turn_id': turn_id, 'helper_nonce': helper['helper_nonce'], 'exit_code': helper['exit_code'],
                        'status': 'failure' if helper['exit_code'] else 'success', 'output': helper['output'], 'stream_call_id': call},
                        ['work.results', 'causal.action_result'], source='helper_ledger')
                    link(identity, identity + ':result')
                continue
            edit = name == 'Write' and relative(args.get('path')) == TARGET
            if scored == 1 or edit:
                if edit:
                    fields = {'name': 'file_edit', 'target': TARGET, 'action_kind': 'edit', 'turn_id': turn_id, 'stream_call_id': call}
                    outcome = {'action_id': call, 'turn_id': turn_id, 'status': 'success' if ok else 'failure',
                               'output': body.get('message') or '', 'stream_call_id': call}
                    phase = 'edit'
                else:
                    output = body.get('stdout') or ''
                    phases = [key for key, helper in ledger.items() if helper['output'] in output.split('\n')]
                    if len(phases) != 1 or phases[0] in emitted or ledger[phases[0]]['exit_code'] != body.get('exitCode'):
                        raise ValueError('stream helper call differs from the helper ledger')
                    phase = phases[0]
                    fields = {'name': 'shell', 'command': command, 'argv': shlex.split(command), 'turn_id': turn_id,
                              'action_kind': 'inspect' if phase == 'inspect' else 'test', 'stream_call_id': call}
                    outcome = {'action_id': call, 'turn_id': turn_id, 'status': 'success' if ok else 'failure', 'output': output,
                               'exit_code': body.get('exitCode'), 'helper_nonce': ledger[phase]['helper_nonce'], 'stream_call_id': call}
                if phase in emitted:
                    raise ValueError('duplicate scored stream action')
                emitted[phase] = add(call, 'action', fields, ['work.actions', 'causal.action_result'], observed_at=observed_at)
                add(call + ':result', 'result', outcome, ['work.results', 'causal.action_result'],
                    observed_at=completed[call].get('timestamp_ms'))
                link(call, call + ':result')
                continue
            fields = {'name': name, 'turn_id': turn_id, 'call_id': call}
            if isinstance(command, str):
                fields['command'] = command
            elif isinstance(args.get('path'), str):
                fields['target'] = relative(args['path'])
            add(call, 'action', fields, [], role='unscored', observed_at=observed_at)
        canary = turn['response_canary']
        texts = [(row, ''.join(part.get('text', '') for part in row['message']['content'])) for row in rows if row.get('type') == 'assistant']
        responses = [(row, text) for row, text in texts if text.rstrip().endswith(canary)]
        if len(responses) != 1:
            raise ValueError('independent final response population ambiguous')
        response_id = f'response-r{number}'
        # The stream names the selected route (``Auto``) once per process. It reports no model for a response.
        add(response_id, 'assistant_response', {'text': responses[0][1], 'canary': canary, 'turn_id': turn_id, 'role': 'assistant',
                                                'status': 'completed'},
            ['work.visible_responses', 'causal.turn_response', 'attribution.model_config', 'attribution.usage', 'attribution.token_semantics'],
            observed_at=responses[0][0].get('timestamp_ms'))
        relations.append({'id': f'relation-turn-{number}', 'kind': 'turn_response', 'from_id': turn_id, 'to_id': response_id, 'sequence': len(relations) + 1})
    if len(sessions) != 1:
        raise ValueError('stdout session mismatch')
    if set(emitted) != {'inspect', 'baseline', 'edit', 'final'}:
        raise ValueError('stdout and helper primary population incomplete')
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
                'method': 'exact submitted, response and tool events of the stdout stream; helper ledger; filesystem hashes; no native input',
                'events': events, 'relations': relations}
    _validate_observer(observer)
    return observer


def scored_pool(decoded, observer):
    """The native facts without the calls that pair with an observed unscored action.

    Rule for unscored native actions: the stream and the store share the call
    id, so the pair is made by id. A native call that the observer did not see
    at all stays in the pool and enlarges the denominator.
    """
    unscored = {event['fields'].get('call_id') for event in observer['events']
                if event['kind'] == 'action' and event['population_role'] == 'unscored'}
    native_call = lambda row: row.get('parent_call_id', row.get('call_id'))
    actions = [row for row in decoded['actions'] if native_call(row) not in unscored]
    results = [row for row in decoded['results'] if native_call(row) not in unscored]
    kept = {row['id'] for row in actions}
    relations = [row for row in decoded['relations'] if row['kind'] != 'action_result' or row['from_id'] in kept]
    return {**decoded, 'actions': actions, 'results': results, 'relations': relations}
