#!/usr/bin/env python3
"""Build a self-contained fourteen-surface release from separately pinned reviews.

The caller supplies the trusted review hashes; packet-provided approvals alone
are never accepted. This command writes local artifacts and never publishes.
"""
import argparse
import copy
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
TRUSTED_INDEX_N1_SCHEMA='session-bench-trusted-release-index-n1-v1'
N1_POLICY_ID='session-bench-v1-ranking-n1'
sys.path.insert(0,str(ROOT))
from session_bench.native_replay import canonical
from session_bench.release_replay import (
    PUBLIC_BUNDLE_N1_SCHEMA, PUBLIC_BUNDLE_SCHEMA,
    verify_release_configuration, verify_release_configuration_n1,
)
from session_bench.release_scope import load_release_scope
from session_bench.release_score import release_scorecard
from session_bench.v1_public_score import PUBLIC_CATEGORY_POINTS, PUBLIC_METRICS


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value)+b'\n')


def _resolve_input_path(value):
    path=Path(value)
    return path if path.is_absolute() else ROOT/path


def _metric_state_counts(metrics):
    allowed={'measured','native_absent','contradiction','unresolved'}
    if not isinstance(metrics,list) or len(metrics)!=31:
        raise ValueError('partial diagnostic must contain exactly 31 metric states')
    ids=set();counts={state:0 for state in sorted(allowed)}
    for metric in metrics:
        metric_id=metric.get('id')
        state=metric.get('state')
        if not isinstance(metric_id,str) or metric_id in ids or state not in allowed:
            raise ValueError('invalid or duplicate partial metric state')
        ids.add(metric_id);counts[state]+=1
    if len(ids)!=31:raise ValueError('partial diagnostic must cover 31 unique metrics')
    return counts


def _provisional_score(intact):
    """Measured points and best possible total from a partial run's own score diagnostics.

    Unresolved metrics count as zero in ``measured_points`` and as full credit
    in ``possible_max``.  A partial run is never ranked on either number.
    """
    diagnostics=intact.get('score_diagnostics')
    categories=diagnostics.get('categories') if isinstance(diagnostics,dict) else None
    if not isinstance(categories,dict) or set(categories)!=set(PUBLIC_CATEGORY_POINTS):return None
    return {'measured_points':str(sum(Decimal(row['possible_min']) for row in categories.values())),
            'possible_max':str(sum(Decimal(row['possible_max']) for row in categories.values()))}


def _provisional_range(runs):
    """Equal-weight mean of the per-run provisional bounds, or None when a run has none."""
    scores=[run.get('provisional_score') for run in runs]
    if not scores or any(score is None for score in scores):return None
    def mean(key):
        value=sum(Decimal(score[key]) for score in scores)/len(scores)
        return str(value.quantize(Decimal('0.1'),rounding=ROUND_HALF_UP))
    return [mean('measured_points'),mean('possible_max')]


def _note_lines(notes):
    """Notes on waived or corrected rules, then the known limits, as Markdown lines. The ranking table stays unmarked."""
    if not notes: return []
    lines=['## Notes on waived and corrected rules','','Each waiver below is an owner decision made after the results were known.','']
    lines.extend(f"- {row['text']}" for row in notes['footnotes'])
    lines.extend(['','## Known limits','',*(f"- {text}" for text in notes['limits']),''])
    return lines


def _ranking_lines(report, names, coverage, notes=None):
    """Ranked rows by rank, then provisional rows by measured points, as Markdown lines."""
    scores={row['configuration_id']:row['overall'] for row in report['scores']}
    lines=['## Ranking','','| Rank | Configuration | Score | Runs |','|---:|---|---:|---:|']
    for config,rank in sorted(report['ranks'].items(),key=lambda item:(item[1],names[item[0]])):
        lines.append(f"| {rank} | {names[config]} | {scores[config]} | {coverage[config]['sample_size']} |")
    provisional=[(config,record['provisional_range']) for config,record in coverage.items() if record.get('provisional_range')]
    if provisional: lines.extend(['','## Provisional, not ranked','','| Configuration | Measured points | Best possible |','|---|---:|---:|'])
    for config,(low,high) in sorted(provisional,key=lambda item:(-Decimal(item[1][0]),names[item[0]])):
        lines.append(f"| {names[config]} | {low} | {high} |")
    lines.append('')
    lines.extend(_note_lines(notes))
    return lines


