"""Focused controls for the copied OpenCode SQLite/WAL decoder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from session_bench.adapters.opencode_decoder import (
    OPENCODE_BUNDLE_FILES,
    OpenCodeDecoderError,
    decode_opencode_bundle,
)
import session_bench.adapters.opencode_decoder as decoder


ROOT = Path(__file__).resolve().parents[1]
COPIED_BUNDLE = ROOT / "artifacts" / "survival-v1-runs" / "opencode-cli-setup-2" / "native-private"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_sanitized_bundle(root: Path, *, malformed_part: bool = False) -> tuple[Path, sqlite3.Connection]:
    root.mkdir()
    database = root / "opencode.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            version TEXT NOT NULL,
            model TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            tokens_reasoning INTEGER DEFAULT 0,
            tokens_cache_read INTEGER DEFAULT 0,
            tokens_cache_write INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE credential (id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    session_id = "ses_fixture"
    connection.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, "/fixture/project", "1.18.30", "fixture/model", 1, 9, 3, 2, 1, 4, 0),
    )
    connection.execute("INSERT INTO credential VALUES (?, ?)", ("secret", "must-not-escape"))
    messages = [
        ("msg_user", "user", None, None, "Prompt with SB_SURVIVAL_V1_CONTEXT_fixture"),
        ("msg_assistant", "assistant", "fixture-model", "stop", "Response SB_SURVIVAL_V1_RESPONSE_fixture"),
    ]
    for index, (message_id, role, model, finish, text) in enumerate(messages, 1):
        payload = {"role": role, "time": {"created": index}, "parentID": "msg_user" if role == "assistant" else None}
        if model:
            payload.update({"modelID": model, "providerID": "fixture", "finish": finish, "tokens": {"input": 3, "output": 2, "reasoning": 1, "cache": {"read": 4, "write": 0}}})
        connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)", (message_id, session_id, index, index, json.dumps(payload)))
        part_data = {"type": "text", "text": text}
        connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", (f"part_{message_id}", message_id, session_id, index, index, json.dumps(part_data)))
    tool_payload = {"type": "tool", "callID": "call_1", "tool": "apply_patch", "state": {"status": "completed", "input": {"patchText": "*** Update File: fixture_project/checkout.py"}, "output": "ok"}}
    if malformed_part:
        raw = "{bad-json"
    else:
        raw = json.dumps(tool_payload)
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_tool", "msg_assistant", session_id, 5, 5, raw))
    connection.commit()
    # Keep the writer open while the decoder takes its copy.  This verifies
    # that the decoder replays the WAL/SHM pair rather than relying on a
    # checkpointed main database.
    return root, connection


def test_copied_setup_bundle_recovers_native_facts_without_private_metadata() -> None:
    before = {name: _digest(COPIED_BUNDLE / name) for name in OPENCODE_BUNDLE_FILES}
    decoded = decode_opencode_bundle(COPIED_BUNDLE)
    after = {name: _digest(COPIED_BUNDLE / name) for name in OPENCODE_BUNDLE_FILES}

    assert before == after
    assert decoded["supported"] is True
    assert decoded["format"] == "opencode-sqlite-v1"
    assert decoded["facts"]["turns"]["submitted"] == 2
    assert decoded["facts"]["turns"]["responses"] == 2
    assert decoded["facts"]["actions"] == {"count": 6, "completed_results": 6}
    assert decoded["facts"]["changed_files"]["paths"] == ["fixture_project/checkout.py"]
    assert decoded["facts"]["reconciliation"]["matches_session_totals"] is True
    assert decoded["metrics"]["work.visible_responses"]["decoded_eligible"] == 2
    assert decoded["metrics"]["work.visible_responses"]["state"] == "unresolved"
    assert decoded["metrics"]["portable.complete_root"]["availability"] == "unknown"
    assert decoded["metrics"]["portable.companions"]["availability"] == "present"
    assert set(decoded["metrics"]) == {
        "work.submitted_turns", "work.visible_responses", "work.actions", "work.results", "work.changed_files",
        "causal.action_result", "causal.turn_response", "revision.r1", "revision.r2", "revision.r1_r2_order", "revision.final_after_r2",
        "attribution.model_config", "attribution.usage", "attribution.token_semantics", "attribution.reconciliation",
        "portable.complete_root", "portable.companions", "portable.isolated_decode", "portable.canonical_equality",
    }
    encoded = json.dumps(decoded, ensure_ascii=False)
    assert "reasoningEncryptedContent" not in encoded
    assert "must-not-escape" not in encoded


def test_decoder_requires_the_copied_wal_shm_boundary(tmp_path: Path) -> None:
    bundle, connection = _create_sanitized_bundle(tmp_path / "bundle")
    (bundle / "opencode.db-shm").unlink()

    with pytest.raises(OpenCodeDecoderError, match="exactly db/WAL/SHM"):
        decode_opencode_bundle(bundle)
    connection.close()


def test_decoder_rejects_a_bundle_that_changes_after_hash_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offline copy must match the exact bytes inspected before cloning."""

    source = tmp_path / "bundle"
    source.mkdir()
    for name in OPENCODE_BUNDLE_FILES:
        (source / name).write_bytes((COPIED_BUNDLE / name).read_bytes())
    original_copy = decoder._copy_bundle

    def mutate_before_copy(bundle: Path, target: Path, *, declared: object):
        path = bundle / "opencode.db"
        path.write_bytes(path.read_bytes() + b"changed-after-inspection")
        return original_copy(bundle, target, declared=declared)  # type: ignore[arg-type]

    monkeypatch.setattr(decoder, "_copy_bundle", mutate_before_copy)
    with pytest.raises(OpenCodeDecoderError, match="changed after bundle inspection"):
        decode_opencode_bundle(source)


