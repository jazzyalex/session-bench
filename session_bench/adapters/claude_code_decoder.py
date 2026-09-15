"""Fail-closed decoder for a copied Claude Code project-keyed JSONL session.

The decoder accepts one explicitly declared copied package.  It never searches
``~/.claude`` or any Desktop profile and cannot silently attach companions.
This keeps a fresh synthetic CLI session useful for format qualification while
leaving normal-account history outside the evidence boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


FORMAT = "claude-code-jsonl-v1"
MANIFEST_NAME = "decode.json"
SESSION_NAME = "session.jsonl"


class ClaudeCodeDecoderError(ValueError):
    """The supplied copied package is not a closed Claude Code JSONL bundle."""


def _strict_json(text: str, source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ClaudeCodeDecoderError(f"{source}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except ClaudeCodeDecoderError:
        raise
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ClaudeCodeDecoderError(f"{source}: invalid JSON") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_package(package: Path) -> bytes:
    package = Path(package)
    if package.is_symlink() or not package.is_dir():
        raise ClaudeCodeDecoderError("copied package must be an ordinary directory")
    package = package.resolve()
    manifest_path = package / MANIFEST_NAME
    session_path = package / SESSION_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ClaudeCodeDecoderError("copied package is missing decode.json")
    if not session_path.is_file() or session_path.is_symlink():
        raise ClaudeCodeDecoderError("copied package is missing session.jsonl")
    manifest = _strict_json(manifest_path.read_text(encoding="utf-8"), MANIFEST_NAME)
    if not isinstance(manifest, dict) or set(manifest) != {"format", "artifacts"} or manifest.get("format") != FORMAT:
        raise ClaudeCodeDecoderError("unsupported Claude Code package format")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], dict):
        raise ClaudeCodeDecoderError("package must declare exactly one session artifact")
    artifact = artifacts[0]
    if set(artifact) != {"id", "path", "sha256", "size_bytes", "depends_on"}:
        raise ClaudeCodeDecoderError("session artifact declaration has unexpected fields")
    if artifact["id"] != "session" or artifact["path"] != SESSION_NAME or artifact["depends_on"] != []:
        raise ClaudeCodeDecoderError("session artifact declaration is invalid")
    session_bytes = session_path.read_bytes()
    if artifact["size_bytes"] != len(session_bytes) or artifact["sha256"] != hashlib.sha256(session_bytes).hexdigest():
        raise ClaudeCodeDecoderError("session artifact digest mismatch")
    declared = {MANIFEST_NAME, SESSION_NAME}
    for candidate in package.rglob("*"):
        if candidate.is_symlink():
            raise ClaudeCodeDecoderError("symlinks are not allowed in copied packages")
        if candidate.is_file() and candidate.relative_to(package).as_posix() not in declared:
            raise ClaudeCodeDecoderError("copied package contains undeclared files")
    return session_bytes


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))


_HELPER_COMMAND_RE = re.compile(
    r"(?:^|[\s;&|])(?:python(?:3)?\s+)?(?:[./\w-]+/)?bench_check\.py"
    r"\s+(inspect|baseline|final)(?=\s|$|[;&|])",
    re.IGNORECASE,
)
_COMPOUND_EDIT_MARKERS = (
    "apply_patch",
    "cat >",
    "cat>>",
    "tee ",
    "sed -i",
    "perl -i",
    "python -c",
    "python3 -c",
    "python - <<",
    "python3 - <<",
    "mv ",
    "cp ",
)


def _input_command(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("command", "cmd", "command_line"):
            command = value.get(key)
            if isinstance(command, str) and command.strip():
                return command
    return None


def _compound_edit(block: dict[str, Any]) -> bool:
    """Mark a Bash tool_use that edits checkout.py before running final."""

    name = block.get("name")
    if not isinstance(name, str) or name.strip().lower() not in {"bash", "shell", "command", "terminal"}:
        return False
    command = _input_command(block.get("input", block.get("arguments", block.get("args"))))
    if command is None or "checkout.py" not in command.lower():
        return False
    phase = _HELPER_COMMAND_RE.search(command)
    if phase is None or phase.group(1).lower() != "final":
        return False
    lowered = command.lower()
    return any(marker in lowered for marker in _COMPOUND_EDIT_MARKERS)


def _event(record: dict[str, Any], *, kind: str, text: str, line_number: int, block_index: int | None = None, tool_use_id: str | None = None) -> dict[str, Any]:
    suffix = "" if block_index is None else f"-block-{block_index}"
    return {
        "id": f"{record.get('uuid', f'line-{line_number}')}{suffix}",
        "kind": kind,
        "session_id": record["sessionId"],
        "timestamp": record.get("timestamp"),
        "text": text,
        "tool_use_id": tool_use_id,
        "locator": {"artifact": SESSION_NAME, "line": line_number, **({"block": block_index} if block_index is not None else {})},
    }


def _events_for_record(
    record: dict[str, Any],
    message: dict[str, Any],
    line_number: int,
    compound_tool_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Expand tool blocks so one JSONL message cannot collapse benchmark populations."""

    compound_tool_ids = compound_tool_ids or set()
    record_type = record["type"]
    content = message.get("content")
    if record_type == "user":
        if isinstance(content, str):
            return [_event(record, kind="submitted_turn", text=content, line_number=line_number)]
        if not isinstance(content, list):
            return []
        result = []
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id", block.get("toolUseId"))
                normalized_id = tool_id if isinstance(tool_id, str) else None
                result_event = _event(record, kind="result", text=_text(block.get("content")), line_number=line_number, block_index=index, tool_use_id=normalized_id)
                result.append(result_event)
                if normalized_id in compound_tool_ids:
                    compound_result = _event(
                        record,
                        kind="result",
                        text="compound edit completed",
                        line_number=line_number,
                        block_index=index,
                        tool_use_id=f"{normalized_id}:compound-edit",
                    )
                    compound_result["id"] = f"{result_event['id']}-compound-edit"
                    compound_result["parent_tool_use_id"] = normalized_id
                    compound_result["compound"] = True
                    result.append(compound_result)
        return result
    if isinstance(content, str):
        return [_event(record, kind="response", text=content, line_number=line_number)] if content else []
    if not isinstance(content, list):
        return []
    actions = []
    for index, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_id = block.get("id")
            normalized_id = tool_id if isinstance(tool_id, str) else None
            action_event = _event(record, kind="action", text=_text(block.get("input")), line_number=line_number, block_index=index, tool_use_id=normalized_id)
            if isinstance(normalized_id, str):
                action_event["tool_name"] = block.get("name")
                action_event["input"] = block.get("input", block.get("arguments", block.get("args")))
            actions.append(action_event)
            if normalized_id in compound_tool_ids:
                compound_action = _event(
                    record,
                    kind="action",
                    text="compound edit",
                    line_number=line_number,
                    block_index=index,
                    tool_use_id=f"{normalized_id}:compound-edit",
                )
                compound_action["id"] = f"{action_event['id']}-compound-edit"
                compound_action["parent_tool_use_id"] = normalized_id
                compound_action["tool_name"] = "Edit"
                compound_action["input"] = {"file_path": "fixture_project/checkout.py", "compound_parent_tool_use_id": normalized_id}
                compound_action["compound"] = True
                # Place the semantic edit before the Bash helper action so an
                # offline reader preserves the workload's causal order.
                actions.insert(len(actions) - 1, compound_action)
    return actions or ([_event(record, kind="response", text=_text(content), line_number=line_number)] if _text(content) else [])


