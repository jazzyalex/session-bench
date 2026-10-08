#!/usr/bin/env python3
"""Produce explicitly transformed public Antigravity CLI candidates, preserving metric values.

The captures ran in the operator's normal profile. Private values sit in three
places: the conversation database, the stdout streams, and the two metadata
inventories of the whole state directory.

Changed, always at identical byte length:

- the home user name, in every file. A stream text delta can cut the home path
  in two; the name is located in the joined text of each step and blanked in
  the raw lines;
- the source-hosting account name of the workspace repository (read from the
  conversation database), in every file;
- in the conversation database, by in-place BLOB writes on a private copy
  (SQLite stays valid, every row and page stays): the two aliases above; the
  installation-level session number and the opaque 404-byte client blob, which
  are equal in all runs; then every byte outside a live cell (free pages,
  free blocks, page slack) is set to zero; every string of the system prompt, the tool
  definitions and the prompt sections except ``identity``; every string of
  the request configuration (in ``gen_metadata`` and in each user step) and
  of ``executor_metadata``;
- in the two inventories: each entry that did not change during the capture
  and is not a directory above a changed entry gets aliased path components
  and blanked size and times. Kind, device and inode stay. Entries that the
  receipt classifies keep their names: they are vendor layout names, the
  captured conversation, and the log names of the run. The hash of every
  link target is zeroed: a target is a log file name and is easy to guess.
  The receipt's metadata digests are recomputed from the public inventories;
- in the shared-store extract (the summary rows of this conversation): the
  two aliases, also inside its hex-encoded protobuf values;
- in the receipt: the inventory digests are recomputed, and the digests of the
  run-owned private files (not in the packet) are zeroed.

Kept on purpose: every native file, row and key that the decoder reads; tool
call ids, response ids and provider request ids (join keys and opaque per-call
handles); the conversation and trajectory ids; the stream tool list.

Dependent digest references are updated transitively across the three packets.
The 31 metric rows must be equal before and after. The public packet carries no
hash of a private original; the parent pins stay in a private sidecar.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from sanitize_claude_score_packets import transformation_receipts
from build_antigravity_public_inputs import build as build_public_inputs
from session_bench.antigravity_conversation_db import field, parse_message, read_conversation_db, text
from session_bench.antigravity_root_evidence import classify_state_changes, inventory_sha256
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('antigravity-2026-10-05-02', 'antigravity-2026-10-05-03', 'antigravity-2026-10-05-04')
DIGEST_ALLOWLIST = {}
RECEIPT, BEFORE, AFTER = 'inputs/capture/r2-native-receipt.json', 'inputs/capture/state-before.json', 'inputs/capture/r2-state-after.json'
_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
_BLANKED = ('size_bytes', 'birth_ns', 'ctime_ns', 'mtime_ns')
_SECRET = re.compile(rb'(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@(?:gmail|googlemail|icloud|me|outlook|hotmail|live|yahoo|proton|protonmail|pm)\.(?:com|me)\b')
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal byte length: the home user name and the repository account '
               'name are aliased in every file; in the conversation database the installation-level session number, the opaque '
               'client blob, the system prompt, the tool definitions, the prompt sections except identity, the request '
               'configuration and executor_metadata are blanked by in-place BLOB writes; in the state inventories every entry '
               'that did not change gets aliased names and blanked size and times; receipt digests of inventories are '
               'recomputed and digests of private run files are zeroed. Every native file, row and decoded key is retained.')


def sha(data): return hashlib.sha256(data).hexdigest()


def _pretty(value):
    """The exact serialization of the capture controller."""
    return json.dumps(value, sort_keys=True, indent=2).encode() + b'\n'


# --- stdout stream -------------------------------------------------------------------------------
def _literal_offsets(raw, start):
    """Byte offset of each decoded character of the JSON string literal that opens at ``raw[start]``."""
    if raw[start:start + 1] != b'"':
        raise ValueError('not a JSON string literal')
    offsets, position = [], start + 1
    while raw[position:position + 1] != b'"':
        offsets.append(position)
        byte = raw[position]
        if byte == 0x5C:  # backslash escape
            if raw[position + 1:position + 2] == b'u':
                high = int(raw[position + 2:position + 6], 16)
                position += 12 if 0xD800 <= high < 0xDC00 else 6
            else:
                position += 2
        else:
            position += 1 if byte < 0x80 else 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
    return offsets


def alias_stream_text(data, name):
    """Blank ``name`` in the streamed step text, also when a text delta cuts it in two."""
    key = b'"text_delta":'
    lines = data.split(b'\n')
    steps = {}
    for number, line in enumerate(lines):
        if not line.strip():
            continue
        update = json.loads(line).get('step_update')
        if isinstance(update, dict) and isinstance(update.get('text_delta'), str):
            if line.count(key) != 1:
                raise ValueError('cannot locate the text delta of a stream line')
            steps.setdefault(update.get('step_index'), []).append((number, update['text_delta'], line.index(key) + len(key)))
    count = 0
    for pieces in steps.values():
        joined = ''.join(piece for _, piece, _ in pieces)
        owners = [(number, offset, literal) for number, piece, literal in pieces for offset in range(len(piece))]
        position = joined.find(name)
        while position >= 0:
            count += 1
            for index in range(position, position + len(name)):
                number, offset, literal = owners[index]
                at = _literal_offsets(lines[number], literal)[offset]
                if lines[number][at:at + 1] != joined[index].encode():
                    raise ValueError('stream text is not a plain character at the alias position')
                lines[number] = lines[number][:at] + b'x' + lines[number][at + 1:]
            position = joined.find(name, position + len(name))
    return b'\n'.join(lines), count


# --- conversation database -----------------------------------------------------------------------
def blank_leaves(data):
    """A protobuf message of equal length with every string and byte value blanked; numbers stay.

    A value that is printable text becomes ``x`` characters. A value that
    parses as a message is blanked field by field. Any other value becomes
    zero bytes.
    """
    try:
        fields = parse_message(data)
    except ValueError:
        return b'\x00' * len(data)
    out = bytearray(data)
    for _, wire, value, start, end in fields:
        if wire != 2 or not value:
            continue
        try:
            printable = all(character.isprintable() or character in '\n\r\t' for character in value.decode('utf-8'))
        except UnicodeDecodeError:
            printable = False
        out[start:end] = b'x' * len(value) if printable else blank_leaves(value)
    return bytes(out)


def rewrite_field(data, path, change):
    """Apply ``change`` (equal length) to every value at a path of field numbers."""
    if not path:
        new = change(data)
        if len(new) != len(data):
            raise ValueError('a field rewrite changed the byte length')
        return new
    try:
        fields = parse_message(data)
    except ValueError:
        return data
    out = bytearray(data)
    for number, wire, value, start, end in fields:
        if number == path[0] and wire == 2:
            out[start:end] = rewrite_field(value, path[1:], change)
    return bytes(out)


def _blank_sections(data):
    """Blank a prompt section body unless the section is ``identity`` (the product statement the decoder reads)."""
    return data if text(data, 1) == 'identity' else rewrite_field(data, (2,), lambda value: b'x' * len(value))


def _blank_user_configuration(data):
    """Blank everything of a user-input payload except the submitted text (fields 2 and 3)."""
    try:
        fields = parse_message(data)
    except ValueError:
        return data
    out = bytearray(data)
    for number, wire, value, start, end in fields:
        if wire == 2 and number not in (2, 3):
            out[start:end] = blank_leaves(value)
    return bytes(out)


def sanitize_database(database, replacements):
    """The database bytes with private BLOB content rewritten in place; returns (bytes, changed blob count).

    ``replacements`` are equal-length byte aliases applied to every BLOB. The
    work happens on a private copy. Incremental BLOB writes keep every page in
    its place. Free pages and the unused space inside pages keep older copies
    of rows, so every byte outside a live cell is then set to zero
    (``scrub_free_space``). Every row is compared with the intended result.
    """
    store = read_conversation_db(database)
    zero = lambda value: b'\x00' * len(value)
    rules = {
        'gen_metadata': [((1, 1), lambda value: b'x' * len(value)), ((1, 8), blank_leaves), ((1, 16), _blank_sections),
                         ((3,), blank_leaves), ((8,), zero), ((1, 4, 8, 2), lambda value: b'0' * len(value)),
                         ((1, 17, 2, 8, 2), lambda value: b'0' * len(value))],
        'executor_metadata': [((), blank_leaves)],
        'trajectory_metadata_blob': [((15,), zero)],
        'steps': [((9, 8, 2), lambda value: b'0' * len(value)), ((5, 9, 8, 2), lambda value: b'0' * len(value)),
                  ((19,), _blank_user_configuration)],
    }
    writes = []
    for table, rows in store['tables'].items():
        for row in rows:
            for column, value in row.items():
                if not isinstance(value, bytes):
                    continue
                new = value
                for path, change in rules.get(table, []):
                    new = rewrite_field(new, path, change)
                for old, alias in replacements:
                    new = new.replace(old, alias)
                if new != value:
                    writes.append((table, column, row['__rowid__'], new))
    with tempfile.TemporaryDirectory(prefix='antigravity-public-db-') as directory:
        clone = Path(directory) / 'conversation.db'
        clone.write_bytes(database)
        connection = sqlite3.connect(clone)
        try:
            for table, column, rowid, new in writes:
                with connection.blobopen(table, column, rowid) as blob:
                    if len(blob) != len(new):
                        raise ValueError('a BLOB rewrite changed the byte length')
                    blob.write(new)
            connection.commit()
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            connection.close()
        public = clone.read_bytes()
    if len(public) != len(database):
        raise ValueError('the public database changed its size')
    # Free pages and the unused space of a page hold older copies of rows. Every such byte is set to zero.
    public = scrub_free_space(public)
    after = read_conversation_db(public)
    expected = {(table, column, rowid): new for table, column, rowid, new in writes}
    for table, rows in store['tables'].items():
        if [{key: expected.get((table, key, row['__rowid__']), value) for key, value in row.items()} for row in rows] != after['tables'].get(table):
            raise ValueError('the public database rows differ from the intended rewrite')
    if after['user_version'] != store['user_version']:
        raise ValueError('the public database lost its user_version')
    return public, len(writes)


def _db_varint(data, position):
    value = 0
    for index in range(9):
        byte = data[position + index]
        if index == 8:
            return (value << 8) | byte, position + 9
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, position + index + 1


def scrub_free_space(database):
    """The database bytes with every byte outside a live structure set to zero; size and all rows stay.

    Kept: the file header, each b-tree page header, cell pointer array and
    live cell, each live overflow payload, the pointers of the free-page
    list, the first four bytes of each free block inside a page (its chain),
    pointer-map pages and the reserved bytes at the end of each page.
    Zeroed: free pages, free blocks, the gap between the pointer array and
    the cells, fragments, and the tail of the last overflow page of a value.
    """
    data = bytearray(database)
    size = int.from_bytes(data[16:18], 'big') or 65536
    usable = size - data[20]
    count = int.from_bytes(data[28:32], 'big')
    if count * size != len(data):
        raise ValueError('database header page count differs from the file size')
    with tempfile.TemporaryDirectory(prefix='antigravity-scrub-') as directory:
        clone = Path(directory) / 'c.db'
        clone.write_bytes(database)
        connection = sqlite3.connect(clone)
        try:
            roots = [1] + [row[0] for row in connection.execute('SELECT rootpage FROM sqlite_master WHERE rootpage > 0')]
        finally:
            connection.close()
    seen = set()

    def keep_page(number, ranges):
        """Zero every byte of the usable part of a page that is not in ``ranges``."""
        base = (number - 1) * size
        mask = bytearray(usable)
        for start, end in ranges:
            if not 0 <= start <= end <= usable:
                raise ValueError('page structure is out of bounds')
            mask[start:end] = b'\x01' * (end - start)
        for offset in range(usable):
            if not mask[offset]:
                data[base + offset] = 0

    def overflow(number, remaining):
        while remaining > 0:
            if number in seen or not 1 <= number <= count:
                raise ValueError('overflow chain is broken')
            seen.add(number)
            base = (number - 1) * size
            used = min(remaining, usable - 4)
            following = int.from_bytes(data[base:base + 4], 'big')
            keep_page(number, [(0, 4 + used)])
            remaining -= used
            number = following

    def tree(number):
        if number in seen or not 1 <= number <= count:
            raise ValueError('b-tree page is visited twice or out of range')
        seen.add(number)
        base = (number - 1) * size
        head = 100 if number == 1 else 0
        kind = data[base + head]
        if kind not in (2, 5, 10, 13):
            raise ValueError('unknown b-tree page type')
        interior = kind in (2, 5)
        header = 12 if interior else 8
        cells = int.from_bytes(data[base + head + 3:base + head + 5], 'big')
        ranges = [(0, head + header + 2 * cells)]
        block = int.from_bytes(data[base + head + 1:base + head + 3], 'big')
        while block:
            ranges.append((block, block + 4))
            block = int.from_bytes(data[base + block:base + block + 2], 'big')
        children = []
        for index in range(cells):
            pointer = int.from_bytes(data[base + head + header + 2 * index:base + head + header + 2 * index + 2], 'big')
            position = base + pointer
            if interior:
                children.append(int.from_bytes(data[position:position + 4], 'big'))
                position += 4
            if kind == 5:
                _, position = _db_varint(data, position)
                ranges.append((pointer, position - base))
                continue
            payload, position = _db_varint(data, position)
            if kind == 13:
                _, position = _db_varint(data, position)
                limit = usable - 35
            else:
                limit = (usable - 12) * 64 // 255 - 23
            minimum = (usable - 12) * 32 // 255 - 23
            local = payload
            if payload > limit:
                local = minimum + (payload - minimum) % (usable - 4)
                if local > limit:
                    local = minimum
            end = position + local
            if local < payload:
                overflow(int.from_bytes(data[end:end + 4], 'big'), payload - local)
                end += 4
            ranges.append((pointer, end - base))
        keep_page(number, ranges)
        if interior:
            children.append(int.from_bytes(data[base + head + 8:base + head + 12], 'big'))
        for child in children:
            tree(child)

    for root in roots:
        tree(root)
    trunk = int.from_bytes(data[32:36], 'big')
    while trunk:
        if trunk in seen or not 1 <= trunk <= count:
            raise ValueError('free-page list is broken')
        seen.add(trunk)
        base = (trunk - 1) * size
        leaves = int.from_bytes(data[base + 4:base + 8], 'big')
        for index in range(leaves):
            leaf = int.from_bytes(data[base + 8 + 4 * index:base + 12 + 4 * index], 'big')
            if leaf in seen or not 1 <= leaf <= count:
                raise ValueError('free-page list is broken')
            seen.add(leaf)
            keep_page(leaf, [])
        following = int.from_bytes(data[base:base + 4], 'big')
        keep_page(trunk, [(0, 8 + 4 * leaves)])
        trunk = following
    pointer_maps = set()
    if int.from_bytes(data[52:56], 'big'):
        number = 2
        while number <= count:
            pointer_maps.add(number)
            number += usable // 5 + 1
    lock_page = 0x40000000 // size + 1
    if set(range(1, count + 1)) - seen - pointer_maps - {lock_page}:
        raise ValueError('a database page belongs to no known structure')
    return bytes(data)


def database_private_values(database):
    """Private values that the database names: the repository account, the session number and the client blob."""
    store = read_conversation_db(database)
    values = {'accounts': set(), 'numbers': set(), 'blobs': set()}
    for row in store['tables'].get('trajectory_metadata_blob', []):
        slug = text(row['data'], 1, 3, 1)
        if slug and '/' in slug:
            values['accounts'].add(slug.split('/', 1)[0])
        values['blobs'].update(field(row['data'], 15))
    for row in store['tables'].get('steps', []):
        values['numbers'].update(field(row.get('metadata') or b'', 9, 8, 2))
    return values


# --- shared-store extract ---------------------------------------------------------------------------
def alias_extract(data, replacements):
    """Apply the byte aliases to the shared-store extract, also inside its hex-encoded protobuf values."""
    document = json.loads(data)
    if canonical(document) != data:
        raise ValueError('shared-store extract is not in canonical form')

    def change(value):
        if isinstance(value, dict):
            return {key: change(item) for key, item in value.items()}
        if isinstance(value, list):
            return [change(item) for item in value]
        if isinstance(value, str) and len(value) >= 40 and re.fullmatch(r'(?:[0-9a-f]{2})+', value):
            raw = bytes.fromhex(value)
            for old, alias in replacements:
                raw = raw.replace(old, alias)
            return raw.hex()
        return value

    public = canonical(change(document))
    for old, alias in replacements:
        public = public.replace(old, alias)
    if len(public) != len(data):
        raise ValueError('extract alias changed the byte length')
    return public


# --- whole-state inventories -----------------------------------------------------------------------
def _blank(value):
    """A number with the same digit count and no information; ``None`` stays."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError('inventory metadata is not a plain count')
    return int('1' + '0' * (len(str(value)) - 1))


