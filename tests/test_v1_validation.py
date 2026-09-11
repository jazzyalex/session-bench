"""Acceptance tests for the bounded v1 evidence and evaluation contracts."""

import json
import os
from pathlib import Path

import pytest

from session_bench.bundle import (
    canonical,
    digest,
    read_json,
    validate_bundle,
    validate_named,
    validate_registry,
    validate_result,
)
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle
from session_bench.fixtures import build_fixture


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _rehash_artifact(bundle: Path, relative: str):
    """Update only the manifest entry for an intentional semantic file edit."""
    import hashlib

    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    artifact = next(item for item in manifest["artifacts"] if item["path"] == relative)
    data = (bundle / relative).read_bytes()
    artifact["sha256"] = hashlib.sha256(data).hexdigest()
    artifact["size_bytes"] = len(data)
    _write_json(manifest_path, manifest)


def _mutate_expected(bundle: Path, mutate):
    path = bundle / "expected" / "assertions.json"
    expected = _json(path)
    mutate(expected)
    _write_json(path, expected)
    _rehash_artifact(bundle, "expected/assertions.json")


def _baseline(tmp_path, fmt="constructed-jsonl-v1", name=None):
    return build_fixture(tmp_path / (name or fmt), fmt)


def test_schema_rejects_unknown_missing_enum_and_boolean_integer(tmp_path):
    bundle = _baseline(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)

    unknown = dict(manifest)
    unknown["unexpected"] = True
    _write_json(manifest_path, unknown)
    with pytest.raises(ValueError, match="unknown fields"):
        validate_bundle(bundle)

    missing = dict(manifest)
    missing.pop("capture")
    _write_json(manifest_path, missing)
    with pytest.raises(ValueError, match="missing required fields"):
        validate_bundle(bundle)

    illegal = dict(manifest)
    illegal["execution"] = dict(manifest["execution"], state="bogus")
    _write_json(manifest_path, illegal)
    with pytest.raises(ValueError, match="invalid value"):
        validate_bundle(bundle)

    boolean_integer = dict(manifest)
    boolean_integer["execution"] = dict(manifest["execution"], scenario_runs=True)
    _write_json(manifest_path, boolean_integer)
    with pytest.raises(ValueError, match=r"expected \['integer'\]"):
        validate_bundle(bundle)


def test_schema_rejects_nonfinite_json(tmp_path):
    path = tmp_path / "nonfinite.json"
    path.write_text('{"value": NaN}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="nonfinite JSON number"):
        read_json(path)


def test_duplicate_artifact_and_dangling_dependency_are_rejected(tmp_path):
    bundle = _baseline(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    manifest["artifacts"][1]["id"] = manifest["artifacts"][0]["id"]
    _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="duplicate id"):
        validate_bundle(bundle)

    bundle = _baseline(tmp_path, "constructed-sqlite-v1")
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    manifest["artifacts"][0]["depends_on"] = ["missing-artifact"]
    _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="dangling dependency"):
        validate_bundle(bundle)


def test_duplicate_assertion_and_dangling_observation_are_rejected(tmp_path):
    bundle = _baseline(tmp_path)

    def duplicate(expected):
        expected["assertions"].append(dict(expected["assertions"][0]))

    _mutate_expected(bundle, duplicate)
    with pytest.raises(ValueError, match="duplicate id"):
        validate_bundle(bundle)

    bundle = _baseline(tmp_path, "constructed-sqlite-v1")

    def dangling(expected):
        expected["assertions"][0]["observation_ids"] = ["missing-observation"]

    _mutate_expected(bundle, dangling)
    with pytest.raises(ValueError, match="dangling observation"):
        validate_bundle(bundle)


