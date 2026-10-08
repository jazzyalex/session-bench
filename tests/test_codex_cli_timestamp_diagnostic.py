from pathlib import Path

import pytest

from session_bench.codex_cli_timestamp_diagnostic import (
    _timestamp,
    build_codex_cli_timestamp_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]


def test_retained_codex_cli_native_timestamps_join_all_observed_events():
    result = build_codex_cli_timestamp_diagnostic(ROOT)
    assert result["metric_closure"] == {
        "metric_id": "broad.event_timestamps",
        "predecessor_state": "unresolved",
        "successor_state": "measured",
        "score_fraction": "1/1",
    }
    assert [row["event_count"] for row in result["repetitions"]] == [13, 13, 13]
    for row in result["repetitions"]:
        assert len(set(row["event_ids"])) == 13
        assert len(row["native_witnesses"]) == 13


def test_native_timestamp_requires_exact_record_hash(tmp_path):
    path = tmp_path / "rollout.jsonl"
    raw = b'{"timestamp":"2026-09-15T00:00:00Z"}\n'
    path.write_bytes(raw)
    fact = {"locator": {"line": 1, "record_sha256": "0" * 64,
                        "artifact_sha256": "0" * 64}}
    with pytest.raises(ValueError, match="locator digest mismatch"):
        _timestamp(path, fact, [raw])
