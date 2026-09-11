import copy
import json
from pathlib import Path

import pytest

from session_bench.live_ledger import validate_capture_evidence, validate_live_ledger
from session_bench.live_plan import plan_sha256, validate_live_plan
from session_bench.schema import validate_named


ROOT = Path(__file__).resolve().parents[1]


def plan():
    return json.loads((ROOT / "docs/live/codex-cli-f0-run-plan.json").read_text())


def ledger():
    p = plan()
    return {"schema_version": "1.0-live-ledger", "gate_id": "codex-cli-f0", "plan_sha256": plan_sha256(p), "attempts": []}


def usage(**changes):
    value = {"captured_files": 0, "captured_bytes": 0, "observable_tokens": 0,
             "spend_usd": 0, "operator_minutes": 0, "wall_clock_minutes": 0}
    value.update(changes)
    return value


def test_checked_in_plan_satisfies_machine_contract():
    validate_live_plan(plan())


def test_plan_rejects_quota_delta_without_absolute_stop():
    value = plan()
    value["limits"]["quota"]["absolute_stop_used_percent"] = 99
    with pytest.raises(ValueError, match="absolute stop"):
        validate_live_plan(value)


def test_plan_rejects_preexisting_hashing_and_unfrozen_controls():
    value = plan()
    value["session_discovery"]["preexisting_file_hashing"] = True
    with pytest.raises(ValueError):
        validate_live_plan(value)
    value = plan()
    value["positive_control_candidates"] = list(reversed(value["positive_control_candidates"]))
    with pytest.raises(ValueError, match="fallback"):
        validate_live_plan(value)


def test_ledger_keeps_attempts_distinct_and_bounded():
    p, value = plan(), ledger()
    value["attempts"].append({"scenario_id": "C01", "scenario_run_id": "run-1", "attempt_id": "attempt-1", "state": "started", "native_session_ids": [], "submitted_turns": 1, "config_identity": "cfg", "quota_state": "known", "retry_allowed": True, "usage": usage(), "events": [], "reason": "started"})
    validate_live_ledger(value, p)
    duplicate = copy.deepcopy(value)
    duplicate["attempts"].append(copy.deepcopy(value["attempts"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate_live_ledger(duplicate, p)


def test_capture_contract_is_fail_closed_on_ambiguous_or_preexisting_open():
    p = plan()
    attempt = {"state": "captured", "attempt_id": "a1", "scenario_id": "C01",
               "scenario_run_id": "run-1", "native_session_ids": ["s1"]}
    base = {"attempt_id": "a1", "scenario_id": "C01", "scenario_run_id": "run-1", "native_session_ids": ["s1"],
            "resolved_config_fingerprint": "cfg", "before_stats": [], "after_stats": [],
            "primary_candidate_path": "a", "companion_paths": [], "candidate_paths": ["a"],
            "opened_paths": ["a"], "preexisting_file_hashing": False,
            "ambiguous": False, "source_mutated": False, "observer_frozen": True,
            "quiescence_checks": 2, "candidate_identity_verified": True}
    with pytest.raises(ValueError, match="ambiguous"):
        validate_capture_evidence({**base, "candidate_paths": ["a", "b"], "ambiguous": True}, p, attempt=attempt)
    with pytest.raises(ValueError, match="pre-existing"):
        validate_capture_evidence({**base, "preexisting_file_hashing": True}, p, attempt=attempt)
    validate_capture_evidence({**base, "companion_paths": ["a.wal"],
        "candidate_paths": ["a", "a.wal"], "opened_paths": ["a", "a.wal"]}, p, attempt=attempt)


def test_ledger_binds_plan_and_aggregate_limits():
    p, value = plan(), ledger()
    value["plan_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        validate_live_ledger(value, p)
    value = ledger()
    for number in range(3):
        value["attempts"].append({"scenario_id": "C01", "scenario_run_id": "run-c01",
            "attempt_id": f"attempt-{number}", "state": "started", "native_session_ids": [],
            "submitted_turns": 0, "config_identity": "cfg", "quota_state": "known", "retry_allowed": True, "usage": usage(), "events": [], "reason": "started"})
    with pytest.raises(ValueError, match="per-scenario"):
        validate_live_ledger(value, p)


def test_ledger_rejects_any_attempt_after_unknown_quota_forbids_retry():
    p, value = plan(), ledger()
    base = {"scenario_id": "C01", "scenario_run_id": "run-c01", "state": "interrupted",
            "native_session_ids": [], "submitted_turns": 1, "config_identity": "cfg",
            "usage": usage(), "events": ["quota"], "reason": "quota state"}
    value["attempts"] = [
        {**base, "attempt_id": "attempt-1", "quota_state": "unknown_after_launch", "retry_allowed": False},
        {**base, "attempt_id": "attempt-2", "quota_state": "known", "retry_allowed": True},
    ]
    with pytest.raises(ValueError, match="after retries became forbidden"):
        validate_live_ledger(value, p)
