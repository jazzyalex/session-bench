"""Evidence-bound inputs for live or reportable Session Survival Scores.

``survival_metrics`` deliberately accepts a small canonical measurement document so
constructed controls can test scoring independently of a capture format.  That shape
is insufficient for a live or public result: each metric must be tied back to an
observer and to a native artifact location.  This module supplies that stricter outer
contract.  It never decodes a store or reads a vendor root.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .survival_metrics import METRICS, RunScore, score_run, validate_input


EVIDENCE_SCHEMA_VERSION = "session-bench-survival-evidence-v1"
PROSPECTIVE_EVIDENCE_SCHEMA_VERSION = "session-bench-survival-evidence-prospective-v1"
PROTOCOL_VERSION = "1.0-survival"
WORKLOAD_VERSION = "1.0-survival-workload"
RUBRIC_VERSION = "1.0-survival-rubric"

CONFIGURATION_IDS = frozenset(
    {"codex-cli", "codex-desktop", "cursor-cli", "cursor-desktop", "opencode-cli"}
)
# Prospective five-configuration cohort for the NEW clearly named campaign plan.
# Frozen CONFIGURATION_IDS above is unchanged; prospective evidence must opt in
# explicitly and remains score-ineligible until a future scored edition.
PROSPECTIVE_CONFIGURATION_IDS = frozenset(
    {"codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# The original v1 evidence wrapper predates the public report's complete
# identity tuple.  Keep the extension optional for old evidence, but reject
# unknown fields and preserve every supplied value.  ``None`` is intentional:
# a missing model/provider/etc. is evidence that the current capture did not
# bind that part of the identity, not a value to infer from a configuration ID.
RUN_IDENTITY_FIELDS = frozenset(
    {
        "provider",
        "harness",
        "surface",
        "execution_mode",
        "os",
        "build",
        "model",
        "configuration",
        "protocol_version",
        "workload_version",
        "observer_schema_version",
        "rubric_version",
    }
)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label}: wrong fields (missing={missing}, extra={extra})")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _identity(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(value, {"id", "sha256"}, label)
    return {
        "id": _identifier(value["id"], f"{label}.id"),
        "sha256": _digest(value["sha256"], f"{label}.sha256"),
    }


def _locator(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _exact_keys(value, {"artifact_id", "artifact_sha256", "record_location"}, label)
    return {
        "artifact_id": _identifier(value["artifact_id"], f"{label}.artifact_id"),
        "artifact_sha256": _digest(value["artifact_sha256"], f"{label}.artifact_sha256"),
        # A retained fact uses a native record location. An absence assertion uses
        # the explicit searched scope (for example, ``scan:native/session.sqlite``).
        "record_location": _identifier(
            value["record_location"], f"{label}.record_location"
        ),
    }


def _run_identity(value: Any, label: str = "identity") -> dict[str, str | None]:
    """Validate an optional bound provider/harness/model identity envelope."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - RUN_IDENTITY_FIELDS
    if unknown:
        raise ValueError(f"{label}: wrong fields (extra={sorted(unknown)})")
    normalized: dict[str, str | None] = {
        field: None for field in RUN_IDENTITY_FIELDS
    }
    for field, raw in value.items():
        if raw is not None:
            normalized[field] = _identifier(raw, f"{label}.{field}")
    # These versions are already frozen top-level fields.  If an identity
    # envelope repeats them, it must bind the same versions exactly.
    for field, expected in (
        ("protocol_version", PROTOCOL_VERSION),
        ("workload_version", WORKLOAD_VERSION),
        ("rubric_version", RUBRIC_VERSION),
    ):
        if normalized[field] is not None and normalized[field] != expected:
            raise ValueError(f"{label}.{field} must be {expected!r}")
    return normalized


