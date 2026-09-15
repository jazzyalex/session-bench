"""Deterministic replay-runtime snapshot builder.

Copies a fixed closed source closure as opaque bytes without importing,
decoding, or executing any package code and without reading artifacts.
Only the Python standard library is used.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

__all__ = [
    "RUNTIME_SCHEMA_VERSION",
    "RUNTIME_FILES",
    "SOURCE_RELATIVE_FILES",
    "MANIFEST_FILENAME",
    "BuildRuntimeError",
    "ReplayRuntimeError",
    "build_runtime",
]

RUNTIME_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

SOURCE_RUNTIME_FILES: tuple[str, ...] = (
    "session_bench/__init__.py",
    "session_bench/adapters/opencode_decoder.py",
    "session_bench/live_metric_comparator.py",
    "session_bench/survival_metrics.py",
    "session_bench/survival_evidence.py",
    "scripts/build_live_survival_result.py",
    "scripts/recheck_opencode_package.py",
)
_GENERATED_RUNTIME_FILES = {
    "session_bench/adapters/__init__.py": b'"""Minimal adapters namespace for the closed replay runtime."""\n',
}
RUNTIME_FILES: tuple[str, ...] = tuple(
    sorted((*SOURCE_RUNTIME_FILES, *_GENERATED_RUNTIME_FILES))
)

# Alias for discoverability; same fixed closure.
SOURCE_RELATIVE_FILES: tuple[str, ...] = RUNTIME_FILES


class BuildRuntimeError(ValueError, OSError):
    """Invalid replay-runtime request or snapshot mismatch."""


# Backwards-compatible alias (same exception object).
ReplayRuntimeError = BuildRuntimeError


def _manifest_document(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"files": entries, "schema_version": RUNTIME_SCHEMA_VERSION}


def _dump_manifest(document: dict[str, object]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _require_source_file(source_root: Path, relative: str) -> None:
    path = source_root / relative
    if path.is_symlink():
        raise BuildRuntimeError(f"source path is a symlink: {relative}")
    if not path.is_file():
        raise BuildRuntimeError(f"missing source file: {relative}")


def build_runtime(source_root: Path, destination: Path) -> dict:
    """Snapshot the fixed closure from source_root into destination.

    Requires source_root to contain all five fixed relative files with no
    symlinks at those paths. Requires destination to be new (must not exist,
    including as a dangling symlink). Copies exact bytes preserving relative
    paths, writes manifest.json, rereads each destination file and rejects
    any mismatch. Returns the manifest document dict.
    """
    src = Path(source_root)
    dest = Path(destination)

    if src.is_symlink() or not src.is_dir():
        raise BuildRuntimeError(f"source root is not a directory: {src}")
    for relative in SOURCE_RUNTIME_FILES:
        _require_source_file(src, relative)

    if os.path.lexists(dest):
        raise BuildRuntimeError(f"destination already exists: {dest}")
    parent = dest.parent
    # Empty parent (e.g. bare relative name) resolves to ".".
    if str(parent) == "":
        parent = Path(".")
    if not parent.is_dir():
        raise BuildRuntimeError(f"destination parent is not a directory: {parent}")

    try:
        dest.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise BuildRuntimeError(f"destination already exists: {dest}") from exc
    except OSError as exc:
        raise BuildRuntimeError(f"cannot create destination: {exc}") from exc

    # Inspection phase: snapshot expected bytes (first read).
    inspected: dict[str, bytes] = {}
    for relative in SOURCE_RUNTIME_FILES:
        path = src / relative
        if path.is_symlink() or not path.is_file():
            raise BuildRuntimeError(f"missing source file: {relative}")
        inspected[relative] = path.read_bytes()
    inspected.update(_GENERATED_RUNTIME_FILES)

    # Copy phase: re-read source (second read) so mutation between
    # inspection and copy is detectable via the final verification.
    for relative in RUNTIME_FILES:
        if relative in _GENERATED_RUNTIME_FILES:
            data = _GENERATED_RUNTIME_FILES[relative]
        else:
            path = src / relative
            if path.is_symlink() or not path.is_file():
                raise BuildRuntimeError(f"missing source file: {relative}")
            data = path.read_bytes()
        target = dest / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise BuildRuntimeError(f"cannot create runtime layout: {exc}") from exc
        target.write_bytes(data)

    entries: list[dict[str, object]] = []
    for relative in sorted(inspected):
        data = inspected[relative]
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )

    document = _manifest_document(entries)
    (dest / MANIFEST_FILENAME).write_bytes(_dump_manifest(document))

    # Closed-loop check: reread each destination file, reject any mismatch.
    for relative in RUNTIME_FILES:
        target = dest / relative
        if target.is_symlink() or not target.is_file():
            raise BuildRuntimeError(f"missing destination file: {relative}")
        actual = target.read_bytes()
        expected = inspected[relative]
        if actual != expected:
            raise BuildRuntimeError(f"destination mismatch: {relative}")
        if len(actual) != len(expected):
            raise BuildRuntimeError(f"destination size mismatch: {relative}")
        if hashlib.sha256(actual).hexdigest() != hashlib.sha256(expected).hexdigest():
            raise BuildRuntimeError(f"destination sha256 mismatch: {relative}")

    return document
