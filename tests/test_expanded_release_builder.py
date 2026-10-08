import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from session_bench.release_scope import load_release_scope

spec=importlib.util.spec_from_file_location('expanded_builder',Path(__file__).resolve().parents[1]/'scripts/build_expanded_release.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_empty_evidence_reports_every_surface_without_publication(tmp_path):
    statuses=[{'configuration_id':row['configuration_id'],'state':'unattempted','attempt_ids':[],'evidence_refs':[],'reason_ids':[]} for row in load_release_scope()['rows']]
    out=tmp_path/'report'
    report=module.build({'schema_version':'session-bench-trusted-release-index-v1','configurations':[]},statuses,out)
    assert report['scoped_rows']==14
    assert not report['publication_eligible'] and not report['release_goal_complete']
    assert report['rankable_rows']==0 and report['ranks']=={}
    text=(out/'REPORT.md').read_text()
    assert all('| '+row['display_name']+' |' in text for row in load_release_scope()['rows'])
    assert 'N/A' not in text
    assert 'Not scored' in text and 'Not attempted' in text
    assert 'The ranked cohort has 0 configurations' in text
    assert 'cover the 0 verified configurations' in text
    assert 'three-score cohort' not in text
    assert len((out/'leaderboard.csv').read_text().splitlines())==15
    assert len(list((out/'coverage').glob('*.json')))==14
    assert (out/'release-manifest.json').exists()


def test_empty_n1_candidate_declares_single_run_policy_without_repeatability_claims(tmp_path):
    statuses=[{'configuration_id':row['configuration_id'],'state':'unattempted','attempt_ids':[],'evidence_refs':[],'reason_ids':[]} for row in load_release_scope()['rows']]
    out=tmp_path/'n1-report'
    report=module.build({'schema_version':module.TRUSTED_INDEX_N1_SCHEMA,'configurations':[]},statuses,out)
    assert report['sample_size']==1
    assert report['ranking_policy']=={
        'id':module.N1_POLICY_ID,
        'sample_size':1,
        'selection_rule':'prospectively designated repetition 1 for every configuration',
        'repeatability_claims':False,
        'rank_gate':'one complete, independently reviewed and reproduced 31-metric run',
    }
    assert report['rankable_rows']==0 and report['ranks']=={}
    report_text=(out/'REPORT.md').read_text()
    assert 'prospectively designated repetition 1 per configuration' in report_text
    assert 'three complete, independently reviewed' not in report_text
    manifest=json.loads((out/'release-manifest.json').read_bytes())
    assert manifest['ranking_policy_id']==module.N1_POLICY_ID
    assert manifest['sample_size']==1


def test_mixed_candidate_routes_each_bundle_and_reports_per_row_sample_size(tmp_path, monkeypatch):
    from session_bench.release_replay import PUBLIC_BUNDLE_N1_SCHEMA, PUBLIC_BUNDLE_SCHEMA

    statuses=[{'configuration_id':row['configuration_id'],'state':'unattempted','attempt_ids':[],
               'evidence_refs':[],'reason_ids':[]} for row in load_release_scope()['rows']]
    rows=[]
    for config,schema,repetitions in (
        ('claude-cli',PUBLIC_BUNDLE_SCHEMA,(1,2,3)),
        ('pi',PUBLIC_BUNDLE_N1_SCHEMA,(1,)),
    ):
        bundle=tmp_path/f'{config}-bundle.json';bundle.write_text(json.dumps({'schema_version':schema}))
        review=tmp_path/f'{config}-review.json';review.write_text('{}')
        packets=[]
        for repetition in repetitions:
            source=tmp_path/f'{config}-r{repetition}';source.mkdir()
            (source/'manifest.json').write_text('{}')
            packets.append({'run_id':f'{config}-r{repetition}','path':str(source),'manifest_sha256':'a'*64})
        rows.append({'configuration_id':config,'public_inputs':str(bundle),'review':str(review),
                     'trusted_review_sha256':'b'*64,'reviewer_id':'reviewer','producer_id':'producer',
                     'packets':packets})

    calls=[]
    def fake_verifier(expected_size):
        def verify(bundle, review, **kwargs):
            config=next(row['configuration_id'] for row in rows if Path(row['public_inputs']).read_bytes()==bundle)
            calls.append((config,expected_size,set(kwargs['packets'])))
            runs=[SimpleNamespace(run_id=f'{config}-r{repetition}',repetition=repetition,
                                  display=lambda config=config,repetition=repetition: {
                                      'configuration_id':config,'run_id':f'{config}-r{repetition}',
                                      'repetition':repetition,'metrics':{f'metric.{i}':100 for i in range(31)},
                                      'metric_evidence':{f'metric.{i}':{'state':'measured'} for i in range(31)}})
                  for repetition in range(1,expected_size+1)]
            return SimpleNamespace(configuration_id=config,rankable=True,runs=runs)
        return verify
    monkeypatch.setattr(module,'verify_release_configuration',fake_verifier(3))
    monkeypatch.setattr(module,'verify_release_configuration_n1',fake_verifier(1))
    diagnostic_runs=[{'run_id':f'codex-cli-r{repetition}','repetition':repetition,
                      'metric_state_counts':{'measured':30,'native_absent':0,'contradiction':0,'unresolved':1},
                      'provisional_score':{'measured_points':f'6{repetition}.0','possible_max':'90.0'}}
                     for repetition in (1,2,3)]
    monkeypatch.setattr(module,'_load_partial_coverage',lambda _: {
        'partial':{'codex-cli':{'runs':diagnostic_runs,'metric_closures':[],
                                'source_summary_sha256':'c'*64,
                                'review_state':'public_safety_and_independent_replay_pending'}},
        'progress':{},
    })

    def fake_scorecard(scope, statuses, aggregates):
        scored={aggregate.configuration_id for aggregate in aggregates}
        return {'rows':[dict(row) for row in statuses],
                'scores':[{'configuration_id':config,'overall':'80.00','range':['70.00','90.00'],
                           'category_ranges':{'format':['70.00','90.00']}} for config in scored],
                'ranks':{},'rankable_rows':len(scored),'scoped_rows':14,'attempted_rows':len(scored),
                'publication_eligible':False,'release_goal_complete':False}
    monkeypatch.setattr(module,'release_scorecard',fake_scorecard)
    report=module.build({'schema_version':module.TRUSTED_INDEX_N1_SCHEMA,'configurations':rows},statuses,tmp_path/'release',partial_coverage={})
    assert calls==[('claude-cli',3,{'claude-cli-r1','claude-cli-r2','claude-cli-r3'}),
                   ('pi',1,{'pi-r1'})]
    assert report['sample_size']=='mixed'
    assert {row['configuration_id']:row['sample_size'] for row in report['scores']}=={'claude-cli':3,'pi':1}
    scores={row['configuration_id']:row for row in report['scores']}
    assert scores['claude-cli']['range']==['70.00','90.00']
    assert 'range' not in scores['pi'] and 'category_ranges' not in scores['pi']
    coverage=report['coverage']
    assert coverage['claude-cli']['sample_size']==3 and coverage['pi']['sample_size']==1
    assert coverage['codex-cli']['sample_size']==3
    assert coverage['codex-cli']['runs']==diagnostic_runs
    assert all(len(coverage[config]['runs'])==size for config,size in (('claude-cli',3),('pi',1)))
    report_text=(tmp_path/'release'/'REPORT.md').read_text()
    assert 'preserves reviewed n=3 rows' in report_text
    assert '3 private replays; each: 30 measured, 1 unresolved' in report_text
    assert coverage['codex-cli']['provisional_range']==['62.0','90.0']
    assert '| 62.0–90.0 provisional | Not ranked |' in report_text
    assert json.loads((tmp_path/'release'/'release-manifest.json').read_text())['sample_size']=='mixed'


def test_a_surface_that_shares_another_rows_format_is_labelled_and_points_to_that_row(tmp_path, monkeypatch):
    statuses=[{'configuration_id':row['configuration_id'],'state':'unattempted','attempt_ids':[],'evidence_refs':[],'reason_ids':[]} for row in load_release_scope()['rows']]
    detail='Same session format as Codex CLI: one rollout schema, one decoder. Scored on the Codex CLI row.'
    monkeypatch.setattr(module,'_load_partial_coverage',lambda _: {'partial':{},'progress':{
        'codex-desktop':{'configuration_id':'codex-desktop','kind':'shared_format','detail':detail}}})

    report=module.build({'schema_version':'session-bench-trusted-release-index-v1','configurations':[]},statuses,tmp_path/'report',partial_coverage={})

    row=next(line for line in (tmp_path/'report'/'REPORT.md').read_text().splitlines() if line.startswith('| Codex Desktop |'))
    assert '| Shared session format |' in row
    assert detail in row
    assert '| See shared row | Not ranked |' in row
    assert report['coverage']['codex-desktop']['evidence_kind']=='shared_format'


def test_ranking_section_lists_ranked_rows_by_rank_then_provisional_rows_by_measured_points():
    report={'ranks':{'a':2,'b':1},'scores':[{'configuration_id':'a','overall':'80.0'},{'configuration_id':'b','overall':'90.0'}]}
    names={'a':'A','b':'B','c':'C','d':'D','e':'E'}
    coverage={'a':{'sample_size':3},'b':{'sample_size':3},'c':{'provisional_range':['60.0','70.0']},
              'd':{'provisional_range':['65.0','99.0']},'e':{}}

    assert module._ranking_lines(report,names,coverage)==[
        '## Ranking','','| Rank | Configuration | Score | Runs |','|---:|---|---:|---:|',
        '| 1 | B | 90.0 | 3 |','| 2 | A | 80.0 | 3 |','',
        '## Provisional, not ranked','','| Configuration | Measured points | Best possible |','|---|---:|---:|',
        '| D | 65.0 | 99.0 |','| C | 60.0 | 70.0 |','']


def test_metric_state_counts_preserve_contradictions_and_unknowns():
    metrics=[{'id':f'metric.{i}','state':'measured'} for i in range(31)]
    metrics[0]['state']='contradiction'
    metrics[1]['state']='unresolved'
    counts=module._metric_state_counts(metrics)
    assert counts=={'contradiction':1,'measured':29,'native_absent':0,'unresolved':1}
    with pytest.raises(ValueError,match='exactly 31'):
        module._metric_state_counts(metrics[:-1])


def test_metric_state_counts_reject_duplicate_metric_ids():
    metrics=[{'id':f'metric.{i}','state':'measured'} for i in range(31)]
    metrics[-1]['id']=metrics[0]['id']
    with pytest.raises(ValueError,match='duplicate'):
        module._metric_state_counts(metrics)


def test_partial_coverage_requires_pinned_private_diagnostic_sources(tmp_path):
    metrics=[{'id':f'metric.{i}','state':'measured'} for i in range(31)]
    metrics[0]['state']='contradiction'
    metrics[1]['state']='unresolved'
    diagnostics={'intact':{'configuration_id':'codex-cli','run_id':'codex-cli-eval-1','repetition':1,'metrics':metrics}}
    diagnostics_sha256=hashlib.sha256(module.canonical(diagnostics)).hexdigest()
    manifest_sha256='a'*64
    receipt={'diagnostics':diagnostics,'diagnostics_sha256':diagnostics_sha256,'manifest_sha256':manifest_sha256,
             'public_safe':False,'independent_reproduction':False}
    receipt_path=tmp_path/'receipt.json';receipt_path.write_text(json.dumps(receipt))
    summary={'public_safe':False,'independent_reproduction':False,
             'runs':[{'run_id':'codex-cli-eval-1','diagnostics_sha256':diagnostics_sha256,'manifest_sha256':manifest_sha256}]}
    summary_path=tmp_path/'summary.json';summary_path.write_text(json.dumps(summary))
    index={'schema_version':'session-bench-partial-coverage-index-v1','configurations':[
        {'configuration_id':'codex-cli','summary_path':str(summary_path),'runs':[
            {'run_id':'codex-cli-eval-1','receipt_path':str(receipt_path)}]}], 'capture_progress':[]}
    loaded=module._load_partial_coverage(index)
    counts=loaded['partial']['codex-cli']['runs'][0]['metric_state_counts']
    assert counts=={'contradiction':1,'measured':29,'native_absent':0,'unresolved':1}
    assert loaded['partial']['codex-cli']['review_state']=='public_safety_and_independent_replay_pending'

    summary['public_safe']=True
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError,match='remain marked private'):
        module._load_partial_coverage(index)


