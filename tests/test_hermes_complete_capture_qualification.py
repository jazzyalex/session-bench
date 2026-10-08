"""Offline qualification of the retained clean Hermes repetition."""

from pathlib import Path
import shutil

import pytest

from scripts.qualify_hermes_complete_capture import (
    CAPTURE_ID, QualificationError, ROOT, verify_complete_capture,
)


CAPTURE = ROOT / "artifacts/v1-expanded-preparation/live-captures" / CAPTURE_ID


def test_completed_two_turn_capture_qualifies_without_score() -> None:
    receipt = verify_complete_capture(CAPTURE)
    assert receipt["capture_id"] == CAPTURE_ID
    assert receipt["repetition"] == 2
    assert receipt["model"] == "gpt-5.5"
    assert receipt["capture_complete"] is True
    assert receipt["same_session_continuation"] is True
    assert receipt["complete_shared_sqlite_root"] is False
    assert receipt["score_eligible"] is receipt["publication_eligible"] is False
    assert receipt["independent_native_to_score_replay"] is False
    assert receipt["api_calls"] == 4
    assert receipt["evidence_files"]["turn-r2/native/session.jsonl"]


def test_mutated_observer_or_export_is_rejected(tmp_path: Path) -> None:
    copy = tmp_path / CAPTURE_ID
    shutil.copytree(CAPTURE, copy)
    prompt = copy / "observer/prompt-r2.txt"
    prompt.write_bytes(prompt.read_bytes() + b" changed")
    with pytest.raises(QualificationError, match="submitted-input witness"):
        verify_complete_capture(copy)

    shutil.rmtree(copy)
    shutil.copytree(CAPTURE, copy)
    native = copy / "turn-r2/native/session.jsonl"
    native.write_bytes(native.read_bytes() + b"\n")
    with pytest.raises(QualificationError, match="official exact-session export receipt"):
        verify_complete_capture(copy)
