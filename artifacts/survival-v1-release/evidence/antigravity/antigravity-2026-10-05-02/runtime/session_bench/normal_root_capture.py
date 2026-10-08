"""Fail-closed before/after capture for an authorized normal native root.

Live calibrations sometimes have to use the product's ordinary account root.
This module provides the narrow boundary needed for that route without making
the ordinary root an evidence bundle:

* inventory uses ``lstat``/``stat`` metadata only and never opens old files;
* the strict default rejects every changed, removed, moved, or replaced
  pre-existing file; an explicit shared-root mode may tolerate metadata
  changes to old files while still refusing to open or copy them;
* exactly one newly-created primary and its explicitly declared companions
  form the copy family;
* source paths are opened without following symlinks and are checked before
  and after the copy; and
* each copied file is hashed while it is copied into a new immutable output.

The module does not launch a vendor client, discover a home directory, or
read a native file until the caller has supplied a valid before/after pair and
the family selector has proven the files are new.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
from typing import Any, Callable, Mapping, Sequence


class NormalRootCaptureError(RuntimeError):
    """The normal-root capture cannot be accepted without weakening a boundary."""


# Familiar short name for callers that use the other capture module's error.
CaptureError = NormalRootCaptureError


def _safe_relative_path(value: Any, field: str = "relative_path") -> str:
    """Validate the one relative-path spelling accepted by the contract."""

    if isinstance(value, Path):
        value = value.as_posix()
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise NormalRootCaptureError(f"{field} must be a non-empty POSIX relative path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise NormalRootCaptureError(f"{field} is not a canonical relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.as_posix() != value:
        raise NormalRootCaptureError(f"{field} is not a canonical relative path")
    return value


def _absolute_path(value: Path | str) -> Path:
    return Path(value).expanduser().absolute()


def _normal_root(value: Path | str) -> Path:
    """Return an existing ordinary directory, rejecting a symlink root."""

    root = _absolute_path(value)
    try:
        info = root.lstat()
    except OSError as exc:
        raise NormalRootCaptureError(f"normal root is not readable: {root}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise NormalRootCaptureError(f"normal root is a symlink: {root}")
    if not stat.S_ISDIR(info.st_mode):
        raise NormalRootCaptureError(f"normal root is not a directory: {root}")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise NormalRootCaptureError(f"normal root cannot be resolved: {root}") from exc
    if resolved != root:
        # A symlink in a parent is not itself a native entry, but resolving the
        # root gives the physical boundary used for every later containment
        # check.  The root's own symlink was rejected above.
        root = resolved
    return root


def _path_inside(root: Path, relative_path: str, field: str) -> Path:
    """Resolve one declared relative path and reject symlink components."""

    relative = _safe_relative_path(relative_path, field)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            # Missing final/parent components are checked by the caller.  A
            # missing path cannot be a symlink escape at this point.
            continue
        except OSError as exc:
            raise NormalRootCaptureError(f"cannot inspect {field}: {relative}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise NormalRootCaptureError(f"{field} contains a symlink: {relative}")
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise NormalRootCaptureError(f"{field} escapes the declared normal root: {relative}") from exc
    return candidate


def _birth_ns(info: os.stat_result) -> int | None:
    value = getattr(info, "st_birthtime", None)
    return None if value is None else int(value * 1_000_000_000)


@dataclass(frozen=True, slots=True)
class NormalRootFile:
    """Metadata for one ordinary file, collected without reading its bytes."""

    relative_path: str
    device: int
    inode: int
    size: int
    birth_ns: int | None
    ctime_ns: int
    mtime_ns: int

    def __post_init__(self) -> None:
        _safe_relative_path(self.relative_path)
        if type(self.device) is not int or self.device < 0:
            raise ValueError("device must be a nonnegative integer")
        if type(self.inode) is not int or self.inode < 0:
            raise ValueError("inode must be a nonnegative integer")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("size must be a nonnegative integer")
        for name in ("ctime_ns", "mtime_ns"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.birth_ns is not None and (type(self.birth_ns) is not int or self.birth_ns < 0):
            raise ValueError("birth_ns must be a nonnegative integer or None")

    @property
    def identity(self) -> tuple[int, int]:
        return self.device, self.inode

    @property
    def filesystem_id(self) -> str:
        return f"{self.device}:{self.inode}"

    @property
    def size_bytes(self) -> int:
        return self.size

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "filesystem_id": self.filesystem_id,
            "device": self.device,
            "inode": self.inode,
            "size_bytes": self.size,
            "birth_ns": self.birth_ns,
            "ctime_ns": self.ctime_ns,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NormalRootFile":
        if not isinstance(value, Mapping):
            raise NormalRootCaptureError("file metadata must be an object")
        relative = _safe_relative_path(value.get("relative_path"))
        device = value.get("device")
        inode = value.get("inode")
        if device is None or inode is None:
            raw_identity = value.get("filesystem_id")
            if not isinstance(raw_identity, str) or raw_identity.count(":") != 1:
                raise NormalRootCaptureError("file metadata has no filesystem identity")
            raw_device, raw_inode = raw_identity.split(":")
            try:
                device, inode = int(raw_device), int(raw_inode)
            except ValueError as exc:
                raise NormalRootCaptureError("file metadata has an invalid filesystem identity") from exc
        size = value.get("size", value.get("size_bytes"))
        birth = value.get("birth_ns", value.get("birth_time"))
        ctime = value.get("ctime_ns", value.get("ctime"))
        mtime = value.get("mtime_ns", value.get("mtime"))
        try:
            return cls(relative, device, inode, size, birth, ctime, mtime)
        except (TypeError, ValueError) as exc:
            raise NormalRootCaptureError("file metadata has an invalid field") from exc


# Names used by existing calibration code and callers that prefer "entry" or
# "candidate" terminology.
NormalRootEntry = NormalRootFile
FileMetadata = NormalRootFile
CandidateMetadata = NormalRootFile


def _coerce_entry(value: Any, field: str = "inventory entry") -> NormalRootFile:
    if isinstance(value, NormalRootFile):
        return value
    if isinstance(value, Mapping):
        return NormalRootFile.from_mapping(value)
    try:
        relative = getattr(value, "relative_path")
        device = getattr(value, "device", None)
        inode = getattr(value, "inode", None)
        if device is None or inode is None:
            identity = getattr(value, "filesystem_id")
            device, inode = (int(part) for part in identity.split(":", 1))
        size = getattr(value, "size", None)
        if size is None:
            size = getattr(value, "size_bytes")
        birth = getattr(value, "birth_ns", None)
        if birth is None:
            birth = getattr(value, "birth_time", None)
        ctime = getattr(value, "ctime_ns", None)
        if ctime is None:
            ctime = getattr(value, "ctime")
        mtime = getattr(value, "mtime_ns", None)
        if mtime is None:
            mtime = getattr(value, "mtime")
        return NormalRootFile(relative, device, inode, size, birth, ctime, mtime)
    except (AttributeError, TypeError, ValueError) as exc:
        raise NormalRootCaptureError(f"{field} has an invalid metadata shape") from exc


def _normalise_inventory(value: Sequence[Any], field: str) -> tuple[NormalRootFile, ...]:
    try:
        entries = tuple(_coerce_entry(item, field) for item in value)
    except TypeError as exc:
        raise NormalRootCaptureError(f"{field} must be a sequence") from exc
    by_path: dict[str, NormalRootFile] = {}
    for entry in entries:
        if entry.relative_path in by_path:
            raise NormalRootCaptureError(f"{field} contains duplicate path: {entry.relative_path}")
        by_path[entry.relative_path] = entry
    return tuple(sorted(entries, key=lambda item: item.relative_path))


def inventory_normal_root(
    root: Path | str,
    *,
    predicate: Callable[[Path], bool] | None = None,
) -> tuple[NormalRootFile, ...]:
    """Recursively inventory ordinary files using metadata only.

    Every symlink or special file encountered is rejected, even when a
    predicate would have filtered its name.  This prevents an apparently
    harmless filter from hiding a path escape or an undeclared native sidecar.
    """

    root = _normal_root(root)
    found: list[NormalRootFile] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise NormalRootCaptureError(f"cannot inventory normal root: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise NormalRootCaptureError(f"cannot stat normal-root entry: {path}") from exc
            if entry.is_symlink() or stat.S_ISLNK(info.st_mode):
                raise NormalRootCaptureError(f"symlink in normal root: {path.relative_to(root)}")
            if entry.is_dir(follow_symlinks=False):
                visit(path)
                continue
            if not entry.is_file(follow_symlinks=False) or not stat.S_ISREG(info.st_mode):
                raise NormalRootCaptureError(f"special file in normal root: {path.relative_to(root)}")
            if predicate is not None and not predicate(path):
                continue
            found.append(
                NormalRootFile(
                    relative_path=_safe_relative_path(path.relative_to(root).as_posix()),
                    device=info.st_dev,
                    inode=info.st_ino,
                    size=info.st_size,
                    birth_ns=_birth_ns(info),
                    ctime_ns=info.st_ctime_ns,
                    mtime_ns=info.st_mtime_ns,
                )
            )

    visit(root)
    return tuple(sorted(found, key=lambda item: item.relative_path))


# Compatibility aliases make the contract usable from older calibration
# helpers without importing a vendor adapter.
inventory_root = inventory_normal_root
inventory_metadata = inventory_normal_root
normal_root_inventory = inventory_normal_root


def inventory_diff(
    before: Sequence[Any],
    after: Sequence[Any],
    *,
    started_ns: int | None = None,
    attempt_started_ns: int | None = None,
    predicate: Callable[[NormalRootFile], bool] | None = None,
    allow_preexisting_changes: bool = False,
) -> tuple[NormalRootFile, ...]:
    """Return only files proven newly created by a metadata-only comparison.

    By default, a path that existed before must still exist with identical metadata.  A
    pre-existing identity appearing at another path is also rejected, since a
    rename or hard-link cannot prove a newly-created native file.  The explicit
    shared-root mode tolerates changed metadata at the same old path and old
    paths removed by the application's retention policy; those identities
    remain outside the candidate population and are never opened.
    """

    if started_ns is not None and attempt_started_ns is not None and started_ns != attempt_started_ns:
        raise NormalRootCaptureError("started_ns and attempt_started_ns disagree")
    started = started_ns if started_ns is not None else attempt_started_ns
    if started is not None and (type(started) is not int or started < 0):
        raise NormalRootCaptureError("started_ns must be a nonnegative integer")
    if not isinstance(allow_preexisting_changes, bool):
        raise NormalRootCaptureError("allow_preexisting_changes must be boolean")

    old = _normalise_inventory(before, "before inventory")
    new = _normalise_inventory(after, "after inventory")
    old_by_path = {entry.relative_path: entry for entry in old}
    new_by_path = {entry.relative_path: entry for entry in new}
    old_ids = {entry.identity for entry in old}

    removed = sorted(set(old_by_path) - set(new_by_path))
    if removed and not allow_preexisting_changes:
        raise NormalRootCaptureError(
            "pre-existing files were removed during capture: " + ", ".join(removed)
        )

    changed = sorted(
        path for path in old_by_path.keys() & new_by_path.keys()
        if old_by_path[path] != new_by_path[path]
    )
    if changed and not allow_preexisting_changes:
        raise NormalRootCaptureError(
            "pre-existing files changed during capture: " + ", ".join(changed)
        )

    moved = sorted(
        entry.relative_path
        for entry in new
        if entry.relative_path not in old_by_path and entry.identity in old_ids
    )
    if moved:
        raise NormalRootCaptureError(
            "pre-existing file identity moved or was reused: " + ", ".join(moved)
        )

    candidates = [
        entry
        for entry in new
        if entry.relative_path not in old_by_path and entry.identity not in old_ids
    ]
    if started is not None:
        too_old = [
            entry.relative_path
            for entry in candidates
            if entry.birth_ns is not None and entry.birth_ns < started
        ]
        if too_old:
            raise NormalRootCaptureError(
                "files are not proven newly created by this attempt: " + ", ".join(too_old)
            )
    if predicate is not None:
        candidates = [entry for entry in candidates if predicate(entry)]
    return tuple(sorted(candidates, key=lambda item: item.relative_path))


new_inventory_entries = inventory_diff
identify_new_files = inventory_diff
diff_normal_root = inventory_diff


@dataclass(frozen=True, slots=True)
class NewFileFamily:
    """One proven-new primary file and its complete required companion set."""

    primary: NormalRootFile
    companions: tuple[NormalRootFile, ...] = ()

    def __post_init__(self) -> None:
        companions = tuple(self.companions)
        if any(item.relative_path == self.primary.relative_path for item in companions):
            raise ValueError("primary is duplicated in companion family")
        paths = [self.primary.relative_path, *(item.relative_path for item in companions)]
        if len(paths) != len(set(paths)):
            raise ValueError("companion family contains duplicate paths")

    @property
    def entries(self) -> tuple[NormalRootFile, ...]:
        return (self.primary, *self.companions)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.entries)

    @property
    def primary_path(self) -> str:
        return self.primary.relative_path

    @property
    def companion_paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.companions)


CaptureFamily = NewFileFamily
NewArtifactFamily = NewFileFamily


def _selector_matches(selector: Callable[[NormalRootFile], bool], entry: NormalRootFile) -> bool:
    try:
        return bool(selector(entry))
    except (AttributeError, TypeError):
        # A few older helpers use a path predicate.  This compatibility path
        # does not weaken the inventory boundary; it only changes the value
        # supplied to the caller's pure selector.
        return bool(selector(entry.relative_path))  # type: ignore[arg-type]


def _declared_relative_path(value: Any, field: str) -> str:
    if isinstance(value, NormalRootFile):
        value = value.relative_path
    elif isinstance(value, Mapping) and "relative_path" in value:
        value = value["relative_path"]
    elif hasattr(value, "relative_path"):
        value = getattr(value, "relative_path")
    return _safe_relative_path(value, field)


def identify_new_family(
    before: Sequence[Any],
    after: Sequence[Any],
    *,
    primary_path: str | Path | None = None,
    primary_predicate: Callable[[NormalRootFile], bool] | None = None,
    primary_suffix: str | None = None,
    required_companion_paths: Sequence[str | Path] = (),
    required_companions: Sequence[str | Path] | None = None,
    companion_suffixes: Sequence[str] = (),
    companion_predicate: Callable[[NormalRootFile], bool] | None = None,
    started_ns: int | None = None,
    attempt_started_ns: int | None = None,
    allow_preexisting_changes: bool = False,
) -> NewFileFamily:
    """Select one exact new primary plus all declared required companions.

    The complete after-minus-before set must equal the selected family.  An
    extra new file is therefore an error instead of an silently omitted
    artifact.  Explicit companion paths are relative to the inventoried root;
    suffixes are appended to the selected primary path (for example ``-wal``
    and ``-shm``).
    """

    if primary_path is not None and (primary_predicate is not None or primary_suffix is not None):
        raise NormalRootCaptureError("primary_path cannot be combined with another primary selector")
    if required_companions is not None and required_companion_paths:
        raise NormalRootCaptureError("required companion paths were supplied twice")
    required = tuple(required_companions if required_companions is not None else required_companion_paths)
    required_paths = tuple(_declared_relative_path(path, "required companion path") for path in required)
    if len(set(required_paths)) != len(required_paths):
        raise NormalRootCaptureError("required companion paths contain duplicates")
    suffixes = tuple(companion_suffixes)
    if any(not isinstance(suffix, str) or not suffix or "/" in suffix or "\\" in suffix for suffix in suffixes):
        raise NormalRootCaptureError("companion suffixes must be non-empty filename suffixes")
    if len(set(suffixes)) != len(suffixes):
        raise NormalRootCaptureError("companion suffixes contain duplicates")

    new_entries = inventory_diff(
        before,
        after,
        started_ns=started_ns,
        attempt_started_ns=attempt_started_ns,
        allow_preexisting_changes=allow_preexisting_changes,
    )
    by_path = {entry.relative_path: entry for entry in new_entries}

    if primary_path is not None:
        primary_name = _declared_relative_path(primary_path, "primary path")
        primary_candidates = [by_path[primary_name]] if primary_name in by_path else []
    elif primary_predicate is not None:
        primary_candidates = [entry for entry in new_entries if _selector_matches(primary_predicate, entry)]
    elif primary_suffix is not None:
        if not isinstance(primary_suffix, str) or not primary_suffix:
            raise NormalRootCaptureError("primary_suffix must be non-empty text")
        primary_candidates = [entry for entry in new_entries if entry.relative_path.endswith(primary_suffix)]
    else:
        excluded = set(required_paths)
        primary_candidates = [entry for entry in new_entries if entry.relative_path not in excluded]

    if len(primary_candidates) != 1:
        if not primary_candidates:
            raise NormalRootCaptureError("no single newly-created primary candidate was proven")
        raise NormalRootCaptureError(
            "ambiguous newly-created primary candidates: "
            + ", ".join(entry.relative_path for entry in primary_candidates)
        )
    primary = primary_candidates[0]

    expected_paths = set(required_paths)
    for suffix in suffixes:
        expected_paths.add(primary.relative_path + suffix)
    if primary.relative_path in expected_paths:
        raise NormalRootCaptureError("a required companion path equals the primary path")

    companion_entries: dict[str, NormalRootFile] = {}
    companion_order: list[str] = []
    for path in (*required_paths, *(primary.relative_path + suffix for suffix in suffixes)):
        if path in companion_entries:
            raise NormalRootCaptureError(f"required companion paths contain duplicates: {path}")
        if path not in by_path:
            raise NormalRootCaptureError(f"required companion was not proven newly created: {path}")
        companion_entries[path] = by_path[path]
        companion_order.append(path)

    if companion_predicate is not None:
        for entry in sorted(new_entries, key=lambda item: item.relative_path):
            if entry.relative_path == primary.relative_path:
                continue
            if _selector_matches(companion_predicate, entry):
                if entry.relative_path in companion_entries:
                    continue
                companion_entries[entry.relative_path] = entry
                companion_order.append(entry.relative_path)

    expected_family = {primary.relative_path, *companion_entries}
    unexpected = sorted(path for path in by_path if path not in expected_family)
    if unexpected:
        raise NormalRootCaptureError(
            "unexpected newly-created files are outside the exact family: " + ", ".join(unexpected)
        )
    return NewFileFamily(primary, tuple(companion_entries[path] for path in companion_order))


select_new_family = identify_new_family
identify_new_file_family = identify_new_family


@dataclass(frozen=True, slots=True)
class QuiescenceReceipt:
    checks: int
    interval_seconds: float
    stable: bool
    observed: tuple[tuple[NormalRootFile, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": self.checks,
            "interval_seconds": self.interval_seconds,
            "stable": self.stable,
            "observed": [[entry.to_dict() for entry in snapshot] for snapshot in self.observed],
        }


def wait_for_family_quiescence(
    root: Path | str,
    family: NewFileFamily,
    *,
    before: Sequence[Any] | None = None,
    started_ns: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    interval_seconds: float = 0.0,
    checks: int = 2,
    allow_preexisting_changes: bool = False,
) -> QuiescenceReceipt:
    """Require unchanged metadata and no extra new files before opening bytes."""

    if type(checks) is not int or checks < 2 or interval_seconds < 0:
        raise NormalRootCaptureError("quiescence requires at least two nonnegative checks")
    if not isinstance(family, NewFileFamily):
        raise NormalRootCaptureError("quiescence requires a proven new file family")
    root = _normal_root(root)
    expected = {entry.relative_path: entry for entry in family.entries}
    before_entries = None if before is None else _normalise_inventory(before, "before inventory")
    observed_snapshots: list[tuple[NormalRootFile, ...]] = []
    for _ in range(checks):
        sleep(interval_seconds)
        snapshot = inventory_normal_root(root)
        if before_entries is not None:
            delta = inventory_diff(
                before_entries,
                snapshot,
                started_ns=started_ns,
                allow_preexisting_changes=allow_preexisting_changes,
            )
            if {entry.relative_path for entry in delta} != set(expected):
                raise NormalRootCaptureError("new native file family changed during quiescence")
        selected = {entry.relative_path: entry for entry in snapshot if entry.relative_path in expected}
        if set(selected) != set(expected) or any(selected[path] != expected[path] for path in expected):
            raise NormalRootCaptureError("selected native file family changed during quiescence")
        observed_snapshots.append(tuple(selected[path] for path in family.paths))
    return QuiescenceReceipt(checks, float(interval_seconds), True, tuple(observed_snapshots))


wait_for_quiescence = wait_for_family_quiescence
quiesce_family = wait_for_family_quiescence


def _open_relative_regular(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
    """Open a source regular file through no-follow directory descriptors."""

    relative = _safe_relative_path(relative_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise NormalRootCaptureError(f"cannot open selected native file: {relative}") from exc
    try:
        parts = PurePosixPath(relative).parts
        for component in parts[:-1]:
            child = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(parts[-1], flags, dir_fd=descriptor)
        info = os.fstat(file_descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_descriptor)
            raise NormalRootCaptureError(f"selected native path is not an ordinary file: {relative}")
        return file_descriptor, info
    except OSError as exc:
        raise NormalRootCaptureError(f"cannot open selected native file: {relative}") from exc
    finally:
        os.close(descriptor)


def _metadata_matches(entry: NormalRootFile, info: os.stat_result) -> bool:
    return (
        entry.device,
        entry.inode,
        entry.size,
        entry.birth_ns,
        entry.ctime_ns,
        entry.mtime_ns,
    ) == (
        info.st_dev,
        info.st_ino,
        info.st_size,
        _birth_ns(info),
        info.st_ctime_ns,
        info.st_mtime_ns,
    )


def _ensure_destination_parent(destination_root: Path, relative_path: str) -> Path:
    parent = destination_root
    parts = PurePosixPath(_safe_relative_path(relative_path)).parts[:-1]
    for component in parts:
        parent = parent / component
        try:
            info = parent.lstat()
        except FileNotFoundError:
            parent.mkdir(mode=0o700)
            continue
        except OSError as exc:
            raise NormalRootCaptureError(f"cannot inspect capture parent: {parent}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise NormalRootCaptureError(f"capture path contains a symlink or non-directory: {parent}")
    return destination_root / PurePosixPath(_safe_relative_path(relative_path))


def _destination_root(source_root: Path, destination: Path | str) -> Path:
    target = _absolute_path(destination)
    if target.exists() or target.is_symlink():
        raise NormalRootCaptureError("capture destination already exists; immutable output cannot be overwritten")
    try:
        resolved = target.resolve(strict=False)
        if resolved == source_root or resolved.is_relative_to(source_root) or source_root.is_relative_to(resolved):
            raise NormalRootCaptureError("capture destination overlaps the normal source root")
    except ValueError as exc:
        raise NormalRootCaptureError("capture destination overlaps the normal source root") from exc
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(mode=0o700, exist_ok=False)
    except OSError as exc:
        raise NormalRootCaptureError(f"cannot create capture destination: {target}") from exc
    try:
        info = target.lstat()
    except OSError as exc:
        raise NormalRootCaptureError("capture destination disappeared during creation") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise NormalRootCaptureError("capture destination is not an ordinary directory")
    return target.resolve()


def _copy_one(
    source_root: Path,
    destination_root: Path,
    entry: NormalRootFile,
    *,
    role: str,
    max_file_bytes: int,
) -> dict[str, Any]:
    if max_file_bytes < 0 or entry.size > max_file_bytes:
        raise NormalRootCaptureError(
            f"selected native file exceeds the per-file size limit: {entry.relative_path}"
        )
    descriptor, initial = _open_relative_regular(source_root, entry.relative_path)
    target: Path | None = None
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    target_descriptor: int | None = None
    digest = hashlib.sha256()
    copied = 0
    try:
        target = _ensure_destination_parent(destination_root, entry.relative_path)
        if not _metadata_matches(entry, initial):
            raise NormalRootCaptureError(f"selected native file identity changed before copy: {entry.relative_path}")
        try:
            target_descriptor = os.open(target, target_flags, 0o600)
        except OSError as exc:
            raise NormalRootCaptureError(f"cannot create copied native file: {entry.relative_path}") from exc
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_file_bytes - copied + 1))
            if not chunk:
                break
            copied += len(chunk)
            if copied > max_file_bytes:
                raise NormalRootCaptureError(
                    f"selected native file exceeds the per-file size limit: {entry.relative_path}"
                )
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_descriptor, view)
                if written <= 0:
                    raise NormalRootCaptureError(f"copy made no progress: {entry.relative_path}")
                view = view[written:]
        os.fsync(target_descriptor)
        final = os.fstat(descriptor)
        if copied != entry.size or not _metadata_matches(entry, final):
            raise NormalRootCaptureError(f"selected native file changed during copy: {entry.relative_path}")
        copied_info = os.fstat(target_descriptor)
        if copied_info.st_size != entry.size:
            raise NormalRootCaptureError(f"copied native file has the wrong size: {entry.relative_path}")
        return {
            "relative_path": entry.relative_path,
            "role": role,
            "size_bytes": copied,
            "sha256": digest.hexdigest(),
            "filesystem_id": entry.filesystem_id,
        }
    except Exception:
        if target is not None:
            target.unlink(missing_ok=True)
        raise
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        os.close(descriptor)


def copy_verified_family(
    source_root: Path | str,
    destination_root: Path | str,
    family: NewFileFamily,
    *,
    max_files: int = 256,
    max_file_bytes: int = 16 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
    roles: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Copy exactly a proven family and return hashes for the copied files."""

    if type(max_files) is not int or max_files < 1:
        raise NormalRootCaptureError("max_files must be positive")
    if type(max_file_bytes) is not int or max_file_bytes < 0:
        raise NormalRootCaptureError("max_file_bytes must be nonnegative")
    if type(max_total_bytes) is not int or max_total_bytes < 0:
        raise NormalRootCaptureError("max_total_bytes must be nonnegative")
    if not isinstance(family, NewFileFamily):
        raise NormalRootCaptureError("copy requires a proven new file family")
    if len(family.entries) > max_files:
        raise NormalRootCaptureError("capture exceeds the file-count limit")
    source = _normal_root(source_root)
    destination = _destination_root(source, destination_root)
    role_map = dict(roles or {})
    artifacts: list[dict[str, Any]] = []
    total = 0
    try:
        for index, entry in enumerate(family.entries):
            role = role_map.get(
                entry.relative_path,
                "native-primary" if index == 0 else "native-companion",
            )
            if not isinstance(role, str) or not role:
                raise NormalRootCaptureError(f"invalid role for copied native file: {entry.relative_path}")
            artifact = _copy_one(
                source,
                destination,
                entry,
                role=role,
                max_file_bytes=max_file_bytes,
            )
            total += artifact["size_bytes"]
            if total > max_total_bytes:
                raise NormalRootCaptureError("capture exceeds the total byte limit")
            artifacts.append(artifact)
    except Exception:
        # The destination was created by this call and is otherwise unused;
        # remove a partial output so it cannot be mistaken for a valid family.
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return tuple(artifacts)


