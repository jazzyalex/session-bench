"""Controls for real OpenCode broad-evidence production."""

from __future__ import annotations

import json
from pathlib import Path

from session_bench.opencode_format_evidence import build_opencode_format_evidence
from session_bench.v1_public_score import FORMAT_METRICS, score_public_run


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
    assert scored.rankable is True
    assert scored.metrics["broad.declared_format_version"] == 0
    assert scored.metrics["broad.honest_version_signal"] == 0
    assert scored.metrics["broad.observed_schema_stability"] == 1
    assert scored.metrics["broad.stable_root_location"] == 1
    assert scored.metrics["broad.naive_reader_duplicate_safety"] == 1
