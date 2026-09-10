"""Small, native-only decoders for the v1 constructed fixture formats.

The decoder deliberately has no expected-answer input and performs no network or
vendor-client work.  Package validation is strict; malformed native content is
reported as diagnostic evidence rather than turned into a successful answer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any
import stat


_FORMATS = {"constructed-jsonl-v1", "constructed-sqlite-v1"}
_KINDS = {"message", "tool_call", "tool_result", "session", "branch", "attachment"}
_ARTIFACT_KEYS = {"id", "path", "sha256", "size_bytes", "depends_on"}
_MAX_FILES = 256
_MAX_BYTES = 16 * 1024 * 1024
_MAX_RECORDS = 100_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _diagnostic(code: str, artifact_id: str | None, detail: str) -> dict[str, str | None]:
    return {"code": code, "artifact_id": artifact_id, "detail": detail}


def _strict_json(value: str | bytes) -> Any:
    def reject_constant(token: str) -> Any:
        raise ValueError(f"non-finite JSON number: {token}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("artifact path must be a non-empty relative POSIX path")
    path = Path(value)
    if path.is_absolute() or path == Path(".") or ".." in path.parts or path.as_posix() != value or "." in path.parts:
        raise ValueError("artifact path escapes package")
    return path


def _reject_symlinks(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"symlink is not allowed: {relative}")


def _validate_package(package: Path) -> tuple[str, list[dict[str, Any]], dict[str, Path]]:
    if not package.is_dir() or package.is_symlink():
        raise ValueError("package must be a real directory")
    manifest_path = package / "decode.json"
    _reject_symlinks(package, Path("decode.json"))
    if not manifest_path.is_file():
        raise ValueError("missing decode.json")
    try:
        manifest = _strict_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid decode.json: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"format", "artifacts"}:
        raise ValueError("decode.json must have exactly format and artifacts keys")
    fmt = manifest["format"]
    if fmt not in _FORMATS:
        raise ValueError("unsupported native format")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a list")
    if len(artifacts) > _MAX_FILES:
        raise ValueError(f"package exceeds {_MAX_FILES} artifact limit")

    records: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != _ARTIFACT_KEYS:
            raise ValueError("artifact has unexpected keys")
        artifact_id = item["id"]
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in paths:
            raise ValueError("artifact ids must be unique non-empty strings")
        relative = _safe_relative(item["path"])
        digest = item["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid digest for {artifact_id}")
        if isinstance(item["size_bytes"], bool) or not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0:
            raise ValueError(f"invalid size for {artifact_id}")
        if item["size_bytes"] > _MAX_BYTES:
            raise ValueError(f"artifact exceeds {_MAX_BYTES}-byte limit: {artifact_id}")
        dependencies = item["depends_on"]
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise ValueError(f"invalid dependencies for {artifact_id}")
        _reject_symlinks(package, relative)
        path = package / relative
        if relative == Path("decode.json") or relative in {item["_relative"] for item in records}:
            raise ValueError("artifact paths must be unique and cannot be decode.json")
        paths[artifact_id] = path
        records.append({**item, "_relative": relative})

    ids = set(paths)
    for item in records:
        if any(dep not in ids for dep in item["depends_on"]):
            raise ValueError(f"undeclared dependency for {item['id']}")

    declared = {Path("decode.json"), *(item["_relative"] for item in records)}
    for candidate in package.rglob("*"):
        relative = candidate.relative_to(package)
        _reject_symlinks(package, relative)
        if candidate.is_dir():
            continue
        if relative not in declared:
            raise ValueError(f"undeclared package file: {relative}")

    for item in records:
        path = paths[item["id"]]
        if path.exists() and not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"declared artifact is not a regular file: {item['id']}")
        if not path.exists():
            continue
        if path.stat().st_size > _MAX_BYTES:
            raise ValueError(f"artifact exceeds {_MAX_BYTES}-byte limit: {item['id']}")
        if path.stat().st_size != item["size_bytes"] or _sha256(path) != item["sha256"]:
            raise ValueError(f"digest or size mismatch for {item['id']}")
    return fmt, records, paths


def _event(row: dict[str, Any], locator: dict[str, Any]) -> dict[str, Any] | None:
    if set(row) != {"id", "session_id", "kind", "fields"}:
        return None
    if row["id"] is not None and not isinstance(row["id"], str):
        return None
    if not isinstance(row["session_id"], str) or not isinstance(row["kind"], str) or not isinstance(row["fields"], dict):
        return None
    event_id = row["id"] or None
    return {"id": event_id, "session_id": row["session_id"], "kind": row["kind"], "fields": row["fields"], "locator": locator, "identity_origin": "native" if event_id is not None else "derived"}


def _postprocess(events: list[dict[str, Any]], diagnostics: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    parents: dict[tuple[str, str], tuple[str, str]] = {}
    calls: set[tuple[str, str]] = set()
    for event in events:
        event_id = event["id"]
        if event_id is not None:
            identity = (event["session_id"], event_id)
            if identity in seen:
                diagnostics.append(_diagnostic("duplicate_id", event["locator"]["artifact_id"], f"duplicate event id: {event_id}"))
            seen.add(identity)
        if event["kind"] not in _KINDS:
            diagnostics.append(_diagnostic("unknown_record", event["locator"]["artifact_id"], f"unknown kind: {event['kind']}"))
        if event["kind"] == "branch":
            parent = event["fields"].get("parent_id")
            if isinstance(event_id, str) and isinstance(parent, str):
                parents[(event["session_id"], event_id)] = (event["session_id"], parent)
        if event["kind"] == "tool_call" and isinstance(event_id, str):
            calls.add((event["session_id"], event_id))
            parent_call = event["fields"].get("parent_call_id")
            if isinstance(parent_call, str):
                parents[(event["session_id"], event_id)] = (event["session_id"], parent_call)
    known = {(event["session_id"], event["id"]) for event in events if event["id"] is not None}
    for child, parent in parents.items():
        if parent not in known:
            diagnostics.append(_diagnostic("missing_parent", next(e["locator"]["artifact_id"] for e in events if (e["session_id"], e["id"]) == child), f"missing parent: {parent[1]}"))
    for start in parents:
        chain: set[tuple[str, str]] = set()
        current = start
        while current in parents:
            if current in chain:
                diagnostics.append(_diagnostic("branch_cycle", next(e["locator"]["artifact_id"] for e in events if (e["session_id"], e["id"]) == start), f"cycle at: {current[1]}"))
                break
            chain.add(current)
            current = parents[current]
    for event in events:
        if event["kind"] == "tool_result":
            call_id = event["fields"].get("tool_call_id", event["fields"].get("call_id"))
            if isinstance(call_id, str) and (event["session_id"], call_id) not in calls:
                diagnostics.append(_diagnostic("dangling_tool_result", event["locator"]["artifact_id"], f"missing tool call: {call_id}"))


def _decode_jsonl(records: list[dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    unknown = 0
    record_count = 0
    for item in records:
        path = paths[item["id"]]
        if not path.exists():
            diagnostics.append(_diagnostic("missing_artifact", item["id"], f"missing native artifact: {item['path']}"))
            continue
        for dependency in item["depends_on"]:
            if not paths[dependency].exists():
                diagnostics.append(_diagnostic("missing_dependency", item["id"], f"missing dependency: {dependency}"))
        if path.suffix.lower() != ".jsonl":
            continue
        with path.open("rb") as handle:
            byte_start = 0
            for line_number, raw in enumerate(handle, 1):
                byte_end = byte_start + len(raw)
                content = raw.rstrip(b"\r\n")
                if not content.strip():
                    byte_start = byte_end
                    continue
                record_digest = hashlib.sha256(content).hexdigest()
                locator = {"artifact_id": item["id"], "sha256": item["sha256"], "line": line_number, "byte_start": byte_start, "byte_end": byte_end, "record_sha256": record_digest}
                try:
                    row = _strict_json(content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    diagnostics.append(_diagnostic("malformed_record", item["id"], f"line {line_number}: {exc}"))
                    byte_start = byte_end
                    continue
                if not isinstance(row, dict):
                    diagnostics.append(_diagnostic("malformed_record", item["id"], f"line {line_number}: record is not an object"))
                else:
                    event = _event(row, locator)
                    if event is None:
                        if isinstance(row, dict) and "id" not in row:
                            diagnostics.append(_diagnostic("missing_event_id", item["id"], f"line {line_number}: id field missing"))
                        diagnostics.append(_diagnostic("malformed_record", item["id"], f"line {line_number}: invalid event shape"))
                    else:
                        record_count += 1
                        if record_count > _MAX_RECORDS:
                            raise ValueError(f"JSONL exceeds {_MAX_RECORDS}-record limit")
                        if event["id"] is None:
                            diagnostics.append(_diagnostic("missing_event_id", item["id"], f"line {line_number}: event id missing or empty"))
                        events.append(event)
                        if event["kind"] not in _KINDS:
                            unknown += 1
                byte_start = byte_end
    _postprocess(events, diagnostics)
    return {"format": "constructed-jsonl-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}


def _decode_sqlite(records: list[dict[str, Any]], paths: dict[str, Path]) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    unknown = 0
    record_count = 0
    if not records:
        return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
    primaries = [item for item in records if Path(item["path"]).suffix.lower() in {".db", ".sqlite", ".sqlite3"}]
    if not primaries:
        diagnostics.append(_diagnostic("unsupported_schema", None, "no SQLite database artifact declared"))
        return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
    if len(primaries) > 1:
        diagnostics.append(_diagnostic("unsupported_schema", None, "multiple SQLite database artifacts are unsupported"))
        return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
    primary = primaries[0]
    for item in records:
        if not paths[item["id"]].exists():
            diagnostics.append(_diagnostic("missing_artifact", item["id"], f"missing native artifact: {item['path']}"))
        for dependency in item["depends_on"]:
            if not paths[dependency].exists():
                diagnostics.append(_diagnostic("missing_dependency", item["id"], f"missing dependency: {dependency}"))
    if not paths[primary["id"]].exists():
        return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
    with tempfile.TemporaryDirectory(prefix="session-bench-sqlite-") as temp:
        clone_root = Path(temp)
        for item in records:
            if not paths[item["id"]].exists():
                continue
            target = clone_root / Path(item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(paths[item["id"]], target)
        clone = clone_root / Path(primary["path"])
        dependency_digests = [item["sha256"] for item in records if item["id"] != primary["id"] and Path(item["path"]).as_posix() in {Path(primary["path"]).as_posix() + "-wal", Path(primary["path"]).as_posix() + "-shm"}]
        try:
            connection = sqlite3.connect(f"file:{clone}?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("PRAGMA query_only=ON")
                schema = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name").fetchall()
                if len(schema) != 1 or schema[0][0:2] != ("table", "events"):
                    diagnostics.append(_diagnostic("unsupported_schema", primary["id"], "unexpected SQLite tables, views, triggers, or virtual objects"))
                    return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
                columns = connection.execute("PRAGMA table_xinfo(events)").fetchall()
                expected = [(0, "row_key", "INTEGER", 0, None, 1, 0), (1, "payload", "TEXT", 0, None, 0, 0)]
                if columns != expected:
                    diagnostics.append(_diagnostic("unsupported_schema", primary["id"], "events schema must be exactly row_key INTEGER PRIMARY KEY and payload TEXT"))
                    return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
                rows = connection.execute("SELECT row_key, payload FROM events ORDER BY row_key").fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            diagnostics.append(_diagnostic("unsupported_schema", primary["id"], str(exc)))
            return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}
    for row_key, payload in rows:
        raw_payload = payload.encode("utf-8") if isinstance(payload, str) else (bytes(payload) if payload is not None else b"")
        locator = {"artifact_id": primary["id"], "sha256": primary["sha256"], "table": "events", "row_key": row_key, "column": "payload", "record_sha256": hashlib.sha256(raw_payload).hexdigest(), "dependency_sha256": dependency_digests}
        try:
            if payload is None:
                raise ValueError("NULL payload")
            row = _strict_json(payload)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            diagnostics.append(_diagnostic("malformed_record", primary["id"], f"row {row_key}: {exc}"))
            continue
        if not isinstance(row, dict):
            diagnostics.append(_diagnostic("malformed_record", primary["id"], f"row {row_key}: record is not an object"))
            continue
        event = _event(row, locator)
        if event is None:
            if isinstance(row, dict) and "id" not in row:
                diagnostics.append(_diagnostic("missing_event_id", primary["id"], f"row {row_key}: id field missing"))
            diagnostics.append(_diagnostic("malformed_record", primary["id"], f"row {row_key}: invalid event shape"))
            continue
        events.append(event)
        record_count += 1
        if record_count > _MAX_RECORDS:
            raise ValueError(f"SQLite exceeds {_MAX_RECORDS}-record limit")
        if event["id"] is None:
            diagnostics.append(_diagnostic("missing_event_id", primary["id"], f"row {row_key}: event id missing or empty"))
        if event["kind"] not in _KINDS:
            unknown += 1
    _postprocess(events, diagnostics)
    return {"format": "constructed-sqlite-v1", "events": events, "diagnostics": diagnostics, "unknown_records": unknown}


def decode_native(package: Path) -> dict[str, Any]:
    """Decode only declared native files from a validated fixture package."""
    package = Path(package)
    fmt, records, paths = _validate_package(package)
    if fmt == "constructed-jsonl-v1":
        return _decode_jsonl(records, paths)
    return _decode_sqlite(records, paths)
