"""Whole Hermes home capture: every changed entry is classified, only the rows of the test session leave the store."""
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from session_bench import hermes_state_evidence as evidence
from session_bench.hermes_state_evidence import (
    HermesStateError, RULES, capture_hermes_state, carries_session_id, check_rows_name_only_session,
    classify_hermes_changes, export_session_rows, inventory_hermes_home, refusal_listing, rules_table,
    verify_hermes_state_capture,
)

SID = '20261006_101500_abc123'
OTHER = '20260901_090000_fed456'
STORE = ('state.db', 'state.db-wal', 'state.db-shm')
SHARED = ('.hermes_history', 'gateway_state.json', 'logs/agent.log', 'kanban.db', 'memories/MEMORY.md', 'sessions/sessions.json')
PRIVATE = ('auth.json', '.env', 'config.yaml', f'sessions/session_{OTHER}.json')
SCHEMA = '''
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, model TEXT, started_at REAL);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
                       role TEXT, content TEXT, payload BLOB);
CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, message_id INTEGER, name TEXT);
CREATE TABLE attachments (id INTEGER PRIMARY KEY, msg INTEGER REFERENCES messages(id), data BLOB);
CREATE TABLE platform_events (id INTEGER PRIMARY KEY, message_id INTEGER, note TEXT);
CREATE TABLE schema_version (version INTEGER);
'''


