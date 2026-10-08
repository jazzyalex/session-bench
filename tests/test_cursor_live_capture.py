"""Single-use fresh capture vectors and runtime hard stops without model calls."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.cursor_live_capture import prepare_cursor_capture, execute_cursor_capture

SESSION = "8c89839e-d4d5-40df-b27a-ffa88429fa09"


def prepared(tmp_path):
    repository = tmp_path / "repo";destination = repository / "artifacts/survival-v1-runs";destination.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload"
    shutil.copytree(source, repository / "fixtures/scenarios/survival-v1/workload")
    root = destination / "cursor-live-test"
    prepare_cursor_capture(root, attempt_id=root.name, repetition=1, repository=repository)
    return root


def runner(calls, *, fault=None):
    def execute(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(argv)
        turn = len(calls);root = cwd.parent
        assert env['CURSOR_CONFIG_DIR'] == str(root / 'cursor-config')
        assert env['CURSOR_DATA_DIR'] == str(root / 'cursor-data')
        assert '--sandbox' in argv and 'enabled' in argv
        canary = 'SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂' if turn == 1 else 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ'
        identity = SESSION if fault != 'session' or turn == 1 else 'b4b9a625-d88c-4bf4-b0f2-94e60a639ed2'
        result = 'Finished '+canary
        if fault == 'canary' and turn == 1:result = 'Missing marker'
        rows = [{'type':'system','subtype':'init','session_id':identity}, {'type':'result','subtype':'success','session_id':identity,'result':result}]
        stdout.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows));stderr.write_text('usage limit reached' if fault == 'quota' else '')
        if fault == 'helper' and turn == 1:(cwd / 'fixture_project/bench_check.py').write_text('altered')
        if fault == 'early_edit' and turn == 1:(cwd / 'fixture_project/checkout.py').write_text('altered')
        if fault == 'timeout':raise ValueError('timed out')
        if fault == 'nonzero':return 1
        if turn == 2 and fault != 'native_missing':
            store = root / 'cursor-config/chats/hash' / SESSION;store.mkdir(parents=True)
            (store / 'store.db').write_bytes(b'copied native bytes');(store / 'meta.json').write_text('{}')
            transcript = root / 'cursor-data/projects/project/agent-transcripts' / SESSION;transcript.mkdir(parents=True)
            (transcript / (SESSION+'.jsonl')).write_text('{"native":"only this session"}\n')
        return 0
    return execute


def test_prepare_is_no_submission_and_excludes_observer_answer_snapshots(tmp_path):
    root = prepared(tmp_path);plan = json.loads((root / 'plan.json').read_bytes())
    assert plan['model_submissions'] == 0 and plan['state'] == 'prepared'
    assert not (root / 'project/fixture_project/snapshots').exists()
    assert not (root / 'controller-state.json').exists()
    with pytest.raises(ValueError,match='exists'):
        prepare_cursor_capture(root,attempt_id=root.name,repetition=1,repository=root.parents[2])


def test_two_turns_use_exact_session_resume_and_copy_only_isolated_native_bytes(tmp_path, monkeypatch):
    monkeypatch.delenv('CURSOR_API_KEY',raising=False);monkeypatch.delenv('CURSOR_API_ENDPOINT',raising=False)
    root = prepared(tmp_path);calls=[]
    state = execute_cursor_capture(root,runner=runner(calls))
    assert state['state'] == 'captured_private_unqualified' and state['model_submissions'] == 2
    assert not state['score_eligible']
    assert '--resume' not in calls[0]
    assert calls[1][calls[1].index('--resume')+1] == SESSION
    manifest = json.loads((root / 'capture/native-manifest.private.json').read_bytes())
    assert {row['path'] for row in manifest['files']} == {'acp/store.db','acp/meta.json','cursor-session.jsonl'}
    assert manifest['complete_native_root'] is manifest['public_safe'] is False
    with pytest.raises(ValueError,match='single-use'):
        execute_cursor_capture(root,runner=runner([]))


@pytest.mark.parametrize('fault,submissions',[('canary',1),('quota',1),('helper',1),('early_edit',1),('timeout',1),('nonzero',1),('session',2),('native_missing',2)])
def test_runtime_or_capture_gap_is_preserved_and_never_retried(tmp_path,monkeypatch,fault,submissions):
    monkeypatch.delenv('CURSOR_API_KEY',raising=False);monkeypatch.delenv('CURSOR_API_ENDPOINT',raising=False)
    root=prepared(tmp_path);calls=[];state=execute_cursor_capture(root,runner=runner(calls,fault=fault))
    assert state['state']=='invalid' and state['model_submissions']==len(calls)==submissions
    assert state['reason_ids'] and not state['score_eligible']
    assert (root/'observer/turn-r1.stdout.jsonl').exists()


@pytest.mark.parametrize('key',['CURSOR_API_KEY','CURSOR_API_ENDPOINT'])
def test_subscription_route_cannot_be_replaced_by_environment_override(tmp_path,monkeypatch,key):
    root=prepared(tmp_path);monkeypatch.setenv(key,'test-override')
    with pytest.raises(ValueError,match='subscription'):
        execute_cursor_capture(root,runner=runner([]))
    assert not (root/'controller-state.json').exists()


def test_tampered_plan_cannot_execute_another_workspace(tmp_path,monkeypatch):
    monkeypatch.delenv('CURSOR_API_KEY',raising=False);monkeypatch.delenv('CURSOR_API_ENDPOINT',raising=False)
    root=prepared(tmp_path);plan=json.loads((root/'plan.json').read_bytes());plan['argv_base']=['dangerous-command']
    (root/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError,match='command'):
        execute_cursor_capture(root,runner=runner([]))
    assert not (root/'controller-state.json').exists()


def test_normal_root_captures_only_proven_new_family_without_opening_old_contents(tmp_path,monkeypatch):
    from session_bench import normal_root_capture
    for name in ('CURSOR_API_KEY','CURSOR_API_ENDPOINT','CURSOR_CONFIG_DIR','CURSOR_DATA_DIR'):
        monkeypatch.delenv(name,raising=False)
    root=prepared(tmp_path)
    # Re-prepare under a new attempt with explicitly authorized fake normal
    # root. Old files are content traps; only metadata may be inspected.
    normal=tmp_path/'normal';(normal/'chats').mkdir(parents=True);(normal/'projects').mkdir()
    (normal/'chats/old-private.db').write_bytes(b'old private bytes must never be opened')
    repository=root.parents[2];root=repository/'artifacts/survival-v1-runs/cursor-normal-test'
    prepare_cursor_capture(root,attempt_id=root.name,repetition=2,repository=repository,normal_root=normal)
    original=normal_root_capture._open_relative_regular
    opened=[]
    def tracked(source,name):
        assert 'old-private' not in name
        opened.append(name)
        return original(source,name)
    monkeypatch.setattr(normal_root_capture,'_open_relative_regular',tracked)
    calls=[]
    def execute(argv,*,cwd,env,stdout,stderr,timeout):
        assert 'CURSOR_CONFIG_DIR' not in env and 'CURSOR_DATA_DIR' not in env
        calls.append(argv)
        turn=len(calls);canary='SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂' if turn==1 else 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ'
        stdout.write_text(json.dumps({'type':'result','subtype':'success','session_id':SESSION,'result':'Done '+canary},ensure_ascii=False)+'\n');stderr.write_text('')
        if turn==2:
            store=normal/'chats'/hashlib.md5(str(cwd).encode()).hexdigest()/SESSION;store.mkdir(parents=True)
            (store/'store.db').write_bytes(b'new database');(store/'meta.json').write_text('{}')
            transcript=normal/'projects'/str(cwd).replace('/','-').lstrip('-')/'agent-transcripts'/SESSION;transcript.mkdir(parents=True)
            (transcript/(SESSION+'.jsonl')).write_text('{"new":true}\n')
        return 0
    state=execute_cursor_capture(root,runner=execute)
    assert state['state']=='captured_private_unqualified' and len(calls)==2
    assert len(opened)==3
    assert (normal/'chats/old-private.db').read_bytes()==b'old private bytes must never be opened'
    receipt=json.loads((root/'capture/normal-root-receipts.private.json').read_bytes())
    assert all(row['old_file_contents_opened'] is False for row in receipt.values())
