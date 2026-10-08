#!/usr/bin/env python3
"""Produce explicitly transformed public Hermes candidates, preserving metric values.

The captures ran in the operator's normal Hermes home. The native record is a
JSON export of the session rows of the shared store. Private values sit in
four places: the row export, the capture documents that name the home, the
two metadata inventories of the whole Hermes home, and the receipt.

Changed, always at identical byte length:

- the home user name and the home path, in every file;
- in the system prompt row: the prompt text is blanked with the vendor marker,
  except its first sentence (``You are Hermes Agent, built by ...``), which
  names the harness and is the one fragment a metric reads. The prompt is one
  string that mixes vendor instruction text with text built from the
  operator's environment; the two cannot be told apart safely, so the whole
  rest is blanked under one marker. ``hash`` is the SHA-256 of the prompt, so
  it is set to the SHA-256 of the public prompt, here and in
  ``sessions.system_prompt_hash``: the join still verifies and no digest of
  the private text is public. The row id in the operator's store is blanked;
- in the row export (every row, column and key stays):
  ``sessions.tool_names`` (a digest of the tool list, which is not in the
  packet) is zeroed. Of the usage record ``_usage_anchor`` in
  ``sessions.model_config``: ``base_last_fp`` is the SHA-256 of the canonical
  JSON of ``content``, ``role`` and ``tool_call_id`` of one message row. It is
  a join key of the usage record. It is recomputed over the public message
  row and kept (a digest of public bytes); if it does not bind to a row it is
  zeroed. ``base_prefix_fp`` has no known preimage and can cover the private
  prompt, so it is zeroed;
  ``sessions.system_prompt_hash`` is rebound as said above (zeroed in a
  packet without the prompt row);
  ``messages.display_identity`` (a 32-byte digest with an unknown preimage)
  is zeroed; ``encrypted_content`` of the provider's reasoning items (an
  opaque payload that nobody can inspect) is blanked. A ``system_prompt``
  text or an ``api_content`` text, if a session row or message row held one,
  is blanked with the vendor marker and checked by the phrase guard;
- in the two inventories: each entry that the receipt does not classify, and
  is not a directory above a classified entry, gets aliased path components
  and blanked size, times and inode. Inside a digested subtree every
  directory name is aliased. The hash of every link target is zeroed;
- in the receipt: the class lists and the metadata digests are recomputed
  from the public inventories; the changed paths of the digested subtrees get
  the same aliases; the size, identity and digest of the store copy are
  blanked, and so are the size and inode of every file entry that keeps its
  name in the inventories (the store, the logs); the row totals of the operator's store (rows of other sessions)
  are blanked. No check reads them.

Kept on purpose: every native row and key that the decoder reads; message
ids, tool call ids and provider response ids (join keys and opaque handles);
the session id; the schema of the store (vendor DDL, no operator value); the
names of the entries that changed during the capture (vendor layout names).

Dependent digest references are updated transitively. The 31 metric rows must
be equal before and after. The public packet carries no hash of a private
original; the parent pins stay in a private sidecar.
"""
import argparse
import base64
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
from sanitize_codex_score_packets import instruction_phrases, require_no_instruction_phrase
from build_hermes_public_inputs import build as build_public_inputs
from session_bench.hermes_score_inputs import AFTER, BEFORE, RECEIPT, verify_state_receipt
from session_bench.hermes_state_evidence import classify_hermes_changes, inventory_sha256
from session_bench.hermes_store_rows import PROMPT_FILE, ROWS_FILE, SCHEMA_FILE, _HARNESS, usage_anchor
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets, read_public_set, short_digest_warnings
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('hermes-codex-2026-10-07-08', 'hermes-codex-2026-10-07-09', 'hermes-codex-2026-10-07-10')
VENDOR_NOTICE = b'[vendor instruction text removed at equal byte length]'
_METADATA_REASON = ('digest of a metadata listing of the operator\'s Hermes home (inode numbers and nanosecond times of every entry of a '
                    'subtree); the listing is not in the packet and cannot be rebuilt from public bytes and low-entropy guesses')
_SCHEMA_REASON = ('digest of the vendor schema of the store (pragmas and sqlite_master rows); a reader recomputes it from the public '
                  'schema export with store_schema_sha256; it holds no operator value')