def test_partial_run_reports_provisional_points_from_its_score_diagnostics(tmp_path):
    metrics=[{'id':f'metric.{i}','state':'measured'} for i in range(31)]
    metrics[1]['state']='unresolved'
    categories={name:{'possible_min':'10.0','possible_max':'12.5'}
                for name in ('record_fidelity','causality_context','usage_attribution','portability_openness','durability_signal')}
    diagnostics={'intact':{'configuration_id':'codex-cli','run_id':'codex-cli-eval-1','repetition':1,'metrics':metrics,
                           'score_diagnostics':{'categories':categories}}}
    diagnostics_sha256=hashlib.sha256(module.canonical(diagnostics)).hexdigest()
    receipt={'diagnostics':diagnostics,'diagnostics_sha256':diagnostics_sha256,'manifest_sha256':'a'*64,
             'public_safe':False,'independent_reproduction':False}
    receipt_path=tmp_path/'receipt.json';receipt_path.write_text(json.dumps(receipt))
    summary={'public_safe':False,'independent_reproduction':False,
             'runs':[{'run_id':'codex-cli-eval-1','diagnostics_sha256':diagnostics_sha256,'manifest_sha256':'a'*64}]}
    summary_path=tmp_path/'summary.json';summary_path.write_text(json.dumps(summary))
    index={'schema_version':'session-bench-partial-coverage-index-v1','configurations':[
        {'configuration_id':'codex-cli','summary_path':str(summary_path),'runs':[
            {'run_id':'codex-cli-eval-1','receipt_path':str(receipt_path)}]}], 'capture_progress':[]}

    run=module._load_partial_coverage(index)['partial']['codex-cli']['runs'][0]

    assert run['provisional_score']=={'measured_points':'50.0','possible_max':'62.5'}


