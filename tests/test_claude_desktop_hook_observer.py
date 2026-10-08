import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

from session_bench.claude_desktop_hook_observer import observe_hook


NOW = datetime(2026, 10, 1, 12, 34, 56, 123456, tzinfo=timezone.utc)


@pytest.fixture
def capture(tmp_path):
    workspace = tmp_path / "workspace"
    fixture = workspace / "fixture_project"
    fixture.mkdir(parents=True)
    return dict(run_id="run-1", session_id="session-1", workspace=workspace,
                fixture_root=fixture, receipts_path=tmp_path / "receipts.jsonl", clock=lambda: NOW)


def event(capture, kind, call="call-1", **changes):
    value = dict(hook_event_name=kind, session_id="session-1", cwd=str(capture["workspace"]),
                 tool_use_id=call, tool_name="Read",
                 tool_input={"file_path": "fixture_project/checkout.py"})
    value.update(changes)
    return json.dumps(value).encode()


def record(capture, kind, call="call-1", **changes):
    return observe_hook(event(capture, kind, call, **changes), **capture)


def test_success_receipts_join_and_timestamp_provenance(capture):
    start = record(capture, "PreToolUse", timestamp="2000-01-01T00:00:00Z")
    finish = record(capture, "PostToolUse", tool_response={"ok": True, "secret": "not retained"}, duration_ms=12.5)
    rows = [json.loads(line) for line in capture["receipts_path"].read_text().splitlines()]
    assert rows == [start, finish]
    assert [row["tool_use_id"] for row in rows] == ["call-1", "call-1"]
    assert [row["result_status"] for row in rows] == ["pending", "success"]
    assert all(row["fixture_relative_target"] == "checkout.py" for row in rows)
    assert all(row["hook_observed_at"] == "2026-10-01T12:34:56.123456Z" for row in rows)
    assert all(row["timestamp_provenance"] == "local_hook_receipt_clock" for row in rows)
    assert finish["duration_ms"] == 12.5
    assert finish["result_sha256"] == hashlib.sha256(b'{"ok":true,"secret":"not retained"}').hexdigest()
    assert "secret" not in capture["receipts_path"].read_text()
    assert os.stat(capture["receipts_path"]).st_mode & 0o777 == 0o600


def test_failure_receipt_and_cross_call_interleaving(capture):
    record(capture, "PreToolUse", "a", tool_name="Bash", tool_input={"command": "python bench_check.py inspect"})
    record(capture, "PreToolUse", "b")
    record(capture, "PostToolUse", "b", tool_response="done")
    failed = record(capture, "PostToolUseFailure", "a", tool_name="Bash",
                    tool_input={"command": "python bench_check.py inspect"}, error={"message": "failed"}, duration_ms=3)
    assert failed["result_status"] == "failure"
    assert failed["duration_ms"] == 3
    assert failed["command_sha256"] == hashlib.sha256(b"python bench_check.py inspect").hexdigest()
    assert "fixture_relative_target" not in failed
    assert [json.loads(line)["tool_use_id"] for line in capture["receipts_path"].read_text().splitlines()] == ["a", "b", "b", "a"]


@pytest.mark.parametrize("sequence", [
    ["PostToolUse"],
    ["PostToolUseFailure"],
    ["PreToolUse", "PreToolUse"],
    ["PreToolUse", "PostToolUse", "PostToolUse"],
    ["PreToolUse", "PostToolUseFailure", "PostToolUse"],
])
def test_duplicate_and_out_of_order_rejected_without_extra_receipt(capture, sequence):
    for kind in sequence[:-1]:
        result = {"tool_response": "ok"} if kind == "PostToolUse" else {"error": "failed"} if kind == "PostToolUseFailure" else {}
        record(capture, kind, **result)
    before = capture["receipts_path"].read_bytes() if capture["receipts_path"].exists() else b""
    last = sequence[-1]
    result = {"tool_response": "ok"} if last == "PostToolUse" else {"error": "failed"} if last == "PostToolUseFailure" else {}
    with pytest.raises(ValueError):
        record(capture, last, **result)
    assert capture["receipts_path"].read_bytes() == before


