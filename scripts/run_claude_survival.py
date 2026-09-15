#!/usr/bin/env python3
"""Run one fresh Claude Code CLI Survival v1 calibration.

This controller is intentionally a single-use calibration runner.  It launches
exactly one R1 submission and, when R1 succeeds, exactly one resumed R2
submission.  The model sees only a copied synthetic ``fixture_project``.  The
normal Claude account root is used for authentication and session persistence,
but inventory of that root is metadata-only and only the newly-created,
project-keyed family is ever opened or copied.

The controller records evidence and never publishes or scores a result.  The
19-row Survival measurement, 12-row broad-format wrapper, and combined 31-row
evidence document retain ``score_eligible: false`` because one calibration is
not a public cohort.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.claude_live import (  # noqa: E402
    ClaudeLiveError,
    claude_stream_to_observer_jsonl,
    native_facts_from_claude_session,
)
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.live_observer import build_opencode_live_observer  # noqa: E402
from session_bench.surface_capture import (  # noqa: E402
    IndependentStdoutObserver,
    copy_verified_artifacts,
    inventory_tree,
    sha256_bytes,
    wait_for_quiescence,
)
from session_bench.survival_evidence import (  # noqa: E402
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_prospective_evidence_input,
)
from session_bench.survival_metrics import METRICS  # noqa: E402
from session_bench.v1_public_score import FORMAT_METRICS, PUBLIC_METRICS, SURVIVAL_METRICS, validate_format_evidence  # noqa: E402
from session_bench.workload_instance import instantiate_workload  # noqa: E402


CONFIGURATION_ID = "claude-cli"
ATTEMPT_ID = "claude-cli-cal-3"
REPETITION = 1
WORKLOAD_RELATIVE = Path("fixtures/scenarios/survival-v1/workload/workload.json")
FIXTURE_RELATIVE = WORKLOAD_RELATIVE.parent / "fixture_project"
RUNS_RELATIVE = Path("artifacts/survival-v1-runs")
CLAUDE_ROOT = Path.home() / ".claude"
CLAUDE_PROJECTS = CLAUDE_ROOT / "projects"
CLAUDE_VERSION = "2.1.272"
CONTROLLER_SCHEMA = "session-bench-claude-cli-calibration-v1"
OBSERVER_METHOD = "independent live observer from submitted input, Claude Code CLI stream-json stdout, helper ledger, filesystem hashes, and usage trace; no native root read before synthetic family selection, no scoring, qualification, or publication"
SUBPROCESS_TIMEOUT = 300.0
QUIESCENCE_CHECKS = 2
QUIESCENCE_INTERVAL = 0.25
SEMANTIC_CASES = (
    {"items": [[10, 2], [5, 1]], "expected": 30},
    {"items": [[25, 2]], "expected": 50},
    {"items": [[49, 1]], "expected": 54},
)


class CalibrationError(RuntimeError):
    """The one authorized calibration cannot be accepted as complete."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value) + b"\n"
    if path.exists():
        raise CalibrationError(f"artifact already exists: {path}")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _artifact_root(repo: Path, attempt_id: str) -> Path:
    root = (repo / RUNS_RELATIVE / attempt_id).resolve()
    if root.exists() or root.is_symlink():
        raise CalibrationError(f"fresh calibration artifact root already exists: {root}")
    base = (repo / RUNS_RELATIVE).resolve()
    if base not in root.parents:
        raise CalibrationError("artifact root escaped the repository run directory")
    return root


def _fresh_project_root(attempt_id: str) -> Path:
    parent = Path("/private/tmp")
    if not parent.is_dir():
        raise CalibrationError("/private/tmp is unavailable for the synthetic project")
    return Path(tempfile.mkdtemp(prefix=f"session-bench-{attempt_id}-", dir=str(parent))) / "project"


def _assert_no_symlinks(root: Path) -> None:
    for directory, directories, files in os.walk(root):
        for name in (*directories, *files):
            candidate = Path(directory, name)
            if candidate.is_symlink():
                raise CalibrationError(f"synthetic fixture contains a symlink: {candidate}")


def _copy_fixture(repo: Path, project_root: Path) -> Path:
    source = repo / FIXTURE_RELATIVE
    if not source.is_dir() or source.is_symlink():
        raise CalibrationError(f"frozen fixture_project is unavailable: {source}")
    _assert_no_symlinks(source)
    project_root.mkdir(parents=True, exist_ok=False)
    destination = project_root / "fixture_project"
    shutil.copytree(source, destination, symlinks=False)
    _assert_no_symlinks(destination)
    return destination


