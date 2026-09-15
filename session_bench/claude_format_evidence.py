"""Fail-closed 12-metric format evidence for one copied Claude JSONL bundle."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from .v1_public_score import (
    CLASSIFIED_CONTENT_DENSITY_RULE,
    FORMAT_EVIDENCE_SCHEMA_VERSION,
    FORMAT_METRICS,
    validate_format_evidence,
)


class ClaudeFormatEvidenceError(ValueError):
    """The copied Claude decode or caller identities are insufficient."""


_HEX = set("0123456789abcdef")


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ClaudeFormatEvidenceError(f"{label} must be a non-empty trimmed string")
    return value


def _digest(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"id", "sha256"}:
        raise ClaudeFormatEvidenceError(f"{label} must contain id and sha256")
    identifier = _identity(value["id"], f"{label}.id")
    digest = value["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in _HEX for char in digest):
        raise ClaudeFormatEvidenceError(f"{label}.sha256 must be a lowercase SHA-256")
    return {"id": identifier, "sha256": digest}


def _rfc3339(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def build_claude_format_evidence(
    decoded: Mapping[str, Any], *, observer: Mapping[str, Any], native_manifest: Mapping[str, Any],
    run_id: str, configuration_id: str, repetition: int, build: str, collected_on: str, result_id: str,
    complete_record_family: bool = False,
    root_repetitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build evidence using only a decoded declared package and immutable IDs.

    A transcript-only call remains fail-closed.  A caller may set
    ``complete_record_family`` only after validating and binding the full copied
    Desktop/CLI family in ``native_manifest``.  That turns observable absences
    (for example, no declared schema version) into scored zeroes instead of
    unresolved evidence.  Stable-root evidence still requires all three
    repetitions explicitly.
    """

    if not isinstance(decoded, Mapping) or decoded.get("format") != "claude-code-jsonl-v1":
        raise ClaudeFormatEvidenceError("decoded bundle must be Claude Code JSONL")
    observer_id, manifest_id = _digest(observer, "observer"), _digest(native_manifest, "native_manifest")
    run_id, configuration_id, build, result_id = (_identity(run_id, "run_id"), _identity(configuration_id, "configuration_id"), _identity(build, "build"), _identity(result_id, "result_id"))
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise ClaudeFormatEvidenceError("repetition must be a positive integer")
    try:
        date.fromisoformat(collected_on)
    except (TypeError, ValueError) as exc:
        raise ClaudeFormatEvidenceError("collected_on must be YYYY-MM-DD") from exc
    session_id = decoded.get("session_id")
    events = decoded.get("events")
    if not isinstance(session_id, str) or not session_id or not isinstance(events, list):
        raise ClaudeFormatEvidenceError("decoded bundle lacks session events")
    rows = [row for row in events if isinstance(row, Mapping) and isinstance(row.get("id"), str)]
    responses = [row for row in rows if row.get("kind") == "response" and isinstance(row.get("text"), str) and row["text"].strip()]
    ordered = sorted(rows, key=lambda row: row.get("locator", {}).get("line", 0) if isinstance(row.get("locator"), Mapping) else 0)
    turns: list[dict[str, Any]] = []
    last_user: str | None = None
    for ordinal, row in enumerate((item for item in ordered if item.get("kind") in {"submitted_turn", "response"}), 1):
        role = "user" if row["kind"] == "submitted_turn" else "assistant"
        turns.append({"id": row["id"], "role": role, "ordinal": ordinal, "parent_id": None if role == "user" else last_user})
        if role == "user":
            last_user = row["id"]
    timestamp_records = [
        {"id": row["id"], "timestamp": row["timestamp"], "unit": "rfc3339", "time_zone": "UTC"}
        for row in rows if _rfc3339(row.get("timestamp"))
    ]
    density = [
        {"record_id": row["id"], "record_kind": {"submitted_turn": "user_message", "response": "assistant_message", "action": "tool_call", "result": "tool_result"}.get(row.get("kind"), "unknown"), "logical_bytes": len(str(row.get("text", "")).encode("utf-8")), "classification": "unknown"}
        for row in rows
    ]
    for row in density:
        row["classification"] = "useful" if row["record_kind"] in {"user_message", "assistant_message", "tool_call", "tool_result"} else "unknown"
    if not isinstance(complete_record_family, bool):
        raise ClaudeFormatEvidenceError("complete_record_family must be boolean")
    if root_repetitions is not None and not isinstance(root_repetitions, list):
        raise ClaudeFormatEvidenceError("root_repetitions must be a list")
    unresolved_identity = {"evidence_complete": False, "session_id": session_id, "harness": "unresolved", "surface": configuration_id, "record_family": "claude-code-jsonl-v1", "external_lookup_required": True, "absolute_path_required": True}
    identity = unresolved_identity
    declared_version = {"evidence_complete": False, "format_version": "", "machine_readable": False, "bundle_binding": ""}
    honest_version = {"evidence_complete": False, "declared_version": "", "decoder_contract_version": "claude-code-jsonl-v1", "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
    schema_stability = {"evidence_complete": False, "advertised_contract": "unresolved", "observations": [], "exceptions": []}
    duplicate_safety = {"evidence_complete": False, "event_ids": [row["id"] for row in rows], "forward_records": [], "deduplication": {"documented": False, "rule": "unqualified"}}
    if complete_record_family:
        identity = {"evidence_complete": True, "session_id": session_id, "harness": "claude", "surface": configuration_id, "record_family": "claude-code-jsonl-v1", "external_lookup_required": False, "absolute_path_required": False}
        # The observed writer/build version identifies the executable, not the
        # native schema.  A complete family therefore proves schema-version
        # absence and scores it as zero rather than leaving the row unresolved.
        declared_version = {"evidence_complete": True, "format_version": "", "machine_readable": False, "bundle_binding": native_manifest["id"]}
        honest_version = {"evidence_complete": True, "declared_version": build, "decoder_contract_version": "claude-code-jsonl-v1", "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
        schema_stability = {"evidence_complete": True, "advertised_contract": "claude-code-jsonl-v1", "observations": [{"build": build, "observed_on": collected_on, "decoder_contract": "claude-code-jsonl-v1", "decoded": True}], "exceptions": []}
        duplicate_safety = {"evidence_complete": True, "event_ids": [row["id"] for row in rows], "forward_records": [{"event_id": row["id"], "occurrence_id": f"{row['id']}@{row.get('locator', {}).get('line', 0)}", "state": "active"} for row in rows], "deduplication": {"documented": True, "rule": "one decoded semantic event per native UUID and content-block index"}}
    broad = {
        "broad.readable_rationale": {"evidence_complete": bool(responses), "response_ids": [row["id"] for row in responses], "records": [{"id": row["id"], "ordered_text": row["text"]} for row in responses]},
        "broad.thread_structure": {"evidence_complete": bool(turns) and turns[0]["role"] == "user", "session_id": session_id, "turns": turns, "explicit_parentage": False},
        "broad.standard_tools_readable": {"evidence_complete": True, "container": "jsonl", "parser": "python-stdlib-json", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
        "broad.documented_format": {"evidence_complete": True, "document_id": "docs/survival-v1/adapters/claude-code.md", "mapping": {"containers": "one declared JSONL session", "record_types": "user and assistant messages with tool blocks", "identities": "sessionId and UUID", "joins": "tool_use id to tool_result id", "version_semantics": "decoder contract is versioned"}},
        "broad.self_contained_identity": identity,
        "broad.declared_format_version": declared_version,
        "broad.event_timestamps": {"evidence_complete": bool(timestamp_records) and len(timestamp_records) == len(rows), "event_ids": [row["id"] for row in timestamp_records], "records": timestamp_records},
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": {"evidence_complete": bool(density), "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": density},
    }
    document = {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "build": build, "collected_on": collected_on, "result_id": result_id, "observer": observer_id, "native_manifest": manifest_id, "profile": {"schema_version": "session-bench-format-profile-v1", "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "broad_evidence": broad}, "metric_evidence": [{"metric_id": metric, "observer_ids": [observer_id["id"]], "native_locators": [manifest_id]} for metric in FORMAT_METRICS]}
    validate_format_evidence(document)
    return document


__all__ = ["ClaudeFormatEvidenceError", "build_claude_format_evidence"]
