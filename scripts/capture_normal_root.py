#!/usr/bin/env python3
"""Record or close an authorized normal-root metadata boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.normal_root_capture import (
    NormalRootFile,
    copy_verified_family,
    identify_new_family,
    inventory_normal_root,
    wait_for_family_quiescence,
)


def _write(path: Path, value: object) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--out", type=Path, required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--root", type=Path, required=True)
    capture.add_argument("--before", type=Path, required=True)
    capture.add_argument("--destination", type=Path, required=True)
    capture.add_argument("--receipt", type=Path, required=True)
    capture.add_argument("--primary-path", required=True)
    capture.add_argument("--companion-path", action="append", default=[])
    capture.add_argument(
        "--allow-preexisting-changes",
        action="store_true",
        help="tolerate metadata changes/removals for old files in a shared live root; old bytes remain unopened",
    )
    args = parser.parse_args()

    if args.command == "snapshot":
        entries = inventory_normal_root(args.root)
        _write(args.out, {"schema_version": "session-bench-normal-root-snapshot-v1", "files": [entry.to_dict() for entry in entries]})
        print(args.out)
        return 0

    before_document = json.loads(args.before.read_text())
    if set(before_document) != {"schema_version", "files"} or before_document["schema_version"] != "session-bench-normal-root-snapshot-v1":
        raise ValueError("unsupported before snapshot")
    before = tuple(NormalRootFile.from_mapping(item) for item in before_document["files"])
    after = inventory_normal_root(args.root)
    family = identify_new_family(
        before,
        after,
        primary_path=args.primary_path,
        required_companion_paths=args.companion_path,
        allow_preexisting_changes=args.allow_preexisting_changes,
    )
    quiescence = wait_for_family_quiescence(
        args.root,
        family,
        before=before,
        checks=2,
        interval_seconds=0.1,
        allow_preexisting_changes=args.allow_preexisting_changes,
    )
    artifacts = copy_verified_family(args.root, args.destination, family)
    _write(args.receipt, {
        "schema_version": "session-bench-normal-root-capture-v1",
        "root_label": args.root.name,
        "before_snapshot": args.before.name,
        "primary_path": family.primary_path,
        "companion_paths": list(family.companion_paths),
        "preexisting_changes_tolerated": args.allow_preexisting_changes,
        "quiescence": quiescence.to_dict(),
        "artifacts": list(artifacts),
    })
    print(args.receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
