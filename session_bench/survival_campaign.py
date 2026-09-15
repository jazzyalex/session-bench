"""Frozen campaign-plan validation for Session-Bench survival-v1."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Mapping


CONFIGURATIONS = (
    "codex-cli",
    "codex-desktop",
    "cursor-cli",
    "cursor-desktop",
    "opencode-cli",
)
# Prospective five-configuration cohort: the frozen Cursor pair is replaced by
# the two Claude surfaces. The frozen CONFIGURATIONS tuple above is unchanged.
PROSPECTIVE_CONFIGURATIONS = (
    "codex-cli",
    "codex-desktop",
    "claude-cli",
    "claude-desktop",
    "opencode-cli",
)
PROSPECTIVE_STATE = "proposed_prospective"
# Exact build/model identities recorded in public docs only:
# - Codex CLI 0.154.0, Codex Desktop 26.908.40834/8881, OpenCode 1.18.30 with
#   opencode/muse-spark-1.3-contributor-free (frozen campaign.json).
# - Claude Code CLI 2.1.270, Claude Desktop Code (Local) 1.52386.3
#   (docs/survival-v1/shared-format-assessment.md,
#   docs/survival-v1/preliminary-report.md). Claude model IDs are not yet
#   bound; calibration must record them before any evaluated submission.
PROSPECTIVE_EXPECTED_IDENTITIES = {
    "codex-cli": {
        "version": "0.154.0",
        "build": None,
        "executable": "/opt/homebrew/bin/codex",
    },
    "codex-desktop": {
        "version": "26.908.40834",
        "build": "8881",
        "bundle": "com.openai.codex",
        "bundled_cli": "0.154.0-alpha.6.2",
    },
    "claude-cli": {
        "version": "2.1.270",
        "build": None,
        "model": None,
    },
    "claude-desktop": {
        "version": "1.52386.3",
        "build": None,
        "model": None,
    },
    "opencode-cli": {
        "version": "1.18.30",
        "build": None,
        "executable": "/opt/homebrew/bin/opencode",
        "model": "opencode/muse-spark-1.3-contributor-free",
    },
}
PROSPECTIVE_EXPECTED_SURFACES = {
    "codex-cli": "cli",
    "codex-desktop": "desktop",
    "claude-cli": "cli",
    "claude-desktop": "desktop",
    "opencode-cli": "cli",
}
KINDS = {"calibration", "evaluated", "crash_qualification"}
STATES = {"scheduled", "blocked", "started", "invalid", "captured", "complete"}
CORRECTION_STATES = {"blocked", "invalid", "captured", "complete"}
FROZEN_INPUT_PATHS = {
    "protocol_sha256": Path("docs/survival-v1/protocol.md"),
    "rubric_sha256": Path("docs/survival-v1/rubric.md"),
    "workload_sha256": Path("fixtures/scenarios/survival-v1/workload/workload.json"),
    "observer_truth_sha256": Path("fixtures/scenarios/survival-v1/workload/observer-truth.json"),
    "response_canaries_sha256": Path("fixtures/scenarios/survival-v1/workload/response-canaries.json"),
}


def read_campaign(path: Path) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    value = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("campaign must be a JSON object")
    return value


def validate_campaign(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    if plan.get("schema_version") != "1.0-survival-campaign":
        raise ValueError("unknown survival campaign schema")
    if plan.get("protocol_version") != "1.0-survival":
        raise ValueError("campaign protocol is not frozen survival-v1")
    if plan.get("state") != "authorized_bounded_calibration":
        raise ValueError("campaign does not carry bounded calibration authority")

    frozen = plan.get("frozen_inputs")
    if not isinstance(frozen, dict) or set(frozen) != {
        "protocol_sha256", "rubric_sha256", "workload_sha256",
        "observer_truth_sha256", "response_canaries_sha256",
    }:
        raise ValueError("campaign frozen input set is incomplete")
    for digest in frozen.values():
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("campaign input digest must be SHA-256")

    quota = plan.get("quota")
    if not isinstance(quota, dict):
        raise ValueError("campaign quota contract is missing")
    if quota.get("source") != "codex_app_account_usage":
        raise ValueError("campaign quota source is not account-qualified")
    baseline = quota.get("baseline_used_percent")
    stop = quota.get("absolute_stop_used_percent")
    if type(baseline) is not int or type(stop) is not int or not (0 <= baseline < stop <= 100):
        raise ValueError("campaign quota values are invalid")
    if quota.get("check") != "before_every_model_submission":
        raise ValueError("campaign must recheck quota before every model submission")
    if quota.get("on_stop") != "no_new_submission_finish_in_flight_capture_only":
        raise ValueError("campaign quota stop action is not fail closed")

    configurations = plan.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("campaign configurations are missing")
    config_ids = [item.get("configuration_id") for item in configurations if isinstance(item, dict)]
    if tuple(config_ids) != CONFIGURATIONS:
        raise ValueError("campaign configuration order or population changed")
    if any(item.get("surface") not in {"cli", "desktop"} for item in configurations):
        raise ValueError("campaign surface identity is invalid")
    if len({json.dumps(item.get("identity"), sort_keys=True) for item in configurations}) != len(CONFIGURATIONS):
        raise ValueError("campaign configuration identities must remain distinct")
    for item in configurations:
        usage = item.get("usage_budget")
        if not isinstance(usage, dict) or set(usage) != {
            "billing_authority", "paid_submission_allowed", "limit_source"
        }:
            raise ValueError("each configuration must declare its submission budget")
        if not isinstance(usage["billing_authority"], str) or not usage["billing_authority"]:
            raise ValueError("configuration billing authority is missing")
        if type(usage["paid_submission_allowed"]) is not bool:
            raise ValueError("configuration paid-submission policy is invalid")
        if not isinstance(usage["limit_source"], str) or not usage["limit_source"]:
            raise ValueError("configuration limit source is missing")

    attempts = plan.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 25:
        raise ValueError("campaign must retain exactly 25 scheduled base attempts")
    ids: set[str] = set()
    by_configuration: dict[str, list[Mapping[str, Any]]] = {key: [] for key in CONFIGURATIONS}
    for attempt in attempts:
        if not isinstance(attempt, dict) or set(attempt) != {
            "attempt_id", "configuration_id", "kind", "repetition", "state"
        }:
            raise ValueError("attempt record shape changed")
        attempt_id = attempt["attempt_id"]
        if not isinstance(attempt_id, str) or attempt_id in ids:
            raise ValueError("attempt IDs must be unique strings")
        ids.add(attempt_id)
        configuration_id = attempt["configuration_id"]
        if configuration_id not in by_configuration:
            raise ValueError("attempt references unknown configuration")
        if attempt["kind"] not in KINDS or attempt["state"] not in STATES:
            raise ValueError("attempt kind or state is invalid")
        by_configuration[configuration_id].append(attempt)

    for configuration_id, rows in by_configuration.items():
        calibrations = [row for row in rows if row["kind"] == "calibration"]
        evaluations = [row for row in rows if row["kind"] == "evaluated"]
        crashes = [row for row in rows if row["kind"] == "crash_qualification"]
        if len(calibrations) != 1 or calibrations[0]["repetition"] is not None:
            raise ValueError(f"{configuration_id} calibration schedule changed")
        if [row["repetition"] for row in evaluations] != [1, 2, 3]:
            raise ValueError(f"{configuration_id} evaluation repetitions changed")
        if len(crashes) != 1 or crashes[0]["repetition"] is not None:
            raise ValueError(f"{configuration_id} crash schedule changed")

    corrections = plan.get("setup_corrections", [])
    if not isinstance(corrections, list) or len(corrections) > len(CONFIGURATIONS):
        raise ValueError("campaign setup corrections exceed the one-per-configuration cap")
    corrected: set[str] = set()
    for correction in corrections:
        if not isinstance(correction, dict) or set(correction) != {
            "attempt_id", "configuration_id", "corrects", "state", "reason_ids"
        }:
            raise ValueError("setup correction record shape changed")
        configuration_id = correction["configuration_id"]
        if configuration_id not in by_configuration or configuration_id in corrected:
            raise ValueError("setup correction configuration is invalid or duplicated")
        corrected.add(configuration_id)
        if correction["attempt_id"] != f"{configuration_id}-setup-2":
            raise ValueError("setup correction ID is not canonical")
        if correction["corrects"] != f"{configuration_id}-cal-1":
            raise ValueError("setup correction does not bind to its calibration")
        if correction["state"] not in CORRECTION_STATES:
            raise ValueError("setup correction state is invalid")
        if not isinstance(correction["reason_ids"], list) or not all(
            isinstance(reason, str) and reason for reason in correction["reason_ids"]
        ):
            raise ValueError("setup correction reasons are invalid")
    return plan


def validate_prospective_campaign(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate a prospective (unscored, preflight-only) campaign plan.

    The frozen campaign keeps its own validator and semantics. This path
    accepts only the new five-configuration cohort in exact order, exact
    build/model identities, 25 scheduled slots (1 calibration + 3 evaluated
    + 1 crash per configuration), and explicit score ineligibility.
    Auth/runtime/capture failures are never evidence of format quality:
    every base attempt must remain ``scheduled``.
    """
    if plan.get("schema_version") != "1.0-survival-campaign":
        raise ValueError("unknown survival campaign schema")
    if plan.get("protocol_version") != "1.0-survival":
        raise ValueError("campaign protocol is not frozen survival-v1")
    if plan.get("state") != PROSPECTIVE_STATE:
        raise ValueError("prospective campaign must be preflight-only")
    if plan.get("score_eligible") is not False:
        raise ValueError("prospective campaign must keep score eligibility false")

    frozen = plan.get("frozen_inputs")
    if not isinstance(frozen, dict) or set(frozen) != {
        "protocol_sha256", "rubric_sha256", "workload_sha256",
        "observer_truth_sha256", "response_canaries_sha256",
    }:
        raise ValueError("campaign frozen input set is incomplete")
    for digest in frozen.values():
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("campaign input digest must be SHA-256")

    publication = plan.get("publication")
    if not isinstance(publication, dict):
        raise ValueError("prospective campaign publication contract is missing")
    if publication.get("live_vendor_claims_allowed") is not False:
        raise ValueError("prospective campaign must allow no live vendor claims")

    quota = plan.get("quota")
    if not isinstance(quota, dict):
        raise ValueError("campaign quota contract is missing")
    if quota.get("source") != "codex_app_account_usage":
        raise ValueError("campaign quota source is not account-qualified")
    baseline = quota.get("baseline_used_percent")
    stop = quota.get("absolute_stop_used_percent")
    if type(baseline) is not int or type(stop) is not int or not (0 <= baseline < stop <= 100):
        raise ValueError("campaign quota values are invalid")
    if quota.get("check") != "before_every_model_submission":
        raise ValueError("campaign must recheck quota before every model submission")
    if quota.get("on_stop") != "no_new_submission_finish_in_flight_capture_only":
        raise ValueError("campaign quota stop action is not fail closed")

    configurations = plan.get("configurations")
    if not isinstance(configurations, list):
        raise ValueError("campaign configurations are missing")
    config_ids = [item.get("configuration_id") for item in configurations if isinstance(item, dict)]
    if tuple(config_ids) != PROSPECTIVE_CONFIGURATIONS:
        raise ValueError("prospective configuration order or population changed")
    if any(item.get("surface") not in {"cli", "desktop"} for item in configurations):
        raise ValueError("campaign surface identity is invalid")
    if len({json.dumps(item.get("identity"), sort_keys=True) for item in configurations}) != len(
        PROSPECTIVE_CONFIGURATIONS
    ):
        raise ValueError("campaign configuration identities must remain distinct")
    for item in configurations:
        configuration_id = item.get("configuration_id")
        expected_identity = PROSPECTIVE_EXPECTED_IDENTITIES.get(configuration_id)
        if expected_identity is None:
            raise ValueError(f"unknown prospective configuration: {configuration_id}")
        if item.get("surface") != PROSPECTIVE_EXPECTED_SURFACES[configuration_id]:
            raise ValueError(f"{configuration_id} surface identity changed")
        if item.get("identity") != expected_identity:
            raise ValueError(f"{configuration_id} build/model identity changed")
        usage = item.get("usage_budget")
        if not isinstance(usage, dict) or set(usage) != {
            "billing_authority", "paid_submission_allowed", "limit_source"
        }:
            raise ValueError("each configuration must declare its submission budget")
        if not isinstance(usage["billing_authority"], str) or not usage["billing_authority"]:
            raise ValueError("configuration billing authority is missing")
        if type(usage["paid_submission_allowed"]) is not bool:
            raise ValueError("configuration paid-submission policy is invalid")
        if not isinstance(usage["limit_source"], str) or not usage["limit_source"]:
            raise ValueError("configuration limit source is missing")

    attempts = plan.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 25:
        raise ValueError("campaign must retain exactly 25 scheduled base attempts")
    ids: set[str] = set()
    by_configuration: dict[str, list[Mapping[str, Any]]] = {key: [] for key in PROSPECTIVE_CONFIGURATIONS}
    for attempt in attempts:
        if not isinstance(attempt, dict) or set(attempt) != {
            "attempt_id", "configuration_id", "kind", "repetition", "state"
        }:
            raise ValueError("attempt record shape changed")
        attempt_id = attempt["attempt_id"]
        if not isinstance(attempt_id, str) or attempt_id in ids:
            raise ValueError("attempt IDs must be unique strings")
        ids.add(attempt_id)
        configuration_id = attempt["configuration_id"]
        if configuration_id not in by_configuration:
            raise ValueError("attempt references unknown configuration")
        if attempt["kind"] not in KINDS:
            raise ValueError("attempt kind or state is invalid")
        # Prospective slots are preflight-only: auth/runtime/capture outcomes
        # are invalid/N/A, never format-quality evidence.
        if attempt["state"] != "scheduled":
            raise ValueError("prospective attempts must remain scheduled")
        by_configuration[configuration_id].append(attempt)

    for configuration_id, rows in by_configuration.items():
        calibrations = [row for row in rows if row["kind"] == "calibration"]
        evaluations = [row for row in rows if row["kind"] == "evaluated"]
        crashes = [row for row in rows if row["kind"] == "crash_qualification"]
        if len(calibrations) != 1 or calibrations[0]["repetition"] is not None:
            raise ValueError(f"{configuration_id} calibration schedule changed")
        if [row["repetition"] for row in evaluations] != [1, 2, 3]:
            raise ValueError(f"{configuration_id} evaluation repetitions changed")
        if len(crashes) != 1 or crashes[0]["repetition"] is not None:
            raise ValueError(f"{configuration_id} crash schedule changed")

    corrections = plan.get("setup_corrections", [])
    if corrections != []:
        raise ValueError("prospective campaign must start with no setup corrections")
    return plan


