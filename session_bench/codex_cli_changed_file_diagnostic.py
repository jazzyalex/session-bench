"""Private Codex CLI changed-file proof from retained independent and native evidence.

The native inspect result supplies the complete pre-edit source. A native
FileChange supplies the exact patch. The independently captured filesystem
snapshots supply the reference hashes. No observer hash is inserted into a
native fact, and neither the decoder nor the release scorer is changed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .native_replay import canonical
from .score_replay import _observer
from .v1_public_score import score_public_control_run


_HUNK = re.compile(r"^@@ -([1-9][0-9]*),([0-9]+) \+([1-9][0-9]*),([0-9]+) @@\n$")


class EvidenceGap(ValueError):
    """A retained source does not support this metric's exact assertion."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise EvidenceGap(f"{path.name}: expected JSON object")
    return value


def _manifest_file(packet: Path, manifest: Mapping[str, Any], relative: str) -> bytes:
    local = Path(relative)
    if local.is_absolute() or ".." in local.parts or not (packet / local).resolve().is_relative_to(packet.resolve()):
        raise EvidenceGap("manifest file path escapes replay packet")
    entry = next((row for row in manifest.get("files", []) if row.get("path") == relative), None)
    if entry is None:
        raise EvidenceGap(f"manifest lacks {relative}")
    path = packet / local
    data = path.read_bytes()
    if entry.get("sha256") != _sha(data) or entry.get("size_bytes") != len(data):
        raise EvidenceGap(f"manifest digest differs for {relative}")
    return data


