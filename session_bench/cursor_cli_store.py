"""Read one Cursor CLI chat store (``chats/<workspace-hash>/<session-id>/store.db``).

The store is a SQLite file (``PRAGMA user_version`` 1) with two tables. The
file is never opened in place: its bytes are copied to a new temporary
directory and the copy is opened.

- ``meta`` has one row, key ``0``. Its value is the hex text of a JSON object:
  ``agentId`` (the session id), ``latestRootBlobId``, ``name``, ``createdAt``,
  ``mode``, ``isRunEverything`` and ``blobEncryptionKey`` (an opaque vendor key;
  the blobs of this build are stored in clear).
- ``blobs`` is a content-addressed table: ``id`` is the SHA-256 of ``data``.

A blob is one of six classes. The decoder names every blob; a blob outside
these classes is a contract exception.

- ``message``: a JSON object with ``role`` (system, user, assistant, tool).
  This is the conversation as it is sent to the model.
- ``root``: a protobuf message, the state of the conversation after one write.
  ``meta.latestRootBlobId`` names the current root. Every older root stays in
  the table as a checkpoint.
- ``turn``: a protobuf message, one user turn: its user message and its steps.
- ``user_message``: a protobuf message, the prompt as the user typed it.
- ``step``: a protobuf message, one of text, tool call with its result, or
  thinking. A step has its own times in Unix milliseconds.
- ``file_content``: the raw bytes of a workspace file before or after an edit.

The vendor publishes no schema for the protobuf blobs. ``SCHEMA`` names every
field by number; ``docs/survival-v1/adapters/cursor.md`` has the meaning.
Opaque by design and kept as bytes: ``reasoning.signature`` of a JSON
assistant message (encrypted model reasoning), field 4 of a turn (a vendor
token) and ``blobEncryptionKey``.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

STORE_FORMAT = 'cursor-cli-chat-store-v1'
USER_VERSION = 1
SIDECAR_SCHEMA_VERSION = 1
TABLES = {'meta': ['key', 'value'], 'blobs': ['id', 'data']}
META_KEYS = frozenset({'agentId', 'latestRootBlobId', 'name', 'createdAt', 'mode', 'isRunEverything', 'blobEncryptionKey'})
SIDECAR_KEYS = frozenset({'schemaVersion', 'createdAtMs', 'hasConversation', 'updatedAtMs', 'cwd'})
TOOLS = {1: 'Shell', 4: 'Glob', 8: 'Read', 12: 'Write'}
_MAX_ROWS = 100_000
_MAX_BLOB_BYTES = 8 * 1024 * 1024
_HEX = re.compile(r'[0-9a-f]{64}\Z')

# Field maps. A kind is 'v' (varint), 's' (UTF-8 text), 'b' (opaque bytes),
# ('m', type) (nested message) or ('r', class) (the 32-byte id of a blob).
SCHEMA = {
    'root': {1: ('r', 'message'), 4: 's', 5: ('m', 'context'), 8: ('r', 'turn'), 9: 's', 10: 'v', 15: ('m', 'file_state'),
             18: 's', 21: ('m', 'git'), 22: 's', 26: 'v', 27: 's', 38: 's', 39: 'v'},
    # Context-window estimate of the next request: 1 used tokens, 2 window size, 3 the same with a list of parts.
    'context': {1: 'v', 2: 'v', 3: ('m', 'context_parts')},
    'context_parts': {1: 'v', 2: 'v', 3: ('m', 'context_part')},
    'context_part': {1: 's', 2: 's', 3: 'v', 4: 'v'},
    'file_state': {1: 's', 2: ('m', 'file_refs')},
    'file_refs': {1: ('r', 'file_content'), 2: ('r', 'file_content')},
    'git': {1: 's', 2: 's'},
    'turn': {1: ('m', 'turn_body')},
    'turn_body': {1: ('r', 'user_message'), 2: ('r', 'step'), 3: 's', 4: 'b', 5: 'v', 9: 's', 10: 's'},
    'user_message': {1: 's', 2: 's', 3: 's', 4: 'v', 10: ('r', 'root'), 17: 's', 25: 'v', 26: 'v'},
    'step': {1: ('m', 'text'), 2: ('m', 'tool'), 3: ('m', 'thinking')},
    'text': {1: 's', 2: 'v', 3: 'v'},
    'thinking': {1: 's', 2: 'v', 3: 'v', 4: 'v'},
    'tool': {1: ('m', 'shell'), 4: ('m', 'glob'), 8: ('m', 'read'), 12: ('m', 'write'), 57: 's', 59: 'v', 60: 'v'},
    'shell': {1: ('m', 'shell_args'), 2: ('m', 'shell_result'), 3: 's'},
    'shell_args': {1: 's', 2: 's', 3: 'v', 4: 's', 5: 's', 7: 'v', 8: 'b', 9: 'b', 10: 'v', 13: 'v', 14: 'v', 15: 's', 17: 'v', 21: 's', 23: 's'},
    # A shell result is a success (exit code 0, not stored) or a failure (field 3 is the exit code).
    'shell_result': {1: ('m', 'shell_success'), 2: ('m', 'shell_failure'), 102: 'v'},
    'shell_success': {1: 's', 2: 's', 5: 's', 7: 'v', 10: 's', 13: 'v'},
    'shell_failure': {1: 's', 2: 's', 3: 'v', 5: 's', 7: 'v', 9: 's', 12: 'v'},
    'glob': {1: ('m', 'glob_args'), 2: ('m', 'glob_result')},
    'glob_args': {1: 's', 2: 's'},
    'glob_result': {1: ('m', 'glob_output')},
    'glob_output': {2: 's'},
    'read': {1: ('m', 'read_args'), 2: ('m', 'read_result')},
    'read_args': {1: 's'},
    'read_result': {1: ('m', 'read_output')},
    'read_output': {1: 's', 4: 'v', 5: 'v', 7: 's', 8: 'b'},
    'write': {1: ('m', 'write_args'), 2: ('m', 'write_result')},
    'write_args': {1: 's', 6: 's'},
    'write_result': {1: ('m', 'write_output')},
    'write_output': {1: 's', 3: 'v', 4: 'v', 5: 's', 6: 's', 7: 's', 8: 's'},
}
BLOB_CLASSES = ('message', 'root', 'turn', 'user_message', 'step', 'file_content')
_MESSAGE_KEYS = frozenset({'role', 'content', 'id', 'providerOptions'})
_PART_KEYS = {
    'text': frozenset({'type', 'text'}),
    'reasoning': frozenset({'type', 'text', 'signature', 'providerOptions'}),
    'tool-call': frozenset({'type', 'toolCallId', 'toolName', 'args'}),
    'tool-result': frozenset({'type', 'toolCallId', 'toolName', 'result', 'experimental_content'}),
}


class CursorStoreError(ValueError):
    """The bytes are not a readable Cursor CLI chat store."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


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


