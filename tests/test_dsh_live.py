import copy
import json
import pytest

from session_bench.dsh_live import decode_dsh_native, stdout_projection, DSHSemanticError, helper_command
from session_bench.adapters.deepseek_harness import NativeInventoryError


def rows():
    return [
        {'type':'session','version':4,'id':'test','createdAt':1,'isSeeded':False,'delegationDepth':0,'cwd':'/tmp/test'},
        {'type':'turn/start','data':{'turn':1}},
        {'type':'user/message','surfaceOp':'append','data':{'id':'u','role':'user','source':{'kind':'user'},'content':[{'type':'text','text':'actual observed task'}]}},
        {'type':'tool/call','data':{'turn':1,'step':1,'callId':'call','name':'bash','arguments':'{"command":"echo hello"}'}},
        {'type':'tool/result','surfaceOp':'append','data':{'turn':1,'step':1,'message':{'id':'result','role':'tool','toolCallId':'call','source':{'kind':'tool','callId':'call'},'isError':False,'content':[{'type':'text','text':'hello'}]}}},
        {'type':'assistant/message','surfaceOp':'append','data':{'turn':1,'step':1,'message':{'id':'a','role':'assistant','source':{'kind':'model','model':'test'},'content':[{'type':'text','text':'done'}]},'usage':{'inputTokens':1,'outputTokens':2,'cacheReadTokens':3,'cacheWriteTokens':4}}},
        {'type':'turn/end','data':{'turn':1,'reason':{'kind':'completed'}}},
    ]


def decode(tmp_path, values):
    for seq,row in enumerate(values[1:]): row.update(seq=seq,time=1000+seq)
    path=tmp_path/'session.v4.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in values))
    return decode_dsh_native(path)


def test_native_has_no_observer_dependency_and_retains_raw_records(tmp_path):
    value=decode(tmp_path,rows())
    assert value['status']=='ok'
    assert len(value['records'])==6
    assert value['turns'][0]['text']=='actual observed task'
    assert value['responses'][0]['phase']=='final_answer'
    assert value['results'][0]['call_id']=='call'
    assert value['usage'][0]['tokens']=={'input':1,'output':2,'cache_read':3,'cache_write':4}
    assert 'reasoning' not in value['usage'][0]['tokens']


def test_runtime_user_context_not_counted_as_submitted_turn(tmp_path):
    values=rows();extra=copy.deepcopy(values[2]);extra['data']['id']='context';extra['data']['source']['kind']='runtime-context';values.insert(3,extra)
    value=decode(tmp_path,values)
    assert len(value['turns'])==1
    assert len(value['records'])==7


@pytest.mark.parametrize('kind',['future/required','compaction/summary'])
def test_unknown_or_unimplemented_semantics_never_looks_complete(tmp_path,kind):
    values=rows();values.insert(-1,{'type':kind,'data':{}})
    assert decode(tmp_path,values)['status']=='unsupported'


def test_missing_native_response_not_replaced_from_workload(tmp_path):
    values=rows();values.pop(5)
    assert decode(tmp_path,values)['responses']==[]


def test_result_cannot_join_across_turn_step(tmp_path):
    values=rows();values[4]['data']['step']=2
    with pytest.raises(DSHSemanticError,match='unmatched'):decode(tmp_path,values)


def test_bad_tool_boolean_rejected(tmp_path):
    values=rows();values[4]['data']['message']['isError']='false'
    with pytest.raises(DSHSemanticError,match='error status'):decode(tmp_path,values)


def test_nonfinal_text_is_retained_but_not_promoted_to_final(tmp_path):
    values=rows();extra=copy.deepcopy(values[5]);extra['data']['message']['id']='preface';values.insert(5,extra)
    value=decode(tmp_path,values)
    assert [x['phase'] for x in value['responses']]==['commentary','final_answer']
    assert len(value['commentary_relations'])==1


@pytest.mark.parametrize('index',[3,4,5,6])
def test_messages_tools_and_closers_outside_active_turn_rejected(tmp_path,index):
    values=rows();extra=copy.deepcopy(values[index])
    if index==5:extra['data']['message']['id']='late-assistant'
    values.append(extra)
    with pytest.raises(DSHSemanticError):decode(tmp_path,values)