def _apply_metric_closures(item, runs, original_states):
    """Apply private, hash-pinned one-metric diagnostics to coverage only."""
    references=item.get('metric_closures',[])
    if not isinstance(references,list):raise ValueError('metric closures must be a list')
    if not references:return []
    expected={(run['run_id'],run['repetition']) for run in runs}
    if len(runs)!=3 or len(expected)!=3 or {run['repetition'] for run in runs}!={1,2,3}:
        raise ValueError('metric closures require exactly three distinct repetitions')
    source_by_id={run['run_id']:run for run in runs}
    applied=[];seen=set()
    for reference in references:
        if not isinstance(reference,dict) or set(reference)!={'path','sha256'}:
            raise ValueError('invalid metric closure reference')
        path=_resolve_input_path(reference['path'])
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=reference['sha256']:raise ValueError('metric closure source hash mismatch')
        overlay=json.loads(raw)
        metric=overlay.get('metric_closure',{})
        metric_id=metric.get('metric_id')
        if (overlay.get('configuration_id')!=item['configuration_id']
                or overlay.get('public_safe') is not False
                or overlay.get('independent_reproduction') is not False
                or overlay.get('rankable') is not False
                or overlay.get('historical_inputs_overwritten') is not False
                or not isinstance(overlay.get('schema_version'),str) or not overlay['schema_version']
                or not isinstance(overlay.get('scope'),str) or not overlay['scope'].startswith('private_additive_')
                or not overlay['scope'].endswith('_only')
                or not isinstance(metric_id,str) or metric_id in seen
                or metric.get('predecessor_state')!='unresolved'
                or metric.get('successor_state')!='measured'
                or metric.get('score_fraction')!='1/1'):
            raise ValueError('metric closure is not a private one-metric unresolved-to-measured proof')
        seen.add(metric_id)
        bindings=overlay.get('bindings') if isinstance(overlay.get('bindings'),list) else overlay.get('repetitions')
        if not isinstance(bindings,list) or len(bindings)!=3:
            raise ValueError('metric closure has no exact three-run binding')
        bound={}
        for binding in bindings:
            if not isinstance(binding,dict):raise ValueError('malformed metric closure run binding')
            identity=(binding.get('run_id'),binding.get('repetition'))
            if identity in bound:raise ValueError('duplicate metric closure run binding')
            base=source_by_id.get(identity[0])
            if (base is None or identity!=(base['run_id'],base['repetition'])
                    or binding.get('replay_receipt_sha256')!=base['source_receipt_sha256']
                    or binding.get('replay_manifest_sha256')!=base['manifest_sha256']):
                raise ValueError('metric closure does not bind the exact replay receipts')
            if 'metric_state' in binding and binding['metric_state']!='measured':
                raise ValueError('metric closure run is not measured')
            if 'score_fraction' in binding and binding['score_fraction']!='1/1':
                raise ValueError('metric closure run score differs')
            bound[identity]=binding
        if set(bound)!=expected:raise ValueError('metric closure run population differs')
        for run in runs:
            run_id=run['run_id']
            if original_states[run_id].get(metric_id)!='unresolved':
                raise ValueError('metric closure predecessor is not unresolved')
            run['metric_state_counts']['unresolved']-=1
            run['metric_state_counts']['measured']+=1
            if run.get('provisional_score') is not None:
                floor=Decimal(run['provisional_score']['measured_points'])+Decimal(str(PUBLIC_METRICS[metric_id].points))
                run['provisional_score']['measured_points']=str(floor)
        applied.append({'metric_id':metric_id,'source_sha256':digest})
    return sorted(applied,key=lambda row:row['metric_id'])


