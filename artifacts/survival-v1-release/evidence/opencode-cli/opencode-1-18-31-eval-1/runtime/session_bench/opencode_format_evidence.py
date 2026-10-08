"""Build locator-bound broad-format evidence from one corrected OpenCode package.

Density can be explicitly backed by the complete copied SQLite family;
selected decoded event text alone never establishes logical byte accounting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .v1_public_score import FORMAT_EVIDENCE_SCHEMA_VERSION, FORMAT_METRICS, validate_format_evidence
from .format_response_population import build_observer_rationale_evidence
from .format_timestamp_population import build_observer_timestamp_evidence
from .adapters.opencode_decoder import read_opencode_schema_ledger

DECODER_CONTRACT = "opencode-sqlite-session-v1"


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
    observer_document: bytes | None = None,
    include_native_density: bool = False,
) -> dict[str, Any]:
    """Return broad evidence; rationale and timestamps need hash-bound observer bytes."""
    package = Path(package_dir).resolve(strict=True)
    summary, evidence = _read(package / "summary.json"), _read(package / "evidence.json")
    decoded, observer = _read(package / "decoded.json"), _read(package / "observer.json")
    manifest = _read(package / "native-manifest.json")
    run_id, configuration_id, repetition = evidence["run_id"], evidence["configuration_id"], evidence["repetition"]
    build = evidence["identity"]["build"]
    observer_id = {"id": "observer:" + summary["attempt_id"], "sha256": _sha(package / "observer.json")}
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
    # Selected decoded text cannot prove complete native logical-byte density.
    declared_version = {"evidence_complete": False, "format_version": "", "machine_readable": False, "bundle_binding": ""}
    honest_version = {"evidence_complete": False, "declared_version": "", "decoder_contract_version": "", "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
    schema_stability = {"evidence_complete": False, "advertised_contract": "", "observations": [], "exceptions": []}
    duplicate_safety = {"evidence_complete": False, "event_ids": response_ids[:1], "forward_records": [], "deduplication": {"documented": False, "rule": "unqualified"}}
    version_scanned = False
    if complete_record_family:
        # Native version scan. OpenCode declares its SQLite schema through an
        # ordered ledger of applied migrations (PRAGMA user_version stays 0).
        # The ledger is read from the hash-bound copied database only.
        declared_version = {"evidence_complete": False, "format_version": "", "machine_readable": False, "bundle_binding": native["id"]}
        honest_version = {"evidence_complete": False, "declared_version": "", "decoder_contract_version": DECODER_CONTRACT, "incompatible_schema_distinguished": False, "matches_decoder_contract": False}
        bundle = package / "native-bundle"
        if bundle.is_dir():
            for item in manifest["files"]:
                if not (bundle / item["name"]).is_file() or _sha(bundle / item["name"]) != item["sha256"]:
                    raise ValueError("OpenCode native bundle differs from its manifest")
            ledger = read_opencode_schema_ledger(bundle)
            version_scanned = True
            if ledger["state"] == "present" and ledger["ids_sha256"] is not None:
                format_version = f"migration-ledger:last={ledger['last_id']};count={ledger['count']};ids_sha256={ledger['ids_sha256']}"
                declared_version.update(evidence_complete=True, format_version=format_version, machine_readable=True)
                # Honest only when the decoder contract names this exact ledger
                # and decoded under it. An unknown ledger is refused by the
                # decoder, so it stays unresolved here instead of scoring.
                bound = ledger["contract_supported"] is True and decoded.get("supported") is True
                honest_version.update(evidence_complete=bound, declared_version=format_version,
                    decoder_contract_version=f"{DECODER_CONTRACT};migration-ledger-ids-sha256={ledger['ids_sha256'] if bound else 'unknown'}",
                    incompatible_schema_distinguished=bound, matches_decoder_contract=bound)
            elif ledger["state"] == "absent":
                # The complete scanned database declares no ledger: a scored absence.
                declared_version["evidence_complete"] = True
                honest_version["evidence_complete"] = True
        schema_stability = {"evidence_complete": True, "advertised_contract": "opencode-sqlite-session-v1", "observations": [{"build": build, "observed_on": collected_on, "decoder_contract": "opencode-sqlite-session-v1", "decoded": True}], "exceptions": []}
        # The read is the session, message, part and event tables. Occurrences
        # are counted on their raw rows, not on decoded facts. The event table
        # repeats every part, so its rows are occurrences too.
        occurrences = []
        if (package / "native-bundle").is_dir():
            from .opencode_density import opencode_forward_occurrences
            occurrences = opencode_forward_occurrences(package / "native-bundle", session_id=summary["session_id"])
        if occurrences:
            duplicate_safety = {"evidence_complete": True, "event_ids": list(dict.fromkeys(event for event, _ in occurrences)),
                                "forward_records": [{"event_id": event, "occurrence_id": occurrence, "state": "active"} for event, occurrence in occurrences],
                                "deduplication": {"documented": False, "rule": "none: the read is the session, message, part and event tables; a text part is one message, a tool part is one call and one result (callID); a part event holds a whole part and states it again, and no supersession or tombstone field exists"}}

    broad = {
        "broad.readable_rationale": build_observer_rationale_evidence(
            decoded, observer=observer_id, run_id=run_id, observer_document=observer_document,
            complete_record_family=complete_record_family,
        ),
        "broad.thread_structure": {"evidence_complete": True, "session_id": summary["session_id"], "turns": [{"id": item["id"], "role": item["role"], "ordinal": item["sequence"], "parent_id": None} for item in turns], "explicit_parentage": False},
        "broad.standard_tools_readable": {"evidence_complete": True, "container": "sqlite", "parser": "Python sqlite3 plus Session-Bench decoder", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
        "broad.documented_format": {"evidence_complete": True, "document_id": "docs/survival-v1/adapters/opencode-cli.md", "mapping": {"containers": "Quiescent SQLite/WAL/SHM capture", "record_types": "session, message, part", "identities": "session ID", "joins": "message and part session/message IDs", "version_semantics": "migration ledger table declares the schema; the decoder contract is bound to the ordered ledger and refuses an unknown one"}},
        "broad.self_contained_identity": {"evidence_complete": True, "session_id": summary["session_id"], "harness": evidence["identity"]["harness"], "surface": "cli", "record_family": decoded["format"], "external_lookup_required": False, "absolute_path_required": False},
        "broad.declared_format_version": declared_version,
        "broad.event_timestamps": build_observer_timestamp_evidence(
            decoded, family="opencode", observer=observer_id, run_id=run_id,
            observer_document=observer_document, complete_record_family=complete_record_family,
            unresolved_diagnostics={"evidence_complete": False, "event_ids": [item["id"] for item in time_records], "records": time_records},
        ),
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": {"evidence_complete": False, "classification_rule": "logical-record-role-v1", "records": []},
    }
    document = {"schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION, "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "build": build, "collected_on": collected_on, "result_id": summary["result_id"], "observer": {"id": "observer:" + summary["attempt_id"], "sha256": _sha(package / "observer.json")}, "native_manifest": {"id": "manifest:" + summary["attempt_id"], "sha256": _sha(package / "native-manifest.json")}, "profile": {"schema_version": "session-bench-format-profile-v1", "run_id": run_id, "configuration_id": configuration_id, "repetition": repetition, "broad_evidence": broad}, "metric_evidence": [{"metric_id": metric, "observer_ids": ["observer:" + summary["attempt_id"]], "native_locators": [documentation if metric == "broad.documented_format" else runtime if metric in {"broad.declared_format_version", "broad.honest_version_signal"} and not version_scanned else native]} for metric in FORMAT_METRICS]}
    # Validate the source-shaped document, then retain that exact source shape
    # for later independent scoring.  The validator's normalized return value
    # intentionally contains derived metrics rather than broad_evidence.
    validate_format_evidence(document)
    if include_native_density:
        from .opencode_density import build_opencode_density
        density = build_opencode_density(package / "native-bundle", manifest=manifest,
            session_id=summary["session_id"], complete_record_family=complete_record_family)
        document["profile"]["broad_evidence"]["broad.classified_content_density"] = density.evidence
        for metric in document["metric_evidence"]:
            if metric["metric_id"] == "broad.classified_content_density":
                metric["native_locators"] = [{"id": "native:" + row["name"], "sha256": row["sha256"]} for row in manifest["files"]]
        validate_format_evidence(document)
    return document
