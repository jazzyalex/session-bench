from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

from session_bench.native_replay import canonical
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("retained_score_inputs", ROOT / "scripts/build_retained_score_replays.py")
inputs_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs_module)


def _packet(tmp_path, configuration="claude-cli"):
    suffix = "-correction-1" if configuration == "claude-desktop" else ""
    source, inputs = inputs_module.retained_inputs(ROOT / f"artifacts/survival-v1-runs/{configuration}-eval-1{suffix}")
    copied = tmp_path / "native-input"
    shutil.copytree(source, copied)
    output = tmp_path / "closed"
    manifest = build_score_replay_package(copied, output, **inputs)
    digest = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    return copied, output, manifest, digest


@pytest.mark.parametrize("configuration", ["codex-cli", "claude-cli", "codex-desktop", "claude-desktop"])
def test_closed_native_to_score_replay_and_loss_preserves_independent_denominator(tmp_path, configuration):
    source, output, manifest, digest = _packet(tmp_path, configuration)
    source.rename(tmp_path / "unavailable")
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    receipt = replay_score_package(output, expected_manifest_sha256=digest)
    assert receipt["diagnostics_sha256"] == manifest["expected_diagnostics_sha256"]
    assert receipt["python_isolated"] and not receipt["os_sandboxed"]
    assert not receipt["independent_reproduction"] and not receipt["public_safe"]
    intact = receipt["diagnostics"]["intact"]
    loss = receipt["diagnostics"]["selected_loss"]
    assert len(intact["metrics"]) == 31
    assert len({row["id"] for row in intact["metrics"]}) == 31
    assert loss["observer_denominator_unchanged"] and loss["response_correctness_reduced"]
    assert intact["score_diagnostics"]["overall"] is None
    assert intact["score_diagnostics"]["rankable"] is False
    assert before == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    controls = verify_score_packet_tamper_controls(output, expected_manifest_sha256=digest)
    assert controls["status"] == "passed" and not controls["code_executed"]
    assert len(controls["controls"]) == 6
    metrics = {row["id"]: row for row in intact["metrics"]}
    if configuration.startswith("codex-"):
        assert metrics["work.visible_responses"]["observed_eligible"] == 2
        assert metrics["work.actions"]["state"] == "unresolved"
        assert metrics["work.actions"]["observed_eligible"] == 0
        if configuration == "codex-desktop":
            assert metrics["broad.readable_rationale"]["state"] == "unresolved"
            assert metrics["broad.classified_content_density"]["state"] == "unresolved"
    elif configuration == "claude-cli":
        # The transcript's own edit result holds the file pre-image, so both
        # whole-file hashes come from native bytes.
        assert metrics["work.changed_files"]["state"] == "measured"
        assert metrics["work.changed_files"]["correct"] == 1
    else:
        # Independent filesystem values must never be projected into native hashes.
        # The Desktop transcript holds the pre-image (inspect result) and the
        # edit (heredoc write confirmed by its own native diff), so both
        # whole-file hashes come from native bytes.
        assert metrics["work.changed_files"]["state"] == "measured"
        assert metrics["work.changed_files"]["correct"] == 1
        # The opt-in is now a closed, hash-bound transcript/metadata pair.
        assert metrics["broad.classified_content_density"]["state"] == "measured"
        # Two compound shell calls are the native record of four actions and
        # three helper results. The observer's placeholder edit result has no
        # native record and stays unmatched.
        counts = {key: (metrics[key]["correct"], metrics[key]["observed_eligible"])
                  for key in ("work.actions", "work.results", "causal.action_result", "broad.readable_rationale", "broad.event_timestamps")}
        assert counts == {"work.actions": (4, 4), "work.results": (3, 4), "causal.action_result": (3, 4),
                          "broad.readable_rationale": (2, 2), "broad.event_timestamps": (11, 13)}
        assert b"compound edit completed" not in (output / "native/session.jsonl").read_bytes()


@pytest.mark.parametrize("target", ["inputs/workload.json", "inputs/observer.json", "native/session.jsonl", "runtime/session_bench/v1_public_score.py", "runtime/scripts/replay_score_package.py"])
def test_tampered_inputs_and_scorer_rejected_before_execution(tmp_path, target, monkeypatch):
    _, output, _, digest = _packet(tmp_path)
    (output / target).write_bytes(b"raise RuntimeError('untrusted code executed')")
    monkeypatch.setattr("session_bench.score_replay.subprocess.run", lambda *a, **k: pytest.fail("unverified packet code executed"))
    with pytest.raises(ValueError, match="inventory mismatch"):
        replay_score_package(output, expected_manifest_sha256=digest)


