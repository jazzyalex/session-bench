"""Package one captured OpenCode survival-v1 run as traceable local evidence.

This module is deliberately offline.  It consumes one explicit run directory,
copies only its isolated SQLite/WAL/SHM family, rebuilds the independent
observer, decodes the copied family while every earlier native copy is denied,
and binds all 19 measurements to observer IDs and native record locators.
It never launches OpenCode, discovers a profile, scores another product, or
publishes an artifact.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence

from .adapters.opencode_cli import read_sqlite_session_ids
from .adapters.opencode_decoder import OPENCODE_BUNDLE_FILES, decode_opencode_bundle
from .live_metric_comparator import compare_survival_run
from .live_observer import build_opencode_live_observer
from .opencode_replay_runtime import MANIFEST_FILENAME, build_runtime
from .survival_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    RUBRIC_VERSION,
    WORKLOAD_VERSION,
    score_evidence_run,
    validate_evidence_input,
)
from .survival_metrics import METRICS
from .workload_instance import instantiate_workload


EVALUATION_SCHEMA_VERSION = "session-bench-opencode-evaluation-v1"
CONFIGURATION_ID = "opencode-cli"
MODEL_ID = "opencode/muse-spark-1.3-contributor-free"
SUPPORTED_BUILD = "1.18.30"


class OpenCodeEvidenceError(ValueError):
    """A captured run cannot support a reportable evidence package."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenCodeEvidenceError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise OpenCodeEvidenceError(f"{path.name} must contain one JSON object")
    return value


def _write_json(path: Path, value: Any) -> str:
    payload = _canonical(value) + b"\n"
    path.write_bytes(payload)
    return _sha256_bytes(payload)


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise OpenCodeEvidenceError(f"cannot hash {path.name}: {exc}") from exc


def _package_file_entries(root: Path) -> list[dict[str, Any]]:
    """Hash the closed package content, excluding the self-referential manifest."""

    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == "package-manifest.json":
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def _verify_closed_package(root: Path) -> dict[str, Any]:
    """Verify a package manifest without selecting a decoder implementation.

    Corrections need to bind historical bytes even when the old package's
    decoder source is unavailable.  This deliberately checks only the closed
    package boundary and its declared file hashes; replay qualification occurs
    later against the new embedded runtime.
    """
    if root.is_symlink() or not root.is_dir():
        raise OpenCodeEvidenceError("historical package must be an ordinary directory")
    if any(path.is_symlink() for path in root.rglob("*")):
        raise OpenCodeEvidenceError("historical package must not contain symlinks")
    package = _read_json(root / "package-manifest.json")
    core = {key: value for key, value in package.items() if key != "package_digest"}
    if _sha256_bytes(_canonical(core)) != package.get("package_digest"):
        raise OpenCodeEvidenceError("historical package digest mismatch")
    files = package.get("files")
    if not isinstance(files, list):
        raise OpenCodeEvidenceError("historical package file list is missing")
    expected = {
        str(item.get("path")) for item in files if isinstance(item, Mapping)
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "package-manifest.json"
    }
    if expected != actual or len(expected) != len(files):
        raise OpenCodeEvidenceError("historical package file boundary mismatch")
    for item in files:
        if not isinstance(item, Mapping):
            raise OpenCodeEvidenceError("historical package file entry is malformed")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise OpenCodeEvidenceError("historical package file path is malformed")
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise OpenCodeEvidenceError("historical package file is missing")
        if _sha256_file(path) != item.get("sha256") or path.stat().st_size != item.get("size_bytes"):
            raise OpenCodeEvidenceError(f"historical package artifact mismatch: {relative}")
    return package


