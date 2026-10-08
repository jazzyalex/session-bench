#!/usr/bin/env python3
"""Bounded new-session capture; existing native history is inventoried, never read.

The whole CLI state directory is listed by metadata before the first turn and
after each turn. Every new, changed or removed entry must be accounted for:
files of the new conversation (copied), new log and implicit files of the run
(copied, private only), and shared stores that changed (metadata only, never
read). Anything else stops the capture.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from session_bench.workload_instance import instantiate_workload
from session_bench.antigravity_root_evidence import capture_state_family, inventory_sha256, inventory_state

STATE_ROOT=Path.home()/'.gemini/antigravity-cli'
REQUESTED_MODEL='claude-sonnet-4-6'
# agy 1.2.16 offers this model with one fixed reasoning setting and rejects --effort for it.
REQUESTED_EFFORT=None


def requested_selection():
    return {'requested_model':REQUESTED_MODEL,'requested_effort':REQUESTED_EFFORT}


def turn_command(agy, turn_text, workspace, primary, first_turn):
    args=[str(agy),'-p',turn_text,'--output-format','stream-json',
          '--model',REQUESTED_MODEL,*(['--effort',REQUESTED_EFFORT] if REQUESTED_EFFORT else []),
          # No --sandbox: it blocked the helper's own ledger write in the workspace (owner decision, 2026-10-05).
          '--disable-slash-commands','--add-dir',str(workspace),
          '--mode','accept-edits','--dangerously-skip-permissions','--print-timeout','120s']
    if first_turn:args.append('--new-project')
    else:args.extend(['--conversation',primary.split('/')[0]])
    return args

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):
    with p.open('x') as f:json.dump(v,f,sort_keys=True,indent=2);f.write('\n')

def capture(attempt):
    if not attempt or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in attempt):
        raise ValueError('invalid attempt ID')
    run=ROOT/'artifacts/v1-expanded-preparation/live-captures'/attempt
    run.mkdir(parents=True,exist_ok=False)
    workspace=run/'workspace';workspace.mkdir()
    template=ROOT/'fixtures/scenarios/survival-v1/workload'
    shutil.copytree(template/'fixture_project',workspace/'fixture_project')
    workload,env=instantiate_workload(json.loads((template/'workload.json').read_text()),attempt)
    write(run/'workload.json',workload)
    fixture=workspace/'fixture_project'
    initial={n:sha(fixture/n) for n in ('checkout.py','bench_check.py')}
    native_root=STATE_ROOT
    started_ns=time.time_ns()
    before=inventory_state(native_root)
    write(run/'state-before.json',before)
    state={'attempt_id':attempt,'configuration_id':'antigravity','started_at':datetime.now(timezone.utc).isoformat(),
           'version':subprocess.check_output([str(Path.home()/'.local/bin/agy'),'--version'],text=True,timeout=10).strip(),
           'model_selection_evidence':'explicit agy --model and --effort arguments; requested selection only',
           'before_sha256':initial['checkout.py'],'turns':[],'score_eligible':False,'status':'in_progress',
           'state_root_scope':'whole_cli_state_directory','started_ns':started_ns}
    state.update(requested_selection())
    write(run/'capture-start.json',state)
    primary=None;conversation=None
    try:
        for i,turn in enumerate(workload['turns'],1):
            args=turn_command(Path.home()/'.local/bin/agy',turn['text'],workspace,conversation,i==1)
            with (run/f'r{i}.stdout.jsonl').open('xb') as out,(run/f'r{i}.stderr.txt').open('xb') as err:
                p=subprocess.Popen(args,cwd=workspace,env={**os.environ,**env},stdout=out,stderr=err,start_new_session=True)
                try:code=p.wait(timeout=150)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                    raise ValueError('bounded turn timed out')
            # Waits for two equal metadata reads, refuses any unexplained change, then copies.
            # The first turn must create exactly one new conversation; the second must use it.
            try:
                receipt=capture_state_family(native_root,run/f'r{i}-native',before,attempt_id=attempt,turn=i,
                                             started_ns=started_ns,conversation_id=conversation)
            except ValueError:
                # Keep the metadata listing of the refused state for the diagnosis; it stays private.
                write(run/f'r{i}-state-refused.json',inventory_state(native_root))
                raise
            conversation=receipt['conversation_id'];primary=receipt['primary_path']
            after=inventory_state(native_root)
            # The saved inventory must be the quiet one that the receipt binds by digest.
            if inventory_sha256(after)!=receipt['after_inventory_sha256']:raise ValueError('state directory changed after the copy')
            write(run/f'r{i}-state-after.json',after)
            receipt.update(requested_selection())
            write(run/f'r{i}-native-receipt.json',receipt)
            stream=(run/f'r{i}.stdout.jsonl').read_text()
            row={'turn':i,'exit_code':code,'response_canary_observed':turn['response_canary'] in stream,
                 'stdout_sha256':sha(run/f'r{i}.stdout.jsonl')}
            row.update(requested_selection())
            state['turns'].append(row)
            if code!=0 or not row['response_canary_observed']:raise ValueError('turn incomplete; no retry')
            if sha(fixture/'bench_check.py')!=initial['bench_check.py']:raise ValueError('protected helper changed')
            if i==1 and sha(fixture/'checkout.py')!=initial['checkout.py']:raise ValueError('R1 target changed')
            if i==1:
                ledger=fixture/'.survival-observer.jsonl'
                observed=[json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
                if [(r['phase'],r['exit_code']) for r in observed]!=[('inspect',0),('baseline',1)]:
                    raise ValueError('R1 helper sequence incomplete; R2 not submitted')
        helper=[json.loads(line) for line in (fixture/'.survival-observer.jsonl').read_text().splitlines()]
        if [r['phase'] for r in helper]!=['inspect','baseline','final'] or helper[-1]['exit_code']!=0:
            raise ValueError('independent final helper did not complete')
        state['status']='captured_pending_qualification'
    except Exception as e:
        state['status']='invalid_or_blocked';state['error_type']=type(e).__name__
        if isinstance(e,ValueError):state['reason']=str(e)
    state.update(after_sha256=sha(fixture/'checkout.py'),finished_at=datetime.now(timezone.utc).isoformat(),native_primary=primary,conversation_id=conversation)
    write(run/'capture-result.json',state)
    print(json.dumps({k:state.get(k) for k in ('attempt_id','status','reason','error_type')}))
    return 0 if state['status']=='captured_pending_qualification' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt-id',required=True)
    sys.exit(capture(p.parse_args().attempt_id))
