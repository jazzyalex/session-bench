import copy
from fractions import Fraction
import json
from pathlib import Path
import sqlite3

import pytest

from session_bench.survival_metrics import (
    CATEGORY_POINTS,
    METRICS,
    SCHEMA_VERSION,
    TARGET_CONFIGURATIONS,
    WEIGHT_VECTORS,
    aggregate_configuration,
    competition_ranks,
    display_decimal,
    leaderboard_ranks,
    leaderboard_sensitivity_winners,
    load_survival_input,
    score_run,
    sensitivity_winners,
    validate_input,
    weighted_total,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1"


def baseline_document():
    return load_survival_input(FIXTURES / "equivalent-jsonl/input.jsonl")


def metric(document, metric_id):
    return next(row for row in document["metrics"] if row["id"] == metric_id)


def changed(document, metric_id, **updates):
    result = copy.deepcopy(document)
    metric(result, metric_id).update(updates)
    return result


def as_run(document, configuration_id, repetition):
    value = copy.deepcopy(document)
    value["run_id"] = f"{configuration_id}-{repetition}"
    value["configuration_id"] = configuration_id
    value["repetition"] = repetition
    return score_run(value)


def test_exact_rubric_has_five_categories_and_100_points():
    assert list(CATEGORY_POINTS) == [
        "work_reconstruction",
        "causal_links",
        "revision_trace",
        "response_attribution",
        "portable_archive",
    ]
    assert sum(CATEGORY_POINTS.values(), Fraction()) == 100
    assert sum((spec.points for spec in METRICS.values()), Fraction()) == 100
    assert all(sum(vector.values()) == 100 for vector in WEIGHT_VECTORS.values())


def test_metric_ids_and_minimum_populations_match_frozen_workload():
    assert {metric_id: spec.minimum_observed for metric_id, spec in METRICS.items()} == {
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


def test_equivalent_jsonl_and_sqlite_inputs_score_identically(tmp_path):
    jsonl = load_survival_input(FIXTURES / "equivalent-jsonl/input.jsonl")
    sqlite_path = tmp_path / "input.sqlite"
    connection = sqlite3.connect(sqlite_path)
    connection.executescript((FIXTURES / "equivalent-sqlite/input.sql").read_text())
    connection.close()
    sqlite_value = load_survival_input(sqlite_path)

    assert sqlite_value == jsonl
    jsonl_score = score_run(jsonl)
    sqlite_score = score_run(sqlite_value)
    assert sqlite_score == jsonl_score
    assert jsonl_score.overall == 100
    assert jsonl_score.portable_gate and jsonl_score.rankable
    assert all(category.complete for category in jsonl_score.categories.values())


def test_duplicate_sensitive_denominator_uses_max_population():
    document = changed(
        baseline_document(),
        "work.submitted_turns",
        correct=2,
        observed_eligible=2,
        decoded_eligible=3,
    )
    result = score_run(document)
    assert result.metrics["work.submitted_turns"] == Fraction(2, 3)
    assert result.categories["work_reconstruction"].score == Fraction(98, 3)
    assert result.overall == Fraction(293, 3)
    assert result.display()["overall"] == "97.7"


@pytest.mark.parametrize("state", ["native_absent", "contradiction"])
def test_resolved_loss_states_contribute_zero(state):
    document = changed(
        baseline_document(),
        "revision.r1",
        state=state,
        correct=0,
    )
    result = score_run(document)
    assert result.metrics["revision.r1"] == 0
    assert result.categories["revision_trace"].score == Fraction(45, 4)
    assert result.rankable


@pytest.mark.parametrize(
    "state",
    ["unresolved", "decoder_unsupported", "unexercised", "invalid_capture"],
)
def test_blocking_state_preserves_known_quality_coverage_and_range(state):
    document = changed(baseline_document(), "attribution.usage", state=state)
    result = score_run(document)
    category = result.categories["response_attribution"]
    assert not result.rankable and result.overall is None
    assert category.score is None
    assert category.known_points == 12
    assert category.known_possible_points == 12
    assert category.known_quality == 1
    assert category.coverage == Fraction(3, 4)
    assert category.possible_min == 12
    assert category.possible_max == 20
    assert category.display() == {
        "score": None,
        "known_points": "12.0",
        "known_possible_points": "12.0",
        "known_quality_percent": "100.0",
        "coverage": "3/4",
        "possible_min": "12.0",
        "possible_max": "20.0",
        "possible_percent_min": "60.0",
        "possible_percent_max": "100.0",
    }


def test_portable_archive_is_a_hard_gate_for_run_and_configuration():
    baseline = baseline_document()
    failed = changed(
        baseline,
        "portable.companions",
        state="native_absent",
        correct=0,
        decoded_eligible=0,
    )
    failed_run = as_run(failed, "portable-failure", 2)
    assert failed_run.categories["portable_archive"].score == Fraction(15, 2)
    assert not failed_run.portable_gate
    assert not failed_run.rankable and failed_run.overall is None
    assert "portable_archive:hard_gate" in failed_run.blockers

    configuration = aggregate_configuration(
        [
            as_run(baseline, "portable-failure", 1),
            failed_run,
            as_run(baseline, "portable-failure", 3),
        ]
    )
    assert not configuration.portable_gate
    assert not configuration.rankable
    assert configuration.overall is None
    assert configuration.sensitivity is None
    assert configuration.categories["portable_archive"] == Fraction(55, 6)


def test_three_run_aggregation_is_exact_and_rounds_only_for_display():
    baseline = baseline_document()
    duplicate = changed(
        baseline,
        "work.submitted_turns",
        correct=2,
        decoded_eligible=3,
    )
    partial = changed(
        baseline,
        "work.submitted_turns",
        correct=1,
        decoded_eligible=2,
    )
    configuration = aggregate_configuration(
        [
            as_run(partial, "exact", 3),
            as_run(baseline, "exact", 1),
            as_run(duplicate, "exact", 2),
        ]
    )
    assert configuration.overall == Fraction(1765, 18)
    assert configuration.overall_range == (Fraction(193, 2), Fraction(100))
    assert configuration.category_ranges["work_reconstruction"] == (
        Fraction(63, 2),
        Fraction(35),
    )
    assert configuration.display()["overall"] == "98.1"
    assert configuration.display()["range"] == ["96.5", "100.0"]
    assert display_decimal(Fraction(201, 20)) == "10.1"


def test_mutation_matrix_changes_only_declared_metric_scores():
    baseline = baseline_document()
    baseline_score = score_run(baseline)
    matrix = json.loads((FIXTURES / "mutations/matrix.json").read_text())
    assert matrix["schema_version"] == "session-bench-survival-mutations-v1"
    assert {item["name"] for item in matrix["mutations"]} == {
        "remove_prompt",
        "remove_visible_response",
        "remove_action",
        "remove_result",
        "remove_changed_file_evidence",
        "remove_r1",
        "remove_r2",
        "reverse_revision_order",
        "remove_usage_join",
        "remove_correction_link",
        "remove_model_config_join",
        "remove_token_semantics",
        "break_usage_reconciliation",
        "incomplete_declared_root",
        "missing_companion",
        "swap_action_result_joins",
        "duplicate_prompt_id",
        "original_root_dependency",
        "canonical_reconstruction_mismatch",
    }
    covered_metrics = {
        metric_id
        for mutation in matrix["mutations"]
        for metric_id in mutation["changed_metrics"]
    }
    assert covered_metrics == set(METRICS)
    for mutation in matrix["mutations"]:
        document = copy.deepcopy(baseline)
        for metric_id, updates in mutation["updates"].items():
            metric(document, metric_id).update(updates)
        result = score_run(document)
        changed_scores = {
            metric_id
            for metric_id in METRICS
            if result.metrics[metric_id] != baseline_score.metrics[metric_id]
        }
        assert changed_scores == set(mutation["changed_metrics"]), mutation["name"]
        changed_categories = {
            category
            for category in CATEGORY_POINTS
            if result.categories[category].score
            != baseline_score.categories[category].score
        }
        assert changed_categories == {
            METRICS[metric_id].category for metric_id in mutation["changed_metrics"]
        }, mutation["name"]
        assert result.portable_gate is mutation["portable_gate"], mutation["name"]
        if mutation["portable_gate"]:
            assert result.rankable and result.overall is not None
            assert result.overall < baseline_score.overall
        else:
            assert not result.rankable and result.overall is None


def test_sensitivity_vectors_and_competition_ranks_use_exact_scores():
    baseline = baseline_document()
    weaker_work = changed(
        baseline,
        "work.changed_files",
        state="contradiction",
        correct=0,
    )
    weaker_causal = changed(
        baseline,
        "causal.action_result",
        correct=2,
        observed_eligible=4,
        decoded_eligible=4,
    )
    configurations = [
        aggregate_configuration([as_run(weaker_work, "work", index) for index in (1, 2, 3)]),
        aggregate_configuration([as_run(weaker_causal, "causal", index) for index in (1, 2, 3)]),
    ]
    assert competition_ranks(configurations, "baseline") == {"causal": 1, "work": 2}
    assert competition_ranks(configurations, "equal") == {"work": 1, "causal": 2}
    winners, stable = sensitivity_winners(configurations)
    assert winners["baseline"] == ("causal",)
    assert winners["equal"] == ("work",)
    assert not stable
    perfect = {category: Fraction(1) for category in CATEGORY_POINTS}
    assert {vector: weighted_total(perfect, vector) for vector in WEIGHT_VECTORS} == {
        vector: Fraction(100) for vector in WEIGHT_VECTORS
    }


def test_competition_ranking_uses_competition_ties_and_skips_unrankable():
    baseline = baseline_document()
    tied_a = aggregate_configuration([as_run(baseline, "a", index) for index in (1, 2, 3)])
    tied_b = aggregate_configuration([as_run(baseline, "b", index) for index in (1, 2, 3)])
    lower = changed(baseline, "revision.r1", state="contradiction", correct=0)
    lower_c = aggregate_configuration([as_run(lower, "c", index) for index in (1, 2, 3)])
    blocked = changed(baseline, "attribution.usage", state="unresolved")
    blocked_d = aggregate_configuration([as_run(blocked, "d", index) for index in (1, 2, 3)])
    assert competition_ranks([lower_c, tied_b, blocked_d, tied_a]) == {"a": 1, "b": 1, "c": 3}
    assert competition_ranks([tied_a, blocked_d]) == {}


def test_public_leaderboard_requires_frozen_targets_three_qualified_and_surface_pair():
    assert TARGET_CONFIGURATIONS == (
        "codex-cli",
        "codex-desktop",
        "cursor-cli",
        "cursor-desktop",
        "opencode-cli",
    )
    baseline = baseline_document()

    def configuration(configuration_id, document=baseline):
        return aggregate_configuration(
            [as_run(document, configuration_id, repetition) for repetition in (1, 2, 3)]
        )

    codex_cli = configuration("codex-cli")
    codex_desktop = configuration("codex-desktop")
    cursor_cli = configuration("cursor-cli")
    cursor_desktop = configuration("cursor-desktop")
    opencode_cli = configuration("opencode-cli")

    assert leaderboard_ranks([codex_cli, codex_desktop]) == {}
    assert leaderboard_ranks([codex_cli, cursor_cli, opencode_cli]) == {}
    assert leaderboard_ranks([codex_cli, codex_desktop, opencode_cli]) == {
        "codex-cli": 1,
        "codex-desktop": 1,
        "opencode-cli": 1,
    }
    assert leaderboard_ranks([cursor_cli, cursor_desktop, opencode_cli]) == {
        "cursor-cli": 1,
        "cursor-desktop": 1,
        "opencode-cli": 1,
    }

    blocked = changed(baseline, "attribution.usage", state="unresolved")
    blocked_opencode = configuration("opencode-cli", blocked)
    assert leaderboard_ranks([codex_cli, codex_desktop, blocked_opencode]) == {}


def test_public_leaderboard_refuses_unknown_or_duplicate_configuration_ids():
    baseline = baseline_document()

    def configuration(configuration_id):
        return aggregate_configuration(
            [as_run(baseline, configuration_id, repetition) for repetition in (1, 2, 3)]
        )

    codex_cli = configuration("codex-cli")
    codex_desktop = configuration("codex-desktop")
    unknown = configuration("unknown-cli")
    with pytest.raises(ValueError, match="unknown target configuration"):
        leaderboard_ranks([codex_cli, codex_desktop, unknown])
    with pytest.raises(ValueError, match="must be unique"):
        leaderboard_ranks([codex_cli, codex_cli, codex_desktop])


def test_public_sensitivity_refuses_arbitrary_configs_and_ineligible_cohorts():
    baseline = baseline_document()

    def configuration(configuration_id):
        return aggregate_configuration(
            [as_run(baseline, configuration_id, repetition) for repetition in (1, 2, 3)]
        )

    with pytest.raises(ValueError, match="unknown target configuration"):
        leaderboard_sensitivity_winners([configuration("a"), configuration("b")])

    winners, stable = leaderboard_sensitivity_winners(
        [configuration("codex-cli"), configuration("codex-desktop")]
    )
    assert winners == {vector: () for vector in WEIGHT_VECTORS}
    assert not stable

    winners, stable = leaderboard_sensitivity_winners(
        [configuration("codex-cli"), configuration("cursor-cli"), configuration("opencode-cli")]
    )
    assert winners == {vector: () for vector in WEIGHT_VECTORS}
    assert not stable


def test_public_sensitivity_reports_stable_and_unstable_eligible_cohorts():
    baseline = baseline_document()

    def configuration(configuration_id, document):
        return aggregate_configuration(
            [as_run(document, configuration_id, repetition) for repetition in (1, 2, 3)]
        )

    perfect = [
        configuration("codex-cli", baseline),
        configuration("codex-desktop", baseline),
        configuration("opencode-cli", baseline),
    ]
    winners, stable = leaderboard_sensitivity_winners(perfect)
    expected_tie = ("codex-cli", "codex-desktop", "opencode-cli")
    assert winners == {vector: expected_tie for vector in WEIGHT_VECTORS}
    assert stable

    weaker_work = changed(
        baseline,
        "work.changed_files",
        state="contradiction",
        correct=0,
    )
    weaker_causal = changed(
        baseline,
        "causal.action_result",
        correct=2,
        observed_eligible=4,
        decoded_eligible=4,
    )
    weaker_both = changed(
        weaker_work,
        "causal.action_result",
        correct=0,
        observed_eligible=4,
        decoded_eligible=4,
    )
    eligible = [
        configuration("codex-cli", weaker_work),
        configuration("codex-desktop", weaker_causal),
        configuration("opencode-cli", weaker_both),
    ]
    winners, stable = leaderboard_sensitivity_winners(eligible)
    assert winners["baseline"] == ("codex-desktop",)
    assert winners["equal"] == ("codex-cli",)
    assert not stable


def test_strict_schema_rejects_drift_and_easier_denominators(tmp_path):
    baseline = baseline_document()
    extra = copy.deepcopy(baseline)
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="wrong fields"):
        validate_input(extra)

    missing = copy.deepcopy(baseline)
    missing["metrics"].pop()
    with pytest.raises(ValueError, match="missing required metric"):
        validate_input(missing)

    duplicate = copy.deepcopy(baseline)
    duplicate["metrics"].append(copy.deepcopy(duplicate["metrics"][0]))
    with pytest.raises(ValueError, match="duplicate metric row"):
        validate_input(duplicate)

    not_applicable = changed(baseline, "work.actions", state="not_applicable")
    with pytest.raises(ValueError, match="forbidden"):
        validate_input(not_applicable)

    under_exercised = changed(
        baseline,
        "work.actions",
        correct=3,
        observed_eligible=3,
        decoded_eligible=3,
    )
    with pytest.raises(ValueError, match="protocol minimum"):
        validate_input(under_exercised)

    mismatched_response_population = changed(
        baseline,
        "attribution.usage",
        correct=2,
        observed_eligible=3,
        decoded_eligible=3,
    )
    with pytest.raises(ValueError, match="same observed population"):
        validate_input(mismatched_response_population)

    bad_json = tmp_path / "duplicate.json"
    bad_json.write_text('{"schema_version":"a","schema_version":"b"}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_survival_input(bad_json)


def test_sqlite_loader_is_read_only_and_rejects_schema_drift(tmp_path):
    sqlite_path = tmp_path / "wrong.sqlite"
    connection = sqlite3.connect(sqlite_path)
    connection.execute("CREATE TABLE surprise (value TEXT)")
    connection.commit()
    connection.close()
    before = sqlite_path.read_bytes()
    with pytest.raises(ValueError, match="exactly run and metrics"):
        load_survival_input(sqlite_path)
    assert sqlite_path.read_bytes() == before


def test_configuration_requires_frozen_repetitions_and_unique_run_ids():
    baseline = baseline_document()
    with pytest.raises(ValueError, match="exactly three"):
        aggregate_configuration([as_run(baseline, "a", 1)])
    with pytest.raises(ValueError, match="exactly 1, 2, and 3"):
        aggregate_configuration([as_run(baseline, "a", 1) for _ in range(3)])
    with pytest.raises(ValueError, match="exactly 1, 2, and 3"):
        aggregate_configuration([as_run(baseline, "a", value) for value in (4, 5, 6)])
    reused = []
    for repetition in (1, 2, 3):
        document = copy.deepcopy(baseline)
        document["run_id"] = "reused-run"
        document["configuration_id"] = "a"
        document["repetition"] = repetition
        reused.append(score_run(document))
    with pytest.raises(ValueError, match="run_id values must be unique"):
        aggregate_configuration(reused)
    with pytest.raises(ValueError, match="one configuration"):
        aggregate_configuration(
            [as_run(baseline, "a", 1), as_run(baseline, "b", 2), as_run(baseline, "a", 3)]
        )
