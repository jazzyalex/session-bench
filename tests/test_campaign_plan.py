import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from session_bench.bundle import read_json
from session_bench.campaign_plan import campaign_plan_summary, validate_campaign_plan
from session_bench.live_plan import plan_sha256


REPO = Path(__file__).parents[1]
PLAN_PATH = REPO / "plans" / "v1" / "l1-calibration.proposed.json"


def _plan():
    return copy.deepcopy(read_json(PLAN_PATH))


def _ready(plan):
    plan["state"] = "ready_for_authorization"
    for target in plan["targets"]:
        subject = target["subject"]
        subject["application_version"] = subject["application_version"] or "1.0"
        subject["build_id"] = "verified-build"
        subject["os"]["version"] = subject["os"]["version"] or "15.7.9"
        subject["os"]["architecture"] = subject["os"]["architecture"] or "arm64"
        subject["model"].update(provider=subject["model"]["provider"] or "provider", id="model",
                                configuration_id="ordinary-default", settings_digest="a" * 64)
        target["access"].update(status="confirmed", account_profile="isolated-benchmark-profile")
        target["isolation"].update(status="confirmed", authorized_roots=["synthetic-project", "new-session-root"],
                                   synthetic_project_id=f"fixture-{target['target_id']}")
        target["artifact"].update(family_id=target["artifact"]["family_id"] or "documented-family",
                                  identity_state="documented", primary_roots=["new-session-root"],
                                  session_join_keys=["session_id"])
        target["provenance"].update(
            fixture_id="session-bench-c01-c02-v1",
            observer_id="observer-v1",
            decoder_id="decoder-v1",
            identity_evidence_sha256="b" * 64,
            access_evidence_sha256="b" * 64,
            isolation_evidence_sha256="b" * 64,
            artifact_evidence_sha256="b" * 64,
        )
    for quota in plan["budget"]["caps"]["quota"]:
        quota.update(source="verified-provider-usage", unit="percentage_points",
                     baseline=10, maximum_delta=3, absolute_stop=13,
                     observed_at=plan["provenance"]["created_at"], evidence_sha256="c" * 64)
    return plan


READY_AS_OF = "2026-09-11T04:35:30Z"


def test_proposed_three_configuration_calibration_is_valid_but_not_authorized():
    plan = validate_campaign_plan(_plan())
    summary = campaign_plan_summary(plan)
    assert {target["subject"]["surface"] for target in plan["targets"]} == {"cli", "desktop"}
    assert summary["scheduled_scenario_runs"] == 7
    assert summary["maximum_attempts"] == 14
    assert summary["maximum_native_sessions"] == 14
    assert plan["execution_authority"] == "none_preparation_only"
    assert plan["live_runs_completed"] == 0


def test_required_target_identity_fields_cannot_be_omitted():
    plan = _plan()
    plan["targets"][0]["subject"].pop("surface")
    with pytest.raises(ValueError, match="missing required fields"):
        validate_campaign_plan(plan)


def test_ready_plan_requires_complete_identity_access_isolation_and_quota():
    plan = _plan()
    plan["state"] = "ready_for_authorization"
    with pytest.raises(ValueError, match="unresolved build|confirmed access|confirmed isolation|artifact identity|quota"):
        validate_campaign_plan(plan, as_of=READY_AS_OF)
    validate_campaign_plan(_ready(_plan()), as_of=READY_AS_OF)


@pytest.mark.parametrize(("mutation", "message"), [
    (lambda plan: plan["targets"][0]["provenance"].update(decoder_id="unimplemented"), "fixture, observer, and decoder"),
    (lambda plan: plan["targets"][0]["artifact"].update(session_join_keys=[]), "session join identity"),
    (lambda plan: plan["targets"][0]["provenance"].update(artifact_evidence_sha256=None), "immutable identity"),
    (lambda plan: plan["budget"]["caps"]["quota"][0].update(observed_at=None), "complete quota"),
    (lambda plan: plan["budget"]["caps"]["quota"][0].update(evidence_sha256=None), "complete quota"),
])
def test_ready_plan_rejects_placeholder_or_unproven_measurement_inputs(mutation, message):
    plan = _ready(_plan())
    mutation(plan)
    with pytest.raises(ValueError, match=message):
        validate_campaign_plan(plan, as_of=READY_AS_OF)


