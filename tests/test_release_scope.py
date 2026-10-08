"""Release coverage stays explicit without rewriting historical cohorts."""

import copy
import json
from pathlib import Path

import pytest

from session_bench.release_scope import (
    CONFIGURATION_IDS, HISTORICAL_IDS, load_release_scope, release_scope_summary,
    validate_release_scope, validate_release_statuses,
)
from session_bench.survival_campaign import CONFIGURATIONS, PROSPECTIVE_CONFIGURATIONS


REPO = Path(__file__).resolve().parents[1]


def statuses():
    return [
        {"configuration_id": key, "state": "unattempted", "attempt_ids": [],
         "evidence_refs": [], "reason_ids": []}
        for key in CONFIGURATION_IDS
    ]


def attempted(rows, key, state="blocked"):
    row = next(row for row in rows if row["configuration_id"] == key)
    row.update(state=state, attempt_ids=[f"{key}-attempt-1"],
               evidence_refs=[f"attempts/{key}-attempt-1.json"],
               reason_ids=[] if state == "rankable" else ["preflight.authentication_unavailable"])
    return row


def test_manifest_preserves_all_ten_historical_aliases_and_four_additions():
    scope = load_release_scope()
    historical = json.loads((REPO / "data/measurements.json").read_text())["agents"]
    assert tuple(row["configuration_id"] for row in scope["rows"]) == CONFIGURATION_IDS
    assert len(CONFIGURATION_IDS) == 14
    assert set(HISTORICAL_IDS.values()) == set(historical)
    assert set(CONFIGURATIONS).issubset(CONFIGURATION_IDS)
    assert set(PROSPECTIVE_CONFIGURATIONS).issubset(CONFIGURATION_IDS)
    assert {row["configuration_id"] for row in scope["rows"] if row["historical_id"] is None} == {
        "codex-desktop", "claude-desktop", "cursor-desktop", "deepseek-harness-cli",
    }
    assert scope["rows"][-1]["identity_state"] == "scoped"
    assert scope["rows"][-1]["harness"] == "dsh"


@pytest.mark.parametrize(("field", "value", "message"), [
    ("historical_id", "claude-cli", "historical identity"),
    ("surface", "desktop", "surface identity"),
    ("harness", "claude-desktop", "harness identity"),
    ("identity_state", "measured", "identity state"),
    ("display_name", " ", "display_name"),
    ("configuration_id", "deepseek", "unknown release"),
])
def test_release_row_rejects_identity_drift(field, value, message):
    scope = load_release_scope()
    scope["rows"][2][field] = value
    with pytest.raises(ValueError, match=message):
        validate_release_scope(scope)


@pytest.mark.parametrize("mutation", [
    lambda scope: scope["rows"].pop(),
    lambda scope: scope["rows"].append(copy.deepcopy(scope["rows"][0])),
    lambda scope: scope["rows"].reverse(),
    lambda scope: scope["rows"].__setitem__(1, copy.deepcopy(scope["rows"][0])),
])
def test_release_population_cannot_shrink_duplicate_or_reorder(mutation):
    scope = load_release_scope()
    mutation(scope)
    with pytest.raises(ValueError, match="14 rows|population or order"):
        validate_release_scope(scope)


def test_partial_cohort_can_be_rankable_while_blocked_rows_remain_visible():
    rows = statuses()
    attempted(rows, "pi", "rankable")
    attempted(rows, "openclaw")
    attempted(rows, "hermes", "incomplete")
    summary = release_scope_summary(load_release_scope(), rows, rankable_configuration_ids=["pi"])
    assert summary["scoped_rows"] == 14
    assert summary["attempted_rows"] == 3
    assert summary["rankable_rows"] == 1
    assert summary["blocked_rows"] == summary["incomplete_rows"] == 1
    assert summary["unattempted_rows"] == 11
    assert summary["rows"][1]["configuration_id"] == "openclaw"
    assert summary["rows"][1]["reason_ids"] == ["preflight.authentication_unavailable"]


def test_all_rows_can_be_attempted_but_none_rankable():
    rows = statuses()
    for key in CONFIGURATION_IDS:
        attempted(rows, key)
    summary = release_scope_summary(load_release_scope(), rows)
    assert summary["attempted_rows"] == summary["blocked_rows"] == 14
    assert summary["rankable_rows"] == summary["unattempted_rows"] == 0


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda rows: rows.pop(), "all 14 rows"),
    (lambda rows: rows[1].update(configuration_id="pi"), "duplicate configuration"),
    (lambda rows: rows[0].update(state="fail"), "unknown release status"),
    (lambda rows: rows[0].update(attempt_ids=["attempt-1"]), "cannot claim attempt"),
    (lambda rows: attempted(rows, "pi").update(reason_ids=[]), "requires reason"),
    (lambda rows: attempted(rows, "pi").update(evidence_refs=[]), "requires attempt IDs"),
    (lambda rows: attempted(rows, "pi").update(attempt_ids=[]), "requires attempt IDs"),
    (lambda rows: attempted(rows, "pi").update(reason_ids=[" "]), "nonempty strings"),
    (lambda rows: attempted(rows, "pi").update(attempt_ids=["a", "a"]), "must be unique"),
])
def test_coverage_rejects_hidden_rows_unproven_attempts_and_missing_blockers(mutation, message):
    rows = statuses()
    mutation(rows)
    with pytest.raises(ValueError, match=message):
        validate_release_statuses(load_release_scope(), rows)


def test_attempt_cannot_be_counted_on_multiple_rows():
    rows = statuses()
    attempted(rows, "pi")["attempt_ids"] = ["shared"]
    attempted(rows, "openclaw")["attempt_ids"] = ["shared"]
    with pytest.raises(ValueError, match="multiple configurations"):
        validate_release_statuses(load_release_scope(), rows)


@pytest.mark.parametrize("declared_rankable", [False, True])
def test_status_and_evaluator_rankable_population_must_agree(declared_rankable):
    rows = statuses()
    attempted(rows, "pi", "rankable" if declared_rankable else "blocked")
    with pytest.raises(ValueError, match="independently evaluated"):
        validate_release_statuses(load_release_scope(), rows,
                                  rankable_configuration_ids=[] if declared_rankable else ["pi"])


@pytest.mark.parametrize("rankable", [["unknown"], ["pi", "pi"]])
def test_evaluator_population_is_a_unique_scoped_subset(rankable):
    with pytest.raises(ValueError, match="unknown configuration|duplicate configuration"):
        validate_release_statuses(load_release_scope(), statuses(), rankable_configuration_ids=rankable)


def test_rankable_row_cannot_carry_blockers():
    rows = statuses()
    attempted(rows, "pi", "rankable")["reason_ids"] = ["observer.incomplete"]
    with pytest.raises(ValueError, match="blocking reason"):
        validate_release_statuses(load_release_scope(), rows, rankable_configuration_ids=["pi"])


def test_coverage_order_is_normalized_to_manifest_without_mutation():
    rows = statuses()[::-1]
    snapshot = copy.deepcopy(rows)
    summary = release_scope_summary(load_release_scope(), rows)
    assert tuple(row["configuration_id"] for row in summary["rows"]) == CONFIGURATION_IDS
    assert rows == snapshot
