from pathlib import Path
import builtins
import json
import os

import pytest

from session_bench.l0_preflight import (
    QuotaSnapshot, build_effective_config, identify_single_new_candidate,
    quota_decision, stat_inventory, verify_resolved_config,
)
from session_bench.l0_scenarios import c01_inputs, c02_inputs


DISABLED = (
    "apps", "browser_use", "browser_use_external", "computer_use", "hooks",
    "image_generation", "in_app_browser", "memories", "multi_agent", "plugins",
    "skill_search", "tool_suggest", "workspace_dependencies",
)
ROOT = Path(__file__).resolve().parents[1]


def test_effective_config_is_exact_deterministic_argv_and_model_unset():
    a = build_effective_config(disable_mcps=["zeta-one", "alpha", "alpha"])
    b = build_effective_config(disable_mcps=["alpha", "zeta-one"])
    assert a == b
    assert a.model is None
    assert a.argv[:4] == ("-c", 'web_search="disabled"', "-c", "sandbox_workspace_write.network_access=false")
    assert a.argv[-4:] == ("-c", "mcp_servers.alpha.enabled=false", "-c", "mcp_servers.zeta-one.enabled=false")
    assert "--mcp-disabled=alpha" not in a.argv


@pytest.mark.parametrize("name", ["ok;touch-pwned", "dot.name", "space name", "", "a/b"])
def test_mcp_encoding_rejects_unrepresentable_names(name):
    with pytest.raises(ValueError):
        build_effective_config(disable_mcps=[name])


def test_resolved_config_requires_exact_feature_and_mcp_population():
    config = build_effective_config(disable_mcps=["shell"])
    features = {name: False for name in DISABLED}
    features["skip_host_skill_discovery"] = True
    identity = verify_resolved_config(config, features=features, mcps={"shell": False})
    assert len(identity) == 64
    with pytest.raises(ValueError):
        verify_resolved_config(config, features={**features, "apps": True}, mcps={"shell": False})
    with pytest.raises(ValueError):
        verify_resolved_config(config, features=features, mcps={"shell": False, "surprise": False})


def test_rollout_inventory_never_opens_or_hashes_old_files(tmp_path: Path, monkeypatch):
    old = tmp_path / "2026" / "09" / "10" / "rollout-old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"private-content")
    os.symlink(old, old.parent / "rollout-link.jsonl")
    monkeypatch.setattr(builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("opened content")))
    before = stat_inventory(tmp_path)
    monkeypatch.undo()
    assert [item.relative_path for item in before] == ["2026/09/10/rollout-old.jsonl"]
    new = old.parent / "rollout-new.jsonl"
    new.write_bytes(b"public-synthetic")
    after = stat_inventory(tmp_path)
    assert identify_single_new_candidate(before, after).relative_path.endswith("rollout-new.jsonl")
    other = old.parent / "rollout-other.jsonl"
    other.write_bytes(b"public-synthetic-2")
    with pytest.raises(ValueError):
        identify_single_new_candidate(before, stat_inventory(tmp_path))


def test_quota_policy_matches_machine_plan():
    plan = json.loads((ROOT / "docs/live/codex-cli-f0-run-plan.json").read_text())
    contract = plan["limits"]["quota"]
    missing = QuotaSnapshot(None, None, None, None, None, None)
    assert quota_decision(missing, plan=plan, now_monotonic=1) == "stop-quota-unreadable"
    assert quota_decision(missing, plan=plan, now_monotonic=1, live_attempt_started=True) == "finish-current-only"
    good = QuotaSnapshot(contract["source"], "2026-09-11T00:01:00Z", 12, 10, 1, 2)
    assert quota_decision(good, plan=plan, now_monotonic=3) == "proceed"
    at_cap = QuotaSnapshot(contract["source"], "2026-09-11T00:01:00Z", 13, 10, 1, 2)
    assert quota_decision(at_cap, plan=plan, now_monotonic=3) == "stop-quota-threshold"
    assert quota_decision(good, plan=plan, now_monotonic=6000) == "stop-wall-clock"


def test_c01_and_c02_inputs_are_frozen():
    c01 = c01_inputs("abc")
    assert len(c01) == 3 and c01[1].observation_id == "c01-submit-2"
    assert "café_🙂" in c01[0].value
    c02 = c02_inputs()
    assert [item.kind for item in c02] == ["inspect", "test", "edit", "test"]
    assert [item.observation_id for item in c02] == ["c02-inspect", "c02-test-fail", "c02-edit", "c02-test-pass"]
