"""Focused deterministic tests for the prospective survival-v1 cohort plan.

The frozen campaign (plans/survival-v1/campaign.json) keeps its own validator
and semantics. These tests cover only the NEW clearly named prospective file
(plans/survival-v1/campaign-prospective-claude-cohort.json): exact cohort
order, exact build/model/workload identities, three planned repetitions per
configuration, and score eligibility false. Auth/runtime/capture failures are
invalid/N/A, never evidence of format quality.
"""

import copy
from pathlib import Path

import pytest

from session_bench.survival_campaign import (
    PROSPECTIVE_CONFIGURATIONS,
    PROSPECTIVE_EXPECTED_IDENTITIES,
    prospective_campaign_summary,
    read_campaign,
    validate_campaign,
    validate_prospective_campaign,
    validate_prospective_frozen_inputs,
)
from session_bench.survival_evidence import (
    CONFIGURATION_IDS,
    PROSPECTIVE_CONFIGURATION_IDS,
)

FROZEN = Path(__file__).parents[1] / "plans" / "survival-v1" / "campaign.json"
PROSPECTIVE = (
    Path(__file__).parents[1]
    / "plans"
    / "survival-v1"
    / "campaign-prospective-claude-cohort.json"
)


def test_prospective_plan_has_new_cohort_and_25_scheduled_slots() -> None:
    plan = read_campaign(PROSPECTIVE)
    validate_prospective_campaign(plan)
    assert tuple(plan["configuration_order"]) == PROSPECTIVE_CONFIGURATIONS
    assert tuple(c["configuration_id"] for c in plan["configurations"]) == PROSPECTIVE_CONFIGURATIONS
    assert prospective_campaign_summary(plan) == {
        "state": "proposed_prospective",
        "configurations": 5,
        "attempts": 25,
        "setup_corrections": 0,
        "attempt_states": {"scheduled": 25},
        "quota_baseline_used_percent": 72,
        "quota_stop_used_percent": 95,
        "score_eligible": False,
    }
    assert validate_prospective_frozen_inputs(plan, PROSPECTIVE.parents[2]) == plan["frozen_inputs"]


def test_prospective_plan_pins_exact_identities_and_repetitions() -> None:
    plan = read_campaign(PROSPECTIVE)
    assert plan["frozen_inputs"] == read_campaign(FROZEN)["frozen_inputs"]
    for item in plan["configurations"]:
        assert item["identity"] == PROSPECTIVE_EXPECTED_IDENTITIES[item["configuration_id"]]
    assert plan["configurations"][2]["identity"]["version"] == "2.1.270"
    assert plan["configurations"][3]["identity"]["version"] == "1.52386.3"
    assert plan["configurations"][4]["identity"]["model"] == "opencode/muse-spark-1.3-contributor-free"
    by_configuration: dict = {}
    for attempt in plan["attempts"]:
        by_configuration.setdefault(attempt["configuration_id"], []).append(attempt)
    for configuration_id in PROSPECTIVE_CONFIGURATIONS:
        rows = by_configuration[configuration_id]
        assert [r["repetition"] for r in rows if r["kind"] == "evaluated"] == [1, 2, 3]


def test_prospective_plan_keeps_score_eligibility_false() -> None:
    plan = read_campaign(PROSPECTIVE)
    assert plan["score_eligible"] is False
    assert plan["publication"]["live_vendor_claims_allowed"] is False
    assert plan["state"] == "proposed_prospective"
    for mutation in ("eligible", "claims", "state", "nonscheduled"):
        broken = copy.deepcopy(plan)
        if mutation == "eligible":
            broken["score_eligible"] = True
        elif mutation == "claims":
            broken["publication"]["live_vendor_claims_allowed"] = True
        elif mutation == "state":
            broken["state"] = "authorized_bounded_calibration"
        else:
            broken["attempts"][0]["state"] = "complete"
        with pytest.raises(ValueError):
            validate_prospective_campaign(broken)


def test_prospective_plan_rejects_drift_and_frozen_validator() -> None:
    plan = read_campaign(PROSPECTIVE)
    drifted = copy.deepcopy(plan)
    drifted["configurations"][2]["identity"]["version"] = "9.9.9"
    with pytest.raises(ValueError, match="build/model identity changed"):
        validate_prospective_campaign(drifted)
    with pytest.raises(ValueError):
        validate_campaign(plan)
    with pytest.raises(ValueError):
        validate_prospective_campaign(read_campaign(FROZEN))


def test_evidence_cohorts_frozen_intact_prospective_opt_in() -> None:
    assert CONFIGURATION_IDS == {
        "codex-cli",
        "codex-desktop",
        "cursor-cli",
        "cursor-desktop",
        "opencode-cli",
    }
    assert PROSPECTIVE_CONFIGURATION_IDS == {
        "codex-cli",
        "codex-desktop",
        "claude-cli",
        "claude-desktop",
        "opencode-cli",
    }
