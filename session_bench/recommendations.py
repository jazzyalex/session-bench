"""Versioned, deterministic v1 use-case recommendations and vendor repairs.

Positive recommendations are derived only from the frozen public cohort.  The
module emits all six use cases on every valid call, including explicit
``no_recommendation`` rows.  Evidence locators and reproduction receipts are
inputs to the recommendation layer; missing evidence can never be repaired by
prose in the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
import json
from typing import Any, Iterable, Mapping, Sequence

from .survival_metrics import ALLOWED_STATES
from .v1_public_score import (
    PUBLIC_CATEGORY_POINTS,
    PUBLIC_METRICS,
    PublicEvidenceLocator,
    PublicConfigurationScore,
    PublicMetricEvidence,
    attempted_public_cohort,
)


RECOMMENDATION_SCHEMA_VERSION = "session-bench-v1-recommendations-1"
RECOMMENDATION_STATUSES = frozenset({"recommended", "qualified_set", "no_recommendation"})
USE_CASES = (
    "audit_ready",
    "portable_archives",
    "usage_accounting",
    "cli_desktop_consistency",
    "lean_complete_record",
    "long_term_archives",
)


@dataclass(frozen=True)
class ResultCitation:
    """Exact scope and immutable identities needed to quote one configuration."""

    configuration_id: str
    surface_id: str
    builds: tuple[str, ...]
    collected_on: tuple[str, ...]
    result_ids: tuple[str, ...]
    evaluation_ids: tuple[str, ...]
    reproduction_receipt_id: str | None

    @property
    def bound_result_ids(self) -> tuple[str, ...]:
        return tuple(f"{evaluation_id}:{result_id}" for evaluation_id, result_id in zip(self.evaluation_ids, self.result_ids, strict=True))

    def display(self) -> dict[str, object]:
        return {
            "configuration_id": self.configuration_id,
            "surface_id": self.surface_id,
            "builds": list(self.builds),
            "collected_on": list(self.collected_on),
            "result_ids": list(self.result_ids),
            "evaluation_ids": list(self.evaluation_ids),
            "bound_result_ids": list(self.bound_result_ids),
            "reproduction_receipt_id": self.reproduction_receipt_id,
        }


@dataclass(frozen=True)
class MetricEvidence:
    observer_ids: tuple[str, ...]
    native_locators: tuple[str, ...]
    evidence_states: tuple[str, ...]


@dataclass(frozen=True)
class UseCaseQualification:
    """One versioned recommendation record, including explicit negative output."""

    use_case: str
    status: str
    configuration_ids: tuple[str, ...]
    rule_version: str
    thresholds: Mapping[str, str]
    objective_name: str
    objective_values: Mapping[str, str]
    cohort_result_ids: tuple[str, ...]
    metric_ids: tuple[str, ...]
    citations: tuple[ResultCitation, ...]
    observer_ids: tuple[str, ...]
    native_locators: tuple[str, ...]
    reason_ids: tuple[str, ...]

    def display(self) -> dict[str, object]:
        if self.status not in RECOMMENDATION_STATUSES:
            raise ValueError(f"unsupported recommendation status: {self.status}")
        return {
            "use_case": self.use_case,
            "status": self.status,
            "configuration_ids": list(self.configuration_ids),
            "rule_version": self.rule_version,
            "thresholds": dict(self.thresholds),
            "objective_name": self.objective_name,
            "objective_values": dict(self.objective_values),
            "cohort_result_ids": list(self.cohort_result_ids),
            "metric_ids": list(self.metric_ids),
            "citations": [citation.display() for citation in self.citations],
            "observer_ids": list(self.observer_ids),
            "native_locators": list(self.native_locators),
            "reason_ids": list(self.reason_ids),
        }


@dataclass(frozen=True)
class ImprovementItem:
    metric_id: str
    category: str
    state: str  # failed or blocked
    repetitions: tuple[int, ...]
    observed_consequence: str
    evidence_states: tuple[str, ...]
    observer_ids: tuple[str, ...]
    native_locators: tuple[str, ...]
    acceptance_condition: str

    def display(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id,
            "category": self.category,
            "state": self.state,
            "repetitions": list(self.repetitions),
            "observed_consequence": self.observed_consequence,
            "evidence_states": list(self.evidence_states),
            "observer_ids": list(self.observer_ids),
            "native_locators": list(self.native_locators),
            "acceptance_condition": self.acceptance_condition,
        }


@dataclass(frozen=True)
class ImprovementList:
    configuration_id: str
    citation: ResultCitation
    items: tuple[ImprovementItem, ...]
    reason_ids: tuple[str, ...] = ()

    def display(self) -> dict[str, object]:
        return {
            "configuration_id": self.configuration_id,
            "citation": self.citation.display(),
            "items": [item.display() for item in self.items],
            "reason_ids": list(self.reason_ids),
        }


@dataclass(frozen=True)
class RecommendationOutputs:
    recommendations: tuple[UseCaseQualification, ...]
    improvements: tuple[ImprovementList, ...]
    schema_version: str = RECOMMENDATION_SCHEMA_VERSION

    @property
    def qualifications(self) -> tuple[UseCaseQualification, ...]:
        """Compatibility alias for the earlier prototype API."""

        return self.recommendations

    def display(self) -> dict[str, object]:
        if tuple(item.use_case for item in self.recommendations) != USE_CASES:
            raise ValueError("recommendation output must contain all six use cases in frozen order")
        return {
            "schema_version": self.schema_version,
            "recommendations": [item.display() for item in self.recommendations],
            "improvements": [item.display() for item in self.improvements],
        }


ACCEPTANCE_CONDITIONS: dict[str, str] = {
    "work.submitted_turns": "Retain each accepted user turn exactly, with role, stable identity, and order.",
    "work.visible_responses": "Retain each completed visible response with its response canary and source turn.",
    "work.actions": "Retain every observed primary action with ordered arguments, target, identity, and turn.",
    "work.results": "Retain every observed primary result with output/status, exit state, identity, and action join.",
    "work.changed_files": "Retain the changed relative path and its before/after content hashes.",
    "revision.r1": "Retain the exact original R1 requirement as a distinct historical turn.",
    "revision.r2": "Retain the exact R2 correction as a distinct historical turn.",
    "revision.r1_r2_order": "Retain R1 before R2 and the exact R2 text that declares its superseding scope.",
    "revision.final_after_r2": "Place the final edit, result, and response downstream of R2.",
    "causal.action_result": "Persist stable, directed action-to-result keys for every observed pair.",
    "causal.turn_response": "Persist stable, directed turn-to-response keys with the observed turn order.",
    "attribution.model_config": "Bind every visible response to the exact recorded model and configuration.",
    "attribution.usage": "Bind every visible response to its own native usage record by stable identity.",
    "attribution.token_semantics": "Name input, output, and cache semantics and distinguish zero, null, missing, estimated, and billed.",
    "attribution.reconciliation": "Declare arithmetic under which per-response usage reconciles to the session total.",
    "portable.complete_root": "Document and capture the complete native session root with a hash-bound inventory.",
    "portable.companions": "Include and bind every required sidecar, journal, and companion file.",
    "portable.isolated_decode": "Decode the copied native bundle without original roots, vendor executable, account, or network.",
    "portable.canonical_equality": "Produce the same canonical reconstruction from ordinary and isolated copied-root decoding.",
    "broad.readable_rationale": "Expose each retained response rationale or summary as ordered reader-visible text.",
    "broad.thread_structure": "Expose deterministic session, turn, role, order, and parent/child boundaries.",
    "broad.standard_tools_readable": "Make the native record readable with a documented commodity JSON, JSONL, SQLite, or text parser.",
    "broad.documented_format": "Document containers, record types, identities, joins, and version semantics needed for reconstruction.",
    "broad.self_contained_identity": "Store session, harness, surface, and record-family identity inside the copied bundle.",
    "broad.declared_format_version": "Bind a machine-readable format/schema version to the native bundle.",
    "broad.event_timestamps": "Store parseable timestamps with declared units/time zone on every required event.",
    "broad.honest_version_signal": "Change or map the declared version whenever schema or field semantics become incompatible.",
    "broad.observed_schema_stability": "Keep records decodable under the advertised contract across the declared build/date window.",
    "broad.stable_root_location": "Document or deterministically expose the complete root across all repetitions.",
    "broad.naive_reader_duplicate_safety": "Yield each required event once or expose exact supersession/tombstone keys for deduplication.",
    "broad.classified_content_density": "Classify useful and unknown logical bytes under non-overlapping rules with unknown bytes in the denominator.",
}


def _fraction_text(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _clean_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _receipt_id(
    configuration: PublicConfigurationScore,
    supplied: Mapping[str, str] | None,
    run_result_ids: tuple[str, ...],
) -> str | None:
    if supplied is not None:
        value = supplied.get(configuration.configuration_id)
        return None if value is None else _clean_id(value, "reproduction receipt")
    bundle = configuration.bundle
    receipt = configuration.reproduction_receipt
    if bundle is None or receipt is None:
        return None
    expected = tuple(sorted(run_result_ids))
    if len(expected) != 3 or len(set(expected)) != 3:
        raise ValueError(f"{configuration.configuration_id} must cite three unique run result IDs")
    if bundle.result_ids != expected or receipt.result_ids != expected:
        raise ValueError(f"{configuration.configuration_id} bundle/receipt result IDs do not match its exact runs")
    if not bundle.immutable or not bundle.public:
        raise ValueError(f"{configuration.configuration_id} bundle is not immutable and public")
    if not receipt.offline_recomputed or not receipt.verified or receipt.bundle_sha256 != bundle.sha256:
        raise ValueError(f"{configuration.configuration_id} reproduction receipt does not verify its bundle")
    return _clean_id(receipt.id, "reproduction receipt")


def _citation(
    configuration: PublicConfigurationScore,
    reproduction_receipts: Mapping[str, str] | None,
) -> ResultCitation:
    builds = {run.build for run in configuration.runs}
    dates = {run.collected_on for run in configuration.runs}
    result_ids = tuple(run.result_id for run in configuration.runs)
    evaluation_ids = tuple(run.survival_evaluation_id for run in configuration.runs)
    if None in builds or None in dates:
        raise ValueError(f"{configuration.configuration_id} lacks bound build/date citations")
    if any(value is None for value in result_ids + evaluation_ids):
        raise ValueError(f"{configuration.configuration_id} lacks three bound result/evaluation IDs")
    if len(set(zip(evaluation_ids, result_ids, strict=True))) != 3:
        raise ValueError(f"{configuration.configuration_id} lacks three unique bound results")
    surface_id = getattr(configuration, "surface_id", configuration.configuration_id)
    return ResultCitation(
        configuration_id=configuration.configuration_id,
        surface_id=_clean_id(surface_id, "surface_id"),
        builds=tuple(sorted(builds)),  # type: ignore[arg-type]
        collected_on=tuple(sorted(dates)),  # type: ignore[arg-type]
        result_ids=tuple(result_ids),  # type: ignore[arg-type]
        evaluation_ids=tuple(evaluation_ids),  # type: ignore[arg-type]
        reproduction_receipt_id=_receipt_id(configuration, reproduction_receipts, tuple(result_ids)),  # type: ignore[arg-type]
    )


def _locator_text(value: Any) -> str:
    if isinstance(value, str):
        return _clean_id(value, "native locator")
    if isinstance(value, PublicEvidenceLocator):
        return json.dumps(value.display(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raise ValueError("native locator must be a string, object, or PublicEvidenceLocator")


def _normalise_metric_evidence(value: Any, label: str) -> MetricEvidence:
    if isinstance(value, PublicMetricEvidence):
        observers = value.observer_ids
        locators = value.native_locators
        states = (value.state,)
    elif isinstance(value, Mapping):
        observers = value.get("observer_ids", ())
        locators = value.get("native_locators", ())
        states = value.get("evidence_states", ())
    else:
        raise ValueError(f"{label} must be an object")
    if not isinstance(observers, (list, tuple)) or not isinstance(locators, (list, tuple)) or not isinstance(states, (list, tuple)):
        raise ValueError(f"{label} evidence fields must be arrays")
    observer_ids = tuple(sorted({_clean_id(item, f"{label} observer ID") for item in observers}))
    native_locators = tuple(sorted({_locator_text(item) for item in locators}))
    evidence_states = tuple(_clean_id(item, f"{label} evidence state") for item in states)
    if any(state not in ALLOWED_STATES for state in evidence_states):
        raise ValueError(f"{label} has unsupported evidence state")
    return MetricEvidence(observer_ids, native_locators, evidence_states)


def _configuration_evidence(
    configuration: PublicConfigurationScore,
    supplied: Mapping[str, Mapping[str, Mapping[str, Any]]] | None,
) -> dict[str, MetricEvidence]:
    if supplied is not None:
        raw = supplied.get(configuration.configuration_id, {})
        if not isinstance(raw, Mapping):
            raise ValueError(f"metric evidence for {configuration.configuration_id} must be an object")
        result: dict[str, MetricEvidence] = {}
        for metric_id, value in raw.items():
            if metric_id not in PUBLIC_METRICS:
                raise ValueError(f"unknown metric evidence ID: {metric_id}")
            result[metric_id] = _normalise_metric_evidence(value, f"{configuration.configuration_id}:{metric_id}")
        return result

    merged: dict[str, dict[str, list[Any]]] = {}
    for run in configuration.runs:
        run_rows = run.metric_evidence
        if not isinstance(run_rows, Mapping):
            continue
        for metric_id, value in run_rows.items():
            if metric_id not in PUBLIC_METRICS:
                raise ValueError(f"unknown typed metric evidence ID: {metric_id}")
            if not isinstance(value, PublicMetricEvidence):
                continue
            if value.metric_id != metric_id:
                raise ValueError(f"{configuration.configuration_id}:{metric_id} evidence identity mismatch")
            normalized = _normalise_metric_evidence(value, f"{configuration.configuration_id}:{metric_id}")
            target = merged.setdefault(metric_id, {"observer_ids": [], "native_locators": [], "evidence_states": []})
            target["observer_ids"].extend(normalized.observer_ids)
            target["native_locators"].extend(normalized.native_locators)
            target["evidence_states"].extend(normalized.evidence_states)
    return {
        metric_id: _normalise_metric_evidence(value, f"{configuration.configuration_id}:{metric_id}")
        for metric_id, value in merged.items()
    }


def _evidence_for_metrics(
    evidence: Mapping[str, MetricEvidence], metric_ids: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    rows = [evidence.get(metric_id) for metric_id in metric_ids]
    if any(
        row is None or not row.observer_ids or not row.native_locators or len(row.evidence_states) != 3
        for row in rows
    ):
        return None
    return (
        tuple(sorted({item for row in rows if row is not None for item in row.observer_ids})),
        tuple(sorted({item for row in rows if row is not None for item in row.native_locators})),
    )


def _category_metrics(*categories: str) -> tuple[str, ...]:
    wanted = set(categories)
    return tuple(metric_id for metric_id, spec in PUBLIC_METRICS.items() if spec.category in wanted)


def _at_least(configuration: PublicConfigurationScore, category: str, threshold: Fraction) -> bool:
    value = configuration.categories[category]
    return value is not None and value >= PUBLIC_CATEGORY_POINTS[category] * threshold


def _metric_perfect(configuration: PublicConfigurationScore, metric_ids: Sequence[str]) -> bool:
    return all(run.metrics[metric_id] == 1 for run in configuration.runs for metric_id in metric_ids)


def _long_term_observation(configuration: PublicConfigurationScore) -> bool:
    builds = {run.build for run in configuration.runs}
    raw_dates = {run.collected_on for run in configuration.runs}
    if None in builds or None in raw_dates or len(builds) < 2:
        return False
    try:
        dates = [date.fromisoformat(value) for value in raw_dates]  # type: ignore[arg-type]
    except ValueError:
        return False
    return len(dates) >= 2 and (max(dates) - min(dates)).days >= 30


def _cohort_result_ids(citations: Mapping[str, ResultCitation], cohort: Sequence[PublicConfigurationScore]) -> tuple[str, ...]:
    return tuple(sorted(result_id for item in cohort for result_id in citations[item.configuration_id].result_ids))


def _eligible(
    configuration: PublicConfigurationScore,
    citation: ResultCitation,
    evidence: Mapping[str, MetricEvidence],
    metric_ids: Sequence[str],
) -> bool:
    return configuration.rankable and citation.reproduction_receipt_id is not None and _evidence_for_metrics(evidence, metric_ids) is not None


def _selected_evidence(
    selected: Sequence[str], evidence_by_configuration: Mapping[str, Mapping[str, MetricEvidence]], metric_ids: Sequence[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    observers: set[str] = set()
    locators: set[str] = set()
    for configuration_id in selected:
        resolved = _evidence_for_metrics(evidence_by_configuration[configuration_id], metric_ids)
        if resolved is None:
            continue
        observers.update(resolved[0])
        locators.update(resolved[1])
    return tuple(sorted(observers)), tuple(sorted(locators))


def _recommendation(
    *,
    use_case: str,
    selected: Sequence[str],
    status: str,
    thresholds: Mapping[str, str],
    objective_name: str,
    objective_values: Mapping[str, Fraction],
    metric_ids: Sequence[str],
    cohort_result_ids: Sequence[str],
    citations: Mapping[str, ResultCitation],
    evidence_by_configuration: Mapping[str, Mapping[str, MetricEvidence]],
    reason_ids: Sequence[str],
) -> UseCaseQualification:
    selected_ids = tuple(selected)
    observers, locators = _selected_evidence(selected_ids, evidence_by_configuration, metric_ids)
    return UseCaseQualification(
        use_case=use_case,
        status=status,
        configuration_ids=selected_ids,
        rule_version=RECOMMENDATION_SCHEMA_VERSION,
        thresholds=dict(thresholds),
        objective_name=objective_name,
        objective_values={key: _fraction_text(value) for key, value in sorted(objective_values.items())},
        cohort_result_ids=tuple(cohort_result_ids),
        metric_ids=tuple(metric_ids),
        citations=tuple(citations[item] for item in selected_ids),
        observer_ids=observers,
        native_locators=locators,
        reason_ids=tuple(reason_ids),
    )


def _objective_rule(
    *,
    use_case: str,
    cohort: Sequence[PublicConfigurationScore],
    citations: Mapping[str, ResultCitation],
    evidence_by_configuration: Mapping[str, Mapping[str, MetricEvidence]],
    cohort_result_ids: Sequence[str],
    metric_ids: Sequence[str],
    thresholds: Mapping[str, str],
    objective_name: str,
    predicate,
    objective,
) -> UseCaseQualification:
    all_values = {item.configuration_id: objective(item) for item in cohort}
    candidates = [
        (item, all_values[item.configuration_id])
        for item in cohort
        if _eligible(item, citations[item.configuration_id], evidence_by_configuration[item.configuration_id], metric_ids) and predicate(item)
    ]
    candidates.sort(key=lambda row: (-row[1], row[0].configuration_id))
    if not candidates:
        reasons = ["qualification.no_candidate"]
        if not any(citations[item.configuration_id].reproduction_receipt_id for item in cohort):
            reasons.append("evidence.no_fully_reproduced_candidate")
        if any(_evidence_for_metrics(evidence_by_configuration[item.configuration_id], metric_ids) is None for item in cohort):
            reasons.append("evidence.metric_locators_incomplete")
        return _recommendation(
            use_case=use_case, selected=(), status="no_recommendation", thresholds=thresholds,
            objective_name=objective_name, objective_values=all_values, metric_ids=metric_ids,
            cohort_result_ids=cohort_result_ids, citations=citations,
            evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
        )
    if len(candidates) == 1 or candidates[0][1] - candidates[1][1] >= 5:
        selected = (candidates[0][0].configuration_id,)
        reasons = ("selection.only_qualifier",) if len(candidates) == 1 else ("selection.exclusive_margin_met",)
        status = "recommended"
    else:
        top = candidates[0][1]
        selected = tuple(item.configuration_id for item, value in candidates if top - value < 5)
        reasons = ("selection.margin_below_exclusive_threshold",)
        status = "qualified_set"
    return _recommendation(
        use_case=use_case, selected=selected, status=status, thresholds=thresholds,
        objective_name=objective_name, objective_values=all_values, metric_ids=metric_ids,
        cohort_result_ids=cohort_result_ids, citations=citations,
        evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
    )


def _pair_rule(
    cohort: Sequence[PublicConfigurationScore],
    citations: Mapping[str, ResultCitation],
    evidence_by_configuration: Mapping[str, Mapping[str, MetricEvidence]],
    cohort_result_ids: Sequence[str],
) -> UseCaseQualification:
    metric_ids = tuple(PUBLIC_METRICS)
    by_id = {item.configuration_id: item for item in cohort}
    values: dict[str, Fraction] = {}
    candidates: list[tuple[tuple[str, str], Fraction]] = []
    for left_id, right_id in (("codex-cli", "codex-desktop"), ("claude-cli", "claude-desktop")):
        pair_key = f"{left_id}+{right_id}"
        if left_id not in by_id or right_id not in by_id:
            continue
        left, right = by_id[left_id], by_id[right_id]
        if not all(_eligible(item, citations[item.configuration_id], evidence_by_configuration[item.configuration_id], metric_ids) for item in (left, right)):
            continue
        gaps = [abs(left.categories[key] / PUBLIC_CATEGORY_POINTS[key] - right.categories[key] / PUBLIC_CATEGORY_POINTS[key]) * 100 for key in PUBLIC_CATEGORY_POINTS]  # type: ignore[operator]
        overall_gap = abs(left.overall - right.overall)  # type: ignore[operator]
        distance = sum(gaps, Fraction(0)) / len(gaps)
        values[pair_key] = distance
        if overall_gap <= 5 and all(gap <= 5 for gap in gaps):
            candidates.append(((left_id, right_id), distance))
    candidates.sort(key=lambda row: (row[1], row[0]))
    if not candidates:
        reasons = ["qualification.no_pair"]
        if any(_evidence_for_metrics(evidence_by_configuration[item.configuration_id], metric_ids) is None for item in cohort):
            reasons.append("evidence.metric_locators_incomplete")
        return _recommendation(
            use_case="cli_desktop_consistency", selected=(), status="no_recommendation",
            thresholds={"overall_gap": "<=5 normalized points", "each_category_gap": "<=5 normalized points", "exclusive_margin": ">=2 normalized points"},
            objective_name="mean_normalized_category_distance", objective_values=values,
            metric_ids=metric_ids, cohort_result_ids=cohort_result_ids, citations=citations,
            evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
        )
    if len(candidates) == 1 or candidates[1][1] - candidates[0][1] >= 2:
        selected_pairs = candidates[:1]
        status = "recommended"
        reasons = ("selection.only_qualifying_pair",) if len(candidates) == 1 else ("selection.exclusive_margin_met",)
    else:
        selected_pairs = [row for row in candidates if row[1] - candidates[0][1] < 2]
        status = "qualified_set"
        reasons = ("selection.margin_below_exclusive_threshold",)
    selected = tuple(configuration_id for pair, _value in selected_pairs for configuration_id in pair)
    return _recommendation(
        use_case="cli_desktop_consistency", selected=selected, status=status,
        thresholds={"overall_gap": "<=5 normalized points", "each_category_gap": "<=5 normalized points", "exclusive_margin": ">=2 normalized points"},
        objective_name="mean_normalized_category_distance", objective_values=values,
        metric_ids=metric_ids, cohort_result_ids=cohort_result_ids, citations=citations,
        evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
    )


def _validate_storage_diagnostics(
    attempted: Sequence[PublicConfigurationScore], diagnostics: Mapping[str, Sequence[Mapping[str, int]]] | None
) -> dict[str, Fraction]:
    if diagnostics is None:
        return {}
    expected = {item.configuration_id for item in attempted}
    if set(diagnostics) != expected:
        raise ValueError("storage diagnostics must cover every attempted configuration")
    medians: dict[str, Fraction] = {}
    for configuration_id, rows in diagnostics.items():
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or len(rows) != 3:
            raise ValueError(f"storage diagnostics for {configuration_id} require exactly three run rows")
        ratios: list[Fraction] = []
        repetitions: set[int] = set()
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {"repetition", "marginal_physical_bytes", "recovered_required_semantic_facts"}:
                raise ValueError(f"storage diagnostic for {configuration_id} has wrong fields")
            repetition = row["repetition"]
            size = row["marginal_physical_bytes"]
            facts = row["recovered_required_semantic_facts"]
            if type(repetition) is not int or repetition not in {1, 2, 3} or repetition in repetitions:
                raise ValueError(f"storage diagnostics for {configuration_id} require repetitions 1, 2, 3")
            if type(size) is not int or type(facts) is not int or size < 0 or facts < 1:
                raise ValueError("storage diagnostics require nonnegative marginal physical bytes and positive recovered facts")
            repetitions.add(repetition)
            ratios.append(Fraction(size, 1024 * facts))
        if repetitions != {1, 2, 3}:
            raise ValueError(f"storage diagnostics for {configuration_id} require repetitions 1, 2, 3")
        medians[configuration_id] = sorted(ratios)[1]
    return medians


def _lean_rule(
    cohort: Sequence[PublicConfigurationScore],
    citations: Mapping[str, ResultCitation],
    evidence_by_configuration: Mapping[str, Mapping[str, MetricEvidence]],
    cohort_result_ids: Sequence[str],
    medians: Mapping[str, Fraction],
) -> UseCaseQualification:
    metric_ids = tuple(dict.fromkeys(_category_metrics("record_fidelity", "causality_context", "portability_openness") + ("broad.classified_content_density",)))
    candidates: list[tuple[str, Fraction]] = []
    for item in cohort:
        if item.configuration_id not in medians or not _eligible(item, citations[item.configuration_id], evidence_by_configuration[item.configuration_id], metric_ids):
            continue
        if not (_at_least(item, "record_fidelity", Fraction(9, 10)) and _at_least(item, "causality_context", Fraction(9, 10)) and _at_least(item, "portability_openness", Fraction(9, 10))):
            continue
        if any(run.metrics["broad.classified_content_density"] < Fraction(19, 20) for run in item.runs):  # type: ignore[operator]
            continue
        candidates.append((item.configuration_id, medians[item.configuration_id]))
    candidates.sort(key=lambda row: (row[1], row[0]))
    if not candidates:
        reason = "diagnostic.missing" if not medians else "qualification.no_candidate"
        reasons = [reason]
        if any(_evidence_for_metrics(evidence_by_configuration[item.configuration_id], metric_ids) is None for item in cohort):
            reasons.append("evidence.metric_locators_incomplete")
        return _recommendation(
            use_case="lean_complete_record", selected=(), status="no_recommendation",
            thresholds={"record_fidelity": ">=27/30", "causality_context": ">=18/20", "portability_openness": ">=18/20", "classified_content_density": ">=95% in 3/3", "exclusive_advantage": ">=10% lower"},
            objective_name="median_marginal_physical_kib_per_recovered_required_semantic_fact",
            objective_values=dict(medians), metric_ids=metric_ids, cohort_result_ids=cohort_result_ids,
            citations=citations, evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
        )
    if len(candidates) == 1 or candidates[0][1] <= candidates[1][1] * Fraction(9, 10):
        selected = (candidates[0][0],)
        status = "recommended"
        reasons = ("selection.only_qualifier",) if len(candidates) == 1 else ("selection.exclusive_ten_percent_advantage",)
    else:
        selected = tuple(configuration_id for configuration_id, value in candidates if value * Fraction(9, 10) < candidates[0][1])
        status = "qualified_set"
        reasons = ("selection.no_ten_percent_advantage",)
    return _recommendation(
        use_case="lean_complete_record", selected=selected, status=status,
        thresholds={"record_fidelity": ">=27/30", "causality_context": ">=18/20", "portability_openness": ">=18/20", "classified_content_density": ">=95% in 3/3", "exclusive_advantage": ">=10% lower"},
        objective_name="median_marginal_physical_kib_per_recovered_required_semantic_fact",
        objective_values=dict(medians), metric_ids=metric_ids, cohort_result_ids=cohort_result_ids,
        citations=citations, evidence_by_configuration=evidence_by_configuration, reason_ids=reasons,
    )


def _improvements(
    configuration: PublicConfigurationScore,
    citation: ResultCitation,
    evidence: Mapping[str, MetricEvidence],
) -> ImprovementList:
    failed: dict[str, list[int]] = {}
    blocked: dict[str, list[int]] = {}
    for run in configuration.runs:
        for metric_id, value in run.metrics.items():
            if value is None:
                blocked.setdefault(metric_id, []).append(run.repetition)
            elif value < 1:
                failed.setdefault(metric_id, []).append(run.repetition)
    fractions = {category: value / PUBLIC_CATEGORY_POINTS[category] for category, value in configuration.categories.items() if value is not None}
    lowest: set[str] = set()
    if fractions:
        floor = min(fractions.values())
        lowest = {category for category, value in fractions.items() if value == floor}
    candidates: list[tuple[int, Fraction, str, ImprovementItem]] = []
    missing_evidence = False
    for metric_id, spec in PUBLIC_METRICS.items():
        repetitions = blocked.get(metric_id) or failed.get(metric_id)
        if not repetitions:
            continue
        metric_evidence = evidence.get(metric_id)
        if metric_evidence is None or not metric_evidence.observer_ids or not metric_evidence.native_locators:
            missing_evidence = True
            continue
        state = "blocked" if metric_id in blocked else "failed"
        consequence = (
            f"{metric_id} is unresolved in repetitions {', '.join(map(str, repetitions))}; the configuration cannot receive a complete public score."
            if state == "blocked"
            else f"{metric_id} retained less than the independently observed population in repetitions {', '.join(map(str, repetitions))}; {spec.category} loses points."
        )
        item = ImprovementItem(
            metric_id=metric_id,
            category=spec.category,
            state=state,
            repetitions=tuple(repetitions),
            observed_consequence=consequence,
            evidence_states=metric_evidence.evidence_states,
            observer_ids=metric_evidence.observer_ids,
            native_locators=metric_evidence.native_locators,
            acceptance_condition=ACCEPTANCE_CONDITIONS[metric_id],
        )
        priority = 0 if state == "blocked" else (1 if spec.category in lowest else 2)
        candidates.append((priority, -spec.points, metric_id, item))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    reasons = ("improvement.evidence_missing",) if missing_evidence else ()
    return ImprovementList(configuration.configuration_id, citation, tuple(item for *_key, item in candidates[:3]), reasons)


def _no_cohort_recommendations(reason_id: str) -> tuple[UseCaseQualification, ...]:
    audit_metrics = _category_metrics("record_fidelity", "causality_context")
    portable_metrics = _category_metrics("portability_openness")
    usage_metrics = _category_metrics("usage_attribution")
    all_metrics = tuple(PUBLIC_METRICS)
    lean_metrics = tuple(dict.fromkeys(
        _category_metrics("record_fidelity", "causality_context", "portability_openness")
        + ("broad.classified_content_density",)
    ))
    long_term_metrics = _category_metrics("durability_signal", "portability_openness")
    contracts = {
        "audit_ready": (
            {"record_fidelity": ">=27/30", "causality_context": ">=18/20", "exclusive_margin": ">=5 normalized points"},
            "mean_normalized_record_fidelity_and_causality_context",
            audit_metrics,
        ),
        "portable_archives": (
            {"portability_openness": ">=18/20", "deep_portable_metrics": "perfect in 3/3", "exclusive_margin": ">=5 normalized points"},
            "normalized_portability_openness",
            portable_metrics,
        ),
        "usage_accounting": (
            {"usage_attribution": ">=13.5/15", "usage_join_semantics_reconciliation": "perfect in 3/3", "exclusive_margin": ">=5 normalized points"},
            "normalized_usage_attribution",
            usage_metrics,
        ),
        "cli_desktop_consistency": (
            {"overall_gap": "<=5 normalized points", "each_category_gap": "<=5 normalized points", "exclusive_margin": ">=2 normalized points"},
            "mean_normalized_category_distance",
            all_metrics,
        ),
        "lean_complete_record": (
            {"record_fidelity": ">=27/30", "causality_context": ">=18/20", "portability_openness": ">=18/20", "classified_content_density": ">=95% in 3/3", "exclusive_advantage": ">=10% lower"},
            "median_marginal_physical_kib_per_recovered_required_semantic_fact",
            lean_metrics,
        ),
        "long_term_archives": (
            {"durability_signal": ">=13.5/15", "portability_openness": ">=18/20", "observed_builds": ">=2", "observation_window_days": ">=30", "exclusive_margin": ">=5 normalized points"},
            "mean_normalized_durability_signal_and_portability_openness",
            long_term_metrics,
        ),
    }
    return tuple(
        UseCaseQualification(
            use_case=use_case,
            status="no_recommendation",
            configuration_ids=(),
            rule_version=RECOMMENDATION_SCHEMA_VERSION,
            thresholds=contracts[use_case][0],
            objective_name=contracts[use_case][1],
            objective_values={},
            cohort_result_ids=(),
            metric_ids=contracts[use_case][2],
            citations=(),
            observer_ids=(),
            native_locators=(),
            reason_ids=(reason_id,),
        )
        for use_case in USE_CASES
    )


def build_recommendation_outputs(
    configurations: Iterable[PublicConfigurationScore],
    *,
    storage_diagnostics: Mapping[str, Sequence[Mapping[str, int]]] | None = None,
    metric_evidence_by_configuration: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
    reproduction_receipts: Mapping[str, str] | None = None,
) -> RecommendationOutputs:
    """Derive all six use cases and evidence-linked improvement lists.

    Normal report generation consumes the scorer's typed immutable bundle,
    deterministic offline reproduction receipt, and per-run metric evidence directly.
    Explicit mappings remain available for isolated negative controls.
    """

    raw = tuple(configurations)
    attempted = attempted_public_cohort(raw)
    if attempted is None:
        return RecommendationOutputs(_no_cohort_recommendations("cohort.attempted_surface_set_incomplete"), ())

    citations = {item.configuration_id: _citation(item, reproduction_receipts) for item in attempted}
    evidence_by_configuration = {
        item.configuration_id: _configuration_evidence(item, metric_evidence_by_configuration)
        for item in attempted
    }
    medians = _validate_storage_diagnostics(attempted, storage_diagnostics)
    improvements = tuple(
        _improvements(item, citations[item.configuration_id], evidence_by_configuration[item.configuration_id])
        for item in attempted
    )
    # Recommendations are scoped to their own use-case rules, so the
    # CLI/Desktop pair gate must not suppress unrelated use cases. Keep only
    # rows that the public scorer itself marked fully
    # reproduced here; each rule still applies its own receipt and metric
    # evidence checks through ``_eligible``.
    cohort = tuple(item for item in attempted if item.rankable)

    cohort_ids = _cohort_result_ids(citations, cohort)
    audit_metrics = _category_metrics("record_fidelity", "causality_context")
    portable_metrics = _category_metrics("portability_openness")
    usage_metrics = _category_metrics("usage_attribution")
    long_term_metrics = _category_metrics("durability_signal", "portability_openness")

    recommendations = [
        _objective_rule(
            use_case="audit_ready", cohort=cohort, citations=citations,
            evidence_by_configuration=evidence_by_configuration, cohort_result_ids=cohort_ids,
            metric_ids=audit_metrics,
            thresholds={"record_fidelity": ">=27/30", "causality_context": ">=18/20", "exclusive_margin": ">=5 normalized points"},
            objective_name="mean_normalized_record_fidelity_and_causality_context",
            predicate=lambda item: _at_least(item, "record_fidelity", Fraction(9, 10)) and _at_least(item, "causality_context", Fraction(9, 10)),
            objective=lambda item: (item.categories["record_fidelity"] / 30 + item.categories["causality_context"] / 20) * 50,
        ),
        _objective_rule(
            use_case="portable_archives", cohort=cohort, citations=citations,
            evidence_by_configuration=evidence_by_configuration, cohort_result_ids=cohort_ids,
            metric_ids=portable_metrics,
            thresholds={"portability_openness": ">=18/20", "deep_portable_metrics": "perfect in 3/3", "exclusive_margin": ">=5 normalized points"},
            objective_name="normalized_portability_openness",
            predicate=lambda item: _at_least(item, "portability_openness", Fraction(9, 10)) and _metric_perfect(item, ("portable.complete_root", "portable.companions", "portable.isolated_decode", "portable.canonical_equality")),
            objective=lambda item: item.categories["portability_openness"] * 5,
        ),
        _objective_rule(
            use_case="usage_accounting", cohort=cohort, citations=citations,
            evidence_by_configuration=evidence_by_configuration, cohort_result_ids=cohort_ids,
            metric_ids=usage_metrics,
            thresholds={"usage_attribution": ">=13.5/15", "usage_join_semantics_reconciliation": "perfect in 3/3", "exclusive_margin": ">=5 normalized points"},
            objective_name="normalized_usage_attribution",
            predicate=lambda item: _at_least(item, "usage_attribution", Fraction(9, 10)) and _metric_perfect(item, ("attribution.usage", "attribution.token_semantics", "attribution.reconciliation")),
            objective=lambda item: item.categories["usage_attribution"] * Fraction(20, 3),
        ),
        _pair_rule(cohort, citations, evidence_by_configuration, cohort_ids),
        _lean_rule(cohort, citations, evidence_by_configuration, cohort_ids, medians),
        _objective_rule(
            use_case="long_term_archives", cohort=cohort, citations=citations,
            evidence_by_configuration=evidence_by_configuration, cohort_result_ids=cohort_ids,
            metric_ids=long_term_metrics,
            thresholds={"durability_signal": ">=13.5/15", "portability_openness": ">=18/20", "observed_builds": ">=2", "observation_window_days": ">=30", "exclusive_margin": ">=5 normalized points"},
            objective_name="mean_normalized_durability_signal_and_portability_openness",
            predicate=lambda item: _at_least(item, "durability_signal", Fraction(9, 10)) and _at_least(item, "portability_openness", Fraction(9, 10)) and _long_term_observation(item),
            objective=lambda item: (item.categories["durability_signal"] / 15 + item.categories["portability_openness"] / 20) * 50,
        ),
    ]
    return RecommendationOutputs(tuple(recommendations), improvements)


__all__ = [
    "ImprovementItem",
    "ImprovementList",
    "MetricEvidence",
    "RECOMMENDATION_SCHEMA_VERSION",
    "RECOMMENDATION_STATUSES",
    "RecommendationOutputs",
    "ResultCitation",
    "USE_CASES",
    "UseCaseQualification",
    "build_recommendation_outputs",
]
