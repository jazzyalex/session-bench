"""Measurement assertions that must distinguish preservation from reader failure."""
import copy
import json
from pathlib import Path

import pytest

from session_bench.bundle import canonical, digest, validate_bundle
from session_bench.decoders import decode_native
from session_bench.evaluate import compare, evaluate_bundle, evaluate_decoded, MISSING
from session_bench.fixtures import build_fixture


def test_storage_layout_does_not_change_assertion_outcomes(tmp_path):
    results=[]
    for fmt in ('constructed-jsonl-v1','constructed-sqlite-v1'):
        bundle=build_fixture(tmp_path/fmt,fmt)
        result,_=evaluate_bundle(bundle,decoder=decode_native)
        for row in result['rows']:
            row.pop('locators')
        results.append(result)
    assert results[0]['rows']==results[1]['rows']
    assert results[0]['metrics']==results[1]['metrics']
    assert all(row['state']=='pass' for row in results[0]['rows'])


def test_answer_key_mutation_cannot_change_native_decoding(tmp_path):
    bundle=build_fixture(tmp_path/'input')
    manifest,expectations,_=validate_bundle(bundle)
    before=decode_native(bundle/'native')
    altered=copy.deepcopy(expectations)
    field=next(f for a in altered['assertions'] if a['event_id']=='m-1' for f in a['fields'] if f['name']=='fields.text')
    field['expected']='ANSWER-KEY-ONLY-MARKER'
    after=decode_native(bundle/'native')
    assert canonical(before)==canonical(after)
    assert b'ANSWER-KEY-ONLY-MARKER' not in canonical(after)
    result=evaluate_decoded(manifest,altered,after,digest(canonical(manifest)))
    row=next(r for r in result['rows'] if r['id']=='event.m-1')
    assert row['state']=='fail' and 'contradicted' in row['findings']


def test_removed_fact_never_reconstructed_from_observer(tmp_path):
    bundle=build_fixture(tmp_path/'loss',mutation='remove_fact')
    result,decoded=evaluate_bundle(bundle,decoder=decode_native)
    assert not any(e['id']=='tr-2' for e in decoded['events'])
    row=next(r for r in result['rows'] if r['id']=='event.tr-2')
    assert row['state']=='fail' and row['outcome']=='verified_absent'
    assert row['observation_ids']


def test_retained_fact_with_broken_decoder_is_not_writer_loss(tmp_path):
    bundle=build_fixture(tmp_path/'reader')
    manifest,expected,_=validate_bundle(bundle)
    decoded=decode_native(bundle/'native')
    event=next(e for e in decoded['events'] if e['id']=='tr-2')
    assertion=next(a for a in expected['assertions'] if a['event_id']=='tr-2')
    assertion['inspection']={'state':'present','locators':[event['locator']],'evidence_ids':['review-fixture-evidence']}
    decoded['events'].remove(event)
    result=evaluate_decoded(manifest,expected,decoded,digest(canonical(manifest)))
    row=next(r for r in result['rows'] if r['id']=='event.tr-2')
    assert row['outcome']=='retained_decoder_incomplete'
    assert row['state']=='fail'
    assert row['locators']==[event['locator']]
    assert 'loss' not in row['findings']


def test_missing_data_remains_in_denominator(tmp_path):
    bundle=build_fixture(tmp_path/'missing')
    manifest,expected,_=validate_bundle(bundle)
    expected['assertions'][0]['execution']='not_exercised'
    result=evaluate_decoded(manifest,expected,decode_native(bundle/'native'),digest(canonical(manifest)))
    assert sum(m['denominator'] for m in result['metrics'])==len(expected['assertions'])
    assert result['rows'][0]['state']=='unresolved'
    expected['assertions']=[]
    empty=evaluate_decoded(manifest,expected,decode_native(bundle/'native'),digest(canonical(manifest)))
    assert empty['metrics']==[{'scope':'all','unit':'assertions','numerator':0,'denominator':0,'state':'unresolved','assertion_ids':[]}]


def test_field_contract_preserves_code_types_and_uncertainty():
    assert compare('  return x  \n','return x\n','text_lf')=='fail'
    assert compare('a\r\nb','a\nb','text_lf')=='pass'
    assert compare('a\rb','a\nb','text_lf')=='fail'
    assert compare({'b':2,'a':1},{'a':1,'b':2},'json')=='pass'
    assert compare([1,2],[2,1],'json')=='fail'
    assert compare(True,1,'exact')=='fail'
    assert compare(None,None,'exact')=='pass'
    assert compare(MISSING,None,'exact')=='fail'
    assert compare('wrapped','wrapped','presentation_unknown')=='unresolved'


def test_full_event_fails_when_only_identity_matches(tmp_path):
    bundle=build_fixture(tmp_path/'status',mutation='wrong_status')
    result,_=evaluate_bundle(bundle,decoder=decode_native)
    row=next(r for r in result['rows'] if r['id']=='event.tr-2')
    assert next(f for f in row['fields'] if f['name']=='id')['state']=='pass'
    assert row['state']=='fail'
    assert any(f['name']=='fields.status' and f['state']=='fail' for f in row['fields'])


def test_incomplete_source_or_observer_coverage_cannot_pass(tmp_path):
    for index,group in enumerate(('capture','observation')):
        bundle=build_fixture(tmp_path/str(index))
        manifest=json.loads((bundle/'manifest.json').read_text())
        manifest[group]['roots_complete' if group=='capture' else 'complete']=False
        (bundle/'manifest.json').write_bytes(canonical(manifest))
        result,_=evaluate_bundle(bundle,decoder=decode_native)
        assert all(r['state']=='unresolved' for r in result['rows'])
        assert all(m['state']=='unresolved' and m['numerator']==0 for m in result['metrics'])


@pytest.mark.parametrize('fmt',['constructed-jsonl-v1','constructed-sqlite-v1'])
def test_forged_native_locator_cannot_support_a_pass(tmp_path,fmt):
    bundle=build_fixture(tmp_path/fmt,fmt)
    def bad_decoder(package):
        result=decode_native(package)
        result['events'][0]['locator']['sha256']='0'*64
        return result
    with pytest.raises(ValueError,match='invalid decoder source locator'):
        evaluate_bundle(bundle,decoder=bad_decoder)


def test_wrong_jsonl_coordinates_cannot_support_a_pass(tmp_path):
    bundle=build_fixture(tmp_path/'input')
    def bad_decoder(package):
        result=decode_native(package)
        result['events'][0]['locator']['line']=999
        return result
    with pytest.raises(ValueError,match='JSONL range'):
        evaluate_bundle(bundle,decoder=bad_decoder)
