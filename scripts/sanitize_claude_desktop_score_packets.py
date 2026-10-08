#!/usr/bin/env python3
"""Produce explicitly transformed public Claude Desktop candidates, preserving metric values.

A Claude Desktop transcript embeds the operator's context: instruction files,
the installed skill list, connector instructions, hook output, the account
email and the system prompt snapshot. The Desktop metadata file embeds the
connector configuration. Those strings are replaced at identical UTF-8 byte
length, together with the home username. Keys of the connector configuration
(connector tool toggles and connector tool schemas) are replaced by distinct
aliases of the same byte length. The remote-control (bridge) session
identifier gets one alias in every file that carries it. All native records
remain, and every JSON document keeps its shape; dependent digest references
are updated transitively across the three packets. Original packets are
retained privately and pinned in transformation receipts.

Kept on purpose: vendor-defined field names, including the tool schemas of the
vendor's built-in tools in the transcript ``prompt_snapshot`` attachment (their
values are blanked; they name no operator connector).
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
from sanitize_codex_score_packets import _string_paths, _string_tokens
from session_bench.native_replay import canonical
from session_bench.public_digest_check import check_public_packets
from session_bench.score_replay import build_score_replay_package, replay_score_package

RUNS = ('claude-desktop-eval-1-correction-1', 'claude-desktop-eval-2-correction-1', 'claude-desktop-eval-3')
# Digests that are not digests of public bytes, each with the reason why its
# preimage cannot be rebuilt from public bytes plus a guessed name or e-mail.
_DAMAGED = ('digest of the decode manifest of the damaged private package; that manifest holds the digest of the private transcript, '
            'which holds thousands of blanked strings of operator context (instruction files, skill list, connector text)')
DIGEST_ALLOWLIST = {
    'gui-observer.json': {'screenshot.sha256': 'digest of a private screenshot image of the desktop window; image bytes are not published and cannot be rebuilt'},
    'before-inventory.json': {'metadata_digest': 'digest over the metadata inventory (paths, sizes, times) of every file of the operator normal roots; thousands of private entries that are not published'},
    'after-inventory.json': {'metadata_digest': 'digest over the metadata inventory (paths, sizes, times) of every file of the operator normal roots; thousands of private entries that are not published'},
    'capture-assertion.json': {'packages.damage_r2.decode_manifest_sha256': _DAMAGED},
    'native-manifest-qualified.json': {'packages.damage_r2.decode_manifest_sha256': _DAMAGED},
}
_NOTICE = b'[personal text removed at equal byte length]'
# Top-level Desktop metadata fields whose nested strings are run identity.
_METADATA_KEPT = ('bridgeSessionIds',)
# Metadata fields whose nested keys are operator-specific at every depth.
_OPERATOR_KEYS = ('enabledMcpTools', 'spawnSeed', 'alwaysAllowedReasons', 'sessionPermissionUpdates')
# Vendor-defined fields of one remote connector entry. Every other key of the
# connector configuration (tool objects and their input schemas) is blanked.
_CONNECTOR_FIELDS = ('uuid', 'name', 'url', 'tools')
_KEY_ALPHABET = b'0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
_BRIDGE_ID = re.compile(rb'(?<![A-Za-z0-9_])(?:cse|session)_(?=[A-Za-z]*[0-9])([A-Za-z0-9]{24})(?![A-Za-z0-9_])')


def sha(data): return hashlib.sha256(data).hexdigest()


def _transcript_rule(record, path, is_key):
    """True when one string of a transcript record is operator context."""
    if is_key:
        return False
    kind = record.get('type')
    if kind == 'attachment':
        # Instruction files, skills, connector and hook text, environment and
        # the prompt snapshot. The attachment kind itself stays readable.
        return path[0] == 'rendered' or (path[0] == 'attachment' and path != ('attachment', 'type'))
    if kind == 'system':
        return path[0] == 'hookInfos'
    if kind == 'bridge-session':
        return path in (('ownerAccountUuid',), ('ownerOrganizationUuid',))
    if kind == 'atis-latch':
        return path == ('atis',)
    return False


def _metadata_rule(record, path, is_key):
    """True when one string of the Desktop metadata is connector or prompt configuration."""
    if is_key:
        if path[0] == 'remoteMcpServersConfig':
            return len(path) >= 3 and not (len(path) == 3 and path[2] in _CONNECTOR_FIELDS)
        return len(path) >= 2 and path[0] in _OPERATOR_KEYS
    return len(path) >= 2 and path[0] not in _METADATA_KEPT


def _strict(document):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('a JSON document holds a duplicate key')
            value[key] = item
        return value
    return json.loads(document, object_pairs_hook=pairs)


def same_shape(before, after):
    """True when two parsed documents have the same structure: key counts, array lengths and value types."""
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return len(before) == len(after) and all(same_shape(old, new) for old, new in zip(before.values(), after.values()))
    if isinstance(before, list):
        return len(before) == len(after) and all(same_shape(old, new) for old, new in zip(before, after))
    return True


def _key_alias(length, used):
    """A key alias of exactly ``length`` bytes that no sibling key uses."""
    base = len(_KEY_ALPHABET)
    for number in range(len(used) + 1):
        digits = bytearray()
        while True:
            number, digit = divmod(number, base)
            digits.insert(0, _KEY_ALPHABET[digit])
            if not number:
                break
        if len(digits) > length:
            break
        alias = b'_' * (length - len(digits)) + bytes(digits)
        if alias not in used:
            return alias
    raise ValueError('a blanked key is too short for a distinct equal-length alias among its sibling keys')


def _blank(document, rule, notice=_NOTICE, originals=None):
    """Blank the selected strings of one JSON document in place; return it and the count."""
    record = _strict(document)
    if not isinstance(record, dict):
        return document, 0
    tokens, paths = _string_tokens(document), list(_string_paths(record))
    if len(tokens) != len(paths) or any(token[2] != item[1] for token, item in zip(tokens, paths)):
        raise ValueError('cannot align the JSON strings of a document with its parsed record')
    selected = [start != end and rule(record, path, is_key) for (start, end, is_key), (path, _, _text) in zip(tokens, paths)]
    # Sibling keys that stay readable; an alias must differ from them and from every other alias.
    used = {}
    for (start, end, is_key), (path, _, _text), blanked in zip(tokens, paths, selected):
        if is_key and not blanked:
            used.setdefault(path[:-1], set()).add(bytes(document[start:end]))
    out, count = bytearray(document), 0
    for (start, end, is_key), (path, _, _text), blanked in zip(tokens, paths, selected):
        if not blanked:
            continue
        if is_key:
            siblings = used.setdefault(path[:-1], set())
            filler = _key_alias(end - start, siblings)
            siblings.add(filler)
        else:
            filler = (notice + b'x' * (end - start))[:end - start]
            if originals is not None:
                originals.append(_text)
        out[start:end] = filler
        count += 1
    if len(out) != len(document) or not same_shape(record, _strict(bytes(out))):
        raise ValueError('blanking changed the shape of a JSON document')
    return bytes(out), count


def bridge_session_aliases(documents):
    """One equal-length alias for every remote-control session identifier found in the documents."""
    found = sorted({match.group(1) for data in documents.values() for match in _BRIDGE_ID.finditer(data)})
    return {identifier: b'x' * (len(identifier) - len(b'%d' % number)) + b'%d' % number for number, identifier in enumerate(found, 1)}


def alias_bridge_sessions(data, aliases):
    """Replace every bridge session identifier, with or without its ``cse_``/``session_`` prefix."""
    for identifier, alias in aliases.items():
        data = data.replace(identifier, alias)
    return data


def redact_desktop_transcript(data):
    """Return the transcript with operator context blanked in place, and how many strings were blanked."""
    lines, count = [], 0
    for line in data.split(b'\n'):
        if line.strip():
            line, blanked = _blank(line, _transcript_rule)
            count += blanked
        lines.append(line)
    return b'\n'.join(lines), count


def redact_desktop_metadata(data):
    """Return the Desktop metadata with connector and prompt configuration blanked in place, and the count."""
    return _blank(data, _metadata_rule)


def build(source, output):
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError('new destination required')
    original, pins = {}, {}
    receipts = []
    for run in RUNS:
        parent = source / run
        pins[run] = sha((parent / 'manifest.json').read_bytes())
        for folder in ('native', 'inputs'):
            for path in (parent / folder).rglob('*'):
                if path.is_file(): original[f'{run}/{path.relative_to(parent).as_posix()}'] = path.read_bytes()
    redactions = {}
    bridges = bridge_session_aliases(original)
    def redact(name, data):
        data = alias_bridge_sessions(data, bridges)
        if name.endswith('/session.jsonl'):
            data, redactions[name] = redact_desktop_transcript(data)
        elif name.endswith('/desktop/session.json'):
            data, redactions[name] = redact_desktop_metadata(data)
        return data
    transformed, alias_counts = transform_documents(original, redact)
    if any(identifier in data for data in transformed.values() for identifier in bridges):
        raise ValueError('a bridge session identifier survived')
    alias_counts['bridge_session_alias_count'] = len(bridges)
    summary = []
    for run in RUNS:
        parent = source / run
        files = {name[len(run) + 1:]: data for name, data in transformed.items() if name.startswith(run + '/')}
        blanked = sum(count for name, count in redactions.items() if name.startswith(run + '/'))
        receipt, private_receipt = transformation_receipts(
            {name: original[f'{run}/{name}'] for name in files}, files, parent_manifest_sha256=pins[run],
            aliases=alias_counts, personal_text_strings_blanked=blanked,
            description='Explicit sanitized derivative, not raw capture. Operator context in the transcript (instruction files, skill list, connector and hook text, environment, prompt snapshot, account identifiers) and connector configuration in the Desktop metadata are blanked, and the home username, the account email and the remote-control (bridge) session identifier are aliased, all at equal UTF-8 byte length. Keys of the connector configuration in the Desktop metadata (tool toggles, tool objects and tool input schemas) are replaced by distinct aliases of equal byte length. Every native record is retained and every JSON document keeps its shape. Vendor-defined field names stay readable, including the schemas of the vendor built-in tools in the transcript prompt snapshot. Transitive digest references are updated across the three packets.')
        with tempfile.TemporaryDirectory(prefix='claude-desktop-public-alias-') as folder:
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
        (output / f'{run}-private-transformation.json').write_bytes(canonical(private_receipt))
        receipts.append(after)
        summary.append({'packet': run, 'manifest_sha256': pin, 'parent_manifest_sha256': pins[run],
                        'diagnostics_sha256': after['diagnostics_sha256'], 'all_31_metric_rows_unchanged': True,
                        'personal_text_strings_blanked': blanked, 'privacy_review_pending': True})
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
