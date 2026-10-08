"""Read the Copilot session store (``session-store.db`` + ``session-store.db-wal``).

The store is a WAL-mode SQLite database in COPILOT_HOME. In the captured runs
the main file is one empty page and every row lives in the WAL. The two files
are never opened in place: they are copied to a new temporary directory and
the copy is opened. SQLite rebuilds the missing ``-shm`` index there.

``assistant_usage_events`` holds one row per model call: ``session_id``,
``turn_index`` (the index of the user turn), ``model``, ``input_tokens``,
``output_tokens``, ``cache_read_tokens``, ``cache_write_tokens``,
``reasoning_tokens``, ``finish_reason``, ``token_details_json``. A row has no
event id. The join to ``events.jsonl`` is structural: the rows of one
``turn_index``, in ``id`` order, are the ``assistant.message`` events of the
user turn with that index, in log order. The join is made only when the counts
are equal, the position equals ``assistant.message.data.turnId``, the models
are equal, and ``finish_reason`` agrees with the message (``tool_calls`` with
tool requests, ``stop`` without). Timestamps are not a join key.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile

STORE_DB = 'session-store.db'
STORE_WAL = 'session-store.db-wal'
STORE_FILES = (STORE_DB, STORE_WAL)
STORE_SCHEMA_VERSION = 8
USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens', 'reasoning_tokens')
USAGE_COLUMNS = {'id', 'session_id', 'turn_index', 'model', 'finish_reason', 'token_details_json', 'total_nano_aiu', 'created_at', *USAGE_FIELDS}
_MAX_ROWS = 100_000
_WAL_MAGIC = (0x377f0682, 0x377f0683)


class SessionStoreError(ValueError):
    """The store files are not a readable WAL-mode SQLite database."""


def _wal_checksum(data, s0, s1, big_endian):
    for value_a, value_b in struct.iter_unpack('>II' if big_endian else '<II', data):
        s0 = (s0 + value_a + s1) & 0xffffffff
        s1 = (s1 + value_b + s0) & 0xffffffff
    return s0, s1


def wal_valid_frames(wal):
    """Offsets of the frames a SQLite reader accepts, and the checksum byte order.

    A frame is valid when its salts equal the header salts and its cumulative
    checksum is right. The first bad frame ends the log, as in SQLite.
    """
    if len(wal) < 32:
        return [], 0, False
    magic, _, page_size, _, salt1, salt2, check1, check2 = struct.unpack('>8I', wal[:32])
    if magic not in _WAL_MAGIC or page_size < 512 or page_size & (page_size - 1):
        raise SessionStoreError('session store WAL header is not valid')
    big_endian = magic == _WAL_MAGIC[1]
    s0, s1 = _wal_checksum(wal[:24], 0, 0, big_endian)
    if (s0, s1) != (check1, check2):
        raise SessionStoreError('session store WAL header checksum is not valid')
    offsets, position = [], 32
    while position + 24 + page_size <= len(wal):
        header = wal[position:position + 24]
        if struct.unpack('>II', header[8:16]) != (salt1, salt2):
            break
        s0, s1 = _wal_checksum(header[:8] + wal[position + 24:position + 24 + page_size], s0, s1, big_endian)
        if (s0, s1) != struct.unpack('>II', header[16:24]):
            break
        offsets.append(position)
        position += 24 + page_size
    return offsets, page_size, big_endian


def rewrite_wal_checksums(wal, frame_count):
    """Return the WAL with the checksums of its first ``frame_count`` frames recomputed.

    Used after an equal-length change of page bytes. The header and the salts
    stay. Bytes after those frames are not touched.
    """
    magic, _, page_size = struct.unpack('>3I', wal[:12])
    big_endian = magic == _WAL_MAGIC[1]
    out = bytearray(wal)
    s0, s1 = _wal_checksum(wal[:24], 0, 0, big_endian)
    position = 32
    for _ in range(frame_count):
        s0, s1 = _wal_checksum(bytes(out[position:position + 8]) + bytes(out[position + 24:position + 24 + page_size]), s0, s1, big_endian)
        out[position + 16:position + 24] = struct.pack('>II', s0, s1)
        position += 24 + page_size
    return bytes(out)


def read_session_store(database, wal):
    """Open a private copy of the two files and export every row.

    Returns ``schema_version``, ``schema`` (the ``sqlite_master`` rows) and
    ``tables`` ({name: rows}). Virtual tables are not read; their shadow
    tables are. Rows are in ``rowid`` order where a table has one.
    """
    if not isinstance(database, bytes) or not isinstance(wal, bytes):
        raise SessionStoreError('session store bytes are missing')
    frames, _, _ = wal_valid_frames(wal)
    if not frames:
        raise SessionStoreError('session store WAL holds no valid frame')
    with tempfile.TemporaryDirectory(prefix='bench-copilot-store-') as directory:
        clone = Path(directory)
        (clone / STORE_DB).write_bytes(database)
        (clone / STORE_WAL).write_bytes(wal)
        try:
            connection = sqlite3.connect(clone / STORE_DB)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute('PRAGMA query_only=ON'); connection.execute('PRAGMA trusted_schema=OFF')
                if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise SessionStoreError('session store fails the SQLite integrity check')
                schema = [dict(row) for row in connection.execute('SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name')]
                tables = {}
                for row in schema:
                    if row['type'] != 'table' or (row['sql'] or '').upper().startswith('CREATE VIRTUAL'):
                        continue
                    name = row['name'].replace('"', '""')
                    without_rowid = 'WITHOUT ROWID' in (row['sql'] or '').upper()
                    rows = [dict(item) for item in connection.execute(
                        f'SELECT * FROM "{name}"' + ('' if without_rowid else ' ORDER BY rowid') + f' LIMIT {_MAX_ROWS + 1}')]
                    if len(rows) > _MAX_ROWS:
                        raise SessionStoreError('session store table row limit')
                    tables[row['name']] = rows
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise SessionStoreError('session store is not readable') from error
    versions = [row.get('version') for row in tables.get('schema_version', [])]
    return {'schema_version': versions[0] if len(versions) == 1 and type(versions[0]) is int else None,
            'schema': schema, 'tables': tables, 'wal_valid_frames': len(frames),
            'physical_sha256': {STORE_DB: hashlib.sha256(database).hexdigest(), STORE_WAL: hashlib.sha256(wal).hexdigest()}}


def read_session_store_files(native):
    native = Path(native)
    paths = [native / name for name in STORE_FILES]
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise SessionStoreError('session store files are missing')
    return read_session_store(paths[0].read_bytes(), paths[1].read_bytes())


def store_row_bytes(row):
    """Logical bytes of one exported row: compact key-sorted JSON; a BLOB counts by its raw length."""
    plain = {key: '' if isinstance(value, bytes) else value for key, value in row.items()}
    return (len(json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))
            + sum(len(value) for value in row.values() if isinstance(value, bytes)))


def store_row_proof(row):
    """Stable bytes of one exported row for a locator digest."""
    plain = {key: {'blob_hex': value.hex()} if isinstance(value, bytes) else value for key, value in row.items()}
    return json.dumps(plain, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def store_contract_exceptions(store, session_id):
    """Reasons why the store does not match the decoder contract (empty when it does)."""
    exceptions = []
    if store['schema_version'] != STORE_SCHEMA_VERSION:
        exceptions.append({'code': 'unsupported_session_store_schema_version', 'record': 'session-store.db:schema_version'})
    tables = store['tables']
    for name in ('sessions', 'turns', 'assistant_usage_events'):
        if name not in tables:
            exceptions.append({'code': 'session_store_table_missing', 'record': f'session-store.db:{name}'})
    if any(not USAGE_COLUMNS <= set(row) for row in tables.get('assistant_usage_events', [])):
        exceptions.append({'code': 'session_store_usage_columns_missing', 'record': 'session-store.db:assistant_usage_events'})
    if [row.get('id') for row in tables.get('sessions', [])] != [session_id]:
        exceptions.append({'code': 'session_store_holds_another_session', 'record': 'session-store.db:sessions'})
    for name in ('turns', 'assistant_usage_events', 'session_files', 'session_refs', 'checkpoints', 'forge_trajectory_events'):
        if any(row.get('session_id') != session_id for row in tables.get(name, [])):
            exceptions.append({'code': 'session_store_holds_another_session', 'record': f'session-store.db:{name}'})
    return exceptions


def row_holds_text(row, texts):
    """True when a text cell of a store row holds the complete text of one of ``texts``."""
    cells = [value for value in row.values() if isinstance(value, str)]
    return any(isinstance(text, str) and text and any(text in cell for cell in cells) for text in texts)


def _usage(row):
    """The named counts of one row. An integer 0 stays 0 and a NULL stays None."""
    return {key: row.get(key) for key in USAGE_FIELDS}


def attach_store_usage(decoded, store):
    """Join usage rows to ``assistant.message`` events and reconcile them with ``session.shutdown``.

    Adds ``usage_records`` (every joined model call), a ``usage`` object on each
    joined visible response, and one ``reconciliation`` verdict. Nothing is
    added for a turn whose rows do not join by the structural rule.
    """
    session = decoded.get('session_id')
    exceptions = store_contract_exceptions(store, session)
    decoded['session_store'] = {'read': True, 'schema_version': store['schema_version'], 'exceptions': exceptions,
                                'usage_row_count': len(store['tables'].get('assistant_usage_events', [])),
                                'physical_sha256': store['physical_sha256']}
    decoded['usage_records'] = []
    if exceptions:
        return
    rows = sorted(store['tables']['assistant_usage_events'], key=lambda row: row['id'])
    # assistant.message events grouped by the user turn before them.
    groups, turn = [], None
    for record in decoded['records']:
        raw = record['raw']
        if record['locator'].get('artifact') != 'events.jsonl':
            continue
        if raw.get('type') == 'user.message':
            turn = {'id': raw.get('id'), 'messages': []}; groups.append(turn)
        elif raw.get('type') == 'assistant.message' and turn is not None:
            turn['messages'].append(record)
    cache = {item.get('response_id'): item for item in decoded.get('prompt_cache_fragments', [])}
    responses = {row['id']: row for row in decoded['responses']}
    joined_ids, line_of = set(), {}
    for index, group in enumerate(groups):
        candidates = [row for row in rows if row['turn_index'] == index]
        messages = group['messages']
        consistent = len(candidates) == len(messages) and bool(candidates)
        for position, (row, record) in enumerate(zip(candidates, messages)):
            data = record['raw'].get('data', {})
            expected_finish = 'tool_calls' if data.get('toolRequests') else 'stop'
            if (str(data.get('turnId')) != str(position) or data.get('model') != row['model']
                    or row['finish_reason'] != expected_finish):
                consistent = False
        if not consistent:
            continue
        for position, (row, record) in enumerate(zip(candidates, messages)):
            event = record['raw'].get('id')
            usage = _usage(row)
            fragment = cache.get(event, {}).get('prompt_cache_state')
            try:
                details = json.loads(row['token_details_json']) if isinstance(row['token_details_json'], str) else None
            except ValueError:
                details = None
            decoded['usage_records'].append({
                'id': f'session-store:assistant_usage_events:{row["id"]}', 'response_event_id': event, 'turn_id': group['id'],
                'turn_index': index, 'position': position, 'model_id': row['model'], 'finish_reason': row['finish_reason'],
                'usage': usage, 'total_nano_aiu': row['total_nano_aiu'], 'token_details': details, 'created_at': row['created_at'],
                'join': 'session_id + turn_index + id order = user turn index + assistant.message order',
                # Second native witness for the last call of a process only.
                'prompt_cache_state_equal': None if fragment is None else (
                    fragment == {'prompt_tokens': usage['input_tokens'], 'cache_read': usage['cache_read_tokens'], 'cache_write': usage['cache_write_tokens']}),
                'locator': {'artifact': STORE_DB, 'table': 'assistant_usage_events', 'row_id': row['id']},
                'response_locator': record['locator']})
            joined_ids.add(row['id']); line_of[row['id']] = record['locator']['line']
            # A second native witness that disagrees with the row refuses the usage.
            if decoded['usage_records'][-1]['prompt_cache_state_equal'] is False:
                continue
            if event in responses and responses[event].get('model_id') == row['model']:
                responses[event]['usage'] = usage
                responses[event]['usage_id'] = f'session-store:assistant_usage_events:{row["id"]}'
    # The store also keeps a copy of a finished turn (``turns``). The same
    # turn index and the same text prove the copy. Usage is scored from the
    # store, so the store is inside the read and the copy is an occurrence.
    copies = []
    for row in store['tables'].get('turns', []):
        index = row.get('turn_index')
        if type(index) is not int or not 0 <= index < len(groups):
            continue
        turn_event = groups[index]['id']
        user = next((item for item in decoded['turns'] if item['id'] == turn_event), None)
        if user is not None and row.get('user_message') == user.get('text'):
            copies.append({'event_id': turn_event, 'occurrence': f'session-store.db:turns:row-{row["id"]}:user_message'})
        for response in decoded['responses']:
            if response.get('turn_id') == turn_event and row.get('assistant_response') == response.get('text'):
                copies.append({'event_id': response['id'], 'occurrence': f'session-store.db:turns:row-{row["id"]}:assistant_response'})
    decoded['session_store']['turn_copies'] = copies
    # A cell of another table that holds the complete text of a message restates it:
    # the search index content holds the prompt and the response of the turn.
    messages = [(item['id'], item.get('text')) for item in decoded['turns']] + [(item['id'], item.get('text')) for item in decoded['responses']]
    cells = []
    for table in sorted(store['tables']):
        if table == 'turns':
            continue
        for number, row in enumerate(store['tables'][table]):
            cells += [{'event_id': event, 'occurrence': f'session-store.db:{table}:row-{number}:{event}'}
                      for event, text in messages if row_holds_text(row, [text])]
    decoded['session_store']['cell_copies'] = cells
    # Reconciliation: every row of the session against the last declared total.
    totals = decoded.get('session_totals', [])
    if not totals or len(joined_ids) != len(rows) or not rows:
        return
    checks = []

    def compare(label, declared, summed):
        checks.append({'field': label, 'declared': declared, 'sum_of_records': summed, 'equal': type(declared) in (int, float) and declared == summed})

    def reconcile(total, selected, label):
        metrics = total.get('model_metrics') if isinstance(total.get('model_metrics'), dict) else {}
        models = sorted({row['model'] for row in selected})
        if sorted(metrics) != models:
            checks.append({'field': f'{label}:models', 'declared': sorted(metrics), 'sum_of_records': models, 'equal': False})
            return
        for model in models:
            own = [row for row in selected if row['model'] == model]
            declared = metrics[model].get('usage', {}) if isinstance(metrics[model], dict) else {}
            compare(f'{label}:{model}:requests.count', (metrics[model].get('requests') or {}).get('count'), len(own))
            for native_name, column in (('inputTokens', 'input_tokens'), ('outputTokens', 'output_tokens'), ('cacheReadTokens', 'cache_read_tokens'),
                                        ('cacheWriteTokens', 'cache_write_tokens'), ('reasoningTokens', 'reasoning_tokens')):
                values = [row[column] for row in own]
                compare(f'{label}:{model}:usage.{native_name}', declared.get(native_name),
                        sum(values) if all(type(value) is int for value in values) else None)
            nano = [row['total_nano_aiu'] for row in own]
            compare(f'{label}:{model}:totalNanoAiu', metrics[model].get('totalNanoAiu'), sum(nano) if all(type(value) is int for value in nano) else None)
        details = total.get('token_details') if isinstance(total.get('token_details'), dict) else {}
        billed, readable = {}, True
        for row in selected:
            try:
                items = json.loads(row['token_details_json'])
            except (TypeError, ValueError):
                items = None
            if not isinstance(items, list) or any(not isinstance(item, dict) or type(item.get('tokenCount')) is not int for item in items):
                readable = False
                continue
            for item in items:
                billed[item.get('tokenType')] = billed.get(item.get('tokenType'), 0) + item['tokenCount']
        for kind in sorted(set(details) | set(billed), key=str):
            declared = details.get(kind)
            compare(f'{label}:tokenDetails.{kind}.tokenCount', declared.get('tokenCount') if isinstance(declared, dict) else None,
                    billed.get(kind) if readable else None)

    # A resumed session writes one cumulative total per process. Each total
    # covers the model calls logged before it.
    for number, total in enumerate(totals):
        last = number == len(totals) - 1
        selected = [row for row in rows if line_of[row['id']] < total['sequence']]
        if last and len(selected) != len(rows):
            checks.append({'field': 'session.shutdown[last]:covers_all_records', 'declared': len(selected), 'sum_of_records': len(rows), 'equal': False})
        reconcile(total, selected, 'session.shutdown[last]' if last else f'session.shutdown[{number}]')
    decoded['reconciliation'] = [{'id': 'session-store-usage-to-session-shutdown', 'matches_session_totals': all(item['equal'] for item in checks),
                                  'declared_total_locator': totals[-1]['locator'], 'record_count': len(rows), 'checks': checks}]
