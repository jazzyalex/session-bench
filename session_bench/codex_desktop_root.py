"""Export one bounded Codex Desktop session family from copied inputs.

Codex Desktop keeps the useful parts of a task in more than one native store.
The task rollout and shell snapshot are line-oriented files, while the task
index and the two SQLite stores are shared stores.  This module is the small
boundary between those inputs and an offline evidence package:

* callers provide every input path and the exact thread id;
* database reads are read-only and every data query is keyed to that id;
* only the selected thread row and rows from known child tables are exported;
* the JSONL index is filtered to exactly one matching identity;
* paths in the output are stable labels, never source filesystem paths; and
* the result is labelled as a per-thread sanitized derivative.  It is not a
  byte-for-byte copy of either native database.

The raw databases and the complete JSONL index remain private shared stores.
The caller still owns content redaction before publication; this module keeps
the public derivative closed to one selected thread and does not publish the
shared database or index.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
from typing import Any, Iterable, Mapping
from urllib.parse import quote


class CodexDesktopRootError(ValueError):
    """The copied Codex Desktop family is incomplete or inconsistent."""


SCHEMA_VERSION = "session-bench.codex-desktop-family-v1"

# These are the tables understood by the current Codex Desktop persistence
# family.  SQLite's own bookkeeping table is tolerated, but never queried for
# rows.  An application table outside this list is a schema drift signal.
STATE_TABLES = frozenset(
    {
        "threads",
        "thread_artifacts",
        "dynamic_tools",
        "thread_dynamic_tools",
        "spawn_edges",
        "thread_spawn_edges",
        "realtime",
    }
)
STATE_IGNORED_TABLES = frozenset(
    {
        "_sqlx_migrations",
        "backfill_state",
        "external_agent_config_imports",
        "project_idempotency_keys",
        "project_roots",
        "projects",
        "remote_control_enrollments",
        "rollout_migration_skipped_rollouts",
        "rollout_migration_state",
        "thread_sections",
    }
)
HISTORY_TABLES = frozenset(
    {
        "thread_turns",
        "thread_items",
        "thread_realtime_items",
        "realtime",
        "thread_history_projection_state",
    }
)
HISTORY_IGNORED_TABLES = frozenset({"_sqlx_migrations"})
REQUIRED_STATE_TABLES = frozenset({"threads"})
REQUIRED_HISTORY_TABLES = frozenset({"thread_turns", "thread_items"})
SQLITE_TABLES = frozenset({"sqlite_sequence", "sqlite_stat1", "sqlite_stat4", "sqlite_stat3"})

_THREAD_ID_KEYS = frozenset(
    {
        "thread_id",
        "threadid",
        "thread_ids",
        "threadids",
        "parent_thread_id",
        "parentthreadid",
        "source_thread_id",
        "sourcethreadid",
        "child_thread_id",
        "childthreadid",
        "root_thread_id",
        "rootthreadid",
        "related_thread_id",
        "relatedthreadid",
    }
)
_THREAD_COLUMN_ALIASES = ("thread_id", "threadid", "thread", "parent_thread_id")
_IDENTITY_COLUMN_ALIASES = ("thread_id", "threadid", "id")


def _fail(message: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise CodexDesktopRootError(message)
    raise CodexDesktopRootError(message) from cause


def _regular_file(value: Path | str, label: str) -> Path:
    path = Path(value).expanduser()
    try:
        info = path.lstat()
    except OSError as exc:
        _fail(f"{label} is not readable", exc)
    if stat.S_ISLNK(info.st_mode):
        _fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        _fail(f"{label} must be a regular file")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb", buffering=0) as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    except OSError as exc:
        _fail(f"cannot read copied input {path.name}", exc)
    return digest.hexdigest()


def _strict_json(value: bytes | str, label: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"{label} contains duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(token: str) -> Any:
        _fail(f"{label} contains non-finite JSON number {token}")

    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicate,
            parse_constant=reject_constant,
        )
    except CodexDesktopRootError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"{label} is malformed JSON", exc)


def _validate_rollout(path: Path) -> int:
    """Parse the copied JSONL so malformed records cannot enter a package."""

    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("cannot read copied rollout", exc)
    if not raw:
        _fail("copied rollout is empty")
    count = 0
    try:
        lines = raw.splitlines()
        for number, line in enumerate(lines, 1):
            if not line.strip():
                _fail(f"rollout line {number} is empty")
            _strict_json(line, f"rollout line {number}")
            count += 1
    except CodexDesktopRootError:
        raise
    except UnicodeDecodeError as exc:
        _fail("copied rollout is not UTF-8 JSONL", exc)
    if count == 0:
        _fail("copied rollout has no records")
    return count


def _validate_shell_snapshot(path: Path) -> int:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail("cannot read copied shell snapshot", exc)
    if not raw:
        _fail("copied shell snapshot is empty")
    if b"\x00" in raw:
        _fail("copied shell snapshot contains NUL bytes")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("copied shell snapshot is not UTF-8 text", exc)
    return len(raw.splitlines())


def _normalized_key(value: str) -> str:
    return value.replace("-", "_").replace(" ", "_").lower()


def _is_thread_id_key(value: str) -> bool:
    normalized = _normalized_key(value)
    return normalized in _THREAD_ID_KEYS or (
        normalized.endswith("_thread_id") and normalized != "thread_id"
    )


def _json_safe(value: Any) -> Any:
    """Convert SQLite scalar values into values permitted by JSON."""

    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, memoryview):
        return {"encoding": "base64", "data": base64.b64encode(value.tobytes()).decode("ascii")}
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not (value == value and abs(value) != float("inf")):
            _fail("SQLite row contains a non-finite number")
        return value
    _fail(f"SQLite row contains an unsupported scalar type: {type(value).__name__}")


def _check_foreign_thread_ids(value: Any, *, table: str, key_path: str = "") -> None:
    """Reject selected row data that carries a second thread identity."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            path = f"{key_path}.{key}" if key_path else key
            if _is_thread_id_key(key):
                values = item if isinstance(item, list) else [item]
                for candidate in values:
                    if candidate is None:
                        continue
                    if not isinstance(candidate, str):
                        _fail(f"{table} row has a non-string thread identity at {path}")
                    # The target is supplied by the caller at the outer
                    # function; this sentinel is replaced by the closure in
                    # _assert_row_thread_identity.
            _check_foreign_thread_ids(item, table=table, key_path=path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_foreign_thread_ids(item, table=table, key_path=f"{key_path}[{index}]")


def _assert_row_thread_identity(row: Mapping[str, Any], *, table: str, thread_id: str) -> None:
    """Check all explicit thread-id fields in a selected row."""

    def visit(value: Any, key_path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str):
                    continue
                path = f"{key_path}.{key}" if key_path else key
                if _is_thread_id_key(key):
                    candidates = item if isinstance(item, list) else [item]
                    for candidate in candidates:
                        if candidate is None:
                            continue
                        if not isinstance(candidate, str) or candidate != thread_id:
                            _fail(f"{table} row contains another thread ID at {path}")
                visit(item, path)
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{key_path}[{index}]")

    visit(row)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _open_sqlite(path: Path, label: str) -> sqlite3.Connection:
    # ``mode=ro`` prevents SQLite from creating a missing DB or mutating the
    # copied source.  URI quoting also handles spaces without shell parsing.
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection
    except (OSError, sqlite3.Error) as exc:
        _fail(f"{label} is not a readable SQLite database", exc)


def _table_names(connection: sqlite3.Connection, label: str) -> tuple[str, ...]:
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
        ).fetchall()
    except sqlite3.Error as exc:
        _fail(f"cannot inspect {label} schema", exc)
    names: list[str] = []
    for row in rows:
        name = row[0]
        if not isinstance(name, str) or not name:
            _fail(f"{label} has an invalid table name")
        names.append(name)
    return tuple(names)


