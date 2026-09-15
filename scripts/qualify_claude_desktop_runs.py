#!/usr/bin/env python3
"""Qualify the three accepted Claude Desktop evaluated captures offline.

The first two scheduled captures remain preserved as invalid because their
per-run filesystem observer was not copied before the fixture was reset.  This
command uses their declared correction captures plus the original third
capture.  It reads only already-copied synthetic evidence and writes additive
qualified derivatives; it never discovers a Claude root or publishes a score.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.live_metric_comparator import compare_survival_run  # noqa: E402
from session_bench.surface_capture import canonical_bytes  # noqa: E402
from session_bench.survival_evidence import validate_prospective_evidence_input  # noqa: E402
from session_bench.v1_public_score import PUBLIC_METRICS, score_public_run, validate_format_evidence  # noqa: E402
from scripts.run_claude_survival import _build_31_evidence  # noqa: E402


RUNS = (
    ("claude-desktop-eval-1-correction-1", 1, "claude-desktop-eval-1"),
    ("claude-desktop-eval-2-correction-1", 2, "claude-desktop-eval-2"),
    ("claude-desktop-eval-3", 3, None),
)


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite {path}")
    data = canonical_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _turn_texts(native: dict) -> list[str]:
    turns = native.get("turns")
    if not isinstance(turns, list):
        raise ValueError("native facts lack turns")
    return [str(row.get("text")) for row in turns if isinstance(row, dict)]


def _observer_with_exact_submissions(observer: dict, gui: dict, native: dict) -> dict:
    prompts = gui.get("submitted_prompts")
    if not isinstance(prompts, dict) or set(prompts) != {"r1", "r2"}:
        raise ValueError("GUI v2 receipt lacks exact submitted prompts")
    expected = [prompts["r1"], prompts["r2"]]
    if _turn_texts(native) != expected:
        raise ValueError("independent GUI submitted prompts do not equal copied native turns")
    result = deepcopy(observer)
    user_turns = [row for row in result.get("events", []) if row.get("kind") == "user_turn"]
    if len(user_turns) != 2:
        raise ValueError("observer must contain exactly two submitted turns")
    for row, text in zip(user_turns, expected, strict=True):
        row["fields"]["text"] = text
    result["method"] = (
        "independent Claude Desktop accessibility receipt, exact submitted prompts, "
        "preserved helper ledger, and filesystem hashes; copied native transcript "
        "used only after observer construction for equality comparison"
    )
    result["gui_receipt"] = "observer-private/gui-observer-v2.json"
    return result


def qualify() -> list[dict[str, object]]:
    root_repetitions = [
        {
            "repetition": repetition,
            "root_locator": "CLAUDE_HOME/projects/<project-key>/<session-id>.jsonl + Claude Application Support/claude-code-sessions/<account>/<workspace>/<desktop-session-id>.json",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }
        for _run_id, repetition, _corrects in RUNS
    ]
    results: list[dict[str, object]] = []
    for run_id, repetition, corrects in RUNS:
        run = ROOT / "artifacts/survival-v1-runs" / run_id
        source = run / "capture/finalized-private-v1"
        output = run / "capture/qualified-private-v1"
        if output.exists() or output.is_symlink():
            raise ValueError(f"qualified output already exists: {output}")
        finalized = _load(source / "attempt-finalized.json")
        if finalized.get("repetition") != repetition or finalized.get("canonical_copy_proven") is not True:
            raise ValueError(f"{run_id} is not the accepted finalized repetition")
        if finalized.get("complete_cross_root_family") is not True or finalized.get("independent_gui_observer") is not True:
            raise ValueError(f"{run_id} lacks Desktop family or GUI closure")
        replay = _load(source / "replay-receipt.json")
        if replay.get("canonical_equality", {}).get("native_vs_offline") is not True:
            raise ValueError(f"{run_id} lacks canonical equality")
        selected = replay.get("selected_loss", {})
        if not all(selected.get(key) is True for key in ("response_loss_detected", "actions_preserved", "results_preserved")):
            raise ValueError(f"{run_id} lacks selected-loss closure")

        gui = _load(run / "capture/observer-private/gui-observer-v2.json")
        observer = _observer_with_exact_submissions(
            _load(source / "observer.json"), gui, _load(source / "native-facts.json")
        )
        native = _load(source / "native-facts.json")
        measurement = compare_survival_run(
            observer,
            native,
            {
                "complete_root": True,
                "companions_present": True,
                "isolated_decode": True,
                "canonical_equality": True,
            },
            configuration_id="claude-desktop",
            repetition=repetition,
        )
        unresolved = [row["id"] for row in measurement["metrics"] if row["state"] in {"unresolved", "decoder_unsupported", "unexercised", "invalid_capture"}]
        if unresolved:
            raise ValueError(f"{run_id} unresolved survival metrics: {unresolved}")

        observer_sha = _write(output / "observer-qualified.json", observer)
        measurement_sha = _write(output / "measurement-qualified.json", measurement)
        family = _load(source / "family-validation.json")
        native_manifest = _load(source / "native-manifest.json")
        native_manifest.update(
            {
                "schema_version": "session-bench-claude-desktop-native-manifest-v3-qualified",
                "stable_root_repetition_qualified": True,
                "root_repetitions": root_repetitions,
                "complete_persistent_family": True,
            }
        )
        native_manifest_sha = _write(output / "native-manifest-qualified.json", native_manifest)

        decoded = _load(source / "decoded-offline.json")
        format_document = build_claude_format_evidence(
            decoded,
            observer={"id": "observer-qualified.json", "sha256": observer_sha},
            native_manifest={"id": "native-manifest-qualified.json", "sha256": native_manifest_sha},
            run_id=measurement["run_id"],
            configuration_id="claude-desktop",
            repetition=repetition,
            build=str(family.get("cli_version") or "build-unreported"),
            collected_on="2026-09-15",
            result_id=f"{run_id}-format-qualified-v1",
            complete_record_family=True,
            root_repetitions=root_repetitions,
        )
        validate_format_evidence(format_document)
        format_sha = _write(output / "format-evidence-qualified.json", format_document)

        survival = _load(source / "survival-evidence.json")
        survival["evaluation_id"] = f"{run_id}-evaluation-qualified-v1"
        survival["measurement"] = measurement
        survival["observer"] = {"id": "observer-qualified.json", "sha256": observer_sha}
        survival["native_manifest"] = {"id": "native-manifest-qualified.json", "sha256": native_manifest_sha}
        validate_prospective_evidence_input(survival)
        survival_sha = _write(output / "survival-evidence-qualified.json", survival)

        score = score_public_run(survival, format_document)
        if not score.rankable or score.overall is None or score.blockers or len(score.metrics) != len(PUBLIC_METRICS):
            raise ValueError(f"{run_id} did not become scorer-complete: {score.blockers}")
        native_selected = native_manifest["selected_artifact"]
        evidence_31 = _build_31_evidence(
            measurement,
            format_document,
            survival,
            observer_id="observer-qualified.json",
            native_artifact_id=str(native_selected["id"]),
            native_artifact_sha256=str(native_selected["sha256"]),
        )
        evidence_31["claim_limit"] = "private scorer-complete run; no public rank before configuration reproduction and cohort assembly"
        evidence_31_sha = _write(output / "evidence-31-qualified.json", evidence_31)
        summary = {
            "schema_version": "session-bench-claude-desktop-qualified-run-v1",
            "configuration_id": "claude-desktop",
            "run_id": measurement["run_id"],
            "source_attempt_id": run_id,
            "corrects_invalid_capture": corrects,
            "repetition": repetition,
            "status": "scorer_complete_private",
            "public_score": False,
            "score": {
                "overall": score.display()["overall"],
                "categories": {key: value["score"] for key, value in score.display()["categories"].items()},
                "blockers": list(score.blockers),
            },
            "metric_count": len(score.metrics),
            "stable_root_repetitions": [1, 2, 3],
            "source_hashes": {
                "observer": observer_sha,
                "measurement": measurement_sha,
                "format": format_sha,
                "survival": survival_sha,
                "evidence_31": evidence_31_sha,
                "native_manifest": native_manifest_sha,
                "replay_receipt": _sha(source / "replay-receipt.json"),
                "gui_receipt": _sha(run / "capture/observer-private/gui-observer-v2.json"),
            },
        }
        _write(output / "qualification.json", summary)
        results.append(summary)
    return results


if __name__ == "__main__":
    print(json.dumps(qualify(), ensure_ascii=False, sort_keys=True, indent=2))
