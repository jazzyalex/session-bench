#!/usr/bin/env python3
"""Prepare public-input candidates; independent review remains mandatory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from session_bench.native_replay import canonical
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA
from session_bench.release_score import score_release_run


DESKTOP_RUNS=('claude-desktop-eval-1-correction-1','claude-desktop-eval-2-correction-1','claude-desktop-eval-3')


def build(source, output, configuration='claude-cli'):
    pairs=[];desktop=configuration=='claude-desktop'
    for repetition in (1,2,3):
        packet=source/(DESKTOP_RUNS[repetition-1] if desktop else f'claude-cli-eval-{repetition}')
        actual=json.loads((source/f'{packet.name}-receipt.json').read_bytes())['diagnostics']['intact']
        observer=json.loads((packet/'inputs/observer.json').read_bytes())
        native=json.loads((packet/'native/decode.json').read_bytes())
        responses=[e for e in observer['events'] if e['kind']=='assistant_response']
        models={e['fields']['model_id'] for e in responses};configs={e['fields']['configuration'] for e in responses}
        if len(models)!=1 or len(configs)!=1:raise ValueError('unstable captured model identity')
        # Captured native environment attachments explicitly record Darwin.
        records=[json.loads(line) for line in (packet/'native/session.jsonl').read_bytes().splitlines()]
        environments=[r['attachment'] for r in records if r.get('attachment',{}).get('type')=='environment']
        # The public Desktop transcript has its environment text blanked; the
        # operating system is then the operator's attestation.
        if not desktop and (not environments or not all('darwin' in json.dumps(e) for e in environments)):raise ValueError('captured OS not established')
        refs=[]
        for metric in actual['metrics']:
            key=metric['id']
            if key.startswith('broad.'):continue
            ids=[e['id'] for e in observer['events'] if key in e.get('metric_ids',[])]
            if key=='revision.final_after_r2':ids=['relation-final-after-r2','turn-r2','helper-emit-final']
            if not ids:raise ValueError('missing observer locator '+key)
            refs.append({'metric_id':key,'observer_ids':ids,'native_locators':[{'artifact_id':'native/session.jsonl','artifact_sha256':native['artifacts'][0]['sha256'],'record_location':'session.jsonl: complete declared session; canonical comparator joins captured event IDs'}]})
        profile=actual['format_evidence']
        survival={'schema_version':RELEASE_EVIDENCE_SCHEMA_VERSION,'protocol_version':'1.0-survival','workload_version':'1.0-survival-workload','rubric_version':'1.0-survival-rubric',
                  'run_id':actual['run_id'],'configuration_id':configuration,'repetition':repetition,'capture_id':packet.name,'evaluation_id':'native-score-replay:'+packet.name,
                  'observer':profile['observer'],'native_manifest':profile['native_manifest'],
                  'decoder':{'id':'claude-code-jsonl-v1','sha256':hashlib.sha256((packet/'runtime/session_bench/adapters/claude_code_decoder.py').read_bytes()).hexdigest()},
                  'identity':{'provider':'anthropic','harness':'claude','surface':'desktop' if desktop else 'cli','execution_mode':'gui' if desktop else 'headless','os':'macOS','build':profile['build'],'model':next(iter(models)),'configuration':next(iter(configs)),'observer_schema_version':observer['schema_version']},
                  'measurement':actual['measurement'],'metric_evidence':refs}
        score_release_run(survival,profile)
        pairs.append({'survival':survival,'format':profile})
    bundle={'schema_version':PUBLIC_BUNDLE_SCHEMA,'configuration_id':configuration,'pairs':pairs}
    with output.open('xb') as stream:stream.write(canonical(bundle))
    print(json.dumps({'output':str(output),'sha256':hashlib.sha256(canonical(bundle)).hexdigest()}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--configuration',choices=('claude-cli','claude-desktop'),default='claude-cli');a=p.parse_args();build(a.source,a.output,a.configuration)
