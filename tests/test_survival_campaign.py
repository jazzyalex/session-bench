import copy
from pathlib import Path

import pytest

from session_bench.survival_campaign import (
    FROZEN_INPUT_PATHS,
    campaign_summary,
    quota_allows_submission,
    read_campaign,
    validate_campaign,
    validate_frozen_inputs,
)


PLAN = Path(__file__).parents[1] / "plans" / "survival-v1" / "campaign.json"


def test_frozen_campaign_has_five_surfaces_and_25_slots() -> None:
    plan = read_campaign(PLAN)
    validate_campaign(plan)
    assert campaign_summary(plan) == {
        "state": "authorized_bounded_calibration",
        "configurations": 5,
        "attempts": 25,
        "setup_corrections": 5,
        "attempt_states": {
            "invalid": 6,
            "captured": 2,
            "complete": 3,
            "blocked": 1,
            "scheduled": 13,
        },
        "quota_baseline_used_percent": 72,
        "quota_stop_used_percent": 95,
    }
    assert validate_frozen_inputs(plan, PLAN.parents[2]) == plan["frozen_inputs"]


def test_frozen_input_validation_detects_byte_drift(tmp_path: Path) -> None:
    plan = read_campaign(PLAN)
    for relative_path in FROZEN_INPUT_PATHS.values():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((PLAN.parents[2] / relative_path).read_bytes())
    target = tmp_path / "docs/survival-v1/protocol.md"
    target.write_text(target.read_text() + "\ndrift\n")
    with pytest.raises(ValueError, match="protocol.md"):
        validate_frozen_inputs(plan, tmp_path)


def test_quota_gate_fails_closed() -> None:
    plan = read_campaign(PLAN)
    assert quota_allows_submission(plan, 72) is True
    assert quota_allows_submission(plan, 94) is True
    assert quota_allows_submission(plan, 95) is False
    assert quota_allows_submission(plan, None) is False


@pytest.mark.parametrize("mutation", ["surface", "attempt", "digest", "authority", "budget", "correction"])
def test_campaign_population_and_authority_are_immutable(mutation: str) -> None:
    plan = read_campaign(PLAN)
    broken = copy.deepcopy(plan)
    if mutation == "surface":
        broken["configurations"].pop()
    elif mutation == "attempt":
        broken["attempts"][2]["repetition"] = 3
    elif mutation == "digest":
        broken["frozen_inputs"]["workload_sha256"] = "short"
    elif mutation == "budget":
        broken["configurations"][0].pop("usage_budget")
    elif mutation == "correction":
        broken["setup_corrections"][0]["attempt_id"] = "retry-unbounded"
    else:
        broken["state"] = "draft"
    with pytest.raises(ValueError):
        validate_campaign(broken)