copy_new_family = copy_verified_family
copy_normal_root_family = copy_verified_family


@dataclass(frozen=True, slots=True)
class NormalRootCaptureReceipt:
    schema_version: str
    source_root: str
    destination_root: str
    before: tuple[NormalRootFile, ...]
    after: tuple[NormalRootFile, ...]
    family: NewFileFamily
    quiescence: QuiescenceReceipt
    artifacts: tuple[dict[str, Any], ...]

    @property
    def primary_path(self) -> str:
        return self.family.primary_path

    @property
    def companion_paths(self) -> tuple[str, ...]:
        return self.family.companion_paths

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_root": self.source_root,
            "destination_root": self.destination_root,
            "before_inventory": [entry.to_dict() for entry in self.before],
            "after_inventory": [entry.to_dict() for entry in self.after],
            "primary_path": self.primary_path,
            "companion_paths": list(self.companion_paths),
            "quiescence": self.quiescence.to_dict(),
            "artifacts": [dict(artifact) for artifact in self.artifacts],
        }


def capture_new_family(
    source_root: Path | str,
    destination_root: Path | str,
    before: Sequence[Any],
    after: Sequence[Any],
    *,
    primary_path: str | Path | None = None,
    primary_predicate: Callable[[NormalRootFile], bool] | None = None,
    primary_suffix: str | None = None,
    required_companion_paths: Sequence[str | Path] = (),
    required_companions: Sequence[str | Path] | None = None,
    companion_suffixes: Sequence[str] = (),
    companion_predicate: Callable[[NormalRootFile], bool] | None = None,
    started_ns: int | None = None,
    attempt_started_ns: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    quiescence_interval_seconds: float = 0.0,
    quiescence_checks: int = 2,
    max_files: int = 256,
    max_file_bytes: int = 16 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
    roles: Mapping[str, str] | None = None,
) -> NormalRootCaptureReceipt:
    """Validate a before/after pair, quiesce, and copy one exact new family."""

    source = _normal_root(source_root)
    before_entries = _normalise_inventory(before, "before inventory")
    after_entries = _normalise_inventory(after, "after inventory")
    family = identify_new_family(
        before_entries,
        after_entries,
        primary_path=primary_path,
        primary_predicate=primary_predicate,
        primary_suffix=primary_suffix,
        required_companion_paths=required_companion_paths,
        required_companions=required_companions,
        companion_suffixes=companion_suffixes,
        companion_predicate=companion_predicate,
        started_ns=started_ns,
        attempt_started_ns=attempt_started_ns,
    )
    quiescence = wait_for_family_quiescence(
        source,
        family,
        before=before_entries,
        started_ns=started_ns if started_ns is not None else attempt_started_ns,
        sleep=sleep,
        interval_seconds=quiescence_interval_seconds,
        checks=quiescence_checks,
    )
    artifacts = copy_verified_family(
        source,
        destination_root,
        family,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        roles=roles,
    )
    destination = _absolute_path(destination_root).resolve()
    return NormalRootCaptureReceipt(
        schema_version="1.0-normal-root-capture",
        source_root=str(source),
        destination_root=str(destination),
        before=before_entries,
        after=after_entries,
        family=family,
        quiescence=quiescence,
        artifacts=artifacts,
    )


