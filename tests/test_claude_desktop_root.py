import json

import pytest

from session_bench.claude_desktop_root import (
    ClaudeDesktopRootError,
    FILES,
    REQUIRED_FILES,
    validate_claude_desktop_family,
)


def _family(tmp_path):
    root = tmp_path / "family"
    for relative in FILES.values():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    session = "cli-session"
    (root / FILES["transcript"]).write_text(json.dumps({"sessionId": session}) + "\n")
    (root / FILES["desktop_metadata"]).write_text(json.dumps({
        "sessionId": "local_desktop", "cliSessionId": session,
        "bridgeSessionIds": ["bridge"], "cwd": "/synthetic", "model": "model", "effort": "medium",
    }))
    (root / FILES["process_registry"]).write_text(json.dumps({
        "sessionId": session, "bridgeSessionId": "bridge", "cwd": "/synthetic",
        "entrypoint": "claude-desktop", "version": "1",
    }))
    (root / FILES["session_hook"]).write_text("# synthetic hook\n")
    return root


def _persistent_family(tmp_path):
    root = tmp_path / "persistent-family"
    for relative in REQUIRED_FILES.values():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
    session = "cli-session"
    (root / REQUIRED_FILES["transcript"]).write_text(json.dumps({"sessionId": session}) + "\n")
    (root / REQUIRED_FILES["desktop_metadata"]).write_text(json.dumps({
        "sessionId": "local_desktop", "cliSessionId": session,
        "bridgeSessionIds": ["bridge"], "cwd": "/synthetic", "model": "model", "effort": "medium",
    }))
    return root


def test_validates_cross_root_join_and_hashes_every_member(tmp_path):
    result = validate_claude_desktop_family(_family(tmp_path))
    assert result["desktop_session_id"] == "local_desktop"
    assert result["cli_session_id"] == "cli-session"
    assert result["bridge_session_id"] == "bridge"
    assert {row["role"] for row in result["artifacts"]} == set(FILES)
    assert all(len(row["sha256"]) == 64 for row in result["artifacts"])


def test_persistent_pair_is_complete_without_ephemeral_companions(tmp_path):
    result = validate_claude_desktop_family(_persistent_family(tmp_path))
    assert result["complete_persistent_family"] is True
    assert result["process_registry_present"] is False
    assert result["session_hook_present"] is False
    assert {row["role"] for row in result["artifacts"]} == set(REQUIRED_FILES)
    assert result["entrypoint"] == "claude-desktop"


def test_rejects_missing_persistent_companion_and_broken_optional_join(tmp_path):
    root = _family(tmp_path)
    (root / FILES["desktop_metadata"]).unlink()
    with pytest.raises(ClaudeDesktopRootError, match="closure"):
        validate_claude_desktop_family(root)

    root = _family(tmp_path / "second")
    process = json.loads((root / FILES["process_registry"]).read_text())
    process["bridgeSessionId"] = "other"
    (root / FILES["process_registry"]).write_text(json.dumps(process))
    with pytest.raises(ClaudeDesktopRootError, match="bridge"):
        validate_claude_desktop_family(root)


def test_rejects_undeclared_file_and_symlink(tmp_path):
    root = _family(tmp_path)
    (root / "extra").write_text("no")
    with pytest.raises(ClaudeDesktopRootError, match="extra"):
        validate_claude_desktop_family(root)

    root = _family(tmp_path / "second")
    target = root / FILES["session_hook"]
    target.unlink()
    target.symlink_to(root / FILES["transcript"])
    with pytest.raises(ClaudeDesktopRootError, match="symlink"):
        validate_claude_desktop_family(root)
