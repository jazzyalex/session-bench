#!/usr/bin/env python3
"""Build a deterministic index over the five sanitized v1 packets.

This command consumes only the already-built public packet directories.  It
does not copy their bytes, discover a native root, open a database, inspect a
transcript, run a vendor executable, or calculate a cross-configuration rank.
Each entry is a repository-relative immutable reference to a packet manifest;
the packet's own verifier and manifest hash are checked before the index is
written.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/survival-v1-public-evidence-index"
SURVIVAL_PACKET_ROOT = ROOT / "artifacts/survival-v1-public-configurations"
OPENCODE_PACKET_ROOT = ROOT / "artifacts/opencode-v1-public-configuration"
CONFIGURATION_IDS = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")
REPETITIONS = (1, 2, 3)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_opencode_configuration_bundle import (  # noqa: E402
    _verify_public_bundle as verify_opencode_packet,
)
from scripts.build_public_configuration_bundles import (  # noqa: E402
    _verify_output as verify_survival_packets,
)
from session_bench.configuration_bundle import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)


INDEX_SCHEMA_VERSION = "session-bench-v1-public-evidence-index-v1"
MANIFEST_SCHEMA_VERSION = "session-bench-v1-public-evidence-index-manifest-v1"
PRIVACY_SCHEMA_VERSION = "session-bench-v1-public-evidence-index-privacy-receipt-v1"
README_NAME = "README.md"
MANIFEST_NAME = "manifest.json"
PRIVACY_RECEIPT_NAME = "privacy-receipt.json"
INDEX_NAME = "index.json"

_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|private|tmp|var|home)(?:/|$)")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET = re.compile(
    r"(?:bearer\s+|authorization\s*[:=]|api[_-]?key\s*[:=|]|"
    r"access[_-]?token\s*[:=|]|refresh[_-]?token\s*[:=|]|"
    r"password\s*[:=|]|private[_-]?key\s*[:=|]|cookie\s*[:=])",
    re.I,
)
_FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".db-wal", ".db-shm", ".jsonl")
_FORBIDDEN_NAMES = {"opencode.db", "opencode.db-wal", "opencode.db-shm"}


class PublicEvidenceIndexError(ValueError):
    """A packet is missing, unsafe, or does not satisfy its public contract."""


@dataclass(frozen=True)
class PacketSpec:
    configuration_id: str
    root: Path
    kind: str


PACKETS = tuple(
    PacketSpec(configuration_id, SURVIVAL_PACKET_ROOT / configuration_id, "survival")
    for configuration_id in CONFIGURATION_IDS[:-1]
) + (PacketSpec("opencode-cli", OPENCODE_PACKET_ROOT, "opencode"),)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicEvidenceIndexError(f"cannot read public JSON {path}") from exc
    if not isinstance(value, dict):
        raise PublicEvidenceIndexError(f"public JSON must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PublicEvidenceIndexError(f"cannot hash public file {path}") from exc


def _ordinary_files(root: Path) -> list[Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise PublicEvidenceIndexError(f"public packet root is not an ordinary directory: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise PublicEvidenceIndexError(f"public packet contains a symlink: {path}")
        if path.is_file():
            files.append(path)
    return files


def _privacy_scan(roots: Sequence[Path]) -> dict[str, Any]:
    """Scan every public byte in the referenced packets and this index."""

    findings: list[str] = []
    file_count = 0
    seen: set[Path] = set()
    for root in roots:
        for path in _ordinary_files(root):
            resolved = path.resolve(strict=True)
            if resolved in seen:
                findings.append(f"duplicate-scan-path:{path}")
                continue
            seen.add(resolved)
            file_count += 1
            relative = f"{root.name}/{path.relative_to(root).as_posix()}"
            if path.name.lower() in _FORBIDDEN_NAMES or path.name.lower().endswith(_FORBIDDEN_SUFFIXES):
                findings.append(f"raw-artifact-name:{relative}")
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                findings.append(f"non-utf8:{relative}")
                continue
            if _LOCAL_PATH.search(text):
                findings.append(f"absolute-local-path:{relative}")
            if _EMAIL.search(text):
                findings.append(f"email:{relative}")
            if _SECRET.search(text):
                findings.append(f"credential-marker:{relative}")
    return {"passed": not findings, "file_count": file_count, "findings": findings}


def _repo_relative(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(ROOT).as_posix()
    except ValueError as exc:
        raise PublicEvidenceIndexError(f"public packet is outside repository: {path}") from exc


def _inventory_digest(root: Path) -> tuple[str, int]:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha_file(path),
        }
        for path in _ordinary_files(root)
    ]
    return canonical_sha256(entries), len(entries)


def _require_exact(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise PublicEvidenceIndexError(
            f"{label} has wrong fields (missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )


def _public_state(configuration_id: str, score_document: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """Retain source publication fields without assigning a new state."""

    if kind == "survival":
        score = score_document
        return {
            "source_document": "configuration-score.json",
            "source_fields": {
                "published": score.get("published"),
                "publication_status": score.get("publication_status"),
                "independent_reproduction": score.get("independent_reproduction"),
                "independent_reproduction_state": score.get("independent_reproduction_state"),
                "global_leaderboard": score.get("global_leaderboard"),
            },
        }
    return {
        "source_document": "configuration-score.json",
        "source_fields": {
            "public_claim_allowed": score_document.get("public_claim_allowed"),
            "global_rank_allowed": score_document.get("global_rank_allowed"),
            "independent_reproduction": score_document.get("independent_reproduction"),
            "leaderboard": score_document.get("leaderboard"),
        },
    }


def _survival_entry(spec: PacketSpec) -> dict[str, Any]:
    try:
        summary = verify_survival_packets(SURVIVAL_PACKET_ROOT)
    except Exception as exc:
        raise PublicEvidenceIndexError("survival public packet verification failed") from exc
    if summary.get("configuration_ids") != list(CONFIGURATION_IDS[:-1]):
        raise PublicEvidenceIndexError("survival packet set has missing or extra configuration IDs")
    config_root = spec.root
    manifest_path = config_root / "bundle-manifest.json"
    bundle_path = config_root / "configuration-bundle.json"
    score_path = config_root / "configuration-score.json"
    manifest = _read_json(manifest_path)
    bundle = _read_json(bundle_path)
    score_document = _read_json(score_path)
    if manifest.get("configuration_id") != spec.configuration_id or manifest.get("schema_version") != "session-bench-public-configuration-manifest-v1":
        raise PublicEvidenceIndexError(f"survival manifest identity mismatch: {spec.configuration_id}")
    if manifest.get("bundle_sha256") != bundle.get("bundle", {}).get("sha256"):
        raise PublicEvidenceIndexError(f"survival manifest bundle binding mismatch: {spec.configuration_id}")
    if bundle.get("configuration_id") != spec.configuration_id or bundle.get("bundle", {}).get("public") is not True or bundle.get("bundle", {}).get("immutable") is not True:
        raise PublicEvidenceIndexError(f"survival bundle is not immutable public evidence: {spec.configuration_id}")
    if score_document.get("configuration_id") != spec.configuration_id or score_document.get("published") is not False or score_document.get("publication_status") != "private_unpublished" or score_document.get("independent_reproduction") is not False or score_document.get("independent_reproduction_state") != "pending":
        raise PublicEvidenceIndexError(f"survival score state is unsafe: {spec.configuration_id}")
    if score_document.get("global_leaderboard", {}).get("published") is not False:
        raise PublicEvidenceIndexError(f"survival global publication state is unsafe: {spec.configuration_id}")
    for repetition in REPETITIONS:
        privacy = _read_json(config_root / "runs" / str(repetition) / "privacy-receipt.json")
        if any(privacy.get(key) is not True for key in ("verified", "credentials_absent", "account_data_absent", "personal_history_absent", "absolute_paths_absent", "raw_native_withheld", "raw_transcript_withheld", "raw_sqlite_withheld")) or privacy.get("independent_reproduction") is not False:
            raise PublicEvidenceIndexError(f"survival private-byte exclusion is incomplete: {spec.configuration_id} r{repetition}")
    runs = bundle.get("runs")
    if not isinstance(runs, list) or len(runs) != 3 or {row.get("repetition") for row in runs if isinstance(row, Mapping)} != set(REPETITIONS) or score_document.get("resolved_metric_counts") != [31, 31, 31]:
        raise PublicEvidenceIndexError(f"survival bundle does not contain exactly three repetitions: {spec.configuration_id}")
    run_rows = []
    for row in sorted(runs, key=lambda item: item["repetition"]):
        result = row.get("result")
        if not isinstance(result, Mapping) or result.get("resolved") is not True or len(result.get("metric_ids", [])) != 31 or len(result.get("metrics", {})) != 31:
            raise PublicEvidenceIndexError(f"survival bundle has an incomplete metric row: {spec.configuration_id}")
        run_rows.append({
            "repetition": row["repetition"],
            "run_id": row.get("run_id"),
            "result_id": row.get("result_id"),
            "metric_count": len(result["metric_ids"]),
            "resolved": result["resolved"],
        })
    inventory_sha, file_count = _inventory_digest(config_root)
    return {
        "configuration_id": spec.configuration_id,
        "packet_kind": spec.kind,
        "packet_path": _repo_relative(config_root),
        "manifest_path": _repo_relative(manifest_path),
        "manifest_sha256": _sha_file(manifest_path),
        "manifest_content_sha256": manifest.get("content_sha256"),
        "configuration_bundle_sha256": bundle.get("bundle", {}).get("sha256"),
        "public_file_inventory_sha256": inventory_sha,
        "public_file_count": file_count,
        "runs": run_rows,
        "score": {
            "overall": score_document.get("score", {}).get("overall"),
            "range": score_document.get("score", {}).get("range"),
            "resolved_metric_counts": score_document.get("resolved_metric_counts"),
        },
        "source_publication_state": _public_state(spec.configuration_id, score_document, spec.kind),
    }


def _opencode_entry(spec: PacketSpec) -> dict[str, Any]:
    try:
        aggregate = verify_opencode_packet(spec.root)
    except Exception as exc:
        raise PublicEvidenceIndexError("OpenCode public packet verification failed") from exc
    manifest_path = spec.root / "bundle-manifest.json"
    bundle_path = spec.root / "configuration-bundle.json"
    score_path = spec.root / "configuration-score.json"
    receipt_path = spec.root / "source-denied-reproduction-receipt.json"
    manifest = _read_json(manifest_path)
    bundle = _read_json(bundle_path)
    score_document = _read_json(score_path)
    receipt = _read_json(receipt_path)
    if manifest.get("configuration_id") != spec.configuration_id or manifest.get("schema_version") != "session-bench-opencode-public-bundle-manifest-v1":
        raise PublicEvidenceIndexError("OpenCode manifest identity mismatch")
    excluded = manifest.get("excluded_private")
    if not isinstance(excluded, Mapping) or any(excluded.get(key) is not True for key in ("native_sqlite_family", "replay_runtime", "original_workspace_paths")):
        raise PublicEvidenceIndexError("OpenCode manifest does not exclude private/native bytes")
    if bundle.get("configuration_id") != spec.configuration_id or bundle.get("bundle", {}).get("public") is not True or bundle.get("bundle", {}).get("immutable") is not True:
        raise PublicEvidenceIndexError("OpenCode bundle is not immutable public evidence")
    if score_document.get("configuration_id") != spec.configuration_id or score_document.get("independent_reproduction") is not False or score_document.get("public_claim_allowed") is not False or score_document.get("global_rank_allowed") is not False:
        raise PublicEvidenceIndexError("OpenCode score state is unsafe")
    if receipt.get("independent_reproduction") is not False or receipt.get("verified") is not True or score_document.get("leaderboard", {}).get("published") is not False:
        raise PublicEvidenceIndexError("OpenCode reproduction state is unsafe")
    runs = bundle.get("runs")
    if not isinstance(runs, list) or len(runs) != 3 or {row.get("repetition") for row in runs if isinstance(row, Mapping)} != set(REPETITIONS):
        raise PublicEvidenceIndexError("OpenCode bundle does not contain exactly three repetitions")
    run_rows = []
    for row in sorted(runs, key=lambda item: item["repetition"]):
        result = row.get("result")
        if not isinstance(result, Mapping) or result.get("resolved") is not True or len(result.get("metric_ids", [])) != 31 or len(result.get("metrics", {})) != 31:
            raise PublicEvidenceIndexError(f"OpenCode bundle has an incomplete metric row r{row.get('repetition')}")
        run_rows.append({
            "repetition": row["repetition"],
            "run_id": row.get("run_id"),
            "result_id": row.get("result_id"),
            "metric_count": len(result["metric_ids"]),
            "resolved": result["resolved"],
        })
    inventory_sha, file_count = _inventory_digest(spec.root)
    return {
        "configuration_id": spec.configuration_id,
        "packet_kind": spec.kind,
        "packet_path": _repo_relative(spec.root),
        "manifest_path": _repo_relative(manifest_path),
        "manifest_sha256": _sha_file(manifest_path),
        "manifest_content_sha256": manifest.get("content_sha256"),
        "configuration_bundle_sha256": bundle.get("bundle", {}).get("sha256"),
        "public_file_inventory_sha256": inventory_sha,
        "public_file_count": file_count,
        "runs": run_rows,
        "score": {
            "overall": score_document.get("overall"),
            "range": score_document.get("range"),
            "resolved_metric_counts": [row.get("metric_count") for row in score_document.get("repetitions", [])],
        },
        "source_publication_state": _public_state(spec.configuration_id, score_document, spec.kind),
    }


def _build_entries() -> list[dict[str, Any]]:
    if {spec.configuration_id for spec in PACKETS} != set(CONFIGURATION_IDS) or len(PACKETS) != len(CONFIGURATION_IDS):
        raise PublicEvidenceIndexError("packet specification set has missing or extra configuration IDs")
    entries = []
    for spec in PACKETS:
        entries.append(_opencode_entry(spec) if spec.kind == "opencode" else _survival_entry(spec))
    if [entry["configuration_id"] for entry in entries] != list(CONFIGURATION_IDS):
        raise PublicEvidenceIndexError("public evidence entries are not the exact five-configuration set")
    return entries


def _readme() -> str:
    return """# Session-Bench v1 public evidence index

