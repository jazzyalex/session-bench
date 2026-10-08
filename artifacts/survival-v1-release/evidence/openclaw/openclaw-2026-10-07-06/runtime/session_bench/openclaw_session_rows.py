"""OpenClaw native facts from the rows of one session in the agent store ``openclaw-agent.sqlite``.

OpenClaw keeps the sessions of an agent in one SQLite store,
``agents/<agent>/agent/openclaw-agent.sqlite``. The capture controller exports
the rows of the test session from a private copy of each shared store
(``openclaw_state_capture.export_test_session_rows``), each row as column name
to value. The decoder reads this row export and the schema export. It never
receives observer bytes.

The read (containers the decoder takes a scored fact from), all in the agent store:

- ``transcript_events``: the transcript. One row per event: a session header,
  a user message, an assistant message (one text or one tool call) or a tool
  result. Order (``seq`` and the ``parentId`` chain), times, model, provider,
  usage.
- ``trajectory_runtime_events``: the runtime trace of each run (one run per
  turn). The decoder takes three facts from it: the exit code of a shell call
  (``tool.result``), the token totals of a run (``model.completed``) and the
  name of the product (``traceSchema``).
- ``session_windows``: the session row (session id, agent harness id, chat type).

Every other container of the family is outside the read and is never opened:
the other tables of the row export (their rows are counted by size), the
Codex rollout file, the shell snapshot and the lock of the Codex thread
(their sizes come from the capture receipt).

Decoder contract ``openclaw-agent-sqlite-session-rows-v1``:

- the agent store has ``PRAGMA user_version`` 24 and each of the three tables
  has exactly the columns of ``COLUMNS``;
- the first transcript row is the session header with ``version`` 4 and the
  session id; every other row is a ``message`` whose ``parentId`` is the id of
  the row before it (one linear chain);
- an event is JSON text in ``event_json``, or Zstandard-compressed JSON in
  ``event_zstd`` (then ``event_json`` is NULL); ``event_utf8_bytes`` is the
  byte length of the JSON text. The BLOB is read as a Zstandard stream: a
  skippable frame after the data frame is skipped (a public packet has one,
  see the sanitizer);
- an assistant message holds exactly one content item: ``text`` or ``toolCall``;
- a trace row has ``traceSchema`` ``openclaw-trajectory`` and ``schemaVersion`` 1.

Any other shape is a contract exception and the decode is ``unsupported``.

What the rows hold:

- A prompt: a ``user`` message. Its ``content`` is the exact prompt.
- A response: an ``assistant`` message with one ``text`` item. The last one of
  a run has ``__openclaw.runTerminal`` true.
- A tool call, in two forms. The *execution form* is the command or patch that
  ran (``bash`` with ``command`` and ``cwd``; ``apply_patch`` with
  ``changes``). The *script form* is the model's own call of the Codex tool
  ``exec`` with a JavaScript source that names the same command or patch.
  The result of an execution-form call has ``__openclaw.toolOutput.source``
  ``execution``; the result of a script-form call has ``provider-response``.
  That native field tells the two forms apart. The decoder takes actions and
  results from the execution form. A script-form call that holds the command
  and directory (or the same changed lines of the same file) of the nearest
  earlier execution-form call of its turn is a second statement of that call,
  not an action. A script-form call that matches no execution-form call is its
  own action.
- A tool result: a ``toolResult`` message, bound to its call by ``toolCallId``.
  It has ``isError`` and the output text. It has no exit code. The exit code
  of a shell call is ``data.result.exitCode`` of the trace row ``tool.result``
  with the same ``toolCallId``.
- Model and provider on every assistant message.
- Usage on every assistant message. Only the last message of a run holds
  counts; the others hold zeros. The counts are the totals of the run (all
  model requests of the turn), not of one request.
- Run totals: ``data.usage`` of the trace row ``model.completed``, joined to
  the transcript by ``runId``. The store writes the total twice (this row and
  the last message of the run) and holds zeros on the other messages. These
  are not per-response usage records, so no reconciliation is stated (below).
- A changed file, from two native values: the file source in the output of the
  frozen inspect helper (an earlier ``bash`` result) is the pre-image; the
  unified ``diff`` in ``changes`` of the ``apply_patch`` call is the edit.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex

from .adapters.claude_code_decoder import (
    classify_shell_segment, compound_shell_segments, helper_segment_results, split_shell_segments,
)
from .hermes_store_rows import apply_unified_diff, row_bytes, row_proof
from .native_density import NativeDensityInventory, _unresolved, forward_occurrences, holds_call_arguments, roles_after_repeats, string_leaves

FORMAT = 'openclaw-agent-sqlite-session-rows-v1'
ROWS_SCHEMA = 'session-bench-openclaw-session-store-rows-v1'
AGENT_STORE = 'agents/main/agent/openclaw-agent.sqlite'
ROWS_FILE, SCHEMA_FILE = 'session-store-rows.json', 'session-store-schema.json'
USER_VERSION, TRANSCRIPT_VERSION, TRACE_SCHEMA, TRACE_VERSION = 24, 4, 'openclaw-trajectory', 1
TARGET = 'fixture_project/checkout.py'
READ_TABLES = ('transcript_events', 'trajectory_runtime_events', 'session_windows')
COLUMNS = {
    'transcript_events': 'session_id seq event_json created_at event_zstd event_utf8_bytes navigation_json'.split(),
    'trajectory_runtime_events': 'session_id seq run_id event_json created_at'.split(),
    'session_windows': ('session_id session_key previous_session_id reason session_scope created_at updated_at transcript_updated_at '
                        'transcript_observed_at session_entry_provenance acp_owned plugin_owner_id hook_external_content_source started_at '
                        'ended_at status chat_type channel account_id primary_conversation_id model_provider model agent_harness_id '
                        'parent_session_key spawned_by display_name').split(),
}
USAGE_KEYS = (('input', 'input_tokens'), ('output', 'output_tokens'), ('cacheRead', 'cache_read_tokens'), ('cacheWrite', 'cache_write_tokens'))
_SHELLS = ('/bin/zsh', '/bin/bash', '/bin/sh', 'zsh', 'bash', 'sh')
_CANARY = re.compile(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+')
_NONCE = re.compile(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)')
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_MAX_BYTES = 64 * 1024 * 1024
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'explanation'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}
# The fixed role of a table outside the read. Its rows are counted by size and never parsed.
_INDEX_TABLES = ('session_transcript_fts', 'session_transcript_fts_content', 'session_transcript_fts_rows', 'session_transcript_fts_data',
                 'session_transcript_fts_idx', 'session_transcript_fts_docsize', 'session_transcript_fts_config',
                 'session_transcript_active_events', 'session_transcript_index_state', 'transcript_event_identities')


class OpenClawRowsError(ValueError):
    """The row export is not a document that this decoder can read."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _strict(data, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise OpenClawRowsError(f'{label}: duplicate JSON key')
            result[key] = value
        return result

    def constant(value):
        raise OpenClawRowsError(f'{label}: non-finite number')

    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except OpenClawRowsError:
        raise
    except (UnicodeDecodeError, ValueError) as error:
        raise OpenClawRowsError(f'{label}: not JSON') from error


