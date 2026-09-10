import copy
import hashlib
import json
from pathlib import Path

import pytest

from session_bench.bundle import canonical, validate_bundle
from session_bench.decoders import decode_native
from session_bench.fixtures import build_fixture
from session_bench.locators import validate_inspections
from session_bench.evaluate import evaluate_decoded


def _persist_expected(bundle: Path, expected: dict) -> None:
    path = bundle / "expected" / "assertions.json"
    data = canonical(expected) + b"\n"
    path.write_bytes(data)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    artifact = next(item for item in manifest["artifacts"] if item["path"] == "expected/assertions.json")
    artifact["size_bytes"] = len(data)
    artifact["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_bytes(canonical(manifest) + b"\n")


def _present_expected(bundle: Path, event_id: str = "m-1") -> tuple[dict, dict]:
    manifest, expected, _ = validate_bundle(bundle)
    decoded = decode_native(bundle / "native")
    event = next(event for event in decoded["events"] if event["id"] == event_id)
    target = next(assertion for assertion in expected["assertions"] if assertion["event_id"] == event_id)
    target["inspection"] = {"state": "present", "locators": [copy.deepcopy(event["locator"])], "evidence_ids": ["provenance-mutation.json"]}
    _persist_expected(bundle, expected)
    return manifest, expected


def test_persisted_inspection_locator_requires_digest_and_coordinates(tmp_path: Path) -> None:
    bundle = build_fixture(tmp_path / "missing", "constructed-jsonl-v1")
    manifest, expected = _present_expected(bundle)
    target = next(assertion for assertion in expected["assertions"] if assertion["event_id"] == "m-1")
    target["inspection"]["locators"][0].pop("record_sha256")
    _persist_expected(bundle, expected)
    with pytest.raises(ValueError, match="record digest"):
        validate_inspections(bundle, manifest, expected)


def test_persisted_inspection_locator_must_target_expected_event_and_fields(tmp_path: Path) -> None:
    bundle = build_fixture(tmp_path / "wrong", "constructed-jsonl-v1")
    manifest, expected = _present_expected(bundle)
    target = next(assertion for assertion in expected["assertions"] if assertion["event_id"] == "m-1")
    other = next(event for event in decode_native(bundle / "native")["events"] if event["id"] == "m-2")
    target["inspection"]["locators"] = [other["locator"]]
    _persist_expected(bundle, expected)
    with pytest.raises(ValueError, match="wrong event"):
        validate_inspections(bundle, manifest, expected)


def test_persisted_inspection_locator_rejects_correct_identity_with_wrong_field(tmp_path: Path) -> None:
    bundle = build_fixture(tmp_path / "wrong-field", "constructed-jsonl-v1")
    manifest, expected = _present_expected(bundle)
    target = next(assertion for assertion in expected["assertions"] if assertion["event_id"] == "m-1")
    field = next(field for field in target["fields"] if field["name"] == "fields.text")
    field["expected"] = "wrong persisted value"
    _persist_expected(bundle, expected)
    with pytest.raises(ValueError, match="field mismatch"):
        validate_inspections(bundle, manifest, expected)


def test_valid_persisted_locator_supports_decoder_omission_classification(tmp_path: Path) -> None:
    bundle = build_fixture(tmp_path / "omitted", "constructed-jsonl-v1")
    manifest, expected = _present_expected(bundle, "tr-2")
    validate_inspections(bundle, manifest, expected)
    decoded = decode_native(bundle / "native")
    decoded["events"] = [event for event in decoded["events"] if event["id"] != "tr-2"]
    result = evaluate_decoded(manifest, expected, decoded, hashlib.sha256(canonical(manifest)).hexdigest())
    row = next(row for row in result["rows"] if row["id"] == "event.tr-2")
    assert row["outcome"] == "retained_decoder_incomplete"
    assert row["state"] == "fail"