def _columns(connection: sqlite3.Connection, table: str, label: str) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    except sqlite3.Error as exc:
        _fail(f"cannot inspect {label}.{table} schema", exc)
    if not rows:
        _fail(f"{label}.{table} has no usable schema")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for row in rows:
        name = row[1]
        if not isinstance(name, str) or not name or name.lower() in names:
            _fail(f"{label}.{table} has duplicate or invalid column names")
        names.add(name.lower())
        result.append(
            {
                "cid": int(row[0]),
                "name": name,
                "type": row[2] if isinstance(row[2], str) else "",
                "notnull": bool(row[3]),
                "default": row[4],
                "primary_key": int(row[5]),
            }
        )
    return result


def _key_column(table: str, columns: Iterable[Mapping[str, Any]], *, is_thread_table: bool) -> str:
    by_normalized = {_normalized_key(str(item["name"])): str(item["name"]) for item in columns}
    aliases = _IDENTITY_COLUMN_ALIASES if is_thread_table else _THREAD_COLUMN_ALIASES
    for alias in aliases:
        if alias in by_normalized:
            return by_normalized[alias]
    _fail(f"{table} has no supported thread identity column")


def _read_selected_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: list[dict[str, Any]],
    *,
    thread_id: str,
    label: str,
    exact_thread_row: bool,
) -> list[dict[str, Any]]:
    key = _key_column(table, columns, is_thread_table=exact_thread_row)
    query = (
        f"SELECT * FROM {_quote_identifier(table)} "
        # ORDER BY the first projected column rather than rowid: a copied
        # SQLite table may legitimately be declared WITHOUT ROWID.
        f"WHERE {_quote_identifier(key)} = ? ORDER BY 1"
    )
    try:
        rows = connection.execute(query, (thread_id,)).fetchall()
    except sqlite3.Error as exc:
        _fail(f"cannot query selected rows from {label}.{table}", exc)
    selected: list[dict[str, Any]] = []
    for row in rows:
        materialized = {str(key_name): _json_safe(row[key_name]) for key_name in row.keys()}
        _assert_row_thread_identity(materialized, table=table, thread_id=thread_id)
        selected.append(materialized)
    if exact_thread_row and len(selected) != 1:
        _fail(f"{label}.{table} must contain exactly one thread identity; found {len(selected)}")
    return selected


