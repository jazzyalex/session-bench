import hashlib
import json

import pytest

from session_bench.adapters.claude_code_decoder import ClaudeCodeDecoderError, decode_claude_code_bundle


def _package(tmp_path):
    package = tmp_path / "package"
    package.mkdir(parents=True)
    rows = [
        {"type": "user", "uuid": "u1", "sessionId": "s1", "timestamp": "2026-09-14T00:00:00Z", "message": {"role": "user", "content": "R1"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1", "timestamp": "2026-09-14T00:00:01Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "R1 response"}]}},
    ]
    session = package / "session.jsonl"
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = {"format": "claude-code-jsonl-v1", "artifacts": [{"id": "session", "path": "session.jsonl", "sha256": hashlib.sha256(session.read_bytes()).hexdigest(), "size_bytes": session.stat().st_size, "depends_on": []}]}
    (package / "decode.json").write_text(json.dumps(manifest))
    return package


def test_decodes_closed_single_session_bundle(tmp_path):
    decoded = decode_claude_code_bundle(_package(tmp_path))
    assert decoded["session_id"] == "s1"
    assert decoded["counts"] == {"submitted_turns": 1, "responses": 1, "actions": 0, "results": 0}
    assert [event["text"] for event in decoded["events"]] == ["R1", "R1 response"]


def test_classifies_tool_blocks_without_turn_inflation(tmp_path):
    package = _package(tmp_path / "second")
    session = package / "session.jsonl"
    rows = [json.loads(line) for line in session.read_text().splitlines()]
    rows.extend([
        {"type": "assistant", "uuid": "a2", "sessionId": "s1", "timestamp": "2026-09-14T00:00:02Z", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "name": "Bash"}, {"type": "tool_use", "id": "tool-2", "name": "Edit"}]}},
        {"type": "user", "uuid": "u2", "sessionId": "s1", "timestamp": "2026-09-14T00:00:03Z", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}, {"type": "tool_result", "tool_use_id": "tool-2", "content": "done"}]}},
    ])
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = json.loads((package / "decode.json").read_text())
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(session.read_bytes()).hexdigest()
    manifest["artifacts"][0]["size_bytes"] = session.stat().st_size
    (package / "decode.json").write_text(json.dumps(manifest))
    decoded = decode_claude_code_bundle(package)
    assert decoded["counts"] == {"submitted_turns": 1, "responses": 1, "actions": 2, "results": 2}
    assert [event["tool_use_id"] for event in decoded["events"] if event["kind"] in {"action", "result"}] == ["tool-1", "tool-2", "tool-1", "tool-2"]


def _with_shell_call(package, command, output):
    session = package / "session.jsonl"
    rows = [json.loads(line) for line in session.read_text().splitlines()]
    rows.extend([
        {"type": "assistant", "uuid": "a2", "sessionId": "s1", "timestamp": "2026-09-14T00:00:02Z",
         "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "tool-shell", "name": "Bash", "input": {"command": command}}]}},
        {"type": "user", "uuid": "u2", "sessionId": "s1", "timestamp": "2026-09-14T00:00:03Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-shell", "content": output}]}},
    ])
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = json.loads((package / "decode.json").read_text())
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(session.read_bytes()).hexdigest()
    manifest["artifacts"][0]["size_bytes"] = session.stat().st_size
    (package / "decode.json").write_text(json.dumps(manifest))
    return session.read_text()


