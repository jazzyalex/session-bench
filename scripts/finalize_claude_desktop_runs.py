#!/usr/bin/env python3
"""Finalize one already captured Claude Desktop Code run offline.

The caller supplies the exact transcript and Desktop metadata files selected by
the capture operator.  The command never searches a Claude root, opens a
neighbouring session, launches Claude, or publishes a score.  It writes an
additive private evidence directory below the run's existing ``capture``
directory.

The Desktop recording family used by this adapter is the explicit pair of
``transcript/session.jsonl`` and ``desktop/session.json`` supplied by the
operator.  The pair is joined by the CLI session ID and Desktop session
metadata.  A future surface can add more companions without changing this
finalizer; an undeclared companion is never silently discovered.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for path in (REPO, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.claude_desktop_root import validate_claude_desktop_family  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.claude_live import native_facts_from_claude_session  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.live_observer import build_opencode_live_observer  # noqa: E402
from session_bench.survival_evidence import (  # noqa: E402
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_prospective_evidence_input,
)
from session_bench.v1_public_score import (  # noqa: E402
    FORMAT_METRICS,
    PUBLIC_METRICS,
    SURVIVAL_METRICS,
    validate_format_evidence,
)
from session_bench.workload_instance import instantiate_workload  # noqa: E402

from run_claude_survival import _build_31_evidence  # noqa: E402


RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GUI_CANARY_KEYS = {
    1: "r1_canary_visible",
    2: "r2_canary_visible",
}


class FinalizeError(RuntimeError):
    """The selected Desktop evidence cannot be finalized fail-closed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"expected one ordinary file: {path}")
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> str:
    if path.exists() or path.is_symlink():
        raise FinalizeError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value) + b"\n"
    path.write_bytes(data)
    return _sha256_bytes(data)


def _load_json(path: Path, *, object_required: bool = True) -> Any:
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"selected input is not an ordinary file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError(f"invalid JSON input: {path}") from exc
    if object_required and not isinstance(value, dict):
        raise FinalizeError(f"JSON input must be an object: {path}")
    return value


def _ordinary_source(path: Path, label: str) -> Path:
    path = Path(path).expanduser()
    if path.is_symlink() or not path.is_file():
        raise FinalizeError(f"{label} must be one explicitly selected ordinary file")
    return path.resolve()


def _safe_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise FinalizeError("run_id must contain only letters, digits, underscore, or hyphen")
    return run_id


def _safe_repetition(repetition: int) -> int:
    if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 1:
        raise FinalizeError("repetition must be a positive integer")
    return repetition


