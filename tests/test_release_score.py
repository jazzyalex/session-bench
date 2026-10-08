"""Synthetic controls for opt-in expanded release scoring and visible coverage."""

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from session_bench.release_evidence import (
    RELEASE_EVIDENCE_SCHEMA_VERSION, validate_release_evidence_input,
)
from session_bench.release_scope import CONFIGURATION_IDS, load_release_scope
from session_bench.release_score import (
    RELEASE_SCORE_SCHEMA_VERSION, attempted_release_cohort,
    qualified_release_cohort, release_leaderboard_ranks, release_scorecard,
    score_release_configuration, score_release_run,
)
from session_bench.survival_evidence import validate_evidence_input, validate_prospective_evidence_input
from session_bench.survival_metrics import load_survival_input
from session_bench.v1_public_score import (
    CONFIGURATION_EVIDENCE_SCHEMA_VERSION, PUBLIC_WEIGHT_VECTORS, TARGET_CONFIGURATIONS,
    format_evidence_control, format_profile_document, public_leaderboard_ranks,
    score_public_run, survival_evidence_control,
)


ROOT = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1"


def evidence_pair(key="pi", repetition=1, *, identity=True):
    measurement = load_survival_input(ROOT / "equivalent-jsonl/input.jsonl")
    measurement.update(run_id=f"{key}-{repetition}", configuration_id=key, repetition=repetition)
    row = next(row for row in load_release_scope()["rows"] if row["configuration_id"] == key)
    envelope = {
        "provider": "synthetic-provider", "harness": row["harness"],
        "surface": row["surface"], "execution_mode": "constructed-control",
        "os": "macOS-test", "build": "constructed-control", "model": "synthetic-model",
        "configuration": "synthetic-default", "observer_schema_version": "1.0-survival-observer",
    } if identity else None
    survival = survival_evidence_control(measurement, evaluation_id=f"evaluation-{key}-{repetition}", identity=envelope)
    survival["schema_version"] = RELEASE_EVIDENCE_SCHEMA_VERSION
    profile = format_profile_document(run_id=measurement["run_id"], configuration_id=key, repetition=repetition)
    return survival, format_evidence_control(profile, result_id=f"result-{key}-{repetition}")


def configuration_evidence(key):
    results = [f"result-{key}-{repetition}" for repetition in (1, 2, 3)]
    return {
        "schema_version": CONFIGURATION_EVIDENCE_SCHEMA_VERSION, "configuration_id": key,
        "bundle": {"id": f"bundle-{key}", "sha256": "f" * 64, "immutable": True,
                   "public": True, "result_ids": results},
        "reproduction_receipt": {"id": f"receipt-{key}", "sha256": "e" * 64,
                                 "bundle_sha256": "f" * 64, "offline_recomputed": True,
                                 "verified": True, "result_ids": results},
    }


def configuration(key="pi", *, identity=True, reproduced=True):
    return score_release_configuration(
        [evidence_pair(key, repetition, identity=identity) for repetition in (1, 2, 3)],
        configuration_evidence=configuration_evidence(key) if reproduced else None,
    )


def status_rows():
    return [{"configuration_id": key, "state": "blocked", "attempt_ids": [f"attempt-{key}"],
             "evidence_refs": [f"attempts/{key}.json"], "reason_ids": ["preflight.unavailable"]}
            for key in CONFIGURATION_IDS]


@pytest.mark.parametrize("key", CONFIGURATION_IDS)
def test_all_release_rows_accept_explicit_schema_and_same_31_metric_math(key):
    survival, profile = evidence_pair(key)
    result = score_release_run(survival, profile)
    validated = validate_release_evidence_input(survival)
    assert validated["configuration_id"] == key
    assert validated["independent_native_score_replay_verified"] is False
    assert result.configuration_id == key
    assert len(result.metrics) == len(result.metric_evidence) == 31
    assert result.overall == 100