def put(root, name, data=b'x'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def add_session(connection, session_id, texts, first_message):
    """One session with messages, a tool call per message (undeclared key) and an attachment (declared key)."""
    connection.execute('INSERT INTO sessions VALUES (?, ?, NULL, ?, ?)', (session_id, 'cli', 'gpt-5.5', 1.5))
    for offset, text in enumerate(texts):
        number = first_message + offset
        connection.execute('INSERT INTO messages VALUES (?, ?, ?, ?, ?)', (number, session_id, 'user', text, b'\x00\xff' + text.encode()))
        connection.execute('INSERT INTO tool_calls (message_id, name) VALUES (?, ?)', (number, 'terminal ' + text))
        connection.execute('INSERT INTO attachments (msg, data) VALUES (?, ?)', (number, text.encode()))
    connection.commit()


def home(tmp_path):
    """A Hermes home with the operator's older sessions, shared stores, an install checkout and a cache.

    Returns the root and the open writer connection of ``state.db``. The
    connection stays open, so the ``-wal`` and ``-shm`` files stay on disk.
    """
    root = tmp_path / 'hermes-home'
    for name in SHARED + PRIVATE:
        put(root, name, b'private ' + name.encode())
    put(root, '.clean_shutdown', b'')
    put(root, 'hermes-agent/hermes_cli/main.py', b'# install\n')
    put(root, 'hermes-agent/.git/HEAD', b'ref: refs/heads/main\n')
    put(root, 'cache/models.json', b'{}')
    (root / 'bin').mkdir()
    (root / 'bin/hermes').symlink_to('../hermes-agent/hermes')
    (root / 'pastes').mkdir()
    connection = sqlite3.connect(root / 'state.db')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.executescript(SCHEMA)
    connection.execute('PRAGMA user_version=6')
    connection.execute('INSERT INTO schema_version VALUES (6)')
    add_session(connection, OTHER, ['other private one', 'other private two'], 1)
    # A column named like a key of messages that is not one: 900 has no message.
    connection.execute("INSERT INTO platform_events (message_id, note) VALUES (900, 'other private platform note')")
    connection.execute("INSERT INTO platform_events (message_id, note) VALUES (10, 'private note with a colliding number')")
    connection.commit()
    assert all((root / name).is_file() for name in STORE)
    return root, connection


def turn(root, connection, *, texts=('SB prompt one',), first_message=10, log='logs/run-20261006.log'):
    """What one Hermes turn writes: session files, a new log, rows in the shared store, changed shared files."""
    put(root, f'sessions/session_{SID}.json', json.dumps({'id': SID, 'n': len(texts)}).encode())
    put(root, f'sessions/{SID}.jsonl', b'{"role":"user"}\n' * len(texts))
    put(root, log, b'run log with personal data')
    for name in SHARED:
        put(root, name, b'changed during the run ' + str(texts).encode())
    put(root, 'kanban.db-wal', b'wal')
    put(root, 'cache/models.json', b'{"changed": true}')
    put(root, 'cache/new-entry.json', b'{}')
    (root / '.clean_shutdown').unlink(missing_ok=True)
    put(root, 'auth.lock', b'')
    if connection.execute('SELECT 1 FROM sessions WHERE id = ?', (SID,)).fetchone():
        for offset, text in enumerate(texts):
            number = first_message + offset
            connection.execute('INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?)', (number, SID, 'user', text, b'\x00\xff'))
        connection.commit()
    else:
        add_session(connection, SID, list(texts), first_message)


def capture(root, destination, before, started, **options):
    options.setdefault('session_id', SID)
    options.setdefault('sleep', lambda _: None)
    return capture_hermes_state(root, destination, before, attempt_id='hermes-test', turn=options.pop('turn', 1),
                                started_ns=started, build='Hermes Agent v0-test', **options)


def bracket(tmp_path):
    root, connection = home(tmp_path)
    detail = {}
    before = inventory_hermes_home(root, detail=detail)
    started = time.time_ns()
    return root, connection, before, detail, started


def test_inventory_covers_the_whole_home_records_links_and_digests_large_subtrees(tmp_path):
    root, _connection = home(tmp_path)
    detail = {}
    entries = {row['relative_path']: row for row in inventory_hermes_home(root, detail=detail)['entries']}
    assert {'state.db', 'state.db-wal', 'state.db-shm', 'sessions', f'sessions/session_{OTHER}.json', 'auth.json', '.env',
            'bin/hermes', 'hermes-agent', 'cache', 'pastes'} <= set(entries)
    link = entries['bin/hermes']
    assert link['kind'] == 'symlink' and link['target_sha256'] == hashlib.sha256(b'../hermes-agent/hermes').hexdigest() and 'target' not in link
    # The install checkout is one entry, but every entry inside is counted and digested.
    install = entries['hermes-agent']
    assert install['kind'] == 'directory-digest' and install['entry_count'] == 4
    assert set(install['directory_sha256']) == {'hermes-agent', 'hermes-agent/.git', 'hermes-agent/hermes_cli'}
    assert not [name for name in entries if name.startswith('hermes-agent/')]
    assert set(detail['hermes-agent']) == {'hermes-agent/.git', 'hermes-agent/.git/HEAD', 'hermes-agent/hermes_cli', 'hermes-agent/hermes_cli/main.py'}
    # A link to a directory is one entry. Its target is not walked.
    (root / 'outside').symlink_to(tmp_path)
    assert not [row for row in inventory_hermes_home(root)['entries'] if row['relative_path'].startswith('outside/')]


def test_session_id_must_be_bounded_inside_a_path_component():
    assert carries_session_id(f'sessions/session_{SID}.json', SID) and carries_session_id(f'checkpoints/{SID}/a', SID)
    assert carries_session_id(f'sessions/{SID}.jsonl', SID)
    assert not carries_session_id(f'sessions/{SID}9.jsonl', SID) and not carries_session_id(f'sessions/x{SID}.jsonl', SID)


def test_every_changed_entry_is_classified_and_owned_files_are_copied(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    assert receipt['schema_version'] == 'hermes-state-root-v1' and receipt['scope'] == 'whole_hermes_home'
    assert receipt['session_id'] == SID and receipt['turn'] == 1 and receipt['build'] == 'Hermes Agent v0-test'
    assert receipt['metadata_only_before_after'] is True and receipt['preexisting_contents_opened'] is False
    assert receipt['shared_store_rows_read'] == 'this session only'
    classes = receipt['classes']
    assert set(classes['session_owned']) == {f'sessions/session_{SID}.json', f'sessions/{SID}.jsonl'}
    assert classes['run_owned'] == ['logs/run-20261006.log']
    shared = {row['relative_path']: row for row in classes['shared_changed']}
    # In WAL mode the new rows are in state.db-wal; state.db itself and its SHM file may stay as they were.
    assert set(shared) - {'state.db', 'state.db-shm'} == set(SHARED) | {'state.db-wal', 'kanban.db-wal', 'cache', '.clean_shutdown', 'auth.lock'}
    assert all(row['content_read'] is False for row in shared.values())
    assert shared['kanban.db-wal']['change'] == 'created_sidecar' and shared['.clean_shutdown']['change'] == 'removed'
    assert shared['auth.lock']['change'] == 'created' and shared['sessions/sessions.json']['change'] == 'modified'
    # A change inside a digested subtree is reported by path.
    assert shared['cache']['kind'] == 'directory-digest' and shared['cache']['changed_directories'] == ['cache']
    assert shared['cache']['changed_paths'] == ['cache/models.json', 'cache/new-entry.json']
    assert 'hermes-agent' not in shared
    accounting = receipt['accounting']
    assert accounting['every_changed_entry_accounted'] is True and accounting['unexplained'] == []
    assert accounting['classes_found'] == ['session_owned', 'run_owned', 'shared_changed']
    assert accounting['shared_entries_changed_content_not_read'] == sorted(shared)
    assert 'does not prove that it holds none' in accounting['statement']
    artifacts = {row['relative_path']: row for row in receipt['artifacts']}
    assert set(artifacts) == set(classes['session_owned']) | set(classes['run_owned'])
    for name, row in artifacts.items():
        owned = name in classes['session_owned']
        assert row['class'] == ('session_owned' if owned else 'run_owned') and row['private_only'] is (not owned)
        data = (tmp_path / 'r1-native' / row['copied_path']).read_bytes()
        assert data == (root / name).read_bytes() and row['sha256'] == hashlib.sha256(data).hexdigest() and row['size_bytes'] == len(data)
        assert row['filesystem_id'] == f'{(root / name).lstat().st_dev}:{(root / name).lstat().st_ino}'
    assert receipt['rules'] == rules_table() and receipt['after_inventory_sha256'] == evidence.inventory_sha256(after)
    assert verify_hermes_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True


def test_session_rows_are_exported_from_a_deleted_copy_and_no_row_of_another_session_leaves(tmp_path, monkeypatch):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection, texts=('SB prompt one', 'SB prompt two'))
    connected = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(evidence.sqlite3, 'connect', lambda target, **options: connected.append((target, options)) or real_connect(target, **options))
    receipt, _after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    monkeypatch.undo()
    store = receipt['session_store']
    # Only the private copy was opened with SQLite, read-only, and it is gone.
    temporary = tmp_path / '.r1-native-store-copy'
    assert len(connected) == 1 and str(temporary) in connected[0][0] and connected[0][0].endswith('?mode=ro') and connected[0][1] == {'uri': True}
    assert str(root) not in connected[0][0] and not temporary.exists()
    assert store['temporary_copy'] == {'directory': '.r1-native-store-copy', 'deleted': True}
    summary = store['stores'][0]
    # The database, its WAL and its SHM file travel together. The new rows are only in the WAL here.
    assert b'SB prompt one' not in (root / 'state.db').read_bytes() and b'SB prompt one' in (root / 'state.db-wal').read_bytes()
    assert [row['relative_path'] for row in summary['copied_files']] == list(STORE)
    for row in summary['copied_files']:
        info = (root / row['relative_path']).lstat()
        assert row['size_bytes'] == info.st_size and row['filesystem_id'] == f'{info.st_dev}:{info.st_ino}' and len(row['sha256']) == 64
    assert summary['user_version'] == 6 and len(summary['schema_sha256']) == 64
    counts = {row['table']: row for row in summary['tables']}
    assert {name: (row['selected_rows'], row['total_rows']) for name, row in counts.items()} == {
        'sessions': (1, 2), 'messages': (2, 4), 'tool_calls': (2, 4), 'attachments': (2, 4), 'platform_events': (0, 2), 'schema_version': (0, 1)}
    assert counts['sessions']['selected_by'] == ['exact:id']
    # messages rows hold the id themselves and also reference the sessions row by a declared key.
    assert counts['messages']['selected_by'] == ['exact:session_id', 'join:session_id->sessions.id']
    assert counts['messages']['other_rows_counted_not_read'] == 2
    assert counts['messages']['rows_with_exact_match'] == counts['messages']['rows_that_contain_the_id'] == 2
    assert counts['tool_calls']['rows_with_exact_match'] == 0 and counts['platform_events']['rows_that_contain_the_id'] == 0
    joins = {(row['table'], row['column']): row for row in summary['joins']}
    assert joins[('attachments', 'msg')]['basis'] == 'declared foreign key' and joins[('attachments', 'msg')]['references'] == 'messages.id'
    assert joins[('tool_calls', 'message_id')]['basis'] == 'column name' and joins[('tool_calls', 'message_id')]['orphan_values_in_whole_table'] == 0
    # A column that only looks like a key is not followed; its matching row is counted, not exported.
    assert summary['join_candidates_not_exported'] == [{
        'table': 'platform_events', 'column': 'message_id', 'references': 'messages.id', 'basis': 'column name',
        'orphan_values_in_whole_table': 1, 'matching_rows_not_exported': 1, 'reason': 'the column does not behave as a key of the referenced table'}]
    raw = (tmp_path / 'r1-native/session-store-rows.json').read_bytes()
    assert store['rows_export'] == {'path': 'session-store-rows.json', 'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    assert OTHER.encode() not in raw and b'other private' not in raw and b'private note' not in raw
    export = json.loads(raw)
    assert export['schema_version'] == 'session-bench-hermes-session-store-rows-v1' and export['session_id'] == SID
    tables = {table['table']: table for table in export['stores'][0]['tables']}
    assert set(tables) == {'sessions', 'messages', 'tool_calls', 'attachments'}
    messages = tables['messages']['rows']
    assert [row['rowid'] for row in messages] == [10, 11] and 'exact:session_id' in messages[0]['selected_by']
    # Lossless: column name to value, BLOB as hex, keys kept.
    assert messages[0]['values'] == {'id': 10, 'session_id': SID, 'role': 'user', 'content': 'SB prompt one',
                                     'payload': {'blob_hex': (b'\x00\xff' + b'SB prompt one').hex()}}
    assert tables['sessions']['rows'][0]['values'] == {'id': SID, 'source': 'cli', 'parent_session_id': None, 'model': 'gpt-5.5', 'started_at': 1.5}
    assert tables['attachments']['rows'][0]['selected_by'] == ['join:msg->messages.id'] and tables['messages']['primary_key'] == ['id']
    schema = json.loads((tmp_path / 'r1-native/session-store-schema.json').read_bytes())['stores'][0]
    assert schema['user_version'] == 6 and {row['name'] for row in schema['tables']} >= {'sessions', 'messages', 'platform_events'}
    assert any('CREATE TABLE messages' in (row['sql'] or '') for row in schema['objects'])


def test_capture_never_opens_a_preexisting_or_shared_file_except_the_store_copy_source(tmp_path, monkeypatch):
    root, connection = home(tmp_path)
    opened = []
    real_open, real_os_open = builtins.open, os.open

    def spy(path, *args, **kwargs):
        opened.append(os.fspath(path))
        return real_open(path, *args, **kwargs)

    def os_spy(path, *args, **kwargs):
        opened.append(os.fspath(path))
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', spy)
    monkeypatch.setattr(io, 'open', spy)
    monkeypatch.setattr(os, 'open', os_spy)
    detail = {}
    before = inventory_hermes_home(root, detail=detail)
    assert opened == []
    started = time.time_ns()
    turn(root, connection)
    stamps = {name: (root / name).lstat() for name in STORE}
    opened.clear()   # the writes of the simulated turn are not reads of the capture
    receipt, _after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    source = [Path(name).relative_to(root).as_posix() for name in opened if Path(name).is_relative_to(root)]
    allowed = set(receipt['classes']['session_owned']) | set(receipt['classes']['run_owned']) | set(STORE)
    assert set(source) == allowed and len(source) == len(allowed)
    assert not [name for name in source if name in SHARED or name in PRIVATE or name.startswith(('hermes-agent', 'cache', 'kanban'))]
    # The original store was only read as bytes: same identity, size and times.
    for name, old in stamps.items():
        new = (root / name).lstat()
        assert (new.st_ino, new.st_size, new.st_mtime_ns, new.st_ctime_ns) == (old.st_ino, old.st_size, old.st_mtime_ns, old.st_ctime_ns)


@pytest.mark.parametrize('name, reason', [
    ('pastes/paste-1.txt', 'no rule explains this entry'),
    ('skills/new-skill/SKILL.md', 'no rule explains this entry'),
    ('config.yaml', None),
])
def test_unexplained_change_fails_closed_and_names_the_path(tmp_path, name, reason):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    if reason is None:      # an old file that was removed
        (root / name).unlink()
    else:
        put(root, name, b'unknown')
    with pytest.raises(HermesStateError, match='unexplained') as caught:
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    rows = {row['relative_path']: row for row in caught.value.unexplained}
    assert name in rows and name in str(caught.value)
    assert rows[name]['change'] == ('removed' if reason is None else 'new')
    assert not (tmp_path / 'r1-native').exists() and not (tmp_path / '.r1-native-store-copy').exists()
    listing = refusal_listing(caught.value, attempt_id='hermes-test', turn=1, session_id=SID)
    assert listing['schema_version'] == 'session-bench-hermes-state-refusal-v1' and listing['unexplained'] == caught.value.unexplained
    assert 'RULES' in listing['how_to_extend'] and listing['rules'] == rules_table()


def test_a_refusal_is_cured_by_one_table_entry(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    put(root, 'pastes/paste-1.txt', b'unknown')
    rules = {**RULES, 'run_owned': RULES['run_owned'] + (r'pastes/paste-[^/]+\.txt',)}
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail, rules=rules)
    assert 'pastes/paste-1.txt' in receipt['classes']['run_owned'] and receipt['rules']['run_owned'][-1] == r'pastes/paste-[^/]+\.txt'
    # The receipt carries its table, so the check does not depend on the table of a later version.
    assert verify_hermes_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True


def test_other_session_change_symlink_and_digest_rules_fail_closed(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    after = inventory_hermes_home(root)
    assert classify_hermes_changes(before, after, session_id=SID, started_ns=started)['changed_entries'] > 0
    # A link that carries the session id cannot be copied.
    (root / f'sessions/{SID}.link').symlink_to('/etc/hosts')
    with pytest.raises(HermesStateError) as caught:
        classify_hermes_changes(before, inventory_hermes_home(root), session_id=SID, started_ns=started)
    assert any(row['relative_path'] == f'sessions/{SID}.link' for row in caught.value.unexplained)
    (root / f'sessions/{SID}.link').unlink()
    # A changed link elsewhere is a shared entry; it is not followed.
    (root / 'bin/hermes').unlink()
    (root / 'bin/hermes').symlink_to('/etc/hosts')
    shared = {row['relative_path']: row for row in classify_hermes_changes(
        before, inventory_hermes_home(root), session_id=SID, started_ns=started)['shared_changed']}
    assert shared['bin/hermes']['kind'] == 'symlink' and shared['bin/hermes']['content_read'] is False
    # A session file inside a digested subtree must not be hidden by the digest.
    put(root, f'cache/{SID}.json', b'{}')
    with pytest.raises(HermesStateError, match='unexplained') as caught:
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    assert [row['relative_path'] for row in caught.value.unexplained] == [f'cache/{SID}.json']
    assert 'digest_subtrees' in caught.value.unexplained[0]['reason']
    (root / f'cache/{SID}.json').unlink()
    # A file of another session changed: another Hermes process wrote during the capture.
    put(root, f'sessions/session_{OTHER}.json', b'another process wrote here')
    with pytest.raises(HermesStateError) as caught:
        classify_hermes_changes(before, inventory_hermes_home(root), session_id=SID, started_ns=started)
    assert [(row['relative_path'], row['reason']) for row in caught.value.unexplained] == [
        (f'sessions/session_{OTHER}.json', 'an entry of another session changed during the capture')]


def test_run_file_born_before_the_capture_is_shared_not_copied(tmp_path):
    root, connection, before, detail, _started = bracket(tmp_path)
    turn(root, connection)
    receipt, _after = capture(root, tmp_path / 'r1-native', before, time.time_ns() + 10**12, before_detail=detail)
    shared = {row['relative_path']: row for row in receipt['classes']['shared_changed']}
    assert receipt['classes']['run_owned'] == [] and shared['logs/run-20261006.log']['change'] == 'created_old_identity'
    assert not (tmp_path / 'r1-native/run-owned-private').exists()


def test_session_id_in_no_row_fails_closed_and_deletes_the_copy(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    connection.execute('DELETE FROM messages WHERE session_id = ?', (SID,))
    connection.execute('DELETE FROM sessions WHERE id = ?', (SID,))
    connection.commit()
    with pytest.raises(HermesStateError, match='in no row of state.db'):
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    assert not (tmp_path / 'r1-native').exists() and not (tmp_path / '.r1-native-store-copy').exists()
    # A missing store fails closed too.
    with pytest.raises(HermesStateError, match='session store is missing'):
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail, rules={**RULES, 'session_stores': ('absent.db',)})


def test_guard_refuses_a_row_that_names_another_session(tmp_path):
    root, connection = home(tmp_path)
    add_session(connection, SID, ['SB prompt one'], 10)
    # A child session of the test session: it holds the test id in a column, but it is another session.
    connection.execute('INSERT INTO sessions VALUES (?, ?, ?, ?, ?)', ('20261006_120000_child1', 'cli', SID, 'gpt-5.5', 2.0))
    connection.commit()
    with pytest.raises(HermesStateError, match='names another session in column id'):
        export_session_rows(root / 'state.db', SID)   # the synthetic store itself; a capture opens only a copy
    connection.execute("DELETE FROM sessions WHERE id = '20261006_120000_child1'")
    connection.commit()
    export, _schema, summary = export_session_rows(root / 'state.db', SID)
    assert summary['selected_rows'] == 4
    planted = json.loads(json.dumps({'stores': [export]}))
    planted['stores'][0]['tables'][0]['rows'].append({'rowid': 1, 'selected_by': ['join:msg->messages.id'],
                                                      'values': {'id': 1, 'msg': 1, 'data': None}})
    assert check_rows_name_only_session(planted, SID) is True     # attachments has no session column
    messages = next(table for table in planted['stores'][0]['tables'] if table['table'] == 'messages')
    messages['rows'].append({'rowid': 1, 'selected_by': ['exact:session_id'],
                             'values': {'id': 1, 'session_id': OTHER, 'role': 'user', 'content': 'x', 'payload': None}})
    with pytest.raises(HermesStateError, match='names another session in column session_id'):
        check_rows_name_only_session(planted, SID)


def test_store_with_search_index_keyless_table_and_bad_text_is_exported_losslessly(tmp_path):
    database = tmp_path / 'copy.db'
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT);
        CREATE VIRTUAL TABLE messages_fts USING fts5(content, content=messages, content_rowid=id);
        CREATE TABLE state_meta (session_id TEXT, key TEXT, value, PRIMARY KEY (session_id, key)) WITHOUT ROWID;
        CREATE TABLE "odd ""name" (owner BLOB, note TEXT);
    """)
    for session_id, number in ((SID, 1), (OTHER, 2)):
        connection.execute('INSERT INTO sessions VALUES (?, CAST(? AS TEXT))', (session_id, b'bad \xff text'))
        connection.execute('INSERT INTO messages VALUES (?, ?, ?)', (number, session_id, f'private text {number}'))
        connection.execute('INSERT INTO messages_fts (rowid, content) VALUES (?, ?)', (number, f'private text {number}'))
        connection.execute('INSERT INTO state_meta VALUES (?, ?, ?)', (session_id, 'cost', float('inf')))
        connection.execute('INSERT INTO "odd ""name" VALUES (?, ?)', (session_id.encode(), 'by bytes'))
    connection.commit()
    connection.close()
    export, schema, summary = export_session_rows(database, SID)
    tables = {table['table']: table for table in export['tables']}
    assert set(tables) == {'sessions', 'messages', 'state_meta', 'odd "name'}
    assert tables['sessions']['rows'][0]['values']['title'] == {'text_hex': b'bad \xff text'.hex()}
    assert tables['state_meta']['has_rowid'] is False and tables['state_meta']['rows'][0]['rowid'] is None
    assert tables['state_meta']['rows'][0]['values'] == {'session_id': SID, 'key': 'cost', 'value': {'real': 'inf'}}
    assert tables['odd "name']['rows'][0]['values']['owner'] == {'blob_hex': SID.encode().hex()}
    assert OTHER not in json.dumps(export) and 'private text 2' not in json.dumps(export)
    # The search index is scanned like a table. Its rows do not name the session and are not exported.
    counts = {row['table']: (row['selected_rows'], row['total_rows']) for row in summary['tables']}
    assert counts['messages_fts'] == (0, 2) and counts['messages'] == (1, 2) and summary['tables_not_scanned'] == []
    assert {row['name']: (row['virtual'], row['virtual_shadow']) for row in schema['tables']}['messages_fts_data'] == (False, True)
    json.dumps(export, allow_nan=False)


def test_quiescence_needs_two_equal_metadata_reads(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    writes = iter(range(100))

    def still_writing(_seconds):
        put(root, f'sessions/{SID}.jsonl', b'x' * (next(writes) + 1))

    with pytest.raises(HermesStateError, match='did not come to rest in 4 metadata reads'):
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail, sleep=still_writing, max_reads=4)
    assert not (tmp_path / 'r1-native').exists()
    calls = []

    def settles(_seconds):
        calls.append(1)
        if len(calls) < 3:
            put(root, f'sessions/{SID}.jsonl', b'y' * len(calls))

    receipt, after = capture_hermes_state(root, tmp_path / 'r1-native', before, attempt_id='hermes-test', turn=1, session_id=SID,
                                          started_ns=started, before_detail=detail, sleep=settles)
    quiet = receipt['quiescence']
    assert quiet['reads'] == 3 and quiet['inventory_sha256'][-1] == quiet['inventory_sha256'][-2] != quiet['inventory_sha256'][0]
    assert quiet['stable'] is True and quiet['unchanged_after_copy'] is True
    assert verify_hermes_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True
    with pytest.raises(HermesStateError, match='at least two metadata reads'):
        capture(root, tmp_path / 'again', before, started, max_reads=1)


def test_second_turn_uses_the_same_before_inventory_and_verification_detects_tampering(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    first, _ = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    turn(root, connection, texts=('SB prompt one', 'SB prompt two'))
    receipt, after = capture(root, tmp_path / 'r2-native', before, started, before_detail=detail, turn=2)
    assert receipt['session_store']['stores'][0]['tables'][0]['table'] == 'attachments'
    counts = {row['table']: row['selected_rows'] for row in receipt['session_store']['stores'][0]['tables']}
    assert counts['messages'] == 2 and first['session_store']['selected_rows'] < receipt['session_store']['selected_rows']
    destination = tmp_path / 'r2-native'
    assert verify_hermes_state_capture(receipt, destination, before=before, after=after) is True

    def refused(change, restore, **replace):
        change()
        with pytest.raises(HermesStateError):
            verify_hermes_state_capture({**receipt, **replace}, destination, before=before, after=after)
        restore()
        assert verify_hermes_state_capture(receipt, destination, before=before, after=after) is True

    rows = destination / 'session-store-rows.json'
    original = rows.read_bytes()
    refused(lambda: rows.write_bytes(original.replace(b'SB prompt two', b'SB prompt 2wo')), lambda: rows.write_bytes(original))
    copied = destination / receipt['artifacts'][0]['copied_path']
    data = copied.read_bytes()
    refused(lambda: copied.write_bytes(data[:-1] + b'!'), lambda: copied.write_bytes(data))
    refused(lambda: put(destination, 'extra.bin'), lambda: (destination / 'extra.bin').unlink())
    leftover = tmp_path / '.r2-native-store-copy'
    refused(leftover.mkdir, leftover.rmdir)
    nothing = lambda: None
    refused(nothing, nothing, preexisting_contents_opened=True)
    refused(nothing, nothing, classes={**receipt['classes'], 'run_owned': []})
    refused(nothing, nothing, rules={**receipt['rules'], 'run_owned': []})
    refused(nothing, nothing, session_id=OTHER)
    refused(nothing, nothing, session_store={**receipt['session_store'], 'rows_of_other_sessions_exported': 1})
    with pytest.raises(HermesStateError):
        verify_hermes_state_capture(receipt, destination, before=before, after=before)


def test_an_old_directory_touched_with_unchanged_children_is_recorded_not_refused(tmp_path):
    root, connection, before, detail, started = bracket(tmp_path)
    put(root, 'pairing/kept.json', b'private pairing')
    before = inventory_hermes_home(root, detail=detail)
    turn(root, connection)
    put(root, 'pairing/transient.tmp').unlink()   # a child that lived only between the inventories
    receipt, _ = capture(root, tmp_path / 'copy', before, started, before_detail=detail)
    touched = [row for row in receipt['classes']['shared_changed'] if row['relative_path'] == 'pairing']
    assert [(row['kind'], row['change'], row['content_read']) for row in touched] == [('directory', 'touched_children_unchanged', False)]
