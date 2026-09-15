"""Synthetic controls for the generic v1 configuration bundle boundary."""

from __future__ import annotations

import copy
import json

import pytest

from session_bench.configuration_bundle import (
    ConfigurationBundleError,
    build_configuration_bundle,
    canonical_json,
    canonical_sha256,
    run_record_from_public_score,
    validate_configuration_bundle,
)
from session_bench.v1_public_score import PUBLIC_CATEGORY_POINTS, PUBLIC_METRICS


def _record(configuration_id: str = "codex-desktop", repetition: int = 1) -> dict:
    return {
        "configuration_id": configuration_id,
        "run_id": f"run-{configuration_id}-{repetition}",
        "repetition": repetition,
        "result_id": f"result-{configuration_id}-{repetition}",
        "evaluation_id": f"evaluation-{configuration_id}-{repetition}",
        "collected_on": "2026-09-14",
        "identity": {
            "provider": "Codex",
            "harness": "Session-Bench v1",
            "surface": "desktop",
            "execution_mode": "interactive local",
            "os": "macOS synthetic",
            "build": "synthetic-build-1",
            "model": "synthetic-model",
            "configuration": "synthetic-default",
            "observer_schema_version": "1.0-survival-observer",
        },
        "result": {
            "resolved": True,
            "metric_ids": list(PUBLIC_METRICS),
            "metrics": {metric_id: 1.0 for metric_id in PUBLIC_METRICS},
            "categories": {category: 20.0 for category in PUBLIC_CATEGORY_POINTS},
            "overall": 100.0,
        },
        "replay": {
            "evidence_id": f"replay-{repetition}",
            "verified": True,
            "offline": True,
            "original_root_denied": True,
            "vendor_executable_denied": True,
            "network_denied": True,
            "runtime_sha256": "a" * 64,
        },
        "canonical_equality": {
            "evidence_id": f"equality-{repetition}",
            "verified": True,
            "ordinary_sha256": "b" * 64,
            "isolated_sha256": "b" * 64,
        },
        "privacy": {
            "evidence_id": f"privacy-{repetition}",
            "verified": True,
            "credentials_absent": True,
            "account_data_absent": True,
            "personal_history_absent": True,
            "absolute_paths_absent": True,
            "raw_native_withheld": True,
            "public_derivative_sha256": "c" * 64,
        },
    }


def _records(configuration_id: str = "codex-desktop") -> list[dict]:
    return [_record(configuration_id, repetition) for repetition in (1, 2, 3)]


def test_builder_is_surface_neutral_deterministic_and_emits_report_input() -> None:
    records = _records()
    built = build_configuration_bundle(list(reversed(records)))
    rebuilt = build_configuration_bundle(records)

    assert built.configuration_id == "codex-desktop"
    assert built.bundle_sha256 == rebuilt.bundle_sha256
    assert built.receipt_sha256 == rebuilt.receipt_sha256
    assert built.result_ids == tuple(sorted(record["result_id"] for record in records))
    assert built.independent_reproduction is False
    assert set(built.report_input) == {
        "schema_version",
        "configuration_id",
        "bundle",
        "reproduction_receipt",
    }
    assert built.report_input["bundle"]["sha256"] == built.bundle_sha256
    assert built.report_input["reproduction_receipt"]["bundle_sha256"] == built.bundle_sha256


def test_builder_binds_hashes_to_full_run_contents_and_validates_tampering() -> None:
    built = build_configuration_bundle(_records(), bundle_id="bundle-codex", receipt_id="receipt-codex")
    document = built.display()
    assert document["bundle"]["sha256"] == canonical_sha256(
        {
            "schema_version": "session-bench-configuration-bundle-v1",
            "id": "bundle-codex",
            "configuration_id": "codex-desktop",
            "immutable": True,
            "public": True,
            "result_ids": built.result_ids,
            "runs": document["runs"],
        }
    )
    validate_configuration_bundle(document)

    tampered = copy.deepcopy(document)
    tampered["runs"][1]["result"]["overall"] = 99.0
    with pytest.raises(ConfigurationBundleError, match="bundle hash"):
        validate_configuration_bundle(tampered)


