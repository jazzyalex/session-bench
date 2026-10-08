"""Expanded release diagnostics without altering archived cohort entry points.

The 31 metrics and arithmetic reuse the public scorer. Raw inputs stay unranked;
release_replay verifies native scoring, independently pinned operator review and
exact public inputs before sealing an aggregate. Structural receipts alone fail.
"""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

from .release_evidence import validate_release_evidence_input
from .release_scope import CONFIGURATION_IDS, load_release_scope, release_scope_summary
from .survival_metrics import display_decimal, score_run
from .v1_public_score import (
    PUBLIC_WEIGHT_VECTORS, PublicConfigurationScore, PublicRunScore,
    _assemble_public_run, _public_run_identity, _reportable_metric_evidence,
    aggregate_public_configuration, validate_format_evidence,
)


RELEASE_SCORE_SCHEMA_VERSION = "session-bench-public-release-score-v1"
MINIMUM_LEADERBOARD_ROWS = 3
INDEPENDENT_REPLAY_VERIFIER_AVAILABLE = True


def score_release_run(
    survival_evidence: Mapping[str, Any], format_evidence: Mapping[str, Any],
) -> PublicRunScore:
    """Score one explicitly opted-in run using the unchanged public metric math."""
    survival = validate_release_evidence_input(survival_evidence)
    profile = validate_format_evidence(format_evidence)
    if (survival["run_id"], survival["configuration_id"], survival["repetition"]) != (
        profile["run_id"], profile["configuration_id"], profile["repetition"]
    ):
        raise ValueError("release survival and format evidence identities must match exactly")
    expected_harness = next(
        row["harness"] for row in load_release_scope()["rows"]
        if row["configuration_id"] == survival["configuration_id"]
    )
    identity = survival["identity"]
    if identity is not None and identity.get("harness") is not None and identity["harness"] != expected_harness:
        raise ValueError("release evidence harness does not match the scoped configuration manifest")
    return _assemble_public_run(
        score_run(survival["measurement"]), profile["profile"],
        metric_evidence=_reportable_metric_evidence(survival, profile),
        build=profile["build"], collected_on=profile["collected_on"],
        result_id=profile["result_id"], survival_evaluation_id=survival["evaluation_id"],
        identity=_public_run_identity(survival, profile),
    )


def score_release_configuration(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]], *,
    configuration_evidence: Mapping[str, Any] | None = None,
) -> PublicConfigurationScore:
    """Return diagnostics, withholding aggregate/public claims pending replay verification.

    The current input validator checks structural bindings only. Until native
    artifacts can be independently decoded and rescored, even a caller-created
    bundle/receipt cannot establish a public overall or ranking.
    """
    aggregate = aggregate_public_configuration(
        [score_release_run(survival, profile) for survival, profile in pairs],
        configuration_evidence=configuration_evidence,
    )
    has_metrics = any(value is not None for run in aggregate.runs for value in run.metrics.values())
    blockers = tuple(dict.fromkeys((*aggregate.blockers, "independent_native_score_replay_not_verified")))
    return replace(
        aggregate,
        overall=None,
        overall_range=None,
        sensitivity=None,
        rankable=False,
        blockers=blockers,
        verification="partially_verified" if has_metrics else "unranked",
    )


def attempted_release_cohort(
    configurations: Iterable[PublicConfigurationScore],
) -> tuple[PublicConfigurationScore, ...]:
    """Validate evaluated rows; broader attempt coverage lives in the status ledger."""
    values = tuple(configurations)
    ids = [value.configuration_id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError("release score configuration IDs must be unique")
    if set(ids) - set(CONFIGURATION_IDS):
        raise ValueError("release score has unknown configuration IDs")
    by_id = {value.configuration_id: value for value in values}
    return tuple(by_id[key] for key in CONFIGURATION_IDS if key in by_id)


def qualified_release_cohort(
    configurations: Iterable[PublicConfigurationScore],
) -> tuple[PublicConfigurationScore, ...]:
    """Expose only exact aggregates sealed by the executed replay verifier."""
    values = attempted_release_cohort(configurations)
    from .release_replay import is_verified_release_configuration
    return tuple(value for value in values if value.rankable and is_verified_release_configuration(value))


def release_leaderboard_ranks(
    configurations: Iterable[PublicConfigurationScore], vector: str = "baseline",
) -> dict[str, int]:
    """Rank at least three sealed complete rows; caller dataclasses grant no proof."""
    if vector not in PUBLIC_WEIGHT_VECTORS:
        raise ValueError(f"unknown public weight vector: {vector}")
    attempted_release_cohort(configurations)
    cohort = qualified_release_cohort(configurations)
    if len(cohort) < MINIMUM_LEADERBOARD_ROWS:
        return {}
    scored = []
    for value in cohort:
        if value.sensitivity is None:
            raise ValueError("rankable release row lacks sensitivity totals")
        scored.append((value.configuration_id, Fraction(display_decimal(value.sensitivity[vector]))))
    scored.sort(key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int] = {}
    prior: Fraction | None = None
    rank = 0
    for position, (configuration_id, value) in enumerate(scored, 1):
        if value != prior:
            rank, prior = position, value
        ranks[configuration_id] = rank
    return ranks


def release_scorecard(
    scope: Mapping[str, Any], statuses: Sequence[Mapping[str, Any]],
    configurations: Iterable[PublicConfigurationScore],
) -> dict[str, Any]:
    """Versioned report payload with all scoped statuses and a qualified subset."""
    cohort = attempted_release_cohort(configurations)
    from .release_replay import is_verified_release_configuration
    qualified = qualified_release_cohort(cohort)
    summary = release_scope_summary(
        scope, statuses, rankable_configuration_ids=[value.configuration_id for value in qualified],
    )
    status_by_id = {row["configuration_id"]: row for row in statuses}
    for value in cohort:
        if status_by_id[value.configuration_id]["state"] == "unattempted":
            raise ValueError("evaluated release score cannot belong to an unattempted row")
        if not is_verified_release_configuration(value) and (
            value.rankable or value.overall is not None or value.overall_range is not None
            or value.sensitivity is not None or value.verification == "fully_reproduced"
        ):
            raise ValueError("release score claims require independent native replay verification")
    all_verified = bool(cohort) and all(is_verified_release_configuration(value) for value in cohort)
    enough_rows = len(qualified) >= MINIMUM_LEADERBOARD_ROWS
    publication_blockers = []
    if not all_verified:
        publication_blockers.append("independent_native_score_replay_not_verified")
    if not enough_rows:
        publication_blockers.append("fewer_than_three_rankable_rows")
    publication_eligible = all_verified and enough_rows
    return {
        "schema_version": RELEASE_SCORE_SCHEMA_VERSION,
        **summary,
        "scores": [value.display() for value in cohort],
        "leaderboard_gate": {
            "minimum_rankable_rows": MINIMUM_LEADERBOARD_ROWS,
            "rankable_rows": len(qualified),
            "met": enough_rows,
            "reason": (
                None if enough_rows
                else "independent_native_score_replay_not_verified"
                if not all_verified
                else "fewer_than_three_rankable_rows"
            ),
        },
        "measurement_population_complete": len(qualified) == len(CONFIGURATION_IDS),
        "release_goal_complete": publication_eligible and len(qualified) == len(CONFIGURATION_IDS),
        "publication_eligible": publication_eligible,
        "publication_blockers": publication_blockers,
        "ranks": release_leaderboard_ranks(qualified),
        "sensitivity_ranks": {
            vector: release_leaderboard_ranks(qualified, vector)
            for vector in PUBLIC_WEIGHT_VECTORS
        },
    }
