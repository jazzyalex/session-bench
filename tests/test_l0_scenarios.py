import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.bundle import validate_bundle
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle
from session_bench.fixtures import build_fixture, damage_codex_rollout
from session_bench.l0_scenarios import (
    FROZEN_POSITIVE_CONTROL_ORDER, PositiveControl, make_damage_receipt,
    select_positive_control, verify_damage_control,
)


def test_positive_control_uses_priority_then_fallback():
    candidates = [
        {"assertion_id": FROZEN_POSITIVE_CONTROL_ORDER[0], "independently_observed": True,
         "native_present": False, "correctly_reconstructed": True, "native_locations": ["bad"]},
        {"assertion_id": FROZEN_POSITIVE_CONTROL_ORDER[1], "independently_observed": True,
         "native_present": True, "correctly_reconstructed": True, "native_locations": ["line-2", "line-4"]},
    ]
    selected = select_positive_control(candidates)
    assert selected.assertion_id == FROZEN_POSITIVE_CONTROL_ORDER[1]
    assert selected.native_locations == ("line-2", "line-4")


def test_positive_control_requires_all_evidence_and_fails_closed():
    with pytest.raises(ValueError, match="no positive control"):
        select_positive_control([{"assertion_id": FROZEN_POSITIVE_CONTROL_ORDER[0],
                                 "independently_observed": True, "native_present": True,
                                 "correctly_reconstructed": False, "native_locations": ["x"]}])


def test_damage_receipt_binds_selected_assertion_locations_and_manifests(tmp_path: Path):
    selected = select_positive_control([{
        "assertion_id": "C02.inspect_target_and_source_bytes", "independently_observed": True,
        "native_present": True, "correctly_reconstructed": True,
        "native_locations": ["rollout.jsonl:line-2"],
    }])
    source = b"source-manifest"
    derived = b"derived-manifest"
    receipt = make_damage_receipt(
        selected_assertion=selected,
        changed_native_locations=["rollout.jsonl:line-2"],
        source_decode_manifest=source,
        derived_decode_manifest=derived,
        transformation="remove selected record",
    )
    assert receipt["selected_assertion"] == selected.assertion_id
    assert receipt["changed_native_locations"] == ["rollout.jsonl:line-2"]
    assert receipt["source_decode_manifest_sha256"] == hashlib.sha256(source).hexdigest()
    assert receipt["derived_decode_manifest_sha256"] == hashlib.sha256(derived).hexdigest()


def test_damage_copy_keeps_nonmatching_native_lines_byte_identical(tmp_path: Path):
    source = tmp_path / "source"; source.mkdir()
    rollout = source / "rollout.jsonl"
    lines = [
        b'{"type":"session_meta","payload":{"id":"s"}}\n',
        b'{"type":"event_msg","payload":{"id":"keep","message":"unchanged"}}\n',
        b'{"type":"response_item","payload":{"call_id":"damage","status":"failure"}}\n',
    ]
    rollout.write_bytes(b"".join(lines))
    manifest = {"format": "codex-rollout-v1", "artifacts": [{"id": "rollout", "path": "rollout.jsonl", "sha256": hashlib.sha256(rollout.read_bytes()).hexdigest(), "size_bytes": rollout.stat().st_size, "depends_on": []}]}
    (source / "decode.json").write_text(json.dumps(manifest))
    selected = select_positive_control([{
        "assertion_id": "C02.failing_test_status_and_exit_code", "independently_observed": True,
        "native_present": True, "correctly_reconstructed": True,
        "native_locations": ["rollout:rollout.jsonl:line-3"],
    }])
    damaged, receipt = damage_codex_rollout(source, tmp_path / "damaged", "damage", selected_assertion=selected)
    damaged_lines = damaged.joinpath("rollout.jsonl").read_bytes().splitlines(keepends=True)
    assert damaged_lines[0] == lines[0]
    assert damaged_lines[1] == lines[1]
    assert receipt["selected_assertion"] == selected.assertion_id
    assert receipt["changed_native_locations"] == ["rollout:rollout.jsonl:line-3"]


def test_damage_control_proves_intact_pass_to_loss_with_frozen_answer_keys(tmp_path: Path):
    source = build_fixture(tmp_path / "source")
    source_manifest_path = source / "manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    expected_path = source / "expected/assertions.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    next(item for item in expected["assertions"] if item["id"] == "event.tr-2")["id"] = FROZEN_POSITIVE_CONTROL_ORDER[0]
    expected_path.write_text(json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    expected_artifact = next(item for item in source_manifest["artifacts"] if item["path"] == "expected/assertions.json")
    expected_artifact["sha256"] = hashlib.sha256(expected_path.read_bytes()).hexdigest()
    expected_artifact["size_bytes"] = expected_path.stat().st_size
    source_manifest_path.write_text(json.dumps(source_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    derived = tmp_path / "derived"
    shutil.copytree(source, derived)
    native = derived / "native/session.jsonl"
    rows = [json.loads(line) for line in native.read_text(encoding="utf-8").splitlines()]
    line_number = next(index for index, row in enumerate(rows, 1) if row["id"] == "tr-2")
    rows = [row for row in rows if row["id"] != "tr-2"]
    native.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    manifest_path = derived / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_manifest_sha = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    manifest["origin"] = "derived_mutation"
    manifest["provenance"]["source_manifest_sha256"] = source_manifest_sha
    manifest["provenance"]["transformation"] = "remove selected tr-2 native row"
    artifact = next(item for item in manifest["artifacts"] if item["path"] == "native/session.jsonl")
    artifact["sha256"] = hashlib.sha256(native.read_bytes()).hexdigest()
    artifact["size_bytes"] = native.stat().st_size
    decode_path = derived / "native/decode.json"
    decode = json.loads(decode_path.read_text(encoding="utf-8"))
    decode["artifacts"][0]["sha256"] = artifact["sha256"]
    decode["artifacts"][0]["size_bytes"] = artifact["size_bytes"]
    decode_path.write_text(json.dumps(decode, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    decode_artifact = next(item for item in manifest["artifacts"] if item["path"] == "native/decode.json")
    decode_artifact["sha256"] = hashlib.sha256(decode_path.read_bytes()).hexdigest()
    decode_artifact["size_bytes"] = decode_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    validate_bundle(source)
    validate_bundle(derived)
    intact, _ = evaluate_bundle(source, decoder=decode_native)
    damaged, _ = evaluate_bundle(derived, decoder=decode_native)
    selected = select_positive_control([{
        "assertion_id": FROZEN_POSITIVE_CONTROL_ORDER[0],
        "independently_observed": True, "native_present": True,
        "correctly_reconstructed": True,
        "native_locations": [f"native/session.jsonl:line-{line_number}"],
    }])
    receipt = verify_damage_control(source_bundle=source, derived_bundle=derived,
        selected_assertion=selected, changed_native_locations=selected.native_locations,
        source_result=intact, derived_result=damaged, transformation="remove selected tr-2 native row")
    assert receipt["source_state"] == "pass"
    assert receipt["derived_state"] == "unresolved"
    assert receipt["observer_sha256"] == hashlib.sha256((derived / "observer/events.json").read_bytes()).hexdigest()
    assert receipt["expectations_sha256"] == hashlib.sha256((derived / "expected/assertions.json").read_bytes()).hexdigest()