_ANCHOR_REASON = ('base_last_fp of the usage record: the SHA-256 of the canonical JSON of content, role and tool_call_id of the message '
                  'row that the record names; the row is in the same public file and a reader recomputes the digest from it '
                  '(session_bench.hermes_store_rows.usage_anchor); the sanitizer recomputes and rebinds it')
DIGEST_ALLOWLIST = {
    'inputs/capture/' + BEFORE: {'entries.*.entries_sha256': _METADATA_REASON, 'entries.*.directory_sha256': _METADATA_REASON},
    'inputs/capture/' + AFTER: {'entries.*.entries_sha256': _METADATA_REASON, 'entries.*.directory_sha256': _METADATA_REASON},
    'inputs/capture/' + RECEIPT: {'classes.shared_changed.*.before.entries_sha256': _METADATA_REASON,
                                  'classes.shared_changed.*.after.entries_sha256': _METADATA_REASON,
                                  'session_store.stores.*.schema_sha256': _SCHEMA_REASON},
    'native/capture/' + ROWS_FILE: {'stores.*.schema_sha256': _SCHEMA_REASON,
                                    'stores.*.tables.*.rows.*.values.model_config': _ANCHOR_REASON},
    'inputs/capture/shared-store-extract/state-db-schema-version.json': {'schema_sha256': _SCHEMA_REASON},
}
_ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
_BLANKED = ('size_bytes', 'birth_ns', 'ctime_ns', 'mtime_ns', 'inode')
_HEX64 = re.compile(r'[0-9a-f]{64}')
_SECRET = re.compile(rb'(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@(?:gmail|googlemail|icloud|me|outlook|hotmail|live|yahoo|proton|protonmail|pm)\.(?:com|me)\b')
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal byte length: the home user name is aliased in every file; the '
               'system prompt text is blanked except its first sentence, and its SHA-256 (the row hash and sessions.system_prompt_hash) '
               'is the digest of the public text; in the row export the tool list digest, the two usage anchor fingerprints and '
               'display_identity are zeroed and the encrypted reasoning payload is blanked; in the home inventories every entry that the receipt does not '
               'classify gets aliased names and blanked size, times and inode, and every name inside a digested subtree is aliased; '
               'receipt digests of inventories are recomputed; the size, identity and digest of the store copy and the row totals of '
               'the operator\'s store are blanked. Every native row, column and decoded key is retained.')


def sha(data): return hashlib.sha256(data).hexdigest()


def _compact(value):
    """The exact serialization of the capture controller."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _rows_bytes(value):
    """The exact serialization of the row export."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode('utf-8') + b'\n'


def _fill(size, notice):
    """``size`` bytes: the notice, then padding."""
    return (notice[:size] + b'.' * max(0, size - len(notice))).decode()


# --- row export ----------------------------------------------------------------------------------
def sanitize_prompt_row(data, originals):
    """The system prompt row with its text blanked at equal byte length; returns (bytes, old hash, new hash).

    The first sentence stays: it names the harness. The rest is blanked with
    the vendor marker and goes to ``originals`` for the phrase guard. The hash
    becomes the SHA-256 of the public prompt. The row id is blanked.
    """
    document = json.loads(data)
    if canonical(document) + b'\n' != data:
        raise ValueError('system prompt row is not in canonical form')
    row = document['rows'][0]
    prompt, old = row['prompt'], row['hash']
    named = _HARNESS.match(prompt)
    stop = prompt.find('.', named.end()) + 1 if named else 0
    if not named or stop <= 0 or stop > 120 or sha(prompt.encode('utf-8')) != old:
        raise ValueError('system prompt row needs a rule: no harness sentence, or the hash is not the SHA-256 of the prompt')
    rest = prompt[stop:]
    originals.append(rest)
    # The length that counts is the length of the JSON text of the string (a line feed is two bytes there).
    size = len(json.dumps(rest, ensure_ascii=False).encode('utf-8')) - 2
    row['prompt'] = prompt[:stop] + _fill(size, VENDOR_NOTICE)
    new = sha(row['prompt'].encode('utf-8'))
    row['hash'] = document['session_system_prompt_hash'] = new
    row['_rowid'] = _blank(row['_rowid'])
    public = canonical(document) + b'\n'
    if len(public) != len(data):
        raise ValueError('system prompt row changed its byte length')
    return public, old, new


