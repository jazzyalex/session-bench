"""Shared-owner preparation must remain offline and never read credential rows."""
import builtins
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from session_bench.openclaw_shared_owner_plan import build_shared_owner_plan, MODEL


def arguments(tmp_path):
    return dict(shared_owner_opt_in=True, owner_state=str(tmp_path / "owner"),
                agent_id="sb-test", agent_dir=str(tmp_path / "benchmark/agent"),
                workspace=str(tmp_path / "benchmark/workspace"),
                session_key="agent:sb-test:sb-run", session_id="14b7f34a-23c3-4e85-a6ce-9d349fd71a11",
                config_path=str(tmp_path / "benchmark/config.json"),
                plugin_path="/explicit/plugin", prompt_paths=(str(tmp_path / "r1.txt"), str(tmp_path / "r2.txt")))


def test_plan_same_session_explicit_owner_and_no_capture(tmp_path):
    args = arguments(tmp_path)
    plan = build_shared_owner_plan(**args)
    assert plan["environment_overrides"]["OPENCLAW_STATE_DIR"] == args["owner_state"]
    assert plan["config"]["agents"]["entries"]["sb-test"]["agentDir"] == args["agent_dir"]
    assert plan["config"]["auth"]["profiles"]["openai:default"] == {"provider": "openai", "mode": "oauth"}
    for argv in plan["argv"]:
        assert argv[:3] == ["openclaw", "agent", "--local"]
        for flag, value in (("--agent", "sb-test"), ("--session-id", args["session_id"]),
                            ("--session-key", args["session_key"]), ("--model", MODEL)):
            assert argv[argv.index(flag) + 1] == value
    assert plan["capture"]["available"] is False
    assert plan["live_model_calls"] == 0


def test_synthetic_auth_and_sibling_rows_never_read_or_exported(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    owner = Path(args["owner_state"])
    owner.mkdir()
    db_path = owner / "openclaw.sqlite"
    with sqlite3.connect(db_path) as database:
        database.execute("CREATE TABLE auth_profile_store(secret TEXT)")
        database.execute("INSERT INTO auth_profile_store VALUES ('AUTH_CANARY_REFRESH')")
        database.execute("CREATE TABLE transcript_events(session_id TEXT, event_json TEXT)")
        database.execute("INSERT INTO transcript_events VALUES ('personal', 'SIBLING_TRANSCRIPT_CANARY')")
    before = db_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("offline planner attempted filesystem, database, or process access")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(Path, "open", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        patch.setattr(subprocess, "run", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        exported_plan = json.dumps(build_shared_owner_plan(**args))
    assert "AUTH_CANARY_REFRESH" not in exported_plan
    assert "SIBLING_TRANSCRIPT_CANARY" not in exported_plan
    assert db_path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"shared_owner_opt_in": False}, {"agent_id": "main"},
    {"session_key": "agent:main:sb-run"}, {"session_key": "agent:sb-test:main"},
    {"session_id": "bad"}, {"owner_state": "relative"},
    {"agent_dir": "/tmp/../owner"},
])
def test_reject_ambiguous_or_non_benchmark_inputs(tmp_path, change):
    args = arguments(tmp_path)
    args.update(change)
    with pytest.raises(ValueError):
        build_shared_owner_plan(**args)


def test_reject_owner_alias_and_agent_workspace_overlap(tmp_path):
    args = arguments(tmp_path)
    with pytest.raises(ValueError):
        build_shared_owner_plan(**{**args, "agent_dir": args["owner_state"] + "/agents/test"})
    with pytest.raises(ValueError):
        build_shared_owner_plan(**{**args, "workspace": args["agent_dir"] + "/work"})
