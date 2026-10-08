from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from scripts import prepare_claude_desktop_v1_run as prepare


REPO = Path(__file__).resolve().parents[1]
FROZEN = REPO / "fixtures/scenarios/survival-v1/workload"
RUN_ID = "claude-desktop-v1-eval-test-01"


def temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    workload = repo / "fixtures/scenarios/survival-v1/workload"
    workload.parent.mkdir(parents=True)
    shutil.copytree(FROZEN, workload)
    (repo / "artifacts/survival-v1-runs").mkdir(parents=True)
    return repo


def test_prepare_rejects_an_existing_run_id_without_touching_it(tmp_path: Path):
    repo = temp_repo(tmp_path)
    existing = repo / "artifacts/survival-v1-runs" / RUN_ID
    existing.mkdir()
    sentinel = existing / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="already in use"):
        prepare.prepare_run(RUN_ID, repo_root=repo)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert list((repo / "artifacts/survival-v1-runs").iterdir()) == [existing]


def test_prepared_paths_are_run_bound_and_starter_files_are_copied_intact(tmp_path: Path):
    repo = temp_repo(tmp_path)
    run_root = prepare.prepare_run(RUN_ID, repo_root=repo)
    workspace = run_root / "workspace/fixture_project"

    assert run_root == repo / "artifacts/survival-v1-runs" / RUN_ID
    assert list((repo / "artifacts/survival-v1-runs").iterdir()) == [run_root]
    source_fixture = repo / "fixtures/scenarios/survival-v1/workload/fixture_project"
    for source in sorted(path for path in source_fixture.rglob("*") if path.is_file()):
        relative = source.relative_to(source_fixture)
        assert (workspace / relative).read_bytes() == source.read_bytes()

    workload = json.loads((run_root / "workload-instance.json").read_text())
    attempt = json.loads((run_root / "attempt.json").read_text())
    wrapper = workspace / ".claude/hooks/record-session-bench-hook.py"
    settings = json.loads((workspace / ".claude/settings.local.json").read_text())
    assert workload["run_id"] == RUN_ID
    assert workload["run_canary"] == "SB_SURVIVAL_V1_RUN_" + RUN_ID
    assert all(RUN_ID in turn["text"] for turn in workload["turns"])
    assert attempt["state"] == "prepared_unscored"
    assert attempt["score_eligible"] is False
    assert attempt["public_score_eligible"] is False
    assert str(run_root) in wrapper.read_text(encoding="utf-8")
    command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert str(wrapper) in command
    assert set(settings["hooks"]) == {
        "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    }
    assert not (run_root / "capture/otel-session-id.private.txt").exists()
    assert "Preparation did not open Claude Desktop" in (run_root / "README.md").read_text()


def test_otel_session_file_is_latched_only_by_an_exact_workload_prompt(tmp_path: Path):
    repo = temp_repo(tmp_path)
    run_root = prepare.prepare_run(RUN_ID, repo_root=repo)
    workspace = run_root / "workspace/fixture_project"
    wrapper = workspace / ".claude/hooks/record-session-bench-hook.py"
    session_file = run_root / "capture/otel-session-id.private.txt"
    workload = json.loads((run_root / "workload-instance.json").read_text())
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO)

    base = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "exact-session-01",
        "cwd": str(workspace),
    }
    rejected = subprocess.run(
        [sys.executable, str(wrapper)],
        input=json.dumps({**base, "prompt": "not the frozen workload"}).encode(),
        cwd=workspace,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert not session_file.exists()

    accepted = subprocess.run(
        [sys.executable, str(wrapper)],
        input=json.dumps({**base, "prompt": workload["turns"][0]["text"]}).encode(),
        cwd=workspace,
        env=environment,
        capture_output=True,
        check=False,
    )
    assert accepted.returncode == 0, accepted.stderr.decode()
    assert session_file.read_text(encoding="utf-8") == "exact-session-01\n"
    assert os.stat(session_file).st_mode & 0o777 == 0o600


def test_readme_names_exact_capture_steps_without_claiming_execution(tmp_path: Path):
    repo = temp_repo(tmp_path)
    run_root = prepare.prepare_run(RUN_ID, repo_root=repo)
    readme = (run_root / "README.md").read_text(encoding="utf-8")

    assert "capture_claude_desktop_source_locations.py" in readme
    desktop_session_file = run_root / "capture/desktop-ui-session-id.private.txt"
    assert f"--expected-desktop-session-id-file {shlex.quote(str(desktop_session_file))}" in readme
    assert "local_<uuid-from-active-desktop-url>" not in readme
    assert "UI identity file only after the command finishes" in readme
    assert "After both responses, copy the `local_<uuid>` ID from the active Claude Code URL" in readme
    assert "umask 077" in readme
    assert "IFS= read -r desktop_session_id" in readme
    assert f"printf '%s\\n' \"$desktop_session_id\" > {shlex.quote(str(desktop_session_file))}" in readme
    assert readme.index("IFS= read -r desktop_session_id") < readme.index("Then press Return")
    assert not desktop_session_file.exists()
    assert "without opening unrelated candidate files" in readme
    assert "capture_claude_desktop_otel_usage.py" in readme
    assert "CLAUDE_CODE_ENABLE_TELEMETRY=1" in readme
    assert "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4318/v1/logs" in readme
    assert "OTEL_LOG_USER_PROMPTS=0" in readme
    assert "OTEL_LOG_ASSISTANT_RESPONSES=1" in readme
    assert "OTEL_METRICS_EXPORTER=none" in readme
    assert "Claude Sonnet 5.5 at Medium effort" in readme
    assert "record_claude_desktop_gui_event.py" in readme
    assert "finalize_claude_desktop_runs.py" in readme
    assert "Preparation did not open Claude Desktop" in readme
    assert "grant a score" in readme
    assert "rank, public-safety approval" in readme
