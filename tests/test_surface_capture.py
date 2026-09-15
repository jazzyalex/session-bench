"""Focused synthetic tests for the shared survival-v1 capture contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_bench.surface_capture import (
    ArgvEnvAllowlist,
    AttemptManifest,
    CaptureError,
    IndependentStdoutObserver,
    ImmutableAttemptLedger,
    IsolatedRoots,
    inventory_diff,
    inventory_tree,
    sha256_json,
    sha256_file,
    validate_attempt_manifest,
    wait_for_quiescence,
)


def roots(tmp_path: Path) -> IsolatedRoots:
    run = tmp_path / "run"
    paths = [run / name for name in ("project", "native", "observer", "capture")]
    run.mkdir()
    for path in paths:
        path.mkdir()
    value = IsolatedRoots(run, *paths)
    value.validate()
    return value


def manifest(tmp_path: Path, *, status: str = "blocked") -> AttemptManifest:
    r = roots(tmp_path)
    allowlist = ArgvEnvAllowlist(
        required_argv=("exec", "--ignore-user-config", "--json"),
        forbidden_argv=("--ephemeral",),
        allowed_env=("CODEX_HOME",),
        required_env=("CODEX_HOME",),
        forbidden_env=("HOME",),
    )
    observer = IndependentStdoutObserver()
    observed = observer.freeze()
    config = {"build": "fixture", "configuration": "isolated"}
    return AttemptManifest(
        {
            "schema_version": "1.0-survival-surface-capture",
            "kind": "attempt",
            "attempt_id": "attempt-1",
            "result_id": "result-1",
            "surface_id": "fixture",
            "config_identity": config,
            "config_sha256": sha256_json(config),
            "argv": ["fixture", "exec", "--ignore-user-config", "--json"],
            "env": {"CODEX_HOME": str(r.native_root)},
            "argv_env_allowlist": allowlist.to_dict(),
            "roots": r.as_dict(),
            "before_inventory": [],
            "after_inventory": [],
            "quiescence": {"checks": 0, "interval_seconds": 0.0, "stable": False, "observed": []},
            "observer": observed,
            "artifacts": [],
            "limits": {"max_files": 2, "max_file_bytes": 1024, "max_total_bytes": 2048},
            "status": status,
            "reason": "authentication prerequisite required",
            "started_ns": 1,
            "ended_ns": 2,
        }
    )


def test_inventory_diff_is_metadata_only_and_quiescence_is_repeatable(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    before = inventory_tree(root)
    path = root / "sessions"
    path.mkdir()
    rollout = path / "rollout-new.jsonl"
    rollout.write_bytes(b"native")
    after = inventory_tree(root)
    candidates = inventory_diff(before, after)
    assert [item.relative_path for item in candidates] == ["sessions/rollout-new.jsonl"]
    receipt = wait_for_quiescence(root, candidates, sleep=lambda _: None)
    assert receipt.stable is True
    assert len(receipt.observed) == 2


def test_copy_hashes_only_proven_new_files_and_respects_immutable_destination(tmp_path: Path) -> None:
    root = tmp_path / "native"
    root.mkdir()
    source = root / "rollout-new.jsonl"
    source.write_bytes(b"native")
    entry = inventory_tree(root)[0]
    from session_bench.surface_capture import copy_verified_artifacts

    output = tmp_path / "capture"
    artifacts = copy_verified_artifacts(root, output, [entry], max_file_bytes=64, max_total_bytes=64)
    assert artifacts[0]["sha256"] == sha256_file(source, max_bytes=64)[0]
    with pytest.raises(CaptureError, match="already exists"):
        copy_verified_artifacts(root, output, [entry], max_file_bytes=64, max_total_bytes=64)


def test_stdout_observer_freezes_independently_and_rejects_late_data(tmp_path: Path) -> None:
    observer = IndependentStdoutObserver(max_bytes=32)
    observer.observe(b"one\n", timestamp_ns=1)
    receipt = observer.freeze(tmp_path / "stdout.jsonl")
    assert receipt["independent"] is True
    assert receipt["frozen"] is True
    with pytest.raises(CaptureError, match="frozen"):
        observer.observe("late")


def test_manifest_is_closed_and_ledger_preserves_prior_attempt_bytes(tmp_path: Path) -> None:
    attempt = manifest(tmp_path)
    value = attempt.to_dict()
    assert validate_attempt_manifest(value)["attempt_id"] == "attempt-1"
    value["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        validate_attempt_manifest(value)

    ledger_path = tmp_path / "attempts.jsonl"
    ledger = ImmutableAttemptLedger(ledger_path)
    ledger.append(attempt)
    before = ledger_path.read_bytes()
    with pytest.raises(CaptureError, match="already retained"):
        ledger.append(attempt)
    assert ledger_path.read_bytes() == before
    assert json.loads(before.decode().splitlines()[0])["attempt_id"] == "attempt-1"
