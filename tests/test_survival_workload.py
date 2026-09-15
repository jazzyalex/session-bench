"""Focused Phase 1 controls for the frozen survival-v1 workload and observer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from session_bench.schema import validate


ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "fixtures" / "scenarios" / "survival-v1" / "workload"
METRIC_IDS = {
    "work.submitted_turns",
    "work.visible_responses",
    "work.actions",
    "work.results",
    "work.changed_files",
    "causal.action_result",
    "causal.turn_response",
    "revision.r1",
    "revision.r2",
    "revision.r1_r2_order",
    "revision.final_after_r2",
    "attribution.model_config",
    "attribution.usage",
    "attribution.token_semantics",
    "attribution.reconciliation",
    "portable.complete_root",
    "portable.companions",
    "portable.isolated_decode",
    "portable.canonical_equality",
}
SURFACES = {
    "codex-cli",
    "codex-desktop",
    "cursor-cli",
    "cursor-desktop",
    "opencode-cli",
}


def read(name: str) -> dict:
    return json.loads((WORKLOAD / name).read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_helper():
    path = WORKLOAD / "fixture_project" / "bench_check.py"
    spec = importlib.util.spec_from_file_location("survival_v1_helper", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_workload_and_observer_match_closed_schemas() -> None:
    workload = read("workload.json")
    observer = read("observer-truth.json")
    validate(workload, read("workload.schema.json"))
    validate(observer, read("observer.schema.json"))
    assert workload["schema_version"] == "1.0-survival-workload"
    assert observer["schema_version"] == "1.0-survival-observer"
    assert observer["independent"] is True


def test_frozen_hashes_and_file_boundary_have_no_placeholders() -> None:
    workload = read("workload.json")
    filesystem = read("filesystem.json")
    helper_path = WORKLOAD / "fixture_project" / "bench_check.py"
    before_path = WORKLOAD / "fixture_project" / "snapshots" / "checkout.before.py"
    after_path = WORKLOAD / "fixture_project" / "snapshots" / "checkout.after.py"
    checkout_path = WORKLOAD / "fixture_project" / "checkout.py"
    zero_digest = "0" * 64

    assert sha256(helper_path) == workload["helper"]["sha256"]
    assert sha256(checkout_path) == sha256(before_path) == workload["filesystem"]["before_sha256"]
    assert sha256(after_path) == workload["filesystem"]["reference_after_sha256"]
    assert workload["filesystem"]["before_sha256"] != workload["filesystem"]["reference_after_sha256"]
    assert "reference bytes are not required" in workload["filesystem"]["success_rule"]
    assert filesystem["after_snapshot_role"].startswith("reference valid solution")
    assert filesystem["changed"] == [
        {
            "path": "fixture_project/checkout.py",
            "before_sha256": workload["filesystem"]["before_sha256"],
            "after_sha256": workload["filesystem"]["reference_after_sha256"],
        }
    ]
    for path in (helper_path, before_path, after_path, checkout_path):
        assert zero_digest not in path.read_text(encoding="utf-8")
    assert all(
        value != zero_digest
        for value in (
            workload["helper"]["sha256"],
            workload["filesystem"]["before_sha256"],
            workload["filesystem"]["reference_after_sha256"],
            *[entry["sha256"] for phase in ("before", "after") for entry in filesystem[phase]],
            filesystem["changed"][0]["before_sha256"],
            filesystem["changed"][0]["after_sha256"],
        )
    )


def test_one_canary_set_is_shared_by_all_five_surfaces() -> None:
    workload = read("workload.json")
    canaries = read("response-canaries.json")
    assert set(canaries["surfaces"]) == SURFACES
    assert canaries["run_canary_prefix"] == "SB_SURVIVAL_V1_RUN_"
    assert set(canaries["rules"]) == {
        "same_for_all_surfaces",
        "response_must_end_with_canary",
        "generated_response_text_is_not_answer_key",
        "intermediate_deltas_scored",
    }
    assert canaries["rules"] == {
        "same_for_all_surfaces": True,
        "response_must_end_with_canary": True,
        "generated_response_text_is_not_answer_key": True,
        "intermediate_deltas_scored": False,
    }
    assert workload["response_canaries"] == [
        {
            **entry,
            "surface_scope": sorted(SURFACES),
        }
        for entry in canaries["canaries"]
    ]
    turns = {turn["id"]: turn for turn in workload["turns"]}
    assert [turn["revision"] for turn in workload["turns"]] == ["r1", "r2"]
    assert [turns[item["turn_id"]]["response_canary"] for item in canaries["canaries"]] == [item["value"] for item in canaries["canaries"]]
    assert canaries["context_marker"] == workload["context_marker"]


def test_helper_is_deterministic_and_records_failure_edit_pass(tmp_path: Path) -> None:
    source = WORKLOAD / "fixture_project"
    copies = []
    for index in (1, 2):
        target = tmp_path / f"run-{index}" / "fixture_project"
        target.parent.mkdir()
        shutil.copytree(source, target)
        copies.append(target)

    outputs = []
    for target in copies:
        inspect = subprocess.run([sys.executable, "bench_check.py", "inspect"], cwd=target, capture_output=True, text=True)
        baseline = subprocess.run([sys.executable, "bench_check.py", "baseline"], cwd=target, capture_output=True, text=True)
        assert inspect.returncode == 0
        assert baseline.returncode == 1
        shutil.copy2(target / "snapshots" / "checkout.after.py", target / "checkout.py")
        final = subprocess.run([sys.executable, "bench_check.py", "final"], cwd=target, capture_output=True, text=True)
        assert final.returncode == 0
        ledger = target / ".survival-observer.jsonl"
        outputs.append((inspect.stdout, baseline.stdout, final.stdout, ledger.read_bytes()))

    assert outputs[0] == outputs[1]
    rows = [json.loads(line) for line in outputs[0][3].decode("utf-8").splitlines()]
    assert [row["phase"] for row in rows] == ["inspect", "baseline", "final"]
    assert [row["exit_code"] for row in rows] == [0, 1, 0]
    assert [row["helper_nonce"] for row in rows] == [
        "inspect-fixture-0001",
        "baseline-fixture-0001",
        "final-fixture-0001",
    ]
    assert all(row["run_canary"] == "SB_SURVIVAL_V1_RUN_fixture_0001" for row in rows)


def test_helper_accepts_per_attempt_run_canary_without_changing_fixture_bytes(tmp_path: Path) -> None:
    target = tmp_path / "fixture_project"
    shutil.copytree(WORKLOAD / "fixture_project", target)
    before = sha256(target / "bench_check.py")
    environment = dict(os.environ, SB_SURVIVAL_V1_RUN_CANARY="SB_SURVIVAL_V1_RUN_eval_02")
    result = subprocess.run(
        [sys.executable, "bench_check.py", "inspect"],
        cwd=target,
        capture_output=True,
        text=True,
        env=environment,
    )
    row = json.loads((target / ".survival-observer.jsonl").read_text().strip())
    assert result.returncode == 0
    assert row["run_canary"] == "SB_SURVIVAL_V1_RUN_eval_02"
    assert sha256(target / "bench_check.py") == before


def test_observer_truth_has_all_frozen_populations_and_explicit_relations() -> None:
    observer = read("observer-truth.json")
    events = observer["events"]
    relations = observer["relations"]
    ids = [event["id"] for event in events]
    assert len(ids) == len(set(ids))
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert set().union(*(set(event["metric_ids"]) for event in events)) == METRIC_IDS
    assert sum(event["kind"] == "user_turn" and event["population_role"] == "primary_scored" for event in events) == 2
    assert sum(event["kind"] == "assistant_response" and event["population_role"] == "primary_scored" for event in events) == 2
    assert sum(event["kind"] == "action" and event["population_role"] == "primary_scored" for event in events) == 4
    assert sum(event["kind"] == "result" and event["population_role"] == "primary_scored" for event in events) == 4
    assert sum(event["kind"] == "file_change" and event["population_role"] == "primary_scored" for event in events) == 1
    assert {relation["kind"] for relation in relations} == {"action_result", "turn_response", "supersedes", "final_after", "helper_for"}
    event_ids = set(ids)
    for relation in relations:
        assert relation["from_id"] in event_ids
        assert relation["to_id"] in event_ids
    action_result = [relation for relation in relations if relation["kind"] == "action_result"]
    assert len(action_result) == 4
    assert {(relation["from_id"], relation["to_id"]) for relation in action_result} == {
        ("action-inspect", "result-inspect"),
        ("action-baseline", "result-baseline"),
        ("action-edit", "result-edit"),
        ("action-final", "result-final"),
    }


def test_observer_primary_events_match_workload_actions_and_results() -> None:
    workload = read("workload.json")
    observer = read("observer-truth.json")
    actions = {action["id"]: action for action in workload["actions"]}
    results = {result["id"]: result for result in workload["results"]}
    observed_actions = {event["id"]: event for event in observer["events"] if event["kind"] == "action"}
    observed_results = {event["id"]: event for event in observer["events"] if event["kind"] == "result"}
    assert set(observed_actions) == set(actions)
    assert set(observed_results) == {result_id.replace("action", "result") for result_id in actions}
    for action_id, action in actions.items():
        fields = observed_actions[action_id]["fields"]
        assert fields["argv"] == action["argv"]
        assert fields["cwd"] == action["cwd"]
        assert fields["target"] == action["target"]
        result_id = action_id.replace("action", "result")
        assert observed_results[result_id]["fields"]["action_id"] == action_id
        assert observed_results[result_id]["fields"]["exit_code"] == results[result_id]["exit_code"]
        assert observed_results[result_id]["fields"]["status"] == results[result_id]["status"]


@pytest.mark.parametrize("name", ["workload.schema.json", "observer.schema.json", "response-canaries.json", "filesystem.json"])
def test_public_fixture_json_is_strict_json(name: str) -> None:
    value = json.loads((WORKLOAD / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
