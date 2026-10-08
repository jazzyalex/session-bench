import hashlib
import json

import pytest

from datetime import datetime, timezone

from session_bench.claude_desktop_root_evidence import FILES, ROOT_LOCATOR, verify_claude_desktop_root_evidence

WINDOW_MS = 1_789_000_000_000


def stamp(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat().replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def receipts(tmp_path):
    for r in (1, 2, 3):
        run, cli, desktop = f'fixture-{r}', f'cli-{r}', f'local_{r}'
        base = WINDOW_MS + r * 100_000
        transcript = encode({'sessionId': cli, 'type': 'user', 'message': {'content': 'synthetic'}, 'cwd': '/synthetic',
                             'timestamp': stamp(base + 2_000)}) + b'\n'
        metadata = encode({'cliSessionId': cli, 'sessionId': desktop, 'bridgeSessionIds': [f'bridge-{r}'], 'cwd': '/synthetic',
                           'originCwd': '/synthetic', 'createdAt': base + 1_000, 'lastActivityAt': base + 3_000})
        artifacts = [{'role': role, 'path': path, 'sha256': sha(data), 'size_bytes': len(data)} for role, path, data in (
            ('transcript', 'transcript/session.jsonl', transcript), ('desktop_metadata', 'desktop/session.json', metadata))]
        family = {'run_id': run, 'repetition': r, 'cli_session_id': cli, 'desktop_session_id': desktop,
                  'metadata_only_discovery': True, 'personal_history_content_scanned': False,
                  'unrelated_preexisting_sessions_opened': False, 'complete_cross_root_family': True, 'artifacts': artifacts}
        validation = encode(family)
        decode = encode({'artifacts': [{'sha256': sha(transcript)}]})
        qualified = {'schema_version': 'session-bench-claude-desktop-native-manifest-v3-qualified', 'configuration_id': 'claude-desktop',
                     'repetition': r, 'complete_cross_root_family': True, 'complete_persistent_family': True,
                     'unrelated_preexisting_sessions_opened': False, 'cli_session_id': cli, 'desktop_session_id': desktop,
                     'family_validation': {'sha256': sha(validation)}, 'packages': {'native': {'decode_manifest_sha256': sha(decode)}},
                     'selected_artifact': {'sha256': sha(transcript), 'size_bytes': len(transcript)}}
        before = {'schema_version': 'session-bench-metadata-inventory-v1', 'run_id': run, 'phase': 'before',
                  'personal_history_content_scanned': False, 'roots': ['claude-projects', 'claude-desktop-sessions'],
                  'created_at_ns': base * 1_000_000, 'metadata_digest': 'a' * 64, 'file_count': 20}
        after = {**before, 'phase': 'after', 'created_at_ns': (base + 4_000) * 1_000_000, 'metadata_digest': 'b' * 64, 'file_count': 22,
                 'new_file_count': 2, 'changed_or_new_file_count': 2}
        docs = (encode({'attempt_id': run, 'configuration_id': 'claude-desktop', 'repetition': r}), encode(before), encode(after),
                encode(family), validation, encode(qualified), decode, transcript, metadata)
        for name, data in zip(FILES, docs):
            target = tmp_path / f'repetition-{r}' / name
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    return tmp_path


def test_three_retained_desktop_roots_are_located_by_their_inventory_window_and_native_ids(receipts):
    rows = verify_claude_desktop_root_evidence(receipts)

    assert [row['repetition'] for row in rows] == [1, 2, 3]
    assert all(row == {'repetition': row['repetition'], 'root_locator': ROOT_LOCATOR,
                       'discovery_mode': 'metadata_safe_normal_root', 'personal_history_scanned': False} for row in rows)


@pytest.mark.parametrize('name,mutation', [
    (FILES[8], lambda d: d.update(originCwd='/elsewhere')),
    (FILES[8], lambda d: d.update(createdAt=d['createdAt'] - 5_000)),
    (FILES[8], lambda d: d.update(lastActivityAt=d['lastActivityAt'] + 5_000)),
    (FILES[7], lambda d: d.update(cwd='/elsewhere')),
    (FILES[7], lambda d: d.update(timestamp=stamp(WINDOW_MS))),
    (FILES[7], lambda d: d.pop('timestamp')),
    (FILES[2], lambda d: d.update(created_at_ns=d['created_at_ns'] + 200_000 * 1_000_000)),
])
def test_a_family_that_is_not_tied_to_its_own_inventory_window_is_refused(receipts, name, mutation):
    path = receipts / 'repetition-2' / name
    data = json.loads(path.read_bytes()); mutation(data)
    path.write_bytes(encode(data) + (b'\n' if name == FILES[7] else b''))
    with pytest.raises(ValueError):
        verify_claude_desktop_root_evidence(receipts)


@pytest.mark.parametrize('name,mutation', [
    (FILES[1], lambda d: d.update(personal_history_content_scanned=True)),
    (FILES[2], lambda d: d.update(roots=['claude-projects'])),
    (FILES[2], lambda d: d.update(new_file_count=3)),
    (FILES[2], lambda d: d.update(run_id='other-run')),
    (FILES[3], lambda d: d.update(cli_session_id='other-session')),
    (FILES[5], lambda d: d.update(complete_persistent_family=False)),
    (FILES[8], lambda d: d.update(cliSessionId='other-session')),
])
def test_rejects_unbound_or_unsafe_desktop_provenance(receipts, name, mutation):
    path = receipts / 'repetition-2' / name
    data = json.loads(path.read_bytes()); mutation(data); path.write_bytes(encode(data))
    with pytest.raises(ValueError):
        verify_claude_desktop_root_evidence(receipts)


def test_rejects_missing_pair_member(receipts):
    (receipts / 'repetition-3' / FILES[-1]).unlink()
    with pytest.raises(ValueError, match='exactly three'):
        verify_claude_desktop_root_evidence(receipts)


def test_rejects_symlinked_root_receipt(receipts, tmp_path):
    path = receipts / 'repetition-3' / FILES[0]
    copied = tmp_path / 'copy'; copied.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(copied)
    with pytest.raises(ValueError):
        verify_claude_desktop_root_evidence(receipts)


@pytest.mark.parametrize('source_path', ['/Users/example/.claude/projects/key/session.jsonl', '/alternate/unproven/session.jsonl'])
def test_injected_source_path_cannot_qualify_alias_only_receipts(receipts, source_path):
    for name in (FILES[1], FILES[2]):
        path = receipts / 'repetition-2' / name
        data = json.loads(path.read_bytes())
        data['source_path'] = source_path
        data['root_locator'] = 'CLAUDE_HOME/projects/<project>/<session>.jsonl'
        path.write_bytes(encode(data))
    assert all(row['root_locator'] == ROOT_LOCATOR for row in verify_claude_desktop_root_evidence(receipts))
