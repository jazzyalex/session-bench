"""Public v1 score built from survival evidence plus a format profile.

This is deliberately a second layer.  It does not change the frozen
19-metric survival scorer or reinterpret historical v0.4 observations.  A
public v1 row is only rankable when three fully resolved survival repetitions
and their twelve explicitly measured format-profile assertions are available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

from .survival_metrics import (
    ALLOWED_STATES,
    BLOCKING_STATES,
    CLI_DESKTOP_PAIRS,
    RESOLVED_STATES,
    CategoryScore,
    RunScore,
    display_decimal,
)
from .survival_campaign import PROSPECTIVE_CONFIGURATIONS
from .survival_evidence import (
    PROSPECTIVE_CONFIGURATION_IDS,
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_evidence_input,
    validate_prospective_evidence_input,
)

# v1 is a new release cohort.  The historical Cursor cohort remains valid for
# its preserved Survival edition, while public v1 uses the approved Claude pair.
TARGET_CONFIGURATIONS = PROSPECTIVE_CONFIGURATIONS
CLI_DESKTOP_PAIRS = (
    frozenset({"codex-cli", "codex-desktop"}),
    frozenset({"claude-cli", "claude-desktop"}),
)


FORMAT_PROFILE_SCHEMA_VERSION = "session-bench-format-profile-v1"
FORMAT_CONTROL_PROFILE_SCHEMA_VERSION = "session-bench-format-profile-control-v1"
FORMAT_EVIDENCE_SCHEMA_VERSION = "session-bench-format-evidence-v1"
CONFIGURATION_EVIDENCE_SCHEMA_VERSION = "session-bench-configuration-evidence-v1"

# The classifier is intentionally small and closed.  A producer must classify
# each logical record by its decoded role, rather than by filename, byte size,
# or an ad-hoc content-key guess.  The stable rule identifier is carried in
# every density assertion so a future classifier cannot silently rewrite old
# scores.
CLASSIFIED_CONTENT_DENSITY_RULE = "logical-record-role-v1"
_CONTENT_DENSITY_USEFUL_ROLES = frozenset({
    "user_message", "correction", "assistant_message", "tool_call",
    "tool_result", "failure", "file_change", "plan", "explanation",
})
_CONTENT_DENSITY_UNCLASSIFIED_ROLES = frozenset({
    "metadata", "index", "snapshot", "session", "system",
})
_CONTENT_DENSITY_ROLE_CLASS = {
    **{role: "useful" for role in _CONTENT_DENSITY_USEFUL_ROLES},
    **{role: "unclassified" for role in _CONTENT_DENSITY_UNCLASSIFIED_ROLES},
    "unknown": "unknown",
}


@dataclass(frozen=True)
class PublicMetricSpec:
    category: str
    points: Fraction
    source: str  # "survival" or "format_profile"


@dataclass(frozen=True)
class PublicEvidenceLocator:
    """One immutable native or documentation locator for a scored metric."""

    artifact_id: str
    artifact_sha256: str
    record_location: str | None = None

    def display(self) -> dict[str, str]:
        result = {
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
        }
        if self.record_location is not None:
            result["record_location"] = self.record_location
        return result


@dataclass(frozen=True)
class PublicMetricEvidence:
    """Normalized proof and canonical state for exactly one public metric."""

    metric_id: str
    state: str
    observer_ids: tuple[str, ...]
    native_locators: tuple[PublicEvidenceLocator, ...]

    def display(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "state": self.state,
            "observer_ids": list(self.observer_ids),
            "native_locators": [locator.display() for locator in self.native_locators],
        }


@dataclass(frozen=True)
class PublicRunIdentity:
    """The identity envelope bound to one public repetition.

    The survival evidence wrapper supplies the protocol/workload/rubric and
    run/capture/evaluation identities.  The optional envelope carries the
    provider, harness, surface, execution mode, OS, model, and declared
    configuration when the collector has actually captured them.  ``None`` is
    retained for fields the current evidence schema did not provide.
    """

    provider: str | None
    harness: str | None
    surface: str | None
    execution_mode: str | None
    os_name: str | None
    build: str | None
    model: str | None
    configuration: str | None
    protocol_version: str
    workload_version: str
    observer_schema_version: str | None
    rubric_version: str
    configuration_id: str
    run_id: str
    capture_id: str
    evaluation_id: str
    repetition: int
    collected_on: str | None
    result_id: str | None

    def display(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "harness": self.harness,
            "surface": self.surface,
            "execution_mode": self.execution_mode,
            "os": self.os_name,
            "build": self.build,
            "model": self.model,
            "configuration": self.configuration,
            "configuration_id": self.configuration_id,
            "protocol_version": self.protocol_version,
            "workload_version": self.workload_version,
            "observer_schema_version": self.observer_schema_version,
            "rubric_version": self.rubric_version,
            "run_id": self.run_id,
            "capture_id": self.capture_id,
            "evaluation_id": self.evaluation_id,
            "repetition": self.repetition,
            "collected_on": self.collected_on,
            "result_id": self.result_id,
            "unavailable": [
                field
                for field, value in (
                    ("provider", self.provider),
                    ("harness", self.harness),
                    ("surface", self.surface),
                    ("execution_mode", self.execution_mode),
                    ("os", self.os_name),
                    ("model", self.model),
                    ("configuration", self.configuration),
                    ("observer_schema_version", self.observer_schema_version),
                )
                if value is None
            ],
        }


@dataclass(frozen=True)
class PublicTimelineEvent:
    """One metric-level observed/native evidence row for a run.

    The current frozen survival evidence schema has observer IDs and native
    locators, but no event timestamps, labels, or sequence numbers.  The
    public timeline therefore exposes the evidence rows in frozen metric
    contract order and labels that order explicitly instead of inventing a
    temporal sequence or event prose.
    """

    step: int
    metric_id: str
    state: str
    observer_ids: tuple[str, ...]
    native_locators: tuple[PublicEvidenceLocator, ...]

    def display(self) -> dict[str, Any]:
        observed = ", ".join(self.observer_ids)
        recorded = ", ".join(
            locator.record_location or locator.artifact_id
            for locator in self.native_locators
        )
        return {
            "step": self.step,
            "label": self.metric_id,
            "metric_id": self.metric_id,
            "state": self.state,
            "observed": f"observer_ids: {observed}",
            "recorded": f"native_locators: {recorded}",
            "observer_ids": list(self.observer_ids),
            "native_locators": [locator.display() for locator in self.native_locators],
            "order_basis": "survival_metric_contract",
            "temporal_order_available": False,
        }


@dataclass(frozen=True)
class ImmutablePublicBundle:
    """The immutable public bundle proven for one three-run configuration."""

    id: str
    sha256: str
    immutable: bool
    public: bool
    result_ids: tuple[str, ...]

    def display(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sha256": self.sha256,
            "immutable": self.immutable,
            "public": self.public,
            "result_ids": list(self.result_ids),
        }


@dataclass(frozen=True)
class ReproductionReceipt:
    """Deterministic offline recomputation proof bound to an immutable bundle."""

    id: str
    sha256: str
    bundle_sha256: str
    offline_recomputed: bool
    verified: bool
    result_ids: tuple[str, ...]

    def display(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sha256": self.sha256,
            "bundle_sha256": self.bundle_sha256,
            "offline_recomputed": self.offline_recomputed,
            "verified": self.verified,
            "result_ids": list(self.result_ids),
        }


@dataclass(frozen=True)
class ConfigurationEvidenceBinding:
    """Validated immutable bundle and deterministic offline receipt for one configuration."""

    configuration_id: str
    bundle: ImmutablePublicBundle
    reproduction_receipt: ReproductionReceipt


# The public categories are a frozen product view.  Every source assertion is
# represented once, and points intentionally sum to 100 without rounding.
PUBLIC_CATEGORY_POINTS: dict[str, Fraction] = {
    "record_fidelity": Fraction(30),
    "causality_context": Fraction(20),
    "usage_attribution": Fraction(15),
    "portability_openness": Fraction(20),
    "durability_signal": Fraction(15),
}

PUBLIC_METRICS: dict[str, PublicMetricSpec] = {
    # The 19 frozen survival assertions (70 points).
    "work.submitted_turns": PublicMetricSpec("record_fidelity", Fraction(4), "survival"),
    "work.visible_responses": PublicMetricSpec("record_fidelity", Fraction(4), "survival"),
    "work.actions": PublicMetricSpec("record_fidelity", Fraction(5), "survival"),
    "work.results": PublicMetricSpec("record_fidelity", Fraction(5), "survival"),
    "work.changed_files": PublicMetricSpec("record_fidelity", Fraction(4), "survival"),
    "revision.r1": PublicMetricSpec("record_fidelity", Fraction(2), "survival"),
    "revision.r2": PublicMetricSpec("record_fidelity", Fraction(2), "survival"),
    "revision.r1_r2_order": PublicMetricSpec("record_fidelity", Fraction(2), "survival"),
    "revision.final_after_r2": PublicMetricSpec("record_fidelity", Fraction(2), "survival"),
    "causal.action_result": PublicMetricSpec("causality_context", Fraction(7), "survival"),
    "causal.turn_response": PublicMetricSpec("causality_context", Fraction(6), "survival"),
    "attribution.model_config": PublicMetricSpec("usage_attribution", Fraction(3), "survival"),
    "attribution.usage": PublicMetricSpec("usage_attribution", Fraction(5), "survival"),
    "attribution.token_semantics": PublicMetricSpec("usage_attribution", Fraction(4), "survival"),
    "attribution.reconciliation": PublicMetricSpec("usage_attribution", Fraction(3), "survival"),
    "portable.complete_root": PublicMetricSpec("portability_openness", Fraction(3), "survival"),
    "portable.companions": PublicMetricSpec("portability_openness", Fraction(2), "survival"),
    "portable.isolated_decode": PublicMetricSpec("portability_openness", Fraction(4), "survival"),
    "portable.canonical_equality": PublicMetricSpec("portability_openness", Fraction(3), "survival"),
    # Twelve separate, frozen broad-format assertions (30 points).
    "broad.readable_rationale": PublicMetricSpec("causality_context", Fraction(4), "format_profile"),
    "broad.thread_structure": PublicMetricSpec("causality_context", Fraction(3), "format_profile"),
    "broad.standard_tools_readable": PublicMetricSpec("portability_openness", Fraction(2), "format_profile"),
    "broad.documented_format": PublicMetricSpec("portability_openness", Fraction(2), "format_profile"),
    "broad.self_contained_identity": PublicMetricSpec("portability_openness", Fraction(2), "format_profile"),
    "broad.declared_format_version": PublicMetricSpec("portability_openness", Fraction(2), "format_profile"),
    "broad.event_timestamps": PublicMetricSpec("durability_signal", Fraction(2), "format_profile"),
    "broad.honest_version_signal": PublicMetricSpec("durability_signal", Fraction(2), "format_profile"),
    "broad.observed_schema_stability": PublicMetricSpec("durability_signal", Fraction(3), "format_profile"),
    "broad.stable_root_location": PublicMetricSpec("durability_signal", Fraction(2), "format_profile"),
    "broad.naive_reader_duplicate_safety": PublicMetricSpec("durability_signal", Fraction(3), "format_profile"),
    "broad.classified_content_density": PublicMetricSpec("durability_signal", Fraction(3), "format_profile"),
}
FORMAT_METRICS = tuple(metric_id for metric_id, spec in PUBLIC_METRICS.items() if spec.source == "format_profile")
SURVIVAL_METRICS = tuple(metric_id for metric_id, spec in PUBLIC_METRICS.items() if spec.source == "survival")

PUBLIC_WEIGHT_VECTORS: dict[str, dict[str, int]] = {
    "baseline": {"record_fidelity": 30, "causality_context": 20, "usage_attribution": 15, "portability_openness": 20, "durability_signal": 15},
    "equal": {"record_fidelity": 20, "causality_context": 20, "usage_attribution": 20, "portability_openness": 20, "durability_signal": 20},
    "record_fidelity_heavy": {"record_fidelity": 40, "causality_context": 15, "usage_attribution": 15, "portability_openness": 15, "durability_signal": 15},
    "portability_openness_heavy": {"record_fidelity": 25, "causality_context": 15, "usage_attribution": 15, "portability_openness": 30, "durability_signal": 15},
}


@dataclass(frozen=True)
class PublicRunScore:
    run_id: str
    configuration_id: str
    repetition: int
    metrics: Mapping[str, Fraction | None]
    categories: Mapping[str, CategoryScore]
    overall: Fraction | None
    portable_gate: bool
    rankable: bool
    blockers: tuple[str, ...]
    survival_run: RunScore
    metric_evidence: Mapping[str, PublicMetricEvidence]
    build: str | None = None
    collected_on: str | None = None
    result_id: str | None = None
    survival_evaluation_id: str | None = None
    identity: PublicRunIdentity | None = None
    timeline: tuple[PublicTimelineEvent, ...] = ()

    def display(self) -> dict[str, Any]:
        def category_display(category: str, score: CategoryScore) -> dict[str, str | int | None]:
            maximum = PUBLIC_CATEGORY_POINTS[category]
            return {
                "score": display_decimal(score.score),
                "known_points": display_decimal(score.known_points),
                "known_possible_points": display_decimal(score.known_possible_points),
                "known_quality_percent": display_decimal(score.known_quality * 100 if score.known_quality is not None else None),
                "coverage": f"{score.resolved_metrics}/{score.total_metrics}",
                "possible_min": display_decimal(score.possible_min),
                "possible_max": display_decimal(score.possible_max),
                "possible_percent_min": display_decimal(score.possible_min * 100 / maximum),
                "possible_percent_max": display_decimal(score.possible_max * 100 / maximum),
            }

        return {
            "run_id": self.run_id,
            "configuration_id": self.configuration_id,
            "repetition": self.repetition,
            "overall": display_decimal(self.overall),
            "rankable": self.rankable,
            "portable_gate": self.portable_gate,
            "metrics": {key: display_decimal(value * 100 if value is not None else None) for key, value in self.metrics.items()},
            "metric_evidence": {
                metric_id: evidence.display()
                for metric_id, evidence in self.metric_evidence.items()
            },
            "identity": None if self.identity is None else self.identity.display(),
            "timeline": [event.display() for event in self.timeline],
            "timeline_order_basis": "survival_metric_contract",
            "temporal_timeline_available": False,
            "categories": {key: category_display(key, value) for key, value in self.categories.items()},
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class PublicConfigurationScore:
    configuration_id: str
    runs: tuple[PublicRunScore, ...]
    categories: Mapping[str, Fraction | None]
    category_ranges: Mapping[str, tuple[Fraction, Fraction] | None]
    overall: Fraction | None
    overall_range: tuple[Fraction, Fraction] | None
    sensitivity: Mapping[str, Fraction] | None
    portable_gate: bool
    rankable: bool
    blockers: tuple[str, ...]
    verification: str = "unranked"
    bundle: ImmutablePublicBundle | None = None
    reproduction_receipt: ReproductionReceipt | None = None
    identities: tuple[PublicRunIdentity | None, ...] = ()
    timelines: tuple[tuple[int, tuple[PublicTimelineEvent, ...]], ...] = ()

    @property
    def bundle_id(self) -> str | None:
        """Compatibility view of the immutable bundle identity."""

        return None if self.bundle is None else self.bundle.id

    @property
    def reproduction_receipt_id(self) -> str | None:
        """Compatibility view of the offline recomputation receipt identity."""

        return None if self.reproduction_receipt is None else self.reproduction_receipt.id

    def display(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "overall": display_decimal(self.overall),
            "range": None if self.overall_range is None else [display_decimal(value) for value in self.overall_range],
            "rankable": self.rankable,
            "portable_gate": self.portable_gate,
            "categories": {key: display_decimal(value) for key, value in self.categories.items()},
            "category_ranges": {key: None if value is None else [display_decimal(point) for point in value] for key, value in self.category_ranges.items()},
            "sensitivity": None if self.sensitivity is None else {key: display_decimal(value) for key, value in self.sensitivity.items()},
            "blockers": list(self.blockers),
            "verification": self.verification,
            "bundle_id": self.bundle_id,
            "reproduction_receipt_id": self.reproduction_receipt_id,
            "bundle": None if self.bundle is None else self.bundle.display(),
            "reproduction_receipt": None if self.reproduction_receipt is None else self.reproduction_receipt.display(),
            "identities": [None if identity is None else identity.display() for identity in self.identities],
            "timelines": [
                {
                    "repetition": repetition,
                    "events": [event.display() for event in events],
                    "order_basis": "survival_metric_contract",
                    "temporal_order_available": False,
                }
                for repetition, events in self.timelines
            ],
        }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(f"{label}: wrong fields (missing={sorted(expected - actual)}, extra={sorted(actual - expected)})")


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _complete_assertion(detail: Mapping[str, Any], passed: bool) -> dict[str, Any]:
    if detail["evidence_complete"] is not True:
        return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
    return {"state": "measured" if passed else "native_absent", "correct": int(passed), "observed_eligible": 1, "decoded_eligible": 1}


def _id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item or item != item.strip() for item in value) or len(set(value)) != len(value):
        raise ValueError(f"{label} must be a non-empty unique identifier list")
    return list(value)


def _population(detail: Mapping[str, Any], required_key: str, records_key: str, valid) -> dict[str, Any]:
    required = _id_list(detail[required_key], required_key)
    records = detail[records_key]
    if not isinstance(records, list):
        raise ValueError(f"{records_key} must be an array")
    if detail["evidence_complete"] is not True:
        return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
    correct = sum(1 for item in records if isinstance(item, Mapping) and item.get("id") in required and valid(item))
    return {"state": "measured" if correct else "native_absent", "correct": correct, "observed_eligible": len(required), "decoded_eligible": len(records)}


def _thread_structure_valid(detail: Mapping[str, Any]) -> bool:
    """Validate explicit parentage or the frozen linear recovery rule.

    When a format does not persist parent links, a linear sequence is still
    recoverable if the first turn is a user turn and every assistant/tool turn
    follows a user turn.  The nearest preceding user is then its unique parent;
    no title or timestamp inference is involved.  A supplied parent link in
    this mode must agree with that derived parent.
    """

    session_id = detail["session_id"]
    explicit_parentage = detail["explicit_parentage"]
    turns = detail["turns"]
    if (
        not isinstance(session_id, str)
        or not session_id.strip()
        or not isinstance(explicit_parentage, bool)
        or not isinstance(turns, list)
        or not turns
    ):
        return False

    seen: set[str] = set()
    previous = 0
    last_user_id: str | None = None
    for turn in turns:
        if (
            not isinstance(turn, Mapping)
            or set(turn) != {"id", "role", "ordinal", "parent_id"}
            or not isinstance(turn["id"], str)
            or not turn["id"].strip()
            or turn["id"] in seen
            or turn["role"] not in {"user", "assistant", "tool"}
            or not _is_int(turn["ordinal"])
            or turn["ordinal"] <= previous
            or (
                turn["parent_id"] is not None
                and (
                    not isinstance(turn["parent_id"], str)
                    or not turn["parent_id"].strip()
                )
            )
        ):
            return False
        seen.add(turn["id"])
        previous = turn["ordinal"]
        if turn["role"] == "user":
            last_user_id = turn["id"]
            continue
        if last_user_id is None:
            # A non-user root has no deterministic turn boundary in this
            # contract, even when the format omitted parent links.
            if not explicit_parentage:
                return False
            continue
        if not explicit_parentage and turn["parent_id"] not in {None, last_user_id}:
            return False

    return last_user_id is not None


def _event_timestamp_valid(record: Mapping[str, Any]) -> bool:
    """Validate the value according to its declared timestamp unit."""

    if (
        set(record) != {"id", "timestamp", "unit", "time_zone"}
        or not isinstance(record["id"], str)
        or not record["id"].strip()
        or record["unit"] not in {"rfc3339", "unix_ms", "unix_s"}
        or not isinstance(record["time_zone"], str)
        or not record["time_zone"].strip()
    ):
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

    # Unix timestamps are numbers in the declared unit.  A numeric-looking
    # label is not a timestamp, and booleans must not pass as zero/one.
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return False
    if isinstance(timestamp, float) and not isfinite(timestamp):
        return False
    seconds = timestamp / 1000 if record["unit"] == "unix_ms" else timestamp
    try:
        datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def _broad_metric(metric_id: str, detail: Any) -> dict[str, Any]:
    """Derive each broad row from its own operational evidence, never counters."""
    if not isinstance(detail, Mapping) or "evidence_complete" not in detail or not isinstance(detail["evidence_complete"], bool):
        raise ValueError(f"{metric_id} requires boolean evidence_complete")
    if metric_id == "broad.readable_rationale":
        _exact_keys(detail, {"evidence_complete", "response_ids", "records"}, metric_id)
        def valid(record: Mapping[str, Any]) -> bool:
            return set(record) == {"id", "ordered_text"} and isinstance(record["ordered_text"], str) and bool(record["ordered_text"].strip())
        return _population(detail, "response_ids", "records", valid)
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
        # The stable public ID is retained for compatibility.  Its current
        # meaning is observed decoder-contract compatibility within the
        # captured build/date window, not a longitudinal stability claim.
        valid = isinstance(detail["advertised_contract"], str) and bool(detail["advertised_contract"].strip()) and isinstance(observations, list) and bool(observations) and detail["exceptions"] == []
        if valid:
            seen_observations: set[tuple[str, str]] = set()
            for item in observations:
                if not isinstance(item, Mapping) or set(item) != {"build", "observed_on", "decoder_contract", "decoded"} or not isinstance(item["build"], str) or not item["build"].strip() or not isinstance(item["observed_on"], str) or item["decoder_contract"] != detail["advertised_contract"] or item["decoded"] is not True:
                    valid = False; break
                try: date.fromisoformat(item["observed_on"])
                except ValueError: valid = False; break
                key = (item["build"], item["observed_on"])
                if key in seen_observations:
                    valid = False; break
                seen_observations.add(key)
        return _complete_assertion(detail, valid)
    if metric_id == "broad.stable_root_location":
        _exact_keys(detail, {"evidence_complete", "repetitions"}, metric_id)
        repetitions = detail["repetitions"]
        valid = isinstance(repetitions, list) and {item.get("repetition") for item in repetitions if isinstance(item, Mapping)} == {1, 2, 3} and len(repetitions) == 3
        if valid:
            valid = all(
                isinstance(item["root_locator"], str)
                and item["root_locator"].strip()
                and item["personal_history_scanned"] is False
                and (
                    (
                        set(item) == {"repetition", "root_locator", "isolated_discovery", "personal_history_scanned"}
                        and item["isolated_discovery"] is True
                    )
                    or (
                        set(item) == {"repetition", "root_locator", "discovery_mode", "personal_history_scanned"}
                        and item["discovery_mode"] in {"isolated", "metadata_safe_normal_root"}
                    )
                )
                for item in repetitions
            )
        return _complete_assertion(detail, valid)
    if metric_id == "broad.naive_reader_duplicate_safety":
        _exact_keys(detail, {"evidence_complete", "event_ids", "forward_records", "deduplication"}, metric_id)
        ids = _id_list(detail["event_ids"], "event_ids")
        records, dedup = detail["forward_records"], detail["deduplication"]
        if not isinstance(records, list) or not isinstance(dedup, Mapping) or set(dedup) != {"documented", "rule"} or not isinstance(dedup["rule"], str):
            raise ValueError(f"{metric_id} requires forward records and documented deduplication")
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
            if len(active) == 1 and (len(values) == 1 or (dedup["documented"] is True and bool(dedup["rule"].strip()))): correct += 1
        return {"state": "measured" if correct else "native_absent", "correct": correct, "observed_eligible": len(ids), "decoded_eligible": len(records)}
    if metric_id == "broad.classified_content_density":
        _exact_keys(detail, {"evidence_complete", "classification_rule", "records"}, metric_id)
        if detail["classification_rule"] != CLASSIFIED_CONTENT_DENSITY_RULE:
            raise ValueError(f"{metric_id} requires {CLASSIFIED_CONTENT_DENSITY_RULE}")
        records = detail["records"]
        if not isinstance(records, list) or not records:
            raise ValueError(f"{metric_id} records must be non-empty")
        total = useful = 0
        record_ids: set[str] = set()
        for record in records:
            if (
                not isinstance(record, Mapping)
                or set(record) != {"record_id", "record_kind", "logical_bytes", "classification"}
                or not isinstance(record["record_id"], str)
                or not record["record_id"].strip()
                or record["record_id"] in record_ids
                or record["record_kind"] not in _CONTENT_DENSITY_ROLE_CLASS
                or record["classification"] != _CONTENT_DENSITY_ROLE_CLASS[record["record_kind"]]
                or not _is_int(record["logical_bytes"])
                or record["logical_bytes"] < 0
            ):
                raise ValueError(f"{metric_id} has invalid logical-byte record")
            record_ids.add(record["record_id"])
            total += record["logical_bytes"]
            if record["classification"] == "useful": useful += record["logical_bytes"]
        if total == 0:
            raise ValueError(f"{metric_id} total logical bytes must be positive")
        if detail["evidence_complete"] is not True:
            return {"state": "unresolved", "correct": 0, "observed_eligible": 0, "decoded_eligible": 0}
        return {"state": "measured" if useful else "native_absent", "correct": useful, "observed_eligible": total, "decoded_eligible": total}
    raise AssertionError(f"unhandled broad metric {metric_id}")


def validate_format_profile(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate broad evidence and derive the twelve scored rows from it."""
    if not isinstance(document, Mapping):
        raise ValueError("format profile must be an object")
    _exact_keys(document, {"schema_version", "run_id", "configuration_id", "repetition", "broad_evidence"}, "format profile")
    if document["schema_version"] != FORMAT_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported format profile schema version")
    run_id = _identity(document["run_id"], "format profile run_id")
    configuration_id = _identity(document["configuration_id"], "format profile configuration_id")
    repetition = document["repetition"]
    if not _is_int(repetition) or repetition < 1:
        raise ValueError("format profile repetition must be a positive integer")
    broad = document["broad_evidence"]
    if not isinstance(broad, Mapping) or set(broad) != set(FORMAT_METRICS):
        raise ValueError("format profile must contain every required broad evidence record")
    normalized = [{"id": metric_id, **_broad_metric(metric_id, broad[metric_id])} for metric_id in FORMAT_METRICS]
    return {"schema_version": FORMAT_PROFILE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "metrics": normalized}


