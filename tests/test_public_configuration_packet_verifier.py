"""Tests for the standalone public configuration packet verifier."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
PACKET = REPOSITORY / "artifacts/opencode-v1-public-configuration"
VERIFIER = REPOSITORY / "scripts/verify_public_configuration_packet.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("public_configuration_packet_verifier", VERIFIER)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load standalone verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_packet_accepts_source_denial_counts_for_each_native_layout() -> None:
    """The producer emits 3 paths for one root and 9 for three roots."""
    verifier = _load_verifier()
    observed = [
        json.loads(
            (PACKET / "runs" / str(repetition) / "semantic/portability-receipt.json").read_text()
        )["source_native_files_denied"]
        for repetition in (1, 2, 3)
    ]
    assert observed == [3, 9, 9]

    receipt = verifier.verify_packet(PACKET, model_id="test-model")
    assert receipt["independent_reproduction"] is True
    assert receipt["native_decode_recomputed"] is False
    assert [
        row["status"] for row in receipt["recomputed"]["selected_loss_public_semantics"]
    ] == ["partially_recomputed"] * 3


def test_manifest_tamper_is_rejected_before_score_reproduction(tmp_path: Path) -> None:
    verifier = _load_verifier()
    copied_packet = tmp_path / "packet"
    shutil.copytree(PACKET, copied_packet)
    score_input = copied_packet / "runs/2/score-input/public-score.json"
    document = json.loads(score_input.read_text())
    document["overall"] = "0.0"
    score_input.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")

    with pytest.raises(verifier.VerificationError, match="manifest|content_sha256|artifact"):
        verifier.verify_packet(copied_packet)


def test_cli_runs_from_fresh_isolated_copy_with_denied_original(tmp_path: Path) -> None:
    copied_packet = tmp_path / "packet"
    shutil.copytree(PACKET, copied_packet)
    copied_verifier = tmp_path / "verify_public_configuration_packet.py"
    shutil.copy2(VERIFIER, copied_verifier)
    isolated_home = tmp_path / "home"
    isolated_tmp = tmp_path / "tmp"
    isolated_home.mkdir()
    isolated_tmp.mkdir()
    denied_original = tmp_path / "original-public-packet"
    denied_original.write_text("withheld public packet\n")
    denied_original.chmod(stat.S_IRUSR | stat.S_IWUSR)
    denied_original.chmod(0)

    environment = {
        "HOME": str(isolated_home),
        "TMPDIR": str(isolated_tmp),
        "PYTHONPATH": "",
        "PATH": "/usr/bin:/bin",
    }
    receipt_path = tmp_path / "independent-reproduction-receipt.json"
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        str(copied_verifier),
        "--packet",
        str(copied_packet),
        "--receipt",
        str(receipt_path),
        "--source-deny",
        str(denied_original),
        "--fresh-copy",
        "--model-id",
        "test-isolated-model",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        denied_original.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert completed.returncode == 0, completed.stderr
    emitted = json.loads(completed.stdout)
    receipt = json.loads(receipt_path.read_text())
    assert emitted == receipt
    assert receipt["reproducer_model_id"] == "test-isolated-model"
    assert receipt["isolation"] == {
        "fresh_temp_copy": True,
        "home_isolated": True,
        "native_decode_recomputed": False,
        "network_used": False,
        "original_public_packet": "permission_denied",
        "pythonpath_isolated": True,
        "tmp_isolated": True,
        "vendor_executable_accessed": False,
    }
    assert receipt["recomputed"]["configuration_aggregation"]["overall_exact"] == "333/4"
    assert [row["overall_exact"] for row in receipt["recomputed"]["run_scores"]] == [
        "333/4",
        "333/4",
        "333/4",
    ]
    assert receipt["recomputed"]["manifest_integrity"]["verified"] is True
    assert receipt["recomputed"]["result_id_binding"]["verified"] is True
    assert receipt["recomputed"]["privacy_scan"] == {
        "file_count": 45,
        "findings": [],
        "verified": True,
    }
    assert all(
        item["native_decode_recomputed"] is False
        for item in receipt["recomputed"]["selected_loss_public_semantics"]
    )
    assert not (copied_packet / "independent-reproduction-receipt.json").exists()


def test_source_denial_uses_lexical_path_before_the_permission_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denied path must not be resolved or stat'ed before it is opened."""
    verifier = _load_verifier()
    denied_original = tmp_path / "denied-original"
    denied_original.write_text("withheld\n")
    denied_original.chmod(0)
    denied_absolute = os.path.abspath(os.fspath(denied_original))
    original_resolve = verifier.Path.resolve

    def reject_denied_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if os.path.abspath(os.fspath(path)) == denied_absolute:
            raise AssertionError("the denied source path was resolved before probing")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(verifier.Path, "resolve", reject_denied_resolve)
    try:
        receipt = verifier.verify_packet(PACKET, source_deny=denied_original)
    finally:
        denied_original.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert receipt["isolation"]["original_public_packet"] == "permission_denied"


def test_unreported_model_sentinel_cannot_form_rankable_identity() -> None:
    verifier = _load_verifier()
    bundle = json.loads((PACKET / "configuration-bundle.json").read_text())
    run = bundle["runs"][0]
    run["identity"]["model"] = "model-unreported"

    with pytest.raises(verifier.VerificationError, match="unavailable sentinel"):
        verifier._validate_bundle_run(run, 0)
