"""Read-only decoder for a copied OpenCode 1.18 SQLite bundle.

The acquisition adapter copies ``opencode.db`` together with its WAL and SHM
companions.  This module consumes that copied directory only.  It never looks
up an OpenCode profile, credentials, or a second database, and it never opens a
database in place.  The copied files are cloned into a temporary directory so
SQLite can replay the WAL without changing the supplied evidence.

The result deliberately has two layers:

* ``events`` and ``facts`` describe what the native rows actually contain;
* ``metrics`` projects those facts onto the frozen 19 survival metric IDs.

Native evidence cannot establish the independently observed side of a survival
comparison.  Therefore metric rows keep ``state="unresolved"`` and expose
``observation="unknown"`` even when a native count is known.  ``availability``
is a separate, explicit native-presence value and is never used to imply a
score.  A missing field is ``absent`` only when that field is explicitly absent
from a valid native row; malformed or unsupported input remains ``unknown``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from ..survival_metrics import METRICS


OPENCODE_DECODER_SCHEMA = "session-bench-opencode-sqlite-decoder-v1"
OPENCODE_FORMAT = "opencode-sqlite-v1"
OPENCODE_DB = "opencode.db"
OPENCODE_WAL = "opencode.db-wal"
OPENCODE_SHM = "opencode.db-shm"
OPENCODE_BUNDLE_FILES = (OPENCODE_DB, OPENCODE_WAL, OPENCODE_SHM)
OPENCODE_SUPPORTED_VERSION_PREFIX = "1.18."

_METRIC_IDS = tuple(METRICS)
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_KNOWN_PART_TYPES = frozenset({"text", "reasoning", "step-start", "step-finish", "tool"})
_TOOL_TYPES = frozenset({"tool", "tool_call", "tool-invocation"})
_TEXT_TYPES = frozenset({"text"})


class OpenCodeDecoderError(ValueError):
    """The explicitly supplied copied OpenCode bundle cannot be decoded."""


@dataclass(frozen=True)
class _BundleFile:
    name: str
    sha256: str
    size_bytes: int

    def display(self) -> dict[str, Any]:
        return {"name": self.name, "sha256": self.sha256, "size_bytes": self.size_bytes}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json(value: str | bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"{label}: duplicate JSON key {key!r}")
            result[key] = item
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"{label}: non-finite JSON number {value}")

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}: invalid JSON: {exc}") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _non_empty_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    if len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise OpenCodeDecoderError(f"{label} exceeds the decoder text limit")
    return value


def _normal_path(path: Path | str) -> Path:
    value = Path(path).expanduser()
    if value.is_symlink() or not value.is_dir():
        raise OpenCodeDecoderError("OpenCode copied bundle must be an ordinary directory")
    resolved = value.resolve(strict=True)
    forbidden_roots = (
        Path.home() / ".local" / "share" / "opencode",
        Path.home() / ".config" / "opencode",
        Path.home() / "Library" / "Application Support" / "opencode",
        Path.home() / "Library" / "Application Support" / "OpenCode",
    )
    if any(resolved == root or root in resolved.parents for root in forbidden_roots):
        raise OpenCodeDecoderError("normal OpenCode profile roots are forbidden; pass the copied bundle")
    return resolved


def _bundle_files(bundle: Path) -> tuple[_BundleFile, ...]:
    try:
        names = {item.name for item in bundle.iterdir()}
    except OSError as exc:
        raise OpenCodeDecoderError(f"cannot inspect copied OpenCode bundle: {exc}") from exc
    expected = set(OPENCODE_BUNDLE_FILES)
    if names != expected:
        missing = sorted(expected - names)
        extra = sorted(names - expected)
        raise OpenCodeDecoderError(f"copied OpenCode bundle must contain exactly db/WAL/SHM (missing={missing}, extra={extra})")
    result: list[_BundleFile] = []
    for name in OPENCODE_BUNDLE_FILES:
        path = bundle / name
        try:
            info = path.lstat()
        except OSError as exc:
            raise OpenCodeDecoderError(f"cannot stat copied OpenCode artifact {name}: {exc}") from exc
        if path.is_symlink() or not path.is_file():
            raise OpenCodeDecoderError(f"copied OpenCode artifact {name} must be an ordinary file")
        if info.st_size > _MAX_TEXT_BYTES * 64:
            raise OpenCodeDecoderError(f"copied OpenCode artifact {name} exceeds the decoder size limit")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise OpenCodeDecoderError(f"cannot read copied OpenCode artifact {name}: {exc}") from exc
        if name != OPENCODE_SHM and not data:
            raise OpenCodeDecoderError(f"copied OpenCode artifact {name} is empty")
        result.append(_BundleFile(name, _sha256_bytes(data), len(data)))
    return tuple(result)


def _sql_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise OpenCodeDecoderError("unexpected SQLite identifier")
    return value


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    table = _sql_identifier(table)
    try:
        return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.DatabaseError as exc:
        raise OpenCodeDecoderError(f"could not inspect OpenCode table {table}: {exc}") from exc


def _locator(artifacts: Mapping[str, _BundleFile], table: str, row_id: str) -> dict[str, Any]:
    db = artifacts[OPENCODE_DB]
    return {
        "artifact": db.name,
        "artifact_sha256": db.sha256,
        "table": table,
        "row_id": row_id,
        "wal_sha256": artifacts[OPENCODE_WAL].sha256,
        "shm_sha256": artifacts[OPENCODE_SHM].sha256,
    }


def _diagnostic(code: str, detail: str, locator: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "detail": detail, **({"locator": dict(locator)} if locator else {})}


def _presence(value: Any, *, explicit: bool = True) -> str:
    if not explicit:
        return "unknown"
    return "present" if value is not None else "absent"


def _event_identity(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _state_for_native(*, supported: bool, native_present: bool) -> str:
    # This decoder has no independent observer.  Keep every comparison
    # unresolved, including known native presence; callers can join it to the
    # observer later without mistaking a native count for a measured score.
    return "decoder_unsupported" if not supported else "unresolved"


def _metric_row(
    metric_id: str,
    *,
    decoded_eligible: int = 0,
    availability: str = "unknown",
    value: Any = None,
    evidence: Iterable[Mapping[str, Any]] = (),
    supported: bool = True,
) -> dict[str, Any]:
    if metric_id not in METRICS:
        raise AssertionError(f"unknown survival metric: {metric_id}")
    if availability not in {"present", "absent", "unknown"}:
        raise AssertionError(f"invalid native availability: {availability}")
    if not isinstance(decoded_eligible, int) or isinstance(decoded_eligible, bool) or decoded_eligible < 0:
        raise AssertionError("decoded_eligible must be a nonnegative integer")
    return {
        "id": metric_id,
        "state": _state_for_native(supported=supported, native_present=availability == "present"),
        "availability": availability,
        "observation": "unknown",
        "correct": 0,
        "observed_eligible": 0,
        "decoded_eligible": decoded_eligible,
        "value": value,
        "evidence": [dict(item) for item in evidence],
    }


def _extract_patch_paths(value: Any) -> list[str]:
    """Extract paths explicitly named by an apply_patch style input.

    This parser only accepts the patch headers themselves.  It does not infer
    changed files from arbitrary shell text or from a diff count.
    """

    if not isinstance(value, str):
        return []
    paths: list[str] = []
    for line in value.splitlines():
        match = re.match(r"^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s+(.+?)\s*$", line)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate not in paths:
                paths.append(candidate)
        match = re.match(r"^\*\*\*\s+Move\s+to:\s+(.+?)\s*$", line)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate not in paths:
                paths.append(candidate)
    return paths


def _extract_file_paths(tool: str | None, state: Mapping[str, Any]) -> list[str]:
    if not isinstance(tool, str):
        return []
    raw_input = state.get("input")
    if tool in {"apply_patch", "patch"}:
        if isinstance(raw_input, Mapping):
            raw_input = raw_input.get("patchText", raw_input.get("patch"))
        return _extract_patch_paths(raw_input)
    if tool in {"edit", "write", "write_file", "edit_file"} and isinstance(raw_input, Mapping):
        for key in ("filePath", "path", "file"):
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                return [value]
    return []


def _extract_tool_target(tool: str | None, state: Mapping[str, Any]) -> str | None:
    if not isinstance(tool, str):
        return None
    raw_input = state.get("input")
    if tool in {"apply_patch", "patch"}:
        value = raw_input
        if isinstance(value, Mapping):
            value = value.get("patchText", value.get("patch"))
        paths = _extract_patch_paths(value)
        return paths[0] if len(paths) == 1 else None
    if isinstance(raw_input, Mapping):
        for key in ("filePath", "path", "file", "target"):
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _relative_native_path(value: Any, session_directory: Any) -> str | None:
    """Keep file-change evidence relative to the declared session root."""

    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute() and isinstance(session_directory, str) and session_directory:
        try:
            return candidate.resolve(strict=False).relative_to(Path(session_directory).resolve(strict=False)).as_posix()
        except ValueError:
            return candidate.name
    return candidate.as_posix()


def _token_semantics(tokens: Any) -> tuple[bool, dict[str, Any]]:
    if not isinstance(tokens, Mapping):
        return False, {"state": "absent"}
    expected = ("input", "output", "reasoning", "cache")
    if any(key not in tokens for key in expected):
        return False, {"state": "unknown", "keys": sorted(str(key) for key in tokens)}
    cache = tokens.get("cache")
    if not isinstance(cache, Mapping) or any(key not in cache for key in ("read", "write")):
        return False, {"state": "unknown", "keys": sorted(str(key) for key in tokens)}
    values = [tokens.get("input"), tokens.get("output"), tokens.get("reasoning"), cache.get("read"), cache.get("write")]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
        return False, {"state": "unknown", "keys": sorted(str(key) for key in tokens)}
    return True, {"state": "present", "input": values[0], "output": values[1], "reasoning": values[2], "cache_read": values[3], "cache_write": values[4]}


_REVISION_PATTERN = re.compile(r"\bR([12])\b", re.IGNORECASE)
_CANARY_PATTERN = re.compile(r"SB_SURVIVAL_V1_[A-Za-z0-9_Δ🙂-]+")


def _revision_from_text(text: Any) -> str | None:
    if not isinstance(text, str) or not text:
        return None
    match = _REVISION_PATTERN.search(text)
    return f"r{match.group(1)}".lower() if match else None


def _qualified_model(provider: Any, model: Any) -> str | None:
    if isinstance(model, str) and model and isinstance(provider, str) and provider:
        if model.startswith(f"{provider}/"):
            return model
        return f"{provider}/{model}"
    if isinstance(model, str) and model:
        return model
    return None


def _canaries_in_text(text: Any) -> list[str]:
    if not isinstance(text, str) or not text:
        return []
    values: list[str] = []
    for match in _CANARY_PATTERN.finditer(text):
        value = match.group(0).rstrip(".,;:)")
        if value and value not in values:
            values.append(value)
    return values


def _explicit_exit_code(state: Any, raw_output: Any) -> int | None:
    containers = [state, raw_output]
    if isinstance(state, Mapping):
        containers.append(state.get("metadata"))
    for container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in ("exit_code", "exitCode", "exit"):
            value = container.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


_HELPER_OUTPUT = re.compile(
    r"^SB_SURVIVAL_V1_HELPER_(?:INSPECT|BASELINE|FINAL)_([^\s]+)", re.MULTILINE
)


def _helper_nonce(output: Any) -> str | None:
    if not isinstance(output, str):
        return None
    match = _HELPER_OUTPUT.search(output)
    return match.group(1) if match else None


_OLD_FRAGMENT_KEYS = ("oldString", "old_string", "oldText", "old_text", "before", "beforeText", "before_text", "original", "originalText", "original_text")
_NEW_FRAGMENT_KEYS = ("newString", "new_string", "newText", "new_text", "after", "afterText", "after_text", "updated", "updatedText", "updated_text", "content", "contents")


def _edit_fragments(input_value: Any) -> tuple[str | None, str | None, str | None, str | None]:
    if not isinstance(input_value, Mapping):
        return None, None, None, None
    old_value: str | None = None
    old_key: str | None = None
    new_value: str | None = None
    new_key: str | None = None
    for key in _OLD_FRAGMENT_KEYS:
        candidate = input_value.get(key)
        if isinstance(candidate, str):
            old_value = candidate
            old_key = key
            break
    for key in _NEW_FRAGMENT_KEYS:
        candidate = input_value.get(key)
        if isinstance(candidate, str):
            new_value = candidate
            new_key = key
            break
    return old_value, old_key, new_value, new_key


def _read_rows(connection: sqlite3.Connection, session_id: str, artifacts: Mapping[str, _BundleFile]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    required_columns = {
        "session": {"id", "directory", "version"},
        "message": {"id", "session_id", "time_created", "data"},
        "part": {"id", "message_id", "session_id", "time_created", "data"},
    }
    table_columns: dict[str, set[str]] = {}
    for table, required in required_columns.items():
        columns = _table_columns(connection, table)
        table_columns[table] = columns
        if not required <= columns:
            raise OpenCodeDecoderError(f"OpenCode {table} table lacks required columns: {sorted(required - columns)}")

    def select_column(table: str, column: str) -> str:
        return f'"{column}"' if column in table_columns[table] else f'NULL AS "{column}"'

    session_row = connection.execute(
        'SELECT ' + ','.join(select_column("session", column) for column in (
            "id", "project_id", "workspace_id", "parent_id", "slug", "directory", "path", "title", "version", "model", "time_created", "time_updated",
            "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write",
        )) + ' FROM "session" WHERE id = ?',
        (session_id,),
    ).fetchone()
    if session_row is None:
        raise OpenCodeDecoderError(f"OpenCode session {session_id!r} is not present in the copied bundle")
    session = dict(zip((
        "id", "project_id", "workspace_id", "parent_id", "slug", "directory", "path", "title", "version", "model", "time_created", "time_updated",
        "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write",
    ), session_row, strict=True))
    # Keep the public result free of arbitrary session metadata and project
    # fields.  Directory is retained only as a native identity fact.
    session = {key: session[key] for key in ("id", "directory", "path", "version", "model", "time_created", "time_updated", "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")}

    message_rows = connection.execute(
        'SELECT ' + ','.join(select_column("message", column) for column in ("id", "session_id", "time_created", "time_updated", "data")) + ' FROM "message" WHERE session_id = ? ORDER BY time_created,id',
        (session_id,),
    ).fetchall()
    part_rows = connection.execute(
        'SELECT ' + ','.join(select_column("part", column) for column in ("id", "message_id", "session_id", "time_created", "time_updated", "data")) + ' FROM "part" WHERE session_id = ? ORDER BY time_created,id',
        (session_id,),
    ).fetchall()
    messages: list[dict[str, Any]] = []
    message_ids: set[str] = set()
    for row in message_rows:
        row_id = str(row[0])
        locator = _locator(artifacts, "message", row_id)
        try:
            value = _strict_json(row[4], f"message/{row_id}")
        except ValueError as exc:
            diagnostics.append(_diagnostic("malformed_message", str(exc), locator))
            continue
        if not isinstance(value, Mapping):
            diagnostics.append(_diagnostic("malformed_message", f"message/{row_id} is not a JSON object", locator))
            continue
        message_ids.add(row_id)
        messages.append({
            "id": row_id,
            "session_id": row[1],
            "time_created": row[2],
            "time_updated": row[3],
            "data": dict(value),
            "locator": locator,
        })
    parts: list[dict[str, Any]] = []
    for row in part_rows:
        row_id = str(row[0])
        locator = _locator(artifacts, "part", row_id)
        if row[1] not in message_ids:
            diagnostics.append(_diagnostic("dangling_part", f"part/{row_id} references unknown message {row[1]!r}", locator))
        try:
            value = _strict_json(row[5], f"part/{row_id}")
        except ValueError as exc:
            diagnostics.append(_diagnostic("malformed_part", str(exc), locator))
            continue
        if not isinstance(value, Mapping):
            diagnostics.append(_diagnostic("malformed_part", f"part/{row_id} is not a JSON object", locator))
            continue
        parts.append({
            "id": row_id,
            "message_id": row[1],
            "session_id": row[2],
            "time_created": row[3],
            "time_updated": row[4],
            "data": dict(value),
            "locator": locator,
        })
    return [session], messages, parts, diagnostics


def _session_candidates(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute('SELECT id FROM "session" ORDER BY id').fetchall()
    return [row[0] for row in rows if isinstance(row[0], str) and row[0]]


def _copy_bundle(
    bundle: Path,
    target: Path,
    *,
    declared: Sequence[_BundleFile],
) -> dict[str, _BundleFile]:
    """Clone only the inspected bytes, rejecting a source that changed after inspection."""

    declared_by_name = {item.name: item for item in declared}
    if set(declared_by_name) != set(OPENCODE_BUNDLE_FILES):
        raise AssertionError("declared OpenCode bundle family is incomplete")
    artifacts: dict[str, _BundleFile] = {}
    for name in OPENCODE_BUNDLE_FILES:
        source = bundle / name
        destination = target / name
        shutil.copyfile(source, destination)
        data = destination.read_bytes()
        copied_artifact = _BundleFile(name, _sha256_bytes(data), len(data))
        if copied_artifact != declared_by_name[name]:
            raise OpenCodeDecoderError(
                f"copied OpenCode artifact {name} changed after bundle inspection"
            )
        artifacts[name] = copied_artifact
    return artifacts


def _decode_rows(
    session: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    parts: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, _BundleFile],
    diagnostics: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    parts_by_message: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for part in parts:
        parts_by_message[str(part["message_id"])].append(part)
    parent_by_message: dict[str, str | None] = {}
    role_by_message: dict[str, str | None] = {}
    for message in messages:
        mid = str(message["id"])
        data = message["data"] if isinstance(message.get("data"), Mapping) else {}
        parent = data.get("parentID")
        parent_by_message[mid] = parent if isinstance(parent, str) and parent else None
        role = data.get("role")
        role_by_message[mid] = role if isinstance(role, str) else None
    user_ids: set[str] = {mid for mid, role in role_by_message.items() if role == "user"}

    def _turn_for_message(mid: str) -> str | None:
        parent = parent_by_message.get(mid)
        return parent if parent is not None and parent in user_ids else None

    events: list[dict[str, Any]] = []
    message_summaries: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []
    usage_rows: list[dict[str, Any]] = []
    part_summaries: list[dict[str, Any]] = []
    user_messages: list[Mapping[str, Any]] = []
    assistant_messages: list[Mapping[str, Any]] = []
    visible_responses: list[Mapping[str, Any]] = []

    for message in messages:
        data = message["data"]
        role = data.get("role")
        message_parts = parts_by_message.get(str(message["id"]), [])
        visible_texts: list[str] = []
        for part in message_parts:
            part_data = part["data"]
            part_type = part_data.get("type")
            part_summaries.append({
                "id": str(part["id"]),
                "message_id": str(part["message_id"]),
                "session_id": str(part["session_id"]),
                "time_created": part["time_created"],
                "time_updated": part["time_updated"],
                "type": part_type if isinstance(part_type, str) else None,
                "tool": part_data.get("tool") if isinstance(part_data.get("tool"), str) else None,
                "locator": part["locator"],
            })
            if part_type not in _KNOWN_PART_TYPES and not isinstance(part_data.get("tool"), str):
                diagnostics.append(_diagnostic("unsupported_part_type", f"part/{part['id']} has unsupported type {part_type!r}", part["locator"]))
            if part_type in _TEXT_TYPES:
                text = _bounded_text(part_data.get("text", part_data.get("content")), f"part/{part['id']}.text")
                if text:
                    visible_texts.append(text)
            if part_type in _TOOL_TYPES or isinstance(part_data.get("tool"), str):
                state = part_data.get("state")
                if state is None:
                    state = {}
                if not isinstance(state, Mapping):
                    diagnostics.append(_diagnostic("malformed_tool_state", f"part/{part['id']} tool state is not an object", part["locator"]))
                    state = {}
                call_id = _event_identity(part_data.get("callID", part_data.get("call_id")), str(part["id"]))
                tool = part_data.get("tool", part_data.get("name"))
                tool_name = tool if isinstance(tool, str) else None
                raw_input = state.get("input", part_data.get("input", part_data.get("arguments")))
                output = state.get("output") if "output" in state else part_data.get("output", part_data.get("result"))
                output_text = _bounded_text(output, f"part/{part['id']}.output") if isinstance(output, str) else (None if output is None else json.dumps(output, ensure_ascii=False, sort_keys=True))
                exit_code = _explicit_exit_code(state, output)
                turn_id = _turn_for_message(str(message["id"]))
                helper_nonce = _helper_nonce(output_text)
                cwd = None
                if isinstance(raw_input, Mapping):
                    for cwd_key in ("workdir", "cwd", "working_directory", "workingDirectory"):
                        candidate = raw_input.get(cwd_key)
                        if isinstance(candidate, str) and candidate:
                            cwd = candidate
                            break
                single_paths = [(_relative_native_path(raw_path, session.get("directory")), raw_path) for raw_path in _extract_file_paths(tool_name, state if isinstance(state, Mapping) else {})]
                raw_target = _extract_tool_target(tool_name, state)
                explicit_target = _relative_native_path(raw_target, session.get("directory"))
                action = {
                    "id": str(part["id"]),
                    "call_id": call_id,
                    "message_id": str(message["id"]),
                    "session_id": str(message["session_id"]),
                    "tool": tool_name,
                    "name": tool_name,
                    "status": state.get("status", part_data.get("status")),
                    "input": raw_input,
                    "arguments": raw_input,
                    "output": output_text,
                    "output_presence": _presence(output, explicit=("output" in state or "output" in part_data or "result" in part_data)),
                    "turn_id": turn_id,
                    "cwd": cwd,
                    "helper_nonce": helper_nonce,
                    "target": explicit_target,
                    "path": explicit_target,
                    "time_created": part["time_created"],
                    "time_updated": part["time_updated"],
                    "locator": part["locator"],
                }
                if exit_code is not None:
                    action["exit_code"] = exit_code
                tool_calls.append(action)
                events.append({"kind": "tool_call", **action})
                if action["status"] == "completed" and action["output_presence"] == "present":
                    events.append({"kind": "tool_result", "id": action["id"], "call_id": call_id, "message_id": str(message["id"]), "session_id": str(message["session_id"]), "output": output_text, "locator": part["locator"]})
                old_fragment, old_key, new_fragment, new_key = _edit_fragments(raw_input)
                for relative_path, _raw in single_paths:
                    if relative_path is None:
                        continue
                    file_change = {"id": f"{part['id']}:{relative_path}", "path": relative_path, "call_id": call_id, "action_id": str(part["id"]), "message_id": str(message["id"]), "turn_id": turn_id, "locator": part["locator"], "fragment_kind": "recorded_fragment", "fragment_note": "recorded fragments, not whole-file hashes"}
                    if old_fragment is not None:
                        if old_key == "oldString":
                            file_change["oldString"] = old_fragment
                        else:
                            file_change["old_fragment"] = old_fragment
                        file_change["old_fragment_key"] = old_key
                    if new_fragment is not None:
                        if new_key == "newString":
                            file_change["newString"] = new_fragment
                        else:
                            file_change["new_fragment"] = new_fragment
                        file_change["new_fragment_key"] = new_key
                    file_changes.append(file_change)
                    events.append({"kind": "file_change", **file_change})
        role_text = "\n".join(visible_texts)
        summary = {
            "id": str(message["id"]),
            "role": role if isinstance(role, str) else None,
            "parent_id": data.get("parentID") if isinstance(data.get("parentID"), str) else None,
            "time_created": message["time_created"],
            "time_updated": message["time_updated"],
            "text": role_text,
            "text_presence": _presence(role_text),
            "model": data.get("modelID", data.get("model")) if isinstance(data.get("modelID", data.get("model")), str) else None,
            "provider": data.get("providerID", data.get("provider")) if isinstance(data.get("providerID", data.get("provider")), str) else None,
            "finish": data.get("finish"),
            "locator": message["locator"],
        }
        message_summaries.append(summary)
        events.append({"kind": "user_message" if role == "user" else "assistant_message" if role == "assistant" else "message", **summary})
        if role == "user":
            user_messages.append(summary)
        elif role == "assistant":
            assistant_messages.append(summary)
            if role_text and data.get("finish") == "stop":
                visible_responses.append(summary)
            tokens = data.get("tokens")
            valid_tokens, token_payload = _token_semantics(tokens)
            usage_rows.append({"message_id": str(message["id"]), "tokens": token_payload, "valid": valid_tokens, "cost": data.get("cost"), "locator": message["locator"]})

    # Parent IDs are native joins, so no turn is invented when OpenCode leaves
    # one side unbound.  The event order remains the database's time order.
    message_summaries.sort(key=lambda item: (item["time_created"], item["id"]))
    tool_calls.sort(key=lambda item: (item["locator"].get("row_id", ""), item["id"]))
    visible_responses.sort(key=lambda item: (item["time_created"], item["id"]))
    user_messages.sort(key=lambda item: (item["time_created"], item["id"]))
    assistant_messages.sort(key=lambda item: (item["time_created"], item["id"]))
    file_changes = list({item["path"]: item for item in file_changes}.values())

    # Explicit semantic records for live_metric_comparator.  Every record uses
    # only stable native IDs, native parent links, and native call links.  A
    # missing parent or call link stays absent; it is never invented.
    turns: list[dict[str, Any]] = []
    for order, summary in enumerate(user_messages, 1):
        revision = _revision_from_text(summary.get("text"))
        record: dict[str, Any] = {
            "id": str(summary["id"]),
            "turn_id": str(summary["id"]),
            "role": "user",
            "text": summary.get("text"),
            "revision": revision,
            "time_created": summary.get("time_created"),
            "time_updated": summary.get("time_updated"),
            "sequence": order,
            "locator": summary.get("locator"),
        }
        turns.append(record)
    responses: list[dict[str, Any]] = []
    for order, summary in enumerate(visible_responses, 1):
        parent = summary.get("parent_id")
        turn_id = parent if isinstance(parent, str) and parent in user_ids else None
        qualified = _qualified_model(summary.get("provider"), summary.get("model"))
        canaries = _canaries_in_text(summary.get("text"))
        record = {
            "id": str(summary["id"]),
            "turn_id": turn_id,
            "role": "assistant",
            "status": "completed",
            "text": summary.get("text"),
            "canary": next(
                (value for value in canaries if value.startswith("SB_SURVIVAL_V1_RESPONSE_")),
                None,
            ),
            "canaries": canaries,
            "model_id": qualified,
            "configuration": qualified,
            "model": summary.get("model"),
            "provider": summary.get("provider"),
            "finish": summary.get("finish"),
            "time_created": summary.get("time_created"),
            "time_updated": summary.get("time_updated"),
            "sequence": order,
            "locator": summary.get("locator"),
        }
        responses.append(record)
    actions: list[dict[str, Any]] = []
    for item in tool_calls:
        record = {
            "id": str(item["id"]),
            "call_id": item.get("call_id"),
            "tool": item.get("tool"),
            "name": item.get("name", item.get("tool")),
            "action_kind": item.get("tool"),
            "input": item.get("input"),
            "arguments": item.get("arguments", item.get("input")),
            "status": item.get("status"),
            "output": item.get("output"),
            "message_id": item.get("message_id"),
            "turn_id": item.get("turn_id"),
            "cwd": item.get("cwd"),
            "helper_nonce": item.get("helper_nonce"),
            "target": item.get("target"),
            "path": item.get("path"),
            "time_created": item.get("time_created"),
            "time_updated": item.get("time_updated"),
            "locator": item.get("locator"),
        }
        if "exit_code" in item:
            record["exit_code"] = item["exit_code"]
        actions.append(record)
    results: list[dict[str, Any]] = []
    for item in tool_calls:
        if not (item.get("status") == "completed" and item.get("output_presence") == "present"):
            continue
        result_id = f"{item['id']}:result"
        native_status = "failure" if item.get("exit_code") not in (None, 0) else "success"
        record = {
            "id": result_id,
            "action_id": str(item["id"]),
            "call_id": item.get("call_id"),
            "status": native_status,
            "output": item.get("output"),
            "message_id": item.get("message_id"),
            "turn_id": item.get("turn_id"),
            "helper_nonce": item.get("helper_nonce"),
            "locator": item.get("locator"),
        }
        if "exit_code" in item:
            record["exit_code"] = item["exit_code"]
        elif item.get("tool") in {"edit", "write", "apply_patch", "patch", "edit_file", "write_file"}:
            # A completed structured mutation has an explicit success state,
            # even though it is not a subprocess with a recorded process code.
            record["exit_code"] = 0
        results.append(record)
    relations: list[dict[str, Any]] = []
    for item in results:
        relations.append({
            "id": f"action-result-{item['action_id']}-{item['id']}",
            "kind": "action_result",
            "from_id": item["action_id"],
            "to_id": item["id"],
            "call_id": item.get("call_id"),
            "locator": item.get("locator"),
        })
    for item in responses:
        if item.get("turn_id") is None:
            continue
        relations.append({
            "id": f"turn-response-{item['turn_id']}-{item['id']}",
            "kind": "turn_response",
            "from_id": item["turn_id"],
            "to_id": item["id"],
            "locator": item.get("locator"),
        })
    usage_by_message = {str(item["message_id"]): item for item in usage_rows if item.get("valid")}
    response_by_id = {str(item["id"]): item for item in responses}
    usage_facts: list[dict[str, Any]] = []
    for summary in visible_responses:
        row = usage_by_message.get(str(summary["id"]))
        if row is None:
            continue
        tokens = row.get("tokens", {})
        response_record = response_by_id.get(str(summary["id"]), {})
        usage_facts.append({
            "id": f"usage-{summary['id']}",
            "response_id": str(summary["id"]),
            "message_id": str(summary["id"]),
            "turn_id": response_record.get("turn_id"),
            "model_id": response_record.get("model_id"),
            "configuration": response_record.get("configuration"),
            "usage": {
                "input_tokens": int(tokens.get("input", 0)),
                "output_tokens": int(tokens.get("output", 0)),
                "reasoning_tokens": int(tokens.get("reasoning", 0)),
                "cache_read_tokens": int(tokens.get("cache_read", 0)),
                "cache_write_tokens": int(tokens.get("cache_write", 0)),
            },
            "time_created": summary.get("time_created"),
            "sequence": response_record.get("sequence"),
            "locator": summary.get("locator"),
        })

    second_user = user_messages[1] if len(user_messages) >= 2 else None
    first_user = user_messages[0] if user_messages else None
    final_response = visible_responses[-1] if visible_responses else None
    response_canaries = sorted({match.rstrip(".,;:)") for summary in visible_responses + user_messages for match in re.findall(r"SB_SURVIVAL_V1_[A-Za-z0-9_Δ🙂-]+", summary["text"])})
    model_values = sorted({f"{item['provider']}/{item['model']}" if item["provider"] and item["model"] and not item["model"].startswith(f"{item['provider']}/") else item["model"] for item in assistant_messages if item["model"]})
    model_facts = {
        "models": model_values,
        "provider_models": [{"provider": item["provider"], "model": item["model"]} for item in assistant_messages if item["provider"] and item["model"]],
    }
    token_rows = [item for item in usage_rows if item["valid"]]
    response_ids = {str(item["id"]) for item in visible_responses}
    response_usage_rows = [item for item in token_rows if item["message_id"] in response_ids]
    token_totals: dict[str, int] = defaultdict(int)
    for item in token_rows:
        tokens = item["tokens"]
        for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
            token_totals[key] += int(tokens[key])
    session_totals = {
        "input": session.get("tokens_input"),
        "output": session.get("tokens_output"),
        "reasoning": session.get("tokens_reasoning"),
        "cache_read": session.get("tokens_cache_read"),
        "cache_write": session.get("tokens_cache_write"),
    }
    reconciliation = bool(token_rows) and all(isinstance(session_totals[key], int) and session_totals[key] == token_totals[key] for key in token_totals)
    facts = {
        "turns": {"submitted": len(user_messages), "responses": len(visible_responses), "response_canaries": response_canaries, "first_user_id": first_user["id"] if first_user else None, "second_user_id": second_user["id"] if second_user else None, "final_response_id": final_response["id"] if final_response else None},
        "actions": {"count": len(tool_calls), "completed_results": sum(1 for item in tool_calls if item["status"] == "completed" and item["output_presence"] == "present")},
        "changed_files": {"count": len(file_changes), "paths": sorted(item["path"] for item in file_changes)},
        "revisions": {"r1": bool(first_user), "r2": bool(second_user), "r1_r2_order": bool(first_user and second_user and (first_user["time_created"], first_user["id"]) < (second_user["time_created"], second_user["id"])), "final_after_r2": bool(second_user and final_response and (second_user["time_created"], second_user["id"]) < (final_response["time_created"], final_response["id"]))},
        "model_config": model_facts | {"response_provider_models": [{"provider": item["provider"], "model": item["model"]} for item in visible_responses if item["provider"] and item["model"]]},
        "usage": {"messages": len(token_rows), "response_messages": len(response_usage_rows), "session_totals": session_totals, "message_totals": dict(token_totals)},
        "token_semantics": {"valid_messages": len(token_rows), "valid_response_messages": len(response_usage_rows), "valid": len(token_rows) == len(assistant_messages)},
        "reconciliation": {"matches_session_totals": reconciliation},
        "session": {"id": session["id"], "version": session["version"], "directory": session["directory"], "model": session["model"]},
    }
    return events, facts, {"messages": message_summaries, "parts": part_summaries, "tool_calls": tool_calls, "file_changes": file_changes, "usage": usage_rows, "turns": turns, "responses": responses, "actions": actions, "results": results, "relations": relations, "usage_facts": usage_facts}


def _build_metrics(
    facts: Mapping[str, Any],
    artifacts: Mapping[str, _BundleFile],
    *,
    supported: bool,
    diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    turns = facts["turns"]
    actions = facts["actions"]
    revisions = facts["revisions"]
    model_config = facts["model_config"]
    usage = facts["usage"]
    token_semantics = facts["token_semantics"]
    reconciliation = facts["reconciliation"]
    native = lambda count: "present" if count else "absent"
    evidence = lambda key: [{"kind": "native_fact", "fact": key}]
    metrics = {
        "work.submitted_turns": _metric_row("work.submitted_turns", decoded_eligible=turns["submitted"], availability=native(turns["submitted"]), value=turns["submitted"], evidence=evidence("turns.submitted"), supported=supported),
        "work.visible_responses": _metric_row("work.visible_responses", decoded_eligible=turns["responses"], availability=native(turns["responses"]), value=turns["responses"], evidence=evidence("turns.responses"), supported=supported),
        "work.actions": _metric_row("work.actions", decoded_eligible=actions["count"], availability=native(actions["count"]), value=actions["count"], evidence=evidence("actions.count"), supported=supported),
        "work.results": _metric_row("work.results", decoded_eligible=actions["completed_results"], availability=native(actions["completed_results"]), value=actions["completed_results"], evidence=evidence("actions.completed_results"), supported=supported),
        "work.changed_files": _metric_row("work.changed_files", decoded_eligible=facts["changed_files"]["count"], availability=native(facts["changed_files"]["count"]), value=facts["changed_files"]["paths"], evidence=evidence("changed_files.paths"), supported=supported),
        "causal.action_result": _metric_row("causal.action_result", decoded_eligible=actions["completed_results"], availability=native(actions["completed_results"]), value=actions["completed_results"] == actions["count"] and actions["count"] > 0, evidence=evidence("actions.completed_results"), supported=supported),
        "causal.turn_response": _metric_row("causal.turn_response", decoded_eligible=min(turns["submitted"], turns["responses"]), availability=native(min(turns["submitted"], turns["responses"])), value=min(turns["submitted"], turns["responses"]), evidence=evidence("turns.parent_ids"), supported=supported),
        "revision.r1": _metric_row("revision.r1", decoded_eligible=int(revisions["r1"]), availability="present" if revisions["r1"] else "unknown", value=revisions["r1"], evidence=evidence("revisions.r1"), supported=supported),
        "revision.r2": _metric_row("revision.r2", decoded_eligible=int(revisions["r2"]), availability="present" if revisions["r2"] else "unknown", value=revisions["r2"], evidence=evidence("revisions.r2"), supported=supported),
        "revision.r1_r2_order": _metric_row("revision.r1_r2_order", decoded_eligible=int(revisions["r1_r2_order"]), availability="present" if revisions["r1_r2_order"] else "unknown", value=revisions["r1_r2_order"], evidence=evidence("revisions.r1_r2_order"), supported=supported),
        "revision.final_after_r2": _metric_row("revision.final_after_r2", decoded_eligible=int(revisions["final_after_r2"]), availability="present" if revisions["final_after_r2"] else "unknown", value=revisions["final_after_r2"], evidence=evidence("revisions.final_after_r2"), supported=supported),
        "attribution.model_config": _metric_row("attribution.model_config", decoded_eligible=len(model_config["response_provider_models"]), availability=native(len(model_config["response_provider_models"])), value=model_config, evidence=evidence("model_config.response_provider_models"), supported=supported),
        "attribution.usage": _metric_row("attribution.usage", decoded_eligible=usage["response_messages"], availability=native(usage["response_messages"]), value=usage, evidence=evidence("usage.response_messages"), supported=supported),
        "attribution.token_semantics": _metric_row("attribution.token_semantics", decoded_eligible=token_semantics["valid_response_messages"], availability=native(token_semantics["valid_response_messages"]), value=token_semantics, evidence=evidence("usage.response_token_semantics"), supported=supported),
        "attribution.reconciliation": _metric_row("attribution.reconciliation", decoded_eligible=int(reconciliation["matches_session_totals"]), availability="present" if reconciliation["matches_session_totals"] else "unknown", value=reconciliation, evidence=evidence("usage.reconciliation"), supported=supported),
        # The copied native bundle proves the companion files and that this
        # decoder can replay them.  It does not prove a complete project root
        # or equality against an independently produced canonical export.
        "portable.complete_root": _metric_row("portable.complete_root", availability="unknown", value=None, evidence=evidence("bundle.complete_root.unknown"), supported=supported),
        "portable.companions": _metric_row("portable.companions", decoded_eligible=1, availability="present", value={"wal": True, "shm": True}, evidence=evidence("bundle.wal_shm"), supported=supported),
        "portable.isolated_decode": _metric_row("portable.isolated_decode", decoded_eligible=int(supported), availability="present" if supported else "unknown", value=supported, evidence=evidence("decoder.copy"), supported=supported),
        "portable.canonical_equality": _metric_row("portable.canonical_equality", availability="unknown", value=None, evidence=evidence("canonical_equality.unavailable"), supported=supported),
    }
    if diagnostics and supported:
        # A malformed row means the native population is no longer complete;
        # keep the known counts but make the comparison state fail closed.
        for item in metrics.values():
            item["state"] = "decoder_unsupported"
    return metrics


def decode_opencode_bundle(bundle: Path | str, *, session_id: str | None = None) -> dict[str, Any]:
    """Decode one explicitly supplied copied ``opencode.db``/WAL/SHM bundle.

    The source bundle is never opened by SQLite and is never modified.  The
    function raises for an invalid bundle boundary or SQLite schema; malformed
    logical JSON rows are retained as diagnostics and produce blocking metric
    states instead of fabricated records.
    """

    root = _normal_path(bundle)
    declared = _bundle_files(root)
    diagnostics: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="session-bench-opencode-decoder-") as directory:
        clone_root = Path(directory)
        copied = _copy_bundle(root, clone_root, declared=declared)
        try:
            connection = sqlite3.connect(f"file:{clone_root / OPENCODE_DB}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if journal_mode != "wal":
                    raise OpenCodeDecoderError(f"OpenCode copied database is not WAL-backed: {journal_mode!r}")
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
                required_tables = {"session", "message", "part"}
                if not required_tables <= tables:
                    raise OpenCodeDecoderError(f"OpenCode SQLite schema lacks required tables: {sorted(required_tables - tables)}")
                candidates = _session_candidates(connection)
                if session_id is None:
                    if len(candidates) != 1:
                        raise OpenCodeDecoderError(f"copied OpenCode bundle must contain exactly one session, found {len(candidates)}")
                    selected_session = candidates[0]
                else:
                    if not isinstance(session_id, str) or not session_id:
                        raise OpenCodeDecoderError("session_id must be a non-empty string")
                    selected_session = session_id
                sessions, messages, parts, row_diagnostics = _read_rows(connection, selected_session, copied)
                diagnostics.extend(row_diagnostics)
                version = sessions[0].get("version")
                if not isinstance(version, str) or not version.startswith(OPENCODE_SUPPORTED_VERSION_PREFIX):
                    diagnostics.append(_diagnostic("unsupported_version", f"OpenCode version is not supported by this decoder: {version!r}"))
            finally:
                connection.close()
        except OpenCodeDecoderError:
            raise
        except sqlite3.DatabaseError as exc:
            raise OpenCodeDecoderError(f"cannot replay copied OpenCode SQLite/WAL bundle: {exc}") from exc
        session = sessions[0]
        events, facts, normalized = _decode_rows(session, messages, parts, copied, diagnostics)
        supported = not diagnostics
        metrics = _build_metrics(facts, copied, supported=supported, diagnostics=diagnostics)
        # Include a compact, stable event stream.  Raw OpenCode metadata such
        # as encrypted reasoning payloads and account/credential tables never
        # enters this result.
        result = {
            "schema_version": OPENCODE_DECODER_SCHEMA,
            "format": OPENCODE_FORMAT,
            "supported": supported,
            "session_id": selected_session,
            "bundle": {"path": root.name, "files": [copied[name].display() for name in OPENCODE_BUNDLE_FILES], "journal_mode": "wal"},
            "session": {key: value for key, value in session.items() if key not in {"project_id", "workspace_id", "parent_id", "slug", "title"}},
            "events": events,
            "messages": normalized["messages"],
            "parts": normalized["parts"],
            "turns": normalized["turns"],
            "responses": normalized["responses"],
            "actions": normalized["actions"],
            "results": normalized["results"],
            "relations": normalized["relations"],
            "usage": normalized["usage_facts"],
            "file_changes": normalized["file_changes"],
            "facts": facts,
            "metrics": metrics,
            "survival_metrics": [metrics[metric_id] for metric_id in _METRIC_IDS],
            "diagnostics": diagnostics,
            "unknown_records": len(diagnostics),
        }
        return result


def decode_opencode_sqlite(bundle: Path | str, *, session_id: str | None = None) -> dict[str, Any]:
    """Compatibility spelling for callers that name the storage format."""

    return decode_opencode_bundle(bundle, session_id=session_id)


decode_native = decode_opencode_bundle
decode = decode_opencode_bundle
decode_opencode_native = decode_opencode_bundle


__all__ = [
    "OPENCODE_BUNDLE_FILES",
    "OPENCODE_DECODER_SCHEMA",
    "OPENCODE_FORMAT",
    "OPENCODE_SUPPORTED_VERSION_PREFIX",
    "OpenCodeDecoderError",
    "decode",
    "decode_native",
    "decode_opencode_bundle",
    "decode_opencode_native",
    "decode_opencode_sqlite",
]