def _correction_binding(
    historical_package_dir: Path | str,
    *,
    attempt_id: str,
) -> dict[str, Any]:
    """Create a compact immutable link to one verified historical package."""
    historical = Path(historical_package_dir).resolve(strict=True)
    package = _verify_closed_package(historical)
    if package.get("attempt_id") != attempt_id:
        raise OpenCodeEvidenceError("historical package belongs to a different attempt")
    evidence = _read_json(historical / "evidence.json")
    summary = _read_json(historical / "summary.json")
    native_manifest = _read_json(historical / "native-manifest.json")
    old_files = package.get("files")
    assert isinstance(old_files, list)  # established by _verify_closed_package
    native_files = [
        {"path": item["path"], "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
        for item in old_files
        if isinstance(item, Mapping) and str(item.get("path", "")).startswith("native-bundle/")
    ]
    if len(native_files) != len(OPENCODE_BUNDLE_FILES):
        raise OpenCodeEvidenceError("historical package lacks its complete native family")
    return {
        "reason": "missing_decoder_runtime",
        "supersedes": {
            "package_id": package.get("package_id"),
            "package_digest": package.get("package_digest"),
            "result_id": package.get("result_id"),
            "native_manifest_sha256": _sha256_file(historical / "native-manifest.json"),
            "native_files": native_files,
            "decoder_sha256": evidence.get("decoder", {}).get("sha256"),
            "decoded_sha256": _sha256_file(historical / "decoded.json"),
            "measurement_sha256": _sha256_file(historical / "measurement.json"),
            "survival_score": summary.get("score"),
        },
    }


def _normalized_decode(value: Mapping[str, Any]) -> bytes:
    normalized = deepcopy(dict(value))
    bundle = normalized.get("bundle")
    if isinstance(bundle, dict):
        bundle["path"] = "bundle"
    return _canonical(normalized)


def _source_bundle(run_root: Path, state: Mapping[str, Any]) -> Path:
    offline = run_root / "offline-bundle"
    if offline.is_dir():
        return offline
    if all((run_root / name).is_file() for name in OPENCODE_BUNDLE_FILES):
        return run_root
    raise OpenCodeEvidenceError("captured run has no explicit SQLite/WAL/SHM family")


def _source_native_files(run_root: Path) -> list[Path]:
    result: list[Path] = []
    for directory in (run_root, run_root / "capture", run_root / "offline-bundle"):
        for name in OPENCODE_BUNDLE_FILES:
            candidate = directory / name
            if candidate.is_file() and candidate not in result:
                result.append(candidate)
    return result


def _session_ids_in_bundle(bundle: Path) -> set[str]:
    """Read the complete session-ID boundary from a throwaway bundle copy."""
    with tempfile.TemporaryDirectory(prefix="session-bench-evidence-sessions-") as raw:
        clone = Path(raw) / "bundle"
        shutil.copytree(bundle, clone, symlinks=False)
        return {
            str(value)
            for value in read_sqlite_session_ids(clone / OPENCODE_BUNDLE_FILES[0])
            if str(value)
        }


def _load_workload(repository_root: Path, run_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    explicit = run_root / "workload-instance.json"
    if explicit.is_file():
        return _read_json(explicit)
    run_slug = state.get("run_slug")
    if not isinstance(run_slug, str) or not run_slug:
        raise OpenCodeEvidenceError("captured run has no reconstructable workload identity")
    template = _read_json(
        repository_root
        / "fixtures/scenarios/survival-v1/workload/workload.json"
    )
    workload, _ = instantiate_workload(template, run_slug)
    recorded = state.get("workload")
    if not isinstance(recorded, Mapping) or not isinstance(recorded.get("turns"), list):
        raise OpenCodeEvidenceError("controller state has no frozen workload turns")
    expected_turns = [
        {
            "id": item.get("id"),
            "sequence": item.get("sequence"),
            "text": item.get("text"),
            "response_canary": item.get("response_canary"),
            "prompt_sha256": _sha256_bytes(str(item.get("text")).encode("utf-8")),
        }
        for item in workload.get("turns", [])
    ]
    if recorded.get("turns") != expected_turns:
        raise OpenCodeEvidenceError("controller workload does not match the frozen instance")
    if state.get("run_canary") != workload.get("run_canary"):
        raise OpenCodeEvidenceError("controller run canary does not match the workload")
    return workload


def _observer_inputs(
    run_root: Path, state: Mapping[str, Any], workload: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[int, str], str, str, str]:
    raw_turns = state.get("turns")
    if isinstance(raw_turns, Mapping) and set(raw_turns) == {"1", "2"}:
        records = {str(key): value for key, value in raw_turns.items()}
        if not all(isinstance(value, Mapping) for value in records.values()):
            raise OpenCodeEvidenceError("controller turn records are malformed")
        stdout = {
            number: str(records[str(number)].get("stdout", ""))
            for number in (1, 2)
        }
        controller = {
            "model": state.get("model"),
            "configuration_id": CONFIGURATION_ID,
            "workspace": state.get("workspace"),
            "turns": {
                key: {
                    "session_id": records[key].get("session_id"),
                    "status": records[key].get("status"),
                }
                for key in ("1", "2")
            },
        }
    else:
        session_id = state.get("session_id")
        controller = {
            "model": state.get("model"),
            "configuration_id": CONFIGURATION_ID,
            "workspace": str(run_root / "project"),
            "turns": {
                "1": {"session_id": session_id, "status": "ok"},
                "2": {"session_id": session_id, "status": "ok"},
            },
        }
        stdout = {
            number: (run_root / f"observer/turn-r{number}.stdout.jsonl").read_text(
                encoding="utf-8"
            )
            for number in (1, 2)
        }
    if any(not value.strip() for value in stdout.values()):
        raise OpenCodeEvidenceError("both captured stdout streams are required")
    fixture = run_root / "project/fixture_project"
    ledger_path = fixture / ".survival-observer.jsonl"
    before_path = fixture / "snapshots/checkout.before.py"
    after_path = fixture / "checkout.py"
    try:
        ledger = ledger_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OpenCodeEvidenceError(f"cannot read helper ledger: {exc}") from exc
    return (
        controller,
        stdout,
        ledger,
        _sha256_file(before_path),
        _sha256_file(after_path),
    )


def _record_location(locator: Mapping[str, Any]) -> str:
    table = locator.get("table")
    row_id = locator.get("row_id")
    if not isinstance(table, str) or not isinstance(row_id, str):
        raise OpenCodeEvidenceError("native record has no stable table/row locator")
    return f"table:{table}/row:{row_id}"


def _native_locator(run_id: str, record: Mapping[str, Any]) -> dict[str, str]:
    locator = record.get("locator")
    if not isinstance(locator, Mapping):
        raise OpenCodeEvidenceError("native record has no locator")
    digest = locator.get("artifact_sha256")
    if not isinstance(digest, str):
        raise OpenCodeEvidenceError("native record locator has no artifact digest")
    return {
        "artifact_id": f"{run_id}:opencode.db",
        "artifact_sha256": digest,
        "record_location": _record_location(locator),
    }


def _dedupe_locators(values: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (
            value["artifact_id"],
            value["artifact_sha256"],
            value["record_location"],
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _metric_observer_ids(observer: Mapping[str, Any], metric_id: str) -> list[str]:
    ids = [
        str(event["id"])
        for event in observer.get("events", [])
        if isinstance(event, Mapping)
        and metric_id in event.get("metric_ids", [])
    ]
    relation_kind = {
        "causal.action_result": "action_result",
        "causal.turn_response": "turn_response",
        "revision.r1_r2_order": "supersedes",
        "revision.final_after_r2": "final_after",
    }.get(metric_id)
    if relation_kind:
        ids.extend(
            str(relation["id"])
            for relation in observer.get("relations", [])
            if isinstance(relation, Mapping) and relation.get("kind") == relation_kind
        )
    if metric_id.startswith("portable."):
        ids.append("portable-observation")
    values = list(dict.fromkeys(ids))
    if not values:
        raise OpenCodeEvidenceError(f"metric {metric_id} has no observer evidence ID")
    return values


def _records_for_metric(
    decoded: Mapping[str, Any],
    observer: Mapping[str, Any],
    metric_id: str,
) -> list[Mapping[str, Any]]:
    mapping = {
        "work.submitted_turns": "turns",
        "work.visible_responses": "responses",
        "work.actions": "actions",
        "work.results": "results",
        "work.changed_files": "file_changes",
        "causal.action_result": "relations",
        "causal.turn_response": "relations",
        "revision.r1": "turns",
        "revision.r2": "turns",
        "revision.r1_r2_order": "turns",
        "revision.final_after_r2": "relations",
        "attribution.model_config": "responses",
        "attribution.usage": "usage",
        "attribution.token_semantics": "usage",
        "attribution.reconciliation": "usage",
    }
    key = mapping.get(metric_id)
    if key is None:
        return []
    values = decoded.get(key)
    if not isinstance(values, list):
        return []
    result = [item for item in values if isinstance(item, Mapping)]
    primary_events = [
        item
        for item in observer.get("events", [])
        if isinstance(item, Mapping) and item.get("population_role") == "primary_scored"
    ]
    expected_actions = [item for item in primary_events if item.get("kind") == "action"]
    expected_results = [item for item in primary_events if item.get("kind") == "result"]
    action_ids = {
        raw
        for item in expected_actions
        for raw in (
            item.get("fields", {}).get("native_action_id"),
            item.get("fields", {}).get("call_id"),
        )
        if isinstance(raw, str)
    }
    result_ids = {
        raw
        for item in expected_results
        for raw in (
            item.get("fields", {}).get("native_result_id"),
            item.get("fields", {}).get("call_id"),
        )
        if isinstance(raw, str)
    }
    response_canaries = {
        item.get("fields", {}).get("canary")
        for item in primary_events
        if item.get("kind") == "assistant_response"
        and isinstance(item.get("fields", {}).get("canary"), str)
    }
    matched_responses = [
        item for item in decoded.get("responses", [])
        if isinstance(item, Mapping) and item.get("canary") in response_canaries
    ]
    response_ids = {
        item.get("id") for item in matched_responses if isinstance(item.get("id"), str)
    }
    turn_ids = {
        item.get("id")
        for item in decoded.get("turns", [])
        if isinstance(item, Mapping)
        and item.get("revision") in {"r1", "r2"}
        and isinstance(item.get("id"), str)
    }
    if metric_id == "work.actions":
        result = [
            item for item in result
            if item.get("id") in action_ids or item.get("call_id") in action_ids
        ]
    elif metric_id == "work.results":
        result = [
            item for item in result
            if item.get("id") in result_ids or item.get("call_id") in result_ids
        ]
    elif metric_id == "work.changed_files":
        result = [
            item for item in result
            if isinstance(item.get("before_sha256"), str)
            and isinstance(item.get("after_sha256"), str)
        ]
    if metric_id == "causal.action_result":
        result = [
            item for item in result
            if item.get("kind") == "action_result"
            and item.get("from_id") in action_ids
            and item.get("to_id") in result_ids
        ]
    elif metric_id == "causal.turn_response":
        result = [
            item for item in result
            if item.get("kind") == "turn_response"
            and item.get("from_id") in turn_ids
            and item.get("to_id") in response_ids
        ]
    elif metric_id == "revision.r1":
        result = [item for item in result if item.get("revision") == "r1"]
    elif metric_id == "revision.r2":
        result = [item for item in result if item.get("revision") == "r2"]
    elif metric_id == "revision.final_after_r2":
        result = [item for item in result if item.get("kind") == "final_after"]
    elif metric_id in {"attribution.model_config"}:
        result = [item for item in result if item.get("id") in response_ids]
    elif metric_id in {
        "attribution.usage",
        "attribution.token_semantics",
        "attribution.reconciliation",
    }:
        result = [
            item for item in result
            if item.get("response_id") in response_ids
            or item.get("message_id") in response_ids
        ]
    return result


def package_opencode_run(
    repository_root: Path | str,
    run_root: Path | str,
    output_dir: Path | str,
    *,
    correction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one new local evidence directory and return its validated summary."""

    repository = Path(repository_root).resolve(strict=True)
    run = Path(run_root).resolve(strict=True)
    final_output = Path(output_dir).resolve(strict=False)
    output = final_output.with_name(final_output.name + ".staging")
    runs_base = (repository / "artifacts/survival-v1-runs").resolve(strict=True)
    if runs_base not in run.parents:
        raise OpenCodeEvidenceError("run root must be inside artifacts/survival-v1-runs")
    if final_output.exists() or output.exists():
        raise OpenCodeEvidenceError("evidence output already exists")
    state_path = run / "controller-state.json"
    state = _read_json(state_path)
    attempt_id = state.get("attempt_id")
    repetition = state.get("repetition")
    if (
        not isinstance(attempt_id, str)
        or not attempt_id.startswith("opencode-cli-eval-")
        or repetition not in {1, 2, 3}
        or state.get("model") != MODEL_ID
    ):
        raise OpenCodeEvidenceError("run identity is outside the frozen OpenCode cohort")
    build = state.get("opencode_version", state.get("version"))
    if build != SUPPORTED_BUILD:
        raise OpenCodeEvidenceError("run does not use the supported OpenCode build")
    workload = _load_workload(repository, run, state)
    controller, stdout, ledger, before_sha, after_sha = _observer_inputs(
        run, state, workload
    )
    observer = build_opencode_live_observer(
        workload=workload,
        controller_state=controller,
        stdout_by_turn=stdout,
        helper_ledger_jsonl=ledger,
        before_checkout_sha256=before_sha,
        after_checkout_sha256=after_sha,
    )
    session_ids = {
        event.get("session_id")
        for event in observer.get("events", [])
        if isinstance(event, Mapping)
    }
    if len(session_ids) != 1:
        raise OpenCodeEvidenceError("observer does not bind exactly one session")
    session_id = next(iter(session_ids))
    if not isinstance(session_id, str) or not session_id:
        raise OpenCodeEvidenceError("observer session identity is invalid")
    run_id = observer.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise OpenCodeEvidenceError("observer run identity is invalid")

    output.mkdir(parents=True, exist_ok=False)
    native_dir = output / "native-bundle"
    native_dir.mkdir()
    source = _source_bundle(run, state)
    for name in OPENCODE_BUNDLE_FILES:
        shutil.copyfile(source / name, native_dir / name)

    # Legacy repetition 1 stored the isolated family directly in the run root,
    # which also contains controller files and therefore is not itself a valid
    # decoder boundary.  In that layout the freshly copied three-file family is
    # the source decode; the earlier root files are still denied below.
    decode_source = native_dir if source == run else source
    source_decoded = decode_opencode_bundle(decode_source, session_id=session_id)
    saved_modes: dict[Path, int] = {}
    try:
        for path in _source_native_files(run):
            saved_modes[path] = stat.S_IMODE(path.lstat().st_mode)
            os.chmod(path, 0)
        decoded = decode_opencode_bundle(native_dir, session_id=session_id)
    finally:
        for path, mode in saved_modes.items():
            os.chmod(path, mode)
    canonical_equal = _normalized_decode(source_decoded) == _normalized_decode(decoded)
    if decoded.get("supported") is not True or decoded.get("diagnostics") != []:
        raise OpenCodeEvidenceError("offline decoder is unsupported or produced diagnostics")
    if not canonical_equal:
        raise OpenCodeEvidenceError("isolated and source decodes are not canonical-equal")
    decoded_session = decoded.get("session")
    if not isinstance(decoded_session, Mapping) or decoded_session.get("version") != build:
        raise OpenCodeEvidenceError(
            "decoded native session build does not match the controller build"
        )

    native_session_ids = _session_ids_in_bundle(native_dir)
    complete_root = native_session_ids == {session_id} and all(
        (native_dir / name).is_file() for name in OPENCODE_BUNDLE_FILES
    )
    receipt = {
        "schema_version": "session-bench-portability-receipt-v1",
        "scope": "isolated OpenCode session database family",
        "complete_root": complete_root,
        "companions_present": all(
            (native_dir / name).is_file() for name in OPENCODE_BUNDLE_FILES[1:]
        ),
        "isolated_decode": True,
        "canonical_equality": canonical_equal,
        "source_native_files_denied": len(saved_modes),
        "selected_session_id": session_id,
        "native_session_ids": sorted(native_session_ids),
        "prior_controller_isolation_attestation": (
            state.get("source_denied_during_offline_decode") is True
        ),
    }
    measurement = compare_survival_run(
        observer,
        decoded,
        receipt,
        configuration_id=CONFIGURATION_ID,
        repetition=repetition,
    )
    if correction is not None:
        if correction.get("reason") != "missing_decoder_runtime":
            raise OpenCodeEvidenceError("unsupported correction reason")
        supersedes = correction.get("supersedes")
        if not isinstance(supersedes, Mapping):
            raise OpenCodeEvidenceError("correction has no historical binding")
        if _sha256_bytes(_canonical(decoded) + b"\n") != supersedes.get("decoded_sha256"):
            raise OpenCodeEvidenceError("correction decode differs from historical package")
        if _sha256_bytes(_canonical(measurement) + b"\n") != supersedes.get("measurement_sha256"):
            raise OpenCodeEvidenceError("correction measurement differs from historical package")

    observer_path = output / "observer.json"
    decoded_path = output / "decoded.json"
    receipt_path = output / "portability-receipt.json"
    measurement_path = output / "measurement.json"
    observer_sha = _write_json(observer_path, observer)
    decoded_sha = _write_json(decoded_path, decoded)
    receipt_sha = _write_json(receipt_path, receipt)
    measurement_sha = _write_json(measurement_path, measurement)
    files = [
        {
            "artifact_id": f"{attempt_id}:{name}",
            "name": name,
            "sha256": _sha256_file(native_dir / name),
            "size_bytes": (native_dir / name).stat().st_size,
        }
        for name in OPENCODE_BUNDLE_FILES
    ]
    manifest = {
        "schema_version": "session-bench-native-manifest-v1",
        "run_id": run_id,
        "configuration_id": CONFIGURATION_ID,
        "selected_session_id": session_id,
        "boundary": "one isolated opencode.db with required WAL and SHM companions",
        "files": files,
        "derived": {
            "decoded.json": decoded_sha,
            "portability-receipt.json": receipt_sha,
            "measurement.json": measurement_sha,
        },
        "source_controller_sha256": _sha256_file(state_path),
    }
    manifest_path = output / "native-manifest.json"
    manifest_sha = _write_json(manifest_path, manifest)
    runtime_dir = output / "replay-runtime"
    runtime_manifest = build_runtime(repository, runtime_dir)
    runtime_manifest_sha = _sha256_file(runtime_dir / MANIFEST_FILENAME)
    decoder_path = runtime_dir / "session_bench/adapters/opencode_decoder.py"
    decoder_sha = _sha256_file(decoder_path)

    db_file = next(item for item in files if item["name"] == "opencode.db")
    receipt_locator = {
        "artifact_id": f"{attempt_id}:portability-receipt",
        "artifact_sha256": receipt_sha,
        "record_location": "document:portability-receipt.json",
    }
    manifest_locator = {
        "artifact_id": f"{attempt_id}:native-manifest",
        "artifact_sha256": manifest_sha,
        "record_location": "scope:isolated-opencode-session-database-family",
    }
    companions = [
        {
            "artifact_id": item["artifact_id"],
            "artifact_sha256": item["sha256"],
            "record_location": f"file:{item['name']}",
        }
        for item in files
        if item["name"] != "opencode.db"
    ]
    metric_evidence: list[dict[str, Any]] = []
    for metric_id in METRICS:
        records = _records_for_metric(decoded, observer, metric_id)
        locators = _dedupe_locators(
            [_native_locator(attempt_id, record) for record in records]
        )
        if metric_id == "portable.companions":
            locators = companions + [manifest_locator]
        elif metric_id.startswith("portable."):
            locators = [manifest_locator, receipt_locator]
        if not locators:
            locators = [
                {
                    "artifact_id": str(db_file["artifact_id"]),
                    "artifact_sha256": str(db_file["sha256"]),
                    "record_location": f"scan:session/{session_id}/{metric_id}",
                }
            ]
        metric_evidence.append(
            {
                "metric_id": metric_id,
                "observer_ids": _metric_observer_ids(observer, metric_id),
                "native_locators": _dedupe_locators(locators),
            }
        )

    campaign = _read_json(repository / "plans/survival-v1/campaign.json")
    os_info = campaign.get("os", {})
    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "workload_version": WORKLOAD_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "run_id": run_id,
        "capture_id": f"capture-{attempt_id}",
        "evaluation_id": f"evaluation-{attempt_id}",
        "configuration_id": CONFIGURATION_ID,
        "repetition": repetition,
        "measurement": measurement,
        "observer": {"id": f"observer-{attempt_id}", "sha256": observer_sha},
        "native_manifest": {"id": f"manifest-{attempt_id}", "sha256": manifest_sha},
        "decoder": {"id": "opencode-sqlite-decoder-v1", "sha256": decoder_sha},
        "identity": {
            "provider": "OpenCode",
            "harness": "Session-Bench OpenCode controller v1",
            "surface": "cli",
            "execution_mode": "noninteractive two-turn continuation",
            "os": f"{os_info.get('family')} {os_info.get('version')} ({os_info.get('build')}) {os_info.get('architecture')}",
            "build": build,
            "model": MODEL_ID,
            "configuration": MODEL_ID,
            "protocol_version": PROTOCOL_VERSION,
            "workload_version": WORKLOAD_VERSION,
            "observer_schema_version": str(observer.get("schema_version")),
            "rubric_version": RUBRIC_VERSION,
        },
        "metric_evidence": metric_evidence,
    }
    validated = validate_evidence_input(evidence)
    score = score_evidence_run(validated)
    if correction is not None and score.display() != correction["supersedes"].get("survival_score"):
        raise OpenCodeEvidenceError("correction survival score differs from historical package")
    evidence_path = output / "evidence.json"
    evidence_sha = _write_json(evidence_path, evidence)
    summary = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "configuration_id": CONFIGURATION_ID,
        "repetition": repetition,
        "session_id": session_id,
        "model": MODEL_ID,
        "build": build,
        "event_cost": 0,
        "score": score.display(),
        "evidence_sha256": evidence_sha,
        "observer_sha256": observer_sha,
        "native_manifest_sha256": manifest_sha,
        "decoder_sha256": decoder_sha,
        "replay_runtime": {
            "path": "replay-runtime",
            "manifest_sha256": runtime_manifest_sha,
            "schema_version": runtime_manifest["schema_version"],
        },
        "portable_receipt": receipt,
        "claim_scope": "local survival score; not a public cross-product ranking",
    }
    if correction is not None:
        summary["correction"] = correction
        correction_receipt = {
            "schema_version": "session-bench-opencode-correction-receipt-v1",
            "scope": "local historical package correction; not independent reproduction",
            "reason": correction["reason"],
            "historical_package_digest": correction["supersedes"]["package_digest"],
            "historical_native_manifest_sha256": correction["supersedes"]["native_manifest_sha256"],
            "historical_decoded_sha256": correction["supersedes"]["decoded_sha256"],
            "historical_measurement_sha256": correction["supersedes"]["measurement_sha256"],
            "correction_decoded_sha256": decoded_sha,
            "correction_measurement_sha256": measurement_sha,
            "canonical_decode_equal": True,
            "measurement_equal": True,
            "survival_score_equal": True,
            "runtime_manifest_sha256": runtime_manifest_sha,
            "independent_reproduction": False,
        }
        correction_receipt_sha = _write_json(output / "correction-receipt.json", correction_receipt)
        summary["correction_receipt_sha256"] = correction_receipt_sha
    result_material = {
        "attempt_id": attempt_id,
        "run_id": run_id,
        "configuration_id": CONFIGURATION_ID,
        "repetition": repetition,
        "session_id": session_id,
        "build": build,
        "model": MODEL_ID,
        "evidence_sha256": evidence_sha,
        "observer_sha256": observer_sha,
        "decoded_sha256": decoded_sha,
        "portability_receipt_sha256": receipt_sha,
        "measurement_sha256": measurement_sha,
        "native_manifest_sha256": manifest_sha,
        "decoder_sha256": decoder_sha,
        "replay_runtime_manifest_sha256": runtime_manifest_sha,
    }
    if correction is not None:
        result_material["correction"] = correction
        result_material["correction_receipt_sha256"] = correction_receipt_sha
    result_material_sha = _sha256_bytes(_canonical(result_material))
    package_id = (
        f"correction-package-{attempt_id}"
        if correction is not None
        else f"package-{attempt_id}"
    )
    result_id = f"survival-result-{result_material_sha[:24]}"
    summary["package_id"] = package_id
    summary["result_id"] = result_id
    summary["result_material_sha256"] = result_material_sha
    _write_json(output / "summary.json", summary)

    package_core = {
        "schema_version": "session-bench-evidence-package-v1",
        "package_id": package_id,
        "result_id": result_id,
        "attempt_id": attempt_id,
        "raw_run_id": run_id,
        "configuration_id": CONFIGURATION_ID,
        "repetition": repetition,
        "scope": "local unpublished synthetic OpenCode survival evidence",
        "self_manifest": {
            "path": "package-manifest.json",
            "hash_excluded_reason": "a file cannot contain its own cryptographic digest",
        },
        "result_material_sha256": result_material_sha,
        "files": _package_file_entries(output),
    }
    if correction is not None:
        package_core["correction"] = correction
    package = dict(package_core)
    package["package_digest"] = _sha256_bytes(_canonical(package_core))
    _write_json(output / "package-manifest.json", package)
    output.rename(final_output)
    return summary


def package_opencode_correction(
    repository_root: Path | str,
    run_root: Path | str,
    historical_package_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Create one new replayable package chained to a verified historical one.

    The historical directory is read only to verify and bind it.  Its contents
    are never copied into or modified by the correction package builder.
    """
    run = Path(run_root).resolve(strict=True)
    state = _read_json(run / "controller-state.json")
    attempt_id = state.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise OpenCodeEvidenceError("captured run has no attempt identity")
    correction = _correction_binding(
        historical_package_dir,
        attempt_id=attempt_id,
    )
    return package_opencode_run(
        repository_root,
        run,
        output_dir,
        correction=correction,
    )


__all__ = [
    "CONFIGURATION_ID",
    "EVALUATION_SCHEMA_VERSION",
    "MODEL_ID",
    "OpenCodeEvidenceError",
    "package_opencode_correction",
    "package_opencode_run",
]