def sanitize_rows(data, originals, prompt_hashes=None):
    """The row export with private values blanked at equal byte length; returns (bytes, counts).

    ``originals`` receives every instruction text that was blanked, for the
    phrase guard. ``prompt_hashes`` maps the hash of a bound system prompt row
    to the hash of its public form. Every row, column and key stays.
    """
    document = json.loads(data)
    if _rows_bytes(document) != data:
        raise ValueError('row export is not in the exporter serialization')
    counts = {'digests_zeroed': 0, 'encrypted_payloads_blanked': 0, 'instruction_texts_blanked': 0}

    def zero(value):
        counts['digests_zeroed'] += 1
        return '0' * len(value)

    def instruction(value):
        originals.append(value)
        counts['instruction_texts_blanked'] += 1
        return _fill(len(value.encode('utf-8')), VENDOR_NOTICE)

    for table in document['stores'][0]['tables']:
        for row in table['rows']:
            values = row['values']
            if table['table'] == 'sessions':
                for key in ('system_prompt_hash', 'tool_names'):
                    if key == 'system_prompt_hash' and values.get(key) in (prompt_hashes or {}):
                        values[key] = prompt_hashes[values[key]]     # the SHA-256 of the public prompt row
                        counts['digests_rebound'] = counts.get('digests_rebound', 0) + 1
                    elif isinstance(values.get(key), str) and _HEX64.fullmatch(values[key]):
                        values[key] = zero(values[key])
                    elif values.get(key) is not None:
                        raise ValueError(f'sessions.{key} is not a digest; it needs a rule')
                if isinstance(values.get('system_prompt'), str) and values['system_prompt']:
                    values['system_prompt'] = instruction(values['system_prompt'])
                config = values.get('model_config')
                if isinstance(config, str):
                    # base_prefix_fp: no known preimage; it can cover the private prompt. base_last_fp is handled below.
                    for match in re.finditer(r'"base_prefix_fp": "([0-9a-f]{64})"', config):
                        config = config.replace(match.group(1), zero(match.group(1)))
                    values['model_config'] = config
            if table['table'] == 'messages':
                identity = values.get('display_identity')
                if isinstance(identity, dict) and 'blob_hex' in identity:
                    identity['blob_hex'] = zero(identity['blob_hex'])
                if isinstance(values.get('api_content'), str) and values['api_content']:
                    values['api_content'] = instruction(values['api_content'])
                items = values.get('codex_reasoning_items')
                if isinstance(items, str):
                    for match in re.finditer(r'"encrypted_content": "([^"\\]+)"', items):
                        items = items.replace(match.group(1), 'x' * len(match.group(1)))
                        counts['encrypted_payloads_blanked'] += 1
                    if 'encrypted_content' in items and '"encrypted_content": "x' not in items:
                        raise ValueError('an encrypted reasoning payload was not blanked')
                    values['codex_reasoning_items'] = items
    # The join key of the usage record: recompute it over the public message row that it names, and rebind it.
    tables = {table['table']: sorted(table['rows'], key=lambda row: row['rowid']) for table in document['stores'][0]['tables']}
    for row in tables.get('sessions', []):
        config = row['values'].get('model_config')
        for match in re.finditer(r'"base_last_fp": "([0-9a-f]{64})"', config if isinstance(config, str) else ''):
            bound = usage_anchor(row['values'], tables.get('messages', []))
            if bound is None:
                config = config.replace(match.group(1), zero(match.group(1)))   # it binds to no row: not a digest of public bytes
            else:
                number = json.loads(config)['_usage_anchor']['base_count']
                member = {key: tables['messages'][number - 1]['values'].get(key) for key in ('content', 'role', 'tool_call_id')}
                fresh = sha(json.dumps(member, sort_keys=True, separators=(',', ':')).encode())
                config = config.replace(match.group(1), fresh)
                counts['digests_rebound'] = counts.get('digests_rebound', 0) + 1
            row['values']['model_config'] = config
    public = _rows_bytes(document)
    if len(public) != len(data):
        raise ValueError('row export changed its byte length')
    return public, counts


