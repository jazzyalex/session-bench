#!/usr/bin/env python3
"""Project explicitly packaged prototype captures into a blocked survival pretest.
This command is offline-only.  It never discovers product stores, launches a
vendor executable, contacts a network service, or emits a leaderboard score.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.survival_bridge import build_pretest_report, write_pretest_report


def _capture_dirs(captures_root: Path) -> list[Path]:
    """Enumerate only direct child capture packages with metadata.json."""

    root = captures_root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("--captures must be an explicit real directory")
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and not child.is_symlink() and (child / "metadata.json").is_file()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a no-ranking survival-v1 pretest from packaged prototype captures."
    )
    parser.add_argument(
        "--captures",
        type=Path,
        default=ROOT / "artifacts/prototype-v1/captures",
        help="directory containing explicitly packaged capture directories",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="optional JSON output path; otherwise print the redacted report",
    )
    args = parser.parse_args(argv)

    captures = _capture_dirs(args.captures)
    report = build_pretest_report(captures)
    if args.out is not None:
        output = write_pretest_report(captures, args.out)
        print(output)
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
