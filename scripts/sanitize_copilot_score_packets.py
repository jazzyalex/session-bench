#!/usr/bin/env python3
"""Produce explicitly transformed public Copilot CLI candidates, preserving metric values.

The three captures ran with a new empty HOME and COPILOT_HOME. The native
session directory holds no home user name, e-mail, GitHub login or token. It
does hold the per-user macOS temporary directory id (``/var/folders/<xx>/<id>``)
in paths, also hex-encoded in the rewind snapshot index. The launch receipts
hold the operator's ``PATH`` with the home user name.

Changed, always at identical byte length:

- the home user name and any account e-mail (shared alias rule);
- the temporary directory id, in plain and hex-encoded form, in every file,
  including the pages of the session store (``sessions.cwd``,
  ``session_files.file_path`` and their index entries);
- the key of each file under ``files`` in ``rewind-file-snapshots/index.json``
  (native copy and capture copy). The vendor derives it as the first 32 hex
  characters of SHA-256 of the file path, so it was a hash of a private path
  (with the temporary directory id). It becomes the first 32 hex characters of
  SHA-256 of the public aliased ``path`` stored beside it, at equal length, in
  every file where the old key stands;
- the 12-character hashes of the request record (``session.usage_checkpoint`` ``promptCacheBreakState``) that depend on
  per-run private text: the ``hash`` of the ``environment_context`` and ``workspace_context`` system prompt segments
  (their text holds the temporary working directory and session folder) and every ``hash`` of ``conversation.points``
  (a hash of the messages sent so far). They become 12 zeros in every file and copy where the value stands (event log,
  raw stream copies, capture copies). The hashes of the other segments and of the tool schemas are the same in every
  packet and are kept; no metric reads any of them;
- the ``PATH`` value of the launch receipts;
- in the root, launch and exit receipts: every digest of a file that is not in
  the packet, not an empty file and not part of the vendor package, and every
  digest-like path component of such a file. Those files (configuration, user
  cache, device id, logs, the shared session store) stay private, and a digest
  of a small private file can confirm a guessed account name.

The vendor system prompt in ``system.message`` (``content`` and every content
block) is blanked at equal byte length, in the event log and in every other
file of the packet. The raw stream copies (``rN.stdout``) hold the server
instructions of ``session.mcp_servers_loaded``; they are blanked too. A final
guard fails when any public file still holds a phrase of the blanked text. One line of it is kept: the operating
system line, which the public inputs builder reads.

Kept on purpose: every native record and key; tool call ids (they are the join keys); per-call provider
handles (``apiCallId``, ``model_call_id``, ``request_id``, ``github_request_id``,
``interactionId``) and the encrypted reasoning blobs. Those are opaque per-call
values. A reader cannot map them to an account.

The session store is a WAL-mode SQLite database and its rows live in the WAL.
Every WAL frame carries a running checksum over its page bytes. After the
equal-length change the script recomputes the checksums of exactly the frames
that were valid before, so a normal SQLite reader accepts the same frames. It
then opens a copy of the public store and requires a clean integrity check, the
same number of valid frames and the same rows (with the alias applied) as the
private store. A reader of the public packet can verify the public bytes, the
checksums and the rows. A reader cannot verify the private checksums or that
the private WAL held nothing else; the private digests stay in the sidecar.

Dependent digest references are updated transitively across the three packets.
The 31 metric rows must be equal before and after. The public packet carries no
hash of a private original; the parent pins stay in a private sidecar.
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
from sanitize_claude_score_packets import transform_documents, transformation_receipts
from build_copilot_public_inputs import build as build_public_inputs
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets, read_public_set
from session_bench.copilot_session_store import (
    STORE_DB, STORE_WAL, read_session_store, rewrite_wal_checksums, store_row_proof, wal_valid_frames,
)
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('copilot-2026-10-04-01', 'copilot-2026-10-04-02', 'copilot-2026-10-04-03')
# Digests that are not digests of bytes of the set, each with its reason.
_VENDOR_FILE = ('digests of files of the vendor package download (Library/Caches/copilot/pkg): the public vendor distribution, no operator data; '
                'this sanitizer zeroes every other digest of the inventory')
_CONTROLLER = 'digest of the capture controller source at capture time; repository code, no operator data'
DIGEST_ALLOWLIST = {
    'r2.launch.json': {'isolated_home_inventory_before': _VENDOR_FILE},
    'root-end.json': {'home_inventory_after': _VENDOR_FILE},
    'runtime/session_bench/copilot_score_inputs.py': {'*': _CONTROLLER},
}
_NOTICE = b'[personal text removed at equal byte length]'
_TEMP_ID = re.compile(rb'/var/folders/([A-Za-z0-9_+]{2})/([A-Za-z0-9_+]{16,})(?=/)')
_SHA = re.compile(r'[0-9a-f]{64}')
_EMPTY = hashlib.sha256(b'').hexdigest()
_VENDOR_PACKAGE = 'Library/Caches/copilot/pkg/'
_SECRET = re.compile(rb'(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@(?:gmail|googlemail|icloud|me|outlook|hotmail|live|yahoo|proton|protonmail|pm)\.(?:com|me)\b')
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal UTF-8 byte length: the home user name is aliased; the '
               'per-user macOS temporary directory id is aliased in plain and hex-encoded form; each file key of rewind-file-snapshots/index.json (the first 32 hex characters of SHA-256 of the private path) is replaced by the first 32 hex characters of SHA-256 of the public aliased path stored beside it; the PATH value of the launch '
               'receipts is blanked; the 12-character hashes of the environment_context and workspace_context prompt segments and of every conversation point of the request record are zeroed; the vendor system prompt of system.message is blanked except its operating-system line, and the server instructions of session.mcp_servers_loaded in the raw stream copies are blanked; in the root, launch and exit receipts every digest of a file outside the packet (other than '
               'an empty file or a vendor package file) is zeroed, with its digest-like path component. Every native record and '
               'key is retained. The temporary directory id inside the session store pages is aliased the same way and the WAL frame '
               'checksums are recomputed, so SQLite reads the same frames and rows. Transitive digest references are updated across '
               'the three packets.')


def sha(data): return hashlib.sha256(data).hexdigest()


def is_receipt(name):
    """True for the root, launch and exit receipts of a capture."""
    return name.endswith(('/root-start.json', '/root-end.json', '.launch.json', '.exit.json'))


def temp_aliases(documents):
    """Equal-length byte aliases for every temporary directory id, plain and hex-encoded."""
    aliases = {}
    for data in documents.values():
        for match in _TEMP_ID.finditer(data):
            old = match.group(0)
            new = b'/var/folders/' + b'x' * len(match.group(1)) + b'/' + b'x' * len(match.group(2))
            aliases[old] = new
            aliases[old.hex().encode()] = new.hex().encode()
            aliases[old.hex().upper().encode()] = new.hex().upper().encode()
    return aliases


def rewind_rekeys(documents, temps):
    """{old key: new key} for the file keys of every rewind snapshot index.

    The new key is the first 32 hex characters of SHA-256 of the public (aliased) ``path`` of the same record.
    """
    keys = {}
    for name, data in documents.items():
        if not name.endswith('rewind-file-snapshots/index.json'):
            continue
        for snapshot in json.loads(data).get('snapshots', []):
            for old, record in snapshot.get('files', {}).items():
                path = record['path'].encode()
                for before, after in temps.items():
                    path = path.replace(before, after)
                new = sha(path)[:32]
                if keys.setdefault(old, new) != new:
                    raise ValueError('one rewind key stands for two public paths')
    return keys


def _inventories(value):
    """Every {path: {'sha256': digest}} inventory inside one parsed receipt."""
    if isinstance(value, dict):
        if value and all(isinstance(row, dict) and set(row) == {'sha256'} for row in value.values()):
            yield value
            return
        for item in value.values():
            yield from _inventories(item)
    elif isinstance(value, list):
        for item in value:
            yield from _inventories(item)


def private_inventory_tokens(documents):
    """Digests and digest-like path components that describe files outside the packets.

    A digest is kept when one packet file has it, when it is the digest of an
    empty file, or when it belongs to a vendor package file. Every other digest
    in a receipt inventory describes a private file and is returned.
    """
    packet = {sha(data) for data in documents.values()} | {_EMPTY}
    seen, kept, names = set(), set(packet), set()
    for name, data in documents.items():
        if not is_receipt(name):
            continue
        for inventory in _inventories(json.loads(data)):
            for path, row in inventory.items():
                seen.add(row['sha256'])
                if path.startswith(_VENDOR_PACKAGE):
                    kept.add(row['sha256'])
                elif row['sha256'] not in packet:
                    names.update(part for part in re.split(r'[/.]', path) if _SHA.fullmatch(part))
    return sorted(seen - kept), sorted(names - packet)


def blank_launch_path(data):
    """Blank the PATH value of one launch receipt at equal byte length."""
    value = json.loads(data).get('safe_environment', {}).get('PATH')
    if not isinstance(value, str) or not value:
        return data, 0
    token = b'"PATH": ' + json.dumps(value).encode()
    if data.count(token) != 1:
        raise ValueError('cannot locate the PATH value of a launch receipt')
    size = len(token) - len(b'"PATH": ""')
    return data.replace(token, b'"PATH": "' + (_NOTICE + b'x' * size)[:size] + b'"'), 1


VENDOR_NOTICE = b'[vendor instruction text removed at equal byte length]'
from sanitize_codex_score_packets import instruction_phrases, require_no_instruction_phrase  # noqa: E402


def _vendor_rule(record, path, is_key):
    """True for vendor instruction text of an event, in the event log or in a raw stream copy.

    ``system.message`` content and its content blocks (the system prompt), and
    the ``instructions`` of a server in ``session.mcp_servers_loaded``.
    """
    if is_key:
        return False
    if record.get('type') == 'system.message':
        return path == ('data', 'content') or (len(path) == 4 and path[:2] == ('data', 'contentBlocks') and path[3] == 'content')
    return len(path) >= 3 and path[0] == 'data' and path[-2:] == ('serverMetadata', 'instructions')


# The public inputs builder reads the operating system of the capture from this
# line of the system prompt. It is the one fragment of the prompt that is kept.
KEPT_SYSTEM_FRAGMENT = b'* Operating System: macos\\n'


def redact_copilot_events(data, originals=None):
    """Blank the vendor instruction text of an event file at equal length; return it and the count.

    The file is ``events.jsonl`` or a raw stream copy (``rN.stdout``). Every
    byte of ``system.message`` ``content`` and of each content block is
    replaced, except the operating-system line of ``content``; so are the
    server instructions of ``session.mcp_servers_loaded``.
    """
    from sanitize_codex_score_packets import _string_paths, _string_tokens
    lines, count = [], 0
    for line in data.split(b'\n'):
        if line.strip() and (b'"system.message"' in line or b'"instructions"' in line):
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if not isinstance(record, dict):
                lines.append(line)
                continue
            tokens, paths = _string_tokens(line), list(_string_paths(record))
            if len(tokens) != len(paths) or any(token[2] != item[1] for token, item in zip(tokens, paths)):
                raise ValueError('cannot align the JSON strings of an event with its parsed record')
            out = bytearray(line)
            for (start, end, is_key), (path, _, _text) in zip(tokens, paths):
                if start == end or not _vendor_rule(record, path, is_key):
                    continue
                out[start:end] = (VENDOR_NOTICE + b'x' * (end - start))[:end - start]
                if path == ('data', 'content'):
                    at = line.find(KEPT_SYSTEM_FRAGMENT, start, end)
                    while at >= 0:
                        out[at:at + len(KEPT_SYSTEM_FRAGMENT)] = KEPT_SYSTEM_FRAGMENT
                        at = line.find(KEPT_SYSTEM_FRAGMENT, at + 1, end)
                count += 1
                if originals is not None:
                    originals.append(_text)
            line = bytes(out)
            json.loads(line)
        lines.append(line)
    return b'\n'.join(lines), count


_PER_RUN_SEGMENTS = ('environment_context', 'workspace_context')
_SHORT_HASH = re.compile(r'[0-9a-f]{12}')


def per_run_request_hashes(documents):
    """The 12-character hashes of the request record that depend on per-run private text (see the module text)."""
    found = set()

    def walk(value, key=''):
        if isinstance(value, dict):
            hashed = value.get('hash')
            if isinstance(hashed, str) and _SHORT_HASH.fullmatch(hashed) and (value.get('segment') in _PER_RUN_SEGMENTS or key == 'points'):
                found.add(hashed)
            for name, item in value.items():
                walk(item, name)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    for name, data in documents.items():
        if not name.endswith(('/events.jsonl', '.stdout', '.stdout.jsonl')):
            continue
        for line in data.split(b'\n'):
            if b'"hash"' in line:
                try:
                    walk(json.loads(line))
                except ValueError:
                    pass
    return sorted(found)


def sanitize(original):
    """Return (transformed documents, alias counts, blanked-string counts per file)."""
    temps = temp_aliases(original)
    rekeys = rewind_rekeys(original, temps)
    digests, names = private_inventory_tokens(original)
    request_hashes = per_run_request_hashes(original)
    hash_pattern = re.compile(rb'(?<![0-9a-f])(?:' + b'|'.join(value.encode() for value in request_hashes) + rb')(?![0-9a-f])') if request_hashes else None
    blanked, blanked_texts = {}, []

    frames = {name: len(wal_valid_frames(data)[0]) for name, data in original.items() if name.endswith('/' + STORE_WAL)}

    def redact(name, data):
        for old, new in temps.items():
            data = data.replace(old, new)
        for old, new in rekeys.items():
            data = data.replace(old.encode(), new.encode())
        if hash_pattern is not None:
            data = hash_pattern.sub(b'0' * 12, data)
        if name in frames:
            data = rewrite_wal_checksums(data, frames[name])
        # The event log, its copies, and the raw stream copies of the capture.
        if name.endswith(('/events.jsonl', '.stdout', '.stdout.jsonl')):
            data, vendor = redact_copilot_events(data, blanked_texts)
            if vendor:
                blanked[name] = vendor
        if is_receipt(name):
            before = json.loads(data)
            if name.endswith('.launch.json'):
                data, blanked[name] = blank_launch_path(data)
            for token in (*digests, *names):
                data = data.replace(token.encode(), b'0' * len(token))
            after = json.loads(data)
            if [len(inventory) for inventory in _inventories(before)] != [len(inventory) for inventory in _inventories(after)]:
                raise ValueError('zeroing merged two inventory entries')
        return data

    transformed, counts = transform_documents(original, redact)
    for old in temps:
        if any(old in data for data in transformed.values()):
            raise ValueError('a temporary directory id survived')
    for token in (*digests, *names):
        if any(token.encode() in data for data in transformed.values()):
            raise ValueError('a private inventory digest survived')
    if hash_pattern is not None and any(hash_pattern.search(data) for data in transformed.values()):
        raise ValueError('a per-run request hash survived')
    for old in rekeys:
        if any(old.encode() in data for data in transformed.values()):
            raise ValueError('a rewind snapshot key derived from a private path survived')
    for name, data in transformed.items():
        if name.endswith('rewind-file-snapshots/index.json'):
            for snapshot in json.loads(data).get('snapshots', []):
                if any(key != sha(record['path'].encode())[:32] for key, record in snapshot.get('files', {}).items()):
                    raise ValueError('a rewind snapshot key is not the hash of its public path')
    # A normal SQLite reader must see the same frames and the same rows.
    for name, count in frames.items():
        database = name[:-len(STORE_WAL)] + STORE_DB
        if len(wal_valid_frames(transformed[name])[0]) != count:
            raise ValueError('the public session store WAL lost valid frames')
        before, after = read_session_store(original[database], original[name]), read_session_store(transformed[database], transformed[name])

        def rows(store, alias):
            out = []
            for table in ['sqlite_master', *sorted(store['tables'])]:
                for row in (store['schema'] if table == 'sqlite_master' else store['tables'][table]):
                    proof = store_row_proof(row)
                    for old, new in (temps.items() if alias else ()):
                        proof = proof.replace(old, new)
                    out.append((table, proof))
            return out
        if rows(before, True) != rows(after, False):
            raise ValueError('the public session store rows differ from the private rows')
    counts.update(request_hashes_zeroed=len(request_hashes))
    counts.update(session_store_wal_frames_rechecksummed=sum(frames.values()))
    counts.update(rewind_snapshot_keys_rekeyed=len(rekeys), temporary_directory_alias_count=len(temps) // 3, private_inventory_digests_zeroed=len(digests),
                  private_inventory_names_zeroed=len(names))
    counts.update(instruction_phrases_checked=require_no_instruction_phrase(transformed, instruction_phrases(blanked_texts)))
    return transformed, counts, blanked


def private_markers(data):
    """Names of the private markers found in public bytes."""
    home = Path.home()
    found = []
    if str(home).encode() in data or home.name.encode() in data: found.append('home user name')
    if re.search(rb'/Users/(?!x+[/:"\\])[A-Za-z0-9_-]+', data): found.append('home path')
    if _TEMP_ID.search(data) and any(match.group(2).strip(b'x') for match in _TEMP_ID.finditer(data)): found.append('temporary directory id')
    if _SECRET.search(data): found.append('credential')
    if _EMAIL.search(data): found.append('e-mail')
    return found


def build(source, output):
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('new destination required')
    original, pins, trees = {}, {}, {}
    receipts = []
    for run in RUNS:
        parent = source / run
        trees[run] = _snapshot_tree(parent)
        pins[run] = sha(trees[run]['manifest.json'])
        original.update({f'{run}/{name}': data for name, data in trees[run].items() if name.startswith(('native/', 'inputs/'))})
    transformed, alias_counts, blanked = sanitize(original)
    summary = []
    for run in RUNS:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        count = sum(value for name, value in blanked.items() if name.startswith(run + '/'))
        receipt, private_receipt = transformation_receipts(
            {name: original[f'{run}/{name}'] for name in files}, files, parent_manifest_sha256=pins[run],
            aliases=alias_counts, personal_text_strings_blanked=count, description=DESCRIPTION)
        with tempfile.TemporaryDirectory(prefix='copilot-public-alias-') as folder:
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
        leaks = sorted({f'{marker} in {name}' for name, data in public.items() for marker in private_markers(data)})
        leaks += [f'{marker} in receipt' for marker in private_markers(canonical(after))]
        if leaks: raise ValueError('private marker remains in the public candidate: ' + '; '.join(leaks))
        (output / f'{run}-receipt.json').write_bytes(canonical(after))
        (output / f'{run}-tamper.json').write_bytes(canonical(controls))
        receipts.append(after)
        (output / f'{run}-private-transformation.json').write_bytes(canonical(private_receipt))
        summary.append({'packet': run, 'manifest_sha256': pin, 'parent_manifest_sha256': pins[run],
                        'diagnostics_sha256': after['diagnostics_sha256'], 'all_31_metric_rows_unchanged': True,
                        'personal_text_strings_blanked': count, 'privacy_review_pending': True})
    for run in RUNS:
        if trees[run] != _snapshot_tree(source / run): raise ValueError('private parent changed')
    bundle = build_public_inputs(output, output / 'public-inputs-candidate.json')
    # No file of a public packet (runtime and bundle included) may still hold a phrase of the blanked instruction text.
    texts = []
    for name, data in original.items():
        if name.endswith(('/events.jsonl', '.stdout', '.stdout.jsonl')):
            redact_copilot_events(data, texts)
    require_no_instruction_phrase(read_public_set([output / run for run in RUNS], [output / 'public-inputs-candidate.json']), instruction_phrases(texts))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
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