def _load_partial_coverage(index):
    if index is None:return {'partial':{},'progress':{}}
    if index.get('schema_version')!='session-bench-partial-coverage-index-v1' or set(index)!={'schema_version','configurations','capture_progress'}:
        raise ValueError('invalid partial coverage index')
    results={}
    for item in index['configurations']:
        config=item['configuration_id']
        if config in results:raise ValueError('duplicate partial coverage configuration')
        summary_path=_resolve_input_path(item['summary_path'])
        summary_bytes=summary_path.read_bytes();summary=json.loads(summary_bytes)
        if summary.get('public_safe') is not False or summary.get('independent_reproduction') is not False:
            raise ValueError('partial diagnostic inputs must remain marked private and unreproduced')
        summary_runs={row.get('run_id') or row.get('packet'):row for row in summary.get('runs',[])}
        runs=[];original_states={}
        for row in item['runs']:
            receipt_path=_resolve_input_path(row['receipt_path'])
            receipt_bytes=receipt_path.read_bytes();receipt=json.loads(receipt_bytes)
            run_id=row['run_id'];summary_row=summary_runs.get(run_id)
            if summary_row is None:raise ValueError('partial receipt is absent from its summary')
            if receipt.get('public_safe') is not False or receipt.get('independent_reproduction') is not False:
                raise ValueError('partial receipt must remain marked private and unreproduced')
            diagnostics=receipt.get('diagnostics',{});integrity=receipt.get('diagnostics_sha256')
            if hashlib.sha256(canonical(diagnostics)).hexdigest()!=integrity:
                raise ValueError('partial diagnostic hash mismatch')
            expected_diagnostic=summary_row.get('diagnostics_sha256')
            expected_manifest=summary_row.get('manifest_sha256')
            if expected_diagnostic!=integrity or expected_manifest!=receipt.get('manifest_sha256'):
                raise ValueError('partial receipt does not match its summary pins')
            intact=diagnostics.get('intact',{})
            if intact.get('configuration_id')!=config or intact.get('run_id')!=run_id:
                raise ValueError('partial diagnostic identity mismatch')
            counts=_metric_state_counts(intact.get('metrics'))
            original_states[run_id]={metric['id']:metric['state'] for metric in intact['metrics']}
            runs.append({'run_id':run_id,'repetition':intact.get('repetition'),
                         'metric_state_counts':counts,
                         'provisional_score':_provisional_score(intact),
                         'source_receipt_sha256':hashlib.sha256(receipt_bytes).hexdigest(),
                         'diagnostics_sha256':integrity,
                         'manifest_sha256':receipt.get('manifest_sha256')})
        if not runs:raise ValueError('partial coverage entry has no diagnostic runs')
        closures=_apply_metric_closures(item,runs,original_states)
        results[config]={'kind':'partial_diagnostic','review_state':'public_safety_and_independent_replay_pending',
                         'source_summary_sha256':hashlib.sha256(summary_bytes).hexdigest(),'runs':runs,
                         'metric_closures':closures}
    progress={}
    for item in index['capture_progress']:
        config=item['configuration_id']
        if config in progress or config in results:raise ValueError('duplicate capture progress configuration')
        progress[config]=copy.deepcopy(item)
    return {'partial':results,'progress':progress}


