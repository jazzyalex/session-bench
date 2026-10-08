#!/usr/bin/env python3
"""Prepare (or explicitly execute) one bounded Hermes survival-v1 capture."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    home = Path.home()
    sys.path.insert(0, str(repository))
    from session_bench.hermes_survival_capture import (
        continue_hermes_capture_after_r1,
        execute_hermes_capture,
        planned_argv,
        prepare_hermes_capture,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_id", help="new capture ID suffix, e.g. hermes-codex-01")
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--python", type=Path,
                        default=home / ".hermes/tools/python-3.14.7+20260901-darwin-arm64/bin/python3")
    parser.add_argument("--source-root", type=Path, default=home / ".hermes/hermes-agent")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--execute", action="store_true",
                        help="submit the two bounded model turns after offline preparation")
    parser.add_argument("--continue-after-r1", action="store_true",
                        help="validate preserved R1 and submit only its same-session R2")
    parser.add_argument("--print-argv", action="store_true",
                        help="offline check: print the planned argv of both turns as JSON; nothing is created or started")
    args = parser.parse_args()
    destination = repository / "artifacts/v1-expanded-preparation/live-captures" / args.attempt_id
    if args.print_argv:
        import json
        from session_bench.workload_instance import instantiate_workload
        template = json.loads((repository / "fixtures/scenarios/survival-v1/workload/workload.json").read_bytes())
        workload, _ = instantiate_workload(template, args.attempt_id)
        turns = planned_argv(python=args.python, source_root=args.source_root, workspace="<scratch>/workspace",
                             prompts=[row["text"] for row in workload["turns"]])
        print(json.dumps({"attempt_id": args.attempt_id, "model_submissions": 0, "destination_exists": destination.exists(),
                          "environment": "PATH, HOME, LANG, TMPDIR, HERMES_IGNORE_USER_CONFIG=1, HERMES_IGNORE_RULES=1, "
                                         "SB_SURVIVAL_V1_RUN_CANARY; HERMES_HOME unset",
                          "turn_1": turns[0], "turn_2": turns[1]}, ensure_ascii=False, indent=2))
        return 0
    if args.continue_after_r1:
        result = continue_hermes_capture_after_r1(destination, timeout=args.timeout)
        print(f"{destination}: {result['status']} ({result['model_submissions_in_continuation']} continuation submissions)")
        if result.get("failure"):
            print(result["failure"], file=sys.stderr)
        return 0 if result["status"] == "captured_pending_qualification" else 1
    prepare_hermes_capture(destination, repository=repository, repetition=args.repetition,
                           python=args.python, source_root=args.source_root)
    if args.execute:
        result = execute_hermes_capture(destination, timeout=args.timeout)
        print(f"{destination}: {result['status']} ({result['model_submissions']} model submissions)")
        if result.get("failure"):
            print(result["failure"], file=sys.stderr)
        for refusal in sorted(destination.glob("r*-refusal.json")):
            print(f"refusal listing: {refusal}", file=sys.stderr)
        return 0 if result["status"] == "captured_pending_qualification" else 1
    print(f"Prepared without model submission: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
