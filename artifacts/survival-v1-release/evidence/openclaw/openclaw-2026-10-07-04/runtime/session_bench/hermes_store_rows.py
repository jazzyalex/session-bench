"""Hermes native facts from the rows of one session in the shared store ``state.db``.

Hermes keeps every session in one SQLite store, ``~/.hermes/state.db``. The
native record of a session is the set of its rows there. The capture
controller exports them from a private copy of the store
(``hermes_state_evidence.export_session_rows``): the one ``sessions`` row, the
``messages`` rows and the ``session_model_usage`` rows, each as column name to
value. The decoder reads this row export and the schema export of the same
store. It never receives observer bytes.

The read (containers the decoder takes a fact from): the tables ``sessions``
(identity, model, surface), ``messages`` (prompts, responses, tool calls,
tool results, order, times), ``session_model_usage`` (the models of the
session) and ``system_prompts`` (the name of the harness). The exporter file
of ``hermes sessions export`` is a derived projection and is outside the read.

The system prompt of a session is one row of the table ``system_prompts``.
The session row names it by ``system_prompt_hash``, which is the SHA-256 of
the prompt text. That row does not hold the session id, so it is bound as its
own document (``system-prompt-row.json``). Its first sentence names the
harness (``You are Hermes Agent, built by ...``). The decoder reads the name
from there and nothing else from the prompt.

Decoder contract ``hermes-state-db-session-rows-v1``:

- the schema export names ``PRAGMA application_id`` 1148637816 and
  ``PRAGMA user_version`` 0;
- each of the three tables has exactly the columns of ``COLUMNS``;
- a message row is active (``active`` 1, ``compacted`` 0,
  ``_compressed_summary`` 0, ``observed`` 0) and its role is ``user``,
  ``assistant`` or ``tool``;
- the store declares its schema version in the table ``schema_version``. That
  row holds no session id, so the row export does not hold it. When the bound
  version extract is present, its value must be in
  ``SUPPORTED_SCHEMA_VERSIONS`` (31). Any other value makes the decode
  unsupported;
- when the system prompt row is present, it is the one row of
  ``system_prompts`` whose ``hash`` equals ``sessions.system_prompt_hash`` and
  the SHA-256 of its ``prompt``.

Any other shape is a contract exception and the decode is ``unsupported``.

What the rows hold and do not hold:

- A prompt, a response, a tool call (``tool_calls`` JSON on an assistant row)
  and a tool result (a ``tool`` row, bound to its call by ``tool_call_id``).
- No key links an assistant row to its user row. The turn of a row is the
  latest user row before it in ``messages.id`` order (the order of writing).
- No model name on a message row. The model of a response is the model of its
  session row (``messages.session_id`` references ``sessions.id``). It is taken
  only when every ``session_model_usage`` row of the session names that same
  model.
- One usage record of one request: ``_usage_anchor`` inside
  ``sessions.model_config``. It holds ``prompt_tokens`` and
  ``completion_tokens`` of the last model request of the session, and names the
  message before that request: ``base_count`` (its number among the message
  rows), ``base_last_role`` and ``base_last_fp``, the SHA-256 of the canonical
  JSON of that row's ``content``, ``role`` and ``tool_call_id``. The response of
  that request is the next assistant row. The decoder checks the three values
  against the rows and then gives that response one usage fact. Each turn
  overwrites the record, so the final store holds the record of the last
  response only. The record has no cache-read and no cache-write key.
- Totals: ``messages.token_count`` is NULL. The token columns of the session
  row and of one ``session_model_usage`` row per model and task are totals of
  all requests of the session. They are kept as ``session_usage`` and never used
  as the usage record of a response.
- A changed file, from two native values: the file source in the output of the
  frozen inspect helper (an earlier ``terminal`` result) is the pre-image; the
  ``old_string`` and ``new_string`` of a ``patch`` call in replace mode are the
  edit. ``old_string`` must occur exactly once in the pre-image, the tool row of
  the call must say ``success`` and the unified ``diff`` in that row must turn
  the pre-image into the same post-image. Both hashes are computed from these
  texts. A ``write_file`` call gives the post-image as its ``content``.
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

FORMAT = 'hermes-state-db-session-rows-v1'
ROWS_SCHEMA = 'session-bench-hermes-session-store-rows-v1'
STORE = 'state.db'
ROWS_FILE, SCHEMA_FILE = 'session-store-rows.json', 'session-store-schema.json'
APPLICATION_ID, USER_VERSION = 1148637816, 0
# Values of ``schema_version.version`` that this decoder reads. The value is not in the row export; it comes
# from the bound version extract (``scripts/extract_hermes_store_version.py``). One value was observed: 31.
SUPPORTED_SCHEMA_VERSIONS = (31,)
PROMPT_FILE = 'system-prompt-row.json'
PROMPT_ROW_SCHEMA = 'session-bench-hermes-system-prompt-row-extract-v1'
# The first sentence of the system prompt names the harness.
_HARNESS = re.compile(r'You are ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)*), built by ')
VERSION_EXTRACT_SCHEMA = 'session-bench-hermes-store-version-extract-v1'
TARGET = 'fixture_project/checkout.py'
COLUMNS = {
    'sessions': (
        'id source user_id model model_config system_prompt parent_session_id started_at ended_at end_reason message_count '
        'tool_call_count input_tokens output_tokens cache_read_tokens cache_write_tokens reasoning_tokens billing_provider '
        'billing_base_url billing_mode estimated_cost_usd actual_cost_usd cost_status cost_source pricing_version title '
        'api_call_count handoff_state handoff_platform handoff_error cwd rewind_count archived session_key chat_id chat_type '
        'thread_id display_name origin_json expiry_finalized system_prompt_hash git_branch git_repo_root '
        'git_metadata_generation title_source last_activity_at last_activity_description last_activity_provenance '
        'compression_failure_cooldown_until compression_failure_error compression_fallback_streak '
        'compression_ineffective_count compression_recovery_deadline profile_name pinned hidden last_read_at tool_names '
        'created_source compression_overload_streak transport_profile auto_archived').split(),
    'messages': (
        'id session_id role content tool_call_id tool_calls tool_name timestamp token_count finish_reason reasoning '
        'reasoning_content reasoning_details codex_reasoning_items codex_message_items platform_message_id observed active '
        'compacted effect_disposition _compressed_summary api_content display_kind display_metadata display_identity '
        'display_order message_uid absorbed_message_uids tool_call_uids tool_call_uid').split(),
    'session_model_usage': (
        'session_id model billing_provider billing_base_url billing_mode task api_call_count input_tokens output_tokens '
        'cache_read_tokens cache_write_tokens reasoning_tokens estimated_cost_usd actual_cost_usd cost_status cost_source '
        'first_seen last_seen').split(),
}
TOKEN_COLUMNS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')
_CANARY = re.compile(r'SB_SURVIVAL_V1_RESPONSE_[^\s]+')
_NONCE = re.compile(r'SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)')
_MAX_BYTES = 64 * 1024 * 1024
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'explanation'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}


class HermesRowsError(ValueError):
    """The row export is not a document that this decoder can read."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _strict(data, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HermesRowsError(f'{label}: duplicate JSON key')
            result[key] = value
        return result

    def constant(value):
        raise HermesRowsError(f'{label}: non-finite number')

    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, ValueError) as error:
        raise HermesRowsError(f'{label}: not JSON') from error