def test_digest_mismatch_path_escape_symlink_and_unexpected_file_rejected(tmp_path):
    bundle = _baseline(tmp_path)
    native = bundle / "native" / "session.jsonl"
    native.write_bytes(native.read_bytes() + b"tampered\n")
    with pytest.raises(ValueError, match="integrity mismatch"):
        validate_bundle(bundle)

    bundle = _baseline(tmp_path, "constructed-sqlite-v1")
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    manifest["expectations_path"] = "../outside.json"
    _write_json(manifest_path, manifest)
    with pytest.raises(ValueError, match="path escape|unknown fields|expected input"):
        validate_bundle(bundle)

    bundle = _baseline(tmp_path, "constructed-jsonl-v1", "symlink")
    os.symlink(bundle / "native" / "session.jsonl", bundle / "native" / "escape")
    with pytest.raises(ValueError, match="symlink"):
        validate_bundle(bundle)

    bundle = _baseline(tmp_path, "constructed-sqlite-v1", "extra")
    (bundle / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="inventory mismatch"):
        validate_bundle(bundle)


def test_registry_result_and_metric_denominator_validation(tmp_path):
    with pytest.raises(ValueError, match="measured surface"):
        validate_registry({
            "schema_version": "1.0-prototype",
            "surfaces": [{
                "id": "x",
                "subject": {
                    "harness": "h", "version": "1", "surface": "cli", "mode": "m",
                    "os": "o", "model": "m", "configuration": "default",
                    "artifact_family": "jsonl", "schema_version": "1",
                },
                "status": "measured", "maintenance_owner": "owner",
                "source_inspected_at": "2026-09-10", "live_tested_at": None,
                "next_inspection_due": "2026-10-10",
            }],
        })

    bundle = _baseline(tmp_path)
    result, _ = evaluate_bundle(bundle, decoder=decode_native)
    forged = json.loads(json.dumps(result))
    forged["metrics"][0]["denominator"] += 1
    with pytest.raises(ValueError, match="population/count/state mismatch"):
        validate_result(forged)

    forged = json.loads(json.dumps(result))
    forged["metrics"][0]["assertion_ids"].append(forged["metrics"][0]["assertion_ids"][0])
    with pytest.raises(ValueError, match="duplicate metric assertion IDs"):
        validate_result(forged)


def test_inspection_contradiction_is_a_failure(tmp_path):
    bundle = _baseline(tmp_path)

    def contradiction(expected):
        target = expected["assertions"][0]
        target["inspection"] = {
            "state": "absent",
            "locators": [],
            "evidence_ids": ["provenance-mutation.json"],
        }

    _mutate_expected(bundle, contradiction)
    validate_bundle(bundle)
    result, _ = evaluate_bundle(bundle, decoder=decode_native)
    row = next(row for row in result["rows"] if row["id"] == "event.session-1")
    assert row["state"] == "fail"
    assert "inspection_contradiction" in row["findings"]


def test_capture_invalid_is_distinct_from_captured_corruption(tmp_path):
    invalid = build_fixture(tmp_path / "invalid", mutation="missing_companion")
    with pytest.raises(ValueError, match="inventory|integrity"):
        validate_bundle(invalid)

    corrupt = build_fixture(tmp_path / "corrupt", mutation="captured_corruption")
    validate_bundle(corrupt)
    result, decoded = evaluate_bundle(corrupt, decoder=decode_native)
    assert result["capture_status"] == "valid"
    assert result["evidence_state"] == "valid"
    assert any(item["code"] in {"decode_error", "malformed_record"} for item in decoded["diagnostics"])


