"""Single-use two-turn Cursor CLI capture in fresh benchmark-owned roots.

No scoring or publishing occurs here. The independent stdout/helper inputs are
retained separately from the session-specific native transcript/ACP store.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Callable

from .native_replay import _open_directory, _snapshot_tree
from .workload_instance import instantiate_workload
from .normal_root_capture import inventory_normal_root, identify_new_family, wait_for_family_quiescence, copy_verified_family, NormalRootCaptureError

SCHEMA = "session-bench-cursor-cli-live-capture-v1"
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_MAX_STDOUT_BYTES = 32 * 1024 * 1024
_STOP_TEXT = re.compile(r"usage limit|quota exceeded|rate.?limit|unauthenticated|authentication failed|not logged in|sign in required|payment required", re.I)


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    with Path(path).open("xb") as handle:
        handle.write(raw)


def _replace_state(path, value):
    # Only this newly-created mutable controller state is updated. Retained
    # stdout/native snapshots are always exclusive-create immutable files.
    temporary = path.with_name("controller-state.pending")
    _write(temporary, value);temporary.replace(path)


def prepare_cursor_capture(
    run_root: Path | str, *, attempt_id: str, repetition: int,
    repository: Path | str, model: str = "auto", executable: str = "agent", build: str = "unverified", normal_root: str | Path | None = None,
) -> dict:
    """Create a concrete fresh-root plan without any model submission."""
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ValueError("repetition must be1,2or3")
    if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", attempt_id):
        raise ValueError("attempt_id must be a bounded run slug")
    if not isinstance(model, str) or not model.strip() or not isinstance(executable, str) or not executable.strip():
        raise ValueError("explicit executable/model is required")
    run_root, repository = Path(run_root).absolute(), Path(repository).absolute()
    if run_root.exists() or run_root.is_symlink():
        raise ValueError("capture destination already exists")
    # All ancestors must already exist and be ordinary directories; do not
    # resolve symlinks into a different project or an account persistence root.
    descriptor = _open_directory(run_root.parent);os.close(descriptor)
    permitted = repository / "artifacts/survival-v1-runs"
    try:
        relative = run_root.relative_to(permitted)
    except ValueError as error:
        raise ValueError("capture must be beneath this repository's survival-v1-runs") from error
    if len(relative.parts) != 1 or relative.name != attempt_id:
        raise ValueError("capture directory must be one fresh attempt beneath survival-v1-runs")
    workload_root = repository / "fixtures/scenarios/survival-v1/workload"
    workload, environment = instantiate_workload(json.loads((workload_root / "workload.json").read_bytes()), attempt_id)
    source = _snapshot_tree(workload_root / "fixture_project")
    if set(source) != {"checkout.py", "bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py"}:
        raise ValueError("unexpected frozen fixture file boundary")
    run_root.mkdir()
    project = run_root / "project";fixture = project / "fixture_project";fixture.mkdir(parents=True)
    # Expected before/after snapshots are observer truth and must never be
    # copied into the model-visible workspace.
    for name in ("checkout.py", "bench_check.py"):
        (fixture / name).write_bytes(source[name])
    for name in ("cursor-config", "cursor-data", "observer", "capture"):
        (run_root / name).mkdir()
    (project / ".cursor").mkdir()
    _write(project / ".cursor/mcp.json", {"mcpServers": {}})
    _write(run_root / "workload-instance.json", workload)
    overrides = environment if normal_root is not None else {**environment, "CURSOR_CONFIG_DIR": str(run_root / "cursor-config"), "CURSOR_DATA_DIR": str(run_root / "cursor-data")}
    _write(run_root / "run-env.json", overrides)
    argv = [executable, "--print", "--output-format", "stream-json", "--workspace", str(project), "--model", model, "--force", "--sandbox", "enabled"]
    plan = {"schema_version": SCHEMA, "configuration_id": "cursor-cli", "attempt_id": attempt_id, "repetition": repetition,
            "model": model, "build": build, "executable": executable, "argv_base": argv,
            "state": "prepared", "model_submissions": 0, "score_eligible": False,
            "persistence_route": "normal-root-new-family" if normal_root is not None else "isolated",
            "normal_root": str(Path(normal_root).absolute()) if normal_root is not None else None,
            "protected_before": {"bench_check.py": hashlib.sha256(source["bench_check.py"]).hexdigest(), "checkout.py": hashlib.sha256(source["checkout.py"]).hexdigest()},
            "scope": "private synthetic collection; root/semantic completeness and scoring remain separately qualified"}
    _write(run_root / "plan.json", plan)
    return plan


def _stream_result(raw: bytes, *, canary: str, expected_session: str | None):
    if len(raw) > _MAX_STDOUT_BYTES:
        raise ValueError("stdout exceeded byte bound")
    text = raw.decode("utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("stdout is not a complete machine-readable stream")
    sessions = {row.get("session_id") for row in rows if row.get("session_id") is not None}
    if len(sessions) != 1 or not all(isinstance(value, str) and _UUID.fullmatch(value) for value in sessions):
        raise ValueError("stdout must bind exactly one native session UUID")
    session = next(iter(sessions))
    if expected_session is not None and session != expected_session:
        raise ValueError("resumed stdout session identity changed")
    if any(row.get("type") == "error" or (row.get("type") == "system" and row.get("subtype") in {"error", "quota", "auth_error"}) for row in rows):
        raise ValueError("runtime error in independent stream")
    results = [row for row in rows if row.get("type") == "result"]
    if len(results) != 1 or results[0].get("is_error") is True or results[0].get("subtype") not in {"success", "completed"}:
        raise ValueError("stdout has no unique completed result boundary")
    result = results[0].get("result")
    if not isinstance(result, str) or not result.rstrip().endswith(canary):
        raise ValueError("completed response lacks exact expected response canary")
    if _STOP_TEXT.search(result):
        raise ValueError("runtime quota/auth stop detected")
    return session


def _run(argv, *, cwd, env, stdout, stderr, timeout):
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
            raise ValueError("model command timed out; no retry or resumed turn allowed")


def execute_cursor_capture(run_root: Path | str, *, timeout: float = 300.0, runner: Callable = _run) -> dict:
    """Submit each frozen turn once, hard-stopping before R2 on any R1 gap.

    The command uses existing subscription authentication. API-key and custom
    endpoint environments are refused so a caller cannot silently switch the
    payment/authentication route. No status/login/logout/update is performed.
    """
    root = Path(run_root).absolute();snapshot = _snapshot_tree(root)
    if "plan.json" not in snapshot or "controller-state.json" in snapshot:
        raise ValueError("capture must be a prepared single-use plan")
    plan = json.loads(snapshot["plan.json"])
    if plan.get("schema_version") != SCHEMA or plan.get("state") != "prepared" or plan.get("attempt_id") != root.name:
        raise ValueError("invalid prepared capture identity")
    if os.environ.get("CURSOR_API_KEY") or os.environ.get("CURSOR_API_ENDPOINT"):
        raise ValueError("existing subscription route required; API-key/custom endpoint overrides are not accepted")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 600:
        raise ValueError("timeout must be bounded at600seconds")
    workload = json.loads(snapshot["workload-instance.json"])
    overrides = json.loads(snapshot["run-env.json"])
    expected_base = [plan["executable"], "--print", "--output-format", "stream-json", "--workspace", str(root / "project"), "--model", plan["model"], "--force", "--sandbox", "enabled"]
    normal = Path(plan["normal_root"]) if plan.get("persistence_route") == "normal-root-new-family" else None
    expected_keys = {"SB_SURVIVAL_V1_RUN_CANARY"} if normal is not None else {"SB_SURVIVAL_V1_RUN_CANARY", "CURSOR_CONFIG_DIR", "CURSOR_DATA_DIR"}
    if plan.get("argv_base") != expected_base or set(overrides) != expected_keys:
        raise ValueError("prepared command or environment boundary changed")
    if ((normal is None and (overrides["CURSOR_CONFIG_DIR"] != str(root / "cursor-config") or overrides["CURSOR_DATA_DIR"] != str(root / "cursor-data")))
            or overrides["SB_SURVIVAL_V1_RUN_CANARY"] != workload.get("run_canary") or workload.get("run_id") != root.name):
        raise ValueError("prepared persistence root or workload identity changed")
    if normal is not None and (os.environ.get("CURSOR_CONFIG_DIR") or os.environ.get("CURSOR_DATA_DIR")):
        raise ValueError("normal-login capture cannot inherit alternate persistence overrides")
    env = {**os.environ, **overrides}
    before_metadata = {}
    normal_containers = {}
    started_ns = time.time_ns()
    if normal is not None:
        # Cursor keys both persistence families to the exact workspace path:
        # chats uses its MD5 and projects its slash-to-dash encoding. These
        # conventions are verified by retained captures. Create only fresh
        # benchmark-owned directories; never traverse unrelated project roots
        # (which may legitimately contain worker sockets or private files).
        project_path = str(root / "project")
        normal_containers = {"chats": normal / "chats" / hashlib.md5(project_path.encode()).hexdigest(),
                             "projects": normal / "projects" / project_path.replace("/", "-").lstrip("-")}
        for container, directory in normal_containers.items():
            descriptor = _open_directory(directory.parent);os.close(descriptor)
            if directory.exists() or directory.is_symlink():
                raise ValueError("normal-root benchmark workspace persistence directory already exists")
            directory.mkdir()
            before_metadata[container] = inventory_normal_root(directory)
        _write(root / "observer/normal-root-before-metadata.private.json", {key: [row.to_dict() for row in rows] for key, rows in before_metadata.items()})
    project = root / "project";fixture = project / "fixture_project"
    before_checkout = (fixture / "checkout.py").read_bytes()
    (root / "observer/checkout.before.py").write_bytes(before_checkout)
    state = {**plan, "state": "collecting", "turns": [], "reason_ids": []}
    _write(root / "controller-state.json", state)
    session = None
    try:
        for turn in (1, 2):
            if hashlib.sha256((fixture / "bench_check.py").read_bytes()).hexdigest() != plan["protected_before"]["bench_check.py"]:
                raise ValueError("protected benchmark helper changed")
            if turn == 2 and hashlib.sha256((fixture / "checkout.py").read_bytes()).hexdigest() != plan["protected_before"]["checkout.py"]:
                raise ValueError("R1 changed checkout before the authorized edit turn")
            prompt = workload["turns"][turn - 1]["text"]
            canary = next(row["value"] for row in workload["response_canaries"] if row["turn_id"] == workload["turns"][turn - 1]["id"])
            argv = list(plan["argv_base"])
            if session:
                argv += ["--resume", session]
            argv.append(prompt)
            stdout, stderr = root / f"observer/turn-r{turn}.stdout.jsonl", root / f"observer/turn-r{turn}.stderr.txt"
            state["model_submissions"] += 1;_replace_state(root / "controller-state.json", state)
            started = time.time_ns()
            code = runner(argv, cwd=project, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
            state["turns"].append({"turn": turn, "started_ns": started, "ended_ns": time.time_ns(), "return_code": code,
                                   "stdout_sha256": hashlib.sha256(stdout.read_bytes()).hexdigest(), "stderr_sha256": hashlib.sha256(stderr.read_bytes()).hexdigest()})
            if code != 0:
                raise ValueError("model command failed; no retry or model switching allowed")
            if _STOP_TEXT.search(stderr.read_text(errors="replace")):
                raise ValueError("runtime quota/auth stop detected")
            session = _stream_result(stdout.read_bytes(), canary=canary, expected_session=session)
            state["session_id"] = session
            ledger = fixture / ".survival-observer.jsonl"
            if ledger.exists():
                (root / f"observer/helper-ledger-r{turn}.jsonl").write_bytes(ledger.read_bytes())
            (root / f"observer/checkout.after-r{turn}.py").write_bytes((fixture / "checkout.py").read_bytes())
            _replace_state(root / "controller-state.json", state)
        if hashlib.sha256((fixture / "bench_check.py").read_bytes()).hexdigest() != plan["protected_before"]["bench_check.py"]:
            raise ValueError("protected benchmark helper changed")
        native = root / "capture/native-private"
        if normal is not None:
            receipts = {}
            copied = {}
            for container in ("chats", "projects"):
                source_root = normal_containers[container]
                after = inventory_normal_root(source_root)
                _write(root / f"observer/normal-root-after-{container}-metadata.private.json", [row.to_dict() for row in after])
                if container == "chats":
                    primary = [row.relative_path for row in after if ("/" + row.relative_path).endswith(f"/{session}/store.db")]
                    companions = [row.relative_path for row in after if f"/{session}/" in ("/" + row.relative_path) and row.relative_path.rsplit("/", 1)[1] in {"meta.json", "store.db-wal", "store.db-shm"}]
                else:
                    primary = [row.relative_path for row in after if ("/" + row.relative_path).endswith(f"/agent-transcripts/{session}/{session}.jsonl")]
                    companions = []
                if len(primary) != 1:
                    raise ValueError("normal-root exact new session family path is missing or ambiguous")
                family = identify_new_family(before_metadata[container], after, primary_path=primary[0], required_companion_paths=companions,
                                             started_ns=started_ns, allow_preexisting_changes=True)
                quiescence = wait_for_family_quiescence(source_root, family, before=before_metadata[container], started_ns=started_ns,
                                                       checks=2, interval_seconds=0.25, allow_preexisting_changes=True)
                destination = root / f"capture/verified-new-{container}"
                artifacts = copy_verified_family(source_root, destination, family)
                copied[container] = _snapshot_tree(destination)
                receipts[container] = {"primary": family.primary_path, "companions": list(family.companion_paths), "quiescence": quiescence.to_dict(), "artifacts": list(artifacts),
                                       "old_file_contents_opened": False, "shared_preexisting_metadata_changes_allowed": True}
            native.mkdir();(native / "acp").mkdir()
            for name, raw in copied["chats"].items():
                (native / "acp" / name.rsplit("/", 1)[-1]).write_bytes(raw)
            (native / "cursor-session.jsonl").write_bytes(next(iter(copied["projects"].values())))
            _write(root / "capture/normal-root-receipts.private.json", receipts)
        else:
            native.mkdir()
            stores = list((root / "cursor-config/chats").glob(f"*/{session}/store.db"))
            transcripts = list((root / "cursor-data/projects").glob(f"*/agent-transcripts/{session}/{session}.jsonl"))
            if len(stores) != 1 or len(transcripts) != 1:
                raise ValueError("isolated session-specific native store/transcript path is missing or ambiguous")
            contents = _snapshot_tree(stores[0].parent)
            if set(contents) - {"store.db", "meta.json", "store.db-wal", "store.db-shm"}:
                raise ValueError("unsupported session-specific ACP companion files")
            (native / "acp").mkdir()
            for name, raw in contents.items():
                (native / "acp" / name).write_bytes(raw)
            # Native transcript reads also refuse symlinked ancestors and changed
            # files. Its explicitly bound session directory must be a closed tree.
            transcript_contents = _snapshot_tree(transcripts[0].parent)
            if set(transcript_contents) != {session + ".jsonl"}:
                raise ValueError("unsupported transcript directory companions")
            (native / "cursor-session.jsonl").write_bytes(transcript_contents[session + ".jsonl"])
        captured = _snapshot_tree(native)
        _write(root / "capture/native-manifest.private.json", {"schema_version": SCHEMA, "session_id": session,
               "files": [{"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)} for name, raw in sorted(captured.items())],
               "complete_native_root": False, "public_safe": False})
        state["state"] = "captured_private_unqualified"
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, StopIteration, NormalRootCaptureError) as error:
        state["state"] = "invalid";state["reason_ids"].append(str(error))
    _replace_state(root / "controller-state.json", state)
    return state
