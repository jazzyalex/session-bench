#!/usr/bin/env python3
"""Prepare (or explicitly execute) one bounded OpenClaw survival-v1 capture in the owner's normal state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    from session_bench.openclaw_state_capture import (
        GATEWAY_ENVIRONMENT, ROUTES, TURN_TIMEOUT_SECONDS, VERSION, acp_argv, execute_openclaw_capture, gateway_argv, planned_argv,
        preflight_argv, prepare_openclaw_capture, rebracket_openclaw_capture,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_id", help="new capture ID, e.g. openclaw-2026-10-08-01")
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--executable", type=Path, default=None, help="the openclaw executable (default: the one on PATH)")
    parser.add_argument("--openclaw-home", type=Path, default=None, help="the normal OpenClaw home (default: ~/.openclaw)")
    parser.add_argument("--expect-version", default=VERSION, help="the exact output of `openclaw --version` that the capture accepts")
    parser.add_argument("--timeout", type=float, default=TURN_TIMEOUT_SECONDS + 120.0,
                        help="seconds the controller waits for one turn; the CLI itself gets --timeout 300")
    parser.add_argument("--route", choices=ROUTES, default="local",
                        help="local: two `openclaw agent --local` runs (no tool events for the observer). "
                             "acp: a foreground gateway that the controller starts and stops, and one ACP session (tool events)")
    parser.add_argument("--rebracket", action="store_true",
                        help="route acp only: take the native state of a capture whose bracket was refused, with the present "
                             "rule table, when the home did not change since; no model call")
    parser.add_argument("--execute", action="store_true",
                        help="place the fixture in the agent workspace, submit the two model turns, remove the fixture")
    parser.add_argument("--print-argv", action="store_true",
                        help="offline check: print the planned argv of both turns as JSON; nothing is created or started")
    args = parser.parse_args()
    executable = args.executable or (Path(found) if (found := shutil.which("openclaw")) else None)
    if executable is None:
        print("openclaw is not on PATH; pass --executable", file=sys.stderr)
        return 1
    destination = repository / "artifacts/v1-expanded-preparation/live-captures" / args.attempt_id
    if args.rebracket:
        result = rebracket_openclaw_capture(destination)
        print(f"{destination}: {result['status']} (rebracket, 0 model submissions)")
        if result.get("failure"):
            print(result["failure"], file=sys.stderr)
        return 0 if result["status"] == "captured_pending_qualification" else 1
    if args.print_argv and args.route == "acp":
        print(json.dumps({"attempt_id": args.attempt_id, "route": "acp", "model_submissions": 0, "destination_exists": destination.exists(),
                          "environment": "PATH, HOME, LANG, TMPDIR, SB_SURVIVAL_V1_RUN_CANARY; no OPENCLAW_ or CLAWDBOT_ variable from outside",
                          "cwd": "the agent workspace that `config get agents.defaults.workspace` prints",
                          "preflight": preflight_argv(executable), "gateway": gateway_argv(executable),
                          "gateway_environment_added": GATEWAY_ENVIRONMENT,
                          "acp": acp_argv(executable, "agent:main:explicit:<new uuid>"),
                          "acp_requests": ["initialize", "session/new", "session/prompt (turn 1)", "session/prompt (turn 2)"]},
                         ensure_ascii=False, indent=2))
        return 0
    if args.print_argv:
        turns = planned_argv(executable=executable, destination=destination)
        print(json.dumps({"attempt_id": args.attempt_id, "model_submissions": 0, "destination_exists": destination.exists(),
                          "environment": "PATH, HOME, LANG, TMPDIR, SB_SURVIVAL_V1_RUN_CANARY; no OPENCLAW_ or CLAWDBOT_ variable",
                          "cwd": "the agent workspace that `config get agents.defaults.workspace` prints",
                          "preflight": preflight_argv(executable), "turn_1": turns[0], "turn_2": turns[1]}, ensure_ascii=False, indent=2))
        return 0
    prepare_openclaw_capture(destination, repository=repository, repetition=args.repetition, executable=executable,
                             openclaw_home=args.openclaw_home, expected_version=args.expect_version, route=args.route)
    if not args.execute:
        print(f"Prepared without model submission: {destination}")
        return 0
    result = execute_openclaw_capture(destination, timeout=args.timeout)
    print(f"{destination}: {result['status']} ({result['model_submissions']} model submissions)")
    if result.get("failure"):
        print(result["failure"], file=sys.stderr)
    for refusal in sorted(destination.glob("r*-refusal.json")):
        print(f"refusal listing: {refusal}", file=sys.stderr)
    if result.get("GATEWAY_NOT_STOPPED"):
        print(f"\n*** GATEWAY NOT STOPPED: pid {result['GATEWAY_NOT_STOPPED']} ***\n"
              "*** Stop this process by hand now. It runs with the owner's configuration. ***", file=sys.stderr)
    if result.get("FIXTURE_NOT_REMOVED"):
        print(f"\n*** FIXTURE NOT REMOVED: {result['FIXTURE_NOT_REMOVED']} ***\n"
              "*** Remove this directory from the agent workspace by hand before any other OpenClaw run. ***", file=sys.stderr)
        return 3
    if result.get("GATEWAY_NOT_STOPPED"):
        return 4
    if result.get("fixture_placed") and result.get("fixture_removed"):
        print("fixture removed from the agent workspace")
    return 0 if result["status"] == "captured_pending_qualification" else 1


if __name__ == "__main__":
    raise SystemExit(main())