def test_bounded_codex_native_live_bundle_is_admitted(tmp_path):
    bundle = _baseline(tmp_path)
    decode_path = bundle / "native" / "decode.json"
    decode = _json(decode_path)
    decode["format"] = "codex-rollout-v1"
    _write_json(decode_path, decode)
    _rehash_artifact(bundle, "native/decode.json")
    manifest_path = bundle / "manifest.json"
    manifest = _json(manifest_path)
    manifest["origin"] = "native_live"
    plan_payload = _json(Path(__file__).resolve().parents[1] / "docs/live/codex-cli-f0-run-plan.json")
    manifest["subject"] = {
        "harness": "codex-cli", "version": plan_payload["subject"]["version"], "surface": "cli",
        "mode": "interactive_local", "os": plan_payload["subject"]["os"], "model": "observed",
        "configuration": "bounded", "artifact_family": "codex-rollout-jsonl",
        "schema_version": "observed",
    }
    manifest["decoder"]["format"] = "codex-rollout-v1"
    manifest["provenance"]["privacy_review"] = "local-f0-synthetic-scan"
    plan_sha = digest(canonical(plan_payload))
    stat = {"relative_path": "rollout-new.jsonl", "filesystem_id": "1:2", "birth_time": 1,
            "ctime": 1, "mtime": 1, "size": 10}
    capture_payload = {"attempt_id": "attempt-1", "scenario_run_id": "scenario-1",
                       "native_session_ids": ["session-1"], "resolved_config_fingerprint": "b" * 64,
                       "before_stats": [], "after_stats": [stat],
                       "primary_candidate_path": "rollout-new.jsonl", "companion_paths": [],
                       "candidate_paths": ["rollout-new.jsonl"], "opened_paths": ["rollout-new.jsonl"],
                       "preexisting_file_hashing": False, "ambiguous": False, "source_mutated": False,
                       "observer_frozen": True, "quiescence_checks": 2, "candidate_identity_verified": True}
    ledger_payload = {"schema_version": "1.0-live-ledger", "gate_id": "codex-cli-f0", "plan_sha256": plan_sha,
                      "attempts": [{"scenario_id": "C01", "attempt_id": "attempt-1",
                                    "scenario_run_id": "scenario-1", "state": "captured",
                                    "native_session_ids": ["session-1"], "submitted_turns": 1,
                                    "config_identity": "b" * 64,
                                    "usage": {"captured_files": 2, "captured_bytes": 100,
                                              "observable_tokens": 0, "spend_usd": 0,
                                              "operator_minutes": 1, "wall_clock_minutes": 1},
                                    "events": ["captured"], "reason": "test evidence"}]}
    provenance = bundle / "provenance"
    files = {
        "plan.json": {"plan": plan_payload, "plan_sha256": plan_sha},
        "capture.json": capture_payload,
        "ledger.json": ledger_payload,
    }
    import hashlib
    for name, value in files.items():
        _write_json(provenance / name, value)
        data = (provenance / name).read_bytes()
        manifest["artifacts"].append({"id": "provenance-" + name[:-5], "role": "provenance",
                                      "path": "provenance/" + name, "sha256": hashlib.sha256(data).hexdigest(),
                                      "size_bytes": len(data), "depends_on": []})
    capture_sha = next(item["sha256"] for item in manifest["artifacts"] if item["id"] == "provenance-capture")
    ledger_sha = next(item["sha256"] for item in manifest["artifacts"] if item["id"] == "provenance-ledger")
    manifest["live_binding"] = {
        "plan_sha256": plan_sha, "resolved_config_fingerprint": capture_payload["resolved_config_fingerprint"],
        "scenario_run_id": "scenario-1", "attempt_id": "attempt-1", "capture_id": manifest["capture_id"],
        "native_session_id": "session-1",
        "capture_evidence_sha256": capture_sha, "ledger_sha256": ledger_sha,
        "plan_artifact_id": "provenance-plan", "capture_evidence_artifact_id": "provenance-capture",
        "ledger_artifact_id": "provenance-ledger",
    }
    _write_json(manifest_path, manifest)
    validate_bundle(bundle)

    broken = _json(manifest_path)
    broken["live_binding"]["plan_sha256"] = "0" * 64
    _write_json(manifest_path, broken)
    with pytest.raises(ValueError, match="plan digest"):
        validate_bundle(bundle)

    broken = json.loads(json.dumps(manifest))
    broken["live_binding"]["capture_id"] = "substituted-capture"
    _write_json(manifest_path, broken)
    with pytest.raises(ValueError, match="capture identity"):
        validate_bundle(bundle)

    _write_json(manifest_path, manifest)
    observer_path = bundle / "observer/events.json"
    observer = _json(observer_path)
    for event in observer["events"]:
        event["session_id"] = "different-native-session"
    _write_json(observer_path, observer)
    _rehash_artifact(bundle, "observer/events.json")
    with pytest.raises(ValueError, match="bound native session"):
        validate_bundle(bundle)
