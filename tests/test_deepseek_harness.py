"""Offline controls for the bounded DeepSeek Harness capture adapter."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from session_bench.adapters.deepseek_harness import (
    NativeInventoryError,
    PreflightError,
    build_headless_launch_plan,
    inventory_native_root,
    run_headless,
)


IDENTITY = {"product": "DeepSeek Harness", "version": "0.2.0-rc.2"}


def layout(tmp_path: Path) -> tuple[Path, Path]:
    run_root = tmp_path / "dsh-run"
    run_root.mkdir()
    workspace = run_root / "project"
    workspace.mkdir()
    return run_root, workspace


def write_v4(native_root: Path, *, events: list[dict] | None = None, suffix: str = "session.v4.jsonl") -> Path:
    session_dir = native_root / "--tmp-project--" / "synthetic-session"
    session_dir.mkdir(parents=True)
    header = {
        "type": "session",
        "version": 4,
        "id": "synthetic-session",
        "createdAt": 1_700_000_000_000,
        "cwd": "/tmp/synthetic-project",
        "isSeeded": False,
        "delegationDepth": 0,
    }
    event_rows = events if events is not None else [
        {"type": "user/message", "seq": 0, "time": 1_700_000_000_001,
         "data": {"id": "user-1", "role": "user", "content": [{"type": "text", "text": "synthetic"}],
                  "source": {"kind": "user"}}},
        {"type": "assistant/message", "seq": 1, "time": 1_700_000_000_002,
         "data": {"turn": 1, "step": 1, "message": {"content": [{"type": "text", "text": "synthetic answer"}]}}},
    ]
    path = session_dir / suffix
    path.write_bytes(b"".join(json.dumps(item, separators=(",", ":")).encode() + b"\n" for item in [header, *event_rows]))
    return path


def test_plan_is_pure_and_targets_fresh_isolated_dsh_home(tmp_path: Path) -> None:
    run_root, workspace = layout(tmp_path)
    result = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)

    assert result.identity.version == "0.2.0-rc.2"
    assert result.argv == ("dsh", "--profile", "headless", "--json")
    assert result.env == {"DSH_HOME": str(run_root / "dsh-home")}
    assert result.native_root == run_root / "dsh-home" / "sessions"
    assert result.model_started is False
    assert not result.dsh_home.exists()


def test_plan_requires_identity_and_fresh_dsh_home(tmp_path: Path) -> None:
    run_root, workspace = layout(tmp_path)
    with pytest.raises(PreflightError, match="identity probe"):
        build_headless_launch_plan(run_root, workspace, identity_probe=None)

    (run_root / "dsh-home").mkdir()
    with pytest.raises(PreflightError, match="fresh and absent"):
        build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)


def test_plan_rejects_workspace_outside_run_root(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(PreflightError, match="inside run_root"):
        build_headless_launch_plan(run_root, outside, identity_probe=IDENTITY)


def test_injected_runner_receives_headless_json_command(tmp_path: Path) -> None:
    run_root, workspace = layout(tmp_path)
    plan = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)
    calls = []

    def fake_runner(argv, *, env, cwd):
        calls.append((tuple(argv), dict(env), cwd))
        return {"returncode": 0, "stdout": "synthetic"}

    result = run_headless(plan, "do synthetic work", runner=fake_runner)
    assert result["returncode"] == 0
    assert calls == [
        (plan.argv + ("do synthetic work",), dict(plan.env), workspace)
    ]


def test_second_turn_uses_explicit_observed_session_id(tmp_path: Path) -> None:
    run_root, workspace = layout(tmp_path)
    plan = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)
    calls = []
    run_headless(plan, "R2 correction", resume_session_id="observed-session-1", runner=lambda argv, **kwargs: calls.append(tuple(argv)))
    assert calls == [("dsh", "--profile", "headless", "--json", "--session-id", "observed-session-1", "R2 correction")]


@pytest.mark.parametrize("session_id", ["", "--patch", "two words", "line\nbreak", 42])
def test_invalid_resume_id_cannot_reach_runner(tmp_path: Path, session_id) -> None:
    run_root, workspace = layout(tmp_path)
    plan = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)
    with pytest.raises(PreflightError, match="resume session id"):
        run_headless(plan, "R2", resume_session_id=session_id, runner=lambda *args, **kwargs: pytest.fail("runner invoked"))


@pytest.mark.parametrize("task", ["--session-id=unobserved", "--help", "-h", "  --session-id=unobserved"])
def test_task_text_cannot_become_a_cli_option_or_bypass_resume_validation(tmp_path: Path, task: str) -> None:
    run_root, workspace = layout(tmp_path)
    plan = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)
    with pytest.raises(PreflightError, match="option prefix"):
        run_headless(plan, task, runner=lambda *args, **kwargs: pytest.fail("runner invoked"))


def test_mutated_launch_plan_cannot_change_native_root(tmp_path: Path) -> None:
    run_root, workspace = layout(tmp_path)
    plan = build_headless_launch_plan(run_root, workspace, identity_probe=IDENTITY)
    plan = replace(plan, env={"DSH_HOME": str(tmp_path / "other")})
    with pytest.raises(PreflightError, match="environment changed"):
        run_headless(plan, "task", runner=lambda *args, **kwargs: pytest.fail("runner invoked"))


def test_v4_inventory_preserves_header_sequence_time_and_raw_rows(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    path = write_v4(native_root)

    result = inventory_native_root(native_root, run_root=run_root, expected_session_id="synthetic-session")

    assert result.status == "inventory_only"
    assert result.relative_path == "--tmp-project--/synthetic-session/session.v4.jsonl"
    assert result.header["id"] == "synthetic-session"
    assert [event.sequence for event in result.event_boundaries] == [0, 1]
    assert [event.time for event in result.event_boundaries] == [1_700_000_000_001, 1_700_000_000_002]
    assert result.raw_records[1]["data"]["id"] == "user-1"
    assert len(result.sha256) == 64
    assert path.stat().st_size == result.size_bytes


def test_unknown_required_event_is_retained_and_reported_unsupported(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    unknown = {"type": "future/required", "seq": 0, "time": 123, "data": {"opaque": True}}
    write_v4(native_root, events=[unknown])

    result = inventory_native_root(native_root, run_root=run_root)

    assert result.status == "unsupported"
    assert result.raw_records[1]["type"] == "future/required"
    assert result.event_boundaries[0].sequence == 0
    assert result.issues == ("unsupported_event_type:future/required:required_or_unclassified:0",)


def test_unknown_ignorable_event_is_also_explicitly_retained(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    unknown = {"type": "future/ignorable", "seq": 0, "time": 123, "data": {}, "ignorable": True}
    write_v4(native_root, events=[unknown])

    result = inventory_native_root(native_root, run_root=run_root)

    assert result.status == "unsupported"
    assert result.raw_records[1]["ignorable"] is True
    assert "ignorable" in result.issues[0]


def test_corrupt_compressed_v4_is_rejected_not_skipped(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    path = write_v4(native_root, suffix="session.v4.jsonl.zstd")
    path.write_bytes(b"not-decoded-zstd-bytes")

    with pytest.raises(NativeInventoryError, match='frame'):
        inventory_native_root(native_root, run_root=run_root)


@pytest.mark.parametrize(
    "events, message",
    [
        ([{"type": "user/message", "seq": 1, "time": 2, "data": {}}], "sequence is not dense"),
        ([{"type": "user/message", "seq": 0, "time": "bad", "data": {}}], "invalid time"),
        ([{"type": "user/message", "seq": 0, "time": 2}], "invalid data"),
    ],
)
def test_malformed_event_records_are_rejected(tmp_path: Path, events: list[dict], message: str) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    write_v4(native_root, events=events)
    with pytest.raises(NativeInventoryError, match=message):
        inventory_native_root(native_root, run_root=run_root)


def test_torn_jsonl_and_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    path = write_v4(native_root)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(NativeInventoryError, match="torn final line"):
        inventory_native_root(native_root, run_root=run_root)

    path.write_text('{"type":"session","type":"session"}\n', encoding="utf-8")
    with pytest.raises(NativeInventoryError, match="duplicate JSON key"):
        inventory_native_root(native_root, run_root=run_root)


def test_native_inventory_requires_exact_declared_root_and_one_file(tmp_path: Path) -> None:
    run_root, _ = layout(tmp_path)
    native_root = run_root / "dsh-home" / "sessions"
    native_root.mkdir(parents=True)
    with pytest.raises(NativeInventoryError, match="expected exactly one"):
        inventory_native_root(native_root, run_root=run_root)
    with pytest.raises(NativeInventoryError, match="declared isolated"):
        inventory_native_root(tmp_path, run_root=run_root)
