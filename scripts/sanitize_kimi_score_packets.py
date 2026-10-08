#!/usr/bin/env python3
"""Produce explicitly transformed public Kimi candidates, preserving metric values.

The captures ran in an isolated Kimi home under a scratch directory. The
scratch paths hold no home name. Private values sit in three places: the plan
names the operator's normal config file under the home directory; a failed
model request (a provider rate limit that Kimi retried) names the provider
organisation and a key id, in the wire log, in the session log and in the
stdout stream; and the receipts hold digests of files that are not public.

Changed, always at identical byte length of the file:

- the home user name and the home path, in every file;
- in the wire log (every record and key stays): the system prompt of
  ``profile.bind`` after its first sentence, every ``description`` of the tool
  definitions of ``llm.tools_snapshot`` (schema descriptions included) and the
  text of every message that the harness injects (``origin.kind``
  ``injection``) are blanked with the vendor marker; the time zone name and the
  local date of an injected date message are blanked with the personal marker
  (the date, set against the UTC times of the records, bounds the UTC offset); ``hash``,
  ``systemPromptHash`` and ``toolsHash`` (digests of the blanked vendor text)
  are zeroed;
- the organisation id and the key id of a provider error message are aliased,
  in the wire log (``turn.step.retrying``, and in a failed session also
  ``turn.step.interrupted`` and the ``error`` of ``turn.ended``), the session
  log and the stdout streams;
- in the plan: the digest of the operator's normal config file and the digest
  of the config file of the isolated home are zeroed;
- in the launch and exit receipts: the digest of every listed file that is
  not a file of the packet, not a rebuilt index file and not empty is zeroed.
  Names and sizes stay: the listing is of the isolated home, not of the
  operator's home.

Kept on purpose: every native record and key that the decoder reads; the
first sentence of the system prompt (it names the harness; the identity
metric reads it); tool names; prompts, responses, reasoning text, tool calls
and results; model fields, usage, times; step uuids, prompt ids, tool call
ids and provider message ids (``chatcmpl-``: eight hex of a Unix time, then
a server value and a counter; a written rule proves the time part); the scratch directory name
(random letters of ``mkdtemp``); the capture times of the receipts.

Not in the packets at all: the stderr and the native files of the unused
attempts, and the files of the isolated home outside the session directory
(three of them are rebuilt from public values; see ``home-index.json``).

Machine identifiers: the receipts of this controller hold no device, inode,
birth time or count of a private root. What stays is of the isolated home and
workspace only: file names and sizes, the scratch directory name, and the
capture times.

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
from sanitize_hermes_score_packets import _compact, _fill, encodings, private_markers
from build_kimi_public_inputs import build as build_public_inputs
from session_bench.kimi_score_inputs import HOME_PROOF, observer_from_capture_documents
from session_bench.kimi_wire_records import SESSION_DIR, WIRE, _HARNESS
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets, read_public_set, short_digest_warnings
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

RUNS = ('kimi-2026-10-08-01', 'kimi-2026-10-08-02', 'kimi-2026-10-08-06')
WIRE_PATH = 'native/' + SESSION_DIR + '/' + WIRE
LOG_PATH = 'native/' + SESSION_DIR + '/logs/kimi-code.log'
_EXECUTABLE_REASON = ('digest of the vendor package file of the Kimi CLI (dist/main.mjs of @moonshot-ai/kimi-code 2.1.1); public data outside '
                      'the set that holds no operator value')
DIGEST_ALLOWLIST = {'inputs/capture/plan.json': {'kimi_executable_sha256': _EXECUTABLE_REASON}}
_EMAIL = re.compile(rb'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
# The provider names the organisation and a key id in an error message: ``org-<hex>`` and ``<ak-<letters and digits>>``.
_ACCOUNT = re.compile(rb'(?<![A-Za-z0-9])(org-[0-9a-f]{8,}|ak-[0-9a-z]{8,})')
_VENDOR_HASHES = {'llm.tools_snapshot': ('hash',), 'llm.request': ('systemPromptHash', 'toolsHash')}
DESCRIPTION = ('Explicit sanitized derivative, not raw capture. At equal byte length: the home user name is aliased in every file; in the '
               'wire log the system prompt after its first sentence, the tool and schema descriptions and the injected messages are '
               'blanked, the digests of the blanked vendor text are zeroed and the time zone name is blanked; the organisation id and '
               'the key id of provider error messages are aliased in the wire log, the session log and the stdout streams; in the '
               'plan and in the launch and exit receipts the digest of every file that is not in the packet is zeroed. Every native '
               'record and decoded key is retained.')


def sha(data): return hashlib.sha256(data).hexdigest()


def completion_id_proof(run):
    """A provider message id ``chatcmpl-<24 hex>``: the first 8 hex are a Unix time within a day of ``time`` of its own record."""
    token = run.token.decode()
    if len(token) != 24 or token != token.lower() or run.view[max(0, run.start - 9):run.start] != b'chatcmpl-':
        return False
    start, end = run.view.rfind(b'\n', 0, run.start) + 1, run.view.find(b'\n', run.end)
    try:
        record = json.loads(run.view[start:end if end >= 0 else len(run.view)])
    except ValueError:
        return False
    time = record.get('time') if isinstance(record, dict) else None
    return type(time) is int and abs(int(token[:8], 16) - time / 1000) <= 86400


# Hex runs of 12 or more characters that are not 64-hex digests (``session_bench.public_digest_check.long_hex_runs``).
HEX_ALLOWLIST = (
    {'file': WIRE_PATH, 'before': rb'"messageId":"chatcmpl-', 'proof': completion_id_proof,
     'reason': 'message id that the model provider issues: 8 hex of a Unix time (proved against the time of the record), then 12 hex that '
               'repeat across requests (a provider server) and a 4-hex counter; a handle of the native record, not a digest of content'},
)


def _line(value):
    """The serialization of one wire record and of one stdout line: compact JSON, Unicode kept."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _encoded_size(text):
    """Byte length of ``text`` inside a JSON string of the file (without the quotes)."""
    return len(json.dumps(text, ensure_ascii=False).encode('utf-8')) - 2


