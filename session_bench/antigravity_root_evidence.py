"""Metadata-only Antigravity root boundary for a newly created session directory.

The root inventory never opens file contents. Only files under the isolated,
new session directory are opened, after the entire root has passed closure.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import time

from .normal_root_capture import NewFileFamily, NormalRootFile, copy_verified_family


class RootClosureError(ValueError):
    pass


def _metadata(info):
    birth = getattr(info, 'st_birthtime', None)
    return {'device': info.st_dev, 'inode': info.st_ino,
            'size_bytes': info.st_size, 'birth_ns': None if birth is None else int(birth * 1_000_000_000),
            'ctime_ns': info.st_ctime_ns, 'mtime_ns': info.st_mtime_ns}


def inventory(root):
    """Inventory all directory and file entries without reading any file bytes."""
    root = Path(root).absolute()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RootClosureError('native root must be an ordinary directory')
    entries = []

    def visit(folder):
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for child in children:
            data = child.stat(follow_symlinks=False)
            if stat.S_ISDIR(data.st_mode):
                kind = 'directory'
            elif stat.S_ISREG(data.st_mode):
                kind = 'file'
            else:
                raise RootClosureError('native root has symlink or special entry')
            relative = Path(child.path).relative_to(root).as_posix()
            entries.append({'relative_path': relative, 'kind': kind, **_metadata(data)})
            if kind == 'directory':
                visit(child.path)

    visit(root)
    return {'root': str(root), 'root_identity': {'device': info.st_dev, 'inode': info.st_ino},
            'entries': entries, 'metadata_only': True}


def _by_path(snapshot):
    if snapshot.get('metadata_only') is not True:
        raise RootClosureError('inventory is not metadata-only')
    entries = snapshot.get('entries')
    if not isinstance(entries, list):
        raise RootClosureError('inventory entries missing')
    result = {}
    for entry in entries:
        path = entry.get('relative_path') if isinstance(entry, dict) else None
        if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or any(
                part in ('', '.', '..') for part in path.split('/')) or path in result:
            raise RootClosureError('invalid or duplicate inventory path')
        if entry.get('kind') not in ('directory', 'file'):
            raise RootClosureError('invalid inventory entry kind')
        result[path] = entry
    return result


def session_files(before, after, primary_path):
    """Prove exact root closure around one new top-level session directory."""
    old, new = _by_path(before), _by_path(after)
    if before.get('root') != after.get('root') or before.get('root_identity') != after.get('root_identity'):
        raise RootClosureError('native root identity changed')
    parts = primary_path.split('/') if isinstance(primary_path, str) else []
    if len(parts) < 4 or parts[1:] != ['.system_generated', 'logs', 'transcript.jsonl']:
        raise RootClosureError('invalid Antigravity primary path')
    session = parts[0]
    if session in old or any(path.startswith(session + '/') for path in old):
        raise RootClosureError('session directory existed before capture')
    if new.get(session, {}).get('kind') != 'directory' or new.get(primary_path, {}).get('kind') != 'file':
        raise RootClosureError('new session directory or primary transcript missing')
    for path, entry in old.items():
        if new.get(path) != entry:
            raise RootClosureError(f'pre-existing native entry changed or disappeared: {path}')
    for path in new.keys() - old.keys():
        if path != session and not path.startswith(session + '/'):
            raise RootClosureError(f'new native entry outside session directory: {path}')
    files = [new[path] for path in sorted(new) if path.startswith(session + '/') and new[path]['kind'] == 'file']
    if not files:
        raise RootClosureError('session directory has no files')
    identities = [(item['device'], item['inode']) for item in files]
    if len(set(identities)) != len(identities):
        raise RootClosureError('session files share a filesystem identity')
    old_ids = {(item['device'], item['inode']) for item in old.values()}
    if any(identity in old_ids for identity in identities):
        raise RootClosureError('session file reuses a pre-existing identity')
    return session, files


def _as_file(entry):
    return NormalRootFile(entry['relative_path'], entry['device'], entry['inode'],
                          entry['size_bytes'], entry['birth_ns'], entry['ctime_ns'], entry['mtime_ns'])


def capture_session_family(root, destination, before, after, primary_path, *, checks=2,
                           interval_seconds=0.1, sleep=time.sleep, attempt_id=None, turn=None):
    """Quiesce the entire root boundary, then copy every session file exactly."""
    session, files = session_files(before, after, primary_path)
    if type(checks) is not int or checks < 2 or interval_seconds < 0:
        raise RootClosureError('at least two quiescence checks are required')
    observed = []
    for _ in range(checks):
        sleep(interval_seconds)
        current = inventory(root)
        _, current_files = session_files(before, current, primary_path)
        if current != after or current_files != files:
            raise RootClosureError('native root changed during quiescence')
        observed.append(current)
    primary = _as_file(next(item for item in files if item['relative_path'] == primary_path))
    companions = tuple(_as_file(item) for item in files if item['relative_path'] != primary_path)
    artifacts = copy_verified_family(root, destination, NewFileFamily(primary, companions))
    try:
        unchanged = inventory(root) == after
    except Exception:
        shutil.rmtree(destination)
        raise
    if not unchanged:
        shutil.rmtree(destination)
        raise RootClosureError('native root changed during copy')
    directory = next(item for item in after['entries'] if item['relative_path'] == session)
    return {'schema_version': 'antigravity-session-root-v1', 'source_root': str(Path(root).absolute()),
            'attempt_id': attempt_id, 'turn': turn, 'session_directory': session,
            'session_identity': {'device': directory['device'], 'inode': directory['inode']},
            'primary_path': primary_path,
            'scope': 'complete_new_session_directory_within_inventoried_brain_root',
            'external_companions_established': False, 'metadata_only_before_after': True,
            'preexisting_contents_opened': False, 'before_inventory': before, 'after_inventory': after,
            'quiescence': {'checks': checks, 'interval_seconds': interval_seconds, 'stable': True,
                           'observed': observed}, 'artifacts': list(artifacts)}


def verify_copied_family(receipt, destination):
    """Offline check of a receipt and its private copied session directory."""
    session, files = session_files(receipt['before_inventory'], receipt['after_inventory'], receipt['primary_path'])
    if (receipt.get('schema_version') != 'antigravity-session-root-v1'
            or receipt.get('session_directory') != session
            or receipt.get('source_root') != receipt['before_inventory'].get('root')
            or receipt.get('scope') != 'complete_new_session_directory_within_inventoried_brain_root'):
        raise RootClosureError('receipt session binding differs')
    directory = next(item for item in receipt['after_inventory']['entries'] if item['relative_path'] == session)
    if receipt.get('session_identity') != {'device': directory['device'], 'inode': directory['inode']}:
        raise RootClosureError('session directory identity differs')
    if receipt.get('external_companions_established') is not False or receipt.get('preexisting_contents_opened') is not False:
        raise RootClosureError('receipt overclaims its privacy or scope')
    quiet = receipt['quiescence']
    if quiet.get('stable') is not True or quiet.get('checks', 0) < 2 or len(quiet.get('observed', [])) != quiet['checks']:
        raise RootClosureError('quiescence is incomplete')
    for snapshot in quiet['observed']:
        if snapshot != receipt['after_inventory']:
            raise RootClosureError('quiescence inventory differs')
    expected = {item['relative_path']: item for item in files}
    artifacts = receipt['artifacts']
    if len(artifacts) != len(expected) or {row['relative_path'] for row in artifacts} != set(expected):
        raise RootClosureError('copied family differs from session inventory')
    copied = inventory(destination)
    copied_files = {entry['relative_path'] for entry in copied['entries'] if entry['kind'] == 'file'}
    if copied_files != set(expected):
        raise RootClosureError('copied directory contains missing or extra files')
    for row in artifacts:
        source = expected[row['relative_path']]
        role = 'native-primary' if row['relative_path'] == receipt['primary_path'] else 'native-companion'
        if row.get('role') != role:
            raise RootClosureError('copied file role differs')
        if row['size_bytes'] != source['size_bytes'] or row['filesystem_id'] != f"{source['device']}:{source['inode']}":
            raise RootClosureError('copied file identity differs')
        target = Path(destination) / row['relative_path']
        if target.is_symlink() or not target.is_file() or target.stat().st_size != source['size_bytes']:
            raise RootClosureError('copied file missing or changed')
        if hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
            raise RootClosureError('copied file hash differs')
    return True


# --- Whole CLI state directory (``~/.gemini/antigravity-cli``), receipt ``antigravity-state-root-v1`` ---
#
# The first receipt type above lists ``brain/`` only. An independent review
# rejected it: the CLI keeps more state beside ``brain/``. The functions below
# list the whole state directory and account for every entry that is new,
# changed or removed during a capture. Listing uses metadata only. A file is
# opened only when its path carries the new conversation id, or when it is a
# new log or implicit file born during the capture.
import json
import re

STATE_SCHEMA = 'antigravity-state-root-v1'
STATE_SCOPE = 'whole_cli_state_directory'
STATE_PRIMARY = 'brain/{id}/.system_generated/logs/transcript.jsonl'
SESSION_COPY, RUN_COPY = 'session-owned', 'run-owned-private'
# Directories that hold one entry per conversation. A changed old file here belongs to another conversation.
CONVERSATION_AREAS = frozenset({'brain', 'conversations', 'presence', 'annotations'})
# New files that a turn creates without the conversation id in their path.
RUN_OWNED_PATTERNS = (re.compile(r'log/cli-[^/]+\.log'),
                      re.compile(r'implicit/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.pb'))
SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal')
_NEW_PRIMARY = re.compile(r'brain/([^/]+)/\.system_generated/logs/transcript\.jsonl')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def inventory_sha256(snapshot):
    return hashlib.sha256(_canonical(snapshot)).hexdigest()


def inventory_state(root):
    """Metadata of every entry under the state directory. No file is opened and no link is followed.

    A symbolic link is one entry of kind ``symlink``. Its target string is
    hashed, not stored, and the target is never visited.
    """
    root = Path(root).absolute()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise RootClosureError('state root must be an ordinary directory')
    entries = []

    def visit(folder):
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for child in children:
            data = child.stat(follow_symlinks=False)
            relative = Path(child.path).relative_to(root).as_posix()
            entry = {'relative_path': relative, **_metadata(data)}
            if stat.S_ISLNK(data.st_mode):
                entry.update(kind='symlink', target_sha256=hashlib.sha256(os.readlink(child.path).encode('utf-8', 'surrogateescape')).hexdigest())
            elif stat.S_ISDIR(data.st_mode):
                entry['kind'] = 'directory'
            elif stat.S_ISREG(data.st_mode):
                entry['kind'] = 'file'
            else:
                entry['kind'] = 'other'
            entries.append(entry)
            if entry['kind'] == 'directory':
                visit(child.path)

    visit(root)
    return {'root': str(root), 'root_identity': {'device': info.st_dev, 'inode': info.st_ino},
            'entries': entries, 'metadata_only': True, 'scope': STATE_SCOPE}


def _state_entries(snapshot):
    if snapshot.get('metadata_only') is not True or snapshot.get('scope') != STATE_SCOPE or not isinstance(snapshot.get('entries'), list):
        raise RootClosureError('not a whole-state metadata inventory')
    result = {}
    for entry in snapshot['entries']:
        path = entry.get('relative_path') if isinstance(entry, dict) else None
        if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/')) or path in result
                or entry.get('kind') not in ('directory', 'file', 'symlink', 'other')):
            raise RootClosureError('invalid or duplicate inventory path')
        result[path] = entry
    return result


def carries_conversation_id(path, conversation_id):
    """True when one path component is the id, or starts with it and continues with a separator character."""
    for part in path.split('/'):
        if part == conversation_id or (part.startswith(conversation_id) and not part[len(conversation_id)].isalnum()):
            return True
    return False


def _summary(entry):
    return None if entry is None else {'size_bytes': entry['size_bytes'], 'metadata_sha256': hashlib.sha256(_canonical(entry)).hexdigest()}


def _sidecar_of(path, known):
    return next((path[:-len(suffix)] for suffix in SIDECAR_SUFFIXES if path.endswith(suffix) and path[:-len(suffix)] in known), None)


def classify_state_changes(before, after, *, started_ns, conversation_id=None):
    """Account for every entry that differs between two whole-state inventories, from metadata only.

    Classes, in this order:

    - ``session_owned``: a file whose path carries the new conversation id.
      No such path may exist in ``before``.
    - ``run_owned``: a new file that matches ``RUN_OWNED_PATTERNS`` and was
      born at or after ``started_ns``.
    - ``shared_changed``: an old file, link or special entry outside the
      conversation areas whose metadata changed; and a ``-wal``, ``-shm`` or
      ``-journal`` file that appeared or disappeared beside such an old file.
    - A directory is only a container. A new one must hold a classified
      entry. An old one may change when a direct child was added, removed or
      changed. An old directory that is empty before and after and was only
      touched is recorded with ``shared_changed`` as ``touched_empty``.

    Everything else raises ``RootClosureError``. Nothing is read.
    """
    old, new = _state_entries(before), _state_entries(after)
    if before.get('root') != after.get('root') or before.get('root_identity') != after.get('root_identity'):
        raise RootClosureError('state root identity changed')
    fresh = [match.group(1) for match in map(_NEW_PRIMARY.fullmatch, sorted(new.keys() - old.keys())) if match]
    if conversation_id is None:
        if len(fresh) != 1:
            raise RootClosureError(f'exactly one new conversation is required, found {len(fresh)}')
        conversation_id = fresh[0]
    elif fresh != [conversation_id]:
        raise RootClosureError('the named conversation is not the one new conversation of this capture')
    if not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z_-]{7,}', conversation_id):
        raise RootClosureError('conversation id is too short to name files safely')
    if any(carries_conversation_id(path, conversation_id) for path in old):
        raise RootClosureError('a path of the conversation existed before the capture')
    primary = STATE_PRIMARY.format(id=conversation_id)
    changed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    session, directories, run, shared, containers, unexplained = [], [], [], [], [], []
    for path in changed:
        was, now = old.get(path), new.get(path)
        kinds = {entry['kind'] for entry in (was, now) if entry}
        if carries_conversation_id(path, conversation_id):
            if kinds == {'file'}:
                session.append(path)
            elif kinds == {'directory'}:
                directories.append(path)
            else:
                raise RootClosureError(f'session-owned symlink or special entry cannot be copied: {path}')
        elif 'directory' in kinds:
            if len(kinds) != 1 or now is None:
                unexplained.append(path)
            else:
                containers.append(path)
        elif was is not None and now is not None:
            if path.split('/')[0] in CONVERSATION_AREAS:
                raise RootClosureError(f'an entry of another conversation changed during the capture: {path}')
            shared.append({'relative_path': path, 'kind': now['kind'], 'change': 'modified', 'before': _summary(was), 'after': _summary(now), 'content_read': False})
        elif _sidecar_of(path, old) is not None and _sidecar_of(path, old).split('/')[0] not in CONVERSATION_AREAS:
            shared.append({'relative_path': path, 'kind': (now or was)['kind'], 'change': 'created_sidecar' if was is None else 'removed_sidecar',
                           'before': _summary(was), 'after': _summary(now), 'content_read': False})
        elif was is None and kinds == {'file'} and any(pattern.fullmatch(path) for pattern in RUN_OWNED_PATTERNS):
            born = now['birth_ns'] if now['birth_ns'] is not None else now['ctime_ns']
            if born < started_ns:
                raise RootClosureError(f'a run file was born before the capture started: {path}')
            run.append(path)
        else:
            unexplained.append(path)
    accounted = set(session) | set(directories) | set(run) | {row['relative_path'] for row in shared}
    changed_set = set(changed)
    for path in containers:
        prefix = path + '/'
        if path in old:   # an old directory changes only through a direct child
            explained = any(other.startswith(prefix) and '/' not in other[len(prefix):] for other in changed_set)
        else:             # a new directory must hold something that is accounted for
            explained = any(other.startswith(prefix) for other in accounted)
        if not explained and path in old and not any(other.startswith(prefix) for other in old.keys() | new.keys()):
            # Empty before and after: a child lived only between the two
            # inventories. The touch is recorded; there is nothing to read.
            shared.append({'relative_path': path, 'kind': 'directory', 'change': 'touched_empty',
                           'before': _summary(old[path]), 'after': _summary(new[path]), 'content_read': False})
            explained = True
        if not explained:
            unexplained.append(path)
    if unexplained:
        raise RootClosureError('unexplained new, changed or removed entries: ' + ', '.join(sorted(unexplained)))
    if primary not in session:
        raise RootClosureError('the primary transcript of the conversation is missing')
    return {'conversation_id': conversation_id, 'primary_path': primary, 'session_owned': session, 'session_directories': directories,
            'run_owned': run, 'shared_changed': shared, 'directories_changed': containers, 'changed_entries': len(changed)}


def _copy_exact(root, relative, entry, target):
    """Copy one new file without following a link; the source must equal its inventory entry before and after."""
    descriptor = os.open(Path(root) / relative, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                entry['device'], entry['inode'], entry['size_bytes'], entry['mtime_ns']):
            raise RootClosureError(f'source file differs from its inventory entry: {relative}')
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        data = b''.join(chunks)
        again = os.fstat(descriptor)
        if len(data) != entry['size_bytes'] or (again.st_size, again.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise RootClosureError(f'source file changed during the copy: {relative}')
    finally:
        os.close(descriptor)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'xb') as stream:
        stream.write(data)
    return hashlib.sha256(data).hexdigest()


def _sqlite_groups(paths):
    known = set(paths)
    groups = []
    for path in sorted(known):
        members = [path] + [path + suffix for suffix in SIDECAR_SUFFIXES if path + suffix in known]
        if path.endswith(('.db', '.sqlite', '.sqlite3')) :
            groups.append({'database': path, 'members': sorted(members)})
    return groups


def _state_statement(classes):
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    return ('Metadata of the whole CLI state directory was listed before the first turn and after this turn. '
            f"{classes['changed_entries']} entries were new, changed or removed and every one is accounted for: "
            f"{len(classes['session_owned'])} session-owned files (path carries the conversation id; copied), "
            f"{len(classes['run_owned'])} run-owned files (new log or implicit files born during the capture; copied, private only), "
            f"{len(shared)} shared stores or their sidecars (changed; content not read and not copied), "
            f"{len(classes['session_directories']) + len(classes['directories_changed'])} directories. "
            'A shared store may hold records of this conversation. Its content was not read, so this receipt does not prove that it holds none.')


def capture_state_family(root, destination, before, *, attempt_id, turn, started_ns, conversation_id=None,
                         interval_seconds=0.1, max_reads=6, sleep=time.sleep):
    """Wait for two equal metadata reads, classify every change, then copy the session-owned and run-owned files.

    SQLite files are copied as bytes with their ``-wal`` and ``-shm`` files in
    the same pass. The originals are never opened with SQLite. A later reader
    must work on a copy of the copy.
    """
    root, destination = Path(root).absolute(), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise RootClosureError('capture destination must be new')
    if type(max_reads) is not int or max_reads < 2 or interval_seconds < 0:
        raise RootClosureError('at least two metadata reads are required')
    digests, previous, after = [], None, None
    for _ in range(max_reads):
        sleep(interval_seconds)
        current = inventory_state(root)
        digests.append(inventory_sha256(current))
        if previous is not None and current == previous:
            after = current
            break
        previous = current
    if after is None:
        raise RootClosureError(f'the state directory did not come to rest in {max_reads} metadata reads')
    classes = classify_state_changes(before, after, started_ns=started_ns, conversation_id=conversation_id)
    entries = _state_entries(after)
    artifacts = []
    try:
        for name, folder, private in (('session_owned', SESSION_COPY, False), ('run_owned', RUN_COPY, True)):
            for relative in classes[name]:
                entry = entries[relative]
                copied = f'{folder}/{relative}'
                digest = _copy_exact(root, relative, entry, destination / copied)
                role = 'native-primary' if relative == classes['primary_path'] else 'native-companion' if name == 'session_owned' else 'run-private'
                artifacts.append({'relative_path': relative, 'class': name, 'private_only': private, 'role': role, 'copied_path': copied,
                                  'filesystem_id': f"{entry['device']}:{entry['inode']}", 'size_bytes': entry['size_bytes'], 'sha256': digest})
        if inventory_state(root) != after:
            raise RootClosureError('the state directory changed during the copy')
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    found = [name for name in ('session_owned', 'run_owned', 'shared_changed') if classes[name]]
    return {'schema_version': STATE_SCHEMA, 'scope': STATE_SCOPE, 'source_root': str(root), 'root_identity': after['root_identity'],
            'attempt_id': attempt_id, 'turn': turn, 'conversation_id': classes['conversation_id'], 'primary_path': classes['primary_path'],
            'started_ns': started_ns, 'metadata_only_before_after': True, 'preexisting_contents_opened': False,
            'before_inventory_sha256': inventory_sha256(before), 'after_inventory_sha256': digests[-1],
            'quiescence': {'required_equal_reads': 2, 'reads': len(digests), 'interval_seconds': interval_seconds, 'stable': True,
                           'inventory_sha256': digests, 'unchanged_after_copy': True},
            'classes': {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')},
            'session_directories': classes['session_directories'],
            'accounting': {'changed_entries': classes['changed_entries'], 'classes_found': found, 'unexplained': [],
                           'every_changed_entry_accounted': True, 'shared_stores_changed_content_not_read': shared,
                           'statement': _state_statement(classes)},
            'sqlite_groups': _sqlite_groups(classes['session_owned']),
            'artifacts': artifacts}


def verify_state_capture(receipt, destination, *, before, after):
    """Offline check of a whole-state receipt against its two inventories and its private copy."""
    if receipt.get('schema_version') != STATE_SCHEMA or receipt.get('scope') != STATE_SCOPE:
        raise RootClosureError('not a whole-state receipt')
    quiet = receipt.get('quiescence', {})
    digests = quiet.get('inventory_sha256', [])
    if (inventory_sha256(before) != receipt.get('before_inventory_sha256') or inventory_sha256(after) != receipt.get('after_inventory_sha256')
            or before.get('root') != receipt.get('source_root') or quiet.get('stable') is not True
            or len(digests) < 2 or digests[-1] != digests[-2] or digests[-1] != receipt['after_inventory_sha256']
            or quiet.get('reads') != len(digests) or quiet.get('unchanged_after_copy') is not True):
        raise RootClosureError('receipt differs from the supplied inventory or its quiescence is incomplete')
    classes = classify_state_changes(before, after, started_ns=receipt['started_ns'], conversation_id=receipt.get('conversation_id'))
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    accounting = receipt.get('accounting', {})
    if (any(receipt.get('classes', {}).get(name) != classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed'))
            or receipt.get('session_directories') != classes['session_directories'] or receipt.get('primary_path') != classes['primary_path']
            or accounting.get('changed_entries') != classes['changed_entries'] or accounting.get('unexplained') != []
            or accounting.get('every_changed_entry_accounted') is not True
            or accounting.get('shared_stores_changed_content_not_read') != shared
            or accounting.get('statement') != _state_statement(classes)
            or receipt.get('sqlite_groups') != _sqlite_groups(classes['session_owned'])
            or receipt.get('preexisting_contents_opened') is not False):
        raise RootClosureError('receipt classification differs from the inventories')
    entries = _state_entries(after)
    expected = {name: 'session_owned' for name in classes['session_owned']} | {name: 'run_owned' for name in classes['run_owned']}
    artifacts = receipt.get('artifacts', [])
    if len(artifacts) != len(expected) or {row['relative_path'] for row in artifacts} != set(expected):
        raise RootClosureError('copied file set differs from the classification')
    destination = Path(destination)
    present = {path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file() or path.is_symlink()}
    if present != {row['copied_path'] for row in artifacts}:
        raise RootClosureError('copied file set on disk differs from the receipt')
    for row in artifacts:
        entry, kind = entries[row['relative_path']], expected[row['relative_path']]
        target = destination / row['copied_path']
        if (row.get('class') != kind or row.get('private_only') is not (kind == 'run_owned')
                or row['copied_path'] != f"{SESSION_COPY if kind == 'session_owned' else RUN_COPY}/{row['relative_path']}"
                or row['filesystem_id'] != f"{entry['device']}:{entry['inode']}" or row['size_bytes'] != entry['size_bytes']
                or target.is_symlink() or target.stat().st_size != entry['size_bytes']
                or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']):
            raise RootClosureError(f"copied file differs from its receipt: {row['relative_path']}")
    return True
