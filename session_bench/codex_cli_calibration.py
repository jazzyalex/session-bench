"""One controlled two-turn Codex CLI survival calibration.

This module owns the narrow live route used before evaluated repetitions.  The
credential-bearing ``CODEX_HOME`` is never copied or inventoried as evidence:
only its ``sessions`` child is the declared native persistence root.  The
workload is public and synthetic, and the caller must supply an already
authenticated, fresh isolated ``CODEX_HOME``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from typing import Any, Mapping, Sequence

from .adapters.codex_cli import CODEX_RUN_CANARY_ENV, SubprocessCodexRunner
from .adapters.codex_cli_decoder import ExpectedAction, FrozenWorkload, decode_codex_cli_bundle
from .codex_format_evidence import build_codex_format_evidence
from .native_sanitize import sanitize_jsonl_paths
from .surface_capture import (
    IndependentStdoutObserver,
    InventoryEntry,
    canonical_bytes,
    copy_verified_artifacts,
    inventory_diff,
    inventory_tree,
    wait_for_quiescence,
    write_immutable_json,
)
from .v1_public_score import validate_format_profile
from .workload_instance import instantiate_workload


_THREAD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f-]{27}$", re.IGNORECASE)
_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload"
_CALIBRATION_WRAPPER_SCHEMA_VERSION = "1.0-survival-v1-calibration-wrapper"
_BLOCKING_STATES = frozenset({"unresolved", "decoder_unsupported", "unexercised", "invalid_capture"})


class CalibrationError(RuntimeError):
    """The controlled calibration did not establish its required evidence."""


@dataclass(frozen=True)
class CalibrationResult:
    run_root: Path
    thread_id: str
    private_package: Path
    public_package: Path
    intact_measurement_sha256: str
    damaged_measurement_sha256: str
    calibration_evidence: Path | None = None


def build_exec_argv(executable: str, *, cwd: Path, prompt: str, model: str) -> tuple[str, ...]:
    if not executable or not prompt or not model:
        raise CalibrationError("executable, prompt, and model are required")
    return (
        executable, "exec", "--ignore-user-config", "--json", "--sandbox", "workspace-write",
        "--model", model, "-C", str(Path(cwd).resolve()), prompt,
    )


def build_resume_argv(executable: str, *, thread_id: str, prompt: str, model: str) -> tuple[str, ...]:
    if not _THREAD_ID.fullmatch(thread_id) or not prompt or not model:
        raise CalibrationError("resume requires one observed thread id, prompt, and model")
    # ``exec resume`` does not expose the top-level ``--sandbox`` or ``-C``
    # flags.  More importantly, a resumed turn can receive a fresh permission
    # profile from the app-server; in that case it silently falls back to
    # read-only even though the first turn used ``workspace-write``.  Pin the
    # supported config key on the continuation so the second workload turn can
    # perform its authorized synthetic edit.  ``--model`` is repeated to
    # prevent a default-model drift at continuation.
    return (
        executable, "exec", "resume", "--ignore-user-config", "--json",
        "--config", 'sandbox_mode="workspace-write"', "--model", model, thread_id, prompt,
    )


def thread_id_from_stdout(lines: Sequence[bytes]) -> str:
    ids: list[str] = []
    for raw in lines:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationError("Codex stdout is not JSONL") from exc
        if value.get("type") == "thread.started":
            candidate = value.get("thread_id")
            if not isinstance(candidate, str) or not _THREAD_ID.fullmatch(candidate):
                raise CalibrationError("thread.started has no valid thread_id")
            ids.append(candidate)
    if len(ids) != 1:
        raise CalibrationError(f"expected exactly one thread.started event, found {len(ids)}")
    return ids[0]


def _stdout_contains(lines: Sequence[bytes], expected: str) -> bool:
    """Check an observed JSON event without depending on JSON escaping form."""

    def walk(value: Any) -> bool:
        if isinstance(value, str):
            return expected in value
        if isinstance(value, list):
            return any(walk(item) for item in value)
        if isinstance(value, dict):
            return any(walk(item) for item in value.values())
        return False

    for raw in lines:
        try:
            if walk(json.loads(raw.decode("utf-8"))):
                return True
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CalibrationError("Codex stdout is not JSONL") from exc
    return False


def _inventory_summary(
    entries: Sequence[InventoryEntry],
    *,
    label: str,
    new_entries: Sequence[InventoryEntry] = (),
) -> dict[str, Any]:
    """Return privacy-preserving metadata evidence for one native inventory.

    The full inventory is used in memory to distinguish a newly created file,
    but pre-existing relative paths are never written to the run artifact.  The
    digest is over stat metadata only; it is not a content hash.
    """

    metadata = [entry.to_dict() for entry in entries]
    return {
        "schema_version": "1.0-codex-cli-stat-inventory",
        "label": label,
        "root": "CODEX_HOME/sessions",
        "metadata_only": True,
        "preexisting_paths_disclosed": False,
        "file_count": len(entries),
        "total_size_bytes": sum(entry.size_bytes for entry in entries),
        "inventory_metadata_sha256": hashlib.sha256(canonical_bytes(metadata)).hexdigest(),
        "new_entries": [entry.to_dict() for entry in new_entries],
    }


def _synthetic_hashes(root: Path) -> dict[str, str]:
    """Hash only the staged synthetic fixture, never the native user root."""

    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _helper_ledger_rows(fixture: Path, *, run_canary: str) -> list[dict[str, Any]]:
    ledger = fixture / ".survival-observer.jsonl"
    if not ledger.is_file():
        raise CalibrationError("workload helper ledger was not written")
    try:
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationError("workload helper ledger is not valid JSONL") from exc
    expected = (("inspect", 0), ("baseline", 1), ("final", 0))
    if [(row.get("phase"), row.get("exit_code")) for row in rows] != list(expected):
        raise CalibrationError("helper ledger does not contain the required three outcomes")
    for phase, _exit_code in expected:
        row = rows[expected.index((phase, _exit_code))]
        if row.get("run_canary") != run_canary:
            raise CalibrationError(f"helper ledger {phase} row has the wrong run canary")
        if row.get("cwd") != "fixture_project":
            raise CalibrationError(f"helper ledger {phase} row has the wrong cwd")
        argv = row.get("argv")
        if argv != ["python3", "bench_check.py", phase]:
            raise CalibrationError(f"helper ledger {phase} row has the wrong helper argv")
        output = row.get("output")
        prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_"
        if not isinstance(output, str) or not output.startswith(prefix):
            raise CalibrationError(f"helper ledger {phase} row has no helper output canary")
        try:
            _nonce, body = output.split(" ", 1)
            decoded_body = json.loads(body)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CalibrationError(f"helper ledger {phase} output is not structured JSON") from exc
        if phase == "inspect":
            if not isinstance(decoded_body, dict) or not isinstance(decoded_body.get("checkout_source"), str):
                raise CalibrationError("inspect helper output did not contain checkout source")
        else:
            tests = decoded_body.get("tests") if isinstance(decoded_body, dict) else None
            if not isinstance(tests, list) or not tests:
                raise CalibrationError(f"{phase} helper output has unexpected case results")
            if any(not isinstance(item, dict) for item in tests):
                raise CalibrationError(f"{phase} helper output has malformed case results")
            if phase == "baseline":
                if all(item.get("passed") is True for item in tests):
                    raise CalibrationError("baseline helper output did not preserve a failing case")
            elif any(item.get("passed") is not True for item in tests):
                raise CalibrationError("final helper output has an unpassed case")
    return rows


def _required_helper_ledger(fixture: Path, *, run_id: str, run_canary: str | None = None) -> list[dict[str, Any]]:
    """Validate the three synthetic helper outcomes and their dynamic canary."""

    expected_canary = run_canary or f"SB_SURVIVAL_V1_RUN_{run_id}"
    return _helper_ledger_rows(fixture, run_canary=expected_canary)


def _fact_rows(decoded: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    facts = decoded.get("facts")
    if not isinstance(facts, Mapping):
        return result
    for family in facts.values():
        if isinstance(family, list):
            for row in family:
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    result[row["id"]] = row
        elif isinstance(family, Mapping):
            for row in family.values():
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    result[row["id"]] = row
    return result


def _native_metric_locators(decoded: Mapping[str, Any], metric: Mapping[str, Any]) -> list[dict[str, str]]:
    """Bind a survival metric to native locators without consulting observer truth."""

    facts = _fact_rows(decoded)
    locators: list[dict[str, str]] = []
    for fact_id in metric.get("fact_ids", ()):
        row = facts.get(fact_id)
        locator = row.get("locator") if isinstance(row, Mapping) else None
        if not isinstance(locator, Mapping):
            continue
        artifact_id = locator.get("artifact_id")
        artifact_sha256 = locator.get("artifact_sha256")
        record_location = locator.get("record_location")
        if all(isinstance(value, str) and value for value in (artifact_id, artifact_sha256, record_location)):
            item = {
                "artifact_id": artifact_id,
                "artifact_sha256": artifact_sha256,
                "record_location": record_location,
            }
            if item not in locators:
                locators.append(item)
    if locators:
        return locators
    artifacts = decoded.get("package", {}).get("artifacts", []) if isinstance(decoded.get("package"), Mapping) else []
    artifact = artifacts[0] if isinstance(artifacts, list) and artifacts else {}
    if isinstance(artifact, Mapping) and all(isinstance(artifact.get(key), str) and artifact.get(key) for key in ("id", "sha256", "path")):
        return [{
            "artifact_id": artifact["id"],
            "artifact_sha256": artifact["sha256"],
            "record_location": f"scan:{artifact['path']}:{metric.get('id', 'metric')}",
        }]
    raise CalibrationError("decoded metric has no native locator")


def _build_calibration_evidence(
    decoded: Mapping[str, Any],
    format_evidence: Mapping[str, Any],
    *,
    observer: Mapping[str, str],
    native_manifest: Mapping[str, str],
    native_mode: str,
    selected_loss: Mapping[str, Any],
    checkout_before_sha256: str,
    checkout_after_sha256: str,
    helper_ledger_sha256: str,
) -> dict[str, Any]:
    """Build one calibration-only, full 31-metric evidence wrapper.

    This is intentionally not a public score input.  It joins the 19 native
    measurement rows with the 12 broad profile rows and retains every blocking
    state instead of converting it into a zero or a guessed value.
    """

    native_metrics = decoded.get("metrics")
    if not isinstance(native_metrics, list) or len(native_metrics) != 19:
        raise CalibrationError("decoder did not return the frozen 19 survival metrics")
    broad_profile_document = format_evidence.get("profile") if isinstance(format_evidence, Mapping) else None
    try:
        broad_profile = validate_format_profile(broad_profile_document)
    except (TypeError, ValueError) as exc:
        raise CalibrationError("broad wrapper profile is not valid") from exc
    broad_metrics = broad_profile.get("metrics")
    if not isinstance(broad_metrics, list) or len(broad_metrics) != 12:
        raise CalibrationError("broad wrapper did not return the frozen 12 format metrics")

    metric_rows: list[dict[str, Any]] = []
    metric_evidence: list[dict[str, Any]] = []
    for metric in native_metrics:
        if not isinstance(metric, Mapping) or not isinstance(metric.get("id"), str):
            raise CalibrationError("decoder returned a malformed survival metric")
        metric_id = metric["id"]
        metric_rows.append({
            "id": metric_id,
            "source": "survival",
            "state": metric.get("state"),
            "correct": metric.get("correct"),
            "observed_eligible": metric.get("observed_eligible"),
            "decoded_eligible": metric.get("decoded_eligible"),
            "reason": metric.get("reason"),
        })
        metric_evidence.append({
            "metric_id": metric_id,
            "observer_ids": [observer["id"]],
            "native_locators": _native_metric_locators(decoded, metric),
        })
    for metric in broad_metrics:
        if not isinstance(metric, Mapping) or not isinstance(metric.get("id"), str):
            raise CalibrationError("broad builder returned a malformed metric")
        metric_id = metric["id"]
        metric_rows.append({
            "id": metric_id,
            "source": "format_profile",
            "state": metric.get("state"),
            "correct": metric.get("correct"),
            "observed_eligible": metric.get("observed_eligible"),
            "decoded_eligible": metric.get("decoded_eligible"),
        })
    metric_evidence.extend(format_evidence.get("metric_evidence", []))

    ids = [row["id"] for row in metric_rows]
    if len(ids) != 31 or len(set(ids)) != 31:
        raise CalibrationError("calibration wrapper does not contain exactly 31 unique metric IDs")
    unresolved = [row["id"] for row in metric_rows if row["state"] in _BLOCKING_STATES]
    resolved = [row["id"] for row in metric_rows if row["state"] not in _BLOCKING_STATES]
    wrapper = {
        "schema_version": _CALIBRATION_WRAPPER_SCHEMA_VERSION,
        "status": "calibration-only",
        "public_score_created": False,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "configuration_id": decoded["measurement"]["configuration_id"],
        "run_id": decoded["measurement"]["run_id"],
        "repetition": decoded["measurement"]["repetition"],
        "native": {
            "mode": native_mode,
            "root": "CODEX_HOME/sessions",
            "manifest": dict(native_manifest),
            "artifact_count": len(decoded.get("package", {}).get("artifacts", [])),
            "complete_root_proven": native_mode == "isolated",
        },
        "observer": dict(observer),
        "decoder": dict(decoded.get("decoder", {})),
        "measurement": decoded["measurement"],
        "format_evidence": format_evidence,
        "metrics": metric_rows,
        "metric_evidence": metric_evidence,
        "resolved_metric_ids": resolved,
        "unresolved_metric_ids": unresolved,
        "metric_counts": {"total": 31, "resolved": len(resolved), "unresolved": len(unresolved)},
        "selected_loss": dict(selected_loss),
        "synthetic_filesystem": {
            "changed_path": "fixture_project/checkout.py",
            "before_sha256": checkout_before_sha256,
            "after_sha256": checkout_after_sha256,
            "helper_ledger_sha256": helper_ledger_sha256,
        },
        "privacy": {
            "preexisting_native_content_opened": False,
            "preexisting_native_paths_disclosed": False,
            "native_inventory_mode": "metadata-only",
            "decoder_received_observer_truth": False,
            "decoder_received_original_native_root": False,
            "network_used_for_decode": False,
        },
    }
    return wrapper


def _stage_workload(run_root: Path, run_id: str) -> tuple[Path, Mapping[str, Any], FrozenWorkload]:
    run_root = Path(run_root).resolve()
    project = run_root / "project"
    fixture = project / "fixture_project"
    if run_root.exists():
        raise CalibrationError("calibration run root must be new")
    shutil.copytree(_FIXTURE / "fixture_project", fixture)
    template = json.loads((_FIXTURE / "workload.json").read_text(encoding="utf-8"))
    instance, _ = instantiate_workload(template, run_id)
    turns = tuple((item["id"], item["text"]) for item in instance["turns"])
    canaries = tuple((item["id"].replace("response-", "turn-"), item["response_canary"])
                      for item in instance["turns"])
    actions = tuple(
        ExpectedAction(
            item["id"], item["turn_id"], item["kind"],
            " ".join(item["argv"]) if item["kind"] != "edit" else None,
            item["target"], item["helper_nonce"],
        )
        for item in instance["actions"]
    )
    workload = FrozenWorkload(
        run_id=run_id,
        run_canary=instance["run_canary"],
        turns=turns,
        response_canaries=canaries,
        actions=actions,
    )
    return fixture, instance, workload


def _write_decode_manifest(package: Path, artifacts: Sequence[Mapping[str, Any]]) -> None:
    declared = [
        {
            "id": "rollout" if index == 0 else f"companion-{index}",
            "path": item["relative_path"], "sha256": item["sha256"],
            "size_bytes": item["size_bytes"], "depends_on": [] if index == 0 else ["rollout"],
        }
        for index, item in enumerate(artifacts)
    ]
    (package / "decode.json").write_bytes(canonical_bytes({"format": "codex-rollout-v1", "artifacts": declared}) + b"\n")


def _copy_package(source: Path, destination: Path) -> None:
    if destination.exists():
        raise CalibrationError("derived package destination already exists")
    shutil.copytree(source, destination, symlinks=False)


def _refresh_decode_manifest(package: Path) -> None:
    manifest_path = package / "decode.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        data = (package / artifact["path"]).read_bytes()
        artifact["sha256"] = hashlib.sha256(data).hexdigest()
        artifact["size_bytes"] = len(data)
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")


def _measurement_digest(decoded: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(decoded["measurement"])).hexdigest()


def _remove_selected_r2_response(package: Path, canary: str) -> None:
    manifest = json.loads((package / "decode.json").read_text(encoding="utf-8"))
    rollout = package / manifest["artifacts"][0]["path"]
    lines = rollout.read_bytes().splitlines(keepends=True)
    r2_turn_ids: set[str] = set()
    parsed = [json.loads(line.decode("utf-8")) for line in lines]

    # Direct Desktop tasks store the final assistant response as a single
    # response_item without a turn ID.  Its unique required canary is the
    # narrowest native mutation available; do not require the CLI's three
    # duplicated response representations for this different writer shape.
    direct = []
    for index, value in enumerate(parsed):
        payload = value.get("payload") if isinstance(value, dict) else None
        content = payload.get("content") if isinstance(payload, dict) else None
        text = "".join(item.get("text", "") for item in content if isinstance(item, dict)) if isinstance(content, list) else ""
        if (
            value.get("type") == "response_item" and isinstance(payload, dict)
            and payload.get("type") == "message" and payload.get("role") == "assistant"
            and canary in text
        ):
            direct.append(index)
    if len(direct) == 1:
        rollout.write_bytes(b"".join(line for index, line in enumerate(lines) if index != direct[0]))
        _refresh_decode_manifest(package)
        return
    if len(direct) > 1:
        raise CalibrationError("selected-loss mutation found multiple native R2 responses")
    for value in parsed:
        payload = value.get("payload") if isinstance(value, dict) else None
        if value.get("type") != "response_item" or not isinstance(payload, dict) or payload.get("role") != "user":
            continue
        content = payload.get("content")
        text = "".join(item.get("text", "") for item in content if isinstance(item, dict)) if isinstance(content, list) else ""
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        if canary in text and isinstance(metadata, dict) and isinstance(metadata.get("turn_id"), str):
            r2_turn_ids.add(metadata["turn_id"])
    if len(r2_turn_ids) != 1:
        raise CalibrationError("selected-loss mutation could not identify one native R2 turn")
    kept: list[bytes] = []
    changed = 0
    for line, value in zip(lines, parsed, strict=True):
        payload = value.get("payload") if isinstance(value, dict) else None
        response_record = False
        if value.get("type") == "response_item" and isinstance(payload, dict):
            content = payload.get("content")
            text = "".join(item.get("text", "") for item in content if isinstance(item, dict)) if isinstance(content, list) else ""
            metadata = payload.get("internal_chat_message_metadata_passthrough")
            response_record = (
                payload.get("type") == "message" and payload.get("role") == "assistant"
                and isinstance(metadata, dict) and metadata.get("turn_id") in r2_turn_ids
            )
        elif value.get("type") == "event_msg" and isinstance(payload, dict):
            item = payload.get("item")
            if payload.get("type") == "item_completed" and isinstance(item, dict) and item.get("type") == "AgentMessage":
                content = item.get("content")
                response_record = payload.get("turn_id") in r2_turn_ids
            elif payload.get("type") == "task_complete":
                response_record = payload.get("turn_id") in r2_turn_ids
        if response_record:
            changed += 1
            continue
        kept.append(line)
    if changed < 3:
        raise CalibrationError("selected-loss mutation did not remove every native R2 response representation")
    rollout.write_bytes(b"".join(kept))
    _refresh_decode_manifest(package)


def _same_paths(entries: Sequence[InventoryEntry], expected: InventoryEntry) -> bool:
    return len(entries) == 1 and entries[0].relative_path == expected.relative_path


def _run_calibration(
    *,
    run_root: Path,
    isolated_codex_home: Path,
    run_id: str,
    model: str,
    executable: str = "/opt/homebrew/bin/codex",
    timeout_seconds: float = 600.0,
    runner: Any | None = None,
    authorized_existing_root: bool = False,
) -> CalibrationResult:
    """Execute one fresh synthetic two-turn calibration and retain private evidence.

    It raises on an invalid run.  A raised run is still left intact under the
    new run root for inspection; it is never overwritten or converted to a
    score.
    """

    run_root = Path(run_root).resolve()
    home = Path(isolated_codex_home).resolve()
    if not (home / "auth.json").is_file():
        raise CalibrationError("CODEX_HOME has no supported authenticated session")
    sessions = home / "sessions"
    if authorized_existing_root:
        if not sessions.is_dir():
            raise CalibrationError("authorized CODEX_HOME has no sessions directory")
        if sessions == run_root or run_root in sessions.parents:
            raise CalibrationError("authorized native root overlaps the calibration run root")
    else:
        sessions.mkdir(exist_ok=True)
    fixture, instance, workload = _stage_workload(run_root, run_id)
    capture = run_root / "capture"
    observer_root = run_root / "observer"
    capture.mkdir()
    observer_root.mkdir()
    write_immutable_json(run_root / "workload-instance.json", instance)
    checkout_before_sha256 = hashlib.sha256((fixture / "checkout.py").read_bytes()).hexdigest()
    synthetic_before = _synthetic_hashes(fixture)
    started_ns = time.time_ns()
    before = inventory_tree(sessions)
    write_immutable_json(capture / "stat-only-before.json", _inventory_summary(before, label="before"))
    if before and not authorized_existing_root:
        raise CalibrationError("isolated session root must be empty before calibration")
    runner = runner or SubprocessCodexRunner(timeout_seconds=timeout_seconds)
    observer_r1 = IndependentStdoutObserver()
    r1_lines: list[bytes] = []

    def observe_r1(line: bytes) -> None:
        r1_lines.append(line)
        observer_r1.observe(line)

    r1 = runner.run(
        build_exec_argv(executable, cwd=fixture, prompt=instance["turns"][0]["text"], model=model),
        env={"CODEX_HOME": str(home), CODEX_RUN_CANARY_ENV: workload.run_canary}, cwd=fixture, on_stdout=observe_r1,
    )
    observer_r1_receipt = observer_r1.freeze(observer_root / "turn-r1.stdout.jsonl")
    write_immutable_json(capture / "observer-r1.json", observer_r1_receipt)
    thread_id = thread_id_from_stdout(r1_lines)
    if r1.returncode != 0 or not _stdout_contains(r1_lines, instance["turns"][0]["response_canary"]):
        raise CalibrationError("R1 did not complete with its observed response canary")
    after_r1 = inventory_tree(sessions)
    new = inventory_diff(before, after_r1, started_ns=started_ns)
    write_immutable_json(capture / "stat-only-after-r1.json", _inventory_summary(after_r1, label="after-r1", new_entries=new))
    candidates = tuple(
        entry for entry in new
        if Path(entry.relative_path).name.startswith("rollout-") and entry.relative_path.endswith(".jsonl")
    )
    if len(candidates) != 1:
        raise CalibrationError("R1 did not create exactly one new native rollout")
    if len(new) != len(candidates):
        extras = [entry.relative_path for entry in new if entry not in candidates]
        raise CalibrationError(f"R1 created undeclared native files: {extras}")
    candidate = candidates[0]
    observer_r2 = IndependentStdoutObserver()
    r2_lines: list[bytes] = []

    def observe_r2(line: bytes) -> None:
        r2_lines.append(line)
        observer_r2.observe(line)

    r2 = runner.run(
        build_resume_argv(executable, thread_id=thread_id, prompt=instance["turns"][1]["text"], model=model),
        env={"CODEX_HOME": str(home), CODEX_RUN_CANARY_ENV: workload.run_canary}, cwd=fixture, on_stdout=observe_r2,
    )
    observer_r2_receipt = observer_r2.freeze(observer_root / "turn-r2.stdout.jsonl")
    write_immutable_json(capture / "observer-r2.json", observer_r2_receipt)
    if r2.returncode != 0 or not _stdout_contains(r2_lines, instance["turns"][1]["response_canary"]):
        raise CalibrationError("R2 did not complete with its observed response canary")
    after_r2 = inventory_tree(sessions)
    new_after_r2 = inventory_diff(before, after_r2, started_ns=started_ns)
    write_immutable_json(capture / "stat-only-after-r2.json", _inventory_summary(after_r2, label="after-r2", new_entries=new_after_r2))
    if len(new_after_r2) != 1 or new_after_r2[0].relative_path != candidate.relative_path or new_after_r2[0].filesystem_id != candidate.filesystem_id:
        raise CalibrationError("session root has an undeclared companion or extra persistence file")
    final_entry = new_after_r2[0]
    wait_for_quiescence(sessions, (final_entry,), checks=2, interval_seconds=0.0)
    helper_rows = _required_helper_ledger(fixture, run_id=run_id, run_canary=workload.run_canary)
    synthetic_after = _synthetic_hashes(fixture)
    changed_synthetic = sorted(set(synthetic_before) ^ set(synthetic_after) | {
        path for path in set(synthetic_before) & set(synthetic_after)
        if synthetic_before[path] != synthetic_after[path]
    })
    if changed_synthetic != [".survival-observer.jsonl", "checkout.py"]:
        raise CalibrationError(f"synthetic workload changed unexpected files: {changed_synthetic}")
    checkout_after_sha256 = synthetic_after["checkout.py"]
    helper_ledger_sha256 = hashlib.sha256((fixture / ".survival-observer.jsonl").read_bytes()).hexdigest()

    private_package = capture / "private-native"
    copied = copy_verified_artifacts(sessions, private_package, (final_entry,), roles={final_entry.relative_path: "native-primary"})
    _write_decode_manifest(private_package, copied)
    complete_root = not authorized_existing_root
    required_companions: Sequence[str] | None = () if complete_root else None
    intact = decode_codex_cli_bundle(
        private_package, workload=workload, complete_root=complete_root,
        required_companions=required_companions,
        isolated_decode_proven=True, canonical_equality_proven=True,
    )
    intact_digest = _measurement_digest(intact)
    intact_decoded_sha256 = hashlib.sha256(canonical_bytes(intact)).hexdigest()
    write_immutable_json(capture / "decoded-intact.json", intact)
    offline_copy = capture / "offline-native"
    _copy_package(private_package, offline_copy)
    copied_decoded = decode_codex_cli_bundle(
        offline_copy, workload=workload, complete_root=complete_root,
        required_companions=required_companions,
        isolated_decode_proven=True, canonical_equality_proven=True,
    )
    if _measurement_digest(copied_decoded) != intact_digest:
        raise CalibrationError("offline copied package changed the canonical measurement")
    offline_decoded_sha256 = hashlib.sha256(canonical_bytes(copied_decoded)).hexdigest()
    if offline_decoded_sha256 != intact_decoded_sha256:
        raise CalibrationError("offline copied package changed the canonical decoded representation")
    write_immutable_json(capture / "decoded-offline.json", copied_decoded)
    damaged = capture / "damage-r2-response"
    _copy_package(offline_copy, damaged)
    _remove_selected_r2_response(damaged, instance["turns"][1]["response_canary"])
    damaged_decoded = decode_codex_cli_bundle(
        damaged, workload=workload, complete_root=complete_root,
        required_companions=required_companions,
        isolated_decode_proven=True, canonical_equality_proven=True,
    )
    intact_r2_responses = [
        row for row in intact["facts"]["visible_responses"]
        if instance["turns"][1]["response_canary"] in (row.get("text") or "")
    ]
    damaged_r2_responses = [
        row for row in damaged_decoded["facts"]["visible_responses"]
        if instance["turns"][1]["response_canary"] in (row.get("text") or "")
    ]
    if len(intact_r2_responses) != 1 or damaged_r2_responses:
        raise CalibrationError("selected-loss copy still reconstructs R2 response")
    write_immutable_json(capture / "decoded-damaged.json", damaged_decoded)
    damage_locations = [row["locator"] for row in intact_r2_responses if isinstance(row.get("locator"), Mapping)]
    selected_loss = {
        "fact": "turn-r2 visible response",
        "canary": instance["turns"][1]["response_canary"],
        "intact_present": True,
        "damaged_present": False,
        "detected": True,
        "native_locations": damage_locations,
        "transformation": "remove every native R2 response representation from copied rollout",
    }
    public_package = capture / "public-native"
    public_package.mkdir()
    public_rollout = public_package / final_entry.relative_path
    public_rollout.parent.mkdir(parents=True, exist_ok=True)
    receipt = sanitize_jsonl_paths(
        private_package / final_entry.relative_path, public_rollout,
        replacements={
            str(run_root): "$RUN_ROOT", str(home): "$ISOLATED_CODEX_HOME",
            str(Path.home()): "$USER_HOME",
        },
    )
    public_artifact = {"relative_path": final_entry.relative_path, "sha256": hashlib.sha256(public_rollout.read_bytes()).hexdigest(), "size_bytes": public_rollout.stat().st_size}
    _write_decode_manifest(public_package, (public_artifact,))
    observer_binding = {
        "id": "codex-cli-runner-stdout-r1-r2",
        "sha256": hashlib.sha256(canonical_bytes({
            "r1": observer_r1_receipt["sha256"], "r2": observer_r2_receipt["sha256"],
        })).hexdigest(),
    }
    native_manifest_binding = {
        "id": "capture/private-native/decode.json",
        "sha256": hashlib.sha256((private_package / "decode.json").read_bytes()).hexdigest(),
    }
    collected_on = datetime.now(timezone.utc).date().isoformat()
    format_evidence = build_codex_format_evidence(
        copied_decoded,
        observer=observer_binding,
        native_manifest=native_manifest_binding,
        build=str(copied_decoded.get("identity", {}).get("cli_version", "unresolved")),
        collected_on=collected_on,
        result_id=f"{run_id}-format",
    )
    write_immutable_json(capture / "format-evidence.json", format_evidence)
    calibration_evidence = _build_calibration_evidence(
        copied_decoded,
        format_evidence,
        observer=observer_binding,
        native_manifest=native_manifest_binding,
        native_mode="authorized-existing-account" if authorized_existing_root else "isolated",
        selected_loss=selected_loss,
        checkout_before_sha256=checkout_before_sha256,
        checkout_after_sha256=checkout_after_sha256,
        helper_ledger_sha256=helper_ledger_sha256,
    )
    calibration_evidence_path = capture / "calibration-evidence.json"
    write_immutable_json(calibration_evidence_path, calibration_evidence)
    write_immutable_json(capture / "calibration-receipt.json", {
        "schema_version": "1.0-codex-cli-calibration", "configuration_id": "codex-cli",
        "run_id": run_id, "model": model, "thread_id": thread_id,
        "native_root": "CODEX_HOME/sessions",
        "native_mode": "authorized-existing-account" if authorized_existing_root else "isolated",
        "complete_root": complete_root,
        "preexisting_native_content_opened": False,
        "preexisting_native_paths_disclosed": False,
        "turn_returncodes": [r1.returncode, r2.returncode],
        "private_package": "capture/private-native", "public_package": "capture/public-native",
        "intact_measurement_sha256": intact_digest,
        "offline_measurement_sha256": _measurement_digest(copied_decoded),
        "damaged_measurement_sha256": _measurement_digest(damaged_decoded),
        "intact_decoded_sha256": intact_decoded_sha256,
        "offline_decoded_sha256": offline_decoded_sha256,
        "selected_loss": selected_loss,
        "checkout_before_sha256": checkout_before_sha256,
        "checkout_after_sha256": checkout_after_sha256,
        "helper_ledger_sha256": helper_ledger_sha256,
        "stat_inventory_artifacts": [
            "capture/stat-only-before.json", "capture/stat-only-after-r1.json", "capture/stat-only-after-r2.json",
        ],
        "calibration_evidence": "capture/calibration-evidence.json",
        "format_evidence": "capture/format-evidence.json",
        "public_score_created": False,
        "metric_counts": calibration_evidence["metric_counts"],
        "public_sanitization": receipt,
    })
    return CalibrationResult(
        run_root, thread_id, private_package, public_package, intact_digest,
        _measurement_digest(damaged_decoded), calibration_evidence_path,
    )


def run_calibration(
    *,
    run_root: Path,
    isolated_codex_home: Path,
    run_id: str,
    model: str,
    executable: str = "/opt/homebrew/bin/codex",
    timeout_seconds: float = 600.0,
    runner: Any | None = None,
    authorized_existing_root: bool = False,
) -> CalibrationResult:
    """Run a calibration and leave a minimal immutable receipt on rejection.

    The receipt deliberately avoids native bytes, prompt content, and private
    home paths.  It makes a failed setup or workload run visible without
    turning it into evidence about session-format quality.
    """

    try:
        return _run_calibration(
            run_root=run_root, isolated_codex_home=isolated_codex_home, run_id=run_id,
            model=model, executable=executable, timeout_seconds=timeout_seconds, runner=runner,
            authorized_existing_root=authorized_existing_root,
        )
    except CalibrationError as exc:
        root = Path(run_root).resolve()
        receipt = root / "invalid-attempt.json"
        if root.is_dir() and not receipt.exists():
            write_immutable_json(receipt, {
                "schema_version": "1.0-codex-cli-calibration-attempt",
                "configuration_id": "codex-cli", "run_id": run_id, "model": model,
                "state": "invalid", "public_score_eligible": False,
                "native_root": "CODEX_HOME/sessions",
                "reason_class": type(exc).__name__,
            })
        raise


__all__ = [
    "CalibrationError", "CalibrationResult", "build_exec_argv", "build_resume_argv",
    "run_calibration", "thread_id_from_stdout",
]
