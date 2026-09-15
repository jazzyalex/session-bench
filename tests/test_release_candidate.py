import csv
import io
import json
from pathlib import Path

import pytest

from scripts.build_v1_release_candidate import DEFAULT_OPENCODE_PACKET, DEFAULT_PACKETS
from session_bench.release_candidate import (
    ReleaseCandidateError,
    assemble_review_candidate,
    assemble_release_candidate,
    load_public_configuration_packet,
)
from session_bench.v1_public_score import TARGET_CONFIGURATIONS


ROOT = Path(__file__).resolve().parents[1]


def _packet_roots() -> dict[str, Path]:
    return {
        configuration_id: (
            DEFAULT_OPENCODE_PACKET
            if configuration_id == "opencode-cli"
            else DEFAULT_PACKETS / configuration_id
        )
        for configuration_id in TARGET_CONFIGURATIONS
    }


@pytest.mark.parametrize("configuration_id", TARGET_CONFIGURATIONS)
def test_load_public_configuration_packet_returns_manifest_digest(configuration_id: str) -> None:
    packet = load_public_configuration_packet(_packet_roots()[configuration_id])

    assert packet.configuration_id == configuration_id
    assert isinstance(packet.manifest_sha256, str)
    assert len(packet.manifest_sha256) == 64
    assert packet.score.rankable is True


def test_release_candidate_fails_closed_without_full_independent_receipts(tmp_path: Path) -> None:
    with pytest.raises(ReleaseCandidateError, match="independent reproduction receipt is missing"):
        assemble_release_candidate(
            _packet_roots(),
            receipts_root=None,
            output_dir=tmp_path / "candidate",
            generated_at="2026-09-14T00:00:00Z",
        )


def test_unpublished_review_candidate_builds_without_independent_receipts(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    result = assemble_review_candidate(
        _packet_roots(),
        output_dir=output,
        generated_at="2026-09-14T00:00:00Z",
    )

    candidate = json.loads((output / "review-candidate.json").read_text())
    report = json.loads((output / "report.json").read_text())
    evidence_index = json.loads((output / "evidence-index.json").read_text())
    leaderboard = list(csv.DictReader(io.StringIO((output / "leaderboard.csv").read_text())))
    html = (output / "index.html").read_text()
    svg = (output / "scorecard.svg").read_text()
    ranks = {row["configuration_id"]: row["rank"] for row in report["configurations"]}
    scores = {row["configuration_id"]: row["overall_points"] for row in report["configurations"]}

    assert result["publication_eligible"] is False
    assert candidate["status"] == "unpublished_review_candidate"
    assert candidate["verification"] == "Locally reproduced"
    assert candidate["publication_status"] == "unpublished"
    assert candidate["independent_native_reproduction"] is False
    assert candidate["publication"] == {"eligible": False, "owner_release_instruction_required": True, "published": False}
    assert candidate["gates"]["independent_native_reproduction"] is False
    assert candidate["publication_blockers"] == ["independent_native_reproduction_incomplete"]
    assert evidence_index["cohort"]["all_independent_receipts_verified"] is False
    assert evidence_index["cohort"]["leaderboard_eligible"] is False
    assert evidence_index["verification"] == "Locally reproduced"
    assert evidence_index["publication_status"] == "unpublished"
    assert evidence_index["independent_native_reproduction"] is False
    assert all(item["verification"] == "Locally reproduced" for item in leaderboard)
    assert all(item["publication_status"] == "unpublished" for item in leaderboard)
    assert all(item["independent_native_reproduction"] == "false" for item in leaderboard)
    assert report["data_status"] == "UNPUBLISHED LOCAL EVIDENCE"
    assert ranks["codex-cli"] == ranks["codex-desktop"] == 1
    assert round(scores["codex-cli"], 1) == round(scores["codex-desktop"], 1) == 87.0
    assert "v1 unpublished review candidate" in html
    assert "UNPUBLISHED LOCAL EVIDENCE" in html
    assert "Locally reproduced" in html
    assert "best preserved work trail under this task" in html
    assert "Usage is evaluated separately" in html
    assert "Usage 0/15" in html
    assert "Ties use the displayed one-decimal score." in html
    assert "v1 unpublished review candidate" in svg
    assert "UNPUBLISHED LOCAL EVIDENCE" in svg


def test_review_candidate_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    output.mkdir()
    with pytest.raises(ReleaseCandidateError, match="refusing to overwrite"):
        assemble_review_candidate(
            _packet_roots(),
            output_dir=output,
            generated_at="2026-09-14T00:00:00Z",
        )


def test_public_semantic_receipt_cannot_clear_native_reproduction_gate(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    source = ROOT / "artifacts" / "v1-independent-reproduction" / "opencode-cli.json"
    (receipts / "codex-cli.json").write_bytes(source.read_bytes())

    with pytest.raises(ReleaseCandidateError, match="wrong fields|unsupported independent receipt schema"):
        assemble_release_candidate(
            _packet_roots(),
            receipts_root=receipts,
            output_dir=tmp_path / "candidate",
            generated_at="2026-09-14T00:00:00Z",
        )


def test_public_packet_root_symlink_is_refused(tmp_path: Path) -> None:
    alias = tmp_path / "packet-alias"
    alias.symlink_to(_packet_roots()["codex-cli"], target_is_directory=True)

    with pytest.raises(ReleaseCandidateError, match="not an ordinary directory"):
        load_public_configuration_packet(alias)
