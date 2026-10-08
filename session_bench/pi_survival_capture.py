"""Bounded, isolated Pi CLI survival-v1 capture using an existing OAuth route.

Preparation never starts a model. Execution uses an explicit temporary session
directory and workspace, and copies the scoped native transcript, both helper
snapshots, stdout/stderr, and launch receipts before the scratch root can be
cleaned. This module does not score or publish a run.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Callable, Mapping

from .adapters.agent_session_native_decoder import NativeDecodeError, decode_agent_native_bytes
from .normal_root_capture import inventory_normal_root
from .workload_instance import instantiate_workload


SCHEMA = "session-bench-pi-survival-capture-v1"
PROVIDER = "openai-codex"
MODEL = "gpt-5.5"
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
_STOP = re.compile(r"authentication required|unauthorized|invalid api key|quota exceeded|rate.?limit|insufficient.*(?:credit|balance)|payment required|not logged in", re.I)
_SYSTEM_PROMPT = (
    "You are completing a synthetic Session-Bench task. Work only in the current "
    "fixture_project directory. Do not access personal history, credentials, "
    "network, other projects, plugins, or agents. Preserve bench_check.py and "
    ".survival-observer.jsonl. Follow the supplied two-turn task exactly."
)
_FIXTURE_FILES = {
    "checkout.py",
    "bench_check.py",
    "snapshots/checkout.before.py",
    "snapshots/checkout.after.py",
}
_ALLOWED_WORKSPACE_FILES = _FIXTURE_FILES | {".survival-observer.jsonl"}


class PiCaptureError(RuntimeError):
    """The requested isolated Pi capture cannot continue safely."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _capture_environment() -> dict:
    """Return the minimal host identity needed to label the acquired run."""
    system = platform.system()
    names = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}
    if system not in names:
        raise PiCaptureError("capture host operating system is unsupported")
    return {"schema_version": "session-bench-pi-capture-host-v1",
            "os_name": names[system], "system": system, "python_platform": sys.platform}


