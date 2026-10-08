import pytest

from session_bench.claude_live import ClaudeLiveError, _normalize_command


def test_normalize_command_strips_shell_terminator_from_run_canary():
    canary = "SB_SURVIVAL_V1_RUN_claude-desktop-api-04"
    command = f'python3 bench_check.py final --run-canary {canary}; echo "exit=$?"'

    assert _normalize_command(command, canary) == ["python3", "bench_check.py", "final", "echo", "exit=$?"]


def test_normalize_command_still_rejects_wrong_canary_with_shell_terminator():
    canary = "SB_SURVIVAL_V1_RUN_claude-desktop-api-04"
    with pytest.raises(ClaudeLiveError, match="wrong run canary"):
        _normalize_command(
            "python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_wrong; echo done",
            canary,
        )


import hashlib
import json

from session_bench.claude_live import native_facts_from_claude_session

_BEFORE = "def checkout(items):\n    total = 0\n    return total + 5\n"
_AFTER = "def checkout(items):\n    total = 0\n    return total\n"


def _edit_session(*, old: str = "return total + 5", new: str = "return total", original: str = _BEFORE) -> bytes:
    rows = [
        {"type": "user", "sessionId": "session-1", "uuid": "u1", "message": {"role": "user", "content": "Correction R2: change it."}},
        {"type": "assistant", "sessionId": "session-1", "uuid": "a1", "message": {
            "id": "m1", "role": "assistant", "model": "vendor-model-5", "content": [
                {"type": "tool_use", "id": "toolu_edit", "name": "Edit",
                 "input": {"file_path": "/ws/fixture_project/checkout.py", "old_string": old, "new_string": new}}]}},
        {"type": "user", "sessionId": "session-1", "uuid": "u2",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_edit", "content": "updated"}]},
         "toolUseResult": {"filePath": "/ws/fixture_project/checkout.py", "oldString": old, "newString": new,
                           "originalFile": original, "replaceAll": False}},
    ]
    return "\n".join(json.dumps(row) for row in rows).encode("utf-8")


def _file_change(session: bytes) -> dict:
    facts = native_facts_from_claude_session(
        session, run_canary="SB_SURVIVAL_V1_RUN_x", workspace="/ws", before_sha256="", after_sha256="")
    return facts["file_changes"][0]


def test_edit_result_pre_image_yields_native_before_and_after_hashes():
    change = _file_change(_edit_session())

    assert change["before_sha256"] == hashlib.sha256(_BEFORE.encode()).hexdigest()
    assert change["after_sha256"] == hashlib.sha256(_AFTER.encode()).hexdigest()
    assert change["hash_source"] == "native_tool_result"


def test_edit_string_that_matches_twice_yields_no_native_hashes():
    change = _file_change(_edit_session(old="total", new="sum"))

    assert "hash_source" not in change
