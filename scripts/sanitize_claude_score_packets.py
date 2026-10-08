#!/usr/bin/env python3
"""Produce explicitly transformed public candidates, preserving metric values.

Only user email and home username are aliased, at identical UTF-8 byte length.
All native records remain; dependent digest references are updated transitively,
across the three packets (each packet carries root evidence of the other two).
Original packets are retained privately and pinned in transformation receipts.
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
from session_bench.native_replay import canonical
from session_bench.public_digest_check import check_public_packets, read_public_set
from session_bench.score_replay import build_score_replay_package, replay_score_package


def sha(data): return hashlib.sha256(data).hexdigest()


def transform_documents(original, redact=None):
    """Alias account email and home name at equal length; ``redact(name, data)`` may remove more at equal length."""
    all_data = b'\n'.join(original.values())
    # Only this archived CLI cohort's account email; vendor attribution stays.
    emails = set(re.findall(rb'[A-Za-z0-9._%+-]+@gmail\.com', all_data))
    homes = set(re.findall(rb'/Users/[A-Za-z0-9_-]+', all_data))
    aliases = {}
    for email in emails:
        suffix = b'@example.test'
        if len(email) <= len(suffix): raise ValueError('email too short for equal-size alias')
        aliases[email] = b'x' * (len(email) - len(suffix)) + suffix
    for home in homes:
        aliases[home] = b'/Users/' + b'x' * (len(home) - 7)
    base = {}
    for name, data in original.items():
        for old, new in aliases.items():
            assert len(old) == len(new)
            data = data.replace(old, new)
        base[name] = redact(name, data) if redact is not None else data
    transformed = dict(base)
    for _ in range(30):
        digest_aliases = {sha(original[name]).encode(): sha(data).encode() for name, data in transformed.items() if original[name] != data}
        revised = {}
        for name, data in base.items():
            for old, new in digest_aliases.items(): data = data.replace(old, new)
            revised[name] = data
        if revised == transformed: break
        transformed = revised
    else: raise ValueError('digest dependency graph did not converge')
    if any(len(original[name]) != len(data) for name, data in transformed.items()):
        raise ValueError('transformation changed physical byte counts')
    if any(old in data for data in transformed.values() for old in aliases):
        raise ValueError('private alias survived')
    return transformed, {'email_alias_count': len(emails), 'home_alias_count': len(homes)}


# Digests of capture-time files that are not in the packet. Each of those files
# holds the digest of the private transcript (or the home name), so its own
# digest confirms a guessed e-mail through a chain of documents. They are
# zeroed at equal length. No verifier reads them.
UNBOUND_ATTEMPT_DIGESTS = (('evidence_31', 'sha256'), ('broad_wrapper', 'sha256'), ('helper_ledger', 'sha256'),
                           ('turn1', 'stdout_sha256'), ('turn2', 'stdout_sha256'), ('decoder', 'source_sha256'))
DIGEST_ALLOWLIST = {
    'root-inventory-before.json': {'metadata_digest': 'digest over the metadata inventory (paths, sizes, times) of every file of the operator normal root; thousands of private entries that are not published'},
    'root-inventory-after.json': {'metadata_digest': 'digest over the metadata inventory (paths, sizes, times) of every file of the operator normal root; thousands of private entries that are not published'},
}


def zero_unbound_attempt_digests(name, data):
    """Zero, at equal length, the digests in ``attempt.json`` whose preimage is not a packet file."""
    if not name.endswith('inputs/attempt.json'):
        return data
    value = json.loads(data)
    for section, field in UNBOUND_ATTEMPT_DIGESTS:
        digest = value.get(section, {}).get(field) if isinstance(value.get(section), dict) else None
        if digest is None:
            continue
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or data.count(digest.encode()) != 1:
            raise ValueError(f'cannot locate the unbound digest {section}.{field} in attempt.json')
        data = data.replace(digest.encode(), b'0' * 64)
    return data


VENDOR_NOTICE = b'[vendor instruction text removed at equal byte length]'
KEPT_ENVIRONMENT_PATH = ('attachment', 'snapshot', 'platform')


def redact_claude_transcript(data, originals=None):
    """Blank the context records of a transcript at equal length; return it and the count.

    The rule is the rule of the Claude Desktop row (same record kinds): every
    string of an attachment record except its kind (prompt snapshot with the
    system prompt, tool descriptions and schema descriptions; output style,
    reminders, listings, environment, session context), and hook text of
    system records. Keys, numbers and every other record stay. One value is
    kept that the Desktop rule blanks: the platform of an environment
    attachment, which states the operating system of the capture.
    """
    from sanitize_claude_desktop_score_packets import _blank, _transcript_rule

    def rule(record, path, is_key):
        # The public inputs builder reads the operating system of the capture
        # from the platform value of an environment attachment. It is kept.
        attachment = record.get('attachment')
        if isinstance(attachment, dict) and attachment.get('type') == 'environment' and path == KEPT_ENVIRONMENT_PATH:
            return False
        return _transcript_rule(record, path, is_key)
    lines, count = [], 0
    for line in data.split(b'\n'):
        if line.strip():
            line, blanked = _blank(line, rule, VENDOR_NOTICE, originals)
            count += blanked
        lines.append(line)
    return b'\n'.join(lines), count


def transformation_receipts(original, transformed, *, parent_manifest_sha256, description, **facts):
    """Return the (public, private) receipts of one transformed packet.

    The public receipt goes into the packet. It carries no hash of a private
    original: with a short alias, such a hash confirms a guessed name. The
    private receipt keeps the parent pins and stays beside the packet.
    """
    names = sorted(transformed)
    public = {'schema_version': 'session-bench-public-alias-transformation-v2', 'original_is_private': True,
              'description': description, **facts,
              'files': [{'path': name, 'transformed_sha256': sha(transformed[name]), 'size_bytes': len(transformed[name])} for name in names]}
    private = {'schema_version': 'session-bench-private-alias-transformation-v1', 'parent_manifest_sha256': parent_manifest_sha256,
               'files': [{'path': name, 'original_sha256': sha(original[name]), 'transformed_sha256': sha(transformed[name])} for name in names]}
    return public, private


def build(source, output):
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError('new destination required')
    summary = []
    # The three packets are transformed together. Each packet carries the root
    # evidence of the other two runs, which names their transcript hashes. A
    # hash of a private transcript confirms a guessed e-mail, so every
    # reference to a changed file is rebound, across packets too.
    parents = [source / f'claude-cli-eval-{repetition}' for repetition in (1, 2, 3)]
    everything = {f'{parent.name}/{path.relative_to(parent).as_posix()}': path.read_bytes()
                  for parent in parents for folder in ('native', 'inputs') for path in (parent / folder).rglob('*') if path.is_file()}
    blanked, blanked_texts = {}, []

    def redact(name, data):
        data = zero_unbound_attempt_digests(name, data)
        if name.endswith('/native/session.jsonl'):
            data, blanked[name.split('/')[0]] = redact_claude_transcript(data, blanked_texts)
        return data
    all_transformed, alias_counts = transform_documents(everything, redact)
    receipts = []
    for parent in parents:
        parent_pin = sha((parent / 'manifest.json').read_bytes())
        original = {name[len(parent.name) + 1:]: data for name, data in everything.items() if name.startswith(parent.name + '/')}
        transformed = {name[len(parent.name) + 1:]: data for name, data in all_transformed.items() if name.startswith(parent.name + '/')}
        receipt, private_receipt = transformation_receipts(
            original, transformed, parent_manifest_sha256=parent_pin, aliases=alias_counts,
            description='Explicit sanitized derivative, not raw capture. Equal UTF-8 length account email and home username aliases; the strings of attachment records (vendor system prompt, tool and schema descriptions, reminders, listings, session context) are blanked at equal length; every native record and key retained. Transitive digest references updated, across the three packets.')
        with tempfile.TemporaryDirectory(prefix='claude-public-alias-') as folder:
            native = Path(folder).resolve()
            for name, data in transformed.items():
                if name.startswith('native/'):
                    path = native / name[7:]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            support = {name[7:]: data for name, data in transformed.items() if name.startswith('inputs/') and name not in ('inputs/workload.json', 'inputs/observer.json', 'inputs/context.json')}
            support['public-transformation.json'] = canonical(receipt)
            packet = output / parent.name
            build_score_replay_package(native, packet,
                workload_document=transformed['inputs/workload.json'], observer_document=transformed['inputs/observer.json'],
                context_document=transformed['inputs/context.json'], supporting_documents=support, source_root=parent / 'runtime')
        pin = sha((packet / 'manifest.json').read_bytes())
        after = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        before = replay_score_package(parent, expected_manifest_sha256=parent_pin, os_sandboxed=True)
        if canonical(before['diagnostics']['intact']['metrics']) != canonical(after['diagnostics']['intact']['metrics']):
            raise ValueError('sanitization changed metric results')
        (output / f'{parent.name}-receipt.json').write_bytes(canonical(after))
        receipts.append(after)
        (output / f'{parent.name}-private-transformation.json').write_bytes(canonical(private_receipt))
        summary.append({'packet': parent.name, 'manifest_sha256': pin, 'parent_manifest_sha256': parent_pin, 'all_31_metric_rows_unchanged': True,
                        'context_strings_blanked': blanked.get(parent.name, 0), 'privacy_review_pending': True})
    # No file of a public packet may still hold a phrase of the context text blanked in the transcripts.
    from sanitize_codex_score_packets import instruction_phrases, require_no_instruction_phrase
    require_no_instruction_phrase(read_public_set([output / parent.name for parent in parents]), instruction_phrases(blanked_texts))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    digests = check_public_packets([output / parent.name for parent in parents], receipts=receipts, allowlist=DIGEST_ALLOWLIST)
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'public_safe': False, 'independent_reproduction': False,
              'public_digest_classes': digests['classes']}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
