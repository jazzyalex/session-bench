"""Whole Hermes home bracket (``~/.hermes``), receipt ``hermes-state-root-v1``.

Hermes runs in the operator's normal home. Its home holds the shared session
store ``state.db`` and many other files. The controller lists the whole home
by metadata before the first turn and after each turn. No old file is opened
and no link is followed. Every entry that is new, changed or removed must
fall into one class (``classify_hermes_changes``):

- **session-owned**: a file whose path carries the session id. Copied.
- **run-owned**: a new file that matches ``RULES['run_owned']`` and was born
  during the capture. Copied, private only.
- **shared**: an old file that changed, a SQLite sidecar beside it, a lock or
  marker file, or a digested subtree. Metadata only. The content is not read.
- **directory**: only a container.

Anything else stops the capture. ``RULES`` is the small table to extend after
a refusal.

One exception to "content is not read": the session store. Its files are
copied as bytes to a private temporary directory, the COPY is opened
read-only, only the rows of the test session are exported, and the copy is
deleted (``export_session_rows``, owner permission of 2026-10-06).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from urllib.parse import quote

STATE_SCHEMA = 'hermes-state-root-v1'
STATE_SCOPE = 'whole_hermes_home'
ROWS_SCHEMA = 'session-bench-hermes-session-store-rows-v1'
REFUSAL_SCHEMA = 'session-bench-hermes-state-refusal-v1'
SESSION_COPY, RUN_COPY = 'session-owned', 'run-owned-private'
ROWS_FILE, STORE_SCHEMA_FILE = 'session-store-rows.json', 'session-store-schema.json'
SIDECAR_SUFFIXES = ('-wal', '-shm', '-journal')

# The table to extend after a refusal. Paths are relative to the Hermes home.
# Patterns are full-match regular expressions. The receipt stores the table it used.
RULES = {
    # Shared SQLite stores whose rows of the test session are exported. Each must hold the session.
    'session_stores': ('state.db',),
    # Large subtrees recorded as one entry with a digest of every entry inside.
    # A change inside is reported by path as a shared change and is never read.
    'digest_subtrees': ('hermes-agent', 'tools', 'installs', 'cache', 'audio_cache', 'image_cache'),
    # Areas with one entry per session. A changed old file here belongs to another session.
    'session_areas': ('sessions',),
    # Old shared files inside a session area that may change (an index of all sessions).
    'shared_in_session_areas': (r'sessions/sessions\.json',),
    # New files that a turn creates without the session id in their path.
    'run_owned': (r'logs/[^/]+',),
    # Lock, process and marker files that may appear or disappear. Metadata only.
    'shared_transient': (r'(.*/)?[^/]+\.lock', r'(.*/)?[^/]+\.pid', r'(.*/)?\.DS_Store', r'\.clean_shutdown', r'\.update_check'),
}
_KINDS = ('directory', 'file', 'symlink', 'other', 'directory-digest')
_SESSION_ID = re.compile(r'[0-9A-Za-z][0-9A-Za-z_-]{7,}')
_OWNER_COLUMNS = frozenset({'session_id', 'sessionid'})
_SESSION_TABLES = frozenset({'session', 'sessions'})


class HermesStateError(ValueError):
    """The Hermes home changed in a way that the capture cannot explain, or the store lacks the session."""

    def __init__(self, message, unexplained=()):
        super().__init__(message)
        self.unexplained = list(unexplained)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory_sha256(snapshot):
    return _sha(_canonical(snapshot))


def rules_table(rules=None):
    """The rule table as plain JSON data (lists of strings)."""
    return {name: list(values) for name, values in sorted((rules or RULES).items())}


def _describe(child, relative):
    info = child.stat(follow_symlinks=False)
    birth = getattr(info, 'st_birthtime', None)
    entry = {'relative_path': relative, 'device': info.st_dev, 'inode': info.st_ino, 'size_bytes': info.st_size,
             'birth_ns': None if birth is None else int(birth * 1_000_000_000),
             'ctime_ns': info.st_ctime_ns, 'mtime_ns': info.st_mtime_ns}
    if stat.S_ISLNK(info.st_mode):
        entry.update(kind='symlink', target_sha256=_sha(os.readlink(child.path).encode('utf-8', 'surrogateescape')))
    elif stat.S_ISDIR(info.st_mode):
        entry['kind'] = 'directory'
    elif stat.S_ISREG(info.st_mode):
        entry['kind'] = 'file'
    else:
        entry['kind'] = 'other'
    return entry


def _children(folder):
    with os.scandir(folder) as scan:
        return sorted(scan, key=lambda item: item.name)


def inventory_hermes_home(root, *, rules=None, detail=None):
    """Metadata of every entry under the Hermes home. No file is opened and no link is followed.

    A symbolic link is one entry of kind ``symlink``. Its target string is
    hashed, not stored, and the target is never visited.

    A subtree named in ``digest_subtrees`` is still walked completely, but it
    is one entry of kind ``directory-digest``: the number of entries inside,
    one digest over all of them, and one digest per directory over its direct
    children. ``detail`` (a dict) receives the digest of every single entry
    inside, so that a later change is reported by path.
    """
    root = Path(root).absolute()
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise HermesStateError('Hermes home must be an ordinary directory')
    digested = set((rules or RULES)['digest_subtrees'])
    entries = []

    def digest(folder, relative, rows, directories):
        listing = []
        for child in _children(folder):
            entry = _describe(child, f'{relative}/{child.name}')
            listing.append(entry)
            rows[entry['relative_path']] = _sha(_canonical(entry))
            if entry['kind'] == 'directory':
                digest(child.path, entry['relative_path'], rows, directories)
        directories[relative] = _sha(_canonical(listing))

    def visit(folder, prefix):
        for child in _children(folder):
            entry = _describe(child, prefix + child.name)
            entries.append(entry)
            if entry['kind'] != 'directory':
                continue
            if entry['relative_path'] in digested:
                rows, directories = {}, {}
                digest(child.path, entry['relative_path'], rows, directories)
                entry.update(kind='directory-digest', entry_count=len(rows), entries_sha256=_sha(_canonical(rows)),
                             directory_sha256=directories)
                if detail is not None:
                    detail[entry['relative_path']] = rows
            else:
                visit(child.path, entry['relative_path'] + '/')

    visit(root, '')
    return {'root': str(root), 'root_identity': {'device': info.st_dev, 'inode': info.st_ino},
            'entries': entries, 'metadata_only': True, 'scope': STATE_SCOPE}


def _state_entries(snapshot):
    if snapshot.get('metadata_only') is not True or snapshot.get('scope') != STATE_SCOPE or not isinstance(snapshot.get('entries'), list):
        raise HermesStateError('not a whole Hermes home metadata inventory')
    result = {}
    for entry in snapshot['entries']:
        path = entry.get('relative_path') if isinstance(entry, dict) else None
        if (not isinstance(path, str) or not path or path.startswith('/') or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/')) or path in result
                or entry.get('kind') not in _KINDS):
            raise HermesStateError('invalid or duplicate inventory path')
        result[path] = entry
    return result


def carries_session_id(path, session_id):
    """True when one path component holds the id with no letter or digit directly before or after it."""
    for part in path.split('/'):
        start = part.find(session_id)
        while start != -1:
            end = start + len(session_id)
            if (start == 0 or not part[start - 1].isalnum()) and (end == len(part) or not part[end].isalnum()):
                return True
            start = part.find(session_id, start + 1)
    return False


def _summary(entry):
    if entry is None:
        return None
    row = {'size_bytes': entry['size_bytes'], 'metadata_sha256': _sha(_canonical(entry))}
    if entry['kind'] == 'directory-digest':
        row.update(entry_count=entry['entry_count'], entries_sha256=entry['entries_sha256'])
    return row


def _sidecar_of(path, known):
    return next((path[:-len(suffix)] for suffix in SIDECAR_SUFFIXES if path.endswith(suffix) and path[:-len(suffix)] in known), None)


def _matches(patterns, path):
    return any(re.fullmatch(pattern, path) for pattern in patterns)


def _changed_directories(was, now):
    old, new = was['directory_sha256'], now['directory_sha256']
    return sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))


def _refusal_row(path, was, now, reason):
    entry = now or was
    return {'relative_path': path, 'change': 'new' if was is None else 'removed' if now is None else 'changed',
            'kind': entry['kind'], 'size_bytes': entry['size_bytes'], 'reason': reason}


def classify_hermes_changes(before, after, *, session_id, started_ns, rules=None, digest_changes=None):
    """Account for every entry that differs between two whole-home inventories, from metadata only.

    Rules, in this order, for each entry that is new, changed or removed:

    1. Its path carries the session id: a file is ``session_owned``, a
       directory is a session directory, anything else is refused. No such
       path may exist in ``before``.
    2. A ``directory-digest`` entry that exists before and after is
       ``shared_changed`` with the directories (and, when ``digest_changes``
       is given, the paths) that changed inside. A path inside that carries
       the session id is refused: the subtree must then leave the digest table.
    3. A directory is only a container (see below).
    4. An old entry that changed is ``shared_changed``. Inside a session area
       it is refused, unless ``shared_in_session_areas`` names it.
    5. A ``-wal``, ``-shm`` or ``-journal`` file that appeared or disappeared
       beside an old file is ``shared_changed``.
    6. A new file that matches ``run_owned`` is ``run_owned`` when it was born
       at or after ``started_ns``. Born earlier, it is an old file under a new
       name (a rotated log): ``shared_changed``, not read.
    7. A file that matches ``shared_transient`` and appeared or disappeared is
       ``shared_changed``.
    8. Everything else is unexplained.

    A new directory must hold a classified entry. An old directory may change
    when a direct child was added, removed or changed. An old directory that
    is empty before and after is recorded as ``touched_empty``. An old
    directory whose own times moved while every entry inside is unchanged is
    recorded as ``touched_children_unchanged``.

    Unexplained entries raise ``HermesStateError``; its ``unexplained`` list
    names each path with the reason. Nothing is read.
    """
    rules = rules_table(rules)
    old, new = _state_entries(before), _state_entries(after)
    if before.get('root') != after.get('root') or before.get('root_identity') != after.get('root_identity'):
        raise HermesStateError('Hermes home identity changed')
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise HermesStateError('session id is missing or too short to name files safely')
    if any(carries_session_id(path, session_id) for path in old):
        raise HermesStateError('a path of the session existed before the capture')
    changed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    session, directories, run, shared, containers, unexplained = [], [], [], [], [], []

    def share(path, change, was, now, **extra):
        shared.append({'relative_path': path, 'kind': (now or was)['kind'], 'change': change,
                       'before': _summary(was), 'after': _summary(now), 'content_read': False, **extra})

    for path in changed:
        was, now = old.get(path), new.get(path)
        kinds = {entry['kind'] for entry in (was, now) if entry}
        sidecar = _sidecar_of(path, old)
        if carries_session_id(path, session_id):
            if kinds == {'file'}:
                session.append(path)
            elif kinds == {'directory'}:
                directories.append(path)
            else:
                unexplained.append(_refusal_row(path, was, now, 'session-owned link, special entry or digested subtree cannot be copied'))
        elif 'directory-digest' in kinds:
            if len(kinds) != 1 or was is None or now is None:
                unexplained.append(_refusal_row(path, was, now, 'a digested subtree appeared, disappeared or changed kind'))
                continue
            folders = _changed_directories(was, now)
            paths = None if digest_changes is None else sorted(digest_changes.get(path, []))
            named = [name for name in folders + (paths or []) if carries_session_id(name, session_id)]
            if named:
                unexplained.extend(_refusal_row(name, None, now, 'a path with the session id lies inside a digested subtree; remove the subtree from digest_subtrees')
                                   for name in sorted(set(named)))
                continue
            extra = {'changed_directories': folders}
            if paths is not None:
                extra['changed_paths'] = paths
            share(path, 'modified', was, now, **extra)
        elif 'directory' in kinds:
            if len(kinds) != 1 or now is None:
                unexplained.append(_refusal_row(path, was, now, 'a directory was removed or changed kind'))
            else:
                containers.append(path)
        elif was is not None and now is not None:
            if path.split('/')[0] in rules['session_areas'] and not _matches(rules['shared_in_session_areas'], path):
                unexplained.append(_refusal_row(path, was, now, 'an entry of another session changed during the capture'))
            else:
                share(path, 'modified', was, now)
        elif sidecar is not None and (sidecar.split('/')[0] not in rules['session_areas'] or _matches(rules['shared_in_session_areas'], sidecar)):
            share(path, 'created_sidecar' if was is None else 'removed_sidecar', was, now)
        elif was is None and kinds == {'file'} and _matches(rules['run_owned'], path):
            born = now['birth_ns'] if now['birth_ns'] is not None else now['ctime_ns']
            if born < started_ns:
                share(path, 'created_old_identity', was, now)
            else:
                run.append(path)
        elif kinds <= {'file', 'symlink'} and _matches(rules['shared_transient'], path):
            share(path, 'created' if was is None else 'removed', was, now)
        else:
            unexplained.append(_refusal_row(path, was, now, 'no rule explains this entry'))
    accounted = set(session) | set(directories) | set(run) | {row['relative_path'] for row in shared}
    changed_set = set(changed)
    for path in containers:
        prefix = path + '/'
        if path in old:   # an old directory changes only through a direct child
            explained = any(other.startswith(prefix) and '/' not in other[len(prefix):] for other in changed_set)
        else:             # a new directory must hold something that is accounted for
            explained = any(other.startswith(prefix) for other in accounted)
        if not explained and path in old and not any(other.startswith(prefix) for other in old.keys() | new.keys()):
            share(path, 'touched_empty', old[path], new[path])
            explained = True
        if not explained and path in old and path in new:
            # Only the directory's own times moved; every entry inside is the
            # same before and after. A child lived only between the two
            # inventories. The touch is recorded; nothing inside is read.
            share(path, 'touched_children_unchanged', old[path], new[path])
            explained = True
        if not explained:
            unexplained.append(_refusal_row(path, old.get(path), new.get(path), 'a directory changed and no entry inside explains it'))
    if unexplained:
        unexplained.sort(key=lambda row: row['relative_path'])
        names = [row['relative_path'] for row in unexplained]
        raise HermesStateError(f'{len(names)} unexplained new, changed or removed entries: ' + ', '.join(names[:20])
                               + (' …' if len(names) > 20 else ''), unexplained)
    shared.sort(key=lambda row: row['relative_path'])
    return {'session_id': session_id, 'session_owned': session, 'session_directories': directories, 'run_owned': run,
            'shared_changed': shared, 'directories_changed': containers, 'changed_entries': len(changed)}


def _copy_exact(root, relative, entry, target):
    """Copy one file as bytes without following a link; the source must equal its inventory entry before and after."""
    descriptor = os.open(Path(root) / relative, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                entry['device'], entry['inode'], entry['size_bytes'], entry['mtime_ns']):
            raise HermesStateError(f'source file differs from its inventory entry: {relative}')
        digest, size = hashlib.sha256(), 0
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'xb') as stream:
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        again = os.fstat(descriptor)
        if size != entry['size_bytes'] or (again.st_size, again.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
            raise HermesStateError(f'source file changed during the copy: {relative}')
    finally:
        os.close(descriptor)
    return digest.hexdigest()


# --- Rows of the test session in the shared session store ---

class _RawText(bytes):
    """TEXT that is not valid UTF-8. Exported as hex."""


def _text(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return _RawText(raw)


def _value(value):
    if isinstance(value, _RawText):
        return {'text_hex': value.hex()}
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {'real': repr(value)}
    return value


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _singular(name):
    lower = name.lower()
    return lower[:-3] + 'y' if lower.endswith('ies') else lower[:-1] if lower.endswith('s') else lower


def _is_session(value, session_id):
    return value == session_id or (isinstance(value, bytes) and bytes(value) == session_id.encode('utf-8'))


def _owner_columns(table):
    """Columns that say which session a row belongs to."""
    owners = [name for name in table['columns'] if name.lower() in _OWNER_COLUMNS]
    if table['name'].lower() in _SESSION_TABLES:
        owners += [name for name in table['columns'] if name in table['primary_key'] or name.lower() == 'id']
    return sorted(set(owners))


def check_rows_name_only_session(export, session_id):
    """Fail when an exported row names another session in a session-id column."""
    for store in export['stores']:
        for table in store['tables']:
            owners = _owner_columns({'name': table['table'], 'columns': table['columns'], 'primary_key': table['primary_key']})
            for row in table['rows']:
                for name in owners:
                    value = row['values'][name]
                    if isinstance(value, dict) and 'blob_hex' in value:
                        value = bytes.fromhex(value['blob_hex'])
                    if value is not None and not _is_session(value, session_id):
                        raise HermesStateError(f"an exported row of {table['table']} names another session in column {name}")
    return True


def _read_schema(connection):
    objects = [{'type': kind, 'name': name, 'tbl_name': owner, 'sql': sql} for kind, name, owner, sql in connection.execute(
        'SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name')]
    virtual = [row['name'] for row in objects if row['type'] == 'table' and (row['sql'] or '').lstrip().upper().startswith('CREATE VIRTUAL TABLE')]
    tables = []
    for row in objects:
        name = row['name']
        if row['type'] != 'table' or name.lower().startswith('sqlite_'):
            continue
        table = {'name': name, 'virtual': name in virtual, 'virtual_shadow': any(name.startswith(owner + '_') for owner in virtual),
                 'columns': [], 'column_types': {}, 'primary_key': [], 'has_rowid': False, 'foreign_keys': [], 'scan_error': None}
        try:
            keys = {}
            for _, column, declared, _, _, position in connection.execute(f'PRAGMA table_info({_quote(name)})'):
                table['column_types'][column] = declared
                if position:
                    keys[position] = column
            table['primary_key'] = [keys[position] for position in sorted(keys)]
            table['columns'] = [item[0] for item in connection.execute(f'SELECT * FROM {_quote(name)} LIMIT 0').description]
            grouped = {}
            for number, _, parent, child, target, *_ in connection.execute(f'PRAGMA foreign_key_list({_quote(name)})'):
                grouped.setdefault(number, []).append({'column': child, 'references_table': parent, 'references_column': target})
            table['foreign_keys'] = [{**parts[0], 'columns': len(parts)} for _, parts in sorted(grouped.items())]
            try:
                connection.execute(f'SELECT rowid FROM {_quote(name)} LIMIT 0')
                table['has_rowid'] = True
            except sqlite3.OperationalError:
                pass
        except sqlite3.Error as error:
            if not (table['virtual'] or table['virtual_shadow']):
                raise HermesStateError(f'table {name} of the session store cannot be read') from error
            table['scan_error'] = type(error).__name__
        tables.append(table)
    pragmas = {name: connection.execute(f'PRAGMA {name}').fetchone()[0] for name in ('user_version', 'application_id')}
    schema = {**pragmas, 'objects': objects, 'tables': tables}
    return schema, _sha(_canonical({**pragmas, 'objects': objects}))


def _select(connection, table, where, parameters):
    head = 'rowid AS "__rowid__", *' if table['has_rowid'] else '*'
    rows = []
    for record in connection.execute(f"SELECT {head} FROM {_quote(table['name'])} WHERE {where}", parameters):
        record = list(record)
        rowid = record.pop(0) if table['has_rowid'] else None
        rows.append((rowid, dict(zip(table['columns'], record, strict=True))))
    return rows


def _row_key(table, rowid, values):
    if rowid is not None:
        return ('rowid', rowid)
    return ('values', _canonical([_value(values[name]) for name in (table['primary_key'] or table['columns'])]))


def export_session_rows(database, session_id, *, store='state.db'):
    """Read the rows of one session from a private COPY of a store. Returns ``(export, schema, summary)``.

    Selection:

    1. *Exact*: in every table, every row with a column whose value is
       exactly the session id (as text or as bytes).
    2. *Join, one level*: rows of another table that reference a row of step 1
       by its key. A declared single-column foreign key is followed. An
       undeclared key is followed when the column is named ``<table>_id`` or
       ``<singular table>_id``, the referenced table has a one-column primary
       key, and no value of that column in the whole table lacks a referenced
       row (a count query; zero orphans). Rows found by a join are not
       followed further. Parents are never followed: a parent can be shared.

    A row that only holds the id inside a longer value is not selected. Such
    rows are counted per table (``rows_that_contain_the_id``), so a gap shows.

    Values are exported per column name. BLOBs are ``{"blob_hex": …}``. The
    rowid and primary key stay. For other sessions only row counts are read.
    """
    connection = sqlite3.connect('file:' + quote(str(Path(database).absolute())) + '?mode=ro', uri=True)
    try:
        connection.text_factory = _text
        connection.execute('PRAGMA query_only=ON')
        schema, schema_sha256 = _read_schema(connection)
        tables = {table['name']: table for table in schema['tables']}
        selected = {name: {} for name in tables}   # table -> row key -> [rowid, values, reasons]
        totals, mentions, not_scanned = {}, {}, []

        def add(table, rows, reason):
            for rowid, values in rows:
                slot = selected[table['name']].setdefault(_row_key(table, rowid, values), [rowid, values, []])
                if reason not in slot[2]:
                    slot[2].append(reason)

        for table in tables.values():
            try:
                if table['scan_error']:
                    raise sqlite3.OperationalError(table['scan_error'])
                totals[table['name']] = connection.execute(f"SELECT COUNT(*) FROM {_quote(table['name'])}").fetchone()[0]
                where = ' OR '.join(f'{_quote(name)} = ?1 OR {_quote(name)} = CAST(?1 AS BLOB)' for name in table['columns'])
                for rowid, values in _select(connection, table, where, (session_id,)) if table['columns'] else []:
                    for name in table['columns']:
                        if _is_session(values[name], session_id):
                            add(table, [(rowid, values)], f'exact:{name}')
                # A count only: rows that hold the id inside a longer value (for example in JSON text).
                inside = ' OR '.join(f'instr(CAST({_quote(name)} AS TEXT), ?1) > 0' for name in table['columns'])
                mentions[table['name']] = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote(table['name'])} WHERE {inside}", (session_id,)).fetchone()[0] if inside else 0
            except sqlite3.Error as error:
                if not (table['virtual'] or table['virtual_shadow']):
                    raise HermesStateError(f"table {table['name']} of the session store cannot be read") from error
                totals[table['name']] = mentions[table['name']] = None
                selected[table['name']].clear()
                not_scanned.append({'table': table['name'], 'reason': 'virtual table or its shadow table could not be queried'})
        seeds = {name: [slot[1] for slot in rows.values()] for name, rows in selected.items() if rows}
        joins, candidates = [], []
        for parent_name, seed_rows in sorted(seeds.items()):
            parent = tables[parent_name]
            for child in tables.values():
                if child['name'] == parent_name or child['scan_error'] or child['name'] in {row['table'] for row in not_scanned}:
                    continue
                links = []
                for key in child['foreign_keys']:
                    if key['references_table'].lower() == parent_name.lower() and key['columns'] == 1:
                        target = key['references_column'] or (parent['primary_key'][0] if len(parent['primary_key']) == 1 else None)
                        if target in parent['columns']:
                            links.append((key['column'], target, 'declared foreign key'))
                if len(parent['primary_key']) == 1:
                    for name in child['columns']:
                        if name.lower() in {parent_name.lower() + '_id', _singular(parent_name) + '_id'} and name not in {link[0] for link in links}:
                            links.append((name, parent['primary_key'][0], 'column name'))
                for column, target, basis in links:
                    values = sorted({row[target] for row in seed_rows if row[target] is not None and not isinstance(row[target], float)},
                                    key=lambda item: (type(item).__name__, item))
                    described = {'table': child['name'], 'column': column, 'references': f'{parent_name}.{target}', 'basis': basis}
                    if basis == 'column name':
                        orphans = connection.execute(
                            f"SELECT COUNT(*) FROM {_quote(child['name'])} WHERE {_quote(column)} IS NOT NULL AND {_quote(column)} NOT IN "
                            f"(SELECT {_quote(target)} FROM {_quote(parent_name)} WHERE {_quote(target)} IS NOT NULL)").fetchone()[0]
                        described['orphan_values_in_whole_table'] = orphans
                        if orphans:
                            matching = sum(connection.execute(
                                f"SELECT COUNT(*) FROM {_quote(child['name'])} WHERE {_quote(column)} IN ({','.join('?' * len(part))})", part).fetchone()[0]
                                for part in (values[i:i + 500] for i in range(0, len(values), 500)))
                            candidates.append({**described, 'matching_rows_not_exported': matching,
                                               'reason': 'the column does not behave as a key of the referenced table'})
                            continue
                    found = 0
                    for part in (values[i:i + 500] for i in range(0, len(values), 500)):
                        rows = _select(connection, child, f"{_quote(column)} IN ({','.join('?' * len(part))})", part)
                        found += len(rows)
                        add(child, rows, f'join:{column}->{parent_name}.{target}')
                    joins.append({**described, 'keys': len(values), 'rows': found})
        export_tables, summary_tables = [], []
        for name in sorted(tables):
            table, rows = tables[name], selected[name]
            ordered = sorted(rows.values(), key=lambda slot: (slot[0] is None, slot[0] if slot[0] is not None else 0, _canonical([_value(v) for v in slot[1].values()])))
            reasons = sorted({reason for slot in ordered for reason in slot[2]})
            summary_tables.append({'table': name, 'total_rows': totals[name], 'selected_rows': len(ordered), 'selected_by': reasons,
                                   'rows_with_exact_match': sum(any(reason.startswith('exact:') for reason in slot[2]) for slot in ordered),
                                   'rows_that_contain_the_id': mentions[name],
                                   'other_rows_counted_not_read': None if totals[name] is None else totals[name] - len(ordered)})
            if ordered:
                export_tables.append({'table': name, 'columns': table['columns'], 'column_types': table['column_types'],
                                      'primary_key': table['primary_key'], 'has_rowid': table['has_rowid'],
                                      'rows': [{'rowid': rowid, 'selected_by': sorted(why), 'values': {key: _value(value) for key, value in values.items()}}
                                               for rowid, values, why in ordered]})
    except sqlite3.Error as error:
        raise HermesStateError(f'the copy of the session store cannot be read: {type(error).__name__}') from error
    finally:
        connection.close()
    if not seeds:
        raise HermesStateError(f'the session id is in no row of {store}: the store did not record the session, or the id is wrong')
    entry = {'store': store, 'user_version': schema['user_version'], 'schema_sha256': schema_sha256, 'tables': export_tables}
    summary = {'store': store, 'user_version': schema['user_version'], 'application_id': schema['application_id'],
               'schema_sha256': schema_sha256, 'tables': summary_tables, 'joins': joins, 'join_candidates_not_exported': candidates,
               'tables_not_scanned': not_scanned, 'selected_rows': sum(row['selected_rows'] for row in summary_tables)}
    check_rows_name_only_session({'stores': [entry]}, session_id)
    return entry, {'store': store, **schema}, summary


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode('utf-8') + b'\n'


def _write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'xb') as stream:
        stream.write(raw)
    return {'size_bytes': len(raw), 'sha256': _sha(raw)}


def _read_session_stores(root, destination, entries, session_id, rules, temporary):
    """Copy each session store with its sidecars, read the session rows from the copy, delete the copy."""
    if temporary.exists() or temporary.is_symlink():
        raise HermesStateError('temporary store directory must be new')
    exports, schemas, stores = [], [], []
    try:
        temporary.mkdir(mode=0o700)
        for index, store in enumerate(rules['session_stores']):
            if entries.get(store, {}).get('kind') != 'file':
                raise HermesStateError(f'the session store is missing from the Hermes home: {store}')
            folder, copied = temporary / str(index), []
            for name in [store] + [store + suffix for suffix in SIDECAR_SUFFIXES if store + suffix in entries]:
                entry = entries[name]
                if entry['kind'] != 'file':
                    raise HermesStateError(f'a file of the session store is not an ordinary file: {name}')
                digest = _copy_exact(root, name, entry, folder / Path(name).name)
                copied.append({'relative_path': name, 'filesystem_id': f"{entry['device']}:{entry['inode']}",
                               'size_bytes': entry['size_bytes'], 'sha256': digest})
            export, schema, summary = export_session_rows(folder / Path(store).name, session_id, store=store)
            exports.append(export)
            schemas.append(schema)
            stores.append({**summary, 'copied_files': copied})
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        if temporary.exists() or temporary.is_symlink():
            raise HermesStateError('the temporary copy of the session store could not be deleted')
    rows = {'schema_version': ROWS_SCHEMA, 'session_id': session_id, 'stores': exports}
    binding = {'rows_export': {'path': ROWS_FILE, **_write_new(destination / ROWS_FILE, _encode(rows))},
               'schema_export': {'path': STORE_SCHEMA_FILE, **_write_new(destination / STORE_SCHEMA_FILE, _encode({'stores': schemas}))},
               'stores': stores, 'selected_rows': sum(store['selected_rows'] for store in stores),
               'rows_of_other_sessions_exported': 0, 'other_sessions': 'row counts only',
               'opened': 'private temporary copy only, read-only; the original store was copied as bytes and never opened with SQLite',
               'temporary_copy': {'directory': temporary.name, 'deleted': True}}
    return binding


def _statement(classes, store):
    shared = classes['shared_changed']
    return ('Metadata of the whole Hermes home was listed before the first turn and after this turn. '
            f"{classes['changed_entries']} entries were new, changed or removed and every one is accounted for: "
            f"{len(classes['session_owned'])} session-owned files (path carries the session id; copied), "
            f"{len(classes['run_owned'])} run-owned files (new files born during the capture; copied, private only), "
            f"{len(shared)} shared entries (old files, sidecars, lock or marker files, digested subtrees; metadata only, content not read), "
            f"{len(classes['session_directories']) + len(classes['directories_changed'])} directories. "
            f"From the session store, {store.get('selected_rows')} rows that name the session or reference such a row were exported from a "
            'private copy that was then deleted; rows of other sessions were counted, not read. '
            'Another shared entry may hold records of this session. Its content was not read, so this receipt does not prove that it holds none.')


def capture_hermes_state(root, destination, before, *, attempt_id, turn, session_id, started_ns, build=None, before_detail=None,
                         rules=None, interval_seconds=0.1, max_reads=6, sleep=time.sleep):
    """Wait for two equal metadata reads, classify every change, copy the owned files, export the session rows.

    Returns ``(receipt, after_inventory)``. On any refusal the destination
    and the temporary store copy are removed and ``HermesStateError`` is raised.
    """
    root, destination = Path(root).absolute(), Path(destination)
    rules = rules_table(rules)
    if destination.exists() or destination.is_symlink():
        raise HermesStateError('capture destination must be new')
    if type(max_reads) is not int or max_reads < 2 or interval_seconds < 0:
        raise HermesStateError('at least two metadata reads are required')
    digests, previous, after, detail = [], None, None, {}
    for _ in range(max_reads):
        sleep(interval_seconds)
        detail = {}
        try:
            current = inventory_hermes_home(root, rules=rules, detail=detail)
        except FileNotFoundError:   # an entry vanished during the walk: the home is not at rest
            digests.append(None)
            previous = None
            continue
        digests.append(inventory_sha256(current))
        if previous is not None and current == previous:
            after = current
            break
        previous = current
    if after is None:
        raise HermesStateError(f'the Hermes home did not come to rest in {max_reads} metadata reads')
    digest_changes = None
    if before_detail is not None:
        digest_changes = {name: [path for path in before_detail.get(name, {}).keys() | rows.keys() if before_detail.get(name, {}).get(path) != rows.get(path)]
                          for name, rows in detail.items()}
    classes = classify_hermes_changes(before, after, session_id=session_id, started_ns=started_ns, rules=rules, digest_changes=digest_changes)
    entries = _state_entries(after)
    artifacts = []
    temporary = destination.parent / f'.{destination.name}-store-copy'
    try:
        for name, folder, private in (('session_owned', SESSION_COPY, False), ('run_owned', RUN_COPY, True)):
            for relative in classes[name]:
                entry = entries[relative]
                copied = f'{folder}/{relative}'
                digest = _copy_exact(root, relative, entry, destination / copied)
                artifacts.append({'relative_path': relative, 'class': name, 'private_only': private,
                                  'role': 'native-session-file' if name == 'session_owned' else 'run-private', 'copied_path': copied,
                                  'filesystem_id': f"{entry['device']}:{entry['inode']}", 'size_bytes': entry['size_bytes'], 'sha256': digest})
        store = _read_session_stores(root, destination, entries, session_id, rules, temporary)
        if inventory_hermes_home(root, rules=rules) != after:
            raise HermesStateError('the Hermes home changed during the copy')
    except FileNotFoundError as error:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(temporary, ignore_errors=True)
        raise HermesStateError('an entry of the Hermes home vanished during the copy') from error
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    found = [name for name in ('session_owned', 'run_owned', 'shared_changed') if classes[name]]
    receipt = {
        'schema_version': STATE_SCHEMA, 'scope': STATE_SCOPE, 'source_root': str(root), 'root_identity': after['root_identity'],
        'attempt_id': attempt_id, 'turn': turn, 'session_id': session_id, 'build': build, 'started_ns': started_ns,
        'metadata_only_before_after': True, 'preexisting_contents_opened': False, 'shared_store_rows_read': 'this session only',
        'shared_store_exception': ('the session store files were copied as bytes to a private temporary directory; only the copy was '
                                   'opened; only rows of this session were exported; the copy was deleted'),
        'before_inventory_sha256': inventory_sha256(before), 'after_inventory_sha256': digests[-1],
        'quiescence': {'required_equal_reads': 2, 'reads': len(digests), 'interval_seconds': interval_seconds, 'stable': True,
                       'inventory_sha256': digests, 'unchanged_after_copy': True},
        'rules': rules, 'rules_sha256': _sha(_canonical(rules)),
        'classes': {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')},
        'session_directories': classes['session_directories'],
        'accounting': {'changed_entries': classes['changed_entries'], 'classes_found': found, 'unexplained': [],
                       'every_changed_entry_accounted': True, 'shared_entries_changed_content_not_read': shared,
                       'session_store_rows_read': list(rules['session_stores']), 'statement': _statement(classes, store)},
        'artifacts': artifacts, 'session_store': store}
    return receipt, after


def _strip_paths(shared):
    return [{key: value for key, value in row.items() if key != 'changed_paths'} for row in shared]


def verify_hermes_state_capture(receipt, destination, *, before, after):
    """Offline check of a whole-home receipt against its two inventories, its copies and its row export."""
    if receipt.get('schema_version') != STATE_SCHEMA or receipt.get('scope') != STATE_SCOPE:
        raise HermesStateError('not a whole Hermes home receipt')
    quiet = receipt.get('quiescence', {})
    digests = quiet.get('inventory_sha256', [])
    if (inventory_sha256(before) != receipt.get('before_inventory_sha256') or inventory_sha256(after) != receipt.get('after_inventory_sha256')
            or before.get('root') != receipt.get('source_root') or quiet.get('stable') is not True
            or len(digests) < 2 or digests[-1] != digests[-2] or digests[-1] != receipt['after_inventory_sha256']
            or quiet.get('reads') != len(digests) or quiet.get('unchanged_after_copy') is not True):
        raise HermesStateError('receipt differs from the supplied inventory or its quiescence is incomplete')
    rules, session_id = receipt.get('rules'), receipt.get('session_id')
    if not isinstance(rules, dict) or receipt.get('rules_sha256') != _sha(_canonical(rules)):
        raise HermesStateError('receipt does not bind its rule table')
    classes = classify_hermes_changes(before, after, session_id=session_id, started_ns=receipt['started_ns'], rules=rules)
    recorded = receipt.get('classes', {})
    for row in recorded.get('shared_changed', []):
        folders, paths = set(row.get('changed_directories', [])), row.get('changed_paths')
        parents = {path.rsplit('/', 1)[0] for path in paths or []}
        if paths is not None and (any(carries_session_id(path, session_id) for path in paths) or not parents <= folders
                                  or any(folder not in parents and folder not in paths for folder in folders)):
            raise HermesStateError(f"changed paths of a digested subtree differ from its directory digests: {row['relative_path']}")
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    accounting, store = receipt.get('accounting', {}), receipt.get('session_store', {})
    if (any(recorded.get(name) != classes[name] for name in ('session_owned', 'run_owned', 'directories_changed'))
            or _strip_paths(recorded.get('shared_changed', [])) != classes['shared_changed']
            or receipt.get('session_directories') != classes['session_directories']
            or accounting.get('changed_entries') != classes['changed_entries'] or accounting.get('unexplained') != []
            or accounting.get('every_changed_entry_accounted') is not True
            or accounting.get('shared_entries_changed_content_not_read') != shared
            or accounting.get('session_store_rows_read') != list(rules['session_stores'])
            or accounting.get('statement') != _statement(classes, store)
            or receipt.get('metadata_only_before_after') is not True or receipt.get('preexisting_contents_opened') is not False
            or receipt.get('shared_store_rows_read') != 'this session only'):
        raise HermesStateError('receipt classification differs from the inventories')
    entries = _state_entries(after)
    expected = {name: 'session_owned' for name in classes['session_owned']} | {name: 'run_owned' for name in classes['run_owned']}
    artifacts = receipt.get('artifacts', [])
    if len(artifacts) != len(expected) or {row['relative_path'] for row in artifacts} != set(expected):
        raise HermesStateError('copied file set differs from the classification')
    destination = Path(destination)
    present = {path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file() or path.is_symlink()}
    if present != {row['copied_path'] for row in artifacts} | {ROWS_FILE, STORE_SCHEMA_FILE}:
        raise HermesStateError('copied file set on disk differs from the receipt')
    for row in artifacts:
        entry, kind = entries[row['relative_path']], expected[row['relative_path']]
        target = destination / row['copied_path']
        if (row.get('class') != kind or row.get('private_only') is not (kind == 'run_owned')
                or row['copied_path'] != f"{SESSION_COPY if kind == 'session_owned' else RUN_COPY}/{row['relative_path']}"
                or row['filesystem_id'] != f"{entry['device']}:{entry['inode']}" or row['size_bytes'] != entry['size_bytes']
                or target.is_symlink() or target.stat().st_size != entry['size_bytes']
                or _sha(target.read_bytes()) != row['sha256']):
            raise HermesStateError(f"copied file differs from its receipt: {row['relative_path']}")
    # The row export: bound by digest, only this session, counts equal to the receipt, store copy gone.
    for name, key in ((ROWS_FILE, 'rows_export'), (STORE_SCHEMA_FILE, 'schema_export')):
        target, bound = destination / name, store.get(key, {})
        raw = b'' if target.is_symlink() else target.read_bytes()
        if bound.get('path') != name or bound.get('size_bytes') != len(raw) or bound.get('sha256') != _sha(raw):
            raise HermesStateError(f'{name} differs from its receipt')
    rows = json.loads((destination / ROWS_FILE).read_bytes())
    if rows.get('schema_version') != ROWS_SCHEMA or rows.get('session_id') != session_id:
        raise HermesStateError('row export is not bound to the session')
    check_rows_name_only_session(rows, session_id)
    summaries = store.get('stores', [])
    if [item.get('store') for item in summaries] != list(rules['session_stores']) or [item['store'] for item in rows['stores']] != list(rules['session_stores']):
        raise HermesStateError('row export does not cover the session stores of the rule table')
    for summary, exported in zip(summaries, rows['stores'], strict=True):
        counts = {table['table']: len(table['rows']) for table in exported['tables']}
        if (summary.get('schema_sha256') != exported.get('schema_sha256')
                or {row['table']: row['selected_rows'] for row in summary['tables'] if row['selected_rows']} != counts
                or summary.get('selected_rows') != sum(counts.values()) or not counts
                or not any(reason.startswith('exact:') for table in exported['tables'] for row in table['rows'] for reason in row['selected_by'])):
            raise HermesStateError(f"row counts of {summary.get('store')} differ from the receipt")
        for copied in summary.get('copied_files', []):
            entry = entries.get(copied['relative_path'], {})
            if copied['filesystem_id'] != f"{entry.get('device')}:{entry.get('inode')}" or copied['size_bytes'] != entry.get('size_bytes'):
                raise HermesStateError(f"copied store file differs from the inventory: {copied['relative_path']}")
        if summary['store'] not in {copied['relative_path'] for copied in summary.get('copied_files', [])}:
            raise HermesStateError('the store file itself was not copied')
    temporary = store.get('temporary_copy', {})
    if (store.get('rows_of_other_sessions_exported') != 0 or temporary.get('deleted') is not True
            or temporary.get('directory') != f'.{destination.name}-store-copy'
            or (destination.parent / temporary['directory']).exists()):
        raise HermesStateError('the temporary copy of the session store is not proved deleted')
    return True


def refusal_listing(error, *, attempt_id, turn, session_id, rules=None):
    """What the operator needs after a refusal: the reason, each unexplained path, and the table to extend."""
    return {'schema_version': REFUSAL_SCHEMA, 'attempt_id': attempt_id, 'turn': turn, 'session_id': session_id,
            'reason': str(error), 'unexplained': list(getattr(error, 'unexplained', [])), 'rules': rules_table(rules),
            'how_to_extend': 'Add a pattern to RULES in session_bench/hermes_state_evidence.py: run_owned (new file of the run, copied, private), '
                             'shared_transient (lock or marker file, metadata only), shared_in_session_areas (shared index inside a session area), '
                             'or digest_subtrees (large subtree, metadata only). Then run a new capture; a refused capture is not reused.',
            'metadata_only': True}
