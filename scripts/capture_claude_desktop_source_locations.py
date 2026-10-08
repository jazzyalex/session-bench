#!/usr/bin/env python3
"""Acquire private Claude Desktop source-location evidence for one future run.

The CLI derives the two normal Claude roots. Their inventories contain stat
metadata only; source contents are opened only for the changed Desktop metadata
named by the caller's live UI session identity and its exact CLI transcript.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from session_bench.claude_desktop_source_discovery import (
    INVENTORY_SCHEMA,
    SELECTION_SCHEMA,
    canonical_claude_desktop_roots,
    verify_claude_desktop_source_discovery,
)


MAX_SELECTED_BYTES = 64 * 1024 * 1024


class ClaudeDesktopSourceCaptureError(RuntimeError):
    """Source acquisition was unsafe, incomplete, or ambiguous."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ClaudeDesktopSourceCaptureError(message)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _same_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev, left.st_ino, left.st_mode, left.st_size,
        left.st_ctime_ns, left.st_mtime_ns,
    ) == (
        right.st_dev, right.st_ino, right.st_mode, right.st_size,
        right.st_ctime_ns, right.st_mtime_ns,
    )


def _ordinary_absolute_directory(path: Path, label: str) -> tuple[Path, os.stat_result]:
    path = Path(path)
    _require(path.is_absolute(), f"{label} must be an explicit absolute path")
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ClaudeDesktopSourceCaptureError(f"{label} is inaccessible") from exc
    _require(stat.S_ISDIR(before.st_mode) and not stat.S_ISLNK(before.st_mode),
             f"{label} is not an ordinary directory")
    _require(resolved == path, f"{label} contains a path alias")
    return path, before


def _kind(root_id: str, path: Path) -> str:
    if root_id == "claude-projects" and path.suffix == ".jsonl":
        return "transcript"
    if root_id == "claude-desktop-sessions" and path.suffix == ".json":
        return "desktop_metadata"
    return "other"


def _walk_root(
    root: Path, *, root_id: str, seen_file_ids: set[tuple[int, int]],
) -> tuple[list[dict[str, Any]], os.stat_result]:
    root, root_stat = _ordinary_absolute_directory(root, root_id)
    entries: list[dict[str, Any]] = []
    directory_snapshots: dict[Path, os.stat_result] = {}

    def visit(directory: Path) -> None:
        try:
            directory_before = directory.lstat()
            with os.scandir(directory) as children:
                children_sorted = sorted(children, key=lambda item: item.name)
            for child in children_sorted:
                info = child.stat(follow_symlinks=False)
                source = Path(child.path)
                _require(not stat.S_ISLNK(info.st_mode),
                         f"{root_id} contains a symlink")
                _require(source.resolve(strict=True) == source,
                         f"{root_id} contains an aliased path")
                if stat.S_ISDIR(info.st_mode):
                    visit(source)
                    continue
                _require(stat.S_ISREG(info.st_mode),
                         f"{root_id} contains a special file")
                file_id = (info.st_dev, info.st_ino)
                _require(file_id not in seen_file_ids,
                         "Claude roots contain duplicate filesystem identities")
                seen_file_ids.add(file_id)
                entries.append({
                    "source_path": str(source),
                    "resolved_path": str(source),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "size_bytes": info.st_size,
                    "ctime_ns": info.st_ctime_ns,
                    "mtime_ns": info.st_mtime_ns,
                    "kind": _kind(root_id, source),
                    "is_regular_file": True,
                    "is_symlink": False,
                })
            directory_after = directory.lstat()
        except (OSError, UnicodeError) as exc:
            raise ClaudeDesktopSourceCaptureError(
                f"{root_id} inventory walk was incomplete"
            ) from exc
        _require(_same_stat(directory_before, directory_after),
                 f"{root_id} changed during inventory walk")
        directory_snapshots[directory] = directory_after

    visit(root)
    try:
        for directory, expected in directory_snapshots.items():
            _require(_same_stat(expected, directory.lstat()),
                     f"{root_id} changed before inventory completion")
        for entry in entries:
            observed = Path(entry["source_path"]).lstat()
            expected = (
                entry["device"], entry["inode"], entry["size_bytes"],
                entry["ctime_ns"], entry["mtime_ns"],
            )
            actual = (
                observed.st_dev, observed.st_ino, observed.st_size,
                observed.st_ctime_ns, observed.st_mtime_ns,
            )
            _require(stat.S_ISREG(observed.st_mode) and actual == expected,
                     f"{root_id} file changed before inventory completion")
    except OSError as exc:
        raise ClaudeDesktopSourceCaptureError(
            f"{root_id} inventory verification was incomplete"
        ) from exc
    final_root_stat = root.lstat()
    _require(_same_stat(root_stat, final_root_stat),
             f"{root_id} root changed during inventory walk")
    return sorted(entries, key=lambda item: item["source_path"]), final_root_stat


