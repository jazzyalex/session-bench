"""Adversarial controls for complete canonical native logical byte inventory."""

import hashlib
import json
from pathlib import Path

import pytest

from session_bench.native_density import BYTE_ACCOUNTING_RULE, inventory_native_jsonl_density


def package(tmp_path: Path, family: str, records: list, *, compact: bool = True, raw_suffix: bytes = b"") -> tuple[Path, list[dict]]:
    root = tmp_path
    root.mkdir()
    name = "rollout.jsonl" if family == "codex" else "session.jsonl"
    artifact_id = "rollout" if family == "codex" else "session"
    data = b"".join((json.dumps(record, ensure_ascii=compact, separators=(",", ":") if compact else None) + "\n").encode() for record in records) + raw_suffix
    (root / name).write_bytes(data)
    artifact = {"id": artifact_id, "path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []}
    (root / "decode.json").write_text(json.dumps({"format": "codex-rollout-v1" if family == "codex" else "claude-code-jsonl-v1", "artifacts": [artifact]}))
    return root, [artifact]


def rows(family: str) -> list[dict]:
    if family == "codex":
        return [
            {"type": "session_meta", "payload": {"session_id": "native-session", "cli_version": "0.154.0"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "résumé 🎾"}]}},
            {"type": "future_native_record", "payload": {"opaque": "unknown payload"}},
            {"type": "token_usage_record", "payload": {"session_id": "native-session", "usage": {"input_tokens": 1}}},
        ]
    return [
        {"type": "user", "sessionId": "native-session", "uuid": "u1", "message": {"role": "user", "content": "start"}},
        {"type": "assistant", "sessionId": "native-session", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "résumé 🎾"}]}},
        {"type": "future_native_record", "sessionId": "native-session", "opaque": "unknown payload"},
        {"type": "system", "sessionId": "native-session", "data": {"cache": "metadata"}},
    ]


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_complete_density_inventory_counts_every_native_record_with_canonical_bytes_and_proof(tmp_path: Path, family: str):
    native_rows = rows(family)
    root, artifacts = package(tmp_path / "native", family, native_rows)
    inventory = inventory_native_jsonl_density(root, family=family, session_id="native-session", expected_artifacts=artifacts)

    assert inventory.evidence["evidence_complete"] is True
    assert inventory.byte_accounting_rule == BYTE_ACCOUNTING_RULE
    assert inventory.diagnostics == ()
    records = inventory.evidence["records"]
    assert len(records) == len(native_rows) == len(inventory.native_locators) - 1
    assert inventory.native_locators[-1]["id"] == f"rule:{BYTE_ACCOUNTING_RULE}:session_bench/native_density.py"
    assert inventory.native_locators[-1]["sha256"] == hashlib.sha256((Path(__file__).resolve().parents[1] / "session_bench/native_density.py").read_bytes()).hexdigest()
    assert [record["logical_bytes"] for record in records] == [
        len(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")) for record in native_rows
    ]
    assert records[2]["classification"] == "unknown"
    assert records[3]["classification"] == "unclassified"
    assert records[2]["logical_bytes"] > 0 and records[3]["logical_bytes"] > 0
    raw_lines = (root / artifacts[0]["path"]).read_bytes().splitlines()
    for index, (record, locator, raw) in enumerate(zip(records, inventory.native_locators, raw_lines), 1):
        assert record["record_id"] == locator["id"] == f"{artifacts[0]['id']}@{artifacts[0]['path']}:line-{index}"
        assert locator["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("family", ["codex", "claude"])
def test_native_logical_density_is_invariant_to_json_whitespace_and_unicode_escaping(tmp_path: Path, family: str):
    compact, compact_artifacts = package(tmp_path / "compact", family, rows(family), compact=True)
    spaced, spaced_artifacts = package(tmp_path / "spaced", family, rows(family), compact=False)
    left = inventory_native_jsonl_density(compact, family=family, session_id="native-session", expected_artifacts=compact_artifacts)
    right = inventory_native_jsonl_density(spaced, family=family, session_id="native-session", expected_artifacts=spaced_artifacts)

    assert compact_artifacts[0]["sha256"] != spaced_artifacts[0]["sha256"]
    assert left.evidence == right.evidence
    assert left.native_locators != right.native_locators


@pytest.mark.parametrize("family", ["codex", "claude"])
@pytest.mark.parametrize("damage", ["malformed", "duplicate_key", "nonobject", "nonfinite", "foreign_session", "wrong_artifact_digest"])
def test_native_inventory_failure_stays_unresolved_without_invented_bytes(tmp_path: Path, family: str, damage: str):
    suffix = {
        "malformed": b"{not-json}\n", "duplicate_key": b'{"type":"system","type":"other"}\n',
        "nonobject": b"42\n", "nonfinite": b'{"count":NaN}\n',
    }.get(damage, b"")
    root, artifacts = package(tmp_path / "damaged", family, rows(family), raw_suffix=suffix)
    expected = artifacts if damage != "wrong_artifact_digest" else [{**artifacts[0], "sha256": "0" * 64}]
    inventory = inventory_native_jsonl_density(root, family=family, session_id="foreign" if damage == "foreign_session" else "native-session", expected_artifacts=expected)

    assert inventory.evidence["evidence_complete"] is False
    assert inventory.evidence["records"] == []
    assert inventory.native_locators == ()
    assert inventory.diagnostics


def test_unsupported_codex_message_content_remains_unknown_native_bytes(tmp_path: Path):
    native_rows = rows("codex")
    native_rows[1]["payload"]["content"] = [{"type": "encrypted_future_content", "ciphertext": "opaque"}]
    root, artifacts = package(tmp_path / "opaque", "codex", native_rows)
    inventory = inventory_native_jsonl_density(root, family="codex", session_id="native-session", expected_artifacts=artifacts)
    assert inventory.evidence["evidence_complete"]
    assert inventory.evidence["records"][1]["record_kind"] == "unknown"
    assert inventory.evidence["records"][1]["classification"] == "unknown"


def test_real_sanitized_codex_fixture_classifies_supported_tool_functions_and_keeps_every_record():
    from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle
    root = Path(__file__).resolve().parents[1] / "tests/fixtures/codex-cli-native-0154"
    decoded = decode_codex_cli_bundle(root)
    inventory = inventory_native_jsonl_density(root, family="codex", session_id=decoded["session_id"], expected_artifacts=decoded["package"]["artifacts"])
    raw_records = [json.loads(line) for line in (root / "rollout.jsonl").read_text().splitlines() if line.strip()]
    assert inventory.evidence["evidence_complete"]
    assert len(inventory.evidence["records"]) == len(raw_records)
    checked_tools = 0
    for raw, counted in zip(raw_records, inventory.evidence["records"]):
        payload = raw["payload"]
        if payload.get("type") == "custom_tool_call":
            assert counted["record_kind"] == "tool_call" and counted["classification"] == "useful"
            checked_tools += 1
        if payload.get("type") == "custom_tool_call_output":
            assert counted["record_kind"] == "tool_result" and counted["classification"] == "useful"
            checked_tools += 1
        if payload.get("type") == "item_completed" and payload.get("item", {}).get("type") in {"CommandExecution", "FileChange"}:
            # It holds all text arguments of the call before it and not the full output of the call: only a copy.
            assert counted["record_kind"] == "snapshot" and counted["classification"] == "unclassified"
            checked_tools += 1
    assert checked_tools == 12


def test_a_record_restates_a_call_only_when_it_holds_all_text_arguments():
    from session_bench.native_density import holds_call_arguments
    edit = {"file_path": "/w/checkout.py", "old_string": "a = 1", "new_string": "a = 2", "replace_all": False}
    assert holds_call_arguments(edit, {"toolUseResult": {"filePath": "/w/checkout.py", "oldString": "a = 1", "newString": "a = 2"}})
    assert not holds_call_arguments(edit, {"toolUseResult": {"filePath": "/w/checkout.py", "oldString": "a = 1"}})  # only some
    # A result that names the file of a read holds the target, not the call.
    assert not holds_call_arguments({"file_path": "/w/checkout.py"}, {"toolUseResult": {"file": {"filePath": "/w/checkout.py"}}})
    # A record with the tool name and all arguments is a second statement of the call.
    assert holds_call_arguments({"path": "/w/checkout.py"}, {"toolName": "view", "arguments": {"path": "/w/checkout.py"}}, names_tool=True)
    # A target may be held as a URL of the same path; a limit (a number) is not text.
    assert holds_call_arguments({"cmd": ["ls -l"], "workdir": ["/w"], "yield_time_ms": 1000}, {"command": ["/bin/zsh", "-lc", "ls -l"], "cwd": "file:///w"})
    assert not holds_call_arguments({"cmd": ["ls -l"], "workdir": ["/w"]}, {"command": ["/bin/zsh", "-lc", "ls"], "cwd": "file:///w"})
    assert not holds_call_arguments({}, {"anything": "x"})


def test_codex_file_change_restates_the_patch_call_and_not_a_different_result():
    from session_bench.native_density import codex_forward_statements
    patch = "*** Begin Patch\n*** Update File: /w/checkout.py\n@@\n def f():\n-    return 1\n+    return 2\n*** End Patch"
    rows = [
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": "const patch = " + json.dumps(patch) + ";\ntext(await tools.apply_patch(patch));"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "t", "item": {"type": "FileChange", "id": "e1", "status": "completed", "stdout": "Success. Updated the following files:\nM /w/checkout.py\n",
            "changes": {"/w/checkout.py": {"type": "update", "unified_diff": "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"}}}}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": [{"type": "input_text", "text": "Script completed\nOutput:\n"}, {"type": "input_text", "text": "{}"}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c2", "name": "exec", "input": "const r = await tools.exec_command({cmd:\"ls\",\"workdir\":\"/w\",\"yield_time_ms\":1000}); text(JSON.stringify(r));"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "t", "item": {"type": "CommandExecution", "id": "e2", "command": ["/bin/zsh", "-lc", "ls"], "cwd": "file:///w", "aggregated_output": "a.txt\n"}}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "Output:\n"}, {"type": "input_text", "text": json.dumps({"exit_code": 0, "output": "a.txt\n"})}]}},
        {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "t", "item": {"type": "CommandExecution", "id": "e3", "command": ["/bin/zsh", "-lc", "pwd"], "cwd": "file:///w", "aggregated_output": "/w\n"}}},
    ]
    assert codex_forward_statements(rows) == [("call:c1",), ("call:c1",), ("result:c1",), ("call:c2",), ("call:c2", "result:c2"), ("result:c2",), ("call:e3", "result:e3")]
    rows[1]["payload"]["item"]["changes"]["/w/checkout.py"]["unified_diff"] = "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 3\n"
    assert codex_forward_statements(rows)[1] == ("call:e1", "result:e1")  # another hunk: its own event


