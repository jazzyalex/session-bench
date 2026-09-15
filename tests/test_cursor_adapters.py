"""Offline controls for the bounded Cursor survival-v1 adapters.

These tests use static metadata and fake streams/runners only.  They do not open
Cursor, launch a model, inspect a normal profile, or copy credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_bench.adapters.cursor import (
    CLI_SURFACE,
    DESKTOP_SURFACE,
    DesktopBlockedError,
    DesktopObservation,
    CursorAdapterError,
    CursorCliAdapter,
    CursorDesktopAdapter,
    CursorPreflightError,
    StaticIdentityProbe,
    SurfaceMismatchError,
    assert_surface_separation,
    build_cli_launch_plan,
    build_desktop_launch_plan,
    observe_cli_stream,
    preflight_cli,
    preflight_desktop,
    require_desktop_calibration_ready,
    reject_desktop_ranked_result,
    run_cli,
)


CLI_IDENTITY = {"surface": CLI_SURFACE, "product": "Cursor Agent", "version": "2026.09.08-6caf4ff"}
DESKTOP_IDENTITY = {
    "surface": DESKTOP_SURFACE,
    "nameShort": "Cursor",
    "version": "3.12.30",
    "commit": "63a2996a10d9e476b6c28e951dd7691d9c0cf480",
}


def roots(tmp_path: Path) -> tuple[Path, Path]:
    run_root = tmp_path / "run"
    workspace = run_root / "project"
    workspace.mkdir(parents=True)
    return run_root, workspace


def test_cli_preflight_is_pure_and_binds_isolated_data_and_config(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    probe = StaticIdentityProbe(CLI_IDENTITY)

    result = preflight_cli(run_root, workspace, identity_probe=probe)

    assert result.ready_for_calibration is True
    assert result.model_started is False
    assert result.rankable is False
    assert result.plan.surface == CLI_SURFACE
    assert result.plan.argv == (
        "agent", "--print", "--output-format", "stream-json", "--workspace", str(workspace)
    )
    assert result.plan.env == {
        "CURSOR_DATA_DIR": str(run_root / "cursor-data"),
        "CURSOR_CONFIG_DIR": str(run_root / "cursor-config"),
    }
    assert result.plan.data_dir.is_relative_to(run_root)
    assert result.plan.config_dir.is_relative_to(run_root)
    assert not result.plan.data_dir.exists()
    assert not result.plan.config_dir.exists()


def test_cli_preflight_never_calls_a_runner_or_reads_the_workspace(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    called: list[object] = []

    def probe(**kwargs):
        called.append(kwargs)
        return CLI_IDENTITY

    result = CursorCliAdapter(identity_probe=probe).preflight(run_root, workspace)
    assert result.plan.identity.version == CLI_IDENTITY["version"]
    assert called == [{"surface": CLI_SURFACE, "executable": "agent"}]
    # The project is a path contract only; preflight does not enumerate it.
    assert list(workspace.iterdir()) == []


@pytest.mark.parametrize("bad", ["relative-run", Path.home() / ".cursor"])
def test_cli_rejects_non_isolated_run_root(tmp_path: Path, bad: str | Path) -> None:
    workspace = tmp_path / "project"
    with pytest.raises(CursorPreflightError):
        build_cli_launch_plan(bad, workspace, identity_probe=CLI_IDENTITY)


def test_cli_requires_a_static_identity_probe(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    with pytest.raises(CursorPreflightError, match="identity probe"):
        build_cli_launch_plan(run_root, workspace, identity_probe=None)


def test_identity_surface_is_never_reused_between_cli_and_desktop(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    with pytest.raises(SurfaceMismatchError, match="identity surface"):
        build_cli_launch_plan(run_root, workspace, identity_probe=DESKTOP_IDENTITY)
    with pytest.raises(SurfaceMismatchError, match="identity surface"):
        build_desktop_launch_plan(run_root, workspace, identity_probe=CLI_IDENTITY)


def test_identity_probe_rejects_credential_or_history_fields(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    for field in ("access_token", "cookie_jar", "session_history"):
        with pytest.raises(CursorPreflightError, match="credentials or history"):
            build_cli_launch_plan(
                run_root,
                workspace,
                identity_probe={**CLI_IDENTITY, field: "must-not-be-collected"},
            )


class FakeRunner:
    def __init__(self, output: str):
        self.output = output
        self.calls: list[tuple[tuple[str, ...], dict[str, str], Path]] = []

    def run(self, argv, *, env, cwd):
        self.calls.append((tuple(argv), dict(env), cwd))
        return {"stdout": self.output, "returncode": 0}


def stream_with_canaries() -> str:
    rows = [
        {"type": "tool_call", "text": "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "R1 result\nSB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"}]},
        },
        {"type": "result", "result": "R2 result\nSB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"},
    ]
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def test_cli_runner_is_injected_and_stream_canaries_are_response_boundaries(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    plan = build_cli_launch_plan(run_root, workspace, identity_probe=CLI_IDENTITY)
    runner = FakeRunner(stream_with_canaries())

    observed = run_cli(
        plan,
        "synthetic prompt",
        runner=runner,
        expected_canaries=(
            "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
            "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ",
        ),
    )

    assert observed.surface == CLI_SURFACE
    assert observed.matched_canaries == (
        "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
        "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ",
    )
    assert len(observed.responses) == 2
    assert runner.calls == [
        (
            plan.argv + ("synthetic prompt",),
            dict(plan.env),
            workspace,
        )
    ]


def test_cli_stream_does_not_count_a_canary_in_a_tool_event() -> None:
    tool_only = json.dumps({"type": "tool_call", "arguments": "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"})
    with pytest.raises(CursorAdapterError, match="response boundary"):
        observe_cli_stream(tool_only, expected_canaries=("SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",))


def test_cli_stream_rejects_malformed_and_unbounded_input() -> None:
    with pytest.raises(CursorAdapterError, match="not JSON"):
        observe_cli_stream("not-json")
    with pytest.raises(CursorAdapterError, match="event bound"):
        observe_cli_stream(
            (json.dumps({"type": "progress", "value": index}) for index in range(3)),
            max_events=2,
        )


def test_desktop_plan_has_safe_isolated_flags_but_does_not_open_cursor(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    plan = build_desktop_launch_plan(run_root, workspace, identity_probe=DESKTOP_IDENTITY)

    assert plan.surface == DESKTOP_SURFACE
    assert plan.argv == (
        "Cursor",
        "--user-data-dir",
        str(run_root / "cursor-desktop-user-data"),
        "--extensions-dir",
        str(run_root / "cursor-desktop-extensions"),
        str(workspace),
    )
    assert plan.user_data_dir.is_relative_to(run_root)
    assert plan.extensions_dir.is_relative_to(run_root)
    assert plan.model_started is False
    assert not plan.user_data_dir.exists()
    assert not plan.extensions_dir.exists()


def test_desktop_preflight_fails_closed_without_gui_and_complete_roots(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    result = preflight_desktop(run_root, workspace, identity_probe=DESKTOP_IDENTITY)

    assert result.ready_for_calibration is False
    assert result.rankable is False
    assert "actual_gui_observation_required" in result.reasons
    assert "complete_native_transcript_roots_required" in result.reasons
    with pytest.raises(DesktopBlockedError, match="remains blocked"):
        require_desktop_calibration_ready(result)


def test_desktop_complete_observation_can_prepare_calibration_but_never_rank(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    user_data = run_root / "cursor-desktop-user-data"
    observation = DesktopObservation(
        gui_observed=True,
        observer_bound_to_isolated_process=True,
        accessibility_text="synthetic response boundary",
        visual_evidence_reviewed=True,
        native_transcript_roots=(user_data / "User" / "workspaceStorage" / "conversation",),
        native_roots_complete=True,
    )
    result = preflight_desktop(
        run_root,
        workspace,
        identity_probe=DESKTOP_IDENTITY,
        observation=observation,
    )

    assert result.ready_for_calibration is True
    assert result.reasons == ()
    assert result.rankable is False
    assert require_desktop_calibration_ready(result) is result
    with pytest.raises(DesktopBlockedError, match="no rankable result"):
        reject_desktop_ranked_result(result)


def test_desktop_rejects_transcript_roots_outside_the_isolated_root(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    result = preflight_desktop(
        run_root,
        workspace,
        identity_probe=DESKTOP_IDENTITY,
        observation={
            "gui_observed": True,
            "observer_bound_to_isolated_process": True,
            "accessibility_text": "observed",
            "visual_evidence_reviewed": True,
            "native_transcript_roots": [str(tmp_path / "normal-profile" / "conversation")],
            "native_roots_complete": True,
        },
    )
    assert result.ready_for_calibration is False
    assert "native_transcript_root_outside_isolated_user_data" in result.reasons


def test_surface_separation_rejects_a_shared_native_root(tmp_path: Path) -> None:
    run_root, workspace = roots(tmp_path)
    shared = run_root / "shared"
    cli = build_cli_launch_plan(
        run_root,
        workspace,
        identity_probe=CLI_IDENTITY,
        data_dir=shared,
    )
    desktop = build_desktop_launch_plan(
        run_root,
        workspace,
        identity_probe=DESKTOP_IDENTITY,
        user_data_dir=shared,
    )
    with pytest.raises(SurfaceMismatchError, match="roots"):
        assert_surface_separation(cli, desktop)
