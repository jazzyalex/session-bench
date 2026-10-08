#!/usr/bin/env python3
"""Validate one frozen Copilot capture without launching Copilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.copilot_capture_qualification import qualify_copilot_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_root", type=Path, help="explicit capture directory")
    parser.add_argument("--output", type=Path, required=True, help="new path for the offline qualification receipt")
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("qualification output must be a new file")
    receipt = qualify_copilot_capture(args.capture_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "qualified_capture", "output": str(args.output), "run_id": receipt["run_id"],
                      "native_event_count": receipt["native_event_count"],
                      "native_tool_calls": receipt["native_tool_calls"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
