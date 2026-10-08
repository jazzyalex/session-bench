#!/usr/bin/env python3
"""Build the additive private Codex CLI stable-root diagnostic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.codex_cli_root_diagnostic import write_codex_cli_root_diagnostic


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/v1-expanded-preparation/codex-cli-root-private-diagnostic-v1/diagnostic.json",
    )
    args = parser.parse_args()
    result = write_codex_cli_root_diagnostic(ROOT, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