def test_decoder_keeps_malformed_logical_rows_unknown_without_fabricating(tmp_path: Path) -> None:
    bundle, connection = _create_sanitized_bundle(tmp_path / "bundle", malformed_part=True)
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    assert decoded["supported"] is False
    assert any(item["code"] == "malformed_part" for item in decoded["diagnostics"])
    assert decoded["metrics"]["work.actions"]["state"] == "decoder_unsupported"
    assert decoded["metrics"]["work.actions"]["decoded_eligible"] == 0
    assert decoded["metrics"]["portable.complete_root"]["availability"] == "unknown"


def test_decoder_reads_only_the_explicit_bundle_and_does_not_discover_siblings(tmp_path: Path) -> None:
    bundle, connection = _create_sanitized_bundle(tmp_path / "bundle")
    sibling = tmp_path / "normal-profile"
    sibling.mkdir()
    (sibling / "opencode.db").write_bytes(b"not a database")

    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()
    assert decoded["session_id"] == "ses_fixture"
    assert decoded["bundle"]["path"] == "bundle"
    assert decoded["facts"]["turns"]["submitted"] == 1
    assert not any("normal-profile" in json.dumps(item) for item in decoded["diagnostics"])


def _create_explicit_bundle(root: Path) -> tuple[Path, sqlite3.Connection]:
    root.mkdir()
    database = root / "opencode.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            version TEXT NOT NULL,
            model TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            tokens_reasoning INTEGER DEFAULT 0,
            tokens_cache_read INTEGER DEFAULT 0,
            tokens_cache_write INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )
    session_id = "ses_explicit"
    # Totals match the two visible responses below: input 3+5, output 2+6,
    # reasoning 1+0, cache read 4+1, cache write 0+2.
    connection.execute(
        "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, "/fixture/project", "1.18.30", "fixture/model", 1, 9, 8, 8, 1, 5, 2),
    )
    user_r1 = "Task R1 do checkout work SB_SURVIVAL_V1_CONTEXT_explicit"
    user_r2 = "Correction R2 supersedes delivery only SB_SURVIVAL_V1_CONTEXT_explicit"
    resp_r1 = "Done R1 SB_SURVIVAL_V1_RESPONSE_r1_explicit1"
    resp_r2 = "Done R2 SB_SURVIVAL_V1_RESPONSE_r2_explicit2"
    messages = [
        ("msg_r1", "user", None, None, None, None, user_r1, 1),
        ("msg_resp1", "assistant", "msg_r1", "fixture-model", "fixture", "stop", resp_r1, 2),
        ("msg_r2", "user", None, None, None, None, user_r2, 3),
        ("msg_resp2", "assistant", "msg_r2", "fixture-model", "fixture", "stop", resp_r2, 4),
    ]
    tokens_by_message = {
        "msg_resp1": {"input": 3, "output": 2, "reasoning": 1, "cache": {"read": 4, "write": 0}},
        "msg_resp2": {"input": 5, "output": 6, "reasoning": 0, "cache": {"read": 1, "write": 2}},
    }
    for message_id, role, parent, model, provider, finish, text, order in messages:
        payload: dict = {"role": role, "parentID": parent}
        if role == "assistant":
            payload.update({"modelID": model, "providerID": provider, "finish": finish, "tokens": tokens_by_message[message_id]})
        connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)", (message_id, session_id, order, order, json.dumps(payload)))
        connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", (f"part_{message_id}", message_id, session_id, order, order, json.dumps({"type": "text", "text": text})))
    edit_payload = {"type": "tool", "callID": "call_edit_1", "tool": "edit", "state": {"status": "completed", "input": {"filePath": "fixture_project/checkout.py", "oldString": "OLD-EXACT", "newString": "NEW-EXACT"}, "output": "ok"}}
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_edit", "msg_resp2", session_id, 5, 5, json.dumps(edit_payload)))
    shell_payload = {"type": "tool", "callID": "call_shell_1", "tool": "bash", "state": {"status": "completed", "input": {"command": "python3 bench_check.py final"}, "output": "done", "exit_code": 0}}
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_shell", "msg_resp2", session_id, 6, 6, json.dumps(shell_payload)))
    connection.commit()
    return root, connection


