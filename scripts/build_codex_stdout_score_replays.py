#!/usr/bin/env python3
"""Build additive private Codex CLI packets with independent stdout-v2 observer.

No live model calls, native acquisition, historical edits, privacy approval or
independent reproduction claim. Usage, model identity and reconciliation are
scored from the native rollout; the stdout usage totals are not compared.
Each packet carries the three retained metadata-only root receipts.
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
from build_retained_score_replays import retained_inputs
from session_bench.codex_cli_root_evidence import FILES, SOURCES, verify_codex_cli_root_evidence
from session_bench.native_replay import canonical
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls


def build(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("successor output must be new")
    root_evidence = {}
    with tempfile.TemporaryDirectory(prefix="codex-root-proof-") as folder:
        for number in (1, 2, 3):
            for name in FILES:
                data = (ROOT / f"artifacts/survival-v1-runs/codex-cli-eval-{number}" / SOURCES[name]).read_bytes()
                target = Path(folder) / f"repetition-{number}" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                root_evidence[f"root-evidence/repetition-{number}/{name}"] = data
        root_repetitions = verify_codex_cli_root_evidence(Path(folder))
    rows = []
    for number in (1, 2, 3):
        run = ROOT / f"artifacts/survival-v1-runs/codex-cli-eval-{number}"
        native, inputs = retained_inputs(run)
        context = json.loads(inputs["context_document"])
        context.update(observer_kind="codex-cli-stdout-v2", result_id=run.name + "-independent-stdout-v2-score-replay",
                       root_repetitions=root_repetitions)
        inputs["context_document"] = canonical(context)
        documents = inputs["supporting_documents"]
        documents.update(root_evidence)
        for name, source in {"helper-ledger.jsonl": "project/fixture_project/.survival-observer.jsonl",
                             "checkout.before.py": "project/fixture_project/snapshots/checkout.before.py",
                             "checkout.after.py": "project/fixture_project/checkout.py",
                             "bench_check.py": "project/fixture_project/bench_check.py",
                             "original-capture-receipt.json": "capture/calibration-receipt.json"}.items():
            documents[name] = (run / source).read_bytes()
        packet = output / run.name
        manifest = build_score_replay_package(native, packet, **inputs)
        pin = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        tamper = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
        (output / f"{run.name}-receipt.json").write_bytes(canonical(receipt) + b"\n")
        (output / f"{run.name}-tamper.json").write_bytes(canonical(tamper) + b"\n")
        intact = receipt["diagnostics"]["intact"]
        unresolved = [row["id"] for row in intact["metrics"] if row["state"] in {"unresolved", "decoder_unsupported", "invalid_capture"}]
        rows.append({"run_id": run.name, "repetition": number, "manifest_sha256": pin,
                     "diagnostics_sha256": manifest["expected_diagnostics_sha256"], "metric_count": len(intact["metrics"]),
                     "resolved_metrics": len(intact["metrics"]) - len(unresolved), "unresolved_metric_ids": unresolved,
                     "unobserved_primary_metric_ids": intact["unobserved_primary_metric_ids"]})
    summary = {"schema_version": "session-bench-codex-stdout-successor-v1", "public_safe": False,
               "independent_reproduction": False, "historical_inputs_overwritten": False, "runs": rows}
    (output / "summary.json").write_bytes(canonical(summary) + b"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), sort_keys=True, indent=2))
