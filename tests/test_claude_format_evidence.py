import copy
import hashlib
import json

import pytest

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle
from session_bench.claude_format_evidence import (
    ClaudeFormatEvidenceError,
    build_claude_format_evidence,
    validate_claude_timestamp_observer,
)
from session_bench.claude_desktop_gui_event_clock import (
    RESPONSE_PROVENANCE,
    TURN_PROVENANCE,
)
from session_bench.v1_public_score import validate_format_evidence


def _package(tmp_path):
    package = tmp_path / "native"
    package.mkdir()
    rows = [
        {"type": "user", "uuid": "u1", "sessionId": "s1", "timestamp": "2026-09-14T00:00:00Z", "message": {"role": "user", "content": "R1"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1", "timestamp": "2026-09-14T00:00:01Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "R1 explanation"}]}},
    ]
    session = package / "session.jsonl"
    session.write_text("".join(json.dumps(row) + "\n" for row in rows))
    (package / "decode.json").write_text(json.dumps({"format": "claude-code-jsonl-v1", "artifacts": [{"id": "session", "path": "session.jsonl", "sha256": hashlib.sha256(session.read_bytes()).hexdigest(), "size_bytes": session.stat().st_size, "depends_on": []}]}))
    return package


def _dual_observers(run_id="claude-desktop-cal-1"):
    kinds = ("user_turn", "action", "result", "file_change")
    fields = (
        {"text": "R1"},
        {"name": "Bash", "input": {"command": "python3 bench_check.py final"}, "target": "fixture_project/checkout.py"},
        {"action_id": "action", "status": "success"},
        {"action_id": "action", "path": "fixture_project/checkout.py"},
    )
    events = [
        {"id": "turn-r1" if kind == "user_turn" else kind, "sequence": index,
         "population_role": "primary_scored", "kind": kind, "session_id": "session-1",
         "fields": detail, "metric_ids": []}
        for index, (kind, detail) in enumerate(zip(kinds, fields), 1)
    ]
    primary = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
               "scenario_id": "survival-v1-repair", "run_id": run_id, "independent": True,
               "events": events, "relations": []}
    timestamp = copy.deepcopy(primary)
    for event in timestamp["events"]:
        if event["kind"] in {"action", "result", "file_change"}:
            event["fields"]["call_id"] = "tool-call-1"
    primary_bytes = json.dumps(primary, sort_keys=True, separators=(",", ":")).encode()
    timestamp_bytes = json.dumps(timestamp, sort_keys=True, separators=(",", ":")).encode()
    return primary, timestamp, primary_bytes, timestamp_bytes


def test_claude_format_wrapper_is_locator_bound_and_fail_closed(tmp_path):
    package = _package(tmp_path)
    document = build_claude_format_evidence(
        decode_claude_code_bundle(package),
        observer={"id": "observer-1", "sha256": "a" * 64},
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id="claude-cal-1", configuration_id="claude-cli", repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-1",
    )
    validated = validate_format_evidence(document)
    states = {row["id"]: row["state"] for row in validated["profile"]["metrics"]}
    assert states["broad.readable_rationale"] == "unresolved"
    assert states["broad.thread_structure"] == "measured"
    assert states["broad.standard_tools_readable"] == "measured"
    assert states["broad.documented_format"] == "measured"
    assert states["broad.event_timestamps"] == "unresolved"
    assert states["broad.self_contained_identity"] == "unresolved"
    assert states["broad.naive_reader_duplicate_safety"] == "unresolved"
    assert states["broad.classified_content_density"] == "unresolved"
    assert all(row["observer_ids"] == ["observer-1"] and row["native_locators"] == [{"id": "native-1", "sha256": "b" * 64}] for row in document["metric_evidence"])


def test_complete_family_scores_observable_absence_and_keeps_three_run_root_gate(tmp_path):
    package = _package(tmp_path)
    document = build_claude_format_evidence(
        decode_claude_code_bundle(package),
        observer={"id": "observer-1", "sha256": "a" * 64},
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id="claude-desktop-cal-1", configuration_id="claude-desktop", repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-2",
        complete_record_family=True,
    )
    validated = validate_format_evidence(document)
    rows = {row["id"]: row for row in validated["profile"]["metrics"]}
    assert rows["broad.self_contained_identity"]["state"] == "measured"
    assert rows["broad.declared_format_version"]["state"] == "native_absent"
    assert rows["broad.honest_version_signal"]["state"] == "native_absent"
    assert rows["broad.observed_schema_stability"]["state"] == "measured"
    # Duplicate safety is counted on the raw transcript records. Decoded events alone leave it unresolved.
    assert rows["broad.naive_reader_duplicate_safety"]["state"] == "unresolved"
    assert rows["broad.stable_root_location"]["state"] == "unresolved"
    assert rows["broad.classified_content_density"]["state"] == "unresolved"
    detail = document["profile"]["broad_evidence"]["broad.classified_content_density"]
    assert detail["records"] == []
    detail["records"] = [{"record_id": "bad", "record_kind": "unknown", "logical_bytes": -1, "classification": "unknown"}]
    with pytest.raises(ValueError, match="invalid logical-byte record"):
        validate_format_evidence(document)


