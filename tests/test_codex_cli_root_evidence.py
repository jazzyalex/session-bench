"""Retained metadata-only Codex CLI root receipts yield the stable-root rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_bench.codex_cli_root_evidence import FILES, SOURCES, verify_codex_cli_root_evidence

ROOT = Path(__file__).resolve().parents[1]


def _copied(tmp_path: Path) -> Path:
    for repetition in (1, 2, 3):
        run = ROOT / f"artifacts/survival-v1-runs/codex-cli-eval-{repetition}"
        folder = tmp_path / f"repetition-{repetition}"
        folder.mkdir()
        for name in FILES:
            (folder / name).write_bytes((run / SOURCES[name]).read_bytes())
    return tmp_path


def _rewrite(path: Path, change) -> None:
    value = json.loads(path.read_bytes())
    change(value)
    path.write_text(json.dumps(value))


def test_retained_receipts_yield_three_metadata_safe_root_rows(tmp_path: Path) -> None:
    rows = verify_codex_cli_root_evidence(_copied(tmp_path))

    assert rows == [
        {"repetition": repetition,
         "root_locator": "CODEX_HOME/sessions/YYYY/MM/DD/rollout-<session-id>.jsonl",
         "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False}
        for repetition in (1, 2, 3)
    ]


def test_a_receipt_that_opened_preexisting_content_is_rejected(tmp_path: Path) -> None:
    root = _copied(tmp_path)
    _rewrite(root / "repetition-2/normal-root-receipt.json",
             lambda value: value.update(preexisting_native_content_opened=True))

    with pytest.raises(ValueError, match="repetition 2"):
        verify_codex_cli_root_evidence(root)


def test_a_native_inventory_that_is_not_the_selected_file_is_rejected(tmp_path: Path) -> None:
    root = _copied(tmp_path)
    _rewrite(root / "repetition-3/decode.json",
             lambda value: value["artifacts"][0].update(sha256="0" * 64))

    with pytest.raises(ValueError, match="repetition 3"):
        verify_codex_cli_root_evidence(root)
