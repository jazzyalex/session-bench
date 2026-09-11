import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from session_bench.atlas import render_atlas, validate_atlas
from session_bench.bundle import read_json


REPO = Path(__file__).parents[1]
ATLAS_PATH = REPO / "atlas" / "v1" / "index.json"
RENDERED_PATH = REPO / "docs" / "atlas" / "README.md"


def _atlas():
    return copy.deepcopy(read_json(ATLAS_PATH))


def test_documentation_atlas_covers_cli_desktop_and_ide_without_measurement():
    atlas = validate_atlas(_atlas())
    assert {entry["identity"]["surface"] for entry in atlas["entries"]} == {"cli", "desktop", "ide"}
    for entry in atlas["entries"]:
        assert entry["status"] == "documented_candidate"
        assert entry["measurement"] is None
        assert entry["maintenance"]["live_tested_at"] is None
        assert entry["artifact_family"]["identity_state"] == "unknown"


def test_atlas_rejects_duplicate_entries_and_missing_maintenance_metadata():
    atlas = _atlas()
    atlas["entries"].append(copy.deepcopy(atlas["entries"][0]))
    with pytest.raises(ValueError, match="duplicate entry_id"):
        validate_atlas(atlas)

    atlas = _atlas()
    atlas["entries"][0]["maintenance"].pop("maintenance_owner")
    with pytest.raises(ValueError, match="missing required fields"):
        validate_atlas(atlas)


def test_candidate_cannot_become_measured_by_status_or_date_alone():
    atlas = _atlas()
    atlas["entries"][0]["status"] = "measured"
    with pytest.raises(ValueError, match="requires measurement identities"):
        validate_atlas(atlas)

    atlas = _atlas()
    atlas["entries"][0]["maintenance"]["live_tested_at"] = "2026-09-10"
    with pytest.raises(ValueError, match="cannot carry live measurement"):
        validate_atlas(atlas)


def test_documentation_cannot_establish_writer_decoder_or_reproduction():
    for subject in ("writer_behavior", "decoder_correctness", "reproduction"):
        atlas = _atlas()
        claim = next(item for item in atlas["entries"][0]["claims"] if item["subject"] == subject)
        claim.update(state="pass", evidence_kind="public_documentation",
                     source_ids=[atlas["entries"][0]["sources"][0]["id"]])
        with pytest.raises(ValueError, match="cannot establish|cannot claim"):
            validate_atlas(atlas)


def test_shared_provisional_family_does_not_merge_surface_identities():
    atlas = _atlas()
    for entry in atlas["entries"][:2]:
        entry["artifact_family"]["identity_state"] = "documented"
        entry["artifact_family"]["family_id"] = "provisional-shared-family"
    validated = validate_atlas(atlas)
    assert validated["entries"][0]["entry_id"] != validated["entries"][1]["entry_id"]
    assert validated["entries"][0]["identity"]["surface"] != validated["entries"][1]["identity"]["surface"]


def test_render_is_deterministic_and_marks_freshness():
    atlas = _atlas()
    rendered = render_atlas(atlas, "2026-09-10")
    assert rendered == render_atlas(atlas, "2026-09-10")
    assert rendered.encode() == RENDERED_PATH.read_bytes()
    assert "no vendor result or qualification" in rendered.lower()
    assert "| current; due 2026-10-10 | not tested |" in rendered
    assert "| stale; due 2026-10-10 | not tested |" in render_atlas(atlas, "2026-10-11")


def test_atlas_cli_validation_and_render(tmp_path):
    validated = subprocess.run(
        [sys.executable, "-m", "session_bench", "validate-atlas", str(ATLAS_PATH)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["entries"] == 3

    output = tmp_path / "atlas.md"
    rendered = subprocess.run(
        [sys.executable, "-m", "session_bench", "render-atlas", str(ATLAS_PATH),
         "--as-of", "2026-09-10", "--out", str(output)],
        cwd=REPO, capture_output=True, text=True,
    )
    assert rendered.returncode == 0, rendered.stderr
    assert output.read_bytes() == RENDERED_PATH.read_bytes()


def test_atlas_is_independent_of_constructed_registry():
    atlas = validate_atlas(_atlas())
    assert atlas["schema_version"] == "1.0-atlas"
    assert all(entry["status"] != "constructed_only" for entry in atlas["entries"])