def _json_text(value):
    """A JSON value held as text in a column; None when it is not JSON."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def read_rows(rows, schema):
    """Parse the two export documents. Returns ``(tables, store schema, exceptions)``.

    ``tables`` maps a table name to its rows in rowid order; a row is
    ``{'rowid': n, 'values': {...}}``. An exception is a contract violation
    of the document shape; the caller makes the decode unsupported.
    """
    document, layout = _strict(rows, 'row export'), _strict(schema, 'schema export')
    exceptions = []
    stores = document.get('stores') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != ROWS_SCHEMA or not isinstance(document.get('session_id'), str)
            or not isinstance(stores, list) or len(stores) != 1 or not isinstance(stores[0], dict) or stores[0].get('store') != STORE
            or not isinstance(stores[0].get('tables'), list)):
        raise HermesRowsError('row export is not one session of state.db')
    described = layout.get('stores') if isinstance(layout, dict) else None
    if not isinstance(described, list) or len(described) != 1 or not isinstance(described[0], dict) or described[0].get('store') != STORE:
        raise HermesRowsError('schema export is not the schema of state.db')
    store = described[0]
    tables = {}
    for table in stores[0]['tables']:
        name, rows_of = table.get('table'), table.get('rows')
        if not isinstance(name, str) or name in tables or not isinstance(rows_of, list):
            raise HermesRowsError('row export table is malformed')
        if name not in COLUMNS:
            exceptions.append({'code': 'unknown_table', 'record': name})
        elif table.get('columns') != COLUMNS[name]:
            exceptions.append({'code': 'unknown_column_set', 'record': name})
        for row in rows_of:
            if (not isinstance(row, dict) or type(row.get('rowid')) is not int or not isinstance(row.get('values'), dict)
                    or not isinstance(table.get('columns'), list) or set(row['values']) != set(table['columns'])):
                raise HermesRowsError('row export row is malformed')
        tables[name] = sorted(rows_of, key=lambda row: row['rowid'])
        if len({row['rowid'] for row in rows_of}) != len(rows_of):
            raise HermesRowsError('row export repeats a row')
    if store.get('application_id') != APPLICATION_ID or store.get('user_version') != USER_VERSION or stores[0].get('user_version') != USER_VERSION:
        exceptions.append({'code': 'unsupported_version', 'record': 'application_id or user_version of the store'})
    declared = {row.get('name'): row for row in store.get('tables', []) if isinstance(row, dict)}
    for name, columns in COLUMNS.items():
        if declared.get(name, {}).get('columns') != columns:
            exceptions.append({'code': 'unsupported_schema', 'record': f'{name}: the store schema differs from the contract'})
    return tables, store, exceptions


def read_version_extract(data, store):
    """The declared schema version from the bound version extract; None when the extract is absent.

    The extract holds the rows of the table ``schema_version`` (one integer
    column). It must name the same store schema as the row export of the
    capture (``schema_sha256``). Raises ``HermesRowsError`` otherwise.
    """
    if data is None:
        return None
    document = _strict(data, 'version extract')
    rows = document.get('rows') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != VERSION_EXTRACT_SCHEMA or document.get('store') != STORE
            or document.get('table') != 'schema_version' or not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict) or set(rows[0]) != {'version'} or type(rows[0]['version']) is not int
            or document.get('application_id') != store.get('application_id') or document.get('user_version') != store.get('user_version')):
        raise HermesRowsError('version extract is not the one row of schema_version of this store')
    return rows[0]['version']


def read_prompt_row(data, session_hash):
    """The one bound ``system_prompts`` row as ``{'hash', 'prompt', 'rowid'}``; None when the document is absent.

    The row must be the row that the session row names: its ``hash`` equals
    ``sessions.system_prompt_hash`` and is the SHA-256 of the prompt text.
    Raises ``HermesRowsError`` otherwise.
    """
    if data is None:
        return None
    document = _strict(data, 'system prompt row')
    rows = document.get('rows') if isinstance(document, dict) else None
    if (not isinstance(document, dict) or document.get('schema_version') != PROMPT_ROW_SCHEMA or document.get('table') != 'system_prompts'
            or not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or set(rows[0]) != {'_rowid', 'hash', 'prompt'}
            or not isinstance(rows[0]['prompt'], str) or type(rows[0]['_rowid']) is not int):
        raise HermesRowsError('system prompt document is not one row of system_prompts')
    row = rows[0]
    if (not isinstance(session_hash, str) or row['hash'] != session_hash or document.get('session_system_prompt_hash') != session_hash
            or sha(row['prompt'].encode('utf-8')) != session_hash):
        raise HermesRowsError('system prompt row is not the row that the session row names')
    return {'hash': row['hash'], 'prompt': row['prompt'], 'rowid': row['_rowid']}


def store_schema_sha256(schema):
    """The schema digest of the store as the capture receipt states it (pragmas and ``sqlite_master`` rows)."""
    store = _strict(schema, 'schema export')['stores'][0]
    value = {'application_id': store['application_id'], 'user_version': store['user_version'], 'objects': store['objects']}
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))


def _relative(path):
    if isinstance(path, str) and (path.endswith('/fixture_project') or '/fixture_project/' in path):
        return 'fixture_project' + path.split('/fixture_project', 1)[1]
    return path


def _tool_calls(values):
    """The calls of one assistant row: ``[(call id, tool name, arguments)]``; None for a shape outside the contract."""
    raw = values.get('tool_calls')
    if raw is None:
        return []
    calls = _json_text(raw)
    if not isinstance(calls, list) or not calls:
        return None
    found = []
    for call in calls:
        function = call.get('function') if isinstance(call, dict) else None
        arguments = _json_text(function.get('arguments')) if isinstance(function, dict) else None
        if (not isinstance(call, dict) or call.get('type') != 'function' or not isinstance(call.get('id'), str) or not call['id']
                or not isinstance(function.get('name'), str) or not isinstance(arguments, dict)):
            return None
        found.append((call['id'], function['name'], arguments))
    return found if len({item[0] for item in found}) == len(found) else None


def _tool_result(values):
    """Output, exit code and status of one tool row; ``{}`` when the content is not the result object."""
    body = _json_text(values.get('content'))
    raw = values.get('content') if isinstance(values.get('content'), str) else None
    if not isinstance(body, dict) or not isinstance(body.get('output'), str):
        return {'output': raw, 'raw': raw}
    result = {'output': body['output'], 'raw': raw}
    if type(body.get('exit_code')) is int:
        result.update(exit_code=body['exit_code'], status='failure' if body['exit_code'] else 'success')
    return result


def _helper_argv(segment, run_canary):
    """The argv of a frozen helper segment, as the command states it (the run canary flag included)."""
    return shlex.split(segment['text'])


EDIT_TOOLS = ('write_file', 'patch')


def _targets_workload_file(path):
    if not isinstance(path, str):
        return False
    path = path.replace('\\', '/')
    return (path == 'checkout.py' or path.endswith('/checkout.py')) and '/snapshots/' not in '/' + path


def apply_unified_diff(text, diff):
    """Apply a unified diff to ``text``; None when a hunk does not fit. File header lines are skipped."""
    source, out, position = text.split('\n'), [], 0
    lines = diff.split('\n')
    index = 0
    while index < len(lines) and not lines[index].startswith('@@'):
        index += 1
    if index == len(lines):
        return None
    while index < len(lines):
        header = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*', lines[index])
        if header is None:
            return None
        start = int(header.group(1)) - 1 if header.group(2) != '0' else int(header.group(1))
        if start < position or start > len(source):
            return None
        out += source[position:start]
        position = start
        index += 1
        while index < len(lines) and not lines[index].startswith('@@'):
            line = lines[index]
            index += 1
            if line.startswith('\\') or (line == '' and index == len(lines)):
                continue
            mark, body = line[:1], line[1:]
            if mark in (' ', '-'):
                if position >= len(source) or source[position] != body:
                    return None
                position += 1
                if mark == ' ':
                    out.append(body)
            elif mark == '+':
                out.append(body)
            else:
                return None
    return '\n'.join(out + source[position:])


def usage_anchor(session, messages):
    """The one usage record of the session row, bound to the message rows; None when there is none or it does not bind.

    ``_usage_anchor`` of ``sessions.model_config`` holds the prompt and
    completion token counts of the last model request. It names the message
    before the request by count, role and content fingerprint. Returns
    ``{'base_row', 'response_row', 'usage'}``: the row that the record names,
    the assistant row that follows it, and the counts under the comparator's
    key names.
    """
    config = _json_text(session.get('model_config'))
    anchor = config.get('_usage_anchor') if isinstance(config, dict) else None
    if not isinstance(anchor, dict):
        return None
    count, counts = anchor.get('base_count'), [anchor.get('prompt_tokens'), anchor.get('completion_tokens')]
    if (type(count) is not int or not 1 <= count < len(messages) or any(type(value) is not int or value < 0 for value in counts)):
        return None
    base, following = messages[count - 1]['values'], messages[count]['values']
    member = {key: base.get(key) for key in ('content', 'role', 'tool_call_id')}
    prints = {sha(json.dumps(member, sort_keys=True, separators=(',', ':'), ensure_ascii=flag).encode('utf-8')) for flag in (True, False)}
    if anchor.get('base_last_fp') not in prints or anchor.get('base_last_role') != base.get('role') or following.get('role') != 'assistant':
        return None
    return {'base_row': messages[count - 1]['rowid'], 'response_row': messages[count]['rowid'],
            'usage': {'input_tokens': counts[0], 'output_tokens': counts[1]}, 'base_last_fp': anchor['base_last_fp']}


def decode_hermes_session_rows(rows, schema, *, run_canary=None, version_extract=None, prompt_row=None, artifact=ROWS_FILE):
    """Decode the row export and the schema export of one session into comparator facts.

    ``run_canary`` is the run canary of the workload. A helper segment that
    carries it gets the helper's canonical argv. ``version_extract`` is the
    bytes of the bound version extract and ``prompt_row`` the bytes of the
    bound system prompt row, when the capture holds them.
    """
    out = {key: [] for key in ('turns', 'responses', 'actions', 'results', 'relations', 'usage', 'file_changes', 'records')}
    tables, store, exceptions = read_rows(rows, schema)
    physical = sha(rows)
    out.update(format=FORMAT, status='ok', session_id=None, physical_sha256=physical, user_version=store.get('user_version'),
               application_id=store.get('application_id'), schema_version=None, harness=None, surface=None, session_usage=None,
               model=None, diagnostics=[{**item, 'severity': 'error'} for item in exceptions])

    def problem(code, **detail):
        out['diagnostics'].append({'code': code, 'severity': 'error', **detail})

    def locator(table, rowid):
        return {'artifact': artifact, 'table': table, 'line': rowid, 'physical_sha256': physical}

    try:
        version = read_version_extract(version_extract, store)
    except HermesRowsError:
        version = None
        problem('unsupported_version', record='version extract')
    if version is not None:
        out['schema_version'] = version
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            problem('unsupported_version', record=f'schema_version {version}')
    sessions = tables.get('sessions', [])
    if len(sessions) != 1 or not isinstance(sessions[0]['values'].get('id'), str):
        problem('missing_artifact', record='sessions row')
        out['status'] = 'unsupported'
        return out
    session = sessions[0]['values']
    out['session_id'] = session['id']
    # ``source`` names the entry point of the session (``oneshot`` for ``hermes -z``). No value of the session
    # rows names the harness; the first sentence of the system prompt row does.
    out['surface'] = session.get('source') if isinstance(session.get('source'), str) and session.get('source') else None
    try:
        prompt = read_prompt_row(prompt_row, session.get('system_prompt_hash'))
    except HermesRowsError:
        prompt = None
        problem('invalid_boundary', record='system prompt row')
    if prompt is not None:
        named = _HARNESS.match(prompt['prompt'])
        out['harness'] = named.group(1) if named else None
        out['records'].append({'raw': {'table': 'system_prompts', 'role': None},
                               'locator': {'artifact': 'capture/' + PROMPT_FILE, 'table': 'system_prompts', 'line': 1}, 'timestamp': None})
    usage_rows = [row['values'] for row in tables.get('session_model_usage', [])]
    models = {row.get('model') for row in usage_rows}
    if isinstance(session.get('model'), str) and session['model'] and models == {session['model']}:
        out['model'] = session['model']
    out['session_usage'] = {
        'scope': 'totals of all requests of the session; not a usage record of a response or a turn',
        'session_row': {key: session.get(key) for key in (*TOKEN_COLUMNS, 'api_call_count')},
        'model_task_rows': [{key: row.get(key) for key in ('model', 'task', 'api_call_count', *TOKEN_COLUMNS)} for row in usage_rows],
        'locator': locator('sessions', sessions[0]['rowid'])}
    for table in ('sessions', 'session_model_usage', 'messages'):
        for row in tables.get(table, []):
            values = row['values']
            if table != 'sessions' and values.get('session_id') != session['id']:
                problem('invalid_boundary', record=f'{table}:{row["rowid"]}')
            stamp = values.get('timestamp') if table == 'messages' else None
            out['records'].append({'raw': {'table': table, 'role': values.get('role') if table == 'messages' else None},
                                   'locator': locator(table, row['rowid']), 'timestamp': stamp})
    turn, pending = None, {}
    for row in tables.get('messages', []):
        values, rowid = row['values'], row['rowid']
        identity = f'messages:{rowid}'
        base = {'sequence': rowid, 'locator': locator('messages', rowid), 'timestamp': values.get('timestamp')}
        if (values.get('id') != rowid or values.get('active') != 1 or values.get('compacted') != 0 or values.get('_compressed_summary') != 0
                or values.get('observed') != 0 or isinstance(values.get('timestamp'), bool) or not isinstance(values.get('timestamp'), (int, float))
                or values.get('role') not in ('user', 'assistant', 'tool')):
            problem('malformed_record', record=identity)
            continue
        if values['role'] == 'user':
            if not isinstance(values.get('content'), str) or values.get('tool_calls') is not None:
                problem('malformed_record', record=identity)
                continue
            turn = identity
            out['turns'].append({**base, 'id': identity, 'role': 'user', 'text': values['content'], 'message_uid': values.get('message_uid')})
            continue
        if turn is None:
            problem('invalid_boundary', record=identity)
            continue
        base['turn_id'] = turn
        if values['role'] == 'assistant':
            calls = _tool_calls(values)
            content = values.get('content')
            if calls is None or not (content is None or isinstance(content, str)):
                problem('malformed_record', record=identity)
                continue
            if content:
                final = values.get('finish_reason') == 'stop' and not calls
                response = {**base, 'id': identity, 'role': 'assistant', 'text': content, 'phase': 'final_answer' if final else 'commentary'}
                if final:
                    response['status'] = 'completed'
                markers = _CANARY.findall(content)
                if len(markers) == 1 and content.rstrip().endswith(markers[0]):
                    response['canary'] = markers[0]
                if out['model'] is not None:
                    # The model of the session row; the row is joined by the declared key messages.session_id.
                    response.update(model_id=out['model'], configuration={'provider': session.get('billing_provider')},
                                    model_locator=locator('sessions', sessions[0]['rowid']))
                out['responses'].append(response)
                if final:
                    out['relations'].append({'kind': 'turn_response', 'from_id': turn, 'to_id': identity, 'locator': base['locator'],
                                             'method': 'native ordered message rows (messages.id) since the latest user row'})
            for call, name, arguments in calls:
                if call in pending or any(action.get('parent_call_id', action['call_id']) == call for action in out['actions']):
                    problem('malformed_record', record=identity)
                    continue
                pending[call] = _add_call(out, base, call, name, arguments, run_canary)
            continue
        call = values.get('tool_call_id')
        if not isinstance(call, str) or call not in pending or pending[call] is None:
            problem('unjoined_execution', record=identity)
            continue
        _add_result(out, base, call, pending[call], _tool_result(values))
        pending[call] = None
    if any(item is not None for item in pending.values()):
        problem('unjoined_execution', record='a tool call has no result row')
    anchor = usage_anchor(session, tables.get('messages', []))
    if anchor is not None:
        identity = f"messages:{anchor['response_row']}"
        response = next((row for row in out['responses'] if row['id'] == identity), None)
        if response is not None:
            # The usage record of the last request of the session; joined by count, role and content fingerprint
            # of the message before the request. It has no cache-read and no cache-write key.
            out['usage'].append({'id': identity + ':usage', 'response_id': identity, 'turn_id': response['turn_id'], 'usage': dict(anchor['usage']),
                                 'locator': locator('sessions', sessions[0]['rowid']), 'timestamp': response['timestamp'],
                                 'native_key': f"_usage_anchor.base_count, base_last_role and base_last_fp name messages:{anchor['base_row']}"})
    _add_file_changes(out)
    if out['diagnostics']:
        out['status'] = 'unsupported'
    return out


def _add_file_changes(out):
    """One changed-file fact per successful native edit chain of the workload file.

    Pre-image: the file source in the output of the inspect helper, from the
    latest earlier result. A ``patch`` call in replace mode: ``old_string``
    occurs exactly once, the result row says ``success`` and its unified diff
    turns the pre-image into the same text as the replacement. A
    ``write_file`` call: the post-image is its ``content`` and the result row
    has no ``error``. Anything else gives no fact.
    """
    results = {row['action_id']: row for row in out['results']}
    source = None
    for action in sorted(out['actions'], key=lambda row: row['sequence']):
        result = results.get(action['id'])
        if result is None:
            continue
        if action.get('action_kind') == 'inspect' and isinstance(result.get('output'), str):
            body = _json_text(result['output'].split(' ', 1)[1]) if ' ' in result['output'] else None
            if isinstance(body, dict) and isinstance(body.get('checkout_source'), str):
                source = (body['checkout_source'], result['locator'])
            continue
        if action.get('action_kind') != 'edit' or action.get('name') not in EDIT_TOOLS or source is None:
            continue
        arguments, done = action['input'], _json_text(result.get('output'))
        after = None
        if action['name'] == 'patch' and arguments.get('mode', 'replace') == 'replace' and isinstance(done, dict) and done.get('success') is True:
            old, new = arguments.get('old_string'), arguments.get('new_string')
            if (isinstance(old, str) and isinstance(new, str) and old and source[0].count(old) == 1 and arguments.get('replace_all') in (None, False)
                    and isinstance(done.get('diff'), str) and apply_unified_diff(source[0], done['diff']) == source[0].replace(old, new, 1)):
                after = source[0].replace(old, new, 1)
        elif action['name'] == 'write_file' and isinstance(arguments.get('content'), str) and isinstance(done, dict) and not done.get('error'):
            after = arguments['content']
        if after is None:
            source = None
            continue
        out['file_changes'].append({'id': action['id'] + ':change', 'path': action['target'], 'before_sha256': sha(source[0].encode('utf-8')),
                                    'after_sha256': sha(after.encode('utf-8')), 'hash_source': 'native_preimage_and_edit',
                                    'turn_id': action['turn_id'], 'action_id': action['id'], 'call_id': action['call_id'],
                                    'timestamp': result['timestamp'], 'locator': result['locator'], 'preimage_locator': source[1],
                                    'sequence': len(out['file_changes']) + 1})
        source = (after, result['locator'])


def _add_call(out, base, call, name, arguments, run_canary):
    """Add the action or the segment actions of one native call. Returns what its result row needs."""
    command = arguments.get('command')
    action = {**base, 'id': call, 'call_id': call, 'name': name, 'input': arguments}
    cwd = _relative(arguments.get('workdir'))
    if isinstance(cwd, str):
        action['cwd'] = cwd
    if name.strip().lower() != 'terminal' or not isinstance(command, str):
        if isinstance(arguments.get('path'), str):
            action['target'] = _relative(arguments['path']) if _targets_workload_file(arguments['path']) else arguments['path']
            if _targets_workload_file(arguments['path']):
                action['target'] = TARGET
                if name in EDIT_TOOLS:
                    action['action_kind'] = 'edit'
        out['actions'].append(action)
        return {'kind': 'plain', 'action': call, 'tool': name}
    action['command'] = command
    compound = compound_shell_segments(name, arguments)
    if compound is not None:
        # Compound shell call rule: the call is the native record of each scored segment.
        parts = []
        for item in compound:
            part_id = f"{call}:segment-{item['index']}"
            part = {**base, 'id': part_id, 'call_id': part_id, 'parent_call_id': call, 'segment_index': item['index'], 'name': name,
                    'input': {'command': item['text']}, 'command': item['text']}
            if isinstance(cwd, str):
                part['cwd'] = cwd
            if item['kind'] == 'helper':
                part.update(argv=_helper_argv(item, run_canary), action_kind='inspect' if item['phase'] == 'inspect' else 'test')
            else:
                part.update(action_kind='edit', target=_relative(item['path']) if '/' in item['path'] else 'fixture_project/' + item['path'])
            out['actions'].append(part)
            parts.append(part_id)
        return {'kind': 'compound', 'segments': compound, 'parts': parts}
    segments = split_shell_segments(command)
    scored = [(segment, classify_shell_segment(segment)) for segment in segments or []]
    helpers = [(segment, role) for segment, role in scored if role is not None and role['kind'] == 'helper']
    last = None
    if len(helpers) == 1 and sum(role is not None for _, role in scored) == 1:
        # One scored segment: the call stays one call. Its identity is the helper invocation that it holds.
        segment, role = helpers[0]
        action.update(argv=_helper_argv({**segment, **role}, run_canary), action_kind='inspect' if role['phase'] == 'inspect' else 'test',
                      scored_segment_index=segment['index'])
        last = segment['index'] == segments[-1]['index']
    else:
        try:
            action['argv'] = shlex.split(command)
        except ValueError:
            pass
    out['actions'].append(action)
    return {'kind': 'plain', 'action': call, 'helper_is_last': last}


def _add_result(out, base, call, pending, native):
    output = native.get('output')
    if pending['kind'] == 'compound':
        found = helper_segment_results(pending['segments'], output or '', native.get('exit_code', 0) != 0)
        for item, part_id in zip(pending['segments'], pending['parts']):
            line = found.get(item['index']) if item['kind'] == 'helper' else None
            if line is None:
                continue
            # The call's own exit code counts only for the last segment of the call.
            code = native.get('exit_code') if item['follows'] is None else line['exit_code']
            outcome = {**base, 'id': part_id + ':result', 'call_id': part_id, 'parent_call_id': call, 'action_id': part_id, 'output': line['output']}
            if type(code) is int:
                outcome.update(exit_code=code, status='failure' if code else 'success')
            nonces = _NONCE.findall(line['output'])
            if len(nonces) == 1:
                outcome['helper_nonce'] = nonces[0]
            out['results'].append(outcome)
            out['relations'].append({'kind': 'action_result', 'from_id': part_id, 'to_id': outcome['id'], 'locator': base['locator'],
                                     'native_key': 'tool_call_id of the tool row and segment index'})
        return
    result = {**base, 'id': call + ':result', 'call_id': call, 'action_id': call, 'output': output}
    if pending.get('tool') is not None:
        # A tool other than the terminal: the result row is the whole content. A file tool says ``success`` or ``error``.
        result['output'] = native.get('raw')
        body = _json_text(native.get('raw'))
        if isinstance(body, dict) and body.get('error'):
            result['status'] = 'failure'
        elif isinstance(body, dict) and (body.get('success') is True or pending['tool'] in ('read_file', 'search_files', 'write_file')):
            result['status'] = 'success'
        out['results'].append(result)
        out['relations'].append({'kind': 'action_result', 'from_id': call, 'to_id': result['id'], 'locator': base['locator'],
                                 'native_key': 'tool_call_id of the tool row'})
        return
    # A call with one helper segment: its status is the helper's status only when the helper is the last segment.
    if type(native.get('exit_code')) is int and pending.get('helper_is_last') is not False:
        result.update(exit_code=native['exit_code'], status=native['status'])
    nonces = _NONCE.findall(output or '')
    if len(nonces) == 1:
        result['helper_nonce'] = nonces[0]
    out['results'].append(result)
    out['relations'].append({'kind': 'action_result', 'from_id': call, 'to_id': result['id'], 'locator': base['locator'],
                             'native_key': 'tool_call_id of the tool row'})


def decode_hermes_native(native, *, run_canary=None, version_extract=None):
    """Decode the one session of a packet's native directory.

    Opened: the row export, the schema export and, when it is there, the
    system prompt row.
    """
    native = Path(native)
    files = {}
    for name in (ROWS_FILE, SCHEMA_FILE, PROMPT_FILE):
        path = native / 'capture' / name
        if name == PROMPT_FILE and not path.exists():
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
            raise ValueError('unsafe Hermes native input')
        files[name] = path.read_bytes()
    return decode_hermes_session_rows(files[ROWS_FILE], files[SCHEMA_FILE], run_canary=run_canary, version_extract=version_extract,
                                      prompt_row=files.get(PROMPT_FILE), artifact='capture/' + ROWS_FILE)


def add_native_final_after_chain(decoded, instance):
    """Add the final-after relation when R2, edit, edit result, final test, its result and the R2 response rise in row order.

    The keys are native: ``messages.id`` inside the R2 turn (the order of
    writing) and ``tool_call_id``, which binds each result row to its call. No
    time is used. The edit must be one native edit action on the workload file
    with a successful result, and the final test must have exit code 0.
    """
    if decoded['status'] != 'ok' or len(decoded['turns']) != 2:
        return
    r2 = decoded['turns'][1]
    inside = lambda rows: [row for row in rows if row.get('turn_id') == r2['id']]
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
    # A compound call holds the edit and the final test in one row: then the two calls and the two results share a row.
    if order != sorted(order) or not order[0] < order[1] or not order[4] < order[5] or (order[1] != order[3] and not order[2] < order[3]):
        return
    decoded['relations'].append({'kind': 'final_after', 'from_id': r2['id'], 'to_id': finals[0]['id'], 'locator': finals[0]['locator'],
                                 'native_order_rows': order, 'native_call_ids': [edit['call_id'], finals[0]['call_id']],
                                 'method': 'rising messages.id inside the R2 turn; each result row bound to its call by tool_call_id'})


# --- Statements and logical records (duplicate safety and density) ---

def row_bytes(values):
    """Logical bytes of one row: compact key-sorted JSON of its columns; a BLOB counts by its raw length."""
    plain = {key: ('' if isinstance(value, dict) else value) for key, value in values.items()}
    blobs = sum(len(value.get('blob_hex', value.get('text_hex', ''))) // 2 for value in values.values() if isinstance(value, dict))
    return len(json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')) + blobs


def row_proof(table, row):
    return json.dumps([table, row['rowid'], row['values']], sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def message_statements(rows):
    """(record ids, base roles, statements) for the message rows in ``messages.id`` order.

    What a row states (the count is made on the raw rows):

    - a user row states its prompt;
    - an assistant row states its text, when it has one, and each of its tool
      calls (``tool_calls``). ``codex_message_items`` holds the same text
      again and ``reasoning_content`` the same summary as ``reasoning``, inside
      the same row: one row is one record;
    - a tool row states the result of its ``tool_call_id``. It states the call
      again only when it holds the tool name and all text arguments of the
      call.

    No column marks a row as superseded in these sessions (``active`` 1,
    ``compacted`` 0), so every statement is active.
    """
    calls = {}
    for row in rows:
        for call, name, arguments in _tool_calls(row['values']) or []:
            calls[call] = (name, arguments)
    record_ids, roles, statements = [], [], []
    for row in rows:
        values, events, role = row['values'], [], 'unknown'
        if values.get('role') == 'user':
            role, events = 'user_message', [f"message:{row['rowid']}"]
        elif values.get('role') == 'assistant':
            own = _tool_calls(values) or []
            if values.get('content'):
                events.append(f"message:{row['rowid']}")
            events += [f'call:{call}' for call, _, _ in own]
            role = 'tool_call' if own else 'assistant_message' if events else 'explanation' if values.get('reasoning') else 'metadata'
        elif values.get('role') == 'tool':
            role, call = 'tool_result', values.get('tool_call_id')
            if isinstance(call, str):
                events.append(f'result:{call}')
                if call in calls and values.get('tool_name') == calls[call][0] and holds_call_arguments(
                        calls[call][1], string_leaves(values.get('content')), names_tool=True):
                    events.append(f'call:{call}')
        record_ids.append(f"messages:{row['rowid']}")
        roles.append(role)
        statements.append(tuple(dict.fromkeys(events)))
    return record_ids, roles, statements


def read_hermes_family(native, *, artifacts):
    """Return (records, locators, exceptions, occurrences) for the bound rows of the session.

    ``artifacts`` is the packet inventory (path, sha256). A file that differs
    from it raises ValueError. Density counts the rows of the session. The
    schema export describes the whole shared store; it is no record of the
    session and is not counted. The system prompt row, when it is bound, is a
    ``system`` record. A row of a table outside the contract is counted as
    ``unknown`` and reported as an exception.
    """
    native = Path(native)
    expected = {row['path']: row['sha256'] for row in artifacts}
    names = {'capture/' + ROWS_FILE, 'capture/' + SCHEMA_FILE}
    if len(expected) != len(artifacts) or not names <= set(expected):
        raise ValueError('Hermes family inventory is malformed')
    data = {}
    for name in sorted(expected):
        path = native / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Hermes family member is missing')
        data[name] = path.read_bytes()
        if len(data[name]) > _MAX_BYTES or sha(data[name]) != expected[name]:
            raise ValueError('Hermes family member differs from its inventory')
    tables, _, exceptions = read_rows(data['capture/' + ROWS_FILE], data['capture/' + SCHEMA_FILE])
    records, locators = [], []

    def add(record_id, kind, row, table):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': row_bytes(row['values']),
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': sha(row_proof(table, row))})

    prefix = 'capture/' + ROWS_FILE
    for row in tables.get('sessions', []):
        add(f"{prefix}:sessions:{row['rowid']}", 'session', row, 'sessions')
    for row in tables.get('session_model_usage', []):
        add(f"{prefix}:session_model_usage:{row['rowid']}", 'metadata', row, 'session_model_usage')
    record_ids, roles, statements = message_statements(tables.get('messages', []))
    for record, role, row in zip(record_ids, roles_after_repeats(roles, statements), tables.get('messages', [])):
        if role == 'unknown':
            exceptions.append({'code': 'unknown_message_role', 'record': f'{prefix}:{record}'})
        add(f'{prefix}:{record}', role, row, 'messages')
    occurrences = forward_occurrences([f'{prefix}:{record}' for record in record_ids], statements)
    for table in sorted(set(tables) - set(COLUMNS)):
        for row in tables[table]:
            add(f"{prefix}:{table}:{row['rowid']}", 'unknown', row, table)
    prompt_name = 'capture/' + PROMPT_FILE
    if prompt_name in expected:
        # The system prompt row: a ``system`` record. It states an event only if it holds a whole prompt of a user row;
        # then it is one more occurrence of that prompt. Its row id in the operator's store is not part of the record.
        sessions = tables.get('sessions', [])
        prompt = read_prompt_row(data[prompt_name], sessions[0]['values'].get('system_prompt_hash') if len(sessions) == 1 else None)
        record = f'{prompt_name}:system_prompts:row'
        add(record, 'system', {'rowid': 0, 'values': {'hash': prompt['hash'], 'prompt': prompt['prompt']}}, 'system_prompts')
        restated = tuple(f"message:{row['rowid']}" for row in tables.get('messages', [])
                         if row['values'].get('role') == 'user' and row['values'].get('content') and row['values']['content'] in prompt['prompt'])
        occurrences += forward_occurrences([record], [restated])
        names = names | {prompt_name}
    for name in sorted(set(expected) - names):
        exceptions.append({'code': 'unknown_family_member', 'record': name})
        records.append({'record_id': name, 'record_kind': 'unknown', 'logical_bytes': len(data[name]), 'classification': 'unknown'})
        locators.append({'id': name, 'sha256': expected[name]})
    return records, locators, exceptions, occurrences


def build_hermes_density(native, *, artifacts, complete_record_family=False):
    """Density evidence for the complete bound rows of the session; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _, _ = read_hermes_family(native, artifacts=artifacts)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Hermes density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Hermes density inventory is empty')
    locators.append({'id': 'rule:canonical-native-json-record-utf8-v1+database-rows:session_bench/hermes_store_rows.py',
                     'sha256': sha(Path(__file__).read_bytes())})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())


