#!/usr/bin/env python3
"""Produce explicitly transformed public Codex CLI candidates, preserving metric values.

Codex rollouts embed the operator's personal agent instructions and installed
skill list. Those texts are replaced at identical UTF-8 byte length, together
with the home username. Vendor instruction text is blanked the same way, with
its own marker: the base instructions of ``session_meta``, the developer-role
instruction messages and the plugin list of the injected context message. All native records remain; dependent digest references
are updated transitively across the three packets. Original packets are
retained privately and pinned in transformation receipts.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from sanitize_claude_score_packets import transform_documents, transformation_receipts
from session_bench.native_replay import canonical
from session_bench.public_digest_check import check_public_packets, read_public_set
from session_bench.score_replay import build_score_replay_package, replay_score_package

RUNS = tuple(f'codex-cli-eval-{number}' for number in (1, 2, 3))
PERSONAL_MESSAGE_PREFIXES = ('<skills_instructions>', '# AGENTS.md instructions')
_NOTICE = b'[personal text removed at equal byte length]'
# Vendor instruction text (system prompt, injected instruction messages, tool
# and plugin descriptions). No metric reads it. It is blanked in the public
# packet so that every row is treated the same.
VENDOR_NOTICE = b'[vendor instruction text removed at equal byte length]'


def sha(data): return hashlib.sha256(data).hexdigest()


def _string_tokens(line):
    """(inner start, inner end, is key) of every JSON string in one line, in order."""
    tokens, index = [], 0
    while index < len(line):
        if line[index] != 0x22:
            index += 1
            continue
        end = index + 1
        while line[end] != 0x22:
            end += 2 if line[end] == 0x5C else 1
        after = end + 1
        while after < len(line) and line[after] in b' \t\r\n':
            after += 1
        tokens.append((index + 1, end, after < len(line) and line[after] == 0x3A))
        index = end + 1
    return tokens


def _string_paths(value, path=()):
    """(path, is key, text) of every string of a parsed record, in document order."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield path + (key,), True, key
            yield from _string_paths(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_paths(item, path + (index,))
    elif isinstance(value, str):
        yield path, False, value


def _rule(record, path, text):
    """'all' blanks a string, 'body' keeps its first marker line, None keeps it."""
    kind = record.get('type')
    if kind == 'world_state' and path[:2] == ('payload', 'state'):
        # Agent instructions, the skill list, approved commands and the rest of
        # this opaque host snapshot; no metric reads it.
        return 'all'
    payload = record.get('payload')
    if (kind == 'response_item' and isinstance(payload, dict) and payload.get('type') == 'message'
            and len(path) == 4 and path[:2] == ('payload', 'content') and path[3] == 'text'
            and text.startswith(PERSONAL_MESSAGE_PREFIXES)):
        return 'body'
    return None


def instruction_phrases(texts, width=48, step=31):
    """Distinctive prose windows of the strings a sanitizer blanked.

    A window is kept when it is plain prose: at least five words, no path
    separator, quote, backslash or control character. A path or an id is a run
    fact that other files of a packet state on purpose.
    """
    phrases = set()
    for text in texts:
        if not isinstance(text, str) or len(text) < width or text.startswith(('[vendor instruction text removed', '[personal text removed', '[redacted unscored context]')):
            continue
        for start in range(0, len(text) - width + 1, step):
            window = text[start:start + width]
            if (window.isprintable() and not any(char in window for char in '/"\\<>{}') and window.strip() == window
                    and len(re.findall(r'[A-Za-z]{3,}', window)) >= 5 and len(set(window.lower())) >= 12):
                phrases.add(window)
    return sorted(phrases)


def _decoded_strings(value, depth=0):
    """Every string of a parsed JSON value, also inside a string that holds JSON."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _decoded_strings(item, depth)
    elif isinstance(value, list):
        for item in value:
            yield from _decoded_strings(item, depth)
    elif isinstance(value, str):
        yield value
        start = 0 if value[:1] in '{[' else value.find('{')
        if depth < 4 and start >= 0 and len(value) > 40:
            try:
                yield from _decoded_strings(json.loads(value[start:]), depth + 1)
            except ValueError:
                pass


def require_no_instruction_phrase(contents, phrases):
    """Fail when any file of a public packet still holds a phrase of the blanked instruction text.

    ``contents`` maps file names to bytes: every file of the packets (native,
    stream copies, receipts, caches, observers, runtime). Each file is searched
    as text and, when it is JSON or JSON lines, in its decoded strings.
    """
    if not phrases:
        return 0
    pattern = re.compile('|'.join(re.escape(phrase) for phrase in phrases))
    leaks = []
    for name in sorted(contents):
        data = contents[name]
        if name.endswith(('.zst', '.zstd')):
            import compression.zstd as zstd
            data = zstd.decompress(data)
        views = [data.decode('utf-8', 'replace')]
        documents = []
        try:
            documents = [json.loads(data)]
        except ValueError:
            for line in data.split(b'\n'):
                if line[:1] in (b'{', b'['):
                    try:
                        documents.append(json.loads(line))
                    except ValueError:
                        pass
        views.append('\n'.join(text for document in documents for text in _decoded_strings(document)))
        if any(pattern.search(view) for view in views):
            leaks.append(name)
    if leaks:
        raise ValueError(f'blanked instruction text is still in {len(leaks)} public file(s): ' + ', '.join(leaks[:8]))
    return len(phrases)


def _vendor_rule(record, path, text):
    """True when a string is vendor instruction text: the base instructions and the injected instruction messages."""
    kind, payload = record.get('type'), record.get('payload')
    if not isinstance(payload, dict):
        return False
    if kind == 'session_meta':
        return path == ('payload', 'base_instructions', 'text')
    if kind == 'response_item' and payload.get('type') == 'message' and len(path) == 4 and path[:2] == ('payload', 'content') and path[3] == 'text':
        if payload.get('role') == 'developer':
            return True
        # A user-role message that Codex marks as context: the plugin list is
        # vendor text. The run environment (cwd, shell, date) is not instruction text.
        meta = payload.get('internal_chat_message_metadata_passthrough')
        kinds = meta.get('content_item_kinds') if isinstance(meta, dict) else None
        if payload.get('role') == 'user' and isinstance(kinds, list) and kinds and 'user.text' not in kinds:
            item = kinds[path[2]] if path[2] < len(kinds) and isinstance(kinds[path[2]], str) else ''
            return not item.startswith('environments.') and not text.startswith('<environment_context>')
    return False


def redact_codex_rollout(data, originals=None):
    """Return the rollout with personal and vendor instruction strings blanked in place, and how many were blanked."""
    lines, count = [], 0
    for line in data.split(b'\n'):
        try:
            record = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            record = None
        if not isinstance(record, dict):
            lines.append(line)
            continue
        tokens, paths = _string_tokens(line), list(_string_paths(record))
        if len(tokens) != len(paths) or any(token[2] != item[1] for token, item in zip(tokens, paths)):
            raise ValueError('cannot align the JSON strings of a rollout line with its parsed record')
        out = bytearray(line)
        for (start, end, is_key), (path, _, text) in zip(tokens, paths):
            rule = None if is_key or start == end else _rule(record, path, text)
            notice = _NOTICE
            if rule is None and not is_key and start != end and _vendor_rule(record, path, text):
                rule, notice = 'all', VENDOR_NOTICE
            if rule is None:
                continue
            cut = line.find(b'\\n', start, end) if rule == 'body' else -1
            keep = cut + 2 if cut >= 0 else start
            out[keep:end] = (notice + b'x' * (end - keep))[:end - keep]
            count += 1
            if originals is not None:
                originals.append(text)
        lines.append(bytes(out))
    return b'\n'.join(lines), count


# Receipt values carried from the capture. Each is the digest of a capture-time
# document (decoded facts, measurement, an earlier sanitized form) that is not
# in the packet and that holds the private rollout digest or the home name.
# They are zeroed at equal length. No verifier reads them.
CARRIED_RECEIPT_DIGESTS = ('intact_decoded_sha256', 'offline_decoded_sha256', 'intact_measurement_sha256',
                           'offline_measurement_sha256', 'damaged_measurement_sha256')
CARRIED_RECEIPT_FILES = ('/inputs/capture-assertion.json', '/inputs/original-capture-receipt.json', '/calibration-receipt.json')
# No digest is allowlisted. The ``current_root_metadata.inventory_metadata_sha256`` of the normal-root receipts is a digest over the
# paths, sizes and times of every file of the operator's normal Codex root. Equal in every receipt and every set, it is a
# stable fingerprint of private machine state, so it is zeroed (``private_root_inventory_digests``). The closed replay
# (``codex_cli_root_evidence``) does not read it.
DIGEST_ALLOWLIST = {}


def private_root_inventory_digests(documents):
    """The inventory digests (normal root, selected file) of every normal-root receipt of the capture."""
    found = set()
    for name, data in documents.items():
        if name.endswith('/normal-root-receipt.json'):
            # The selected-file digest covers the device, inode and times that are zeroed below: kept, it would confirm a guess.
            for key in ('current_root_metadata', 'current_selected_metadata'):
                digest = json.loads(data).get(key, {}).get('inventory_metadata_sha256')
                if not isinstance(digest, str) or len(digest) != 64:
                    raise ValueError('cannot locate the inventory digest of a normal-root receipt')
                found.add(digest)
    return found


# Plain machine identifiers of the operator's normal Codex root and of the selected rollout file, in the normal-root receipts.
# The closed replay (``codex_cli_root_evidence``) reads none of them: it reads ``current_selected_metadata`` (the selected file),
# ``historical_proof`` and ``quiescence.observed[*][0].filesystem_id`` (which must equal ``historical_proof.selected_filesystem_id``)
# and ``size_bytes``, and tests ``observed[0] == observed[1]``, which a transform that is equal in both entries keeps. So these
# are replaced by the number 0, padded with spaces to the byte length of the original number (valid JSON, same type, same length).
ROOT_METADATA_NUMBERS = ('file_count', 'total_size_bytes')
QUIESCENCE_NUMBERS = ('device', 'inode', 'birth_ns')


def _zero_numbers(text, keys):
    for key in keys:
        text = re.sub(r'("%s"\s*:\s*)(\d+)' % key, lambda m: m.group(1) + '0' + ' ' * (len(m.group(2)) - 1), text)
    return text


def zero_machine_identifiers(name, data):
    """Zero, at equal length, the root counts and the file identifiers of a normal-root receipt that no verifier reads."""
    if not name.endswith('/normal-root-receipt.json'):
        return data
    text = data.decode()
    value = json.loads(text)
    decoder = json.JSONDecoder()
    spans = []
    for key, keys in (('current_root_metadata', ROOT_METADATA_NUMBERS), ('quiescence', QUIESCENCE_NUMBERS)):
        match = re.search(r'"%s"\s*:\s*' % key, text)
        if match is None or not isinstance(value.get(key), dict):
            raise ValueError(f'cannot locate {key} in a normal-root receipt')
        _, end = decoder.raw_decode(text, match.end())
        spans.append((match.end(), end, keys))
    for start, end, keys in sorted(spans, reverse=True):
        text = text[:start] + _zero_numbers(text[start:end], keys) + text[end:]
    out = text.encode()
    result = json.loads(out)
    quiet = [item for group in result['quiescence']['observed'] for item in group]
    if (len(out) != len(data) or result['current_root_metadata']['file_count'] != 0 or result['current_root_metadata']['total_size_bytes'] != 0
            or any(item[key] != 0 for item in quiet for key in QUIESCENCE_NUMBERS)
            or result['historical_proof'] != value['historical_proof'] or result['current_selected_metadata'] != value['current_selected_metadata']
            or [item['filesystem_id'] for item in quiet] != [item['filesystem_id'] for group in value['quiescence']['observed'] for item in group]):
        raise ValueError('machine identifier transform changed more than the unread numbers')
    return out


def zero_carried_receipt_digests(name, data):
    """Zero, at equal length, the carried receipt digests whose preimage is not a packet file."""
    if not name.endswith(CARRIED_RECEIPT_FILES):
        return data
    value = json.loads(data)
    digests = [value.get(field) for field in CARRIED_RECEIPT_DIGESTS]
    sanitization = value.get('public_sanitization')
    digests.append(sanitization.get('sanitized_sha256') if isinstance(sanitization, dict) else None)
    for digest in {item for item in digests if isinstance(item, str) and len(item) == 64 and set(item) != {'0'}}:
        data = data.replace(digest.encode(), b'0' * 64)
    return data


def build(source, output):
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError('new destination required')
    original, pins = {}, {}
    for run in RUNS:
        parent = source / run
        pins[run] = sha((parent / 'manifest.json').read_bytes())
        for folder in ('native', 'inputs'):
            for path in (parent / folder).rglob('*'):
                if path.is_file(): original[f'{run}/{path.relative_to(parent).as_posix()}'] = path.read_bytes()
    redactions, blanked_texts = {}, []
    root_digests = private_root_inventory_digests(original)
    def redact(name, data):
        data = zero_carried_receipt_digests(name, data)
        data = zero_machine_identifiers(name, data)
        for digest in root_digests:
            data = data.replace(digest.encode(), b'0' * 64)
        if name.endswith('/inputs/capture-assertion.json'):
            # observer.sha256 is a digest over the private stdout digests. A
            # reader could rebuild it with a guessed home name, so it is zeroed.
            value = json.loads(data)
            digest = value.get('observer', {}).get('sha256')
            if not isinstance(digest, str) or data.count(digest.encode()) != 1:
                raise ValueError('cannot locate the private observer digest in the capture assertion')
            return data.replace(digest.encode(), b'0' * len(digest))
        if '/native/' not in name or not name.endswith('.jsonl'): return data
        data, redactions[name] = redact_codex_rollout(data, blanked_texts)
        return data
    transformed, alias_counts = transform_documents(original, redact)
    if any(digest.encode() in data for digest in root_digests for data in transformed.values()):
        raise ValueError('a private root inventory digest survived')
    for name, data in original.items():
        if name.endswith('/normal-root-receipt.json'):
            for secret in (json.loads(data)['current_root_metadata']['total_size_bytes'], json.loads(data)['quiescence']['observed'][0][0]['birth_ns']):
                if any(str(secret).encode() in kept for kept in transformed.values()):
                    raise ValueError('a private root size or file birth time survived')
    summary, receipts = [], []
    for run in RUNS:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        blanked = sum(count for name, count in redactions.items() if name.startswith(run + '/'))
        receipt, private_receipt = transformation_receipts(
            {name: original[f'{run}/{name}'] for name in files}, files, parent_manifest_sha256=pins[run],
            aliases=alias_counts, personal_text_strings_blanked=blanked,
            description='Explicit sanitized derivative, not raw capture. Personal agent instructions, the installed skill list and the rest of the host world-state snapshot are blanked, vendor instruction text (base instructions, developer-role instruction messages, plugin list) is blanked, the home username is aliased, and, in each normal-root receipt, the inventory digest of the operator normal Codex root (current_root_metadata.inventory_metadata_sha256) and the one of the selected file (current_selected_metadata.inventory_metadata_sha256, which covers the zeroed device, inode and times), the root file_count and total_size_bytes, and the device, inode and birth_ns of the selected rollout file in the quiescence records are zeroed (a number becomes 0 padded with spaces; the filesystem_id, read by the replay, stays), all at equal UTF-8 byte length; every native record is retained. Transitive digest references are updated across the three packets.')
        with tempfile.TemporaryDirectory(prefix='codex-public-alias-') as folder:
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
        (output / f'{run}-receipt.json').write_bytes(canonical(after))
        receipts.append(after)
        (output / f'{run}-private-transformation.json').write_bytes(canonical(private_receipt))
        summary.append({'packet': run, 'manifest_sha256': pin, 'parent_manifest_sha256': pins[run],
                        'diagnostics_sha256': after['diagnostics_sha256'], 'all_31_metric_rows_unchanged': True,
                        'personal_text_strings_blanked': blanked, 'privacy_review_pending': True})
    # No file of a public packet may still hold a phrase of the instruction text blanked in the rollouts.
    require_no_instruction_phrase(read_public_set([output / run for run in RUNS]), instruction_phrases(blanked_texts))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    digests = check_public_packets([output / run for run in RUNS], receipts=receipts, allowlist=DIGEST_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'public_safe': False, 'independent_reproduction': False,
              'public_digest_classes': digests['classes']}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
