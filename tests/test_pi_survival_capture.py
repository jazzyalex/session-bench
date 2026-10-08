from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from session_bench.pi_survival_capture import (
    PiCaptureError,
    cleanup_pi_scratch,
    execute_pi_capture,
    prepare_pi_capture,
)


REPO = Path(__file__).resolve().parents[1]


def _repo_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    workload_root = repository / "fixtures/scenarios/survival-v1/workload"
    workload_root.parent.mkdir(parents=True)
    shutil.copytree(REPO / "fixtures/scenarios/survival-v1/workload", workload_root)
    return repository


def ready_preflight(_executable, _env):
    return {"ready": True, "version": "pi-test", "auth": {
        "status": "ready", "provider": "openai-codex", "authType": "oauth"},
        "model_catalog_match": "gpt-5.5"}


def _fake_runner_factory():
    calls = []

    def runner(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append((list(argv), cwd, dict(env), timeout))
        turn = 1 if "RESPONSE_R1" in argv[-1] else 2
        fixture = cwd / "fixture_project"
        sid = argv[argv.index("--session-id") + 1]
        native_dir = Path(argv[argv.index("--session-dir") + 1]) / "workspace-key"
        native_dir.mkdir(exist_ok=True)
        native = native_dir / f"{sid}.jsonl"
        if turn == 1:
            rows = [{"type": "session", "version": 3, "id": sid, "cwd": str(cwd)}]
        else:
            rows = []
        prompt = argv[-1]
        if turn == 1:
            for phase in ("inspect", "baseline"):
                subprocess.run(["python3", "bench_check.py", phase], cwd=fixture, env=dict(env),
                               capture_output=True, check=False)
        else:
            (fixture / "checkout.py").write_text(
                "def checkout(items):\n"
                "    subtotal = sum(price * quantity for price, quantity in items)\n"
                "    return subtotal + (0 if subtotal >= 50 else 5)\n"
            )
            subprocess.run(["python3", "bench_check.py", "final"], cwd=fixture, env=dict(env),
                           capture_output=True, check=False)
        rows += [
            {"type": "message", "id": f"user-{turn}", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
            {"type": "message", "id": f"assistant-{turn}", "message": {"role": "assistant", "content": [{"type": "text", "text": f"Done. {('SB_SURVIVAL_V1_RESPONSE_R1_' in prompt) and 'SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂' or 'SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ'}"}]}},
        ]
        with native.open("ab") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False).encode() + b"\n")
        canary = "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂" if turn == 1 else "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
        assistant = {"role": "assistant", "content": [{"type": "text", "text": f"Done. {canary}"}],
                     "api": "openai-codex-responses", "provider": "openai-codex", "model": "gpt-5.5",
                     "usage": {"input": 12, "output": 8, "cacheRead": 0, "cacheWrite": 0,
                               "totalTokens": 20, "cost": {"input": 0, "output": 0, "cacheRead": 0,
                                                                "cacheWrite": 0, "total": 0}},
                     "stopReason": "stop", "timestamp": 1_790_900_000_000}
        protocol_rows = [
            {"type": "session", "version": 3, "id": sid, "cwd": str(cwd)},
            {"type": "message_end", "message": assistant},
        ]
        stdout.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in protocol_rows))
        stderr.write_text("")
        return 0

    return runner, calls


