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

The forward read is the declared JSONL file. Every record that states an event
(message text; call name and arguments; result output) is one occurrence of
that event, whatever its record type. The first record that states an event
keeps its role. A later record that states only events already stated is a
``snapshot``: unclassified, in the denominator. Duplicate safety counts the
same statements, so a copy is never also counted as useful content.
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
    if kind == "queue-operation" and record.get("operation") == "enqueue" and isinstance(record.get("content"), str) and record["content"]:
        # The queued prompt is the first record of the read that holds the prompt text.
        return "user_message"
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


def _texts(content: Any) -> str | None:
    """The joined text of a native content value; None when it holds no text."""
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    parts = [item if isinstance(item, str) else item.get("text") for item in content
             if isinstance(item, str) or (isinstance(item, Mapping) and isinstance(item.get("text"), str)
                                          and str(item.get("type")).lower() in {"text", "input_text", "output_text"})]
    return "".join(parts) or None


def codex_forward_statements(objects: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """For each rollout record in file order, the events it states.

    A message is one event per turn, role and exact text: ``response_item``,
    the ``item_completed`` copy and ``task_complete.last_agent_message`` are
    joined by text comparison. A user-role message that the harness marks as
    context (``content_item_kinds`` without ``user.text``) is not a prompt.
    A tool call is ``call_id``. ``CommandExecution`` and ``FileChange`` state
    the command or the edit, and its outcome, of the open call before them.
    """
    statements: list[tuple[str, ...]] = []
    messages: dict[tuple[Any, str, str], str] = {}
    current_turn: Any = None
    open_call: str | None = None

    def message(turn: Any, role: str, text: str | None, native_id: Any, number: int) -> tuple[str, ...]:
        if text is None:
            return ()
        key = (turn if turn is not None else current_turn, role, text)
        if key not in messages:
            messages[key] = f"message:{native_id}" if isinstance(native_id, str) and native_id else f"message:line-{number}"
        return (messages[key],)

    for number, record in enumerate(objects, 1):
        payload, events = record.get("payload"), ()
        if not isinstance(payload, Mapping):
            statements.append(())
            continue
        kind, inner = record.get("type"), payload.get("type")
        if kind == "response_item":
            meta = payload.get("internal_chat_message_metadata_passthrough")
            meta = meta if isinstance(meta, Mapping) else {}
            if inner == "message" and payload.get("role") in {"user", "assistant"}:
                kinds = meta.get("content_item_kinds")
                context = payload["role"] == "user" and isinstance(kinds, list) and bool(kinds) and "user.text" not in kinds
                if not context:
                    events = message(meta.get("turn_id"), payload["role"], _texts(payload.get("content")), payload.get("id"), number)
            elif inner == "custom_tool_call" and isinstance(payload.get("call_id"), str):
                open_call = payload["call_id"]
                events = (f"call:{open_call}",)
            elif inner == "custom_tool_call_output" and isinstance(payload.get("call_id"), str):
                events = (f"result:{payload['call_id']}",)
                if open_call == payload["call_id"]:
                    open_call = None
        elif kind == "event_msg":
            item = payload.get("item")
            if inner == "task_started":
                current_turn, open_call = payload.get("turn_id", current_turn), None
            elif inner == "task_complete":
                text = payload.get("last_agent_message")
                events = message(payload.get("turn_id"), "assistant", text if isinstance(text, str) and text else None, None, number)
            elif inner == "item_completed" and isinstance(item, Mapping):
                if item.get("type") in {"UserMessage", "AgentMessage"}:
                    events = message(payload.get("turn_id"), "user" if item["type"] == "UserMessage" else "assistant",
                                     _texts(item.get("content")), item.get("id"), number)
                elif item.get("type") in {"CommandExecution", "FileChange"}:
                    call = open_call or (item.get("id") if isinstance(item.get("id"), str) else f"line-{number}")
                    events = (f"call:{call}", f"result:{call}")
        statements.append(events)
    return statements


def claude_forward_statements(objects: Sequence[Mapping[str, Any]]) -> list[tuple[str, ...]]:
    """For each transcript record in file order, the events it states.

    A ``queue-operation`` ``enqueue`` record holds the full prompt and the next
    ``user`` record with the same text repeats it. An assistant text block is
    a message, a ``tool_use`` block is a call and a ``tool_result`` block is a
    result. ``last-prompt`` holds a cut prefix and titles hold other text; they
    state no event.
    """
    statements: list[tuple[str, ...]] = []
    queued: dict[str, list[str]] = {}
    for number, record in enumerate(objects, 1):
        kind, events = record.get("type"), []
        identity = record.get("uuid") if isinstance(record.get("uuid"), str) and record.get("uuid") else f"line-{number}"
        message = record.get("message")
        if kind == "queue-operation" and record.get("operation") == "enqueue" and isinstance(record.get("content"), str) and record["content"]:
            queued.setdefault(record["content"], []).append(f"message:line-{number}")
            events.append(f"message:line-{number}")
        elif kind in {"user", "assistant"} and isinstance(message, Mapping) and message.get("role") == kind and record.get("isMeta") is not True:
            content = message.get("content")
            blocks = [item for item in content if isinstance(item, Mapping)] if isinstance(content, list) else []
            text = content if isinstance(content, str) else "".join(
                item["text"] for item in blocks if item.get("type") == "text" and isinstance(item.get("text"), str))
            if text:
                waiting = queued.get(text) if kind == "user" else None
                events.append(waiting.pop(0) if waiting else f"message:{identity}")
            for item in blocks:
                if kind == "assistant" and item.get("type") == "tool_use" and isinstance(item.get("id"), str):
                    events.append(f"call:{item['id']}")
                elif kind == "user" and item.get("type") == "tool_result" and isinstance(item.get("tool_use_id"), str):
                    events.append(f"result:{item['tool_use_id']}")
        statements.append(tuple(events))
    return statements


FORWARD_STATEMENTS = {"codex": codex_forward_statements, "claude": claude_forward_statements}


def roles_after_repeats(roles: Sequence[str], statements: Sequence[Sequence[str]]) -> list[str]:
    """Give ``snapshot`` to a record that states only events already stated before it."""
    seen: set[str] = set()
    result = []
    for role, events in zip(roles, statements):
        result.append("snapshot" if events and all(event in seen for event in events) else role)
        seen.update(events)
    return result


def forward_occurrences(record_ids: Sequence[str], statements: Sequence[Sequence[str]]) -> list[tuple[str, str]]:
    """(event id, occurrence id) for every statement of the read, in forward order."""
    return [(event, record_id if len(events) == 1 else f"{record_id}:statement-{index}")
            for record_id, events in zip(record_ids, statements) for index, event in enumerate(events)]


def native_forward_occurrences(package: str | Path, *, family: str,
                               expected_artifacts: Sequence[Mapping[str, str]] | None) -> list[tuple[str, str]] | None:
    """Occurrences of the declared read of a copied Codex or Claude package; None when it cannot be read."""
    try:
        inputs = _family_inputs(Path(package), family)
        if inputs is None or expected_artifacts is None or {(item[0], item[2]) for item in inputs} != {(item["id"], item["sha256"]) for item in expected_artifacts}:
            return None
        occurrences: list[tuple[str, str]] = []
        for artifact_id, relative, _digest, data in inputs:
            numbered = [(number, _strict_object(raw.rstrip(b"\r\n"))) for number, raw in enumerate(data.split(b"\n"), 1) if raw.strip()]
            statements = FORWARD_STATEMENTS[family]([obj for _, obj in numbered])
            occurrences += forward_occurrences([f"{artifact_id}@{relative}:line-{number}" for number, _ in numbered], statements)
        return occurrences
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError):
        return None


