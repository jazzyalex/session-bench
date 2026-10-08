"""Read-only, bounded projection of retained Cursor CLI ACP JSON-message references.

Only an explicitly supplied copied ACP directory is opened. The root's repeated
field 1 is treated as an ordered message list; other root wire data stays opaque.
This projection is not evidence of a complete native family or density parity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Any

from .native_replay import _open_directory

SCHEMA = "session-bench-cursor-cli-acp-json-v1"
_ALLOWED = {"store.db", "store.db-wal", "store.db-shm", "meta.json"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
MAX_FAMILY_BYTES = 64 * 1024 * 1024
MAX_BLOB_BYTES = 4 * 1024 * 1024
MAX_BLOBS = 100_000
MAX_MESSAGES = 10_000
MAX_JSON_DEPTH = 40
MAX_JSON_NODES = 100_000
MAX_WIRE_FIELDS = 20_000
_KNOWN_BLOCKS = {"text", "reasoning", "tool-call", "tool-result"}


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _snapshot(directory: Path) -> dict[str, bytes]:
    fd = _open_directory(directory)
    try:
        names = os.listdir(fd)
        if not {"store.db", "meta.json"} <= set(names) or set(names) - _ALLOWED:
            raise ValueError("Cursor ACP family closure failure")
        if len(names) != len(set(names)):
            raise ValueError("duplicate Cursor ACP file")
        result: dict[str, bytes] = {}
        total = 0
        for name in sorted(names):
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FAMILY_BYTES - total:
                raise ValueError("Cursor ACP family byte limit or special file")
            child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("Cursor ACP file changed before snapshot")
                raw = stream.read(MAX_FAMILY_BYTES - total + 1)
                after = os.fstat(stream.fileno())
                if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("Cursor ACP file changed during snapshot")
                if len(raw) > MAX_FAMILY_BYTES - total:
                    raise ValueError("Cursor ACP family byte limit")
            total += len(raw)
            result[name] = raw
        if set(os.listdir(fd)) != set(names):
            raise ValueError("Cursor ACP file population changed")
        return result
    finally:
        os.close(fd)


def _json(raw: bytes | str) -> Any:
    def pairs(entries):
        value = {}
        for key, item in entries:
            if key in value:
                raise ValueError("duplicate native JSON key")
            value[key] = item
        return value
    def constant(_value):
        raise ValueError("non-finite native JSON number")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    nodes = 0
    def walk(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ValueError("native JSON depth or node count limit")
        if isinstance(item, dict):
            for child in item.values(): walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item: walk(child, depth + 1)
    walk(value, 0)
    return value


def _wire(data: bytes) -> list[tuple[int, int, bytes]]:
    if len(data) > MAX_BLOB_BYTES:
        raise ValueError("Cursor ACP protobuf blob byte limit")
    offset = 0
    output = []
    def varint():
        nonlocal offset
        value = 0
        for index in range(10):
            if offset >= len(data): raise ValueError("truncated protobuf varint")
            byte = data[offset]; offset += 1
            if index == 9 and byte > 1: raise ValueError("overflowing protobuf varint")
            value |= (byte & 127) << (7 * index)
            if not byte & 128: return value
        raise ValueError("overflowing protobuf varint")
    while offset < len(data):
        if len(output) >= MAX_WIRE_FIELDS: raise ValueError("protobuf field count limit")
        start = offset
        key = varint(); number, wire = key >> 3, key & 7
        if number <= 0: raise ValueError("invalid protobuf field number")
        if wire == 0: varint()
        elif wire in (1, 5):
            offset += 8 if wire == 1 else 4
            if offset > len(data): raise ValueError("truncated fixed-width protobuf field")
        elif wire == 2:
            length = varint(); end = offset + length
            if end > len(data): raise ValueError("truncated length-delimited protobuf field")
            payload = data[offset:end]; offset = end
            output.append((number, wire, payload))
            continue
        else: raise ValueError("unsupported protobuf wire type")
        output.append((number, wire, data[start:offset]))
    return output


def _reference(payload: bytes, blobs: dict[str, bytes]) -> str:
    if len(payload) != 32: raise ValueError("Cursor ACP reference must be SHA-256")
    identity = payload.hex()
    if identity not in blobs: raise ValueError("Cursor ACP reference points to a missing blob")
    return identity


def _turn_user(reference: bytes, blobs: dict[str, bytes]) -> tuple[str, str, list[dict[str, Any]]] | None:
    turn_id = _reference(reference, blobs)
    fields = _wire(blobs[turn_id])
    unsupported = []
    def note(path, entries, allowed):
        for index, (number, wire, payload) in enumerate(entries):
            if (number, wire) not in allowed:
                unsupported.append({"path": path, "field_index": index, "field_number": number,
                                    "wire_type": wire, "size_bytes": len(payload), "sha256": _digest(payload)})
    note("turn", fields, {(1, 2), (2, 2)})
    variants = [item for item in fields if item[0] in (1, 2) and item[1] == 2]
    if len(variants) != 1 or variants[0][0] != 1:
        return None
    agent = _wire(variants[0][2])
    note("agent-turn", agent, {(1, 2), (2, 2)})
    users = [item for item in agent if item[0] == 1 and item[1] == 2]
    if len(users) != 1: return None
    user_id = _reference(users[0][2], blobs)
    user_fields = _wire(blobs[user_id])
    note("user-message", user_fields, {(1, 2)})
    texts = [item[2] for item in user_fields if item[0] == 1 and item[1] == 2]
    if len(texts) != 1: return None
    return user_id, texts[0].decode("utf-8"), unsupported


def decode_cursor_cli_acp(directory: str | Path, *, session_id: str, submitted_prompts: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """Project root field-1 JSON messages, retaining native order and byte proofs.

    ``submitted_prompts`` is independent workload evidence, in submission order.
    It is used only for exact content binding; role counts never label prompts.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("explicit Cursor ACP session identity required")
    if submitted_prompts is not None and (not isinstance(submitted_prompts, (list, tuple)) or any(not isinstance(p, str) or not p for p in submitted_prompts) or len(set(submitted_prompts)) != len(submitted_prompts)):
        raise ValueError("submitted prompts must be distinct nonempty strings")
    if submitted_prompts is not None and (len(submitted_prompts) > MAX_MESSAGES or
                                          sum(len(prompt.encode("utf-8")) for prompt in submitted_prompts) > MAX_BLOB_BYTES):
        raise ValueError("submitted prompt count or byte limit")
    snapshot = _snapshot(Path(directory))
    sidecar = _json(snapshot["meta.json"])
    if not isinstance(sidecar, dict) or type(sidecar.get("schemaVersion")) is not int or sidecar["schemaVersion"] != 1:
        raise ValueError("unsupported Cursor ACP sidecar schema")
    with tempfile.TemporaryDirectory(prefix="session-bench-cursor-cli-acp-") as temporary:
        temp = Path(temporary)
        for name, raw in snapshot.items(): (temp / name).write_bytes(raw)
        connection = sqlite3.connect((temp / "store.db").as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables != {"meta", "blobs"}: raise ValueError("unsupported Cursor ACP SQLite tables")
            for table, expected in (("meta", ["key", "value"]), ("blobs", ["id", "data"])):
                if [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] != expected:
                    raise ValueError("unsupported Cursor ACP SQLite columns")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Cursor ACP SQLite integrity failure")
            metadata = connection.execute("SELECT key,value FROM meta").fetchall()
            if len(metadata) != 1 or metadata[0][0] != "0" or not isinstance(metadata[0][1], str):
                raise ValueError("Cursor ACP meta root must be unique")
            root_metadata = _json(bytes.fromhex(metadata[0][1]))
            if not isinstance(root_metadata, dict) or root_metadata.get("agentId") != session_id:
                raise ValueError("Cursor ACP root session identity mismatch")
            count = connection.execute("SELECT count(*) FROM blobs").fetchone()[0]
            if count > MAX_BLOBS: raise ValueError("Cursor ACP blob count limit")
            rows = connection.execute("SELECT id,data FROM blobs ORDER BY id").fetchall()
        finally:
            connection.close()
    blobs: dict[str, bytes] = {}
    for identity, raw in rows:
        if not isinstance(identity, str) or not _HEX.fullmatch(identity) or not isinstance(raw, bytes):
            raise ValueError("invalid Cursor ACP blob identity or type")
        if identity in blobs: raise ValueError("duplicate Cursor ACP blob identity")
        if len(raw) > MAX_BLOB_BYTES: raise ValueError("Cursor ACP blob byte limit")
        if _digest(raw) != identity: raise ValueError("Cursor ACP blob hash mismatch")
        blobs[identity] = raw
    root_id = root_metadata.get("latestRootBlobId")
    if not isinstance(root_id, str) or root_id not in blobs:
        raise ValueError("Cursor ACP root reference missing")
    fields = _wire(blobs[root_id])
    messages, seen = [], set()
    unsupported = []
    turn_refs = []
    for field_index, (number, wire, payload) in enumerate(fields):
        if number == 1 and wire == 2:
            if len(messages) >= MAX_MESSAGES: raise ValueError("Cursor ACP message count limit")
            identity = _reference(payload, blobs)
            if identity in seen: raise ValueError("duplicate Cursor ACP JSON message reference")
            seen.add(identity)
            message = _json(blobs[identity])
            if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"} or "content" not in message:
                raise ValueError("unsupported Cursor ACP JSON message shape")
            content = message["content"]
            if not isinstance(content, (str, list)) or (isinstance(content, list) and any(not isinstance(x, dict) for x in content)):
                raise ValueError("unsupported Cursor ACP JSON content shape")
            gaps = []
            for key in message:
                if key not in {"role", "content", "providerOptions", "id"}: gaps.append(f"message.{key}")
            if "providerOptions" in message: gaps.append("message.providerOptions:opaque")
            if isinstance(content, list):
                for block_index, block in enumerate(content):
                    kind = block.get("type")
                    if kind not in _KNOWN_BLOCKS: gaps.append(f"content[{block_index}].type:{kind!r}")
                    for key in block:
                        if key not in {"type", "text", "signature", "providerOptions", "toolCallId", "toolName", "args", "result", "experimental_content"}:
                            gaps.append(f"content[{block_index}].{key}")
                        if key in {"providerOptions", "experimental_content"}:
                            gaps.append(f"content[{block_index}].{key}:opaque")
            messages.append({"index": len(messages), "root_field_index": field_index, "blob_id": identity,
                             "sha256": identity, "size_bytes": len(blobs[identity]), "role": message["role"],
                             "message": message, "unsupported_json_paths": gaps})
        elif number == 8 and wire == 2:
            turn_refs.append((field_index, payload))
        else:
            unsupported.append({"root_field_index": field_index, "field_number": number, "wire_type": wire,
                                "size_bytes": len(payload), "sha256": _digest(payload)})
    links = []
    for index, (field_index, ref) in enumerate(turn_refs):
        turn_id = _reference(ref, blobs)
        resolved = _turn_user(ref, blobs)
        links.append({"index": index, "root_field_index": field_index, "turn_blob_id": turn_id,
                      "user_blob_id": resolved[0] if resolved else None,
                      "user_text_sha256": _digest(resolved[1].encode()) if resolved else None,
                      "unsupported_fields": resolved[2] if resolved else [],
                      "status": "user-text-decoded" if resolved else "unresolved-turn-shape"})
    bindings = []
    prompts = list(submitted_prompts or ())
    for prompt_index, prompt in enumerate(prompts):
        candidates = []
        for entry in messages:
            if entry["role"] != "user": continue
            content = entry["message"]["content"]
            texts = [content] if isinstance(content, str) else [block.get("text") for block in content if block.get("type") == "text"]
            if any(isinstance(text, str) and prompt in text for text in texts): candidates.append(entry["index"])
        turn_candidates = [link["index"] for link, (_, ref) in zip(links, turn_refs)
                           if (resolved := _turn_user(ref, blobs)) and resolved[1] == prompt]
        status = "bound" if len(candidates) == len(turn_candidates) == 1 else "unresolved"
        bindings.append({"prompt_index": prompt_index, "prompt_sha256": _digest(prompt.encode()),
                         "message_indices": candidates, "turn_indices": turn_candidates, "status": status})
    bound = [entry for entry in bindings if entry["status"] == "bound"]
    if ([entry["message_indices"][0] for entry in bound] != sorted(entry["message_indices"][0] for entry in bound)
            or [entry["turn_indices"][0] for entry in bound] != sorted(entry["turn_indices"][0] for entry in bound)):
        for entry in bindings:
            entry["status"] = "unresolved-order"
    used = {root_id, *seen, *(link["turn_blob_id"] for link in links), *(link["user_blob_id"] for link in links if link["user_blob_id"])}
    return {"schema_version": SCHEMA, "session_id": session_id,
            "artifacts": [{"path": name, "sha256": _digest(raw), "size_bytes": len(raw)} for name, raw in sorted(snapshot.items())],
            "root_blob_id": root_id, "messages": messages, "turn_links": links,
            "submitted_prompt_bindings": bindings,
            "unsupported_root_fields": unsupported,
            "all_blob_records": [{"id": key, "size_bytes": len(raw), "sha256": key, "projected_or_linked": key in used} for key, raw in sorted(blobs.items())],
            "unlinked_blob_count": len(blobs) - len(used),
            "complete_native_root": False, "complete_semantic_family": False, "usage_absence_proven": False,
            "density_qualified": False,
            "limitations": ["non-message root fields and opaque provider options remain unresolved",
                            "field-8 links cover only decodable turn shapes; they do not prove all message relationships",
                            "unlinked blobs may be historical or unsupported native state",
                            "retained ACP files do not prove full native family or comparable byte density"]}
