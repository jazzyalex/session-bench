"""Private timestamp closure for the retained Codex CLI stdout captures.

The observer fixes the required event population.  The native decoder supplies
event identities and exact rollout locators.  Timestamp values are read from
those native lines only; stdout times and nearby native records are never used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .adapters.codex_cli_decoder import decode_codex_cli_bundle
from .native_replay import canonical
from .score_replay import _observer, _workload
from .v1_public_score import score_public_control_run, validate_format_evidence


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _timestamp(path: Path, fact: dict[str, Any], lines: list[bytes]) -> str:
    locator = fact.get("locator")
    if not isinstance(locator, dict) or not isinstance(locator.get("line"), int):
        raise ValueError("native event has no exact line locator")
    number = locator["line"]
    if number < 1 or number > len(lines):
        raise ValueError("native event line is outside rollout")
    raw = lines[number - 1]
    record_bytes = raw[:-1] if raw.endswith(b"\n") else raw
    if (hashlib.sha256(record_bytes).hexdigest() != locator.get("record_sha256")
            or locator.get("artifact_sha256") != _sha(path)):
        raise ValueError("native event locator digest mismatch")
    record = json.loads(raw)
    value = record.get("timestamp")
    if not isinstance(value, str) or not value:
        raise ValueError("native event lacks its own timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("native event timestamp has no offset")
    return value


def build_codex_cli_timestamp_diagnostic(repository: Path) -> dict[str, Any]:
    root = repository.resolve() / "artifacts/v1-expanded-preparation/codex-stdout-score-replay-v2"
    root_proof_path = repository.resolve() / "artifacts/v1-expanded-preparation/codex-cli-root-private-diagnostic-v1/diagnostic.json"
    root_proof = _read(root_proof_path)
    if root_proof.get("metric_closure", {}).get("metric_id") != "broad.stable_root_location":
        raise ValueError("retained root diagnostic missing")
    output_rows: list[dict[str, Any]] = []
    for repetition in (1, 2, 3):
        run_id = f"codex-cli-eval-{repetition}"
        packet = root / run_id
        manifest_path = packet / "manifest.json"
        receipt_path = root / f"{run_id}-receipt.json"
        manifest, receipt = _read(manifest_path), _read(receipt_path)
        intact = receipt["diagnostics"]["intact"]
        if (manifest.get("configuration_id") != "codex-cli"
                or manifest.get("repetition") != repetition
                or receipt.get("manifest_sha256") != _sha(manifest_path)
                or intact.get("run_id") != run_id):
            raise ValueError(f"{run_id}: replay identity is unbound")
        root_binding = next((row for row in root_proof.get("bindings", []) if row.get("run_id") == run_id), None)
        if not root_binding or root_binding.get("replay_manifest_sha256") != _sha(manifest_path):
            raise ValueError(f"{run_id}: root diagnostic is not bound to replay")
        native_relative = root_binding["selected_native_path"]
        native_path = packet / native_relative
        if _sha(native_path) != root_binding["selected_native_sha256"]:
            raise ValueError(f"{run_id}: native rollout digest changed")
        if not any(row.get("path") == native_relative and row.get("sha256") == _sha(native_path)
                   for row in manifest.get("files", [])):
            raise ValueError(f"{run_id}: native rollout is absent from manifest")

        context = _read(packet / "inputs/context.json")
        instance = _read(packet / "inputs/workload.json")
        observer_bytes, _missing = _observer(packet, instance, context)
        if hashlib.sha256(observer_bytes).hexdigest() != intact["observer_sha256"]:
            raise ValueError(f"{run_id}: frozen independent observer differs")
        observer = json.loads(observer_bytes)
        required = {row["id"]: row for row in observer["events"] if row["population_role"] == "primary_scored"
                    and row["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"}}
        if len(required) != 13:
            raise ValueError(f"{run_id}: expected exactly 13 independently observed events")
        metric_states = {row["id"]: row for row in intact["metrics"]}
        for metric in ("work.submitted_turns", "work.visible_responses", "work.actions", "work.results"):
            if metric_states[metric]["state"] != "measured" or metric_states[metric]["correct"] != metric_states[metric]["observed_eligible"]:
                raise ValueError(f"{run_id}: {metric} identity join is not complete")
        if metric_states["broad.event_timestamps"]["state"] != "unresolved":
            raise ValueError(f"{run_id}: timestamp predecessor is not unresolved")
        workload = _workload(instance)
        decoded = decode_codex_cli_bundle(packet / "native", workload=workload,
                                           complete_root=context["complete_root"],
                                           required_companions=context["required_companions"],
                                           configuration_id="codex-cli", repetition=repetition)
        facts = decoded["facts"]
        selected: dict[str, dict[str, Any]] = {}
        for family in ("submitted_turns", "visible_responses", "actions", "results"):
            for fact in facts[family]:
                if fact.get("state") != "present":
                    continue
                if family == "submitted_turns":
                    matched = [row for row in required.values() if row["kind"] == "user_turn"
                               and row["fields"].get("turn_id") == fact.get("turn_id")
                               and row["fields"].get("text") == fact.get("text")]
                elif family == "visible_responses":
                    matched = [row for row in required.values() if row["kind"] == "assistant_response"
                               and row["fields"].get("turn_id") == fact.get("turn_id")
                               and row["fields"].get("text") == fact.get("text")]
                else:
                    matched = [row for row in required.values() if row["kind"] == ("action" if family == "actions" else "result")
                               and row["id"] == fact.get("id")]
                if len(matched) == 1:
                    event_id = matched[0]["id"]
                    if event_id in selected:
                        raise ValueError(f"{run_id}: duplicate native event {event_id}")
                    selected[event_id] = fact
        changes = facts["changed_files"]
        observed_changes = [row for row in required.values() if row["kind"] == "file_change"]
        edit = selected.get("action-edit")
        if (len(changes) != 1 or len(observed_changes) != 1 or edit is None
                or changes[0].get("id") != edit.get("native_file_change_id")
                or changes[0].get("paths") != [observed_changes[0]["fields"].get("path")]
                or observed_changes[0]["fields"].get("action_id") != "action-edit"):
            raise ValueError(f"{run_id}: native file change is not uniquely joined")
        selected[observed_changes[0]["id"]] = changes[0]
        if set(selected) != set(required):
            raise ValueError(f"{run_id}: required native event population is incomplete")
        lines = native_path.read_bytes().splitlines(keepends=True)
        records = [{"id": event_id, "timestamp": _timestamp(native_path, selected[event_id], lines),
                    "unit": "rfc3339", "time_zone": "native RFC3339 offset"}
                   for event_id in required]
        successor = deepcopy(intact["format_evidence"])
        successor["profile"]["broad_evidence"]["broad.event_timestamps"] = {
            "evidence_complete": True, "event_ids": list(required), "records": records,
        }
        validate_format_evidence(successor)
        before = score_public_control_run(intact["measurement"], intact["format_evidence"]["profile"])
        after = score_public_control_run(intact["measurement"], successor["profile"])
        changed = {key for key in before.metrics if before.metrics[key] != after.metrics[key]}
        if changed != {"broad.event_timestamps"} or after.metrics["broad.event_timestamps"] != 1:
            raise ValueError(f"{run_id}: timestamp proof changed unexpected score")
        output_rows.append({"run_id": run_id, "repetition": repetition, "event_count": len(records),
                            "event_ids": list(required), "native_rollout_sha256": _sha(native_path),
                            "replay_manifest_sha256": _sha(manifest_path), "replay_receipt_sha256": _sha(receipt_path),
                            "observer_sha256": intact["observer_sha256"],
                            "native_witnesses": [{"event_id": event_id,
                                                  "native_record_location": selected[event_id]["locator"]["record_location"],
                                                  "native_record_sha256": selected[event_id]["locator"]["record_sha256"]}
                                                 for event_id in required]})
    return {"schema_version": "session-bench-codex-cli-timestamp-private-diagnostic-v1",
            "configuration_id": "codex-cli", "scope": "private_additive_event_timestamps_only",
            "public_safe": False, "independent_reproduction": False, "rankable": False,
            "historical_inputs_overwritten": False,
            "root_diagnostic_sha256": _sha(root_proof_path),
            "metric_closure": {"metric_id": "broad.event_timestamps", "predecessor_state": "unresolved",
                               "successor_state": "measured", "score_fraction": "1/1"},
            "repetitions": output_rows,
            "limitations": ["The retained Codex CLI packets remain private and non-rankable.",
                            "This diagnostic does not change the separate file-hash or usage metrics."]}


def write_codex_cli_timestamp_diagnostic(repository: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValueError("diagnostic output must be new")
    result = build_codex_cli_timestamp_diagnostic(repository)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(result) + b"\n")
    return result
