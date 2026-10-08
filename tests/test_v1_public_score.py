import copy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from session_bench.survival_metrics import load_survival_input
from session_bench.v1_public_score import (
    CLASSIFIED_CONTENT_DENSITY_RULE,
    CONFIGURATION_EVIDENCE_N1_SCHEMA_VERSION,
    CONFIGURATION_EVIDENCE_SCHEMA_VERSION,
    FORMAT_METRICS,
    PUBLIC_CATEGORY_POINTS,
    PUBLIC_METRICS,
    PUBLIC_WEIGHT_VECTORS,
    aggregate_public_configuration,
    aggregate_public_configuration_n1,
    attempted_public_cohort,
    format_evidence_control,
    format_profile_document,
    public_leaderboard_ranks,
    public_sensitivity_winners,
    qualified_public_cohort,
    score_public_run,
    score_public_control_run,
    survival_evidence_control,
    TARGET_CONFIGURATIONS,
    validate_format_profile,
    weighted_public_total,
)


ROOT = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1"


def survival(configuration_id="codex-cli", repetition=1):
    value = load_survival_input(ROOT / "equivalent-jsonl/input.jsonl")
    value.update(run_id=f"{configuration_id}-{repetition}", configuration_id=configuration_id, repetition=repetition)
    return value


def profile(configuration_id="codex-cli", repetition=1):
    return format_profile_document(run_id=f"{configuration_id}-{repetition}", configuration_id=configuration_id, repetition=repetition)


def configuration(configuration_id, *, mutate=None):
    runs = []
    surface = "desktop" if configuration_id.endswith("-desktop") else "cli"
    for repetition in (1, 2, 3):
        document, format_document = survival(configuration_id, repetition), profile(configuration_id, repetition)
        if mutate:
            mutate(document, format_document, repetition)
        runs.append(score_public_run(
            survival_evidence_control(document, evaluation_id=f"survival-{configuration_id}-{repetition}", identity={
                "provider": configuration_id.split("-")[0], "harness": configuration_id,
                "surface": surface, "execution_mode": "constructed-control",
                "os": "macOS-test", "build": "constructed-control", "model": "synthetic-model",
                "configuration": "synthetic-default", "observer_schema_version": "1.0-survival-observer",
            }),
            format_evidence_control(format_document, result_id=f"format-{configuration_id}-{repetition}"),
        ))
    result_ids = [f"format-{configuration_id}-{repetition}" for repetition in (1, 2, 3)]
    return aggregate_public_configuration(runs, configuration_evidence={
        "schema_version": CONFIGURATION_EVIDENCE_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "bundle": {"id": f"bundle-{configuration_id}", "sha256": "f" * 64, "immutable": True, "public": True, "result_ids": result_ids},
        "reproduction_receipt": {"id": f"receipt-{configuration_id}", "sha256": "e" * 64, "bundle_sha256": "f" * 64, "offline_recomputed": True, "verified": True, "result_ids": result_ids},
    })


def configuration_evidence(configuration_id, result_ids):
    return {
        "schema_version": CONFIGURATION_EVIDENCE_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "bundle": {
            "id": f"bundle-{configuration_id}", "sha256": "f" * 64,
            "immutable": True, "public": True, "result_ids": list(result_ids),
        },
        "reproduction_receipt": {
            "id": f"receipt-{configuration_id}", "sha256": "e" * 64,
            "bundle_sha256": "f" * 64, "offline_recomputed": True, "verified": True,
            "result_ids": list(result_ids),
        },
    }


def configuration_evidence_n1(configuration_id, result_id):
    return {
        "schema_version": CONFIGURATION_EVIDENCE_N1_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "bundle": {
            "id": f"bundle-n1-{configuration_id}", "sha256": "c" * 64,
            "immutable": True, "public": True, "result_ids": [result_id],
        },
        "reproduction_receipt": {
            "id": f"receipt-n1-{configuration_id}", "sha256": "d" * 64,
            "bundle_sha256": "c" * 64, "offline_recomputed": True,
            "verified": True, "result_ids": [result_id],
        },
    }


