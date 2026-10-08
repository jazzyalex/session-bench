#!/usr/bin/env python3
"""Private successor diagnostics for an already-captured Antigravity session."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from session_bench.antigravity_live import decode_antigravity_native, stdout_projection
from session_bench.live_observer import build_opencode_live_observer
from session_bench.live_metric_comparator import compare_survival_run
from session_bench.v1_public_score import format_profile_document,validate_format_profile

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,obj):
    with path.open('x') as f:json.dump(obj,f,ensure_ascii=False,sort_keys=True,indent=2);f.write('\n')
def qualify(attempt,repetition):
    root=ROOT/'artifacts/v1-expanded-preparation/live-captures'
    run=root/attempt
    if run.resolve().parent!=root.resolve():raise ValueError('invalid capture path')
    state=json.loads((run/'capture-result.json').read_text())
    if state['status']!='captured_pending_qualification':raise ValueError('incomplete capture')
    native=run/'r2-native'/state['native_primary']
    receipt=json.loads((run/'r2-native-receipt.json').read_text())
    # Every copied member must still match its acquisition receipt.
    for entry in receipt['artifacts']:
        candidate=run/'r2-native'/entry['relative_path']
        if sha(candidate)!=entry['sha256']:raise ValueError('acquired native member changed')
    workload=json.loads((run/'workload.json').read_text())
    fixture=run/'workspace/fixture_project'
    helper=ROOT/'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
    if sha(fixture/'bench_check.py')!=sha(helper) or sha(fixture/'checkout.py')!=state['after_sha256']:
        raise ValueError('workspace changed after capture')
    ledger=(fixture/'.survival-observer.jsonl').read_text();observed=[json.loads(l) for l in ledger.splitlines()]
    if [(r['phase'],r['exit_code']) for r in observed]!=[('inspect',0),('baseline',1),('final',0)]:
        raise ValueError('independent helper sequence incomplete')
    streams={i:stdout_projection((run/f'r{i}.stdout.jsonl').read_text(),observed) for i in (1,2)}
    if streams[1]['session_id']!=streams[2]['session_id'] or state['native_primary'].split('/')[0]!=streams[1]['session_id']:
        raise ValueError('native capture and stdout session mismatch')
    observer=build_opencode_live_observer(workload=workload,controller_state={
        'turns':{str(i):{'session_id':streams[i]['session_id']} for i in (1,2)},
        'model':'unobserved-default','configuration':'antigravity-cli-default','workspace':str(run/'workspace')},
        stdout_by_turn={i:streams[i]['jsonl'] for i in (1,2)},helper_ledger_jsonl=ledger,
        before_checkout_sha256=state['before_sha256'],after_checkout_sha256=state['after_sha256'])
    # The installed CLI's selected default model was not exposed in this trace.
    for event in observer['events']:
        for key in ('model_id','configuration'):event['fields'].pop(key,None)
        # Step IDs identify this stdout projection, not native tool-call IDs.
        # Keep the source locator without imposing a false cross-format join.
        local_id=event['fields'].pop('call_id',None)
        if local_id is not None:event['fields']['stdout_step_id']=local_id
    observer['method']='Submitted workload, stdout steps and final response, protected helper ledger and filesystem hashes; helper exit codes from separate ledger; model and response-scoped tokens unobserved; completed edits are logical operations, not process exit codes.'
    decoded=decode_antigravity_native(native)
    portability={k:None for k in ('complete_root','companions_present','canonical_equality','isolated_decode')}
    measurement=compare_survival_run(observer,decoded,portability,configuration_id='antigravity',repetition=repetition)
    # Source order binds turns. No tool-call ID is present in GENERIC output;
    # do not manufacture a causal link from adjacency or desired helper phase.
    broad=validate_format_profile(format_profile_document(run_id=workload['run_id'],configuration_id='antigravity',repetition=repetition))
    output=run/'qualification-v4';output.mkdir(exist_ok=False)
    shutil.copytree(run/'r2-native',output/'native')
    shutil.copyfile(helper,output/'bench_check.py')
    for name in ('workload.json','capture-result.json','r1.stdout.jsonl','r2.stdout.jsonl','r2-native-receipt.json'):
        shutil.copyfile(run/name,output/name)
    (output/'helper-ledger.jsonl').write_text(ledger)
    for name,value in [('decoded.json',decoded),('observer.json',observer),('measurement.json',measurement),('broad-unresolved.json',broad),('portability.json',portability)]:write(output/name,value)
    files=[{'path':p.relative_to(output).as_posix(),'sha256':sha(p),'size_bytes':p.stat().st_size} for p in sorted(output.rglob('*')) if p.is_file()]
    write(output/'manifest.json',{'schema_version':'session-bench-antigravity-diagnostic-v1','attempt_id':attempt,'repetition':repetition,
        'files':files,'source_sha256':{rel:sha(ROOT/rel) for rel in ('session_bench/antigravity_live.py','session_bench/live_observer.py','session_bench/live_metric_comparator.py','scripts/qualify_antigravity_capture.py')},
        'public_safe':False,'independently_reproduced':False,'score_eligible':False,
        'limits':['default model identity unobserved','complete native family not proven','GENERIC results lack explicit tool call joins','twelve broad metrics unqualified']})
    print(json.dumps({'attempt_id':attempt,'status':'private_diagnostics','native_events':len(decoded['records']),'manifest_sha256':sha(output/'manifest.json'),'states':{r['id']:r['state'] for r in measurement['metrics']}}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt-id',required=True);p.add_argument('--repetition',type=int,choices=(1,2,3),required=True)
    a=p.parse_args();qualify(a.attempt_id,a.repetition)
