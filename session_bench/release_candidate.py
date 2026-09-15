"""Assemble an evidence-bound five-configuration v1 release candidate.

The release candidate is deliberately a consumer of *sanitized public packets*.
It never discovers a native root, opens a vendor database, launches a vendor
executable, or uses a score projection as its source of truth.  Each packet's
31-cell score is reconstructed with :func:`score_public_run`, then aggregated
with :func:`aggregate_public_configuration` before the existing authoritative
report and recommendation layers are called.

An ordinary local replay receipt is not enough to clear the release gate.  A
separate operator or environment must provide one independently hashed receipt
per configuration.  This module validates that receipt and keeps its metadata
in the candidate evidence index; it does not manufacture an independent badge
from a local recheck.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import date
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from .configuration_bundle import (
    ConfigurationBundle,
    ConfigurationBundleError,
    canonical_json,
    canonical_sha256,
    validate_configuration_bundle,
)
from .recommendations import RecommendationOutputs, build_recommendation_outputs
from .v1_public_score import (
    PUBLIC_CATEGORY_POINTS,
    PUBLIC_METRICS,
    TARGET_CONFIGURATIONS,
    PublicConfigurationScore,
    PublicRunScore,
    aggregate_public_configuration,
    display_decimal,
    score_public_run,
)
from .v1_report import build_authoritative_report, render_authoritative_report


RELEASE_CANDIDATE_SCHEMA_VERSION = "session-bench-v1-release-candidate-v1"
REVIEW_CANDIDATE_SCHEMA_VERSION = "session-bench-v1-unpublished-review-candidate-v1"
EVIDENCE_INDEX_SCHEMA_VERSION = "session-bench-v1-evidence-index-v1"
# This is the schema emitted by the standalone public verifier.  The release
# builder deliberately does not accept the older local helper schema: that
# schema let a caller serialize a set of all-true claims without showing what
# the verifier actually recomputed.
INDEPENDENT_RECEIPT_SCHEMA_VERSION = "session-bench-independent-reproduction-receipt-v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "session-bench-v1-release-artifact-manifest-v1"

REPETITIONS = (1, 2, 3)

_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:Users|private|tmp|var|home)(?:/|$)")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET_RE = re.compile(
    r"(?:bearer\s+|authorization\s*[:=]|api[_-]?key\s*[:=|]|"
    r"access[_-]?token\s*[:=|]|refresh[_-]?token\s*[:=|]|"
    r"password\s*[:=|]|private[_-]?key\s*[:=|]|cookie\s*[:=|])",
    re.I,
)
_FORBIDDEN_SUFFIXES = (
    ".sqlite",
    ".sqlite-wal",
    ".sqlite-shm",
    ".db",
    ".db-wal",
    ".db-shm",
    ".jsonl",
)


class ReleaseCandidateError(ValueError):
    """Raised when sanitized evidence cannot clear the release-candidate gate."""


def _fail(message: str) -> None:
    raise ReleaseCandidateError(message)


def _strict_json(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(f"{path}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        _fail(f"{path}: non-finite JSON number {value}")

    try:
        return json.loads(path.read_bytes(), object_pairs_hook=pairs, parse_constant=constant)
    except ReleaseCandidateError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read JSON {path}: {exc}")


def _object(path: Path) -> dict[str, Any]:
    value = _strict_json(path)
    if not isinstance(value, dict):
        _fail(f"{path} must contain one JSON object")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return canonical_json(value)
    except ConfigurationBundleError as exc:
        raise ReleaseCandidateError(str(exc)) from exc


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    try:
        return _sha_bytes(path.read_bytes())
    except OSError as exc:
        _fail(f"cannot hash packet file {path}: {exc}")


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label} must be a non-empty trimmed string")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        _fail(f"{label}: wrong fields (missing={sorted(expected - actual)}, extra={sorted(actual - expected)})")


def _safe_relative(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"{label} must be a safe relative POSIX path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts:
        _fail(f"{label} must be a safe relative path")
    return path


def _assert_packet_root(root: Path) -> Path:
    supplied = Path(root)
    if supplied.is_symlink() or not supplied.is_dir():
        _fail(f"packet is not an ordinary directory: {supplied}")
    root = supplied.resolve(strict=True)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            _fail(f"packet contains a symlink: {path.relative_to(root)}")
        if path.is_file() and path.name.endswith(_FORBIDDEN_SUFFIXES):
            _fail(f"packet contains a raw native or transcript file: {path.relative_to(root)}")
    return root


def _read_packet_json(root: Path, relative: str) -> dict[str, Any]:
    path = root / _safe_relative(relative, "packet path")
    try:
        path.relative_to(root)
    except ValueError:
        _fail(f"packet path escaped its root: {relative}")
    if path.is_symlink() or not path.is_file():
        _fail(f"required packet file is missing: {relative}")
    return _object(path)


def _privacy_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == "README.md":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"{relative}:non_utf8_or_unreadable")
            continue
        for marker, pattern in (
            ("absolute_local_path", _LOCAL_PATH_RE),
            ("email_address", _EMAIL_RE),
            ("credential_assignment", _SECRET_RE),
        ):
            if pattern.search(text):
                findings.append(f"{relative}:{marker}")
    return findings


def _privacy_findings_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{path.name}:non_utf8_or_unreadable"]
    findings: list[str] = []
    for marker, pattern in (
        ("absolute_local_path", _LOCAL_PATH_RE),
        ("email_address", _EMAIL_RE),
        ("credential_assignment", _SECRET_RE),
    ):
        if pattern.search(text):
            findings.append(f"{path.name}:{marker}")
    return findings


def _verify_manifest(root: Path, bundle: ConfigurationBundle) -> str:
    """Verify either the generic or OpenCode public packet manifest."""

    path = root / "bundle-manifest.json"
    manifest = _object(path)
    if "schema_version" not in manifest or "configuration_id" not in manifest or "files" not in manifest or "content_sha256" not in manifest:
        _fail(f"{path}: incomplete public packet manifest")
    if manifest["configuration_id"] != bundle.configuration_id:
        _fail(f"{path}: configuration ID does not match configuration bundle")
    if manifest.get("immutable") is not None and manifest["immutable"] is not True:
        _fail(f"{path}: manifest is not immutable")
    if manifest.get("public") is not None and manifest["public"] is not True:
        _fail(f"{path}: manifest is not public")
    manifest_bundle_sha = manifest.get("bundle_sha256", manifest.get("configuration_bundle_sha256"))
    if manifest_bundle_sha is not None and manifest_bundle_sha != bundle.bundle_sha256:
        _fail(f"{path}: manifest does not bind the configuration bundle")
    manifest_bundle_id = manifest.get("bundle_id", manifest.get("id"))
    if manifest_bundle_id is not None and manifest_bundle_id != bundle.bundle_id:
        _fail(f"{path}: manifest does not bind the configuration bundle ID")
    if "result_ids" in manifest and sorted(manifest["result_ids"]) != list(bundle.result_ids):
        _fail(f"{path}: manifest result IDs do not match the bundle")
    core = {key: value for key, value in manifest.items() if key != "content_sha256"}
    if canonical_sha256(core) != _digest(manifest["content_sha256"], f"{path}.content_sha256"):
        _fail(f"{path}: manifest content hash mismatch")
    raw_files = manifest["files"]
    if not isinstance(raw_files, list):
        _fail(f"{path}: manifest files must be an array")
    listed: set[str] = set()
    # The OpenCode public packet carries a top-level source-denied audit
    # receipt alongside its manifest. That receipt is intentionally excluded
    # from the bundle content hash by the producer, while still being scanned
    # for privacy and validated through the per-run controls below. Generic
    # packets have no such sidecar; accepting arbitrary unlisted files would
    # make the manifest boundary meaningless.
    excluded_names = {"bundle-manifest.json", "README.md"}
    if manifest.get("schema_version") == "session-bench-opencode-public-bundle-manifest-v1":
        excluded_names.add("source-denied-reproduction-receipt.json")
    for index, item in enumerate(raw_files):
        label = f"{path}.files[{index}]"
        if not isinstance(item, Mapping):
            _fail(f"{label} must be an object")
        _strict_keys(dict(item), {"path", "sha256", "size_bytes"}, label)
        relative = _safe_relative(item["path"], f"{label}.path").as_posix()
        if relative in listed or Path(relative).name in excluded_names:
            _fail(f"{label}.path is duplicated or excluded")
        listed.add(relative)
        digest = _digest(item["sha256"], f"{label}.sha256")
        size = item["size_bytes"]
        if type(size) is not int or size < 0:
            _fail(f"{label}.size_bytes must be a nonnegative integer")
        target = root / relative
        if target.is_symlink() or not target.is_file():
            _fail(f"{label}.path is missing from the packet")
        if target.stat().st_size != size or _sha_file(target) != digest:
            _fail(f"{label}.path hash or size does not match the packet")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in excluded_names
    }
    if listed != actual:
        _fail(f"{path}: manifest file set differs from packet")
    return _sha_file(path)


def _number_close(left: Any, right: Fraction | None, label: str) -> None:
    if right is None:
        if left is not None:
            _fail(f"{label}: packet claims a value for an unresolved scorer cell")
        return
    if isinstance(left, bool) or not isinstance(left, (int, float)) or (isinstance(left, float) and not math.isfinite(left)):
        _fail(f"{label} is not a finite number")
    if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12):
        _fail(f"{label} differs from the recomputed scorer value")


def _verify_result_projection(result: Mapping[str, Any], score: PublicRunScore, label: str) -> None:
    if result.get("resolved") is not True:
        _fail(f"{label} is not marked resolved")
    if result.get("metric_ids") != list(PUBLIC_METRICS):
        _fail(f"{label}.metric_ids does not use the frozen 31-cell order")
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != set(PUBLIC_METRICS):
        _fail(f"{label}.metrics does not contain the frozen 31-cell set")
    for metric_id in PUBLIC_METRICS:
        _number_close(metrics[metric_id], score.metrics[metric_id], f"{label}.metrics.{metric_id}")
    categories = result.get("categories")
    if not isinstance(categories, Mapping) or set(categories) != set(PUBLIC_CATEGORY_POINTS):
        _fail(f"{label}.categories does not contain the five public categories")
    for category in PUBLIC_CATEGORY_POINTS:
        value = categories[category]
        if isinstance(value, Mapping):
            value = value.get("score")
        _number_close(value, score.categories[category].score, f"{label}.categories.{category}")
    _number_close(result.get("overall"), score.overall, f"{label}.overall")


def _verify_score_projection(root: Path, repetition: int, score: PublicRunScore) -> None:
    path = root / "runs" / str(repetition) / "score-input" / "public-score.json"
    document = _object(path)
    if document.get("published") is True or document.get("independent_reproduction") is True:
        _fail(f"{path}: local score projection claims publication or independence")
    projection = document.get("score", document)
    if not isinstance(projection, Mapping):
        _fail(f"{path}: score projection is not an object")
    # The generic packets wrap the scorer output; OpenCode stores it directly.
    if projection != score.display():
        _fail(f"{path}: score projection differs from the recomputed scorer output")


def _verify_bundle_run_projection(bundle: ConfigurationBundle, repetition: int, score: PublicRunScore) -> None:
    raw = next((item for item in bundle.document["runs"] if item.get("repetition") == repetition), None)
    if not isinstance(raw, Mapping):
        _fail(f"{bundle.configuration_id} bundle is missing repetition {repetition}")
    for field, expected in (
        ("configuration_id", score.configuration_id),
        ("run_id", score.run_id),
        ("result_id", score.result_id),
        ("evaluation_id", score.survival_evaluation_id),
    ):
        if raw.get(field) != expected:
            _fail(f"{bundle.configuration_id} repetition {repetition} bundle {field} is not scorer-bound")
    result = raw.get("result")
    if not isinstance(result, Mapping):
        _fail(f"{bundle.configuration_id} repetition {repetition} bundle result is missing")
    _verify_result_projection(result, score, f"{bundle.configuration_id} repetition {repetition} bundle result")


def _verify_generic_controls(root: Path, repetition: int, configuration_id: str) -> None:
    run_root = root / "runs" / str(repetition)
    replay = _object(run_root / "replay-receipt.json")
    for field in ("verified", "offline", "original_root_denied", "vendor_executable_denied", "network_denied"):
        if replay.get(field) is not True:
            _fail(f"{configuration_id} repetition {repetition} replay receipt lacks {field}")
    if replay.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} replay receipt claims independence")
    equality = _object(run_root / "canonical-equality-receipt.json")
    if equality.get("verified") is not True or equality.get("copied_decode_equal") is not True:
        _fail(f"{configuration_id} repetition {repetition} canonical equality is not verified")
    if equality.get("ordinary_sha256") != equality.get("isolated_sha256"):
        _fail(f"{configuration_id} repetition {repetition} canonical equality hashes differ")
    if equality.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} equality receipt claims independence")
    loss = _object(run_root / "loss-receipt.json")
    for field in ("verified", "response_loss_detected", "actions_preserved", "results_preserved"):
        if loss.get(field) is not True:
            _fail(f"{configuration_id} repetition {repetition} selected-loss control lacks {field}")
    if loss.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} loss receipt claims independence")
    privacy = _object(run_root / "privacy-receipt.json")
    for field in (
        "verified", "absolute_paths_absent", "account_data_absent", "credentials_absent",
        "personal_history_absent", "raw_native_withheld", "raw_transcript_withheld", "raw_sqlite_withheld",
    ):
        if privacy.get(field) is not True:
            _fail(f"{configuration_id} repetition {repetition} privacy receipt lacks {field}")
    if privacy.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} privacy receipt claims independence")


def _verify_opencode_controls(root: Path, repetition: int, configuration_id: str) -> None:
    run_root = root / "runs" / str(repetition)
    replay = _object(run_root / "source-denied-replay.json")
    if replay.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} replay receipt claims independence")
    denial = replay.get("denial")
    if not isinstance(denial, Mapping) or any(denial.get(key) != "denied" for key in ("network", "original_package_read", "original_runtime_read", "vendor_executable_read")):
        _fail(f"{configuration_id} repetition {repetition} source-denied replay controls are incomplete")
    canonical = replay.get("canonical_decode")
    if not isinstance(canonical, Mapping) or canonical.get("equal") is not True or canonical.get("ordinary_sha256") != canonical.get("isolated_sha256"):
        _fail(f"{configuration_id} repetition {repetition} OpenCode canonical equality is not verified")
    selected = replay.get("selected_loss_control")
    if not isinstance(selected, Mapping) or selected.get("independent_reproduction") is not False:
        _fail(f"{configuration_id} repetition {repetition} OpenCode selected-loss receipt is missing")
    if not isinstance(selected.get("intact_correct"), int) or not isinstance(selected.get("damaged_correct"), int) or selected["damaged_correct"] >= selected["intact_correct"]:
        _fail(f"{configuration_id} repetition {repetition} OpenCode selected-loss control did not reduce the selected fact")
    correction = _object(run_root / "semantic" / "correction-receipt.json")
    if any(correction.get(key) is not True for key in ("canonical_decode_equal", "measurement_equal", "survival_score_equal")):
        _fail(f"{configuration_id} repetition {repetition} correction equality is not verified")
    portability = _object(run_root / "semantic" / "portability-receipt.json")
    if any(portability.get(key) is not True for key in ("complete_root", "companions_present", "isolated_decode", "canonical_equality")):
        _fail(f"{configuration_id} repetition {repetition} portability receipt is incomplete")
    redaction = _object(run_root / "semantic" / "redaction-receipt.json")
    if redaction.get("raw_sqlite_withheld") is not True or redaction.get("raw_runtime_withheld") is not True:
        _fail(f"{configuration_id} repetition {repetition} redaction receipt does not withhold private native/runtime data")


@dataclass(frozen=True)
class IndependentReproductionReceipt:
    """A receipt issued by a second operator or environment."""

    id: str
    sha256: str
    configuration_id: str
    bundle_sha256: str
    result_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    operator_id: str
    environment_id: str
    verified: bool
    independent_reproduction: bool
    offline: bool
    original_root_denied: bool
    vendor_executable_denied: bool
    network_denied: bool
    canonical_equality: bool
    score_recomputed: bool
    selected_loss_verified: bool
    privacy_verified: bool

    def preimage(self) -> dict[str, Any]:
        return {
            "schema_version": INDEPENDENT_RECEIPT_SCHEMA_VERSION,
            "id": self.id,
            "configuration_id": self.configuration_id,
            "bundle_sha256": self.bundle_sha256,
            "result_ids": list(self.result_ids),
            "run_ids": list(self.run_ids),
            "operator_id": self.operator_id,
            "environment_id": self.environment_id,
            "verified": True,
            "independent_reproduction": True,
            "offline": True,
            "original_root_denied": True,
            "vendor_executable_denied": True,
            "network_denied": True,
            "canonical_equality": True,
            "score_recomputed": True,
            "selected_loss_verified": True,
            "privacy_verified": True,
        }

    def display(self) -> dict[str, Any]:
        return {**self.preimage(), "sha256": self.sha256}


def _parse_independent_receipt(path: Path, bundle: ConfigurationBundle, manifest_sha256: str) -> IndependentReproductionReceipt:
    document = _object(path)
    expected = {
        "schema_version", "id", "sha256", "configuration_id", "bundle_sha256",
        "result_ids", "run_ids", "operator_id", "environment_id", "verified",
        "independent_reproduction", "offline", "original_root_denied",
        "vendor_executable_denied", "network_denied", "canonical_equality",
        "score_recomputed", "selected_loss_verified", "privacy_verified",
    }
    _strict_keys(document, expected, "independent reproduction receipt")
    if document["schema_version"] != INDEPENDENT_RECEIPT_SCHEMA_VERSION:
        _fail(f"{path}: unsupported independent receipt schema")
    if document["configuration_id"] != bundle.configuration_id or document["bundle_sha256"] != bundle.bundle_sha256:
        _fail(f"{path}: independent receipt is bound to another configuration bundle")
    result_ids = tuple(document["result_ids"])
    run_ids = tuple(document["run_ids"])
    if len(result_ids) != 3 or len(set(result_ids)) != 3 or any(not isinstance(value, str) or not value for value in result_ids):
        _fail(f"{path}: independent receipt result IDs are incomplete")
    expected_result_ids = tuple(sorted(bundle.result_ids))
    if tuple(sorted(result_ids)) != expected_result_ids:
        _fail(f"{path}: independent receipt result IDs do not bind the packet")
    expected_run_ids = tuple(sorted(run["run_id"] for run in bundle.document["runs"]))
    if len(run_ids) != 3 or len(set(run_ids)) != 3 or tuple(sorted(run_ids)) != expected_run_ids:
        _fail(f"{path}: independent receipt run IDs do not bind the packet")
    for field in (
        "verified", "independent_reproduction", "offline", "original_root_denied",
        "vendor_executable_denied", "network_denied", "canonical_equality",
        "score_recomputed", "selected_loss_verified", "privacy_verified",
    ):
        if document[field] is not True:
            _fail(f"{path}: independent receipt {field} must be true")
    operator_id = _text(document["operator_id"], f"{path}.operator_id")
    environment_id = _text(document["environment_id"], f"{path}.environment_id")
    if _LOCAL_PATH_RE.search(operator_id + " " + environment_id) or _EMAIL_RE.search(operator_id + " " + environment_id):
        _fail(f"{path}: independent receipt operator/environment identity is not sanitized")
    sha256 = _digest(document["sha256"], f"{path}.sha256")
    preimage = dict(document)
    del preimage["sha256"]
    if canonical_sha256(preimage) != sha256:
        _fail(f"{path}: independent receipt hash does not match its canonical contents")
    return IndependentReproductionReceipt(
        id=_text(document["id"], f"{path}.id"),
        sha256=sha256,
        configuration_id=bundle.configuration_id,
        bundle_sha256=bundle.bundle_sha256,
        result_ids=result_ids,
        run_ids=run_ids,
        operator_id=operator_id,
        environment_id=environment_id,
        verified=True,
        independent_reproduction=True,
        offline=True,
        original_root_denied=True,
        vendor_executable_denied=True,
        network_denied=True,
        canonical_equality=True,
        score_recomputed=True,
        selected_loss_verified=True,
        privacy_verified=True,
    )


@dataclass(frozen=True)
class PublicConfigurationPacket:
    root: Path
    configuration_id: str
    bundle: ConfigurationBundle
    runs: tuple[PublicRunScore, ...]
    score: PublicConfigurationScore
    manifest_sha256: str
    privacy_findings: tuple[str, ...]


def load_public_configuration_packet(root: Path | str) -> PublicConfigurationPacket:
    """Load one sanitized packet and recompute its typed configuration score."""

    packet_root = _assert_packet_root(Path(root))
    bundle_document = _read_packet_json(packet_root, "configuration-bundle.json")
    try:
        bundle = validate_configuration_bundle(bundle_document)
    except (TypeError, ValueError, ConfigurationBundleError) as exc:
        raise ReleaseCandidateError(f"{packet_root}: configuration bundle is invalid") from exc
    if bundle.configuration_id not in TARGET_CONFIGURATIONS:
        _fail(f"{packet_root}: configuration is outside the fixed v1 cohort")
    evidence = _read_packet_json(packet_root, "configuration-evidence.json")
    if evidence != bundle.report_input:
        _fail(f"{packet_root}: configuration evidence does not equal the bundle binding")
    manifest_sha256 = _verify_manifest(packet_root, bundle)
    findings = _privacy_findings(packet_root)
    if findings:
        _fail(f"{packet_root}: public packet privacy scan failed: {findings}")

    scores: list[PublicRunScore] = []
    for repetition in REPETITIONS:
        survival = _read_packet_json(packet_root, f"runs/{repetition}/score-input/survival-evidence.json")
        profile = _read_packet_json(packet_root, f"runs/{repetition}/score-input/format-evidence.json")
        try:
            run_score = score_public_run(survival, profile)
        except (TypeError, ValueError) as exc:
            raise ReleaseCandidateError(f"{packet_root}: repetition {repetition} cannot be recomputed from public score inputs") from exc
        if (
            run_score.configuration_id != bundle.configuration_id
            or run_score.repetition != repetition
            or not run_score.rankable
            or len(run_score.metrics) != len(PUBLIC_METRICS)
            or any(value is None for value in run_score.metrics.values())
        ):
            _fail(f"{packet_root}: repetition {repetition} is not a complete resolved 31-cell run")
        _verify_score_projection(packet_root, repetition, run_score)
        _verify_bundle_run_projection(bundle, repetition, run_score)
        if bundle.configuration_id == "opencode-cli":
            _verify_opencode_controls(packet_root, repetition, bundle.configuration_id)
        else:
            _verify_generic_controls(packet_root, repetition, bundle.configuration_id)
        scores.append(run_score)

    try:
        aggregate = aggregate_public_configuration(scores, configuration_evidence=evidence)
    except (TypeError, ValueError) as exc:
        raise ReleaseCandidateError(f"{packet_root}: typed three-run aggregate is invalid") from exc
    if not aggregate.rankable or aggregate.verification != "fully_reproduced":
        _fail(f"{packet_root}: aggregate did not clear local evidence gates: {aggregate.blockers}")
    return PublicConfigurationPacket(packet_root, bundle.configuration_id, bundle, tuple(scores), aggregate, manifest_sha256, tuple(findings))


def _receipt_path(packet_root: Path, receipts_root: Path | None, configuration_id: str) -> Path:
    candidates: list[Path] = []
    if receipts_root is not None:
        candidates.extend((
            receipts_root / f"{configuration_id}.json",
            receipts_root / configuration_id / "independent-reproduction-receipt.json",
            receipts_root / configuration_id / "receipt.json",
        ))
    candidates.append(packet_root / "independent-reproduction-receipt.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    _fail(f"{configuration_id}: independent reproduction receipt is missing")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _candidate_evidence_index(
    packets: Sequence[PublicConfigurationPacket],
    receipts: Mapping[str, IndependentReproductionReceipt],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    rows = []
    report_rows = {row["configuration_id"]: row for row in report["configurations"]}
    for packet in packets:
        receipt = receipts[packet.configuration_id]
        score_row = report_rows[packet.configuration_id]
        rows.append({
            "configuration_id": packet.configuration_id,
            "packet_label": packet.configuration_id,
            "packet_manifest_sha256": packet.manifest_sha256,
            "bundle_id": packet.bundle.bundle_id,
            "bundle_sha256": packet.bundle.bundle_sha256,
            "local_reproduction_receipt_id": packet.bundle.receipt_id,
            "independent_reproduction_receipt_id": receipt.id,
            "independent_reproduction_receipt_sha256": receipt.sha256,
            "overall": score_row["overall_points"],
            "range": score_row["overall_range_points"],
            "rank": score_row["rank"],
            "run_ids": [run.run_id for run in packet.runs],
            "result_ids": [run.result_id for run in packet.runs],
            "evaluation_ids": [run.survival_evaluation_id for run in packet.runs],
            "metric_counts": [len(run.metrics) for run in packet.runs],
            "verification": score_row["verification"]["state"],
        })
    return {
        "schema_version": EVIDENCE_INDEX_SCHEMA_VERSION,
        "generated_at": report["generated_at"],
        "cohort": {
            "required_configuration_ids": list(TARGET_CONFIGURATIONS),
            "configuration_ids": [packet.configuration_id for packet in packets],
            "exact_five_configuration_set": True,
            "all_local_bundles_verified": True,
            "all_independent_receipts_verified": True,
            "leaderboard_eligible": report["cohort"]["leaderboard_eligible"],
        },
        "configurations": rows,
    }


def _review_candidate_evidence_index(
    packets: Sequence[PublicConfigurationPacket],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Index the verified local packet chain without implying independent replay."""

    report_rows = {row["configuration_id"]: row for row in report["configurations"]}
    rows = []
    for packet in packets:
        score_row = report_rows[packet.configuration_id]
        rows.append({
            "configuration_id": packet.configuration_id,
            "packet_manifest_sha256": packet.manifest_sha256,
            "bundle_id": packet.bundle.bundle_id,
            "bundle_sha256": packet.bundle.bundle_sha256,
            "local_reproduction_receipt_id": packet.bundle.receipt_id,
            "independent_reproduction_receipt_id": None,
            "overall": score_row["overall_points"],
            "range": score_row["overall_range_points"],
            "review_only_rank": score_row["rank"],
            "run_ids": [run.run_id for run in packet.runs],
            "result_ids": [run.result_id for run in packet.runs],
            "evaluation_ids": [run.survival_evaluation_id for run in packet.runs],
            "metric_counts": [len(run.metrics) for run in packet.runs],
            "local_verification": "Locally reproduced",
            "verification": "Locally reproduced",
            "publication_status": "unpublished",
            "independent_native_reproduction": False,
        })
    return {
        "schema_version": EVIDENCE_INDEX_SCHEMA_VERSION,
        "generated_at": report["generated_at"],
        "status": "unpublished_review_candidate",
        "verification": "Locally reproduced",
        "publication_status": "unpublished",
        "independent_native_reproduction": False,
        "cohort": {
            "required_configuration_ids": list(TARGET_CONFIGURATIONS),
            "configuration_ids": [packet.configuration_id for packet in packets],
            "exact_five_configuration_set": True,
            "all_local_bundles_verified": True,
            "all_independent_receipts_verified": False,
            "leaderboard_eligible": False,
            "review_only_ranks_present": True,
        },
        "configurations": rows,
    }


