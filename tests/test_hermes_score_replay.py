"""Hermes row decoder and score replay: facts only from the bound rows of the shared store."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from session_bench.hermes_score_inputs import (
    RECEIPT, STREAM_CAPTURE_DOCUMENTS, VERSION_EXTRACT as VERSION_DOCUMENT, absence_findings, apply_hermes_native_absence, required_companions,
    scored_pool, stream_observer_from_capture_documents, verify_state_receipt,
)
from session_bench.hermes_state_evidence import export_session_rows
from session_bench.hermes_store_rows import (
    APPLICATION_ID, COLUMNS, PROMPT_FILE, PROMPT_ROW_SCHEMA, ROWS_FILE, SCHEMA_FILE, SUPPORTED_SCHEMA_VERSIONS, VERSION_EXTRACT_SCHEMA,
    HermesRowsError, add_native_final_after_chain, apply_unified_diff, decode_hermes_session_rows, message_statements, read_hermes_family,
    read_prompt_row, read_rows, remove_final_response, row_bytes, sha, store_schema_sha256, usage_anchor, usage_key_findings,
)
from session_bench import hermes_store_rows
from session_bench.native_replay import canonical
from session_bench.score_replay import replay_score_package, validate_score_packet, verify_score_packet_tamper_controls

ROOT = Path(__file__).resolve().parents[1]
RUNS = ("hermes-codex-2026-10-07-08", "hermes-codex-2026-10-07-09", "hermes-codex-2026-10-07-10")
CAPTURES = ROOT / "artifacts/v1-expanded-preparation/live-captures"
PACKETS = ROOT / "artifacts/v1-expanded-preparation/hermes-score-replay-v3"
VERSION_EXTRACT = ROOT / "artifacts/v1-expanded-preparation/hermes-store-version-extract-v1/state-db-schema-version.json"
SID, OTHER = "20261006_101500_abc123", "20260901_090000_fed456"
CANARY = "SB_SURVIVAL_V1_RUN_hermes-test"
R1, R2 = "SB_SURVIVAL_V1_RESPONSE_R1_cafe", "SB_SURVIVAL_V1_RESPONSE_R2_fix"
HELPER = "python3 bench_check.py {} --run-canary " + CANARY
LINE = {phase: f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 {{"phase":"{phase}"}}' for phase in ("inspect", "baseline", "final")}
SCRIPT = "python3 - <<'PY'\nfrom pathlib import Path\nPath('checkout.py').write_text('def checkout(items):\\n    return 1\\n')\nPY\n"
PROMPT = "You are Hermes Agent, built by Test Lab. Be direct.\nThe operator likes short private answers about the garden.\n"
PROMPT_HASH = __import__("hashlib").sha256(PROMPT.encode()).hexdigest()


def prompt_row(text=PROMPT, *, digest=None, named=None, rowid=7):
    """The bound system prompt row document, in the canonical form of the packet."""
    digest = digest or __import__("hashlib").sha256(text.encode()).hexdigest()
    return canonical({"schema_version": PROMPT_ROW_SCHEMA, "table": "system_prompts", "selected_by": "hash", "read": "test", "read_on": "2026-10-06",
                      "session_system_prompt_hash": named or digest, "rows": [{"_rowid": rowid, "hash": digest, "prompt": text}]}) + b"\n"
_spec = importlib.util.spec_from_file_location("extract_hermes_store_version_under_test", ROOT / "scripts/extract_hermes_store_version.py")
extractor = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(extractor)


def _ddl(table, keys):
    typed = {"id": "TEXT PRIMARY KEY" if table == "sessions" else "INTEGER PRIMARY KEY AUTOINCREMENT",
             "session_id": "TEXT NOT NULL REFERENCES sessions(id)", "display_identity": "BLOB"}
    columns = ", ".join(f'"{name}" {typed.get(name, "")}'.strip() for name in COLUMNS[table])
    return f"CREATE TABLE {table} ({columns}{keys})"


SOURCE = "def checkout(items):\n    return 1\n"
INSPECT = 'SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001 ' + json.dumps({"checkout_source": SOURCE, "phase": "inspect"})
DIFF = "--- a//w/checkout.py\n+++ b//w/checkout.py\n@@ -1,2 +1,2 @@\n def checkout(items):\n-    return 1\n+    return 2\n"
FILE_STEPS = [
    ("call_r", "read_file", {"path": "/tmp/w/workspace/fixture_project/checkout.py"}, json.dumps({"content": "1|def checkout(items):\n2|    return 1"})),
    ("call_p", "patch", {"path": "fixture_project/checkout.py", "old_string": "    return 1\n", "new_string": "    return 2\n"},
     json.dumps({"success": True, "diff": DIFF})),
    ("call_b", "terminal", {"command": HELPER.format("final"), "workdir": "/tmp/w/workspace/fixture_project"},
     json.dumps({"output": LINE["final"], "exit_code": 0, "error": None})),
]


def make_store(path, *, r1=None, r2=None, r1_exit=1, session=SID, extra=None, application_id=APPLICATION_ID, models=("gpt-5.5", "gpt-5.5"),
               r2_steps=None, r1_output=None, anchor=False):
    """A store with the contract schema, one other session, and one test session of two turns with one call each."""
    connection = sqlite3.connect(path)
    connection.executescript(";".join([
        _ddl("sessions", ""), _ddl("messages", ""),
        _ddl("session_model_usage", ", PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)"),
        "CREATE TABLE schema_version (version INTEGER NOT NULL)", "INSERT INTO schema_version VALUES (17)",
        "CREATE TABLE system_prompts (hash TEXT PRIMARY KEY, prompt TEXT NOT NULL)",
        "CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)", "INSERT INTO state_meta VALUES ('fts_high_water', '5')",
        f"PRAGMA application_id={application_id}"]))

    def insert(table, **values):
        names = ", ".join(f'"{name}"' for name in values)
        connection.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' * len(values))})", list(values.values()))

    def message(owner, role, stamp, **values):
        insert("messages", session_id=owner, role=role, timestamp=stamp, observed=0, active=1, compacted=0, _compressed_summary=0,
               display_identity=b"\x01" * 32, message_uid="ab" * 16, **values)

    def call(identity, command):
        return json.dumps([{"id": identity, "call_id": identity, "type": "function",
                            "function": {"name": "terminal", "arguments": json.dumps({"command": command, "workdir": "/tmp/w/workspace/fixture_project", "timeout": 120})}}])

    insert("sessions", id=OTHER, source="cli", model="other-model", started_at=1.0, system_prompt_hash="e" * 64)
    message(OTHER, "user", 2.0, content="other private prompt")
    insert("sessions", id=session, source="oneshot", model=models[0], started_at=1791347136.5, billing_provider="openai-codex",
           system_prompt_hash=PROMPT_HASH, tool_names="a" * 64, input_tokens=600, output_tokens=60, cache_read_tokens=10, cache_write_tokens=0,
           reasoning_tokens=5, api_call_count=4, model_config=json.dumps({"_usage_anchor": {"prompt_tokens": 9, "base_last_fp": "b" * 64, "base_prefix_fp": "c" * 64}}))
    insert("system_prompts", hash=PROMPT_HASH, prompt=PROMPT)
    for task, model in zip(("", "title_generation"), models):
        insert("session_model_usage", session_id=session, model=model, billing_provider="openai-codex", billing_base_url="https://example.invalid/" + task,
               billing_mode="", task=task, api_call_count=4 if not task else 1, input_tokens=600, output_tokens=60, cache_read_tokens=10,
               cache_write_tokens=0, reasoning_tokens=5)
    r1 = r1 or HELPER.format("inspect") + " && " + HELPER.format("baseline")
    r2 = r2 or SCRIPT + HELPER.format("final")
    message(session, "user", 10.5, content="Requirement R1: run the helper. " + CANARY)
    message(session, "assistant", 11.5, content="", finish_reason="tool_calls", tool_calls=call("call_a", r1), reasoning="plan",
            codex_reasoning_items=json.dumps([{"type": "reasoning", "encrypted_content": "gAAAAsecretpayload", "id": "rs_1"}]))
    message(session, "tool", 12.5, tool_call_id="call_a", tool_name="terminal",
            content=json.dumps({"output": r1_output or (LINE["inspect"] + "\n" + LINE["baseline"]), "exit_code": r1_exit, "error": None}))
    message(session, "assistant", 13.5, content="Baseline fails. " + R1, finish_reason="stop")
    message(session, "user", 20.5, content="Correction R2 supersedes R1 delivery. " + CANARY)
    steps = r2_steps or [("call_b", "terminal", {"command": r2, "workdir": "/tmp/w/workspace/fixture_project", "timeout": 120},
                          json.dumps({"output": LINE["final"], "exit_code": 0, "error": None}))]
    for index, (identity, name, arguments, result) in enumerate(steps):
        calls = json.dumps([{"id": identity, "call_id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}])
        message(session, "assistant", 21.0 + index, content="", finish_reason="tool_calls", tool_calls=calls)
        message(session, "tool", 21.5 + index, tool_call_id=identity, tool_name=name, content=result)
    message(session, "assistant", 29.5, content="Fixed. " + R2, finish_reason="stop")
    if anchor:
        # The usage record of the last request: it names the message before the final response by count and fingerprint.
        last = steps[-1]
        member = {"content": last[3], "role": "tool", "tool_call_id": last[0]}
        fingerprint = __import__("hashlib").sha256(json.dumps(member, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        count = 5 + 2 * len(steps)
        connection.execute("UPDATE sessions SET model_config = ? WHERE id = ?", (json.dumps({"yolo_mode": True, "_usage_anchor": {
            "prompt_tokens": 900, "completion_tokens": 40, "base_count": count, "base_last_role": "tool", "base_last_fp": fingerprint,
            "base_prefix_fp": "c" * 64}}), session))
    if extra:
        extra(connection, insert, message)
    connection.commit()
    connection.close()
    return path


def exports(path, session=SID):
    """(row export bytes, schema export bytes) of one session, as the capture controller writes them."""
    export, schema, _ = export_session_rows(path, session)
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False).encode("utf-8") + b"\n"
    return encode({"schema_version": "session-bench-hermes-session-store-rows-v1", "session_id": session, "stores": [export]}), encode({"stores": [schema]})


def decode(tmp_path, **options):
    version = options.pop("version_extract", None)
    rows, schema = exports(make_store(tmp_path / "state.db", **options))
    return decode_hermes_session_rows(rows, schema, run_canary=CANARY, version_extract=version), rows, schema


def test_rows_give_turns_responses_and_calls_and_no_row_of_another_session(tmp_path):
    decoded, rows, schema = decode(tmp_path)
    assert decoded["status"] == "ok" and decoded["diagnostics"] == [] and decoded["session_id"] == SID and decoded["surface"] == "oneshot"
    last = decoded["responses"][-1]["id"]
    assert b"other private" not in rows and OTHER.encode() not in rows and b"private answers" not in rows
    assert [(row["id"], row["text"][:14]) for row in decoded["turns"]] == [("messages:2", "Requirement R1"), ("messages:6", "Correction R2 ")]
    assert [(row["id"], row["phase"], row["canary"], row["turn_id"], row["timestamp"]) for row in decoded["responses"]] == [
        ("messages:5", "final_answer", R1, "messages:2", 13.5), ("messages:9", "final_answer", R2, "messages:6", 29.5)]
    # No key links a response to its prompt: the relation rests on the row order since the latest user row.
    turns = [row for row in decoded["relations"] if row["kind"] == "turn_response"]
    assert [(row["from_id"], row["to_id"]) for row in turns] == [("messages:2", "messages:5"), ("messages:6", "messages:9")]
    assert all("messages.id" in row["method"] for row in turns)
    # The model is the session row's model, joined by the declared key, and only when every usage row names it.
    assert {row["model_id"] for row in decoded["responses"]} == {"gpt-5.5"} and decoded["responses"][0]["configuration"] == {"provider": "openai-codex"}
    other, _, _ = decode(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, models=("gpt-5.5", "other-model"))
    assert other["status"] == "ok" and other["model"] is None and "model_id" not in other["responses"][0]


def test_compound_call_is_the_record_of_each_helper_and_a_script_edit_is_no_edit_action(tmp_path):
    decoded, _, _ = decode(tmp_path)
    actions = {row["id"]: row for row in decoded["actions"]}
    assert list(actions) == ["call_a:segment-0", "call_a:segment-1", "call_b"]
    # The argv is the command as the row states it, the run canary flag included.
    assert [row["argv"] for row in actions.values()] == [["python3", "bench_check.py", phase, "--run-canary", CANARY] for phase in ("inspect", "baseline", "final")]
    assert [row["action_kind"] for row in actions.values()] == ["inspect", "test", "test"] and actions["call_b"]["cwd"] == "fixture_project"
    assert actions["call_a:segment-1"]["parent_call_id"] == "call_a" and "parent_call_id" not in actions["call_b"]
    results = {row["id"]: row for row in decoded["results"]}
    # An unbroken && chain to a later helper with its own output line proves exit 0. The call status is the last segment's.
    assert [(row.get("exit_code"), row.get("status"), row["output"]) for row in results.values()] == [
        (0, "success", LINE["inspect"]), (1, "failure", LINE["baseline"]), (0, "success", LINE["final"])]
    assert [row["helper_nonce"] for row in results.values()] == ["inspect-fixture-0001", "baseline-fixture-0001", "final-fixture-0001"]
    links = [(row["from_id"], row["to_id"]) for row in decoded["relations"] if row["kind"] == "action_result"]
    assert links == [("call_a:segment-0", "call_a:segment-0:result"), ("call_a:segment-1", "call_a:segment-1:result"), ("call_b", "call_b:result")]
    # The R2 call holds a Python script that writes the file, and the final helper: one scored segment, so one call.
    assert not [row for row in decoded["actions"] if row.get("action_kind") == "edit"] and decoded["file_changes"] == []
    add_native_final_after_chain(decoded, {"turns": [{}, {"response_canary": R2}]})
    assert not [row for row in decoded["relations"] if row["kind"] == "final_after"]
    assert decoded["session_usage"]["session_row"]["input_tokens"] == 600


def test_semicolon_chain_gives_no_exit_code_for_the_first_helper_and_a_wrong_canary_keeps_the_raw_argv(tmp_path):
    command = "pwd; " + HELPER.format("inspect") + "; " + HELPER.format("baseline")
    decoded, _, _ = decode(tmp_path, r1=command)
    first, second = decoded["results"][0], decoded["results"][1]
    assert first["id"] == "call_a:segment-1:result" and "exit_code" not in first and "status" not in first and first["output"] == LINE["inspect"]
    assert second["exit_code"] == 1 and decoded["actions"][0]["segment_index"] == 1
    (tmp_path / "w").mkdir()
    wrong, _, _ = decode(tmp_path / "w", r2=SCRIPT + "python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_other")
    assert wrong["actions"][-1]["argv"][-2:] == ["--run-canary", "SB_SURVIVAL_V1_RUN_other"]
    (tmp_path / "v").mkdir()
    # When the one helper is not the last segment, the call status is not the helper's status.
    early, _, _ = decode(tmp_path / "v", r2=HELPER.format("final") + "\necho done")
    assert "exit_code" not in early["results"][-1] and early["actions"][-1]["argv"][:3] == ["python3", "bench_check.py", "final"]


def test_file_tools_give_an_edit_action_a_changed_file_and_the_final_after_chain(tmp_path):
    decoded, rows, schema = decode(tmp_path, r2_steps=FILE_STEPS, r1_output=INSPECT + "\n" + LINE["baseline"], anchor=True)
    assert decoded["status"] == "ok" and decoded["diagnostics"] == []
    actions = {row["id"]: row for row in decoded["actions"]}
    assert [(row["name"], row.get("action_kind"), row.get("target")) for row in actions.values()][2:] == [
        ("read_file", None, "fixture_project/checkout.py"), ("patch", "edit", "fixture_project/checkout.py"), ("terminal", "test", None)]
    results = {row["action_id"]: row for row in decoded["results"]}
    assert results["call_p"]["status"] == "success" and results["call_p"]["output"] == FILE_STEPS[1][3] and "exit_code" not in results["call_p"]
    assert ("call_p", "call_p:result") in [(row["from_id"], row["to_id"]) for row in decoded["relations"] if row["kind"] == "action_result"]
    # Changed file: the inspect result is the pre-image, the patch arguments are the edit, the native diff must agree.
    change = decoded["file_changes"]
    assert len(change) == 1 and change[0]["path"] == "fixture_project/checkout.py" and change[0]["hash_source"] == "native_preimage_and_edit"
    assert change[0]["before_sha256"] == sha(SOURCE.encode()) and change[0]["after_sha256"] == sha(SOURCE.replace("return 1", "return 2").encode())
    assert change[0]["timestamp"] == results["call_p"]["timestamp"] and change[0]["action_id"] == "call_p"
    assert apply_unified_diff(SOURCE, DIFF) == "def checkout(items):\n    return 2\n" and apply_unified_diff("other\n", DIFF) is None
    # Final after R2: rising row ids of R2, patch call, patch result, final call, final result (exit 0) and response; results bound by call id.
    add_native_final_after_chain(decoded, {"turns": [{}, {"response_canary": R2}]})
    chain = [row for row in decoded["relations"] if row["kind"] == "final_after"]
    assert len(chain) == 1 and chain[0]["to_id"] == "call_b" and chain[0]["native_order_rows"] == sorted(chain[0]["native_order_rows"])
    assert chain[0]["native_call_ids"] == ["call_p", "call_b"] and "tool_call_id" in chain[0]["method"]


@pytest.mark.parametrize("change", [
    lambda steps: [steps[0], (steps[1][0], "patch", {**steps[1][2], "old_string": "missing\n"}, steps[1][3]), steps[2]],          # edit does not fit the pre-image
    lambda steps: [steps[0], (steps[1][0], "patch", steps[1][2], json.dumps({"success": True, "diff": DIFF.replace("return 2", "return 3")})), steps[2]],
    lambda steps: [steps[0], (steps[1][0], "patch", steps[1][2], json.dumps({"error": "no match"})), steps[2]],                      # the edit failed
    lambda steps: [steps[0], (steps[1][0], "patch", {**steps[1][2], "old_string": "    return", "new_string": "    return"}, steps[1][3]), steps[2]],
])
def test_a_patch_that_the_native_values_do_not_prove_gives_no_changed_file(tmp_path, change):
    decoded, _, _ = decode(tmp_path, r2_steps=change(FILE_STEPS), r1_output=INSPECT + "\n" + LINE["baseline"])
    assert decoded["status"] == "ok" and decoded["file_changes"] == []
    (tmp_path / "n").mkdir()
    without, _, _ = decode(tmp_path / "n", r2_steps=FILE_STEPS)        # no inspect source: no pre-image
    assert without["file_changes"] == []


def test_usage_record_of_the_last_request_joins_by_count_role_and_fingerprint(tmp_path):
    decoded, rows, schema = decode(tmp_path, r2_steps=FILE_STEPS, anchor=True)
    final = decoded["responses"][-1]
    assert [(row["response_id"], row["usage"]) for row in decoded["usage"]] == [(final["id"], {"input_tokens": 900, "output_tokens": 40})]
    assert "cache_read_tokens" not in decoded["usage"][0]["usage"] and "base_last_fp" in decoded["usage"][0]["native_key"]
    tables, _, _ = read_rows(rows, schema)
    bound = usage_anchor(tables["sessions"][0]["values"], tables["messages"])
    assert bound["response_row"] == tables["messages"][-1]["rowid"] and bound["base_row"] == tables["messages"][-2]["rowid"]
    # A record that names another message, role or count is not a usage record of any response.
    for damage in (lambda text: text.replace('"base_count": 11', '"base_count": 9'), lambda text: text.replace('"base_last_role": "tool"', '"base_last_role": "user"'),
                   lambda text: text.replace(bound["base_last_fp"], "0" * 64), lambda text: text.replace('"prompt_tokens": 900', '"prompt_tokens": "900"')):
        session = {**tables["sessions"][0]["values"], "model_config": damage(tables["sessions"][0]["values"]["model_config"])}
        assert usage_anchor(session, tables["messages"]) is None
    (tmp_path / "n").mkdir()
    assert decode(tmp_path / "n")[0]["usage"] == []        # the synthetic default anchor binds to no row


def test_a_redirection_write_in_a_compound_call_is_an_edit_action(tmp_path):
    command = "cat > checkout.py <<'EOF'\ndef checkout(items):\n    return 2\nEOF\n" + HELPER.format("final")
    decoded, _, _ = decode(tmp_path, r2=command)
    edit = next(row for row in decoded["actions"] if row.get("action_kind") == "edit")
    assert edit["id"] == "call_b:segment-0" and edit["target"] == "fixture_project/checkout.py"
    # The rows still hold no pre-image with a native diff, so there is no changed-file fact.
    assert decoded["file_changes"] == []


@pytest.mark.parametrize("change, code", [
    (lambda c, insert, message: c.execute("ALTER TABLE messages ADD COLUMN new_column TEXT"), "unknown_column_set"),
    (lambda c, insert, message: c.execute("UPDATE messages SET active = 0 WHERE id = 5"), "malformed_record"),
    (lambda c, insert, message: c.execute("UPDATE messages SET compacted = 1 WHERE id = 3"), "malformed_record"),
    (lambda c, insert, message: c.execute("UPDATE messages SET role = 'system' WHERE id = 2"), "malformed_record"),
    (lambda c, insert, message: c.execute("DELETE FROM messages WHERE id = 8"), "unjoined_execution"),
    (lambda c, insert, message: c.execute("UPDATE messages SET tool_calls = 'not json' WHERE id = 3"), "malformed_record"),
])
def test_a_shape_outside_the_contract_makes_the_decode_unsupported(tmp_path, change, code):
    decoded, _, _ = decode(tmp_path, extra=change)
    assert decoded["status"] == "unsupported" and code in {row["code"] for row in decoded["diagnostics"]}


def test_another_application_id_or_schema_version_is_refused(tmp_path, monkeypatch):
    decoded, _, _ = decode(tmp_path, application_id=7)
    assert decoded["status"] == "unsupported" and decoded["diagnostics"][0]["code"] == "unsupported_version"
    (tmp_path / "a").mkdir()
    store = make_store(tmp_path / "a/state.db")
    rows, schema = exports(store)
    extract = lambda version, **other: canonical({"schema_version": VERSION_EXTRACT_SCHEMA, "store": "state.db", "table": "schema_version",
                                                   "rows": [{"version": version}], "application_id": APPLICATION_ID, "user_version": 0,
                                                   "schema_sha256": store_schema_sha256(schema), **other})
    # Without the extract the declared version is unknown. With it, only the supported value (31) decodes.
    assert decode_hermes_session_rows(rows, schema)["schema_version"] is None and SUPPORTED_SCHEMA_VERSIONS == (31,)
    refused = decode_hermes_session_rows(rows, schema, version_extract=extract(17))
    assert refused["status"] == "unsupported" and refused["schema_version"] == 17
    accepted = decode_hermes_session_rows(rows, schema, version_extract=extract(31))
    assert accepted["status"] == "ok" and accepted["schema_version"] == 31
    assert decode_hermes_session_rows(rows, schema, version_extract=extract(32))["status"] == "unsupported"
    assert decode_hermes_session_rows(rows, schema, version_extract=extract(31, application_id=1))["status"] == "unsupported"
    with pytest.raises(HermesRowsError):
        read_rows(rows.replace(b'"store": "state.db"', b'"store": "other.db"'), schema)


def test_system_prompt_row_is_bound_by_content_and_names_the_harness(tmp_path):
    _, rows, schema = decode(tmp_path)
    assert decode_hermes_session_rows(rows, schema)["harness"] is None
    decoded = decode_hermes_session_rows(rows, schema, prompt_row=prompt_row())
    assert decoded["status"] == "ok" and decoded["harness"] == "Hermes Agent"
    assert read_prompt_row(prompt_row(), PROMPT_HASH) == {"hash": PROMPT_HASH, "prompt": PROMPT, "rowid": 7}
    # A prompt without the harness sentence names no harness; nothing else is read from the prompt.
    other = "Be direct.\n"
    digest = __import__("hashlib").sha256(other.encode()).hexdigest()
    plain = decode_hermes_session_rows(rows.replace(PROMPT_HASH.encode(), digest.encode()), schema, prompt_row=prompt_row(other))
    assert plain["status"] == "ok" and plain["harness"] is None
    # Another row, a changed text or a hash that is not the SHA-256 of the text is refused.
    for document in (prompt_row(other), prompt_row(PROMPT + "x", digest=PROMPT_HASH), prompt_row(named="0" * 64),
                     prompt_row().replace(b"system_prompts", b"other_table")):
        refused = decode_hermes_session_rows(rows, schema, prompt_row=document)
        assert refused["status"] == "unsupported" and refused["harness"] is None and refused["diagnostics"][0]["code"] == "invalid_boundary"
    with pytest.raises(HermesRowsError):
        read_prompt_row(prompt_row(), "0" * 64)
    # Density: the prompt row is one system record; it states no event unless it holds a whole user prompt.
    native = tmp_path / "native"
    (native / "capture").mkdir(parents=True)
    files = {ROWS_FILE: rows, SCHEMA_FILE: schema, PROMPT_FILE: prompt_row()}
    for name, data in files.items():
        (native / "capture" / name).write_bytes(data)
    artifacts = [{"path": "capture/" + name, "sha256": sha(data)} for name, data in files.items()]
    records, _, exceptions, occurrences = read_hermes_family(native, artifacts=artifacts)
    system = [row for row in records if row["record_kind"] == "system"]
    assert exceptions == [] and len(system) == 1 and system[0]["classification"] == "unclassified" and len(occurrences) == 8
    assert system[0]["logical_bytes"] == len(json.dumps({"hash": PROMPT_HASH, "prompt": PROMPT}, sort_keys=True, separators=(",", ":")).encode())
    tables, _, _ = read_rows(rows, schema)
    holds = PROMPT + tables["messages"][0]["values"]["content"]
    digest = __import__("hashlib").sha256(holds.encode()).hexdigest()
    (native / "capture" / PROMPT_FILE).write_bytes(prompt_row(holds))
    (native / "capture" / ROWS_FILE).write_bytes(rows.replace(PROMPT_HASH.encode(), digest.encode()))
    artifacts = [{"path": "capture/" + name, "sha256": sha((native / "capture" / name).read_bytes())} for name in files]
    assert [event for event, _ in read_hermes_family(native, artifacts=artifacts)[3]].count("message:2") == 2


def test_version_extract_reads_one_row_from_a_deleted_copy(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    make_store(home / "state.db")
    _, schema = exports(home / "state.db")
    before = (home / "state.db").stat()
    document = extractor.extract(home, tmp_path / "out", state_meta_keys=True)
    assert document["rows"] == [{"version": 17}] and document["schema_sha256"] == store_schema_sha256(schema)
    assert document["state_meta_keys"] == [{"key": "fts_high_water", "value_type": "text", "value_length": 1}]
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == ["state-db-schema-version.json"]
    raw = (tmp_path / "out/state-db-schema-version.json").read_bytes()
    assert b"private answers" not in raw and b"other private" not in raw and SID.encode() not in raw and OTHER.encode() not in raw
    after = (home / "state.db").stat()
    assert (before.st_mtime_ns, before.st_size, before.st_ino) == (after.st_mtime_ns, after.st_size, after.st_ino)
    with pytest.raises(ValueError, match="new output"):
        extractor.extract(home, tmp_path / "out")


def test_each_event_is_stated_once_and_density_counts_the_session_rows(tmp_path):
    decoded, rows, schema = decode(tmp_path)
    tables, _, _ = read_rows(rows, schema)
    record_ids, roles, statements = message_statements(tables["messages"])
    assert roles == ["user_message", "tool_call", "tool_result", "assistant_message"] * 2
    assert statements == [("message:2",), ("call:call_a",), ("result:call_a",), ("message:5",), ("message:6",), ("call:call_b",), ("result:call_b",), ("message:9",)]
    (tmp_path / "f").mkdir()
    _, file_rows, file_schema = decode(tmp_path / "f", r2_steps=FILE_STEPS)
    # A read result and a patch result hold part of a call's arguments at most: neither restates its call.
    assert [events for events in message_statements(read_rows(file_rows, file_schema)[0]["messages"])[2][5:11]] == [
        ("call:call_r",), ("result:call_r",), ("call:call_p",), ("result:call_p",), ("call:call_b",), ("result:call_b",)]
    native = tmp_path / "native"
    (native / "capture").mkdir(parents=True)
    (native / "capture" / ROWS_FILE).write_bytes(rows); (native / "capture" / SCHEMA_FILE).write_bytes(schema)
    artifacts = [{"path": "capture/" + name, "sha256": sha(data)} for name, data in ((ROWS_FILE, rows), (SCHEMA_FILE, schema))]
    records, locators, exceptions, occurrences = read_hermes_family(native, artifacts=artifacts)
    assert exceptions == [] and len(records) == 1 + 2 + 8 and len(locators) == len(records)
    kinds = [row["record_kind"] for row in records]
    assert kinds[:3] == ["session", "metadata", "metadata"] and "snapshot" not in kinds and "unknown" not in kinds
    assert [event for event, _ in occurrences] == [event for events in statements for event in events]
    # A BLOB counts by its raw length; the schema of the whole store is no record of the session.
    first = tables["messages"][0]["values"]
    assert row_bytes(first) == len(json.dumps({**first, "display_identity": ""}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) + 32
    assert sum(row["logical_bytes"] for row in records) == sum(row_bytes(row["values"]) for rows_of in tables.values() for row in rows_of)
    with pytest.raises(ValueError, match="differs"):
        read_hermes_family(native, artifacts=[{**artifacts[0], "sha256": "0" * 64}, artifacts[1]])


def test_absence_scan_and_loss_control(tmp_path):
    decoded, rows, schema = decode(tmp_path)
    assert usage_key_findings(rows, schema) == []
    (tmp_path / "t").mkdir()
    counted, counted_rows, counted_schema = decode(tmp_path / "t", extra=lambda c, insert, message: c.execute("UPDATE messages SET token_count = 12 WHERE id = 5"))
    assert usage_key_findings(counted_rows, counted_schema) == ["messages:5:token_count"]
    damaged, removed = remove_final_response(rows, R2)
    assert len(removed) == 1 and removed[0]["rowid"] == 9 and R2.encode() not in damaged and R1.encode() in damaged
    assert len(decode_hermes_session_rows(damaged, schema, run_canary=CANARY)["responses"]) == 1
    with pytest.raises(ValueError, match="one final response"):
        remove_final_response(rows, "SB_SURVIVAL_V1_RESPONSE_absent")
    # The absence rows need a clean decode, an empty scan and a proving receipt; anything less stays unresolved.
    measurement = {"metrics": [{"id": name, "state": "unresolved", "correct": 0, "observed_eligible": 1, "decoded_eligible": 0}
                               for name in ("attribution.usage", "attribution.token_semantics", "attribution.reconciliation",
                                            "work.changed_files", "revision.final_after_r2", "work.actions")]}
    states = lambda *arguments: {row["id"]: row["state"] for row in apply_hermes_native_absence(measurement, *arguments)["metrics"]}
    proved = states(decoded, {"proved": True, "reasons": []}, [])
    assert proved.pop("work.actions") == "unresolved" and set(proved.values()) == {"native_absent"}
    # With the usage record of one response: usage is measured, token semantics is absent (no cache key on the record), and a
    # changed file or an edit chain that the rows hold is not turned into an absence.
    joined = {"metrics": [{"id": "attribution.usage", "state": "measured", "correct": 1, "observed_eligible": 2, "decoded_eligible": 1},
                          {"id": "attribution.token_semantics", "state": "unresolved", "correct": 0, "observed_eligible": 2, "decoded_eligible": 1},
                          {"id": "attribution.reconciliation", "state": "unresolved", "correct": 0, "observed_eligible": 1, "decoded_eligible": 0}]}
    after = {row["id"]: row["state"] for row in apply_hermes_native_absence(joined, {**decoded, "usage": [{"usage": {}}]}, {"proved": True, "reasons": []}, [])["metrics"]}
    assert after == {"attribution.usage": "measured", "attribution.token_semantics": "native_absent", "attribution.reconciliation": "native_absent"}
    unproved = apply_hermes_native_absence(joined, decoded, {"proved": False, "reasons": ["x"]}, [])["metrics"]
    assert [row["state"] for row in unproved] == ["measured", "unresolved", "unresolved"]
    assert set(states(decoded, {"proved": False, "reasons": ["x"]}, []).values()) == {"unresolved"}
    assert set(states(decoded, {"proved": True, "reasons": []}, ["messages:5:token_count"]).values()) == {"unresolved"}
    assert set(states({**decoded, "diagnostics": [{"code": "malformed_record"}]}, {"proved": True, "reasons": []}, []).values()) == {"unresolved"}
    assert set(states({**decoded, "status": "unsupported"}, {"proved": True, "reasons": []}, []).values()) == {"unresolved"}
    tables, _, _ = read_rows(rows, schema)
    assert required_companions(tables) == ["capture/session-store-schema.json", "capture/system-prompt-row.json"]


# --- The three captured runs and their private packets (skipped when the private artifacts are absent) ---

real = pytest.mark.skipif(not (PACKETS / "summary.json").is_file(), reason="private Hermes packets are not in this checkout")


@pytest.fixture(scope="module")
def summary():
    return json.loads((PACKETS / "summary.json").read_bytes())


def _documents(run):
    documents = {name: (CAPTURES / run / name).read_bytes() for name in STREAM_CAPTURE_DOCUMENTS if name != VERSION_DOCUMENT}
    documents[VERSION_DOCUMENT] = canonical(json.loads(VERSION_EXTRACT.read_bytes()))
    return documents


def _decoded(run):
    capture = CAPTURES / run
    rows, schema = ((capture / "r2-native" / name).read_bytes() for name in (ROWS_FILE, SCHEMA_FILE))
    prompt = canonical(json.loads((capture / "shared-store-extract/system-prompt-row.json").read_bytes())) + b"\n"
    return decode_hermes_session_rows(rows, schema, version_extract=VERSION_EXTRACT.read_bytes(), prompt_row=prompt), rows, schema


@real
@pytest.mark.parametrize("run", RUNS)
def test_observer_comes_from_the_stream_and_unscored_native_calls_leave_the_pool(run):
    documents = _documents(run)
    observer = stream_observer_from_capture_documents(documents)
    scored = [row for row in observer["events"] if row["kind"] == "action" and row["population_role"] == "primary_scored"]
    assert [row["fields"]["action_kind"] for row in scored] == ["inspect", "test", "edit", "test"] and {row["source"] for row in scored} == {"harness_stdout"}
    assert not [name for name in STREAM_CAPTURE_DOCUMENTS if "native/" in name or name.endswith(("rows.json", "schema.json"))]
    unscored = [row["fields"] for row in observer["events"] if row["kind"] == "action" and row["population_role"] == "unscored"]
    assert [row["name"] for row in unscored] == (["search_files", "search_files", "read_file"] if run.endswith("-09") else ["read_file"])
    decoded, _, _ = _decoded(run)
    pool = scored_pool(decoded, observer)
    # The observed read and search calls pair with the native calls of the same turn, tool and arguments, and leave the pool.
    assert len(decoded["actions"]) - len(pool["actions"]) == len(unscored) == len(decoded["results"]) - len(pool["results"])
    assert [row["name"] for row in pool["actions"]] == ["terminal", "terminal", "patch", "terminal"]
    assert not {row["name"] for row in pool["actions"]} & {"read_file", "search_files"}
    # A native call that the observer did not see stays and enlarges the denominator.
    extra = {**decoded["actions"][-1], "id": "call_extra", "call_id": "call_extra", "name": "read_file", "input": {"path": "x"}}
    assert len(scored_pool({**decoded, "actions": decoded["actions"] + [extra]}, observer)["actions"]) == 5
    with pytest.raises(ValueError):
        stream_observer_from_capture_documents({**documents, "turn-r2/stdout.jsonl": documents["turn-r2/stdout.jsonl"] + b"not json\n"})


@real
def test_31_metric_rows_of_each_run(summary):
    assert [row["unresolved_metric_ids"] for row in summary["runs"]] == [[]] * 3 and [row["resolved_metric_count"] for row in summary["runs"]] == [31] * 3
    wanted = {"attribution.usage": ("measured", 1, 2, 1), "attribution.token_semantics": ("native_absent", 0, 2, 1),
              "attribution.reconciliation": ("native_absent", 0, 1, 0), "portable.complete_root": ("contradiction", 0, 1, 1)}
    for row in summary["runs"]:
        receipt = json.loads((PACKETS / f"{row['packet']}-receipt.json").read_bytes())
        intact = receipt["diagnostics"]["intact"]
        metrics = {item["id"]: (item["state"], item["correct"], item["observed_eligible"], item["decoded_eligible"]) for item in intact["metrics"]}
        assert len(metrics) == 31 and {name: metrics[name] for name in wanted} == wanted and intact["observer_mode"] == "hermes-stream-capture-v1"
        rest = {name: value for name, value in metrics.items() if name not in wanted and name != "broad.classified_content_density"}
        assert all(state == "measured" and correct == observed == decoded for state, correct, observed, decoded in rest.values())
        assert metrics["work.actions"] == ("measured", 4, 4, 4) and metrics["work.changed_files"] == ("measured", 1, 1, 1)
        assert metrics["revision.final_after_r2"] == ("measured", 1, 1, 1) and metrics["broad.event_timestamps"] == ("measured", 13, 13, 13)
        state, useful, total, _ = metrics["broad.classified_content_density"]
        assert state == "measured" and 0.60 < useful / total < 0.69
        evidence = intact["format_evidence"]
        broad = evidence["profile"]["broad_evidence"]
        assert broad["broad.self_contained_identity"]["harness"] == "Hermes Agent" and broad["broad.declared_format_version"]["format_version"] == "state.db schema_version=31"
        # The version rows cite the version extract, not the native inventory.
        cited = {item["metric_id"]: [locator["id"] for locator in item["native_locators"]] for item in evidence["metric_evidence"]}
        assert cited["broad.declared_format_version"] == cited["broad.honest_version_signal"] == ["inputs/capture/shared-store-extract/state-db-schema-version.json"]
        assert broad["broad.declared_format_version"]["bundle_binding"] == "inputs/capture/shared-store-extract/state-db-schema-version.json"
        events = broad["broad.naive_reader_duplicate_safety"]
        assert len(events["event_ids"]) == len(events["forward_records"]) and [item["record_kind"] for item in broad["broad.classified_content_density"]["records"]].count("system") == 1
        assert row["os_sandboxed"] is True and row["tamper_controls"] == "passed" and row["selected_loss_detected"] is True
        check = row["exporter_cross_check"]
        assert check["differences"] == [] and check["session_id_equal"] is True and check["message_rows"] == check["exporter_messages"]


@real
def test_packet_replays_and_rejects_changed_rows_streams_receipts_and_claims(summary):
    row = summary["runs"][0]
    packet = PACKETS / row["packet"]
    receipt = replay_score_package(packet, expected_manifest_sha256=row["manifest_sha256"], os_sandboxed=False)
    assert receipt["diagnostics_sha256"] == row["diagnostics_sha256"]
    assert verify_score_packet_tamper_controls(packet, expected_manifest_sha256=row["manifest_sha256"])["status"] == "passed"
    contents = {path.relative_to(packet).as_posix(): path.read_bytes() for path in packet.rglob("*") if path.is_file()}
    assert "runtime/session_bench/hermes_stream_observer.py" in contents and not [name for name in contents if name.endswith("session.jsonl")]
    assert {"inputs/capture/turn-r1/stdout.jsonl", "inputs/capture/turn-r2/stdout.jsonl", "native/capture/" + PROMPT_FILE} <= set(contents)

    def repin(changed):
        manifest = json.loads(changed["manifest.json"])
        for entry in manifest["files"]:
            entry.update(sha256=sha(changed[entry["path"]]), size_bytes=len(changed[entry["path"]]))
        return {**changed, "manifest.json": canonical(manifest) + b"\n"}

    validate_score_packet(repin(contents))
    rows_name, prompt_name = "native/capture/" + ROWS_FILE, "native/capture/" + PROMPT_FILE
    extract_name, stream_name = "inputs/capture/shared-store-extract/state-db-schema-version.json", "inputs/capture/turn-r2/stdout.jsonl"
    extract = json.loads(contents[extract_name])
    other = (PACKETS / summary["runs"][1]["packet"] / prompt_name).read_bytes()
    for changed in ({rows_name: contents[rows_name].replace(b'"role": "tool"', b'"role": "user"', 1)},
                    {prompt_name: contents[prompt_name].replace(b"You are Hermes Agent", b"You are Another Agent")}, {prompt_name: other},
                    {extract_name: canonical({**extract, "schema_sha256": "0" * 64})},
                    {stream_name: contents[stream_name].replace(b'"name": "patch"', b'"name": "other"')},        # the observer no longer matches the stream
                    {stream_name: contents[stream_name] + b"not json\n"}):
        with pytest.raises(ValueError):
            validate_score_packet(repin({**contents, **changed}))
    context = json.loads(contents["inputs/context.json"])
    assert context["complete_root"] is False and context["required_companions"] == ["capture/session-store-schema.json", "capture/system-prompt-row.json"]
    with pytest.raises(ValueError, match="unproven|cannot be declared"):
        validate_score_packet(repin({**contents, "inputs/context.json": canonical({**context, "complete_root": True})}))
    name = "inputs/capture/" + RECEIPT
    state = json.loads(contents[name])
    for change in (lambda value: value["classes"]["shared_changed"].pop(), lambda value: value.update(preexisting_contents_opened=True),
                   lambda value: value["session_store"]["stores"][0]["tables"][0].update(selected_rows=1)):
        changed = json.loads(contents[name]); change(changed)
        with pytest.raises(ValueError):
            validate_score_packet(repin({**contents, name: canonical(changed) + b"\n"}))
    # The absence proof: the unread changed files are the store, process logs and three named process-state files.
    before, after = (json.loads(contents["inputs/capture/" + item]) for item in ("state-before.json", "r2-state-after.json"))
    classes = verify_state_receipt(state, before, after)
    tables, _, _ = read_rows(contents[rows_name], contents["native/capture/" + SCHEMA_FILE])
    assert absence_findings(state, classes, tables) == {"proved": True, "reasons": []}
    files = {item["relative_path"] for item in classes["shared_changed"] if item["kind"] == "file"}
    assert {"runtime/active_sessions.json", "skills/.bundled_manifest", "spawn-ledger.json", "logs/agent.log", "state.db"} <= files
    unread = {**classes, "shared_changed": classes["shared_changed"] + [{"relative_path": "kanban.db", "kind": "file", "after": {"size_bytes": 4096}}]}
    assert absence_findings(state, unread, tables)["reasons"] == ["a changed shared file was not read: kanban.db"]
    small = {**classes, "shared_changed": classes["shared_changed"] + [{"relative_path": "marker", "kind": "file", "after": {"size_bytes": 3}}]}
    assert absence_findings(state, small, tables)["proved"] is True      # a file smaller than the session id cannot name it


@real
@pytest.mark.parametrize("run", RUNS)
def test_real_rows_give_one_usage_record_a_changed_file_and_the_final_chain(run):
    decoded, rows, schema = _decoded(run)
    workload = json.loads((CAPTURES / run / "workload-instance.json").read_bytes())
    add_native_final_after_chain(decoded, workload)
    assert decoded["status"] == "ok" and decoded["diagnostics"] == [] and usage_key_findings(rows, schema) == []
    assert decoded["schema_version"] == 31 and decoded["harness"] == "Hermes Agent" and decoded["model"] == "gpt-5.5" and decoded["surface"] == "oneshot"
    # One usage record: the last request of the session, joined to the final response of turn 2. No cache key on it.
    final = [row for row in decoded["responses"] if row["phase"] == "final_answer"][-1]
    assert [row["response_id"] for row in decoded["usage"]] == [final["id"]] and set(decoded["usage"][0]["usage"]) == {"input_tokens", "output_tokens"}
    totals = decoded["session_usage"]["session_row"]
    assert decoded["usage"][0]["usage"]["input_tokens"] < totals["input_tokens"] + totals["cache_read_tokens"] and totals["api_call_count"] >= 6
    # The first-turn copy held the record of turn 1; the final store does not: each turn overwrites it.
    capture = CAPTURES / run
    earlier, _, _ = read_rows((capture / "r1-native" / ROWS_FILE).read_bytes(), (capture / "r1-native" / SCHEMA_FILE).read_bytes())
    first = usage_anchor(earlier["sessions"][0]["values"], earlier["messages"])
    assert first is not None and first["response_row"] == earlier["messages"][-1]["rowid"] != int(final["id"].split(":")[1])
    change = decoded["file_changes"]
    assert len(change) == 1 and change[0]["before_sha256"] == sha((capture / "workspaces/before/fixture_project/checkout.py").read_bytes())
    assert change[0]["after_sha256"] == sha((capture / "turn-r2/workspace/fixture_project/checkout.py").read_bytes())
    assert len([row for row in decoded["relations"] if row["kind"] == "final_after"]) == 1
    tables, _, _ = read_rows(rows, schema)
    assert all(row["values"]["token_count"] is None for row in tables["messages"])
    receipt = json.loads((capture / "r2-native-receipt.json").read_bytes())
    assert store_schema_sha256(schema) == receipt["session_store"]["stores"][0]["schema_sha256"] == json.loads(VERSION_EXTRACT.read_bytes())["schema_sha256"]
