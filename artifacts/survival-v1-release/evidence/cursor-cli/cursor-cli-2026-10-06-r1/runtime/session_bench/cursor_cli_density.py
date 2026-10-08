"""Statements and logical records of one copied Cursor CLI session family.

The read is the chat store (tables ``meta`` and ``blobs``), the sidecar
``meta.json`` and the agent transcript JSONL. The decoder takes its facts
from the store and the declared version from the sidecar. The transcript is
in the read because the absence scan for usage opens it. One table row or one
transcript line is one record. The forward read is the ``blobs`` table in
rowid order (the order in which the rows were written), then the transcript.

What a record states (the count is made on the raw records):

- A ``user_message`` blob states its prompt. A JSON user message with parts
  states the same prompt again (it holds the prompt inside ``<user_query>``).
- A text step and a thinking step each state one assistant message. A JSON
  assistant message states each of its text parts and each of its tool calls.
  Its reasoning part holds only an encrypted signature and states nothing.
- A tool step states its call (arguments, call id) and the result of that
  call. A JSON tool message states the result of its call id. It states the
  call again when it holds the tool name and all text arguments of the call.
- A root blob with a pending assistant message (field 4) states the text and
  the tool calls of that message.
- A file content blob states a result again when its bytes are the full
  output of that result (the file text that a read returned).
- A transcript line of a user states its prompt; a line of the assistant
  states its text and its tool calls. The transcript has no ids. A line is
  bound to an event of the store by content: the same prompt, the same text,
  or the same tool name and arguments, in order.
- The JSON system message, the JSON user message with injected context (text
  content; it is in no turn of the turn tree), turn blobs, roots without a
  pending message and the ``meta`` row state no event.

No field marks a statement as superseded, so every statement is active.

Density: a row is counted as compact key-sorted JSON of its columns; a BLOB
counts by its raw length. A transcript line is counted as compact key-sorted
JSON. The first record that states an event keeps its role; a record that
states only events already stated is a ``snapshot``. A root with a pending
message has the role of that message (a call or an assistant message), so a
call that such a root states first is useful content there.
"""
import hashlib
import json
from pathlib import Path

from .cursor_cli_live import _tool_fields, find_store, message_parts, prompt_text
from .cursor_cli_store import classify_blobs, first, read_store, row_bytes, row_proof
from .native_density import BYTE_ACCOUNTING_RULE, NativeDensityInventory, _strict_object, _unresolved, forward_occurrences, holds_call_arguments, roles_after_repeats, string_leaves

_MAX_BYTES = 64 * 1024 * 1024
_USEFUL = {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'explanation'}
_UNCLASSIFIED = {'session', 'metadata', 'snapshot', 'system', 'index'}