def _verify_diagnostics_pin(manifest: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    diagnostics = receipt.get("diagnostics")
    if (not isinstance(diagnostics, dict)
            or receipt.get("diagnostics_sha256") != _sha(canonical(diagnostics))
            or manifest.get("expected_diagnostics_sha256") != receipt.get("diagnostics_sha256")):
        raise EvidenceGap("replay diagnostics digest differs from receipt or manifest pin")


def apply_single_native_hunk(before: bytes, diff: str) -> bytes:
    """Apply one exact UTF-8 unified hunk, rejecting ambiguous patch syntax."""
    if not isinstance(diff, str) or not diff.endswith("\n"):
        raise EvidenceGap("native FileChange diff is missing or unterminated")
    lines = diff.splitlines(keepends=True)
    match = _HUNK.fullmatch(lines[0]) if lines else None
    if match is None:
        raise EvidenceGap("native FileChange must contain one explicit unified hunk")
    old_start, old_count, new_start, new_count = map(int, match.groups())
    if old_start != new_start or not lines[1:]:
        raise EvidenceGap("native FileChange hunk has unsupported position")
    old, new = [], []
    for line in lines[1:]:
        if line.startswith(" "):
            old.append(line[1:]); new.append(line[1:])
        elif line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("+"):
            new.append(line[1:])
        else:
            raise EvidenceGap("native FileChange has a second hunk or unsupported marker")
    if len(old) != old_count or len(new) != new_count:
        raise EvidenceGap("native FileChange hunk count differs")
    try:
        source = before.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise EvidenceGap("observed before file is not UTF-8") from exc
    start = old_start - 1
    if source[start:start + old_count] != old:
        raise EvidenceGap("native FileChange old hunk differs from before file")
    return "".join(source[:start] + new + source[start + old_count:]).encode("utf-8")


def _native_witnesses(native: bytes, *, run_id: str, inspect_output: str) -> tuple[bytes, str, dict[str, Any]]:
    inspect: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    changes: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for number, raw in enumerate(native.splitlines(), 1):
        record = json.loads(raw)
        payload = record.get("payload", {})
        item = payload.get("item", {}) if isinstance(payload, dict) else {}
        if record.get("type") != "event_msg" or payload.get("type") != "item_completed" or not isinstance(item, dict):
            continue
        if item.get("type") == "CommandExecution" and inspect_output in item.get("aggregated_output", ""):
            inspect.append((number, record, item))
        if item.get("type") == "FileChange":
            changes.append((number, record, item))
    if len(inspect) != 1 or len(changes) != 1:
        raise EvidenceGap("native inspect or FileChange record is not unique")
    inspect_line, _, inspect_item = inspect[0]
    change_line, _, change_item = changes[0]
    # Eval 2 ran inspect and baseline in one shell command. Inspect emitted
    # its exact source before baseline returned 1, making the combined shell
    # item terminal-failed while its inspect subcommand remained observable.
    if (inspect_line >= change_line or inspect_item.get("status") not in {"completed", "failed"}
            or type(inspect_item.get("exit_code")) is not int
            or change_item.get("status") != "completed"):
        raise EvidenceGap("native inspect/edit order or completion is unproven")
    output = inspect_item["aggregated_output"]
    marker = output.find(inspect_output)
    opening = output.find("{", marker)
    if opening < 0:
        raise EvidenceGap("native inspect has no structured source")
    try:
        parsed, _ = json.JSONDecoder().raw_decode(output[opening:])
    except json.JSONDecodeError as exc:
        raise EvidenceGap("native inspect source is malformed") from exc
    source = parsed.get("checkout_source") if isinstance(parsed, dict) else None
    if not isinstance(source, str) or parsed.get("checkout_sha256") != _sha(source.encode("utf-8")):
        raise EvidenceGap("native inspect source/hash assertion differs")
    native_changes = change_item.get("changes")
    if not isinstance(native_changes, dict) or len(native_changes) != 1:
        raise EvidenceGap("native FileChange has an ambiguous path set")
    path, detail = next(iter(native_changes.items()))
    expected_suffix = f"/{run_id}/project/fixture_project/checkout.py"
    if (not isinstance(path, str) or not path.endswith(expected_suffix)
            or not isinstance(detail, dict) or detail.get("type") != "update" or detail.get("move_path") is not None):
        raise EvidenceGap("native FileChange is not the project-relative checkout update")
    diff = detail.get("unified_diff")
    if not isinstance(diff, str):
        raise EvidenceGap("native FileChange has no diff")
    return source.encode("utf-8"), diff, {
        "inspect_line": inspect_line, "inspect_record_sha256": _sha(native.splitlines()[inspect_line - 1]),
        "change_line": change_line, "change_record_sha256": _sha(native.splitlines()[change_line - 1]),
        "native_change_id": change_item.get("id"), "native_path": path,
    }


def _run(replay_root: Path, repetition: int) -> dict[str, Any]:
    run_id = f"codex-cli-eval-{repetition}"
    packet = replay_root / run_id
    manifest_path = packet / "manifest.json"
    receipt_path = replay_root / f"{run_id}-receipt.json"
    manifest, receipt = _load(manifest_path), _load(receipt_path)
    if (manifest.get("configuration_id") != "codex-cli" or manifest.get("repetition") != repetition
            or receipt.get("manifest_sha256") != _sha(manifest_path.read_bytes())):
        raise EvidenceGap(f"{run_id}: replay identity is unbound")
    _verify_diagnostics_pin(manifest, receipt)
    intact = receipt["diagnostics"]["intact"]
    if intact.get("run_id") != run_id:
        raise EvidenceGap(f"{run_id}: replay diagnostics identity differs")
    before = _manifest_file(packet, manifest, "inputs/checkout.before.py")
    after = _manifest_file(packet, manifest, "inputs/checkout.after.py")
    capture_bytes = _manifest_file(packet, manifest, "inputs/original-capture-receipt.json")
    helper_bytes = _manifest_file(packet, manifest, "inputs/helper-ledger.jsonl")
    capture = json.loads(capture_bytes)
    if (capture.get("run_id") != run_id or capture.get("checkout_before_sha256") != _sha(before)
            or capture.get("checkout_after_sha256") != _sha(after)
            or capture.get("helper_ledger_sha256") != _sha(helper_bytes)):
        raise EvidenceGap(f"{run_id}: independent snapshot hashes differ from capture receipt")
    helpers = [json.loads(line) for line in helper_bytes.splitlines()]
    if [row.get("phase") for row in helpers] != ["inspect", "baseline", "final"]:
        raise EvidenceGap(f"{run_id}: independent helper population differs")
    if (helpers[0].get("checkout_sha256") != _sha(before) or helpers[2].get("checkout_sha256") != _sha(after)
            or not isinstance(helpers[0].get("output"), str)):
        raise EvidenceGap(f"{run_id}: independent helper hashes differ")
    context = json.loads(_manifest_file(packet, manifest, "inputs/context.json"))
    instance = json.loads(_manifest_file(packet, manifest, "inputs/workload.json"))
    observer_bytes, _missing = _observer(packet, instance, context)
    if _sha(observer_bytes) != intact.get("observer_sha256"):
        raise EvidenceGap(f"{run_id}: independent observer differs from replay receipt")
    events = json.loads(observer_bytes)["events"]
    file_changes = [row for row in events if row.get("population_role") == "primary_scored" and row.get("kind") == "file_change"]
    if (len(file_changes) != 1 or file_changes[0].get("id") != "change-checkout"
            or file_changes[0].get("fields") != {"path": "fixture_project/checkout.py", "before_sha256": _sha(before),
                                                    "after_sha256": _sha(after), "action_id": "action-edit"}):
        raise EvidenceGap(f"{run_id}: observer changed-file assertion differs")
    native_entries = [row for row in manifest["files"] if row.get("path", "").startswith("native/") and row.get("path", "").endswith(".jsonl")]
    if len(native_entries) != 1:
        raise EvidenceGap(f"{run_id}: native rollout selection is ambiguous")
    native_entry = native_entries[0]
    native = _manifest_file(packet, manifest, native_entry["path"])
    native_before, diff, witness = _native_witnesses(native, run_id=run_id, inspect_output=helpers[0]["output"])
    if native_before != before:
        raise EvidenceGap(f"{run_id}: native inspect source differs from observer before snapshot")
    if apply_single_native_hunk(native_before, diff) != after or apply_single_native_hunk(before, diff) != after:
        raise EvidenceGap(f"{run_id}: native diff does not reconstruct observer after snapshot")
    metric = next(row for row in intact["metrics"] if row["id"] == "work.changed_files")
    if metric["state"] != "unresolved" or metric["observed_eligible"] != 1 or metric["decoded_eligible"] != 1:
        raise EvidenceGap(f"{run_id}: changed-file predecessor metric differs")
    successor = deepcopy(intact["measurement"])
    target = next(row for row in successor["metrics"] if row["id"] == "work.changed_files")
    target.update(state="measured", correct=1)
    before_score = score_public_control_run(intact["measurement"], intact["format_evidence"]["profile"])
    after_score = score_public_control_run(successor, intact["format_evidence"]["profile"])
    changed = {key for key in before_score.metrics if before_score.metrics[key] != after_score.metrics[key]}
    if changed != {"work.changed_files"} or after_score.metrics["work.changed_files"] != 1:
        raise EvidenceGap(f"{run_id}: diagnostic changed unexpected score")
    return {"run_id": run_id, "repetition": repetition, "metric_state": "measured",
            "score_fraction": "1/1", "project_relative_path": "fixture_project/checkout.py",
            "before_sha256": _sha(before), "after_sha256": _sha(after),
            "replay_manifest_sha256": _sha(manifest_path.read_bytes()),
            "replay_receipt_sha256": _sha(receipt_path.read_bytes()),
            "capture_receipt_sha256": _sha(capture_bytes), "helper_ledger_sha256": _sha(helper_bytes),
            "observer_sha256": _sha(observer_bytes), "native_rollout_path": native_entry["path"],
            "native_rollout_sha256": _sha(native), "native_diff_sha256": _sha(diff.encode("utf-8")),
            **witness}


def build_codex_cli_changed_file_diagnostic(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    replay_root = repository / "artifacts/v1-expanded-preparation/codex-stdout-score-replay-v2"
    repetitions = []
    for number in (1, 2, 3):
        try:
            repetitions.append(_run(replay_root, number))
        except EvidenceGap as exc:
            repetitions.append({"run_id": f"codex-cli-eval-{number}", "repetition": number,
                                "metric_state": "unresolved", "reason": str(exc)})
    closed = all(row["metric_state"] == "measured" for row in repetitions)
    return {"schema_version": "session-bench-codex-cli-changed-file-private-diagnostic-v1",
            "configuration_id": "codex-cli", "scope": "private_additive_changed_file_only",
            "public_safe": False, "independent_reproduction": False, "rankable": False,
            "historical_inputs_overwritten": False,
            "metric_closure": {"metric_id": "work.changed_files", "predecessor_state": "unresolved",
                               "successor_state": "measured" if closed else "unresolved",
                               "score_fraction": "1/1" if closed else None},
            "repetitions": repetitions,
            "limitations": ["This diagnostic is private and has not received independent reproduction or public-safety review.",
                            "Other unresolved Codex CLI metrics and the complete-root contradiction are unchanged."]}


def write_codex_cli_changed_file_diagnostic(repository: Path, output: Path) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValueError("diagnostic output must be new")
    result = build_codex_cli_changed_file_diagnostic(repository)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical(result) + b"\n")
    return result
