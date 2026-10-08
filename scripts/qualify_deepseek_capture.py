#!/usr/bin/env python3
"""Create a private successor diagnostic packet from one selected DSH capture."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.adapters.deepseek_harness import inventory_native_root
from session_bench.dsh_live import decode_dsh_native, stdout_projection
from session_bench.live_observer import build_opencode_live_observer
from session_bench.live_metric_comparator import compare_survival_run
from session_bench.dsh_format_evidence import build_dsh_format_evidence
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_score import score_release_run


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x') as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write('\n')


def qualify(attempt, repetition):
    run = ROOT / 'artifacts/survival-v1-runs' / attempt
    if not run.is_dir() or run.resolve().parent != (ROOT/'artifacts/survival-v1-runs').resolve():
        raise ValueError('expected selected direct run directory')
    state = json.loads((run/'capture-result.json').read_text())
    if state.get('status') != 'captured_pending_qualification' or state['attempt_id'] != attempt:
        raise ValueError('capture is not a completed selected attempt')
    streams = {i: stdout_projection((run/f'r{i}.stdout.jsonl').read_text()) for i in (1,2)}
    session = streams[1]['session_id']
    if streams[2]['session_id'] != session:
        raise ValueError('R1/R2 session mismatch')
    native_root = run/'dsh-home/sessions'
    inventory = inventory_native_root(native_root, run_root=run, expected_session_id=session)
    if inventory.status != 'inventory_only':
        raise ValueError('native inventory includes unsupported records')
    native = native_root/inventory.relative_path
    if digest(native) != inventory.sha256:
        raise ValueError('native changed after inventory')
    decoded = decode_dsh_native(native)
    if decoded['status'] != 'ok':
        raise ValueError('native semantics unsupported')
    workload = json.loads((run/'workload.json').read_text())
    fixture = run/'workspace/fixture_project'
    if digest(fixture/'checkout.py') != state['after_sha256']:
        raise ValueError('final workspace changed after capture')
    frozen_helper = ROOT/'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py'
    if digest(fixture/'bench_check.py') != digest(frozen_helper):
        raise ValueError('protected helper changed')
    ledger = (fixture/'.survival-observer.jsonl').read_text()
    helper = [json.loads(line) for line in ledger.splitlines()]
    if [r['phase'] for r in helper] != ['inspect','baseline','final'] or helper[-1]['exit_code'] != 0:
        raise ValueError('independent helper does not show the required successful sequence')
    controller = {'turns': {str(i): {'session_id': streams[i]['session_id']} for i in (1,2)},
                  'model':'deepseek-flash', 'configuration':'deepseek-harness-cli', 'workspace':str(run/'workspace')}
    observer = build_opencode_live_observer(workload=workload,controller_state=controller,
        stdout_by_turn={i:streams[i]['jsonl'] for i in (1,2)},helper_ledger_jsonl=ledger,
        before_checkout_sha256=state['before_sha256'],after_checkout_sha256=state['after_sha256'])
    observer['method'] = ('submitted prompts, captured DSH stdout, protected helper ledger and filesystem hashes; '
                          'stdout is a vendor committed-event projection, not independent instrumentation of its event producer; '
                          'native decoder receives no observer or filesystem hashes; missing reasoning-token values are not guessed')
    output = run/'qualification-v5'
    output.mkdir(exist_ok=False)
    copied = output/'native'
    copied.mkdir()
    shutil.copyfile(native, copied/native.name)
    if digest(copied/native.name) != inventory.sha256:
        raise ValueError('copied native bytes mismatch')
    copied_decode = decode_dsh_native(copied/native.name)
    if copied_decode != decoded:
        raise ValueError('copied native decode differs')
    # This proves copied-file equality only. Fresh-process closed-runtime replay
    # and independent reproduction remain separate gates.
    receipt = {'complete_root': True, 'companions_present': True,
               'canonical_equality': True, 'supported': True,
               'method':'fresh isolated DSH_HOME sessions inventory; one v4 generation plus empty session.lock; same-process copied-file decode',
               'independent':False,'isolated_decode':None}
    measurement = compare_survival_run(observer,decoded,receipt,configuration_id='deepseek-harness-cli',repetition=repetition)
    for name,value in [('observer.json',observer),('decoded.json',decoded),('measurement.json',measurement),('portability.json',receipt)]:
        write(output/name,value)
    write(output/'native-manifest.json',{'format':'dsh-native-v4','session_id':session,
        'files':[{'path':'native/'+native.name,'sha256':inventory.sha256,'size_bytes':inventory.size_bytes}],
        'empty_lock_observed':True,'complete_root_method':receipt['method']})
    manifest_ref={'id':'native-manifest:'+attempt,'sha256':digest(output/'native-manifest.json')}
    profile=build_dsh_format_evidence(decoded,native_path=copied/native.name,
        observer_document=(output/'observer.json').read_bytes(),run_id=workload['run_id'],repetition=repetition,
        build=state['version'],collected_on=state['started_at'][:10],native_manifest=manifest_ref,complete_record_family=True)
    write(output/'format-evidence.json',profile)
    if {r.get('model') for r in decoded['responses']} != {'deepseek-flash'}:
        raise ValueError('native model differs from captured shipped default route')
    evidence={'schema_version':RELEASE_EVIDENCE_SCHEMA_VERSION,'protocol_version':'1.0-survival',
        'workload_version':'1.0-survival-workload','rubric_version':'1.0-survival-rubric',
        'run_id':workload['run_id'],'capture_id':attempt,'evaluation_id':'dsh-evaluation:'+attempt,
        'configuration_id':'deepseek-harness-cli','repetition':repetition,'measurement':measurement,
        'observer':profile['observer'],'native_manifest':manifest_ref,
        'decoder':{'id':'dsh-native-v4','sha256':digest(ROOT/'session_bench/dsh_live.py')},
        'identity':{'provider':'deepseek-official','harness':'dsh','surface':'cli','execution_mode':'headless',
                    'build':state['version'],'model':'deepseek-flash','configuration':'shipped-headless-isolated-home',
                    'observer_schema_version':'1.0-survival-observer'},
        'metric_evidence':[{'metric_id':r['id'],
            'observer_ids':([e['id'] for e in observer['relations'] if e['kind']=='final_after']
                            if r['id']=='revision.final_after_r2' else
                            [e['id'] for e in observer['events'] if r['id'] in e.get('metric_ids',[])]) or [profile['observer']['id']],
            'native_locators':[{'artifact_id':'native:'+native.name,'artifact_sha256':inventory.sha256,
                                'record_location':'complete-v4-session-inventory'}]} for r in measurement['metrics']]}
    write(output/'survival-evidence.json',evidence)
    score=score_release_run(evidence,profile).display()
    score.update(rankable=False,overall=None,scope='private_diagnostics')
    write(output/'score-diagnostics.json',score)
    for name in ('workload.json','capture-start.json','capture-result.json','r1.stdout.jsonl','r2.stdout.jsonl'):
        shutil.copyfile(run/name,output/name)
    shutil.copyfile(fixture/'.survival-observer.jsonl',output/'helper-ledger.jsonl')
    shutil.copyfile(frozen_helper,output/'bench_check.py')
    files = [{'path':p.relative_to(output).as_posix(),'sha256':digest(p),'size_bytes':p.stat().st_size}
             for p in sorted(output.rglob('*')) if p.is_file()]
    manifest = {'schema_version':'session-bench-dsh-diagnostic-package-v1','attempt_id':attempt,'repetition':repetition,
                'files':files,'native_sha256':inventory.sha256,'source_files':{
                    rel:digest(ROOT/rel) for rel in ('session_bench/dsh_live.py','session_bench/dsh_native.py',
                    'session_bench/adapters/deepseek_harness.py','session_bench/live_observer.py','session_bench/live_metric_comparator.py',
                    'session_bench/dsh_format_evidence.py','session_bench/format_timestamp_population.py',
                    'scripts/qualify_deepseek_capture.py',
                    'fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py')},
                'parent_manifest_sha256':digest(run/'qualification-v3/manifest.json'),
                'public_safe':False,'independently_reproduced':False,'score_eligible':False}
    write(output/'manifest.json',manifest)
    summary={'attempt_id':attempt,'repetition':repetition,'native_events':len(decoded['records']),
             'states':{r['id']:r['state'] for r in measurement['metrics']},
             'manifest_sha256':digest(output/'manifest.json'),'public_safe':False,'score_eligible':False}
    write(output/'summary.json',summary)
    print(json.dumps(summary,sort_keys=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--attempt-id',required=True)
    parser.add_argument('--repetition',required=True,type=int,choices=(1,2,3))
    args=parser.parse_args()
    qualify(args.attempt_id,args.repetition)
