#!/usr/bin/env python3
"""Close one already captured Codex Desktop survival-v1 run offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.adapters.codex_cli_decoder import (  # noqa: E402
    ExpectedAction,
    FrozenWorkload,
    decode_codex_cli_bundle,
)
from session_bench.codex_cli_calibration import (  # noqa: E402
    _remove_selected_r2_response,
)
from session_bench.codex_format_evidence import build_codex_format_evidence  # noqa: E402
from session_bench.surface_capture import canonical_bytes  # noqa: E402


def _write(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workload(value: dict[str, object]) -> FrozenWorkload:
    turns = tuple((item["id"], item["text"]) for item in value["turns"])
    canaries = tuple((item["turn_id"], item["value"]) for item in value["response_canaries"])
    actions = tuple(
        ExpectedAction(
            item["id"],
            item["turn_id"],
            item["kind"],
            " ".join(item["argv"]) if item["kind"] != "edit" else None,
            item["target"],
            item["helper_nonce"],
        )
        for item in value["actions"]
    )
    return FrozenWorkload(
        run_id=value["run_id"],
        run_canary=value["run_canary"],
        turns=turns,
        response_canaries=canaries,
        actions=actions,
    )


def _copy_regular(source: Path, destination: Path) -> dict[str, object]:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"source must be a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"refusing to overwrite: {destination}")
    shutil.copyfile(source, destination)
    return {
        "path": destination.name,
        "sha256": _sha(destination),
        "size_bytes": destination.stat().st_size,
    }


def close_run(run_root: Path, *, build: str, collected_on: str, generation: str = "v2") -> Path:
    run_root = run_root.resolve()
    capture = run_root / "capture"
    if not generation or not generation.replace("-", "").isalnum():
        raise ValueError("generation must be a simple label")
    package = capture / f"canonical-private-{generation}"
    package.mkdir()
    rollout_candidates = list((capture / "native-private").rglob("rollout-*.jsonl"))
    shell_candidates = list((capture / "shell-snapshot-private").glob("*.sh"))
    if len(rollout_candidates) != 1 or len(shell_candidates) != 1:
        raise ValueError("capture must contain exactly one rollout and one shell snapshot")
    artifacts = [
        ("rollout", _copy_regular(rollout_candidates[0], package / "rollout.jsonl"), []),
        ("shell-snapshot", _copy_regular(shell_candidates[0], package / "shell_snapshot.sh"), ["rollout"]),
        (
            "desktop-family",
            _copy_regular(capture / "codex-desktop-family-private.json", package / "desktop_family.json"),
            ["rollout"],
        ),
    ]
    manifest = {
        "format": "codex-rollout-v1",
        "artifacts": [
            {"id": artifact_id, **row, "depends_on": dependencies}
            for artifact_id, row, dependencies in artifacts
        ],
    }
    _write(package / "decode.json", manifest)

    workload = _workload(json.loads((run_root / "workload-instance.json").read_text()))
    try:
        repetition = int(workload.run_id.rsplit("-", 1)[1])
    except (ValueError, IndexError) as exc:
        raise ValueError("run_id must end in evaluated repetition 1, 2, or 3") from exc
    required = ("shell_snapshot.sh", "desktop_family.json")
    ordinary = decode_codex_cli_bundle(
        package,
        workload=workload,
        complete_root=True,
        required_companions=required,
        configuration_id="codex-desktop",
        repetition=repetition,
    )
    _write(capture / f"decoded-private-{generation}.json", ordinary)

    offline = capture / f"offline-replay-private-{generation}"
    shutil.copytree(package, offline)
    replay = decode_codex_cli_bundle(
        offline,
        workload=workload,
        complete_root=True,
        required_companions=required,
        isolated_decode_proven=True,
        canonical_equality_proven=True,
        configuration_id="codex-desktop",
        repetition=repetition,
    )
    _write(capture / f"decoded-offline-private-{generation}.json", replay)

    damage = capture / f"damage-r2-private-{generation}"
    shutil.copytree(package, damage)
    _remove_selected_r2_response(damage, workload.canary_by_turn["turn-r2"])
    damaged = decode_codex_cli_bundle(
        damage,
        workload=workload,
        complete_root=True,
        required_companions=required,
        isolated_decode_proven=True,
        configuration_id="codex-desktop",
        repetition=repetition,
    )
    _write(capture / f"damage-r2-decoded-private-{generation}.json", damaged)

    intact_r2 = next(row for row in ordinary["metrics"] if row["id"] == "work.visible_responses")
    damaged_r2 = next(row for row in damaged["metrics"] if row["id"] == "work.visible_responses")
    if intact_r2["correct"] != 2 or damaged_r2["correct"] != 1:
        raise ValueError("selected R2 loss control did not reduce visible-response recovery")
    excluded = {"portable.isolated_decode", "portable.canonical_equality"}
    ordinary_measurement = {
        **{k: v for k, v in ordinary["measurement"].items() if k != "metrics"},
        "metrics": [row for row in ordinary["measurement"]["metrics"] if row["id"] not in excluded],
    }
    replay_measurement = {
        **{k: v for k, v in replay["measurement"].items() if k != "metrics"},
        "metrics": [row for row in replay["measurement"]["metrics"] if row["id"] not in excluded],
    }
    if ordinary_measurement != replay_measurement:
        raise ValueError("offline replay measurement differs from ordinary decode")

    observer = json.loads((capture / "observer.json").read_text())
    observer_binding = {"id": observer["observer_id"], "sha256": _sha(capture / "observer.json")}
    manifest_binding = {"id": f"capture/canonical-private-{generation}/decode.json", "sha256": _sha(package / "decode.json")}
    format_evidence = build_codex_format_evidence(
        replay,
        observer=observer_binding,
        native_manifest=manifest_binding,
        build=build,
        collected_on=collected_on,
        result_id=f"{workload.run_id}-format",
        complete_record_family=True,
    )
    _write(capture / f"format-evidence-private-{generation}.json", format_evidence)
    attempt_path = run_root / f"attempt-{generation}.json"
    _write(
        attempt_path,
        {
            "schema_version": "session-bench-codex-desktop-attempt-v1",
            "run_id": workload.run_id,
            "configuration_id": "codex-desktop",
            "status": "captured_unscored",
            "complete_record_family": True,
            "offline_canonical_equality": True,
            "selected_r2_loss_detected": True,
            "survival_metric_count": 19,
            "format_metric_count": 12,
            "unresolved_format_metrics": [
                metric_id
                for metric_id, evidence in format_evidence["profile"]["broad_evidence"].items()
                if not evidence.get("evidence_complete")
            ],
        },
    )
    return attempt_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--build", required=True)
    parser.add_argument("--collected-on", required=True)
    parser.add_argument("--generation", default="v2")
    args = parser.parse_args()
    print(close_run(args.run_root, build=args.build, collected_on=args.collected_on, generation=args.generation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