def _metadata_stat(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CalibrationError(f"cannot stat metadata root {path}: {exc}") from exc
    return {
        "filesystem_id": f"{info.st_dev}:{info.st_ino}",
        "size_bytes": info.st_size,
        "ctime_ns": info.st_ctime_ns,
        "mtime_ns": info.st_mtime_ns,
        "mode_type": stat.S_IFMT(info.st_mode),
    }


def _project_key(entry_path: str) -> str:
    return entry_path.split("/", 1)[0]


def _inventory_summary(root: Path) -> tuple[dict[str, Any], set[str], tuple[Any, ...]]:
    """Collect metadata only and retain no unrelated names in the artifact."""

    entries = inventory_tree(root)
    metadata_rows = [
        {
            # A digest of a pre-existing relative name allows before/after
            # comparison without retaining unrelated session or project names.
            "relative_path_sha256": sha256_bytes(entry.relative_path.encode("utf-8")),
            "filesystem_id": entry.filesystem_id,
            "size_bytes": entry.size_bytes,
            "birth_ns": entry.birth_ns,
            "ctime_ns": entry.ctime_ns,
            "mtime_ns": entry.mtime_ns,
        }
        for entry in entries
    ]
    summary = {
        "root": str(root),
        "root_metadata": _metadata_stat(root),
        "file_count": len(entries),
        "metadata_digest": sha256_bytes(_canonical(metadata_rows)),
        "project_key_count": len({_project_key(entry.relative_path) for entry in entries}),
        "privacy": "metadata-only; unrelated relative names are represented only by SHA-256",
    }
    return summary, {_project_key(entry.relative_path) for entry in entries}, entries


def _run_canary_slug() -> str:
    return f"{ATTEMPT_ID}-r{REPETITION}"


def _hash_checkout(fixture: Path) -> str:
    target = fixture / "checkout.py"
    if target.is_symlink() or not target.is_file():
        raise CalibrationError("synthetic checkout.py is not an ordinary file")
    return _digest_file(target)


def _hash_protected_helper(fixture: Path) -> str:
    target = fixture / "bench_check.py"
    if target.is_symlink() or not target.is_file():
        raise CalibrationError("protected bench_check.py is not an ordinary file")
    return _digest_file(target)


def _run_semantic_cases(fixture: Path) -> list[dict[str, Any]]:
    """Execute only the copied synthetic checkout module in memory."""

    target = fixture / "checkout.py"
    source = target.read_text(encoding="utf-8")
    namespace: dict[str, Any] = {}
    try:
        exec(compile(source, str(target), "exec"), namespace)  # noqa: S102 - frozen synthetic fixture only
    except Exception as exc:
        raise CalibrationError(f"synthetic checkout.py does not parse: {exc}") from exc
    checkout = namespace.get("checkout")
    if not callable(checkout):
        raise CalibrationError("synthetic checkout.py has no callable checkout")
    outcomes: list[dict[str, Any]] = []
    for case in SEMANTIC_CASES:
        try:
            actual = checkout([list(item) for item in case["items"]])
            outcomes.append({"items": case["items"], "expected": case["expected"], "actual": actual, "passed": actual == case["expected"]})
        except Exception as exc:
            outcomes.append({"items": case["items"], "expected": case["expected"], "actual": None, "passed": False, "error": f"{type(exc).__name__}: {exc}"})
    return outcomes


def _read_ledger(fixture: Path, run_canary: str) -> tuple[str, dict[str, Any]]:
    path = fixture / ".survival-observer.jsonl"
    if path.is_symlink() or not path.is_file():
        raise CalibrationError("helper ledger is missing or is not an ordinary file")
    raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise CalibrationError(f"helper ledger line {number} is blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"helper ledger line {number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise CalibrationError(f"helper ledger line {number} is not an object")
        rows.append(value)
    if len(rows) != 3 or sorted(row.get("phase") for row in rows) != ["baseline", "final", "inspect"]:
        raise CalibrationError("helper ledger must contain exactly inspect, baseline, and final")
    expected_codes = {"inspect": 0, "baseline": 1, "final": 0}
    seen: set[str] = set()
    for row in rows:
        phase = row.get("phase")
        if phase in seen or phase not in expected_codes:
            raise CalibrationError("helper ledger phases are duplicated or unsupported")
        seen.add(phase)
        if row.get("run_canary") != run_canary:
            raise CalibrationError("helper ledger run canary mismatch")
        if row.get("argv") != ["python3", "bench_check.py", phase]:
            raise CalibrationError(f"helper ledger {phase} argv mismatch")
        if row.get("cwd") != "fixture_project":
            raise CalibrationError(f"helper ledger {phase} cwd mismatch")
        if row.get("exit_code") != expected_codes[phase]:
            raise CalibrationError(f"helper ledger {phase} exit code mismatch")
        output = row.get("output")
        prefix = f"SB_SURVIVAL_V1_HELPER_{str(phase).upper()}_"
        if not isinstance(output, str) or not output.startswith(prefix):
            raise CalibrationError(f"helper ledger {phase} output prefix mismatch")
    summary = {
        "present": True,
        "line_count": len(rows),
        "phases": [row["phase"] for row in rows],
        "exit_codes": {row["phase"]: row["exit_code"] for row in rows},
        "run_canary": run_canary,
        "sha256": _digest_file(path),
    }
    return raw, summary


def _stream_jsonl(value: Mapping[str, Any]) -> str:
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows:
        raise CalibrationError("Claude stream normalization produced no rows")
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)


def _prompt_sha256(prompt: str) -> str:
    return sha256_bytes(prompt.encode("utf-8"))


def _redact_argv(argv: list[str], prompt: str) -> list[str]:
    if not argv or argv[-1] != prompt:
        return [str(value) for value in argv]
    return [str(value) for value in argv[:-1]] + [f"<prompt-redacted:sha256:{_prompt_sha256(prompt)}>"]


def _claude_argv(project_root: Path, prompt: str, session_id: str | None = None) -> list[str]:
    # ``--tools`` is a single comma-separated value so the positional prompt
    # cannot be consumed by the CLI's variadic option parser.  No CLAUDE_* root
    # override is supplied: authentication and native persistence use the
    # user's normal account root by explicit authorization.
    argv = [
        "/Users/alexm/.local/bin/claude",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--safe-mode",
        "--strict-mcp-config",
        "--no-chrome",
        "--disable-slash-commands",
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "none",
        "--tools",
        "Bash,Read,Edit",
        "--allow-dangerously-skip-permissions",
        "--dangerously-skip-permissions",
    ]
    if session_id is not None:
        argv.extend(["--resume", session_id])
    argv.append(prompt)
    return argv


def _run_cli(argv: list[str], project_root: Path, env: Mapping[str, str], stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    started = _utcnow()
    try:
        process = subprocess.run(
            argv,
            cwd=str(project_root),
            env=dict(env),
            capture_output=True,
            text=False,
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CalibrationError(f"Claude Code CLI could not start: {exc}") from exc
    stdout = process.stdout if isinstance(process.stdout, bytes) else bytes(process.stdout or "", "utf-8")
    stderr = process.stderr if isinstance(process.stderr, bytes) else bytes(process.stderr or "", "utf-8")
    observer = IndependentStdoutObserver(method="claude-code-cli-stdout")
    observer.observe(stdout)
    stdout_receipt = observer.freeze(stdout_path)
    stderr_path.write_bytes(stderr)
    return {
        "returncode": process.returncode,
        "stdout_sha256": sha256_bytes(stdout),
        "stdout_size_bytes": len(stdout),
        "stderr_sha256": sha256_bytes(stderr),
        "stderr_size_bytes": len(stderr),
        "stdout_receipt": stdout_receipt,
        "started_at": started,
        "finished_at": _utcnow(),
        "stdout": stdout,
        "stderr": stderr,
    }


def _decode_native_bundle(package: Path, destination: Path) -> dict[str, Any]:
    decoded = decode_claude_code_bundle(package)
    _write_json(destination, decoded)
    return decoded


def _write_decoder_manifest(package: Path, session_bytes: bytes) -> str:
    manifest = {
        "format": "claude-code-jsonl-v1",
        "artifacts": [{
            "id": "session",
            "path": "session.jsonl",
            "sha256": sha256_bytes(session_bytes),
            "size_bytes": len(session_bytes),
            "depends_on": [],
        }],
    }
    _write_json(package / "decode.json", manifest)
    return _digest_file(package / "decode.json")


def _selected_family(
    projects_root: Path,
    before_keys: set[str],
    expected_project_root: Path,
) -> tuple[str, tuple[Any, ...], dict[str, Any]]:
    """Identify one new project key using metadata only, then return its entries."""

    deadline = time.monotonic() + 20.0
    latest_summary: dict[str, Any] | None = None
    latest_entries: tuple[Any, ...] = ()
    latest_keys: set[str] = set()
    while time.monotonic() < deadline:
        latest_summary, latest_keys, latest_entries = _inventory_summary(projects_root)
        new_keys = latest_keys - before_keys
        # Claude's project key is a path encoding of the synthetic workspace.
        # Requiring the expected suffix keeps unrelated account-root activity
        # outside the selected family even if another new key appears.
        expected_suffix = re.sub(r"[^A-Za-z0-9-]+", "-", str(expected_project_root).replace("/", "-"))
        matching = {key for key in new_keys if key == expected_suffix or key.endswith(expected_suffix)}
        if len(matching) == 1:
            key = next(iter(matching))
            selected = tuple(entry for entry in latest_entries if entry.relative_path == key or entry.relative_path.startswith(key + "/"))
            if selected:
                return key, selected, latest_summary
        time.sleep(0.25)
    raise CalibrationError("no unique newly-created synthetic Claude project key appeared in metadata inventory")


def _copy_selected_family(
    projects_root: Path,
    native_family_dir: Path,
    entries: tuple[Any, ...],
    project_key: str,
) -> tuple[dict[str, Any], str, bytes]:
    artifacts = copy_verified_artifacts(projects_root, native_family_dir, entries, max_files=128, max_file_bytes=64 * 1024 * 1024, max_total_bytes=256 * 1024 * 1024, roles={entry.relative_path: "new-project-key-family" for entry in entries})
    jsonl = [artifact for artifact in artifacts if artifact["relative_path"].startswith(project_key + "/") and artifact["relative_path"].lower().endswith(".jsonl")]
    if len(jsonl) != 1:
        raise CalibrationError(f"new synthetic project key must contain exactly one JSONL session (found {len(jsonl)})")
    selected_relative = jsonl[0]["relative_path"]
    selected_bytes = (native_family_dir / selected_relative).read_bytes()
    family_manifest = {
        "schema_version": "session-bench-claude-cli-native-family-v1",
        "project_key": project_key,
        "selected_session_relative_path": selected_relative,
        "selected_session_sha256": sha256_bytes(selected_bytes),
        "selected_session_size_bytes": len(selected_bytes),
        "artifacts": list(artifacts),
        "closure": "all metadata-proven-new regular files beneath the one synthetic project key",
        "unrelated_preexisting_sessions_opened": False,
    }
    return family_manifest, selected_relative, selected_bytes


def _damage_selected_response(source_package: Path, target_package: Path, response_canary: str) -> dict[str, Any]:
    shutil.copytree(source_package, target_package, symlinks=False)
    session = target_package / "session.jsonl"
    lines = session.read_text(encoding="utf-8").splitlines(keepends=True)
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict) or response_canary not in line:
            continue
        if value.get("type") in {"assistant", "result"}:
            candidates.append((index, line))
    if not candidates:
        raise CalibrationError("selected-loss control could not find the R2 response record")
    index, removed = candidates[-1]
    remaining = lines[:index] + lines[index + 1:]
    session.write_text("".join(remaining), encoding="utf-8")
    manifest = json.loads((target_package / "decode.json").read_text(encoding="utf-8"))
    session_bytes = session.read_bytes()
    manifest["artifacts"][0]["sha256"] = sha256_bytes(session_bytes)
    manifest["artifacts"][0]["size_bytes"] = len(session_bytes)
    (target_package / "decode.json").write_bytes(_canonical(manifest) + b"\n")
    return {
        "removed_line_number": index + 1,
        "removed_record_sha256": sha256_bytes(removed.encode("utf-8")),
        "removed_response_canary": response_canary,
        "remaining_session_sha256": sha256_bytes(session_bytes),
    }


def _build_31_evidence(
    measurement: Mapping[str, Any],
    format_evidence: Mapping[str, Any],
    survival_evidence: Mapping[str, Any],
    *,
    observer_id: str,
    native_artifact_id: str,
    native_artifact_sha256: str,
) -> dict[str, Any]:
    deep = {row["id"]: row for row in measurement["metrics"]}
    # The reportable format document stores source evidence under
    # ``profile.broad_evidence``; the validator derives the twelve canonical
    # metric rows used by the public wrapper.
    normalized_format = validate_format_evidence(format_evidence)
    broad = {row["id"]: row for row in normalized_format["profile"]["metrics"]}
    if set(deep) != set(SURVIVAL_METRICS) or set(broad) != set(FORMAT_METRICS):
        raise CalibrationError("31-metric wrapper inputs do not cover the frozen metric sets")
    metric_rows: list[dict[str, Any]] = []
    for metric_id in PUBLIC_METRICS:
        source_row = deep.get(metric_id, broad.get(metric_id))
        assert source_row is not None
        metric_rows.append({"id": metric_id, "source": PUBLIC_METRICS[metric_id].source, **dict(source_row)})
    proofs = list(survival_evidence["metric_evidence"]) + list(format_evidence["metric_evidence"])
    if len(proofs) != 31:
        raise CalibrationError("31-metric wrapper proof rows are incomplete")
    return {
        "schema_version": "session-bench-survival-v1-evidence-31",
        "run_id": survival_evidence["run_id"],
        "configuration_id": survival_evidence["configuration_id"],
        "repetition": survival_evidence["repetition"],
        "score_eligible": False,
        "published": False,
        "metrics": metric_rows,
        "metric_evidence": proofs,
        "sources": {
            "survival_measurement": "survival-evidence.json:measurement",
            "format_profile": "format-evidence.json:profile.broad_evidence",
            "observer_id": observer_id,
            "native_artifact": {"id": native_artifact_id, "sha256": native_artifact_sha256},
        },
        "claim_limit": "one prospective Claude Code CLI calibration; no score or vendor claim",
    }


def _artifact_hashes(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "artifact-hashes.json":
            continue
        rows.append({"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": _digest_file(path)})
    return rows


def _finalize_hashes(root: Path) -> str:
    return _write_json(root / "artifact-hashes.json", {"schema_version": "session-bench-artifact-hashes-v1", "artifacts": _artifact_hashes(root)})


def run_calibration(attempt_id: str = ATTEMPT_ID, *, repetition: int = REPETITION) -> dict[str, Any]:
    if type(repetition) is not int or repetition not in (1, 2, 3):
        raise CalibrationError("repetition must be 1, 2, or 3")
    repo = _repo_root()
    artifact_root = _artifact_root(repo, attempt_id)
    artifact_root.mkdir(parents=True, exist_ok=False)
    project_root: Path | None = None
    state: dict[str, Any] = {
        "schema_version": CONTROLLER_SCHEMA,
        "attempt_id": attempt_id,
        "configuration_id": CONFIGURATION_ID,
        "repetition": repetition,
        "score_eligible": False,
        "published": False,
        "status": "running",
        "started_at": _utcnow(),
        "privacy": {
            "normal_root": str(CLAUDE_PROJECTS),
            "before_after_inventory_metadata_only": True,
            "unrelated_preexisting_sessions_read": False,
            "copied_family_scope": "one newly-created synthetic project key only",
        },
    }
    _write_json(artifact_root / "controller-start.json", state)
    try:
        template_path = repo / WORKLOAD_RELATIVE
        template = json.loads(template_path.read_text(encoding="utf-8"))
        workload, env_binding = instantiate_workload(template, f"{attempt_id}-r{repetition}")
        _write_json(artifact_root / "workload-instance.json", workload)
        project_root = _fresh_project_root(attempt_id)
        fixture = _copy_fixture(repo, project_root)
        initial_file_paths = {
            path.relative_to(fixture).as_posix()
            for path in fixture.rglob("*")
            if path.is_file()
        }
        initial_noncheckout_hashes = {
            relative: _digest_file(fixture / relative)
            for relative in initial_file_paths
            if relative != "checkout.py"
        }
        before_checkout = _hash_checkout(fixture)
        protected_before = _hash_protected_helper(fixture)
        if (fixture / ".survival-observer.jsonl").exists():
            raise CalibrationError("fresh synthetic fixture unexpectedly contains a helper ledger")
        if not CLAUDE_PROJECTS.is_dir() or CLAUDE_PROJECTS.is_symlink():
            raise CalibrationError("normal Claude project root is unavailable")
        before_summary, before_keys, _before_entries = _inventory_summary(CLAUDE_PROJECTS)
        _write_json(artifact_root / "root-inventory-before.json", before_summary)
        state.update({
            "project_root": str(project_root),
            "fixture_project": str(fixture),
            "run_canary": workload["run_canary"],
            "before_checkout_sha256": before_checkout,
            "protected_bench_check_sha256": protected_before,
            "claude_version": CLAUDE_VERSION,
            "workload_run_id": workload["run_id"],
            "environment_safe": env_binding,
        })
        _write_json(artifact_root / "controller-prepared.json", state)
        env = dict(os.environ)
        env.update(env_binding)
        prompts = {str(turn["sequence"]): str(turn["text"]) for turn in workload["turns"]}
        response_canaries = {str(turn["sequence"]): str(turn["response_canary"]) for turn in workload["turns"]}

        argv1 = _claude_argv(project_root, prompts["1"])
        run1 = _run_cli(argv1, project_root, env, artifact_root / "stdout-r1.jsonl", artifact_root / "stderr-r1.txt")
        if run1["returncode"] != 0:
            raise CalibrationError(f"Claude R1 exited with {run1['returncode']} (authentication/runtime refusal is invalid/N/A)")
        try:
            normalized1 = claude_stream_to_observer_jsonl(run1["stdout"].decode("utf-8"), turn=1, run_canary=workload["run_canary"], workspace=project_root)
        except (UnicodeDecodeError, ClaudeLiveError) as exc:
            raise CalibrationError(f"Claude R1 stream did not bind: {exc}") from exc
        if response_canaries["1"] not in _stream_jsonl(normalized1):
            raise CalibrationError("Claude R1 response canary is absent (invalid/N/A workload refusal)")
        session_id = normalized1["session_id"]
        state["turn1"] = {"returncode": run1["returncode"], "session_id": session_id, "model": normalized1.get("model"), "argv_redacted": _redact_argv(argv1, prompts["1"]), "prompt_sha256": _prompt_sha256(prompts["1"]), "stdout_sha256": run1["stdout_sha256"], "stderr_sha256": run1["stderr_sha256"]}
        _write_json(artifact_root / "normalized-r1.json", normalized1)
        _write_json(artifact_root / "controller-r1.json", state)

        argv2 = _claude_argv(project_root, prompts["2"], session_id=session_id)
        run2 = _run_cli(argv2, project_root, env, artifact_root / "stdout-r2.jsonl", artifact_root / "stderr-r2.txt")
        if run2["returncode"] != 0:
            raise CalibrationError(f"Claude R2 exited with {run2['returncode']} (authentication/runtime refusal is invalid/N/A)")
        try:
            normalized2 = claude_stream_to_observer_jsonl(run2["stdout"].decode("utf-8"), turn=2, run_canary=workload["run_canary"], expected_session_id=session_id, workspace=project_root)
        except (UnicodeDecodeError, ClaudeLiveError) as exc:
            raise CalibrationError(f"Claude R2 continuation stream did not bind: {exc}") from exc
        if response_canaries["2"] not in _stream_jsonl(normalized2):
            raise CalibrationError("Claude R2 response canary is absent (invalid/N/A workload refusal)")
        state["turn2"] = {"returncode": run2["returncode"], "session_id": normalized2["session_id"], "model": normalized2.get("model"), "argv_redacted": _redact_argv(argv2, prompts["2"]), "prompt_sha256": _prompt_sha256(prompts["2"]), "stdout_sha256": run2["stdout_sha256"], "stderr_sha256": run2["stderr_sha256"]}
        _write_json(artifact_root / "normalized-r2.json", normalized2)
        _write_json(artifact_root / "controller-r2.json", state)
        if normalized1["session_id"] != normalized2["session_id"]:
            raise CalibrationError("Claude R1/R2 session IDs differ")

        after_checkout = _hash_checkout(fixture)
        protected_after = _hash_protected_helper(fixture)
        if protected_after != protected_before:
            raise CalibrationError("protected bench_check.py changed")
        if after_checkout == before_checkout:
            raise CalibrationError("checkout.py did not change")
        semantic = _run_semantic_cases(fixture)
        if not all(case["passed"] for case in semantic):
            raise CalibrationError("final synthetic checkout semantic cases did not all pass")
        ledger_raw, ledger_summary = _read_ledger(fixture, workload["run_canary"])
        _write_json(artifact_root / "helper-ledger-summary.json", ledger_summary)
        (artifact_root / "helper-ledger.jsonl").write_text(ledger_raw, encoding="utf-8")
        state.update({"after_checkout_sha256": after_checkout, "semantic_cases": semantic, "helper_ledger": ledger_summary})

        # The ledger and checkout are the only allowed mutable artifacts under
        # the synthetic fixture; snapshots and protected helper stay intact.
        allowed_changed = {"checkout.py", ".survival-observer.jsonl"}
        changed_paths = {path for path in initial_file_paths if path == "checkout.py"}
        after_files = {path.relative_to(fixture).as_posix() for path in fixture.rglob("*") if path.is_file()}
        unexpected = {path for path in after_files - initial_file_paths if path not in allowed_changed} | {path for path in initial_file_paths - after_files if path not in allowed_changed}
        changed_protected = {
            relative
            for relative, digest in initial_noncheckout_hashes.items()
            if relative in after_files and _digest_file(fixture / relative) != digest
        }
        unexpected |= changed_protected
        if unexpected:
            raise CalibrationError(f"synthetic fixture changed unexpected files: {sorted(unexpected)}")
        state["filesystem"] = {"before_checkout_sha256": before_checkout, "after_checkout_sha256": after_checkout, "protected_before": protected_before, "protected_after": protected_after, "changed_paths": sorted(changed_paths | ({".survival-observer.jsonl"} if ".survival-observer.jsonl" in after_files else set())), "unexpected_paths": sorted(unexpected)}

        project_key, selected_entries, after_summary = _selected_family(CLAUDE_PROJECTS, before_keys, project_root)
        _write_json(artifact_root / "root-inventory-after.json", after_summary)
        quiescence = wait_for_quiescence(CLAUDE_PROJECTS, selected_entries, interval_seconds=QUIESCENCE_INTERVAL, checks=QUIESCENCE_CHECKS)
        _write_json(artifact_root / "native-quiescence.json", quiescence.to_dict())
        state["project_key"] = project_key
        state["native_inventory"] = {"selected_file_count": len(selected_entries), "project_key": project_key, "before_project_key_absent": project_key not in before_keys, "quiescent": True}

        native_family_dir = artifact_root / "native-family"
        family_manifest, selected_relative, selected_bytes = _copy_selected_family(CLAUDE_PROJECTS, native_family_dir, selected_entries, project_key)
        _write_json(artifact_root / "native-family-manifest.json", family_manifest)
        native_bundle = artifact_root / "native-bundle"
        native_bundle.mkdir(parents=True, exist_ok=False)
        (native_bundle / "session.jsonl").write_bytes(selected_bytes)
        _write_decoder_manifest(native_bundle, selected_bytes)
        offline_bundle = artifact_root / "offline-bundle"
        shutil.copytree(native_bundle, offline_bundle, symlinks=False)
        decoded_current = decode_claude_code_bundle(native_bundle)
        decoded_offline = decode_claude_code_bundle(offline_bundle)
        if decoded_current != decoded_offline:
            raise CalibrationError("native and offline Claude decodes are not canonically equal")
        if decoded_offline.get("format") != "claude-code-jsonl-v1":
            raise CalibrationError("current Claude decoder did not report supported format")
        _write_json(artifact_root / "decoded.json", decoded_current)
        _write_json(artifact_root / "offline-decoded.json", decoded_offline)

        loss_control = artifact_root / "loss-control"
        loss_selection = _damage_selected_response(offline_bundle, loss_control, response_canaries["2"])
        decoded_loss = decode_claude_code_bundle(loss_control)
        if decoded_loss["counts"]["responses"] >= decoded_offline["counts"]["responses"]:
            raise CalibrationError("selected-loss control did not remove a decoded response")
        _write_json(loss_control / "decoded.json", decoded_loss)
        _write_json(loss_control / "selection.json", loss_selection)
        state["decoder"] = {"format": decoded_offline["format"], "native_counts": decoded_current["counts"], "offline_counts": decoded_offline["counts"], "canonical_equal": True, "selected_loss": {"intact_response_count": decoded_offline["counts"]["responses"], "damaged_response_count": decoded_loss["counts"]["responses"], **loss_selection}}

        model_id = normalized2.get("model") or normalized1.get("model") or "model-unreported"
        controller_state = {
            "turns": {"1": {"session_id": session_id}, "2": {"session_id": session_id}},
            "model": model_id,
            "configuration": "claude-code-cli-print-stream-json",
            "workspace": str(project_root),
        }
        stdout_by_turn = {1: _stream_jsonl(normalized1), 2: _stream_jsonl(normalized2)}
        observer = build_opencode_live_observer(workload=workload, controller_state=controller_state, stdout_by_turn=stdout_by_turn, helper_ledger_jsonl=ledger_raw, before_checkout_sha256=before_checkout, after_checkout_sha256=after_checkout)
        observer["method"] = OBSERVER_METHOD
        observer["configuration_id"] = CONFIGURATION_ID
        observer["repetition"] = repetition
        observer["native_stream_ids"] = [session_id]
        observer_sha = _write_json(artifact_root / "observer.json", observer)

        native_session_id = f"claude-session-{session_id}"
        native_session_sha = sha256_bytes(selected_bytes)
        native_manifest = {
            "schema_version": "session-bench-claude-cli-native-manifest-v1",
            "id": native_session_id,
            "configuration_id": CONFIGURATION_ID,
            "project_key": project_key,
            "selected_artifact": {"id": native_session_id, "path": selected_relative, "sha256": native_session_sha, "size_bytes": len(selected_bytes)},
            "family_manifest": {"id": "native-family-manifest", "sha256": _digest_file(artifact_root / "native-family-manifest.json")},
            "decode_manifest": {"id": "decode.json", "sha256": _digest_file(native_bundle / "decode.json")},
            "offline_copy": {"path": "offline-bundle/session.jsonl", "sha256": _digest_file(offline_bundle / "session.jsonl")},
            "new_family_only": True,
            "root_closure": "project-key directory closed; complete normal-root closure unresolved",
            "unrelated_preexisting_sessions_read": False,
        }
        native_manifest_sha = _write_json(artifact_root / "native-manifest.json", native_manifest)
        native_facts = native_facts_from_claude_session(selected_bytes, run_canary=workload["run_canary"], workspace=project_root, before_sha256=before_checkout, after_sha256=after_checkout)
        measurement = compare_survival_run(observer, native_facts, {"complete_root": None, "companions_present": True, "isolated_decode": True, "canonical_equality": True}, configuration_id=CONFIGURATION_ID, repetition=repetition)
        _write_json(artifact_root / "native-facts.json", native_facts)
        _write_json(artifact_root / "measurement.json", measurement)

        format_evidence = build_claude_format_evidence(decoded_offline, observer={"id": "observer.json", "sha256": observer_sha}, native_manifest={"id": "native-manifest.json", "sha256": native_manifest_sha}, run_id=workload["run_id"], configuration_id=CONFIGURATION_ID, repetition=repetition, build=CLAUDE_VERSION, collected_on=_date_today(), result_id=f"{attempt_id}-format")
        format_sha = _write_json(artifact_root / "format-evidence.json", format_evidence)
        decoder_sha = _digest_file(repo / "session_bench/adapters/claude_code_decoder.py")
        survival_evidence = {
            "schema_version": PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
            "protocol_version": "1.0-survival",
            "workload_version": "1.0-survival-workload",
            "rubric_version": "1.0-survival-rubric",
            "run_id": workload["run_id"],
            "capture_id": f"{attempt_id}-capture",
            "evaluation_id": f"{attempt_id}-evaluation",
            "configuration_id": CONFIGURATION_ID,
            "repetition": repetition,
            "measurement": measurement,
            "observer": {"id": "observer.json", "sha256": observer_sha},
            "native_manifest": {"id": "native-manifest.json", "sha256": native_manifest_sha},
            "decoder": {"id": "claude-code-decoder.py", "sha256": decoder_sha},
            "identity": {
                "provider": "Anthropic",
                "harness": "Claude Code",
                "surface": "CLI",
                "execution_mode": "print-stream-json",
                "os": "macOS",
                "build": CLAUDE_VERSION,
                "model": model_id,
                "configuration": CONFIGURATION_ID,
                "protocol_version": "1.0-survival",
                "workload_version": "1.0-survival-workload",
                "observer_schema_version": "1.0-survival-observer",
                "rubric_version": "1.0-survival-rubric",
            },
            "metric_evidence": [
                {"metric_id": metric_id, "observer_ids": ["observer.json"], "native_locators": [{"artifact_id": native_session_id, "artifact_sha256": native_session_sha, "record_location": "session.jsonl"}]}
                for metric_id in SURVIVAL_METRICS
            ],
        }
        validate_prospective_evidence_input(survival_evidence)
        survival_sha = _write_json(artifact_root / "survival-evidence.json", survival_evidence)
        evidence_31 = _build_31_evidence(measurement, format_evidence, survival_evidence, observer_id="observer.json", native_artifact_id=native_session_id, native_artifact_sha256=native_session_sha)
        evidence_31_sha = _write_json(artifact_root / "evidence-31.json", evidence_31)
        state.update({
            "status": "complete",
            "completed_at": _utcnow(),
            "score_eligible": False,
            "published": False,
            "observer": {"path": "observer.json", "sha256": observer_sha},
            "native_manifest": {"path": "native-manifest.json", "sha256": native_manifest_sha},
            "decoder": {"format": decoded_offline["format"], "offline_decode": True, "canonical_equal": True, "source_sha256": decoder_sha},
            "measurement": {"path": "measurement.json", "metric_count": len(measurement["metrics"]), "states": {row["id"]: row["state"] for row in measurement["metrics"]}},
            "broad_wrapper": {"path": "format-evidence.json", "sha256": format_sha, "metric_count": len(format_evidence["profile"]["broad_evidence"])},
            "evidence_31": {"path": "evidence-31.json", "sha256": evidence_31_sha, "metric_count": len(evidence_31["metrics"])},
            "uncertainties": ["complete normal Claude root closure and cross-root companions remain unresolved", "one repetition is calibration evidence only", "Claude build is current local --version 2.1.272 while prospective plan recorded 2.1.270"],
        })
        _write_json(artifact_root / "attempt.json", state)
        _finalize_hashes(artifact_root)
        print(json.dumps({"status": "complete", "attempt_id": attempt_id, "artifact_root": str(artifact_root), "project_key": project_key, "session_id": session_id, "measurement_states": {row["id"]: row["state"] for row in measurement["metrics"]}, "score_eligible": False}, ensure_ascii=False, sort_keys=True))
        return state
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        state.update({"status": "invalid", "classification": "invalid/N/A", "error": error, "completed_at": _utcnow(), "score_eligible": False, "published": False})
        try:
            _write_json(artifact_root / "attempt.json", state)
            _finalize_hashes(artifact_root)
        except Exception as write_exc:
            print(f"could not finalize invalid attempt: {write_exc}", file=sys.stderr)
        print(json.dumps({"status": "invalid", "classification": "invalid/N/A", "attempt_id": attempt_id, "artifact_root": str(artifact_root), "error": error}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one fresh Claude Code CLI Survival v1 calibration")
    parser.add_argument("--attempt-id", default=ATTEMPT_ID)
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3), default=REPETITION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_calibration(args.attempt_id, repetition=args.repetition)
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
