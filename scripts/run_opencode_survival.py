#!/usr/bin/env python3
"""Bounded local controller for OpenCode survival-v1 attempts.

Three explicit phases, no automatic retry, no live calls at import time:

1. ``prepare`` -- build a fresh run root from the frozen campaign inputs.
2. ``submit``  -- run exactly one OpenCode submission per invocation.
3. ``capture`` -- capture the SQLite bundle and decode it offline.

The controller captures evidence only. It never scores and never publishes
(``score_eligible`` stays ``false``). It never executes ``bench_check.py``;
semantic verification parses and executes only the synthetic
``project/fixture_project/checkout.py`` in memory.

Run layout (the frozen prompts address ``fixture_project`` as a child path,
so the workspace must be its parent)::

    <run>/project/fixture_project/...   # only copied tree; helper/ledger/checkout checks
    <run>/project                  # OpenCode --dir and subprocess cwd

Security boundaries enforced here:

* run roots must live inside ``<repo>/artifacts/survival-v1-runs``;
* symlinks, existing roots, unknown attempt IDs, wrong phase order,
  prompt/model overrides, and normal OpenCode data roots are rejected;
* state records only the safe environment keys (``OPENCODE_DB`` and the
  per-run canary) and the exact argv with the prompt redacted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session_bench.adapters.opencode_cli import (  # noqa: E402
    OPENCODE_DATABASE_NAME,
    OPENCODE_DEFAULT_MODEL,
    OPENCODE_EXECUTABLE,
    OpenCodeIdentity,
    capture_sqlite_bundle,
    observe_stdout,
    proposed_environment,
    read_sqlite_session_ids,
    validate_model_id,
)
from session_bench.adapters.opencode_decoder import decode_opencode_bundle  # noqa: E402
from session_bench.survival_campaign import (  # noqa: E402
    quota_allows_submission,
    read_campaign,
    validate_frozen_inputs,
)
from session_bench.workload_instance import (  # noqa: E402
    RUN_CANARY_ENV,
    instantiate_workload,
)


CONTROLLER_SCHEMA_VERSION = "1.0-survival-opencode-controller"
OPENCODE_VERSION_PIN = "1.18.30"
RUNS_DIRNAME = Path("artifacts") / "survival-v1-runs"
CAMPAIGN_RELATIVE_PATH = Path("plans") / "survival-v1" / "campaign.json"
WORKLOAD_RELATIVE_PATH = (
    Path("fixtures") / "scenarios" / "survival-v1" / "workload" / "workload.json"
)
FIXTURE_PROJECT_NAME = "fixture_project"
PROJECT_DIRNAME = "project"
PROTECTED_HELPER_NAME = "bench_check.py"
LEDGER_NAME = ".survival-observer.jsonl"
STATE_NAME = "controller-state.json"
CAPTURE_DIRNAME = "capture"
OFFLINE_DIRNAME = "offline-bundle"
DECODED_CURRENT_NAME = "decoded-current.json"
CONFIGURATION_ID = "opencode-cli"
SUBPROCESS_TIMEOUT_SECONDS = 300.0

# Normal OpenCode profile roots are forbidden as run roots. The runs-base
# restriction already confines roots, but this is a second explicit barrier.
FORBIDDEN_ROOT_SUFFIXES = (
    Path(".local") / "share" / "opencode",
    Path(".config") / "opencode",
    Path("Library") / "Application Support" / "opencode",
    Path("Library") / "Application Support" / "OpenCode",
)

# Semantic cases mirror the frozen workload's final success rule
# (R2: delivery is free when subtotal >= 50, else 5). They are hardcoded here
# so the controller never needs to read, let alone execute, bench_check.py.
# bench_check.py is only hashed (protected) and its ledger is only read.
SEMANTIC_CASES = (
    {"items": [[10, 2], [5, 1]], "expected": 30},
    {"items": [[25, 2]], "expected": 50},
    {"items": [[49, 1]], "expected": 54},
)

SAFE_ENV_KEYS = ("OPENCODE_DB", RUN_CANARY_ENV)


class ControllerError(Exception):
    """A fail-closed controller refusal."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _runs_base(repo_root: Path) -> Path:
    return repo_root / RUNS_DIRNAME