def test_manifest_pin_detects_self_consistent_score_forgery(tmp_path):
    _, output, _, digest = _packet(tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["expected_diagnostics_sha256"] = "a" * 64
    manifest_path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match="trusted expected digest"):
        replay_score_package(output, expected_manifest_sha256=digest)
    forged_pin = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="diagnostics differ"):
        replay_score_package(output, expected_manifest_sha256=forged_pin)


@pytest.mark.parametrize("mode", ["extra", "missing", "symlink", "overclaim"])
def test_closed_population_and_scope_guards(tmp_path, mode):
    _, output, _, digest = _packet(tmp_path)
    if mode == "extra":
        (output / "unexpected.txt").write_text("extra")
    elif mode == "missing":
        (output / "inputs/workload.json").unlink()
    elif mode == "symlink":
        path = output / "inputs/workload.json"
        data = path.read_bytes()
        path.unlink()
        outside = tmp_path / "workload.json"
        outside.write_bytes(data)
        path.symlink_to(outside)
    else:
        path = output / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["independent_reproduction"] = True
        path.write_bytes(canonical(manifest))
    with pytest.raises(ValueError):
        replay_score_package(output, expected_manifest_sha256=digest)


def test_no_promoting_retained_codex_complete_root_assertion(tmp_path):
    source, inputs = inputs_module.retained_inputs(ROOT / "artifacts/survival-v1-runs/codex-cli-eval-1")
    context = json.loads(inputs["context_document"])
    context["complete_root"] = True
    inputs["context_document"] = canonical(context)
    with pytest.raises(ValueError, match="promotes an unproven"):
        build_score_replay_package(source, tmp_path / "closed", **inputs)
    assert not (tmp_path / "closed").exists()


def test_missing_desktop_companion_cannot_keep_complete_family_claim(tmp_path):
    source, inputs = inputs_module.retained_inputs(ROOT / "artifacts/survival-v1-runs/claude-desktop-eval-1-correction-1")
    del inputs["supporting_documents"]["native-family/desktop/session.json"]
    with pytest.raises(ValueError, match="omits an asserted native companion"):
        build_score_replay_package(source, tmp_path / "closed", **inputs)


