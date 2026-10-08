#!/usr/bin/env python3
"""Bracket one Cursor Desktop benchmark run in an isolated profile.

`before` prepares the synthetic workspace and snapshots the whole isolated
profile. `after` snapshots it again and copies the files that Cursor wrote for
this workspace under the shared `~/.cursor/projects` root. Cursor must not run
on the isolated profile at either point, so that its databases are at rest.
The shared `~/.cursor` root is inventoried by metadata only; no file of
another project is opened.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.workload_instance import instantiate_workload

PROFILE = ROOT / 'artifacts/cdp'
SHARED = Path.home() / '.cursor'
PROTECTED = ('checkout.py', 'bench_check.py')
# A distinctive folder name, so the window title cannot be mistaken for another project.
WORKSPACE = 'SESSION-BENCH-TEST'


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=1, sort_keys=True)
        stream.write('\n')


def profile_running():
    listing = subprocess.run(['pgrep', '-fl', 'Cursor'], capture_output=True, text=True).stdout
    return any(f'--user-data-dir {PROFILE / "ud"}' in line or f'--user-data-dir={PROFILE / "ud"}' in line for line in listing.splitlines())


def metadata_inventory(root):
    """Relative path, kind, size, times and inode of every entry; nothing is opened."""
    entries = []
    for folder, names, files in os.walk(root, followlinks=False):
        for name in sorted(names + files):
            path = Path(folder) / name
            status = path.lstat()
            kind = 'symlink' if path.is_symlink() else 'directory' if path.is_dir() else 'file'
            entries.append({'relative_path': path.relative_to(root).as_posix(), 'kind': kind, 'size_bytes': status.st_size,
                            'mtime_ns': status.st_mtime_ns, 'ctime_ns': status.st_ctime_ns, 'inode': status.st_ino})
    return {'root': 'CURSOR_SHARED_HOME', 'created_ns': time.time_ns(), 'entries': sorted(entries, key=lambda row: row['relative_path'])}


def snapshot_profile(target):
    shutil.copytree(PROFILE / 'ud', target / 'ud', symlinks=True, ignore=shutil.ignore_patterns('*.sock'))
    shutil.copytree(PROFILE / 'ext', target / 'ext', symlinks=True)


def before(attempt, repetition):
    if profile_running(): raise SystemExit('quit the isolated Cursor instance first')
    run = ROOT / 'artifacts/survival-v1-runs' / attempt
    run.mkdir(parents=True)
    template = ROOT / 'fixtures/scenarios/survival-v1/workload'
    shutil.copytree(template / 'fixture_project', run / WORKSPACE / 'fixture_project')
    workload, _ = instantiate_workload(json.loads((template / 'workload.json').read_text()), attempt)
    write(run / 'workload-instance.json', workload)
    fixture = run / WORKSPACE / 'fixture_project'
    build = json.loads(Path('/Applications/Cursor.app/Contents/Resources/app/package.json').read_text()).get('version')
    snapshot_profile(run / 'state-before')
    write(run / 'shared-home-before.json', metadata_inventory(SHARED))
    write(run / 'attempt.json', {'schema_version': 'session-bench-cursor-desktop-capture-v1', 'attempt_id': attempt, 'repetition': repetition,
                                 'configuration_id': 'cursor-desktop', 'build': build, 'profile': 'isolated user-data-dir and extensions-dir',
                                 'workspace': str(run / WORKSPACE), 'started_ns': time.time_ns(), 'state': 'prepared',
                                 'protected_before': {name: sha(fixture / name) for name in PROTECTED}, 'score_eligible': False})
    print(json.dumps({'attempt_id': attempt, 'state': 'prepared', 'workspace': str(run / WORKSPACE),
                      'turns': {turn['id']: turn['text'] for turn in workload['turns']}}))


def after(attempt):
    if profile_running(): raise SystemExit('quit the isolated Cursor instance first')
    run = ROOT / 'artifacts/survival-v1-runs' / attempt
    attempt_record = json.loads((run / 'attempt.json').read_text())
    fixture = run / WORKSPACE / 'fixture_project'
    snapshot_profile(run / 'state-after')
    inventory = metadata_inventory(SHARED)
    write(run / 'shared-home-after.json', inventory)
    old = {row['relative_path']: row for row in json.loads((run / 'shared-home-before.json').read_text())['entries']}
    changed = [row for row in inventory['entries'] if old.get(row['relative_path']) != row]
    removed = sorted(set(old) - {row['relative_path'] for row in inventory['entries']})
    key = ''.join(char if char.isalnum() else '-' for char in str(run / WORKSPACE)).strip('-')
    owned = [row for row in changed if row['relative_path'].startswith(f'projects/{key}/') or row['relative_path'] == f'projects/{key}']
    copied = []
    for row in owned:
        if row['kind'] != 'file': continue
        target = run / 'shared-home-owned' / row['relative_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SHARED / row['relative_path'], target, follow_symlinks=False)
        copied.append({'relative_path': row['relative_path'], 'size_bytes': target.stat().st_size, 'sha256': sha(target)})
    ledger = fixture / '.survival-observer.jsonl'
    result = {'schema_version': 'session-bench-cursor-desktop-capture-result-v1', 'attempt_id': attempt, 'finished_ns': time.time_ns(),
              'project_key': key, 'shared_home_changed': [row['relative_path'] for row in changed if row not in owned],
              'shared_home_removed': removed, 'shared_home_owned_copied': copied,
              'protected_after': {name: sha(fixture / name) for name in PROTECTED},
              'helper_ledger': [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else [],
              'protected_before': attempt_record['protected_before'], 'state': 'captured_private_unqualified'}
    write(run / 'capture-result.json', result)
    print(json.dumps({'attempt_id': attempt, 'owned_files': len(copied), 'other_shared_changes': len(result['shared_home_changed']),
                      'ledger': [(row.get('phase'), row.get('exit_code')) for row in result['helper_ledger']],
                      'checkout_changed': result['protected_after']['checkout.py'] != attempt_record['protected_before']['checkout.py'],
                      'helper_unchanged': result['protected_after']['bench_check.py'] == attempt_record['protected_before']['bench_check.py']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('before', 'after'))
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--repetition', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    before(args.attempt_id, args.repetition) if args.phase == 'before' else after(args.attempt_id)
