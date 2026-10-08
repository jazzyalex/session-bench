"""OpenClaw capture in the owner's normal state: synthetic homes, synthetic stores, a fake agent.

No test starts OpenClaw or reads a real OpenClaw home.
"""
import builtins
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from session_bench import openclaw_state_capture as capture_module
from session_bench.openclaw_envelope_observer import build_openclaw_envelope_observer
from session_bench.openclaw_state_capture import (
    CODEX_HOME, RULES, VERSION, OpenClawCaptureError, OpenClawStateError, capture_openclaw_state, check_rows_name_only_test_session,
    classify_openclaw_changes, denied_table, execute_openclaw_capture, export_test_session_rows, inventory_openclaw_home,
    new_rollout_threads, openclaw_processes, planned_argv, prepare_openclaw_capture, refusal_listing, rules_table, suggests_secret,
    turn_argv, verify_openclaw_state_capture,
)

REPO = Path(__file__).resolve().parents[1]
SID = '9f1c2d3e-0abd-4117-af54-3aade9c80001'
OTHER = '5a020166-88b2-429c-8777-c672f4270002'
TID = '01a117b7-6ec3-73a3-a113-b1bffc1b0001'
OTHER_TID = '01a117b2-9399-7e61-af4f-bff81dc00002'
KEY, OTHER_KEY = 'agent:main:explicit:' + SID, 'agent:main:explicit:' + OTHER
IDENTITY = {'session_id': SID, 'thread_id': TID, 'session_key': KEY}
AGENT_DB = 'agents/main/agent/openclaw-agent.sqlite'
STATE_DB = 'state/openclaw.sqlite'
THREADS_DB, HISTORY_DB = CODEX_HOME + '/state_5.sqlite', CODEX_HOME + '/thread_history_1.sqlite'
ROLLOUT = f'{CODEX_HOME}/sessions/2026/10/07/rollout-2026-10-07T11-53-00-{TID}.jsonl'
OLD_ROLLOUT = f'{CODEX_HOME}/sessions/2026/10/06/rollout-2026-10-06T10-00-00-{OTHER_TID}.jsonl'
SNAPSHOT = f'{CODEX_HOME}/shell_snapshots/{TID}.1791398941630115000.sh'
THREAD_LOCK = f'{CODEX_HOME}/thread-writer-locks/{TID}.lock'
PRIVATE = ('.env', 'openclaw.json', 'openclaw.json.bak', 'credentials/oauth.json', 'identity/device.json', 'cron/jobs.json',
           'exec-approvals.json', f'agents/main/sessions/{OTHER}.jsonl', OLD_ROLLOUT, CODEX_HOME + '/config.toml')
SCHEMAS = {
    AGENT_DB: '''
        CREATE TABLE session_nodes (session_key TEXT PRIMARY KEY, current_session_id TEXT, entry_json TEXT);
        CREATE TABLE session_windows (session_id TEXT PRIMARY KEY, session_key TEXT REFERENCES session_nodes(session_key), previous_session_id TEXT);
        CREATE TABLE transcript_events (session_id TEXT REFERENCES session_windows(session_id), seq INTEGER, event_json TEXT, event_zstd BLOB,
                                        PRIMARY KEY (session_id, seq));
        CREATE TABLE session_entry_snapshots (session_key TEXT REFERENCES session_nodes(session_key), field TEXT, value_json TEXT);
        CREATE TABLE auth_profile_store (store_key TEXT PRIMARY KEY, store_json TEXT);
        CREATE TABLE oauth_grants (session_id TEXT, grant_json TEXT);
        CREATE TABLE provider_settings (session_id TEXT, api_key TEXT);
        CREATE TABLE memory_notes (id INTEGER PRIMARY KEY, note TEXT);
    ''',
    STATE_DB: '''
        CREATE TABLE plugin_state_entries (plugin_id TEXT, namespace TEXT, entry_key TEXT, value_json TEXT, PRIMARY KEY (plugin_id, namespace, entry_key));
        CREATE TABLE session_state_heads (session_key TEXT PRIMARY KEY, agent_id TEXT, last_sequence INTEGER);
        CREATE TABLE secret_store_entries (name TEXT, value TEXT);
        CREATE TABLE device_auth_tokens (device_id TEXT, token TEXT);
    ''',
    THREADS_DB: '''
        CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, tokens_used INTEGER);
        CREATE TABLE thread_dynamic_tools (thread_id TEXT REFERENCES threads(id), name TEXT);
    ''',
    HISTORY_DB: '''
        CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, status TEXT);
        CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, item_json TEXT);
    ''',
}