def _leaderboard_csv(report: Mapping[str, Any], *, review_candidate: bool = False) -> str:
    """Serialize the leaderboard with an explicit provenance boundary.

    The authoritative release path keeps its historical CSV schema and
    verification values. A detached review candidate gets additional columns
    so opening the CSV alone cannot turn local packet replay into a claim of
    independent native reproduction or publication.
    """

    columns = [
        "rank", "configuration_id", "name", "surface", "overall", "range",
        "record_fidelity", "causality_context", "usage_attribution",
        "portability_openness", "durability_signal", "verification",
    ]
    if review_candidate:
        columns.extend(("publication_status", "independent_native_reproduction"))
    rows = []
    for row in report["configurations"]:
        output = {
            "rank": "" if row["rank"] is None else row["rank"],
            "configuration_id": row["configuration_id"],
            "name": row["name"],
            "surface": row["surface"],
            "overall": "" if row["overall_points"] is None else row["overall_points"],
            "range": "" if row["overall_range_points"] is None else "–".join(str(value) for value in row["overall_range_points"]),
            **{category: row["categories"][category]["points"] for category in PUBLIC_CATEGORY_POINTS},
            "verification": "Locally reproduced" if review_candidate else row["verification"]["state"],
        }
        if review_candidate:
            output.update({
                "publication_status": "unpublished",
                "independent_native_reproduction": "false",
            })
        rows.append(output)
    from io import StringIO
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _artifact_manifest(root: Path) -> dict[str, Any]:
    files = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name == "artifact-manifest.json" or not path.is_file():
            continue
        payload = path.read_bytes()
        files.append({"path": path.name, "sha256": _sha_bytes(payload), "size_bytes": len(payload)})
    core = {"schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION, "files": files}
    return {**core, "content_sha256": canonical_sha256(core)}


def assemble_release_candidate(
    packet_roots: Mapping[str, Path | str],
    *,
    receipts_root: Path | str | None,
    output_dir: Path | str,
    generated_at: str,
) -> dict[str, Any]:
    """Build the deterministic five-config candidate from sanitized inputs.

    The output directory must not already exist.  This protects an existing
    candidate from an accidental overwrite and keeps every invocation's output
    independently reviewable.
    """

    if not isinstance(generated_at, str) or not generated_at.strip():
        _fail("generated_at must be a non-empty explicit timestamp")
    if set(packet_roots) != set(TARGET_CONFIGURATIONS) or len(packet_roots) != len(TARGET_CONFIGURATIONS):
        _fail(f"release candidate requires the exact five packet IDs: {list(TARGET_CONFIGURATIONS)}")
    ordered_packets = tuple(load_public_configuration_packet(packet_roots[configuration_id]) for configuration_id in TARGET_CONFIGURATIONS)
    if {packet.configuration_id for packet in ordered_packets} != set(TARGET_CONFIGURATIONS):
        _fail("loaded packet IDs do not form the exact five-config cohort")

    receipts_base = None if receipts_root is None else _assert_packet_root(Path(receipts_root))
    receipts: dict[str, IndependentReproductionReceipt] = {}
    for packet in ordered_packets:
        path = _receipt_path(packet.root, receipts_base, packet.configuration_id)
        # Receipts are sanitized JSON input as well.  The receipt file itself
        # is the only file read from its external root.
        if path.is_symlink() or not path.is_file() or path.name.endswith(_FORBIDDEN_SUFFIXES):
            _fail(f"{packet.configuration_id}: independent receipt path is unsafe")
        findings = _privacy_findings_file(path)
        if findings:
            _fail(f"{packet.configuration_id}: independent receipt privacy scan failed: {findings}")
        receipts[packet.configuration_id] = _parse_independent_receipt(path, packet.bundle, packet.manifest_sha256)

    try:
        recommendations: RecommendationOutputs = build_recommendation_outputs([packet.score for packet in ordered_packets])
        report = build_authoritative_report(
            [packet.score for packet in ordered_packets],
            recommendations,
            generated_at=generated_at,
            constructed=False,
            scope=(
                "Five fixed Session-Bench v1 configurations: Codex CLI, Codex Desktop, "
                "Claude Code CLI, Claude Desktop Code (Local), and OpenCode CLI. "
                "Every ranked row has three evidence-bound 31-metric runs and a separately verified independent reproduction receipt."
            ),
            method=(
                "Each score is recomputed from sanitized survival and format evidence with the shared 31-cell scorer. "
                "The candidate then requires immutable bundle bindings, copied-artifact controls, privacy receipts, "
                "and one independently hashed offline reproduction receipt per configuration."
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseCandidateError(f"typed report assembly failed: {exc}") from exc
    if report["cohort"]["leaderboard_eligible"] is not True:
        _fail("authoritative report did not clear the exact-five leaderboard gate")

    output = Path(output_dir).resolve(strict=False)
    if output.exists() or output.is_symlink():
        _fail(f"refusing to overwrite existing release-candidate output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".session-bench-v1-release-candidate-", dir=output.parent))
    try:
        render_authoritative_report(report, temporary)
        (temporary / "leaderboard.csv").write_text(_leaderboard_csv(report), encoding="utf-8")
        evidence_index = _candidate_evidence_index(ordered_packets, receipts, report)
        _write_json(temporary / "evidence-index.json", evidence_index)
        candidate = {
            "schema_version": RELEASE_CANDIDATE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "status": "review_candidate",
            "publication": {"published": False, "owner_release_instruction_required": True},
            "gates": {
                "exact_five_configuration_set": True,
                "three_runs_per_configuration": True,
                "31_metrics_per_run": True,
                "local_bundle_reproduction": True,
                "independent_reproduction_receipts": True,
                "privacy_scan": True,
                "leaderboard_eligible": True,
            },
            "configuration_ids": list(TARGET_CONFIGURATIONS),
            "report_sha256": _sha_file(temporary / "report.json"),
            "evidence_index_sha256": _sha_file(temporary / "evidence-index.json"),
            "independent_receipts": [
                {
                    "configuration_id": configuration_id,
                    "id": receipts[configuration_id].id,
                    "sha256": receipts[configuration_id].sha256,
                    "bundle_sha256": receipts[configuration_id].bundle_sha256,
                }
                for configuration_id in TARGET_CONFIGURATIONS
            ],
        }
        _write_json(temporary / "release-candidate.json", candidate)
        _write_json(temporary / "artifact-manifest.json", _artifact_manifest(temporary))
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "output": str(output),
        "configuration_ids": list(TARGET_CONFIGURATIONS),
        "leaderboard_eligible": True,
        "report_sha256": _sha_file(output / "report.json"),
        "artifact_manifest_sha256": _sha_file(output / "artifact-manifest.json"),
    }


def assemble_review_candidate(
    packet_roots: Mapping[str, Path | str],
    *,
    output_dir: Path | str,
    generated_at: str,
) -> dict[str, Any]:
    """Build a ranked local candidate while keeping publication fail-closed."""

    if not isinstance(generated_at, str) or not generated_at.strip():
        _fail("generated_at must be a non-empty explicit timestamp")
    if set(packet_roots) != set(TARGET_CONFIGURATIONS) or len(packet_roots) != len(TARGET_CONFIGURATIONS):
        _fail(f"review candidate requires the exact five packet IDs: {list(TARGET_CONFIGURATIONS)}")
    packets = tuple(load_public_configuration_packet(packet_roots[item]) for item in TARGET_CONFIGURATIONS)
    if {packet.configuration_id for packet in packets} != set(TARGET_CONFIGURATIONS):
        _fail("loaded packet IDs do not form the exact five-config cohort")

    try:
        recommendations = build_recommendation_outputs([packet.score for packet in packets])
        report = build_authoritative_report(
            [packet.score for packet in packets],
            recommendations,
            generated_at=generated_at,
            constructed=False,
            data_status="UNPUBLISHED LOCAL EVIDENCE",
            scope=(
                "Five fixed Session-Bench v1 configurations scored from 15 existing sanitized runs. "
                "The displayed ranks are an unpublished local review candidate; independent native reproduction remains pending."
            ),
            method=(
                "Each score is recomputed from sanitized survival and format evidence with the shared 31-cell scorer. "
                "Local packet manifests, copied-artifact controls, bundle receipts, and privacy boundaries are verified; "
                "this candidate does not claim independent native replay."
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseCandidateError(f"typed review-candidate assembly failed: {exc}") from exc
    if report["cohort"]["leaderboard_eligible"] is not True:
        _fail("local report did not contain the complete five-configuration score cohort")

    output = Path(output_dir).resolve(strict=False)
    if output.exists() or output.is_symlink():
        _fail(f"refusing to overwrite existing review-candidate output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".session-bench-v1-review-candidate-", dir=output.parent))
    try:
        render_authoritative_report(report, temporary)
        (temporary / "leaderboard.csv").write_text(_leaderboard_csv(report, review_candidate=True), encoding="utf-8")
        evidence_index = _review_candidate_evidence_index(packets, report)
        _write_json(temporary / "evidence-index.json", evidence_index)
        candidate = {
            "schema_version": REVIEW_CANDIDATE_SCHEMA_VERSION,
            "generated_at": generated_at,
            "status": "unpublished_review_candidate",
            "verification": "Locally reproduced",
            "publication_status": "unpublished",
            "independent_native_reproduction": False,
            "publication": {
                "published": False,
                "eligible": False,
                "owner_release_instruction_required": True,
            },
            "gates": {
                "exact_five_configuration_set": True,
                "three_runs_per_configuration": True,
                "metrics_per_run": 31,
                "local_bundle_reproduction": True,
                "independent_native_reproduction": False,
                "privacy_scan": True,
            },
            "publication_blockers": ["independent_native_reproduction_incomplete"],
            "configuration_ids": list(TARGET_CONFIGURATIONS),
            "report_sha256": _sha_file(temporary / "report.json"),
            "evidence_index_sha256": _sha_file(temporary / "evidence-index.json"),
        }
        _write_json(temporary / "review-candidate.json", candidate)
        _write_json(temporary / "artifact-manifest.json", _artifact_manifest(temporary))
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "output": str(output),
        "configuration_ids": list(TARGET_CONFIGURATIONS),
        "publication_eligible": False,
        "independent_native_reproduction": False,
        "report_sha256": _sha_file(output / "report.json"),
        "artifact_manifest_sha256": _sha_file(output / "artifact-manifest.json"),
    }


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA_VERSION",
    "EVIDENCE_INDEX_SCHEMA_VERSION",
    "INDEPENDENT_RECEIPT_SCHEMA_VERSION",
    "IndependentReproductionReceipt",
    "PublicConfigurationPacket",
    "RELEASE_CANDIDATE_SCHEMA_VERSION",
    "REVIEW_CANDIDATE_SCHEMA_VERSION",
    "ReleaseCandidateError",
    "assemble_release_candidate",
    "assemble_review_candidate",
    "load_public_configuration_packet",
]