@pytest.mark.parametrize("change", [
    {"session_id": "other"}, {"cwd": "/other"}, {"run_id": "other"},
    {"tool_use_id": ""}, {"hook_event_name": "Other"},
])
def test_wrong_identity_or_malformed_event_rejected(capture, change):
    with pytest.raises(ValueError):
        record(capture, "PreToolUse", **change)
    assert not capture["receipts_path"].exists()


def test_malformed_json_duplicate_keys_and_bad_duration_rejected(capture):
    for raw in (b"{", b'{"hook_event_name":"PreToolUse","hook_event_name":"PostToolUse"}'):
        with pytest.raises(ValueError):
            observe_hook(raw, **capture)
    record(capture, "PreToolUse")
    with pytest.raises(ValueError, match="duration"):
        record(capture, "PostToolUse", tool_response="ok", duration_ms=-1)


def test_completion_identity_and_target_must_match_start(capture):
    record(capture, "PreToolUse")
    for changes in ({"tool_name": "Edit", "tool_response": "ok"},
                    {"tool_input": {"file_path": "fixture_project/other.py"}, "tool_response": "ok"}):
        with pytest.raises(ValueError):
            record(capture, "PostToolUse", **changes)
    assert len(capture["receipts_path"].read_text().splitlines()) == 1