capture_normal_root = capture_new_family
capture_before_after = capture_new_family


class NormalRootCaptureContract:
    """Stateful convenience wrapper for a calibration's before/after barrier."""

    def __init__(
        self,
        source_root: Path | str,
        destination_root: Path | str,
        *,
        primary_path: str | Path | None = None,
        primary_predicate: Callable[[NormalRootFile], bool] | None = None,
        primary_suffix: str | None = None,
        required_companion_paths: Sequence[str | Path] = (),
        required_companions: Sequence[str | Path] | None = None,
        companion_suffixes: Sequence[str] = (),
        companion_predicate: Callable[[NormalRootFile], bool] | None = None,
        started_ns: int | None = None,
        **copy_options: Any,
    ) -> None:
        self.source_root = _absolute_path(source_root)
        self.destination_root = _absolute_path(destination_root)
        self.primary_path = primary_path
        self.primary_predicate = primary_predicate
        self.primary_suffix = primary_suffix
        self.required_companion_paths = tuple(required_companion_paths)
        self.required_companions = required_companions
        self.companion_suffixes = tuple(companion_suffixes)
        self.companion_predicate = companion_predicate
        self.started_ns = started_ns
        self.copy_options = dict(copy_options)
        self._before: tuple[NormalRootFile, ...] | None = None

    @property
    def before_inventory(self) -> tuple[NormalRootFile, ...] | None:
        return self._before

    def record_before(self) -> tuple[NormalRootFile, ...]:
        if self._before is not None:
            raise NormalRootCaptureError("before inventory was already recorded")
        self._before = inventory_normal_root(self.source_root)
        return self._before

    inventory_before = record_before
    before = record_before

    def capture_after(
        self,
        after: Sequence[Any] | None = None,
        *,
        started_ns: int | None = None,
        **overrides: Any,
    ) -> NormalRootCaptureReceipt:
        if self._before is None:
            raise NormalRootCaptureError("record_before must run before capture_after")
        snapshot = inventory_normal_root(self.source_root) if after is None else tuple(after)
        options = {
            **self.copy_options,
            **overrides,
        }
        return capture_new_family(
            self.source_root,
            self.destination_root,
            self._before,
            snapshot,
            primary_path=self.primary_path,
            primary_predicate=self.primary_predicate,
            primary_suffix=self.primary_suffix,
            required_companion_paths=self.required_companion_paths,
            required_companions=self.required_companions,
            companion_suffixes=self.companion_suffixes,
            companion_predicate=self.companion_predicate,
            started_ns=self.started_ns if started_ns is None else started_ns,
            **options,
        )

    capture = capture_after
    record_after = capture_after


NormalRootCapture = NormalRootCaptureContract


__all__ = [
    "CaptureError",
    "CandidateMetadata",
    "CaptureFamily",
    "FileMetadata",
    "NewArtifactFamily",
    "NewFileFamily",
    "NormalRootCapture",
    "NormalRootCaptureContract",
    "NormalRootCaptureError",
    "NormalRootCaptureReceipt",
    "NormalRootEntry",
    "NormalRootFile",
    "QuiescenceReceipt",
    "capture_before_after",
    "capture_new_family",
    "capture_normal_root",
    "copy_new_family",
    "copy_normal_root_family",
    "copy_verified_family",
    "diff_normal_root",
    "identify_new_file_family",
    "identify_new_family",
    "identify_new_files",
    "inventory_diff",
    "inventory_metadata",
    "inventory_normal_root",
    "inventory_root",
    "new_inventory_entries",
    "normal_root_inventory",
    "quiesce_family",
    "select_new_family",
    "wait_for_family_quiescence",
    "wait_for_quiescence",
]
