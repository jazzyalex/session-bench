#!/usr/bin/env python3
"""Build the additive private Codex CLI native timestamp diagnostic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.codex_cli_timestamp_diagnostic import write_codex_cli_timestamp_diagnostic


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = write_codex_cli_timestamp_diagnostic(ROOT, args.output)
    print(json.dumps({"metric_closure": result["metric_closure"],
                      "repetitions": [(row["run_id"], row["event_count"]) for row in result["repetitions"]]},
                     sort_keys=True))
