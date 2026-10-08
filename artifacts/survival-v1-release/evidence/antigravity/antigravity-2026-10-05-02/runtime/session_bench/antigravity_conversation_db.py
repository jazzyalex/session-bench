"""Read one Antigravity conversation database (``conversations/<conversation-id>.db``).

The database is the primary record of a conversation. It is a SQLite file
(``PRAGMA user_version`` 1). The file is never opened in place: its bytes are
copied to a new temporary directory and the copy is opened.

Tables: ``trajectory_meta`` (one row: ``trajectory_id``, ``cascade_id`` = the
conversation id), ``steps`` (one row per step: ``idx``, ``step_type``,
``status``, ``step_format``, ``metadata``, ``step_payload``), ``gen_metadata``
(one row per model call), ``executor_metadata``, ``trajectory_metadata_blob``,
``parent_references`` and ``battle_mode_infos``. The BLOB columns hold
protobuf messages. The vendor publishes no schema, so fields are named here by
number (``docs/survival-v1/adapters/antigravity.md`` has the map):

- step ``metadata``: 1 = creation time (1 seconds, 2 nanoseconds, UTC);
  4 = the tool call a tool step answers (1 call id, 2 name, 3 JSON arguments);
  9 = usage of a model step (2 input, 3 output, 5 cache-read count, 7 response
  id, 11 provider request id).
- ``step_payload`` by ``step_type``: 14 user input (19.2 text); 15 model step
  (20.1 text, 20.3 thinking, 20.6 response id, 20.7 tool calls: 1 id, 2 name,
  3 JSON arguments); 132 tool step (140.2.1 result text); 101 system message
  (114.4.4 text).
- ``gen_metadata.data``: 1.4.7 response id, 1.19 model name, 1.20 labels
  (1 key, 2 value), 1.2 the messages sent to the model (18 step index, 2 role,
  3 text, 11 thinking text, 6 tool call, 7 tool call id; 4 is an estimated
  size of the message, not a usage count), 1.9.10 context-size estimates,
  1.16 prompt sections (1 name, 2 body).
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

USER_VERSION = 1
STEP_FORMAT = 0
STEP_DONE = 3
STEP_USER, STEP_MODEL, STEP_SYSTEM, STEP_TOOL = 14, 15, 101, 132
STEP_TYPES = frozenset({STEP_USER, STEP_MODEL, STEP_SYSTEM, STEP_TOOL})
TABLES = frozenset({'trajectory_meta', 'steps', 'gen_metadata', 'executor_metadata', 'parent_references',
                    'trajectory_metadata_blob', 'battle_mode_infos'})
_MAX_ROWS = 100_000


class ConversationDatabaseError(ValueError):
    """The bytes are not a readable conversation database."""


def _varint(data, position):
    value = shift = 0
    while True:
        if position >= len(data) or shift > 63:
            raise ValueError('truncated varint')
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, position


def parse_message(data):
    """Fields of one protobuf message as (number, wire type, value, start, end); raises ValueError when it is not one.

    ``start`` and ``end`` bound the value bytes inside ``data``.
    """
    fields, position = [], 0
    while position < len(data):
        key, position = _varint(data, position)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError('field number 0')
        if wire == 0:
            start = position
            value, position = _varint(data, position)
        elif wire == 1:
            start, position = position, position + 8
            value = data[start:position]
        elif wire == 5:
            start, position = position, position + 4
            value = data[start:position]
        elif wire == 2:
            size, start = _varint(data, position)
            position = start + size
            value = data[start:position]
        else:
            raise ValueError('unsupported wire type')
        if position > len(data):
            raise ValueError('truncated field')
        fields.append((number, wire, value, start, position))
    return fields


def field(data, *path):
    """Every value at a path of field numbers, in order. A part that is not a message gives no value."""
    values = [data]
    for number in path:
        found = []
        for value in values:
            if not isinstance(value, bytes):
                continue
            try:
                found += [item[2] for item in parse_message(value) if item[0] == number]
            except ValueError:
                continue
        values = found
    return values


def one(data, *path, kind=bytes):
    """The single value at a path, or None when it is missing, repeated or of another kind."""
    values = field(data, *path)
    if len(values) != 1 or not isinstance(values[0], kind) or isinstance(values[0], bool):
        return None
    return values[0]


def text(data, *path):
    value = one(data, *path)
    try:
        return None if value is None else value.decode('utf-8')
    except UnicodeDecodeError:
        return None


def read_conversation_db(database):
    """Open a private copy of the database bytes and export every row.

    Returns ``user_version``, ``schema`` and ``tables`` ({name: rows in rowid order}).
    """
    if not isinstance(database, bytes) or database[:16] != b'SQLite format 3\x00':
        raise ConversationDatabaseError('conversation database bytes are missing')
    with tempfile.TemporaryDirectory(prefix='bench-antigravity-db-') as directory:
        clone = Path(directory) / 'conversation.db'
        clone.write_bytes(database)
        try:
            connection = sqlite3.connect(clone)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute('PRAGMA query_only=ON'); connection.execute('PRAGMA trusted_schema=OFF')
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ConversationDatabaseError('conversation database fails the SQLite integrity check')
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                schema = [dict(row) for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
                tables = {}
                for row in schema:
                    if row['type'] != 'table':
                        continue
                    name = row['name'].replace('"', '""')
                    rows = [dict(item) for item in connection.execute(f'SELECT rowid AS "__rowid__", * FROM "{name}" ORDER BY rowid LIMIT {_MAX_ROWS + 1}')]
                    if len(rows) > _MAX_ROWS:
                        raise ConversationDatabaseError('conversation database table row limit')
                    tables[row['name']] = rows
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise ConversationDatabaseError('conversation database is not readable') from error
    return {'user_version': version, 'schema': schema, 'tables': tables, 'physical_sha256': hashlib.sha256(database).hexdigest()}


def row_bytes(row):
    """Logical bytes of one exported row: compact key-sorted JSON; a BLOB counts by its raw length."""
    plain = {key: '' if isinstance(value, bytes) else value for key, value in row.items() if key != '__rowid__'}
    return (len(json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))
            + sum(len(value) for value in row.values() if isinstance(value, bytes)))


def row_proof(row):
    """Stable bytes of one exported row for a locator digest."""
    plain = {key: {'blob_hex': value.hex()} if isinstance(value, bytes) else value for key, value in row.items() if key != '__rowid__'}
    return json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def tool_calls(payload):
    """(call id, name, parsed arguments) of every tool call of a model step; None marks an unreadable part."""
    calls = []
    for item in field(payload, 20, 7):
        arguments = text(item, 3)
        try:
            parsed = json.loads(arguments) if arguments is not None else None
        except ValueError:
            parsed = None
        calls.append((text(item, 1), text(item, 2), parsed if isinstance(parsed, dict) else None))
    return calls


def generation_rows(store):
    """{response id: {'model', 'model_enum', 'row'}} from ``gen_metadata``; a repeated response id is dropped."""
    result, repeated = {}, set()
    for row in store['tables'].get('gen_metadata', []):
        data = row.get('data')
        if not isinstance(data, bytes):
            continue
        response = text(data, 1, 4, 7)
        if response is None:
            continue
        if response in result:
            repeated.add(response)
        labels = {text(item, 1): text(item, 2) for item in field(data, 1, 20)}
        result[response] = {'model': text(data, 1, 19), 'model_enum': labels.get('model_enum'), 'row': row['idx']}
    return {key: value for key, value in result.items() if key not in repeated}


def snapshot_statements(store):
    """Events that a ``gen_metadata`` row states again in its copy of the messages sent to the model.

    Returns (event id, occurrence id) pairs. Role 1 is a user or system message,
    2 a model message (text in field 3, thinking text in field 11, tool calls
    in field 6), 4 a tool result. The copy of a system step is skipped: the
    notice is injected context that ``step_type`` 101 marks, not an event.
    """
    system = {row.get('idx') for row in store['tables'].get('steps', []) if row.get('step_type') == STEP_SYSTEM}
    statements = []
    for row in store['tables'].get('gen_metadata', []):
        data = row.get('data')
        if not isinstance(data, bytes):
            continue
        for position, message in enumerate(field(data, 1, 2)):
            step, role = one(message, 18, kind=int) or 0, one(message, 2, kind=int)
            where = f"gen_metadata:{row['idx']}:message-{position}"
            if step in system:
                continue
            if (role in (1, 2) and field(message, 3)) or (role == 2 and field(message, 11)):
                statements.append((f'message:step:{step}', where + ':text'))
            if role == 2:
                for index, call in enumerate(field(message, 6)):
                    statements.append((f'call:{text(call, 1)}', f'{where}:call-{index}'))
            if role == 4:
                statements.append((f'result:step:{step}', where))
    return statements


def contract_exceptions(store, session_id):
    """Reasons why the database does not match the decoder contract (empty when it does)."""
    exceptions = []
    if store['user_version'] != USER_VERSION:
        exceptions.append({'code': 'unsupported_user_version', 'record': 'conversation.db:user_version'})
    tables = store['tables']
    if set(tables) != TABLES:
        exceptions.append({'code': 'unknown_table_set', 'record': 'conversation.db:sqlite_master'})
    meta = tables.get('trajectory_meta', [])
    if len(meta) != 1 or meta[0].get('cascade_id') != session_id:
        exceptions.append({'code': 'conversation_id_differs', 'record': 'conversation.db:trajectory_meta'})
    for row in tables.get('steps', []):
        where = f"conversation.db:steps:{row.get('idx')}"
        payload, metadata = row.get('step_payload'), row.get('metadata')
        if row.get('step_type') not in STEP_TYPES:
            exceptions.append({'code': 'unknown_step_type', 'record': where})
        elif row.get('status') != STEP_DONE or row.get('step_format') != STEP_FORMAT:
            exceptions.append({'code': 'unknown_step_status_or_format', 'record': where})
        elif not isinstance(payload, bytes) or not isinstance(metadata, bytes) or one(metadata, 1, 1, kind=int) is None:
            exceptions.append({'code': 'unknown_step_shape', 'record': where})
        elif row['step_type'] == STEP_TOOL and (text(metadata, 4, 1) is None or text(payload, 140, 2, 1) is None):
            exceptions.append({'code': 'unknown_tool_step_shape', 'record': where})
        elif row['step_type'] == STEP_USER and text(payload, 19, 2) is None:
            exceptions.append({'code': 'unknown_user_step_shape', 'record': where})
        elif row['step_type'] == STEP_MODEL and (text(payload, 20, 6) is None or any(None in call for call in tool_calls(payload))):
            exceptions.append({'code': 'unknown_model_step_shape', 'record': where})
    return exceptions


def remove_step(database, index):
    """The database bytes without one step row (selected-loss control). Works on a private copy."""
    with tempfile.TemporaryDirectory(prefix='bench-antigravity-loss-') as directory:
        clone = Path(directory) / 'conversation.db'
        clone.write_bytes(database)
        connection = sqlite3.connect(clone)
        try:
            connection.execute('PRAGMA journal_mode=DELETE')
            if connection.execute('DELETE FROM steps WHERE idx=?', (index,)).rowcount != 1:
                raise ConversationDatabaseError('step row to remove is missing')
            connection.commit()
        finally:
            connection.close()
        return clone.read_bytes()


def pack_time(seconds, nanos=0):
    """Test helper: a protobuf timestamp message."""
    return b'\x08' + _encode_varint(seconds) + b'\x10' + _encode_varint(nanos)


def _encode_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def encode(fields):
    """Test helper: encode [(number, value)] where a value is int, bytes, str or a nested list."""
    out = bytearray()
    for number, value in fields:
        if isinstance(value, int):
            out += _encode_varint(number << 3) + _encode_varint(value)
        else:
            body = encode(value) if isinstance(value, list) else value.encode('utf-8') if isinstance(value, str) else value
            out += _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
    return bytes(out)
