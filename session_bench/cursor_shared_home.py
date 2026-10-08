"""Metadata-only bracket of the shared Cursor home (``~/.cursor``) around an isolated capture.

``CURSOR_CONFIG_DIR`` and ``CURSOR_DATA_DIR`` move the session family into
the run directory, but ``HOME`` stays the operator's home and the CLI still
writes to the shared home: rows of each AI edit go to
``ai-tracking/ai-code-tracking.db``. The controller lists the whole shared
home before the first turn and after the last. No file content is read and
no link is followed. Every entry that is new, changed or removed must fall
into one class:

- **session-owned**: its path carries the session id;
- **shared store**: a known shared file, or a SQLite sidecar beside it. Its
  content holds other sessions and is not read here;
- **directory**: a directory above a classified entry (only its times change).

Anything else is unexplained and the capture fails closed.
"""
import os
from pathlib import Path
import stat

SCHEMA = 'session-bench-cursor-shared-home-v1'
KNOWN_SHARED_STORES = ('ai-tracking/ai-code-tracking.db',)
_SIDECARS = ('-journal', '-wal', '-shm')
_COMPARED = ('kind', 'size_bytes', 'mtime_ns', 'inode')


class SharedHomeError(ValueError):
    """The shared home changed in a way that the capture cannot explain."""


def inventory_shared_home(root):
    """List every entry below ``root`` by metadata. Nothing is opened and no link is followed."""
    root = Path(root).absolute()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise SharedHomeError('shared home must be an ordinary directory')
    entries = []

    def visit(folder):
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda item: item.name)
        for child in children:
            data = child.stat(follow_symlinks=False)
            kind = ('directory' if stat.S_ISDIR(data.st_mode) else 'file' if stat.S_ISREG(data.st_mode)
                    else 'link' if stat.S_ISLNK(data.st_mode) else 'other')
            entries.append({'relative_path': Path(child.path).relative_to(root).as_posix(), 'kind': kind, 'size_bytes': data.st_size,
                            'mtime_ns': data.st_mtime_ns, 'ctime_ns': data.st_ctime_ns, 'inode': data.st_ino})
            if kind == 'directory':
                visit(child.path)

    visit(root)
    return {'schema_version': SCHEMA, 'root': str(root), 'metadata_only': True, 'entries': entries}


def _is_shared_store(path):
    return any(path == name or path in {name + suffix for suffix in _SIDECARS} for name in KNOWN_SHARED_STORES)


def classify_shared_home_changes(before, after, *, session_id):
    """Classify every entry that differs between two inventories, or raise SharedHomeError.

    Returns a receipt with the session-owned entries, the shared stores that
    changed and the directories above them. The error names only the number
    of unexplained entries; their paths can belong to other projects.
    """
    if not isinstance(session_id, str) or not session_id:
        raise SharedHomeError('session id required')
    for snapshot in (before, after):
        if snapshot.get('schema_version') != SCHEMA or snapshot.get('metadata_only') is not True:
            raise SharedHomeError('inventory is not a metadata-only shared home inventory')
    if before.get('root') != after.get('root'):
        raise SharedHomeError('the two inventories list different directories')
    old = {row['relative_path']: row for row in before['entries']}
    new = {row['relative_path']: row for row in after['entries']}
    changed = sorted(path for path in set(old) | set(new)
                     if path not in old or path not in new or any(old[path][key] != new[path][key] for key in _COMPARED))
    session, shared, directories, unexplained = [], [], [], []
    explained = []
    for path in changed:
        if session_id in path:
            session.append(path); explained.append(path)
        elif _is_shared_store(path):
            shared.append(path); explained.append(path)
    for path in changed:
        if path in explained:
            continue
        row = new.get(path, old.get(path))
        if row['kind'] == 'directory' and path in old and path in new and any(other.startswith(path + '/') for other in explained):
            directories.append(path)
        else:
            unexplained.append(path)
    if unexplained:
        error = SharedHomeError(f'{len(unexplained)} unexplained change(s) in the shared home')
        error.paths = unexplained
        raise error
    summary = lambda path: {'relative_path': path, **{key: new[path][key] for key in ('kind', 'size_bytes', 'mtime_ns')}} if path in new else {'relative_path': path, 'removed': True}
    return {'schema_version': SCHEMA, 'session_id': session_id, 'root': after['root'], 'entries_before': len(old), 'entries_after': len(new),
            'session_owned': [summary(path) for path in session], 'shared_stores_changed': [summary(path) for path in shared],
            'directories_touched': directories, 'unexplained': 0,
            'statement': 'metadata only; the content of a shared store is not read by the controller'}