def test_existing_five_cohort_entry_points_do_not_accept_new_schema():
    survival, profile = evidence_pair("codex-cli")
    for validator in (validate_evidence_input, validate_prospective_evidence_input):
        with pytest.raises(ValueError, match="schema_version"):
            validator(survival)
    with pytest.raises(ValueError, match="schema_version"):
        score_public_run(survival, profile)
    assert len(TARGET_CONFIGURATIONS) == 5


def test_expanded_scorer_requires_opt_in_and_rejects_unknown_configuration():
    survival, profile = evidence_pair()
    survival["schema_version"] = "session-bench-survival-evidence-v1"
    with pytest.raises(ValueError, match="schema_version"):
        score_release_run(survival, profile)
    survival["schema_version"] = RELEASE_EVIDENCE_SCHEMA_VERSION
    survival["configuration_id"] = survival["measurement"]["configuration_id"] = "unknown"
    with pytest.raises(ValueError, match="configuration_id"):
        score_release_run(survival, profile)


@pytest.mark.parametrize("mutation", [
    lambda survival, profile: survival["metric_evidence"].pop(),
    lambda survival, profile: survival["observer"].update(sha256="bad"),
    lambda survival, profile: profile.update(run_id="other-run"),
])
def test_expansion_retains_strict_evidence_checks(mutation):
    survival, profile = evidence_pair()
    mutation(survival, profile)
    with pytest.raises(ValueError):
        score_release_run(survival, profile)


def test_release_evidence_cannot_assert_its_own_native_replay_verification():
    survival, _ = evidence_pair()
    survival["independent_native_score_replay_verified"] = True
    with pytest.raises(ValueError, match="wrong fields"):
        validate_release_evidence_input(survival)


@pytest.mark.parametrize(("identity", "reproduced"), [(False, True), (True, False)])
def test_configuration_still_requires_bound_identity_and_reproduction(identity, reproduced):
    result = configuration(identity=identity, reproduced=reproduced)
    assert not result.rankable
    assert result.overall is None
    assert release_leaderboard_ranks([result]) == {}


def test_constructed_receipts_keep_diagnostics_but_cannot_publish_totals_or_ranks():
    complete = configuration("pi")
    incomplete = configuration("openclaw", reproduced=False)
    assert not complete.rankable and complete.overall is None
    assert complete.sensitivity is None
    assert complete.verification == "partially_verified"
    assert "independent_native_score_replay_not_verified" in complete.blockers
    assert complete.categories["record_fidelity"] is not None
    assert qualified_release_cohort([incomplete, complete]) == ()
    assert release_leaderboard_ranks([incomplete, complete]) == {}
    forged = replace(
        complete,
        rankable=True,
        sensitivity={vector: 100 for vector in PUBLIC_WEIGHT_VECTORS},
    )
    forged_rows = [forged, replace(forged, configuration_id="kimi"), replace(forged, configuration_id="hermes")]
    assert release_leaderboard_ranks(forged_rows) == {}
    assert public_leaderboard_ranks([configuration("codex-cli")]) == {}


def test_versioned_scorecard_reports_all_rows_and_keeps_blockers_unscored():
    rows = status_rows()
    snapshot = copy.deepcopy(rows)
    result = release_scorecard(load_release_scope(), rows, [configuration("pi")])
    assert result["schema_version"] == RELEASE_SCORE_SCHEMA_VERSION
    assert result["scoped_rows"] == result["attempted_rows"] == 14
    assert result["blocked_rows"] == 14 and result["rankable_rows"] == 0
    assert result["ranks"] == {}
    assert not result["leaderboard_gate"]["met"]
    assert result["leaderboard_gate"]["reason"] == "independent_native_score_replay_not_verified"
    assert not result["release_goal_complete"]
    assert not result["publication_eligible"]
    assert not result["measurement_population_complete"]
    assert "independent_native_score_replay_not_verified" in result["publication_blockers"]
    assert result["rows"][1]["reason_ids"] == ["preflight.unavailable"]
    assert len(result["scores"]) == 1
    assert rows == snapshot


