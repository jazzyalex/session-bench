#!/usr/bin/env python3
"""Close three captured Claude CLI evaluated runs as complete record families."""

from __future__ import annotations

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
from session_bench.v1_public_score import score_public_control_run, validate_format_evidence  # noqa: E402


RUNS = tuple(ROOT / "artifacts/survival-v1-runs" / f"claude-cli-eval-{n}" for n in (1, 2, 3))


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
    roots = [
        {
            "repetition": number,
            "root_locator": "CLAUDE_HOME/projects/<project-key>/<session-id>.jsonl",
            "discovery_mode": "metadata_safe_normal_root",
            "personal_history_scanned": False,
        }
        for number in (1, 2, 3)
    ]
    results: list[dict[str, object]] = []
    for repetition, run in enumerate(RUNS, 1):
        attempt = _load(run / "attempt.json")
        if attempt.get("status") != "complete" or attempt.get("repetition") != repetition:
            raise ValueError(f"{run.name} is not the expected complete repetition")
        inventory = attempt.get("native_inventory", {})
        privacy = attempt.get("privacy", {})
        if not (
            inventory.get("before_project_key_absent") is True
            and inventory.get("quiescent") is True
            and inventory.get("selected_file_count") == 1
            and privacy.get("before_after_inventory_metadata_only") is True
            and privacy.get("unrelated_preexisting_sessions_read") is False
        ):
            raise ValueError(f"{run.name} did not prove one fresh project-key family")
        native_manifest = _load(run / "native-manifest.json")
        selected = native_manifest["selected_artifact"]
        copied = run / "native-bundle/session.jsonl"
        if _sha(copied) != selected["sha256"]:
            raise ValueError(f"{run.name} copied session hash differs")

        qualified_manifest = {
            **native_manifest,
            "schema_version": "session-bench-claude-cli-native-manifest-v1-qualified",
            "root_closure": "complete newly-created project-key family",
            "complete_record_family": True,
            "qualification": {
                "source_manifest": {"id": "native-manifest.json", "sha256": _sha(run / "native-manifest.json")},
                "metadata_only_before_after": True,
                "preexisting_content_opened": False,
                "selected_file_count": 1,
            },
        }
        qualified_manifest_path = run / "native-manifest-qualified.json"
        _write(qualified_manifest_path, qualified_manifest)

        observer = _load(run / "observer.json")
        native_facts = _load(run / "native-facts.json")
        measurement = compare_survival_run(
            observer,
            native_facts,
            {
                "complete_root": True,
                "companions_present": True,
                "isolated_decode": True,
                "canonical_equality": True,
            },
            configuration_id="claude-cli",
            repetition=repetition,
        )
        measurement_path = run / "measurement-qualified.json"
        _write(measurement_path, measurement)

        decoded = _load(run / "offline-decoded.json")
        format_document = build_claude_format_evidence(
            decoded,
            observer={"id": "observer.json", "sha256": _sha(run / "observer.json")},
            native_manifest={"id": "native-manifest-qualified.json", "sha256": _sha(qualified_manifest_path)},
            run_id=measurement["run_id"],
            configuration_id="claude-cli",
            repetition=repetition,
            build=str(attempt["claude_version"]),
            collected_on=str(attempt["completed_at"])[:10],
            result_id=f"claude-cli-eval-{repetition}-format-qualified",
            complete_record_family=True,
            root_repetitions=roots,
        )
        validate_format_evidence(format_document)
        format_path = run / "format-evidence-qualified.json"
        _write(format_path, format_document)

        survival = _load(run / "survival-evidence.json")
        survival["evaluation_id"] = f"claude-cli-eval-{repetition}-evaluation-qualified"
        survival["measurement"] = measurement
        survival["native_manifest"] = {
            "id": "native-manifest-qualified.json",
            "sha256": _sha(qualified_manifest_path),
        }
        validate_prospective_evidence_input(survival)
        survival_path = run / "survival-evidence-qualified.json"
        _write(survival_path, survival)

        score = score_public_control_run(measurement, format_document["profile"])
        if not score.rankable or score.overall is None or score.blockers:
            raise ValueError(f"{run.name} did not become scorer-complete: {score.blockers}")
        result = {
            "schema_version": "session-bench-claude-cli-qualified-run-v1",
            "configuration_id": "claude-cli",
            "run_id": measurement["run_id"],
            "repetition": repetition,
            "score": {
                "status": "scorer_complete_private",
                "public_score": False,
                "overall": score.display()["overall"],
                "categories": {
                    category_id: row["score"]
                    for category_id, row in score.display()["categories"].items()
                },
                "blockers": list(score.blockers),
            },
            "measurement_sha256": _sha(measurement_path),
            "format_evidence_sha256": _sha(format_path),
            "survival_evidence_sha256": _sha(survival_path),
            "native_manifest_sha256": _sha(qualified_manifest_path),
        }
        _write(run / "qualification.json", result)
        results.append(result)
    return results


if __name__ == "__main__":
    print(json.dumps(qualify(), ensure_ascii=False, sort_keys=True))
