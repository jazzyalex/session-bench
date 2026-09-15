"""Focused checks for authoritative v1 identity and evidence timelines."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_bench.survival_metrics import load_survival_input
from session_bench.v1_public_score import (
    SURVIVAL_METRICS,
    aggregate_public_configuration,
    format_evidence_control,
    format_profile_document,
    score_public_run,
    survival_evidence_control,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/equivalent-jsonl/input.jsonl"


def _documents(configuration_id: str = "codex-cli", repetition: int = 1):
    source = load_survival_input(
        FIXTURE
    )
    run_id = f"{configuration_id}-{repetition}"
    source.update(
        run_id=run_id,
        configuration_id=configuration_id,
        repetition=repetition,
    )
    profile = format_profile_document(
        run_id=run_id,
        configuration_id=configuration_id,
        repetition=repetition,
    )
    return source, profile


def test_public_run_preserves_bound_identity_and_populates_deep_evidence_timeline():
    measurement, profile = _documents()
    identity = {
        "provider": "openai",
        "harness": "codex-cli",
        "surface": "cli",
        "execution_mode": "exec-json",
        "os": "macOS-test",
        "build": "codex-0.154.0",
        "model": "gpt-test",
        "configuration": "ordinary-default",
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "observer_schema_version": "1.0-survival-observer",
        "rubric_version": "1.0-survival-rubric",
    }
    run = score_public_run(
        survival_evidence_control(
            measurement,
            evaluation_id="evaluation-1",
            identity=identity,
        ),
        format_evidence_control(
            profile,
            build="codex-0.154.0",
            collected_on="2026-09-11",
            result_id="result-1",
        ),
    )

    assert run.identity is not None
    assert run.identity.provider == "openai"
    assert run.identity.model == "gpt-test"
    assert run.identity.configuration == "ordinary-default"
    assert run.identity.protocol_version == "1.0-survival"
    assert run.identity.build == "codex-0.154.0"
    assert len(run.timeline) == len(SURVIVAL_METRICS)
    first = run.timeline[0]
    assert first.metric_id == SURVIVAL_METRICS[0]
    assert first.observer_ids == run.metric_evidence[first.metric_id].observer_ids
    assert first.native_locators == run.metric_evidence[first.metric_id].native_locators
    assert first.display()["temporal_order_available"] is False
    assert run.display()["identity"]["model"] == "gpt-test"
    assert run.display()["timeline"][0]["metric_id"] == SURVIVAL_METRICS[0]


def test_missing_model_identity_is_explicitly_unavailable_and_not_inferred():
    measurement, profile = _documents()
    run = score_public_run(
        survival_evidence_control(measurement),
        format_evidence_control(profile, build="build-without-model"),
    )

    assert run.identity is not None
    assert run.identity.model is None
    assert run.identity.configuration_id == "codex-cli"
    assert "model" in run.identity.display()["unavailable"]
    assert run.identity.protocol_version == "1.0-survival"


def test_identity_build_must_match_the_bound_format_build():
    measurement, profile = _documents()
    with pytest.raises(ValueError, match="identity build does not match"):
        score_public_run(
            survival_evidence_control(
                measurement,
                identity={"build": "different-build"},
            ),
            format_evidence_control(profile, build="actual-build"),
        )


def test_configuration_propagates_per_run_identity_and_timeline_without_flattening():
    runs = []
    for repetition in (1, 2, 3):
        measurement, profile = _documents(repetition=repetition)
        runs.append(
            score_public_run(
                survival_evidence_control(
                    measurement,
                    evaluation_id=f"evaluation-{repetition}",
                    identity={"model": f"model-{repetition}"},
                ),
                format_evidence_control(
                    profile,
                    build=f"build-{repetition}",
                    result_id=f"result-{repetition}",
                ),
            )
        )
    configuration = aggregate_public_configuration(runs)

    assert [identity.model for identity in configuration.identities if identity] == [
        "model-1",
        "model-2",
        "model-3",
    ]
    assert [repetition for repetition, _events in configuration.timelines] == [1, 2, 3]
    assert all(len(events) == len(SURVIVAL_METRICS) for _rep, events in configuration.timelines)
    assert configuration.display()["identities"][1]["model"] == "model-2"
    assert configuration.display()["timelines"][2]["events"][0]["metric_id"] == SURVIVAL_METRICS[0]
