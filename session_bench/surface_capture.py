"""Shared, fail-closed capture primitives for survival-v1 surface adapters.

The helpers in this module intentionally do not know how to launch a vendor
client.  An adapter supplies an injected runner and uses these primitives to
record a strict attempt manifest, observe stdout independently, identify newly
created native files from metadata only, and copy a quiescent immutable bundle.

No function in this module discovers a home directory, reads an existing
session store, or copies authentication material.  A caller must provide the
isolated roots explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = "1.0-survival-surface-capture"
ATTEMPT_KIND = "attempt"
RESULT_KIND = "result"

ATTEMPT_STATUSES = frozenset(
    {"planned", "running", "captured", "complete", "invalid", "blocked", "interrupted"}
)
RESULT_STATUSES = frozenset({"captured", "complete", "invalid", "blocked", "unresolved", "failed"})

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INVENTORY_KEYS = frozenset(
    {"relative_path", "filesystem_id", "size_bytes", "birth_ns", "ctime_ns", "mtime_ns"}
)
_ARTIFACT_KEYS = frozenset({"relative_path", "role", "size_bytes", "sha256", "filesystem_id"})
_ROOT_KEYS = frozenset({"run", "project", "native", "observer", "capture"})
_ROOT_KEYS_WITH_MODE = frozenset({"run", "project", "native", "observer", "capture", "native_mode"})
_LIMIT_KEYS = frozenset({"max_files", "max_file_bytes", "max_total_bytes"})
_ALLOWLIST_KEYS = frozenset({"required_argv", "forbidden_argv", "allowed_env", "required_env", "forbidden_env"})


class CaptureError(RuntimeError):
    """A capture cannot be accepted without weakening an evidence boundary."""


class ManifestError(ValueError):
    """A manifest does not satisfy the closed attempt/result contract."""


def canonical_bytes(value: Any) -> bytes:
    """Return the stable JSON representation used for all manifest digests."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty string")
    return value


