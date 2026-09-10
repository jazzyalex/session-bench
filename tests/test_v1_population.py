"""Adversarial population and observer controls from the independent review."""
import copy
import json
import pytest
from session_bench.bundle import canonical, digest, validate_bundle, validate_registry, read_json
from session_bench.fixtures import build_fixture
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle


def rehash(root):
    index=read_json(root/'native/decode.json')
    for artifact in index['artifacts']:
        data=(root/'native'/artifact['path']).read_bytes()
        artifact.update(sha256=digest(data),size_bytes=len(data))
    (root/'native/decode.json').write_bytes(canonical(index))
    manifest=read_json(root/'manifest.json')
    for artifact in manifest['artifacts']:
        data=(root/artifact['path']).read_bytes()
        artifact.update(sha256=digest(data),size_bytes=len(data))
    (root/'manifest.json').write_bytes(canonical(manifest))


def test_deleted_failed_assertion_cannot_shrink_population(tmp_path):
    root=build_fixture(tmp_path/'loss',mutation='remove_fact')
    path=root/'expected/assertions.json'
    expected=read_json(path)
    expected['assertions']=[a for a in expected['assertions'] if a['event_id']!='tr-2']
    path.write_bytes(canonical(expected)); rehash(root)
    with pytest.raises(ValueError,match='primary observation population'):
        evaluate_bundle(root,decoder=decode_native)


@pytest.mark.parametrize('extra_session',[False,True])
def test_additional_known_native_event_cannot_be_ignored(tmp_path,extra_session):
    root=build_fixture(tmp_path/'extra')
    path=root/'native/session.jsonl'
    event=next(json.loads(line) for line in path.read_text().splitlines() if json.loads(line)['id']=='tr-2')
    event['id']='tr-2-copy'
    if extra_session:
        event={'id':'session-3','session_id':'session-3','kind':'session','fields':{}}
    with path.open('ab') as stream:
        stream.write(canonical(event)+b'\n')
    rehash(root)
    result,_=evaluate_bundle(root,decoder=decode_native)
    row=next(r for r in result['rows'] if 'unexpected_native_event' in r['findings'])
    assert row['state']=='fail'
    assert ('native_session_population_mismatch' in row['findings'])==extra_session
    assert next(m for m in result['metrics'] if m['scope']=='extended')['state']=='fail'


@pytest.mark.parametrize('frankenstein',[False,True])
def test_conflicting_observers_cannot_be_cherry_picked(tmp_path,frankenstein):
    root=build_fixture(tmp_path/'conflict')
    op=root/'observer/events.json'; ep=root/'expected/assertions.json'
    observer=read_json(op); expected=read_json(ep)
    original=next(o for o in observer['events'] if o['event_id']=='tr-2')
    second=copy.deepcopy(original); second['id']='obs-conflict'
    second['fields']['fields.status']='success'
    if frankenstein:
        original['fields']['fields.exit_code']=0
    observer['events'].append(second)
    assertion=next(a for a in expected['assertions'] if a['event_id']=='tr-2')
    assertion['observation_ids'].append(second['id'])
    op.write_bytes(canonical(observer)); ep.write_bytes(canonical(expected)); rehash(root)
    with pytest.raises(ValueError,match='conflicting.*observations'):
        validate_bundle(root)


@pytest.mark.parametrize('mutation,event_id,finding',[
    ('missing_join','tr-2','unresolved_join'),
    ('branch_dangling','branch-fork','unresolved_join'),
    ('branch_cycle','branch-main','branch_cycle')])
def test_confirmed_failure_survives_relationship_uncertainty(tmp_path,mutation,event_id,finding):
    root=build_fixture(tmp_path/mutation,mutation=mutation)
    result,_=evaluate_bundle(root,decoder=decode_native)
    row=next(r for r in result['rows'] if r['id']=='event.'+event_id)
    assert row['state']=='fail'
    assert finding in row['findings']
    assert any(f['state']=='fail' for f in row['fields'])
    assert next(m for m in result['metrics'] if m['scope']==row['scenario'])['state']=='fail'


@pytest.mark.parametrize('field,value',[('harness','Codex'),('artifact_family','vendor-jsonl')])
def test_constructed_subject_cannot_be_relabelled(tmp_path,field,value):
    root=build_fixture(tmp_path/'subject')
    path=root/'manifest.json'; manifest=read_json(path)
    manifest['subject'][field]=value; path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError,match='exact constructed subject'):
        validate_bundle(root)


def test_registry_cannot_claim_measured_with_arbitrary_date():
    registry=read_json('registry/prototype.json')
    registry['surfaces'][0].update(status='measured',live_tested_at='unknown')
    with pytest.raises(ValueError,match='measured surface unsupported'):
        validate_registry(registry)