def _canonical_bytes(value):
    return len(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _prompt_events(blobs):
    return {first(entry['value'], 1): f"message:{first(entry['value'], 2) or identity}" for identity, entry in blobs.items()
            if entry['class'] == 'user_message' and isinstance(first(entry['value'], 1), str) and first(entry['value'], 1)}


def transcript_statements(lines, blobs):
    """(base roles, statements) for the parsed transcript lines, bound to the events of the store by content.

    A tool use is the next call of the store with the same tool name and
    arguments that no earlier line took. A line that matches nothing states
    its own event.
    """
    prompts, texts, waiting = _prompt_events(blobs), {}, {}
    for identity, entry in blobs.items():
        if entry['class'] != 'step':
            continue
        said, tool = first(entry['value'], 1), first(entry['value'], 2)
        if said is not None and first(said, 1):
            texts.setdefault(first(said, 1), f'message:{identity}')
        fields = _tool_fields(tool) if tool is not None else None
        if fields is not None and isinstance(first(tool, 57), str):
            waiting.setdefault(json.dumps([fields['name'], fields['input']], sort_keys=True), []).append(first(tool, 57))
    roles, statements = [], []
    for number, row in enumerate(lines, 1):
        role, events = 'metadata', []
        message = row.get('message') if isinstance(row, dict) else None
        parts = message.get('content') if isinstance(message, dict) and isinstance(message.get('content'), list) else []
        parts = [part for part in parts if isinstance(part, dict)]
        text = ''.join(part.get('text', '') for part in parts if part.get('type') == 'text' and isinstance(part.get('text'), str))
        if isinstance(row, dict) and row.get('role') == 'user' and text:
            role = 'user_message'
            events = [event for prompt, event in prompts.items() if f'<user_query>\n{prompt}\n</user_query>' in text][:1] or [f'message:transcript-line-{number}']
        elif isinstance(row, dict) and row.get('role') == 'assistant':
            for index, part in enumerate(parts):
                if part.get('type') == 'text' and isinstance(part.get('text'), str) and part['text']:
                    events.append(texts.get(part['text'], f'message:transcript-line-{number}:part-{index}'))
                elif part.get('type') == 'tool_use':
                    queue = waiting.get(json.dumps([part.get('name'), part.get('input')], sort_keys=True), [])
                    events.append(f'call:{queue.pop(0)}' if queue else f'call:transcript-line-{number}:part-{index}')
            role = 'tool_call' if any(event.startswith('call:') for event in events) else 'assistant_message' if events else 'metadata'
        roles.append(role)
        statements.append(tuple(dict.fromkeys(events)))
    return roles, statements


def store_statements(blobs):
    """(record ids, base roles, statements) for every blob in rowid order.

    ``blobs`` is the result of ``classify_blobs``. The event ids are made from
    native keys: the message id of a prompt, the blob id of a text or thinking
    step, the call id of a call and of its result.
    """
    prompts, texts, calls, outputs = {}, {}, {}, {}
    for identity, entry in blobs.items():
        value = entry['value']
        if entry['class'] == 'user_message' and isinstance(first(value, 1), str) and first(value, 1):
            prompts.setdefault(first(value, 1), f"message:{first(value, 2) or identity}")
        elif entry['class'] == 'step':
            said, tool = first(value, 1), first(value, 2)
            if said is not None and first(said, 1):
                texts.setdefault(first(said, 1), f'message:{identity}')
            fields = _tool_fields(tool) if tool is not None else None
            if fields is not None and isinstance(first(tool, 57), str):
                calls[first(tool, 57)] = fields
                if isinstance(fields.get('output'), str) and fields['output']:
                    outputs.setdefault(fields['output'], first(tool, 57))

    def message_events(message, own):
        """Events of one JSON assistant message: its text parts and its tool calls."""
        events = []
        for number, (kind, part) in enumerate(message_parts(message)):
            if kind == 'text':
                if isinstance(part, str) and part:
                    events.append(texts.get(part, f'message:{own}:part-{number}'))
            elif isinstance(part.get('toolCallId'), str):
                events.append(f"call:{part['toolCallId']}")
        return events

    record_ids, roles, statements = [], [], []
    for identity, entry in blobs.items():
        value, kind, events, role = entry['value'], entry['class'], [], 'unknown'
        if kind == 'message':
            role = {'system': 'system', 'user': 'system', 'tool': 'tool_result'}.get(value.get('role'), 'metadata')
            prompt = prompt_text(value)
            if prompt is not None:
                role = 'user_message'
                events = [event for text, event in prompts.items() if f'<user_query>\n{text}\n</user_query>' in prompt][:1] or [f'message:{identity}']
            elif value.get('role') == 'assistant':
                events = message_events(value, identity)
                role = 'tool_call' if any(event.startswith('call:') for event in events) else 'assistant_message' if events else 'metadata'
            elif value.get('role') == 'tool':
                leaves = string_leaves(value)
                for part in value.get('content') if isinstance(value.get('content'), list) else []:
                    call = part.get('toolCallId') if isinstance(part, dict) else None
                    if not isinstance(call, str):
                        continue
                    events.append(f'result:{call}')
                    if call in calls and calls[call]['name'] == part.get('toolName') and holds_call_arguments(calls[call]['input'], leaves, names_tool=True):
                        events.append(f'call:{call}')
        elif kind == 'user_message':
            role = 'user_message'
            events = [prompts[first(value, 1)]] if first(value, 1) in prompts else []
        elif kind == 'step':
            said, tool, thought = first(value, 1), first(value, 2), first(value, 3)
            if said is not None:
                role = 'assistant_message'
                events = [texts[first(said, 1)]] if first(said, 1) in texts else []
            elif thought is not None:
                role = 'explanation'
                events = [f'message:{identity}'] if first(thought, 1) else []
            elif tool is not None and first(tool, 57) in calls:
                role = 'tool_call'
                events = [f'call:{first(tool, 57)}', f'result:{first(tool, 57)}']
        elif kind == 'root':
            role = 'session'
            pending = first(value, 4)
            if isinstance(pending, str) and pending:
                try:
                    events = message_events(json.loads(pending), identity)
                except (ValueError, AttributeError, TypeError):
                    events = []
                # The role of the pending message. A root that only repeats stated events becomes a snapshot below.
                role = 'tool_call' if any(event.startswith('call:') for event in events) else 'assistant_message' if events else 'snapshot'
        elif kind == 'turn':
            role = 'index'
        elif kind == 'file_content':
            role = 'snapshot'
            try:
                text = entry['data'].decode('utf-8')
            except UnicodeDecodeError:
                text = None
            if text in outputs:
                events = [f'result:{outputs[text]}']
        record_ids.append(f"blobs:{entry['rowid']}")
        roles.append(role)
        statements.append(tuple(dict.fromkeys(events)))
    return record_ids, roles, statements


def store_forward_occurrences(blobs, artifact):
    """(event id, occurrence id) for every statement of the store, in rowid order."""
    record_ids, _, statements = store_statements(blobs)
    return forward_occurrences([f'{artifact}:{record}' for record in record_ids], statements)


def read_cursor_family(native, *, artifacts):
    """Return (records, locators, exceptions, occurrences) for the declared copied family.

    ``artifacts`` is the packet inventory (path, sha256). A file that differs
    from it raises ValueError. A file outside the known family is counted as
    ``unknown`` and reported as an exception.
    """
    native = Path(native)
    directory, session = find_store(native)
    expected = {row['path']: row['sha256'] for row in artifacts}
    if len(expected) != len(artifacts) or directory + '/store.db' not in expected:
        raise ValueError('Cursor family inventory is malformed')
    records, locators, exceptions, occurrences = [], [], [], []
    blobs, seen = {}, set()

    def add(record_id, kind, size, proof):
        records.append({'record_id': record_id, 'record_kind': kind, 'logical_bytes': size,
                        'classification': 'useful' if kind in _USEFUL else 'unclassified' if kind in _UNCLASSIFIED else 'unknown'})
        locators.append({'id': record_id, 'sha256': hashlib.sha256(proof).hexdigest()})

    for name in sorted(expected):
        path = native / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Cursor family member is missing')
        data = path.read_bytes()
        if len(data) > _MAX_BYTES or hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('Cursor family member differs from its inventory')
        if name == directory + '/store.db':
            store = read_store(data)
            blobs, blob_exceptions = classify_blobs(store)
            exceptions += blob_exceptions
            for number, row in enumerate(store['schema']):
                add(f'{name}:sqlite_master:row-{number}', 'metadata', row_bytes(row), row_proof(row))
            for row in store['tables'].get('meta', []):
                add(f"{name}:meta:{row['__rowid__']}", 'session', row_bytes(row), row_proof(row))
            record_ids, roles, statements = store_statements(blobs)
            seen = {event for events in statements for event in events}
            rows = {row['__rowid__']: row for row in store['tables'].get('blobs', [])}
            for record, role, entry in zip(record_ids, roles_after_repeats(roles, statements), blobs.values()):
                if role == 'unknown':
                    exceptions.append({'code': 'unknown_blob_role', 'record': f'{name}:{record}'})
                add(f'{name}:{record}', role, row_bytes(rows[entry['rowid']]), row_proof(rows[entry['rowid']]))
            occurrences = forward_occurrences([f'{name}:{record}' for record in record_ids], statements)
            for table in sorted(set(store['tables']) - {'meta', 'blobs'}):
                exceptions.append({'code': 'unknown_store_table', 'record': f'{name}:{table}'})
                for row in store['tables'][table]:
                    add(f"{name}:{table}:{row['__rowid__']}", 'unknown', row_bytes(row), row_proof(row))
        elif name == directory + '/meta.json':
            add(name, 'session', _canonical_bytes(_strict_object(data)), data)
        elif name.startswith('cursor-data/projects/') and name.endswith(f'/agent-transcripts/{session}/{session}.jsonl'):
            # In the read, after the store (sorted names put the store first). A line that only repeats events
            # of the store is a snapshot.
            numbered = [(number, line.rstrip(b'\r')) for number, line in enumerate(data.split(b'\n'), 1) if line.strip()]
            lines = [_strict_object(raw) for _, raw in numbered]
            roles, statements = transcript_statements(lines, blobs)
            line_ids = [f'{name}:line-{number}' for number, _ in numbered]
            for record, role, events, row, (_, raw) in zip(line_ids, roles, statements, lines, numbered):
                final = 'snapshot' if events and all(event in seen for event in events) else role
                seen.update(events)
                add(record, final, _canonical_bytes(row), raw)
            occurrences = occurrences + forward_occurrences(line_ids, statements)
        else:
            exceptions.append({'code': 'unknown_family_member', 'record': name})
            add(name, 'unknown', len(data), data)
    return records, locators, exceptions, occurrences


def build_cursor_density(native, *, artifacts, complete_record_family=False):
    """Density evidence for a complete copied family; anything less stays unresolved."""
    if complete_record_family is not True:
        return _unresolved('complete native family required')
    try:
        records, locators, _, _ = read_cursor_family(native, artifacts=artifacts)
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f'Cursor density inventory failed: {error}')
    if not records or not sum(row['logical_bytes'] for row in records):
        return _unresolved('Cursor density inventory is empty')
    locators.append({'id': f'rule:{BYTE_ACCOUNTING_RULE}+database-rows+raw-transcript-lines:session_bench/cursor_cli_density.py',
                     'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return NativeDensityInventory({'evidence_complete': True, 'classification_rule': 'logical-record-role-v1', 'records': records},
                                  tuple(locators), ())