def _require_hex(value: Any, field: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ManifestError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _safe_relative_path(value: Any, field: str = "relative_path") -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{field} must be a non-empty POSIX relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ManifestError(f"{field} is not a canonical relative path")
    return parsed.as_posix()


def _path_inside(root: Path, child: Path, field: str) -> Path:
    root = Path(root).resolve()
    child = Path(child).resolve()
    try:
        child.relative_to(root)
    except ValueError as exc:
        raise CaptureError(f"{field} escapes the declared isolated root") from exc
    return child


@dataclass(frozen=True)
class IsolatedRoots:
    """Named roots owned by one fresh attempt.

    All roots must live below ``run_root``.  ``run_root`` itself is allowed to
    contain the named roots, while the other roots must not overlap each other.
    This prevents the observer or capture output from becoming native input.
    """

    run_root: Path
    project_root: Path
    native_root: Path
    observer_root: Path
    capture_root: Path
    native_isolated: bool = True

    def validate(self) -> "IsolatedRoots":
        run = Path(self.run_root).resolve()
        names = {
            "project": Path(self.project_root).resolve(),
            "native": Path(self.native_root).resolve(),
            "observer": Path(self.observer_root).resolve(),
            "capture": Path(self.capture_root).resolve(),
        }
        for name, path in names.items():
            if name == "native" and not self.native_isolated:
                # An explicitly authorized existing-account route may use the
                # product's normal CODEX_HOME for metadata-only discovery.
                # It is never copied wholesale; only one proven-new rollout is
                # opened below the adapter's capture procedure.
                if path == run or path in (run / "project", run / "observer", run / "capture"):
                    raise CaptureError("authorized native root overlaps a capture-owned root")
                continue
            try:
                path.relative_to(run)
            except ValueError as exc:
                raise CaptureError(f"{name} root is outside run root") from exc
            if path == run:
                raise CaptureError(f"{name} root cannot be the run root")
        ordered = tuple(names.items())
        for index, (left_name, left) in enumerate(ordered):
            for right_name, right in ordered[index + 1 :]:
                if left == right or left in right.parents or right in left.parents:
                    raise CaptureError(f"isolated roots overlap: {left_name} and {right_name}")
        return self

    def as_dict(self) -> dict[str, str]:
        self.validate()
        return {
            "run": str(Path(self.run_root).resolve()),
            "project": str(Path(self.project_root).resolve()),
            "native": str(Path(self.native_root).resolve()),
            "observer": str(Path(self.observer_root).resolve()),
            "capture": str(Path(self.capture_root).resolve()),
            "native_mode": "isolated" if self.native_isolated else "authorized-existing-account",
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IsolatedRoots":
        if set(value) not in {_ROOT_KEYS, _ROOT_KEYS_WITH_MODE}:
            raise ManifestError("roots must contain run, project, native, observer, and capture, plus an optional native_mode")
        mode = value.get("native_mode", "isolated")
        if mode not in {"isolated", "authorized-existing-account"}:
            raise ManifestError("roots.native_mode is invalid")
        roots = cls(
            *(Path(_require_nonempty_string(value[key], f"roots.{key}")) for key in ("run", "project", "native", "observer", "capture")),
            native_isolated=mode == "isolated",
        )
        try:
            roots.validate()
        except CaptureError as exc:
            raise ManifestError(str(exc)) from exc
        return roots


@dataclass(frozen=True)
class InventoryEntry:
    """A metadata-only file observation.

    Inventory collection never reads file bytes.  Hashes are recorded only in
    copied artifact records after a path has been proven new and quiescent.
    """

    relative_path: str
    filesystem_id: str
    size_bytes: int
    birth_ns: int | None
    ctime_ns: int
    mtime_ns: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "filesystem_id": self.filesystem_id,
            "size_bytes": self.size_bytes,
            "birth_ns": self.birth_ns,
            "ctime_ns": self.ctime_ns,
            "mtime_ns": self.mtime_ns,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InventoryEntry":
        if set(value) != _INVENTORY_KEYS:
            raise ManifestError("inventory entries have an invalid field set")
        relative = _safe_relative_path(value["relative_path"])
        filesystem_id = _require_nonempty_string(value["filesystem_id"], "filesystem_id")
        if type(value["size_bytes"]) is not int or value["size_bytes"] < 0:
            raise ManifestError("inventory size_bytes must be a nonnegative integer")
        for field in ("ctime_ns", "mtime_ns"):
            if type(value[field]) is not int or value[field] < 0:
                raise ManifestError(f"inventory {field} must be a nonnegative integer")
        birth = value["birth_ns"]
        if birth is not None and (type(birth) is not int or birth < 0):
            raise ManifestError("inventory birth_ns must be a nonnegative integer or null")
        return cls(relative, filesystem_id, value["size_bytes"], birth, value["ctime_ns"], value["mtime_ns"])


def _entry_from_stat(root: Path, path: Path, info: os.stat_result) -> InventoryEntry:
    relative = path.relative_to(root).as_posix()
    birth = getattr(info, "st_birthtime", None)
    birth_ns = int(birth * 1_000_000_000) if birth is not None else None
    return InventoryEntry(
        relative_path=_safe_relative_path(relative),
        filesystem_id=f"{info.st_dev}:{info.st_ino}",
        size_bytes=info.st_size,
        birth_ns=birth_ns,
        ctime_ns=info.st_ctime_ns,
        mtime_ns=info.st_mtime_ns,
    )


def inventory_tree(
    root: Path,
    *,
    predicate: Callable[[Path], bool] | None = None,
) -> tuple[InventoryEntry, ...]:
    """Recursively inventory regular files without opening or hashing them."""

    root = Path(root).resolve()
    if not root.exists() or not root.is_dir():
        raise CaptureError(f"inventory root is not an existing directory: {root}")
    found: list[InventoryEntry] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise CaptureError(f"cannot inventory isolated root: {directory}") from exc
        for entry in entries:
            if entry.is_symlink():
                # Symlinks are never native evidence and are not followed.
                continue
            path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                visit(path)
            elif entry.is_file(follow_symlinks=False):
                if predicate is None or predicate(path):
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise CaptureError(f"cannot stat inventory entry: {path}") from exc
                    found.append(_entry_from_stat(root, path, info))

    visit(root)
    return tuple(sorted(found, key=lambda item: item.relative_path))


# Names used by older controller code and by adapters are intentionally kept as
# aliases.  Both are metadata-only and do not hash pre-existing files.
stat_inventory = inventory_tree
before_after_inventory = inventory_tree


def inventory_diff(
    before: Sequence[InventoryEntry],
    after: Sequence[InventoryEntry],
    *,
    started_ns: int | None = None,
    predicate: Callable[[InventoryEntry], bool] | None = None,
) -> tuple[InventoryEntry, ...]:
    """Return entries proven new by path and filesystem identity."""

    old_paths = {entry.relative_path for entry in before}
    old_ids = {entry.filesystem_id for entry in before}
    candidates = []
    for entry in after:
        if entry.relative_path in old_paths or entry.filesystem_id in old_ids:
            continue
        if started_ns is not None and entry.birth_ns is not None and entry.birth_ns < started_ns:
            continue
        if predicate is None or predicate(entry):
            candidates.append(entry)
    return tuple(sorted(candidates, key=lambda item: item.relative_path))


new_inventory_entries = inventory_diff


def _stat_entry(root: Path, relative_path: str) -> InventoryEntry:
    relative_path = _safe_relative_path(relative_path)
    root = Path(root).resolve()
    path = _path_inside(root, root / relative_path, "inventory path")
    try:
        info = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise CaptureError(f"cannot stat selected native file: {relative_path}") from exc
    if path.is_symlink() or not path.is_file():
        raise CaptureError(f"selected native path is not an ordinary file: {relative_path}")
    return _entry_from_stat(root, path, info)


@dataclass(frozen=True)
class QuiescenceReceipt:
    checks: int
    interval_seconds: float
    stable: bool
    observed: tuple[tuple[InventoryEntry, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": self.checks,
            "interval_seconds": self.interval_seconds,
            "stable": self.stable,
            "observed": [[entry.to_dict() for entry in snapshot] for snapshot in self.observed],
        }


def wait_for_quiescence(
    root: Path,
    entries: Sequence[InventoryEntry],
    *,
    sleep: Callable[[float], None] = time.sleep,
    interval_seconds: float = 0.0,
    checks: int = 2,
) -> QuiescenceReceipt:
    """Require repeated unchanged metadata before opening any native file."""

    if checks < 2 or interval_seconds < 0:
        raise CaptureError("quiescence requires at least two nonnegative checks")
    if not entries:
        raise CaptureError("quiescence requires at least one selected native file")
    expected = {entry.relative_path: entry for entry in entries}
    observed: list[tuple[InventoryEntry, ...]] = []
    current = tuple(entries)
    for _ in range(checks):
        sleep(interval_seconds)
        snapshot = tuple(_stat_entry(root, path) for path in sorted(expected))
        if snapshot != current:
            raise CaptureError("selected native files changed during quiescence")
        observed.append(snapshot)
        current = snapshot
    return QuiescenceReceipt(checks, float(interval_seconds), True, tuple(observed))


quiesce = wait_for_quiescence


def _metadata_from_stat(info: os.stat_result) -> tuple[str, int, int | None, int, int]:
    birth = getattr(info, "st_birthtime", None)
    birth_ns = int(birth * 1_000_000_000) if birth is not None else None
    return (f"{info.st_dev}:{info.st_ino}", info.st_size, birth_ns, info.st_ctime_ns, info.st_mtime_ns)


def _same_entry(left: InventoryEntry, right: InventoryEntry) -> bool:
    return left == right


def _read_copy_verified(
    source: Path,
    destination: Path,
    expected: InventoryEntry,
    *,
    max_file_bytes: int,
) -> tuple[str, int]:
    """Copy one selected file through a no-follow descriptor and hash it."""

    if max_file_bytes < 0:
        raise CaptureError("max_file_bytes must be nonnegative")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CaptureError(f"cannot open selected native file: {source}") from exc
    try:
        info = os.fstat(descriptor)
        initial = _metadata_from_stat(info)
        expected_metadata = (
            expected.filesystem_id,
            expected.size_bytes,
            expected.birth_ns,
            expected.ctime_ns,
            expected.mtime_ns,
        )
        if initial != expected_metadata:
            raise CaptureError("selected native file identity changed before copy")
        if expected.size_bytes > max_file_bytes:
            raise CaptureError("selected native file exceeds the per-file size limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        copied = 0
        try:
            with os.fdopen(os.dup(descriptor), "rb") as source_stream, destination.open("xb") as target_stream:
                while True:
                    chunk = source_stream.read(min(1024 * 1024, max_file_bytes - copied + 1))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_file_bytes:
                        raise CaptureError("selected native file exceeds the per-file size limit")
                    digest.update(chunk)
                    target_stream.write(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        after = os.fstat(descriptor)
        final = _metadata_from_stat(after)
        if copied != expected.size_bytes or final != expected_metadata:
            destination.unlink(missing_ok=True)
            raise CaptureError("selected native file changed during copy")
        return digest.hexdigest(), copied
    finally:
        os.close(descriptor)


def copy_verified_artifacts(
    source_root: Path,
    destination_root: Path,
    entries: Sequence[InventoryEntry],
    *,
    max_files: int = 256,
    max_file_bytes: int = 16 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
    roles: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Copy exactly the selected, proven-new files and return hashed artifacts."""

    if max_files < 1 or max_file_bytes < 0 or max_total_bytes < 0:
        raise CaptureError("invalid capture size limits")
    if len(entries) > max_files:
        raise CaptureError("capture exceeds the file-count limit")
    source_root = Path(source_root).resolve()
    destination_root = Path(destination_root).resolve()
    if destination_root == source_root or destination_root.is_relative_to(source_root):
        raise CaptureError("capture output must be outside the native source root")
    if destination_root.exists():
        if not destination_root.is_dir() or any(destination_root.iterdir()):
            raise CaptureError("capture output already exists; immutable capture would be overwritten")
    else:
        destination_root.mkdir(parents=True, exist_ok=False)
    roles = {} if roles is None else dict(roles)
    artifacts: list[dict[str, Any]] = []
    total = 0
    try:
        for entry in sorted(entries, key=lambda item: item.relative_path):
            source = _path_inside(source_root, source_root / entry.relative_path, "native artifact")
            target = _path_inside(destination_root, destination_root / entry.relative_path, "capture artifact")
            digest, size = _read_copy_verified(
                source,
                target,
                entry,
                max_file_bytes=max_file_bytes,
            )
            total += size
            if total > max_total_bytes:
                raise CaptureError("capture exceeds the total byte limit")
            artifacts.append(
                {
                    "relative_path": entry.relative_path,
                    "role": roles.get(entry.relative_path, "native"),
                    "size_bytes": size,
                    "sha256": digest,
                    "filesystem_id": entry.filesystem_id,
                }
            )
    except Exception:
        # Keep the destination directory as an immutable diagnostic boundary;
        # any complete files remain inspectable, and a later attempt cannot
        # silently replace them.
        raise
    return tuple(artifacts)


copy_capture = copy_verified_artifacts


def hash_file(path: Path, *, max_bytes: int = 16 * 1024 * 1024) -> tuple[str, int]:
    """Hash a regular file with no-follow and a hard byte limit."""

    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise CaptureError(f"cannot hash file: {path}") from exc
    try:
        info = os.fstat(descriptor)
        if not os.path.isfile(path) or info.st_size > max_bytes:
            raise CaptureError("file is not a regular file or exceeds the size limit")
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            while chunk := stream.read(min(1024 * 1024, max_bytes - total + 1)):
                total += len(chunk)
                if total > max_bytes:
                    raise CaptureError("file exceeds the size limit")
                digest.update(chunk)
        after = os.fstat(descriptor)
        if after.st_ino != info.st_ino or after.st_dev != info.st_dev or after.st_size != info.st_size or after.st_mtime_ns != info.st_mtime_ns:
            raise CaptureError("file changed while hashing")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


sha256_file = hash_file


@dataclass(frozen=True)
class ArgvEnvAllowlist:
    """The launch subset a surface adapter is permitted to use."""

    required_argv: tuple[str, ...] = ()
    forbidden_argv: tuple[str, ...] = ()
    allowed_env: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    forbidden_env: tuple[str, ...] = ()

    def validate(self, argv: Sequence[str], env: Mapping[str, str]) -> None:
        if any(not isinstance(item, str) or not item for item in argv):
            raise CaptureError("argv entries must be non-empty strings")
        if any(not isinstance(key, str) or not key for key in env):
            raise CaptureError("environment keys must be non-empty strings")
        if any(key not in self.allowed_env for key in env):
            unknown = sorted(set(env) - set(self.allowed_env))
            raise CaptureError(f"environment contains keys outside its allowlist: {unknown}")
        missing = [item for item in self.required_argv if item not in argv]
        if missing:
            raise CaptureError(f"argv is missing required tokens: {missing}")
        forbidden = [item for item in self.forbidden_argv if item in argv]
        if forbidden:
            raise CaptureError(f"argv contains forbidden tokens: {forbidden}")
        missing_env = [item for item in self.required_env if item not in env]
        if missing_env:
            raise CaptureError(f"environment is missing required keys: {missing_env}")
        forbidden_env = [item for item in self.forbidden_env if item in env]
        if forbidden_env:
            raise CaptureError(f"environment contains forbidden keys: {forbidden_env}")

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "required_argv": list(self.required_argv),
            "forbidden_argv": list(self.forbidden_argv),
            "allowed_env": list(self.allowed_env),
            "required_env": list(self.required_env),
            "forbidden_env": list(self.forbidden_env),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ArgvEnvAllowlist":
        if set(value) != _ALLOWLIST_KEYS:
            raise ManifestError("argv/env allowlist has an invalid field set")
        fields: dict[str, tuple[str, ...]] = {}
        for key in _ALLOWLIST_KEYS:
            data = value[key]
            if not isinstance(data, list) or any(not isinstance(item, str) or not item for item in data):
                raise ManifestError(f"allowlist field {key} must be a list of non-empty strings")
            if len(set(data)) != len(data):
                raise ManifestError(f"allowlist field {key} contains duplicates")
            fields[key] = tuple(data)
        return cls(**fields)


class IndependentStdoutObserver:
    """Raw stdout observer that is independent of native session artifacts."""

    def __init__(self, *, max_bytes: int = 16 * 1024 * 1024, method: str = "runner-stdout") -> None:
        if max_bytes < 0:
            raise ValueError("max_bytes must be nonnegative")
        self.max_bytes = max_bytes
        self.method = _require_nonempty_string(method, "observer method")
        self._chunks: list[bytes] = []
        self._events: list[dict[str, Any]] = []
        self._size = 0
        self._frozen = False
        self._receipt: dict[str, Any] | None = None

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def size_bytes(self) -> int:
        return self._size

    @property
    def data(self) -> bytes:
        return b"".join(self._chunks)

    @property
    def events(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._events)

    def observe(self, data: bytes | bytearray | str, *, timestamp_ns: int | None = None) -> None:
        if self._frozen:
            raise CaptureError("stdout observer is frozen")
        if isinstance(data, str):
            chunk = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            chunk = bytes(data)
        else:
            raise TypeError("stdout observation must be bytes or text")
        if self._size + len(chunk) > self.max_bytes:
            raise CaptureError("stdout observer exceeds its byte limit")
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise CaptureError("stdout timestamp must be a nonnegative integer")
        self._chunks.append(chunk)
        self._size += len(chunk)
        self._events.append({"sequence": len(self._events) + 1, "timestamp_ns": timestamp_ns, "size_bytes": len(chunk)})

    record = observe
    observe_stdout = observe

    def freeze(self, output: Path | None = None) -> dict[str, Any]:
        if self._receipt is not None:
            if output is not None and not Path(output).exists():
                raise CaptureError("stdout observer was already frozen without this output path")
            return dict(self._receipt)
        data = self.data
        artifact: dict[str, Any] | None = None
        if output is not None:
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            try:
                with output.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as exc:
                raise CaptureError("stdout observer output already exists") from exc
            artifact = {
                "relative_path": output.name,
                "role": "observer",
                "size_bytes": len(data),
                "sha256": sha256_bytes(data),
                "filesystem_id": f"{output.stat().st_dev}:{output.stat().st_ino}",
            }
        self._frozen = True
        self._receipt = {
            "method": self.method,
            "independent": True,
            "frozen": True,
            "event_count": len(self._events),
            "size_bytes": len(data),
            "sha256": sha256_bytes(data),
            "artifact": artifact,
        }
        return dict(self._receipt)

    def manifest_record(self) -> dict[str, Any]:
        if not self._frozen:
            raise CaptureError("stdout observer must be frozen before it is bound to a manifest")
        assert self._receipt is not None
        return dict(self._receipt)


StdoutObserver = IndependentStdoutObserver


def _validate_inventory_list(value: Any, field: str) -> tuple[InventoryEntry, ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{field} must be an array")
    entries = tuple(InventoryEntry.from_dict(item) for item in value if isinstance(item, Mapping))
    if len(entries) != len(value):
        raise ManifestError(f"{field} contains a non-object entry")
    if len({entry.relative_path for entry in entries}) != len(entries):
        raise ManifestError(f"{field} contains duplicate paths")
    return entries


def _validate_artifacts(value: Any, field: str = "artifacts") -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise ManifestError(f"{field} must be an array")
    artifacts: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _ARTIFACT_KEYS:
            raise ManifestError(f"{field} contains an invalid artifact")
        path = _safe_relative_path(item["relative_path"], f"{field}.relative_path")
        if path in paths:
            raise ManifestError(f"{field} contains duplicate paths")
        paths.add(path)
        role = _require_nonempty_string(item["role"], f"{field}.role")
        if type(item["size_bytes"]) is not int or item["size_bytes"] < 0:
            raise ManifestError(f"{field}.size_bytes must be a nonnegative integer")
        digest = _require_hex(item["sha256"], f"{field}.sha256")
        filesystem_id = _require_nonempty_string(item["filesystem_id"], f"{field}.filesystem_id")
        artifacts.append({
            "relative_path": path,
            "role": role,
            "size_bytes": item["size_bytes"],
            "sha256": digest,
            "filesystem_id": filesystem_id,
        })
    return tuple(artifacts)


def _validate_roots(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ManifestError("roots must be an object")
    roots = IsolatedRoots.from_mapping(value)
    return roots.as_dict()


def _validate_limits(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != _LIMIT_KEYS:
        raise ManifestError("limits must contain exactly max_files, max_file_bytes, and max_total_bytes")
    result: dict[str, int] = {}
    for key in _LIMIT_KEYS:
        if type(value[key]) is not int or value[key] < 0:
            raise ManifestError(f"limits.{key} must be a nonnegative integer")
        result[key] = value[key]
    if result["max_files"] < 1:
        raise ManifestError("limits.max_files must be positive")
    return result


def _validate_observer(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError("observer must be an object")
    expected = {"method", "independent", "frozen", "event_count", "size_bytes", "sha256", "artifact"}
    if set(value) != expected:
        raise ManifestError("observer has an invalid field set")
    method = _require_nonempty_string(value["method"], "observer.method")
    if value["independent"] is not True or value["frozen"] is not True:
        raise ManifestError("accepted manifests require an independent frozen observer")
    if type(value["event_count"]) is not int or value["event_count"] < 0:
        raise ManifestError("observer.event_count must be a nonnegative integer")
    if type(value["size_bytes"]) is not int or value["size_bytes"] < 0:
        raise ManifestError("observer.size_bytes must be a nonnegative integer")
    digest = _require_hex(value["sha256"], "observer.sha256")
    artifact = value["artifact"]
    if artifact is not None:
        _validate_artifacts([artifact], "observer.artifact")
    return {"method": method, "independent": True, "frozen": True, "event_count": value["event_count"], "size_bytes": value["size_bytes"], "sha256": digest, "artifact": artifact}


def _validate_quiescence(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError("quiescence must be an object")
    expected = {"checks", "interval_seconds", "stable", "observed"}
    if set(value) != expected:
        raise ManifestError("quiescence has an invalid field set")
    if type(value["checks"]) is not int or value["checks"] < 0:
        raise ManifestError("quiescence.checks must be a nonnegative integer")
    if type(value["interval_seconds"]) not in (int, float) or value["interval_seconds"] < 0:
        raise ManifestError("quiescence.interval_seconds must be nonnegative")
    if type(value["stable"]) is not bool or not isinstance(value["observed"], list) or len(value["observed"]) != value["checks"]:
        raise ManifestError("quiescence must retain every observation")
    observations: list[list[dict[str, Any]]] = []
    for snapshot in value["observed"]:
        entries = _validate_inventory_list(snapshot, "quiescence.observed")
        observations.append([entry.to_dict() for entry in entries])
    return {"checks": value["checks"], "interval_seconds": float(value["interval_seconds"]), "stable": value["stable"], "observed": observations}


_ATTEMPT_KEYS = frozenset({
    "schema_version", "kind", "attempt_id", "result_id", "surface_id", "config_identity", "config_sha256",
    "argv", "env", "argv_env_allowlist", "roots", "before_inventory", "after_inventory", "quiescence",
    "observer", "artifacts", "limits", "status", "reason", "started_ns", "ended_ns",
})


def _validate_identity(value: Any, digest: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping) or not value:
        raise ManifestError("config_identity must be a non-empty object")
    identity = dict(value)
    for key in identity:
        if not isinstance(key, str) or not key:
            raise ManifestError("config_identity keys must be non-empty strings")
    expected = sha256_json(identity)
    if digest != expected:
        raise ManifestError("config_sha256 does not match exact config_identity")
    return identity, expected


def validate_attempt_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a normalized attempt manifest.

    The validator is deliberately closed: an adapter cannot smuggle an
    unreviewed field into the evidence contract or silently omit an identity,
    isolation, observer, inventory, or outcome field.
    """

    if not isinstance(value, Mapping) or set(value) != _ATTEMPT_KEYS:
        raise ManifestError("attempt manifest has missing or unknown fields")
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION or value["kind"] != ATTEMPT_KIND:
        raise ManifestError("attempt manifest schema or kind is invalid")
    attempt_id = _require_nonempty_string(value["attempt_id"], "attempt_id")
    result_id = _require_nonempty_string(value["result_id"], "result_id")
    surface_id = _require_nonempty_string(value["surface_id"], "surface_id")
    identity, config_sha = _validate_identity(value["config_identity"], value["config_sha256"])
    argv = value["argv"]
    env = value["env"]
    if not isinstance(argv, list) or any(not isinstance(item, str) or not item for item in argv):
        raise ManifestError("argv must be a non-empty string array")
    if not isinstance(env, Mapping) or any(not isinstance(key, str) or not key or not isinstance(item, str) for key, item in env.items()):
        raise ManifestError("env must be a string map")
    allowlist = ArgvEnvAllowlist.from_dict(value["argv_env_allowlist"])
    try:
        allowlist.validate(argv, env)
    except CaptureError as exc:
        raise ManifestError(str(exc)) from exc
    roots = _validate_roots(value["roots"])
    before = _validate_inventory_list(value["before_inventory"], "before_inventory")
    after = _validate_inventory_list(value["after_inventory"], "after_inventory")
    quiescence = _validate_quiescence(value["quiescence"])
    observer = _validate_observer(value["observer"])
    artifacts = _validate_artifacts(value["artifacts"])
    limits = _validate_limits(value["limits"])
    status = value["status"]
    if status not in ATTEMPT_STATUSES:
        raise ManifestError("attempt status is invalid")
    reason = _require_nonempty_string(value["reason"], "reason")
    for field in ("started_ns",):
        if type(value[field]) is not int or value[field] < 0:
            raise ManifestError(f"{field} must be a nonnegative integer")
    ended = value["ended_ns"]
    if ended is not None and (type(ended) is not int or ended < value["started_ns"]):
        raise ManifestError("ended_ns must be null or a timestamp after started_ns")
    if status in {"captured", "complete"}:
        if not artifacts or not quiescence["stable"] or not observer["frozen"]:
            raise ManifestError("captured attempts require artifacts, quiescence, and a frozen observer")
    if status == "planned" and (before or after or artifacts):
        raise ManifestError("planned attempts cannot claim inventory or captured artifacts")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": ATTEMPT_KIND,
        "attempt_id": attempt_id,
        "result_id": result_id,
        "surface_id": surface_id,
        "config_identity": identity,
        "config_sha256": config_sha,
        "argv": list(argv),
        "env": dict(env),
        "argv_env_allowlist": allowlist.to_dict(),
        "roots": roots,
        "before_inventory": [entry.to_dict() for entry in before],
        "after_inventory": [entry.to_dict() for entry in after],
        "quiescence": quiescence,
        "observer": observer,
        "artifacts": list(artifacts),
        "limits": limits,
        "status": status,
        "reason": reason,
        "started_ns": value["started_ns"],
        "ended_ns": ended,
    }


@dataclass(frozen=True)
class AttemptManifest:
    """Immutable in-memory representation of one acquisition attempt."""

    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        normalized = validate_attempt_manifest(self.value)
        object.__setattr__(self, "value", normalized)

    @property
    def attempt_id(self) -> str:
        return self.value["attempt_id"]

    @property
    def result_id(self) -> str:
        return self.value["result_id"]

    @property
    def config_sha256(self) -> str:
        return self.value["config_sha256"]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.value, ensure_ascii=False))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AttemptManifest":
        return cls(value)


_RESULT_KEYS = frozenset({
    "schema_version", "kind", "result_id", "attempt_id", "surface_id", "config_identity", "config_sha256",
    "status", "reason", "observer", "artifacts", "attempt_sha256",
})


def validate_result_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RESULT_KEYS:
        raise ManifestError("result manifest has missing or unknown fields")
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION or value["kind"] != RESULT_KIND:
        raise ManifestError("result manifest schema or kind is invalid")
    result_id = _require_nonempty_string(value["result_id"], "result_id")
    attempt_id = _require_nonempty_string(value["attempt_id"], "attempt_id")
    surface_id = _require_nonempty_string(value["surface_id"], "surface_id")
    identity, config_sha = _validate_identity(value["config_identity"], value["config_sha256"])
    status = value["status"]
    if status not in RESULT_STATUSES:
        raise ManifestError("result status is invalid")
    reason = _require_nonempty_string(value["reason"], "reason")
    observer = _validate_observer(value["observer"])
    artifacts = _validate_artifacts(value["artifacts"])
    attempt_sha = _require_hex(value["attempt_sha256"], "attempt_sha256")
    if status in {"captured", "complete"} and not artifacts:
        raise ManifestError("captured result requires at least one artifact")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "result_id": result_id,
        "attempt_id": attempt_id,
        "surface_id": surface_id,
        "config_identity": identity,
        "config_sha256": config_sha,
        "status": status,
        "reason": reason,
        "observer": observer,
        "artifacts": list(artifacts),
        "attempt_sha256": attempt_sha,
    }


@dataclass(frozen=True)
class ResultManifest:
    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", validate_result_manifest(self.value))

    @property
    def result_id(self) -> str:
        return self.value["result_id"]

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.value, ensure_ascii=False))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResultManifest":
        return cls(value)


