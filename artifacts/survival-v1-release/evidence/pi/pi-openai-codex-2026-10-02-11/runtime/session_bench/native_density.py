"""Complete native JSONL density under the frozen record-role classifier.

One nonblank native JSON object is one logical record. Its logical byte count
is the length of its complete, compact, key-sorted, UTF-8 JSON encoding with
Unicode preserved and no trailing newline. Every record is included once by
its native artifact/line locator, including metadata and unknown records.
Raw record proof hashes strip trailing CR/LF bytes, matching Codex _locator;
they hash original record bytes rather than the canonical accounting encoding.
This accounting is independent of physical JSON whitespace and filtered
survival decoder output. Classification uses supported native record functions;
unknown shapes retain their whole logical byte count in the denominator.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .v1_public_score import CLASSIFIED_CONTENT_DENSITY_RULE


BYTE_ACCOUNTING_RULE = "canonical-native-json-record-utf8-v1"
_MAX_BYTES = 64 * 1024 * 1024
_MAX_RECORDS = 100_000
_USEFUL = {"user_message", "assistant_message", "tool_call", "tool_result", "file_change"}
_UNCLASSIFIED = {"session", "metadata", "snapshot", "system"}


@dataclass(frozen=True)
class NativeDensityInventory:
    evidence: dict[str, Any]
    native_locators: tuple[dict[str, str], ...]
    diagnostics: tuple[str, ...]
    byte_accounting_rule: str = BYTE_ACCOUNTING_RULE


def _unresolved(reason: str) -> NativeDensityInventory:
    return NativeDensityInventory(
        {"evidence_complete": False, "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": []},
        (), (reason,),
    )


def _strict_object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate native JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid native JSON constant: {value}")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("native logical record is not an object")
    return value


def _codex_role(record: Mapping[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return "unknown"
    kind = record.get("type")
    if kind == "session_meta":
        return "session"
    if kind in {"turn_context", "token_usage_record", "thread_settings_applied"}:
        return "metadata"
    if kind == "world_state":
        return "snapshot"
    if kind == "response_item":
        item_type = payload.get("type")
        if item_type == "message":
            role = payload.get("role")
            content = payload.get("content")
            supported_text = isinstance(content, str) or (
                isinstance(content, list) and bool(content) and all(
                    isinstance(item, str) or (
                        isinstance(item, Mapping) and item.get("type") in {"text", "input_text", "output_text"}
                        and isinstance(item.get("text"), str)
                    ) for item in content
                )
            )
            if supported_text:
                return {"user": "user_message", "assistant": "assistant_message", "system": "system"}.get(role, "unknown") if isinstance(role, str) else "unknown"
        if item_type == "custom_tool_call" and isinstance(payload.get("call_id"), str) and isinstance(payload.get("name"), str):
            return "tool_call"
        if item_type == "custom_tool_call_output" and isinstance(payload.get("call_id"), str) and "output" in payload:
            return "tool_result"
        if item_type == "compaction":
            return "snapshot"
    if kind == "event_msg":
        if payload.get("type") in {"task_started", "task_complete", "token_count"}:
            return "metadata"
        item = payload.get("item")
        if payload.get("type") == "item_completed" and isinstance(item, Mapping):
            if item.get("type") == "FunctionCallOutput" and item.get("namespace") == "codex_app" and item.get("name") in {"create_thread", "send_message_to_thread"} and isinstance(item.get("output"), str):
                from .adapters.codex_cli_decoder import _CODEX_DELEGATION_INPUT
                if _CODEX_DELEGATION_INPUT.fullmatch(item["output"]):
                    return "user_message"
            return {
                "CommandExecution": "tool_result", "FileChange": "file_change",
                "UserMessage": "user_message", "AgentMessage": "assistant_message",
            }.get(item.get("type"), "unknown") if isinstance(item.get("type"), str) else "unknown"
    # Opaque reasoning and unsupported types have no qualified useful role.
    return "unknown"


def _claude_role(record: Mapping[str, Any]) -> str:
    kind = record.get("type")
    if kind == "system":
        return "system"
    if kind not in {"user", "assistant"}:
        return "unknown"
    message = record.get("message")
    if not isinstance(message, Mapping) or message.get("role") != kind:
        return "unknown"
    content = message.get("content")
    if isinstance(content, list):
        types = {item.get("type") for item in content if isinstance(item, Mapping) and isinstance(item.get("type"), str)}
        if len([item for item in content if isinstance(item, Mapping) and isinstance(item.get("type"), str)]) != len(content) or not types or not types <= {"text", "tool_use", "tool_result"}:
            return "unknown"
        if types == {"tool_result"} and kind == "user":
            return "tool_result"
        if types == {"tool_use"} and kind == "assistant":
            return "tool_call"
    elif not isinstance(content, str):
        return "unknown"
    return "user_message" if kind == "user" else "assistant_message"


def _pi_role(record: Mapping[str, Any]) -> str:
    kind = record.get("type")
    if kind == "session":
        return "session"
    if kind in {"model_change", "thinking_level_change", "usage", "context_edit", "custom"}:
        return "metadata"
    if kind in {"compaction", "branch_summary"}:
        return "snapshot"
    if kind != "message":
        return "unknown"
    message = record.get("message")
    if not isinstance(message, Mapping):
        return "unknown"
    role = message.get("role")
    if role == "user":
        return "user_message"
    if role == "assistant":
        content = message.get("content")
        if isinstance(content, list) and any(
                isinstance(block, Mapping) and block.get("type") == "toolCall"
                for block in content):
            return "tool_call"
        return "assistant_message" if isinstance(content, (str, list)) else "unknown"
    if role == "toolResult":
        return "tool_result"
    if role == "system":
        return "system"
    return "unknown"


def inventory_native_jsonl_density(
    package: str | Path, *, family: str, session_id: str,
    expected_artifacts: Sequence[Mapping[str, str]] | None = None,
) -> NativeDensityInventory:
    """Inventory an explicit closed copied package, never a discovered store.

    The caller must separately qualify capture completeness. Codex callers
    bind declared artifact hashes and the native session ID to their existing
    decode. Any integrity,
    malformed-record, foreign-session, or inventory failure remains unresolved.
    """
    try:
        package = Path(package)
        if not isinstance(session_id, str) or not session_id.strip():
            return _unresolved("decoded native session identity missing")
        if family == "codex":
            from .adapters.codex_cli_decoder import _validate_package
            _manifest, artifacts = _validate_package(package)
            if len({item.relative_path for item in artifacts}) != len(artifacts):
                return _unresolved("native density artifact is declared more than once")
            if any(item.path.suffix.lower() != ".jsonl" for item in artifacts):
                return _unresolved("native companion logical records are not inventoried")
            inputs = [(item.id, item.relative_path, item.sha256, item.path.read_bytes()) for item in artifacts]
            role_for = _codex_role
        elif family == "claude":
            from .adapters.claude_code_decoder import _validate_package
            data = _validate_package(package)
            inputs = [("session", "session.jsonl", hashlib.sha256(data).hexdigest(), data)]
            role_for = _claude_role
        elif family == "pi":
            jsonl_paths = sorted(path for path in package.glob("*.jsonl") if path.is_file() and not path.is_symlink())
            if len(jsonl_paths) != 1 or jsonl_paths[0].name != "session.jsonl":
                return _unresolved("Pi density requires exactly the declared copied session JSONL")
            data = jsonl_paths[0].read_bytes()
            inputs = [("pi-session", "session.jsonl", hashlib.sha256(data).hexdigest(), data)]
            role_for = _pi_role
        else:
            return _unresolved("unsupported native density family")
        if expected_artifacts is None or {(item[0], item[2]) for item in inputs} != {(item["id"], item["sha256"]) for item in expected_artifacts}:
            return _unresolved("native density artifacts differ from decoded artifacts")
        records: list[dict[str, Any]] = []
        locators: list[dict[str, str]] = []
        observed_session = False
        for artifact_id, relative, digest, data in inputs:
            if len(data) > _MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
                return _unresolved("native density artifact changed or exceeds byte limit")
            for line_number, raw_line in enumerate(data.split(b"\n"), 1):
                raw = raw_line.rstrip(b"\r\n")
                if not raw.strip():
                    continue
                if len(records) >= _MAX_RECORDS:
                    return _unresolved("native density inventory exceeds record limit")
                obj = _strict_object(raw)
                if family == "codex":
                    payload = obj.get("payload")
                    native_session = payload.get("session_id") if isinstance(payload, Mapping) else None
                    if obj.get("type") == "session_meta" and isinstance(payload, Mapping):
                        native_session = native_session or payload.get("id")
                elif family == "claude":
                    native_session = obj.get("sessionId")
                else:
                    native_session = obj.get("id") if obj.get("type") == "session" else None
                if native_session is not None:
                    if native_session != session_id:
                        return _unresolved("native density inventory contains a foreign session")
                    observed_session = True
                location = f"{artifact_id}@{relative}:line-{line_number}"
                role = role_for(obj)
                logical_bytes = len(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))
                records.append({"record_id": location, "record_kind": role, "logical_bytes": logical_bytes, "classification": "useful" if role in _USEFUL else "unclassified" if role in _UNCLASSIFIED else "unknown"})
                locators.append({"id": location, "sha256": hashlib.sha256(raw).hexdigest()})
        if not observed_session or not records:
            return _unresolved("native density inventory lacks a bound session or records")
        # Broad evidence may cite a documentation/version record. Persist the
        # byte rule and its exact implementation hash beside the native proofs.
        locators.append({"id": f"rule:{BYTE_ACCOUNTING_RULE}:session_bench/native_density.py", "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
        return NativeDensityInventory(
            {"evidence_complete": True, "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": records},
            tuple(locators), (),
        )
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as exc:
        return _unresolved(f"native density inventory failed: {exc}")
