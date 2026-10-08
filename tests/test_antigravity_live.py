import json
import pytest
from session_bench.antigravity_live import decode_antigravity_native,stdout_projection


def native(tmp_path,rows):
    path=tmp_path/'transcript.jsonl'
    path.write_text(''.join(json.dumps(dict(step_index=i,source='MODEL',status='DONE',created_at='2026-09-29T00:00:00Z',**r))+'\n' for i,r in enumerate(rows)))
    return decode_antigravity_native(path)


def test_request_wrapper_retains_metadata_without_counting_it_as_submitted_text(tmp_path):
    text='<USER_REQUEST>\nactual task\n</USER_REQUEST>\n<ADDITIONAL_METADATA>clock</ADDITIONAL_METADATA>'
    decoded=native(tmp_path,[{'type':'USER_INPUT','content':text},{'type':'PLANNER_RESPONSE','content':'done'}])
    assert decoded['turns'][0]['text']=='actual task'
    assert decoded['records'][0]['raw']['content']==text
    assert decoded['responses'][0]['turn_id']==decoded['turns'][0]['id']


def test_ambiguous_wrapper_fails_closed(tmp_path):
    with pytest.raises(ValueError,match='ambiguous'):
        native(tmp_path,[{'type':'USER_INPUT','content':'<USER_REQUEST>\na<USER_REQUEST>b</USER_REQUEST>'}])


def test_generic_result_has_no_invented_tool_join_or_native_usage(tmp_path):
    decoded=native(tmp_path,[{'type':'USER_INPUT','content':'task'},
       {'type':'PLANNER_RESPONSE','tool_calls':[{'name':'run_command','args':{'CommandLine':'"echo hi"'}}]},
       {'type':'GENERIC','content':'The command exited with code 0.\nOutput:\nhi'},
       {'type':'PLANNER_RESPONSE','content':'done'}])
    assert decoded['actions'][0]['argv']==['echo','hi']
    assert decoded['results'][0]['exit_code']==0
    assert 'call_id' not in decoded['results'][0]
    assert all(r['kind']=='turn_response' for r in decoded['relations'])
    assert decoded['usage']==[]


def test_unknown_native_kind_cannot_claim_complete(tmp_path):
    decoded=native(tmp_path,[{'type':'USER_INPUT','content':'task'},{'type':'FUTURE_REWRITE','content':'opaque'}])
    assert decoded['status']=='unsupported'


def test_stdout_aggregate_usage_is_not_response_scoped_usage():
    rows=[{'event':'init','conversation_id':'s'},{'event':'result','result':{
        'conversation_id':'s','status':'SUCCESS','response':'done','usage':{'input_tokens':999}}}]
    value=stdout_projection('\n'.join(map(json.dumps,rows)),[])
    assert 'step_finish' not in value['jsonl'] and '999' not in value['jsonl']


def test_stdout_other_session_rejected():
    rows=[{'event':'init','conversation_id':'s'},{'event':'result','result':{'conversation_id':'other','status':'SUCCESS','response':'done'}}]
    with pytest.raises(ValueError):stdout_projection('\n'.join(map(json.dumps,rows)),[])


def test_native_response_canary_is_not_confused_with_prior_run_marker(tmp_path):
    decoded=native(tmp_path,[{'type':'USER_INPUT','content':'task'},
        {'type':'PLANNER_RESPONSE','content':'Ran helper --run-canary SB_SURVIVAL_V1_RUN_example\nDone\nSB_SURVIVAL_V1_RESPONSE_R2_example\n'}])
    assert decoded['responses'][0]['canary']=='SB_SURVIVAL_V1_RESPONSE_R2_example'


def test_multiple_native_response_markers_rejected(tmp_path):
    with pytest.raises(ValueError,match='ambiguous native response'):
        native(tmp_path,[{'type':'USER_INPUT','content':'task'},
            {'type':'PLANNER_RESPONSE','content':'SB_SURVIVAL_V1_RESPONSE_R1_a SB_SURVIVAL_V1_RESPONSE_R2_a'}])