def test_claude_edit_result_restates_its_call_and_a_read_result_does_not():
    from session_bench.native_density import claude_forward_statements
    use = lambda identity, name, arguments: {"type": "assistant", "uuid": "a-" + identity, "message": {"role": "assistant", "content": [{"type": "tool_use", "id": identity, "name": name, "input": arguments}]}}
    result = lambda identity, extra: {"type": "user", "uuid": "u-" + identity, "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": identity, "content": "ok"}]}, "toolUseResult": extra}
    statements = claude_forward_statements([
        use("read", "Read", {"file_path": "/w/checkout.py"}), result("read", {"type": "text", "file": {"filePath": "/w/checkout.py", "content": "a = 1"}}),
        use("edit", "Edit", {"replace_all": False, "file_path": "/w/checkout.py", "old_string": "a = 1", "new_string": "a = 2"}),
        result("edit", {"filePath": "/w/checkout.py", "oldString": "a = 1", "newString": "a = 2", "originalFile": "a = 1", "replaceAll": False}),
        use("bash", "Bash", {"command": "python3 x.py", "description": "run"}), result("bash", {"stdout": "done", "stderr": ""}),
    ])
    assert statements == [("call:read",), ("result:read",), ("call:edit",), ("result:edit", "call:edit"), ("call:bash",), ("result:bash",)]


