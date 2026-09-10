"""Resolve and validate native source locators for decoder and inspection claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .bundle import canonical, digest, safe_path, safe_read


def _strict_json(value: bytes) -> Any:
    def duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    def nonfinite(token: str) -> Any:
        raise ValueError(f"nonfinite JSON number: {token}")

    return json.loads(value, object_pairs_hook=duplicate, parse_constant=nonfinite)


def _inventory(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["id"]: a for a in manifest["artifacts"] if a["role"] == "native"}


def _read_artifact(root: Path, artifact: dict[str, Any], cache: dict[str, bytes] | None = None) -> bytes:
    if cache is not None and artifact["id"] in cache:
        return cache[artifact["id"]]
    content = safe_read(root, artifact["path"])
    if digest(content) != artifact["sha256"]:
        raise ValueError("native source changed before locator verification")
    if cache is not None:
        cache[artifact["id"]] = content
    return content


def _jsonl_lines(content: bytes) -> tuple[list[bytes], list[tuple[int, int]]]:
    pieces = content.split(b"\n")
    lines = [piece + b"\n" for piece in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])
    positions: list[tuple[int, int]] = []
    offset = 0
    for line in lines:
        positions.append((offset, offset + len(line)))
        offset += len(line)
    return lines, positions


def _sqlite_rows(root: Path, manifest: dict[str, Any], inventory: dict[str, dict[str, Any]], aid: str, artifact_cache: dict[str, bytes], row_cache: dict[str, dict[int, Any]]) -> dict[int, Any]:
    if aid in row_cache:
        return row_cache[aid]
    with tempfile.TemporaryDirectory(prefix="sb-locator-") as directory:
        clone = Path(directory)
        for artifact in inventory.values():
            relative = artifact["path"].removeprefix("native/")
            target = safe_path(clone, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_read_artifact(root, artifact, artifact_cache))
        artifact = inventory[aid]
        database = clone / artifact["path"].removeprefix("native/")
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            objects = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            if len(objects) != 1 or objects[0][0:2] != ("table", "events") or "VIRTUAL" in (objects[0][2] or "").upper():
                raise ValueError("invalid SQLite source schema for locator")
            info = connection.execute("PRAGMA table_info(events)").fetchall()
            if len(info) != 2 or [(x[1], x[2].upper(), x[5]) for x in info] != [("row_key", "INTEGER", 1), ("payload", "TEXT", 0)]:
                raise ValueError("invalid SQLite source columns for locator")
            rows = {key: value for key, value in connection.execute("SELECT row_key,payload FROM events")}
            row_cache[aid] = rows
            return rows
        finally:
            connection.close()


def _resolve_locator(root: Path, manifest: dict[str, Any], locator: dict[str, Any], artifact_cache: dict[str, bytes] | None = None, row_cache: dict[str, dict[int, Any]] | None = None) -> dict[str, Any]:
    artifact_cache = artifact_cache if artifact_cache is not None else {}
    row_cache = row_cache if row_cache is not None else {}
    if not isinstance(locator, dict):
        raise ValueError("invalid inspection locator: object required")
    inventory = _inventory(manifest)
    aid = locator.get("artifact_id")
    artifact = inventory.get(aid)
    if artifact is None:
        raise ValueError("invalid inspection locator: unknown native artifact")
    content = _read_artifact(root, artifact, artifact_cache)
    if locator.get("sha256") != artifact["sha256"]:
        raise ValueError("invalid inspection locator: artifact digest/identity")
    if "line" in locator:
        line, start, end = locator.get("line"), locator.get("byte_start"), locator.get("byte_end")
        if any(type(value) is not int for value in (line, start, end)):
            raise ValueError("invalid inspection locator: JSONL coordinates")
        lines, positions = _jsonl_lines(content)
        if not 1 <= line <= len(lines) or (start, end) != positions[line - 1]:
            raise ValueError("invalid inspection locator: JSONL range")
        payload = content[start:end].rstrip(b"\r\n")
    elif locator.get("table") == "events" and locator.get("column") == "payload" and type(locator.get("row_key")) is int:
        if Path(artifact["path"]).suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("invalid inspection locator: SQLite artifact required")
        companion_paths = {artifact["path"] + "-wal", artifact["path"] + "-shm"}
        expected = sorted(a["sha256"] for a in inventory.values() if a["path"] in companion_paths)
        if sorted(locator.get("dependency_sha256", [])) != expected:
            raise ValueError("invalid inspection locator: companion digest")
        value = _sqlite_rows(root, manifest, inventory, aid, artifact_cache, row_cache).get(locator["row_key"])
        if not isinstance(value, (str, bytes)):
            raise ValueError("invalid inspection locator: SQLite row")
        payload = value.encode("utf-8") if isinstance(value, str) else value
    else:
        raise ValueError("invalid inspection locator: unsupported locator")
    if digest(payload) != locator.get("record_sha256"):
        raise ValueError("invalid inspection locator: record digest")
    try:
        row = _strict_json(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid inspection locator: native record JSON: {exc}") from exc
    if not isinstance(row, dict) or set(row) != {"id", "session_id", "kind", "fields"}:
        raise ValueError("invalid inspection locator: native event shape")
    return row


def _lookup(event: dict[str, Any], name: str) -> Any:
    value: Any = event
    for part in name.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"inspection locator does not establish field: {name}")
        value = value[part]
    return value


def _field_matches(actual: Any, expected: Any, comparison: str) -> bool:
    if comparison == "exact":
        return type(actual) is type(expected) and canonical(actual) == canonical(expected)
    if comparison == "json":
        return canonical(actual) == canonical(expected)
    if comparison == "text_lf":
        return isinstance(actual, str) and isinstance(expected, str) and actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n")
    raise ValueError(f"inspection locator cannot prove comparison rule: {comparison}")


def validate_inspections(root: Path, manifest: dict[str, Any], expected: dict[str, Any]) -> None:
    """Verify every persisted ``inspection.state=present`` locator and claim."""
    artifact_cache: dict[str, bytes] = {}
    row_cache: dict[str, dict[int, Any]] = {}
    for assertion in expected["assertions"]:
        inspection = assertion["inspection"]
        if inspection["state"] != "present":
            continue
        locators = inspection["locators"]
        if not locators:
            raise ValueError(f"present inspection lacks locators: {assertion['id']}")
        resolved = [_resolve_locator(Path(root), manifest, locator, artifact_cache, row_cache) for locator in locators]
        for event in resolved:
            if event.get("id") != assertion["event_id"] or event.get("session_id") != assertion["session_id"]:
                raise ValueError(f"inspection locator targets wrong event: {assertion['id']}")
            for field in assertion["fields"]:
                actual = _lookup(event, field["name"])
                if not _field_matches(actual, field["expected"], field["comparison"]):
                    raise ValueError(f"inspection locator field mismatch: {assertion['id']} {field['name']}")


def validate_sources(root, manifest, decoded):
    """Validate each decoder event's native source locator."""
    artifact_cache: dict[str, bytes] = {}
    row_cache: dict[str, dict[int, Any]] = {}
    for event in decoded["events"]:
        try:
            row = _resolve_locator(Path(root), manifest, event.get("locator", {}), artifact_cache, row_cache)
        except ValueError as exc:
            raise ValueError(f"invalid decoder source locator: {exc}") from exc
        normalized = {key: event[key] for key in ("id", "session_id", "kind", "fields")}
        row["id"] = row["id"] or None
        if canonical(row) != canonical(normalized):
            raise ValueError("decoder values do not match referenced native record")
