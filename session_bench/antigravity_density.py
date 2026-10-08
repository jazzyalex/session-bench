"""Logical records of one complete copied Antigravity session directory.

Every file of the directory is in scope. One nonblank line of a transcript
file is one logical record. Its byte count is the length of its compact,
key-sorted, UTF-8 JSON encoding (the rule of ``native_density``). A JSON
companion is one record with the same encoding. A step output file is one
record with its raw byte count.

Roles follow the decoded function of the record, never the file name. The
directory holds the same step records four times (``transcript.jsonl``,
``transcript_full.jsonl`` and one chunk copy of each). Each copy of a step is
the same kind of record and gets the same role. The repetition is measured by
``broad.naive_reader_duplicate_safety``, not here. Unknown step types and
unknown files stay in the denominator as ``unknown``.
"""
import hashlib
import json
from pathlib import Path
import re

from .antigravity_live import RESULT_HEADER, step_contract_exceptions
from .native_density import BYTE_ACCOUNTING_RULE, NativeDensityInventory, _strict_object, _unresolved

_MAX_BYTES = 64 * 1024 * 1024
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}
_CHUNK = re.compile(r'logs/chunks/(transcript|transcript_full)/(\d{8})\.jsonl')
_STEP_OUTPUT = re.compile(r'steps/(\d+)/output\.txt')
_MESSAGE = re.compile(r'messages/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json')
_MESSAGE_KEYS = {'id', 'recipient', 'sender', 'priority', 'timestamp', 'hideFromUser', 'content'}


def _canonical_bytes(value):
    return len(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))


def antigravity_step_role(row):
    """Record role of one step object."""
    if not isinstance(row, dict) or step_contract_exceptions(row):
        return 'unknown'
    kind = row['type']
    if kind == 'USER_INPUT':
        return 'user_message' if isinstance(row.get('content'), str) else 'unknown'
    if kind == 'SYSTEM_MESSAGE':
        return 'system'
    if kind == 'PLANNER_RESPONSE':
        if row.get('tool_calls'):
            return 'tool_call'
        return 'assistant_message' if isinstance(row.get('content'), str) and row['content'] else 'unknown'
    # A GENERIC step is a tool result when it has the result header.
    return 'tool_result' if isinstance(row.get('content'), str) and RESULT_HEADER.match(row['content']) else 'unknown'


def decoded_full_row(row):
    """A ``transcript.jsonl`` step as ``transcript_full.jsonl`` writes it: tool arguments as JSON values."""
    row = json.loads(json.dumps(row))
    for call in row.get('tool_calls', []) if isinstance(row.get('tool_calls'), list) else []:
        if isinstance(call, dict) and isinstance(call.get('args'), dict):
            call['args'] = {key: json.loads(value) if isinstance(value, str) else value for key, value in call['args'].items()}
    return row


def _jsonl(data):
    return [(number, line.rstrip(b'\r')) for number, line in enumerate(data.split(b'\n'), 1) if line.strip()]


def read_antigravity_family(native, *, artifacts, primary):
    """Return (records, locators, exceptions) for the declared copied directory.

    ``artifacts`` is the packet inventory (path, sha256), with paths relative
    to ``native``. A file that differs from it, or malformed JSON, raises
    ValueError. A copy that does not hold the steps of the primary transcript,
    and a file outside the known family, are reported as exceptions.
    """
    native = Path(native)
    parts = primary.split('/')
    if len(parts) != 4 or parts[1:] != ['.system_generated', 'logs', 'transcript.jsonl']:
        raise ValueError('Antigravity primary path is malformed')
    session, base = parts[0], parts[0] + '/.system_generated/'
    names = sorted(row['path'] for row in artifacts)
    if len(set(names)) != len(names) or primary not in names:
        raise ValueError('Antigravity family inventory is malformed')
    expected = {row['path']: row['sha256'] for row in artifacts}
    data = {}
    for name in names:
        path = native / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Antigravity family member is missing')
        data[name] = path.read_bytes()
        if len(data[name]) > _MAX_BYTES or hashlib.sha256(data[name]).hexdigest() != expected[name]:
            raise ValueError('Antigravity family member differs from its inventory')
    records, locators, exceptions = [], [], []

    def add(record_id, kind, size, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': size,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': hashlib.sha256(proof).hexdigest()})

    steps = [_strict_object(raw) for _, raw in _jsonl(data[primary])]
    by_index = {row.get('step_index'): row for row in steps}
    full_name = base + 'logs/transcript_full.jsonl'
    chunks = {'transcript': [], 'transcript_full': []}
    for name in names:
        relative = name[len(base):] if name.startswith(base) else None
        chunk = _CHUNK.fullmatch(relative or '')
        output = _STEP_OUTPUT.fullmatch(relative or '')
        message = _MESSAGE.fullmatch(relative or '')
        if name in (primary, full_name) or chunk:
            for number, raw in _jsonl(data[name]):
                row = _strict_object(raw)
                role = antigravity_step_role(row)
                if role == 'unknown':
                    exceptions.append({'code': 'unknown_step_record', 'record': f'{name}:line-{number}'})
                add(f'{name}:line-{number}', role, _canonical_bytes(row), raw)
            if chunk:
                chunks[chunk.group(1)].append(data[name])
            elif name == full_name and [_strict_object(raw) for _, raw in _jsonl(data[name])] != [decoded_full_row(row) for row in steps]:
                exceptions.append({'code': 'full_transcript_differs', 'record': name})
        elif output:
            step = by_index.get(int(output.group(1)))
            content = step.get('content') if isinstance(step, dict) else None
            header = RESULT_HEADER.match(content) if isinstance(content, str) else None
            if header is None or content[header.end():].encode('utf-8') != data[name]:
                exceptions.append({'code': 'step_output_differs', 'record': name})
            add(name, 'tool_result', len(data[name]), data[name])
        elif message:
            document = _strict_object(data[name])
            if set(document) != _MESSAGE_KEYS or document.get('id') != message.group(1) or document.get('recipient') != session:
                exceptions.append({'code': 'unknown_message_shape', 'record': name})
            add(name, 'system', _canonical_bytes(document), data[name])
        elif relative == 'messages/read.json':
            document = _strict_object(data[name])
            if not all(value is True or value is False for value in document.values()):
                exceptions.append({'code': 'unknown_read_index_shape', 'record': name})
            add(name, 'metadata', _canonical_bytes(document), data[name])
        else:
            exceptions.append({'code': 'unknown_family_member', 'record': name})
            add(name, 'unknown', len(data[name]), data[name])
    if full_name not in data:
        exceptions.append({'code': 'full_transcript_missing', 'record': full_name})
    for kind, source in (('transcript', primary), ('transcript_full', full_name)):
        if b''.join(chunks[kind]) != data.get(source, b''):
            exceptions.append({'code': 'chunk_copy_differs', 'record': f'{base}logs/chunks/{kind}'})
    return records, locators, exceptions


