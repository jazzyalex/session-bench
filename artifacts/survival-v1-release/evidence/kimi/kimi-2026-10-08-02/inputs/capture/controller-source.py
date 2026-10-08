"""Bounded Kimi Code survival capture using the existing Moonshot API route.

The saved config contains an environment-variable reference, never the API
key. Only a new, isolated KIMI_CODE_HOME and its one created session are read.
Preparation is offline; execution makes at most one CLI invocation per turn.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib
from typing import Mapping

from .workload_instance import instantiate_workload


SCHEMA = "session-bench-kimi-survival-capture-v1"
ALIAS = "moonshot-ai/kimi-k2.7-code"
PROVIDER = "moonshot-ai"
HOST = "api.moonshot.ai"
KEY_ENV = "SB_KIMI_MOONSHOT_API_KEY"
_FIXTURE = {"checkout.py", "bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py"}
_STOP = re.compile(r"\b(?:401|403|429)\b|provider\.rate_limit|APIProviderRateLimitError|unauthori[sz]ed|invalid api key|quota exhausted|insufficient.*(?:credit|balance)", re.I)


class KimiCaptureError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def _json(path: Path, value) -> None:
    _write_new(path, (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode())


def _inventory(root: Path) -> dict[str, dict]:
    if root.is_symlink() or not root.is_dir():
        raise KimiCaptureError("owned root is absent or a symlink")
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise KimiCaptureError("symlink inside owned evidence root")
        if path.is_dir():
            continue
        if not path.is_file():
            raise KimiCaptureError("special file inside owned evidence root")
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        found[relative] = {"sha256": _sha(raw), "size_bytes": len(raw)}
        if len(found) > 2000 or sum(row["size_bytes"] for row in found.values()) > 128 * 1024 * 1024:
            raise KimiCaptureError("owned evidence root exceeded capture bounds")
    return found


def _config(source: Path) -> tuple[str, bytes]:
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 65536:
        raise KimiCaptureError("existing Kimi config unavailable or unsafe")
    value = tomllib.loads(source.read_text())
    provider = value.get("providers", {}).get(PROVIDER, {})
    model = value.get("models", {}).get(ALIAS, {})
    secret = provider.get("api_key")
    if (value.get("default_model") != ALIAS or provider.get("type") != "kimi"
            or provider.get("base_url") != "https://api.moonshot.ai/v1"
            or not isinstance(secret, str) or not secret.strip()
            or "api_key_env" in provider or "oauth" in provider
            or model.get("provider") != PROVIDER or model.get("model") != "kimi-k2.7-code"
            or type(model.get("max_context_size")) is not int
            or model["max_context_size"] < 1
            or not isinstance(model.get("capabilities"), list)
            or any(not isinstance(item, str) for item in model["capabilities"])):
        raise KimiCaptureError("existing config differs from fixed Moonshot API cohort")
    # JSON string/array syntax is a valid TOML subset for these scalar values.
    safe = (
        f"default_model = {json.dumps(ALIAS)}\n"
        f"[thinking]\nenabled = {'true' if value.get('thinking', {}).get('enabled') is True else 'false'}\n"
        # No [loop_control] override: one attempt per step turned every provider
        # rate limit (3 requests a minute on this account) into a failed turn.
        f"[providers.{PROVIDER}]\ntype = \"kimi\"\nbase_url = \"https://api.moonshot.ai/v1\"\n"
        f"api_key_env = {json.dumps(KEY_ENV)}\n"
        f"[models.{json.dumps(ALIAS)}]\nprovider = {json.dumps(PROVIDER)}\n"
        f"model = \"kimi-k2.7-code\"\nmax_context_size = {model['max_context_size']}\n"
        f"capabilities = {json.dumps(model['capabilities'])}\n"
    ).encode()
    if secret.encode() in safe:
        raise KimiCaptureError("credential appeared in sanitized config")
    parsed = tomllib.loads(safe.decode())
    if parsed["providers"][PROVIDER].get("api_key_env") != KEY_ENV:
        raise KimiCaptureError("sanitized config failed TOML self-check")
    return secret, safe


def prepare(destination: Path, *, repository: Path, executable: Path, config: Path) -> dict:
    repository = repository.resolve()
    destination = destination.absolute()
    if destination.parent != repository / "artifacts/v1-expanded-preparation/live-captures":
        raise ValueError("new attempt must reside in repository live-captures")
    if not re.fullmatch(r"kimi-[a-z0-9-]+", destination.name):
        raise ValueError("invalid Kimi attempt ID")
    if destination.exists() or destination.is_symlink():
        raise ValueError("attempt path already exists")
    executable = executable.resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise KimiCaptureError("Kimi CLI executable unavailable")
    _, safe_config = _config(config)
    fixture_source = repository / "fixtures/scenarios/survival-v1/workload"
    fixture_files = _inventory(fixture_source / "fixture_project")
    if set(fixture_files) != _FIXTURE:
        raise KimiCaptureError("frozen synthetic fixture changed")
    template = (fixture_source / "workload.json").read_bytes()
    workload, _ = instantiate_workload(json.loads(template), destination.name)
    scratch = Path(tempfile.mkdtemp(prefix="session-bench-kimi-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)).resolve()
    os.chmod(scratch, 0o700)
    home = scratch / "home"; home.mkdir()
    kimi_home = scratch / "kimi"; kimi_home.mkdir()
    skills = scratch / "empty-skills"; skills.mkdir()
    workspace = scratch / "workspace"; workspace.mkdir()
    fixture = workspace / "fixture_project"
    shutil.copytree(fixture_source / "fixture_project", fixture)
    _write_new(kimi_home / "config.toml", safe_config)
    destination.mkdir(parents=True)
    _write_new(destination / "workload-template.json", template)
    _json(destination / "workload-instance.json", workload)
    _json(destination / "before-workspace.json", fixture_files)
    plan = {
        "schema_version": SCHEMA, "attempt_id": destination.name, "configuration_id": "kimi",
        "status": "prepared", "model_submissions": 0, "score_eligible": False,
        "provider": PROVIDER, "provider_host": HOST, "model_alias": ALIAS,
        "credential_route": "existing normal Kimi config read only in process; secret passed only through child environment",
        "secret_env_key": KEY_ENV, "credential_values_retained": False,
        "kimi_executable": str(executable), "kimi_executable_sha256": _sha(executable.read_bytes()),
        "source_config_path": str(config), "source_config_sha256": _sha(config.read_bytes()),
        "safe_config_sha256": _sha(safe_config), "scratch_retained": str(scratch),
        "workspace": str(workspace), "fixture": str(fixture), "kimi_code_home": str(kimi_home),
        "isolated_home": str(home), "skills_dir": str(skills),
        "fixture_before": fixture_files, "workload_template_sha256": _sha(template),
        "controller_source_sha256": _sha(Path(__file__).read_bytes()),
        "no_topup_or_route_switch": True, "max_cli_invocations_per_turn": 1,
        "max_attempts_per_step": "kimi default", "per_turn_timeout_seconds": 600, "pause_before_later_turns_seconds": 65,
    }
    _json(destination / "plan.json", plan)
    return plan


def _secret_free(raw: bytes, secret: str) -> None:
    if secret.encode() in raw:
        raise KimiCaptureError("credential appeared in captured output; evidence quarantined in private scratch")


def _session_family(kimi_home: Path) -> tuple[str, Path]:
    base = kimi_home / "sessions"
    if not base.is_dir() or base.is_symlink():
        raise KimiCaptureError("Kimi session root missing")
    wires = [p for p in base.rglob("wire.jsonl") if p.is_file() and not p.is_symlink() and p.parent.name == "main" and p.parent.parent.name == "agents"]
    if len(wires) != 1:
        raise KimiCaptureError("new isolated Kimi session boundary ambiguous")
    family = wires[0].parents[2]
    if not family.name.startswith("session_") or not re.fullmatch(r"[0-9a-f-]{36}", family.name[8:]):
        raise KimiCaptureError("Kimi session ID unavailable")
    # The CLI's `-S` argument and resume_hint use the `session_` prefix.
    return family.name, family


def _ledger(fixture: Path, workload: Mapping, phases: tuple[str, ...]) -> list[dict]:
    path = fixture / ".survival-observer.jsonl"
    if not path.is_file() or path.is_symlink():
        raise KimiCaptureError("independent helper ledger absent")
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if [row.get("phase") for row in rows] != list(phases):
        raise KimiCaptureError("independent helper phase population incomplete")
    for row in rows:
        phase = row["phase"]
        if (row.get("run_canary") != workload["run_canary"] or
                row.get("helper_nonce") != workload["helper"]["nonces"][phase] or
                row.get("exit_code") != {"inspect": 0, "baseline": 1, "final": 0}[phase] or
                not str(row.get("output", "")).startswith("SB_SURVIVAL_V1_HELPER_" + phase.upper() + "_")):
            raise KimiCaptureError("independent helper contents invalid")
    return rows


def _final_stdout(raw: bytes, canary: str, session_id: str | None, *, require_hint: bool) -> str:
    """Require a completed assistant text, not a prompt, tool, or error echo."""
    try:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KimiCaptureError("Kimi stdout is not JSONL") from error
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise KimiCaptureError("Kimi stdout lacks structured records")
    hints = [row for row in rows if row.get("type") == "session.resume_hint"]
    if ((require_hint and len(hints) != 1) or len(hints) > 1
            or any(row.get("session_id") != session_id for row in hints)):
        raise KimiCaptureError("Kimi stdout has no unique matching native session ID")
    assistants = [row for row in rows if row.get("role") == "assistant" and isinstance(row.get("content"), str)
                  and not row.get("tool_calls") and not row.get("tool_call_id")]
    matches = [row for row in assistants if row["content"].rstrip().endswith(canary)]
    if len(matches) != 1 or not assistants or assistants[-1] is not matches[0]:
        raise KimiCaptureError("Kimi stdout has no unique final assistant response canary")
    return matches[0]["content"]


def _native_turns(wire: bytes, workload: Mapping, expected_count: int) -> None:
    try:
        rows = [json.loads(line) for line in wire.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KimiCaptureError("Kimi native wire is not JSONL") from error
    prompts = [row for row in rows if row.get("type") == "turn.prompt"]
    ends = [row for row in rows if row.get("type") == "turn.ended"]
    agent_ends = [row for row in rows if row.get("type") == "agent.turn.ended"]
    if len(prompts) != expected_count or len(ends) != expected_count or len(agent_ends) != expected_count:
        raise KimiCaptureError("Kimi native turn population incomplete or extra")
    for number, (prompt, end, agent_end) in enumerate(zip(prompts, ends, agent_ends, strict=True)):
        if (prompt.get("turnId") != number or end.get("turnId") != number or agent_end.get("turnId") != number
                or prompt.get("input") != [{"type": "text", "text": workload["turns"][number]["text"]}]
                or end.get("reason") != "completed" or agent_end.get("outcome") != "done"):
            raise KimiCaptureError("Kimi native prompt or completion differs from exact workload")


def _invoke(argv: list[str], *, cwd: Path, env: Mapping[str, str], stdout: Path, stderr: Path, timeout: int) -> int:
    with stdout.open("xb") as out, stderr.open("xb") as err:
        child = subprocess.Popen(argv, cwd=cwd, env=dict(env), stdout=out, stderr=err, start_new_session=True)
        try:
            return child.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise KimiCaptureError("bounded turn timed out; no retry") from error


def _turn_argv(plan: Mapping, prompt: str, session_id: str | None) -> list[str]:
    argv = [plan["kimi_executable"], "-p", prompt, "--output-format", "stream-json",
            "-m", ALIAS, "--skills-dir", plan["skills_dir"]]
    if session_id is not None:
        argv.extend(["-S", session_id])
    return argv


def run(destination: Path, *, repository: Path, config: Path) -> dict:
    destination = destination.absolute(); repository = repository.resolve()
    plan = json.loads((destination / "plan.json").read_bytes())
    if plan.get("status") != "prepared" or plan.get("schema_version") != SCHEMA or plan.get("attempt_id") != destination.name:
        raise KimiCaptureError("unrecognized prepared attempt")
    if (destination / "capture-result.json").exists():
        raise KimiCaptureError("attempt already executed")
    secret, safe = _config(config)
    kimi_home = Path(plan["kimi_code_home"])
    if (_sha(safe) != plan["safe_config_sha256"] or _sha((kimi_home / "config.toml").read_bytes()) != _sha(safe)
            or _sha(config.read_bytes()) != plan["source_config_sha256"]
            or _sha(Path(plan["kimi_executable"]).read_bytes()) != plan["kimi_executable_sha256"]
            or _sha(Path(__file__).read_bytes()) != plan["controller_source_sha256"]):
        raise KimiCaptureError("prepared route/controller source changed")
    scratch = Path(plan["scratch_retained"]); fixture = Path(plan["fixture"])
    if _inventory(kimi_home) != {"config.toml": {"sha256": _sha(safe), "size_bytes": len(safe)}}:
        raise KimiCaptureError("isolated Kimi root is not fresh")
    workload = json.loads((destination / "workload-instance.json").read_bytes())
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TERM", "TMPDIR") if key in os.environ}
    env.update({"HOME": plan["isolated_home"], "KIMI_CODE_HOME": plan["kimi_code_home"],
                KEY_ENV: secret,
                "SB_SURVIVAL_V1_RUN_CANARY": workload["run_canary"]})
    result = {"schema_version": SCHEMA, "attempt_id": destination.name, "status": "in_progress",
              "cli_invocations": 0, "model_submissions": 0, "turns": [], "credential_values_retained": False,
              "scratch_retained": str(scratch), "route": {"provider": PROVIDER, "host": HOST, "model_alias": ALIAS}}
    session_id = None
    try:
        for number, turn in enumerate(workload["turns"], 1):
            argv = _turn_argv(plan, turn["text"], session_id)
            if number > 1:
                time.sleep(65)  # The account allows 3 requests a minute; start each later turn in a fresh window.
            turn_scratch = scratch / f"turn-r{number}"; turn_scratch.mkdir()
            launch = {"argv": argv, "cwd": plan["fixture"], "env_keys": sorted(env),
                      "secret_env_keys": [KEY_ENV], "started_ns": time.time_ns(),
                      "kimi_home_inventory_before": _inventory(kimi_home), "model_submission": True}
            _json(destination / f"turn-r{number}/launch.json", launch)
            stdout = turn_scratch / "stdout.jsonl"; stderr = turn_scratch / "stderr.txt"
            result["cli_invocations"] += 1
            code = _invoke(argv, cwd=fixture, env=env, stdout=stdout, stderr=stderr, timeout=600)
            rawout, rawerr = stdout.read_bytes(), stderr.read_bytes()
            _secret_free(rawout, secret); _secret_free(rawerr, secret)
            _write_new(destination / f"turn-r{number}/stdout.jsonl", rawout)
            _write_new(destination / f"turn-r{number}/stderr.txt", rawerr)
            workspace = _inventory(fixture)
            _json(destination / f"turn-r{number}/workspace-inventory.json", workspace)
            for name in ("checkout.py", "bench_check.py", ".survival-observer.jsonl"):
                p = fixture / name
                if p.is_file():
                    raw = p.read_bytes()
                    _secret_free(raw, secret)
                    _write_new(destination / f"turn-r{number}/workspace/{name}", raw)
            _json(destination / f"turn-r{number}/exit-pre-native.json", {
                "exit_code": code, "ended_ns": time.time_ns(), "stdout_sha256": _sha(rawout),
                "stderr_sha256": _sha(rawerr), "workspace_inventory": workspace,
                "kimi_home_inventory_after": _inventory(kimi_home), "credential_exact_match_in_captured_data": False,
            })
            stopped = _STOP.search((rawout + rawerr).decode("utf-8", errors="replace")) is not None
            # A rate limit that Kimi retried past leaves its text in the stream; only a failed turn is a stop.
            stop_reason = None if code == 0 else "provider/auth/quota stop" if stopped else "CLI turn failed"
            try:
                current_session, family = _session_family(kimi_home)
            except KimiCaptureError:
                if stop_reason is not None:
                    raise KimiCaptureError(stop_reason + "; no native session; no retry")
                raise
            if session_id is None:
                session_id = current_session
            if session_id != current_session:
                raise KimiCaptureError("Kimi R2 created another session")
            first = _inventory(family)
            time.sleep(.1)
            if first != _inventory(family):
                raise KimiCaptureError("Kimi native session still changing after CLI exit")
            for path in family.rglob("*"):
                if path.is_file():
                    _secret_free(path.read_bytes(), secret)
            wire = family / "agents/main/wire.jsonl"
            if not wire.is_file() or wire.is_symlink():
                raise KimiCaptureError("Kimi exact native wire missing")
            result["model_submissions"] = sum(
                json.loads(line).get("type") == "llm.request" for line in wire.read_bytes().splitlines() if line.strip())
            shutil.copytree(family, destination / f"turn-r{number}/native-session")
            if first != _inventory(destination / f"turn-r{number}/native-session"):
                raise KimiCaptureError("copied native session differs from isolated source")
            _json(destination / f"turn-r{number}/exit.json", {
                "exit_code": code, "ended_ns": time.time_ns(), "stdout_sha256": _sha(rawout),
                "stderr_sha256": _sha(rawerr), "native_family_source_inventory": first,
                "native_family_copied_inventory": _inventory(destination / f"turn-r{number}/native-session"),
                "kimi_home_inventory_after": _inventory(kimi_home), "session_id": session_id,
                "credential_exact_match_in_captured_data": False,
            })
            result["turns"].append({"turn": number, "exit_code": code,
                                     "response_canary_in_stdout": False,
                                     "native_family_files": len(first), "session_id": session_id})
            if stop_reason is not None:
                raise KimiCaptureError(stop_reason + "; no retry")
            _final_stdout(rawout, turn["response_canary"], session_id, require_hint=number == 1)
            _native_turns(wire.read_bytes(), workload, number)
            result["turns"][-1]["response_canary_in_stdout"] = True
            if _sha((fixture / "bench_check.py").read_bytes()) != plan["fixture_before"]["bench_check.py"]["sha256"]:
                raise KimiCaptureError("protected synthetic helper changed")
            if number == 1:
                _ledger(fixture, workload, ("inspect", "baseline"))
                if _sha((fixture / "checkout.py").read_bytes()) != plan["fixture_before"]["checkout.py"]["sha256"]:
                    raise KimiCaptureError("R1 modified synthetic checkout")
            else:
                _ledger(fixture, workload, ("inspect", "baseline", "final"))
        result["status"] = "captured_pending_qualification"
    except Exception as error:
        result["status"] = "blocked_or_invalid"
        result["failure_type"] = type(error).__name__
        result["reason"] = str(error) if isinstance(error, KimiCaptureError) else "unexpected capture failure"
    result["session_id"] = session_id
    result["finished_ns"] = time.time_ns()
    _json(destination / "capture-result.json", result)
    return result