def test_path_confinement_including_symlink(capture, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("x")
    (capture["fixture_root"] / "escape.py").symlink_to(outside)
    for path in ("../outside.py", "fixture_project/../../outside.py", str(outside),
                 "fixture_project/escape.py"):
        with pytest.raises(ValueError, match="escapes fixture root"):
            record(capture, "PreToolUse", tool_input={"file_path": path})
    assert not capture["receipts_path"].exists()


def test_private_append_only_file_rejects_broad_permissions_and_symlink(capture, tmp_path):
    path = capture["receipts_path"]
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        record(capture, "PreToolUse")
    assert path.read_bytes() == b""
    path.unlink()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    path.symlink_to(outside)
    with pytest.raises(OSError):
        record(capture, "PreToolUse")
    assert outside.read_bytes() == b""


def test_capture_time_projection_keeps_semantics_and_clock_without_raw_command(capture, tmp_path):
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    projection_path = tmp_path / "projection.jsonl"
    command = (
        f"apply_patch checkout.py; python3 bench_check.py final --run-canary {canary}"
    )
    context = {**capture, "projection_receipts_path": projection_path, "run_canary": canary}
    start = record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    finish = observe_hook(
        event(capture, "PostToolUse", tool_name="Bash", tool_input={"command": command},
              tool_response={"exit_code": 0}),
        **context,
    )
    rows = [json.loads(line) for line in projection_path.read_text().splitlines()]
    assert [row["event_type"] for row in rows] == ["PreToolUse", "PostToolUse"]
    assert rows[0]["projection_state"] == rows[1]["projection_state"] == "supported"
    assert rows[0]["command_sha256"] == start["command_sha256"] == finish["command_sha256"]
    assert rows[0]["helper_phases"] == [{
        "phase": "final", "argv": ["python3", "bench_check.py", "final"],
        "run_canary": canary,
    }]
    assert rows[0]["compound_edit"] is True
    assert rows[0]["compound_edit_target"] == "fixture_project/checkout.py"
    assert rows[1]["result_sha256"] == finish["result_sha256"]
    assert "apply_patch" not in projection_path.read_text()
    assert command not in projection_path.read_text()
    assert os.stat(projection_path).st_mode & 0o777 == 0o600


def test_unproven_command_is_recorded_unsupported_without_blocking_tool(capture, tmp_path):
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    projection_path = tmp_path / "projection.jsonl"
    context = {**capture, "projection_receipts_path": projection_path, "run_canary": canary}
    record(context, "PreToolUse", tool_name="Bash",
           tool_input={"command": "python3 bench_check.py inspect --run-canary wrong"})
    observe_hook(event(capture, "PostToolUse", tool_name="Bash",
                       tool_input={"command": "python3 bench_check.py inspect --run-canary wrong"},
                       tool_response="completed"), **context)
    rows = [json.loads(line) for line in projection_path.read_text().splitlines()]
    assert [row["projection_state"] for row in rows] == ["unsupported", "unsupported"]
    assert rows[0]["unsupported_reason"] == "helper_run_canary_unproven"
    assert "wrong" not in projection_path.read_text()


@pytest.mark.parametrize("command", [
    "# python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_run-1",
    "echo 'python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_run-1'",
])
def test_comments_and_quoted_output_do_not_create_helper_or_edit_evidence(capture, tmp_path, command):
    path = tmp_path / "projection.jsonl"
    context = {**capture, "projection_receipts_path": path,
               "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}
    start = record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert projection["action_kind"] == "shell"
    assert projection["helper_phases"] == []
    assert projection["compound_edit"] is False
    assert projection["compound_edit_target"] is None
    assert start["command_projection_schema"] == projection["schema"]
    assert command not in path.read_text()


def test_quoted_shell_separator_in_echo_is_not_execution_evidence(capture, tmp_path):
    path = tmp_path / "projection.jsonl"
    command = "echo 'apply_patch checkout.py; python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_run-1'"
    context = {**capture, "projection_receipts_path": path,
               "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}
    record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert projection["action_kind"] == "shell"
    assert projection["helper_phases"] == []
    assert projection["compound_edit"] is False
    assert command not in path.read_text()


def test_simple_status_expansion_in_echo_preserves_helper_projection(capture, tmp_path):
    path = tmp_path / "projection.jsonl"
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    command = f"python3 bench_check.py inspect --run-canary {canary}; echo status:$?"
    context = {**capture, "projection_receipts_path": path, "run_canary": canary}
    record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert [row["phase"] for row in projection["helper_phases"]] == ["inspect"]
    assert projection["compound_edit"] is False
    assert command not in path.read_text()


def test_two_safe_helper_phases_with_status_echoes_are_projected(capture, tmp_path):
    path = tmp_path / "projection.jsonl"
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    command = (
        f"python3 bench_check.py inspect --run-canary {canary}; echo inspect:$?; "
        f"python3 bench_check.py baseline --run-canary {canary}; echo baseline:$?"
    )
    context = {**capture, "projection_receipts_path": path, "run_canary": canary}
    record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert [row["phase"] for row in projection["helper_phases"]] == ["inspect", "baseline"]
    assert projection["action_kind"] == "inspect"
    assert projection["compound_edit"] is False
    assert command not in path.read_text()


def test_command_substitution_in_echo_stays_unsupported(capture, tmp_path):
    path = tmp_path / "projection.jsonl"
    command = "echo $(python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_run-1)"
    context = {**capture, "projection_receipts_path": path,
               "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}
    record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "unsupported"
    assert projection["unsupported_reason"] == "ambiguous_shell_input"
    assert "helper_phases" not in projection
    assert command not in path.read_text()


def test_quoted_edit_phrase_does_not_make_direct_final_compound(capture, tmp_path):
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    path = tmp_path / "projection.jsonl"
    command = f"echo 'apply_patch checkout.py'; python3 bench_check.py final --run-canary {canary}"
    context = {**capture, "projection_receipts_path": path, "run_canary": canary}
    record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert [row["phase"] for row in projection["helper_phases"]] == ["final"]
    assert projection["compound_edit"] is False


@pytest.mark.parametrize("command", [
    "python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_run-1",
    "apply_patch checkout.py; python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_run-1",
])
def test_direct_helper_and_compound_edit_remain_proven(capture, tmp_path, command):
    path = tmp_path / "projection.jsonl"
    context = {**capture, "projection_receipts_path": path,
               "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}
    start = record(context, "PreToolUse", tool_name="Bash", tool_input={"command": command})
    projection = json.loads(path.read_text().splitlines()[0])
    assert projection["projection_state"] == "supported"
    assert len(projection["helper_phases"]) == 1
    assert projection["compound_edit"] is command.startswith("apply_patch")
    semantics = {key: value for key, value in projection.items() if key not in {
        "schema", "run_id", "session_id", "workspace", "call_id", "event_type",
        "tool_name", "observed_at", "timestamp_provenance",
    }}
    expected = hashlib.sha256(json.dumps(semantics, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert start["command_projection_sha256"] == expected