def _schema_document(
    path: Path,
    *,
    label: str,
    allowed_tables: frozenset[str],
    ignored_tables: frozenset[str],
    required_tables: frozenset[str],
    thread_id: str,
) -> dict[str, Any]:
    connection = _open_sqlite(path, label)
    try:
        names = _table_names(connection, label)
        lower_to_actual = {name.lower(): name for name in names}
        unexpected = sorted(
            name for name in names
            if name.lower()
            not in {item.lower() for item in allowed_tables | ignored_tables | SQLITE_TABLES}
        )
        if unexpected:
            _fail(f"{label} has unexpected tables: {unexpected}")
        required_missing = sorted(
            table for table in required_tables if table.lower() not in lower_to_actual
        )
        if required_missing:
            _fail(f"{label} is missing required tables: {required_missing}")

        table_docs: dict[str, Any] = {}
        ordered_known = sorted(
            (name for name in names if name.lower() in {item.lower() for item in allowed_tables}),
            key=str.lower,
        )
        for actual in ordered_known:
            canonical = actual.lower()
            allowed = {item.lower() for item in allowed_tables}
            if canonical not in allowed:
                _fail(f"{label} has unexpected table: {actual}")
            table_columns = _columns(connection, actual, label)
            selected = _read_selected_rows(
                connection,
                actual,
                table_columns,
                thread_id=thread_id,
                label=label,
                exact_thread_row=canonical == "threads",
            )
            table_docs[actual] = {
                "columns": table_columns,
                "identity_column": _key_column(
                    actual,
                    table_columns,
                    is_thread_table=canonical == "threads",
                ),
                "rows": selected,
            }
        return {
            "label": label,
            "classification": "private_shared_store",
            "byte_for_byte_copy": False,
            "tables": table_docs,
            "table_names": list(ordered_known),
        }
    finally:
        connection.close()


def _matching_session_index(path: Path, thread_id: str) -> tuple[dict[str, Any], int]:
    matches: list[dict[str, Any]] = []
    total = 0
    try:
        handle = path.open("rb")
    except OSError as exc:
        _fail("cannot read copied session index", exc)
    with handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip():
                _fail(f"session index line {number} is empty")
            value = _strict_json(raw, f"session index line {number}")
            if not isinstance(value, dict):
                _fail(f"session index line {number} is not a JSON object")
            total += 1
            explicit = [
                item
                for key, item in value.items()
                if isinstance(key, str) and _normalized_key(key) in {"thread_id", "threadid"}
            ]
            if explicit:
                if any(item == thread_id for item in explicit):
                    matches.append(value)
                continue
            # Older index records use `id` as the thread identity.  Use this
            # fallback only when an explicit thread_id field is absent.
            if value.get("id") == thread_id:
                matches.append(value)
    if len(matches) != 1:
        _fail(f"session index must contain exactly one matching thread identity; found {len(matches)}")
    _assert_row_thread_identity(matches[0], table="session_index", thread_id=thread_id)
    return matches[0], total


def _source_file(label: str, path: Path, *, sha256: str, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "label": label,
        "classification": "private_shared_store" if label in {"state_5.sqlite", "thread_history_1.sqlite", "session_index.jsonl"} else "private_copied_input",
        "size_bytes": path.stat().st_size,
        "sha256": sha256,
    }
    if extra:
        result.update(extra)
    return result