def _json_text(value):
    """A JSON value held as text; None when it is not JSON."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _unzstd(raw):
    """Decompress a Zstandard stream with the standard library (Python 3.14 ``compression.zstd``)."""
    try:
        from compression import zstd
    except ImportError as error:
        raise OpenClawRowsError('no Zstandard reader: Python 3.14 or later is required') from error
    try:
        return zstd.decompress(raw)
    except Exception as error:   # the module raises its own error type
        raise OpenClawRowsError('a compressed transcript event is not a Zstandard stream') from error


def read_rows(rows, schema):
    """Parse the two export documents. Returns ``(read tables, other tables, agent store schema, exceptions, document)``.

    ``read tables`` maps each table of the read to its rows in ``seq`` order
    (a row is ``{'rowid', 'values'}``). ``other tables`` is a list of
    ``(store, table, rows)`` for every exported table outside the read; the
    decoder counts their rows and never parses a value.
    """
    document, layout = _strict(rows, 'row export'), _strict(schema, 'schema export')
    stores = document.get('stores') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != ROWS_SCHEMA or not isinstance(document.get('session_id'), str)
            or not isinstance(stores, list) or not stores or not all(isinstance(store, dict) and isinstance(store.get('tables'), list) for store in stores)
            or [store.get('store') for store in stores].count(AGENT_STORE) != 1):
        raise OpenClawRowsError('row export is not one session with the agent store')
    described = layout.get('stores') if isinstance(layout, dict) else None
    if not isinstance(described, list) or [store.get('store') for store in described if isinstance(store, dict)] != [store['store'] for store in stores]:
        raise OpenClawRowsError('schema export does not describe the stores of the row export')
    layout_store = next(store for store in described if store['store'] == AGENT_STORE)
    read, others, exceptions = {}, [], []
    for store in stores:
        seen = set()
        for table in store['tables']:
            name, rows_of, columns = table.get('table'), table.get('rows'), table.get('columns')
            if not isinstance(name, str) or name in seen or not isinstance(rows_of, list) or not isinstance(columns, list):
                raise OpenClawRowsError('row export table is malformed')
            seen.add(name)
            for row in rows_of:
                if not isinstance(row, dict) or not isinstance(row.get('values'), dict) or set(row['values']) != set(columns):
                    raise OpenClawRowsError('row export row is malformed')
            if store['store'] == AGENT_STORE and name in READ_TABLES:
                if columns != COLUMNS[name]:
                    exceptions.append({'code': 'unknown_column_set', 'record': name})
                if name != 'session_windows' and (any(type(row['values'].get('seq')) is not int for row in rows_of)
                                                  or len({row['values']['seq'] for row in rows_of}) != len(rows_of)):
                    raise OpenClawRowsError(f'{name}: rows without a distinct seq')
                read[name] = sorted(rows_of, key=lambda row: row['values'].get('seq') or 0)
            else:
                others.append((store['store'], name, rows_of))
    agent = next(store for store in stores if store['store'] == AGENT_STORE)
    if layout_store.get('user_version') != USER_VERSION or agent.get('user_version') != USER_VERSION:
        exceptions.append({'code': 'unsupported_version', 'record': 'user_version of the agent store'})
    declared = {row.get('name'): row for row in layout_store.get('tables', []) if isinstance(row, dict)}
    for name, columns in COLUMNS.items():
        if declared.get(name, {}).get('columns') != columns:
            exceptions.append({'code': 'unsupported_schema', 'record': f'{name}: the store schema differs from the contract'})
        if name not in read:
            exceptions.append({'code': 'missing_artifact', 'record': f'{name}: no row of the session'})
            read[name] = []
    return read, others, layout_store, exceptions, document


def store_schema_sha256(schema, store):
    """The schema digest of one store as the capture receipt states it (pragmas and ``sqlite_master`` rows)."""
    layout = next(row for row in _strict(schema, 'schema export')['stores'] if row['store'] == store)
    value = {'application_id': layout['application_id'], 'user_version': layout['user_version'], 'objects': layout['objects']}
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def _relative(path):
    if isinstance(path, str) and (path.endswith('/fixture_project') or '/fixture_project/' in path):
        return 'fixture_project' + path.split('/fixture_project', 1)[1]
    return path


def inner_command(command):
    """The command inside ``<shell> -lc '<command>'``; the command itself when there is no such wrapper."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command
    return words[2] if len(words) == 3 and words[0] in _SHELLS and words[1] in ('-lc', '-c') else command