def _desktop_per_run_inputs():
    source, inputs = inputs_module.retained_inputs(
        ROOT / "artifacts/survival-v1-runs/claude-desktop-eval-1-correction-1"
    )
    inputs["supporting_documents"] = dict(inputs["supporting_documents"])
    context = json.loads(inputs["context_document"])
    context["root_repetitions"] = [{
        "repetition": context["repetition"],
        "root_locator": ("Claude Code normal session roots: projects/<project-key>/<session-id>.jsonl + "
                         "Desktop session metadata/<session-group>/<local-session-id>.json"),
        "discovery_mode": "metadata_safe_normal_root",
        "personal_history_scanned": False,
    }]
    inputs["context_document"] = canonical(context)
    assertion = json.loads(inputs["supporting_documents"]["capture-assertion.json"])
    assertion["schema_version"] = "session-bench-claude-desktop-native-manifest-v2"
    assertion["cross_run_root_repeatability_qualified"] = False
    roots = {"transcript": ("claude-projects", "/synthetic/.claude/projects"),
             "desktop_metadata": ("claude-desktop-sessions", "/synthetic/Claude/claude-code-sessions")}
    selected = []
    summaries = []
    for role, relative, name in (
        ("transcript", "project/" + assertion["cli_session_id"] + ".jsonl", "transcript/session.jsonl"),
        ("desktop_metadata", "account/" + assertion["desktop_session_id"] + ".json", "desktop/session.json"),
    ):
        root_id, root = roots[role]
        data = inputs["supporting_documents"]["native-family/" + name]
        root_digest = hashlib.sha256(root.encode()).hexdigest()
        fs_digest = hashlib.sha256((root_id + "-fs").encode()).hexdigest()
        selected.append({"role": role, "source_path": root + "/" + relative,
                         "relative_path": relative, "source_root_sha256": root_digest,
                         "filesystem_id_sha256": fs_digest, "device": 1, "inode": len(selected) + 1,
                         "size_bytes": len(data), "ctime_ns": 1, "mtime_ns": 1,
                         "sha256": hashlib.sha256(data).hexdigest()})
        summaries.append({"root_id": root_id, "source_root_sha256": root_digest,
                          "filesystem_id_sha256": fs_digest, "before_entry_count": 0,
                          "after_entry_count": 1, "unrelated_entry_count": 0,
                          "unrelated_inventory_sha256": "0" * 64})
    receipt = {"schema_version": "session-bench-claude-desktop-source-discovery-v1",
               "run_id": context["run_id"], "repetition": context["repetition"],
               "cli_session_id": assertion["cli_session_id"],
               "desktop_session_id": assertion["desktop_session_id"],
               "metadata_only_discovery": True, "personal_history_content_read": False,
               "unrelated_content_read": False, "complete_inventories": True,
               "isolated_pair": True, "selected_artifacts": selected, "root_summaries": summaries}
    receipt["proof_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    raw = canonical(receipt)
    assertion["source_discovery_receipt"] = {
        "path": "source-discovery-private.json", "sha256": hashlib.sha256(raw).hexdigest(),
    }
    inputs["supporting_documents"]["source-discovery-private.json"] = raw
    inputs["supporting_documents"]["capture-assertion.json"] = canonical(assertion)
    return source, inputs


def test_claude_desktop_per_run_discovery_scores_one_root_row(tmp_path):
    source, inputs = _desktop_per_run_inputs()
    packet = tmp_path / "closed"
    manifest = build_score_replay_package(source, packet, **inputs)
    digest = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
    result = replay_score_package(packet, expected_manifest_sha256=digest)
    assert result["diagnostics_sha256"] == manifest["expected_diagnostics_sha256"]
    root = result["diagnostics"]["intact"]["format_evidence"]["profile"]["broad_evidence"]["broad.stable_root_location"]
    assert root == {"evidence_complete": True, "repetitions": json.loads(inputs["context_document"])["root_repetitions"]}
    assert result["diagnostics"]["intact"]["independent_reproduction"] is False


@pytest.mark.parametrize("change", ["digest", "identity", "source_bytes", "extra_root_row", "repeatability"])
def test_claude_desktop_per_run_discovery_rejects_mismatch(tmp_path, change):
    source, inputs = _desktop_per_run_inputs()
    support = inputs["supporting_documents"]
    assertion = json.loads(support["capture-assertion.json"])
    receipt = json.loads(support["source-discovery-private.json"])
    context = json.loads(inputs["context_document"])
    if change == "digest":
        assertion["source_discovery_receipt"]["sha256"] = "0" * 64
    elif change == "identity":
        receipt["run_id"] = "other-run"
    elif change == "source_bytes":
        receipt["selected_artifacts"][0]["sha256"] = "0" * 64
    elif change == "extra_root_row":
        context["root_repetitions"].append({**context["root_repetitions"][0], "repetition": 2})
    else:
        assertion["cross_run_root_repeatability_qualified"] = True
    if change in {"identity", "source_bytes"}:
        receipt["proof_sha256"] = hashlib.sha256(canonical({key: value for key, value in receipt.items() if key != "proof_sha256"})).hexdigest()
        raw = canonical(receipt)
        support["source-discovery-private.json"] = raw
        assertion["source_discovery_receipt"]["sha256"] = hashlib.sha256(raw).hexdigest()
    support["capture-assertion.json"] = canonical(assertion)
    inputs["context_document"] = canonical(context)
    with pytest.raises(ValueError, match="Claude Desktop"):
        build_score_replay_package(source, tmp_path / "closed", **inputs)


def _timestamp_observer_bytes(primary_bytes):
    observer = json.loads(primary_bytes)
    for index, event in enumerate(observer["events"]):
        if event["kind"] in {"action", "result", "file_change"}:
            event["fields"]["call_id"] = f"independent-tool-{index}"
    return canonical(observer)


def test_claude_desktop_packet_binds_separate_timestamp_observer(tmp_path):
    source, inputs = inputs_module.retained_inputs(
        ROOT / "artifacts/survival-v1-runs/claude-desktop-eval-1-correction-1"
    )
    plain_inputs = {**inputs, "supporting_documents": dict(inputs["supporting_documents"])}
    plain_output = tmp_path / "plain"
    build_score_replay_package(source, plain_output, **plain_inputs)
    plain_digest = hashlib.sha256((plain_output / "manifest.json").read_bytes()).hexdigest()
    plain = replay_score_package(plain_output, expected_manifest_sha256=plain_digest)["diagnostics"]["intact"]
    timestamp_bytes = _timestamp_observer_bytes(inputs["observer_document"])
    inputs["supporting_documents"]["timestamp-observer.json"] = timestamp_bytes
    output = tmp_path / "closed"
    build_score_replay_package(source, output, **inputs)
    digest = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    receipt = replay_score_package(output, expected_manifest_sha256=digest)
    intact = receipt["diagnostics"]["intact"]
    assert intact["measurement"] == plain["measurement"]
    assert intact["observer_sha256"] == hashlib.sha256(inputs["observer_document"]).hexdigest()
    assert intact["format_evidence"]["observer"]["sha256"] == intact["observer_sha256"]
    bindings = {row["metric_id"]: row["observer_ids"] for row in intact["format_evidence"]["metric_evidence"]}
    assert bindings["broad.event_timestamps"] == ["inputs/timestamp-observer.json"]
    assert all(ids == ["closed-replay-observer"] for metric, ids in bindings.items()
               if metric != "broad.event_timestamps")
    controls = verify_score_packet_tamper_controls(output, expected_manifest_sha256=digest)
    assert any(row["target"] == "inputs/timestamp-observer.json" for row in controls["controls"])


def test_claude_desktop_packet_rejects_timestamp_observer_content_tamper(tmp_path):
    source, inputs = inputs_module.retained_inputs(
        ROOT / "artifacts/survival-v1-runs/claude-desktop-eval-1-correction-1"
    )
    timestamp = json.loads(_timestamp_observer_bytes(inputs["observer_document"]))
    timestamp["events"][0]["fields"]["text"] += " altered"
    inputs["supporting_documents"]["timestamp-observer.json"] = canonical(timestamp)
    with pytest.raises(ValueError, match="non-identity field"):
        build_score_replay_package(source, tmp_path / "closed", **inputs)


def test_non_desktop_packet_rejects_timestamp_observer_input(tmp_path):
    source, inputs = inputs_module.retained_inputs(ROOT / "artifacts/survival-v1-runs/claude-cli-eval-1")
    inputs["supporting_documents"]["timestamp-observer.json"] = inputs["observer_document"]
    with pytest.raises(ValueError, match="only for claude-desktop"):
        build_score_replay_package(source, tmp_path / "closed", **inputs)


def test_original_native_assertion_cannot_qualify_different_manifest(tmp_path):
    source, inputs = inputs_module.retained_inputs(ROOT / "artifacts/survival-v1-runs/codex-cli-eval-1")
    copied = tmp_path / "native"
    shutil.copytree(source, copied)
    path = copied / "decode.json"
    # Semantically equal JSON with different exact bytes is a different captured input.
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="does not bind native inventory"):
        build_score_replay_package(copied, tmp_path / "closed", **inputs)


