"""Capture only one proven-new synthetic Cursor Desktop session's SQLite rows.

This deliberately never copies an account SQLite database. Run after the isolated
Cursor writer exits; the selected rows are retained privately and a redacted
logical derivative is made for copied-bundle decoding.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"missing isolated SQLite store: {path}")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.execute("BEGIN")
    return db


def capture(session_id: str, project: Path, user_data: Path, output: Path) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f-]{27,}", session_id):
        raise ValueError("session ID must be an explicit native UUID")
    project = project.resolve(strict=True)
    user_data = user_data.resolve(strict=True)
    global_db = user_data / "User/globalStorage/state.vscdb"
    db = connection(global_db)
    try:
        rows = db.execute(
            "SELECT key,value FROM cursorDiskKV WHERE key=? OR key LIKE ? OR key LIKE ? OR key LIKE ? ORDER BY key",
            (f"composerData:{session_id}", f"bubbleId:{session_id}:%", f"checkpointId:{session_id}:%", f"ofsContent:{session_id}:%"),
        ).fetchall()
        if len(rows) != len({key for key, _ in rows}):
            raise ValueError("duplicate session keys")
        by_key = {key: value for key, value in rows}
        composer_key = f"composerData:{session_id}"
        if composer_key not in by_key:
            raise ValueError("session composer index missing")
        composer = json.loads(by_key[composer_key])
        headers = composer.get("fullConversationHeadersOnly")
        if not isinstance(headers, list) or not headers:
            raise ValueError("session bubble index missing")
        bubble_ids = [item.get("bubbleId") for item in headers if isinstance(item, dict)]
        if len(bubble_ids) != len(headers) or len(set(bubble_ids)) != len(headers):
            raise ValueError("session bubble index is incomplete or duplicated")
        indexed = {f"bubbleId:{session_id}:{bubble_id}" for bubble_id in bubble_ids}
        retained = {key for key in by_key if key.startswith(f"bubbleId:{session_id}:")}
        if indexed != retained:
            raise ValueError("bubble index does not close over keyed session rows")
        checkpoint_ids = {
            value.get("checkpointId")
            for key, raw in rows if key in indexed
            for value in [json.loads(raw)]
            if value.get("checkpointId")
        }
        checkpoint_keys = {f"checkpointId:{session_id}:{checkpoint_id}" for checkpoint_id in checkpoint_ids}
        if not checkpoint_keys.issubset(by_key):
            raise ValueError("referenced checkpoint is missing")
        raw_rows = [{"store": "global.cursorDiskKV", "key": key, "value": value} for key, value in rows]
    finally:
        db.close()

    workspace_matches: list[Path] = []
    project_aliases = {str(project), str(project).replace("/private/tmp/", "/tmp/")}
    for workspace_file in (user_data / "User/workspaceStorage").glob("*/workspace.json"):
        try:
            data = json.loads(workspace_file.read_text())
        except (OSError, ValueError):
            continue
        if any(alias in json.dumps(data) for alias in project_aliases):
            workspace_matches.append(workspace_file.parent / "state.vscdb")
    if len(workspace_matches) != 1:
        raise ValueError("expected exactly one isolated workspace SQLite store")
    workspace_db = connection(workspace_matches[0])
    try:
        workspace_rows = workspace_db.execute(
            "SELECT key,value FROM ItemTable WHERE instr(cast(value as text), ?) > 0 ORDER BY key",
            (session_id,),
        ).fetchall()
    finally:
        workspace_db.close()
    raw_rows.extend({"store": "workspace.ItemTable", "key": key, "value": value} for key, value in workspace_rows)

    # Cursor also writes session-bearing records under keys that do not encode
    # the session ID. Keep their exact bytes in the private capture; the public
    # derivative carries only digests and sizes pending field-level redaction.
    extra_db = connection(global_db)
    try:
        extra_rows = extra_db.execute(
            "SELECT key,value FROM cursorDiskKV WHERE instr(cast(value as text), ?) > 0 AND instr(cast(key as text), ?) = 0 ORDER BY key",
            (session_id, session_id),
        ).fetchall()
    finally:
        extra_db.close()
    private_extras = [
        {"store": "global.cursorDiskKV", "key": key, "value_base64": base64.b64encode(value if isinstance(value, bytes) else value.encode()).decode()}
        for key, value in extra_rows
    ]
    public_extra_inventory = [
        {"store": "global.cursorDiskKV", "key_family": key.split(":")[0], "key_sha256": digest(key.encode()), "value_sha256": digest(value if isinstance(value, bytes) else value.encode()), "size_bytes": len(value)}
        for key, value in extra_rows
    ]

    # Search the isolated profile's SQLite key/value families for any second
    # session-bearing row before declaring this logical export closed. Read-only
    # queries return keys and match counts; unrelated account values are not copied.
    selected_global = {row["key"] for row in raw_rows if row["store"] == "global.cursorDiskKV"} | {key for key, _ in extra_rows}
    selected_workspace = {row["key"] for row in raw_rows if row["store"] == "workspace.ItemTable"}
    scanned_databases = 0
    matched_rows = 0
    for candidate in (user_data / "User").rglob("*.vscdb"):
        source = connection(candidate)
        scanned_databases += 1
        try:
            for (table,) in source.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                columns = {item[1] for item in source.execute(f'PRAGMA table_info("{table}")')}
                if not {"key", "value"}.issubset(columns):
                    continue
                hits = source.execute(
                    f'SELECT key FROM "{table}" WHERE instr(cast(key as text), ?) > 0 OR instr(cast(value as text), ?) > 0',
                    (session_id, session_id),
                ).fetchall()
                matched_rows += len(hits)
                allowed = (
                    selected_global if candidate == global_db and table == "cursorDiskKV"
                    else selected_workspace if candidate == workspace_matches[0] and table == "ItemTable"
                    else set()
                )
                if any(key not in allowed for (key,) in hits):
                    raise ValueError("unselected session-bearing SQLite row exists")
        finally:
            source.close()

    output.mkdir(parents=True, exist_ok=False)
    private = output / "native-private"
    public = output / "native-sanitized"
    private.mkdir()
    public.mkdir()
    private_bytes = b"".join(canonical(row) for row in raw_rows)
    (private / "session-companions.jsonl").write_bytes(private_bytes)
    extra_bytes = b"".join(canonical(row) for row in private_extras)
    (private / "extra-session-rows.jsonl").write_bytes(extra_bytes)
    (public / "extra-session-row-inventory.json").write_bytes(canonical(public_extra_inventory))

    replacements = [
        (str(project), "$RUN_PROJECT"),
        (str(project).replace("/private/tmp/", "/tmp/"), "$RUN_PROJECT"),
        (session_id, "$SESSION_ID"),
    ]
    sanitized_rows = []
    for row in raw_rows:
        text = json.dumps(row, ensure_ascii=False)
        for source, target in replacements:
            text = text.replace(source, target)
        sanitized_rows.append(json.loads(text))
    public_bytes = b"".join(canonical(row) for row in sanitized_rows)
    if re.search(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|/Users/[^\s\"\\]+|/(?:private/)?tmp/sbcd\d+", public_bytes):
        raise ValueError("sanitized companion contains a private identifier or path")
    (public / "session-companions.jsonl").write_bytes(public_bytes)
    receipt: dict[str, object] = {
        "kind": "cursor_desktop_session_selected_rows_v1",
        "session_key_sha256": digest(session_id.encode()),
        "composer_header_count": len(headers),
        "bubble_row_count": len(indexed),
        "checkpoint_reference_count": len(checkpoint_keys),
        "workspace_session_row_count": len(workspace_rows),
        "selected_row_count": len(raw_rows),
        "extra_session_row_count": len(extra_rows),
        "private_extra_rows_sha256": digest(extra_bytes),
        "isolated_sqlite_database_count_scanned": scanned_databases,
        "session_bearing_rows_found": matched_rows,
        "private_selected_sha256": digest(private_bytes),
        "sanitized_selected_sha256": digest(public_bytes),
        "selection": ["composerData:SESSION", "bubbleId:SESSION:*", "checkpointId:SESSION:*", "ofsContent:SESSION:*", "workspace ItemTable values containing SESSION"],
        "limitations": "Logical session-row export from a quiesced isolated profile; unrelated account DB rows are intentionally excluded. This does not by itself prove all possible remote or unkeyed native stores are absent.",
    }
    (output / "companion-capture-receipt.json").write_bytes(canonical(receipt))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--user-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = capture(args.session_id, args.project, args.user_data, args.output)
    print(json.dumps({key: receipt[key] for key in ("composer_header_count", "bubble_row_count", "selected_row_count", "sanitized_selected_sha256")}))


if __name__ == "__main__":
    main()