def _family_inputs(package: Path, family: str) -> list[tuple[str, str, str, bytes]] | None:
    """(artifact id, relative path, sha256, bytes) of the declared Codex or Claude read."""
    if family == "codex":
        from .adapters.codex_cli_decoder import _validate_package
        _manifest, artifacts = _validate_package(package)
        if len({item.relative_path for item in artifacts}) != len(artifacts) or any(item.path.suffix.lower() != ".jsonl" for item in artifacts):
            return None
        return [(item.id, item.relative_path, item.sha256, item.path.read_bytes()) for item in artifacts]
    if family == "claude":
        from .adapters.claude_code_decoder import _validate_package
        data = _validate_package(package)
        return [("session", "session.jsonl", hashlib.sha256(data).hexdigest(), data)]
    return None


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
            first, objects = len(records), []
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
                objects.append(obj)
                logical_bytes = len(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8"))
                records.append({"record_id": location, "record_kind": role, "logical_bytes": logical_bytes, "classification": "useful" if role in _USEFUL else "unclassified" if role in _UNCLASSIFIED else "unknown"})
                locators.append({"id": location, "sha256": hashlib.sha256(raw).hexdigest()})
            if family in FORWARD_STATEMENTS:
                roles = roles_after_repeats([row["record_kind"] for row in records[first:]], FORWARD_STATEMENTS[family](objects))
                for row, role in zip(records[first:], roles):
                    row.update(record_kind=role, classification="useful" if role in _USEFUL else "unclassified" if role in _UNCLASSIFIED else "unknown")
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
