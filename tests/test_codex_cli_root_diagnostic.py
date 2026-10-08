from pathlib import Path

import pytest

from session_bench.codex_cli_root_diagnostic import (
    build_codex_cli_root_diagnostic,
    write_codex_cli_root_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]


def test_retained_codex_cli_root_receipts_close_only_stable_root() -> None:
    result = build_codex_cli_root_diagnostic(ROOT)

    assert result["metric_closure"]["metric_id"] == "broad.stable_root_location"
    assert result["metric_closure"]["successor_state"] == "measured"
    assert result["metric_closure"]["score_fraction"] == "1/1"
    assert result["public_safe"] is False
    assert result["independent_reproduction"] is False
    assert result["rankable"] is False
    assert result["per_run_counts"] == [
        {"run_id": f"codex-cli-eval-{number}", "measured": 22, "contradiction": 1, "unresolved": 8}
        for number in (1, 2, 3)
    ]
    assert len(result["bindings"]) == 3
    assert all(row["selected_native_path"].startswith("native/2026/09/14/rollout-") for row in result["bindings"])


def test_writer_is_additive(tmp_path: Path) -> None:
    output = tmp_path / "diagnostic.json"
    write_codex_cli_root_diagnostic(ROOT, output)
    assert output.is_file()
    with pytest.raises(ValueError, match="must be new"):
        write_codex_cli_root_diagnostic(ROOT, output)