def inventory_claude_roots(
    *, transcript_root: Path, desktop_root: Path, run_id: str,
    repetition: int, phase: str, captured_at_ns: int | None = None,
) -> dict[str, Any]:
    """Return a complete metadata-only inventory of two explicit roots."""
    _require(isinstance(run_id, str) and run_id, "run identity is missing")
    _require(type(repetition) is int and repetition > 0, "repetition is invalid")
    _require(phase in {"before", "after"}, "inventory phase is invalid")
    transcript_root = Path(transcript_root)
    desktop_root = Path(desktop_root)
    _require(transcript_root != desktop_root
             and transcript_root not in desktop_root.parents
             and desktop_root not in transcript_root.parents,
             "Claude roots overlap")
    seen_file_ids: set[tuple[int, int]] = set()
    roots = []
    for root_id, path in (
        ("claude-projects", transcript_root),
        ("claude-desktop-sessions", desktop_root),
    ):
        rows, root_stat = _walk_root(path, root_id=root_id,
                                     seen_file_ids=seen_file_ids)
        roots.append({
            "root_id": root_id,
            "source_root": str(path),
            "resolved_root": str(path),
            "filesystem_id": f"{root_stat.st_dev}:{root_stat.st_ino}",
            "complete": True,
            "aliases_detected": False,
            "entry_count": len(rows),
            "entries": rows,
        })
    when = captured_at_ns if captured_at_ns is not None else __import__("time").time_ns()
    _require(type(when) is int and when > 0, "capture time is invalid")
    return {
        "schema_version": INVENTORY_SCHEMA,
        "run_id": run_id,
        "repetition": repetition,
        "phase": phase,
        "captured_at_ns": when,
        "metadata_only": True,
        "personal_history_content_read": False,
        "unrelated_content_read": False,
        "complete": True,
        "root_count": 2,
        "entry_count": sum(root["entry_count"] for root in roots),
        "roots": roots,
    }


def _entries(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        entry["source_path"]: entry
        for root in inventory["roots"]
        for entry in root["entries"]
    }


def _metadata_identity(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(entry[key] for key in (
        "device", "inode", "size_bytes", "ctime_ns", "mtime_ns", "kind",
    ))


def _changed_entries(
    before: Mapping[str, Any], after: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], int]:
    old, new = _entries(before), _entries(after)
    changed = [
        entry for path, entry in new.items()
        if path not in old or _metadata_identity(entry) != _metadata_identity(old[path])
    ]
    transcripts = [entry for entry in changed if entry["kind"] == "transcript"]
    metadata = [entry for entry in changed if entry["kind"] == "desktop_metadata"]
    other_count = len(changed) - len(transcripts) - len(metadata)
    return transcripts, metadata, other_count


def _isolate_desktop_metadata(
    before: Mapping[str, Any], after: Mapping[str, Any],
    *, expected_desktop_session_id: str,
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any], int, int]:
    transcripts, metadata, other_count = _changed_entries(before, after)
    matches = [entry for entry in metadata
               if Path(entry["source_path"]).name == f"{expected_desktop_session_id}.json"]
    _require(len(matches) == 1,
             "run did not isolate exactly one Desktop metadata file for the expected UI session "
             f"(changed counts: transcript={len(transcripts)}, "
             f"desktop_metadata={len(metadata)}, other={other_count})")
    return transcripts, matches[0], other_count, len(metadata)


