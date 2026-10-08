#!/usr/bin/env python3
"""Validate and display expanded v1 preparation; never infer scores from status."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.release_scope import load_release_scope, release_scope_summary


def build_status(path: Path) -> dict:
    source = json.loads(path.read_text())
    if set(source) != {"observed_on", "scope_id", "rows"}:
        raise ValueError("unexpected readiness ledger fields")
    date.fromisoformat(source["observed_on"])
    scope = load_release_scope()
    if source["scope_id"] != scope["release_id"]:
        raise ValueError("ledger scope mismatch")
    # This ledger supplies no evaluated score packages, so it cannot promote
    # any row into the rankable set or claim a publication gate has passed.
    summary = release_scope_summary(scope, source["rows"])
    evidence = {}
    for row in summary["rows"]:
        for reference in row["evidence_refs"]:
            relative = PurePosixPath(reference)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != reference or "\\" in reference:
                raise ValueError("unsafe readiness evidence reference")
            target = ROOT / reference
            if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(ROOT):
                raise ValueError(f"missing readiness evidence: {reference}")
            evidence[reference] = hashlib.sha256(target.read_bytes()).hexdigest()
    return {
        "schema_version": "session-bench-release-readiness-v1",
        "observed_on": source["observed_on"],
        "publication_eligible": False,
        "release_goal_complete": False,
        "ledger_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "evidence_sha256": evidence,
        **summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=ROOT / "plans/survival-v1/expanded-release-status.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = build_status(args.ledger)
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded)
    print(json.dumps({key: result[key] for key in ("release_id", "scoped_rows", "attempted_rows", "rankable_rows", "incomplete_rows", "unattempted_rows", "publication_eligible")}, sort_keys=True))


if __name__ == "__main__":
    main()
