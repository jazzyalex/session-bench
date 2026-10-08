import json
import importlib.util
from pathlib import Path

import pytest

from session_bench.antigravity_root_evidence import (
    RootClosureError, capture_session_family, inventory, session_files, verify_copied_family,
)


PRIMARY = 'synthetic-01/.system_generated/logs/transcript.jsonl'


def _session(root):
    path = root / PRIMARY
    path.parent.mkdir(parents=True)
    path.write_text('synthetic transcript\n')
    (root / 'synthetic-01' / 'sidecar.bin').write_bytes(b'sidecar')


def test_captures_all_session_files_and_rechecks_second_turn(tmp_path):
    root = tmp_path / 'brain'
    root.mkdir()
    old = root / 'old-session' / 'private.txt'
    old.parent.mkdir()
    old.write_text('pre-existing private contents')
    before = inventory(root)
    _session(root)
    after = inventory(root)
    receipt = capture_session_family(root, tmp_path / 'r1', before, after, PRIMARY, sleep=lambda _: None)
    assert receipt['external_companions_established'] is False
    assert {row['relative_path'] for row in receipt['artifacts']} == {PRIMARY, 'synthetic-01/sidecar.bin'}
    assert verify_copied_family(receipt, tmp_path / 'r1')
    assert not (tmp_path / 'r1' / 'old-session').exists()

    (root / PRIMARY).write_text('synthetic continuation\n')
    (root / 'synthetic-01' / 'second-turn.bin').write_bytes(b'new')
    after2 = inventory(root)
    receipt2 = capture_session_family(root, tmp_path / 'r2', before, after2, PRIMARY, sleep=lambda _: None)
    assert len(receipt2['artifacts']) == 3
    assert verify_copied_family(json.loads(json.dumps(receipt2)), tmp_path / 'r2')


@pytest.mark.parametrize('change', ['new_file', 'old_file', 'removed_file', 'empty_directory'])
def test_rejects_unexplained_outside_change_before_copy(tmp_path, change):
    root = tmp_path / 'brain'
    root.mkdir()
    old = root / 'old.txt'
    old.write_text('private')
    before = inventory(root)
    _session(root)
    if change == 'new_file':
        (root / 'other.txt').write_text('x')
    elif change == 'old_file':
        old.write_text('changed')
    elif change == 'removed_file':
        old.unlink()
    else:
        (root / 'empty').mkdir()
    with pytest.raises(RootClosureError):
        session_files(before, inventory(root), PRIMARY)
    assert not (tmp_path / 'copy').exists()


def test_rejects_quiescence_change_and_tampered_copy(tmp_path):
    root = tmp_path / 'brain'
    root.mkdir()
    before = inventory(root)
    _session(root)
    after = inventory(root)

    def mutate(_):
        (root / 'other.txt').write_text('outside')

    with pytest.raises(RootClosureError):
        capture_session_family(root, tmp_path / 'copy', before, after, PRIMARY, sleep=mutate)
    assert not (tmp_path / 'copy').exists()
    (root / 'other.txt').unlink()
    after = inventory(root)
    receipt = capture_session_family(root, tmp_path / 'copy', before, after, PRIMARY, sleep=lambda _: None)
    (tmp_path / 'copy' / PRIMARY).write_text('altered')
    with pytest.raises(RootClosureError):
        verify_copied_family(receipt, tmp_path / 'copy')
    (tmp_path / 'copy' / PRIMARY).write_text('synthetic transcript\n')
    (tmp_path / 'copy' / 'extra').write_text('extra')
    with pytest.raises(RootClosureError, match='extra files'):
        verify_copied_family(receipt, tmp_path / 'copy')


def test_rejects_preexisting_session_and_symlink(tmp_path):
    root = tmp_path / 'brain'
    root.mkdir()
    _session(root)
    before = inventory(root)
    with pytest.raises(RootClosureError, match='existed before'):
        session_files(before, inventory(root), PRIMARY)
    (root / 'link').symlink_to(root / PRIMARY)
    with pytest.raises(RootClosureError, match='symlink'):
        inventory(root)


def test_runner_pins_sonnet_medium_for_new_and_resumed_turns():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'run_antigravity_survival.py'
    spec = importlib.util.spec_from_file_location('antigravity_runner_under_test', path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    selection = runner.requested_selection()
    # agy 1.2.16 rejects --effort for this model; the flag is omitted.
    assert selection == {'requested_model': 'claude-sonnet-4-6', 'requested_effort': None}
    for first in (True, False):
        args = runner.turn_command(Path('/synthetic/agy'), 'synthetic prompt',
                                   Path('/synthetic/workspace'), PRIMARY, first)
        assert args[args.index('--model') + 1] == selection['requested_model']
        assert '--effort' not in args
        assert ('--new-project' in args) is first
        if not first:
            assert args[args.index('--conversation') + 1] == 'synthetic-01'
