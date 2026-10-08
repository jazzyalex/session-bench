"""Offline tests for the bounded Kimi capture's route and stop conditions."""
from pathlib import Path
import tomllib

import pytest

from session_bench.kimi_survival_capture import _config, _final_stdout, _session_family, _turn_argv, KimiCaptureError


def config(tmp_path, *, host="https://api.moonshot.ai/v1", key="synthetic-secret-do-not-copy"):
    path = tmp_path / "config.toml"
    path.write_text(f'''default_model = "moonshot-ai/kimi-k2.7-code"
[thinking]
enabled = true
[providers.moonshot-ai]
type = "kimi"
base_url = "{host}"
api_key = "{key}"
[models."moonshot-ai/kimi-k2.7-code"]
provider = "moonshot-ai"
model = "kimi-k2.7-code"
max_context_size = 262144
capabilities = ["thinking", "image_in", "video_in", "tool_use"]
''')
    return path


def test_sanitized_config_uses_existing_route_without_persisting_key(tmp_path):
    secret, safe = _config(config(tmp_path))
    parsed = tomllib.loads(safe.decode())
    assert secret == "synthetic-secret-do-not-copy"
    assert secret.encode() not in safe
    assert parsed["providers"]["moonshot-ai"] == {
        "type": "kimi", "base_url": "https://api.moonshot.ai/v1", "api_key_env": "SB_KIMI_MOONSHOT_API_KEY"}
    # The harness keeps its own default retry on a provider rate limit.
    assert "loop_control" not in parsed


def test_different_endpoint_fails_before_any_run(tmp_path):
    with pytest.raises(KimiCaptureError, match="fixed Moonshot API cohort"):
        _config(config(tmp_path, host="https://other.example/v1"))


def test_new_session_boundary_requires_exact_one_owned_wire(tmp_path):
    home = tmp_path / "kimi"
    first = home / "sessions/wd_fixture_project/session_7a3ea483-c247-47f1-8fd6-42838835321c/agents/main/wire.jsonl"
    first.parent.mkdir(parents=True)
    first.write_text("{}\n")
    session, family = _session_family(home)
    assert session == "session_7a3ea483-c247-47f1-8fd6-42838835321c"
    assert family.name == session
    second = home / "sessions/wd_fixture_project/session_8a3ea483-c247-47f1-8fd6-42838835321c/agents/main/wire.jsonl"
    second.parent.mkdir(parents=True)
    second.write_text("{}\n")
    with pytest.raises(KimiCaptureError, match="ambiguous"):
        _session_family(home)


def test_final_response_requires_assistant_content_and_matching_session_hint():
    session = "session_7a3ea483-c247-47f1-8fd6-42838835321c"
    def stream(*rows):
        import json
        return b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
    hint = {"role": "meta", "type": "session.resume_hint", "session_id": session}
    echoed = stream({"role": "tool", "content": "exact canary"}, hint)
    with pytest.raises(KimiCaptureError, match="final assistant"):
        _final_stdout(echoed, "exact canary", session, require_hint=True)
    valid = stream({"role": "tool", "content": "exact canary"},
                   {"role": "assistant", "content": "Final answer\nexact canary"}, hint)
    assert _final_stdout(valid, "exact canary", session, require_hint=True).startswith("Final answer")
    with pytest.raises(KimiCaptureError, match="native session ID"):
        _final_stdout(valid, "exact canary", "session_other", require_hint=True)


def test_print_mode_argv_avoids_installed_cli_permission_conflicts():
    plan = {"kimi_executable": "/installed/kimi", "skills_dir": "/owned/empty-skills"}
    first = _turn_argv(plan, "synthetic prompt", None)
    second = _turn_argv(plan, "synthetic followup", "session_7a3ea483-c247-47f1-8fd6-42838835321c")
    assert first == ["/installed/kimi", "-p", "synthetic prompt", "--output-format", "stream-json",
                     "-m", "moonshot-ai/kimi-k2.7-code", "--skills-dir", "/owned/empty-skills"]
    assert second[-2:] == ["-S", "session_7a3ea483-c247-47f1-8fd6-42838835321c"]
    assert not {"-y", "--yolo", "--auto", "--plan"}.intersection(first + second)