# --- whole-home inventories and receipt ----------------------------------------------------------
def _blank(value):
    """A number with the same digit count and no information; ``None`` stays."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError('inventory metadata is not a plain count')
    return int('1' + '0' * (len(str(value)) - 1))


def _blank_text_number(text):
    return ''.join('0' if character.isdigit() else character for character in text)


def kept_paths(before, after, receipt):
    """Paths that keep their names: every classified entry and every directory above one."""
    classes = classify_hermes_changes(before, after, session_id=receipt['session_id'], started_ns=receipt['started_ns'], rules=receipt['rules'])
    kept = set(classes['session_owned']) | set(classes['session_directories']) | set(classes['run_owned']) | set(classes['directories_changed'])
    kept |= {row['relative_path'] for row in classes['shared_changed']}
    kept |= {entry['relative_path'] for inventory in (before, after) for entry in inventory['entries'] if entry['kind'] == 'directory-digest'}
    # The session store and its sidecars are vendor layout names, also in a turn that left the main file as it was.
    stores = {store + suffix for store in receipt['rules']['session_stores'] for suffix in ('', '-wal', '-shm', '-journal')}
    kept |= {entry['relative_path'] for inventory in (before, after) for entry in inventory['entries'] if entry['relative_path'] in stores}
    for path in list(kept):
        parts = path.split('/')
        kept.update('/'.join(parts[:depth]) for depth in range(1, len(parts)))
    return kept


def alias_inventories(documents):
    """Alias the unclassified entries of ``state-before`` and ``state-after`` and repair the receipt.

    ``documents`` maps the three names (receipt, before, after) to bytes.
    Returns (public documents, aliased names, kept names). An alias depends only on the
    position of a name among the names of the same length under the same
    directory, never on the name itself.
    """
    values = {name: json.loads(data) for name, data in documents.items()}
    if any(_compact(values[name]) != data for name, data in documents.items()):
        raise ValueError('capture document is not in the controller serialization')
    receipt, before, after = values[RECEIPT], values[BEFORE], values[AFTER]
    kept = kept_paths(before, after, receipt)
    aliases, counters, foreign = {}, {}, set()

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
            if len(tag.encode()) > len(part.encode()):
                raise ValueError('inventory name is too short for a distinct alias')
            aliases[key] = 'x' * (len(part.encode()) - len(tag)) + tag
            foreign.add(part)
        return aliases[key]

    def alias_path(path):
        parts = path.split('/')
        return '/'.join(alias(tuple(parts[:depth]), part) for depth, part in enumerate(parts))

    for inventory in (before, after):
        for entry in inventory['entries']:
            if entry['kind'] == 'directory-digest':
                # Every name inside a digested subtree is aliased; the digests stay (metadata listings).
                renamed = {alias_path(path): digest for path, digest in entry['directory_sha256'].items()}
                if len(renamed) != len(entry['directory_sha256']):
                    raise ValueError('inventory alias merged two directories')
                entry['directory_sha256'] = renamed
            elif entry['relative_path'] not in kept:
                entry['relative_path'] = alias_path(entry['relative_path'])
                for key in _BLANKED:
                    entry[key] = _blank(entry[key])
            elif entry['kind'] == 'file':
                # A file that keeps its name (the store, a log): its size and its inode are the operator's.
                # They are blanked here too, so no public document states them; the times still show the change.
                for key in ('size_bytes', 'inode'):
                    entry[key] = _blank(entry[key])
            if entry['kind'] == 'symlink':
                entry['target_sha256'] = '0' * 64   # a link target is a path of the home; its hash is not published
        names = [entry['relative_path'] for entry in inventory['entries']]
        if len(set(names)) != len(names):
            raise ValueError('inventory alias merged two entries')
    # A digest inside a digested subtree is published only where it shows a change: a digest that is equal
    # before and after is zeroed on both sides. Equality, and so the classification, stays.
    late = {entry['relative_path']: entry for entry in after['entries'] if entry['kind'] == 'directory-digest'}
    for entry in before['entries']:
        other = late.get(entry['relative_path']) if entry['kind'] == 'directory-digest' else None
        if other is None:
            continue
        if entry['entries_sha256'] == other['entries_sha256']:
            entry['entries_sha256'] = other['entries_sha256'] = '0' * 64
        for path, digest in entry['directory_sha256'].items():
            if other['directory_sha256'].get(path) == digest:
                entry['directory_sha256'][path] = other['directory_sha256'][path] = '0' * 64
    # The receipt must describe the public inventories: its class lists and metadata digests are recomputed.
    classes = classify_hermes_changes(before, after, session_id=receipt['session_id'], started_ns=receipt['started_ns'], rules=receipt['rules'])
    recorded = receipt['classes']
    if any(len(classes[name]) != len(recorded[name]) for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')):
        raise ValueError('the public inventories classify differently')
    paths = {row['relative_path']: row.get('changed_paths') for row in recorded['shared_changed']}
    for row in classes['shared_changed']:
        if paths.get(row['relative_path']) is not None:
            row['changed_paths'] = sorted(alias_path(path) for path in paths[row['relative_path']])
    receipt['classes'] = {name: classes[name] for name in ('session_owned', 'run_owned', 'shared_changed', 'directories_changed')}
    old_after = receipt['after_inventory_sha256']
    receipt['before_inventory_sha256'], receipt['after_inventory_sha256'] = inventory_sha256(before), inventory_sha256(after)
    receipt['quiescence']['inventory_sha256'] = [receipt['after_inventory_sha256'] if digest == old_after else '0' * 64
                                                 for digest in receipt['quiescence']['inventory_sha256']]
    # The store copy is the operator's whole store: its size, identity and digest are not published.
    # The row totals count the rows of other sessions; no check reads them.
    for store in receipt['session_store']['stores']:
        for copied in store['copied_files']:
            copied.update(sha256='0' * 64, size_bytes=_blank(copied['size_bytes']), filesystem_id=_blank_text_number(copied['filesystem_id']))
        for table in store['tables']:
            for key in ('total_rows', 'other_rows_counted_not_read'):
                table[key] = _blank(table[key])
    public = {RECEIPT: _compact(receipt), BEFORE: _compact(before), AFTER: _compact(after)}
    if any(len(public[name]) != len(data) for name, data in documents.items()):
        raise ValueError('inventory alias changed the byte length')
    verify_state_receipt(receipt, before, after)
    public_names = {part for path in kept for part in path.split('/')}
    return public, foreign, public_names


# --- all documents ---------------------------------------------------------------------------------
def home_path(original):
    """The one home directory that the capture plans name."""
    homes = set()
    for name, data in original.items():
        if name.endswith('/inputs/capture/plan.json'):
            match = re.match(r'/Users/[A-Za-z0-9._-]+(?=/)', json.loads(data)['hermes_home'])
            if match is None:
                raise ValueError('the Hermes home is not under a home directory')
            homes.add(match.group(0))
    if len(homes) != 1:
        raise ValueError('the packets do not name one home directory')
    return homes.pop()


def encodings(value):
    """Byte forms in which a short private text value could hide."""
    raw = value.encode()
    forms = {raw.hex().encode(), raw.hex().upper().encode(), value.encode('utf-16-le'), value.encode('utf-16-be')}
    for shift in range(3):
        # Base64 at each of the three alignments, without the characters that depend on the neighbours.
        coded = base64.b64encode(b'\x00' * shift + raw)
        forms.add(coded[(0, 2, 3)[shift]:len(coded) - (0, 4, 4)[(shift + len(raw)) % 3]])
    return {raw} | {form for form in forms if len(form) >= 6}


def sanitize(original):
    """Return (transformed documents, counts, private byte values that must not be public, blanked instruction texts)."""
    home = home_path(original)
    name = home[len('/Users/'):]
    runs = sorted({path.split('/', 1)[0] for path in original})
    if any(_EMAIL.search(data) for data in original.values()):
        raise ValueError('an account e-mail needs an alias rule')
    replacements = [(home.encode(), b'/Users/' + b'x' * len(name)), (name.encode(), b'x' * len(name))]
    counts = {'home_alias_count': 1, 'digests_zeroed': 0, 'digests_rebound': 0, 'encrypted_payloads_blanked': 0, 'instruction_texts_blanked': 0,
              'system_prompt_rows_blanked': 0}
    base, blanked_texts, foreign, kept_names, prompts, old_hashes = {}, [], set(), set(), {}, set()
    for path, data in original.items():
        if path.endswith('/native/capture/' + PROMPT_FILE):
            prompts[path], old, new = sanitize_prompt_row(data, blanked_texts)
            old_hashes.add(old.encode())
            counts['system_prompt_rows_blanked'] += 1
            prompts[path.split('/', 1)[0]] = {old: new}
    for path, data in original.items():
        if path in prompts:
            data = prompts[path]
        if path.endswith('/native/capture/' + ROWS_FILE):
            data, found = sanitize_rows(data, blanked_texts, prompts.get(path.split('/', 1)[0]))
            for key, value in found.items():
                counts[key] += value
        for old, alias in replacements:
            data = data.replace(old, alias)
        base[path] = data
    for run in runs:
        triple = {key: base[f'{run}/inputs/capture/{key}'] for key in (RECEIPT, BEFORE, AFTER)}
        public, names, kept = alias_inventories(triple)
        foreign |= names
        kept_names |= kept
        base.update({f'{run}/inputs/capture/{key}': data for key, data in public.items()})
    # A digest of a changed file is replaced by the digest of its public bytes, until nothing changes.
    transformed = dict(base)
    for _ in range(30):
        digests = {run: {sha(original[path]).encode(): sha(data).encode() for path, data in transformed.items()
                         if path.startswith(run + '/') and original[path] != data} for run in runs}
        revised = {}
        for path, data in base.items():
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
    forbidden = sorted(encodings(name) | old_hashes)
    everything = b'\n'.join(transformed.values())
    if any(value in everything for value in forbidden):
        raise ValueError('the home user name or a digest of the private system prompt survived')
    # An aliased name must be gone from the inventories and receipts. The session ids of the captured runs are public.
    # A name that one run keeps (a log that changed) is an old entry in another run. It is public.
    foreign -= kept_names
    captured = {json.loads(original[f'{run}/inputs/capture/{RECEIPT}'])['session_id'] for run in runs}
    public_parts = set()
    for path, data in transformed.items():
        if not path.endswith((RECEIPT, BEFORE, AFTER)):
            continue
        document = json.loads(data)
        listed = [entry['relative_path'] for entry in document.get('entries', [])]
        listed += [name for entry in document.get('entries', []) for name in entry.get('directory_sha256', {})]
        for row in document.get('classes', {}).get('shared_changed', []):
            listed += [row['relative_path'], *row.get('changed_directories', []), *row.get('changed_paths', [])]
        public_parts.update(part for name in listed for part in name.split('/'))
    survivors = [item for item in foreign & public_parts if len(item) >= 8 and not any(session in item for session in captured)]
    if survivors:
        raise ValueError(f'{len(survivors)} name(s) of other entries of the Hermes home survived')
    other_sessions = {match for item in foreign for match in re.findall(r'\d{8}_\d{6}_[0-9a-f]{6}', item)} - captured
    if any(session.encode() in everything for session in other_sessions):
        raise ValueError('a session id of another session survived')
    counts['foreign_inventory_names_aliased'] = len(foreign)
    counts['instruction_phrases_checked'] = require_no_instruction_phrase(transformed, instruction_phrases(blanked_texts))
    return transformed, counts, forbidden, blanked_texts


def private_markers(data, forbidden=()):
    """Names of the private markers found in public bytes."""
    home = Path.home()
    found = []
    if str(home).encode() in data or home.name.encode() in data: found.append('home user name')
    if re.search(rb'/Users/(?!x+[/:"\\`\'.)\s])[A-Za-z0-9_-]+', data): found.append('home path')
    if _SECRET.search(data): found.append('credential')
    if _EMAIL.search(data): found.append('e-mail')
    if any(value and value in data for value in forbidden): found.append('home user name in an encoding or a digest of private text')
    return found


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
        with tempfile.TemporaryDirectory(prefix='hermes-public-alias-') as folder:
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
    # No short hex value under a digest-like key may remain unless public bytes yield it.
    warnings = short_digest_warnings(public_set)
    if warnings:
        raise ValueError(f'{len(warnings)} short digest value(s) are not bound to public bytes: ' + ', '.join(sorted({row['key'] for row in warnings})))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    digests = check_public_packets([output / run for run in runs], receipts=receipts, extra_files=[output / 'public-inputs-candidate.json'], allowlist=DIGEST_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'aliases': counts, 'digest_classes': digests['classes'],
              'public_bundle_sha256': bundle['sha256'], 'public_safe': False, 'independent_reproduction': False}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
