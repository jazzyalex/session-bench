import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.bundle import canonical, validate_bundle
from session_bench.c04_fixture import build_c04_portability_fixture, derive_c04_portability_fixture
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle


REPO = Path(__file__).parents[1]


def _tree_digests(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_intact_c04_fixture_uses_two_physical_artifacts_and_one_run_attempt(tmp_path):
    bundle = build_c04_portability_fixture(tmp_path / "intact")
    manifest, _, _ = validate_bundle(bundle)
    assert manifest["execution"] == {
        "state": "valid", "reason": "one constructed C04 scenario run and attempt",
        "attempt": 1, "scenario_runs": 1, "native_sessions": 2,
    }
    decoded = decode_native(bundle / "native")
    by_session = {}
    for event in decoded["events"]:
        by_session.setdefault(event["session_id"], []).append(event)
    assert set(by_session) == {"c04-session-a", "c04-session-b"}
    assert {event["locator"]["artifact_id"] for event in by_session["c04-session-a"]} == {"native-session-a"}
    assert {event["locator"]["artifact_id"] for event in by_session["c04-session-b"]} == {"native-session-b"}
    assert [event["id"] for event in by_session["c04-session-a"]].count("anchor") == 1
    assert [event["id"] for event in by_session["c04-session-b"]].count("anchor") == 1
    continuation = next(event for event in by_session["c04-session-b"] if event["id"] == "continuation")
    assert continuation["fields"]["continuation_from_session_id"] == "c04-session-a"
    result, _ = evaluate_bundle(bundle, decoder=decode_native)
    assert all(metric["state"] == "pass" for metric in result["metrics"])


def test_damaged_session_b_fixture_preserves_source_and_fails_c04(tmp_path):
    source = build_c04_portability_fixture(tmp_path / "source")
    source_digests = _tree_digests(source)
    derived = derive_c04_portability_fixture(source, tmp_path / "damaged", "damage_session_b_continuation")
    assert _tree_digests(source) == source_digests
    source_manifest, _, _ = validate_bundle(source)
    derived_manifest, _, _ = validate_bundle(derived)
    assert derived_manifest["run_id"] != source_manifest["run_id"]
    assert derived_manifest["capture_id"] != source_manifest["capture_id"]
    receipt = json.loads((derived / "provenance/mutation.json").read_text(encoding="utf-8"))
    assert receipt["changed_native_locations"][0]["artifact_id"] == "native-session-b"
    assert receipt["changed_native_locations"][0]["before_sha256"] != receipt["changed_native_locations"][0]["after_sha256"]
    result, _ = evaluate_bundle(derived, decoder=decode_native)
    assert next(metric for metric in result["metrics"] if metric["scope"] == "C04")["state"] in {"fail", "unresolved"}


def test_missing_session_b_companion_invalidates_bundle_and_decoder_reports_it(tmp_path):
    source = build_c04_portability_fixture(tmp_path / "source")
    derived = derive_c04_portability_fixture(source, tmp_path / "missing", "missing_session_b_companion")
    with pytest.raises(ValueError, match="inventory mismatch|integrity mismatch"):
        validate_bundle(derived)
    decoded = decode_native(derived / "native")
    codes = {diagnostic["code"] for diagnostic in decoded["diagnostics"]}
    assert {"missing_artifact", "missing_dependency"}.issubset(codes)


def test_copied_c04_native_package_decodes_identically_after_source_deletion(tmp_path):
    source = build_c04_portability_fixture(tmp_path / "source")
    delivered = tmp_path / "delivered"
    shutil.copytree(source, delivered)
    before = decode_native(delivered / "native")
    shutil.rmtree(source)
    after = decode_native(delivered / "native")
    assert canonical(before) == canonical(after)


def test_checked_in_c04_packs_regenerate_byte_identically(tmp_path):
    generated = tmp_path / "generated"
    intact = build_c04_portability_fixture(generated / "c04-portability-intact")
    derive_c04_portability_fixture(intact, generated / "c04-portability-damaged",
                                   "damage_session_b_continuation")
    derive_c04_portability_fixture(intact, generated / "c04-portability-missing-companion",
                                   "missing_session_b_companion")
    checked_in = REPO / "fixtures" / "scenarios" / "v1"
    for name in ("c04-portability-intact", "c04-portability-damaged", "c04-portability-missing-companion"):
        assert _tree_digests(generated / name) == _tree_digests(checked_in / name)
