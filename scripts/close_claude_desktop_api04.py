#!/usr/bin/env python3
"""Close Claude Desktop api04 with the current decoder, without a new run.

The original api04 artifacts are historical calibration evidence.  This
command writes only versioned derivatives, so correcting the decoder's
compound-edit expansion cannot rewrite the original record.  The observer
side is deliberately limited to the already archived GUI receipt, helper
ledger, submitted workload, and filesystem hashes; it does not use the
native transcript to author the expected event populations.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.claude_desktop_root import validate_claude_desktop_family  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.claude_live import native_facts_from_claude_session  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.live_observer import build_opencode_live_observer  # noqa: E402
from session_bench.survival_evidence import PROSPECTIVE_EVIDENCE_SCHEMA_VERSION, validate_prospective_evidence_input  # noqa: E402
from session_bench.v1_public_score import FORMAT_METRICS, PUBLIC_METRICS, SURVIVAL_METRICS, validate_format_evidence  # noqa: E402
from session_bench.workload_instance import instantiate_workload  # noqa: E402

from run_claude_survival import _build_31_evidence  # noqa: E402


ATTEMPT_ID = "claude-desktop-api-04"
RUN_CANARY = "SB_SURVIVAL_V1_RUN_claude-desktop-api-04"
SESSION_ID = "13cd899f-6ec2-46dc-9311-e400b0e70095"
BUILD = "2.1.270"
COLLECTED_ON = "2026-09-15"
CAPTURE = ROOT / "artifacts" / "survival-v1-runs" / ATTEMPT_ID / "capture"
SOURCE_NATIVE = CAPTURE / "native-private"
SOURCE_DAMAGE = CAPTURE / "damage-r2-private"
SOURCE_FAMILY = CAPTURE / "full-family-private"
SOURCE_OBSERVER = CAPTURE / "observer-private"
VERSION = "v2"


class CloseError(RuntimeError):
    """The already captured calibration cannot be closed fail-closed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _write_json(path: Path, value: Any) -> str:
    if path.exists() or path.is_symlink():
        raise CloseError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value) + b"\n"
    path.write_bytes(data)
    return _sha256_bytes(data)


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloseError(f"invalid JSON artifact: {path}") from exc


def _copy_closed_package(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise CloseError(f"source package is not an ordinary directory: {source}")
    if destination.exists() or destination.is_symlink():
        raise CloseError(f"refusing to overwrite package: {destination}")
    shutil.copytree(source, destination, symlinks=False)
    for candidate in destination.rglob("*"):
        if candidate.is_symlink():
            raise CloseError(f"versioned package contains a symlink: {candidate}")


def _workload() -> dict[str, Any]:
    template = _load(ROOT / "fixtures/scenarios/survival-v1/workload/workload.json")
    workload, _ = instantiate_workload(template, ATTEMPT_ID)
    if workload["run_canary"] != RUN_CANARY:
        raise CloseError("workload run canary does not bind api04")
    return workload


def _helper_rows() -> tuple[str, dict[str, dict[str, Any]]]:
    path = SOURCE_OBSERVER / "workspace-helper-ledger.jsonl"
    raw = path.read_text(encoding="utf-8")
    rows: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("phase") in rows:
            raise CloseError("helper ledger is malformed or duplicated")
        rows[str(value["phase"])] = value
    if set(rows) != {"inspect", "baseline", "final"}:
        raise CloseError("helper ledger does not contain exactly inspect, baseline, final")
    return raw, rows


def _gui_receipt() -> dict[str, Any]:
    value = _load(SOURCE_OBSERVER / "gui-observer.json")
    if not isinstance(value, dict):
        raise CloseError("GUI receipt is not an object")
    observations = value.get("observations")
    if not isinstance(observations, dict):
        raise CloseError("GUI receipt has no observations")
    expected = {
        "r1_canary_visible": "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂",
        "r2_canary_visible": "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ",
    }
    for key, canary in expected.items():
        if observations.get(key) != canary:
            raise CloseError(f"GUI receipt does not prove {key}")
    if observations.get("r1_response_boundary_visible") is not True or observations.get("r2_response_boundary_visible") is not True:
        raise CloseError("GUI receipt does not prove both response boundaries")
    if observations.get("edit_visible") is not True or observations.get("final_table_rows") != 3:
        raise CloseError("GUI receipt does not prove the edit and final table")
    return value


def _tool_row(*, tool: str, call_id: str, input_value: Any, session_id: str, output: str, exit_code: int) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "tool": tool,
        "input": input_value,
        "callID": call_id,
        "id": call_id,
        "sessionID": session_id,
        "state": {"status": "completed", "output": output, "metadata": {"exit_code": exit_code}},
    }