def _strict_json_loads(text: str, label: str) -> Any:
    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label}: non-finite JSON number {value!r}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ControllerError(f"{label} is not strict JSON: {exc}") from exc


def _is_forbidden_root(resolved: Path, home: Path) -> bool:
    for suffix in FORBIDDEN_ROOT_SUFFIXES:
        root = home / suffix
        if resolved == root or root in resolved.parents:
            return True
    return False


def _check_no_symlink_prefix(path: Path) -> None:
    """Reject any existing path prefix (including the target) that is a symlink."""
    current = path.expanduser()
    prefixes = [current, *current.parents]
    for prefix in prefixes:
        try:
            info = prefix.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ControllerError(f"cannot inspect path prefix {prefix}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ControllerError(f"symlink in run root path is forbidden: {prefix}")


def _resolve_new_root(repo_root: Path, raw: str) -> Path:
    """Resolve a prepare root and require it to be new, bound, and ordinary."""
    if not isinstance(raw, str) or not raw.strip():
        raise ControllerError("root must be a non-empty path")
    candidate = Path(raw).expanduser()
    _check_no_symlink_prefix(candidate)
    if candidate.exists() or candidate.is_symlink():
        raise ControllerError("run root already exists; prepare requires a new root")
    resolved = candidate.resolve()
    base = _runs_base(repo_root).resolve(strict=False)
    if resolved == base or base not in resolved.parents:
        raise ControllerError(
            f"run root must be inside {RUNS_DIRNAME.as_posix()} (got {resolved})"
        )
    if _is_forbidden_root(resolved, Path.home()):
        raise ControllerError("normal OpenCode data roots are forbidden as run roots")
    return resolved


def _resolve_existing_root(repo_root: Path, raw: str) -> Path:
    """Resolve a submit/capture root and require it to be the bound ordinary dir."""
    if not isinstance(raw, str) or not raw.strip():
        raise ControllerError("root must be a non-empty path")
    candidate = Path(raw).expanduser()
    _check_no_symlink_prefix(candidate)
    if not candidate.exists() or candidate.is_symlink() or not candidate.is_dir():
        raise ControllerError("run root must be an existing ordinary directory")
    resolved = candidate.resolve()
    base = _runs_base(repo_root).resolve(strict=False)
    if resolved == base or base not in resolved.parents:
        raise ControllerError(
            f"run root must be inside {RUNS_DIRNAME.as_posix()} (got {resolved})"
        )
    if _is_forbidden_root(resolved, Path.home()):
        raise ControllerError("normal OpenCode data roots are forbidden as run roots")
    return resolved


def load_campaign_plan(repo_root: Path) -> Mapping[str, Any]:
    """Read the frozen campaign plan. Tests may monkeypatch this narrow seam."""
    path = repo_root / CAMPAIGN_RELATIVE_PATH
    if not path.is_file():
        raise ControllerError(f"campaign plan is missing: {path}")
    try:
        return read_campaign(path)
    except (ValueError, OSError) as exc:
        raise ControllerError(f"campaign plan is invalid: {exc}") from exc


def _load_workload_template(repo_root: Path) -> Mapping[str, Any]:
    path = repo_root / WORKLOAD_RELATIVE_PATH
    if not path.is_file():
        raise ControllerError(f"frozen workload template is missing: {path}")
    try:
        value = _strict_json_loads(path.read_text(encoding="utf-8"), "workload template")
    except OSError as exc:
        raise ControllerError(f"cannot read workload template: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ControllerError("workload template must be a JSON object")
    return value


def _find_attempt(plan: Mapping[str, Any], attempt_id: str) -> Mapping[str, Any]:
    attempts = plan.get("attempts", [])
    for attempt in attempts:
        if isinstance(attempt, Mapping) and attempt.get("attempt_id") == attempt_id:
            return attempt
    raise ControllerError(f"unknown attempt ID: {attempt_id}")


def _parse_repetition(raw: str) -> int:
    if not isinstance(raw, str) or not raw.isdigit():
        raise ControllerError("repetition must be a non-negative integer")
    return int(raw, 10)


def _parse_used_percent(raw: str) -> int:
    if not isinstance(raw, str) or not raw.isdigit():
        raise ControllerError("codex-used-percent must be an integer 0-100")
    value = int(raw, 10)
    if not 0 <= value <= 100:
        raise ControllerError("codex-used-percent must be an integer 0-100")
    return value


def _run_slug(attempt_id: str, repetition: int) -> str:
    slug = "".join(
        char if char.isascii() and (char.isalnum() or char in "_-") else "-"
        for char in attempt_id
    ).strip("-")
    if not slug:
        raise ControllerError("attempt ID yields an empty run slug")
    return f"{slug}-r{repetition}"


def _state_path(root: Path) -> Path:
    return root / STATE_NAME


def _write_state(root: Path, state: Mapping[str, Any]) -> None:
    target = _state_path(root)
    text = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tmp = target.with_suffix(".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, target)


def _load_state(root: Path) -> dict[str, Any]:
    target = _state_path(root)
    if not target.is_file() or target.is_symlink():
        raise ControllerError("controller state is missing; prepare must run first")
    try:
        state = _strict_json_loads(target.read_text(encoding="utf-8"), "controller state")
    except OSError as exc:
        raise ControllerError(f"cannot read controller state: {exc}") from exc
    if not isinstance(state, dict):
        raise ControllerError("controller state must be a JSON object")
    if state.get("schema_version") != CONTROLLER_SCHEMA_VERSION:
        raise ControllerError("controller state schema is not supported")
    for key in (
        "attempt_id",
        "repetition",
        "root",
        "workspace",
        "fixture_project",
        "run_canary",
        "model",
        "opencode_version",
        "protected_bench_check_sha256",
        "workload",
        "phase",
        "turns",
    ):
        if key not in state:
            raise ControllerError(f"controller state is missing required key: {key}")
    if state.get("root") != str(root):
        raise ControllerError("controller state is bound to a different run root")
    return state


def _copy_fixture_only(repo_root: Path, root: Path) -> Path:
    """Copy only fixture_project under <run>/project; reject symlink sources."""
    source = repo_root / WORKLOAD_RELATIVE_PATH.parent / FIXTURE_PROJECT_NAME
    if not source.is_dir() or source.is_symlink():
        raise ControllerError("frozen fixture_project source is not an ordinary directory")
    for dirpath, dirnames, filenames in os.walk(source):
        for name in (*dirnames, *filenames):
            if Path(dirpath, name).is_symlink():
                raise ControllerError("symlink in frozen fixture_project is forbidden")
    workspace = root / PROJECT_DIRNAME
    try:
        workspace.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise ControllerError(f"cannot create run workspace: {exc}") from exc
    destination = workspace / FIXTURE_PROJECT_NAME
    try:
        shutil.copytree(source, destination, symlinks=False)
    except OSError as exc:
        raise ControllerError(f"cannot copy fixture_project: {exc}") from exc
    return destination


def _project_dir(root: Path) -> Path:
    """Return the <run>/project workspace (OpenCode --dir and subprocess cwd)."""
    workspace = root / PROJECT_DIRNAME
    if not workspace.is_dir() or workspace.is_symlink():
        raise ControllerError("run project workspace is not an ordinary directory")
    return workspace


def _fixture_dir(root: Path) -> Path:
    """Return the nested <run>/project/fixture_project for helper/ledger/checkout checks."""
    fixture = root / PROJECT_DIRNAME / FIXTURE_PROJECT_NAME
    if not fixture.is_dir() or fixture.is_symlink():
        raise ControllerError("run fixture_project is not an ordinary directory")
    return fixture


def _protected_hash(fixture: Path) -> str:
    helper = fixture / PROTECTED_HELPER_NAME
    try:
        info = helper.lstat()
    except FileNotFoundError as exc:
        raise ControllerError("protected bench_check.py is missing") from exc
    if helper.is_symlink() or not helper.is_file():
        raise ControllerError("protected bench_check.py is not an ordinary file")
    if info.st_size <= 0:
        raise ControllerError("protected bench_check.py is empty")
    return _sha256_bytes(helper.read_bytes())


def _build_argv(
    workspace: Path, prompt: str, model: str, session_id: str | None
) -> tuple[str, ...]:
    """Build the exact pinned submission argv.

    ``--auto`` is declared explicitly here. It is safe only because the
    copied synthetic fixture is the only workspace (``--dir``) and the
    database is an isolated per-run path.
    """
    validate_model_id(model)
    if model != OPENCODE_DEFAULT_MODEL:
        raise ControllerError("model override is forbidden; only the pinned model may run")
    if not isinstance(prompt, str) or not prompt:
        raise ControllerError("prompt must come from the instantiated workload turn")
    argv: list[str] = [
        OPENCODE_EXECUTABLE,
        "run",
        "--model",
        model,
        "--pure",
        "--format",
        "json",
        "--auto",
        "--dir",
        str(workspace),
    ]
    if session_id is not None:
        if not session_id.strip():
            raise ControllerError("session continuation requires an exact session ID")
        argv.extend(["--session", session_id])
    argv.append(prompt)
    return tuple(argv)


def _redacted_argv(argv: tuple[str, ...]) -> list[str]:
    """Return argv with the prompt positional redacted for state storage."""
    if not argv:
        return []
    digest = _sha256_bytes(argv[-1].encode("utf-8"))
    return [*argv[:-1], f"<prompt-redacted:sha256:{digest}>"]


def _submission_env(root: Path, run_canary: str) -> dict[str, str]:
    """Child env: inherited process env plus the isolated DB and run canary.

    Only the two safe keys are ever recorded in state (see SAFE_ENV_KEYS).
    """
    env = dict(os.environ)
    env.update(proposed_environment(root))
    env[RUN_CANARY_ENV] = run_canary
    return env


def _safe_env_record(env: Mapping[str, str]) -> dict[str, str]:
    return {key: env[key] for key in SAFE_ENV_KEYS if key in env}


def _event_cost(payloads: list[Mapping[str, Any]]) -> float | None:
    total = 0.0
    found = False
    stack: list[Any] = list(payloads)
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                if key == "cost" and isinstance(value, (int, float)) and not isinstance(
                    value, bool
                ):
                    total += float(value)
                    found = True
                else:
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return total if found else None


def _run_semantic_cases(fixture: Path) -> list[dict[str, Any]]:
    """Execute only the synthetic checkout.py in memory; never bench_check.py."""
    target = fixture / "checkout.py"
    try:
        source = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ControllerError(f"cannot read synthetic checkout.py: {exc}") from exc
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, str(target), "exec"), namespace)  # noqa: S102 - synthetic fixture only
    except Exception as exc:
        raise ControllerError(f"synthetic checkout.py did not parse: {exc}") from exc
    checkout = namespace.get("checkout")
    if not callable(checkout):
        raise ControllerError("synthetic checkout.py does not define checkout()")
    outcomes: list[dict[str, Any]] = []
    for case in SEMANTIC_CASES:
        try:
            actual = checkout([list(item) for item in case["items"]])
        except Exception as exc:
            outcomes.append(
                {
                    "items": case["items"],
                    "expected": case["expected"],
                    "actual": None,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        outcomes.append(
            {
                "items": case["items"],
                "expected": case["expected"],
                "actual": actual,
                "passed": actual == case["expected"],
            }
        )
    return outcomes


def _read_helper_ledger(fixture: Path) -> dict[str, Any]:
    """Read (never execute) the helper ledger if the model produced one."""
    ledger = fixture / LEDGER_NAME
    if not ledger.exists():
        return {"present": False, "lines": 0, "events": [], "sha256": None}
    try:
        info = ledger.lstat()
    except OSError as exc:
        raise ControllerError(f"cannot inspect helper ledger: {exc}") from exc
    if ledger.is_symlink() or not ledger.is_file():
        raise ControllerError("helper ledger is not an ordinary file")
    data = ledger.read_bytes()
    events: list[dict[str, Any]] = []
    for line in data.decode("utf-8", errors="strict").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            events.append({"parse_ok": False})
            continue
        if isinstance(value, dict):
            events.append(
                {
                    "parse_ok": True,
                    "id": value.get("id"),
                    "phase": value.get("phase"),
                    "run_canary": value.get("run_canary"),
                    "exit_code": value.get("exit_code"),
                }
            )
        else:
            events.append({"parse_ok": False})
    _ = info
    return {
        "present": True,
        "lines": len(events),
        "events": events,
        "sha256": _sha256_bytes(data),
    }


def cmd_prepare(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    try:
        repetition = _parse_repetition(args.repetition)
        root = _resolve_new_root(repo_root, args.root)
        plan = load_campaign_plan(repo_root)
        try:
            validate_frozen_inputs(plan, repo_root)
        except ValueError as exc:
            raise ControllerError(f"frozen campaign inputs do not validate: {exc}") from exc
        attempt = _find_attempt(plan, args.attempt_id)
        if attempt.get("configuration_id") != CONFIGURATION_ID:
            raise ControllerError("attempt does not belong to opencode-cli")
        if attempt.get("kind") != "evaluated":
            raise ControllerError("attempt is not an evaluated attempt")
        if attempt.get("state") != "scheduled":
            raise ControllerError("attempt is not scheduled")
        if attempt.get("repetition") != repetition:
            raise ControllerError("repetition does not match the scheduled attempt")
        template = _load_workload_template(repo_root)
        slug = _run_slug(attempt["attempt_id"], repetition)
        try:
            workload, _canary_env = instantiate_workload(template, slug)
        except ValueError as exc:
            raise ControllerError(f"workload instantiation failed: {exc}") from exc
        turns = sorted(workload.get("turns", []), key=lambda item: item.get("sequence", 0))
        if len(turns) != 2:
            raise ControllerError("instantiated workload must carry exactly two turns")
        root.mkdir(parents=True, exist_ok=False)
        fixture = _copy_fixture_only(repo_root, root)
        workspace = _project_dir(root)
        protected = _protected_hash(fixture)
        identity = OpenCodeIdentity(
            executable=OPENCODE_EXECUTABLE,
            version=OPENCODE_VERSION_PIN,
            model=OPENCODE_DEFAULT_MODEL,
        )
        state: dict[str, Any] = {
            "schema_version": CONTROLLER_SCHEMA_VERSION,
            "attempt_id": attempt["attempt_id"],
            "configuration_id": CONFIGURATION_ID,
            "repetition": repetition,
            "root": str(root),
            "workspace": str(workspace),
            "fixture_project": str(fixture),
            "run_slug": slug,
            "run_canary": workload["run_canary"],
            "model": identity.model,
            "opencode_version": identity.version,
            "protected_bench_check_sha256": protected,
            "checkout_before_sha256": _sha256_bytes(
                (fixture / "checkout.py").read_bytes()
            ),
            "workload": {
                "turns": [
                    {
                        "id": turn["id"],
                        "sequence": turn["sequence"],
                        "text": turn["text"],
                        "response_canary": turn.get("response_canary"),
                        "prompt_sha256": _sha256_bytes(turn["text"].encode("utf-8")),
                    }
                    for turn in turns
                ]
            },
            "phase": "prepared",
            "turns": {"1": None, "2": None},
            "capture": None,
            "score_eligible": False,
            "created_at": _utcnow(),
        }
        _write_state(root, state)
    except ControllerError as exc:
        print(f"prepare refused: {exc}", file=sys.stderr)
        return 2
    print(f"prepared {state['attempt_id']} repetition {repetition} at {root}")
    return 0


def _submit_turn(
    state: dict[str, Any], root: Path, turn: int, used_percent: int
) -> dict[str, Any]:
    workspace = _project_dir(root)
    fixture = _fixture_dir(root)
    if state.get("workspace") != str(workspace):
        raise ControllerError("run workspace does not match controller state")
    if state.get("fixture_project") != str(fixture):
        raise ControllerError("run fixture path does not match controller state")
    if state["protected_bench_check_sha256"] != _protected_hash(fixture):
        raise ControllerError("protected bench_check.py changed; failing closed")
    entries = state["workload"]["turns"]
    entry = next(item for item in entries if item["sequence"] == turn)
    prompt = entry["text"]
    response_canary = entry.get("response_canary")
    if not response_canary:
        raise ControllerError("workload turn has no response canary")
    session_id: str | None = None
    if turn == 2:
        previous = state["turns"]["1"]
        if not isinstance(previous, dict) or previous.get("status") != "ok":
            raise ControllerError("turn 2 requires a successful turn 1")
        session_id = previous.get("session_id")
        if not session_id:
            raise ControllerError("turn 1 recorded no session to continue")
    argv = _build_argv(workspace, prompt, state["model"], session_id)
    env = _submission_env(root, state["run_canary"])
    started_at = _utcnow()
    try:
        # Exactly one submission per invocation; no retry loop by construction.
        # cwd is <run>/project so the frozen child path fixture_project/ resolves.
        process = subprocess.run(
            argv,
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControllerError(f"OpenCode submission could not start: {exc}") from exc
    finished_at = _utcnow()
    stdout = process.stdout if isinstance(process.stdout, str) else str(process.stdout)
    stderr = process.stderr if isinstance(process.stderr, str) else str(process.stderr)
    try:
        observation = observe_stdout(
            stdout,
            existing_session_ids=(),
            response_canaries=[response_canary],
            returncode=process.returncode,
        )
    except ValueError as exc:
        raise ControllerError(f"stdout observer did not bind: {exc}") from exc
    if process.returncode != 0:
        raise ControllerError(f"OpenCode runner exited with {process.returncode}")
    if response_canary not in observation.response_canaries:
        raise ControllerError("response canary is absent from the observed stdout")
    if turn == 2 and observation.session_id != session_id:
        raise ControllerError("turn 2 did not continue the exact turn 1 session")
    try:
        cases = _run_semantic_cases(fixture)
    except ControllerError as exc:
        raise ControllerError(f"semantic verification failed: {exc}") from exc
    if turn == 2 and not all(case["passed"] for case in cases):
        raise ControllerError("turn 2 semantic cases did not all pass")
    if state["protected_bench_check_sha256"] != _protected_hash(fixture):
        raise ControllerError("protected bench_check.py changed during submission")
    ledger = _read_helper_ledger(fixture)
    return {
        "status": "ok",
        "turn": turn,
        "workspace": str(workspace),
        "fixture_project": str(fixture),
        "returncode": process.returncode,
        "session_id": observation.session_id,
        "session_ids": sorted(observation.session_ids),
        "response_canary": response_canary,
        "response_canary_found": True,
        "event_cost": _event_cost([dict(event.payload) for event in observation.events]),
        "event_count": len(observation.events),
        "semantic_cases": cases,
        "helper_ledger": ledger,
        "protected_bench_check_sha256": state["protected_bench_check_sha256"],
        "checkout_sha256": _sha256_bytes((fixture / "checkout.py").read_bytes()),
        "argv_redacted": _redacted_argv(argv),
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "environment_safe": _safe_env_record(env),
        "stdout_sha256": _sha256_bytes(stdout.encode("utf-8")),
        "stderr_sha256": _sha256_bytes(stderr.encode("utf-8")),
        "stdout": stdout,
        "stderr": stderr,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def cmd_submit(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    try:
        root = _resolve_existing_root(repo_root, args.root)
        state = _load_state(root)
    except ControllerError as exc:
        print(f"submit refused: {exc}", file=sys.stderr)
        return 2
    turn = int(args.turn)
    try:
        used_percent = _parse_used_percent(args.codex_used_percent)
    except ControllerError as exc:
        print(f"submit refused: {exc}", file=sys.stderr)
        return 2
    expected_phase = "prepared" if turn == 1 else "turn1_ok"
    if state.get("phase") != expected_phase:
        print(
            f"submit refused: wrong phase order (phase={state.get('phase')}, "
            f"turn={turn} requires {expected_phase})",
            file=sys.stderr,
        )
        return 2
    try:
        plan = load_campaign_plan(repo_root)
        try:
            frozen_ok = validate_frozen_inputs(plan, repo_root)
        except ValueError as exc:
            raise ControllerError(f"frozen campaign inputs do not validate: {exc}") from exc
        _ = frozen_ok
        if not quota_allows_submission(plan, used_percent):
            raise ControllerError(
                "quota stop reached: codex-used-percent is not below the campaign stop"
            )
        record = _submit_turn(state, root, turn, used_percent)
    except ControllerError as exc:
        state["turns"][str(turn)] = {
            "status": "invalid",
            "turn": turn,
            "error": str(exc),
            "finished_at": _utcnow(),
        }
        state["phase"] = f"turn{turn}_invalid"
        _write_state(root, state)
        print(f"submit turn {turn} invalid: {exc}", file=sys.stderr)
        return 1
    state["turns"][str(turn)] = record
    state["phase"] = "turn1_ok" if turn == 1 else "turn2_ok"
    _write_state(root, state)
    print(f"submit turn {turn} ok session {record['session_id']}")
    return 0


def _canonical_normalized(result: Mapping[str, Any]) -> bytes:
    normalized = json.loads(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    if isinstance(normalized, dict) and isinstance(normalized.get("bundle"), dict):
        normalized["bundle"]["path"] = "bundle"
    return json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def cmd_capture(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    try:
        root = _resolve_existing_root(repo_root, args.root)
        state = _load_state(root)
    except ControllerError as exc:
        print(f"capture refused: {exc}", file=sys.stderr)
        return 2
    if state.get("phase") != "turn2_ok":
        print(
            f"capture refused: wrong phase order (phase={state.get('phase')}; "
            "a successful turn 2 is required)",
            file=sys.stderr,
        )
        return 2
    if state.get("score_eligible") is not False:
        print("capture refused: score_eligible must remain false", file=sys.stderr)
        return 2
    turn2 = state["turns"].get("2")
    if not isinstance(turn2, dict) or turn2.get("status") != "ok":
        print("capture refused: turn 2 is not successful", file=sys.stderr)
        return 2
    session_id = turn2.get("session_id")
    turn1 = state["turns"].get("1")
    if (
        not isinstance(turn1, dict)
        or turn1.get("status") != "ok"
        or turn1.get("session_id") != session_id
    ):
        print("capture refused: combined observer spans more than one session", file=sys.stderr)
        return 2
    try:
        database = Path(state["turns"]["2"]["environment_safe"]["OPENCODE_DB"])
        if database.name != OPENCODE_DATABASE_NAME:
            raise ControllerError("isolated database name is not the pinned name")
        if _resolve_existing_root(repo_root, str(database.parent)) != root:
            raise ControllerError("isolated database is outside the run root")
        capture_dir = root / CAPTURE_DIRNAME
        offline_dir = root / OFFLINE_DIRNAME
        if capture_dir.exists() or offline_dir.exists():
            raise ControllerError("capture destinations already exist; failing closed")
        exit_barrier = {"quiescent": True, "writer_alive": False}

        def _process_exited_barrier() -> Mapping[str, Any]:
            # subprocess.run returned before submit recorded turn 2, so the
            # writer process has exited; the barrier only attests that fact.
            return dict(exit_barrier)

        capture = capture_sqlite_bundle(
            database,
            capture_dir,
            barrier=_process_exited_barrier,
            session_ids=(),
        )
        # Copy to the offline bundle before any reads: even read-only SQLite
        # opens rewrite the SHM companion, so integrity-check the pristine
        # bytes first.
        offline_dir.mkdir(parents=True, exist_ok=False)
        offline_hashes: list[dict[str, str]] = []
        for artifact in capture.artifacts:
            source = capture.destination / artifact.relative_path
            target = offline_dir / artifact.relative_path
            data = source.read_bytes()
            if _sha256_bytes(data) != artifact.sha256:
                raise ControllerError(
                    f"captured artifact failed integrity check: {artifact.relative_path}"
                )
            target.write_bytes(data)
            offline_hashes.append(
                {"name": artifact.relative_path, "sha256": artifact.sha256}
            )
        # Read session IDs from a throwaway clone: even read-only SQLite
        # opens rewrite the SHM companion, and both compared bundles must
        # stay byte-identical so that normalizing only the bundle path
        # suffices for canonical equality.
        try:
            with tempfile.TemporaryDirectory(
                prefix="session-bench-opencode-read-"
            ) as tmpdir:
                read_clone = Path(tmpdir) / "read-clone"
                shutil.copytree(capture_dir, read_clone, symlinks=False)
                native_ids = tuple(
                    str(value)
                    for value in read_sqlite_session_ids(
                        read_clone / OPENCODE_DATABASE_NAME
                    )
                    if str(value)
                )
        except ValueError as exc:
            raise ControllerError(f"captured bundle session read failed: {exc}") from exc
        if sorted(set(native_ids)) != [session_id]:
            raise ControllerError(
                "combined observer does not bind to exactly one native session"
            )
        # First decode: the captured bundle, with live sources still readable.
        try:
            captured_result = decode_opencode_bundle(capture_dir, session_id=session_id)
        except ValueError as exc:
            raise ControllerError(f"captured bundle decode failed: {exc}") from exc
        native_files = [
            database.parent / name
            for name in (
                database.name,
                database.name + "-wal",
                database.name + "-shm",
            )
        ]
        # Second decode: the separate offline copy, with the original live
        # DB/WAL/SHM denied, so the decode cannot depend on live sources.
        saved_modes: dict[Path, int] = {}
        try:
            for native in native_files:
                saved_modes[native] = stat.S_IMODE(native.lstat().st_mode)
                os.chmod(native, 0)
            offline_result = decode_opencode_bundle(offline_dir, session_id=session_id)
        except ValueError as exc:
            raise ControllerError(f"offline decode failed: {exc}") from exc
        finally:
            for native, mode in saved_modes.items():
                try:
                    os.chmod(native, mode)
                except OSError:
                    pass
        canonical_captured = _canonical_normalized(captured_result)
        canonical_offline = _canonical_normalized(offline_result)
        equal = canonical_captured == canonical_offline
        supported = (
            captured_result.get("supported") is True
            and offline_result.get("supported") is True
        )
        if not supported or not equal:
            raise ControllerError(
                "captured and offline decodes are not supported or not "
                "canonical-equal; failing closed"
            )
        decoded_path = root / DECODED_CURRENT_NAME
        decoded_path.write_bytes(canonical_offline + b"\n")
        state["capture"] = {
            "captured": True,
            "session_id": session_id,
            "decoded_sources": ["capture", "offline-bundle"],
            "artifacts": offline_hashes,
            "native_session_ids": sorted(set(native_ids)),
            "supported": True,
            "canonical_equal": True,
            "decoded_current_sha256": _sha256_bytes(canonical_offline + b"\n"),
            "captured_at": _utcnow(),
        }
        state["phase"] = "captured"
        state["score_eligible"] = False
        _write_state(root, state)
    except (ControllerError, OSError) as exc:
        print(f"capture invalid: {exc}", file=sys.stderr)
        return 1
    print(f"captured session {session_id} at {root / DECODED_CURRENT_NAME}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_opencode_survival",
        description="Bounded local controller for OpenCode survival attempts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="build a fresh run root")
    prepare.add_argument("--attempt-id", required=True)
    prepare.add_argument("--repetition", required=True)
    prepare.add_argument("--root", required=True)
    prepare.set_defaults(func=cmd_prepare)

    submit = sub.add_parser("submit", help="run exactly one OpenCode submission")
    submit.add_argument("--root", required=True)
    submit.add_argument("--turn", required=True, choices=("1", "2"))
    submit.add_argument("--codex-used-percent", required=True)
    submit.set_defaults(func=cmd_submit)

    capture = sub.add_parser("capture", help="capture the SQLite bundle offline")
    capture.add_argument("--root", required=True)
    capture.set_defaults(func=cmd_capture)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