def usage_key_findings(rows, schema):
    """Places of the bound rows that could hold a token count of a response or a turn (absence scan).

    The scan covers every column of the three tables and every key of the
    JSON values in them. The session totals (the token and cost columns of
    ``sessions`` and ``session_model_usage``) are known and are no record of a
    response. ``_usage_anchor`` of ``sessions.model_config`` is the known
    usage record of the last request; the decoder reads it. ``messages.token_count``
    is the one column that could hold a count per message: a row where it is
    not NULL is a finding. So is any other key of a message row that names a
    token count.
    """
    tables, _, _ = read_rows(rows, schema)
    known = re.compile(r'token|usage|cost|billing|pricing', re.I)
    findings = []

    def keys(value, found):
        if isinstance(value, dict):
            for key, item in value.items():
                found.add(key)
                keys(item, found)
        elif isinstance(value, list):
            for item in value:
                keys(item, found)
        return found

    for row in tables.get('messages', []):
        if row['values'].get('token_count') is not None:
            findings.append(f"messages:{row['rowid']}:token_count")
        for column, value in row['values'].items():
            for key in keys(_json_text(value), set()):
                if known.search(key):
                    findings.append(f"messages:{row['rowid']}:{column}:{key}")
    for table in set(tables) - set(COLUMNS):
        findings.append(f'{table}: table outside the contract')
    return sorted(set(findings))


def remove_final_response(rows, canary):
    """Selected-loss control: the row export without the one assistant row that ends with ``canary``.

    Returns ``(new bytes, removed records)``.
    """
    document = json.loads(rows)
    removed = []
    for table in document['stores'][0]['tables']:
        if table['table'] != 'messages':
            continue
        kept = []
        for row in table['rows']:
            values = row['values']
            if values.get('role') == 'assistant' and isinstance(values.get('content'), str) and values['content'].rstrip().endswith(canary):
                removed.append({'path': 'capture/' + ROWS_FILE, 'table': 'messages', 'rowid': row['rowid'], 'sha256': sha(row_proof('messages', row)),
                                'control_transformation': 'delete the selected assistant message row only'})
            else:
                kept.append(row)
        table['rows'] = kept
    if len(removed) != 1:
        raise ValueError('Hermes loss control requires one final response row')
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode('utf-8') + b'\n', removed
