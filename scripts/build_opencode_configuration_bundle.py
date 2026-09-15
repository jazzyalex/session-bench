#!/usr/bin/env python3
"""Build and verify the OpenCode CLI v1 public configuration packet.

This command closes the OpenCode lane from the three existing correction
packages.  It deliberately never copies SQLite/WAL/SHM files or the private
replay runtime into the output.  Each source package is copied to a temporary
directory and replayed under a macOS sandbox with the source package, vendor
executable, and network denied.  The resulting packet contains the exact
sanitized score inputs, a canonical three-run binding, and a local source-
denied receipt.  ``independent_reproduction`` remains false because this
process and the separate audit run on the same implementation host.

The packet is a configuration-level evidence artifact.  It must not be used
as a cross-product rank until the complete five-configuration v1 cohort has
qualified.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGES = tuple(
    ROOT / f"artifacts/survival-v1-runs/opencode-cli-eval-{number}/evaluation-correction"
    for number in (1, 2, 3)
)
DEFAULT_OUTPUT = ROOT / "artifacts/opencode-v1-public-configuration"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_live_survival_result import (  # noqa: E402
    _sha,
    _verify_package,
)
from scripts.recheck_opencode_package import _normalized_decode  # noqa: E402
from session_bench.configuration_bundle import (  # noqa: E402
    build_configuration_bundle,
    canonical_sha256,
    run_record_from_public_score,
    validate_configuration_bundle,
)
from session_bench.v1_public_score import (  # noqa: E402
    PUBLIC_CATEGORY_POINTS,
    PUBLIC_METRICS,
    aggregate_public_configuration,
    public_identity_blockers,
    score_public_run,
    validate_format_evidence,
)


MANIFEST_SCHEMA_VERSION = "session-bench-opencode-public-bundle-manifest-v1"
RECEIPT_SCHEMA_VERSION = "session-bench-opencode-source-denied-reproduction-receipt-v1"
BUNDLE_ID = "session-bench-v1-opencode-cli-3-run-public-bundle"
BINDING_RECEIPT_ID = "session-bench-v1-opencode-cli-public-bundle-reproduction-binding"
RECEIPT_ID = "session-bench-v1-opencode-cli-source-denied-reproduction"
PUBLIC_SEMANTIC_FILES = (
    "correction-receipt.json",
    "decoded.json",
    "evidence.json",
    "measurement.json",
    "observer.json",
    "portability-receipt.json",
    "public-manifest.json",
    "redaction-receipt.json",
    "summary.json",
)
CONTENT_EXCLUDED_FROM_MANIFEST = frozenset(
    {"bundle-manifest.json", "source-denied-reproduction-receipt.json", "README.md"}
)
_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|private|tmp|var|home)(?:/|$)")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?:bearer\s+|authorization\s*[:=]|api[_-]?key\s*[:=|]|"
    r"access[_-]?token\s*[:=|]|refresh[_-]?token\s*[:=|]|"
    r"password\s*[:=|]|private[_-]?key\s*[:=|])",
    re.I,
)


class OpenCodeConfigurationBundleError(ValueError):
    """The OpenCode configuration cannot be closed as a public packet."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OpenCodeConfigurationBundleError("bundle data must be finite JSON") from exc


def _write_json(path: Path, value: Any) -> str:
    payload = _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenCodeConfigurationBundleError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OpenCodeConfigurationBundleError(f"{path} must contain one JSON object")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise OpenCodeConfigurationBundleError(
            f"{label} has wrong fields (missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)})"
        )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ordinary_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise OpenCodeConfigurationBundleError(f"bundle root is not an ordinary directory: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise OpenCodeConfigurationBundleError(
                f"public bundle must not contain symlinks: {path.relative_to(root)}"
            )
        if path.is_file():
            files.append(path)
    return files


def _privacy_findings(root: Path) -> list[str]:
    """Return stable, non-content privacy findings for every public byte."""

    findings: list[str] = []
    for path in _ordinary_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"{path.relative_to(root)}:non_utf8_or_unreadable")
            continue
        relative = path.relative_to(root).as_posix()
        for marker, pattern in (
            ("absolute_local_path", _LOCAL_PATH),
            ("email_address", _EMAIL),
            ("credential_assignment", _SECRET_ASSIGNMENT),
        ):
            if pattern.search(text):
                findings.append(f"{relative}:{marker}")
    return findings


def _assert_no_private_artifact_names(root: Path) -> None:
    forbidden_names = {"opencode.db", "opencode.db-wal", "opencode.db-shm"}
    for path in _ordinary_files(root):
        if path.name in forbidden_names or "replay-runtime" in path.parts:
            raise OpenCodeConfigurationBundleError(
                f"private native/runtime artifact entered public bundle: {path.relative_to(root)}"
            )