def codex_duplicate_rows() -> list[dict]:
    def item(kind, **fields):
        return {"type": "event_msg", "payload": {"type": "item_completed", "turn_id": "t1", "item": {"type": kind, **fields}}}
    return [
        {"type": "session_meta", "payload": {"session_id": "native-session"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "response_item", "payload": {"type": "message", "id": "ctx", "role": "user", "content": [{"type": "input_text", "text": "context"}],
                                              "internal_chat_message_metadata_passthrough": {"turn_id": "t1", "content_item_kinds": ["environments.environment_context"]}}},
        {"type": "response_item", "payload": {"type": "message", "id": "u1", "role": "user", "content": [{"type": "input_text", "text": "prompt"}],
                                              "internal_chat_message_metadata_passthrough": {"turn_id": "t1", "content_item_kinds": ["user.text"]}}},
        item("UserMessage", id="um1", content=[{"type": "text", "text": "prompt"}]),
        item("AgentMessage", id="m1", content=[{"type": "Text", "text": "done"}]),
        {"type": "response_item", "payload": {"type": "message", "id": "m1", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": "ls"}},
        item("CommandExecution", id="exec-1", command=["sh", "-c", "ls"], aggregated_output="a"),
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "a"}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1", "last_agent_message": "done"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t2"}},
        {"type": "response_item", "payload": {"type": "message", "id": "u2", "role": "user", "content": [{"type": "input_text", "text": "prompt"}],
                                              "internal_chat_message_metadata_passthrough": {"turn_id": "t2", "content_item_kinds": ["user.text"]}}},
    ]


