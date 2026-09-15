"""Independent live observer for one captured OpenCode survival-v1 attempt.

The builder creates observer evidence only. It never reads native SQLite,
decodes, scores, qualifies, publishes, or makes product claims. It consumes
only the supplied in-memory mappings/strings and the two filesystem hash
values; it never opens files.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Mapping

SCHEMA_VERSION = "1.0-survival-observer"
PROTOCOL_VERSION = "1.0-survival"
SCENARIO_ID = "survival-v1-repair"

_RUN_CANARY = re.compile(r"^SB_SURVIVAL_V1_RUN_[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HELPER_OUTPUT = re.compile(
    r"^SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)",
    re.MULTILINE,
)
_PATCH_FILE = re.compile(
    r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+?)\s*$", re.MULTILINE
)

_SESSION_KEYS = ("sessionID", "session_id", "sessionId", "session")
_TOOL_ROW_TYPES = frozenset(
    {"tool_use", "tool_call", "tool", "tool-invocation", "tool_invocation"}
)
_STEP_FINISH_TYPES = frozenset(
    {"step_finish", "step-finish", "stepfinish"}
)
_EDIT_TOOLS = frozenset(
    {"edit", "write", "apply_patch", "patch", "edit_file", "write_file"}
)

_LEGACY_PATH_KEYS = frozenset(
    {"stdout_path", "helper_ledger_path", "ledger_path", "artifact_path"}
)

_METHOD = (
    "independent live observer from submitted input, live OpenCode JSON "
    "stream, helper ledger, filesystem hashes, and usage trace; no native "
    "SQLite decode, scoring, qualification, or publication"
)


class LiveObserverError(ValueError):
    """The supplied live evidence cannot be observed fail-closed."""


def _fail(message: str) -> LiveObserverError:
    return LiveObserverError(message)


def _nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{label} must be a non-empty string")
    return value


def _strict_json_loads(text: str, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number {value!r}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _fail(f"{label} is not strict JSON: {exc}") from exc


def _validate_workload(workload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(workload, Mapping):
        raise _fail("workload must be an object")
    if workload.get("schema_version") != "1.0-survival-workload":
        raise _fail("workload schema_version must be '1.0-survival-workload'")
    if workload.get("protocol_version") != PROTOCOL_VERSION:
        raise _fail("workload protocol_version must be '1.0-survival'")
    if workload.get("scenario_id") != SCENARIO_ID:
        raise _fail("workload scenario_id must be 'survival-v1-repair'")
    run_id = _nonempty_str(workload.get("run_id"), "workload.run_id")
    run_canary = _nonempty_str(workload.get("run_canary"), "workload.run_canary")
    if not _RUN_CANARY.fullmatch(run_canary):
        raise _fail("workload.run_canary is invalid")
    turns = workload.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise _fail("workload.turns must contain exactly two turns")
    ordered = sorted(
        turns,
        key=lambda item: item.get("sequence", 0)
        if isinstance(item, Mapping)
        else 0,
    )
    seen_ids: set[str] = set()
    revisions: list[str] = []
    for index, turn in enumerate(ordered):
        label = f"workload.turns[{index}]"
        if not isinstance(turn, Mapping):
            raise _fail(f"{label} must be an object")
        turn_id = _nonempty_str(turn.get("id"), f"{label}.id")
        if turn_id in seen_ids:
            raise _fail(f"duplicate workload turn id: {turn_id}")
        seen_ids.add(turn_id)
        revision = turn.get("revision")
        if revision not in ("r1", "r2"):
            raise _fail(f"{label}.revision must be 'r1' or 'r2'")
        revisions.append(revision)
        text = _nonempty_str(turn.get("text"), f"{label}.text")
        response_canary = _nonempty_str(
            turn.get("response_canary"), f"{label}.response_canary"
        )
        if response_canary in seen_ids:
            raise _fail(f"duplicate workload canary: {response_canary}")
        seen_ids.add(response_canary)
        if run_canary not in text:
            raise _fail(f"{label}.text must contain the workload run canary")
        bound = turn.get("run_canary")
        if bound is not None and bound != run_canary:
            raise _fail(f"{label}.run_canary must match the workload run canary")
    if sorted(revisions) != ["r1", "r2"]:
        raise _fail("workload.turns must contain exactly R1 and R2")
    canaries = workload.get("response_canaries")
    if not isinstance(canaries, list) or len(canaries) != 2:
        raise _fail("workload.response_canaries must contain exactly two canaries")
    values: set[str] = set()
    for index, entry in enumerate(canaries):
        label = f"workload.response_canaries[{index}]"
        if not isinstance(entry, Mapping):
            raise _fail(f"{label} must be an object")
        value = _nonempty_str(entry.get("value"), f"{label}.value")
        if value in values:
            raise _fail("workload response canaries must be unique")
        values.add(value)
    expected = {
        str(turn.get("response_canary"))
        for turn in ordered
        if isinstance(turn, Mapping)
    }
    if values != expected:
        raise _fail("workload response canaries must match the two turn canaries")
    return {
        "run_id": run_id,
        "run_canary": run_canary,
        "turns": ordered,
        "response_canaries": list(canaries),
    }


def _validate_controller(
    controller_state: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(controller_state, Mapping):
        raise _fail("controller_state must be an object")
    for key in _LEGACY_PATH_KEYS:
        if key in controller_state:
            raise _fail(f"controller_state legacy artifact path is unsupported: {key}")
    turns = controller_state.get("turns")
    if not isinstance(turns, Mapping):
        raise _fail("controller_state.turns must be an object")
    if set(turns.keys()) != {"1", "2"}:
        raise _fail("controller_state.turns must be keyed by strings '1'/'2'")
    sessions: dict[str, str] = {}
    for key in ("1", "2"):
        record = turns[key]
        if not isinstance(record, Mapping):
            raise _fail(f"controller_state.turns[{key!r}] must be an object")
        session_id = record.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise _fail(
                f"controller_state.turns[{key!r}].session_id must be non-empty"
            )
        sessions[key] = session_id.strip()
    if sessions["1"] != sessions["2"]:
        raise _fail("controller R1/R2 continuation session IDs differ")
    model = controller_state.get("model")
    if not isinstance(model, str) or not model.strip():
        raise _fail("controller_state.model must be a non-empty string")
    configuration = controller_state.get("configuration",
                                         controller_state.get("configuration_id"))
    if not isinstance(configuration, str) or not configuration.strip():
        raise _fail(
            "controller_state configuration must be a non-empty string"
        )
    return {
        "session_id": sessions["1"],
        "model": model.strip(),
        "configuration": configuration.strip(),
        "workspace": controller_state.get("workspace"),
        "turns": sessions,
    }


def _workspace_relative(value: str | None, workspace: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if not isinstance(workspace, str) or not workspace or not value.startswith("/"):
        return value
    prefix = workspace.rstrip("/")
    if value == prefix:
        return "."
    if value.startswith(prefix + "/"):
        return value[len(prefix) + 1 :]
    return value


def _normalize_stdout_by_turn(
    stdout_by_turn: Mapping[Any, Any],
) -> dict[int, str]:
    if not isinstance(stdout_by_turn, Mapping):
        raise _fail("stdout_by_turn must be an object")
    normalized: dict[int, str] = {}
    for key, value in stdout_by_turn.items():
        try:
            number = int(str(key), 10)
        except (TypeError, ValueError):
            raise _fail("stdout_by_turn keys must be 1 and 2") from None
        if number not in (1, 2) or number in normalized:
            raise _fail("stdout_by_turn must contain exactly turns 1 and 2")
        if not isinstance(value, str) or not value.strip():
            raise _fail(f"stdout turn {number} must be nonblank JSONL")
        normalized[number] = value
    if set(normalized) != {1, 2}:
        raise _fail("stdout_by_turn must contain exactly turns 1 and 2")
    return normalized


def _collect_session_values(value: Any, found: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _SESSION_KEYS and isinstance(child, str) and child.strip():
                found.append(child.strip())
            _collect_session_values(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_session_values(child, found)


def _parse_stdout_stream(
    raw: str, *, turn: int, session_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = raw.splitlines()
    if not lines:
        raise _fail(f"stdout turn {turn} must be nonblank JSONL")
    for index, line in enumerate(lines):
        if not line.strip():
            raise _fail(f"stdout turn {turn} line {index + 1} is blank")
        row = _strict_json_loads(line, f"stdout turn {turn} line {index + 1}")
        if not isinstance(row, dict):
            raise _fail(f"stdout turn {turn} line {index + 1} must be an object")
        found: list[str] = []
        _collect_session_values(row, found)
        if not found:
            raise _fail(
                f"stdout turn {turn} line {index + 1} is missing session identity"
            )
        if len(set(found)) != 1:
            raise _fail(
                f"stdout turn {turn} line {index + 1} has ambiguous session identity"
            )
        if found[0] != session_id:
            raise _fail(
                f"stdout turn {turn} line {index + 1} session mismatch"
            )
        part = row.get("part")
        if isinstance(part, Mapping):
            payload = dict(part)
            payload.setdefault("sessionID", found[0])
            payload.setdefault("timestamp", row.get("timestamp"))
            rows.append(payload)
        else:
            rows.append(row)
    return rows


def _row_text(row: Mapping[str, Any]) -> str | None:
    for key in ("text", "content"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_tool_use(row: Mapping[str, Any]) -> bool:
    raw_type = row.get("type")
    if isinstance(raw_type, str) and raw_type.lower() in _TOOL_ROW_TYPES:
        return True
    tool = row.get("tool")
    if isinstance(tool, str) and tool.strip():
        return True
    tool_name = row.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        return True
    return False


def _tool_name(row: Mapping[str, Any]) -> str:
    for key in ("tool", "tool_name", "name"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise _fail("stdout tool_use row is missing an explicit tool name")


def _tool_input(row: Mapping[str, Any]) -> Any:
    for key in ("input", "arguments", "args"):
        if key in row:
            return row[key]
    state = row.get("state")
    if isinstance(state, Mapping):
        for key in ("input", "arguments"):
            if key in state:
                return state[key]
    return None


def _call_id(row: Mapping[str, Any], fallback: str) -> str:
    for key in ("callID", "call_id", "tool_call_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    state = row.get("state")
    if isinstance(state, Mapping):
        for key in ("callID", "call_id", "tool_call_id"):
            value = state.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _explicit_command_text(
    row: Mapping[str, Any], raw_input: Any
) -> str | None:
    candidates: list[Any] = []
    if isinstance(raw_input, Mapping):
        for key in ("command", "cmd", "command_line"):
            candidates.append(raw_input.get(key))
    elif isinstance(raw_input, str):
        candidates.append(raw_input)
    for key in ("command", "cmd", "command_line"):
        candidates.append(row.get(key))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _explicit_argv(
    row: Mapping[str, Any], raw_input: Any, command_text: str | None
) -> list[str] | None:
    if isinstance(raw_input, Mapping):
        for key in ("argv", "args"):
            value = raw_input.get(key)
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, str) and item for item in value)
            ):
                return list(value)
    if command_text is not None:
        try:
            parts = shlex.split(command_text)
        except ValueError as exc:
            raise _fail(f"stdout tool command is not shlex-parseable: {exc}") from exc
        if not parts:
            raise _fail("stdout tool command must not be empty")
        return parts
    return None


def _explicit_cwd(row: Mapping[str, Any], raw_input: Any) -> str | None:
    containers: list[Any] = [raw_input, row]
    state = row.get("state")
    if isinstance(state, Mapping):
        containers.append(state)
        metadata = state.get("metadata")
        if isinstance(metadata, Mapping):
            containers.append(metadata)
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("workdir", "cwd", "working_directory", "workingDirectory"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _explicit_target(
    row: Mapping[str, Any], raw_input: Any
) -> str | None:
    containers: list[Any] = [raw_input, row]
    state = row.get("state")
    if isinstance(state, Mapping):
        containers.append(state)
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("filePath", "path", "target", "file_path", "target_path"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("patchText", "patch"):
            value = container.get(key)
            if isinstance(value, str) and value:
                match = _PATCH_FILE.search(value)
                if match and match.group(1).strip():
                    return match.group(1).strip()
    if isinstance(raw_input, str):
        match = _PATCH_FILE.search(raw_input)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return None


def _canonical_action_kind(
    tool: str, command_text: str | None, argv: list[str] | None
) -> str:
    haystack = " ".join([command_text or "", " ".join(argv or [])])
    if "bench_check.py" in haystack and "inspect" in haystack:
        return "inspect"
    if "bench_check.py" in haystack and (
        "baseline" in haystack or "final" in haystack
    ):
        return "test"
    lowered = tool.lower()
    if lowered in _EDIT_TOOLS or lowered in {
        "file_edit",
        "fileedit",
        "apply-patch",
    }:
        return "edit"
    return lowered


def _completed_output(row: Mapping[str, Any]) -> tuple[bool, str | None]:
    state = row.get("state") if isinstance(row.get("state"), Mapping) else {}
    assert isinstance(state, Mapping)
    status_values = [row.get("status"), state.get("status")]
    completed = any(
        isinstance(value, str) and value.lower() == "completed"
        for value in status_values
    )
    if not completed:
        return False, None
    for container in (state, row):
        for key in ("output", "result"):
            value = container.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                return True, value
            return True, json.dumps(value, ensure_ascii=False, sort_keys=True)
    return False, None


def _explicit_exit(state: Mapping[str, Any]) -> int | None:
    metadata = state.get("metadata")
    candidates: list[Any] = []
    if isinstance(metadata, Mapping):
        candidates.append(metadata.get("exit"))
        candidates.append(metadata.get("exit_code"))
        candidates.append(metadata.get("exitCode"))
    candidates.append(state.get("exit"))
    candidates.append(state.get("exit_code"))
    candidates.append(state.get("exitCode"))
    for candidate in candidates:
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int):
            return candidate
    return None


def _helper_nonce_from_output(output: str | None) -> tuple[str | None, str | None]:
    if not isinstance(output, str):
        return None, None
    # Bash wrappers may prepend directory listings or an exit-code line before
    # the helper's own newline-delimited payload.  Join on the canonical helper
    # record rather than requiring it to be byte 0 of the tool output.
    match = _HELPER_OUTPUT.search(output)
    if match is None:
        return None, None
    prefix = match.group(0).split()[0]
    return match.group(1), prefix


def _tokens_from_record(row: Mapping[str, Any]) -> dict[str, int] | None:
    container: Any = None
    for key in ("tokens", "usage"):
        value = row.get(key)
        if isinstance(value, Mapping):
            container = value
            break
    if not isinstance(container, Mapping):
        return None
    aliases = {
        "input": ("input_tokens", "input"),
        "output": ("output_tokens", "output"),
        "reasoning": ("reasoning_tokens", "reasoning"),
        "cache_read": (
            "cache_read_tokens",
            "cached_input_tokens",
            "cache_read",
        ),
        "cache_write": (
            "cache_write_tokens",
            "cache_write_input_tokens",
            "cache_write",
        ),
    }
    resolved: dict[str, int] = {}
    for semantic, keys in aliases.items():
        found: Any = None
        for key in keys:
            if key in container:
                found = container[key]
                break
        if semantic.startswith("cache"):
            cache = container.get("cache")
            if found is None and isinstance(cache, Mapping):
                found = cache.get("read" if semantic == "cache_read" else "write")
        if isinstance(found, bool) or not isinstance(found, int) or found < 0:
            return None
        resolved[semantic] = found
    return resolved


def _is_step_finish_stop(row: Mapping[str, Any]) -> bool:
    raw_type = row.get("type")
    if not isinstance(raw_type, str) or raw_type.lower() not in _STEP_FINISH_TYPES:
        return False
    reason = row.get("reason")
    if not isinstance(reason, str) or reason.lower() != "stop":
        return False
    return True


def _is_step_finish(row: Mapping[str, Any]) -> bool:
    raw_type = row.get("type")
    return isinstance(raw_type, str) and raw_type.lower() in _STEP_FINISH_TYPES


def _validate_hashes(before: str, after: str) -> tuple[str, str]:
    if (
        not isinstance(before, str)
        or not _SHA256.fullmatch(before)
        or not isinstance(after, str)
        or not _SHA256.fullmatch(after)
    ):
        raise _fail("checkout hashes must be lowercase 64-hex SHA-256 digests")
    if before == after:
        raise _fail("checkout before/after hashes must differ")
    return before, after


def _parse_helper_ledger(
    helper_ledger_jsonl: str, *, run_canary: str
) -> list[dict[str, Any]]:
    if not isinstance(helper_ledger_jsonl, str) or not helper_ledger_jsonl.strip():
        raise _fail("helper_ledger_jsonl must be nonblank JSONL")
    lines = helper_ledger_jsonl.splitlines()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            raise _fail(f"helper ledger line {index + 1} is blank")
        row = _strict_json_loads(line, f"helper ledger line {index + 1}")
        if not isinstance(row, dict):
            raise _fail(f"helper ledger line {index + 1} must be an object")
        rows.append(row)
    if len(rows) != 3:
        raise _fail("helper ledger must record exactly inspect, baseline, final")
    phases = [row.get("phase") for row in rows]
    if sorted(phases) != ["baseline", "final", "inspect"]:
        raise _fail("helper ledger must record exactly inspect, baseline, final")
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        label = f"helper ledger line {index + 1}"
        helper_id = _nonempty_str(row.get("id"), f"{label}.id")
        if helper_id in seen_ids:
            raise _fail(f"duplicate helper ledger id: {helper_id}")
        seen_ids.add(helper_id)
        if row.get("run_canary") != run_canary:
            raise _fail(f"{label} run canary mismatch")
        argv = row.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(item, str) or not item for item in argv)
        ):
            raise _fail(f"{label}.argv must be a non-empty string array")
        _nonempty_str(row.get("cwd"), f"{label}.cwd")
        output = row.get("output")
        if not isinstance(output, str) or not output:
            raise _fail(f"{label}.output must be a non-empty string")
        nonce, _ = _helper_nonce_from_output(output)
        if nonce is None:
            raise _fail(f"{label}.output must start with SB_SURVIVAL_V1_HELPER_*")
        exit_code = row.get("exit_code")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise _fail(f"{label}.exit_code must be an integer")
    return rows


def build_opencode_live_observer(
    *,
    workload: Mapping[str, Any],
    controller_state: Mapping[str, Any],
    stdout_by_turn: Mapping[int, str],
    helper_ledger_jsonl: str,
    before_checkout_sha256: str,
    after_checkout_sha256: str,
) -> dict:
    """Build independent observer evidence for one captured OpenCode attempt."""
    validated_workload = _validate_workload(workload)
    validated_controller = _validate_controller(controller_state)
    normalized_stdout = _normalize_stdout_by_turn(stdout_by_turn)
    before_sha, after_sha = _validate_hashes(
        before_checkout_sha256, after_checkout_sha256
    )
    helper_rows = _parse_helper_ledger(
        helper_ledger_jsonl, run_canary=validated_workload["run_canary"]
    )

    session_id = validated_controller["session_id"]
    model_id = validated_controller["model"]
    # The response-scoped native configuration is the exact provider/model
    # selected at launch. The surface identity remains the run's separate
    # configuration_id (opencode-cli).
    configuration = model_id
    workspace = validated_controller["workspace"]
    workload_turns: list[dict[str, Any]] = validated_workload["turns"]
    turn_by_sequence = {1: workload_turns[0], 2: workload_turns[1]}
    if workload_turns[0].get("revision") != "r1" or workload_turns[1].get(
        "revision"
    ) != "r2":
        raise _fail("workload turns must be ordered R1 then R2")

    streams: dict[int, list[dict[str, Any]]] = {}
    for turn in (1, 2):
        streams[turn] = _parse_stdout_stream(
            normalized_stdout[turn], turn=turn, session_id=session_id
        )

    events: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    sequence = 0
    relation_sequence = 0

    def next_sequence() -> int:
        nonlocal sequence
        sequence += 1
        return sequence

    def next_relation_sequence() -> int:
        nonlocal relation_sequence
        relation_sequence += 1
        return relation_sequence

    def add_event(
        *,
        event_id: str,
        kind: str,
        boundary: str,
        population_role: str,
        source: str,
        fields: dict[str, Any],
        metric_ids: list[str],
    ) -> dict[str, Any]:
        event = {
            "id": event_id,
            "sequence": next_sequence(),
            "boundary": boundary,
            "population_role": population_role,
            "kind": kind,
            "session_id": session_id,
            "source": source,
            "fields": fields,
            "metric_ids": metric_ids,
        }
        events.append(event)
        return event

    def add_relation(*, relation_id: str, kind: str, from_id: str, to_id: str) -> None:
        relations.append(
            {
                "id": relation_id,
                "kind": kind,
                "from_id": from_id,
                "to_id": to_id,
                "sequence": next_relation_sequence(),
            }
        )

    # Submitted user turns from the instantiated workload.
    turn_event_ids: dict[int, str] = {}
    for turn in (1, 2):
        workload_turn = turn_by_sequence[turn]
        event_id = str(workload_turn.get("id"))
        turn_event_ids[turn] = event_id
        revision = "r1" if turn == 1 else "r2"
        add_event(
            event_id=event_id,
            kind="user_turn",
            boundary="accepted",
            population_role="primary_scored",
            source="submitted_input",
            fields={
                "turn_id": event_id,
                "revision": revision,
                "role": "user",
                "text": str(workload_turn.get("text")),
                "run_canary": validated_workload["run_canary"],
                "response_canary": str(workload_turn.get("response_canary")),
            },
            metric_ids=[
                "work.submitted_turns",
                f"revision.{revision}",
                "revision.r1_r2_order",
            ],
        )

    # Actions and results from explicit tool_use rows.
    action_ids: dict[str, str] = {}
    result_ids: dict[str, str] = {}
    action_kinds: dict[str, str] = {}
    action_argvs: dict[str, list[str] | None] = {}
    result_exit_codes: dict[str, int | None] = {}
    result_nonces: dict[str, str | None] = {}
    result_prefixes: dict[str, str | None] = {}
    pending_file_change_action: str | None = None
    pending_edit_input: Mapping[str, Any] | None = None

    response_specs: dict[int, dict[str, Any]] = {}

    for turn in (1, 2):
        rows = streams[turn]
        workload_turn_id = turn_event_ids[turn]
        tool_indices = [index for index, row in enumerate(rows) if _is_tool_use(row)]
        per_turn_counter = 0
        for row_index in tool_indices:
            row = rows[row_index]
            per_turn_counter += 1
            tool = _tool_name(row)
            raw_input = _tool_input(row)
            command_text = _explicit_command_text(row, raw_input)
            argv = _explicit_argv(row, raw_input, command_text)
            cwd = _explicit_cwd(row, raw_input)
            target = _explicit_target(row, raw_input)
            cwd = _workspace_relative(cwd, workspace)
            target = _workspace_relative(target, workspace)
            action_kind = _canonical_action_kind(tool, command_text, argv)
            action_id = f"action-t{turn}-{per_turn_counter}"
            call_id = _call_id(row, f"call-t{turn}-{per_turn_counter}")
            fields: dict[str, Any] = {
                "action_kind": action_kind,
                "name": tool,
                "turn_id": workload_turn_id,
                "call_id": call_id,
            }
            native_action_id = row.get("id")
            if isinstance(native_action_id, str) and native_action_id:
                fields["native_action_id"] = native_action_id
            if argv is not None:
                fields["argv"] = list(argv)
            if cwd is not None:
                fields["cwd"] = cwd
            if target is not None:
                fields["target"] = target
            if raw_input is not None:
                fields["input"] = raw_input
            add_event(
                event_id=action_id,
                kind="action",
                boundary="harness_received",
                population_role="unscored",
                source="harness_trace",
                fields=fields,
                metric_ids=["work.actions", "causal.action_result"],
            )
            action_ids[f"{turn}:{row_index}"] = action_id
            action_kinds[action_id] = action_kind
            action_argvs[action_id] = argv
            if action_kind == "edit" and pending_file_change_action is None:
                pending_file_change_action = action_id
                pending_edit_input = raw_input if isinstance(raw_input, Mapping) else None

            completed, output_text = _completed_output(row)
            if not completed:
                continue
            state = row.get("state")
            state_mapping = state if isinstance(state, Mapping) else {}
            exit_code = _explicit_exit(state_mapping)
            if exit_code is not None and exit_code != 0:
                status = "failure"
            else:
                status = "success"
            result_fields: dict[str, Any] = {
                "action_id": action_id,
                "call_id": call_id,
                "status": status,
                "output": output_text,
            }
            if isinstance(native_action_id, str) and native_action_id:
                result_fields["native_result_id"] = f"{native_action_id}:result"
            if exit_code is not None:
                result_fields["exit_code"] = exit_code
            elif action_kind == "edit":
                result_fields["exit_code"] = 0
                exit_code = 0
            nonce, prefix = _helper_nonce_from_output(output_text)
            if nonce is not None:
                result_fields["helper_nonce"] = nonce
                result_fields["output_prefix"] = prefix
            result_id = f"result-t{turn}-{per_turn_counter}"
            add_event(
                event_id=result_id,
                kind="result",
                boundary="harness_received",
                population_role="unscored",
                source="harness_trace",
                fields=result_fields,
                metric_ids=["work.results", "causal.action_result"],
            )
            result_ids[action_id] = result_id
            result_exit_codes[action_id] = exit_code
            result_nonces[action_id] = nonce
            result_prefixes[action_id] = prefix
            add_relation(
                relation_id=f"relation-{action_id}-result",
                kind="action_result",
                from_id=action_id,
                to_id=result_id,
            )

        # One final response per turn from the text row with the exact canary.
        required_canary = str(turn_by_sequence[turn].get("response_canary"))
        matches = [
            index
            for index, row in enumerate(rows)
            if not _is_tool_use(row)
            and not _is_step_finish_stop(row)
            and (_row_text(row) is not None)
            and required_canary in str(_row_text(row))
        ]
        if len(matches) != 1:
            raise _fail(
                f"stdout turn {turn} must contain exactly one response "
                f"with the required canary (found {len(matches)})"
            )
        response_index = matches[0]
        response_text = str(_row_text(rows[response_index]))
        response_specs[turn] = {
            "row_index": response_index,
            "text": response_text,
            "canary": required_canary,
        }

    # Response-scoped usage from the following step_finish reason=stop.
    usage_ids: dict[int, str | None] = {1: None, 2: None}
    usage_values: dict[int, dict[str, int] | None] = {1: None, 2: None}
    for turn in (1, 2):
        rows = streams[turn]
        response_index = response_specs[turn]["row_index"]
        chosen: dict[str, int] | None = None
        for row in rows[response_index + 1 :]:
            if not _is_step_finish_stop(row):
                continue
            tokens = _tokens_from_record(row)
            if tokens is not None:
                if chosen is None:
                    chosen = tokens
        if chosen is not None:
            usage_ids[turn] = f"usage-r{turn}"
            usage_values[turn] = chosen

    for turn in (1, 2):
        spec = response_specs[turn]
        workload_turn_id = turn_event_ids[turn]
        response_id = f"response-r{turn}"
        fields: dict[str, Any] = {
            "turn_id": workload_turn_id,
            "role": "assistant",
            "status": "completed",
            "text": spec["text"],
            "canary": spec["canary"],
            "model_id": model_id,
            "configuration": configuration,
        }
        if usage_ids[turn] is not None and usage_values[turn] is not None:
            tokens = usage_values[turn]
            assert tokens is not None
            fields["usage_id"] = usage_ids[turn]
            fields["usage"] = {
                "input_tokens": tokens["input"],
                "output_tokens": tokens["output"],
                "reasoning_tokens": tokens["reasoning"],
                "cache_read_tokens": tokens["cache_read"],
                "cache_write_tokens": tokens["cache_write"],
            }
        add_event(
            event_id=response_id,
            kind="assistant_response",
            boundary="displayed",
            population_role="primary_scored",
            source="visible_stream",
            fields=fields,
            metric_ids=[
                "work.visible_responses",
                "causal.turn_response",
                "attribution.model_config",
                "attribution.usage",
                "attribution.token_semantics",
            ],
        )
        add_relation(
            relation_id=f"relation-r{turn}-response",
            kind="turn_response",
            from_id=workload_turn_id,
            to_id=response_id,
        )

    # Helper ledger cross-checks and supporting events.
    def find_helper_action(phase: str) -> str:
        candidates: list[str] = []
        for action_id, argv in action_argvs.items():
            joined = " ".join(argv) if argv else ""
            kind = action_kinds.get(action_id)
            if phase == "inspect" and kind == "inspect":
                candidates.append(action_id)
            elif phase in ("baseline", "final") and phase in joined:
                candidates.append(action_id)
        if len(candidates) != 1:
            raise _fail(
                f"helper phase {phase!r} must match exactly one stdout action "
                f"(found {len(candidates)})"
            )
        return candidates[0]

    helper_action: dict[str, str] = {}
    for row in helper_rows:
        phase = str(row.get("phase"))
        helper_action[phase] = find_helper_action(phase)

    for row in helper_rows:
        phase = str(row.get("phase"))
        helper_id = str(row.get("id"))
        action_id = helper_action[phase]
        ledger_argv = list(row.get("argv"))
        stdout_argv = action_argvs.get(action_id)
        comparable_argv = list(stdout_argv) if stdout_argv is not None else None
        if comparable_argv is not None and "--run-canary" in comparable_argv:
            flag = comparable_argv.index("--run-canary")
            if (
                comparable_argv.count("--run-canary") != 1
                or flag + 1 >= len(comparable_argv)
                or comparable_argv[flag + 1] != validated_workload["run_canary"]
            ):
                raise _fail(f"helper phase {phase!r} has an invalid run canary flag")
            del comparable_argv[flag : flag + 2]
        if comparable_argv != ledger_argv:
            raise _fail(f"helper phase {phase!r} argv disagrees with stdout action")
        if action_id not in result_ids:
            raise _fail(f"helper phase {phase!r} stdout action has no result link")
        ledger_exit = row.get("exit_code")
        stdout_exit = result_exit_codes.get(action_id)
        if stdout_exit is None or stdout_exit != ledger_exit:
            raise _fail(f"helper phase {phase!r} exit code disagrees")
        ledger_nonce, _ = _helper_nonce_from_output(str(row.get("output")))
        stdout_nonce = result_nonces.get(action_id)
        if ledger_nonce is None or stdout_nonce is None or ledger_nonce != stdout_nonce:
            raise _fail(f"helper phase {phase!r} helper output disagrees")
        ledger_prefix = str(row.get("output")).split()[0]
        if result_prefixes.get(action_id) != ledger_prefix:
            raise _fail(f"helper phase {phase!r} helper output disagrees")
        invoke_id = f"helper-invoke-{phase}"
        emit_id = f"helper-emit-{phase}"
        add_event(
            event_id=invoke_id,
            kind="helper",
            boundary="helper_invoked",
            population_role="supporting",
            source="helper_ledger",
            fields={
                "helper_id": helper_id,
                "phase": phase,
                "helper_nonce": ledger_nonce,
                "action_id": action_id,
                "argv": ledger_argv,
            },
            metric_ids=[],
        )
        add_event(
            event_id=emit_id,
            kind="helper",
            boundary="helper_emitted",
            population_role="supporting",
            source="helper_ledger",
            fields={
                "helper_id": helper_id,
                "phase": phase,
                "helper_nonce": ledger_nonce,
                "action_id": action_id,
                "exit_code": ledger_exit,
                "output_prefix": ledger_prefix,
            },
            metric_ids=[],
        )
        add_relation(
            relation_id=f"relation-helper-{phase}",
            kind="helper_for",
            from_id=invoke_id,
            to_id=action_id,
        )

    # Only the four frozen workload actions/results are scored. Discovery
    # reads remain in the observer as unscored context and cannot silently
    # enlarge the denominator.
    primary_actions = {
        helper_action["inspect"],
        helper_action["baseline"],
        pending_file_change_action,
        helper_action["final"],
    }
    if None in primary_actions or len(primary_actions) != 4:
        raise _fail("the frozen workload did not bind exactly four actions")
    declared_by_role = {
        helper_action["inspect"]: "action-inspect",
        helper_action["baseline"]: "action-baseline",
        pending_file_change_action: "action-edit",
        helper_action["final"]: "action-final",
    }
    workload_actions = {
        str(item.get("id")): item
        for item in workload.get("actions", [])
        if isinstance(item, Mapping)
    }
    for event in events:
        if event["kind"] == "action" and event["id"] in primary_actions:
            event["population_role"] = "primary_scored"
            event["metric_ids"] = ["work.actions", "causal.action_result"]
            declared = workload_actions.get(declared_by_role[event["id"]])
            if isinstance(declared, Mapping):
                for key in ("target", "cwd"):
                    value = declared.get(key)
                    if isinstance(value, str) and value:
                        event["fields"][key] = value
        elif (
            event["kind"] == "result"
            and event["fields"].get("action_id") in primary_actions
        ):
            event["population_role"] = "primary_scored"
            event["metric_ids"] = ["work.results", "causal.action_result"]
    relations[:] = [
        relation
        for relation in relations
        if relation.get("kind") != "action_result"
        or relation.get("from_id") in primary_actions
    ]

    # One primary file change for the fixture checkout.
    if pending_file_change_action is None:
        raise _fail("stdout streams must contain an explicit edit action")
    file_change_fields: dict[str, Any] = {
        "path": "fixture_project/checkout.py",
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "action_id": pending_file_change_action,
    }
    if pending_edit_input is not None:
        for key in ("oldString", "old_string", "oldText", "before"):
            value = pending_edit_input.get(key)
            if isinstance(value, str):
                file_change_fields["before_fragment"] = value
                break
        for key in ("newString", "new_string", "newText", "after"):
            value = pending_edit_input.get(key)
            if isinstance(value, str):
                file_change_fields["after_fragment"] = value
                break
    add_event(
        event_id="file-change-checkout",
        kind="file_change",
        boundary="file_observed",
        population_role="primary_scored",
        source="filesystem_snapshot",
        fields=file_change_fields,
        metric_ids=["work.changed_files"],
    )

    # Ordering and final relations.
    add_relation(
        relation_id="relation-r1-r2",
        kind="supersedes",
        from_id=turn_event_ids[1],
        to_id=turn_event_ids[2],
    )
    final_action = helper_action.get("final")
    if final_action is None:
        raise _fail("helper ledger must link an explicit final action")
    add_relation(
        relation_id="relation-final-after-r2",
        kind="final_after",
        from_id=turn_event_ids[2],
        to_id=final_action,
    )

    # Frozen reconciliation is response-scoped: sum the two usage records
    # joined to the displayed responses, then compare that sum to the native
    # session total. Internal model steps cannot be substituted for responses.
    response_usage = [
        value for value in usage_values.values() if isinstance(value, dict)
    ]
    total_input = sum(item["input"] for item in response_usage)
    total_output = sum(item["output"] for item in response_usage)
    total_reasoning = sum(item["reasoning"] for item in response_usage)
    total_read = sum(item["cache_read"] for item in response_usage)
    total_write = sum(item["cache_write"] for item in response_usage)
    emitted_usage_ids = [
        usage_ids[turn] for turn in (1, 2) if usage_ids[turn] is not None
    ]
    if response_usage:
        reconciliation = (
            f"sum of {len(response_usage)} response-linked usage records"
        )
    else:
        reconciliation = (
            "no valid step_finish token records; session total is zero"
        )
    add_event(
        event_id="usage-total",
        kind="usage_total",
        boundary="harness_received",
        population_role="supporting",
        source="usage_trace",
        fields={
            "usage_ids": emitted_usage_ids,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "reasoning_tokens": total_reasoning,
            "cache_read_tokens": total_read,
            "cache_write_tokens": total_write,
            "reconciliation": reconciliation,
        },
        metric_ids=["attribution.reconciliation"],
    )

    # Portable unknowns only; never a completeness or equality claim.
    add_event(
        event_id="portable-observation",
        kind="portable",
        boundary="harness_received",
        population_role="supporting",
        source="harness_trace",
        fields={
            "capture_id": "unknown",
            "declared_root": "unknown",
            "complete_root": "unknown",
            "companions_present": "unknown",
            "isolated_decode": "unknown",
            "canonical_equality": "unknown",
            "note": (
                "live observer does not claim complete_root, companions, "
                "isolated decode, or canonical equality"
            ),
        },
        metric_ids=[
            "portable.complete_root",
            "portable.companions",
            "portable.isolated_decode",
            "portable.canonical_equality",
        ],
    )

    # Reassign event sequence to the observed two-turn chronology. Supporting
    # helper/usage/portability evidence follows the primary stream.
    def timeline_key(event: Mapping[str, Any]) -> tuple[int, int]:
        event_id = str(event.get("id"))
        kind = event.get("kind")
        fields = event.get("fields") if isinstance(event.get("fields"), Mapping) else {}
        turn_id = fields.get("turn_id")
        old_sequence = int(event.get("sequence", 0))
        if event_id == turn_event_ids[1]:
            return (0, old_sequence)
        if turn_id == turn_event_ids[1] and kind != "assistant_response":
            return (1, old_sequence)
        if event_id == "response-r1":
            return (2, old_sequence)
        if event_id == turn_event_ids[2]:
            return (3, old_sequence)
        if turn_id == turn_event_ids[2] and kind != "assistant_response":
            return (4, old_sequence)
        if event_id == "file-change-checkout":
            return (4, old_sequence)
        if event_id == "response-r2":
            return (5, old_sequence)
        return (6, old_sequence)

    events.sort(key=timeline_key)
    for new_sequence, event in enumerate(events, 1):
        event["sequence"] = new_sequence

    # Structural validation: unique ids/sequences, valid relation endpoints.
    seen_event_ids: set[str] = set()
    seen_sequences: set[int] = set()
    for event in events:
        event_id = event.get("id")
        if event_id in seen_event_ids:
            raise _fail(f"duplicate observer event id: {event_id}")
        seen_event_ids.add(event_id)
        event_sequence = event.get("sequence")
        if (
            isinstance(event_sequence, bool)
            or not isinstance(event_sequence, int)
            or event_sequence < 1
        ):
            raise _fail("observer event sequence must be a positive integer")
        if event_sequence in seen_sequences:
            raise _fail("duplicate observer event sequence")
        seen_sequences.add(event_sequence)
    seen_relation_ids: set[str] = set()
    for relation in relations:
        relation_id = relation.get("id")
        if relation_id in seen_relation_ids:
            raise _fail(f"duplicate observer relation id: {relation_id}")
        seen_relation_ids.add(relation_id)
        if relation.get("kind") not in {
            "action_result",
            "turn_response",
            "supersedes",
            "final_after",
            "helper_for",
        }:
            raise _fail("observer relation kind is unsupported")
        for field in ("from_id", "to_id"):
            endpoint = relation.get(field)
            if endpoint not in seen_event_ids:
                raise _fail("observer relation endpoint does not name an event")
        relation_sequence_value = relation.get("sequence")
        if (
            isinstance(relation_sequence_value, bool)
            or not isinstance(relation_sequence_value, int)
            or relation_sequence_value < 1
        ):
            raise _fail("observer relation sequence must be a positive integer")

    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "scenario_id": SCENARIO_ID,
        "run_id": validated_workload["run_id"],
        "independent": True,
        "method": _METHOD,
        "events": events,
        "relations": relations,
    }
