#!/usr/bin/env python3
"""Build new private successor packets from twelve retained CLI/Desktop captures."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical
from session_bench.score_replay import SCHEMA, build_score_replay_package, build_dsh_score_replay_package, replay_score_package, verify_score_packet_tamper_controls
from session_bench.workload_instance import instantiate_workload


def retained_inputs(run: Path) -> tuple[Path, dict]:
    """Select explicit historical inputs; never discover a vendor/home root."""
    configuration = next(name for name in ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop") if run.name.startswith(name + "-"))
    repetition = int(run.name.split("-eval-", 1)[1].split("-", 1)[0])
    supporting = {}
    if configuration == "claude-desktop":
        template_bytes = (ROOT / "fixtures/scenarios/survival-v1/workload/workload.json").read_bytes()
        gui_bytes = (run / "capture/observer-private/gui-observer-v2.json").read_bytes()
        gui = json.loads(gui_bytes)
        instance, _ = instantiate_workload(json.loads(template_bytes), run.name)
        for row, revision in zip(instance["turns"], ("r1", "r2")):
            row["text"] = gui["submitted_prompts"][revision]
        instance_bytes = canonical(instance)
        supporting.update({"workload-template.json": template_bytes, "gui-observer.json": gui_bytes})
    else:
        instance_bytes = (run / "workload-instance.json").read_bytes()
        instance = json.loads(instance_bytes)
    context = {"schema_version": SCHEMA, "configuration_id": configuration, "repetition": repetition,
               "run_id": instance["run_id"], "build": "", "collected_on": "2026-09-29",
               "result_id": run.name + "-current-source-score-replay", "observer_kind": "canonical",
               "complete_record_family": True, "complete_root": False, "required_companions": [],
               "root_repetitions": None, "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
    if configuration == "codex-cli":
        requalified = run / "capture/requalification-v1"
        native = requalified / "capture/canonical-private"
        assertion_bytes = (requalified / "requalification-receipt.json").read_bytes()
        assertion = json.loads(assertion_bytes)
        prior_format = json.loads((requalified / "capture/format-evidence.json").read_bytes())
        context.update(build=prior_format["build"], collected_on=prior_format["collected_on"], complete_root=assertion["complete_root"], observer_kind="codex-cli-stdout-v1")
        streams = []
        for number in (1, 2):
            data = (run / f"observer/turn-r{number}.stdout.jsonl").read_bytes()
            receipt = (run / f"capture/observer-r{number}.json").read_bytes()
            supporting[f"stdout-r{number}.jsonl"] = data
            supporting[f"stdout-r{number}-receipt.json"] = receipt
            streams.append({"path": f"inputs/stdout-r{number}.jsonl", "sha256": hashlib.sha256(data).hexdigest(), "receipt_path": f"inputs/stdout-r{number}-receipt.json"})
        observer_bytes = canonical({"schema_version": "session-bench-codex-stdout-observer-v1", "run_id": instance["run_id"], "independent": True, "streams": streams})
    elif configuration == "codex-desktop":
        captured_format_bytes = (run / "capture/format-evidence-qualified.json").read_bytes()
        captured_format = json.loads(captured_format_bytes)
        native = run / Path(captured_format["native_manifest"]["id"]).parent
        assertion_bytes = (run / "attempt.json").read_bytes()
        observer_bytes = (run / "capture/observer.json").read_bytes()
        if hashlib.sha256(observer_bytes).hexdigest() != captured_format["observer"]["sha256"]:
            raise ValueError("Desktop task observer differs from retained binding")
        context.update(build=captured_format["build"], collected_on=captured_format["collected_on"], observer_kind="codex-desktop-task-api-v1", required_companions=["shell_snapshot.sh", "desktop_family.json"])
        supporting["captured-format-binding.json"] = captured_format_bytes
        for number in (1, 2):
            supporting[f"prompt-r{number}.txt"] = (run / f"prompt-r{number}.txt").read_bytes()
    elif configuration == "claude-desktop":
        finalized = run / "capture/finalized-private-v1"
        native = finalized / "native-package"
        assertion_bytes = (run / "capture/qualified-private-v1/native-manifest-qualified.json").read_bytes()
        observer_bytes = (run / "capture/qualified-private-v1/observer-qualified.json").read_bytes()
        captured_format = json.loads((finalized / "format-evidence.json").read_bytes())
        desktop = json.loads((finalized / "native-family-private/desktop/session.json").read_bytes())
        context.update(build=captured_format["build"], collected_on=captured_format["collected_on"], complete_root=True,
                       claude_projection={"workspace": desktop["cwd"], "usage_mode": "none"})
        supporting["family-validation.json"] = (finalized / "family-validation.json").read_bytes()
        for path in ("transcript/session.jsonl", "desktop/session.json"):
            supporting["native-family/" + path] = (finalized / "native-family-private" / path).read_bytes()
    else:
        native = run / "native-bundle"
        assertion_bytes = (run / "native-manifest-qualified.json").read_bytes()
        attempt_bytes = (run / "attempt.json").read_bytes()
        attempt = json.loads(attempt_bytes)
        observer_bytes = (run / "observer.json").read_bytes()
        if hashlib.sha256(observer_bytes).hexdigest() != attempt["observer"]["sha256"]:
            raise ValueError("retained observer differs from captured attempt digest")
        context.update(build=attempt["claude_version"], collected_on=attempt["completed_at"][:10], complete_root=True,
                       claude_projection={"workspace": attempt["project_root"], "usage_mode": "full"})
        supporting["attempt.json"] = attempt_bytes
    supporting["capture-assertion.json"] = assertion_bytes
    return native, {"workload_document": instance_bytes, "observer_document": observer_bytes,
                    "context_document": canonical(context), "supporting_documents": supporting}


def build_retained(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("successor artifact root must be new")
    rows = []
    for configuration in ("codex-cli", "claude-cli", "codex-desktop", "claude-desktop"):
        for repetition in (1, 2, 3):
            name = f"{configuration}-eval-{repetition}"
            if configuration == "claude-desktop" and repetition in (1, 2):
                name += "-correction-1"
            run = ROOT / "artifacts/survival-v1-runs" / name
            native, inputs = retained_inputs(run)
            packet = output / run.name
            manifest = build_score_replay_package(native, packet, **inputs)
            digest = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
            receipt = replay_score_package(packet, expected_manifest_sha256=digest)
            receipt["tamper_controls"] = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=digest)
            (output / f"{run.name}-receipt.json").write_bytes(canonical(receipt) + b"\n")
            intact = receipt["diagnostics"]["intact"]
            rows.append({"run_id": intact["run_id"], "configuration_id": configuration, "repetition": repetition,
                         "packet": packet.name, "manifest_sha256": digest, "diagnostics_sha256": manifest["expected_diagnostics_sha256"],
                         "metric_count": len(intact["metrics"]), "unresolved_metric_ids": [row["id"] for row in intact["metrics"] if row["state"] in {"unresolved", "decoder_unsupported", "invalid_capture"}],
                         "selected_loss_detected": True, "independent_reproduction": False})
    summary = {"schema_version": SCHEMA, "scope": "private_native_to_score_diagnostics", "public_safe": False, "independent_reproduction": False,
               "historical_inputs_overwritten": False, "runs": rows}
    (output / "summary.json").write_bytes(canonical(summary) + b"\n")
    return summary


def retained_dsh_inputs(run: Path, repetition: int) -> tuple[Path, dict]:
    """Use the explicit v5 observer correction, without reusing its scores."""
    source = run / "qualification-v5"
    state_bytes = (source / "capture-result.json").read_bytes()
    state = json.loads(state_bytes)
    if state.get("status") != "captured_pending_qualification" or state.get("attempt_id") != run.name:
        raise ValueError("DSH input is not a completed selected captured attempt")
    native = source / "native/session.v4.jsonl.zstd"
    assertion_bytes = (source / "manifest.json").read_bytes()
    assertion = json.loads(assertion_bytes)
    if assertion["repetition"] != repetition:
        raise ValueError("DSH declared repetition differs from selected input")
    context = {"schema_version": SCHEMA, "configuration_id": "deepseek-harness-cli", "repetition": repetition,
               "run_id": run.name, "build": state["version"], "collected_on": state["started_at"][:10],
               "result_id": run.name + "-current-source-score-replay", "observer_kind": "canonical",
               "complete_record_family": True, "complete_root": True, "required_companions": [],
               "root_repetitions": None, "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
    supporting = {"capture-assertion.json": assertion_bytes, "captured-portability.json": (source / "portability.json").read_bytes(),
                  "capture-result.json": state_bytes, "bench_check.py": (source / "bench_check.py").read_bytes(),
                  "helper-ledger.jsonl": (source / "helper-ledger.jsonl").read_bytes(),
                  "r1.stdout.jsonl": (source / "r1.stdout.jsonl").read_bytes(), "r2.stdout.jsonl": (source / "r2.stdout.jsonl").read_bytes()}
    return native, {"workload_document": (source / "workload.json").read_bytes(), "observer_document": (source / "observer.json").read_bytes(),
                    "context_document": canonical(context), "supporting_documents": supporting}


def build_retained_dsh(output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("successor artifact root must be new")
    rows = []
    for repetition, name in enumerate(("dsh-cal-20260929-2", "dsh-eval-20260929-1", "dsh-eval-20260929-2"), 1):
        run = ROOT / "artifacts/survival-v1-runs" / name
        native, inputs = retained_dsh_inputs(run, repetition)
        packet = output / name
        manifest = build_dsh_score_replay_package(native, packet, **inputs)
        digest = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=digest)
        receipt["tamper_controls"] = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=digest)
        (output / f"{name}-receipt.json").write_bytes(canonical(receipt) + b"\n")
        intact = receipt["diagnostics"]["intact"]
        rows.append({"run_id": name, "configuration_id": "deepseek-harness-cli", "repetition": repetition, "packet": name,
                     "manifest_sha256": digest, "diagnostics_sha256": manifest["expected_diagnostics_sha256"],
                     "metric_count": len(intact["metrics"]), "selected_loss_detected": True, "independent_reproduction": False,
                     "platform_dependencies": manifest["platform_dependencies"],
                     "unresolved_metric_ids": [row["id"] for row in intact["metrics"] if row["state"] in {"unresolved", "decoder_unsupported", "invalid_capture"}]})
    summary = {"schema_version": SCHEMA, "scope": "private_native_to_score_diagnostics", "public_safe": False,
               "independent_reproduction": False, "historical_inputs_overwritten": False, "runs": rows}
    (output / "summary.json").write_bytes(canonical(summary) + b"\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dsh-only", action="store_true")
    args = parser.parse_args()
    builder = build_retained_dsh if args.dsh_only else build_retained
    print(json.dumps(builder(args.output), sort_keys=True, indent=2))
