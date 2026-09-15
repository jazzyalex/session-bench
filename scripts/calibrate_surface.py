#!/usr/bin/env python3
"""Render a bounded surface calibration plan without launching a model.
This command is intentionally a dry-run helper.  Live acquisition belongs to
an explicitly authorized caller that supplies an injected runner and auth
prerequisite to :class:`CodexCLIAdapter`; this script never reads a normal
Codex home and never copies credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_bench.adapters.codex_cli import (  # noqa: E402
    CodexCLIAdapter,
    CodexCLIIdentity,
    CodexCLIRunRoots,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True, help="fresh empty root for the dry calibration plan")
    parser.add_argument("--build", default="unknown", help="declared Codex CLI build")
    parser.add_argument("--model", default="declared-at-run", help="declared model identity")
    parser.add_argument("--prompt", default="survival-v1 calibration prompt")
    args = parser.parse_args(argv)
    roots = CodexCLIRunRoots.create(args.run_root).roots
    plan = CodexCLIAdapter(runner=object()).calibration_plan(
        roots,
        args.prompt,
        identity=CodexCLIIdentity(build=args.build, model=args.model),
    )
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