def transcript_event(row):
    """The JSON event of one transcript row, or None for a row outside the contract."""
    values = row['values']
    text, blob = values.get('event_json'), values.get('event_zstd')
    if text is None and isinstance(blob, dict) and isinstance(blob.get('blob_hex'), str):
        try:
            text = _unzstd(bytes.fromhex(blob['blob_hex'])).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
    elif blob is not None or not isinstance(text, str):
        return None
    event = _json_text(text)
    if not isinstance(event, dict) or values.get('event_utf8_bytes') != len(text.encode('utf-8')):
        return None
    return event


def transcript_items(rows):
    """One parsed item per transcript row, in ``seq`` order: ``{'seq', 'kind', ...}``. ``kind`` None marks a malformed row.

    Kinds: ``session``, ``user``, ``text`` (assistant text), ``call`` (assistant
    tool call) and ``result`` (tool result).
    """
    items = []
    for row in rows:
        seq, event = row['values']['seq'], transcript_event(row)
        item = {'seq': seq, 'kind': None, 'row': row}
        message = event.get('message') if isinstance(event, dict) else None
        if isinstance(event, dict) and event.get('type') == 'session':
            item.update(kind='session', id=event.get('id'), version=event.get('version'), timestamp=event.get('timestamp'))
        elif (isinstance(event, dict) and event.get('type') == 'message' and isinstance(event.get('id'), str) and isinstance(message, dict)
              and isinstance(event.get('timestamp'), str)):
            meta = message.get('__openclaw') if isinstance(message.get('__openclaw'), dict) else {}
            item.update(id=event['id'], parent=event.get('parentId'), timestamp=event['timestamp'], run=meta.get('runId'), meta=meta, message=message)
            content, role = message.get('content'), message.get('role')
            if role == 'user' and isinstance(content, str):
                item.update(kind='user', text=content)
            elif role == 'assistant' and isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict):
                part = content[0]
                if part.get('type') == 'text' and isinstance(part.get('text'), str):
                    item.update(kind='text', text=part['text'])
                elif (part.get('type') == 'toolCall' and isinstance(part.get('id'), str) and part['id'] and isinstance(part.get('name'), str)
                      and isinstance(part.get('arguments'), dict)):
                    item.update(kind='call', call=part['id'], name=part['name'], arguments=part['arguments'])
            elif (role == 'toolResult' and isinstance(message.get('toolCallId'), str) and isinstance(content, list)
                  and all(isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str) for part in content)
                  and type(message.get('isError')) is bool):
                source = meta.get('toolOutput', {}).get('source') if isinstance(meta.get('toolOutput'), dict) else None
                item.update(kind='result', call=message['toolCallId'], name=message.get('toolName'), output=''.join(part['text'] for part in content),
                            is_error=message['isError'], source=source)
        items.append(item)
    return items


def trace_events(rows):
    """One parsed event per trace row, in ``seq`` order; None for a row outside the contract."""
    events = []
    for row in rows:
        event = _json_text(row['values'].get('event_json'))
        if (not isinstance(event, dict) or event.get('traceSchema') != TRACE_SCHEMA or event.get('schemaVersion') != TRACE_VERSION
                or not isinstance(event.get('type'), str) or not isinstance(event.get('data'), dict) or event.get('runId') != row['values'].get('run_id')):
            events.append(None)
        else:
            events.append(event)
    return events


def _script_strings(source):
    """Every string literal of a script source, decoded."""
    found = []
    for literal in _STRING.findall(source if isinstance(source, str) else ''):
        try:
            found.append(json.loads(literal))
        except ValueError:
            pass
    return found


def _net_lines(text):
    """Removed and added lines of a patch or diff, without a line that is removed and added again unchanged."""
    removed = [line[1:] for line in text.splitlines() if line.startswith('-') and not line.startswith('---')]
    added = [line[1:] for line in text.splitlines() if line.startswith('+') and not line.startswith('+++')]
    for line in list(removed):
        if line in added:
            removed.remove(line)
            added.remove(line)
    return sorted(removed), sorted(added)


def script_restates(script, execution):
    """True when a script-form call holds the command and directory, or the changed lines and file, of an execution-form call."""
    source = script['arguments'].get('input')
    strings = _script_strings(source)
    arguments = execution['arguments']
    if isinstance(arguments.get('command'), str):
        cwd = arguments.get('cwd')
        return inner_command(arguments['command']) in strings and (not isinstance(cwd, str) or cwd in strings)
    changes = arguments.get('changes')
    if isinstance(changes, list) and changes and all(isinstance(change, dict) and isinstance(change.get('path'), str)
                                                    and isinstance(change.get('diff'), str) for change in changes):
        patches = [text for text in strings if '*** Begin Patch' in text]
        diff = '\n'.join(change['diff'] for change in changes)
        return (len(patches) == 1 and all(change['path'] in patches[0] for change in changes)
                and _net_lines(patches[0]) == _net_lines(diff) and _net_lines(diff) != ([], []))
    return False