def put(root, name, data=b'x'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def session_rows(stores, session, key, thread, texts, *, secrets=False):
    """The rows of one session in the four stores; ``secrets`` adds rows that name it in secret tables."""
    agent, state, threads, history = (stores[name] for name in (AGENT_DB, STATE_DB, THREADS_DB, HISTORY_DB))
    if not agent.execute('SELECT 1 FROM session_windows WHERE session_id = ?', (session,)).fetchone():
        agent.execute('INSERT INTO session_nodes VALUES (?, ?, ?)', (key, session, json.dumps({'sessionId': session})))
        agent.execute('INSERT INTO session_windows VALUES (?, ?, NULL)', (session, key))
        agent.execute('INSERT INTO session_entry_snapshots VALUES (?, ?, ?)', (key, 'model', '"gpt-5.6-terra"'))
        threads.execute('INSERT INTO threads VALUES (?, ?, 0)', (thread, f'/synthetic/rollout-{thread}.jsonl'))
        threads.execute('INSERT INTO thread_dynamic_tools VALUES (?, ?)', (thread, 'bash'))
        state.execute('INSERT INTO plugin_state_entries VALUES (?, ?, ?, ?)',
                      ('codex', 'app-server-thread-bindings', 'session-key:main:' + session[:8], json.dumps({'threadId': thread, 'sessionId': session})))
        if secrets:
            agent.execute('INSERT INTO auth_profile_store VALUES (?, ?)', (session, 'SECRET-PROFILE-TOKEN'))
            agent.execute('INSERT INTO oauth_grants VALUES (?, ?)', (session, 'SECRET-GRANT'))
            agent.execute('INSERT INTO provider_settings VALUES (?, ?)', (session, 'sk-SECRET-KEY'))
    first = agent.execute('SELECT COUNT(*) FROM transcript_events WHERE session_id = ?', (session,)).fetchone()[0]
    for offset, text in enumerate(texts):
        agent.execute('INSERT INTO transcript_events VALUES (?, ?, ?, ?)', (session, first + offset, json.dumps({'text': text}), b'\x28\xb5' + text.encode()))
        history.execute('INSERT INTO thread_turns VALUES (?, ?, ?)', (thread, f'turn-{first + offset}', 'completed'))
        history.execute('INSERT INTO thread_items VALUES (?, ?, ?, ?)', (thread, f'turn-{first + offset}', f'item-{first + offset}', json.dumps({'text': text})))
    for connection in stores.values():
        connection.commit()


def home(tmp_path):
    """An OpenClaw home of an active install: an older session, shared stores, credentials, a package tree.

    Returns the root and the open writer connections of the four session
    stores. They stay open, so the ``-wal`` and ``-shm`` files stay on disk.
    """
    root = tmp_path / 'openclaw-home'
    for name in PRIVATE:
        put(root, name, b'private ' + name.encode())
    put(root, 'agents/main/sessions/sessions.json', b'{}')
    put(root, 'logs/gateway.log', b'old log')
    put(root, 'npm/node_modules/openclaw/package.json', b'{}')
    put(root, CODEX_HOME + '/.tmp/plugins/README.md', b'# plugins')
    put(root, CODEX_HOME + '/tmp/arg0/codex-arg0OLD/.lock', b'')
    (root / CODEX_HOME / 'tmp/arg0/codex-arg0OLD/apply_patch').symlink_to('/synthetic/codex')
    put(root, 'tmp/plugin-captures/cap-old/owner.sqlite', b'old capture')
    put(root, 'state/openclaw-quarantine.sqlite', b'quarantine')
    put(root, CODEX_HOME + '/logs_2.sqlite', b'codex logs')
    (root / 'workspace').mkdir()
    stores = {}
    for name, schema in SCHEMAS.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        connection = stores[name] = sqlite3.connect(root / name)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.executescript(schema)
        connection.execute('PRAGMA user_version=7')
    stores[AGENT_DB].execute("INSERT INTO auth_profile_store VALUES ('openai:default', 'OWNER-REFRESH-TOKEN')")
    stores[AGENT_DB].execute("INSERT INTO memory_notes (note) VALUES ('other private note')")
    stores[STATE_DB].execute("INSERT INTO secret_store_entries VALUES ('github', 'OWNER-SECRET')")
    stores[STATE_DB].execute("INSERT INTO device_auth_tokens VALUES ('phone', 'OWNER-DEVICE-TOKEN')")
    session_rows(stores, OTHER, OTHER_KEY, OTHER_TID, ['other private one', 'other private two'])
    assert all((root / (name + suffix)).is_file() for name in SCHEMAS for suffix in ('', '-wal', '-shm'))
    return root, stores


def turn(root, stores, *, texts=('SB prompt one',), number=1, secrets=False, rollout=ROLLOUT, thread=TID):
    """What one agent turn leaves in the home: files of the thread, rows, temporary entries, changed shared files."""
    put(root, rollout, b'{"type":"session_meta"}\n' + b'{"type":"turn"}\n' * number)
    put(root, SNAPSHOT.replace(TID, thread), b'# shell snapshot\n')
    put(root, THREAD_LOCK.replace(TID, thread), b'')
    put(root, CODEX_HOME + '/thread-writer-locks/.coordination.lock', b'')
    arg0 = root / CODEX_HOME / f'tmp/arg0/codex-arg0NEW{number}'
    put(root, f'{CODEX_HOME}/tmp/arg0/codex-arg0NEW{number}/.lock', b'')
    (arg0 / 'apply_patch').symlink_to('/synthetic/codex')
    shutil.rmtree(root / CODEX_HOME / 'tmp/arg0/codex-arg0OLD', ignore_errors=True)   # the plugin removes a stale directory
    put(root, 'tmp/plugin-captures/cap-old/owner.sqlite', b'old capture, changed ' + str(number).encode())
    put(root, f'tmp/plugin-captures/cap-{number}/owner.sqlite', b'owner')
    put(root, f'tmp/plugin-captures/cap-{number}/native/admission-AbC/content/package.json', b'{"name":"@openclaw/codex"}')
    put(root, f'tmp/plugin-captures/cap-{number}/native/admission-AbC/content/vendor/bin/codex', b'\x7fELF large binary')
    put(root, f'logs/openclaw-2026-10-07-{number}.log', b'run log with personal data')
    put(root, 'agents/main/sessions/sessions.json', b'{"changed": %d}' % number)
    put(root, CODEX_HOME + '/logs_2.sqlite', b'codex logs, changed %d' % number)
    put(root, CODEX_HOME + '/.tmp/plugins/README.md', b'# plugins, fetched %d' % number)
    put(root, f'openclaw.json.bak.{number}', b'config backup')
    session_rows(stores, SID, KEY, thread, list(texts), secrets=secrets)


def capture(root, destination, before, started, **options):
    options.setdefault('session_id', SID)
    options.setdefault('thread_id', TID)
    options.setdefault('session_key', KEY)
    options.setdefault('sleep', lambda _: None)
    return capture_openclaw_state(root, destination, before, attempt_id='openclaw-test', turn=options.pop('turn', 1),
                                  started_ns=started, build=VERSION, **options)


def bracket(tmp_path):
    root, stores = home(tmp_path)
    detail = {}
    before = inventory_openclaw_home(root, detail=detail)
    return root, stores, before, detail, time.time_ns()


# --- Inventory and classification ---

def test_inventory_covers_the_whole_home_and_digests_large_subtrees(tmp_path):
    root, _stores = home(tmp_path)
    detail = {}
    snapshot = inventory_openclaw_home(root, detail=detail)
    entries = {row['relative_path']: row for row in snapshot['entries']}
    assert snapshot['scope'] == 'whole_openclaw_home' and snapshot['metadata_only'] is True
    assert set(PRIVATE) | {AGENT_DB, AGENT_DB + '-wal', STATE_DB, THREADS_DB, HISTORY_DB, 'workspace'} <= set(entries)
    assert entries['npm']['kind'] == 'directory-digest' and entries[CODEX_HOME + '/.tmp']['kind'] == 'directory-digest'
    assert not [name for name in entries if name.startswith('npm/')] and 'npm/node_modules/openclaw/package.json' in detail['npm']
    link = entries[CODEX_HOME + '/tmp/arg0/codex-arg0OLD/apply_patch']
    assert link['kind'] == 'symlink' and 'target' not in link
    (tmp_path / 'link-home').symlink_to(root)
    with pytest.raises(OpenClawStateError, match='ordinary directory'):
        inventory_openclaw_home(tmp_path / 'link-home')


def test_every_changed_entry_is_classified_and_owned_files_are_copied(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    assert receipt['schema_version'] == 'openclaw-state-root-v1' and receipt['scope'] == 'whole_openclaw_home'
    assert (receipt['session_id'], receipt['thread_id'], receipt['session_key']) == (SID, TID, KEY)
    assert receipt['thread_id_source'].startswith('stdout envelope, confirmed') and receipt['new_rollout_threads'] == [TID]
    assert receipt['metadata_only_before_after'] is True and receipt['preexisting_contents_opened'] is False
    classes = receipt['classes']
    # (a) Session-owned: every file named by the Codex thread id. Copied byte-exact.
    assert set(classes['session_owned']) == {ROLLOUT, SNAPSHOT, THREAD_LOCK}
    # (b) Run-owned by name and birth time. Copied, private only.
    assert set(classes['run_owned']) == {'tmp/plugin-captures/cap-1/owner.sqlite', 'tmp/plugin-captures/cap-1/native/admission-AbC/content/package.json',
                                         CODEX_HOME + '/tmp/arg0/codex-arg0NEW1/.lock', 'logs/openclaw-2026-10-07-1.log'}
    shared = {row['relative_path']: row for row in classes['shared_changed']}
    assert all(row['content_read'] is False for row in shared.values())
    # (c) Old files that changed, sidecars, temporary entries, a removed temporary directory, a digested subtree: metadata only.
    assert {AGENT_DB + '-wal', STATE_DB + '-wal', THREADS_DB + '-wal', HISTORY_DB + '-wal', 'agents/main/sessions/sessions.json',
            CODEX_HOME + '/logs_2.sqlite', 'tmp/plugin-captures/cap-old/owner.sqlite', 'openclaw.json.bak.1',
            'tmp/plugin-captures/cap-1/native/admission-AbC/content/vendor/bin/codex', CODEX_HOME + '/thread-writer-locks/.coordination.lock',
            CODEX_HOME + '/tmp/arg0/codex-arg0NEW1/apply_patch', CODEX_HOME + '/tmp/arg0/codex-arg0OLD',
            CODEX_HOME + '/tmp/arg0/codex-arg0OLD/.lock', CODEX_HOME + '/tmp/arg0/codex-arg0OLD/apply_patch', CODEX_HOME + '/.tmp'} <= set(shared)
    assert shared[CODEX_HOME + '/tmp/arg0/codex-arg0OLD'] ['change'] == 'removed' and shared[CODEX_HOME + '/tmp/arg0/codex-arg0OLD']['kind'] == 'directory'
    assert shared[CODEX_HOME + '/.tmp']['changed_paths'] == [CODEX_HOME + '/.tmp/plugins/README.md']
    assert not set(PRIVATE) & (set(shared) | set(classes['session_owned']) | set(classes['run_owned']))
    accounting = receipt['accounting']
    assert accounting['every_changed_entry_accounted'] is True and accounting['unexplained'] == []
    assert accounting['classes_found'] == ['session_owned', 'run_owned', 'shared_changed']
    assert accounting['session_store_rows_read'] == list(RULES['session_stores'])
    assert 'does not prove that it holds none' in accounting['statement'] and 'suggest secrets were not read' in accounting['statement']
    artifacts = {row['relative_path']: row for row in receipt['artifacts']}
    assert set(artifacts) == set(classes['session_owned']) | set(classes['run_owned'])
    for name, row in artifacts.items():
        owned = name in classes['session_owned']
        assert row['class'] == ('session_owned' if owned else 'run_owned') and row['private_only'] is (not owned)
        assert row.get('owner_id') == ('codex_thread_id' if owned else None)
        data = (tmp_path / 'r1-native' / row['copied_path']).read_bytes()
        assert data == (root / name).read_bytes() and row['sha256'] == hashlib.sha256(data).hexdigest() and row['size_bytes'] == len(data)
    assert receipt['rules'] == rules_table()
    assert verify_openclaw_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True


def test_a_path_with_the_openclaw_session_id_is_session_owned_too(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    put(root, f'agents/main/sessions/{SID}.jsonl', b'{"role":"user"}\n')
    put(root, f'agents/main/sessions/{SID}.trajectory/steps.json', b'[]')
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    owners = {row['relative_path']: row['owner_id'] for row in receipt['artifacts'] if row['class'] == 'session_owned'}
    assert owners[f'agents/main/sessions/{SID}.jsonl'] == owners[f'agents/main/sessions/{SID}.trajectory/steps.json'] == 'openclaw_session_id'
    assert owners[ROLLOUT] == 'codex_thread_id' and receipt['session_directories'] == [f'agents/main/sessions/{SID}.trajectory']
    assert verify_openclaw_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True


@pytest.mark.parametrize('change, path, reason', [
    (lambda root: put(root, 'devices/unknown.bin', b'?'), 'devices/unknown.bin', 'no rule explains'),
    (lambda root: put(root, f'agents/main/sessions/{OTHER}.jsonl', b'another session grew'), f'agents/main/sessions/{OTHER}.jsonl', 'another session changed'),
    (lambda root: put(root, OLD_ROLLOUT, b'another thread grew'), OLD_ROLLOUT, 'another session changed'),
    (lambda root: put(root, f'agents/main/sessions/{OTHER}.jsonl.lock', b''), f'agents/main/sessions/{OTHER}.jsonl.lock', 'no rule explains'),
    (lambda root: put(root, ROLLOUT.replace(TID, '01a117b9-0000-7000-8000-00000000abcd'), b'{}'), 'rollout-2026-10-07T11-53-00-01a117b9', 'no rule explains'),
    (lambda root: shutil.rmtree(root / 'cron'), 'cron', 'a directory was removed'),
    (lambda root: (root / 'credentials/oauth.json').unlink(), 'credentials/oauth.json', 'no rule explains'),
    (lambda root: shutil.rmtree(root / 'npm'), 'npm', 'digested subtree appeared, disappeared'),
    (lambda root: (root / CODEX_HOME / f'sessions/link-{TID}').symlink_to('/synthetic'), f'link-{TID}', 'session-owned link'),
])
def test_unexplained_change_fails_closed_with_a_refusal_listing(tmp_path, change, path, reason):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    change(root)
    with pytest.raises(OpenClawStateError) as raised:
        capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    rows = [row for row in raised.value.unexplained if path in row['relative_path']]
    assert rows and reason in rows[0]['reason'], raised.value.unexplained
    assert not (tmp_path / 'r1-native').exists() and not (tmp_path / '.r1-native-store-copy').exists()
    listing = refusal_listing(raised.value, attempt_id='openclaw-test', turn=1, session_id=SID, thread_id=TID)
    assert listing['schema_version'] == 'session-bench-openclaw-state-refusal-v1' and listing['unexplained'] == raised.value.unexplained
    assert listing['rules'] == rules_table() and 'RULES' in listing['how_to_extend'] and listing['metadata_only'] is True


def test_a_path_of_the_session_before_the_capture_and_a_home_that_does_not_rest_are_refused(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    stale = inventory_openclaw_home(root)
    with pytest.raises(OpenClawStateError, match='existed before the capture'):
        classify_openclaw_changes(stale, stale, session_id=SID, thread_id=TID, started_ns=started)
    with pytest.raises(OpenClawStateError, match='no new rollout file'):
        capture(root, tmp_path / 'late', stale, started)
    counter = iter(range(100))
    with pytest.raises(OpenClawStateError, match='did not come to rest in 3'):
        capture(root, tmp_path / 'busy', before, started, max_reads=3, sleep=lambda _: put(root, 'logs/gateway.log', str(next(counter)).encode()))
    with pytest.raises(OpenClawStateError, match='too short'):
        classify_openclaw_changes(before, before, session_id='abc', thread_id=TID, started_ns=started)


def test_a_log_file_born_before_the_capture_is_not_copied(tmp_path):
    root, stores = home(tmp_path)
    old = put(root, 'logs/staging.log', b'old private log')
    before = inventory_openclaw_home(root)
    time.sleep(0.02)
    started = time.time_ns()
    turn(root, stores)
    old.rename(root / 'logs/rotated.log')
    receipt, _after = capture(root, tmp_path / 'r1-native', before, started)
    shared = {row['relative_path']: row['change'] for row in receipt['classes']['shared_changed']}
    assert shared['logs/rotated.log'] == 'created_old_identity' and 'logs/rotated.log' not in receipt['classes']['run_owned']
    assert not (tmp_path / 'r1-native/run-owned-private/logs/rotated.log').exists()


# --- The Codex thread id ---

def test_thread_id_comes_from_the_envelope_and_must_be_confirmed_by_a_rollout_name(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    assert new_rollout_threads(before, inventory_openclaw_home(root)) == [TID]
    # Without an id on stdout, the one new rollout file gives it.
    receipt, _after = capture(root, tmp_path / 'by-name', before, started, thread_id=None)
    assert receipt['thread_id'] == TID and receipt['thread_id_source'] == 'name of the one new rollout file'
    with pytest.raises(OpenClawStateError, match='no new rollout file carries the Codex thread id'):
        capture(root, tmp_path / 'wrong', before, started, thread_id='01a117b9-0000-7000-8000-00000000abcd')
    put(root, ROLLOUT.replace(TID, '01a117b9-0000-7000-8000-00000000abcd'), b'{}')
    with pytest.raises(OpenClawStateError, match='cannot be told: 2 new rollout files'):
        capture(root, tmp_path / 'two', before, started, thread_id=None)


def test_no_rollout_file_means_no_capture(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    (root / ROLLOUT).unlink()
    with pytest.raises(OpenClawStateError, match='no new rollout file'):
        capture(root, tmp_path / 'r1-native', before, started)
    with pytest.raises(OpenClawStateError, match='cannot be told: 0 new rollout files'):
        capture(root, tmp_path / 'r1-native', before, started, thread_id=None)


# --- Rows of the test session ---

def test_rows_are_exported_from_deleted_copies_and_no_row_of_another_session_leaves(tmp_path, monkeypatch):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores, texts=('SB prompt one', 'SB prompt two'))
    connected = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(capture_module.sqlite3, 'connect', lambda target, **options: connected.append((target, options)) or real_connect(target, **options))
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    monkeypatch.undo()
    store = receipt['session_store']
    # Only private copies were opened with SQLite, read-only, and they are gone.
    temporary = tmp_path / '.r1-native-store-copy'
    assert len(connected) == 4 and all(str(temporary) in target and target.endswith('?mode=ro') and options == {'uri': True} for target, options in connected)
    assert not any(str(root) in target for target, _ in connected) and not temporary.exists()
    assert store['temporary_copy'] == {'directory': '.r1-native-store-copy', 'deleted': True}
    assert store['rows_of_other_sessions_exported'] == 0 and store['rows_of_denied_tables_exported'] == 0
    summaries = {item['store']: item for item in store['stores']}
    assert list(summaries) == list(RULES['session_stores'])
    # The database, its WAL and its SHM file travel together. The new rows are only in the WAL here.
    assert [row['relative_path'] for row in summaries[AGENT_DB]['copied_files']] == [AGENT_DB, AGENT_DB + '-wal', AGENT_DB + '-shm']
    assert b'SB prompt one' not in (root / AGENT_DB).read_bytes() and b'SB prompt one' in (root / (AGENT_DB + '-wal')).read_bytes()
    counts = {name: {row['table']: (row['selected_rows'], row['total_rows']) for row in item['tables']} for name, item in summaries.items()}
    assert counts[AGENT_DB] == {'session_nodes': (1, 2), 'session_windows': (1, 2), 'transcript_events': (2, 4), 'session_entry_snapshots': (1, 2),
                                'auth_profile_store': (0, None), 'oauth_grants': (0, None), 'provider_settings': (0, None), 'memory_notes': (0, 1)}
    assert counts[THREADS_DB] == {'threads': (1, 2), 'thread_dynamic_tools': (1, 2)}
    assert counts[HISTORY_DB] == {'thread_turns': (2, 4), 'thread_items': (2, 4)}
    by_table = {row['table']: row for row in summaries[AGENT_DB]['tables']}
    assert by_table['session_nodes']['selected_by'] == ['exact:current_session_id', 'exact:session_key']
    assert 'join:session_key->session_nodes.session_key' in by_table['session_entry_snapshots']['selected_by']
    assert {(row['table'], row['column'], row['basis']) for row in summaries[THREADS_DB]['joins']} == {('thread_dynamic_tools', 'thread_id', 'declared foreign key')}
    # The shared state store names the session only inside a longer value: counted, not exported.
    shared_state = summaries[STATE_DB]
    assert shared_state['required'] is False and shared_state['selected_rows'] == 0
    assert {row['table']: row['rows_that_contain_an_id'] for row in shared_state['tables']}['plugin_state_entries'] == 1
    raw = (tmp_path / 'r1-native/session-store-rows.json').read_bytes()
    assert store['rows_export'] == {'path': 'session-store-rows.json', 'size_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    for private in (OTHER, OTHER_TID, 'other private', 'OWNER-', 'SECRET'):
        assert private.encode() not in raw
    export = json.loads(raw)
    assert export['schema_version'] == 'session-bench-openclaw-session-store-rows-v1'
    assert (export['session_id'], export['thread_id'], export['session_key']) == (SID, TID, KEY)
    assert [item['store'] for item in export['stores']] == list(RULES['session_stores'])
    events = next(table for table in export['stores'][0]['tables'] if table['table'] == 'transcript_events')
    assert events['rows'][0]['values'] == {'session_id': SID, 'seq': 0, 'event_json': '{"text": "SB prompt one"}',
                                           'event_zstd': {'blob_hex': (b'\x28\xb5' + b'SB prompt one').hex()}}
    schema = json.loads((tmp_path / 'r1-native/session-store-schema.json').read_bytes())['stores'][0]
    assert schema['user_version'] == 7 and {row['name'] for row in schema['tables']} >= {'session_nodes', 'auth_profile_store'}
    assert verify_openclaw_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True


def test_credential_tables_are_never_read_even_when_a_row_names_the_session(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores, secrets=True)
    # Rows in three secret tables name the test session exactly.
    agent = stores[AGENT_DB]
    assert agent.execute('SELECT COUNT(*) FROM auth_profile_store WHERE store_key = ?', (SID,)).fetchone()[0] == 1
    assert agent.execute('SELECT COUNT(*) FROM provider_settings WHERE session_id = ?', (SID,)).fetchone()[0] == 1
    receipt, _after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    summaries = {item['store']: item for item in receipt['session_store']['stores']}
    assert summaries[AGENT_DB]['tables_denied'] == [
        {'table': 'auth_profile_store', 'reason': 'the table name suggests secrets'},
        {'table': 'oauth_grants', 'reason': 'the table name suggests secrets'},
        {'table': 'provider_settings', 'reason': 'column names suggest secrets: api_key'}]
    assert [row['table'] for row in summaries[STATE_DB]['tables_denied']] == ['device_auth_tokens', 'secret_store_entries']
    for row in summaries[AGENT_DB]['tables']:
        if row['denied']:
            # Not selected, not counted, not searched.
            assert (row['selected_rows'], row['total_rows'], row['rows_that_contain_an_id'], row['other_rows_counted_not_read']) == (0, None, None, None)
    raw = (tmp_path / 'r1-native/session-store-rows.json').read_bytes()
    assert b'SECRET' not in raw and b'sk-' not in raw and b'auth_profile_store' not in raw and b'provider_settings' not in raw
    # The names of the denied tables and their columns stay in the schema export; no row does.
    schema = (tmp_path / 'r1-native/session-store-schema.json').read_bytes()
    assert b'auth_profile_store' in schema and b'api_key' in schema and b'SECRET' not in schema


def test_a_secret_table_is_not_queried_at_all(tmp_path):
    root, stores = home(tmp_path)
    turn(root, stores, secrets=True)
    for connection in stores.values():
        connection.close()
    statements = []
    real_connect = sqlite3.connect

    def traced(target, **options):
        connection = real_connect(target, **options)
        connection.set_trace_callback(statements.append)
        return connection

    capture_module.sqlite3.connect = traced
    try:
        export, _schema, summary = export_test_session_rows(root / AGENT_DB, IDENTITY, store=AGENT_DB)
    finally:
        capture_module.sqlite3.connect = real_connect
    assert summary['selected_rows'] == 4
    # Only the schema statements name a secret table: its columns are listed, no row is selected or counted.
    for name in ('auth_profile_store', 'oauth_grants', 'provider_settings'):
        touching = [text for text in statements if name in text and 'sqlite_master' not in text]
        assert touching and all(text.startswith('PRAGMA') or 'LIMIT 0' in text for text in touching), touching


def test_deny_rule_judges_names_only():
    for name in ('auth_profile_store', 'device_auth_tokens', 'mcp_oauth_stores', 'secret_store_entries', 'worker_environment_credentials',
                 'config_revision_keys', 'ApiKeys', 'userPasswords', 'oauth2_state'):
        assert suggests_secret(name), name
    for name in ('session_nodes', 'transcript_events', 'threads', 'thread_items', 'authors', 'keyboard_layouts', 'monkeys', 'tokenizer_cache'):
        assert not suggests_secret(name), name
    assert denied_table('session_windows', ['session_id', 'session_key', 'previous_session_id']) is None
    assert denied_table('threads', ['id', 'rollout_path', 'tokens_used']) is None
    assert denied_table('transcript_event_identities', ['session_id', 'message_idempotency_key']) is None
    assert denied_table('usage', ['input_tokens', 'cache_read_tokens']) is None
    assert denied_table('native_hook_relay_bridges', ['id', 'token']) == 'column names suggest secrets: token'
    assert denied_table('web_push_subscriptions', ['endpoint', 'auth', 'p256dh']) == 'column names suggest secrets: auth'
    assert denied_table('session_key_contract', ['id']) == 'the table name suggests secrets'


def test_guard_rejects_rows_of_another_session_or_thread_and_denied_tables():
    def export(table, columns, values, primary_key=()):
        return {'stores': [{'store': 's', 'tables': [{'table': table, 'columns': columns, 'primary_key': list(primary_key),
                                                      'rows': [{'rowid': 1, 'selected_by': ['exact:x'], 'values': dict(zip(columns, values))}]}]}]}

    assert check_rows_name_only_test_session(export('session_windows', ['session_id', 'session_key', 'previous_session_id'], [SID, KEY, OTHER]), IDENTITY)
    assert check_rows_name_only_test_session(export('thread_items', ['thread_id', 'item_json'], [{'blob_hex': TID.encode().hex()}, '{}']), IDENTITY)
    for table, columns, values, key in (
            ('session_windows', ['session_id', 'previous_session_id'], [OTHER, SID], ()),
            ('session_nodes', ['session_key', 'current_session_id'], [OTHER_KEY, SID], ()),
            ('session_nodes', ['session_key', 'current_session_id'], ['agent:main:main', SID], ()),
            ('thread_items', ['thread_id', 'item_json'], [OTHER_TID, TID], ()),
            ('threads', ['id', 'rollout_path'], [OTHER_TID, TID], ('id',)),
            ('transcript_events', ['session_id'], [{'blob_hex': OTHER.encode().hex()}], ())):
        with pytest.raises(OpenClawStateError, match='names another session or thread'):
            check_rows_name_only_test_session(export(table, columns, values, key), IDENTITY)
    with pytest.raises(OpenClawStateError, match='deny rule: auth_profile_store'):
        check_rows_name_only_test_session(export('auth_profile_store', ['store_key', 'store_json'], [SID, '{}']), IDENTITY)
    with pytest.raises(OpenClawStateError, match='does not carry the session id'):
        check_rows_name_only_test_session(export('t', ['a'], [1]), {**IDENTITY, 'session_key': 'agent:main:main'})


def test_a_row_that_names_the_session_and_another_session_stops_the_export(tmp_path):
    root, stores = home(tmp_path)
    turn(root, stores)
    # A window of another session that points back to the test session: an exact match in a row of someone else.
    stores[AGENT_DB].execute('UPDATE session_windows SET previous_session_id = ? WHERE session_id = ?', (SID, OTHER))
    stores[AGENT_DB].commit()
    with pytest.raises(OpenClawStateError, match='session_windows names another session or thread in column session_id'):
        export_test_session_rows(root / AGENT_DB, IDENTITY, store=AGENT_DB)


def test_required_store_without_the_session_is_refused_and_the_optional_store_exports_exact_key_rows(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    stores[STATE_DB].execute("INSERT INTO session_state_heads VALUES (?, 'main', 3)", (KEY,))
    stores[STATE_DB].execute("INSERT INTO session_state_heads VALUES (?, 'main', 9)", (OTHER_KEY,))
    stores[STATE_DB].commit()
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    state = next(item for item in receipt['session_store']['stores'] if item['store'] == STATE_DB)
    assert {row['table']: row['selected_rows'] for row in state['tables']}['session_state_heads'] == 1
    assert verify_openclaw_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True
    stores[HISTORY_DB].execute('DELETE FROM thread_turns')
    stores[HISTORY_DB].execute('DELETE FROM thread_items')
    stores[HISTORY_DB].commit()
    with pytest.raises(OpenClawStateError, match='no row of .*thread_history_1.sqlite names the session'):
        capture(root, tmp_path / 'r2-native', before, started, before_detail=detail)
    assert not (tmp_path / 'r2-native').exists() and not (tmp_path / '.r2-native-store-copy').exists()


def test_a_missing_optional_store_is_recorded_and_a_missing_required_store_is_refused(tmp_path):
    root, stores = home(tmp_path)
    stores.pop(STATE_DB).close()
    for suffix in ('', '-wal', '-shm'):
        (root / (STATE_DB + suffix)).unlink(missing_ok=True)
    before, started = inventory_openclaw_home(root), time.time_ns()
    stores[STATE_DB] = sqlite3.connect(':memory:')
    stores[STATE_DB].executescript(SCHEMAS[STATE_DB])
    turn(root, stores)
    receipt, after = capture(root, tmp_path / 'r1-native', before, started)
    state = receipt['session_store']['stores'][-1]
    assert state == {'store': STATE_DB, 'required': False, 'present': False, 'selected_rows': 0, 'copied_files': []}
    assert STATE_DB not in receipt['accounting']['session_store_rows_read']
    assert verify_openclaw_state_capture(receipt, tmp_path / 'r1-native', before=before, after=after) is True
    rules = {**RULES, 'session_stores': RULES['session_stores'] + ('state/absent.sqlite',)}
    with pytest.raises(OpenClawStateError, match='a session store is missing from the OpenClaw home: state/absent.sqlite'):
        capture(root, tmp_path / 'r2-native', before, started, rules=rules)


def test_capture_never_opens_a_preexisting_or_shared_file_except_the_store_copy_sources(tmp_path, monkeypatch):
    root, stores = home(tmp_path)
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
    before = inventory_openclaw_home(root, detail=detail)
    assert opened == []
    started = time.time_ns()
    turn(root, stores)
    opened.clear()
    receipt, _after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    monkeypatch.undo()
    inside = {str(Path(path).relative_to(root)) for path in opened if str(path).startswith(str(root) + os.sep)}
    stores_and_sidecars = {name + suffix for name in RULES['session_stores'] for suffix in ('', '-wal', '-shm')}
    owned = set(receipt['classes']['session_owned']) | set(receipt['classes']['run_owned'])
    assert inside == owned | stores_and_sidecars
    assert not inside & set(PRIVATE) and CODEX_HOME + '/logs_2.sqlite' not in inside and 'state/openclaw-quarantine.sqlite' not in inside
    assert 'tmp/plugin-captures/cap-old/owner.sqlite' not in inside and 'tmp/plugin-captures/cap-1/native/admission-AbC/content/vendor/bin/codex' not in inside


def test_verifier_detects_a_changed_copy_a_changed_export_and_a_left_over_store_copy(tmp_path):
    root, stores, before, detail, started = bracket(tmp_path)
    turn(root, stores)
    receipt, after = capture(root, tmp_path / 'r1-native', before, started, before_detail=detail)
    check = lambda value=receipt: verify_openclaw_state_capture(value, tmp_path / 'r1-native', before=before, after=after)  # noqa: E731
    assert check() is True
    copied = tmp_path / 'r1-native/session-owned' / ROLLOUT
    original = copied.read_bytes()
    copied.write_bytes(original[:-1] + b'X')
    with pytest.raises(OpenClawStateError, match='copied file differs'):
        check()
    copied.write_bytes(original)
    rows = tmp_path / 'r1-native/session-store-rows.json'
    saved = rows.read_bytes()
    rows.write_bytes(saved.replace(b'SB prompt one', b'SB prompt 0ne'))
    with pytest.raises(OpenClawStateError, match='session-store-rows.json differs'):
        check()
    rows.write_bytes(saved)
    (tmp_path / '.r1-native-store-copy').mkdir()
    with pytest.raises(OpenClawStateError, match='not proved deleted'):
        check()
    (tmp_path / '.r1-native-store-copy').rmdir()
    with pytest.raises(OpenClawStateError, match='classification differs'):
        check({**receipt, 'classes': {**receipt['classes'], 'run_owned': []}})
    with pytest.raises(OpenClawStateError, match='not confirmed by the name of a new rollout file'):
        check({**receipt, 'thread_id': OTHER_TID})
    with pytest.raises(OpenClawStateError, match='does not bind its rule table'):
        check({**receipt, 'rules': {**receipt['rules'], 'secret_words': []}})
    assert check() is True


# --- The controller ---

FIXED = ('def checkout(items):\n'
         '    subtotal = sum(price * quantity for price, quantity in items)\n'
         '    return subtotal + (0 if subtotal >= 50 else 5)\n')
_SETTLE = {'interval_seconds': 0, 'sleep': lambda _seconds: None}


def envelope(text, *, session, turn, model='gpt-5.6-terra', thread=TID, workspace='/synthetic', harness='codex'):
    turn_id = f'01a117b8-300d-7342-92ef-00000000000{turn}'
    return json.dumps({
        'payloads': [{'text': text, 'mediaUrl': None}],
        'meta': {'durationMs': 9, 'aborted': False, 'stopReason': 'stop', 'finalAssistantVisibleText': text,
                 'agentMeta': {'sessionId': session, 'provider': 'openai', 'model': model, 'agentHarnessId': harness,
                               'credentialSource': {'kind': 'profile'},
                               'usage': {'input': 10, 'output': 5, 'cacheRead': 3, 'cacheWrite': 0, 'total': 18},
                               'terminalReceipt': {'runId': f'run-{turn}', 'sessionId': session, 'turnId': turn_id, 'rerouted': False,
                                                   'assistantTranscriptIdempotencyKey': f'codex-app-server:{thread}:{turn_id}:assistant'}},
                 'systemPromptReport': {'generatedAt': 1791399260000 + turn, 'sessionId': session, 'sessionKey': 'agent:main:explicit:' + session,
                                        'workspaceDir': workspace},
                 'executionTrace': {'winnerProvider': 'openai', 'winnerModel': model, 'fallbackUsed': False},
                 'toolSummary': {'calls': 2, 'tools': ['bash'], 'failures': 0}}}, ensure_ascii=False, indent=2)


def prepared(tmp_path, monkeypatch):
    for name in [key for key in os.environ if key.startswith(('OPENCLAW_', 'CLAWDBOT_'))]:
        monkeypatch.delenv(name)
    repository = tmp_path / 'repo'
    workload_root = repository / 'fixtures/scenarios/survival-v1/workload'
    workload_root.parent.mkdir(parents=True)
    shutil.copytree(REPO / 'fixtures/scenarios/survival-v1/workload', workload_root)
    destination = repository / 'artifacts/v1-expanded-preparation/live-captures/openclaw-controller-test'
    destination.parent.mkdir(parents=True)
    executable = tmp_path / 'openclaw'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    root, stores = home(tmp_path)
    workspace = tmp_path / 'owner-workspace'
    put(workspace, 'AGENTS.md', b'owner instructions')
    put(workspace, 'memory/2026-10-06.md', b'owner memory')
    plan = prepare_openclaw_capture(destination, repository=repository, repetition=1, executable=executable, openclaw_home=root)
    return destination, plan, root, stores, workspace


def ready(workspace):
    def preflight(plan, env):
        return {'ready': True, 'version': VERSION, 'workspace': str(workspace), 'processes': [], 'model_submission': False}
    return preflight


def fake_agent(root, stores, workspace, *, change=None, extra=None):
    """A runner that does what the local agent does: helper runs in the workspace, state in the home, one envelope on stdout."""
    calls = []

    def runner(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(list(argv))
        turn_number = len(calls)
        session = argv[argv.index('--session-id') + 1]
        prompt = Path(argv[argv.index('--message-file') + 1]).read_text()
        assert Path(cwd) == workspace and f'RESPONSE_R{turn_number}' in prompt
        fixture = workspace / 'fixture_project'
        if turn_number == 1:
            for phase in ('inspect', 'baseline'):
                subprocess.run([sys.executable, 'bench_check.py', phase], cwd=fixture, env=dict(env), capture_output=True, check=False)
        else:
            (fixture / 'checkout.py').write_text(FIXED)
            subprocess.run([sys.executable, 'bench_check.py', 'final'], cwd=fixture, env=dict(env), capture_output=True, check=False)
        rows = dict(stores)
        # The fake agent writes the rows of the session that the controller asked for.
        global_ids = {'session': session, 'key': 'agent:main:explicit:' + session}
        turn_files(root, rows, turn_number, prompt, global_ids)
        if extra is not None:
            extra(turn_number)
        canary = 'SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂' if turn_number == 1 else 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ'
        document = envelope(f'Done. {canary}', session=session, turn=turn_number, workspace=str(workspace))
        code = 0
        if change is not None:
            document, code = change(turn_number, document)
        stdout.write_text(document)
        stderr.write_text('[agents/agent-command] [agent] run ended with stopReason=stop\n')
        return code

    return runner, calls


def turn_files(root, stores, number, prompt, ids):
    put(root, ROLLOUT, b'{"type":"session_meta"}\n' + b'{"type":"turn"}\n' * number)
    put(root, SNAPSHOT, b'# shell snapshot\n')
    put(root, f'logs/openclaw-2026-10-07-{number}.log', b'run log')
    put(root, 'agents/main/sessions/sessions.json', b'{"changed": %d}' % number)
    session_rows(stores, ids['session'], ids['key'], TID, [prompt])


def run(tmp_path, monkeypatch, **options):
    destination, plan, root, stores, workspace = prepared(tmp_path, monkeypatch)
    before_run = options.pop('before_run', None)
    if before_run is not None:
        before_run(workspace, root)
    runner, calls = fake_agent(root, stores, workspace, **options)
    result = execute_openclaw_capture(destination, preflight=ready(workspace), runner=runner, state_settle=_SETTLE)
    return destination, plan, root, workspace, calls, result


def assert_fixture_gone(destination, workspace, result):
    assert result['fixture_removed'] is True and 'FIXTURE_NOT_REMOVED' not in result
    assert not os.path.lexists(workspace / 'fixture_project')
    assert sorted(path.name for path in workspace.iterdir()) == ['AGENTS.md', 'memory']
    assert (workspace / 'AGENTS.md').read_bytes() == b'owner instructions'
    removal = json.loads((destination / 'fixture-removal.json').read_text())
    assert removal['removal']['removed'] is True and removal['removal']['was_present'] is True


def test_prepare_makes_an_offline_plan_and_places_nothing(tmp_path, monkeypatch):
    destination, plan, root, _stores, workspace = prepared(tmp_path, monkeypatch)
    session = plan['session_id']
    assert plan['schema_version'] == 'session-bench-openclaw-survival-capture-v2' and plan['status'] == 'prepared' and plan['model_submissions'] == 0
    assert plan['expected_version'] == 'OpenClaw 2026.9.8 (fc23bc8)' and plan['harness'] == 'codex' and plan['openclaw_home'] == str(root)
    executable = plan['executable']
    assert plan['argv'] == [[executable, 'agent', '--local', '--session-id', session, '--json', '--timeout', '300',
                             '--message-file', str(destination / f'observer/prompt-r{turn}.txt')] for turn in (1, 2)]
    assert plan['argv'] == planned_argv(executable=executable, destination=destination, session_id=session)
    assert plan['argv'][0] == turn_argv(executable, session, destination / 'observer/prompt-r1.txt')
    flat = [word for argv in plan['argv'] for word in argv]
    assert not {'--model', '--verbose', '--agent', '--deliver', '--thinking', '--session-key'} & set(flat)
    assert plan['preflight_argv'] == {'version': [executable, '--version'], 'workspace': [executable, 'config', 'get', 'agents.defaults.workspace']}
    assert plan['environment_keys'] == sorted({'SB_SURVIVAL_V1_RUN_CANARY'} | {key for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if os.environ.get(key)})
    workload = json.loads((destination / 'workload-instance.json').read_text())
    assert (destination / 'observer/prompt-r1.txt').read_text() == workload['turns'][0]['text']
    assert (destination / 'workspaces/before/fixture_project/checkout.py').is_file()
    assert not os.path.lexists(workspace / 'fixture_project') and not (destination / 'preflight.json').exists()
    with pytest.raises(ValueError, match='must be new'):
        prepare_openclaw_capture(destination, repository=tmp_path / 'repo', repetition=1, executable=executable, openclaw_home=root)


def test_prepare_refuses_a_routing_override_a_linked_home_and_a_bad_attempt_id(tmp_path, monkeypatch):
    destination, plan, root, _stores, _workspace = prepared(tmp_path, monkeypatch)
    other = destination.with_name('openclaw-second')
    arguments = {'repository': tmp_path / 'repo', 'repetition': 1, 'executable': plan['executable']}
    (tmp_path / 'linked-home').symlink_to(root)
    with pytest.raises(OpenClawCaptureError, match='not an ordinary directory'):
        prepare_openclaw_capture(other, openclaw_home=tmp_path / 'linked-home', **arguments)
    with pytest.raises(ValueError, match='must start with openclaw-'):
        prepare_openclaw_capture(destination.with_name('hermes-x'), openclaw_home=root, **arguments)
    monkeypatch.setenv('OPENCLAW_STATE_DIR', str(tmp_path / 'elsewhere'))
    with pytest.raises(OpenClawCaptureError, match='storage routing'):
        prepare_openclaw_capture(other, openclaw_home=root, **arguments)
    assert not other.exists()


def test_capture_runs_two_turns_in_one_session_brackets_the_home_and_removes_the_fixture(tmp_path, monkeypatch):
    destination, plan, root, workspace, calls, result = run(tmp_path, monkeypatch)
    assert result['status'] == 'captured_pending_qualification', result
    session = plan['session_id']
    assert result['model_submissions'] == 2 and result['session_id'] == session and result['thread_id'] == TID
    assert (result['provider'], result['model'], result['harness']) == ('openai', 'gpt-5.6-terra', 'codex')
    # Exactly the planned argv, twice the same session, no model flag, no retry.
    assert calls == plan['argv'] and len(calls) == 2 and calls[0][4] == calls[1][4] == session
    assert_fixture_gone(destination, workspace, result)
    assert result['workspace_entries_changed_outside_the_fixture'] == 0
    placement = json.loads((destination / 'fixture-placement.json').read_text())
    assert placement['path'] == str(workspace / 'fixture_project') and placement['placed'] is True
    assert {row['relative_path'] for row in placement['files']} == {'checkout.py', 'bench_check.py', 'snapshots/checkout.before.py', 'snapshots/checkout.after.py'}
    # The fixture as it was at the end is in the run directory.
    assert (destination / 'workspaces/after/fixture_project/checkout.py').read_text() == FIXED
    assert len((destination / 'workspaces/after/fixture_project/.survival-observer.jsonl').read_text().splitlines()) == 3
    before = json.loads((destination / 'state-before.json').read_text())
    for turn in (1, 2):
        prefix = destination / f'turn-r{turn}'
        launch = json.loads((prefix / 'launch.json').read_text())
        assert launch['argv'] == plan['argv'][turn - 1] and launch['cwd'] == str(workspace) and launch['model_override'] is None
        assert (prefix / 'stdout.json').is_file() and (prefix / 'stderr.txt').is_file() and (prefix / 'workspace/fixture_project/bench_check.py').is_file()
        summary = json.loads((prefix / 'envelope-receipt.json').read_text())
        assert summary['session_id'] == session and summary['thread_id'] == TID and summary['tool_events_in_envelope'] is False and 'text' not in summary
        receipt = json.loads((destination / f'r{turn}-native-receipt.json').read_text())
        after = json.loads((destination / f'r{turn}-state-after.json').read_text())
        assert verify_openclaw_state_capture(receipt, destination / f'r{turn}-native', before=before, after=after) is True
        assert receipt['session_id'] == session and receipt['thread_id'] == TID and receipt['turn'] == turn and receipt['build'] == VERSION
        assert set(receipt['classes']['session_owned']) == {ROLLOUT, SNAPSHOT}
        rows = (destination / f'r{turn}-native/session-store-rows.json').read_text()
        assert OTHER not in rows and 'other private' not in rows and 'OWNER-' not in rows
        events = next(table for table in json.loads(rows)['stores'][0]['tables'] if table['table'] == 'transcript_events')
        assert len(events['rows']) == turn and not (destination / f'.r{turn}-native-store-copy').exists()
        assert result['turns'][turn - 1]['tool_summary'] == {'calls': 2, 'tools': ['bash'], 'failures': 0}
    assert not list(destination.glob('r*-refusal.json'))
    # The independent documents of the capture give the envelope observer (no native input).
    ledgers = {turn: (destination / f'turn-r{turn}/workspace/fixture_project/.survival-observer.jsonl').read_bytes() for turn in (1, 2)}
    observer = build_openclaw_envelope_observer(
        workload=json.loads((destination / 'workload-instance.json').read_text()),
        stdout_by_turn={turn: (destination / f'turn-r{turn}/stdout.json').read_bytes() for turn in (1, 2)}, helper_by_turn=ledgers,
        before_sha256=hashlib.sha256((destination / 'workspaces/before/fixture_project/checkout.py').read_bytes()).hexdigest(),
        after_sha256=hashlib.sha256((destination / 'workspaces/after/fixture_project/checkout.py').read_bytes()).hexdigest())
    assert observer['tool_events_observed'] is False and len([row for row in observer['events'] if row['kind'] == 'action']) == 4
    assert {row['session_id'] for row in observer['events']} == {session}
    with pytest.raises(OpenClawCaptureError, match='single-use'):
        execute_openclaw_capture(destination, preflight=ready(workspace), runner=lambda *a, **k: pytest.fail('second run'))


def test_existing_fixture_project_in_the_workspace_is_refused_before_any_model_call(tmp_path, monkeypatch):
    def owner_has_one(workspace, _root):
        put(workspace, 'fixture_project/owner-file.txt', b"the owner's own directory")

    destination, _plan, _root, workspace, calls, result = run(tmp_path, monkeypatch, before_run=owner_has_one)
    assert result['status'] == 'capture_incomplete' and 'fixture_project already exists' in result['failure']
    assert calls == [] and result['model_submissions'] == 0 and result['fixture_placed'] is False and result['fixture_removed'] is None
    # The owner's directory is as it was.
    assert [path.name for path in (workspace / 'fixture_project').iterdir()] == ['owner-file.txt']
    assert not (destination / 'fixture-placement.json').exists() and not (destination / 'state-before.json').exists()


def test_a_dangling_link_named_fixture_project_is_refused_too(tmp_path, monkeypatch):
    destination, _plan, _root, workspace, calls, result = run(
        tmp_path, monkeypatch, before_run=lambda workspace, _root: (workspace / 'fixture_project').symlink_to(workspace / 'absent'))
    assert 'fixture_project already exists' in result['failure'] and calls == [] and (workspace / 'fixture_project').is_symlink()


@pytest.mark.parametrize('change, reason, submissions', [
    (lambda turn, document: (document, 1), 'R1 stopped on command/auth/quota failure', 1),
    (lambda turn, document: (json.dumps({'ok': False, 'status': 'error', 'payloads': [], 'error': {'kind': 'exception'}}), 0), 'reports a failed run', 1),
    (lambda turn, document: ('not json', 0), 'not one JSON object', 1),
    (lambda turn, document: (document.replace('SB_SURVIVAL_V1_RESPONSE', 'SB_OTHER'), 0), 'lacks the exact canary', 1),
    (lambda turn, document: (document.replace('"agentHarnessId": "codex"', '"agentHarnessId": "embedded"'), 0), 'codex harness plugin', 1),
    (lambda turn, document: (document.replace('"fallbackUsed": false', '"fallbackUsed": true'), 0), 'without fallback', 1),
    (lambda turn, document: (document if turn == 1 else document.replace('gpt-5.6-terra', 'gpt-5.4'), 0), 'R2 reports another provider or model', 2),
    (lambda turn, document: (document if turn == 1 else document.replace(TID, OTHER_TID), 0), 'R2 reports another Codex thread', 2),
])
def test_fixture_is_removed_when_a_turn_fails_and_no_turn_is_retried(tmp_path, monkeypatch, change, reason, submissions):
    destination, _plan, _root, workspace, calls, result = run(tmp_path, monkeypatch, change=change)
    assert result['status'] == 'capture_incomplete' and reason in result['failure'], result
    assert len(calls) == submissions == result['model_submissions']
    assert_fixture_gone(destination, workspace, result)
    # The fixture of the failed turn was copied out before it was removed.
    assert (destination / 'workspaces/after/fixture_project/bench_check.py').is_file()
    assert (destination / f'turn-r{submissions}/workspace/fixture_project/bench_check.py').is_file()


def test_second_turn_in_another_session_stops_the_capture(tmp_path, monkeypatch):
    def other_session(turn, document):
        return (document if turn == 1 else document.replace(json.loads(document)['meta']['agentMeta']['sessionId'], OTHER), 0)

    destination, _plan, _root, workspace, calls, result = run(tmp_path, monkeypatch, change=other_session)
    assert result['status'] == 'capture_incomplete' and 'R2 ran in another session' in result['failure'] and len(calls) == 2
    assert_fixture_gone(destination, workspace, result)
    assert (destination / 'r1-native-receipt.json').is_file() and not (destination / 'r2-native-receipt.json').exists()


def test_unexplained_home_change_stops_after_r1_with_a_refusal_listing_and_removes_the_fixture(tmp_path, monkeypatch):
    destination, plan, root, workspace, calls, result = run(
        tmp_path, monkeypatch, extra=lambda _turn: put(tmp_path / 'openclaw-home', 'devices/unknown.bin', b'?'))
    assert result['status'] == 'capture_incomplete' and 'refused' in result['failure'] and 'devices/unknown.bin' in result['failure']
    assert len(calls) == 1 and result['model_submissions'] == 1
    refusal = json.loads((destination / 'r1-refusal.json').read_text())
    assert {row['relative_path']: row['change'] for row in refusal['unexplained']} == {'devices': 'new', 'devices/unknown.bin': 'new'}
    assert refusal['session_id'] == plan['session_id'] and refusal['thread_id'] == TID
    assert (destination / 'r1-state-refused.json').is_file() and not (destination / 'r1-native').exists()
    assert not (destination / '.r1-native-store-copy').exists()
    assert_fixture_gone(destination, workspace, result)


def test_fixture_is_removed_on_an_interrupt(tmp_path, monkeypatch):
    destination, _plan, root, stores, workspace = prepared(tmp_path, monkeypatch)

    def interrupted(argv, *, cwd, env, stdout, stderr, timeout):
        assert (workspace / 'fixture_project/checkout.py').is_file()
        stdout.write_text('')
        stderr.write_text('')
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_openclaw_capture(destination, preflight=ready(workspace), runner=interrupted, state_settle=_SETTLE)
    result = json.loads((destination / 'capture-result.json').read_text())
    assert result['status'] == 'capture_incomplete' and result['failure'] == 'interrupted: KeyboardInterrupt'
    assert_fixture_gone(destination, workspace, result)


def test_a_fixture_that_cannot_be_removed_is_reported_loudly(tmp_path, monkeypatch):
    def replace(turn):
        # Something replaces the placed directory by another one: the controller must not delete what it did not place.
        if turn == 2:
            shutil.move(tmp_path / 'owner-workspace/fixture_project', tmp_path / 'moved-away')
            shutil.copytree(tmp_path / 'moved-away', tmp_path / 'owner-workspace/fixture_project')

    destination, _plan, _root, workspace, _calls, result = run(tmp_path, monkeypatch, extra=replace)
    assert result['status'] == 'capture_incomplete' and result['fixture_removed'] is False
    assert result['FIXTURE_NOT_REMOVED'] == str(workspace / 'fixture_project') and result['failure'].startswith('FIXTURE NOT REMOVED')
    assert 'not the directory that the controller placed' in result['failure'] and (workspace / 'fixture_project').is_dir()


def test_side_effects_in_the_workspace_outside_the_fixture_are_listed_by_metadata(tmp_path, monkeypatch):
    def memory(turn):
        put(tmp_path / 'owner-workspace', 'memory/2026-10-07.md', b'the agent wrote a memory note')

    destination, _plan, _root, workspace, _calls, result = run(tmp_path, monkeypatch, extra=memory)
    assert result['status'] == 'captured_pending_qualification' and result['workspace_entries_changed_outside_the_fixture'] == 2
    removal = json.loads((destination / 'fixture-removal.json').read_text())
    assert [(row['relative_path'], row['change'], row['content_read']) for row in removal['workspace_entries_changed_outside_the_fixture']] == [
        ('memory', 'changed', False), ('memory/2026-10-07.md', 'new', False)]
    assert not os.path.lexists(workspace / 'fixture_project')


@pytest.mark.parametrize('receipt, reason', [
    (lambda workspace, root: {'ready': False, 'failure': '1 other OpenClaw process(es) are running (gateway or agent); stop them first'}, 'other OpenClaw process'),
    (lambda workspace, root: {'ready': True, 'workspace': str(root / 'workspace')}, 'overlap'),
    (lambda workspace, root: {'ready': True, 'workspace': str(root.parent)}, 'overlap'),
    (lambda workspace, root: {'ready': True, 'workspace': str(workspace / 'absent')}, 'not an ordinary directory'),
])
def test_preflight_failure_places_nothing_and_submits_nothing(tmp_path, monkeypatch, receipt, reason):
    destination, _plan, root, _stores, workspace = prepared(tmp_path, monkeypatch)
    result = execute_openclaw_capture(destination, preflight=lambda plan, env: receipt(workspace, root),
                                      runner=lambda *a, **k: pytest.fail('model call escaped the preflight'), state_settle=_SETTLE)
    assert result['status'] == 'capture_incomplete' and reason in result['failure'] and 'no model call submitted' in result['failure']
    assert result['model_submissions'] == 0 and result['fixture_placed'] is False
    assert not os.path.lexists(workspace / 'fixture_project') and not os.path.lexists(root / 'workspace/fixture_project')


def test_real_preflight_reads_version_and_workspace_from_the_cli_and_starts_no_agent(tmp_path, monkeypatch):
    destination, plan, _root, _stores, workspace = prepared(tmp_path, monkeypatch)
    log = tmp_path / 'cli-calls.txt'
    Path(plan['executable']).write_text(
        f'#!/bin/sh\necho "$@" >> "{log}"\n'
        f'if [ "$1" = "--version" ]; then echo "{VERSION}"; exit 0; fi\n'
        f'if [ "$1 $2 $3" = "config get agents.defaults.workspace" ]; then echo "Config warnings: x" >&2; echo "{workspace}"; exit 0; fi\n'
        'exit 9\n')
    monkeypatch.setattr(capture_module, 'openclaw_processes', lambda listing, own_pids=(): [])
    env = {'PATH': os.environ['PATH']}
    receipt = capture_module._preflight(plan, env)
    assert receipt['ready'] is True and receipt['version'] == VERSION and receipt['workspace'] == str(workspace) and receipt['model_submission'] is False
    assert log.read_text().splitlines() == ['--version', 'config get agents.defaults.workspace']
    monkeypatch.setattr(capture_module, 'openclaw_processes', lambda listing, own_pids=(): [{'pid': 1, 'kind': 'gateway'}])
    assert 'other OpenClaw process' in capture_module._preflight(plan, env)['failure']
    assert capture_module._preflight({**plan, 'expected_version': 'OpenClaw 2026.9.5 (ec9c1a1)'}, env)['failure'].endswith('expected version')
    Path(plan['executable']).write_text(f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo "{VERSION}"; exit 0; fi\necho first; echo second\n')
    assert 'exactly one absolute path' in capture_module._preflight(plan, env)['failure']


def test_process_check_keeps_pids_and_kinds_only_and_skips_the_controller_and_its_parents():
    listing = '\n'.join([
        '    1     0 /sbin/launchd',
        '  100     1 /bin/zsh -l',
        '  200   100 python3 scripts/capture_openclaw_survival.py openclaw-2026-10-08-01 --repetition 1 --execute',
        '  300     1 node /opt/homebrew/lib/node_modules/openclaw/openclaw.mjs gateway --port 18789 --token PRIVATE',
        '  301     1 openclaw-gateway',
        '  302   100 /opt/homebrew/bin/openclaw agent --local --message hello',
        '  303     1 /usr/bin/tail -f /synthetic/notes-about-openclaw.txt',
        '  304     1 openclaw',
        '  305     1 vim docs/survival-v1/adapters/openclaw.md artifacts/openclaw-2026-10-08-01',
    ])
    found = openclaw_processes(listing, own_pids=(200,))
    assert found == [{'pid': 300, 'kind': 'gateway'}, {'pid': 301, 'kind': 'gateway'}, {'pid': 302, 'kind': 'agent'}, {'pid': 304, 'kind': 'other'}]
    assert 'PRIVATE' not in json.dumps(found) and openclaw_processes('', own_pids=(1,)) == []


def test_script_prints_the_planned_argv_offline_and_creates_nothing():
    attempt = 'openclaw-print-argv-test'
    env = {key: value for key, value in os.environ.items() if not key.startswith(('OPENCLAW_', 'CLAWDBOT_'))}
    done = subprocess.run([sys.executable, str(REPO / 'scripts/capture_openclaw_survival.py'), attempt, '--repetition', '1', '--print-argv',
                           '--executable', '/synthetic/bin/openclaw'], capture_output=True, text=True, env=env, check=True)
    printed = json.loads(done.stdout)
    assert printed['model_submissions'] == 0 and printed['destination_exists'] is False
    assert printed['turn_1'][:4] == ['/synthetic/bin/openclaw', 'agent', '--local', '--session-id'] and printed['turn_1'][4] == printed['turn_2'][4]
    assert printed['turn_1'][5:9] == ['--json', '--timeout', '300', '--message-file'] and printed['turn_2'][-1].endswith('observer/prompt-r2.txt')
    assert printed['preflight']['workspace'] == ['/synthetic/bin/openclaw', 'config', 'get', 'agents.defaults.workspace']
    assert 'OPENCLAW_' in printed['environment'] and not (REPO / 'artifacts/v1-expanded-preparation/live-captures' / attempt).exists()
