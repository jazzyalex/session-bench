#!/usr/bin/env python3
"""Prepare Codex CLI public-input candidates; independent review remains mandatory."""
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
from session_bench.score_replay import _observer


def _observer_ids(observer, metric_id):
    events=lambda kind:[row['id'] for row in observer['events'] if row['kind']==kind]
    relations=lambda kind:[row['id'] for row in observer['relations'] if row['kind']==kind]
    responses=events('assistant_response')
    if metric_id.startswith('portable.'):
        # The root boundary is a capture receipt, not a stream event.
        return ['codex-cli-normal-root-receipt','closed-replay-observer']
    ids={'work.submitted_turns':events('user_turn'),'work.visible_responses':responses,'work.actions':events('action'),
         'work.results':events('result'),'work.changed_files':events('file_change'),
         'causal.action_result':relations('action_result'),'causal.turn_response':relations('turn_response'),
         'revision.r1':['turn-r1','response-r1'],'revision.r2':['turn-r2','response-r2'],
         'revision.r1_r2_order':relations('supersedes'),'revision.final_after_r2':relations('final_after'),
         # Native-attested metrics: the observer supplies the displayed responses they join to.
         'attribution.model_config':responses,'attribution.usage':responses,
         'attribution.token_semantics':responses,'attribution.reconciliation':responses}.get(metric_id)
    if not ids or len(ids)!=len(set(ids)):raise ValueError('missing or ambiguous observer locator '+metric_id)
    return ids


def build(source, output):
    pairs=[]
    for repetition in (1,2,3):
        packet=source/f'codex-cli-eval-{repetition}'
        actual=json.loads((source/f'{packet.name}-receipt.json').read_bytes())['diagnostics']['intact']
        context=json.loads((packet/'inputs/context.json').read_bytes())
        instance=json.loads((packet/'inputs/workload.json').read_bytes())
        observer=json.loads(_observer(packet,instance,context)[0])
        native=json.loads((packet/'native/decode.json').read_bytes())
        if len(native['artifacts'])!=1:raise ValueError('unexpected Codex native family')
        rollout=native['artifacts'][0]
        records=[json.loads(line) for line in (packet/'native'/rollout['path']).read_bytes().splitlines()]
        models={row['payload']['model'] for row in records if row.get('type')=='turn_context'}
        if len(models)!=1:raise ValueError('unstable captured model identity')
        model=next(iter(models))
        locator={'artifact_id':'native/'+rollout['path'],'artifact_sha256':rollout['sha256'],
                 'record_location':'rollout JSONL: complete declared session; canonical comparator joins native record ids'}
        refs=[{'metric_id':metric['id'],'observer_ids':_observer_ids(observer,metric['id']),'native_locators':[locator]}
              for metric in actual['metrics'] if not metric['id'].startswith('broad.')]
        profile=actual['format_evidence']
        survival={'schema_version':RELEASE_EVIDENCE_SCHEMA_VERSION,'protocol_version':'1.0-survival','workload_version':'1.0-survival-workload','rubric_version':'1.0-survival-rubric',
                  'run_id':actual['run_id'],'configuration_id':'codex-cli','repetition':repetition,'capture_id':packet.name,'evaluation_id':'native-score-replay:'+packet.name,
                  'observer':profile['observer'],'native_manifest':profile['native_manifest'],
                  'decoder':{'id':'codex-rollout-v1','sha256':hashlib.sha256((packet/'runtime/session_bench/adapters/codex_cli_decoder.py').read_bytes()).hexdigest()},
                  # The operating system is the operator's attestation; the rollout does not name it.
                  'identity':{'provider':'openai','harness':'codex','surface':'cli','execution_mode':'headless','os':'macOS','build':profile['build'],'model':model,'configuration':model,'observer_schema_version':observer['schema_version']},
                  'measurement':actual['measurement'],'metric_evidence':refs}
        score_release_run(survival,profile)
        pairs.append({'survival':survival,'format':profile})
    bundle={'schema_version':PUBLIC_BUNDLE_SCHEMA,'configuration_id':'codex-cli','pairs':pairs}
    with output.open('xb') as stream:stream.write(canonical(bundle))
    print(json.dumps({'output':str(output),'sha256':hashlib.sha256(canonical(bundle)).hexdigest()}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();build(a.source,a.output)