def test_codex_forward_read_counts_every_record_that_states_an_event():
    from session_bench.native_density import codex_forward_statements, forward_occurrences
    statements = codex_forward_statements(codex_duplicate_rows())
    assert statements == [(), (), (), ("message:u1",), ("message:u1",), ("message:m1",), ("message:m1",), ("call:c1",),
                          ("call:c1", "result:c1"), ("result:c1",), ("message:m1",), (), ("message:u2",)]
    occurrences = forward_occurrences([f"line-{number}" for number in range(1, 14)], statements)
    assert [occurrence for event, occurrence in occurrences if event == "call:c1"] == ["line-8", "line-9:statement-0"]
    assert len({occurrence for _, occurrence in occurrences}) == len(occurrences) == 10


def test_claude_forward_read_joins_the_queued_prompt_to_its_user_record_by_text():
    from session_bench.native_density import claude_forward_statements
    statements = claude_forward_statements([
        {"type": "queue-operation", "operation": "enqueue", "content": "prompt"},
        {"type": "queue-operation", "operation": "dequeue"},
        {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "prompt"}},
        {"type": "last-prompt", "lastPrompt": "pro"},
        {"type": "assistant", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}]}},
        {"type": "assistant", "uuid": "a2", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}},
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"type": "assistant", "uuid": "a3", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
        {"type": "user", "uuid": "u3", "message": {"role": "user", "content": "prompt"}},
    ])
    assert statements == [("message:line-1",), (), ("message:line-1",), (), (), ("call:t1",), ("result:t1",), ("message:a3",), ("message:u3",)]


def test_a_record_that_only_repeats_stated_events_is_a_snapshot_in_density(tmp_path: Path):
    root, artifacts = package(tmp_path / "codex", "codex", codex_duplicate_rows())
    inventory = inventory_native_jsonl_density(root, family="codex", session_id="native-session", expected_artifacts=artifacts)
    kinds = [row["record_kind"] for row in inventory.evidence["records"]]
    assert kinds == ["session", "metadata", "user_message", "user_message", "snapshot", "assistant_message", "snapshot", "tool_call",
                     "tool_result", "snapshot", "snapshot", "metadata", "user_message"]
    assert all(row["classification"] == "unclassified" for row in inventory.evidence["records"] if row["record_kind"] == "snapshot")
    claude = [
        {"type": "queue-operation", "sessionId": "native-session", "operation": "enqueue", "content": "start"},
        {"type": "user", "sessionId": "native-session", "uuid": "u1", "message": {"role": "user", "content": "start"}},
        {"type": "assistant", "sessionId": "native-session", "uuid": "a1", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
    ]
    root, artifacts = package(tmp_path / "claude", "claude", claude)
    inventory = inventory_native_jsonl_density(root, family="claude", session_id="native-session", expected_artifacts=artifacts)
    assert [row["record_kind"] for row in inventory.evidence["records"]] == ["user_message", "snapshot", "assistant_message"]