def _json_bytes(raw: bytes, label: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PiCaptureError(f"{label}: duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, PiCaptureError):
            raise
        raise PiCaptureError(f"{label}: invalid JSON") from error


def _write_new(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def _write_json(path: Path, value) -> None:
    _write_new(path, (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False) + "\n").encode())


def _safe_tree(root: Path, *, max_files: int = 1000) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise PiCaptureError("workspace/session root must be an ordinary directory")
    found: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PiCaptureError("symlink encountered in isolated evidence root")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PiCaptureError("special file encountered in isolated evidence root")
        relative = path.relative_to(root).as_posix()
        found[relative] = path.read_bytes()
        if len(found) > max_files:
            raise PiCaptureError("isolated evidence root exceeded file-count limit")
    return found


def _copy_tree_snapshot(files: Mapping[str, bytes], destination: Path) -> list[dict]:
    receipts = []
    destination.mkdir(parents=True, exist_ok=False)
    for relative, raw in sorted(files.items()):
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise PiCaptureError("non-canonical workspace artifact path")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_new(target, raw)
        receipts.append({"path": relative, "sha256": _sha(raw), "size_bytes": len(raw)})
    return receipts


def _capture_native_root_snapshot(root: Path, destination: Path, *, attempt_id: str,
                                  session_id: str, phase: str) -> dict:
    """Copy and bind every ordinary file in the explicitly isolated Pi root."""
    metadata_before = _metadata_map(root)
    files = _safe_tree(root)
    metadata_after = _metadata_map(root)
    if metadata_before != metadata_after or set(metadata_before) != set(files):
        raise PiCaptureError("isolated Pi root changed during full inventory copy")
    entries = []
    for relative, raw in sorted(files.items()):
        metadata = metadata_before[relative]
        if metadata.get("size_bytes") != len(raw):
            raise PiCaptureError("isolated Pi root file size changed during snapshot")
        _write_new(destination / "files" / relative, raw)
        entries.append({"path": relative, "filesystem_id": metadata["filesystem_id"],
                        "size_bytes": len(raw), "sha256": _sha(raw)})
    receipt = {"schema_version": "session-bench-pi-native-root-snapshot-v1",
               "attempt_id": attempt_id, "session_id": session_id, "phase": phase,
               "source_root": str(root), "captured_at_ns": time.time_ns(),
               "entries": entries}
    _write_json(destination / "inventory.json", receipt)
    return receipt


def _run(argv, *, cwd: Path, env: Mapping[str, str], stdout: Path, stderr: Path, timeout: float) -> int:
    with stdout.open("xb") as out, stderr.open("xb") as err:
        process = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdout=out, stderr=err,
                                   start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise PiCaptureError("turn timed out; no retry or fallback") from error


def _preflight(executable: str, env: Mapping[str, str], timeout: float = 30.0) -> dict:
    version = subprocess.run([executable, "--version"], capture_output=True, env=dict(env), timeout=timeout)
    auth = subprocess.run([executable, "auth", "check", "--provider", PROVIDER, "--json", "--no-refresh"],
                          capture_output=True, env=dict(env), timeout=timeout)
    models = subprocess.run([executable, "--list-models", PROVIDER], capture_output=True,
                            env=dict(env), timeout=timeout)
    try:
        auth_doc = _json_bytes(auth.stdout, "Pi auth preflight") if auth.stdout else {}
    except PiCaptureError:
        auth_doc = {"status": "invalid_response"}
    model_lines = models.stdout.decode("utf-8", errors="replace").splitlines()
    model_match = any(re.match(rf"^{re.escape(PROVIDER)}\s+{re.escape(MODEL)}(?:\s|$)", line) for line in model_lines)
    version_text = version.stdout.decode("utf-8", errors="replace").strip()
    ready = (version.returncode == 0 and bool(version_text) and auth.returncode == 0
             and auth_doc.get("status") == "ready" and auth_doc.get("provider") == PROVIDER
             and auth_doc.get("authType") == "oauth" and models.returncode == 0 and model_match)
    return {"ready": ready, "version": version_text, "version_exit_code": version.returncode,
            "auth": auth_doc, "auth_exit_code": auth.returncode,
            "model_catalog_sha256": _sha(models.stdout), "model_catalog_exit_code": models.returncode,
            "model_catalog_match": MODEL if model_match else None,
            "preflight_failure": None if ready else "existing OAuth route or pinned model unavailable"}


def prepare_pi_capture(destination: Path | str, *, repository: Path | str, repetition: int,
                       executable: str = "pi") -> dict:
    """Create a fresh capture plan without launching Pi or reading sessions."""
    destination = Path(destination).absolute()
    repository = Path(repository).resolve()
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ValueError("repetition must be 1, 2, or 3")
    if destination.exists() or destination.is_symlink():
        raise ValueError("capture destination must be new")
    if not re.fullmatch(r"pi-[A-Za-z0-9_-]+", destination.name):
        raise ValueError("attempt ID must start with pi- and use bounded ASCII characters")
    if destination.parent != repository / "artifacts/v1-expanded-preparation/live-captures":
        raise ValueError("capture destination must be in the repository live-captures directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="session-bench-pi-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)).absolute()
    if repository == scratch or repository in scratch.parents:
        raise PiCaptureError("scratch must be outside the repository")
    workspace = scratch / "workspace"
    fixture = workspace / "fixture_project"
    session_root = scratch / "pi-native-source"
    fixture.mkdir(parents=True)
    session_root.mkdir()
    source = repository / "fixtures/scenarios/survival-v1/workload"
    source_files = _safe_tree(source / "fixture_project")
    if set(source_files) != _FIXTURE_FILES:
        raise PiCaptureError("frozen survival fixture tree has unexpected or missing files")
    for name, raw in source_files.items():
        target = fixture / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    template_raw = (source / "workload.json").read_bytes()
    template = _json_bytes(template_raw, "checked-in survival workload")
    workload, _ = instantiate_workload(template, destination.name)
    destination.mkdir(parents=True)
    for name in ("observer", "native", "workspaces", "native-root"):
        (destination / name).mkdir()
    _write_new(destination / "workload-template.json", template_raw)
    _write_json(destination / "workload-instance.json", workload)
    _copy_tree_snapshot(source_files, destination / "workspaces/before/fixture_project")
    env_keys = ("PATH", "HOME", "LANG", "TMPDIR")
    environment = {key: os.environ[key] for key in env_keys if os.environ.get(key)}
    environment["SB_SURVIVAL_V1_RUN_CANARY"] = workload["run_canary"]
    environment["PI_TELEMETRY"] = "0"
    capture_environment = _capture_environment()
    plan = {
        "schema_version": SCHEMA, "attempt_id": destination.name,
        "repetition": repetition, "configuration_id": "pi",
        "provider": PROVIDER, "model": MODEL, "executable": executable,
        "capture_environment": capture_environment,
        "scratch": str(scratch), "workspace": str(workspace),
        "fixture": str(fixture), "session_dir": str(session_root),
        "session_id": str(uuid.uuid4()), "environment_keys": sorted(environment),
        "environment_overrides": {"PI_TELEMETRY": "0", "run_canary": workload["run_canary"]},
        "model_submissions": 0, "status": "prepared", "score_eligible": False,
        "stdout_protocol": "pi-json-session-events-v1",
        "auth_route": "existing Pi openai-codex OAuth; checked without refresh before R1",
        "no_session_history_read": True, "no_extensions": True, "no_context_files": True,
        "protected_sha256": {name: _sha(raw) for name, raw in source_files.items()},
        "workload_sha256": _sha(template_raw),
    }
    _write_json(destination / "plan.json", plan)
    return plan


def _metadata_map(root: Path) -> dict[str, dict]:
    rows = inventory_normal_root(root)
    return {row.relative_path: row.to_dict() for row in rows}


def _session_file_for_id(session_root: Path, expected_id: str, before: Mapping[str, Mapping],
                         expected_relative: str | None = None) -> tuple[str, bytes, dict]:
    after = _metadata_map(session_root)
    changed_old = [path for path in set(after) & set(before)
                   if after[path].get("filesystem_id") != before[path].get("filesystem_id")]
    if changed_old:
        raise PiCaptureError("unexpected pre-existing native files changed identity")
    if expected_relative is None:
        new_paths = sorted(set(after) - set(before))
        if not new_paths or any(not path.endswith(".jsonl") for path in new_paths):
            raise PiCaptureError("native persistence family contains unexpected new files")
        candidates = [path for path in new_paths if Path(path).name.endswith(".jsonl")]
        if len(candidates) != 1 or len(new_paths) != 1:
            raise PiCaptureError("native session family is missing or ambiguous")
        relative = candidates[0]
    else:
        if set(after) != set(before) or expected_relative not in after:
            raise PiCaptureError("resumed native session family changed path inventory")
        relative = expected_relative
    native_path = session_root / relative
    raw = native_path.read_bytes()
    final = _metadata_map(session_root).get(relative)
    if final is None or final.get("filesystem_id") != after[relative].get("filesystem_id") or final.get("size_bytes") != len(raw):
        raise PiCaptureError("native session changed during evidence copy")
    header = _json_bytes(raw.splitlines()[0], "Pi native session header")
    if header.get("type") != "session" or header.get("id") != expected_id:
        raise PiCaptureError("native session header does not match the explicit session ID")
    return relative, raw, final


def _safe_stdout(raw: bytes, canary: str, label: str, *, expected_session_id: str) -> None:
    if len(raw) > 16 * 1024 * 1024:
        raise PiCaptureError(f"{label} stdout exceeded byte limit")
    text = raw.decode("utf-8", errors="strict")
    rows = [_json_bytes(line, f"{label} Pi JSON stdout event") for line in raw.splitlines()]
    if not rows or not isinstance(rows[0], dict) or rows[0].get("type") != "session" or rows[0].get("id") != expected_session_id:
        raise PiCaptureError(f"{label} JSON stdout header does not bind the explicit Pi session")
    if any(not isinstance(row, dict) for row in rows):
        raise PiCaptureError(f"{label} JSON stdout contains a non-object event")
    finals = [row.get("message") for row in rows
              if row.get("type") == "message_end" and isinstance(row.get("message"), dict)
              and row["message"].get("role") == "assistant"]
    text_parts = [part.get("text") for message in finals if isinstance(message.get("content"), list)
                  for part in message["content"] if isinstance(part, dict) and part.get("type") == "text"
                  and isinstance(part.get("text"), str)]
    if not text_parts or not text_parts[-1].rstrip().endswith(canary):
        raise PiCaptureError(f"{label} visible response lacks the exact completed JSON event canary")
    if _STOP.search(text):
        raise PiCaptureError(f"{label} response indicates auth or quota stop")


def _ledger_rows(path: Path, *, label: str) -> list[dict]:
    raw = path.read_bytes()
    if len(raw) > 1024 * 1024:
        raise PiCaptureError(f"{label} helper ledger exceeded byte limit")
    rows = []
    for line in raw.splitlines():
        if not line:
            raise PiCaptureError(f"{label} helper ledger contains an empty row")
        row = _json_bytes(line, f"{label} helper ledger row")
        if not isinstance(row, dict):
            raise PiCaptureError(f"{label} helper ledger row is not an object")
        rows.append(row)
    return rows


def _validate_helper_rows(rows: list[dict], workload: Mapping, phases: tuple[str, ...], *, label: str) -> None:
    helper = workload["helper"]
    if len(rows) != len(phases):
        raise PiCaptureError(f"{label} helper ledger has missing or extra phase rows")
    canary = workload["run_canary"]
    expected_codes = {"inspect": 0, "baseline": 1, "final": 0}
    for row, phase in zip(rows, phases, strict=True):
        nonce = helper["nonces"][phase]
        expected_output_prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} "
        if (row.get("schema_version") != "1.0-survival-helper-ledger"
                or row.get("phase") != phase
                or row.get("run_canary") != canary
                or row.get("helper_nonce") != nonce
                or row.get("id") != f"helper-{phase}-{nonce}"
                or row.get("argv") != ["python3", "bench_check.py", phase]
                or row.get("cwd") != "fixture_project"
                or row.get("exit_code") != expected_codes[phase]
                or not isinstance(row.get("output"), str)
                or not row["output"].startswith(expected_output_prefix)):
            raise PiCaptureError(f"{label} helper ledger does not prove exact {phase} execution")


