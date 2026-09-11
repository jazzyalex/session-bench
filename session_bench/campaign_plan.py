"""Pure offline validation for staged multi-surface campaign preparation."""

from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path

from .bundle import canonical, digest
from .schema import validate


SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "campaign" / "v1" / "campaign_plan.schema.json"
IMPLEMENTATION_REGISTRY = (Path(__file__).resolve().parent.parent / "registries" / "campaign" / "v1"
                           / "implemented.json")
REQUIRED_FORBIDDEN = {
    "private_history_collection",
    "credential_copy_or_inspection",
    "purchase_or_credit_redemption",
    "publication",
    "cross_repository_access",
    "unapproved_surface_expansion",
}
LAUNCH_MODES = {
    "cli": {"interactive_local", "one_shot_local"},
    "desktop": {"desktop_local"},
    "ide": {"ide_local"},
}
REQUIRED_RETRY_REASONS = {"clearly corrected harness or capture defect"}


def _unique(items, key, context):
    values = [item[key] for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {key} in {context}")


def _known(value):
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return lowered not in {"", "unknown", "unconfirmed", "unimplemented"} and "proposed" not in lowered


def _timestamp(value, context):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{context} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{context} timestamp requires a UTC offset")
    return parsed


def _load_schema():
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def _load_implementation_registry():
    return json.loads(IMPLEMENTATION_REGISTRY.read_text(encoding="utf-8"))


def _implementation_ids(registry, category):
    items = registry.get(category)
    if not isinstance(items, list):
        raise ValueError(f"implementation registry lacks {category}")
    _unique(items, "id", f"implementation registry {category}")
    for item in items:
        if (set(item) != {"id", "implementation_path"} or not _known(item["id"])
                or not _known(item["implementation_path"])):
            raise ValueError(f"implementation registry has invalid {category} entry")
        implementation_path = Path(item["implementation_path"])
        repository_root = Path(__file__).resolve().parent.parent
        resolved = (repository_root / implementation_path).resolve()
        if (implementation_path.is_absolute() or not resolved.is_relative_to(repository_root) or not resolved.is_file()):
            raise ValueError(f"implementation registry {category} path does not resolve inside repository")
    return {item["id"] for item in items}


def validate_campaign_plan(plan, as_of=None, implementation_registry=None):
    """Validate structure, cross-references, arithmetic, and readiness without I/O."""
    validate(plan, _load_schema())
    targets, scenarios = plan["targets"], plan["scenarios"]
    profiles, schedule = plan["profiles"], plan["schedule"]
    _unique(targets, "target_id", "campaign targets")
    _unique(scenarios, "scenario_id", "campaign scenarios")
    _unique(profiles, "profile_id", "campaign profiles")
    _unique(schedule, "schedule_id", "campaign schedule")
    _unique(plan["provenance"]["source_records"], "source_id", "campaign sources")
    _unique(plan["budget"]["caps"]["quota"], "quota_id", "campaign quota caps")

    target_by_id = {item["target_id"]: item for item in targets}
    scenario_by_id = {item["scenario_id"]: item for item in scenarios}
    profile_by_id = {item["profile_id"]: item for item in profiles}
    source_ids = {item["source_id"] for item in plan["provenance"]["source_records"]}
    identities = set()
    for target in targets:
        target_id = target["target_id"]
        subject = target["subject"]
        identity = (subject["harness"], subject["surface"], subject["launch_mode"])
        if identity in identities:
            raise ValueError("duplicate harness/surface/launch identity")
        identities.add(identity)
        if subject["launch_mode"] not in LAUNCH_MODES[subject["surface"]]:
            raise ValueError("launch mode is incompatible with target surface")
        dangling_sources = set(target["provenance"]["source_ids"]) - source_ids
        if dangling_sources:
            raise ValueError(f"target {target_id} has dangling source IDs: {sorted(dangling_sources)}")
        artifact = target["artifact"]
        if artifact["track"] == "native_local":
            if artifact["export"] is not None:
                raise ValueError("native-local target cannot carry export identity")
        elif artifact["export"] is None or artifact["primary_roots"]:
            raise ValueError("explicit export requires export identity and no authoritative native roots")

    for profile in profiles:
        if len(profile["scenario_ids"]) != len(set(profile["scenario_ids"])):
            raise ValueError("duplicate scenario in profile")
        if len(profile["applies_to_target_ids"]) != len(set(profile["applies_to_target_ids"])):
            raise ValueError("duplicate target in profile applicability")
        if set(profile["scenario_ids"]) - set(scenario_by_id):
            raise ValueError("profile references unknown scenario")
        if set(profile["applies_to_target_ids"]) - set(target_by_id):
            raise ValueError("profile references unknown target")

    scheduled_pairs = set()
    runs = attempts = native_sessions = 0
    advanced_schedule = []
    retry_multiplier = 1 + plan["retry_policy"]["maximum_retries_per_scenario"]
    scheduled_scenarios = {target_id: set() for target_id in target_by_id}
    for item in schedule:
        target_id, profile_id = item["target_id"], item["profile_id"]
        if target_id not in target_by_id or profile_id not in profile_by_id:
            raise ValueError("schedule has dangling target or profile reference")
        profile = profile_by_id[profile_id]
        if target_id not in profile["applies_to_target_ids"]:
            raise ValueError("schedule target is outside profile applicability")
        pair = (target_id, profile_id)
        if pair in scheduled_pairs:
            raise ValueError("target/profile pair is scheduled more than once")
        scheduled_pairs.add(pair)
        if item["repetitions"] < profile["minimum_repetitions"]:
            raise ValueError("schedule is below profile minimum repetitions")
        for scenario_id in profile["scenario_ids"]:
            scenario = scenario_by_id[scenario_id]
            scenario_runs = item["repetitions"]
            runs += scenario_runs
            attempts += scenario_runs * retry_multiplier
            native_sessions += scenario_runs * scenario["native_sessions_per_run"] * retry_multiplier
            scheduled_scenarios[target_id].add(scenario_id)
        if profile["profile_class"] == "advanced":
            advanced_schedule.append(item)

    for profile in profiles:
        if profile["mandatory"]:
            missing = {
                (target_id, profile["profile_id"])
                for target_id in profile["applies_to_target_ids"]
            } - scheduled_pairs
            if missing:
                raise ValueError("mandatory profile is not scheduled for every applicable target")
    mandatory_scenarios = {item["scenario_id"] for item in scenarios if item["mandatory"]}
    for target_id, present in scheduled_scenarios.items():
        if mandatory_scenarios - present:
            raise ValueError(f"target {target_id} omits mandatory scenarios")
    if plan["phase"] == "calibration":
        surfaces = {target["subject"]["surface"] for target in targets}
        if len(targets) != 3 or not {"cli", "desktop"}.issubset(surfaces):
            raise ValueError("calibration requires exactly three targets spanning CLI and desktop")
        if not {"C01", "C02"}.issubset(mandatory_scenarios):
            raise ValueError("calibration must declare C01 and C02 mandatory")
        baseline_profiles = [profile for profile in profiles if profile["profile_class"] == "baseline"]
        advanced_profiles = [profile for profile in profiles if profile["profile_class"] == "advanced"]
        if any(any(scenario_by_id[scenario_id]["scenario_class"] != "core"
                   for scenario_id in profile["scenario_ids"]) for profile in baseline_profiles):
            raise ValueError("calibration baseline profiles may contain only core scenarios")
        if any(any(scenario_by_id[scenario_id]["scenario_class"] != "advanced"
                   for scenario_id in profile["scenario_ids"]) for profile in advanced_profiles):
            raise ValueError("calibration advanced profiles may contain only advanced scenarios")
        baseline_schedule = [item for item in schedule
                             if profile_by_id[item["profile_id"]]["profile_class"] == "baseline"]
        if (len(baseline_profiles) != 1 or set(baseline_profiles[0]["scenario_ids"]) != {"C01", "C02"}
                or len(baseline_schedule) != len(targets)
                or {item["target_id"] for item in baseline_schedule} != set(target_by_id)
                or any(item["profile_id"] != baseline_profiles[0]["profile_id"]
                       or item["repetitions"] != 1 for item in baseline_schedule)):
            raise ValueError("calibration requires exactly C01 and C02 once per target in one baseline profile")
        if (len(advanced_profiles) != 1 or len(advanced_profiles[0]["scenario_ids"]) != 1
                or len(advanced_schedule) != 1
                or advanced_schedule[0]["profile_id"] != advanced_profiles[0]["profile_id"]
                or advanced_schedule[0]["repetitions"] != 1):
            raise ValueError("calibration requires exactly one separately scheduled single-run advanced profile")

    declared = plan["budget"]["declared"]
    computed = {
        "scenario_runs": runs,
        "maximum_attempts": attempts,
        "maximum_native_sessions": native_sessions,
    }
    if declared != computed:
        raise ValueError(f"declared campaign arithmetic differs from schedule: expected {computed}")
    caps = plan["budget"]["caps"]
    if caps["artifact_total_bytes"] < caps["artifact_file_bytes"]:
        raise ValueError("artifact total-byte cap is smaller than per-file cap")
    spend = caps["spend"]
    if spend["maximum"] == 0 and not _known(spend["zero_cost_reason"]):
        raise ValueError("zero spend cap requires an explicit zero-cost reason")
    if spend["maximum"] > 0 and spend["zero_cost_reason"] is not None:
        raise ValueError("positive spend cap cannot claim a zero-cost reason")

    quota = caps["quota"]
    if {item["target_id"] for item in quota} != set(target_by_id) or len(quota) != len(target_by_id):
        raise ValueError("campaign requires exactly one quota cap per target")
    for item in quota:
        if item["unit"] == "percentage_points" and all(
            value is not None for value in (item["baseline"], item["maximum_delta"], item["absolute_stop"])
        ) and Decimal(str(item["baseline"])) + Decimal(str(item["maximum_delta"])) != Decimal(str(item["absolute_stop"])):
            raise ValueError("percentage-point quota stop must equal baseline plus maximum delta")

    retry = plan["retry_policy"]
    if (set(retry["allowed_reasons"]) != REQUIRED_RETRY_REASONS
            or len(retry["allowed_reasons"]) != len(REQUIRED_RETRY_REASONS)):
        raise ValueError("retry reasons must be limited to a corrected harness or capture defect")
    if set(plan["forbidden_operations"]) != REQUIRED_FORBIDDEN or len(plan["forbidden_operations"]) != len(REQUIRED_FORBIDDEN):
        raise ValueError("forbidden operation set is incomplete or duplicated")

    for source in plan["provenance"]["source_records"]:
        try:
            date.fromisoformat(source["inspected_at"])
        except ValueError as exc:
            raise ValueError("source inspection date is invalid") from exc
    created_at = _timestamp(plan["provenance"]["created_at"], "campaign creation")

    if plan["state"] == "ready_for_authorization":
        if as_of is None:
            raise ValueError("ready validation requires an independent as_of authorization time")
        authorization_time = _timestamp(as_of, "authorization as_of")
        if created_at > authorization_time:
            raise ValueError("campaign creation cannot be later than authorization as_of")
        registry = _load_implementation_registry() if implementation_registry is None else implementation_registry
        if (set(registry) != {"schema_version", "registry_id", "fixtures", "observers", "decoders",
                             "assertion_sets"}
                or registry["schema_version"] != "1.0-campaign-implementations"
                or not _known(registry["registry_id"])):
            raise ValueError("implementation registry identity or structure is invalid")
        fixture_ids = _implementation_ids(registry, "fixtures")
        observer_ids = _implementation_ids(registry, "observers")
        decoder_ids = _implementation_ids(registry, "decoders")
        assertion_set_ids = _implementation_ids(registry, "assertion_sets")
        scheduled_scenario_ids = set().union(*scheduled_scenarios.values())
        unresolved_assertions = {
            scenario_by_id[scenario_id]["assertion_set"] for scenario_id in scheduled_scenario_ids
        } - assertion_set_ids
        if unresolved_assertions:
            raise ValueError(f"ready plan has unimplemented assertion sets: {sorted(unresolved_assertions)}")
        for target in targets:
            subject = target["subject"]
            required_identity = [subject["application_version"], subject["build_id"], subject["os"]["version"],
                                 subject["os"]["architecture"], subject["model"]["provider"], subject["model"]["id"],
                                 subject["model"]["configuration_id"], subject["model"]["settings_digest"]]
            if not all(_known(value) for value in required_identity):
                raise ValueError("ready target has unresolved build, OS, model, or configuration identity")
            if target["access"]["status"] != "confirmed" or not _known(target["access"]["account_profile"]):
                raise ValueError("ready target requires confirmed access and account/profile boundary")
            isolation = target["isolation"]
            if (isolation["status"] != "confirmed" or not isolation["authorized_roots"]
                    or not _known(isolation["synthetic_project_id"])):
                raise ValueError("ready target requires confirmed isolation, roots, and synthetic project")
            artifact = target["artifact"]
            if not _known(artifact["family_id"]) or artifact["identity_state"] == "candidate":
                raise ValueError("ready target requires documented or verified artifact identity")
            roots = artifact["primary_roots"]
            if artifact["track"] == "native_local" and (
                    not roots or not set(roots).issubset(isolation["authorized_roots"])):
                raise ValueError("ready native-local roots must be declared within authorized isolation roots")
            if (len(roots) != len(set(roots)) or not all(_known(value) for value in roots)
                    or len(isolation["authorized_roots"]) != len(set(isolation["authorized_roots"]))
                    or not all(_known(value) for value in isolation["authorized_roots"])):
                raise ValueError("ready target roots must be unique and non-placeholder")
            if (not artifact["session_join_keys"]
                    or len(artifact["session_join_keys"]) != len(set(artifact["session_join_keys"]))
                    or not all(_known(value) for value in artifact["session_join_keys"])):
                raise ValueError("ready target requires native session join identity")
            if artifact["track"] == "explicit_export" and not all(
                    _known(artifact["export"][key]) for key in ("format", "version", "acquisition")):
                raise ValueError("ready explicit export requires resolved format, version, and acquisition")
            target_provenance = target["provenance"]
            if (target_provenance["fixture_id"] not in fixture_ids
                    or target_provenance["observer_id"] not in observer_ids
                    or target_provenance["decoder_id"] not in decoder_ids):
                raise ValueError("ready target requires registry-resolved fixture, observer, and decoder identities")
            if any(target_provenance[key] is None for key in (
                "identity_evidence_sha256", "access_evidence_sha256",
                "isolation_evidence_sha256", "artifact_evidence_sha256",
            )):
                raise ValueError("ready target requires immutable identity, access, isolation, and artifact evidence")
        for item in quota:
            if (not _known(item["source"]) or item["unit"] == "unknown"
                    or any(item[key] is None for key in (
                        "baseline", "maximum_delta", "absolute_stop", "observed_at", "evidence_sha256"
                    ))):
                raise ValueError("ready target requires a complete quota source and stop")
            observed_at = _timestamp(item["observed_at"], "quota observation")
            age_seconds = (authorization_time - observed_at).total_seconds()
            if age_seconds < 0 or age_seconds > item["freshness_seconds"]:
                raise ValueError("ready quota observation is not fresh at authorization as_of")
    return plan


def campaign_plan_summary(plan, as_of=None):
    validate_campaign_plan(plan, as_of=as_of)
    declared = plan["budget"]["declared"]
    return {
        "plan_id": plan["plan_id"],
        "state": plan["state"],
        "targets": len(plan["targets"]),
        "scheduled_scenario_runs": declared["scenario_runs"],
        "maximum_attempts": declared["maximum_attempts"],
        "maximum_native_sessions": declared["maximum_native_sessions"],
        "plan_sha256": digest(canonical(plan)),
    }
