#!/usr/bin/env python3
"""Produce explicitly transformed public OpenClaw candidates, preserving metric values.

The captures ran in the owner's normal OpenClaw state. The native record is a
JSON export of the session rows of four shared stores. Private values sit in
the row export, in the ACP streams, in the capture documents that name the
home, in the two metadata inventories of the whole OpenClaw home, and in the
receipt.

Changed, always at identical byte length of the file:

- the home user name and the home path, in every file;
- in the row export, tables of the read (``transcript_events``,
  ``trajectory_runtime_events``, ``session_windows``; every row, column and key
  stays): the tool descriptions and schema descriptions in the trace rows
  ``context.compiled`` are blanked with the vendor marker; the account e-mail
  in ``authProfileId`` is aliased; ``mirrorSourceFingerprint`` (the first 32
  hex of a SHA-256 of the message; for a user row the preimage is not known)
  is zeroed, also inside a Zstandard-compressed event: the event text with
  the zeroed value is compressed again and a Zstandard skippable frame of
  zeros brings the BLOB back to its byte length (``recompress_event``);
- in the row export, every table outside the read (the decoder never parses
  them; density counts their rows by size): every text value is blanked with
  its own marker and every BLOB is zeroed, except a value that is exactly an
  id of the test session (session id, session key, the id inside the key,
  ACP session id, Codex thread id). These tables hold the owner's skills
  catalog, the list of the owner's workspace files, the account ids of the
  Codex login, the workspace repository address and copies of the events.
  Numbers and NULLs stay;
- in the ACP streams: the names, descriptions and hints of the listed slash
  commands (``available_commands_update``) are blanked with the personal
  marker: the list can name the owner's own skills;
- in ``fixture-placement.json``: device, inode, the count of the owner's
  workspace entries and the digest of their listing; in ``gateway-stop.json``:
  size and digest of the raw gateway output;
- in the two inventories: each entry that the receipt does not classify, and
  is not a directory above a classified entry, gets aliased path components
  and blanked size, times and inode. Inside a digested subtree every
  directory name is aliased. The hash of every link target is zeroed. A path
  component that holds a hex run of 32 or more characters is aliased also
  when the receipt classifies its entry: such a name can be a digest of a
  private value (the Codex cache names a file by the SHA-1 of the account id
  and the user id);
- in the two inventories: every digest of a digested subtree (``entries_sha256``
  and each value of ``directory_sha256``) is 64 zeros. Their preimages are
  metadata listings of the owner's home (paths, inodes, nanosecond times). A
  digest that differs between the two inventories is the word ``changed``
  padded with underscores to 64 characters in the later inventory: the receipt
  check compares entries and needs only that fact. The receipt is recomputed
  from the public inventories and holds the same values;
- in the receipt: the class lists and the metadata digests are recomputed
  from the public inventories; the digest and file identity of every copied
  file (the files of the Codex thread, the temporary run files, the store
  copies) are blanked; the size of every store copy and the row totals of the
  owner's stores are blanked. The sizes of the files of the Codex thread stay:
  density counts them.

Not in the packets at all: the Codex rollout file, the shell snapshot and the
lock of the Codex thread, and the temporary run files. They are outside the
read. A shell snapshot holds the owner's shell environment.

Kept on purpose: every native row and key that the decoder reads; message
ids, tool call ids, run ids; the ids of the test session; the schema of the
stores (vendor DDL, no operator value); the names of the entries that changed
during the capture (vendor layout names).

Dependent digest references are updated transitively. The 31 metric rows must
be equal before and after. The public packet carries no hash of a private
original; the parent pins stay in a private sidecar.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from sanitize_claude_score_packets import transformation_receipts
from sanitize_codex_score_packets import _NOTICE as PERSONAL_NOTICE, VENDOR_NOTICE, instruction_phrases, require_no_instruction_phrase
from sanitize_hermes_score_packets import _ALPHABET, _BLANKED, _blank, _blank_text_number, _compact, _fill, _rows_bytes, encodings, private_markers
from build_openclaw_public_inputs import build as build_public_inputs
from session_bench.hermes_state_evidence import inventory_sha256
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.openclaw_acp_observer import stream_line
from session_bench.openclaw_score_inputs import AFTER, BEFORE, RECEIPT, observer_from_capture_documents, verify_state_receipt
from session_bench.openclaw_session_rows import AGENT_STORE, READ_TABLES, ROWS_FILE, SCHEMA_FILE, transcript_event
from session_bench.openclaw_state_capture import classify_openclaw_changes
from session_bench.public_digest_check import check_public_packets, read_public_set, short_digest_warnings
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('openclaw-2026-10-07-04', 'openclaw-2026-10-07-07', 'openclaw-2026-10-07-06')
_DIGITS = _ALPHABET.replace('x', '')
OUTSIDE_NOTICE = b'[value of a row outside the read removed at equal byte length]'
# A digest of a digested subtree (``entries_sha256``, ``directory_sha256``) hashes a metadata listing of the owner's home: paths, inode
# numbers and nanosecond times. No such digest is published. Where two inventories share one, both hold zeros; where it differs, the
# later inventory holds this state word, padded to the length of a digest (the receipt check reads only whether the two values differ).
CHANGED = 'changed'.ljust(64, '_')
_SCHEMA_REASON = ('digest of the vendor schema of a store (pragmas and sqlite_master rows); a reader recomputes it from the public '
                  'schema export with store_schema_sha256; it holds no operator value')
_FRAME_REASON = ('bytes of a Zstandard frame of one transcript event (the first prompt), as hex; the sanitizer wrote the frame from the '
                 'public event text; it is compressed public content, not a digest')
# Hex runs of 32 or more characters that are not 64-hex digests (``session_bench.public_digest_check.long_hex_runs``).
HEX_ALLOWLIST = (
    {'file': 'native/capture/' + ROWS_FILE, 'before': rb'"blob_hex": "', 'reason': _FRAME_REASON},
)
DIGEST_ALLOWLIST = {
    'inputs/capture/' + RECEIPT: {'session_store.stores.*.schema_sha256': _SCHEMA_REASON},
    'native/capture/' + ROWS_FILE: {'stores.*.schema_sha256': _SCHEMA_REASON},
}
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
_FINGERPRINT = re.compile(rb'(mirrorSourceFingerprint\\*":\\*")([0-9a-f]{32})')
_HEX_NAME = re.compile(r'[0-9A-Fa-f]{32,}')
_SKIPPABLE_MAGIC = 0x184D2A50
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal byte length: the home user name is aliased in every file; in the '
               'row export the tool and schema descriptions of the trace rows are blanked, the account e-mail is aliased, the mirror '
               'fingerprints are zeroed, and every text value and BLOB of a table outside the read is blanked except the ids of the test '
               'session; in the ACP streams the listed slash commands are blanked; in the home inventories every entry that the receipt '
               'does not classify gets aliased names and blanked size, times and inode, and every name inside a digested subtree is '
               'aliased; every digest of a digested subtree is zeros, or the word changed where the two inventories differ; receipt digests of inventories are recomputed; the digest and identity of every copied file, the size of the '
               'store copies and the row totals of the owner\'s stores are blanked. Every native row, column and decoded key is retained. '
               'The files of the Codex thread are not in the packet.')


def sha(data): return hashlib.sha256(data).hexdigest()


def _compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _encoded_size(text, depth):
    """Byte length of ``text`` after ``depth`` levels of JSON string encoding (without the quotes)."""
    for _ in range(depth):
        text = json.dumps(text, ensure_ascii=False)[1:-1]
    return len(text.encode('utf-8'))


def _alias_email(match):
    """An e-mail with every letter and digit replaced by ``x``; the ``@`` and the dots stay."""
    return re.sub(rb'[A-Za-z0-9_%+-]', b'x', match.group(0))


def recompress_event(blob_hex):
    """A compressed transcript event with ``mirrorSourceFingerprint`` zeroed, at the byte length of the original BLOB.

    Returns ``(hex, number of zeroed values)``. The event text is
    decompressed, the 32-hex value is zeroed (the text keeps its length, so
    ``event_utf8_bytes`` stays right), and the text is compressed again. The
    new frame is shorter than the old BLOB. A Zstandard skippable frame (magic
    ``0x184D2A50``, a length, then zeros) fills the rest; a reader skips it.
    A frame that cannot be brought to the old length this way raises.
    """
    from compression import zstd
    import struct
    original = bytes.fromhex(blob_hex)
    text = zstd.decompress(original)
    public, zeroed = _FINGERPRINT.subn(lambda match: match.group(1) + b'0' * 32, text)
    if not zeroed:
        return blob_hex, 0
    for level in (19, 22, 15, 12, 9, 6, 3, 1):
        frame = zstd.compress(public, level=level)
        room = len(original) - len(frame)
        if room == 0 or room >= 8:
            padded = frame + (struct.pack('<II', _SKIPPABLE_MAGIC, room - 8) + b'\0' * (room - 8) if room else b'')
            if zstd.decompress(padded) != public or len(padded) != len(original):
                raise ValueError('the rewritten compressed event does not read back')
            return padded.hex(), zeroed
    raise ValueError('a compressed transcript event cannot be rewritten at equal length; it needs a rule')


# --- row export ----------------------------------------------------------------------------------
def sanitize_rows(data, originals, private_values):
    """The row export with private values blanked at equal byte length; returns (bytes, counts).

    ``originals`` receives every instruction text that was blanked, for the
    phrase guard. ``private_values`` receives short private values of the
    blanked rows that must not appear anywhere in the public set.
    """
    document = json.loads(data)
    if _rows_bytes(document) != data:
        raise ValueError('row export is not in the exporter serialization')
    counts = {'tool_descriptions_blanked': 0, 'outside_values_blanked': 0, 'outside_blobs_zeroed': 0, 'outside_rows': 0,
              'emails_aliased': 0, 'fingerprints_zeroed': 0, 'compressed_events_rewritten': 0, 'fingerprints_zeroed_in_compressed_events': 0}
    identities = {document.get(key) for key in ('session_id', 'thread_id', 'session_key', 'key_id', 'acp_session_id')} - {None}

    def describe(value, key=None):
        if isinstance(value, dict):
            return {name: describe(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [describe(item, key) for item in value]
        if isinstance(value, str) and key == 'description' and value:
            originals.append(value)
            counts['tool_descriptions_blanked'] += 1
            # The description sits two string levels deep in the file: inside the event text, inside the row export.
            return _fill(_encoded_size(value, 2), VENDOR_NOTICE)
        return value

    for store in document['stores']:
        for table in store['tables']:
            read = store['store'] == AGENT_STORE and table['table'] in READ_TABLES
            for row in table['rows']:
                values = row['values']
                if read and table['table'] == 'trajectory_runtime_events':
                    event = json.loads(values['event_json'])
                    if _compact_json(event) != values['event_json']:
                        raise ValueError('a trace event is not compact JSON; it needs a rule')
                    if event.get('type') == 'context.compiled' and isinstance(event.get('data', {}).get('tools'), list):
                        event['data']['tools'] = describe(event['data']['tools'])
                        values['event_json'] = _compact_json(event)
                if read and table['table'] == 'transcript_events' and values.get('event_zstd') is not None:
                    # A compressed event is written again with its fingerprint zeroed. It must hold no other private value.
                    inner = json.dumps(transcript_event(row), ensure_ascii=False).encode()
                    if b'/Users/' in inner or _EMAIL.search(inner) or Path.home().name.encode() in inner:
                        raise ValueError('a compressed transcript event holds a private value; it needs a rule')
                    values['event_zstd']['blob_hex'], zeroed = recompress_event(values['event_zstd']['blob_hex'])
                    counts['compressed_events_rewritten'] += 1
                    counts['fingerprints_zeroed_in_compressed_events'] += zeroed
                if read:
                    continue
                counts['outside_rows'] += 1
                for key, value in values.items():
                    if isinstance(value, str) and value and value not in identities:
                        if store['store'].endswith('state_5.sqlite') and key in ('creator_user_id', 'creator_account_id', 'git_origin_url', 'git_sha'):
                            private_values.add(value)
                        if store['store'].endswith('state_5.sqlite') and key == 'creator_user_id' and isinstance(values.get('creator_account_id'), str):
                            # The Codex cache names a file by the SHA-1 of this document. The digest must not be public.
                            for flag in ('false', 'true'):
                                text = '{"account_id":%s,"chatgpt_user_id":%s,"is_workspace_account":%s}' % (
                                    json.dumps(values['creator_account_id']), json.dumps(value), flag)
                                private_values.add(hashlib.sha1(text.encode()).hexdigest())
                        if table['table'] == 'session_entry_snapshots' and key == 'value_json':
                            originals.append(value)
                        values[key] = _fill(_encoded_size(value, 1), OUTSIDE_NOTICE)
                        counts['outside_values_blanked'] += 1
                    elif isinstance(value, dict):
                        for name in ('blob_hex', 'text_hex'):
                            if isinstance(value.get(name), str):
                                value[name] = '0' * len(value[name])
                                counts['outside_blobs_zeroed'] += 1
    public = _rows_bytes(document)
    private_values.update(match.group(0).decode() for match in _EMAIL.finditer(public))
    public, emails = _EMAIL.subn(_alias_email, public)
    public, prints = _FINGERPRINT.subn(lambda match: match.group(1) + b'0' * 32, public)
    counts.update(emails_aliased=emails, fingerprints_zeroed=prints)
    if len(public) != len(data):
        raise ValueError('row export changed its byte length')
    return public, counts


# --- ACP streams ---------------------------------------------------------------------------------
def sanitize_stream(data):
    """An ACP stream with the listed slash commands blanked at equal byte length; returns (bytes, count)."""
    lines, count = [], 0
    for line in data.splitlines(keepends=True):
        wrapper = json.loads(line)
        if stream_line(wrapper['dir'], wrapper['raw'], wrapper['t_ns']) != line:
            raise ValueError('an ACP stream line is not in the controller serialization')
        message = json.loads(wrapper['raw'])
        forms = {_compact_json(message): _compact_json, json.dumps(message, ensure_ascii=False): lambda value: json.dumps(value, ensure_ascii=False)}
        if wrapper['raw'] not in forms:
            raise ValueError('an ACP message is not in a known JSON form; it needs a rule')
        update = message.get('params', {}).get('update') if isinstance(message.get('params'), dict) else None
        if isinstance(update, dict) and update.get('sessionUpdate') == 'available_commands_update':
            def blank(value):
                nonlocal count
                if isinstance(value, dict):
                    return {key: blank(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [blank(item) for item in value]
                if isinstance(value, str) and value:
                    count += 1
                    return _fill(_encoded_size(value, 2), PERSONAL_NOTICE)
                return value
            update['availableCommands'] = blank(update['availableCommands'])
            line = stream_line(wrapper['dir'], forms[wrapper['raw']](message), wrapper['t_ns'])
        lines.append(line)
    public = b''.join(lines)
    if len(public) != len(data):
        raise ValueError('ACP stream changed its byte length')
    return public, count


def sanitize_small_documents(name, data):
    """Blank the operator values of two small capture documents at equal byte length."""
    if name.endswith('/fixture-placement.json'):
        value = json.loads(data)
        for key in ('device', 'inode', 'workspace_entries_before'):
            value[key] = _blank(value[key])
        value['workspace_listing_sha256'] = '0' * 64
    elif name.endswith('/gateway-stop.json'):
        value = json.loads(data)
        for output in value.get('output', {}).values():
            output.update(sha256='0' * 64, size_bytes=_blank(output['size_bytes']))
    else:
        return data
    public = _compact(value)
    if _compact(json.loads(data)) != data or len(public) != len(data):
        raise ValueError(f'{name} is not in the controller serialization or changed its byte length')
    return public


# --- whole-home inventories and receipt ----------------------------------------------------------
def _classify(before, after, receipt):
    return classify_openclaw_changes(before, after, session_id=receipt['session_id'], thread_id=receipt['thread_id'], key_id=receipt.get('key_id'),
                                     acp_session_id=receipt.get('acp_session_id'), started_ns=receipt['started_ns'], rules=receipt['rules'])


def kept_paths(before, after, receipt):
    """Paths that keep their names: every classified entry and every directory above one."""
    classes = _classify(before, after, receipt)
    kept = set(classes['session_owned']) | set(classes['session_directories']) | set(classes['run_owned']) | set(classes['directories_changed'])
    kept |= {row['relative_path'] for row in classes['shared_changed']}
    kept |= {entry['relative_path'] for inventory in (before, after) for entry in inventory['entries'] if entry['kind'] == 'directory-digest'}
    # The session stores and their sidecars are vendor layout names, also when a file did not change.
    stores = {store + suffix for store in receipt['rules']['session_stores'] for suffix in ('', '-wal', '-shm', '-journal')}
    kept |= {entry['relative_path'] for inventory in (before, after) for entry in inventory['entries'] if entry['relative_path'] in stores}
    for path in list(kept):
        parts = path.split('/')
        kept.update('/'.join(parts[:depth]) for depth in range(1, len(parts)))
    return kept


def alias_inventories(documents):
    """Alias the unclassified entries of ``state-before`` and ``state-after`` and repair the receipt.

    ``documents`` maps the three names (receipt, before, after) to bytes.
    Returns (public documents, aliased names, kept names). An alias depends
    only on the position of a name among the names of the same length under
    the same directory, never on the name itself.
    """
    values = {name: json.loads(data) for name, data in documents.items()}
    if any(_compact(values[name]) != data for name, data in documents.items()):
        raise ValueError('capture document is not in the controller serialization')
    receipt, before, after = values[RECEIPT], values[BEFORE], values[AFTER]
    kept = kept_paths(before, after, receipt)
    aliases, counters, foreign = {}, {}, set()

    def alias(prefix, part):
        key = prefix + (part,)
        if '/'.join(key) in kept and not _HEX_NAME.search(part):
            return part
        if key not in aliases:
            size = _encoded_size(part, 1)   # the byte length of the name in the controller's JSON text
            number = counters.get((prefix, size), 0)
            counters[(prefix, size)] = number + 1
            tag = ''
            while True:   # the padding letter is not a digit of the tag, so two numbers never give one alias
                tag = _DIGITS[number % len(_DIGITS)] + tag
                number //= len(_DIGITS)
                if not number:
                    break
            if len(tag) > size:
                raise ValueError('inventory name is too short for a distinct alias')
            aliases[key] = 'x' * (size - len(tag)) + tag
            foreign.add(part)
        return aliases[key]

    def alias_path(path):
        parts = path.split('/')
        return '/'.join(alias(tuple(parts[:depth]), part) for depth, part in enumerate(parts))

    for inventory in (before, after):
        for entry in inventory['entries']:
            if entry['kind'] == 'directory-digest':
                renamed = {alias_path(path): digest for path, digest in entry['directory_sha256'].items()}
                if len(renamed) != len(entry['directory_sha256']):
                    raise ValueError('inventory alias merged two directories')
                entry['directory_sha256'] = renamed
            elif entry['relative_path'] not in kept:
                entry['relative_path'] = alias_path(entry['relative_path'])
                for key in _BLANKED:
                    entry[key] = _blank(entry[key])
            else:
                # A classified entry keeps its name, except a component with a long hex run (alias_path changes only those).
                entry['relative_path'] = alias_path(entry['relative_path'])
                if entry['kind'] == 'file':
                    # A file that keeps its name (a store, a log, a file of the thread): its size and inode are blanked.
                    for key in ('size_bytes', 'inode'):
                        entry[key] = _blank(entry[key])
            if entry['kind'] == 'symlink':
                entry['target_sha256'] = '0' * 64   # a link target is a path of the home; its hash is not published
        names = [entry['relative_path'] for entry in inventory['entries']]
        if len(set(names)) != len(names):
            raise ValueError('inventory alias merged two entries')
    # No digest of a digested subtree is published (see ``CHANGED``). The receipt check recomputes its classes from the two
    # inventories and compares entries: it needs to know whether a digest differs, not its value.
    digested = lambda inventory: {entry['relative_path']: entry for entry in inventory['entries'] if entry['kind'] == 'directory-digest'}
    early, late = digested(before), digested(after)
    for path in early.keys() & late.keys():
        was, now = early[path], late[path]
        # Which digests differ is read before any value is replaced.
        marks = {'entries_sha256': was['entries_sha256'] != now['entries_sha256'],
                 'directories': {name for name, digest in was['directory_sha256'].items() if name in now['directory_sha256'] and now['directory_sha256'][name] != digest}}
        now['entries_sha256'] = CHANGED if marks['entries_sha256'] else '0' * 64
        now['directory_sha256'] = {name: CHANGED if name in marks['directories'] else '0' * 64 for name in now['directory_sha256']}
    for entry in late.values():
        if entry['relative_path'] not in early:
            entry['entries_sha256'] = '0' * 64
            entry['directory_sha256'] = {name: '0' * 64 for name in entry['directory_sha256']}
    for entry in early.values():
        entry['entries_sha256'] = '0' * 64
        entry['directory_sha256'] = {name: '0' * 64 for name in entry['directory_sha256']}
    # The receipt must describe the public inventories: its class lists and metadata digests are recomputed.
    classes = _classify(before, after, receipt)
    recorded = receipt['classes']
    if any(len(classes[name]) != len(recorded[name]) for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')):
        raise ValueError('the public inventories classify differently')
    paths = {row['relative_path']: row.get('changed_paths') for row in recorded['shared_changed']}
    for row in classes['shared_changed']:
        if paths.get(row['relative_path']) is not None:
            row['changed_paths'] = sorted(alias_path(path) for path in paths[row['relative_path']])
    receipt['classes'] = {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')}
    receipt['accounting']['shared_entries_changed_content_not_read'] = sorted(row['relative_path'] for row in classes['shared_changed'])
    receipt['session_directories'] = classes['session_directories']
    hex_names = {part for path in kept for part in path.split('/') if _HEX_NAME.search(part)}
    old_after = receipt['after_inventory_sha256']
    receipt['before_inventory_sha256'], receipt['after_inventory_sha256'] = inventory_sha256(before), inventory_sha256(after)
    receipt['quiescence']['inventory_sha256'] = [receipt['after_inventory_sha256'] if digest == old_after else '0' * 64
                                                 for digest in receipt['quiescence']['inventory_sha256']]
    # The copied files are private: their digests and file identities are blanked. The sizes of the files of the
    # Codex thread stay (density counts them). The store copies are the owner's whole stores: size blanked too.
    for row in receipt['artifacts']:
        row.update(sha256='0' * 64, filesystem_id=_blank_text_number(row['filesystem_id']))
    for store in receipt['session_store']['stores']:
        for copied in store['copied_files']:
            copied.update(sha256='0' * 64, size_bytes=_blank(copied['size_bytes']), filesystem_id=_blank_text_number(copied['filesystem_id']))
        for table in store.get('tables', []):
            for key in ('total_rows', 'other_rows_counted_not_read'):
                table[key] = _blank(table[key])
    public = {RECEIPT: _compact(receipt), BEFORE: _compact(before), AFTER: _compact(after)}
    if any(len(public[name]) != len(data) for name, data in documents.items()):
        raise ValueError('inventory alias changed the byte length')
    verify_state_receipt(receipt, before, after)
    public_names = {part for path in kept for part in path.split('/')} - hex_names
    return public, foreign | hex_names, public_names


# --- all documents ---------------------------------------------------------------------------------
def home_path(original):
    """The one home directory that the capture plans name."""
    homes = set()
    for name, data in original.items():
        if name.endswith('/inputs/capture/plan.json'):
            match = re.match(r'/Users/[A-Za-z0-9._-]+(?=/)', json.loads(data)['openclaw_home'])
            if match is None:
                raise ValueError('the OpenClaw home is not under a home directory')
            homes.add(match.group(0))
    if len(homes) != 1:
        raise ValueError('the packets do not name one home directory')
    return homes.pop()


def sanitize(original):
    """Return (transformed documents, counts, private byte values that must not be public, blanked instruction texts)."""
    home = home_path(original)
    name = home[len('/Users/'):]
    runs = sorted({path.split('/', 1)[0] for path in original})
    replacements = [(home.encode(), b'/Users/' + b'x' * len(name)), (name.encode(), b'x' * len(name))]
    counts = {'home_alias_count': 1, 'tool_descriptions_blanked': 0, 'outside_values_blanked': 0, 'outside_blobs_zeroed': 0, 'outside_rows': 0,
              'emails_aliased': 0, 'fingerprints_zeroed': 0, 'compressed_events_rewritten': 0, 'fingerprints_zeroed_in_compressed_events': 0,
              'slash_command_texts_blanked': 0, 'home_name_occurrences': 0, 'hex_names_aliased': 0}
    base, blanked_texts, foreign, kept_names, private_values, hex_names = {}, [], set(), set(), set(), set()
    for path, data in original.items():
        if path.endswith('/native/capture/' + ROWS_FILE):
            data, found = sanitize_rows(data, blanked_texts, private_values)
            for key, value in found.items():
                counts[key] += value
        elif path.endswith('/acp-stream.jsonl'):
            data, found = sanitize_stream(data)
            counts['slash_command_texts_blanked'] += found
        elif _EMAIL.search(data) and not path.endswith(('.py', SCHEMA_FILE)):
            raise ValueError(f'an e-mail address needs a rule: {path.split("/", 1)[1]}')
        data = sanitize_small_documents(path, data)
        counts['home_name_occurrences'] += data.count(name.encode())
        for old, alias in replacements:
            data = data.replace(old, alias)
        base[path] = data
    for run in runs:
        triple = {key: base[f'{run}/inputs/capture/{key}'] for key in (RECEIPT, BEFORE, AFTER)}
        public, names, kept = alias_inventories(triple)
        foreign |= names
        kept_names |= kept
        hex_names |= {item for item in names if _HEX_NAME.search(item)}
        base.update({f'{run}/inputs/capture/{key}': data for key, data in public.items()})
    def rebind(files):
        """Replace the digest of every changed file by the digest of its public bytes, until nothing changes."""
        current = dict(files)
        for _ in range(30):
            digests = {run: {sha(original[path]).encode(): sha(data).encode() for path, data in current.items()
                             if path.startswith(run + '/') and original[path] != data} for run in runs}
            revised = {}
            for path, data in files.items():
                for old, new in digests[path.split('/', 1)[0]].items():
                    data = data.replace(old, new)
                revised[path] = data
            if revised == current:
                return current
            current = revised
        raise ValueError('digest dependency graph did not converge')

    transformed = rebind(base)
    # The observer is rebuilt from the public capture documents; the replay does the same and compares.
    for run in runs:
        prefix = f'{run}/inputs/capture/'
        documents = {path[len(prefix):]: data for path, data in transformed.items() if path.startswith(prefix)}
        observer = canonical(observer_from_capture_documents(documents))
        if len(observer) != len(original[f'{run}/inputs/observer.json']):
            raise ValueError('the public observer changed its byte length')
        base[f'{run}/inputs/observer.json'] = observer
    transformed = rebind(base)
    if any(len(original[path]) != len(data) for path, data in transformed.items()):
        raise ValueError('transformation changed physical byte counts')
    counts['hex_names_aliased'] = len(hex_names)
    # A hex name is forbidden as a whole and as its hex run (a name can be ``<digest>.json``).
    hex_values = hex_names | {run for item in hex_names for run in _HEX_NAME.findall(item)}
    forbidden = sorted(encodings(name) | {value.encode() for value in private_values | hex_values if len(value) >= 6})
    everything = b'\n'.join(transformed.values())
    if any(value in everything for value in forbidden):
        raise ValueError('the home user name, an account id or an e-mail address survived')
    foreign -= kept_names
    public_parts = set()
    for path, data in transformed.items():
        if not path.endswith((RECEIPT, BEFORE, AFTER)):
            continue
        document = json.loads(data)
        listed = [entry['relative_path'] for entry in document.get('entries', [])]
        listed += [item for entry in document.get('entries', []) for item in entry.get('directory_sha256', {})]
        for row in document.get('classes', {}).get('shared_changed', []):
            listed += [row['relative_path'], *row.get('changed_directories', []), *row.get('changed_paths', [])]
        public_parts.update(part for item in listed for part in item.split('/'))
    survivors = [item for item in (foreign | hex_names) & public_parts if len(item) >= 8]
    if survivors:
        raise ValueError(f'{len(survivors)} name(s) of other entries of the OpenClaw home survived')
    counts['foreign_inventory_names_aliased'] = len(foreign)
    counts['instruction_phrases_checked'] = require_no_instruction_phrase(transformed, instruction_phrases(blanked_texts))
    return transformed, counts, forbidden, blanked_texts


def build(source, output, *, runs=RUNS):
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('new destination required')
    original, pins, trees, receipts = {}, {}, {}, []
    for run in runs:
        parent = source / run
        trees[run] = _snapshot_tree(parent)
        pins[run] = sha(trees[run]['manifest.json'])
        original.update({f'{run}/{name}': data for name, data in trees[run].items() if name.startswith(('native/', 'inputs/'))})
    transformed, counts, forbidden, blanked_texts = sanitize(original)
    summary = []
    for run in runs:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        receipt, private_receipt = transformation_receipts(
            {name: original[f'{run}/{name}'] for name in files}, files, parent_manifest_sha256=pins[run],
            aliases=counts, description=DESCRIPTION)
        with tempfile.TemporaryDirectory(prefix='openclaw-public-alias-') as folder:
            native = Path(folder).resolve()
            for name, data in files.items():
                if name.startswith('native/'):
                    path = native / name[7:]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            support = {name[7:]: data for name, data in files.items() if name.startswith('inputs/') and name not in ('inputs/workload.json', 'inputs/observer.json', 'inputs/context.json')}
            support['public-transformation.json'] = canonical(receipt)
            packet = output / run
            build_score_replay_package(native, packet,
                workload_document=files['inputs/workload.json'], observer_document=files['inputs/observer.json'],
                context_document=files['inputs/context.json'], supporting_documents=support, source_root=parent / 'runtime')
        pin = sha((packet / 'manifest.json').read_bytes())
        after = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        before = replay_score_package(parent, expected_manifest_sha256=pins[run], os_sandboxed=True)
        if canonical(before['diagnostics']['intact']['metrics']) != canonical(after['diagnostics']['intact']['metrics']):
            raise ValueError('sanitization changed metric results')
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
        if controls['status'] != 'passed': raise ValueError('candidate tamper controls failed')
        public = _snapshot_tree(packet)
        leaks = sorted({f'{marker} in {name}' for name, data in public.items() for marker in private_markers(name.encode() + b'\n' + data, forbidden)})
        leaks += [f'{marker} in receipt' for marker in private_markers(canonical(after), forbidden)]
        if leaks: raise ValueError('private marker remains in the public candidate: ' + '; '.join(leaks))
        (output / f'{run}-receipt.json').write_bytes(canonical(after))
        (output / f'{run}-tamper.json').write_bytes(canonical(controls))
        receipts.append(after)
        (output / f'{run}-private-transformation.json').write_bytes(canonical(private_receipt))
        summary.append({'packet': run, 'manifest_sha256': pin, 'parent_manifest_sha256': pins[run],
                        'diagnostics_sha256': after['diagnostics_sha256'], 'all_31_metric_rows_unchanged': True,
                        'privacy_review_pending': True})
    for run in runs:
        if trees[run] != _snapshot_tree(source / run): raise ValueError('private parent changed')
    bundle = build_public_inputs(output, output / 'public-inputs-candidate.json', runs=runs)
    public_set = read_public_set([output / run for run in runs], [output / 'public-inputs-candidate.json'])
    # No file of a public packet (runtime and bundle included) may still hold a phrase of a blanked instruction text.
    require_no_instruction_phrase(public_set, instruction_phrases(blanked_texts))
    if any(private_markers(data, forbidden) for data in public_set.values()):
        raise ValueError('private marker remains in the public set')
    warnings = short_digest_warnings(public_set)
    if warnings:
        raise ValueError(f'{len(warnings)} short digest value(s) are not bound to public bytes: ' + ', '.join(sorted({row['key'] for row in warnings})))
    digests = check_public_packets([output / run for run in runs], receipts=receipts, extra_files=[output / 'public-inputs-candidate.json'],
                                   allowlist=DIGEST_ALLOWLIST, hex_allowlist=HEX_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'aliases': counts, 'digest_classes': digests['classes'], 'hex_run_classes': digests['hex_runs']['classes'],
              'public_bundle_sha256': bundle['sha256'], 'public_safe': False, 'independent_reproduction': False}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
