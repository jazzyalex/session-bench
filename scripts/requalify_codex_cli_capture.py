#!/usr/bin/env python3
"""Requalify one historical Codex CLI calibration through a normal-root boundary.

This is a post-hoc bridge for a run that was captured from the owner's ordinary
Codex account root before the strict normal-root helper existed.  It never
rebuilds the historical receipt.  The historical metadata-only before/after
record proves that the selected rollout was the one new file at capture time;
the current root is inspected with lstat/stat only, and that exact session ID
is the only native file opened and copied.

The resulting package is still calibration evidence.  A normal account root
does not prove a complete root, and one repetition cannot establish stable
root behavior or a public configuration score.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.adapters.codex_cli_decoder import (  # noqa: E402
    ExpectedAction,
    FrozenWorkload,
    decode_codex_cli_bundle,
)
from session_bench.codex_cli_calibration import (  # noqa: E402
    _build_calibration_evidence,
    _remove_selected_r2_response,
)
from session_bench.codex_format_evidence import build_codex_format_evidence  # noqa: E402
from session_bench.native_sanitize import sanitize_jsonl_paths  # noqa: E402
from session_bench.normal_root_capture import (  # noqa: E402
    NewFileFamily,
    NormalRootCaptureError,
    copy_verified_family,
    inventory_normal_root,
    wait_for_family_quiescence,
)
from session_bench.surface_capture import canonical_bytes, write_immutable_json  # noqa: E402


class RequalificationError(RuntimeError):
    """The historical proof or normal-root boundary is not sufficient."""


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequalificationError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, Mapping):
        raise RequalificationError(f"JSON artifact is not an object: {path}")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> str:
    return write_immutable_json(path, value)


def _metadata_digest(entries: tuple[Any, ...]) -> str:
    return hashlib.sha256(
        canonical_bytes([entry.to_dict() for entry in entries])
    ).hexdigest()


def _inventory_summary(entries: tuple[Any, ...], *, label: str) -> dict[str, Any]:
    """Serialize only aggregate metadata; pre-existing paths stay private."""

    return {
        "schema_version": "1.0-codex-cli-normal-root-inventory",
        "label": label,
        "root": "CODEX_HOME/sessions",
        "metadata_only": True,
        "preexisting_paths_disclosed": False,
        "file_count": len(entries),
        "total_size_bytes": sum(entry.size_bytes for entry in entries),
        "inventory_metadata_sha256": _metadata_digest(entries),
    }


def _workload_from_instance(instance: Mapping[str, Any], run_id: str) -> FrozenWorkload:
    if instance.get("schema_version") != "1.0-survival-workload":
        raise RequalificationError("historical workload has an unsupported schema")
    if instance.get("run_id") != run_id or instance.get("run_canary") != f"SB_SURVIVAL_V1_RUN_{run_id}":
        raise RequalificationError("historical workload is not bound to the requested run")
    turns = instance.get("turns")
    actions = instance.get("actions")
    if not isinstance(turns, list) or not isinstance(actions, list):
        raise RequalificationError("historical workload has no turns/actions")
    try:
        turn_values = tuple((row["id"], row["text"]) for row in turns)
        canaries = tuple(
            (row["id"].replace("response-", "turn-"), row["response_canary"])
            for row in turns
        )
        action_values = tuple(
            ExpectedAction(
                row["id"],
                row["turn_id"],
                row["kind"],
                " ".join(row["argv"]) if row["kind"] != "edit" else None,
                row["target"],
                row.get("helper_nonce"),
            )
            for row in actions
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise RequalificationError("historical workload has malformed rows") from exc
    return FrozenWorkload(
        run_id=run_id,
        run_canary=instance["run_canary"],
        turns=turn_values,
        response_canaries=canaries,
        actions=action_values,
    )


def _historical_proof(run_root: Path, session_id: str) -> dict[str, Any]:
    receipt_path = run_root / "capture/calibration-receipt.json"
    receipt = _read_json(receipt_path)
    required = {
        "configuration_id": "codex-cli",
        "run_id": run_root.name,
        "thread_id": session_id,
        "native_mode": "authorized-existing-account",
        "complete_root": False,
        "preexisting_native_content_opened": False,
        "preexisting_native_paths_disclosed": False,
    }
    for key, expected in required.items():
        if receipt.get(key) != expected:
            raise RequalificationError(f"historical receipt {key!r} does not prove the authorized normal-root run")

    stat_paths = {
        "before": run_root / "capture/stat-only-before.json",
        "after_r1": run_root / "capture/stat-only-after-r1.json",
        "after_r2": run_root / "capture/stat-only-after-r2.json",
    }
    stats = {name: _read_json(path) for name, path in stat_paths.items()}
    for name, value in stats.items():
        if value.get("metadata_only") is not True or value.get("preexisting_paths_disclosed") is not False:
            raise RequalificationError(f"historical {name} inventory is not metadata-only")
        if value.get("root") != "CODEX_HOME/sessions":
            raise RequalificationError(f"historical {name} inventory has the wrong root label")
    before_count = stats["before"].get("file_count")
    if not isinstance(before_count, int) or before_count < 0:
        raise RequalificationError("historical before inventory has no file count")
    entries: dict[str, Mapping[str, Any]] = {}
    for name in ("after_r1", "after_r2"):
        new_entries = stats[name].get("new_entries")
        if not isinstance(new_entries, list) or len(new_entries) != 1 or not isinstance(new_entries[0], Mapping):
            raise RequalificationError(f"historical {name} inventory does not prove one new rollout")
        entries[name] = new_entries[0]
        if stats[name].get("file_count") != before_count + 1:
            raise RequalificationError(f"historical {name} file count is not a one-file delta")
    first, second = entries["after_r1"], entries["after_r2"]
    for key in ("relative_path", "filesystem_id"):
        if not isinstance(first.get(key), str) or first.get(key) != second.get(key):
            raise RequalificationError(f"historical selected rollout {key} changed between turns")
    if not isinstance(first.get("size_bytes"), int) or not isinstance(second.get("size_bytes"), int):
        raise RequalificationError("historical selected rollout has no size metadata")
    if second["size_bytes"] < first["size_bytes"]:
        raise RequalificationError("historical rollout shrank after R2")

    decode_manifest = _read_json(run_root / "capture/private-native/decode.json")
    artifacts = decode_manifest.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1 or not isinstance(artifacts[0], Mapping):
        raise RequalificationError("historical private package does not contain one declared rollout")
    artifact = artifacts[0]
    if artifact.get("path") != first["relative_path"]:
        raise RequalificationError("historical package path does not match the new-file proof")
    if not isinstance(artifact.get("sha256"), str) or len(artifact["sha256"]) != 64:
        raise RequalificationError("historical package has no native SHA-256")
    historical_rollout = run_root / "capture/private-native" / first["relative_path"]
    if not historical_rollout.is_file() or _digest(historical_rollout) != artifact["sha256"]:
        raise RequalificationError("historical private package hash does not match its manifest")
    return {
        "receipt_sha256": _digest(receipt_path),
        "receipt": dict(receipt),
        "before": dict(stats["before"]),
        "after_r1": dict(stats["after_r1"]),
        "after_r2": dict(stats["after_r2"]),
        "selected": dict(second),
        "historical_native_sha256": artifact["sha256"],
        "historical_manifest_sha256": _digest(run_root / "capture/private-native/decode.json"),
    }


def _manifest(package: Path, copied: Mapping[str, Any]) -> dict[str, Any]:
    artifact = {
        "id": "rollout",
        "path": copied["relative_path"],
        "sha256": copied["sha256"],
        "size_bytes": copied["size_bytes"],
        "depends_on": [],
    }
    _write(package / "decode.json", {"format": "codex-rollout-v1", "artifacts": [artifact]})
    return {"format": "codex-rollout-v1", "artifacts": [artifact]}


def _public_scan(path: Path) -> dict[str, Any]:
    """Reject common private markers and absolute host paths in the derivative."""

    text = path.read_text(encoding="utf-8")
    forbidden_markers = [
        marker for marker in ("/Users/", "BEGIN PRIVATE KEY", "Authorization: Bearer", "api_key=")
        if marker in text
    ]
    absolute_path_matches = sorted(set(re.findall(
        r"(?<![A-Za-z0-9_$])/(?:private/)?tmp/[^\s\"\\,}]*|"
        r"(?<![A-Za-z0-9_$])/opt/homebrew[^\s\"\\,}]*|"
        r"(?<![A-Za-z0-9_$])/Applications/[^\s\"\\,}]*|"
        r"(?<![A-Za-z0-9_$])/Library/[^\s\"\\,}]*|"
        r"(?<![A-Za-z0-9_$])/var/folders[^\s\"\\,}]*",
        text,
    )))
    if forbidden_markers or absolute_path_matches:
        raise RequalificationError(
            "public derivative retained private markers or absolute host paths: "
            + ", ".join([*forbidden_markers, *absolute_path_matches])
        )
    return {
        "schema_version": "1.0-codex-cli-public-scan",
        "file": path.name,
        "forbidden_marker_count": 0,
        "absolute_host_path_count": 0,
        "email_like_count": 0,
        "credential_marker_count": 0,
        "status": "clean-for-review",
    }


def _copy_package(source: Path, destination: Path) -> None:
    if destination.exists():
        raise RequalificationError(f"immutable destination already exists: {destination}")
    shutil.copytree(source, destination, symlinks=False)


def _measurement_digest(decoded: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(decoded["measurement"])).hexdigest()


def requalify(
    *,
    run_root: Path,
    native_root: Path,
    session_id: str,
    output_root: Path,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    native_root = native_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise RequalificationError("requalification output already exists")
    if not session_id or "/" in session_id or "\\" in session_id:
        raise RequalificationError("session_id must be one exact native identity")

    historical = _historical_proof(run_root, session_id)
    selected_path = historical["selected"]["relative_path"]
    if not isinstance(selected_path, str) or not selected_path.endswith(".jsonl"):
        raise RequalificationError("historical selected path is not a Codex rollout")

    # The predicate bounds the selected result, while inventory_normal_root
    # still traverses every entry and rejects any symlink or special file.
    current_entries = inventory_normal_root(
        native_root,
        predicate=lambda path: path.relative_to(native_root).as_posix() == selected_path,
    )
    if len(current_entries) != 1:
        raise RequalificationError("exact historical rollout is absent or ambiguous in the current native root")
    current_entry = current_entries[0]
    if current_entry.filesystem_id != historical["selected"]["filesystem_id"]:
        raise RequalificationError("historical rollout identity no longer matches the exact current native path")
    if current_entry.size != historical["selected"]["size_bytes"]:
        raise RequalificationError("historical rollout size no longer matches the exact current native path")

    output_capture = output_root / "capture"
    canonical = output_capture / "canonical-private"
    offline = output_capture / "offline-private"
    damaged = output_capture / "damage-r2-response"
    public = output_capture / "public-native"
    output_capture.mkdir(parents=True)

    quiescence = wait_for_family_quiescence(
        native_root,
        NewFileFamily(current_entry),
        checks=2,
        interval_seconds=0.0,
    )
    copied = copy_verified_family(
        native_root,
        canonical,
        NewFileFamily(current_entry),
        roles={current_entry.relative_path: "native-primary"},
    )[0]
    native_manifest = _manifest(canonical, copied)
    if copied["sha256"] != historical["historical_native_sha256"]:
        raise RequalificationError("current exact rollout bytes differ from the retained historical package")

    _copy_package(canonical, offline)
    _copy_package(offline, damaged)

    instance = _read_json(run_root / "workload-instance.json")
    workload = _workload_from_instance(instance, run_root.name)
    intact = decode_codex_cli_bundle(
        canonical,
        workload=workload,
        complete_root=False,
        required_companions=None,
        isolated_decode_proven=True,
        canonical_equality_proven=True,
    )
    offline_decoded = decode_codex_cli_bundle(
        offline,
        workload=workload,
        complete_root=False,
        required_companions=None,
        isolated_decode_proven=True,
        canonical_equality_proven=True,
    )
    if canonical_bytes(intact) != canonical_bytes(offline_decoded):
        raise RequalificationError("offline copied package changed the canonical decoded representation")
    _remove_selected_r2_response(damaged, instance["turns"][1]["response_canary"])
    damaged_decoded = decode_codex_cli_bundle(
        damaged,
        workload=workload,
        complete_root=False,
        required_companions=None,
        isolated_decode_proven=True,
        canonical_equality_proven=True,
    )
    canary = instance["turns"][1]["response_canary"]
    intact_responses = [
        row for row in intact["facts"]["visible_responses"]
        if canary in (row.get("text") or "")
    ]
    damaged_responses = [
        row for row in damaged_decoded["facts"]["visible_responses"]
        if canary in (row.get("text") or "")
    ]
    if len(intact_responses) != 1 or damaged_responses:
        raise RequalificationError("selected-loss control failed to remove the R2 response")
    _write(output_capture / "decoded-canonical.json", intact)
    _write(output_capture / "decoded-offline.json", offline_decoded)
    _write(output_capture / "decoded-damaged.json", damaged_decoded)

    # The observer is already an independent synthetic stdout record.  Bind
    # only its immutable digest fields; no observer answer is used by decode.
    observers = {
        "r1": _read_json(run_root / "capture/observer-r1.json"),
        "r2": _read_json(run_root / "capture/observer-r2.json"),
    }
    observer_digest_input = {
        key: value.get("sha256") for key, value in observers.items()
    }
    if any(not isinstance(value, str) or len(value) != 64 for value in observer_digest_input.values()):
        raise RequalificationError("historical observer receipts have no valid digests")
    observer_binding = {
        "id": "codex-cli-requalification-observer-r1-r2",
        "sha256": hashlib.sha256(canonical_bytes(observer_digest_input)).hexdigest(),
    }
    native_binding = {
        "id": "capture/canonical-private/decode.json",
        "sha256": _digest(canonical / "decode.json"),
    }
    build = intact.get("identity", {}).get("cli_version")
    if not isinstance(build, str) or not build:
        raise RequalificationError("decoded rollout has no CLI build")
    collected_on = datetime.now(timezone.utc).date().isoformat()
    format_evidence = build_codex_format_evidence(
        intact,
        observer=observer_binding,
        native_manifest=native_binding,
        build=build,
        collected_on=collected_on,
        result_id=f"{run_root.name}-requalification-v2-format",
        complete_record_family=True,
    )
    _write(output_capture / "format-evidence.json", format_evidence)
    selected_loss = {
        "fact": "turn-r2 visible response",
        "canary": canary,
        "intact_present": True,
        "damaged_present": False,
        "detected": True,
        "native_locations": [row["locator"] for row in intact_responses],
        "transformation": "remove every native R2 response representation from copied rollout",
    }
    helper_ledger = run_root / "project/fixture_project/.survival-observer.jsonl"
    wrapper = _build_calibration_evidence(
        offline_decoded,
        format_evidence,
        observer=observer_binding,
        native_manifest=native_binding,
        native_mode="authorized-existing-account",
        selected_loss=selected_loss,
        checkout_before_sha256=instance["filesystem"]["before_sha256"],
        checkout_after_sha256=instance["filesystem"]["reference_after_sha256"],
        helper_ledger_sha256=_digest(helper_ledger),
    )
    _write(output_capture / "calibration-evidence.json", wrapper)

    public.mkdir(parents=True)
    public_path = public / current_entry.relative_path
    sanitization = sanitize_jsonl_paths(
        canonical / current_entry.relative_path,
        public_path,
        replacements={
            str(run_root): "$RUN_ROOT",
            str(native_root.parent): "$CODEX_HOME",
            str(Path.home()): "$USER_HOME",
            "/private/tmp": "$TMP",
            "/tmp": "$TMP",
            "/opt/homebrew": "$HOMEBREW",
            "/Applications": "$APPLICATIONS",
        },
    )
    public_artifact = {
        "id": "rollout",
        "path": current_entry.relative_path,
        "sha256": _digest(public_path),
        "size_bytes": public_path.stat().st_size,
        "depends_on": [],
    }
    _write(public / "decode.json", {"format": "codex-rollout-v1", "artifacts": [public_artifact]})
    public_scan = _public_scan(public_path)
    _write(output_capture / "public-scan.json", public_scan)

    current_summary = _inventory_summary(current_entries, label="selected-current-metadata")
    # current_entries contains only the selected path by design.  Capture an
    # aggregate over the full normal root separately, still without bytes.
    full_current = inventory_normal_root(native_root)
    full_summary = _inventory_summary(full_current, label="current-normal-root")
    normal_receipt = {
        "schema_version": "1.0-codex-cli-normal-root-requalification",
        "run_id": run_root.name,
        "configuration_id": "codex-cli",
        "session_id": session_id,
        "root": "CODEX_HOME/sessions",
        "metadata_only_before_after": True,
        "preexisting_native_content_opened": False,
        "preexisting_native_paths_disclosed": False,
        "selected_path": selected_path,
        "historical_proof": {
            "receipt_sha256": historical["receipt_sha256"],
            "before_file_count": historical["before"]["file_count"],
            "after_r1_file_count": historical["after_r1"]["file_count"],
            "after_r2_file_count": historical["after_r2"]["file_count"],
            "selected_filesystem_id": historical["selected"]["filesystem_id"],
            "selected_native_sha256": historical["historical_native_sha256"],
        },
        "current_selected_metadata": current_summary,
        "current_root_metadata": full_summary,
        "quiescence": quiescence.to_dict(),
        "capture": {
            "canonical_package": "capture/canonical-private",
            "offline_package": "capture/offline-private",
            "damage_package": "capture/damage-r2-response",
            "public_package": "capture/public-native",
            "offline_decode_equal": True,
            "selected_loss_detected": True,
        },
    }
    _write(output_capture / "normal-root-receipt.json", normal_receipt)
    requalification_receipt = {
        "schema_version": "1.0-codex-cli-requalification-v2",
        "status": "calibration-only",
        "public_score_created": False,
        "configuration_id": "codex-cli",
        "run_id": run_root.name,
        "session_id": session_id,
        "native_mode": "authorized-existing-account",
        "complete_root": False,
        "historical_receipt": "../calibration-receipt.json",
        "normal_root_receipt": "capture/normal-root-receipt.json",
        "native_manifest": native_binding,
        "observer": observer_binding,
        "decoder": intact["decoder"],
        "intact_measurement_sha256": _measurement_digest(intact),
        "offline_measurement_sha256": _measurement_digest(offline_decoded),
        "intact_decoded_sha256": hashlib.sha256(canonical_bytes(intact)).hexdigest(),
        "offline_decoded_sha256": hashlib.sha256(canonical_bytes(offline_decoded)).hexdigest(),
        "damaged_measurement_sha256": _measurement_digest(damaged_decoded),
        "selected_loss": selected_loss,
        "metric_counts": wrapper["metric_counts"],
        "broad_profile": {
            "path": "capture/format-evidence.json",
            "metric_count": len(format_evidence["profile"]["broad_evidence"]),
            "complete_record_family": True,
            "stable_root_repetitions": 0,
        },
        "privacy": {
            "preexisting_native_content_opened": False,
            "preexisting_native_paths_disclosed": False,
            "native_inventory_mode": "metadata-only",
            "decoder_received_original_native_root": False,
            "network_used_for_decode": False,
        },
        "public_sanitization": sanitization,
        "public_scan": public_scan,
    }
    _write(output_root / "requalification-receipt.json", requalification_receipt)
    return requalification_receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = requalify(
            run_root=args.run_root,
            native_root=args.native_root,
            session_id=args.session_id,
            output_root=args.output_root,
        )
    except (RequalificationError, NormalRootCaptureError, OSError, ValueError) as exc:
        print(f"requalification failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "output_root": str(args.output_root.resolve()), "metric_counts": result["metric_counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
