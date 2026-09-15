#!/usr/bin/env python3
"""Close three captured Codex CLI runs under the metadata-safe normal-root contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle  # noqa: E402
from session_bench.codex_cli_calibration import _build_calibration_evidence  # noqa: E402
from session_bench.codex_format_evidence import build_codex_format_evidence  # noqa: E402
from session_bench.surface_capture import canonical_bytes  # noqa: E402
from session_bench.v1_public_score import score_public_control_run, validate_format_evidence  # noqa: E402
from requalify_codex_cli_capture import _workload_from_instance  # noqa: E402


def _load(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite {path}")
    path.write_bytes(canonical_bytes(value) + b"\n")


def qualify() -> list[dict[str, object]]:
    root_repetitions = [
        {
            "repetition": repetition,
            "root_locator": "CODEX_HOME/sessions/YYYY/MM/DD/rollout-<session-id>.jsonl",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }
        for repetition in (1, 2, 3)
    ]
    results: list[dict[str, object]] = []
    for repetition in (1, 2, 3):
        run = ROOT / f"artifacts/survival-v1-runs/codex-cli-eval-{repetition}"
        requalified = run / "capture/requalification-v1"
        receipt = _load(requalified / "requalification-receipt.json")
        normal = _load(requalified / "capture/normal-root-receipt.json")
        if not (
            receipt.get("status") == "calibration-only"
            and receipt.get("run_id") == run.name
            and normal.get("metadata_only_before_after") is True
            and normal.get("preexisting_native_content_opened") is False
            and normal.get("preexisting_native_paths_disclosed") is False
            and normal.get("capture", {}).get("offline_decode_equal") is True
            and normal.get("capture", {}).get("selected_loss_detected") is True
        ):
            raise ValueError(f"{run.name} normal-root evidence is incomplete")
        instance = _load(run / "workload-instance.json")
        workload = _workload_from_instance(instance, run.name)
        package = requalified / "capture/canonical-private"
        decoded = decode_codex_cli_bundle(
            package,
            workload=workload,
            complete_root=True,
            required_companions=(),
            isolated_decode_proven=True,
            canonical_equality_proven=True,
            configuration_id="codex-cli",
            repetition=repetition,
        )
        decoded_path = requalified / "capture/decoded-qualified.json"
        _write(decoded_path, decoded)

        prior_wrapper = _load(requalified / "capture/calibration-evidence.json")
        observer_binding = dict(prior_wrapper["observer"])
        native_binding = {
            "id": "capture/canonical-private/decode.json",
            "sha256": _sha(package / "decode.json"),
        }
        format_document = build_codex_format_evidence(
            decoded,
            observer=observer_binding,
            native_manifest=native_binding,
            build=str(prior_wrapper["format_evidence"]["build"]),
            collected_on=str(prior_wrapper["format_evidence"]["collected_on"]),
            result_id=f"codex-cli-eval-{repetition}-format-qualified",
            complete_record_family=True,
            root_repetitions=root_repetitions,
        )
        validate_format_evidence(format_document)
        format_path = requalified / "capture/format-evidence-qualified.json"
        _write(format_path, format_document)

        ledger = run / "project/fixture_project/.survival-observer.jsonl"
        wrapper = _build_calibration_evidence(
            decoded,
            format_document,
            observer=observer_binding,
            native_manifest=native_binding,
            native_mode="metadata-safe-normal-root",
            selected_loss=receipt["selected_loss"],
            checkout_before_sha256=instance["filesystem"]["before_sha256"],
            checkout_after_sha256=instance["filesystem"]["reference_after_sha256"],
            helper_ledger_sha256=_sha(ledger),
        )
        if wrapper["metric_counts"]["resolved"] != 31:
            raise ValueError(f"{run.name} still has unresolved metrics")
        wrapper_path = requalified / "capture/evidence-31-qualified.json"
        _write(wrapper_path, wrapper)

        score = score_public_control_run(decoded["measurement"], format_document["profile"])
        if not score.rankable or score.overall is None or score.blockers:
            raise ValueError(f"{run.name} is not scorer complete: {score.blockers}")
        display = score.display()
        result = {
            "schema_version": "session-bench-codex-cli-qualified-run-v1",
            "configuration_id": "codex-cli",
            "run_id": decoded["measurement"]["run_id"],
            "repetition": repetition,
            "status": "scorer_complete_private",
            "public_score": False,
            "overall": display["overall"],
            "categories": {key: value["score"] for key, value in display["categories"].items()},
            "blockers": list(score.blockers),
            "decoded_sha256": _sha(decoded_path),
            "format_evidence_sha256": _sha(format_path),
            "evidence_31_sha256": _sha(wrapper_path),
        }
        _write(requalified / "qualification.json", result)
        results.append(result)
    return results


if __name__ == "__main__":
    print(json.dumps(qualify(), ensure_ascii=False, sort_keys=True))