def _metric_evidence(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("metric_evidence must be an array")
    expected = list(METRICS)
    if len(rows) != len(expected):
        raise ValueError("metric_evidence must contain every required metric exactly once")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        label = f"metric_evidence[{index}]"
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} must be an object")
        _exact_keys(row, {"metric_id", "observer_ids", "native_locators"}, label)
        metric_id = row["metric_id"]
        if metric_id not in METRICS:
            raise ValueError(f"{label}.metric_id is unknown: {metric_id!r}")
        if metric_id in ids:
            raise ValueError(f"duplicate metric evidence: {metric_id}")
        ids.add(metric_id)
        observer_ids = row["observer_ids"]
        if not isinstance(observer_ids, list) or not observer_ids:
            raise ValueError(f"{label}.observer_ids must be a non-empty array")
        normalized_observers = [
            _identifier(value, f"{label}.observer_ids") for value in observer_ids
        ]
        if len(set(normalized_observers)) != len(normalized_observers):
            raise ValueError(f"{label}.observer_ids must be unique")
        locators = row["native_locators"]
        if not isinstance(locators, list) or not locators:
            raise ValueError(f"{label}.native_locators must be a non-empty array")
        normalized_locators = [
            _locator(value, f"{label}.native_locators") for value in locators
        ]
        locator_keys = {
            (item["artifact_id"], item["artifact_sha256"], item["record_location"])
            for item in normalized_locators
        }
        if len(locator_keys) != len(normalized_locators):
            raise ValueError(f"{label}.native_locators must be unique")
        result.append(
            {
                "metric_id": metric_id,
                "observer_ids": normalized_observers,
                "native_locators": normalized_locators,
            }
        )
    if set(ids) != set(expected):
        raise ValueError("metric_evidence must contain the exact survival metric set")
    return result


def _validate_evidence_input(
    document: Mapping[str, Any],
    *,
    expected_schema_version: str,
    allowed_configurations: frozenset[str],
) -> dict[str, Any]:
    """Validate either a reportable or deliberately non-reportable evidence document."""

    if not isinstance(document, Mapping):
        raise ValueError("survival evidence input must be an object")
    expected_fields = {
            "schema_version",
            "protocol_version",
            "workload_version",
            "rubric_version",
            "run_id",
            "capture_id",
            "evaluation_id",
            "configuration_id",
            "repetition",
            "measurement",
            "observer",
            "native_manifest",
            "decoder",
            "metric_evidence",
        }
    _exact_keys(
        document,
        expected_fields | ({"identity"} if "identity" in document else set()),
        "survival evidence input",
    )
    if document["schema_version"] != expected_schema_version:
        raise ValueError(f"schema_version must be {expected_schema_version!r}")
    if document["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError(f"protocol_version must be {PROTOCOL_VERSION!r}")
    if document["workload_version"] != WORKLOAD_VERSION:
        raise ValueError(f"workload_version must be {WORKLOAD_VERSION!r}")
    if document["rubric_version"] != RUBRIC_VERSION:
        raise ValueError(f"rubric_version must be {RUBRIC_VERSION!r}")
    configuration_id = _identifier(document["configuration_id"], "configuration_id")
    if configuration_id not in allowed_configurations:
        raise ValueError(
            f"configuration_id must be one of {sorted(allowed_configurations)}"
        )
    run_id = _identifier(document["run_id"], "run_id")
    capture_id = _identifier(document["capture_id"], "capture_id")
    evaluation_id = _identifier(document["evaluation_id"], "evaluation_id")
    measurement = validate_input(document["measurement"])
    if (
        measurement["run_id"] != run_id
        or measurement["configuration_id"] != configuration_id
        or measurement["repetition"] != document["repetition"]
    ):
        raise ValueError(
            "measurement run_id, configuration_id, and repetition must match evidence input"
        )
    return {
        "schema_version": expected_schema_version,
        "protocol_version": PROTOCOL_VERSION,
        "workload_version": WORKLOAD_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": run_id,
        "capture_id": capture_id,
        "evaluation_id": evaluation_id,
        "configuration_id": configuration_id,
        "repetition": measurement["repetition"],
        "measurement": measurement,
        "observer": _identity(document["observer"], "observer"),
        "native_manifest": _identity(document["native_manifest"], "native_manifest"),
        "decoder": _identity(document["decoder"], "decoder"),
        "identity": _run_identity(document["identity"]) if "identity" in document else None,
        "metric_evidence": _metric_evidence(document["metric_evidence"]),
    }


def validate_evidence_input(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a scoreable, frozen-cohort evidence input."""

    return _validate_evidence_input(
        document,
        expected_schema_version=EVIDENCE_SCHEMA_VERSION,
        allowed_configurations=CONFIGURATION_IDS,
    )


def validate_prospective_evidence_input(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate prospective evidence without making it admissible to the scorer."""

    return _validate_evidence_input(
        document,
        expected_schema_version=PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        allowed_configurations=PROSPECTIVE_CONFIGURATION_IDS,
    )


def score_evidence_run(document: Mapping[str, Any]) -> RunScore:
    """Validate evidence binding before returning a score for a reportable run."""

    evidence = validate_evidence_input(document)
    return score_run(evidence["measurement"])
