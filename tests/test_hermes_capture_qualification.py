from __future__ import annotations

import pytest

from scripts.qualify_hermes_capture import (
    QualificationError,
    require_turn_response_canaries,
)


def test_hermes_r2_qualification_rejects_missing_r2_assistant_canary() -> None:
    response_canaries = ["SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
                         "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"]
    assistant_text = ["Baseline observed. " + response_canaries[0],
                      "Fixed checkout; final tests pass."]

    with pytest.raises(QualificationError, match="R2 assistant response canary"):
        require_turn_response_canaries(assistant_text, response_canaries, turn=2)


def test_hermes_r2_qualification_requires_ordered_turn_specific_suffixes() -> None:
    response_canaries = ["SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
                         "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"]
    require_turn_response_canaries(
        ["Baseline observed. " + response_canaries[0],
         "Fixed checkout; final tests pass. " + response_canaries[1]],
        response_canaries,
        turn=2,
    )
