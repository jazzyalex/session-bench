#!/usr/bin/env python3
"""Prepare or execute one isolated Kimi survival capture; no implicit retry."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.kimi_survival_capture import prepare, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--run", action="store_true", help="submit at most one invocation per turn after offline preparation")
    args = parser.parse_args()
    destination = ROOT / "artifacts/v1-expanded-preparation/live-captures" / args.attempt_id
    config = Path.home() / ".kimi-code/config.toml"
    if args.run:
        result = run(destination, repository=ROOT, config=config)
    else:
        result = prepare(destination, repository=ROOT, executable=Path("/opt/homebrew/bin/kimi"), config=config)
    print({key: result.get(key) for key in ("attempt_id", "status", "model_submissions", "reason")})
    return 0 if result["status"] in ("prepared", "captured_pending_qualification") else 1


if __name__ == "__main__":
    raise SystemExit(main())
