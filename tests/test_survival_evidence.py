"""Strict evidence-binding controls for reportable survival-v1 runs."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from session_bench import survival_evidence
from session_bench.survival_evidence import (
    CONFIGURATION_IDS,
    EVIDENCE_SCHEMA_VERSION,
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    RUBRIC_VERSION,
    WORKLOAD_VERSION,
    score_evidence_run,
    validate_evidence_input,
    validate_prospective_evidence_input,
)
from session_bench.survival_metrics import METRICS, load_survival_input


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1"
DIGEST = "a" * 64


def evidence_document() -> dict:
    measurement = load_survival_input(FIXTURES / "equivalent-jsonl/input.jsonl")
    measurement["run_id"] = "codex-cli-run-001"
    measurement["configuration_id"] = "codex-cli"
    measurement["repetition"] = 1
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "workload_version": WORKLOAD_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": measurement["run_id"],
        "capture_id": "capture-001",
        "evaluation_id": "evaluation-001",
        "configuration_id": measurement["configuration_id"],
        "repetition": measurement["repetition"],
        "measurement": measurement,
        "observer": {"id": "observer-001", "sha256": DIGEST},
        "native_manifest": {"id": "manifest-001", "sha256": "b" * 64},
        "decoder": {"id": "decoder-001", "sha256": "c" * 64},
        "metric_evidence": [
            {
                "metric_id": metric_id,
                "observer_ids": [f"observer-{index:02d}"],
                "native_locators": [
                    {
                        "artifact_id": f"native-{index:02d}",
                        "artifact_sha256": f"{index:064x}",
                        "record_location": f"record:{index}",
                    }
                ],
            }
            for index, metric_id in enumerate(METRICS, 1)
        ],
    }


def test_valid_evidence_wraps_and_scores_the_canonical_measurement() -> None:
    document = evidence_document()
    validated = validate_evidence_input(document)
    assert validated["measurement"] == document["measurement"]
    assert set(item["metric_id"] for item in validated["metric_evidence"]) == set(METRICS)
    result = score_evidence_run(document)
    assert result.rankable and result.overall == 100


def test_wrapper_accepts_only_the_frozen_configuration_cohort() -> None:
    document = evidence_document()
    document["configuration_id"] = "invented-surface"
    with pytest.raises(ValueError, match="configuration_id must be one of"):
        validate_evidence_input(document)
    assert CONFIGURATION_IDS == {
        "codex-cli", "codex-desktop", "cursor-cli", "cursor-desktop", "opencode-cli"
    }


def test_prospective_evidence_can_never_enter_the_frozen_scorer() -> None:
    document = evidence_document()
    document["schema_version"] = PROSPECTIVE_EVIDENCE_SCHEMA_VERSION
    prospective = validate_prospective_evidence_input(document)
    assert prospective["schema_version"] == PROSPECTIVE_EVIDENCE_SCHEMA_VERSION
    with pytest.raises(ValueError, match="schema_version"):
        score_evidence_run(document)


@pytest.mark.parametrize("field", ["run_id", "configuration_id", "repetition"])
def test_wrapper_rejects_measurement_identity_mismatch(field: str) -> None:
    document = evidence_document()
    if field == "repetition":
        document[field] = 2
    elif field == "configuration_id":
        document[field] = "cursor-cli"
    else:
        document[field] = "other-run"
    with pytest.raises(ValueError, match="measurement run_id, configuration_id, and repetition"):
        validate_evidence_input(document)


@pytest.mark.parametrize(
    ("path", "value", "error"),
    [
        (("schema_version",), "wrong", "schema_version"),
        (("protocol_version",), "wrong", "protocol_version"),
        (("workload_version",), "wrong", "workload_version"),
        (("rubric_version",), "wrong", "rubric_version"),
        (("observer", "sha256"), "not-a-digest", "observer.sha256"),
        (("native_manifest", "id"), " ", "native_manifest.id"),
        (("decoder", "sha256"), "A" * 64, "decoder.sha256"),
    ],
)
def test_wrapper_rejects_wrong_identities_and_digests(path, value, error) -> None:
    document = evidence_document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=error):
        validate_evidence_input(document)


def test_every_metric_needs_nonempty_observer_and_bound_native_locator() -> None:
    document = evidence_document()
    document["metric_evidence"][0]["observer_ids"] = []
    with pytest.raises(ValueError, match="observer_ids must be a non-empty"):
        validate_evidence_input(document)

    document = evidence_document()
    document["metric_evidence"][0]["native_locators"] = []
    with pytest.raises(ValueError, match="native_locators must be a non-empty"):
        validate_evidence_input(document)

    document = evidence_document()
    document["metric_evidence"][0]["native_locators"][0].pop("record_location")
    with pytest.raises(ValueError, match="wrong fields"):
        validate_evidence_input(document)

    document = evidence_document()
    document["metric_evidence"][0]["native_locators"][0]["artifact_sha256"] = "d" * 63
    with pytest.raises(ValueError, match="artifact_sha256"):
        validate_evidence_input(document)


def test_metric_evidence_requires_exactly_one_row_per_scored_metric() -> None:
    document = evidence_document()
    document["metric_evidence"].pop()
    with pytest.raises(ValueError, match="every required metric exactly once"):
        validate_evidence_input(document)

    document = evidence_document()
    document["metric_evidence"][-1]["metric_id"] = document["metric_evidence"][0]["metric_id"]
    with pytest.raises(ValueError, match="duplicate metric evidence"):
        validate_evidence_input(document)


def test_scoring_is_not_called_until_the_evidence_wrapper_validates(monkeypatch) -> None:
    document = evidence_document()
    document["observer"]["sha256"] = "wrong"
    called = False

    def should_not_score(_measurement):
        nonlocal called
        called = True
        raise AssertionError("score_run must not be called for invalid evidence")

    monkeypatch.setattr(survival_evidence, "score_run", should_not_score)
    with pytest.raises(ValueError, match="observer.sha256"):
        score_evidence_run(document)
    assert not called


def test_wrapper_rejects_extra_fields_and_duplicate_evidence_values() -> None:
    document = evidence_document()
    document["unexpected"] = True
    with pytest.raises(ValueError, match="wrong fields"):
        validate_evidence_input(document)

    document = evidence_document()
    row = document["metric_evidence"][0]
    row["observer_ids"].append(row["observer_ids"][0])
    with pytest.raises(ValueError, match="observer_ids must be unique"):
        validate_evidence_input(document)

    document = evidence_document()
    row = document["metric_evidence"][0]
    row["native_locators"].append(copy.deepcopy(row["native_locators"][0]))
    with pytest.raises(ValueError, match="native_locators must be unique"):
        validate_evidence_input(document)