def result_restates(script_output, execution_output):
    """True when the result of a script-form call holds the full output of an execution-form result."""
    if not execution_output:
        return False
    parts = _json_text(script_output)
    texts = [part.get('text') for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []
    return any(isinstance(text, str) and execution_output in text for text in texts)


def pair_script_calls(items):
    """Map the call id of each script-form call to the execution-form call that it restates, or to None.

    A call is in the script form when its result has ``toolOutput.source``
    ``provider-response``. It is joined to the nearest earlier execution-form
    call since the last user message that no script-form call has taken, and
    then checked by content (``script_restates``).
    """
    sources = {item['call']: item.get('source') for item in items if item['kind'] == 'result'}
    pairs, open_calls = {}, []
    for item in items:
        if item['kind'] == 'user':
            open_calls = []
        elif item['kind'] == 'call' and sources.get(item['call']) == 'provider-response':
            match = next((call for call in reversed(open_calls) if script_restates(item, call)), None)
            pairs[item['call']] = match['call'] if match else None
            if match:
                open_calls.remove(match)
        elif item['kind'] == 'call':
            open_calls.append(item)
    return pairs


def decode_openclaw_session_rows(rows, schema, *, run_canary=None, artifact=ROWS_FILE):
    """Decode the row export and the schema export of one session into comparator facts."""
    out = {key: [] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'records', 'reconciliation')}
    read, _others, layout, exceptions, document = read_rows(rows, schema)
    physical = sha(rows)
    out.update(format=FORMAT, status='ok', session_id=None, physical_sha256=physical, user_version=layout.get('user_version'),
               transcript_version=None, trace_schema=None, trace_version=None, harness=None, surface=None, model=None, thread=[],
               run_totals=[], diagnostics=[{**item, 'severity': 'error'} for item in exceptions])

    def problem(code, **detail):
        out['diagnostics'].append({'code': code, 'severity': 'error', **detail})

    def locator(table, seq):
        return {'artifact': artifact, 'table': table, 'line': seq, 'physical_sha256': physical}

    windows = read['session_windows']
    if len(windows) != 1 or windows[0]['values'].get('session_id') != document['session_id']:
        problem('missing_artifact', record='session_windows row')
        out['status'] = 'unsupported'
        return out
    window = windows[0]['values']
    session = out['session_id'] = window['session_id']
    if isinstance(window.get('agent_harness_id'), str) and isinstance(window.get('chat_type'), str):
        out['surface'] = f"agent harness {window['agent_harness_id']}; chat type {window['chat_type']}"
    out['records'].append({'raw': {'table': 'session_windows', 'role': None}, 'locator': locator('session_windows', 0), 'timestamp': None})

    # The runtime trace: exit codes of shell calls, run totals, the product name.
    traces = trace_events(read['trajectory_runtime_events'])
    exits, totals, schemas = {}, {}, set()
    for row, event in zip(read['trajectory_runtime_events'], traces):
        seq = row['values']['seq']
        out['records'].append({'raw': {'table': 'trajectory_runtime_events', 'role': None}, 'locator': locator('trajectory_runtime_events', seq),
                               'timestamp': event.get('ts') if event else None})
        if event is None or row['values'].get('session_id') != session or event.get('sessionId') != session:
            problem('malformed_record', record=f'trajectory_runtime_events:{seq}')
            continue
        schemas.add((event['traceSchema'], event['schemaVersion']))
        data = event['data']
        if event['type'] == 'tool.result' and isinstance(data.get('toolCallId'), str):
            result = data.get('result') if isinstance(data.get('result'), dict) else {}
            if data['toolCallId'] in exits:
                problem('malformed_record', record=f'trajectory_runtime_events:{seq}')
            exits[data['toolCallId']] = {'exit_code': result.get('exitCode') if type(result.get('exitCode')) is int else None,
                                         'is_error': data.get('isError'), 'locator': locator('trajectory_runtime_events', seq)}
        elif event['type'] == 'model.completed' and isinstance(data.get('usage'), dict):
            if event['runId'] in totals:
                problem('malformed_record', record=f'trajectory_runtime_events:{seq}')
            totals[event['runId']] = {'usage': data['usage'], 'locator': locator('trajectory_runtime_events', seq)}
    if len(schemas) == 1:
        out['trace_schema'], out['trace_version'] = next(iter(schemas))
        # The schema name of the trace names the product: ``openclaw-trajectory``.
        out['harness'] = out['trace_schema'].split('-', 1)[0]

    items = transcript_items(read['transcript_events'])
    pairs = pair_script_calls(items)
    restated = {call for call, target in pairs.items() if target is not None}
    turn, previous, pending, calls, run_usage, run_final = None, None, {}, {}, {}, {}
    for position, item in enumerate(items):
        seq, identity = item['seq'], f"transcript_events:{item['seq']}"
        out['records'].append({'raw': {'table': 'transcript_events', 'role': item['kind']}, 'locator': locator('transcript_events', seq),
                               'timestamp': item.get('timestamp')})
        if item['row']['values'].get('session_id') != session or item['kind'] is None:
            problem('malformed_record', record=identity)
            continue
        if item['kind'] == 'session':
            if position != 0 or item['id'] != session or item['version'] != TRANSCRIPT_VERSION:
                problem('unsupported_version' if position == 0 and item['id'] == session else 'malformed_record', record=identity)
            out['transcript_version'] = item['version'] if position == 0 else out['transcript_version']
            continue
        if position == 0 or item['parent'] != previous:
            problem('invalid_boundary', record=identity)   # the transcript is one linear parent chain after the header
        previous = item['id']
        base = {'sequence': seq, 'locator': locator('transcript_events', seq), 'timestamp': item['timestamp']}
        role = 'user' if item['kind'] == 'user' else 'tool' if item['kind'] == 'result' else 'assistant'
        out['thread'].append({'id': item['id'], 'role': role, 'ordinal': seq, 'parent_id': item['parent']})
        if item['kind'] == 'user':
            turn = item['id']
            out['turns'].append({**base, 'id': item['id'], 'role': 'user', 'text': item['text'], 'run_id': item['run']})
            continue
        if turn is None:
            problem('invalid_boundary', record=identity)
            continue
        base['turn_id'] = turn
        message = item['message']
        if item['kind'] in ('text', 'call'):
            usage = message.get('usage')
            if isinstance(usage, dict) and isinstance(item['run'], str):
                run_usage.setdefault(item['run'], []).append(usage)
        if item['kind'] == 'text':
            final = item['meta'].get('runTerminal') is True
            response = {**base, 'id': item['id'], 'role': 'assistant', 'text': item['text'], 'phase': 'final_answer' if final else 'commentary',
                        'run_id': item['run']}
            if final and message.get('stopReason') == 'stop':
                response['status'] = 'completed'
            markers = _CANARY.findall(item['text'])
            if len(markers) == 1 and item['text'].rstrip().endswith(markers[0]):
                response['canary'] = markers[0]
            if isinstance(message.get('model'), str) and message['model'] and isinstance(message.get('provider'), str) and message['provider']:
                response.update(model_id=message['model'], configuration={'provider': message['provider']}, model_locator=base['locator'])
            out['responses'].append(response)
            if final:
                out['relations'].append({'kind': 'turn_response', 'from_id': turn, 'to_id': item['id'], 'locator': base['locator'],
                                         'method': 'native parentId chain of the transcript from the response back to its user message'})
                usage = message.get('usage')
                counts = {new: usage.get(old) for old, new in USAGE_KEYS} if isinstance(usage, dict) else {}
                if counts and all(type(value) is int and value >= 0 for value in counts.values()):
                    # The usage record sits on the response record. Its counts are the totals of the run.
                    out['usage'].append({'id': item['id'] + ':usage', 'response_id': item['id'], 'turn_id': turn, 'usage': counts,
                                         'locator': base['locator'], 'timestamp': item['timestamp'],
                                         'native_key': 'message.usage of the response record; scope: all model requests of the run'})
                    run_final[item['run']] = item['id']
            continue
        if item['kind'] == 'call':
            if item['call'] in calls:
                problem('malformed_record', record=identity)
                continue
            calls[item['call']] = item
            if item['call'] in restated:
                pending[item['call']] = {'kind': 'restated'}
            else:
                pending[item['call']] = _add_call(out, base, item, run_canary)
            continue
        waiting = pending.get(item['call'])
        if waiting is None:
            problem('unjoined_execution', record=identity)
            continue
        pending[item['call']] = None
        if waiting['kind'] == 'restated':
            continue
        native = {'output': item['output'], 'status': 'failure' if item['is_error'] else 'success'}
        trace = exits.get(item['call'])
        if trace is not None:
            if trace['is_error'] is not item['is_error']:
                problem('invalid_boundary', record=identity)   # the transcript and the trace disagree about the outcome
            if trace['exit_code'] is not None:
                native.update(exit_code=trace['exit_code'], exit_locator=trace['locator'])
        _add_result(out, base, item['call'], waiting, native)
    if any(item is not None for item in pending.values()):
        problem('unjoined_execution', record='a tool call has no result row')
    models = {(row.get('model_id'), (row.get('configuration') or {}).get('provider')) for row in out['responses']}
    if len(models) == 1 and None not in next(iter(models)):
        out['model'] = next(iter(models))[0]
    # Run totals: the trace row ``model.completed`` of a run declares them. The rubric asks that the per-response
    # usage records of the run sum to the declared total. Here one assistant message holds the whole total (the copy
    # of the trace row) and the others hold zeros. That sum is the total added to zeros, not a check. A reconciliation
    # is stated only when the total is split over more than one record with counts; else the property is absent.
    # One verdict per session; none when a run has no total.
    runs = [row['run_id'] for row in out['turns']]
    checked = []
    for run in runs:
        declared = totals.get(run)
        records = run_usage.get(run, [])
        if declared is None or not records or not isinstance(run, str):
            checked = []
            break
        sums = {old: sum(record.get(old, 0) for record in records if type(record.get(old, 0)) is int) for old, _ in USAGE_KEYS}
        equal = all(type(declared['usage'].get(old)) is int and declared['usage'][old] == sums[old] for old, _ in USAGE_KEYS)
        checked.append({'run_id': run, 'declared': {old: declared['usage'].get(old) for old, _ in USAGE_KEYS}, 'sum_of_message_usage': sums,
                        'messages': len(records), 'equal': equal, 'locator': declared['locator'], 'response_id': run_final.get(run),
                        'records_with_counts': sum(any(type(record.get(old)) is int and record[old] > 0 for old, _ in USAGE_KEYS) for record in records)})
    out['run_totals'] = checked
    if checked and all(row['records_with_counts'] > 1 for row in checked):
        out['reconciliation'].append({'matches_session_totals': all(row['equal'] for row in checked), 'scope': 'run totals (one run per turn)',
                                      'method': 'usage of the assistant messages of a run, summed, against data.usage of the trace row '
                                                'model.completed of the same runId'})
    _add_file_changes(out)
    if out['diagnostics']:
        out['status'] = 'unsupported'
    return out


