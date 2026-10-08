"""Synthetic ACP JSON graph checks; no live Cursor profile is opened."""
import hashlib
import json
import sqlite3

import pytest

from session_bench import cursor_cli_native as decoder


def field(number, payload):
    assert len(payload) < 128
    return bytes([(number << 3) | 2, len(payload)]) + payload


def make_store(path, *, duplicate=False, missing=False, mismatch=False, contextual=True, large=False):
    path.mkdir()
    blobs = {}
    def add(raw):
        identity = hashlib.sha256(raw).hexdigest()
        blobs[identity] = raw
        return bytes.fromhex(identity)
    prompt = "Submit exactly this prompt."
    first = add(json.dumps({"role": "user", "content": "Earlier context only."}).encode())
    submitted = add(json.dumps({"role": "user", "content": [{"type": "text", "text": f"Context prefix\n{prompt}\nContext suffix"}]}).encode())
    assistant = add(json.dumps({"role": "assistant", "content": [{"type": "reasoning", "text": "thinking"}, {"type": "tool-call", "toolCallId": "x", "toolName": "read", "args": {"p": "a"}}], "providerOptions": {"opaque": True}}).encode())
    tool = add(json.dumps({"role": "tool", "content": [{"type": "tool-result", "toolCallId": "x", "result": {"ok": True}}]}).encode())
    user = add(field(1, prompt.encode()))
    turn = add(field(1, field(1, user)))
    refs = [first, submitted, assistant, tool] if contextual else [submitted, assistant, tool]
    if duplicate: refs.append(submitted)
    root = add(b"".join(field(1, ref) for ref in refs) + field(8, turn) + field(5, b"opaque")).hex()
    add(b"unlinked historical blob")
    if large: blobs[submitted.hex()] = b"x" * (decoder.MAX_BLOB_BYTES + 1)
    if mismatch: blobs[submitted.hex()] = b"changed"
    if missing: del blobs[submitted.hex()]
    con = sqlite3.connect(path / "store.db")
    con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)")
    con.execute("INSERT INTO meta VALUES ('0',?)", (json.dumps({"agentId": "session-a", "latestRootBlobId": root}).encode().hex(),))
    con.executemany("INSERT INTO blobs VALUES (?,?)", blobs.items())
    con.commit();con.close()
    (path / "meta.json").write_text('{"schemaVersion":1}')
    return prompt


def test_ordered_json_and_turn_bind_only_submitted_prompt(tmp_path):
    path = tmp_path / "acp"
    prompt = make_store(path)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    result = decoder.decode_cursor_cli_acp(path, session_id="session-a", submitted_prompts=[prompt])
    assert [row["role"] for row in result["messages"]] == ["user", "user", "assistant", "tool"]
    assert result["submitted_prompt_bindings"][0]["message_indices"] == [1]
    assert result["submitted_prompt_bindings"][0]["turn_indices"] == [0]
    assert result["submitted_prompt_bindings"][0]["status"] == "bound"
    assert result["messages"][2]["message"]["content"][0]["type"] == "reasoning"
    assert result["messages"][2]["unsupported_json_paths"] == ["message.providerOptions:opaque"]
    assert result["unsupported_root_fields"][0]["field_number"] == 5
    assert result["unlinked_blob_count"] == 1
    assert not result["complete_native_root"] and not result["density_qualified"] and not result["usage_absence_proven"]
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before


def test_user_role_alone_does_not_bind_workload(tmp_path):
    path = tmp_path / "acp"
    make_store(path)
    result = decoder.decode_cursor_cli_acp(path, session_id="session-a", submitted_prompts=["Earlier context only."])
    assert result["submitted_prompt_bindings"][0]["status"] == "unresolved"
    assert result["submitted_prompt_bindings"][0]["message_indices"] == [0]
    assert result["submitted_prompt_bindings"][0]["turn_indices"] == []


@pytest.mark.parametrize("mutation,match", [("duplicate", "duplicate.*reference"), ("missing", "missing blob"), ("mismatch", "hash mismatch"), ("large", "byte limit")])
def test_graph_or_blob_violation_fails_closed(tmp_path, mutation, match):
    path = tmp_path / "acp"
    prompt = make_store(path, **{mutation: True})
    with pytest.raises(ValueError, match=match):
        decoder.decode_cursor_cli_acp(path, session_id="session-a", submitted_prompts=[prompt])


def test_json_duplicate_key_and_identity_rejected(tmp_path):
    path = tmp_path / "acp"
    make_store(path)
    with pytest.raises(ValueError, match="identity"):
        decoder.decode_cursor_cli_acp(path, session_id="wrong")
    with pytest.raises(ValueError, match="duplicate"):
        decoder._json(b'{"role":"user","role":"tool"}')


def test_json_depth_and_workload_byte_bounds(tmp_path):
    path = tmp_path / "acp"
    make_store(path)
    with pytest.raises(ValueError, match="depth"):
        decoder._json(("[" * (decoder.MAX_JSON_DEPTH + 1) + "0" + "]" * (decoder.MAX_JSON_DEPTH + 1)).encode())
    with pytest.raises(ValueError, match="byte limit"):
        decoder.decode_cursor_cli_acp(path, session_id="session-a", submitted_prompts=["x" * (decoder.MAX_BLOB_BYTES + 1)])
