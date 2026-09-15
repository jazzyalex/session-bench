#!/usr/bin/env python3
"""Create one immutable OpenCode correction package from an explicit run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.opencode_evidence import (  # noqa: E402
    OpenCodeEvidenceError,
    package_opencode_correction,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--historical-package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        summary = package_opencode_correction(
            ROOT,
            args.run,
            args.historical_package,
            args.output,
        )
    except (OpenCodeEvidenceError, OSError, ValueError) as exc:
        print(f"correction refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
