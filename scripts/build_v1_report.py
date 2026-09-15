#!/usr/bin/env python3
"""Build the offline v1 report-card prototype.

The command accepts a JSON report payload and writes exactly the local public
artifacts used by the prototype: ``index.html``, ``scorecard.svg``, and
``report.json``.  It never discovers session stores, contacts a service, or
collects live data.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.v1_report import (  # noqa: E402
    build_authoritative_control_report,
    load_authoritative_report,
    load_report,
    render_authoritative_report,
    render_report,
)


DEFAULT_INPUT = ROOT / "fixtures" / "scenarios" / "survival-v1" / "public-report-demo.json"


def build(input_path: Path | str = DEFAULT_INPUT, output_dir: Path | str = "artifacts/v1-report") -> Path:
    """Render one explicit report payload into a local output directory."""

    source = Path(input_path).resolve()
    output = Path(output_dir).resolve()
    if output == source or source in output.parents:
        raise ValueError("output directory must not be the report input directory")
    return render_report(load_report(source), output)


def build_authoritative(input_path: Path | str, output_dir: Path | str) -> Path:
    """Validate and render an authoritative serialized public report."""

    source = Path(input_path).resolve()
    output = Path(output_dir).resolve()
    if output == source or source in output.parents:
        raise ValueError("output directory must not be the report input directory")
    return render_authoritative_report(load_authoritative_report(source), output)


def build_authoritative_control(output_dir: Path | str) -> Path:
    """Render the five-surface constructed control through the real scorer."""

    return render_authoritative_report(build_authoritative_control_report(), Path(output_dir).resolve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="explicit offline report JSON payload")
    parser.add_argument("--out", type=Path, required=True, help="new or existing local output directory")
    parser.add_argument("--authoritative", action="store_true", help="validate --input as an authoritative public report")
    parser.add_argument("--authoritative-control", action="store_true", help="build the five-surface constructed control through the public scorer")
    args = parser.parse_args(argv)
    if args.authoritative and args.authoritative_control:
        parser.error("--authoritative and --authoritative-control are mutually exclusive")
    if args.authoritative_control:
        output = build_authoritative_control(args.out)
    elif args.authoritative:
        output = build_authoritative(args.input, args.out)
    else:
        output = build(args.input, args.out)
    print(output / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