def _evidence_integrity(destination: Path, plan: Mapping, workload: Mapping) -> dict:
    checks = []
    for turn in (1, 2):
        prefix = destination / f"turn-r{turn}"
        for name in ("stdout.txt", "stderr.txt", "launch.json", "native/session.jsonl", "workspace/fixture_project/bench_check.py"):
            path = prefix / name
            if path.is_symlink() or not path.is_file():
                raise PiCaptureError(f"required copied evidence missing: {path.relative_to(destination)}")
        native = (prefix / "native/session.jsonl").read_bytes()
        result = decode_agent_native_bytes("pi", native)
        if result.session_id != plan.get("session_id"):
            raise PiCaptureError("copied native session identity differs from plan")
        copied_workspace = prefix / "workspace/fixture_project"
        ledger = copied_workspace / ".survival-observer.jsonl"
        if not ledger.is_file() or ledger.is_symlink():
            raise PiCaptureError("helper ledger was not copied before scratch cleanup")
        copied_files = _safe_tree(copied_workspace)
        if set(copied_files) != _ALLOWED_WORKSPACE_FILES and not (
                turn == 1 and set(copied_files) == _FIXTURE_FILES | {".survival-observer.jsonl"}):
            raise PiCaptureError("copied workspace does not contain the complete frozen fixture and declared observer")
        for key, digest in plan["protected_sha256"].items():
            if key != "checkout.py" and _sha(copied_files.get(key, b"")) != digest:
                raise PiCaptureError(f"protected fixture bytes differ from the frozen fixture: {key}")
        phases = ("inspect", "baseline") if turn == 1 else ("inspect", "baseline", "final")
        _validate_helper_rows(_ledger_rows(ledger, label=f"R{turn}"), workload, phases, label=f"R{turn}")
        checks.append({"turn": turn, "native_sha256": _sha(native), "native_status": result.status,
                       "workspace_files": sorted(path.relative_to(prefix / "workspace").as_posix()
                                                  for path in (prefix / "workspace").rglob("*") if path.is_file()),
                       "helper_sha256": _sha(ledger.read_bytes())})
    root_receipt = _json_bytes((destination / "native-root/root-capture.json").read_bytes(),
                               "Pi isolated native-root receipt")
    phases = ("before-r1", "after-r1", "after-r2")
    if (root_receipt.get("schema_version") != "session-bench-pi-native-root-capture-v1"
            or root_receipt.get("attempt_id") != plan["attempt_id"]
            or root_receipt.get("session_id") != plan["session_id"]
            or root_receipt.get("session_root") != plan["session_dir"]
            or root_receipt.get("discovery_mode") != "isolated"
            or root_receipt.get("pre_run_empty") is not True
            or root_receipt.get("personal_history_scanned") is not False
            or root_receipt.get("required_companions") != []):
        raise PiCaptureError("Pi isolated native-root receipt does not bind the explicit capture boundary")
    snapshots = root_receipt.get("snapshots")
    if not isinstance(snapshots, list) or [row.get("phase") for row in snapshots] != list(phases):
        raise PiCaptureError("Pi isolated native-root snapshots are incomplete or unordered")
    root_rows = []
    for item in snapshots:
        inventory_raw = (destination / item["inventory_path"]).read_bytes()
        if _sha(inventory_raw) != item.get("inventory_sha256"):
            raise PiCaptureError("Pi isolated native-root inventory digest mismatch")
        inventory = _json_bytes(inventory_raw, "Pi isolated native-root inventory")
        if (inventory.get("schema_version") != "session-bench-pi-native-root-snapshot-v1"
                or inventory.get("attempt_id") != plan["attempt_id"]
                or inventory.get("session_id") != plan["session_id"]
                or inventory.get("phase") != item["phase"]
                or inventory.get("source_root") != plan["session_dir"]):
            raise PiCaptureError("Pi isolated native-root inventory identity mismatch")
        entries = inventory.get("entries")
        if not isinstance(entries, list):
            raise PiCaptureError("Pi isolated native-root entries are malformed")
        copied_paths = set()
        for entry in entries:
            relative = entry.get("path")
            if not isinstance(relative, str) or not relative or relative.startswith("/") or ".." in Path(relative).parts or relative in copied_paths:
                raise PiCaptureError("Pi isolated native-root path is invalid or duplicated")
            copied_paths.add(relative)
            raw = (destination / f"native-root/{item['phase']}/files" / relative).read_bytes()
            if len(raw) != entry.get("size_bytes") or _sha(raw) != entry.get("sha256"):
                raise PiCaptureError("Pi isolated native-root copy differs from its inventory")
        if item["phase"] == "before-r1" and entries:
            raise PiCaptureError("Pi isolated native-root was not empty before R1")
        root_rows.append(entries)
    if (len(root_rows[1]) != 1 or len(root_rows[2]) != 1
            or root_rows[1][0]["path"] != root_rows[2][0]["path"]
            or root_rows[1][0]["filesystem_id"] != root_rows[2][0]["filesystem_id"]
            or root_rows[2][0]["sha256"] != _sha((destination / "turn-r2/native/session.jsonl").read_bytes())):
        raise PiCaptureError("Pi isolated native-root changed identity or differs from its final copied session")
    return {"verified": True, "turns": checks, "score_eligible": False,
            "native_root": {"complete": True, "root_files": 1,
                            "root_capture_sha256": _sha((destination / "native-root/root-capture.json").read_bytes())}}


