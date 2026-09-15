"""Conservative offline joining of survival-v1 observer and native evidence.

The native adapters intentionally stop at a native semantic decode.  They do
not have the independent observer needed to produce a survival score.  This
module is the small, offline join at that boundary: it accepts one frozen
observer document, one already-decoded native result, and an explicit
portability receipt, then emits the strict 19-row document consumed by
``survival_metrics``.

The join is deliberately evidence-first.  Counts come from explicit event or
fact records, not decoder aggregates.  A record is counted as correct only
when its semantic fields match the supplied (possibly run-instantiated)
observer.  ``native_absent`` is used only when a supported decoder and an
explicit complete-root receipt establish the search boundary.  In particular,
this module never uses the frozen fixture canaries as a lookup key.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex
from typing import Any, Callable, Iterable, Mapping, Sequence

from .survival_metrics import METRICS, SCHEMA_VERSION, validate_input


OBSERVER_SCHEMA_VERSION = "1.0-survival-observer"
PROTOCOL_VERSION = "1.0-survival"
SCENARIO_ID = "survival-v1-repair"

_METRIC_IDS = tuple(METRICS)
_PRIMARY_KINDS = frozenset(
    {"user_turn", "assistant_response", "action", "result", "file_change"}
)
_RELATION_KINDS = frozenset(
    {"action_result", "turn_response", "supersedes", "final_after", "helper_for"}
)
_SEVERE_DIAGNOSTICS = frozenset(
    {
        "decode_error",
        "malformed_record",
        "malformed_message",
        "malformed_part",
        "unsupported_schema",
        "unsupported_version",
        "invalid_boundary",
        "receipt_digest_mismatch",
        "missing_artifact",
        "missing_dependency",
        "unjoined_execution",
    }
)
_CANARY = re.compile(r"SB_SURVIVAL_V1_[^\s<`]+", re.UNICODE)
_REVISION = re.compile(r"\bR([12])\b", re.IGNORECASE)
_PATCH_FILE = re.compile(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE)


class ComparatorError(ValueError):
    """The supplied observer/native/receipt boundary is not joinable."""


@dataclass(frozen=True)
class _Match:
    """One semantic comparison result.

    ``value`` is ``True`` for a match, ``False`` for a related contradiction,
    and ``None`` when the candidate cannot be compared.  ``related`` prevents
    unrelated native records (for example an exploratory shell command) from
    being misreported as contradictions.
    """

    value: bool | None
    related: bool = False


@dataclass(frozen=True)
class _Portable:
    complete_root: bool | None
    companions: bool | None
    isolated_decode: bool | None
    canonical_equality: bool | None
    supported: bool | None


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ComparatorError(f"{label} must be an object")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComparatorError(f"{label} must be a non-empty string")
    return value


def _list(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise ComparatorError(f"{label} must be an array")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ComparatorError(f"{label}[{index}] must be an object")
        result.append(item)
    return result


def _validate_observer(observer: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    value = _mapping(observer, "observer")
    if value.get("schema_version") != OBSERVER_SCHEMA_VERSION:
        raise ComparatorError(f"observer schema_version must be {OBSERVER_SCHEMA_VERSION!r}")
    if value.get("protocol_version") != PROTOCOL_VERSION:
        raise ComparatorError(f"observer protocol_version must be {PROTOCOL_VERSION!r}")
    if value.get("scenario_id") != SCENARIO_ID:
        raise ComparatorError(f"observer scenario_id must be {SCENARIO_ID!r}")
    if value.get("independent") is not True:
        raise ComparatorError("observer independent must be true")
    run_id = _nonempty(value.get("run_id"), "observer.run_id")
    events = _list(value.get("events"), "observer.events")
    relations = _list(value.get("relations"), "observer.relations")
    if not events:
        raise ComparatorError("observer.events must not be empty")
    event_ids: set[str] = set()
    sequences: set[int] = set()
    for index, event in enumerate(events):
        label = f"observer.events[{index}]"
        event_id = _nonempty(event.get("id"), f"{label}.id")
        if event_id in event_ids:
            raise ComparatorError(f"duplicate observer event id: {event_id}")
        event_ids.add(event_id)
        sequence = event.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ComparatorError(f"{label}.sequence must be a positive integer")
        if sequence in sequences:
            raise ComparatorError(f"duplicate observer sequence: {sequence}")
        sequences.add(sequence)
        if event.get("kind") not in _PRIMARY_KINDS | {"helper", "usage_total", "portable"}:
            raise ComparatorError(f"{label}.kind is unsupported")
        if event.get("population_role") not in {"primary_scored", "supporting", "unscored"}:
            raise ComparatorError(f"{label}.population_role is invalid")
        _nonempty(event.get("session_id"), f"{label}.session_id")
        if not isinstance(event.get("fields"), Mapping):
            raise ComparatorError(f"{label}.fields must be an object")
        metric_ids = event.get("metric_ids")
        if not isinstance(metric_ids, list) or any(not isinstance(item, str) for item in metric_ids):
            raise ComparatorError(f"{label}.metric_ids must be an array of strings")
    relation_ids: set[str] = set()
    for index, relation in enumerate(relations):
        label = f"observer.relations[{index}]"
        relation_id = _nonempty(relation.get("id"), f"{label}.id")
        if relation_id in relation_ids:
            raise ComparatorError(f"duplicate observer relation id: {relation_id}")
        relation_ids.add(relation_id)
        if relation.get("kind") not in _RELATION_KINDS:
            raise ComparatorError(f"{label}.kind is unsupported")
        for field in ("from_id", "to_id"):
            _nonempty(relation.get(field), f"{label}.{field}")
            if relation[field] not in event_ids:
                raise ComparatorError(f"{label}.{field} does not name an observer event")
        sequence = relation.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ComparatorError(f"{label}.sequence must be a positive integer")
    return run_id, events, relations


def _unwrap(value: Any) -> Any:
    if isinstance(value, Mapping):
        # Decoder fact wrappers use state/value/partial.  A native payload that
        # happens to contain a field named value remains safe because the state
        # marker is required for unwrapping.
        if "state" in value and ("value" in value or "partial" in value):
            return value.get("value", value.get("partial"))
    return value


def _field(value: Mapping[str, Any], *names: str) -> Any:
    containers: list[Mapping[str, Any]] = [value]
    for key in ("fields", "data", "payload"):
        item = value.get(key)
        if isinstance(item, Mapping):
            containers.append(item)
    for name in names:
        for container in containers:
            if name in container:
                return _unwrap(container[name])
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip()
    # OpenCode stores submitted prompts as a JSON string in some versions.
    if len(result) >= 2 and result[0] == '"' and result[-1] == '"':
        try:
            decoded = json.loads(result)
            if isinstance(decoded, str):
                result = decoded.strip()
        except json.JSONDecodeError:
            pass
    result = re.sub(r"<user_query>\s*", "", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*</user_query>", "", result, flags=re.IGNORECASE)
    return result


def _semantic_text(value: Any) -> str | None:
    result = _text(value)
    if result is None:
        return None
    return result


def _embedded_canary(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    match = _CANARY.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:)]}")


def _revision(value: Mapping[str, Any]) -> str | None:
    direct = _field(value, "revision")
    if isinstance(direct, str) and direct.lower() in {"r1", "r2"}:
        return direct.lower()
    match = _REVISION.search(_text(_field(value, "text", "query", "content")) or "")
    return f"r{match.group(1)}".lower() if match else None


def _kind(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "kind", "type", "event_kind")
    return raw if isinstance(raw, str) else None


def _sequence(value: Mapping[str, Any], fallback: int | None = None) -> int | float | str | None:
    for key in ("sequence", "ordinal", "time_created", "timestamp", "time"):
        raw = _field(value, key)
        if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
            return raw
    locator = value.get("locator")
    if isinstance(locator, Mapping):
        for key in ("ordinal", "line", "row_id"):
            raw = locator.get(key)
            if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
                return raw
    return fallback


def _order_key(value: Mapping[str, Any], fallback: int) -> tuple[int, str]:
    raw = _sequence(value, fallback)
    if isinstance(raw, (int, float)):
        return (0, f"{raw:030.9f}")
    if isinstance(raw, str):
        return (1, raw)
    return (2, str(fallback))


def _path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("\\", "/")
    return candidate


def _same_path(left: Any, right: Any) -> bool:
    a, b = _path(left), _path(right)
    if a is None or b is None:
        return False
    if a == b:
        return True
    # Native stores often retain the absolute session directory while the
    # observer intentionally keeps a fixture-relative path.
    return a.endswith("/" + b) or b.endswith("/" + a)


def _argv(value: Mapping[str, Any]) -> tuple[str, ...] | None:
    raw = _field(value, "argv", "command", "command_line", "cmd")
    if isinstance(raw, (list, tuple)) and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    if isinstance(raw, str):
        try:
            return tuple(shlex.split(raw))
        except ValueError:
            return None
    nested = _field(value, "input", "arguments")
    if isinstance(nested, Mapping):
        raw = nested.get("argv", nested.get("command", nested.get("cmd")))
        if isinstance(raw, (list, tuple)) and all(isinstance(item, str) for item in raw):
            return tuple(raw)
        if isinstance(raw, str):
            try:
                return tuple(shlex.split(raw))
            except ValueError:
                return None
    return None


def _argv_semantically_equal(expected: Sequence[str], candidate: Sequence[str]) -> bool:
    """Compare commands while treating the per-run canary as metadata.

    The independent stream retains ``--run-canary VALUE`` so the observer can
    prove the invocation was bound to this run.  Claude's native projection
    validates that same flag and removes it from its canonical argv.  Preserve
    a mismatch when both sides expose different canaries, while allowing the
    validated flag to be absent from the native semantic projection.
    """
    left, right = list(expected), list(candidate)

    def flag(values: list[str]) -> tuple[int, str] | None:
        indexes = [index for index, item in enumerate(values) if item == "--run-canary"]
        if not indexes:
            return None
        if len(indexes) != 1:
            return (-1, "")
        index = indexes[0]
        if index + 1 >= len(values):
            return (-1, "")
        return index, values[index + 1]

    left_flag, right_flag = flag(left), flag(right)
    if left_flag is not None:
        if left_flag[0] < 0:
            return False
        if right_flag is None:
            if not left_flag[1].startswith("SB_SURVIVAL_V1_RUN_"):
                return False
            del left[left_flag[0] : left_flag[0] + 2]
        else:
            if right_flag[0] < 0 or left_flag[1] != right_flag[1]:
                return False
            del left[left_flag[0] : left_flag[0] + 2]
            del right[right_flag[0] : right_flag[0] + 2]
    elif right_flag is not None:
        # A native-only canary is not silently discarded when the observer did
        # not observe one on this action.
        return False
    return left == right


def _target(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "target", "path", "target_path", "patch_target", "file_path", "filePath")
    if isinstance(raw, str) and raw:
        return raw.strip()
    nested = _field(value, "input", "arguments")
    if isinstance(nested, Mapping):
        for key in ("target", "path", "target_path", "file_path", "filePath"):
            item = nested.get(key)
            if isinstance(item, str) and item:
                return item.strip()
        patch = nested.get("patchText", nested.get("patch"))
        if isinstance(patch, str):
            match = _PATCH_FILE.search(patch)
            if match:
                return match.group(1).strip()
    patch = _field(value, "patchText", "patch")
    if isinstance(patch, str):
        match = _PATCH_FILE.search(patch)
        if match:
            return match.group(1).strip()
    return None


def _cwd(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "cwd", "workdir", "working_directory", "workingDirectory")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _native_id(value: Mapping[str, Any]) -> str | None:
    # Claude Code's JSONL projection keeps the provider call identifier in
    # ``tool_use_id`` rather than copying it to the normalized event ``id``.
    # Results use the observer contract's ``<call-id>:result`` identity so an
    # action and its result cannot collapse into one population member.
    raw_kind = _field(value, "kind", "event_kind", "type")
    tool_use_id = _field(value, "tool_use_id", "toolUseId")
    if isinstance(tool_use_id, str) and tool_use_id:
        if raw_kind in {"result", "tool_result"}:
            return f"{tool_use_id}:result"
        if raw_kind in {"action", "tool_use", "tool_call", "custom_tool_call"}:
            return tool_use_id
    for key in ("id", "event_id", "native_id"):
        raw = value.get(key)
        if isinstance(raw, str) and raw:
            return raw
    if isinstance(tool_use_id, str) and tool_use_id:
        return tool_use_id
    return None


def _turn_id(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "turn_id", "turn", "parent_id", "parentID")
    return raw if isinstance(raw, str) and raw else None


def _native_role(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "role")
    return raw if isinstance(raw, str) else None


def _native_records(native: Mapping[str, Any], names: Sequence[str], fact_names: Sequence[str], kinds: set[str]) -> list[Mapping[str, Any]]:
    for key in names:
        value = native.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    facts = native.get("facts")
    if isinstance(facts, Mapping):
        for key in fact_names:
            value = facts.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    for key in ("events", "records"):
        value = native.get(key)
        if not isinstance(value, list):
            continue
        result: list[Mapping[str, Any]] = []
        for item in value:
            if not isinstance(item, Mapping):
                continue
            raw_kind = _kind(item)
            role = _native_role(item)
            if raw_kind in kinds or ("user" in kinds and role == "user") or ("assistant" in kinds and role == "assistant"):
                result.append(item)
        if result:
            return result
    return []


def _native_turns(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    # Claude Code's JSONL adapter emits ``submitted_turn`` events directly;
    # Codex/OpenCode adapters use ``turn``/``user_turn`` or a facts family.
    # Keep the semantic kind in this shared set so the observer/native join
    # does not silently turn a complete Claude transcript into an unresolved
    # submitted-turn population.
    return _native_records(native, ("turns", "submitted_turns"), ("turns", "submitted_turns"), {"turn", "user_turn", "submitted_turn", "user_message", "submission", "user"})


def _native_responses(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = _native_records(native, ("responses", "visible_responses"), ("responses", "visible_responses"), {"assistant_response", "assistant_message", "visible_response", "response", "assistant"})
    result: list[Mapping[str, Any]] = []
    for item in values:
        phase = _field(item, "phase")
        finish = _field(item, "finish")
        if phase is not None and phase != "final_answer":
            continue
        if finish is not None and finish != "stop":
            continue
        text = _field(item, "text", "content")
        if text is not None and _text(text) == "":
            continue
        result.append(item)
    return result


def _native_actions(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _native_records(native, ("actions",), ("action_facts",), {"action", "tool_call", "custom_tool_call", "tool_use"})


def _native_results(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _native_records(native, ("results",), ("result_facts",), {"result", "tool_result", "custom_tool_call_output"})


def _native_changes(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _native_records(native, ("file_changes", "changed_files"), ("changed_files",), {"file_change"})


def _native_relations(native: Mapping[str, Any], kind: str | None = None) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    raw = native.get("relations")
    if isinstance(raw, list):
        values.extend(item for item in raw if isinstance(item, Mapping))
    facts = native.get("facts")
    if isinstance(facts, Mapping):
        names = ("action_result_relations",) if kind == "action_result" else ("turn_response_relations",) if kind == "turn_response" else ("relations",)
        for name in names:
            raw = facts.get(name)
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, Mapping):
                        continue
                    if kind is not None and not isinstance(item.get("kind"), str):
                        values.append({**item, "kind": kind})
                    else:
                        values.append(item)
    if not values:
        for key in ("events", "records"):
            raw = native.get(key)
            if not isinstance(raw, list):
                continue
            values.extend(item for item in raw if isinstance(item, Mapping) and _kind(item) in _RELATION_KINDS)
    if kind is None:
        return values
    return [item for item in values if _field(item, "kind", "relation_kind") == kind or (kind == "action_result" and _field(item, "kind") == "action-result")]


def _native_usage(native: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = _native_records(native, ("usage", "usage_facts"), ("usage",), {"usage", "usage_total"})
    for response in _native_responses(native):
        usage = _field(response, "usage")
        if isinstance(usage, Mapping):
            values.append({"response_id": _native_id(response), "turn_id": _turn_id(response), "usage": usage, "fields": {"usage": usage}, "locator": response.get("locator", {})})
    return values


def _native_supported(native: Mapping[str, Any]) -> bool:
    if isinstance(native.get("supported"), bool):
        return native["supported"]
    if isinstance(native.get("status"), str):
        return native["status"] == "ok"
    if isinstance(native.get("decoder_supported"), bool):
        return native["decoder_supported"]
    diagnostics = native.get("diagnostics")
    if diagnostics is None:
        return True
    if not isinstance(diagnostics, list):
        return False
    return not any(isinstance(item, Mapping) and item.get("code") in _SEVERE_DIAGNOSTICS for item in diagnostics)


def _portable_value(receipt: Mapping[str, Any], names: Sequence[str]) -> bool | None:
    containers: list[Mapping[str, Any]] = [receipt]
    for key in ("portable", "portability", "receipt", "facts", "fields"):
        value = receipt.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    for name in names:
        for container in containers:
            if name not in container:
                continue
            value = container[name]
            if isinstance(value, bool):
                return value
            if isinstance(value, Mapping):
                if isinstance(value.get("value"), bool):
                    return value["value"]
                if value.get("state") in {"present", "measured", "true", True}:
                    return True
                if value.get("state") in {"absent", "missing", "false", False}:
                    return False
            if isinstance(value, str) and value in {"present", "measured", "true"}:
                return True
            if isinstance(value, str) and value in {"absent", "missing", "false"}:
                return False
    return None


def _portable(receipt: Mapping[str, Any]) -> _Portable:
    value = _mapping(receipt, "portability receipt")
    return _Portable(
        _portable_value(value, ("complete_root", "complete-root", "complete_root_proven", "roots_complete")),
        _portable_value(value, ("companions_present", "companions", "required_companions_present", "companions_complete")),
        _portable_value(value, ("isolated_decode", "isolated_decode_proven", "copy_decode")),
        _portable_value(value, ("canonical_equality", "canonical_equal", "canonical_equality_proven", "decoded_semantics_identical", "ordinary_isolated_equal")),
        _portable_value(value, ("supported", "decoder_supported")),
    )


def _observer_events(events: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [item for item in events if item.get("kind") == kind and item.get("population_role") == "primary_scored"]


def _observer_relations(relations: Sequence[Mapping[str, Any]], kind: str) -> list[Mapping[str, Any]]:
    return [item for item in relations if item.get("kind") == kind]


def _match_turn(expected: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Match:
    ef = _mapping(expected.get("fields"), "observer turn fields")
    expected_id = expected.get("id")
    candidate_id = _native_id(candidate)
    identity_related = candidate_id == expected_id or _turn_id(candidate) == expected_id
    er = ef.get("revision")
    cr = _revision(candidate)
    et = _semantic_text(ef.get("text"))
    ct = _semantic_text(_field(candidate, "text", "query", "content"))
    run_canary = _semantic_text(ef.get("run_canary"))
    run_related = bool(run_canary and ct and run_canary in ct)
    related = identity_related or run_related
    if er is not None and cr is not None:
        if er != cr:
            return _Match(False, True) if related else _Match(None, False)
        related = True
    if et is not None and ct is not None:
        if et != ct:
            return _Match(False, True) if identity_related or run_related else _Match(None, False)
        related = True
    erole = ef.get("role")
    crole = _native_role(candidate)
    if erole is not None and crole is not None:
        if erole != crole:
            return _Match(False, True) if identity_related or run_related else _Match(None, False)
        related = True
    if et is not None and ct is None and not identity_related and not run_related:
        return _Match(None, False)
    comparable = (er is not None and cr is not None) or (et is not None and ct is not None) or identity_related
    return _Match(True, related) if comparable and related else _Match(None, related)


def _match_response(expected: Mapping[str, Any], candidate: Mapping[str, Any], turn_map: Mapping[str, str]) -> _Match:
    ef = _mapping(expected.get("fields"), "observer response fields")
    related = False
    expected_id = expected.get("id")
    identity_related = _native_id(candidate) == expected_id
    if identity_related:
        related = True
    expected_turn = ef.get("turn_id")
    native_turn = _turn_id(candidate)
    turn_matches = native_turn == expected_turn or (native_turn is not None and turn_map.get(native_turn) == expected_turn)
    turn_mismatch = expected_turn is not None and native_turn is not None and not turn_matches
    if turn_matches:
        related = True
    # Generated response prose is deliberately not an answer key.  The
    # observer's supplied per-run canary, role, status, and turn are the
    # semantic identity fields; differing prose remains a comparable response.
    ct = _semantic_text(_field(candidate, "text", "content"))
    ecanary = _semantic_text(ef.get("canary", ef.get("response_canary")))
    ccanary = _text(_field(candidate, "canary", "response_canary")) or _embedded_canary(ct)
    if ecanary is not None:
        if ccanary is not None:
            related = True
            if ecanary != ccanary:
                if turn_mismatch and not identity_related:
                    return _Match(None, False)
                return _Match(False, True) if identity_related else _Match(None, False)
        else:
            return _Match(None, related)
    # A response canary is a strong per-run identity, but it cannot authorize
    # attaching that response to a different explicit turn.  If the native
    # decoder supplies a turn id and it cannot be mapped, leave the candidate
    # unresolved (or expose an explicit contradiction when its identity is
    # otherwise exact).
    if expected_turn is not None and native_turn is not None and not turn_matches:
        if identity_related or (ecanary is not None and ccanary == ecanary):
            return _Match(False, True)
        return _Match(None, False)
    erole = ef.get("role")
    crole = _native_role(candidate)
    if erole is not None and crole is not None:
        related = True
        if erole != crole:
            return _Match(False, True) if identity_related or (ecanary is not None and ccanary == ecanary) else _Match(None, False)
    status = ef.get("status")
    cstatus = _field(candidate, "status")
    if status is not None and cstatus is not None:
        related = True
        if status != cstatus:
            return _Match(False, True) if identity_related or (ecanary is not None and ccanary == ecanary) else _Match(None, False)
    if ecanary is not None and ccanary is None and (ct is None or "<survival-canary>" not in ct):
        return _Match(None, related)
    return _Match(True, related) if related else _Match(None, False)


def _action_kind(value: Mapping[str, Any]) -> str | None:
    raw = _field(value, "action_kind", "tool_name", "tool", "name")
    if not isinstance(raw, str):
        raw = _field(value, "kind")
    if not isinstance(raw, str):
        return None
    lowered = raw.lower()
    if lowered in {"write", "edit", "file_edit", "apply_patch", "patch"}:
        return "edit"
    if lowered in {"shell", "bash", "exec", "command", "run", "terminal"}:
        argv = _argv(value)
        if argv:
            if "inspect" in argv:
                return "inspect"
            if "baseline" in argv:
                return "test"
            if "final" in argv:
                return "test"
    return raw.lower()


def _match_action(expected: Mapping[str, Any], candidate: Mapping[str, Any], turn_map: Mapping[str, str]) -> _Match:
    ef = _mapping(expected.get("fields"), "observer action fields")
    related = False
    # A decoder commonly emits more than one action for a turn (for example,
    # an inspection before the workload command).  Turn membership is useful
    # evidence for matching, but it is not an identity claim for a particular
    # observer action.  Reserve contradiction handling for an explicit action
    # id/expected-id, otherwise an unrelated same-turn action would be treated
    # as proof that the observer action was decoded incorrectly.
    identity_related = False
    expected_native_id = ef.get("native_action_id")
    candidate_native_id = _native_id(candidate)
    expected_call_id = ef.get("call_id")
    candidate_call_id = _field(candidate, "call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId")
    if isinstance(expected_native_id, str):
        if candidate_native_id != expected_native_id:
            return _Match(None, False)
        related = identity_related = True
    if isinstance(expected_call_id, str):
        if candidate_call_id != expected_call_id:
            return _Match(False, True) if identity_related else _Match(None, False)
        related = identity_related = True
    if _native_id(candidate) == expected.get("id") or _field(candidate, "expected_id", "expected_action_id") == expected.get("id"):
        related = identity_related = True
    expected_turn = ef.get("turn_id")
    native_turn = _turn_id(candidate)
    if native_turn == expected_turn or (native_turn is not None and turn_map.get(native_turn) == expected_turn):
        related = True
    elif expected_turn is not None and native_turn is not None:
        # A same-command action on another explicit turn is not evidence for
        # this observer action.  An exact id still provides enough identity to
        # report a contradiction; otherwise keep the candidate out of the
        # matching pool so unrelated work cannot poison the row.
        if identity_related:
            return _Match(False, True)
        return _Match(None, False)
    expected_argv = ef.get("argv")
    native_argv = _argv(candidate)
    if isinstance(expected_argv, list) and native_argv is not None:
        if not _argv_semantically_equal(expected_argv, native_argv):
            return _Match(False, True) if identity_related else _Match(None, False)
        related = True
    expected_target = ef.get("target")
    native_target = _target(candidate)
    if expected_target is not None and native_target is not None:
        if not _same_path(expected_target, native_target):
            return _Match(False, True) if identity_related else _Match(None, False)
        related = True
    expected_cwd = ef.get("cwd")
    native_cwd = _cwd(candidate)
    if expected_cwd is not None and native_cwd is not None:
        if not _same_path(expected_cwd, native_cwd):
            return _Match(False, True) if identity_related else _Match(None, False)
        related = True
    expected_kind = ef.get("action_kind")
    native_kind = _action_kind(candidate)
    # A bare native tool name such as ``bash`` is not an identity.  Compare
    # its derived action kind only after another semantic field has related the
    # candidate to this observer action.
    if expected_kind is not None and native_kind is not None and (related or identity_related):
        if expected_kind != native_kind:
            return _Match(False, True) if identity_related else _Match(None, False)
    if isinstance(expected_argv, list) and native_argv is None:
        return _Match(None, related)
    if expected_target is not None and native_target is None:
        return _Match(None, related)
    if expected_turn is not None and native_turn is None and not related:
        return _Match(None, related)
    return _Match(True, related) if related else _Match(None, False)


def _match_many(expected: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matcher: Callable[[Mapping[str, Any], Mapping[str, Any]], _Match]) -> tuple[int, bool, dict[str, Mapping[str, Any]], set[int]]:
    used: set[int] = set()
    matched: dict[str, Mapping[str, Any]] = {}
    conflicts = False
    for item in expected:
        best: int | None = None
        saw_related_conflict = False
        for index, candidate in enumerate(candidates):
            if index in used:
                continue
            result = matcher(item, candidate)
            if result.value is True:
                best = index
                break
            if result.value is False and result.related:
                saw_related_conflict = True
        if best is not None:
            used.add(best)
            item_id = item.get("id")
            if isinstance(item_id, str):
                matched[item_id] = candidates[best]
        elif saw_related_conflict:
            conflicts = True
    return len(matched), conflicts, matched, used


def _row(metric_id: str, observed: int, decoded: int, correct: int, *, complete: bool, supported: bool, conflicts: bool = False) -> dict[str, Any]:
    if observed == 0:
        state = "unresolved"
    elif not supported:
        state = "decoder_unsupported"
    elif correct:
        state = "measured"
    elif decoded == 0 and complete and supported:
        state = "native_absent"
    elif conflicts:
        state = "contradiction"
    else:
        state = "unresolved"
    return {"id": metric_id, "state": state, "correct": correct, "observed_eligible": observed, "decoded_eligible": decoded}


def _match_relation(expected: Mapping[str, Any], candidate: Mapping[str, Any], *, action_map: Mapping[str, str], result_map: Mapping[str, str], turn_map: Mapping[str, str], response_map: Mapping[str, str]) -> _Match:
    from_id = _field(candidate, "from_id", "action_id", "turn_id", "parent_id")
    to_id = _field(candidate, "to_id", "result_id", "response_id")
    relation_kind = _field(candidate, "kind", "relation_kind")
    if to_id is None and relation_kind == "turn_response":
        to_id = _native_id(candidate)
    if to_id is None and relation_kind == "action_result":
        to_id = _native_id(candidate)
    expected_from = expected.get("from_id")
    expected_to = expected.get("to_id")
    def resolve(raw: Any, mapping: Mapping[str, str], direct: str) -> str | None:
        if raw == direct:
            return direct
        if isinstance(raw, str):
            return mapping.get(raw)
        return None
    direct_from = resolve(from_id, action_map | turn_map, expected_from)
    # ``final_after`` points at the downstream action, while
    # ``action_result`` points at a result.  Resolve both target classes so a
    # native relation can bind either frozen relation without being reported
    # as a contradiction merely because its action ID is vendor-native.
    if relation_kind == "final_after":
        # A native action and its result may share the same call ID.  The
        # frozen final_after assertion names the downstream action, so prefer
        # that map before considering a result or response alias.
        direct_to = resolve(to_id, action_map, expected_to)
        if direct_to is None:
            direct_to = resolve(to_id, result_map | response_map, expected_to)
    else:
        direct_to = resolve(to_id, result_map | response_map | action_map, expected_to)
    related = direct_from is not None or direct_to is not None or from_id == expected_from or to_id == expected_to
    if direct_from == expected_from and direct_to == expected_to:
        return _Match(True, True)
    return _Match(False, True) if related else _Match(None, False)


def _usage_shape(value: Mapping[str, Any]) -> tuple[bool, bool]:
    usage = _field(value, "usage", "tokens")
    if not isinstance(usage, Mapping):
        # Some decoders expose usage fields directly on the fact.
        usage = value
    aliases = {
        "input": ("input_tokens", "input"),
        "output": ("output_tokens", "output"),
        "cache_read": ("cache_read_tokens", "cached_input_tokens", "cache_read"),
        "cache_write": ("cache_write_tokens", "cache_write_input_tokens", "cache_write"),
    }
    present = True
    cache_split = False
    for semantic, keys in aliases.items():
        found = next((usage.get(key) for key in keys if key in usage), None)
        if found is None and semantic.startswith("cache"):
            cache = usage.get("cache")
            if isinstance(cache, Mapping):
                found = cache.get("read" if semantic == "cache_read" else "write")
                cache_split = True
        if not isinstance(found, int) or isinstance(found, bool) or found < 0:
            present = False
        if semantic in {"cache_read", "cache_write"} and any(key in usage for key in keys):
            cache_split = True
    return present, cache_split


def _match_usage(expected: Mapping[str, Any], candidate: Mapping[str, Any], turn_map: Mapping[str, str], response_map: Mapping[str, str]) -> _Match:
    ef = _mapping(expected.get("fields"), "observer response fields")
    usage_id = ef.get("usage_id")
    candidate_id = _field(candidate, "usage_id", "id", "response_id")
    expected_turn = ef.get("turn_id")
    native_turn = _turn_id(candidate)
    related = candidate_id == usage_id or (native_turn is not None and turn_map.get(native_turn) == expected_turn) or native_turn == expected_turn
    response_id = _field(candidate, "response_id", "message_id")
    if isinstance(response_id, str) and response_map.get(response_id) == expected.get("id"):
        related = True
    complete, _ = _usage_shape(candidate)
    if not complete:
        return _Match(None, related)
    expected_usage = ef.get("usage")
    candidate_usage = _field(candidate, "usage", "tokens")
    if not isinstance(candidate_usage, Mapping):
        return _Match(None, related)
    # Usage accuracy is outside this format benchmark.  A surface observer may
    # prove the response boundary without independently exposing token values
    # (Claude Desktop is one such surface).  In that case a complete native
    # usage object with a stable response/turn join is enough to measure
    # retention and named token semantics.  When the observer does expose
    # values, keep the stricter exact comparison below.
    if not isinstance(expected_usage, Mapping):
        return _Match(True, True) if related else _Match(None, False)
    aliases = {
        "input_tokens": ("input_tokens", "input"),
        "output_tokens": ("output_tokens", "output"),
        "reasoning_tokens": ("reasoning_tokens", "reasoning"),
        "cache_read_tokens": ("cache_read_tokens", "cached_input_tokens", "cache_read"),
        "cache_write_tokens": ("cache_write_tokens", "cache_write_input_tokens", "cache_write"),
    }
    for expected_key, candidate_keys in aliases.items():
        if expected_key not in expected_usage:
            continue
        expected_value = expected_usage[expected_key]
        candidate_value = next(
            (candidate_usage[key] for key in candidate_keys if key in candidate_usage),
            None,
        )
        if candidate_value is None and expected_key.startswith("cache_"):
            cache = candidate_usage.get("cache")
            if isinstance(cache, Mapping):
                candidate_value = cache.get("read" if expected_key == "cache_read_tokens" else "write")
        if candidate_value is None:
            return _Match(None, related)
        if candidate_value != expected_value:
            return _Match(False, True) if related else _Match(None, False)
    return _Match(True, related) if related else _Match(None, False)


def _metric_match(expected: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matcher: Callable[[Mapping[str, Any], Mapping[str, Any]], _Match], *, complete: bool, supported: bool, metric_id: str) -> dict[str, Any]:
    correct, conflicts, _matched, _used = _match_many(expected, candidates, matcher)
    return _row(metric_id, len(expected), len(candidates), correct, complete=complete, supported=supported, conflicts=conflicts)


def _portable_rows(portable: _Portable, *, complete: bool, supported: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric_id, value in (
        ("portable.complete_root", portable.complete_root),
        ("portable.companions", portable.companions),
        ("portable.isolated_decode", portable.isolated_decode),
        ("portable.canonical_equality", portable.canonical_equality),
    ):
        if value is None:
            rows.append({"id": metric_id, "state": "unresolved", "correct": 0, "observed_eligible": 1, "decoded_eligible": 0})
        elif not supported:
            rows.append(_row(metric_id, 1, 0, 0, complete=False, supported=False))
        elif value:
            rows.append(_row(metric_id, 1, 1, 1, complete=complete, supported=True))
        else:
            # A false receipt is an explicit observed failure.  Keep it
            # resolved without pretending it is a missing native record.
            rows.append({"id": metric_id, "state": "contradiction", "correct": 0, "observed_eligible": 1, "decoded_eligible": 1})
    return rows


def compare_survival_run(
    observer: Mapping[str, Any],
    native: Mapping[str, Any],
    portability_receipt: Mapping[str, Any],
    *,
    configuration_id: str | None = None,
    repetition: int | None = None,
) -> dict[str, Any]:
    """Build one canonical survival-v1 input from explicit offline evidence.

    ``configuration_id`` and ``repetition`` identify the evaluated run and
    are required unless the caller has put the same fields on a constructed
    observer object.  The public observer schema has no configuration field,
    so normal callers should pass both explicitly.
    """

    run_id, observer_events, observer_relations = _validate_observer(observer)
    native_value = _mapping(native, "native decoder result")
    portability = _portable(portability_receipt)
    configuration_id = configuration_id if configuration_id is not None else observer.get("configuration_id") if isinstance(observer, Mapping) else None
    repetition = repetition if repetition is not None else observer.get("repetition") if isinstance(observer, Mapping) else None
    _nonempty(configuration_id, "configuration_id")
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 1:
        raise ComparatorError("repetition must be a positive integer")

    # A decoder label, aggregate facts object, or projected metric list does
    # not prove that the native search reached its end.  The boundary must be
    # an explicit record stream (including an intentionally empty stream), or
    # an explicit boundary flag supplied by the decoder.
    native_boundary_declared = (
        any(isinstance(native_value.get(key), list) for key in ("events", "records", "turns", "responses", "actions", "results", "file_changes"))
        or native_value.get("complete") is True
        or native_value.get("boundary_complete") is True
        or native_value.get("complete_boundary") is True
    )
    supported = _native_supported(native_value) and portability.supported is not False
    diagnostics = native_value.get("diagnostics")
    no_diagnostics = diagnostics in (None, [])
    complete = native_boundary_declared and supported and no_diagnostics and portability.complete_root is True

    turns = _observer_events(observer_events, "user_turn")
    responses = _observer_events(observer_events, "assistant_response")
    actions = _observer_events(observer_events, "action")
    results = _observer_events(observer_events, "result")
    changes = _observer_events(observer_events, "file_change")

    native_turns = _native_turns(native_value)
    native_responses = _native_responses(native_value)
    native_actions = _native_actions(native_value)
    native_results = _native_results(native_value)
    native_changes = _native_changes(native_value)
    native_action_relations = _native_relations(native_value, "action_result")
    native_turn_relations = _native_relations(native_value, "turn_response")

    turn_correct, turn_conflicts, turn_map_raw, _ = _match_many(turns, native_turns, _match_turn)
    turn_map: dict[str, str] = {}
    for expected_id, candidate in turn_map_raw.items():
        candidate_id = _native_id(candidate)
        if candidate_id:
            turn_map[candidate_id] = expected_id
        native_turn = _turn_id(candidate)
        if native_turn:
            turn_map[native_turn] = expected_id
    response_matcher = lambda expected, candidate: _match_response(expected, candidate, turn_map)
    response_correct, response_conflicts, response_map_raw, _ = _match_many(responses, native_responses, response_matcher)
    response_map: dict[str, str] = {}
    for expected_id, candidate in response_map_raw.items():
        candidate_id = _native_id(candidate)
        if candidate_id:
            response_map[candidate_id] = expected_id

    def frozen_identity_candidates(
        expected: Sequence[Mapping[str, Any]],
        candidates: Sequence[Mapping[str, Any]],
        native_id_field: str,
    ) -> list[Mapping[str, Any]]:
        native_ids: set[str] = set()
        call_ids: set[str] = set()
        for item in expected:
            fields = _mapping(item.get("fields"), "observer identity fields")
            native_id = fields.get(native_id_field)
            call_id = fields.get("call_id")
            if isinstance(native_id, str):
                native_ids.add(native_id)
            if isinstance(call_id, str):
                call_ids.add(call_id)
        if not native_ids and not call_ids:
            return list(candidates)
        return [
            item
            for item in candidates
            if _native_id(item) in native_ids
            or _field(item, "call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId") in call_ids
        ]

    scored_native_actions = frozen_identity_candidates(
        actions, native_actions, "native_action_id"
    )
    action_matcher = lambda expected, candidate: _match_action(expected, candidate, turn_map)
    action_correct, action_conflicts, action_map_raw, _ = _match_many(actions, scored_native_actions, action_matcher)
    action_map: dict[str, str] = {}
    for expected in actions:
        fields = _mapping(expected.get("fields"), "observer action fields")
        expected_id = expected.get("id")
        if not isinstance(expected_id, str):
            continue
        for raw in (fields.get("native_action_id"), fields.get("call_id")):
            if isinstance(raw, str):
                action_map[raw] = expected_id
    for expected_id, candidate in action_map_raw.items():
        candidate_id = _native_id(candidate)
        if candidate_id:
            action_map[candidate_id] = expected_id
        call_id = _field(candidate, "call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId")
        if isinstance(call_id, str):
            action_map[call_id] = expected_id

    def result_matcher(expected: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Match:
        ef = _mapping(expected.get("fields"), "observer result fields")
        identity_related = _native_id(candidate) == expected.get("id")
        expected_native_result = ef.get("native_result_id")
        if isinstance(expected_native_result, str):
            if _native_id(candidate) != expected_native_result:
                return _Match(None, False)
            identity_related = True
        expected_call = ef.get("call_id")
        native_call = _field(candidate, "call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId")
        if isinstance(expected_call, str):
            if native_call != expected_call:
                return _Match(False, True) if identity_related else _Match(None, False)
            identity_related = True
        expected_action = ef.get("action_id")
        raw_action = _field(candidate, "action_id", "expected_action_id", "expected_id")
        raw_call = _field(candidate, "call_id", "callID", "tool_call_id", "tool_use_id", "toolUseId")
        mapped_action = action_map.get(raw_action) if isinstance(raw_action, str) else None
        mapped_call = action_map.get(raw_call) if isinstance(raw_call, str) else None
        related = identity_related or raw_action == expected_action or mapped_action == expected_action or mapped_call == expected_action
        if not related:
            return _Match(None, False)
        status = ef.get("status")
        native_status = _field(candidate, "status")
        if status is not None and native_status is not None:
            related = True
            if status != native_status:
                return _Match(False, True)
        exit_code = ef.get("exit_code")
        native_exit = _field(candidate, "exit_code")
        if exit_code is not None and native_exit is not None:
            related = True
            if exit_code != native_exit:
                return _Match(False, True)
        helper = ef.get("helper_nonce")
        native_helper = _field(candidate, "helper_nonce")
        if helper is not None:
            if native_helper is None:
                return _Match(None, related)
            related = True
            if helper != native_helper:
                return _Match(False, True)
        expected_output = ef.get("output")
        native_output = _field(candidate, "output", "text", "content")
        if isinstance(expected_output, str):
            if not isinstance(native_output, str):
                return _Match(None, related)
            if expected_output != native_output:
                return _Match(False, True)
        if not related:
            return _Match(None, False)
        if status is not None and native_status is None:
            return _Match(None, True)
        if exit_code is not None and native_exit is None:
            return _Match(None, True)
        return _Match(True, True)

    scored_native_results = frozen_identity_candidates(
        results, native_results, "native_result_id"
    )
    result_correct, result_conflicts, result_map_raw, _ = _match_many(results, scored_native_results, result_matcher)
    result_map: dict[str, str] = {}
    for expected in results:
        fields = _mapping(expected.get("fields"), "observer result fields")
        expected_id = expected.get("id")
        if not isinstance(expected_id, str):
            continue
        for raw in (fields.get("native_result_id"), fields.get("call_id")):
            if isinstance(raw, str):
                result_map[raw] = expected_id
    for expected_id, candidate in result_map_raw.items():
        candidate_id = _native_id(candidate)
        if candidate_id:
            result_map[candidate_id] = expected_id

    def change_matcher(expected: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Match:
        ef = _mapping(expected.get("fields"), "observer file-change fields")
        expected_path = ef.get("path")
        native_path = _field(candidate, "path", "target", "file_path")
        identity_related = _native_id(candidate) == expected.get("id")
        related = _same_path(expected_path, native_path)
        if expected_path is not None and native_path is not None and not related:
            return _Match(False, True) if identity_related else _Match(None, False)
        for key in ("before_sha256", "after_sha256"):
            expected_value = ef.get(key)
            native_value_for_key = _field(candidate, key)
            if expected_value is not None and native_value_for_key is not None:
                related = True
                if expected_value != native_value_for_key:
                    return _Match(False, True)
            elif expected_value is not None:
                return _Match(None, related)
        return _Match(True, True) if related and native_path is not None else _Match(None, related)

    change_correct, change_conflicts, _change_map, _ = _match_many(changes, native_changes, change_matcher)

    # A complete native boundary can contain an edit record while omitting the
    # frozen assertion we score: exact whole-file before/after digests.  Treat
    # that as native absence of the assertion, rather than leaving the metric
    # unresolved merely because a weaker fragment record exists.
    change_hash_assertion_absent = bool(changes) and complete and all(
        any(
            _same_path(
                _mapping(expected.get("fields"), "observer file-change fields").get("path"),
                _field(candidate, "path", "target", "file_path"),
            )
            for candidate in native_changes
        )
        and not any(
            _same_path(
                _mapping(expected.get("fields"), "observer file-change fields").get("path"),
                _field(candidate, "path", "target", "file_path"),
            )
            and isinstance(_field(candidate, "before_sha256"), str)
            and isinstance(_field(candidate, "after_sha256"), str)
            for candidate in native_changes
        )
        for expected in changes
    )

    rows: dict[str, dict[str, Any]] = {}
    rows["work.submitted_turns"] = _row("work.submitted_turns", len(turns), len(native_turns), turn_correct, complete=complete, supported=supported, conflicts=turn_conflicts)
    rows["work.visible_responses"] = _row("work.visible_responses", len(responses), len(native_responses), response_correct, complete=complete, supported=supported, conflicts=response_conflicts)
    rows["work.actions"] = _row("work.actions", len(actions), len(scored_native_actions), action_correct, complete=complete, supported=supported, conflicts=action_conflicts)
    rows["work.results"] = _row("work.results", len(results), len(scored_native_results), result_correct, complete=complete, supported=supported, conflicts=result_conflicts)
    rows["work.changed_files"] = _row("work.changed_files", len(changes), len(native_changes), change_correct, complete=complete, supported=supported, conflicts=change_conflicts)
    if change_hash_assertion_absent and supported:
        rows["work.changed_files"]["state"] = "native_absent"

    action_relations = _observer_relations(observer_relations, "action_result")
    turn_relations = _observer_relations(observer_relations, "turn_response")
    frozen_action_ids = set(action_map)
    frozen_result_ids = set(result_map)
    scored_native_action_relations = [
        relation
        for relation in native_action_relations
        if _field(relation, "from_id", "action_id") in frozen_action_ids
        and _field(relation, "to_id", "result_id") in frozen_result_ids
    ] if frozen_action_ids and frozen_result_ids else native_action_relations
    rows["causal.action_result"] = _metric_match(action_relations, scored_native_action_relations, lambda expected, candidate: _match_relation(expected, candidate, action_map=action_map, result_map=result_map, turn_map={}, response_map={}), complete=complete, supported=supported, metric_id="causal.action_result")
    rows["causal.turn_response"] = _metric_match(turn_relations, native_turn_relations, lambda expected, candidate: _match_relation(expected, candidate, action_map={}, result_map={}, turn_map=turn_map, response_map=response_map), complete=complete, supported=supported, metric_id="causal.turn_response")

    # Revisions are measured from the exact observer turns and their matched
    # native semantic records.  Order uses native order metadata, never a
    # response/action count.
    for metric_id, revision in (("revision.r1", "r1"), ("revision.r2", "r2")):
        expected = [item for item in turns if _mapping(item.get("fields"), "observer turn fields").get("revision") == revision]
        candidates = [item for item in native_turns if _revision(item) == revision]
        rows[metric_id] = _metric_match(expected, candidates, _match_turn, complete=complete, supported=supported, metric_id=metric_id)
    r1 = next((item for item in turns if _mapping(item.get("fields"), "observer turn fields").get("revision") == "r1"), None)
    r2 = next((item for item in turns if _mapping(item.get("fields"), "observer turn fields").get("revision") == "r2"), None)
    order_expected = _observer_relations(observer_relations, "supersedes")
    order_candidates = [item for item in native_turns if _revision(item) in {"r1", "r2"}]
    order_correct = 0
    order_conflict = False
    if r1 is not None and r2 is not None and len(order_candidates) >= 2:
        r1_candidates = [item for item in order_candidates if _revision(item) == "r1"]
        r2_candidates = [item for item in order_candidates if _revision(item) == "r2"]
        if len(r1_candidates) == 1 and len(r2_candidates) == 1:
            order_correct = int(_order_key(r1_candidates[0], 0) < _order_key(r2_candidates[0], 1))
            if not order_correct:
                order_conflict = True
    rows["revision.r1_r2_order"] = _row("revision.r1_r2_order", len(order_expected), len(order_candidates) and 1 or 0, order_correct, complete=complete, supported=supported, conflicts=order_conflict)

    final_relations = _observer_relations(observer_relations, "final_after")
    facts = native_value.get("facts")
    # A response timestamp after R2 is not the frozen assertion. Accept only
    # an explicit native chain covering the downstream edit, result, and final
    # response. The OpenCode decoder currently emits no such chain.
    final_candidates = _native_relations(native_value, "final_after")
    final_correct, final_conflict, _final_map, _ = _match_many(
        final_relations,
        final_candidates,
        lambda expected, candidate: _match_relation(
            expected,
            candidate,
            action_map=action_map,
            result_map=result_map,
            turn_map=turn_map,
            response_map=response_map,
        ),
    )
    final_decoded = len(final_candidates)
    rows["revision.final_after_r2"] = _row("revision.final_after_r2", len(final_relations), final_decoded, final_correct, complete=complete, supported=supported, conflicts=final_conflict)

    # Attribution rows are response-scoped.  The native result must retain the
    # relevant fields on each response/usage fact; model/session aggregates are
    # intentionally insufficient.
    def model_match(expected: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Match:
        ef = _mapping(expected.get("fields"), "observer response fields")
        model = _field(candidate, "model_id", "model")
        config = _field(candidate, "configuration", "config", "configuration_id")
        related = _match_response(expected, candidate, turn_map).related
        if model is None or config is None:
            return _Match(None, related)
        expected_model = ef.get("model_id")
        expected_config = ef.get("configuration")
        if expected_model is None or expected_config is None:
            return _Match(None, related)
        if related and (model != expected_model or config != expected_config):
            return _Match(False, True)
        return _Match(True, True) if related else _Match(None, False)

    rows["attribution.model_config"] = _metric_match(responses, native_responses, model_match, complete=complete, supported=supported, metric_id="attribution.model_config")
    usage_candidates = _native_usage(native_value)
    usage_matcher = lambda expected, candidate: _match_usage(expected, candidate, turn_map, response_map)
    rows["attribution.usage"] = _metric_match(responses, usage_candidates, usage_matcher, complete=complete, supported=supported, metric_id="attribution.usage")
    rows["attribution.token_semantics"] = _metric_match(
        responses,
        [item for item in usage_candidates if (_usage_shape(item)[0] and _usage_shape(item)[1]) or item.get("usage_opaque") is True],
        usage_matcher,
        complete=complete,
        supported=supported,
        metric_id="attribution.token_semantics",
    )
    # Token usage is an optional provider field.  A complete transcript whose
    # native format does not expose response-scoped usage is ambiguous rather
    # than proof that usage was absent.  Keep these rows unresolved so a
    # stronger root boundary cannot turn an unrecorded optional field into a
    # falsely precise ``native_absent`` result.
    if not usage_candidates:
        for metric_id in ("attribution.usage", "attribution.token_semantics"):
            rows[metric_id] = {
                "id": metric_id,
                "state": "unresolved",
                "correct": 0,
                "observed_eligible": len(responses),
                "decoded_eligible": 0,
            }
    usage_total = [item for item in observer_events if item.get("kind") == "usage_total"]
    reconciliation_expected = usage_total or [item for item in observer_relations if item.get("kind") == "helper_for" and False]
    reconciliation_candidates: list[Mapping[str, Any]] = []
    if isinstance(facts, Mapping):
        reconciliation = facts.get("reconciliation")
        if isinstance(reconciliation, Mapping) and isinstance(reconciliation.get("matches_session_totals"), bool):
            candidate = dict(reconciliation)
            native_usage_summary = facts.get("usage")
            if isinstance(native_usage_summary, Mapping) and isinstance(native_usage_summary.get("session_totals"), Mapping):
                candidate["session_totals"] = dict(native_usage_summary["session_totals"])
            reconciliation_candidates.append(candidate)
        native_usage_facts = facts.get("usage")
        if isinstance(native_usage_facts, list) and native_usage_facts and all(isinstance(item, Mapping) and item.get("reconciles") is True for item in native_usage_facts):
            reconciliation_candidates.append({"reconciles": True})
    direct_reconciliation = [item for item in native_value.get("reconciliation", []) if isinstance(item, Mapping)] if isinstance(native_value.get("reconciliation"), list) else []
    reconciliation_candidates.extend(direct_reconciliation)
    def reconciliation_match(expected: Mapping[str, Any], candidate: Mapping[str, Any]) -> _Match:
        expected_fields = _mapping(expected.get("fields"), "observer usage total fields")
        if candidate.get("matches_session_totals") is not True:
            return _Match(False, True) if candidate.get("matches_session_totals") is False else _Match(None, False)
        totals = candidate.get("session_totals")
        if not isinstance(totals, Mapping):
            return _Match(None, True)
        aliases = {
            "input_tokens": "input",
            "output_tokens": "output",
            "reasoning_tokens": "reasoning",
            "cache_read_tokens": "cache_read",
            "cache_write_tokens": "cache_write",
        }
        for expected_key, native_key in aliases.items():
            if expected_key not in expected_fields:
                continue
            expected_value = expected_fields.get(expected_key)
            native_value = totals.get(native_key, totals.get(expected_key))
            if not isinstance(expected_value, int) or isinstance(expected_value, bool):
                return _Match(None, True)
            if native_value != expected_value:
                return _Match(False, True)
        return _Match(True, True)

    reconciliation_correct, reconciliation_conflict, _reconciliation_map, _ = _match_many(
        reconciliation_expected, reconciliation_candidates, reconciliation_match
    )
    rows["attribution.reconciliation"] = _row(
        "attribution.reconciliation",
        len(reconciliation_expected),
        len(reconciliation_candidates),
        reconciliation_correct,
        complete=complete,
        supported=supported,
        conflicts=reconciliation_conflict,
    )

    rows.update({row["id"]: row for row in _portable_rows(portability, complete=complete, supported=supported)})
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
        "metrics": [rows[metric_id] for metric_id in _METRIC_IDS],
    }
    try:
        return validate_input(result)
    except ValueError as exc:
        raise ComparatorError(f"comparator emitted invalid survival input: {exc}") from exc


# Descriptive aliases make the seam discoverable without creating another
# implementation entry point.
build_survival_input = compare_survival_run
compare_observer_to_native = compare_survival_run
compare_live_metrics = compare_survival_run
build_canonical_input = compare_survival_run
canonical_survival_input = compare_survival_run


__all__ = [
    "ComparatorError",
    "OBSERVER_SCHEMA_VERSION",
    "PROTOCOL_VERSION",
    "SCENARIO_ID",
    "build_survival_input",
    "build_canonical_input",
    "canonical_survival_input",
    "compare_live_metrics",
    "compare_observer_to_native",
    "compare_survival_run",
]