def _text_row(text: str, session_id: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "sessionID": session_id}


def _observer(workload: Mapping[str, Any], helper: Mapping[str, Mapping[str, Any]], gui: Mapping[str, Any], before_sha: str, after_sha: str) -> dict[str, Any]:
    """Build the independent observer from GUI/ledger evidence only.

    The command inputs are the frozen helper invocations and the edit target
    visible in the GUI receipt.  Native tool IDs and native response prose are
    intentionally absent from this input; the comparator must bind by semantic
    turn/action/result fields instead of being handed native identity.
    """

    def command(phase: str) -> dict[str, Any]:
        row = helper[phase]
        output = str(row["output"])
        return _tool_row(
            tool="Bash",
            call_id=f"gui-{phase}",
            input_value={"command": f"python3 bench_check.py {phase} --run-canary {RUN_CANARY}"},
            session_id=SESSION_ID,
            output=output,
            exit_code=int(row["exit_code"]),
        )

    edit = _tool_row(
        tool="Edit",
        call_id="gui-edit",
        input_value={"file_path": "fixture_project/checkout.py"},
        session_id=SESSION_ID,
        output="compound edit completed",
        exit_code=0,
    )
    r1_canary = str(gui["observations"]["r1_canary_visible"])
    r2_canary = str(gui["observations"]["r2_canary_visible"])
    streams = {
        1: "\n".join(json.dumps(row, ensure_ascii=False) for row in [command("inspect"), command("baseline"), _text_row(r1_canary, SESSION_ID)]) + "\n",
        2: "\n".join(json.dumps(row, ensure_ascii=False) for row in [edit, command("final"), _text_row(r2_canary, SESSION_ID)]) + "\n",
    }
    observer = build_opencode_live_observer(
        workload=workload,
        controller_state={
            "turns": {"1": {"session_id": SESSION_ID}, "2": {"session_id": SESSION_ID}},
            "model": "claude-opus-5",
            "configuration": "claude-desktop",
            "workspace": "fixture_project",
        },
        stdout_by_turn=streams,
        helper_ledger_jsonl="\n".join(json.dumps(value, ensure_ascii=False) for value in helper.values()) + "\n",
        before_checkout_sha256=before_sha,
        after_checkout_sha256=after_sha,
    )
    # The shared builder includes convenient call IDs from its input rows.
    # They are not GUI-observed identity, so remove them from expected fields
    # before comparison and retain semantic turn/argv/target binding only.
    for event in observer["events"]:
        if event.get("kind") in {"action", "result"} and isinstance(event.get("fields"), dict):
            event["fields"].pop("call_id", None)
            event["fields"].pop("native_action_id", None)
            event["fields"].pop("native_result_id", None)
    observer["method"] = (
        "independent GUI observer from archived accessibility receipt, submitted workload, "
        "helper ledger, and filesystem hashes; no native transcript used to author expected events"
    )
    observer["observer_receipt"] = "observer-private/gui-observer.json"
    return observer


def _metric_evidence(metric_ids: tuple[str, ...], observer_id: str, native_id: str, native_sha: str) -> list[dict[str, Any]]:
    return [
        {
            "metric_id": metric_id,
            "observer_ids": [observer_id],
            "native_locators": [{"artifact_id": native_id, "artifact_sha256": native_sha, "record_location": "transcript/session.jsonl"}],
        }
        for metric_id in metric_ids
    ]


