#!/usr/bin/env python3
"""Record local copied-package replay and selected-loss receipts outside a package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/recheck_opencode_package.py"


def _run(package: Path, *, damage: bool) -> dict:
    command = [sys.executable, str(RUNNER), "--package", str(package)]
    if damage:
        command.append("--damage-control")
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "recheck failed")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("recheck output must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    package = args.package.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if output.exists() or output.is_symlink():
        print("receipt refused: output already exists", file=sys.stderr)
        return 1
    try:
        replay = _run(package, damage=False)
        damage = _run(package, damage=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"receipt refused: {exc}", file=sys.stderr)
        return 1
    receipt = {
        "schema_version": "session-bench-opencode-local-replay-receipt-v1",
        "scope": "local copied-package replay; not independent reproduction",
        "independent_reproduction": False,
        "package": replay,
        "selected_loss_control": damage,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