def full_file_read(text, path='/workspace/fixture_project/checkout.py'):
    lines = text.splitlines()
    return (f'Created At: clock\nFile Path: `file://{path}`\nTotal Lines: {len(lines)}\n'
            f'Total Bytes: {len(text.encode())}\nShowing lines 1 to {len(lines)}\n'
            'The following code has been modified to include a line number before every line.\n'
            + '\n'.join(f'{i}: {line}' for i, line in enumerate(lines, 1))
            + '\nThe above content shows the entire, complete file contents of the requested file.\n')


def completed_diff(diff, path='/workspace/fixture_project/checkout.py'):
    return ('Created At: clock\nThe following changes were made by the replace_file_content tool to: '
            + path + '. If relevant, run commands.\n[diff_block_start]\n' + diff
            + '\n[diff_block_end]\n')


def test_completed_full_file_diff_proves_hashes_without_action_result_join(tmp_path):
    import hashlib
    before = 'one\ntwo\n'
    after = 'one\nthree\n'
    decoded = native(tmp_path, [
        {'type': 'USER_INPUT', 'content': 'task'},
        {'type': 'GENERIC', 'content': full_file_read(before)},
        {'type': 'GENERIC', 'content': completed_diff('@@ -1,2 +1,2 @@\n one\n-two\n+three')},
    ])
    change, = decoded['file_changes']
    assert change['path'] == 'fixture_project/checkout.py'
    assert change['before_sha256'] == hashlib.sha256(before.encode()).hexdigest()
    assert change['after_sha256'] == hashlib.sha256(after.encode()).hexdigest()
    assert change['preimage_locator']['step_index'] == 1
    assert change['locator']['step_index'] == 2
    assert 'action_id' not in change and 'call_id' not in change
    assert decoded['relations'] == []


@pytest.mark.parametrize('read, diff', [
    (full_file_read('one\ntwo\n').replace('Total Bytes: 8', 'Total Bytes: 9'), '@@ -1,2 +1,2 @@\n one\n-two\n+three'),
    (full_file_read('one\ntwo\n'), '@@ -1,1 +1,1 @@\n-one\n+three'),
    (full_file_read('one\ntwo\n'), '@@ -1,2 +1,2 @@\n one\n-other\n+three'),
    (full_file_read('one\ntwo\n'), '@@ -1,2 +1,3 @@\n one\n-two\n+three'),
    (full_file_read('one\ntwo\n', '/other/file.py'), '@@ -1,2 +1,2 @@\n one\n-two\n+three'),
])
def test_unproven_full_file_diff_cannot_supply_changed_file_hashes(tmp_path, read, diff):
    decoded = native(tmp_path, [
        {'type': 'USER_INPUT', 'content': 'task'},
        {'type': 'GENERIC', 'content': read},
        {'type': 'GENERIC', 'content': completed_diff(diff)},
    ])
    assert decoded['file_changes'] == []


def test_edit_intent_does_not_supply_changed_file_hashes(tmp_path):
    decoded = native(tmp_path, [
        {'type': 'USER_INPUT', 'content': 'task'},
        {'type': 'GENERIC', 'content': full_file_read('one\ntwo\n')},
        {'type': 'PLANNER_RESPONSE', 'tool_calls': [{'name': 'replace_file_content', 'args': {
            'TargetFile': '"/workspace/fixture_project/checkout.py"',
            'TargetContent': '"one\\ntwo\\n"', 'ReplacementContent': '"one\\nthree\\n"'}}]},
    ])
    assert decoded['file_changes'] == []


@pytest.mark.parametrize('suffix', ['02', '03', '04'])
@pytest.mark.parametrize('missing', ['complete_read', 'completed_diff'])
def test_retained_native_file_change_requires_both_exact_witnesses(tmp_path, suffix, missing):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    capture = root / f'artifacts/v1-expanded-preparation/live-captures/antigravity-2026-09-29-{suffix}/qualification-v4'
    state = json.loads((capture / 'capture-result.json').read_text())
    source = capture / 'native' / state['native_primary']
    intact = decode_antigravity_native(source)
    change, = intact['file_changes']
    assert change['before_sha256'] == state['before_sha256']
    assert change['after_sha256'] == state['after_sha256']
    removed_step = change['preimage_locator' if missing == 'complete_read' else 'locator']['step_index']
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    damaged = tmp_path / 'transcript.jsonl'
    damaged.write_text(''.join(json.dumps(row) + '\n' for row in rows if row['step_index'] != removed_step))
    assert decode_antigravity_native(damaged)['file_changes'] == []
