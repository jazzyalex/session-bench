import json

import pytest

from session_bench.adapters.agent_session_native_decoder import (
    NativeDecodeError,
    decode_agent_native_bytes,
)


def jsonl(*rows):
    return ("\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n").encode()


@pytest.mark.parametrize(
    ("agent", "rows", "session_id", "version", "expected_kinds"),
    [
        (
            "pi",
            [
                {"type": "session", "id": "pi-fixture", "version": 3, "timestamp": "t0"},
                {"type": "message", "id": "a1", "parentId": "u1", "timestamp": "t1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Ran a listing."}, {"type": "toolCall", "id": "c1", "name": "ls", "arguments": {"path": "."}}]}},
                {"type": "message", "id": "r1", "parentId": "a1", "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "ls", "content": [{"type": "text", "text": "a.txt"}]}},
            ],
            "pi-fixture", 3, ["meta", "assistant", "tool_call", "tool_result"],
        ),
        (
            "openclaw",
            [
                {"type": "session", "id": "oc-fixture", "version": 3},
                {"type": "message", "id": "a1", "message": {"role": "assistant", "content": [{"type": "toolCall", "id": "c1", "name": "shell_exec", "input": {"command": "ls"}}]}},
            ],
            "oc-fixture", 3, ["meta", "tool_call"],
        ),
        (
            "kimi",
            [
                {"type": "metadata", "protocol_version": "1.4", "created_at": 100},
                {"type": "context.append_message", "time": 101, "message": {"role": "user", "content": [{"type": "text", "text": "List files"}]}},
                {"type": "context.append_loop_event", "time": 102, "event": {"type": "tool.call", "toolCallId": "k1", "name": "Bash", "args": {"command": "ls"}}},
                {"type": "context.append_loop_event", "time": 103, "event": {"type": "tool.result", "toolCallId": "k1", "content": "a.txt"}},
            ],
            None, "1.4", ["meta", "user", "tool_call", "tool_result"],
        ),
        (
            "copilot",
            [
                {"type": "session.start", "id": "e1", "data": {"sessionId": "cp-fixture", "copilotVersion": "1.0.89"}},
                {"type": "assistant.message", "id": "e2", "data": {"content": "Listing.", "messageId": "m1", "toolRequests": [{"toolCallId": "c1", "name": "shell", "arguments": "{\\\"command\\\":\\\"ls\\\"}"}]}},
                {"type": "tool.execution_complete", "id": "e3", "data": {"toolCallId": "c1", "success": True, "result": {"content": "a.txt"}}},
            ],
            "cp-fixture", "1.0.89", ["meta", "assistant", "tool_call", "tool_result"],
        ),
        (
            "antigravity",
            [
                {"step_index": 0, "type": "USER_INPUT", "created_at": "t0", "content": "List files."},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "created_at": "t1", "thinking": "[redacted]", "tool_calls": [{"name": "list_dir", "args": "{\"DirectoryPath\":\"/tmp/work\"}"}]},
                {"step_index": 2, "type": "LIST_DIRECTORY", "created_at": "t2", "content": "a.txt"},
            ],
            None, None, ["user", "assistant", "tool_call", "tool_result"],
        ),
    ],
)
def test_native_families_normalize_fixture_shapes_without_losing_raw_rows(
    agent, rows, session_id, version, expected_kinds,
):
    result = decode_agent_native_bytes(agent, jsonl(*rows))
    assert result.status == "complete"
    assert result.session_id == session_id
    assert result.version == version
    assert [event.kind for event in result.events] == expected_kinds
    assert all(event.raw for event in result.events)
    assert [event.sequence for event in result.events] == sorted(event.sequence for event in result.events)


def test_hermes_json_message_array_and_tool_calls_are_preserved():
    payload = json.dumps({
        "session_id": "hermes-fixture",
        "model": "fixture-model",
        "session_start": "t0",
        "messages": [
            {"role": "user", "content": "List files."},
            {"role": "assistant", "content": "Listing.", "tool_calls": [{"id": "h1", "function": {"name": "shell", "arguments": "{\\\"command\\\":\\\"ls\\\"}"}}]},
            {"role": "tool", "tool_call_id": "h1", "content": "a.txt"},
        ],
    }).encode()
    result = decode_agent_native_bytes("hermes", payload)
    assert result.status == "complete"
    assert result.session_id == "hermes-fixture"
    assert [event.kind for event in result.events] == ["user", "assistant", "tool_call", "tool_result"]
    assert result.events[2].name == "shell"
    assert result.events[3].event_id == "h1"


