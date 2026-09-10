"""v0.4 preservation checks for the bounded v1 prototype authorization."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).parents[1]
HISTORY = REPO / "docs" / "prototype-history.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v04_preservation_receipt_matches_files():
    receipt = json.loads(HISTORY.read_text())
    assert receipt["approval"]["scope"] == "O1-O5"
    assert receipt["approval"]["status"] == "approved"
    for relative, expected in receipt["v0_4_preservation"]["sha256"].items():
        assert _sha256(REPO / relative) == expected, relative


def test_v04_generation_remains_byte_identical(tmp_path):
    output = tmp_path / "leaderboard.yml"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "evaluate.py"),
            "--measurements",
            str(REPO / "data" / "measurements.json"),
            "--checklist",
            str(REPO / "data" / "verdicts.yml"),
            "--out",
            str(output),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == (REPO / "data" / "leaderboard.yml").read_bytes()
