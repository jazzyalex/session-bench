#!/usr/bin/env python3
"""Prepare and conditionally run a fresh two-turn OpenClaw survival capture.

Preparation and readiness checks make no model call. Execution fails closed if
the exact isolated configuration cannot resolve a usable OpenAI auth route.
The existing preflight-01 capture is never opened or changed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.workload_instance import instantiate_workload

MODEL = "openai/gpt-5.6-terra"
VERSION = "OpenClaw 2026.9.5 (ec9c1a1)"
HOST_BINARY = Path("/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs")
TEMPLATE = ROOT / "fixtures/scenarios/survival-v1/workload"
CAPTURE_ROOT = ROOT / "artifacts/v1-expanded-preparation/live-captures"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def inventory(root: Path) -> dict[str, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("inventory root is not an ordinary directory")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlink in isolated capture")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha(path.read_bytes())
    return result


def plugin_identity(path: Path) -> dict:
    path = path.resolve(strict=True)
    metadata = json.loads((path / "openclaw.plugin.json").read_bytes())
    package = json.loads((path / "package.json").read_bytes())
    if metadata.get("id") != "codex" or package.get("name") != "@openclaw/codex" or package.get("version") != "2026.9.5":
        raise ValueError("pinned Codex plugin identity/version differs")
    return {"path": str(path), "manifest_sha256": sha((path / "openclaw.plugin.json").read_bytes()),
            "package_sha256": sha((path / "package.json").read_bytes())}


def host_identity() -> str:
    executable = shutil.which("openclaw")
    if not executable or Path(executable).resolve() != HOST_BINARY.resolve():
        raise ValueError("OpenClaw PATH executable differs from pinned host")
    if subprocess.check_output([executable, "--version"], text=True).strip() != VERSION:
        raise ValueError("OpenClaw host version changed")
    return executable


def isolated_config(workspace: Path, plugin: Path) -> dict:
    return {"agents": {"defaults": {"workspace": str(workspace), "model": {"primary": MODEL, "fallbacks": []},
                                     "skipBootstrap": True}},
            "auth": {"profiles": {"openai:default": {"provider": "openai", "mode": "oauth"}}},
            "tools": {"allow": ["read", "write", "edit", "exec", "process"], "fs": {"workspaceOnly": True}},
            "plugins": {"allow": ["codex"], "load": {"paths": [str(plugin)]},
                        "entries": {"codex": {"enabled": True}}}}


def isolated_env(root: Path, canary: str) -> dict[str, str]:
    inherited = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "TERM") if key in os.environ}
    inherited.update(OPENCLAW_CONFIG_PATH=str(root / "config.json"), OPENCLAW_STATE_DIR=str(root / "state"),
                     OPENCLAW_WORKSPACE_DIR=str(root / "workspace"), SB_SURVIVAL_V1_RUN_CANARY=canary)
    return inherited


def readiness(root: Path, canary: str) -> dict:
    env = isolated_env(root, canary)
    validated = subprocess.run(["openclaw", "config", "validate", "--json"], cwd=root, env=env,
                               capture_output=True, text=True, timeout=30)
    try:
        validation = json.loads(validated.stdout)
    except json.JSONDecodeError:
        validation = {}
    checked = subprocess.run(["openclaw", "models", "status", "--json"], cwd=root, env=env,
                             capture_output=True, text=True, timeout=30)
    try:
        status = json.loads(checked.stdout)
    except json.JSONDecodeError:
        status = {}
    auth = status.get("auth", {}) if isinstance(status, dict) else {}
    routes = auth.get("runtimeAuthRoutes", []) if isinstance(auth, dict) else []
    openai = [row for row in routes if isinstance(row, dict) and row.get("provider") == "openai"]
    result = {"config_valid": validated.returncode == 0 and validation.get("valid") is True,
              "model": status.get("resolvedDefault"), "fallbacks": status.get("fallbacks"),
              "missing_providers": auth.get("missingProvidersInUse"),
              "openai_runtime_routes": [{"runtime": row.get("runtime"), "status": row.get("status"),
                                         "reason": row.get("runtimeReason")} for row in openai],
              "auth_profile_count": len(auth.get("oauth", {}).get("profiles", [])) if isinstance(auth.get("oauth"), dict) else 0,
              "live_probe_performed": False}
    result["ready"] = (result["config_valid"] and checked.returncode == 0 and result["model"] == MODEL
                       and result["fallbacks"] == [] and result["missing_providers"] == []
                       and any(row["runtime"] == "codex" and row["status"] == "usable"
                               for row in result["openai_runtime_routes"]))
    return result


def prepare(attempt_id: str, plugin_path: Path) -> dict:
    if not attempt_id.startswith("openclaw-") or "/" in attempt_id or ".." in attempt_id:
        raise ValueError("attempt id must be a fresh OpenClaw name")
    plugin = plugin_identity(plugin_path)
    if not HOST_BINARY.is_file():
        raise ValueError("OpenClaw host binary missing")
    host_identity()
    run = CAPTURE_ROOT / attempt_id
    run.mkdir(parents=True, exist_ok=False)
    isolated = run / "isolated"
    workspace = isolated / "workspace"
    workspace.mkdir(parents=True)
    (isolated / "state").mkdir()
    shutil.copytree(TEMPLATE / "fixture_project", workspace / "fixture_project")
    workload, _ = instantiate_workload(json.loads((TEMPLATE / "workload.json").read_bytes()), attempt_id)
    write_new(run / "workload_instance.json", workload)
    (run / "workload_template.json").write_bytes((TEMPLATE / "workload.json").read_bytes())
    write_new(isolated / "config.json", isolated_config(workspace, Path(plugin["path"])))
    for number, turn in enumerate(workload["turns"], 1):
        (isolated / f"r{number}.prompt.txt").write_text(turn["text"], encoding="utf-8")
    session_id = str(uuid.uuid4())
    argv = [["openclaw", "agent", "--local", "--session-id", session_id, "--json", "--model", MODEL,
             "--message-file", str(isolated / f"r{number}.prompt.txt"), "--timeout", "120"] for number in (1, 2)]
    plan = {"schema_version": "session-bench-openclaw-two-turn-plan-v1", "attempt_id": attempt_id,
            "configuration_id": "openclaw", "status": "prepared", "session_id": session_id,
            "version": VERSION, "model": MODEL, "plugin": plugin,
            "host_binary_sha256": sha(HOST_BINARY.read_bytes()),
            "controller_sha256": sha(Path(__file__).read_bytes()), "argv": argv,
            "environment": isolated_env(isolated, workload["run_canary"]),
            "workspace_before": inventory(workspace), "native_state_before": inventory(isolated / "state"),
            "personal_session_history_opened": False, "credential_values_retained": False,
            "live_model_calls": 0}
    write_new(run / "plan.json", plan)
    checked = readiness(isolated, workload["run_canary"])
    write_new(run / "readiness.json", checked)
    return {"attempt_id": attempt_id, "plan": str(run / "plan.json"), "ready": checked["ready"],
            "readiness": checked, "live_model_calls": 0}


def _turn(argv: list[str], *, root: Path, env: dict, stdout: Path, stderr: Path) -> tuple[int, dict]:
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(argv, cwd=root / "workspace", env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            code = process.wait(timeout=150)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    try:
        result = json.loads(stdout.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError):
        result = {}
    return code, result


def _helpers(workspace: Path, canary: str) -> list[dict]:
    path = workspace / "fixture_project/.survival-observer.jsonl"
    return [row for line in path.read_bytes().splitlines() if line and (row := json.loads(line)).get("run_canary") == canary]


def execute(attempt_id: str) -> dict:
    run = CAPTURE_ROOT / attempt_id
    plan = json.loads((run / "plan.json").read_bytes())
    workload = json.loads((run / "workload_instance.json").read_bytes())
    isolated = run / "isolated"
    if plan.get("status") != "prepared" or plan.get("controller_sha256") != sha(Path(__file__).read_bytes()):
        raise ValueError("prepared OpenClaw plan/source changed")
    host_identity()
    try:
        parsed_session = uuid.UUID(plan["session_id"])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("prepared OpenClaw session id invalid") from error
    if parsed_session.version != 4:
        raise ValueError("prepared OpenClaw session id is not fresh UUIDv4")
    plugin = plugin_identity(Path(plan["plugin"]["path"]))
    if plugin != plan["plugin"] or plan.get("host_binary_sha256") != sha(HOST_BINARY.read_bytes()):
        raise ValueError("OpenClaw host/plugin changed after preparation")
    if json.loads((isolated / "config.json").read_bytes()) != isolated_config(isolated / "workspace", Path(plugin["path"])):
        raise ValueError("isolated OpenClaw configuration changed")
    expected_argv = [["openclaw", "agent", "--local", "--session-id", plan["session_id"], "--json", "--model", MODEL,
                      "--message-file", str(isolated / f"r{number}.prompt.txt"), "--timeout", "120"] for number in (1, 2)]
    if plan.get("argv") != expected_argv or plan.get("model") != MODEL or plan.get("version") != VERSION:
        raise ValueError("OpenClaw launch plan changed")
    for number, turn in enumerate(workload["turns"], 1):
        if (isolated / f"r{number}.prompt.txt").read_text(encoding="utf-8") != turn["text"]:
            raise ValueError("OpenClaw workload prompt changed")
    if plan.get("workspace_before") != inventory(isolated / "workspace") or plan.get("native_state_before") != {}:
        raise ValueError("prepared OpenClaw workspace/native state changed")
    if (isolated / "state/agents/main/sessions").exists():
        raise ValueError("prepared OpenClaw session root is not fresh")
    if plan.get("environment") != isolated_env(isolated, workload["run_canary"]):
        raise ValueError("OpenClaw environment changed")
    if (run / "attempt.json").exists():
        raise ValueError("OpenClaw attempt already executed")
    checked = readiness(isolated, workload["run_canary"])
    if not checked["ready"]:
        write_new(run / "attempt.json", {"attempt_id": attempt_id, "status": "blocked_before_model_call",
                                         "reason": "isolated_openai_oauth_route_unavailable", "readiness": checked,
                                         "live_model_calls": 0})
        return {"attempt_id": attempt_id, "status": "blocked_before_model_call", "live_model_calls": 0}
    before = inventory(isolated / "workspace")
    record = {"attempt_id": attempt_id, "status": "in_progress", "started_at": datetime.now(timezone.utc).isoformat(),
              "session_id": plan["session_id"], "model": MODEL, "controller_sha256": plan["controller_sha256"],
              "live_model_calls": 0, "credential_values_retained": False, "personal_session_history_opened": False}
    try:
        for number in (1, 2):
            out = run / f"capture/r{number}.stdout.json"
            err = run / f"capture/r{number}.stderr.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            code, result = _turn(plan["argv"][number - 1], root=isolated,
                                 env=isolated_env(isolated, workload["run_canary"]), stdout=out, stderr=err)
            record["live_model_calls"] += 1
            record[f"r{number}_exit_code"] = code
            if (code != 0 or result.get("ok") is not True or result.get("sessionId") != plan["session_id"]
                    or not isinstance(result.get("final"), str)
                    or not result["final"].rstrip().endswith(workload["turns"][number - 1]["response_canary"])):
                raise ValueError(f"R{number} failed exact result/session/canary gate")
            helpers = _helpers(isolated / "workspace", workload["run_canary"])
            phases = [row.get("phase") for row in helpers]
            if phases != (["inspect", "baseline"] if number == 1 else ["inspect", "baseline", "final"]):
                raise ValueError(f"R{number} helper phase gate failed")
            if [row.get("exit_code") for row in helpers] != ([0, 1] if number == 1 else [0, 1, 0]):
                raise ValueError(f"R{number} helper result gate failed")
            after = inventory(isolated / "workspace")
            changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
            allowed = {"fixture_project/.survival-observer.jsonl"} if number == 1 else {"fixture_project/.survival-observer.jsonl", "fixture_project/checkout.py"}
            if not changed <= allowed or (number == 2 and "fixture_project/checkout.py" not in changed):
                raise ValueError(f"R{number} workspace change gate failed")
        source = isolated / "state/agents/main/sessions"
        first = inventory(source)
        time.sleep(0.5)
        if first != inventory(source):
            raise ValueError("OpenClaw selected native session root did not quiesce")
        selected = [name for name in first if plan["session_id"] in name and name.endswith(".jsonl")]
        if len(selected) != 1:
            raise ValueError("OpenClaw native session singleton not found")
        shutil.copytree(source, run / "native/sessions")
        if inventory(run / "native/sessions") != first or inventory(source) != first:
            raise ValueError("OpenClaw native source/copy mismatch")
        record.update(status="captured_pending_qualification", native_files=first,
                      workspace_before=before, workspace_after=inventory(isolated / "workspace"))
    except Exception as error:
        record.update(status="blocked", reason=str(error))
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_new(run / "attempt.json", record)
    return {"attempt_id": attempt_id, "status": record["status"], "live_model_calls": record["live_model_calls"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-id", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--execute", action="store_true")
    parser.add_argument("--codex-plugin-path", type=Path)
    args = parser.parse_args()
    if args.prepare:
        if args.codex_plugin_path is None:
            parser.error("--prepare requires --codex-plugin-path")
        result = prepare(args.attempt_id, args.codex_plugin_path)
    else:
        result = execute(args.attempt_id)
    print(json.dumps(result, sort_keys=True))