def _alias_account(match):
    value = match.group(1)
    # An upper-case letter: the alias is then not of the form of an id.
    return value[:value.index(b'-') + 1] + b'X' * (len(value) - value.index(b'-') - 1)


def alias_accounts(data, private_values=None):
    """Alias every organisation id and key id at equal length; returns (bytes, count)."""
    if private_values is not None:
        private_values.update(match.group(1).decode() for match in _ACCOUNT.finditer(data))
    return _ACCOUNT.subn(_alias_account, data)


def sanitize_wire(data, originals, private_values):
    """The wire log with vendor instruction text blanked and vendor digests zeroed at equal byte length; returns (bytes, counts).

    ``originals`` receives every instruction text that was blanked, for the
    phrase guard. A record that is not in the compact serialization of the
    harness raises: it needs a rule.
    """
    counts = {'system_prompts_blanked': 0, 'tool_descriptions_blanked': 0, 'injected_messages_blanked': 0, 'vendor_digests_zeroed': 0,
              'time_zones_blanked': 0, 'local_dates_blanked': 0}

    def describe(value, key=None):
        if isinstance(value, dict):
            return {name: describe(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [describe(item, key) for item in value]
        if isinstance(value, str) and key == 'description' and value:
            originals.append(value)
            counts['tool_descriptions_blanked'] += 1
            return _fill(_encoded_size(value), VENDOR_NOTICE)
        return value

    lines = []
    for number, line in enumerate(data.split(b'\n'), 1):
        if not line.strip():
            lines.append(line)
            continue
        record = json.loads(line)
        if _line(record) != line:
            raise ValueError(f'wire line {number} is not in the compact serialization; it needs a rule')
        kind = record.get('type')
        if kind == 'profile.bind' and isinstance(record.get('systemPrompt'), str):
            prompt = record['systemPrompt']
            named = _HARNESS.match(prompt)
            stop = prompt.find('.', named.end()) + 1 if named else 0
            if not named or stop <= 0 or stop > 160:
                raise ValueError('the system prompt needs a rule: no harness sentence')
            # The first sentence stays: it names the harness. The rest is blanked.
            originals.append(prompt[stop:])
            record['systemPrompt'] = prompt[:stop] + _fill(_encoded_size(prompt[stop:]), VENDOR_NOTICE)
            counts['system_prompts_blanked'] += 1
        elif kind == 'llm.tools_snapshot' and isinstance(record.get('tools'), list):
            record['tools'] = describe(record['tools'])
        elif kind == 'context.append_message':
            message = record.get('message') if isinstance(record.get('message'), dict) else {}
            origin = message.get('origin') if isinstance(message.get('origin'), dict) else {}
            if origin.get('kind') == 'injection':
                for part in message.get('content') if isinstance(message.get('content'), list) else []:
                    if isinstance(part, dict) and isinstance(part.get('text'), str) and part['text']:
                        originals.append(part['text'])
                        part['text'] = _fill(_encoded_size(part['text']), VENDOR_NOTICE)
                        counts['injected_messages_blanked'] += 1
                disclosure = origin.get('disclosure')
                if isinstance(disclosure, dict) and isinstance(disclosure.get('timeZone'), str) and disclosure['timeZone']:
                    private_values.add(disclosure['timeZone'])
                    disclosure['timeZone'] = _fill(_encoded_size(disclosure['timeZone']), PERSONAL_NOTICE)
                    counts['time_zones_blanked'] += 1
                # The local date, set against the UTC times of the records, bounds the UTC offset of the operator's clock.
                if isinstance(disclosure, dict) and isinstance(disclosure.get('localDate'), str) and disclosure['localDate']:
                    disclosure['localDate'] = _fill(_encoded_size(disclosure['localDate']), PERSONAL_NOTICE)
                    counts['local_dates_blanked'] += 1
            elif origin.get('kind') != 'user':
                raise ValueError(f'wire line {number} holds a context message of an unknown origin; it needs a rule')
        for key in _VENDOR_HASHES.get(kind, ()):
            if isinstance(record.get(key), str) and re.fullmatch(r'[0-9a-f]{64}', record[key]):
                record[key] = '0' * 64
                counts['vendor_digests_zeroed'] += 1
        public = _line(record)
        if len(public) != len(line):
            raise ValueError(f'wire line {number} changed its byte length')
        lines.append(public)
    return b'\n'.join(lines), counts


def zero_plan_digests(data):
    """The plan without the digest of the operator's config file and of the config file of the isolated home; returns (bytes, count)."""
    value = json.loads(data)
    if _compact(value) != data:
        raise ValueError('plan.json is not in the controller serialization')
    for key in ('source_config_sha256', 'safe_config_sha256'):
        value[key] = '0' * 64
    return _compact(value), 2


def zero_unbound_listing_digests(data, bound):
    """A launch or exit receipt without the digest of every listed file that is not in ``bound``; returns (bytes, count).

    ``bound`` holds the digests that the public packet proves: the files of
    the packet, the rebuilt index files and the empty file.
    """
    value = json.loads(data)
    if _compact(value) != data:
        raise ValueError('a receipt is not in the controller serialization')
    count = 0

    def walk(item):
        nonlocal count
        if isinstance(item, dict):
            if set(item) == {'sha256', 'size_bytes'} and isinstance(item['sha256'], str):
                if item['sha256'] not in bound:
                    item['sha256'] = '0' * 64
                    count += 1
                return
            for inner in item.values():
                walk(inner)
        elif isinstance(item, list):
            for inner in item:
                walk(inner)
    for key in ('kimi_home_inventory_before', 'kimi_home_inventory_after', 'native_family_source_inventory', 'native_family_copied_inventory'):
        walk(value.get(key))
    return _compact(value), count


def home_path(original):
    """The one home directory that the capture plans name (the path of the operator's normal config file)."""
    homes = set()
    for name, data in original.items():
        if name.endswith('/inputs/capture/plan.json'):
            match = re.match(r'/Users/[A-Za-z0-9._-]+(?=/)', json.loads(data)['source_config_path'])
            if match is None:
                raise ValueError('the config path is not under a home directory')
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
    counts = {'home_alias_count': 1, 'home_name_occurrences': 0, 'system_prompts_blanked': 0, 'tool_descriptions_blanked': 0,
              'injected_messages_blanked': 0, 'vendor_digests_zeroed': 0, 'time_zones_blanked': 0, 'local_dates_blanked': 0, 'account_ids_aliased': 0,
              'plan_digests_zeroed': 0, 'listing_digests_zeroed': 0}
    base, blanked_texts, private_values = {}, [], set()
    for path, data in original.items():
        run, inner = path.split('/', 1)
        if inner == WIRE_PATH:
            data, found = sanitize_wire(data, blanked_texts, private_values)
            for key, value in found.items():
                counts[key] += value
        if inner in (WIRE_PATH, LOG_PATH) or inner.endswith('/stdout.jsonl'):
            data, found = alias_accounts(data, private_values)
            counts['account_ids_aliased'] += found
        elif _ACCOUNT.search(data):
            raise ValueError(f'an organisation id or a key id needs a rule: {inner}')
        if _EMAIL.search(data) and not inner.endswith('.py'):
            raise ValueError(f'an e-mail address needs a rule: {inner}')
        if inner == 'inputs/capture/plan.json':
            data, found = zero_plan_digests(data)
            counts['plan_digests_zeroed'] += found
        elif inner.endswith(('/launch.json', '/exit.json')):
            bound = {sha(item) for other, item in original.items() if other.startswith(run + '/')} | {sha(b'')}
            bound |= {row['sha256'] for row in json.loads(original[f'{run}/inputs/{HOME_PROOF}'])['files']}
            data, found = zero_unbound_listing_digests(data, bound)
            counts['listing_digests_zeroed'] += found
        counts['home_name_occurrences'] += data.count(name.encode())
        for old, alias in replacements:
            data = data.replace(old, alias)
        base[path] = data

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
    forbidden = sorted(encodings(name) | {value.encode() for value in private_values if len(value) >= 6})
    everything = b'\n'.join(transformed.values())
    if any(value in everything for value in forbidden) or _ACCOUNT.search(everything):
        raise ValueError('the home user name, an organisation id or a key id survived')
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
        with tempfile.TemporaryDirectory(prefix='kimi-public-alias-') as folder:
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
    result = {'schema_version': 'session-bench-sanitized-candidates-v1', 'runs': summary, 'aliases': counts, 'digest_classes': digests['classes'],
              'hex_run_classes': digests['hex_runs']['classes'], 'hex_runs_allowlisted_by_reason': digests['hex_runs']['allowlisted_by_reason'],
              'public_bundle_sha256': bundle['sha256'], 'public_safe': False, 'independent_reproduction': False}
    (output / 'summary.json').write_bytes(canonical(result))
    print(json.dumps(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); build(args.source, args.output)