def build_result_manifest(
    attempt: AttemptManifest | Mapping[str, Any],
    *,
    status: str,
    reason: str,
    observer: Mapping[str, Any] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
) -> ResultManifest:
    """Bind a result to the exact immutable attempt preimage."""

    attempt_value = attempt.to_dict() if isinstance(attempt, AttemptManifest) else validate_attempt_manifest(attempt)
    observer_value = attempt_value["observer"] if observer is None else dict(observer)
    artifact_values = attempt_value["artifacts"] if artifacts is None else [dict(item) for item in artifacts]
    value = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "result_id": attempt_value["result_id"],
        "attempt_id": attempt_value["attempt_id"],
        "surface_id": attempt_value["surface_id"],
        "config_identity": attempt_value["config_identity"],
        "config_sha256": attempt_value["config_sha256"],
        "status": status,
        "reason": reason,
        "observer": observer_value,
        "artifacts": artifact_values,
        "attempt_sha256": sha256_json(attempt_value),
    }
    return ResultManifest(value)


def write_immutable_json(path: Path, value: Mapping[str, Any]) -> str:
    """Create one immutable JSON artifact; never overwrite an earlier record."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value) + b"\n"
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise CaptureError(f"immutable artifact already exists: {path}") from exc
    return sha256_bytes(data)


class ImmutableAttemptLedger:
    """An append-only JSONL ledger that never rewrites retained attempts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        attempts: list[dict[str, Any]] = []
        try:
            lines = self.path.read_bytes().splitlines()
        except OSError as exc:
            raise CaptureError(f"cannot read attempt ledger: {self.path}") from exc
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                raise ManifestError(f"blank line in immutable attempt ledger at {line_number}")
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ManifestError(f"invalid JSON in immutable attempt ledger at {line_number}") from exc
            attempts.append(validate_attempt_manifest(value))
        return tuple(attempts)

    def append(self, attempt: AttemptManifest | Mapping[str, Any]) -> str:
        value = attempt.to_dict() if isinstance(attempt, AttemptManifest) else validate_attempt_manifest(attempt)
        existing = self.read()
        if any(item["attempt_id"] == value["attempt_id"] for item in existing):
            raise CaptureError(f"attempt_id already retained: {value['attempt_id']}")
        if existing and any(item["surface_id"] != value["surface_id"] for item in existing):
            raise CaptureError("one attempt ledger cannot mix surface identities")
        if existing and any(item["config_sha256"] != value["config_sha256"] for item in existing):
            raise CaptureError("one attempt ledger cannot mix effective configuration identities")
        data = canonical_bytes(value) + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("ab") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise CaptureError(f"cannot append immutable attempt ledger: {self.path}") from exc
        return sha256_bytes(data)


AttemptLedger = ImmutableAttemptLedger


__all__ = [
    "ATTEMPT_KIND", "ATTEMPT_STATUSES", "AttemptLedger", "AttemptManifest", "ArgvEnvAllowlist",
    "CaptureError", "IndependentStdoutObserver", "InventoryEntry", "ImmutableAttemptLedger",
    "IsolatedRoots", "MANIFEST_SCHEMA_VERSION", "ManifestError", "RESULT_KIND", "RESULT_STATUSES",
    "ResultManifest", "StdoutObserver", "before_after_inventory", "build_result_manifest", "canonical_bytes",
    "copy_capture", "copy_verified_artifacts", "hash_file", "inventory_diff", "inventory_tree",
    "new_inventory_entries", "quiesce", "sha256_bytes", "sha256_file", "sha256_json", "stat_inventory",
    "validate_attempt_manifest", "validate_result_manifest", "wait_for_quiescence", "write_immutable_json",
]
