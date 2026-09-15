"""Controls for the unified five-configuration public evidence index."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import build_public_evidence_index as evidence_index


def _inputs_present() -> bool:
    return all(spec.root.is_dir() for spec in evidence_index.PACKETS)


@pytest.mark.skipif(not _inputs_present(), reason="public packet inputs are not present")
def test_index_builds_verifies_and_is_byte_deterministic(tmp_path: Path) -> None:
    first_root = tmp_path / "index-one"
    second_root = tmp_path / "index-two"

    first = evidence_index.build(output=first_root)
    second = evidence_index.build(output=second_root)

    assert first["configuration_ids"] == list(evidence_index.CONFIGURATION_IDS)
    assert first["packet_bytes_copied"] is False
    assert first["cross_configuration_rank_published"] is False
    assert first["privacy_findings"] == []
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert second["index_sha256"] == first["index_sha256"]

    first_files = {
        path.relative_to(first_root): path.read_bytes()
        for path in first_root.rglob("*")
        if path.is_file()
    }
    second_files = {
        path.relative_to(second_root): path.read_bytes()
        for path in second_root.rglob("*")
        if path.is_file()
    }
    assert first_files == second_files
    assert evidence_index._verify_output(first_root)["privacy_findings"] == []


@pytest.mark.skipif(not _inputs_present(), reason="public packet inputs are not present")
def test_index_fails_closed_on_missing_configuration_id(tmp_path: Path) -> None:
    output = tmp_path / "index"
    evidence_index.build(output=output)
    index = evidence_index._read_json(output / "index.json")
    index["configuration_ids"] = list(evidence_index.CONFIGURATION_IDS[:-1])
    evidence_index._write_json(output / "index.json", index)

    with pytest.raises(evidence_index.PublicEvidenceIndexError, match="configuration boundary"):
        evidence_index._verify_output(output)


@pytest.mark.skipif(not _inputs_present(), reason="public packet inputs are not present")
def test_index_retains_source_publication_and_independence_fields(tmp_path: Path) -> None:
    output = tmp_path / "index"
    evidence_index.build(output=output)
    index = evidence_index._read_json(output / "index.json")

    for entry in index["configurations"]:
        fields = entry["source_publication_state"]["source_fields"]
        assert fields["independent_reproduction"] is False
        if entry["packet_kind"] == "survival":
            assert fields["published"] is False
            assert fields["publication_status"] == "private_unpublished"
        else:
            assert fields["public_claim_allowed"] is False
            assert fields["global_rank_allowed"] is False


def test_index_builder_declares_only_references_and_no_packet_copy() -> None:
    assert evidence_index.CONFIGURATION_IDS == (
        "codex-cli",
        "codex-desktop",
        "claude-cli",
        "claude-desktop",
        "opencode-cli",
    )
    assert all(spec.root != evidence_index.DEFAULT_OUTPUT for spec in evidence_index.PACKETS)
