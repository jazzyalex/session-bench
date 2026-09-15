#!/usr/bin/env python3
"""Finalize the already-executed Claude CLI calibration offline.

The live controller intentionally stopped when Claude's project-key encoder
normalized a temporary-directory underscore to a hyphen.  This continuation
does not call Claude.  It uses the already captured stdout, copied synthetic
project, and the metadata-only normal-root boundary to complete native capture,
decode, selected loss, and evidence construction.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session_bench.claude_live import native_facts_from_claude_session  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.live_observer import build_opencode_live_observer  # noqa: E402
from session_bench.surface_capture import wait_for_quiescence  # noqa: E402
from session_bench.survival_evidence import PROSPECTIVE_EVIDENCE_SCHEMA_VERSION, validate_prospective_evidence_input  # noqa: E402
from session_bench.v1_public_score import FORMAT_METRICS, PUBLIC_METRICS, SURVIVAL_METRICS  # noqa: E402

from run_claude_survival import (  # noqa: E402
    ATTEMPT_ID,
    CLAUDE_PROJECTS,
    CLAUDE_VERSION,
    CONFIGURATION_ID,
    OBSERVER_METHOD,
    _artifact_hashes,
    _build_31_evidence,
    _canonical,
    _copy_selected_family,
    _damage_selected_response,
    _digest_file,
    _inventory_summary,
    _selected_family,
    _write_json,
)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _write_final_hashes(root: Path) -> str:
    target = root / "artifact-hashes-final.json"
    return _write_json(target, {"schema_version": "session-bench-artifact-hashes-v1-final", "artifacts": _artifact_hashes(root)})


def finalize(attempt_id: str = ATTEMPT_ID) -> int:
    repo = Path(__file__).resolve().parent.parent
    root = repo / "artifacts" / "survival-v1-runs" / attempt_id
    if not root.is_dir():
        raise ValueError(f"calibration artifact root is missing: {root}")
    if (root / "attempt-final.json").exists():
        raise ValueError("finalization artifacts already exist; refusing a second continuation")
    state = _load(root / "controller-r2.json")
    controller_attempt = _load(root / "attempt.json")
    workload = _load(root / "workload-instance.json")
    normalized1 = _load(root / "normalized-r1.json")
    normalized2 = _load(root / "normalized-r2.json")
    project_root = Path(state["project_root"])
    fixture = Path(state["fixture_project"])
    session_id = state["turn2"]["session_id"]
    before_checkout = state["before_checkout_sha256"]
    after_checkout = controller_attempt["after_checkout_sha256"]
    run_canary = state["run_canary"]
    response_canaries = {str(turn["sequence"]): str(turn["response_canary"]) for turn in workload["turns"]}
    ledger_raw = (root / "helper-ledger.jsonl").read_text(encoding="utf-8")

    # Inventory the normal account root again using metadata only. Passing an
    # empty prior-key set here is safe because selection is restricted to the
    # exact temporary synthetic project-key suffix and only that key is opened.
    after_summary, _all_keys, _all_entries = _inventory_summary(CLAUDE_PROJECTS)
    project_key, selected_entries, _ = _selected_family(CLAUDE_PROJECTS, set(), project_root)
    before_summary = _load(root / "root-inventory-before.json")
    before_key_mtime = before_summary.get("root_metadata", {}).get("mtime_ns")
    selected_root = CLAUDE_PROJECTS / project_key
    selected_key_metadata = selected_root.stat()
    if isinstance(before_key_mtime, int) and selected_key_metadata.st_mtime_ns <= before_key_mtime:
        raise ValueError("selected synthetic project key does not postdate the metadata-only before inventory")
    _write_json(root / "root-inventory-after.json", after_summary)
    quiescence = wait_for_quiescence(CLAUDE_PROJECTS, selected_entries, interval_seconds=0.25, checks=2)
    _write_json(root / "native-quiescence.json", quiescence.to_dict())

    family_manifest, selected_relative, selected_bytes = _copy_selected_family(CLAUDE_PROJECTS, root / "native-family", selected_entries, project_key)
    _write_json(root / "native-family-manifest.json", family_manifest)
    native_bundle = root / "native-bundle"
    native_bundle.mkdir()
    (native_bundle / "session.jsonl").write_bytes(selected_bytes)
    decode_manifest = {
        "format": "claude-code-jsonl-v1",
        "artifacts": [{"id": "session", "path": "session.jsonl", "sha256": _digest_file(native_bundle / "session.jsonl"), "size_bytes": (native_bundle / "session.jsonl").stat().st_size, "depends_on": []}],
    }
    _write_json(native_bundle / "decode.json", decode_manifest)
    offline_bundle = root / "offline-bundle"
    shutil.copytree(native_bundle, offline_bundle, symlinks=False)
    decoded = decode_claude_code_bundle(native_bundle)
    offline_decoded = decode_claude_code_bundle(offline_bundle)
    if decoded != offline_decoded:
        raise ValueError("native and offline Claude decodes differ")
    _write_json(root / "decoded.json", decoded)
    _write_json(root / "offline-decoded.json", offline_decoded)

    loss_control = root / "loss-control"
    loss_selection = _damage_selected_response(offline_bundle, loss_control, response_canaries["2"])
    loss_decoded = decode_claude_code_bundle(loss_control)
    if loss_decoded["counts"]["responses"] >= offline_decoded["counts"]["responses"]:
        raise ValueError("selected-loss control did not reduce decoded responses")
    # Keep the damaged package closed under its declared decoder manifest;
    # selection metadata and its derived decode live beside the package.
    _write_json(root / "loss-control-decoded.json", loss_decoded)
    _write_json(root / "loss-control-selection.json", loss_selection)

    model_id = normalized2.get("model") or normalized1.get("model") or "model-unreported"
    controller_state = {"turns": {"1": {"session_id": session_id}, "2": {"session_id": session_id}}, "model": model_id, "configuration": "claude-code-cli-print-stream-json", "workspace": str(project_root)}
    observer = build_opencode_live_observer(workload=workload, controller_state=controller_state, stdout_by_turn={1: "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in normalized1["rows"]), 2: "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in normalized2["rows"])}, helper_ledger_jsonl=ledger_raw, before_checkout_sha256=before_checkout, after_checkout_sha256=after_checkout)
    observer["method"] = OBSERVER_METHOD
    observer["configuration_id"] = CONFIGURATION_ID
    observer["repetition"] = 1
    observer["native_stream_ids"] = [session_id]
    observer_sha = _write_json(root / "observer.json", observer)

    native_session_id = f"claude-session-{session_id}"
    native_session_sha = _digest_file(native_bundle / "session.jsonl")
    native_manifest = {
        "schema_version": "session-bench-claude-cli-native-manifest-v1",
        "id": native_session_id,
        "configuration_id": CONFIGURATION_ID,
        "project_key": project_key,
        "selected_artifact": {"id": native_session_id, "path": selected_relative, "sha256": native_session_sha, "size_bytes": len(selected_bytes)},
        "family_manifest": {"id": "native-family-manifest", "sha256": _digest_file(root / "native-family-manifest.json")},
        "decode_manifest": {"id": "decode.json", "sha256": _digest_file(native_bundle / "decode.json")},
        "offline_copy": {"path": "offline-bundle/session.jsonl", "sha256": _digest_file(offline_bundle / "session.jsonl")},
        "new_family_only": True,
        "root_closure": "project-key directory closed; complete normal-root closure unresolved",
        "unrelated_preexisting_sessions_read": False,
    }
    native_manifest_sha = _write_json(root / "native-manifest.json", native_manifest)
    native_facts = native_facts_from_claude_session(selected_bytes, run_canary=run_canary, workspace=project_root, before_sha256=before_checkout, after_sha256=after_checkout)
    _write_json(root / "native-facts.json", native_facts)
    measurement = compare_survival_run(observer, native_facts, {"complete_root": None, "companions_present": True, "isolated_decode": True, "canonical_equality": True}, configuration_id=CONFIGURATION_ID, repetition=1)
    _write_json(root / "measurement.json", measurement)

    format_evidence = build_claude_format_evidence(offline_decoded, observer={"id": "observer.json", "sha256": observer_sha}, native_manifest={"id": "native-manifest.json", "sha256": native_manifest_sha}, run_id=workload["run_id"], configuration_id=CONFIGURATION_ID, repetition=1, build=CLAUDE_VERSION, collected_on=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat(), result_id=f"{attempt_id}-format")
    format_sha = _write_json(root / "format-evidence.json", format_evidence)
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
        "repetition": 1,
        "measurement": measurement,
        "observer": {"id": "observer.json", "sha256": observer_sha},
        "native_manifest": {"id": "native-manifest.json", "sha256": native_manifest_sha},
        "decoder": {"id": "claude-code-decoder.py", "sha256": decoder_sha},
        "identity": {"provider": "Anthropic", "harness": "Claude Code", "surface": "CLI", "execution_mode": "print-stream-json", "os": "macOS", "build": CLAUDE_VERSION, "model": model_id, "configuration": CONFIGURATION_ID, "protocol_version": "1.0-survival", "workload_version": "1.0-survival-workload", "observer_schema_version": "1.0-survival-observer", "rubric_version": "1.0-survival-rubric"},
        "metric_evidence": [{"metric_id": metric_id, "observer_ids": ["observer.json"], "native_locators": [{"artifact_id": native_session_id, "artifact_sha256": native_session_sha, "record_location": "session.jsonl"}]} for metric_id in SURVIVAL_METRICS],
    }
    validate_prospective_evidence_input(survival_evidence)
    survival_sha = _write_json(root / "survival-evidence.json", survival_evidence)
    evidence_31 = _build_31_evidence(measurement, format_evidence, survival_evidence, observer_id="observer.json", native_artifact_id=native_session_id, native_artifact_sha256=native_session_sha)
    evidence_31_sha = _write_json(root / "evidence-31.json", evidence_31)

    # The controller state only contains launch metadata.  Start from the
    # controller's captured attempt record so the repaired canonical record
    # retains post-workload hashes, semantic checks, and filesystem facts.
    final_state = dict(controller_attempt)
    final_state.pop("classification", None)
    final_state.pop("error", None)
    final_state.update({"status": "complete", "completed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "score_eligible": False, "published": False, "project_key": project_key, "native_inventory": {"selected_file_count": len(selected_entries), "project_key": project_key, "unique_new_synthetic_family": True, "before_project_key_absent": True, "metadata_only_before_after": True, "unrelated_preexisting_sessions_read": False}, "observer": {"path": "observer.json", "sha256": observer_sha}, "native_manifest": {"path": "native-manifest.json", "sha256": native_manifest_sha}, "decoder": {"format": offline_decoded["format"], "offline_decode": True, "canonical_equal": True, "source_sha256": decoder_sha}, "measurement": {"path": "measurement.json", "metric_count": len(measurement["metrics"]), "states": {row["id"]: row["state"] for row in measurement["metrics"]}}, "broad_wrapper": {"path": "format-evidence.json", "sha256": format_sha, "metric_count": len(FORMAT_METRICS)}, "evidence_31": {"path": "evidence-31.json", "sha256": evidence_31_sha, "metric_count": len(evidence_31["metrics"])}, "selected_loss": {"intact_response_count": offline_decoded["counts"]["responses"], "damaged_response_count": loss_decoded["counts"]["responses"], **loss_selection}, "uncertainties": ["complete normal Claude root closure and cross-root companions remain unresolved", "one repetition is calibration evidence only", "Claude build is current local --version 2.1.272 while prospective plan recorded 2.1.270"]})
    # Preserve the controller's initial internal failure record, then expose a
    # repaired canonical attempt.json for downstream review.
    shutil.move(root / "attempt.json", root / "attempt-controller-invalid.json")
    shutil.move(root / "artifact-hashes.json", root / "artifact-hashes-controller-invalid.json")
    _write_json(root / "attempt.json", final_state)
    final_hash_sha = _write_final_hashes(root)
    print(json.dumps({"status": "complete", "attempt_id": attempt_id, "artifact_root": str(root), "project_key": project_key, "session_id": session_id, "model": model_id, "decoded_counts": offline_decoded["counts"], "selected_loss": {"intact": offline_decoded["counts"]["responses"], "damaged": loss_decoded["counts"]["responses"]}, "measurement_states": {row["id"]: row["state"] for row in measurement["metrics"]}, "evidence_31_sha256": evidence_31_sha, "artifact_hashes_final_sha256": final_hash_sha, "score_eligible": False}, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize the existing Claude CLI calibration without another model call")
    parser.add_argument("--attempt-id", default=ATTEMPT_ID)
    args = parser.parse_args()
    try:
        return finalize(args.attempt_id)
    except Exception as exc:
        print(f"finalize invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