def _source_package(package: Path) -> dict[str, Any]:
    """Verify one corrected package and its existing sanitized derivative."""

    package = package.resolve(strict=True)
    if any(path.is_symlink() for path in package.rglob("*")):
        raise OpenCodeConfigurationBundleError(f"source package contains a symlink: {package}")
    manifest = _verify_package(package)
    correction = manifest.get("correction")
    if not isinstance(correction, Mapping) or correction.get("reason") != "missing_decoder_runtime":
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: source is not a missing_decoder_runtime correction package"
        )
    correction_receipt = _read_json(package / "correction-receipt.json")
    if (
        correction_receipt.get("reason") != "missing_decoder_runtime"
        or correction_receipt.get("canonical_decode_equal") is not True
        or correction_receipt.get("measurement_equal") is not True
        or correction_receipt.get("survival_score_equal") is not True
        or correction_receipt.get("independent_reproduction") is not False
    ):
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: correction equality or independence gate is incomplete"
        )
    # package is .../<run>/evaluation-correction; the derivative is the
    # sibling created by the existing sanitized-derivative command.
    derivative = package.parent / "public-derivative"
    if derivative.is_symlink() or not derivative.is_dir():
        raise OpenCodeConfigurationBundleError(f"{package.name}: sanitized public derivative is missing")
    if tuple(sorted(path.name for path in derivative.iterdir() if path.is_file())) != tuple(sorted(PUBLIC_SEMANTIC_FILES)):
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: sanitized derivative file set is incomplete"
        )
    public_manifest = _read_json(derivative / "public-manifest.json")
    if (
        public_manifest.get("source_package_id") != manifest.get("package_id")
        or public_manifest.get("source_package_digest") != manifest.get("package_digest")
        or public_manifest.get("raw_publication_state") != "withheld"
        or public_manifest.get("replayable_as_raw_package") is not False
        or public_manifest.get("independent_reproduction") is not False
    ):
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: public derivative does not bind the corrected source"
        )
    redaction = _read_json(derivative / "redaction-receipt.json")
    if redaction.get("raw_sqlite_withheld") is not True or redaction.get("raw_runtime_withheld") is not True:
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: redaction receipt does not withhold private artifacts"
        )
    if _privacy_findings(derivative):
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: sanitized derivative failed the public-data scan"
        )
    format_document = _read_json(package.parent / "format-evidence-qualified.json")
    try:
        validate_format_evidence(format_document)
    except (TypeError, ValueError) as exc:
        raise OpenCodeConfigurationBundleError(
            f"{package.name}: qualified format evidence is invalid: {exc}"
        ) from exc
    return {
        "package": package,
        "manifest": manifest,
        "derivative": derivative,
        "public_manifest": public_manifest,
        "redaction": redaction,
        "format": format_document,
        "package_id": str(manifest["package_id"]),
        "package_digest": str(manifest["package_digest"]),
    }


def _source_denied_profile(read_roots: Sequence[Path], write_roots: Sequence[Path]) -> str:
    """Build a macOS profile without the broad Homebrew allow-list.

    The shared ``macos_profile`` intentionally allows Homebrew so the generic
    offline decoder can find a Python interpreter.  This stricter profile
    allows only the interpreter's own installation prefix, so an installed
    OpenCode executable in Homebrew remains a denied probe target.
    """

    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise OpenCodeConfigurationBundleError(
            "source-denied OpenCode replay requires macOS sandbox-exec"
        )
    import sysconfig

    roots = [
        Path("/System"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/Library"),
        Path("/private/etc"),
        Path("/dev"),
        Path(sys.executable).resolve(),
        Path(sys.prefix).resolve(),
        Path(sysconfig.get_paths()["stdlib"]).resolve(),
        # Homebrew's sqlite3 extension is dynamically linked to this
        # dependency; allowing the library directory does not allow the
        # OpenCode executable in /opt/homebrew/bin.
        Path("/opt/homebrew/opt/sqlite").resolve(),
        *[Path(value).resolve() for value in read_roots],
        *[Path(value).resolve() for value in write_roots],
    ]
    # Preserve order while avoiding redundant rules.
    unique_roots: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        value = str(root)
        if value not in seen:
            seen.add(value)
            unique_roots.append(root)
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec)",
        "(deny process-fork)",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow file-read-metadata)",
        '(allow file-read* (literal "/"))',
    ]
    for root in unique_roots:
        encoded = json.dumps(str(root))
        rules.append(f"(allow file-read* (subpath {encoded}))")
    for root in [Path(value).resolve() for value in write_roots]:
        rules.append(f"(allow file-write* (subpath {json.dumps(str(root))}))")
    return "\n".join(rules)


