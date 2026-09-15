"""Focused offline controls for the Cursor CLI native survival-v1 decoder."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from session_bench.adapters.cursor_decoder import (
    ABSENT,
    UNKNOWN,
    CursorBundleError,
    decode_cursor_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
RETAINED_PUBLIC_BUNDLE = (
    ROOT
    / "artifacts"
    / "survival-v1-runs"
    / "cursor-cli-setup-2"
    / "capture"
    / "public-native"
)


def copy_sanitized_bundle(tmp_path: Path) -> Path:
    destination = tmp_path / "copied-cursor-native"
    shutil.copytree(RETAINED_PUBLIC_BUNDLE, destination)
    shutil.copy2(
        RETAINED_PUBLIC_BUNDLE.parent / "sanitization-receipt.json",
        destination / "sanitization-receipt.json",
    )
    return destination


def test_retained_sanitized_bundle_recovers_literal_semantic_facts() -> None:
    decoded = decode_cursor_bundle(RETAINED_PUBLIC_BUNDLE)

    assert len(decoded["records"]) == 9
    assert len(decoded["turns"]) == 2
    assert len(decoded["responses"]) == 2
    assert len(decoded["actions"]) == 6
    assert len(decoded["results"]) == 0
    assert len(decoded["file_changes"]) == 1
    assert [item["revision"] for item in decoded["turns"]] == ["r1", "r2"]
    assert decoded["responses"][0]["response_canary"] == "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"
    assert decoded["responses"][1]["response_canary"] == "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
    assert decoded["file_changes"][0]["path"] == "$RUN_ROOT/project/fixture_project/checkout.py"
    assert decoded["file_changes"][0]["fields"]["before_sha256"]["state"] == ABSENT

    # The public derivative contains no result/usage/config records.  The
    # decoder reports that fact directly instead of borrowing observer truth or
    # inventing a missing tool call.
    assert decoded["metrics"]["work.results"]["state"] == ABSENT
    assert decoded["metrics"]["causal.action_result"]["state"] == ABSENT
    assert decoded["metrics"]["attribution.usage"]["state"] == ABSENT
    assert decoded["metrics"]["attribution.model_config"]["state"] == ABSENT
    encoded = json.dumps(decoded.as_dict(), ensure_ascii=False)
    assert "tool987" not in encoded
    assert "queuegz" not in encoded


def test_decode_is_copy_only_and_semantically_stable_after_source_removal(tmp_path: Path) -> None:
    copied = copy_sanitized_bundle(tmp_path)
    source_copy = tmp_path / "source-before-copy"
    shutil.copytree(RETAINED_PUBLIC_BUNDLE, source_copy)
    shutil.copy2(
        RETAINED_PUBLIC_BUNDLE.parent / "sanitization-receipt.json",
        source_copy / "sanitization-receipt.json",
    )
    original = decode_cursor_bundle(source_copy)
    copied_result = decode_cursor_bundle(copied)

    assert copied_result.semantic_sha256 == original.semantic_sha256
    assert copied_result["copy_only"] is True
    assert str(RETAINED_PUBLIC_BUNDLE) not in json.dumps(copied_result.as_dict())

    # The decoder has no fallback to the retained attempt or a normal Cursor
    # profile.  Removing the temporary source after the copied decode is safe.
    shutil.rmtree(source_copy)
    assert decode_cursor_bundle(copied).semantic_sha256 == copied_result.semantic_sha256


def test_attempt_root_is_rejected_before_sibling_project_or_config_reads() -> None:
    attempt_root = ROOT / "artifacts" / "survival-v1-runs" / "cursor-cli-setup-2"
    with pytest.raises(CursorBundleError, match="root-level cursor-session.jsonl|undeclared sibling"):
        decode_cursor_bundle(attempt_root)


def test_damage_control_does_not_reconstruct_removed_native_action(tmp_path: Path) -> None:
    copied = copy_sanitized_bundle(tmp_path)
    transcript = copied / "cursor-session.jsonl"
    lines = transcript.read_text(encoding="utf-8").splitlines()
    damaged = [line for line in lines if '"name":"Write"' not in line]
    transcript.write_text("\n".join(damaged) + "\n", encoding="utf-8")

    decoded = decode_cursor_bundle(copied)

    assert not any(action.get("tool_name") == "Write" for action in decoded["actions"])
    assert not any(action.get("tool_name") == "tool987" for action in decoded["actions"])
    assert "queuegz" not in json.dumps(decoded.as_dict(), ensure_ascii=False)
    assert decoded["metrics"]["work.actions"]["state"] == UNKNOWN
    assert any(item["code"] == "receipt_digest_mismatch" for item in decoded["diagnostics"])


def test_normal_cursor_roots_are_never_accepted(tmp_path: Path) -> None:
    normal = Path.home() / ".cursor"
    with pytest.raises(CursorBundleError, match="normal or personal"):
        decode_cursor_bundle(normal)

    outside = tmp_path / "bundle"
    outside.mkdir()
    (outside / "cursor-session.jsonl").write_text("{}\n", encoding="utf-8")
    (outside / "extra.txt").write_text("undeclared\n", encoding="utf-8")
    with pytest.raises(CursorBundleError, match="undeclared sibling"):
        decode_cursor_bundle(outside)