def decode_typed(data, kind):
    """One message as {field number: [values]} under ``SCHEMA[kind]``.

    Raises ValueError for a field, a wire type or a text that the map does not
    hold. A nested message is decoded the same way. A reference is the hex id.
    """
    known, result = SCHEMA[kind], {}
    for number, wire, value, _, _ in parse_message(data):
        expected = known.get(number)
        if expected is None:
            raise ValueError(f'{kind}: unknown field {number}')
        if expected == 'v':
            if wire != 0:
                raise ValueError(f'{kind}.{number}: not a varint')
        elif wire != 2:
            raise ValueError(f'{kind}.{number}: not length-delimited')
        elif expected == 's':
            value = value.decode('utf-8')
        elif expected != 'b' and expected[0] == 'm':
            value = decode_typed(value, expected[1])
        elif expected != 'b':
            if len(value) != 32:
                raise ValueError(f'{kind}.{number}: not a blob reference')
            value = value.hex()
        result.setdefault(number, []).append(value)
    return result


def first(value, *path):
    """The single value at a path of field numbers of a decoded message; None when missing or repeated."""
    for number in path:
        if not isinstance(value, dict):
            return None
        items = value.get(number, [])
        if len(items) != 1:
            return None
        value = items[0]
    return value


def read_store(database):
    """Open a private copy of the store bytes and export every row in rowid order."""
    if not isinstance(database, bytes) or database[:16] != b'SQLite format 3\x00':
        raise CursorStoreError('chat store bytes are missing')
    with tempfile.TemporaryDirectory(prefix='bench-cursor-store-') as directory:
        clone = Path(directory) / 'store.db'
        clone.write_bytes(database)
        try:
            connection = sqlite3.connect(clone)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute('PRAGMA query_only=ON'); connection.execute('PRAGMA trusted_schema=OFF')
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CursorStoreError('chat store fails the SQLite integrity check')
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                schema = [dict(row) for row in connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
                tables = {}
                for row in schema:
                    if row['type'] != 'table':
                        continue
                    name = row['name'].replace('"', '""')
                    rows = [dict(item) for item in connection.execute(f'SELECT rowid AS "__rowid__", * FROM "{name}" ORDER BY rowid LIMIT {_MAX_ROWS + 1}')]
                    if len(rows) > _MAX_ROWS:
                        raise CursorStoreError('chat store table row limit')
                    tables[row['name']] = rows
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise CursorStoreError('chat store is not readable') from error
    return {'user_version': version, 'schema': schema, 'tables': tables, 'physical_sha256': sha(database)}


def row_bytes(row):
    """Logical bytes of one exported row: compact key-sorted JSON; a BLOB counts by its raw length."""
    plain = {key: '' if isinstance(value, bytes) else value for key, value in row.items() if key != '__rowid__'}
    return (len(json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))
            + sum(len(value) for value in row.values() if isinstance(value, bytes)))


