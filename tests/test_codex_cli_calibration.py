from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from session_bench.codex_cli_calibration import (
    CalibrationError,
    _remove_selected_r2_response,
    build_exec_argv,
    build_resume_argv,
    run_calibration,
    thread_id_from_stdout,
)


THREAD = "01a093ab-3f58-7b51-97dc-e48773802502"


def test_exec_and_resume_commands_keep_supported_flag_scopes(tmp_path: Path) -> None:
    first = build_exec_argv("codex", cwd=tmp_path, prompt="r1", model="gpt-5.6-luna")
    second = build_resume_argv("codex", thread_id=THREAD, prompt="r2", model="gpt-5.6-luna")
    assert first[:6] == ("codex", "exec", "--ignore-user-config", "--json", "--sandbox", "workspace-write")
    assert second[:5] == ("codex", "exec", "resume", "--ignore-user-config", "--json")
    assert "--sandbox" not in second
    assert "-C" not in second
    assert second[5:7] == ("--config", 'sandbox_mode="workspace-write"')
    assert second[7:9] == ("--model", "gpt-5.6-luna")
    assert second[-2:] == (THREAD, "r2")


def test_thread_id_requires_one_observed_thread_started_event() -> None:
    line = json.dumps({"type": "thread.started", "thread_id": THREAD}).encode() + b"\n"
    assert thread_id_from_stdout([line]) == THREAD
    with pytest.raises(CalibrationError, match="exactly one"):
        thread_id_from_stdout([line, line])
    with pytest.raises(CalibrationError, match="stdout is not JSONL"):
        thread_id_from_stdout([b"not-json\n"])


def test_resume_rejects_unobserved_thread_id(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="observed thread id"):
        build_resume_argv("codex", thread_id="untrusted", prompt="r2", model="gpt-5.6-luna")


def test_selected_loss_mutator_supports_direct_desktop_response_without_turn_id(tmp_path: Path) -> None:
    package = tmp_path / "native"
    package.mkdir()
    canary = "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
    rows = [
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "R1"}]}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": f"R2\n{canary}"}]}},
    ]
    rollout = package / "rollout.jsonl"
    rollout.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    import hashlib
    (package / "decode.json").write_text(json.dumps({"format": "codex-rollout-v1", "artifacts": [{"id": "rollout", "path": "rollout.jsonl", "sha256": hashlib.sha256(rollout.read_bytes()).hexdigest(), "size_bytes": rollout.stat().st_size, "depends_on": []}]}), encoding="utf-8")

    _remove_selected_r2_response(package, canary)

    assert canary not in rollout.read_text(encoding="utf-8")
    manifest = json.loads((package / "decode.json").read_text())
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(rollout.read_bytes()).hexdigest()


class _FixtureRunner:
    def __init__(self, source: Path, run_id: str) -> None:
        self.source = source
        self.run_id = run_id
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str]] = []

    def run(self, argv, *, env, cwd, on_stdout):
        self.calls.append(tuple(argv))
        self.envs.append(dict(env))
        sessions = Path(env["CODEX_HOME"]) / "sessions" / "2026" / "09"
        rollout = sessions / f"rollout-{THREAD}.jsonl"
        if len(self.calls) == 1:
            sessions.mkdir(parents=True, exist_ok=True)
            text = self.source.read_text(encoding="utf-8").replace(
                "SB_SURVIVAL_V1_RUN_fixture_0001", f"SB_SURVIVAL_V1_RUN_{self.run_id}",
            )
            rollout.write_text(text, encoding="utf-8")
            on_stdout(json.dumps({"type": "thread.started", "thread_id": THREAD}).encode() + b"\n")
            on_stdout(b'{"type":"item.completed","item":{"text":"SB_SURVIVAL_V1_RESPONSE_R1_cafe_\\ud83d\\ude42"}}\n')
        else:
            helper = Path(cwd) / ".survival-observer.jsonl"
            helper_rows = []
            for phase, code in (("inspect", 0), ("baseline", 1), ("final", 0)):
                body = {"checkout_source": "synthetic"} if phase == "inspect" else {
                    "tests": [
                        {"passed": phase == "final"},
                        {"passed": phase == "final" or phase == "baseline"},
                    ]
                }
                output = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 " + json.dumps(body)
                helper_rows.append({
                    "phase": phase, "exit_code": code,
                    "run_canary": f"SB_SURVIVAL_V1_RUN_{self.run_id}",
                    "cwd": "fixture_project", "argv": ["python3", "bench_check.py", phase],
                    "output": output,
                })
            helper.write_text("\n".join(json.dumps(row) for row in helper_rows) + "\n", encoding="utf-8")
            shutil.copy2(Path(cwd) / "snapshots/checkout.after.py", Path(cwd) / "checkout.py")
            on_stdout(b'{"type":"item.completed","item":{"text":"SB_SURVIVAL_V1_RESPONSE_R2_correction_\\u0394"}}\n')
        return type("Result", (), {"returncode": 0, "stdout": b""})()


