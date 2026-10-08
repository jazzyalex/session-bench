#!/usr/bin/env python3
"""Attempt at most three new free-route OpenCode captures; stop on first gap."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.opencode_expanded_capture import prepare_expanded_opencode_capture, execute_expanded_opencode_capture

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    base = ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-live-v1"
    if base.exists() and any(base.iterdir()):
        raise ValueError("expanded capture batch already prepared or attempted")
    base.mkdir(exist_ok=True)
    for repetition in (1, 2, 3):
        destination = base / f"opencode-1-18-31-eval-{repetition}"
        prepare_expanded_opencode_capture(destination, repository=ROOT, repetition=repetition)
        if args.execute:
            result = execute_expanded_opencode_capture(destination)
            print(json.dumps({key: result[key] for key in ("attempt_id", "status", "model_submissions", "reason_ids")}), flush=True)
            if result["status"] != "captured_pending_qualification": return 1
    return 0

if __name__ == "__main__": raise SystemExit(main())