def _create_missing_link_bundle(root: Path) -> tuple[Path, sqlite3.Connection]:
    root.mkdir()
    database = root / "opencode.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            version TEXT NOT NULL,
            model TEXT,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            tokens_reasoning INTEGER DEFAULT 0,
            tokens_cache_read INTEGER DEFAULT 0,
            tokens_cache_write INTEGER DEFAULT 0
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )
    session_id = "ses_missing"
    connection.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (session_id, "/fixture/project", "1.18.30", "fixture/model", 1, 3, 0, 0, 0, 0, 0))
    connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)", ("msg_user1", session_id, 1, 1, json.dumps({"role": "user", "parentID": None})))
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_msg_user1", "msg_user1", session_id, 1, 1, json.dumps({"type": "text", "text": "lonely prompt"})))
    # Orphan assistant has no native parent link.
    connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?)", ("msg_orphan", session_id, 2, 2, json.dumps({"role": "assistant", "parentID": None, "modelID": "fixture-model", "providerID": "fixture", "finish": "stop"})))
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_msg_orphan", "msg_orphan", session_id, 2, 2, json.dumps({"type": "text", "text": "orphan response"})))
    pending = {"type": "tool", "callID": "call_pending", "tool": "bash", "state": {"status": "pending", "input": {"command": "echo hi"}}}
    connection.execute("INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)", ("part_pending", "msg_orphan", session_id, 3, 3, json.dumps(pending)))
    connection.commit()
    return root, connection