def _copy_file(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FinalizeError(f"refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_decode_manifest(package: Path, session: Path) -> str:
    data = session.read_bytes()
    return _write_json(
        package / "decode.json",
        {
            "format": "claude-code-jsonl-v1",
            "artifacts": [
                {
                    "id": "session",
                    "path": "session.jsonl",
                    "sha256": _sha256_bytes(data),
                    "size_bytes": len(data),
                    "depends_on": [],
                }
            ],
        },
    )


def _remove_unique_assistant_canary(source: Path, destination: Path, canary: str) -> dict[str, Any]:
    """Create a selected-loss copy by removing one assistant record only."""

    if not isinstance(canary, str) or not canary:
        raise FinalizeError("selected response canary must be non-empty")
    source_bytes = source.read_bytes()
    lines = source_bytes.splitlines(keepends=True)
    kept: list[bytes] = []
    removed: list[tuple[int, bytes]] = []
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FinalizeError(f"selected transcript line {number} is invalid JSON") from exc
        message = row.get("message") if isinstance(row, dict) else None
        is_assistant = (
            isinstance(row, dict)
            and row.get("type") == "assistant"
            and isinstance(message, dict)
            and message.get("role") == "assistant"
        )
        if is_assistant and canary in json.dumps(message.get("content"), ensure_ascii=False):
            removed.append((number, line))
        else:
            kept.append(line)
    if len(removed) != 1:
        raise FinalizeError(
            f"expected one assistant record containing selected canary, found {len(removed)}"
        )
    if destination.exists() or destination.is_symlink():
        raise FinalizeError(f"refusing to overwrite artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"".join(kept))
    line_number, line = removed[0]
    return {
        "removed_line_number": line_number,
        "removed_record_sha256": _sha256_bytes(line),
        "removed_response_canary": canary,
        "remaining_session_sha256": _sha256_bytes(destination.read_bytes()),
    }


def _validate_gui_receipt(receipt: Mapping[str, Any], *, run_id: str, workload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise FinalizeError("GUI receipt must be an object")
    observations = receipt.get("observations")
    if not isinstance(observations, Mapping):
        raise FinalizeError("GUI receipt must contain observations")
    turns = workload.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise FinalizeError("workload must contain exactly two turns")
    for ordinal, turn in enumerate(turns, 1):
        if not isinstance(turn, Mapping):
            raise FinalizeError("workload turn is malformed")
        key = GUI_CANARY_KEYS[ordinal]
        expected = turn.get("response_canary")
        if observations.get(key) != expected:
            raise FinalizeError(f"GUI receipt does not prove {key}")
        if observations.get(f"r{ordinal}_response_boundary_visible") is not True:
            raise FinalizeError(f"GUI receipt does not prove response boundary {ordinal}")
    if observations.get("edit_visible") is not True:
        raise FinalizeError("GUI receipt does not prove the edit")
    if observations.get("final_table_rows") != 3:
        raise FinalizeError("GUI receipt does not prove the three-row final result")
    if receipt.get("run_id") not in (None, run_id):
        raise FinalizeError("GUI receipt run_id does not match selected run")
    return dict(receipt)


def _validate_desktop_pair(family_package: Path, *, decoded_session_id: str) -> dict[str, Any]:
    """Validate only the copied exact family; optional runtime files stay optional."""

    try:
        family = validate_claude_desktop_family(family_package)
    except ValueError as exc:
        raise FinalizeError(f"Desktop family validation failed: {exc}") from exc
    if family.get("cli_session_id") != decoded_session_id:
        raise FinalizeError("Desktop metadata cliSessionId does not match decoder session")
    family["complete_cross_root_family"] = bool(family.get("complete_persistent_family"))
    family["family_scope"] = "the two exact operator-selected persistent Desktop recording artifacts"
    family["metadata_only_discovery"] = True
    family["personal_history_content_scanned"] = False
    family["unrelated_preexisting_sessions_opened"] = False
    return family


def _helper_ledger(project_root: Path, *, run_canary: str) -> tuple[str, dict[str, Mapping[str, Any]]]:
    path = project_root / ".survival-observer.jsonl"
    if path.is_symlink() or not path.is_file():
        raise FinalizeError("preserved synthetic helper ledger is missing")
    raw = path.read_text(encoding="utf-8")
    rows: dict[str, Mapping[str, Any]] = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            raise FinalizeError(f"helper ledger line {number} is blank")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FinalizeError(f"helper ledger line {number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise FinalizeError(f"helper ledger line {number} is not an object")
        phase = value.get("phase")
        if phase in rows or phase not in {"inspect", "baseline", "final"}:
            raise FinalizeError("helper ledger phases are duplicated or unsupported")
        if value.get("run_canary") != run_canary:
            raise FinalizeError("helper ledger run canary does not match selected run")
        if value.get("argv") != ["python3", "bench_check.py", phase]:
            raise FinalizeError(f"helper ledger {phase} argv is not the frozen helper invocation")
        if value.get("cwd") != "fixture_project":
            raise FinalizeError(f"helper ledger {phase} cwd is not fixture_project")
        if not isinstance(value.get("output"), str) or not value["output"].startswith(f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_"):
            raise FinalizeError(f"helper ledger {phase} output is malformed")
        rows[phase] = value
    if set(rows) != {"inspect", "baseline", "final"}:
        raise FinalizeError("helper ledger must contain inspect, baseline, and final exactly once")
    return raw, rows


def _build_observer(
    *,
    workload: Mapping[str, Any],
    session_id: str,
    model: str,
    helper_raw: str,
    helper: Mapping[str, Mapping[str, Any]],
    gui: Mapping[str, Any],
    before_sha: str,
    after_sha: str,
    run_id: str,
    repetition: int,
) -> dict[str, Any]:
    """Build expected events from independent workload, helper, GUI, and FS evidence."""

    observations = gui["observations"]

    def tool(tool: str, call_id: str, input_value: Any, output: str, exit_code: int) -> dict[str, Any]:
        return {
            "type": "tool_use",
            "tool": tool,
            "input": input_value,
            "callID": call_id,
            "id": call_id,
            "sessionID": session_id,
            "state": {"status": "completed", "output": output, "metadata": {"exit_code": exit_code}},
        }

    def text_row(value: str) -> dict[str, Any]:
        return {"type": "text", "text": value, "sessionID": session_id}

    run_canary = str(workload["run_canary"])
    streams = {
        1: "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                tool("Bash", "gui-inspect", {"command": f"python3 bench_check.py inspect --run-canary {run_canary}"}, str(helper["inspect"]["output"]), 0),
                tool("Bash", "gui-baseline", {"command": f"python3 bench_check.py baseline --run-canary {run_canary}"}, str(helper["baseline"]["output"]), 1),
                text_row(str(observations["r1_canary_visible"])),
            )
        )
        + "\n",
        2: "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                tool("Edit", "gui-edit", {"file_path": "fixture_project/checkout.py"}, "compound edit completed", 0),
                tool("Bash", "gui-final", {"command": f"python3 bench_check.py final --run-canary {run_canary}"}, str(helper["final"]["output"]), 0),
                text_row(str(observations["r2_canary_visible"])),
            )
        )
        + "\n",
    }
    observer = build_opencode_live_observer(
        workload=workload,
        controller_state={
            "turns": {"1": {"session_id": session_id}, "2": {"session_id": session_id}},
            "model": model,
            "configuration": "claude-desktop",
            "workspace": "fixture_project",
        },
        stdout_by_turn=streams,
        helper_ledger_jsonl=helper_raw,
        before_checkout_sha256=before_sha,
        after_checkout_sha256=after_sha,
    )
    # Tool IDs are native-looking fields in the synthetic stream projection;
    # they were not observed by the GUI receipt.  Keep semantic command/target
    # fields and make the comparator bind on those fields only.
    for event in observer["events"]:
        if event.get("kind") in {"action", "result"} and isinstance(event.get("fields"), dict):
            for key in ("call_id", "native_action_id", "native_result_id"):
                event["fields"].pop(key, None)
    observer.update(
        {
            "configuration_id": "claude-desktop",
            "repetition": repetition,
            "method": "independent Desktop accessibility receipt, submitted workload, preserved helper ledger, and filesystem hashes; native transcript was not used to author expected events",
            "observer_receipt": "gui-receipt.json",
            "run_id": run_id,
        }
    )
    return observer


def _metric_evidence(metric_ids: tuple[str, ...], observer_id: str, native_id: str, native_sha: str) -> list[dict[str, Any]]:
    return [
        {
            "metric_id": metric_id,
            "observer_ids": [observer_id],
            "native_locators": [
                {
                    "artifact_id": native_id,
                    "artifact_sha256": native_sha,
                    "record_location": "transcript/session.jsonl",
                }
            ],
        }
        for metric_id in metric_ids
    ]


def _artifact_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FinalizeError(f"finalized evidence contains a symlink: {path}")
        if path.is_file() and path.name != "artifact-hashes.json":
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return rows


def finalize(
    *,
    run_id: str,
    repetition: int,
    transcript: Path,
    desktop_metadata: Path,
    gui_receipt: Path,
    gui_screenshot: Path | None = None,
    project_root: Path | None = None,
    build: str | None = None,
    collected_on: str | None = None,
) -> dict[str, Any]:
    run_id = _safe_run_id(run_id)
    repetition = _safe_repetition(repetition)
    transcript = _ordinary_source(transcript, "transcript")
    desktop_metadata = _ordinary_source(desktop_metadata, "Desktop metadata")
    gui_receipt = _ordinary_source(gui_receipt, "GUI receipt")
    if gui_screenshot is not None:
        gui_screenshot = _ordinary_source(gui_screenshot, "GUI screenshot")
    if collected_on is None:
        collected_on = date.today().isoformat()
    try:
        date.fromisoformat(collected_on)
    except (TypeError, ValueError) as exc:
        raise FinalizeError("collected_on must be YYYY-MM-DD") from exc

    run_root = (REPO / "artifacts" / "survival-v1-runs" / run_id).resolve()
    if not run_root.is_dir():
        raise FinalizeError(f"run root is missing: {run_root}")
    artifact_base = (REPO / "artifacts" / "survival-v1-runs").resolve()
    if artifact_base not in run_root.parents:
        raise FinalizeError("run root escaped the repository survival run directory")
    output = run_root / "capture" / "finalized-private-v1"
    if output.exists() or output.is_symlink():
        raise FinalizeError(f"refusing to overwrite existing finalizer output: {output}")

    attempt_path = run_root / "attempt.json"
    if attempt_path.is_file() and not attempt_path.is_symlink():
        attempt = _load_json(attempt_path)
        if isinstance(attempt.get("run_canary"), str) and attempt["run_canary"] != f"SB_SURVIVAL_V1_RUN_{run_id}":
            raise FinalizeError("existing attempt is bound to a different run canary")

    workload_template = _load_json(REPO / "fixtures/scenarios/survival-v1/workload/workload.json")
    workload, _ = instantiate_workload(workload_template, run_id)
    run_canary = workload["run_canary"]

    selected_project = (project_root or (run_root / "final-project-private")).expanduser()
    if selected_project.is_symlink() or not selected_project.is_dir():
        raise FinalizeError(f"preserved synthetic project root is missing: {selected_project}")
    selected_project = selected_project.resolve()
    before_path = selected_project / "snapshots" / "checkout.before.py"
    checkout_path = selected_project / "checkout.py"
    before_sha = _sha256(before_path)
    after_sha = _sha256(checkout_path)
    if before_sha == after_sha:
        raise FinalizeError("preserved synthetic project did not change checkout.py")

    gui = _validate_gui_receipt(_load_json(gui_receipt), run_id=run_id, workload=workload)
    screenshot_sha = _sha256(gui_screenshot) if gui_screenshot is not None else None
    gui_declared = gui.get("screenshot")
    if screenshot_sha is not None and isinstance(gui_declared, Mapping):
        declared_sha = gui_declared.get("sha256")
        if isinstance(declared_sha, str) and declared_sha != screenshot_sha:
            raise FinalizeError("GUI receipt screenshot digest does not match supplied screenshot")
    helper_raw, helper = _helper_ledger(selected_project, run_canary=run_canary)

    # Package the exact transcript and metadata pair.  The family package is
    # private; the decoder package below contains only the declared transcript.
    family_package = output / "native-family-private"
    _copy_file(transcript, family_package / "transcript" / "session.jsonl")
    _copy_file(desktop_metadata, family_package / "desktop" / "session.json")
    decoder_package = output / "native-package"
    _copy_file(transcript, decoder_package / "session.jsonl")
    decode_manifest_sha = _write_decode_manifest(decoder_package, decoder_package / "session.jsonl")
    intact = decode_claude_code_bundle(decoder_package)
    session_id = intact["session_id"]
    family = _validate_desktop_pair(family_package, decoded_session_id=session_id)
    family["run_id"] = run_id
    family["repetition"] = repetition
    family_sha = _write_json(output / "family-validation.json", family)

    offline_package = output / "offline-package"
    _copy_file(decoder_package / "session.jsonl", offline_package / "session.jsonl")
    offline_decode_manifest_sha = _write_decode_manifest(offline_package, offline_package / "session.jsonl")
    offline = decode_claude_code_bundle(offline_package)
    if intact != offline:
        raise FinalizeError("native and copied offline Claude decodes differ")
    if intact["counts"]["submitted_turns"] != 2 or intact["counts"]["responses"] != 2:
        raise FinalizeError(f"selected transcript does not contain two turns and two responses: {intact['counts']}")

    damage_package = output / "damage-r2-package"
    selection = _remove_unique_assistant_canary(
        offline_package / "session.jsonl",
        damage_package / "session.jsonl",
        workload["turns"][1]["response_canary"],
    )
    damage_manifest_sha = _write_decode_manifest(damage_package, damage_package / "session.jsonl")
    damaged = decode_claude_code_bundle(damage_package)
    if damaged["counts"]["responses"] != intact["counts"]["responses"] - 1:
        raise FinalizeError("selected-loss control did not remove exactly one response")
    if damaged["counts"]["actions"] != intact["counts"]["actions"] or damaged["counts"]["results"] != intact["counts"]["results"]:
        raise FinalizeError("selected response loss changed the action or result population")

    intact_decode_sha = _write_json(output / "decoded-native.json", intact)
    offline_decode_sha = _write_json(output / "decoded-offline.json", offline)
    damage_decode_sha = _write_json(output / "decoded-damage-r2.json", damaged)
    _write_json(output / "loss-control-selection.json", selection)

    gui_output = dict(gui)
    gui_output["bound_screenshot"] = {
        "supplied": screenshot_sha is not None,
        "sha256": screenshot_sha,
    }
    gui_output_sha = _write_json(output / "gui-receipt.json", gui_output)
    observer = _build_observer(
        workload=workload,
        session_id=session_id,
        model=str(family["model"]),
        helper_raw=helper_raw,
        helper=helper,
        gui=gui,
        before_sha=before_sha,
        after_sha=after_sha,
        run_id=run_id,
        repetition=repetition,
    )
    observer_sha = _write_json(output / "observer.json", observer)

    native_id = f"claude-desktop-transcript-{session_id}"
    native_sha = _sha256(decoder_package / "session.jsonl")
    native_manifest = {
        "schema_version": "session-bench-claude-desktop-native-manifest-v2",
        "id": f"{run_id}-native-manifest-v1",
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "desktop_session_id": family["desktop_session_id"],
        "cli_session_id": family["cli_session_id"],
        "bridge_session_ids": family["bridge_session_ids"],
        "family_validation": {"id": "family-validation.json", "sha256": family_sha},
        "selected_artifact": {"id": native_id, "path": "native-package/session.jsonl", "sha256": native_sha, "size_bytes": (decoder_package / "session.jsonl").stat().st_size},
        "packages": {
            "native": {"path": "native-package", "decode_manifest_sha256": decode_manifest_sha},
            "offline": {"path": "offline-package", "decode_manifest_sha256": offline_decode_manifest_sha},
            "damage_r2": {"path": "damage-r2-package", "decode_manifest_sha256": damage_manifest_sha},
        },
        "complete_cross_root_family": family["complete_cross_root_family"],
        "stable_root_repetition_qualified": False,
        "unrelated_preexisting_sessions_opened": False,
        "privacy": "private native family; no public derivative is created by this command",
    }
    native_manifest_sha = _write_json(output / "native-manifest.json", native_manifest)

    native_facts = native_facts_from_claude_session(
        (decoder_package / "session.jsonl").read_bytes(),
        run_canary=run_canary,
        workspace=selected_project,
        before_sha256=before_sha,
        after_sha256=after_sha,
    )
    native_facts_sha = _write_json(output / "native-facts.json", native_facts)
    replay_receipt = {
        "schema_version": "session-bench-claude-desktop-replay-receipt-v2",
        "run_id": run_id,
        "repetition": repetition,
        "decoder": {
            "path": "session_bench/adapters/claude_code_decoder.py",
            "sha256": _sha256(REPO / "session_bench/adapters/claude_code_decoder.py"),
        },
        "packages": {"native": "native-package", "offline": "offline-package", "damage_r2": "damage-r2-package"},
        "decoded_counts": {"native": intact["counts"], "offline": offline["counts"], "damage_r2": damaged["counts"]},
        "canonical_equality": {"native_vs_offline": intact == offline, "native_decode_sha256": intact_decode_sha, "offline_decode_sha256": offline_decode_sha},
        "selected_loss": {
            "intact_responses": intact["counts"]["responses"],
            "damaged_responses": damaged["counts"]["responses"],
            "response_loss_detected": damaged["counts"]["responses"] < intact["counts"]["responses"],
            "actions_preserved": damaged["counts"]["actions"] == intact["counts"]["actions"],
            "results_preserved": damaged["counts"]["results"] == intact["counts"]["results"],
            "selection": "loss-control-selection.json",
        },
        "family_validation_sha256": family_sha,
        "gui_receipt_sha256": gui_output_sha,
        "independent_reproduction": False,
        "stable_root_repetition_qualified": False,
    }
    replay_sha = _write_json(output / "replay-receipt.json", replay_receipt)

    measurement = compare_survival_run(
        observer,
        native_facts,
        {
            "complete_root": True if family["complete_cross_root_family"] else None,
            "companions_present": True if family["complete_cross_root_family"] else None,
            "isolated_decode": True,
            "canonical_equality": intact == offline,
        },
        configuration_id="claude-desktop",
        repetition=repetition,
    )
    measurement_sha = _write_json(output / "measurement.json", measurement)

    format_evidence = build_claude_format_evidence(
        offline,
        observer={"id": "observer.json", "sha256": observer_sha},
        native_manifest={"id": "native-manifest.json", "sha256": native_manifest_sha},
        run_id=run_id,
        configuration_id="claude-desktop",
        repetition=repetition,
        build=build or str(family["cli_version"]),
        collected_on=collected_on,
        result_id=f"{run_id}-desktop-format-v1",
        complete_record_family=bool(family["complete_cross_root_family"]),
        root_repetitions=None,
    )
    validate_format_evidence(format_evidence)
    format_sha = _write_json(output / "format-evidence.json", format_evidence)

    survival_evidence = {
        "schema_version": PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": run_id,
        "capture_id": f"{run_id}-desktop-capture-v1",
        "evaluation_id": f"{run_id}-desktop-evaluation-v1",
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "measurement": measurement,
        "observer": {"id": "observer.json", "sha256": observer_sha},
        "native_manifest": {"id": "native-manifest.json", "sha256": native_manifest_sha},
        "decoder": {"id": "claude-code-decoder.py", "sha256": _sha256(REPO / "session_bench/adapters/claude_code_decoder.py")},
        "identity": {
            "provider": "Anthropic",
            "harness": "Claude Code",
            "surface": "Desktop Code (Local)",
            "execution_mode": "Claude Desktop local task",
            "os": "macOS",
            "build": build or str(family["cli_version"]),
            "model": str(family["model"]),
            "configuration": "claude-desktop",
            "protocol_version": "1.0-survival",
            "workload_version": "1.0-survival-workload",
            "observer_schema_version": "1.0-survival-observer",
            "rubric_version": "1.0-survival-rubric",
        },
        "metric_evidence": _metric_evidence(SURVIVAL_METRICS, "observer.json", native_id, native_sha),
    }
    validate_prospective_evidence_input(survival_evidence)
    survival_sha = _write_json(output / "survival-evidence.json", survival_evidence)
    evidence_31 = _build_31_evidence(
        measurement,
        format_evidence,
        survival_evidence,
        observer_id="observer.json",
        native_artifact_id=native_id,
        native_artifact_sha256=native_sha,
    )
    evidence_31["claim_limit"] = "one Claude Desktop Code (Local) private evidence run; no public score, rank, badge, recommendation, or vendor claim"
    evidence_31_sha = _write_json(output / "evidence-31.json", evidence_31)
    if len(evidence_31.get("metrics", [])) != 31 or len(evidence_31.get("metric_evidence", [])) != 31:
        raise FinalizeError("31-metric wrapper is incomplete")

    states = {row["id"]: row["state"] for row in measurement["metrics"]}
    broad_states = {row["id"]: row["state"] for row in validate_format_evidence(format_evidence)["profile"]["metrics"]}
    attempt_output = {
        "schema_version": "session-bench-claude-desktop-finalized-attempt-v1",
        "attempt_id": run_id,
        "configuration_id": "claude-desktop",
        "repetition": repetition,
        "state": "captured_unscored",
        "score_eligible": False,
        "public_score_eligible": False,
        "source_attempt": "../attempt.json",
        "session_id": session_id,
        "desktop_session_id": family["desktop_session_id"],
        "bridge_session_ids": family["bridge_session_ids"],
        "counts": intact["counts"],
        "selected_loss": replay_receipt["selected_loss"],
        "canonical_copy_proven": True,
        "complete_cross_root_family": family["complete_cross_root_family"],
        "independent_gui_observer": True,
        "decoder": {"format": intact["format"], "canonical_equal": True, "source_sha256": replay_receipt["decoder"]["sha256"]},
        "measurement": {"path": "measurement.json", "sha256": measurement_sha, "metric_count": len(measurement["metrics"]), "states": states},
        "format_evidence": {"path": "format-evidence.json", "sha256": format_sha, "metric_count": len(FORMAT_METRICS), "states": broad_states},
        "survival_evidence": {"path": "survival-evidence.json", "sha256": survival_sha},
        "evidence_31": {"path": "evidence-31.json", "sha256": evidence_31_sha, "metric_count": len(evidence_31["metrics"])},
        "replay_receipt": {"path": "replay-receipt.json", "sha256": replay_sha},
        "stable_root_repetition": "unresolved: this run does not prove all three stable-root repetitions",
        "claim_limit": "private evidence only; no public score, rank, badge, recommendation, or vendor claim",
    }
    attempt_sha = _write_json(output / "attempt-finalized.json", attempt_output)
    hashes = {
        "schema_version": "session-bench-claude-desktop-finalized-artifact-hashes-v1",
        "run_id": run_id,
        "repetition": repetition,
        "artifacts": _artifact_rows(output),
    }
    hashes_sha = _write_json(output / "artifact-hashes.json", hashes)

    result = {
        "status": "complete_private_unscored",
        "run_id": run_id,
        "repetition": repetition,
        "output": "capture/finalized-private-v1",
        "session_id": session_id,
        "desktop_session_id": family["desktop_session_id"],
        "decoded_counts": {"native": intact["counts"], "offline": offline["counts"], "damage_r2": damaged["counts"]},
        "canonical_equal": True,
        "selected_loss": replay_receipt["selected_loss"],
        "metric_count": 31,
        "measurement_states": states,
        "format_states": broad_states,
        "score_eligible": False,
        "attempt_sha256": attempt_sha,
        "artifact_hashes_sha256": hashes_sha,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repetition", required=True, type=int)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--desktop-metadata", required=True, type=Path)
    parser.add_argument("--gui-receipt", required=True, type=Path)
    parser.add_argument("--gui-screenshot", type=Path)
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--build")
    parser.add_argument("--collected-on")
    args = parser.parse_args()
    try:
        finalize(
            run_id=args.run_id,
            repetition=args.repetition,
            transcript=args.transcript,
            desktop_metadata=args.desktop_metadata,
            gui_receipt=args.gui_receipt,
            gui_screenshot=args.gui_screenshot,
            project_root=args.project_root,
            build=args.build,
            collected_on=args.collected_on,
        )
    except Exception as exc:
        print(f"finalize invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
