"""OpenClaw local agent capture in the owner's normal state (receipt ``openclaw-state-root-v1``).

The row is the regular local agent, ``openclaw agent --local``, with the
``codex`` harness plugin. It runs in the owner's normal OpenClaw home: an
isolated state has no login, and credentials may not be copied (probes of
2026-10-07). The agent's workspace is fixed by the owner's configuration, so
the controller places the fixture in that workspace as ``fixture_project`` and
always removes it at the end.

The native record is what OpenClaw and its Codex plugin wrote. The controller
lists the whole OpenClaw home by metadata before the first turn and after
each turn. No old file is opened and no link is followed. Every entry that is
new, changed or removed must fall into one class (``classify_openclaw_changes``):

- **session-owned**: a file whose path carries the OpenClaw session id or the
  Codex thread id of the session (rollout file, shell snapshot, thread lock). Copied.
- **run-owned**: a new file that matches ``RULES['run_owned']`` and was born
  during the capture. Copied, private only.
- **shared**: an old file that changed, a SQLite sidecar beside it, a lock,
  marker or temporary file, or a digested subtree. Metadata only.
- **directory**: only a container.

Anything else stops the capture with a refusal listing. ``RULES`` is the
small table to extend after a refusal.

One exception to "content is not read": the SQLite stores in
``RULES['session_stores']``. Each is copied as bytes to a private temporary
directory, the COPY is opened read-only, the rows that name the test session
(session id, session key or Codex thread id) are exported, and the copy is
deleted (``export_test_session_rows``). A table whose name, or one of whose
column names, suggests secrets is never read, also when a row names the session.

The generic helpers (metadata walk, exact copy, schema read) are imported from
``hermes_state_evidence``. That module is bound by digest in the Hermes
packets, so it is used as it is and not changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import sqlite3
import stat
import socket
import subprocess
import tempfile
import threading
import time
from typing import Callable, Mapping
from urllib.parse import quote
import uuid

from .hermes_state_evidence import (
    SIDECAR_SUFFIXES, HermesStateError, _canonical, _changed_directories, _copy_exact, _encode, _matches, _quote, _read_schema,
    _refusal_row, _row_key, _select, _sha, _sidecar_of, _singular, _state_entries, _summary, _text, _value, _write_new,
    carries_session_id, inventory_hermes_home, inventory_sha256,
)
from .hermes_state_evidence import STATE_SCOPE as _WALK_SCOPE
from .hermes_survival_capture import _FIXTURE_FILES, _STOP, _copy_tree, _json, _tree, _validate_ledger, _write_json
from .openclaw_acp_observer import OpenClawAcpError, stream_line, turn_summary
from .openclaw_envelope_observer import OpenClawEnvelopeError, envelope_summary
from .workload_instance import instantiate_workload

SCHEMA = 'session-bench-openclaw-survival-capture-v2'
STATE_SCHEMA = 'openclaw-state-root-v1'
STATE_SCOPE = 'whole_openclaw_home'
ROWS_SCHEMA = 'session-bench-openclaw-session-store-rows-v1'
REFUSAL_SCHEMA = 'session-bench-openclaw-state-refusal-v1'
SESSION_COPY, RUN_COPY = 'session-owned', 'run-owned-private'
ROWS_FILE, STORE_SCHEMA_FILE = 'session-store-rows.json', 'session-store-schema.json'
VERSION = 'OpenClaw 2026.9.8 (fc23bc8)'
HARNESS = 'codex'
TURN_TIMEOUT_SECONDS = 300
STDOUT_NAME = 'stdout.json'
FIXTURE_NAME = 'fixture_project'
WORKSPACE_KEY = 'agents.defaults.workspace'
ROUTES = ('local', 'acp')
GATEWAY_PORT = 18789
# The owner's configuration has live message channels and a timed heartbeat. A gateway that the controller starts
# must not connect a channel, run a timed job, watch mail, serve a browser or canvas host, announce itself, or update.
GATEWAY_ENVIRONMENT = {'OPENCLAW_SKIP_CHANNELS': '1', 'OPENCLAW_SKIP_CRON': '1', 'OPENCLAW_SKIP_GMAIL_WATCHER': '1',
                       'OPENCLAW_SKIP_BROWSER_CONTROL_SERVER': '1', 'OPENCLAW_SKIP_CANVAS_HOST': '1',
                       'OPENCLAW_DISABLE_BONJOUR': '1', 'OPENCLAW_NO_AUTO_UPDATE': '1'}
CODEX_HOME = 'agents/main/agent/codex-home'
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

# The table to extend after a refusal. Paths are relative to the OpenClaw home.
# Patterns are full-match regular expressions. The receipt stores the table it used.
RULES = {
    # Shared SQLite stores whose rows of the test session are exported from a private copy.
    'session_stores': ('agents/main/agent/openclaw-agent.sqlite', CODEX_HOME + '/state_5.sqlite',
                       CODEX_HOME + '/thread_history_1.sqlite', 'state/openclaw.sqlite'),
    # A store of 'session_stores' that may hold no row of the session (then only counts are recorded).
    'optional_session_stores': ('state/openclaw.sqlite',),
    # Large subtrees recorded as one entry with a digest of every entry inside.
    # A change inside is reported by path as a shared change and is never read.
    'digest_subtrees': ('npm', 'browser', 'backups', 'bin', 'completions', 'canvas', CODEX_HOME + '/.tmp', CODEX_HOME + '/skills'),
    # Areas with one entry per session. A changed old file here belongs to another session.
    'session_areas': ('agents/main/sessions', CODEX_HOME + '/sessions'),
    # Old shared files inside a session area that may change (an index of all sessions).
    'shared_in_session_areas': (r'agents/main/sessions/sessions\.json(\.lock)?',),
    # The rollout file of a Codex thread. Group 1 is the thread id.
    'thread_rollout': (re.escape(CODEX_HOME) + rf'/sessions/(?:[^/]+/)*rollout-[^/]*-({_UUID})\.jsonl',),
    # New files that a turn creates without an id of the session in their path.
    'run_owned': (r'tmp/plugin-captures/[^/]+/owner\.sqlite(-wal|-shm)?',
                  r'tmp/plugin-captures/[^/]+/native/admission-[^/]+/content/package\.json',
                  re.escape(CODEX_HOME) + r'/tmp/arg0/codex-arg0[^/]*/\.lock',
                  r'logs/[^/]+'),
    # Lock, marker and temporary files that may appear or disappear. Metadata only.
    'shared_transient': (r'(.*/)?[^/]+\.lock', r'(.*/)?\.lock', r'(.*/)?[^/]+\.pid', r'(.*/)?\.DS_Store', r'(.*/)?[^/]+\.tmp',
                         r'tmp/plugin-captures/.+', r'tmp/openclaw-[0-9]+/.+', re.escape(CODEX_HOME) + r'/tmp/.+',
                         r'(openclaw|clawdbot)\.json\.[^/]+', r'logs/[^/]+'),
    # Temporary directories that may disappear. Metadata only.
    'transient_directories': (r'tmp/plugin-captures/.+', r'tmp/openclaw-[0-9]+/.+', re.escape(CODEX_HOME) + r'/tmp/arg0/[^/]+'),
    # Where the session id of a session key is found when no stdout envelope gives it (route ``acp``):
    # store, table, key column, id column. Only the row of the test session key is read, from the private copy.
    'session_key_lookup': ('agents/main/agent/openclaw-agent.sqlite', 'session_nodes', 'session_key', 'current_session_id'),
    # A table is never read when its name, or the name of one of its columns, holds one of these words.
    'secret_words': ('auth', 'oauth', 'profile', 'token', 'credential', 'secret', 'key', 'password', 'passwd', 'apikey', 'bearer', 'cookie'),
    # Column names that hold such a word and are not secrets (keys of sessions and requests, token counts).
    'benign_columns': (r'(.*_)?session_key', r'(.*_)?idempotency_key', r'entry_key', r'tokens_used', r'token_budget',
                       r'(input|output|total|cached|cache_read|cache_write|reasoning|context|prompt|completion)_tokens'),
}
_ID = re.compile(r'[0-9A-Za-z][0-9A-Za-z_-]{15,}')
_SESSION_COLUMNS = frozenset({'session_id', 'sessionid', 'current_session_id'})
_THREAD_COLUMNS = frozenset({'thread_id', 'threadid'})
_ID_TABLES = {'sessions': 'session_id', 'session': 'session_id', 'threads': 'thread_id', 'thread': 'thread_id'}


class OpenClawStateError(HermesStateError):
    """The OpenClaw home changed in a way that the capture cannot explain, or a store lacks the session."""


class OpenClawCaptureError(RuntimeError):
    """The OpenClaw capture cannot safely continue."""


def rules_table(rules=None):
    """The rule table as plain JSON data (lists of strings)."""
    return {name: list(values) for name, values in sorted((rules or RULES).items())}


def inventory_openclaw_home(root, *, rules=None, detail=None):
    """Metadata of every entry under the OpenClaw home. No file is opened and no link is followed."""
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise OpenClawStateError('OpenClaw home must be an ordinary directory')
    return {**inventory_hermes_home(root, rules=rules_table(rules), detail=detail), 'scope': STATE_SCOPE}


def _entries(snapshot):
    if not isinstance(snapshot, Mapping) or snapshot.get('scope') != STATE_SCOPE:
        raise OpenClawStateError('not a whole OpenClaw home metadata inventory')
    return _state_entries({**snapshot, 'scope': _WALK_SCOPE})


def _in_area(path, areas):
    return any(path == area or path.startswith(area + '/') for area in areas)


def new_rollout_threads(before, after, *, rules=None):
    """Thread ids in the names of the Codex rollout files that are in ``after`` and not in ``before``."""
    pattern = rules_table(rules)['thread_rollout'][0]
    old = _entries(before)
    found = (re.fullmatch(pattern, path) for path in _entries(after) if path not in old)
    return sorted({match.group(1) for match in found if match})


def classify_openclaw_changes(before, after, *, session_id, thread_id, started_ns, key_id=None, acp_session_id=None, rules=None,
                              digest_changes=None):
    """Account for every entry that differs between two whole-home inventories, from metadata only.

    Rules, in this order, for each entry that is new, changed or removed:

    1. Its path carries the OpenClaw session id, the Codex thread id, or
       ``key_id`` (the id inside a session key that the controller made) or
       ``acp_session_id`` (the session id of the ACP bridge): a file
       is ``session_owned``, a directory is a session directory, anything else
       is refused. No such path may exist in ``before``.
    2. A ``directory-digest`` entry that exists before and after is
       ``shared_changed`` with the directories (and paths) that changed
       inside. A path inside that carries an id of the session is refused.
    3. A directory is only a container (see below). A removed directory is
       ``shared_changed`` when it matches ``transient_directories``; else refused.
    4. An old entry that changed is ``shared_changed``. Inside a session area
       it is refused (another session changed), unless ``shared_in_session_areas`` names it.
    5. A ``-wal``, ``-shm`` or ``-journal`` file that appeared or disappeared
       beside an old file is ``shared_changed``.
    6. A new file that matches ``run_owned`` is ``run_owned`` when it was born
       at or after ``started_ns``. Born earlier, it is an old file under a new
       name: ``shared_changed``, not read.
    7. A file or link that matches ``shared_transient`` and appeared or
       disappeared is ``shared_changed``. Inside a session area only a name
       in ``shared_in_session_areas`` passes.
    8. Everything else is unexplained.

    A new directory must hold a classified entry. An old directory may change
    when a direct child was added, removed or changed, or when only its own
    times moved (``touched_empty``, ``touched_children_unchanged``).

    Unexplained entries raise ``OpenClawStateError``; its ``unexplained`` list
    names each path with the reason. Nothing is read.
    """
    rules = rules_table(rules)
    old, new = _entries(before), _entries(after)
    if before.get('root') != after.get('root') or before.get('root_identity') != after.get('root_identity'):
        raise OpenClawStateError('OpenClaw home identity changed')
    ids = {'openclaw_session_id': session_id, 'codex_thread_id': thread_id}
    if key_id is not None and key_id != session_id:   # the id that the controller put into the session key (route ``acp``)
        ids['openclaw_session_key_id'] = key_id
    if acp_session_id is not None:   # the session id of the ACP bridge, from the ACP stream (route ``acp``)
        ids['acp_session_id'] = acp_session_id
    if any(not isinstance(value, str) or not _ID.fullmatch(value) for value in ids.values()) or len(set(ids.values())) != len(ids):
        raise OpenClawStateError('session id or Codex thread id is missing or too short to name files safely')

    def owner(path):
        return next((label for label, value in ids.items() if carries_session_id(path, value)), None)

    if any(owner(path) for path in old):
        raise OpenClawStateError('a path of the session or of its Codex thread existed before the capture')
    changed = sorted(path for path in old.keys() | new.keys() if old.get(path) != new.get(path))
    session, directories, run, shared, containers, unexplained = [], [], [], [], [], []

    def share(path, change, was, now, **extra):
        shared.append({'relative_path': path, 'kind': (now or was)['kind'], 'change': change,
                       'before': _summary(was), 'after': _summary(now), 'content_read': False, **extra})

    for path in changed:
        was, now = old.get(path), new.get(path)
        kinds = {entry['kind'] for entry in (was, now) if entry}
        sidecar = _sidecar_of(path, old)
        if owner(path):
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
            named = [name for name in folders + (paths or []) if owner(name)]
            if named:
                unexplained.extend(_refusal_row(name, None, now, 'a path with an id of the session lies inside a digested subtree; remove the subtree from digest_subtrees')
                                   for name in sorted(set(named)))
                continue
            extra = {'changed_directories': folders}
            if paths is not None:
                extra['changed_paths'] = paths
            share(path, 'modified', was, now, **extra)
        elif 'directory' in kinds:
            if kinds == {'directory'} and now is None and _matches(rules['transient_directories'], path):
                share(path, 'removed', was, now)
            elif len(kinds) != 1 or now is None:
                unexplained.append(_refusal_row(path, was, now, 'a directory was removed or changed kind'))
            else:
                containers.append(path)
        elif was is not None and now is not None:
            if _in_area(path, rules['session_areas']) and not _matches(rules['shared_in_session_areas'], path):
                unexplained.append(_refusal_row(path, was, now, 'an entry of another session changed during the capture'))
            else:
                share(path, 'modified', was, now)
        elif sidecar is not None and (not _in_area(sidecar, rules['session_areas']) or _matches(rules['shared_in_session_areas'], sidecar)):
            share(path, 'created_sidecar' if was is None else 'removed_sidecar', was, now)
        elif was is None and kinds == {'file'} and _matches(rules['run_owned'], path):
            born = now['birth_ns'] if now['birth_ns'] is not None else now['ctime_ns']
            if born < started_ns:
                share(path, 'created_old_identity', was, now)
            else:
                run.append(path)
        elif (kinds <= {'file', 'symlink'} and _matches(rules['shared_transient'], path)
              and (not _in_area(path, rules['session_areas']) or _matches(rules['shared_in_session_areas'], path))):
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
            # Only the directory's own times moved: a child lived only between the two inventories.
            share(path, 'touched_children_unchanged', old[path], new[path])
            explained = True
        if not explained:
            unexplained.append(_refusal_row(path, old.get(path), new.get(path), 'a directory changed and no entry inside explains it'))
    if unexplained:
        unexplained.sort(key=lambda row: row['relative_path'])
        names = [row['relative_path'] for row in unexplained]
        raise OpenClawStateError(f'{len(names)} unexplained new, changed or removed entries: ' + ', '.join(names[:20])
                                 + (' …' if len(names) > 20 else ''), unexplained)
    shared.sort(key=lambda row: row['relative_path'])
    return {'session_id': session_id, 'thread_id': thread_id, 'session_owned': session, 'owners': {path: owner(path) for path in session},
            'session_directories': directories, 'run_owned': run, 'shared_changed': shared, 'directories_changed': containers,
            'changed_entries': len(changed)}


# --- Rows of the test session in the shared stores ---

def _words(name):
    return [part.lower() for part in re.findall(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+', name)]


def suggests_secret(name, rules=None):
    """True when one word of a table or column name is in ``secret_words`` (also as a plural)."""
    words = set(rules_table(rules)['secret_words'])
    return any(word in words or (word.endswith('s') and word[:-1] in words) for word in _words(name))


def denied_table(name, columns, rules=None):
    """Why a table is never read, or None. Only names are judged; no row is read for this."""
    rules = rules_table(rules)
    if suggests_secret(name, rules):
        return 'the table name suggests secrets'
    named = [column for column in columns if suggests_secret(column, rules) and not _matches(rules['benign_columns'], column.lower())]
    return 'column names suggest secrets: ' + ', '.join(named) if named else None


def _identities(identity):
    values = [identity.get('session_id'), identity.get('thread_id')]
    if any(not isinstance(value, str) or not _ID.fullmatch(value) for value in values) or values[0] == values[1]:
        raise OpenClawStateError('session id or Codex thread id is missing or too short to select rows safely')
    key = identity.get('session_key')
    if key is not None:
        # The session key is used only when it carries the session id, or the id that the controller put into the key
        # (route ``acp``): a key such as ``agent:main:main`` is shared.
        if not isinstance(key, str) or not _own_key(key, identity):
            raise OpenClawStateError('the session key does not carry the session id')
        values.append(key)
    bridge = identity.get('acp_session_id')
    if bridge is not None:
        # The session id of the ACP bridge (route ``acp``). The gateway keeps its replay rows under this id.
        if not isinstance(bridge, str) or not _ID.fullmatch(bridge) or bridge in values:
            raise OpenClawStateError('the ACP session id is too short or equals another id')
        values.append(bridge)
    return values


def _own_key(value, identity):
    marks = [mark for mark in (identity.get('session_id'), identity.get('key_id')) if isinstance(mark, str) and _ID.fullmatch(mark)]
    return isinstance(value, str) and any(carries_session_id(value, mark) for mark in marks)


def _plain(value):
    if isinstance(value, dict) and 'blob_hex' in value:
        try:
            return bytes.fromhex(value['blob_hex']).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return value
    return value


def check_rows_name_only_test_session(export, identity, *, rules=None):
    """Fail when an exported row names another session or thread, or comes from a denied table."""
    session_id, thread_id = _identities(identity)[:2]
    expected = {'session_id': session_id, 'thread_id': thread_id}
    sessions = {session_id, identity.get('acp_session_id')} - {None}
    for store in export['stores']:
        for table in store['tables']:
            name = table['table']
            if denied_table(name, table['columns'], rules):
                raise OpenClawStateError(f'an exported table is on the deny rule: {name}')
            key = _ID_TABLES.get(name.lower())
            keyed = [column for column in table['columns'] if key and (column in table['primary_key'] or column.lower() == 'id')]
            for row in table['rows']:
                for column, raw in row['values'].items():
                    value, lower = _plain(raw), column.lower()
                    if value is None:
                        continue
                    wrong = ((lower in _SESSION_COLUMNS and value not in sessions) or (lower in _THREAD_COLUMNS and value != thread_id)
                             or (lower == 'session_key' and not _own_key(value, identity))
                             or (column in keyed and value != expected[key]))
                    if wrong:
                        raise OpenClawStateError(f'an exported row of {name} names another session or thread in column {column}')
    return True


def export_test_session_rows(database, identity, *, store, rules=None, required=True):
    """Read the rows of the test session from a private COPY of a store. Returns ``(export, schema, summary)``.

    ``identity`` holds ``session_id``, ``thread_id`` and, when known, ``session_key``.

    1. *Deny*: a table on the deny rule (``denied_table``) is not queried at
       all, not as a source and not as a join target. Its name and columns
       stay in the schema export.
    2. *Exact*: in every other table, every row with a column whose value is
       exactly one of the identities (as text or as bytes).
    3. *Join, one level*: rows of another table that reference a row of step 2
       by its key: a declared single-column foreign key, or a column named
       ``<table>_id`` / ``<singular table>_id`` with zero orphan values in the
       whole table. Rows found by a join are not followed further.

    A row that only holds an identity inside a longer value is not selected;
    such rows are counted per table (``rows_that_contain_an_id``). For other
    sessions only row counts are read. With ``required=False`` a store without
    any row of the session gives an empty export instead of an error.
    """
    rules = rules_table(rules)
    values = _identities(identity)
    marks = ', '.join(f'?{index}, CAST(?{index} AS BLOB)' for index in range(1, len(values) + 1))
    raw_values = {value.encode('utf-8') for value in values}

    def is_identity(value):
        return value in values if isinstance(value, str) else isinstance(value, bytes) and bytes(value) in raw_values

    connection = sqlite3.connect('file:' + quote(str(Path(database).absolute())) + '?mode=ro', uri=True)
    try:
        connection.text_factory = _text
        connection.execute('PRAGMA query_only=ON')
        schema, schema_sha256 = _read_schema(connection)
        tables = {table['name']: table for table in schema['tables']}
        denied = {name: reason for name, table in tables.items() if (reason := denied_table(name, table['columns'] or list(table['column_types']), rules))}
        selected = {name: {} for name in tables}   # table -> row key -> [rowid, values, reasons]
        totals, mentions, not_scanned = {}, {}, []

        def add(table, rows, reason):
            for rowid, row in rows:
                slot = selected[table['name']].setdefault(_row_key(table, rowid, row), [rowid, row, []])
                if reason not in slot[2]:
                    slot[2].append(reason)

        for table in tables.values():
            name = table['name']
            if name in denied:
                totals[name] = mentions[name] = None
                continue
            try:
                if table['scan_error']:
                    raise sqlite3.OperationalError(table['scan_error'])
                totals[name] = connection.execute(f'SELECT COUNT(*) FROM {_quote(name)}').fetchone()[0]
                where = ' OR '.join(f'{_quote(column)} IN ({marks})' for column in table['columns'])
                for rowid, row in _select(connection, table, where, values) if table['columns'] else []:
                    for column in table['columns']:
                        if is_identity(row[column]):
                            add(table, [(rowid, row)], f'exact:{column}')
                # A count only: rows that hold an identity inside a longer value (for example in JSON text).
                inside = ' OR '.join(f'instr(CAST({_quote(column)} AS TEXT), ?{index}) > 0'
                                     for column in table['columns'] for index in range(1, len(values) + 1))
                mentions[name] = connection.execute(f'SELECT COUNT(*) FROM {_quote(name)} WHERE {inside}', values).fetchone()[0] if inside else 0
            except sqlite3.Error as error:
                if not (table['virtual'] or table['virtual_shadow']):
                    raise OpenClawStateError(f'table {name} of {store} cannot be read') from error
                totals[name] = mentions[name] = None
                selected[name].clear()
                not_scanned.append({'table': name, 'reason': 'virtual table or its shadow table could not be queried'})
        seeds = {name: [slot[1] for slot in rows.values()] for name, rows in selected.items() if rows}
        skipped = set(denied) | {row['table'] for row in not_scanned}
        joins, candidates = [], []
        for parent_name, seed_rows in sorted(seeds.items()):
            parent = tables[parent_name]
            for child in tables.values():
                if child['name'] == parent_name or child['scan_error'] or child['name'] in skipped:
                    continue
                links = []
                for key in child['foreign_keys']:
                    if key['references_table'].lower() == parent_name.lower() and key['columns'] == 1:
                        target = key['references_column'] or (parent['primary_key'][0] if len(parent['primary_key']) == 1 else None)
                        if target in parent['columns']:
                            links.append((key['column'], target, 'declared foreign key'))
                if len(parent['primary_key']) == 1:
                    for column in child['columns']:
                        if column.lower() in {parent_name.lower() + '_id', _singular(parent_name) + '_id'} and column not in {link[0] for link in links}:
                            links.append((column, parent['primary_key'][0], 'column name'))
                for column, target, basis in links:
                    keys = sorted({row[target] for row in seed_rows if row[target] is not None and not isinstance(row[target], float)},
                                  key=lambda item: (type(item).__name__, item))
                    described = {'table': child['name'], 'column': column, 'references': f'{parent_name}.{target}', 'basis': basis}
                    parts = [keys[index:index + 500] for index in range(0, len(keys), 500)]
                    if basis == 'column name':
                        orphans = connection.execute(
                            f"SELECT COUNT(*) FROM {_quote(child['name'])} WHERE {_quote(column)} IS NOT NULL AND {_quote(column)} NOT IN "
                            f"(SELECT {_quote(target)} FROM {_quote(parent_name)} WHERE {_quote(target)} IS NOT NULL)").fetchone()[0]
                        described['orphan_values_in_whole_table'] = orphans
                        if orphans:
                            matching = sum(connection.execute(
                                f"SELECT COUNT(*) FROM {_quote(child['name'])} WHERE {_quote(column)} IN ({','.join('?' * len(part))})", part).fetchone()[0]
                                for part in parts)
                            candidates.append({**described, 'matching_rows_not_exported': matching,
                                               'reason': 'the column does not behave as a key of the referenced table'})
                            continue
                    found = 0
                    for part in parts:
                        rows = _select(connection, child, f"{_quote(column)} IN ({','.join('?' * len(part))})", part)
                        found += len(rows)
                        add(child, rows, f'join:{column}->{parent_name}.{target}')
                    joins.append({**described, 'keys': len(keys), 'rows': found})
        export_tables, summary_tables = [], []
        for name in sorted(tables):
            table, rows = tables[name], selected[name]
            ordered = sorted(rows.values(), key=lambda slot: (slot[0] is None, slot[0] if slot[0] is not None else 0, _canonical([_value(v) for v in slot[1].values()])))
            summary_tables.append({'table': name, 'denied': denied.get(name), 'total_rows': totals[name], 'selected_rows': len(ordered),
                                   'selected_by': sorted({reason for slot in ordered for reason in slot[2]}),
                                   'rows_with_exact_match': sum(any(reason.startswith('exact:') for reason in slot[2]) for slot in ordered),
                                   'rows_that_contain_an_id': mentions[name],
                                   'other_rows_counted_not_read': None if totals[name] is None else totals[name] - len(ordered)})
            if ordered:
                export_tables.append({'table': name, 'columns': table['columns'], 'column_types': table['column_types'],
                                      'primary_key': table['primary_key'], 'has_rowid': table['has_rowid'],
                                      'rows': [{'rowid': rowid, 'selected_by': sorted(why), 'values': {key: _value(value) for key, value in row.items()}}
                                               for rowid, row, why in ordered]})
    except sqlite3.Error as error:
        raise OpenClawStateError(f'the copy of {store} cannot be read: {type(error).__name__}') from error
    finally:
        connection.close()
    if not seeds and required:
        raise OpenClawStateError(f'no row of {store} names the session or its Codex thread: the store did not record the session, or an id is wrong')
    entry = {'store': store, 'user_version': schema['user_version'], 'schema_sha256': schema_sha256, 'tables': export_tables}
    summary = {'store': store, 'required': required, 'user_version': schema['user_version'], 'application_id': schema['application_id'],
               'schema_sha256': schema_sha256, 'tables': summary_tables, 'joins': joins, 'join_candidates_not_exported': candidates,
               'tables_not_scanned': not_scanned,
               'tables_denied': [{'table': name, 'reason': reason} for name, reason in sorted(denied.items())],
               'selected_rows': sum(row['selected_rows'] for row in summary_tables)}
    check_rows_name_only_test_session({'stores': [entry]}, identity, rules=rules)
    return entry, {'store': store, **schema}, summary


def _discover_session_id(root, entries, session_key, key_id, rules, temporary):
    """The session id of a session key that the controller made, from a private copy of the agent store. The copy is deleted."""
    lookup = rules.get('session_key_lookup')
    if (not lookup or not isinstance(session_key, str) or not isinstance(key_id, str) or not _ID.fullmatch(key_id)
            or not carries_session_id(session_key, key_id)):
        raise OpenClawStateError('the session id is unknown and the session key is not one that the controller made')
    store, table, key_column, id_column = lookup
    if temporary.exists() or temporary.is_symlink():
        raise OpenClawStateError('temporary store directory must be new')
    try:
        temporary.mkdir(mode=0o700)
        for name in [store] + [store + suffix for suffix in SIDECAR_SUFFIXES if store + suffix in entries]:
            if entries.get(name, {}).get('kind') != 'file':
                raise OpenClawStateError(f'a session store is missing from the OpenClaw home: {name}')
            _copy_exact(root, name, entries[name], temporary / Path(name).name)
        connection = sqlite3.connect('file:' + quote(str((temporary / Path(store).name).absolute())) + '?mode=ro', uri=True)
        try:
            connection.execute('PRAGMA query_only=ON')
            found = [row[0] for row in connection.execute(
                f'SELECT DISTINCT {_quote(id_column)} FROM {_quote(table)} WHERE {_quote(key_column)} = ?1', (session_key,))]
        except sqlite3.Error as error:
            raise OpenClawStateError(f'the row of the session key cannot be read from the copy of {store}: {type(error).__name__}') from error
        finally:
            connection.close()
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        if temporary.exists() or temporary.is_symlink():
            raise OpenClawStateError('the temporary copy of the session stores could not be deleted')
    if len(found) != 1 or not isinstance(found[0], str) or not _ID.fullmatch(found[0]):
        raise OpenClawStateError(f'{store} holds {len(found)} session ids for the session key of the capture; exactly one is required')
    return found[0]


def _read_stores(root, destination, entries, identity, rules, temporary):
    """Copy each session store with its sidecars, read the rows of the test session from the copy, delete the copy."""
    if temporary.exists() or temporary.is_symlink():
        raise OpenClawStateError('temporary store directory must be new')
    exports, schemas, stores = [], [], []
    try:
        temporary.mkdir(mode=0o700)
        for index, store in enumerate(rules['session_stores']):
            required = store not in rules['optional_session_stores']
            if entries.get(store, {}).get('kind') != 'file':
                if required:
                    raise OpenClawStateError(f'a session store is missing from the OpenClaw home: {store}')
                stores.append({'store': store, 'required': False, 'present': False, 'selected_rows': 0, 'copied_files': []})
                continue
            folder, copied = temporary / str(index), []
            for name in [store] + [store + suffix for suffix in SIDECAR_SUFFIXES if store + suffix in entries]:
                entry = entries[name]
                if entry['kind'] != 'file':
                    raise OpenClawStateError(f'a file of a session store is not an ordinary file: {name}')
                digest = _copy_exact(root, name, entry, folder / Path(name).name)
                copied.append({'relative_path': name, 'filesystem_id': f"{entry['device']}:{entry['inode']}",
                               'size_bytes': entry['size_bytes'], 'sha256': digest})
            export, schema, summary = export_test_session_rows(folder / Path(store).name, identity, store=store, rules=rules, required=required)
            exports.append(export)
            schemas.append(schema)
            stores.append({**summary, 'present': True, 'copied_files': copied})
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        if temporary.exists() or temporary.is_symlink():
            raise OpenClawStateError('the temporary copy of the session stores could not be deleted')
    rows = {'schema_version': ROWS_SCHEMA, **{key: identity.get(key) for key in ('session_id', 'thread_id', 'session_key', 'key_id', 'acp_session_id')}, 'stores': exports}
    return {'rows_export': {'path': ROWS_FILE, **_write_new(destination / ROWS_FILE, _encode(rows))},
            'schema_export': {'path': STORE_SCHEMA_FILE, **_write_new(destination / STORE_SCHEMA_FILE, _encode({'stores': schemas}))},
            'stores': stores, 'selected_rows': sum(store['selected_rows'] for store in stores),
            'rows_of_other_sessions_exported': 0, 'rows_of_denied_tables_exported': 0, 'other_sessions': 'row counts only',
            'opened': 'private temporary copies only, read-only; the original stores were copied as bytes and never opened with SQLite',
            'temporary_copy': {'directory': temporary.name, 'deleted': True}}


def _statement(classes, store):
    shared = classes['shared_changed']
    return ('Metadata of the whole OpenClaw home was listed before the first turn and after this turn. '
            f"{classes['changed_entries']} entries were new, changed or removed and every one is accounted for: "
            f"{len(classes['session_owned'])} session-owned files (path carries the session id or the Codex thread id; copied), "
            f"{len(classes['run_owned'])} run-owned files (new files born during the capture; copied, private only), "
            f"{len(shared)} shared entries (old files, sidecars, lock, marker or temporary files, digested subtrees; metadata only, content not read), "
            f"{len(classes['session_directories']) + len(classes['directories_changed'])} directories. "
            f"From the session stores, {store.get('selected_rows')} rows that name the session, its key or its Codex thread, or reference such a row, "
            'were exported from private copies that were then deleted; rows of other sessions were counted, not read; '
            'tables whose names suggest secrets were not read. '
            'Another shared entry may hold records of this session. Its content was not read, so this receipt does not prove that it holds none.')


def capture_openclaw_state(root, destination, before, *, attempt_id, turn, session_id, started_ns, thread_id=None, session_key=None,
                           key_id=None, acp_session_id=None, build=None, before_detail=None, rules=None, interval_seconds=0.1, max_reads=6, sleep=time.sleep):
    """Wait for two equal metadata reads, classify every change, copy the owned files, export the session rows.

    ``thread_id`` is the Codex thread id from the stdout envelope, or None.
    The names of the new rollout files must confirm it; without it, exactly
    one new rollout file gives the id. ``session_id`` may be None when
    ``session_key`` is a key that the controller made and ``key_id`` is the id
    inside it: the session id is then read from the row of that key in the
    agent store (``session_key_lookup``). Returns ``(receipt, after_inventory)``.
    On any refusal the destination and the temporary store copies are removed
    and ``OpenClawStateError`` is raised.
    """
    root, destination = Path(root).absolute(), Path(destination)
    rules = rules_table(rules)
    if destination.exists() or destination.is_symlink():
        raise OpenClawStateError('capture destination must be new')
    if type(max_reads) is not int or max_reads < 2 or interval_seconds < 0:
        raise OpenClawStateError('at least two metadata reads are required')
    digests, previous, after, detail = [], None, None, {}
    for _ in range(max_reads):
        sleep(interval_seconds)
        detail = {}
        try:
            current = inventory_openclaw_home(root, rules=rules, detail=detail)
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
        raise OpenClawStateError(f'the OpenClaw home did not come to rest in {max_reads} metadata reads')
    threads = new_rollout_threads(before, after, rules=rules)
    if thread_id is None:
        if len(threads) != 1:
            raise OpenClawStateError(f'the Codex thread of the session cannot be told: {len(threads)} new rollout files and no thread id on stdout')
        thread_id, thread_source = threads[0], 'name of the one new rollout file'
    elif thread_id not in threads:
        raise OpenClawStateError('no new rollout file carries the Codex thread id of the stdout envelope')
    else:
        thread_source = 'stdout envelope, confirmed by the name of a new rollout file'
    temporary = destination.parent / f'.{destination.name}-store-copy'
    session_source = 'given by the controller (the --session-id value)'
    if session_id is None:
        session_id = _discover_session_id(root, _entries(after), session_key, key_id, rules, temporary)
        session_source = 'row of the session key in ' + rules['session_key_lookup'][0]
    identity = {'session_id': session_id, 'thread_id': thread_id, 'session_key': session_key, 'key_id': key_id,
                'acp_session_id': acp_session_id}
    digest_changes = None
    if before_detail is not None:
        digest_changes = {name: [path for path in before_detail.get(name, {}).keys() | rows.keys() if before_detail.get(name, {}).get(path) != rows.get(path)]
                          for name, rows in detail.items()}
    classes = classify_openclaw_changes(before, after, session_id=session_id, thread_id=thread_id, started_ns=started_ns,
                                        key_id=key_id, acp_session_id=acp_session_id, rules=rules, digest_changes=digest_changes)
    entries = _entries(after)
    artifacts = []
    try:
        for name, folder, private in (('session_owned', SESSION_COPY, False), ('run_owned', RUN_COPY, True)):
            for relative in classes[name]:
                entry = entries[relative]
                copied = f'{folder}/{relative}'
                digest = _copy_exact(root, relative, entry, destination / copied)
                row = {'relative_path': relative, 'class': name, 'private_only': private,
                       'role': 'native-session-file' if name == 'session_owned' else 'run-private', 'copied_path': copied,
                       'filesystem_id': f"{entry['device']}:{entry['inode']}", 'size_bytes': entry['size_bytes'], 'sha256': digest}
                if name == 'session_owned':
                    row['owner_id'] = classes['owners'][relative]
                artifacts.append(row)
        store = _read_stores(root, destination, entries, identity, rules, temporary)
        if inventory_openclaw_home(root, rules=rules) != after:
            raise OpenClawStateError('the OpenClaw home changed during the copy')
    except FileNotFoundError as error:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(temporary, ignore_errors=True)
        raise OpenClawStateError('an entry of the OpenClaw home vanished during the copy') from error
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    found = [name for name in ('session_owned', 'run_owned', 'shared_changed') if classes[name]]
    receipt = {
        'schema_version': STATE_SCHEMA, 'scope': STATE_SCOPE, 'source_root': str(root), 'root_identity': after['root_identity'],
        'attempt_id': attempt_id, 'turn': turn, 'session_id': session_id, 'session_id_source': session_source,
        'session_key': session_key, 'key_id': key_id, 'acp_session_id': acp_session_id,
        'thread_id': thread_id, 'thread_id_source': thread_source, 'new_rollout_threads': threads,
        'build': build, 'started_ns': started_ns,
        'metadata_only_before_after': True, 'preexisting_contents_opened': False, 'shared_store_rows_read': 'this session only',
        'shared_store_exception': ('the session store files were copied as bytes to a private temporary directory; only the copies were '
                                   'opened; only rows of this session were exported; tables whose names suggest secrets were not read; '
                                   'the copies were deleted'),
        'before_inventory_sha256': inventory_sha256(before), 'after_inventory_sha256': digests[-1],
        'quiescence': {'required_equal_reads': 2, 'reads': len(digests), 'interval_seconds': interval_seconds, 'stable': True,
                       'inventory_sha256': digests, 'unchanged_after_copy': True},
        'rules': rules, 'rules_sha256': _sha(_canonical(rules)),
        'classes': {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')},
        'session_directories': classes['session_directories'],
        'accounting': {'changed_entries': classes['changed_entries'], 'classes_found': found, 'unexplained': [],
                       'every_changed_entry_accounted': True, 'shared_entries_changed_content_not_read': shared,
                       'session_store_rows_read': [item['store'] for item in store['stores'] if item['present']],
                       'statement': _statement(classes, store)},
        'artifacts': artifacts, 'session_store': store}
    return receipt, after


def _strip_paths(shared):
    return [{key: value for key, value in row.items() if key != 'changed_paths'} for row in shared]


def verify_openclaw_state_capture(receipt, destination, *, before, after):
    """Offline check of a whole-home receipt against its two inventories, its copies and its row export."""
    if receipt.get('schema_version') != STATE_SCHEMA or receipt.get('scope') != STATE_SCOPE:
        raise OpenClawStateError('not a whole OpenClaw home receipt')
    quiet = receipt.get('quiescence', {})
    digests = quiet.get('inventory_sha256', [])
    if (inventory_sha256(before) != receipt.get('before_inventory_sha256') or inventory_sha256(after) != receipt.get('after_inventory_sha256')
            or before.get('root') != receipt.get('source_root') or quiet.get('stable') is not True
            or len(digests) < 2 or digests[-1] != digests[-2] or digests[-1] != receipt['after_inventory_sha256']
            or quiet.get('reads') != len(digests) or quiet.get('unchanged_after_copy') is not True):
        raise OpenClawStateError('receipt differs from the supplied inventory or its quiescence is incomplete')
    rules = receipt.get('rules')
    identity = {key: receipt.get(key) for key in ('session_id', 'thread_id', 'session_key', 'key_id', 'acp_session_id')}
    if not isinstance(rules, dict) or receipt.get('rules_sha256') != _sha(_canonical(rules)):
        raise OpenClawStateError('receipt does not bind its rule table')
    threads = new_rollout_threads(before, after, rules=rules)
    if receipt.get('new_rollout_threads') != threads or identity['thread_id'] not in threads:
        raise OpenClawStateError('the Codex thread id is not confirmed by the name of a new rollout file')
    classes = classify_openclaw_changes(before, after, session_id=identity['session_id'], thread_id=identity['thread_id'],
                                        key_id=identity['key_id'], acp_session_id=identity['acp_session_id'],
                                        started_ns=receipt['started_ns'], rules=rules)
    recorded = receipt.get('classes', {})
    owned = lambda path: any(identity[key] and carries_session_id(path, identity[key]) for key in ('session_id', 'thread_id', 'key_id', 'acp_session_id'))  # noqa: E731
    for row in recorded.get('shared_changed', []):
        folders, paths = set(row.get('changed_directories', [])), row.get('changed_paths')
        parents = {path.rsplit('/', 1)[0] for path in paths or []}
        if paths is not None and (any(owned(path) for path in paths) or not parents <= folders
                                  or any(folder not in parents and folder not in paths for folder in folders)):
            raise OpenClawStateError(f"changed paths of a digested subtree differ from its directory digests: {row['relative_path']}")
    shared = sorted(row['relative_path'] for row in classes['shared_changed'])
    accounting, store = receipt.get('accounting', {}), receipt.get('session_store', {})
    summaries = store.get('stores', [])
    if (any(recorded.get(name) != classes[name] for name in ('session_owned', 'run_owned', 'directories_changed'))
            or _strip_paths(recorded.get('shared_changed', [])) != classes['shared_changed']
            or receipt.get('session_directories') != classes['session_directories']
            or accounting.get('changed_entries') != classes['changed_entries'] or accounting.get('unexplained') != []
            or accounting.get('every_changed_entry_accounted') is not True
            or accounting.get('shared_entries_changed_content_not_read') != shared
            or accounting.get('session_store_rows_read') != [item.get('store') for item in summaries if item.get('present')]
            or accounting.get('statement') != _statement(classes, store)
            or receipt.get('metadata_only_before_after') is not True or receipt.get('preexisting_contents_opened') is not False
            or receipt.get('shared_store_rows_read') != 'this session only'):
        raise OpenClawStateError('receipt classification differs from the inventories')
    entries = _entries(after)
    expected = {name: 'session_owned' for name in classes['session_owned']} | {name: 'run_owned' for name in classes['run_owned']}
    artifacts = receipt.get('artifacts', [])
    if len(artifacts) != len(expected) or {row['relative_path'] for row in artifacts} != set(expected):
        raise OpenClawStateError('copied file set differs from the classification')
    destination = Path(destination)
    present = {path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file() or path.is_symlink()}
    if present != {row['copied_path'] for row in artifacts} | {ROWS_FILE, STORE_SCHEMA_FILE}:
        raise OpenClawStateError('copied file set on disk differs from the receipt')
    for row in artifacts:
        entry, kind = entries[row['relative_path']], expected[row['relative_path']]
        target = destination / row['copied_path']
        if (row.get('class') != kind or row.get('private_only') is not (kind == 'run_owned')
                or row['copied_path'] != f"{SESSION_COPY if kind == 'session_owned' else RUN_COPY}/{row['relative_path']}"
                or (kind == 'session_owned' and row.get('owner_id') != classes['owners'][row['relative_path']])
                or row['filesystem_id'] != f"{entry['device']}:{entry['inode']}" or row['size_bytes'] != entry['size_bytes']
                or target.is_symlink() or target.stat().st_size != entry['size_bytes']
                or _sha(target.read_bytes()) != row['sha256']):
            raise OpenClawStateError(f"copied file differs from its receipt: {row['relative_path']}")
    # The row export: bound by digest, only this session, no denied table, counts equal to the receipt, store copies gone.
    for name, key in ((ROWS_FILE, 'rows_export'), (STORE_SCHEMA_FILE, 'schema_export')):
        target, bound = destination / name, store.get(key, {})
        raw = b'' if target.is_symlink() else target.read_bytes()
        if bound.get('path') != name or bound.get('size_bytes') != len(raw) or bound.get('sha256') != _sha(raw):
            raise OpenClawStateError(f'{name} differs from its receipt')
    rows = json.loads((destination / ROWS_FILE).read_bytes())
    if rows.get('schema_version') != ROWS_SCHEMA or any(rows.get(key) != identity[key] for key in ('session_id', 'thread_id', 'session_key')):
        raise OpenClawStateError('row export is not bound to the session')
    check_rows_name_only_test_session(rows, identity, rules=rules)
    read = [item for item in summaries if item.get('present')]
    if ([item.get('store') for item in summaries] != list(rules['session_stores']) or [item['store'] for item in rows['stores']] != [item['store'] for item in read]
            or any(not item.get('present') and item['store'] not in rules['optional_session_stores'] for item in summaries)):
        raise OpenClawStateError('row export does not cover the session stores of the rule table')
    for summary, exported in zip(read, rows['stores'], strict=True):
        counts = {table['table']: len(table['rows']) for table in exported['tables']}
        required = summary['store'] not in rules['optional_session_stores']
        if (summary.get('schema_sha256') != exported.get('schema_sha256') or summary.get('required') is not required
                or {row['table']: row['selected_rows'] for row in summary['tables'] if row['selected_rows']} != counts
                or summary.get('selected_rows') != sum(counts.values()) or (required and not counts)
                or any(row['selected_rows'] for row in summary['tables'] if row.get('denied'))
                or (counts and not any(reason.startswith('exact:') for table in exported['tables'] for row in table['rows'] for reason in row['selected_by']))):
            raise OpenClawStateError(f"row counts of {summary.get('store')} differ from the receipt")
        for copied in summary.get('copied_files', []):
            entry = entries.get(copied['relative_path'], {})
            if copied['filesystem_id'] != f"{entry.get('device')}:{entry.get('inode')}" or copied['size_bytes'] != entry.get('size_bytes'):
                raise OpenClawStateError(f"copied store file differs from the inventory: {copied['relative_path']}")
        if summary['store'] not in {copied['relative_path'] for copied in summary.get('copied_files', [])}:
            raise OpenClawStateError('the store file itself was not copied')
    temporary = store.get('temporary_copy', {})
    if (store.get('rows_of_other_sessions_exported') != 0 or store.get('rows_of_denied_tables_exported') != 0
            or temporary.get('deleted') is not True or temporary.get('directory') != f'.{destination.name}-store-copy'
            or (destination.parent / temporary['directory']).exists()):
        raise OpenClawStateError('the temporary copy of the session stores is not proved deleted')
    return True


def refusal_listing(error, *, attempt_id, turn, session_id, thread_id=None, rules=None):
    """What the operator needs after a refusal: the reason, each unexplained path, and the table to extend."""
    return {'schema_version': REFUSAL_SCHEMA, 'attempt_id': attempt_id, 'turn': turn, 'session_id': session_id, 'thread_id': thread_id,
            'reason': str(error), 'unexplained': list(getattr(error, 'unexplained', [])), 'rules': rules_table(rules),
            'how_to_extend': 'Add a pattern to RULES in session_bench/openclaw_state_capture.py: run_owned (new file of the run, copied, private), '
                             'shared_transient (lock, marker or temporary file, metadata only), transient_directories (temporary directory that may '
                             'disappear), shared_in_session_areas (shared index inside a session area), or digest_subtrees (large subtree, metadata '
                             'only). Then run a new capture; a refused capture is not reused.',
            'metadata_only': True}


# --- The fixture in the owner's workspace ---

def place_fixture(workspace, files, record):
    """Create ``<workspace>/fixture_project`` from ``files``. Refuses when the entry exists.

    ``record`` (a dict) receives the path and the directory identity as soon as
    the directory exists, so that a failure after that point still removes it.
    """
    target = Path(workspace) / FIXTURE_NAME
    if os.path.lexists(target):
        raise OpenClawCaptureError(f'{FIXTURE_NAME} already exists in the agent workspace; nothing was placed and no model call was made')
    os.mkdir(target, 0o755)   # fails when another process created the entry in between
    info = os.lstat(target)
    record.update(path=str(target), device=info.st_dev, inode=info.st_ino, placed=True, files=[])
    for relative, raw in sorted(files.items()):
        if relative.startswith('/') or '..' in Path(relative).parts:
            raise OpenClawCaptureError('non-canonical fixture path')
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(raw)
        record['files'].append({'relative_path': relative, 'size_bytes': len(raw), 'sha256': _sha(raw)})
    return record


def copy_fixture_out(record, destination):
    """Copy the placed fixture into the run directory. Links and special entries are named, not copied."""
    target, destination = Path(record['path']), Path(destination)
    copied, skipped = [], []
    if not os.path.lexists(target):
        return {'copied': copied, 'skipped': skipped, 'present': False}
    destination.mkdir(parents=True, exist_ok=False)
    for folder, names, files in os.walk(target, followlinks=False):
        for name in sorted(names + files):
            path = Path(folder) / name
            relative = path.relative_to(target).as_posix()
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                (destination / relative).mkdir(parents=True, exist_ok=True)
            elif stat.S_ISREG(info.st_mode) and info.st_size <= 16 * 1024 * 1024 and len(copied) < 1000:
                raw = path.read_bytes()
                (destination / relative).parent.mkdir(parents=True, exist_ok=True)
                with (destination / relative).open('xb') as handle:
                    handle.write(raw)
                copied.append({'relative_path': relative, 'size_bytes': len(raw), 'sha256': _sha(raw)})
            else:
                skipped.append({'relative_path': relative, 'reason': 'link, special entry, or over the size or count limit'})
    return {'copied': copied, 'skipped': skipped, 'present': True}


def remove_fixture(record):
    """Remove the placed fixture directory and prove that it is gone. Never follows a link."""
    target = Path(record['path'])
    if not os.path.lexists(target):
        return {'removed': True, 'was_present': False, 'path': str(target)}
    info = os.lstat(target)
    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (record['device'], record['inode']):
        return {'removed': False, 'was_present': True, 'path': str(target),
                'reason': 'the entry is not the directory that the controller placed; it was not touched'}
    try:
        shutil.rmtree(target)
    except OSError as error:
        return {'removed': False, 'was_present': True, 'path': str(target), 'reason': f'removal failed: {type(error).__name__}'}
    gone = not os.path.lexists(target)
    return {'removed': gone, 'was_present': True, 'path': str(target), **({} if gone else {'reason': 'the entry still exists after removal'})}


def _workspace_listing(workspace):
    """Metadata of every entry of the workspace, by path. Nothing is opened. The names stay in memory."""
    snapshot = inventory_hermes_home(workspace, rules={'digest_subtrees': ()})
    return {entry['relative_path']: entry for entry in snapshot['entries']}


def workspace_side_effects(before, after):
    """Entries of the workspace outside the fixture that differ between two listings. Metadata only."""
    rows = []
    for path in sorted(before.keys() | after.keys()):
        was, now = before.get(path), after.get(path)
        if was == now or path == FIXTURE_NAME or path.startswith(FIXTURE_NAME + '/'):
            continue
        entry = now or was
        rows.append({'relative_path': path, 'change': 'new' if was is None else 'removed' if now is None else 'changed',
                     'kind': entry['kind'], 'size_bytes': entry['size_bytes'], 'content_read': False})
    return rows


# --- The controller ---

def turn_argv(executable, session_id, message_file):
    """The argv of one turn, exactly as probed on 2026-10-07. Both turns use the same session id."""
    return [str(executable), 'agent', '--local', '--session-id', session_id, '--json', '--timeout', str(TURN_TIMEOUT_SECONDS),
            '--message-file', str(message_file)]


def preflight_argv(executable):
    """The two read-only commands of the preflight. Neither starts an agent or a model call."""
    return {'version': [str(executable), '--version'], 'workspace': [str(executable), 'config', 'get', WORKSPACE_KEY]}


def planned_argv(*, executable, destination, session_id='<new uuid, the same in both turns>'):
    """The full argv of both turns, for an offline check. Nothing is started."""
    destination = Path(destination)
    return [turn_argv(executable, session_id, destination / f'observer/prompt-r{turn}.txt') for turn in (1, 2)]


def _environment(run_canary):
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'TMPDIR') if os.environ.get(key)}
    env['SB_SURVIVAL_V1_RUN_CANARY'] = run_canary
    return env


def _routing_overrides():
    return sorted(key for key in os.environ if key.startswith(('OPENCLAW_', 'CLAWDBOT_')))


def prepare_openclaw_capture(destination: Path | str, *, repository: Path | str, repetition: int, executable: Path | str,
                             openclaw_home: Path | str | None = None, expected_version: str = VERSION,
                             route: str = 'local', gateway_port: int = GATEWAY_PORT) -> dict:
    """Create an offline plan; no OpenClaw command or model is started and nothing is placed in the workspace.

    ``openclaw_home`` is the normal OpenClaw home (``~/.openclaw``). Tests pass
    a synthetic tree. It is only named here; nothing in it is read.
    """
    destination = Path(destination).absolute()
    repository = Path(repository).resolve()
    executable = Path(executable).absolute()
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ValueError('repetition must be 1, 2, or 3')
    if route not in ROUTES:
        raise ValueError('route must be local or acp')
    if destination.exists() or destination.is_symlink():
        raise ValueError('capture destination must be new')
    if not re.fullmatch(r'openclaw-[A-Za-z0-9_-]+', destination.name):
        raise ValueError('attempt ID must start with openclaw- and use bounded ASCII characters')
    if destination.parent != repository / 'artifacts/v1-expanded-preparation/live-captures':
        raise ValueError('capture destination must be in repository live-captures')
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise OpenClawCaptureError('the OpenClaw executable is unavailable')
    if _routing_overrides():
        raise OpenClawCaptureError('an OPENCLAW_ or CLAWDBOT_ variable is set; refusing to alter OpenClaw storage routing')
    openclaw_home = Path(openclaw_home).absolute() if openclaw_home is not None else Path.home() / '.openclaw'
    if openclaw_home.is_symlink() or not openclaw_home.is_dir():
        raise OpenClawCaptureError('normal OpenClaw home is not an ordinary directory')

    source = repository / 'fixtures/scenarios/survival-v1/workload'
    files = _tree(source / FIXTURE_NAME)
    if set(files) != _FIXTURE_FILES:
        raise OpenClawCaptureError('frozen survival fixture tree has unexpected or missing files')
    template_raw = (source / 'workload.json').read_bytes()
    workload, _ = instantiate_workload(_json(template_raw, 'checked-in survival workload'), destination.name)
    session_id = str(uuid.uuid4())

    destination.mkdir(parents=True)
    for name in ('observer', 'turn-r1', 'turn-r2'):
        (destination / name).mkdir()
    _write_new(destination / 'workload-template.json', template_raw)
    _write_json(destination / 'workload-instance.json', workload)
    _copy_tree(files, destination / f'workspaces/before/{FIXTURE_NAME}')
    for turn, row in enumerate(workload['turns'], 1):
        _write_new(destination / f'observer/prompt-r{turn}.txt', row['text'].encode())
    env = _environment(workload['run_canary'])
    plan = {
        'schema_version': SCHEMA, 'attempt_id': destination.name, 'configuration_id': 'openclaw', 'repetition': repetition,
        'executable': str(executable), 'expected_version': expected_version, 'harness': HARNESS,
        'session_id': session_id, 'openclaw_home': str(openclaw_home),
        'argv': planned_argv(executable=executable, destination=destination, session_id=session_id),
        'preflight_argv': preflight_argv(executable),
        'launch_mode': 'openclaw agent --local --session-id ID --json --timeout 300 --message-file FILE; cwd is the agent workspace',
        'model': 'not set by the controller; the owner default is used and the model that the stdout envelope reports is recorded',
        'state': "the owner's normal OpenClaw state; no state directory, profile or workspace override",
        'workspace_source': f'openclaw config get {WORKSPACE_KEY} (read-only CLI call at execution; the configuration file is not read)',
        'fixture_placement': f'<workspace>/{FIXTURE_NAME}: placed before turn 1 when no such entry exists; always removed at the end',
        'environment_keys': sorted(env), 'environment_overrides': {'run_canary': workload['run_canary']},
        'model_submissions': 0, 'status': 'prepared', 'score_eligible': False,
        'native_capture': ('whole OpenClaw home metadata bracket per turn; files named by the session id or the Codex thread id copied; '
                           'rows of the test session exported from private temporary copies of ' + ', '.join(RULES['session_stores'])),
        'observer': 'stdout envelope of each turn, helper ledger, file hashes; the envelope holds no single tool call',
        'no_model_override': True, 'no_retry': True, 'no_fallback': True, 'no_unrelated_session_reads': True,
        'protected_sha256': {name: _sha(raw) for name, raw in files.items()}, 'workload_sha256': _sha(template_raw),
    }
    if route == 'acp':
        # One gateway in the foreground and one ACP client for both turns. The session id is not known before the
        # run: the controller makes the session key, and the agent store gives the session id of that key.
        key = f'agent:main:explicit:{session_id}'
        plan.update(
            session_id=None, session_key=key, key_id=session_id, gateway_port=gateway_port,
            argv=None, gateway_argv=gateway_argv(executable), acp_argv=acp_argv(executable, key),
            gateway_environment=dict(GATEWAY_ENVIRONMENT),
            launch_mode=('openclaw gateway run (foreground, started and stopped by the controller); then one openclaw acp process: '
                         'initialize, session/new, and one session/prompt per turn'),
            observer='ACP stream of each turn (tool calls with arguments, status, exit code), helper ledger, file hashes',
            native_capture=plan['native_capture'].replace('per turn', 'once, after both turns and after the gateway stopped'))
    plan['route'] = route
    _write_json(destination / 'plan.json', plan)
    return plan


def gateway_argv(executable):
    """The gateway in the foreground. Never ``gateway install``, ``start`` or ``restart``."""
    return [str(executable), 'gateway', 'run']


def acp_argv(executable, session_key):
    """The ACP bridge to the running gateway for one session key."""
    return [str(executable), 'acp', '--session', session_key, '--no-prefix-cwd']


_PROCESS = re.compile(r'openclaw(\.m?js|-gateway|-agent|-node|-daemon)?')


def openclaw_processes(listing: str, own_pids=()) -> list[dict]:
    """OpenClaw processes in the output of ``ps -axo pid=,ppid=,command=``. Command lines are not kept.

    A process counts when the file name of one word of its command is
    ``openclaw``, ``openclaw.mjs``, ``openclaw.js`` or ``openclaw-gateway``
    (also ``-agent``, ``-node``, ``-daemon``): the CLI, the gateway, the
    script under node. The controller's own process and its ancestors are
    left out.
    """
    rows, parents = [], {}
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            parents[int(parts[0])] = int(parts[1])
            rows.append((int(parts[0]), parts[2]))
    own = set()
    for pid in own_pids:
        while pid in parents and pid not in own:
            own.add(pid)
            pid = parents[pid]
    found = []
    for pid, command in rows:
        words = command.split()
        if pid not in own and any(_PROCESS.fullmatch(word.rsplit('/', 1)[-1]) for word in words):
            kind = 'gateway' if any('gateway' in word for word in words) else 'agent' if 'agent' in words else 'other'
            found.append({'pid': pid, 'kind': kind})
    return found


def _preflight(plan: Mapping, env: Mapping[str, str], timeout: float = 60.0) -> dict:
    """Version, workspace path and running OpenClaw processes. No agent run and no model call."""
    argv = plan['preflight_argv']
    cwd = str(Path(plan['executable']).parent)
    version = subprocess.run(argv['version'], cwd=cwd, env=dict(env), capture_output=True, timeout=timeout)
    version_text = version.stdout.decode('utf-8', errors='replace').strip()
    receipt = {'ready': False, 'argv': argv, 'version': version_text, 'expected_version': plan['expected_version'],
               'model_submission': False, 'workspace': None, 'processes': None, 'failure': None}
    if version.returncode != 0 or version_text != plan['expected_version']:
        return {**receipt, 'failure': 'the OpenClaw executable did not report the expected version'}
    config = subprocess.run(argv['workspace'], cwd=cwd, env=dict(env), capture_output=True, timeout=timeout)
    lines = [line.strip() for line in config.stdout.decode('utf-8', errors='replace').splitlines() if line.strip()]
    value = lines[0] if len(lines) == 1 else ''
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if value.startswith('~/'):
        value = str(Path(env.get('HOME', '')) / value[2:])
    if config.returncode != 0 or not value.startswith('/'):
        return {**receipt, 'failure': f'`config get {WORKSPACE_KEY}` did not print exactly one absolute path'}
    listing = subprocess.run(['ps', '-axo', 'pid=,ppid=,command='], capture_output=True, timeout=timeout)
    processes = openclaw_processes(listing.stdout.decode('utf-8', errors='replace'), own_pids=(os.getpid(),)) if listing.returncode == 0 else None
    receipt.update(workspace=value, processes=processes,
                   process_check='ps -axo pid=,ppid=,command=; only pid and kind of a matching process are kept')
    if processes is None:
        return {**receipt, 'failure': 'the process list could not be read'}
    if processes:
        return {**receipt, 'failure': f'{len(processes)} other OpenClaw process(es) are running (gateway or agent); stop them first'}
    return {**receipt, 'ready': True}


def _run(argv, *, cwd, env, stdout, stderr, timeout) -> int:
    """Run one command to its end. On a timeout or an interrupt the whole process group is killed first."""
    with stdout.open('xb') as out, stderr.open('xb') as err:
        process = subprocess.Popen(argv, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise OpenClawCaptureError('turn timed out; no retry or fallback') from error
            raise


def _save(destination, state):
    (destination / 'controller-state.json.tmp').write_text(json.dumps(state, indent=2) + '\n')
    (destination / 'controller-state.json.tmp').replace(destination / 'controller-state.json')


def _native_state(destination, turn, plan, before, detail, summary, started_ns, build, settle):
    """Bracket the whole OpenClaw home for one turn; on a refusal leave a listing and stop."""
    home, session_id = Path(plan['openclaw_home']), plan['session_id']
    try:
        receipt, after = capture_openclaw_state(
            home, destination / f'r{turn}-native', before, attempt_id=destination.name, turn=turn, session_id=session_id,
            thread_id=summary['thread_id'], session_key=summary['session_key'], key_id=summary.get('key_id'),
            acp_session_id=summary.get('acp_session_id'),
            started_ns=started_ns, build=build, before_detail=detail, **settle)
    except HermesStateError as error:
        _write_json(destination / f'r{turn}-refusal.json',
                    refusal_listing(error, attempt_id=destination.name, turn=turn, session_id=session_id, thread_id=summary['thread_id']))
        try:
            # The metadata listing of the refused state helps the diagnosis; it stays private.
            _write_json(destination / f'r{turn}-state-refused.json', inventory_openclaw_home(home))
        except (OSError, ValueError):
            pass
        raise OpenClawCaptureError(f'R{turn} native state capture refused (see r{turn}-refusal.json): {error}') from error
    _write_json(destination / f'r{turn}-state-after.json', after)
    _write_json(destination / f'r{turn}-native-receipt.json', receipt)
    return receipt


# --- Route ``acp``: a gateway in the foreground and one ACP client ---

_AGENT_MODEL = re.compile(r'\[gateway\] agent model: ([A-Za-z0-9._-]+)/([A-Za-z0-9._:-]+)')
_TOKEN_LIKE = re.compile(r'[A-Za-z0-9_\-+/=.]{24,}')


def redact(raw: bytes) -> bytes:
    """Text for a kept log: every run of 24 or more token characters is cut out, and the home directory is named ``~``.

    A gateway may print its access token or a URL with it. Ids and long paths are cut too.
    """
    text = raw.decode('utf-8', errors='replace').replace(str(Path.home()), '~')
    return _TOKEN_LIKE.sub('<cut>', text).encode('utf-8')


def port_open(port: int, host: str = '127.0.0.1') -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def start_gateway(plan: Mapping, env: Mapping[str, str], workspace: Path, record: dict, *, wait_seconds: float = 90.0) -> dict:
    """Start ``openclaw gateway run`` and wait for its port. Refuses when the port is already open.

    ``record`` receives the process as soon as it exists, so that every later
    path stops it. The gateway output goes to temporary files outside the run directory.
    """
    port, argv = plan['gateway_port'], plan['gateway_argv']
    missing = [name for name in ('OPENCLAW_SKIP_CHANNELS', 'OPENCLAW_SKIP_CRON') if plan.get('gateway_environment', {}).get(name) != '1']
    if missing or argv[1:] != ['gateway', 'run']:
        raise OpenClawCaptureError('the plan does not start a foreground gateway with channels and timed jobs switched off; nothing was started')
    if port_open(port):
        raise OpenClawCaptureError(f'port {port} is already open: another gateway is running; nothing was started and no model call was made')
    gateway_env = {**env, **plan['gateway_environment']}
    out = tempfile.NamedTemporaryFile(prefix='session-bench-openclaw-gateway-', suffix='.out', delete=False)
    err = tempfile.NamedTemporaryFile(prefix='session-bench-openclaw-gateway-', suffix='.err', delete=False)
    record.update(port=port, argv=argv, environment_keys=sorted(gateway_env), stdout=out.name, stderr=err.name, stopped=False)
    process = subprocess.Popen(argv, cwd=workspace, env=gateway_env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
    out.close()
    err.close()
    record.update(process=process, pid=process.pid, started_ns=time.time_ns())
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise OpenClawCaptureError(f'the gateway exited with code {process.returncode} before its port opened; no model call was made')
        if port_open(port):
            record['port_open_ns'] = time.time_ns()
            return record
        time.sleep(0.25)
    raise OpenClawCaptureError(f'the gateway did not open port {port} in {wait_seconds:.0f} s; no model call was made')


def stop_gateway(record: dict, destination: Path, *, wait_seconds: float = 20.0) -> dict:
    """Stop the gateway that the controller started (its process group), then check that the port is closed."""
    process = record.get('process')
    result = {'pid': record.get('pid'), 'port': record.get('port'), 'signal': None, 'returncode': None, 'port_closed': None}
    if process is not None:
        for name, sign in (('SIGTERM', signal.SIGTERM), ('SIGKILL', signal.SIGKILL)):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, sign)
                    result['signal'] = name
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=wait_seconds)
                break
            except subprocess.TimeoutExpired:
                continue
        try:   # the group may hold children that outlived the gateway process
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        result['returncode'] = process.poll()
    deadline = time.monotonic() + wait_seconds
    while port_open(record['port']) and time.monotonic() < deadline:
        time.sleep(0.25)
    result['port_closed'] = not port_open(record['port'])
    result['stopped'] = result['port_closed'] and (process is None or process.poll() is not None)
    logs = {}
    for name in ('stdout', 'stderr'):
        path = Path(record[name]) if record.get(name) else None
        if path is not None and path.is_file():
            raw = path.read_bytes()
            logs[name] = {'size_bytes': len(raw), 'sha256': _sha(raw), 'kept': f'gateway-{name}.redacted.txt'}
            models = sorted(set(_AGENT_MODEL.findall(raw.decode('utf-8', errors='replace')))) if name == 'stdout' else []
            if len(models) == 1:   # the one model that this gateway process announced at its start
                result['agent_model'] = {'provider': models[0][0], 'model': models[0][1], 'source': 'gateway start line `agent model:`'}
            target = destination / f'gateway-{name}.redacted.txt'
            if not target.exists():
                _write_new(target, redact(raw[-256 * 1024:]))
            path.unlink()
    result['output'] = logs
    record.update(stopped=result['stopped'], stop=result)
    return result


class AcpClient:
    """A minimal ACP client on the stdio of one ``openclaw acp`` process. Every line is written to the stream file."""

    def __init__(self, process, stream_path: Path):
        self.process, self.next_id, self.permissions = process, 0, 0
        self.lines: queue.Queue = queue.Queue()
        self.stream = None
        self.use(stream_path)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.lines.put((time.time_ns(), line))
        self.lines.put((time.time_ns(), None))

    def use(self, stream_path: Path):
        if self.stream is not None:
            self.stream.close()
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = stream_path.open('xb')

    def _log(self, direction, raw: bytes, t_ns):
        self.stream.write(stream_line(direction, raw.decode('utf-8', errors='replace').rstrip('\r\n'), t_ns))
        self.stream.flush()

    def _send(self, message):
        raw = (json.dumps(message, ensure_ascii=False) + '\n').encode('utf-8')
        self._log('send', raw, time.time_ns())
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def request(self, method, params, timeout):
        """Send one request and return its response. Permission requests in between are answered with the allow option."""
        self.next_id += 1
        identity = self.next_id
        self._send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                t_ns, line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise OpenClawCaptureError(f'ACP {method} timed out; no retry or fallback') from error
            if line is None:
                raise OpenClawCaptureError(f'the ACP process closed its output before the response of {method}')
            self._log('recv', line, t_ns)
            try:
                message = json.loads(line)
            except ValueError as error:
                raise OpenClawCaptureError('the ACP process wrote a line that is not JSON') from error
            if not isinstance(message, dict):
                raise OpenClawCaptureError('the ACP process wrote a line that is not a JSON-RPC message')
            if 'method' not in message and message.get('id') == identity:
                if 'error' in message or not isinstance(message.get('result'), dict):
                    code = message.get('error', {}).get('code') if isinstance(message.get('error'), dict) else None
                    raise OpenClawCaptureError(f'ACP {method} failed (error code {code}); no retry or fallback')
                return message['result']
            if message.get('method') == 'session/request_permission' and 'id' in message:
                options = (message.get('params') or {}).get('options') or []
                allow = [option for option in options if isinstance(option, dict) and str(option.get('kind', '')).startswith('allow')]
                once = [option for option in allow if option.get('kind') == 'allow_once']
                if not allow:
                    raise OpenClawCaptureError('an ACP permission request offers no allow option')
                self.permissions += 1
                self._send({'jsonrpc': '2.0', 'id': message['id'],
                            'result': {'outcome': {'outcome': 'selected', 'optionId': (once or allow)[0]['optionId']}}})
            elif 'method' in message and 'id' in message:
                raise OpenClawCaptureError(f"the ACP process sent a request that the controller does not serve: {message.get('method')}")

    def close(self, wait_seconds: float = 10.0):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait()
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        return self.process.returncode


# --- Execution ---

def _protected(context, turn):
    current = _tree(context['fixture_path'])
    for relative, digest in context['plan']['protected_sha256'].items():
        if relative != 'checkout.py' and _sha(current.get(relative, b'')) != digest:
            raise OpenClawCaptureError(f'protected fixture changed: {relative}')
    if turn == 1 and _sha(current.get('checkout.py', b'')) != context['plan']['protected_sha256']['checkout.py']:
        raise OpenClawCaptureError('checkout changed before R1 was submitted')


def _snapshot(context, turn):
    """Copy the fixture as it is after a turn. Returns the files."""
    current = _tree(context['fixture_path'])
    _copy_tree(current, context['destination'] / f'turn-r{turn}/workspace/{FIXTURE_NAME}')
    return current


def _fixture_boundary(context, turn, current):
    if turn == 1 and _sha(current.get('checkout.py', b'')) != context['plan']['protected_sha256']['checkout.py']:
        raise OpenClawCaptureError('R1 changed checkout before R2 was authorized')
    if set(current) - (_FIXTURE_FILES | {'.survival-observer.jsonl'}):
        raise OpenClawCaptureError('workspace wrote files outside the fixture boundary')
    phases = ('inspect', 'baseline') if turn == 1 else ('inspect', 'baseline', 'final')
    _validate_ledger(context['destination'] / f'turn-r{turn}/workspace/{FIXTURE_NAME}/.survival-observer.jsonl', context['workload'], phases, f'R{turn}')


def _local_route(context):
    """Two ``openclaw agent --local`` processes with one session id; the home is bracketed after each."""
    destination, plan, workload, env, state = (context[key] for key in ('destination', 'plan', 'workload', 'env', 'state'))
    workspace, session_id = context['workspace'], plan['session_id']
    for turn in (1, 2):
        _protected(context, turn)
        canary = workload['turns'][turn - 1]['response_canary']
        prefix = destination / f'turn-r{turn}'
        argv = plan['argv'][turn - 1]
        prompt_raw = (destination / f'observer/prompt-r{turn}.txt').read_bytes()
        if prompt_raw != workload['turns'][turn - 1]['text'].encode() or argv != turn_argv(plan['executable'], session_id, destination / f'observer/prompt-r{turn}.txt'):
            raise OpenClawCaptureError('the prompt file or the planned argv changed after preparation')
        _write_json(prefix / 'launch.json', {'argv': argv, 'cwd': str(workspace), 'environment_keys': sorted(env),
                                             'prompt_sha256': _sha(prompt_raw), 'session_id': session_id, 'model_override': None,
                                             'state_override': None, 'stdout_format': 'one JSON envelope'})
        state['model_submissions'] += 1
        state['turns'].append({'turn': turn, 'status': 'running'})
        _save(destination, state)
        stdout, stderr = prefix / STDOUT_NAME, prefix / 'stderr.txt'
        code = context['runner'](argv, cwd=workspace, env=env, stdout=stdout, stderr=stderr, timeout=context['timeout'])
        out_raw, err_raw = stdout.read_bytes(), stderr.read_bytes()
        _write_json(prefix / 'exit.json', {'returncode': code, 'ended_ns': time.time_ns(),
                                           'stdout_sha256': _sha(out_raw), 'stderr_sha256': _sha(err_raw)})
        # Snapshot the fixture before interpreting failures or reading the native state.
        current = _snapshot(context, turn)
        if code != 0 or _STOP.search(err_raw.decode('utf-8', errors='replace')):
            raise OpenClawCaptureError(f'R{turn} stopped on command/auth/quota failure; no retry or fallback')
        if len(out_raw) > 16 * 1024 * 1024:
            raise OpenClawCaptureError(f'R{turn} stdout exceeded byte limit')
        try:
            summary = envelope_summary(out_raw, f'R{turn} stdout')
        except OpenClawEnvelopeError as error:
            raise OpenClawCaptureError(f'R{turn} stdout is not the envelope of a completed turn: {error}; no retry or fallback') from error
        if summary['session_id'] != session_id:
            raise OpenClawCaptureError(f'R{turn} ran in another session than the one requested')
        if summary['harness'] != HARNESS:
            raise OpenClawCaptureError(f'R{turn} did not run through the {HARNESS} harness plugin')
        if summary['workspace_dir'] is None or Path(summary['workspace_dir']).resolve() != context['resolved']:
            raise OpenClawCaptureError(f'R{turn} reports another workspace than the one of the preflight')
        if turn == 1:
            state.update(provider=summary['provider'], model=summary['model'], thread_id=summary['thread_id'])
        elif (summary['provider'], summary['model']) != (state['provider'], state['model']):
            raise OpenClawCaptureError('R2 reports another provider or model than R1')
        elif summary['thread_id'] != state['thread_id']:
            raise OpenClawCaptureError('R2 reports another Codex thread than R1')
        if not summary['text'].rstrip().endswith(canary):
            raise OpenClawCaptureError(f'R{turn} visible response lacks the exact canary')
        _write_json(prefix / 'envelope-receipt.json', {key: value for key, value in summary.items() if key != 'text'}
                    | {'stdout_sha256': _sha(out_raw), 'tool_events_in_envelope': False})
        native_receipt = _native_state(destination, turn, plan, context['before'], context['before_detail'], summary, context['started_ns'],
                                       context['build'], context['settle'])
        if state['thread_id'] not in (None, native_receipt['thread_id']):
            raise OpenClawCaptureError(f'R{turn} native state names another Codex thread')
        state['thread_id'] = native_receipt['thread_id']
        _fixture_boundary(context, turn, current)
        state['turns'][-1].update({'status': 'completed', 'returncode': code, 'stdout_sha256': _sha(out_raw), 'stderr_sha256': _sha(err_raw),
                                   'envelope_receipt': f'turn-r{turn}/envelope-receipt.json', 'native_receipt': f'r{turn}-native-receipt.json',
                                   'tool_summary': summary['tool_summary'],
                                   'native_session_store_rows': native_receipt['session_store']['selected_rows'],
                                   'native_session_owned_files': len(native_receipt['classes']['session_owned'])})
        _save(destination, state)
    state.update(native_scope=('whole OpenClaw home bracket per turn (openclaw-state-root-v1): files named by the session id or the '
                               'Codex thread id, and the rows of the test session in the session stores'),
                 observer_scope='stdout envelope, helper ledger, file hashes; no tool events')


def _acp_route(context):
    """One foreground gateway and one ACP process for both turns; the home is bracketed once, after the gateway stopped."""
    destination, plan, workload, env, state = (context[key] for key in ('destination', 'plan', 'workload', 'env', 'state'))
    workspace, gateway, key = context['workspace'], context['gateway'], plan['session_key']
    if plan['acp_argv'] != acp_argv(plan['executable'], key) or plan['gateway_argv'] != gateway_argv(plan['executable']):
        raise OpenClawCaptureError('the planned argv changed after preparation')
    start_gateway(plan, env, workspace, gateway)
    state.update(gateway_started=True, gateway_pid=gateway['pid'])
    _write_json(destination / 'gateway-launch.json', {'argv': gateway['argv'], 'cwd': str(workspace), 'environment_keys': gateway['environment_keys'],
                                                      'environment_overrides': plan['gateway_environment'], 'pid': gateway['pid'],
                                                      'port': gateway['port'], 'started_ns': gateway['started_ns'],
                                                      'port_open_ns': gateway['port_open_ns'], 'foreground': True, 'service_installed': False})
    _save(destination, state)
    acp_err = tempfile.NamedTemporaryFile(prefix='session-bench-openclaw-acp-', suffix='.err', delete=False)
    process = subprocess.Popen(plan['acp_argv'], cwd=workspace, env=dict(env), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=acp_err,
                               start_new_session=True)
    acp_err.close()
    context['acp'] = client = AcpClient(process, destination / 'turn-r1/acp-stream.jsonl')
    context['acp_stderr'] = acp_err.name
    _write_json(destination / 'acp-launch.json', {'argv': plan['acp_argv'], 'cwd': str(workspace), 'environment_keys': sorted(env),
                                                  'session_key': key, 'pid': process.pid, 'model_override': None})
    initialized = client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {
        'fs': {'readTextFile': False, 'writeTextFile': False}, 'terminal': False}}, 60.0)
    created = client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}, 60.0)
    acp_session = created.get('sessionId')
    if initialized.get('protocolVersion') != 1 or not isinstance(acp_session, str) or not acp_session:
        raise OpenClawCaptureError('the ACP process did not accept protocol version 1 or gave no session; no model call was made')
    state['acp_session_id'] = acp_session
    for turn in (1, 2):
        _protected(context, turn)
        row = workload['turns'][turn - 1]
        prefix = destination / f'turn-r{turn}'
        prompt_raw = (destination / f'observer/prompt-r{turn}.txt').read_bytes()
        if prompt_raw != row['text'].encode():
            raise OpenClawCaptureError('the prompt file changed after preparation')
        if turn == 2:
            client.use(prefix / 'acp-stream.jsonl')
        _write_json(prefix / 'launch.json', {'method': 'session/prompt', 'acp_session_id': acp_session, 'session_key': key,
                                             'prompt_sha256': _sha(prompt_raw), 'same_acp_process_as_turn_1': True, 'model_override': None})
        state['model_submissions'] += 1
        state['turns'].append({'turn': turn, 'status': 'running'})
        _save(destination, state)
        permissions = client.permissions
        client.request('session/prompt', {'sessionId': acp_session, 'prompt': [{'type': 'text', 'text': row['text']}]}, context['timeout'])
        client.stream.flush()
        current = _snapshot(context, turn)
        stream_raw = (prefix / 'acp-stream.jsonl').read_bytes()
        try:
            summary = turn_summary(stream_raw, f'R{turn} ACP stream')
        except OpenClawAcpError as error:
            raise OpenClawCaptureError(f'R{turn} ACP stream is not a complete successful turn: {error}; no retry or fallback') from error
        if any(seen != key for seen in summary['session_keys']):
            raise OpenClawCaptureError(f'R{turn} ACP stream names another session key than the one requested')
        if not summary['text'].rstrip().endswith(row['response_canary']):
            raise OpenClawCaptureError(f'R{turn} visible response lacks the exact canary')
        receipt = {'acp_session_id': acp_session, 'session_key': key, 'stop_reason': summary['stop_reason'], 'messages': summary['messages'],
                   'stream_sha256': _sha(stream_raw), 'tool_calls': len(summary['calls']),
                   'tool_titles': [call['title'].split(':', 1)[0] for call in summary['calls']],
                   'tool_calls_with_arguments': sum(isinstance(call['raw_input'], dict) for call in summary['calls']),
                   'permission_requests_answered': client.permissions - permissions,
                   'session_keys_in_stream': summary['session_keys'], 'context_usage': summary['context_usage'],
                   'tool_events_in_stream': bool(summary['calls'])}
        _write_json(prefix / 'acp-receipt.json', receipt)
        _fixture_boundary(context, turn, current)
        state['turns'][-1].update({'status': 'completed', 'acp_receipt': f'turn-r{turn}/acp-receipt.json', 'stream_sha256': receipt['stream_sha256'],
                                   'tool_calls': receipt['tool_calls'], 'permission_requests_answered': receipt['permission_requests_answered']})
        _save(destination, state)
    _close_acp(context)
    stop = stop_gateway(gateway, destination)
    _write_json(destination / 'gateway-stop.json', stop)
    state['gateway_stopped'] = stop['stopped']
    if stop.get('agent_model'):
        state.update(provider=stop['agent_model']['provider'], model=stop['agent_model']['model'],
                     provider_and_model='the start line of the gateway process; the ACP stream names no model')
    if not stop['stopped']:
        raise OpenClawCaptureError('the gateway did not stop or its port is still open; the home was not bracketed')
    # The native state once, after both turns: the bracket covers the gateway start and stop.
    summary = {'thread_id': None, 'session_key': key, 'key_id': plan['key_id'], 'acp_session_id': acp_session}
    native_receipt = _native_state(destination, 2, plan, context['before'], context['before_detail'], summary, context['started_ns'],
                                   context['build'], context['settle'])
    state.update(session_id=native_receipt['session_id'], thread_id=native_receipt['thread_id'])
    state['turns'][-1].update({'native_receipt': 'r2-native-receipt.json',
                               'native_session_store_rows': native_receipt['session_store']['selected_rows'],
                               'native_session_owned_files': len(native_receipt['classes']['session_owned'])})
    state.update(native_scope=('whole OpenClaw home bracket (openclaw-state-root-v1), taken once after both turns and after the gateway '
                               'stopped: files named by the session id, the id in the session key or the Codex thread id, and the rows of '
                               'the test session in the session stores'),
                 observer_scope='ACP stream of each turn, helper ledger, file hashes')


def _close_acp(context):
    client = context.pop('acp', None)
    if client is None:
        return
    code = client.close()
    path = Path(context.pop('acp_stderr'))
    raw = path.read_bytes() if path.is_file() else b''
    path.unlink(missing_ok=True)
    target = context['destination'] / 'acp-stderr.redacted.txt'
    if not target.exists():
        _write_new(target, redact(raw[-256 * 1024:]))
    context['state']['acp_returncode'] = code


def execute_openclaw_capture(destination: Path | str, *, timeout: float = TURN_TIMEOUT_SECONDS + 120.0,
                             preflight: Callable = _preflight, runner: Callable = _run,
                             state_settle: Mapping | None = None) -> dict:
    """Place the fixture, submit two turns once each in one session, bracket the home, always remove the fixture.

    The plan names the route. ``local``: two ``openclaw agent --local``
    processes. ``acp``: a foreground gateway that the controller starts and
    always stops, and one ACP process for both turns.

    ``state_settle`` passes ``interval_seconds``, ``max_reads`` and ``sleep`` to
    the whole-home bracket. The default waits 0.5 s between metadata reads.
    The result says ``fixture_removed``; when it is false, the path of the
    left-over directory is in ``FIXTURE_NOT_REMOVED``. On the route ``acp`` it
    says ``gateway_stopped``; when that is false, ``GATEWAY_NOT_STOPPED`` holds the pid.
    """
    destination = Path(destination).absolute()
    plan = _json((destination / 'plan.json').read_bytes(), 'OpenClaw plan')
    workload = _json((destination / 'workload-instance.json').read_bytes(), 'OpenClaw workload')
    route = plan.get('route', 'local')
    if plan.get('schema_version') != SCHEMA or plan.get('status') != 'prepared' or plan.get('attempt_id') != destination.name or route not in ROUTES:
        raise OpenClawCaptureError('capture is not a fresh prepared plan')
    if (any((destination / f'turn-r{n}/{name}').exists() for n in (1, 2) for name in (STDOUT_NAME, 'acp-stream.jsonl'))
            or (destination / 'capture-result.json').exists()):
        raise OpenClawCaptureError('capture is single-use')
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= TURN_TIMEOUT_SECONDS or timeout > 900:
        raise ValueError('timeout must be above the turn timeout of the CLI and at most 900 seconds')
    env = _environment(workload['run_canary'])
    if _routing_overrides() or sorted(env) != plan['environment_keys']:
        raise OpenClawCaptureError('an OpenClaw routing variable is set, or the environment boundary changed')
    home = Path(plan['openclaw_home'])
    settle = {'interval_seconds': 0.5, 'max_reads': 8, **(state_settle or {})}
    state = {'schema_version': SCHEMA, 'attempt_id': destination.name, 'status': 'attempted', 'route': route, 'harness': HARNESS,
             'provider': None, 'model': None, 'model_submissions': 0, 'session_id': plan['session_id'], 'thread_id': None, 'turns': [],
             'score_eligible': False, 'normal_state_only': True, 'no_unrelated_session_reads': True,
             'state_root_scope': STATE_SCOPE, 'fixture_placed': False, 'fixture_removed': None}
    if route == 'acp':
        state.update(session_key=plan['session_key'], gateway_started=False, gateway_stopped=None,
                     provider_and_model='not on the ACP stream; see the native rows of the session')
    _save(destination, state)
    fixture: dict = {}
    gateway: dict = {}
    context = {'destination': destination, 'plan': plan, 'workload': workload, 'env': env, 'state': state, 'settle': settle,
               'timeout': timeout, 'runner': runner, 'gateway': gateway}
    workspace_before = None
    interrupted = None
    try:
        preflight_receipt = preflight(plan, env)
        _write_json(destination / 'preflight.json', preflight_receipt)
        if preflight_receipt.get('ready') is not True:
            raise OpenClawCaptureError(f"preflight failed; no model call submitted: {preflight_receipt.get('failure')}")
        workspace = Path(preflight_receipt['workspace'])
        if not workspace.is_absolute() or workspace.is_symlink() or not workspace.is_dir():
            raise OpenClawCaptureError('the agent workspace is not an ordinary directory; no model call submitted')
        resolved, home_resolved = workspace.resolve(), home.resolve()
        if resolved == home_resolved or home_resolved in resolved.parents or resolved in home_resolved.parents:
            raise OpenClawCaptureError('the agent workspace and the OpenClaw home overlap; the bracket cannot tell them apart; no model call submitted')
        if os.path.lexists(workspace / FIXTURE_NAME):
            raise OpenClawCaptureError(f'{FIXTURE_NAME} already exists in the agent workspace; nothing was placed and no model call was made')
        if route == 'acp' and port_open(plan['gateway_port']):
            raise OpenClawCaptureError(f"port {plan['gateway_port']} is already open: another gateway is running; nothing was placed and no model call was made")
        # Metadata of the workspace before the fixture is placed. Names stay in memory; only a digest is written.
        workspace_before = _workspace_listing(workspace)
        files = _tree(destination / f'workspaces/before/{FIXTURE_NAME}')
        if {name: _sha(raw) for name, raw in files.items()} != plan['protected_sha256']:
            raise OpenClawCaptureError('the prepared fixture copy differs from the plan')
        place_fixture(workspace, files, fixture)
        state.update(fixture_placed=True, workspace=str(workspace))
        _write_json(destination / 'fixture-placement.json', {**fixture, 'workspace_entries_before': len(workspace_before),
                                                             'workspace_listing_sha256': _sha(_canonical(workspace_before))})
        _save(destination, state)
        # Metadata of the whole OpenClaw home before the first turn (and before the gateway starts). Nothing is opened.
        started_ns = time.time_ns()
        before_detail: dict = {}
        before = inventory_openclaw_home(home, detail=before_detail)
        _write_json(destination / 'state-before.json', before)
        state['started_ns'] = started_ns
        context.update(workspace=workspace, resolved=resolved, fixture_path=Path(fixture['path']), before=before,
                       before_detail=before_detail, started_ns=started_ns, build=preflight_receipt.get('version'))
        (_acp_route if route == 'acp' else _local_route)(context)
        state.update(status='captured_pending_qualification', independent_reproduction=False)
    except Exception as error:
        state.update(status='capture_incomplete', failure=str(error))
    except BaseException as error:   # an interrupt still stops the gateway and removes the fixture; it is raised again below
        interrupted = error
        state.update(status='capture_incomplete', failure=f'interrupted: {type(error).__name__}')
    # The ACP process and the gateway that the controller started always stop, before the fixture leaves.
    try:
        _close_acp(context)
    except Exception:
        pass
    if gateway and not gateway.get('stopped'):
        try:
            stop = stop_gateway(gateway, destination)
        except Exception as error:
            stop = {'stopped': False, 'pid': gateway.get('pid'), 'failure': type(error).__name__}
        if not (destination / 'gateway-stop.json').exists():
            _write_json(destination / 'gateway-stop.json', stop)
        state['gateway_stopped'] = stop['stopped']
    if gateway and not gateway.get('stopped'):
        state.update(status='capture_incomplete', GATEWAY_NOT_STOPPED=gateway.get('pid'),
                     failure=f"GATEWAY NOT STOPPED (pid {gateway.get('pid')}, port {gateway.get('port')}); earlier failure: {state.get('failure')}")
    # The fixture always leaves the owner's workspace: copy it into the run directory, remove it, prove that it is gone.
    if fixture.get('placed'):
        try:
            copied = copy_fixture_out(fixture, destination / f'workspaces/after/{FIXTURE_NAME}')
        except Exception as error:
            copied = {'copied': [], 'skipped': [], 'failure': f'{type(error).__name__}: {error}'}
        removal = remove_fixture(fixture)
        effects = None
        try:
            if workspace_before is not None:
                effects = workspace_side_effects(workspace_before, _workspace_listing(Path(state['workspace'])))
        except Exception:
            effects = None
        _write_json(destination / 'fixture-removal.json', {'copy_out': copied, 'removal': removal,
                                                           'workspace_entries_changed_outside_the_fixture': effects})
        state.update(fixture_removed=removal['removed'], workspace_entries_changed_outside_the_fixture=None if effects is None else len(effects))
        if not removal['removed']:
            state.update(status='capture_incomplete', FIXTURE_NOT_REMOVED=removal['path'],
                         failure=f"FIXTURE NOT REMOVED from the owner's workspace ({removal.get('reason')}); earlier failure: {state.get('failure')}")
    _write_json(destination / 'capture-result.json', state)
    if interrupted is not None:
        raise interrupted
    return state


def rebracket_openclaw_capture(destination: Path | str, *, state_settle: Mapping | None = None) -> dict:
    """Take the native state of a capture whose bracket was refused, with the present rule table. No model call.

    Only for the route ``acp``, where the home is bracketed once at the end and
    a refusal would cost both turns. Conditions: the capture failed only on the
    native refusal; the gateway stopped and the fixture left; the home is, by
    metadata, exactly as it was at the refusal (a new inventory under the rules
    of the refusal equals ``r2-state-refused.json``); and the digested subtrees
    of the rule table did not change, so ``state-before.json`` still fits.
    The first result and the refusal stay; ``rebracket-result.json`` is added.
    """
    destination = Path(destination).absolute()
    plan = _json((destination / 'plan.json').read_bytes(), 'OpenClaw plan')
    result = _json((destination / 'capture-result.json').read_bytes(), 'OpenClaw capture result')
    refusal_raw = (destination / 'r2-refusal.json').read_bytes()
    refusal = _json(refusal_raw, 'OpenClaw refusal')
    refused_raw = (destination / 'r2-state-refused.json').read_bytes()
    if (plan.get('route') != 'acp' or result.get('status') != 'capture_incomplete' or result.get('model_submissions') != 2
            or 'R2 native state capture refused' not in str(result.get('failure')) or result.get('gateway_stopped') is not True
            or result.get('fixture_removed') is not True or [turn.get('status') for turn in result.get('turns', [])] != ['completed', 'completed']
            or (destination / 'rebracket-result.json').exists() or (destination / 'r2-native-receipt.json').exists()):
        raise OpenClawCaptureError('capture is not a complete ACP run whose only failure is the refused native bracket')
    if refusal['rules'].get('digest_subtrees') != rules_table()['digest_subtrees']:
        raise OpenClawCaptureError('the digested subtrees changed since the refusal; the first inventory no longer fits')
    home = Path(plan['openclaw_home'])
    if _canonical(inventory_openclaw_home(home, rules=refusal['rules'])) != _canonical(_json(refused_raw, 'refused inventory')):
        raise OpenClawCaptureError('the OpenClaw home changed since the refusal; the capture cannot be bracketed again')
    before = _json((destination / 'state-before.json').read_bytes(), 'first inventory')
    settle = {'interval_seconds': 0.5, 'max_reads': 8, **(state_settle or {})}
    state = {'schema_version': SCHEMA, 'attempt_id': destination.name, 'route': 'acp', 'model_submissions_in_rebracket': 0,
             'first_result_sha256': _sha((destination / 'capture-result.json').read_bytes()), 'refusal_sha256': _sha(refusal_raw),
             'refused_inventory_sha256': _sha(refused_raw), 'home_unchanged_since_refusal': True,
             'rules_added_after_refusal': {name: sorted(set(values) - set(refusal['rules'].get(name, [])))
                                           for name, values in rules_table().items() if set(values) - set(refusal['rules'].get(name, []))}}
    try:
        receipt, after = capture_openclaw_state(
            home, destination / 'r2-native', before, attempt_id=destination.name, turn=2, session_id=None, thread_id=None,
            session_key=plan['session_key'], key_id=plan['key_id'], acp_session_id=result['acp_session_id'], started_ns=result['started_ns'],
            build=_json((destination / 'preflight.json').read_bytes(), 'preflight').get('version'), **settle)
    except HermesStateError as error:
        state.update(status='capture_incomplete', failure=f'native state capture refused again: {error}',
                     unexplained=list(getattr(error, 'unexplained', [])))
        _write_json(destination / f"rebracket-refusal-{len(list(destination.glob('rebracket-refusal-*.json'))) + 1}.json", state)
        return state
    _write_json(destination / 'r2-state-after.json', after)
    _write_json(destination / 'r2-native-receipt.json', receipt)
    state.update(status='captured_pending_qualification', session_id=receipt['session_id'], thread_id=receipt['thread_id'],
                 native_receipt='r2-native-receipt.json', native_session_store_rows=receipt['session_store']['selected_rows'],
                 native_session_owned_files=len(receipt['classes']['session_owned']),
                 note=('the native state was taken after a refusal, with the present rule table and code, from a home that did not '
                       'change in between'))
    _write_json(destination / 'rebracket-result.json', state)
    return state
