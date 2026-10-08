"""Focused offline checks for the one qualified private Hermes diagnostic."""

from pathlib import Path

import pytest

from session_bench.hermes_partial_diagnostic import (
    BROAD, CAPTURE, DEEP, build, write_new,
)
from session_bench.v1_public_score import PUBLIC_METRICS


def test_exact_qualified_capture_reports_all_metric_states_without_score():
    report = build()
    rows = {row["id"]: row for row in report["metrics"]}
    assert set(rows) == set(PUBLIC_METRICS) == set(DEEP + BROAD)
    assert len(rows) == report["metric_count"] == 31
    assert report["qualified_repetitions"] == 1
    assert report["native_terminal_calls"] == report["native_terminal_results"] == 2
    assert report["compound_calls_count_as_extra_actions"] is False
    assert report["response_scoped_usage_inferred"] is False
    assert sum(row["state"] == "measured" for row in report["metrics"]) == 9
    assert {rows[key]["state"] for key in (
        "work.actions", "work.results", "causal.action_result",
        "attribution.usage", "attribution.token_semantics",
        "attribution.reconciliation", "portable.complete_root",
    )} == {"unresolved"}
    assert rows["work.submitted_turns"]["state"] == "measured"
    assert rows["work.visible_responses"]["state"] == "measured"
    deep_witness_paths = {
        "work.submitted_turns": {"observer/prompt-r1.txt", "observer/prompt-r2-continuation.txt",
                                 "capture-result.json", "continuation-result.json"},
        "work.visible_responses": {"turn-r1/stdout.txt", "turn-r2/stdout.txt"},
        "revision.r1": {"observer/prompt-r1.txt"},
        "revision.r2": {"observer/prompt-r2-continuation.txt"},
        "revision.r1_r2_order": {"observer/prompt-r1.txt", "observer/prompt-r2-continuation.txt",
                                 "capture-result.json", "continuation-result.json"},
    }
    for metric, witnesses in deep_witness_paths.items():
        assert rows[metric]["state"] == "measured"
        assert witnesses <= {entry["path"] for entry in rows[metric]["evidence"]}
    assert rows["work.changed_files"]["state"] == "measured"
    assert {entry["path"] for entry in rows["work.changed_files"]["evidence"]} >= {
        "turn-r1/workspace/fixture_project/checkout.py",
        "turn-r2/workspace/fixture_project/checkout.py",
    }
    assert rows["causal.turn_response"]["state"] == "measured"
    assert report["score"] is report["rank"] is None
    assert report["public_safe"] is report["independent_reproduction"] is False
    assert all(row["reason"] and row["evidence"] for row in rows.values())


def test_only_exact_capture_and_new_output_are_accepted(tmp_path: Path):
    with pytest.raises(ValueError, match="only the exact retained"):
        build(tmp_path)
    output = tmp_path / "private-hermes-diagnostic"
    report = write_new(output, CAPTURE)
    assert report["metric_count"] == 31
    assert (output / "diagnostic.json").is_file()
    with pytest.raises(ValueError, match="must be new"):
        write_new(output, CAPTURE)
