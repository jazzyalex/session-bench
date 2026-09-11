"""Semantic validation for C03 archive and native-continuation sidecars."""

import hashlib
import json
from pathlib import Path

from .bundle import canonical, digest
from .schema import validate


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = REPOSITORY_ROOT / "schemas" / "c03" / "v1" / "contract.schema.json"
CONTROL_EXPECTATIONS = {
    "intact": ("preserved", {"pass"}),
    "damaged": ("loss_detected", {"fail", "unresolved"}),
    "missing_companion": ("invalid_evidence", {"invalid"}),
}


def _unique(values, context):
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate identity in {context}")


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_c03_contract(contract, repository_root=REPOSITORY_ROOT):
    validate(contract, json.loads(SCHEMA.read_text(encoding="utf-8")))
    base = contract["base"]
    manifest_path = Path(base["manifest_path"])
    root = Path(repository_root).resolve()
    resolved_manifest = (root / manifest_path).resolve()
    if manifest_path.is_absolute() or not resolved_manifest.is_relative_to(root) or not resolved_manifest.is_file():
        raise ValueError("C03 base manifest must resolve inside the supplied repository root")
    if _sha256(resolved_manifest) != base["manifest_sha256"]:
        raise ValueError("C03 base manifest digest mismatch")
    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    if manifest.get("capture_id") != base["capture_id"] or manifest.get("run_id") != base["run_id"]:
        raise ValueError("C03 base capture or run identity mismatch")
    if manifest.get("track") != base["track"]:
        raise ValueError("C03 base track mismatch")
    _unique(base["native_session_ids"], "C03 native sessions")

    controls = contract["controls"]
    if {control["kind"] for control in controls} != set(CONTROL_EXPECTATIONS) or len(controls) != 3:
        raise ValueError("C03 requires exactly intact, damaged, and missing-companion controls")
    for control in controls:
        expected, allowed_observed = CONTROL_EXPECTATIONS[control["kind"]]
        if control["expected_measurement"] != expected or control["observed_measurement"] not in allowed_observed:
            raise ValueError(f"C03 {control['kind']} control does not distinguish preservation from loss")
        if control["kind"] == "intact":
            if control["derived_bundle_sha256"] is not None or control["changed_native_locations"]:
                raise ValueError("C03 intact control cannot claim a derived mutation")
        elif control["derived_bundle_sha256"] is None or not control["changed_native_locations"]:
            raise ValueError(f"C03 {control['kind']} control requires a derived bundle and changed locations")
        elif control["derived_bundle_sha256"] == control["source_bundle_sha256"]:
            raise ValueError(f"C03 {control['kind']} derived bundle must differ from its source")
    if (len({control["source_bundle_sha256"] for control in controls}) != 1
            or len({control["frozen_observer_sha256"] for control in controls}) != 1
            or len({control["frozen_assertions_sha256"] for control in controls}) != 1):
        raise ValueError("C03 controls must share one source and frozen observation/answer key")

    archive = contract["archive"]
    _unique(archive["expected_event_ids"], "C03-A expected events")
    _unique(archive["recovered_event_ids"], "C03-A recovered events")
    if archive["state"] == "pass":
        if (not contract["observation"]["complete"] or contract["observation"]["blind_spots"]
                or not contract["copy_receipt"]["decoded_semantics_identical"]
                or contract["copy_receipt"]["source_bundle_sha256"] != contract["copy_receipt"]["copied_bundle_sha256"]
                or contract["copy_receipt"]["source_bundle_sha256"] != controls[0]["source_bundle_sha256"]
                or archive["expected_event_ids"] != archive["recovered_event_ids"]
                or archive["duplicate_event_ids"]):
            raise ValueError("C03-A pass requires complete independent observation and exact copied recovery")

    continuation = contract["continuation"]
    if continuation["state"] == "pass":
        if (continuation["support"] != "exercised" or continuation["accepted"] is not True
                or continuation["lineage_state"] != "linked" or not continuation["parent_session_id"]
                or not continuation["continued_session_id"] or not continuation["selection_observation_ids"]
                or not continuation["continuation_observation_ids"] or continuation["duplicate_event_ids"]):
            raise ValueError("C03-B pass requires exercised accepted continuation with independent linked lineage")
    elif continuation["support"] == "unsupported" and continuation["state"] not in {"unsupported", "not_run"}:
        raise ValueError("unsupported C03-B cannot pass or receive a measured failure state")

    archive_pass = archive["state"] == "pass"
    continuation_pass = continuation["state"] == "pass"
    expected_designation = ("archive_and_continuation" if archive_pass and continuation_pass else
                            "archive_preservation_only" if archive_pass else "incomplete")
    if contract["designation"] != expected_designation:
        raise ValueError("C03 designation does not match separate archive and continuation states")
    if contract["c06_recovery"]["state"] not in {"not_assessed", "incomplete"}:
        raise ValueError("C03 evidence cannot qualify C06 process recovery")
    return contract


def c03_contract_summary(contract, repository_root=REPOSITORY_ROOT):
    validate_c03_contract(contract, repository_root=repository_root)
    return {
        "contract_id": contract["contract_id"],
        "archive_state": contract["archive"]["state"],
        "continuation_state": contract["continuation"]["state"],
        "c06_recovery_state": contract["c06_recovery"]["state"],
        "designation": contract["designation"],
        "contract_sha256": digest(canonical(contract)),
    }
