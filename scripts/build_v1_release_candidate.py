#!/usr/bin/env python3
"""Build the evidence-bound Session-Bench v1 release-candidate artifacts.

The command consumes five sanitized configuration packet directories and one
independent-reproduction receipt per configuration.  It emits the existing
authoritative report-card files plus a CSV leaderboard, evidence index, and a
machine-readable release-candidate gate record.  It never opens native session
stores or launches a vendor process.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_bench.release_candidate import (  # noqa: E402
    TARGET_CONFIGURATIONS,
    ReleaseCandidateError,
    assemble_release_candidate,
    assemble_review_candidate,
)


DEFAULT_PACKETS = ROOT / "artifacts" / "survival-v1-public-configurations"
DEFAULT_OPENCODE_PACKET = ROOT / "artifacts" / "opencode-v1-public-configuration"
DEFAULT_RECEIPTS = ROOT / "artifacts" / "v1-independent-reproduction"
DEFAULT_OUTPUT = ROOT / "artifacts" / "survival-v1-release-candidate"


def _default_generated_at() -> str:
    """Provide a convenient timestamp while the library API stays explicit."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build(
    *,
    packets_dir: Path | str = DEFAULT_PACKETS,
    opencode_packet: Path | str = DEFAULT_OPENCODE_PACKET,
    receipts_dir: Path | str | None = DEFAULT_RECEIPTS,
    output_dir: Path | str = DEFAULT_OUTPUT,
    generated_at: str,
    review_candidate: bool = False,
) -> dict[str, object]:
    packet_root = Path(packets_dir).resolve(strict=False)
    packet_roots = {
        configuration_id: (
            Path(opencode_packet).resolve(strict=False)
            if configuration_id == "opencode-cli"
            else packet_root / configuration_id
        )
        for configuration_id in TARGET_CONFIGURATIONS
    }
    if review_candidate:
        return assemble_review_candidate(
            packet_roots,
            output_dir=output_dir,
            generated_at=generated_at,
        )
    return assemble_release_candidate(
        packet_roots,
        receipts_root=receipts_dir,
        output_dir=output_dir,
        generated_at=generated_at,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packets", type=Path, default=DEFAULT_PACKETS, help="directory containing the four Codex and Claude sanitized packet directories")
    parser.add_argument("--opencode-packet", type=Path, default=DEFAULT_OPENCODE_PACKET, help="sanitized OpenCode configuration packet directory")
    parser.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS, help="directory containing <configuration-id>.json independent receipts")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="new output directory for the candidate report")
    parser.add_argument("--generated-at", default=None, help="explicit UTC timestamp for deterministic report generation")
    parser.add_argument("--review-candidate", action="store_true", help="build an unpublished local candidate without reading independent-reproduction receipts")
    args = parser.parse_args(argv)
    generated_at = args.generated_at or _default_generated_at()
    try:
        result = build(
            packets_dir=args.packets,
            opencode_packet=args.opencode_packet,
            receipts_dir=args.receipts,
            output_dir=args.out,
            generated_at=generated_at,
            review_candidate=args.review_candidate,
        )
    except (ReleaseCandidateError, OSError, ValueError) as exc:
        print(f"release candidate failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
