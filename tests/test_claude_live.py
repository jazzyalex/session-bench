import pytest

from session_bench.claude_live import ClaudeLiveError, _normalize_command


def test_normalize_command_strips_shell_terminator_from_run_canary():
    canary = "SB_SURVIVAL_V1_RUN_claude-desktop-api-04"
    command = f'python3 bench_check.py final --run-canary {canary}; echo "exit=$?"'

    assert _normalize_command(command, canary) == ["python3", "bench_check.py", "final", "echo", "exit=$?"]


def test_normalize_command_still_rejects_wrong_canary_with_shell_terminator():
    canary = "SB_SURVIVAL_V1_RUN_claude-desktop-api-04"
    with pytest.raises(ClaudeLiveError, match="wrong run canary"):
        _normalize_command(
            "python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_wrong; echo done",
            canary,
        )