def _targets_workload_file(path):
    if not isinstance(path, str):
        return False
    path = path.replace('\\', '/')
    return (path == 'checkout.py' or path.endswith('/checkout.py')) and '/snapshots/' not in '/' + path


def _add_call(out, base, item, run_canary):
    """Add the action or the segment actions of one execution-form call. Returns what its result row needs."""
    call, name, arguments = item['call'], item['name'], item['arguments']
    action = {**base, 'id': call, 'call_id': call, 'name': name, 'input': arguments}
    cwd = _relative(arguments.get('cwd'))
    if isinstance(cwd, str):
        action['cwd'] = cwd
    raw = arguments.get('command')
    if name != 'bash' or not isinstance(raw, str):
        changes = arguments.get('changes')
        paths = [change.get('path') for change in changes if isinstance(change, dict)] if isinstance(changes, list) else []
        if name == 'apply_patch' and len(paths) == 1 and _targets_workload_file(paths[0]):
            action.update(target=TARGET, action_kind='edit')
        elif paths and isinstance(paths[0], str):
            action['target'] = _relative(paths[0])
        out['actions'].append(action)
        return {'kind': 'plain', 'action': call, 'tool': name}
    command = inner_command(raw)
    action['command'] = command
    compound = compound_shell_segments('bash', {'command': command})
    if compound is not None:
        # Compound shell call rule: the call is the native record of each scored segment. Each segment keeps the call id.
        parts = []
        for segment in compound:
            part_id = f"{call}:segment-{segment['index']}"
            part = {**base, 'id': part_id, 'call_id': call, 'parent_call_id': call, 'segment_index': segment['index'], 'name': name,
                    'input': {'command': segment['text']}, 'command': segment['text']}
            if isinstance(cwd, str):
                part['cwd'] = cwd
            if segment['kind'] == 'helper':
                part.update(argv=shlex.split(segment['text']), action_kind='inspect' if segment['phase'] == 'inspect' else 'test')
            else:
                part.update(action_kind='edit', target=_relative(segment['path']) if '/' in segment['path'] else 'fixture_project/' + segment['path'])
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
    return {'kind': 'plain', 'action': call, 'helper_is_last': last}


