"""Bounded Hermes CLI survival-v1 capture using its installed Python entry point.

The controller avoids the shell launcher (which performs source bootstrap) and
never sets HERMES_HOME. Each turn is one single-query run,
``hermes chat -q PROMPT --format stream-json``: stdout is the JSON-lines event
stream of the turn (tool calls, tool results, final result). The session ID
comes from that stream. The toolsets are ``terminal`` and ``file`` (terminal,
read, write, patch, search): what a coding session needs for this task, and no
tool that reaches the network. ``--yolo`` answers the approval gate, because a
single-query run has no user (the earlier ``-z`` mode set the same switch itself).

Captures before 2026-10-07 used ``-z`` with ``--toolsets terminal``. That
removed the file tools of Hermes and gave no tool events. They are superseded.

The native record is the state that Hermes itself wrote. The controller lists
the whole Hermes home by metadata before the first turn and after each turn,
accounts for every changed entry, copies the files of the session, and exports
the rows of the session from the shared store ``state.db``
(``session_bench/hermes_state_evidence.py``). The ``hermes sessions export``
file is kept as a derived projection for cross-checks. It is not the native record.
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
from typing import Callable, Mapping

from .adapters.agent_session_native_decoder import decode_agent_native_bytes
from .hermes_stream_observer import HermesStreamError, stream_summary
from .hermes_state_evidence import (
    HermesStateError, capture_hermes_state, inventory_hermes_home, refusal_listing,
)
from .workload_instance import instantiate_workload


SCHEMA = "session-bench-hermes-survival-capture-v2"
LEGACY_SCHEMA = "session-bench-hermes-survival-capture-v1"   # ``-z`` captures with the terminal toolset only
TOOLSETS = ("terminal", "file")
STDOUT_NAME = "stdout.jsonl"
PROVIDER = "openai-codex"
MODEL = "gpt-5.5"
# The entry code of captures before 2026-10-07. It takes the source root from ``sys.argv`` and pops it.
# Hermes' bootstrap can re-execute the process under its managed interpreter
# (``hermes_bootstrap`` -> ``venv_sync.relaunch_command``): it sets ``sys.argv`` to the argv of the first
# process (the root already popped) and runs this code again. The second pop then drops the first Hermes
# argument. With ``-z`` that was ``--ignore-user-config`` (the environment switch gave the same bypass);
# with ``chat`` first it dropped the subcommand and the launch failed.
LEGACY_ENTRYPOINT_CODE = (
    "import runpy,sys; sys.path.insert(0, sys.argv.pop(1)); "
    "runpy.run_module('hermes_cli.main', run_name='__main__')"
)


def entrypoint_code(source_root: Path | str) -> str:
    """The ``python -c`` entry: the source root is in the code, and ``sys.argv`` is not changed.

    So the code gives the same argv when Hermes' bootstrap runs it a second time.
    """
    return f"import runpy,sys; sys.path.insert(0, {str(source_root)!r}); runpy.run_module('hermes_cli.main', run_name='__main__')"
_STOP = re.compile(r"authentication required|unauthorized|invalid api key|quota exceeded|rate.?limit|insufficient.*(?:credit|balance)|payment required|not logged in|token refresh failed", re.I)
_FIXTURE_FILES = {
    "checkout.py", "bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py",
}


class HermesCaptureError(RuntimeError):
    """The isolated Hermes capture cannot safely continue."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HermesCaptureError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise HermesCaptureError(f"{label}: invalid JSON") from error


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def _write_json(path: Path, value) -> None:
    _write_new(path, (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False) + "\n").encode())