def format_profile_document(*, run_id: str, configuration_id: str, repetition: int) -> dict[str, Any]:
    """Construct conspicuously synthetic broad evidence for evaluator controls."""
    return {
        "schema_version": FORMAT_PROFILE_SCHEMA_VERSION,
        "run_id": run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
        "broad_evidence": {
            "broad.readable_rationale": {"evidence_complete": True, "response_ids": ["response-1", "response-2"], "records": [{"id": "response-1", "ordered_text": "synthetic rationale one"}, {"id": "response-2", "ordered_text": "synthetic rationale two"}]},
            "broad.thread_structure": {"evidence_complete": True, "session_id": "synthetic-session", "explicit_parentage": True, "turns": [{"id": "turn-1", "role": "user", "ordinal": 1, "parent_id": None}, {"id": "turn-2", "role": "assistant", "ordinal": 2, "parent_id": "turn-1"}]},
            "broad.standard_tools_readable": {"evidence_complete": True, "container": "jsonl", "parser": "synthetic-json-parser", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
            "broad.documented_format": {"evidence_complete": True, "document_id": "synthetic-format-doc", "mapping": {"containers": "line 1", "record_types": "line 2", "identities": "line 3", "joins": "line 4", "version_semantics": "line 5"}},
            "broad.self_contained_identity": {"evidence_complete": True, "session_id": "synthetic-session", "harness": "constructed", "surface": configuration_id, "record_family": "synthetic-jsonl", "external_lookup_required": False, "absolute_path_required": False},
            "broad.declared_format_version": {"evidence_complete": True, "format_version": "1", "machine_readable": True, "bundle_binding": "synthetic-native-manifest"},
            "broad.event_timestamps": {"evidence_complete": True, "event_ids": ["event-1", "event-2"], "records": [{"id": "event-1", "timestamp": "2026-09-11T00:00:00Z", "unit": "rfc3339", "time_zone": "UTC"}, {"id": "event-2", "timestamp": "2026-09-11T00:00:01Z", "unit": "rfc3339", "time_zone": "UTC"}]},
            "broad.honest_version_signal": {"evidence_complete": True, "declared_version": "1", "decoder_contract_version": "1", "incompatible_schema_distinguished": True, "matches_decoder_contract": True},
            "broad.observed_schema_stability": {"evidence_complete": True, "advertised_contract": "1", "observations": [{"build": "synthetic-build", "observed_on": "2026-09-11", "decoder_contract": "1", "decoded": True}], "exceptions": []},
            "broad.stable_root_location": {"evidence_complete": True, "repetitions": [{"repetition": 1, "root_locator": "synthetic/root/1", "isolated_discovery": True, "personal_history_scanned": False}, {"repetition": 2, "root_locator": "synthetic/root/2", "isolated_discovery": True, "personal_history_scanned": False}, {"repetition": 3, "root_locator": "synthetic/root/3", "isolated_discovery": True, "personal_history_scanned": False}]},
            "broad.naive_reader_duplicate_safety": {"evidence_complete": True, "event_ids": ["event-1", "event-2"], "forward_records": [{"event_id": "event-1", "occurrence_id": "one", "state": "active"}, {"event_id": "event-2", "occurrence_id": "two", "state": "active"}], "deduplication": {"documented": True, "rule": "synthetic forward reader"}},
            "broad.classified_content_density": {"evidence_complete": True, "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": [{"record_id": "one", "record_kind": "assistant_message", "logical_bytes": 50, "classification": "useful"}, {"record_id": "two", "record_kind": "tool_result", "logical_bytes": 50, "classification": "useful"}]},
        },
    }


def _fraction(row: Mapping[str, Any]) -> Fraction | None:
    if row["state"] in BLOCKING_STATES:
        return None
    if row["state"] in {"native_absent", "contradiction"}:
        return Fraction(0)
    return Fraction(row["correct"], max(row["observed_eligible"], row["decoded_eligible"]))


def _digest_identifier(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"id", "sha256"}:
        raise ValueError(f"{label} must have id and sha256")
    identifier, digest = value["id"], value["sha256"]
    if not isinstance(identifier, str) or not identifier or identifier != identifier.strip():
        raise ValueError(f"{label}.id must be a non-empty trimmed string")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError(f"{label}.sha256 must be a lowercase SHA-256 digest")
    return {"id": identifier, "sha256": digest}


def _format_metric_evidence(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(FORMAT_METRICS):
        raise ValueError("format metric evidence must contain every format metric exactly once")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != {"metric_id", "observer_ids", "native_locators"}:
            raise ValueError(f"format metric evidence {index} has wrong fields")
        metric_id = row["metric_id"]
        observers, locators = row["observer_ids"], row["native_locators"]
        if metric_id not in FORMAT_METRICS or metric_id in ids:
            raise ValueError("format metric evidence has unknown or duplicate metric")
        if not isinstance(observers, list) or not observers or len(set(observers)) != len(observers) or any(not isinstance(item, str) or not item for item in observers):
            raise ValueError("format metric evidence observer_ids must be unique non-empty strings")
        if not isinstance(locators, list) or not locators:
            raise ValueError("format metric evidence native_locators must be non-empty")
        normalized_locators = [_digest_identifier(locator, "format metric locator") for locator in locators]
        if len({(locator["id"], locator["sha256"]) for locator in normalized_locators}) != len(normalized_locators):
            raise ValueError("format metric evidence native_locators must be unique")
        ids.add(metric_id)
        result.append({"metric_id": metric_id, "observer_ids": list(observers), "native_locators": normalized_locators})
    return result


def _metric_states(rows: Any, metric_ids: Sequence[str], label: str) -> dict[str, str]:
    """Read canonical metric states without accepting a caller-supplied summary."""

    if not isinstance(rows, list):
        raise ValueError(f"{label} metrics must be an array")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} metric must be an object")
        metric_id = row.get("id")
        state = row.get("state")
        if metric_id not in metric_ids or metric_id in result or state not in ALLOWED_STATES:
            raise ValueError(f"{label} does not contain the exact canonical metric states")
        result[metric_id] = state
    if set(result) != set(metric_ids):
        raise ValueError(f"{label} does not contain the exact canonical metric states")
    return result


def _proof_rows(
    rows: Any,
    *,
    metric_ids: Sequence[str],
    states: Mapping[str, str],
    source: str,
) -> dict[str, PublicMetricEvidence]:
    """Normalize evidence already validated by a reportable evidence wrapper."""

    if not isinstance(rows, list) or len(rows) != len(metric_ids):
        raise ValueError(f"{source} metric evidence must contain the exact metric set")
    result: dict[str, PublicMetricEvidence] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{source} metric evidence row must be an object")
        metric_id = row.get("metric_id")
        if metric_id not in metric_ids or metric_id in result:
            raise ValueError(f"{source} metric evidence must contain the exact metric set")
        observers = row.get("observer_ids")
        locators = row.get("native_locators")
        if not isinstance(observers, list) or not observers or not isinstance(locators, list) or not locators:
            raise ValueError(f"{source} metric evidence is incomplete")
        normalized_observers = tuple(_identity(item, f"{source} observer ID") for item in observers)
        if len(set(normalized_observers)) != len(normalized_observers):
            raise ValueError(f"{source} metric evidence has duplicate observer IDs")
        normalized_locators: list[PublicEvidenceLocator] = []
        for locator in locators:
            if source == "survival":
                if not isinstance(locator, Mapping) or set(locator) != {"artifact_id", "artifact_sha256", "record_location"}:
                    raise ValueError("survival metric evidence has an invalid native locator")
                normalized_locators.append(PublicEvidenceLocator(
                    _identity(locator["artifact_id"], "survival artifact ID"),
                    _digest_identifier({"id": locator["artifact_id"], "sha256": locator["artifact_sha256"]}, "survival artifact")["sha256"],
                    _identity(locator["record_location"], "survival record location"),
                ))
            else:
                normalized = _digest_identifier(locator, "format metric locator")
                normalized_locators.append(PublicEvidenceLocator(normalized["id"], normalized["sha256"]))
        if len({(item.artifact_id, item.artifact_sha256, item.record_location) for item in normalized_locators}) != len(normalized_locators):
            raise ValueError(f"{source} metric evidence has duplicate native locators")
        result[metric_id] = PublicMetricEvidence(metric_id, states[metric_id], normalized_observers, tuple(normalized_locators))
    if set(result) != set(metric_ids):
        raise ValueError(f"{source} metric evidence must contain the exact metric set")
    return result


def _reportable_metric_evidence(survival: Mapping[str, Any], format_evidence: Mapping[str, Any]) -> dict[str, PublicMetricEvidence]:
    survival_states = _metric_states(survival["measurement"]["metrics"], SURVIVAL_METRICS, "survival measurement")
    format_states = _metric_states(format_evidence["profile"]["metrics"], FORMAT_METRICS, "format profile")
    result = _proof_rows(
        survival["metric_evidence"], metric_ids=SURVIVAL_METRICS,
        states=survival_states, source="survival",
    )
    result.update(_proof_rows(
        format_evidence["metric_evidence"], metric_ids=FORMAT_METRICS,
        states=format_states, source="format",
    ))
    if set(result) != set(PUBLIC_METRICS):  # defensive: both source sets are frozen.
        raise ValueError("reportable metric evidence must cover all 31 public metrics")
    return result


def _constructed_metric_evidence(survival_document: Mapping[str, Any], profile: Mapping[str, Any]) -> dict[str, PublicMetricEvidence]:
    """Explicit constructed-only proofs for raw scorer controls.

    This path is deliberately unavailable to ``score_public_run``.  It gives
    tests and renderer controls a complete, conspicuously constructed evidence
    shape without laundering it into a reportable native-evidence result.
    """

    survival_states = _metric_states(survival_document["metrics"], SURVIVAL_METRICS, "constructed survival")
    format_states = _metric_states(profile["metrics"], FORMAT_METRICS, "constructed format")
    result: dict[str, PublicMetricEvidence] = {}
    for metric_id in PUBLIC_METRICS:
        state = survival_states.get(metric_id, format_states.get(metric_id))
        assert state is not None
        result[metric_id] = PublicMetricEvidence(
            metric_id, state, (f"constructed-control-observer:{metric_id}",),
            (PublicEvidenceLocator(
                f"constructed-control-artifact:{metric_id}",
                "0" * 64,
                f"constructed-control:{metric_id}",
            ),),
        )
    return result


def validate_format_evidence(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a reportable format profile and its evidence binding."""
    if not isinstance(document, Mapping):
        raise ValueError("format evidence must be an object")
    expected = {"schema_version", "run_id", "configuration_id", "repetition", "build", "collected_on", "result_id", "observer", "native_manifest", "profile", "metric_evidence"}
    _exact_keys(document, expected, "format evidence")
    if document["schema_version"] != FORMAT_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported format evidence schema version")
    profile = validate_format_profile(document["profile"])
    run_id = _identity(document["run_id"], "format evidence run_id")
    configuration_id = _identity(document["configuration_id"], "format evidence configuration_id")
    build = _identity(document["build"], "format evidence build")
    collected_on = document["collected_on"]
    if not isinstance(collected_on, str) or len(collected_on) != 10:
        raise ValueError("format evidence collected_on must be YYYY-MM-DD")
    try:
        date.fromisoformat(collected_on)
    except ValueError as exc:
        raise ValueError("format evidence collected_on must be YYYY-MM-DD") from exc
    result_id = _identity(document["result_id"], "format evidence result_id")
    repetition = document["repetition"]
    if (run_id, configuration_id, repetition) != (profile["run_id"], profile["configuration_id"], profile["repetition"]):
        raise ValueError("format evidence and profile identities must match exactly")
    return {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "build": build, "collected_on": collected_on, "result_id": result_id, "observer": _digest_identifier(document["observer"], "format evidence observer"), "native_manifest": _digest_identifier(document["native_manifest"], "format evidence native_manifest"), "profile": profile, "metric_evidence": _format_metric_evidence(document["metric_evidence"])}


def format_evidence_control(profile: Mapping[str, Any], *, build: str = "constructed-control", collected_on: str = "2026-09-11", result_id: str = "constructed-format-result") -> dict[str, Any]:
    """Make a clearly named constructed-evidence control for tests only."""
    value = validate_format_profile(profile)
    return {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": value["run_id"], "configuration_id": value["configuration_id"], "repetition": value["repetition"], "build": build, "collected_on": collected_on, "result_id": result_id, "observer": {"id": "constructed-format-observer", "sha256": "d" * 64}, "native_manifest": {"id": "constructed-format-manifest", "sha256": "e" * 64}, "profile": dict(profile), "metric_evidence": [{"metric_id": metric_id, "observer_ids": ["constructed-observer-" + str(index)], "native_locators": [{"id": "constructed-native-" + str(index), "sha256": f"{index:064x}"}]} for index, metric_id in enumerate(FORMAT_METRICS, 1)]}


def survival_evidence_control(
    measurement: Mapping[str, Any],
    *,
    evaluation_id: str = "constructed-survival-evaluation",
    identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a clearly named constructed survival-evidence control for tests only."""
    from .survival_metrics import validate_input
    value = validate_input(measurement)
    document = {
        "schema_version": (
            PROSPECTIVE_EVIDENCE_SCHEMA_VERSION
            if value["configuration_id"] in PROSPECTIVE_CONFIGURATION_IDS
            else "session-bench-survival-evidence-v1"
        ),
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": value["run_id"],
        "capture_id": "constructed-capture-" + value["run_id"],
        "evaluation_id": evaluation_id,
        "configuration_id": value["configuration_id"],
        "repetition": value["repetition"],
        "measurement": value,
        "observer": {"id": "constructed-survival-observer", "sha256": "a" * 64},
        "native_manifest": {"id": "constructed-survival-manifest", "sha256": "b" * 64},
        "decoder": {"id": "constructed-survival-decoder", "sha256": "c" * 64},
        "metric_evidence": [
            {
                "metric_id": metric_id,
                "observer_ids": ["constructed-survival-observer-" + str(index)],
                "native_locators": [
                    {
                        "artifact_id": "constructed-survival-native-" + str(index),
                        "artifact_sha256": f"{index:064x}",
                        "record_location": "record:" + str(index),
                    }
                ],
            }
            for index, metric_id in enumerate(SURVIVAL_METRICS, 1)
        ],
    }
    if identity is not None:
        document["identity"] = dict(identity)
    return document


def _public_run_identity(
    survival: Mapping[str, Any], profile: Mapping[str, Any]
) -> PublicRunIdentity:
    """Join the optional identity envelope to validated run/profile IDs."""

    raw = survival.get("identity")
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):  # defensive; the evidence wrapper validates this
        raise ValueError("survival evidence identity must be an object or null")
    identity_build = raw.get("build")
    if identity_build is not None and identity_build != profile["build"]:
        raise ValueError("survival evidence identity build does not match format evidence build")
    return PublicRunIdentity(
        provider=raw.get("provider"),
        harness=raw.get("harness"),
        surface=raw.get("surface"),
        execution_mode=raw.get("execution_mode"),
        os_name=raw.get("os"),
        build=profile["build"],
        model=raw.get("model"),
        configuration=raw.get("configuration"),
        protocol_version=survival["protocol_version"],
        workload_version=survival["workload_version"],
        observer_schema_version=raw.get("observer_schema_version"),
        rubric_version=survival["rubric_version"],
        configuration_id=survival["configuration_id"],
        run_id=survival["run_id"],
        capture_id=survival["capture_id"],
        evaluation_id=survival["evaluation_id"],
        repetition=survival["repetition"],
        collected_on=profile["collected_on"],
        result_id=profile["result_id"],
    )


def _public_timeline(metric_evidence: Mapping[str, PublicMetricEvidence]) -> tuple[PublicTimelineEvent, ...]:
    """Expose only the observer/native evidence rows for the 19 deep metrics."""

    return tuple(
        PublicTimelineEvent(
            step=index,
            metric_id=metric_id,
            state=metric_evidence[metric_id].state,
            observer_ids=metric_evidence[metric_id].observer_ids,
            native_locators=metric_evidence[metric_id].native_locators,
        )
        for index, metric_id in enumerate(SURVIVAL_METRICS, 1)
    )


def _assemble_public_run(
    survival: RunScore,
    profile: Mapping[str, Any],
    *,
    metric_evidence: Mapping[str, PublicMetricEvidence],
    build: str | None,
    collected_on: str | None,
    result_id: str | None,
    survival_evaluation_id: str | None,
    identity: PublicRunIdentity | None,
) -> PublicRunScore:
    if (survival.run_id, survival.configuration_id, survival.repetition) != (profile["run_id"], profile["configuration_id"], profile["repetition"]):
        raise ValueError("survival input and format profile identities must match exactly")
    metric_values: dict[str, Fraction | None] = dict(survival.metrics)
    profile_by_id = {row["id"]: row for row in profile["metrics"]}
    blockers = list(survival.blockers)
    for metric_id in FORMAT_METRICS:
        row = profile_by_id[metric_id]
        metric_values[metric_id] = _fraction(row)
        if row["state"] in BLOCKING_STATES:
            blockers.append(f"{metric_id}:{row['state']}")
    category_rows: dict[str, list[tuple[PublicMetricSpec, Fraction | None]]] = {category: [] for category in PUBLIC_CATEGORY_POINTS}
    for metric_id, spec in PUBLIC_METRICS.items():
        category_rows[spec.category].append((spec, metric_values[metric_id]))
    categories: dict[str, CategoryScore] = {}
    for category, rows in category_rows.items():
        known_points = sum((spec.points * value for spec, value in rows if value is not None), Fraction(0))
        known_possible = sum((spec.points for spec, value in rows if value is not None), Fraction(0))
        resolved = sum(value is not None for _spec, value in rows)
        maximum = PUBLIC_CATEGORY_POINTS[category]
        categories[category] = CategoryScore(category, known_points, known_possible, known_points, known_points + maximum - known_possible, resolved, len(rows), known_points if resolved == len(rows) else None)
    complete = all(category.complete for category in categories.values())
    # Portability remains visible and is required by its specific use-case,
    # but v0.4-style ranking does not turn a resolved loss into missing data.
    portable_gate = survival.portable_gate
    rankable = complete
    overall = sum((category.score for category in categories.values() if category.score is not None), Fraction(0)) if rankable else None
    if set(metric_evidence) != set(PUBLIC_METRICS):
        raise ValueError("public run metric evidence must cover all 31 public metrics")
    if any(row.metric_id != metric_id for metric_id, row in metric_evidence.items()):
        raise ValueError("public run metric evidence identity does not match its metric key")
    return PublicRunScore(
        run_id=survival.run_id,
        configuration_id=survival.configuration_id,
        repetition=survival.repetition,
        metrics=metric_values,
        categories=categories,
        overall=overall,
        portable_gate=portable_gate,
        rankable=rankable,
        blockers=tuple(blockers),
        survival_run=survival,
        metric_evidence=dict(metric_evidence),
        build=build,
        collected_on=collected_on,
        result_id=result_id,
        survival_evaluation_id=survival_evaluation_id,
        identity=identity,
        timeline=_public_timeline(metric_evidence),
    )


def score_public_run(survival_evidence: Mapping[str, Any], format_evidence: Mapping[str, Any]) -> PublicRunScore:
    """Score one reportable run; both inputs must bind observations to evidence."""
    if survival_evidence.get("schema_version") == PROSPECTIVE_EVIDENCE_SCHEMA_VERSION:
        survival = validate_prospective_evidence_input(survival_evidence)
    else:
        survival = validate_evidence_input(survival_evidence)
    if survival["configuration_id"] not in TARGET_CONFIGURATIONS:
        raise ValueError(
            "survival evidence configuration is outside the v1 release cohort: "
            + survival["configuration_id"]
        )
    profile = validate_format_evidence(format_evidence)
    if (survival["run_id"], survival["configuration_id"], survival["repetition"]) != (profile["run_id"], profile["configuration_id"], profile["repetition"]):
        raise ValueError("survival evidence and format evidence identities must match exactly")
    from .survival_metrics import score_run
    metric_evidence = _reportable_metric_evidence(survival, profile)
    return _assemble_public_run(
        score_run(survival["measurement"]), profile["profile"],
        metric_evidence=metric_evidence, build=profile["build"],
        collected_on=profile["collected_on"], result_id=profile["result_id"],
        survival_evaluation_id=survival["evaluation_id"],
        identity=_public_run_identity(survival, profile),
    )


def score_public_control_run(survival_document: Mapping[str, Any], format_profile: Mapping[str, Any]) -> PublicRunScore:
    """Score raw constructed controls only; never use this in a public report."""
    from .survival_metrics import score_run, validate_input
    measurement = validate_input(survival_document)
    profile = validate_format_profile(format_profile)
    return _assemble_public_run(
        score_run(measurement), profile,
        metric_evidence=_constructed_metric_evidence(measurement, profile),
        build=None, collected_on=None, result_id=None, survival_evaluation_id=None,
        identity=None,
    )


def weighted_public_total(category_fractions: Mapping[str, Fraction], vector: str = "baseline") -> Fraction:
    if vector not in PUBLIC_WEIGHT_VECTORS:
        raise ValueError(f"unknown public weight vector: {vector}")
    if set(category_fractions) != set(PUBLIC_CATEGORY_POINTS):
        raise ValueError("category fractions must contain the exact five public categories")
    if any(not isinstance(value, Fraction) or value < 0 or value > 1 for value in category_fractions.values()):
        raise ValueError("category fractions must be Fractions from zero through one")
    return sum((category_fractions[category] * weight for category, weight in PUBLIC_WEIGHT_VECTORS[vector].items()), Fraction(0))


def _configuration_result_ids(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{label} must contain exact three public result IDs")
    normalized = tuple(sorted(_identity(item, label) for item in value))
    if len(set(normalized)) != 3:
        raise ValueError(f"{label} must contain three unique public result IDs")
    return normalized


def validate_configuration_evidence(
    document: Mapping[str, Any], runs: Sequence[PublicRunScore]
) -> ConfigurationEvidenceBinding:
    """Bind a fully reproduced configuration to immutable bundle/receipt IDs."""
    if not isinstance(document, Mapping):
        raise ValueError("configuration evidence must be an object")
    _exact_keys(document, {"schema_version", "configuration_id", "bundle", "reproduction_receipt"}, "configuration evidence")
    if document["schema_version"] != CONFIGURATION_EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported configuration evidence schema version")
    configuration_id = _identity(document["configuration_id"], "configuration evidence configuration_id")
    if {run.configuration_id for run in runs} != {configuration_id}:
        raise ValueError("configuration evidence must bind the scored configuration")
    bundle = document["bundle"]
    receipt = document["reproduction_receipt"]
    if not isinstance(bundle, Mapping) or set(bundle) != {"id", "sha256", "immutable", "public", "result_ids"}:
        raise ValueError("configuration bundle has wrong fields")
    bundle_id = _identity(bundle["id"], "configuration bundle id")
    bundle_sha = _digest_identifier({"id": bundle_id, "sha256": bundle["sha256"]}, "configuration bundle")["sha256"]
    expected_ids = tuple(sorted(_identity(run.result_id, "public result ID") for run in runs))
    bundle_result_ids = _configuration_result_ids(bundle.get("result_ids"), "configuration bundle result_ids")
    if len(expected_ids) != 3 or bundle["immutable"] is not True or bundle["public"] is not True or bundle_result_ids != expected_ids:
        raise ValueError("configuration bundle must immutably bind the exact three public result IDs")
    if not isinstance(receipt, Mapping) or set(receipt) != {"id", "sha256", "bundle_sha256", "offline_recomputed", "verified", "result_ids"}:
        raise ValueError("reproduction receipt has wrong fields")
    receipt_id = _identity(receipt["id"], "reproduction receipt id")
    receipt_sha = _digest_identifier({"id": receipt_id, "sha256": receipt["sha256"]}, "reproduction receipt")["sha256"]
    receipt_bundle_sha = _digest_identifier({"id": "receipt bundle", "sha256": receipt["bundle_sha256"]}, "reproduction receipt bundle")["sha256"]
    receipt_result_ids = _configuration_result_ids(receipt.get("result_ids"), "reproduction receipt result_ids")
    if receipt_bundle_sha != bundle_sha or receipt["offline_recomputed"] is not True or receipt["verified"] is not True or receipt_result_ids != expected_ids:
        raise ValueError("reproduction receipt must deterministically recompute the exact immutable bundle")
    return ConfigurationEvidenceBinding(
        configuration_id,
        ImmutablePublicBundle(bundle_id, bundle_sha, True, True, bundle_result_ids),
        ReproductionReceipt(receipt_id, receipt_sha, receipt_bundle_sha, True, True, receipt_result_ids),
    )


PUBLIC_IDENTITY_REQUIRED_FIELDS = (
    "provider", "harness", "surface", "execution_mode", "os_name", "build",
    "model", "configuration", "observer_schema_version", "collected_on", "result_id",
)
PUBLIC_IDENTITY_STABLE_FIELDS = (
    "provider", "harness", "surface", "execution_mode", "os_name", "build",
    "model", "configuration", "observer_schema_version",
)
PUBLIC_IDENTITY_UNAVAILABLE_VALUES = frozenset({
    "model-unreported", "unreported", "unknown", "n/a", "na", "not reported",
})


def public_identity_value_bound(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() not in PUBLIC_IDENTITY_UNAVAILABLE_VALUES
    )


def public_identity_blockers(runs: Sequence[PublicRunScore]) -> tuple[str, ...]:
    """Fail closed on missing or mixed public configuration identity.

    Older evidence may omit the optional envelope and still expose measured
    cells. It cannot acquire a rank until the identity is bound in every run.
    """
    blockers: list[str] = []
    for run in runs:
        identity = run.identity
        if identity is None:
            blockers.append(f"repetition {run.repetition}: public identity envelope missing")
            continue
        for field in PUBLIC_IDENTITY_REQUIRED_FIELDS:
            value = getattr(identity, field)
            if not public_identity_value_bound(value):
                blockers.append(f"repetition {run.repetition}: public identity {field} missing")
        expected_surface = "desktop" if run.configuration_id.endswith("-desktop") else "cli"
        if identity.surface is not None and identity.surface != expected_surface:
            blockers.append(f"repetition {run.repetition}: public identity surface mismatch")
        for field, expected in (
            ("configuration_id", run.configuration_id),
            ("run_id", run.run_id),
            ("repetition", run.repetition),
            ("build", run.build),
            ("collected_on", run.collected_on),
            ("result_id", run.result_id),
            ("evaluation_id", run.survival_evaluation_id),
        ):
            if getattr(identity, field) != expected:
                blockers.append(f"repetition {run.repetition}: public identity {field} mismatch")
    identities = [run.identity for run in runs if run.identity is not None]
    if len(identities) == len(runs) and identities:
        for field in PUBLIC_IDENTITY_STABLE_FIELDS:
            if len({getattr(identity, field) for identity in identities}) != 1:
                blockers.append(f"public identity {field} differs across repetitions")
    return tuple(blockers)


def aggregate_public_configuration(
    runs: Sequence[PublicRunScore], *, configuration_evidence: Mapping[str, Any] | None = None
) -> PublicConfigurationScore:
    if len(runs) != 3:
        raise ValueError("a public configuration requires exactly three evaluated repetitions")
    ids = {run.configuration_id for run in runs}
    if len(ids) != 1 or {run.repetition for run in runs} != {1, 2, 3} or len({run.run_id for run in runs}) != 3:
        raise ValueError("public repetitions must have one configuration, unique run IDs, and repetitions 1, 2, 3")
    ordered = tuple(sorted(runs, key=lambda run: run.repetition))
    categories: dict[str, Fraction | None] = {}
    ranges: dict[str, tuple[Fraction, Fraction] | None] = {}
    for category in PUBLIC_CATEGORY_POINTS:
        values = [run.categories[category].score for run in ordered]
        categories[category] = None if any(value is None for value in values) else sum(values, Fraction(0)) / 3  # type: ignore[arg-type]
        ranges[category] = None if any(value is None for value in values) else (min(values), max(values))  # type: ignore[arg-type]
    portable_gate = all(run.portable_gate for run in ordered)
    complete = all(run.rankable for run in ordered)
    evidence = validate_configuration_evidence(configuration_evidence, ordered) if configuration_evidence is not None else None
    identity_blockers = public_identity_blockers(ordered)
    verification = "fully_reproduced" if complete and evidence is not None and not identity_blockers else ("partially_verified" if any(value is not None for run in ordered for value in run.metrics.values()) else "unranked")
    rankable = verification == "fully_reproduced"
    blockers = tuple(f"repetition {run.repetition}: {blocker}" for run in ordered for blocker in run.blockers) + identity_blockers
    if not rankable:
        return PublicConfigurationScore(
            next(iter(ids)), ordered, categories, ranges, None, None, None,
            portable_gate, False, blockers, verification,
            evidence.bundle if evidence else None,
            evidence.reproduction_receipt if evidence else None,
            tuple(run.identity for run in ordered),
            tuple((run.repetition, run.timeline) for run in ordered),
        )
    totals = [run.overall for run in ordered]
    assert all(value is not None for value in totals)
    fractions = {category: categories[category] / PUBLIC_CATEGORY_POINTS[category] for category in PUBLIC_CATEGORY_POINTS}  # type: ignore[operator]
    assert evidence is not None
    return PublicConfigurationScore(
        next(iter(ids)), ordered, categories, ranges,
        sum(totals, Fraction(0)) / 3, (min(totals), max(totals)),
        {vector: weighted_public_total(fractions, vector) for vector in PUBLIC_WEIGHT_VECTORS},
        portable_gate, True, blockers, verification,
        evidence.bundle, evidence.reproduction_receipt,
        tuple(run.identity for run in ordered),
        tuple((run.repetition, run.timeline) for run in ordered),
    )


def score_public_configuration(pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]],], *, configuration_evidence: Mapping[str, Any] | None = None) -> PublicConfigurationScore:
    """Convenience entry point for the three (survival, profile) repetitions."""
    return aggregate_public_configuration([score_public_run(survival, profile) for survival, profile in pairs], configuration_evidence=configuration_evidence)


