#!/usr/bin/env python3
"""Deterministic helper for the survival-v1 workload.

The helper is a public synthetic test asset. It has no clock, randomness, network,
credential, or subprocess dependency. Live adapters may pass a fresh run canary and
fixed nonce; the default values make this checked-in fixture reproducible.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


CASES = (
    {"items": [[10, 2], [5, 1]], "expected": 30},
    {"items": [[25, 2]], "expected": 50},
    {"items": [[49, 1]], "expected": 54},
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cases(app: Path) -> tuple[list[dict], int]:
    namespace: dict[str, object] = {}
    exec(compile(app.read_text(encoding="utf-8"), str(app), "exec"), namespace)
    checkout = namespace["checkout"]
    outcomes = []
    for case in CASES:
        actual = checkout(case["items"])
        outcomes.append(
            {
                "items": case["items"],
                "expected": case["expected"],
                "actual": actual,
                "passed": actual == case["expected"],
            }
        )
    code = 0 if all(outcome["passed"] for outcome in outcomes) else 1
    return outcomes, code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("inspect", "baseline", "final"))
    parser.add_argument(
        "--run-canary",
        default=os.environ.get("SB_SURVIVAL_V1_RUN_CANARY", "SB_SURVIVAL_V1_RUN_fixture_0001"),
    )
    parser.add_argument("--helper-nonce", default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    app = root / "checkout.py"
    nonce = args.helper_nonce or f"{args.phase}-fixture-0001"
    app_sha256 = digest(app)
    if args.phase == "inspect":
        body = {
            "phase": args.phase,
            "checkout_sha256": app_sha256,
            "checkout_source": app.read_text(encoding="utf-8"),
        }
        exit_code = 0
    else:
        outcomes, exit_code = run_cases(app)
        body = {"phase": args.phase, "tests": outcomes}

    output = (
        f"SB_SURVIVAL_V1_HELPER_{args.phase.upper()}_{nonce} "
        + json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    event = {
        "schema_version": "1.0-survival-helper-ledger",
        "id": f"helper-{args.phase}-{nonce}",
        "phase": args.phase,
        "run_canary": args.run_canary,
        "helper_nonce": nonce,
        "argv": ["python3", "bench_check.py", args.phase],
        "cwd": "fixture_project",
        "output": output,
        "exit_code": exit_code,
        "checkout_sha256": app_sha256,
    }
    with (root / ".survival-observer.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    print(output)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