def test_pi_capture_copies_two_turn_native_and_helper_evidence_before_cleanup(tmp_path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/pi-controller-test"
    destination.parent.mkdir(parents=True)
    plan = prepare_pi_capture(destination, repository=repository, repetition=1)
    runner, calls = _fake_runner_factory()
    result = execute_pi_capture(destination, preflight=ready_preflight, runner=runner)
    assert result["status"] == "captured_pending_qualification"
    assert result["model_submissions"] == 2
    assert result["evidence_integrity"]["verified"] is True
    assert len(calls) == 2
    sid = plan["session_id"]
    assert all(call[0][call[0].index("--session-id") + 1] == sid for call in calls)
    assert all(call[0][call[0].index("--mode") + 1] == "json" for call in calls)
    assert plan["stdout_protocol"] == "pi-json-session-events-v1"
    assert calls[0][0][-1] == json.loads((destination / "workload-instance.json").read_text())["turns"][0]["text"]
    assert calls[1][0][-1] == json.loads((destination / "workload-instance.json").read_text())["turns"][1]["text"]
    for turn in (1, 2):
        root = destination / f"turn-r{turn}"
        assert (root / "native/session.jsonl").is_file()
        assert (root / "workspace/fixture_project/.survival-observer.jsonl").is_file()
        assert (root / "workspace/fixture_project/snapshots/checkout.before.py").is_file()
        assert (root / "workspace/fixture_project/snapshots/checkout.after.py").is_file()
        assert (root / "stdout.txt").is_file() and (root / "stderr.txt").is_file()
    assert plan["configuration_id"] == "pi"
    assert plan["capture_environment"]["schema_version"] == "session-bench-pi-capture-host-v1"
    assert result["capture_environment"] == plan["capture_environment"]
    scratch = Path(plan["scratch"])
    cleanup_pi_scratch(destination)
    assert not scratch.exists()


def test_pi_auth_preflight_failure_writes_attempt_without_model_submission(tmp_path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/pi-auth-test"
    destination.parent.mkdir(parents=True)
    prepare_pi_capture(destination, repository=repository, repetition=1)
    calls = []

    def unavailable(_executable, _env):
        return {"ready": False, "auth": {"status": "not_ready", "provider": "openai-codex", "authType": "oauth"}}

    def runner(*args, **kwargs):
        calls.append(args)
        return 0

    result = execute_pi_capture(destination, preflight=unavailable, runner=runner)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 0
    assert calls == []
    with pytest.raises(PiCaptureError, match="cleanup requires verified copies"):
        cleanup_pi_scratch(destination)


def test_pi_native_identity_mismatch_stops_after_first_attempt(tmp_path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/pi-mismatch-test"
    destination.parent.mkdir(parents=True)
    prepare_pi_capture(destination, repository=repository, repetition=1)
    runner, calls = _fake_runner_factory()

    def wrong_id_runner(argv, *, cwd, env, stdout, stderr, timeout):
        code = runner(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
        native = next(Path(argv[argv.index("--session-dir") + 1]).rglob("*.jsonl"))
        rows = [json.loads(line) for line in native.read_text().splitlines()]
        rows[0]["id"] = "00000000-0000-4000-8000-000000000000"
        native.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        return code

    result = execute_pi_capture(destination, preflight=ready_preflight, runner=wrong_id_runner)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 1
    assert "header does not match" in result["failure"]
    assert len(calls) == 1


def test_pi_missing_exact_r1_helper_proof_stops_before_r2(tmp_path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/pi-helper-proof-test"
    destination.parent.mkdir(parents=True)
    prepare_pi_capture(destination, repository=repository, repetition=1)
    runner, calls = _fake_runner_factory()

    def corrupted_ledger_runner(argv, *, cwd, env, stdout, stderr, timeout):
        code = runner(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
        if "RESPONSE_R1" in argv[-1]:
            ledger = cwd / "fixture_project/.survival-observer.jsonl"
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
            rows[0]["run_canary"] = "SB_SURVIVAL_V1_RUN_wrong"
            ledger.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        return code

    result = execute_pi_capture(destination, preflight=ready_preflight, runner=corrupted_ledger_runner)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 1
    assert len(calls) == 1
    assert "does not prove exact inspect execution" in result["failure"]


def test_pi_runner_timeout_is_not_retried_and_preserves_attempt_state(tmp_path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/pi-timeout-test"
    destination.parent.mkdir(parents=True)
    prepare_pi_capture(destination, repository=repository, repetition=1)
    calls = []

    def timeout_runner(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(argv)
        stdout.write_text("")
        stderr.write_text("")
        raise PiCaptureError("turn timed out; no retry or fallback")

    result = execute_pi_capture(destination, preflight=ready_preflight, runner=timeout_runner)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 1
    assert len(calls) == 1
    assert "timed out" in result["failure"]
