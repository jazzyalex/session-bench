import json
import sqlite3
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from session_bench.bundle import validate_bundle
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle
from session_bench.fixtures import build_fixture


def _native_rows(bundle: Path, fmt: str):
    if fmt == "constructed-jsonl-v1":
        return [json.loads(line) for line in (bundle / "native/session.jsonl").read_text(encoding="utf-8").splitlines() if line]
    con = sqlite3.connect(bundle / "native/session.sqlite")
    rows = [json.loads(payload) for (payload,) in con.execute("SELECT payload FROM events ORDER BY row_key")]
    con.close()
    return rows


@pytest.mark.parametrize("fmt", ["constructed-jsonl-v1", "constructed-sqlite-v1"])
def test_builds_equivalent_constructed_bundle(tmp_path, fmt):
    bundle = build_fixture(tmp_path / fmt, fmt)
    assert json.loads((bundle / "manifest.json").read_text())["origin"] == "constructed"
    decode = json.loads((bundle / "native/decode.json").read_text())
    assert decode["format"] == fmt
    assert all(set(item) == {"id", "path", "sha256", "size_bytes", "depends_on"} for item in decode["artifacts"])
    rows = _native_rows(bundle, fmt)
    assert [row["id"] for row in rows].count("session-1") == 1
    assert any(row["fields"].get("text", "").endswith("🙂") for row in rows)
    assert any("  \n```" in row["fields"].get("text", "") for row in rows)
    assert json.loads((bundle / "expected/assertions.json").read_text())["schema_version"] == "1.0-prototype"


@pytest.mark.parametrize("mutation", ["remove_fact", "wrong_status", "duplicate", "missing_join", "unknown_event", "empty_native", "branch_dangling", "branch_cycle"])
def test_mutations_are_recorded_and_deterministic(tmp_path, mutation):
    bundle = build_fixture(tmp_path / mutation, mutation=mutation)
    detail = json.loads((bundle / "provenance/mutation.json").read_text())
    assert detail["name"] == mutation
    assert detail["baseline_sha256"]
    assert detail["transformation"] != "none"
    assert (bundle / "native/decode.json").exists()


@pytest.mark.parametrize("mutation", ["malformed_tail", "captured_corruption", "missing_companion"])
def test_damage_controls_preserve_explicit_artifact_state(tmp_path, mutation):
    bundle = build_fixture(tmp_path / mutation, mutation=mutation)
    decode = json.loads((bundle / "native/decode.json").read_text())
    attachment = bundle / "native/attachments/target.txt"
    assert decode["artifacts"][0]["sha256"]
    if mutation == "missing_companion":
        assert not attachment.exists()
        assert decode["artifacts"][1]["sha256"]
        assert decode["artifacts"][1]["size_bytes"] == len(b"value = 2\n")
    else:
        assert json.loads((bundle / "provenance/mutation.json").read_text())["transformation"] != "none"


def test_rejects_unknown_format_and_mutation(tmp_path):
    with pytest.raises(ValueError):
        build_fixture(tmp_path / "x", "vendor-format")
    with pytest.raises(ValueError):
        build_fixture(tmp_path / "x", mutation="not-a-mutation")


@pytest.mark.parametrize("fmt", ["constructed-jsonl-v1", "constructed-sqlite-v1"])
def test_valid_bundle_and_evaluator_baseline(tmp_path, fmt):
    bundle = build_fixture(tmp_path / fmt, fmt)
    validate_bundle(bundle)
    result, _ = evaluate_bundle(bundle, decoder=decode_native)
    assert all(metric["state"] == "pass" for metric in result["metrics"])


@pytest.mark.parametrize("fmt", ["constructed-jsonl-v1", "constructed-sqlite-v1"])
def test_fixture_project_reproduces_fail_edit_pass_slice(tmp_path, fmt):
    bundle = build_fixture(tmp_path / fmt, fmt)
    project = bundle / "workload/fixture_project"
    command = [sys.executable, "-I", "-S", "-B", "test_target.py"]
    shutil.rmtree(project / "__pycache__", ignore_errors=True)
    failed = subprocess.run(command, cwd=project, capture_output=True, text=True)
    assert failed.returncode != 0
    shutil.rmtree(project / "__pycache__", ignore_errors=True)
    shutil.copy2(project / "snapshots/target.after.py", project / "target.py")
    passed = subprocess.run(command, cwd=project, capture_output=True, text=True)
    assert passed.returncode == 0, passed.stderr
    rows = _native_rows(bundle, fmt)
    edit_call = next(row for row in rows if row["id"] == "tc-3")
    edit_result = next(row for row in rows if row["id"] == "tr-3")
    assert edit_call["fields"]["action_kind"] == "edit"
    assert edit_call["fields"]["project_relative_target"] == "fixture_project/target.py"
    assert edit_result["fields"]["before_file_sha256"] != edit_result["fields"]["after_file_sha256"]
    assert edit_result["fields"]["outcome"] == "edited"
    assert next(row for row in rows if row["id"] == "tr-2")["fields"]["status"] == "failure"
    assert next(row for row in rows if row["id"] == "tr-4")["fields"]["status"] == "success"


@pytest.mark.parametrize("fmt", ["constructed-jsonl-v1", "constructed-sqlite-v1"])
@pytest.mark.parametrize("mutation", ["remove_fact", "wrong_status", "duplicate", "missing_join", "empty_native", "branch_dangling", "branch_cycle"])
def test_mutations_produce_failure_or_unresolved(tmp_path, fmt, mutation):
    bundle = build_fixture(tmp_path / f"{fmt}-{mutation}", fmt, mutation)
    validate_bundle(bundle)
    result, _ = evaluate_bundle(bundle, decoder=decode_native)
    assert any(metric["state"] in {"fail", "unresolved"} for metric in result["metrics"])


@pytest.mark.parametrize("fmt", ["constructed-jsonl-v1", "constructed-sqlite-v1"])
@pytest.mark.parametrize("mutation", ["malformed_tail", "unknown_event", "captured_corruption"])
def test_diagnostic_mutations_are_not_silent(tmp_path, fmt, mutation):
    bundle = build_fixture(tmp_path / f"{fmt}-{mutation}", fmt, mutation)
    if mutation == "captured_corruption":
        result = decode_native(bundle / "native")
    else:
        validate_bundle(bundle)
        result, _ = evaluate_bundle(bundle, decoder=decode_native)
    codes = {item["code"] for item in result["diagnostics"]}
    assert codes & {"malformed_record", "unknown_record", "unsupported_schema", "decode_error"}


def test_missing_companion_is_declared_invalid_but_native_decoder_diagnoses(tmp_path):
    bundle = build_fixture(tmp_path / "missing", mutation="missing_companion")
    with pytest.raises(ValueError, match=r"inventory mismatch|integrity mismatch"):
        validate_bundle(bundle)
    decoded = decode_native(bundle / "native")
    assert any(item["code"] in {"missing_artifact", "missing_dependency"} for item in decoded["diagnostics"])
