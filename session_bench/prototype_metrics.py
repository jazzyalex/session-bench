"""Bounded five-angle measurements for the Session-Bench v1 prototype.

This module reads only explicitly passed capture directories.  It does not
discover product stores, call a model, or reuse the historical evaluator.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
from typing import Any, Iterable


EDITION = "v1 prototype"
METADATA_SCHEMA = "session-bench-prototype-capture-v1"
OBSERVER_SCHEMA = "session-bench-prototype-observer-v1"
MAX_NATIVE_BYTES = 32 * 1024 * 1024

CATEGORIES = (
    {"id": "work_history", "name": "Work history", "weight": 30},
    {"id": "context_visibility", "name": "Context visibility", "weight": 15},
    {"id": "usage_transparency", "name": "Usage transparency", "weight": 20},
    {"id": "access_portability", "name": "Access and portability", "weight": 25},
    {"id": "storage_efficiency", "name": "Storage efficiency", "weight": 10},
)

CHECKS = (
    ("W1", "work_history", "Accepted messages and corrections"),
    ("W2", "work_history", "Actions, arguments, and results"),
    ("W3", "work_history", "Failures and exit states"),
    ("W4", "work_history", "Changed-file references and edit evidence"),
    ("W5", "work_history", "Order and action-result identity"),
    ("C1", "context_visibility", "Attributable model and configuration"),
    ("C2", "context_visibility", "Declared instruction/context marker"),
    ("C3", "context_visibility", "Visible plan and explanation"),
    ("U1", "usage_transparency", "Response usage coverage"),
    ("U2", "usage_transparency", "Token-kind and cache semantics"),
    ("U3", "usage_transparency", "Usage total reconciliation"),
    ("U4", "usage_transparency", "Pricing/billing provenance and unknowns"),
    ("A1", "access_portability", "Session discovery and project identity"),
    ("A2", "access_portability", "Independent native reading"),
    ("A3", "access_portability", "Declared companions and stable joins"),
    ("A4", "access_portability", "Complete copied-bundle decoding"),
    ("S1", "storage_efficiency", "Physical footprint at fixed anchors"),
    ("S2", "storage_efficiency", "Descriptive record-role composition"),
)

_OBSERVER_KINDS = {
    "user_message", "correction", "assistant_message", "tool_call",
    "tool_result", "failure", "file_change", "plan", "explanation",
}
_OBSERVER_SOURCES = {
    "user_message": {"submitted_input"},
    "correction": {"submitted_input"},
    "assistant_message": {"pty", "ui", "live_cli_stream"},
    "plan": {"pty", "ui", "live_cli_stream"},
    "explanation": {"pty", "ui", "live_cli_stream"},
    "tool_call": {"disk_ledger", "pty", "ui", "live_cli_stream"},
    "tool_result": {"disk_ledger", "pty", "ui", "live_cli_stream"},
    "failure": {"disk_ledger", "pty", "ui", "live_cli_stream"},
    "file_change": {"disk_ledger", "pty", "ui", "live_cli_stream"},
}
_SUPPORTED_FORMATS = {"codex-rollout-jsonl", "opencode-messages-json",
                      "opencode-sqlite", "opencode-sqlite-companion"}


def _strict_json(data: str | bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return value


def _relative(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("native path must be a non-empty relative POSIX path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"native path escapes capture: {value}")
    path = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink is not allowed in native path: {value}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"native path escapes capture: {value}")
    return path


def _read_json(path: Path) -> Any:
    try:
        return _strict_json(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON at {path.name}: {exc}") from exc


def _validated_metadata(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    value = _read_json(root / "metadata.json")
    if not isinstance(value, dict) or value.get("schema_version") != METADATA_SCHEMA:
        raise ValueError(f"metadata.json must use {METADATA_SCHEMA}")
    for key in ("id", "name", "surface", "version", "model", "run_id"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f"metadata.{key} must be a non-empty string")
    _iso(value.get("captured_at"), "metadata.captured_at")
    if value.get("status") not in {"evaluated", "pilot", "unavailable", "invalid"}:
        raise ValueError("metadata.status is invalid")
    if not isinstance(value.get("artifact_set_complete"), bool):
        raise ValueError("metadata.artifact_set_complete must be boolean")
    physical_bytes = value.get("physical_bytes")
    if physical_bytes is not None and (isinstance(physical_bytes, bool) or not isinstance(physical_bytes, int) or physical_bytes < 0):
        raise ValueError("metadata.physical_bytes must be a nonnegative integer when present")
    if physical_bytes is not None and (not isinstance(value.get("physical_measurement"), str) or not value["physical_measurement"].strip()):
        raise ValueError("metadata.physical_measurement must describe the physical-byte barrier")
    native = value.get("native_files")
    required = value.get("required_native_files")
    if not isinstance(native, list) or not native:
        raise ValueError("metadata.native_files must be a non-empty array")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("metadata.required_native_files must be an array of paths")
    paths: set[str] = set()
    normalized: list[dict[str, Any]] = []
    problems: list[str] = []
    total = 0
    for item in native:
        if not isinstance(item, dict):
            raise ValueError("metadata.native_files entries must be objects")
        path_value, digest, size, kind = (item.get("path"), item.get("sha256"),
                                          item.get("bytes"), item.get("kind"))
        if not isinstance(path_value, str) or path_value in paths:
            raise ValueError("native file paths must be unique strings")
        path = _relative(root, path_value)
        paths.add(path_value)
        if kind not in _SUPPORTED_FORMATS:
            raise ValueError(f"unsupported declared native kind: {kind}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid SHA-256 for {path_value}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"invalid byte count for {path_value}")
        if not path.is_file():
            problems.append(f"Declared native file is missing: {path_value}")
            content = None
        elif size > MAX_NATIVE_BYTES or total + size > MAX_NATIVE_BYTES:
            raise ValueError("capture exceeds the 32 MiB prototype native-byte limit")
        else:
            content = path.read_bytes()
            if len(content) != size or _sha256(content) != digest:
                problems.append(f"Native size or digest mismatch: {path_value}")
            total += len(content)
        normalized.append({**item, "_path": path, "_content": content})
    if len(set(required)) != len(required):
        raise ValueError("metadata.required_native_files contains duplicates")
    if any(path not in paths for path in required):
        raise ValueError("required native path is not declared")
    for path in required:
        row = next(item for item in normalized if item["path"] == path)
        if row["_content"] is None:
            problems.append(f"Required native file is unavailable: {path}")
    return value, normalized, list(dict.fromkeys(problems))


def _manifest_digest(native: list[dict[str, Any]]) -> str:
    public = [{key: value for key, value in item.items() if not key.startswith("_")}
              for item in native]
    return _sha256(_canonical(sorted(public, key=lambda item: item["path"])))


def _validated_observer(root: Path, metadata: dict[str, Any], native: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[str]]:
    path = root / "observer.json"
    if not path.is_file():
        return None, ["observer.json is missing; observer-dependent checks are unresolved."]
    try:
        value = _read_json(path)
    except ValueError as exc:
        return None, [f"Observer evidence is malformed: {exc}"]
    if not isinstance(value, dict) or value.get("schema_version") != OBSERVER_SCHEMA:
        return None, [f"Observer evidence does not use {OBSERVER_SCHEMA}."]
    if value.get("run_id") != metadata["run_id"]:
        return None, ["Observer run_id does not match metadata."]
    if value.get("artifact_manifest_sha256") != _manifest_digest(native):
        return None, ["Observer evidence is not bound to the declared native manifest."]
    if value.get("independent") is not True or not isinstance(value.get("observer_method"), str) or not value["observer_method"].strip():
        return None, ["Observer independence or method is not established."]
    events = value.get("events")
    if not isinstance(events, list):
        return None, ["Observer events are malformed."]
    ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not event["id"]:
            return None, ["Every observer event needs a non-empty ID."]
        if event["id"] in ids:
            return None, ["Observer event IDs are duplicated; observer-dependent checks are unresolved."]
        ids.add(event["id"])
        if event.get("kind") not in _OBSERVER_KINDS:
            return None, [f"Observer event {event['id']} has an unsupported kind."]
        if event.get("expected_native", True) not in {True, False}:
            return None, [f"Observer event {event['id']} has invalid expected_native."]
        if event.get("context_marker", False) not in {True, False}:
            return None, [f"Observer event {event['id']} has invalid context_marker."]
        source = event.get("source")
        if source is not None and not isinstance(source, str):
            return None, [f"Observer event {event['id']} has invalid source."]
    return value, []


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            if isinstance(item, str):
                found.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                found.append(item["text"])
        return "\n".join(found) if found else None
    return None


def _event(kind: str, locator: str, **values: Any) -> dict[str, Any]:
    return {"kind": kind, "locator": locator, **values}


def _append_tool_file_changes(events: list[dict[str, Any]], locator: str,
                              call_id: Any, tool: Any, arguments: Any) -> None:
    if not isinstance(tool, str) or not any(word in tool.lower() for word in ("edit", "write", "patch")):
        return
    if isinstance(arguments, dict):
        for key in ("filePath", "file_path", "path", "file"):
            if isinstance(arguments.get(key), str):
                events.append(_event("file_change", locator, id=call_id, file=arguments[key]))
                return
    if isinstance(arguments, str):
        for match in re.finditer(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", arguments, re.MULTILINE):
            events.append(_event("file_change", locator, id=call_id, file=match.group(1)))


def _tool_exit_code(state: dict[str, Any], part: dict[str, Any]) -> Any:
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    for source in (state, metadata, part):
        for key in ("exitCode", "exit_code", "exit"):
            if isinstance(source.get(key), int) and not isinstance(source[key], bool):
                return source[key]
    return None


def _codex_record(row: dict[str, Any], locator: str, events: list[dict[str, Any]], identity: dict[str, Any], usage: list[dict[str, Any]]) -> None:
    outer = row.get("type")
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return
    kind = payload.get("type")
    if outer == "session_meta":
        for target, sources in {
            "session_id": ("id", "session_id"), "cwd": ("cwd",),
            "version": ("cli_version", "version"), "surface": ("source", "surface"),
            "model": ("model", "model_provider"),
        }.items():
            for source in sources:
                if isinstance(payload.get(source), str) and payload[source]:
                    identity[target] = payload[source]
                    break
        for key in ("originator", "source", "thread_source"):
            if isinstance(payload.get(key), str) and payload[key]:
                identity[key] = payload[key]
        events.append(_event("session", locator, id=payload.get("id"), fields=payload))
    elif outer == "turn_context":
        for key in ("model", "cwd"):
            if isinstance(payload.get(key), str) and payload[key]:
                identity[key] = payload[key]
    elif outer == "token_usage_record":
        usage.append({"locator": locator, **payload})
    elif kind == "user_message":
        events.append(_event("user_message", locator, id=payload.get("id"), text=_text(payload.get("message"))))
    elif kind in {"agent_message", "assistant_message"}:
        events.append(_event("assistant_message", locator, id=payload.get("id"), text=_text(payload.get("content", payload.get("message")))))
    elif kind == "message" and payload.get("role") in {"user", "assistant"}:
        events.append(_event("user_message" if payload["role"] == "user" else "assistant_message",
                             locator, id=payload.get("id"), text=_text(payload.get("content"))))
    elif kind in {"function_call", "tool_call", "local_shell_call", "custom_tool_call"}:
        tool = payload.get("name")
        arguments = payload.get("arguments", payload.get("input"))
        if kind == "local_shell_call":
            action = payload.get("action")
            if isinstance(action, dict):
                tool = action.get("type", "exec")
                arguments = action
        events.append(_event("tool_call", locator, id=payload.get("call_id", payload.get("id")),
                             tool=tool, arguments=arguments, status=payload.get("status")))
        patch = arguments if isinstance(arguments, str) else ""
        for match in re.finditer(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", patch, re.MULTILINE):
            events.append(_event("file_change", locator, id=payload.get("call_id"), file=match.group(1)))
    elif kind in {"function_call_output", "tool_result", "tool_output", "custom_tool_call_output"}:
        result = payload.get("output", payload.get("result"))
        rendered = _text(result) if not isinstance(result, str) else result
        if kind == "function_call_output" and isinstance(rendered, str):
            delegated = re.search(r"<codex_delegation>.*?<input>(.*?)</input>.*?</codex_delegation>", rendered, re.DOTALL)
            if delegated:
                events.append(_event("user_message", locator, id=payload.get("id"), text=delegated.group(1)))
        code = payload.get("exit_code")
        if code is None and isinstance(rendered, str):
            match = re.search(r"Process exited with code\s+(\d+)", rendered)
            if match:
                code = int(match.group(1))
        events.append(_event("tool_result", locator, id=payload.get("call_id", payload.get("id")),
                             result=rendered, exit_code=code))
    if outer == "event_msg" and kind == "item_completed" and isinstance(payload.get("item"), dict):
        item = payload["item"]
        item_type = item.get("type")
        item_id = item.get("id")
        if item_type == "CommandExecution":
            arguments = {"command": item.get("command"), "cwd": item.get("cwd")}
            output = item.get("aggregated_output", item.get("formatted_output", item.get("stdout")))
            events.append(_event("tool_call", locator, id=item_id, tool="exec", arguments=arguments,
                                 status=item.get("status")))
            events.append(_event("tool_result", locator, id=item_id, result=output,
                                 exit_code=item.get("exit_code")))
        elif item_type == "FileChange" and isinstance(item.get("changes"), dict):
            for path in item["changes"]:
                events.append(_event("file_change", locator, id=item_id, file=path))
        elif item_type in {"UserMessage", "AgentMessage"}:
            events.append(_event("user_message" if item_type == "UserMessage" else "assistant_message",
                                 locator, id=item_id, text=_text(item.get("content"))))
        elif item_type == "FunctionCallOutput":
            rendered = _text(item.get("output")) or item.get("output")
            if isinstance(rendered, str):
                delegated = re.search(r"<codex_delegation>.*?<input>(.*?)</input>.*?</codex_delegation>", rendered, re.DOTALL)
                if delegated:
                    events.append(_event("user_message", locator, id=item_id, text=delegated.group(1)))
    if outer == "event_msg" and kind in {"token_count", "usage", "usage_update"}:
        usage.append({"locator": locator, **payload})


def _decode_codex(item: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, int], list[str]]:
    events: list[dict[str, Any]] = []
    identity: dict[str, Any] = {}
    usage: list[dict[str, Any]] = []
    composition = defaultdict(int)
    problems: list[str] = []
    content = item["_content"]
    if content is None:
        return events, identity, usage, composition, problems
    offset = 0
    for line_number, raw in enumerate(content.splitlines(keepends=True), 1):
        locator = f"{item['path']}:{line_number}#sha256={_sha256(raw.rstrip())}"
        try:
            row = _strict_json(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            problems.append(f"Malformed native JSONL at {item['path']}:{line_number}: {exc}")
            composition["Unknown/unclassified"] += len(raw)
            offset += len(raw)
            continue
        if not isinstance(row, dict):
            problems.append(f"Native JSONL record is not an object at {item['path']}:{line_number}")
            composition["Unknown/unclassified"] += len(raw)
            continue
        _codex_record(row, locator, events, identity, usage)
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        kind = payload.get("type")
        outer = row.get("type")
        if outer in {"session_meta", "turn_context", "world_state"}:
            bucket = "Context/configuration"
        elif kind in {"user_message", "agent_message", "assistant_message"}:
            bucket = "Conversation/explanation"
        elif kind == "message":
            bucket = ("Conversation/explanation" if payload.get("role") in {"user", "assistant"}
                      else "Context/configuration")
        elif kind in {"function_call", "tool_call", "local_shell_call", "custom_tool_call",
                      "function_call_output", "tool_result", "tool_output", "custom_tool_call_output"}:
            bucket = "Tool/work artifacts"
        elif kind in {"token_count", "usage", "usage_update"} or outer == "token_usage_record":
            bucket = "Usage"
        elif kind == "item_completed" and isinstance(payload.get("item"), dict):
            item_type = payload["item"].get("type")
            if item_type in {"UserMessage", "AgentMessage"}:
                bucket = "Conversation/explanation"
            elif item_type in {"CommandExecution", "FileChange", "FunctionCallOutput"}:
                bucket = "Tool/work artifacts"
            else:
                bucket = "Structural overhead"
        elif outer in {"event_msg", "response_item"}:
            bucket = "Structural overhead"
        else:
            bucket = "Unknown/unclassified"
        composition[bucket] += len(raw)
        offset += len(raw)
    if offset < len(content):
        composition["Structural overhead"] += len(content) - offset
    return events, identity, usage, composition, problems


def _opencode_messages(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        for key in ("messages", "data", "items"):
            candidate = value.get(key)
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                return candidate
        if any(key in value for key in ("role", "info", "parts")):
            return [value]
    return None


def _decode_opencode(item: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, int], list[str]]:
    content = item["_content"]
    events: list[dict[str, Any]] = []
    identity: dict[str, Any] = {}
    usage: list[dict[str, Any]] = []
    composition = defaultdict(int)
    problems: list[str] = []
    if content is None:
        return events, identity, usage, composition, problems
    try:
        value = _strict_json(content)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        # Some native/export paths use one message object per line.
        rows: list[Any] = []
        try:
            for raw in content.splitlines():
                if raw.strip():
                    rows.append(_strict_json(raw))
            value = rows
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return events, identity, usage, {"Unknown/unclassified": len(content)}, [f"Malformed OpenCode native JSON at {item['path']}: {exc}"]
    messages = _opencode_messages(value)
    if messages is None:
        return events, identity, usage, {"Unknown/unclassified": len(content)}, [f"Unsupported OpenCode message shape at {item['path']}"]
    logical: list[tuple[str, int]] = []
    for index, message in enumerate(messages):
        locator = f"{item['path']}:/messages/{index}#sha256={_sha256(_canonical(message))}"
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        role = info.get("role", message.get("role"))
        message_id = info.get("id", message.get("id"))
        for target, keys in {
            "session_id": ("sessionID", "session_id"), "cwd": ("path", "cwd", "directory"),
            "version": ("version",), "surface": ("surface", "client"),
            "model": ("modelID", "model", "model_id"),
        }.items():
            for key in keys:
                if isinstance(info.get(key), str) and info[key]:
                    identity[target] = info[key]
                    break
        provider_id = info.get("providerID", info.get("provider"))
        model_id = info.get("modelID", info.get("model"))
        if isinstance(provider_id, str) and provider_id and isinstance(model_id, str) and model_id:
            identity["model"] = (model_id if model_id.startswith(provider_id + "/")
                                 else f"{provider_id}/{model_id}")
        parts = message.get("parts")
        if not isinstance(parts, list):
            parts = [message]
        message_texts: list[str] = []
        for part_index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            part_locator = f"{locator}/parts/{part_index}"
            part_type = part.get("type")
            text = _text(part.get("text", part.get("content")))
            if part_type in {"text", "reasoning"} and text:
                message_texts.append(text)
            elif part_type in {"tool", "tool_call", "tool-invocation"} or isinstance(part.get("tool"), str):
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                call_id = part.get("callID", part.get("call_id", part.get("id")))
                events.append(_event("tool_call", part_locator, id=call_id,
                                     tool=part.get("tool", part.get("name")),
                                     arguments=state.get("input", part.get("input", part.get("arguments"))),
                                     status=state.get("status", part.get("status"))))
                tool = part.get("tool", part.get("name"))
                arguments = state.get("input", part.get("input", part.get("arguments")))
                _append_tool_file_changes(events, part_locator, call_id, tool, arguments)
                output = state.get("output", part.get("output", part.get("result")))
                if output is not None:
                    events.append(_event("tool_result", part_locator, id=call_id,
                                         result=_text(output) or (str(output) if not isinstance(output, (dict, list)) else json.dumps(output, sort_keys=True)),
                                         exit_code=_tool_exit_code(state, part)))
                candidate_file = part.get("file", state.get("file"))
                if isinstance(candidate_file, str):
                    events.append(_event("file_change", part_locator, id=call_id, file=candidate_file))
        if message_texts:
            kind = "user_message" if role == "user" else "assistant_message" if role == "assistant" else "unknown"
            if kind != "unknown":
                events.append(_event(kind, locator, id=message_id, text="\n".join(message_texts)))
        tokens = info.get("tokens")
        if isinstance(tokens, dict):
            usage.append({"locator": locator, "message_id": message_id, "tokens": tokens,
                          "cost": info.get("cost"), "model": info.get("modelID", info.get("model"))})
        bucket = "Conversation/explanation" if role in {"user", "assistant"} else "Structural overhead"
        if any(event["locator"].startswith(locator) and event["kind"].startswith("tool_") for event in events):
            bucket = "Tool/work artifacts"
        logical.append((bucket, len(_canonical(message))))
    logical_total = sum(size for _, size in logical)
    scale = min(1.0, len(content) / logical_total) if logical_total else 0.0
    assigned = 0
    for bucket, size in logical:
        amount = math.floor(size * scale)
        composition[bucket] += amount
        assigned += amount
    composition["Structural overhead"] += len(content) - assigned
    return events, identity, usage, composition, problems


def _decode_opencode_sqlite(item: dict[str, Any], native: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, int], list[str]]:
    content = item["_content"]
    composition = {"Structural overhead": len(content)} if content is not None else {}
    if content is None:
        return [], {}, [], composition, []
    try:
        with tempfile.TemporaryDirectory(prefix="session-bench-opencode-") as directory:
            clone = Path(directory)
            for artifact in native:
                if artifact["kind"] not in {"opencode-sqlite", "opencode-sqlite-companion"} or artifact["_content"] is None:
                    continue
                # A WAL snapshot is replayable; its shared-memory file contains
                # process-local lock state. Keep the SHM as evidence/composition,
                # but let SQLite create a fresh one beside the temporary clone.
                if Path(artifact["path"]).name.endswith("-shm"):
                    continue
                target = clone / Path(artifact["path"]).name
                if target.exists():
                    raise ValueError("OpenCode SQLite artifact basenames collide")
                target.write_bytes(artifact["_content"])
            path = clone / Path(item["path"]).name
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                if not {"session", "message", "part"}.issubset(tables):
                    raise ValueError("required session/message/part tables are absent")
                columns: dict[str, set[str]] = {}
                for table in ("session", "message", "part"):
                    columns[table] = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                required_columns = {
                    "session": {"id", "directory", "version"},
                    "message": {"id", "session_id", "time_created", "data"},
                    "part": {"id", "message_id", "session_id", "time_created", "data"},
                }
                if any(not required_columns[table].issubset(columns[table]) for table in required_columns):
                    raise ValueError("required OpenCode columns are absent")
                sessions = list(connection.execute('SELECT id,directory,version FROM "session" ORDER BY id'))
                messages = list(connection.execute('SELECT id,session_id,data FROM "message" ORDER BY time_created,id'))
                parts = list(connection.execute('SELECT id,message_id,session_id,data FROM "part" ORDER BY time_created,id'))
            finally:
                connection.close()
    except (sqlite3.Error, ValueError) as exc:
        return [], {}, [], composition, [f"Unsupported OpenCode SQLite shape at {item['path']}: {exc}"]

    identity: dict[str, Any] = {}
    if len(sessions) == 1:
        identity.update({"session_id": sessions[0][0], "cwd": sessions[0][1],
                         "version": sessions[0][2], "surface": "opencode"})
    by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    problems: list[str] = []
    for part_id, message_id, session_id, data in parts:
        try:
            value = _strict_json(data)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            problems.append(f"Malformed OpenCode part JSON at {item['path']}:part/{part_id}: {exc}")
            continue
        if not isinstance(value, dict):
            problems.append(f"OpenCode part data is not an object at {item['path']}:part/{part_id}")
            continue
        by_message[message_id].append({**value, "id": value.get("id", part_id),
                                       "messageID": value.get("messageID", message_id),
                                       "sessionID": value.get("sessionID", session_id),
                                       "_locator": f"{item['path']}:part/{part_id}#sha256={item['sha256']}"})
    normalized: list[dict[str, Any]] = []
    for message_id, session_id, data in messages:
        try:
            info = _strict_json(data)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            problems.append(f"Malformed OpenCode message JSON at {item['path']}:message/{message_id}: {exc}")
            continue
        if not isinstance(info, dict):
            problems.append(f"OpenCode message data is not an object at {item['path']}:message/{message_id}")
            continue
        normalized.append({"info": {**info, "id": info.get("id", message_id),
                                     "sessionID": info.get("sessionID", session_id)},
                           "parts": by_message.get(message_id, []),
                           "_locator": f"{item['path']}:message/{message_id}#sha256={item['sha256']}"})
    events: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for message in normalized:
        info = message["info"]
        locator = message["_locator"]
        role = info.get("role")
        if isinstance(info.get("modelID", info.get("model")), str):
            model_id = info.get("modelID", info.get("model"))
            provider_id = info.get("providerID", info.get("provider"))
            identity["model"] = (f"{provider_id}/{model_id}" if isinstance(provider_id, str)
                                 and provider_id and not str(model_id).startswith(provider_id + "/")
                                 else model_id)
        texts: list[str] = []
        for part in message["parts"]:
            part_locator = part.pop("_locator")
            part_type = part.get("type")
            part_text = _text(part.get("text", part.get("content")))
            if part_type in {"text", "reasoning"} and part_text:
                texts.append(part_text)
            if part_type in {"tool", "tool_call", "tool-invocation"} or isinstance(part.get("tool"), str):
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                call_id = part.get("callID", part.get("call_id", part.get("id")))
                events.append(_event("tool_call", part_locator, id=call_id,
                                     tool=part.get("tool", part.get("name")),
                                     arguments=state.get("input", part.get("input", part.get("arguments"))),
                                     status=state.get("status", part.get("status"))))
                tool = part.get("tool", part.get("name"))
                arguments = state.get("input", part.get("input", part.get("arguments")))
                _append_tool_file_changes(events, part_locator, call_id, tool, arguments)
                output = state.get("output", part.get("output", part.get("result")))
                if output is not None:
                    events.append(_event("tool_result", part_locator, id=call_id,
                                         result=_text(output) or str(output),
                                         exit_code=_tool_exit_code(state, part)))
        if texts and role in {"user", "assistant"}:
            events.append(_event("user_message" if role == "user" else "assistant_message",
                                 locator, id=info.get("id"), text="\n".join(texts)))
        if isinstance(info.get("tokens"), dict):
            usage.append({"locator": locator, "message_id": info.get("id"), "tokens": info["tokens"],
                          "cost": info.get("cost"), "model": info.get("modelID", info.get("model"))})
    return events, identity, usage, composition, problems


def _decode(native: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], dict[str, int], list[str], bool]:
    all_events: list[dict[str, Any]] = []
    identity: dict[str, Any] = {}
    usage: list[dict[str, Any]] = []
    composition = defaultdict(int)
    problems: list[str] = []
    supported = True
    for item in native:
        if item["kind"] == "opencode-sqlite-companion":
            if item["_content"] is not None:
                composition["Structural overhead"] += len(item["_content"])
            continue
        decoder = (_decode_codex if item["kind"] == "codex-rollout-jsonl"
                   else (lambda sqlite_item: _decode_opencode_sqlite(sqlite_item, native)) if item["kind"] == "opencode-sqlite"
                   else _decode_opencode)
        events, found_identity, found_usage, found_composition, found_problems = decoder(item)
        all_events.extend(events)
        identity.update({key: value for key, value in found_identity.items() if value})
        usage.extend(found_usage)
        for key, value in found_composition.items():
            composition[key] += value
        problems.extend(found_problems)
        if found_problems:
            supported = False
    return all_events, identity, usage, dict(composition), problems, supported


def _eligible_observations(observer: dict[str, Any] | None, kinds: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if observer is None:
        return [], False
    eligible: list[dict[str, Any]] = []
    for event in observer["events"]:
        if event["kind"] not in kinds or event.get("expected_native", True) is False:
            continue
        source = event.get("source")
        allowed = _OBSERVER_SOURCES[event["kind"]]
        if source not in allowed:
            continue
        if source == "live_cli_stream" and "live" not in observer["observer_method"].lower():
            continue
        eligible.append(event)
    return eligible, True


def _norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value).replace("\r\n", "\n").strip()


def _matches(observed: dict[str, Any], native: dict[str, Any]) -> bool:
    kind = observed["kind"]
    native_kind = native["kind"]
    if kind in {"correction", "user_message"}:
        return native_kind == "user_message" and _norm(observed.get("text")) == _norm(native.get("text"))
    if kind in {"assistant_message", "plan", "explanation"}:
        expected = _norm(observed.get("text"))
        actual = _norm(native.get("text"))
        return native_kind == "assistant_message" and bool(expected) and (expected == actual or expected in actual)
    if kind == "tool_call":
        if native_kind != "tool_call" or observed.get("tool") and _norm(observed.get("tool")) != _norm(native.get("tool")):
            return False
        expected_args = observed.get("arguments")
        return expected_args is None or _norm(expected_args) == _norm(native.get("arguments"))
    if kind == "tool_result":
        if native_kind != "tool_result":
            return False
        expected = observed.get("result")
        return expected is None or _norm(expected) == _norm(native.get("result"))
    if kind == "failure":
        if native_kind != "tool_result":
            return False
        actual_exit = native.get("exit_code")
        expected_exit = observed.get("exit_code")
        if not isinstance(actual_exit, int) or isinstance(actual_exit, bool) or actual_exit == 0:
            return False
        if isinstance(expected_exit, int) and not isinstance(expected_exit, bool) and actual_exit != expected_exit:
            return False
        expected_evidence = _norm(observed.get("result", observed.get("text")))
        actual_evidence = _norm(native.get("result"))
        return bool(expected_evidence) and (
            expected_evidence == actual_evidence or expected_evidence in actual_evidence)
    if kind == "file_change":
        expected = _norm(observed.get("file"))
        actual = _norm(native.get("file"))
        return native_kind == "file_change" and bool(expected) and (expected == actual or actual.endswith("/" + expected))
    return False


def _match_population(observed: list[dict[str, Any]], native: list[dict[str, Any]]) -> tuple[int, list[tuple[dict[str, Any], dict[str, Any]]]]:
    used: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for expected in observed:
        for index, candidate in enumerate(native):
            if index not in used and _matches(expected, candidate):
                used.add(index)
                pairs.append((expected, candidate))
                break
    return len(pairs), pairs


def _measurement(check_id: str, value: float | None, state: str, summary: str,
                 *, numerator: int | float | None = None, denominator: int | float | None = None,
                 locators: Iterable[str] = ()) -> dict[str, Any]:
    category = next(category for candidate, category, _ in CHECKS if candidate == check_id)
    name = next(name for candidate, _, name in CHECKS if candidate == check_id)
    return {"id": check_id, "category": category, "name": name,
            "score": None if value is None else round(max(0.0, min(1.0, value)) * 100, 2),
            "state": state, "summary": summary, "numerator": numerator,
            "denominator": denominator, "locators": list(dict.fromkeys(locators))}


def _observed_metric(check_id: str, observer: dict[str, Any] | None, kinds: set[str], native: list[dict[str, Any]], *, supported: bool, complete: bool) -> tuple[dict[str, Any], list[tuple[dict[str, Any], dict[str, Any]]]]:
    expected, observer_ready = _eligible_observations(observer, kinds)
    if not observer_ready:
        return _measurement(check_id, None, "unresolved", "Independent observer evidence is unavailable."), []
    if not expected:
        return _measurement(check_id, None, "unexercised", "No eligible independently observed event exercised this check.", numerator=0, denominator=0), []
    if not supported:
        return _measurement(check_id, None, "decoder_unsupported", "Native content is malformed or outside the supported decoder shape.", numerator=0, denominator=len(expected)), []
    matched, pairs = _match_population(expected, native)
    if matched < len(expected) and not complete:
        state = "unresolved"
        score = None
        summary = f"Recovered {matched}/{len(expected)} events, but the declared native boundary is incomplete."
    else:
        state = "measured" if matched == len(expected) else "native_absent"
        score = matched / len(expected)
        summary = f"Recovered {matched}/{len(expected)} independently observed events."
    return _measurement(check_id, score, state, summary, numerator=matched,
                        denominator=len(expected), locators=(native_event["locator"] for _, native_event in pairs)), pairs


def _numbers(value: Any, prefix: str = "") -> dict[str, float]:
    found: dict[str, float] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, (int, float)) and not isinstance(item, bool) and item >= 0:
                found[path.lower()] = float(item)
            elif isinstance(item, (dict, list)):
                found.update(_numbers(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_numbers(item, f"{prefix}.{index}"))
    return found


def _usage_metrics(usage: list[dict[str, Any]], assistant_observations: list[dict[str, Any]],
                   native_events: list[dict[str, Any]], *, observer_ready: bool,
                   complete: bool, supported: bool) -> list[dict[str, Any]]:
    if not supported:
        return [_measurement(check_id, None, "decoder_unsupported",
                             "Usage cannot be decided because native decoding is incomplete.")
                for check_id in ("U1", "U2", "U3", "U4")]
    if not usage:
        missing_state = "native_absent" if complete else "unresolved"
        missing_score = 0 if complete else None
        return [
            _measurement("U1", missing_score if observer_ready and assistant_observations else None,
                         missing_state if observer_ready and assistant_observations else "unexercised" if observer_ready else "unresolved",
                         "No native usage records were decoded." if complete else "Usage absence cannot be established from an incomplete artifact boundary.",
                         numerator=0, denominator=len(assistant_observations)),
            _measurement("U2", missing_score, missing_state,
                         "No native token-kind or cache fields were decoded." if complete else "Token-field absence cannot be established from an incomplete artifact boundary."),
            _measurement("U3", None, "unexercised", "No native usage totals were available to reconcile."),
            _measurement("U4", 0 if complete else None, "native_absent" if complete else "unresolved",
                         "No native price, cost, billing unit, or explicit unknown was decoded."
                         if complete else "Cost/billing absence cannot be established from an incomplete artifact boundary."),
        ]
    per_record = [_numbers(row) for row in usage]
    if not observer_ready:
        u1_metric = _measurement("U1", None, "unresolved", "Independent assistant-response observations are unavailable.")
    elif not assistant_observations:
        u1_metric = _measurement("U1", None, "unexercised", "No independently observed assistant-response population exercised usage coverage.", numerator=0, denominator=0)
    else:
        _, response_pairs = _match_population(assistant_observations, native_events)
        native_response_ids = {native.get("id") for _, native in response_pairs if native.get("id")}
        usage_response_ids = {row.get("message_id", row.get("assistant_message_id", row.get("response_id")))
                              for row in usage}
        usage_response_ids.discard(None)
        joined = len(native_response_ids & usage_response_ids)
        if not native_response_ids or not usage_response_ids:
            u1_metric = _measurement("U1", None, "unresolved",
                                     "Usage records and observed assistant responses lack a common stable native identity.",
                                     numerator=joined, denominator=len(assistant_observations))
        elif joined < len(assistant_observations) and not complete:
            u1_metric = _measurement("U1", None, "unresolved",
                                     f"Joined {joined}/{len(assistant_observations)} observed responses, but the native boundary is incomplete.",
                                     numerator=joined, denominator=len(assistant_observations))
        else:
            u1_metric = _measurement("U1", joined / len(assistant_observations),
                                     "measured" if joined == len(assistant_observations) else "native_absent",
                                     f"Joined usage to {joined}/{len(assistant_observations)} independently observed assistant responses.",
                                     numerator=joined, denominator=len(assistant_observations))
    token_keys = {key for row in per_record for key in row if "token" in key}
    has_input = any("input" in key or "prompt" in key for key in token_keys)
    has_output = any("output" in key or "completion" in key for key in token_keys)
    has_cache = any("cache" in key for key in token_keys)
    semantic_count = sum((has_input, has_output, has_cache))

    total_series: list[float] = []
    for row in per_record:
        candidates = [number for key, number in row.items()
                      if "token" in key and any(marker in key for marker in
                                                ("cumulative", "thread_token", "session_token", "total_token_usage"))
                      and key.endswith("total_tokens")]
        if candidates:
            total_series.append(max(candidates))
    if len(total_series) >= 2:
        consistent = all(after >= before for before, after in zip(total_series, total_series[1:]))
        u3 = _measurement("U3", 1 if consistent else 0, "measured" if consistent else "contradiction",
                          "Native cumulative usage totals are monotonic." if consistent else "Native cumulative usage totals decrease.",
                          numerator=sum(after >= before for before, after in zip(total_series, total_series[1:])),
                          denominator=len(total_series) - 1)
    else:
        u3 = _measurement("U3", None, "unexercised", "Fewer than two cumulative usage snapshots were available.")
    raw_text = _norm(usage).lower()
    provenance = any(marker in raw_text for marker in
                     ('"billing_unit"', '"currency"', '"pricing_source"', '"price_source"'))
    explicit_unknown = bool(re.search(r'"(?:cost|price|billing)[^"]*"\s*:\s*"unknown"', raw_text))
    provenance = provenance or explicit_unknown
    return [
        u1_metric,
        _measurement("U2", semantic_count / 3, "measured",
                     f"Decoded {semantic_count}/3 token semantic families: input, output, cache.", numerator=semantic_count, denominator=3),
        u3,
        _measurement("U4", 1 if provenance else 0 if complete else None,
                     "measured" if provenance else "native_absent" if complete else "unresolved",
                     "Native usage includes cost/billing provenance or an explicit unknown."
                     if provenance else "Native usage does not expose cost/billing provenance or an explicit unknown."
                     if complete else "Cost/billing absence cannot be established from an incomplete artifact boundary."),
    ]


def _fixed_size_score(size: int) -> float:
    low, high = 64 * 1024, 4 * 1024 * 1024
    if size <= low:
        return 1.0
    if size >= high:
        return 0.0
    return 1.0 - (math.log(size) - math.log(low)) / (math.log(high) - math.log(low))


def _category_rows(measurements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in CATEGORIES:
        checks = [item for item in measurements if item["category"] == category["id"]]
        measured = [item for item in checks if item["score"] is not None]
        # Fixed denominator: unresolved checks cannot improve the visible category score.
        score = sum(item["score"] for item in measured) / len(checks) if measured else None
        rows.append({**category, "score": None if score is None else round(score, 2),
                     "summary": f"{len(measured)}/{len(checks)} checks measured; unresolved checks contribute no displayed points.",
                     "checks": checks})
    return rows


def analyze_capture(capture_dir: Path) -> dict[str, Any]:
    """Measure one immutable, explicitly supplied capture directory."""
    root = Path(capture_dir).resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("capture_dir must be a real directory")
    metadata, native, integrity_problems = _validated_metadata(root)
    observer, observer_problems = _validated_observer(root, metadata, native)
    events, identity, usage, composition, decode_problems, decoder_supported = _decode(native)
    metadata_limitations = metadata.get("limitations", [])
    if not isinstance(metadata_limitations, list) or any(not isinstance(item, str) for item in metadata_limitations):
        raise ValueError("metadata.limitations must be an array of strings when present")
    limitations = [*metadata_limitations, *integrity_problems, *observer_problems, *decode_problems]
    integrity_ok = not integrity_problems
    complete = metadata["artifact_set_complete"] and integrity_ok

    measurements: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    for check_id, kinds in (
        ("W1", {"user_message", "correction"}),
        ("W2", {"tool_call", "tool_result"}),
        ("W3", {"failure"}),
        ("W4", {"file_change"}),
        ("C3", {"plan", "explanation"}),
    ):
        metric, pairs = _observed_metric(check_id, observer, kinds, events,
                                         supported=decoder_supported, complete=complete)
        measurements.append(metric)
        for expected, native_event in pairs[:2]:
            examples.append({"label": f"{check_id} {expected['id']}",
                             "observed": _norm(expected.get("text", expected.get("result", expected.get("file", expected.get("tool"))))),
                             "recorded": _norm(native_event.get("text", native_event.get("result", native_event.get("file", native_event.get("tool"))))),
                             "locator": native_event["locator"]})

    context_events: list[dict[str, Any]] = []
    context_ready = observer is not None
    if observer is not None:
        context_events = [event for event in observer["events"]
                          if event.get("context_marker") is True
                          and event["kind"] in {"user_message", "correction"}
                          and event.get("source") == "submitted_input"
                          and event.get("expected_native", True)]
    if not context_ready:
        measurements.append(_measurement("C2", None, "unresolved", "Independent observer evidence is unavailable."))
    elif not context_events:
        measurements.append(_measurement("C2", None, "unexercised", "No predeclared context marker exercised this check."))
    else:
        matched, pairs = _match_population(context_events, events)
        if matched < len(context_events) and not complete:
            measurements.append(_measurement("C2", None, "unresolved",
                                             f"Recovered {matched}/{len(context_events)} markers, but the native boundary is incomplete.",
                                             numerator=matched, denominator=len(context_events)))
        elif not decoder_supported:
            measurements.append(_measurement("C2", None, "decoder_unsupported", "The context marker cannot be decided by the supported decoder."))
        else:
            measurements.append(_measurement("C2", matched / len(context_events),
                                             "measured" if matched == len(context_events) else "native_absent",
                                             f"Recovered {matched}/{len(context_events)} predeclared context markers.",
                                             numerator=matched, denominator=len(context_events),
                                             locators=(native_event["locator"] for _, native_event in pairs)))

    calls = [event for event in events if event["kind"] == "tool_call" and event.get("id")]
    results = [event for event in events if event["kind"] == "tool_result" and event.get("id")]
    unique_calls = {event["id"]: event for event in calls}
    unique_results = {event["id"]: event for event in results}
    joined = len(set(unique_calls) & set(unique_results))
    duplicate_ids = len(calls) - len(unique_calls) + len(results) - len(unique_results)
    if calls or results:
        population = max(len(unique_calls), len(unique_results))
        order_score = joined / population if population else 0
        state = "measured" if duplicate_ids == 0 else "contradiction"
        if duplicate_ids:
            order_score = 0
        measurements.insert(4, _measurement("W5", order_score, state,
                                             f"Joined {joined}/{population} unique calls and results; {duplicate_ids} duplicate IDs.",
                                             numerator=joined, denominator=population))
    else:
        measurements.insert(4, _measurement("W5", None, "unexercised", "No native action/result population was decoded."))

    expected_surface = metadata["surface"].lower()
    surface_values = [_norm(identity.get(key)).lower() for key in ("surface", "source", "originator") if identity.get(key)]
    identity_pairs = (("version", identity.get("version"), metadata["version"]),
                      ("model", identity.get("model"), metadata["model"]))
    comparable = [(key, actual, expected) for key, actual, expected in identity_pairs if actual]
    surface_comparable = bool(surface_values)
    expected_surface_words = set(re.findall(r"[a-z0-9]+", expected_surface))
    surface_match = any(value in expected_surface or expected_surface in value
                        or bool(expected_surface_words & set(re.findall(r"[a-z0-9]+", value)))
                        for value in surface_values)
    matches = int(surface_match) + sum(
        _norm(actual).lower() == _norm(expected).lower()
        or _norm(actual).lower() in _norm(expected).lower()
        or _norm(expected).lower() in _norm(actual).lower()
        for _, actual, expected in comparable)
    comparable_count = len(comparable) + int(surface_comparable)
    measurements.append(_measurement("C1", matches / 3 if comparable_count else None,
                                     "measured" if matches == comparable_count and comparable_count == 3 else "contradiction" if matches < comparable_count else "unresolved",
                                     f"Native records establish and match {matches}/3 declared identity fields; {comparable_count} fields were comparable.",
                                     numerator=matches, denominator=3))

    assistant_observations, assistant_observer_ready = _eligible_observations(
        observer, {"assistant_message", "plan", "explanation"})
    measurements.extend(_usage_metrics(usage, assistant_observations, events,
                                       observer_ready=assistant_observer_ready, complete=complete,
                                       supported=decoder_supported))

    session_ok = bool(identity.get("session_id"))
    project_ok = bool(identity.get("cwd"))
    measurements.append(_measurement("A1", (session_ok + project_ok) / 2, "measured" if session_ok or project_ok else "native_absent",
                                     f"Native session/project identity fields present: {int(session_ok) + int(project_ok)}/2.",
                                     numerator=int(session_ok) + int(project_ok), denominator=2))
    readable = integrity_ok and decoder_supported and bool(events)
    measurements.append(_measurement("A2", 1 if readable else 0,
                                     "measured" if readable else "decoder_unsupported" if not decoder_supported else "invalid_capture",
                                     f"Decoded {len(events)} native events from hash-verified declared artifacts." if readable else "Declared native artifacts were not independently readable."))
    required = metadata["required_native_files"]
    required_present = sum(any(item["path"] == path and item["_content"] is not None for item in native) for path in required)
    stable_join = (not calls and not results) or (joined == max(len(unique_calls), len(unique_results)) and duplicate_ids == 0)
    companion_score = required_present / len(required) if required else 1.0
    measurements.append(_measurement("A3", (companion_score + int(stable_join)) / 2,
                                     "measured" if required_present == len(required) and stable_join else "invalid_capture",
                                     f"Required artifacts present {required_present}/{len(required)}; stable action/result joins: {stable_join}.",
                                     numerator=required_present, denominator=len(required)))
    measurements.append(_measurement("A4", 1 if complete and readable else None,
                                     "measured" if complete and readable else "unresolved" if integrity_ok else "invalid_capture",
                                     "Complete declared bundle decoded from the copied capture." if complete and readable else "Complete copied-bundle decoding is not established."))

    native_bytes = sum(len(item["_content"]) for item in native if item["_content"] is not None)
    physical_bytes = metadata.get("physical_bytes")
    if complete and readable and physical_bytes is not None:
        measurements.append(_measurement("S1", _fixed_size_score(physical_bytes), "measured",
                                         f"{physical_bytes} incremental physical bytes scored against frozen 64 KiB (full) and 4 MiB (zero) logarithmic anchors."))
    else:
        measurements.append(_measurement("S1", None, "unresolved", "Physical efficiency requires a complete decodable boundary and a declared incremental physical-byte measurement."))
    measurements.append(_measurement(
        "S2", None, "unresolved",
        "Record-role byte composition is descriptive only; this prototype has no comparable cross-container payload-byte classifier."))

    # Restore the frozen check order after checks were calculated in dependency order.
    by_id = {item["id"]: item for item in measurements}
    measurements = [by_id[check_id] for check_id, _, _ in CHECKS]
    if integrity_problems:
        for item in measurements:
            item["score"] = None
            item["state"] = "invalid_capture"
            item["summary"] = "Artifact integrity failed; this check is not scored."
    category_rows = _category_rows(measurements)
    coverage = round(sum(item["score"] is not None for item in measurements) / len(CHECKS) * 100, 2)
    fully_rankable = (metadata["status"] == "evaluated" and complete and decoder_supported
                      and observer is not None and all(item["score"] is not None for item in measurements))
    total_score = None
    if fully_rankable:
        total_score = round(sum(row["score"] * row["weight"] for row in category_rows) / 100, 2)
    status = metadata["status"]
    if integrity_problems:
        status = "invalid"
    facts = [
        {"label": "Capture status", "value": status, "detail": f"run_id={metadata['run_id']}"},
        {"label": "Measurement coverage", "value": f"{coverage:.2f}%", "detail": f"{sum(item['score'] is not None for item in measurements)}/{len(CHECKS)} prototype checks"},
        {"label": "Artifact manifest SHA-256", "value": _manifest_digest(native), "detail": "Binds observer evidence to the sorted declared native manifest."},
        {"label": "Native events decoded", "value": len(events), "detail": f"usage records={len(usage)}; complete boundary={complete}"},
        {"label": "Usage records decoded", "value": len(usage), "detail": "Raw record count only; U1 requires stable joins and cannot be earned by snapshot density."},
    ]
    if metadata["status"] == "pilot":
        limitations.append("Pilot capture: category measurements are shown, but the overall score is deliberately unranked.")
    if not fully_rankable and metadata["status"] == "evaluated":
        limitations.append("Incomplete evaluated capture: one or more required checks are unresolved, so no overall score is reported.")
    composition_rows = [{"label": label, "bytes": composition.get(label, 0)} for label in (
        "Conversation/explanation", "Tool/work artifacts", "Context/configuration", "Usage",
        "Structural overhead", "Unknown/unclassified") if composition.get(label, 0)]
    return {
        "id": metadata["id"], "name": metadata["name"], "surface": metadata["surface"],
        "version": metadata["version"], "model": metadata["model"], "status": status,
        "score": total_score, "coverage": coverage, "runs": 1, "categories": category_rows,
        "facts": facts, "composition": composition_rows, "native_bytes": native_bytes,
        "physical_bytes": physical_bytes,
        "examples": examples[:8], "limitations": list(dict.fromkeys(limitations)),
        "evidence_path": str(root), "run_id": metadata["run_id"],
        "captured_at": metadata["captured_at"], "measurements": measurements,
        "rankable": fully_rankable,
    }


def _aggregate_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    if any((row["name"], row["surface"], row["version"], row["model"]) !=
           (first["name"], first["surface"], first["version"], first["model"]) for row in rows):
        raise ValueError(f"configuration identity changed across runs for {first['id']}")
    if len(rows) == 1:
        # Preserve concrete facts and check-level category evidence for a
        # single-run pilot. Multi-run rows below add explicit aggregation.
        return {**first, "run_results": rows}
    categories: list[dict[str, Any]] = []
    for category in CATEGORIES:
        candidates = [next(item for item in row["categories"] if item["id"] == category["id"]) for row in rows]
        values = [item["score"] for item in candidates if item["score"] is not None]
        categories.append({**category, "score": round(sum(values) / len(rows), 2) if values else None,
                           "summary": f"Equal-run aggregate: {len(values)}/{len(rows)} runs supplied a category score.",
                           "range": [min(values), max(values)] if values else None})
    rankable = all(row["rankable"] for row in rows)
    score = round(sum(category["score"] * category["weight"] for category in categories) / 100, 2) if rankable else None
    composition = defaultdict(int)
    for row in rows:
        for item in row["composition"]:
            composition[item["label"]] += item["bytes"]
    return {
        "id": first["id"], "name": first["name"], "surface": first["surface"],
        "version": first["version"], "model": first["model"],
        "status": "evaluated" if rankable else "pilot" if all(row["status"] == "pilot" for row in rows) else "incomplete",
        "score": score, "coverage": round(sum(row["coverage"] for row in rows) / len(rows), 2),
        "runs": len(rows), "categories": categories,
        "facts": [
            {"label": "Runs", "value": len(rows), "detail": "Every run has equal aggregate weight."},
            {"label": "Run coverage range", "value": f"{min(row['coverage'] for row in rows):.2f}–{max(row['coverage'] for row in rows):.2f}%", "detail": "Frozen 18-check denominator per run."},
            {"label": "Run IDs", "value": ", ".join(row["run_id"] for row in rows), "detail": "All supplied attempts are retained."},
        ],
        "composition": [{"label": label, "bytes": value} for label, value in sorted(composition.items())],
        "native_bytes": sum(row["native_bytes"] for row in rows),
        "physical_bytes": (sum(row["physical_bytes"] for row in rows)
                           if all(row["physical_bytes"] is not None for row in rows) else None),
        "examples": [example for row in rows for example in row["examples"]][:8],
        "limitations": list(dict.fromkeys(item for row in rows for item in row["limitations"])),
        "evidence_path": ", ".join(row["evidence_path"] for row in rows),
        "run_results": rows,
    }


def build_report(capture_dirs: list[Path]) -> dict[str, Any]:
    """Build the static report data model from explicitly supplied captures."""
    analyzed = [analyze_capture(path) for path in capture_dirs]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in analyzed:
        if any(existing["run_id"] == row["run_id"] for existing in grouped[row["id"]]):
            raise ValueError(f"duplicate run_id for configuration {row['id']}: {row['run_id']}")
        grouped[row["id"]].append(row)
    configurations = [_aggregate_group(rows) for _, rows in sorted(grouped.items())]
    ranked = [row for row in configurations if row["score"] is not None]
    if ranked:
        headline = f"{len(ranked)} configuration(s) have complete evaluated evidence; pilot and incomplete rows remain unranked."
    elif configurations:
        headline = "Prototype measurements are available, but no configuration has complete evaluated evidence for ranking."
    else:
        headline = "No capture directories were supplied; no results are available."
    return {
        "edition": EDITION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "headline": headline,
        "method": "Eighteen frozen native-artifact checks across five weighted categories; equal run weights; unresolved checks never leave the fixed denominator; only complete evaluated captures receive an overall score.",
        "categories": [dict(category) for category in CATEGORIES],
        "configurations": configurations,
        "limitations": [
            "This bounded prototype decodes only declared Codex rollout JSONL and OpenCode message JSON files.",
            "A native absence requires a complete hash-verified artifact boundary and eligible independent observation; otherwise the result is unresolved or decoder unsupported.",
            "Pilot and incomplete configurations are never ranked, and the report invents no cohort ranks.",
            "S1 uses fixed predeclared anchors only when incremental physical bytes are supplied; it does not infer runtime cost, token use, or lost work.",
            "Composition classifies whole JSONL records by role and SQLite container bytes as structural; it is descriptive and does not score JSONL against SQLite.",
        ],
    }
