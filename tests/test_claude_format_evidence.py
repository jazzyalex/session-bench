import hashlib
import json

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle
from session_bench.claude_format_evidence import build_claude_format_evidence
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
    assert states["broad.readable_rationale"] == "measured"
    assert states["broad.thread_structure"] == "measured"
    assert states["broad.standard_tools_readable"] == "measured"
    assert states["broad.documented_format"] == "measured"
    assert states["broad.event_timestamps"] == "measured"
    assert states["broad.self_contained_identity"] == "unresolved"
    assert states["broad.naive_reader_duplicate_safety"] == "unresolved"
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
    assert rows["broad.naive_reader_duplicate_safety"]["state"] == "measured"
    assert rows["broad.stable_root_location"]["state"] == "unresolved"
