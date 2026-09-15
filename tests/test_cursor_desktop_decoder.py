"""Copied-bundle controls for the observed Cursor Desktop JSONL transcript."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import pytest

from session_bench.adapters.cursor_decoder import (
    KNOWN,
    UNKNOWN,
    DESKTOP_FORMAT_VERSION,
    CursorBundleError,
    CursorDesktopTranscriptDecoder,
    decode_cursor_desktop_bundle,
)


BUNDLE = Path(__file__).resolve().parent / "fixtures" / "cursor_desktop_cal_6"


def test_desktop_transcript_and_session_companions_reconstruct_native_facts() -> None:
    result = CursorDesktopTranscriptDecoder().decode(BUNDLE)

    assert result["format"] == DESKTOP_FORMAT_VERSION
    assert len(result.turns) == 2
    assert len(result.responses) == 2
    assert len(result.actions) == 9
    assert len(result.file_changes) == 1
    assert len(result.results) == 9
    assert [item["response_canary"] for item in result.responses] == [
        "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
        "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ",
    ]
    assert result.metrics["work.submitted_turns"]["state"] == KNOWN
    assert result.metrics["causal.turn_response"]["state"] == KNOWN
    assert result.metrics["work.results"]["state"] == KNOWN
    assert result.metrics["causal.action_result"]["state"] == KNOWN
    assert result.metrics["revision.final_after_r2"]["state"] == UNKNOWN
    assert result.metrics["portable.complete_root"]["state"] == UNKNOWN
    assert result.metrics["attribution.model_config"]["state"] == KNOWN
    assert result.facts["model"]["state"] == KNOWN
    assert result.facts["companions"]["state"] == KNOWN


def test_desktop_copy_and_damage_control(tmp_path: Path) -> None:
    copied = tmp_path / "copied-desktop-native"
    shutil.copytree(BUNDLE, copied)
    original = decode_cursor_desktop_bundle(BUNDLE)
    assert decode_cursor_desktop_bundle(copied).semantic_sha256 == original.semantic_sha256

    transcript = copied / "cursor-session.jsonl"
    records = [json.loads(line) for line in transcript.read_text().splitlines()]
    records = [
        record for record in records
        if not (
            record.get("role") == "assistant"
            and any(
                block.get("type") == "text"
                and block.get("text", "").rstrip().endswith("SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ")
                for block in record.get("message", {}).get("content", [])
            )
        )
    ]
    transcript.write_text("\n".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in records) + "\n")
    receipt_path = copied / "sanitization-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["sanitized_sha256"] = hashlib.sha256(transcript.read_bytes()).hexdigest()
    receipt["line_count"] = len(records)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")

    with pytest.raises(CursorBundleError, match="response boundaries conflict"):
        decode_cursor_desktop_bundle(copied)


def test_declared_companion_cannot_be_silently_dropped(tmp_path: Path) -> None:
    copied = tmp_path / "missing-companion"
    shutil.copytree(BUNDLE, copied)
    (copied / "session-companions.jsonl").unlink()
    result = decode_cursor_desktop_bundle(copied)
    assert result.facts["bundle_integrity"]["state"] == UNKNOWN
    assert result.metrics["work.results"]["state"] == UNKNOWN
    assert any(item["code"] == "companion_missing" for item in result.diagnostics)


def test_missing_receipt_is_not_verified(tmp_path: Path) -> None:
    copied = tmp_path / "missing-receipt"
    shutil.copytree(BUNDLE, copied)
    (copied / "sanitization-receipt.json").unlink()
    result = decode_cursor_desktop_bundle(copied)
    assert result.facts["bundle_integrity"]["state"] == UNKNOWN
    assert result.facts["bundle_integrity"]["partial"]["receipt_verified"] is False
    assert any(item["code"] == "receipt_missing" for item in result["diagnostics"])
