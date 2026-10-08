"""Independent event populations and exact native timestamp witnesses.

Only primary observer turns, responses, actions, results and file changes are
required. Native metadata and unscored exploration cannot redefine that set.
JSONL line/block locators and SQLite message/part row locators retain native
order; observer sequence is not assumed to be a native chronological order.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any, Mapping

from .format_response_population import _bound_observer, ObserverResponseEvidenceError, observed_response_text_matches
from .live_metric_comparator import (
    _SEVERE_DIAGNOSTICS, _argv, _cwd, _field, _match_action, _match_response,
    _match_turn, _native_actions, _native_changes, _native_id, _native_responses,
    _native_results, _native_supported, _native_turns, _observer_events,
    _same_path, _target, _validate_observer,
)

_KINDS = ("user_turn", "assistant_response", "action", "result", "file_change")
_CALL_KEYS = ("call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _streams(decoded: Mapping[str, Any], family: str):
    keys = {"codex": ("records",), "claude": ("events",), "opencode": ("messages", "parts"), "dsh": ("records",), "copilot": ("records",)}[family]
    arrays = [decoded.get(key) for key in keys]
    valid = all(isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows) for rows in arrays)
    return ([row for rows in arrays for row in rows] if valid else []), valid


def _locator(row: Mapping[str, Any], family: str) -> str | None:
    value = row.get("locator")
    if not isinstance(value, Mapping):
        return None
    if family == "dsh":
        line, sequence = value.get("line"), value.get("seq")
        valid = (isinstance(line, int) and not isinstance(line, bool) and line > 0
                 and isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 0
                 and _nonempty(value.get("raw_sha256")) and _nonempty(value.get("physical_sha256")))
    elif family == "opencode":
        valid = value.get("table") in {"message", "part"} and _nonempty(value.get("row_id")) and _nonempty(value.get("artifact"))
    else:
        line = value.get("line")
        valid = (isinstance(line, int) and not isinstance(line, bool) and line > 0
                 and (_nonempty(value.get("artifact")) or _nonempty(value.get("artifact_id"))))
        if "block" in value:
            block = value["block"]
            valid = valid and isinstance(block, int) and not isinstance(block, bool) and block >= 0
    if not valid:
        return None
    try:
        return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None


def _sufficient(row: Mapping[str, Any]) -> bool:
    fields, kind = row["fields"], row["kind"]
    if kind == "user_turn":
        return _nonempty(fields.get("text"))
    if kind == "assistant_response":
        return _nonempty(fields.get("text")) and any(_nonempty(fields.get(key)) for key in ("canary", "response_canary", "turn_id"))
    if kind == "action":
        # An edit has no argv: its tool name, target file and turn identify it.
        return (any(_nonempty(fields.get(key)) for key in ("native_action_id", "call_id"))
                or (isinstance(fields.get("argv"), list) and bool(fields["argv"]) and _nonempty(fields.get("turn_id")))
                or all(_nonempty(fields.get(key)) for key in ("name", "target", "turn_id")))
    if kind == "result":
        return any(_nonempty(fields.get(key)) for key in ("native_result_id", "call_id", "action_id"))
    return _nonempty(fields.get("path")) and any(_nonempty(fields.get(key)) for key in ("native_change_id", "action_id", "call_id"))


def _aliases(expected, candidates, matcher, keys):
    # Every successful occurrence contributes aliases; contradictory aliases
    # are removed rather than resolved by input order or greedy matching.
    result, ambiguous = {}, set()
    for candidate in candidates:
        matches = [row["id"] for row in expected if matcher(row, candidate)]
        if len(matches) != 1:
            continue
        values = [_native_id(candidate), *(_field(candidate, key) for key in keys)]
        for value in values:
            if _nonempty(value):
                if value in result and result[value] != matches[0]:
                    ambiguous.add(value)
                result[value] = matches[0]
    return {key: value for key, value in result.items() if key not in ambiguous}


def _bound_relation(expected, candidate, action_map, native_key):
    fields = expected["fields"]
    related = False
    native_id = fields.get(native_key)
    if _nonempty(native_id):
        if _native_id(candidate) != native_id:
            return False
        related = True
    call = fields.get("call_id")
    native_call = _field(candidate, *_CALL_KEYS)
    if _nonempty(call):
        if native_call != call:
            return False
        related = True
    action = fields.get("action_id")
    if _nonempty(action):
        native_action = _field(candidate, "action_id", "expected_action_id")
        if native_action != action and action_map.get(native_action) != action and action_map.get(native_call) != action:
            return False
        related = True
    return related


def build_observer_timestamp_evidence(
    decoded: Mapping[str, Any], *, family: str, observer: Mapping[str, Any],
    run_id: str, observer_document: bytes | None = None,
    complete_record_family: bool = False,
    unresolved_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Use the hash-bound observer denominator, retaining duplicate witnesses.

    The complete-family declaration is a caller contract, not a root verifier.
    Missing observer/stream/completeness proof stays unresolved. Once complete,
    missing events and missing/malformed timestamps lower coverage. Timestamp
    values come only from an exact native locator, never from observer times,
    expected event order, a nearby record, or a guessed action timestamp.
    """
    if family not in {"codex", "claude", "opencode", "dsh", "copilot"}:
        raise ValueError("unsupported timestamp record family")
    if not isinstance(complete_record_family, bool):
        raise ObserverResponseEvidenceError("complete_record_family must be boolean")
    if observer_document is None:
        return {**dict(unresolved_diagnostics or {"event_ids": [], "records": []}), "evidence_complete": False}
    document = _bound_observer(observer_document, observer, run_id)
    _, events, _ = _validate_observer(document)
    expected = [row for row in events if row["kind"] in _KINDS and row["population_role"] == "primary_scored"]
    required = [row["id"] for row in expected]
    raw, valid_streams = _streams(decoded, family)
    diagnostics = decoded.get("diagnostics", [])
    clean = isinstance(diagnostics, list) and all(
        isinstance(row, Mapping) and row.get("code") not in _SEVERE_DIAGNOSTICS and row.get("severity") != "error"
        for row in diagnostics
    )
    # Validate projection arrays too: silently filtering malformed projections
    # would otherwise turn an incomplete decode into measured event absence.
    projections = [decoded[key] for key in ("turns", "responses", "actions", "results", "file_changes") if key in decoded]
    facts = decoded.get("facts")
    if family == "codex" and isinstance(facts, Mapping):
        projections += [facts[key] for key in ("submitted_turns", "visible_responses", "changed_files") if key in facts]
    valid_projections = all(isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows) for rows in projections)
    complete = bool(expected) and complete_record_family and valid_streams and valid_projections and clean and _native_supported(decoded) and all(_sufficient(row) for row in expected)
    by_kind = {kind: _observer_events(events, kind) for kind in _KINDS}
    turns = _native_turns(decoded)
    turn_match = lambda row, candidate: _match_turn(row, candidate).value is True
    turn_map = _aliases(by_kind["user_turn"], turns, turn_match, ("turn_id", "native_turn_id"))
    actions = _native_actions(decoded)
    def action_match(row, candidate):
        # Timestamp identity can be bound by an independently observed native
        # action/call ID even when the native shell record omits a workload
        # target or cwd. Keep every comparable field: an explicit conflicting
        # command/target/turn remains a rejection. This does not qualify the
        # separate action-content metric or infer missing native fields.
        fields = dict(row["fields"])
        if _nonempty(fields.get("native_action_id")) or _nonempty(fields.get("call_id")):
            for key, native_value in (("target", _target(candidate)), ("cwd", _cwd(candidate)), ("argv", _argv(candidate))):
                if native_value is None:
                    fields.pop(key, None)
        return _match_action({**row, "fields": fields}, candidate, turn_map).value is True
    action_map = _aliases(by_kind["action"], actions, action_match, _CALL_KEYS)
    # Codex explicitly links a file-change event to its action. This is a
    # native identity link, not a path/nearest-action inference.
    change_actions = {}
    for candidate in actions:
        action_id = action_map.get(_native_id(candidate))
        change = candidate.get("file_change")
        if action_id and isinstance(change, Mapping) and _native_id(change):
            change_actions.setdefault(_native_id(change), set()).add(action_id)

    def response_match(row, candidate):
        if _match_response(row, candidate, turn_map).value is not True:
            return False
        # The same text rule as the rationale evidence, canary-only observers included.
        return observed_response_text_matches(row["fields"], _field(candidate, "text", "content"))

    def result_match(row, candidate):
        if not _bound_relation(row, candidate, action_map, "native_result_id"):
            return False
        # Contradictory comparable result content cannot authorize identity.
        for key in ("output", "status", "exit_code", "helper_nonce"):
            left, right = row["fields"].get(key), _field(candidate, key)
            if left is not None and right is not None and left != right:
                return False
        return True

    def change_match(row, candidate):
        fields = row["fields"]
        paths = _field(candidate, "paths")
        paths = paths if isinstance(paths, list) else [_field(candidate, "path", "target", "file_path")]
        if not any(_same_path(fields.get("path"), path) for path in paths):
            return False
        enriched = dict(candidate)
        linked = change_actions.get(_native_id(candidate), set())
        if len(linked) == 1 and _field(candidate, "action_id") is None:
            enriched["action_id"] = next(iter(linked))
        return _bound_relation(row, enriched, action_map, "native_change_id")

    candidates = {"user_turn": turns, "assistant_response": _native_responses(decoded),
                  "action": actions, "result": _native_results(decoded), "file_change": _native_changes(decoded)}
    matchers = {"user_turn": turn_match, "assistant_response": response_match,
                "action": action_match, "result": result_match, "file_change": change_match}
    witnesses = defaultdict(list)
    for row in raw:
        locator = _locator(row, family)
        if locator:
            witnesses[locator].append(row)
    groups = defaultdict(list)
    for kind in _KINDS:
        for candidate in candidates[kind]:
            matched = [row["id"] for row in by_kind[kind] if matchers[kind](row, candidate)]
            if not matched:
                continue  # unrelated exploration/metadata is outside the population
            event_id = matched[0] if len(matched) == 1 else f"ambiguous-native-event:{kind}:{len(groups)}"
            if len(matched) != 1:
                while event_id in required:
                    event_id += ":unmatched"
            groups[(event_id, _locator(candidate, family))].append(candidate)
    records = []
    unit = "unix_ms" if family in {"opencode", "dsh"} else "rfc3339"
    for (event_id, locator), occurrences in groups.items():
        values = witnesses.get(locator, []) if locator else []
        if family == "claude":
            # Compound actions/results can share one content-block locator.
            # Their distinct native call identities are separate semantic
            # events, not duplicate witnesses for each other.
            native_ids = {_native_id(row) for row in occurrences}
            values = [row for row in values if _native_id(row) in native_ids]
        # Repeated projections and repeated native witnesses are two views of
        # occurrences, not a Cartesian product of duplicate counts.
        for index in range(max(len(occurrences), len(values))):
            witness = values[index % len(values)] if values else {}
            timestamp = witness.get("timestamp_ms" if family == "dsh" else "time_created" if family == "opencode" else "timestamp")
            if isinstance(timestamp, float) and not math.isfinite(timestamp):
                timestamp = None
            records.append({"id": event_id, "timestamp": timestamp, "unit": unit,
                            "time_zone": "UTC" if family in {"opencode", "dsh"} else "native RFC3339 offset"})
    return {"evidence_complete": bool(complete), "event_ids": required, "records": records}
