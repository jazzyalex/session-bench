"""Logical records of one complete copied Copilot session directory.

Every file of the directory is in scope. One nonblank ``events.jsonl`` line is
one logical record. Its byte count is the length of its compact, key-sorted,
UTF-8 JSON encoding (the rule of ``native_density``). A JSON companion is one
record with the same encoding. A text companion (YAML, Markdown, a backup
pre-image, a lock file) is one record with its raw byte count.

Roles follow the decoded function of the record. A tool request in
``assistant.message`` is the call; ``tool.execution_start`` repeats it and is
lifecycle metadata. Rewind snapshots and their backups are ``snapshot``.
Unknown event types and unknown files stay in the denominator as ``unknown``.

The session store is in the read too (usage is scored from it): each
``sqlite_master`` row and each table row is one record, counted as compact
key-sorted JSON of its columns (a BLOB counts by its raw length). Its ``turns``
row restates the prompt and the response of a turn and is a ``snapshot``, like
the rewind index that restates a prompt. A row of another table that holds the
complete prompt or response (the search index content) is a ``snapshot`` too. The
rest of its search index is ``index`` and its
usage rows are accounting. Every store row is unclassified; a row of a table
outside the known schema is ``unknown``.
"""
import hashlib
import json
from pathlib import Path
import re

from .copilot_session_store import STORE_DB, STORE_WAL, read_session_store_files, row_holds_text, store_row_bytes, store_row_proof
from .native_density import BYTE_ACCOUNTING_RULE, NativeDensityInventory, _strict_object, _unresolved

_MAX_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}')
_YAML_LINE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*): (.*)')
_EVENT_ROLES = {
    'session.start': 'session', 'session.resume': 'session', 'session.shutdown': 'session',
    'session.auto_mode_resolved': 'metadata', 'session.usage_checkpoint': 'metadata',
    'session.model_change': 'metadata', 'session.info': 'metadata', 'session.truncation': 'metadata',
    'assistant.turn_start': 'metadata', 'assistant.turn_end': 'metadata',
    'tool.execution_start': 'metadata', 'system.message': 'system', 'system.notification': 'system',
    'user.message': 'user_message', 'tool.execution_complete': 'tool_result',
}
_STORE_ROLES = {
    'sessions': 'session', 'schema_version': 'metadata', 'sqlite_sequence': 'metadata', 'assistant_usage_events': 'metadata',
    'turns': 'snapshot', 'session_files': 'index', 'session_refs': 'index', 'checkpoints': 'index',
    'search_index_config': 'index', 'search_index_content': 'index', 'search_index_data': 'index',
    'search_index_docsize': 'index', 'search_index_idx': 'index', 'forge_trajectory_events': 'index',
    'forge_skill_proposals': 'index', 'dynamic_context_items': 'index',
}
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}


def _canonical_bytes(value):
    return len(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))


def copilot_event_role(row):
    """Record role of one ``events.jsonl`` object."""
    kind, data = row.get('type'), row.get('data')
    if not isinstance(kind, str) or not isinstance(data, dict):
        return 'unknown'
    if kind == 'assistant.message':
        if isinstance(data.get('toolRequests'), list) and data['toolRequests']:
            return 'tool_call'
        return 'assistant_message' if isinstance(data.get('content'), str) and data['content'] else 'unknown'
    return _EVENT_ROLES.get(kind, 'unknown')


def parse_workspace_yaml(raw):
    """Read the flat ``key: value`` lines of ``workspace.yaml``; None when it is not flat."""
    result = {}
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return None
    for line in lines:
        match = _YAML_LINE.fullmatch(line)
        if match is None or match.group(1) in result:
            return None
        value = match.group(2)
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("''", "'")
        result[match.group(1)] = value
    return result


