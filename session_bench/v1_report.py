"""Render the local Session-Bench v1 report-card prototype.

This module is intentionally a small, dependency-free presentation layer.  It
does not discover session stores, contact a service, or score a product.  The
caller supplies a public report payload and this module renders that payload
with the evidence boundary still visible.  The constructed fixture under
``fixtures/scenarios/survival-v1`` is the only data used by the local demo.

The renderer keeps two ideas separate:

* a row may expose useful *measured* category facts while still being
  unranked; and
* a recommendation is shown only when it was supplied by the report producer,
  its target is eligible, and every cited locator exists in that target.

That separation prevents a partial row from quietly becoming a leaderboard
winner or a prose suggestion from becoming a citable recommendation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import html
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping, Sequence


REPORT_SCHEMA = "session-bench-v1-public-report"
AUTHORITATIVE_REPORT_SCHEMA = "session-bench-v1-authoritative-report"
AUTHORITATIVE_REPORT_KIND = "authoritative-public-report"
DEFAULT_CATEGORIES: tuple[dict[str, Any], ...] = (
    {"id": "record_fidelity", "name": "Record fidelity", "weight": 30},
    {"id": "causality_context", "name": "Causality & context", "weight": 20},
    {"id": "usage_attribution", "name": "Usage & attribution", "weight": 15},
    {"id": "portability_openness", "name": "Portability & openness", "weight": 20},
    {"id": "durability_signal", "name": "Durability & signal", "weight": 15},
)

# The palette is deliberately warm/editorial: coral and blue carry the visual
# hierarchy while the remaining colors are reserved for the category bars.
PAPER = "#f4efe6"
PAPER_DARK = "#ebe3d6"
INK = "#182633"
MUTED = "#647078"
LINE = "#d7cec0"
CORAL = "#dc5e4c"
BLUE = "#2e668c"
MOSS = "#627c57"
OCHRE = "#bd8a28"
PLUM = "#805d78"
TEAL = "#2c837f"
UNKNOWN = "#c3b9ad"

CATEGORY_COLORS = (CORAL, BLUE, OCHRE, TEAL, PLUM)

# These imports are intentionally tied to the scoring/recommendation layers.
# The ordinary three-row page below remains a renderer-only fixture, while the
# authoritative builder accepts only these validated/scored objects.
from .v1_public_score import (  # noqa: E402  (constants are part of the contract)
    CLI_DESKTOP_PAIRS,
    ImmutablePublicBundle,
    ReproductionReceipt,
    PUBLIC_CATEGORY_POINTS,
    PUBLIC_METRICS,
    SURVIVAL_METRICS,
    PublicEvidenceLocator,
    TARGET_CONFIGURATIONS,
    PublicConfigurationScore,
    PublicMetricEvidence,
    PublicRunScore,
    PUBLIC_IDENTITY_REQUIRED_FIELDS,
    PUBLIC_IDENTITY_STABLE_FIELDS,
    public_identity_value_bound,
    attempted_public_cohort,
    public_identity_blockers,
    qualified_public_cohort,
)
from .survival_metrics import ALLOWED_STATES, RESOLVED_STATES, display_decimal  # noqa: E402
from .recommendations import (  # noqa: E402
    RECOMMENDATION_SCHEMA_VERSION,
    RecommendationOutputs,
    USE_CASES,
)


ATTEMPT_STATES = frozenset({"blocked", "preflight", "calibration_failed", "unranked"})


@dataclass(frozen=True)
class PublicConfigurationAttempt:
    """An attempted target that has not earned a configuration score.

    ``PublicConfigurationScore`` deliberately remains a three-repetition
    aggregate. This separate value is the report-path carrier for a target
    that stopped in preflight/calibration or has only zero, one, or two
    evaluated repetitions. Its runs, when present, are real
    ``PublicRunScore`` values; callers must never pad this tuple.
    """

    configuration_id: str
    status: str
    runs: tuple[Any, ...] = ()
    reason_ids: tuple[str, ...] = ()
    blocker_evidence: tuple[Any, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def attempt_state(self) -> str:
        """Compatibility spelling for consumers that call this an attempt state."""

        return self.status


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any, fallback: str = "Unknown") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value if value else fallback
    return str(value)


def _esc(value: Any, fallback: str = "Unknown") -> str:
    return html.escape(_text(value, fallback), quote=True)


def _slug(value: Any, fallback: str = "item") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", _text(value, fallback).lower()).strip("-")
    return result or fallback


def _score(value: Any) -> float | None:
    number = _finite_number(value)
    if number is None:
        return None
    return max(0.0, min(100.0, number))


def _score_label(value: Any, *, dash: str = "—") -> str:
    score = _score(value)
    if score is None:
        return dash
    if score.is_integer():
        return f"{score:.0f}"
    return str(Decimal(str(score)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _published_score(value: Any) -> float | None:
    """Return the one-decimal score readers see and ranks must follow."""

    score = _score(value)
    if score is None:
        return None
    return float(Decimal(str(score)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _ellipsize(value: Any, limit: int) -> str:
    text = _text(value)
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


def _percent(value: Any) -> str:
    score = _score(value)
    if score is None:
        return "Unknown"
    return f"{score:.0f}%" if score.is_integer() else f"{score:.1f}%"


def _safe_local_href(value: Any, *, fallback: str | None = None) -> str | None:
    """Allow fragments and report-local relative paths only.

    The report is copied and opened from disk.  A source payload must never be
    able to make the page request a remote resource or navigate out of the
    report directory.
    """

    if not isinstance(value, str) or not value.strip():
        return fallback
    raw = value.strip().replace("\\", "/")
    if raw.startswith(("/", "//")) or "://" in raw:
        return fallback
    if raw.startswith("#"):
        return raw if re.fullmatch(r"#[A-Za-z0-9_.:-]+", raw) else fallback
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts) or ":" in path.parts[0]:
        return fallback
    return f"./{path.as_posix()}"


def _strict_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label}: wrong fields (missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)})"
        )


def _fraction(value: Any, label: str = "value") -> Fraction:
    """Coerce an exact score value while rejecting booleans and non-finite data."""

    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must be finite")
        return Fraction(str(value))
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"{label} must be a finite number") from exc
    raise ValueError(f"{label} must be a finite number")


def _fraction_display(value: Fraction | None) -> float | None:
    """Render exact scorer fractions as JSON numbers for the public artifact."""

    if value is None:
        return None
    return float(value)


def _percent_from_points(points: Fraction | None, maximum: Fraction) -> float | None:
    if points is None:
        return None
    return float(points * 100 / maximum)


def _attempt_reason_ids(attempt: PublicConfigurationAttempt) -> tuple[str, ...]:
    """Normalize the two historical spellings for attempt blocker IDs."""

    values = [*attempt.reason_ids, *attempt.blockers]
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{attempt.configuration_id}: attempt reason IDs must be non-empty strings")
        if value not in result:
            result.append(value)
    return tuple(result)


def _blocker_locator_payload(value: Any, label: str) -> dict[str, Any]:
    """Normalize either a typed public locator or its JSON spelling."""

    if isinstance(value, PublicEvidenceLocator):
        artifact_id = value.artifact_id
        artifact_sha256 = value.artifact_sha256
        record_location = value.record_location
    elif isinstance(value, Mapping):
        if "artifact_id" in value:
            artifact_id = value.get("artifact_id")
            artifact_sha256 = value.get("artifact_sha256")
            record_location = value.get("record_location")
            allowed = {"artifact_id", "artifact_sha256", "record_location"}
            if set(value) not in (allowed - {"record_location"}, allowed):
                raise ValueError(f"{label} has wrong fields")
        elif "id" in value and "sha256" in value:
            artifact_id = value.get("id")
            artifact_sha256 = value.get("sha256")
            record_location = value.get("record_location")
            allowed = {"id", "sha256", "record_location"}
            if set(value) not in (allowed - {"record_location"}, allowed):
                raise ValueError(f"{label} has wrong fields")
        else:
            raise ValueError(f"{label} must identify an artifact")
    else:
        raise ValueError(f"{label} must be a public evidence locator")
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        raise ValueError(f"{label}.artifact_id must be non-empty")
    artifact_sha256 = _sha256(artifact_sha256, f"{label}.artifact_sha256")
    if record_location is not None and (not isinstance(record_location, str) or not record_location.strip()):
        raise ValueError(f"{label}.record_location must be non-empty when present")
    return {
        "artifact_id": artifact_id,
        "artifact_sha256": artifact_sha256,
        **({"record_location": record_location} if record_location is not None else {}),
    }


def _blocker_evidence_payload(value: Any, label: str, *, default_kind: str) -> dict[str, Any]:
    """Normalize one preflight/calibration evidence record for the report."""

    if isinstance(value, str):
        value = {"id": value}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object or evidence ID")
    evidence_id = value.get("id", value.get("evidence_id"))
    kind = value.get("kind", value.get("type", default_kind))
    detail = value.get("detail", value.get("reason", ""))
    observer_ids = value.get("observer_ids", [])
    native_locators = value.get("native_locators", [])
    allowed = {"id", "evidence_id", "kind", "type", "detail", "reason", "observer_ids", "native_locators"}
    if not set(value) <= allowed:
        raise ValueError(f"{label} has unknown fields")
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        raise ValueError(f"{label}.id must be non-empty")
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError(f"{label}.kind must be non-empty")
    if not isinstance(detail, str):
        raise ValueError(f"{label}.detail must be a string")
    if not isinstance(observer_ids, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in observer_ids):
        raise ValueError(f"{label}.observer_ids must contain strings")
    if len(set(observer_ids)) != len(observer_ids):
        raise ValueError(f"{label}.observer_ids must be unique")
    if not isinstance(native_locators, (list, tuple)):
        raise ValueError(f"{label}.native_locators must be an array")
    locators = [_blocker_locator_payload(locator, f"{label}.native_locators[{index}]") for index, locator in enumerate(native_locators)]
    identities = {(item["artifact_id"], item["artifact_sha256"], item.get("record_location")) for item in locators}
    if len(identities) != len(locators):
        raise ValueError(f"{label}.native_locators must be unique")
    return {
        "id": evidence_id,
        "kind": kind,
        "detail": detail,
        "observer_ids": list(observer_ids),
        "native_locators": locators,
    }


def _attempt_evidence_payloads(attempt: PublicConfigurationAttempt) -> list[dict[str, Any]]:
    raw = attempt.blocker_evidence
    if not isinstance(raw, (list, tuple)):
        raise ValueError(f"{attempt.configuration_id}: blocker_evidence must be an array")
    result = [
        _blocker_evidence_payload(item, f"{attempt.configuration_id}.blocker_evidence[{index}]", default_kind=attempt.status)
        for index, item in enumerate(raw)
    ]
    if len({item["id"] for item in result}) != len(result):
        raise ValueError(f"{attempt.configuration_id}: blocker evidence IDs must be unique")
    return result


def _attempt_metric_evidence(runs: Sequence[Any], configuration_id: str) -> dict[int, dict[str, dict[str, Any]]]:
    """Serialize the exact evidence for the real runs supplied by an attempt."""

    result: dict[int, dict[str, dict[str, Any]]] = {}
    repetitions: set[int] = set()
    for run in sorted(runs, key=lambda item: item.repetition):
        if not isinstance(run, PublicRunScore):
            raise TypeError(f"{configuration_id}: attempt runs must be PublicRunScore values")
        if not isinstance(getattr(run, "repetition", None), int) or run.repetition not in {1, 2, 3}:
            raise ValueError(f"{configuration_id}: attempt repetitions must be 1, 2, or 3")
        if run.repetition in repetitions:
            raise ValueError(f"{configuration_id}: attempt repetitions must be unique")
        repetitions.add(run.repetition)
        raw = getattr(run, "metric_evidence", None)
        if not isinstance(raw, Mapping) or set(raw) != set(PUBLIC_METRICS):
            raise ValueError(f"{configuration_id} repetition {run.repetition}: attempt requires typed evidence for all 31 metrics")
        result[run.repetition] = {
            metric_id: _metric_evidence_payload(raw[metric_id], metric_id, f"{configuration_id} repetition {run.repetition} {metric_id}")
            for metric_id in PUBLIC_METRICS
        }
    return result


def _validate_attempt(attempt: PublicConfigurationAttempt) -> PublicConfigurationAttempt:
    if not isinstance(attempt.configuration_id, str) or not attempt.configuration_id:
        raise ValueError("attempt configuration_id must be a non-empty string")
    if attempt.configuration_id not in TARGET_CONFIGURATIONS:
        raise ValueError(f"unknown authoritative target configuration: {attempt.configuration_id}")
    if attempt.status not in ATTEMPT_STATES:
        raise ValueError(f"{attempt.configuration_id}: unsupported attempt status {attempt.status!r}")
    if not isinstance(attempt.runs, (list, tuple)):
        raise ValueError(f"{attempt.configuration_id}: attempt runs must be an array")
    if len(attempt.runs) > 2:
        raise ValueError(f"{attempt.configuration_id}: an attempted row may contain at most two runs; use PublicConfigurationScore for three")
    _attempt_metric_evidence(attempt.runs, attempt.configuration_id)
    _attempt_reason_ids(attempt)
    evidence = _attempt_evidence_payloads(attempt)
    if not _attempt_reason_ids(attempt) and not evidence and not attempt.runs:
        raise ValueError(f"{attempt.configuration_id}: blocked attempt requires a reason or blocker evidence")
    # Force the payload validation while the typed object is still at the API
    # boundary. The returned object remains the caller's real attempt; no
    # score or pseudo-run is synthesized.
    del evidence
    return attempt


def _configuration_metric_ids(configuration: PublicConfigurationScore) -> tuple[str, ...]:
    """Require the full frozen 31-cell public set on every repetition."""

    expected = set(PUBLIC_METRICS)
    if len(configuration.runs) != 3:
        raise ValueError(f"{configuration.configuration_id}: authoritative rows require three runs")
    for run in configuration.runs:
        actual = set(run.metrics)
        if actual != expected:
            raise ValueError(
                f"{configuration.configuration_id} repetition {run.repetition}: "
                f"requires exact 31 metric IDs (missing={sorted(expected - actual)}, "
                f"extra={sorted(actual - expected)})"
            )
    return tuple(PUBLIC_METRICS)


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _metric_evidence_payload(
    evidence: PublicMetricEvidence,
    metric_id: str,
    label: str,
) -> dict[str, Any]:
    """Serialize one scorer-retained PublicMetricEvidence without flattening locators."""

    if not isinstance(evidence, PublicMetricEvidence) or evidence.metric_id != metric_id:
        raise ValueError(f"{label} must be the typed PublicMetricEvidence for {metric_id}")
    if evidence.state not in ALLOWED_STATES:
        raise ValueError(f"{label} has an unsupported evidence state")
    observers = list(evidence.observer_ids)
    if not observers or len(set(observers)) != len(observers) or any(not isinstance(item, str) or not item for item in observers):
        raise ValueError(f"{label} must retain non-empty observer IDs")
    locators: list[dict[str, Any]] = []
    seen_locators: set[tuple[str, str, str | None]] = set()
    for locator in evidence.native_locators:
        if not isinstance(locator, PublicEvidenceLocator):
            raise ValueError(f"{label} must retain typed PublicEvidenceLocator values")
        artifact_id = locator.artifact_id
        artifact_sha256 = _sha256(locator.artifact_sha256, f"{label}.artifact_sha256")
        record_location = locator.record_location
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError(f"{label}.artifact_id must be non-empty")
        if record_location is not None and (not isinstance(record_location, str) or not record_location):
            raise ValueError(f"{label}.record_location must be non-empty when present")
        identity = (artifact_id, artifact_sha256, record_location)
        if identity in seen_locators:
            raise ValueError(f"{label} native locators must be unique")
        seen_locators.add(identity)
        locators.append({
            "artifact_id": artifact_id,
            "artifact_sha256": artifact_sha256,
            **({"record_location": record_location} if record_location is not None else {}),
        })
    if not locators:
        raise ValueError(f"{label} must retain at least one native locator")
    return {
        "state": evidence.state,
        "observer_ids": observers,
        "native_locators": locators,
    }


def _configuration_metric_evidence(configuration: PublicConfigurationScore) -> dict[int, dict[str, dict[str, Any]]]:
    """Validate and expose the typed per-run metric evidence retained by the scorer."""

    result: dict[int, dict[str, dict[str, Any]]] = {}
    for run in sorted(configuration.runs, key=lambda item: item.repetition):
        raw = getattr(run, "metric_evidence", None)
        if not isinstance(raw, Mapping) or set(raw) != set(PUBLIC_METRICS):
            raise ValueError(
                f"{configuration.configuration_id} repetition {run.repetition}: "
                "authoritative rows require typed evidence for all 31 metrics"
            )
        result[run.repetition] = {
            metric_id: _metric_evidence_payload(
                raw[metric_id], metric_id,
                f"{configuration.configuration_id} repetition {run.repetition} {metric_id}",
            )
            for metric_id in PUBLIC_METRICS
        }
    if set(result) != {1, 2, 3}:
        raise ValueError(f"{configuration.configuration_id}: authoritative metric evidence requires repetitions 1, 2, 3")
    return result


def _binding_payloads(configuration: PublicConfigurationScore) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate typed bundle/receipt provenance and return their full payloads."""

    bundle = getattr(configuration, "bundle", None)
    receipt = getattr(configuration, "reproduction_receipt", None)
    if bundle is None and receipt is None:
        return None, None
    if not isinstance(bundle, ImmutablePublicBundle) or not isinstance(receipt, ReproductionReceipt):
        raise ValueError(f"{configuration.configuration_id}: bundle and reproduction receipt must use scorer types")
    runs = tuple(sorted(configuration.runs, key=lambda item: item.repetition))
    result_ids = tuple(run.result_id for run in runs)
    if any(not isinstance(value, str) or not value for value in result_ids) or len(set(result_ids)) != 3:
        raise ValueError(f"{configuration.configuration_id}: bundle provenance requires three unique run result IDs")
    expected_result_ids = tuple(sorted(result_ids))
    if not isinstance(bundle.id, str) or not bundle.id:
        raise ValueError(f"{configuration.configuration_id}: bundle id is missing")
    bundle_sha256 = _sha256(bundle.sha256, f"{configuration.configuration_id}.bundle.sha256")
    if bundle.immutable is not True or bundle.public is not True or tuple(bundle.result_ids) != expected_result_ids:
        raise ValueError(f"{configuration.configuration_id}: bundle does not bind its exact immutable public results")
    if not isinstance(receipt.id, str) or not receipt.id:
        raise ValueError(f"{configuration.configuration_id}: reproduction receipt id is missing")
    receipt_sha256 = _sha256(receipt.sha256, f"{configuration.configuration_id}.reproduction_receipt.sha256")
    receipt_bundle_sha256 = _sha256(receipt.bundle_sha256, f"{configuration.configuration_id}.reproduction_receipt.bundle_sha256")
    if (
        receipt_bundle_sha256 != bundle_sha256
        or receipt.offline_recomputed is not True
        or receipt.verified is not True
        or tuple(receipt.result_ids) != expected_result_ids
    ):
        raise ValueError(f"{configuration.configuration_id}: receipt does not deterministically recompute the exact bundle")
    return (
        {
            "id": bundle.id,
            "sha256": bundle_sha256,
            "immutable": True,
            "public": True,
            "result_ids": list(bundle.result_ids),
        },
        {
            "id": receipt.id,
            "sha256": receipt_sha256,
            "bundle_sha256": receipt_bundle_sha256,
            "offline_recomputed": True,
            "verified": True,
            "result_ids": list(receipt.result_ids),
        },
    )


