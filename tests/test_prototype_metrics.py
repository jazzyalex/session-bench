from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from session_bench.prototype_metrics import analyze_capture, build_report


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def make_capture(tmp_path: Path, *, capture_id: str = "codex-cli", run_id: str = "run-1",
                 status: str = "pilot", complete: bool = True, malformed: bool = False,
                 observer: bool = True, events: list[dict] | None = None) -> Path:
    root = tmp_path / run_id
    native = root / "native" / "rollout.jsonl"
    native.parent.mkdir(parents=True)
    rows = [
        {"timestamp": "2026-09-10T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/tmp/work", "source": "cli", "cli_version": "1.2.3", "model": "gpt-test"}},
        {"timestamp": "2026-09-10T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "Keep MARKER."}},
        {"timestamp": "2026-09-10T00:00:02Z", "type": "response_item", "payload": {"type": "local_shell_call", "call_id": "call-1", "status": "completed", "action": {"type": "exec", "command": ["python", "test.py"]}}},
        {"timestamp": "2026-09-10T00:00:03Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "Process exited with code 1\nfailure"}},
        {"timestamp": "2026-09-10T00:00:04Z", "type": "response_item", "payload": {"type": "function_call", "name": "apply_patch", "call_id": "call-2", "arguments": "*** Begin Patch\n*** Update File: target.py\n*** End Patch"}},
        {"timestamp": "2026-09-10T00:00:05Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-2", "output": "Done!"}},
        {"timestamp": "2026-09-10T00:00:06Z", "type": "response_item", "payload": {"type": "agent_message", "id": "a1", "content": [{"type": "output_text", "text": "Plan then explanation."}]}},
        {"timestamp": "2026-09-10T00:00:07Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2, "total_tokens": 17}}, "cost": "unknown"}},
        {"timestamp": "2026-09-10T00:00:08Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 12, "output_tokens": 7, "cached_input_tokens": 2, "total_tokens": 21}}, "cost": "unknown"}},
    ]
    payload = b"{bad\n" if malformed else b"".join(canonical(row) + b"\n" for row in rows)
    native.write_bytes(payload)
    native_files = [{"path": "native/rollout.jsonl", "sha256": hashlib.sha256(payload).hexdigest(),
                     "bytes": len(payload), "kind": "codex-rollout-jsonl"}]
    metadata = {"schema_version": "session-bench-prototype-capture-v1", "id": capture_id,
                "name": "Codex CLI test", "surface": "cli", "version": "1.2.3",
                "model": "gpt-test", "run_id": run_id, "captured_at": "2026-09-10T00:01:00Z",
                "native_files": native_files, "required_native_files": ["native/rollout.jsonl"],
                "artifact_set_complete": complete, "status": status}
    (root / "metadata.json").write_bytes(canonical(metadata))
    if observer:
        observed = events if events is not None else [
            {"id": "u1", "kind": "user_message", "source": "submitted_input", "text": "Keep MARKER.", "context_marker": True, "expected_native": True},
            {"id": "t1", "kind": "tool_call", "source": "disk_ledger", "tool": "exec", "arguments": {"type": "exec", "command": ["python", "test.py"]}, "expected_native": True},
            {"id": "r1", "kind": "tool_result", "source": "disk_ledger", "result": "Process exited with code 1\nfailure", "expected_native": True},
            {"id": "f1", "kind": "failure", "source": "disk_ledger", "text": "failure", "expected_native": True},
            {"id": "e1", "kind": "file_change", "source": "disk_ledger", "file": "target.py", "expected_native": True},
            {"id": "p1", "kind": "plan", "source": "live_cli_stream", "text": "Plan then explanation.", "expected_native": True},
        ]
        observer_value = {"schema_version": "session-bench-prototype-observer-v1", "run_id": run_id,
                          "artifact_manifest_sha256": hashlib.sha256(canonical(native_files)).hexdigest(),
                          "independent": True, "observer_method": "submitted input, disk ledger, live CLI stream",
                          "events": observed}
        (root / "observer.json").write_bytes(canonical(observer_value))
    return root


def metric(result, check_id):
    return next(item for item in result["measurements"] if item["id"] == check_id)


def test_pilot_produces_five_category_scores_but_no_ranked_total(tmp_path: Path):
    result = analyze_capture(make_capture(tmp_path))
    assert result["score"] is None
    assert result["rankable"] is False
    assert len(result["categories"]) == 5
    assert metric(result, "W1")["score"] == 100, result["limitations"]
    assert metric(result, "W3")["score"] == 100
    assert metric(result, "W4")["score"] == 100
    assert metric(result, "C3")["score"] == 100
    assert metric(result, "U2")["score"] == 100
    assert metric(result, "U3")["score"] == 100
    assert metric(result, "U1")["state"] == "unresolved"
    assert metric(result, "U1")["score"] is None
    assert sum(item["bytes"] for item in result["composition"]) == result["native_bytes"]


def test_duplicate_observer_ids_invalidate_binding_and_do_not_inflate_coverage(tmp_path: Path):
    duplicate = [
        {"id": "same", "kind": "user_message", "source": "submitted_input", "text": "Keep MARKER.", "expected_native": True},
        {"id": "same", "kind": "user_message", "source": "submitted_input", "text": "Keep MARKER.", "expected_native": True},
    ]
    result = analyze_capture(make_capture(tmp_path, events=duplicate))
    assert metric(result, "W1")["score"] is None
    assert metric(result, "W1")["state"] == "unresolved"
    assert result["coverage"] < 100
    assert any("duplicated" in value for value in result["limitations"])


def test_malformed_native_is_decoder_unsupported_not_native_absence(tmp_path: Path):
    result = analyze_capture(make_capture(tmp_path, malformed=True))
    assert metric(result, "W1")["state"] == "decoder_unsupported"
    assert metric(result, "A2")["state"] == "decoder_unsupported"
    assert result["score"] is None
    assert any("Malformed native" in value for value in result["limitations"])


def test_missing_observer_and_incomplete_boundary_are_unresolved(tmp_path: Path):
    result = analyze_capture(make_capture(tmp_path, observer=False, complete=False))
    assert metric(result, "W1")["state"] == "unresolved"
    assert metric(result, "S1")["state"] == "unresolved"
    assert metric(result, "A4")["score"] is None
    assert result["score"] is None


def test_missing_expected_event_does_not_shrink_denominator(tmp_path: Path):
    events = [
        {"id": "present", "kind": "user_message", "source": "submitted_input", "text": "Keep MARKER.", "expected_native": True},
        {"id": "missing", "kind": "correction", "source": "submitted_input", "text": "This correction was omitted.", "expected_native": True},
    ]
    result = analyze_capture(make_capture(tmp_path, events=events))
    w1 = metric(result, "W1")
    assert (w1["numerator"], w1["denominator"], w1["score"], w1["state"]) == (1, 2, 50.0, "native_absent")


def test_incomplete_boundary_prevents_native_absence_claim(tmp_path: Path):
    events = [{"id": "missing", "kind": "correction", "source": "submitted_input", "text": "missing", "expected_native": True}]
    result = analyze_capture(make_capture(tmp_path, events=events, complete=False))
    assert metric(result, "W1")["state"] == "unresolved"
    assert metric(result, "W1")["score"] is None


def test_unrelated_nonzero_exit_cannot_satisfy_observed_failure(tmp_path: Path):
    events = [{"id": "failure-other", "kind": "failure", "source": "disk_ledger",
               "exit_code": 1, "text": "SB_OBS_UNRELATED_NONCE", "expected_native": True}]
    result = analyze_capture(make_capture(tmp_path, events=events))
    w3 = metric(result, "W3")
    assert (w3["numerator"], w3["denominator"], w3["score"], w3["state"]) == (
        0, 1, 0.0, "native_absent")


def test_erased_failure_identity_is_not_recovered_from_same_exit_code(tmp_path: Path):
    root = make_capture(tmp_path)
    native = root / "native" / "rollout.jsonl"
    native.write_bytes(native.read_bytes().replace(b"failure", b"erased!"))
    metadata = json.loads((root / "metadata.json").read_text())
    payload = native.read_bytes()
    metadata["native_files"][0]["bytes"] = len(payload)
    metadata["native_files"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    (root / "metadata.json").write_bytes(canonical(metadata))
    observer = json.loads((root / "observer.json").read_text())
    observer["artifact_manifest_sha256"] = hashlib.sha256(
        canonical(sorted(metadata["native_files"], key=lambda item: item["path"]))).hexdigest()
    (root / "observer.json").write_bytes(canonical(observer))
    w3 = metric(analyze_capture(root), "W3")
    assert (w3["numerator"], w3["denominator"], w3["score"], w3["state"]) == (
        0, 1, 0.0, "native_absent")


def test_report_rejects_duplicate_runs_and_aggregates_equal_run_weight(tmp_path: Path):
    first = make_capture(tmp_path, run_id="run-1")
    second = make_capture(tmp_path, run_id="run-2")
    single = build_report([first])["configurations"][0]
    assert any(item["label"] == "Artifact manifest SHA-256" for item in single["facts"])
    assert single["categories"][0]["checks"]
    report = build_report([first, second])
    assert report["edition"] == "v1 prototype"
    assert report["configurations"][0]["runs"] == 2
    assert report["configurations"][0]["score"] is None
    assert report["configurations"][0]["coverage"] == pytest.approx(
        (analyze_capture(first)["coverage"] + analyze_capture(second)["coverage"]) / 2
    )
    with pytest.raises(ValueError, match="duplicate run_id"):
        build_report([first, first])


def test_hash_mismatch_invalidates_capture_instead_of_scoring_artifact(tmp_path: Path):
    root = make_capture(tmp_path)
    native = root / "native" / "rollout.jsonl"
    native.write_bytes(native.read_bytes() + b"{}\n")
    result = analyze_capture(root)
    assert result["status"] == "invalid"
    assert metric(result, "A4")["score"] is None
    assert metric(result, "W1")["score"] is None
    assert result["coverage"] == 0
    assert result["score"] is None


def test_opencode_sqlite_reads_only_declared_message_and_part_rows(tmp_path: Path):
    root = tmp_path / "opencode-run"
    native = root / "native" / "bench.db"
    native.parent.mkdir(parents=True)
    connection = sqlite3.connect(native)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript("""
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT, version TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
    """)
    connection.execute("INSERT INTO session VALUES (?,?,?)", ("ses-1", "/tmp/work", "1.18.30"))
    connection.execute("INSERT INTO message VALUES (?,?,?,?)",
                       ("msg-1", "ses-1", 1, json.dumps({"role": "user"})))
    connection.execute("INSERT INTO part VALUES (?,?,?,?,?)",
                       ("part-1", "msg-1", "ses-1", 1,
                        json.dumps({"type": "text", "text": "SQLite marker"})))
    connection.commit()
    artifacts = [(native, "opencode-sqlite"),
                 (native.with_name("bench.db-wal"), "opencode-sqlite-companion"),
                 (native.with_name("bench.db-shm"), "opencode-sqlite-companion")]
    assert all(path.is_file() for path, _ in artifacts)
    files = [{"path": f"native/{path.name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "bytes": path.stat().st_size, "kind": kind} for path, kind in artifacts]
    metadata = {"schema_version": "session-bench-prototype-capture-v1", "id": "opencode-cli",
                "name": "OpenCode CLI", "surface": "OpenCode CLI", "version": "1.18.30",
                "model": "unobserved", "run_id": "opencode-run", "captured_at": "2026-09-10T00:01:00Z",
                "native_files": files, "required_native_files": [item["path"] for item in files],
                "artifact_set_complete": False, "status": "pilot"}
    (root / "metadata.json").write_bytes(canonical(metadata))
    observer = {"schema_version": "session-bench-prototype-observer-v1", "run_id": "opencode-run",
                "artifact_manifest_sha256": hashlib.sha256(canonical(sorted(files, key=lambda item: item["path"]))).hexdigest(),
                "independent": True, "observer_method": "submitted input and disk ledger",
                "events": [{"id": "input-1", "kind": "user_message", "source": "submitted_input",
                            "text": "SQLite marker", "expected_native": True}]}
    (root / "observer.json").write_bytes(canonical(observer))
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in artifacts}
    try:
        result = analyze_capture(root)
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path, _ in artifacts}
    finally:
        connection.close()
    assert after == before
    assert metric(result, "W1")["score"] == 100, result["limitations"]
    assert metric(result, "A2")["score"] == 100
    assert metric(result, "U2")["state"] == "unresolved"
    assert result["composition"] == [{"label": "Structural overhead",
                                      "bytes": sum(item["bytes"] for item in files)}]
