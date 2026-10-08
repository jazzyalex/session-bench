#!/usr/bin/env python3
"""Prepare or execute one bounded Pi survival-v1 capture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.pi_survival_capture import prepare_pi_capture, execute_pi_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_id")
    parser.add_argument("--repetition", type=int, required=True, choices=(1, 2, 3))
    parser.add_argument("--execute", action="store_true", help="submit each workload turn once")
    parser.add_argument("--executable", default="pi")
    args = parser.parse_args()
    destination = ROOT / "artifacts/v1-expanded-preparation/live-captures" / args.attempt_id
    prepared = prepare_pi_capture(destination, repository=ROOT, repetition=args.repetition,
                                  executable=args.executable)
    result = execute_pi_capture(destination) if args.execute else {"status": prepared["status"], "model_submissions": 0}
    print(json.dumps({"attempt_id": args.attempt_id, "status": result["status"],
                      "model_submissions": result.get("model_submissions", 0),
                      "capture_root": str(destination)}, sort_keys=True))
    return 0 if result["status"] in {"prepared", "captured_pending_qualification"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