@pytest.mark.skipif(sys.platform != "darwin", reason="actual OS backend is macOS only")
def test_os_sandbox_executes_real_denied_probes_and_reproduces_packet(tmp_path):
    _, output, manifest, digest = _packet(tmp_path)
    result = replay_score_package(output, expected_manifest_sha256=digest, os_sandboxed=True)
    assert result["os_sandboxed"] is True
    assert result["os_isolation"]["probes"] == {"outside_read": True, "outside_write": True, "network_connect": True}
    assert result["diagnostics_sha256"] == manifest["expected_diagnostics_sha256"]


def test_unsupported_os_sandbox_has_no_python_only_fallback(tmp_path, monkeypatch):
    _, output, _, digest = _packet(tmp_path)
    monkeypatch.setattr("session_bench.score_replay.sys.platform", "linux")
    monkeypatch.setattr("session_bench.score_replay.subprocess.run", lambda *a, **k: pytest.fail("unsandboxed fallback executed"))
    with pytest.raises(ValueError, match="no unsandboxed fallback"):
        replay_score_package(output, expected_manifest_sha256=digest, os_sandboxed=True)


def test_pinned_library_hash_is_checked_before_loading(monkeypatch):
    from session_bench.score_replay import _zstd_dependency
    monkeypatch.setattr("session_bench.score_replay.ctypes.util.find_library", lambda name: "/installed/libzstd.dylib")
    monkeypatch.setattr("session_bench.score_replay._read_runtime_source", lambda path: b"altered installed native library")
    monkeypatch.setattr("session_bench.score_replay.ctypes.CDLL", lambda *a, **k: pytest.fail("unverified native library loaded"))
    with pytest.raises(ValueError, match="before loading"):
        _zstd_dependency({"sha256": "0" * 64})
