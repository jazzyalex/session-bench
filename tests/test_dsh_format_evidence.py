"""Physical binding and denominator controls for the DSH broad profile."""
import copy
import json

import pytest

from session_bench.dsh_live import decode_dsh_native
from session_bench.dsh_format_evidence import build_dsh_format_evidence
from session_bench.v1_public_score import validate_format_evidence
from test_dsh_live import rows
from test_format_response_population import observer_document


def build(tmp_path, values=None, *, complete=True, mutate=None, cache_units=None):
    values = rows() if values is None else values
    for seq, row in enumerate(values[1:]):
        row.update(seq=seq, time=1000+seq)
    path=tmp_path/'session.v4.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in values))
    decoded=decode_dsh_native(path)
    if mutate:
        mutate(decoded, path)
    companion=None
    if cache_units is not None:
        buckets={'uncachedInputTokens':1,'outputTokens':2,'cacheReadTokens':3,'cacheWriteTokens':4}
        units={'tokenUsage':{'ver':2,'seq':max(r['sequence'] for r in decoded['records']),'val':{'totals':buckets,'last':{'turn':1,'step':1,'buckets':buckets}}},
               **{name:{'ver':1,'seq':0,'val':value} for name,value in cache_units.items()}}
        companion=tmp_path/'session-projcache.json'
        companion.write_text(json.dumps({'version':7,'record':{'identity':{'formatVersion':4,'createdAt':1,'cwd':'/tmp/test','isSeeded':False,'inheritedEventCount':0},'rows':units}}))
    return build_dsh_format_evidence(decoded,native_path=path,
        observer_document=json.dumps(observer_document()).encode(),run_id='test-run',repetition=1,
        build='0.test',collected_on='2026-09-29',
        native_manifest={'id':'manifest','sha256':'a'*64},complete_record_family=complete,native_companion=companion)


def test_forged_semantics_with_original_physical_hash_rejected(tmp_path):
    def mutate(decoded,path):
        decoded['responses'][0]['text']='invented'
    with pytest.raises(ValueError,match='semantic decode differs'):
        build(tmp_path,mutate=mutate)


def test_native_changed_after_decode_rejected(tmp_path):
    def mutate(decoded,path):
        path.write_text(path.read_text().replace('actual observed task','altered task'))
    with pytest.raises(ValueError,match='physical source differs'):
        build(tmp_path,mutate=mutate)


def test_incomplete_family_cannot_award_density_or_version(tmp_path):
    profile=validate_format_evidence(build(tmp_path,complete=False))['profile']
    metrics={m['id']:m for m in profile['metrics']}
    for name in ('classified_content_density','declared_format_version','honest_version_signal'):
        assert metrics['broad.'+name]['state']=='unresolved'


def test_complete_family_boolean_cannot_omit_known_persisted_session_cache(tmp_path):
    profile = validate_format_evidence(build(tmp_path, complete=True))['profile']
    density = next(row for row in profile['metrics'] if row['id'] == 'broad.classified_content_density')
    assert density['state'] == 'unresolved'


def test_reasoning_only_record_is_not_classified_as_visible_content(tmp_path):
    values=rows()
    extra=copy.deepcopy(values[5])
    extra['data']['message']['id']='reasoning-only'
    extra['data']['message']['content']=[{'type':'reasoning','text':'private reasoning'}]
    values.insert(5,extra)
    detail=build(tmp_path,values)['profile']['broad_evidence']['broad.classified_content_density']
    record=next(r for r in detail['records'] if r['record_id']=='line-6')
    assert record['classification']=='unknown'
    assert record['logical_bytes']>0


def test_assistant_tool_block_and_call_are_two_forward_occurrences(tmp_path):
    values=rows()
    extra=copy.deepcopy(values[5])
    extra['data']['message']['id']='call-plan'
    extra['data']['message']['content']=[{'type':'tool-call','id':'call','name':'bash','arguments':'{"command":"echo hello"}'}]
    values.insert(3,extra)
    detail=build(tmp_path,values)['profile']['broad_evidence']['broad.naive_reader_duplicate_safety']
    occurrences=[r for r in detail['forward_records'] if r['event_id']=='call:1:1:call']
    assert len(occurrences)==2
    assert len({r['occurrence_id'] for r in occurrences})==2
    # The first record that states the call keeps its role. The tool/call record that only repeats it is a snapshot.
    density={r['record_id']:r for r in build(tmp_path/'again',values)['profile']['broad_evidence']['broad.classified_content_density']['records']} if (tmp_path/'again').mkdir() is None else {}
    first,second=[r['occurrence_id'].split(':')[0] for r in occurrences]
    assert density[second]=={**density[second],'record_kind':'snapshot','classification':'unclassified'}
    assert density[first]['classification']=='useful'


