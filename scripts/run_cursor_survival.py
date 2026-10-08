#!/usr/bin/env python3
"""Prepare or execute one single-use isolated Cursor CLI benchmark capture."""
import argparse
import json
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session_bench.cursor_live_capture import prepare_cursor_capture, execute_cursor_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--normal-root", type=Path, help="Authorized existing-login root; metadata-only before/after, copy only proven-new session family")
    parser.add_argument("--shared-home", type=Path, default=Path.home() / ".cursor",
                        help="Shared Cursor home; listed by metadata before and after an isolated capture (no content is read)")
    parser.add_argument("--execute", action="store_true", help="Submit each frozen prompt once using existing subscription authentication")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    root = repository / "artifacts/survival-v1-runs" / args.attempt_id
    build = subprocess.run(["agent", "--version"], check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    plan = prepare_cursor_capture(root, attempt_id=args.attempt_id, repetition=args.repetition, repository=repository, model=args.model, build=build, normal_root=args.normal_root,
                                  shared_home=None if args.normal_root is not None else args.shared_home)
    if args.execute:
        plan = execute_cursor_capture(root)
    print(json.dumps({key: plan[key] for key in ("attempt_id", "repetition", "state", "model_submissions", "score_eligible")}, sort_keys=True))
    if plan['state'] == 'invalid':
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
