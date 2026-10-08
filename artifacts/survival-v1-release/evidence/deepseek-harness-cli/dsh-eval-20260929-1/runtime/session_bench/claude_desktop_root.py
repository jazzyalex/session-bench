"""Validate the session-specific Claude Desktop Code cross-root family."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ClaudeDesktopRootError(ValueError):
    """The copied Desktop family is incomplete, ambiguous, or inconsistent."""


REQUIRED_FILES = {
    "transcript": "transcript/session.jsonl",
    "desktop_metadata": "desktop/session.json",
}

# These files are useful provenance when a capture operator has preserved them,
# but Claude Desktop's persistent record boundary does not depend on either
# ephemeral process state or the session-start hook.  Validate them when they
# are present; do not make a missing ephemeral snapshot invalidate a persistent
# transcript/metadata pair.
OPTIONAL_FILES = {
    "process_registry": "runtime/process.json",
    "session_hook": "runtime/sessionstart-hook.sh",
}

# Backward-compatible complete inventory for callers that need the known file
# names.  The validator below deliberately uses REQUIRED_FILES and
# OPTIONAL_FILES separately.
FILES = {**REQUIRED_FILES, **OPTIONAL_FILES}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeDesktopRootError(f"invalid JSON companion: {path.name}") from exc
    if not isinstance(value, dict):
        raise ClaudeDesktopRootError(f"JSON companion is not an object: {path.name}")
    return value


def validate_claude_desktop_family(package: Path) -> dict[str, Any]:
    package = Path(package)
    if package.is_symlink() or not package.is_dir():
        raise ClaudeDesktopRootError("family must be an ordinary copied directory")
    required = set(REQUIRED_FILES.values())
    optional = set(OPTIONAL_FILES.values())
    expected = required | optional
    actual: set[str] = set()
    for candidate in package.rglob("*"):
        if candidate.is_symlink():
            raise ClaudeDesktopRootError("family contains a symlink")
        if candidate.is_file():
            actual.add(candidate.relative_to(package).as_posix())
    missing = required - actual
    extra = actual - expected
    if missing or extra:
        raise ClaudeDesktopRootError(f"family closure mismatch: missing={sorted(missing)}, extra={sorted(extra)}")

    transcript = package / FILES["transcript"]
    session_ids: set[str] = set()
    for number, line in enumerate(transcript.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClaudeDesktopRootError(f"transcript line {number} is invalid JSON") from exc
        if isinstance(row, dict) and isinstance(row.get("sessionId"), str):
            session_ids.add(row["sessionId"])
    if len(session_ids) != 1:
        raise ClaudeDesktopRootError("transcript must contain exactly one CLI session ID")
    cli_session_id = next(iter(session_ids))
    desktop = _json(package / FILES["desktop_metadata"])
    process_path = package / OPTIONAL_FILES["process_registry"]
    process = _json(process_path) if process_path.exists() else None
    if desktop.get("cliSessionId") != cli_session_id:
        raise ClaudeDesktopRootError("CLI session join is inconsistent")
    bridge_ids = desktop.get("bridgeSessionIds")
    if not isinstance(bridge_ids, list) or len(bridge_ids) != 1 or not isinstance(bridge_ids[0], str) or not bridge_ids[0]:
        raise ClaudeDesktopRootError("bridge session join is inconsistent")
    if not isinstance(desktop.get("cwd"), str):
        raise ClaudeDesktopRootError("workspace identity is inconsistent")
    if process is not None:
        if process.get("sessionId") != cli_session_id:
            raise ClaudeDesktopRootError("process registry CLI session join is inconsistent")
        if process.get("bridgeSessionId") != bridge_ids[0]:
            raise ClaudeDesktopRootError("process registry bridge session join is inconsistent")
        if process.get("entrypoint") != "claude-desktop":
            raise ClaudeDesktopRootError("process registry is not a Claude Desktop session")
        if process.get("cwd") != desktop.get("cwd"):
            raise ClaudeDesktopRootError("workspace identity is inconsistent")
    hook_path = package / OPTIONAL_FILES["session_hook"]
    if hook_path.exists() and (not hook_path.is_file() or hook_path.stat().st_size == 0):
        raise ClaudeDesktopRootError("session hook companion is empty or not a regular file")
    desktop_session_id = desktop.get("sessionId")
    if not isinstance(desktop_session_id, str) or not desktop_session_id.startswith("local_"):
        raise ClaudeDesktopRootError("Desktop session ID is missing")
    artifacts = []
    for role, relative in FILES.items():
        if not (package / relative).exists():
            continue
        data = (package / relative).read_bytes()
        artifacts.append({"role": role, "path": relative, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    metadata_snapshot = desktop.get("toolSurfaceSnapshot")
    cli_version = process.get("version") if isinstance(process, dict) else None
    if not isinstance(cli_version, str) or not cli_version:
        cli_version = metadata_snapshot.get("cliVersion") if isinstance(metadata_snapshot, dict) else None
    return {
        "schema_version": "session-bench-claude-desktop-family-v1",
        "desktop_session_id": desktop_session_id,
        "cli_session_id": cli_session_id,
        "bridge_session_id": bridge_ids[0],
        "bridge_session_ids": list(bridge_ids),
        "model": desktop.get("model"),
        "effort": desktop.get("effort"),
        "cli_version": cli_version,
        "entrypoint": process.get("entrypoint") if isinstance(process, dict) else "claude-desktop",
        "process_registry_present": process is not None,
        "session_hook_present": hook_path.exists(),
        "complete_persistent_family": True,
        "artifacts": artifacts,
    }


__all__ = [
    "ClaudeDesktopRootError",
    "FILES",
    "OPTIONAL_FILES",
    "REQUIRED_FILES",
    "validate_claude_desktop_family",
]