def decode_claude_code_bundle(package: Path) -> dict[str, Any]:
    """Decode messages represented by the declared copied JSONL file only."""

    session_bytes = _validate_package(package)
    events: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    parsed_rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(session_bytes.decode("utf-8").splitlines(), 1):
        row = _strict_json(line, f"session.jsonl:{line_number}")
        if not isinstance(row, dict):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: record must be an object")
        session_id = row.get("sessionId")
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: sessionId is missing")
        if isinstance(session_id, str):
            session_ids.add(session_id)
        parsed_rows.append((line_number, row))
    compound_tool_ids: set[str] = set()
    for _line_number, row in parsed_rows:
        if row.get("type") != "assistant":
            continue
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and _compound_edit(block):
                tool_id = block.get("id")
                if isinstance(tool_id, str) and tool_id:
                    compound_tool_ids.add(tool_id)
    for line_number, row in parsed_rows:
        record_type = row.get("type")
        if record_type not in {"user", "assistant"}:
            continue
        row_session_id = row.get("sessionId")
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != record_type or not isinstance(row_session_id, str):
            raise ClaudeCodeDecoderError(f"session.jsonl:{line_number}: message role or session ID is invalid")
        events.extend(_events_for_record(row, message, line_number, compound_tool_ids))
    if len(session_ids) != 1:
        raise ClaudeCodeDecoderError("copied session must contain exactly one session ID")
    return {
        "format": FORMAT,
        "session_id": next(iter(session_ids)),
        "events": events,
        "counts": {
            "submitted_turns": sum(event["kind"] == "submitted_turn" for event in events),
            "responses": sum(event["kind"] == "response" for event in events),
            "actions": sum(event["kind"] == "action" for event in events),
            "results": sum(event["kind"] == "result" for event in events),
        },
    }