def execute_pi_capture(destination: Path | str, *, timeout: float = 300.0,
                       preflight: Callable = _preflight, runner: Callable = _run) -> dict:
    """Submit R1/R2 once each on the explicit existing OAuth route."""
    destination = Path(destination).absolute()
    plan = _json_bytes((destination / "plan.json").read_bytes(), "Pi capture plan")
    workload = _json_bytes((destination / "workload-instance.json").read_bytes(), "Pi workload")
    if plan.get("schema_version") != SCHEMA or plan.get("status") != "prepared" or plan.get("attempt_id") != destination.name:
        raise PiCaptureError("capture is not a fresh prepared plan")
    if plan.get("capture_environment") != _capture_environment():
        raise PiCaptureError("capture operating system differs from the pinned pre-run plan")
    if any((destination / f"turn-r{turn}").exists() for turn in (1, 2)) or (destination / "capture-result.json").exists():
        raise PiCaptureError("capture is single-use")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 600:
        raise ValueError("timeout must be bounded at 600 seconds")
    scratch = Path(plan["scratch"]); workspace = Path(plan["workspace"]); fixture = Path(plan["fixture"]); session_root = Path(plan["session_dir"])
    if scratch.resolve() == Path(__file__).resolve().parents[1] or Path(__file__).resolve().parents[1] in scratch.resolve().parents:
        raise PiCaptureError("scratch unexpectedly overlaps the repository")
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "TMPDIR") if os.environ.get(key)}
    env.update({"PI_TELEMETRY": "0", "SB_SURVIVAL_V1_RUN_CANARY": workload["run_canary"]})
    if sorted(env) != plan["environment_keys"]:
        raise PiCaptureError("launch environment boundary changed after preparation")
    state = {"schema_version": SCHEMA, "attempt_id": destination.name, "status": "attempted",
             "provider": PROVIDER, "model": MODEL, "model_submissions": 0,
             "session_id": plan["session_id"], "capture_environment": plan["capture_environment"],
             "turns": [], "score_eligible": False}
    _write_json(destination / "controller-state.json", state)
    session_metadata: dict[str, dict] = {}
    session_relative = None
    try:
        preflight_receipt = preflight(plan["executable"], env)
        _write_json(destination / "preflight.json", preflight_receipt)
        state["preflight"] = preflight_receipt
        if preflight_receipt.get("ready") is not True:
            raise PiCaptureError("preflight did not prove the exact openai-codex OAuth/model route; no model call submitted")
        session_metadata = _metadata_map(session_root)
        if session_metadata:
            raise PiCaptureError("explicit Pi session directory was not empty before R1")
        root_snapshots = [_capture_native_root_snapshot(
            session_root, destination / "native-root/before-r1", attempt_id=destination.name,
            session_id=plan["session_id"], phase="before-r1")]
        if root_snapshots[0]["entries"]:
            raise PiCaptureError("explicit Pi session directory was not empty before its first snapshot")
        for turn in (1, 2):
            current_files = _safe_tree(fixture)
            if _sha(current_files.get("bench_check.py", b"")) != plan["protected_sha256"]["bench_check.py"]:
                raise PiCaptureError("protected fixture helper changed")
            for relative, digest in plan["protected_sha256"].items():
                if relative != "checkout.py" and _sha(current_files.get(relative, b"")) != digest:
                    raise PiCaptureError(f"protected fixture changed: {relative}")
            if turn == 1 and _sha(current_files.get("checkout.py", b"")) != plan["protected_sha256"]["checkout.py"]:
                raise PiCaptureError("checkout changed before R1")
            if turn == 2 and _sha(current_files.get("checkout.py", b"")) != plan["protected_sha256"]["checkout.py"]:
                raise PiCaptureError("R1 changed checkout before the authorized R2 edit")
            prompt_row = workload["turns"][turn - 1]
            prompt = prompt_row["text"]
            canary = prompt_row["response_canary"]
            turn_dir = destination / f"turn-r{turn}"
            for name in ("native", "workspace"):
                (turn_dir / name).mkdir(parents=True)
            prompt_path = destination / f"observer/prompt-r{turn}.txt"
            _write_new(prompt_path, prompt.encode("utf-8"))
            argv = [plan["executable"], "--provider", PROVIDER, "--model", MODEL,
                    "--session-dir", str(session_root), "--session-id", plan["session_id"],
                    "--mode", "json",
                    "--system-prompt", _SYSTEM_PROMPT, "--no-extensions", "--no-skills",
                    "--no-context-files", "--no-prompt-templates", "--no-themes",
                    "--tools", "read,bash,edit,write", "--approve", "--print", "--", prompt]
            launch = {"argv": argv, "cwd": str(workspace), "environment_keys": sorted(env),
                      "environment_overrides": plan["environment_overrides"], "started_ns": time.time_ns(),
                      "provider": PROVIDER, "model": MODEL, "stdout_protocol": plan["stdout_protocol"],
                      "prompt_sha256": _sha(prompt.encode()),
                      "session_id": plan["session_id"], "auth_not_copied": True}
            _write_json(turn_dir / "launch.json", launch)
            state["model_submissions"] += 1
            state["turns"].append({"turn": turn, "status": "running", "launch": launch})
            (destination / "controller-state.json.tmp").write_text(json.dumps(state, indent=2) + "\n")
            (destination / "controller-state.json.tmp").replace(destination / "controller-state.json")
            stdout, stderr = turn_dir / "stdout.txt", turn_dir / "stderr.txt"
            return_code = runner(argv, cwd=workspace, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
            out_raw, err_raw = stdout.read_bytes(), stderr.read_bytes()
            _write_json(turn_dir / "exit.json", {"returncode": return_code, "ended_ns": time.time_ns(),
                                                  "stdout_sha256": _sha(out_raw), "stderr_sha256": _sha(err_raw)})
            state["turns"][-1].update({"status": "completed" if return_code == 0 else "failed",
                                       "returncode": return_code, "stdout_sha256": _sha(out_raw),
                                       "stderr_sha256": _sha(err_raw)})
            if return_code != 0 or _STOP.search(err_raw.decode("utf-8", errors="replace")):
                raise PiCaptureError(f"R{turn} stopped on command/auth/quota failure; no retry or fallback")
            _safe_stdout(out_raw, canary, f"R{turn}", expected_session_id=plan["session_id"])
            session_relative, native_raw, native_stat = _session_file_for_id(
                session_root, plan["session_id"], session_metadata,
                expected_relative=session_relative if turn == 2 else None)
            session_metadata = _metadata_map(session_root)
            root_snapshots.append(_capture_native_root_snapshot(
                session_root, destination / f"native-root/after-r{turn}",
                attempt_id=destination.name, session_id=plan["session_id"],
                phase=f"after-r{turn}"))
            _write_new(turn_dir / "native/session.jsonl", native_raw)
            _write_json(turn_dir / "native/receipt.json", {"relative_path": session_relative,
                                                              "filesystem_id": native_stat["filesystem_id"],
                                                              "size_bytes": len(native_raw), "sha256": _sha(native_raw),
                                                              "metadata_only_before_after": True})
            current_files = _safe_tree(fixture)
            unexpected = set(current_files) - _ALLOWED_WORKSPACE_FILES
            if unexpected:
                raise PiCaptureError("workspace wrote files outside the declared fixture boundary")
            _copy_tree_snapshot(current_files, turn_dir / "workspace/fixture_project")
            if turn == 1:
                _validate_helper_rows(
                    _ledger_rows(turn_dir / "workspace/fixture_project/.survival-observer.jsonl", label="R1"),
                    workload, ("inspect", "baseline"), label="R1",
                )
            state["turns"][-1].update({"native_sha256": _sha(native_raw),
                                       "native_status": decode_agent_native_bytes("pi", native_raw).status,
                                       "workspace_files": sorted(current_files)})
            (destination / "controller-state.json.tmp").write_text(json.dumps(state, indent=2) + "\n")
            (destination / "controller-state.json.tmp").replace(destination / "controller-state.json")
        _write_json(destination / "native-root/root-capture.json", {
            "schema_version": "session-bench-pi-native-root-capture-v1",
            "attempt_id": destination.name, "session_id": plan["session_id"],
            "session_root": str(session_root), "discovery_mode": "isolated",
            "pre_run_empty": True, "personal_history_scanned": False,
            "required_companions": [],
            "snapshots": [{"phase": receipt["phase"],
                           "inventory_path": f"native-root/{'before-r1' if receipt['phase'] == 'before-r1' else receipt['phase']}/inventory.json",
                           "inventory_sha256": _sha((destination / f"native-root/{'before-r1' if receipt['phase'] == 'before-r1' else receipt['phase']}/inventory.json").read_bytes())}
                          for receipt in root_snapshots],
        })
        integrity = _evidence_integrity(destination, plan, workload)
        state.update(status="captured_pending_qualification", evidence_integrity=integrity,
                     scratch_retained=True, independent_reproduction=False)
    except Exception as error:
        state.update(status="capture_incomplete", failure=str(error), scratch_retained=True)
    _write_json(destination / "capture-result.json", state)
    return state


def cleanup_pi_scratch(destination: Path | str) -> None:
    """Remove only this controller's scratch after every copied input verifies."""
    destination = Path(destination).absolute()
    plan = _json_bytes((destination / "plan.json").read_bytes(), "Pi capture plan")
    result = _json_bytes((destination / "capture-result.json").read_bytes(), "Pi capture result")
    if result.get("status") != "captured_pending_qualification" or result.get("evidence_integrity", {}).get("verified") is not True:
        raise PiCaptureError("scratch cleanup requires verified copies of native, helper, workspace, stdout, and stderr")
    scratch = Path(plan["scratch"])
    if scratch.is_symlink() or not scratch.is_dir() or scratch == Path("/") or Path(__file__).resolve().parents[1] in scratch.resolve().parents:
        raise PiCaptureError("refusing unsafe scratch cleanup path")
    shutil.rmtree(scratch)
