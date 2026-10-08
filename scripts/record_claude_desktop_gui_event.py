#!/usr/bin/env python3
"""Stamp a displayed Claude Desktop response after its accessibility observation.

Submitted turns are timestamped by the workspace's UserPromptSubmit hook so the
clock event precedes Claude's first tool call.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from session_bench.claude_desktop_gui_event_clock import record_gui_event  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    entered_at = datetime.now(timezone.utc)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--event-kind", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.event_kind != "assistant_response":
        parser.error("user-turn timestamps must come from the UserPromptSubmit hook")
    record_gui_event(run_id=args.run_id, event_id=args.event_id,
                     event_kind=args.event_kind, ledger_path=args.ledger,
                     clock=lambda: entered_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
