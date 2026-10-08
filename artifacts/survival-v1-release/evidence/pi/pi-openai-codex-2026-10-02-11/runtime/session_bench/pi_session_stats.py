"""Validate a read-only Pi RPC session-statistics observation.

The collector runs Pi's ``get_session_stats`` against a byte-identical copied
session file with networking and user configuration disabled. It does not
submit a prompt or request a model response.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from datetime import datetime


SCHEMA = "session-bench-pi-session-stats-v1"
REQUEST_ID = "session-bench-stats-1"
_TOKEN_FIELDS = {"input", "output", "cacheRead", "cacheWrite", "total"}


def _strict(raw: bytes, label: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith(label + ":"):
            raise
        raise ValueError(f"{label}: invalid JSON") from error


def validate_pi_session_stats(
    stdout: bytes,
    *,
    session_bytes: bytes,
    session_filename: str,
    pi_version: str,
) -> dict[str, Any]:
    """Bind exactly one successful stats RPC response to copied native bytes."""
    if not isinstance(session_bytes, bytes) or not session_bytes or not isinstance(stdout, bytes):
        raise ValueError("Pi stats input bytes are missing")
    if Path(session_filename).name != session_filename or not session_filename.endswith(".jsonl"):
        raise ValueError("Pi stats session filename must be a basename")
    if pi_version != "1.0.0":
        raise ValueError("Pi stats collector is pinned to Pi 1.0.0")

    session_rows = [_strict(line, "Pi stats native header")
                    for line in session_bytes.splitlines() if line.strip()]
    if (not session_rows or not isinstance(session_rows[0], Mapping)
            or session_rows[0].get("type") != "session"):
        raise ValueError("Pi stats native session header is missing")
    session_id = session_rows[0].get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi stats native session ID is missing")

    rows = [_strict(line, "Pi stats RPC output") for line in stdout.splitlines() if line.strip()]
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        raise ValueError("Pi stats RPC must return exactly one response and no model events")
    response = rows[0]
    if (response.get("type") != "response" or response.get("id") != REQUEST_ID
            or response.get("command") != "get_session_stats" or response.get("success") is not True):
        raise ValueError("Pi stats RPC response identity or success mismatch")
    data = response.get("data")
    if not isinstance(data, Mapping) or data.get("sessionId") != session_id:
        raise ValueError("Pi stats RPC returned the wrong session")
    reported_path = data.get("sessionFile")
    if not isinstance(reported_path, str) or Path(reported_path).name != session_filename:
        raise ValueError("Pi stats RPC returned the wrong session file")
    tokens = data.get("tokens")
    if not isinstance(tokens, Mapping) or not _TOKEN_FIELDS.issubset(tokens):
        raise ValueError("Pi stats RPC token totals are incomplete")
    for key in _TOKEN_FIELDS:
        value = tokens[key]
        if type(value) is not int or value < 0:
            raise ValueError("Pi stats RPC token total is not a non-negative integer")
    if tokens["total"] != sum(tokens[key] for key in ("input", "output", "cacheRead", "cacheWrite")):
        raise ValueError("Pi stats RPC total does not equal its declared token buckets")
    counts = {key: data.get(key) for key in
              ("userMessages", "assistantMessages", "toolCalls", "toolResults", "totalMessages")}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Pi stats RPC message counts are incomplete")
    if counts["toolCalls"] != counts["toolResults"]:
        raise ValueError("Pi stats RPC tool call/result counts do not balance")
    cost = data.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("Pi stats RPC cost is invalid")

    return {
        "schema_version": SCHEMA,
        "source": "pi_rpc_get_session_stats",
        "pi_version": pi_version,
        "rpc_request_id": REQUEST_ID,
        "session_id": session_id,
        "session_filename": session_filename,
        "session_sha256": hashlib.sha256(session_bytes).hexdigest(),
        "stats": {**counts, "tokens": dict(tokens), "cost": cost},
    }


def validate_pi_session_stats_observation(
    receipt: Mapping[str, Any],
    *,
    stdout: bytes,
    stderr: bytes,
    session_bytes: bytes,
    session_filename: str,
    attempt_id: str,
) -> dict[str, Any]:
    """Recompute and verify the stored raw RPC observation and collection flags."""
    fields = {
        "schema_version", "source", "pi_version", "rpc_request_id", "session_id",
        "session_filename", "session_sha256", "stats", "attempt_id", "captured_at",
        "rpc_stdout_sha256", "rpc_stderr_sha256", "network_disabled",
        "user_config_disabled", "model_submissions",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != fields:
        raise ValueError("Pi stats observation has an unsupported receipt shape")
    if receipt.get("attempt_id") != attempt_id:
        raise ValueError("Pi stats observation belongs to another attempt")
    captured_at = receipt.get("captured_at")
    if not isinstance(captured_at, str):
        raise ValueError("Pi stats observation timestamp is missing")
    try:
        parsed_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Pi stats observation timestamp is invalid") from error
    if parsed_time.tzinfo is None:
        raise ValueError("Pi stats observation timestamp lacks a timezone")
    if (receipt.get("network_disabled") is not True
            or receipt.get("user_config_disabled") is not True
            or type(receipt.get("model_submissions")) is not int
            or receipt["model_submissions"] != 0
            or receipt.get("rpc_stdout_sha256") != hashlib.sha256(stdout).hexdigest()
            or receipt.get("rpc_stderr_sha256") != hashlib.sha256(stderr).hexdigest()):
        raise ValueError("Pi stats observation collection receipt is inconsistent")
    recomputed = validate_pi_session_stats(
        stdout, session_bytes=session_bytes, session_filename=session_filename,
        pi_version=receipt.get("pi_version"),
    )
    if any(receipt.get(key) != value for key, value in recomputed.items()):
        raise ValueError("Pi stats normalized receipt differs from its raw RPC response")
    return recomputed


def reconciliation_candidate(receipt: Mapping[str, Any], *, session_id: str,
                              session_sha256: str, locator: Mapping[str, str]) -> dict[str, Any]:
    """Normalize Pi's reported session totals for comparison with stdout usage."""
    if (receipt.get("schema_version") != SCHEMA
            or receipt.get("source") != "pi_rpc_get_session_stats"
            or receipt.get("session_id") != session_id
            or receipt.get("session_sha256") != session_sha256):
        raise ValueError("Pi stats receipt does not bind this native session")
    stats = receipt.get("stats")
    tokens = stats.get("tokens") if isinstance(stats, Mapping) else None
    if not isinstance(tokens, Mapping):
        raise ValueError("Pi stats receipt lacks token totals")
    normalized = {
        "input_tokens": tokens["input"],
        "output_tokens": tokens["output"],
        "cache_read_tokens": tokens["cacheRead"],
        "cache_write_tokens": tokens["cacheWrite"],
    }
    return {"id": "pi-rpc-session-total", "matches_session_totals": True,
            "session_totals": normalized, "locator": dict(locator)}