def test_public_mapping_is_exact_and_format_material():
    assert TARGET_CONFIGURATIONS == (
        "codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli",
    )
    assert list(PUBLIC_CATEGORY_POINTS) == ["record_fidelity", "causality_context", "usage_attribution", "portability_openness", "durability_signal"]
    assert sum(PUBLIC_CATEGORY_POINTS.values(), Fraction()) == 100
    assert sum((metric.points for metric in PUBLIC_METRICS.values()), Fraction()) == 100
    assert len(FORMAT_METRICS) == 12
    assert sum((PUBLIC_METRICS[metric].points for metric in FORMAT_METRICS), Fraction()) == 30
    assert all(sum(vector.values()) == 100 for vector in PUBLIC_WEIGHT_VECTORS.values())
    assert {metric.category for metric in PUBLIC_METRICS.values()} == set(PUBLIC_CATEGORY_POINTS)
    assert {metric_id: (spec.category, spec.points) for metric_id, spec in PUBLIC_METRICS.items()} == {
        "work.submitted_turns": ("record_fidelity", 4), "work.visible_responses": ("record_fidelity", 4),
        "work.actions": ("record_fidelity", 5), "work.results": ("record_fidelity", 5),
        "work.changed_files": ("record_fidelity", 4), "revision.r1": ("record_fidelity", 2),
        "revision.r2": ("record_fidelity", 2), "revision.r1_r2_order": ("record_fidelity", 2),
        "revision.final_after_r2": ("record_fidelity", 2), "causal.action_result": ("causality_context", 7),
        "causal.turn_response": ("causality_context", 6), "attribution.model_config": ("usage_attribution", 3),
        "attribution.usage": ("usage_attribution", 5), "attribution.token_semantics": ("usage_attribution", 4),
        "attribution.reconciliation": ("usage_attribution", 3), "portable.complete_root": ("portability_openness", 3),
        "portable.companions": ("portability_openness", 2), "portable.isolated_decode": ("portability_openness", 4),
        "portable.canonical_equality": ("portability_openness", 3), "broad.readable_rationale": ("causality_context", 4),
        "broad.thread_structure": ("causality_context", 3), "broad.standard_tools_readable": ("portability_openness", 2),
        "broad.documented_format": ("portability_openness", 2), "broad.self_contained_identity": ("portability_openness", 2),
        "broad.declared_format_version": ("portability_openness", 2), "broad.event_timestamps": ("durability_signal", 2),
        "broad.honest_version_signal": ("durability_signal", 2), "broad.observed_schema_stability": ("durability_signal", 3),
        "broad.stable_root_location": ("durability_signal", 2), "broad.naive_reader_duplicate_safety": ("durability_signal", 3),
        "broad.classified_content_density": ("durability_signal", 3),
    }


def test_complete_constructed_profile_yields_100_without_changing_survival_score():
    public = score_public_control_run(survival(), profile())
    assert public.survival_run.overall == 100
    assert public.overall == 100
    assert public.portable_gate and public.rankable
    assert all(category.score == PUBLIC_CATEGORY_POINTS[name] for name, category in public.categories.items())


def test_reportable_run_retains_normalized_metric_proofs_and_canonical_states():
    survival_document = survival_evidence_control(survival(), evaluation_id="survival-evaluation")
    format_document = format_evidence_control(profile(), result_id="format-result")
    public = score_public_run(survival_document, format_document)

    assert set(public.metric_evidence) == set(PUBLIC_METRICS)
    survival_proof = public.metric_evidence["work.actions"]
    assert survival_proof.state == "measured"
    assert survival_proof.observer_ids == ("constructed-survival-observer-3",)
    assert survival_proof.native_locators[0].artifact_id == "constructed-survival-native-3"
    assert survival_proof.native_locators[0].artifact_sha256 == f"{3:064x}"
    assert survival_proof.native_locators[0].record_location == "record:3"

    format_proof = public.metric_evidence["broad.documented_format"]
    assert format_proof.state == "measured"
    assert format_proof.observer_ids == ("constructed-observer-4",)
    assert format_proof.native_locators[0].artifact_id == "constructed-native-4"
    assert format_proof.native_locators[0].artifact_sha256 == f"{4:064x}"
    assert format_proof.native_locators[0].record_location is None
    assert public.display()["metric_evidence"]["work.actions"]["state"] == "measured"


def test_format_profile_missing_evidence_exposes_category_range_but_no_total():
    document = profile()
    document["broad_evidence"]["broad.observed_schema_stability"]["evidence_complete"] = False
    public = score_public_control_run(survival(), document)
    category = public.categories["durability_signal"]
    assert public.overall is None and not public.rankable
    assert category.score is None
    assert category.known_points == 12
    assert category.possible_min == 12
    assert category.possible_max == 15
    assert "broad.observed_schema_stability:unresolved" in public.blockers


