#!/usr/bin/env python3
"""Build offline evidence packages for explicit captured OpenCode runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.opencode_evidence import (  # noqa: E402
    OpenCodeEvidenceError,
    package_opencode_run,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    summaries = []
    try:
        for run in args.run:
            output = run / "evaluation"
            summaries.append(package_opencode_run(ROOT, run, output))
    except (OpenCodeEvidenceError, OSError, ValueError) as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