def test_rankable_status_cannot_exist_without_evaluated_qualified_row():
    rows = status_rows()
    rows[0].update(state="rankable", reason_ids=[])
    with pytest.raises(ValueError, match="independently evaluated"):
        release_scorecard(load_release_scope(), rows, [])


@pytest.mark.parametrize("claims", [
    {"rankable": True},
    {"overall": 100},
    {"overall_range": (100, 100)},
    {"sensitivity": {vector: 100 for vector in PUBLIC_WEIGHT_VECTORS}},
    {"verification": "fully_reproduced"},
])
def test_report_boundary_rejects_forged_public_score_claims_without_native_verifier(claims):
    forged = replace(configuration("pi"), **claims)
    with pytest.raises(ValueError, match="independent native replay verification"):
        release_scorecard(load_release_scope(), status_rows(), [forged])


def test_unattempted_row_cannot_smuggle_in_an_evaluated_score():
    rows = status_rows()
    rows[0].update(state="unattempted", attempt_ids=[], evidence_refs=[], reason_ids=[])
    with pytest.raises(ValueError, match="unattempted row"):
        release_scorecard(load_release_scope(), rows, [configuration("pi", reproduced=False)])


def test_score_cohort_rejects_duplicate_and_unknown_configuration():
    value = configuration()
    with pytest.raises(ValueError, match="unique"):
        attempted_release_cohort([value, value])
    with pytest.raises(ValueError, match="unknown configuration"):
        attempted_release_cohort([replace(value, configuration_id="unknown")])


def test_release_ranking_remains_unavailable_even_with_three_forged_rankable_rows():
    first, second, third = configuration("pi"), configuration("openclaw"), configuration("kimi")
    assert release_leaderboard_ranks([first, second, third]) == {}
    forged = [replace(value, rankable=True, sensitivity={vector: 100 for vector in PUBLIC_WEIGHT_VECTORS})
              for value in (first, second, third)]
    assert release_leaderboard_ranks(forged) == {}
    with pytest.raises(ValueError, match="unknown public weight"):
        release_leaderboard_ranks([], "unknown")


def test_release_identity_harness_must_match_manifest_without_inventing_missing_identity():
    survival, profile = evidence_pair("codex-desktop")
    survival["identity"]["harness"] = "codex-cli"
    with pytest.raises(ValueError, match="harness does not match"):
        score_release_run(survival, profile)

    missing, profile = evidence_pair("codex-desktop", identity=False)
    result = score_release_run(missing, profile)
    assert result.identity.harness is None
    aggregate = score_release_configuration(
        [evidence_pair("codex-desktop", n, identity=False) for n in (1, 2, 3)],
        configuration_evidence=configuration_evidence("codex-desktop"),
    )
    assert not aggregate.rankable
    assert any("identity harness missing" in blocker for blocker in aggregate.blockers)


def test_three_verified_rows_do_not_complete_fourteen_configuration_goal(monkeypatch):
    """Exercise coverage policy separately from native verification controls."""
    import session_bench.release_replay as replay
    import session_bench.release_score as scoring
    keys = CONFIGURATION_IDS[:3]
    cohort = tuple(configuration(key) for key in keys)
    monkeypatch.setattr(scoring, 'qualified_release_cohort', lambda values: tuple(values))
    monkeypatch.setattr(replay, 'is_verified_release_configuration', lambda value: True)
    monkeypatch.setattr(scoring, 'release_leaderboard_ranks', lambda *args: {})
    statuses = status_rows()
    for row in statuses:
        if row['configuration_id'] in keys:
            row.update(state='rankable', reason_ids=[])
    report = release_scorecard(load_release_scope(), statuses, cohort)
    assert report['publication_eligible']
    assert report['attempted_rows'] == 14
    assert report['rankable_rows'] == 3
    assert not report['measurement_population_complete']
    assert not report['release_goal_complete']
