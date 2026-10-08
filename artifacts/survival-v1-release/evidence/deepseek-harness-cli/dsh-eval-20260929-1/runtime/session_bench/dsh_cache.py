"""Supported DSH projection-cache v7/tokenUsage state v2 companion reader.

The installed 0.2.0-rc.2 dsh-session-projection-cache domain declares version7,
identity-bound per-session checkpoint rows. dsh-token-meter's tokenUsage v2 maps
inputTokens to uncachedInputTokens, folds every settled model step, and stores
four named buckets. This reader never supplies missing reasoning or reconstructs
an absent persisted aggregate from the transcript. All checkpoint fields remain
in the canonical logical byte denominator, including unknown projection units.
"""
import hashlib
from pathlib import Path

from .dsh_live import strict_json, DSHSemanticError
from .native_replay import canonical

BUCKETS = {"uncachedInputTokens": "input", "outputTokens": "output", "cacheReadTokens": "cache_read", "cacheWriteTokens": "cache_write"}


def read_dsh_cache(path: Path, *, header, decoded):
    data = Path(path).read_bytes()
    value = strict_json(data)
    if not isinstance(value, dict) or set(value) != {"version", "record"} or type(value["version"]) is not int or value["version"] != 7:
        raise DSHSemanticError("unsupported DSH projection-cache domain schema")
    record = value["record"]
    if not isinstance(record, dict) or set(record) != {"identity", "rows"} or not isinstance(record["rows"], dict):
        raise DSHSemanticError("malformed DSH projection-cache record")
    expected = {"formatVersion": header["version"], "createdAt": header["createdAt"], "cwd": header["cwd"],
                "isSeeded": header["isSeeded"], "inheritedEventCount": 0}
    if record["identity"] != expected or header["isSeeded"] is not False:
        raise DSHSemanticError("DSH projection-cache lifecycle differs from native header")
    last_sequence = max(row["sequence"] for row in decoded["records"])
    for key, row in record["rows"].items():
        if not isinstance(row, dict) or set(row) != {"ver", "seq", "val"} or type(row["ver"]) is not int or row["ver"] < 0 or type(row["seq"]) is not int or not -1 <= row["seq"] <= last_sequence:
            raise DSHSemanticError("invalid DSH projection-cache unit checkpoint")
    row = record["rows"].get("tokenUsage")
    if not isinstance(row, dict) or row["ver"] != 2 or row["seq"] != last_sequence or not isinstance(row["val"], dict) or set(row["val"]) != {"totals", "last"}:
        raise DSHSemanticError("unsupported or stale DSH tokenUsage checkpoint")
    usage = row["val"]
    totals, last = usage["totals"], usage["last"]
    def buckets(value):
        return isinstance(value, dict) and set(value) == set(BUCKETS) and all(type(count) is int and count >= 0 for count in value.values())
    if not buckets(totals) or not isinstance(last, dict) or set(last) != {"turn", "step", "buckets"} or not buckets(last["buckets"]) or any(type(last[key]) is not int or last[key] < 0 for key in ("turn", "step")):
        raise DSHSemanticError("malformed DSH persisted usage buckets")
    # Verify the checkpoint against the actual settled native steps. These
    # retained runs have unique (turn,step) settlements and no attempt retries.
    settlements = decoded["usage_steps"]
    coordinates = [(item["turn_id"], item["response_id"]) for item in settlements]
    if len(coordinates) != len(set(coordinates)):
        raise DSHSemanticError("DSH cache fold verification has ambiguous settlements")
    aliases = {"uncachedInputTokens": "inputTokens", "outputTokens": "outputTokens", "cacheReadTokens": "cacheReadTokens", "cacheWriteTokens": "cacheWriteTokens"}
    if any(any(type(item["tokens"].get(native)) is not int or item["tokens"][native] < 0 for native in aliases.values()) for item in settlements):
        raise DSHSemanticError("DSH checkpoint cannot be verified from missing native usage")
    sums = {key: sum(item["tokens"][native] for item in settlements) for key, native in aliases.items()}
    if sums != totals:
        raise DSHSemanticError("DSH persisted tokenUsage totals differ from native settlements")
    final = next((row["raw"]["data"] for row in reversed(decoded["records"]) if row["type"] == "assistant/message" and "usage" in row["raw"]["data"]), None)
    if final is None or (last["turn"], last["step"]) != (final.get("turn"), final.get("step")) or last["buckets"] != {key: final["usage"].get(native) for key, native in aliases.items()}:
        raise DSHSemanticError("DSH persisted last-step usage differs from native settlement")
    locator = {"id": "native-companion:session-projcache.json", "sha256": hashlib.sha256(data).hexdigest()}
    return {"reconciliation": [{"id": "persisted-session-tokenUsage", "matches_session_totals": True,
                                "session_totals": {semantic: totals[key] for key, semantic in BUCKETS.items()}, "locator": locator}],
            "density_record": {"record_id": "session-projcache-v7", "record_kind": "metadata", "classification": "unclassified", "logical_bytes": len(canonical(value))},
            "locator": locator, "schema": "projection-cache-v7/tokenUsage-v2"}