def _artifact_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "artifact-hashes-v2.json":
            rows.append({"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
    return rows


def close() -> dict[str, Any]:
    if not CAPTURE.is_dir():
        raise CloseError(f"capture root missing: {CAPTURE}")
    if not SOURCE_NATIVE.is_dir() or not SOURCE_DAMAGE.is_dir() or not SOURCE_FAMILY.is_dir():
        raise CloseError("api04 source packages are incomplete")

    workload = _workload()
    helper_raw, helper = _helper_rows()
    gui = _gui_receipt()
    final_project = ROOT / "artifacts/survival-v1-runs/claude-desktop-api-04/final-project-private"
    before_sha = _sha256(final_project / "snapshots/checkout.before.py")
    after_sha = _sha256(final_project / "checkout.py")
    if before_sha != "f4482bd25089ad5c521d2d8c75d17607d5dec0582b9bb687b5716c9673546401":
        raise CloseError("before checkout hash does not match the helper and observer receipts")
    if after_sha != "a020043db82bec2df47204c03e11ab40c5275139e0b122723810304d81e7050f":
        raise CloseError("after checkout hash does not match the helper and observer receipts")

    family = validate_claude_desktop_family(SOURCE_FAMILY)
    if family["cli_session_id"] != SESSION_ID:
        raise CloseError("family validator selected the wrong CLI session")
    family_validation_path = CAPTURE / "full-family-validation-v2-private.json"
    family_validation = {
        **family,
        "run_id": ATTEMPT_ID,
        "source": "full-family-private",
        "metadata_only_discovery": True,
        "unrelated_preexisting_sessions_opened": False,
        "stable_root_repetition_qualified": False,
    }
    family_sha = _write_json(family_validation_path, family_validation)

    native_pkg = CAPTURE / "native-private-v2"
    offline_pkg = CAPTURE / "offline-private-v2"
    damage_pkg = CAPTURE / "damage-r2-private-v2"
    _copy_closed_package(SOURCE_NATIVE, native_pkg)
    _copy_closed_package(SOURCE_NATIVE, offline_pkg)
    _copy_closed_package(SOURCE_DAMAGE, damage_pkg)

    native_decoded = decode_claude_code_bundle(native_pkg)
    offline_decoded = decode_claude_code_bundle(offline_pkg)
    damage_decoded = decode_claude_code_bundle(damage_pkg)
    if native_decoded != offline_decoded:
        raise CloseError("native and copied offline Claude decodes differ")
    if native_decoded["session_id"] != SESSION_ID:
        raise CloseError("decoder selected the wrong CLI session")
    if native_decoded["counts"] != {"submitted_turns": 2, "responses": 2, "actions": 4, "results": 4}:
        raise CloseError(f"current decoder counts are not 2/2/4/4: {native_decoded['counts']}")
    if damage_decoded["counts"]["responses"] != 1:
        raise CloseError("selected damage control did not remove exactly one response")
    if damage_decoded["counts"]["actions"] != 4 or damage_decoded["counts"]["results"] != 4:
        raise CloseError("selected response loss unexpectedly changed action/result populations")

    native_decode_sha = _write_json(CAPTURE / "decoded-native-private-v2.json", native_decoded)
    offline_decode_sha = _write_json(CAPTURE / "decoded-offline-private-v2.json", offline_decoded)
    damage_decode_sha = _write_json(CAPTURE / "decoded-damage-r2-private-v2.json", damage_decoded)

    native_session = native_pkg / "session.jsonl"
    native_session_sha = _sha256(native_session)
    native_id = f"claude-desktop-transcript-{SESSION_ID}"
    native_manifest = {
        "schema_version": "session-bench-claude-desktop-native-manifest-v2",
        "id": "claude-desktop-full-family-api-04-v2",
        "configuration_id": "claude-desktop",
        "desktop_session_id": family["desktop_session_id"],
        "cli_session_id": family["cli_session_id"],
        "bridge_session_id": family["bridge_session_id"],
        "family_validation": {"id": "full-family-validation-v2-private.json", "sha256": family_sha},
        "selected_artifact": {"id": native_id, "path": "transcript/session.jsonl", "sha256": native_session_sha, "size_bytes": native_session.stat().st_size},
        "native_package": {"path": "native-private-v2", "manifest_sha256": _sha256(native_pkg / "decode.json")},
        "offline_package": {"path": "offline-private-v2", "manifest_sha256": _sha256(offline_pkg / "decode.json")},
        "complete_cross_root_family": True,
        "stable_root_repetition_qualified": False,
        "unrelated_preexisting_sessions_opened": False,
        "privacy": "raw family private; public derivatives must omit account, runtime-secret, and absolute-root content",
    }
    native_manifest_sha = _write_json(CAPTURE / "native-manifest-v2-private.json", native_manifest)

    observer = _observer(workload, helper, gui, before_sha, after_sha)
    observer_sha = _write_json(CAPTURE / "observer-v2-private.json", observer)
    native_facts = native_facts_from_claude_session(native_session.read_bytes(), run_canary=RUN_CANARY, workspace=final_project, before_sha256=before_sha, after_sha256=after_sha)
    native_facts_sha = _write_json(CAPTURE / "native-facts-v2-private.json", native_facts)
    measurement = compare_survival_run(
        observer,
        native_facts,
        {"complete_root": None, "companions_present": True, "isolated_decode": True, "canonical_equality": True},
        configuration_id="claude-desktop",
        repetition=1,
    )
    measurement_sha = _write_json(CAPTURE / "measurement-v2-private.json", measurement)

    format_evidence = build_claude_format_evidence(
        offline_decoded,
        observer={"id": "observer-v2-private.json", "sha256": observer_sha},
        native_manifest={"id": "native-manifest-v2-private.json", "sha256": native_manifest_sha},
        run_id=ATTEMPT_ID,
        configuration_id="claude-desktop",
        repetition=1,
        build=BUILD,
        collected_on=COLLECTED_ON,
        result_id=f"{ATTEMPT_ID}-format-v2",
        complete_record_family=True,
        root_repetitions=None,
    )
    format_sha = _write_json(CAPTURE / "format-evidence-v2-private.json", format_evidence)
    validate_format_evidence(format_evidence)

    survival_evidence = {
        "schema_version": PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": ATTEMPT_ID,
        "capture_id": f"{ATTEMPT_ID}-capture-v2",
        "evaluation_id": f"{ATTEMPT_ID}-evaluation-v2",
        "configuration_id": "claude-desktop",
        "repetition": 1,
        "measurement": measurement,
        "observer": {"id": "observer-v2-private.json", "sha256": observer_sha},
        "native_manifest": {"id": "native-manifest-v2-private.json", "sha256": native_manifest_sha},
        "decoder": {"id": "claude-code-decoder-v2.py", "sha256": _sha256(ROOT / "session_bench/adapters/claude_code_decoder.py")},
        "identity": {
            "provider": "Anthropic",
            "harness": "Claude Code",
            "surface": "Desktop Code (Local)",
            "execution_mode": "Claude Desktop local task",
            "os": "macOS",
            "build": BUILD,
            "model": family.get("model") or "model-unreported",
            "configuration": "claude-desktop",
            "protocol_version": "1.0-survival",
            "workload_version": "1.0-survival-workload",
            "observer_schema_version": "1.0-survival-observer",
            "rubric_version": "1.0-survival-rubric",
        },
        "metric_evidence": _metric_evidence(SURVIVAL_METRICS, "observer-v2-private.json", native_id, native_session_sha),
    }
    validate_prospective_evidence_input(survival_evidence)
    survival_sha = _write_json(CAPTURE / "survival-evidence-v2-private.json", survival_evidence)
    evidence_31 = _build_31_evidence(
        measurement,
        format_evidence,
        survival_evidence,
        observer_id="observer-v2-private.json",
        native_artifact_id=native_id,
        native_artifact_sha256=native_session_sha,
    )
    evidence_31["claim_limit"] = "one Claude Desktop Code (Local) calibration; not a three-run score, rank, or vendor claim"
    evidence_31_sha = _write_json(CAPTURE / "evidence-31-v2-private.json", evidence_31)

    states = {row["id"]: row["state"] for row in measurement["metrics"]}
    broad_states = {row["id"]: row["state"] for row in validate_format_evidence(format_evidence)["profile"]["metrics"]}
    replay_receipt = {
        "schema_version": "session-bench-claude-desktop-replay-receipt-v2",
        "run_id": ATTEMPT_ID,
        "decoder": {"path": "session_bench/adapters/claude_code_decoder.py", "sha256": _sha256(ROOT / "session_bench/adapters/claude_code_decoder.py")},
        "packages": {"native": "native-private-v2", "offline": "offline-private-v2", "damage": "damage-r2-private-v2"},
        "decoded_counts": {"native": native_decoded["counts"], "offline": offline_decoded["counts"], "damage": damage_decoded["counts"]},
        "canonical_equality": {"native_vs_offline": native_decoded == offline_decoded, "native_decode_sha256": native_decode_sha, "offline_decode_sha256": offline_decode_sha},
        "selected_loss": {"intact_responses": native_decoded["counts"]["responses"], "damaged_responses": damage_decoded["counts"]["responses"], "response_loss_detected": damage_decoded["counts"]["responses"] < native_decoded["counts"]["responses"], "actions_preserved": damage_decoded["counts"]["actions"] == native_decoded["counts"]["actions"], "results_preserved": damage_decoded["counts"]["results"] == native_decoded["counts"]["results"]},
        "family_validation_sha256": family_sha,
        "independent_reproduction": False,
        "stable_root_repetition_qualified": False,
    }
    replay_sha = _write_json(CAPTURE / "replay-receipt-v2-private.json", replay_receipt)
    attempt = {
        "schema_version": "session-bench-claude-desktop-attempt-v2",
        "attempt_id": ATTEMPT_ID,
        "configuration_id": "claude-desktop",
        "state": "captured_unscored",
        "score_eligible": False,
        "public_score_eligible": False,
        "session_id": SESSION_ID,
        "desktop_session_id": family["desktop_session_id"],
        "bridge_session_id": family["bridge_session_id"],
        "counts": native_decoded["counts"],
        "selected_loss": replay_receipt["selected_loss"],
        "complete_cross_root_family": True,
        "canonical_copy_proven": True,
        "independent_gui_observer": True,
        "decoder": {"format": native_decoded["format"], "native_counts": native_decoded["counts"], "offline_counts": offline_decoded["counts"], "canonical_equal": True, "source_sha256": replay_receipt["decoder"]["sha256"]},
        "survival_measurement": {"path": "measurement-v2-private.json", "sha256": measurement_sha, "states": states},
        "format_evidence": {"path": "format-evidence-v2-private.json", "sha256": format_sha, "metric_count": len(FORMAT_METRICS), "states": broad_states},
        "evidence_31": {"path": "evidence-31-v2-private.json", "sha256": evidence_31_sha, "metric_count": len(PUBLIC_METRICS)},
        "replay_receipt": {"path": "replay-receipt-v2-private.json", "sha256": replay_sha},
        "stable_root_repetition": "unresolved: api04 is one calibration and does not prove repetitions 1, 2, and 3",
        "claim_limit": "calibration evidence only; no public score, rank, badge, recommendation, or vendor claim",
    }
    attempt_sha = _write_json(CAPTURE / "attempt-v2.json", attempt)

    hashes = {"schema_version": "session-bench-artifact-hashes-v2", "source_attempt": ATTEMPT_ID, "artifacts": _artifact_rows(CAPTURE)}
    hashes_sha = _write_json(CAPTURE / "artifact-hashes-v2.json", hashes)
    result = {
        "status": "complete",
        "attempt": "capture/attempt-v2.json",
        "attempt_sha256": attempt_sha,
        "counts": native_decoded["counts"],
        "damage_counts": damage_decoded["counts"],
        "canonical_equal": True,
        "format_states": broad_states,
        "survival_states": states,
        "stable_root_state": broad_states["broad.stable_root_location"],
        "artifact_hashes": {"path": "capture/artifact-hashes-v2.json", "sha256": hashes_sha},
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        close()
    except Exception as exc:
        print(f"close invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
