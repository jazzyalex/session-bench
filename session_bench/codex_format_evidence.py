"""Fail-closed Codex format-evidence builder for the 12 broad metrics.

Turns an already-decoded copied Codex JSONL bundle plus explicit immutable
observer/native metadata into the existing v1_public_score 12-broad-metric
format-evidence document.  Uses only bundle-local declared artifacts and
decoded facts; never reads a home directory, neighboring root, credential,
or network.  Unresolved evidence stays unresolved.  Self-contained identity,
stable root, honest version signal, and observed schema stability are never
claimed: this bundle alone cannot prove them.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from .v1_public_score import (
    CLASSIFIED_CONTENT_DENSITY_RULE,
    FORMAT_EVIDENCE_SCHEMA_VERSION,
    FORMAT_METRICS,
    validate_format_evidence,
)


class CodexFormatEvidenceError(ValueError):
    """Caller metadata or decoded bundle cannot yield reportable evidence."""


_HEX = set("0123456789abcdef")


def _trimmed(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CodexFormatEvidenceError(f"{label} must be a non-empty trimmed string")
    return value


def _digest(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"id", "sha256"}:
        raise CodexFormatEvidenceError(f"{label} must have id and sha256")
    identifier = _trimmed(value["id"], f"{label}.id")
    digest = value["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in _HEX for c in digest):
        raise CodexFormatEvidenceError(f"{label}.sha256 must be a lowercase SHA-256 digest")
    return {"id": identifier, "sha256": digest}


def _date(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 10:
        raise CodexFormatEvidenceError(f"{label} must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise CodexFormatEvidenceError(f"{label} must be YYYY-MM-DD") from exc
    return value


def _artifacts(decoded: Mapping[str, Any]) -> list[dict[str, str]]:
    try:
        package = decoded["package"]
        raw = package["artifacts"]
    except (KeyError, TypeError) as exc:
        raise CodexFormatEvidenceError("decoded bundle has no package.artifacts") from exc
    if not isinstance(raw, list) or not raw:
        raise CodexFormatEvidenceError("decoded bundle has no declared artifacts")
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise CodexFormatEvidenceError("decoded artifact must be an object")
        artifact_id = item.get("id")
        digest = item.get("sha256")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise CodexFormatEvidenceError("decoded artifact id missing")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in _HEX for c in digest):
            raise CodexFormatEvidenceError("decoded artifact sha256 missing")
        result.append({"id": artifact_id, "sha256": digest})
    return result


def _measurement_identity(decoded: Mapping[str, Any]) -> tuple[str, str, int]:
    try:
        measurement = decoded["measurement"]
    except (KeyError, TypeError) as exc:
        raise CodexFormatEvidenceError("decoded bundle has no measurement") from exc
    if not isinstance(measurement, Mapping):
        raise CodexFormatEvidenceError("decoded measurement must be an object")
    run_id = _trimmed(measurement.get("run_id"), "decoded run_id")
    configuration_id = _trimmed(measurement.get("configuration_id"), "decoded configuration_id")
    repetition = measurement.get("repetition")
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise CodexFormatEvidenceError("decoded repetition must be a positive integer")
    return run_id, configuration_id, repetition


def _facts(decoded: Mapping[str, Any]) -> Mapping[str, Any]:
    facts = decoded.get("facts")
    if not isinstance(facts, Mapping):
        raise CodexFormatEvidenceError("decoded bundle has no facts")
    return facts


def _rfc3339(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _fact_id(row: Mapping[str, Any], prefix: str) -> str | None:
    """Use a native message ID when present, otherwise its immutable locator."""

    value = row.get("id")
    if isinstance(value, str) and value.strip():
        return value
    locator = row.get("locator")
    if isinstance(locator, Mapping):
        digest = locator.get("record_sha256")
        if isinstance(digest, str) and len(digest) == 64 and all(char in _HEX for char in digest):
            return f"{prefix}:{digest}"
    return None


def _readable_rationale(facts: Mapping[str, Any]) -> dict[str, Any]:
    rows = facts.get("visible_responses")
    present = []
    if isinstance(rows, list):
        for row in rows:
            if (
                isinstance(row, Mapping)
                and row.get("state") == "present"
                and _fact_id(row, "response") is not None
                and isinstance(row.get("text"), str)
                and row["text"].strip()
            ):
                present.append(row)
    if not present:
        return {"evidence_complete": False, "response_ids": ["unresolved-response"], "records": []}
    return {
        "evidence_complete": True,
        "response_ids": [_fact_id(row, "response") for row in present],
        "records": [{"id": _fact_id(row, "response"), "ordered_text": row["text"]} for row in present],
    }


def _thread_structure(decoded: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    session_id = decoded.get("session_id")
    turns: list[dict[str, Any]] = []
    if isinstance(session_id, str) and session_id.strip():
        candidates: list[tuple[int, str, str, str]] = []
        for key, role in (("submitted_turns", "user"), ("visible_responses", "assistant")):
            rows = facts.get(key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping) or row.get("state") != "present":
                    continue
                locator = row.get("locator")
                ordinal = locator.get("ordinal") if isinstance(locator, Mapping) else None
                rid = _fact_id(row, role)
                if rid is None:
                    continue
                if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
                    continue
                candidates.append((ordinal, rid, role, row.get("turn_id") if isinstance(row.get("turn_id"), str) else ""))
        candidates.sort(key=lambda item: item[0])
        last_user: str | None = None
        for ordinal, rid, role, _logical in candidates:
            if role == "user":
                turns.append({"id": rid, "role": "user", "ordinal": ordinal, "parent_id": None})
                last_user = rid
            elif last_user is not None:
                turns.append({"id": rid, "role": "assistant", "ordinal": ordinal, "parent_id": last_user})
    if (
        isinstance(session_id, str)
        and session_id.strip()
        and turns
        and turns[0]["role"] == "user"
        and all(turns[i]["ordinal"] < turns[i + 1]["ordinal"] for i in range(len(turns) - 1))
        and len({turn["id"] for turn in turns}) == len(turns)
    ):
        return {
            "evidence_complete": True,
            "session_id": session_id,
            "turns": turns,
            "explicit_parentage": False,
        }
    return {
        "evidence_complete": False,
        "session_id": session_id if isinstance(session_id, str) and session_id.strip() else "unresolved-session",
        "turns": turns,
        "explicit_parentage": False,
    }


def _standard_tools(decoded: Mapping[str, Any]) -> dict[str, Any]:
    try:
        package = decoded["package"]
        raw = package["artifacts"]
    except (KeyError, TypeError):
        raw = []
    has_jsonl = any(
        isinstance(item, Mapping)
        and isinstance(item.get("path"), str)
        and item["path"].lower().endswith(".jsonl")
        for item in raw
    ) if isinstance(raw, list) else False
    if has_jsonl:
        return {
            "evidence_complete": True,
            "container": "jsonl",
            "parser": "python-stdlib-json",
            "vendor_binary_required": False,
            "account_required": False,
            "backend_required": False,
            "network_required": False,
        }
    return {
        "evidence_complete": False,
        "container": "jsonl",
        "parser": "unresolved-parser",
        "vendor_binary_required": False,
        "account_required": False,
        "backend_required": False,
        "network_required": False,
    }


def _documented_format(decoded: Mapping[str, Any]) -> dict[str, Any]:
    package_format = None
    try:
        package_format = decoded["package"]["format"]
    except (KeyError, TypeError):
        package_format = None
    if isinstance(package_format, str) and package_format.strip():
        return {
            "evidence_complete": True,
            "document_id": "docs/survival-v1/adapters/codex-cli.md+docs/survival-v1/adapters/codex-desktop.md",
            "mapping": {
                "containers": "decode.json artifacts plus JSONL rollout",
                "record_types": "response_item, event_msg, turn_context, token_usage_record",
                "identities": "session_id, turn_id, call_id, ordinal locators",
                "joins": "ordinal-ordered call_id and task-window joins",
                "version_semantics": "decode.json format field codex-rollout-v1",
            },
        }
    return {
        "evidence_complete": False,
        "document_id": "unresolved-document",
        "mapping": {
            "containers": "unresolved",
            "record_types": "unresolved",
            "identities": "unresolved",
            "joins": "unresolved",
            "version_semantics": "unresolved",
        },
    }


def _declared_version(decoded: Mapping[str, Any]) -> dict[str, Any]:
    package_format = None
    try:
        package_format = decoded["package"]["format"]
    except (KeyError, TypeError):
        package_format = None
    if isinstance(package_format, str) and package_format.strip():
        return {
            "evidence_complete": True,
            "format_version": package_format,
            "machine_readable": True,
            "bundle_binding": "decode.json",
        }
    return {
        "evidence_complete": False,
        "format_version": "unresolved",
        "machine_readable": False,
        "bundle_binding": "unresolved",
    }


def _event_timestamps(decoded: Mapping[str, Any]) -> dict[str, Any]:
    records = decoded.get("records")
    ids: list[str] = []
    detail: list[dict[str, Any]] = []
    complete = False
    if isinstance(records, list) and records:
        ok = True
        for index, row in enumerate(records):
            if not isinstance(row, Mapping):
                ok = False
                break
            locator = row.get("locator")
            if isinstance(locator, Mapping) and isinstance(locator.get("record_location"), str) and locator["record_location"].strip():
                rid: str = locator["record_location"]
            elif isinstance(row.get("id"), str) and row["id"].strip():  # type: ignore[union-attr]
                rid = row["id"]  # type: ignore[assignment]
            else:
                rid = f"record-{index}"
            timestamp = row.get("timestamp")
            if not _rfc3339(timestamp):
                ok = False
            ids.append(rid)
            detail.append({"id": rid, "timestamp": timestamp, "unit": "rfc3339", "time_zone": "UTC"})
        # Deduplicate ids while preserving order; duplicates cannot be measured.
        seen: set[str] = set()
        unique_ids: list[str] = []
        unique_detail: list[dict[str, Any]] = []
        for rid, item in zip(ids, detail):
            if rid in seen:
                ok = False
                continue
            seen.add(rid)
            unique_ids.append(rid)
            unique_detail.append(item)
        if ok and unique_ids:
            complete = True
            ids, detail = unique_ids, unique_detail
        elif unique_ids:
            ids, detail = unique_ids, unique_detail
    if complete:
        return {"evidence_complete": True, "event_ids": ids, "records": detail}
    if ids and detail:
        return {"evidence_complete": False, "event_ids": ids, "records": detail}
    return {"evidence_complete": False, "event_ids": ["unresolved-event"], "records": []}


def _duplicate_safety(_: Mapping[str, Any], __: Any) -> dict[str, Any]:
    """Keep duplicate safety unresolved until every semantic family is audited.

    The decoder's final-response duplicate control does not establish that a
    naïve reader yields each required turn, action, result, and response once.
    """
    return {
        "evidence_complete": False,
        "event_ids": ["unresolved-event"],
        "forward_records": [],
        "deduplication": {"documented": False, "rule": ""},
    }


def _density_role(kind: str, fields: Any) -> str:
    if kind == "message" and isinstance(fields, Mapping) and fields.get("role") == "user":
        return "user_message"
    if kind == "message":
        return "assistant_message"
    if kind == "tool_call":
        return "tool_call"
    if kind in {"tool_result", "command_execution"}:
        return "tool_result"
    if kind == "file_change":
        return "file_change"
    if kind == "session":
        return "session"
    if kind in {"turn_context", "task_started", "task_complete", "token_count", "usage", "item_completed"}:
        return "metadata"
    return "unknown"


def _density_class(role: str) -> str:
    if role in {"user_message", "correction", "assistant_message", "tool_call", "tool_result", "failure", "file_change", "plan", "explanation"}:
        return "useful"
    if role in {"metadata", "index", "snapshot", "session", "system"}:
        return "unclassified"
    return "unknown"


def _classified_density(decoded: Mapping[str, Any]) -> dict[str, Any]:
    records = decoded.get("records")
    detail: list[dict[str, Any]] = []
    if isinstance(records, list) and records:
        seen: set[str] = set()
        ok = True
        for index, row in enumerate(records):
            if not isinstance(row, Mapping):
                ok = False
                break
            locator = row.get("locator") if isinstance(row.get("locator"), Mapping) else {}
            location = locator.get("record_location") if isinstance(locator, Mapping) else None
            rid = location if isinstance(location, str) and location.strip() else f"record-{index}"
            if rid in seen:
                ok = False
                break
            seen.add(rid)
            kind = row.get("kind") if isinstance(row.get("kind"), str) else "unknown"
            role = _density_role(kind, row.get("fields"))
            size: int
            if isinstance(locator, Mapping) and isinstance(locator.get("byte_start"), int) and isinstance(locator.get("byte_end"), int):
                size = locator["byte_end"] - locator["byte_start"]
            else:
                size = 1
            if not isinstance(size, int) or isinstance(size, bool) or size < 1:
                size = 1
            detail.append({
                "record_id": rid,
                "record_kind": role,
                "logical_bytes": size,
                "classification": _density_class(role),
            })
        if ok and detail and sum(item["logical_bytes"] for item in detail) > 0:
            return {
                "evidence_complete": True,
                "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE,
                "records": detail,
            }
        if detail:
            return {
                "evidence_complete": False,
                "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE,
                "records": detail,
            }
    return {
        "evidence_complete": False,
        "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE,
        "records": [{"record_id": "unresolved-record", "record_kind": "unknown", "logical_bytes": 1, "classification": "unknown"}],
    }


def build_codex_format_evidence(
    decoded: Mapping[str, Any],
    *,
    observer: Mapping[str, Any],
    native_manifest: Mapping[str, Any],
    build: str,
    collected_on: str,
    result_id: str,
    complete_record_family: bool = False,
    root_repetitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build fail-closed 12-metric format evidence from an already-decoded bundle.

    ``decoded`` must be the return value of ``decode_codex_cli_bundle`` for one
    declared copied package.  ``observer`` and ``native_manifest`` are explicit
    immutable ``{id, sha256}`` identities supplied by the caller.  ``build``,
    ``collected_on`` (YYYY-MM-DD), and ``result_id`` bind the captured
    build/date/result window.  Only bundle-local declared artifacts and decoded
    facts are used.  The result is validated with ``validate_format_evidence``.
    """

    if not isinstance(decoded, Mapping):
        raise CodexFormatEvidenceError("decoded bundle must be an object")
    observer_id = _digest(observer, "observer")
    manifest_id = _digest(native_manifest, "native_manifest")
    build_value = _trimmed(build, "build")
    collected_value = _date(collected_on, "collected_on")
    result_value = _trimmed(result_id, "result_id")
    run_id, configuration_id, repetition = _measurement_identity(decoded)
    artifacts = _artifacts(decoded)
    facts = _facts(decoded)
    diagnostics = decoded.get("diagnostics", [])
    if not isinstance(complete_record_family, bool):
        raise CodexFormatEvidenceError("complete_record_family must be boolean")
    if root_repetitions is not None and not isinstance(root_repetitions, list):
        raise CodexFormatEvidenceError("root_repetitions must be a list")

    session_id = decoded.get("session_id")
    session_text = session_id if isinstance(session_id, str) and session_id.strip() else "unresolved-session"

    identity_evidence = {
        "evidence_complete": False,
        "session_id": session_text,
        "harness": "unresolved",
        "surface": "unresolved",
        "record_family": "codex-rollout-v1",
        "external_lookup_required": True,
        "absolute_path_required": True,
    }
    honest_version = {
        "evidence_complete": False,
        "declared_version": decoded.get("identity", {}).get("cli_version") if isinstance(decoded.get("identity"), Mapping) and isinstance(decoded.get("identity", {}).get("cli_version"), str) else "unresolved",
        "decoder_contract_version": "codex-rollout-v1",
        "incompatible_schema_distinguished": False,
        "matches_decoder_contract": False,
    }
    schema_stability = {"evidence_complete": False, "advertised_contract": "unresolved", "observations": [], "exceptions": []}
    duplicate_safety = _duplicate_safety(facts, diagnostics)
    if complete_record_family:
        identity_evidence = {
            "evidence_complete": True,
            "session_id": session_text,
            "harness": "codex",
            "surface": configuration_id,
            "record_family": "codex-rollout-v1",
            "external_lookup_required": False,
            "absolute_path_required": False,
        }
        honest_version = {
            "evidence_complete": True,
            "declared_version": "codex-rollout-v1",
            "decoder_contract_version": "codex-rollout-v1",
            "incompatible_schema_distinguished": True,
            "matches_decoder_contract": True,
        }
        schema_stability = {
            "evidence_complete": True,
            "advertised_contract": "codex-rollout-v1",
            "observations": [{"build": build_value, "observed_on": collected_value, "decoder_contract": "codex-rollout-v1", "decoded": True}],
            "exceptions": [],
        }
        forward: list[dict[str, str]] = []
        event_ids: list[str] = []
        for family in ("submitted_turns", "visible_responses", "actions", "results", "changed_files"):
            rows = facts.get(family, [])
            if not isinstance(rows, list):
                continue
            for ordinal, row in enumerate(rows):
                if not isinstance(row, Mapping) or row.get("state") not in {"present", "contradiction", "unknown"}:
                    continue
                event_id = row.get("id")
                if not isinstance(event_id, str) or not event_id:
                    locator = row.get("locator")
                    event_id = locator.get("record_location") if isinstance(locator, Mapping) else None
                if not isinstance(event_id, str) or not event_id:
                    continue
                event_ids.append(event_id)
                forward.append({"event_id": event_id, "occurrence_id": f"{family}:{ordinal}", "state": "active"})
        duplicate_safety = {
            "evidence_complete": bool(event_ids),
            "event_ids": event_ids,
            "forward_records": forward,
            "deduplication": {"documented": True, "rule": "one forward semantic record per native ID or immutable record locator"},
        }

    broad_evidence: dict[str, Any] = {
        "broad.readable_rationale": _readable_rationale(facts),
        "broad.thread_structure": _thread_structure(decoded, facts),
        "broad.standard_tools_readable": _standard_tools(decoded),
        "broad.documented_format": _documented_format(decoded),
        "broad.self_contained_identity": identity_evidence,
        "broad.declared_format_version": _declared_version(decoded),
        "broad.event_timestamps": _event_timestamps(decoded),
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": _classified_density(decoded),
    }
    if set(broad_evidence) != set(FORMAT_METRICS):
        raise CodexFormatEvidenceError("broad evidence must cover all 12 format metrics")

    locators = [{"id": item["id"], "sha256": item["sha256"]} for item in artifacts]
    metric_evidence = [
        {"metric_id": metric_id, "observer_ids": [observer_id["id"]], "native_locators": [dict(item) for item in locators]}
        for metric_id in FORMAT_METRICS
    ]

    document = {
        "schema_version": FORMAT_EVIDENCE_SCHEMA_VERSION,
        "run_id": run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
        "build": build_value,
        "collected_on": collected_value,
        "result_id": result_value,
        "observer": observer_id,
        "native_manifest": manifest_id,
        "profile": {
            "schema_version": "session-bench-format-profile-v1",
            "run_id": run_id,
            "configuration_id": configuration_id,
            "repetition": repetition,
            "broad_evidence": broad_evidence,
        },
        "metric_evidence": metric_evidence,
    }
    validate_format_evidence(document)
    return document


__all__ = ["CodexFormatEvidenceError", "build_codex_format_evidence"]
