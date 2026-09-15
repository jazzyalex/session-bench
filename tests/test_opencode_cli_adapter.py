"""Offline controls for the bounded OpenCode survival-v1 adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session_bench.adapters.opencode_cli import (
    OPENCODE_DEFAULT_MODEL,
    AttemptLedger,
    CaptureError,
    ExplicitAuth,
    IsolationError,
    OpenCodeIdentity,
    RunnerResult,
    bind_observer_to_native_session,
    build_launch,
    calibration_command,
    capture_sqlite_bundle,
    observe_stdout,
    proposed_environment,
    run_calibration,
)


def _auth(run_root: Path) -> ExplicitAuth:
    target = run_root / "xdg-data" / "opencode" / "auth.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"provider":"fixture"}', encoding="utf-8")
    return ExplicitAuth(target)


def _launch(tmp_path: Path):
    run_root = tmp_path / "run-root"
    project = tmp_path / "fixture-project"
    project.mkdir()
    return build_launch(run_root, project, "calibration prompt", auth=_auth(run_root)), run_root, project


def test_route_uses_fresh_data_home_and_explicit_database(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    environment = proposed_environment(run_root)

    assert environment == {
        "OPENCODE_DB": str(run_root.resolve() / "opencode.db"),
    }

    project = tmp_path / "project"
    project.mkdir()
    launch = build_launch(run_root, project, "prompt", auth=_auth(run_root))
    assert OPENCODE_DEFAULT_MODEL == "opencode/muse-spark-1.3-contributor-free"
    assert launch.argv == (
        "opencode",
        "run",
        "--model",
        OPENCODE_DEFAULT_MODEL,
        "--pure",
        "--format",
        "json",
        "--dir",
        str(project.resolve()),
        "prompt",
    )
    assert launch.identity.model == OPENCODE_DEFAULT_MODEL
    assert calibration_command(launch) == launch.argv
    assert launch.environment["OPENCODE_DB"].endswith("/opencode.db")


def test_explicit_alternative_model_pin_is_preserved(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alternative = "fixture-provider/alternative-model"
    launch = build_launch(
        tmp_path / "run-root",
        project,
        "prompt",
        model=alternative,
        identity=OpenCodeIdentity(model=alternative),
    )

    assert "--model" in launch.argv
    assert launch.argv[launch.argv.index("--model") + 1] == alternative
    assert launch.identity.model == alternative
    assert calibration_command(launch) == launch.argv
    assert calibration_command(launch, model=alternative) == launch.argv
    rebuilt = calibration_command(tmp_path / "run-root-2", project, "prompt", model=alternative)
    assert rebuilt[rebuilt.index("--model") + 1] == alternative


@pytest.mark.parametrize(
    "bad_model",
    ["", "   ", "baremodel", " opencode/model", "opencode/model ", "-opencode/model", "--model", "/model", "provider/", "opencode/"],
)
def test_invalid_model_ids_are_rejected(tmp_path: Path, bad_model: str) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ValueError, match="model"):
        build_launch(tmp_path / "run-root", project, "prompt", model=bad_model)
    with pytest.raises(ValueError, match="model"):
        OpenCodeIdentity(model=bad_model)


def test_identity_model_mismatch_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alternative = "fixture-provider/alternative-model"
    with pytest.raises(ValueError, match="[Mm]ismatch|model"):
        build_launch(
            tmp_path / "run-root",
            project,
            "prompt",
            model=alternative,
            identity=OpenCodeIdentity(model=OPENCODE_DEFAULT_MODEL),
        )
    with pytest.raises(ValueError, match="[Mm]ismatch|model"):
        build_launch(
            tmp_path / "run-root",
            project,
            "prompt",
            identity=OpenCodeIdentity(model=alternative),
        )


def test_calibration_command_model_mismatch_in_prompt_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    decoy = "fixture-provider/alternative-model"
    launch = build_launch(tmp_path / "run-root", project, f"mention {decoy} in prompt")

    assert decoy in launch.argv[-1]
    with pytest.raises(ValueError, match="[Mm]ismatch|model|pin"):
        calibration_command(launch, model=decoy)


def test_default_auth_never_requires_reading_or_copying_auth_material(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    launch = build_launch(tmp_path / "run-root", project, "prompt")

    assert launch.auth is None
    assert "XDG_DATA_HOME" not in launch.environment
    assert launch.require_auth() is None


def test_normal_profile_auth_is_rejected_without_being_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    run_root = tmp_path / "run-root"
    normal_auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    with pytest.raises(IsolationError, match="isolated"):
        build_launch(run_root, project, "prompt", auth=normal_auth, validate_auth=True)


def test_stdout_binds_exactly_one_new_native_session_marker() -> None:
    stream = [
        {"type": "session.created", "sessionID": "session-new"},
        {"type": "text", "sessionID": "session-new", "text": "done\nCANARY"},
    ]
    observation = observe_stdout(stream, response_canaries=("CANARY",))

    assert observation.session_id == "session-new"
    assert observation.response_canaries == ("CANARY",)
    assert observation.response_boundaries == (2,)
    assert bind_observer_to_native_session(observation, ("session-new",)) == "session-new"

    with pytest.raises(ValueError, match="pre-existing"):
        observe_stdout(stream, existing_session_ids=("session-new",))
    with pytest.raises(ValueError, match="exactly one"):
        observe_stdout(
            [
                {"sessionID": "session-a", "type": "session.created"},
                {"sessionID": "session-b", "type": "session.created"},
            ]
        )


def test_capture_requires_quiescent_db_wal_and_shm_and_hashes_all_files(tmp_path: Path) -> None:
    source = tmp_path / "run" / "opencode.db"
    source.parent.mkdir()
    source.write_bytes(b"sqlite fixture")
    (source.with_name("opencode.db-wal")).write_bytes(b"wal fixture")
    (source.with_name("opencode.db-shm")).write_bytes(b"shm fixture")
    destination = tmp_path / "capture"

    capture = capture_sqlite_bundle(source, destination, barrier=lambda: {"quiescent": True, "writer_alive": False})

    assert [item.relative_path for item in capture.artifacts] == [
        "opencode.db",
        "opencode.db-wal",
        "opencode.db-shm",
    ]
    assert all((destination / item.relative_path).read_bytes() for item in capture.artifacts)
    assert all(len(item.sha256) == 64 for item in capture.artifacts)

    with pytest.raises(CaptureError, match="missing"):
        (source.with_name("opencode.db-shm")).unlink()
        capture_sqlite_bundle(
            source,
            tmp_path / "missing-capture",
            barrier=lambda: True,
        )


def test_live_writer_barrier_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "opencode.db"
    source.write_bytes(b"db")
    source.with_name("opencode.db-wal").write_bytes(b"wal")
    source.with_name("opencode.db-shm").write_bytes(b"shm")

    with pytest.raises(CaptureError, match="live"):
        capture_sqlite_bundle(source, tmp_path / "capture", barrier=lambda: {"writer_alive": True})


class FakeRunner:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.calls: list[tuple[tuple[str, ...], dict[str, str], Path]] = []

    def run(self, argv, *, env, cwd):
        self.calls.append((tuple(argv), dict(env), cwd))
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.database.write_bytes(b"sqlite fixture")
        self.database.with_name("opencode.db-wal").write_bytes(b"wal fixture")
        self.database.with_name("opencode.db-shm").write_bytes(b"shm fixture")
        stream = (
            json.dumps({"type": "session.created", "sessionID": "session-new"}) + "\n"
            + json.dumps({"type": "text", "sessionID": "session-new", "text": "read and edit CANARY"}) + "\n"
        )
        return RunnerResult(0, stream)


def test_calibration_uses_injected_runner_and_retains_read_edit_proof(tmp_path: Path) -> None:
    launch, run_root, _ = _launch(tmp_path)
    runner = FakeRunner(launch.database)
    ledger = AttemptLedger()
    result = run_calibration(
        launch,
        attempt_id="opencode-cli-cal-1",
        runner=runner,
        destination=tmp_path / "capture",
        barrier=lambda: True,
        native_session_reader=lambda copied: ("session-new",),
        response_canaries=("CANARY",),
        permission_probe={
            "read_ok": True,
            "edit_ok": True,
            "read_marker": "read-fixture",
            "edit_marker": "edit-fixture",
            "before_sha256": "a" * 64,
            "after_sha256": "b" * 64,
        },
        ledger=ledger,
    )

    assert result.calibration_ready is True
    assert result.attempt.state == "complete"
    assert result.attempt.native_session_id == "session-new"
    assert result.attempt.permission_qualified is True
    assert len(runner.calls) == 1
    assert len(ledger.records) == 1
    assert runner.calls[0][1]["XDG_DATA_HOME"] == str(run_root / "xdg-data")


def test_failed_explicit_auth_attempt_is_preserved_and_runner_is_not_called(tmp_path: Path) -> None:
    run_root = tmp_path / "run-root"
    project = tmp_path / "project"
    project.mkdir()
    launch = build_launch(
        run_root,
        project,
        "prompt",
        auth=ExplicitAuth(run_root / "xdg-data" / "opencode" / "auth.json"),
    )
    ledger = AttemptLedger()

    result = run_calibration(
        launch,
        attempt_id="opencode-cli-cal-1",
        runner=lambda **_: pytest.fail("runner must not be called without auth"),
        destination=tmp_path / "capture",
        barrier=lambda: True,
        permission_probe={"read_ok": True, "edit_ok": True, "before": "a", "after": "b"},
        ledger=ledger,
    )

    assert result.calibration_ready is False
    assert result.attempt.state == "invalid"
    assert "opencode.auth_or_isolation" in result.attempt.reason_ids
    assert ledger.records[0].attempt_id == "opencode-cli-cal-1"
