"""Cursor CLI chat store decoder, capture qualification and the private packet set."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from session_bench.cursor_cli_density import read_cursor_family, store_statements, transcript_statements
from session_bench.cursor_cli_live import add_native_final_after_chain, decode_cursor_cli_native, decode_cursor_cli_store, scored_pool
from session_bench.cursor_cli_score_inputs import (
    CONTROLLER_SHA256, EXTRACT, EXTRACT_SCHEMA, ROOT_DOCUMENTS, SHARED_STORE_TABLES, apply_cursor_cli_native_absence, qualify_isolated_root,
    remove_final_response, session_paths, shared_store_findings, usage_key_findings,
)
from session_bench.cursor_cli_store import classify_blobs, contract_exceptions, decode_typed, encode, read_store, remove_blobs
from session_bench.native_replay import canonical
from session_bench.score_replay import replay_score_package, validate_score_packet, verify_score_packet_tamper_controls

ROOT = Path(__file__).resolve().parents[1]
RUNS = ("cursor-cli-2026-10-06-r1", "cursor-cli-2026-10-06-r2", "cursor-cli-2026-10-06-r3")
CAPTURES = ROOT / "artifacts/survival-v1-runs"
PACKETS = ROOT / "artifacts/v1-expanded-preparation/cursor-cli-score-replay-v3"
SESSION = "11111111-2222-3333-4444-555555555555"
R1 = "Requirement R1: run the checks. SB_SURVIVAL_V1_RUN_x"
R2 = "Correction R2 supersedes R1. SB_SURVIVAL_V1_RUN_x"
CALL = "python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_x && python3 bench_check.py baseline --run-canary SB_SURVIVAL_V1_RUN_x"
INSPECT, BASELINE, FINAL = ("SB_SURVIVAL_V1_HELPER_INSPECT_i-1 {}", "SB_SURVIVAL_V1_HELPER_BASELINE_b-1 {}", "SB_SURVIVAL_V1_HELPER_FINAL_f-1 {}")
BEFORE, AFTER = "def checkout(items):\n    return 5\n", "def checkout(items):\n    return 0\n"
TARGET = "/work/project/fixture_project/checkout.py"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_store(*, mutate=None, model="model-a", user_version=1):
    """A two-turn store in the native shape: turn tree, message list, one older root, two file blobs."""
    blobs = {}

    def add(data):
        data = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        blobs[sha(data)] = data
        return bytes.fromhex(sha(data))

    def reasoning():
        return {"type": "reasoning", "text": "", "signature": "opaque", "providerOptions": {"cursor": {"modelName": model}}}

    def shell(call, command, output, *, code=0, start=10):
        body = [(1, command), (2, "/work/project/fixture_project"), (5, output)] + ([(3, code)] if code else [])
        step = add(encode([(2, [(1, [(1, [(1, command), (2, "/work/project/fixture_project"), (15, "run")]),
                                        (2, [(2 if code else 1, body), (102, 0)])]), (57, call), (59, start), (60, start + 5)])]))
        used = {"type": "tool-call", "toolCallId": call, "toolName": "Shell",
                "args": {"command": command, "working_directory": "/work/project/fixture_project", "description": "run"}}
        result = add({"role": "tool", "content": [{"type": "tool-result", "toolCallId": call, "toolName": "Shell", "result": output}], "id": call,
                      "providerOptions": {"cursor": {"highLevelToolCallResult": {"output": {"success": {"stdout": output}}}}}})
        return step, used, result

    system = add({"role": "system", "content": "You are a coding assistant. Follow the instructions of the vendor."})
    context = add({"role": "user", "content": "<user_info>\nOS Version: darwin 1\n</user_info>"})
    start = add(encode([(1, system), (1, context), (5, b""), (22, "cli"), (26, 1), (27, "UTC"), (39, 0)]))
    user1 = add(encode([(1, R1), (2, "user-1"), (10, start), (25, 100), (26, 100)]))
    prompt1 = add({"role": "user", "content": [{"type": "text", "text": f"<timestamp>t</timestamp>\n<user_query>\n{R1}\n</user_query>"}]})
    thinking = add(encode([(3, [(1, "I will run both checks."), (2, 3), (3, 101), (4, 104)])]))
    step1, used1, result1 = shell("call-1", CALL, f"{INSPECT}\n{BASELINE}\n", code=1, start=110)
    text1 = "Baseline fails.\n\nSB_SURVIVAL_V1_RESPONSE_R1_a"
    said1 = add(encode([(1, [(1, text1), (2, 120)])]))
    assistant1 = add({"role": "assistant", "content": [reasoning(), used1], "id": "1"})
    assistant2 = add({"role": "assistant", "content": [reasoning(), {"type": "text", "text": text1}], "id": "2"})
    turn1 = add(encode([(1, [(1, user1), (2, thinking), (2, step1), (2, said1), (3, "request-1"), (4, b"token-1"), (5, 1), (10, "user-1")])]))
    file_before, file_after = add(BEFORE.encode()), add(AFTER.encode())
    write = add(encode([(2, [(12, [(1, [(1, TARGET), (6, AFTER)]), (2, [(1, [(1, TARGET), (6, BEFORE), (7, AFTER), (8, "Wrote contents")])])]),
                            (57, "call-2"), (59, 210), (60, 215)])]))
    used2 = {"type": "tool-call", "toolCallId": "call-2", "toolName": "Write", "args": {"path": TARGET, "contents": AFTER}}
    result2 = add({"role": "tool", "content": [{"type": "tool-result", "toolCallId": "call-2", "toolName": "Write", "result": "Wrote contents"}], "id": "call-2"})
    step3, used3, result3 = shell("call-3", "python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_x", FINAL + "\n", start=220)
    text2 = "Fixed.\n\nSB_SURVIVAL_V1_RESPONSE_R2_b"
    said2 = add(encode([(1, [(1, text2), (2, 230)])]))
    pending = json.dumps({"id": "3", "role": "assistant", "content": [reasoning(), used2, used3]})
    assistant3 = add({"role": "assistant", "content": [reasoning(), used2, used3], "id": "3"})
    assistant4 = add({"role": "assistant", "content": [reasoning(), {"type": "text", "text": text2}], "id": "4"})
    first = [system, context, prompt1, assistant1, result1, assistant2]
    middle = add(encode([(1, item) for item in first] + [(8, turn1), (22, "cli"), (26, 1), (27, "UTC"), (39, 0)]))
    user2 = add(encode([(1, R2), (2, "user-2"), (10, middle), (25, 200), (26, 200)]))
    prompt2 = add({"role": "user", "content": [{"type": "text", "text": f"<timestamp>t</timestamp>\n<user_query>\n{R2}\n</user_query>"}]})
    turn2 = add(encode([(1, [(1, user2), (2, write), (2, step3), (2, said2), (3, "request-2"), (4, b"token-2"), (5, 1), (10, "user-2")])]))
    older = add(encode([(1, item) for item in first + [prompt2]] + [(4, pending), (8, turn1), (22, "cli"), (26, 1), (27, "UTC"), (39, 0)]))
    messages = first + [prompt2, assistant3, result2, result3, assistant4]
    latest = add(encode([(1, item) for item in messages] + [(5, [(1, 900), (2, 1000)]), (8, turn1), (8, turn2),
                                                           (15, [(1, TARGET), (2, [(1, file_after), (2, file_before)])]),
                                                           (22, "cli"), (26, 1), (27, "UTC"), (39, 0)]))
    meta = {"agentId": SESSION, "latestRootBlobId": latest.hex(), "name": "New Agent", "createdAt": 1, "mode": "default",
            "isRunEverything": True, "blobEncryptionKey": "ab" * 32}
    if mutate is not None:
        mutate(blobs, meta, locals())
    return blobs, meta, user_version


def write_store(path, blobs, meta, user_version=1):
    connection = sqlite3.connect(path)
    connection.executescript("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB);CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO meta VALUES ('0',?)", (json.dumps(meta).encode().hex(),))
    connection.executemany("INSERT INTO blobs VALUES (?,?)", blobs.items())
    connection.execute(f"PRAGMA user_version={user_version}")
    connection.commit(); connection.close()
    return path.read_bytes()


def decode(tmp_path, sidecar=b'{"schemaVersion":1,"createdAtMs":1,"cwd":"/work/project"}', **options):
    blobs, meta, version = synthetic_store(**options)
    return decode_cursor_cli_store(write_store(tmp_path / "store.db", blobs, meta, version), sidecar, session_id=SESSION)


def test_typed_decoder_refuses_a_field_or_wire_type_outside_the_map():
    assert decode_typed(encode([(1, [(1, "text"), (2, 5)])]), "step") == {1: [{1: ["text"], 2: [5]}]}
    with pytest.raises(ValueError, match="unknown field"):
        decode_typed(encode([(7, "x")]), "step")
    with pytest.raises(ValueError, match="not a varint"):
        decode_typed(encode([(1, [(1, "text"), (2, "5")])]), "step")


def test_every_blob_gets_a_class_and_an_older_root_is_a_checkpoint(tmp_path):
    blobs, meta, _ = synthetic_store()
    classified, exceptions = classify_blobs(read_store(write_store(tmp_path / "store.db", blobs, meta)))
    assert exceptions == [] and all(entry["class"] for entry in classified.values())
    roots = [entry for entry in classified.values() if entry["class"] == "root"]
    assert len(roots) == 4 and sum(entry["current"] for entry in roots) == 1
    assert sum(entry["class"] == "file_content" for entry in classified.values()) == 2


def test_native_facts_come_from_the_turn_tree_and_the_model_from_the_bound_json_message(tmp_path):
    decoded = decode(tmp_path)
    assert decoded["status"] == "ok" and decoded["diagnostics"] == []
    assert [(row["id"], row["text"], row["timestamp"]) for row in decoded["turns"]] == [("user-1", R1, 100), ("user-2", R2, 200)]
    assert [(row["phase"], row.get("canary"), row["model_id"], row["timestamp"]) for row in decoded["responses"]] == [
        ("final_answer", "SB_SURVIVAL_V1_RESPONSE_R1_a", "model-a", 120), ("final_answer", "SB_SURVIVAL_V1_RESPONSE_R2_b", "model-a", 230)]
    assert decoded["harness"] == "cursor" and decoded["surface"] == "cli" and decoded["usage"] == []
    assert decoded["context_window"]["estimated_tokens"] == 900


def test_compound_shell_call_is_the_record_of_each_helper_segment(tmp_path):
    decoded = decode(tmp_path)
    segments = [row for row in decoded["actions"] if row.get("parent_call_id") == "call-1"]
    assert [(row["id"], row["action_kind"], row["argv"][2]) for row in segments] == [
        ("call-1:segment-0", "inspect", "inspect"), ("call-1:segment-1", "test", "baseline")]
    results = {row["action_id"]: row for row in decoded["results"]}
    # Inspect returned zero (the && chain reached baseline). The call's exit code counts for the last segment.
    assert (results["call-1:segment-0"]["output"], results["call-1:segment-0"]["exit_code"]) == (INSPECT, 0)
    assert (results["call-1:segment-1"]["output"], results["call-1:segment-1"]["exit_code"], results["call-1:segment-1"]["status"]) == (BASELINE, 1, "failure")
    assert results["call-3"]["exit_code"] == 0 and results["call-3"]["helper_nonce"] == "f-1"
    assert {row["from_id"] for row in decoded["relations"] if row["kind"] == "action_result"} == {"call-1:segment-0", "call-1:segment-1", "call-2", "call-3"}


def test_changed_file_hashes_are_the_content_blob_ids_and_equal_the_native_edit(tmp_path):
    change, = decode(tmp_path)["file_changes"]
    assert (change["before_sha256"], change["after_sha256"]) == (sha(BEFORE.encode()), sha(AFTER.encode()))
    assert change["hash_source"] == "native_content_addressed_file_blobs" and change["path"] == "fixture_project/checkout.py"

    def other_blob(blobs, meta, names):
        # The root names a content blob that is not the text of the edit result: no fact is made.
        wrong = b"other"
        blobs[sha(wrong)] = wrong
        old = names["latest"]
        data = blobs.pop(old.hex()).replace(names["file_after"], bytes.fromhex(sha(wrong)))
        blobs[sha(data)] = data
        meta["latestRootBlobId"] = sha(data)
    assert decode(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, mutate=other_blob)["file_changes"] == []


def test_final_after_needs_the_ordered_steps_of_the_r2_turn(tmp_path):
    decoded = decode(tmp_path)
    instance = {"turns": [{"response_canary": "SB_SURVIVAL_V1_RESPONSE_R1_a"}, {"response_canary": "SB_SURVIVAL_V1_RESPONSE_R2_b"}]}
    add_native_final_after_chain(decoded, instance)
    relation, = [row for row in decoded["relations"] if row["kind"] == "final_after"]
    assert (relation["from_id"], relation["to_id"]) == ("user-2", "call-3") and len(relation["native_chain_blob_ids"]) == 4
    other = decode(tmp_path / "y" if (tmp_path / "y").mkdir() is None else tmp_path)
    add_native_final_after_chain(other, {"turns": [{}, {"response_canary": "SB_SURVIVAL_V1_RESPONSE_R2_other"}]})
    assert not [row for row in other["relations"] if row["kind"] == "final_after"]


@pytest.mark.parametrize("options,code", [
    ({"user_version": 2}, "unsupported_user_version"),
    ({"sidecar": b'{"schemaVersion":2}'}, "unsupported_sidecar_schema_version"),
    ({"sidecar": b'{"cwd":"/work"}'}, "sidecar_without_schema_version"),
])
def test_another_declared_version_makes_the_decode_unsupported(tmp_path, options, code):
    decoded = decode(tmp_path, **options)
    assert decoded["status"] == "unsupported" and code in {row["code"] for row in decoded["diagnostics"]}


def test_a_blob_outside_the_classes_or_a_turn_whose_two_statements_differ_is_a_contract_exception(tmp_path):
    def stray(blobs, meta, names):
        blobs[sha(b"stray bytes")] = b"stray bytes"
    decoded = decode(tmp_path, mutate=stray)
    assert decoded["status"] == "unsupported" and "unclassified_blob" in {row["code"] for row in decoded["diagnostics"]}

    def other_text(blobs, meta, names):
        # The JSON message list states another response text than the text step of the turn.
        old = names["assistant4"].hex()
        changed = blobs.pop(old).replace(b"Fixed.", b"Other.")
        blobs[sha(changed)] = changed
        root = blobs.pop(names["latest"].hex()).replace(bytes.fromhex(old), bytes.fromhex(sha(changed)))
        blobs[sha(root)] = root
        meta["latestRootBlobId"] = sha(root)
    (tmp_path / "z").mkdir()
    decoded = decode(tmp_path / "z", mutate=other_text)
    assert decoded["status"] == "unsupported" and "message_list_differs_from_turn" in {row["code"] for row in decoded["diagnostics"]}


def test_a_changed_blob_no_longer_matches_its_id(tmp_path):
    blobs, meta, _ = synthetic_store()
    victim = next(key for key, data in blobs.items() if b"I will run both checks" in data)
    blobs[victim] = blobs[victim].replace(b"both", b"some")
    _, exceptions = classify_blobs(read_store(write_store(tmp_path / "store.db", blobs, meta)))
    assert "blob_hash_mismatch" in {row["code"] for row in exceptions}


def test_statements_count_both_structures_and_only_thinking_is_stated_once(tmp_path):
    blobs, meta, _ = synthetic_store()
    classified, _ = classify_blobs(read_store(write_store(tmp_path / "store.db", blobs, meta)))
    _, roles, statements = store_statements(classified)
    counts = {}
    for events in statements:
        for event in events:
            counts[event] = counts.get(event, 0) + 1
    once = [event for event, count in counts.items() if count == 1]
    assert len(once) == 1 and once[0].startswith("message:")           # the thinking step
    assert counts["message:user-1"] == 2 and counts["call:call-1"] >= 2 and counts["result:call-1"] == 2
    assert counts["call:call-2"] == 3                                   # JSON call, tool step, pending message of an older root
    assert "explanation" in roles and roles.count("index") == 2
    # A root with a pending message has the role of that message; a root without one is a session record.
    by_class = [role for role, entry in zip(roles, classified.values()) if entry["class"] == "root"]
    assert sorted(by_class) == ["session", "session", "session", "tool_call"]


def test_transcript_lines_are_bound_to_store_events_by_content(tmp_path):
    blobs, meta, _ = synthetic_store()
    classified, _ = classify_blobs(read_store(write_store(tmp_path / "store.db", blobs, meta)))
    lines = [
        {"role": "user", "message": {"content": [{"type": "text", "text": f"<user_query>\n{R1}\n</user_query>"}]}},
        {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell", "input": {
            "command": CALL, "working_directory": "/work/project/fixture_project", "description": "run"}}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "Baseline fails.\n\nSB_SURVIVAL_V1_RESPONSE_R1_a"}]}},
        {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell", "input": {"command": "echo not in the store"}}]}},
        {"type": "turn_ended", "status": "success"},
    ]
    roles, statements = transcript_statements(lines, classified)
    assert statements[0] == ("message:user-1",) and statements[1] == ("call:call-1",) and statements[2][0].startswith("message:")
    assert statements[3] == ("call:transcript-line-4:part-0",) and statements[4] == ()
    assert roles == ["user_message", "tool_call", "assistant_message", "tool_call", "metadata"]


def test_pool_drops_a_native_call_paired_by_id_with_an_unscored_observed_action(tmp_path):
    decoded = decode(tmp_path)
    observer = {"events": [{"kind": "action", "population_role": "unscored", "fields": {"call_id": "call-3"}}]}
    pool = scored_pool(decoded, observer)
    assert "call-3" not in {row["id"] for row in pool["actions"]} and "call-3:result" not in {row["id"] for row in pool["results"]}
    assert len(pool["actions"]) == 3 and len(scored_pool(decoded, {"events": []})["actions"]) == 4


def extract_for(decoded, **changes):
    edit = next(row for row in decoded["actions"] if row.get("action_kind") == "edit")
    done = next(row for row in decoded["results"] if row["action_id"] == edit["id"])
    row = {"rowid": 1, "hash": "0a1b2c3d", "source": "cli", "fileExtension": "py", "fileName": TARGET, "requestId": edit["call_id"],
           "conversationId": SESSION, "timestamp": edit["timestamp"] + 1, "createdAt": edit["timestamp"] + 1, "model": "default"}
    content = {"rowid": 1, "gitPath": "project/fixture_project/checkout.py", "content": AFTER, "conversationId": SESSION, "model": "default",
               "fileExtension": "py", "createdAt": done["timestamp"]}
    document = {"schema_version": EXTRACT_SCHEMA, "session_id": SESSION, "tables_in_store": list(SHARED_STORE_TABLES),
                "rows": {"ai_code_hashes": [row], "tracked_file_content": [content]}}
    for key, value in changes.items():
        if key == "document":
            document.update(value)
        elif key == "content":
            content.update(value)
        else:
            row.update(value) if key == "hashes" else None
    return json.dumps(document).encode()


def test_shared_store_rows_prove_the_absence_only_when_they_describe_the_captured_edit(tmp_path):
    decoded = decode(tmp_path)
    assert shared_store_findings(extract_for(decoded), decoded) == {"proved": True, "reasons": []}
    refused = [
        {"hashes": {"inputTokens": 5}},                                   # an unknown column could be a count
        {"hashes": {"conversationId": "another-session"}},
        {"hashes": {"requestId": "call-of-another-edit"}},
        {"hashes": {"timestamp": 1}},                                     # outside the native edit step
        {"content": {"content": "another file text"}},
        {"content": {"totalCost": 1}},
        {"document": {"session_id": "another-session"}},
        {"document": {"tables_in_store": list(SHARED_STORE_TABLES) + ["usage_events"]}},
        {"document": {"rows": {"ai_code_hashes": [], "tracked_file_content": []}}},
        {"document": {"rows": {"usage_events": [{"tokens": 5}]}}},
    ]
    for change in refused:
        assert shared_store_findings(extract_for(decoded, **change), decoded)["proved"] is False, change
    assert shared_store_findings(b"not json", decoded)["proved"] is False


def test_private_extract_needs_real_short_hashes_and_row_ids_and_the_public_form_needs_them_zeroed(tmp_path):
    decoded = decode(tmp_path)
    private = extract_for(decoded)
    seven = extract_for(decoded, hashes={"hash": "741d0d3"})              # a 32-bit value prints without its leading zero
    zeroed = json.loads(private)
    for items in zeroed["rows"].values():
        for row in items:
            row["rowid"] = 0
    zeroed["rows"]["ai_code_hashes"][0]["hash"] = "00000000"
    public = json.dumps(zeroed).encode()
    assert shared_store_findings(private, decoded)["proved"] and shared_store_findings(seven, decoded)["proved"]
    assert shared_store_findings(public, decoded, public=True)["proved"]
    assert not shared_store_findings(public, decoded)["proved"]             # a private packet may not hold the zeroed form
    assert not shared_store_findings(private, decoded, public=True)["proved"]  # a public packet may not hold the real values
    half = json.loads(public); half["rows"]["ai_code_hashes"][0]["hash"] = "0a1b2c3d"
    assert not shared_store_findings(json.dumps(half).encode(), decoded, public=True)["proved"]
    assert not shared_store_findings(extract_for(decoded, hashes={"hash": "not-hex!"}), decoded)["proved"]


def test_absence_needs_a_clean_decode_an_empty_scan_and_proving_shared_store_rows():
    measurement = {"metrics": [{"id": name, "state": "unresolved", "correct": 0, "decoded_eligible": 0}
                               for name in ("attribution.usage", "attribution.token_semantics", "attribution.reconciliation")]}
    clean = {"status": "ok", "diagnostics": [], "usage": []}
    states = lambda result: [row["state"] for row in result["metrics"]]
    assert states(apply_cursor_cli_native_absence(measurement, clean, [], {"proved": True})) == ["native_absent"] * 3
    assert states(apply_cursor_cli_native_absence(measurement, clean, [], {"proved": False})) == ["unresolved"] * 3
    assert states(apply_cursor_cli_native_absence(measurement, clean, [], None)) == ["unresolved"] * 3
    assert states(apply_cursor_cli_native_absence(measurement, clean, ["inputTokens"], {"proved": True})) == ["unresolved"] * 3
    assert states(apply_cursor_cli_native_absence(measurement, {**clean, "status": "unsupported"}, [], {"proved": True})) == ["unresolved"] * 3


# --- the three retained captures and the private packet set ---

def documents(run):
    from scripts.build_cursor_cli_score_replays import capture_documents
    return capture_documents(CAPTURES / run)


@pytest.fixture(scope="module")
def receipts():
    return {run: {name: documents(run)[name] for name in ROOT_DOCUMENTS} for run in RUNS}


@pytest.mark.parametrize("run", RUNS)
def test_isolated_root_receipts_bind_the_session_family(run, receipts):
    fresh = qualify_isolated_root(receipts[run])
    plan = json.loads(receipts[run]["plan.json"])
    workspace = plan["argv_base"][plan["argv_base"].index("--workspace") + 1]
    assert set(fresh["family"]) == set(session_paths(workspace, fresh["session_id"]).values())
    assert all((CAPTURES / run / name).is_file() and sha((CAPTURES / run / name).read_bytes()) == digest for name, digest in fresh["family"].items())
    from scripts.build_cursor_cli_score_replays import capture_controller_source
    assert sha(capture_controller_source()) == CONTROLLER_SHA256      # the controller as it was at capture time


@pytest.mark.parametrize("mutation", ["normal_root", "other_env", "old_entry", "late_entry", "extra_file", "changed_family", "harness_session", "failed_turn"])
def test_isolated_root_rejects_what_the_receipts_do_not_prove(mutation, receipts):
    value = {name: json.loads(data) for name, data in receipts[RUNS[0]].items()}
    plan, env, state, inventory = value["plan.json"], value["run-env.json"], value["controller-state.json"], value["root-inventory.json"]
    files = [row for row in inventory["entries"] if row["kind"] == "file"]
    if mutation == "normal_root":
        plan["persistence_route"] = state["persistence_route"] = "normal-root-new-family"
    elif mutation == "other_env":
        env["CURSOR_DATA_DIR"] = "/somewhere/else"
    elif mutation == "old_entry":
        files[0]["birthtime_ns"] = state["turns"][0]["started_ns"] - 1
    elif mutation == "late_entry":
        files[0]["mtime_ns"] = state["turns"][1]["ended_ns"] + 1
    elif mutation == "extra_file":
        inventory["entries"].append({**files[0], "path": "cursor-config/chats/other/other/store.db"})
    elif mutation == "changed_family":
        next(row for row in files if row["path"].endswith("/store.db"))["sha256"] = "0" * 64
    elif mutation == "harness_session":
        next(row for row in files if row["path"].endswith("cli-config.json"))["holds_session_id"] = True
    else:
        state["turns"][1]["return_code"] = 1
    with pytest.raises(ValueError):
        qualify_isolated_root({name: canonical(item) for name, item in value.items()})


@pytest.mark.parametrize("run", RUNS)
def test_real_store_decodes_clean_and_the_root_holds_no_usage_key(run):
    decoded = decode_cursor_cli_native(CAPTURES / run)
    assert decoded["status"] == "ok" and decoded["diagnostics"] == [] and decoded["usage"] == []
    assert {row.get("model_id") for row in decoded["responses"]} == {"cursor-grok-4.5-high"}
    assert all(type(row["timestamp"]) is int for key in ("turns", "responses", "actions", "results", "file_changes") for row in decoded[key])
    assert usage_key_findings(CAPTURES / run) == []
    assert not {row["locator"]["artifact"] for row in decoded["records"]} - {next(iter(decoded["records"]))["locator"]["artifact"]}


@pytest.mark.parametrize("run", RUNS)
def test_density_counts_every_record_of_the_family_once(run):
    fresh = qualify_isolated_root({name: documents(run)[name] for name in ROOT_DOCUMENTS})
    artifacts = [{"path": name, "sha256": digest} for name, digest in fresh["family"].items()]
    records, _, exceptions, occurrences = read_cursor_family(CAPTURES / run, artifacts=artifacts)
    assert exceptions == [] and len({row["record_id"] for row in records}) == len(records)
    kinds = {row["record_kind"] for row in records}
    assert "unknown" not in kinds and {"user_message", "explanation", "snapshot", "system", "index", "session"} <= kinds
    # The transcript is in the read: a line that repeats events of the store is a snapshot.
    transcript = [row for row in records if ".jsonl:line-" in row["record_id"]]
    assert transcript and {row["record_kind"] for row in transcript} == {"snapshot", "metadata"}
    assert sum(1 for _, occurrence in occurrences if ".jsonl:line-" in occurrence) >= 8
    assert "tool_call" in kinds
    counts = {}
    for event, _ in occurrences:
        counts[event] = counts.get(event, 0) + 1
    assert all(count >= 2 for event, count in counts.items() if event.startswith(("call:", "result:")))


@pytest.fixture(scope="module")
def summary():
    return json.loads((PACKETS / "summary.json").read_bytes())


def test_all_31_metrics_resolve_with_honest_absences(summary):
    assert [row["resolved_metric_count"] for row in summary["runs"]] == [31, 31, 31]
    for row in summary["runs"]:
        receipt = json.loads((PACKETS / f"{row['packet']}-receipt.json").read_bytes())
        metrics = {item["id"]: item for item in receipt["diagnostics"]["intact"]["metrics"]}
        absent = {name for name, item in metrics.items() if item["state"] == "native_absent"}
        assert absent == {"attribution.usage", "attribution.token_semantics", "attribution.reconciliation", "broad.standard_tools_readable"}
        # The root is not complete: rows of the session sit in a shared store.
        assert metrics.pop("portable.complete_root")["state"] == "contradiction" and metrics["portable.companions"]["state"] == "measured"
        assert all(item["state"] == "measured" for name, item in metrics.items() if name not in absent)
        full = {name for name, item in metrics.items() if item["state"] == "measured" and item["correct"] == item["observed_eligible"] == item["decoded_eligible"]}
        assert set(metrics) - full - absent == {"broad.naive_reader_duplicate_safety", "broad.classified_content_density"}
        assert metrics["broad.event_timestamps"]["correct"] == 13 and metrics["work.actions"]["decoded_eligible"] == 4
        assert row["os_sandboxed"] is True and row["tamper_controls"] == "passed" and row["selected_loss_detected"] is True


def test_packet_replays_and_rejects_a_changed_native_file_or_receipt(summary):
    row = summary["runs"][0]
    packet = PACKETS / row["packet"]
    receipt = replay_score_package(packet, expected_manifest_sha256=row["manifest_sha256"], os_sandboxed=False)
    assert receipt["diagnostics_sha256"] == row["diagnostics_sha256"]
    assert verify_score_packet_tamper_controls(packet, expected_manifest_sha256=row["manifest_sha256"])["status"] == "passed"
    contents = {path.relative_to(packet).as_posix(): path.read_bytes() for path in packet.rglob("*") if path.is_file()}

    def repin(changed):
        manifest = json.loads(changed["manifest.json"])
        for entry in manifest["files"]:
            entry.update(sha256=sha(changed[entry["path"]]), size_bytes=len(changed[entry["path"]]))
        return {**changed, "manifest.json": canonical(manifest) + b"\n"}
    inventory = json.loads(contents["inputs/capture/root-inventory.json"])
    inventory["entries"][0]["birthtime_ns"] = 1
    with pytest.raises(ValueError):
        validate_score_packet(repin({**contents, "inputs/capture/root-inventory.json": canonical(inventory) + b"\n"}))
    context = json.loads(contents["inputs/context.json"])
    with pytest.raises(ValueError, match="companion"):
        validate_score_packet(repin({**contents, "inputs/context.json": canonical({**context, "required_companions": []})}))
    # The context cannot call the root complete, and the packet cannot drop the shared-store extract.
    assert context["complete_root"] is False
    with pytest.raises(ValueError, match="unproven"):
        validate_score_packet(repin({**contents, "inputs/context.json": canonical({**context, "complete_root": True})}))
    without = {name: data for name, data in contents.items() if name != "inputs/capture/" + EXTRACT}
    manifest = json.loads(without["manifest.json"])
    manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "inputs/capture/" + EXTRACT]
    with pytest.raises(ValueError):
        validate_score_packet({**without, "manifest.json": canonical(manifest) + b"\n"})


@pytest.mark.parametrize("run", RUNS)
def test_real_shared_store_rows_describe_the_captured_edit_and_hold_no_count(run):
    decoded = decode_cursor_cli_native(CAPTURES / run)
    extract = (CAPTURES / run / EXTRACT).read_bytes()
    assert shared_store_findings(extract, decoded) == {"proved": True, "reasons": []}
    rows = json.loads(extract)["rows"]
    assert len(rows["ai_code_hashes"]) == 6 and len(rows["tracked_file_content"]) == 1
    other = decode_cursor_cli_native(CAPTURES / RUNS[(RUNS.index(run) + 1) % 3])
    assert shared_store_findings(extract, other)["proved"] is False


def test_loss_control_removes_both_statements_of_the_final_response(tmp_path, summary):
    packet = PACKETS / summary["runs"][0]["packet"]
    workload = json.loads((packet / "inputs/workload.json").read_bytes())
    removed = remove_final_response(packet / "native", tmp_path / "native", workload["turns"][1]["response_canary"])
    assert len(removed) == 2 and len({row["sha256"] for row in removed}) == 2
    damaged = decode_cursor_cli_native(tmp_path / "native")
    assert damaged["status"] == "unsupported" and "dangling_blob_reference" in {row["code"] for row in damaged["diagnostics"]}
    assert not [row for row in damaged["responses"] if row.get("canary") == workload["turns"][1]["response_canary"]]


def test_remove_blobs_needs_an_existing_row(tmp_path):
    blobs, meta, _ = synthetic_store()
    database = write_store(tmp_path / "store.db", blobs, meta)
    assert len(read_store(remove_blobs(database, [next(iter(blobs))]))["tables"]["blobs"]) == len(blobs) - 1
    with pytest.raises(ValueError):
        remove_blobs(database, ["0" * 64])
    assert contract_exceptions(read_store(database), b'{"schemaVersion":1}', "another-session") == [{"code": "session_identity_mismatch"}]