def result_object_text(value):
    """The declared text of a result object: key-sorted JSON with the default separators. Text that is no JSON object stays."""
    parsed = _json_text(value) if isinstance(value, str) else value
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True) if isinstance(parsed, dict) else value


def _add_result(out, base, call, pending, native):
    output = native['output']
    if pending['kind'] == 'compound':
        found = helper_segment_results(pending['segments'], output, native.get('exit_code', 0) != 0)
        for segment, part_id in zip(pending['segments'], pending['parts']):
            line = found.get(segment['index']) if segment['kind'] == 'helper' else None
            if line is None:
                continue
            # The call's own exit code counts only for the last segment of the call.
            code = native.get('exit_code') if segment['follows'] is None else line['exit_code']
            outcome = {**base, 'id': part_id + ':result', 'call_id': call, 'parent_call_id': call, 'action_id': part_id, 'output': line['output']}
            if type(code) is int:
                outcome.update(exit_code=code, status='failure' if code else 'success')
            nonces = _NONCE.findall(line['output'])
            if len(nonces) == 1:
                outcome['helper_nonce'] = nonces[0]
            out['results'].append(outcome)
            out['relations'].append({'kind': 'action_result', 'from_id': part_id, 'to_id': outcome['id'], 'locator': base['locator'],
                                     'native_key': 'toolCallId of the tool result row and segment index'})
        return
    result = {**base, 'id': call + ':result', 'call_id': call, 'action_id': call, 'output': output}
    if pending.get('tool') is not None:
        # A tool other than the shell: the status is ``isError`` of the result row. The row holds the result object as
        # indented JSON text; the declared text transform is its key-sorted compact JSON (``result_object_text``).
        result['status'] = native['status']
        result['output'] = result_object_text(output)
    elif pending.get('helper_is_last') is not False:
        # A call with one helper segment: the call's status is the helper's only when the helper is the last segment.
        result['status'] = native['status']
        if type(native.get('exit_code')) is int:
            result['exit_code'] = native['exit_code']
        # The helper output line without the line end of the shell output, as the helper ledger states it.
        lines = [line for line in output.split('\n') if _NONCE.match(line)]
        if len(lines) == 1:
            result['output'] = lines[0]
    nonces = _NONCE.findall(output or '')
    if len(nonces) == 1:
        result['helper_nonce'] = nonces[0]
    out['results'].append(result)
    out['relations'].append({'kind': 'action_result', 'from_id': call, 'to_id': result['id'], 'locator': base['locator'],
                             'native_key': 'toolCallId of the tool result row'})


