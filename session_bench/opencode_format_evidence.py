"""Build locator-bound broad-format evidence from one corrected OpenCode package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def build_opencode_format_evidence(
    package_dir: Path,
    *,
    collected_on: str,
    complete_record_family: bool = False,
    root_repetitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return actual broad evidence; unresolved facts remain unresolved."""
    package = Path(package_dir).resolve(strict=True)
    summary, evidence = _read(package / "summary.json"), _read(package / "evidence.json")
    decoded, observer = _read(package / "decoded.json"), _read(package / "observer.json")
    manifest = _read(package / "native-manifest.json")
    run_id, configuration_id, repetition = evidence["run_id"], evidence["configuration_id"], evidence["repetition"]
    build = evidence["identity"]["build"]
    if not isinstance(complete_record_family, bool):
        raise ValueError("complete_record_family must be boolean")
    if root_repetitions is not None and not isinstance(root_repetitions, list):
        raise ValueError("root_repetitions must be a list")
    responses = decoded.get("responses", [])
    events = decoded.get("events", [])
    turns = decoded.get("turns", [])
    db_sha = next(item["sha256"] for item in manifest["files"] if item["name"] == "opencode.db")
    native = {"id": "native:opencode.db", "sha256": db_sha}
    documentation = {"id": "documentation:opencode-cli", "sha256": _sha(Path(__file__).resolve().parents[1] / "docs/survival-v1/adapters/opencode-cli.md")}
    runtime = {"id": "runtime:manifest", "sha256": _sha(package / "replay-runtime/manifest.json")}
    response_ids = [item["id"] for item in responses if isinstance(item, dict) and isinstance(item.get("id"), str)]
    time_records = [
        {"id": item["id"], "timestamp": item["time_created"], "unit": "unix_ms", "time_zone": "UTC"}
        for item in events if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("time_created"), int)
    ]
    density_records = [
        {"record_id": str(index) + ":" + item["id"], "record_kind": item["kind"] if item.get("kind") in {"user_message", "correction", "assistant_message", "tool_call", "tool_result", "failure", "file_change", "plan", "explanation", "metadata", "index", "snapshot", "session", "system", "unknown"} else "unknown", "logical_bytes": len(str(item.get("text", "")).encode("utf-8")), "classification": "useful" if item.get("kind") in {"user_message", "correction", "assistant_message", "tool_call", "tool_result", "failure", "file_change", "plan", "explanation"} else "unknown"}
        for index, item in enumerate(events) if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    declared_version = {"evidence_complete": False, "format_version": "", "machine_readable": False, "bundle_binding": ""}
    honest_version = {"evidence_complete": False, "declared_version": "", "decoder_contract_version": "", "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
    schema_stability = {"evidence_complete": False, "advertised_contract": "", "observations": [], "exceptions": []}
    duplicate_safety = {"evidence_complete": False, "event_ids": response_ids[:1], "forward_records": [], "deduplication": {"documented": False, "rule": "unqualified"}}
    if complete_record_family:
        # The observed SQLite schema has no native format-version field.  A
        # complete family resolves that as a scored absence rather than a gap.
        declared_version = {"evidence_complete": True, "format_version": "", "machine_readable": False, "bundle_binding": runtime["id"]}
        honest_version = {"evidence_complete": True, "declared_version": build, "decoder_contract_version": "opencode-sqlite-session-v1", "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
        schema_stability = {"evidence_complete": True, "advertised_contract": "opencode-sqlite-session-v1", "observations": [{"build": build, "observed_on": collected_on, "decoder_contract": "opencode-sqlite-session-v1", "decoded": True}], "exceptions": []}
        forward_records = []
        semantic_ids = []
        for ordinal, item in enumerate(events):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("kind"), str):
                continue
            semantic_id = f"{item['kind']}:{item['id']}"
            semantic_ids.append(semantic_id)
            forward_records.append({"event_id": semantic_id, "occurrence_id": f"row-{ordinal}", "state": "active"})
        duplicate_safety = {"evidence_complete": bool(semantic_ids), "event_ids": semantic_ids, "forward_records": forward_records, "deduplication": {"documented": True, "rule": "semantic identity is native record kind plus native row ID"}}

    broad = {
        "broad.readable_rationale": {"evidence_complete": True, "response_ids": response_ids, "records": [{"id": item["id"], "ordered_text": item.get("text", "")} for item in responses]},
        "broad.thread_structure": {"evidence_complete": True, "session_id": summary["session_id"], "turns": [{"id": item["id"], "role": item["role"], "ordinal": item["sequence"], "parent_id": None} for item in turns], "explicit_parentage": False},
        "broad.standard_tools_readable": {"evidence_complete": True, "container": "sqlite", "parser": "Python sqlite3 plus Session-Bench decoder", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
        "broad.documented_format": {"evidence_complete": True, "document_id": "docs/survival-v1/adapters/opencode-cli.md", "mapping": {"containers": "Quiescent SQLite/WAL/SHM capture", "record_types": "session, message, part", "identities": "session ID", "joins": "message and part session/message IDs", "version_semantics": "decoder contract is versioned"}},
        "broad.self_contained_identity": {"evidence_complete": True, "session_id": summary["session_id"], "harness": evidence["identity"]["harness"], "surface": "cli", "record_family": decoded["format"], "external_lookup_required": False, "absolute_path_required": False},
        "broad.declared_format_version": declared_version,
        "broad.event_timestamps": {"evidence_complete": bool(time_records), "event_ids": [item["id"] for item in time_records], "records": time_records},
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": {"evidence_complete": bool(density_records), "classification_rule": "logical-record-role-v1", "records": density_records},
    }
    document = {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "build": build, "collected_on": collected_on, "result_id": summary["result_id"], "observer": {"id": "observer:" + summary["attempt_id"], "sha256": _sha(package / "observer.json")}, "native_manifest": {"id": "manifest:" + summary["attempt_id"], "sha256": _sha(package / "native-manifest.json")}, "profile": {"schema_version": "session-bench-format-profile-v1", "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "broad_evidence": broad}, "metric_evidence": [{"metric_id": metric, "observer_ids": ["observer:" + summary["attempt_id"]], "native_locators": [documentation if metric == "broad.documented_format" else runtime if metric in {"broad.declared_format_version", "broad.honest_version_signal"} else native]} for metric in FORMAT_METRICS]}
    # Validate the source-shaped document, then retain that exact source shape
    # for later independent scoring.  The validator's normalized return value
    # intentionally contains derived metrics rather than broad_evidence.
    validate_format_evidence(document)
    return document
