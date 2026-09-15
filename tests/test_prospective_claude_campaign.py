import copy
from pathlib import Path

import pytest

from session_bench.survival_campaign import (
    PROSPECTIVE_CONFIGURATIONS,
    prospective_campaign_summary,
    read_campaign,
    validate_prospective_campaign,
    validate_prospective_frozen_inputs,
)


ROOT = Path(__file__).parents[1]
PLAN_PATH = ROOT / "plans" / "survival-v1" / "campaign-prospective-claude-cohort.json"


def _plan():
    return copy.deepcopy(read_campaign(PLAN_PATH))


def test_prospective_claude_cohort_is_frozen_and_unscored():
    plan = validate_prospective_campaign(_plan())
    assert tuple(item["configuration_id"] for item in plan["configurations"]) == PROSPECTIVE_CONFIGURATIONS
    assert prospective_campaign_summary(plan)["score_eligible"] is False
    assert len(plan["attempts"]) == 25
    assert validate_prospective_frozen_inputs(plan, ROOT)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda plan: plan.__setitem__("score_eligible", True), "score eligibility"),
        (lambda plan: plan["attempts"][0].__setitem__("state", "complete"), "remain scheduled"),
        (lambda plan: plan["configurations"][2]["identity"].__setitem__("version", "unverified"), "identity changed"),
        (lambda plan: plan["configurations"][2].__setitem__("surface", "desktop"), "surface identity changed"),
    ],
)
def test_prospective_claude_cohort_rejects_score_or_identity_drift(mutate, message):
    plan = _plan()
    mutate(plan)
    with pytest.raises(ValueError, match=message):
        validate_prospective_campaign(plan)