def test_canonical_json_round_trip_accepts_sorted_object_keys() -> None:
    """Canonical serialization sorts metric mappings without changing meaning."""

    built = build_configuration_bundle(_records())
    serialized = canonical_json(built.display())
    restored = json.loads(serialized)
    validated = validate_configuration_bundle(restored)
    assert validated.bundle_sha256 == built.bundle_sha256
    assert validated.receipt_sha256 == built.receipt_sha256


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("replay", "offline"),
        ("canonical_equality", "verified"),
        ("privacy", "raw_native_withheld"),
    ],
)
def test_missing_or_failed_required_evidence_never_builds(section: str, field: str) -> None:
    records = _records()
    records[0][section][field] = False
    with pytest.raises(ConfigurationBundleError, match=section):
        build_configuration_bundle(records)


def test_missing_required_evidence_object_fails_closed() -> None:
    records = _records()
    del records[2]["canonical_equality"]
    with pytest.raises(ConfigurationBundleError, match="canonical_equality"):
        build_configuration_bundle(records)


@pytest.mark.parametrize(
    "records",
    [
        _records()[:2],
        [_record(repetition=1), _record(repetition=1), _record(repetition=3)],
        [_record("codex-desktop", 1), _record("opencode-cli", 2), _record("codex-desktop", 3)],
    ],
)
def test_builder_requires_exact_three_repetitions_for_one_configuration(records: list[dict]) -> None:
    with pytest.raises(ConfigurationBundleError):
        build_configuration_bundle(records)


def test_builder_rejects_unresolved_result_or_unsafe_path() -> None:
    records = _records()
    records[0]["result"]["resolved"] = False
    with pytest.raises(ConfigurationBundleError, match="resolved"):
        build_configuration_bundle(records)

    records = _records()
    records[0]["result"]["metrics"][next(iter(PUBLIC_METRICS))] = "/Users/private"
    with pytest.raises(ConfigurationBundleError, match="finite number"):
        build_configuration_bundle(records)


def test_validate_rejects_local_builder_relabelled_as_independent() -> None:
    document = build_configuration_bundle(_records()).display()
    document["independent_reproduction"] = True
    with pytest.raises(ConfigurationBundleError, match="independent reproduction"):
        validate_configuration_bundle(document)


def test_public_score_projection_is_checked_before_bundle_build() -> None:
    from pathlib import Path

    from session_bench.survival_metrics import load_survival_input
    from session_bench.v1_public_score import (
        format_evidence_control,
        format_profile_document,
        score_public_run,
        survival_evidence_control,
    )

    source = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/equivalent-jsonl/input.jsonl"
    scores = []
    for repetition in (1, 2, 3):
        measurement = load_survival_input(source)
        measurement.update(
            run_id=f"projected-{repetition}",
            configuration_id="codex-desktop",
            repetition=repetition,
        )
        profile = format_profile_document(
            run_id=f"projected-{repetition}",
            configuration_id="codex-desktop",
            repetition=repetition,
        )
        identity = {
            "provider": "Codex",
            "harness": "Session-Bench",
            "surface": "desktop",
            "execution_mode": "constructed-control",
            "os": "macOS-test",
            "build": "synthetic-build",
            "model": "synthetic-model",
            "configuration": "synthetic-default",
            "observer_schema_version": "1.0-survival-observer",
        }
        scores.append(
            score_public_run(
                survival_evidence_control(
                    measurement,
                    evaluation_id=f"projected-evaluation-{repetition}",
                    identity=identity,
                ),
                format_evidence_control(
                    profile,
                    build="synthetic-build",
                    collected_on="2026-09-14",
                    result_id=f"projected-result-{repetition}",
                ),
            )
        )
    records = [
        run_record_from_public_score(
            score,
            replay={
                "evidence_id": f"replay-{score.repetition}",
                "verified": True,
                "offline": True,
                "original_root_denied": True,
                "vendor_executable_denied": True,
                "network_denied": True,
                "runtime_sha256": "a" * 64,
            },
            canonical_equality={
                "evidence_id": f"equality-{score.repetition}",
                "verified": True,
                "ordinary_sha256": "b" * 64,
                "isolated_sha256": "b" * 64,
            },
            privacy={
                "evidence_id": f"privacy-{score.repetition}",
                "verified": True,
                "credentials_absent": True,
                "account_data_absent": True,
                "personal_history_absent": True,
                "absolute_paths_absent": True,
                "raw_native_withheld": True,
                "public_derivative_sha256": "c" * 64,
            },
        )
        for score in scores
    ]
    built = build_configuration_bundle(records)
    assert built.result_ids == ("projected-result-1", "projected-result-2", "projected-result-3")