def test_compound_shell_write_and_final_helper_split_into_native_segments_only(tmp_path):
    package = _package(tmp_path / "compound")
    helper = "python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_compound"
    native = _with_shell_call(package, f"cat > checkout.py <<'EOF'\nchanged\nEOF\n{helper}", "SB_SURVIVAL_V1_HELPER_FINAL_compound {}")

    decoded = decode_claude_code_bundle(package)
    assert decoded["counts"] == {"submitted_turns": 1, "responses": 1, "actions": 2, "results": 1}
    actions = [event for event in decoded["events"] if event["kind"] == "action"]
    results = [event for event in decoded["events"] if event["kind"] == "result"]
    assert [event["tool_use_id"] for event in actions] == ["tool-shell:segment-0", "tool-shell:segment-1"]
    assert [event["id"] for event in actions] == ["a2-block-0-segment-0", "a2-block-0-segment-1"]
    assert [event["text"] for event in actions] == ["cat > checkout.py <<'EOF'", helper]
    assert (actions[0]["tool_name"], actions[0]["action_kind"], actions[0]["target"]) == ("Bash", "edit", "checkout.py")
    # The write has no result of its own; the helper result is its own native output line.
    assert [(event["tool_use_id"], event["text"], event["exit_code"]) for event in results] == [
        ("tool-shell:segment-1", "SB_SURVIVAL_V1_HELPER_FINAL_compound {}", 0)]
    assert all(event["parent_tool_use_id"] == "tool-shell" and event["locator"]["line"] in (3, 4) for event in actions + results)
    for event in decoded["events"]:
        assert "compound edit" not in json.dumps(event) and json.dumps(event["text"])[1:-1] in native


def test_shell_call_with_one_scored_segment_or_an_unrecognized_edit_stays_one_call(tmp_path):
    # A script that edits the file is not a redirection write: the call holds one scored segment.
    command = "python3 - <<'PY'\nfrom pathlib import Path\nPath('fixture_project/checkout.py').write_text('changed')\nPY\npython3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_compound"
    package = _package(tmp_path / "single")
    _with_shell_call(package, command, "SB_SURVIVAL_V1_HELPER_FINAL_compound exit code: 0")

    decoded = decode_claude_code_bundle(package)
    assert decoded["counts"] == {"submitted_turns": 1, "responses": 1, "actions": 1, "results": 1}
    action, result = (event for event in decoded["events"] if event["kind"] in {"action", "result"})
    assert (action["id"], action["tool_use_id"], action["tool_name"], action["input"]) == ("a2-block-0", "tool-shell", "Bash", {"command": command})
    assert (result["tool_use_id"], result["text"]) == ("tool-shell", "SB_SURVIVAL_V1_HELPER_FINAL_compound exit code: 0")
    assert "parent_tool_use_id" not in action and "parent_tool_use_id" not in result


def test_rejects_undeclared_companion(tmp_path):
    package = _package(tmp_path)
    (package / "companion.sqlite").write_text("not declared")
    with pytest.raises(ClaudeCodeDecoderError, match="undeclared"):
        decode_claude_code_bundle(package)


def test_rejects_damaged_session_bytes(tmp_path):
    package = _package(tmp_path)
    (package / "session.jsonl").write_text("{}\n")
    with pytest.raises(ClaudeCodeDecoderError, match="digest"):
        decode_claude_code_bundle(package)


def test_rejects_manifest_side_channels_and_second_session_ids(tmp_path):
    package = _package(tmp_path)
    manifest = json.loads((package / "decode.json").read_text())
    manifest["observer_truth"] = "forbidden"
    (package / "decode.json").write_text(json.dumps(manifest))
    with pytest.raises(ClaudeCodeDecoderError, match="unsupported"):
        decode_claude_code_bundle(package)

    package = _package(tmp_path / "second")
    session = package / "session.jsonl"
    rows = [json.loads(line) for line in session.read_text().splitlines()]
    rows.append({"type": "last-prompt", "sessionId": "s2"})
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    manifest = json.loads((package / "decode.json").read_text())
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(session.read_bytes()).hexdigest()
    manifest["artifacts"][0]["size_bytes"] = session.stat().st_size
    (package / "decode.json").write_text(json.dumps(manifest))
    with pytest.raises(ClaudeCodeDecoderError, match="exactly one session"):
        decode_claude_code_bundle(package)
