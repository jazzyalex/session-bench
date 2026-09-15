"""Controls for the offline prototype-to-survival-v1 compatibility bridge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.survival_bridge import (
    CANONICAL_METRIC_IDS,
    build_pretest_report,
    build_survival_input,
    native_decode_digest,
    write_pretest_report,
)
from session_bench.survival_metrics import BLOCKING_STATES, SCHEMA_VERSION, score_run, validate_input


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_capture(
    tmp_path: Path,
    name: str = "codex-cli-pilot-01",
    *,
    observer: bool = True,
    complete: bool = False,
    corrupt_native: bool = False,
) -> Path:
    root = tmp_path / name
    native = root / "native" / "rollout.jsonl"
    native.parent.mkdir(parents=True)
    rows = [
        {
            "type": "session_meta",
            "payload": {
                "id": "legacy-session",
                "cwd": "/tmp/legacy-workspace",
                "source": "exec",
                "cli_version": "0.154.0",
                "model": "legacy-model",
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "legacy first turn"},
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "legacy correction"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "local_shell_call",
                "call_id": "legacy-call",
                "action": {"type": "exec", "command": ["python3", "check.py"]},
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "legacy-call",
                "output": "Process exited with code 1",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "agent_message",
                "id": "legacy-response",
                "content": [{"type": "output_text", "text": "legacy response"}],
            },
        },
    ]
    payload = b"".join(_canonical(row) + b"\n" for row in rows)
    native.write_bytes(payload)
    native_files = [
        {
            "path": "native/rollout.jsonl",
            "sha256": _sha(native),
            "bytes": native.stat().st_size,
            "kind": "codex-rollout-jsonl",
        }
    ]
    metadata = {
        "schema_version": "session-bench-prototype-capture-v1",
        "id": name.split("-pilot", 1)[0],
        "name": "Legacy prototype capture",
        "surface": "CLI / legacy prototype",
        "version": "0.154.0",
        "model": "legacy-model",
        "run_id": name,
        "captured_at": "2026-09-11T00:00:00Z",
        "native_files": native_files,
        "required_native_files": ["native/rollout.jsonl"],
        "artifact_set_complete": complete,
        "status": "pilot",
    }
    (root / "metadata.json").write_bytes(_canonical(metadata))
    if observer:
        manifest = hashlib.sha256(_canonical(native_files)).hexdigest()
        observer_value = {
            "schema_version": "session-bench-prototype-observer-v1",
            "run_id": name,
            "artifact_manifest_sha256": manifest,
            "independent": True,
            "observer_method": "submitted input and live CLI stream",
            "events": [
                {
                    "id": "legacy-r1",
                    "kind": "user_message",
                    "source": "submitted_input",
                    "text": "legacy first turn",
                    "expected_native": True,
                },
                {
                    "id": "legacy-r2",
                    "kind": "correction",
                    "source": "submitted_input",
                    "text": "legacy correction",
                    "expected_native": True,
                },
                {
                    "id": "legacy-response",
                    "kind": "assistant_message",
                    "source": "live_cli_stream",
                    "text": "legacy response",
                    "expected_native": True,
                },
            ],
        }
        (root / "observer.json").write_bytes(_canonical(observer_value))
    if corrupt_native:
        native.write_bytes(native.read_bytes() + b"corruption\n")
    return root


def test_bridge_emits_exact_blocked_19_row_input(tmp_path: Path):
    capture = make_capture(tmp_path)
    document = build_survival_input(capture)

    assert set(document) == {
        "schema_version",
        "run_id",
        "configuration_id",
        "repetition",
        "metrics",
    }
    assert document["schema_version"] == SCHEMA_VERSION
    assert [row["id"] for row in document["metrics"]] == list(CANONICAL_METRIC_IDS)
    assert len(document["metrics"]) == 19
    assert {row["state"] for row in document["metrics"]} <= BLOCKING_STATES
    assert document["metrics"][0]["observed_eligible"] == 2
    assert document["metrics"][0]["correct"] == 0
    assert validate_input(document) == document

    scored = score_run(document)
    assert scored.overall is None
    assert scored.rankable is False
    assert scored.portable_gate is False
    assert all(value is None for value in scored.metrics.values())


def test_incomplete_legacy_capture_never_becomes_native_absence(tmp_path: Path):
    document = build_survival_input(make_capture(tmp_path, observer=False))

    assert all(row["state"] not in {"native_absent", "contradiction"} for row in document["metrics"])
    assert all(row["observed_eligible"] == 0 for row in document["metrics"])
    assert score_run(document).rankable is False


def test_integrity_failure_becomes_invalid_capture_for_every_metric(tmp_path: Path):
    document = build_survival_input(make_capture(tmp_path, corrupt_native=True))

    assert {row["state"] for row in document["metrics"]} == {"invalid_capture"}
    scored = score_run(document)
    assert scored.overall is None
    assert scored.rankable is False
    assert all(value is None for value in scored.metrics.values())


def test_pretest_report_is_redacted_and_cannot_create_leaderboard_rows(tmp_path: Path):
    captures = [
        make_capture(tmp_path, "codex-cli-pilot-01"),
        make_capture(tmp_path, "codex-desktop-pilot-01"),
    ]
    report = build_pretest_report(captures)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["schema_version"] == "session-bench-survival-pretest-v1"
    assert report["publishable"] is False
    assert report["leaderboard_eligible"] is False
    assert report["capture_count"] == 2
    assert report["rankable_count"] == 0
    assert all(record["publishable"] is False for record in report["captures"])
    assert all(record["survival"]["overall"] is None for record in report["captures"])
    assert str(tmp_path) not in encoded
    assert "evidence_path" not in encoded
    assert "legacy response" not in encoded


def test_report_writer_refuses_to_write_inside_capture_root(tmp_path: Path):
    capture = make_capture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_pretest_report([capture], capture / "pretest.json")


def test_native_decode_is_copy_stable_and_observer_blind(tmp_path: Path):
    capture = make_capture(tmp_path)
    copied = tmp_path / "copied-capture"
    shutil.copytree(capture, copied)

    original_digest = native_decode_digest(capture)
    copied_digest = native_decode_digest(copied)
    assert copied_digest == original_digest

    observer_path = copied / "observer.json"
    observer_value = json.loads(observer_path.read_text(encoding="utf-8"))
    observer_value["events"] = [
        {
            "id": "fabricated",
            "kind": "assistant_message",
            "source": "live_cli_stream",
            "text": "observer-only change",
            "expected_native": True,
        }
    ]
    observer_path.write_bytes(_canonical(observer_value))
    assert native_decode_digest(copied) == original_digest


def test_bridge_requires_one_explicit_capture_root(tmp_path: Path):
    with pytest.raises(ValueError):
        build_survival_input(tmp_path)
