"""Fail-closed Codex format-evidence builder for the 12 broad metrics.

Turns an already-decoded copied Codex JSONL bundle plus explicit immutable
observer/native metadata into the existing v1_public_score 12-broad-metric
format-evidence document.  Uses only bundle-local declared artifacts and
decoded facts; never reads a home directory, neighboring root, credential,
or network. Unresolved evidence stays unresolved. Schema absence requires a
supported native scan and a complete captured family; version-like fields do
not imply undocumented schema semantics. Density remains unresolved until a
complete native logical record inventory uses comparable byte accounting.
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


def _native_version_scan(decoded: Mapping[str, Any], artifacts: list[dict[str, str]]) -> tuple[bool, list[dict[str, str]]]:
    """Bind the supported absence scan to individual copied native records.

    Candidate version fields retain their raw values in the decoder output.
    None currently has documented vendor schema semantics, so none can grant
    positive credit. A cli_version field has application-build semantics only.
    """
    scan = decoded.get("native_schema_version")
    if not isinstance(scan, Mapping) or scan.get("scan_rule") != "codex-native-schema-version-scan-v1":
        return False, []
    raw = scan.get("record_locators")
    metadata = scan.get("session_metadata_locators")
    fields = scan.get("version_fields")
    if not isinstance(raw, list) or not raw or not isinstance(metadata, list) or not metadata or not isinstance(fields, list):
        return False, []
    declared = {(item["id"], item["sha256"]) for item in artifacts}
    locators: list[dict[str, str]] = []
    for locator in raw:
        if not isinstance(locator, Mapping) or not isinstance(locator.get("artifact_id"), str) or not isinstance(locator.get("artifact_sha256"), str) or (locator["artifact_id"], locator["artifact_sha256"]) not in declared:
            return False, []
        location, digest = locator.get("record_location"), locator.get("record_sha256")
        if not isinstance(location, str) or not location.strip() or not isinstance(digest, str) or len(digest) != 64 or any(char not in _HEX for char in digest):
            return False, []
        locators.append({"id": f"{locator['artifact_id']}@{location}", "sha256": digest})
    if len({item["id"] for item in locators}) != len(locators) or any(item not in raw for item in metadata):
        return False, []
    no_schema_candidates = all(
        isinstance(item, Mapping)
        and (
            (item.get("semantics") == "application_build" and item.get("field_path") == "payload.cli_version"
             and item.get("locator") in metadata)
            or (item.get("semantics") == "runtime_feature" and item.get("field_path") == "payload.multi_agent_version"
                and item.get("locator") in raw)
        )
        for item in fields
    )
    return scan.get("scan_complete") is True and scan.get("state") == "absent" and no_schema_candidates, locators


def _declared_version(*, absence_proven: bool, bundle_binding: str) -> dict[str, Any]:
    # package.format and the CLI version cannot establish vendor schema
    # semantics. Only the bounded native scan can establish schema absence.
    return {
        "evidence_complete": absence_proven,
        "format_version": "",
        "machine_readable": False,
        "bundle_binding": bundle_binding if absence_proven else "",
    }


def _event_timestamps(decoded: Mapping[str, Any]) -> dict[str, Any]:
    """Keep native timestamp diagnostics without claiming an independent population."""
    records = decoded.get("records")
    ids: list[str] = []
    detail: list[dict[str, Any]] = []
    if isinstance(records, list) and records:
        for index, row in enumerate(records):
            if not isinstance(row, Mapping):
                break
            locator = row.get("locator")
            if isinstance(locator, Mapping) and isinstance(locator.get("record_location"), str) and locator["record_location"].strip():
                rid: str = locator["record_location"]
            elif isinstance(row.get("id"), str) and row["id"].strip():  # type: ignore[union-attr]
                rid = row["id"]  # type: ignore[assignment]
            else:
                rid = f"record-{index}"
            timestamp = row.get("timestamp")
            ids.append(rid)
            detail.append({"id": rid, "timestamp": timestamp, "unit": "rfc3339", "time_zone": "UTC"})
        # Keep diagnostic IDs unique as required by the format detail schema.
        seen: set[str] = set()
        unique_ids: list[str] = []
        unique_detail: list[dict[str, Any]] = []
        for rid, item in zip(ids, detail):
            if rid in seen:
                continue
            seen.add(rid)
            unique_ids.append(rid)
            unique_detail.append(item)
        ids, detail = unique_ids, unique_detail
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


def _classified_density(_: Mapping[str, Any]) -> dict[str, Any]:
    """Hold density when only a filtered semantic decode is supplied.

    Decoded semantic records omit native records, and physical JSONL spans
    are not comparable to the selected text lengths used by other adapters.
    A complete family flag does not qualify either accounting method.
    """
    return {
        "evidence_complete": False,
        "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE,
        "records": [],
    }


def _native_line_timestamp(fact: Mapping[str, Any], rollouts: Mapping[str, list[bytes]]) -> str | None:
    """The timezone-aware timestamp on the exact hashed native line of one fact."""
    locator = fact.get("locator")
    lines = rollouts.get(locator.get("artifact_sha256")) if isinstance(locator, Mapping) else None
    number = locator.get("line") if isinstance(locator, Mapping) else None
    if lines is None or type(number) is not int or not 1 <= number <= len(lines):
        return None
    raw = lines[number - 1]
    if hashlib.sha256(raw[:-1] if raw.endswith(b"\n") else raw).hexdigest() != locator.get("record_sha256"):
        return None
    try:
        value = json.loads(raw).get("timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return value if parsed.utcoffset() is not None else None


def codex_stdout_event_timestamps(decoded: Mapping[str, Any], *, observer_document: bytes,
                                  native_package: str | Path) -> dict[str, Any] | None:
    """Timestamp evidence for every primary event of the Codex stdout observer.

    Turns and responses join by turn and exact text, actions and results by
    their decoded id, and the file change through the edit action's native
    change id.  Each timestamp is read from the fact's own hashed rollout
    line.  The result is None unless every observed event joins exactly once.
    """
    observer = json.loads(observer_document)
    required = {row["id"]: row for row in observer["events"] if row["population_role"] == "primary_scored"
                and row["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"}}
    facts = decoded["facts"]
    selected: dict[str, Mapping[str, Any]] = {}
    for family, kind in (("submitted_turns", "user_turn"), ("visible_responses", "assistant_response"),
                         ("actions", "action"), ("results", "result")):
        for fact in facts[family]:
            if fact.get("state") != "present":
                continue
            if kind in {"user_turn", "assistant_response"}:
                matched = [row["id"] for row in required.values() if row["kind"] == kind
                           and row["fields"].get("turn_id") == fact.get("turn_id") and row["fields"].get("text") == fact.get("text")]
            else:
                matched = [row["id"] for row in required.values() if row["kind"] == kind and row["id"] == fact.get("id")]
            if len(matched) == 1:
                if matched[0] in selected:
                    return None
                selected[matched[0]] = fact
    changes = facts["changed_files"]
    observed_changes = [row for row in required.values() if row["kind"] == "file_change"]
    edit = selected.get("action-edit")
    if (len(changes) != 1 or len(observed_changes) != 1 or edit is None
            or changes[0].get("id") != edit.get("native_file_change_id")
            or changes[0].get("paths") != [observed_changes[0]["fields"].get("path")]):
        return None
    selected[observed_changes[0]["id"]] = changes[0]
    if set(selected) != set(required):
        return None
    rollouts = {hashlib.sha256(raw).hexdigest(): raw.splitlines(keepends=True)
                for raw in (path.read_bytes() for path in sorted(Path(native_package).rglob("*.jsonl")))}
    records = []
    for event_id in required:
        timestamp = _native_line_timestamp(selected[event_id], rollouts)
        if timestamp is None:
            return None
        records.append({"id": event_id, "timestamp": timestamp, "unit": "rfc3339", "time_zone": "native RFC3339 offset"})
    return {"evidence_complete": True, "event_ids": list(required), "records": records}


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
    native_package: str | Path | None = None,
    observer_document: bytes | None = None,
    event_timestamps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build fail-closed 12-metric format evidence from an already-decoded bundle.

    ``decoded`` must be the return value of ``decode_codex_cli_bundle`` for one
    declared copied package.  ``observer`` and ``native_manifest`` are explicit
    immutable ``{id, sha256}`` identities supplied by the caller.  ``build``,
    ``collected_on`` (YYYY-MM-DD), and ``result_id`` bind the captured
    build/date/result window.  Only bundle-local declared artifacts and decoded
    facts are used. CLI density additionally requires an explicit ``native_package``
    with the same declared artifact hashes and ``complete_record_family=True``;
    every native record is then inventoried under canonical UTF-8 JSON byte
    accounting. Desktop density remains unresolved until its full companion
    family is inventoried. The result is validated with ``validate_format_evidence``.
    Rationale and timestamp coverage require exact serialized ``observer_document`` bytes
    matching the supplied observer digest; a digest alone leaves it unresolved.
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
    native_version_absent, version_locators = _native_version_scan(decoded, artifacts)
    facts = _facts(decoded)
    diagnostics = decoded.get("diagnostics", [])
    if not isinstance(complete_record_family, bool):
        raise CodexFormatEvidenceError("complete_record_family must be boolean")
    if root_repetitions is not None and not isinstance(root_repetitions, list):
        raise CodexFormatEvidenceError("root_repetitions must be a list")

    session_id = decoded.get("session_id")
    session_text = session_id if isinstance(session_id, str) and session_id.strip() else "unresolved-session"
    density_evidence = _classified_density(decoded)
    density_locators: tuple[dict[str, str], ...] = ()
    if configuration_id == "codex-cli" and complete_record_family and native_package is not None:
        inventory = inventory_native_jsonl_density(native_package, family="codex", session_id=session_id, expected_artifacts=artifacts)
        density_evidence, density_locators = inventory.evidence, inventory.native_locators

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
        "evidence_complete": complete_record_family and native_version_absent,
        "declared_version": "",
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
        # A complete root proves acquisition; native scan evidence is required
        # separately before absence can resolve either version assertion.
        schema_stability = {
            "evidence_complete": True,
            "advertised_contract": "codex-rollout-v1",
            "observations": [{"build": build_value, "observed_on": collected_value, "decoder_contract": "codex-rollout-v1", "decoded": True}],
            "exceptions": [],
        }
        # The read is the declared rollout. Occurrences are counted on its raw
        # records, not on decoded facts; without the package the row stays unresolved.
        occurrences = native_forward_occurrences(native_package, family="codex", expected_artifacts=artifacts) if native_package is not None else None
        if occurrences:
            duplicate_safety = {
                "evidence_complete": True,
                "event_ids": list(dict.fromkeys(event for event, _ in occurrences)),
                "forward_records": [{"event_id": event, "occurrence_id": occurrence, "state": "active"} for event, occurrence in occurrences],
                "deduplication": {"documented": True, "rule": "the read is the rollout JSONL; a message is one event per turn, role and exact text, a tool call is call_id; response_item, the item_completed copy and task_complete.last_agent_message are all retained occurrences, and no field marks one as superseded"},
            }

    broad_evidence: dict[str, Any] = {
        "broad.readable_rationale": build_observer_rationale_evidence(
            decoded, observer=observer_id, run_id=run_id, observer_document=observer_document,
            complete_record_family=complete_record_family,
        ),
        "broad.thread_structure": _thread_structure(decoded, facts),
        "broad.standard_tools_readable": _standard_tools(decoded),
        "broad.documented_format": _documented_format(decoded),
        "broad.self_contained_identity": identity_evidence,
        "broad.declared_format_version": _declared_version(absence_proven=complete_record_family and native_version_absent, bundle_binding=manifest_id["id"]),
        "broad.event_timestamps": dict(event_timestamps) if event_timestamps is not None else build_observer_timestamp_evidence(
            decoded, family="codex", observer=observer_id, run_id=run_id,
            observer_document=observer_document, complete_record_family=complete_record_family,
            unresolved_diagnostics=_event_timestamps(decoded),
        ),
        "broad.honest_version_signal": honest_version,
        "broad.observed_schema_stability": schema_stability,
        "broad.stable_root_location": {"evidence_complete": root_repetitions is not None, "repetitions": root_repetitions or []},
        "broad.naive_reader_duplicate_safety": duplicate_safety,
        "broad.classified_content_density": density_evidence,
    }
    if set(broad_evidence) != set(FORMAT_METRICS):
        raise CodexFormatEvidenceError("broad evidence must cover all 12 format metrics")

    locators = [{"id": item["id"], "sha256": item["sha256"]} for item in artifacts]
    metric_evidence = [
        {"metric_id": metric_id, "observer_ids": [observer_id["id"]], "native_locators": [dict(item) for item in (
            density_locators if density_locators and metric_id == "broad.classified_content_density"
            else version_locators if version_locators and metric_id in {"broad.declared_format_version", "broad.honest_version_signal"}
            else locators
        )]}
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
