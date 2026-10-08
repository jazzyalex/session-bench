import hashlib
import json
import shutil

import pytest

from session_bench.copilot_capture_qualification import (
    CopilotQualificationError,
    _validate_tool_path_scope,
    qualify_copilot_capture,
)
from session_bench.workload_instance import instantiate_workload


REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures/scenarios/survival-v1/workload/fixture_project"
TEMPLATE = REPO / "fixtures/scenarios/survival-v1/workload/workload.json"


def _jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_capture(root):
    (root / "capture").mkdir(parents=True)
    native_dir = root / "native/session-fixture"
    native_dir.mkdir(parents=True)
    workspace = root / "workspace/fixture_project"
    shutil.copytree(FIXTURE, workspace)
    (workspace / "checkout.py").write_text(
        "def checkout(items):\n"
        "    subtotal = sum(price * quantity for price, quantity in items)\n"
        "    return subtotal + (0 if subtotal >= 50 else 5)\n"
    )
    workload, _ = instantiate_workload(json.loads(TEMPLATE.read_text()), "copilot-qualification-fixture")
    shutil.copy2(TEMPLATE, root / "workload_template.json")
    (root / "workload_instance.json").write_text(json.dumps(workload, ensure_ascii=False))
    canary = workload["run_canary"]
    session = "copilot-session-fixture"
    rows = [
        {"type": "session.start", "id": "start", "data": {"sessionId": session, "copilotVersion": "1.0.89", "selectedModel": "auto"}},
        {"type": "user.message", "id": "user-r1", "data": {"content": workload["turns"][0]["text"]}},
        {"type": "assistant.message", "id": "assistant-call", "data": {"content": "", "messageId": "m-call", "toolRequests": [{"toolCallId": "call-1", "name": "bash", "arguments": {"command": "inspect"}}]}},
        {"type": "tool.execution_start", "id": "call-start", "parentId": "assistant-call", "data": {"toolCallId": "call-1", "toolName": "bash", "arguments": {"command": "inspect"}}},
        {"type": "tool.execution_complete", "id": "call-complete", "parentId": "call-start", "data": {"toolCallId": "call-1", "success": True, "result": {"content": "inspected"}}},
        {"type": "assistant.message", "id": "assistant-r1", "data": {"content": "baseline observed. " + workload["turns"][0]["response_canary"], "messageId": "m-r1"}},
        {"type": "session.resume", "id": "resume", "data": {"selectedModel": "auto"}},
        {"type": "user.message", "id": "user-r2", "data": {"content": workload["turns"][1]["text"]}},
    ]
    (native_dir / "events.jsonl").write_bytes(_jsonl(rows))
    for number, turn in enumerate(workload["turns"], 1):
        stream = [
            {"type": "assistant.message", "data": {"content": "response. " + turn["response_canary"]}},
            {"type": "result", "exitCode": 0},
        ]
        (root / f"capture/r{number}.stdout").write_bytes(_jsonl(stream))
        (root / f"capture/r{number}.stderr").write_bytes(b"")

    initial_files = {
        path.relative_to(FIXTURE).as_posix(): {"sha256": _sha(path)}
        for path in FIXTURE.rglob("*") if path.is_file()
    }
    initial_files[".survival-observer.jsonl"] = {"existed_before_first_turn": False}
    (root / "filesystem").mkdir()
    (root / "filesystem/initial-state.json").write_text(json.dumps({"files": initial_files}))
    helper_rows = []
    nonces = {"inspect": "inspect-fixture-0001", "baseline": "baseline-fixture-0001", "final": "final-fixture-0001"}
    codes = {"inspect": 0, "baseline": 1, "final": 0}
    bodies = {
        "inspect": {"checkout_sha256": _sha(FIXTURE / "checkout.py")},
        "baseline": {"tests": [
            {"actual": 20, "expected": 30, "passed": False},
            {"actual": 30, "expected": 50, "passed": False},
            {"actual": 54, "expected": 54, "passed": True},
        ]},
        "final": {"tests": [{"passed": True, "actual": i, "expected": i} for i in (30, 50, 54)]},
    }
    for phase in ("inspect", "baseline", "final"):
        nonce = nonces[phase]
        helper_rows.append({"phase": phase, "helper_nonce": nonce, "run_canary": canary,
                            "exit_code": codes[phase],
                            "output": f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} " + json.dumps(bodies[phase])})
    observer = workspace / ".survival-observer.jsonl"
    observer.write_bytes(_jsonl(helper_rows))
    after_files = {
        path.relative_to(workspace).as_posix(): _sha(path)
        for path in workspace.rglob("*") if path.is_file()
    }
    (root / "filesystem/after-state.json").write_text(json.dumps({"files": after_files}))
    (root / "attempt.json").write_text(json.dumps({"status": "completed", "exit_code": 0,
                                                   "session_id": session, "cli_version": "1.0.89"}))


def test_qualify_copilot_capture_joins_native_stdout_helper_and_workspace(tmp_path):
    root = tmp_path / "capture"
    root.mkdir()
    _write_capture(root)
    receipt = qualify_copilot_capture(root)
    assert receipt["native_format"] == "copilot-session-events-jsonl-v1"
    assert receipt["submitted_turns"] == 2
    assert receipt["native_tool_calls"] == 1
    assert receipt["native_tool_results"] == 1
    assert receipt["helper_phases"]["final"]["exit_code"] == 0
    assert receipt["workspace_changed_paths"] == ["checkout.py"]
    assert receipt["independent_reproduction"] is False
    assert receipt["score_eligible"] is False


def test_qualification_rejects_missing_helper_phase(tmp_path):
    root = tmp_path / "capture"
    root.mkdir()
    _write_capture(root)
    observer = root / "workspace/fixture_project/.survival-observer.jsonl"
    observer.write_bytes(b"")
    with pytest.raises(CopilotQualificationError, match="helper observer"):
        qualify_copilot_capture(root)


def test_qualification_rejects_workspace_mutation_outside_declared_file(tmp_path):
    root = tmp_path / "capture"
    root.mkdir()
    _write_capture(root)
    workspace = root / "workspace/fixture_project"
    (workspace / "unexpected.txt").write_text("unexpected")
    with pytest.raises(CopilotQualificationError, match="after-state manifest"):
        qualify_copilot_capture(root)


def test_tool_path_scope_allows_only_dev_null_shell_redirection(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    rows = [{
        "type": "tool.execution_start",
        "data": {"arguments": {"command": "git diff -- checkout.py 2>/dev/null || true"}},
    }]
    _validate_tool_path_scope(rows, workspace)

    rows[0]["data"]["arguments"]["command"] = "cat /dev/null"
    with pytest.raises(CopilotQualificationError, match="outside the isolated fixture workspace"):
        _validate_tool_path_scope(rows, workspace)


def test_tool_path_scope_reads_a_relative_glob_as_a_pattern_not_a_root_path(tmp_path):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    rows = [{"type": "tool.execution_start", "data": {"arguments": {"pattern": "**/*", "paths": [str(workspace)]}}}]

    _validate_tool_path_scope(rows, workspace)

    rows[0]["data"]["arguments"]["pattern"] = "/etc/*"
    with pytest.raises(CopilotQualificationError, match="outside the isolated fixture workspace"):
        _validate_tool_path_scope(rows, workspace)
