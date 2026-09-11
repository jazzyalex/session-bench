import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from session_bench.c03_contract import c03_contract_summary, validate_c03_contract


REPO = Path(__file__).parents[1]


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contract(root):
    manifest_path = root / "fixture" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "capture_id": "c03-capture", "run_id": "c03-run", "track": "native_local",
    }), encoding="utf-8")
    return {
        "schema_version": "1.0-c03",
        "contract_id": "constructed-c03-control",
        "base": {
            "manifest_path": "fixture/manifest.json", "manifest_sha256": _digest(manifest_path),
            "capture_id": "c03-capture", "run_id": "c03-run", "track": "native_local",
            "native_session_ids": ["session-a"],
        },
        "observation": {
            "method": "synthetic_control", "independent": True, "complete": True,
            "blind_spots": [], "sha256": "1" * 64,
        },
        "copy_receipt": {
            "source_bundle_sha256": "4" * 64, "copied_bundle_sha256": "4" * 64,
            "decoded_semantics_identical": True, "original_bundle_access": "denied",
            "original_code_access": "denied", "application_access": "denied",
            "backend_access": "denied", "network_access": "denied", "sha256": "3" * 64,
        },
        "archive": {
            "scenario": "C03-A", "state": "pass", "target_session_id": "session-a",
            "close_observation_ids": ["close-1"], "reopen_observation_ids": ["reopen-1"],
            "expected_event_ids": ["event-1", "event-2"],
            "recovered_event_ids": ["event-1", "event-2"], "duplicate_event_ids": [],
            "decoder_evaluation_id": "eval-c03-a",
        },
        "continuation": {
            "scenario": "C03-B", "state": "unsupported", "support": "unsupported",
            "accepted": None, "parent_session_id": None, "continued_session_id": None,
            "lineage_state": "unknown", "selection_observation_ids": [],
            "continuation_observation_ids": [], "duplicate_event_ids": [],
            "reason": "synthetic archive control does not exercise a native writer",
        },
        "c06_recovery": {
            "scenario": "C06", "state": "incomplete",
            "reason": "process termination recovery requires separate evidence",
        },
        "controls": [
            {"kind": "intact", "expected_measurement": "preserved", "observed_measurement": "pass",
             "source_bundle_sha256": "4" * 64, "derived_bundle_sha256": None,
             "frozen_observer_sha256": "5" * 64, "frozen_assertions_sha256": "6" * 64,
             "changed_native_locations": [], "receipt_sha256": "7" * 64},
            {"kind": "damaged", "expected_measurement": "loss_detected", "observed_measurement": "fail",
             "source_bundle_sha256": "4" * 64, "derived_bundle_sha256": "8" * 64,
             "frozen_observer_sha256": "5" * 64, "frozen_assertions_sha256": "6" * 64,
             "changed_native_locations": ["native-session:line-2"], "receipt_sha256": "9" * 64},
            {"kind": "missing_companion", "expected_measurement": "invalid_evidence", "observed_measurement": "invalid",
             "source_bundle_sha256": "4" * 64, "derived_bundle_sha256": "a" * 64,
             "frozen_observer_sha256": "5" * 64, "frozen_assertions_sha256": "6" * 64,
             "changed_native_locations": ["native-attachment:missing"], "receipt_sha256": "b" * 64},
        ],
        "designation": "archive_preservation_only",
    }


def test_archive_can_pass_while_native_continuation_and_c06_remain_incomplete(tmp_path):
    contract = _contract(tmp_path)
    validate_c03_contract(contract, repository_root=tmp_path)
    summary = c03_contract_summary(contract, repository_root=tmp_path)
    assert summary["designation"] == "archive_preservation_only"
    assert summary["continuation_state"] == "unsupported"
    assert summary["c06_recovery_state"] == "incomplete"


def test_continuation_pass_requires_independent_selection_and_linked_lineage(tmp_path):
    contract = _contract(tmp_path)
    contract["continuation"].update(
        state="pass", support="exercised", accepted=True, parent_session_id="session-a",
        continued_session_id="session-a", lineage_state="linked",
        selection_observation_ids=["select-1"], continuation_observation_ids=["continued-1"],
        reason="synthetic continuation control",
    )
    contract["designation"] = "archive_and_continuation"
    validate_c03_contract(contract, repository_root=tmp_path)
    contract["continuation"]["selection_observation_ids"] = []
    with pytest.raises(ValueError, match="exercised accepted continuation"):
        validate_c03_contract(contract, repository_root=tmp_path)


def test_unsupported_continuation_never_counts_as_pass(tmp_path):
    contract = _contract(tmp_path)
    contract["continuation"]["state"] = "pass"
    contract["designation"] = "archive_and_continuation"
    with pytest.raises(ValueError, match="exercised accepted continuation|unsupported C03-B"):
        validate_c03_contract(contract, repository_root=tmp_path)


def test_archive_pass_requires_exact_recovery_no_duplicates_and_complete_observation(tmp_path):
    for mutate in (
        lambda c: c["archive"]["recovered_event_ids"].pop(),
        lambda c: c["archive"]["duplicate_event_ids"].append("event-1"),
        lambda c: c["observation"].update(complete=False),
        lambda c: c["copy_receipt"].update(decoded_semantics_identical=False),
    ):
        contract = _contract(tmp_path)
        mutate(contract)
        with pytest.raises(ValueError, match="C03-A pass"):
            validate_c03_contract(contract, repository_root=tmp_path)


def test_controls_must_distinguish_preservation_loss_and_invalid_evidence(tmp_path):
    contract = _contract(tmp_path)
    contract["controls"][1]["observed_measurement"] = "pass"
    with pytest.raises(ValueError, match="distinguish preservation from loss"):
        validate_c03_contract(contract, repository_root=tmp_path)
    contract = _contract(tmp_path)
    contract["controls"][2]["changed_native_locations"] = []
    with pytest.raises(ValueError, match="requires a derived bundle"):
        validate_c03_contract(contract, repository_root=tmp_path)
    contract = _contract(tmp_path)
    contract["controls"][1]["frozen_observer_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="share one source"):
        validate_c03_contract(contract, repository_root=tmp_path)


def test_base_manifest_identity_is_bound_and_cannot_escape_root(tmp_path):
    contract = _contract(tmp_path)
    (tmp_path / "fixture" / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_c03_contract(contract, repository_root=tmp_path)
    contract = _contract(tmp_path)
    contract["base"]["manifest_path"] = "../outside.json"
    with pytest.raises(ValueError, match="resolve inside"):
        validate_c03_contract(contract, repository_root=tmp_path)


def test_c03_schema_is_closed_and_c06_cannot_be_qualified(tmp_path):
    contract = _contract(tmp_path)
    contract["archive"]["native_recovery"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        validate_c03_contract(contract, repository_root=tmp_path)
    contract = _contract(tmp_path)
    contract["c06_recovery"]["state"] = "pass"
    with pytest.raises(ValueError, match="invalid value"):
        validate_c03_contract(contract, repository_root=tmp_path)


def test_cli_validates_c03_sidecar(tmp_path):
    contract = _contract(tmp_path)
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "session_bench", "validate-c03", str(path), "--root", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, env={"PYTHONPATH": str(REPO)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["designation"] == "archive_preservation_only"
