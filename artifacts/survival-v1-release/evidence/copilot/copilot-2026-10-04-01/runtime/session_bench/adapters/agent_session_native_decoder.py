"""Bounded native row decoder for Agent Sessions' missing survival families.

This module accepts only caller-supplied bytes copied into a declared package.
It does not discover home directories, inspect credentials, launch a harness, or
score a session. Rows are retained in source order; any event family outside
the checked-in fixture vocabulary makes the result ``decoder_unsupported``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_RECORD_BYTES = 4 * 1024 * 1024

SUPPORTED_AGENT_TYPES = frozenset({"pi", "openclaw", "kimi", "hermes", "copilot", "antigravity"})

_PI_TYPES = frozenset({"session", "model_change", "thinking_level_change", "message", "compaction"})
_OPENCLAW_TYPES = _PI_TYPES
_KIMI_TYPES = frozenset({
    "metadata", "config.update", "tools.set_active_tools", "turn.prompt",
    "context.append_message", "context.append_loop_event", "llm.tools_snapshot",
    "llm.request", "agent.message.appended", "agent.turn.started", "agent.turn.ended",
    "turn.steer", "turn.cancel", "turn.ended", "prompt.completed", "profile.bind",
    "runtime.set_binding", "permission.set_mode", "token_counting.measured",
    "token_counting.turn_recorded", "context.apply_compaction", "full_compaction.begin",
    "full_compaction.complete", "permission.record_approval_result", "plan_mode.enter",
    "plan_mode.exit", "usage.record", "content.part", "step.begin", "tool.call",
    "tool.result", "step.end", "assistant.message", "think", "text",
})
_COPILOT_TYPES = frozenset({
    "session.start", "session.model_change", "session.info", "session.error", "hook.start",
    "system.message", "user.message", "assistant.turn_start", "assistant.message",
    "tool.execution_start", "tool.execution_complete", "assistant.turn_end",
    "session.auto_mode_resolved", "session.usage_checkpoint", "session.shutdown",
    "subagent.started", "subagent.completed", "abort", "assistant.reasoning",
    "session.resume", "session.truncation", "system.notification",
    "permission.requested", "permission.completed",
})
_ANTIGRAVITY_TYPES = frozenset({
    "USER_INPUT", "CONVERSATION_HISTORY", "PLANNER_RESPONSE", "LIST_DIRECTORY", "VIEW_FILE",
    "RUN_COMMAND", "CHECKPOINT", "CODE_ACTION", "SEARCH_WEB", "SYSTEM_MESSAGE", "GENERIC",
    "GREP_SEARCH",
})
_KIMI_LOOP_TYPES = frozenset({"content.part", "step.begin", "step.end", "tool.call", "tool.result"})
_KIMI_PART_TYPES = frozenset({"text", "think"})


class NativeDecodeError(ValueError):
    """The supplied artifact is malformed or outside the fixed decoder bounds."""


@dataclass(frozen=True)
class NativeEvent:
    sequence: int
    native_sequence: int | None
    event_type: str
    event_id: str | None
    parent_id: str | None
    timestamp: str | int | float | None
    kind: str
    role: str | None
    name: str | None
    content: str | None
    raw: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "native_sequence": self.native_sequence,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "parent_id": self.parent_id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "role": self.role,
            "name": self.name,
            "content": self.content,
            "raw": dict(self.raw),
        }


@dataclass(frozen=True)
class NativeDecodeResult:
    agent: str
    status: str
    format_id: str
    session_id: str | None
    version: str | int | None
    header: Mapping[str, Any] | None
    events: tuple[NativeEvent, ...]
    unsupported_types: tuple[str, ...]
    issues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "format_id": self.format_id,
            "session_id": self.session_id,
            "version": self.version,
            "header": None if self.header is None else dict(self.header),
            "events": [event.as_dict() for event in self.events],
            "unsupported_types": list(self.unsupported_types),
            "issues": list(self.issues),
        }


def _strict_json(text: str, source: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise NativeDecodeError(f"{source}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except NativeDecodeError:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        raise NativeDecodeError(f"{source}: invalid JSON") from exc


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None
    chunks: list[str] = []
    for block in value:
        if not isinstance(block, Mapping):
            continue
        kind = str(block.get("type", "")).lower().replace("_", "").replace("-", "")
        if kind in {"text", "toolresult"}:
            part = block.get("text", block.get("content"))
            if isinstance(part, str):
                chunks.append(part)
    return "".join(chunks) if chunks else None


def _event(
    sequence: int, raw: Mapping[str, Any], event_type: str, *, kind: str = "meta",
    role: str | None = None, name: str | None = None, content: str | None = None,
    event_id: Any = None, parent_id: Any = None, timestamp: Any = None,
    native_sequence: Any = None,
) -> NativeEvent:
    return NativeEvent(
        sequence=sequence,
        native_sequence=native_sequence if isinstance(native_sequence, int) and not isinstance(native_sequence, bool) else None,
        event_type=event_type,
        event_id=event_id if isinstance(event_id, str) else None,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        timestamp=timestamp if isinstance(timestamp, (str, int, float)) and not isinstance(timestamp, bool) else None,
        kind=kind,
        role=role,
        name=name,
        content=content,
        raw=raw,
    )


def _pi_openclaw_event(sequence: int, raw: Mapping[str, Any], *, agent: str) -> list[NativeEvent]:
    event_type = raw.get("type")
    if not isinstance(event_type, str):
        return [_event(sequence, raw, "untyped", kind="unsupported")]
    known = _PI_TYPES if agent == "pi" else _OPENCLAW_TYPES
    if event_type not in known:
        return [_event(sequence, raw, event_type, kind="unsupported")]
    message = raw.get("message")
    stamp = raw.get("timestamp")
    if event_type == "message" and isinstance(message, Mapping):
        stamp = stamp if stamp is not None else message.get("timestamp")
        role = message.get("role") if isinstance(message.get("role"), str) else None
        content = message.get("content")
        norm = (role or "").lower().replace("_", "")
        if norm in {"toolresult", "bashexecution"}:
            return [_event(sequence, raw, event_type, kind="tool_result", role=role,
                           name=message.get("toolName") or message.get("tool_name"),
                           content=_text(content), event_id=message.get("toolCallId"),
                           parent_id=raw.get("parentId"), timestamp=stamp)]
        if isinstance(content, list):
            out: list[NativeEvent] = []
            for index, block in enumerate(content):
                if not isinstance(block, Mapping):
                    continue
                block_type = str(block.get("type", "")).lower().replace("_", "").replace("-", "")
                if block_type == "text":
                    kind, text = ("user" if norm in {"user", "human"} else "assistant"), block.get("text")
                    out.append(_event(sequence, raw, event_type, kind=kind, role=role,
                                      content=text if isinstance(text, str) else None,
                                      event_id=raw.get("id"), parent_id=raw.get("parentId"), timestamp=stamp,
                                      native_sequence=index))
                elif block_type == "thinking":
                    out.append(_event(sequence, raw, event_type, kind="meta", role="thinking",
                                      event_id=raw.get("id"), parent_id=raw.get("parentId"), timestamp=stamp,
                                      native_sequence=index))
                elif block_type == "toolcall":
                    out.append(_event(sequence, raw, event_type, kind="tool_call", role=role,
                                      name=block.get("name"), content=json.dumps(block.get("arguments", block.get("input")), ensure_ascii=False) if block.get("arguments", block.get("input")) is not None else None,
                                      event_id=block.get("id"), parent_id=raw.get("parentId"), timestamp=stamp,
                                      native_sequence=index))
                elif block_type == "toolresult":
                    out.append(_event(sequence, raw, event_type, kind="tool_result", role="tool",
                                      name=block.get("toolName") or block.get("name"),
                                      content=_text(block.get("content", block.get("text"))),
                                      event_id=block.get("toolCallId") or block.get("id"),
                                      parent_id=raw.get("parentId"), timestamp=stamp, native_sequence=index))
                else:
                    out.append(_event(sequence, raw, f"{event_type}:{block_type or 'unknown-block'}", kind="unsupported",
                                      role=role, event_id=raw.get("id"), parent_id=raw.get("parentId"), timestamp=stamp,
                                      native_sequence=index))
            return out or [_event(sequence, raw, event_type, kind="meta", role=role,
                                  event_id=raw.get("id"), parent_id=raw.get("parentId"), timestamp=stamp)]
        kind = "user" if norm in {"user", "human"} else "assistant" if norm == "assistant" else "meta"
        return [_event(sequence, raw, event_type, kind=kind, role=role, content=_text(content),
                       event_id=raw.get("id"), parent_id=raw.get("parentId"), timestamp=stamp)]
    return [_event(sequence, raw, event_type, event_id=raw.get("id"), parent_id=raw.get("parentId"),
                   timestamp=stamp)]


def _kimi_event(sequence: int, raw: Mapping[str, Any]) -> list[NativeEvent]:
    event_type = raw.get("type")
    if not isinstance(event_type, str):
        return [_event(sequence, raw, "untyped", kind="unsupported")]
    if event_type not in _KIMI_TYPES:
        return [_event(sequence, raw, event_type, kind="unsupported", timestamp=raw.get("time"))]
    timestamp = raw.get("time")
    if event_type == "turn.prompt":
        return [_event(sequence, raw, event_type, kind="user", role="user", content=_text(raw.get("input")), timestamp=timestamp)]
    if event_type == "context.append_message" and isinstance(raw.get("message"), Mapping):
        message = raw["message"]
        role = message.get("role") if isinstance(message.get("role"), str) else None
        norm = (role or "").lower()
        kind = {"user": "user", "assistant": "assistant", "tool": "tool_result"}.get(norm, "meta")
        out = [_event(sequence, raw, event_type, kind=kind, role=role,
                      content=_text(message.get("content")), timestamp=timestamp)]
        calls = message.get("toolCalls")
        if isinstance(calls, list):
            for index, call in enumerate(calls):
                if isinstance(call, Mapping):
                    args = call.get("arguments")
                    out.append(_event(sequence, raw, event_type, kind="tool_call", role=role,
                                      name=call.get("name"), content=args if isinstance(args, str) else None,
                                      event_id=call.get("id"), timestamp=timestamp, native_sequence=index))
        return out
    if event_type == "context.append_loop_event" and isinstance(raw.get("event"), Mapping):
        event = raw["event"]
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in _KIMI_LOOP_TYPES:
            return [_event(sequence, raw, f"{event_type}:{kind or 'untyped'}", kind="unsupported", timestamp=timestamp)]
        if kind == "content.part" and isinstance(event.get("part"), Mapping):
            part = event["part"]
            ptype = part.get("type")
            if not isinstance(ptype, str) or ptype not in _KIMI_PART_TYPES:
                return [_event(sequence, raw, f"{event_type}:content.part:{ptype or 'untyped'}",
                               kind="unsupported", timestamp=timestamp)]
            if ptype == "text":
                text = part.get("text")
                return [_event(sequence, raw, event_type, kind="assistant", role="assistant",
                               content=text if isinstance(text, str) else None,
                               event_id=event.get("stepUuid"), parent_id=event.get("stepUuid"), timestamp=timestamp)]
            if ptype == "think":
                return [_event(sequence, raw, event_type, kind="meta", role="thinking",
                               event_id=event.get("uuid"), parent_id=event.get("stepUuid"), timestamp=timestamp)]
        if kind == "tool.call":
            args = event.get("args")
            return [_event(sequence, raw, event_type, kind="tool_call", role="assistant",
                           name=event.get("name"), content=json.dumps(args, ensure_ascii=False) if args is not None else None,
                           event_id=event.get("toolCallId") or event.get("uuid"),
                           parent_id=event.get("stepUuid"), timestamp=timestamp)]
        if kind == "tool.result":
            return [_event(sequence, raw, event_type, kind="tool_result", role="tool",
                           content=_text(event.get("content", event.get("output"))),
                           event_id=event.get("toolCallId"), parent_id=event.get("parentUuid"), timestamp=timestamp)]
        return [_event(sequence, raw, event_type, timestamp=timestamp)]
    return [_event(sequence, raw, event_type, event_id=raw.get("id"), timestamp=timestamp)]


def _copilot_event(sequence: int, raw: Mapping[str, Any]) -> list[NativeEvent]:
    event_type, data = raw.get("type"), raw.get("data")
    if not isinstance(event_type, str) or not isinstance(data, Mapping):
        return [_event(sequence, raw, "invalid-envelope", kind="unsupported")]
    if event_type not in _COPILOT_TYPES:
        return [_event(sequence, raw, event_type, kind="unsupported", timestamp=raw.get("timestamp"))]
    timestamp = raw.get("timestamp")
    event_id, parent_id = raw.get("id"), raw.get("parentId")
    if event_type == "user.message":
        return [_event(sequence, raw, event_type, kind="user", role="user",
                       content=_text(data.get("content")), event_id=event_id, parent_id=parent_id, timestamp=timestamp)]
    if event_type == "assistant.message":
        out = [_event(sequence, raw, event_type, kind="assistant", role="assistant",
                      content=_text(data.get("content")), event_id=event_id, parent_id=parent_id, timestamp=timestamp)]
        requests = data.get("toolRequests")
        if isinstance(requests, list):
            for index, request in enumerate(requests):
                if isinstance(request, Mapping):
                    args = request.get("arguments")
                    if isinstance(args, (Mapping, list)):
                        args_text = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    else:
                        args_text = args if isinstance(args, str) else None
                    out.append(_event(sequence, raw, event_type, kind="tool_call", role="assistant",
                                      name=request.get("name"), content=args_text,
                                      event_id=request.get("toolCallId"), parent_id=data.get("messageId"),
                                      timestamp=timestamp, native_sequence=index))
        return out
    if event_type == "tool.execution_start":
        args = data.get("arguments")
        return [_event(sequence, raw, event_type, kind="tool_call", role="assistant",
                       name=data.get("toolName"), content=json.dumps(args, ensure_ascii=False) if args is not None else None,
                       event_id=data.get("toolCallId"), parent_id=parent_id, timestamp=timestamp)]
    if event_type == "tool.execution_complete":
        result = data.get("result") if isinstance(data.get("result"), Mapping) else {}
        return [_event(sequence, raw, event_type, kind="tool_result" if data.get("success") is True else "error",
                       role="tool", content=_text(result.get("content")), event_id=data.get("toolCallId"),
                       parent_id=parent_id, timestamp=timestamp)]
    return [_event(sequence, raw, event_type, event_id=event_id, parent_id=parent_id, timestamp=timestamp)]


def _antigravity_event(sequence: int, raw: Mapping[str, Any]) -> list[NativeEvent]:
    event_type = raw.get("type")
    if not isinstance(event_type, str):
        return [_event(sequence, raw, "untyped", kind="unsupported")]
    if event_type not in _ANTIGRAVITY_TYPES:
        return [_event(sequence, raw, event_type, kind="unsupported", timestamp=raw.get("created_at"),
                       native_sequence=raw.get("step_index"))]
    timestamp = raw.get("created_at")
    step = raw.get("step_index")
    if event_type == "USER_INPUT":
        return [_event(sequence, raw, event_type, kind="user", role="user", content=_text(raw.get("content")),
                       timestamp=timestamp, native_sequence=step)]
    if event_type == "PLANNER_RESPONSE":
        out = [_event(sequence, raw, event_type, kind="assistant", role="assistant",
                      content=_text(raw.get("content")), timestamp=timestamp, native_sequence=step)]
        calls = raw.get("tool_calls")
        if isinstance(calls, list):
            for index, call in enumerate(calls):
                if isinstance(call, Mapping):
                    args = call.get("args")
                    out.append(_event(sequence, raw, event_type, kind="tool_call", role="assistant",
                                      name=call.get("name"), content=args if isinstance(args, str) else None,
                                      timestamp=timestamp, native_sequence=index))
        return out
    if event_type in {"LIST_DIRECTORY", "VIEW_FILE", "RUN_COMMAND", "CODE_ACTION", "SEARCH_WEB", "GREP_SEARCH", "GENERIC"}:
        return [_event(sequence, raw, event_type, kind="tool_result", role="tool",
                       content=_text(raw.get("content")), timestamp=timestamp, native_sequence=step)]
    return [_event(sequence, raw, event_type, timestamp=timestamp, native_sequence=step)]


def _hermes_events(data: Mapping[str, Any]) -> tuple[list[NativeEvent], tuple[str, ...], tuple[str, ...]]:
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise NativeDecodeError("hermes: messages must be an array")
    events: list[NativeEvent] = []
    issues: list[str] = []
    unsupported: set[str] = set()
    for sequence, message in enumerate(messages, 1):
        if not isinstance(message, Mapping):
            issues.append(f"record:{sequence}:invalid_message")
            continue
        role = message.get("role") if isinstance(message.get("role"), str) else None
        norm = (role or "").lower()
        raw_type = "hermes.message." + (norm or "unknown")
        if norm not in {"user", "assistant", "tool", "system", "session_meta"}:
            unsupported.add(raw_type)
            events.append(_event(sequence, message, raw_type, kind="unsupported"))
            continue
        kind = {"user": "user", "assistant": "assistant", "tool": "tool_result"}.get(norm, "meta")
        timestamp = data.get("last_updated") or data.get("session_start")
        events.append(_event(sequence, message, raw_type, kind=kind, role=role,
                             content=_text(message.get("content")),
                             event_id=message.get("tool_call_id"), timestamp=timestamp))
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            for index, call in enumerate(calls):
                if not isinstance(call, Mapping):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                args = fn.get("arguments")
                events.append(_event(sequence, message, raw_type, kind="tool_call", role="assistant",
                                     name=fn.get("name"), content=args if isinstance(args, str) else None,
                                     event_id=call.get("id"), timestamp=timestamp, native_sequence=index))
    return events, tuple(issues), tuple(sorted(unsupported))


def decode_agent_native_bytes(agent: str, payload: bytes) -> NativeDecodeResult:
    """Decode one already-copied native artifact without any store discovery."""
    if agent not in SUPPORTED_AGENT_TYPES:
        raise NativeDecodeError(f"unsupported agent family: {agent}")
    if not isinstance(payload, bytes) or not payload:
        raise NativeDecodeError("native artifact must be non-empty bytes")
    if len(payload) > MAX_INPUT_BYTES:
        raise NativeDecodeError("native artifact exceeds the byte limit")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NativeDecodeError("native artifact is not UTF-8") from exc

    if agent == "hermes":
        data = _strict_json(text, "hermes")
        if not isinstance(data, Mapping):
            raise NativeDecodeError("hermes: expected a session object")
        session_id = data.get("session_id")
        native_id = data.get("id")
        if ((session_id is not None and (not isinstance(session_id, str) or not session_id))
                or (native_id is not None and (not isinstance(native_id, str) or not native_id))
                or (session_id is not None and native_id is not None and session_id != native_id)):
            raise NativeDecodeError("hermes: session identity fields are invalid or conflict")
        session_id = session_id or native_id
        if not session_id:
            raise NativeDecodeError("hermes: expected session_id or id and messages fields")
        events, issues, unsupported = _hermes_events(data)
        status = "invalid_capture" if issues else "decoder_unsupported" if unsupported else "complete"
        return NativeDecodeResult(agent, status, "hermes-session-json-v1",
                                  session_id, None, data, tuple(events), unsupported, issues)

    lines = text.splitlines()
    if not lines or len(lines) > MAX_RECORDS:
        raise NativeDecodeError("native JSONL record count is empty or exceeds limit")
    parsed: list[Mapping[str, Any]] = []
    issues: list[str] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
            issues.append(f"line:{line_number}:record_too_large")
            continue
        value = _strict_json(line, f"line:{line_number}")
        if not isinstance(value, Mapping):
            issues.append(f"line:{line_number}:record_not_object")
            continue
        parsed.append(value)
    if not parsed:
        raise NativeDecodeError("native JSONL has no object records")

    if agent in {"pi", "openclaw"}:
        expected_header = "session"
        if parsed[0].get("type") != expected_header:
            issues.append("header_missing_or_invalid")
        header = parsed[0] if parsed[0].get("type") == expected_header else None
        decoder = lambda i, row: _pi_openclaw_event(i, row, agent=agent)
        session_id = header.get("id") if header and isinstance(header.get("id"), str) else None
        version = header.get("version") if header else None
        format_id = f"{agent}-session-jsonl-v1"
    elif agent == "kimi":
        header = parsed[0] if parsed[0].get("type") == "metadata" else None
        if header is None or not isinstance(header.get("protocol_version"), str):
            issues.append("header_missing_or_invalid")
        decoder = lambda i, row: _kimi_event(i, row)
        session_id = None
        version = header.get("protocol_version") if header else None
        format_id = "kimi-wire-jsonl-v1"
    elif agent == "copilot":
        header = parsed[0] if parsed[0].get("type") == "session.start" else None
        if header is None:
            issues.append("header_missing_or_invalid")
        decoder = lambda i, row: _copilot_event(i, row)
        start_data = header.get("data") if header and isinstance(header.get("data"), Mapping) else {}
        session_id = start_data.get("sessionId") if isinstance(start_data.get("sessionId"), str) else None
        version = start_data.get("copilotVersion") if isinstance(start_data.get("copilotVersion"), str) else None
        format_id = "copilot-session-events-jsonl-v1"
    else:
        header = None
        decoder = lambda i, row: _antigravity_event(i, row)
        session_id = None
        version = None
        format_id = "antigravity-cli-transcript-jsonl-v1"

    events: list[NativeEvent] = []
    unsupported: set[str] = set()
    copilot_requested_calls: set[str] = set()
    for sequence, row in enumerate(parsed, 1):
        decoded = decoder(sequence, row)
        if agent == "copilot":
            event_type = row.get("type")
            data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
            if event_type == "assistant.message":
                # Copilot records a tool request in assistant.message and then
                # repeats its execution details in tool.execution_start. The
                # request is the canonical call; execution_start is a lifecycle
                # record unless it backfills a request absent from the log.
                copilot_requested_calls.update(
                    event.event_id for event in decoded
                    if event.kind == "tool_call" and isinstance(event.event_id, str) and event.event_id
                )
            elif event_type == "tool.execution_start":
                call_id = data.get("toolCallId")
                if isinstance(call_id, str) and call_id:
                    if call_id in copilot_requested_calls:
                        decoded = [
                            NativeEvent(
                                sequence=event.sequence,
                                native_sequence=event.native_sequence,
                                event_type=event.event_type,
                                event_id=event.event_id,
                                parent_id=event.parent_id,
                                timestamp=event.timestamp,
                                kind="meta" if event.kind == "tool_call" else event.kind,
                                role=event.role,
                                name=event.name,
                                content=event.content,
                                raw=event.raw,
                            )
                            for event in decoded
                        ]
                    else:
                        copilot_requested_calls.add(call_id)
        events.extend(decoded)
        unsupported.update(event.event_type for event in decoded if event.kind == "unsupported")
    status = "invalid_capture" if issues else "decoder_unsupported" if unsupported else "complete"
    return NativeDecodeResult(agent, status, format_id, session_id, version, header,
                              tuple(events), tuple(sorted(unsupported)), tuple(issues))
