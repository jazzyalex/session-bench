#!/usr/bin/env python3
"""Independently verify a sanitized Session-Bench configuration packet.

The verifier is intentionally standalone: it uses only the Python standard
library and the packet supplied with ``--packet``.  It never imports the
repository scorer, discovers a native root, launches a vendor executable, or
uses the network.  A packet can therefore be copied together with this file
and checked from a clean temporary directory.

The public OpenCode packet contains semantic derivatives and score inputs, but
not the native SQLite family or the replay runtime.  This command recomputes
the public manifest, all 31 metric values, each run's score, the three-run
configuration aggregate, ID bindings, the checkable portion of the selected
loss receipt, and a limited privacy scan.  Claims that require withheld native
bytes remain explicitly source-attested in the emitted receipt.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import sys
from typing import Any, Callable, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = "session-bench-opencode-public-bundle-manifest-v1"
BUNDLE_SCHEMA_VERSION = "session-bench-configuration-bundle-v1"
CONFIGURATION_EVIDENCE_SCHEMA_VERSION = "session-bench-configuration-evidence-v1"
RECEIPT_SCHEMA_VERSION = "session-bench-reproduction-receipt-v1"
CONFIGURATION_SCORE_SCHEMA_VERSION = "session-bench-opencode-public-configuration-score-v1"
SURVIVAL_EVIDENCE_SCHEMA_VERSION = "session-bench-survival-evidence-v1"
FORMAT_EVIDENCE_SCHEMA_VERSION = "session-bench-format-evidence-v1"
FORMAT_PROFILE_SCHEMA_VERSION = "session-bench-format-profile-v1"
SURVIVAL_INPUT_SCHEMA_VERSION = "session-bench-survival-input-v1"

PROTOCOL_VERSION = "1.0-survival"
WORKLOAD_VERSION = "1.0-survival-workload"
RUBRIC_VERSION = "1.0-survival-rubric"

SURVIVAL_METRICS = (
    "work.submitted_turns",
    "work.visible_responses",
    "work.actions",
    "work.results",
    "work.changed_files",
    "revision.r1",
    "revision.r2",
    "revision.r1_r2_order",
    "revision.final_after_r2",
    "causal.action_result",
    "causal.turn_response",
    "attribution.model_config",
    "attribution.usage",
    "attribution.token_semantics",
    "attribution.reconciliation",
    "portable.complete_root",
    "portable.companions",
    "portable.isolated_decode",
    "portable.canonical_equality",
)

FORMAT_METRICS = (
    "broad.readable_rationale",
    "broad.thread_structure",
    "broad.standard_tools_readable",
    "broad.documented_format",
    "broad.self_contained_identity",
    "broad.declared_format_version",
    "broad.event_timestamps",
    "broad.honest_version_signal",
    "broad.observed_schema_stability",
    "broad.stable_root_location",
    "broad.naive_reader_duplicate_safety",
    "broad.classified_content_density",
)

PUBLIC_METRICS = SURVIVAL_METRICS + FORMAT_METRICS

SURVIVAL_MINIMUM_OBSERVED = {
    "work.submitted_turns": 2,
    "work.visible_responses": 2,
    "work.actions": 4,
    "work.results": 4,
    "work.changed_files": 1,
    "causal.action_result": 4,
    "causal.turn_response": 2,
    "revision.r1": 1,
    "revision.r2": 1,
    "revision.r1_r2_order": 1,
    "revision.final_after_r2": 1,
    "attribution.model_config": 2,
    "attribution.usage": 2,
    "attribution.token_semantics": 2,
    "attribution.reconciliation": 1,
    "portable.complete_root": 1,
    "portable.companions": 1,
    "portable.isolated_decode": 1,
    "portable.canonical_equality": 1,
}

RESOLVED_STATES = frozenset({"measured", "native_absent", "contradiction"})
BLOCKING_STATES = frozenset({"unresolved", "decoder_unsupported", "unexercised", "invalid_capture"})
ALLOWED_STATES = RESOLVED_STATES | BLOCKING_STATES
UNAVAILABLE_IDENTITY_VALUES = frozenset({
    "model-unreported", "unreported", "unknown", "n/a", "na", "not reported",
})

PUBLIC_CATEGORY_POINTS = {
    "record_fidelity": Fraction(30),
    "causality_context": Fraction(20),
    "usage_attribution": Fraction(15),
    "portability_openness": Fraction(20),
    "durability_signal": Fraction(15),
}

PUBLIC_METRIC_SPECS = {
    "work.submitted_turns": ("record_fidelity", Fraction(4)),
    "work.visible_responses": ("record_fidelity", Fraction(4)),
    "work.actions": ("record_fidelity", Fraction(5)),
    "work.results": ("record_fidelity", Fraction(5)),
    "work.changed_files": ("record_fidelity", Fraction(4)),
    "revision.r1": ("record_fidelity", Fraction(2)),
    "revision.r2": ("record_fidelity", Fraction(2)),
    "revision.r1_r2_order": ("record_fidelity", Fraction(2)),
    "revision.final_after_r2": ("record_fidelity", Fraction(2)),
    "causal.action_result": ("causality_context", Fraction(7)),
    "causal.turn_response": ("causality_context", Fraction(6)),
    "attribution.model_config": ("usage_attribution", Fraction(3)),
    "attribution.usage": ("usage_attribution", Fraction(5)),
    "attribution.token_semantics": ("usage_attribution", Fraction(4)),
    "attribution.reconciliation": ("usage_attribution", Fraction(3)),
    "portable.complete_root": ("portability_openness", Fraction(3)),
    "portable.companions": ("portability_openness", Fraction(2)),
    "portable.isolated_decode": ("portability_openness", Fraction(4)),
    "portable.canonical_equality": ("portability_openness", Fraction(3)),
    "broad.readable_rationale": ("causality_context", Fraction(4)),
    "broad.thread_structure": ("causality_context", Fraction(3)),
    "broad.standard_tools_readable": ("portability_openness", Fraction(2)),
    "broad.documented_format": ("portability_openness", Fraction(2)),
    "broad.self_contained_identity": ("portability_openness", Fraction(2)),
    "broad.declared_format_version": ("portability_openness", Fraction(2)),
    "broad.event_timestamps": ("durability_signal", Fraction(2)),
    "broad.honest_version_signal": ("durability_signal", Fraction(2)),
    "broad.observed_schema_stability": ("durability_signal", Fraction(3)),
    "broad.stable_root_location": ("durability_signal", Fraction(2)),
    "broad.naive_reader_duplicate_safety": ("durability_signal", Fraction(3)),
    "broad.classified_content_density": ("durability_signal", Fraction(3)),
}

WEIGHT_VECTORS = {
    "baseline": {"record_fidelity": 30, "causality_context": 20, "usage_attribution": 15, "portability_openness": 20, "durability_signal": 15},
    "equal": {"record_fidelity": 20, "causality_context": 20, "usage_attribution": 20, "portability_openness": 20, "durability_signal": 20},
    "record_fidelity_heavy": {"record_fidelity": 40, "causality_context": 15, "usage_attribution": 15, "portability_openness": 15, "durability_signal": 15},
    "portability_openness_heavy": {"record_fidelity": 25, "causality_context": 15, "usage_attribution": 15, "portability_openness": 30, "durability_signal": 15},
}

CONTENT_DENSITY_RULE = "logical-record-role-v1"
CONTENT_DENSITY_CLASSES = {
    **{role: "useful" for role in ("user_message", "correction", "assistant_message", "tool_call", "tool_result", "failure", "file_change", "plan", "explanation")},
    **{role: "unclassified" for role in ("metadata", "index", "snapshot", "session", "system")},
    "unknown": "unknown",
}

MANIFEST_EXCLUDED_NAMES = frozenset({"bundle-manifest.json", "README.md", "source-denied-reproduction-receipt.json"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:Users|private|tmp|var|home)(?:/|$)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:bearer\s+|authorization\s*[:=]|api[_-]?key\s*[:=|]|"
    r"access[_-]?token\s*[:=|]|refresh[_-]?token\s*[:=|]|"
    r"password\s*[:=|]|private[_-]?key\s*[:=|])",
    re.I,
)


class VerificationError(ValueError):
    """Raised when a public packet cannot be independently checked."""


def _fail(message: str) -> None:
    raise VerificationError(message)


def _exact_keys(value: Mapping[str, Any], expected: set[str] | frozenset[str], label: str) -> None:
    actual = set(value)
    expected_set = set(expected)
    if actual != expected_set:
        _fail(f"{label}: wrong fields (missing={sorted(expected_set - actual)}, extra={sorted(actual - expected_set)})")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(f"{label} must be a non-empty trimmed string")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label)
    if not SHA256_RE.fullmatch(value):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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
    except VerificationError:
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
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VerificationError("canonical value must be finite JSON") from exc


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha_bytes(_canonical(value))


def _fraction_text(value: Fraction | None) -> str | None:
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _display_decimal(value: Fraction | None) -> str | None:
    if value is None:
        return None
    with localcontext() as context:
        context.prec = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 4)
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")


def _number_fraction(value: Any, label: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{label} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(f"{label} must be a finite number")
    return Fraction(str(value))


def _read_digest_object(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    _exact_keys(value, {"id", "sha256"}, label)
    return {"id": _text(value["id"], f"{label}.id"), "sha256": _digest(value["sha256"], f"{label}.sha256")}


def _ordinary_files(root: Path) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        _fail(f"packet must be an ordinary directory: {root}")
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            _fail(f"packet must not contain symlinks: {path.relative_to(root)}")
        if path.is_file():
            files.append(path)
    return files


def _privacy_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in _ordinary_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"{relative}:non_utf8_or_unreadable")
            continue
        for marker, pattern in (("absolute_local_path", LOCAL_PATH_RE), ("email_address", EMAIL_RE), ("credential_assignment", SECRET_ASSIGNMENT_RE)):
            if pattern.search(value):
                findings.append(f"{relative}:{marker}")
    return findings


def verify_manifest(root: Path) -> dict[str, Any]:
    """Verify the packet file boundary and manifest/content digests."""
    manifest = _object(root / "bundle-manifest.json")
    _exact_keys(manifest, {"schema_version", "id", "configuration_id", "immutable", "public", "configuration_bundle_sha256", "result_ids", "source_packages", "files", "excluded_private", "content_sha256"}, "bundle manifest")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION or manifest["immutable"] is not True or manifest["public"] is not True:
        _fail("bundle manifest schema or flags are invalid")
    _text(manifest["id"], "bundle manifest id")
    configuration_id = _text(manifest["configuration_id"], "bundle manifest configuration_id")
    _digest(manifest["configuration_bundle_sha256"], "bundle manifest configuration_bundle_sha256")
    result_ids = _result_ids(manifest["result_ids"], "bundle manifest result_ids")
    files = manifest["files"]
    if not isinstance(files, list):
        _fail("bundle manifest files must be an array")
    listed: set[str] = set()
    for index, item in enumerate(files):
        label = f"bundle manifest files[{index}]"
        if not isinstance(item, Mapping):
            _fail(f"{label} must be an object")
        _exact_keys(item, {"path", "sha256", "size_bytes"}, label)
        relative = _text(item["path"], f"{label}.path")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            _fail(f"{label}.path is unsafe")
        if relative in listed or relative_path.name in MANIFEST_EXCLUDED_NAMES:
            _fail(f"{label}.path is duplicated or excluded")
        listed.add(relative)
        _digest(item["sha256"], f"{label}.sha256")
        if not _is_int(item["size_bytes"]) or item["size_bytes"] < 0:
            _fail(f"{label}.size_bytes must be a nonnegative integer")
        path = root / relative_path
        if not path.is_file() or path.is_symlink():
            _fail(f"bundle manifest file is missing: {relative}")
        actual = path.read_bytes()
        if _sha_bytes(actual) != item["sha256"] or len(actual) != item["size_bytes"]:
            _fail(f"bundle manifest hash mismatch: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in _ordinary_files(root)
        if path.name not in MANIFEST_EXCLUDED_NAMES
    }
    if listed != actual:
        _fail(f"bundle manifest file boundary differs from packet (missing={sorted(actual - listed)}, extra={sorted(listed - actual)})")
    core = {key: value for key, value in manifest.items() if key != "content_sha256"}
    if _canonical_sha(core) != manifest["content_sha256"]:
        _fail("bundle manifest content digest mismatch")
    return {"document": manifest, "file_count": len(files), "result_ids": result_ids, "configuration_id": configuration_id}


def _result_ids(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != 3:
        _fail(f"{label} must contain exactly three result IDs")
    normalized = tuple(sorted(_text(item, label) for item in value))
    if len(set(normalized)) != 3:
        _fail(f"{label} must contain three unique result IDs")
    return normalized


def _validate_survival_input(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    _exact_keys(value, {"schema_version", "run_id", "configuration_id", "repetition", "metrics"}, label)
    if value["schema_version"] != SURVIVAL_INPUT_SCHEMA_VERSION:
        _fail(f"{label}.schema_version is unsupported")
    run_id = _text(value["run_id"], f"{label}.run_id")
    configuration_id = _text(value["configuration_id"], f"{label}.configuration_id")
    if not _is_int(value["repetition"]) or value["repetition"] < 1:
        _fail(f"{label}.repetition must be a positive integer")
    rows = value["metrics"]
    if not isinstance(rows, list):
        _fail(f"{label}.metrics must be an array")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_label = f"{label}.metrics[{index}]"
        if not isinstance(row, Mapping):
            _fail(f"{row_label} must be an object")
        _exact_keys(row, {"id", "state", "correct", "observed_eligible", "decoded_eligible"}, row_label)
        metric_id = row["id"]
        if metric_id not in SURVIVAL_METRICS or metric_id in seen:
            _fail(f"{row_label}.id is unknown or duplicated")
        seen.add(metric_id)
        state = row["state"]
        if state == "not_applicable" or state not in ALLOWED_STATES:
            _fail(f"{row_label}.state is invalid")
        counts = (row["correct"], row["observed_eligible"], row["decoded_eligible"])
        if any(not _is_int(item) or item < 0 for item in counts):
            _fail(f"{row_label} counts must be nonnegative integers")
        correct, observed, decoded = counts
        if correct > min(observed, decoded):
            _fail(f"{row_label}.correct exceeds an eligible population")
        if state in RESOLVED_STATES and max(observed, decoded) == 0:
            _fail(f"{row_label} resolved state has no denominator")
        if state in RESOLVED_STATES and observed < SURVIVAL_MINIMUM_OBSERVED[metric_id]:
            _fail(f"{row_label} observed population is below protocol minimum")
        if state in {"native_absent", "contradiction"} and correct != 0:
            _fail(f"{row_label} {state} must have zero correct records")
        normalized.append(dict(row))
    if set(seen) != set(SURVIVAL_METRICS):
        _fail(f"{label} does not contain the exact 19-metric set")
    response_population = {
        next(item["observed_eligible"] for item in normalized if item["id"] == metric_id)
        for metric_id in ("work.visible_responses", "attribution.model_config", "attribution.usage", "attribution.token_semantics")
    }
    if len(response_population) != 1:
        _fail(f"{label} response-scoped populations differ")
    return {"schema_version": SURVIVAL_INPUT_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": value["repetition"], "metrics": normalized}


def _metric_fraction(row: Mapping[str, Any]) -> Fraction | None:
    state = row["state"]
    if state in BLOCKING_STATES:
        return None
    if state in {"native_absent", "contradiction"}:
        return Fraction(0)
    return Fraction(row["correct"], max(row["observed_eligible"], row["decoded_eligible"]))


def _id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item or item != item.strip() for item in value) or len(set(value)) != len(value):
        _fail(f"{label} must be a non-empty unique identifier list")
    return list(value)


def _complete_assertion(detail: Mapping[str, Any], passed: bool) -> dict[str, Any]:
    if detail["evidence_complete"] is not True:
        return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
    return {"state": "measured" if passed else "native_absent", "correct": int(passed), "observed_eligible": 1, "decoded_eligible": 1}


def _population(detail: Mapping[str, Any], required_key: str, records_key: str, valid: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    required = _id_list(detail[required_key], required_key)
    records = detail[records_key]
    if not isinstance(records, list):
        _fail(f"{records_key} must be an array")
    if detail["evidence_complete"] is not True:
        return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
    correct = sum(1 for item in records if isinstance(item, Mapping) and item.get("id") in required and valid(item))
    return {"state": "measured" if correct else "native_absent", "correct": correct, "observed_eligible": len(required), "decoded_eligible": len(records)}


def _thread_structure_valid(detail: Mapping[str, Any]) -> bool:
    session_id = detail["session_id"]
    explicit_parentage = detail["explicit_parentage"]
    turns = detail["turns"]
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(explicit_parentage, bool) or not isinstance(turns, list) or not turns:
        return False
    seen: set[str] = set()
    previous = 0
    last_user_id: str | None = None
    for turn in turns:
        if not isinstance(turn, Mapping) or set(turn) != {"id", "role", "ordinal", "parent_id"} or not isinstance(turn["id"], str) or not turn["id"].strip() or turn["id"] in seen or turn["role"] not in {"user", "assistant", "tool"} or not _is_int(turn["ordinal"]) or turn["ordinal"] <= previous or (turn["parent_id"] is not None and (not isinstance(turn["parent_id"], str) or not turn["parent_id"].strip())):
            return False
        seen.add(turn["id"])
        previous = turn["ordinal"]
        if turn["role"] == "user":
            last_user_id = turn["id"]
            continue
        if last_user_id is None:
            if not explicit_parentage:
                return False
            continue
        if not explicit_parentage and turn["parent_id"] not in {None, last_user_id}:
            return False
    return last_user_id is not None


def _event_timestamp_valid(record: Mapping[str, Any]) -> bool:
    if set(record) != {"id", "timestamp", "unit", "time_zone"} or not isinstance(record["id"], str) or not record["id"].strip() or record["unit"] not in {"rfc3339", "unix_ms", "unix_s"} or not isinstance(record["time_zone"], str) or not record["time_zone"].strip():
        return False
    timestamp = record["timestamp"]
    if record["unit"] == "rfc3339":
        if not isinstance(timestamp, str) or not timestamp:
            return False
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or (isinstance(timestamp, float) and not math.isfinite(timestamp)):
        return False
    seconds = timestamp / 1000 if record["unit"] == "unix_ms" else timestamp
    try:
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def _broad_metric(metric_id: str, detail: Any) -> dict[str, Any]:
    if not isinstance(detail, Mapping) or "evidence_complete" not in detail or not isinstance(detail["evidence_complete"], bool):
        _fail(f"{metric_id} requires boolean evidence_complete")
    if metric_id == "broad.readable_rationale":
        _exact_keys(detail, {"evidence_complete", "response_ids", "records"}, metric_id)
        return _population(detail, "response_ids", "records", lambda record: set(record) == {"id", "ordered_text"} and isinstance(record["ordered_text"], str) and bool(record["ordered_text"].strip()))
    if metric_id == "broad.thread_structure":
        _exact_keys(detail, {"evidence_complete", "session_id", "turns", "explicit_parentage"}, metric_id)
        return _complete_assertion(detail, _thread_structure_valid(detail))
    if metric_id == "broad.standard_tools_readable":
        _exact_keys(detail, {"evidence_complete", "container", "parser", "vendor_binary_required", "account_required", "backend_required", "network_required"}, metric_id)
        valid = detail["container"] in {"json", "jsonl", "sqlite", "text"} and isinstance(detail["parser"], str) and bool(detail["parser"].strip()) and all(detail[key] is False for key in ("vendor_binary_required", "account_required", "backend_required", "network_required"))
        return _complete_assertion(detail, valid)
    if metric_id == "broad.documented_format":
        _exact_keys(detail, {"evidence_complete", "document_id", "mapping"}, metric_id)
        mapping = detail["mapping"]
        required = {"containers", "record_types", "identities", "joins", "version_semantics"}
        valid = isinstance(detail["document_id"], str) and bool(detail["document_id"].strip()) and isinstance(mapping, Mapping) and set(mapping) == required and all(isinstance(value, str) and value.strip() for value in mapping.values())
        return _complete_assertion(detail, valid)
    if metric_id == "broad.self_contained_identity":
        _exact_keys(detail, {"evidence_complete", "session_id", "harness", "surface", "record_family", "external_lookup_required", "absolute_path_required"}, metric_id)
        valid = all(isinstance(detail[key], str) and detail[key].strip() for key in ("session_id", "harness", "surface", "record_family")) and detail["external_lookup_required"] is False and detail["absolute_path_required"] is False
        return _complete_assertion(detail, valid)
    if metric_id == "broad.declared_format_version":
        _exact_keys(detail, {"evidence_complete", "format_version", "machine_readable", "bundle_binding"}, metric_id)
        valid = isinstance(detail["format_version"], str) and bool(detail["format_version"].strip()) and detail["machine_readable"] is True and isinstance(detail["bundle_binding"], str) and bool(detail["bundle_binding"].strip())
        return _complete_assertion(detail, valid)
    if metric_id == "broad.event_timestamps":
        _exact_keys(detail, {"evidence_complete", "event_ids", "records"}, metric_id)
        return _population(detail, "event_ids", "records", _event_timestamp_valid)
    if metric_id == "broad.honest_version_signal":
        _exact_keys(detail, {"evidence_complete", "declared_version", "decoder_contract_version", "incompatible_schema_distinguished", "matches_decoder_contract"}, metric_id)
        valid = isinstance(detail["declared_version"], str) and bool(detail["declared_version"].strip()) and isinstance(detail["decoder_contract_version"], str) and bool(detail["decoder_contract_version"].strip()) and detail["incompatible_schema_distinguished"] is True and detail["matches_decoder_contract"] is True
        return _complete_assertion(detail, valid)
    if metric_id == "broad.observed_schema_stability":
        _exact_keys(detail, {"evidence_complete", "advertised_contract", "observations", "exceptions"}, metric_id)
        observations = detail["observations"]
        valid = isinstance(detail["advertised_contract"], str) and bool(detail["advertised_contract"].strip()) and isinstance(observations, list) and bool(observations) and detail["exceptions"] == []
        if valid:
            seen: set[tuple[str, str]] = set()
            for item in observations:
                if not isinstance(item, Mapping) or set(item) != {"build", "observed_on", "decoder_contract", "decoded"} or not isinstance(item["build"], str) or not item["build"].strip() or not isinstance(item["observed_on"], str) or item["decoder_contract"] != detail["advertised_contract"] or item["decoded"] is not True:
                    valid = False
                    break
                try:
                    date.fromisoformat(item["observed_on"])
                except ValueError:
                    valid = False
                    break
                key = (item["build"], item["observed_on"])
                if key in seen:
                    valid = False
                    break
                seen.add(key)
        return _complete_assertion(detail, valid)
    if metric_id == "broad.stable_root_location":
        _exact_keys(detail, {"evidence_complete", "repetitions"}, metric_id)
        repetitions = detail["repetitions"]
        valid = isinstance(repetitions, list) and {item.get("repetition") for item in repetitions if isinstance(item, Mapping)} == {1, 2, 3} and len(repetitions) == 3
        if valid:
            valid = all(isinstance(item["root_locator"], str) and item["root_locator"].strip() and item["personal_history_scanned"] is False and ((set(item) == {"repetition", "root_locator", "isolated_discovery", "personal_history_scanned"} and item["isolated_discovery"] is True) or (set(item) == {"repetition", "root_locator", "discovery_mode", "personal_history_scanned"} and item["discovery_mode"] in {"isolated", "metadata_safe_normal_root"})) for item in repetitions)
        return _complete_assertion(detail, valid)
    if metric_id == "broad.naive_reader_duplicate_safety":
        _exact_keys(detail, {"evidence_complete", "event_ids", "forward_records", "deduplication"}, metric_id)
        ids = _id_list(detail["event_ids"], "event_ids")
        records, dedup = detail["forward_records"], detail["deduplication"]
        if not isinstance(records, list) or not isinstance(dedup, Mapping) or set(dedup) != {"documented", "rule"} or not isinstance(dedup["rule"], str):
            _fail(f"{metric_id} requires forward records and documented deduplication")
        if detail["evidence_complete"] is not True:
            return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
        by_id: dict[str, list[Mapping[str, Any]]] = {event_id: [] for event_id in ids}
        for record in records:
            if isinstance(record, Mapping) and set(record) == {"event_id", "occurrence_id", "state"} and record.get("event_id") in by_id and isinstance(record.get("occurrence_id"), str) and record.get("state") in {"active", "superseded", "tombstone"}:
                by_id[record["event_id"]].append(record)
        correct = 0
        for event_id in ids:
            values = by_id[event_id]
            active = [item for item in values if item["state"] == "active"]
            if len(active) == 1 and (len(values) == 1 or (dedup["documented"] is True and bool(dedup["rule"].strip()))):
                correct += 1
        return {"state": "measured" if correct else "native_absent", "correct": correct, "observed_eligible": len(ids), "decoded_eligible": len(records)}
    if metric_id == "broad.classified_content_density":
        _exact_keys(detail, {"evidence_complete", "classification_rule", "records"}, metric_id)
        if detail["classification_rule"] != CONTENT_DENSITY_RULE:
            _fail(f"{metric_id} requires {CONTENT_DENSITY_RULE}")
        records = detail["records"]
        if not isinstance(records, list) or not records:
            _fail(f"{metric_id}.records must be non-empty")
        total = useful = 0
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {"record_id", "record_kind", "logical_bytes", "classification"} or not isinstance(record["record_id"], str) or not record["record_id"].strip() or record["record_id"] in seen or record["record_kind"] not in CONTENT_DENSITY_CLASSES or record["classification"] != CONTENT_DENSITY_CLASSES[record["record_kind"]] or not _is_int(record["logical_bytes"]) or record["logical_bytes"] < 0:
                _fail(f"{metric_id} has an invalid logical-byte record")
            seen.add(record["record_id"])
            total += record["logical_bytes"]
            if record["classification"] == "useful":
                useful += record["logical_bytes"]
        if total == 0:
            _fail(f"{metric_id} total logical bytes must be positive")
        if detail["evidence_complete"] is not True:
            return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
        return {"state": "measured" if useful else "native_absent", "correct": useful, "observed_eligible": total, "decoded_eligible": total}
    raise AssertionError(f"unhandled broad metric {metric_id}")


def _validate_profile(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    _exact_keys(value, {"schema_version", "run_id", "configuration_id", "repetition", "broad_evidence"}, label)
    if value["schema_version"] != FORMAT_PROFILE_SCHEMA_VERSION:
        _fail(f"{label}.schema_version is unsupported")
    run_id = _text(value["run_id"], f"{label}.run_id")
    configuration_id = _text(value["configuration_id"], f"{label}.configuration_id")
    if not _is_int(value["repetition"]) or value["repetition"] < 1:
        _fail(f"{label}.repetition must be a positive integer")
    broad = value["broad_evidence"]
    if not isinstance(broad, Mapping) or set(broad) != set(FORMAT_METRICS):
        _fail(f"{label} must contain all 12 broad evidence records")
    metrics = [{"id": metric_id, **_broad_metric(metric_id, broad[metric_id])} for metric_id in FORMAT_METRICS]
    return {"schema_version": FORMAT_PROFILE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": value["repetition"], "metrics": metrics}


def _validate_metric_evidence(value: Any, metrics: Sequence[str], source: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(metrics):
        _fail(f"{source} metric evidence must contain the exact metric set")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        label = f"{source} metric_evidence[{index}]"
        if not isinstance(row, Mapping):
            _fail(f"{label} must be an object")
        _exact_keys(row, {"metric_id", "observer_ids", "native_locators"}, label)
        metric_id = row["metric_id"]
        if metric_id not in metrics or metric_id in seen:
            _fail(f"{label}.metric_id is unknown or duplicated")
        observers = row["observer_ids"]
        if not isinstance(observers, list) or not observers or len(set(observers)) != len(observers) or any(not isinstance(item, str) or not item.strip() for item in observers):
            _fail(f"{label}.observer_ids is invalid")
        locators = row["native_locators"]
        if not isinstance(locators, list) or not locators:
            _fail(f"{label}.native_locators must be non-empty")
        locs: list[dict[str, str]] = []
        for locator in locators:
            if source == "survival":
                if not isinstance(locator, Mapping):
                    _fail(f"{label} survival locator must be an object")
                _exact_keys(locator, {"artifact_id", "artifact_sha256", "record_location"}, "survival locator")
                locs.append({"artifact_id": _text(locator["artifact_id"], "survival locator artifact_id"), "artifact_sha256": _digest(locator["artifact_sha256"], "survival locator artifact_sha256"), "record_location": _text(locator["record_location"], "survival locator record_location")})
            else:
                normalized_locator = _read_digest_object(locator, "format locator")
                locs.append(normalized_locator)
        keys = {tuple(item.values()) for item in locs}
        if len(keys) != len(locs):
            _fail(f"{label}.native_locators must be unique")
        seen.add(metric_id)
        normalized.append({"metric_id": metric_id, "observer_ids": list(observers), "native_locators": locs})
    if set(seen) != set(metrics):
        _fail(f"{source} metric evidence is incomplete")
    return normalized


def _validate_survival_evidence(value: Any, expected: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("survival evidence must be an object")
    expected_fields = {"schema_version", "protocol_version", "workload_version", "rubric_version", "run_id", "capture_id", "evaluation_id", "configuration_id", "repetition", "measurement", "observer", "native_manifest", "decoder", "metric_evidence"}
    if "identity" in value:
        expected_fields.add("identity")
    _exact_keys(value, expected_fields, "survival evidence")
    if value["schema_version"] != SURVIVAL_EVIDENCE_SCHEMA_VERSION or value["protocol_version"] != PROTOCOL_VERSION or value["workload_version"] != WORKLOAD_VERSION or value["rubric_version"] != RUBRIC_VERSION:
        _fail("survival evidence version tuple is unsupported")
    for field in ("run_id", "capture_id", "evaluation_id", "configuration_id"):
        _text(value[field], f"survival evidence {field}")
    repetition = value["repetition"]
    if not _is_int(repetition) or repetition < 1:
        _fail("survival evidence repetition must be positive")
    measurement = _validate_survival_input(value["measurement"], "survival evidence measurement")
    if (measurement["run_id"], measurement["configuration_id"], measurement["repetition"]) != (value["run_id"], value["configuration_id"], repetition):
        _fail("survival measurement identity does not match evidence wrapper")
    observer = _read_digest_object(value["observer"], "survival evidence observer")
    native_manifest = _read_digest_object(value["native_manifest"], "survival evidence native_manifest")
    decoder = _read_digest_object(value["decoder"], "survival evidence decoder")
    metric_evidence = _validate_metric_evidence(value["metric_evidence"], SURVIVAL_METRICS, "survival")
    if (value["run_id"], value["configuration_id"], repetition) != (expected["run_id"], expected["configuration_id"], expected["repetition"]):
        _fail("survival evidence is not bound to its bundle run")
    identity = value.get("identity")
    if identity is not None:
        if not isinstance(identity, Mapping):
            _fail("survival evidence identity must be an object")
        allowed = {"provider", "harness", "surface", "execution_mode", "os", "build", "model", "configuration", "protocol_version", "workload_version", "observer_schema_version", "rubric_version"}
        if set(identity) - allowed:
            _fail("survival evidence identity contains unknown fields")
        for field, item in identity.items():
            _text(item, f"survival identity {field}")
        for field, version in (("protocol_version", PROTOCOL_VERSION), ("workload_version", WORKLOAD_VERSION), ("rubric_version", RUBRIC_VERSION)):
            if field in identity and identity[field] != version:
                _fail(f"survival identity {field} does not match protocol")
    return {"document": dict(value), "measurement": measurement, "observer": observer, "native_manifest": native_manifest, "decoder": decoder, "metric_evidence": metric_evidence, "identity": identity}


def _validate_format_evidence(value: Any, expected: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("format evidence must be an object")
    _exact_keys(value, {"schema_version", "run_id", "configuration_id", "repetition", "build", "collected_on", "result_id", "observer", "native_manifest", "profile", "metric_evidence"}, "format evidence")
    if value["schema_version"] != FORMAT_EVIDENCE_SCHEMA_VERSION:
        _fail("format evidence schema is unsupported")
    for field in ("run_id", "configuration_id", "build", "result_id"):
        _text(value[field], f"format evidence {field}")
    collected_on = _text(value["collected_on"], "format evidence collected_on")
    if len(collected_on) != 10:
        _fail("format evidence collected_on must be YYYY-MM-DD")
    try:
        date.fromisoformat(collected_on)
    except ValueError as exc:
        raise VerificationError("format evidence collected_on must be YYYY-MM-DD") from exc
    repetition = value["repetition"]
    if not _is_int(repetition) or repetition < 1:
        _fail("format evidence repetition must be positive")
    profile = _validate_profile(value["profile"], "format evidence profile")
    if (value["run_id"], value["configuration_id"], repetition) != (profile["run_id"], profile["configuration_id"], profile["repetition"]):
        _fail("format evidence and profile identities differ")
    metric_evidence = _validate_metric_evidence(value["metric_evidence"], FORMAT_METRICS, "format")
    _read_digest_object(value["observer"], "format evidence observer")
    _read_digest_object(value["native_manifest"], "format evidence native_manifest")
    if (value["run_id"], value["configuration_id"], repetition, value["result_id"]) != (expected["run_id"], expected["configuration_id"], expected["repetition"], expected["result_id"]):
        _fail("format evidence is not bound to its bundle run")
    return {"document": dict(value), "profile": profile, "build": value["build"], "collected_on": collected_on, "result_id": value["result_id"], "metric_evidence": metric_evidence}


def _reject_absolute_paths(value: Any, label: str) -> None:
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("~/"):
            _fail(f"{label} contains an absolute or home-relative path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_absolute_paths(key, f"{label}.key")
            _reject_absolute_paths(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_absolute_paths(item, f"{label}[{index}]")


def _validate_bundle_run(value: Any, index: int) -> dict[str, Any]:
    label = f"configuration bundle run {index}"
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    fields = {"configuration_id", "run_id", "repetition", "result_id", "evaluation_id", "collected_on", "identity", "result", "replay", "canonical_equality", "privacy"}
    _exact_keys(value, fields, label)
    for field in ("configuration_id", "run_id", "result_id", "evaluation_id", "collected_on"):
        _text(value[field], f"{label}.{field}")
    if type(value["repetition"]) is not int or value["repetition"] not in {1, 2, 3}:
        _fail(f"{label}.repetition must be 1, 2, or 3")
    try:
        date.fromisoformat(value["collected_on"])
    except ValueError as exc:
        raise VerificationError(f"{label}.collected_on must be YYYY-MM-DD") from exc
    identity = value["identity"]
    if not isinstance(identity, Mapping):
        _fail(f"{label}.identity must be an object")
    _exact_keys(identity, {"provider", "harness", "surface", "execution_mode", "os", "build", "model", "configuration", "observer_schema_version"}, f"{label}.identity")
    for field, item in identity.items():
        _text(item, f"{label}.identity.{field}")
        if item.strip().lower() in UNAVAILABLE_IDENTITY_VALUES:
            _fail(f"{label}.identity.{field} is an unavailable sentinel")
    result = value["result"]
    if not isinstance(result, Mapping):
        _fail(f"{label}.result must be an object")
    _exact_keys(result, {"resolved", "metric_ids", "metrics", "categories", "overall"}, f"{label}.result")
    if result["resolved"] is not True or result["metric_ids"] != list(PUBLIC_METRICS):
        _fail(f"{label}.result is not the frozen 31-metric result shape")
    if not isinstance(result["metrics"], Mapping) or set(result["metrics"]) != set(PUBLIC_METRICS):
        _fail(f"{label}.result.metrics does not contain all 31 metrics")
    if not isinstance(result["categories"], Mapping) or set(result["categories"]) != set(PUBLIC_CATEGORY_POINTS):
        _fail(f"{label}.result.categories does not contain all five categories")
    for metric_id, metric_value in result["metrics"].items():
        _number_fraction(metric_value, f"{label}.result.metrics.{metric_id}")
    for category, category_value in result["categories"].items():
        _number_fraction(category_value, f"{label}.result.categories.{category}")
    overall = _number_fraction(result["overall"], f"{label}.result.overall")
    if overall < 0 or overall > 100:
        _fail(f"{label}.result.overall is outside 0..100")
    replay = value["replay"]
    if not isinstance(replay, Mapping):
        _fail(f"{label}.replay must be an object")
    _exact_keys(replay, {"evidence_id", "verified", "offline", "original_root_denied", "vendor_executable_denied", "network_denied", "runtime_sha256"}, f"{label}.replay")
    for field in ("verified", "offline", "original_root_denied", "vendor_executable_denied", "network_denied"):
        if replay[field] is not True:
            _fail(f"{label}.replay.{field} must be true")
    _text(replay["evidence_id"], f"{label}.replay.evidence_id")
    _digest(replay["runtime_sha256"], f"{label}.replay.runtime_sha256")
    equality = value["canonical_equality"]
    if not isinstance(equality, Mapping):
        _fail(f"{label}.canonical_equality must be an object")
    _exact_keys(equality, {"evidence_id", "verified", "ordinary_sha256", "isolated_sha256"}, f"{label}.canonical_equality")
    if equality["verified"] is not True:
        _fail(f"{label}.canonical_equality.verified must be true")
    _text(equality["evidence_id"], f"{label}.canonical_equality.evidence_id")
    ordinary = _digest(equality["ordinary_sha256"], f"{label}.canonical_equality.ordinary_sha256")
    isolated = _digest(equality["isolated_sha256"], f"{label}.canonical_equality.isolated_sha256")
    if ordinary != isolated:
        _fail(f"{label}.canonical_equality hashes differ")
    privacy = value["privacy"]
    if not isinstance(privacy, Mapping):
        _fail(f"{label}.privacy must be an object")
    _exact_keys(privacy, {"absolute_paths_absent", "account_data_absent", "credentials_absent", "evidence_id", "personal_history_absent", "public_derivative_sha256", "raw_native_withheld", "verified"}, f"{label}.privacy")
    for field in ("absolute_paths_absent", "account_data_absent", "credentials_absent", "personal_history_absent", "raw_native_withheld", "verified"):
        if privacy[field] is not True:
            _fail(f"{label}.privacy.{field} must be true")
    _text(privacy["evidence_id"], f"{label}.privacy.evidence_id")
    _digest(privacy["public_derivative_sha256"], f"{label}.privacy.public_derivative_sha256")
    normalized = dict(value)
    _reject_absolute_paths(normalized, label)
    return normalized


def _validate_bundle(root: Path, manifest_info: Mapping[str, Any]) -> dict[str, Any]:
    document = _object(root / "configuration-bundle.json")
    _exact_keys(document, {"schema_version", "configuration_id", "bundle", "runs", "reproduction_receipt", "independent_reproduction"}, "configuration bundle")
    if document["schema_version"] != BUNDLE_SCHEMA_VERSION or document["independent_reproduction"] is not False:
        _fail("configuration bundle schema or independence flag is invalid")
    configuration_id = _text(document["configuration_id"], "configuration bundle configuration_id")
    raw_runs = document["runs"]
    if not isinstance(raw_runs, list) or len(raw_runs) != 3:
        _fail("configuration bundle requires exactly three runs")
    runs = [_validate_bundle_run(item, index) for index, item in enumerate(raw_runs, 1)]
    if {run["configuration_id"] for run in runs} != {configuration_id} or {run["repetition"] for run in runs} != {1, 2, 3}:
        _fail("configuration bundle runs do not contain one configuration and repetitions 1, 2, 3")
    for field in ("run_id", "result_id", "evaluation_id"):
        if len({run[field] for run in runs}) != 3:
            _fail(f"configuration bundle runs must have unique {field} values")
    first_identity = runs[0]["identity"]
    if any(run["identity"] != first_identity for run in runs[1:]):
        _fail("configuration bundle identity differs across repetitions")
    ordered_runs = sorted(runs, key=lambda item: item["repetition"])
    result_ids = tuple(sorted(run["result_id"] for run in ordered_runs))
    bundle = document["bundle"]
    if not isinstance(bundle, Mapping):
        _fail("configuration bundle binding must be an object")
    _exact_keys(bundle, {"id", "sha256", "immutable", "public", "result_ids"}, "configuration bundle binding")
    bundle_id = _text(bundle["id"], "configuration bundle id")
    bundle_sha = _digest(bundle["sha256"], "configuration bundle sha256")
    if bundle["immutable"] is not True or bundle["public"] is not True or bundle["result_ids"] != list(result_ids):
        _fail("configuration bundle binding does not bind the exact three runs")
    preimage = {"schema_version": BUNDLE_SCHEMA_VERSION, "id": bundle_id, "configuration_id": configuration_id, "immutable": True, "public": True, "result_ids": list(result_ids), "runs": ordered_runs}
    if _canonical_sha(preimage) != bundle_sha:
        _fail("configuration bundle hash does not match canonical contents")
    receipt = document["reproduction_receipt"]
    if not isinstance(receipt, Mapping):
        _fail("configuration bundle reproduction_receipt must be an object")
    _exact_keys(receipt, {"id", "sha256", "bundle_sha256", "offline_recomputed", "verified", "result_ids"}, "configuration bundle reproduction_receipt")
    receipt_id = _text(receipt["id"], "reproduction receipt id")
    receipt_sha = _digest(receipt["sha256"], "reproduction receipt sha256")
    if receipt["bundle_sha256"] != bundle_sha or receipt["offline_recomputed"] is not True or receipt["verified"] is not True or receipt["result_ids"] != list(result_ids):
        _fail("configuration bundle reproduction receipt is not bound to its bundle")
    receipt_preimage = {"schema_version": RECEIPT_SCHEMA_VERSION, "id": receipt_id, "bundle_id": bundle_id, "bundle_sha256": bundle_sha, "offline_recomputed": True, "verified": True, "result_ids": list(result_ids)}
    if _canonical_sha(receipt_preimage) != receipt_sha:
        _fail("configuration bundle reproduction receipt hash does not match canonical contents")
    if manifest_info["configuration_id"] != configuration_id or manifest_info["result_ids"] != result_ids or manifest_info["document"]["configuration_bundle_sha256"] != bundle_sha:
        _fail("bundle manifest does not bind configuration bundle")
    configuration_evidence = _object(root / "configuration-evidence.json")
    _exact_keys(configuration_evidence, {"schema_version", "configuration_id", "bundle", "reproduction_receipt"}, "configuration evidence")
    if configuration_evidence["schema_version"] != CONFIGURATION_EVIDENCE_SCHEMA_VERSION or configuration_evidence["configuration_id"] != configuration_id or configuration_evidence["bundle"] != bundle or configuration_evidence["reproduction_receipt"] != receipt:
        _fail("configuration evidence does not duplicate the canonical bundle binding")
    return {"document": document, "configuration_id": configuration_id, "runs": ordered_runs, "bundle": dict(bundle), "receipt": dict(receipt), "bundle_sha256": bundle_sha, "receipt_id": receipt_id}


def _category_scores(metric_values: Mapping[str, Fraction | None]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for category, maximum in PUBLIC_CATEGORY_POINTS.items():
        rows = [(points, metric_values[metric_id]) for metric_id, (row_category, points) in PUBLIC_METRIC_SPECS.items() if row_category == category]
        known_points = sum((points * value for points, value in rows if value is not None), Fraction(0))
        known_possible = sum((points for points, value in rows if value is not None), Fraction(0))
        resolved = sum(value is not None for _points, value in rows)
        complete = resolved == len(rows)
        score = known_points if complete else None
        result[category] = {
            "score": score,
            "known_points": known_points,
            "known_possible_points": known_possible,
            "known_quality_percent": None if known_possible == 0 else known_points * 100 / known_possible,
            "coverage": f"{resolved}/{len(rows)}",
            "possible_min": known_points,
            "possible_max": known_points + maximum - known_possible,
            "possible_percent_min": known_points * 100 / maximum,
            "possible_percent_max": (known_points + maximum - known_possible) * 100 / maximum,
            "resolved_metrics": resolved,
            "total_metrics": len(rows),
        }
    return result


def _category_display(value: Mapping[str, Any]) -> dict[str, str | None]:
    return {
        "score": _display_decimal(value["score"]),
        "known_points": _display_decimal(value["known_points"]),
        "known_possible_points": _display_decimal(value["known_possible_points"]),
        "known_quality_percent": _display_decimal(value["known_quality_percent"]),
        "coverage": value["coverage"],
        "possible_min": _display_decimal(value["possible_min"]),
        "possible_max": _display_decimal(value["possible_max"]),
        "possible_percent_min": _display_decimal(value["possible_percent_min"]),
        "possible_percent_max": _display_decimal(value["possible_percent_max"]),
    }


def _metric_state_map(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        metric_id = row.get("id")
        state = row.get("state")
        if metric_id in result or metric_id not in PUBLIC_METRICS or state not in ALLOWED_STATES:
            _fail(f"{label} has an unknown or duplicate metric state")
        result[metric_id] = state
    if set(result) != set(PUBLIC_METRICS):
        _fail(f"{label} does not cover all 31 public metrics")
    return result


def _public_metric_evidence(survival: Mapping[str, Any], format_info: Mapping[str, Any], states: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in survival["metric_evidence"]:
        result[row["metric_id"]] = {
            "metric_id": row["metric_id"],
            "state": states[row["metric_id"]],
            "observer_ids": list(row["observer_ids"]),
            "native_locators": [dict(locator) for locator in row["native_locators"]],
        }
    for row in format_info["metric_evidence"]:
        result[row["metric_id"]] = {
            "metric_id": row["metric_id"],
            "state": states[row["metric_id"]],
            "observer_ids": list(row["observer_ids"]),
            "native_locators": [{"artifact_id": locator["id"], "artifact_sha256": locator["sha256"]} for locator in row["native_locators"]],
        }
    if set(result) != set(PUBLIC_METRICS):
        _fail("public metric evidence does not cover all 31 metrics")
    return result


def _public_identity(survival: Mapping[str, Any], format_info: Mapping[str, Any]) -> dict[str, Any]:
    raw = survival["identity"] or {}
    if not isinstance(raw, Mapping):
        _fail("survival identity must be an object")
    identity = {
        "provider": raw.get("provider"),
        "harness": raw.get("harness"),
        "surface": raw.get("surface"),
        "execution_mode": raw.get("execution_mode"),
        "os": raw.get("os"),
        "build": format_info["build"],
        "model": raw.get("model"),
        "configuration": raw.get("configuration"),
        "configuration_id": survival["document"]["configuration_id"],
        "protocol_version": survival["document"]["protocol_version"],
        "workload_version": survival["document"]["workload_version"],
        "observer_schema_version": raw.get("observer_schema_version"),
        "rubric_version": survival["document"]["rubric_version"],
        "run_id": survival["document"]["run_id"],
        "capture_id": survival["document"]["capture_id"],
        "evaluation_id": survival["document"]["evaluation_id"],
        "repetition": survival["document"]["repetition"],
        "collected_on": format_info["collected_on"],
        "result_id": format_info["result_id"],
    }
    if raw.get("build") is not None and raw["build"] != format_info["build"]:
        _fail("survival identity build does not match format evidence build")
    identity["unavailable"] = [
        field
        for field in ("provider", "harness", "surface", "execution_mode", "os", "model", "configuration", "observer_schema_version")
        if not isinstance(identity[field], str)
        or not identity[field].strip()
        or identity[field].strip().lower() in UNAVAILABLE_IDENTITY_VALUES
    ]
    return identity


def _public_timeline(metric_evidence: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for step, metric_id in enumerate(SURVIVAL_METRICS, 1):
        evidence = metric_evidence[metric_id]
        observed = ", ".join(evidence["observer_ids"])
        recorded = ", ".join(locator.get("record_location") or locator["artifact_id"] for locator in evidence["native_locators"])
        result.append({
            "step": step,
            "label": metric_id,
            "metric_id": metric_id,
            "state": evidence["state"],
            "observed": f"observer_ids: {observed}",
            "recorded": f"native_locators: {recorded}",
            "observer_ids": list(evidence["observer_ids"]),
            "native_locators": [dict(locator) for locator in evidence["native_locators"]],
            "order_basis": "survival_metric_contract",
            "temporal_order_available": False,
        })
    return result


def _score_run(survival_info: Mapping[str, Any], format_info: Mapping[str, Any]) -> dict[str, Any]:
    measurement = survival_info["measurement"]
    survival_rows = {row["id"]: row for row in measurement["metrics"]}
    format_rows = {row["id"]: row for row in format_info["profile"]["metrics"]}
    metric_values: dict[str, Fraction | None] = {}
    blockers: list[str] = []
    for metric_id in SURVIVAL_METRICS:
        value = _metric_fraction(survival_rows[metric_id])
        metric_values[metric_id] = value
        if survival_rows[metric_id]["state"] in BLOCKING_STATES:
            blockers.append(f"{metric_id}:{survival_rows[metric_id]['state']}")
    for metric_id in FORMAT_METRICS:
        value = _metric_fraction(format_rows[metric_id])
        metric_values[metric_id] = value
        if format_rows[metric_id]["state"] in BLOCKING_STATES:
            blockers.append(f"{metric_id}:{format_rows[metric_id]['state']}")
    categories = _category_scores(metric_values)
    identity = _public_identity(survival_info, format_info)
    complete = (
        all(value["resolved_metrics"] == value["total_metrics"] for value in categories.values())
        and not identity["unavailable"]
    )
    overall = sum((value["score"] for value in categories.values() if value["score"] is not None), Fraction(0)) if complete else None
    portable_gate = all(metric_values[metric_id] == Fraction(1) for metric_id in ("portable.complete_root", "portable.companions", "portable.isolated_decode", "portable.canonical_equality"))
    metric_evidence = _public_metric_evidence(survival_info["document"], format_info, {**{row["id"]: row["state"] for row in measurement["metrics"]}, **{row["id"]: row["state"] for row in format_info["profile"]["metrics"]}})
    expected = {
        "run_id": measurement["run_id"],
        "configuration_id": measurement["configuration_id"],
        "repetition": measurement["repetition"],
        "overall": _display_decimal(overall),
        "rankable": complete,
        "portable_gate": portable_gate,
        "metrics": {metric_id: _display_decimal(value * 100 if value is not None else None) for metric_id, value in metric_values.items()},
        "metric_evidence": metric_evidence,
        "identity": identity,
        "timeline": _public_timeline(metric_evidence),
        "timeline_order_basis": "survival_metric_contract",
        "temporal_timeline_available": False,
        "categories": {category: _category_display(value) for category, value in categories.items()},
        "blockers": blockers,
    }
    return {"display": expected, "metric_values": metric_values, "categories": categories, "overall": overall, "portable_gate": portable_gate, "rankable": complete, "blockers": blockers, "metric_evidence": metric_evidence, "identity": identity}


def _compare_number(actual: Any, expected: Fraction, label: str) -> None:
    if _number_fraction(actual, label) != expected:
        _fail(f"{label} does not match independently recomputed value")


def _bundle_result_projection(score: Mapping[str, Any]) -> dict[str, Any]:
    if not score["rankable"] or score["overall"] is None:
        _fail("configuration bundle cannot contain an unresolved public run")
    return {
        "resolved": True,
        "metric_ids": list(PUBLIC_METRICS),
        "metrics": {metric_id: float(score["metric_values"][metric_id]) for metric_id in PUBLIC_METRICS},
        "categories": {category: float(score["categories"][category]["score"]) for category in PUBLIC_CATEGORY_POINTS},
        "overall": float(score["overall"]),
    }


def verify_score_inputs(root: Path, bundle_info: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recompute every run from its survival and format evidence inputs."""
    results: list[dict[str, Any]] = []
    for run in bundle_info["runs"]:
        repetition = run["repetition"]
        run_root = root / "runs" / str(repetition)
        survival_info = _validate_survival_evidence(_object(run_root / "score-input/survival-evidence.json"), run)
        format_info = _validate_format_evidence(_object(run_root / "score-input/format-evidence.json"), run)
        if survival_info["identity"] is not None and survival_info["identity"].get("build") not in {None, format_info["build"]}:
            _fail(f"repetition {repetition} survival identity build differs from format build")
        score = _score_run(survival_info, format_info)
        public_score = _object(run_root / "score-input/public-score.json")
        if public_score != score["display"]:
            _fail(f"repetition {repetition} public-score projection differs from independently recomputed score")
        if run["result"] != _bundle_result_projection(score):
            _fail(f"repetition {repetition} configuration-bundle result differs from score-input recomputation")
        results.append({"run": run, "survival": survival_info, "format": format_info, "score": score})
    return results


