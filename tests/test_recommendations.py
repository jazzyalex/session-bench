from dataclasses import replace

import pytest

from session_bench.recommendations import (
    RECOMMENDATION_SCHEMA_VERSION,
    USE_CASES,
    build_recommendation_outputs,
)
from test_v1_public_score import configuration


IDS = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")


def configurations(*, mutations=None):
    mutations = mutations or {}
    return [configuration(item, mutate=mutations.get(item)) for item in IDS]


def diagnostics(*, lean_id=None, close=False):
    result = {}
    for index, item in enumerate(IDS):
        if lean_id == item:
            sizes = (1000, 1024, 1100)
        elif close:
            sizes = (1000 + index, 1024 + index, 1100 + index)
        else:
            sizes = (2000 + index, 2048 + index, 2200 + index)
        result[item] = [
            {
                "repetition": repetition,
                "marginal_physical_bytes": size,
                "recovered_required_semantic_facts": 10,
            }
            for repetition, size in zip((1, 2, 3), sizes, strict=True)
        ]
    return result


def build(cohort, *, storage=None):
    return build_recommendation_outputs(
        cohort,
        storage_diagnostics=diagnostics(close=True) if storage is None else storage,
    )


def by_use_case(output):
    return {item.use_case: item for item in output.recommendations}


def test_normal_typed_scorer_path_emits_versioned_six_row_contract_with_exact_citations():
    cohort = configurations()
    output = build(cohort)
    assert output.schema_version == RECOMMENDATION_SCHEMA_VERSION
    assert tuple(item.use_case for item in output.recommendations) == USE_CASES
    assert len(output.recommendations) == 6

    rows = by_use_case(output)
    assert rows["audit_ready"].status == "qualified_set"
    assert rows["portable_archives"].status == "qualified_set"
    assert rows["usage_accounting"].status == "qualified_set"
    assert rows["cli_desktop_consistency"].status == "qualified_set"
    assert rows["lean_complete_record"].status == "qualified_set"
    assert rows["long_term_archives"].status == "no_recommendation"

    for row in output.recommendations:
        assert row.rule_version == RECOMMENDATION_SCHEMA_VERSION
        assert row.thresholds
        assert row.objective_name
        assert len(row.cohort_result_ids) == 15
        assert row.metric_ids
        assert row.reason_ids
        if row.status != "no_recommendation":
            assert row.citations and row.observer_ids and row.native_locators
            for citation in row.citations:
                assert citation.surface_id == citation.configuration_id
                assert citation.builds == ("constructed-control",)
                assert citation.collected_on == ("2026-09-11",)
                assert len(citation.result_ids) == 3
                assert len(citation.evaluation_ids) == 3
                assert citation.reproduction_receipt_id == f"receipt-{citation.configuration_id}"
                assert citation.result_ids == tuple(f"format-{citation.configuration_id}-{repetition}" for repetition in (1, 2, 3))
            assert any('"artifact_id"' in locator and '"artifact_sha256"' in locator for locator in row.native_locators)
            assert any('"record_location"' in locator for locator in row.native_locators)

    rendered = output.display()
    assert rendered["schema_version"] == RECOMMENDATION_SCHEMA_VERSION
    assert len(rendered["recommendations"]) == 6
    assert set(rendered) == {"schema_version", "recommendations", "improvements"}


def test_missing_reproduction_receipts_emits_no_recommendation_for_every_use_case():
    cohort = [replace(item, reproduction_receipt=None) for item in configurations()]
    output = build(cohort)
    assert tuple(item.use_case for item in output.recommendations) == USE_CASES
    assert {item.status for item in output.recommendations} == {"no_recommendation"}
    assert all(not item.configuration_ids for item in output.recommendations)
    assert "evidence.no_fully_reproduced_candidate" in by_use_case(output)["audit_ready"].reason_ids


def test_typed_receipt_must_bind_exact_run_result_ids():
    cohort = configurations()
    original = cohort[0]
    assert original.reproduction_receipt is not None
    cohort[0] = replace(
        original,
        reproduction_receipt=replace(
            original.reproduction_receipt,
            result_ids=("format-codex-cli-1", "format-codex-cli-2", "wrong-result"),
        ),
    )
    with pytest.raises(ValueError, match="result IDs do not match its exact runs"):
        build(cohort)


def test_missing_metric_locators_never_produces_a_positive_recommendation():
    cohort = [
        replace(item, runs=tuple(replace(run, metric_evidence={}) for run in item.runs))
        for item in configurations()
    ]
    output = build(cohort)
    assert {item.status for item in output.recommendations} == {"no_recommendation"}
    assert all(not item.observer_ids and not item.native_locators for item in output.recommendations)
    assert all("evidence.metric_locators_incomplete" in item.reason_ids for item in output.recommendations)


def test_unresolved_configuration_is_excluded_and_gets_citable_blocker_fix():
    def unresolved(_survival, profile, repetition):
        if repetition == 2:
            profile["broad_evidence"]["broad.documented_format"]["evidence_complete"] = False

    cohort = configurations(mutations={"claude-desktop": unresolved})
    output = build(cohort)
    assert all("claude-desktop" not in item.configuration_ids for item in output.recommendations)
    consistency = by_use_case(output)["cli_desktop_consistency"]
    assert consistency.status == "recommended"
    assert consistency.configuration_ids == ("codex-cli", "codex-desktop")

    repairs = next(item for item in output.improvements if item.configuration_id == "claude-desktop")
    blocker = repairs.items[0]
    assert blocker.metric_id == "broad.documented_format"
    assert blocker.state == "blocked"
    assert blocker.repetitions == (2,)
    assert blocker.evidence_states == ("measured", "unresolved", "measured")
    assert blocker.observer_ids and blocker.native_locators
    assert "Document containers" in blocker.acceptance_condition
    assert "cannot receive a complete public score" in blocker.observed_consequence


