#!/usr/bin/env python3
"""Finalize one already captured Claude Desktop Code run offline.

The caller supplies the exact transcript and Desktop metadata files selected by
the capture operator.  The command never searches a Claude root, opens a
neighbouring session, launches Claude, or publishes a score.  It writes an
additive private evidence directory below the run's existing ``capture``
directory.

The Desktop recording family used by this adapter is the explicit pair of
``transcript/session.jsonl`` and ``desktop/session.json`` supplied by the
operator.  The pair is joined by the CLI session ID and Desktop session
metadata.  A future surface can add more companions without changing this
finalizer; an undeclared companion is never silently discovered.
"""

from __future__ import annotations

import argparse
import copy
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for path in (REPO, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.claude_desktop_root import validate_claude_desktop_family  # noqa: E402
from session_bench.claude_desktop_source_discovery import canonical_claude_desktop_roots  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.claude_live import native_facts_from_claude_session  # noqa: E402
from session_bench.claude_desktop_hook_observer import (  # noqa: E402
    SCHEMA as HOOK_SCHEMA,
    PROJECTION_SCHEMA as HOOK_PROJECTION_SCHEMA,
    _json_object as _hook_json_object,
    _validate_ledger as _validate_hook_ledger,
)
from session_bench.claude_desktop_otel_usage import (  # noqa: E402
    ClaudeDesktopOtelUsageError,
    join_claude_desktop_response_usage,
    _strict_json as _strict_otel_json,
)
from session_bench.format_timestamp_population import build_observer_timestamp_evidence  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.live_observer import build_opencode_live_observer  # noqa: E402
from session_bench.survival_evidence import (  # noqa: E402
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_prospective_evidence_input,
)
from session_bench.v1_public_score import (  # noqa: E402
    FORMAT_METRICS,
    PUBLIC_METRICS,
    SURVIVAL_METRICS,
    validate_format_evidence,
)
from session_bench.workload_instance import instantiate_workload  # noqa: E402

from run_claude_survival import _build_31_evidence  # noqa: E402


RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GUI_CANARY_KEYS = {
    1: "r1_canary_visible",
    2: "r2_canary_visible",
}


class FinalizeError(RuntimeError):
    """The selected Desktop evidence cannot be finalized fail-closed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"expected one ordinary file: {path}")
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> str:
    if path.exists() or path.is_symlink():
        raise FinalizeError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value) + b"\n"
    path.write_bytes(data)
    return _sha256_bytes(data)


def _load_json(path: Path, *, object_required: bool = True) -> Any:
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"selected input is not an ordinary file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError(f"invalid JSON input: {path}") from exc
    if object_required and not isinstance(value, dict):
        raise FinalizeError(f"JSON input must be an object: {path}")
    return value


def _ordinary_source(path: Path, label: str) -> Path:
    path = Path(path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"{label} must be one explicitly selected ordinary file")
    return path.resolve()


def _validate_otel_usage_receipt(
    path: Path, *, run_id: str, session_id: str,
    workload: Mapping[str, Any], gui_receipt: Mapping[str, Any],
) -> tuple[bytes, dict[str, dict[str, Any]]]:
    """Read one private loopback receipt and bind usage to visible canaries."""
    path = Path(path).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise FinalizeError("OTel usage receipt must be a private ordinary file")
            raw = stream.read(8 * 1024 * 1024 + 1)
    except OSError as exc:
        raise FinalizeError("OTel usage receipt must be a private ordinary file") from exc
    if not raw or len(raw) > 8 * 1024 * 1024:
        raise FinalizeError("OTel usage receipt is empty or oversized")
    try:
        value = _strict_otel_json(raw)
        if not isinstance(value, Mapping):
            raise ClaudeDesktopOtelUsageError("OTel receipt must be an object")
        if value.get("capture_scope") != "single synthetic Claude Desktop Code (Local) run":
            raise ClaudeDesktopOtelUsageError("OTel receipt capture scope is invalid")
        collector = value.get("collector")
        if not isinstance(collector, Mapping) or set(collector) != {
            "listener", "raw_prompt_content_persisted", "raw_response_content_persisted",
            "raw_api_body_content_persisted", "unselected_attributes_persisted",
        }:
            raise ClaudeDesktopOtelUsageError("OTel receipt collector fields are unexpected")
        common_event_fields = {
            "kind", "session_id", "prompt_id", "request_id", "model",
            "query_source", "effort", "event_sequence", "event_timestamp",
        }
        rows = value.get("events")
        if not isinstance(rows, list):
            raise ClaudeDesktopOtelUsageError("OTel receipt events are malformed")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ClaudeDesktopOtelUsageError("OTel receipt event is malformed")
            allowed = common_event_fields | (
                {"usage", "success"} if row.get("kind") == "api_request" else {
                    "response_sha256", "response_length", "response_length_semantics",
                    "matched_response_canaries", "message_uuid",
                }
            )
            if set(row) - allowed:
                raise ClaudeDesktopOtelUsageError("OTel receipt contains unselected event fields")
        joined = join_claude_desktop_response_usage(
            value, run_id=run_id, session_id=session_id,
            workload=workload, gui_receipt=gui_receipt,
        )
    except ClaudeDesktopOtelUsageError as exc:
        raise FinalizeError(f"invalid OTel usage receipt: {exc}") from exc
    return raw, joined


def _attach_otel_usage(
    observer: Mapping[str, Any], joined: Mapping[str, Mapping[str, Any]],
    *, receipt_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach independently collected request usage without native ingestion."""
    result = copy.deepcopy(observer)
    responses = [
        event for event in result.get("events", [])
        if isinstance(event, Mapping)
        and event.get("kind") == "assistant_response"
        and event.get("population_role") == "primary_scored"
    ]
    if len(responses) != 2:
        raise FinalizeError("OTel usage requires exactly two primary displayed responses")
    attached: set[str] = set()
    for event in responses:
        fields = event.get("fields")
        if not isinstance(fields, dict):
            raise FinalizeError("observer response fields are malformed")
        turn_id = fields.get("turn_id")
        usage = joined.get(turn_id) if isinstance(turn_id, str) else None
        if not isinstance(usage, Mapping) or usage.get("turn_id") != turn_id:
            raise FinalizeError("OTel usage does not bind every visible response turn")
        if fields.get("canary") != usage.get("response_canary"):
            raise FinalizeError("OTel response canary differs from independent GUI observation")
        if turn_id in attached:
            raise FinalizeError("OTel usage turn is assigned to multiple visible responses")
        attached.add(turn_id)
        fields.update({
            "usage_id": usage["request_id"],
            "usage": dict(usage["usage"]),
            "usage_source": "loopback_collected_claude_code_otel_api_request",
            "usage_prompt_id": usage["prompt_id"],
            "usage_request_id": usage["request_id"],
            "usage_request_ids": list(usage["request_ids"]),
            "usage_request_count": usage["request_count"],
            "usage_model": usage["model"],
            "usage_event_timestamp": usage["usage_event_timestamp"],
            "response_event_timestamp": usage["response_event_timestamp"],
            "otel_response_sha256": usage["response_sha256"],
            "token_semantics": dict(usage["token_semantics"]),
        })
    if attached != set(joined):
        raise FinalizeError("OTel usage join contains unassigned or missing turns")
    result["capture_time_usage_receipt"] = dict(receipt_binding)
    return result


def _safe_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise FinalizeError("run_id must contain only letters, digits, underscore, or hyphen")
    return run_id


def _source_location_receipt(transcript: Path, desktop_metadata: Path) -> dict[str, Any]:
    """Bind only the two caller-selected files; never enumerate a source root."""
    sources = []
    for role, path in (("transcript", transcript), ("desktop_metadata", desktop_metadata)):
        source = _ordinary_source(path, role)
        sources.append({"role": role, "resolved_path": str(source), "sha256": _sha256(source)})
    return {
        "schema_version": "session-bench-claude-desktop-source-location-receipt-v1",
        "provenance": "caller-selected source path", "sources": sources,
        "source_root_scanned": False, "root_completeness_proven": False,
        "location_metric_resolved": False,
    }


def _validate_source_discovery_receipt(
    path: Path, *, run_id: str, repetition: int, cli_session_id: str,
    desktop_session_id: str, source_location: Mapping[str, Any], family_package: Path,
    expected_roots: Mapping[str, Path] | None = None,
) -> bytes:
    """Bind a single discovery attestation; it is not a three-run root proof."""
    path = Path(path).expanduser()
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise FinalizeError("discovery receipt must be a regular file")
            raw = stream.read(2_000_001)
        receipt = _hook_json_object(raw)
        keys = {"schema_version", "run_id", "repetition", "cli_session_id", "desktop_session_id",
                "metadata_only_discovery", "personal_history_content_read", "unrelated_content_read",
                "complete_inventories", "isolated_pair", "selected_artifacts", "root_summaries", "proof_sha256"}
        if set(receipt) != keys:
            raise ValueError("discovery schema mismatch")
        body = {key: value for key, value in receipt.items() if key != "proof_sha256"}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        if receipt["schema_version"] != "session-bench-claude-desktop-source-discovery-v1" or receipt["proof_sha256"] != _sha256_bytes(encoded):
            raise ValueError("discovery proof hash mismatch")
        if type(receipt["repetition"]) is not int or any(receipt[key] != expected for key, expected in (
            ("run_id", run_id), ("repetition", repetition), ("cli_session_id", cli_session_id), ("desktop_session_id", desktop_session_id),
        )):
            raise ValueError("discovery identity mismatch")
        if any(receipt[key] is not True for key in ("metadata_only_discovery", "complete_inventories", "isolated_pair")) or any(receipt[key] is not False for key in ("personal_history_content_read", "unrelated_content_read")):
            raise ValueError("unsafe or incomplete discovery")
        artifacts, summaries = receipt["selected_artifacts"], receipt["root_summaries"]
        if not isinstance(artifacts, list) or len(artifacts) != 2 or not isinstance(summaries, list) or len(summaries) != 2:
            raise ValueError("discovery population mismatch")
        roots = {"transcript": "claude-projects", "desktop_metadata": "claude-desktop-sessions"}
        canonical_roots = canonical_claude_desktop_roots() if expected_roots is None else expected_roots
        selected = {row["role"]: row for row in artifacts}
        summary_by_root = {row["root_id"]: row for row in summaries}
        if set(selected) != set(roots) or set(summary_by_root) != set(roots.values()):
            raise ValueError("discovery roles mismatch")
        for summary in summaries:
            if set(summary) != {"root_id", "source_root_sha256", "filesystem_id_sha256", "before_entry_count", "after_entry_count", "unrelated_entry_count", "unrelated_inventory_sha256"}:
                raise ValueError("discovery root schema mismatch")
            if any(type(summary[key]) is not int or summary[key] < 0 for key in ("before_entry_count", "after_entry_count", "unrelated_entry_count")):
                raise ValueError("discovery root counts invalid")
            if summary["after_entry_count"] < summary["before_entry_count"] or summary["unrelated_entry_count"] != summary["after_entry_count"] - 1:
                raise ValueError("discovery root counts inconsistent")
            if any(not isinstance(summary[key], str) or not SHA256_RE.fullmatch(summary[key]) for key in ("source_root_sha256", "filesystem_id_sha256", "unrelated_inventory_sha256")):
                raise ValueError("discovery root digest invalid")
        copied_paths = {"transcript": "transcript/session.jsonl", "desktop_metadata": "desktop/session.json"}
        supplied = {source["role"]: source for source in source_location["sources"]}
        if set(supplied) != set(roots):
            raise ValueError("discovery supplied roles mismatch")
        for role, artifact in selected.items():
            source = supplied[role]
            if set(artifact) != {"role", "source_path", "relative_path", "source_root_sha256", "filesystem_id_sha256", "device", "inode", "size_bytes", "ctime_ns", "mtime_ns", "sha256"}:
                raise ValueError("discovery artifact schema mismatch")
            # The caller may supply an immutable capture-time copy.  Bind its
            # bytes to the receipt while retaining the receipt's canonical
            # original path as the root-location proof.  The original can
            # legitimately drift after capture (for example, lastFocusedAt in
            # Desktop metadata), so never reopen or compare its current bytes.
            if not Path(artifact["source_path"]).is_absolute():
                raise ValueError("discovery selected path mismatch")
            relative = artifact["relative_path"]
            if not isinstance(relative, str) or not relative or PurePosixPath(relative).is_absolute() or str(PurePosixPath(relative)) != relative or ".." in PurePosixPath(relative).parts:
                raise ValueError("discovery relative path invalid")
            suffix = "/" + relative
            if not artifact["source_path"].endswith(suffix):
                raise ValueError("discovery relative path mismatch")
            root = artifact["source_path"][:-len(suffix)]
            summary = summary_by_root[roots[role]]
            if root != str(canonical_roots[role]) or artifact["source_path"] != str(canonical_roots[role] / relative):
                raise ValueError("discovery selected path is outside the canonical Claude root")
            if not root or _sha256_bytes(root.encode()) != artifact["source_root_sha256"] or any(artifact[key] != summary[key] for key in ("source_root_sha256", "filesystem_id_sha256")):
                raise ValueError("discovery source root mismatch")
            for key in ("device", "inode", "ctime_ns", "mtime_ns", "size_bytes"):
                if type(artifact[key]) is not int or artifact[key] < (0 if key == "size_bytes" else 1):
                    raise ValueError("discovery file metadata invalid")
            copied = family_package / copied_paths[role]
            if artifact["sha256"] != source["sha256"] or artifact["sha256"] != _sha256(copied) or artifact["size_bytes"] != copied.stat().st_size:
                raise ValueError("discovery artifact hash or size mismatch")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise FinalizeError(f"invalid source discovery receipt: {exc}") from exc
    return raw


def _validate_hook_receipt(
    path: Path, *, run_id: str, session_id: str, workspace: str, fixture_root: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Validate selected capture-time receipts without reading the original cwd.

    The explicitly selected Desktop metadata supplies the original workspace
    identity. The preserved synthetic project supplies the allowed fixture
    paths, even when the project was relocated after capture. Hook timestamps
    are receipt clock observations, never native source timestamps.
    """
    path = Path(path).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise FinalizeError("hook receipt must be one private ordinary JSONL file")
            raw = stream.read(16_000_001)
    except OSError as exc:
        raise FinalizeError("hook receipt must be one private ordinary JSONL file") from exc
    if not raw or len(raw) > 16_000_000 or not raw.endswith(b"\n"):
        raise FinalizeError("hook receipt is empty, too large, or incomplete")
    workspace_path = PurePosixPath(workspace)
    if not workspace_path.is_absolute() or str(workspace_path) != workspace or ".." in workspace_path.parts:
        raise FinalizeError("hook workspace must be the exact absolute synthetic workspace")
    identity = {"run_id": run_id, "session_id": session_id, "workspace": workspace}
    allowed = {"schema", *identity, "tool_use_id", "event_type", "tool_name",
               "hook_observed_at", "timestamp_provenance", "result_status", "result_sha256",
               "fixture_relative_target", "command_sha256", "command_projection_schema",
               "command_projection_sha256", "duration_ms"}
    previous = None
    try:
        rows = [_hook_json_object(line) for line in raw.splitlines()]
        for row in rows:
            if set(row) - allowed or row.get("schema") != HOOK_SCHEMA:
                raise ValueError("unsupported hook receipt fields or schema")
            if row.get("timestamp_provenance") != "local_hook_receipt_clock":
                raise ValueError("hook timestamp provenance mismatch")
            stamp = row.get("hook_observed_at")
            if not isinstance(stamp, str) or not stamp.endswith("Z"):
                raise ValueError("hook receipt timestamp must be UTC")
            observed = datetime.fromisoformat(stamp[:-1] + "+00:00")
            if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
                raise ValueError("invalid hook receipt clock")
            if previous is not None and observed < previous:
                raise ValueError("hook receipt clock moves backwards")
            previous = observed
            tool = row.get("tool_name")
            if not isinstance(tool, str) or not tool or tool != tool.strip() or "\x00" in tool:
                raise ValueError("invalid hook tool name")
            target = row.get("fixture_relative_target")
            command = row.get("command_sha256")
            if (target is None) == (command is None):
                raise ValueError("hook must identify exactly one target or command digest")
            if target is not None:
                if not isinstance(target, str) or not target or "\x00" in target:
                    raise ValueError("invalid hook fixture path")
                relative = PurePosixPath(target)
                if relative.is_absolute() or str(relative) != target or ".." in relative.parts:
                    raise ValueError("hook fixture path escapes or is not canonical")
                candidate = fixture_root.joinpath(*relative.parts)
                if any(part.is_symlink() for part in [candidate, *candidate.parents] if part == fixture_root or fixture_root in part.parents):
                    raise ValueError("hook fixture path contains a symlink")
                if not candidate.is_file():
                    raise ValueError("hook fixture path is not in the preserved synthetic project")
            elif not isinstance(command, str) or not SHA256_RE.fullmatch(command):
                raise ValueError("invalid hook command digest")
            semantic_digest = row.get("command_projection_sha256")
            semantic_schema = row.get("command_projection_schema")
            if (semantic_digest is None) != (semantic_schema is None):
                raise ValueError("incomplete hook semantic projection binding")
            if semantic_digest is not None and (semantic_schema != HOOK_PROJECTION_SCHEMA
                                                or not isinstance(semantic_digest, str)
                                                or not SHA256_RE.fullmatch(semantic_digest)):
                raise ValueError("invalid hook semantic projection digest")
            duration = row.get("duration_ms")
            if "duration_ms" in row and (row.get("event_type") == "PreToolUse" or type(duration) not in (int, float) or not 0 <= duration < float("inf")):
                raise ValueError("invalid hook duration")
            result = row.get("result_sha256")
            if row.get("event_type") == "PreToolUse":
                if result is not None:
                    raise ValueError("PreToolUse result must be pending")
            elif not isinstance(result, str) or not SHA256_RE.fullmatch(result):
                raise ValueError("invalid hook result digest")
        calls = _validate_hook_ledger(rows, identity)
        if any(row["event_type"] != "PreToolUse" and
               ("command_projection_sha256" in row or "command_projection_schema" in row)
               for row in rows):
            raise ValueError("hook semantic projection binding belongs on PreToolUse")
        if any(not call.get("completed") for call in calls.values()):
            raise ValueError("hook receipt has unfinished tool lifecycles")
    except (ValueError, TypeError) as exc:
        raise FinalizeError(f"invalid hook receipt: {exc}") from exc
    return raw, {
        "schema_version": "session-bench-claude-desktop-hook-validation-v1",
        **identity, "receipt_sha256": _sha256_bytes(raw), "receipt_size_bytes": len(raw),
        "event_count": len(rows), "completed_tool_count": len(calls),
        "timestamp_provenance": "local_hook_receipt_clock",
        "native_source_time_observed": False, "score_eligible": False,
        "claim_limit": "supplemental hook lifecycle receipt only; command digests do not prove command scope or output content",
    }


def _hook_timestamp_observer(
    observer: Mapping[str, Any], raw_hooks: bytes,
    *, gui_event_clock: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind independent hook identities and capture-clock observations.

    No native transcript participates in the join. Exact commands are hashed
    without shell normalization; path joins use only the fixture-relative target.
    This function must receive bytes from _validate_hook_receipt.
    """
    rows = [_hook_json_object(line) for line in raw_hooks.splitlines()]
    if not rows:
        raise FinalizeError("empty hook identity ledger")
    identity = {key: rows[0].get(key) for key in ("run_id", "session_id", "workspace")}
    try:
        calls = _validate_hook_ledger(rows, identity)
    except (TypeError, ValueError) as exc:
        raise FinalizeError(f"invalid hook identity ledger: {exc}") from exc
    if any(not row.get("completed") for row in calls.values()):
        raise FinalizeError("incomplete hook identity ledger")
    if identity["run_id"] != observer.get("run_id"):
        raise FinalizeError("hook observer run mismatch")
    result = copy.deepcopy(observer)
    events = result["events"]
    if any(row.get("session_id") != identity["session_id"] for row in events):
        raise FinalizeError("hook observer session mismatch")
    actions = [row for row in events if row["kind"] == "action"]
    completions = {row["tool_use_id"]: row for row in rows if row["event_type"] != "PreToolUse"}
    bound = set()

    def relative(value):
        if not isinstance(value, str) or not value.startswith("fixture_project/"):
            return None
        path = PurePosixPath(value)
        if str(path) != value or ".." in path.parts:
            return None
        return value[len("fixture_project/"):]

    def matches(action, hook):
        fields = action["fields"]
        if fields.get("name") != hook["tool_name"]:
            return False
        inputs = fields.get("input", {})
        if "command_sha256" in hook:
            command = inputs.get("command")
            return isinstance(command, str) and _sha256_bytes(command.encode()) == hook["command_sha256"]
        target = relative(inputs.get("file_path"))
        return target == hook.get("fixture_relative_target") and relative(fields.get("target")) == target

    def bind(event, call, observed_at):
        fields = event["fields"]
        if any(key in fields for key in ("call_id", "native_action_id", "native_result_id", "native_change_id")):
            raise FinalizeError("observer already carries native identities")
        if gui_event_clock is not None and any(key in fields for key in ("observed_at", "timestamp_provenance")):
            raise FinalizeError("observer already carries capture-clock fields")
        fields["call_id"] = call
        if gui_event_clock is not None:
            fields["observed_at"] = observed_at
            fields["timestamp_provenance"] = "local_hook_receipt_clock"

    gui_by_id: dict[str, Mapping[str, Any]] = {}
    if gui_event_clock is not None:
        for row in gui_event_clock:
            event_id = row.get("event_id")
            if not isinstance(event_id, str) or event_id in gui_by_id:
                raise FinalizeError("GUI event clock IDs are duplicate or malformed")
            gui_by_id[event_id] = row
        expected_gui = {
            row["id"]: row["kind"]
            for row in events
            if row["kind"] in {"user_turn", "assistant_response"}
            and row.get("population_role") == "primary_scored"
        }
        if set(gui_by_id) != set(expected_gui):
            raise FinalizeError("GUI event clock does not cover the exact primary turn/response population")
        for event in events:
            if event["id"] not in expected_gui:
                continue
            receipt = gui_by_id[event["id"]]
            if receipt.get("event_kind") != expected_gui[event["id"]]:
                raise FinalizeError("GUI event clock kind conflicts with the independent observer")
            fields = event["fields"]
            if "observed_at" in fields or "timestamp_provenance" in fields:
                raise FinalizeError("primary observer already carries GUI clock fields")
            fields["observed_at"] = receipt["gui_observed_at"]
            fields["timestamp_provenance"] = receipt["timestamp_provenance"]

    for call, hook in calls.items():
        candidates = [action for action in actions if matches(action, hook)]
        if len(candidates) != 1 or candidates[0]["id"] in bound:
            raise FinalizeError("ambiguous or unmatched hook action")
        action = candidates[0]
        bound.add(action["id"])
        results = [row for row in events if row["kind"] == "result" and row["fields"].get("action_id") == action["id"]]
        if len(results) != 1:
            raise FinalizeError("hook action must have exactly one observer result")
        completion = completions[call]
        result_fields = results[0]["fields"]
        observer_status = result_fields.get("status")
        # PostToolUse records lifecycle completion, not process exit success.
        # An independently observed nonzero Bash exit is a valid completed
        # lifecycle (the benchmark baseline intentionally fails).
        completed_process_failure = (
            completion["event_type"] == "PostToolUse"
            and hook["tool_name"] == "Bash"
            and observer_status == "failure"
            and type(result_fields.get("exit_code")) is int
            and result_fields["exit_code"] != 0
        )
        if observer_status != completion["result_status"] and not completed_process_failure:
            raise FinalizeError("hook result lifecycle conflicts with observer")
        changes = [row for row in events if row["kind"] == "file_change" and row["fields"].get("action_id") == action["id"]]
        if len(changes) > 1 or (changes and (completion["result_status"] != "success" or relative(changes[0]["fields"].get("path")) != hook.get("fixture_relative_target"))):
            raise FinalizeError("hook file-change relationship conflicts with observer")
        pre = next(row for row in rows if row["tool_use_id"] == call and row["event_type"] == "PreToolUse")
        completion = completions[call]
        bind(action, call, pre["hook_observed_at"])
        for row in results:
            bind(row, call, completion["hook_observed_at"])
        for row in changes:
            bind(row, call, completion["hook_observed_at"])
    if len(bound) != len(actions) or any(row["kind"] in {"result", "file_change"} and "call_id" not in row["fields"] for row in events):
        raise FinalizeError("hook identities do not cover the observer population")
    if gui_event_clock is not None:
        required_clocked = [
            event for event in events
            if event["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"}
            and event.get("population_role") == "primary_scored"
        ]
        if any(not event["fields"].get("observed_at") or not event["fields"].get("timestamp_provenance") for event in required_clocked):
            raise FinalizeError("capture clock does not cover every primary scored event")
    return result


def _validate_command_projection_receipt(
    path: Path, *, raw_hooks: bytes, run_id: str, session_id: str,
    workspace: str, run_canary: str,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Join private capture-time semantic projections to exact hook events."""
    source = Path(path).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(source, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise FinalizeError("command projection receipt must be a private ordinary JSONL file")
            raw = stream.read(16_000_001)
    except OSError as exc:
        raise FinalizeError("command projection receipt must be a private ordinary JSONL file") from exc
    if not raw or len(raw) > 16_000_000 or not raw.endswith(b"\n"):
        raise FinalizeError("command projection receipt is empty, too large, or incomplete")
    identity = {"run_id": run_id, "session_id": session_id, "workspace": workspace}
    hook_rows = [_hook_json_object(line) for line in raw_hooks.splitlines()]
    try:
        projection_rows = [_hook_json_object(line) for line in raw.splitlines()]
        if len(projection_rows) != len(hook_rows):
            raise ValueError("projection event count differs from hook ledger")
        starts: dict[str, dict[str, Any]] = {}
        finishes: dict[str, dict[str, Any]] = {}
        supported_fields = {
            "projection_state", "command_sha256", "cwd", "action_kind", "argv",
            "target", "helper_phases", "compound_edit", "compound_edit_target",
        }
        for hook, row in zip(hook_rows, projection_rows, strict=True):
            base_allowed = {
                "schema", *identity, "call_id", "event_type", "tool_name",
                "observed_at", "timestamp_provenance", "projection_state",
            }
            if row.get("event_type") == "PreToolUse":
                allowed = base_allowed | supported_fields | {"unsupported_reason"}
            else:
                allowed = base_allowed | {"result_status", "result_sha256"}
            if set(row) - allowed or row.get("schema") != HOOK_PROJECTION_SCHEMA:
                raise ValueError("unsupported projection fields or schema")
            if any(row.get(key) != value for key, value in identity.items()):
                raise ValueError("projection capture identity mismatch")
            if (row.get("call_id") != hook.get("tool_use_id")
                    or row.get("event_type") != hook.get("event_type")
                    or row.get("tool_name") != hook.get("tool_name")
                    or row.get("observed_at") != hook.get("hook_observed_at")
                    or row.get("timestamp_provenance") != "local_hook_receipt_clock"):
                raise ValueError("projection event does not match its capture-time hook receipt")
            call, kind = row["call_id"], row["event_type"]
            if kind == "PreToolUse":
                if call in starts or row.get("projection_state") != "supported":
                    raise ValueError("tool call has no unique supported semantic projection")
                semantic_keys = supported_fields if row["tool_name"] == "Bash" else supported_fields - {"command_sha256"}
                if set(row) != base_allowed | semantic_keys:
                    raise ValueError("projected tool has missing or unsupported semantic fields")
                semantic = {key: row[key] for key in semantic_keys}
                if (hook.get("command_projection_schema") != HOOK_PROJECTION_SCHEMA
                        or hook.get("command_projection_sha256") != _sha256_bytes(_canonical(semantic))):
                    raise ValueError("projected semantics differ from capture-time hook digest")
                if row["cwd"] != "fixture_project":
                    raise ValueError("projected cwd differs from synthetic fixture")
                tool = row["tool_name"]
                if tool == "Bash":
                    if row.get("command_sha256") != hook.get("command_sha256"):
                        raise ValueError("projected command digest differs from hook")
                    phases = row.get("helper_phases")
                    if not isinstance(phases, list):
                        raise ValueError("projected helper phase list is missing")
                    for phase in phases:
                        if (not isinstance(phase, Mapping)
                                or set(phase) != {"phase", "argv", "run_canary"}
                                or phase.get("phase") not in {"inspect", "baseline", "final"}
                                or phase.get("argv") != ["python3", "bench_check.py", phase.get("phase")]
                                or phase.get("run_canary") != run_canary):
                            raise ValueError("projected helper phase or run canary mismatch")
                    first = phases[0]["phase"] if phases else None
                    action_kind = ("inspect" if first == "inspect" else
                                   "test" if first in {"baseline", "final"} else "shell")
                    expected_argv = ["python3", "bench_check.py", first] if first else None
                    expected_target = "fixture_project/checkout.py" if phases else None
                    compound = row.get("compound_edit")
                    if (type(compound) is not bool
                            or (compound and (not phases or phases[-1]["phase"] != "final"))
                            or row.get("action_kind") != action_kind
                            or row.get("argv") != expected_argv
                            or row.get("target") != expected_target
                            or row.get("compound_edit_target") !=
                            ("fixture_project/checkout.py" if compound else None)):
                        raise ValueError("projected Bash semantics are inconsistent")
                elif tool in {"Edit", "Write"}:
                    if (hook.get("fixture_relative_target") != "checkout.py"
                            or row.get("target") != "fixture_project/checkout.py"
                            or row.get("action_kind") != "edit"
                            or row.get("argv") != ["replace_function", "fixture_project/checkout.py"]
                            or row.get("helper_phases") != []
                            or row.get("compound_edit") is not False
                            or row.get("compound_edit_target") is not None):
                        raise ValueError("projected edit target differs from hook")
                else:
                    raise ValueError("unsupported projected tool name")
                starts[call] = row
            elif kind in {"PostToolUse", "PostToolUseFailure"}:
                if call in finishes or row.get("projection_state") != "supported":
                    raise ValueError("tool completion has no unique supported projection")
                if (row.get("result_status") != hook.get("result_status")
                        or row.get("result_sha256") != hook.get("result_sha256")):
                    raise ValueError("projected result differs from hook receipt")
                finishes[call] = row
            else:
                raise ValueError("unsupported projected hook event")
        if set(starts) != set(finishes):
            raise ValueError("command projection has incomplete lifecycles")
        merged: list[dict[str, Any]] = []
        for hook in (row for row in hook_rows if row["event_type"] == "PreToolUse"):
            call = hook["tool_use_id"]
            start, finish = starts[call], finishes[call]
            projected = {
                key: value for key, value in start.items()
                if key not in {"schema", "workspace", "event_type", "observed_at", "timestamp_provenance", "projection_state", "unsupported_reason"}
            }
            projected.update({
                "schema": "claude-desktop-command-projection-v2",
                "action_observed_at": start["observed_at"],
                "result_status": finish["result_status"],
                "result_sha256": finish["result_sha256"],
                "result_observed_at": finish["observed_at"],
            })
            merged.append(projected)
        return raw, merged
    except (KeyError, TypeError, ValueError) as exc:
        raise FinalizeError(f"invalid command projection receipt: {exc}") from exc


def _composite_timestamp_observer(
    observer: Mapping[str, Any], raw_hooks: bytes,
    gui_event_clock: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a separate clock/identity view for a hook-projected observer."""
    rows = [_hook_json_object(line) for line in raw_hooks.splitlines()]
    starts = {row["tool_use_id"]: row for row in rows if row["event_type"] == "PreToolUse"}
    finishes = {row["tool_use_id"]: row for row in rows if row["event_type"] != "PreToolUse"}
    if not starts or set(starts) != set(finishes):
        raise FinalizeError("composite timestamp observer requires complete hook lifecycles")
    result = copy.deepcopy(observer)
    events = result["events"]
    clocks = {row.get("event_id"): row for row in gui_event_clock}
    expected_gui = {
        event["id"]: event["kind"] for event in events
        if event["kind"] in {"user_turn", "assistant_response"}
        and event.get("population_role") == "primary_scored"
    }
    if set(clocks) != set(expected_gui):
        raise FinalizeError("GUI event clock does not cover the exact turn/response population")
    for event in events:
        fields = event["fields"]
        if event["id"] in expected_gui:
            clock = clocks[event["id"]]
            if clock.get("event_kind") != expected_gui[event["id"]]:
                raise FinalizeError("GUI event clock kind conflicts with observer")
            fields["observed_at"] = clock["gui_observed_at"]
            fields["timestamp_provenance"] = clock["timestamp_provenance"]
            continue
        call_ref = fields.get("hook_call_ref")
        if call_ref is None:
            continue
        compound = fields.get("projected_compound_edit") is True
        call = call_ref
        if call not in starts or call not in finishes:
            raise FinalizeError("observer hook reference has no complete capture-time lifecycle")
        action_stamp = starts[call]["hook_observed_at"]
        result_stamp = finishes[call]["hook_observed_at"]
        event_kind = event["kind"]
        if event_kind == "action":
            fields["call_id"] = f"{call}:compound-edit" if compound else call
            fields["observed_at"] = action_stamp
        elif event_kind in {"result", "file_change"}:
            fields["call_id"] = f"{call}:compound-edit" if compound else call
            fields["observed_at"] = result_stamp
        else:
            raise FinalizeError("unexpected event carries a tool hook reference")
        fields["timestamp_provenance"] = "local_hook_receipt_clock"
    required = [
        event for event in events
        if event.get("population_role") == "primary_scored"
        and event["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"}
    ]
    if any(not event["fields"].get("observed_at")
           or not event["fields"].get("timestamp_provenance") for event in required):
        raise FinalizeError("capture clocks do not cover every primary scored event")
    return result


def _safe_repetition(repetition: int) -> int:
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 1:
        raise FinalizeError("repetition must be a positive integer")
    return repetition


def _copy_file(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FinalizeError(f"refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_decode_manifest(package: Path, session: Path) -> str:
    data = session.read_bytes()
    return _write_json(
        package / "decode.json",
        {
            "format": "claude-code-jsonl-v1",
            "artifacts": [
                {
                    "id": "session",
                    "path": "session.jsonl",
                    "sha256": _sha256_bytes(data),
                    "size_bytes": len(data),
                    "depends_on": [],
                }
            ],
        },
    )


def _remove_unique_assistant_canary(source: Path, destination: Path, canary: str) -> dict[str, Any]:
    """Create a selected-loss copy by removing one assistant record only."""

    if not isinstance(canary, str) or not canary:
        raise FinalizeError("selected response canary must be non-empty")
    source_bytes = source.read_bytes()
    lines = source_bytes.splitlines(keepends=True)
    kept: list[bytes] = []
    removed: list[tuple[int, bytes]] = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinalizeError(f"selected transcript line {number} is invalid JSON") from exc
        message = row.get("message") if isinstance(row, dict) else None
        is_assistant = (
            isinstance(row, dict)
            and row.get("type") == "assistant"
            and isinstance(message, dict)
            and message.get("role") == "assistant"
        )
        if is_assistant and canary in json.dumps(message.get("content"), ensure_ascii=False):
            removed.append((number, line))
        else:
            kept.append(line)
    if len(removed) != 1:
        raise FinalizeError(
            f"expected one assistant record containing selected canary, found {len(removed)}"
        )
    if destination.exists() or destination.is_symlink():
        raise FinalizeError(f"refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"".join(kept))
    line_number, line = removed[0]
    return {
        "removed_line_number": line_number,
        "removed_record_sha256": _sha256_bytes(line),
        "removed_response_canary": canary,
        "remaining_session_sha256": _sha256_bytes(destination.read_bytes()),
    }


def _validate_gui_receipt(receipt: Mapping[str, Any], *, run_id: str, workload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise FinalizeError("GUI receipt must be an object")
    observations = receipt.get("observations")
    if not isinstance(observations, Mapping):
        raise FinalizeError("GUI receipt must contain observations")
    turns = workload.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise FinalizeError("workload must contain exactly two turns")
    for ordinal, turn in enumerate(turns, 1):
        if not isinstance(turn, Mapping):
            raise FinalizeError("workload turn is malformed")
        key = GUI_CANARY_KEYS[ordinal]
        expected = turn.get("response_canary")
        if observations.get(key) != expected:
            raise FinalizeError(f"GUI receipt does not prove {key}")
        if observations.get(f"r{ordinal}_response_boundary_visible") is not True:
            raise FinalizeError(f"GUI receipt does not prove response boundary {ordinal}")
    if observations.get("edit_visible") is not True:
        raise FinalizeError("GUI receipt does not prove the edit")
    if observations.get("final_table_rows") != 3:
        raise FinalizeError("GUI receipt does not prove the three-row final result")
    if receipt.get("run_id") not in (None, run_id):
        raise FinalizeError("GUI receipt run_id does not match selected run")
    return dict(receipt)


def _validate_desktop_pair(family_package: Path, *, decoded_session_id: str) -> dict[str, Any]:
    """Validate only the copied exact family; optional runtime files stay optional."""

    try:
        family = validate_claude_desktop_family(family_package)
    except ValueError as exc:
        raise FinalizeError(f"Desktop family validation failed: {exc}") from exc
    if family.get("cli_session_id") != decoded_session_id:
        raise FinalizeError("Desktop metadata cliSessionId does not match decoder session")
    family["complete_cross_root_family"] = bool(family.get("complete_persistent_family"))
    family["family_scope"] = "the two exact operator-selected persistent Desktop recording artifacts"
    family["metadata_only_discovery"] = True
    family["personal_history_content_scanned"] = False
    family["unrelated_preexisting_sessions_opened"] = False
    return family


def _helper_ledger(project_root: Path, *, run_canary: str) -> tuple[str, dict[str, Mapping[str, Any]]]:
    path = project_root / ".survival-observer.jsonl"
    if path.is_symlink() or not path.is_file():
        raise FinalizeError("preserved synthetic helper ledger is missing")
    raw = path.read_text(encoding="utf-8")
    rows: dict[str, Mapping[str, Any]] = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise FinalizeError(f"helper ledger line {number} is blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FinalizeError(f"helper ledger line {number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise FinalizeError(f"helper ledger line {number} is not an object")
        phase = value.get("phase")
        if phase in rows or phase not in {"inspect", "baseline", "final"}:
            raise FinalizeError("helper ledger phases are duplicated or unsupported")
        if value.get("run_canary") != run_canary:
            raise FinalizeError("helper ledger run canary does not match selected run")
        if value.get("argv") != ["python3", "bench_check.py", phase]:
            raise FinalizeError(f"helper ledger {phase} argv is not the frozen helper invocation")
        if value.get("cwd") != "fixture_project":
            raise FinalizeError(f"helper ledger {phase} cwd is not fixture_project")
        if not isinstance(value.get("output"), str) or not value["output"].startswith(f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_"):
            raise FinalizeError(f"helper ledger {phase} output is malformed")
        rows[phase] = value
    if set(rows) != {"inspect", "baseline", "final"}:
        raise FinalizeError("helper ledger must contain inspect, baseline, and final exactly once")
    return raw, rows


def _build_observer(
    *,
    workload: Mapping[str, Any],
    session_id: str,
    model: str,
    helper_raw: str,
    helper: Mapping[str, Mapping[str, Any]],
    gui: Mapping[str, Any],
    before_sha: str,
    after_sha: str,
    run_id: str,
    repetition: int,
) -> dict[str, Any]:
    """Build expected events from independent workload, helper, GUI, and FS evidence."""

    observations = gui["observations"]

    def tool(tool: str, call_id: str, input_value: Any, output: str, exit_code: int) -> dict[str, Any]:
        return {
            "type": "tool_use",
            "tool": tool,
            "input": input_value,
            "callID": call_id,
            "id": call_id,
            "sessionID": session_id,
            "state": {"status": "completed", "output": output, "metadata": {"exit_code": exit_code}},
        }

    def text_row(value: str) -> dict[str, Any]:
        return {"type": "text", "text": value, "sessionID": session_id}

    run_canary = str(workload["run_canary"])
    streams = {
        1: "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                tool("Bash", "gui-inspect", {"command": f"python3 bench_check.py inspect --run-canary {run_canary}"}, str(helper["inspect"]["output"]), 0),
                tool("Bash", "gui-baseline", {"command": f"python3 bench_check.py baseline --run-canary {run_canary}"}, str(helper["baseline"]["output"]), 1),
                text_row(str(observations["r1_canary_visible"])),
            )
        )
        + "\n",
        2: "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                tool("Edit", "gui-edit", {"file_path": "fixture_project/checkout.py"}, "compound edit completed", 0),
                tool("Bash", "gui-final", {"command": f"python3 bench_check.py final --run-canary {run_canary}"}, str(helper["final"]["output"]), 0),
                text_row(str(observations["r2_canary_visible"])),
            )
        )
        + "\n",
    }
    observer = build_opencode_live_observer(
        workload=workload,
        controller_state={
            "turns": {"1": {"session_id": session_id}, "2": {"session_id": session_id}},
            "model": model,
            "configuration": "claude-desktop",
            "workspace": "fixture_project",
        },
        stdout_by_turn=streams,
        helper_ledger_jsonl=helper_raw,
        before_checkout_sha256=before_sha,
        after_checkout_sha256=after_sha,
    )
    # Tool IDs are native-looking fields in the synthetic stream projection;
    # they were not observed by the GUI receipt.  Keep semantic command/target
    # fields and make the comparator bind on those fields only.
    for event in observer["events"]:
        if event.get("kind") in {"action", "result"} and isinstance(event.get("fields"), dict):
            for key in ("call_id", "native_action_id", "native_result_id"):
                event["fields"].pop(key, None)
    observer.update(
        {
            "configuration_id": "claude-desktop",
            "repetition": repetition,
            "method": "independent Desktop accessibility receipt, submitted workload, preserved helper ledger, and filesystem hashes; native transcript was not used to author expected events",
            "observer_receipt": "gui-receipt.json",
            "run_id": run_id,
        }
    )
    return observer


def _metric_evidence(metric_ids: tuple[str, ...], observer_id: str, native_id: str, native_sha: str) -> list[dict[str, Any]]:
    return [
        {
            "metric_id": metric_id,
            "observer_ids": [observer_id],
            "native_locators": [
                {
                    "artifact_id": native_id,
                    "artifact_sha256": native_sha,
                    "record_location": "transcript/session.jsonl",
                }
            ],
        }
        for metric_id in metric_ids
    ]


def _artifact_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FinalizeError(f"finalized evidence contains a symlink: {path}")
        if path.is_file() and path.name != "artifact-hashes.json":
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return rows


def finalize(
    *,
    run_id: str,
    repetition: int,
    transcript: Path,
    desktop_metadata: Path,
    gui_receipt: Path,
    hook_receipt: Path | None = None,
    command_projection_receipt: Path | None = None,
    source_discovery_receipt: Path | None = None,
    gui_event_clock_receipt: Path | None = None,
    otel_usage_receipt: Path | None = None,
    gui_screenshot: Path | None = None,
    project_root: Path | None = None,
    build: str | None = None,
    collected_on: str | None = None,
) -> dict[str, Any]:
    run_id = _safe_run_id(run_id)
    repetition = _safe_repetition(repetition)
    transcript = _ordinary_source(transcript, "transcript")
    desktop_metadata = _ordinary_source(desktop_metadata, "Desktop metadata")
    gui_receipt = _ordinary_source(gui_receipt, "GUI receipt")
    source_location = _source_location_receipt(transcript, desktop_metadata)
    if command_projection_receipt is not None and (
        hook_receipt is None or gui_event_clock_receipt is None
        or source_discovery_receipt is None
    ):
        raise FinalizeError(
            "scored capture-time projection requires hook, GUI clock, and complete source discovery receipts"
        )
    gui_clock_raw = None
    gui_clock_rows = None
    if gui_event_clock_receipt is not None:
        from session_bench.claude_desktop_gui_event_clock import (
            EVENT_PROVENANCE,
            load_gui_event_clock,
        )

        gui_event_clock_receipt = _ordinary_source(gui_event_clock_receipt, "GUI event clock receipt")
        gui_clock_raw, gui_clock_rows = load_gui_event_clock(gui_event_clock_receipt, run_id=run_id)
        if hook_receipt is None:
            raise FinalizeError("GUI event clock requires the complete hook lifecycle receipt")
    if gui_screenshot is not None:
        gui_screenshot = _ordinary_source(gui_screenshot, "GUI screenshot")
    if collected_on is None:
        collected_on = date.today().isoformat()
    try:
        date.fromisoformat(collected_on)
    except (TypeError, ValueError) as exc:
        raise FinalizeError("collected_on must be YYYY-MM-DD") from exc

    run_root = (REPO / "artifacts" / "survival-v1-runs" / run_id).resolve()
    if not run_root.is_dir():
        raise FinalizeError(f"run root is missing: {run_root}")
    artifact_base = (REPO / "artifacts" / "survival-v1-runs").resolve()
    if artifact_base not in run_root.parents:
        raise FinalizeError("run root escaped the repository survival run directory")
    output = run_root / "capture" / "finalized-private-v1"
    if output.exists() or output.is_symlink():
        raise FinalizeError(f"refusing to overwrite existing finalizer output: {output}")

    attempt_path = run_root / "attempt.json"
    if attempt_path.is_file() and not attempt_path.is_symlink():
        attempt = _load_json(attempt_path)
        if isinstance(attempt.get("run_canary"), str) and attempt["run_canary"] != f"SB_SURVIVAL_V1_RUN_{run_id}":
            raise FinalizeError("existing attempt is bound to a different run canary")

    workload_template = _load_json(REPO / "fixtures/scenarios/survival-v1/workload/workload.json")
    workload, _ = instantiate_workload(workload_template, run_id)
    run_canary = workload["run_canary"]

    selected_project = (project_root or (run_root / "final-project-private")).expanduser()
    if selected_project.is_symlink() or not selected_project.is_dir():
        raise FinalizeError(f"preserved synthetic project root is missing: {selected_project}")
    selected_project = selected_project.resolve()
    before_path = selected_project / "snapshots" / "checkout.before.py"
    checkout_path = selected_project / "checkout.py"
    before_sha = _sha256(before_path)
    after_sha = _sha256(checkout_path)
    if before_sha == after_sha:
        raise FinalizeError("preserved synthetic project did not change checkout.py")

    gui = _validate_gui_receipt(_load_json(gui_receipt), run_id=run_id, workload=workload)
    screenshot_sha = _sha256(gui_screenshot) if gui_screenshot is not None else None
    gui_declared = gui.get("screenshot")
    if screenshot_sha is not None and isinstance(gui_declared, Mapping):
        declared_sha = gui_declared.get("sha256")
        if isinstance(declared_sha, str) and declared_sha != screenshot_sha:
            raise FinalizeError("GUI receipt screenshot digest does not match supplied screenshot")
    helper_raw, helper = _helper_ledger(selected_project, run_canary=run_canary)

    # Package the exact transcript and metadata pair.  The family package is
    # private; the decoder package below contains only the declared transcript.
    family_package = output / "native-family-private"
    _copy_file(transcript, family_package / "transcript" / "session.jsonl")
    _copy_file(desktop_metadata, family_package / "desktop" / "session.json")
    decoder_package = output / "native-package"
    _copy_file(transcript, decoder_package / "session.jsonl")
    decode_manifest_sha = _write_decode_manifest(decoder_package, decoder_package / "session.jsonl")
    intact = decode_claude_code_bundle(decoder_package)
    for source, copied in zip(source_location["sources"], (
        family_package / "transcript/session.jsonl", family_package / "desktop/session.json",
    )):
        if source["sha256"] != _sha256(copied):
            raise FinalizeError("caller-selected source changed while packaging")
    session_id = intact["session_id"]
    family = _validate_desktop_pair(family_package, decoded_session_id=session_id)
    otel_usage_join = None
    otel_usage_binding = None
    if otel_usage_receipt is not None:
        otel_usage_raw, otel_usage_join = _validate_otel_usage_receipt(
            otel_usage_receipt, run_id=run_id, session_id=session_id,
            workload=workload, gui_receipt=gui,
        )
        otel_usage_output = output / "otel-usage-receipt-private.json"
        with otel_usage_output.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(otel_usage_raw)
        otel_usage_binding = {
            "path": otel_usage_output.name,
            "sha256": _sha256_bytes(otel_usage_raw),
            "event_count": len(_strict_otel_json(otel_usage_raw)["events"]),
            "usage_join_count": len(otel_usage_join),
            "raw_response_text_persisted": False,
            "observer_truth_source": "capture_time_loopback_otel_export",
        }
    discovery_binding = None
    root_repetitions = None
    if source_discovery_receipt is not None:
        discovery_raw = _validate_source_discovery_receipt(
            source_discovery_receipt, run_id=run_id, repetition=repetition,
            cli_session_id=session_id, desktop_session_id=family["desktop_session_id"],
            source_location=source_location, family_package=family_package,
        )
        discovery_output = output / "source-discovery-private.json"
        with discovery_output.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(discovery_raw)
        discovery_binding = {"path": discovery_output.name, "sha256": _sha256_bytes(discovery_raw)}
        source_location["source_discovery_receipt"] = discovery_binding
        source_location.update({
            "provenance": "complete metadata-safe inventory of both normal Claude Desktop session roots",
            "source_root_scanned": True,
            "root_completeness_proven": True,
            "location_metric_resolved": True,
        })
        root_repetitions = [{
            "repetition": repetition,
            "root_locator": "Claude Code normal session roots: projects/<project-key>/<session-id>.jsonl + Desktop session metadata/<session-group>/<local-session-id>.json",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }]
    source_location_sha = _write_json(output / "source-location-private.json", source_location)
    os.chmod(output / "source-location-private.json", 0o600)
    source_location_binding = {"path": "source-location-private.json", "sha256": source_location_sha}
    hook_binding: dict[str, Any] = {
        "supplied": False, "state": "missing_unscored",
        "timestamp_provenance": "local_hook_receipt_clock",
        "native_source_time_observed": False, "score_eligible": False,
    }
    command_projections = None
    command_projection_raw = None
    if hook_receipt is not None:
        desktop = _load_json(family_package / "desktop/session.json")
        raw_hooks, hook_validation = _validate_hook_receipt(
            hook_receipt, run_id=run_id, session_id=session_id,
            workspace=desktop["cwd"], fixture_root=selected_project,
        )
        hook_output = output / "hook-receipt-private.jsonl"
        # Preserve the exact validated bytes, not a second read of the input.
        with hook_output.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw_hooks)
        validation_sha = _write_json(output / "hook-validation-private.json", hook_validation)
        os.chmod(output / "hook-validation-private.json", 0o600)
        hook_binding.update({
            "supplied": True, "state": "validated_supplemental_unscored",
            "path": "hook-receipt-private.jsonl", "sha256": _sha256_bytes(raw_hooks),
            "validation": {"path": "hook-validation-private.json", "sha256": validation_sha},
            "event_count": hook_validation["event_count"],
            "completed_tool_count": hook_validation["completed_tool_count"],
        })
        if command_projection_receipt is not None:
            command_projection_raw, command_projections = _validate_command_projection_receipt(
                command_projection_receipt, raw_hooks=raw_hooks,
                run_id=run_id, session_id=session_id, workspace=desktop["cwd"],
                run_canary=run_canary,
            )
            projection_output = output / "command-projection-private.jsonl"
            with projection_output.open("xb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(command_projection_raw)
            hook_binding["command_projection"] = {
                "path": projection_output.name,
                "sha256": _sha256_bytes(command_projection_raw),
                "event_count": len(command_projections) * 2,
                "capture_time_only": True,
                "raw_tool_input_retained": False,
            }
            hook_binding["state"] = "validated_capture_time_projection"
    gui_clock_binding = None
    if gui_clock_raw is not None:
        gui_clock_output = output / "gui-event-clock-private.jsonl"
        with gui_clock_output.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(gui_clock_raw)
        gui_clock_binding = {
            "path": gui_clock_output.name,
            "sha256": _sha256_bytes(gui_clock_raw),
            "event_count": len(gui_clock_rows or []),
            "timestamp_provenance_by_event": {
                "user_turn": EVENT_PROVENANCE["turn-r1"],
                "assistant_response": EVENT_PROVENANCE["response-r1"],
            },
        }
    family["run_id"] = run_id
    family["repetition"] = repetition
    family_sha = _write_json(output / "family-validation.json", family)

    offline_package = output / "offline-package"
    _copy_file(decoder_package / "session.jsonl", offline_package / "session.jsonl")
    offline_decode_manifest_sha = _write_decode_manifest(offline_package, offline_package / "session.jsonl")
    offline = decode_claude_code_bundle(offline_package)
    if intact != offline:
        raise FinalizeError("native and copied offline Claude decodes differ")
    if intact["counts"]["submitted_turns"] != 2 or intact["counts"]["responses"] != 2:
        raise FinalizeError(f"selected transcript does not contain two turns and two responses: {intact['counts']}")

    damage_package = output / "damage-r2-package"
    selection = _remove_unique_assistant_canary(
        offline_package / "session.jsonl",
        damage_package / "session.jsonl",
        workload["turns"][1]["response_canary"],
    )
    damage_manifest_sha = _write_decode_manifest(damage_package, damage_package / "session.jsonl")
    damaged = decode_claude_code_bundle(damage_package)
    if damaged["counts"]["responses"] != intact["counts"]["responses"] - 1:
        raise FinalizeError("selected-loss control did not remove exactly one response")
    if damaged["counts"]["actions"] != intact["counts"]["actions"] or damaged["counts"]["results"] != intact["counts"]["results"]:
        raise FinalizeError("selected response loss changed the action or result population")

    intact_decode_sha = _write_json(output / "decoded-native.json", intact)
    offline_decode_sha = _write_json(output / "decoded-offline.json", offline)
    damage_decode_sha = _write_json(output / "decoded-damage-r2.json", damaged)
    _write_json(output / "loss-control-selection.json", selection)

    gui_output = dict(gui)
    gui_output["bound_screenshot"] = {
        "supplied": screenshot_sha is not None,
        "sha256": screenshot_sha,
    }
    gui_output_sha = _write_json(output / "gui-receipt.json", gui_output)
    if command_projections is None:
        observer = _build_observer(
            workload=workload,
            session_id=session_id,
            model=str(family["model"]),
            helper_raw=helper_raw,
            helper=helper,
            gui=gui,
            before_sha=before_sha,
            after_sha=after_sha,
            run_id=run_id,
            repetition=repetition,
        )
    else:
        from session_bench.claude_desktop_composite_observer import build_scored_composite_observer

        if gui_clock_rows is None:
            raise FinalizeError("capture-time score projection requires GUI event clocks")
        observer = build_scored_composite_observer(
            workload=workload, hook_jsonl=raw_hooks,
            gui_clock_rows=gui_clock_rows, helper_rows=helper,
            command_projections=command_projections, gui_receipt=gui,
            session_id=session_id, workspace=desktop["cwd"],
            before_checkout_sha256=before_sha,
            after_checkout_sha256=after_sha,
        )
    if otel_usage_join is not None and otel_usage_binding is not None:
        observer = _attach_otel_usage(
            observer, otel_usage_join, receipt_binding=otel_usage_binding,
        )
    observer["hook_receipt"] = hook_binding
    observer_sha = _write_json(output / "observer.json", observer)
    timestamp_observer = None
    if command_projections is not None:
        timestamp_observer = _composite_timestamp_observer(
            observer, raw_hooks, gui_event_clock=gui_clock_rows or [],
        )
        timestamp_sha = _write_json(output / "timestamp-observer.json", timestamp_observer)
        timestamp_binding = {"id": "timestamp-observer.json", "sha256": timestamp_sha}
    elif hook_receipt is not None:
        timestamp_observer = _hook_timestamp_observer(
            observer, raw_hooks, gui_event_clock=gui_clock_rows,
        )
        timestamp_sha = _write_json(output / "timestamp-observer.json", timestamp_observer)
        timestamp_binding = {"id": "timestamp-observer.json", "sha256": timestamp_sha}

    native_id = f"claude-desktop-transcript-{session_id}"
    native_sha = _sha256(decoder_package / "session.jsonl")
    native_manifest = {
        "schema_version": "session-bench-claude-desktop-native-manifest-v2",
        "id": f"{run_id}-native-manifest-v1",
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "desktop_session_id": family["desktop_session_id"],
        "cli_session_id": family["cli_session_id"],
        "bridge_session_ids": family["bridge_session_ids"],
        "family_validation": {"id": "family-validation.json", "sha256": family_sha},
        "source_location_receipt": source_location_binding,
        "selected_artifact": {"id": native_id, "path": "native-package/session.jsonl", "sha256": native_sha, "size_bytes": (decoder_package / "session.jsonl").stat().st_size},
        "packages": {
            "native": {"path": "native-package", "decode_manifest_sha256": decode_manifest_sha},
            "offline": {"path": "offline-package", "decode_manifest_sha256": offline_decode_manifest_sha},
            "damage_r2": {"path": "damage-r2-package", "decode_manifest_sha256": damage_manifest_sha},
        },
        "complete_cross_root_family": family["complete_cross_root_family"],
        "run_root_discovery_qualified": discovery_binding is not None,
        "cross_run_root_repeatability_qualified": False,
        "unrelated_preexisting_sessions_opened": False,
        "privacy": "private native family; no public derivative is created by this command",
    }
    if discovery_binding is not None:
        native_manifest["source_discovery_receipt"] = discovery_binding
    if gui_clock_binding is not None:
        native_manifest["gui_event_clock_receipt"] = gui_clock_binding
    if otel_usage_binding is not None:
        native_manifest["otel_usage_receipt"] = otel_usage_binding
    native_manifest_sha = _write_json(output / "native-manifest.json", native_manifest)

    native_facts = native_facts_from_claude_session(
        (decoder_package / "session.jsonl").read_bytes(),
        run_canary=run_canary,
        workspace=selected_project,
        before_sha256=before_sha,
        after_sha256=after_sha,
    )
    native_facts_sha = _write_json(output / "native-facts.json", native_facts)
    replay_receipt = {
        "schema_version": "session-bench-claude-desktop-replay-receipt-v2",
        "run_id": run_id,
        "repetition": repetition,
        "decoder": {
            "path": "session_bench/adapters/claude_code_decoder.py",
            "sha256": _sha256(REPO / "session_bench/adapters/claude_code_decoder.py"),
        },
        "packages": {"native": "native-package", "offline": "offline-package", "damage_r2": "damage-r2-package"},
        "decoded_counts": {"native": intact["counts"], "offline": offline["counts"], "damage_r2": damaged["counts"]},
        "canonical_equality": {"native_vs_offline": intact == offline, "native_decode_sha256": intact_decode_sha, "offline_decode_sha256": offline_decode_sha},
        "selected_loss": {
            "intact_responses": intact["counts"]["responses"],
            "damaged_responses": damaged["counts"]["responses"],
            "response_loss_detected": damaged["counts"]["responses"] < intact["counts"]["responses"],
            "actions_preserved": damaged["counts"]["actions"] == intact["counts"]["actions"],
            "results_preserved": damaged["counts"]["results"] == intact["counts"]["results"],
            "selection": "loss-control-selection.json",
        },
        "family_validation_sha256": family_sha,
        "gui_receipt_sha256": gui_output_sha,
        "hook_receipt": hook_binding,
        "source_location_receipt": source_location_binding,
        "gui_event_clock_receipt": gui_clock_binding,
        "otel_usage_receipt": otel_usage_binding,
        "independent_reproduction": False,
        "run_root_discovery_qualified": discovery_binding is not None,
        "cross_run_root_repeatability_qualified": False,
    }
    replay_sha = _write_json(output / "replay-receipt.json", replay_receipt)

    measurement = compare_survival_run(
        observer,
        native_facts,
        {
            "complete_root": True if family["complete_cross_root_family"] else None,
            "companions_present": True if family["complete_cross_root_family"] else None,
            "isolated_decode": True,
            "canonical_equality": intact == offline,
        },
        configuration_id="claude-desktop",
        repetition=repetition,
    )
    measurement_sha = _write_json(output / "measurement.json", measurement)

    format_evidence = build_claude_format_evidence(
        offline,
        observer={"id": "observer.json", "sha256": observer_sha},
        native_manifest={"id": "native-manifest.json", "sha256": native_manifest_sha},
        run_id=run_id,
        configuration_id="claude-desktop",
        repetition=repetition,
        build=build or str(family["cli_version"]),
        collected_on=collected_on,
        result_id=f"{run_id}-desktop-format-v1",
        complete_record_family=bool(family["complete_cross_root_family"]),
        root_repetitions=root_repetitions,
    )
    if timestamp_observer is not None:
        # Only timestamp evidence consumes the independently bound IDs. Other
        # broad facts and the survival observer/measurement stay unchanged.
        format_evidence["observer"] = timestamp_binding
        for metric in format_evidence["metric_evidence"]:
            metric["observer_ids"] = [timestamp_binding["id"]]
        format_evidence["profile"]["broad_evidence"]["broad.event_timestamps"] = build_observer_timestamp_evidence(
            offline, family="claude", observer=timestamp_binding, run_id=run_id,
            observer_document=(output / "timestamp-observer.json").read_bytes(),
            complete_record_family=bool(family["complete_cross_root_family"]),
        )
    validate_format_evidence(format_evidence)
    format_sha = _write_json(output / "format-evidence.json", format_evidence)

    survival_evidence = {
        "schema_version": PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": run_id,
        "capture_id": f"{run_id}-desktop-capture-v1",
        "evaluation_id": f"{run_id}-desktop-evaluation-v1",
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "measurement": measurement,
        "observer": {"id": "observer.json", "sha256": observer_sha},
        "native_manifest": {"id": "native-manifest.json", "sha256": native_manifest_sha},
        "decoder": {"id": "claude-code-decoder.py", "sha256": _sha256(REPO / "session_bench/adapters/claude_code_decoder.py")},
        "identity": {
            "provider": "Anthropic",
            "harness": "Claude Code",
            "surface": "Desktop Code (Local)",
            "execution_mode": "Claude Desktop local task",
            "os": "macOS",
            "build": build or str(family["cli_version"]),
            "model": str(family["model"]),
            "configuration": "claude-desktop",
            "protocol_version": "1.0-survival",
            "workload_version": "1.0-survival-workload",
            "observer_schema_version": "1.0-survival-observer",
            "rubric_version": "1.0-survival-rubric",
        },
        "metric_evidence": _metric_evidence(SURVIVAL_METRICS, "observer.json", native_id, native_sha),
    }
    validate_prospective_evidence_input(survival_evidence)
    survival_sha = _write_json(output / "survival-evidence.json", survival_evidence)
    evidence_31 = _build_31_evidence(
        measurement,
        format_evidence,
        survival_evidence,
        observer_id="observer.json",
        native_artifact_id=native_id,
        native_artifact_sha256=native_sha,
    )
    evidence_31["claim_limit"] = "one Claude Desktop Code (Local) private evidence run; no public score, rank, badge, recommendation, or vendor claim"
    evidence_31_sha = _write_json(output / "evidence-31.json", evidence_31)
    if len(evidence_31.get("metrics", [])) != 31 or len(evidence_31.get("metric_evidence", [])) != 31:
        raise FinalizeError("31-metric wrapper is incomplete")

    states = {row["id"]: row["state"] for row in measurement["metrics"]}
    broad_states = {row["id"]: row["state"] for row in validate_format_evidence(format_evidence)["profile"]["metrics"]}
    attempt_output = {
        "schema_version": "session-bench-claude-desktop-finalized-attempt-v1",
        "attempt_id": run_id,
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "state": "captured_unscored",
        "score_eligible": False,
        "public_score_eligible": False,
        "source_attempt": "../attempt.json",
        "session_id": session_id,
        "desktop_session_id": family["desktop_session_id"],
        "bridge_session_ids": family["bridge_session_ids"],
        "counts": intact["counts"],
        "selected_loss": replay_receipt["selected_loss"],
        "canonical_copy_proven": True,
        "complete_cross_root_family": family["complete_cross_root_family"],
        "independent_gui_observer": True,
        "source_location_receipt": source_location_binding,
        "hook_receipt": hook_binding,
        "gui_event_clock_receipt": gui_clock_binding,
        "otel_usage_receipt": otel_usage_binding,
        "decoder": {"format": intact["format"], "canonical_equal": True, "source_sha256": replay_receipt["decoder"]["sha256"]},
        "measurement": {"path": "measurement.json", "sha256": measurement_sha, "metric_count": len(measurement["metrics"]), "states": states},
        "format_evidence": {"path": "format-evidence.json", "sha256": format_sha, "metric_count": len(FORMAT_METRICS), "states": broad_states},
        "survival_evidence": {"path": "survival-evidence.json", "sha256": survival_sha},
        "evidence_31": {"path": "evidence-31.json", "sha256": evidence_31_sha, "metric_count": len(evidence_31["metrics"])},
        "replay_receipt": {"path": "replay-receipt.json", "sha256": replay_sha},
        "run_root_discovery": "qualified for this run" if discovery_binding is not None else "unresolved: no complete metadata-safe discovery receipt",
        "cross_run_root_repeatability": "not assessed; a second run is optional for the per-run metric",
        "claim_limit": "private evidence only; no public score, rank, badge, recommendation, or vendor claim",
    }
    attempt_sha = _write_json(output / "attempt-finalized.json", attempt_output)
    hashes = {
        "schema_version": "session-bench-claude-desktop-finalized-artifact-hashes-v1",
        "run_id": run_id,
        "repetition": repetition,
        "artifacts": _artifact_rows(output),
    }
    hashes_sha = _write_json(output / "artifact-hashes.json", hashes)

    result = {
        "status": "complete_private_unscored",
        "run_id": run_id,
        "repetition": repetition,
        "output": "capture/finalized-private-v1",
        "session_id": session_id,
        "desktop_session_id": family["desktop_session_id"],
        "decoded_counts": {"native": intact["counts"], "offline": offline["counts"], "damage_r2": damaged["counts"]},
        "canonical_equal": True,
        "selected_loss": replay_receipt["selected_loss"],
        "metric_count": 31,
        "measurement_states": states,
        "format_states": broad_states,
        "score_eligible": False,
        "attempt_sha256": attempt_sha,
        "hook_receipt": hook_binding,
        "artifact_hashes_sha256": hashes_sha,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repetition", required=True, type=int)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--desktop-metadata", required=True, type=Path)
    parser.add_argument("--gui-receipt", required=True, type=Path)
    parser.add_argument("--hook-receipt", type=Path, help="one explicitly selected private capture-time hook JSONL receipt")
    parser.add_argument("--command-projection-receipt", type=Path, help="private capture-time semantic projection JSONL; raw commands are not retained")
    parser.add_argument("--source-discovery-receipt", type=Path, help="one exact-session metadata-only source discovery receipt")
    parser.add_argument("--gui-event-clock-receipt", type=Path, help="private local accessibility timestamps for all four turn/response boundaries")
    parser.add_argument("--otel-usage-receipt", type=Path, help="private loopback OTel receipt with request-scoped usage linked by synthetic canaries")
    parser.add_argument("--gui-screenshot", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--build")
    parser.add_argument("--collected-on")
    args = parser.parse_args()
    try:
        finalize(
            run_id=args.run_id,
            repetition=args.repetition,
            transcript=args.transcript,
            desktop_metadata=args.desktop_metadata,
            gui_receipt=args.gui_receipt,
            hook_receipt=args.hook_receipt,
            command_projection_receipt=args.command_projection_receipt,
            source_discovery_receipt=args.source_discovery_receipt,
            gui_event_clock_receipt=args.gui_event_clock_receipt,
            otel_usage_receipt=args.otel_usage_receipt,
            gui_screenshot=args.gui_screenshot,
            project_root=args.project_root,
            build=args.build,
            collected_on=args.collected_on,
        )
    except Exception as exc:
        print(f"finalize invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
