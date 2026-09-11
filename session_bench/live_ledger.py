"""Validation helpers for the append-only L0 attempt/evidence ledger."""
from .schema import validate_named
from .live_plan import plan_sha256, validate_live_plan


def validate_live_ledger(ledger, plan):
    validate_live_plan(plan)
    validate_named(ledger, "live_ledger")
    if ledger["gate_id"] != plan["gate_id"]:
        raise ValueError("ledger gate does not match run plan")
    if ledger["plan_sha256"] != plan_sha256(plan):
        raise ValueError("ledger plan digest does not match run plan")
    if len(ledger["attempts"]) > plan["limits"]["attempts_total"]:
        raise ValueError("attempt ledger exceeds plan attempt limit")
    seen_attempts, seen_sessions, submitted = set(), set(), 0
    totals = {key: 0 for key in ("captured_files", "captured_bytes", "observable_tokens",
                                  "spend_usd", "operator_minutes", "wall_clock_minutes")}
    run_by_scenario = {}
    attempts_by_scenario = {scenario: 0 for scenario in plan["scenarios"]}
    config_identity = None
    for attempt in ledger["attempts"]:
        if attempt["attempt_id"] in seen_attempts:
            raise ValueError("duplicate attempt_id")
        seen_attempts.add(attempt["attempt_id"])
        if config_identity is None:
            config_identity = attempt["config_identity"]
        elif attempt["config_identity"] != config_identity:
            raise ValueError("all attempts must use one effective configuration identity")
        if attempt["scenario_id"] not in attempts_by_scenario:
            raise ValueError("attempt scenario is outside the run plan")
        attempts_by_scenario[attempt["scenario_id"]] += 1
        if attempts_by_scenario[attempt["scenario_id"]] > plan["limits"]["attempts_per_scenario"]:
            raise ValueError("attempt ledger exceeds per-scenario attempt limit")
        existing_run = run_by_scenario.setdefault(attempt["scenario_id"], attempt["scenario_run_id"])
        if existing_run != attempt["scenario_run_id"]:
            raise ValueError("one scheduled scenario must retain one scenario_run_id across retries")
        if len(set(attempt["native_session_ids"])) != len(attempt["native_session_ids"]):
            raise ValueError("duplicate native session identity within attempt")
        if seen_sessions & set(attempt["native_session_ids"]):
            raise ValueError("native session identity reused across attempts")
        seen_sessions.update(attempt["native_session_ids"])
        submitted += attempt["submitted_turns"]
        for key in totals:
            totals[key] += attempt["usage"][key]
        if attempt["submitted_turns"] and attempt["state"] == "planned":
            raise ValueError("submitted turns require a started or terminal execution state")
        if attempt["state"] == "captured" and not attempt["native_session_ids"]:
            raise ValueError("captured attempt requires native session identity")
    if submitted > plan["limits"]["submitted_turns"]:
        raise ValueError("ledger exceeds submitted-turn limit")
    if len(seen_sessions) > plan["limits"]["native_sessions"]:
        raise ValueError("ledger exceeds aggregate native-session limit")
    limit_names = {"captured_files": "artifact_files", "captured_bytes": "artifact_total_bytes",
                   "observable_tokens": "tokens_total_when_observable",
                   "spend_usd": "incremental_spend_usd", "operator_minutes": "operator_minutes",
                   "wall_clock_minutes": "wall_clock_minutes"}
    for key, limit_name in limit_names.items():
        if totals[key] > plan["limits"][limit_name]:
            raise ValueError(f"ledger exceeds {key.replace('_', '-')} limit")
    return ledger


def validate_capture_evidence(evidence, plan, *, attempt):
    """Validate metadata-only discovery and immutable capture claims.

    This contract intentionally accepts metadata records rather than opening files;
    the controller owns the actual stat/copy operation.
    """
    required = {"attempt_id", "scenario_run_id", "native_session_ids", "resolved_config_fingerprint",
                "before_stats", "after_stats", "primary_candidate_path",
                "companion_paths", "candidate_paths", "opened_paths",
                "preexisting_file_hashing", "ambiguous", "source_mutated",
                "observer_frozen", "quiescence_checks", "candidate_identity_verified"}
    missing = required - set(evidence)
    if missing:
        raise ValueError(f"capture evidence missing fields: {sorted(missing)}")
    if evidence["preexisting_file_hashing"] is not False:
        raise ValueError("pre-existing files must not be hashed")
    if not isinstance(evidence["resolved_config_fingerprint"], str) or not evidence["resolved_config_fingerprint"]:
        raise ValueError("capture evidence requires the resolved configuration identity")
    if evidence["quiescence_checks"] < 2 or evidence["candidate_identity_verified"] is not True:
        raise ValueError("capture requires two stable observations and a final identity check")
    if evidence["ambiguous"] and evidence["opened_paths"]:
        raise ValueError("ambiguous discovery cannot open candidates")
    approved = {evidence["primary_candidate_path"], *evidence["companion_paths"]}
    if not evidence["ambiguous"] and set(evidence["candidate_paths"]) != approved:
        raise ValueError("candidate population must equal one primary plus proven companions")
    if set(evidence["opened_paths"]) - set(evidence["candidate_paths"]):
        raise ValueError("opened path was not proven new")
    if evidence["primary_candidate_path"] not in evidence["candidate_paths"]:
        raise ValueError("primary candidate was not proven new")
    if set(evidence["companion_paths"]) - set(evidence["candidate_paths"]):
        raise ValueError("companion was not proven new")
    if (attempt.get("attempt_id") != evidence["attempt_id"]
            or attempt.get("scenario_run_id") != evidence["scenario_run_id"]
            or attempt.get("native_session_ids", []) != evidence["native_session_ids"]):
        raise ValueError("capture evidence identity differs from attempt")
    stat_keys = {"relative_path", "filesystem_id", "birth_time", "ctime", "mtime", "size"}
    for record in evidence["before_stats"] + evidence["after_stats"]:
        if set(record) != stat_keys:
            raise ValueError("capture stat record does not contain the approved metadata fields")
    if attempt["state"] in {"captured", "complete"} and set(evidence["opened_paths"]) != approved:
        raise ValueError("captured attempt must open exactly the primary and proven companions")
    if evidence["source_mutated"]:
        raise ValueError("native source mutation invalidates capture")
    if not evidence["observer_frozen"] and attempt["state"] in {"captured", "complete"}:
        raise ValueError("observer must be frozen before capture is accepted")
    return evidence