def test_tool_call_record_that_first_states_its_call_stays_useful(tmp_path):
    document=build(tmp_path)
    calls=[r['occurrence_id'] for r in document['profile']['broad_evidence']['broad.naive_reader_duplicate_safety']['forward_records'] if r['event_id'].startswith('call:')]
    density={r['record_id']:r['record_kind'] for r in document['profile']['broad_evidence']['broad.classified_content_density']['records']}
    assert calls and all(density[line]=='tool_call' for line in calls)



def occurrences_of(document,event):
    return [r['occurrence_id'] for r in document['profile']['broad_evidence']['broad.naive_reader_duplicate_safety']['forward_records'] if r['event_id']==event]


def kinds_of(document):
    return {r['record_id']:r['record_kind'] for r in document['profile']['broad_evidence']['broad.classified_content_density']['records']}


def test_inbox_record_and_user_message_are_two_occurrences_of_one_prompt(tmp_path):
    values=rows()
    prompt=copy.deepcopy(values[2]['data'])
    values.insert(1,{'type':'agent/inbox/spliced','data':{'target':'next-turn','start':0,'inserted':[prompt]}})
    values.insert(2,{'type':'agent/inbox/spliced','data':{'target':'next-turn','start':0,'removedCount':1,'inserted':[]}})
    document=build(tmp_path,values)
    assert occurrences_of(document,'message:u')==['line-2','line-5']
    kinds=kinds_of(document)
    # The inbox record is the first record with the prompt. The user/message record only repeats it.
    assert (kinds['line-2'],kinds['line-3'],kinds['line-5'])==('user_message','metadata','snapshot')
    row=next(r for r in validate_format_evidence(document)['profile']['metrics'] if r['id']=='broad.naive_reader_duplicate_safety')
    assert row['correct']==row['observed_eligible']-1 and row['decoded_eligible']==row['observed_eligible']+1


def test_projection_cache_is_in_the_read_and_a_unit_that_holds_a_prompt_is_an_occurrence(tmp_path):
    plain=build(tmp_path/'plain',cache_units={'title':'a title'}) if (tmp_path/'plain').mkdir() is None else None
    assert occurrences_of(plain,'message:u')==['line-3'] and kinds_of(plain)['session-projcache-v7']=='metadata'
    copied=build(tmp_path/'copied',cache_units={'titleInput':'actual observed task'}) if (tmp_path/'copied').mkdir() is None else None
    assert occurrences_of(copied,'message:u')==['line-3','session-projcache-v7:titleInput']
    assert kinds_of(copied)['session-projcache-v7']=='snapshot'
    metrics={r['id']:r for r in validate_format_evidence(copied)['profile']['metrics']}
    assert metrics['broad.classified_content_density']['state']=='measured'


def test_result_record_with_all_arguments_of_its_call_restates_the_call(tmp_path):
    plain=build(tmp_path/'plain') if (tmp_path/'plain').mkdir() is None else None
    assert occurrences_of(plain,'call:1:1:call')==['line-4']  # the result of a shell call holds no command
    values=rows()
    values[3]['data'].update(name='edit',arguments=json.dumps({'file_path':'a.py','old_string':'x = 1','new_string':'x = 2'}))
    values[4]['data']['meta']={'diffs':[{'path':'a.py','oldText':'x = 1','newText':'x = 2'}]}
    (tmp_path/'edit').mkdir()
    edited=build(tmp_path/'edit',values)
    assert occurrences_of(edited,'call:1:1:call')==['line-4','line-5:arguments']
    assert kinds_of(edited)['line-5']=='tool_result'  # it still states its own result first
    values[4]['data']['meta']={'diffs':[{'path':'a.py','oldText':'x = 1'}]}  # only some arguments
    (tmp_path/'partial').mkdir()
    assert occurrences_of(build(tmp_path/'partial',values),'call:1:1:call')==['line-4']