def test_controller_preserves_two_turn_copy_and_selected_loss_control(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "isolated-home"
    home.mkdir()
    (home / "auth.json").write_text("{}\n", encoding="utf-8")
    source = next((root / "artifacts/survival-v1-runs/codex-cli-setup-2/capture/private-native").glob("*.jsonl"))
    result = run_calibration(
        run_root=tmp_path / "run", isolated_codex_home=home, run_id="cal-test",
        model="gpt-5.6-luna", runner=_FixtureRunner(source, "cal-test"),
    )
    receipt = json.loads((result.run_root / "capture/calibration-receipt.json").read_text())
    assert result.thread_id == THREAD
    assert receipt["native_root"] == "CODEX_HOME/sessions"
    assert receipt["selected_loss"]["detected"] is True
    assert result.private_package.is_dir() and result.public_package.is_dir()
    assert "/Users/" not in next(result.public_package.glob("**/*.jsonl")).read_text(encoding="utf-8")


def test_authorized_existing_root_uses_stat_only_delta_and_emits_honest_31_metric_wrapper(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    home = tmp_path / "normal-codex-home"
    home.mkdir()
    (home / "auth.json").write_text("{}\n", encoding="utf-8")
    old = home / "sessions/2026/09/rollout-preexisting.jsonl"
    old.parent.mkdir(parents=True)
    old.write_text("private old session\n", encoding="utf-8")
    source = next((root / "artifacts/survival-v1-runs/codex-cli-setup-2/capture/private-native").glob("*.jsonl"))
    runner = _FixtureRunner(source, "normal-root")

    result = run_calibration(
        run_root=tmp_path / "run", isolated_codex_home=home, run_id="normal-root",
        model="gpt-5.6-luna", runner=runner, authorized_existing_root=True,
    )

    receipt = json.loads((result.run_root / "capture/calibration-receipt.json").read_text())
    wrapper = json.loads((result.run_root / "capture/calibration-evidence.json").read_text())
    before = json.loads((result.run_root / "capture/stat-only-before.json").read_text())
    after_r1 = json.loads((result.run_root / "capture/stat-only-after-r1.json").read_text())
    assert runner.envs[0]["SB_SURVIVAL_V1_RUN_CANARY"] == "SB_SURVIVAL_V1_RUN_normal-root"
    assert receipt["native_mode"] == "authorized-existing-account"
    assert receipt["complete_root"] is False
    assert before["metadata_only"] is True
    assert before["preexisting_paths_disclosed"] is False
    assert "rollout-preexisting.jsonl" not in (result.run_root / "capture/stat-only-before.json").read_text()
    assert len(after_r1["new_entries"]) == 1
    assert after_r1["new_entries"][0]["relative_path"].endswith(".jsonl")
    assert wrapper["public_score_created"] is False
    assert wrapper["metric_counts"]["total"] == 31
    assert "portable.complete_root" in wrapper["unresolved_metric_ids"]
    assert "broad.self_contained_identity" in wrapper["unresolved_metric_ids"]


def test_controller_leaves_non_scoring_receipt_when_a_calibration_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "isolated-home"
    home.mkdir()
    (home / "auth.json").write_text("{}\n", encoding="utf-8")

    class BadRunner:
        def run(self, _argv, *, env, cwd, on_stdout):
            sessions = Path(env["CODEX_HOME"]) / "sessions"
            sessions.mkdir(exist_ok=True)
            (sessions / "rollout-test.jsonl").write_text("{}\n", encoding="utf-8")
            on_stdout(json.dumps({"type": "thread.started", "thread_id": THREAD}).encode() + b"\n")
            return type("Result", (), {"returncode": 0, "stdout": b""})()

    with pytest.raises(CalibrationError, match="R1 did not complete"):
        run_calibration(
            run_root=tmp_path / "run", isolated_codex_home=home, run_id="cal-fail",
            model="gpt-5.6-luna", runner=BadRunner(),
        )
    receipt = json.loads((tmp_path / "run/invalid-attempt.json").read_text())
    assert receipt["state"] == "invalid"
    assert receipt["public_score_eligible"] is False