@pytest.mark.parametrize("complete_record_family", [False, True])
def test_claude_native_inventory_density_requires_complete_bound_package(tmp_path, complete_record_family):
    package = _package(tmp_path)
    decoded = decode_claude_code_bundle(package)
    assert decoded["package"]["artifacts"][0]["sha256"] == hashlib.sha256((package / "session.jsonl").read_bytes()).hexdigest()
    document = build_claude_format_evidence(
        decoded, observer={"id": "observer-1", "sha256": "a" * 64},
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id="claude-cal-1", configuration_id="claude-cli", repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-1",
        complete_record_family=complete_record_family, native_package=package,
    )
    row = next(row for row in validate_format_evidence(document)["profile"]["metrics"] if row["id"] == "broad.classified_content_density")
    assert row["state"] == ("measured" if complete_record_family else "unresolved")
    detail = document["profile"]["broad_evidence"]["broad.classified_content_density"]
    assert len(detail["records"]) == (2 if complete_record_family else 0)
    if complete_record_family:
        proof = next(row for row in document["metric_evidence"] if row["metric_id"] == "broad.classified_content_density")
        assert proof["native_locators"][-1]["id"].startswith("rule:canonical-native-json-record-utf8-v1:")


def test_claude_desktop_transcript_cannot_qualify_missing_companion_density(tmp_path):
    package = _package(tmp_path)
    document = build_claude_format_evidence(
        decode_claude_code_bundle(package), observer={"id": "observer-1", "sha256": "a" * 64},
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id="claude-desktop-cal-1", configuration_id="claude-desktop", repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-1",
        complete_record_family=True, native_package=package,
    )
    row = next(row for row in validate_format_evidence(document)["profile"]["metrics"] if row["id"] == "broad.classified_content_density")
    assert row["state"] == "unresolved"
    assert document["profile"]["broad_evidence"]["broad.classified_content_density"]["records"] == []


def test_desktop_timestamp_observer_is_used_only_for_timestamp_metric(tmp_path):
    package = _package(tmp_path)
    primary, _, primary_bytes, timestamp_bytes = _dual_observers()
    primary_ref = {"id": "primary-observer", "sha256": hashlib.sha256(primary_bytes).hexdigest()}
    timestamp_ref = {"id": "timestamp-observer", "sha256": hashlib.sha256(timestamp_bytes).hexdigest()}
    document = build_claude_format_evidence(
        decode_claude_code_bundle(package), observer=primary_ref,
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id=primary["run_id"], configuration_id="claude-desktop", repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-dual",
        complete_record_family=True, observer_document=primary_bytes,
        timestamp_observer=timestamp_ref, timestamp_observer_document=timestamp_bytes,
    )
    bindings = {row["metric_id"]: row["observer_ids"] for row in document["metric_evidence"]}
    assert document["observer"] == primary_ref
    assert bindings["broad.event_timestamps"] == ["timestamp-observer"]
    assert all(ids == ["primary-observer"] for metric, ids in bindings.items()
               if metric != "broad.event_timestamps")
    assert document["profile"]["broad_evidence"]["broad.event_timestamps"]["event_ids"] == [
        event["id"] for event in primary["events"]
    ]


@pytest.mark.parametrize("mutation", [
    lambda value: value["events"][1]["fields"].update(target="fixture_project/other.py"),
    lambda value: value["events"][2]["fields"].pop("call_id"),
    lambda value: value["events"][0]["fields"].update(call_id="tool-call-1"),
    lambda value: value["events"].reverse(),
    lambda value: value.update(run_id="other-run"),
])
def test_desktop_timestamp_observer_rejects_population_or_content_changes(mutation):
    primary, timestamp, _, _ = _dual_observers()
    mutation(timestamp)
    with pytest.raises(ClaudeFormatEvidenceError):
        validate_claude_timestamp_observer(primary, timestamp, run_id=primary["run_id"])


def test_desktop_timestamp_observer_accepts_complete_local_capture_clock():
    primary, timestamp, _, _ = _dual_observers()
    for event in timestamp["events"]:
        event["fields"]["observed_at"] = "2026-10-02T12:00:00.000000Z"
        event["fields"]["timestamp_provenance"] = (
            {"user_turn": TURN_PROVENANCE,
             "assistant_response": RESPONSE_PROVENANCE}.get(
                 event["kind"], "local_hook_receipt_clock"
             )
        )

    validate_claude_timestamp_observer(primary, timestamp, run_id=primary["run_id"])


