#!/usr/bin/env python3
"""Verify a closed private package before importing its bundled native decoder."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
import sys

sys.dont_write_bytecode = True


def replay(package: Path) -> dict:
    if any(part.is_symlink() for part in (package, *package.parents)) or not package.is_dir():
        raise ValueError("package must be an ordinary directory")
    package = package.resolve()
    if any(not (stat.S_ISDIR(path.lstat().st_mode) or stat.S_ISREG(path.lstat().st_mode)) for path in package.rglob("*")):
        raise ValueError("package contains a symlink or special file")
    runtime = package / "runtime"
    if Path(__file__).resolve() != runtime / "scripts/replay_native_package.py":
        raise ValueError("runner must execute from the package-local runtime")
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("missing replay manifest")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "session-bench-native-replay-v1":
        raise ValueError("unsupported replay manifest")
    if (manifest.get("scope") != "native_decode_only" or manifest.get("public_safe") is not False
            or manifest.get("independent_reproduction") is not False):
        raise ValueError("native replay manifest overstates verification scope")
    if not isinstance(manifest.get("expected_decode_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["expected_decode_sha256"]):
        raise ValueError("invalid expected decode digest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("missing file inventory")
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size_bytes"}:
            raise ValueError("malformed inventory entry")
        name = item["path"]
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("invalid relative path")
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name or name in seen:
            raise ValueError("unsafe or duplicate inventory path")
        if relative.parts[0] not in {"native", "runtime"}:
            raise ValueError("inventory path outside native/runtime")
        seen.add(name)
        target = package / name
        if not target.is_file():
            raise ValueError("missing inventory file")
        data = target.read_bytes()
        if type(item["size_bytes"]) is not int or len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"inventory mismatch: {name}")
    actual = {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}
    if actual != seen | {"manifest.json"}:
        raise ValueError("file inventory is not closed")
    config, repetition = manifest.get("configuration_id"), manifest.get("repetition")
    if config not in {"codex-cli", "codex-desktop", "claude-cli", "claude-desktop"} or type(repetition) is not int or repetition not in (1, 2, 3):
        raise ValueError("invalid replay identity")
    decoder = "codex_cli_decoder" if config.startswith("codex-") else "claude_code_decoder"
    expected_runtime = {
        "runtime/session_bench/__init__.py", "runtime/session_bench/adapters/__init__.py",
        "runtime/session_bench/survival_metrics.py", "runtime/scripts/replay_native_package.py",
        f"runtime/session_bench/adapters/{decoder}.py",
    }
    if {name for name in seen if name.startswith("runtime/")} != expected_runtime:
        raise ValueError("runtime dependency closure changed")
    sys.path.insert(0, str(runtime))
    if config.startswith("codex-"):
        from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle
        decoded = decode_codex_cli_bundle(package / "native", configuration_id=config, repetition=repetition)
    else:
        from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle
        decoded = decode_claude_code_bundle(package / "native")
    encoded = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if digest != manifest.get("expected_decode_sha256"):
        raise ValueError("native re-decode differs from packaged expected digest")
    return {
        "schema_version": "session-bench-native-replay-receipt-v1",
        "configuration_id": config, "repetition": repetition,
        "package_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "decode_sha256": digest, "scope": "native_decode_only",
        "python_isolated": bool(sys.flags.isolated), "independent_reproduction": False,
        "os_sandboxed": False, "public_safe": False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(replay(Path(sys.argv[1])), sort_keys=True))
    except (ValueError, OSError, KeyError, IndexError, TypeError) as error:
        print(f"replay rejected: {error}", file=sys.stderr)
        raise SystemExit(1)
