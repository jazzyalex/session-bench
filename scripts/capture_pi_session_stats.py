#!/usr/bin/env python3
"""Read Pi session totals from exact copied captures without prompting a model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.pi_session_stats import REQUEST_ID, validate_pi_session_stats


def _strict_object(raw: bytes, label: str) -> dict:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def collect_stats(attempt_id: str, *, executable: str = "pi") -> dict:
    if not attempt_id.startswith("pi-") or Path(attempt_id).name != attempt_id:
        raise ValueError("attempt ID must be a bounded pi-* path component")
    capture = ROOT / "artifacts/v1-expanded-preparation/live-captures" / attempt_id
    native_path = capture / "turn-r2/native/session.jsonl"
    native_receipt = _strict_object((capture / "turn-r2/native/receipt.json").read_bytes(), "native receipt")
    capture_result = _strict_object((capture / "capture-result.json").read_bytes(), "capture result")
    session_bytes = native_path.read_bytes()
    digest = hashlib.sha256(session_bytes).hexdigest()
    if (capture_result.get("status") != "captured_pending_qualification"
            or native_receipt.get("sha256") != digest
            or native_receipt.get("size_bytes") != len(session_bytes)):
        raise ValueError("exact copied Pi native session did not pass its retained capture receipts")
    native_filename = Path(native_receipt.get("relative_path", "")).name
    if not native_filename.endswith(".jsonl"):
        raise ValueError("native capture receipt lacks a JSONL basename")

    temp_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="pi-session-stats-", dir=temp_parent) as temporary:
        root = Path(temporary)
        home = root / "home"
        config = root / "config"
        sessions = root / "sessions"
        home.mkdir(); config.mkdir(); sessions.mkdir()
        session_copy = sessions / native_filename
        session_copy.write_bytes(session_bytes)
        before = hashlib.sha256(session_copy.read_bytes()).hexdigest()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
               "PI_CODING_AGENT_DIR": str(config), "PI_OFFLINE": "1",
               "PI_TELEMETRY": "0", "TERM": "dumb"}
        version = subprocess.run([executable, "--version"], capture_output=True,
                                 env=env, timeout=30, check=False)
        version_text = version.stdout.decode("utf-8", "strict").strip()
        if version.returncode != 0 or version_text != "1.0.0":
            raise ValueError("session-stats collector requires Pi 1.0.0")
        argv = [executable, "--session", str(session_copy), "--mode", "rpc",
                "--no-extensions", "--no-skills", "--no-context-files",
                "--no-prompt-templates", "--no-themes", "--no-tools", "--offline"]
        request = {"id": REQUEST_ID, "type": "get_session_stats"}
        process = subprocess.run(argv, input=(json.dumps(request) + "\n").encode(),
                                 capture_output=True, env=env, timeout=45, check=False)
        after = hashlib.sha256(session_copy.read_bytes()).hexdigest()
        if process.returncode != 0 or before != after or after != digest:
            raise ValueError("Pi stats RPC failed or changed the copied native session")
        receipt = validate_pi_session_stats(process.stdout, session_bytes=session_bytes,
                    session_filename=native_filename, pi_version=version_text)
        receipt.update({"attempt_id": attempt_id,
                       "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                       "rpc_stdout_sha256": hashlib.sha256(process.stdout).hexdigest(),
                       "rpc_stderr_sha256": hashlib.sha256(process.stderr).hexdigest(),
                       "network_disabled": True, "user_config_disabled": True,
                       "model_submissions": 0})

    stats_path = capture / "observer/pi-session-stats.json"
    targets = {
        stats_path:
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        capture / "observer/pi-session-stats.stdout.jsonl": process.stdout,
        capture / "observer/pi-session-stats.stderr.txt": process.stderr,
    }
    if any(target.exists() or target.is_symlink() for target in targets):
        raise ValueError("Pi stats receipt already exists; refusing to overwrite")
    for target, contents in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents)
    return {"attempt_id": attempt_id, "session_id": receipt["session_id"],
            "session_sha256": receipt["session_sha256"], "receipt_path": str(stats_path),
            "rpc_stdout_sha256": receipt["rpc_stdout_sha256"], "model_submissions": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_ids", nargs="+", help="exact retained Pi capture attempt IDs")
    parser.add_argument("--executable", default="pi")
    args = parser.parse_args()
    print(json.dumps({"schema_version": "session-bench-pi-stats-collection-v1",
                      "runs": [collect_stats(attempt_id, executable=args.executable)
                               for attempt_id in args.attempt_ids]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
