"""Verify archived Claude Desktop root receipts without inspecting live stores.

Source authenticity and completeness remain the historical operator's capture
assertions. This verifier checks their internal consistency and binds all three
independent Desktop session families to their exact copied native bytes.

The retained inventories did not record the source paths of the two selected
files. Each family is tied to its own metadata-only inventory window by its
native timestamps and session identifiers instead. The locator is therefore a
derivation rule over native fields, not an observed path. This is weaker
evidence than an observed-path receipt, and the report says so.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .claude_desktop_root import validate_claude_desktop_family
from .native_replay import _snapshot_tree

FILES = (
    'attempt.json', 'capture/observer-private/before-inventory.json',
    'capture/observer-private/after-inventory.json',
    'capture/full-family-manifest-private.json',
    'capture/finalized-private-v1/family-validation.json',
    'capture/qualified-private-v1/native-manifest-qualified.json',
    'capture/finalized-private-v1/native-package/decode.json',
    'capture/finalized-private-v1/native-family-private/transcript/session.jsonl',
    'capture/finalized-private-v1/native-family-private/desktop/session.json',
)
FAMILY_PREFIX = 'capture/finalized-private-v1/native-family-private/'
# Every variable of the rule is a field of the two native files.
ROOT_LOCATOR = ('CLAUDE_HOME/projects/<cwd with non-alphanumerics as "-">/<sessionId>.jsonl + '
                'Claude Application Support/claude-code-sessions/<account>/<workspace>/<desktop sessionId>.json')


def _nanoseconds(value):
    """Nanoseconds of one native RFC3339 timestamp, or None."""
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
    except ValueError:
        return None
    if moment is None or moment.tzinfo is None:
        return None
    return int(moment.timestamp() * 1000) * 1_000_000


def verify_claude_desktop_root_evidence(root: Path):
    """Validate retained family receipts; return three root metric rows.

    These historical inventory schemas contain logical root aliases and an
    aggregate digest but no selected-artifact source paths. A row is returned
    only when the copied family lies inside its own inventory window, carries
    one session in both files, and no two windows overlap. Freeform
    source_path/root_locator fields in a receipt are never read.
    """
    contents = _snapshot_tree(Path(root))
    expected = {f'repetition-{r}/{name}' for r in (1, 2, 3) for name in FILES}
    if set(contents) != expected:
        raise ValueError('Claude Desktop root proof must contain exactly three retained receipt/family sets')
    sessions, desktop_sessions, windows = set(), set(), []
    for repetition in (1, 2, 3):
        raw = {name: contents[f'repetition-{repetition}/{name}'] for name in FILES}
        def require(condition, message):
            if not condition:
                raise ValueError(f'Claude Desktop root repetition {repetition}: {message}')
        def doc(name):
            value = json.loads(raw[name])
            require(isinstance(value, dict), 'receipt is not an object')
            return value
        attempt = doc('attempt.json')
        run_id = attempt.get('attempt_id')
        require(attempt.get('configuration_id') == 'claude-desktop'
                and attempt.get('repetition') == repetition and isinstance(run_id, str), 'attempt identity mismatch')
        before, after = [doc(f'capture/observer-private/{side}-inventory.json') for side in ('before', 'after')]
        for side, inventory in zip(('before', 'after'), (before, after)):
            require(inventory.get('schema_version') == 'session-bench-metadata-inventory-v1'
                    and inventory.get('run_id') == run_id and inventory.get('phase') == side, 'inventory identity mismatch')
            require(inventory.get('personal_history_content_scanned') is False
                    and inventory.get('roots') == ['claude-projects', 'claude-desktop-sessions'], 'unsafe or different root discovery')
            require(isinstance(inventory.get('metadata_digest'), str)
                    and re.fullmatch('[0-9a-f]{64}', inventory['metadata_digest']), 'missing inventory digest')
        require(after['created_at_ns'] > before['created_at_ns'] and after['metadata_digest'] != before['metadata_digest'], 'inventory chronology mismatch')
        require(after.get('new_file_count') == after.get('changed_or_new_file_count') == 2
                and after['file_count'] == before['file_count'] + 2, 'inventory does not isolate the new persistent pair')
        family = doc('capture/full-family-manifest-private.json')
        validation = doc('capture/finalized-private-v1/family-validation.json')
        qualified = doc('capture/qualified-private-v1/native-manifest-qualified.json')
        for receipt in (family, validation):
            require(receipt.get('run_id') == run_id and receipt.get('repetition') == repetition, 'family identity mismatch')
            require(receipt.get('metadata_only_discovery') is True
                    and receipt.get('personal_history_content_scanned') is False
                    and receipt.get('unrelated_preexisting_sessions_opened') is False
                    and receipt.get('complete_cross_root_family') is True, 'family privacy/completeness not qualified')
        require(qualified.get('schema_version') == 'session-bench-claude-desktop-native-manifest-v3-qualified'
                and qualified.get('configuration_id') == 'claude-desktop'
                and qualified.get('repetition') == repetition, 'qualified receipt identity mismatch')
        require(qualified.get('complete_cross_root_family') is True and qualified.get('complete_persistent_family') is True
                and qualified.get('unrelated_preexisting_sessions_opened') is False, 'qualified family incomplete')
        require(qualified['family_validation']['sha256'] == hashlib.sha256(raw['capture/finalized-private-v1/family-validation.json']).hexdigest(), 'family validation digest mismatch')
        require(qualified['packages']['native']['decode_manifest_sha256'] == hashlib.sha256(raw['capture/finalized-private-v1/native-package/decode.json']).hexdigest(), 'native inventory digest mismatch')
        with tempfile.TemporaryDirectory(prefix='claude-desktop-root-family-') as folder:
            for relative in ('transcript/session.jsonl', 'desktop/session.json'):
                target = Path(folder) / relative; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw[FAMILY_PREFIX + relative])
            actual = validate_claude_desktop_family(Path(folder))
        require(family['artifacts'] == validation['artifacts'] == actual['artifacts'], 'copied family differs from retained inventory')
        for key, seen in (('cli_session_id', sessions), ('desktop_session_id', desktop_sessions)):
            require(actual[key] == family[key] == validation[key] == qualified[key], 'cross-root session identity mismatch')
            require(actual[key] not in seen, 'reused Desktop session')
            seen.add(actual[key])
        transcript = raw[FAMILY_PREFIX + 'transcript/session.jsonl']
        selected = qualified['selected_artifact']
        decode = doc('capture/finalized-private-v1/native-package/decode.json')
        require(selected['sha256'] == hashlib.sha256(transcript).hexdigest()
                and selected['size_bytes'] == len(transcript) and len(decode['artifacts']) == 1
                and decode['artifacts'][0]['sha256'] == selected['sha256'], 'selected transcript binding mismatch')
        records = [json.loads(line) for line in transcript.splitlines() if line.strip()]
        require(all(isinstance(record, dict) for record in records), 'transcript record is not an object')
        require({record['sessionId'] for record in records if record.get('sessionId') is not None} == {actual['cli_session_id']},
                'transcript does not hold exactly one session')
        metadata = doc(FAMILY_PREFIX + 'desktop/session.json')
        folders = [record['cwd'] for record in records if isinstance(record.get('cwd'), str)]
        require(metadata.get('sessionId') == actual['desktop_session_id']
                and metadata.get('cliSessionId') == actual['cli_session_id'], 'Desktop metadata identity mismatch')
        require(bool(folders) and metadata.get('cwd') == metadata.get('originCwd') == folders[0],
                'Desktop working directory differs from the transcript')
        moments = [_nanoseconds(record['timestamp']) for record in records if 'timestamp' in record]
        for key in ('createdAt', 'lastActivityAt'):
            value = metadata.get(key)
            moments.append(value * 1_000_000 if type(value) is int else None)
        require(len(moments) > 2 and None not in moments, 'native timestamp missing or malformed')
        require(before['created_at_ns'] < min(moments) and max(moments) < after['created_at_ns'],
                'native family lies outside its inventory window')
        windows.append((before['created_at_ns'], after['created_at_ns']))
    windows.sort()
    if any(first[1] >= second[0] for first, second in zip(windows, windows[1:])):
        raise ValueError('Claude Desktop root inventory windows overlap')
    return [{'repetition': repetition, 'root_locator': ROOT_LOCATOR,
             'discovery_mode': 'metadata_safe_normal_root', 'personal_history_scanned': False}
            for repetition in (1, 2, 3)]