@pytest.mark.parametrize("observed_at", ["2026-09-11T04:34:29Z", "2026-09-11T04:35:31Z", "2026-09-11T04:35:00"])
def test_ready_quota_observation_must_be_recent_prior_and_timezone_aware(observed_at):
    plan = _ready(_plan())
    plan["budget"]["caps"]["quota"][0]["observed_at"] = observed_at
    with pytest.raises(ValueError, match="fresh|UTC offset"):
        validate_campaign_plan(plan, as_of=READY_AS_OF)


def test_ready_validation_requires_independent_authorization_time():
    with pytest.raises(ValueError, match="independent as_of"):
        validate_campaign_plan(_ready(_plan()))


def test_surface_and_launch_mode_must_match():
    plan = _plan()
    plan["targets"][1]["subject"]["launch_mode"] = "interactive_local"
    with pytest.raises(ValueError, match="incompatible"):
        validate_campaign_plan(plan)


def test_native_and_export_tracks_are_mutually_exclusive():
    plan = _plan()
    artifact = plan["targets"][1]["artifact"]
    artifact["track"] = "explicit_export"
    artifact["export"] = {"format": "portable-json", "version": "1", "acquisition": "documented-export-command"}
    validate_campaign_plan(plan)

    artifact["track"] = "native_local"
    with pytest.raises(ValueError, match="cannot carry export"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["targets"][1]["artifact"]["track"] = "explicit_export"
    with pytest.raises(ValueError, match="requires export identity"):
        validate_campaign_plan(plan)


@pytest.mark.parametrize(("collection", "key", "message"), [
    ("targets", "target_id", "duplicate target_id"),
    ("scenarios", "scenario_id", "duplicate scenario_id"),
    ("profiles", "profile_id", "duplicate profile_id"),
    ("schedule", "schedule_id", "duplicate schedule_id"),
])
def test_duplicate_campaign_identities_are_rejected(collection, key, message):
    plan = _plan()
    plan[collection].append(copy.deepcopy(plan[collection][0]))
    with pytest.raises(ValueError, match=message):
        validate_campaign_plan(plan)


def test_dangling_and_inapplicable_schedule_references_are_rejected():
    plan = _plan()
    plan["schedule"][0]["profile_id"] = "missing"
    with pytest.raises(ValueError, match="dangling"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["schedule"][-1]["target_id"] = "goose-cli-local"
    with pytest.raises(ValueError, match="outside profile applicability"):
        validate_campaign_plan(plan)


@pytest.mark.parametrize(("field", "value"), [
    ("scenario_runs", 6),
    ("maximum_attempts", 13),
    ("maximum_native_sessions", 13),
])
def test_declared_run_attempt_and_session_arithmetic_is_exact(field, value):
    plan = _plan()
    plan["budget"]["declared"][field] = value
    with pytest.raises(ValueError, match="arithmetic"):
        validate_campaign_plan(plan)


def test_c04_two_session_multiplier_is_charged_to_every_attempt():
    plan = _plan()
    plan["scenarios"].append({"scenario_id": "C04", "scenario_class": "core",
                              "assertion_set": "v1-c04-portability",
                              "mandatory": True, "native_sessions_per_run": 2})
    plan["profiles"][0]["scenario_ids"].append("C04")
    plan["budget"]["declared"] = {
        "scenario_runs": 10,
        "maximum_attempts": 20,
        "maximum_native_sessions": 26,
    }
    validate_campaign_plan(plan)
    plan["budget"]["declared"]["maximum_native_sessions"] = 25
    with pytest.raises(ValueError, match="arithmetic"):
        validate_campaign_plan(plan)


def test_mandatory_profiles_and_scenarios_cannot_be_omitted():
    plan = _plan()
    plan["schedule"].pop()
    with pytest.raises(ValueError, match="mandatory profile"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["profiles"][0]["scenario_ids"].remove("C02")
    with pytest.raises(ValueError, match="omits mandatory scenarios"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["profiles"][1]["mandatory"] = False
    plan["schedule"].pop()
    plan["budget"]["declared"] = {
        "scenario_runs": 6,
        "maximum_attempts": 12,
        "maximum_native_sessions": 12,
    }
    with pytest.raises(ValueError, match="advanced profile"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["profiles"][0]["scenario_ids"].append("E01-branches")
    plan["profiles"].pop()
    plan["schedule"].pop()
    plan["budget"]["declared"] = {
        "scenario_runs": 9,
        "maximum_attempts": 18,
        "maximum_native_sessions": 18,
    }
    with pytest.raises(ValueError, match="baseline profiles may contain only core"):
        validate_campaign_plan(plan)


def test_calibration_target_shape_is_fixed():
    plan = _plan()
    plan["targets"][1]["subject"].update(surface="cli", launch_mode="interactive_local")
    with pytest.raises(ValueError, match="spanning CLI and desktop"):
        validate_campaign_plan(plan, as_of=READY_AS_OF)


def test_budget_quota_retry_and_forbidden_boundaries_fail_closed():
    plan = _plan()
    plan["budget"]["caps"]["artifact_total_bytes"] = 1
    with pytest.raises(ValueError, match="total-byte"):
        validate_campaign_plan(plan)

    plan = _ready(_plan())
    plan["budget"]["caps"]["quota"][0]["absolute_stop"] = 12
    with pytest.raises(ValueError, match="baseline plus maximum delta"):
        validate_campaign_plan(plan)

    plan = _ready(_plan())
    plan["budget"]["caps"]["quota"][0].update(baseline=10.1, maximum_delta=0.2, absolute_stop=10.3)
    validate_campaign_plan(plan, as_of=READY_AS_OF)

    plan = _plan()
    plan["budget"]["caps"]["quota"].pop()
    with pytest.raises(ValueError, match="exactly one quota cap"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["forbidden_operations"].remove("publication")
    with pytest.raises(ValueError, match="forbidden operation"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["retry_policy"]["uncertain_submitted_attempt"] = "retry"
    with pytest.raises(ValueError, match="do_not_duplicate"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["retry_policy"]["allowed_reasons"] = ["product failure"]
    with pytest.raises(ValueError, match="corrected harness or capture defect"):
        validate_campaign_plan(plan)


def test_sources_and_creation_dates_are_valid_and_resolve():
    plan = _plan()
    plan["targets"][0]["provenance"]["source_ids"] = ["missing"]
    with pytest.raises(ValueError, match="dangling source"):
        validate_campaign_plan(plan)

    plan = _plan()
    plan["provenance"]["source_records"][0]["inspected_at"] = "2026-02-31"
    with pytest.raises(ValueError, match="inspection date"):
        validate_campaign_plan(plan)


def test_cli_reports_canonical_preparation_summary():
    result = subprocess.run(
        [sys.executable, "-m", "session_bench", "validate-campaign-plan", str(PLAN_PATH)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["plan_id"] == "l1-three-configuration-calibration"
    assert summary["scheduled_scenario_runs"] == 7
    assert len(summary["plan_sha256"]) == 64


def test_existing_codex_f0_contract_and_digest_are_unchanged():
    f0 = read_json(REPO / "docs" / "live" / "codex-cli-f0-run-plan.json")
    assert plan_sha256(f0) == "0f427b85ceff5dc46d0ca8141ff5093af51718b2898efc1b1f64cbf21a761494"
