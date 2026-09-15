#!/usr/bin/env python3
"""Close one freshly identified Claude Desktop Code JSONL capture.

The caller must identify the synthetic session with a metadata-only before/after
inventory.  This command never discovers or reads neighbouring Claude sessions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle
from session_bench.claude_format_evidence import build_claude_format_evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_manifest(package: Path) -> dict[str, object]:
    session = package / "session.jsonl"
    value = {
        "format": "claude-code-jsonl-v1",
        "artifacts": [{
            "id": "session",
            "path": "session.jsonl",
            "sha256": _sha256(session),
            "size_bytes": session.stat().st_size,
            "depends_on": [],
        }],
    }
    _write_json(package / "decode.json", value)
    return value


def _remove_unique_canary(source: Path, destination: Path, canary: str) -> int:
    rows: list[dict[str, object]] = []
    removed = 0
    for line in source.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        message = row.get("message") if isinstance(row, dict) else None
        if (
            row.get("type") == "assistant"
            and isinstance(message, dict)
            and message.get("role") == "assistant"
            and canary in json.dumps(message.get("content"), ensure_ascii=False)
        ):
            removed += 1
            continue
        rows.append(row)
    if removed != 1:
        raise ValueError(f"expected one native row containing the R2 canary, found {removed}")
    destination.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-session", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--r2-canary", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--observer-receipt", type=Path, required=True)
    args = parser.parse_args()

    source = args.source_session.resolve()
    run_root = args.run_root.resolve()
    observer_receipt = args.observer_receipt.resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("source session must be one ordinary identified JSONL file")
    if not observer_receipt.is_file() or observer_receipt.is_symlink():
        raise ValueError("observer receipt must be an ordinary file")

    capture = run_root / "capture"
    private = capture / "native-private"
    damaged = capture / "damage-r2-private"
    if private.exists() or damaged.exists():
        raise ValueError("capture package already exists")
    private.mkdir(parents=True)
    damaged.mkdir(parents=True)
    shutil.copyfile(source, private / "session.jsonl")
    private_manifest = _write_manifest(private)
    intact = decode_claude_code_bundle(private)
    _write_json(capture / "decoded-private.json", intact)

    removed = _remove_unique_canary(private / "session.jsonl", damaged / "session.jsonl", args.r2_canary)
    _write_manifest(damaged)
    damaged_decode = decode_claude_code_bundle(damaged)
    _write_json(capture / "damage-r2-decoded-private.json", damaged_decode)
    if damaged_decode["counts"]["responses"] >= intact["counts"]["responses"]:
        raise ValueError("selected-loss control did not reduce the response population")

    observer_digest = _sha256(observer_receipt)
    native_digest = _sha256(private / "decode.json")
    format_evidence = build_claude_format_evidence(
        intact,
        observer={"id": "claude-desktop-gui-observer", "sha256": observer_digest},
        native_manifest={"id": "claude-desktop-native-manifest", "sha256": native_digest},
        run_id=args.run_id,
        configuration_id="claude-desktop",
        repetition=1,
        build=args.build,
        collected_on="2026-09-14",
        result_id=f"{args.run_id}-format-evidence",
    )
    _write_json(capture / "format-evidence-private.json", format_evidence)
    _write_json(run_root / "attempt.json", {
        "schema_version": "session-bench-claude-desktop-attempt-v1",
        "attempt_id": args.run_id,
        "configuration_id": "claude-desktop",
        "state": "captured_unscored",
        "score_eligible": False,
        "source_session_name": source.name,
        "session_id": intact["session_id"],
        "counts": intact["counts"],
        "selected_loss": {
            "native_rows_removed": removed,
            "intact_response_count": intact["counts"]["responses"],
            "damaged_response_count": damaged_decode["counts"]["responses"],
            "detected": True,
        },
        "native_manifest": private_manifest,
        "complete_cross_root_family": False,
        "canonical_copy_proven": True,
        "independent_gui_observer": True,
        "public_score_eligible": False,
    })
    print(json.dumps({
        "attempt": str(run_root / "attempt.json"),
        "session_id": intact["session_id"],
        "intact_counts": intact["counts"],
        "damaged_counts": damaged_decode["counts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