def qualified_public_cohort(configurations: Iterable[PublicConfigurationScore]) -> tuple[PublicConfigurationScore, ...] | None:
    """Return rankable rows only after the complete five-surface cohort qualifies.

    v1 is a fixed release cohort, so an incomplete surface is visible and
    unranked but cannot leave a partial public leaderboard behind. CLI/Desktop
    pairing remains a separate use-case recommendation gate.
    """
    values = attempted_public_cohort(configurations)
    if values is None:
        return None
    qualified = tuple(value for value in values if value.rankable)
    if len(qualified) != len(TARGET_CONFIGURATIONS):
        return None
    return tuple(sorted(qualified, key=lambda value: value.configuration_id))


def attempted_public_cohort(configurations: Iterable[PublicConfigurationScore]) -> tuple[PublicConfigurationScore, ...] | None:
    """Validate the fixed five-surface attempted cohort without hiding blocked rows."""
    values = tuple(configurations)
    ids = [value.configuration_id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("public leaderboard configuration IDs must be unique")
    unknown = set(ids) - set(TARGET_CONFIGURATIONS)
    if unknown:
        raise ValueError(f"unknown public configuration IDs: {sorted(unknown)}")
    if set(ids) != set(TARGET_CONFIGURATIONS) or len(values) != len(TARGET_CONFIGURATIONS):
        return None
    return tuple(sorted(values, key=lambda value: value.configuration_id))


def public_leaderboard_ranks(configurations: Iterable[PublicConfigurationScore], vector: str = "baseline") -> dict[str, int]:
    cohort = qualified_public_cohort(configurations)
    if cohort is None:
        return {}
    if vector not in PUBLIC_WEIGHT_VECTORS:
        raise ValueError(f"unknown public weight vector: {vector}")
    # The public card prints one decimal. Rank the same published value so two
    # rows displayed as 87.0 cannot misleadingly receive different ranks.
    scored = sorted(
        (
            (value.configuration_id, Fraction(display_decimal(value.sensitivity[vector])))
            for value in cohort
            if value.sensitivity is not None
        ),
        key=lambda item: (-item[1], item[0]),
    )
    ranks: dict[str, int] = {}
    prior: Fraction | None = None
    rank = 0
    for position, (configuration_id, value) in enumerate(scored, 1):
        if value != prior:
            rank, prior = position, value
        ranks[configuration_id] = rank
    return ranks


def public_sensitivity_winners(configurations: Iterable[PublicConfigurationScore]) -> tuple[dict[str, tuple[str, ...]], bool]:
    cohort = qualified_public_cohort(configurations)
    if cohort is None:
        return ({vector: () for vector in PUBLIC_WEIGHT_VECTORS}, False)
    winners: dict[str, tuple[str, ...]] = {}
    for vector in PUBLIC_WEIGHT_VECTORS:
        values = {
            item.configuration_id: item.sensitivity[vector]
            for item in cohort
            if item.sensitivity is not None
        }
        best = max(values.values())
        winners[vector] = tuple(sorted(key for key, value in values.items() if value == best))
    return winners, len(set(winners.values())) == 1