def _read_selected(entry: Mapping[str, Any]) -> bytes:
    path = entry["source_path"]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ClaudeDesktopSourceCaptureError("selected source cannot be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        _require(stat.S_ISREG(before.st_mode), "selected source is not a regular file")
        _require((before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns, before.st_mtime_ns)
                 == tuple(entry[key] for key in (
                     "device", "inode", "size_bytes", "ctime_ns", "mtime_ns")),
                 "selected source changed after inventory")
        _require(before.st_size <= MAX_SELECTED_BYTES, "selected source exceeds size limit")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(remaining, 1024 * 1024))
            _require(bool(block), "selected source ended before its inventoried size")
            chunks.append(block)
            remaining -= len(block)
        _require(os.read(descriptor, 1) == b"", "selected source grew while being read")
        after = os.fstat(descriptor)
        _require(_same_stat(before, after), "selected source changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _json_object(raw: bytes, label: str) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ClaudeDesktopSourceCaptureError(f"{label} is invalid JSON") from exc
    _require(isinstance(value, Mapping), f"{label} is not a JSON object")
    return value


def _contains_marker(value: Any, marker: str) -> bool:
    if isinstance(value, str):
        return marker in value
    if isinstance(value, Mapping):
        return any(_contains_marker(item, marker) for item in value.values())
    if isinstance(value, list):
        return any(_contains_marker(item, marker) for item in value)
    return False


def _validate_desktop_metadata(
    desktop_raw: bytes, desktop_entry: Mapping[str, Any], *, workspace: Path,
) -> tuple[str, str]:
    desktop = _json_object(desktop_raw, "selected Desktop metadata")
    desktop_session_id = desktop.get("sessionId")
    _require(isinstance(desktop_session_id, str) and desktop_session_id.startswith("local_")
             and Path(desktop_entry["source_path"]).name == f"{desktop_session_id}.json",
             "selected Desktop session identity is invalid")
    cli_session_id = desktop.get("cliSessionId")
    _require(isinstance(cli_session_id, str) and cli_session_id
             and "/" not in cli_session_id and "\\" not in cli_session_id
             and cli_session_id not in {".", ".."},
             "selected Desktop CLI session identity is invalid")
    _require(desktop.get("cwd") == str(workspace),
             "selected Desktop workspace identity mismatch")
    bridge = desktop.get("bridgeSessionIds")
    _require(isinstance(bridge, list) and len(bridge) == 1
             and isinstance(bridge[0], str) and bridge[0],
             "selected Desktop bridge identity is invalid")
    return cli_session_id, desktop_session_id


def _validate_selected_transcript(
    transcript_raw: bytes, *, workspace: Path, run_marker: str,
    cli_session_id: str,
) -> None:
    _require(isinstance(run_marker, str) and len(run_marker) >= 16,
             "run marker must be an exact, high-entropy string")
    rows = []
    for number, line in enumerate(transcript_raw.splitlines(), 1):
        _require(bool(line.strip()), f"selected transcript line {number} is blank")
        rows.append(_json_object(line, f"selected transcript line {number}"))
    _require(bool(rows), "selected transcript is empty")
    session_ids = {row.get("sessionId") for row in rows if isinstance(row.get("sessionId"), str)}
    _require(session_ids == {cli_session_id},
             "selected transcript CLI session identity mismatch")
    workspace_text = str(workspace)
    _require(any(row.get("cwd") == workspace_text for row in rows),
             "selected transcript workspace identity mismatch")
    _require(any(_contains_marker(row, run_marker) for row in rows),
             "selected transcript run identity marker is missing")


def _isolate_transcript(
    transcripts: Sequence[Mapping[str, Any]], cli_session_id: str,
    *, other_count: int, metadata_count: int,
) -> Mapping[str, Any]:
    matches = [entry for entry in transcripts
               if Path(entry["source_path"]).name == f"{cli_session_id}.jsonl"]
    _require(len(matches) == 1,
             "run did not isolate exactly one transcript for the Desktop CLI session "
             f"(changed counts: transcript={len(transcripts)}, "
             f"desktop_metadata={metadata_count}, other={other_count})")
    return matches[0]


def _selection(
    entry: Mapping[str, Any], *, role: str, run_id: str, repetition: int,
    cli_session_id: str, desktop_session_id: str, raw: bytes,
) -> dict[str, Any]:
    return {
        "schema_version": SELECTION_SCHEMA,
        "role": role,
        "run_id": run_id,
        "repetition": repetition,
        "cli_session_id": cli_session_id,
        "desktop_session_id": desktop_session_id,
        "source_path": entry["source_path"],
        "device": entry["device"],
        "inode": entry["inode"],
        "size_bytes": entry["size_bytes"],
        "ctime_ns": entry["ctime_ns"],
        "mtime_ns": entry["mtime_ns"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "synthetic_fixture": True,
    }


def _private_temp_snapshot(before: Mapping[str, Any], temp_parent: Path | None) -> Path:
    parent = Path(temp_parent or tempfile.gettempdir()).resolve(strict=True)
    _require(parent.is_dir() and not parent.is_symlink(), "snapshot temp parent is invalid")
    _require(parent != REPOSITORY and REPOSITORY not in parent.parents,
             "raw inventory snapshot must be outside the repository")
    descriptor, name = tempfile.mkstemp(prefix="session-bench-claude-desktop-before-",
                                        suffix=".json", dir=parent)
    path = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_canonical_bytes(before))
            stream.flush()
            os.fsync(stream.fileno())
        _require(path.stat().st_mode & 0o077 == 0, "raw inventory snapshot is not private")
        return path
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def _write_private_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path = Path(path)
    _require(path.is_absolute(), "private receipt path must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ClaudeDesktopSourceCaptureError("private receipt already exists or is unsafe") from exc
    try:
        os.fchmod(descriptor, 0o600)
        payload = _canonical_bytes(receipt)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, "private receipt write failed")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_expected_desktop_session_id(value: Any) -> str:
    _require(isinstance(value, str)
             and value.startswith("local_") and len(value) > len("local_")
             and all(character.isascii() and (character.isalnum() or character in "-_")
                     for character in value),
             "expected Desktop session identity must be an explicit local session ID from the live UI")
    return value


def _read_expected_desktop_session_id_file(path: Path) -> str:
    """Read a bounded caller-owned UI identity file, never a session candidate."""
    path = Path(path)
    try:
        metadata = path.lstat()
        _require(path.is_absolute() and path.resolve(strict=True) == path
                 and stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
                 and metadata.st_uid == os.getuid() and metadata.st_mode & 0o077 == 0
                 and 0 < metadata.st_size <= 256,
                 "expected Desktop session ID file must be a private ordinary bounded file")
    except OSError as exc:
        raise ClaudeDesktopSourceCaptureError("expected Desktop session ID file is inaccessible") from exc
    raw = _read_selected({
        "source_path": str(path), "device": metadata.st_dev, "inode": metadata.st_ino,
        "size_bytes": metadata.st_size, "ctime_ns": metadata.st_ctime_ns,
        "mtime_ns": metadata.st_mtime_ns,
    })
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ClaudeDesktopSourceCaptureError("expected Desktop session ID file is malformed") from exc
    return _validate_expected_desktop_session_id(value.removesuffix("\n"))


def capture_claude_desktop_source_locations(
    *,
    transcript_root: Path,
    desktop_root: Path,
    workspace: Path,
    run_id: str,
    repetition: int,
    run_marker: str,
    run_action: Callable[[], Any],
    expected_desktop_session_id: str | None = None,
    expected_desktop_session_id_file: Path | None = None,
    receipt_path: Path | None = None,
    temp_parent: Path | None = None,
    expected_roots: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Run one caller action between bounded inventories and return its receipt."""
    _require((expected_desktop_session_id is None) != (expected_desktop_session_id_file is None),
             "supply exactly one expected Desktop session identity or ID file")
    if expected_desktop_session_id is not None:
        _validate_expected_desktop_session_id(expected_desktop_session_id)
    if expected_desktop_session_id_file is not None:
        _require(Path(expected_desktop_session_id_file).is_absolute(),
                 "expected Desktop session ID file path must be absolute")
    roots = canonical_claude_desktop_roots() if expected_roots is None else expected_roots
    _require(Path(transcript_root) == roots["transcript"]
             and Path(desktop_root) == roots["desktop_metadata"],
             "capture roots do not match the canonical Claude roots")
    workspace = Path(workspace)
    _require(workspace.is_absolute() and workspace.resolve(strict=True) == workspace
             and workspace.is_dir() and not workspace.is_symlink(),
             "workspace must be an ordinary explicit directory")
    before = inventory_claude_roots(
        transcript_root=transcript_root, desktop_root=desktop_root,
        run_id=run_id, repetition=repetition, phase="before",
    )
    snapshot = _private_temp_snapshot(before, temp_parent)
    try:
        run_action()
        if expected_desktop_session_id_file is not None:
            expected_desktop_session_id = _read_expected_desktop_session_id_file(
                Path(expected_desktop_session_id_file),
            )
        after = inventory_claude_roots(
            transcript_root=transcript_root, desktop_root=desktop_root,
            run_id=run_id, repetition=repetition, phase="after",
        )
        transcripts, desktop_entry, other_count, metadata_count = _isolate_desktop_metadata(
            before, after, expected_desktop_session_id=expected_desktop_session_id,
        )
        desktop_raw = _read_selected(desktop_entry)
        cli_session_id, desktop_session_id = _validate_desktop_metadata(
            desktop_raw, desktop_entry, workspace=workspace,
        )
        _require(desktop_session_id == expected_desktop_session_id,
                 "selected Desktop session does not match the expected UI session")
        transcript_entry = _isolate_transcript(
            transcripts, cli_session_id, other_count=other_count,
            metadata_count=metadata_count,
        )
        transcript_raw = _read_selected(transcript_entry)
        _validate_selected_transcript(
            transcript_raw, workspace=workspace, run_marker=run_marker,
            cli_session_id=cli_session_id,
        )
        receipt = verify_claude_desktop_source_discovery(
            run_id=run_id,
            repetition=repetition,
            cli_session_id=cli_session_id,
            desktop_session_id=desktop_session_id,
            before_inventory=before,
            after_inventory=after,
            selected_transcript=_selection(
                transcript_entry, role="transcript", run_id=run_id,
                repetition=repetition, cli_session_id=cli_session_id,
                desktop_session_id=desktop_session_id, raw=transcript_raw,
            ),
            selected_desktop_metadata=_selection(
                desktop_entry, role="desktop_metadata", run_id=run_id,
                repetition=repetition, cli_session_id=cli_session_id,
                desktop_session_id=desktop_session_id, raw=desktop_raw,
            ),
        )
        if receipt_path is not None:
            _write_private_receipt(Path(receipt_path), receipt)
        return {
            "private_receipt": receipt,
            "selected_absolute_paths": [
                transcript_entry["source_path"], desktop_entry["source_path"],
            ],
        }
    finally:
        try:
            snapshot.unlink(missing_ok=True)
        except OSError as exc:
            raise ClaudeDesktopSourceCaptureError(
                "raw inventory snapshot could not be removed"
            ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-root", type=Path, help="must equal the canonical CLAUDE_HOME/projects root")
    parser.add_argument("--desktop-root", type=Path, help="must equal the canonical Claude Application Support session root")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repetition", type=int, required=True)
    parser.add_argument("--run-marker", required=True)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--expected-desktop-session-id",
                          help="exact local_ session ID observed in the live Claude Desktop task URL")
    identity.add_argument("--expected-desktop-session-id-file", type=Path,
                          help="private UI identity file read after the capture command completes")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--temp-parent", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    roots = canonical_claude_desktop_roots()
    _require(args.transcript_root is None or args.transcript_root == roots["transcript"],
             "transcript root does not match the canonical Claude root")
    _require(args.desktop_root is None or args.desktop_root == roots["desktop_metadata"],
             "Desktop root does not match the canonical Claude root")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    _require(bool(command), "an explicit capture command is required")

    def run() -> None:
        try:
            subprocess.run(command, check=True, shell=False)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ClaudeDesktopSourceCaptureError("capture command failed") from exc

    result = capture_claude_desktop_source_locations(
        transcript_root=roots["transcript"],
        desktop_root=roots["desktop_metadata"],
        workspace=args.workspace,
        run_id=args.run_id,
        repetition=args.repetition,
        run_marker=args.run_marker,
        expected_desktop_session_id=args.expected_desktop_session_id,
        expected_desktop_session_id_file=args.expected_desktop_session_id_file,
        run_action=run,
        receipt_path=args.receipt,
        temp_parent=args.temp_parent,
    )
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
