"""Antigravity replay preserves evidence gaps and detects selected loss."""
from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from session_bench.score_replay import replay_score_package, verify_score_packet_tamper_controls
from session_bench.antigravity_root_evidence import capture_session_family, inventory
from session_bench.antigravity_score_inputs import _bound_final_response_usage, validate_bounded_root_receipt
from session_bench.antigravity_response_usage import project_final_response_usage

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('antigravity_successor', ROOT / 'scripts/build_antigravity_partial_score_replays.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_antigravity_closed_replay_has_31_source_bound_rows_and_loss(tmp_path):
    output = tmp_path / 'successor'
    summary = builder.build(output)
    assert summary['overall_rank'] is None
    assert [row['resolved_metric_count'] for row in summary['runs']] == [10, 10, 10]
    for row in summary['runs']:
        packet = output / row['packet']
        pin = hashlib.sha256((packet / 'manifest.json').read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=pin)
        intact = receipt['diagnostics']['intact']
        loss = receipt['diagnostics']['selected_loss']
        metrics = {metric['id']: metric for metric in intact['metrics']}
        assert len(metrics) == 31
        assert intact['score_diagnostics']['overall'] is None
        assert not intact['score_diagnostics']['rankable']
        assert metrics['work.submitted_turns']['observed_eligible'] == 2
        assert metrics['work.visible_responses']['observed_eligible'] == 2
        assert metrics['work.changed_files']['state'] == 'measured'
        assert metrics['work.changed_files']['correct'] == 1
        assert all(metrics[name]['state'] == 'unresolved' for name in (
            'work.results', 'causal.action_result', 'attribution.model_config',
            'attribution.usage', 'attribution.token_semantics', 'attribution.reconciliation',
            'portable.complete_root', 'portable.companions'))
        assert all(metric['state'] == 'unresolved' for metric in intact['metrics'][19:])
        assert loss['observer_denominator_unchanged'] and loss['response_correctness_reduced']
        assert len(loss['removed_records']) == 1
    packet = output / summary['runs'][0]['packet']
    pin = hashlib.sha256((packet / 'manifest.json').read_bytes()).hexdigest()
    assert verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)['status'] == 'passed'


def test_antigravity_capture_companion_tamper_rejected_before_code_runs(tmp_path, monkeypatch):
    output = tmp_path / 'successor'
    builder.build(output)
    packet = output / 'antigravity-2026-09-29-02'
    pin = hashlib.sha256((packet / 'manifest.json').read_bytes()).hexdigest()
    companion = next((packet / 'inputs/capture/native').rglob('transcript_full.jsonl'))
    companion.write_bytes(companion.read_bytes() + b'\n')
    monkeypatch.setattr('session_bench.score_replay.subprocess.run',
                        lambda *args, **kwargs: pytest.fail('unverified code executed'))
    with pytest.raises(ValueError, match='inventory mismatch'):
        replay_score_package(packet, expected_manifest_sha256=pin)


def test_new_session_root_receipt_is_bounded_and_source_bound(tmp_path):
    root = tmp_path / 'brain'
    root.mkdir()
    (root / 'old-session').mkdir()
    (root / 'old-session' / 'private.txt').write_text('do not read')
    before = inventory(root)
    primary = 'new-session/.system_generated/logs/transcript.jsonl'
    target = root / primary
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"step_index":1}\n')
    (root / 'new-session' / 'sidecar.bin').write_bytes(b'sidecar')
    receipt = capture_session_family(root, tmp_path / 'copy', before, inventory(root), primary,
                                     sleep=lambda _: None, attempt_id='test-run', turn=2)
    docs = {'native/' + row['relative_path']: (tmp_path / 'copy' / row['relative_path']).read_bytes()
            for row in receipt['artifacts']}
    index = {'artifacts': [{'path': 'capture/' + row['relative_path']} for row in receipt['artifacts']]}
    assert validate_bounded_root_receipt(receipt, docs, index, run_id='test-run', primary_path=primary)
    assert receipt['external_companions_established'] is False
    altered = json.loads(json.dumps(receipt))
    altered['external_companions_established'] = True
    with pytest.raises(ValueError, match='overclaims'):
        validate_bounded_root_receipt(altered, docs, index, run_id='test-run', primary_path=primary)
    docs['native/new-session/sidecar.bin'] += b'x'
    with pytest.raises(ValueError, match='member differs'):
        validate_bounded_root_receipt(receipt, docs, index, run_id='test-run', primary_path=primary)


def test_adapter_usage_projection_matches_source_contract_on_retained_streams():
    captures = ROOT / 'artifacts/v1-expanded-preparation/live-captures'
    for path in sorted(captures.glob('antigravity-2026-09-29-0[234]/qualification-v4')):
        observer = json.loads((path / 'observer.json').read_bytes())
        responses = {event['id']: event['fields']['text'] for event in observer['events']
                     if event['kind'] == 'assistant_response'}
        session = json.loads((path / 'capture-result.json').read_bytes())['native_primary'].split('/')[0]
        for turn in (1, 2):
            raw = (path / f'r{turn}.stdout.jsonl').read_bytes()
            args = {'expected_response': responses[f'response-r{turn}'], 'expected_session_id': session}
            assert _bound_final_response_usage(raw, **args) == project_final_response_usage(raw, **args)