def build(index, statuses, output, partial_coverage=None, notes=None):
    output=Path(output).resolve()
    if output.exists():raise ValueError('release output must be a new directory')
    scope=load_release_scope()
    allowed={row['configuration_id'] for row in scope['rows']}
    if (set(index)!={'schema_version','configurations'} or index['schema_version'] not in {
            'session-bench-trusted-release-index-v1', TRUSTED_INDEX_N1_SCHEMA}):
        raise ValueError('invalid trusted index')
    empty_n1_policy=index['schema_version']==TRUSTED_INDEX_N1_SCHEMA
    partial=_load_partial_coverage(partial_coverage)
    if (set(partial['partial'])|set(partial['progress']))-allowed:raise ValueError('partial coverage references an unknown configuration')
    output.mkdir(parents=True)
    aggregates=[];included=set();public_index=[];sample_sizes={}
    for item in index['configurations']:
        config=item['configuration_id']
        if config not in allowed or config in included:raise ValueError('invalid/duplicate configuration')
        included.add(config)
        bundle=_resolve_input_path(item['public_inputs']).read_bytes();review=_resolve_input_path(item['review']).read_bytes()
        bundle_schema=json.loads(bundle).get('schema_version')
        if bundle_schema==PUBLIC_BUNDLE_N1_SCHEMA:
            sample_size=1;verifier=verify_release_configuration_n1
        elif bundle_schema==PUBLIC_BUNDLE_SCHEMA:
            sample_size=3;verifier=verify_release_configuration
        else:
            raise ValueError('unsupported public replay bundle schema')
        sample_sizes[config]=sample_size
        target=output/'evidence'/config;target.mkdir(parents=True)
        packets={};public={};pins=[]
        for row in item['packets']:
            run_id=row['run_id'];source=_resolve_input_path(row['path']).resolve()
            if run_id in packets:raise ValueError('duplicate packet run')
            destination=target/source.name
            shutil.copytree(source,destination)
            packets[run_id]=(source,row['manifest_sha256']);public[run_id]=destination
            pins.append({'run_id':run_id,'path':destination.relative_to(output).as_posix(),'manifest_sha256':row['manifest_sha256']})
        aggregate=verifier(bundle,review,
            trusted_review_sha256=item['trusted_review_sha256'],expected_reviewer_id=item['reviewer_id'],expected_producer_id=item['producer_id'],
            packets=packets,public_packets=public)
        if aggregate.configuration_id!=config:raise ValueError('review configuration mismatch')
        if len(aggregate.runs)!=sample_size or {run.repetition for run in aggregate.runs}!=set(range(1,sample_size+1)):
            raise ValueError('verified run population differs from public bundle schema')
        aggregates.append(aggregate)
        (target/'public-inputs.json').write_bytes(bundle);(target/'independent-review.json').write_bytes(review)
        public_index.append({'configuration_id':config,'public_inputs':f'evidence/{config}/public-inputs.json','review':f'evidence/{config}/independent-review.json',
                             'trusted_review_sha256':item['trusted_review_sha256'],'reviewer_id':item['reviewer_id'],'producer_id':item['producer_id'],'packets':pins})
    rankable={a.configuration_id:a for a in aggregates if a.rankable}
    if any(config in rankable and diagnostic['metric_closures'] for config,diagnostic in partial['partial'].items()):
        raise ValueError('metric closures may only annotate partial-diagnostic configurations')
    public_statuses=copy.deepcopy(statuses)
    original_statuses={row['configuration_id']:row for row in statuses}
    for row in public_statuses:
        config=row['configuration_id']
        if config in rankable:
            row['state']='rankable';row['reason_ids']=[];row['evidence_refs']=[f'evidence/{config}/independent-review.json']
    n1_policy=1 in sample_sizes.values() or (empty_n1_policy and not sample_sizes)
    report=release_scorecard(scope,public_statuses,aggregates)
    mixed_samples=len(set(sample_sizes.values()))>1
    candidate_sample_size='mixed' if mixed_samples else (next(iter(sample_sizes.values())) if sample_sizes else (1 if n1_policy else 3))
    for row in report['rows']:
        row['sample_size']=sample_sizes.get(row['configuration_id'])
    for score in report['scores']:
        size=sample_sizes[score['configuration_id']]
        score['sample_size']=size
        if size==1:
            score.pop('range',None)
            score.pop('category_ranges',None)
    if n1_policy:
        report['ranking_policy']={
            'id':N1_POLICY_ID,
            'sample_size':candidate_sample_size,
            'selection_rule':('prospectively designated repetition 1 for n=1 rows; existing independently reviewed repetitions 1, 2, 3 for n=3 rows' if mixed_samples else 'prospectively designated repetition 1 for every configuration'),
            'repeatability_claims':False,
            'rank_gate':('one or three complete, independently reviewed and reproduced 31-metric runs according to each row bundle schema' if mixed_samples else 'one complete, independently reviewed and reproduced 31-metric run'),
        }
        report['sample_size']=candidate_sample_size
    report['runs']=[run.display() for aggregate in aggregates for run in aggregate.runs]
    score_runs={config:[run for run in report['runs'] if run['configuration_id']==config] for config in rankable}
    coverage={};coverage_by_id={row['configuration_id']:row for row in report['rows']}
    scope_names={row['configuration_id']:row['display_name'] for row in scope['rows']}
    for config,row in coverage_by_id.items():
        original=original_statuses[config]
        source_pins=[]
        for relative in original.get('evidence_refs',[]):
            path=ROOT/relative
            if not path.is_file():raise ValueError('missing coverage evidence: '+relative)
            source_pins.append({'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'availability':'source receipt withheld; digest retained'})
        if config in rankable:
            runs=[]
            for run in score_runs[config]:
                counts={state:0 for state in ('measured','native_absent','contradiction','unresolved')}
                for evidence in run['metric_evidence'].values():counts[evidence['state']]+=1
                runs.append({'run_id':run['run_id'],'repetition':run['repetition'],'metric_state_counts':counts,
                             'verification':'independently_reviewed_and_reproduced'})
            record={'configuration_id':config,'display_name':scope_names[config],'evidence_kind':'verified_complete',
                    'evidence_state':('one prospectively selected, independently reviewed and reproduced run' if sample_sizes[config]==1 else 'three independently reviewed and reproduced runs'),
                    'sample_size':sample_sizes[config],
                    'overall_score':next(s['overall'] for s in report['scores'] if s['configuration_id']==config),
                    'rank':report['ranks'].get(config),'runs':runs,'source_receipt_digests':source_pins}
        elif config in partial['partial']:
            diagnostic=partial['partial'][config]
            diagnostic_runs=diagnostic['runs']
            record={'configuration_id':config,'display_name':scope_names[config],'evidence_kind':'partial_diagnostic',
                    'evidence_state':'local metric-state replay; public-safety review and independent reproduction pending',
                    'sample_size':len(diagnostic_runs),
                    'overall_score':None,'rank':None,'provisional_range':_provisional_range(diagnostic_runs),
                    'runs':diagnostic_runs,'source_summary_sha256':diagnostic['source_summary_sha256'],
                    'review_state':diagnostic['review_state'],'source_receipt_digests':source_pins,
                    'metric_closure_ids':[entry['metric_id'] for entry in diagnostic['metric_closures']],
                    'metric_closure_source_digests':diagnostic['metric_closures']}
        elif config in partial['progress']:
            progress=partial['progress'][config]
            record={'configuration_id':config,'display_name':scope_names[config],'evidence_kind':progress['kind'],
                    'evidence_state':progress['detail'],'overall_score':None,'rank':None,'runs':[],
                    'capture_progress':{key:value for key,value in progress.items() if key not in ('configuration_id','kind','detail')},
                    'attempt_ids':original.get('attempt_ids',[]),'reason_ids':row.get('reason_ids',[]),
                    'source_receipt_digests':source_pins}
        else:
            state=row['state']
            kind={'blocked':'blocked','unattempted':'not_attempted'}.get(state,'evidence_closure_incomplete')
            detail=('blocked before a qualified score run' if state=='blocked' else
                    'no attempt recorded' if state=='unattempted' else 'capture, observation, or provenance closure remains incomplete')
            record={'configuration_id':config,'display_name':scope_names[config],'evidence_kind':kind,'evidence_state':detail,
                    'overall_score':None,'rank':None,'runs':[],'attempt_ids':original.get('attempt_ids',[]),
                    'reason_ids':row.get('reason_ids',[]),'source_receipt_digests':source_pins}
        destination=f'coverage/{config}.json'
        record['sample_size']=sample_sizes.get(config,record.get('sample_size'))
        write(output/destination,record)
        row['coverage_ref']=destination
        if config not in rankable:row['evidence_refs']=[destination]
        coverage[config]=record
    report['coverage']=coverage
    report['partial_coverage_publication_ready']=not any(record.get('review_state')=='public_safety_and_independent_replay_pending' for record in coverage.values())
    report['full_scope_publication_eligible']=report['publication_eligible'] and report['partial_coverage_publication_ready']
    write(output/'scorecard.json',report)
    write(output/'replay-index.json',{'schema_version':index['schema_version'],'configurations':public_index})
    scores={row['configuration_id']:row for row in report['scores']}
    names={row['configuration_id']:row['display_name'] for row in scope['rows']}
    verified_count=report['rankable_rows']
    lines=['# Session Bench v1 release candidate','',
           'Fourteen configurations: all ten v0.4 harnesses, Codex Desktop, Claude Desktop, Cursor Desktop, and DeepSeek Harness CLI (`dsh`).','',
           'Scores measure recovery of this synthetic two-turn workload from retained session files. They are not coding-quality, price, speed, or vendor reliability scores. A Desktop surface is a separate row unless its session format is the same as its CLI; then it is shown as sharing that row.','',
           (f'Ranking policy `{N1_POLICY_ID}` preserves reviewed n=3 rows and accepts prospectively designated repetition 1 for n=1 rows. Every ranked row requires all 31 metric states to resolve in each included run, with an independently reviewed public bundle and executed replay. Sample size is shown per row; n=1 rows contain no range or repeatability claim.' if mixed_samples else f'Ranking policy `{N1_POLICY_ID}` uses one prospectively designated repetition 1 per configuration. A rank requires all 31 metric states to resolve in that run, with an independently reviewed public bundle and executed replay. Single-run reports contain no range or repeatability claim.' if n1_policy else 'An overall score and rank require three complete, independently reviewed 31-metric replays.'),
           'A provisional row shows two numbers: the points its resolved metrics earn with every unresolved metric counted as zero, and the best total it could reach if every unresolved metric earned full credit. Provisional rows are not ranked: their private inputs are not public-safe or independently reproduced. Capture-only and blocked entries show their actual milestone or blocker without inventing metric values.','',
           (f'The ranked cohort has {verified_count} configurations with sample sizes declared per row and executable replays for each sanitized packet. Reviews use a separate agent session on the same host and account; retained capture provenance is not a fresh independent acquisition.' if mixed_samples else f'The ranked cohort has {verified_count} configurations with one selected capture each and executable replays for each sanitized packet. Reviews use a separate agent session on the same host and account; retained capture provenance is not a fresh independent acquisition.' if n1_policy else f'The ranked cohort has {verified_count} configurations with three captures each and executable replays for each sanitized packet. Reviews use a separate agent session on the same host and account; retained capture provenance is not a fresh independent acquisition.'),'',
           *_ranking_lines(report,names,coverage,notes),'## All configurations','',
           '| Configuration | Evidence | Sample size | Current coverage | Overall score | Rank | Remaining work | Evidence file |','|---|---|---:|---|---:|---:|---|---|']
    csv_rows=[]
    for row in report['rows']:
        config=row['configuration_id'];score=scores.get(config,{});overall=score.get('overall');rank=report['ranks'].get(config)
        coverage_record=coverage[config];kind=coverage_record['evidence_kind']
        status_labels={'verified_complete':'Verified score','partial_diagnostic':'Partial diagnostic; review pending',
                       'capture_only':'Qualified capture only','unqualified_capture':'Capture qualification incomplete',
                       'partial_diagnostic_capture':'Private partial diagnostics; score closure pending',
                       'blocked':'Blocked before scoring','not_attempted':'Not attempted',
                       'shared_format':'Shared session format',
                       'evidence_closure_incomplete':'Evidence closure incomplete'}
        status=status_labels.get(kind,'Evidence status pending')
        metric_runs=coverage_record.get('runs',[])
        if kind=='verified_complete':
            state_text=(f"{len(metric_runs)}/1 verified run; 31/31 metric states"
                        if sample_sizes[config]==1 else f"{len(metric_runs)}/3 verified runs; 31/31 metric states in each")
        elif kind=='partial_diagnostic':
            profiles={tuple(sorted(run['metric_state_counts'].items())) for run in metric_runs}
            if len(profiles)==1:
                counts=dict(next(iter(profiles)))
                profile=', '.join(f"{counts[state]} {state.replace('_',' ')}" for state in ('measured','native_absent','contradiction','unresolved') if counts[state])
                state_text=f"{len(metric_runs)} private replays; each: {profile}"
            else:
                state_text=f"{len(metric_runs)} private replays; see per-run coverage file"
        elif coverage_record.get('capture_progress'):
            progress=coverage_record['capture_progress'];state_text=coverage_record['evidence_state']
            if 'completed_two_turn_captures' in progress:
                target=progress['target_repetitions']
                state_text+=f" ({progress['completed_two_turn_captures']}/{target} two-turn captures completed; {progress['qualified_two_turn_captures']}/{target} score-qualified repetitions)"
            elif 'qualified_two_turn_captures' in progress:
                state_text+=f" ({progress['qualified_two_turn_captures']}/{progress['target_repetitions']} qualified repetitions)"
            elif 'evaluated_captures' in progress:
                state_text+=f" ({progress['evaluated_captures']} evaluated capture)"
        elif kind=='blocked':
            state_text='; '.join(reason.replace('_',' ') for reason in row['reason_ids']) or coverage_record['evidence_state']
        else:
            state_text=coverage_record['evidence_state']
        reason=', '.join(row.get('reason_ids',[]))
        ref=coverage_record['configuration_id'] and row.get('coverage_ref')
        link=f'[Coverage]({ref})' if ref else 'No coverage record'
        size=coverage_record['sample_size']
        provisional=coverage_record.get('provisional_range')
        shown=(overall if overall is not None else f"{provisional[0]}–{provisional[1]} provisional" if provisional
               else 'See shared row' if kind=='shared_format' else 'Not scored')
        lines.append(f"| {names[config]} | {status} | {size if size is not None else '—'} | {state_text} | {shown} | {rank if rank is not None else 'Not ranked'} | {reason.replace('_',' ')} | {link} |")
        csv_rows.append({'configuration_id':config,'surface':names[config],'evidence_kind':kind,'sample_size':size if size is not None else '', 'coverage':state_text,
                         'overall':overall if overall is not None else '',
                         'provisional_min':provisional[0] if provisional else '', 'provisional_max':provisional[1] if provisional else '',
                         'rank':rank or '', 'remaining_work':reason})
    lines.extend(['','Detailed scored-run records, categories, sensitivity rankings, exact build/model/date identities, and public packet hashes in [scorecard.json](scorecard.json) cover the '+str(verified_count)+' verified configurations. Partial coverage records contain state counts and source pins; private diagnostic details are withheld.','',
                  'Limited-cohort score publication gate: **'+('met' if report['publication_eligible'] else 'not met')+'**. '+f"{verified_count} configurations with three replayed and reviewed runs; {report['attempted_rows']}/{report['scoped_rows']} surfaces have attempt or capture evidence.", '',
                  'All fourteen configurations are represented in this coverage view; full fourteen-configuration scoring remains **'+('complete' if report['release_goal_complete'] else 'incomplete')+'**. This full-scope candidate is **'+('past the automatic release checks' if report['full_scope_publication_eligible'] else 'not past the automatic release checks')+'** while partial diagnostic inputs await public-safety review, independent reproduction, and replay. The separate limited-cohort score gate is reported above.','',
                  'Evidence is frozen by SHA-256. Verify reviews using independently obtained pins before trusting the replay index. Each complete-score packet includes its scoring source and standalone replay script. Platform dependencies, where required, are declared in the packet manifest.'])
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    with (output/'leaderboard.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
    with (output/'metrics.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['configuration_id','run_id','repetition','metric_id','state','percent'])
        writer.writeheader()
        for run in report['runs']:
            for metric_id,value in run['metrics'].items():
                writer.writerow({'configuration_id':run['configuration_id'],'run_id':run['run_id'],'repetition':run['repetition'],'metric_id':metric_id,'state':run['metric_evidence'][metric_id]['state'],'percent':value if value is not None else ''})
    reproduction = ["# Reproduce this candidate", "", "Obtain the independent review hashes from a trusted channel, then verify them against replay-index.json. Do not treat a hash shipped beside its own data as independent approval.", "", "The release manifest hashes every shipped file. Each packet manifest separately pins its complete native, observer, workload, and scoring-source inventory.", "", "For each packet, verify its manifest SHA-256 against the independent review, inspect the bundled source, then run from this directory:", "", "```sh"]
    for item in public_index:
        for row in item['packets']:
            relative=row['path']
            reproduction.append(f"python3 -I -B {relative}/runtime/scripts/replay_score_package.py {relative}")
    reproduction.extend(["```", "", "The runner recomputes all 31 metric states and a selected-response loss control. Never pass --record-expected when verifying an existing packet.", "", "The -B option stops Python from writing bytecode files into the packet; the runner refuses a packet that holds a file its manifest does not list. The OpenClaw packets need Python 3.14 for the standard Zstandard module. Python isolated mode is not an operating-system sandbox. The independent review receipts separately record actual macOS sandbox-exec denial probes and hash/tamper controls. Use the repository's replay_score_package(..., expected_manifest_sha256=..., os_sandboxed=True) API to repeat those checks on macOS.", "", "DeepSeek packets require the exact declared libzstd platform dependency; the library is not bundled. An unavailable or mismatched dependency is a reproduction failure, not a format score. See the packet manifest for its version and hash.", "", "These are explicitly sanitized synthetic-session derivatives. Transformation receipts pin the private originals and record changes. Privacy review and original capture provenance remain operator attestations; deterministic replay does not independently reacquire a live session."])
    (output/'REPRODUCE.md').write_text('\n'.join(reproduction)+'\n')
    inventory=[{'path':path.relative_to(output).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size_bytes':path.stat().st_size} for path in sorted(output.rglob('*')) if path.is_file()]
    write(output/'release-manifest.json',{'schema_version':'session-bench-expanded-release-manifest-v1','files':inventory,
         'ranking_policy_id':N1_POLICY_ID if n1_policy else 'session-bench-ranking-policy-v0.4-three-run',
         'sample_size':candidate_sample_size,
         'publication_eligible':report['publication_eligible'],'full_scope_publication_eligible':report['full_scope_publication_eligible'],
         'partial_coverage_publication_ready':report['partial_coverage_publication_ready'],'release_goal_complete':report['release_goal_complete']})
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--trusted-index',type=Path,required=True);p.add_argument('--statuses',type=Path,default=ROOT/'plans/survival-v1/expanded-release-status.json');p.add_argument('--partial-coverage',type=Path,default=ROOT/'plans/survival-v1/expanded-partial-coverage.json');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--notes',type=Path,help='footnotes and known limits for the report');a=p.parse_args();partial=json.loads(a.partial_coverage.read_bytes()) if a.partial_coverage else None
    report=build(json.loads(a.trusted_index.read_bytes()),json.loads(a.statuses.read_bytes())['rows'],a.output,partial,json.loads(a.notes.read_bytes()) if a.notes else None)
    print(json.dumps({key:report[key] for key in ('scoped_rows','attempted_rows','rankable_rows','publication_eligible','release_goal_complete')}))