def row_proof(row):
    """Stable bytes of one exported row for a locator digest."""
    plain = {key: {'blob_hex': value.hex()} if isinstance(value, bytes) else value for key, value in row.items() if key != '__rowid__'}
    return json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate JSON key')
            value[key] = item
        return value

    def constant(_):
        raise ValueError('non-finite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read_meta(store):
    """The decoded ``meta`` row, or raise CursorStoreError."""
    rows = store['tables'].get('meta', [])
    if len(rows) != 1 or rows[0].get('key') != '0' or not isinstance(rows[0].get('value'), str):
        raise CursorStoreError('chat store meta row must be single')
    try:
        value = _strict_json(bytes.fromhex(rows[0]['value']))
    except (ValueError, UnicodeDecodeError) as error:
        raise CursorStoreError('chat store meta row is not hex JSON') from error
    if not isinstance(value, dict):
        raise CursorStoreError('chat store meta row is not an object')
    return value


def message_exceptions(message):
    """Shape problems of one JSON message blob (a list of short texts; empty when it follows the contract)."""
    problems = []
    if set(message) - _MESSAGE_KEYS:
        problems.append('unknown message key')
    role, content = message.get('role'), message.get('content')
    if role not in ('system', 'user', 'assistant', 'tool') or not isinstance(content, (str, list)):
        return problems + ['unknown message shape']
    if isinstance(content, str):
        return problems if role in ('system', 'user') else problems + ['text content on a part message']
    for part in content:
        kind = part.get('type') if isinstance(part, dict) else None
        allowed = {'user': {'text'}, 'assistant': {'text', 'reasoning', 'tool-call'}, 'tool': {'tool-result'}}.get(role, set())
        if kind not in allowed or set(part) - _PART_KEYS[kind]:
            problems.append('unknown content part')
        elif kind in ('text', 'reasoning') and not isinstance(part.get('text'), str):
            problems.append('part without text')
        elif kind in ('tool-call', 'tool-result') and not (isinstance(part.get('toolCallId'), str) and isinstance(part.get('toolName'), str)):
            problems.append('tool part without id or name')
    return problems


def classify_blobs(store):
    """Name the class of every blob by its place in the graph.

    Returns (blobs, exceptions). ``blobs`` is {id: {'class', 'rowid', 'data',
    'value', 'current'}} in rowid order. ``value`` is the decoded JSON object or
    protobuf message. ``current`` is True for a blob that the current root
    reaches through its message list, its turns and its file states.

    A root is found from ``meta.latestRootBlobId`` and from the checkpoint
    reference of a user message. A root that nothing names is an older
    checkpoint: it is accepted only when it decodes under the root map, holds
    the surface, time and time-zone fields, and all its references resolve.
    """
    exceptions, blobs, failed = [], {}, set()
    for row in store['tables'].get('blobs', []):
        identity, data = row.get('id'), row.get('data')
        where = f"blobs:{row.get('__rowid__')}"
        if not isinstance(identity, str) or not _HEX.fullmatch(identity) or not isinstance(data, bytes) or identity in blobs:
            exceptions.append({'code': 'malformed_blob_row', 'record': where})
            continue
        if len(data) > _MAX_BLOB_BYTES:
            exceptions.append({'code': 'blob_byte_limit', 'record': where})
            continue
        if sha(data) != identity:
            exceptions.append({'code': 'blob_hash_mismatch', 'record': where})
        blobs[identity] = {'class': None, 'rowid': row['__rowid__'], 'data': data, 'value': None, 'current': False}

    def assign(identity, kind, source):
        """Give a class to a referenced blob; False when it is missing, of another class or does not decode."""
        entry = blobs.get(identity)
        if entry is None:
            exceptions.append({'code': 'dangling_blob_reference', 'record': source, 'target': identity})
            return False
        if entry['class'] is not None or identity in failed:
            if entry['class'] != kind and identity not in failed:
                exceptions.append({'code': 'blob_class_conflict', 'record': f"blobs:{entry['rowid']}"})
                return False
            return None
        if kind == 'file_content':
            entry['class'] = kind
            return True
        try:
            if kind == 'message':
                value = _strict_json(entry['data'])
                if not isinstance(value, dict):
                    raise ValueError('message is not an object')
                problems = message_exceptions(value)
                if problems:
                    exceptions.append({'code': 'unknown_message_shape', 'record': f"blobs:{entry['rowid']}", 'detail': problems})
            else:
                value = decode_typed(entry['data'], kind)
        except (ValueError, UnicodeDecodeError, RecursionError) as error:
            exceptions.append({'code': 'undecodable_blob', 'record': f"blobs:{entry['rowid']}", 'class': kind, 'detail': str(error)})
            failed.add(identity)
            return False
        entry['class'], entry['value'] = kind, value
        return True

    def walk_root(identity, source):
        if assign(identity, 'root', source) is not True:
            return
        root = blobs[identity]['value']
        where = f"blobs:{blobs[identity]['rowid']}"
        for reference in root.get(1, []):
            assign(reference, 'message', where)
        for state in root.get(15, []):
            for reference in (first(state, 2, 1), first(state, 2, 2)):
                if reference is not None:
                    assign(reference, 'file_content', where)
        for reference in root.get(8, []):
            if assign(reference, 'turn', where) is not True:
                continue
            body = first(blobs[reference]['value'], 1) or {}
            turn_where = f"blobs:{blobs[reference]['rowid']}"
            for user in body.get(1, []):
                if assign(user, 'user_message', turn_where) is True:
                    for checkpoint in blobs[user]['value'].get(10, []):
                        walk_root(checkpoint, f"blobs:{blobs[user]['rowid']}")
            for step in body.get(2, []):
                assign(step, 'step', turn_where)

    try:
        meta = read_meta(store)
    except CursorStoreError as error:
        return blobs, exceptions + [{'code': 'meta_unreadable', 'detail': str(error)}]
    latest = meta.get('latestRootBlobId')
    if not isinstance(latest, str) or latest not in blobs:
        exceptions.append({'code': 'latest_root_missing'})
    else:
        walk_root(latest, 'meta:latestRootBlobId')
    # Older roots that nothing names. Take them in rowid order.
    for identity, entry in blobs.items():
        if entry['class'] is not None or identity in failed:
            continue
        try:
            candidate = decode_typed(entry['data'], 'root')
        except (ValueError, UnicodeDecodeError, RecursionError):
            continue
        if all(len(candidate.get(number, [])) == 1 for number in (22, 26, 27)):
            walk_root(identity, 'unreferenced checkpoint')
    for identity, entry in blobs.items():
        if entry['class'] is None and identity not in failed:
            exceptions.append({'code': 'unclassified_blob', 'record': f"blobs:{entry['rowid']}"})
    # What the current root reaches (the checkpoint reference of a user message is not followed).
    if isinstance(latest, str) and blobs.get(latest, {}).get('class') == 'root':
        blobs[latest]['current'] = True
        root = blobs[latest]['value']
        reached = list(root.get(1, [])) + [ref for state in root.get(15, []) for ref in (first(state, 2, 1), first(state, 2, 2))]
        for reference in root.get(8, []):
            reached.append(reference)
            body = first(blobs.get(reference, {}).get('value'), 1) or {}
            reached += body.get(1, []) + body.get(2, [])
        for reference in reached:
            if reference in blobs:
                blobs[reference]['current'] = True
    return blobs, exceptions


def contract_exceptions(store, sidecar, session_id):
    """What in the store or its sidecar is outside the decoder contract (empty when it follows it).

    ``sidecar`` is the bytes of ``meta.json``. The contract is bound to the two
    declared versions: ``PRAGMA user_version`` of the store and
    ``schemaVersion`` of the sidecar. Any other value is refused.
    """
    exceptions = []
    if store.get('user_version') != USER_VERSION:
        exceptions.append({'code': 'unsupported_user_version', 'value': store.get('user_version')})
    tables = {row['name'] for row in store.get('schema', []) if row['type'] == 'table'}
    if tables != set(TABLES):
        exceptions.append({'code': 'unknown_tables', 'tables': sorted(tables ^ set(TABLES))})
    for name, columns in TABLES.items():
        rows = store['tables'].get(name, [])
        if rows and [key for key in rows[0] if key != '__rowid__'] != columns:
            exceptions.append({'code': 'unknown_columns', 'table': name})
    try:
        meta = read_meta(store)
        if set(meta) - META_KEYS:
            exceptions.append({'code': 'unknown_meta_key', 'keys': sorted(set(meta) - META_KEYS)})
        if meta.get('agentId') != session_id:
            exceptions.append({'code': 'session_identity_mismatch'})
    except CursorStoreError as error:
        exceptions.append({'code': 'meta_unreadable', 'detail': str(error)})
    try:
        document = _strict_json(sidecar)
        if not isinstance(document, dict) or type(document.get('schemaVersion')) is not int:
            exceptions.append({'code': 'sidecar_without_schema_version'})
        else:
            if document['schemaVersion'] != SIDECAR_SCHEMA_VERSION:
                exceptions.append({'code': 'unsupported_sidecar_schema_version', 'value': document['schemaVersion']})
            if set(document) - SIDECAR_KEYS:
                exceptions.append({'code': 'unknown_sidecar_key', 'keys': sorted(set(document) - SIDECAR_KEYS)})
    except (ValueError, UnicodeDecodeError, TypeError):
        exceptions.append({'code': 'sidecar_unreadable'})
    return exceptions


def remove_blobs(database, identities):
    """The store bytes without the named blob rows (selected-loss control). Works on a private copy."""
    with tempfile.TemporaryDirectory(prefix='bench-cursor-loss-') as directory:
        clone = Path(directory) / 'store.db'
        clone.write_bytes(database)
        connection = sqlite3.connect(clone)
        try:
            connection.execute('PRAGMA journal_mode=DELETE')
            for identity in identities:
                if connection.execute('DELETE FROM blobs WHERE id=?', (identity,)).rowcount != 1:
                    raise CursorStoreError('blob row to remove is missing')
            connection.commit()
        finally:
            connection.close()
        return clone.read_bytes()


def _encode_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def encode(fields):
    """Encode [(number, value)] where a value is int, bytes, str or a nested list (tests and the sanitizer use it)."""
    out = bytearray()
    for number, value in fields:
        if isinstance(value, int):
            out += _encode_varint(number << 3) + _encode_varint(value)
        else:
            body = encode(value) if isinstance(value, list) else value.encode('utf-8') if isinstance(value, str) else value
            out += _encode_varint(number << 3 | 2) + _encode_varint(len(body)) + body
    return bytes(out)