def _add_file_changes(out):
    """One changed-file fact per successful native ``apply_patch`` of the workload file.

    Pre-image: the file source in the output of the inspect helper, from the
    latest earlier result. Edit: the unified ``diff`` of the one change of
    the call. The diff must apply to the pre-image and the result row must
    say success. Both hashes are computed from these native texts.
    """
    results = {row['action_id']: row for row in out['results']}
    source = None
    for action in sorted(out['actions'], key=lambda row: (row['sequence'], row.get('segment_index', 0))):
        result = results.get(action['id'])
        if result is None:
            continue
        if action.get('action_kind') == 'inspect' and isinstance(result.get('output'), str):
            body = _json_text(result['output'].split(' ', 1)[1]) if ' ' in result['output'] else None
            if isinstance(body, dict) and isinstance(body.get('checkout_source'), str):
                source = (body['checkout_source'], result['locator'])
            continue
        if action.get('action_kind') != 'edit' or action.get('name') != 'apply_patch' or source is None:
            continue
        change = action['input']['changes'][0]
        kind = change.get('kind') if isinstance(change.get('kind'), dict) else {}
        after = (apply_unified_diff(source[0], change['diff'])
                 if isinstance(change.get('diff'), str) and kind.get('type') == 'update' and result.get('status') == 'success' else None)
        if after is None or after == source[0]:
            source = None
            continue
        out['file_changes'].append({'id': action['id'] + ':change', 'path': TARGET, 'before_sha256': sha(source[0].encode('utf-8')),
                                    'after_sha256': sha(after.encode('utf-8')), 'hash_source': 'native_preimage_and_edit',
                                    'turn_id': action['turn_id'], 'action_id': action['id'], 'call_id': action['call_id'],
                                    'timestamp': result['timestamp'], 'locator': result['locator'], 'preimage_locator': source[1],
                                    'sequence': len(out['file_changes']) + 1})
        source = (after, result['locator'])


def decode_openclaw_native(native, *, run_canary=None):
    """Decode the one session of a packet's native directory. Opened: the row export and the schema export."""
    native = Path(native)
    files = {}
    for name in (ROWS_FILE, SCHEMA_FILE):
        path = native / 'capture' / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError('unsafe OpenClaw native input')
        files[name] = path.read_bytes()
    return decode_openclaw_session_rows(files[ROWS_FILE], files[SCHEMA_FILE], run_canary=run_canary, artifact='capture/' + ROWS_FILE)