def test_pinned_codex_metric_closures_raise_the_provisional_floor_by_their_points():
    # 67.6 measured in the pinned replay, plus changed files (4), event
    # timestamps (2) and stable root (2) closed by the pinned diagnostics.
    root=Path(__file__).resolve().parents[1]
    index=json.loads((root/'plans/survival-v1/expanded-partial-coverage-v12.json').read_bytes())

    run=module._load_partial_coverage(index)['partial']['codex-cli']['runs'][0]

    assert run['provisional_score']=={'measured_points':'75.6','possible_max':'94.6'}


def test_cannot_overwrite_frozen_release(tmp_path):
    with pytest.raises(ValueError,match='new directory'):
        module.build({},[],tmp_path)


def test_pinned_codex_metric_closures_change_only_private_coverage_counts():
    root=Path(__file__).resolve().parents[1]
    index=json.loads((root/'plans/survival-v1/expanded-partial-coverage-v12.json').read_bytes())
    loaded=module._load_partial_coverage(index)
    codex=loaded['partial']['codex-cli']
    assert [row['metric_id'] for row in codex['metric_closures']]==[
        'broad.event_timestamps','broad.stable_root_location','work.changed_files']
    assert len({row['source_sha256'] for row in codex['metric_closures']})==3
    assert [row['metric_state_counts'] for row in codex['runs']]==[
        {'measured':24,'contradiction':1,'native_absent':0,'unresolved':6}]*3
    assert loaded['progress']['hermes']['qualified_two_turn_captures']==2


