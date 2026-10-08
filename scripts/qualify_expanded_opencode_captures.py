#!/usr/bin/env python3
"""Build three closed private native-to-score packets from explicit fresh captures."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.score_replay import SCHEMA, build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls
from session_bench.opencode_score_inputs import ASSERTION_SCHEMA, NATIVE_FILES

def qualify(source, output):
    source, output = Path(source), Path(output)
    if output.exists(): raise ValueError("qualified successor output must be new")
    supporting = {}; roots = []
    for rep in (1, 2, 3):
        run = source / f"opencode-1-18-31-eval-{rep}"
        contents = _snapshot_tree(run)
        selected = {name: data for name, data in contents.items() if name not in {"decoded.json", "collector-inventory.json"}}
        entries = []
        for name, data in sorted(selected.items()):
            path = f"root-proof/{rep}/{name}"; supporting[path] = data
            entries.append({"path": "inputs/" + path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)})
        roots.append({"repetition": rep, "run_id": run.name, "files": entries})
    proof = canonical({"schema_version": "session-bench-opencode-three-fresh-roots-v1", "repetitions": roots})
    supporting["root-proofs.json"] = proof
    root_repetitions = [{"repetition": n, "root_locator": f"isolated-opencode-run-{n}/native/opencode.db", "isolated_discovery": True, "personal_history_scanned": False} for n in (1, 2, 3)]
    output.mkdir()
    rows = []
    for rep in (1, 2, 3):
        run = source / f"opencode-1-18-31-eval-{rep}"
        workload = (run / "workload.json").read_bytes(); observer = (run / "observer.json").read_bytes()
        with tempfile.TemporaryDirectory(prefix="bench-opencode-packet-input-") as temporary:
            native = Path(temporary).resolve()
            artifacts = []
            for name in sorted(NATIVE_FILES):
                data = (run / "native-bundle" / name).read_bytes(); (native / name).write_bytes(data)
                artifacts.append({"id": "native:" + name, "path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []})
            inventory = canonical({"format": "opencode-sqlite-v1", "artifacts": artifacts}) + b"\n"
            (native / "decode.json").write_bytes(inventory)
            assertion = canonical({"schema_version": ASSERTION_SCHEMA, "run_id": run.name, "repetition": rep,
                "native_inventory_sha256": hashlib.sha256(inventory).hexdigest(),
                "input_sha256": {"workload.json": hashlib.sha256(workload).hexdigest(), "observer.json": hashlib.sha256(observer).hexdigest()},
                "root_proofs_sha256": hashlib.sha256(proof).hexdigest()})
            context = {"schema_version": SCHEMA, "configuration_id": "opencode-cli", "repetition": rep, "run_id": run.name,
                "build": "1.18.31", "collected_on": "2026-09-30", "result_id": run.name + "-native-score-replay",
                "observer_kind": "canonical", "complete_record_family": True, "complete_root": True,
                "required_companions": ["opencode.db-wal", "opencode.db-shm"], "root_repetitions": root_repetitions,
                "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
            packet = output / run.name
            manifest = build_score_replay_package(native, packet, workload_document=workload, observer_document=observer,
                context_document=canonical(context), supporting_documents={**supporting, "capture-assertion.json": assertion})
        pin = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
        replay = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        replay["tamper_controls"] = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
        with (output / f"{run.name}.producer-replay.json").open("xb") as file: file.write(canonical(replay) + b"\n")
        intact = replay["diagnostics"]["intact"]
        unresolved = [row["id"] for row in intact["metrics"] if row["state"] in {"unresolved", "decoder_unsupported", "invalid_capture"}]
        row = {"run_id": run.name, "repetition": rep, "packet": packet.name, "manifest_sha256": pin,
               "diagnostics_sha256": manifest["expected_diagnostics_sha256"], "metric_count": len(intact["metrics"]),
               "unresolved_metric_ids": unresolved, "os_sandboxed": replay["os_sandboxed"], "public_safe": False, "independent_reproduction": False}
        rows.append(row); print(json.dumps(row), flush=True)
    summary = {"schema_version": "session-bench-expanded-opencode-qualified-summary-v1", "runs": rows,
               "public_safe": False, "independent_reproduction": False, "publication_eligible": False}
    with (output / "summary.json").open("xb") as file: file.write(canonical(summary) + b"\n")
    return summary

if __name__ == "__main__":
    qualify(ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-live-v1",
            ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-native-score-v1")