def kept_paths(before, after, receipt):
    """Paths that keep their names: every classified entry and every directory above one."""
    classes = classify_state_changes(before, after, started_ns=receipt['started_ns'], conversation_id=receipt['conversation_id'])
    kept = set(classes['session_owned']) | set(classes['session_directories']) | set(classes['run_owned']) | set(classes['directories_changed'])
    kept |= {row['relative_path'] for row in classes['shared_changed']}
    for path in list(kept):
        parts = path.split('/')
        kept.update('/'.join(parts[:depth]) for depth in range(1, len(parts)))
    return kept


def foreign_names(before, after, receipt):
    """Path components of the entries that get an alias, without the names that a kept entry also uses."""
    kept = kept_paths(before, after, receipt)
    public = {part for path in kept for part in path.split('/')}
    return {part for inventory in (before, after) for entry in inventory['entries'] if entry['relative_path'] not in kept
            for part in entry['relative_path'].split('/')} - public


def alias_inventories(documents):
    """Alias the unchanged entries of ``state-before`` and ``state-after`` and repair the receipt digests.

    ``documents`` maps the three names (receipt, before, after) to bytes.
    An alias depends only on the position of a name among the names of the
    same length under the same directory, never on the name itself.
    """
    values = {name: json.loads(data) for name, data in documents.items()}
    if any(_pretty(values[name]) != data for name, data in documents.items()):
        raise ValueError('capture document is not in the controller serialization')
    receipt, before, after = values[RECEIPT], values[BEFORE], values[AFTER]
    kept = kept_paths(before, after, receipt)
    aliases, counters = {}, {}

    def alias(prefix, part):
        key = prefix + (part,)
        if '/'.join(key) in kept:
            return part
        if key not in aliases:
            number = counters.get((prefix, len(part)), 0)
            counters[(prefix, len(part))] = number + 1
            tag = ''
            while True:
                tag = _ALPHABET[number % len(_ALPHABET)] + tag
                number //= len(_ALPHABET)
                if not number:
                    break
            if len(tag) > len(part):
                raise ValueError('inventory name is too short for a distinct alias')
            aliases[key] = 'x' * (len(part) - len(tag)) + tag
        return aliases[key]

    for inventory in (before, after):
        for entry in inventory['entries']:
            if entry['relative_path'] in kept:
                continue
            parts = entry['relative_path'].split('/')
            entry['relative_path'] = '/'.join(alias(tuple(parts[:depth]), part) for depth, part in enumerate(parts))
            for key in _BLANKED:
                entry[key] = _blank(entry[key])
            # A link target is a file name of the state directory, for example a log name with a time.
            # Such a name is easy to guess, so its hash is not published.
        for entry in inventory['entries']:
            if entry['kind'] == 'symlink':
                entry['target_sha256'] = '0' * 64
        names = [entry['relative_path'] for entry in inventory['entries']]
        if len(set(names)) != len(names):
            raise ValueError('inventory alias merged two entries')
    # The receipt must describe the public inventories: its class lists and metadata digests are recomputed.
    classes = classify_state_changes(before, after, started_ns=receipt['started_ns'], conversation_id=receipt['conversation_id'])
    if any(len(classes[name]) != len(receipt['classes'][name]) for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')):
        raise ValueError('the public inventories classify differently')
    receipt['classes'] = {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')}
    # The receipt binds the inventories by digest. A digest of a private inventory is not published.
    old_after = receipt['after_inventory_sha256']
    receipt['before_inventory_sha256'], receipt['after_inventory_sha256'] = inventory_sha256(before), inventory_sha256(after)
    receipt['quiescence']['inventory_sha256'] = [receipt['after_inventory_sha256'] if digest == old_after else '0' * 64
                                                 for digest in receipt['quiescence']['inventory_sha256']]
    for row in receipt['artifacts']:
        if row['private_only']:
            row['sha256'] = '0' * 64   # a run-owned file is not in the packet
    public = {RECEIPT: _pretty(receipt), BEFORE: _pretty(before), AFTER: _pretty(after)}
    if any(len(public[name]) != len(data) for name, data in documents.items()):
        raise ValueError('inventory alias changed the byte length')
    return public


# --- all documents ---------------------------------------------------------------------------------
def home_path(original):
    """The one home directory that the captured workspace paths name."""
    homes = set()
    for name, data in original.items():
        if name.endswith('/inputs/capture-assertion.json'):
            match = re.match(r'/Users/[A-Za-z0-9._-]+(?=/)', json.loads(data)['captured_workspace'])
            if match is None:
                raise ValueError('captured workspace is not under a home directory')
            homes.add(match.group(0))
    if len(homes) != 1:
        raise ValueError('the packets do not name one home directory')
    return homes.pop()


def sanitize(original):
    """Return (transformed documents, counts, private byte values that must not be public)."""
    home = home_path(original)
    name = home[len('/Users/'):]
    runs = sorted({path.split('/', 1)[0] for path in original})
    databases = {path: data for path, data in original.items() if '/native/capture/conversations/' in path and path.endswith('.db')}
    private = {'accounts': set(), 'numbers': set(), 'blobs': set()}
    for data in databases.values():
        for key, found in database_private_values(data).items():
            private[key] |= found
    if any(_EMAIL.search(data) for path, data in original.items() if path not in databases):
        raise ValueError('an account e-mail needs an alias rule')
    replacements = [(home.encode(), b'/Users/' + b'x' * len(name)), (name.encode(), b'x' * len(name))]
    for account in sorted(private['accounts'], key=len, reverse=True):
        for form in {account, account.lower(), account.upper()}:
            replacements.append((form.encode(), b'x' * len(form.encode())))
    forbidden = [old for old, _ in replacements[1:]] + sorted(private['numbers']) + sorted(private['blobs'])
    foreign, public_names = set(), set()
    base, counts = {}, {'home_alias_count': 1, 'account_alias_count': len(private['accounts']), 'database_blobs_rewritten': 0,
                        'home_name_occurrences_in_stream_step_text': 0}
    for run in runs:
        triple = {key: original[f'{run}/{key}'] for key in (RECEIPT, BEFORE, AFTER)}
        loaded = {key: json.loads(data) for key, data in triple.items()}
        foreign |= foreign_names(loaded[BEFORE], loaded[AFTER], loaded[RECEIPT])
        public_names |= {part for path in kept_paths(loaded[BEFORE], loaded[AFTER], loaded[RECEIPT]) for part in path.split('/')}
    # A name that one run keeps (its log, a vendor file that changed) is an old entry in another run. It is public.
    foreign -= public_names
    for path, data in original.items():
        if path in databases:
            data, written = sanitize_database(data, replacements)
            counts['database_blobs_rewritten'] += written
        else:
            if path.endswith('.stdout.jsonl'):
                data, count = alias_stream_text(data, name)
                counts['home_name_occurrences_in_stream_step_text'] += count
            if path.endswith('/inputs/capture/shared-store-extract.json'):
                data = alias_extract(data, replacements)
            for old, alias in replacements:
                data = data.replace(old, alias)
        base[path] = data
    for run in runs:
        base.update({f'{run}/{key}': data for key, data in alias_inventories({key: base[f'{run}/{key}'] for key in (RECEIPT, BEFORE, AFTER)}).items()})
    # A digest of a changed file is replaced by the digest of its public bytes, until nothing changes.
    transformed = dict(base)
    for _ in range(30):
        # Per run: the after inventory of one run can be byte-equal to the before inventory of the next.
        digests = {run: {sha(original[path]).encode(): sha(data).encode() for path, data in transformed.items()
                         if path.startswith(run + '/') and original[path] != data} for run in runs}
        revised = {}
        for path, data in base.items():
            if path not in databases:
                for old, new in digests[path.split('/', 1)[0]].items():
                    data = data.replace(old, new)
            revised[path] = data
        if revised == transformed:
            break
        transformed = revised
    else:
        raise ValueError('digest dependency graph did not converge')
    if any(len(original[path]) != len(data) for path, data in transformed.items()):
        raise ValueError('transformation changed physical byte counts')
    everything = b'\n'.join(transformed.values())
    for value in forbidden:
        if value and (value in everything or (len(value) < 64 and value.hex().encode() in everything)):
            raise ValueError('a private value survived')
    for path, data in transformed.items():
        if path.endswith('.stdout.jsonl'):
            texts = {}
            for line in data.splitlines():
                update = json.loads(line).get('step_update', {}) if line.strip() else {}
                if isinstance(update.get('text_delta'), str):
                    texts[update.get('step_index')] = texts.get(update.get('step_index'), '') + update['text_delta']
            if any(name in value for value in texts.values()):
                raise ValueError('the home user name survived across two text deltas')
    # An aliased name must be gone from the inventories and receipts. A conversation id of the operator must be
    # gone everywhere. A captured conversation is listed as an old entry in the receipt of a later run; its id is public.
    listings = b'\n'.join(data for path, data in transformed.items() if path.endswith((RECEIPT, BEFORE, AFTER)))
    captured = {json.loads(original[f'{run}/{RECEIPT}'])['conversation_id'] for run in runs}
    identifier = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
    for item in foreign:
        match = identifier.search(item)
        if match and match.group(0) in captured:
            continue
        if (len(item) >= 8 and item.encode() in listings) or (match and match.group(0).encode() in everything):
            raise ValueError('a name of another entry of the state directory survived')
    counts['foreign_inventory_names_aliased'] = len(foreign)
    return transformed, counts, forbidden


def private_markers(data, forbidden=()):
    """Names of the private markers found in public bytes."""
    home = Path.home()
    found = []
    if str(home).encode() in data or home.name.encode() in data: found.append('home user name')
    if re.search(rb'/Users/(?!x+[/:"\\`\'.)\s])[A-Za-z0-9_-]+', data): found.append('home path')
    if _SECRET.search(data): found.append('credential')
    if _EMAIL.search(data): found.append('e-mail')
    if any(value and value in data for value in forbidden): found.append('account or installation value')
    return found


def build(source, output):
    receipts = []
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('new destination required')
    original, pins, trees = {}, {}, {}
    for run in RUNS:
        parent = source / run
        trees[run] = _snapshot_tree(parent)
        pins[run] = sha(trees[run]['manifest.json'])
        original.update({f'{run}/{name}': data for name, data in trees[run].items() if name.startswith(('native/', 'inputs/'))})
    transformed, alias_counts, forbidden = sanitize(original)
    summary = []
    for run in RUNS:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        receipt, private_receipt = transformation_receipts(
            {name: original[f'{run}/{name}'] for name in files}, files, parent_manifest_sha256=pins[run],
            aliases=alias_counts, description=DESCRIPTION)
        with tempfile.TemporaryDirectory(prefix='antigravity-public-alias-') as folder:
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
        leaks = sorted({f'{marker} in {name}' for name, data in public.items() for marker in private_markers(data, forbidden)})
        leaks += [f'{marker} in receipt' for marker in private_markers(canonical(after), forbidden)]
        if leaks: raise ValueError('private marker remains in the public candidate: ' + '; '.join(leaks))
        (output / f'{run}-receipt.json').write_bytes(canonical(after))
        (output / f'{run}-tamper.json').write_bytes(canonical(controls))
        receipts.append(after)
        (output / f'{run}-private-transformation.json').write_bytes(canonical(private_receipt))
        summary.append({'packet': run, 'manifest_sha256': pin, 'parent_manifest_sha256': pins[run],
                        'diagnostics_sha256': after['diagnostics_sha256'], 'all_31_metric_rows_unchanged': True,
                        'privacy_review_pending': True})
    for run in RUNS:
        if trees[run] != _snapshot_tree(source / run): raise ValueError('private parent changed')
    bundle = build_public_inputs(output, output / 'public-inputs-candidate.json')
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason. No allowlist is needed.
    check_public_packets([output / run for run in RUNS], receipts=receipts, extra_files=[output / 'public-inputs-candidate.json'], allowlist=DIGEST_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'aliases': alias_counts,
              'public_bundle_sha256': bundle['sha256'], 'public_safe': False, 'independent_reproduction': False}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
