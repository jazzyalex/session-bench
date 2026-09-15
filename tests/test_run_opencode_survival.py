"""Tests for scripts/run_opencode_survival.py.

No live model calls. Subprocess is faked, the SQLite/WAL/SHM bundle is a
small synthetic fixture, and the campaign plan is a synthetic plan carrying
the real frozen-input digests (the controller validates them from repo root).
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import os
import shutil
import sqlite3
import sqlite3 as _sqlite3  # noqa: F401  (keeps the sqlite import explicit)
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
CTRL_PATH = REPO / "scripts" / "run_opencode_survival.py"

_spec = importlib.util.spec_from_file_location("run_opencode_survival_ctrl", CTRL_PATH)
assert _spec is not None and _spec.loader is not None
ctrl = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ctrl
_spec.loader.exec_module(ctrl)

from session_bench.adapters.opencode_cli import OPENCODE_DEFAULT_MODEL  # noqa: E402
from session_bench.survival_campaign import (  # noqa: E402
    CONFIGURATIONS,
    FROZEN_INPUT_PATHS,
)

SESSION_ID = "ses_opencode_test_01"
FIXED_CHECKOUT = (
    "def checkout(items):\n"
    "    subtotal = sum(price * quantity for price, quantity in items)\n"
    "    return subtotal if subtotal >= 50 else subtotal + 5\n"
)

_counter = itertools.count()


def _synthetic_plan() -> dict:
    frozen = {}
    for key, rel in FROZEN_INPUT_PATHS.items():
        frozen[key] = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
    surfaces = {
        "codex-cli": "cli",
        "codex-desktop": "desktop",
        "cursor-cli": "cli",
        "cursor-desktop": "desktop",
        "opencode-cli": "cli",
    }
    configurations = [
        {
            "configuration_id": cfg,
            "surface": surfaces[cfg],
            "identity": {"id": cfg, "surface": surfaces[cfg]},
            "usage_budget": {
                "billing_authority": "test-authority",
                "paid_submission_allowed": False,
                "limit_source": "test-source",
            },
        }
        for cfg in CONFIGURATIONS
    ]
    attempts = []
    for cfg in CONFIGURATIONS:
        attempts.append(
            {
                "attempt_id": f"{cfg}-cal-1",
                "configuration_id": cfg,
                "kind": "calibration",
                "repetition": None,
                "state": "scheduled",
            }
        )
        for rep in (1, 2, 3):
            attempts.append(
                {
                    "attempt_id": f"{cfg}-eval-{rep}",
                    "configuration_id": cfg,
                    "kind": "evaluated",
                    "repetition": rep,
                    "state": "scheduled",
                }
            )
        attempts.append(
            {
                "attempt_id": f"{cfg}-crash-1",
                "configuration_id": cfg,
                "kind": "crash_qualification",
                "repetition": None,
                "state": "scheduled",
            }
        )
    return {
        "schema_version": "1.0-survival-campaign",
        "protocol_version": "1.0-survival",
        "state": "authorized_bounded_calibration",
        "frozen_inputs": frozen,
        "quota": {
            "source": "codex_app_account_usage",
            "baseline_used_percent": 12,
            "absolute_stop_used_percent": 95,
            "check": "before_every_model_submission",
            "on_stop": "no_new_submission_finish_in_flight_capture_only",
        },
        "configurations": configurations,
        "attempts": attempts,
    }


@pytest.fixture()
def plan(monkeypatch):
    value = _synthetic_plan()
    monkeypatch.setattr(ctrl, "load_campaign_plan", lambda repo_root: value)
    return value


@pytest.fixture()
def run_root():
    base = REPO / "artifacts" / "survival-v1-runs"
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"ut-{os.getpid()}-{next(_counter)}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeRun:
    """Fake subprocess.run: records calls, returns scripted output."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self.script = script

    def __call__(self, argv, **kwargs):
        self.calls.append(
            {"argv": tuple(argv), "env": dict(kwargs.get("env", {})), "cwd": kwargs.get("cwd")}
        )
        returncode, stdout, stderr = self.script(tuple(argv), kwargs.get("env", {}))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _stdout_lines(session_id, run_canary, response_canary, cost=1.25):
    # ensure_ascii=False mimics the real CLI emitting raw UTF-8 JSON text;
    # the observer matches response canaries against the raw lines.
    return "\n".join(
        [
            json.dumps(
                {
                    "session_id": session_id,
                    "type": "text",
                    "text": f"working {run_canary} {response_canary}",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {"session_id": session_id, "text": "done", "cost": cost},
                ensure_ascii=False,
            ),
        ]
    )


def _turn_script(session_id, state_path, cost=1.25):
    def script(argv, env):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        prompt = argv[-1]
        entry = next(
            item
            for item in state["workload"]["turns"]
            if item["text"] == prompt
        )
        return (
            0,
            _stdout_lines(session_id, state["run_canary"], entry["response_canary"], cost),
            "",
        )

    return script


def _prepare_ok(run_root, attempt="opencode-cli-eval-1", repetition="1"):
    rc = ctrl.main(
        ["prepare", "--attempt-id", attempt, "--repetition", repetition, "--root", str(run_root)]
    )
    assert rc == 0
    return json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))