This directory is a deterministic index over the five existing sanitized
configuration packets. It contains repository-relative references and hashes;
it does not copy packet bytes. The referenced packets remain the source of
their own score, publication, and independent-reproduction fields.

The index verifies exactly `codex-cli`, `codex-desktop`, `claude-cli`,
`claude-desktop`, and `opencode-cli`, with three repetitions and 31 resolved
metrics per repetition. It does not calculate or publish a cross-configuration
rank. Independent reproduction and publication states are retained from each
packet's score document without relabeling.

The manifest and privacy receipt cover this index, while the verification scan
also covers every byte in all five referenced public packets. Raw native,
transcript, SQLite, credential, account, personal-history, and absolute-path
content is excluded by the nested packet contracts and checked again here.

Revalidate with:

`python3 scripts/build_public_evidence_index.py --verify --output artifacts/survival-v1-public-evidence-index`
"""


def _index_document(entries: Sequence[Mapping[str, Any]], privacy_receipt_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "configuration_ids": list(CONFIGURATION_IDS),
        "configurations": [dict(entry) for entry in entries],
        "packet_bytes_copied": False,
        "cross_configuration_rank": {"computed": False, "published": False, "state": "not_built"},
        "privacy_receipt_sha256": privacy_receipt_sha256,
    }


def _manifest(root: Path, index: Mapping[str, Any]) -> dict[str, Any]:
    files = []
    for path in _ordinary_files(root):
        if path.name == MANIFEST_NAME:
            continue
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha_file(path),
        })
    core = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "id": "session-bench-v1-public-evidence-index",
        "immutable": True,
        "public": True,
        "configuration_ids": list(CONFIGURATION_IDS),
        "index_sha256": _sha_file(root / INDEX_NAME),
        "index_content_sha256": canonical_sha256(index),
        "files": files,
        "packet_references": [
            {
                "configuration_id": entry["configuration_id"],
                "manifest_path": entry["manifest_path"],
                "manifest_sha256": entry["manifest_sha256"],
                "manifest_content_sha256": entry["manifest_content_sha256"],
                "configuration_bundle_sha256": entry["configuration_bundle_sha256"],
            }
            for entry in index["configurations"]
        ],
        "packet_bytes_copied": False,
        "cross_configuration_rank_published": False,
    }
    return {**core, "content_sha256": canonical_sha256(core)}


def _verify_manifest(root: Path, document: Mapping[str, Any]) -> None:
    expected = {
        "schema_version", "id", "immutable", "public", "configuration_ids",
        "index_sha256", "index_content_sha256", "files", "packet_references",
        "packet_bytes_copied", "cross_configuration_rank_published", "content_sha256",
    }
    if set(document) != expected or document.get("schema_version") != MANIFEST_SCHEMA_VERSION or document.get("immutable") is not True or document.get("public") is not True or document.get("configuration_ids") != list(CONFIGURATION_IDS) or document.get("packet_bytes_copied") is not False or document.get("cross_configuration_rank_published") is not False:
        raise PublicEvidenceIndexError("unified public evidence manifest schema or flags are invalid")
    index = _read_json(root / INDEX_NAME)
    if document.get("index_sha256") != _sha_file(root / INDEX_NAME) or document.get("index_content_sha256") != canonical_sha256(index):
        raise PublicEvidenceIndexError("unified public evidence manifest is bound to a different index")
    core = {key: document[key] for key in expected if key != "content_sha256"}
    if canonical_sha256(core) != document.get("content_sha256"):
        raise PublicEvidenceIndexError("unified public evidence manifest content hash mismatch")
    files = document.get("files")
    if not isinstance(files, list):
        raise PublicEvidenceIndexError("unified public evidence manifest files are malformed")
    listed = set()
    for item in files:
        if not isinstance(item, Mapping) or set(item) != {"path", "size_bytes", "sha256"} or not isinstance(item["path"], str) or Path(item["path"]).is_absolute() or item["path"] in listed:
            raise PublicEvidenceIndexError("unified public evidence manifest file row is malformed")
        listed.add(item["path"])
        path = root / item["path"]
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item["size_bytes"] or _sha_file(path) != item["sha256"]:
            raise PublicEvidenceIndexError(f"unified public evidence manifest file hash mismatch: {item['path']}")
    actual = {path.relative_to(root).as_posix() for path in _ordinary_files(root) if path.name != MANIFEST_NAME}
    if listed != actual:
        raise PublicEvidenceIndexError("unified public evidence manifest file set differs from index")
    entries = index.get("configurations")
    expected_references = [
        {
            "configuration_id": entry["configuration_id"],
            "manifest_path": entry["manifest_path"],
            "manifest_sha256": entry["manifest_sha256"],
            "manifest_content_sha256": entry["manifest_content_sha256"],
            "configuration_bundle_sha256": entry["configuration_bundle_sha256"],
        }
        for entry in entries
        if isinstance(entry, Mapping)
    ] if isinstance(entries, list) else []
    if document.get("packet_references") != expected_references:
        raise PublicEvidenceIndexError("unified manifest packet references differ from index")


def _verify_output(output: Path) -> dict[str, Any]:
    root = Path(output).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise PublicEvidenceIndexError(f"unified public evidence output is not an ordinary directory: {root}")
    files = {path.name for path in root.iterdir() if path.is_file()}
    dirs = {path.name for path in root.iterdir() if path.is_dir()}
    if files != {README_NAME, INDEX_NAME, MANIFEST_NAME, PRIVACY_RECEIPT_NAME} or dirs:
        raise PublicEvidenceIndexError("unified public evidence output contains unexpected files")
    index = _read_json(root / INDEX_NAME)
    if index.get("schema_version") != INDEX_SCHEMA_VERSION or index.get("configuration_ids") != list(CONFIGURATION_IDS) or index.get("packet_bytes_copied") is not False:
        raise PublicEvidenceIndexError("unified public evidence index has the wrong configuration boundary")
    if index.get("cross_configuration_rank", {}).get("computed") is not False or index.get("cross_configuration_rank", {}).get("published") is not False:
        raise PublicEvidenceIndexError("unified index incorrectly contains a cross-configuration rank")
    entries = index.get("configurations")
    if not isinstance(entries, list) or [entry.get("configuration_id") for entry in entries if isinstance(entry, Mapping)] != list(CONFIGURATION_IDS):
        raise PublicEvidenceIndexError("unified public evidence index has missing or extra configuration IDs")
    expected_entries = _build_entries()
    if entries != expected_entries:
        raise PublicEvidenceIndexError("unified public evidence index references changed packet bytes or state")
    privacy = _read_json(root / PRIVACY_RECEIPT_NAME)
    if privacy.get("schema_version") != PRIVACY_SCHEMA_VERSION or privacy.get("passed") is not True or privacy.get("findings") != [] or privacy.get("scanned_packet_ids") != list(CONFIGURATION_IDS) or any(privacy.get(key) is not False for key in ("raw_native_bytes_copied", "raw_transcript_bytes_copied", "raw_sqlite_bytes_copied", "absolute_paths_present", "credentials_present", "account_data_present", "personal_history_present")):
        raise PublicEvidenceIndexError("unified public evidence privacy receipt is not clean")
    if index.get("privacy_receipt_sha256") != _sha_file(root / PRIVACY_RECEIPT_NAME):
        raise PublicEvidenceIndexError("unified index privacy receipt binding mismatch")
    _verify_manifest(root, _read_json(root / MANIFEST_NAME))
    scan = _privacy_scan([spec.root for spec in PACKETS] + [root])
    if not scan["passed"] or scan["file_count"] != privacy.get("scanned_file_count"):
        raise PublicEvidenceIndexError(f"unified public evidence privacy scan failed: {scan}")
    return {
        "output": str(root),
        "configuration_ids": list(CONFIGURATION_IDS),
        "manifest_sha256": _sha_file(root / MANIFEST_NAME),
        "index_sha256": _sha_file(root / INDEX_NAME),
        "privacy_receipt_sha256": _sha_file(root / PRIVACY_RECEIPT_NAME),
        "packet_bytes_copied": False,
        "cross_configuration_rank_published": False,
        "privacy_findings": [],
    }


def build(*, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    output = Path(output).resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise PublicEvidenceIndexError(f"refusing to overwrite existing unified index: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".session-bench-public-index-", dir=output.parent))
    try:
        entries = _build_entries()
        (temporary / README_NAME).write_text(_readme(), encoding="utf-8")
        # The final scan includes all four index files plus every referenced
        # packet byte.  The count is deterministic before the receipt exists.
        source_scan = _privacy_scan([spec.root for spec in PACKETS])
        if not source_scan["passed"]:
            raise PublicEvidenceIndexError(f"referenced public packet privacy scan failed: {source_scan['findings']}")
        expected_scan_count = source_scan["file_count"] + 4
        provisional_receipt = {
            "schema_version": PRIVACY_SCHEMA_VERSION,
            "passed": True,
            "findings": [],
            "scanned_file_count": expected_scan_count,
            "scanned_packet_ids": list(CONFIGURATION_IDS),
            "raw_native_bytes_copied": False,
            "raw_transcript_bytes_copied": False,
            "raw_sqlite_bytes_copied": False,
            "absolute_paths_present": False,
            "credentials_present": False,
            "account_data_present": False,
            "personal_history_present": False,
        }
        _write_json(temporary / PRIVACY_RECEIPT_NAME, provisional_receipt)
        index = _index_document(entries, _sha_file(temporary / PRIVACY_RECEIPT_NAME))
        _write_json(temporary / INDEX_NAME, index)
        _write_json(temporary / MANIFEST_NAME, _manifest(temporary, index))
        scan = _privacy_scan([spec.root for spec in PACKETS] + [temporary])
        if not scan["passed"] or scan["file_count"] != expected_scan_count:
            raise PublicEvidenceIndexError(f"unified public evidence privacy scan failed: {scan}")
        # The receipt has no dynamic finding content; rewrite it only if its
        # deterministic scan summary differs, then refresh index/manifest binds.
        receipt = _read_json(temporary / PRIVACY_RECEIPT_NAME)
        receipt["scanned_file_count"] = scan["file_count"]
        receipt["findings"] = scan["findings"]
        receipt["passed"] = scan["passed"]
        _write_json(temporary / PRIVACY_RECEIPT_NAME, receipt)
        index = _index_document(entries, _sha_file(temporary / PRIVACY_RECEIPT_NAME))
        _write_json(temporary / INDEX_NAME, index)
        _write_json(temporary / MANIFEST_NAME, _manifest(temporary, index))
        final_scan = _privacy_scan([spec.root for spec in PACKETS] + [temporary])
        if not final_scan["passed"] or final_scan["file_count"] != receipt["scanned_file_count"]:
            raise PublicEvidenceIndexError(f"final unified privacy scan failed: {final_scan}")
        os.replace(temporary, output)
        return _verify_output(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = _verify_output(args.output) if args.verify else build(output=args.output)
    except Exception as exc:
        print(f"public evidence index failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
