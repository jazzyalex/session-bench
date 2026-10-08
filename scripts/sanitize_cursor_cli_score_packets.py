#!/usr/bin/env python3
"""Produce explicitly transformed public Cursor CLI candidates, preserving metric values.

The captures ran with new empty ``CURSOR_CONFIG_DIR`` and ``CURSOR_DATA_DIR``
inside the run directory, which is under the operator's home. So the home user
name is in paths, in the project key of the transcript path, in ``ls`` output
and in the chat store. The chat store also holds the vendor system prompt and
one injected context message with the operator's rules and skill list.

Changed, always at identical byte length:

- the home user name, in every file and in file names (the project key);
- the account e-mail (shared alias rule), where a file holds it;
- the name of an operator plugin or skill that the model repeated in its
  thinking or in a response (found from the skill list of the injected
  context message), in every file;
- the directory name ``chats/<MD5 of the workspace path>``: the private value
  is a digest of a path with the home name. It becomes the MD5 of the public
  workspace path, in names and in every file, so the validator can still
  derive it;
- in ``root-inventory.json``: the digests of ``cli-config.json`` and
  ``statsig-cache.json`` are zeroed. Those files hold the account identity and
  stay private;
- in the shared-store extract: the ``hash`` values of ``ai_code_hashes`` (a
  short vendor digest with an unknown preimage that may cover the private file
  path; up to eight hex characters) become zeros and every row id becomes 0; the column and the rows
  stay;
- in the chat store: the system prompt (vendor marker); the injected context
  message (personal marker; the fragment ``OS Version: darwin`` is kept, the
  public inputs builder reads it); the five short digests of
  ``systemPromptFingerprint`` in the system message (digests of the private
  prompt, rules and MCP setup); field 4 of each turn blob, an opaque vendor
  token; ``blobEncryptionKey`` of the ``meta`` row (zeros).

The chat store is content-addressed: a blob id is the SHA-256 of the blob
bytes, and blobs name each other by id. A changed blob gets a new id, so every
blob that names it changes too, up to the root and the ``meta`` row. The
script computes the new ids to a fixed point and builds a new SQLite file with
stock SQLite: the same page size, schema text, ``user_version`` and journal
mode, and every row at its old rowid. The file is then filled with free pages
to the private byte length and every byte outside a live structure is set to
zero. The script requires: a clean integrity check; the public rows equal the
intended rows; every public blob id is the SHA-256 of its public bytes; the
public graph has the same classes and the same references as the private
graph under the id map. No id of a changed private blob may remain in any
public file, as text, as raw bytes or as hex text.

A reader of the public packet can verify: the SQLite file, each blob id
against its bytes, every reference, the decode and the 31 metric rows. A
reader cannot verify that the private store had the same shape; the script
checks it and the private ids stay in the sidecar. The physical page layout of
the public store is new; no metric reads it.

Kept on purpose: every native record and key; tool call ids, user message ids
and request ids (join keys and opaque per-call handles); the encrypted
reasoning signatures; the two file content blobs (the public workload file).

Dependent digest references are updated transitively across the three packets.
The 31 metric rows must be equal before and after. A final guard fails when
any public file still holds a phrase of a blanked text. The public packet
carries no hash of a private original; the parent pins stay in a private sidecar.
A last guard lists every hex value of 8 to 63 characters under a digest-like
key of public JSON that public bytes do not yield, and fails on any.
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
from sanitize_claude_score_packets import transform_documents, transformation_receipts
from sanitize_codex_score_packets import _string_paths, _string_tokens, instruction_phrases, require_no_instruction_phrase
from sanitize_antigravity_score_packets import scrub_free_space
from build_cursor_cli_public_inputs import OS_FRAGMENT, build as build_public_inputs
from session_bench.cursor_cli_score_inputs import EXTRACT, HARNESS_FILES
from session_bench.cursor_cli_store import classify_blobs, first, parse_message, read_meta, read_store, row_proof
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets, read_public_set, short_digest_warnings
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('cursor-cli-2026-10-06-r1', 'cursor-cli-2026-10-06-r2', 'cursor-cli-2026-10-06-r3')
CAPTURES = ROOT / 'artifacts/survival-v1-runs'
# Every digest of this set is a digest of public bytes or a replay output. Nothing is allowlisted.
DIGEST_ALLOWLIST = {}
VENDOR_NOTICE = b'[vendor instruction text removed at equal byte length]'
PERSONAL_NOTICE = b'[personal text removed at equal byte length]'
_HOME = re.compile(rb'/Users/([A-Za-z0-9_-]+)')
_SECRET = re.compile(rb'(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|auth0\|[0-9a-f]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@(?:gmail|googlemail|icloud|me|outlook|hotmail|live|yahoo|proton|protonmail|pm)\.(?:com|me)\b')
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal byte length: the home user name is aliased in every file and '
               'file name; the MD5 directory name of the workspace path is recomputed from the public path; the digests of the two '
               'private harness files in the root inventory are zeroed; in the chat store the system prompt, the injected context '
               'message (except its operating-system fragment), the short digests of systemPromptFingerprint, the opaque token of each turn '
               'and the blob encryption key are blanked; an operator plugin or skill name in model text is aliased. In the shared-store extract '
               'the vendor line hashes (column hash) and the row ids are zeroed. The chat store is rebuilt by stock SQLite with new content-addressed blob ids, every row at its rowid, '
               'filled with zeroed free pages to the private byte length. Every native record and key is retained. Transitive '
               'digest references are updated across the three packets.')


def sha(data): return hashlib.sha256(data).hexdigest()


def home_names(documents):
    """The home user name of every workspace path that a capture plan states."""
    names = set()
    for name, data in documents.items():
        if name.endswith('/plan.json'):
            argv = json.loads(data)['argv_base']
            match = _HOME.match(argv[argv.index('--workspace') + 1].encode())
            if match:
                names.add(match.group(1))
    return sorted(names)


FINGERPRINT_PATH = ('providerOptions', 'cursor', 'systemPromptFingerprint')
FINGERPRINT_KEYS = ('content', 'toolNames', 'mcp', 'rules', 'featureFlags')
_SKILL_PATH = re.compile(r'fullPath="([^"]+)"')


def operator_names(original):
    """Names of operator plugins and skills that the model wrote into its own text.

    The injected context message of a store lists the operator's skills by
    path. A path part is taken when the model text of that store (thinking and
    text steps) holds it as a word and no workload document holds it (a word of
    the prompt is public).
    """
    workload = b'\n'.join(data for name, data in original.items() if name.endswith('/inputs/workload.json')).decode('utf-8', 'replace')
    names = set()
    for name, data in original.items():
        if not name.endswith('/store.db'):
            continue
        blobs, _ = classify_blobs(read_store(data))
        candidates, texts = set(), []
        for entry in blobs.values():
            value = entry['value']
            if entry['class'] == 'message' and value.get('role') == 'user' and isinstance(value.get('content'), str):
                for path in _SKILL_PATH.findall(value['content']):
                    candidates.update(part for part in path.split('/') if len(part) >= 5)
            elif entry['class'] == 'step':
                texts += [text for text in (first(value, 1, 1), first(value, 3, 1)) if text]
        text = '\n'.join(texts)
        word = lambda part, where: re.search(r'(?<![A-Za-z0-9])' + re.escape(part) + r'(?![A-Za-z0-9])', where) is not None
        names.update(part for part in candidates if word(part, text) and not word(part, workload))
    return sorted(part.encode() for part in names)


def _blank(size, notice):
    return (notice + b'x' * size)[:size]


def blank_message(data, originals):
    """Blank the instruction text of one JSON message blob at equal length; returns the bytes.

    The system message is vendor text. A user message with text content is the
    injected context (operator rules and skill list); its operating-system
    fragment is kept.
    """
    message = json.loads(data)
    role, content = message.get('role'), message.get('content')
    if not isinstance(content, str) or role not in ('system', 'user'):
        return data
    tokens, paths = _string_tokens(data), list(_string_paths(message))
    if len(tokens) != len(paths):
        raise ValueError('cannot align the JSON strings of a message blob')
    out = bytearray(data)
    for (start, end, is_key), (path, _, text) in zip(tokens, paths):
        if not is_key and path[:-1] == FINGERPRINT_PATH and path[-1] in FINGERPRINT_KEYS:
            # A short digest of private text (the prompt, the operator's rules and MCP setup).
            out[start:end] = b'x' * (end - start)
            continue
        if is_key or path != ('content',) or start == end:
            continue
        notice = VENDOR_NOTICE if role == 'system' else PERSONAL_NOTICE
        out[start:end] = _blank(end - start, notice)
        at = data.find(OS_FRAGMENT.encode(), start, end) if role == 'user' else -1
        if at >= 0:
            # The kept fragment stays at its place; the marker goes before it or after it.
            fragment = OS_FRAGMENT.encode()
            out[start:end] = b'x' * (end - start)
            out[at:at + len(fragment)] = fragment
            place = start if at - start >= len(notice) else at + len(fragment)
            if place + len(notice) <= end:
                out[place:place + len(notice)] = notice
        originals.append(text)
    json.loads(bytes(out))
    return bytes(out)


def blank_turn_token(data):
    """Blank field 4 of a turn blob (an opaque vendor token) at equal length."""
    out = bytearray(data)
    for number, wire, value, start, _ in parse_message(data):
        if number == 1 and wire == 2:
            for inner, inner_wire, token, inner_start, inner_end in parse_message(value):
                if inner == 4 and inner_wire == 2:
                    out[start + inner_start:start + inner_end] = b'x' * len(token)
    return bytes(out)


def public_rows(database, aliases, originals):
    """The intended public rows of one store: (schema rows, meta rows, blob rows, id map, counts)."""
    store = read_store(database)
    blobs, exceptions = classify_blobs(store)
    if exceptions:
        raise ValueError('the private chat store is outside the decoder contract')

    def alias(data):
        for old, new in aliases:
            data = data.replace(old, new)
        return data
    base, counts = {}, {'instruction_messages_blanked': 0, 'turn_tokens_blanked': 0}
    for identity, entry in blobs.items():
        data = alias(entry['data'])
        if entry['class'] == 'message':
            blanked = blank_message(data, originals)
            counts['instruction_messages_blanked'] += blanked != data
            data = blanked
        elif entry['class'] == 'turn':
            blanked = blank_turn_token(data)
            counts['turn_tokens_blanked'] += blanked != data
            data = blanked
        base[identity] = data
    # New ids to a fixed point: a blob that names a changed blob changes too.
    current = dict(base)
    for _ in range(64):
        mapping = {bytes.fromhex(old): hashlib.sha256(data).digest() for old, data in current.items()}
        revised = {}
        for identity, data in base.items():
            if blobs[identity]['class'] not in ('message', 'file_content'):
                for old, new in mapping.items():
                    if old != new:
                        data = data.replace(old, new)
            revised[identity] = data
        if revised == current:
            break
        current = revised
    else:
        raise ValueError('blob id graph did not converge')
    id_map = {old: sha(data) for old, data in current.items()}
    if len(set(id_map.values())) != len(id_map) or any(len(current[old]) != len(blobs[old]['data']) for old in blobs):
        raise ValueError('a blob changed its length or two blobs became one')
    # The meta row is hex text of JSON. Its two values are replaced in the raw JSON bytes.
    meta = read_meta(store)
    raw = bytes.fromhex(store['tables']['meta'][0]['value'])
    for old, new in [(meta['latestRootBlobId'], id_map[meta['latestRootBlobId']])] + (
            [(meta['blobEncryptionKey'], '0' * len(meta['blobEncryptionKey']))] if isinstance(meta.get('blobEncryptionKey'), str) and meta['blobEncryptionKey'] else []):
        if old != new and raw.count(old.encode()) != 1:
            raise ValueError('cannot locate a value of the meta row')
        raw = raw.replace(old.encode(), new.encode())
    value = alias(raw).hex()
    row = store['tables']['meta'][0]
    if len(value) != len(row['value']):
        raise ValueError('the meta row changed its length')
    meta_rows = [{'__rowid__': row['__rowid__'], 'key': row['key'], 'value': value}]
    blob_rows = [{'__rowid__': entry['rowid'], 'id': id_map[identity], 'data': current[identity]} for identity, entry in blobs.items()]
    return store, blobs, meta_rows, blob_rows, id_map, counts


def build_store(private, meta_rows, blob_rows):
    """A new SQLite file with the given rows and the byte length of ``private``; every free byte is zero."""
    store = read_store(private)
    page_size = int.from_bytes(private[16:18], 'big') or 65536
    target = len(private) // page_size
    with tempfile.TemporaryDirectory(prefix='cursor-public-store-') as directory:
        path = Path(directory) / 'store.db'
        connection = sqlite3.connect(path, isolation_level=None)
        try:
            connection.execute(f'PRAGMA page_size={page_size}')
            connection.execute('PRAGMA auto_vacuum=0'); connection.execute('PRAGMA secure_delete=ON')
            connection.execute('PRAGMA journal_mode=DELETE')
            # Tables in the order of their root pages in the private file.
            with tempfile.TemporaryDirectory(prefix='cursor-private-schema-') as other:
                clone = Path(other) / 'store.db'
                clone.write_bytes(private)
                source = sqlite3.connect(clone)
                try:
                    statements = [row[0] for row in source.execute("SELECT sql FROM sqlite_master WHERE type='table' ORDER BY rootpage")]
                finally:
                    source.close()
            connection.execute('BEGIN')
            for statement in statements:
                connection.execute(statement)
            connection.executemany('INSERT INTO meta(rowid,key,value) VALUES (?,?,?)', [(row['__rowid__'], row['key'], row['value']) for row in meta_rows])
            connection.executemany('INSERT INTO blobs(rowid,id,data) VALUES (?,?,?)', [(row['__rowid__'], row['id'], row['data']) for row in blob_rows])
            connection.execute(f"PRAGMA user_version={int(store['user_version'])}")
            connection.execute('COMMIT')
            count = connection.execute('PRAGMA page_count').fetchone()[0]
            if count > target:
                raise ValueError('the rebuilt chat store is larger than the private file')
            if count < target:
                # One table page plus overflow pages of one zero value, then dropped: the pages become free pages.
                need, usable = target - count, page_size - private[20]
                connection.execute('CREATE TABLE __fill(x BLOB)')
                if need > 1:
                    size = (need - 1) * (usable - 4)
                    for _ in range(64):
                        connection.execute('BEGIN'); connection.execute('DELETE FROM __fill')
                        connection.execute('INSERT INTO __fill VALUES (zeroblob(?))', (size,)); connection.execute('COMMIT')
                        connection.execute('VACUUM')
                        now = connection.execute('PRAGMA page_count').fetchone()[0]
                        if now == target:
                            break
                        size += (target - now) * (usable - 4)
                    else:
                        raise ValueError('cannot fill the chat store to the private length')
                connection.execute('DROP TABLE __fill')
            if connection.execute('PRAGMA page_count').fetchone()[0] != target:
                raise ValueError('the rebuilt chat store has another page count')
            if private[18:20] == b'\x02\x02':
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            connection.close()
        if any(path.with_name(path.name + suffix).exists() and path.with_name(path.name + suffix).stat().st_size for suffix in ('-wal', '-journal')):
            raise ValueError('the rebuilt chat store left a journal')
        public = path.read_bytes()
    if len(public) != len(private):
        raise ValueError('the public chat store changed its size')
    return scrub_free_space(public)


def sanitize_store(private, aliases, originals):
    """The public bytes of one chat store, its id map and its counts; every check of the module text is made here."""
    store, blobs, meta_rows, blob_rows, id_map, counts = public_rows(private, aliases, originals)
    public = build_store(private, meta_rows, blob_rows)
    after = read_store(public)
    if (after['user_version'] != store['user_version'] or [row_proof(row) for row in after['tables']['meta']] != [row_proof(row) for row in meta_rows]
            or [(row['__rowid__'], row_proof(row)) for row in after['tables']['blobs']] != [(row['__rowid__'], row_proof(row)) for row in blob_rows]
            or sorted(row['sql'] or '' for row in after['schema']) != sorted(row['sql'] or '' for row in store['schema'])):
        raise ValueError('the public chat store rows differ from the intended rows')
    public_blobs, exceptions = classify_blobs(after)
    if exceptions:
        raise ValueError('the public chat store is outside the decoder contract')
    # Same graph: each private blob and its public blob have the same class, the same place and the same references.
    for old, entry in blobs.items():
        new = public_blobs[id_map[old]]
        if (entry['class'], entry['rowid'], entry['current']) != (new['class'], new['rowid'], new['current']):
            raise ValueError('the public blob graph differs from the private graph')
        if entry['class'] not in ('message', 'file_content'):
            mapped = entry['data']
            for other, target in id_map.items():
                mapped = mapped.replace(bytes.fromhex(other), bytes.fromhex(target))
            references = lambda data: [value for _, wire, value, _, _ in _walk(data) if wire == 2 and len(value) == 32 and value.hex() in public_blobs]
            if references(mapped) != references(new['data']):
                raise ValueError('the public blob references differ from the private references')
    counts.update(blob_ids_changed=sum(old != new for old, new in id_map.items()), blob_ids_kept=sum(old == new for old, new in id_map.items()))
    return public, id_map, counts


def _walk(data, depth=0):
    """Every field of a protobuf message and of the messages inside it."""
    try:
        fields = parse_message(data)
    except ValueError:
        return
    for field in fields:
        yield field
        if field[1] == 2 and depth < 6 and len(field[2]) != 32:
            yield from _walk(field[2], depth + 1)


def zero_harness_digests(data):
    """Zero the digests of the private harness files in one root inventory."""
    inventory, count = json.loads(data), 0
    for row in inventory['entries']:
        if row['path'] in HARNESS_FILES:
            token = row['sha256'].encode()
            if data.count(token) != 1:
                raise ValueError('cannot locate a harness file digest')
            data = data.replace(token, b'0' * len(token))
            count += 1
    return data, count


_EXTRACT_HASH = re.compile(rb'("hash"\s*:\s*")([0-9a-f]{1,8})(")')
_EXTRACT_ROWID = re.compile(rb'("rowid"\s*:\s*)(\d+)')


def zero_extract_values(data):
    """Zero the vendor line hashes and the row ids of one shared-store extract at equal length.

    ``hash`` is a short vendor digest with an unknown preimage; it may cover
    the private file path. A row id shows how many rows the operator's store
    held. No verifier reads either value. A row id becomes ``0`` followed by
    spaces, which JSON allows after a number. Returns (bytes, hashes, row ids,
    the private hash values).
    """
    rows = json.loads(data)['rows']
    private = [row['hash'].encode() for row in rows.get('ai_code_hashes', [])]
    expected = sum(len(items) for items in rows.values())
    data, hashes = _EXTRACT_HASH.subn(lambda match: match.group(1) + b'0' * len(match.group(2)) + match.group(3), data)
    data, rowids = _EXTRACT_ROWID.subn(lambda match: match.group(1) + b'0' + b' ' * (len(match.group(2)) - 1), data)
    after = json.loads(data)['rows']
    if (hashes != len(private) or rowids != expected or any(row['rowid'] != 0 for items in after.values() for row in items)
            or any(set(row['hash']) != {'0'} for row in after.get('ai_code_hashes', []))):
        raise ValueError('cannot zero the short digests and row ids of the shared-store extract')
    return data, hashes, rowids, private


def sanitize(original):
    """Return (transformed documents under their public names, counts, private tokens that may not remain)."""
    names = home_names(original)
    if not names:
        raise ValueError('no home path in the private packets')
    # Every home path first (the shared alias rule), then the bare home name.
    paths = sorted({match.group(0) for data in original.values() for match in _HOME.finditer(data)}, key=len, reverse=True)
    aliases = [(path, b'/Users/' + b'x' * (len(path) - 7)) for path in paths] + [(name, b'x' * len(name)) for name in names]
    # Operator plugin and skill names that the model repeated in its thinking or its responses.
    plugins = operator_names(original)
    aliases += [(name, b'x' * len(name)) for name in plugins]
    originals, counts, forbidden = [], {'harness_digests_zeroed': 0}, []

    def alias(data):
        for old, new in aliases:
            data = data.replace(old, new)
        return data
    # The MD5 directory of each workspace path, private and public.
    for name, data in original.items():
        if name.endswith('/inputs/capture/plan.json'):
            argv = json.loads(data)['argv_base']
            workspace = argv[argv.index('--workspace') + 1].encode()
            old, new = hashlib.md5(workspace).hexdigest().encode(), hashlib.md5(alias(workspace)).hexdigest().encode()
            aliases.append((old, new))
            forbidden.append(old)
    stores, short = {}, []

    def redact(name, data):
        if name.endswith('/store.db'):
            public, id_map, store_counts = sanitize_store(original[name], aliases, originals)
            stores[name] = id_map
            for key, value in store_counts.items():
                counts[key] = counts.get(key, 0) + value
            return public
        data = alias(data)
        if name.endswith('/root-inventory.json'):
            data, zeroed = zero_harness_digests(data)
            counts['harness_digests_zeroed'] += zeroed
        if name.endswith('/' + EXTRACT):
            data, hashes, rowids, private = zero_extract_values(data)
            counts['shared_store_line_hashes_zeroed'] = counts.get('shared_store_line_hashes_zeroed', 0) + hashes
            counts['shared_store_row_ids_zeroed'] = counts.get('shared_store_row_ids_zeroed', 0) + rowids
            short.extend(private)
        return data

    transformed, alias_counts = transform_documents(original, redact)
    for id_map in stores.values():
        for old, new in id_map.items():
            if old != new:
                forbidden += [old.encode(), bytes.fromhex(old), old.encode().hex().encode()]
    for name, data in original.items():
        if name.endswith('/root-inventory.json'):
            forbidden += [row['sha256'].encode() for row in json.loads(data)['entries'] if row['path'] in HARNESS_FILES]
        if name.endswith('/store.db'):
            key = read_meta(read_store(data)).get('blobEncryptionKey')
            if isinstance(key, str) and key:
                forbidden.append(key.encode())
    forbidden += names + plugins
    # A private line hash is short; it may not remain in any JSON text of the set.
    for name, data in transformed.items():
        if not name.endswith('.db') and any(token in data for token in short):
            raise ValueError('a private shared-store line hash survived')
    # A stream can cut a word between two text deltas. The joined thinking text of each stream must be clean too.
    for name, data in transformed.items():
        if name.endswith('.stdout.jsonl'):
            joined = ''.join(row.get('text', '') for row in map(json.loads, filter(None, data.decode('utf-8').splitlines()))
                             if row.get('type') == 'thinking' and isinstance(row.get('text'), str)).encode()
            if any(token in joined for token in plugins + names):
                raise ValueError('a private name survived across stream deltas')
    for token in forbidden:
        if any(token in data for data in transformed.values()):
            raise ValueError('a private value survived in a public file')
    renamed = {alias(name.encode()).decode(): data for name, data in transformed.items()}
    if len(renamed) != len(transformed):
        raise ValueError('two files got one public name')
    counts.update(alias_counts)
    counts.update(home_name_alias_count=len(names), operator_plugin_name_alias_count=len(plugins), instruction_phrases_checked=require_no_instruction_phrase(renamed, instruction_phrases(originals)))
    return renamed, counts, forbidden, originals, alias


def account_values(captures):
    """Account values of the private harness files of the retained runs; none may be in a public file."""
    values = set()
    for run in RUNS:
        config = captures / run / 'cursor-config/cli-config.json'
        cache = captures / run / 'cursor-config/statsig-cache.json'
        if config.is_file():
            info = json.loads(config.read_bytes()).get('authInfo', {})
            values.update(str(info[key]) for key in ('email', 'displayName', 'userId', 'authId') if info.get(key))
        if cache.is_file():
            document = json.loads(cache.read_bytes())
            user = json.loads(document.get('data', '{}')).get('user', {})
            values.update(str(item) for item in (document.get('userID'), user.get('userID'), user.get('ip')) if item)
            values.update(str(item) for item in (user.get('customIDs') or {}).values() if item)
            custom = user.get('custom') or {}
            values.update(str(custom[key]) for key in ('stripeCustomerID',) if custom.get(key))
    return sorted(value.encode() for value in values if len(value) >= 6)


def private_markers(data, forbidden=()):
    """Names of the private markers found in public bytes."""
    home = Path.home()
    found = []
    if str(home).encode() in data or home.name.encode() in data: found.append('home user name')
    if re.search(rb'/Users/(?!x+[/:"\\\'\s])[A-Za-z0-9_-]+', data): found.append('home path')
    if _SECRET.search(data): found.append('credential')
    if _EMAIL.search(data): found.append('e-mail')
    if any(token in data for token in forbidden): found.append('private value')
    return found


def build(source, output, *, captures=CAPTURES):
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('new destination required')
    original, pins, trees = {}, {}, {}
    receipts = []
    for run in RUNS:
        parent = source / run
        trees[run] = _snapshot_tree(parent)
        pins[run] = sha(trees[run]['manifest.json'])
        original.update({f'{run}/{name}': data for name, data in trees[run].items() if name.startswith(('native/', 'inputs/'))})
    transformed, counts, forbidden, blanked_texts, alias = sanitize(original)
    forbidden = list(forbidden) + account_values(Path(captures))
    summary = []
    for run in RUNS:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        before_files = {alias(name.encode()).decode(): data for name, data in trees[run].items() if name.startswith(('native/', 'inputs/'))}
        receipt, private_receipt = transformation_receipts(
            before_files, files, parent_manifest_sha256=pins[run], aliases=counts,
            personal_text_strings_blanked=len(blanked_texts) // len(RUNS), description=DESCRIPTION)
        with tempfile.TemporaryDirectory(prefix='cursor-public-alias-') as folder:
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
    for run in RUNS:
        if trees[run] != _snapshot_tree(source / run): raise ValueError('private parent changed')
    bundle = build_public_inputs(output, output / 'public-inputs-candidate.json')
    public_set = read_public_set([output / run for run in RUNS], [output / 'public-inputs-candidate.json'])
    # No file of a public packet (runtime and bundle included) may still hold a phrase of the blanked text.
    require_no_instruction_phrase(public_set, instruction_phrases(blanked_texts))
    if any(private_markers(data, forbidden) for data in public_set.values()):
        raise ValueError('private marker remains in the public set')
    # No short hex value under a digest-like key may remain unless public bytes yield it.
    warnings = short_digest_warnings(public_set)
    if warnings:
        raise ValueError(f'{len(warnings)} short digest value(s) are not bound to public bytes: ' + ', '.join(sorted({row['key'] for row in warnings})))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    check_public_packets([output / run for run in RUNS], receipts=receipts, extra_files=[output / 'public-inputs-candidate.json'], allowlist=DIGEST_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'aliases': counts,
              'public_bundle_sha256': bundle['sha256'], 'public_safe': False, 'independent_reproduction': False}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