def _make_native_bundle(root: Path, session_id: str, project: Path):
    """Build a small WAL bundle and hold one idle read connection open.

    SQLite checkpoints the WAL away when the last connection closes, so the
    holder must stay open until capture finishes. The connection is idle (no
    writes), so the capture double-snapshot stays stable. Returns the
    connection; the caller must close it before removing the root.
    """
    db = root / "opencode.db"
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db) + suffix)
        if candidate.exists():
            candidate.unlink()
    writer = sqlite3.connect(str(db))
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute(
            "CREATE TABLE session (id TEXT, directory TEXT, version TEXT)"
        )
        writer.execute(
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)"
        )
        writer.execute(
            "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,"
            " time_created INTEGER, data TEXT)"
        )
        writer.execute(
            "INSERT INTO session VALUES (?,?,?)", (session_id, str(project), "1.18.30")
        )
        writer.execute(
            "INSERT INTO message VALUES (?,?,?,?)",
            ("msg1", session_id, 1, json.dumps({"role": "user"})),
        )
        writer.execute(
            "INSERT INTO part VALUES (?,?,?,?,?)",
            (
                "part1",
                "msg1",
                session_id,
                1,
                json.dumps({"type": "text", "text": "hello"}),
            ),
        )
        writer.commit()
        wal = Path(str(db) + "-wal")
        for index in range(5):
            writer.execute(
                "INSERT INTO part VALUES (?,?,?,?,?)",
                (
                    f"pad{index}",
                    "msg1",
                    session_id,
                    1,
                    json.dumps({"type": "text", "text": "x" * 500}),
                ),
            )
            writer.commit()
            if wal.exists() and wal.stat().st_size > 0:
                break
    except Exception:
        writer.close()
        raise
    assert wal.exists() and wal.stat().st_size > 0, "WAL companion must be non-empty"
    holder = sqlite3.connect(str(db))
    holder.execute("SELECT COUNT(*) FROM session").fetchone()
    writer.close()
    return holder


# -- prepare ---------------------------------------------------------------


def test_prepare_ok_and_state_strict(plan, run_root):
    state = _prepare_ok(run_root)
    assert state["schema_version"] == "1.0-survival-opencode-controller"
    assert state["attempt_id"] == "opencode-cli-eval-1"
    assert state["repetition"] == 1
    assert state["model"] == OPENCODE_DEFAULT_MODEL
    assert state["model"] == "opencode/muse-spark-1.3-contributor-free"
    assert state["opencode_version"] == "1.18.30"
    assert state["phase"] == "prepared"
    assert state["score_eligible"] is False
    assert state["turns"] == {"1": None, "2": None}
    assert len(state["workload"]["turns"]) == 2
    assert "SB_SURVIVAL_V1_RUN_fixture_0001" not in json.dumps(state["workload"])
    assert state["run_canary"] in state["workload"]["turns"][0]["text"]
    assert state["workspace"] == str(run_root / "project")
    assert state["fixture_project"] == str(run_root / "project" / "fixture_project")
    copied = run_root / "project" / "fixture_project" / "bench_check.py"
    assert state["protected_bench_check_sha256"] == hashlib.sha256(
        copied.read_bytes()
    ).hexdigest()
    assert {item.name for item in run_root.iterdir()} == {
        "project",
        "controller-state.json",
    }
    assert {item.name for item in (run_root / "project").iterdir()} == {
        "fixture_project",
    }


def test_prepare_rejects_unknown_attempt(plan, run_root):
    rc = ctrl.main(
        ["prepare", "--attempt-id", "no-such-attempt", "--repetition", "1",
         "--root", str(run_root)]
    )
    assert rc != 0
    assert not run_root.exists()


def test_prepare_rejects_root_outside_runs(plan, tmp_path):
    rc = ctrl.main(
        ["prepare", "--attempt-id", "opencode-cli-eval-1", "--repetition", "1",
         "--root", str(tmp_path / "outside")]
    )
    assert rc != 0


