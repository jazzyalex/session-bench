from copy import deepcopy
import json
from pathlib import Path

import pytest

from session_bench.codex_cli_changed_file_diagnostic import (
    EvidenceGap,
    _verify_diagnostics_pin,
    apply_single_native_hunk,
    build_codex_cli_changed_file_diagnostic,
)


ROOT = Path(__file__).resolve().parents[1]


def test_retained_native_patch_reconstructs_independent_file_snapshots():
    result = build_codex_cli_changed_file_diagnostic(ROOT)
    assert result["metric_closure"] == {
        "metric_id": "work.changed_files",
        "predecessor_state": "unresolved",
        "successor_state": "measured",
        "score_fraction": "1/1",
    }
    assert [row["metric_state"] for row in result["repetitions"]] == ["measured"] * 3
    assert {row["project_relative_path"] for row in result["repetitions"]} == {"fixture_project/checkout.py"}
    for row in result["repetitions"]:
        assert row["before_sha256"] != row["after_sha256"]
        assert row["inspect_line"] < row["change_line"]
        assert len(row["change_record_sha256"]) == 64


def test_native_diff_rejects_mismatched_old_source_and_second_hunk():
    before = b"first\nsecond\n"
    diff = "@@ -2,1 +2,1 @@\n-second\n+third\n"
    assert apply_single_native_hunk(before, diff) == b"first\nthird\n"
    with pytest.raises(EvidenceGap, match="old hunk differs"):
        apply_single_native_hunk(b"first\nother\n", diff)
    with pytest.raises(EvidenceGap, match="second hunk"):
        apply_single_native_hunk(before, diff + "@@ -1,1 +1,1 @@\n-first\n+other\n")


def test_replay_diagnostics_must_match_receipt_and_manifest_digest():
    base = ROOT / "artifacts/v1-expanded-preparation/codex-stdout-score-replay-v2"
    manifest = json.loads((base / "codex-cli-eval-1/manifest.json").read_bytes())
    receipt = json.loads((base / "codex-cli-eval-1-receipt.json").read_bytes())
    _verify_diagnostics_pin(manifest, receipt)
    tampered = deepcopy(receipt)
    tampered["diagnostics"]["intact"]["metrics"][0]["correct"] = 0
    with pytest.raises(EvidenceGap, match="diagnostics digest differs"):
        _verify_diagnostics_pin(manifest, tampered)
    wrong_manifest = deepcopy(manifest)
    wrong_manifest["expected_diagnostics_sha256"] = "0" * 64
    with pytest.raises(EvidenceGap, match="diagnostics digest differs"):
        _verify_diagnostics_pin(wrong_manifest, receipt)
