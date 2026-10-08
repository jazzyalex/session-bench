#!/usr/bin/env python3
"""Build the three private Copilot CLI score replay packets from the fresh-root captures.

Each packet holds the whole copied session directory, the session store
(``session-store.db`` and ``session-store.db-wal``, retrieved from the retained
root and equal to the root-end receipt hashes), the independent stdout
streams, the helper ledger, the filesystem states and the fresh-root receipts.
The store files are read as bytes only; they are never opened in place.
The receipts of the other two runs travel with each packet for the stable-root
rows. Every packet is replayed under the OS sandbox and passes the tamper
controls. The packets are private: they hold the home user name.
"""
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
from session_bench.copilot_score_inputs import (
    ASSERTION_SCHEMA, ROOT_DOCUMENTS, ROOT_INDEX_SCHEMA, STORE_RECEIPT, expected_root_row, observer_from_capture_documents,
)
from session_bench.copilot_session_store import STORE_FILES
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ("copilot-2026-10-04-01", "copilot-2026-10-04-02", "copilot-2026-10-04-03")
CAPTURES = ROOT / "artifacts/v1-expanded-preparation/live-captures"
COLLECTED_ON = "2026-10-04"
CAPTURE_DOCUMENTS = (
    "attempt.json", "qualification.json", "workload_instance.json", "workload_template.json",
    "capture-start.json", "root-start.json", "root-end.json", "controller-source.py",
    "capture/r1.stdout", "capture/r2.stdout", "capture/r1.stderr", "capture/r2.stderr",
    "capture/r1.launch.json", "capture/r2.launch.json", "capture/r1.exit.json", "capture/r2.exit.json",
    "filesystem/initial-state.json", "filesystem/after-state.json",
    "workspace/fixture_project/.survival-observer.jsonl",
)
RESOLVED = ("measured", "native_absent", "contradiction")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(output: Path, *, captures: Path = CAPTURES, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("new destination required")
    receipts = {run: {name: (captures / run / name).read_bytes() for name in ROOT_DOCUMENTS} for run in RUNS}
    attempts = {run: json.loads(receipts[run]["attempt.json"]) for run in RUNS}
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(RUNS, 1):
        capture = captures / run
        documents = {name: (capture / name).read_bytes() for name in CAPTURE_DOCUMENTS}
        attempt = attempts[run]
        workload = json.loads(documents["workload_instance.json"])
        session = attempt["session_id"]
        native_root = capture / "native" / session
        native_documents = {path.relative_to(native_root).as_posix(): path.read_bytes()
                            for path in native_root.rglob("*") if path.is_file() and not path.is_symlink()}
        if "events.jsonl" not in native_documents:
            raise ValueError("selected Copilot native events missing")
        for name, data in native_documents.items():
            documents["native/" + session + "/" + name] = data
        # The session store of this run, bound to the root-end receipt.
        inventory_after = json.loads(documents["root-end.json"])["copilot_home_inventory_after"]
        documents[STORE_RECEIPT] = (capture / STORE_RECEIPT).read_bytes()
        for name in STORE_FILES:
            data = (capture / "native-store" / name).read_bytes()
            if sha(data) != inventory_after[name]["sha256"]:
                raise ValueError("retrieved session store differs from the root-end receipt")
            documents["native-store/" + name] = data
            native_documents[name] = data
        observer_bytes = canonical(observer_from_capture_documents(documents))
        inventory = {"artifacts": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                   for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(inventory) + b"\n"
        assertion = {"schema_version": ASSERTION_SCHEMA, "run_id": workload["run_id"], "repetition": repetition,
                     "native_inventory_sha256": sha(inventory_bytes),
                     "input_sha256": {"workload.json": sha(documents["workload_instance.json"]),
                                      "observer.json": sha(observer_bytes)},
                     "capture_documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                           for name, data in sorted(documents.items())]}
        supporting = {"capture-assertion.json": canonical(assertion) + b"\n"}
        supporting.update({"capture/" + name: data for name, data in documents.items()})
        # The receipts of all three fresh roots bind the stable-root rows.
        index = {"schema_version": ROOT_INDEX_SCHEMA, "runs": []}
        for number, other in enumerate(RUNS, 1):
            index["runs"].append({"repetition": number, "run_id": attempts[other]["run_id"],
                                  "session_id": attempts[other]["session_id"],
                                  "documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                                for name, data in sorted(receipts[other].items())]})
            if number != repetition:
                supporting.update({f"root-repetitions/repetition-{number}/{name}": data
                                   for name, data in receipts[other].items()})
        supporting["root-repetitions.json"] = canonical(index) + b"\n"
        context = {"schema_version": SCHEMA, "configuration_id": "copilot", "repetition": repetition,
                   "run_id": workload["run_id"], "build": attempt["cli_version"], "collected_on": COLLECTED_ON,
                   "result_id": run + "-native-score-replay", "observer_kind": "copilot-capture-v1",
                   "complete_record_family": True, "complete_root": True,
                   "required_companions": sorted(set(native_documents) - {"events.jsonl"}),
                   "root_repetitions": [expected_root_row(number) for number in (1, 2, 3)],
                   "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
        with tempfile.TemporaryDirectory(prefix="bench-copilot-native-") as directory:
            native = Path(directory).resolve()
            (native / "decode.json").write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(native, packet, workload_document=documents["workload_instance.json"],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / "manifest.json").read_bytes())
        receipt = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls["status"] != "passed":
            raise ValueError("Copilot packet tamper controls failed")
        (output / f"{run}-receipt.json").write_bytes(canonical(receipt) + b"\n")
        (output / f"{run}-tamper.json").write_bytes(canonical(controls) + b"\n")
        metrics = receipt["diagnostics"]["intact"]["metrics"]
        unresolved = [row["id"] for row in metrics if row["state"] not in RESOLVED]
        summary.append({"run_id": workload["run_id"], "repetition": repetition, "packet": packet.name,
                        "manifest_sha256": pinned, "diagnostics_sha256": manifest["expected_diagnostics_sha256"],
                        "metric_count": len(metrics), "resolved_metric_count": len(metrics) - len(unresolved),
                        "unresolved_metric_ids": unresolved, "os_sandboxed": receipt["os_sandboxed"],
                        "tamper_controls": controls["status"],
                        "selected_loss_detected": receipt["diagnostics"]["selected_loss"]["response_correctness_reduced"]})
    result = {"schema_version": "session-bench-copilot-score-replay-v1", "scope": "private_native_to_score_diagnostics",
              "public_safe": False, "independent_reproduction": False, "overall_rank": None, "runs": summary}
    (output / "summary.json").write_bytes(canonical(result) + b"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
