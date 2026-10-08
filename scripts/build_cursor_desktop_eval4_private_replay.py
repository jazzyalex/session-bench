"""Offline, private replay of retained eval4 bytes; never reads a live Cursor store.

The CLI has fixed source/output paths. The library entry point accepts temporary
paths for synthetic tests. Its decoder receipt verifies normalized bytes, not
sanitization, native-family completeness, or independent observer evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.adapters.cursor_decoder import (  # noqa: E402
    DESKTOP_DECODER_CONTRACT_VERSION, decode_cursor_desktop_bundle,
)

ATTEMPT = "cursor-desktop-eval-4"
SOURCE = ROOT / "artifacts/survival-v1-runs" / ATTEMPT
OUTPUT = ROOT / "artifacts/v1-expanded-preparation/cursor-desktop-eval4-private-replay-v1"
FILES = (
    "attempt.json",
    "capture/r1-transcript-checkpoint.private.jsonl",
    "capture/r1-transcript-checkpoint-receipt.private.json",
    "capture/r2-transcript-checkpoint.private.jsonl",
    "capture/r2-transcript-checkpoint-receipt.private.json",
    "capture/r2-global-selected-session-rows.private.jsonl",
    "capture/r2-global-selected-session-rows-receipt.private.json",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _pairs(items: list) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def parse(raw: bytes | str):
    return json.loads(raw, object_pairs_hook=_pairs)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def build(source: Path, output: Path) -> dict:
    """Validate only seven named retained files, then publish an additive replay."""
    require(not source.is_symlink(), "source may not be a symlink")
    source = source.resolve(strict=True)
    output = output.absolute()
    require(not output.exists() and not output.is_symlink(), "output already exists")
    require(source != output and source not in output.parents, "output must be outside source")
    blobs = {}
    for name in FILES:
        path = source / name
        for ancestor in path.parents:
            if ancestor == source:
                break
            require(not ancestor.is_symlink(), "source ancestor may not be a symlink")
        require(not path.is_symlink() and path.resolve(strict=True).is_relative_to(source), "source file escapes retained root")
        require(path.stat().st_size <= 8 * 1024 * 1024, "source exceeds byte bound")
        blobs[name] = path.read_bytes()
    attempt = parse(blobs[FILES[0]])
    observed = attempt["observed"]
    require(attempt["attempt_id"] == ATTEMPT, "wrong attempt identity")
    require(attempt.get("score_eligible") is False and observed.get("native_root_complete") is False,
            "source must retain incomplete, non-score-eligible status")
    r1, r2, rows_raw = (blobs[FILES[i]] for i in (1, 3, 5))
    r1_receipt, r2_receipt, rows_receipt = (parse(blobs[FILES[i]]) for i in (2, 4, 6))
    for ordinal, raw, receipt in ((1, r1, r1_receipt), (2, r2, r2_receipt)):
        require(receipt.get("schema_version") == f"cursor-desktop-r{ordinal}-transcript-checkpoint-v1", "wrong transcript receipt schema")
        require(receipt.get("attempt_id") == ATTEMPT, "receipt attempt identity mismatch")
        require(receipt.get("transcript_sha256") == sha(raw) and receipt.get("size_bytes") == len(raw)
                and receipt.get("line_count") == len(raw.splitlines()), "transcript receipt mismatch")
        require(observed.get(f"native_transcript_r{ordinal}_sha256") == sha(raw), "attempt transcript hash mismatch")
    require(r2.startswith(r1), "R1 transcript is not an exact prefix of R2")
    require(rows_receipt.get("schema_version") == "cursor-desktop-exact-session-global-store-receipt-v1", "wrong selected-row receipt schema")
    require(rows_receipt.get("attempt_id") == ATTEMPT, "row receipt attempt mismatch")
    require(rows_receipt.get("private_rows_sha256") == sha(rows_raw)
            and rows_receipt.get("private_rows_size_bytes") == len(rows_raw), "selected-row receipt mismatch")
    require(observed.get("global_store_rows_sha256") == sha(rows_raw)
            and observed.get("global_store_receipt_sha256") == sha(blobs[FILES[6]]), "attempt selected-row hash mismatch")
    rows = [parse(line) for line in rows_raw.splitlines()]
    require(len(rows) == rows_receipt.get("selected_row_count") == observed.get("global_store_selected_row_count") == 25,
            "expected exactly 25 retained selected rows")
    require(all(isinstance(row, dict) and set(row) == {"store", "key", "value"}
                and row["store"] == "global.cursorDiskKV" and isinstance(row["key"], str)
                and isinstance(row["value"], str) for row in rows), "invalid selected-row shape")
    require(len({row["key"] for row in rows}) == 25, "duplicate selected-row key")
    composers = [row for row in rows if row["key"].startswith("composerData:")]
    require(len(composers) == 1, "expected one composer")
    session = composers[0]["key"].split(":", 1)[1]
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", session) is not None, "invalid native session UUID")
    require(parse(composers[0]["value"]).get("composerId") == session, "composer identity mismatch")
    session_hash = sha(session.encode())
    require(observed.get("native_session_key_sha256") == rows_receipt.get("session_key_sha256")
            == r1_receipt.get("session_key_sha256") == session_hash, "session hash mismatch")
    if "session_key_sha256" in r2_receipt:
        require(r2_receipt["session_key_sha256"] == session_hash, "R2 session hash mismatch")
    counts = {"composerData": 0, "bubbleId": 0, "checkpointId": 0, "ofsContent": 0}
    for row in rows:
        parts = row["key"].split(":", 2)
        require(parts[0] in counts and len(parts) == (2 if parts[0] == "composerData" else 3)
                and parts[1] == session and all(parts), "foreign session or key family")
        counts[parts[0]] += 1
    require(counts == rows_receipt.get("key_family_counts") == observed.get("global_store_key_family_counts")
            == {"bubbleId": 20, "checkpointId": 3, "composerData": 1, "ofsContent": 1}, "key family count mismatch")
    require(rows_receipt.get("native_family_complete") is False and rows_receipt.get("score_eligible") is False,
            "selected rows must not declare complete native evidence")
    transcript = b"".join(canonical(parse(line)) for line in r2.replace(session.encode(), b"$SESSION_ID").splitlines())
    companions = b"".join(canonical(parse(canonical(row).replace(session.encode(), b"$SESSION_ID"))) for row in rows)
    receipt = {
        "kind": "private_session_identity_normalization_v1", "public_safe": False,
        "sanitization_performed": False, "native_family_complete": False, "score_eligible": False,
        "sanitized_sha256": sha(transcript), "line_count": len(transcript.splitlines()),
        "companion_sha256": sha(companions), "companion_row_count": 25,
        "native_session_key_sha256": session_hash,
        "note": "sanitized_sha256 is the existing decoder field name; this derivative is private and retains paths and encoded native data.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".eval4-private-replay-", dir=output.parent))
    try:
        bundle = temporary / "native-private-normalized"
        bundle.mkdir()
        (bundle / "cursor-session.jsonl").write_bytes(transcript)
        (bundle / "session-companions.jsonl").write_bytes(companions)
        (bundle / "sanitization-receipt.json").write_bytes(canonical(receipt))
        decoded = decode_cursor_desktop_bundle(bundle).as_dict()
        require(len(decoded["turns"]) == len(decoded["responses"]) == 2, "two-turn boundaries missing")
        composer = parse(composers[0]["value"])
        ids = [item["bubbleId"] for item in composer["fullConversationHeadersOnly"]]
        checkpoint_ids = {parse(row["value"]).get("checkpointId") for row in rows if row["key"].startswith("bubbleId:")}
        checkpoint_ids.discard(None)
        require(len(ids) == rows_receipt.get("composer_header_count") == 20, "composer receipt mismatch")
        require(len(checkpoint_ids) == rows_receipt.get("referenced_checkpoint_count") == 2
                and 3 - len(checkpoint_ids) == rows_receipt.get("unreferenced_checkpoint_row_count") == 1,
                "checkpoint receipt mismatch")
        report = {
            "schema_version": "cursor-desktop-eval4-private-replay-v1", "attempt_id": ATTEMPT,
            "decoder_contract": DESKTOP_DECODER_CONTRACT_VERSION,
            "decoder_source_sha256": sha((ROOT / "session_bench/adapters/cursor_decoder.py").read_bytes()),
            "sources": {name: {"sha256": sha(raw), "size_bytes": len(raw)} for name, raw in blobs.items()},
            "normalization": "canonical JSONL and literal native session UUID replaced with $SESSION_ID; no field synthesis or sanitization",
            "native_session_key_sha256": session_hash,
            "derived": {"transcript_sha256": sha(transcript), "companion_sha256": sha(companions),
                        "decoded_sha256": sha(canonical(decoded)), "semantic_sha256": decoded["semantic_sha256"]},
            "counts": {key: len(decoded[key]) for key in ("turns", "responses", "actions", "results", "relations", "file_changes")},
            "known_facts": {key: value for key, value in decoded["facts"].items() if value["state"] == "known"},
            "unresolved_facts": {key: value for key, value in decoded["facts"].items() if value["state"] != "known"},
            "missing_result_exit_codes": [item["id"] for item in decoded["results"] if item.get("exit_code") is None],
            "unresolved": ["missing tool exit codes", "usage accounting", "complete native root/family", "independent observer truth", "31-cell completion", "numeric score eligibility"],
            "public_safe": False, "native_family_complete": False, "observer_independent": False,
            "score_eligible": False, "score": None,
        }
        (temporary / "decoded.private.json").write_bytes(canonical(decoded))
        (temporary / "replay-report.private.json").write_bytes(canonical(report))
        require(not output.exists(), "output already exists")
        temporary.rename(output)
    except BaseException:
        shutil.rmtree(temporary)
        raise
    return report


def main() -> None:
    report = build(SOURCE, OUTPUT)
    print(json.dumps({"output": str(OUTPUT), "counts": report["counts"], "score_eligible": False, "public_safe": False}))


if __name__ == "__main__":
    main()
