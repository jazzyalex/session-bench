#!/usr/bin/env python3
"""Build one private successor from capture 01's retained original native source."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical
from session_bench.copilot_score_inputs import ASSERTION_SCHEMA, observer_from_capture_documents
from session_bench.score_replay import SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls

CAPTURE = ROOT / "artifacts/v1-expanded-preparation/live-captures/copilot-2026-09-29-01"
V5_RECEIPT = ROOT / "artifacts/v1-expanded-preparation/copilot-partial-score-replays-v5/copilot-2026-09-29-01-receipt.json"
SESSION = "1f2f8ef7-577e-4ca1-946e-cc056cd4a870"
SOURCE = CAPTURE / "isolated-home/.copilot/session-state" / SESSION
CAPTURE_DOCUMENTS = (
    "attempt.json", "qualification.json", "workload_instance.json", "workload_template.json",
    "capture/r1.stdout", "capture/r2.stdout", "capture/r1.stderr", "capture/r2.stderr",
    "filesystem/initial-state.json", "filesystem/after-state.json",
    "workspace/fixture_project/.survival-observer.jsonl",
)
SOURCE_HASHES = {
    ".workspace-fork.lock": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "checkpoints/index.md": "a6508da52c083afcfd134779a5b6a71a31235f9f154f2aa865ea920d68189734",
    "events.jsonl": "4c9e1357f508bb26b963c51c624479b45eae3308b3203387635597232a72d0a7",
    "rewind-file-snapshots/backups/f4482bd25089ad5c521d2d8c75d17607d5dec0582b9bb687b5716c9673546401": "f4482bd25089ad5c521d2d8c75d17607d5dec0582b9bb687b5716c9673546401",
    "rewind-file-snapshots/index.json": "33f0be3ddff80f4e79f925826453d56a2e5418bb4e5acb78737c4264f56eec91",
    "rewind-file-snapshots/tracking.json": "0916b582e1bc5439b7454e8dee676b3d7f24d524cac8715e1d0afd44a8e66e3b",
    "workspace.yaml": "fb3a0ca542e53450efb286a967edcd0219781ab772b84ac38f0e1d708db16e98",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_documents() -> dict[str, bytes]:
    if SOURCE.is_symlink() or not SOURCE.is_dir():
        raise ValueError("original Copilot source directory missing or linked")
    files = list(SOURCE.rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ValueError("original Copilot source contains a symlink")
    names = {path.relative_to(SOURCE).as_posix() for path in files if path.is_file()}
    if names != set(SOURCE_HASHES):
        raise ValueError("original Copilot source family differs from pinned inventory")
    documents = {name: (SOURCE / name).read_bytes() for name in SOURCE_HASHES}
    if {name: sha(data) for name, data in documents.items()} != SOURCE_HASHES:
        raise ValueError("original Copilot source hash mismatch")
    copied = CAPTURE / "native" / SESSION / "events.jsonl"
    if copied.read_bytes() != documents["events.jsonl"]:
        raise ValueError("original Copilot events differ from captured copy")
    return documents


def build(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("successor output must be new")
    native_documents = source_documents()
    documents = {name: (CAPTURE / name).read_bytes() for name in CAPTURE_DOCUMENTS}
    attempt = json.loads(documents["attempt.json"])
    workload = json.loads(documents["workload_instance.json"])
    if attempt["session_id"] != SESSION or workload["run_id"] != "copilot-live-2026-09-29-01":
        raise ValueError("capture 01 identity mismatch")
    for name, data in native_documents.items():
        documents[f"native/{SESSION}/{name}"] = data
    observer_bytes = canonical(observer_from_capture_documents(documents))
    inventory = {"artifacts": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                               for name, data in sorted(native_documents.items())]}
    inventory_bytes = canonical(inventory) + b"\n"
    assertion = {"schema_version": ASSERTION_SCHEMA, "run_id": workload["run_id"],
                 "repetition": 1, "native_inventory_sha256": sha(inventory_bytes),
                 "input_sha256": {"workload.json": sha(documents["workload_instance.json"]),
                                  "observer.json": sha(observer_bytes)},
                 "capture_documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                       for name, data in sorted(documents.items())]}
    supporting = {"capture-assertion.json": canonical(assertion) + b"\n"}
    supporting.update({"capture/" + name: data for name, data in documents.items()})
    context = {"schema_version": SCHEMA, "configuration_id": "copilot", "repetition": 1,
               "run_id": workload["run_id"], "build": attempt["cli_version"], "collected_on": "2026-09-29",
               "result_id": "copilot-2026-09-29-01-original-source-snapshot-private-replay",
               "observer_kind": "copilot-capture-v1", "complete_record_family": False,
               "complete_root": False, "required_companions": [], "root_repetitions": None,
               "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="bench-copilot-source-", dir=output) as directory:
        native = Path(directory)
        (native / "decode.json").write_bytes(inventory_bytes)
        for name, data in native_documents.items():
            target = native / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        packet = output / "copilot-2026-09-29-01"
        manifest = build_score_replay_package(native, packet, workload_document=documents["workload_instance.json"],
            observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
    pinned = sha((packet / "manifest.json").read_bytes())
    receipt = replay_score_package(packet, expected_manifest_sha256=pinned)
    intact = receipt["diagnostics"]["intact"]
    old = json.loads(V5_RECEIPT.read_bytes())["diagnostics"]["intact"]
    metrics = {row["id"]: row for row in intact["metrics"]}
    old_metrics = {row["id"]: row for row in old["metrics"]}
    if len(metrics) != 31 or metrics.keys() != old_metrics.keys():
        raise ValueError("unexpected Copilot metric inventory")
    if any(metrics[key] != old_metrics[key] for key in metrics.keys() - {"work.changed_files"}):
        raise ValueError("successor changed a metric outside work.changed_files")
    if old_metrics["work.changed_files"]["state"] != "unresolved" or metrics["work.changed_files"]["state"] != "measured" or metrics["work.changed_files"]["correct"] != 1:
        raise ValueError("source snapshot did not resolve exactly the changed-file metric")
    if intact["score_diagnostics"]["rankable"] or intact["score_diagnostics"]["overall"] is not None or receipt["public_safe"]:
        raise ValueError("private partial replay unexpectedly qualified for a score")
    tamper = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
    if tamper["status"] != "passed":
        raise ValueError("successor tamper controls failed")
    (output / "copilot-2026-09-29-01-receipt.json").write_bytes(canonical(receipt) + b"\n")
    result = {"schema_version": "session-bench-copilot-01-source-snapshot-private-successor-v1",
              "scope": "one_private_changed_file_metric", "run_id": workload["run_id"],
              "source_root": SOURCE.relative_to(ROOT).as_posix(), "source_hashes": SOURCE_HASHES,
              "copied_events_equal_original_source": True, "complete_record_family": False,
              "complete_root": False, "public_safe": False, "independent_reproduction": False,
              "overall_rank": None, "metric_count": len(metrics), "resolved_metric_count": 18,
              "newly_resolved_metric_ids": ["work.changed_files"], "manifest_sha256": pinned,
              "diagnostics_sha256": manifest["expected_diagnostics_sha256"], "tamper_controls": tamper["status"]}
    (output / "summary.json").write_bytes(canonical(result) + b"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2, sort_keys=True))
