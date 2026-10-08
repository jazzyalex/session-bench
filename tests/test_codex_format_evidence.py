"""Deterministic tests for the fail-closed Codex format-evidence builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import copy

import pytest

from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle
from session_bench.codex_format_evidence import (
    CodexFormatEvidenceError,
    build_codex_format_evidence,
)
from session_bench.v1_public_score import FORMAT_METRICS, validate_format_evidence


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/codex-cli-native-0154"

OBSERVER = {"id": "fixture-observer", "sha256": "a" * 64}
MANIFEST = {"id": "fixture-manifest", "sha256": "b" * 64}
BUILD = "0.154.0"
COLLECTED_ON = "2026-09-11"
RESULT_ID = "fixture-format-result-001"


def _state(validated: dict, metric_id: str) -> str:
    return next(row for row in validated["profile"]["metrics"] if row["id"] == metric_id)["state"]


def _build(decoded: dict, *, complete_record_family: bool = False) -> dict:
    return build_codex_format_evidence(
        decoded,
        observer=dict(OBSERVER),
        native_manifest=dict(MANIFEST),
        build=BUILD,
        collected_on=COLLECTED_ON,
        result_id=RESULT_ID,
        complete_record_family=complete_record_family,
    )


def test_fixture_bundle_yields_reportable_evidence_with_guarded_claims() -> None:
    decoded = decode_codex_cli_bundle(FIXTURE)
    document = _build(decoded)
    validated = validate_format_evidence(document)

    assert validated["run_id"] == decoded["measurement"]["run_id"]
    assert validated["configuration_id"] == decoded["measurement"]["configuration_id"]
    assert validated["repetition"] == decoded["measurement"]["repetition"]
    assert validated["build"] == BUILD
    assert validated["collected_on"] == COLLECTED_ON
    assert validated["result_id"] == RESULT_ID

    assert _state(validated, "broad.readable_rationale") == "unresolved"
    assert _state(validated, "broad.thread_structure") == "measured"
    assert _state(validated, "broad.standard_tools_readable") == "measured"
    assert _state(validated, "broad.documented_format") == "measured"
    assert _state(validated, "broad.declared_format_version") == "unresolved"
    assert _state(validated, "broad.event_timestamps") == "unresolved"
    assert _state(validated, "broad.naive_reader_duplicate_safety") == "unresolved"
    assert _state(validated, "broad.classified_content_density") == "unresolved"

    assert _state(validated, "broad.self_contained_identity") == "unresolved"
    assert _state(validated, "broad.stable_root_location") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"
    assert _state(validated, "broad.observed_schema_stability") == "unresolved"

    declared = {(item["id"], item["sha256"]) for item in decoded["package"]["artifacts"]}
    assert declared
    assert len(validated["metric_evidence"]) == len(FORMAT_METRICS)
    for row in validated["metric_evidence"]:
        assert row["observer_ids"] == ["fixture-observer"]
        assert row["native_locators"]
        for locator in row["native_locators"]:
            if row["metric_id"] in {"broad.declared_format_version", "broad.honest_version_signal"}:
                assert (locator["id"], locator["sha256"]) in {
                    (f"{item['artifact_id']}@{item['record_location']}", item["record_sha256"])
                    for item in decoded["native_schema_version"]["record_locators"]
                }
            else:
                assert (locator["id"], locator["sha256"]) in declared
    json.dumps(document)


def _write_records_bundle(destination: Path, records: list[dict]) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    data = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()
    (destination / "rollout.jsonl").write_bytes(data)
    (destination / "decode.json").write_text(
        json.dumps(
            {
                "format": "codex-rollout-v1",
                "artifacts": [{
                    "id": "rollout",
                    "path": "rollout.jsonl",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "depends_on": [],
                }],
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    return destination


def test_incomplete_bundle_stays_unresolved_but_valid(tmp_path: Path) -> None:
    package = _write_records_bundle(tmp_path / "empty-native", [])
    decoded = decode_codex_cli_bundle(package)
    document = _build(decoded)
    validated = validate_format_evidence(document)

    assert _state(validated, "broad.readable_rationale") == "unresolved"
    assert _state(validated, "broad.thread_structure") == "unresolved"
    assert _state(validated, "broad.event_timestamps") == "unresolved"
    assert _state(validated, "broad.self_contained_identity") == "unresolved"
    assert _state(validated, "broad.stable_root_location") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"
    assert _state(validated, "broad.observed_schema_stability") == "unresolved"


def test_desktop_style_messages_without_native_message_ids_use_record_locators() -> None:
    decoded = copy.deepcopy(decode_codex_cli_bundle(FIXTURE))
    for key in ("submitted_turns", "visible_responses"):
        for row in decoded["facts"][key]:
            row["id"] = None
    validated = validate_format_evidence(_build(decoded))

    assert _state(validated, "broad.readable_rationale") == "unresolved"
    assert _state(validated, "broad.thread_structure") == "measured"


def test_caller_identities_are_required() -> None:
    decoded = decode_codex_cli_bundle(FIXTURE)
    with pytest.raises(CodexFormatEvidenceError):
        build_codex_format_evidence(
            decoded,
            observer={},
            native_manifest=dict(MANIFEST),
            build=BUILD,
            collected_on=COLLECTED_ON,
            result_id=RESULT_ID,
        )


@pytest.mark.parametrize("complete_record_family", [False, True])
def test_decoder_label_and_writer_version_cannot_supply_native_schema_credit(complete_record_family: bool) -> None:
    decoded = copy.deepcopy(decode_codex_cli_bundle(FIXTURE))
    decoded["package"]["format"] = "invented-schema-version-999"
    decoded.setdefault("identity", {})["cli_version"] = "999.0.0"
    document = build_codex_format_evidence(
        decoded, observer=dict(OBSERVER), native_manifest=dict(MANIFEST),
        build=BUILD, collected_on=COLLECTED_ON, result_id=RESULT_ID,
        complete_record_family=complete_record_family,
    )
    validated = validate_format_evidence(document)
    expected = "native_absent" if complete_record_family else "unresolved"
    assert _state(validated, "broad.declared_format_version") == expected
    assert _state(validated, "broad.honest_version_signal") == expected
    assert document["profile"]["broad_evidence"]["broad.declared_format_version"]["format_version"] == ""


def test_complete_family_resolves_identity_version_window_and_duplicate_safety() -> None:
    decoded = decode_codex_cli_bundle(FIXTURE)
    document = build_codex_format_evidence(
        decoded,
        observer=dict(OBSERVER),
        native_manifest=dict(MANIFEST),
        build=BUILD,
        collected_on=COLLECTED_ON,
        result_id=RESULT_ID,
        complete_record_family=True,
    )
    validated = validate_format_evidence(document)
    assert _state(validated, "broad.self_contained_identity") == "measured"
    assert _state(validated, "broad.declared_format_version") == "native_absent"
    assert _state(validated, "broad.honest_version_signal") == "native_absent"
    assert _state(validated, "broad.observed_schema_stability") == "measured"
    # Duplicate safety is counted on the raw rollout records. Decoded facts alone leave it unresolved.
    assert _state(validated, "broad.naive_reader_duplicate_safety") == "unresolved"
    assert _state(validated, "broad.classified_content_density") == "unresolved"
    assert _state(validated, "broad.stable_root_location") == "unresolved"
    counted = build_codex_format_evidence(
        decoded, observer=dict(OBSERVER), native_manifest=dict(MANIFEST), build=BUILD, collected_on=COLLECTED_ON,
        result_id=RESULT_ID, complete_record_family=True, native_package=FIXTURE,
    )
    detail = counted["profile"]["broad_evidence"]["broad.naive_reader_duplicate_safety"]
    occurrences: dict[str, list[str]] = {}
    for record in detail["forward_records"]:
        assert record["state"] == "active"
        occurrences.setdefault(record["event_id"], []).append(record["occurrence_id"])
    assert list(occurrences) == detail["event_ids"]
    raw = [json.loads(line) for line in (FIXTURE / "rollout.jsonl").read_text().splitlines() if line.strip()]
    # An item_completed record restates its call: it holds the command and its directory, or the same hunk.
    # In this fixture it does not hold the full output text of the call output, so each result is stated once.
    # Its four messages are written once (a live rollout also repeats them; see test_native_density).
    calls = [row for row in raw if row["payload"].get("type") == "custom_tool_call"]
    assert len(calls) == 4 and sum(1 for event in occurrences if event.startswith("call:")) == 4
    assert all(len(found) == (2 if event.startswith("call:") else 1) for event, found in occurrences.items())
    row = next(item for item in validate_format_evidence(counted)["profile"]["metrics"] if item["id"] == "broad.naive_reader_duplicate_safety")
    assert row == {"id": "broad.naive_reader_duplicate_safety", "state": "measured", "correct": 8,
                   "observed_eligible": 12, "decoded_eligible": 16}
    with pytest.raises(CodexFormatEvidenceError):
        build_codex_format_evidence(
            decoded,
            observer=dict(OBSERVER),
            native_manifest=dict(MANIFEST),
            build="",
            collected_on=COLLECTED_ON,
            result_id=RESULT_ID,
        )
    with pytest.raises(CodexFormatEvidenceError):
        build_codex_format_evidence(
            decoded,
            observer=dict(OBSERVER),
            native_manifest=dict(MANIFEST),
            build=BUILD,
            collected_on="not-a-date",
            result_id=RESULT_ID,
        )
    with pytest.raises(CodexFormatEvidenceError):
        build_codex_format_evidence(
            decoded,
            observer={"id": "x", "sha256": "not-hex"},
            native_manifest=dict(MANIFEST),
            build=BUILD,
            collected_on=COLLECTED_ON,
            result_id=RESULT_ID,
        )
    with pytest.raises(CodexFormatEvidenceError):
        build_codex_format_evidence(
            {"unexpected": "shape"},
            observer=dict(OBSERVER),
            native_manifest=dict(MANIFEST),
            build=BUILD,
            collected_on=COLLECTED_ON,
            result_id=RESULT_ID,
        )


@pytest.mark.parametrize("field", ["schema_version", "format_version", "version", "nativeSchemaVersion", "future_version_semantics"])
@pytest.mark.parametrize("value", ["1.0", 1, None, "", {"major": 1}])
def test_native_version_candidates_preserve_provenance_without_guessed_schema_credit(tmp_path: Path, field: str, value: object) -> None:
    payload = {"session_id": "schema-candidate", "cli_version": "999.0.0", field: value}
    records = [{"type": "session_meta", "payload": payload, "ordinal": 1, "timestamp": "2026-09-11T00:00:00Z"}]
    package = _write_records_bundle(tmp_path / "candidate", records)
    decoded = decode_codex_cli_bundle(package)
    observation = decoded["native_schema_version"]

    assert observation["scan_complete"]
    assert observation["state"] == "unknown"
    candidate = next(item for item in observation["version_fields"] if item["field_path"] == f"payload.{field}")
    assert candidate["value"] == value
    assert candidate["semantics"] == "unqualified"
    assert candidate["locator"]["record_location"] == "rollout.jsonl:line-1"
    assert candidate["locator"]["artifact_sha256"] == hashlib.sha256((package / "rollout.jsonl").read_bytes()).hexdigest()
    assert candidate["locator"]["record_sha256"] == hashlib.sha256((package / "rollout.jsonl").read_bytes().rstrip(b"\n")).hexdigest()

    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"


def test_nested_or_other_envelope_version_fields_prevent_schema_absence(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"session_id": "nested", "cli_version": "0.154.0", "metadata": {"schema": {"version": "next"}}}},
        {"type": "turn_context", "payload": {"format_version": "future", "model": "test-model"}},
    ]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "nested", records))
    candidates = decoded["native_schema_version"]["version_fields"]
    assert {item["field_path"] for item in candidates if item["semantics"] == "unqualified"} == {
        "payload.metadata.schema", "payload.metadata.schema.version", "payload.format_version",
    }
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"


@pytest.mark.parametrize("extra", [42, {"type": "future_envelope", "payload": {}}, {"type": "session_meta", "payload": {"session_id": "conflicting"}}])
def test_unsupported_or_malformed_native_scan_cannot_prove_absence(tmp_path: Path, extra: object) -> None:
    records = [{"type": "session_meta", "payload": {"session_id": "valid", "cli_version": "0.154.0"}}, extra]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "invalid-scan", records))
    assert decoded["native_schema_version"]["state"] == "unknown"
    assert not decoded["native_schema_version"]["scan_complete"]
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"


def test_missing_native_version_scan_cannot_be_replaced_by_complete_family_flag() -> None:
    decoded = decode_codex_cli_bundle(FIXTURE)
    decoded.pop("native_schema_version")
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"


def test_schema_absence_requires_native_locator_binding() -> None:
    decoded = copy.deepcopy(decode_codex_cli_bundle(FIXTURE))
    decoded["native_schema_version"]["record_locators"][0]["artifact_sha256"] = "0" * 64
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"


def test_native_application_version_is_preserved_but_never_schema_credit(tmp_path: Path) -> None:
    records = [{"type": "session_meta", "payload": {"session_id": "application-version", "cli_version": "999.0.0"}}]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "application", records))
    observation = decoded["native_schema_version"]
    assert observation["state"] == "absent" and observation["scan_complete"]
    assert observation["version_fields"][0]["value"] == "999.0.0"
    assert observation["version_fields"][0]["semantics"] == "application_build"
    for complete, expected in ((False, "unresolved"), (True, "native_absent")):
        validated = validate_format_evidence(_build(decoded, complete_record_family=complete))
        assert _state(validated, "broad.declared_format_version") == expected
        assert _state(validated, "broad.honest_version_signal") == expected


def test_multi_agent_feature_version_is_not_a_schema_version(tmp_path: Path) -> None:
    # turn_context.payload.multi_agent_version names the active collaboration
    # feature set.  It is not a storage schema version.
    records = [
        {"type": "session_meta", "payload": {"session_id": "feature", "cli_version": "0.154.0"}},
        {"type": "turn_context", "payload": {"multi_agent_version": "v2", "model": "test-model"}},
    ]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "feature", records))
    observation = decoded["native_schema_version"]
    assert observation["state"] == "absent" and observation["scan_complete"]
    assert {(item["field_path"], item["semantics"]) for item in observation["version_fields"]} == {
        ("payload.cli_version", "application_build"), ("payload.multi_agent_version", "runtime_feature"),
    }
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "native_absent"
    assert _state(validated, "broad.honest_version_signal") == "native_absent"


def test_thread_settings_event_is_a_supported_record(tmp_path: Path) -> None:
    records = [
        {"type": "session_meta", "payload": {"session_id": "settings", "cli_version": "0.154.0"}},
        {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {"model": "test-model"}}},
    ]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "settings", records))
    assert decoded["unknown_records"] == 0
    assert decoded["native_schema_version"]["scan_complete"]


def test_session_metadata_is_required_for_schema_absence(tmp_path: Path) -> None:
    records = [{"type": "turn_context", "payload": {"model": "test-model"}}]
    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "no-metadata", records))
    assert decoded["native_schema_version"]["state"] == "unknown"
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"


@pytest.mark.parametrize("damage", ["malformed_line", "unscanned_companion"])
def test_unscanned_native_bytes_prevent_schema_absence(tmp_path: Path, damage: str) -> None:
    package = _write_records_bundle(tmp_path / "unscanned", [{"type": "session_meta", "payload": {"session_id": "unscanned"}}])
    manifest = json.loads((package / "decode.json").read_text())
    if damage == "malformed_line":
        data = (package / "rollout.jsonl").read_bytes() + b"{invalid-json}\n"
        (package / "rollout.jsonl").write_bytes(data)
        manifest["artifacts"][0].update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    else:
        data = b'{"schema_version":"future"}'
        (package / "companion.json").write_bytes(data)
        manifest["artifacts"].append({"id": "companion", "path": "companion.json", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []})
    (package / "decode.json").write_text(json.dumps(manifest))

    decoded = decode_codex_cli_bundle(package)

    assert not decoded["native_schema_version"]["scan_complete"]
    assert decoded["native_schema_version"]["state"] == "unknown"
    validated = validate_format_evidence(_build(decoded, complete_record_family=True))
    assert _state(validated, "broad.declared_format_version") == "unresolved"
    assert _state(validated, "broad.honest_version_signal") == "unresolved"


@pytest.mark.parametrize("complete_record_family", [False, True])
def test_filtered_codex_density_has_no_invented_byte_fallback(complete_record_family: bool) -> None:
    decoded = copy.deepcopy(decode_codex_cli_bundle(FIXTURE))
    for row in decoded["records"]:
        row["locator"].pop("byte_start", None)
        row["locator"].pop("byte_end", None)
    document = _build(decoded, complete_record_family=complete_record_family)
    detail = document["profile"]["broad_evidence"]["broad.classified_content_density"]
    assert detail["evidence_complete"] is False
    assert detail["records"] == []
    assert _state(validate_format_evidence(document), "broad.classified_content_density") == "unresolved"
    detail["records"] = [{"record_id": "bad", "record_kind": "unknown", "logical_bytes": -1, "classification": "unknown"}]
    with pytest.raises(ValueError, match="invalid logical-byte record"):
        validate_format_evidence(document)


@pytest.mark.parametrize("complete_record_family", [False, True])
def test_codex_explicit_native_inventory_resolves_density_only_with_bound_complete_family(complete_record_family: bool) -> None:
    decoded = decode_codex_cli_bundle(FIXTURE)
    document = build_codex_format_evidence(
        decoded, observer=OBSERVER, native_manifest=MANIFEST, build=BUILD,
        collected_on=COLLECTED_ON, result_id=RESULT_ID,
        complete_record_family=complete_record_family, native_package=FIXTURE,
    )
    validated = validate_format_evidence(document)
    assert _state(validated, "broad.classified_content_density") == ("measured" if complete_record_family else "unresolved")
    detail = document["profile"]["broad_evidence"]["broad.classified_content_density"]
    if complete_record_family:
        assert len(detail["records"]) == len((FIXTURE / "rollout.jsonl").read_text().splitlines())
        assert {record["classification"] for record in detail["records"]} >= {"useful", "unclassified"}
        proof = next(row for row in document["metric_evidence"] if row["metric_id"] == "broad.classified_content_density")
        assert proof["native_locators"][-1]["id"].startswith("rule:canonical-native-json-record-utf8-v1:")
    else:
        assert detail["records"] == []


def test_desktop_transcript_package_cannot_qualify_complete_companion_density() -> None:
    decoded = decode_codex_cli_bundle(FIXTURE, configuration_id="codex-desktop")
    document = build_codex_format_evidence(
        decoded, observer=OBSERVER, native_manifest=MANIFEST, build=BUILD,
        collected_on=COLLECTED_ON, result_id=RESULT_ID,
        complete_record_family=True, native_package=FIXTURE,
    )
    assert _state(validate_format_evidence(document), "broad.classified_content_density") == "unresolved"
    assert document["profile"]["broad_evidence"]["broad.classified_content_density"]["records"] == []


@pytest.mark.parametrize("delimiter", [b"\n", b"\r\n", b"\r\r\n"])
def test_version_and_density_native_locator_ids_share_exact_raw_hash_semantics(tmp_path: Path, delimiter: bytes) -> None:
    native_records = [json.loads(line) for line in (FIXTURE / "rollout.jsonl").read_text().splitlines()]
    package = _write_records_bundle(tmp_path / "delimiter", native_records)
    data = (package / "rollout.jsonl").read_bytes().replace(b"\n", delimiter)
    (package / "rollout.jsonl").write_bytes(data)
    manifest = json.loads((package / "decode.json").read_text())
    manifest["artifacts"][0].update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    (package / "decode.json").write_text(json.dumps(manifest))
    decoded = decode_codex_cli_bundle(package)
    document = build_codex_format_evidence(
        decoded, observer=OBSERVER, native_manifest=MANIFEST, build=BUILD,
        collected_on=COLLECTED_ON, result_id=RESULT_ID,
        complete_record_family=True, native_package=package,
    )
    proof_rows = {row["metric_id"]: {locator["id"]: locator["sha256"] for locator in row["native_locators"]} for row in document["metric_evidence"]}
    version = proof_rows["broad.declared_format_version"]
    density = proof_rows["broad.classified_content_density"]
    assert len(version) == len(native_records)
    assert all(density[identifier] == digest for identifier, digest in version.items())
