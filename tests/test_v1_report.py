"""Focused offline tests for the v1 report-card renderer."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import pytest

from session_bench.v1_report import (
    PublicConfigurationAttempt,
    build_authoritative_control_report,
    build_authoritative_report,
    load_authoritative_report,
    load_report,
    render_authoritative_report,
    render_index_html,
    render_report,
    render_scorecard_svg,
    validate_authoritative_report,
)
from session_bench.v1_public_score import PUBLIC_METRICS, SURVIVAL_METRICS, TARGET_CONFIGURATIONS


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "fixtures" / "scenarios" / "survival-v1" / "public-report-demo.json"


def test_constructed_demo_has_the_required_surfaces_and_artifacts(tmp_path: Path):
    report = load_report(DEMO)
    assert report["constructed"] is True
    assert report["data_status"].startswith("CONSTRUCTED")
    assert {row["name"] for row in report["configurations"]} == {"Fixture Alpha", "Fixture Beta", "Fixture Gamma"}
    assert {row["surface"] for row in report["configurations"]} == {"CLI", "Desktop"}

    output = render_report(report, tmp_path / "report")
    assert {path.name for path in output.iterdir()} == {"index.html", "scorecard.svg", "report.json"}
    assert json.loads((output / "report.json").read_text(encoding="utf-8")) == report


def test_renderer_escapes_snippets_and_keeps_assets_offline(tmp_path: Path):
    report = load_report(DEMO)
    report["headline"] = "<script>alert(1)</script>"
    report["configurations"][0]["evidence"][0]["snippet"] = "</pre><img src=x onerror=alert(1)>"
    output = render_report(report, tmp_path / "report")
    page = (output / "index.html").read_text(encoding="utf-8")
    svg = (output / "scorecard.svg").read_text(encoding="utf-8")

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;/pre&gt;&lt;img src=x onerror=alert(1)&gt;" in page
    assert "<script src=" not in page
    assert "http://" not in page and "https://" not in page
    # The SVG namespace is a required XML identifier; there are no external
    # images, stylesheets, fonts, or scripts in the asset.
    assert "<image" not in svg and "<script" not in svg and "url(http" not in svg


def test_mobile_and_accessibility_hooks_are_present():
    report = load_report(DEMO)
    page = render_index_html(report)
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    assert 'aria-label="CLI"' in page or 'surface-cli' in page
    assert 'aria-label="Desktop"' in page or 'surface-desktop' in page
    assert 'aria-label="Five category scores' in page
    assert '<details class="deep-dive"' in page
    assert 'Observed by independent observer' in page
    assert 'Recorded in native artifact' in page
    assert 'What should I use?' in page
    assert 'href="#scope"' in page and 'href="#method"' in page


def test_incomplete_rows_never_receive_a_rank_or_recommendation(tmp_path: Path):
    report = load_report(DEMO)
    beta = next(item for item in report["configurations"] if item["id"] == "fixture-beta-cli")
    beta["ranking_eligible"] = True
    beta["evidence_complete"] = True
    beta["score"] = 99
    # A hand-edited incomplete row still lacks measured gates, a complete
    # three-run set, and a complete range; its score cannot become a rank.
    report["recommendations"].append({
        "id": "invented-beta",
        "use_case": "Invented advice",
        "target_id": "fixture-beta-cli",
        "claim": "This must never render",
        "citation_ids": ["beta-missing"],
        "qualifies": True,
    })
    output = render_report(report, tmp_path / "report")
    page = (output / "index.html").read_text(encoding="utf-8")

    assert "Fixture Beta" in page
    beta_start = page.index("Fixture Beta")
    beta_window = page[beta_start:beta_start + 1400]
    assert "UNRANKED" in beta_window
    assert "Invented advice" not in page
    assert "This must never render" not in page


def test_scorecard_has_five_named_angles_and_constructed_unranked_state():
    report = load_report(DEMO)
    svg = render_scorecard_svg(report)
    for category in report["categories"]:
        assert category["name"].replace("&", "&amp;") in svg
    assert "Fixture Alpha" in svg and "Fixture Gamma" in svg and "Fixture Beta" in svg
    assert "UNRANKED" in svg
    assert "role=" in svg and "aria-labelledby" in svg


def test_authoritative_control_uses_public_scorer_contract_and_five_surface_gate(tmp_path: Path):
    report = build_authoritative_control_report()
    assert report["schema_version"] == "session-bench-v1-authoritative-report"
    assert report["constructed"] is True
    assert report["metric_ids"] == list(PUBLIC_METRICS)
    assert len(report["target_surfaces"]) == 5
    assert report["cohort"]["leaderboard_eligible"] is True
    assert all(row["verification"]["state"] == "Fully reproduced" for row in report["configurations"])
    assert all(row["rank"] == 1 for row in report["configurations"])
    assert all(len(row["metrics"]) == 31 for row in report["configurations"])

    output = render_authoritative_report(report, tmp_path / "authoritative")
    loaded = load_authoritative_report(output / "report.json")
    assert loaded == report
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "CONSTRUCTED" in page
    assert "Codex CLI" in page and "Claude Desktop" in page and "OpenCode CLI" in page
    assert all(use_case in page for use_case in ("Audit Ready", "Portable Archives", "Usage Accounting", "Cli Desktop Consistency", "Lean Complete Record", "Long Term Archives"))


def test_authoritative_report_accepts_honest_zero_run_blocked_rows(tmp_path: Path):
    attempts = [
        PublicConfigurationAttempt(
            configuration_id,
            "blocked",
            reason_ids=("preflight.gui_observer_missing",),
            blocker_evidence=({
                "id": f"{configuration_id}-preflight",
                "kind": "preflight",
                "detail": "GUI observer missing",
                "observer_ids": [],
                "native_locators": [],
            },),
        )
        for configuration_id in TARGET_CONFIGURATIONS
    ]
    report = build_authoritative_report(
        attempts,
        None,
        generated_at="2026-09-11T12:00:00Z",
    )

    assert report["cohort"]["leaderboard_eligible"] is False
    assert all(row["attempt_state"] == "blocked" for row in report["configurations"])
    assert all(row["runs"] == [] and row["rank"] is None for row in report["configurations"])
    assert all(row["verification"]["state"] == "Unranked" for row in report["configurations"])
    output = render_authoritative_report(report, tmp_path / "blocked")
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "GUI observer missing" in page
    assert "Evidence-bound public result" not in page


def test_authoritative_runs_retain_identity_and_deep_timeline():
    report = build_authoritative_control_report()
    for configuration in report["configurations"]:
        for run in configuration["runs"]:
            assert run["identity"]["configuration_id"] == configuration["configuration_id"]
            assert run["identity"]["run_id"] == run["run_id"]
            assert [event["metric_id"] for event in run["timeline"]] == list(SURVIVAL_METRICS)
            assert all(event["temporal_order_available"] is False for event in run["timeline"])


def test_authoritative_view_renders_resolved_evidence_comparisons_with_locators(tmp_path: Path):
    report = build_authoritative_control_report()
    output = render_authoritative_report(report, tmp_path / "authoritative")
    page = (output / "index.html").read_text(encoding="utf-8")
    comparison = page[page.index('<h2 id="timeline-title">Evidence comparison</h2>'):]

    expected_rows = len(TARGET_CONFIGURATIONS) * 3 * len(SURVIVAL_METRICS)
    assert comparison.count('class="state-chip state-measured"') == expected_rows
    assert "No locator" not in comparison
    assert "native_locators:" in comparison
    assert "observer_ids:" in comparison
    assert "Rows follow metric-contract order unless temporal ordering is available." in page
    assert "Observed versus recorded" not in page


@pytest.mark.parametrize("model", [None, "different-model"])
def test_serialized_rank_rejects_missing_or_mixed_model_identity(model):
    report = build_authoritative_control_report()
    run = report["configurations"][0]["runs"][1]
    run["identity"]["model"] = model
    if model is None:
        run["identity"]["unavailable"] = ["model"]
    with pytest.raises(ValueError, match="verification facts"):
        validate_authoritative_report(report)


def test_authoritative_path_rejects_renderer_shape_and_arbitrary_recommendations():
    with pytest.raises(TypeError, match="PublicConfigurationScore"):
        build_authoritative_report([{}] * 5, {}, generated_at="2026-09-11T12:00:00Z")

    report = build_authoritative_control_report()
    report["recommendation_outputs"]["recommendations"][0]["qualifies"] = True
    with pytest.raises(ValueError, match="wrong fields"):
        validate_authoritative_report(report)


def test_authoritative_path_rejects_rank_when_cohort_gate_fails_and_wrong_category_normalization():
    report = build_authoritative_control_report()
    report["cohort"]["leaderboard_eligible"] = False
    with pytest.raises(ValueError, match="ranked without"):
        validate_authoritative_report(report)

    report = build_authoritative_control_report()
    report["configurations"][0]["categories"]["record_fidelity"]["percent"] = 99.0
    with pytest.raises(ValueError, match="not normalized"):
        validate_authoritative_report(report)


def test_resolved_portability_loss_remains_fully_reproduced_and_rankable():
    report = build_authoritative_control_report(portability_loss_configuration_id="codex-cli")
    row = next(item for item in report["configurations"] if item["configuration_id"] == "codex-cli")
    metric = next(item for item in row["metrics"] if item["id"] == "portable.companions")
    assert row["verification"]["state"] == "Fully reproduced"
    assert row["verification"]["facts"]["portable_gate"] is False
    assert row["rank"] is not None
    assert [item["state"] for item in metric["repetitions"]] == ["contradiction"] * 3
    assert all(item["metric_evidence"]["state"] == "contradiction" for item in metric["repetitions"])
    assert report["cohort"]["leaderboard_eligible"] is True
    assert row["categories"]["portability_openness"]["points"] < 20
    for other in report["configurations"]:
        if other["configuration_id"] != "codex-cli":
            assert other["categories"]["portability_openness"]["points"] == 20.0
    validate_authoritative_report(report)


def test_authoritative_validator_rejects_serialized_state_mismatch():
    report = build_authoritative_control_report(portability_loss_configuration_id="codex-cli")
    row = next(item for item in report["configurations"] if item["configuration_id"] == "codex-cli")
    metric = next(item for item in row["metrics"] if item["id"] == "portable.companions")
    metric["repetitions"][0]["state"] = "measured"

    with pytest.raises(ValueError, match="state does not match metric evidence state"):
        validate_authoritative_report(report)


def test_generic_renderer_ranks_rows_with_resolved_losses():
    report = load_report(DEMO)
    alpha = next(item for item in report["configurations"] if item["id"] == "fixture-alpha-cli")
    for gate in alpha["gate_matrix"]:
        gate["state"] = "native_absent"

    svg = render_scorecard_svg(report)

    assert "Fixture Alpha · CLI · #1" in svg


def test_authoritative_rows_retain_typed_metric_provenance_and_scorer_rec_outputs():
    report = build_authoritative_control_report()
    for configuration in report["configurations"]:
        assert configuration["bundle"]["sha256"]
        assert configuration["bundle"]["immutable"] is True
        assert configuration["bundle"]["public"] is True
        assert configuration["reproduction_receipt"]["offline_recomputed"] is True
        assert configuration["reproduction_receipt"]["verified"] is True
        for metric in configuration["metrics"]:
            for repetition in metric["repetitions"]:
                evidence = repetition["metric_evidence"]
                assert evidence["state"]
                assert evidence["observer_ids"]
                assert evidence["native_locators"]
                assert all(set(locator) >= {"artifact_id", "artifact_sha256"} for locator in evidence["native_locators"])
    positive = [
        item
        for item in report["recommendation_outputs"]["recommendations"]
        if item["status"] != "no_recommendation"
    ]
    assert positive
    assert any(
        "constructed-native-" in locator or "constructed-survival-native-" in locator
        for item in positive
        for locator in item["native_locators"]
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda report: report["configurations"][0]["metrics"][0]["repetitions"][0]["metric_evidence"]["native_locators"].clear(),
            "native locators",
        ),
        (
            lambda report: report["configurations"][0]["metrics"][0]["repetitions"][0]["metric_evidence"]["native_locators"][0].update(artifact_sha256="bad"),
            "SHA-256",
        ),
        (
            lambda report: report["configurations"][0]["reproduction_receipt"].update(offline_recomputed=False),
            "bindings",
        ),
        (
            lambda report: report["configurations"][0]["reproduction_receipt"].update(bundle_sha256="0" * 64),
            "bindings",
        ),
        (
            lambda report: report["recommendation_outputs"]["recommendations"][0]["citations"][0]["result_ids"].__setitem__(0, "tampered-result"),
            "attempted scorer row",
        ),
    ],
)
def test_authoritative_serialized_provenance_tampering_is_rejected(mutate, message):
    report = deepcopy(build_authoritative_control_report())
    mutate(report)
    with pytest.raises(ValueError, match=message):
        validate_authoritative_report(report)
