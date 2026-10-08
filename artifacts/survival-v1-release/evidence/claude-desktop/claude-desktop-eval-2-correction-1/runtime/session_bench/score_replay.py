"""Private, closed native-to-score replay of retained captures.

The packet contains exact observer/workload/native bytes, capture assertions,
and a fixed current-source scoring closure. Replaying verifies integrity against
a caller-pinned manifest, freshly decodes native records, joins the independent
observer, builds all twelve broad metrics, and computes 31 diagnostic rows.
Capture completeness/source authenticity remain assertions of the retained
receipts, not facts newly established by replay. This is neither public score
evidence nor independent reproduction. The host replay API optionally enforces
macOS filesystem/network isolation and records executed denial probes; the
packet's standalone Python runner alone provides no OS isolation guarantee.

Codex CLI's historical stdout observer only establishes submitted turns and
visible final responses. Unobserved primary populations stay unresolved; native
records or the workload's requested actions never supply their denominator.
"""

from __future__ import annotations

import hashlib
import ctypes
import ctypes.util
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from .native_replay import _read_runtime_source, _snapshot_tree, canonical

SCHEMA = "session-bench-native-score-replay-v1"
CONFIGURATIONS = frozenset({"codex-cli", "claude-cli", "codex-desktop", "claude-desktop", "deepseek-harness-cli", "opencode-cli", "copilot", "antigravity", "pi"})
SOURCE_FILES = (
    "session_bench/score_replay.py", "session_bench/native_replay.py",
    "session_bench/survival_metrics.py", "session_bench/survival_campaign.py",
    "session_bench/survival_evidence.py", "session_bench/v1_public_score.py",
    "session_bench/workload_instance.py",
    "session_bench/live_metric_comparator.py", "session_bench/format_response_population.py",
    "session_bench/format_timestamp_population.py",
    "session_bench/native_density.py", "session_bench/codex_format_evidence.py",
    "session_bench/claude_format_evidence.py", "session_bench/claude_live.py",
    "session_bench/adapters/codex_cli_decoder.py", "session_bench/adapters/claude_code_decoder.py",
    "scripts/replay_score_package.py",
)
_NAMESPACES = {"session_bench/__init__.py", "session_bench/adapters/__init__.py"}
FORMAT_EXTENSION_FILES = ("session_bench/desktop_density.py", "session_bench/claude_desktop_root.py")
PREVIOUS_EXTRA_SOURCES = {
    "codex-cli": ("session_bench/codex_stdout_observer.py", "session_bench/codex_native_projection.py",
                  "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"),
    "claude-cli": ("session_bench/claude_root_evidence.py",),
    "claude-desktop": ("session_bench/claude_desktop_root_evidence.py",),
    "copilot": ("session_bench/copilot_score_inputs.py", "session_bench/copilot_format_evidence.py",
                "session_bench/copilot_live.py", "session_bench/copilot_capture_qualification.py",
                "session_bench/adapters/agent_session_native_decoder.py",
                "docs/survival-v1/adapters/copilot.md"),
    "antigravity": ("session_bench/antigravity_score_inputs.py", "session_bench/antigravity_live.py",
                    "session_bench/live_observer.py", "session_bench/dsh_live.py",
                    "session_bench/dsh_native.py", "session_bench/adapters/deepseek_harness.py",
                    "session_bench/adapters/agent_session_native_decoder.py",
                    "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"),
}
DSH_SOURCE_FILES = ("session_bench/dsh_native.py", "session_bench/dsh_live.py",
                    "session_bench/dsh_format_evidence.py", "session_bench/adapters/deepseek_harness.py",
                    "docs/survival-v1/adapters/deepseek-harness.md",
                    "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py")
CURRENT_EXTRA_SOURCES = {
    "codex-cli": ("session_bench/codex_stdout_observer.py", "session_bench/codex_native_projection.py",
                  "session_bench/codex_cli_root_evidence.py",
                  "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"),
    "claude-cli": ("session_bench/claude_root_evidence.py",
                   "session_bench/claude_desktop_gui_event_clock.py"),
    "claude-desktop": ("session_bench/claude_desktop_root_evidence.py",
                       "session_bench/claude_desktop_gui_event_clock.py"),
    "deepseek-harness-cli": ("session_bench/dsh_observer.py", "session_bench/dsh_cache.py", "session_bench/dsh_closure.py"),
    "opencode-cli": ("session_bench/opencode_score_inputs.py", "session_bench/opencode_format_evidence.py",
                     "session_bench/opencode_density.py", "session_bench/adapters/opencode_decoder.py",
                     "session_bench/adapters/opencode_cli.py",
                     "docs/survival-v1/adapters/opencode-cli.md"),
    "copilot": ("session_bench/copilot_score_inputs.py", "session_bench/copilot_format_evidence.py",
                 "session_bench/copilot_live.py", "session_bench/copilot_capture_qualification.py",
                 "session_bench/copilot_density.py", "session_bench/copilot_session_store.py",
                 "session_bench/adapters/agent_session_native_decoder.py",
                 "docs/survival-v1/adapters/copilot.md"),
    "antigravity": ("session_bench/antigravity_score_inputs.py", "session_bench/antigravity_live.py",
                    "session_bench/live_observer.py", "session_bench/dsh_live.py",
                    "session_bench/dsh_native.py", "session_bench/adapters/deepseek_harness.py",
                    "session_bench/adapters/agent_session_native_decoder.py",
                    "session_bench/antigravity_format_evidence.py", "session_bench/antigravity_density.py",
                    "session_bench/antigravity_state_inputs.py", "session_bench/antigravity_conversation_db.py",
                    "session_bench/antigravity_root_evidence.py", "session_bench/normal_root_capture.py",
                    "docs/survival-v1/adapters/antigravity.md",
                    "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"),
    "pi": ("session_bench/pi_score_inputs.py", "session_bench/pi_session_stats.py",
           "session_bench/adapters/agent_session_native_decoder.py",
           "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"),
}
_SHA = re.compile(r"[0-9a-f]{64}")
_CONTEXT_KEYS = {
    "schema_version", "configuration_id", "repetition", "run_id", "build", "collected_on",
    "result_id", "observer_kind", "complete_record_family", "complete_root",
    "required_companions", "root_repetitions", "capture_assertion_path", "claude_projection",
}
_MISSING_CODEX_PRIMARY = ["work.actions", "work.results", "work.changed_files", "causal.action_result", "revision.final_after_r2", "attribution.model_config", "attribution.usage", "attribution.token_semantics", "attribution.reconciliation"]
_CLAUDE_DESKTOP_ROOT_LOCATOR = (
    "Claude Code normal session roots: projects/<project-key>/<session-id>.jsonl + "
    "Desktop session metadata/<session-group>/<local-session-id>.json"
)


