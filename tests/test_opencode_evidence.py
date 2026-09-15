"""Focused controls for the OpenCode evidence-package boundary."""

from __future__ import annotations

import hashlib
import json

import pytest

from scripts.build_live_survival_result import _canonical, _verify_package
from session_bench.opencode_evidence import _package_file_entries, _records_for_metric


def _write(path, value) -> None:
    path.write_bytes(_canonical(value) + b"\n")


def _closed_package(root) -> None:
    payload = root / "payload.json"
    payload.write_text('{"ok":true}\n', encoding="utf-8")
    core = {
        "schema_version": "session-bench-evidence-package-v1",
        "package_id": "package-test",
        "result_id": "survival-result-test",
        "attempt_id": "test",
        "raw_run_id": "test",
        "configuration_id": "opencode-cli",
        "repetition": 1,
        "scope": "test",
        "self_manifest": {
            "path": "package-manifest.json",
            "hash_excluded_reason": "self reference",
        },
        "result_material_sha256": "a" * 64,
        "files": _package_file_entries(root),
    }
    package = {**core, "package_digest": hashlib.sha256(_canonical(core)).hexdigest()}
    _write(root / "package-manifest.json", package)


def test_closed_package_rejects_tampering_and_extra_files(tmp_path) -> None:
    _closed_package(tmp_path)
    assert _verify_package(tmp_path)["package_id"] == "package-test"

    (tmp_path / "payload.json").write_text('{"ok":false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="package artifact mismatch"):
        _verify_package(tmp_path)

    (tmp_path / "payload.json").write_text('{"ok":true}\n', encoding="utf-8")
    (tmp_path / "undeclared.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(ValueError, match="package file boundary mismatch"):
        _verify_package(tmp_path)


def test_metric_locators_use_only_the_frozen_action_population() -> None:
    observer = {
        "events": [
            {
                "id": "action-1",
                "kind": "action",
                "population_role": "primary_scored",
                "fields": {"native_action_id": "native-a1", "call_id": "call-1"},
            },
            {
                "id": "result-1",
                "kind": "result",
                "population_role": "primary_scored",
                "fields": {"native_result_id": "native-r1", "call_id": "call-1"},
            },
        ]
    }
    decoded = {
        "actions": [
            {"id": "native-a1", "call_id": "call-1"},
            {"id": "discovery-a2", "call_id": "discovery-call"},
        ],
        "results": [
            {"id": "native-r1", "call_id": "call-1"},
            {"id": "discovery-r2", "call_id": "discovery-call"},
        ],
        "file_changes": [
            {"path": "fixture_project/checkout.py", "oldString": "a", "newString": "b"}
        ],
        "relations": [
            {"kind": "action_result", "from_id": "native-a1", "to_id": "native-r1"},
            {"kind": "action_result", "from_id": "discovery-a2", "to_id": "discovery-r2"},
        ],
    }

    assert [row["id"] for row in _records_for_metric(decoded, observer, "work.actions")] == ["native-a1"]
    assert [row["id"] for row in _records_for_metric(decoded, observer, "work.results")] == ["native-r1"]
    assert len(_records_for_metric(decoded, observer, "causal.action_result")) == 1
    assert _records_for_metric(decoded, observer, "work.changed_files") == []
