"""Package one quiesced synthetic Cursor Desktop transcript plus selected SQLite rows.

Input paths are explicit. The command never discovers or opens another Cursor
session, and it refuses to overwrite an existing native bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.adapters.cursor_decoder import decode_cursor_desktop_bundle  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package(attempt: Path, project: Path, transcript: Path, session_id: str) -> dict:
    attempt = attempt.resolve(strict=True)
    project = project.resolve(strict=True)
    transcript = transcript.resolve(strict=True)
    if transcript.stem != session_id or transcript.parent.name != session_id:
        raise ValueError("transcript does not match explicit session ID")
    raw = transcript.read_bytes()
    run_marker = "SB_SURVIVAL_V1_RUN_" + attempt.name.replace("-", "_")
    if run_marker.encode() not in raw:
        raise ValueError("fresh transcript lacks attempt canary")
    companion = attempt / "companions/native-sanitized/session-companions.jsonl"
    if not companion.is_file():
        raise ValueError("selected SQLite companion derivative is missing")
    private = attempt / "native-private"
    public = attempt / "native-sanitized"
    private.mkdir(exist_ok=False)
    public.mkdir(exist_ok=False)
    (private / "cursor-session.jsonl").write_bytes(raw)
    shutil.copy2(companion, public / "session-companions.jsonl")
    text = raw.decode("utf-8")
    replacements = [
        (str(project), "$RUN_PROJECT"),
        (str(project).replace("/private/tmp/", "/tmp/"), "$RUN_PROJECT"),
        (session_id, "$SESSION_ID"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)
    sanitized = text.encode("utf-8")
    if re.search(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|/Users/[^\s\"\\]+|/(?:private/)?tmp/sbcd\w+", sanitized):
        raise ValueError("sanitized transcript contains a private path or identifier")
    (public / "cursor-session.jsonl").write_bytes(sanitized)
    receipt = {
        "kind": "sanitized_native_derivative",
        "raw_publication_state": "withheld",
        "native_session_key_sha256": sha(session_id.encode()),
        "raw_sha256": sha(raw),
        "sanitized_sha256": sha(sanitized),
        "line_count": len(sanitized.splitlines()),
        "companion_sha256": sha(companion.read_bytes()),
        "companion_row_count": len(companion.read_bytes().splitlines()),
        "redactions": ["run project path", "native session ID"],
    }
    (public / "sanitization-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    decoded = decode_cursor_desktop_bundle(public)
    (attempt / "decoded.json").write_text(json.dumps(decoded.as_dict(), ensure_ascii=False, indent=2) + "\n")
    summary = {"attempt_id": attempt.name, "turns": len(decoded.turns), "responses": len(decoded.responses), "actions": len(decoded.actions), "results": len(decoded.results), "relations": len(decoded.relations), "semantic_sha256": decoded.semantic_sha256}
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.attempt, args.project, args.transcript, args.session_id)))


if __name__ == "__main__":
    main()
