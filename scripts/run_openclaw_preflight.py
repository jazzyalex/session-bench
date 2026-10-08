#!/usr/bin/env python3
"""Bounded first-turn OpenClaw capture against existing configured OAuth.

Agent exec creates a new session per call; this preflight cannot qualify a
continued two-turn benchmark. No private session store is discovered or read.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from session_bench.workload_instance import instantiate_workload


def capture(name):
    template=ROOT/'fixtures/scenarios/survival-v1/workload'
    workload,canary=instantiate_workload(json.loads((template/'workload.json').read_bytes()),name)
    run=ROOT/'artifacts/v1-expanded-preparation/live-captures'/name;run.mkdir(parents=True,exist_ok=False)
    temp=Path(tempfile.mkdtemp(prefix='session-bench-openclaw-')).resolve()
    workspace=temp/'project';workspace.mkdir();shutil.copytree(template/'fixture_project',workspace/'fixture_project')
    state=temp/'state';state.mkdir()
    (run/'workload.json').write_text(json.dumps(workload,indent=2)+'\n')
    record={'attempt_id':name,'configuration_id':'openclaw','started_at':datetime.now(timezone.utc).isoformat(),
            'version':subprocess.check_output(['openclaw','--version'],text=True).strip(),
            'auth_route':'existing configured openai OAuth; client may refresh normally',
            'model_requested':'openai/gpt-5.4','native_state_root':str(state),'workspace':str(workspace),
            'personal_session_history_opened':False,'temporary_root_retained':True,
            'controller_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'limitation':'agent exec lacks resume; this is R1 preflight only, never a qualified two-turn run'}
    (run/'capture-start.json').write_text(json.dumps(record,indent=2)+'\n')
    args=['openclaw','agent','exec','--config',str(Path.home()/'.openclaw/openclaw.json'),'--state-dir',str(state),
          '--cwd',str(workspace),'--model','openai/gpt-5.4','--no-auth-env-only','--timeout','120','--json',workload['turns'][0]['text']]
    with (run/'r1.stdout.json').open('xb') as out,(run/'r1.stderr.txt').open('xb') as err:
        process=subprocess.Popen(args,cwd=workspace,env={**os.environ,**canary},stdout=out,stderr=err,start_new_session=True)
        try:code=process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            code=124
    shutil.copytree(workspace,run/'workspace')
    # Only newly created session JSONL, never auth profiles or other state files.
    native=[]
    for path in state.rglob('*.jsonl'):
        if path.is_symlink() or 'sessions' not in path.relative_to(state).parts:continue
        target=run/'native'/path.relative_to(state);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target);native.append({'path':target.relative_to(run).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    raw=(run/'r1.stdout.json').read_text()
    record.update(exit_code=code,status='preflight_only' if code==0 else 'blocked',native_files=native,
                  response_canary_seen=workload['turns'][0]['response_canary'] in raw,
                  finished_at=datetime.now(timezone.utc).isoformat(),score_eligible=False)
    (run/'attempt.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({k:record[k] for k in ('attempt_id','exit_code','status','response_canary_seen')}))
    return code

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attempt-id',required=True);sys.exit(capture(p.parse_args().attempt_id))
