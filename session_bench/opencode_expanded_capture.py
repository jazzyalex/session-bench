"""Bounded fresh OpenCode captures, separate from the frozen 1.18.30 campaign."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from .native_replay import _snapshot_tree, _open_directory
from .workload_instance import instantiate_workload
from .adapters.opencode_cli import capture_sqlite_bundle, observe_stdout, read_sqlite_session_ids
from .adapters.opencode_decoder import decode_opencode_bundle, _strict_json
from .live_observer import build_opencode_live_observer

VERSION = "1.18.31"
MODEL = "opencode/muse-spark-1.3-contributor-free"
SCHEMA = "session-bench-expanded-opencode-capture-v1"
_STOP = re.compile(r"authentication required|unauthorized|invalid api key|quota exceeded|rate.?limit|insufficient.*(?:credit|balance)|payment required|ECONN|ENOTFOUND|fetch failed|network error", re.I)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write(path, value):
    with Path(path).open("xb") as file:
        file.write((json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode())


def prepare_expanded_opencode_capture(destination, *, repository, repetition, executable="opencode"):
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ValueError("repetition must be 1, 2, or 3")
    destination = Path(destination).absolute(); repository = Path(repository).absolute()
    descriptor = _open_directory(destination.parent); os.close(descriptor)
    if destination.exists() or destination.is_symlink():
        raise ValueError("capture destination exists")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", destination.name):
        raise ValueError("invalid capture ID")
    # Persistent OS-temp root remains private evidence. Never copy account
    # configuration, credentials, Git state, or model-visible answer snapshots.
    scratch = Path(tempfile.mkdtemp(prefix="session-bench-opencode-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)).resolve()
    if repository == scratch or repository in scratch.parents:
        raise ValueError("workspace must be outside the repository")
    dirs = {name: scratch / name for name in ("home", "xdg-config", "xdg-data", "xdg-cache", "xdg-state", "native", "workspace")}
    for directory in dirs.values(): directory.mkdir()
    fixture = dirs["workspace"] / "fixture_project"; fixture.mkdir()
    frozen = repository / "fixtures/scenarios/survival-v1/workload"
    source = _snapshot_tree(frozen / "fixture_project")
    if set(source) != {"checkout.py", "bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py"}:
        raise ValueError("unexpected frozen fixture boundary")
    for name in ("checkout.py", "bench_check.py"): (fixture / name).write_bytes(source[name])
    workload, canary = instantiate_workload(json.loads((frozen / "workload.json").read_bytes()), destination.name)
    config = {"$schema": "https://opencode.ai/config.json", "model": MODEL, "small_model": MODEL,
              "share": "disabled", "autoupdate": False, "plugin": [], "instructions": [],
              "enabled_providers": ["opencode"], "snapshot": False,
              "permission": {"external_directory": "deny", "webfetch": "deny", "websearch": "deny", "task": "deny"}}
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "en_US.UTF-8", "HOME": str(dirs["home"]),
           "XDG_CONFIG_HOME": str(dirs["xdg-config"]), "XDG_DATA_HOME": str(dirs["xdg-data"]),
           "XDG_CACHE_HOME": str(dirs["xdg-cache"]), "XDG_STATE_HOME": str(dirs["xdg-state"]),
           "OPENCODE_DB": str(dirs["native"] / "opencode.db"), "OPENCODE_CONFIG_CONTENT": json.dumps(config, ensure_ascii=False),
           **canary}
    destination.mkdir(); (destination / "observer").mkdir()
    plan = {"schema_version": SCHEMA, "configuration_id": "opencode-cli", "attempt_id": destination.name,
            "repetition": repetition, "version_pin": VERSION, "model": MODEL, "executable": executable,
            "scratch": str(scratch), "workspace": str(dirs["workspace"]), "environment": env, "config": config,
            "native_empty_before_preflight": list(dirs["native"].iterdir()) == [],
            "home_xdg_empty_before_preflight": all(not list(dirs[name].iterdir()) for name in ("home", "xdg-config", "xdg-data", "xdg-cache", "xdg-state")),
            "auth_copied": False, "score_eligible": False, "protected_helper_sha256": _sha(source["bench_check.py"]),
            "checkout_before_sha256": _sha(source["checkout.py"]),
            "workload_source_sha256": _sha((frozen / "workload.json").read_bytes())}
    _write(destination / "plan.json", plan); _write(destination / "workload.json", workload)
    return plan


def _run(argv, *, cwd, env, stdout, stderr, timeout):
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
        try: return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait()
            raise ValueError("180-second turn timeout; no retry")


def _preflight(plan, destination):
    for label, argv in (("version", [plan["executable"], "--version"]),
                        ("resolved-config", [plan["executable"], "--pure", "debug", "config"])):
        completed = subprocess.run(argv, cwd=plan["workspace"], env=plan["environment"], capture_output=True, timeout=30)
        (destination / f"{label}.stdout.txt").write_bytes(completed.stdout)
        (destination / f"{label}.stderr.txt").write_bytes(completed.stderr)
        if completed.returncode != 0: raise ValueError("read-only CLI preflight failed")
        if label == "version":
            if completed.stdout.decode().strip() != VERSION: raise ValueError("installed OpenCode version differs from pin")
        else:
            value = json.loads(completed.stdout)
            for key in ("model", "small_model", "share", "autoupdate", "enabled_providers"):
                if value.get(key) != plan["config"][key]: raise ValueError("resolved CLI config differs from declared free/isolation route")
            if value.get("plugin") not in (None, []): raise ValueError("external plugin unexpectedly enabled")
    return VERSION


def execute_expanded_opencode_capture(destination, *, runner=_run, preflight=_preflight):
    """Submit each turn once, preserving exact independent inputs and native bytes."""
    destination = Path(destination).absolute(); retained = _snapshot_tree(destination)
    if "capture-result.json" in retained or any(name.startswith("observer/r") for name in retained):
        raise ValueError("capture is single-use")
    plan = json.loads(retained["plan.json"]); workload = json.loads(retained["workload.json"])
    if plan["schema_version"] != SCHEMA or plan["version_pin"] != VERSION or plan["model"] != MODEL:
        raise ValueError("capture identity changed")
    scratch = Path(plan["scratch"]); workspace = Path(plan["workspace"]); fixture = workspace / "fixture_project"
    expected_dirs = {"HOME": "home", "XDG_CONFIG_HOME": "xdg-config", "XDG_DATA_HOME": "xdg-data", "XDG_CACHE_HOME": "xdg-cache", "XDG_STATE_HOME": "xdg-state"}
    env = plan["environment"]
    if (workspace != scratch / "workspace" or env["OPENCODE_DB"] != str(scratch / "native/opencode.db")
            or any(env[key] != str(scratch / name) for key, name in expected_dirs.items())
            or json.loads(env["OPENCODE_CONFIG_CONTENT"]) != plan["config"]
            or plan["config"].get("small_model") != MODEL or plan["config"].get("share") != "disabled"
            or set(env) != {"PATH", "LANG", "OPENCODE_DB", "OPENCODE_CONFIG_CONTENT", "SB_SURVIVAL_V1_RUN_CANARY", *expected_dirs}):
        raise ValueError("fresh persistence/config boundary changed")
    state = {"schema_version": SCHEMA, "attempt_id": plan["attempt_id"], "repetition": plan["repetition"],
             "configuration_id": "opencode-cli", "model": MODEL, "version": VERSION,
             "status": "invalid", "score_eligible": False, "model_submissions": 0, "turns": {}, "reason_ids": []}
    session = None
    try:
        state["actual_version"] = preflight(plan, destination)
        if state["actual_version"] != VERSION: raise ValueError("actual version differs from pin")
        for turn in (1, 2):
            files = _snapshot_tree(fixture)
            if _sha(files["bench_check.py"]) != plan["protected_helper_sha256"]: raise ValueError("protected helper changed")
            if turn == 2 and _sha(files["checkout.py"]) != plan["checkout_before_sha256"]: raise ValueError("R1 edited fixture before R2")
            prompt = workload["turns"][turn - 1]["text"]; canary = workload["turns"][turn - 1]["response_canary"]
            argv = [plan["executable"], "run", "--pure", "--model", MODEL, "--format", "json", "--auto", "--dir", str(workspace)]
            if session: argv += ["--session", session]
            argv.append(prompt)
            stdout = destination / f"observer/r{turn}.stdout.jsonl"; stderr = destination / f"observer/r{turn}.stderr.txt"
            launch = {"argv": argv, "environment": env, "cwd": str(workspace), "started_ns": time.time_ns(),
                      "native_files_before": sorted(_snapshot_tree(scratch / "native")), "auth_copied": False}
            _write(destination / f"observer/r{turn}.launch.json", launch)
            state["model_submissions"] += 1
            code = runner(argv, cwd=workspace, env=env, stdout=stdout, stderr=stderr, timeout=180.0)
            raw = stdout.read_bytes(); error = stderr.read_bytes()
            _write(destination / f"observer/r{turn}.exit.json", {"returncode": code, "ended_ns": time.time_ns(), "stdout_sha256": _sha(raw), "stderr_sha256": _sha(error)})
            if code != 0 or _STOP.search(error.decode(errors="replace")): raise ValueError("runtime auth/quota/network/command stop; no fallback")
            if len(raw) > 32 * 1024 * 1024: raise ValueError("stdout byte limit")
            rows = [_strict_json(line, "live stdout") for line in raw.splitlines() if line.strip()]
            if any(not isinstance(row, dict) for row in rows): raise ValueError("stdout event must be an object")
            if any(row.get("type") == "error" for row in rows): raise ValueError("runtime error event; no fallback")
            observation = observe_stdout(raw, expected_session_id=session, response_canaries=[canary], returncode=code)
            visible = [row.get("part", row).get("text") for row in rows if row.get("type") == "text"]
            if not any(isinstance(text, str) and text.rstrip().endswith(canary) for text in visible): raise ValueError("no exact completed response canary")
            response_positions = [i for i, row in enumerate(rows) if row.get("type") == "text"
                                  and isinstance(row.get("part", row).get("text"), str)
                                  and row.get("part", row)["text"].rstrip().endswith(canary)]
            stop_positions = [i for i, row in enumerate(rows) if row.get("type") == "step_finish"
                              and row.get("part", row).get("reason") == "stop"]
            if len(response_positions) != 1 or len(stop_positions) != 1 or stop_positions[0] <= response_positions[0]:
                raise ValueError("response has no unique final stop boundary")
            session = observation.session_id
            state["turns"][str(turn)] = {"session_id": session, "stdout_sha256": _sha(raw), "returncode": code, "environment_safe": env}
            if ".survival-observer.jsonl" in _snapshot_tree(fixture):
                (destination / f"observer/helper-ledger-r{turn}.jsonl").write_bytes((fixture / ".survival-observer.jsonl").read_bytes())
            (destination / f"observer/checkout-after-r{turn}.py").write_bytes((fixture / "checkout.py").read_bytes())
        files = _snapshot_tree(fixture)
        if _sha(files["bench_check.py"]) != plan["protected_helper_sha256"]: raise ValueError("protected helper changed")
        ledger = files[".survival-observer.jsonl"].decode()
        helper = [json.loads(line) for line in ledger.splitlines() if line.strip()]
        if [(row["phase"], row["exit_code"]) for row in helper] != [("inspect", 0), ("baseline", 1), ("final", 0)]:
            raise ValueError("two-turn helper sequence incomplete")
        native = scratch / "native"; first = _snapshot_tree(native); time.sleep(0.25)
        if first != _snapshot_tree(native): raise ValueError("native family not quiescent")
        if set(first) != {"opencode.db", "opencode.db-wal", "opencode.db-shm"}: raise ValueError("native family inventory incomplete or expanded")
        captured = capture_sqlite_bundle(native / "opencode.db", destination / "native-bundle", barrier=lambda: {"quiescent": True, "writer_alive": False})
        with tempfile.TemporaryDirectory(prefix="bench-opencode-native-check-") as temp:
            clone = Path(temp)
            for name, raw in _snapshot_tree(destination / "native-bundle").items(): (clone / name).write_bytes(raw)
            if read_sqlite_session_ids(clone / "opencode.db") != (session,): raise ValueError("native singleton session differs from stdout")
        decoded = decode_opencode_bundle(destination / "native-bundle", session_id=session)
        observer = build_opencode_live_observer(workload=workload, controller_state={**state, "workspace": str(workspace), "configuration": MODEL},
            stdout_by_turn={i: (destination / f"observer/r{i}.stdout.jsonl").read_text() for i in (1, 2)},
            helper_ledger_jsonl=ledger, before_checkout_sha256=plan["checkout_before_sha256"], after_checkout_sha256=_sha(files["checkout.py"]))
        _write(destination / "observer.json", observer); _write(destination / "decoded.json", decoded)
        _write(destination / "native-manifest.json", {"schema_version": SCHEMA, "session_id": session, "source_database": str(native / "opencode.db"),
            "files": [artifact.__dict__ for artifact in captured.artifacts], "quiescent": True, "exact_native_family": True, "auth_copied": False})
        state.update(status="captured_pending_qualification", session_id=session, native_decoder_supported=decoded.get("supported"),
                     checkout_after_sha256=_sha(files["checkout.py"]), root_launch_proof="observer/r{1,2}.launch.json + fresh plan roots + native-manifest.json")
    except Exception as error:
        state["reason_ids"].append(str(error))
    _write(destination / "capture-result.json", state)
    return state
