"""Frozen C01/C02 observer inputs for L0 construction tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObserverInput:
    scenario: str
    observation_id: str
    sequence: int
    kind: str
    value: str


def c01_inputs(run_id: str) -> tuple[ObserverInput, ...]:
    marker = f"SB_F0_{run_id}_C01_café_🙂"
    prompts = (
        f"Remember this marker exactly: {marker}. Reply with the marker on its own line, then say READY.",
        "Correction: preserve the marker's accents and emoji exactly; reply with the marker on its own line, then say CORRECTED.",
        "Now repeat the marker exactly once and say DONE.",
    )
    return tuple(ObserverInput("C01", f"c01-submit-{i}", i, "accepted_prompt", prompt)
                 for i, prompt in enumerate(prompts, 1))


def c02_inputs() -> tuple[ObserverInput, ...]:
    values = (
        ("c02-inspect", "inspect", "fixture_project/target.py"),
        ("c02-test-fail", "test", "python3 fixture_project/test_target.py"),
        ("c02-edit", "edit", "fixture_project/target.py"),
        ("c02-test-pass", "test", "python3 fixture_project/test_target.py"),
    )
    return tuple(ObserverInput("C02", ident, seq, kind, value)
                 for seq, (ident, kind, value) in enumerate(values, 1))