def test_explicit_turns_expose_native_ids_text_order_and_revision(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    assert decoded["supported"] is True
    turns = decoded["turns"]
    assert [item["id"] for item in turns] == ["msg_r1", "msg_r2"]
    assert all(item["role"] == "user" for item in turns)
    assert turns[0]["text"] == "Task R1 do checkout work SB_SURVIVAL_V1_CONTEXT_explicit"
    assert turns[1]["text"] == "Correction R2 supersedes delivery only SB_SURVIVAL_V1_CONTEXT_explicit"
    assert turns[0]["revision"] == "r1"
    assert turns[1]["revision"] == "r2"
    assert turns[0]["time_created"] < turns[1]["time_created"]
    assert turns[0]["sequence"] < turns[1]["sequence"]
    assert all("locator" in item and item["locator"]["table"] == "message" for item in turns)


def test_explicit_final_responses_expose_model_config_canary_and_turn(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    responses = decoded["responses"]
    assert [item["id"] for item in responses] == ["msg_resp1", "msg_resp2"]
    assert all(item["role"] == "assistant" and item["status"] == "completed" for item in responses)
    assert responses[0]["model_id"] == "fixture/fixture-model"
    assert responses[0]["configuration"] == "fixture/fixture-model"
    assert responses[1]["model_id"] == "fixture/fixture-model"
    assert responses[1]["configuration"] == "fixture/fixture-model"
    assert responses[0]["text"] == "Done R1 SB_SURVIVAL_V1_RESPONSE_r1_explicit1"
    assert responses[0]["canary"] == "SB_SURVIVAL_V1_RESPONSE_r1_explicit1"
    assert responses[1]["canary"] == "SB_SURVIVAL_V1_RESPONSE_r2_explicit2"
    assert responses[0]["turn_id"] == "msg_r1"
    assert responses[1]["turn_id"] == "msg_r2"
    assert responses[0]["time_created"] < responses[1]["time_created"]


def test_response_canary_ignores_other_survival_markers(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    connection.execute(
        "UPDATE part SET data = ? WHERE id = 'part_msg_resp1'",
        (
            json.dumps(
                {
                    "type": "text",
                    "text": "SB_SURVIVAL_V1_CONTEXT_x SB_SURVIVAL_V1_RUN_y SB_SURVIVAL_V1_RESPONSE_right",
                }
            ),
        ),
    )
    connection.commit()
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    assert decoded["responses"][0]["canary"] == "SB_SURVIVAL_V1_RESPONSE_right"


def test_explicit_per_response_usage_is_response_scoped(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    usage = decoded["usage"]
    assert len(usage) == 2
    by_response = {item["response_id"]: item for item in usage}
    assert set(by_response) == {"msg_resp1", "msg_resp2"}
    assert by_response["msg_resp1"]["turn_id"] == "msg_r1"
    assert by_response["msg_resp2"]["turn_id"] == "msg_r2"
    assert by_response["msg_resp1"]["model_id"] == "fixture/fixture-model"
    assert by_response["msg_resp1"]["configuration"] == "fixture/fixture-model"
    assert by_response["msg_resp1"]["usage"] == {"input_tokens": 3, "output_tokens": 2, "reasoning_tokens": 1, "cache_read_tokens": 4, "cache_write_tokens": 0}
    assert by_response["msg_resp2"]["usage"] == {"input_tokens": 5, "output_tokens": 6, "reasoning_tokens": 0, "cache_read_tokens": 1, "cache_write_tokens": 2}
    assert all("locator" in item and item["locator"]["table"] == "message" for item in usage)
    # Session reconciliation stays an aggregate fact and is not copied per response.
    assert decoded["facts"]["reconciliation"] == {"matches_session_totals": True}
    assert all("matches_session_totals" not in item for item in usage)


def test_explicit_actions_results_and_relations_use_only_native_links(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    actions = {item["id"]: item for item in decoded["actions"]}
    assert set(actions) == {"part_edit", "part_shell"}
    assert actions["part_edit"]["call_id"] == "call_edit_1"
    assert actions["part_edit"]["tool"] == "edit"
    assert actions["part_edit"]["name"] == "edit"
    assert actions["part_edit"]["input"] == {"filePath": "fixture_project/checkout.py", "oldString": "OLD-EXACT", "newString": "NEW-EXACT"}
    assert actions["part_edit"]["status"] == "completed"
    assert actions["part_edit"]["message_id"] == "msg_resp2"
    assert actions["part_edit"]["turn_id"] == "msg_r2"
    assert actions["part_shell"]["turn_id"] == "msg_r2"

    results = {item["id"]: item for item in decoded["results"]}
    assert set(results) == {"part_edit:result", "part_shell:result"}
    assert results["part_edit:result"]["action_id"] == "part_edit"
    assert results["part_edit:result"]["call_id"] == "call_edit_1"
    assert results["part_edit:result"]["status"] == "success"
    assert results["part_edit:result"]["output"] == "ok"
    assert results["part_edit:result"]["turn_id"] == "msg_r2"
    assert results["part_edit:result"]["exit_code"] == 0
    assert results["part_shell:result"]["exit_code"] == 0

    relations = decoded["relations"]
    action_relations = [item for item in relations if item["kind"] == "action_result"]
    turn_relations = [item for item in relations if item["kind"] == "turn_response"]
    assert {(item["from_id"], item["to_id"]) for item in action_relations} == {("part_edit", "part_edit:result"), ("part_shell", "part_shell:result")}
    assert {(item["from_id"], item["to_id"]) for item in turn_relations} == {("msg_r1", "msg_resp1"), ("msg_r2", "msg_resp2")}


def test_explicit_file_changes_keep_recorded_fragments_without_hash_claims(tmp_path: Path) -> None:
    bundle, connection = _create_explicit_bundle(tmp_path / "explicit")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    assert decoded["file_changes"] == decoded["file_changes"]
    changes = decoded["file_changes"]
    assert len(changes) == 1
    change = changes[0]
    assert change["path"] == "fixture_project/checkout.py"
    assert change["oldString"] == "OLD-EXACT"
    assert change["newString"] == "NEW-EXACT"
    assert change["fragment_kind"] == "recorded_fragment"
    assert "recorded fragments" in change["fragment_note"]
    assert "before_sha256" not in change
    assert "after_sha256" not in change


def test_missing_parent_and_call_links_are_not_invented(tmp_path: Path) -> None:
    bundle, connection = _create_missing_link_bundle(tmp_path / "missing")
    try:
        decoded = decode_opencode_bundle(bundle)
    finally:
        connection.close()

    assert decoded["responses"][0]["id"] == "msg_orphan"
    assert decoded["responses"][0]["turn_id"] is None
    assert decoded["actions"][0]["id"] == "part_pending"
    assert decoded["actions"][0]["turn_id"] is None
    assert decoded["results"] == []
    assert [item for item in decoded["relations"] if item["kind"] == "turn_response"] == []
    assert [item for item in decoded["relations"] if item["kind"] == "action_result"] == []
