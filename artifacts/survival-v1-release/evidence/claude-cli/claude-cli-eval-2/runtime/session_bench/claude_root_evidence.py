"""Verify retained metadata-only Claude capture root receipts.

This checks archived capture provenance, not a fresh inventory of personal roots.
"""
import hashlib
import json
from pathlib import Path
import re

FILES = ('controller-prepared.json', 'root-inventory-before.json', 'root-inventory-after.json',
         'native-quiescence.json', 'native-family-manifest.json', 'native-manifest.json',
         'native-manifest-qualified.json', 'native-bundle/decode.json')


def verify_claude_root_evidence(root: Path):
    """Return three root metric rows from a copied receipt directory."""
    root = Path(root)
    rows = []
    sessions = set()
    projects = set()
    for repetition in (1, 2, 3):
        folder = root / f'repetition-{repetition}'
        raw = {name: (folder / name).read_bytes() for name in FILES}
        data = {name: json.loads(value) for name, value in raw.items()}
        prepared = data['controller-prepared.json']
        qualified = data['native-manifest-qualified.json']
        source = data['native-manifest.json']
        family = data['native-family-manifest.json']
        decode = data['native-bundle/decode.json']
        def require(condition, message):
            if not condition:
                raise ValueError(f'Claude root repetition {repetition}: {message}')
        require(prepared['configuration_id'] == 'claude-cli' and prepared['repetition'] == repetition, 'configuration mismatch')
        privacy = prepared['privacy']
        require(privacy['before_after_inventory_metadata_only'] is True and privacy['unrelated_preexisting_sessions_read'] is False, 'unsafe discovery')
        require(qualified['complete_record_family'] is True and qualified['new_family_only'] is True and qualified['unrelated_preexisting_sessions_read'] is False, 'family not qualified')
        q = qualified['qualification']
        require(q['metadata_only_before_after'] is True and q['preexisting_content_opened'] is False and q['selected_file_count'] == 1, 'qualification not metadata safe')
        require(q['source_manifest']['sha256'] == hashlib.sha256(raw['native-manifest.json']).hexdigest(), 'source manifest digest mismatch')
        require(qualified['family_manifest']['sha256'] == hashlib.sha256(raw['native-family-manifest.json']).hexdigest(), 'family digest mismatch')
        require(qualified['decode_manifest']['sha256'] == hashlib.sha256(raw['native-bundle/decode.json']).hexdigest(), 'decode digest mismatch')
        key = re.sub(r'[^A-Za-z0-9]', '-', prepared['project_root'])
        require(key == qualified['project_key'] == family['project_key'] == source['project_key'], 'project key mismatch')
        require(key not in projects, 'reused project')
        projects.add(key)
        selected = qualified['selected_artifact']
        require(selected == source['selected_artifact'] and selected['path'].startswith(key + '/'), 'selected path mismatch')
        require(selected['sha256'] == family['selected_session_sha256'] and selected['path'] == family['selected_session_relative_path'], 'selected family mismatch')
        require(len(family['artifacts']) == len(decode['artifacts']) == 1 and family['unrelated_preexisting_sessions_opened'] is False, 'family closure differs')
        artifact = family['artifacts'][0]
        require(artifact['sha256'] == selected['sha256'] == decode['artifacts'][0]['sha256'] == qualified['offline_copy']['sha256'], 'native binding mismatch')
        require(artifact['relative_path'] == selected['path'] and artifact['size_bytes'] == selected['size_bytes'], 'native size/path mismatch')
        require(selected['id'] not in sessions, 'reused session')
        sessions.add(selected['id'])
        before, after = (data[f'root-inventory-{side}.json'] for side in ('before', 'after'))
        require(before['root'] == after['root'] == privacy['normal_root'], 'normal root changed')
        require(before['root_metadata']['filesystem_id'] == after['root_metadata']['filesystem_id'], 'root identity changed')
        require(after['file_count'] >= before['file_count'] + 1 and after['project_key_count'] >= before['project_key_count'] + 1, 'new family not reflected by inventory')
        quiet = data['native-quiescence.json']
        require(quiet['stable'] is True and quiet['checks'] >= 2 and len(quiet['observed']) >= 2, 'not quiescent')
        require(all(len(snapshot) == 1 and snapshot[0]['relative_path'] == selected['path'] and snapshot[0]['size_bytes'] == selected['size_bytes'] and snapshot[0]['filesystem_id'] == artifact['filesystem_id'] for snapshot in quiet['observed']), 'quiescence family mismatch')
        rows.append({'repetition': repetition, 'root_locator': f'claude/projects/{key}',
                     'discovery_mode': 'metadata_safe_normal_root', 'personal_history_scanned': False})
    return rows
