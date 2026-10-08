"""Build private, closed native-to-decoded replay packages.

This is a decoder reproduction primitive, not a score or proof of complete
vendor-root acquisition. It only accepts an explicitly supplied copied native
bundle. It never discovers or reads a vendor home and never marks a package
independently reproduced or public-safe.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA = "session-bench-native-replay-v1"
DECODERS = {
    "codex-cli": "codex_cli_decoder",
    "codex-desktop": "codex_cli_decoder",
    "claude-cli": "claude_code_decoder",
    "claude-desktop": "claude_code_decoder",
}
COMMON_FILES = ("session_bench/survival_metrics.py", "scripts/replay_native_package.py")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _open_directory(root: Path) -> int:
    """Open every ancestor without following links, then retain the root fd."""
    absolute = root.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as error:
        os.close(fd)
        raise ValueError("input and ancestors must be ordinary directories without symlinks") from error


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    """Read regular files through directory fds, rejecting links and changed reads."""
    result: dict[str, bytes] = {}

    def walk(fd: int, prefix: str) -> None:
        names = sorted(os.listdir(fd))
        for name in names:
            relative = prefix + name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(file_fd, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                        raise ValueError("input file changed before snapshot")
                    result[relative] = stream.read()
                    after = os.fstat(stream.fileno())
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns
                    ):
                        raise ValueError("input file changed during snapshot")
            else:
                raise ValueError("symlinks and special files are forbidden in replay packages")
        if names != sorted(os.listdir(fd)):
            raise ValueError("input file population changed during snapshot")

    fd = _open_directory(root)
    try:
        walk(fd, "")
    except OSError as error:
        raise ValueError("input file changed or is not an ordinary replay file") from error
    finally:
        os.close(fd)
    return result


def _read_runtime_source(source: Path) -> bytes:
    fd = _open_directory(source.parent)
    try:
        file_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("runtime source must be an ordinary file")
            data = stream.read()
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ValueError("runtime source changed during packaging")
            return data
    except OSError as error:
        raise ValueError("runtime source is missing, changed, or symlinked") from error
    finally:
        os.close(fd)


def _manifest(contents: dict[str, bytes]) -> dict[str, Any]:
    """Trusted host-side inventory validation BEFORE any package code executes."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate manifest JSON key")
            result[key] = value
        return result

    try:
        manifest = json.loads(contents["manifest.json"], object_pairs_hook=pairs)
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("missing or malformed replay manifest") from error
    fields = {"schema_version", "configuration_id", "repetition", "files", "expected_decode_sha256",
              "scope", "public_safe", "independent_reproduction"}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest["schema_version"] != SCHEMA:
        raise ValueError("unsupported replay manifest")
    config, repetition = manifest["configuration_id"], manifest["repetition"]
    if not isinstance(config, str) or config not in DECODERS or type(repetition) is not int or repetition not in (1, 2, 3):
        raise ValueError("invalid replay identity")
    if (manifest["scope"] != "native_decode_only" or manifest["public_safe"] is not False
            or manifest["independent_reproduction"] is not False):
        raise ValueError("native replay manifest overstates verification scope")
    if not isinstance(manifest["expected_decode_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["expected_decode_sha256"]):
        raise ValueError("invalid expected decode digest")
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("missing file inventory")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise ValueError("malformed inventory entry")
        name = entry["path"]
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("invalid relative path")
        relative = PurePosixPath(name)
        if (relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name
                or name in seen or relative.parts[0] not in {"native", "runtime"}):
            raise ValueError("unsafe or duplicate inventory path")
        seen.add(name)
        data = contents.get(name)
        if (data is None or type(entry["size_bytes"]) is not int or len(data) != entry["size_bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]):
            raise ValueError(f"inventory mismatch: {name}")
    if set(contents) != seen | {"manifest.json"}:
        raise ValueError("file inventory is not closed")
    expected_runtime = {
        "runtime/session_bench/__init__.py", "runtime/session_bench/adapters/__init__.py",
        *{f"runtime/{name}" for name in COMMON_FILES},
        f"runtime/session_bench/adapters/{DECODERS[config]}.py",
    }
    if {name for name in seen if name.startswith("runtime/")} != expected_runtime:
        raise ValueError("runtime closure differs from the fixed decoder dependency set")
    if "native/decode.json" not in seen:
        raise ValueError("missing native decode inventory")
    return manifest


def build_native_replay_package(
    native_bundle: Path, destination: Path, *, configuration_id: str,
    repetition: int, source_root: Path | None = None,
) -> dict[str, Any]:
    """Snapshot exact native bytes and a fixed, hash-bound decoder closure.

    Writes only a new destination. Validate and decode the native input before
    copying; retain its decode.json inventory verbatim. Absolute local paths in
    the copied source are private evidence and are never declared public-safe.
    """
    if configuration_id not in DECODERS or type(repetition) is not int or repetition not in (1, 2, 3):
        raise ValueError("unsupported configuration or repetition")
    native_bundle, destination = Path(native_bundle), Path(destination)
    if os.path.lexists(destination):
        raise ValueError("destination must not exist")
    if Path(os.path.abspath(destination)).is_relative_to(Path(os.path.abspath(native_bundle))):
        raise ValueError("destination must be outside the immutable input tree")
    native_bytes = _snapshot_tree(native_bundle)
    # Decode the snapshot rather than reopening live input paths after checking them.
    with tempfile.TemporaryDirectory(prefix="bench-native-snapshot-") as temp:
        snapshot = Path(temp).resolve()
        for name, data in native_bytes.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if configuration_id.startswith("codex-"):
            from .adapters.codex_cli_decoder import decode_codex_cli_bundle
            decoded = decode_codex_cli_bundle(snapshot, configuration_id=configuration_id, repetition=repetition)
        else:
            from .adapters.claude_code_decoder import decode_claude_code_bundle
            decoded = decode_claude_code_bundle(snapshot)
    # This expected digest is a comparison target, never an input to decoding.
    expected_digest = hashlib.sha256(canonical(decoded)).hexdigest()
    source_root = source_root or Path(__file__).resolve().parents[1]
    module = DECODERS[configuration_id]
    sources = (*COMMON_FILES, f"session_bench/adapters/{module}.py")
    runtime: dict[str, bytes] = {
        "session_bench/__init__.py": b'"""Closed replay namespace."""\n',
        "session_bench/adapters/__init__.py": b'"""Closed replay adapters."""\n',
    }
    for relative in sources:
        source = source_root / relative
        if any(part.is_symlink() for part in (source, *source.parents)) or not source.is_file():
            raise ValueError(f"runtime source is missing or symlinked: {relative}")
        runtime[relative] = _read_runtime_source(source)
    # Catch source mutation across validation before publishing the package.
    if native_bytes != _snapshot_tree(native_bundle):
        raise ValueError("native input changed during packaging")
    # Reject existing symlink ancestors before creating any output directory.
    if any(part.is_symlink() for part in (destination, *destination.parents)):
        raise ValueError("destination and ancestors must not be symlinked")
    contents = {f"native/{name}": data for name, data in native_bytes.items()}
    contents.update({f"runtime/{name}": data for name, data in runtime.items()})
    entries = []
    for relative, data in sorted(contents.items()):
        entries.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
    manifest = {
        "schema_version": SCHEMA, "configuration_id": configuration_id, "repetition": repetition,
        "files": entries, "expected_decode_sha256": expected_digest,
        "scope": "native_decode_only", "public_safe": False,
        "independent_reproduction": False,
    }
    _manifest({**contents, "manifest.json": canonical(manifest) + b"\n"})
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in contents.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (destination / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    return manifest


def replay_native_package(package: Path, *, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    """Run the packaged decoder in an isolated Python process from a fresh copy.

    Python isolation prevents repository/site imports. This is not an OS
    network/filesystem sandbox, a full score replay, or independent reproduction.
    Inventory hashes establish integrity relative to the manifest, not source
    authenticity. Callers with a trusted manifest digest should supply it.
    """
    package = Path(package)
    contents = _snapshot_tree(package)
    _manifest(contents)
    if expected_manifest_sha256 is not None and hashlib.sha256(contents["manifest.json"]).hexdigest() != expected_manifest_sha256:
        raise ValueError("replay manifest differs from the trusted expected digest")
    with tempfile.TemporaryDirectory(prefix="bench-native-replay-") as temp:
        copied = Path(temp).resolve() / "package"
        copied.mkdir()
        for name, data in contents.items():
            target = copied / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        command = [sys.executable, "-I", "-B", str(copied / "runtime/scripts/replay_native_package.py"), str(copied)]
        completed = subprocess.run(command, cwd=temp, capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode:
            raise ValueError(f"native replay failed: {completed.stderr.strip()}")
        return json.loads(completed.stdout)
