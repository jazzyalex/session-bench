#!/usr/bin/env python3
"""Build the three private Cursor CLI score replay packets from the isolated-directory captures.

Each packet holds the session family of the run (``store.db``, ``meta.json``
and the agent transcript, at their paths inside the isolated directories), the
independent stdout streams, the helper ledger, the file states, the controller
receipts, a builder-made inventory of both isolated directories and the rows of
the session from the shared store of the operator's home (read after the
capture, saved beside the run). The store
is read as bytes only; it is never opened in place. The receipts of the other
two runs travel with each packet for the stable-root rows. Every packet is
replayed under the OS sandbox and passes the tamper controls. The packets are
private: they hold the home user name.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical
from session_bench.cursor_cli_score_inputs import (
    ASSERTION_SCHEMA, CAPTURE_DOCUMENTS, CONTROLLER_SHA256, OBSERVER_KIND, ROOT_DOCUMENTS, ROOT_INDEX_SCHEMA, expected_root_row,
    inventory_isolated_roots, observer_from_capture_documents, qualify_isolated_root,
)
from session_bench.score_replay import (
    SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls,
)

RUNS = ("cursor-cli-2026-10-06-r1", "cursor-cli-2026-10-06-r2", "cursor-cli-2026-10-06-r3")
CAPTURES = ROOT / "artifacts/survival-v1-runs"
# The controller source as it was at capture time (the file changed afterwards).
CONTROLLER_COMMIT, CONTROLLER_PATH = "9049700", "session_bench/cursor_live_capture.py"
RESOLVED = ("measured", "native_absent", "contradiction")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture_controller_source() -> bytes:
    import subprocess
    return subprocess.run(["git", "show", f"{CONTROLLER_COMMIT}:{CONTROLLER_PATH}"], cwd=ROOT, check=True, capture_output=True).stdout


def capture_documents(capture: Path) -> dict[str, bytes]:
    """The closed document set of one run; the root inventory is taken here."""
    state = json.loads((capture / "controller-state.json").read_bytes())
    workload = json.loads((capture / "workload-instance.json").read_bytes())
    inventory = inventory_isolated_roots(capture, session_id=state["session_id"], run_canary=workload["run_canary"])
    documents = {"root-inventory.json": canonical(inventory) + b"\n", "controller-source.py": capture_controller_source()}
    for name in CAPTURE_DOCUMENTS:
        if name not in documents:
            documents[name] = (capture / name).read_bytes()
    if sha(documents["controller-source.py"]) != CONTROLLER_SHA256:
        raise ValueError("controller source differs from the bound capture controller")
    return documents


def build(output: Path, *, captures: Path = CAPTURES, os_sandboxed: bool = True) -> dict:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError("new destination required")
    documents_by_run = {run: capture_documents(captures / run) for run in RUNS}
    qualified = {run: qualify_isolated_root({name: documents_by_run[run][name] for name in ROOT_DOCUMENTS}) for run in RUNS}
    output.mkdir(parents=True)
    summary = []
    for repetition, run in enumerate(RUNS, 1):
        capture, documents, fresh = captures / run, documents_by_run[run], qualified[run]
        workload = json.loads(documents["workload-instance.json"])
        native_documents = {name: (capture / name).read_bytes() for name in fresh["family"]}
        if {name: sha(data) for name, data in native_documents.items()} != fresh["family"]:
            raise ValueError("session family differs from the controller manifest")
        observer_bytes = canonical(observer_from_capture_documents(documents))
        inventory = {"artifacts": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                   for name, data in sorted(native_documents.items())]}
        inventory_bytes = canonical(inventory) + b"\n"
        assertion = {"schema_version": ASSERTION_SCHEMA, "run_id": workload["run_id"], "repetition": repetition,
                     "native_inventory_sha256": sha(inventory_bytes),
                     "input_sha256": {"workload.json": sha(documents["workload-instance.json"]), "observer.json": sha(observer_bytes)},
                     "capture_documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                           for name, data in sorted(documents.items())]}
        supporting = {"capture-assertion.json": canonical(assertion) + b"\n"}
        supporting.update({"capture/" + name: data for name, data in documents.items()})
        index = {"schema_version": ROOT_INDEX_SCHEMA, "runs": []}
        for number, other in enumerate(RUNS, 1):
            receipts = {name: documents_by_run[other][name] for name in ROOT_DOCUMENTS}
            index["runs"].append({"repetition": number, "run_id": qualified[other]["run_id"], "session_id": qualified[other]["session_id"],
                                  "documents": [{"path": name, "sha256": sha(data), "size_bytes": len(data)}
                                                for name, data in sorted(receipts.items())]})
            if number != repetition:
                supporting.update({f"root-repetitions/repetition-{number}/{name}": data for name, data in receipts.items()})
        supporting["root-repetitions.json"] = canonical(index) + b"\n"
        store = next(name for name in native_documents if name.endswith("/store.db"))
        created = json.loads(native_documents[store[:-len("store.db")] + "meta.json"])["createdAtMs"]
        context = {"schema_version": SCHEMA, "configuration_id": "cursor-cli", "repetition": repetition,
                   "run_id": workload["run_id"], "build": fresh["build"],
                   "collected_on": datetime.fromtimestamp(created / 1000, tz=timezone.utc).date().isoformat(),
                   "result_id": run + "-native-score-replay", "observer_kind": OBSERVER_KIND,
                   "complete_record_family": True, "complete_root": False,
                   "required_companions": sorted(set(native_documents) - {store}),
                   "root_repetitions": [expected_root_row(number) for number in (1, 2, 3)],
                   "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
        with tempfile.TemporaryDirectory(prefix="bench-cursor-native-") as directory:
            native = Path(directory).resolve()
            (native / "decode.json").write_bytes(inventory_bytes)
            for name, data in native_documents.items():
                target = native / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            packet = output / run
            manifest = build_score_replay_package(native, packet, workload_document=documents["workload-instance.json"],
                observer_document=observer_bytes, context_document=canonical(context), supporting_documents=supporting)
        pinned = sha((packet / "manifest.json").read_bytes())
        receipt = replay_score_package(packet, expected_manifest_sha256=pinned, os_sandboxed=os_sandboxed)
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pinned)
        if controls["status"] != "passed":
            raise ValueError("Cursor packet tamper controls failed")
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
    result = {"schema_version": "session-bench-cursor-cli-score-replay-v1", "scope": "private_native_to_score_diagnostics",
              "public_safe": False, "independent_reproduction": False, "overall_rank": None, "runs": summary}
    (output / "summary.json").write_bytes(canonical(result) + b"\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), indent=2, sort_keys=True))