def test_missing_cli_desktop_pairs_only_block_consistency_recommendation():
    def unresolved(_survival, profile, repetition):
        if repetition == 1:
            profile["broad_evidence"]["broad.stable_root_location"]["evidence_complete"] = False

    cohort = configurations(mutations={"codex-desktop": unresolved, "claude-desktop": unresolved})
    output = build(cohort)
    rows = by_use_case(output)

    assert rows["cli_desktop_consistency"].status == "no_recommendation"
    assert rows["cli_desktop_consistency"].reason_ids == ("qualification.no_pair",)
    assert rows["cli_desktop_consistency"].configuration_ids == ()

    for use_case in ("audit_ready", "portable_archives", "usage_accounting", "lean_complete_record"):
        assert rows[use_case].status == "qualified_set"
        assert set(rows[use_case].configuration_ids) == {"codex-cli", "claude-cli", "opencode-cli"}
        assert len(rows[use_case].cohort_result_ids) == 9
        assert rows[use_case].observer_ids and rows[use_case].native_locators


def test_improvement_order_is_blockers_then_lowest_category_points_then_metric_id():
    def failures(survival, profile, repetition):
        if repetition == 1:
            profile["broad_evidence"]["broad.documented_format"]["evidence_complete"] = False
            next(row for row in survival["metrics"] if row["id"] == "causal.action_result").update(state="contradiction", correct=0)
            next(row for row in survival["metrics"] if row["id"] == "work.actions").update(state="contradiction", correct=0)
            next(row for row in survival["metrics"] if row["id"] == "work.results").update(state="contradiction", correct=0)

    cohort = configurations(mutations={"opencode-cli": failures})
    output = build(cohort)
    repairs = next(item for item in output.improvements if item.configuration_id == "opencode-cli")
    assert len(repairs.items) == 3
    assert repairs.items[0].metric_id == "broad.documented_format"
    assert repairs.items[0].state == "blocked"
    assert all(item.observed_consequence and item.acceptance_condition for item in repairs.items)
    assert all(item.observer_ids and item.native_locators for item in repairs.items)


def test_lean_rule_uses_three_run_median_kib_per_fact_and_ten_percent_margin():
    cohort = configurations()
    output = build(cohort, storage=diagnostics(lean_id="codex-cli"))
    lean = by_use_case(output)["lean_complete_record"]
    assert lean.status == "recommended"
    assert lean.configuration_ids == ("codex-cli",)
    assert lean.objective_name == "median_marginal_physical_kib_per_recovered_required_semantic_fact"
    assert lean.objective_values["codex-cli"] == "1/10"
    assert "selection.exclusive_ten_percent_advantage" in lean.reason_ids


def test_lean_rule_returns_qualified_set_when_no_row_has_ten_percent_advantage():
    cohort = configurations()
    output = build(cohort, storage=diagnostics(close=True))
    lean = by_use_case(output)["lean_complete_record"]
    assert lean.status == "qualified_set"
    assert len(lean.configuration_ids) > 1
    assert lean.reason_ids == ("selection.no_ten_percent_advantage",)


def test_lean_rule_requires_density_floor_in_every_run():
    def low_density(_survival, profile, repetition):
        if repetition == 3:
            records = profile["broad_evidence"]["broad.classified_content_density"]["records"]
            records[0]["logical_bytes"] = 94
            records[1].update(logical_bytes=6, record_kind="unknown", classification="unknown")

    cohort = configurations(mutations={"codex-cli": low_density})
    output = build(cohort, storage=diagnostics(lean_id="codex-cli"))
    assert "codex-cli" not in by_use_case(output)["lean_complete_record"].configuration_ids


def test_storage_diagnostics_require_exact_named_three_run_population():
    cohort = configurations()
    invalid = diagnostics()
    invalid["opencode-cli"] = invalid["opencode-cli"][:2]
    with pytest.raises(ValueError, match="exactly three"):
        build(cohort, storage=invalid)

    invalid = diagnostics()
    invalid["opencode-cli"][2] = {
        "repetition": 3,
        "marginal_physical_bytes": -1,
        "recovered_required_semantic_facts": 10,
    }
    with pytest.raises(ValueError, match="nonnegative marginal physical bytes"):
        build(cohort, storage=invalid)


def test_long_term_rule_uses_two_builds_and_thirty_day_window():
    cohort = []
    for configuration_id in IDS:
        original = configuration(configuration_id)
        dates = ("2026-01-01", "2026-01-31", "2026-02-01")
        builds = ("build-a", "build-b", "build-b")
        runs = tuple(
            replace(run, build=build, collected_on=collected_on)
            for run, build, collected_on in zip(original.runs, builds, dates, strict=True)
        )
        cohort.append(replace(original, runs=runs))
    output = build(cohort)
    long_term = by_use_case(output)["long_term_archives"]
    assert long_term.status == "qualified_set"
    assert set(long_term.configuration_ids) == set(IDS)
    assert all(citation.builds == ("build-a", "build-b") for citation in long_term.citations)


def test_incomplete_attempted_surface_set_still_emits_all_six_negative_rows():
    output = build_recommendation_outputs(configurations()[:-1])
    assert tuple(item.use_case for item in output.recommendations) == USE_CASES
    assert {item.status for item in output.recommendations} == {"no_recommendation"}
    assert all(item.reason_ids == ("cohort.attempted_surface_set_incomplete",) for item in output.recommendations)
    assert all(item.thresholds and item.objective_name != "unavailable" and item.metric_ids for item in output.recommendations)
    assert output.improvements == ()
