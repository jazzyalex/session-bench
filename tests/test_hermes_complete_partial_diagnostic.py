"""The second Hermes capture stays private and only credits bound evidence."""

from pathlib import Path

import pytest

from session_bench.hermes_complete_partial_diagnostic import CAPTURE, build, write_new
from session_bench.v1_public_score import PUBLIC_METRICS


def test_second_repetition_has_31_explicit_states_without_score() -> None:
    report = build()
    rows = {row["id"]: row for row in report["metrics"]}
    assert set(rows) == set(PUBLIC_METRICS)
    assert len(rows) == 31
    assert sum(row["state"] == "measured" for row in rows.values()) == 9
    assert report["repetition"] == 2
    assert report["model"] == "gpt-5.5"
    assert report["qualified_repetitions_in_packet"] == 1
    assert report["score"] is report["rank"] is None
    assert report["public_safe"] is report["independent_reproduction"] is False
    assert all(rows[key]["state"] == "unresolved" for key in (
        "work.actions", "work.results", "attribution.usage",
        "portable.complete_root", "portable.companions"))
    assert {entry["path"] for entry in rows["work.changed_files"]["evidence"]} >= {
        "turn-r1/workspace/fixture_project/checkout.py",
        "turn-r2/workspace/fixture_project/checkout.py",
    }


def test_only_exact_capture_and_new_output_are_accepted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only the exact retained"):
        build(tmp_path)
    output = tmp_path / "diagnostic"
    assert write_new(output, CAPTURE)["metric_count"] == 31
    with pytest.raises(ValueError, match="must be new"):
        write_new(output, CAPTURE)
