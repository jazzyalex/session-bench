"""Fail-closed validation for the bounded Codex CLI L0/F0 machine contract."""
from .bundle import canonical, digest
from .schema import validate_named

REQUIRED_SCALARS = {
    "web_search": "disabled",
    "sandbox_workspace_write.network_access": False,
    "sandbox_workspace_write.writable_roots": [],
    "hooks": {},
    "project_doc_max_bytes": 0,
}
REQUIRED_DISABLED = {
    "apps", "browser_use", "browser_use_external", "computer_use", "hooks",
    "image_generation", "in_app_browser", "memories", "multi_agent", "plugins",
    "skill_search", "tool_suggest", "workspace_dependencies",
}
REQUIRED_PREFLIGHT = {"feature_states_match", "all_mcp_servers_disabled", "scratch_write_allowed", "undeclared_sibling_write_denied", "outbound_network_denied"}
REQUIRED_FORBIDDEN = {
    "read_preexisting_session_content", "private_history_collection",
    "credential_copy_or_inspection", "model_provider_or_endpoint_switch",
    "purchase_or_credit_redemption", "publication", "L1_or_broader_collection",
}
MCP_POLICY = "enumerate_names_only_then_disable_every_configured_server_and_require_all_disabled"
CONTROL_ORDER = [
    "C02.failing_test_status_and_exit_code", "C02.inspect_target_and_source_bytes",
    "C02.accepted_prompt_bytes", "C01.correction_prompt_bytes", "C01.first_prompt_marker_bytes",
]


def validate_live_plan(plan):
    validate_named(plan, "live_run_plan")
    if plan["limits"]["quota"]["baseline_used_percent"] + plan["limits"]["quota"]["maximum_delta_percentage_points"] != plan["limits"]["quota"]["absolute_stop_used_percent"]:
        raise ValueError("quota absolute stop must equal baseline plus maximum delta")
    if plan["limits"]["attempts_total"] != plan["limits"]["scenario_runs"] * plan["limits"]["attempts_per_scenario"]:
        raise ValueError("attempt total does not cover each scenario")
    if plan["limits"]["native_sessions"] < plan["limits"]["scenario_runs"]:
        raise ValueError("native session limit must cover scheduled scenarios")
    effective = plan["effective_configuration"]
    if effective["scalar_overrides"] != REQUIRED_SCALARS:
        raise ValueError("effective scalar overrides differ from the approved vector")
    if set(effective["disabled_features"]) != REQUIRED_DISABLED or len(effective["disabled_features"]) != len(REQUIRED_DISABLED):
        raise ValueError("effective disabled features differ from the approved vector")
    if effective["enabled_features"] != ["skip_host_skill_discovery"]:
        raise ValueError("unexpected enabled feature")
    if set(effective["preflight"]) != REQUIRED_PREFLIGHT or len(effective["preflight"]) != len(REQUIRED_PREFLIGHT):
        raise ValueError("effective preflight checks differ from the approved vector")
    if effective["mcp_policy"] != MCP_POLICY or effective["same_for_all_runs"] is not True:
        raise ValueError("effective MCP or cross-run identity policy differs from the approved vector")
    discovery = plan["session_discovery"]
    if discovery["preexisting_file_hashing"] is not False:
        raise ValueError("pre-existing files must remain metadata-only")
    if discovery["candidate_rule"] != "exactly_one_file_proven_new_for_attempt_before_open":
        raise ValueError("unsafe session candidate rule")
    if discovery["ambiguous_candidates"] != "stop_without_opening_candidates":
        raise ValueError("ambiguous discovery must stop before opening candidates")
    if plan["positive_control_candidates"] != CONTROL_ORDER:
        raise ValueError("positive-control fallback order is not frozen")
    if plan["retry_policy"]["uncertain_submitted_attempt"] != "do_not_duplicate":
        raise ValueError("uncertain submitted attempts cannot be duplicated")
    quota = plan["limits"]["quota"]
    if quota["check"] != "before_every_submission_and_retry":
        raise ValueError("quota must be checked before every submission and retry")
    if quota["unreadable_before_first_launch"] != "stop_without_live_attempt":
        raise ValueError("unreadable pre-launch quota policy is unsafe")
    if quota["unreadable_after_launch"] != "finish_current_attempt_only_no_retry_report_unknown":
        raise ValueError("unreadable post-launch quota policy is unsafe")
    if set(plan["forbidden"]) != REQUIRED_FORBIDDEN or len(plan["forbidden"]) != len(REQUIRED_FORBIDDEN):
        raise ValueError("forbidden operation set differs from the approved plan")
    return plan


def plan_sha256(plan):
    """Validate and return the identity digest used by ledgers."""
    validate_live_plan(plan)
    return digest(canonical(plan))
