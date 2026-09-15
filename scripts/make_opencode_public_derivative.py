#!/usr/bin/env python3
"""Create a redacted semantic hand-off derivative of one correction package.

The raw SQLite/WAL/SHM family and the replay runtime remain private.  This
command emits transformed JSON evidence for inspection only; it is explicitly
not a raw replay package or an independent-reproduction receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.native_sanitize import FORBIDDEN_PUBLIC_MARKERS, sanitize_json_document  # noqa: E402


PUBLIC_FILES = (
    "observer.json",
    "decoded.json",
    "portability-receipt.json",
    "measurement.json",
    "evidence.json",
    "summary.json",
    "correction-receipt.json",
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def build(package: Path, output: Path) -> Path:
    package = package.resolve(strict=True)
    output = output.resolve(strict=False)
    if package.is_symlink() or not package.is_dir():
        raise ValueError("package must be an ordinary directory")
    if output.exists() or output.is_symlink():
        raise ValueError("public derivative output already exists")
    package_manifest = _read(package / "package-manifest.json")
    correction = package_manifest.get("correction")
    if not isinstance(correction, dict) or correction.get("reason") != "missing_decoder_runtime":
        raise ValueError("package is not a bound OpenCode decoder-runtime correction")
    run_root = package.parent
    private_project = str(run_root / "project")
    replacements = {private_project: "$SYNTHETIC_PROJECT", "/Users/alexm": "$BENCH_HOME"}
    output.mkdir(parents=True, exist_ok=False)
    receipts: dict[str, dict] = {}
    for name in PUBLIC_FILES:
        source = package / name
        if not source.is_file():
            raise ValueError(f"package lacks required public derivative input: {name}")
        receipt = sanitize_json_document(source, output / name, replacements=replacements)
        # A public receipt may bind the transformation result but must not
        # repeat the withheld absolute source path in its rule text.
        receipt["rules"] = ["synthetic_project_path", "home_path"]
        receipts[name] = receipt
    raw_native = correction.get("supersedes", {}).get("native_files")
    if not isinstance(raw_native, list):
        raise ValueError("correction has no raw-native hash binding")
    public_manifest = {
        "schema_version": "session-bench-opencode-public-derivative-v1",
        "kind": "redacted_semantic_derivative",
        "raw_publication_state": "withheld",
        "replayable_as_raw_package": False,
        "independent_reproduction": False,
        "source_package_id": package_manifest.get("package_id"),
        "source_package_digest": package_manifest.get("package_digest"),
        "source_result_id": package_manifest.get("result_id"),
        "correction_reason": correction.get("reason"),
        "withheld_native_artifacts": raw_native,
        "withheld_runtime_manifest_sha256": _sha(package / "replay-runtime/manifest.json"),
        "files": [
            {"path": name, "sha256": _sha(output / name), "source_raw_sha256": receipts[name]["raw_sha256"]}
            for name in PUBLIC_FILES
        ],
    }
    (output / "public-manifest.json").write_bytes(_canonical(public_manifest))
    redaction_receipt = {
        "schema_version": "session-bench-public-redaction-receipt-v1",
        "scope": "OpenCode correction semantic derivative",
        "raw_sqlite_withheld": True,
        "raw_runtime_withheld": True,
        "source_package_digest": package_manifest.get("package_digest"),
        "files": receipts,
        "public_manifest_sha256": _sha(output / "public-manifest.json"),
    }
    (output / "redaction-receipt.json").write_bytes(_canonical(redaction_receipt))
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in output.rglob("*.json"))
    findings = [marker for marker in FORBIDDEN_PUBLIC_MARKERS if marker in rendered]
    if _EMAIL.search(rendered):
        findings.append("email-address")
    if findings:
        raise ValueError(f"public derivative retains forbidden data: {findings}")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(build(args.package, args.output))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"public derivative refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