def _bound_public_run(run: Any) -> bool:
    """Whether the public score carries the identity needed for citation."""

    return all(
        isinstance(getattr(run, field, None), str) and bool(getattr(run, field))
        for field in ("build", "collected_on", "result_id", "survival_evaluation_id")
    )


def _serialized_identity_bound(runs: Sequence[Mapping[str, Any]], configuration_id: str) -> bool:
    """Recheck the citation identity after a report is serialized or edited."""
    if not runs:
        return False
    identities: list[Mapping[str, Any]] = []
    expected_surface = "desktop" if configuration_id.endswith("-desktop") else "cli"
    for run in runs:
        identity = run.get("identity")
        if not isinstance(identity, Mapping):
            return False
        for field in PUBLIC_IDENTITY_REQUIRED_FIELDS:
            serialized_field = "os" if field == "os_name" else field
            value = identity.get(serialized_field)
            if not public_identity_value_bound(value):
                return False
        if identity.get("surface") != expected_surface or identity.get("unavailable") != []:
            return False
        if any(identity.get(field) != run.get(field) for field in ("build", "collected_on", "result_id")):
            return False
        if identity.get("evaluation_id") != run.get("evaluation_id"):
            return False
        identities.append(identity)
    for field in PUBLIC_IDENTITY_STABLE_FIELDS:
        serialized_field = "os" if field == "os_name" else field
        if len({identity[serialized_field] for identity in identities}) != 1:
            return False
    return True


def _derived_verification(configuration: PublicConfigurationScore) -> tuple[str, dict[str, bool], tuple[str, ...]]:
    """Derive the public badge from scored facts, never from display text."""

    metric_ids_ok = True
    try:
        _configuration_metric_ids(configuration)
    except ValueError:
        metric_ids_ok = False
    repetition_ok = len(configuration.runs) == 3 and {run.repetition for run in configuration.runs} == {1, 2, 3}
    all_resolved = False
    any_resolved = False
    try:
        typed_metric_evidence = _configuration_metric_evidence(configuration)
        all_resolved = metric_ids_ok and all(
            all(typed_metric_evidence[run.repetition][metric_id]["state"] in RESOLVED_STATES for metric_id in PUBLIC_METRICS)
            for run in configuration.runs
        )
        any_resolved = any(
            typed_metric_evidence[run.repetition][metric_id]["state"] in RESOLVED_STATES
            for run in configuration.runs
            for metric_id in PUBLIC_METRICS
        )
    except (KeyError, ValueError):
        typed_metric_evidence = {}
    portable_ok = bool(configuration.portable_gate) and all(bool(run.portable_gate) for run in configuration.runs)
    identity_blockers = public_identity_blockers(configuration.runs)
    evidence_bound = bool(configuration.runs) and not identity_blockers and all(_bound_public_run(run) for run in configuration.runs)
    try:
        bundle_payload, receipt_payload = _binding_payloads(configuration)
    except ValueError:
        bundle_payload, receipt_payload = None, None
        typed_metric_evidence = {}
    bundle_bound = bundle_payload is not None
    receipt_bound = receipt_payload is not None
    evidence_bound = evidence_bound and bool(typed_metric_evidence)
    facts = {
        "three_repetitions": repetition_ok,
        "exact_metric_set": metric_ids_ok,
        "all_metrics_resolved": all_resolved,
        "portable_gate": portable_ok,
        "evidence_bound": evidence_bound,
        "immutable_public_bundle": bundle_bound,
        "reproduction_receipt": receipt_bound,
        "any_metric_resolved": any_resolved,
    }
    # A resolved portability loss is a scored result, not missing evidence. The
    # portable gate remains visible for use-case qualification and diagnostics,
    # but it is not a hard gate for the public overall rank.
    if repetition_ok and metric_ids_ok and all_resolved and evidence_bound and bundle_bound and receipt_bound:
        return "Fully reproduced", facts, ()
    if any_resolved and evidence_bound:
        blockers = tuple(sorted({blocker for run in configuration.runs for blocker in run.blockers} | set(identity_blockers)))
        return "Partially verified", facts, blockers
    blockers = tuple(sorted({blocker for run in configuration.runs for blocker in run.blockers} | set(identity_blockers)))
    return "Unranked", facts, blockers


def _target_descriptor(configuration_id: str) -> dict[str, str]:
    if configuration_id not in TARGET_CONFIGURATIONS:
        raise ValueError(f"unknown authoritative target configuration: {configuration_id}")
    if configuration_id.endswith("-desktop"):
        surface = "Desktop"
    else:
        surface = "CLI"
    family = configuration_id.removesuffix("-desktop").removesuffix("-cli")
    family_name = {"codex": "Codex", "claude": "Claude", "opencode": "OpenCode"}.get(family, family.title())
    return {
        "configuration_id": configuration_id,
        "name": f"{family_name} {surface}",
        "surface": surface,
        "surface_family": family_name,
    }


def _run_metric_state(run: Any, metric_id: str) -> str:
    metric_evidence = getattr(run, "metric_evidence", None)
    evidence = metric_evidence.get(metric_id) if isinstance(metric_evidence, Mapping) else None
    if not isinstance(evidence, PublicMetricEvidence) or evidence.metric_id != metric_id:
        raise ValueError(f"run metric evidence is missing the typed proof for {metric_id}")
    if evidence.state not in ALLOWED_STATES:
        raise ValueError(f"run metric evidence has an unsupported state for {metric_id}")
    return evidence.state


