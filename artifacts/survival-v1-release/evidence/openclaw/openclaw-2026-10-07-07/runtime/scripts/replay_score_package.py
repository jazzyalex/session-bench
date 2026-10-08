#!/usr/bin/env python3
"""Run the fixed private native-to-score closure in isolated Python."""
from pathlib import Path
import json
import sys


def main() -> None:
    if len(sys.argv) not in (2, 3) or len(sys.argv) == 3 and sys.argv[2] != "--record-expected":
        raise ValueError("usage: replay_score_package.py PACKAGE [--record-expected]")
    package = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(package / "runtime"))
    from session_bench.score_replay import execute_score_packet
    result = execute_score_packet(package, record_expected=len(sys.argv) == 3)
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
