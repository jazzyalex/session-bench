#!/usr/bin/env python3
"""Render byte-exact, instantiated Survival-v1 turns for a manual surface.

This prevents a clipboard paraphrase from silently changing a calibration's
declared workload. It has no capture, vendor, or network behavior.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "fixtures/scenarios/survival-v1/workload/workload.json"
sys.path.insert(0, str(ROOT))

from session_bench.workload_instance import instantiate_workload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, help="per-attempt run identity, e.g. codex_desktop_manual_02")
    parser.add_argument("--turn", choices=("r1", "r2"), help="render one turn instead of both")
    parser.add_argument("--json", action="store_true", help="emit machine-readable turns")
    args = parser.parse_args()

    instance, _ = instantiate_workload(json.loads(WORKLOAD.read_text()), args.run_id)
    turns = instance["turns"]
    if args.turn:
        turns = [item for item in turns if item["id"] == f"turn-{args.turn}"]
    if args.json:
        print(json.dumps({"run_id": instance["run_id"], "turns": turns}, ensure_ascii=False, indent=2))
    else:
        for item in turns:
            print(f"--- {item['id']} / {instance['run_id']} ---")
            print(item["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