def _validate_claude_desktop_run_discovery(
    contents: Mapping[str, bytes], context: Mapping[str, Any], assertion: Mapping[str, Any],
) -> None:
    """Bind one capture-time discovery to this run's exact copied family."""
    binding = assertion.get("source_discovery_receipt")
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ValueError("Claude Desktop source discovery binding is missing")
    name = _relative("inputs/" + binding["path"], {"inputs"})
    raw = contents.get(name)
    if raw is None or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise ValueError("Claude Desktop source discovery receipt digest mismatch")
    receipt = _json(raw, "Claude Desktop source discovery")
    keys = {"schema_version", "run_id", "repetition", "cli_session_id", "desktop_session_id",
            "metadata_only_discovery", "personal_history_content_read", "unrelated_content_read",
            "complete_inventories", "isolated_pair", "selected_artifacts", "root_summaries", "proof_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != keys:
        raise ValueError("Claude Desktop source discovery schema mismatch")
    body = {key: value for key, value in receipt.items() if key != "proof_sha256"}
    if (receipt["schema_version"] != "session-bench-claude-desktop-source-discovery-v1"
            or receipt["proof_sha256"] != hashlib.sha256(canonical(body)).hexdigest()
            or receipt["run_id"] != context["run_id"]
            or receipt["repetition"] != context["repetition"]
            or receipt["cli_session_id"] != assertion.get("cli_session_id")
            or receipt["desktop_session_id"] != assertion.get("desktop_session_id")):
        raise ValueError("Claude Desktop source discovery identity or proof mismatch")
    if (any(receipt[key] is not True for key in ("metadata_only_discovery", "complete_inventories", "isolated_pair"))
            or any(receipt[key] is not False for key in ("personal_history_content_read", "unrelated_content_read"))):
        raise ValueError("Claude Desktop source discovery is incomplete or unsafe")
    roots = {"transcript": "claude-projects", "desktop_metadata": "claude-desktop-sessions"}
    artifacts, summaries = receipt["selected_artifacts"], receipt["root_summaries"]
    if (not isinstance(artifacts, list) or len(artifacts) != 2
            or not all(isinstance(item, dict) for item in artifacts)
            or not isinstance(summaries, list) or len(summaries) != 2
            or not all(isinstance(item, dict) for item in summaries)):
        raise ValueError("Claude Desktop source discovery pair is malformed")
    selected = {item.get("role"): item for item in artifacts}
    by_root = {item.get("root_id"): item for item in summaries}
    if len(selected) != 2 or set(selected) != set(roots) or len(by_root) != 2 or set(by_root) != set(roots.values()):
        raise ValueError("Claude Desktop source discovery pair roles differ")
    for role, relative in (("transcript", "transcript/session.jsonl"), ("desktop_metadata", "desktop/session.json")):
        item = selected[role]
        family = contents.get("inputs/native-family/" + relative)
        if family is None or not isinstance(item.get("source_path"), str) or not item["source_path"].startswith("/"):
            raise ValueError("Claude Desktop source discovery lacks selected bytes or path")
        suffix = "/" + item.get("relative_path", "")
        if (suffix == "/" or not item["source_path"].endswith(suffix)
                or item.get("sha256") != hashlib.sha256(family).hexdigest()
                or item.get("size_bytes") != len(family)):
            raise ValueError("Claude Desktop source discovery differs from selected source bytes")
        source_root = item["source_path"][:-len(suffix)]
        summary = by_root[roots[role]]
        if (hashlib.sha256(source_root.encode()).hexdigest() != item.get("source_root_sha256")
                or item.get("source_root_sha256") != summary.get("source_root_sha256")
                or item.get("filesystem_id_sha256") != summary.get("filesystem_id_sha256")):
            raise ValueError("Claude Desktop source discovery root binding differs")
    transcript = contents["inputs/native-family/transcript/session.jsonl"]
    if transcript != contents.get("native/session.jsonl"):
        raise ValueError("Claude Desktop discovered transcript differs from scored native bytes")
    if assertion.get("cross_run_root_repeatability_qualified") is not False:
        raise ValueError("Claude Desktop per-run discovery overclaims cross-run repeatability")
    expected = [{"repetition": context["repetition"], "root_locator": _CLAUDE_DESKTOP_ROOT_LOCATOR,
                 "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False}]
    if context.get("root_repetitions") != expected:
        raise ValueError("Claude Desktop root rows differ from this run's discovery")


def _zstd_dependency(expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind the actual dynamically loaded library; it is not bundled Python."""
    library = ctypes.util.find_library("zstd")
    if not library or not Path(library).is_absolute():
        raise ValueError("DSH replay requires an explicitly locatable installed libzstd")
    path = Path(library).resolve()
    data = _read_runtime_source(path)
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and expected.get("sha256") != digest:
        raise ValueError("DSH installed libzstd differs from pinned platform dependency before loading")
    zstd = ctypes.CDLL(str(path))
    zstd.ZSTD_versionString.restype = ctypes.c_char_p
    if data != _read_runtime_source(path):
        raise ValueError("DSH installed libzstd changed during loading")
    return {"name": "libzstd", "version": zstd.ZSTD_versionString().decode("ascii"),
            "sha256": digest, "bundled": False,
            "provision": "reviewer_installed_platform_dependency", "loader": "ctypes.util.find_library('zstd')"}


def _json(data: bytes, label: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"{label}: non-finite JSON value {value}")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{label}: invalid JSON") from error


def _relative(name: Any, roots: set[str]) -> str:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("unsafe replay inventory path")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.as_posix() != name or path.parts[0] not in roots:
        raise ValueError("unsafe replay inventory path")
    return name


def _validate_pi_root_repetitions(contents: Mapping[str, bytes], *, current_documents: Mapping[str, bytes],
                                  current_identity: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    """Bind all three stable-root claims to complete, independently qualified captures."""
    index_path = "inputs/pi-root-repetitions.json"
    cross_prefix = "inputs/root-repetitions/"
    rows = context.get("root_repetitions")
    if rows is None:
        if index_path in contents or any(name.startswith(cross_prefix) for name in contents):
            raise ValueError("Pi root repetition evidence is present without a context declaration")
        return
    if not isinstance(rows, list) or len(rows) != 3 or index_path not in contents:
        raise ValueError("Pi stable-root claim requires three indexed complete-root captures")
    expected_rows = {item.get("repetition"): item for item in rows if isinstance(item, Mapping)}
    if set(expected_rows) != {1, 2, 3} or len(expected_rows) != 3:
        raise ValueError("Pi stable-root repetition declaration is malformed")
    index = _json(contents[index_path], "Pi root repetitions")
    if not isinstance(index, dict) or set(index) != {"schema_version", "runs"} or index.get("schema_version") != "session-bench-pi-root-repetitions-v1":
        raise ValueError("unsupported Pi root repetition evidence")
    runs = index.get("runs")
    if not isinstance(runs, list) or len(runs) != 3:
        raise ValueError("Pi root repetition evidence must contain three runs")
    indexed_support_paths = set()
    seen_attempts = set()
    observed_build_dates = set()
    for row in runs:
        fields = {"repetition", "attempt_id", "session_id", "root_capture_sha256",
                  "collected_on", "pi_version", "capture_documents"}
        if not isinstance(row, dict) or set(row) != fields or type(row.get("repetition")) is not int:
            raise ValueError("malformed Pi root repetition row")
        repetition = row["repetition"]
        if repetition not in {1, 2, 3} or repetition in seen_attempts:
            raise ValueError("duplicate or invalid Pi root repetition")
        seen_attempts.add(repetition)
        if repetition == context["repetition"]:
            attempt_prefix = "inputs/capture/"
            documents = dict(current_documents)
            identity = current_identity
        else:
            attempt_prefix = f"inputs/root-repetitions/repetition-{repetition}/capture/"
            indexed_support_paths.update(name for name in contents if name.startswith(attempt_prefix))
            documents = {name[len(attempt_prefix):]: raw for name, raw in contents.items()
                         if name.startswith(attempt_prefix)}
            if not documents:
                raise ValueError("Pi cross-repetition capture documents are missing")
            from .pi_score_inputs import qualify_capture_documents
            identity = qualify_capture_documents(documents)
        file_index = row.get("capture_documents")
        if not isinstance(file_index, list):
            raise ValueError("Pi root repetition lacks its capture document inventory")
        normalized, seen_names = [], set()
        for entry in file_index:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
                raise ValueError("malformed Pi root repetition capture inventory")
            name = _relative(entry["path"], {"plan.json", "capture-result.json", "workload-instance.json",
                "workload-template.json", "controller-state.json", "preflight.json", "turn-r1", "turn-r2",
                "workspaces", "observer", "native-root"})
            raw = documents.get(name)
            if (name in seen_names or raw is None or type(entry["size_bytes"]) is not int
                    or len(raw) != entry["size_bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]):
                raise ValueError("Pi root repetition capture inventory mismatch")
            normalized.append(name); seen_names.add(name)
        if seen_names != set(documents):
            raise ValueError("Pi root repetition capture inventory is not closed")
        plan = _json(documents["plan.json"], "Pi repeated capture plan")
        result = _json(documents["capture-result.json"], "Pi repeated capture result")
        started_ns = result.get("turns", [{}])[0].get("launch", {}).get("started_ns")
        if type(started_ns) is not int:
            raise ValueError("Pi repeated capture lacks a bound start time")
        collected_on = datetime.fromtimestamp(started_ns / 1_000_000_000, timezone.utc).date().isoformat()
        pi_version = result.get("preflight", {}).get("version")
        if (identity.get("repetition") != repetition or identity.get("run_id") != row.get("attempt_id")
                or identity.get("session_id") != row.get("session_id")
                or identity.get("complete_root") is not True
                or identity.get("complete_record_family") is not True
                or row.get("root_capture_sha256") != hashlib.sha256(documents["native-root/root-capture.json"]).hexdigest()
                or row.get("collected_on") != collected_on or row.get("pi_version") != pi_version):
            raise ValueError("Pi repeated complete-root evidence does not match its capture receipts")
        observed_build_dates.add((pi_version, collected_on))
        prefix_for_locator = attempt_prefix
        expected_location = {"repetition": repetition,
            "root_locator": prefix_for_locator + "plan.json:session_dir passed as Pi --session-dir",
            "isolated_discovery": True, "personal_history_scanned": False}
        if expected_rows[repetition] != expected_location:
            raise ValueError("Pi broad root-location evidence differs from its indexed captures")
    if (seen_attempts != {1, 2, 3} or len(observed_build_dates) != 1
            or (context["repetition"] not in seen_attempts)):
        raise ValueError("Pi root repetitions do not share one declared build/date window")
    actual_cross_paths = {name for name in contents if name.startswith(cross_prefix)}
    if actual_cross_paths != indexed_support_paths:
        raise ValueError("Pi cross-repetition files differ from their capture inventory")


def validate_score_packet(contents: dict[str, bytes]) -> dict[str, Any]:
    """Trusted host validation before running any code in a packet."""
    manifest = _json(contents.get("manifest.json", b""), "manifest")
    fields = {"schema_version", "configuration_id", "repetition", "files", "expected_diagnostics_sha256",
              "scope", "public_safe", "independent_reproduction"}
    if not isinstance(manifest, dict) or set(manifest) not in (fields, fields | {"platform_dependencies"}) or manifest["schema_version"] != SCHEMA:
        raise ValueError("unsupported score replay manifest")
    if not isinstance(manifest["configuration_id"], str) or manifest["configuration_id"] not in CONFIGURATIONS or type(manifest["repetition"]) is not int or manifest["repetition"] not in (1, 2, 3):
        raise ValueError("unsupported score replay identity")
    if (manifest["scope"] != "private_native_to_score_diagnostics" or manifest["public_safe"] is not False
            or manifest["independent_reproduction"] is not False):
        raise ValueError("score replay overstates verification scope")
    if not isinstance(manifest["expected_diagnostics_sha256"], str) or not _SHA.fullmatch(manifest["expected_diagnostics_sha256"]):
        raise ValueError("invalid expected diagnostics digest")
    seen = set()
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise ValueError("missing score replay inventory")
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
            raise ValueError("malformed score replay inventory entry")
        name = _relative(entry["path"], {"native", "runtime", "inputs"})
        if name in seen:
            raise ValueError("duplicate score replay inventory path")
        seen.add(name)
        data = contents.get(name)
        if data is None or type(entry["size_bytes"]) is not int or len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"score replay inventory mismatch: {name}")
    if set(contents) != seen | {"manifest.json"}:
        raise ValueError("score replay inventory is not closed")
    extra = DSH_SOURCE_FILES if manifest["configuration_id"] == "deepseek-harness-cli" else ()
    expected_runtime = {f"runtime/{name}" for name in (*SOURCE_FILES, *extra, *_NAMESPACES)}
    extended_runtime = expected_runtime | {f"runtime/{name}" for name in FORMAT_EXTENSION_FILES}
    current_runtime = extended_runtime | {f"runtime/{name}" for name in CURRENT_EXTRA_SOURCES.get(manifest["configuration_id"], ())}
    previous_runtime = extended_runtime | {
        f"runtime/{name}" for name in PREVIOUS_EXTRA_SOURCES.get(manifest["configuration_id"], ())
    }
    allowed = ((extended_runtime, current_runtime) if extra else
               (expected_runtime, extended_runtime, previous_runtime, current_runtime))
    if {name for name in seen if name.startswith("runtime/")} not in allowed:
        raise ValueError("score replay runtime differs from fixed dependency closure")
    if not {"native/decode.json", "inputs/workload.json", "inputs/observer.json", "inputs/context.json"} <= seen:
        raise ValueError("missing closed score replay input")
    if manifest["configuration_id"] == "deepseek-harness-cli":
        dependencies = manifest.get("platform_dependencies")
        if not isinstance(dependencies, list) or len(dependencies) != 1 or not isinstance(dependencies[0], dict):
            raise ValueError("DSH replay requires one pinned libzstd dependency")
        if dependencies != [_zstd_dependency(dependencies[0])]:
            raise ValueError("DSH installed libzstd differs from pinned platform dependency")
    elif "platform_dependencies" in manifest:
        raise ValueError("unexpected platform dependency declaration")
    context = _json(contents["inputs/context.json"], "context")
    if not isinstance(context, dict) or set(context) != _CONTEXT_KEYS or context["schema_version"] != SCHEMA:
        raise ValueError("unsupported score replay context")
    if context["configuration_id"] != manifest["configuration_id"] or context["repetition"] != manifest["repetition"]:
        raise ValueError("score replay context identity mismatch")
    timestamp_observer_bytes = contents.get("inputs/timestamp-observer.json")
    if timestamp_observer_bytes is not None:
        if context["configuration_id"] != "claude-desktop":
            raise ValueError("timestamp observer input is allowed only for claude-desktop")
        from .claude_format_evidence import validate_claude_timestamp_observer
        validate_claude_timestamp_observer(
            _json(contents["inputs/observer.json"], "primary observer"),
            _json(timestamp_observer_bytes, "timestamp observer"),
            run_id=context["run_id"],
        )
    if type(context["complete_record_family"]) is not bool or type(context["complete_root"]) is not bool:
        raise ValueError("capture assertions must be explicit booleans")
    assertion_path = _relative(context["capture_assertion_path"], {"inputs"})
    if assertion_path not in seen or assertion_path in {"inputs/workload.json", "inputs/observer.json", "inputs/context.json"}:
        raise ValueError("missing separate capture assertion document")
    assertion = _json(contents[assertion_path], "capture assertion")
    if not isinstance(assertion, dict):
        raise ValueError("capture assertion must be an object")
    # Recognized retained capture schemas only; never elevate an arbitrary flag.
    native_inventory = _json(contents["native/decode.json"], "native inventory")
    if not isinstance(native_inventory, dict) or not isinstance(native_inventory.get("artifacts"), list):
        raise ValueError("missing native record inventory")
    if assertion.get("schema_version") == "session-bench-claude-cli-native-manifest-v1-qualified":
        family = assertion.get("complete_record_family") is True and assertion.get("new_family_only") is True and assertion.get("unrelated_preexisting_sessions_read") is False
        root = family
        if context["configuration_id"] != "claude-cli":
            raise ValueError("capture assertion family mismatch")
        selected = assertion.get("selected_artifact", {})
        if len(native_inventory["artifacts"]) != 1 or selected.get("sha256") != native_inventory["artifacts"][0].get("sha256"):
            raise ValueError("Claude capture assertion does not bind selected native bytes")
    elif assertion.get("schema_version") == "1.0-codex-cli-requalification-v2":
        family = assertion.get("broad_profile", {}).get("complete_record_family") is True
        root = assertion.get("complete_root") is True
        if context["configuration_id"] != "codex-cli" or assertion.get("run_id") != context["run_id"]:
            raise ValueError("capture assertion run mismatch")
        if assertion.get("native_manifest", {}).get("sha256") != hashlib.sha256(contents["native/decode.json"]).hexdigest():
            raise ValueError("Codex capture assertion does not bind native inventory")
    elif assertion.get("schema_version") == "session-bench-codex-desktop-attempt-v1":
        family = assertion.get("complete_record_family") is True
        root = False
        if context["configuration_id"] != "codex-desktop" or assertion.get("run_id") != context["run_id"]:
            raise ValueError("Desktop capture assertion run mismatch")
        binding = _json(contents.get("inputs/captured-format-binding.json", b""), "captured binding")
        if binding.get("native_manifest", {}).get("sha256") != hashlib.sha256(contents["native/decode.json"]).hexdigest():
            raise ValueError("Desktop capture assertion does not bind native inventory")
    elif assertion.get("schema_version") in {"session-bench-claude-desktop-native-manifest-v2", "session-bench-claude-desktop-native-manifest-v3-qualified"}:
        new_discovery = assertion["schema_version"] == "session-bench-claude-desktop-native-manifest-v2"
        family = (assertion.get("complete_cross_root_family") is True
                  and (new_discovery or assertion.get("complete_persistent_family") is True)
                  and assertion.get("unrelated_preexisting_sessions_opened") is False)
        root = family
        if context["configuration_id"] != "claude-desktop" or assertion.get("repetition") != context["repetition"]:
            raise ValueError("Desktop capture assertion identity mismatch")
        if len(native_inventory["artifacts"]) != 1 or assertion.get("selected_artifact", {}).get("sha256") != native_inventory["artifacts"][0].get("sha256"):
            raise ValueError("Desktop capture assertion does not bind transcript")
        validation_bytes = contents.get("inputs/family-validation.json", b"")
        if hashlib.sha256(validation_bytes).hexdigest() != assertion.get("family_validation", {}).get("sha256"):
            raise ValueError("Desktop family assertion digest mismatch")
        validation = _json(validation_bytes, "Desktop family validation")
        family_artifacts = validation.get("artifacts", [])
        if validation.get("schema_version") != "session-bench-claude-desktop-family-v2" or not isinstance(family_artifacts, list) or len(family_artifacts) != 2 or {row.get("path") for row in family_artifacts} != {"transcript/session.jsonl", "desktop/session.json"}:
            raise ValueError("Desktop family validation must inventory both known native families")
        for artifact in family_artifacts:
            data = contents.get(_relative("inputs/native-family/" + artifact["path"], {"inputs"}))
            if data is None or hashlib.sha256(data).hexdigest() != artifact["sha256"] or len(data) != artifact["size_bytes"]:
                raise ValueError("Desktop full family omits an asserted native companion")
        if new_discovery:
            _validate_claude_desktop_run_discovery(contents, context, assertion)
        elif "inputs/source-discovery-private.json" in contents:
            raise ValueError("legacy Claude Desktop assertion cannot acquire new discovery evidence")
    elif assertion.get("schema_version") == "session-bench-dsh-score-closure-v1":
        from .dsh_closure import validate_dsh_closure
        family, root = validate_dsh_closure(contents, context, native_inventory)
    elif context["configuration_id"] == "opencode-cli":
        from .opencode_score_inputs import validate_opencode_capture_assertion
        family, root = validate_opencode_capture_assertion(contents, context, native_inventory)
    elif context["configuration_id"] == "copilot":
        from .copilot_score_inputs import validate_copilot_capture_assertion
        family, root = validate_copilot_capture_assertion(contents, context, native_inventory)
    elif context["configuration_id"] == "antigravity":
        from .antigravity_score_inputs import validate_antigravity_capture_assertion
        family, root = validate_antigravity_capture_assertion(contents, context, native_inventory)
    elif assertion.get("schema_version") == "session-bench-pi-score-capture-v1":
        from .pi_score_inputs import qualify_capture_documents
        if (context["configuration_id"] != "pi" or assertion.get("attempt_id") != context["run_id"]
                or assertion.get("repetition") != context["repetition"]):
            raise ValueError("Pi capture assertion identity or scope mismatch")
        entries = assertion.get("capture_documents")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Pi assertion has no retained capture documents")
        documents, seen_capture = {}, set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size_bytes"}:
                raise ValueError("malformed Pi capture document index")
            relative = _relative("inputs/capture/" + entry["path"], {"inputs"})
            if relative in seen_capture:
                raise ValueError("duplicate Pi capture document")
            seen_capture.add(relative)
            data = contents.get(relative)
            if (data is None or type(entry["size_bytes"]) is not int or len(data) != entry["size_bytes"]
                    or hashlib.sha256(data).hexdigest() != entry["sha256"]):
                raise ValueError("Pi capture document differs from its retained digest")
            documents[entry["path"]] = data
        if {name for name in seen if name.startswith("inputs/capture/")} != seen_capture:
            raise ValueError("Pi capture document inventory is not closed")
        identity = qualify_capture_documents(documents)
        if (identity["run_id"] != context["run_id"] or identity["repetition"] != context["repetition"]
                or identity["session_id"] != assertion.get("session_id")
                or identity["capture_environment"] != assertion.get("capture_environment")):
            raise ValueError("Pi retained capture does not bind the declared scope")
        _validate_pi_root_repetitions(contents, current_documents=documents,
                                      current_identity=identity, context=context)
        if len(native_inventory["artifacts"]) != 1:
            raise ValueError("Pi replay requires exactly one final session transcript")
        artifact = native_inventory["artifacts"][0]
        if artifact.get("path") != "session.jsonl" or artifact.get("sha256") != identity["native_sha256"]:
            raise ValueError("Pi replay transcript differs from captured R2 native bytes")
        if contents.get("inputs/workload.json") != documents.get("workload-instance.json"):
            raise ValueError("Pi replay workload differs from captured workload")
        family = identity["complete_record_family"]
        root = identity["complete_root"]
    elif assertion.get("schema_version") == "session-bench-dsh-diagnostic-package-v1":
        if context["configuration_id"] != "deepseek-harness-cli" or assertion.get("attempt_id") != context["run_id"] or assertion.get("repetition") != context["repetition"]:
            raise ValueError("DSH capture assertion identity mismatch")
        if len(native_inventory["artifacts"]) != 1 or assertion.get("native_sha256") != native_inventory["artifacts"][0].get("sha256"):
            raise ValueError("DSH capture assertion does not bind native physical bytes")
        indexed = {row["path"]: row for row in assertion.get("files", [])}
        for name in ("observer.json", "workload.json"):
            raw = contents["inputs/" + name]
            if indexed.get(name, {}).get("sha256") != hashlib.sha256(raw).hexdigest():
                raise ValueError("DSH observer/workload differ from captured assertion")
        for name in ("bench_check.py", "helper-ledger.jsonl", "r1.stdout.jsonl", "r2.stdout.jsonl"):
            data = contents.get("inputs/" + name, b"")
            if indexed.get(name, {}).get("sha256") != hashlib.sha256(data).hexdigest():
                raise ValueError("DSH captured observer/helper provenance digest mismatch")
        if contents["inputs/bench_check.py"] != contents["runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"]:
            raise ValueError("DSH captured helper differs from frozen native semantic contract")
        portable = _json(contents.get("inputs/captured-portability.json", b""), "DSH capture portability")
        if indexed.get("portability.json", {}).get("sha256") != hashlib.sha256(contents["inputs/captured-portability.json"]).hexdigest():
            raise ValueError("DSH retained portability assertion digest mismatch")
        family = portable.get("complete_root") is True and portable.get("companions_present") is True
        root = family
    else:
        raise ValueError("unsupported retained capture assertion schema")
    if context["complete_record_family"] and not family or context["complete_root"] and not root:
        raise ValueError("context promotes an unproven capture assertion")
    return manifest


def _workload(instance: Mapping[str, Any]):
    from .adapters.codex_cli_decoder import ExpectedAction, FrozenWorkload
    if instance.get("schema_version") != "1.0-survival-workload" or not isinstance(instance.get("run_id"), str):
        raise ValueError("unsupported replay workload")
    try:
        turns = tuple((row["id"], row["text"]) for row in instance["turns"])
        canaries = tuple((row["id"], row["response_canary"]) for row in instance["turns"])
        actions = tuple(ExpectedAction(row["id"], row["turn_id"], row["kind"],
                                      " ".join(row["argv"]) if row["kind"] != "edit" else None,
                                      row["target"], row.get("helper_nonce")) for row in instance["actions"])
    except (KeyError, TypeError) as error:
        raise ValueError("malformed replay workload") from error
    if len(turns) != 2 or [row[0] for row in turns] != ["turn-r1", "turn-r2"]:
        raise ValueError("replay requires exact R1/R2 submitted workload")
    return FrozenWorkload(instance["run_id"], turns, canaries, actions, instance["run_canary"])


def _observer(root: Path, instance: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[bytes, list[str]]:
    data = (root / "inputs/observer.json").read_bytes()
    value = _json(data, "observer")
    from .live_metric_comparator import _validate_observer
    if context["observer_kind"] == "canonical":
        if context["configuration_id"] == "claude-desktop":
            from .workload_instance import instantiate_workload
            template = _json((root / "inputs/workload-template.json").read_bytes(), "original frozen workload")
            gui = _json((root / "inputs/gui-observer.json").read_bytes(), "independent GUI receipt")
            reconstructed, _ = instantiate_workload(template, context["run_id"])
            if gui.get("run_id") != context["run_id"] or not isinstance(gui.get("submitted_prompts"), dict):
                raise ValueError("Desktop workload lacks exact independent GUI submissions")
            for row, revision in zip(reconstructed["turns"], ("r1", "r2")):
                text = gui["submitted_prompts"].get(revision)
                if not isinstance(text, str) or hashlib.sha256(text.encode()).hexdigest() != gui.get("submitted_prompt_sha256", {}).get(revision):
                    raise ValueError("Desktop submitted prompt digest mismatch")
                row["text"] = text
            if instance != reconstructed:
                raise ValueError("Desktop workload differs from exact declared reconstruction")
        observed_run, events, _ = _validate_observer(value)
        if observed_run != instance["run_id"]:
            raise ValueError("observer/workload run mismatch")
        for turn in instance["turns"]:
            matches = [event for event in events if event["kind"] == "user_turn" and event["population_role"] == "primary_scored" and event["fields"].get("text") == turn["text"]]
            if len(matches) != 1:
                raise ValueError("observer does not bind the exact submitted workload")
        return data, []
    if context["observer_kind"] == "copilot-capture-v1" and context["configuration_id"] == "copilot":
        from .copilot_score_inputs import observer_from_capture_documents
        assertion = _json((root / context["capture_assertion_path"]).read_bytes(), "Copilot assertion")
        documents = {entry["path"]: (root / "inputs/capture" / entry["path"]).read_bytes()
                     for entry in assertion["capture_documents"]}
        rebuilt = canonical(observer_from_capture_documents(documents))
        if rebuilt != data:
            raise ValueError("Copilot observer differs from independent capture")
        return rebuilt, []
    if context["observer_kind"] in {"antigravity-capture-v1", "antigravity-capture-v2", "antigravity-capture-v3"} and context["configuration_id"] == "antigravity":
        from .antigravity_score_inputs import observer_from_capture_documents
        assertion = _json((root / context["capture_assertion_path"]).read_bytes(), "Antigravity assertion")
        documents = {entry["path"]: (root / "inputs/capture" / entry["path"]).read_bytes()
                     for entry in assertion["capture_documents"]}
        rebuilt = canonical(observer_from_capture_documents(documents, assertion["captured_workspace"],
                                                            step_bound=context["observer_kind"] != "antigravity-capture-v1"))
        if rebuilt != data:
            raise ValueError("Antigravity observer differs from independent capture")
        return rebuilt, []
    if context["observer_kind"] in {"pi-json-capture-v1", "pi-print-capture-v1"} and context["configuration_id"] == "pi":
        from .pi_score_inputs import PRINT_UNOBSERVED, UNOBSERVED, observer_from_capture_documents
        assertion = _json((root / context["capture_assertion_path"]).read_bytes(), "Pi capture assertion")
        documents = {entry["path"]: (root / "inputs/capture" / entry["path"]).read_bytes()
                     for entry in assertion["capture_documents"]}
        rebuilt = canonical(observer_from_capture_documents(documents))
        if rebuilt != data:
            raise ValueError("Pi observer differs from independent JSON stdout and capture documents")
        # A complete-root Pi packet carries a separately qualified root
        # inventory and the replay closure supplies its portability receipt.
        # Leave those rows unresolved only for older transcript-only captures.
        missing = [] if context["complete_root"] is True else list(UNOBSERVED)
        if context["observer_kind"] == "pi-print-capture-v1":
            missing.extend(PRINT_UNOBSERVED)
        return rebuilt, missing
    if context["observer_kind"] == "codex-desktop-task-api-v1":
        if (context["configuration_id"] != "codex-desktop" or not isinstance(value, dict)
                or value.get("schema_version") != "session-bench-codex-task-api-observer-v1"
                or value.get("independent_of_native_root") is not True or not isinstance(value.get("turns"), list)
                or len(value["turns"]) != 2 or not isinstance(value.get("thread_id"), str)):
            raise ValueError("unsupported independent Desktop task observer")
        events, relations = [], []
        for number, (receipt, turn) in enumerate(zip(value["turns"], instance["turns"]), 1):
            prompt = (root / f"inputs/prompt-r{number}.txt").read_text()
            # Prompt text files use a single terminal LF as file delimiter;
            # accept that exact serialization only, with no whitespace trimming.
            if prompt not in (turn["text"], turn["text"] + "\n") or receipt.get("revision") != turn["revision"] or receipt.get("response_canary") != turn["response_canary"] or not isinstance(receipt.get("response_message_id"), str):
                raise ValueError("Desktop task observer lacks submitted-turn/response identity")
            response_id = f"response-r{number}"
            for kind, identifier, fields in (("user_turn", turn["id"], {"turn_id": turn["id"], "revision": turn["revision"], "role": "user", "text": turn["text"], "run_canary": instance["run_canary"]}),
                    ("assistant_response", response_id, {"turn_id": turn["id"], "role": "assistant", "canary": receipt["response_canary"]})):
                events.append({"id": identifier, "sequence": len(events) + 1, "population_role": "primary_scored", "kind": kind, "session_id": value["thread_id"], "fields": fields, "metric_ids": [], "source": "independent_task_api"})
            relations.append({"id": f"turn-response-{number}", "kind": "turn_response", "from_id": turn["id"], "to_id": response_id, "sequence": len(relations) + 1})
        relations.append({"id": "r2-supersedes-r1", "kind": "supersedes", "from_id": "turn-r1", "to_id": "turn-r2", "sequence": len(relations) + 1})
        document = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival", "scenario_id": "survival-v1-repair", "run_id": instance["run_id"], "independent": True, "events": events, "relations": relations}
        _validate_observer(document)
        return canonical(document), list(_MISSING_CODEX_PRIMARY)
    if context["observer_kind"] == "codex-cli-stdout-v2" and context["configuration_id"] == "codex-cli":
        from .codex_stdout_observer import build_codex_stdout_observer
        if (not isinstance(value, dict) or set(value) != {"schema_version", "run_id", "independent", "streams"}
                or value.get("schema_version") != "session-bench-codex-stdout-observer-v1"
                or value.get("run_id") != instance["run_id"] or value.get("independent") is not True
                or not isinstance(value.get("streams"), list) or len(value["streams"]) != 2):
            raise ValueError("invalid independent stdout successor observer")
        streams, receipts = {}, {}
        for number, stream in enumerate(value["streams"], 1):
            if not isinstance(stream, dict) or set(stream) != {"path", "sha256", "receipt_path"}:
                raise ValueError("invalid stdout successor stream reference")
            streams[number] = (root / _relative(stream["path"], {"inputs"})).read_bytes()
            receipts[number] = (root / _relative(stream["receipt_path"], {"inputs"})).read_bytes()
            if hashlib.sha256(streams[number]).hexdigest() != stream["sha256"]:
                raise ValueError("stdout successor stream digest mismatch")
        document = build_codex_stdout_observer(instance, stdout_by_turn=streams, receipts_by_turn=receipts,
            helper_ledger=(root / "inputs/helper-ledger.jsonl").read_bytes(),
            checkout_before=(root / "inputs/checkout.before.py").read_bytes(),
            checkout_after=(root / "inputs/checkout.after.py").read_bytes(),
            capture_receipt=(root / "inputs/original-capture-receipt.json").read_bytes(),
            helper_source=(root / "inputs/bench_check.py").read_bytes(),
            frozen_helper_source=(root / "runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py").read_bytes())
        # The stdout stream carries no model or response-scoped usage.  Those
        # four attribution metrics are native-attested, so nothing is forced
        # to unresolved here.
        return canonical(document), []
    if context["observer_kind"] != "codex-cli-stdout-v1" or context["configuration_id"] != "codex-cli":
        raise ValueError("unsupported replay observer kind")
    if not isinstance(value, dict) or set(value) != {"schema_version", "run_id", "independent", "streams"} or value["schema_version"] != "session-bench-codex-stdout-observer-v1" or value["run_id"] != instance["run_id"] or value["independent"] is not True:
        raise ValueError("invalid independent stdout observer")
    if not isinstance(value["streams"], list) or len(value["streams"]) != 2:
        raise ValueError("missing exact R1/R2 independent stdout streams")
    events, relations = [], []
    session_ids = set()
    for number, (stream, turn) in enumerate(zip(value["streams"], instance["turns"]), 1):
        if not isinstance(stream, dict) or set(stream) != {"path", "sha256", "receipt_path"}:
            raise ValueError("invalid stdout stream reference")
        raw = (root / _relative(stream["path"], {"inputs"})).read_bytes()
        receipt = _json((root / _relative(stream["receipt_path"], {"inputs"})).read_bytes(), "observer receipt")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != stream["sha256"] or receipt.get("sha256") != digest or receipt.get("independent") is not True or receipt.get("frozen") is not True:
            raise ValueError("stdout observer receipt is not bound to exact bytes")
        rows = [_json(line, "stdout record") for line in raw.splitlines() if line.strip()]
        ids = {row.get("thread_id") for row in rows if row.get("type") == "thread.started"}
        session_ids.update(ids)
        responses = [row["item"] for row in rows if row.get("type") == "item.completed" and isinstance(row.get("item"), dict) and row["item"].get("type") == "agent_message"]
        matches = [row for row in responses if isinstance(row.get("text"), str) and turn["response_canary"] in row["text"]]
        if len(matches) != 1 or not any(row.get("type") == "turn.completed" for row in rows):
            raise ValueError("stdout lacks one completed independently observed final response")
        response_id = f"response-r{number}"
        fields = {"turn_id": turn["id"], "revision": turn["revision"], "role": "user", "text": turn["text"], "run_canary": instance["run_canary"]}
        for kind, identifier, detail in (("user_turn", turn["id"], fields), ("assistant_response", response_id,
            {"turn_id": turn["id"], "role": "assistant", "status": "completed", "canary": turn["response_canary"], "text": matches[0]["text"]})):
            events.append({"id": identifier, "sequence": len(events) + 1, "population_role": "primary_scored", "kind": kind, "session_id": "pending", "fields": detail, "metric_ids": [], "source": "submitted_input" if kind == "user_turn" else "independent_stdout"})
        relations.append({"id": f"turn-response-{number}", "kind": "turn_response", "from_id": turn["id"], "to_id": response_id, "sequence": len(relations) + 1})
    if len(session_ids) != 1 or not all(isinstance(value, str) and value for value in session_ids):
        raise ValueError("stdout does not bind one native-independent session")
    for event in events:
        event["session_id"] = next(iter(session_ids))
    relations.append({"id": "r2-supersedes-r1", "kind": "supersedes", "from_id": "turn-r1", "to_id": "turn-r2", "sequence": len(relations) + 1})
    document = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival", "scenario_id": "survival-v1-repair", "run_id": instance["run_id"], "independent": True, "events": events, "relations": relations}
    _validate_observer(document)
    return canonical(document), list(_MISSING_CODEX_PRIMARY)


def _diagnostics(root: Path, *, native: Path | None = None) -> dict[str, Any]:
    context = _json((root / "inputs/context.json").read_bytes(), "context")
    instance = _json((root / "inputs/workload.json").read_bytes(), "workload")
    if instance.get("run_id") != context["run_id"]:
        raise ValueError("context/workload run mismatch")
    workload = _workload(instance)
    observer_bytes, missing = _observer(root, instance, context)
    observer = _json(observer_bytes, "normalized observer")
    timestamp_observer_bytes = None
    timestamp_observer_reference = None
    timestamp_observer_path = root / "inputs/timestamp-observer.json"
    if timestamp_observer_path.is_file():
        timestamp_observer_bytes = timestamp_observer_path.read_bytes()
        timestamp_observer_reference = {
            "id": "inputs/timestamp-observer.json",
            "sha256": hashlib.sha256(timestamp_observer_bytes).hexdigest(),
        }
    native = native or root / "native"
    desktop_assertion = (
        _json((root / context["capture_assertion_path"]).read_bytes(), "Desktop capture assertion")
        if context["configuration_id"] == "claude-desktop" else None
    )
    if (context["configuration_id"] == "claude-desktop" and context["root_repetitions"] is not None
            and desktop_assertion.get("schema_version") != "session-bench-claude-desktop-native-manifest-v2"):
        from .claude_desktop_root_evidence import FAMILY_PREFIX, verify_claude_desktop_root_evidence
        proof = root / "inputs/root-evidence"
        if verify_claude_desktop_root_evidence(proof) != context["root_repetitions"]:
            raise ValueError("Claude Desktop root rows differ from retained proof")
        selected = proof / f'repetition-{context["repetition"]}'
        for relative in ("transcript/session.jsonl", "desktop/session.json"):
            if (selected / FAMILY_PREFIX / relative).read_bytes() != (root / "inputs/native-family" / relative).read_bytes():
                raise ValueError("Claude Desktop root proof differs from current persistent family")
        if native == root / "native":
            expected = _json((selected / "capture/finalized-private-v1/native-package/decode.json").read_bytes(), "Desktop root native inventory")
            actual = _json((native / "decode.json").read_bytes(), "Desktop current native inventory")
            if expected["artifacts"] != actual["artifacts"]:
                raise ValueError("Claude Desktop root proof differs from current native artifacts")
    if context["configuration_id"] == "claude-cli" and context["root_repetitions"] is not None:
        from .claude_root_evidence import verify_claude_root_evidence
        if verify_claude_root_evidence(root / "inputs/root-evidence") != context["root_repetitions"]:
            raise ValueError("Claude root rows differ from verified retained root evidence")
        if native == root / "native":
            expected = _json((root / f'inputs/root-evidence/repetition-{context["repetition"]}/native-bundle/decode.json').read_bytes(), "Claude root native inventory")
            actual = _json((native / "decode.json").read_bytes(), "Claude current native inventory")
            if expected["artifacts"] != actual["artifacts"]:
                raise ValueError("Claude root evidence differs from current native artifacts")
    if context["configuration_id"] == "codex-cli" and context["root_repetitions"] is not None:
        from .codex_cli_root_evidence import verify_codex_cli_root_evidence
        if verify_codex_cli_root_evidence(root / "inputs/root-evidence") != context["root_repetitions"]:
            raise ValueError("Codex CLI root rows differ from verified retained root evidence")
        if native == root / "native":
            expected = _json((root / f'inputs/root-evidence/repetition-{context["repetition"]}/decode.json').read_bytes(), "Codex CLI root native inventory")
            actual = _json((native / "decode.json").read_bytes(), "Codex CLI current native inventory")
            if expected["artifacts"] != actual["artifacts"]:
                raise ValueError("Codex CLI root evidence differs from current native artifacts")
    from .adapters.codex_cli_decoder import decode_codex_cli_bundle
    from .adapters.claude_code_decoder import decode_claude_code_bundle
    from .claude_live import native_facts_from_claude_session
    from .live_metric_comparator import compare_survival_run
    from .v1_public_score import score_public_control_run, validate_format_profile
    reference = {"id": "closed-replay-observer", "sha256": hashlib.sha256(observer_bytes).hexdigest()}
    native_reference = {"id": "native/decode.json", "sha256": hashlib.sha256((native / "decode.json").read_bytes()).hexdigest()}
    declared_paths = {row["path"] for row in _json((native / "decode.json").read_bytes(), "native inventory")["artifacts"]}
    companions = context["required_companions"]
    portable = {"complete_root": context["complete_root"], "companions_present": isinstance(companions, list) and all(name in declared_paths for name in companions), "isolated_decode": True, "canonical_equality": True}
    if context["configuration_id"] == "pi" and not context["complete_root"]:
        # A transcript-only Pi capture cannot establish a missing sidecar or
        # root boundary. The packet-local isolated decode checks below remain
        # measurable independently of that missing acquisition proof.
        portable.update(complete_root=None, companions_present=None)
    if context["configuration_id"] in {"copilot", "antigravity"} and not context["complete_root"] and context["observer_kind"] != "antigravity-capture-v3":
        # A capture without root receipts does not establish whether the
        # session had companions or a complete root boundary.
        portable.update(complete_root=None, companions_present=None)
    common = dict(observer=reference, native_manifest=native_reference, build=context["build"], collected_on=context["collected_on"], result_id=context["result_id"], complete_record_family=context["complete_record_family"], root_repetitions=context["root_repetitions"], native_package=native, observer_document=observer_bytes)
    if context["configuration_id"] == "pi":
        root_index = root / "inputs/pi-root-repetitions.json"
        root_references = []
        if context["root_repetitions"] is not None:
            if not root_index.is_file():
                raise ValueError("Pi root-location evidence index is missing")
            paths = ["inputs/pi-root-repetitions.json"]
            for repetition in (1, 2, 3):
                prefix = ("inputs/capture/" if repetition == context["repetition"] else
                          f"inputs/root-repetitions/repetition-{repetition}/capture/")
                paths.extend(prefix + relative for relative in (
                    "plan.json", "native-root/root-capture.json",
                    "native-root/before-r1/inventory.json",
                    "native-root/after-r1/inventory.json",
                    "native-root/after-r2/inventory.json",
                ))
            for relative in paths:
                path = root / relative
                if not path.is_file():
                    raise ValueError("Pi stable-root metric locator is missing: " + relative)
                raw = path.read_bytes()
                root_references.append({"id": relative,
                                        "sha256": hashlib.sha256(raw).hexdigest()})
        common["pi_root_evidence_locators"] = root_references
    if context["configuration_id"].startswith("codex-"):
        from .codex_format_evidence import build_codex_format_evidence, codex_stdout_event_timestamps
        decoded = decode_codex_cli_bundle(native, workload=workload, complete_root=context["complete_root"], required_companions=context["required_companions"], configuration_id=context["configuration_id"], repetition=context["repetition"])
        projected = dict(decoded)
        # These are native facts only. No observer field is added to projection.
        projected.update({"turns": decoded["facts"]["submitted_turns"], "responses": decoded["facts"]["visible_responses"], "actions": decoded["facts"]["actions"], "results": decoded["facts"]["results"], "file_changes": decoded["facts"]["changed_files"]})
        if context["observer_kind"] == "codex-cli-stdout-v2":
            from .codex_native_projection import project_codex_native
            projected = project_codex_native(decoded)
            common["event_timestamps"] = codex_stdout_event_timestamps(decoded, observer_document=observer_bytes, native_package=native)
        format_document = build_codex_format_evidence(decoded, **common)
    elif context["configuration_id"] == "opencode-cli":
        from .opencode_score_inputs import build_opencode_replay_evidence
        decoded, projected, format_document = build_opencode_replay_evidence(root, native, instance, context, common)
    elif context["configuration_id"] == "copilot":
        from .copilot_score_inputs import build_copilot_replay_evidence
        decoded, projected, format_document = build_copilot_replay_evidence(root, native, instance, context, common)
    elif context["configuration_id"] == "antigravity":
        from .antigravity_score_inputs import build_antigravity_replay_evidence
        decoded, projected, format_document = build_antigravity_replay_evidence(root, native, instance, context, common)
    elif context["configuration_id"] == "deepseek-harness-cli":
        from .dsh_live import decode_dsh_native
        from .dsh_format_evidence import build_dsh_format_evidence
        inventory = _json((native / "decode.json").read_bytes(), "DSH native physical inventory")
        if len(inventory["artifacts"]) != 1:
            raise ValueError("DSH replay requires one native physical generation")
        artifact = inventory["artifacts"][0]
        relative = _relative("native/" + artifact["path"], {"native"})
        native_path = native / PurePosixPath(relative).relative_to("native")
        if hashlib.sha256(native_path.read_bytes()).hexdigest() != artifact["sha256"]:
            raise ValueError("DSH physical file differs from native inventory")
        decoded = decode_dsh_native(native_path)
        projected = decoded
        companion = root / "inputs/native-companions/session-projcache.json"
        closure_options = {}
        cache_unresolved = False
        if companion.exists():
            from .dsh_cache import read_dsh_cache
            from .dsh_native import read_physical
            from .dsh_live import DSHSemanticError
            original = native == root / "native"
            header = _json(read_physical(native_path)[0].splitlines()[0], "DSH header")
            try:
                cached = read_dsh_cache(companion, header=header, decoded=decoded)
                projected = {**decoded, "reconciliation": cached["reconciliation"]}
            except DSHSemanticError:
                if original:
                    raise
                cache_unresolved = True  # selected transcript loss makes its retained cache stale
            closure_options = {"native_companion": companion, "root_repetitions": context["root_repetitions"], "allow_stale_loss_cache": not original}
        format_document = build_dsh_format_evidence(decoded, native_path=native_path, observer_document=observer_bytes,
            run_id=context["run_id"], repetition=context["repetition"], build=context["build"], collected_on=context["collected_on"],
            native_manifest=native_reference, complete_record_family=context["complete_record_family"], **closure_options)
    elif context["configuration_id"] == "pi":
        from .pi_score_inputs import build_pi_format_evidence, project_pi_native
        inventory = _json((native / "decode.json").read_bytes(), "Pi native inventory")
        if len(inventory["artifacts"]) != 1 or inventory["artifacts"][0].get("path") != "session.jsonl":
            raise ValueError("Pi replay requires one complete final native transcript copy")
        artifact = inventory["artifacts"][0]
        native_path = native / "session.jsonl"
        native_bytes = native_path.read_bytes()
        if (len(native_bytes) != artifact.get("size_bytes")
                or hashlib.sha256(native_bytes).hexdigest() != artifact.get("sha256")):
            raise ValueError("Pi native transcript differs from its inventory")
        assertion = _json((root / context["capture_assertion_path"]).read_bytes(), "Pi capture assertion")
        capture_documents = {
            row["path"]: (root / "inputs/capture" / row["path"]).read_bytes()
            for row in assertion["capture_documents"]
        }
        if native == root / "native" and capture_documents.get("turn-r2/native/session.jsonl") != native_bytes:
            raise ValueError("Pi captured source differs from the scored native transcript")
        plan = _json(capture_documents["plan.json"], "Pi capture plan")
        capture_result = _json(capture_documents["capture-result.json"], "Pi capture result")
        common["pi_version"] = capture_result.get("preflight", {}).get("version")
        common["native_artifacts"] = inventory["artifacts"]
        doc_paths = ("index.json", "package.json", "docs/session-format.md", "docs/message-types.md")
        common["pi_format_documents"] = {
            name: (root / "inputs/pi-format" / name).read_bytes()
            for name in doc_paths if (root / "inputs/pi-format" / name).is_file()
        }
        common["pi_root_repetitions"] = context["root_repetitions"]
        common["pi_capture_documents"] = capture_documents
        decoded = project_pi_native(
            native_bytes,
            before_checkout=capture_documents["workspaces/before/fixture_project/checkout.py"],
            after_checkout=capture_documents["turn-r2/workspace/fixture_project/checkout.py"],
            workspace=plan.get("workspace"),
        )
        projected = decoded
        format_document = build_pi_format_evidence(projected, context=context, common=common)
    else:
        from .claude_format_evidence import build_claude_format_evidence
        decoded = decode_claude_code_bundle(native)
        options = context["claude_projection"]
        if not isinstance(options, dict) or set(options) != {"workspace", "usage_mode"}:
            raise ValueError("missing closed Claude projection context")
        manifest = _json((native / "decode.json").read_bytes(), "native inventory")
        if len(manifest["artifacts"]) != 1:
            raise ValueError("Claude replay requires one selected transcript")
        artifact = manifest["artifacts"][0]
        relative = _relative("native/" + artifact["path"], {"native"})
        projected = native_facts_from_claude_session((native / PurePosixPath(relative).relative_to("native")).read_bytes(), run_canary=instance["run_canary"], before_sha256="", after_sha256="", **options)
        # The helper can project caller-supplied filesystem hashes as native
        # facts. They are not native transcript evidence, so none is supplied
        # here. Hashes stay only when the transcript's own edit result yields
        # them (its pre-image, and that pre-image with the recorded edit).
        for change in projected["file_changes"]:
            if change.get("hash_source") not in {"native_tool_result", "native_preimage_and_shell_write"}:
                change.pop("before_sha256", None)
                change.pop("after_sha256", None)
        desktop_options = {"desktop_family": root / "inputs/native-family"} if context["configuration_id"] == "claude-desktop" else {}
        if timestamp_observer_reference is not None:
            desktop_options.update(timestamp_observer=timestamp_observer_reference,
                                   timestamp_observer_document=timestamp_observer_bytes)
        format_document = build_claude_format_evidence(decoded, run_id=context["run_id"], configuration_id=context["configuration_id"], repetition=context["repetition"], **common, **desktop_options)
    measurement = compare_survival_run(observer, projected, portable, configuration_id=context["configuration_id"], repetition=context["repetition"])
    if context["configuration_id"] == "pi":
        from .pi_score_inputs import apply_unresolved_policy
        measurement = apply_unresolved_policy(measurement, json_stdout=context["observer_kind"] == "pi-json-capture-v1")
    if context["configuration_id"] == "copilot":
        # Usage, token semantics and reconciliation are native-attested. With a
        # complete root, a missing response usage record is a proven absence.
        from .copilot_score_inputs import apply_copilot_native_absence
        measurement = apply_copilot_native_absence(measurement, decoded, complete_root=context["complete_root"])
    if context["configuration_id"] == "antigravity":
        # Model identity, usage, token semantics and reconciliation are
        # native-attested. With a complete root, a missing native record is a
        # proven absence; without one the rows stay unresolved.
        from .antigravity_score_inputs import apply_antigravity_native_absence
        measurement = apply_antigravity_native_absence(measurement, decoded, complete_root=context["complete_root"])
    if context["configuration_id"] == "deepseek-harness-cli" and cache_unresolved:
        next(row for row in measurement["metrics"] if row["id"] == "attribution.reconciliation").update(state="unresolved", correct=0)
    for row in measurement["metrics"]:
        if row["id"] in missing:
            row.update(state="unresolved", correct=0)
    # Reuse scoring arithmetic only, discard its constructed-control proof rows.
    score = score_public_control_run(measurement, format_document["profile"]).display()
    summary = {key: score[key] for key in ("categories", "metrics", "blockers", "portable_gate")}
    summary.update(rankable=False, overall=None)
    metrics = [*measurement["metrics"], *validate_format_profile(format_document["profile"])["metrics"]]
    if context["configuration_id"] == "deepseek-harness-cli" and native == root / "native" and (root / "inputs/transformation.json").exists():
        transform = _json((root / "inputs/transformation.json").read_bytes(), "DSH transformation")
        if hashlib.sha256(canonical(metrics)).hexdigest() != transform.get("metric_rows_sha256"):
            raise ValueError("DSH sanitization changed scored metric values")
    return {"schema_version": SCHEMA, "run_id": context["run_id"], "configuration_id": context["configuration_id"], "repetition": context["repetition"],
            "scope": "private_native_to_score_diagnostics", "public_safe": False, "independent_reproduction": False,
            "observer_sha256": reference["sha256"], "observer_mode": context["observer_kind"], "unobserved_primary_metric_ids": missing,
            "native_decode_sha256": hashlib.sha256(canonical(decoded)).hexdigest(), "measurement": measurement,
            "format_evidence": format_document, "metrics": metrics, "score_diagnostics": summary,
            "capture_completeness": "retained_assertion_not_independently_reverified", "source_authenticity": "caller_pinned_packet_integrity_only"}


def _response_loss(root: Path, output: Path, canary: str, configuration: str) -> list[dict[str, Any]]:
    if configuration == "pi":
        contents = _snapshot_tree(root / "native")
        inventory = _json(contents["decode.json"], "Pi loss inventory")
        if len(inventory["artifacts"]) != 1 or inventory["artifacts"][0].get("path") != "session.jsonl":
            raise ValueError("Pi selected-loss control requires one session transcript")
        artifact = inventory["artifacts"][0]
        name = artifact["path"]
        removed, kept = [], []
        for number, line in enumerate(contents[name].splitlines(keepends=True), 1):
            row = _json(line, "Pi loss record")
            message = row.get("message") if row.get("type") == "message" else None
            if (isinstance(message, dict) and message.get("role") == "assistant"
                    and message.get("stopReason") == "stop" and canary in json.dumps(row, ensure_ascii=False)):
                removed.append({"path": name, "line": number, "sha256": hashlib.sha256(line).hexdigest(),
                                "control_transformation": "remove selected final assistant response row only"})
            else:
                kept.append(line)
        if len(removed) != 1:
            raise ValueError("Pi selected-loss control requires exactly one final response row")
        contents[name] = b"".join(kept)
        artifact.update(sha256=hashlib.sha256(contents[name]).hexdigest(), size_bytes=len(contents[name]))
        contents["decode.json"] = canonical(inventory) + b"\n"
        output.mkdir()
        for relative, data in contents.items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return removed
    if configuration == "antigravity":
        contents = _snapshot_tree(root / "native")
        inventory = _json(contents["decode.json"], "Antigravity loss inventory")
        state = _json((root / "inputs/capture/capture-result.json").read_bytes(), "Antigravity capture result")
        database = "capture/conversations/" + str(state.get("conversation_id")) + ".db"
        if database in contents:
            # Whole-state capture: the primary record is the conversation database.
            from .antigravity_conversation_db import read_conversation_db, remove_step, row_proof, text
            artifact = next(entry for entry in inventory["artifacts"] if entry["path"] == database)
            rows = [row for row in read_conversation_db(contents[database])["tables"]["steps"]
                    if row["step_type"] == 15 and canary in (text(row["step_payload"], 20, 1) or "")]
            if len(rows) != 1:
                raise ValueError("Antigravity loss control requires one final response")
            removed = [{"path": database, "table": "steps", "idx": rows[0]["idx"], "sha256": hashlib.sha256(row_proof(rows[0])).hexdigest(),
                        "control_transformation": "delete the selected model step row only"}]
            contents[database] = remove_step(contents[database], rows[0]["idx"])
            artifact.update(sha256=hashlib.sha256(contents[database]).hexdigest(), size_bytes=len(contents[database]))
            contents["decode.json"] = canonical(inventory) + b"\n"
            output.mkdir()
            for relative, data in contents.items():
                path = output / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return removed
        name = "capture/" + state["native_primary"]
        artifact = next((entry for entry in inventory["artifacts"] if entry["path"] == name), None)
        if artifact is None:
            raise ValueError("Antigravity primary transcript absent")
        kept, removed = [], []
        for number, line in enumerate(contents[name].splitlines(keepends=True), 1):
            row = _json(line, "Antigravity loss record")
            if row.get("type") == "PLANNER_RESPONSE" and canary in str(row.get("content", "")):
                removed.append({"path": name, "line": number, "sha256": hashlib.sha256(line).hexdigest()})
            else:
                kept.append(line)
        if len(removed) != 1:
            raise ValueError("Antigravity loss control requires one final response")
        contents[name] = b"".join(kept)
        artifact.update(sha256=hashlib.sha256(contents[name]).hexdigest(), size_bytes=len(contents[name]))
        contents["decode.json"] = canonical(inventory) + b"\n"
        output.mkdir()
        for relative, data in contents.items():
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return removed
    if configuration == "copilot":
        contents = _snapshot_tree(root / "native")
        inventory = _json(contents["decode.json"], "Copilot loss inventory")
        artifact = next((entry for entry in inventory["artifacts"] if entry["path"] == "events.jsonl"), None)
        if artifact is None:
            raise ValueError("Copilot native events absent")
        kept, removed = [], []
        for number, line in enumerate(contents["events.jsonl"].splitlines(keepends=True), 1):
            row = _json(line, "Copilot loss record")
            if row.get("type") == "assistant.message" and canary in str(row.get("data", {}).get("content", "")):
                removed.append({"path": "events.jsonl", "line": number, "sha256": hashlib.sha256(line).hexdigest()})
            else:
                kept.append(line)
        if len(removed) != 1:
            raise ValueError("Copilot loss control requires one final response")
        contents["events.jsonl"] = b"".join(kept)
        artifact.update(sha256=hashlib.sha256(contents["events.jsonl"]).hexdigest(), size_bytes=len(contents["events.jsonl"]))
        contents["decode.json"] = canonical(inventory) + b"\n"
        output.mkdir()
        for name, data in contents.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return removed
    if configuration == "opencode-cli":
        contents = _snapshot_tree(root / "native")
        inventory = _json(contents["decode.json"], "OpenCode loss inventory")
        removed = []
        with tempfile.TemporaryDirectory(prefix="bench-opencode-loss-") as directory:
            clone = Path(directory).resolve()
            for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm"):
                (clone / name).write_bytes(contents[name])
            with sqlite3.connect(clone / "opencode.db") as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                for part_id, part_data, message_data in connection.execute('SELECT p.id,p.data,m.data FROM part p JOIN message m ON m.id=p.message_id'):
                    part = _json(part_data.encode() if isinstance(part_data, str) else part_data, "OpenCode loss part")
                    message = _json(message_data.encode() if isinstance(message_data, str) else message_data, "OpenCode loss message")
                    if message.get("role") == "assistant" and part.get("type") == "text" and isinstance(part.get("text"), str) and canary in part["text"]:
                        removed.append({"table": "part", "id": part_id, "sha256": hashlib.sha256(canonical(part)).hexdigest(), "control_transformation": "delete selected assistant text part only; preserve message metadata and other SQL rows"})
                if not removed:
                    raise ValueError("OpenCode selected-loss control found no assistant response part")
                connection.executemany('DELETE FROM part WHERE id=?', [(row["id"],) for row in removed])
                connection.commit()
                # Snapshot while the WAL writer remains open; closing the last
                # connection may checkpoint and unlink its WAL/SHM companions.
                for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm"):
                    contents[name] = (clone / name).read_bytes()
        for row in inventory["artifacts"]:
            row.update(sha256=hashlib.sha256(contents[row["path"]]).hexdigest(), size_bytes=len(contents[row["path"]]))
        contents["decode.json"] = canonical(inventory) + b"\n"
        output.mkdir()
        for name, data in contents.items():
            (output / name).write_bytes(data)
        return removed
    contents = _snapshot_tree(root / "native")
    inventory = _json(contents["decode.json"], "native inventory")
    removed = []
    if configuration == "deepseek-harness-cli":
        from .dsh_native import read_physical
        artifact = inventory["artifacts"][0]
        original_name = artifact["path"]
        raw, _ = read_physical(root / "native" / original_name)
        kept = []
        for number, line in enumerate(raw.splitlines(keepends=True), 1):
            row = _json(line, "DSH loss-control native record")
            if row.get("type") == "assistant/message" and canary in json.dumps(row.get("data", {}).get("message", {}).get("content"), ensure_ascii=False):
                removed.append({"path": original_name, "logical_line": number, "sha256": hashlib.sha256(line).hexdigest(),
                                "control_transformation": "remove selected assistant record; reindex dense seq; serialize plain JSONL"})
            else:
                if number > 1:
                    row["seq"] = len(kept) - 1
                kept.append(canonical(row) + b"\n")
        if not removed:
            raise ValueError("DSH selected-loss control found no native response record")
        plain = b"".join(kept)
        del contents[original_name]
        contents["session.v4.jsonl"] = plain
        artifact.update(path="session.v4.jsonl", sha256=hashlib.sha256(plain).hexdigest(), size_bytes=len(plain))
    for artifact in inventory["artifacts"]:
        path = artifact["path"]
        if configuration == "deepseek-harness-cli":
            continue
        if not path.endswith(".jsonl"):
            continue
        kept = []
        for number, line in enumerate(contents[path].splitlines(keepends=True), 1):
            if not line.strip():
                kept.append(line)
                continue
            row = _json(line, "loss-control native record")
            payload = row.get("payload", {})
            if configuration.startswith("codex-"):
                response = row.get("type") == "event_msg" and payload.get("type") == "agent_message" or row.get("type") == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant"
            else:
                response = row.get("type") == "assistant" and row.get("message", {}).get("role") == "assistant" or row.get("type") == "result"
            if response and canary in json.dumps(row, ensure_ascii=False):
                removed.append({"path": path, "line": number, "sha256": hashlib.sha256(line).hexdigest()})
            else:
                kept.append(line)
        contents[path] = b"".join(kept)
        artifact.update(sha256=hashlib.sha256(contents[path]).hexdigest(), size_bytes=len(contents[path]))
    if not removed:
        raise ValueError("selected-loss control found no native response record")
    contents["decode.json"] = canonical(inventory) + b"\n"
    output.mkdir()
    for name, data in contents.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return removed


def execute_score_packet(root: Path, *, record_expected: bool = False) -> dict[str, Any]:
    """Runner entry point; operates on its freshly copied packet only."""
    contents = _snapshot_tree(root)
    manifest = validate_score_packet(contents)
    intact = _diagnostics(root)
    instance = _json(contents["inputs/workload.json"], "workload")
    canary = instance["turns"][1]["response_canary"]
    with tempfile.TemporaryDirectory(prefix="bench-score-loss-") as temp:
        damaged_root = Path(temp).resolve() / "native"
        removed = _response_loss(root, damaged_root, canary, manifest["configuration_id"])
        damaged = _diagnostics(root, native=damaged_root)
    before = next(row for row in intact["metrics"] if row["id"] == "work.visible_responses")
    after = next(row for row in damaged["metrics"] if row["id"] == "work.visible_responses")
    before_rationale = intact["format_evidence"]["profile"]["broad_evidence"]["broad.readable_rationale"]
    after_rationale = damaged["format_evidence"]["profile"]["broad_evidence"]["broad.readable_rationale"]
    if before["observed_eligible"] != after["observed_eligible"] or not before["correct"] > after["correct"] or before_rationale["response_ids"] != after_rationale["response_ids"]:
        raise ValueError("native selected loss did not preserve observer denominator and reduce response correctness")
    diagnostics = {"intact": intact, "selected_loss": {"canary": canary, "removed_records": removed, "observer_denominator_unchanged": True, "response_correctness_reduced": True, "damaged": damaged}}
    digest = hashlib.sha256(canonical(diagnostics)).hexdigest()
    if not record_expected and digest != manifest["expected_diagnostics_sha256"]:
        raise ValueError("native-to-score diagnostics differ from expected replay")
    return {"schema_version": SCHEMA, "manifest_sha256": hashlib.sha256(contents["manifest.json"]).hexdigest(), "diagnostics_sha256": digest, "diagnostics": diagnostics,
            "python_isolated": True, "os_sandboxed": False, "public_safe": False, "independent_reproduction": False,
            "python_version": sys.version.split()[0], "python_implementation": sys.implementation.name,
            "source_inventory": [row for row in manifest["files"] if row["path"].startswith("runtime/")]}


def _sandbox_profile(root: Path) -> str:
    """Allow copied inputs/runtime and interpreter libraries, deny source/home/network."""
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ValueError("OS score replay requires macOS sandbox-exec; no unsandboxed fallback")
    import sysconfig
    roots = ["/System", "/usr", "/bin", "/sbin", "/Library", "/private/etc", "/dev",
             str(Path(sys.executable).resolve()), str(Path(sys.prefix).resolve()),
             str(Path("/opt/homebrew/opt/sqlite/lib").resolve()),
             str(Path(sysconfig.get_paths()["stdlib"]).resolve()), str(root.parent.resolve())]
    if (_json((root / "manifest.json").read_bytes(), "manifest"))["configuration_id"] == "deepseek-harness-cli":
        roots.append(str(Path(ctypes.util.find_library("zstd")).resolve().parent))
    rules = ['(version 1)', '(deny default)', '(allow process-exec)', '(deny process-fork)',
             '(allow sysctl-read)', '(allow mach-lookup)', '(allow file-read-metadata)', '(allow file-read* (literal "/"))']
    rules.extend(f'(allow file-read* (subpath {json.dumps(value)}))' for value in dict.fromkeys(roots))
    ancestors = {parent for value in roots for parent in Path(value).resolve().parents}
    rules.extend(f'(allow file-read* (literal {json.dumps(str(value))}))' for value in sorted(ancestors))
    rules.append(f'(allow file-write* (subpath {json.dumps(str(root.parent.resolve()))}))')
    return "\n".join(rules)


def _sandbox_probes(root: Path, profile: str) -> dict[str, Any]:
    """Actually attempt forbidden operations under the exact replay profile."""
    with tempfile.TemporaryDirectory(prefix="bench-score-denied-") as directory, socket.socket() as listener:
        denied = Path(directory).resolve()
        (denied / "source.txt").write_text("must stay unread")
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        script = '''import json,socket,sys
from pathlib import Path
checks={}
for name,operation in [("outside_read",lambda:Path(sys.argv[1]).read_bytes()),("outside_write",lambda:Path(sys.argv[2]).write_text("forbidden")),("network_connect",lambda:socket.create_connection(("127.0.0.1",int(sys.argv[3])),timeout=1))]:
 try: operation(); checks[name]=False
 except PermissionError: checks[name]=True
print(json.dumps(checks))'''
        env = {"PATH": "/usr/bin:/bin", "HOME": str(root.parent), "TMPDIR": str(root.parent), "PYTHONHASHSEED": "0"}
        result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, sys.executable, "-I", "-B", "-c", script,
                                 str(denied / "source.txt"), str(denied / "write.txt"), str(listener.getsockname()[1])],
                                env=env, cwd=root.parent, capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise ValueError("OS isolation probes failed: " + result.stderr.strip())
        checks = _json(result.stdout.encode(), "OS isolation probes")
        if checks != {"outside_read": True, "outside_write": True, "network_connect": True} or (denied / "write.txt").exists():
            raise ValueError("OS score replay did not deny all isolation probes")
        return checks


def _run(root: Path, *, record_expected: bool = False, os_sandboxed: bool = False) -> dict[str, Any]:
    root = root.resolve()
    command = [sys.executable, "-I", "-B", str(root / "runtime/scripts/replay_score_package.py"), str(root)]
    if record_expected:
        command.append("--record-expected")
    profile = _sandbox_profile(root) if os_sandboxed else None
    probes = _sandbox_probes(root, profile) if profile else None
    env = None
    if profile:
        command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(root.parent), "TMPDIR": str(root.parent), "PYTHONHASHSEED": "0"}
    before = _snapshot_tree(root)
    result = subprocess.run(command, cwd=root.parent, env=env, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise ValueError(f"score replay failed: {result.stderr.strip()}")
    if before != _snapshot_tree(root):
        raise ValueError("score replay altered its pinned input/runtime")
    receipt = _json(result.stdout.encode(), "score replay receipt")
    receipt["os_sandboxed"] = os_sandboxed
    if profile:
        receipt["os_isolation"] = {"backend": "macOS sandbox-exec", "profile_sha256": hashlib.sha256(profile.encode()).hexdigest(), "probes": probes,
                                   "scope": "copied packet and temporary work area; interpreter and installed dependency reads allowed"}
    return receipt


def build_score_replay_package(native_bundle: Path, destination: Path, *, workload_document: bytes,
                               observer_document: bytes, context_document: bytes,
                               supporting_documents: Mapping[str, bytes], source_root: Path | None = None) -> dict[str, Any]:
    """Publish a new packet after isolated scoring of the snapshotted closure.

    Supporting names are relative to inputs/. They contain retained completeness
    receipts and, for raw Codex stdout, exact streams plus original digest receipts.
    No caller-supplied metric/profile/score document is accepted as an input.
    """
    native_bundle, destination = Path(native_bundle), Path(destination)
    if destination.exists() or any(part.is_symlink() for part in (destination, *destination.parents)):
        raise ValueError("score replay destination exists or is symlinked")
    if destination.resolve().is_relative_to(native_bundle.resolve()):
        raise ValueError("score replay output must be outside native input")
    source_root = source_root or Path(__file__).resolve().parents[1]
    native_bytes = _snapshot_tree(native_bundle)
    contents = {f"native/{name}": data for name, data in native_bytes.items()}
    contents.update({"inputs/workload.json": workload_document, "inputs/observer.json": observer_document, "inputs/context.json": context_document})
    for name, data in supporting_documents.items():
        relative = _relative(f"inputs/{name}", {"inputs"})
        if relative in contents or not isinstance(data, bytes):
            raise ValueError("supporting input collides or is not exact bytes")
        contents[relative] = data
    sources = {}
    context = _json(context_document, "context")
    extra = DSH_SOURCE_FILES if context["configuration_id"] == "deepseek-harness-cli" else ()
    for name in (*SOURCE_FILES, *FORMAT_EXTENSION_FILES, *extra, *CURRENT_EXTRA_SOURCES.get(context["configuration_id"], ())):
        sources[name] = _read_runtime_source(source_root / name)
        contents[f"runtime/{name}"] = sources[name]
    for name in _NAMESPACES:
        contents[f"runtime/{name}"] = b'"""Closed score replay namespace."""\n'
    manifest = {"schema_version": SCHEMA, "configuration_id": context["configuration_id"], "repetition": context["repetition"],
                "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)} for name, data in sorted(contents.items())],
                "expected_diagnostics_sha256": "0" * 64, "scope": "private_native_to_score_diagnostics", "public_safe": False, "independent_reproduction": False}
    if context["configuration_id"] == "deepseek-harness-cli":
        manifest["platform_dependencies"] = [_zstd_dependency()]
    contents["manifest.json"] = canonical(manifest) + b"\n"
    validate_score_packet(contents)
    with tempfile.TemporaryDirectory(prefix="bench-score-build-") as temp:
        root = Path(temp) / "packet"
        root.mkdir()
        for name, data in contents.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        receipt = _run(root, record_expected=True)
    if native_bytes != _snapshot_tree(native_bundle) or any(data != _read_runtime_source(source_root / name) for name, data in sources.items()):
        raise ValueError("score replay input/source changed during packaging")
    manifest["expected_diagnostics_sha256"] = receipt["diagnostics_sha256"]
    contents["manifest.json"] = canonical(manifest) + b"\n"
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in contents.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return manifest


def replay_score_package(package: Path, *, expected_manifest_sha256: str, os_sandboxed: bool = False) -> dict[str, Any]:
    """Verify a pinned packet before executing its immutable source copy."""
    if not isinstance(expected_manifest_sha256, str) or not _SHA.fullmatch(expected_manifest_sha256):
        raise ValueError("score replay requires a caller-pinned manifest SHA-256")
    contents = _snapshot_tree(Path(package))
    if hashlib.sha256(contents["manifest.json"]).hexdigest() != expected_manifest_sha256:
        raise ValueError("score replay manifest differs from trusted expected digest")
    validate_score_packet(contents)
    with tempfile.TemporaryDirectory(prefix="bench-score-replay-") as temp:
        root = Path(temp) / "packet"
        root.mkdir()
        for name, data in contents.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return _run(root, os_sandboxed=os_sandboxed)


def build_dsh_score_replay_package(native_path: Path, destination: Path, *, workload_document: bytes,
                                   observer_document: bytes, context_document: bytes,
                                   supporting_documents: Mapping[str, bytes], source_root: Path | None = None) -> dict[str, Any]:
    """Retain exact DSH physical bytes plus a generated packaging inventory.

    decode.json is explicitly packaging metadata. DSH schema credit comes only
    from the original native header read by the physical/semantic decoder.
    """
    path = Path(native_path)
    if path.name not in {"session.v4.jsonl", "session.v4.jsonl.zstd"}:
        raise ValueError("DSH packet requires the captured native generation filename")
    data = _read_runtime_source(path)
    with tempfile.TemporaryDirectory(prefix="bench-dsh-score-source-") as temp:
        native = Path(temp).resolve() / "native"
        native.mkdir()
        (native / path.name).write_bytes(data)
        inventory = {"format": "dsh-native-v4", "artifacts": [{"id": "dsh-native", "path": path.name,
                     "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []}]}
        (native / "decode.json").write_bytes(canonical(inventory) + b"\n")
        result = build_score_replay_package(native, destination, workload_document=workload_document,
                                           observer_document=observer_document, context_document=context_document,
                                           supporting_documents=supporting_documents, source_root=source_root)
    if data != _read_runtime_source(path):
        raise ValueError("DSH captured physical source changed during packaging")
    return result


def verify_score_packet_tamper_controls(package: Path, *, expected_manifest_sha256: str) -> dict[str, Any]:
    """Exercise host integrity validation on modified snapshots; execute no code."""
    contents = _snapshot_tree(Path(package))
    validate_score_packet(contents)
    if hashlib.sha256(contents["manifest.json"]).hexdigest() != expected_manifest_sha256:
        raise ValueError("tamper controls require the pinned intact packet")
    targets = ["inputs/workload.json", "inputs/observer.json", "inputs/context.json",
               next(name for name in sorted(contents) if name.startswith("native/") and name.endswith((".jsonl", ".jsonl.zstd", ".db"))),
               "runtime/session_bench/v1_public_score.py", "runtime/scripts/replay_score_package.py"]
    if "inputs/timestamp-observer.json" in contents:
        targets.insert(2, "inputs/timestamp-observer.json")
    controls = []
    for target in targets:
        mutated = {**contents, target: contents[target] + b"tamper"}
        try:
            validate_score_packet(mutated)
        except ValueError:
            controls.append({"target": target, "rejected_before_execution": True})
        else:
            raise ValueError("tamper control accepted altered input/source")
    return {"status": "passed", "code_executed": False, "controls": controls,
            "scope": "host_integrity_validation_against_pinned_inventory", "independent_reproduction": False}
