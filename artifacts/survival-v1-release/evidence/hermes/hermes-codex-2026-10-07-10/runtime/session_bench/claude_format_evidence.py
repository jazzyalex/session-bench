"""Fail-closed 12-metric format evidence for one copied Claude JSONL bundle.

Density from filtered decoded text is unresolved. An explicitly bound CLI
native package supports a complete canonical logical record inventory instead;
Desktop density requires an explicit bound transcript/metadata persistent pair.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .v1_public_score import (
    CLASSIFIED_CONTENT_DENSITY_RULE,
    FORMAT_EVIDENCE_SCHEMA_VERSION,
    FORMAT_METRICS,
    validate_format_evidence,
)
from .native_density import inventory_native_jsonl_density, native_forward_occurrences
from .format_response_population import build_observer_rationale_evidence
from .format_timestamp_population import build_observer_timestamp_evidence
from .claude_desktop_gui_event_clock import (
    RESPONSE_PROVENANCE,
    TURN_PROVENANCE,
)


class ClaudeFormatEvidenceError(ValueError):
    """The copied Claude decode or caller identities are insufficient."""


_HEX = set("0123456789abcdef")
_TIMESTAMP_IDENTITY_KINDS = {"action", "result", "file_change"}
_TIMESTAMP_POPULATION_KINDS = {"user_turn", "assistant_response", "action", "result", "file_change"}
_HOOK_CLOCK_PROVENANCE = "local_hook_receipt_clock"


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


def validate_claude_timestamp_observer(
    primary: Mapping[str, Any], timestamp: Mapping[str, Any], *, run_id: str,
) -> None:
    """Require an exact observer copy with hook identities and optional capture clocks."""

    from .live_metric_comparator import _validate_observer

    primary_run, primary_events, _ = _validate_observer(primary)
    timestamp_run, timestamp_events, _ = _validate_observer(timestamp)
    if primary_run != run_id or timestamp_run != run_id:
        raise ClaudeFormatEvidenceError("timestamp observer run differs from the primary observer")
    primary_top = {key: value for key, value in primary.items() if key != "events"}
    timestamp_top = {key: value for key, value in timestamp.items() if key != "events"}
    if primary_top != timestamp_top or len(primary_events) != len(timestamp_events):
        raise ClaudeFormatEvidenceError("timestamp observer population differs from the primary observer")
    augmented = 0
    primary_clocked = 0
    primary_clock_expected = sum(
        row["kind"] in _TIMESTAMP_POPULATION_KINDS
        and row.get("population_role") == "primary_scored"
        for row in primary_events
    )
    for primary_event, timestamp_event in zip(primary_events, timestamp_events):
        primary_outer = {key: value for key, value in primary_event.items() if key != "fields"}
        timestamp_outer = {key: value for key, value in timestamp_event.items() if key != "fields"}
        if primary_outer != timestamp_outer:
            raise ClaudeFormatEvidenceError("timestamp observer event identity or population differs")
        primary_fields = dict(primary_event["fields"])
        timestamp_fields = dict(timestamp_event["fields"])
        if primary_event["kind"] in _TIMESTAMP_IDENTITY_KINDS:
            call_id = timestamp_fields.pop("call_id", None)
            if "call_id" in primary_fields or not isinstance(call_id, str) or not call_id.strip():
                raise ClaudeFormatEvidenceError("timestamp observer requires one added non-empty call_id per tool event")
            augmented += 1
        has_observed_at = "observed_at" in timestamp_fields
        has_provenance = "timestamp_provenance" in timestamp_fields
        if has_observed_at or has_provenance:
            observed_at = timestamp_fields.pop("observed_at", None)
            provenance = timestamp_fields.pop("timestamp_provenance", None)
            if not has_observed_at or not has_provenance or not _rfc3339(observed_at):
                raise ClaudeFormatEvidenceError("capture-clock fields require a timezone-aware observed_at and provenance")
            expected_provenance = {
                "user_turn": TURN_PROVENANCE,
                "assistant_response": RESPONSE_PROVENANCE,
            }.get(primary_event["kind"], _HOOK_CLOCK_PROVENANCE)
            if provenance != expected_provenance:
                raise ClaudeFormatEvidenceError("capture-clock provenance does not match the observed event boundary")
            if (timestamp_event["kind"] in _TIMESTAMP_POPULATION_KINDS
                    and timestamp_event.get("population_role") == "primary_scored"):
                primary_clocked += 1
        if timestamp_fields != primary_fields:
            raise ClaudeFormatEvidenceError("timestamp observer changes a non-identity field")
    if not augmented:
        raise ClaudeFormatEvidenceError("timestamp observer has no tool-event identity additions")
    if primary_clocked not in {0, primary_clock_expected}:
        raise ClaudeFormatEvidenceError("capture clock must cover the complete primary timestamp population")


def _observer_json(document: bytes, label: str) -> Mapping[str, Any]:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ClaudeFormatEvidenceError(f"{label} contains a duplicate JSON key")
            value[key] = item
        return value

    try:
        value = json.loads(document, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeFormatEvidenceError(f"{label} must be valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ClaudeFormatEvidenceError(f"{label} must be an object")
    return value


def build_claude_format_evidence(
    decoded: Mapping[str, Any], *, observer: Mapping[str, Any], native_manifest: Mapping[str, Any],
    run_id: str, configuration_id: str, repetition: int, build: str, collected_on: str, result_id: str,
    complete_record_family: bool = False,
    root_repetitions: list[dict[str, Any]] | None = None,
    native_package: str | Path | None = None,
    desktop_family: str | Path | None = None,
    observer_document: bytes | None = None,
    timestamp_observer: Mapping[str, Any] | None = None,
    timestamp_observer_document: bytes | None = None,
) -> dict[str, Any]:
    """Build evidence using only a decoded declared package and immutable IDs.

    A transcript-only call remains fail-closed.  A caller may set
    ``complete_record_family`` only after validating and binding the full copied
    Desktop/CLI family in ``native_manifest``.  That turns observable absences
    (for example, no declared schema version) into scored zeroes instead of
    unresolved evidence. Stable-root evidence requires a bound discovery receipt
    for this run; additional runs test repeatability but are optional. CLI density
    also requires an explicit ``native_package``
    whose artifact hashes match the decode; all native records are inventoried
    under canonical UTF-8 JSON byte accounting rather than selected event text.
    Desktop density requires an explicit ``desktop_family`` containing the full
    persistent transcript/session metadata pair bound to the decoded transcript.
    Rationale coverage additionally requires serialized ``observer_document``
    bytes matching the supplied observer digest, including observed response text.
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
    timestamp_observer_id = None
    if timestamp_observer is not None or timestamp_observer_document is not None:
        if configuration_id != "claude-desktop":
            raise ClaudeFormatEvidenceError("timestamp observer is allowed only for claude-desktop")
        if timestamp_observer is None or timestamp_observer_document is None or observer_document is None:
            raise ClaudeFormatEvidenceError("timestamp observer requires both exact observer documents")
        timestamp_observer_id = _digest(timestamp_observer, "timestamp_observer")
        if hashlib.sha256(timestamp_observer_document).hexdigest() != timestamp_observer_id["sha256"]:
            raise ClaudeFormatEvidenceError("timestamp observer digest differs from exact document bytes")
        validate_claude_timestamp_observer(
            _observer_json(observer_document, "primary observer document"),
            _observer_json(timestamp_observer_document, "timestamp observer document"),
            run_id=run_id,
        )
    rows = [row for row in events if isinstance(row, Mapping) and isinstance(row.get("id"), str)]
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
    # Selected decoded text cannot prove complete native logical-byte density.
    if not isinstance(complete_record_family, bool):
        raise ClaudeFormatEvidenceError("complete_record_family must be boolean")
    if root_repetitions is not None and not isinstance(root_repetitions, list):
        raise ClaudeFormatEvidenceError("root_repetitions must be a list")
    density_evidence = {"evidence_complete": False, "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": []}
    density_locators: tuple[dict[str, str], ...] = ()
    if configuration_id == "claude-cli" and complete_record_family and native_package is not None:
        package_metadata = decoded.get("package")
        expected_artifacts = package_metadata.get("artifacts") if isinstance(package_metadata, Mapping) else None
        inventory = inventory_native_jsonl_density(native_package, family="claude", session_id=session_id, expected_artifacts=expected_artifacts)
        density_evidence, density_locators = inventory.evidence, inventory.native_locators
    elif configuration_id == "claude-desktop" and complete_record_family and desktop_family is not None:
        from .desktop_density import inventory_claude_desktop_density
        package_metadata = decoded.get("package")
        expected_artifacts = package_metadata.get("artifacts") if isinstance(package_metadata, Mapping) else None
        inventory = inventory_claude_desktop_density(desktop_family, session_id=session_id, expected_transcript_artifacts=expected_artifacts)
        density_evidence, density_locators = inventory.evidence, inventory.native_locators
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
        # The read is the transcript JSONL. Occurrences are counted on its raw
        # records, not on decoded events; without the package the row stays unresolved.
        package_metadata = decoded.get("package")
        occurrences = native_forward_occurrences(native_package, family="claude", expected_artifacts=package_metadata.get("artifacts") if isinstance(package_metadata, Mapping) else None) if native_package is not None else None
        if occurrences:
            duplicate_safety = {"evidence_complete": True, "event_ids": list(dict.fromkeys(event for event, _ in occurrences)), "forward_records": [{"event_id": event, "occurrence_id": occurrence, "state": "active"} for event, occurrence in occurrences], "deduplication": {"documented": True, "rule": "the read is the transcript JSONL; a prompt is one event, and its queue-operation enqueue record and its user record with the same text are both retained occurrences; an assistant text block is the record UUID, a tool call is the tool_use id and a result is its tool_use_id"}}
    broad = {
        "broad.readable_rationale": build_observer_rationale_evidence(
            decoded, observer=observer_id, run_id=run_id, observer_document=observer_document,
            complete_record_family=complete_record_family,
        ),
        "broad.thread_structure": {"evidence_complete": bool(turns) and turns[0]["role"] == "user", "session_id": session_id, "turns": turns, "explicit_parentage": False},
        "broad.standard_tools_readable": {"evidence_complete": True, "container": "jsonl", "parser": "python-stdlib-json", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
        "broad.documented_format": {"evidence_complete": True, "document_id": "docs/survival-v1/adapters/claude-code.md", "mapping": {"containers": "one declared JSONL session", "record_types": "user and assistant messages with tool blocks", "identities": "sessionId and UUID", "joins": "tool_use id to tool_result id", "version_semantics": "decoder contract is versioned"}},
        "broad.self_contained_identity": identity,
        "broad.declared_format_version": declared_version,
        "broad.event_timestamps": build_observer_timestamp_evidence(
            decoded, family="claude", observer=timestamp_observer_id or observer_id, run_id=run_id,
            observer_document=timestamp_observer_document or observer_document, complete_record_family=complete_record_family,
            unresolved_diagnostics={"evidence_complete": False, "event_ids": [row["id"] for row in timestamp_records], "records": timestamp_records},
        ),
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": density_evidence,
    }
    document = {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "build": build, "collected_on": collected_on, "result_id": result_id, "observer": observer_id, "native_manifest": manifest_id, "profile": {"schema_version": "session-bench-format-profile-v1", "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "broad_evidence": broad}, "metric_evidence": [{"metric_id": metric, "observer_ids": [(timestamp_observer_id or observer_id)["id"] if metric == "broad.event_timestamps" else observer_id["id"]], "native_locators": list(density_locators) if density_locators and metric == "broad.classified_content_density" else [manifest_id]} for metric in FORMAT_METRICS]}
    validate_format_evidence(document)
    return document


__all__ = ["ClaudeFormatEvidenceError", "build_claude_format_evidence", "validate_claude_timestamp_observer"]