def export_codex_desktop_session_family(
    rollout_path: Path | str,
    shell_snapshot_path: Path | str,
    state_5_path: Path | str,
    thread_history_1_path: Path | str,
    session_index_path: Path | str,
    thread_id: str,
    *,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build a closed per-thread Codex Desktop family document.

    All six paths must be copied regular files.  The function reads the
    rollout and index to validate their JSON syntax, queries only rows whose
    supported identity column equals ``thread_id``, and returns a plain JSON
    object.  If ``output_path`` is supplied it is created once and never
    overwritten.
    """

    if not isinstance(thread_id, str) or not thread_id or "\x00" in thread_id:
        _fail("thread_id must be a non-empty string")
    rollout = _regular_file(rollout_path, "rollout")
    shell = _regular_file(shell_snapshot_path, "shell snapshot")
    state = _regular_file(state_5_path, "state_5.sqlite")
    history = _regular_file(thread_history_1_path, "thread_history_1.sqlite")
    index = _regular_file(session_index_path, "session_index.jsonl")

    rollout_count = _validate_rollout(rollout)
    shell_line_count = _validate_shell_snapshot(shell)
    rollout_hash = _sha256(rollout)
    shell_hash = _sha256(shell)
    index_hash = _sha256(index)
    state_hash = _sha256(state)
    history_hash = _sha256(history)
    index_record, index_record_count = _matching_session_index(index, thread_id)

    state_doc = _schema_document(
        state,
        label="state_5.sqlite",
        allowed_tables=STATE_TABLES,
        ignored_tables=STATE_IGNORED_TABLES,
        required_tables=REQUIRED_STATE_TABLES,
        thread_id=thread_id,
    )
    history_doc = _schema_document(
        history,
        label="thread_history_1.sqlite",
        allowed_tables=HISTORY_TABLES,
        ignored_tables=HISTORY_IGNORED_TABLES,
        required_tables=REQUIRED_HISTORY_TABLES,
        thread_id=thread_id,
    )

    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codex_desktop_per_thread_derivative",
        "thread_id": thread_id,
        "classification": {
            "raw_sources": "private_shared_stores",
            "derivative": "sanitized_per_thread_export",
            "byte_for_byte_native_database_copy": False,
            "other_thread_rows_exported": False,
        },
        "inputs": {
            "rollout": _source_file(
                "rollout.jsonl",
                rollout,
                sha256=rollout_hash,
                extra={"record_count": rollout_count},
            ),
            "shell_snapshot": _source_file(
                "shell_snapshot.sh",
                shell,
                sha256=shell_hash,
                extra={"line_count": shell_line_count},
            ),
            "state_5": _source_file("state_5.sqlite", state, sha256=state_hash),
            "thread_history_1": _source_file(
                "thread_history_1.sqlite", history, sha256=history_hash
            ),
            "session_index": _source_file(
                "session_index.jsonl",
                index,
                sha256=index_hash,
                extra={"record_count": index_record_count},
            ),
        },
        "sources": {
            "state_5.sqlite": state_doc,
            "thread_history_1.sqlite": history_doc,
            "session_index.jsonl": {
                "label": "session_index.jsonl",
                "classification": "private_shared_store",
                "matching_record": index_record,
                "matching_identity_count": 1,
            },
        },
        # Convenience aliases make the selected native records easy to find
        # in a report without requiring consumers to understand source paths.
        "thread": state_doc["tables"][next(name for name in state_doc["tables"] if name.lower() == "threads")]["rows"][0],
        "thread_artifacts": state_doc["tables"].get("thread_artifacts", {"rows": []})["rows"],
        "dynamic_tools": [
            *state_doc["tables"].get("dynamic_tools", {"rows": []})["rows"],
            *state_doc["tables"].get("thread_dynamic_tools", {"rows": []})["rows"],
        ],
        "spawn_edges": (
            state_doc["tables"].get(
                "spawn_edges", state_doc["tables"].get("thread_spawn_edges", {"rows": []})
            )["rows"]
        ),
        "thread_turns": history_doc["tables"].get("thread_turns", {"rows": []})["rows"],
        "thread_items": history_doc["tables"].get("thread_items", {"rows": []})["rows"],
        "realtime": [
            *state_doc["tables"].get("realtime", {"rows": []})["rows"],
            *history_doc["tables"].get("realtime", {"rows": []})["rows"],
            *history_doc["tables"].get("thread_realtime_items", {"rows": []})["rows"],
        ],
        "history_projection": history_doc["tables"].get(
            "thread_history_projection_state", {"rows": []}
        )["rows"],
    }

    try:
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _fail("export is not a closed JSON document", exc)

    if output_path is not None:
        destination = Path(output_path).expanduser()
        if destination.exists() or destination.is_symlink():
            _fail("output path already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_text(encoded + "\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            _fail("cannot write closed export", exc)
    return document


def validate_codex_desktop_session_family(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Compatibility spelling for callers that use ``validate`` semantics."""

    return export_codex_desktop_session_family(*args, **kwargs)


def export_codex_desktop_family(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Short alias retained for the adapter and report-generation layers."""

    return export_codex_desktop_session_family(*args, **kwargs)


__all__ = [
    "CodexDesktopRootError",
    "SCHEMA_VERSION",
    "STATE_TABLES",
    "STATE_IGNORED_TABLES",
    "HISTORY_TABLES",
    "HISTORY_IGNORED_TABLES",
    "export_codex_desktop_session_family",
    "export_codex_desktop_family",
    "validate_codex_desktop_session_family",
]