def test_profile_rejects_missing_duplicate_and_mismatched_identity():
    document = profile()
    document["broad_evidence"].pop("broad.readable_rationale")
    with pytest.raises(ValueError, match="every required broad"):
        validate_format_profile(document)
    with pytest.raises(ValueError, match="identities"):
        score_public_run(survival_evidence_control(survival()), format_evidence_control(profile("claude-cli")))
    with pytest.raises(ValueError, match="survival evidence"):
        score_public_run(survival(), profile())


def test_historical_cursor_evidence_cannot_enter_the_v1_public_scorer():
    with pytest.raises(ValueError, match="outside the v1 release cohort"):
        score_public_run(
            survival_evidence_control(survival("cursor-cli")),
            format_evidence_control(profile("cursor-cli")),
        )


def test_broad_rows_are_derived_from_metric_specific_evidence_not_counters():
    document = profile()
    document["broad_evidence"]["broad.event_timestamps"]["records"][1]["timestamp"] = "not-a-timestamp"
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.event_timestamps"] == Fraction(1, 2)

    document = profile()
    document["broad_evidence"]["broad.classified_content_density"]["records"].append(
        {"record_id": "three", "record_kind": "unknown", "logical_bytes": 100, "classification": "unknown"}
    )
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.classified_content_density"] == Fraction(1, 2)

    generic = {"schema_version": "session-bench-format-profile-v1", "run_id": "x", "configuration_id": "codex-cli", "repetition": 1, "metrics": []}
    with pytest.raises(ValueError, match="broad_evidence"):
        validate_format_profile(generic)


@pytest.mark.parametrize(
    ("metric_id", "mutate", "expected"),
    [
        ("broad.readable_rationale", lambda d: d["records"][1].update(ordered_text=""), Fraction(1, 2)),
        ("broad.thread_structure", lambda d: d.update(session_id=""), Fraction(0)),
        ("broad.standard_tools_readable", lambda d: d.update(vendor_binary_required=True), Fraction(0)),
        ("broad.documented_format", lambda d: d["mapping"].update(joins=""), Fraction(0)),
        ("broad.self_contained_identity", lambda d: d.update(external_lookup_required=True), Fraction(0)),
        ("broad.declared_format_version", lambda d: d.update(machine_readable=False), Fraction(0)),
        ("broad.event_timestamps", lambda d: d["records"][1].update(timestamp="invalid"), Fraction(1, 2)),
        ("broad.honest_version_signal", lambda d: d.update(matches_decoder_contract=False), Fraction(0)),
        ("broad.observed_schema_stability", lambda d: d.update(exceptions=["decode error"]), Fraction(0)),
        ("broad.stable_root_location", lambda d: d["repetitions"][2].update(isolated_discovery=False), Fraction(0)),
        ("broad.naive_reader_duplicate_safety", lambda d: d["forward_records"].append({"event_id": "event-1", "occurrence_id": "duplicate", "state": "active"}), Fraction(1, 3)),
        ("broad.classified_content_density", lambda d: d["records"].append({"record_id": "unknown", "record_kind": "unknown", "logical_bytes": 100, "classification": "unknown"}), Fraction(1, 2)),
    ],
)
def test_every_broad_validator_derives_a_negative_or_partial_value(metric_id, mutate, expected):
    baseline = score_public_control_run(survival(), profile())
    document = profile()
    mutate(document["broad_evidence"][metric_id])
    scored = score_public_control_run(survival(), document)
    assert scored.metrics[metric_id] == expected
    assert scored.metrics[metric_id] < 1
    assert {key: value for key, value in scored.metrics.items() if key != metric_id} == {
        key: value for key, value in baseline.metrics.items() if key != metric_id
    }


def test_stable_root_accepts_metadata_safe_normal_root_discovery():
    document = profile()
    document["broad_evidence"]["broad.stable_root_location"]["repetitions"] = [
        {
            "repetition": number,
            "root_locator": "CODEX_HOME/sessions/<session-id>",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }
        for number in (1, 2, 3)
    ]

    scored = score_public_control_run(survival(), document)

    assert scored.metrics["broad.stable_root_location"] == 1


