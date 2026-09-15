"""Synthetic Codex CLI adapter tests; no model or user session store is used."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

from session_bench.adapters.codex_cli import (
    AuthenticationPrerequisite,
    CODEX_RUN_CANARY_ENV,
    CodexCLIAdapter,
    CodexCLIError,
    CodexCLIIdentity,
    CodexCLIRunRoots,
    RunnerResult,
    SubprocessCodexRunner,
    build_codex_argv,
)
from session_bench.surface_capture import ImmutableAttemptLedger


class FakeRunner:
    def __init__(self, *, rollout_count: int = 1) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str], Path]] = []
        self.rollout_count = rollout_count

    def run(self, argv, *, env, cwd, on_stdout):
        self.calls.append((tuple(argv), dict(env), Path(cwd)))
        native = Path(env["CODEX_HOME"]) / "sessions"
        native.mkdir(parents=True)
        for index in range(self.rollout_count):
            (native / f"rollout-{index}.jsonl").write_text(
                '{"type":"session_meta","payload":{"id":"fixture"}}\n', encoding="utf-8"
            )
        on_stdout('{"type":"event_msg"}\n')
        return RunnerResult(returncode=0)


def identity() -> CodexCLIIdentity:
    return CodexCLIIdentity(build="codex-cli 0.154.0", model="fixture-model", os_name="fixture-os")


def test_route_is_exactly_exec_json_and_never_ephemeral(tmp_path: Path) -> None:
    argv = build_codex_argv(tmp_path / "project", "prompt")
    assert argv[:4] == ("/opt/homebrew/bin/codex", "exec", "--ignore-user-config", "--json")
    assert "--ephemeral" not in argv


def test_missing_auth_is_recorded_without_invoking_runner(tmp_path: Path) -> None:
    roots = CodexCLIRunRoots.create(tmp_path / "run").roots
    runner = FakeRunner()
    adapter = CodexCLIAdapter(runner, sleep=lambda _: None)
    attempt, result = adapter.acquire(
        roots=roots,
        auth=None,
        prompt="synthetic",
        attempt_id="attempt-auth",
        result_id="result-auth",
        identity=identity(),
    )
    assert runner.calls == []
    assert attempt.value["status"] == "blocked"
    assert "authentication" in attempt.value["reason"]
    assert result.value["status"] == "blocked"


def test_fixture_execution_captures_one_new_rollout_and_stdout(tmp_path: Path) -> None:
    roots = CodexCLIRunRoots.create(tmp_path / "run").roots
    runner = FakeRunner()
    ledger = ImmutableAttemptLedger(tmp_path / "attempts.jsonl")
    attempt, result = CodexCLIAdapter(runner, sleep=lambda _: None).acquire(
        roots=roots,
        auth=AuthenticationPrerequisite.fixture(),
        prompt="synthetic",
        attempt_id="attempt-1",
        result_id="result-1",
        identity=identity(),
        ledger=ledger,
    )
    assert attempt.value["status"] == "captured"
    assert result.value["status"] == "captured"
    assert attempt.value["env"] == {"CODEX_HOME": str(roots.native_root.resolve())}
    assert len(attempt.value["artifacts"]) == 1
    assert (roots.observer_root / "attempt-1.stdout.jsonl").read_bytes()
    assert ledger.read()[0]["attempt_id"] == "attempt-1"


def test_ambiguous_rollouts_remain_invalid_and_are_not_collapsed(tmp_path: Path) -> None:
    roots = CodexCLIRunRoots.create(tmp_path / "run").roots
    attempt, result = CodexCLIAdapter(FakeRunner(rollout_count=2), sleep=lambda _: None).acquire(
        roots=roots,
        auth=AuthenticationPrerequisite.fixture(),
        prompt="synthetic",
        attempt_id="attempt-ambiguous",
        result_id="result-ambiguous",
        identity=identity(),
    )
    assert attempt.value["status"] == "invalid"
    assert "exactly one" in attempt.value["reason"]
    assert result.value["status"] == "invalid"


def test_existing_account_auth_is_explicit_and_does_not_copy_normal_credentials(tmp_path: Path) -> None:
    native = tmp_path / "existing-codex-home"
    native.mkdir()
    roots = CodexCLIRunRoots.for_existing_account(tmp_path / "run", native).roots
    runner = FakeRunner()
    attempt, _ = CodexCLIAdapter(runner, sleep=lambda _: None).acquire(
        roots=roots,
        auth=AuthenticationPrerequisite.existing_account(),
        prompt="synthetic",
        attempt_id="attempt-existing",
        result_id="result-existing",
        identity=identity(),
    )
    assert attempt.value["status"] == "captured"
    assert attempt.value["roots"]["native_mode"] == "authorized-existing-account"
    assert runner.calls[0][1] == {"CODEX_HOME": str(native.resolve())}


def test_subprocess_runner_streams_only_stdout_under_an_isolated_environment(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text(
        "import os, sys\n"
        "assert 'HOME' not in os.environ\n"
        "assert os.environ['CODEX_HOME'].endswith('isolated-home')\n"
        "sys.stdout.write('{\\\"type\\\":\\\"event\\\"}\\n')\n"
        "sys.stderr.write('private diagnostic\\n')\n",
        encoding="utf-8",
    )
    observed: list[bytes] = []
    result = SubprocessCodexRunner(timeout_seconds=5).run(
        (sys.executable, str(script)),
        env={"CODEX_HOME": str(tmp_path / "isolated-home")},
        cwd=tmp_path,
        on_stdout=observed.append,
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert observed == [b'{"type":"event"}\n']


def test_subprocess_runner_rejects_environment_widening(tmp_path: Path) -> None:
    with pytest.raises(CodexCLIError, match="only isolated CODEX_HOME"):
        SubprocessCodexRunner().run(
            (sys.executable, "-c", "pass"),
            env={"CODEX_HOME": str(tmp_path), "HOME": str(tmp_path)},
            cwd=tmp_path,
            on_stdout=lambda _line: None,
        )


def test_subprocess_runner_passes_only_the_synthetic_run_canary(tmp_path: Path) -> None:
    script = tmp_path / "emit.py"
    script.write_text(
        "import os, sys\n"
        "assert os.environ['SB_SURVIVAL_V1_RUN_CANARY'] == 'SB_SURVIVAL_V1_RUN_test'\n"
        "assert 'HOME' not in os.environ\n"
        "sys.stdout.write('{\\\"type\\\":\\\"event\\\"}\\n')\n",
        encoding="utf-8",
    )
    observed: list[bytes] = []
    result = SubprocessCodexRunner(timeout_seconds=5).run(
        (sys.executable, str(script)),
        env={"CODEX_HOME": str(tmp_path / "isolated-home"), CODEX_RUN_CANARY_ENV: "SB_SURVIVAL_V1_RUN_test"},
        cwd=tmp_path,
        on_stdout=observed.append,
    )
    assert result.returncode == 0
    assert observed == [b'{"type":"event"}\n']


def test_subprocess_runner_times_out_when_no_complete_stdout_line_arrives(tmp_path: Path) -> None:
    with pytest.raises(CodexCLIError, match="timed out"):
        SubprocessCodexRunner(timeout_seconds=0.05).run(
            (sys.executable, "-c", "import time; print('partial', end='', flush=True); time.sleep(1)"),
            env={"CODEX_HOME": str(tmp_path / "isolated-home")},
            cwd=tmp_path,
            on_stdout=lambda _line: None,
        )