@pytest.mark.parametrize("mutation", [
    lambda value: value["events"][2]["fields"].pop("observed_at"),
    lambda value: value["events"][0]["fields"].update(timestamp_provenance="local_hook_receipt_clock"),
    lambda value: value["events"][1]["fields"].update(observed_at="2026-10-02T12:00:00"),
])
def test_desktop_timestamp_observer_rejects_incomplete_or_misprovenanced_clock(mutation):
    primary, timestamp, _, _ = _dual_observers()
    for event in timestamp["events"]:
        event["fields"]["observed_at"] = "2026-10-02T12:00:00.000000Z"
        event["fields"]["timestamp_provenance"] = (
            {"user_turn": TURN_PROVENANCE,
             "assistant_response": RESPONSE_PROVENANCE}.get(
                 event["kind"], "local_hook_receipt_clock"
             )
        )
    mutation(timestamp)

    with pytest.raises(ClaudeFormatEvidenceError):
        validate_claude_timestamp_observer(primary, timestamp, run_id=primary["run_id"])


def test_timestamp_observer_rejects_wrong_surface_and_unbound_bytes(tmp_path):
    package = _package(tmp_path)
    primary, timestamp, primary_bytes, timestamp_bytes = _dual_observers("claude-cal-1")
    common = dict(
        decoded=decode_claude_code_bundle(package),
        observer={"id": "primary", "sha256": hashlib.sha256(primary_bytes).hexdigest()},
        native_manifest={"id": "native", "sha256": "b" * 64}, run_id=primary["run_id"],
        repetition=1, build="2.1.270", collected_on="2026-09-14", result_id="result",
        observer_document=primary_bytes,
        timestamp_observer={"id": "timestamp", "sha256": hashlib.sha256(timestamp_bytes).hexdigest()},
        timestamp_observer_document=timestamp_bytes,
    )
    with pytest.raises(ClaudeFormatEvidenceError, match="only for claude-desktop"):
        build_claude_format_evidence(configuration_id="claude-cli", **common)
    primary["run_id"] = timestamp["run_id"] = common["run_id"] = "claude-desktop-cal-1"
    common["observer_document"] = json.dumps(primary, sort_keys=True, separators=(",", ":")).encode()
    common["observer"] = {"id": "primary", "sha256": hashlib.sha256(common["observer_document"]).hexdigest()}
    common["timestamp_observer_document"] = timestamp_bytes + b" "
    with pytest.raises(ClaudeFormatEvidenceError, match="digest differs"):
        build_claude_format_evidence(configuration_id="claude-desktop", **common)


@pytest.mark.parametrize("configuration", ["claude-cli", "claude-desktop"])
def test_queued_prompt_and_user_record_are_two_occurrences_of_one_prompt(tmp_path, configuration):
    package = _package(tmp_path)
    session = package / "session.jsonl"
    queued = json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": "s1", "content": "R1"}) + "\n"
    last = json.dumps({"type": "last-prompt", "sessionId": "s1", "lastPrompt": "R"}) + "\n"
    session.write_text(queued + session.read_text() + last)
    inventory = json.loads((package / "decode.json").read_text())
    inventory["artifacts"][0].update(sha256=hashlib.sha256(session.read_bytes()).hexdigest(), size_bytes=session.stat().st_size)
    (package / "decode.json").write_text(json.dumps(inventory))
    document = build_claude_format_evidence(
        decode_claude_code_bundle(package), observer={"id": "observer-1", "sha256": "a" * 64},
        native_manifest={"id": "native-1", "sha256": "b" * 64},
        run_id="claude-cal-1", configuration_id=configuration, repetition=1,
        build="2.1.270", collected_on="2026-09-14", result_id="claude-format-1",
        complete_record_family=True, native_package=package,
    )
    detail = document["profile"]["broad_evidence"]["broad.naive_reader_duplicate_safety"]
    assert detail["event_ids"] == ["message:line-1", "message:a1"]
    assert [(row["event_id"], row["occurrence_id"].rsplit(":", 1)[1]) for row in detail["forward_records"]] == [
        ("message:line-1", "line-1"), ("message:line-1", "line-2"), ("message:a1", "line-3")]
    row = next(row for row in validate_format_evidence(document)["profile"]["metrics"] if row["id"] == "broad.naive_reader_duplicate_safety")
    assert row == {"id": "broad.naive_reader_duplicate_safety", "state": "measured", "correct": 1, "observed_eligible": 2, "decoded_eligible": 3}
    if configuration == "claude-cli":
        kinds = [row["record_kind"] for row in document["profile"]["broad_evidence"]["broad.classified_content_density"]["records"]]
        assert kinds == ["user_message", "snapshot", "assistant_message", "unknown"]