def add_native_final_after_chain(decoded, instance):
    """Add the final-after relation when R2, edit, edit result, final test, its result and the R2 response rise in ``seq`` order.

    The keys are native: ``seq`` and the ``parentId`` chain inside the R2 turn,
    and ``toolCallId``, which binds each result row to its call. No time is
    used. The edit must be one ``apply_patch`` of the workload file with a
    successful result, and the final test must have exit code 0.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    inside = lambda rows: [row for row in rows if row.get('turn_id') == r2['id']]  # noqa: E731
    responses = [row for row in inside(decoded['responses']) if row.get('canary') == instance['turns'][1]['response_canary']
                 and row.get('phase') == 'final_answer']
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
                                 'native_order_rows': order, 'native_call_ids': [edit['call_id'], finals[0]['call_id']],
                                 'method': 'rising seq on the parentId chain inside the R2 turn; each result row bound to its call by toolCallId'})


# --- Statements and logical records (duplicate safety and density) ---

def read_statements(read):
    """(record ids, base roles, statements) for the rows of the read, in the forward read order.

    The forward read: ``transcript_events`` by ``seq``, then
    ``trajectory_runtime_events`` by ``seq``, then the ``session_windows`` row.
    The count is made on the raw rows.

    Transcript rows:

    - a user message states its prompt; an assistant text states that text;
    - an execution-form call states the call; its result states the result;
    - a script-form call that holds the command and directory (or the changed
      lines and file) of an execution-form call states that call again; else
      it states its own call. Its result states the execution result again
      when it holds that full output; else its own result.

    Trace rows state an event of the same run (``runId``) when they hold it:
    the exact prompt, the exact text of an assistant message, all arguments
    and the name of a call, or the full output of a result (a trace row keeps
    the output without its final line end; the parsed result object of
    ``apply_patch`` counts as its JSON text). A trace row that names a
    ``toolCallId`` states only that call or result. The row ``model.completed``
    holds a copy of every message of its run.

    No field marks a row as superseded, so every statement is active.
    """
    items = transcript_items(read['transcript_events'])
    pairs = pair_script_calls(items)
    calls = {item['call']: item for item in items if item['kind'] == 'call'}
    outputs = {item['call']: item['output'] for item in items if item['kind'] == 'result'}
    record_ids, roles, statements, by_run, turn_run = [], [], [], {}, None
    for item in items:
        events, role = [], 'unknown'
        if item['kind'] == 'session':
            role = 'session'
        elif item['kind'] == 'user':
            role, events, turn_run = 'user_message', [f"message:{item['id']}"], item['run']
            by_run.setdefault(turn_run, []).append(('message', item['id'], item['text'], None))
        elif item['kind'] == 'text':
            role, events = 'assistant_message', [f"message:{item['id']}"]
            by_run.setdefault(item['run'] or turn_run, []).append(('message', item['id'], item['text'], None))
        elif item['kind'] == 'call':
            role, target = 'tool_call', pairs.get(item['call'])
            events = [f"call:{target or item['call']}"]
            if target is None:
                by_run.setdefault(item['run'] or turn_run, []).append(('call', item['call'], item['arguments'], item['name']))
        elif item['kind'] == 'result':
            role, target = 'tool_result', pairs.get(item['call'])
            if target is not None and result_restates(item['output'], outputs.get(target)):
                events = [f'result:{target}']
            else:
                events = [f"result:{item['call']}"]
                by_run.setdefault(item['run'] or turn_run, []).append(('result', item['call'], item['output'], None))
        record_ids.append(f"transcript_events:{item['seq']}")
        roles.append(role)
        statements.append(tuple(events))
    base = {'context.compiled': 'user_message', 'prompt.submitted': 'user_message', 'tool.call': 'tool_call', 'tool.result': 'tool_result',
            'model.completed': 'assistant_message'}
    for row, event in zip(read['trajectory_runtime_events'], trace_events(read['trajectory_runtime_events'])):
        events = []
        if event is not None:
            data = event['data']
            leaves = string_leaves(data)
            named = data.get('toolCallId') if isinstance(data.get('toolCallId'), str) else None
            for kind, identity, value, name in by_run.get(event.get('runId'), []):
                if named is not None and (kind == 'message' or identity != named):
                    continue
                if kind == 'message':
                    held = value in leaves
                elif kind == 'call':
                    held = (event['type'] == 'tool.call' and named == identity and data.get('arguments') == value) or (
                        name in leaves and holds_call_arguments(value, leaves, names_tool=True))
                else:
                    held = bool(value) and (value in leaves or value.rstrip('\n') in leaves or (
                        named == identity and isinstance(data.get('result'), dict) and _json_text(value) == data['result']))
                if held:
                    events.append(f'{kind}:{identity}')
        record_ids.append(f"trajectory_runtime_events:{row['values']['seq']}")
        roles.append(base.get(event['type'], 'metadata') if event is not None and events else 'metadata' if event is not None else 'unknown')
        statements.append(tuple(dict.fromkeys(events)))
    for row in read['session_windows']:
        record_ids.append('session_windows:0')
        roles.append('session')
        statements.append(())
    return record_ids, roles, statements


def read_openclaw_family(native, *, artifacts, outside_files=()):
    """Return (records, locators, exceptions, occurrences) for the family of the session.

    ``artifacts`` is the packet inventory (path, sha256). ``outside_files`` is
    a list of ``{'relative_path', 'size_bytes'}`` from the capture receipt:
    the files of the family that are outside the read and not in the packet.
    Density counts every row of the row export (a row of a table outside the
    read by its size, with a fixed unclassified role) and each outside file
    as one record of its size. The schema export describes the whole shared
    stores; it is no record of the session and is not counted.
    """
    native = Path(native)
    expected = {row['path']: row['sha256'] for row in artifacts}
    names = {'capture/' + ROWS_FILE, 'capture/' + SCHEMA_FILE}
    if len(expected) != len(artifacts) or set(expected) != names:
        raise ValueError('OpenClaw family inventory is not the row export and the schema export')
    data = {}
    for name in sorted(expected):
        path = native / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('OpenClaw family member is missing')
        data[name] = path.read_bytes()
        if len(data[name]) > _MAX_BYTES or sha(data[name]) != expected[name]:
            raise ValueError('OpenClaw family member differs from its inventory')
    read, others, _, exceptions, _ = read_rows(data['capture/' + ROWS_FILE], data['capture/' + SCHEMA_FILE])
    records, locators = [], []
    prefix = 'capture/' + ROWS_FILE

    def add(record_id, kind, logical_bytes, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': logical_bytes,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': proof})

    record_ids, roles, statements = read_statements(read)
    rows = [*read['transcript_events'], *read['trajectory_runtime_events'], *read['session_windows']]
    for record, role, row in zip(record_ids, roles_after_repeats(roles, statements), rows, strict=True):
        if role == 'unknown':
            exceptions.append({'code': 'unknown_record', 'record': f'{prefix}:{record}'})
        table = record.split(':', 1)[0]
        add(f'{prefix}:{record}', role, row_bytes(row['values']), sha(row_proof(table, {'rowid': row.get('rowid') or 0, 'values': row['values']})))
    occurrences = forward_occurrences([f'{prefix}:{record}' for record in record_ids], statements)
    for store, table, table_rows in others:
        role = 'index' if table in _INDEX_TABLES else 'metadata'
        for number, row in enumerate(table_rows):
            add(f"{prefix}:{store}:{table}:{number}", role, row_bytes(row['values']),
                sha(row_proof(table, {'rowid': row.get('rowid') or 0, 'values': row['values']})))
    seen = set()
    for item in outside_files:
        path, size = item.get('relative_path'), item.get('size_bytes')
        if not isinstance(path, str) or path in seen or type(size) is not int or size < 0:
            raise ValueError('OpenClaw outside file entry is malformed')
        seen.add(path)
        add('outside-the-read:' + path, 'metadata', size, sha(json.dumps([path, size], separators=(',', ':')).encode('utf-8')))
    return records, locators, exceptions, occurrences


def build_openclaw_density(native, *, artifacts, outside_files=(), complete_record_family=False):
    """Density evidence for the family of the session; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _, _ = read_openclaw_family(native, artifacts=artifacts, outside_files=outside_files)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'OpenClaw density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('OpenClaw density inventory is empty')
    locators.append({'id': 'rule:canonical-native-json-record-utf8-v1+database-rows:session_bench/openclaw_session_rows.py',
                     'sha256': sha(Path(__file__).read_bytes())})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())


def remove_final_response(rows, canary):
    """Selected-loss control: the row export without the one transcript row whose assistant text ends with ``canary``.

    Returns ``(new bytes, removed records)``.
    """
    document = json.loads(rows)
    removed = []
    for store in document['stores']:
        for table in store['tables']:
            if store['store'] != AGENT_STORE or table['table'] != 'transcript_events':
                continue
            kept = []
            for item in transcript_items(table['rows']):
                row = item['row']
                if item['kind'] == 'text' and item['text'].rstrip().endswith(canary):
                    removed.append({'path': 'capture/' + ROWS_FILE, 'table': 'transcript_events', 'seq': item['seq'],
                                    'sha256': sha(row_proof('transcript_events', {'rowid': row.get('rowid') or 0, 'values': row['values']})),
                                    'control_transformation': 'delete the selected assistant message row only'})
                else:
                    kept.append(row)
            table['rows'] = kept
    if len(removed) != 1:
        raise ValueError('OpenClaw loss control requires one final response row')
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode('utf-8') + b'\n', removed
