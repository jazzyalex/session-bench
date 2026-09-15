"""Synthetic controls for the closed OpenCode replay-runtime snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from session_bench.opencode_replay_runtime import (
    BuildRuntimeError,
    MANIFEST_FILENAME,
    RUNTIME_FILES,
    RUNTIME_SCHEMA_VERSION,
    build_runtime,
)


def _contents() -> dict[str, bytes]:
    return {
        "session_bench/__init__.py": b'"""Synthetic package."""\n',
        "session_bench/adapters/__init__.py": b'"""Minimal adapters namespace for the closed replay runtime."""\n',
        "session_bench/adapters/opencode_decoder.py": b'"""Synthetic decoder."""\n',
        "session_bench/live_metric_comparator.py": b'"""Synthetic comparator."""\n',
        "session_bench/survival_metrics.py": b'"""Synthetic scorer."""\n',
        "session_bench/survival_evidence.py": b'"""Synthetic evidence."""\n',
        "scripts/build_live_survival_result.py": b'"""Synthetic package verifier."""\n',
        "scripts/recheck_opencode_package.py": b'"""Synthetic standalone runner."""\n',
    }


def _source(root: Path) -> Path:
    for relative, value in _contents().items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return root


def test_builds_the_fixed_closure_and_manifest(tmp_path: Path) -> None:
    source = _source(tmp_path / "source")
    destination = tmp_path / "runtime"

    manifest = build_runtime(source, destination)

    assert manifest["schema_version"] == RUNTIME_SCHEMA_VERSION
    assert [entry["path"] for entry in manifest["files"]] == sorted(RUNTIME_FILES)
    on_disk = json.loads((destination / MANIFEST_FILENAME).read_text())
    assert on_disk == manifest
    for entry in manifest["files"]:
        relative = str(entry["path"])
        copied = destination / relative
        assert copied.read_bytes() == _contents()[relative]
        assert entry["size_bytes"] == len(_contents()[relative])
        assert entry["sha256"] == hashlib.sha256(_contents()[relative]).hexdigest()


def test_manifest_is_deterministic(tmp_path: Path) -> None:
    first = build_runtime(_source(tmp_path / "first"), tmp_path / "runtime-first")
    second = build_runtime(_source(tmp_path / "second"), tmp_path / "runtime-second")

    assert first == second
    assert (tmp_path / "runtime-first" / MANIFEST_FILENAME).read_bytes() == (
        tmp_path / "runtime-second" / MANIFEST_FILENAME
    ).read_bytes()


@pytest.mark.parametrize("as_symlink", [False, True])
def test_refuses_an_existing_or_symlink_destination(tmp_path: Path, as_symlink: bool) -> None:
    source = _source(tmp_path / "source")
    destination = tmp_path / "runtime"
    if as_symlink:
        target = tmp_path / "target"
        target.mkdir()
        os.symlink(target, destination)
    else:
        destination.mkdir()

    with pytest.raises(BuildRuntimeError, match="destination already exists"):
        build_runtime(source, destination)


def test_refuses_missing_or_unexpected_source_root(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BuildRuntimeError, match="missing source file"):
        build_runtime(empty, tmp_path / "runtime-empty")

    not_directory = tmp_path / "not-directory"
    not_directory.write_text("not a root")
    with pytest.raises(BuildRuntimeError, match="source root is not a directory"):
        build_runtime(not_directory, tmp_path / "runtime-file")

    source = _source(tmp_path / "source")
    source_link = tmp_path / "source-link"
    os.symlink(source, source_link)
    with pytest.raises(BuildRuntimeError, match="source root is not a directory"):
        build_runtime(source_link, tmp_path / "runtime-link")


def test_refuses_source_mutation_between_inspection_and_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path / "source")
    changed = source / "session_bench/adapters/opencode_decoder.py"
    original_read = Path.read_bytes
    reads = 0

    def mutate_after_first_read(path: Path) -> bytes:
        nonlocal reads
        value = original_read(path)
        if path == changed:
            reads += 1
            if reads == 1:
                path.write_bytes(value + b"# changed after inspection\n")
        return value

    monkeypatch.setattr(Path, "read_bytes", mutate_after_first_read)
    with pytest.raises(BuildRuntimeError, match="destination mismatch"):
        build_runtime(source, tmp_path / "runtime")
