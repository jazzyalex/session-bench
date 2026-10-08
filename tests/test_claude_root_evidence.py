import json
from pathlib import Path
import pytest
from session_bench.claude_root_evidence import FILES, verify_claude_root_evidence


@pytest.fixture
def receipts(tmp_path):
    import hashlib
    def encode(value): return json.dumps(value, sort_keys=True).encode()
    def digest(value): return hashlib.sha256(encode(value)).hexdigest()
    for repetition in (1, 2, 3):
        key=f"-tmp-fixture-{repetition}"
        native_sha=hashlib.sha256(f"synthetic-native-{repetition}".encode()).hexdigest()
        selected={"id":f"session-{repetition}","path":f"{key}/session.jsonl","sha256":native_sha,"size_bytes":100}
        artifact={"relative_path":selected["path"],"sha256":native_sha,"size_bytes":100,"filesystem_id":f"device:{repetition}"}
        family={"project_key":key,"selected_session_sha256":native_sha,"selected_session_relative_path":selected["path"],"artifacts":[artifact],"unrelated_preexisting_sessions_opened":False}
        decode={"artifacts":[{"sha256":native_sha}]}
        source={"project_key":key,"selected_artifact":selected}
        qualification={"metadata_only_before_after":True,"preexisting_content_opened":False,"selected_file_count":1,"source_manifest":{"sha256":digest(source)}}
        qualified={"complete_record_family":True,"new_family_only":True,"unrelated_preexisting_sessions_read":False,"project_key":key,"selected_artifact":selected,
                   "qualification":qualification,"family_manifest":{"sha256":digest(family)},"decode_manifest":{"sha256":digest(decode)},"offline_copy":{"sha256":native_sha}}
        root="/Users/example/.claude/projects"
        prepared={"configuration_id":"claude-cli","repetition":repetition,"project_root":f"/tmp/fixture-{repetition}",
                  "privacy":{"before_after_inventory_metadata_only":True,"unrelated_preexisting_sessions_read":False,"normal_root":root}}
        inventory={"root":root,"root_metadata":{"filesystem_id":"device:root"},"file_count":10,"project_key_count":5}
        after={**inventory,"file_count":11,"project_key_count":6}
        quiet={"stable":True,"checks":2,"observed":[[artifact],[artifact]]}
        docs=dict(zip(FILES,(prepared,inventory,after,quiet,family,source,qualified,decode)))
        for name,value in docs.items():
            target=tmp_path/f"repetition-{repetition}"/name
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(encode(value))
    return tmp_path


def test_retained_three_roots_qualified(receipts):
    rows = verify_claude_root_evidence(receipts)
    assert [row['repetition'] for row in rows] == [1, 2, 3]
    assert all(row['discovery_mode'] == 'metadata_safe_normal_root' for row in rows)


@pytest.mark.parametrize('name,mutation', [
    ('native-manifest.json', lambda value: value.update(project_key='forged')),
    ('controller-prepared.json', lambda value: value['privacy'].update(unrelated_preexisting_sessions_read=True)),
    ('root-inventory-after.json', lambda value: value['root_metadata'].update(filesystem_id='different')),
    ('native-quiescence.json', lambda value: value.update(stable=False)),
    ('native-manifest-qualified.json', lambda value: value['qualification'].update(selected_file_count=2)),
])
def test_rejects_invalid_provenance(receipts, name, mutation):
    path = receipts / 'repetition-2' / name
    value = json.loads(path.read_bytes()); mutation(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verify_claude_root_evidence(receipts)