def _authoritative_metric_rows_for_runs(
    configuration_id: str,
    runs: Sequence[PublicRunScore],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evidence_by_repetition = _attempt_metric_evidence(runs, configuration_id)
    for metric_id, spec in PUBLIC_METRICS.items():
        repetitions = []
        for run in sorted(runs, key=lambda item: item.repetition):
            value = run.metrics[metric_id]
            repetitions.append({
                "repetition": run.repetition,
                "state": _run_metric_state(run, metric_id),
                "fraction": _fraction_display(value),
                "points": _fraction_display(None if value is None else spec.points * value),
                "metric_evidence": evidence_by_repetition[run.repetition][metric_id],
                "citation": None if not _bound_public_run(run) else {
                    "evaluation_id": run.survival_evaluation_id,
                    "result_id": run.result_id,
                },
            })
        rows.append({"id": metric_id, "category": spec.category, "maximum_points": _fraction_display(spec.points), "repetitions": repetitions})
    return rows


def _authoritative_metric_rows(configuration: PublicConfigurationScore) -> list[dict[str, Any]]:
    return _authoritative_metric_rows_for_runs(configuration.configuration_id, configuration.runs)


def _authoritative_categories(configuration: PublicConfigurationScore) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for category, maximum in PUBLIC_CATEGORY_POINTS.items():
        points = configuration.categories.get(category)
        ranges = configuration.category_ranges.get(category)
        if points is not None:
            points = _fraction(points, f"{configuration.configuration_id}.{category}")
            if points < 0 or points > maximum:
                raise ValueError(f"{configuration.configuration_id}.{category} exceeds its public category budget")
        if ranges is not None:
            if not isinstance(ranges, tuple) or len(ranges) != 2:
                raise ValueError(f"{configuration.configuration_id}.{category} has an invalid range")
            range_values = [_fraction(item, f"{configuration.configuration_id}.{category}.range") for item in ranges]
            if any(item < 0 or item > maximum for item in range_values) or range_values[0] > range_values[1]:
                raise ValueError(f"{configuration.configuration_id}.{category} has an invalid range")
            range_points: list[float] | None = [_fraction_display(item) for item in range_values]  # type: ignore[list-item]
            range_percent: list[float] | None = [_percent_from_points(item, maximum) for item in range_values]  # type: ignore[list-item]
        else:
            range_points = None
            range_percent = None
        result[category] = {
            "points": _fraction_display(points),
            "maximum_points": _fraction_display(maximum),
            "percent": _percent_from_points(points, maximum),
            "range_points": range_points,
            "range_percent": range_percent,
            "metric_ids": [metric_id for metric_id, spec in PUBLIC_METRICS.items() if spec.category == category],
        }
    return result


def _validate_configuration_inputs(
    configurations: Iterable[PublicConfigurationScore | PublicConfigurationAttempt],
) -> tuple[PublicConfigurationScore | PublicConfigurationAttempt, ...]:
    values = tuple(configurations)
    if len(values) != len(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative report requires all five attempted target surfaces")
    if any(not isinstance(item, (PublicConfigurationScore, PublicConfigurationAttempt)) for item in values):
        raise TypeError("authoritative report consumes PublicConfigurationScore or PublicConfigurationAttempt objects")
    ids = [item.configuration_id for item in values]
    if set(ids) != set(TARGET_CONFIGURATIONS) or len(set(ids)) != len(ids):
        raise ValueError("authoritative report requires the exact five target configuration IDs")
    for item in values:
        if isinstance(item, PublicConfigurationScore):
            _configuration_metric_ids(item)
            if set(item.categories) != set(PUBLIC_CATEGORY_POINTS):
                raise ValueError(f"{item.configuration_id}: category points must use the exact five public categories")
            _authoritative_categories(item)
        else:
            _validate_attempt(item)
    return tuple(sorted(values, key=lambda value: value.configuration_id))


def _recommendation_document(outputs: RecommendationOutputs | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(outputs, Mapping):
        return deepcopy(dict(outputs))
    if not isinstance(outputs, RecommendationOutputs):
        raise TypeError("authoritative recommendations must be RecommendationOutputs or a serialized recommendation document")
    # Keep the recommendation module's versioned serialized contract intact.
    # The extra source marker makes provenance visible in report.json without
    # accepting an alternate, renderer-shaped recommendation input.
    rendered = outputs.display()
    return {
        "source": "session_bench.recommendations.RecommendationOutputs",
        **rendered,
    }


def _empty_recommendation_document(reason_id: str) -> dict[str, Any]:
    """Build explicit negative use-case rows for an attempted-only report."""

    return {
        "source": "session_bench.recommendations.RecommendationOutputs",
        "schema_version": RECOMMENDATION_SCHEMA_VERSION,
        "recommendations": [
            {
                "use_case": use_case,
                "status": "no_recommendation",
                "configuration_ids": [],
                "rule_version": "v1-report-attempted-row",
                "thresholds": {},
                "objective_name": "No eligible evaluated configuration",
                "objective_values": {},
                "cohort_result_ids": [],
                "metric_ids": [],
                "citations": [],
                "observer_ids": [],
                "native_locators": [],
                "reason_ids": [reason_id],
            }
            for use_case in USE_CASES
        ],
        "improvements": [],
    }


def _authoritative_run_row(run: PublicRunScore, *, scored: bool) -> dict[str, Any]:
    """Serialize one typed run without dropping its identity or evidence timeline."""

    return {
        "run_id": run.run_id,
        "repetition": run.repetition,
        "build": run.build,
        "collected_on": run.collected_on,
        "result_id": run.result_id,
        "evaluation_id": run.survival_evaluation_id,
        "overall_points": _fraction_display(run.overall) if scored else None,
        "rankable": bool(run.rankable) if scored else False,
        "portable_gate": bool(run.portable_gate),
        "identity": None if run.identity is None else run.identity.display(),
        "timeline": [event.display() for event in run.timeline],
    }


def _attempt_run_rows(attempt: PublicConfigurationAttempt) -> list[dict[str, Any]]:
    """Serialize only the run identities that actually occurred."""

    return [
        _authoritative_run_row(run, scored=False)
        for run in sorted(attempt.runs, key=lambda value: value.repetition)
    ]


def _attempt_categories(runs: Sequence[PublicRunScore]) -> dict[str, dict[str, Any]]:
    """Expose partial category ranges without manufacturing an aggregate score."""

    result: dict[str, dict[str, Any]] = {}
    for category, maximum in PUBLIC_CATEGORY_POINTS.items():
        values = [run.categories[category].score for run in runs if run.categories[category].score is not None]
        range_points = None if not values else [_fraction_display(min(values)), _fraction_display(max(values))]
        range_percent = None if not values else [_percent_from_points(min(values), maximum), _percent_from_points(max(values), maximum)]
        result[category] = {
            "points": None,
            "maximum_points": _fraction_display(maximum),
            "percent": None,
            "range_points": range_points,
            "range_percent": range_percent,
            "metric_ids": [metric_id for metric_id, spec in PUBLIC_METRICS.items() if spec.category == category],
        }
    return result


def _attempt_row(
    attempt: PublicConfigurationAttempt,
    descriptor: Mapping[str, str],
) -> dict[str, Any]:
    _validate_attempt(attempt)
    run_rows = _attempt_run_rows(attempt)
    metric_rows = _authoritative_metric_rows_for_runs(attempt.configuration_id, attempt.runs)
    evidence = _attempt_evidence_payloads(attempt)
    reasons = list(_attempt_reason_ids(attempt))
    run_blockers = [
        f"repetition {run.repetition}: {blocker}"
        for run in sorted(attempt.runs, key=lambda value: value.repetition)
        for blocker in run.blockers
    ]
    blockers = list(dict.fromkeys([*reasons, *run_blockers]))
    bound = bool(run_rows) and all(
        isinstance(run.get(field), str) and bool(run.get(field))
        for run in run_rows
        for field in ("build", "collected_on", "result_id", "evaluation_id")
    )
    any_resolved = any(
        repetition["state"] in RESOLVED_STATES
        for metric in metric_rows
        for repetition in metric["repetitions"]
    )
    verification_state = "Partially verified" if any_resolved and bound else "Unranked"
    facts = {
        "three_repetitions": False,
        "exact_metric_set": True,
        "all_metrics_resolved": bool(run_rows) and all(
            repetition["state"] in RESOLVED_STATES
            for metric in metric_rows
            for repetition in metric["repetitions"]
        ),
        "portable_gate": bool(run_rows) and all(run["portable_gate"] is True for run in run_rows),
        "evidence_bound": bound,
        "immutable_public_bundle": False,
        "reproduction_receipt": False,
        "any_metric_resolved": any_resolved,
    }
    return {
        **descriptor,
        "attempted": True,
        "attempt_state": attempt.status,
        "status": attempt.status,
        "attempt_reasons": reasons,
        "attempt_evidence": evidence,
        "bundle_id": None,
        "reproduction_receipt_id": None,
        "bundle": None,
        "reproduction_receipt": None,
        "rank": None,
        "overall_points": None,
        "overall_percent": None,
        "overall_range_points": None,
        "categories": _attempt_categories(attempt.runs),
        "metric_ids": list(PUBLIC_METRICS),
        "metrics": metric_rows,
        "runs": run_rows,
        "verification": {
            "state": verification_state,
            "derived": True,
            "facts": facts,
            "reason_ids": blockers,
        },
        "blockers": blockers,
    }


def _serialized_attempt_citation(row: Mapping[str, Any]) -> dict[str, Any]:
    runs = row["runs"]
    result_ids = [run["result_id"] for run in runs if isinstance(run.get("result_id"), str) and run["result_id"]]
    evaluation_ids = [run["evaluation_id"] for run in runs if isinstance(run.get("evaluation_id"), str) and run["evaluation_id"]]
    if len(result_ids) != len(evaluation_ids):
        raise ValueError(f"{row['configuration_id']}: blocker citation result/evaluation IDs must be paired")
    return {
        "configuration_id": row["configuration_id"],
        "surface_id": row["configuration_id"],
        "status": row["attempt_state"],
        "builds": sorted({run["build"] for run in runs if isinstance(run.get("build"), str) and run["build"]}),
        "collected_on": sorted({run["collected_on"] for run in runs if isinstance(run.get("collected_on"), str) and run["collected_on"]}),
        "result_ids": result_ids,
        "evaluation_ids": evaluation_ids,
        "bound_result_ids": [f"{evaluation_id}:{result_id}" for evaluation_id, result_id in zip(evaluation_ids, result_ids, strict=True)],
        "reproduction_receipt_id": None,
        "blocker_evidence_ids": [item["id"] for item in row["attempt_evidence"]],
    }


def _attempt_improvement(attempt: PublicConfigurationAttempt) -> dict[str, Any]:
    evidence = _attempt_evidence_payloads(attempt)
    evidence_ids = [item["id"] for item in evidence]
    observer_ids = sorted({observer for item in evidence for observer in item["observer_ids"]})
    native_locators = sorted({json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in evidence for locator in item["native_locators"]})
    reason_ids = list(_attempt_reason_ids(attempt)) or [f"attempt.{attempt.status}"]
    return {
        "configuration_id": attempt.configuration_id,
        "citation": {
            "configuration_id": attempt.configuration_id,
            "surface_id": attempt.configuration_id,
            "status": attempt.status,
            "builds": sorted({run.build for run in attempt.runs if isinstance(run.build, str) and run.build}),
            "collected_on": sorted({run.collected_on for run in attempt.runs if isinstance(run.collected_on, str) and run.collected_on}),
            "result_ids": [run.result_id for run in attempt.runs if isinstance(run.result_id, str) and run.result_id],
            "evaluation_ids": [run.survival_evaluation_id for run in attempt.runs if isinstance(run.survival_evaluation_id, str) and run.survival_evaluation_id],
            "bound_result_ids": [f"{run.survival_evaluation_id}:{run.result_id}" for run in attempt.runs if isinstance(run.result_id, str) and run.result_id and isinstance(run.survival_evaluation_id, str) and run.survival_evaluation_id],
            "reproduction_receipt_id": None,
            "blocker_evidence_ids": evidence_ids,
        },
        "items": [
            {
                "blocker_id": reason_id,
                "state": "blocked",
                "observed_consequence": f"The {attempt.status} attempt has no complete public score.",
                "evidence_ids": evidence_ids,
                "observer_ids": observer_ids,
                "native_locators": native_locators,
                "acceptance_condition": "Resolve the cited blocker and complete three evidence-bound evaluated repetitions.",
            }
            for reason_id in reason_ids
        ],
        "reason_ids": reason_ids,
    }


def build_authoritative_report(
    configurations: Iterable[PublicConfigurationScore | PublicConfigurationAttempt],
    recommendations: RecommendationOutputs | Mapping[str, Any] | None,
    *,
    generated_at: str,
    constructed: bool = False,
    scope: str | None = None,
    method: str | None = None,
    data_status: str | None = None,
) -> dict[str, Any]:
    """Build the strict public report from scorer and recommendation outputs.

    ``PublicConfigurationScore`` remains the three-repetition scored row. A
    ``PublicConfigurationAttempt`` carries only real partial runs and blocker
    evidence, so an unavailable or incomplete surface is visible without a
    fabricated score or pseudo-run.
    """

    if not isinstance(generated_at, str) or not generated_at.strip():
        raise ValueError("generated_at must be a non-empty string")
    attempted = _validate_configuration_inputs(configurations)
    scores = tuple(item for item in attempted if isinstance(item, PublicConfigurationScore))
    incomplete = tuple(item for item in attempted if isinstance(item, PublicConfigurationAttempt))
    if recommendations is None:
        recommendation_document = _empty_recommendation_document("cohort.no_recommendation_without_evaluated_rows")
    else:
        recommendation_document = _recommendation_document(recommendations)
    if incomplete:
        _validate_mixed_recommendations(recommendation_document, scores)
        existing_improvements = {
            item.get("configuration_id")
            for item in recommendation_document.get("improvements", [])
            if isinstance(item, Mapping)
        }
        recommendation_document["improvements"] = [
            *recommendation_document.get("improvements", []),
            *[_attempt_improvement(item) for item in incomplete if item.configuration_id not in existing_improvements],
        ]
    elif isinstance(recommendations, RecommendationOutputs):
        _validate_recommendations_against_scores(recommendations, scores)
    else:
        _validate_authoritative_recommendations(recommendation_document)
    descriptors = {item.configuration_id: _target_descriptor(item.configuration_id) for item in attempted}
    derived = {item.configuration_id: _derived_verification(item) for item in scores}
    full_ids = {item.configuration_id for item in scores if derived[item.configuration_id][0] == "Fully reproduced"}
    if incomplete:
        # v1 publishes one fixed five-surface cohort.  Partial rows remain
        # inspectable, but an attempted/incomplete configuration can never
        # leave a smaller public leaderboard behind.
        scorer_qualified = None
    else:
        scorer_qualified = qualified_public_cohort(scores)
    scorer_ids = {item.configuration_id for item in scorer_qualified or ()}
    verified_ids = full_ids & scorer_ids
    pair_gate = any(pair <= verified_ids for pair in CLI_DESKTOP_PAIRS)
    leaderboard_eligible = (
        scorer_qualified is not None
        and len(verified_ids) == len(TARGET_CONFIGURATIONS)
    )
    qualified_ids = tuple(sorted(verified_ids if leaderboard_eligible else ()))
    rank_values: dict[str, int] = {}
    if leaderboard_eligible:
        rankable = [item for item in attempted if item.configuration_id in full_ids and item.configuration_id in scorer_ids]
        rankable.sort(
            key=lambda item: (
                -Fraction(display_decimal(item.overall or Fraction(0))),
                item.configuration_id,
            )
        )
        prior: Fraction | None = None
        rank = 0
        for position, item in enumerate(rankable, 1):
            published = Fraction(display_decimal(item.overall or Fraction(0)))
            if prior is None or published != prior:
                rank = position
                prior = published
            rank_values[item.configuration_id] = rank
    rows: list[dict[str, Any]] = []
    for item in attempted:
        descriptor = descriptors[item.configuration_id]
        if isinstance(item, PublicConfigurationAttempt):
            rows.append(_attempt_row(item, descriptor))
            continue
        state, facts, reasons = derived[item.configuration_id]
        rank = rank_values.get(item.configuration_id)
        bundle_payload, receipt_payload = _binding_payloads(item)
        overall = _fraction(item.overall, f"{item.configuration_id}.overall") if item.overall is not None else None
        overall_range = None
        if item.overall_range is not None:
            overall_range = [_fraction_display(_fraction(value)) for value in item.overall_range]
        rows.append({
            **descriptor,
            "attempted": True,
            "attempt_state": "evaluated",
            "status": "evaluated",
            "attempt_reasons": [],
            "attempt_evidence": [],
            "bundle_id": None if bundle_payload is None else bundle_payload["id"],
            "reproduction_receipt_id": None if receipt_payload is None else receipt_payload["id"],
            "bundle": bundle_payload,
            "reproduction_receipt": receipt_payload,
            "rank": rank,
            "overall_points": _fraction_display(overall),
            "overall_percent": None if overall is None else float(overall),
            "overall_range_points": overall_range,
            "categories": _authoritative_categories(item),
            "metric_ids": list(PUBLIC_METRICS),
            "metrics": _authoritative_metric_rows(item),
            "runs": [
                _authoritative_run_row(run, scored=True)
                for run in sorted(item.runs, key=lambda value: value.repetition)
            ],
            "verification": {
                "state": state,
                "derived": True,
                "facts": facts,
                "reason_ids": list(reasons),
            },
            "blockers": list(item.blockers),
        })
    document = {
        "schema_version": AUTHORITATIVE_REPORT_SCHEMA,
        "kind": AUTHORITATIVE_REPORT_KIND,
        "generated_at": generated_at,
        "constructed": bool(constructed),
        "data_status": data_status or ("CONSTRUCTED CONTROL" if constructed else "EVIDENCE-BOUND PUBLIC RESULT"),
        "scope": scope or "Five fixed Session-Bench v1 target surfaces; one public 31-metric score per configuration.",
        "method": method or "Scores and recommendations are derived from validated public scorer outputs and their bound result identities.",
        "metric_ids": list(PUBLIC_METRICS),
        "categories": [{"id": category, "name": next(item["name"] for item in DEFAULT_CATEGORIES if item["id"] == category), "maximum_points": _fraction_display(points)} for category, points in PUBLIC_CATEGORY_POINTS.items()],
        "target_surfaces": [descriptors[configuration_id] | {"attempted": True} for configuration_id in TARGET_CONFIGURATIONS],
        "cohort": {
            "attempted_configuration_ids": list(TARGET_CONFIGURATIONS),
            "scorer_qualified_configuration_ids": sorted(scorer_ids),
            "qualified_configuration_ids": list(qualified_ids),
            "minimum_qualified": len(TARGET_CONFIGURATIONS),
            "requires_cli_desktop_pair": False,
            "complete_cli_desktop_pair": pair_gate,
            "leaderboard_eligible": leaderboard_eligible,
        },
        "configurations": rows,
        "recommendation_outputs": recommendation_document,
    }
    return validate_authoritative_report(document)


_RESULT_CITATION_KEYS = {
    "configuration_id", "surface_id", "builds", "collected_on", "result_ids",
    "evaluation_ids", "bound_result_ids", "reproduction_receipt_id",
}
_BLOCKER_CITATION_KEYS = _RESULT_CITATION_KEYS | {"status", "blocker_evidence_ids"}


def _is_blocker_citation(value: Mapping[str, Any]) -> bool:
    return "blocker_evidence_ids" in value or value.get("status") in ATTEMPT_STATES


def _validate_evaluated_citation(value: Mapping[str, Any], label: str) -> None:
    _strict_keys(value, _RESULT_CITATION_KEYS, label)
    if not isinstance(value["result_ids"], list) or len(value["result_ids"]) != 3 or len(set(value["result_ids"])) != 3:
        raise ValueError(f"{label} requires three unique result IDs")
    if not isinstance(value["evaluation_ids"], list) or len(value["evaluation_ids"]) != 3 or len(set(value["evaluation_ids"])) != 3:
        raise ValueError(f"{label} requires three unique evaluation IDs")
    if not isinstance(value["bound_result_ids"], list) or len(value["bound_result_ids"]) != 3:
        raise ValueError(f"{label} requires three bound result IDs")
    if not isinstance(value["reproduction_receipt_id"], str) or not value["reproduction_receipt_id"]:
        raise ValueError(f"{label} requires a reproduction receipt")


def _validate_blocker_citation(value: Mapping[str, Any], label: str) -> None:
    _strict_keys(value, _BLOCKER_CITATION_KEYS, label)
    if value["status"] not in ATTEMPT_STATES:
        raise ValueError(f"{label} must identify an attempted-row state")
    if not isinstance(value["blocker_evidence_ids"], list) or not value["blocker_evidence_ids"] or len(set(value["blocker_evidence_ids"])) != len(value["blocker_evidence_ids"]):
        raise ValueError(f"{label} requires blocker evidence IDs")
    result_ids = value["result_ids"]
    evaluation_ids = value["evaluation_ids"]
    bound_ids = value["bound_result_ids"]
    if not isinstance(result_ids, list) or not isinstance(evaluation_ids, list) or not isinstance(bound_ids, list) or len(result_ids) > 2 or len(result_ids) != len(evaluation_ids) or len(result_ids) != len(bound_ids):
        raise ValueError(f"{label} may cite only paired zero-to-two evaluated IDs")
    if value["reproduction_receipt_id"] is not None:
        raise ValueError(f"{label} must not claim a reproduction receipt")


def _validate_authoritative_recommendations(
    value: Any,
    *,
    configuration_rows: Sequence[Mapping[str, Any]] | None = None,
    cohort: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("recommendation_outputs must be an object")
    _strict_keys(value, {"source", "schema_version", "recommendations", "improvements"}, "recommendation_outputs")
    if value["source"] != "session_bench.recommendations.RecommendationOutputs":
        raise ValueError("recommendation_outputs must identify RecommendationOutputs")
    if value["schema_version"] != RECOMMENDATION_SCHEMA_VERSION:
        raise ValueError("recommendation_outputs.schema_version is not the frozen recommendation schema")
    qualifications = value["recommendations"]
    improvements = value["improvements"]
    if not isinstance(qualifications, list) or not isinstance(improvements, list):
        raise ValueError("recommendation_outputs sections must be arrays")
    if len(qualifications) != 6 or [item.get("use_case") for item in qualifications if isinstance(item, Mapping)] != list(USE_CASES):
        raise ValueError("recommendation_outputs must contain all six frozen use cases")
    for index, item in enumerate(qualifications):
        if not isinstance(item, Mapping):
            raise ValueError(f"recommendation qualification {index} must be an object")
        _strict_keys(item, {"use_case", "status", "configuration_ids", "rule_version", "thresholds", "objective_name", "objective_values", "cohort_result_ids", "metric_ids", "citations", "observer_ids", "native_locators", "reason_ids"}, f"recommendation qualification {index}")
        if not isinstance(item["use_case"], str) or item["status"] not in {"recommended", "qualified_set", "no_recommendation"} or not isinstance(item["configuration_ids"], list):
            raise ValueError(f"recommendation qualification {index} has invalid status or selection")
        if any(configuration_id not in TARGET_CONFIGURATIONS for configuration_id in item["configuration_ids"]):
            raise ValueError(f"recommendation qualification {index} names an unknown configuration")
        if not isinstance(item["rule_version"], str) or not isinstance(item["thresholds"], Mapping) or not isinstance(item["objective_name"], str) or not isinstance(item["objective_values"], Mapping):
            raise ValueError(f"recommendation qualification {index} has invalid frozen rule fields")
        if not isinstance(item["cohort_result_ids"], list) or not isinstance(item["metric_ids"], list) or not all(metric_id in PUBLIC_METRICS for metric_id in item["metric_ids"]):
            raise ValueError(f"recommendation qualification {index} has invalid metric or cohort IDs")
        if item["status"] != "no_recommendation" and (
            not item["configuration_ids"]
            or not item["cohort_result_ids"]
            or not item["metric_ids"]
            or not item["observer_ids"]
            or not item["native_locators"]
            or not item["reason_ids"]
        ):
            raise ValueError(f"recommendation qualification {index} lacks evidence-bound positive output fields")
        citations = item["citations"]
        if not isinstance(citations, list) or len(citations) != len(item["configuration_ids"]):
            raise ValueError(f"recommendation qualification {index} citations do not cover its selection")
        if item["status"] == "no_recommendation" and (item["configuration_ids"] or citations):
            raise ValueError("no_recommendation cannot select configurations")
        for citation_index, citation in enumerate(citations):
            if not isinstance(citation, Mapping):
                raise ValueError(f"recommendation qualification {index} citation {citation_index} must be an object")
            _validate_evaluated_citation(citation, f"recommendation qualification {index} citation {citation_index}")
            if citation["configuration_id"] != item["configuration_ids"][citation_index]:
                raise ValueError("recommendation citation configuration does not match qualification")
            if item["status"] != "no_recommendation" and (not isinstance(citation["reproduction_receipt_id"], str) or not citation["reproduction_receipt_id"]):
                raise ValueError("positive recommendation citations require a reproduction receipt")
    for index, item in enumerate(improvements):
        if not isinstance(item, Mapping):
            raise ValueError(f"improvement {index} must be an object")
        _strict_keys(item, {"configuration_id", "citation", "items", "reason_ids"}, f"improvement {index}")
        citation = item["citation"]
        if not isinstance(citation, Mapping):
            raise ValueError(f"improvement {index} citation must be an object")
        blocker_citation = _is_blocker_citation(citation)
        if blocker_citation:
            _validate_blocker_citation(citation, f"improvement {index} citation")
        else:
            _validate_evaluated_citation(citation, f"improvement {index} citation")
        if citation["configuration_id"] != item["configuration_id"]:
            raise ValueError("improvement citation configuration does not match improvement")
        if citation["configuration_id"] not in TARGET_CONFIGURATIONS:
            raise ValueError("improvement citation must identify a known configuration and receipt")
        if not blocker_citation and (not isinstance(citation["reproduction_receipt_id"], str) or not citation["reproduction_receipt_id"]):
            raise ValueError("improvement citation must identify a known configuration and receipt")
        if not blocker_citation and (not isinstance(citation["result_ids"], list) or len(citation["result_ids"]) != 3 or not isinstance(citation["evaluation_ids"], list) or len(citation["evaluation_ids"]) != 3):
            raise ValueError("improvement citation must cover three repetitions")
        if not isinstance(item["items"], list):
            raise ValueError(f"improvement {index}.items must be an array")
        for improvement_index, improvement in enumerate(item["items"]):
            if not isinstance(improvement, Mapping):
                raise ValueError(f"improvement {index} item {improvement_index} must be an object")
            if "blocker_id" in improvement:
                _strict_keys(improvement, {"blocker_id", "state", "observed_consequence", "evidence_ids", "observer_ids", "native_locators", "acceptance_condition"}, f"improvement {index} blocker item {improvement_index}")
                if not isinstance(improvement["blocker_id"], str) or not improvement["blocker_id"] or improvement["state"] != "blocked":
                    raise ValueError(f"improvement {index} blocker item {improvement_index} is invalid")
                if not isinstance(improvement["evidence_ids"], list) or not improvement["evidence_ids"]:
                    raise ValueError(f"improvement {index} blocker item {improvement_index} needs blocker evidence IDs")
            else:
                _strict_keys(improvement, {"metric_id", "category", "state", "repetitions", "observed_consequence", "evidence_states", "observer_ids", "native_locators", "acceptance_condition"}, f"improvement {index} item {improvement_index}")
                if improvement["metric_id"] not in PUBLIC_METRICS or improvement["state"] not in {"failed", "blocked"}:
                    raise ValueError(f"improvement {index} item {improvement_index} is not a public metric output")
    normalized = {
        "source": value["source"],
        "schema_version": value["schema_version"],
        "recommendations": [dict(item) for item in qualifications],
        "improvements": [dict(item) for item in improvements],
    }
    if configuration_rows is not None and cohort is not None:
        _validate_serialized_recommendation_provenance(normalized, configuration_rows, cohort)
    return normalized


def _validate_mixed_recommendations(
    recommendations: Mapping[str, Any],
    scores: Sequence[PublicConfigurationScore],
) -> None:
    """Validate the evaluated part before blocker fixes are appended."""

    _validate_authoritative_recommendations(recommendations)
    by_id = {item.configuration_id: item for item in scores}
    improvements = recommendations["improvements"]
    improvement_ids = {item["configuration_id"] for item in improvements}
    if not set(by_id) <= improvement_ids:
        missing = sorted(set(by_id) - improvement_ids)
        raise ValueError(f"improvement outputs must cover scored configurations: {missing}")
    for index, improvement_list in enumerate(improvements):
        configuration_id = improvement_list["configuration_id"]
        if configuration_id not in by_id:
            # Blocker rows are appended by build_authoritative_report after
            # this check; a caller-supplied blocker record is validated later
            # against the serialized attempted row.
            continue
        configuration = by_id[configuration_id]
        if dict(improvement_list["citation"]) != _object_citation(configuration):
            raise ValueError(f"improvement {index} citation does not match typed scorer output")
        by_repetition = _configuration_metric_evidence(configuration)
        for item_index, item in enumerate(improvement_list["items"]):
            if "metric_id" not in item or item["metric_id"] not in PUBLIC_METRICS:
                raise ValueError(f"improvement {index} item {item_index} names an unknown metric")
            blocked = [run.repetition for run in configuration.runs if run.metrics[item["metric_id"]] is None]
            failed = [run.repetition for run in configuration.runs if run.metrics[item["metric_id"]] is not None and run.metrics[item["metric_id"]] < 1]
            expected_repetitions = blocked or failed
            expected_state = "blocked" if blocked else "failed"
            observers, locators, states = _object_metric_evidence_summary(configuration, (item["metric_id"],))
            if tuple(item["repetitions"]) != tuple(expected_repetitions) or item["state"] != expected_state:
                raise ValueError(f"improvement {index} item {item_index} consequences do not match typed scorer output")
            if tuple(item["observer_ids"]) != observers or tuple(item["native_locators"]) != locators or tuple(item["evidence_states"]) != states[0]:
                raise ValueError(f"improvement {index} item {item_index} evidence does not match typed scorer output")


def _serialized_metric_evidence_summary(
    row: Mapping[str, Any],
    metric_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Return the exact observer/locator/state projection used by recommendations."""

    by_metric = {metric["id"]: metric for metric in row["metrics"]}
    observers: set[str] = set()
    locators: set[str] = set()
    states: list[tuple[str, ...]] = []
    for metric_id in metric_ids:
        metric = by_metric[metric_id]
        metric_states: list[str] = []
        for repetition in metric["repetitions"]:
            evidence = repetition["metric_evidence"]
            observers.update(evidence["observer_ids"])
            locators.update(
                json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for locator in evidence["native_locators"]
            )
            metric_states.append(evidence["state"])
        states.append(tuple(metric_states))
    return tuple(sorted(observers)), tuple(sorted(locators)), tuple(states)


def _serialized_citation(row: Mapping[str, Any]) -> dict[str, Any]:
    runs = row["runs"]
    result_ids = [run["result_id"] for run in runs]
    evaluation_ids = [run["evaluation_id"] for run in runs]
    return {
        "configuration_id": row["configuration_id"],
        "surface_id": row["configuration_id"],
        "builds": sorted({run["build"] for run in runs}),
        "collected_on": sorted({run["collected_on"] for run in runs}),
        "result_ids": result_ids,
        "evaluation_ids": evaluation_ids,
        "bound_result_ids": [f"{evaluation_id}:{result_id}" for evaluation_id, result_id in zip(evaluation_ids, result_ids, strict=True)],
        "reproduction_receipt_id": row["reproduction_receipt_id"],
    }


def _validate_serialized_recommendation_provenance(
    recommendations: Mapping[str, Any],
    configuration_rows: Sequence[Mapping[str, Any]],
    cohort: Mapping[str, Any],
) -> None:
    """Cross-check recommendation citations against the serialized scorer rows."""

    rows = {row["configuration_id"]: row for row in configuration_rows}
    expected_ids = set(TARGET_CONFIGURATIONS)
    if set(rows) != expected_ids:
        raise ValueError("serialized recommendation cross-check requires all attempted configuration rows")
    scorer_ids = set(cohort["scorer_qualified_configuration_ids"])
    cohort_result_ids = tuple(sorted(result_id for configuration_id in scorer_ids for result_id in _serialized_citation(rows[configuration_id])["result_ids"]))
    recommendation_rows = recommendations["recommendations"]
    for index, item in enumerate(recommendation_rows):
        selected = item["configuration_ids"]
        if any(configuration_id not in expected_ids for configuration_id in selected):
            raise ValueError(f"recommendation {index} names an unknown attempted configuration")
        if item["cohort_result_ids"] != list(cohort_result_ids):
            raise ValueError(f"recommendation {index} cohort result IDs do not match the scorer cohort")
        expected_observers: set[str] = set()
        expected_locators: set[str] = set()
        for citation_index, citation in enumerate(item["citations"]):
            configuration_id = selected[citation_index]
            expected_citation = _serialized_citation(rows[configuration_id])
            if dict(citation) != expected_citation:
                raise ValueError(f"recommendation {index} citation {citation_index} does not match the attempted scorer row")
        for configuration_id in selected:
            observers, locators, _states = _serialized_metric_evidence_summary(rows[configuration_id], item["metric_ids"])
            expected_observers.update(observers)
            expected_locators.update(locators)
        if item["status"] != "no_recommendation":
            if tuple(sorted(item["observer_ids"])) != tuple(sorted(expected_observers)):
                raise ValueError(f"recommendation {index} observer IDs do not match cited metric evidence")
            if tuple(sorted(item["native_locators"])) != tuple(sorted(expected_locators)):
                raise ValueError(f"recommendation {index} native locators do not match cited metric evidence")
    improvements = recommendations["improvements"]
    if {item["configuration_id"] for item in improvements} != expected_ids:
        raise ValueError("improvement outputs must cover every attempted configuration")
    for index, improvement_list in enumerate(improvements):
        row = rows[improvement_list["configuration_id"]]
        if row.get("attempt_state") in ATTEMPT_STATES:
            expected_citation = _serialized_attempt_citation(row)
            if dict(improvement_list["citation"]) != expected_citation:
                raise ValueError(f"improvement {index} blocker citation does not match the attempted row")
            expected_evidence_ids = set(expected_citation["blocker_evidence_ids"])
            if not expected_evidence_ids:
                raise ValueError(f"improvement {index} blocker citation lacks preflight/calibration evidence")
            for item_index, blocker in enumerate(improvement_list["items"]):
                if "blocker_id" not in blocker:
                    raise ValueError(f"improvement {index} item {item_index} must cite a blocker")
                if not set(blocker.get("evidence_ids", [])) <= expected_evidence_ids:
                    raise ValueError(f"improvement {index} blocker item {item_index} cites unknown evidence")
            continue
        if dict(improvement_list["citation"]) != _serialized_citation(row):
            raise ValueError(f"improvement {index} citation does not match the attempted scorer row")
        for item_index, item in enumerate(improvement_list["items"]):
            if item["metric_id"] not in PUBLIC_METRICS:
                raise ValueError(f"improvement {index} item {item_index} names an unknown metric")
            _observers, locators, states = _serialized_metric_evidence_summary(row, (item["metric_id"],))
            if tuple(sorted(item["observer_ids"])) != _observers or tuple(sorted(item["native_locators"])) != locators:
                raise ValueError(f"improvement {index} item {item_index} locators do not match scorer metric evidence")
            if tuple(item["evidence_states"]) != states[0]:
                raise ValueError(f"improvement {index} item {item_index} evidence states do not match scorer metric evidence")


def _validate_serialized_bindings(
    row: Mapping[str, Any],
    run_rows: Sequence[Mapping[str, Any]],
) -> tuple[bool, bool]:
    """Validate serialized immutable bundle and deterministic offline receipt identities."""

    bundle = row["bundle"]
    receipt = row["reproduction_receipt"]
    if bundle is None or receipt is None:
        if bundle is not None or receipt is not None or row["bundle_id"] is not None or row["reproduction_receipt_id"] is not None:
            raise ValueError(f"authoritative configuration {row['configuration_id']} has a partial bundle/receipt binding")
        return False, False
    if not isinstance(bundle, Mapping) or not isinstance(receipt, Mapping):
        raise ValueError(f"authoritative configuration {row['configuration_id']} bindings must be objects")
    _strict_keys(bundle, {"id", "sha256", "immutable", "public", "result_ids"}, f"authoritative bundle {row['configuration_id']}")
    _strict_keys(receipt, {"id", "sha256", "bundle_sha256", "offline_recomputed", "verified", "result_ids"}, f"authoritative receipt {row['configuration_id']}")
    if row["bundle_id"] != bundle["id"] or row["reproduction_receipt_id"] != receipt["id"]:
        raise ValueError(f"authoritative configuration {row['configuration_id']} binding IDs are inconsistent")
    if not isinstance(bundle["id"], str) or not bundle["id"] or not isinstance(receipt["id"], str) or not receipt["id"]:
        raise ValueError(f"authoritative configuration {row['configuration_id']} binding IDs are invalid")
    bundle_sha256 = _sha256(bundle["sha256"], f"authoritative bundle {row['configuration_id']}")
    receipt_sha256 = _sha256(receipt["sha256"], f"authoritative receipt {row['configuration_id']}")
    receipt_bundle_sha256 = _sha256(receipt["bundle_sha256"], f"authoritative receipt bundle {row['configuration_id']}")
    expected_result_ids = [run["result_id"] for run in run_rows]
    expected_sorted_result_ids = sorted(expected_result_ids)
    if (
        bundle["immutable"] is not True
        or bundle["public"] is not True
        or receipt["offline_recomputed"] is not True
        or receipt["verified"] is not True
        or bundle["result_ids"] != expected_sorted_result_ids
        or receipt["result_ids"] != expected_sorted_result_ids
        or receipt_bundle_sha256 != bundle_sha256
        or len(set(expected_result_ids)) != 3
    ):
        raise ValueError(f"authoritative configuration {row['configuration_id']} bindings do not cover exact public results")
    # Keep the local variables explicit so future schema additions cannot make
    # the digest checks look like mere shape validation.
    if not bundle_sha256 or not receipt_sha256:
        raise ValueError(f"authoritative configuration {row['configuration_id']} bindings have invalid digests")
    return True, True


def _object_citation(configuration: PublicConfigurationScore) -> dict[str, Any]:
    runs = tuple(sorted(configuration.runs, key=lambda item: item.repetition))
    result_ids = [run.result_id for run in runs]
    evaluation_ids = [run.survival_evaluation_id for run in runs]
    if any(not isinstance(value, str) or not value for value in result_ids + evaluation_ids):
        raise ValueError(f"{configuration.configuration_id}: recommendation citation identities are incomplete")
    _bundle, receipt = _binding_payloads(configuration)
    return {
        "configuration_id": configuration.configuration_id,
        "surface_id": configuration.configuration_id,
        "builds": sorted({run.build for run in runs}),
        "collected_on": sorted({run.collected_on for run in runs}),
        "result_ids": result_ids,
        "evaluation_ids": evaluation_ids,
        "bound_result_ids": [f"{evaluation_id}:{result_id}" for evaluation_id, result_id in zip(evaluation_ids, result_ids, strict=True)],
        "reproduction_receipt_id": None if receipt is None else receipt["id"],
    }


def _object_metric_evidence_summary(
    configuration: PublicConfigurationScore,
    metric_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, ...], ...]]:
    by_repetition = _configuration_metric_evidence(configuration)
    observers: set[str] = set()
    locators: set[str] = set()
    states: list[tuple[str, ...]] = []
    for metric_id in metric_ids:
        metric_states: list[str] = []
        for repetition in (1, 2, 3):
            evidence = by_repetition[repetition][metric_id]
            observers.update(evidence["observer_ids"])
            locators.update(
                json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for locator in evidence["native_locators"]
            )
            metric_states.append(evidence["state"])
        states.append(tuple(metric_states))
    return tuple(sorted(observers)), tuple(sorted(locators)), tuple(states)


def _validate_recommendations_against_scores(
    outputs: RecommendationOutputs,
    configurations: Sequence[PublicConfigurationScore],
) -> None:
    """Check recommendation objects against the still-typed scorer outputs."""

    display = _recommendation_document(outputs)
    _validate_authoritative_recommendations(display)
    by_id = {item.configuration_id: item for item in configurations}
    scorer_cohort = qualified_public_cohort(configurations)
    expected_cohort_ids = () if scorer_cohort is None else tuple(sorted(item.configuration_id for item in scorer_cohort))
    expected_cohort_results = tuple(sorted(
        result_id
        for item in scorer_cohort or ()
        for result_id in _object_citation(item)["result_ids"]
    ))
    for index, item in enumerate(outputs.recommendations):
        if tuple(item.cohort_result_ids) != expected_cohort_results:
            raise ValueError(f"recommendation {index} cohort result IDs do not match typed scorer output")
        expected_observers: set[str] = set()
        expected_locators: set[str] = set()
        for configuration_id, citation in zip(item.configuration_ids, item.citations, strict=True):
            if configuration_id not in by_id:
                raise ValueError(f"recommendation {index} names an unknown configuration")
            if citation.display() != _object_citation(by_id[configuration_id]):
                raise ValueError(f"recommendation {index} citation does not match typed scorer output")
            observers, locators, _states = _object_metric_evidence_summary(by_id[configuration_id], item.metric_ids)
            expected_observers.update(observers)
            expected_locators.update(locators)
        if item.status != "no_recommendation":
            if tuple(sorted(item.observer_ids)) != tuple(sorted(expected_observers)) or tuple(sorted(item.native_locators)) != tuple(sorted(expected_locators)):
                raise ValueError(f"recommendation {index} evidence locators do not match typed scorer output")
    if {item.configuration_id for item in outputs.improvements} != set(by_id):
        raise ValueError("typed recommendation improvements must cover every attempted configuration")
    for index, improvement_list in enumerate(outputs.improvements):
        configuration = by_id[improvement_list.configuration_id]
        if improvement_list.citation.display() != _object_citation(configuration):
            raise ValueError(f"improvement {index} citation does not match typed scorer output")
        by_repetition = _configuration_metric_evidence(configuration)
        for item_index, item in enumerate(improvement_list.items):
            metric_id = item.metric_id
            if metric_id not in PUBLIC_METRICS:
                raise ValueError(f"improvement {index} item {item_index} names an unknown metric")
            blocked = [run.repetition for run in configuration.runs if run.metrics[metric_id] is None]
            failed = [run.repetition for run in configuration.runs if run.metrics[metric_id] is not None and run.metrics[metric_id] < 1]
            expected_repetitions = blocked or failed
            expected_state = "blocked" if blocked else "failed"
            observers, locators, states = _object_metric_evidence_summary(configuration, (metric_id,))
            if tuple(item.repetitions) != tuple(expected_repetitions) or item.state != expected_state:
                raise ValueError(f"improvement {index} item {item_index} consequences do not match typed scorer output")
            if tuple(item.observer_ids) != observers or tuple(item.native_locators) != locators or tuple(item.evidence_states) != states[0]:
                raise ValueError(f"improvement {index} item {item_index} evidence does not match typed scorer output")


def validate_authoritative_report(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a serialized authoritative report before it reaches the renderer."""

    if not isinstance(document, Mapping):
        raise ValueError("authoritative report must be an object")
    expected = {
        "schema_version", "kind", "generated_at", "constructed", "data_status", "scope", "method",
        "metric_ids", "categories", "target_surfaces", "cohort", "configurations", "recommendation_outputs",
    }
    _strict_keys(document, expected, "authoritative report")
    if document["schema_version"] != AUTHORITATIVE_REPORT_SCHEMA or document["kind"] != AUTHORITATIVE_REPORT_KIND:
        raise ValueError("unsupported authoritative report schema")
    if not isinstance(document["generated_at"], str) or not document["generated_at"].strip():
        raise ValueError("authoritative report generated_at must be a non-empty string")
    if not isinstance(document["constructed"], bool) or not isinstance(document["data_status"], str):
        raise ValueError("authoritative report construction status is invalid")
    metric_ids = document["metric_ids"]
    if metric_ids != list(PUBLIC_METRICS) or len(metric_ids) != 31 or len(set(metric_ids)) != 31:
        raise ValueError("authoritative report requires the exact 31 public metric IDs in frozen order")
    categories = document["categories"]
    if not isinstance(categories, list) or len(categories) != len(PUBLIC_CATEGORY_POINTS):
        raise ValueError("authoritative report requires all five public categories")
    if {item.get("id") for item in categories if isinstance(item, Mapping)} != set(PUBLIC_CATEGORY_POINTS):
        raise ValueError("authoritative report category IDs do not match PUBLIC_CATEGORY_POINTS")
    for index, category in enumerate(categories):
        if not isinstance(category, Mapping):
            raise ValueError(f"authoritative category {index} must be an object")
        _strict_keys(category, {"id", "name", "maximum_points"}, f"authoritative category {index}")
        if _fraction(category["maximum_points"], f"category {category['id']}.maximum_points") != PUBLIC_CATEGORY_POINTS[category["id"]]:
            raise ValueError(f"authoritative category {category['id']} does not use PUBLIC_CATEGORY_POINTS")
    targets = document["target_surfaces"]
    if not isinstance(targets, list) or len(targets) != len(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative report must show all five attempted target surfaces")
    target_ids = []
    for index, target in enumerate(targets):
        if not isinstance(target, Mapping):
            raise ValueError(f"target surface {index} must be an object")
        _strict_keys(target, {"configuration_id", "name", "surface", "surface_family", "attempted"}, f"target surface {index}")
        if target["configuration_id"] not in TARGET_CONFIGURATIONS or target["surface"] not in {"CLI", "Desktop"} or target["attempted"] is not True:
            raise ValueError(f"target surface {index} is invalid")
        target_ids.append(target["configuration_id"])
    if set(target_ids) != set(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative report target surfaces do not match the frozen five")
    cohort = document["cohort"]
    if not isinstance(cohort, Mapping):
        raise ValueError("authoritative report cohort must be an object")
    _strict_keys(cohort, {"attempted_configuration_ids", "scorer_qualified_configuration_ids", "qualified_configuration_ids", "minimum_qualified", "requires_cli_desktop_pair", "complete_cli_desktop_pair", "leaderboard_eligible"}, "authoritative cohort")
    if cohort["attempted_configuration_ids"] != list(TARGET_CONFIGURATIONS) or cohort["minimum_qualified"] != len(TARGET_CONFIGURATIONS) or cohort["requires_cli_desktop_pair"] is not False:
        raise ValueError("authoritative cohort attempted-target contract is invalid")
    scorer_ids = set(cohort["scorer_qualified_configuration_ids"])
    qualified_ids = set(cohort["qualified_configuration_ids"])
    if not scorer_ids <= set(TARGET_CONFIGURATIONS) or not qualified_ids <= set(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative cohort has unknown configuration IDs")
    pair_gate = bool(cohort["complete_cli_desktop_pair"])
    leaderboard_eligible = bool(cohort["leaderboard_eligible"])
    if leaderboard_eligible and qualified_ids != set(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative leaderboard gate cannot pass without all five qualified rows")
    rows = document["configurations"]
    if not isinstance(rows, list) or len(rows) != len(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative report must contain all five configuration rows")
    if {item.get("configuration_id") for item in rows if isinstance(item, Mapping)} != set(TARGET_CONFIGURATIONS):
        raise ValueError("authoritative configuration rows do not cover all five target surfaces")
    ranked_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"authoritative configuration {index} must be an object")
        _strict_keys(row, {"configuration_id", "name", "surface", "surface_family", "attempted", "attempt_state", "status", "attempt_reasons", "attempt_evidence", "bundle_id", "reproduction_receipt_id", "bundle", "reproduction_receipt", "rank", "overall_points", "overall_percent", "overall_range_points", "categories", "metric_ids", "metrics", "runs", "verification", "blockers"}, f"authoritative configuration {index}")
        configuration_id = row["configuration_id"]
        if row["surface"] not in {"CLI", "Desktop"} or row["attempted"] is not True or row["metric_ids"] != list(PUBLIC_METRICS):
            raise ValueError(f"authoritative configuration {configuration_id} has invalid surface or metric IDs")
        is_attempt = row["attempt_state"] in ATTEMPT_STATES
        if row["status"] != row["attempt_state"] or not isinstance(row["attempt_reasons"], list) or any(not isinstance(value, str) or not value for value in row["attempt_reasons"]):
            raise ValueError(f"authoritative configuration {configuration_id} has invalid attempt state or reasons")
        if not isinstance(row["attempt_evidence"], list):
            raise ValueError(f"authoritative configuration {configuration_id} attempt evidence must be an array")
        evidence_ids: set[str] = set()
        for evidence_index, blocker in enumerate(row["attempt_evidence"]):
            if not isinstance(blocker, Mapping):
                raise ValueError(f"authoritative configuration {configuration_id} blocker evidence must be an object")
            _strict_keys(blocker, {"id", "kind", "detail", "observer_ids", "native_locators"}, f"authoritative configuration {configuration_id} blocker evidence {evidence_index}")
            if not isinstance(blocker["id"], str) or not blocker["id"] or blocker["id"] in evidence_ids or not isinstance(blocker["kind"], str) or not isinstance(blocker["detail"], str):
                raise ValueError(f"authoritative configuration {configuration_id} blocker evidence identity is invalid")
            evidence_ids.add(blocker["id"])
            if not isinstance(blocker["observer_ids"], list) or len(set(blocker["observer_ids"])) != len(blocker["observer_ids"]):
                raise ValueError(f"authoritative configuration {configuration_id} blocker observer IDs are invalid")
            for locator in blocker["native_locators"]:
                _blocker_locator_payload(locator, f"authoritative configuration {configuration_id} blocker locator")
        if is_attempt and not row["attempt_reasons"] and not row["attempt_evidence"] and not row["runs"]:
            raise ValueError(f"authoritative configuration {configuration_id} blocked row lacks a reason or evidence")
        if not is_attempt and (row["attempt_state"] != "evaluated" or row["attempt_reasons"] or row["attempt_evidence"]):
            raise ValueError(f"authoritative configuration {configuration_id} evaluated row has attempt-only fields")
        metric_rows = row["metrics"]
        if not isinstance(metric_rows, list) or len(metric_rows) != 31 or {item.get("id") for item in metric_rows if isinstance(item, Mapping)} != set(PUBLIC_METRICS):
            raise ValueError(f"authoritative configuration {configuration_id} must expose exact 31 metric rows")
        for metric_index, metric in enumerate(metric_rows):
            if not isinstance(metric, Mapping):
                raise ValueError(f"authoritative metric row {metric_index} must be an object")
            _strict_keys(metric, {"id", "category", "maximum_points", "repetitions"}, f"authoritative metric {metric_index}")
            if metric["id"] not in PUBLIC_METRICS or metric["category"] != PUBLIC_METRICS[metric["id"]].category:
                raise ValueError(f"authoritative metric {metric_index} has the wrong category")
            if _fraction(metric["maximum_points"], f"authoritative metric {metric['id']}.maximum_points") != PUBLIC_METRICS[metric["id"]].points:
                raise ValueError(f"authoritative metric {metric['id']} maximum does not match PUBLIC_METRICS")
            if not isinstance(metric["repetitions"], list) or (len(metric["repetitions"]) != 3 if not is_attempt else len(metric["repetitions"]) > 2):
                raise ValueError(f"authoritative metric {metric['id']} has an invalid repetition count")
            repetitions = metric["repetitions"]
            repetition_ids = []
            for repetition in repetitions:
                if not isinstance(repetition, Mapping):
                    raise ValueError(f"authoritative metric {metric['id']} repetition must be an object")
                _strict_keys(repetition, {"repetition", "state", "fraction", "points", "metric_evidence", "citation"}, f"authoritative metric {metric['id']} repetition")
                if type(repetition["repetition"]) is not int or repetition["repetition"] not in {1, 2, 3}:
                    raise ValueError(f"authoritative metric {metric['id']} has an invalid repetition")
                if repetition["state"] not in {"measured", "native_absent", "contradiction", "unresolved", "decoder_unsupported", "unexercised", "invalid_capture"}:
                    raise ValueError(f"authoritative metric {metric['id']} has an invalid state")
                evidence = repetition["metric_evidence"]
                if not isinstance(evidence, Mapping):
                    raise ValueError(f"authoritative metric {metric['id']} repetition evidence must be an object")
                _strict_keys(evidence, {"state", "observer_ids", "native_locators"}, f"authoritative metric {metric['id']} repetition evidence")
                if evidence["state"] not in ALLOWED_STATES:
                    raise ValueError(f"authoritative metric {metric['id']} repetition evidence has an invalid state")
                if repetition["state"] != evidence["state"]:
                    raise ValueError(f"authoritative metric {metric['id']} repetition state does not match metric evidence state")
                if not isinstance(evidence["observer_ids"], list) or not evidence["observer_ids"] or len(set(evidence["observer_ids"])) != len(evidence["observer_ids"]) or any(not isinstance(observer, str) or not observer for observer in evidence["observer_ids"]):
                    raise ValueError(f"authoritative metric {metric['id']} repetition evidence observer IDs are invalid")
                locators = evidence["native_locators"]
                if not isinstance(locators, list) or not locators:
                    raise ValueError(f"authoritative metric {metric['id']} repetition evidence requires native locators")
                seen_locators: set[tuple[str, str, str | None]] = set()
                for locator in locators:
                    if not isinstance(locator, Mapping):
                        raise ValueError(f"authoritative metric {metric['id']} repetition locator must be an object")
                    allowed_locator_keys = {"artifact_id", "artifact_sha256", "record_location"}
                    if set(locator) not in (allowed_locator_keys - {"record_location"}, allowed_locator_keys):
                        raise ValueError(f"authoritative metric {metric['id']} repetition locator has wrong fields")
                    artifact_id = locator["artifact_id"]
                    artifact_sha256 = _sha256(locator["artifact_sha256"], f"authoritative metric {metric['id']} repetition locator")
                    record_location = locator.get("record_location")
                    if not isinstance(artifact_id, str) or not artifact_id or (record_location is not None and (not isinstance(record_location, str) or not record_location)):
                        raise ValueError(f"authoritative metric {metric['id']} repetition locator has invalid identity")
                    identity = (artifact_id, artifact_sha256, record_location)
                    if identity in seen_locators:
                        raise ValueError(f"authoritative metric {metric['id']} repetition locators must be unique")
                    seen_locators.add(identity)
                if repetition["fraction"] is not None:
                    fraction = _fraction(repetition["fraction"], f"authoritative metric {metric['id']}.fraction")
                    if fraction < 0 or fraction > 1:
                        raise ValueError(f"authoritative metric {metric['id']} fraction is outside 0..1")
                repetition_ids.append(repetition["repetition"])
            if repetition_ids != sorted(repetition_ids) or len(set(repetition_ids)) != len(repetition_ids) or any(value not in {1, 2, 3} for value in repetition_ids):
                raise ValueError(f"authoritative metric {metric['id']} repetitions must be 1, 2, 3")
            if not is_attempt and repetition_ids != [1, 2, 3]:
                raise ValueError(f"authoritative metric {metric['id']} repetitions must be 1, 2, 3")
        category_values = row["categories"]
        if not isinstance(category_values, Mapping) or set(category_values) != set(PUBLIC_CATEGORY_POINTS):
            raise ValueError(f"authoritative configuration {configuration_id} has invalid category map")
        for category, value in category_values.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"authoritative category {category} must be an object")
            _strict_keys(value, {"points", "maximum_points", "percent", "range_points", "range_percent", "metric_ids"}, f"authoritative category {category}")
            maximum = PUBLIC_CATEGORY_POINTS[category]
            if _fraction(value["maximum_points"], f"{configuration_id}.{category}.maximum_points") != maximum:
                raise ValueError(f"authoritative category {category} maximum does not match PUBLIC_CATEGORY_POINTS")
            points = None if value["points"] is None else _fraction(value["points"], f"{configuration_id}.{category}.points")
            if points is not None and (points < 0 or points > maximum):
                raise ValueError(f"authoritative category {category} points are outside its budget")
            expected_percent = _percent_from_points(points, maximum)
            if value["percent"] != expected_percent:
                raise ValueError(f"authoritative category {category} percent is not normalized by PUBLIC_CATEGORY_POINTS")
        verification = row["verification"]
        if not isinstance(verification, Mapping):
            raise ValueError(f"authoritative configuration {configuration_id} verification must be an object")
        _strict_keys(verification, {"state", "derived", "facts", "reason_ids"}, f"authoritative verification {configuration_id}")
        if verification["derived"] is not True or verification["state"] not in {"Fully reproduced", "Partially verified", "Unranked"}:
            raise ValueError(f"authoritative configuration {configuration_id} verification is not derived")
        facts = verification["facts"]
        if not isinstance(facts, Mapping):
            raise ValueError(f"authoritative verification facts {configuration_id} must be an object")
        if set(facts) != {"three_repetitions", "exact_metric_set", "all_metrics_resolved", "portable_gate", "evidence_bound", "immutable_public_bundle", "reproduction_receipt", "any_metric_resolved"} or any(not isinstance(value, bool) for value in facts.values()):
            raise ValueError(f"authoritative verification facts {configuration_id} are invalid")
        run_rows = row["runs"]
        if not isinstance(run_rows, list) or (len(run_rows) > 2 if is_attempt else len(run_rows) != 3):
            raise ValueError(f"authoritative configuration {configuration_id} has an invalid run count")
        run_repetitions = [run.get("repetition") for run in run_rows if isinstance(run, Mapping)]
        if run_repetitions != sorted(run_repetitions) or len(set(run_repetitions)) != len(run_repetitions) or any(value not in {1, 2, 3} for value in run_repetitions):
            raise ValueError(f"authoritative configuration {configuration_id} run repetitions are invalid")
        if not is_attempt and run_repetitions != [1, 2, 3]:
            raise ValueError(f"authoritative configuration {configuration_id} run repetitions must be 1, 2, 3")
        for run in run_rows:
            if not isinstance(run, Mapping):
                raise ValueError(f"authoritative configuration {configuration_id} run must be an object")
            _strict_keys(run, {"run_id", "repetition", "build", "collected_on", "result_id", "evaluation_id", "overall_points", "rankable", "portable_gate", "identity", "timeline"}, f"authoritative configuration {configuration_id} run")
            if not isinstance(run["run_id"], str) or not run["run_id"] or type(run["rankable"]) is not bool or type(run["portable_gate"]) is not bool:
                raise ValueError(f"authoritative configuration {configuration_id} run identity is invalid")
            if not is_attempt and not all(isinstance(run.get(field), str) and run.get(field) for field in ("build", "collected_on", "result_id", "evaluation_id")):
                raise ValueError(f"authoritative configuration {configuration_id} lacks bound run identities")
            identity = run["identity"]
            if identity is not None:
                if not isinstance(identity, Mapping):
                    raise ValueError(f"authoritative configuration {configuration_id} run identity envelope is invalid")
                required_identity = {"provider", "harness", "surface", "execution_mode", "os", "build", "model", "configuration", "configuration_id", "protocol_version", "workload_version", "observer_schema_version", "rubric_version", "run_id", "capture_id", "evaluation_id", "repetition", "collected_on", "result_id", "unavailable"}
                _strict_keys(identity, required_identity, f"authoritative configuration {configuration_id} run identity envelope")
                if identity["configuration_id"] != configuration_id or identity["run_id"] != run["run_id"] or identity["repetition"] != run["repetition"] or identity["build"] != run["build"] or identity["result_id"] != run["result_id"] or identity["evaluation_id"] != run["evaluation_id"]:
                    raise ValueError(f"authoritative configuration {configuration_id} run identity envelope does not match the run")
            timeline = run["timeline"]
            if not isinstance(timeline, list) or (not is_attempt and len(timeline) != len(SURVIVAL_METRICS)):
                raise ValueError(f"authoritative configuration {configuration_id} timeline is incomplete")
            if timeline and [event.get("metric_id") for event in timeline if isinstance(event, Mapping)] != list(SURVIVAL_METRICS):
                raise ValueError(f"authoritative configuration {configuration_id} timeline metric order is invalid")
            for event in timeline:
                if not isinstance(event, Mapping):
                    raise ValueError(f"authoritative configuration {configuration_id} timeline event is invalid")
                _strict_keys(event, {"step", "label", "metric_id", "state", "observed", "recorded", "observer_ids", "native_locators", "order_basis", "temporal_order_available"}, f"authoritative configuration {configuration_id} timeline event")
                if event["order_basis"] != "survival_metric_contract" or event["temporal_order_available"] is not False:
                    raise ValueError(f"authoritative configuration {configuration_id} timeline overstates temporal evidence")
        bundle_bound, receipt_bound = _validate_serialized_bindings(row, run_rows)
        runs_by_repetition = {run["repetition"]: run for run in run_rows}
        for metric in metric_rows:
            for repetition in metric["repetitions"]:
                run = runs_by_repetition[repetition["repetition"]]
                citation = repetition["citation"]
                if citation is None:
                    if all(repetition["metric_evidence"] for _ in (0,)) and all(run.get(field) for field in ("result_id", "evaluation_id")):
                        raise ValueError(f"authoritative metric {metric['id']} repetition citation is missing")
                else:
                    if not isinstance(citation, Mapping):
                        raise ValueError(f"authoritative metric {metric['id']} repetition citation must be an object")
                    _strict_keys(citation, {"evaluation_id", "result_id"}, f"authoritative metric {metric['id']} repetition citation")
                    if citation["evaluation_id"] != run["evaluation_id"] or citation["result_id"] != run["result_id"]:
                        raise ValueError(f"authoritative metric {metric['id']} repetition citation does not match its run")
        recomputed_facts = {
            "three_repetitions": len(run_rows) == 3 and set(run_repetitions) == {1, 2, 3},
            "exact_metric_set": row["metric_ids"] == list(PUBLIC_METRICS) and len(metric_rows) == 31,
            "all_metrics_resolved": bool(run_rows) and all(repetition["state"] in RESOLVED_STATES for metric in metric_rows for repetition in metric["repetitions"]),
            "portable_gate": bool(run_rows) and all(run["portable_gate"] is True for run in run_rows),
            "evidence_bound": _serialized_identity_bound(run_rows, configuration_id) and all(all(isinstance(run.get(field), str) and run.get(field) for field in ("build", "collected_on", "result_id", "evaluation_id")) for run in run_rows),
            "immutable_public_bundle": bundle_bound,
            "reproduction_receipt": receipt_bound,
            "any_metric_resolved": any(repetition["state"] in RESOLVED_STATES for metric in metric_rows for repetition in metric["repetitions"]),
        }
        if dict(facts) != recomputed_facts:
            raise ValueError(f"authoritative configuration {configuration_id} verification facts are not derived from serialized metrics")
        expected_state = "Fully reproduced" if all(facts[key] for key in ("three_repetitions", "exact_metric_set", "all_metrics_resolved", "evidence_bound", "immutable_public_bundle", "reproduction_receipt")) else ("Partially verified" if facts["any_metric_resolved"] and facts["evidence_bound"] else "Unranked")
        if verification["state"] != expected_state:
            raise ValueError(f"authoritative configuration {configuration_id} verification state is not derived from facts")
        rank = row["rank"]
        if rank is not None:
            if type(rank) is not int or rank < 1 or not leaderboard_eligible or configuration_id not in qualified_ids or expected_state != "Fully reproduced":
                raise ValueError(f"authoritative configuration {configuration_id} is ranked without the cohort and verification gates")
            ranked_ids.add(configuration_id)
    if leaderboard_eligible and ranked_ids != qualified_ids:
        raise ValueError("authoritative leaderboard must rank every qualified configuration")
    if not leaderboard_eligible and ranked_ids:
        raise ValueError("authoritative leaderboard rows must be unranked when the cohort gate fails")
    _validate_authoritative_recommendations(
        document["recommendation_outputs"],
        configuration_rows=rows,
        cohort=cohort,
    )
    return deepcopy(dict(document))


def load_authoritative_report(path: Path | str) -> dict[str, Any]:
    return validate_authoritative_report(load_report(path))


def _authoritative_view(document: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the strict document to the existing editorial renderer shape."""

    unpublished_local = document["data_status"] == "UNPUBLISHED LOCAL EVIDENCE"
    rows: list[dict[str, Any]] = []
    improvements = {item["configuration_id"]: item for item in document["recommendation_outputs"]["improvements"]}
    target_by_id = {item["configuration_id"]: item for item in document["target_surfaces"]}
    for row in document["configurations"]:
        target = target_by_id[row["configuration_id"]]
        evidence: list[dict[str, Any]] = []
        for blocker in row["attempt_evidence"]:
            evidence.append({
                "id": blocker["id"],
                "kind": blocker["kind"].upper(),
                "title": blocker["detail"],
                "locator": ", ".join(
                    locator.get("record_location") or locator["artifact_id"]
                    for locator in blocker["native_locators"]
                ) or ", ".join(blocker["observer_ids"]) or "No artifact locator available",
                "snippet": json.dumps({
                    "observer_ids": blocker["observer_ids"],
                    "native_locators": blocker["native_locators"],
                }, sort_keys=True),
                "provenance": "Retained preflight or calibration blocker evidence",
            })
        for run in row["runs"]:
            evidence.append({
                "id": f"{row['configuration_id']}-r{run['repetition']}",
                "kind": "PUBLIC RESULT",
                "title": f"Repetition {run['repetition']} result",
                "locator": f"{run['evaluation_id']}:{run['result_id']}" if run["evaluation_id"] and run["result_id"] else "Result identity unavailable",
                "snippet": json.dumps({"run_id": run["run_id"], "overall_points": run["overall_points"], "portable_gate": run["portable_gate"]}, sort_keys=True),
                "provenance": "Derived from validated PublicConfigurationScore output",
            })
        gates = []
        for metric in row["metrics"]:
            repetition_states = [item["state"] for item in metric["repetitions"]]
            state = "measured" if all(item == "measured" for item in repetition_states) else (repetition_states[0] if repetition_states else "unresolved")
            gates.append({"id": metric["id"], "name": metric["id"], "state": state, "detail": ", ".join(repetition_states), "evidence_id": evidence[0]["id"] if evidence else None})
        fix = improvements.get(row["configuration_id"])
        fixes = []
        if fix:
            for item in fix["items"]:
                fixes.append({"title": item.get("metric_id", item.get("blocker_id", "Blocker")), "detail": f"{item['observed_consequence']} Acceptance: {item['acceptance_condition']}"})
        identities = [run["identity"] for run in row["runs"] if isinstance(run.get("identity"), Mapping)]
        models = sorted({identity["model"] for identity in identities if identity.get("model")})
        timeline = []
        for run in row["runs"]:
            for event in run["timeline"]:
                native_locators = event["native_locators"]
                locator_text = ", ".join(
                    locator.get("record_location") or locator["artifact_id"]
                    for locator in native_locators
                )
                timeline.append({
                    "step": f"R{run['repetition']}.{event['step']}",
                    "label": event["label"],
                    "metric_id": event["metric_id"],
                    "state": event["state"],
                    "observed": event["observed"],
                    "recorded": event["recorded"],
                    # The editorial renderer reads ``state`` and ``locator``.
                    # Keep the original structured evidence beside those
                    # display fields so the view cannot silently lose the
                    # observer/native binding.
                    "locator": locator_text,
                    "locator_id": locator_text,
                    "observer_ids": list(event["observer_ids"]),
                    "native_locators": [dict(locator) for locator in native_locators],
                    "order_basis": event["order_basis"],
                    "temporal_order_available": event["temporal_order_available"],
                })
        rows.append({
            "id": row["configuration_id"],
            "name": target["name"],
            "surface": target["surface"],
            "version": ", ".join(sorted({run["build"] for run in row["runs"] if run["build"]})) or "Build unknown",
            "model": ", ".join(models) if models else "Model identity unavailable in evidence",
            "status": "ranked" if row["rank"] is not None else row["attempt_state"],
            "verification": (
                "Locally reproduced"
                if unpublished_local and row["verification"]["state"] == "Fully reproduced"
                else row["verification"]["state"]
            ),
            "ranking_eligible": row["rank"] is not None,
            "evidence_complete": row["verification"]["state"] == "Fully reproduced",
            "score": row["overall_percent"] if row["rank"] is not None else None,
            "runs": len(row["runs"]),
            "scheduled_runs": 3,
            "run_range": {"min": row["overall_range_points"][0], "max": row["overall_range_points"][1]} if row["overall_range_points"] else None,
            "categories": [{"id": category, "score": value["percent"], "summary": f"{value['points']} / {value['maximum_points']} points"} for category, value in row["categories"].items()],
            "gate_matrix": gates,
            "timeline": timeline,
            "evidence": evidence,
            "fixes": fixes,
        })
    qualifications = document["recommendation_outputs"]["recommendations"]
    recommendations = []
    evidence_by_config = {row["id"]: row["evidence"] for row in rows}
    use_cases = []
    for item in qualifications:
        results = {}
        selected = set(item["configuration_ids"])
        for config_id in document["cohort"]["attempted_configuration_ids"]:
            results[config_id] = item["status"] if config_id in selected else ("No recommendation" if item["status"] == "no_recommendation" else "Not selected")
        use_cases.append({
            "id": item["use_case"],
            "label": item["use_case"].replace("_", " ").title(),
            "requirement": f"{item['objective_name']} · rule {item['rule_version']}",
            "results": results,
        })
        if item["status"] == "no_recommendation":
            continue
        for config_id in item["configuration_ids"]:
            citation_ids = [evidence["id"] for evidence in evidence_by_config.get(config_id, [])]
            recommendations.append({
                "id": f"{item['use_case']}-{config_id}",
                "use_case": item["use_case"],
                "target_id": config_id,
                "claim": item["objective_name"],
                "qualification": f"Status: {item['status']}; rule {item['rule_version']}; thresholds {json.dumps(item['thresholds'], sort_keys=True)}",
                "citation_ids": citation_ids,
                "qualifies": True,
            })
    return {
        "schema_version": REPORT_SCHEMA,
        "edition": "v1 unpublished review candidate" if unpublished_local else "v1 authoritative public report",
        "title": "Session-Bench v1 · unpublished review candidate" if unpublished_local else "Session-Bench v1 · authoritative public report",
        "generated_at": document["generated_at"],
        "constructed": document["constructed"],
        "data_status": document["data_status"],
        "headline": "What did the session keep?",
        "deck": document["scope"],
        "scope": document["scope"],
        "method": document["method"],
        "categories": [dict(item) for item in document["categories"]],
        "configurations": rows,
        "recommendations": recommendations,
        "use_cases": use_cases,
        "limitations": [
            "Rendered from validated authoritative public scorer output.",
            "Ranks require all five release-cohort rows; CLI/Desktop pairing is recommendation-specific.",
            *(["Independent native reproduction is pending; these ranks are local review-candidate results and are not eligible for publication."] if unpublished_local else []),
        ],
    }


def render_authoritative_report(document: Mapping[str, Any], output_dir: Path | str) -> Path:
    """Render a validated authoritative report while preserving its strict JSON."""

    authoritative = validate_authoritative_report(document)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    view = _authoritative_view(authoritative)
    (output / "index.html").write_text(render_index_html(view), encoding="utf-8")
    (output / "scorecard.svg").write_text(render_scorecard_svg(view), encoding="utf-8")
    (output / "report.json").write_text(json.dumps(authoritative, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return output


def build_authoritative_control_report(*, generated_at: str = "2026-09-11T12:00:00Z", portability_loss_configuration_id: str | None = None) -> dict[str, Any]:
    """Build a five-surface constructed control through the real public scorer.

    This helper is intentionally named *control*: it is useful for renderer and
    schema tests, but its values cannot be presented as vendor observations.
    The source is the repository's constructed survival input plus the strict
    format-profile/evidence controls used by ``tests/test_v1_public_score.py``.
    """

    from .recommendations import build_recommendation_outputs
    from .survival_metrics import load_survival_input
    from .v1_public_score import (
        aggregate_public_configuration,
        format_evidence_control,
        format_profile_document,
        score_public_run,
        survival_evidence_control,
    )

    source = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios" / "survival-v1" / "equivalent-jsonl" / "input.jsonl"
    base = load_survival_input(source)
    configurations: list[PublicConfigurationScore] = []
    for configuration_id in TARGET_CONFIGURATIONS:
        runs = []
        for repetition in (1, 2, 3):
            measurement = deepcopy(base)
            measurement.update(run_id=f"constructed-{configuration_id}-{repetition}", configuration_id=configuration_id, repetition=repetition)
            if configuration_id == portability_loss_configuration_id:
                metric = next(row for row in measurement["metrics"] if row["id"] == "portable.companions")
                metric.update(state="contradiction", correct=0)
            profile = format_profile_document(run_id=measurement["run_id"], configuration_id=configuration_id, repetition=repetition)
            surface = "desktop" if configuration_id.endswith("-desktop") else "cli"
            survival = survival_evidence_control(measurement, evaluation_id=f"constructed-evaluation-{configuration_id}-{repetition}", identity={
                "provider": configuration_id.split("-")[0], "harness": configuration_id,
                "surface": surface, "execution_mode": "constructed-control",
                "os": "macOS-test", "build": f"constructed-{configuration_id}-build",
                "model": "synthetic-model", "configuration": "synthetic-default",
                "observer_schema_version": "1.0-survival-observer",
            })
            format_evidence = format_evidence_control(
                profile,
                build=f"constructed-{configuration_id}-build",
                collected_on=f"2026-09-{11 + repetition:02d}",
                result_id=f"constructed-result-{configuration_id}-{repetition}",
            )
            runs.append(score_public_run(survival, format_evidence))
        result_ids = [run.result_id for run in runs]
        bundle_sha = hashlib.sha256(("bundle:" + configuration_id).encode("utf-8")).hexdigest()
        receipt_sha = hashlib.sha256(("receipt:" + configuration_id).encode("utf-8")).hexdigest()
        configuration_evidence = {
            "schema_version": "session-bench-configuration-evidence-v1",
            "configuration_id": configuration_id,
            "bundle": {
                "id": f"constructed-bundle-{configuration_id}",
                "sha256": bundle_sha,
                "immutable": True,
                "public": True,
                "result_ids": sorted(result_ids),
            },
            "reproduction_receipt": {
                "id": f"constructed-receipt-{configuration_id}",
                "sha256": receipt_sha,
                "bundle_sha256": bundle_sha,
                "offline_recomputed": True,
                "verified": True,
                "result_ids": sorted(result_ids),
            },
        }
        configurations.append(aggregate_public_configuration(runs, configuration_evidence=configuration_evidence))
    outputs = build_recommendation_outputs(
        configurations,
        storage_diagnostics={item.configuration_id: [{"repetition": repetition, "marginal_physical_bytes": 100 + index, "recovered_required_semantic_facts": 10} for repetition in (1, 2, 3)] for index, item in enumerate(configurations)},
    )
    return build_authoritative_report(
        configurations,
        outputs,
        generated_at=generated_at,
        constructed=True,
        scope="Five synthetic control rows use the fixed target-surface identifiers to exercise the authoritative 31-metric report contract offline.",
        method="Constructed survival and format-profile controls were validated and scored by the public scorer; RecommendationOutputs supplied the recommendation and fix sections.",
    )


def _link(label: Any, href: Any, *, class_name: str = "") -> str:
    safe = _safe_local_href(href)
    if safe is None:
        return _esc(label)
    class_attr = f' class="{_esc(class_name)}"' if class_name else ""
    return f'<a{class_attr} href="{_esc(safe)}">{_esc(label)}</a>'


def load_report(path: Path | str) -> dict[str, Any]:
    """Load one strict JSON report payload from a caller-selected path."""

    report_path = Path(path)
    value = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("v1 report must be a JSON object")
    return value


def _categories(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("categories")
    if not isinstance(raw, list) or not raw:
        return [dict(item) for item in DEFAULT_CATEGORIES]
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        category_id = item.get("id")
        if not isinstance(category_id, str) or not category_id:
            continue
        result.append({
            "id": category_id,
            "name": _text(item.get("name"), category_id.replace("_", " ").title()),
            "weight": item.get("weight", 0),
        })
    return result or [dict(item) for item in DEFAULT_CATEGORIES]


def _category_rows(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = config.get("categories")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _category(config: Mapping[str, Any], category_id: str) -> Mapping[str, Any] | None:
    for item in _category_rows(config):
        if item.get("id") == category_id:
            return item
    return None


def _evidence_rows(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = config.get("evidence")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _has_complete_evidence(config: Mapping[str, Any]) -> bool:
    """Return true only for rows whose report producer asserts full evidence.

    The explicit ``evidence_complete`` and ``ranking_eligible`` flags are part
    of the public contract.  The additional structural checks defend the
    renderer when a hand-edited payload lies about either flag.
    """

    if config.get("ranking_eligible") is not True or config.get("evidence_complete") is not True:
        return False
    if _score(config.get("score")) is None:
        return False
    if config.get("status") in {"unranked", "incomplete", "invalid", "unverified"}:
        return False
    runs = config.get("runs")
    scheduled = config.get("scheduled_runs", 3)
    try:
        if int(runs) < 3 or int(scheduled) < 3:
            return False
    except (TypeError, ValueError):
        return False
    run_range = config.get("run_range")
    if not isinstance(run_range, Mapping) or _score(run_range.get("min")) is None or _score(run_range.get("max")) is None:
        return False
    categories = _categories({"categories": config.get("categories")})
    if any(_score(_category(config, item["id"]).get("score") if _category(config, item["id"]) else None) is None for item in categories):
        return False
    evidence = _evidence_rows(config)
    if not evidence:
        return False
    if any(not isinstance(item.get("id"), str) or not item.get("id") or not item.get("locator") for item in evidence):
        return False
    # A complete matrix contains resolved evidence, including proven losses.
    matrix = config.get("gate_matrix")
    if not isinstance(matrix, list) or not matrix:
        return False
    if any(not isinstance(item, Mapping) or item.get("state") not in RESOLVED_STATES for item in matrix):
        return False
    return True


def _ranking_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("configurations")
    configurations = [dict(item) for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []
    eligible = [item for item in configurations if _has_complete_evidence(item)]
    eligible.sort(key=lambda item: float(_published_score(item.get("score")) or 0), reverse=True)
    previous: float | None = None
    rank = 0
    for position, item in enumerate(eligible, start=1):
        score = _published_score(item.get("score"))
        if previous is None or score != previous:
            rank = position
        item["rank"] = rank
        item["display_status"] = "ranked"
        previous = score
    eligible_ids = {item.get("id") for item in eligible}
    # The display order is ranking first, then the attempted rows that cannot
    # be ranked.  Unranked rows retain source order so missing evidence remains
    # visible and cannot be mistaken for an omitted competitor.
    remainder = []
    for original in configurations:
        if original.get("id") in eligible_ids:
            continue
        original["rank"] = None
        original["display_status"] = "unranked"
        remainder.append(original)
    return eligible + remainder


def _recommendations(report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Filter producer-supplied recommendation records conservatively."""

    by_id = {item.get("id"): item for item in rows}
    raw = report.get("recommendations")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for recommendation in raw:
        if not isinstance(recommendation, Mapping) or recommendation.get("qualifies") is not True:
            continue
        target = by_id.get(recommendation.get("target_id"))
        if target is None or not _has_complete_evidence(target):
            continue
        evidence_ids = {item.get("id") for item in _evidence_rows(target)}
        citations = recommendation.get("citation_ids")
        if not isinstance(citations, list) or not citations or any(item not in evidence_ids for item in citations):
            continue
        row = dict(recommendation)
        row["target_name"] = target.get("name")
        row["target_surface"] = target.get("surface")
        row["target_rank"] = target.get("rank")
        row["citation_rows"] = [item for item in _evidence_rows(target) if item.get("id") in citations]
        result.append(row)
    return result


def _safe_report_copy(report: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = deepcopy(dict(report))
    except Exception as exc:  # pragma: no cover - defensive for unusual mappings
        raise ValueError("report must be a JSON-like mapping") from exc
    return value


def _status_label(config: Mapping[str, Any]) -> str:
    status = _text(config.get("status"), "unranked").replace("_", " ")
    return status.upper()


def _verification_label(config: Mapping[str, Any]) -> str:
    value = _text(config.get("verification"), "unverified").replace("_", " ")
    return value


def _range_label(config: Mapping[str, Any]) -> str:
    value = config.get("run_range")
    if not isinstance(value, Mapping):
        return "Run range unavailable"
    low = _score(value.get("min"))
    high = _score(value.get("max"))
    runs = config.get("runs")
    scheduled = config.get("scheduled_runs", 3)
    if low is None or high is None:
        return f"{runs or 0}/{scheduled} runs · range unavailable"
    return f"{runs or 0}/{scheduled} runs · {low:g}–{high:g} across runs"


def _bar_html(config: Mapping[str, Any], category: Mapping[str, Any], index: int) -> str:
    item = _category(config, _text(category.get("id"), ""))
    score = _score(item.get("score") if item else None)
    width = f"{score:.1f}" if score is not None else "0"
    color = CATEGORY_COLORS[index % len(CATEGORY_COLORS)]
    label = _text(category.get("name"), "Category")
    score_label = _score_label(score, dash="Unknown")
    aria = f"{label}: {score_label} out of 100"
    if score is None:
        fill = f'<span class="bar-fill bar-unknown" style="--bar-width:100%;--bar-color:{color}"></span>'
    else:
        fill = f'<span class="bar-fill" style="--bar-width:{width}%;--bar-color:{color}"></span>'
    return (
        f'<div class="category-bar" role="img" aria-label="{_esc(aria)}">'
        f'<span class="category-name">{_esc(label)}</span>'
        f'<span class="bar-track">{fill}</span>'
        f'<span class="category-value">{_esc(score_label)}</span></div>'
    )


def _gate_table(config: Mapping[str, Any]) -> str:
    matrix = config.get("gate_matrix")
    if not isinstance(matrix, list) or not matrix:
        return '<p class="empty-note">No gate observations supplied; this row cannot be ranked.</p>'
    rows: list[str] = []
    for gate in matrix:
        if not isinstance(gate, Mapping):
            continue
        state = _text(gate.get("state"), "unresolved").replace("_", " ")
        state_class = _slug(state, "unresolved")
        evidence = gate.get("evidence_id") or gate.get("locator")
        rows.append(
            "<tr>"
            f'<th scope="row"><code>{_esc(gate.get("id"), "gate")}</code> {_esc(gate.get("name"), "Gate")}</th>'
            f'<td><span class="state-dot state-{_esc(state_class)}" aria-label="{_esc(state)}"></span>{_esc(state)}</td>'
            f'<td>{_esc(gate.get("detail"), "No detail supplied")}</td>'
            f'<td class="gate-source">{_esc(evidence, "Evidence unavailable")}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table class="gate-table">'
        '<caption class="sr-only">Gate matrix for this configuration</caption>'
        '<thead><tr><th scope="col">Gate</th><th scope="col">State</th><th scope="col">Observed fact</th><th scope="col">Locator</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _timeline_table(config: Mapping[str, Any]) -> str:
    timeline = config.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        return '<p class="empty-note">No evidence comparison supplied. The evidence boundary is unresolved.</p>'
    rows: list[str] = []
    for event in timeline:
        if not isinstance(event, Mapping):
            continue
        state = _text(event.get("state"), "unresolved").replace("_", " ")
        state_class = _slug(state, "unresolved")
        locator = event.get("locator_id") or event.get("locator")
        rows.append(
            "<tr>"
            f'<th scope="row"><span class="timeline-index">{_esc(event.get("step"), "—")}</span>{_esc(event.get("label"), "Event")}</th>'
            f'<td><span class="lane-tag observed">OBSERVED</span><span class="timeline-copy">{_esc(event.get("observed"), "Unknown")}</span></td>'
            f'<td><span class="lane-tag recorded">RECORDED</span><span class="timeline-copy">{_esc(event.get("recorded"), "Unknown")}</span></td>'
            f'<td><span class="state-chip state-{_esc(state_class)}">{_esc(state)}</span><br><code>{_esc(locator, "No locator")}</code></td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table class="timeline-table">'
        '<caption class="sr-only">Evidence comparison in metric-contract order</caption>'
        '<thead><tr><th scope="col">Event</th><th scope="col">Observed by independent observer</th><th scope="col">Recorded in native artifact</th><th scope="col">Join</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def _evidence_cards(config: Mapping[str, Any]) -> str:
    evidence = _evidence_rows(config)
    if not evidence:
        return '<p class="empty-note">No evidence locator snippets were supplied.</p>'
    cards: list[str] = []
    for item in evidence:
        evidence_id = _slug(item.get("id"), "evidence")
        locator = item.get("locator")
        path = _safe_local_href(item.get("href"))
        locator_markup = _link(locator, path, class_name="locator-link") if path else _esc(locator, "Locator unavailable")
        cards.append(
            f'<article class="evidence-card" id="evidence-{_esc(evidence_id)}">'
            f'<div class="evidence-card-head"><span class="evidence-kind">{_esc(item.get("kind"), "EVIDENCE")}</span><code>{_esc(item.get("id"), "evidence")}</code></div>'
            f'<h4>{_esc(item.get("title"), "Evidence locator")}</h4>'
            f'<p class="locator">{locator_markup}</p>'
            f'<pre><code>{_esc(item.get("snippet"), "Snippet unavailable")}</code></pre>'
            f'<p class="evidence-provenance">{_esc(item.get("provenance"), "Source provenance not supplied")}</p>'
            "</article>"
        )
    return f'<div class="evidence-grid">{"".join(cards)}</div>'


def _fixes(config: Mapping[str, Any]) -> str:
    fixes = config.get("fixes")
    if not isinstance(fixes, list) or not fixes:
        return '<p class="empty-note">No corrective action supplied.</p>'
    items = []
    for item in fixes:
        if isinstance(item, Mapping):
            title = item.get("title")
            detail = item.get("detail")
        else:
            title = "Next check"
            detail = item
        items.append(f'<li><strong>{_esc(title, "Next check")}</strong><span>{_esc(detail, "No detail supplied")}</span></li>')
    return f'<ul class="fix-list">{"".join(items)}</ul>'


def _configuration_card(config: Mapping[str, Any], categories: Sequence[Mapping[str, Any]], index: int) -> str:
    config_id = _slug(config.get("id"), f"configuration-{index + 1}")
    rank = config.get("rank")
    rank_markup = f'<span class="rank-number">{_esc(rank)}</span>' if rank is not None else '<span class="rank-number rank-empty">—</span>'
    status = _status_label(config)
    verification = _verification_label(config)
    surface = _text(config.get("surface"), "Surface unknown")
    score = _score_label(config.get("score")) if rank is not None else "—"
    score_detail = "ranked score / 100" if rank is not None else "overall score withheld"
    row_class = "configuration-card ranked" if rank is not None else "configuration-card unranked"
    bars = "".join(_bar_html(config, category, idx) for idx, category in enumerate(categories))
    details_id = f"details-{config_id}"
    return f'''
    <article class="{row_class}" id="config-{_esc(config_id)}">
      <div class="configuration-topline">
        <div class="rank-cell" aria-label="{_esc(f'Rank {rank}' if rank is not None else 'Unranked')}">{rank_markup}</div>
        <div class="configuration-identity">
          <div class="identity-line"><h3>{_esc(config.get("name"), f"Configuration {index + 1}")}</h3><span class="surface-badge surface-{_esc(_slug(surface))}">{_esc(surface)}</span></div>
          <p class="config-meta">{_esc(config.get("version"), "Build unknown")} · {_esc(config.get("model"), "Model unknown")}</p>
          <div class="badge-row"><span class="status-badge status-{_esc(_slug(status))}">{_esc(status)}</span><span class="verification-badge verification-{_esc(_slug(verification))}">{_esc(verification)}</span></div>
        </div>
        <div class="score-cell"><span class="score-number">{_esc(score)}</span><span class="score-detail">{_esc(score_detail)}</span><span class="run-range">{_esc(_range_label(config))}</span></div>
      </div>
      <div class="category-grid" aria-label="Five category scores for {_esc(config.get("name"), "configuration")}">{bars}</div>
      <div class="configuration-links"><a href="#{_esc(details_id)}">Open gate matrix</a><a href="#timeline-{_esc(config_id)}">Open evidence comparison</a><a href="#evidence-{_esc(config_id)}">Open evidence</a></div>
      <details class="deep-dive" id="{_esc(details_id)}">
        <summary><span>Gate matrix</span><span class="summary-note">{_esc(len(config.get("gate_matrix", [])) if isinstance(config.get("gate_matrix"), list) else 0)} gates · expandable</span></summary>
        {_gate_table(config)}
      </details>
      <section class="fix-panel" aria-labelledby="fix-{_esc(config_id)}"><h4 id="fix-{_esc(config_id)}">To pass, fix</h4>{_fixes(config)}</section>
    </article>'''


def _recommendation_panel(report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    recommendations = _recommendations(report, rows)
    if not recommendations:
        return '''<section class="recommendations panel" id="recommendations" aria-labelledby="recommendations-title">
          <div class="section-kicker">02 / QUALIFICATION</div><h2 id="recommendations-title">What should I use?</h2>
          <p class="empty-note recommendation-empty">No citable recommendation qualifies in this report. A recommendation requires a complete ranked row and evidence locators that resolve inside that row.</p>
        </section>'''
    body: list[str] = []
    for item in recommendations:
        citations = item.get("citation_rows")
        citation_markup = []
        if isinstance(citations, list):
            for citation in citations:
                if isinstance(citation, Mapping):
                    citation_markup.append(f'<a href="#evidence-{_esc(_slug(citation.get("id"), "evidence"))}">{_esc(citation.get("id"), "locator")}</a>')
        body.append(
            '<tr>'
            f'<th scope="row">{_esc(item.get("use_case"), "Use case")}</th>'
            f'<td><strong>{_esc(item.get("target_name"), "Configuration")}</strong><br><span class="surface-mini">{_esc(item.get("target_surface"), "Surface")}</span></td>'
            f'<td>{_esc(item.get("claim"), "Claim unavailable")}<br><span class="recommendation-note">{_esc(item.get("qualification"), "Qualification note unavailable")}</span></td>'
            f'<td class="citation-cell">{" · ".join(citation_markup) or "Citations unavailable"}</td>'
            '</tr>'
        )
    return f'''<section class="recommendations panel" id="recommendations" aria-labelledby="recommendations-title">
      <div class="section-kicker">02 / QUALIFICATION</div><h2 id="recommendations-title">What should I use?</h2>
      <p class="section-lede">Recommendations are evidence-generated rows. “Audit ready” means the best preserved work trail under this task, using Fidelity and Causality only; Usage is evaluated separately, so a row can qualify here with Usage 0/15. The fixture below is a constructed control; it is not vendor guidance.</p>
      <div class="table-wrap"><table class="recommendation-table"><caption class="sr-only">Evidence-generated recommendations</caption><thead><tr><th scope="col">Use case</th><th scope="col">Qualified row</th><th scope="col">Observed claim</th><th scope="col">Citations</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>
    </section>'''


def _use_case_table(report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    use_cases = report.get("use_cases")
    if not isinstance(use_cases, list) or not use_cases:
        return ""
    configs = list(rows)
    header = "".join(f'<th scope="col">{_esc(item.get("name"), "Configuration")}</th>' for item in configs)
    body: list[str] = []
    for use_case in use_cases:
        if not isinstance(use_case, Mapping):
            continue
        values = use_case.get("results")
        values = values if isinstance(values, Mapping) else {}
        cells = []
        for config in configs:
            value = values.get(config.get("id"), "Not assessed")
            cls = _slug(value, "not-assessed")
            cells.append(f'<td><span class="qualification qualification-{_esc(cls)}">{_esc(value)}</span></td>')
        body.append(f'<tr><th scope="row">{_esc(use_case.get("label"), "Use case")}<small>{_esc(use_case.get("requirement"), "Requirement not supplied")}</small></th>{"".join(cells)}</tr>')
    return f'''<section class="use-cases panel" id="use-cases" aria-labelledby="use-cases-title">
      <div class="section-kicker">03 / USE-CASE QUALIFICATION</div><h2 id="use-cases-title">If you’re building on session files</h2>
      <p class="section-lede">The matrix keeps the v0.4 report-card question intact: which workflows does the retained record actually support?</p>
      <div class="table-wrap"><table class="use-case-table"><caption class="sr-only">Use-case qualification by configuration</caption><thead><tr><th scope="col">Workflow</th>{header}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
    </section>'''


def render_scorecard_svg(report: Mapping[str, Any]) -> str:
    """Return a self-contained SVG snapshot of the compact leaderboard."""

    categories = _categories(report)
    rows = _ranking_rows(report)
    width = 1320
    row_height = 178
    height = max(720, 180 + max(len(rows), 1) * row_height)
    rendered: list[str] = []
    for row_index, config in enumerate(rows):
        y = 170 + row_index * row_height
        name = _text(config.get("name"), f"Configuration {row_index + 1}")
        surface = _text(config.get("surface"), "Surface unknown")
        rank = config.get("rank")
        score = _score(config.get("score")) if rank is not None else None
        rank_label = f"#{rank}" if rank is not None else "UNRANKED"
        rendered.append(
            f'<g class="svg-row"><title>{_esc(name)} · {_esc(surface)} · {_esc(rank_label)}</title>'
            f'<rect x="46" y="{y}" width="1228" height="142" rx="14" class="svg-card"/>'
            f'<text x="72" y="{y + 38}" class="svg-rank">{_esc(rank_label)}</text>'
            f'<text x="165" y="{y + 36}" class="svg-name">{_esc(name)}</text>'
            f'<text x="165" y="{y + 61}" class="svg-meta">{_esc(_ellipsize(f"{surface} · {_text(config.get("version"), "Build unknown")}", 43))}</text>'
            f'<text x="72" y="{y + 93}" class="svg-score">{_esc(_score_label(score, dash="—"))}</text>'
            f'<text x="72" y="{y + 116}" class="svg-small">SCORE / 100</text>'
        )
        for category_index, category in enumerate(categories[:5]):
            item = _category(config, _text(category.get("id"), ""))
            value = _score(item.get("score") if item else None)
            x = 420
            bar_y = y + 16 + category_index * 22
            color = CATEGORY_COLORS[category_index % len(CATEGORY_COLORS)]
            rendered.append(f'<text x="{x}" y="{bar_y + 11}" class="svg-category">{_esc(category.get("name"), "Category")}</text><rect x="610" y="{bar_y}" width="520" height="11" rx="5" class="svg-track"/>')
            if value is None:
                rendered.append(f'<rect x="610" y="{bar_y}" width="520" height="11" rx="5" class="svg-unknown"/>')
            else:
                rendered.append(f'<rect x="610" y="{bar_y}" width="{5.2 * value:.1f}" height="11" rx="5" fill="{color}"/>')
            rendered.append(f'<text x="1142" y="{bar_y + 11}" class="svg-value">{_esc(_score_label(value, dash="?") )}</text>')
        rendered.append("</g>")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="scorecard-title scorecard-desc">
  <title id="scorecard-title">{_esc(report.get("edition"), "Session-Bench v1")} scorecard</title>
  <desc id="scorecard-desc">Ranked configurations with five category bars. Rows without complete evidence are marked unranked.</desc>
  <defs><pattern id="unknown" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="8" height="8" fill="#ddd4c7"/><rect width="3" height="8" fill="#b4a99b"/></pattern></defs>
  <style>
    text {{ font-family: "Avenir Next", "Helvetica Neue", sans-serif; fill: {INK}; }}
    .svg-kicker {{ font-size: 13px; font-weight: 700; letter-spacing: 2px; fill: {BLUE}; }}
    .svg-title {{ font-family: Georgia, serif; font-size: 34px; font-weight: 700; }}
    .svg-sub {{ font-size: 14px; fill: {MUTED}; }}
    .svg-card {{ fill: #fffaf3; stroke: {LINE}; }}
    .svg-rank {{ font-size: 15px; font-weight: 700; fill: {CORAL}; letter-spacing: 1px; }}
    .svg-name {{ font-family: Georgia, serif; font-size: 23px; font-weight: 700; }}
    .svg-meta, .svg-small, .svg-category {{ font-size: 11px; fill: {MUTED}; }}
    .svg-score {{ font-family: Georgia, serif; font-size: 34px; font-weight: 700; }}
    .svg-track {{ fill: #e2d9ce; }} .svg-unknown {{ fill: url(#unknown); }} .svg-value {{ font-size: 12px; font-weight: 700; }}
  </style>
  <rect width="100%" height="100%" fill="{PAPER}"/>
  <circle cx="1240" cy="55" r="30" fill="{CORAL}"/><circle cx="1255" cy="40" r="15" fill="#e1b642"/>
  <text x="46" y="48" class="svg-kicker">{_esc(report.get("edition"), "SESSION-BENCH · V1 REPORT")}</text>
  <text x="46" y="96" class="svg-title">{_esc(report.get("headline"), "What did the session keep?")}</text>
  <text x="46" y="124" class="svg-sub">{_esc(report.get("data_status"), "EVIDENCE-BOUND RESULT")} · incomplete rows remain unranked</text>
  {"".join(rendered)}
</svg>
'''


def render_index_html(report: Mapping[str, Any]) -> str:
    categories = _categories(report)
    rows = _ranking_rows(report)
    ranked_count = sum(1 for item in rows if item.get("rank") is not None)
    total_count = len(rows)
    data_status_text = _text(report.get("data_status"), "EVIDENCE STATUS UNAVAILABLE")
    constructed = bool(report.get("constructed")) or "construct" in data_status_text.lower()
    if constructed:
        data_banner = '<div class="constructed-banner"><span>CONSTRUCTED</span><p>Offline scorer and renderer control. Every displayed score is synthetic and must not be read as a vendor result.</p></div>'
    else:
        data_banner = f'<div class="constructed-banner"><span>{_esc(data_status_text)}</span><p>Evidence status supplied by the report producer.</p></div>'
    leaderboard_rows: list[str] = []
    for index, config in enumerate(rows):
        rank = config.get("rank")
        rank_markup = f'<span class="rank-pill">{_esc(rank)}</span>' if rank is not None else '<span class="rank-pill rank-unranked">—</span>'
        surface = _text(config.get("surface"), "Surface")
        status = _status_label(config)
        verification = _verification_label(config)
        score = _score_label(config.get("score")) if rank is not None else "—"
        categories_markup = "".join(_bar_html(config, category, category_index) for category_index, category in enumerate(categories))
        config_id = _slug(config.get("id"), f"configuration-{index + 1}")
        leaderboard_rows.append(f'''
        <article class="leaderboard-row {'ranked-row' if rank is not None else 'unranked-row'}" id="leaderboard-{_esc(config_id)}">
          <div class="leaderboard-rank" aria-label="{_esc(f'Rank {rank}' if rank is not None else 'Unranked')}">{rank_markup}</div>
          <div class="leaderboard-main"><div class="identity-line"><h3>{_esc(config.get("name"), f"Configuration {index + 1}")}</h3><span class="surface-badge surface-{_esc(_slug(surface))}">{_esc(surface)}</span></div><p class="config-meta">{_esc(config.get("version"), "Build unknown")} · {_esc(config.get("model"), "Model unknown")}</p><div class="badge-row"><span class="status-badge status-{_esc(_slug(status))}">{_esc(status)}</span><span class="verification-badge verification-{_esc(_slug(verification))}">{_esc(verification)}</span></div></div>
          <div class="leaderboard-score"><span class="score-number">{_esc(score)}</span><span class="score-detail">{'ranked score / 100' if rank is not None else 'overall score withheld'}</span><span class="run-range">{_esc(_range_label(config))}</span></div>
          <div class="leaderboard-bars" aria-label="Five category scores for {_esc(config.get("name"), "configuration")}">{categories_markup}</div>
          <div class="row-actions"><a href="#config-{_esc(config_id)}">Report card</a><a href="#timeline-{_esc(config_id)}">Evidence comparison</a><a href="#evidence-{_esc(config_id)}">Evidence</a></div>
        </article>''')
    report_card_markup = "".join(_configuration_card(config, categories, index) for index, config in enumerate(rows))
    timeline_blocks: list[str] = []
    for index, config in enumerate(rows):
        config_id = _slug(config.get("id"), f"configuration-{index + 1}")
        timeline_blocks.append(f'<section class="timeline-block" id="timeline-{_esc(config_id)}" aria-labelledby="timeline-heading-{_esc(config_id)}"><div class="timeline-block-head"><h3 id="timeline-heading-{_esc(config_id)}">{_esc(config.get("name"), "Configuration")}</h3><span>{_esc(_status_label(config))}</span></div>{_timeline_table(config)}</section>')
    limitations = report.get("limitations")
    limitation_items = "".join(f"<li>{_esc(item)}</li>" for item in limitations if item is not None) if isinstance(limitations, list) else "<li>Limitations were not supplied.</li>"
    method = _text(report.get("method"), "The report producer did not supply a method.")
    scope = _text(report.get("scope"), "The report producer did not supply a scope.")
    report_title = _text(report.get("title"), "Session-Bench v1 report prototype")
    generated = _text(report.get("generated_at"), "Date unknown")
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{_esc(report.get("deck"), "Session-Bench v1 report-card prototype")}">
  <title>{_esc(report_title)}</title>
  <style>
    :root {{ color-scheme: light; --paper:{PAPER}; --paper-dark:{PAPER_DARK}; --ink:{INK}; --muted:{MUTED}; --line:{LINE}; --coral:{CORAL}; --blue:{BLUE}; --moss:{MOSS}; --ochre:{OCHRE}; --plum:{PLUM}; --teal:{TEAL}; --shadow:0 18px 55px rgba(38,46,48,.08); }}
    * {{ box-sizing:border-box; }} html {{ scroll-behavior:smooth; }} body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 78% 2%,rgba(220,94,76,.10),transparent 24rem),linear-gradient(135deg,#f8f3ea 0%,var(--paper) 58%,#ede4d8 100%); font-family:"Avenir Next","Helvetica Neue",sans-serif; line-height:1.45; }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.23; background-image:linear-gradient(rgba(24,38,51,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(24,38,51,.025) 1px,transparent 1px); background-size:28px 28px; mask-image:linear-gradient(to bottom,black,transparent 70%); }}
    a {{ color:var(--blue); text-decoration-thickness:1px; text-underline-offset:3px; }} a:hover {{ color:var(--coral); }}
    .page {{ width:min(1420px,100%); margin:0 auto; padding:24px clamp(18px,4vw,62px) 80px; position:relative; }}
    .masthead {{ display:flex; justify-content:space-between; gap:24px; align-items:flex-start; padding:7px 0 26px; border-bottom:1px solid var(--line); }} .masthead-brand {{ font:700 14px/1.1 Georgia,serif; letter-spacing:.08em; text-transform:uppercase; }} .masthead-label {{ color:var(--muted); font-size:11px; letter-spacing:.16em; text-transform:uppercase; }} .masthead-nav {{ display:flex; flex-wrap:wrap; gap:16px; font-size:12px; }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.4fr) minmax(280px,.6fr); gap:42px; align-items:end; padding:58px 0 40px; }} .eyebrow,.section-kicker {{ color:var(--blue); font-size:11px; font-weight:800; letter-spacing:.18em; text-transform:uppercase; }} h1,h2,h3,h4,p {{ margin-top:0; }} h1,h2,h3,h4 {{ font-family:Georgia,"Iowan Old Style",serif; }} h1 {{ max-width:840px; margin:10px 0 18px; font-size:clamp(46px,7.4vw,104px); line-height:.91; letter-spacing:-.055em; font-weight:700; }} h1 em {{ color:var(--coral); font-style:normal; }} .hero-deck {{ max-width:720px; color:var(--muted); font-size:clamp(16px,1.8vw,21px); line-height:1.45; }} .hero-aside {{ border-left:4px solid var(--coral); padding:4px 0 4px 20px; color:var(--muted); font-size:13px; }} .hero-aside strong {{ display:block; color:var(--ink); font:700 25px/1 Georgia,serif; margin-bottom:8px; }}
    .constructed-banner {{ display:flex; align-items:center; gap:14px; padding:12px 15px; margin:0 0 26px; border:1px solid rgba(189,138,40,.45); background:rgba(255,250,243,.8); box-shadow:0 6px 20px rgba(38,46,48,.04); }} .constructed-banner span {{ color:#8a6212; font-size:11px; font-weight:900; letter-spacing:.18em; }} .constructed-banner p {{ margin:0; color:var(--muted); font-size:12px; }}
    .panel {{ margin-top:34px; padding:30px; border:1px solid var(--line); background:rgba(255,250,243,.78); box-shadow:var(--shadow); }} .panel h2 {{ margin:5px 0 8px; font-size:clamp(30px,4vw,52px); line-height:1; letter-spacing:-.035em; }} .section-lede {{ max-width:780px; color:var(--muted); font-size:14px; }}
    .leaderboard-intro {{ display:flex; justify-content:space-between; gap:30px; align-items:end; margin-bottom:18px; }} .leaderboard-intro h2 {{ margin:5px 0 4px; font-size:clamp(34px,5vw,66px); }} .leaderboard-count {{ color:var(--muted); font-size:12px; text-align:right; }}
    .leaderboard-list {{ display:grid; gap:12px; }} .leaderboard-row {{ display:grid; grid-template-columns:55px minmax(220px,1.15fr) minmax(130px,.45fr) minmax(350px,1.6fr); gap:18px; align-items:center; padding:20px; border:1px solid var(--line); background:#fffaf3; }} .ranked-row {{ border-left:5px solid var(--coral); }} .unranked-row {{ border-left:5px solid var(--unknown); background:rgba(255,250,243,.55); }} .rank-pill {{ display:grid; place-items:center; width:40px; height:40px; border-radius:50%; background:var(--ink); color:#fffaf3; font:700 17px Georgia,serif; }} .rank-unranked {{ background:var(--paper-dark); color:var(--muted); }} .identity-line {{ display:flex; align-items:center; flex-wrap:wrap; gap:9px; }} .identity-line h3 {{ margin:0; font-size:23px; line-height:1.1; letter-spacing:-.02em; }} .surface-badge,.status-badge,.verification-badge,.qualification,.lane-tag,.state-chip {{ display:inline-flex; align-items:center; width:max-content; max-width:100%; border-radius:999px; padding:4px 8px; font-size:10px; font-weight:800; letter-spacing:.08em; line-height:1.1; text-transform:uppercase; }} .surface-badge {{ border:1px solid var(--blue); color:var(--blue); background:#e9f1f4; }} .status-badge {{ color:#fff; background:var(--ink); }} .status-unranked,.status-incomplete {{ color:#725c4c; background:#e9dfd1; }} .verification-badge {{ border:1px solid var(--moss); color:var(--moss); background:#edf2e9; }} .verification-partially-verified,.verification-unverified {{ border-color:var(--ochre); color:#88630c; background:#faf1d8; }} .badge-row {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:11px; }} .config-meta,.score-detail,.run-range {{ margin:4px 0 0; color:var(--muted); font-size:11px; }} .leaderboard-score {{ display:flex; flex-direction:column; }} .score-number {{ display:block; font:700 43px/.92 Georgia,serif; letter-spacing:-.05em; }} .leaderboard-bars {{ display:grid; gap:6px; }} .category-bar {{ display:grid; grid-template-columns:minmax(124px,1fr) minmax(120px,2.25fr) 28px; align-items:center; gap:8px; min-width:0; }} .category-name,.category-value {{ color:var(--muted); font-size:10px; }} .category-value {{ color:var(--ink); font-weight:800; text-align:right; }} .bar-track {{ display:block; height:7px; overflow:hidden; border-radius:9px; background:#e4dbd0; }} .bar-fill {{ display:block; width:var(--bar-width); height:100%; border-radius:inherit; background:var(--bar-color); }} .bar-unknown {{ width:100%; opacity:.75; background:repeating-linear-gradient(135deg,#c5baac 0 3px,#e4dbd0 3px 7px); }} .row-actions {{ grid-column:2/-1; display:flex; gap:14px; flex-wrap:wrap; margin-top:1px; font-size:11px; }}
    .report-cards {{ display:grid; gap:18px; margin-top:44px; }} .configuration-card {{ padding:26px; border:1px solid var(--line); background:rgba(255,250,243,.86); scroll-margin-top:20px; }} .configuration-card.ranked {{ border-top:4px solid var(--coral); }} .configuration-card.unranked {{ border-top:4px solid var(--unknown); }} .configuration-topline {{ display:grid; grid-template-columns:55px minmax(0,1fr) minmax(180px,.5fr); gap:18px; align-items:start; }} .rank-number {{ font:700 29px/1 Georgia,serif; color:var(--coral); }} .rank-empty {{ color:var(--muted); }} .score-cell {{ text-align:right; }} .score-cell .run-range {{ display:block; }} .category-grid {{ display:grid; gap:7px; margin:22px 0; max-width:840px; }} .configuration-links {{ display:flex; flex-wrap:wrap; gap:15px; padding-top:12px; border-top:1px solid var(--line); font-size:12px; }} details.deep-dive {{ margin-top:22px; border-top:1px solid var(--line); }} details.deep-dive summary {{ cursor:pointer; display:flex; justify-content:space-between; gap:20px; padding:15px 0; font-weight:800; list-style-position:inside; }} .summary-note {{ color:var(--muted); font-size:11px; font-weight:500; }} .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; font-size:12px; }} th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} thead th {{ color:var(--muted); font-size:10px; letter-spacing:.08em; text-transform:uppercase; }} tbody th {{ font-weight:700; }} code,pre {{ font-family:"SFMono-Regular",Consolas,monospace; font-size:11px; }} .gate-source, .locator {{ color:var(--blue); }} .state-dot {{ display:inline-block; width:8px; height:8px; margin-right:6px; border-radius:50%; background:var(--ochre); }} .state-measured,.state-pass,.state-passed {{ background:var(--moss); }} .state-unresolved,.state-missing,.state-native-absent {{ background:var(--coral); }} .fix-panel {{ margin-top:25px; padding:18px 18px 16px; background:#f0e6d8; border-left:3px solid var(--ochre); }} .fix-panel h4 {{ margin-bottom:10px; font-size:20px; }} .fix-list {{ display:grid; gap:9px; margin:0; padding-left:18px; }} .fix-list li {{ padding-left:4px; }} .fix-list strong {{ display:block; font-size:12px; }} .fix-list span {{ display:block; color:var(--muted); font-size:12px; }}
    .timeline-section {{ margin-top:58px; }} .timeline-block {{ margin-top:22px; padding:21px; border:1px solid var(--line); background:rgba(255,250,243,.62); scroll-margin-top:20px; }} .timeline-block-head {{ display:flex; justify-content:space-between; gap:20px; align-items:baseline; }} .timeline-block h3 {{ margin:0 0 10px; font-size:25px; }} .timeline-block-head span {{ color:var(--muted); font-size:10px; font-weight:800; letter-spacing:.1em; }} .timeline-table th:first-child {{ min-width:150px; }} .timeline-table td {{ min-width:210px; }} .timeline-index {{ display:inline-grid; place-items:center; width:25px; height:25px; margin-right:7px; border:1px solid var(--line); border-radius:50%; color:var(--coral); font:700 11px Georgia,serif; }} .lane-tag {{ margin:0 4px 5px 0; padding:3px 6px; font-size:9px; }} .observed {{ color:var(--blue); background:#e8f0f2; }} .recorded {{ color:var(--plum); background:#f0e8ef; }} .timeline-copy {{ display:block; }} .state-chip {{ color:#775716; background:#f8edcf; }} .state-measured,.state-pass,.state-passed {{ color:#416044; background:#e6efe1; }}
    .evidence-section {{ margin-top:58px; }} .evidence-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }} .evidence-card {{ min-width:0; padding:18px; border:1px solid var(--line); background:#fffaf3; }} .evidence-card-head {{ display:flex; justify-content:space-between; gap:12px; color:var(--muted); font-size:10px; letter-spacing:.1em; text-transform:uppercase; }} .evidence-kind {{ color:var(--blue); font-weight:800; }} .evidence-card h4 {{ margin:15px 0 5px; font-size:20px; }} .evidence-card pre {{ overflow:auto; padding:11px; color:#2f3b43; white-space:pre-wrap; background:#efe8de; border-left:2px solid var(--blue); }} .evidence-provenance {{ color:var(--muted); font-size:11px; margin-bottom:0; }}
    .recommendation-empty {{ padding:18px; border-left:3px solid var(--ochre); background:#f0e6d8; }} .recommendation-table th:first-child {{ min-width:170px; }} .recommendation-table td,.recommendation-table th {{ min-width:130px; }} .recommendation-note {{ color:var(--muted); font-size:11px; }} .citation-cell a {{ display:inline-block; margin-bottom:4px; }} .surface-mini {{ color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; }}
    .qualification {{ border-radius:4px; color:var(--ink); background:#e7eee0; }} .qualification-blocked,.qualification-missing-evidence,.qualification-unresolved {{ color:#8a463b; background:#f6dfd8; }} .qualification-partial,.qualification-partially-verified {{ color:#80610d; background:#faefd0; }} .use-case-table small {{ display:block; max-width:280px; color:var(--muted); font-size:11px; font-weight:400; margin-top:4px; }}
    .method-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:17px; }} .method-grid article {{ padding:18px; border-top:3px solid var(--blue); background:rgba(255,250,243,.54); }} .method-grid h3 {{ font-size:21px; margin-bottom:7px; }} .method-grid p,.method-grid li {{ color:var(--muted); font-size:12px; }} .method-grid ul {{ margin:0; padding-left:18px; }} .site-footer {{ display:flex; justify-content:space-between; gap:18px; padding-top:30px; margin-top:55px; border-top:1px solid var(--line); color:var(--muted); font-size:11px; }} .sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }} .empty-note {{ color:var(--muted); font-size:13px; }}
    @media (max-width:1050px) {{ .leaderboard-row {{ grid-template-columns:45px minmax(180px,1fr) 120px; }} .leaderboard-bars {{ grid-column:2/-1; }} .row-actions {{ grid-column:2/-1; }} .hero {{ grid-template-columns:1fr; gap:20px; }} .hero-aside {{ max-width:500px; }} }}
    @media (max-width:760px) {{ .page {{ padding:16px 14px 55px; }} .masthead {{ display:block; }} .masthead-nav {{ margin-top:16px; }} .hero {{ padding-top:40px; }} h1 {{ font-size:clamp(48px,16vw,78px); }} .panel {{ padding:20px 15px; }} .leaderboard-intro {{ display:block; }} .leaderboard-count {{ margin-top:12px; text-align:left; }} .leaderboard-row,.configuration-topline {{ grid-template-columns:38px minmax(0,1fr); gap:12px; }} .leaderboard-score,.score-cell {{ grid-column:2; text-align:left; margin-top:8px; }} .leaderboard-bars,.category-grid,.row-actions {{ grid-column:2; }} .category-bar {{ grid-template-columns:minmax(100px,1fr) minmax(90px,1.5fr) 25px; }} .category-name {{ font-size:9px; }} .identity-line h3 {{ font-size:21px; }} .evidence-grid,.method-grid {{ grid-template-columns:1fr; }} .timeline-table,.gate-table,.recommendation-table,.use-case-table {{ min-width:680px; }} .timeline-block {{ padding:15px; }} .site-footer {{ display:block; }} .site-footer span {{ display:block; margin-top:8px; }} }}
    @media (prefers-reduced-motion:reduce) {{ html {{ scroll-behavior:auto; }} * {{ transition:none !important; animation:none !important; }} }}
  </style>
</head>
<body>
  <main class="page">
    <header class="masthead"><div><span class="masthead-brand">Session-Bench</span><span class="masthead-label"> / v1 report-card prototype</span></div><nav class="masthead-nav" aria-label="Report navigation"><a href="#leaderboard">Leaderboard</a><a href="#recommendations">Recommendations</a><a href="#method">Method</a><a href="#scope">Scope</a></nav></header>
    <section class="hero"><div><div class="eyebrow">{_esc(report.get("edition"), "V1 FIELD REPORT")}</div><h1>{_esc(report.get("headline"), "What did the session keep?")}</h1><p class="hero-deck">{_esc(report.get("deck"), "A report card for the durable, inspectable record left after a coding-agent session.")}</p></div><aside class="hero-aside"><strong>{ranked_count} of {total_count} rows rank</strong><span>Every incomplete row remains visible. The category profile can be useful before an overall score is earned.</span></aside></section>
    {data_banner}
    <section class="panel leaderboard" id="leaderboard" aria-labelledby="leaderboard-title"><div class="leaderboard-intro"><div><div class="section-kicker">01 / COMPACT LEADERBOARD</div><h2 id="leaderboard-title">The report card</h2><p class="section-lede">Five angles keep the v0.4 scan-ability; deeper evidence sits below each row. Ties use the displayed one-decimal score.</p></div><div class="leaderboard-count">{ranked_count} ranked · {total_count - ranked_count} unranked<br>Scores shown only with complete evidence</div></div><div class="leaderboard-list">{"".join(leaderboard_rows)}</div></section>
    <section class="report-cards" aria-labelledby="report-cards-title"><div class="section-kicker">REPORT CARD DETAIL</div><h2 id="report-cards-title" class="sr-only">Report card detail</h2>{report_card_markup}</section>
    {_recommendation_panel(report, rows)}
    {_use_case_table(report, rows)}
    <section class="timeline-section" aria-labelledby="timeline-title"><div class="section-kicker">04 / DEPTH</div><h2 id="timeline-title">Evidence comparison</h2><p class="section-lede">Independent observation is the left lane. Native evidence is the right lane. Rows follow metric-contract order unless temporal ordering is available. A join is only as strong as its locator.</p>{"".join(timeline_blocks)}</section>
    <section class="evidence-section" aria-labelledby="evidence-title"><div class="section-kicker">05 / CITABLE EVIDENCE</div><h2 id="evidence-title">Locator snippets</h2><p class="section-lede">Short excerpts keep the public page inspectable while the original capture remains the source of truth.</p>{"".join(f'<article class="config-evidence-block" id="evidence-{_esc(_slug(config.get("id"), f"configuration-{idx + 1}"))}"><h3>{_esc(config.get("name"), "Configuration")}</h3>{_evidence_cards(config)}</article>' for idx, config in enumerate(rows))}</section>
    <section class="method-grid panel" id="method" aria-labelledby="method-title"><article><div class="section-kicker">METHOD</div><h3 id="method-title">What is measured</h3><p>{_esc(method)}</p></article><article id="scope"><div class="section-kicker">SCOPE</div><h3>What this report means</h3><p>{_esc(scope)}</p></article><article><div class="section-kicker">LIMITATIONS</div><h3>Read the boundary</h3><ul>{limitation_items}</ul></article></section>
    <footer class="site-footer"><span>Generated {_esc(generated)} · local files only</span><span>{_esc(data_status_text)}</span><span><a href="#leaderboard">Back to leaderboard ↑</a></span></footer>
  </main>
</body>
</html>
'''


def render_report(report: Mapping[str, Any], output_dir: Path | str) -> Path:
    """Render ``index.html``, ``scorecard.svg``, and ``report.json`` locally."""

    if not isinstance(report, Mapping):
        raise TypeError("report must be a mapping")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    source = _safe_report_copy(report)
    # Rendering computes ranks transiently.  The machine-readable artifact is
    # the exact supplied payload, so downstream tools can tell what the
    # producer asserted from what the presentation inferred.
    (output / "index.html").write_text(render_index_html(source), encoding="utf-8")
    (output / "scorecard.svg").write_text(render_scorecard_svg(source), encoding="utf-8")
    (output / "report.json").write_text(json.dumps(source, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return output


__all__ = [
    "AUTHORITATIVE_REPORT_KIND",
    "AUTHORITATIVE_REPORT_SCHEMA",
    "DEFAULT_CATEGORIES",
    "REPORT_SCHEMA",
    "build_authoritative_control_report",
    "build_authoritative_report",
    "load_authoritative_report",
    "load_report",
    "render_index_html",
    "render_authoritative_report",
    "render_report",
    "render_scorecard_svg",
    "validate_authoritative_report",
]