def test_metric_closure_rejects_wrong_source_pin_or_run_identity(tmp_path):
    root=Path(__file__).resolve().parents[1]
    original=json.loads((root/'plans/survival-v1/expanded-partial-coverage-v12.json').read_bytes())
    index=json.loads(json.dumps(original))
    codex=next(row for row in index['configurations'] if row['configuration_id']=='codex-cli')
    codex['metric_closures'][0]['sha256']='0'*64
    with pytest.raises(ValueError,match='source hash mismatch'):
        module._load_partial_coverage(index)

    index=json.loads(json.dumps(original))
    codex=next(row for row in index['configurations'] if row['configuration_id']=='codex-cli')
    overlay=json.loads((root/codex['metric_closures'][0]['path']).read_bytes())
    overlay['bindings'][0]['run_id']='different-run'
    replacement=tmp_path/'wrong-binding.json';replacement.write_text(json.dumps(overlay))
    codex['metric_closures'][0]={'path':str(replacement),'sha256':hashlib.sha256(replacement.read_bytes()).hexdigest()}
    with pytest.raises(ValueError,match='exact replay receipts'):
        module._load_partial_coverage(index)

    overlay['bindings'][0]['run_id']='codex-cli-eval-1'
    overlay['rankable']=True
    replacement.write_text(json.dumps(overlay))
    codex['metric_closures'][0]['sha256']=hashlib.sha256(replacement.read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='private one-metric'):
        module._load_partial_coverage(index)
