"""Offline, opt-in OpenClaw 2026.9.5 shared-owner launch planning.

This module never opens a path, invokes OpenClaw, or imports its runtime. A plan
does not establish authentication, directory freshness, or permission to launch.
The upstream snapshot API is internal; capture remains blocked until a reviewed
installed API bridge can read the exact new agent/session without bootstrap.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re
import uuid

VERSION = "OpenClaw 2026.9.5 (ec9c1a1)"
MODEL = "openai/gpt-5.6-terra"
UPSTREAM = "https://github.com/openclaw/openclaw/blob/v2026.9.5/"


def _path(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{label} must be an explicit absolute path")
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or path == PurePosixPath("/"):
        raise ValueError(f"{label} must be an explicit absolute non-root path without traversal")
    return path


def _overlap(first: PurePosixPath, second: PurePosixPath) -> bool:
    return first == second or first in second.parents or second in first.parents


def build_shared_owner_plan(*, shared_owner_opt_in: bool, owner_state: str,
                            agent_id: str, agent_dir: str, workspace: str,
                            session_key: str, session_id: str, config_path: str,
                            plugin_path: str, prompt_paths: tuple[str, str]) -> dict:
    """Return data only; lexical checks deliberately never inspect owner state.

    The caller must supply new benchmark directories outside the owner state.
    Neither freshness nor symlink containment can be proven offline. The config
    is a separate benchmark file, never the owner's configuration.
    """
    if shared_owner_opt_in is not True:
        raise ValueError("shared-owner planning requires explicit opt-in")
    if not isinstance(agent_id, str) or not re.fullmatch(r"sb-[a-z0-9][a-z0-9-]{0,60}", agent_id):
        raise ValueError("fresh benchmark agent id must begin with sb-")
    expected_prefix = f"agent:{agent_id}:"
    if not isinstance(session_key, str) or not session_key.startswith(expected_prefix):
        raise ValueError("session key must belong to the explicit benchmark agent")
    suffix = session_key[len(expected_prefix):]
    if not re.fullmatch(r"sb-[a-z0-9][a-z0-9-]{0,60}", suffix):
        raise ValueError("session key must name a fresh sb- benchmark run")
    try:
        parsed = uuid.UUID(session_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("session id must be canonical UUIDv4") from error
    if parsed.version != 4 or str(parsed) != session_id:
        raise ValueError("session id must be canonical UUIDv4")
    owner = _path(owner_state, "owner state")
    agent = _path(agent_dir, "agent directory")
    work = _path(workspace, "workspace")
    config = _path(config_path, "benchmark config")
    plugin = _path(plugin_path, "plugin")
    if _overlap(owner, agent) or _overlap(owner, work) or _overlap(owner, config):
        raise ValueError("benchmark paths must be outside the owner state root")
    if _overlap(agent, work) or _overlap(agent, config) or config == work:
        raise ValueError("agent state must be separate from workspace and config")
    if len(prompt_paths) != 2:
        raise ValueError("exactly two prompt paths are required")
    prompts = [_path(item, "prompt") for item in prompt_paths]
    if prompts[0] == prompts[1] or any(_overlap(owner, item) or _overlap(agent, item) for item in prompts):
        raise ValueError("distinct benchmark prompts must be outside owner and agent state")
    configuration = {
        "agents": {"ownership": "explicit", "defaults": {"skipBootstrap": True},
                   "entries": {agent_id: {"default": True, "workspace": str(work),
                                          "agentDir": str(agent),
                                          "model": {"primary": MODEL, "fallbacks": []}}}},
        "auth": {"profiles": {"openai:default": {"provider": "openai", "mode": "oauth"}},
                 "order": {"openai": ["openai:default"]}},
        "tools": {"allow": ["read", "write", "edit", "exec", "process"],
                  "fs": {"workspaceOnly": True}, "agentToAgent": {"enabled": False},
                  "sessions": {"visibility": "self"}},
        "plugins": {"allow": ["codex"], "load": {"paths": [str(plugin)]},
                    "entries": {"codex": {"enabled": True}}},
    }
    argv = [["openclaw", "agent", "--local", "--agent", agent_id,
             "--session-key", session_key, "--session-id", session_id,
             "--model", MODEL, "--message-file", str(prompt), "--timeout", "120", "--json"]
            for prompt in prompts]
    return {
        "schema_version": "session-bench-openclaw-shared-owner-offline-plan-v1",
        "status": "offline_plan_capture_blocked", "expected_version": VERSION,
        "model": MODEL, "agent_id": agent_id, "session_key": session_key,
        "session_id": session_id, "config": configuration, "argv": argv,
        "environment_overrides": {"OPENCLAW_STATE_DIR": str(owner),
                                  "OPENCLAW_CONFIG_PATH": str(config),
                                  "OPENCLAW_WORKSPACE_DIR": str(work)},
        "live_model_calls": 0, "owner_state_opened": False,
        "capture": {"available": False, "reason": "installed_snapshot_bridge_not_verified",
                    "upstream_api": "readTranscriptExportSnapshotReadOnlySync",
                    "agent_database": str(agent / "openclaw-agent.sqlite"),
                    "required_scope": {"agentId": agent_id, "sessionKey": session_key,
                                       "sessionId": session_id},
                    "source": UPSTREAM + "src/config/sessions/session-accessor.sqlite-read.ts#L96"},
        "prerequisites": [
            "Owner confirms openai:default is usable in the relocated shared state store, not a private personal account or local override.",
            "Owner provides exclusive state-root ownership; Desktop/Gateway and other embedded writers are stopped.",
            "Verify pinned host/plugin identity and validate the separate config without a model probe under separately authorized access.",
            "Verify benchmark paths are fresh ordinary directories with no symlink alias into owner state.",
            "Provide a reviewed installed snapshot bridge with no runtime bootstrap or broad discovery; export only the exact new session.",
        ],
    }
