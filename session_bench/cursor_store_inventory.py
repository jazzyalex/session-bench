"""Bounded offline inventory of one retained Cursor CLI ACP SQLite family.

Schema/graph references follow Agent Sessions' CursorSessionParser (read-only
reference). Tool and thinking protobuf payloads are deliberately uninterpreted.
This inventory cannot qualify full semantic recovery or comparable v1 density.
It reads an explicit copied session directory and never discovers live profiles.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any

from .native_replay import _snapshot_tree

SCHEMA = "session-bench-cursor-acp-inventory-v1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_FILES = {"store.db", "meta.json", "store.db-wal", "store.db-shm"}
_MAX_BYTES = 64 * 1024 * 1024
_MAX_BLOBS = 100_000


def _json(raw: bytes | str):
    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError("duplicate native JSON key")
            output[key] = value
        return output
    def constant(value):
        raise ValueError("non-finite native JSON number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def protobuf_fields(data: bytes) -> dict[int, list[bytes]]:
    """Validate supported protobuf wire forms and retain length-delimited fields."""
    offset, fields = 0, {}
    def varint():
        nonlocal offset
        value = 0
        for index in range(10):
            if offset >= len(data):
                raise ValueError("truncated protobuf varint")
            byte = data[offset];offset += 1
            if index == 9 and byte > 1:
                raise ValueError("overflowing protobuf varint")
            value |= (byte & 127) << (index * 7)
            if not byte & 128:
                return value
        raise ValueError("overflowing protobuf varint")
    while offset < len(data):
        key = varint();number, wire = key >> 3, key & 7
        if number <= 0:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            varint()
        elif wire in {1, 5}:
            offset += 8 if wire == 1 else 4
            if offset > len(data):
                raise ValueError("truncated fixed-width protobuf field")
        elif wire == 2:
            length = varint();end = offset + length
            if end > len(data):
                raise ValueError("truncated length-delimited protobuf field")
            fields.setdefault(number, []).append(data[offset:end]);offset = end
        else:
            raise ValueError("unsupported protobuf wire type")
    return fields


def inventory_cursor_cli_store(directory: str | Path, *, session_id: str) -> dict[str, Any]:
    """Close known ACP graph edges and account for every content-addressed blob.

    Presence of a hash/known graph link is independent of benchmark observer
    truth. Unsupported protobuf fields and tool/thinking variants remain
    explicit gaps. SQLite reads operate on a disposable byte snapshot so WAL
    processing cannot alter the retained capture. Source SHA proofs include
    all retained WAL/SHM files, when present.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("explicit native session identity is required")
    snapshot = _snapshot_tree(Path(directory))
    if not {"store.db", "meta.json"} <= set(snapshot) or set(snapshot) - _ALLOWED_FILES:
        raise ValueError("Cursor ACP family closure requires store.db/meta.json and known SQLite companions only")
    if sum(len(raw) for raw in snapshot.values()) > _MAX_BYTES:
        raise ValueError("Cursor ACP family exceeds byte limit")
    sidecar = _json(snapshot["meta.json"])
    if not isinstance(sidecar, dict) or sidecar.get("schemaVersion") != 1 or type(sidecar.get("schemaVersion")) is not int:
        raise ValueError("unsupported Cursor ACP sidecar schema")
    with tempfile.TemporaryDirectory(prefix="session-bench-cursor-acp-") as temporary:
        root = Path(temporary)
        for name, raw in snapshot.items():
            (root / name).write_bytes(raw)
        connection = sqlite3.connect((root / "store.db").as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON");connection.execute("PRAGMA trusted_schema=OFF")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"meta", "blobs"}:
                raise ValueError("unsupported Cursor ACP SQLite table inventory")
            for table, names in (("meta", ["key", "value"]), ("blobs", ["id", "data"])):
                if [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] != names:
                    raise ValueError("unsupported Cursor ACP SQLite columns")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Cursor ACP SQLite integrity failure")
            metadata = connection.execute("SELECT key,value FROM meta").fetchall()
            if len(metadata) != 1 or metadata[0][0] != "0" or not isinstance(metadata[0][1], str):
                raise ValueError("Cursor ACP meta root must be unique")
            root_metadata = _json(bytes.fromhex(metadata[0][1]))
            if not isinstance(root_metadata, dict) or root_metadata.get("agentId") != session_id:
                raise ValueError("Cursor ACP root session identity mismatch")
            rows = connection.execute("SELECT id,data FROM blobs ORDER BY id").fetchall()
        finally:
            connection.close()
    if len(rows) > _MAX_BLOBS:
        raise ValueError("Cursor ACP blob population exceeds limit")
    blobs = {}
    for identity, raw in rows:
        if not isinstance(identity, str) or not _HEX.fullmatch(identity) or not isinstance(raw, bytes):
            raise ValueError("invalid Cursor ACP blob identity or scalar type")
        if identity in blobs or hashlib.sha256(raw).hexdigest() != identity:
            raise ValueError("Cursor ACP blob content-address hash mismatch")
        blobs[identity] = raw
    visited, roles, texts = set(), {}, []
    unknown = Counter()
    assistant_variants = Counter()
    def node(identity, role):
        if isinstance(identity, bytes):
            if len(identity) != 32:
                raise ValueError("Cursor ACP graph reference is not a SHA-256 identity")
            identity = identity.hex()
        if not isinstance(identity, str) or identity not in blobs:
            raise ValueError("Cursor ACP graph reference points to a missing blob")
        visited.add(identity);roles.setdefault(identity, set()).add(role)
        return identity, protobuf_fields(blobs[identity])
    def one(fields, number):
        values = fields.get(number, [])
        if len(values) != 1:
            raise ValueError("Cursor ACP graph field is absent or repeated")
        return values[0]
    def extras(fields, allowed, label):
        for number, values in fields.items():
            if number not in allowed:
                unknown[f"{label}:field-{number}"] += len(values)
    root_id, root_fields = node(root_metadata.get("latestRootBlobId"), "root")
    extras(root_fields, {8}, "root")
    for turn_index, reference in enumerate(root_fields.get(8, [])):
        turn_id, turn_fields = node(reference, "turn")
        extras(turn_fields, {1, 2}, "turn")
        variants = [number for number in (1, 2) if turn_fields.get(number)]
        if len(variants) != 1:
            raise ValueError("Cursor ACP conversation turn has ambiguous oneof")
        turn = protobuf_fields(one(turn_fields, variants[0]))
        if variants[0] == 2:
            unknown["shell-turn"] += 1
            continue
        extras(turn, {1, 2}, "agent-turn")
        user_id, user = node(one(turn, 1), "user_message")
        extras(user, {1}, "user-message")
        text = one(user, 1).decode("utf-8")
        if not text:
            raise ValueError("Cursor ACP user message is empty")
        texts.append({"kind": "user_message", "turn": turn_index, "blob_id": user_id, "text": text})
        for step_index, reference in enumerate(turn.get(2, [])):
            step_id, step = node(reference, "step")
            extras(step, {1, 2, 3}, "step")
            variants = [number for number in (1, 2, 3) if step.get(number)]
            if len(variants) != 1:
                raise ValueError("Cursor ACP step has ambiguous oneof")
            payload = one(step, variants[0])
            if variants[0] == 1:
                if len(payload) == 32:
                    response_id, response = node(payload, "assistant_message")
                    assistant_variants["content-addressed-reference"] += 1
                else:
                    # Retained Cursor CLI captures use an inline assistant
                    # message in the same step oneof. No recursive hash guess:
                    # only the known field-1 UTF-8 text shape is projected.
                    response_id, response = step_id, protobuf_fields(payload)
                    roles[step_id].add("inline_assistant_message")
                    assistant_variants["inline-message"] += 1
                extras(response, {1}, "assistant-message")
                text = one(response, 1).decode("utf-8")
                if not text:
                    raise ValueError("Cursor ACP assistant message is empty")
                texts.append({"kind": "assistant_message", "turn": turn_index, "step": step_index, "blob_id": response_id, "text": text})
            else:
                # The reference parser recognizes these variants as valid
                # graph nodes but does not describe their semantic schema.
                # Retain them without guessing tool/result or nested links.
                unknown["tool-step" if variants[0] == 2 else "thinking-step"] += 1
    artifacts = [{"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)} for name, raw in sorted(snapshot.items())]
    records = [{"id": identity, "logical_blob_bytes": len(raw), "sha256": identity,
                "known_graph_roles": sorted(roles.get(identity, set())), "reachable_known_graph": identity in visited}
               for identity, raw in sorted(blobs.items())]
    return {"schema_version": SCHEMA, "session_id": session_id, "artifacts": artifacts,
            "capture_file_family_closed": True, "known_graph_references_closed": True,
            "complete_semantic_family": False, "density_qualified": False,
            "byte_accounting_rule": "native-protobuf-blob-payload-bytes-v1",
            "blob_count": len(records), "logical_blob_bytes": sum(row["logical_blob_bytes"] for row in records),
            "known_graph_blob_count": len(visited), "unclassified_blob_count": len(blobs) - len(visited),
            "unsupported_payloads": dict(sorted(unknown.items())), "assistant_encoding_variants": dict(sorted(assistant_variants.items())), "records": records,
            "text_records": texts,
            "limitations": ["tool/thinking/shell protobuf payloads and unknown fields are not semantically decoded",
                            "unreachable historical blobs are included in accounting but are not current-session events",
                            "protobuf payload bytes are not comparable to the frozen canonical JSON density rule",
                            "file closure does not prove complete remote/unkeyed session storage or independent observation"]}
