from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.native_replay import build_native_replay_package, replay_native_package


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/codex-cli-native-0154"


@pytest.mark.parametrize("configuration", ["codex-cli", "codex-desktop"])
def test_closed_runtime_replays_after_source_disappears(tmp_path, configuration):
    source = tmp_path / "input"
    shutil.copytree(FIXTURE, source)
    output = tmp_path / "closed"
    manifest = build_native_replay_package(source, output, configuration_id=configuration, repetition=1)
    source.rename(tmp_path / "unavailable")
    before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    receipt = replay_native_package(output)
    assert receipt["decode_sha256"] == manifest["expected_decode_sha256"]
    assert receipt["python_isolated"]
    assert not receipt["independent_reproduction"]
    assert not receipt["os_sandboxed"]
    assert not receipt["public_safe"]
    assert before == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_claude_closed_runtime(tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    data = (json.dumps({"type": "user", "uuid": "turn-1", "sessionId": "synthetic", "timestamp": "2026-09-29T00:00:00Z", "message": {"role": "user", "content": "Synthetic input"}}) + "\n").encode()
    (source / "session.jsonl").write_bytes(data)
    (source / "decode.json").write_text(json.dumps({"format": "claude-code-jsonl-v1", "artifacts": [{"id": "session", "path": "session.jsonl", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []}]}))
    output = tmp_path / "closed"
    manifest = build_native_replay_package(source, output, configuration_id="claude-cli", repetition=2)
    assert replay_native_package(output)["decode_sha256"] == manifest["expected_decode_sha256"]


@pytest.mark.parametrize("mutation", ["native", "runtime", "extra", "missing", "manifest_traversal"])
def test_replay_rejects_tampering(tmp_path, mutation):
    output = tmp_path / "closed"
    build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    if mutation == "native":
        with (output / "native/rollout.jsonl").open("ab") as handle:
            handle.write(b"\n")
    elif mutation == "runtime":
        (output / "runtime/session_bench/survival_metrics.py").write_text("raise RuntimeError('unverified code executed')")
    elif mutation == "extra":
        (output / "extra.txt").write_text("undeclared")
    elif mutation == "missing":
        (output / "native/rollout.jsonl").unlink()
    else:
        manifest = json.loads((output / "manifest.json").read_text())
        manifest["files"][0]["path"] = "../escaped"
        (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inventory mismatch|inventory is not closed|unsafe.*inventory") as error:
        replay_native_package(output)
    assert "unverified code executed" not in str(error.value)


def test_corrupted_runner_is_rejected_before_any_subprocess_executes(tmp_path, monkeypatch):
    output = tmp_path / "closed"
    build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    (output / "runtime/scripts/replay_native_package.py").write_text("raise RuntimeError('runner executed')")
    def forbidden_launch(*args, **kwargs):
        pytest.fail("corrupted package launched before inventory validation")
    monkeypatch.setattr("session_bench.native_replay.subprocess.run", forbidden_launch)
    with pytest.raises(ValueError, match="inventory mismatch.*replay_native_package"):
        replay_native_package(output)


@pytest.mark.parametrize("kind", ["input", "package", "destination"])
def test_symlinked_ancestor_is_rejected(tmp_path, kind):
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    if kind == "input":
        shutil.copytree(FIXTURE, actual / "input")
        with pytest.raises(ValueError, match="ancestors.*ordinary"):
            build_native_replay_package(linked / "input", tmp_path / "new", configuration_id="codex-cli", repetition=1)
    elif kind == "package":
        build_native_replay_package(FIXTURE, actual / "package", configuration_id="codex-cli", repetition=1)
        with pytest.raises(ValueError, match="ancestors.*ordinary"):
            replay_native_package(linked / "package")
    else:
        with pytest.raises(ValueError, match="ancestors.*symlinked"):
            build_native_replay_package(FIXTURE, linked / "new", configuration_id="codex-cli", repetition=1)


def test_snapshot_is_replayed_even_if_source_changes_after_validation(tmp_path, monkeypatch):
    import session_bench.native_replay as replay_module
    output = tmp_path / "closed"
    manifest = build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    original_validate = replay_module._manifest
    def mutate_after_validation(contents):
        validated = original_validate(contents)
        (output / "runtime/scripts/replay_native_package.py").write_text("raise RuntimeError('reopened unsafe file')")
        return validated
    monkeypatch.setattr(replay_module, "_manifest", mutate_after_validation)
    receipt = replay_native_package(output)
    assert receipt["decode_sha256"] == manifest["expected_decode_sha256"]


def test_trusted_manifest_digest_rejects_rehashed_runtime_mutation_before_exec(tmp_path, monkeypatch):
    output = tmp_path / "closed"
    build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    manifest_path = output / "manifest.json"
    trusted_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    runtime_name = "runtime/session_bench/survival_metrics.py"
    (output / runtime_name).write_text("raise RuntimeError('untrusted runtime')")
    manifest = json.loads(manifest_path.read_text())
    entry = next(entry for entry in manifest["files"] if entry["path"] == runtime_name)
    data = (output / runtime_name).read_bytes()
    entry.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr("session_bench.native_replay.subprocess.run", lambda *args, **kwargs: pytest.fail("untrusted runtime executed"))
    with pytest.raises(ValueError, match="trusted expected digest"):
        replay_native_package(output, expected_manifest_sha256=trusted_digest)


def test_rehashed_extra_runtime_module_still_violates_fixed_closure(tmp_path):
    output = tmp_path / "closed"
    build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    extra = output / "runtime/extra.py"
    extra.write_bytes(b"# unwanted dependency\n")
    manifest["files"].append({"path": "runtime/extra.py", "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(), "size_bytes": extra.stat().st_size})
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="fixed decoder dependency"):
        replay_native_package(output)


def test_does_not_overwrite_or_follow_input_symlinks(tmp_path):
    output = tmp_path / "closed"
    output.mkdir()
    with pytest.raises(ValueError, match="destination must not exist"):
        build_native_replay_package(FIXTURE, output, configuration_id="codex-cli", repetition=1)
    link = tmp_path / "input"
    link.symlink_to(FIXTURE, target_is_directory=True)
    with pytest.raises(ValueError, match="ordinary"):
        build_native_replay_package(link, tmp_path / "new", configuration_id="codex-cli", repetition=1)


def test_output_cannot_modify_the_copied_native_input_tree(tmp_path):
    source = tmp_path / "input"
    shutil.copytree(FIXTURE, source)
    with pytest.raises(ValueError, match="immutable input tree"):
        build_native_replay_package(source, source / "generated", configuration_id="codex-cli", repetition=1)
    assert not (source / "generated").exists()
