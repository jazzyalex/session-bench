"""Offline controls for the retained private Cursor Desktop eval4 diagnostic."""

from pathlib import Path

import pytest

from session_bench.cursor_desktop_eval4_partial_diagnostic import (
    BROAD,
    CAPTURE,
    DEEP,
    REPLAY,
    build,
    write_new,
)
from session_bench.v1_public_score import PUBLIC_METRICS


def test_exact_retained_capture_adds_bounded_31_metric_diagnostic():
    report = build()
    rows = {row["id"]: row for row in report["metrics"]}
    assert set(rows) == set(PUBLIC_METRICS) == set(DEEP + BROAD)
    assert report["metric_count"] == 31
    assert report["measured_count"] == 5
    assert report["unresolved_count"] == 26
    assert {metric for metric, row in rows.items() if row["state"] == "measured"} == {
        "work.changed_files",
        "broad.readable_rationale",
        "broad.thread_structure",
        "broad.standard_tools_readable",
        "broad.declared_format_version",
    }
    observer_paths = {item["path"] for item in rows["work.changed_files"]["evidence"]}
    assert "capture/observer/pre-run-project-manifest.json" in observer_paths
    assert "capture/project/fixture_project/.survival-observer.jsonl" in observer_paths
    assert rows["work.submitted_turns"]["state"] == "unresolved"
    assert rows["work.visible_responses"]["state"] == "unresolved"
    assert rows["portable.complete_root"]["state"] == "unresolved"
    assert rows["broad.classified_content_density"]["state"] == "unresolved"
    assert report["score"] is report["rank"] is None
    assert report["score_eligible"] is report["public_safe"] is False
    assert report["native_family_complete"] is report["observer_independent"] is False
    assert all(row["reason"] and row["evidence"] for row in rows.values())


def test_only_exact_sources_and_new_private_output_are_accepted(tmp_path: Path):
    with pytest.raises(ValueError, match="only the exact retained Cursor Desktop eval4 capture"):
        build(tmp_path, REPLAY)
    with pytest.raises(ValueError, match="only the exact retained Cursor Desktop eval4 replay"):
        build(CAPTURE, tmp_path)
    output = tmp_path / "private-diagnostic"
    report = write_new(output)
    assert report["metric_count"] == 31
    assert (output / "diagnostic.private.json").is_file()
    with pytest.raises(ValueError, match="must be new"):
        write_new(output)