def test_final_response_usage_is_not_sum_of_other_messages(tmp_path):
    values=rows();extra=copy.deepcopy(values[5]);extra['data']['message']['id']='preface'
    extra['data']['usage']['inputTokens']=100
    values.insert(5,extra)
    value=decode(tmp_path,values)
    assert len(value['usage_steps'])==2
    assert value['usage'][0]['scope']=='response'
    assert value['usage'][0]['tokens']['input']==1


@pytest.mark.parametrize('change',[lambda v:v.pop(),lambda v:v[1].update(truncated=True),lambda v:v.insert(1,{'type':'session','sessionId':'other'})])
def test_stdout_requires_complete_untruncated_single_session(change):
    values=[{'type':'session','sessionId':'s'},{'type':'text','text':'hello'},{'type':'final','text':'hello'}]
    change(values)
    with pytest.raises(DSHSemanticError):stdout_projection('\n'.join(json.dumps(x) for x in values))


def test_helper_normalizer_cannot_hide_other_shell_actions():
    prefix='python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_test'
    assert helper_command(prefix+'; echo "[exit code: $?]"')==prefix
    assert helper_command(prefix+'; rm checkout.py') is None


def _two_turn_edit_rows(old='return total + 5', new='return total'):
    import hashlib
    source='def checkout(items):\n    total = 0\n    return total + 5\n'
    inspect='SB_SURVIVAL_V1_HELPER_INSPECT_nonce '+json.dumps({'checkout_sha256':hashlib.sha256(source.encode()).hexdigest(),'checkout_source':source,'phase':'inspect'})
    def assistant(turn, identity, text):
        return {'type':'assistant/message','surfaceOp':'append','data':{'turn':turn,'step':1,'message':{'id':identity,'role':'assistant','source':{'kind':'model','model':'test'},'content':[{'type':'text','text':text}]},'usage':{'inputTokens':1,'outputTokens':2,'cacheReadTokens':3,'cacheWriteTokens':4}}}
    def call(turn, identity, name, arguments):
        return {'type':'tool/call','data':{'turn':turn,'step':1,'callId':identity,'name':name,'arguments':json.dumps(arguments)}}
    def result(turn, identity, call_id, text):
        return {'type':'tool/result','surfaceOp':'append','data':{'turn':turn,'step':1,'message':{'id':identity,'role':'tool','toolCallId':call_id,'source':{'kind':'tool','callId':call_id},'isError':False,'content':[{'type':'text','text':text}]}}}
    def user(identity, text):
        return {'type':'user/message','surfaceOp':'append','data':{'id':identity,'role':'user','source':{'kind':'user'},'content':[{'type':'text','text':text}]}}
    return source,[
        {'type':'session','version':4,'id':'test','createdAt':1,'isSeeded':False,'delegationDepth':0,'cwd':'/tmp/test'},
        {'type':'turn/start','data':{'turn':1}}, user('u1','first task'),
        call(1,'inspect','bash',{'command':'python3 bench_check.py inspect','workdir':'/tmp/test/fixture_project'}), result(1,'r-inspect','inspect',inspect),
        assistant(1,'a1','inspected'), {'type':'turn/end','data':{'turn':1,'reason':{'kind':'completed'}}},
        {'type':'turn/start','data':{'turn':2}}, user('u2','second task'),
        call(2,'edit','edit',{'file_path':'/tmp/test/fixture_project/checkout.py','old_string':old,'new_string':new}), result(2,'r-edit','edit','edited'),
        assistant(2,'a2','changed'), {'type':'turn/end','data':{'turn':2,'reason':{'kind':'completed'}}},
    ]


def test_changed_file_hashes_come_from_the_native_inspect_source_and_edit_call(tmp_path):
    import hashlib
    source,values=_two_turn_edit_rows()
    change=decode(tmp_path,values)['file_changes'][0]
    assert change['before_sha256']==hashlib.sha256(source.encode()).hexdigest()
    assert change['after_sha256']==hashlib.sha256(source.replace('return total + 5','return total').encode()).hexdigest()


def test_changed_file_stays_unhashed_when_the_native_edit_text_matches_twice(tmp_path):
    _,values=_two_turn_edit_rows(old='total',new='sum')
    change=decode(tmp_path,values)['file_changes'][0]
    assert 'before_sha256' not in change and 'after_sha256' not in change
