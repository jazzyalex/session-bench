from pathlib import Path
import shutil

import pytest

from session_bench.copilot_native_copy import (
    RECEIPT_SCHEMA,
    copy_quiescent_copilot_session,
)


SESSION = "16c91d5b-9863-48d6-9fc6-50f28a9ed767"


def _session(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "copilot"
    source = home / "session-state" / SESSION
    (source / "checkpoints").mkdir(parents=True)
    (source / "events.jsonl").write_text('{"type":"session.start"}\n')
    (source / "checkpoints/index.md").write_text("checkpoint")
    return home, source


def test_copy_receipt_binds_stable_complete_session_tree(tmp_path: Path):
    home, _ = _session(tmp_path)
    receipt = copy_quiescent_copilot_session(
        home, SESSION, tmp_path / "capture/native", sleep=lambda _: None
    )

    assert receipt["schema_version"] == RECEIPT_SCHEMA
    assert receipt["source_relative_path"] == f"session-state/{SESSION}"
    assert receipt["destination_relative_path"] == f"native/{SESSION}"
    assert receipt["complete_selected_session_directory"] is True
    assert receipt["source_unchanged_during_copy"] is True
    assert receipt["copy_matches_source"] is True
    assert len(set(receipt["quiescence_observation_sha256"])) == 1
    assert set(receipt["files"]) == {"checkpoints/index.md", "events.jsonl"}
    assert (tmp_path / "capture/native" / SESSION / "checkpoints/index.md").read_text() == "checkpoint"


def test_copy_rejects_source_change_during_quiescence(tmp_path: Path):
    home, source = _session(tmp_path)
    calls = 0

    def mutate(_: float) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            (source / "events.jsonl").write_text('{"type":"changed"}\n')

    with pytest.raises(ValueError, match="changed during quiescence"):
        copy_quiescent_copilot_session(
            home, SESSION, tmp_path / "capture/native", sleep=mutate
        )
    assert not (tmp_path / "capture/native" / SESSION).exists()


def test_copy_rejects_source_change_during_copy(tmp_path: Path):
    home, source = _session(tmp_path)

    def mutating_copy(src: Path, destination: Path) -> object:
        result = shutil.copytree(src, destination)
        (source / "late-companion.json").write_text("late")
        return result

    with pytest.raises(ValueError, match="changed during copy"):
        copy_quiescent_copilot_session(
            home,
            SESSION,
            tmp_path / "capture/native",
            sleep=lambda _: None,
            copy_tree=mutating_copy,
        )


def test_copy_rejects_symlinked_family_member(tmp_path: Path):
    home, source = _session(tmp_path)
    (source / "events-link.jsonl").symlink_to(source / "events.jsonl")
    with pytest.raises(ValueError, match="symlink"):
        copy_quiescent_copilot_session(
            home, SESSION, tmp_path / "capture/native", sleep=lambda _: None
        )
