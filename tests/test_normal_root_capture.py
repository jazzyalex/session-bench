"""Deterministic tests for the authorized normal-root capture contract."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from session_bench.normal_root_capture import (
    NormalRootCapture,
    NormalRootCaptureError,
    capture_new_family,
    copy_verified_family,
    identify_new_family,
    inventory_diff,
    inventory_normal_root,
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inventory_is_metadata_only_and_accepts_existing_calibration_stat_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    old = root / "sessions" / "old.jsonl"
    old.parent.mkdir()
    old.write_bytes(b"pre-existing native content")

    def forbidden_read(*args, **kwargs):
        raise AssertionError("before/after inventory must not read file bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    before = inventory_normal_root(root)
    assert [entry.relative_path for entry in before] == ["sessions/old.jsonl"]
    assert before[0].size_bytes == len(b"pre-existing native content")
    assert before[0].filesystem_id == f"{before[0].device}:{before[0].inode}"

    # Existing l0_preflight CandidateStat-like objects are accepted without
    # making the normal-root module depend on a vendor adapter.
    class LegacyStat:
        relative_path = before[0].relative_path
        device = before[0].device
        inode = before[0].inode
        size = before[0].size
        birth_ns = before[0].birth_ns
        ctime_ns = before[0].ctime_ns
        mtime_ns = before[0].mtime_ns

    assert inventory_diff((LegacyStat(),), before) == ()


def test_changed_preexisting_file_is_rejected_before_any_candidate_is_opened(
    tmp_path: Path,
) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    old = root / "sessions" / "old.jsonl"
    old.parent.mkdir()
    old.write_bytes(b"old")
    before = inventory_normal_root(root)
    old.write_bytes(b"changed")
    after = inventory_normal_root(root)

    with pytest.raises(NormalRootCaptureError, match="pre-existing files changed"):
        inventory_diff(before, after)


def test_explicit_shared_root_mode_ignores_changed_old_bytes_and_selects_only_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    old = root / "active-controller.jsonl"
    old.write_bytes(b"before")
    before = inventory_normal_root(root)
    old.write_bytes(b"after")
    new = root / "new-task.jsonl"
    new.write_bytes(b"synthetic new session")
    after = inventory_normal_root(root)

    family = identify_new_family(
        before,
        after,
        primary_path="new-task.jsonl",
        allow_preexisting_changes=True,
    )
    assert family.paths == ("new-task.jsonl",)

    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        if Path(path) == old:
            raise AssertionError("changed pre-existing bytes must remain unopened")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    copied = copy_verified_family(root, tmp_path / "capture", family)
    assert [row["relative_path"] for row in copied] == ["new-task.jsonl"]


def test_explicit_shared_root_mode_tolerates_unrelated_old_file_removal(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    old = root / "expired.sh"
    old.write_bytes(b"old")
    before = inventory_normal_root(root)
    old.unlink()
    new = root / "new-task.sh"
    new.write_bytes(b"synthetic")
    after = inventory_normal_root(root)

    family = identify_new_family(
        before,
        after,
        primary_path="new-task.sh",
        allow_preexisting_changes=True,
    )

    assert family.paths == ("new-task.sh",)


def test_removed_or_replaced_preexisting_identity_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    old = root / "old.jsonl"
    old.write_bytes(b"old")
    before = inventory_normal_root(root)
    old.unlink()
    old.write_bytes(b"replacement")
    after = inventory_normal_root(root)

    with pytest.raises(NormalRootCaptureError, match="pre-existing files changed"):
        inventory_diff(before, after)

    old.unlink()
    after = inventory_normal_root(root)
    with pytest.raises(NormalRootCaptureError, match="pre-existing files were removed"):
        inventory_diff(before, after)


def test_ambiguous_primary_candidates_stop_before_copy(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    before = inventory_normal_root(root)
    (root / "rollout-a.jsonl").write_bytes(b"a")
    (root / "rollout-b.jsonl").write_bytes(b"b")
    after = inventory_normal_root(root)

    with pytest.raises(NormalRootCaptureError, match="ambiguous"):
        identify_new_family(
            before,
            after,
            primary_predicate=lambda entry: entry.relative_path.endswith(".jsonl"),
        )
    assert not (tmp_path / "capture").exists()


def test_symlink_is_rejected_during_inventory(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    target = tmp_path / "outside.jsonl"
    target.write_bytes(b"outside")
    (root / "rollout.jsonl").symlink_to(target)

    with pytest.raises(NormalRootCaptureError, match="symlink"):
        inventory_normal_root(root)


def test_out_of_root_paths_and_overlapping_destination_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    before = inventory_normal_root(root)
    native = root / "rollout.jsonl"
    native.write_bytes(b"native")
    after = inventory_normal_root(root)

    with pytest.raises(NormalRootCaptureError, match="relative path"):
        identify_new_family(before, after, primary_path="../outside.jsonl")
    family = identify_new_family(before, after, primary_path="rollout.jsonl")
    with pytest.raises(NormalRootCaptureError, match="overlaps"):
        copy_verified_family(root, root / "capture", family)


def test_exact_new_family_is_copied_and_each_artifact_is_hashed(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    (root / "old.txt").write_bytes(b"must stay outside the family")
    before = inventory_normal_root(root)
    primary = root / "events.db"
    wal = root / "events.db-wal"
    shm = root / "events.db-shm"
    primary.write_bytes(b"db")
    wal.write_bytes(b"wal")
    shm.write_bytes(b"shm")
    after = inventory_normal_root(root)

    receipt = capture_new_family(
        root,
        tmp_path / "capture",
        before,
        after,
        primary_suffix=".db",
        companion_suffixes=("-wal", "-shm"),
        sleep=lambda _: None,
    )
    assert receipt.primary_path == "events.db"
    assert receipt.companion_paths == ("events.db-wal", "events.db-shm")
    assert [item["role"] for item in receipt.artifacts] == [
        "native-primary", "native-companion", "native-companion"
    ]
    for artifact in receipt.artifacts:
        source = root / artifact["relative_path"]
        copied = tmp_path / "capture" / artifact["relative_path"]
        assert copied.read_bytes() == source.read_bytes()
        assert artifact["sha256"] == digest(source)
        assert artifact["size_bytes"] == source.stat().st_size
    assert not (tmp_path / "capture" / "old.txt").exists()
    assert receipt.to_dict()["schema_version"] == "1.0-normal-root-capture"


def test_required_companion_must_be_new_and_extra_new_file_invalidates_family(
    tmp_path: Path,
) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    companion = root / "events.db-wal"
    companion.write_bytes(b"old companion")
    before = inventory_normal_root(root)
    (root / "events.db").write_bytes(b"db")
    after = inventory_normal_root(root)
    with pytest.raises(NormalRootCaptureError, match="required companion"):
        identify_new_family(
            before,
            after,
            primary_suffix=".db",
            companion_suffixes=("-wal",),
        )

    # A new unrelated file cannot be silently dropped from the exact family.
    root = tmp_path / "normal-extra"
    root.mkdir()
    before = inventory_normal_root(root)
    (root / "events.db").write_bytes(b"db")
    (root / "events.db-wal").write_bytes(b"new companion")
    (root / "unrelated.jsonl").write_bytes(b"unexpected")
    after = inventory_normal_root(root)
    with pytest.raises(NormalRootCaptureError, match="unexpected newly-created"):
        identify_new_family(
            before,
            after,
            primary_suffix=".db",
            companion_suffixes=("-wal",),
        )


def test_stateful_contract_records_before_once_and_captures_after(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    contract = NormalRootCapture(
        root,
        tmp_path / "capture",
        primary_predicate=lambda entry: entry.relative_path.endswith(".jsonl"),
    )
    assert contract.record_before() == ()
    with pytest.raises(NormalRootCaptureError, match="already recorded"):
        contract.record_before()
    (root / "rollout-new.jsonl").write_bytes(b"new")
    receipt = contract.capture_after(sleep=lambda _: None)
    assert receipt.primary_path == "rollout-new.jsonl"
    assert contract.before_inventory == ()


def test_capture_rejects_source_replacement_after_selection(tmp_path: Path) -> None:
    root = tmp_path / "normal"
    root.mkdir()
    before = inventory_normal_root(root)
    source = root / "rollout-new.jsonl"
    source.write_bytes(b"original")
    after = inventory_normal_root(root)
    family = identify_new_family(before, after, primary_path="rollout-new.jsonl")
    source.unlink()
    source.write_bytes(b"replacement")

    with pytest.raises(NormalRootCaptureError, match="identity changed"):
        copy_verified_family(root, tmp_path / "capture", family)
