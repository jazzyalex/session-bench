"""Personal context is removed from a Claude Desktop family at equal byte length."""

from __future__ import annotations

import json

import pytest

from scripts.sanitize_claude_desktop_score_packets import (
    alias_bridge_sessions, bridge_session_aliases, redact_desktop_metadata, redact_desktop_transcript, same_shape,
)


def _line(record: dict) -> bytes:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _transcript() -> bytes:
    return b"\n".join(_line(record) for record in (
        {"type": "attachment", "timestamp": "2026-09-15T02:27:56.690Z", "sessionId": "s",
         "attachment": {"type": "skill_listing", "content": "- tennis-usta-auth: sign in", "names": ["tennis-usta-auth"]},
         "rendered": [{"content": "<system-reminder>\nprivate rule: never touch \"x\"\n"}]},
        {"type": "attachment", "sessionId": "s", "attachment": {"type": "instructions", "files": [{"path": "/Users/someone/.claude/CLAUDE.md", "content": "règle privée"}]}},
        {"type": "system", "subtype": "stop_hook_summary", "hookInfos": [{"command": "/Users/someone/.config/private-hook", "durationMs": 20}], "sessionId": "s"},
        {"type": "bridge-session", "sessionId": "s", "bridgeSessionId": "cse_1", "ownerAccountUuid": "386fa668-aaaa", "ownerOrganizationUuid": "89ec7e97-bbbb"},
        {"type": "atis-latch", "atis": "v1.opaque.token", "sessionId": "s"},
        {"type": "user", "sessionId": "s", "message": {"role": "user", "content": "The synthetic task prompt."}},
        {"type": "assistant", "sessionId": "s", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    )) + b"\n"


def _metadata() -> bytes:
    return _line({"sessionId": "local_1", "cliSessionId": "s", "cwd": "/synthetic", "originCwd": "/synthetic", "createdAt": 1,
                  "title": "Session-Bench survival-v1 R1", "bridgeSessionIds": ["session_1"],
                  "enabledMcpTools": {"private-connector:read_mail-0123456789": True, "private-connector:send_mail-0123456789": True},
                  "remoteMcpServersConfig": [{"name": "Private Mail", "url": "https://mail.example/mcp", "tools": [{"name": "send"}]}],
                  "spawnSeed": {"note": "private seed"}})


def test_personal_context_records_are_blanked_and_the_conversation_is_kept() -> None:
    redacted, count = redact_desktop_transcript(_transcript())

    for private in (b"tennis", b"private rule", b"priv", b"private-hook", b"386fa668", b"89ec7e97", b"opaque"):
        assert private not in redacted
    records = [json.loads(line) for line in redacted.splitlines()]
    assert records[0]["attachment"]["type"] == "skill_listing" and records[0]["timestamp"] == "2026-09-15T02:27:56.690Z"
    assert records[3]["bridgeSessionId"] == "cse_1"
    assert records[5]["message"]["content"] == "The synthetic task prompt."
    assert records[6]["message"]["content"][0]["text"] == "Done."
    assert count > 0


def test_every_transcript_line_keeps_its_byte_length_and_keys() -> None:
    original = _transcript()
    redacted, _ = redact_desktop_transcript(original)

    assert [len(line) for line in redacted.splitlines()] == [len(line) for line in original.splitlines()]
    for before, after in zip(original.splitlines(), redacted.splitlines()):
        assert set(json.loads(after)) == set(json.loads(before))


def test_metadata_keeps_identity_fields_and_blanks_connector_names_and_config() -> None:
    original = _metadata()
    redacted, count = redact_desktop_metadata(original)
    value = json.loads(redacted)

    assert len(redacted) == len(original) and count > 0
    for private in (b"private-connector", b"Private Mail", b"mail.example", b"private seed"):
        assert private not in redacted
    assert {key: value[key] for key in ("sessionId", "cliSessionId", "cwd", "originCwd", "createdAt", "title", "bridgeSessionIds")} == {
        "sessionId": "local_1", "cliSessionId": "s", "cwd": "/synthetic", "originCwd": "/synthetic", "createdAt": 1,
        "title": "Session-Bench survival-v1 R1", "bridgeSessionIds": ["session_1"]}
    assert len(value["enabledMcpTools"]) == 2 and set(value["enabledMcpTools"].values()) == {True}
    assert set(value["remoteMcpServersConfig"][0]) == {"name", "url", "tools"}


_SCHEMA = {"type": "object", "properties": {"calendarId": {"type": "string"}, "q": {"type": "string"}, "id": {"type": "string"},
                                            "x-vendor-enum-descriptions": ["a", "b"]},
           "required": ["calendarId"], "vendor.example/hidden_in_chat": True}


def _connector_metadata(**extra) -> bytes:
    tools = [{"name": "create_event", "description": "Create an event", "inputSchema": _SCHEMA},
             {"name": "list_labels", "description": "List labels", "inputSchema": {"type": "object", "properties": {"labelIds": {"type": "array"}}}}]
    return _line({"sessionId": "local_1", "cliSessionId": "s", "title": "Session-Bench survival-v1 R1", "bridgeSessionIds": ["session_1"],
                  "enabledMcpTools": {"a": True, "b": True, "private-connector:send_mail-0123456789": True},
                  "remoteMcpServersConfig": [{"uuid": "11111111-2222", "name": "Private Calendar", "url": "https://calendar.example/mcp",
                                              "tools": tools, "authState": {"scopes": ["calendar.readonly"]}}],
                  "toolSurfaceSnapshot": {"familyHashes": {"builtin": "0123456789abcdef"}, "appVersion": "1.2.3"}, **extra})


def _keys_below(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield path + (key,), key
            yield from _keys_below(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _keys_below(item, path + (index,))


def test_no_connector_schema_key_stays_readable_in_the_metadata() -> None:
    original = _connector_metadata()
    redacted, _ = redact_desktop_metadata(original)
    before, after = json.loads(original), json.loads(redacted)

    assert len(redacted) == len(original) and same_shape(before, after)
    private = {key for path, key in _keys_below(before["remoteMcpServersConfig"]) if len(path) > 2 or key not in {"uuid", "name", "url", "tools"}}
    public = {key for path, key in _keys_below(after["remoteMcpServersConfig"]) if len(path) > 2 or key not in {"uuid", "name", "url", "tools"}}
    assert {"calendarId", "labelIds", "q", "id", "inputSchema", "properties", "x-vendor-enum-descriptions",
            "vendor.example/hidden_in_chat", "authState", "scopes"} <= private
    assert not private & public
    assert set(after["remoteMcpServersConfig"][0]) >= {"uuid", "name", "url", "tools"}
    for word in (b"calendar", b"Calendar", b"label", b"inputSchema", b"hidden_in_chat", b"authState", b"readonly", b"send_mail"):
        assert word not in redacted
    # Vendor-defined field names outside the connector configuration stay readable.
    assert after["toolSurfaceSnapshot"].keys() == before["toolSurfaceSnapshot"].keys()
    assert after["toolSurfaceSnapshot"]["familyHashes"].keys() == {"builtin"}


def test_blanked_sibling_keys_stay_distinct_at_their_exact_byte_length() -> None:
    original = _connector_metadata()
    before, after = json.loads(original), json.loads(redact_desktop_metadata(original)[0])

    def pairs(left, right):
        if isinstance(left, dict):
            assert len(set(right)) == len(right) == len(left)
            for (old, value), (new, changed) in zip(left.items(), right.items()):
                assert len(old.encode()) == len(new.encode())
                yield from pairs(value, changed)
        elif isinstance(left, list):
            for value, changed in zip(left, right):
                yield from pairs(value, changed)
        yield left

    list(pairs(before, after))
    properties = after["remoteMcpServersConfig"][0]["tools"][0]
    assert sorted(len(key) for key in after["enabledMcpTools"]) == [1, 1, 38] and len(set(after["enabledMcpTools"])) == 3
    assert len(properties) == 3


def test_operator_specific_keys_in_other_metadata_fields_are_blanked() -> None:
    original = _connector_metadata(spawnSeed={"privateAgent": {"mode": "x"}}, alwaysAllowedReasons=[{"private-tool": "granted"}],
                                   sessionPermissionUpdates=[{"rules": [{"toolName": "private-tool"}]}])
    redacted, _ = redact_desktop_metadata(original)

    assert len(redacted) == len(original) and same_shape(json.loads(original), json.loads(redacted))
    for word in (b"privateAgent", b"mode", b"private-tool", b"rules", b"toolName", b"granted"):
        assert word not in redacted


def test_too_many_short_sibling_keys_fail_loudly() -> None:
    crowded = {chr(code): True for code in range(0x23, 0x7F) if chr(code) != "\\"}
    original = _line({"sessionId": "local_1", "enabledMcpTools": crowded})

    with pytest.raises(ValueError, match="distinct equal-length alias"):
        redact_desktop_metadata(original)


def test_shape_check_sees_merged_keys_lost_items_and_changed_types() -> None:
    value = {"a": [1, {"b": "x", "c": None}], "d": True}

    assert same_shape(value, {"k": [2, {"m": "y", "n": None}], "d": False})
    assert not same_shape(value, {"a": [1, {"b": "x"}], "d": True})
    assert not same_shape(value, {"a": [1], "d": True})
    assert not same_shape(value, {"a": [1, {"b": "x", "c": 0}], "d": True})
    assert not same_shape(value, {"a": [1, {"b": "x", "c": None}], "d": 1})


def test_bridge_session_identifiers_get_one_equal_length_alias_in_every_file() -> None:
    first, second = "01AbCdEfGhIjKlMnOpQrSt12", "01ZyXwVuTsRqPoNmLkJiHg34"
    documents = {
        "a/native/session.jsonl": _line({"type": "bridge-session", "sessionId": "s", "bridgeSessionId": f"cse_{first}"}) + b"\n",
        "a/inputs/native-family/desktop/session.json": _line({"bridgeSessionIds": [f"session_{first}"]}),
        "a/inputs/capture-assertion.json": _line({"bridge_session_id": f"session_{first}", "tool": "mcp__session_transcripts"}),
        "b/inputs/family-validation.json": _line({"bridge_session_ids": [f"session_{second}"]}),
    }
    aliases = bridge_session_aliases(documents)
    public = {name: alias_bridge_sessions(data, aliases) for name, data in documents.items()}

    assert len(aliases) == 2 and len(set(aliases.values())) == 2
    assert all(len(public[name]) == len(data) for name, data in documents.items())
    assert not any(secret.encode() in data for data in public.values() for secret in (first, second))
    alias = aliases[first.encode()].decode()
    assert json.loads(public["a/native/session.jsonl"])["bridgeSessionId"] == f"cse_{alias}"
    assert json.loads(public["a/inputs/native-family/desktop/session.json"])["bridgeSessionIds"] == [f"session_{alias}"]
    assert json.loads(public["a/inputs/capture-assertion.json"]) == {"bridge_session_id": f"session_{alias}", "tool": "mcp__session_transcripts"}
