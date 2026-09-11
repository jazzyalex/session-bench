import json
from pathlib import Path

import pytest

from session_bench.l0_controller import (
    HardCounters, L0Controller, PTYObserver, build_codex_decode_package, dry_run, explicit_decode_package,
    exact_argv, privacy_scan,
)
from session_bench.l0_preflight import QuotaSnapshot, build_effective_config, stat_inventory


ROOT = Path(__file__).resolve().parents[1]


def plan():
    return json.loads((ROOT / "docs/live/codex-cli-f0-run-plan.json").read_text())


class FakeRunner:
    def enumerate_mcp_names(self, argv):
        return {"safe": False} if argv else {"safe": True}

    def resolve_features(self, argv):
        return {name: name == "skip_host_skill_discovery" for name in (
            "apps", "browser_use", "browser_use_external", "computer_use", "hooks",
            "image_generation", "in_app_browser", "memories", "multi_agent", "plugins",
            "skill_search", "tool_suggest", "workspace_dependencies", "skip_host_skill_discovery")}

    def sandbox_probe(self, argv, scratch, sibling):
        return {"scratch_write_allowed": True, "undeclared_sibling_write_denied": True, "outbound_network_denied": True}


def quota():
    p = plan()["limits"]["quota"]
    return QuotaSnapshot(p["source"], "2026-09-11T00:00:00Z", 10, p["baseline_used_percent"], 1, 1)


def test_dry_run_and_preflight_have_exact_argv(tmp_path):
    p = plan()
    preview = dry_run(p, ["safe"], tmp_path)
    assert preview["argv"][0] == "/opt/homebrew/bin/codex"
    assert "-c" in preview["argv"]
    controller = L0Controller(p, FakeRunner())
    result = controller.preflight(tmp_path, tmp_path.parent / "sibling", quota=quota(), now_monotonic=2)
    assert tuple(preview["argv"]) == result["argv"]
    assert result["mcp_names"] == ("safe",)


def test_preflight_stops_when_sandbox_probe_fails(tmp_path):
    class Bad(FakeRunner):
        def sandbox_probe(self, *args):
            return {"scratch_write_allowed": True, "undeclared_sibling_write_denied": False, "outbound_network_denied": True}
    with pytest.raises(RuntimeError, match="sandbox"):
        L0Controller(plan(), Bad()).preflight(tmp_path, tmp_path.parent / "sibling", quota=quota(), now_monotonic=2)


def test_preflight_checks_quota_before_any_codex_command(tmp_path):
    class MustNotRun(FakeRunner):
        def enumerate_mcp_names(self, argv):
            raise AssertionError("Codex command ran before quota gate")
    p = plan()
    contract = p["limits"]["quota"]
    over = QuotaSnapshot(contract["source"], "2026-09-11T00:00:00Z", 15,
                         contract["baseline_used_percent"], 1, 1)
    with pytest.raises(RuntimeError, match="quota-threshold"):
        L0Controller(p, MustNotRun()).preflight(tmp_path, tmp_path.parent / "sibling", quota=over, now_monotonic=2)


def test_observer_freezes_and_rejects_late_events():
    observer = PTYObserver()
    observer.record("prompt", "hello")
    assert len(observer.freeze()) == 1
    with pytest.raises(RuntimeError):
        observer.record("visible", "done")


def test_explicit_decode_package_and_privacy_scan(tmp_path):
    source = tmp_path / "native"; source.mkdir()
    (source / "rollout.jsonl").write_text("safe")
    out = tmp_path / "copy"
    assert explicit_decode_package(source, out, ["rollout.jsonl"]) == ("rollout.jsonl",)
    assert privacy_scan(out) == ()
    (source / "secret").write_bytes(b"BEGIN PRIVATE KEY")
    out2 = tmp_path / "copy2"
    explicit_decode_package(source, out2, ["secret"])
    assert privacy_scan(out2)


def test_complete_codex_decode_package_is_integrity_bound(tmp_path):
    source = tmp_path / "store" / "rollout-new.jsonl"
    source.parent.mkdir()
    source.write_text('{"type":"session_meta","payload":{"id":"s"}}\n')
    package = build_codex_decode_package(source, tmp_path / "package")
    decode = json.loads((package / "decode.json").read_text())
    assert decode["format"] == "codex-rollout-v1"
    assert decode["artifacts"][0]["path"] == "rollout-new.jsonl"


def test_capture_requires_frozen_observer_and_single_new_candidate(tmp_path):
    root = tmp_path / "store"; root.mkdir()
    (root / "rollout-old.jsonl").write_text("old")
    before = stat_inventory(root)
    (root / "rollout-new.jsonl").write_text("new")
    after = stat_inventory(root)
    controller = L0Controller(plan(), FakeRunner())
    controller.observer.record("prompt", "x")
    with pytest.raises(ValueError, match="observer"):
        controller.capture_evidence(before, after, opened=["rollout-new.jsonl"])
    controller.observer.freeze()
    evidence = controller.capture_evidence(before, after, opened=["rollout-new.jsonl"])
    assert evidence["candidate_paths"] == ["rollout-new.jsonl"]


def test_hard_counters_stop_at_plan_limits():
    counters = HardCounters(attempts=4)
    with pytest.raises(RuntimeError, match="attempt"):
        counters.check(plan())
    counters = HardCounters()
    counters.reserve(plan(), attempts=1, submitted_turns=3, native_sessions=1)
    assert (counters.attempts, counters.submitted_turns, counters.native_sessions) == (1, 3, 1)
    with pytest.raises(RuntimeError, match="spend"):
        counters.reserve(plan(), spend_usd=0.01)
