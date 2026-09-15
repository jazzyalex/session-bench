"""The visible preliminary page must name the active v1 release cohort."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_calibration_snapshot", ROOT / "scripts" / "build_calibration_snapshot.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_snapshot_is_the_prospective_claude_release_cohort(tmp_path: Path) -> None:
    source = ROOT / "plans" / "survival-v1" / "calibration-snapshot.json"
    output = tmp_path / "snapshot"
    MODULE.build(source, output)

    payload = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
    assert [row["configuration_id"] for row in payload["surfaces"]] == [
        "codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli",
    ]
    assert [row["name"] for row in payload["categories"]] == [
        "Record fidelity", "Causality & context", "Usage & attribution",
        "Portability & openness", "Durability & signal",
    ]

    page = (output / "index.html").read_text(encoding="utf-8")
    assert "Claude Code CLI" in page
    assert "Claude Desktop Code (Local)" in page
    assert "Cursor CLI" not in page
