"""Controls for real OpenCode broad-evidence production."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_bench.opencode_format_evidence import build_opencode_format_evidence
from session_bench.v1_public_score import FORMAT_METRICS, score_public_run, validate_format_evidence


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction"


def test_corrected_opencode_package_emits_bound_broad_rows_without_a_score() -> None:
    document = build_opencode_format_evidence(PACKAGE, collected_on="2026-09-14")

    assert set(document["profile"]["broad_evidence"]) == set(FORMAT_METRICS)
    assert {row["metric_id"] for row in document["metric_evidence"]} == set(FORMAT_METRICS)
    assert all(row["observer_ids"] and row["native_locators"] for row in document["metric_evidence"])
    survival = json.loads((PACKAGE / "evidence.json").read_text())
    scored = score_public_run(survival, document)
    assert scored.rankable is False
    assert scored.overall is None
    assert "broad.declared_format_version:unresolved" in scored.blockers
    assert "broad.classified_content_density:unresolved" in scored.blockers


def test_complete_three_run_family_resolves_absences_and_root_stability() -> None:
    roots = [
        {"repetition": number, "root_locator": f"isolated/opencode-{number}", "isolated_discovery": True, "personal_history_scanned": False}
        for number in (1, 2, 3)
    ]
    document = build_opencode_format_evidence(
        PACKAGE,
        collected_on="2026-09-14",
        complete_record_family=True,
        root_repetitions=roots,
    )
    survival = json.loads((PACKAGE / "evidence.json").read_text())
    scored = score_public_run(survival, document)
    assert scored.rankable is False
    assert scored.overall is None
    assert "broad.classified_content_density:unresolved" in scored.blockers
    # The copied database declares its schema through a migration ledger that
    # this decoder contract is bound to, so both version rows are measured.
    assert scored.metrics["broad.declared_format_version"] == 1
    assert scored.metrics["broad.honest_version_signal"] == 1
    assert scored.metrics["broad.observed_schema_stability"] == 1
    assert scored.metrics["broad.stable_root_location"] == 1
    # The event table is in the read and repeats every part with no supersession field: no event is stated once.
    assert scored.metrics["broad.naive_reader_duplicate_safety"] == 0
    # Duplicate safety is counted on the raw rows of the read (session, message, part, event), not on decoded facts.
    forward = document["profile"]["broad_evidence"]["broad.naive_reader_duplicate_safety"]
    assert forward["event_ids"] and forward["deduplication"]["documented"] is False
    assert all(row["event_id"].split(":")[0] in {"message", "call", "result"} and row["occurrence_id"].split(":")[0] in {"part", "event", "message", "session"} for row in forward["forward_records"])
    import sqlite3, shutil, tempfile
    with tempfile.TemporaryDirectory() as folder:
        for path in (PACKAGE / "native-bundle").iterdir(): shutil.copy(path, folder)
        connection = sqlite3.connect(Path(folder) / "opencode.db")
        parts = [json.loads(row[0]) for row in connection.execute("SELECT data FROM part")]
        connection.close()
    expected = sum(1 for part in parts if part.get("type") == "text" and part.get("text")) + sum(
        1 + (part["state"].get("status") in {"completed", "error"}) for part in parts if part.get("type") == "tool")
    assert len(forward["event_ids"]) == expected
    # Every event of the part rows is stated again by an event row.
    assert len(forward["forward_records"]) > len(forward["event_ids"])
    assert {row["occurrence_id"].split(":")[0] for row in forward["forward_records"]} >= {"part", "event"}
    detail = document["profile"]["broad_evidence"]["broad.classified_content_density"]
    assert detail["evidence_complete"] is False and detail["records"] == []
    detail["records"] = [{"record_id": "bad", "record_kind": "unknown", "logical_bytes": -1, "classification": "unknown"}]
    with pytest.raises(ValueError, match="invalid logical-byte record"):
        validate_format_evidence(document)


# --- native version scan (migration ledger) ---

import hashlib
import shutil
import sqlite3

from session_bench.adapters.opencode_decoder import OPENCODE_SUPPORTED_SCHEMA_LEDGERS
from session_bench.v1_public_score import validate_format_profile

LEDGER_SHA, = OPENCODE_SUPPORTED_SCHEMA_LEDGERS
LAST = "20260622202450_simplify_session_input"
ROOTS = [{"repetition": n, "root_locator": f"isolated/opencode-{n}", "isolated_discovery": True, "personal_history_scanned": False} for n in (1, 2, 3)]


def version_rows(package, **options):
    document = build_opencode_format_evidence(package, collected_on="2026-09-14", complete_record_family=True, root_repetitions=ROOTS, **options)
    states = {row["id"]: row for row in validate_format_profile(document["profile"])["metrics"]}
    locators = {row["metric_id"]: row["native_locators"] for row in document["metric_evidence"]}
    return document["profile"]["broad_evidence"], states, locators


def changed_package(tmp_path, statements):
    """Copy the package and change only the copied database; keep the manifest hash-bound."""
    package = tmp_path / "package"
    shutil.copytree(PACKAGE, package)
    bundle = package / "native-bundle"
    connection = sqlite3.connect(bundle / "opencode.db")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    manifest = json.loads((package / "native-manifest.json").read_text())
    for row in manifest["files"]:
        data = (bundle / row["name"]).read_bytes()
        row.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    (package / "native-manifest.json").write_text(json.dumps(manifest))
    return package, connection


def test_migration_ledger_is_the_measured_declared_version_bound_to_the_native_database() -> None:
    broad, states, locators = version_rows(PACKAGE)
    declared, honest = broad["broad.declared_format_version"], broad["broad.honest_version_signal"]
    db_sha = hashlib.sha256((PACKAGE / "native-bundle/opencode.db").read_bytes()).hexdigest()

    assert declared == {"evidence_complete": True, "machine_readable": True, "bundle_binding": "native:opencode.db",
                        "format_version": f"migration-ledger:last={LAST};count=38;ids_sha256={LEDGER_SHA}"}
    assert states["broad.declared_format_version"]["state"] == "measured" and states["broad.declared_format_version"]["correct"] == 1
    assert locators["broad.declared_format_version"] == [{"id": "native:opencode.db", "sha256": db_sha}]
    assert honest == {"evidence_complete": True, "declared_version": declared["format_version"],
                      "decoder_contract_version": f"opencode-sqlite-session-v1;migration-ledger-ids-sha256={LEDGER_SHA}",
                      "incompatible_schema_distinguished": True, "matches_decoder_contract": True}
    assert states["broad.honest_version_signal"]["state"] == "measured" and states["broad.honest_version_signal"]["correct"] == 1
    assert locators["broad.honest_version_signal"] == [{"id": "native:opencode.db", "sha256": db_sha}]


@pytest.mark.parametrize("statements", [["DELETE FROM migration"], ["DROP TABLE migration"]])
def test_empty_or_missing_ledger_is_a_scored_absence(tmp_path, statements) -> None:
    package, connection = changed_package(tmp_path, statements)
    try:
        broad, states, _ = version_rows(package)
    finally:
        connection.close()
    assert broad["broad.declared_format_version"]["format_version"] == ""
    assert broad["broad.declared_format_version"]["machine_readable"] is False
    assert states["broad.declared_format_version"]["state"] == "native_absent"
    assert states["broad.honest_version_signal"]["state"] == "native_absent"


def test_unknown_ledger_tail_is_declared_but_not_measured_as_honest(tmp_path) -> None:
    package, connection = changed_package(tmp_path, ["INSERT INTO migration VALUES ('20270101000000_later_schema_change', 1)"])
    try:
        broad, states, _ = version_rows(package)
    finally:
        connection.close()
    assert "last=20270101000000_later_schema_change;count=39" in broad["broad.declared_format_version"]["format_version"]
    assert states["broad.declared_format_version"]["state"] == "measured"
    honest = broad["broad.honest_version_signal"]
    assert honest["matches_decoder_contract"] is False and honest["evidence_complete"] is False
    assert states["broad.honest_version_signal"]["state"] == "unresolved"
    assert states["broad.honest_version_signal"]["correct"] == 0


def test_version_scan_refuses_a_database_that_differs_from_the_manifest(tmp_path) -> None:
    package = tmp_path / "package"
    shutil.copytree(PACKAGE, package)
    manifest = json.loads((package / "native-manifest.json").read_text())
    next(row for row in manifest["files"] if row["name"] == "opencode.db")["sha256"] = "0" * 64
    (package / "native-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest"):
        version_rows(package)
