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


def _build(decoded: dict) -> dict:
    return build_codex_format_evidence(
        decoded,
        observer=dict(OBSERVER),
        native_manifest=dict(MANIFEST),
        build=BUILD,
        collected_on=COLLECTED_ON,
        result_id=RESULT_ID,
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

    assert _state(validated, "broad.readable_rationale") == "measured"
    assert _state(validated, "broad.thread_structure") == "measured"
    assert _state(validated, "broad.standard_tools_readable") == "measured"
    assert _state(validated, "broad.documented_format") == "measured"
    assert _state(validated, "broad.declared_format_version") == "measured"
    assert _state(validated, "broad.event_timestamps") == "measured"
    assert _state(validated, "broad.naive_reader_duplicate_safety") == "unresolved"
    assert _state(validated, "broad.classified_content_density") == "measured"

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

    assert _state(validated, "broad.readable_rationale") == "measured"
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
    assert _state(validated, "broad.honest_version_signal") == "measured"
    assert _state(validated, "broad.observed_schema_stability") == "measured"
    assert _state(validated, "broad.naive_reader_duplicate_safety") == "measured"
    assert _state(validated, "broad.stable_root_location") == "unresolved"
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
