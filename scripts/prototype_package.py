#!/usr/bin/env python3
"""Bind already captured synthetic native files to independent workload evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

def sha(data): return hashlib.sha256(data).hexdigest()
def canonical(data): return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()

def package(capture, workspace, identity, kind, prompts, *, complete=False):
    capture=Path(capture).resolve(); workspace=Path(workspace).resolve()
    if (capture/'metadata.json').exists(): raise ValueError('capture already packaged; preserve it')
    workload=json.loads((workspace/'workload.json').read_text())
    if sha((workspace/'bench_check.py').read_bytes())!=workload['helper_sha256']:
        raise ValueError('independent helper changed; cannot trust its ledger')
    files=[]
    for p in sorted((capture/'native').iterdir()):
        if p.is_symlink() or not p.is_file(): raise ValueError('native artifacts must be ordinary files')
        data=p.read_bytes()
        if len(data)>16*1024*1024: raise ValueError('artifact exceeds pilot byte ceiling')
        artifact_kind='opencode-sqlite-companion' if kind=='opencode-sqlite' and p.name.endswith(('-wal','-shm')) else kind
        files.append({'path':p.relative_to(capture).as_posix(),'sha256':sha(data),'bytes':len(data),'kind':artifact_kind})
    if not files: raise ValueError('no native artifacts')
    events=[]
    for index,prompt in enumerate(prompts):
        events.append({'id':f'prompt-{index+1}','kind':'correction' if index else 'user_message','text':prompt,'source':'submitted_input','expected_native':True})
    ledger=workspace/'.bench-observer.jsonl'
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            event=json.loads(line)
            events.append(event)
            if event.get('exit_code'):
                events.append({**event,'id':event['id']+'-failure','kind':'failure'})
    if sha((workspace/'checkout.py').read_bytes())!=workload['initial_app_sha256']:
        events.append({'id':'checkout-change','kind':'file_change','file':'checkout.py','text':'checkout.py','source':'disk_ledger','expected_native':True})
    for stream in sorted(capture.glob('turn*.stdout.jsonl')):
        for index,line in enumerate(stream.read_text().splitlines()):
            try: row=json.loads(line)
            except ValueError: continue
            item=row.get('item',{})
            if row.get('type')=='item.completed' and item.get('type')=='agent_message':
                events.append({'id':f'{stream.stem}-{index}','kind':'assistant_message','text':item['text'],'source':'live_cli_stream','expected_native':True})
            if row.get('type')=='text' and isinstance(row.get('part',{}).get('text'),str):
                events.append({'id':f'{stream.stem}-{index}','kind':'assistant_message','text':row['part']['text'],'source':'live_cli_stream','expected_native':True})
    meta={'schema_version':'session-bench-prototype-capture-v1',**identity,
          'captured_at':datetime.now(timezone.utc).isoformat(),'native_files':files,
          'required_native_files':[x['path'] for x in files], 'artifact_set_complete':complete,'status':'pilot'}
    observer={'schema_version':'session-bench-prototype-observer-v1','run_id':identity['run_id'],
              'artifact_manifest_sha256':sha(canonical(sorted(files,key=lambda x:x['path']))),
              'independent':True,'observer_method':'independently generated helper disk ledger and submitted inputs; separately captured live CLI stream when present; Desktop display is unobserved',
              'events':events}
    evidence=capture/'workload'; evidence.mkdir()
    for name in ['workload.json','bench_check.py','checkout.py','.bench-observer.jsonl']:
        if (workspace/name).exists(): shutil.copyfile(workspace/name,evidence/name)
    (capture/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
    (capture/'observer.json').write_text(json.dumps(observer,indent=2)+'\n')
    return {'capture':str(capture),'events':len(events),'bytes':sum(x['bytes'] for x in files)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('capture',type=Path);p.add_argument('workspace',type=Path)
    p.add_argument('--id',required=True);p.add_argument('--name',required=True);p.add_argument('--surface',required=True)
    p.add_argument('--version',required=True);p.add_argument('--model',required=True);p.add_argument('--kind',required=True)
    p.add_argument('--prompts',type=Path);p.add_argument('--complete',action='store_true')
    a=p.parse_args(); w=json.loads((a.workspace/'workload.json').read_text())
    prompts=json.loads(a.prompts.read_text()) if a.prompts else [w['first'],w['second']]
    identity={k:getattr(a,k) for k in ['id','name','surface','version','model']};identity['run_id']=a.capture.name
    print(json.dumps(package(a.capture,a.workspace,identity,a.kind,prompts,complete=a.complete)))
