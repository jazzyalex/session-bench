#!/usr/bin/env python3
"""Prepare Antigravity CLI public-input candidates; independent review remains mandatory."""
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

RUNS=('antigravity-2026-10-05-02','antigravity-2026-10-05-03','antigravity-2026-10-05-04')
# The model was requested with --model. The conversation database names the same model on each response.
CONFIGURATION='agy --model selection; no effort flag; no sandbox flag'


def _observer_ids(observer, metric_id):
    events=lambda kind:[row['id'] for row in observer['events'] if row['kind']==kind and row['population_role']=='primary_scored']
    relations=lambda kind:[row['id'] for row in observer['relations'] if row['kind']==kind]
    turns,responses=events('user_turn'),events('assistant_response')
    if metric_id.startswith('portable.'):
        # The root boundary is a capture receipt, not a stream event.
        return ['antigravity-state-root-receipt','closed-replay-observer']
    ids={'work.submitted_turns':turns,'work.visible_responses':responses,'work.actions':events('action'),
         'work.results':events('result'),'work.changed_files':events('file_change'),
         'causal.action_result':relations('action_result'),'causal.turn_response':relations('turn_response'),
         'revision.r1':turns[:1],'revision.r2':turns[1:2],
         'revision.r1_r2_order':relations('supersedes'),'revision.final_after_r2':relations('final_after'),
         # Native-attested metrics: the observer supplies the displayed responses they join to.
         'attribution.model_config':responses,'attribution.usage':responses,
         'attribution.token_semantics':responses,'attribution.reconciliation':responses}.get(metric_id)
    if not ids or len(ids)!=len(set(ids)):raise ValueError('missing or ambiguous observer locator '+metric_id)
    return ids


def build(source, output):
    source,output=Path(source),Path(output)
    pairs=[]
    for repetition,run in enumerate(RUNS,1):
        packet=source/run
        actual=json.loads((source/f'{run}-receipt.json').read_bytes())['diagnostics']['intact']
        observer=json.loads((packet/'inputs/observer.json').read_bytes())
        native=json.loads((packet/'native/decode.json').read_bytes())
        state=json.loads((packet/'inputs/capture/capture-result.json').read_bytes())
        streams=[json.loads((packet/f'inputs/capture/r{turn}.stdout.jsonl').read_bytes().splitlines()[0]) for turn in (1,2)]
        models={row.get('init',{}).get('model') for row in streams}|{state.get('requested_model')}
        if len(models)!=1 or not all(isinstance(model,str) and model for model in models):raise ValueError('unstable requested model selection')
        # The stream names a macOS home directory as the working directory.
        if not all(str(row.get('init',{}).get('cwd','')).startswith('/Users/') for row in streams):raise ValueError('captured OS not established')
        primary='capture/conversations/'+state['conversation_id']+'.db'
        locators=[{'artifact_id':'native/'+a['path'],'artifact_sha256':a['sha256'],
                   'record_location':'conversation database: steps and gen_metadata rows; canonical comparator joins native step, call and response ids' if a['path']==primary
                   else 'companion of the copied conversation family'} for a in native['artifacts']]
        refs=[{'metric_id':metric['id'],'observer_ids':_observer_ids(observer,metric['id']),
               'native_locators':locators if metric['id'].startswith('portable.') else [l for l in locators if l['artifact_id']=='native/'+primary]}
              for metric in actual['metrics'] if not metric['id'].startswith('broad.')]
        profile=actual['format_evidence']
        survival={'schema_version':RELEASE_EVIDENCE_SCHEMA_VERSION,'protocol_version':'1.0-survival','workload_version':'1.0-survival-workload','rubric_version':'1.0-survival-rubric',
                  'run_id':actual['run_id'],'configuration_id':'antigravity','repetition':repetition,'capture_id':packet.name,'evaluation_id':'native-score-replay:'+packet.name,
                  'observer':profile['observer'],'native_manifest':profile['native_manifest'],
                  'decoder':{'id':'antigravity-conversation-db-v1','sha256':hashlib.sha256((packet/'runtime/session_bench/antigravity_conversation_db.py').read_bytes()).hexdigest()},
                  'identity':{'provider':'google','harness':'antigravity','surface':'cli','execution_mode':'headless','os':'macOS','build':profile['build'],'model':next(iter(models)),'configuration':CONFIGURATION,'observer_schema_version':observer['schema_version']},
                  'measurement':actual['measurement'],'metric_evidence':refs}
        score_release_run(survival,profile)
        pairs.append({'survival':survival,'format':profile})
    bundle=canonical({'schema_version':PUBLIC_BUNDLE_SCHEMA,'configuration_id':'antigravity','pairs':pairs})
    with output.open('xb') as stream:stream.write(bundle)
    result={'output':str(output),'sha256':hashlib.sha256(bundle).hexdigest()}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.source,a.output)))
