"""Focused controls for the sanitized public configuration packets."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_public_configuration_bundles as public_bundles
from session_bench.v1_public_score import format_evidence_control, format_profile_document


def test_decoded_model_uses_unique_native_turn_context_label() -> None:
    assert public_bundles._decoded_model(
        {
            "facts": {
                "model_contexts": [
                    {"fields": {"model": "gpt-5.6-sol"}},
                    {"fields": {"model": "gpt-5.6-sol"}},
                ]
            }
        }
    ) == "gpt-5.6-sol"


def test_decoded_model_rejects_mixed_native_labels() -> None:
    with pytest.raises(public_bundles.PublicConfigurationBundleError, match="mixed native model"):
        public_bundles._decoded_model(
            {
                "model_contexts": [
                    {"fields": {"model": "gpt-5.6-sol"}},
                    {"fields": {"model": "gpt-6-astra"}},
                ]
            }
        )


def _desktop_qualification(repetition: int = 1) -> dict:
    return {
        "configuration_id": "codex-desktop",
        "run_id": f"codex-desktop-eval-{repetition}",
        "complete_record_family": True,
        "offline_canonical_equality": True,
        "selected_r2_loss_detected": True,
        "survival_metric_count": 19,
        "format_metric_count": 12,
        # This is retained by the pre-cohort attempt artifact and must not
        # override the later qualified format evidence.
        "unresolved_format_metrics": ["broad.stable_root_location"],
    }


def _desktop_format(repetition: int = 1) -> dict:
    run_id = f"codex-desktop-eval-{repetition}"
    return format_evidence_control(
        format_profile_document(
            run_id=run_id,
            configuration_id="codex-desktop",
            repetition=repetition,
        ),
        build="synthetic-build",
        collected_on="2026-09-14",
        result_id=f"{run_id}-format-qualified",
    )


def test_codex_desktop_uses_qualified_format_for_stable_root_gate() -> None:
    public_bundles._check_qualification(
        public_bundles._spec("codex-desktop", 1),
        _desktop_qualification(),
        [],
        format_document=_desktop_format(),
    )


def test_codex_desktop_rejects_unmeasured_qualified_stable_root() -> None:
    format_document = _desktop_format()
    format_document["profile"]["broad_evidence"]["broad.stable_root_location"]["evidence_complete"] = False
    with pytest.raises(public_bundles.PublicConfigurationBundleError, match="stable-root"):
        public_bundles._check_qualification(
            public_bundles._spec("codex-desktop", 1),
            _desktop_qualification(),
            [],
            format_document=format_document,
        )


def test_public_packets_build_and_verify_when_copied_inputs_are_present(tmp_path: Path) -> None:
    required = []
    for configuration_id in public_bundles.TARGET_CONFIGURATIONS:
        spec = public_bundles._spec(configuration_id, 1)
        required.extend(
            [
                spec.measurement_path,
                spec.format_path,
                spec.decoded_path,
                spec.damage_path,
                spec.qualification_path,
                *spec.audit_paths,
            ]
        )
    if not all(path.is_file() for path in required):
        pytest.skip("qualified copied run artifacts are not present")

    output = tmp_path / "public-configurations"
    result = public_bundles.build(output=output)
    assert result["configuration_ids"] == list(public_bundles.TARGET_CONFIGURATIONS)
    assert all(row["metric_counts"] == [31, 31, 31] for row in result["configurations"])
    assert result["published"] is False
    assert result["independent_reproduction"] is False

    verified = public_bundles._verify_output(output)
    assert verified["configuration_ids"] == list(public_bundles.TARGET_CONFIGURATIONS)
    assert all(row["metric_counts"] == [31, 31, 31] for row in verified["configurations"])

    rebuilt_output = tmp_path / "public-configurations-rebuilt"
    rebuilt = public_bundles.build(output=rebuilt_output)
    assert [row["bundle_sha256"] for row in result["configurations"]] == [
        row["bundle_sha256"] for row in rebuilt["configurations"]
    ]
    first_files = {
        path.relative_to(output): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    rebuilt_files = {
        path.relative_to(rebuilt_output): path.read_bytes()
        for path in rebuilt_output.rglob("*")
        if path.is_file()
    }
    assert first_files == rebuilt_files
