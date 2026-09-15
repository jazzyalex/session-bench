from pathlib import Path

import pytest

from session_bench.adapters.codex_desktop import (
    CodexDesktopIdentity,
    CodexDesktopPreflight,
    DesktopObserverContract,
    DesktopRootContract,
    blocked_current_preflight,
    calibration_command,
    proposed_environment,
    task_api_normal_root_preflight,
)


def identity() -> CodexDesktopIdentity:
    return CodexDesktopIdentity(
        app_path="/Applications/ChatGPT.app",
        bundle_identifier="com.openai.codex",
        version="26.903.71938",
        build="8576",
        bundled_cli_version="codex-cli 0.153.4",
    )


def test_current_preflight_is_explicitly_blocked() -> None:
    result = blocked_current_preflight(identity())
    assert result.calibration_ready is False
    assert result.reason_ids == (
        "codex_desktop.gui_observer_denied",
        "codex_desktop.isolated_launch_unproven",
        "codex_desktop.complete_root_unproven",
    )
    with pytest.raises(RuntimeError, match="gui_observer_denied"):
        result.require_calibration_ready()
    with pytest.raises(RuntimeError, match="supported app task API"):
        calibration_command(result)


def test_all_independent_gates_are_required() -> None:
    observer = DesktopObserverContract("fixture", True, True, True)
    native = DesktopRootContract(("/isolated/native",), True, True, True)
    ready = CodexDesktopPreflight(identity(), observer, native, True, ())
    assert ready.calibration_ready is True
    assert ready.evaluation_ready is True
    ready.require_calibration_ready()
    ready.require_evaluation_ready()

    task_api_calibration = CodexDesktopPreflight(identity(), observer, native, False, ())
    assert task_api_calibration.calibration_ready is True
    assert task_api_calibration.evaluation_ready is True
    task_api_calibration.require_evaluation_ready()
    assert DesktopObserverContract("fixture", False, True, True).qualified is False
    assert DesktopRootContract(("/isolated/native",), True, False, True).qualified is False

    incomplete_root = DesktopRootContract(("/selected/new-session",), False, False, True)
    calibration = CodexDesktopPreflight(identity(), observer, incomplete_root, False, ())
    assert calibration.calibration_ready is True
    assert calibration.evaluation_ready is False


def test_proposed_environment_never_claims_launch_proof(tmp_path: Path) -> None:
    assert proposed_environment(tmp_path) == {"CODEX_HOME": str(tmp_path.resolve() / "codex-home")}


def test_task_api_normal_root_is_ready_only_after_metadata_safe_complete_capture() -> None:
    ready = task_api_normal_root_preflight(
        identity(), roots=("CODEX_HOME/sessions", "CODEX_HOME/shell_snapshots"),
        complete_root_proven=True, newly_created_session_proven=True,
    )
    assert ready.launch_isolated is False
    assert ready.native.safe_capture_boundary is True
    assert ready.evaluation_ready is True

    unsafe = task_api_normal_root_preflight(
        identity(), roots=("CODEX_HOME/sessions",),
        complete_root_proven=True, newly_created_session_proven=True,
        preexisting_content_opened=True,
    )
    assert unsafe.evaluation_ready is False
    assert "codex_desktop.preexisting_content_opened" in unsafe.reason_ids