def test_prepare_rejects_existing_root(plan, run_root):
    _prepare_ok(run_root)
    rc = ctrl.main(
        ["prepare", "--attempt-id", "opencode-cli-eval-2", "--repetition", "2",
         "--root", str(run_root)]
    )
    assert rc != 0


def test_prepare_rejects_wrong_configuration(plan, run_root):
    rc = ctrl.main(
        ["prepare", "--attempt-id", "codex-cli-eval-1", "--repetition", "1",
         "--root", str(run_root)]
    )
    assert rc != 0


# -- submit ----------------------------------------------------------------


def test_submit_wrong_phase(plan, run_root, monkeypatch):
    run_root.mkdir(parents=True)
    fake = FakeRun(lambda argv, env: (0, "", ""))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    rc = ctrl.main(
        ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
    )
    assert rc != 0
    assert fake.calls == []


def test_submit_quota_stop_fail_closed(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(lambda argv, env: (0, "", ""))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    rc = ctrl.main(
        ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "95"]
    )
    assert rc != 0
    assert fake.calls == []
    state = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "turn1_invalid"
    assert state["turns"]["1"]["status"] == "invalid"


def test_submit_rejects_non_integer_percent(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(lambda argv, env: (0, "", ""))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    rc = ctrl.main(
        ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "abc"]
    )
    assert rc != 0
    assert fake.calls == []


def test_submit_turn1_exact_pin_single_submission(plan, run_root, monkeypatch):
    state = _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    rc = ctrl.main(
        ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
    )
    assert rc == 0
    assert len(fake.calls) == 1
    argv = list(fake.calls[0]["argv"])
    assert argv[0] == "opencode"
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == OPENCODE_DEFAULT_MODEL
    assert argv[argv.index("--model") + 1] == "opencode/muse-spark-1.3-contributor-free"
    assert sum(1 for item in argv if item == "--model") == 1
    for flag in ("--pure", "--auto"):
        assert flag in argv
    assert argv[argv.index("--format") + 1] == "json"
    assert "--session" not in argv
    assert argv[argv.index("--dir") + 1] == str(run_root / "project")
    assert fake.calls[0]["cwd"] == str(run_root / "project")
    # The frozen child path must resolve inside the workspace cwd.
    assert Path(fake.calls[0]["cwd"], "fixture_project").is_dir()
    env = fake.calls[0]["env"]
    assert env["OPENCODE_DB"] == str(run_root / "opencode.db")
    assert env[ctrl.RUN_CANARY_ENV] == state["run_canary"]
    updated = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert updated["phase"] == "turn1_ok"
    turn = updated["turns"]["1"]
    assert turn["status"] == "ok"
    assert turn["session_id"] == SESSION_ID
    assert turn["response_canary_found"] is True
    assert turn["event_cost"] == 1.25
    assert set(turn["environment_safe"]) == {"OPENCODE_DB", ctrl.RUN_CANARY_ENV}
    assert turn["argv_redacted"][-1].startswith("<prompt-redacted:sha256:")
    assert state["workload"]["turns"][0]["text"] not in json.dumps(turn["argv_redacted"])
    assert turn["helper_ledger"]["present"] is False
    assert len(turn["semantic_cases"]) == 3


def test_workspace_layout_exact(plan, run_root, monkeypatch):
    state = _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    assert len(fake.calls) == 1
    argv = list(fake.calls[0]["argv"])
    assert argv[argv.index("--dir") + 1] == state["workspace"] == str(run_root / "project")
    assert fake.calls[0]["cwd"] == state["workspace"]
    fixture = Path(state["fixture_project"])
    assert fixture == run_root / "project" / "fixture_project"
    assert (fixture / "checkout.py").is_file()
    assert (fixture / "bench_check.py").is_file()
    updated = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert updated["turns"]["1"]["workspace"] == str(run_root / "project")
    assert updated["turns"]["1"]["fixture_project"] == str(fixture)


def test_turn2_continues_same_session(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    (run_root / "project" / "fixture_project" / "checkout.py").write_text(FIXED_CHECKOUT, encoding="utf-8")
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "2", "--codex-used-percent", "20"]
        )
        == 0
    )
    assert len(fake.calls) == 2
    argv2 = list(fake.calls[1]["argv"])
    assert "--session" in argv2
    assert argv2[argv2.index("--session") + 1] == SESSION_ID
    assert argv2[argv2.index("--model") + 1] == OPENCODE_DEFAULT_MODEL
    state = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "turn2_ok"
    assert state["turns"]["2"]["session_id"] == state["turns"]["1"]["session_id"] == SESSION_ID
    assert all(case["passed"] for case in state["turns"]["2"]["semantic_cases"])