def _tree(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise HermesCaptureError("capture workspace must be an ordinary directory")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise HermesCaptureError("symlink encountered in isolated workspace")
        if path.is_dir():
            continue
        if not path.is_file():
            raise HermesCaptureError("special file encountered in isolated workspace")
        files[path.relative_to(root).as_posix()] = path.read_bytes()
        if len(files) > 1000:
            raise HermesCaptureError("workspace exceeded file-count limit")
    return files


def _copy_tree(files: Mapping[str, bytes], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for relative, raw in sorted(files.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise HermesCaptureError("non-canonical workspace artifact path")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_new(target, raw)


def prepare_hermes_capture(destination: Path | str, *, repository: Path | str,
                           repetition: int, python: Path | str,
                           source_root: Path | str,
                           hermes_home: Path | str | None = None) -> dict:
    """Create an offline plan; no Hermes CLI command or model is started.

    ``hermes_home`` is the normal Hermes home (``~/.hermes``). Tests pass a
    synthetic tree. It is only named here; nothing in it is read.
    """
    destination = Path(destination).absolute()
    repository = Path(repository).resolve()
    python = Path(python).resolve()
    source_root = Path(source_root).resolve()
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ValueError("repetition must be 1, 2, or 3")
    if destination.exists() or destination.is_symlink():
        raise ValueError("capture destination must be new")
    if not re.fullmatch(r"hermes-[A-Za-z0-9_-]+", destination.name):
        raise ValueError("attempt ID must start with hermes- and use bounded ASCII characters")
    if destination.parent != repository / "artifacts/v1-expanded-preparation/live-captures":
        raise ValueError("capture destination must be in repository live-captures")
    if not python.is_file() or not os.access(python, os.X_OK):
        raise HermesCaptureError("verified Hermes Python runtime is unavailable")
    if not (source_root / "hermes_cli/main.py").is_file():
        raise HermesCaptureError("installed Hermes Python entry point is unavailable")
    if os.environ.get("HERMES_HOME"):
        raise HermesCaptureError("HERMES_HOME override is set; refusing to alter Hermes storage routing")
    hermes_home = Path(hermes_home).absolute() if hermes_home is not None else Path.home() / ".hermes"
    if hermes_home.is_symlink() or not hermes_home.is_dir():
        raise HermesCaptureError("normal Hermes home is not an ordinary directory")

    source = repository / "fixtures/scenarios/survival-v1/workload"
    files = _tree(source / "fixture_project")
    if set(files) != _FIXTURE_FILES:
        raise HermesCaptureError("frozen survival fixture tree has unexpected or missing files")
    template_raw = (source / "workload.json").read_bytes()
    template = _json(template_raw, "checked-in survival workload")
    workload, _ = instantiate_workload(template, destination.name)

    scratch = Path(tempfile.mkdtemp(prefix="session-bench-hermes-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)).absolute()
    workspace = scratch / "workspace"
    fixture = workspace / "fixture_project"
    fixture.mkdir(parents=True)
    for relative, raw in files.items():
        target = fixture / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

    destination.mkdir(parents=True)
    for name in ("observer", "turn-r1", "turn-r2"):
        (destination / name).mkdir()
    _write_new(destination / "workload-template.json", template_raw)
    _write_json(destination / "workload-instance.json", workload)
    _copy_tree(files, destination / "workspaces/before/fixture_project")
    allowed = {"PATH", "HOME", "LANG", "TMPDIR"}
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    env["HERMES_IGNORE_USER_CONFIG"] = "1"
    env["HERMES_IGNORE_RULES"] = "1"
    env["SB_SURVIVAL_V1_RUN_CANARY"] = workload["run_canary"]
    plan = {
        "schema_version": SCHEMA, "attempt_id": destination.name,
        "configuration_id": "hermes", "repetition": repetition,
        "provider": PROVIDER, "model": MODEL,
        "python": str(python), "source_root": str(source_root),
        "entrypoint": "hermes_cli.main:main via runpy; shell launcher bypassed",
        "entrypoint_code": entrypoint_code(source_root),
        "scratch": str(scratch), "workspace": str(workspace), "fixture": str(fixture),
        "environment_keys": sorted(env),
        "environment_overrides": {"HERMES_IGNORE_USER_CONFIG": "1", "HERMES_IGNORE_RULES": "1",
                                  "run_canary": workload["run_canary"]},
        "model_submissions": 0, "status": "prepared", "score_eligible": False,
        "auth_route": "existing normal-HOME openai-codex OAuth; no HERMES_HOME override",
        "hermes_home": str(hermes_home),
        "native_capture": ("whole Hermes home metadata bracket per turn; session-owned files copied; "
                           "session rows of state.db exported from a private temporary copy"),
        "derived_capture": "session-scoped hermes sessions export --session-id from the stream session id",
        "launch_mode": "hermes chat -q PROMPT --format stream-json (single query, JSON-lines events on stdout)",
        "toolsets": list(TOOLSETS), "approval": "--yolo", "stdout_format": "stream-json",
        "session_id_source": "system/init and result events of the stdout stream",
        "no_session_list": True, "no_unrelated_session_reads": True,
        "no_fallback_config": True, "protected_sha256": {name: _sha(raw) for name, raw in files.items()},
        "workload_sha256": _sha(template_raw),
    }
    _write_json(destination / "plan.json", plan)
    return plan


def turn_arguments(workspace: str, prompt: str, session_id: str | None = None) -> list[str]:
    """The Hermes arguments of one turn. The second turn resumes the session of the first."""
    args = ["chat", "--ignore-user-config", "--ignore-rules", "--no-restore-cwd", "--in", str(workspace),
            "--provider", PROVIDER, "--model", MODEL, "--toolsets", ",".join(TOOLSETS), "--yolo",
            "--format", "stream-json"]
    if session_id is not None:
        args.extend(["--resume", session_id])
    return [*args, "-q", prompt]


def planned_argv(*, python: Path | str, source_root: Path | str, workspace: str, prompts: list[str],
                 session_id: str = "<session id of turn 1>") -> list[list[str]]:
    """The full argv of both turns, for an offline check. Nothing is started."""
    plan = {"python": str(python), "entrypoint_code": entrypoint_code(source_root), "source_root": str(source_root)}
    return [_module_argv(plan, turn_arguments(workspace, prompts[0])),
            _module_argv(plan, turn_arguments(workspace, prompts[1], session_id))]


def _module_argv(plan: Mapping, args: list[str]) -> list[str]:
    if "sys.argv.pop(" in plan["entrypoint_code"]:
        # A plan of an older capture (only the legacy continuation path still starts one).
        return [plan["python"], "-I", "-B", "-c", plan["entrypoint_code"], plan["source_root"], *args]
    return [plan["python"], "-I", "-B", "-c", plan["entrypoint_code"], *args]


def _preflight(plan: Mapping, env: Mapping[str, str], timeout: float = 30.0) -> dict:
    argv = _module_argv(plan, ["--version"])
    result = subprocess.run(argv, cwd=plan["workspace"], env=dict(env),
                            capture_output=True, timeout=timeout)
    version = result.stdout.decode("utf-8", errors="replace").strip()
    ready = result.returncode == 0 and version.startswith("Hermes Agent v")
    return {"ready": ready, "argv": argv, "returncode": result.returncode,
            "version": version, "stderr_sha256": _sha(result.stderr),
            "model_submission": False,
            "failure": None if ready else "installed Hermes Python entry point did not report a version"}


def _run(argv, *, cwd: Path, env: Mapping[str, str], stdout: Path, stderr: Path, timeout: float) -> int:
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=dict(env), stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise HermesCaptureError("turn timed out; no retry or fallback") from error


def _validate_ledger(path: Path, workload: Mapping, phases: tuple[str, ...], label: str) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise HermesCaptureError(f"{label} helper ledger missing")
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise HermesCaptureError(f"{label} helper ledger exceeded byte limit")
    rows = [_json(line, f"{label} helper ledger") for line in raw.splitlines()]
    if len(rows) != len(phases):
        raise HermesCaptureError(f"{label} helper ledger has missing or extra phases")
    expected_codes = {"inspect": 0, "baseline": 1, "final": 0}
    for row, phase in zip(rows, phases, strict=True):
        nonce = workload["helper"]["nonces"][phase]
        if (row.get("schema_version") != "1.0-survival-helper-ledger"
                or row.get("phase") != phase or row.get("run_canary") != workload["run_canary"]
                or row.get("helper_nonce") != nonce
                or row.get("id") != f"helper-{phase}-{nonce}"
                or row.get("argv") != ["python3", "bench_check.py", phase]
                or row.get("cwd") != "fixture_project"
                or row.get("exit_code") != expected_codes[phase]
                or not str(row.get("output", "")).startswith(f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} ")):
            raise HermesCaptureError(f"{label} helper ledger does not prove exact {phase} execution")
    return rows


def _native_export(destination: Path, turn: int, plan: Mapping, session_id: str,
                   runner: Callable, env: Mapping, workspace: Path, timeout: float) -> bytes:
    prefix = destination / f"turn-r{turn}"
    raw_target = prefix / "native/session.jsonl"
    argv = _module_argv(plan, ["--ignore-user-config", "sessions", "export", "--format", "jsonl",
                              "--session-id", session_id, str(raw_target)])
    _write_json(prefix / "native/launch.json", {"argv": argv, "cwd": str(workspace),
                                                  "session_id": session_id,
                                                  "exact_session_only": True, "derived": True})
    out, err = prefix / "native/stdout.txt", prefix / "native/stderr.txt"
    code = runner(argv, cwd=workspace, env=env, stdout=out, stderr=err, timeout=timeout)
    if code != 0 or not raw_target.is_file() or raw_target.is_symlink():
        raise HermesCaptureError("exact-session Hermes export failed")
    raw = raw_target.read_bytes()
    result = decode_agent_native_bytes("hermes", raw)
    if result.session_id != session_id:
        raise HermesCaptureError("Hermes export returned a different session identity")
    _write_json(prefix / "native/receipt.json", {"session_id": session_id,
                  "sha256": _sha(raw), "size_bytes": len(raw),
                  "derived": True, "native_record": False,
                  "role": "derived-exporter-projection",
                  "scope": ("single exact session ID; a projection made by the Hermes exporter, "
                            "not the native record; the native record is bound by the "
                            "r{N}-native-receipt.json of the turn when the controller wrote one")})
    return raw


def _native_state(destination: Path, turn: int, plan: Mapping, before: Mapping, detail: Mapping,
                  session_id: str, started_ns: int, build, settle: Mapping) -> dict:
    """Bracket the whole Hermes home for one turn; on a refusal leave a listing and stop."""
    home = Path(plan["hermes_home"])
    try:
        receipt, after = capture_hermes_state(
            home, destination / f"r{turn}-native", before, attempt_id=destination.name, turn=turn,
            session_id=session_id, started_ns=started_ns, build=build, before_detail=detail, **settle)
    except HermesStateError as error:
        _write_json(destination / f"r{turn}-refusal.json",
                    refusal_listing(error, attempt_id=destination.name, turn=turn, session_id=session_id))
        try:
            # The metadata listing of the refused state helps the diagnosis; it stays private.
            _write_json(destination / f"r{turn}-state-refused.json", inventory_hermes_home(home))
        except (OSError, ValueError):
            pass
        raise HermesCaptureError(f"R{turn} native state capture refused (see r{turn}-refusal.json): {error}") from error
    _write_json(destination / f"r{turn}-state-after.json", after)
    _write_json(destination / f"r{turn}-native-receipt.json", receipt)
    return receipt


def _stdout_ok(raw: bytes, canary: str, label: str) -> None:
    if len(raw) > 16 * 1024 * 1024:
        raise HermesCaptureError(f"{label} stdout exceeded byte limit")
    text = raw.decode("utf-8", errors="strict")
    if not text.rstrip().endswith(canary):
        raise HermesCaptureError(f"{label} visible response lacks the exact canary")
    if _STOP.search(text):
        raise HermesCaptureError(f"{label} indicates auth/quota failure")


def execute_hermes_capture(destination: Path | str, *, timeout: float = 300.0,
                           preflight: Callable = _preflight, runner: Callable = _run,
                           state_settle: Mapping | None = None) -> dict:
    """Submit two one-shot turns once each, resuming only the exact captured session.

    ``state_settle`` passes ``interval_seconds``, ``max_reads`` and ``sleep`` to
    the whole-home bracket. The default waits 0.5 s between metadata reads.
    """
    destination = Path(destination).absolute()
    plan = _json((destination / "plan.json").read_bytes(), "Hermes plan")
    workload = _json((destination / "workload-instance.json").read_bytes(), "Hermes workload")
    if plan.get("schema_version") != SCHEMA or plan.get("status") != "prepared" or plan.get("attempt_id") != destination.name:
        raise HermesCaptureError("capture is not a fresh prepared plan")
    if any((destination / f"turn-r{n}/{STDOUT_NAME}").exists() for n in (1, 2)) or (destination / "capture-result.json").exists():
        raise HermesCaptureError("capture is single-use")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 600:
        raise ValueError("timeout must be bounded at 600 seconds")
    scratch, workspace, fixture = Path(plan["scratch"]), Path(plan["workspace"]), Path(plan["fixture"])
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if os.environ.get(key)}
    env.update({"HERMES_IGNORE_USER_CONFIG": "1", "HERMES_IGNORE_RULES": "1",
                "SB_SURVIVAL_V1_RUN_CANARY": workload["run_canary"]})
    if os.environ.get("HERMES_HOME") or sorted(env) != plan["environment_keys"]:
        raise HermesCaptureError("normal Hermes home or environment boundary changed")
    if scratch.resolve() == Path(__file__).resolve().parents[1] or Path(__file__).resolve().parents[1] in scratch.resolve().parents:
        raise HermesCaptureError("scratch unexpectedly overlaps the repository")
    if not isinstance(plan.get("hermes_home"), str):
        raise HermesCaptureError("plan predates the whole Hermes home bracket; prepare a new capture")
    settle = {"interval_seconds": 0.5, "max_reads": 8, **(state_settle or {})}
    state = {"schema_version": SCHEMA, "attempt_id": destination.name, "status": "attempted",
             "provider": PROVIDER, "model": MODEL, "model_submissions": 0,
             "session_id": None, "turns": [], "score_eligible": False,
             "normal_home_only": True, "no_unrelated_session_reads": True,
             "state_root_scope": "whole_hermes_home"}
    _write_json(destination / "controller-state.json", state)
    try:
        preflight_receipt = preflight(plan, env)
        _write_json(destination / "preflight.json", preflight_receipt)
        if preflight_receipt.get("ready") is not True:
            raise HermesCaptureError("installed Hermes entry point preflight failed; no model call submitted")
        # Metadata of the whole Hermes home before the first turn. Nothing is opened.
        started_ns = time.time_ns()
        before_detail: dict = {}
        before = inventory_hermes_home(Path(plan["hermes_home"]), detail=before_detail)
        _write_json(destination / "state-before.json", before)
        state["started_ns"] = started_ns
        for turn in (1, 2):
            current = _tree(fixture)
            for relative, digest in plan["protected_sha256"].items():
                if relative != "checkout.py" and _sha(current.get(relative, b"")) != digest:
                    raise HermesCaptureError(f"protected fixture changed: {relative}")
            if _sha(current.get("checkout.py", b"")) != plan["protected_sha256"]["checkout.py"]:
                raise HermesCaptureError("R1 changed checkout before R2 was authorized")

            prompt_row = workload["turns"][turn - 1]
            prompt, canary = prompt_row["text"], prompt_row["response_canary"]
            prefix = destination / f"turn-r{turn}"
            argv = _module_argv(plan, turn_arguments(str(workspace), prompt, state["session_id"] if turn == 2 else None))
            _write_new(destination / f"observer/prompt-r{turn}.txt", prompt.encode())
            _write_json(prefix / "launch.json", {"argv": argv, "cwd": str(workspace),
                          "environment_keys": sorted(env), "provider": PROVIDER, "model": MODEL,
                          "prompt_sha256": _sha(prompt.encode()), "session_id": state["session_id"],
                          "config_bypass": True, "shell_launcher_bypassed": True,
                          "toolsets": list(TOOLSETS), "approval": "--yolo", "stdout_format": "stream-json"})
            state["model_submissions"] += 1
            state["turns"].append({"turn": turn, "status": "running"})
            (destination / "controller-state.json.tmp").write_text(json.dumps(state, indent=2) + "\n")
            (destination / "controller-state.json.tmp").replace(destination / "controller-state.json")
            stdout, stderr = prefix / STDOUT_NAME, prefix / "stderr.txt"
            code = runner(argv, cwd=workspace, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
            out_raw, err_raw = stdout.read_bytes(), stderr.read_bytes()
            _write_json(prefix / "exit.json", {"returncode": code, "ended_ns": time.time_ns(),
                                                 "stdout_sha256": _sha(out_raw), "stderr_sha256": _sha(err_raw)})
            # Snapshot all model-written files before interpreting failures or trying
            # the exact-session native export.
            current = _tree(fixture)
            _copy_tree(current, prefix / "workspace/fixture_project")
            if code != 0 or _STOP.search(err_raw.decode("utf-8", errors="replace")):
                raise HermesCaptureError(f"R{turn} stopped on command/auth/quota failure; no retry or fallback")
            if len(out_raw) > 16 * 1024 * 1024:
                raise HermesCaptureError(f"R{turn} stdout exceeded byte limit")
            try:
                # The stream must be complete: one init event, one result event with exit code 0, one session.
                summary = stream_summary(out_raw, f"R{turn} stdout")
            except HermesStreamError as error:
                raise HermesCaptureError(f"R{turn} stream is not a complete successful turn: {error}; no retry or fallback") from error
            if not summary["text"].rstrip().endswith(canary):
                raise HermesCaptureError(f"R{turn} visible response lacks the exact canary")
            sid = summary["session_id"]
            if summary["model"] != MODEL:
                raise HermesCaptureError(f"R{turn} stream does not confirm the pinned model")
            _write_json(prefix / "stream-receipt.json", {
                "session_id": sid, "model": summary["model"], "exit_code": summary["exit_code"], "tokens": summary["tokens"],
                "events": summary["events"], "stdout_sha256": _sha(out_raw),
                "event_types": sorted({row["type"] for row in summary["rows"]}),
                "tool_events_with_call_id": sum(1 for row in summary["rows"] if row["type"] in ("tool_use", "tool_result") and row.get("tool_call_id")),
                "tool_events": sum(1 for row in summary["rows"] if row["type"] in ("tool_use", "tool_result"))})
            if state["session_id"] is not None and sid != state["session_id"]:
                raise HermesCaptureError("Hermes resumed a different session")
            state["session_id"] = sid
            # The native record first: the exporter below starts Hermes again and writes to the home.
            native_receipt = _native_state(destination, turn, plan, before, before_detail, sid, started_ns,
                                           preflight_receipt.get("version"), settle)
            _native_export(destination, turn, plan, sid, runner, env, workspace, timeout)

            unexpected = set(current) - (_FIXTURE_FILES | {".survival-observer.jsonl"})
            if unexpected:
                raise HermesCaptureError("workspace wrote files outside the fixture boundary")
            phases = ("inspect", "baseline") if turn == 1 else ("inspect", "baseline", "final")
            _validate_ledger(prefix / "workspace/fixture_project/.survival-observer.jsonl",
                             workload, phases, f"R{turn}")
            state["turns"][-1].update({"status": "completed", "returncode": code,
                          "stdout_sha256": _sha(out_raw), "stderr_sha256": _sha(err_raw),
                          "stream_receipt": f"turn-r{turn}/stream-receipt.json",
                          "native_receipt": f"r{turn}-native-receipt.json",
                          "native_session_store_rows": native_receipt["session_store"]["selected_rows"],
                          "native_session_owned_files": len(native_receipt["classes"]["session_owned"])})
            (destination / "controller-state.json.tmp").write_text(json.dumps(state, indent=2) + "\n")
            (destination / "controller-state.json.tmp").replace(destination / "controller-state.json")
        state.update(status="captured_pending_qualification", scratch_retained=True,
                     independent_reproduction=False,
                     native_scope=("whole Hermes home bracket per turn (hermes-state-root-v1): session-owned files "
                                   "and session rows of state.db"),
                     derived_scope="two exact session-scoped JSONL exports (Hermes exporter projection)")
    except Exception as error:
        state.update(status="capture_incomplete", failure=str(error), scratch_retained=True)
    _write_json(destination / "capture-result.json", state)
    return state


def continue_hermes_capture_after_r1(destination: Path | str, *, timeout: float = 300.0,
                                    preflight: Callable = _preflight,
                                    runner: Callable = _run) -> dict:
    """Validate a preserved successful R1 and submit only its same-session R2.

    This is an append-only decoder-correction continuation. It never edits the
    original capture-result or R1 evidence and will not launch if any R1 pin
    differs from the named original attempt.
    """
    destination = Path(destination).absolute()
    plan_path = destination / "plan.json"
    plan_raw = plan_path.read_bytes()
    plan = _json(plan_raw, "Hermes plan")
    workload = _json((destination / "workload-instance.json").read_bytes(), "Hermes workload")
    original_result_path = destination / "capture-result.json"
    original_result_raw = original_result_path.read_bytes()
    original = _json(original_result_raw, "original Hermes capture result")
    if (plan.get("schema_version") not in (SCHEMA, LEGACY_SCHEMA) or original.get("status") != "capture_incomplete"
            or original.get("model_submissions") != 1
            or original.get("failure") != "hermes: expected session_id and messages fields"
            or (destination / "continuation-result.json").exists()
            or (destination / "continuation-receipt.json").exists()):
        raise HermesCaptureError("capture is not the single preserved Hermes R1 decoder-alias failure")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 600:
        raise ValueError("timeout must be bounded at 600 seconds")

    r1 = destination / "turn-r1"
    native_path = r1 / "native/session.jsonl"
    usage_path = r1 / "usage.json"
    workspace = r1 / "workspace/fixture_project"
    required = [native_path, usage_path, r1 / "stdout.txt", r1 / "stderr.txt",
                r1 / "exit.json", workspace / ".survival-observer.jsonl"]
    if any(path.is_symlink() or not path.is_file() for path in required):
        raise HermesCaptureError("preserved R1 evidence is incomplete")
    native_raw = native_path.read_bytes()
    usage_raw = usage_path.read_bytes()
    usage = _json(usage_raw, "preserved R1 usage receipt")
    sid = usage.get("session_id")
    if (not isinstance(sid, str) or not sid or sid != original.get("session_id")
            or usage.get("provider") != PROVIDER or usage.get("model") != MODEL
            or usage.get("completed") is not True):
        raise HermesCaptureError("preserved R1 usage does not prove the same completed pinned route")
    native_result = decode_agent_native_bytes("hermes", native_raw)
    if native_result.session_id != sid or native_result.status != "complete":
        raise HermesCaptureError("preserved R1 native export does not match its exact usage session")
    out_raw = (r1 / "stdout.txt").read_bytes()
    err_raw = (r1 / "stderr.txt").read_bytes()
    _stdout_ok(out_raw, workload["turns"][0]["response_canary"], "preserved R1")
    if err_raw.strip():
        raise HermesCaptureError("preserved R1 stderr is not empty")
    exit_receipt = _json((r1 / "exit.json").read_bytes(), "preserved R1 exit receipt")
    if exit_receipt.get("returncode") != 0:
        raise HermesCaptureError("preserved R1 did not exit successfully")

    fixture_source = destination.parents[3] / "fixtures/scenarios/survival-v1/workload/fixture_project"
    expected_files = _tree(fixture_source)
    r1_files = _tree(workspace)
    if set(expected_files) | {".survival-observer.jsonl"} != set(r1_files):
        raise HermesCaptureError("preserved R1 workspace lacks the complete frozen fixture tree")
    for relative, raw in expected_files.items():
        if _sha(r1_files[relative]) != _sha(raw):
            raise HermesCaptureError(f"preserved R1 modified or lost frozen file: {relative}")
    _validate_ledger(workspace / ".survival-observer.jsonl", workload,
                     ("inspect", "baseline"), "preserved R1")
    r2_root = destination / "turn-r2"
    if not r2_root.is_dir() or any(r2_root.iterdir()):
        raise HermesCaptureError("R2 already has artifacts; refusing a duplicate turn")

    receipt = {
        "schema_version": "session-bench-hermes-continuation-v1",
        "reason": "installed Hermes exporter stores session identity in `id`; decoder now accepts that exact field",
        "original_capture_result_sha256": _sha(original_result_raw),
        "plan_sha256": _sha(plan_raw), "r1_session_id": sid,
        "r1_native_sha256": _sha(native_raw), "r1_usage_sha256": _sha(usage_raw),
        "r1_workspace_sha256": _sha(json.dumps({k: _sha(v) for k, v in sorted(r1_files.items())}, sort_keys=True).encode()),
        "r1_ledger_sha256": _sha(r1_files[".survival-observer.jsonl"]),
        "r1_stdout_sha256": _sha(out_raw), "r1_stderr_sha256": _sha(err_raw),
        "r1_native_identity_verified": sid, "r1_helper_phases_verified": ["inspect", "baseline"],
        "r2_model_submissions_before_continuation": 0,
        "no_prior_capture_or_r1_evidence_modified": True,
    }
    _write_json(destination / "continuation-receipt.json", receipt)

    scratch, live_workspace, fixture = Path(plan["scratch"]), Path(plan["workspace"]), Path(plan["fixture"])
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if os.environ.get(key)}
    env.update({"HERMES_IGNORE_USER_CONFIG": "1", "HERMES_IGNORE_RULES": "1",
                "SB_SURVIVAL_V1_RUN_CANARY": workload["run_canary"]})
    state = {"schema_version": SCHEMA, "attempt_id": destination.name,
             "status": "attempted", "provider": PROVIDER, "model": MODEL,
             "model_submissions_before_continuation": 1, "model_submissions_in_continuation": 0,
             "session_id": sid, "no_auth_retry": True, "no_provider_fallback": True,
             "original_capture_result_sha256": receipt["original_capture_result_sha256"]}
    try:
        if os.environ.get("HERMES_HOME") or sorted(env) != plan["environment_keys"]:
            raise HermesCaptureError("normal Hermes home or original environment boundary changed")
        version_receipt = preflight(plan, env)
        _write_json(destination / "continuation-preflight.json", version_receipt)
        if version_receipt.get("ready") is not True:
            raise HermesCaptureError("installed Hermes entry point preflight failed; no continuation model call")
        current = _tree(fixture)
        if set(current) != _FIXTURE_FILES | {".survival-observer.jsonl"}:
            raise HermesCaptureError("live R1 workspace is missing the frozen tree or observer ledger")
        for relative, digest in plan["protected_sha256"].items():
            if relative != "checkout.py" and _sha(current.get(relative, b"")) != digest:
                raise HermesCaptureError(f"R1 live workspace changed protected file: {relative}")
        if _sha(current.get("checkout.py", b"")) != plan["protected_sha256"]["checkout.py"]:
            raise HermesCaptureError("live workspace checkout differs from the frozen R1 baseline")
        if _sha(current[".survival-observer.jsonl"]) != receipt["r1_ledger_sha256"]:
            raise HermesCaptureError("live R1 helper ledger differs from preserved R1 copy")

        prompt_row = workload["turns"][1]
        prompt, canary = prompt_row["text"], prompt_row["response_canary"]
        usage_file = scratch / "usage-r2.json"
        args = ["--ignore-user-config", "--ignore-rules", "--no-restore-cwd", "--in", str(live_workspace),
                "--provider", PROVIDER, "--model", MODEL, "--toolsets", "terminal",
                "--usage-file", str(usage_file), "--resume", sid, "-z", prompt]
        argv = _module_argv(plan, args)
        _write_new(destination / "observer/prompt-r2-continuation.txt", prompt.encode())
        _write_json(r2_root / "launch.json", {"argv": argv, "cwd": str(live_workspace),
                    "environment_keys": sorted(env), "provider": PROVIDER, "model": MODEL,
                    "prompt_sha256": _sha(prompt.encode()), "session_id": sid,
                    "config_bypass": True, "shell_launcher_bypassed": True,
                    "continuation_of_original_r1": True})
        state["model_submissions_in_continuation"] = 1
        (destination / "continuation-state.json").write_text(json.dumps(state, indent=2) + "\n")
        stdout, stderr = r2_root / "stdout.txt", r2_root / "stderr.txt"
        code = runner(argv, cwd=live_workspace, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
        out_raw, err_raw = stdout.read_bytes(), stderr.read_bytes()
        _write_json(r2_root / "exit.json", {"returncode": code, "ended_ns": time.time_ns(),
                                              "stdout_sha256": _sha(out_raw), "stderr_sha256": _sha(err_raw)})
        current = _tree(fixture)
        _copy_tree(current, r2_root / "workspace/fixture_project")
        if usage_file.is_file() and not usage_file.is_symlink():
            usage_raw = usage_file.read_bytes()
            _write_new(r2_root / "usage.json", usage_raw)
            usage = _json(usage_raw, "R2 usage receipt")
        else:
            usage_raw, usage = None, {}
        if code != 0 or _STOP.search(err_raw.decode("utf-8", errors="replace")):
            raise HermesCaptureError("R2 stopped on command/auth/quota failure; no retry or fallback")
        _stdout_ok(out_raw, canary, "R2")
        if usage_raw is None or usage.get("session_id") != sid:
            raise HermesCaptureError("R2 usage receipt does not confirm the exact resumed session")
        if usage.get("provider") != PROVIDER or usage.get("model") != MODEL or usage.get("completed") is not True:
            raise HermesCaptureError("R2 usage receipt does not confirm the pinned completed route")
        _native_export(destination, 2, plan, sid, runner, env, live_workspace, timeout)
        unexpected = set(current) - (_FIXTURE_FILES | {".survival-observer.jsonl"})
        if unexpected:
            raise HermesCaptureError("R2 workspace wrote files outside the fixture boundary")
        _validate_ledger(r2_root / "workspace/fixture_project/.survival-observer.jsonl",
                         workload, ("inspect", "baseline", "final"), "R2")
        state.update(status="captured_pending_qualification", session_id=sid,
                     model_submissions_in_continuation=1, scratch_retained=True,
                     independent_reproduction=False,
                     native_scope="two exact session-scoped JSONL exports",
                     native_state_bracket=False)
    except Exception as error:
        state.update(status="capture_incomplete", failure=str(error), scratch_retained=True)
    _write_json(destination / "continuation-result.json", state)
    return state


def cleanup_hermes_scratch(destination: Path | str) -> None:
    destination = Path(destination).absolute()
    result = _json((destination / "capture-result.json").read_bytes(), "Hermes capture result")
    if result.get("status") != "captured_pending_qualification":
        raise HermesCaptureError("scratch cleanup requires a complete capture")
    plan = _json((destination / "plan.json").read_bytes(), "Hermes plan")
    scratch = Path(plan["scratch"])
    if scratch.is_symlink() or not scratch.is_dir() or scratch == Path("/"):
        raise HermesCaptureError("refusing unsafe scratch cleanup path")
    shutil.rmtree(scratch)