def _aggregate_scores(run_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(run_results) != 3:
        _fail("configuration aggregate requires exactly three runs")
    ordered = sorted(run_results, key=lambda item: item["run"]["repetition"])
    if [item["run"]["repetition"] for item in ordered] != [1, 2, 3]:
        _fail("configuration aggregate requires repetitions 1, 2, and 3")
    config_ids = {item["run"]["configuration_id"] for item in ordered}
    if len(config_ids) != 1:
        _fail("configuration aggregate mixes configurations")
    categories: dict[str, Fraction] = {}
    ranges: dict[str, list[Fraction]] = {}
    for category in PUBLIC_CATEGORY_POINTS:
        values = [item["score"]["categories"][category]["score"] for item in ordered]
        if any(value is None for value in values):
            _fail(f"configuration category {category} is unresolved")
        exact = [value for value in values if value is not None]
        categories[category] = sum(exact, Fraction(0)) / 3
        ranges[category] = [min(exact), max(exact)]
    overalls = [item["score"]["overall"] for item in ordered]
    if any(value is None for value in overalls):
        _fail("configuration overall is unresolved")
    exact_overalls = [value for value in overalls if value is not None]
    overall = sum(exact_overalls, Fraction(0)) / 3
    overall_range = [min(exact_overalls), max(exact_overalls)]
    category_fractions = {category: categories[category] / maximum for category, maximum in PUBLIC_CATEGORY_POINTS.items()}
    sensitivity = {
        vector: sum((category_fractions[category] * weight for category, weight in weights.items()), Fraction(0))
        for vector, weights in WEIGHT_VECTORS.items()
    }
    return {"configuration_id": next(iter(config_ids)), "ordered": ordered, "categories": categories, "category_ranges": ranges, "overall": overall, "overall_range": overall_range, "sensitivity": sensitivity}


def _configuration_score_projection(bundle_info: Mapping[str, Any], aggregate: Mapping[str, Any]) -> dict[str, Any]:
    ordered = aggregate["ordered"]
    return {
        "schema_version": CONFIGURATION_SCORE_SCHEMA_VERSION,
        "configuration_id": aggregate["configuration_id"],
        "bundle_id": bundle_info["bundle"]["id"],
        "reproduction_receipt_id": bundle_info["receipt"]["id"],
        "verification": "fully_reproduced",
        "rankable_within_configuration": True,
        "public_claim_allowed": False,
        "global_rank_allowed": False,
        "unranked_reasons": ["independent operator or environment reproduction is not established", "global v1 rank requires all five qualified configurations"],
        "independent_reproduction": False,
        "overall": _display_decimal(aggregate["overall"]),
        "overall_exact": _fraction_text(aggregate["overall"]),
        "range": [_display_decimal(value) for value in aggregate["overall_range"]],
        "range_exact": [_fraction_text(value) for value in aggregate["overall_range"]],
        "categories": {category: _display_decimal(aggregate["categories"][category]) for category in PUBLIC_CATEGORY_POINTS},
        "category_ranges": {category: [_display_decimal(value) for value in aggregate["category_ranges"][category]] for category in PUBLIC_CATEGORY_POINTS},
        "sensitivity": {vector: _display_decimal(value) for vector, value in aggregate["sensitivity"].items()},
        "repetitions": [
            {
                "repetition": item["run"]["repetition"],
                "run_id": item["run"]["run_id"],
                "result_id": item["run"]["result_id"],
                "overall": _display_decimal(item["score"]["overall"]),
                "overall_exact": _fraction_text(item["score"]["overall"]),
                "metric_count": len(item["score"]["metric_values"]),
                "rankable": item["score"]["rankable"],
            }
            for item in ordered
        ],
        "leaderboard": {"published": False, "reason": "global v1 rank requires all five qualified configurations"},
    }


def verify_configuration_aggregate(root: Path, bundle_info: Mapping[str, Any], run_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    aggregate = _aggregate_scores(run_results)
    score_document = _object(root / "configuration-score.json")
    expected = _configuration_score_projection(bundle_info, aggregate)
    if score_document != expected:
        _fail("configuration-score.json differs from independently recomputed aggregate")
    return aggregate


def _semantic_paths(run_root: Path) -> dict[str, dict[str, Any]]:
    names = {
        "correction": "semantic/correction-receipt.json",
        "decoded": "semantic/decoded.json",
        "evidence": "semantic/evidence.json",
        "measurement": "semantic/measurement.json",
        "observer": "semantic/observer.json",
        "portability": "semantic/portability-receipt.json",
        "summary": "semantic/summary.json",
        "public_manifest": "semantic/public-manifest.json",
        "redaction": "semantic/redaction-receipt.json",
        "source_denied": "source-denied-replay.json",
    }
    return {key: _object(run_root / relative) for key, relative in names.items()}


def _validate_portability_receipt(
    value: Mapping[str, Any],
    *,
    run: Mapping[str, Any],
    decoded: Mapping[str, Any],
    observer: Mapping[str, Any],
    public_manifest: Mapping[str, Any],
) -> None:
    """Validate the source-attested OpenCode portability receipt contract.

    ``source_native_files_denied`` is produced by the native evidence capture
    as the number of distinct paths visited while denying each
    ``opencode.db``/WAL/SHM family in the run, capture, and offline-bundle
    directories.  A public derivative does not retain those roots, so the
    count cannot be recomputed here.  It is nevertheless constrained to a
    positive number of complete three-file families and the receipt's
    remaining identity/qualification fields are checked against public
    semantic data.
    """
    label = f"repetition {run['repetition']} portability receipt"
    expected_fields = {
        "canonical_equality",
        "companions_present",
        "complete_root",
        "isolated_decode",
        "native_session_ids",
        "prior_controller_isolation_attestation",
        "schema_version",
        "scope",
        "selected_session_id",
        "source_native_files_denied",
    }
    _exact_keys(value, expected_fields, label)
    if value["schema_version"] != "session-bench-portability-receipt-v1":
        _fail(f"{label}.schema_version is unsupported")
    if value["scope"] != "isolated OpenCode session database family":
        _fail(f"{label}.scope is unsupported")
    for field in ("complete_root", "companions_present", "isolated_decode", "canonical_equality"):
        if value[field] is not True:
            _fail(f"{label}.{field} must be true")
    if type(value["prior_controller_isolation_attestation"]) is not bool:
        _fail(f"{label}.prior_controller_isolation_attestation must be boolean")

    denied = value["source_native_files_denied"]
    if not _is_int(denied) or denied < 3 or denied % 3 != 0:
        _fail(
            f"{label}.source_native_files_denied must be a positive multiple of the three-file native family"
        )

    selected_session_id = _text(value["selected_session_id"], f"{label}.selected_session_id")
    native_session_ids = value["native_session_ids"]
    if not isinstance(native_session_ids, list) or len(native_session_ids) != 1:
        _fail(f"{label}.native_session_ids must contain exactly one session ID")
    native_session_id = _text(native_session_ids[0], f"{label}.native_session_ids[0]")
    if native_session_id != selected_session_id:
        _fail(f"{label}.native_session_ids is not bound to selected_session_id")

    decoded_session_id = _text(decoded.get("session_id"), f"repetition {run['repetition']} decoded.session_id")
    if selected_session_id != decoded_session_id:
        _fail(f"{label}.selected_session_id differs from decoded session identity")
    observer_session_ids = {
        _text(item.get("session_id"), f"repetition {run['repetition']} observer session_id")
        for item in observer.get("events", [])
        if isinstance(item, Mapping) and item.get("session_id") is not None
    }
    if observer_session_ids != {selected_session_id}:
        _fail(f"{label}.selected_session_id is not bound to observer events")

    withheld = public_manifest.get("withheld_native_artifacts")
    if not isinstance(withheld, list) or len(withheld) != 3:
        _fail(f"repetition {run['repetition']} public manifest must withhold one native family")
    expected_native_paths = {
        "native-bundle/opencode.db",
        "native-bundle/opencode.db-wal",
        "native-bundle/opencode.db-shm",
    }
    actual_native_paths: set[str] = set()
    for index, item in enumerate(withheld):
        if not isinstance(item, Mapping):
            _fail(f"repetition {run['repetition']} withheld native artifact {index} is malformed")
        _exact_keys(item, {"path", "sha256", "size_bytes"}, f"repetition {run['repetition']} withheld native artifact {index}")
        path = _text(item["path"], f"repetition {run['repetition']} withheld native artifact path")
        _digest(item["sha256"], f"repetition {run['repetition']} withheld native artifact sha256")
        if not _is_int(item["size_bytes"]) or item["size_bytes"] < 0:
            _fail(f"repetition {run['repetition']} withheld native artifact size_bytes is invalid")
        actual_native_paths.add(path)
    if actual_native_paths != expected_native_paths:
        _fail(f"repetition {run['repetition']} public manifest native family is incomplete")


def _selected_loss_from_public_semantics(run_result: Mapping[str, Any], semantic: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    run = run_result["run"]
    survival = run_result["survival"]
    decoded = semantic["decoded"]
    observer = semantic["observer"]
    measurement = semantic["measurement"]
    summary = semantic["summary"]
    source_denied = semantic["source_denied"]
    if measurement != survival["measurement"]:
        _fail(f"repetition {run['repetition']} semantic measurement differs from score-input measurement")
    if semantic["evidence"] != survival["document"]:
        _fail(f"repetition {run['repetition']} semantic evidence differs from score-input evidence")
    responses = decoded.get("responses")
    if not isinstance(responses, list):
        _fail(f"repetition {run['repetition']} decoded semantic responses are missing")
    events = observer.get("events")
    if not isinstance(events, list):
        _fail(f"repetition {run['repetition']} observer semantic events are missing")
    selected = source_denied.get("selected_loss_control")
    if not isinstance(selected, Mapping):
        _fail(f"repetition {run['repetition']} selected-loss receipt is missing")
    required_selected = {"damaged_correct", "independent_reproduction", "intact_correct", "loss_state", "package_id", "scope", "selected_canary", "selected_native_row_id", "selected_native_table", "selected_observer_id"}
    _exact_keys(selected, required_selected, f"repetition {run['repetition']} selected-loss receipt")
    selected_canary = _text(selected["selected_canary"], "selected-loss canary")
    decoded_matches = [item for item in responses if isinstance(item, Mapping) and item.get("canary") == selected_canary]
    observer_matches = [item for item in events if isinstance(item, Mapping) and item.get("id") == selected["selected_observer_id"] and isinstance(item.get("fields"), Mapping) and item["fields"].get("canary") == selected_canary]
    if len(decoded_matches) != 1 or len(observer_matches) != 1:
        _fail(f"repetition {run['repetition']} selected-loss canary is not uniquely present in public semantics")
    visible_row = next((row for row in measurement.get("metrics", []) if isinstance(row, Mapping) and row.get("id") == "work.visible_responses"), None)
    if not isinstance(visible_row, Mapping):
        _fail(f"repetition {run['repetition']} visible-response measurement is missing")
    intact_correct = selected["intact_correct"]
    if not _is_int(intact_correct) or intact_correct != len(responses) or intact_correct != visible_row.get("correct"):
        _fail(f"repetition {run['repetition']} selected-loss intact count is not bound to public semantics")
    if not _is_int(selected["damaged_correct"]) or selected["damaged_correct"] >= intact_correct:
        _fail(f"repetition {run['repetition']} selected-loss damaged count is invalid")
    if selected["independent_reproduction"] is not False or selected["loss_state"] != "reduced_visible_response_accuracy" or selected["scope"] != "local_copied_package_damage_control_only":
        _fail(f"repetition {run['repetition']} selected-loss receipt has an invalid qualification")
    _text(selected["selected_observer_id"], "selected-loss observer ID")
    _text(selected["selected_native_table"], "selected-loss native table")
    _text(selected["selected_native_row_id"], "selected-loss native row ID")
    package_id = _text(selected["package_id"], "selected-loss package ID")
    for source in (source_denied, summary, semantic["public_manifest"]):
        if source.get("package_id") != package_id and source is not semantic["public_manifest"]:
            _fail(f"repetition {run['repetition']} selected-loss package binding differs")
    if semantic["public_manifest"].get("source_package_id") != package_id:
        _fail(f"repetition {run['repetition']} selected-loss public-manifest binding differs")
    if source_denied.get("independent_reproduction") is not False or source_denied.get("result_id") != run["result_id"] or source_denied.get("repetition") != run["repetition"] or source_denied.get("configuration_id") != run["configuration_id"]:
        _fail(f"repetition {run['repetition']} source-denied receipt identity differs")
    return {
        "status": "partially_recomputed",
        "recomputed": {
            "intact_visible_response_count": len(responses),
            "intact_correct_matches_measurement": True,
            "selected_observer_id": selected["selected_observer_id"],
            "selected_canary_occurrences_in_decoded": len(decoded_matches),
            "selected_canary_occurrences_in_observer": len(observer_matches),
            "receipt_intact_correct": intact_correct,
        },
        "source_attested": {
            "damaged_correct": selected["damaged_correct"],
            "loss_state": selected["loss_state"],
            "selected_native_table": selected["selected_native_table"],
            "selected_native_row_id": selected["selected_native_row_id"],
            "native_mutation": "source-denied receipt; damaged native bytes are withheld",
        },
        "native_decode_recomputed": False,
    }


def verify_semantics(root: Path, manifest_info: Mapping[str, Any], run_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bind semantic derivatives and classify selected-loss evidence."""
    selected_results: list[dict[str, Any]] = []
    manifest_sources = manifest_info["document"].get("source_packages")
    if not isinstance(manifest_sources, list) or len(manifest_sources) != 3:
        _fail("bundle manifest source_packages must contain three entries")
    source_by_result: dict[str, Mapping[str, Any]] = {}
    for item in manifest_sources:
        if not isinstance(item, Mapping):
            _fail("bundle manifest source package entry is malformed")
        result_id = _text(item.get("result_id"), "bundle source package result_id")
        if result_id in source_by_result:
            _fail("bundle manifest source package result IDs are duplicated")
        source_by_result[result_id] = item
    for run_result in run_results:
        run = run_result["run"]
        semantic = _semantic_paths(root / "runs" / str(run["repetition"]))
        summary = semantic["summary"]
        public_manifest = semantic["public_manifest"]
        redaction = semantic["redaction"]
        portability = semantic["portability"]
        correction = semantic["correction"]
        source_denied = semantic["source_denied"]
        if summary.get("run_id") != run["run_id"] or summary.get("result_id") != run["result_id"] or summary.get("configuration_id") != run["configuration_id"] or summary.get("repetition") != run["repetition"]:
            _fail(f"repetition {run['repetition']} semantic summary identity differs")
        if public_manifest.get("source_result_id") != run["result_id"] or public_manifest.get("independent_reproduction") is not False or public_manifest.get("raw_publication_state") != "withheld" or public_manifest.get("replayable_as_raw_package") is not False:
            _fail(f"repetition {run['repetition']} public semantic manifest qualification differs")
        if correction.get("independent_reproduction") is not False or correction.get("reason") != "missing_decoder_runtime" or correction.get("canonical_decode_equal") is not True or correction.get("measurement_equal") is not True or correction.get("survival_score_equal") is not True:
            _fail(f"repetition {run['repetition']} correction receipt qualification differs")
        _validate_portability_receipt(
            portability,
            run=run,
            decoded=semantic["decoded"],
            observer=semantic["observer"],
            public_manifest=public_manifest,
        )
        if source_denied.get("schema_version") != "session-bench-opencode-source-denied-run-receipt-v1":
            _fail(f"repetition {run['repetition']} source-denied receipt schema is unsupported")
        public_manifest_raw = (root / "runs" / str(run["repetition"]) / "semantic/public-manifest.json").read_bytes()
        expected_public_manifest_sha = _sha_bytes(public_manifest_raw)
        if redaction.get("public_manifest_sha256") != expected_public_manifest_sha or redaction.get("raw_sqlite_withheld") is not True or redaction.get("raw_runtime_withheld") is not True:
            _fail(f"repetition {run['repetition']} redaction receipt does not bind public manifest or withholding")
        if run["privacy"]["public_derivative_sha256"] != expected_public_manifest_sha:
            _fail(f"repetition {run['repetition']} bundle privacy digest differs from semantic manifest")
        source_entry = source_by_result.get(run["result_id"])
        if source_entry is None:
            _fail(f"repetition {run['repetition']} result ID is absent from bundle source packages")
        if source_entry.get("package_id") != summary.get("package_id") or source_entry.get("public_derivative_manifest_sha256") != expected_public_manifest_sha or source_entry.get("repetition") != run["repetition"]:
            _fail(f"repetition {run['repetition']} bundle source package binding differs")
        selected_results.append(_selected_loss_from_public_semantics(run_result, semantic))
    return selected_results


def _source_access_status(path: Path) -> str:
    """Probe a caller-declared source path without retaining its name in a receipt."""
    try:
        # Probe the supplied path directly.  Even ``Path.is_dir()`` performs a
        # stat that raises EPERM under the source-denial sandbox, before the
        # result can be classified as the expected permission denial.
        descriptor = os.open(path, os.O_RDONLY)
    except PermissionError:
        return "permission_denied"
    except FileNotFoundError:
        return "not_found"
    except OSError as exc:
        return f"os_error:{type(exc).__name__}"
    else:
        os.close(descriptor)
        return "readable"


def _is_isolated_environment() -> dict[str, Any]:
    home = os.environ.get("HOME")
    tmpdir = os.environ.get("TMPDIR")
    pythonpath = os.environ.get("PYTHONPATH")
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    home_isolated = bool(home) and Path(home).expanduser().resolve() != account_home
    tmp_isolated = bool(tmpdir) and Path(tmpdir).expanduser().resolve() != Path("/tmp").resolve()
    return {
        "home_isolated": home_isolated,
        "tmp_isolated": tmp_isolated,
        "pythonpath_isolated": pythonpath in (None, ""),
        "network_used": False,
        "vendor_executable_accessed": False,
        "native_decode_recomputed": False,
    }


def _receipt(
    *,
    root: Path,
    manifest_info: Mapping[str, Any],
    bundle_info: Mapping[str, Any],
    run_results: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, Any],
    selected_loss: Sequence[Mapping[str, Any]],
    privacy_findings: Sequence[str],
    source_access: str,
    fresh_copy: bool,
    model_id: str,
) -> dict[str, Any]:
    packet_name = root.name or "public-configuration-packet"
    runs = [
        {
            "repetition": item["run"]["repetition"],
            "run_id": item["run"]["run_id"],
            "result_id": item["run"]["result_id"],
            "overall": _display_decimal(item["score"]["overall"]),
            "overall_exact": _fraction_text(item["score"]["overall"]),
            "metric_count": len(item["score"]["metric_values"]),
            "rankable": item["score"]["rankable"],
        }
        for item in sorted(run_results, key=lambda value: value["run"]["repetition"])
    ]
    return {
        "schema_version": "session-bench-public-configuration-independent-reproduction-receipt-v1",
        "receipt_id": f"session-bench-independent-public-reproduction-{bundle_info['configuration_id']}",
        "verified_on": date.today().isoformat(),
        "independent_reproduction": True,
        "qualification": "independent public-semantic and score-input reproduction; native decode not established",
        "reproducer_model_id": model_id,
        "packet": {
            "name": packet_name,
            "configuration_id": bundle_info["configuration_id"],
            "bundle_id": bundle_info["bundle"]["id"],
            "bundle_sha256": bundle_info["bundle_sha256"],
            "manifest_content_sha256": manifest_info["document"]["content_sha256"],
            "manifest_file_count": manifest_info["file_count"],
        },
        "isolation": {
            "fresh_temp_copy": fresh_copy,
            "original_public_packet": source_access,
            **_is_isolated_environment(),
        },
        "recomputed": {
            "manifest_integrity": {
                "verified": True,
                "file_count": manifest_info["file_count"],
                "content_sha256": manifest_info["document"]["content_sha256"],
            },
            "run_scores": runs,
            "configuration_aggregation": {
                "verified": True,
                "overall": _display_decimal(aggregate["overall"]),
                "overall_exact": _fraction_text(aggregate["overall"]),
                "range": [_display_decimal(value) for value in aggregate["overall_range"]],
                "sensitivity": {vector: _display_decimal(value) for vector, value in aggregate["sensitivity"].items()},
            },
            "result_id_binding": {
                "verified": True,
                "result_ids": list(bundle_info["bundle"]["result_ids"]),
            },
            "selected_loss_public_semantics": list(selected_loss),
            "privacy_scan": {"verified": not privacy_findings, "findings": list(privacy_findings), "file_count": len(_ordinary_files(root))},
        },
        "source_attested": [
            "native SQLite/WAL/SHM decode and canonical decode equality",
            "source-denied replay and vendor/network denial receipts",
            "selected native table/row mutation and damaged result count",
            "redaction claims for withheld raw native files and replay runtime",
            "historical source package digests and correction provenance",
        ],
        "native_decode_recomputed": False,
        "raw_native_available": False,
        "raw_replay_runtime_available": False,
    }


def verify_packet(packet: str | Path, *, source_deny: str | Path | None = None, fresh_copy: bool = False, model_id: str = "not-provided") -> dict[str, Any]:
    """Run every public-only check and return an additive receipt object."""
    root = Path(packet).resolve(strict=True)
    manifest_info = verify_manifest(root)
    bundle_info = _validate_bundle(root, manifest_info)
    run_results = verify_score_inputs(root, bundle_info)
    aggregate = verify_configuration_aggregate(root, bundle_info, run_results)
    selected_loss = verify_semantics(root, manifest_info, run_results)
    privacy_findings = _privacy_findings(root)
    if privacy_findings:
        _fail(f"public packet privacy scan found {privacy_findings}")
    # Do not resolve the denied path here.  ``Path.resolve`` performs filesystem
    # reads on macOS and therefore raises EPERM before the verifier can record
    # that the sandbox successfully denied the original packet.
    denied_path = None if source_deny is None else Path(os.path.abspath(os.fspath(source_deny)))
    source_access = "not_requested" if denied_path is None else _source_access_status(denied_path)
    if source_deny is not None and source_access != "permission_denied":
        _fail(f"declared original public packet remained {source_access}")
    return _receipt(root=root, manifest_info=manifest_info, bundle_info=bundle_info, run_results=run_results, aggregate=aggregate, selected_loss=selected_loss, privacy_findings=privacy_findings, source_access=source_access, fresh_copy=fresh_copy, model_id=_text(model_id, "model_id"))


def _write_receipt(path: Path, receipt: Mapping[str, Any], packet: Path) -> None:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(packet.resolve())
    except ValueError:
        pass
    else:
        _fail("receipt output must be outside the immutable packet")
    if resolved.exists() or resolved.is_symlink():
        _fail(f"receipt output already exists: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(_canonical(receipt) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", required=True, type=Path, help="sanitized public configuration packet")
    parser.add_argument("--receipt", type=Path, help="write an additive receipt outside the immutable packet")
    parser.add_argument("--source-deny", type=Path, help="path that must be unreadable to this process")
    parser.add_argument("--fresh-copy", action="store_true", help="record that the packet was copied into a fresh temporary directory")
    parser.add_argument("--model-id", default="not-provided", help="independent verifier model identifier to record")
    args = parser.parse_args(argv)
    try:
        receipt = verify_packet(args.packet, source_deny=args.source_deny, fresh_copy=args.fresh_copy, model_id=args.model_id)
        if args.receipt:
            _write_receipt(args.receipt.resolve(strict=False), receipt, Path(args.packet).resolve())
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (OSError, VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"public packet verification refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