def test_turn2_requires_turn1(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(lambda argv, env: (0, "", ""))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    rc = ctrl.main(
        ["submit", "--root", str(run_root), "--turn", "2", "--codex-used-percent", "10"]
    )
    assert rc != 0
    assert fake.calls == []


def test_no_bench_check_execution(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    (run_root / "project" / "fixture_project" / "checkout.py").write_text(FIXED_CHECKOUT, encoding="utf-8")
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "2", "--codex-used-percent", "10"]
        )
        == 0
    )
    for call in fake.calls:
        assert "bench_check.py" not in call["argv"]
    # The helper ledger exists only if bench_check.py runs; the controller
    # must never run it, so no ledger may appear and no import may exist.
    assert not (run_root / "project" / "fixture_project" / ".survival-observer.jsonl").exists()
    assert "import bench_check" not in CTRL_PATH.read_text(encoding="utf-8")
    state = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert all(case["passed"] for case in state["turns"]["2"]["semantic_cases"])


def test_cli_rejects_model_override(plan, run_root):
    with pytest.raises(SystemExit) as exc:
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1",
             "--codex-used-percent", "10", "--model", "other/model"]
        )
    assert exc.value.code == 2


# -- capture ---------------------------------------------------------------


def test_capture_requires_turn2(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    assert ctrl.main(["capture", "--root", str(run_root)]) != 0


def test_capture_happy_path(plan, run_root, monkeypatch):
    _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    (run_root / "project" / "fixture_project" / "checkout.py").write_text(FIXED_CHECKOUT, encoding="utf-8")
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "2", "--codex-used-percent", "20"]
        )
        == 0
    )
    _make_native_bundle_holder = _make_native_bundle(
        run_root, SESSION_ID, run_root / "project" / "fixture_project"
    )
    try:
        assert ctrl.main(["capture", "--root", str(run_root)]) == 0
    finally:
        _make_native_bundle_holder.close()
    state = json.loads((run_root / "controller-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "captured"
    assert state["score_eligible"] is False
    assert state["capture"]["captured"] is True
    assert state["capture"]["session_id"] == SESSION_ID
    assert state["capture"]["supported"] is True
    assert state["capture"]["canonical_equal"] is True
    offline = run_root / "offline-bundle"
    assert {item.name for item in offline.iterdir()} == {
        "opencode.db",
        "opencode.db-wal",
        "opencode.db-shm",
    }
    decoded = json.loads((run_root / "decoded-current.json").read_text(encoding="utf-8"))
    assert decoded["session_id"] == SESSION_ID
    assert decoded["bundle"]["path"] == "bundle"
    assert state["capture"]["decoded_sources"] == ["capture", "offline-bundle"]
    # Native sources are readable again after the denied offline decode.
    assert (run_root / "opencode.db").stat().st_size > 0


def test_capture_decodes_captured_then_offline_with_live_denied(
    plan, run_root, monkeypatch
):
    _prepare_ok(run_root)
    fake = FakeRun(_turn_script(SESSION_ID, run_root / "controller-state.json"))
    monkeypatch.setattr(ctrl.subprocess, "run", fake)
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "1", "--codex-used-percent", "10"]
        )
        == 0
    )
    (run_root / "project" / "fixture_project" / "checkout.py").write_text(
        FIXED_CHECKOUT, encoding="utf-8"
    )
    assert (
        ctrl.main(
            ["submit", "--root", str(run_root), "--turn", "2", "--codex-used-percent", "20"]
        )
        == 0
    )
    holder = _make_native_bundle(
        run_root, SESSION_ID, run_root / "project" / "fixture_project"
    )
    real_decode = ctrl.decode_opencode_bundle
    calls: list[dict] = []
    live_db = run_root / "opencode.db"

    def spy(bundle, *, session_id=None):
        try:
            live_db.read_bytes()
            live_readable = True
        except OSError:
            live_readable = False
        calls.append(
            {
                "bundle": Path(bundle).resolve(),
                "session_id": session_id,
                "live_readable": live_readable,
            }
        )
        return real_decode(bundle, session_id=session_id)

    monkeypatch.setattr(ctrl, "decode_opencode_bundle", spy)
    try:
        assert ctrl.main(["capture", "--root", str(run_root)]) == 0
    finally:
        holder.close()
    # Exactly two decodes over two distinct directories: the captured bundle
    # first (live readable), then the separate offline copy (live denied).
    assert [call["bundle"] for call in calls] == [
        (run_root / "capture").resolve(),
        (run_root / "offline-bundle").resolve(),
    ]
    assert all(call["session_id"] == SESSION_ID for call in calls)
    assert calls[0]["live_readable"] is True
    assert calls[1]["live_readable"] is False
