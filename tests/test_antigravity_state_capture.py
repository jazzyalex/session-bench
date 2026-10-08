"""Whole-state-directory capture: every changed entry is classified, shared stores are never read."""
import builtins
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import time

import pytest

from session_bench.antigravity_root_evidence import (
    RootClosureError, capture_state_family, classify_state_changes, inventory_state, verify_state_capture,
)

CID = '11111111-2222-3333-4444-555555555555'
OTHER = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
PRIMARY = f'brain/{CID}/.system_generated/logs/transcript.jsonl'
SHARED = ('conversation_summaries.db', 'history.jsonl', 'jetbox_summaries_proto.pb', 'settings.json',
          'updater/update_status.json', 'last_check.timestamp', 'builtin/skills/demo/SKILL.md')


def put(root, name, data=b'x'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def state(tmp_path):
    """A state directory with the operator's older conversations and shared stores."""
    root = tmp_path / 'antigravity-cli'
    put(root, f'brain/{OTHER}/.system_generated/logs/transcript.jsonl', b'private old transcript\n')
    put(root, f'conversations/{OTHER}.db', b'private old conversation')
    put(root, 'log/cli-20260101-000000.log', b'old log')
    put(root, 'installation_id', b'private id')
    for name in SHARED:
        put(root, name, b'private shared store ' + name.encode())
    (root / 'cli.log').symlink_to('log/cli-20260101-000000.log')
    return root


def turn(root, *, text=b'{"step_index":0}\n', log='log/cli-20261004-200000.log'):
    """What one agy turn writes: session files, run files, and changes to shared stores."""
    put(root, PRIMARY, text)
    put(root, f'brain/{CID}/.system_generated/steps/2/output.txt', b'out')
    (root / f'brain/{CID}/scratch').mkdir(exist_ok=True)
    put(root, f'conversations/{CID}.db', b'SQLite format 3\x00 main ' + text)
    put(root, f'conversations/{CID}.db-wal', b'wal ' + text)
    put(root, f'conversations/{CID}.db-shm', b'shm')
    put(root, f'presence/{CID}.lock', b'')
    put(root, f'annotations/{CID}.pbtxt', b'note')
    put(root, log, b'run log with personal data')
    put(root, 'implicit/99999999-8888-7777-6666-555555555555.pb', b'implicit')
    for name in SHARED:
        put(root, name, b'changed during the run ' + text)
    (root / 'cli.log').unlink()
    (root / 'cli.log').symlink_to(log)


def capture(root, destination, before, started, **options):
    return capture_state_family(root, destination, before, attempt_id='test-run', turn=options.pop('turn', 1),
                                started_ns=started, sleep=lambda _: None, **options)


def test_inventory_covers_the_whole_tree_and_records_a_symlink_without_following_it(tmp_path):
    root = state(tmp_path)
    entries = {row['relative_path']: row for row in inventory_state(root)['entries']}
    assert {'brain', 'conversations', f'conversations/{OTHER}.db', 'cli.log', 'settings.json', 'builtin/skills/demo/SKILL.md'} <= set(entries)
    link = entries['cli.log']
    assert link['kind'] == 'symlink' and link['target_sha256'] == hashlib.sha256(b'log/cli-20260101-000000.log').hexdigest()
    assert 'target' not in link and entries['settings.json']['kind'] == 'file'
    # A link to a directory is one entry. Its target is not walked.
    (root / 'outside').symlink_to(tmp_path)
    assert not [name for name in (row['relative_path'] for row in inventory_state(root)['entries']) if name.startswith('outside/')]


def test_inventory_and_capture_never_open_a_preexisting_or_shared_file(tmp_path, monkeypatch):
    root = state(tmp_path)
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
    before = inventory_state(root)
    assert opened == []
    started = time.time_ns()
    turn(root)
    opened.clear()   # the writes of the simulated turn are not reads of the capture
    receipt = capture(root, tmp_path / 'copy', before, started)
    source = [Path(name).relative_to(root).as_posix() for name in opened if Path(name).is_relative_to(root)]
    assert source and all(CID in name or name in receipt['classes']['run_owned'] for name in source)
    assert not [name for name in source if name in SHARED or OTHER in name or name == 'installation_id']


def test_every_changed_entry_is_classified_and_session_and_run_files_are_copied(tmp_path):
    root = state(tmp_path)
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    receipt = capture(root, tmp_path / 'copy', before, started)
    assert receipt['schema_version'] == 'antigravity-state-root-v1' and receipt['conversation_id'] == CID
    assert receipt['scope'] == 'whole_cli_state_directory' and receipt['primary_path'] == PRIMARY
    classes = receipt['classes']
    assert set(classes['session_owned']) == {
        PRIMARY, f'brain/{CID}/.system_generated/steps/2/output.txt', f'conversations/{CID}.db',
        f'conversations/{CID}.db-wal', f'conversations/{CID}.db-shm', f'presence/{CID}.lock', f'annotations/{CID}.pbtxt'}
    assert set(classes['run_owned']) == {'log/cli-20261004-200000.log', 'implicit/99999999-8888-7777-6666-555555555555.pb'}
    shared = {row['relative_path']: row for row in classes['shared_changed']}
    assert set(shared) == set(SHARED) | {'cli.log'}
    assert all(row['content_read'] is False and row['before']['metadata_sha256'] != row['after']['metadata_sha256'] for row in shared.values())
    assert shared['cli.log']['kind'] == 'symlink' and 'sha256' not in shared['settings.json']['after']
    accounting = receipt['accounting']
    assert accounting['every_changed_entry_accounted'] is True and accounting['unexplained'] == []
    assert accounting['classes_found'] == ['session_owned', 'run_owned', 'shared_changed']
    assert accounting['shared_stores_changed_content_not_read'] == sorted(shared)
    assert 'external_companions_established' not in receipt
    # Copies: session files are public candidates, run files are private only.
    artifacts = {row['relative_path']: row for row in receipt['artifacts']}
    assert set(artifacts) == set(classes['session_owned']) | set(classes['run_owned'])
    for name, row in artifacts.items():
        owned = name in classes['session_owned']
        assert row['class'] == ('session_owned' if owned else 'run_owned') and row['private_only'] is (not owned)
        data = (tmp_path / 'copy' / row['copied_path']).read_bytes()
        assert data == (root / name).read_bytes() and row['sha256'] == hashlib.sha256(data).hexdigest() and row['size_bytes'] == len(data)
        assert row['filesystem_id'] == f'{(root / name).lstat().st_dev}:{(root / name).lstat().st_ino}'
    assert artifacts[PRIMARY]['role'] == 'native-primary'
    # The SQLite database travels with its WAL and SHM files.
    assert receipt['sqlite_groups'] == [{'database': f'conversations/{CID}.db',
                                         'members': [f'conversations/{CID}.db', f'conversations/{CID}.db-shm', f'conversations/{CID}.db-wal']}]
    assert f'brain/{CID}/scratch' in receipt['session_directories']
    assert not (tmp_path / 'copy' / 'settings.json').exists() and not list((tmp_path / 'copy').rglob('*' + OTHER + '*'))
    assert receipt['quiescence']['stable'] is True and len(set(receipt['quiescence']['inventory_sha256'][-2:])) == 1
    assert receipt['quiescence']['inventory_sha256'][-1] == receipt['after_inventory_sha256']
    after = inventory_state(root)
    assert verify_state_capture(receipt, tmp_path / 'copy', before=before, after=after)
    # Second turn: same conversation, the family is copied again.
    turn(root, text=b'{"step_index":0}\n{"step_index":8}\n')
    second = capture(root, tmp_path / 'copy2', before, started, turn=2, conversation_id=CID)
    assert second['turn'] == 2 and (tmp_path / 'copy2' / 'session-owned' / PRIMARY).read_bytes().endswith(b'{"step_index":8}\n')
    with pytest.raises(RootClosureError, match='conversation'):
        capture(root, tmp_path / 'copy3', before, started, turn=2, conversation_id=OTHER)


@pytest.mark.parametrize('change, message', [
    (lambda root: put(root, 'knowledge/new-note.md'), 'unexplained'),
    (lambda root: put(root, f'brain/{OTHER}/.system_generated/logs/transcript.jsonl', b'another conversation moved'), 'another conversation'),
    (lambda root: put(root, f'conversations/{OTHER}.db', b'changed'), 'another conversation'),
    (lambda root: (root / 'installation_id').unlink(), 'unexplained'),
    (lambda root: put(root, 'log/not-a-cli-log.txt'), 'unexplained'),
    (lambda root: put(root, f'brain/{OTHER[:-1]}0/.system_generated/logs/transcript.jsonl'), 'exactly one new conversation'),
    (lambda root: (root / f'brain/{CID}/link').symlink_to('/etc/hosts'), 'symlink'),
])
def test_an_unexplained_or_ambiguous_change_fails_closed_and_leaves_no_copy(tmp_path, change, message):
    root = state(tmp_path)
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    change(root)
    with pytest.raises(RootClosureError, match=message):
        capture(root, tmp_path / 'copy', before, started)
    assert not (tmp_path / 'copy').exists()


def test_a_run_file_must_be_born_inside_the_capture_window(tmp_path):
    root = state(tmp_path)
    before = inventory_state(root)
    turn(root)
    with pytest.raises(RootClosureError, match='before the capture started'):
        capture(root, tmp_path / 'copy', before, time.time_ns() + 60 * 10**9)


def test_sidecars_of_a_shared_store_are_shared_and_not_copied(tmp_path):
    root = state(tmp_path)
    put(root, 'history.jsonl-journal', b'old journal')
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    put(root, 'conversation_summaries.db-wal', b'private wal')
    (root / 'history.jsonl-journal').unlink()
    receipt = capture(root, tmp_path / 'copy', before, started)
    shared = {row['relative_path']: row for row in receipt['classes']['shared_changed']}
    assert shared['conversation_summaries.db-wal']['change'] == 'created_sidecar' and shared['conversation_summaries.db-wal']['before'] is None
    assert shared['history.jsonl-journal']['change'] == 'removed_sidecar' and shared['history.jsonl-journal']['after'] is None
    assert shared['settings.json']['change'] == 'modified'
    assert not list((tmp_path / 'copy').rglob('conversation_summaries*'))


def test_capture_waits_for_two_equal_reads_and_fails_when_the_tree_never_rests(tmp_path):
    root = state(tmp_path)
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    writes = []

    def late_write(_):
        if len(writes) < 2:
            writes.append(1)
            put(root, 'history.jsonl', b'late write %d' % len(writes))

    receipt = capture_state_family(root, tmp_path / 'copy', before, attempt_id='test-run', turn=1, started_ns=started, sleep=late_write)
    assert receipt['quiescence']['reads'] == 3 and receipt['quiescence']['stable'] is True

    def always(_):
        writes.append(1)
        put(root, 'history.jsonl', b'write %d' % len(writes))

    with pytest.raises(RootClosureError, match='did not come to rest'):
        capture_state_family(root, tmp_path / 'copy2', before, attempt_id='test-run', turn=1, started_ns=started, sleep=always)
    assert not (tmp_path / 'copy2').exists()


def test_verification_rejects_a_changed_copy_inventory_or_claim(tmp_path):
    root = state(tmp_path)
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    receipt = capture(root, tmp_path / 'copy', before, started)
    after = inventory_state(root)
    altered = json.loads(json.dumps(receipt))
    altered['classes']['shared_changed'] = altered['classes']['shared_changed'][1:]
    with pytest.raises(RootClosureError, match='classification'):
        verify_state_capture(altered, tmp_path / 'copy', before=before, after=after)
    with pytest.raises(RootClosureError, match='inventory'):
        verify_state_capture(receipt, tmp_path / 'copy', before={**before, 'entries': before['entries'][1:]}, after=after)
    (tmp_path / 'copy' / 'session-owned' / f'conversations/{CID}.db-wal').write_bytes(b'altered')
    with pytest.raises(RootClosureError, match='copied file'):
        verify_state_capture(receipt, tmp_path / 'copy', before=before, after=after)


def test_classification_is_pure_metadata(tmp_path):
    root = state(tmp_path)
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    after = inventory_state(root)
    for name in SHARED:
        (root / name).chmod(0)   # unreadable: the classifier needs no content
    try:
        result = classify_state_changes(before, after, started_ns=started)
    finally:
        for name in SHARED:
            (root / name).chmod(0o600)
    assert result['conversation_id'] == CID and len(result['shared_changed']) == len(SHARED) + 1


def test_controller_inventories_the_whole_state_directory_and_keeps_its_flags():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'run_antigravity_survival.py'
    spec = importlib.util.spec_from_file_location('antigravity_runner_state', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    assert runner.STATE_ROOT == Path.home() / '.gemini/antigravity-cli'
    args = runner.turn_command(Path('/synthetic/agy'), 'prompt', Path('/synthetic/workspace'), CID, False)
    assert args[args.index('--conversation') + 1] == CID and '--sandbox' not in args and '--effort' not in args
    source = path.read_text()
    assert 'capture_state_family' in source and 'inventory_state' in source and "'.gemini/antigravity-cli/brain'" not in source


def test_an_old_empty_directory_that_was_only_touched_is_recorded_and_not_read(tmp_path):
    root = state(tmp_path)
    (root / 'crashes').mkdir()
    before = inventory_state(root)
    started = time.time_ns()
    turn(root)
    put(root, 'crashes/transient.dmp').unlink()
    receipt = capture(root, tmp_path / 'copy', before, started)
    touched = [row for row in receipt['classes']['shared_changed'] if row['relative_path'] == 'crashes']
    assert [(row['kind'], row['change'], row['content_read']) for row in touched] == [('directory', 'touched_empty', False)]

    put(root, 'crashes/kept.dmp')
    with pytest.raises(RootClosureError, match='crashes'):
        classify_state_changes(before, inventory_state(root), started_ns=started)
