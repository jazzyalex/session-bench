"""OpenClaw setup stays isolated and cannot call a model without auth readiness."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openclaw_survival", ROOT / "scripts/run_openclaw_survival.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def test_minimal_config_has_explicit_model_tools_and_no_channels(tmp_path):
    workspace = tmp_path / "workspace"
    config = controller.isolated_config(workspace, tmp_path / "plugin")
    assert config["agents"]["defaults"]["workspace"] == str(workspace)
    assert config["agents"]["defaults"]["model"] == {"primary": controller.MODEL, "fallbacks": []}
    assert config["agents"]["defaults"]["skipBootstrap"] is True
    assert config["tools"] == {"allow": ["read", "write", "edit", "exec", "process"],
                               "fs": {"workspaceOnly": True}}
    assert config["plugins"]["allow"] == ["codex"]
    assert not {"channels", "hooks", "gateway", "memory", "env"} & set(config)


def test_unavailable_isolated_auth_blocks_before_any_model_call(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "openclaw.plugin.json").write_text('{"id":"codex"}')
    (plugin / "package.json").write_text('{"name":"@openclaw/codex","version":"2026.9.5"}')
    host = tmp_path / "openclaw.mjs"
    host.write_text("host")
    monkeypatch.setattr(controller, "HOST_BINARY", host)
    monkeypatch.setattr(controller, "CAPTURE_ROOT", tmp_path / "captures")
    (tmp_path / "captures").mkdir()
    monkeypatch.setattr(controller, "host_identity", lambda: str(host))
    blocked = {"config_valid": True, "model": controller.MODEL, "fallbacks": [],
               "missing_providers": ["openai"], "openai_runtime_routes": [{"runtime": "codex", "status": "missing"}],
               "auth_profile_count": 0, "live_probe_performed": False, "ready": False}
    monkeypatch.setattr(controller, "readiness", lambda *args: blocked)
    monkeypatch.setattr(controller, "_turn", lambda *args, **kwargs: pytest.fail("model call escaped readiness gate"))

    prepared = controller.prepare("openclaw-controller-test-02", plugin)
    assert prepared["live_model_calls"] == 0 and prepared["ready"] is False
    plan = json.loads((tmp_path / "captures/openclaw-controller-test-02/plan.json").read_bytes())
    assert len(plan["argv"]) == 2
    assert plan["argv"][0][plan["argv"][0].index("--session-id") + 1] == plan["session_id"]
    assert plan["argv"][1][plan["argv"][1].index("--session-id") + 1] == plan["session_id"]
    result = controller.execute("openclaw-controller-test-02")
    assert result == {"attempt_id": "openclaw-controller-test-02", "status": "blocked_before_model_call", "live_model_calls": 0}
    assert not (tmp_path / "captures/openclaw-controller-test-02/capture").exists()
    assert not (tmp_path / "captures/openclaw-controller-test-02/native").exists()