def test_hermes_installed_export_id_alias_is_used_and_conflicts_are_rejected():
    native = json.dumps({"id": "hermes-native-export", "messages": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]}).encode()
    result = decode_agent_native_bytes("hermes", native)
    assert result.status == "complete"
    assert result.session_id == "hermes-native-export"

    conflict = json.dumps({"id": "native-id", "session_id": "other-id", "messages": []}).encode()
    with pytest.raises(NativeDecodeError, match="identity fields.*conflict"):
        decode_agent_native_bytes("hermes", conflict)


def test_unknown_family_event_is_retained_and_marks_decoder_unsupported():
    rows = [
        {"type": "session", "id": "pi-unknown", "version": 3},
        {"type": "message", "id": "m1", "message": {"role": "assistant", "content": [{"type": "newBlock", "value": "kept"}]}},
        {"type": "future.event", "opaque": {"keep": True}},
    ]
    result = decode_agent_native_bytes("pi", jsonl(*rows))
    assert result.status == "decoder_unsupported"
    assert result.unsupported_types == ("future.event", "message:newblock")
    assert len(result.events) == 3
    assert result.events[-1].raw["opaque"] == {"keep": True}


def test_missing_session_identity_is_not_fabricated_for_kimi_or_antigravity():
    kimi = decode_agent_native_bytes("kimi", jsonl({"type": "metadata", "protocol_version": "1.4"}))
    antigravity = decode_agent_native_bytes("antigravity", jsonl({"type": "USER_INPUT", "content": "hi"}))
    assert kimi.session_id is None
    assert antigravity.session_id is None


def test_copilot_request_is_canonical_call_and_execution_start_is_lifecycle():
    result = decode_agent_native_bytes("copilot", jsonl(
        {"type": "session.start", "id": "e0", "data": {"sessionId": "cp-fixture", "copilotVersion": "1.0.89"}},
        {"type": "assistant.message", "id": "e1", "data": {
            "messageId": "m1", "content": "", "toolRequests": [
                {"toolCallId": "c1", "name": "bash", "arguments": {"command": "python3 bench_check.py inspect"}},
            ],
        }},
        {"type": "tool.execution_start", "id": "e2", "parentId": "e1", "data": {
            "toolCallId": "c1", "toolName": "bash", "arguments": {"command": "python3 bench_check.py inspect"},
        }},
        {"type": "tool.execution_complete", "id": "e3", "parentId": "e2", "data": {
            "toolCallId": "c1", "success": True, "result": {"content": "inspected"},
        }},
    ))
    calls = [event for event in result.events if event.kind == "tool_call"]
    starts = [event for event in result.events if event.event_type == "tool.execution_start"]
    results = [event for event in result.events if event.kind == "tool_result"]
    assert len(calls) == len(starts) == len(results) == 1
    assert calls[0].event_id == starts[0].event_id == results[0].event_id == "c1"
    assert starts[0].kind == "meta"
    assert calls[0].content == '{"command":"python3 bench_check.py inspect"}'


def test_copilot_execution_start_backfills_missing_tool_request():
    result = decode_agent_native_bytes("copilot", jsonl(
        {"type": "session.start", "id": "e0", "data": {"sessionId": "cp-fixture", "copilotVersion": "1.0.89"}},
        {"type": "tool.execution_start", "id": "e1", "data": {
            "toolCallId": "c-missing-request", "toolName": "bash", "arguments": {"command": "true"},
        }},
    ))
    calls = [event for event in result.events if event.kind == "tool_call"]
    assert len(calls) == 1
    assert calls[0].event_id == "c-missing-request"


def test_copilot_permission_lifecycle_is_retained_as_metadata():
    result = decode_agent_native_bytes("copilot", jsonl(
        {"type": "session.start", "id": "e0", "data": {"sessionId": "cp-fixture", "copilotVersion": "1.0.89"}},
        {"type": "permission.requested", "id": "e1", "data": {"requestId": "p1", "kind": "tool"}},
        {"type": "permission.completed", "id": "e2", "data": {"requestId": "p1", "decision": "approved"}},
    ))
    assert result.status == "complete"
    assert [event.kind for event in result.events] == ["meta", "meta", "meta"]
    assert [event.event_type for event in result.events[1:]] == ["permission.requested", "permission.completed"]
    assert result.events[-1].raw["data"]["decision"] == "approved"


@pytest.mark.parametrize("payload", [b'{"type":"session","id":"x","type":"again"}', b"not-json\n"])
def test_malformed_or_duplicate_json_is_rejected(payload):
    with pytest.raises(NativeDecodeError):
        decode_agent_native_bytes("pi", payload)


def test_hermes_requires_native_session_identity_and_message_array():
    with pytest.raises(NativeDecodeError, match="expected session_id or id"):
        decode_agent_native_bytes("hermes", b'{"messages":[]}')
    with pytest.raises(NativeDecodeError, match="messages must be an array"):
        decode_agent_native_bytes("hermes", b'{"id":"x"}')