def _run_sandboxed(
    package: Path,
    temporary_root: Path,
    *,
    damage: bool,
) -> dict[str, Any]:
    """Execute only the copied package's standalone runner under the sandbox."""

    runner = package / "replay-runtime/scripts/recheck_opencode_package.py"
    if runner.is_symlink() or not runner.is_file():
        raise OpenCodeConfigurationBundleError("copied package lacks its standalone runner")
    profile = _source_denied_profile([package, temporary_root], [temporary_root])
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(temporary_root),
        "TMPDIR": str(temporary_root),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C",
    }
    command = [
        "/usr/bin/sandbox-exec",
        "-p",
        profile,
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(runner),
        "--package",
        str(package),
        "--execute-in-runtime",
    ]
    if damage:
        command.append("--damage-control")
    try:
        completed = subprocess.run(
            command,
            cwd=package / "replay-runtime",
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenCodeConfigurationBundleError(
            f"sandboxed OpenCode replay failed to start: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise OpenCodeConfigurationBundleError(
            f"sandboxed OpenCode replay failed ({completed.returncode}): {detail[-1200:]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OpenCodeConfigurationBundleError(
            "sandboxed OpenCode replay did not return one JSON receipt"
        ) from exc
    if not isinstance(value, dict):
        raise OpenCodeConfigurationBundleError("sandboxed OpenCode replay returned a non-object")
    return value


def _denial_probe(
    package: Path,
    temporary_root: Path,
    *,
    original_package: Path,
    vendor_candidates: Sequence[Path],
) -> dict[str, Any]:
    """Prove source, runtime, vendor, and network access are denied."""

    if not vendor_candidates:
        raise OpenCodeConfigurationBundleError(
            "no installed OpenCode executable was available for the denial probe"
        )
    profile = _source_denied_profile([package, temporary_root], [temporary_root])
    code = r'''
import errno, json, os, socket, sys

def read_status(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.read(descriptor, 1)
        finally:
            os.close(descriptor)
        return "allowed"
    except PermissionError:
        return "denied"
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return "denied" if exc.errno in {errno.EACCES, errno.EPERM} else type(exc).__name__

result = {
    "original_package": read_status(sys.argv[1]),
    "original_runtime": read_status(sys.argv[2]),
    "vendor": [read_status(value) for value in sys.argv[3:]],
}
try:
    descriptor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        descriptor.bind(("127.0.0.1", 0))
    finally:
        descriptor.close()
    result["network"] = "allowed"
except PermissionError:
    result["network"] = "denied"
except OSError as exc:
    result["network"] = "denied" if exc.errno in {errno.EACCES, errno.EPERM} else type(exc).__name__
print(json.dumps(result, sort_keys=True))
'''
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(temporary_root),
        "TMPDIR": str(temporary_root),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "LC_ALL": "C",
    }
    command = [
        "/usr/bin/sandbox-exec",
        "-p",
        profile,
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        code,
        str(original_package / "evidence.json"),
        str(original_package / "replay-runtime/session_bench/adapters/opencode_decoder.py"),
        *[str(value) for value in vendor_candidates],
    ]
    # These arguments deliberately point back to the original source package,
    # which is outside every read root in the profile.  The copied package is
    # passed only to the standalone runner above.
    try:
        completed = subprocess.run(
            command,
            cwd=temporary_root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OpenCodeConfigurationBundleError(
            f"sandboxed denial probe failed to start: {type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic"
        raise OpenCodeConfigurationBundleError(
            f"sandboxed denial probe failed ({completed.returncode}): {detail[-1200:]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OpenCodeConfigurationBundleError("denial probe did not return one JSON object") from exc
    if not isinstance(value, dict):
        raise OpenCodeConfigurationBundleError("denial probe returned a non-object")
    if value.get("original_package") != "denied" or value.get("original_runtime") != "denied":
        raise OpenCodeConfigurationBundleError("original package/runtime access was not denied")
    vendor_status = value.get("vendor")
    if not isinstance(vendor_status, list) or not vendor_status or any(item != "denied" for item in vendor_status):
        raise OpenCodeConfigurationBundleError("vendor executable access was not denied")
    if value.get("network") != "denied":
        raise OpenCodeConfigurationBundleError("network access was not denied")
    return {
        "original_package_read": "denied",
        "original_runtime_read": "denied",
        "vendor_executable_read": "denied",
        "vendor_candidates_checked": len(vendor_status),
        "network": "denied",
    }


def _source_denied_run(source: Mapping[str, Any], vendor_candidates: Sequence[Path]) -> dict[str, Any]:
    package = Path(source["package"])
    with tempfile.TemporaryDirectory(prefix="session-bench-opencode-source-denied-") as raw:
        temporary_root = Path(raw)
        copied = temporary_root / "package"
        shutil.copytree(package, copied, symlinks=False)
        if any(path.is_symlink() for path in copied.rglob("*")):
            raise OpenCodeConfigurationBundleError("temporary correction copy contains a symlink")
        replay = _run_sandboxed(copied, temporary_root, damage=False)
        damage = _run_sandboxed(copied, temporary_root, damage=True)
        denial = _denial_probe(
            copied,
            temporary_root,
            original_package=package,
            vendor_candidates=vendor_candidates,
        )
    expected_manifest = source["manifest"]
    expected_package_id = expected_manifest.get("package_id")
    expected_package_digest = expected_manifest.get("package_digest")
    expected_result_id = expected_manifest.get("result_id")
    if replay.get("package_id") != expected_package_id or replay.get("package_digest") != expected_package_digest:
        raise OpenCodeConfigurationBundleError("replay: copied package identity mismatch")
    if damage.get("package_id") != expected_package_id:
        raise OpenCodeConfigurationBundleError("damage: copied package identity mismatch")
    if (
        replay.get("configuration_id") != "opencode-cli"
        or replay.get("result_id") != expected_result_id
        or replay.get("independent_reproduction") is not False
        or replay.get("survival_overall") != "81.0"
    ):
        raise OpenCodeConfigurationBundleError("source-denied replay did not reproduce the corrected Survival result")
    if (
        damage.get("package_id") != expected_package_id
        or damage.get("independent_reproduction") is not False
        or damage.get("intact_correct") != 2
        or damage.get("damaged_correct") != 1
        or damage.get("loss_state") != "reduced_visible_response_accuracy"
    ):
        raise OpenCodeConfigurationBundleError("source-denied selected-loss control did not qualify")
    stored_decode = _read_json(package / "decoded.json")
    ordinary_decode_sha = _sha256_bytes(_normalized_decode(stored_decode))
    if replay.get("fresh_decode_sha256") != ordinary_decode_sha:
        raise OpenCodeConfigurationBundleError("source-denied native decode differs from stored canonical decode")
    return {
        "replay": replay,
        "selected_loss_control": damage,
        "denial": denial,
        "canonical_decode": {
            "ordinary_sha256": ordinary_decode_sha,
            "isolated_sha256": replay["fresh_decode_sha256"],
            "equal": True,
        },
    }


def _score_inputs(source: Mapping[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    derivative = Path(source["derivative"])
    survival = _read_json(derivative / "evidence.json")
    format_document = deepcopy(dict(source["format"]))
    try:
        scored = score_public_run(survival, format_document)
    except (TypeError, ValueError, KeyError) as exc:
        raise OpenCodeConfigurationBundleError(
            f"{source['package_id']}: public score inputs are not admissible: {exc}"
        ) from exc
    blockers = public_identity_blockers([scored])
    if not scored.rankable or scored.overall is None or blockers:
        raise OpenCodeConfigurationBundleError(
            f"{source['package_id']}: public score is unresolved (blockers={list(scored.blockers) + list(blockers)})"
        )
    if len(scored.metrics) != len(PUBLIC_METRICS) or len(scored.metric_evidence) != len(PUBLIC_METRICS):
        raise OpenCodeConfigurationBundleError(f"{source['package_id']}: score does not contain all 31 metrics")
    return scored, survival, format_document


def _fraction_text(value: Fraction | None) -> str | None:
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _configuration_score_document(aggregate: Any) -> dict[str, Any]:
    if aggregate.overall is None or aggregate.overall_range is None or aggregate.sensitivity is None:
        raise OpenCodeConfigurationBundleError("configuration aggregate has no complete score")
    return {
        "schema_version": "session-bench-opencode-public-configuration-score-v1",
        "configuration_id": aggregate.configuration_id,
        "bundle_id": aggregate.bundle_id,
        "reproduction_receipt_id": aggregate.reproduction_receipt_id,
        "verification": aggregate.verification,
        "rankable_within_configuration": aggregate.rankable,
        "public_claim_allowed": False,
        "global_rank_allowed": False,
        "unranked_reasons": [
            "independent operator or environment reproduction is not established",
            "global v1 rank requires all five qualified configurations",
        ],
        "independent_reproduction": False,
        "overall": aggregate.display()["overall"],
        "overall_exact": _fraction_text(aggregate.overall),
        "range": aggregate.display()["range"],
        "range_exact": [_fraction_text(value) for value in aggregate.overall_range],
        "categories": {key: aggregate.display()["categories"][key] for key in PUBLIC_CATEGORY_POINTS},
        "category_ranges": aggregate.display()["category_ranges"],
        "sensitivity": aggregate.display()["sensitivity"],
        "repetitions": [
            {
                "repetition": run.repetition,
                "run_id": run.run_id,
                "result_id": run.result_id,
                "overall": run.display()["overall"],
                "overall_exact": _fraction_text(run.overall),
                "metric_count": len(run.metrics),
                "rankable": run.rankable,
            }
            for run in aggregate.runs
        ],
        "leaderboard": {
            "published": False,
            "reason": "global v1 rank requires all five qualified configurations",
        },
    }


def _manifest_core(
    *,
    bundle: Any,
    source_packages: Sequence[Mapping[str, Any]],
    files: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "id": BUNDLE_ID,
        "configuration_id": "opencode-cli",
        "immutable": True,
        "public": True,
        "configuration_bundle_sha256": bundle.bundle_sha256,
        "result_ids": list(bundle.result_ids),
        "source_packages": [deepcopy(dict(item)) for item in source_packages],
        "files": [deepcopy(dict(item)) for item in files],
        "excluded_private": {
            "native_sqlite_family": True,
            "replay_runtime": True,
            "original_workspace_paths": True,
        },
    }


def _content_entries(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _ordinary_files(root):
        relative = path.relative_to(root).as_posix()
        if path.name in CONTENT_EXCLUDED_FROM_MANIFEST:
            continue
        entries.append(
            {
                "path": relative,
                "sha256": _sha(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def _manifest_document(root: Path, *, bundle: Any, source_packages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files = _content_entries(root)
    core = _manifest_core(bundle=bundle, source_packages=source_packages, files=files)
    return {**core, "content_sha256": canonical_sha256(core)}


def _validate_manifest(root: Path, document: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "id",
        "configuration_id",
        "immutable",
        "public",
        "configuration_bundle_sha256",
        "result_ids",
        "source_packages",
        "files",
        "excluded_private",
        "content_sha256",
    }
    _strict_keys(document, expected, "bundle manifest")
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION or document.get("immutable") is not True or document.get("public") is not True:
        raise OpenCodeConfigurationBundleError("bundle manifest flags or schema are invalid")
    files = document.get("files")
    if not isinstance(files, list):
        raise OpenCodeConfigurationBundleError("bundle manifest files are missing")
    listed = {str(item.get("path")) for item in files if isinstance(item, Mapping)}
    actual = {path.relative_to(root).as_posix() for path in _ordinary_files(root) if path.name not in CONTENT_EXCLUDED_FROM_MANIFEST}
    if listed != actual or len(listed) != len(files):
        raise OpenCodeConfigurationBundleError("bundle manifest file boundary differs from output")
    for item in files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256", "size_bytes"}:
            raise OpenCodeConfigurationBundleError("bundle manifest file entry is malformed")
        relative = item["path"]
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise OpenCodeConfigurationBundleError("bundle manifest contains an unsafe path")
        path = root / relative
        if not path.is_file() or path.is_symlink() or _sha(path) != item["sha256"] or path.stat().st_size != item["size_bytes"]:
            raise OpenCodeConfigurationBundleError(f"bundle manifest hash mismatch: {relative}")
    core = {key: value for key, value in document.items() if key != "content_sha256"}
    if canonical_sha256(core) != document.get("content_sha256"):
        raise OpenCodeConfigurationBundleError("bundle manifest content digest mismatch")


def _rich_receipt(
    *,
    bundle: Any,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    run_details: Sequence[Mapping[str, Any]],
    privacy_scan_count: int,
) -> dict[str, Any]:
    core = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "id": RECEIPT_ID,
        "scope": "source-denied local replay of copied correction packages",
        "independent_reproduction": False,
        "verified": True,
        "offline_recomputed": True,
        "independence_basis": (
            "same implementation host and filesystem; separate local audit corroborates the replay "
            "but does not establish an independent operator or environment"
        ),
        "configuration_id": "opencode-cli",
        "bundle_id": bundle.bundle_id,
        "bundle_sha256": bundle.bundle_sha256,
        "bundle_manifest_content_sha256": manifest["content_sha256"],
        "bundle_manifest_sha256": manifest_sha256,
        "result_ids": list(bundle.result_ids),
        "runs": [deepcopy(dict(item)) for item in run_details],
        "separate_audit": {
            "observed": True,
            "independent_reproduction": False,
            "qualification": "same-host copied-package audit only",
        },
        "privacy_scan": {
            "passed": True,
            "scanned_file_count": privacy_scan_count,
            "raw_sqlite_published": False,
            "raw_runtime_published": False,
        },
    }
    return {**core, "receipt_sha256": canonical_sha256(core)}


def _read_vendor_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    discovered = shutil.which("opencode")
    if discovered:
        candidates.append(Path(discovered).resolve(strict=False))
    app_binary = Path("/Applications/OpenCode.app/Contents/MacOS/opencode")
    if app_binary.is_file():
        candidates.append(app_binary)
    result: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path.is_file() and path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def _build_in_directory(packages: Sequence[Path], work: Path) -> dict[str, Any]:
    if len(packages) != 3:
        raise OpenCodeConfigurationBundleError("OpenCode configuration requires exactly three packages")
    sources = [_source_package(package) for package in packages]
    repetitions = [int(_read_json(Path(source["package"]) / "evidence.json")["repetition"]) for source in sources]
    if set(repetitions) != {1, 2, 3}:
        raise OpenCodeConfigurationBundleError(f"OpenCode package repetitions are not exactly 1, 2, 3: {repetitions}")
    vendor_candidates = _read_vendor_candidates()
    run_details: list[dict[str, Any]] = []
    public_runs: list[Any] = []
    run_records: list[dict[str, Any]] = []
    for source, repetition in sorted(zip(sources, repetitions), key=lambda item: item[1]):
        score, survival, format_document = _score_inputs(source)
        replay = _source_denied_run(source, vendor_candidates)
        package = Path(source["package"])
        runtime_sha = _sha(package / "replay-runtime/manifest.json")
        public_manifest_sha = _sha(Path(source["derivative"]) / "public-manifest.json")
        result_id = score.result_id
        if result_id is None:
            raise OpenCodeConfigurationBundleError("public score is missing a result ID")
        run_record = run_record_from_public_score(
            score,
            replay={
                "evidence_id": f"source-denied-replay:{source['package_id']}",
                "verified": True,
                "offline": True,
                "original_root_denied": replay["denial"]["original_package_read"] == "denied",
                "vendor_executable_denied": replay["denial"]["vendor_executable_read"] == "denied",
                "network_denied": replay["denial"]["network"] == "denied",
                "runtime_sha256": runtime_sha,
            },
            canonical_equality={
                "evidence_id": f"canonical-decode:{source['package_id']}",
                "verified": replay["canonical_decode"]["equal"],
                "ordinary_sha256": replay["canonical_decode"]["ordinary_sha256"],
                "isolated_sha256": replay["canonical_decode"]["isolated_sha256"],
            },
            privacy={
                "evidence_id": f"redaction:{source['package_id']}",
                "verified": True,
                "credentials_absent": True,
                "account_data_absent": True,
                "personal_history_absent": True,
                "absolute_paths_absent": True,
                "raw_native_withheld": True,
                "public_derivative_sha256": public_manifest_sha,
            },
        )
        run_records.append(run_record)
        public_runs.append(score)
        run_dir = work / "runs" / str(repetition)
        score_input_dir = run_dir / "score-input"
        semantic_dir = run_dir / "semantic"
        score_input_dir.mkdir(parents=True, exist_ok=True)
        for name in PUBLIC_SEMANTIC_FILES:
            source_path = Path(source["derivative"]) / name
            target = semantic_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        shutil.copyfile(Path(source["derivative"]) / "evidence.json", score_input_dir / "survival-evidence.json")
        shutil.copyfile(Path(source["package"]).parent / "format-evidence-qualified.json", score_input_dir / "format-evidence.json")
        _write_json(score_input_dir / "public-score.json", score.display())
        _write_json(run_dir / "source-denied-replay.json", {
            "schema_version": "session-bench-opencode-source-denied-run-receipt-v1",
            "configuration_id": "opencode-cli",
            "repetition": repetition,
            "package_id": source["package_id"],
            "package_digest": source["package_digest"],
            "result_id": result_id,
            "replay": replay["replay"],
            "selected_loss_control": replay["selected_loss_control"],
            "denial": replay["denial"],
            "canonical_decode": replay["canonical_decode"],
            "independent_reproduction": False,
        })
        run_details.append({
            "repetition": repetition,
            "run_id": score.run_id,
            "result_id": result_id,
            "package_id": source["package_id"],
            "package_digest": source["package_digest"],
            "runtime_manifest_sha256": runtime_sha,
            "source_denied": replay["denial"],
            "canonical_equality": replay["canonical_decode"],
            "selected_loss_control": {
                "intact_correct": replay["selected_loss_control"]["intact_correct"],
                "damaged_correct": replay["selected_loss_control"]["damaged_correct"],
                "loss_state": replay["selected_loss_control"]["loss_state"],
            },
            "public_score": {
                "overall": score.display()["overall"],
                "overall_exact": _fraction_text(score.overall),
                "rankable": score.rankable,
                "metric_count": len(score.metrics),
                "categories": score.display()["categories"],
            },
        })
        # Keep the input variables in scope for reviewers/debuggers; score_public_run
        # has already validated both exact evidence wrappers above.
        del survival, format_document
    bundle = build_configuration_bundle(
        run_records,
        bundle_id=BUNDLE_ID,
        receipt_id=BINDING_RECEIPT_ID,
    )
    validate_configuration_bundle(bundle.display())
    aggregate = aggregate_public_configuration(
        public_runs,
        configuration_evidence=bundle.report_input,
    )
    if not aggregate.rankable or aggregate.overall is None:
        raise OpenCodeConfigurationBundleError(
            f"OpenCode configuration aggregate is not rankable: {aggregate.blockers}"
        )
    if public_identity_blockers(public_runs):
        raise OpenCodeConfigurationBundleError("OpenCode configuration identity gate is incomplete")
    work.mkdir(parents=True, exist_ok=True)
    _write_json(work / "configuration-bundle.json", bundle.display())
    _write_json(work / "configuration-evidence.json", bundle.report_input)
    _write_json(work / "configuration-score.json", _configuration_score_document(aggregate))
    source_package_rows = [
        {
            "repetition": int(repetition),
            "package_id": source["package_id"],
            "package_digest": source["package_digest"],
            "result_id": public_runs[index].result_id,
            "public_derivative_manifest_sha256": _sha(Path(source["derivative"]) / "public-manifest.json"),
            "format_evidence_sha256": _sha(Path(source["package"]).parent / "format-evidence-qualified.json"),
        }
        for index, (source, repetition) in enumerate(sorted(zip(sources, repetitions), key=lambda item: item[1]))
    ]
    manifest = _manifest_document(work, bundle=bundle, source_packages=source_package_rows)
    _write_json(work / "bundle-manifest.json", manifest)
    _validate_manifest(work, manifest)
    preliminary_findings = _privacy_findings(work)
    if preliminary_findings:
        raise OpenCodeConfigurationBundleError(
            f"public bundle failed privacy scan: {preliminary_findings}"
        )
    readme = f"""# OpenCode CLI v1 public configuration packet

This is the sanitized three repetition configuration packet for OpenCode CLI.
It is derived from three immutable `missing_decoder_runtime` correction packages
and contains the exact public score inputs for all 31 v1 metrics.

## Result

- Configuration: OpenCode CLI 1.18.30 using `opencode/muse-spark-1.3-contributor-free`
- Three repetition mean: **{aggregate.display()['overall']}/100** (exact arithmetic `{_fraction_text(aggregate.overall)}`)
- Repetition range: **{aggregate.display()['range'][0]}–{aggregate.display()['range'][1]}**
- Offline verification: **{aggregate.verification}** for this configuration packet
- Independent reproduction: **not established** (`independent_reproduction: false`)
- Five-angle categories: {', '.join(f"{name} {aggregate.display()['categories'][name]}" for name in PUBLIC_CATEGORY_POINTS)}

An internal report-card line (not cleared for publication) would read: “OpenCode CLI
scored {aggregate.display()['overall']}/100 on Session-Bench v1's five-angle, 31-metric
configuration packet across three corrected runs.” This is a configuration-level local
evidence result. The independent-reproduction and five-configuration cohort gates remain
closed, so it is not a public v1 score, leaderboard rank, or general model-quality claim.

## Evidence boundary

Each repetition includes a sanitized survival evidence input, a qualified 12-metric
format evidence input, the scorer's 31-metric output, and a source-denied replay
receipt. The replay copied the correction package into a temporary directory and
ran only its bundled decoder, comparator, scorer, package verifier, and recheck
runner. Original package bytes, the vendor executable, and network access were
denied. The selected loss control removed the observed R2 response and changed
visible-response accuracy from 2/2 to 1/2 in every repetition.

Raw SQLite/WAL/SHM files, replay-runtime source, original workspace paths, and
account data are withheld. The `semantic` directories are redacted derivatives;
they are exact public evidence inputs but are not raw native packages.

The local source-denied receipt intentionally says `independent_reproduction: false`.
The separate audit used the same host and filesystem, so it corroborates the
replay without qualifying an independent operator or environment.

## Verification

The canonical binding is in `configuration-bundle.json`; its digest is bound by
`configuration-evidence.json` and `bundle-manifest.json`. The strict scorer input
is the pair of files under each `runs/<n>/score-input/` directory. Run
`python3 scripts/build_opencode_configuration_bundle.py --verify` after copying
this directory into a checkout to revalidate the hashes, 31 cells, score, privacy
scan, and global-cohort boundary.

`bundle-manifest.json` is an immutable manifest over all packet content except
itself, this README, and the source-denied receipt, which bind the manifest after
the content digest is known.
"""
    (work / "README.md").write_text(readme, encoding="utf-8")
    privacy_findings = _privacy_findings(work)
    if privacy_findings:
        raise OpenCodeConfigurationBundleError(f"public bundle failed privacy scan: {privacy_findings}")
    receipt = _rich_receipt(
        bundle=bundle,
        manifest=manifest,
        manifest_sha256=_sha(work / "bundle-manifest.json"),
        run_details=run_details,
        privacy_scan_count=len(_ordinary_files(work)),
    )
    _write_json(work / "source-denied-reproduction-receipt.json", receipt)
    _assert_no_private_artifact_names(work)
    privacy_findings = _privacy_findings(work)
    if privacy_findings:
        raise OpenCodeConfigurationBundleError(f"public bundle failed final privacy scan: {privacy_findings}")
    _validate_manifest(work, _read_json(work / "bundle-manifest.json"))
    _verify_public_bundle(work)
    return {
        "output": str(work),
        "configuration_id": "opencode-cli",
        "bundle_id": bundle.bundle_id,
        "bundle_sha256": bundle.bundle_sha256,
        "bundle_manifest_content_sha256": manifest["content_sha256"],
        "reproduction_receipt_id": RECEIPT_ID,
        "reproduction_receipt_sha256": receipt["receipt_sha256"],
        "overall": aggregate.display()["overall"],
        "overall_exact": _fraction_text(aggregate.overall),
        "range": aggregate.display()["range"],
        "category_scores": aggregate.display()["categories"],
        "rankable_within_configuration": aggregate.rankable,
        "public_claim_allowed": False,
        "global_rank_allowed": False,
        "global_leaderboard": "blocked_until_five_configuration_cohort_qualifies",
        "independent_reproduction": False,
        "privacy_findings": [],
        "source_denied_runs": 3,
    }


def _verify_rich_receipt(root: Path, bundle: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    receipt_path = root / "source-denied-reproduction-receipt.json"
    receipt = _read_json(receipt_path)
    if (
        receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION
        or receipt.get("id") != RECEIPT_ID
        or receipt.get("independent_reproduction") is not False
    ):
        raise OpenCodeConfigurationBundleError("source-denied receipt schema or independence flag is invalid")
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if canonical_sha256(core) != receipt.get("receipt_sha256"):
        raise OpenCodeConfigurationBundleError("source-denied receipt hash mismatch")
    if receipt.get("bundle_sha256") != bundle["bundle"]["sha256"] or receipt.get("bundle_manifest_content_sha256") != manifest["content_sha256"]:
        raise OpenCodeConfigurationBundleError("source-denied receipt is bound to a different bundle")
    if receipt.get("verified") is not True or receipt.get("offline_recomputed") is not True:
        raise OpenCodeConfigurationBundleError("source-denied receipt is not verified")
    rows = receipt.get("runs")
    if not isinstance(rows, list) or len(rows) != 3 or {row.get("repetition") for row in rows if isinstance(row, Mapping)} != {1, 2, 3}:
        raise OpenCodeConfigurationBundleError("source-denied receipt does not cover three repetitions")
    for row in rows:
        if not isinstance(row, Mapping) or row.get("public_score", {}).get("metric_count") != 31:
            raise OpenCodeConfigurationBundleError("source-denied receipt lacks one complete 31-metric row")
        if row.get("source_denied", {}).get("original_package_read") != "denied" or row.get("source_denied", {}).get("original_runtime_read") != "denied" or row.get("source_denied", {}).get("vendor_executable_read") != "denied" or row.get("source_denied", {}).get("network") != "denied":
            raise OpenCodeConfigurationBundleError("source-denied receipt lacks a complete access-denial proof")
        if row.get("canonical_equality", {}).get("equal") is not True:
            raise OpenCodeConfigurationBundleError("source-denied receipt lacks canonical equality")
        loss = row.get("selected_loss_control", {})
        if loss.get("intact_correct") != 2 or loss.get("damaged_correct") != 1:
            raise OpenCodeConfigurationBundleError("source-denied receipt lacks the selected-loss control")


def _verify_public_bundle(root: Path) -> dict[str, Any]:
    """Verify a built packet using only its public files plus scorer code."""

    root = Path(root).resolve(strict=True)
    _assert_no_private_artifact_names(root)
    findings = _privacy_findings(root)
    if findings:
        raise OpenCodeConfigurationBundleError(f"public bundle privacy scan failed: {findings}")
    bundle_document = _read_json(root / "configuration-bundle.json")
    bundle = validate_configuration_bundle(bundle_document)
    manifest = _read_json(root / "bundle-manifest.json")
    _validate_manifest(root, manifest)
    if manifest.get("configuration_bundle_sha256") != bundle.bundle_sha256 or manifest.get("result_ids") != list(bundle.result_ids):
        raise OpenCodeConfigurationBundleError("bundle manifest and canonical bundle disagree")
    evidence = _read_json(root / "configuration-evidence.json")
    if evidence != bundle.report_input:
        raise OpenCodeConfigurationBundleError("configuration evidence differs from canonical bundle binding")
    scores: list[Any] = []
    for repetition in (1, 2, 3):
        run_dir = root / "runs" / str(repetition)
        survival = _read_json(run_dir / "score-input/survival-evidence.json")
        format_document = _read_json(run_dir / "score-input/format-evidence.json")
        score = score_public_run(survival, format_document)
        if not score.rankable or score.result_id not in bundle.result_ids or len(score.metrics) != 31:
            raise OpenCodeConfigurationBundleError(f"public run {repetition} is not a complete score input")
        recorded_score = _read_json(run_dir / "score-input/public-score.json")
        if recorded_score != score.display():
            raise OpenCodeConfigurationBundleError(f"public run {repetition} score projection differs from recomputation")
        replay = _read_json(run_dir / "source-denied-replay.json")
        if replay.get("independent_reproduction") is not False or replay.get("denial", {}).get("network") != "denied":
            raise OpenCodeConfigurationBundleError(f"public run {repetition} source-denied receipt is incomplete")
        scores.append(score)
    aggregate = aggregate_public_configuration(scores, configuration_evidence=evidence)
    score_document = _read_json(root / "configuration-score.json")
    if (
        score_document.get("overall_exact") != _fraction_text(aggregate.overall)
        or score_document.get("rankable_within_configuration") is not True
        or score_document.get("public_claim_allowed") is not False
        or score_document.get("global_rank_allowed") is not False
    ):
        raise OpenCodeConfigurationBundleError("configuration score differs from scorer recomputation")
    _verify_rich_receipt(root, bundle_document, manifest)
    return aggregate.display()


def build(
    *,
    packages: Sequence[Path] = DEFAULT_PACKAGES,
    output: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    """Build an immutable packet at ``output`` and return its public summary."""

    output = Path(output).resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise OpenCodeConfigurationBundleError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".session-bench-opencode-public-", dir=output.parent))
    try:
        summary = _build_in_directory([Path(value) for value in packages], temporary)
        os.replace(temporary, output)
        summary["output"] = str(output)
        return summary
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--package",
        action="append",
        dest="packages",
        type=Path,
        help="corrected package path; repeat exactly three times",
    )
    parser.add_argument("--verify", action="store_true", help="verify an existing output packet")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            value = _verify_public_bundle(args.output)
            print(json.dumps({"output": str(args.output.resolve()), "verified": True, "score": value}, indent=2, sort_keys=True))
        else:
            packages = tuple(args.packages) if args.packages is not None else DEFAULT_PACKAGES
            if len(packages) != 3:
                raise OpenCodeConfigurationBundleError("--package must be supplied exactly three times")
            print(json.dumps(build(packages=packages, output=args.output), indent=2, sort_keys=True))
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"OpenCode configuration bundle refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