def build_antigravity_density(native, *, artifacts, primary, complete_record_family=False):
    """Density evidence for a complete copied directory; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _ = read_antigravity_family(native, artifacts=artifacts, primary=primary)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Antigravity density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Antigravity density inventory is empty')
    locators.append({'id': f'rule:{BYTE_ACCOUNTING_RULE}+raw-text-companions:session_bench/antigravity_density.py',
                     'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())


# --- Whole-state family: the conversation database is the primary read; the transcript files are mirrors ---
def state_identity(store, session_id):
    """Session, harness and surface as the database itself states them (empty when it does not)."""
    from .antigravity_conversation_db import field, text
    meta = store['tables'].get('trajectory_meta', [])
    result = {'session_id': session_id if len(meta) == 1 and meta[0].get('cascade_id') == session_id else ''}
    # The prompt section ``identity`` of a model call names the product.
    for row in store['tables'].get('gen_metadata', []):
        for section in field(row.get('data') or b'', 1, 16):
            if text(section, 1) == 'identity' and 'You are Antigravity' in (text(section, 2) or ''):
                result['harness'] = 'Antigravity'
    # A tool step stores the location of its output file; the directory name of the store names the surface.
    marker = f'/antigravity-cli/brain/{session_id}/'
    for row in store['tables'].get('steps', []):
        if any(marker in (value.decode('utf-8', 'replace')) for value in field(row.get('step_payload') or b'', 140, 2, 7)):
            result['surface'] = 'antigravity-cli'
    return result


def _db_role(table, row):
    from .antigravity_conversation_db import STEP_MODEL, STEP_SYSTEM, STEP_TOOL, STEP_USER, field, tool_calls
    if table == 'steps':
        kind, payload = row.get('step_type'), row.get('step_payload')
        if not isinstance(payload, bytes):
            return 'unknown'
        if kind == STEP_USER:
            return 'user_message'
        if kind == STEP_MODEL:
            return 'tool_call' if tool_calls(payload) else 'assistant_message' if field(payload, 20, 1) else 'unknown'
        return {STEP_TOOL: 'tool_result', STEP_SYSTEM: 'system'}.get(kind, 'unknown')
    if table == 'gen_metadata':
        # A row that carries the messages sent to the model is a copy of earlier steps.
        return 'snapshot' if isinstance(row.get('data'), bytes) and field(row['data'], 1, 2) else 'metadata'
    return {'executor_metadata': 'metadata', 'trajectory_meta': 'session', 'trajectory_metadata_blob': 'session'}.get(table, 'unknown')


def mirror_differences(store, transcript):
    """Step indexes where ``transcript.jsonl`` does not state what the database step states (content comparison)."""
    from .antigravity_conversation_db import STEP_MODEL, STEP_SYSTEM, STEP_TOOL, STEP_USER, text, tool_calls
    steps = store['tables'].get('steps', [])
    rows = [_strict_object(raw) for _, raw in _jsonl(transcript)]
    if [row.get('step_index') for row in rows] != [step.get('idx') for step in steps]:
        return ['step list']
    different = []
    for row, step in zip(rows, steps):
        payload, kind, content = step.get('step_payload') or b'', step.get('step_type'), row.get('content')
        if kind == STEP_USER:
            same = row.get('type') == 'USER_INPUT' and isinstance(content, str) and f"<USER_REQUEST>\n{text(payload, 19, 2)}\n</USER_REQUEST>" in content
        elif kind == STEP_MODEL:
            calls = [(call.get('name'), decoded_full_row({'tool_calls': [call]})['tool_calls'][0].get('args')) for call in row.get('tool_calls', [])]
            same = (row.get('type') == 'PLANNER_RESPONSE' and (content or '') == (text(payload, 20, 1) or '')
                    and calls == [(name, arguments) for _, name, arguments in tool_calls(payload)])
        elif kind == STEP_TOOL:
            header = RESULT_HEADER.match(content) if isinstance(content, str) else None
            same = row.get('type') == 'GENERIC' and header is not None and content[header.end():] == text(payload, 140, 2, 1)
        elif kind == STEP_SYSTEM:
            same = row.get('type') == 'SYSTEM_MESSAGE' and isinstance(content, str) and (text(payload, 114, 4, 4) or '\x00') in content
        else:
            same = False
        if not same:
            different.append(step.get('idx'))
    return different


def read_state_family(native, *, artifacts, session_id, store):
    """Return (records, locators, exceptions) for the copied whole-state family.

    The database rows are classified by their function. A file under
    ``brain/<id>/`` that repeats database content is a ``snapshot``; this is
    proved by comparing its content with the database, not by its name. A
    mirror that differs keeps its own role and is reported as an exception.
    """
    from .antigravity_conversation_db import row_bytes, row_proof, text
    native = Path(native)
    database = f'conversations/{session_id}.db'
    primary = f'{session_id}/.system_generated/logs/transcript.jsonl'
    expected = {row['path']: row['sha256'] for row in artifacts}
    if database not in expected or len(expected) != len(artifacts):
        raise ValueError('Antigravity state family inventory is malformed')
    for name, digest in expected.items():
        path = native / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Antigravity family member differs from its inventory')
    records, locators, exceptions = [], [], []

    def add(record_id, kind, size, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': size,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': hashlib.sha256(proof).hexdigest()})

    for table in sorted(store['tables']):
        for row in store['tables'][table]:
            role = _db_role(table, row)
            if role == 'unknown':
                exceptions.append({'code': 'unknown_database_row', 'record': f"{database}:{table}:{row['__rowid__']}"})
            add(f"{database}:{table}:{row['__rowid__']}", role, row_bytes(row), row_proof(row))
    brain = [{**row, 'path': row['path'][len('brain/'):]} for row in artifacts if row['path'].startswith('brain/')]
    mirrored = False
    if any(row['path'] == primary for row in brain):
        mirror_records, mirror_locators, mirror_exceptions = read_antigravity_family(native / 'brain', artifacts=brain, primary=primary)
        exceptions += mirror_exceptions
        different = mirror_differences(store, (native / 'brain' / primary).read_bytes())
        if different:
            exceptions.append({'code': 'transcript_mirror_differs', 'record': f'brain/{primary}', 'steps': different})
        mirrored = not different and not mirror_exceptions
        notices = {text(row.get('step_payload') or b'', 114, 4, 4) for row in store['tables'].get('steps', [])}
        for record, locator in zip(mirror_records, mirror_locators):
            name = record['record_id'].split(':line-')[0]
            kind = record['record_kind']
            if mirrored and (name.endswith('.jsonl') or name.endswith('/output.txt')):
                kind = 'snapshot'   # proved equal to the database steps
            elif '/messages/' in name and not name.endswith('/read.json') and _strict_object((native / 'brain' / name).read_bytes()).get('content') in notices:
                kind = 'snapshot'   # the system message text is the text of a database system step
            add('brain/' + record['record_id'], kind, record['logical_bytes'], locator['sha256'].encode())
    else:
        exceptions.append({'code': 'transcript_mirror_missing', 'record': f'brain/{primary}'})
    for name in sorted(expected):
        data = (native / name).read_bytes()
        if name == database or name.startswith('brain/'):
            continue
        if name == f'annotations/{session_id}.pbtxt':
            add(name, 'index', len(data), data)       # a derived title of the conversation
        elif name == f'presence/{session_id}.lock':
            add(name, 'metadata', len(data), data)
        else:
            exceptions.append({'code': 'unknown_family_member', 'record': name})
            add(name, 'unknown', len(data), data)
    return records, locators, exceptions


def build_state_density(native, *, artifacts, session_id, store):
    try:
        records, locators, _ = read_state_family(native, artifacts=artifacts, session_id=session_id, store=store)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Antigravity density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Antigravity density inventory is empty')
    locators.append({'id': f'rule:{BYTE_ACCOUNTING_RULE}+database-rows+raw-text-companions:session_bench/antigravity_density.py',
                     'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())
