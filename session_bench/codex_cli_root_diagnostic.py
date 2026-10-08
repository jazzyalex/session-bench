"""Private, additive stable-root diagnostic for retained Codex CLI runs.

This module does not change a replay packet or grant release qualification.  It
binds the three metadata-safe normal-root receipts to the exact native files in
the current private stdout-v2 packets, then checks the existing format scorer
with only ``broad.stable_root_location`` replaced by that bound evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .native_replay import canonical
from .v1_public_score import score_public_control_run, validate_format_evidence


_SELECTED_PATH = re.compile(
    r"^\d{4}/\d{2}/\d{2}/rollout-[^/]+-(?P<session>[0-9a-f-]{36})\.jsonl$"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected JSON object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric_states(receipt: Mapping[str, Any]) -> dict[str, str]:
    rows = receipt["diagnostics"]["intact"]["metrics"]
    return {row["id"]: row["state"] for row in rows}


def build_codex_cli_root_diagnostic(repository: Path) -> dict[str, Any]:
    """Return a hash-bound private diagnostic without mutating source packets."""
    repository = repository.resolve()
    replay_root = repository / "artifacts/v1-expanded-preparation/codex-stdout-score-replay-v2"
    run_root = repository / "artifacts/survival-v1-runs"
    repetitions: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    before_states: dict[str, dict[str, str]] = {}
    after_states: dict[str, dict[str, str]] = {}

    for repetition in (1, 2, 3):
        run_id = f"codex-cli-eval-{repetition}"
        packet = replay_root / run_id
        receipt_path = replay_root / f"{run_id}-receipt.json"
        manifest_path = packet / "manifest.json"
        receipt = _load(receipt_path)
        manifest = _load(manifest_path)
        if manifest.get("configuration_id") != "codex-cli" or manifest.get("repetition") != repetition:
            raise ValueError(f"{run_id}: unexpected replay identity")
        if receipt.get("manifest_sha256") != _sha(manifest_path):
            raise ValueError(f"{run_id}: replay receipt does not bind manifest")
        states = _metric_states(receipt)
        if states.get("broad.stable_root_location") != "unresolved":
            raise ValueError(f"{run_id}: stable-root predecessor is not unresolved")

        original = run_root / run_id
        calibration_path = original / "capture/calibration-receipt.json"
        normal_path = original / "capture/requalification-v1/capture/normal-root-receipt.json"
        calibration = _load(calibration_path)
        normal = _load(normal_path)
        selected_path = normal.get("selected_path")
        selected_match = _SELECTED_PATH.fullmatch(selected_path) if isinstance(selected_path, str) else None
        if not selected_match or selected_match.group("session") != normal.get("session_id"):
            raise ValueError(f"{run_id}: selected path is not session-bound")
        if not (
            normal.get("schema_version") == "1.0-codex-cli-normal-root-requalification"
            and normal.get("configuration_id") == "codex-cli"
            and normal.get("run_id") == run_id
            and normal.get("root") == "CODEX_HOME/sessions"
            and normal.get("metadata_only_before_after") is True
            and normal.get("preexisting_native_content_opened") is False
            and normal.get("preexisting_native_paths_disclosed") is False
            and normal.get("capture", {}).get("offline_decode_equal") is True
            and normal.get("capture", {}).get("selected_loss_detected") is True
        ):
            raise ValueError(f"{run_id}: normal-root receipt boundary is incomplete")
        historical = normal.get("historical_proof", {})
        if not (
            historical.get("receipt_sha256") == _sha(calibration_path)
            and historical.get("after_r1_file_count") == historical.get("before_file_count") + 1
            and historical.get("after_r2_file_count") == historical.get("after_r1_file_count")
            and calibration.get("thread_id") == normal.get("session_id")
        ):
            raise ValueError(f"{run_id}: historical root transition is not closed")

        native_entry = next(
            (
                row for row in manifest.get("files", [])
                if row.get("path") == f"native/{selected_path}"
            ),
            None,
        )
        native_path = packet / "native" / selected_path
        selected = normal.get("current_selected_metadata", {})
        if not (
            isinstance(native_entry, dict)
            and native_entry.get("sha256") == historical.get("selected_native_sha256") == _sha(native_path)
            and native_entry.get("size_bytes") == native_path.stat().st_size
            and selected.get("root") == "CODEX_HOME/sessions"
            and selected.get("file_count") == 1
            and selected.get("total_size_bytes") == native_path.stat().st_size
            and selected.get("metadata_only") is True
            and selected.get("preexisting_paths_disclosed") is False
        ):
            raise ValueError(f"{run_id}: selected native file is not hash-bound")
        quiescence = normal.get("quiescence", {})
        observed = quiescence.get("observed")
        if not (
            quiescence.get("stable") is True
            and quiescence.get("checks") == 2
            and isinstance(observed, list)
            and len(observed) == 2
            and observed[0] == observed[1]
            and len(observed[0]) == 1
            and observed[0][0].get("relative_path") == selected_path
            and observed[0][0].get("filesystem_id") == historical.get("selected_filesystem_id")
            and observed[0][0].get("size_bytes") == native_path.stat().st_size
        ):
            raise ValueError(f"{run_id}: selected native file was not quiescent")

        root_row = {
            "repetition": repetition,
            "root_locator": "CODEX_HOME/sessions/YYYY/MM/DD/rollout-<session-id>.jsonl",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }
        repetitions.append(root_row)
        profile = deepcopy(receipt["diagnostics"]["intact"]["format_evidence"])
        profile["profile"]["broad_evidence"]["broad.stable_root_location"] = {
            "evidence_complete": True,
            "repetitions": deepcopy(repetitions) if repetition == 3 else [],
        }
        before_states[run_id] = states
        bindings.append({
            "run_id": run_id,
            "repetition": repetition,
            "replay_manifest_sha256": _sha(manifest_path),
            "replay_receipt_sha256": _sha(receipt_path),
            "normal_root_receipt_sha256": _sha(normal_path),
            "calibration_receipt_sha256": _sha(calibration_path),
            "selected_native_path": f"native/{selected_path}",
            "selected_native_sha256": _sha(native_path),
        })

    # Apply the same three-repetition proof to each run's format profile and
    # prove that the scorer changes only this one metric.
    for repetition in (1, 2, 3):
        run_id = f"codex-cli-eval-{repetition}"
        receipt = _load(replay_root / f"{run_id}-receipt.json")
        intact = receipt["diagnostics"]["intact"]
        successor = deepcopy(intact["format_evidence"])
        successor["profile"]["broad_evidence"]["broad.stable_root_location"] = {
            "evidence_complete": True,
            "repetitions": deepcopy(repetitions),
        }
        validate_format_evidence(successor)
        scored_before = score_public_control_run(intact["measurement"], intact["format_evidence"]["profile"])
        scored_after = score_public_control_run(intact["measurement"], successor["profile"])
        changed = {
            key for key in scored_before.metrics
            if scored_before.metrics[key] != scored_after.metrics[key]
        }
        if changed != {"broad.stable_root_location"} or scored_after.metrics["broad.stable_root_location"] != 1:
            raise ValueError(f"{run_id}: stable-root proof changed unexpected score state")
        states = dict(before_states[run_id])
        states["broad.stable_root_location"] = "measured"
        after_states[run_id] = states

    return {
        "schema_version": "session-bench-codex-cli-root-private-diagnostic-v1",
        "configuration_id": "codex-cli",
        "scope": "private_additive_stable_root_only",
        "public_safe": False,
        "independent_reproduction": False,
        "rankable": False,
        "historical_inputs_overwritten": False,
        "metric_closure": {
            "metric_id": "broad.stable_root_location",
            "predecessor_state": "unresolved",
            "successor_state": "measured",
            "score_fraction": "1/1",
            "repetitions": repetitions,
        },
        "per_run_counts": [
            {
                "run_id": run_id,
                "measured": sum(state == "measured" for state in after_states[run_id].values()),
                "contradiction": sum(state == "contradiction" for state in after_states[run_id].values()),
                "unresolved": sum(state == "unresolved" for state in after_states[run_id].values()),
            }
            for run_id in sorted(after_states)
        ],
        "bindings": bindings,
        "limitations": [
            "This diagnostic does not change portable.complete_root; the copied packet still retains the predecessor contradiction.",
            "This diagnostic does not derive response usage from cumulative thread totals.",
            "This diagnostic is private and has not received independent reproduction or public-safety review.",
        ],
    }


def write_codex_cli_root_diagnostic(repository: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValueError("diagnostic output must be new")
    result = build_codex_cli_root_diagnostic(repository)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(result) + b"\n")
    return result