def read_copilot_family(native, *, artifacts):
    """Return (records, locators, exceptions) for the declared copied directory.

    ``artifacts`` is the packet inventory (path, sha256). A file that differs
    from it, or malformed JSON, raises ValueError. A file outside the known
    family is counted as ``unknown`` and reported as an exception.
    """
    native = Path(native)
    records, locators, exceptions = [], [], []

    def add(record_id, kind, size, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': size,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': hashlib.sha256(proof).hexdigest()})

    names = sorted(row['path'] for row in artifacts)
    if len(set(names)) != len(names) or 'events.jsonl' not in names:
        raise ValueError('Copilot family inventory is malformed')
    expected = {row['path']: row['sha256'] for row in artifacts}
    for name in names:
        path = native / name
        if name == STORE_WAL and STORE_DB in expected:
            # Counted with the database: the rows live in the WAL.
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
                raise ValueError('Copilot family member differs from its inventory')
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('Copilot family member is missing')
        data = path.read_bytes()
        if len(data) > _MAX_BYTES or hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('Copilot family member differs from its inventory')
        if name == 'events.jsonl':
            for number, line in enumerate(data.split(b'\n'), 1):
                raw = line.rstrip(b'\r')
                if not raw.strip():
                    continue
                row = _strict_object(raw)
                role = copilot_event_role(row)
                if role == 'unknown':
                    exceptions.append({'code': 'unknown_event_record', 'record': f'events.jsonl:line-{number}'})
                add(f'events.jsonl:line-{number}', role, _canonical_bytes(row), raw)
        elif name == 'workspace.yaml':
            if parse_workspace_yaml(data) is None:
                exceptions.append({'code': 'workspace_yaml_not_flat', 'record': name})
            add(name, 'session', len(data), data)
        elif name == 'rewind-file-snapshots/index.json':
            document = _strict_object(data)
            if document.get('schema') != 1 or not isinstance(document.get('snapshots'), list):
                exceptions.append({'code': 'unknown_rewind_index_schema', 'record': name})
            add(name, 'snapshot', _canonical_bytes(document), data)
        elif name == 'rewind-file-snapshots/tracking.json':
            document = _strict_object(data)
            if document.get('schema') != 1:
                exceptions.append({'code': 'unknown_rewind_tracking_schema', 'record': name})
            add(name, 'metadata', _canonical_bytes(document), data)
        elif name.startswith('rewind-file-snapshots/backups/') and _SHA.fullmatch(name.rsplit('/', 1)[1]):
            if hashlib.sha256(data).hexdigest() != name.rsplit('/', 1)[1]:
                exceptions.append({'code': 'backup_name_differs_from_content', 'record': name})
            add(name, 'snapshot', len(data), data)
        elif name == 'checkpoints/index.md':
            add(name, 'index', len(data), data)
        elif name.startswith('checkpoints/') and name.endswith('.md'):
            add(name, 'snapshot', len(data), data)
        elif name == STORE_DB:
            store = read_session_store_files(native)
            if store['physical_sha256'][STORE_WAL] != expected.get(STORE_WAL):
                raise ValueError('Copilot family member differs from its inventory')
            for number, row in enumerate(store['schema']):
                add(f'{STORE_DB}:sqlite_master:row-{number}', 'metadata', store_row_bytes(row), store_row_proof(row))
            for table in sorted(store['tables']):
                role = _STORE_ROLES.get(table, 'unknown')
                if role == 'unknown':
                    exceptions.append({'code': 'unknown_session_store_table', 'record': f'{STORE_DB}:{table}'})
                # A row of another table that holds the complete prompt or response of a turn restates it: a snapshot.
                texts = [value for turn in store['tables'].get('turns', []) for value in (turn.get('user_message'), turn.get('assistant_response'))]
                for number, row in enumerate(store['tables'][table]):
                    restates = role != 'unknown' and table != 'turns' and row_holds_text(row, texts)
                    add(f'{STORE_DB}:{table}:row-{number}', 'snapshot' if restates else role, store_row_bytes(row), store_row_proof(row))
        elif name == '.workspace-fork.lock':
            add(name, 'metadata', len(data), data)
        else:
            exceptions.append({'code': 'unknown_family_member', 'record': name})
            add(name, 'unknown', len(data), data)
    return records, locators, exceptions


def build_copilot_density(native, *, artifacts, complete_record_family=False):
    """Density evidence for a complete copied directory; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _ = read_copilot_family(native, artifacts=artifacts)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Copilot density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Copilot density inventory is empty')
    locators.append({'id': f'rule:{BYTE_ACCOUNTING_RULE}+raw-text-companions:session_bench/copilot_density.py',
                     'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())
