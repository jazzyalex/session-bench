#!/usr/bin/env python3
"""Build additive Copilot diagnostics from three retained, selected captures."""
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
from session_bench.score_replay import SCHEMA, build_score_replay_package, replay_score_package


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CAPTURE_DOCUMENTS = (
    "attempt.json", "qualification.json", "workload_instance.json", "workload_template.json",
    "capture/r1.stdout", "capture/r2.stdout", "capture/r1.stderr", "capture/r2.stderr",
    "filesystem/initial-state.json", "filesystem/after-state.json",
    "workspace/fixture_project/.survival-observer.jsonl",
)


def build(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("successor output must be new")
    output.mkdir(parents=True)
    summary = []
    for repetition, suffix in enumerate(("01", "03", "06"), 1):
        capture = ROOT / f"artifacts/v1-expanded-preparation/live-captures/copilot-2026-09-29-{suffix}"
        documents = {name: (capture / name).read_bytes() for name in CAPTURE_DOCUMENTS}
        attempt = json.loads(documents["attempt.json"])
        if suffix == "06":
            source = ROOT / "artifacts/v1-expanded-preparation/copilot-controller-sources" / (attempt["controller_sha256"] + ".py")
            documents["controller-source.py"] = source.read_bytes()
        workload = json.loads(documents["workload_instance.json"])
        session = attempt["session_id"]
        native_root = capture / "native" / session
        native_documents = {p.relative_to(native_root).as_posix(): p.read_bytes()
                            for p in native_root.rglob("*") if p.is_file() and not p.is_symlink()}
        if "events.jsonl" not in native_documents:
            raise ValueError("selected Copilot native events missing")
        for name, data in native_documents.items():
            documents["native/" + session + "/" + name] = data
        observer_bytes = canonical(observer_from_capture_documents(documents))
        with tempfile.TemporaryDirectory(prefix="bench-copilot-native-", dir=output) as directory:
            native = Path(directory)
            inventory = {"artifacts": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                       for name, data in sorted(native_documents.items())]}
            inventory_bytes = canonical(inventory) + b"\n"
            (native / "decode.json").write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            assertion = {"schema_version": ASSERTION_SCHEMA, "run_id": workload["run_id"],
                         "repetition": repetition, "native_inventory_sha256": sha(inventory_bytes),
                         "input_sha256": {"workload.json": sha(documents["workload_instance.json"]),
                                          "observer.json": sha(observer_bytes)},
                         "capture_documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                               for name, data in sorted(documents.items())]}
            supporting = {"capture-assertion.json": canonical(assertion) + b"\n"}
            supporting.update({"capture/" + name: data for name, data in documents.items()})
            context = {"schema_version": SCHEMA, "configuration_id": "copilot", "repetition": repetition,
                       "run_id": workload["run_id"], "build": attempt["cli_version"], "collected_on": "2026-09-29",
                       "result_id": capture.name + "-partial-current-source-score-replay",
                       "observer_kind": "copilot-capture-v1", "complete_record_family": False,
                       "complete_root": False, "required_companions": [],
                       "root_repetitions": None, "capture_assertion_path": "inputs/capture-assertion.json",
                       "claude_projection": None}
            packet = output / capture.name
            manifest = build_score_replay_package(native, packet, workload_document=documents["workload_instance.json"],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / "manifest.json").read_bytes())
        receipt = replay_score_package(packet, expected_manifest_sha256=pinned)
        (output / f"{capture.name}-receipt.json").write_bytes(canonical(receipt) + b"\n")
        intact = receipt["diagnostics"]["intact"]
        metrics = intact["metrics"]
        summary.append({"run_id": workload["run_id"], "repetition": repetition, "packet": packet.name,
                        "manifest_sha256": pinned, "diagnostics_sha256": manifest["expected_diagnostics_sha256"],
                        "metric_count": len(metrics), "resolved_metric_count": sum(r["state"] not in
                        ("unresolved", "decoder_unsupported", "invalid_capture") for r in metrics),
                        "unresolved_metric_ids": [r["id"] for r in metrics if r["state"] in
                        ("unresolved", "decoder_unsupported", "invalid_capture")],
                        "selected_loss_detected": receipt["diagnostics"]["selected_loss"]["response_correctness_reduced"]})
    result = {"schema_version": "session-bench-copilot-partial-score-successor-v1", "scope": "private_native_to_score_diagnostics",
              "public_safe": False, "independent_reproduction": False, "historical_inputs_overwritten": False,
              "overall_rank": None, "runs": summary}
    (output / "summary.json").write_bytes(canonical(result) + b"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
