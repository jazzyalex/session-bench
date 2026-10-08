"""Three fresh-root Copilot captures resolve all 31 metrics from native bytes and receipts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from session_bench.copilot_density import copilot_event_role, read_copilot_family
from session_bench.copilot_format_evidence import copilot_forward_occurrences, copilot_thread_turns
from session_bench.copilot_live import decode_copilot_native_bytes
from session_bench.copilot_session_store import (
    SessionStoreError, attach_store_usage, read_session_store, rewrite_wal_checksums, wal_valid_frames,
)
from session_bench.copilot_score_inputs import (
    ROOT_DOCUMENTS, apply_copilot_native_absence, apply_copilot_patch, qualify_fresh_root,
    _add_native_snapshot_change,
)
from session_bench.score_replay import replay_score_package, validate_score_packet
from session_bench.native_replay import _snapshot_tree, canonical

ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "artifacts/v1-expanded-preparation/live-captures"
RUNS = ("copilot-2026-10-04-01", "copilot-2026-10-04-02", "copilot-2026-10-04-03")
spec = importlib.util.spec_from_file_location("copilot_score_builder", ROOT / "scripts/build_copilot_score_replays.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def containses(forward, event):
    """Container prefixes of the occurrences of one event."""
    return sorted({item["occurrence_id"].split(":")[0].rstrip("0123456789abcdef") for item in forward if item["event_id"] == event})


def native_dir(run):
    session = json.loads((CAPTURES / run / "attempt.json").read_bytes())["session_id"]
    return CAPTURES / run / "native" / session


def root_documents(run):
    return {name: (CAPTURES / run / name).read_bytes() for name in ROOT_DOCUMENTS}


@pytest.fixture(scope="module")
def packets(tmp_path_factory):
    output = tmp_path_factory.mktemp("copilot") / "packets"
    return output, builder.build(output)


def test_all_31_metrics_resolve_with_honest_absences(packets):
    output, summary = packets
    assert [row["resolved_metric_count"] for row in summary["runs"]] == [31, 31, 31]
    assert all(row["os_sandboxed"] and row["tamper_controls"] == "passed" and row["selected_loss_detected"] for row in summary["runs"])
    for row in summary["runs"]:
        receipt = json.loads((output / f'{row["packet"]}-receipt.json').read_bytes())
        metrics = {item["id"]: item for item in receipt["diagnostics"]["intact"]["metrics"]}
        assert len(metrics) == 31
        # Usage comes from the session store rows joined to the two displayed responses.
        for name in ("attribution.usage", "attribution.token_semantics", "attribution.model_config"):
            assert metrics[name] == {"id": name, "state": "measured", "correct": 2, "observed_eligible": 2, "decoded_eligible": 2}
        assert metrics["attribution.reconciliation"] == {"id": "attribution.reconciliation", "state": "measured", "correct": 1, "observed_eligible": 1, "decoded_eligible": 1}
        context = json.loads((output / row["packet"] / "inputs/context.json").read_bytes())
        assert {"session-store.db", "session-store.db-wal"} <= set(context["required_companions"])
        # With the final response lost, its usage row no longer joins and nothing reconciles.
        damaged = {item["id"]: item for item in receipt["diagnostics"]["selected_loss"]["damaged"]["metrics"]}
        assert damaged["attribution.usage"]["correct"] == 1 and damaged["attribution.reconciliation"]["correct"] == 0
        for name in ("portable.complete_root", "portable.companions", "work.changed_files", "broad.stable_root_location",
                     "broad.thread_structure", "broad.declared_format_version", "broad.honest_version_signal"):
            assert (metrics[name]["state"], metrics[name]["correct"]) == ("measured", 1)
        assert (metrics["broad.event_timestamps"]["correct"], metrics["broad.event_timestamps"]["observed_eligible"]) == (13, 13)
        duplicate = metrics["broad.naive_reader_duplicate_safety"]
        # Each tool call has two active occurrences, so it cannot pass.
        assert duplicate["decoded_eligible"] > duplicate["observed_eligible"] > duplicate["correct"] > 0
        # The read is every container the decoder takes a scored fact from: events.jsonl, the session
        # store (usage) and the rewind snapshot index (changed file). A copy of a message there is an occurrence.
        forward = receipt["diagnostics"]["intact"]["format_evidence"]["profile"]["broad_evidence"]["broad.naive_reader_duplicate_safety"]["forward_records"]
        containers = lambda event: sorted({item["occurrence_id"].split(":")[0] for item in forward if item["event_id"] == event})
        counts = {}
        for item in forward:
            counts[item["event_id"]] = counts.get(item["event_id"], 0) + 1
        twice = {event: containers(event) for event, count in counts.items() if count == 2 and event.startswith("message:")}
        # The R2 prompt is also in the rewind index. R1 prompt and R1 response are also in the store turns row and in the
        # search index content cell: three statements each.
        assert sorted(sorted(part.rstrip("0123456789") for part in value) for value in twice.values()) == [["line-", "rewind-file-snapshots/index.json"]]
        thrice = {event: containers(event) for event, count in counts.items() if count == 3 and event.startswith("message:")}
        assert sorted(sorted(part.rstrip("0123456789") for part in value) for value in thrice.values()) == [["line-", "session-store.db"]] * 2
        assert all(count >= 2 for event, count in counts.items() if event.startswith("call:")) and max(counts.values()) <= 3
        # A backup file holds the full output of the view result: it restates that result.
        restated = [event for event, count in counts.items() if count == 2 and event.startswith("result:")]
        assert len(restated) == 1 and containses(forward, restated[0]) == ["line-", "rewind-file-snapshots/backups/"]
        # In run 3 the completion record of the glob call holds both arguments of that call: a third statement.
        # The completion of a view names the tool and the path: a third statement of each view call.
        assert sorted(event.split(":")[0] for event, count in counts.items() if count == 3 and not event.startswith("message:")) == ["call"] * (2 if row["packet"].endswith("-03") else 1)
        assert duplicate["correct"] == sum(1 for count in counts.values() if count == 1) and duplicate["decoded_eligible"] == len(forward)
        assert (duplicate["correct"], duplicate["decoded_eligible"]) == {"copilot-2026-10-04-01": (7, 28), "copilot-2026-10-04-02": (8, 29), "copilot-2026-10-04-03": (6, 30)}[row["packet"]]
        density = metrics["broad.classified_content_density"]
        assert 0 < density["correct"] < density["observed_eligible"] == density["decoded_eligible"]
        # In density a record of the read that restates a stated message is a snapshot.
        records = receipt["diagnostics"]["intact"]["format_evidence"]["profile"]["broad_evidence"]["broad.classified_content_density"]["records"]
        kinds = {item["record_id"]: item["record_kind"] for item in records}
        assert {kind for name, kind in kinds.items() if name.startswith("session-store.db:turns:")} == {"snapshot"}
        assert kinds["rewind-file-snapshots/index.json"] == "snapshot"


def test_packet_rejects_a_changed_native_companion_or_root_receipt(packets, monkeypatch):
    output, summary = packets
    packet = output / summary["runs"][0]["packet"]
    contents = _snapshot_tree(packet)
    validate_score_packet(dict(contents))

    def rebuilt(changes):
        # Recompute the inventory so only the semantic gate can refuse.
        changed = {**contents, **changes}
        manifest = json.loads(changed["manifest.json"])
        for entry in manifest["files"]:
            data = changed[entry["path"]]
            entry.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
        changed["manifest.json"] = canonical(manifest) + b"\n"
        return changed

    with pytest.raises(ValueError):
        validate_score_packet(rebuilt({"native/workspace.yaml": contents["native/workspace.yaml"] + b"x: y\n"}))
    end = json.loads(contents["inputs/capture/root-end.json"])
    end["copied_session_inventory"]["events.jsonl"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_score_packet(rebuilt({"inputs/capture/root-end.json": json.dumps(end).encode()}))
    monkeypatch.setattr("session_bench.score_replay.subprocess.run", lambda *args, **kwargs: pytest.fail("unverified packet code ran"))
    stream = packet / "inputs/root-repetitions/repetition-2/root-end.json"
    original = stream.read_bytes()
    try:
        stream.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="inventory mismatch"):
            replay_score_package(packet, expected_manifest_sha256=summary["runs"][0]["manifest_sha256"])
    finally:
        stream.write_bytes(original)


def test_context_cannot_claim_a_root_or_rows_the_receipts_do_not_prove(packets):
    output, summary = packets
    contents = _snapshot_tree(output / summary["runs"][1]["packet"])

    def with_context(update):
        context = json.loads(contents["inputs/context.json"])
        update(context)
        changed = {**contents, "inputs/context.json": canonical(context)}
        manifest = json.loads(changed["manifest.json"])
        for entry in manifest["files"]:
            if entry["path"] == "inputs/context.json":
                entry.update(sha256=hashlib.sha256(changed[entry["path"]]).hexdigest(), size_bytes=len(changed[entry["path"]]))
        changed["manifest.json"] = canonical(manifest) + b"\n"
        return changed

    with pytest.raises(ValueError, match="stable-root"):
        validate_score_packet(with_context(lambda c: c["root_repetitions"][0].update(root_locator="somewhere else")))
    with pytest.raises(ValueError, match="companion set"):
        validate_score_packet(with_context(lambda c: c.update(required_companions=[])))


@pytest.mark.parametrize("run", RUNS)
def test_fresh_root_receipts_bind_the_copied_directory(run):
    fresh = qualify_fresh_root(root_documents(run))
    native = native_dir(run)
    store = CAPTURES / run / "native-store"
    assert fresh["store"] == {name: hashlib.sha256((store / name).read_bytes()).hexdigest() for name in ("session-store.db", "session-store.db-wal")}
    actual = {path.relative_to(native).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in native.rglob("*") if path.is_file()}
    assert fresh["family"] == actual and "events.jsonl" in actual
    assert fresh["run_id"] == run and fresh["cli_version"] == "1.0.91"


@pytest.mark.parametrize("mutation", ["home_not_empty", "copy_differs", "written_after_exit", "written_between_turns",
                                      "second_session", "other_home", "other_session_flag", "failed_turn",
                                      "store_written_after_exit", "store_missing"])
def test_fresh_root_rejects_unproved_isolation_quiescence_or_copy(mutation):
    documents = root_documents(RUNS[0])
    value = {name: json.loads(data) for name, data in documents.items()}
    session = value["attempt.json"]["session_id"]
    key = f"session-state/{session}/events.jsonl"
    if mutation == "home_not_empty":
        value["root-start.json"]["copilot_home_inventory_before"] = {"session-store.db": {"sha256": "1" * 64}}
    elif mutation == "copy_differs":
        value["root-end.json"]["copied_session_inventory"]["events.jsonl"]["sha256"] = "1" * 64
    elif mutation == "written_after_exit":
        value["capture/r2.exit.json"]["copilot_home_inventory_after"][key]["sha256"] = "1" * 64
    elif mutation == "written_between_turns":
        value["capture/r2.launch.json"]["copilot_home_inventory_before"][key]["sha256"] = "1" * 64
    elif mutation == "second_session":
        value["root-end.json"]["copilot_home_inventory_after"]["session-state/another-session/events.jsonl"] = {"sha256": "1" * 64}
    elif mutation == "other_home":
        value["capture/r1.launch.json"]["safe_environment"]["COPILOT_HOME"] = "/elsewhere/copilot"
    elif mutation == "other_session_flag":
        value["capture/r2.launch.json"]["argv"][-1] = "another-session"
    elif mutation == "store_written_after_exit":
        value["capture/r2.exit.json"]["copilot_home_inventory_after"]["session-store.db-wal"]["sha256"] = "1" * 64
    elif mutation == "store_missing":
        del value["root-end.json"]["copilot_home_inventory_after"]["session-store.db"]
    else:
        value["capture/r2.exit.json"]["returncode"] = 1
    with pytest.raises(ValueError):
        qualify_fresh_root({name: json.dumps(item).encode() for name, item in value.items()})


def test_schema_version_is_read_from_the_native_line_and_bound_to_the_decoder():
    raw = (native_dir(RUNS[0]) / "events.jsonl").read_bytes()
    decoded = decode_copilot_native_bytes(raw)
    assert (decoded["status"], decoded["native_schema_version"], decoded["producer"]) == ("ok", 1, "copilot-agent")
    lines = raw.split(b"\n")
    start = json.loads(lines[0])
    start["data"]["version"] = 2
    other = decode_copilot_native_bytes(b"\n".join([json.dumps(start).encode(), *lines[1:]]))
    assert other["status"] == "unsupported" and other["native_schema_version"] == 2
    del start["data"]["version"]
    missing = decode_copilot_native_bytes(b"\n".join([json.dumps(start).encode(), *lines[1:]]))
    assert missing["status"] == "unsupported" and missing["native_schema_version"] is None


def store_bytes(run):
    # Bytes only. The decoder opens a private copy, never these files.
    return tuple((CAPTURES / run / "native-store" / name).read_bytes() for name in ("session-store.db", "session-store.db-wal"))


def decoded_with_store(run, mutate=None):
    decoded = decode_copilot_native_bytes((native_dir(run) / "events.jsonl").read_bytes())
    store = read_session_store(*store_bytes(run))
    if mutate is not None:
        mutate(store, decoded)
    attach_store_usage(decoded, store)
    return decoded, store


@pytest.mark.parametrize("run", RUNS)
def test_store_usage_joins_by_turn_index_and_order_and_reconciles_every_field(run):
    decoded, store = decoded_with_store(run)
    rows = store["tables"]["assistant_usage_events"]
    messages = [row for row in decoded["records"] if row["raw"]["type"] == "assistant.message"]
    assert store["schema_version"] == 8 and len(rows) == len(messages) == len(decoded["usage_records"])
    assert [item["response_event_id"] for item in decoded["usage_records"]] == [row["raw"]["id"] for row in messages]
    assert decoded["usage"] == [] and len(decoded["prompt_cache_fragments"]) == 2
    displayed = [row for row in decoded["responses"] if "canary" in row]
    assert len(displayed) == 2
    for response in displayed:
        record, = [item for item in decoded["usage_records"] if item["response_event_id"] == response["id"]]
        assert response["usage"] == record["usage"] and record["finish_reason"] == "stop"
        assert all(type(response["usage"][key]) is int for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"))
        # The prompt-cache state of the same call, joined by apiCallId, has equal counts.
        assert record["prompt_cache_state_equal"] is True
    assert not any("usage" in row for row in decoded["responses"] if "canary" not in row)
    # The first call of a session reads no cache: an integer 0, not a missing value.
    assert decoded["usage_records"][0]["usage"]["cache_read_tokens"] == 0
    verdict, = decoded["reconciliation"]
    assert verdict["matches_session_totals"] is True and verdict["record_count"] == len(rows)
    fields = {item["field"].split(":", 1)[1] for item in verdict["checks"] if item["field"].startswith("session.shutdown[last]")}
    assert fields == {"gpt-6-luna:requests.count", "gpt-6-luna:usage.inputTokens", "gpt-6-luna:usage.outputTokens",
                      "gpt-6-luna:usage.cacheReadTokens", "gpt-6-luna:usage.cacheWriteTokens", "gpt-6-luna:usage.reasoningTokens",
                      "gpt-6-luna:totalNanoAiu", "tokenDetails.input.tokenCount", "tokenDetails.output.tokenCount",
                      "tokenDetails.cache_read.tokenCount", "tokenDetails.cache_write.tokenCount"}
    assert all(item["equal"] and item["declared"] == item["sum_of_records"] for item in verdict["checks"])
    last = decoded["session_totals"][-1]["model_metrics"]["gpt-6-luna"]["usage"]
    assert sum(row["output_tokens"] for row in rows) == last["outputTokens"] and sum(row["input_tokens"] for row in rows) == last["inputTokens"]
    # The store keeps a copy of the finished R1 turn only.
    assert [item["occurrence"].rsplit(":", 1)[1] for item in decoded["session_store"]["turn_copies"]] == ["user_message", "assistant_response"]
    # The search index content cell holds both complete R1 texts: one more statement of each.
    cells = decoded["session_store"]["cell_copies"]
    assert [item["event_id"] for item in cells] == [item["event_id"] for item in decoded["session_store"]["turn_copies"]]
    assert all(item["occurrence"].startswith("session-store.db:search_index_content:row-0:") for item in cells)


def test_usage_is_refused_when_the_prompt_cache_witness_disagrees_with_the_store_row():
    def mutate(store, decoded):
        decoded["prompt_cache_fragments"][-1]["prompt_cache_state"]["prompt_tokens"] += 1
    decoded, _ = decoded_with_store(RUNS[0], mutate)
    refused = decoded["prompt_cache_fragments"][-1]["response_id"]
    record, = [item for item in decoded["usage_records"] if item["response_event_id"] == refused]
    assert record["prompt_cache_state_equal"] is False
    displayed = [row for row in decoded["responses"] if "canary" in row]
    assert [("usage" in row, "usage_id" in row) for row in displayed if row["id"] == refused] == [(False, False)]
    assert all("usage" in row for row in displayed if row["id"] != refused)


@pytest.mark.parametrize("mutation", ["row_missing", "wrong_turn_index", "finish_reason", "model", "other_session", "schema_version", "null_output", "total_off"])
def test_store_usage_is_not_joined_or_reconciled_by_guesswork(mutation):
    def mutate(store, decoded):
        rows = store["tables"]["assistant_usage_events"]
        if mutation == "row_missing": del rows[1]
        elif mutation == "wrong_turn_index": rows[2]["turn_index"] = 1
        elif mutation == "finish_reason": rows[2]["finish_reason"] = "tool_calls"
        elif mutation == "model": rows[2]["model"] = "another-model"
        elif mutation == "other_session": rows[0]["session_id"] = "another-session"
        elif mutation == "schema_version": store["schema_version"] = 9
        elif mutation == "null_output": rows[2]["output_tokens"] = None
        else: rows[0]["output_tokens"] += 1
    decoded, _ = decoded_with_store(RUNS[0], mutate)
    r1, r2 = [row for row in decoded["responses"] if "canary" in row]
    if mutation in ("other_session", "schema_version"):
        assert decoded["session_store"]["exceptions"] and decoded["usage_records"] == [] and "usage" not in r1 and "usage" not in r2
    elif mutation in ("null_output", "total_off"):
        # The join holds; a NULL stays None and the totals do not reconcile.
        assert r1["usage"]["output_tokens"] == (None if mutation == "null_output" else r1["usage"]["output_tokens"])
        assert decoded["reconciliation"][0]["matches_session_totals"] is False
    else:
        # R1 does not join and no reconciliation verdict is made. A row moved to
            # the other turn breaks both counts, so R2 does not join either.
        assert "usage" not in r1 and ("usage" in r2) == (mutation != "wrong_turn_index") and "reconciliation" not in decoded


def test_absence_is_proved_only_by_an_empty_store_of_a_complete_root():
    rows = {"metrics": [{"id": name, "state": "unresolved", "correct": 0, "observed_eligible": 2, "decoded_eligible": 0}
                        for name in ("attribution.usage", "attribution.token_semantics", "work.actions")]}
    states = lambda decoded, complete: {row["id"]: row["state"] for row in apply_copilot_native_absence(rows, decoded, complete_root=complete)["metrics"]}
    base = {"status": "ok", "diagnostics": []}
    empty = {**base, "session_store": {"read": True, "exceptions": [], "usage_row_count": 0}}
    assert states(empty, True) == {"attribution.usage": "native_absent", "attribution.token_semantics": "native_absent", "work.actions": "unresolved"}
    for decoded, complete in ((empty, False), (base, True), ({**base, "session_store": {"read": True, "exceptions": [], "usage_row_count": 7}}, True),
                              ({**base, "session_store": {"read": False, "exceptions": [{"code": "x"}]}}, True),
                              ({**base, "session_store": {"read": True, "exceptions": [{"code": "x"}], "usage_row_count": 0}}, True)):
        assert set(states(decoded, complete).values()) == {"unresolved"}


def test_wal_checksums_follow_the_sqlite_rule_and_can_be_rewritten():
    database, wal = store_bytes(RUNS[0])
    frames, page_size, _ = wal_valid_frames(wal)
    assert page_size == 4096 and len(frames) == (len(wal) - 32) // (24 + page_size) > 0
    changed = bytearray(wal); changed[frames[3] + 24 + 100] ^= 1
    assert len(wal_valid_frames(bytes(changed))[0]) == 3
    repaired = rewrite_wal_checksums(bytes(changed), len(frames))
    assert len(wal_valid_frames(repaired)[0]) == len(frames) and rewrite_wal_checksums(wal, len(frames)) == wal
    with pytest.raises(SessionStoreError):
        read_session_store(database, wal[:32])


@pytest.mark.parametrize("run", RUNS)
def test_changed_file_hashes_come_from_the_snapshot_and_the_patch_reproduces_them(run):
    native = native_dir(run)
    decoded = decode_copilot_native_bytes((native / "events.jsonl").read_bytes())
    _add_native_snapshot_change(decoded, native)
    change, = decoded["file_changes"]
    index = json.loads((native / "rewind-file-snapshots/index.json").read_bytes())
    image, = index["snapshots"][0]["files"].values()
    assert (change["before_sha256"], change["after_sha256"]) == (image["preimage"]["contentHash"], image["postimage"]["contentHash"])
    assert change["edit_recomputed_from_backup"] is True and change["hash_source"] == "native_snapshot_hashes"
    # The file change takes the timestamp of its own snapshot record.
    witness, = [row for row in decoded["records"] if row["locator"] == change["locator"]]
    assert witness["timestamp"] == index["snapshots"][0]["timestamp"] == change["timestamp"]
    edit = next(row for row in decoded["actions"] if row.get("name") == "apply_patch")
    backup = (native / "rewind-file-snapshots/backups" / image["preimage"]["backupFile"]).read_text()
    assert hashlib.sha256(apply_copilot_patch(backup, edit["patch"]).encode()).hexdigest() == change["after_sha256"]
    assert apply_copilot_patch(backup + "extra\n" + backup, edit["patch"]) is None
    assert apply_copilot_patch(backup, edit["patch"].replace("subtotal", "missing")) is None


@pytest.mark.parametrize("run", RUNS)
def test_a_record_that_only_repeats_stated_events_is_not_useful_in_density(run):
    from session_bench.native_density import roles_after_repeats
    raw = [json.loads(line) for line in (native_dir(run) / "events.jsonl").read_text().splitlines() if line.strip()]
    decoded = decode_copilot_native_bytes((native_dir(run) / "events.jsonl").read_bytes())
    stated = {}
    for identity, occurrence in copilot_forward_occurrences(decoded):
        stated.setdefault(int(occurrence.split(":")[0].removeprefix("line-")), []).append(identity)
    roles = [copilot_event_role(row) for row in raw]
    after = roles_after_repeats(roles, [stated.get(number, ()) for number in range(1, len(raw) + 1)])
    repeats = [number for number, role in enumerate(after) if role == "snapshot"]
    # tool.execution_start repeats the request of assistant.message. It is the only in-read copy and is already unclassified.
    assert repeats and {raw[number]["type"] for number in repeats} == {"tool.execution_start"}
    assert all(roles[number] == "metadata" for number in repeats)
    assert all(roles[number] in {"user_message", "assistant_message", "tool_call", "tool_result"}
               for number in range(len(raw)) if stated.get(number + 1) and number not in repeats)


def test_tool_call_is_two_forward_occurrences_and_parents_are_native_joins():
    decoded = decode_copilot_native_bytes((native_dir(RUNS[0]) / "events.jsonl").read_bytes())
    occurrences = copilot_forward_occurrences(decoded)
    counts = {}
    for identity, _ in occurrences:
        counts[identity] = counts.get(identity, 0) + 1
    assert {identity.split(":")[0] for identity, count in counts.items() if count == 2} == {"call"}
    assert all(count in {2, 3} for identity, count in counts.items() if identity.startswith("call:"))
    # A completion record that names the tool and holds the path of a view restates that call: a third statement.
    views = [row["raw"]["data"]["toolCallId"] for row in decoded["records"] if row["raw"].get("type") == "tool.execution_start" and row["raw"]["data"].get("toolName") == "view"]
    assert views and all(counts[f"call:{identity}"] == 3 for identity in views)
    # Without the tool name in the completion, the path alone does not restate the call.
    plain = copy.deepcopy(decoded)
    for row in plain["records"]:
        if row["raw"].get("type") == "tool.execution_complete" and row["raw"]["data"].get("toolCallId") in views:
            row["raw"]["data"]["toolTelemetry"]["properties"]["command"] = "other"
    after = {}
    for identity, _ in copilot_forward_occurrences(plain):
        after[identity] = after.get(identity, 0) + 1
    assert all(after[f"call:{identity}"] == 2 for identity in views)
    assert len(occurrences) == len({occurrence for _, occurrence in occurrences})
    turns, explicit = copilot_thread_turns(decoded)
    by_id = {turn["id"]: turn for turn in turns}
    assert explicit and [turn["role"] for turn in turns][:3] == ["user", "assistant", "tool"]
    assert all(by_id[turn["parent_id"]]["role"] == {"assistant": "user", "tool": "assistant"}[turn["role"]]
               for turn in turns if turn["role"] != "user")
    broken = copy.deepcopy(decoded)
    next(row for row in broken["records"] if row["raw"]["type"] == "assistant.message")["raw"]["data"]["originatingMessageId"] = "lost"
    turns, explicit = copilot_thread_turns(broken)
    assert not explicit and all(turn["parent_id"] is None for turn in turns)


def test_density_counts_every_file_of_the_directory_once():
    native = native_dir(RUNS[0])
    artifacts = [{"path": path.relative_to(native).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in native.rglob("*") if path.is_file()]
    records, locators, exceptions = read_copilot_family(native, artifacts=artifacts)
    assert not any(row["record_id"].startswith("session-store") for row in records)
    assert exceptions == [] and len(records) == len(locators) == len({row["record_id"] for row in records})
    lines = [line for line in (native / "events.jsonl").read_bytes().split(b"\n") if line.strip()]
    assert len(records) == len(lines) + len(artifacts) - 1
    kinds = {row["record_id"]: row["record_kind"] for row in records}
    assert kinds["workspace.yaml"] == "session" and kinds["rewind-file-snapshots/index.json"] == "snapshot"
    assert copilot_event_role({"type": "tool.execution_start", "data": {}}) == "metadata"
    assert copilot_event_role({"type": "assistant.message", "data": {"content": "", "toolRequests": [{}]}}) == "tool_call"
    assert copilot_event_role({"type": "assistant.message", "data": {"content": "", "toolRequests": []}}) == "unknown"
    assert copilot_event_role({"type": "new.event", "data": {}}) == "unknown"
    with pytest.raises(ValueError):
        read_copilot_family(native, artifacts=[{**artifacts[0], "sha256": "0" * 64}, *artifacts[1:]])


def test_density_counts_every_store_row_as_unclassified(packets):
    output, summary = packets
    native = output / summary["runs"][0]["packet"] / "native"
    artifacts = json.loads((native / "decode.json").read_bytes())["artifacts"]
    records, _, exceptions = read_copilot_family(native, artifacts=artifacts)
    store = [row for row in records if row["record_id"].startswith("session-store.db:")]
    assert exceptions == [] and store and {row["classification"] for row in store} == {"unclassified"}
    assert sum(row["record_id"].startswith("session-store.db:assistant_usage_events:") for row in store) == 7
    assert any(row["record_id"].startswith("session-store.db:sqlite_master:") for row in store)
    # The search index content row holds the complete R1 prompt and response: a snapshot. The other index rows stay index.
    kinds = {row["record_id"]: row["record_kind"] for row in store}
    assert kinds["session-store.db:search_index_content:row-0"] == "snapshot" and kinds["session-store.db:turns:row-0"] == "snapshot"
    assert kinds["session-store.db:search_index_data:row-0"] == "index"
    assert not any(row["record_id"].startswith("session-store.db-wal") for row in records)