def test_stable_root_single_run_can_score_eval_three():
    document = profile(repetition=3)
    document["broad_evidence"]["broad.stable_root_location"]["repetitions"] = [
        {"repetition": 3, "root_locator": "CLAUDE_HOME/projects/<session-id>",
         "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False}
    ]

    scored = score_public_control_run(survival(repetition=3), document)

    assert len(scored.metrics) == 31
    assert scored.metrics["broad.stable_root_location"] == 1
    assert scored.overall == 100


def test_stable_root_multi_run_discovery_mode_mismatch_fails():
    document = profile()
    root = document["broad_evidence"]["broad.stable_root_location"]
    root["repetitions"] = [
        {"repetition": 1, "root_locator": "root/session-1", "isolated_discovery": True, "personal_history_scanned": False},
        {"repetition": 2, "root_locator": "root/session-2", "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False},
    ]
    assert score_public_control_run(survival(), document).metrics["broad.stable_root_location"] == 0


def test_stable_root_run_specific_session_paths_remain_valid():
    document = profile()
    rows = document["broad_evidence"]["broad.stable_root_location"]["repetitions"]
    for row in rows:
        row["root_locator"] = f"isolated/opencode-cli-eval-{row['repetition']}/database"
    assert score_public_control_run(survival(), document).metrics["broad.stable_root_location"] == 1


def test_stable_root_single_run_personal_history_scan_fails():
    document = profile(repetition=3)
    document["broad_evidence"]["broad.stable_root_location"]["repetitions"] = [
        {"repetition": 3, "root_locator": "selected/file", "discovery_mode": "metadata_safe_normal_root",
         "personal_history_scanned": True}
    ]
    assert score_public_control_run(survival(repetition=3), document).metrics["broad.stable_root_location"] == 0


@pytest.mark.parametrize("repetitions", [
    [],
    [{"repetition": 1, "root_locator": "root", "isolated_discovery": True, "personal_history_scanned": False}] * 2,
    [{"repetition": number, "root_locator": "root", "isolated_discovery": True, "personal_history_scanned": False} for number in (1, 3)],
    [{"repetition": number, "root_locator": "root", "isolated_discovery": True, "personal_history_scanned": False} for number in (2, 1)],
    [{"repetition": True, "root_locator": "root", "isolated_discovery": True, "personal_history_scanned": False}],
])
def test_stable_root_rejects_malformed_repetition_populations(repetitions):
    document = profile()
    document["broad_evidence"]["broad.stable_root_location"]["repetitions"] = repetitions
    with pytest.raises(ValueError, match="repetition population|repetitions must|malformed repetition"):
        validate_format_profile(document)


@pytest.mark.parametrize("metric_id", FORMAT_METRICS)
def test_every_broad_validator_derives_unresolved_from_incomplete_evidence(metric_id):
    baseline = score_public_control_run(survival(), profile())
    document = profile()
    document["broad_evidence"][metric_id]["evidence_complete"] = False
    scored = score_public_control_run(survival(), document)
    assert scored.metrics[metric_id] is None
    assert {key: value for key, value in scored.metrics.items() if key != metric_id} == {
        key: value for key, value in baseline.metrics.items() if key != metric_id
    }


@pytest.mark.parametrize("metric_id", ["broad.readable_rationale", "broad.event_timestamps"])
@pytest.mark.parametrize(
    ("record_indexes", "expected"),
    [([0, 0], Fraction(1, 2)), ([0, 0, 0], Fraction(1, 3)), ([0, 1, 0], Fraction(2, 3))],
)
def test_broad_population_credits_distinct_events_and_penalizes_duplicates(metric_id, record_indexes, expected):
    document = profile()
    detail = document["broad_evidence"][metric_id]
    original_records = detail["records"]
    detail["records"] = [copy.deepcopy(original_records[index]) for index in record_indexes]

    scored = score_public_control_run(survival(), document)
    row = next(row for row in validate_format_profile(document)["metrics"] if row["id"] == metric_id)

    assert row["correct"] == len(set(record_indexes))
    assert row["observed_eligible"] == 2
    assert row["decoded_eligible"] == len(record_indexes)
    assert scored.metrics[metric_id] == expected
    assert scored.overall == 100 - PUBLIC_METRICS[metric_id].points * (1 - expected)


@pytest.mark.parametrize("metric_id", ["broad.readable_rationale", "broad.event_timestamps"])
def test_broad_population_dangling_or_invalid_records_cannot_replace_missing_events(metric_id):
    document = profile()
    detail = document["broad_evidence"][metric_id]
    dangling = copy.deepcopy(detail["records"][0])
    dangling["id"] = "unexpected-event"
    detail["records"] = [detail["records"][0], dangling, {"id": []}]

    scored = score_public_control_run(survival(), document)

    assert scored.metrics[metric_id] == Fraction(1, 3)


@pytest.mark.parametrize(
    ("metric_id", "ids_key", "records_key"),
    [
        ("broad.readable_rationale", "response_ids", "records"),
        ("broad.event_timestamps", "event_ids", "records"),
        ("broad.naive_reader_duplicate_safety", "event_ids", "forward_records"),
    ],
)
def test_empty_incomplete_broad_populations_are_unresolved(metric_id, ids_key, records_key):
    document = profile()
    document["broad_evidence"][metric_id].update(evidence_complete=False, **{ids_key: [], records_key: []})

    scored = score_public_control_run(survival(), document)

    assert scored.metrics[metric_id] is None
    assert scored.metric_evidence[metric_id].state == "unresolved"
    assert scored.overall is None and not scored.rankable
    document["broad_evidence"][metric_id]["evidence_complete"] = True
    with pytest.raises(ValueError, match="non-empty unique identifier list"):
        validate_format_profile(document)


@pytest.mark.parametrize("records", [[], [{"record_id": "one", "record_kind": "assistant_message", "logical_bytes": 0, "classification": "useful"}]])
def test_empty_or_zero_byte_incomplete_density_is_unresolved(records):
    document = profile()
    detail = document["broad_evidence"]["broad.classified_content_density"]
    detail.update(evidence_complete=False, records=records)

    scored = score_public_control_run(survival(), document)

    assert scored.metrics["broad.classified_content_density"] is None
    assert scored.overall is None and not scored.rankable
    detail["evidence_complete"] = True
    with pytest.raises(ValueError, match="non-empty|positive"):
        validate_format_profile(document)


@pytest.mark.parametrize(
    ("metric_id", "mutate", "message"),
    [
        ("broad.readable_rationale", lambda detail: detail.update(response_ids=["duplicate", "duplicate"]), "unique identifier list"),
        ("broad.event_timestamps", lambda detail: detail.update(records={}), "must be an array"),
        ("broad.naive_reader_duplicate_safety", lambda detail: detail.update(deduplication={}), "documented deduplication"),
        ("broad.classified_content_density", lambda detail: detail["records"][0].update(classification="unknown"), "invalid logical-byte record"),
    ],
)
def test_incomplete_broad_evidence_still_rejects_malformed_provided_data(metric_id, mutate, message):
    document = profile()
    detail = document["broad_evidence"][metric_id]
    detail["evidence_complete"] = False
    mutate(detail)

    with pytest.raises(ValueError, match=message):
        validate_format_profile(document)


@pytest.mark.parametrize("dependency", ["vendor_binary_required", "account_required", "backend_required", "network_required"])
def test_standard_tool_access_dependencies_are_measured_failures(dependency):
    document = profile()
    document["broad_evidence"]["broad.standard_tools_readable"][dependency] = True

    scored = score_public_control_run(survival(), document)

    assert scored.metrics["broad.standard_tools_readable"] == 0
    assert scored.metric_evidence["broad.standard_tools_readable"].state == "measured"
    assert scored.overall == 98


def test_thread_structure_accepts_deterministic_linear_parent_recovery():
    document = profile()
    structure = document["broad_evidence"]["broad.thread_structure"]
    structure["explicit_parentage"] = False
    structure["turns"] = [
        {"id": "turn-1", "role": "user", "ordinal": 1, "parent_id": None},
        {"id": "turn-2", "role": "assistant", "ordinal": 2, "parent_id": None},
        {"id": "turn-3", "role": "tool", "ordinal": 3, "parent_id": None},
    ]
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.thread_structure"] == 1


def test_thread_structure_rejects_ambiguous_linear_recovery():
    document = profile()
    structure = document["broad_evidence"]["broad.thread_structure"]
    structure["explicit_parentage"] = False
    structure["turns"][0]["role"] = "assistant"
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.thread_structure"] == 0


@pytest.mark.parametrize(
    ("unit", "timestamp"),
    [("unix_s", 1_757_548_800), ("unix_ms", 1_757_548_800_000)],
)
def test_event_timestamps_validate_numeric_unix_values(unit, timestamp):
    document = profile()
    records = document["broad_evidence"]["broad.event_timestamps"]["records"]
    records[0].update(timestamp=timestamp, unit=unit)
    records[1].update(timestamp=timestamp + (1 if unit == "unix_s" else 1_000), unit=unit)
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.event_timestamps"] == 1


def test_event_timestamps_reject_numeric_looking_string_for_unix_unit():
    document = profile()
    record = document["broad_evidence"]["broad.event_timestamps"]["records"][0]
    record.update(timestamp="1757548800", unit="unix_s")
    scored = score_public_control_run(survival(), document)
    assert scored.metrics["broad.event_timestamps"] == Fraction(1, 2)


def test_schema_compatibility_metric_is_explicitly_a_captured_window_check():
    document = profile()
    evidence = document["broad_evidence"]["broad.observed_schema_stability"]
    assert len(evidence["observations"]) == 1
    row = next(row for row in validate_format_profile(document)["metrics"] if row["id"] == "broad.observed_schema_stability")
    assert row["state"] == "measured"


def test_content_density_requires_frozen_role_classifier_and_matches_derived_class():
    document = profile()
    evidence = document["broad_evidence"]["broad.classified_content_density"]
    assert evidence["classification_rule"] == CLASSIFIED_CONTENT_DENSITY_RULE
    evidence["records"][0]["classification"] = "unknown"
    with pytest.raises(ValueError, match="invalid logical-byte record"):
        validate_format_profile(document)


def test_content_density_rejects_unlisted_record_role():
    document = profile()
    evidence = document["broad_evidence"]["broad.classified_content_density"]
    evidence["records"][0]["record_kind"] = "arbitrary_payload"
    with pytest.raises(ValueError, match="invalid logical-byte record"):
        validate_format_profile(document)


def test_generic_metric_counts_cannot_be_smuggled_into_broad_evidence():
    document = profile()
    document["broad_evidence"]["broad.event_timestamps"].update(
        correct=99, observed_eligible=99, decoded_eligible=99
    )
    with pytest.raises(ValueError, match="wrong fields"):
        validate_format_profile(document)


def test_aggregate_requires_three_exact_repetitions_and_preserves_range():
    good = configuration("codex-cli")
    assert good.overall == 100 and good.overall_range == (100, 100)
    with pytest.raises(ValueError, match="exactly three"):
        aggregate_public_configuration(good.runs[:2])


def test_n1_complete_repetition_one_with_singleton_evidence_is_rankable_without_ranges():
    run = configuration("codex-cli").runs[0]
    evidence = configuration_evidence_n1("codex-cli", run.result_id)

    score = aggregate_public_configuration_n1(run, configuration_evidence=evidence)

    assert score.runs == (run,)
    assert score.rankable and score.verification == "fully_reproduced"
    assert score.overall == run.overall == 100
    assert score.categories == {name: row.score for name, row in run.categories.items()}
    assert score.overall_range is None
    assert all(value is None for value in score.category_ranges.values())
    assert score.bundle is not None and score.bundle.result_ids == (run.result_id,)
    assert score.reproduction_receipt is not None
    assert score.reproduction_receipt.result_ids == (run.result_id,)


def test_n1_rejects_nonprospective_repetition_two():
    run = configuration("codex-cli").runs[1]
    with pytest.raises(ValueError, match="prospectively designated repetition 1"):
        aggregate_public_configuration_n1(
            run,
            configuration_evidence=configuration_evidence_n1(
                "codex-cli", run.result_id
            ),
        )


def test_n1_unresolved_metric_cannot_rank():
    format_document = profile("codex-cli", 1)
    format_document["broad_evidence"]["broad.event_timestamps"]["evidence_complete"] = False
    run = score_public_run(
        survival_evidence_control(
            survival("codex-cli", 1),
            evaluation_id="n1-unresolved",
            identity={
                "provider": "codex", "harness": "codex-cli", "surface": "cli",
                "execution_mode": "constructed-control", "os": "macOS-test",
                "build": "constructed-control", "model": "synthetic-model",
                "configuration": "synthetic-default",
                "observer_schema_version": "1.0-survival-observer",
            },
        ),
        format_evidence_control(format_document, result_id="n1-unresolved-result"),
    )

    score = aggregate_public_configuration_n1(
        run,
        configuration_evidence=configuration_evidence_n1(
            "codex-cli", "n1-unresolved-result"
        ),
    )

    assert not score.rankable and score.overall is None
    assert score.verification == "partially_verified"
    assert any("broad.event_timestamps:unresolved" in item for item in score.blockers)


def test_n1_missing_identity_or_singleton_evidence_cannot_rank():
    run = configuration("codex-cli").runs[0]

    missing = aggregate_public_configuration_n1(run)
    assert not missing.rankable and missing.overall is None
    assert "n=1 configuration evidence missing" in missing.blockers

    unidentified = aggregate_public_configuration_n1(
        replace(run, identity=None),
        configuration_evidence=configuration_evidence_n1("codex-cli", run.result_id),
    )
    assert not unidentified.rankable and unidentified.overall is None
    assert "repetition 1: public identity envelope missing" in unidentified.blockers

    malformed = configuration_evidence_n1("codex-cli", run.result_id)
    malformed["bundle"]["result_ids"].append("another-result")
    with pytest.raises(ValueError, match="exactly one public result ID"):
        aggregate_public_configuration_n1(run, configuration_evidence=malformed)


def test_configuration_needs_immutable_bundle_and_independent_receipt_for_rankability():
    runs = tuple(score_public_run(
        survival_evidence_control(survival("codex-cli", repetition), evaluation_id=f"s-{repetition}"),
        format_evidence_control(profile("codex-cli", repetition), result_id=f"r-{repetition}"),
    ) for repetition in (1, 2, 3))
    partial = aggregate_public_configuration(runs)
    assert partial.verification == "partially_verified"
    assert not partial.rankable and partial.overall is None

    with pytest.raises(ValueError, match="exact three public result IDs"):
        aggregate_public_configuration(runs, configuration_evidence={
            "schema_version": CONFIGURATION_EVIDENCE_SCHEMA_VERSION, "configuration_id": "codex-cli",
            "bundle": {"id": "bundle", "sha256": "a" * 64, "immutable": True, "public": True, "result_ids": ["r-1"]},
            "reproduction_receipt": {"id": "receipt", "sha256": "b" * 64, "bundle_sha256": "a" * 64, "offline_recomputed": True, "verified": True, "result_ids": ["r-1"]},
        })


def test_configuration_retains_full_immutable_bundle_and_receipt_binding_and_rejects_tampering():
    runs = tuple(score_public_run(
        survival_evidence_control(survival("codex-cli", repetition), evaluation_id=f"s-{repetition}"),
        format_evidence_control(profile("codex-cli", repetition), result_id=f"r-{repetition}"),
    ) for repetition in (1, 2, 3))
    evidence = configuration_evidence("codex-cli", ["r-1", "r-2", "r-3"])
    score = aggregate_public_configuration(runs, configuration_evidence=evidence)

    assert score.bundle is not None
    assert score.bundle.id == "bundle-codex-cli"
    assert score.bundle.sha256 == "f" * 64
    assert score.bundle.immutable and score.bundle.public
    assert score.bundle.result_ids == ("r-1", "r-2", "r-3")
    assert score.reproduction_receipt is not None
    assert score.reproduction_receipt.id == "receipt-codex-cli"
    assert score.reproduction_receipt.sha256 == "e" * 64
    assert score.reproduction_receipt.bundle_sha256 == score.bundle.sha256
    assert score.reproduction_receipt.offline_recomputed and score.reproduction_receipt.verified
    assert score.reproduction_receipt.result_ids == score.bundle.result_ids
    assert score.bundle_id == score.bundle.id
    assert score.reproduction_receipt_id == score.reproduction_receipt.id

    tampered = copy.deepcopy(evidence)
    tampered["bundle"]["immutable"] = False
    with pytest.raises(ValueError, match="immutably bind"):
        aggregate_public_configuration(runs, configuration_evidence=tampered)

    tampered = copy.deepcopy(evidence)
    tampered["reproduction_receipt"]["bundle_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="deterministically recompute"):
        aggregate_public_configuration(runs, configuration_evidence=tampered)

    tampered = copy.deepcopy(evidence)
    tampered["reproduction_receipt"]["result_ids"] = ["r-1", "r-2", "other"]
    with pytest.raises(ValueError, match="deterministically recompute"):
        aggregate_public_configuration(runs, configuration_evidence=tampered)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("model", None, "public identity model missing"),
        ("model", "model-unreported", "public identity model missing"),
        ("model", "different-model", "public identity model differs across repetitions"),
        ("surface", "desktop", "public identity surface mismatch"),
    ],
)
def test_resolved_three_run_result_cannot_rank_with_missing_or_mixed_identity(field, value, reason):
    good = configuration("codex-cli")
    runs = list(good.runs)
    assert runs[1].identity is not None
    runs[1] = replace(runs[1], identity=replace(runs[1].identity, **{field: value}))
    result = aggregate_public_configuration(
        runs,
        configuration_evidence=configuration_evidence(
            "codex-cli", [run.result_id for run in runs]
        ),
    )
    assert result.verification == "partially_verified"
    assert not result.rankable and result.overall is None
    assert any(reason in blocker for blocker in result.blockers)
    assert all(run.overall is not None for run in result.runs)


def test_resolved_portability_loss_scores_zero_but_does_not_block_rankability():
    def portability_loss(document, _profile, repetition):
        if repetition == 1:
            row = next(row for row in document["metrics"] if row["id"] == "portable.isolated_decode")
            row.update(state="native_absent", correct=0)

    result = configuration("codex-cli", mutate=portability_loss)
    assert result.categories["portability_openness"] == Fraction(56, 3)
    assert not result.portable_gate
    assert result.rankable and result.overall is not None
    run = result.runs[0]
    assert run.metric_evidence["portable.isolated_decode"].state == "native_absent"
    assert run.display()["metric_evidence"]["portable.isolated_decode"]["state"] == "native_absent"


def test_public_leaderboard_requires_exact_five_surface_cohort_and_pair():
    ids = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")
    cohort = [configuration(item) for item in ids]
    assert qualified_public_cohort(cohort) is not None
    assert public_leaderboard_ranks(cohort) == {item: 1 for item in ids}
    assert public_leaderboard_ranks(cohort[:-1]) == {}

    def unresolved(_survival, format_document, repetition):
        if repetition == 1:
            format_document["broad_evidence"]["broad.stable_root_location"]["evidence_complete"] = False

    broken = [configuration(item) for item in ids[:-1]] + [configuration("opencode-cli", mutate=unresolved)]
    assert len(attempted_public_cohort(broken)) == 5
    assert qualified_public_cohort(broken) is None
    assert public_leaderboard_ranks(broken) == {}


def test_public_leaderboard_ties_values_that_publish_to_same_decimal():
    ids = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")
    cohort = [configuration(item) for item in ids]
    baselines = {
        "codex-cli": Fraction(86963, 1000),
        "codex-desktop": Fraction(87021, 1000),
        "claude-cli": Fraction(81),
        "claude-desktop": Fraction(8275, 100),
        "opencode-cli": Fraction(8325, 100),
    }
    cohort = [
        replace(item, sensitivity={**item.sensitivity, "baseline": baselines[item.configuration_id]})
        for item in cohort
    ]

    assert public_leaderboard_ranks(cohort) == {
        "codex-cli": 1,
        "codex-desktop": 1,
        "opencode-cli": 3,
        "claude-desktop": 4,
        "claude-cli": 5,
    }


def test_sensitivity_is_exact_and_visible_for_the_rankable_subset():
    ids = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")

    def record_loss(survival_document, _format, repetition):
        if repetition == 1:
            row = next(row for row in survival_document["metrics"] if row["id"] == "work.actions")
            row.update(correct=0, state="contradiction")

    cohort = [configuration(item, mutate=record_loss if item == "codex-cli" else None) for item in ids]
    winners, stable = public_sensitivity_winners(cohort)
    assert winners["baseline"] == ("claude-cli", "claude-desktop", "codex-desktop", "opencode-cli")
    assert stable
    assert weighted_public_total({category: Fraction(1) for category in PUBLIC_CATEGORY_POINTS}) == 100


def test_sensitivity_keeps_ranked_subset_when_fifth_row_is_unresolved():
    ids = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop", "opencode-cli")

    def unresolved(_survival, format_document, repetition):
        if repetition == 2:
            format_document["broad_evidence"]["broad.documented_format"]["evidence_complete"] = False

    cohort = [configuration(item, mutate=unresolved if item == "opencode-cli" else None) for item in ids]
    winners, stable = public_sensitivity_winners(cohort)
    assert not stable
    assert all(not values for values in winners.values())