def validate_prospective_frozen_inputs(
    plan: Mapping[str, Any], repository_root: Path
) -> Mapping[str, str]:
    """Verify prospective digests against exact frozen input bytes."""
    validate_prospective_campaign(plan)
    root = Path(repository_root)
    actual: dict[str, str] = {}
    for digest_key, relative_path in FROZEN_INPUT_PATHS.items():
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"frozen input is missing: {relative_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        actual[digest_key] = digest
        if digest != plan["frozen_inputs"][digest_key]:
            raise ValueError(f"frozen input digest mismatch: {relative_path}")
    return actual


def prospective_campaign_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_prospective_campaign(plan)
    states: dict[str, int] = {}
    for attempt in plan["attempts"]:
        states[attempt["state"]] = states.get(attempt["state"], 0) + 1
    return {
        "state": plan["state"],
        "configurations": len(plan["configurations"]),
        "attempts": len(plan["attempts"]),
        "setup_corrections": len(plan.get("setup_corrections", [])),
        "attempt_states": states,
        "quota_baseline_used_percent": plan["quota"]["baseline_used_percent"],
        "quota_stop_used_percent": plan["quota"]["absolute_stop_used_percent"],
        "score_eligible": False,
    }


def validate_frozen_inputs(plan: Mapping[str, Any], repository_root: Path) -> Mapping[str, str]:
    """Verify that every campaign digest still names the exact frozen input bytes."""
    validate_campaign(plan)
    root = Path(repository_root)
    actual: dict[str, str] = {}
    for digest_key, relative_path in FROZEN_INPUT_PATHS.items():
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"frozen input is missing: {relative_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        actual[digest_key] = digest
        if digest != plan["frozen_inputs"][digest_key]:
            raise ValueError(f"frozen input digest mismatch: {relative_path}")
    return actual


def quota_allows_submission(plan: Mapping[str, Any], current_used_percent: int | None) -> bool:
    validate_campaign(plan)
    if type(current_used_percent) is not int:
        return False
    return current_used_percent < plan["quota"]["absolute_stop_used_percent"]


def campaign_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_campaign(plan)
    states: dict[str, int] = {}
    for attempt in plan["attempts"]:
        states[attempt["state"]] = states.get(attempt["state"], 0) + 1
    return {
        "state": plan["state"],
        "configurations": len(plan["configurations"]),
        "attempts": len(plan["attempts"]),
        "setup_corrections": len(plan.get("setup_corrections", [])),
        "attempt_states": states,
        "quota_baseline_used_percent": plan["quota"]["baseline_used_percent"],
        "quota_stop_used_percent": plan["quota"]["absolute_stop_used_percent"],
    }
