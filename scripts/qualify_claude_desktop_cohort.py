#!/usr/bin/env python3
"""Qualify the three captured Claude Desktop repetitions as private evidence.

This is an additive, offline pass over the exact finalized captures named in
``COHORT``.  It does not discover Claude roots, open a session, change the
historical finalizer output, or publish a score.  The pass binds three
metadata-only root inventories, re-runs the persistent-family validator, and
rebuilds the observer with the exact prompts recorded by the independent GUI
receipt.  The latter matters because the instantiated workload text contains
an evaluation wrapper that is not necessarily the text submitted through CUA.

The output is private by design.  Each repetition receives a
``capture/qualified-private-v1`` directory containing a qualified 31-cell
wrapper and a score from the shared v1 scorer.  A score here is a diagnostic
for the captured cohort; it is not a public rank or vendor claim.
"""

from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for path in (REPO, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle  # noqa: E402
from session_bench.claude_desktop_root import REQUIRED_FILES, validate_claude_desktop_family  # noqa: E402
from session_bench.claude_format_evidence import build_claude_format_evidence  # noqa: E402
from session_bench.survival_evidence import (  # noqa: E402
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_prospective_evidence_input,
)
from session_bench.v1_public_score import (  # noqa: E402
    FORMAT_METRICS,
    aggregate_public_configuration,
    score_public_run,
    validate_format_evidence,
)
from session_bench.survival_metrics import validate_input  # noqa: E402

from finalize_claude_desktop_runs import _build_observer  # noqa: E402
from run_claude_survival import _build_31_evidence  # noqa: E402


COHORT: tuple[tuple[str, int], ...] = (
    ("claude-desktop-eval-1-correction-1", 1),
    ("claude-desktop-eval-2-correction-1", 2),
    ("claude-desktop-eval-3", 3),
)
COHORT_IDS = {run_id for run_id, _repetition in COHORT}
OUTPUT_NAME = "qualified-private-v1"
ROOT_LOCATOR = (
    "CLAUDE_HOME/projects/<project-key>/<session-id>.jsonl + "
    "Claude Desktop session metadata"
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
HEX = set("0123456789abcdef")


class QualificationError(ValueError):
    """The selected three-run Desktop cohort is not qualifiable."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise QualificationError(f"expected an ordinary file: {path}")
    return _sha_bytes(path.read_bytes())


def _load(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise QualificationError(f"expected an ordinary JSON file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"invalid JSON file: {path}") from exc
    return value


def _load_object(path: Path) -> dict[str, Any]:
    value = _load(path)
    if not isinstance(value, dict):
        raise QualificationError(f"JSON value must be an object: {path}")
    return value


def _write(path: Path, value: Any) -> str:
    if path.exists() or path.is_symlink():
        raise QualificationError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(value) + b"\n"
    path.write_bytes(data)
    return _sha_bytes(data)


def _digest_binding(identifier: str, digest: str) -> dict[str, str]:
    if not identifier or identifier != identifier.strip():
        raise QualificationError("artifact identifier is empty or untrimmed")
    if len(digest) != 64 or any(char not in HEX for char in digest):
        raise QualificationError(f"invalid SHA-256 for {identifier}")
    return {"id": identifier, "sha256": digest}


def _run_root(run_id: str) -> Path:
    if run_id not in COHORT_IDS or not RUN_ID_RE.fullmatch(run_id):
        raise QualificationError(f"run is outside the exact Claude Desktop cohort: {run_id}")
    root = (REPO / "artifacts" / "survival-v1-runs" / run_id).resolve()
    if not root.is_dir() or root.is_symlink():
        raise QualificationError(f"run root is missing or is a symlink: {run_id}")
    return root


def _validate_inventory(run_id: str, repetition: int, run_root: Path) -> dict[str, Any]:
    """Bind a metadata-only before/after inventory without exposing names."""

    before_path = run_root / "capture/observer-private/before-inventory.json"
    after_path = run_root / "capture/observer-private/after-inventory.json"
    before = _load_object(before_path)
    after = _load_object(after_path)
    expected_roots = ["claude-projects", "claude-desktop-sessions"]
    for label, value, phase in (
        ("before", before, "before"),
        ("after", after, "after"),
    ):
        if value.get("schema_version") != "session-bench-metadata-inventory-v1":
            raise QualificationError(f"{run_id} {label} inventory schema is not supported")
        if value.get("run_id") != run_id or value.get("phase") != phase:
            raise QualificationError(f"{run_id} {label} inventory identity is inconsistent")
        if value.get("roots") != expected_roots:
            raise QualificationError(f"{run_id} {label} inventory roots changed")
        if value.get("personal_history_content_scanned") is not False:
            raise QualificationError(f"{run_id} {label} inventory is not privacy safe")
        if not isinstance(value.get("metadata_digest"), str) or len(value["metadata_digest"]) != 64:
            raise QualificationError(f"{run_id} {label} inventory lacks a metadata digest")
        if type(value.get("file_count")) is not int or value["file_count"] < 0:
            raise QualificationError(f"{run_id} {label} inventory file count is invalid")
    if type(after.get("new_file_count")) is not int or after["new_file_count"] != 2:
        raise QualificationError(f"{run_id} after inventory does not isolate the two-file family")
    if type(after.get("changed_or_new_file_count")) is not int or after["changed_or_new_file_count"] != 2:
        raise QualificationError(f"{run_id} after inventory has unexpected changed-file count")
    if after["file_count"] - before["file_count"] != 2:
        raise QualificationError(f"{run_id} before/after file counts do not bind two new files")
    return {
        "run_id": run_id,
        "repetition": repetition,
        "root_locator": ROOT_LOCATOR,
        "isolated_discovery": True,
        "personal_history_scanned": False,
        "before": {
            "id": "capture/observer-private/before-inventory.json",
            "sha256": _sha(before_path),
            "file_count": before["file_count"],
            "metadata_digest": before["metadata_digest"],
        },
        "after": {
            "id": "capture/observer-private/after-inventory.json",
            "sha256": _sha(after_path),
            "file_count": after["file_count"],
            "metadata_digest": after["metadata_digest"],
            "new_file_count": after["new_file_count"],
            "changed_or_new_file_count": after["changed_or_new_file_count"],
        },
        "privacy": {
            "metadata_only": True,
            "unrelated_preexisting_sessions_opened": False,
            "personal_history_content_scanned": False,
        },
    }


def _validate_submitted_prompts(
    run_id: str,
    repetition: int,
    final_gui: Mapping[str, Any],
    independent_gui: Mapping[str, Any],
) -> dict[str, str]:
    """Require the exact prompts observed through CUA, independent of JSONL."""

    # The observer-private receipt is the independent source.  The finalizer
    # receipt is checked as a matching copy when it already carries the field.
    prompts = independent_gui.get("submitted_prompts")
    if prompts is None:
        prompts = final_gui.get("submitted_prompts")
    if not isinstance(prompts, Mapping) or set(prompts) != {"r1", "r2"}:
        raise QualificationError(
            f"{run_id} GUI receipt must record exact submitted_prompts.r1 and .r2"
        )
    result: dict[str, str] = {}
    for key in ("r1", "r2"):
        value = prompts[key]
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise QualificationError(f"{run_id} submitted prompt {key} is not exact text")
        result[key] = value
    if "submitted_prompts" in independent_gui and "submitted_prompts" in final_gui:
        if independent_gui["submitted_prompts"] != final_gui["submitted_prompts"]:
            raise QualificationError(f"{run_id} GUI prompt copies disagree")
    if independent_gui.get("run_id") not in (None, run_id):
        raise QualificationError(f"{run_id} independent GUI receipt has the wrong run")
    if independent_gui.get("repetition") not in (None, repetition):
        raise QualificationError(f"{run_id} independent GUI receipt has the wrong repetition")
    return result


def _validate_existing_capture(
    run_id: str, repetition: int
) -> dict[str, Any]:
    run_root = _run_root(run_id)
    output = run_root / "capture" / "finalized-private-v1"
    if not output.is_dir() or output.is_symlink():
        raise QualificationError(f"{run_id} has no finalized private output")
    if (run_root / "capture" / OUTPUT_NAME).exists():
        raise QualificationError(f"{run_id} already has additive qualification output")

    final_attempt = _load_object(output / "attempt-finalized.json")
    if (
        final_attempt.get("attempt_id") != run_id
        or final_attempt.get("configuration_id") != "claude-desktop"
        or final_attempt.get("repetition") != repetition
        or final_attempt.get("state") != "captured_unscored"
        or final_attempt.get("score_eligible") is not False
        or final_attempt.get("public_score_eligible") is not False
    ):
        raise QualificationError(f"{run_id} finalized capture identity or guard changed")
    if final_attempt.get("counts") != {
        "actions": 3,
        "responses": 2,
        "results": 3,
        "submitted_turns": 2,
    }:
        raise QualificationError(f"{run_id} does not have the required 2/2/3/3 capture")

    family_package = output / "native-family-private"
    try:
        family = validate_claude_desktop_family(family_package)
    except ValueError as exc:
        raise QualificationError(f"{run_id} persistent family failed validation: {exc}") from exc
    roles = {row.get("role") for row in family.get("artifacts", []) if isinstance(row, Mapping)}
    if not set(REQUIRED_FILES) <= roles:
        raise QualificationError(f"{run_id} persistent family is missing a required role")
    if family.get("complete_persistent_family") is not True:
        raise QualificationError(f"{run_id} persistent pair is not complete")

    manifest = _load_object(output / "native-manifest.json")
    selected = manifest.get("selected_artifact")
    if (
        manifest.get("configuration_id") != "claude-desktop"
        or manifest.get("repetition") != repetition
        or manifest.get("complete_cross_root_family") is not True
        or not isinstance(selected, Mapping)
        or selected.get("sha256") != _sha(output / "native-package/session.jsonl")
        or manifest.get("stable_root_repetition_qualified") is not False
    ):
        raise QualificationError(f"{run_id} native manifest is not the expected unqualified source")
    if manifest.get("family_validation", {}).get("sha256") != _sha(output / "family-validation.json"):
        raise QualificationError(f"{run_id} native manifest family binding is stale")

    replay = _load_object(output / "replay-receipt.json")
    canonical = replay.get("canonical_equality", {})
    loss = replay.get("selected_loss", {})
    if (
        canonical.get("native_vs_offline") is not True
        or loss.get("response_loss_detected") is not True
        or loss.get("actions_preserved") is not True
        or loss.get("results_preserved") is not True
    ):
        raise QualificationError(f"{run_id} replay or selected-loss controls are incomplete")
    intact = _load_object(output / "decoded-native.json")
    offline = _load_object(output / "decoded-offline.json")
    if intact != offline or intact.get("session_id") != family.get("cli_session_id"):
        raise QualificationError(f"{run_id} native/offline decode equality is not bound")

    gui_final = _load_object(output / "gui-receipt.json")
    gui_independent = _load_object(run_root / "capture/observer-private/gui-observer.json")
    prompts = _validate_submitted_prompts(run_id, repetition, gui_final, gui_independent)
    inventory = _validate_inventory(run_id, repetition, run_root)

    source_measurement = _load_object(output / "measurement.json")
    source_format = _load_object(output / "format-evidence.json")
    source_survival = _load_object(output / "survival-evidence.json")
    source_31 = _load_object(output / "evidence-31.json")
    validate_input(source_measurement)
    validate_format_evidence(source_format)
    validate_prospective_evidence_input(source_survival)
    if (
        len(source_31.get("metrics", [])) != 31
        or len(source_31.get("metric_evidence", [])) != 31
        or source_31.get("score_eligible") is not False
    ):
        raise QualificationError(f"{run_id} source 31-cell wrapper is incomplete or score-enabled")

    project_root = run_root / "final-project-private"
    ledger_path = project_root / ".survival-observer.jsonl"
    before_checkout = project_root / "snapshots" / "checkout.before.py"
    checkout = project_root / "checkout.py"
    for path in (ledger_path, before_checkout, checkout):
        if path.is_symlink() or not path.is_file():
            raise QualificationError(f"{run_id} selected synthetic project is incomplete: {path.name}")

    return {
        "run_id": run_id,
        "repetition": repetition,
        "run_root": run_root,
        "output": output,
        "family": family,
        "manifest": manifest,
        "replay": replay,
        "intact": intact,
        "gui_final": gui_final,
        "gui_independent": gui_independent,
        "submitted_prompts": prompts,
        "inventory": inventory,
        "measurement": source_measurement,
        "format": source_format,
        "survival": source_survival,
        "source_31": source_31,
        "project_root": project_root,
        "before_checkout_sha256": _sha(before_checkout),
        "after_checkout_sha256": _sha(checkout),
        "helper_raw": ledger_path.read_text(encoding="utf-8"),
        "observer_source_sha256": _sha(output / "observer.json"),
    }


def _qualify_measurement(source: Mapping[str, Any], *, run_id: str, repetition: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = copy.deepcopy(dict(source))
    if (
        value.get("run_id") != run_id
        or value.get("configuration_id") != "claude-desktop"
        or value.get("repetition") != repetition
    ):
        raise QualificationError(f"{run_id} measurement identity changed")
    rows = value.get("metrics")
    if not isinstance(rows, list):
        raise QualificationError(f"{run_id} measurement metrics are missing")
    changed: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") in by_id:
            raise QualificationError(f"{run_id} measurement metric set is malformed")
        by_id[row["id"]] = row
    for metric_id in ("attribution.usage", "attribution.token_semantics"):
        row = by_id.get(metric_id)
        if row is None:
            raise QualificationError(f"{run_id} lacks {metric_id}")
        before = row["state"]
        if before not in {"unresolved", "native_absent"}:
            raise QualificationError(
                f"{run_id} {metric_id} is {before}; qualification cannot reinterpret it"
            )
        row["state"] = "native_absent"
        row["correct"] = 0
        if before != "native_absent":
            changed.append(
                {
                    "metric_id": metric_id,
                    "from": before,
                    "to": "native_absent",
                    "reason": (
                        "the complete persistent transcript plus Desktop metadata pair "
                        "was searched and contains no per-response usage/token semantics"
                    ),
                }
            )
    reconciliation = by_id.get("attribution.reconciliation")
    if reconciliation is None or reconciliation.get("state") != "native_absent":
        raise QualificationError(f"{run_id} reconciliation is not the expected native absence")
    revision = by_id.get("revision.r1")
    if revision is None or revision.get("state") != "contradiction":
        raise QualificationError(
            f"{run_id} revision.r1 contradiction was not preserved from the source measurement"
        )
    validate_input(value)
    return value, changed


def _build_root_repetitions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(records) != 3 or {item.get("repetition") for item in records} != {1, 2, 3}:
        raise QualificationError("stable-root qualification requires exactly repetitions 1, 2, and 3")
    if len({item.get("root_locator") for item in records}) != 1:
        raise QualificationError("stable-root locators differ across repetitions")
    return [
        {
            "repetition": item["repetition"],
            "root_locator": item["root_locator"],
            "isolated_discovery": item["isolated_discovery"],
            "personal_history_scanned": item["personal_history_scanned"],
        }
        for item in sorted(records, key=lambda row: row["repetition"])
    ]


def _build_stable_root_proof(records: list[dict[str, Any]]) -> dict[str, Any]:
    repetitions = _build_root_repetitions(records)
    return {
        "schema_version": "session-bench-claude-desktop-stable-root-proof-v1",
        "configuration_id": "claude-desktop",
        "cohort_id": "+".join(run_id for run_id, _rep in COHORT),
        "root_locator": ROOT_LOCATOR,
        "same_root_locator_across_repetitions": True,
        "repetitions": [
            {
                "run_id": item["run_id"],
                "repetition": item["repetition"],
                "root_locator": item["root_locator"],
                "isolated_discovery": True,
                "personal_history_scanned": False,
                "before_inventory": item["before"],
                "after_inventory": item["after"],
                "selected_persistent_family": {
                    "family_validation_sha256": item["family_validation_sha256"],
                    "roles": ["transcript", "desktop_metadata"],
                },
            }
            for item in sorted(records, key=lambda row: row["repetition"])
        ],
        "complete_persistent_pair": True,
        "privacy": {
            "metadata_only_before_after": True,
            "personal_history_content_scanned": False,
            "unrelated_preexisting_sessions_opened": False,
        },
    }


def _qualified_observer(record: Mapping[str, Any]) -> dict[str, Any]:
    workload_path = REPO / "fixtures/scenarios/survival-v1/workload/workload.json"
    workload = _load_object(workload_path)
    # The workload is only used for its canaries/shape.  Exact submitted text
    # comes from the independent GUI receipt, never from the native transcript.
    turns = workload.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise QualificationError("frozen workload does not contain two turns")
    for turn in turns:
        if not isinstance(turn, dict):
            raise QualificationError("frozen workload turn is malformed")
        key = "r1" if turn.get("sequence") == 1 else "r2"
        turn["text"] = record["submitted_prompts"][key]
        turn["run_id"] = record["run_id"]
    # The generic observer builder requires an instantiated workload.  The
    # finalizer's preserved ledger and GUI receipt remain the independent
    # evidence inputs; no native transcript field is copied into the observer.
    from session_bench.workload_instance import instantiate_workload

    instantiated, _ = instantiate_workload(workload, record["run_id"])
    # Reapply the exact CUA text after instantiation, whose template normally
    # adds the evaluated/calibration wrapper.
    for turn in instantiated["turns"]:
        key = "r1" if turn.get("sequence") == 1 else "r2"
        turn["text"] = record["submitted_prompts"][key]
    observer = _build_observer(
        workload=instantiated,
        session_id=record["intact"]["session_id"],
        model=str(record["family"].get("model") or "model-unreported"),
        helper_raw=record["helper_raw"],
        helper=_helper_rows(record["helper_raw"]),
        gui=record["gui_final"],
        before_sha=record["before_checkout_sha256"],
        after_sha=record["after_checkout_sha256"],
        run_id=record["run_id"],
        repetition=record["repetition"],
    )
    observer["schema_version"] = "session-bench-claude-desktop-qualified-observer-v1"
    observer["method"] = (
        "independent Desktop GUI receipt with exact submitted prompts, frozen "
        "workload canaries, preserved helper ledger, and filesystem hashes; "
        "native transcript was not used to author expected user text"
    )
    observer["submitted_prompt_source"] = "capture/observer-private/gui-observer.json"
    observer["submitted_prompts_sha256"] = _sha_bytes(_canonical(record["submitted_prompts"]))
    return observer


def _helper_rows(raw: str) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for line in raw.splitlines():
        if not line.strip():
            raise QualificationError("helper ledger contains a blank line")
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("phase") in rows:
            raise QualificationError("helper ledger has duplicate or malformed phase")
        rows[str(value["phase"])] = value
    if set(rows) != {"inspect", "baseline", "final"}:
        raise QualificationError("helper ledger lacks its three expected phases")
    return rows


def qualify(run_pairs: tuple[tuple[str, int], ...] = COHORT) -> list[dict[str, Any]]:
    if run_pairs != COHORT:
        raise QualificationError("only the exact captured Claude Desktop cohort is supported")

    records = [_validate_existing_capture(run_id, repetition) for run_id, repetition in run_pairs]
    if [record["repetition"] for record in records] != [1, 2, 3]:
        raise QualificationError("cohort repetitions must be 1, 2, and 3 in order")
    if len({record["family"].get("cli_version") for record in records}) != 1:
        raise QualificationError("Claude Desktop CLI versions differ across repetitions")
    if len({record["family"].get("model") for record in records}) != 1:
        raise QualificationError("Claude Desktop models differ across repetitions")

    stable_records = [
        {
            **record["inventory"],
            "family_validation_sha256": _sha(record["output"] / "family-validation.json"),
        }
        for record in records
    ]
    stable_proof = _build_stable_root_proof(stable_records)
    stable_proof_sha = _sha_bytes(_canonical(stable_proof) + b"\n")
    root_repetitions = _build_root_repetitions(stable_records)

    prepared: list[dict[str, Any]] = []
    for record in records:
        run_id, repetition = record["run_id"], record["repetition"]
        qualified_measurement, changed = _qualify_measurement(
            record["measurement"], run_id=run_id, repetition=repetition
        )
        observer = _qualified_observer(record)
        observer_sha = _sha_bytes(_canonical(observer) + b"\n")

        qualified_family = copy.deepcopy(record["family"])
        qualified_family.update(
            {
                "run_id": run_id,
                "repetition": repetition,
                "complete_cross_root_family": True,
                "family_scope": "the two exact operator-selected persistent Desktop recording artifacts",
                "metadata_only_discovery": True,
                "personal_history_content_scanned": False,
                "unrelated_preexisting_sessions_opened": False,
                "stable_root_repetition_qualified": True,
                "stable_root_proof": {"id": "stable-root-proof.json", "sha256": stable_proof_sha},
            }
        )
        family_sha = _sha_bytes(_canonical(qualified_family) + b"\n")
        qualified_manifest = copy.deepcopy(record["manifest"])
        qualified_manifest.update(
            {
                "schema_version": "session-bench-claude-desktop-native-manifest-v2-qualified",
                "id": f"{run_id}-native-manifest-qualified-v1",
                "family_validation": {"id": "family-validation-qualified.json", "sha256": family_sha},
                "stable_root_repetition_qualified": True,
                "complete_persistent_family": True,
                "qualified_observer": {"id": "observer-qualified.json", "sha256": observer_sha},
                "stable_root_proof": {"id": "stable-root-proof.json", "sha256": stable_proof_sha},
                "qualification": {
                    "source_manifest": {
                        "id": "../finalized-private-v1/native-manifest.json",
                        "sha256": _sha(record["output"] / "native-manifest.json"),
                    },
                    "complete_persistent_pair": True,
                    "attribution_absence_policy": "complete-family-evidence-v1",
                    "revision_contradiction_preserved": True,
                },
            }
        )
        manifest_sha = _sha_bytes(_canonical(qualified_manifest) + b"\n")

        decoded = record["intact"]
        source_format = record["format"]
        collected_on = source_format["collected_on"]
        build = source_format["build"]
        format_evidence = build_claude_format_evidence(
            decoded,
            observer={"id": "observer-qualified.json", "sha256": observer_sha},
            native_manifest={"id": "native-manifest-qualified-v1", "sha256": manifest_sha},
            run_id=run_id,
            configuration_id="claude-desktop",
            repetition=repetition,
            build=build,
            collected_on=collected_on,
            result_id=f"{run_id}-desktop-format-qualified-v1",
            complete_record_family=True,
            root_repetitions=root_repetitions,
        )
        validate_format_evidence(format_evidence)

        survival_evidence = copy.deepcopy(record["survival"])
        survival_evidence["evaluation_id"] = f"{run_id}-desktop-evaluation-qualified-v1"
        survival_evidence["measurement"] = qualified_measurement
        survival_evidence["observer"] = {"id": "observer-qualified.json", "sha256": observer_sha}
        survival_evidence["native_manifest"] = {
            "id": "native-manifest-qualified-v1",
            "sha256": manifest_sha,
        }
        if survival_evidence.get("schema_version") != PROSPECTIVE_EVIDENCE_SCHEMA_VERSION:
            raise QualificationError(f"{run_id} source evidence is not prospective v1")
        validate_prospective_evidence_input(survival_evidence)

        native_selected = record["manifest"]["selected_artifact"]
        evidence_31 = _build_31_evidence(
            qualified_measurement,
            format_evidence,
            survival_evidence,
            observer_id="observer-qualified.json",
            native_artifact_id=str(native_selected["id"]),
            native_artifact_sha256=str(native_selected["sha256"]),
        )
        evidence_31["claim_limit"] = (
            "three-repetition Claude Desktop Code (Local) private cohort evidence; "
            "no public score, rank, badge, recommendation, or vendor claim"
        )
        if len(evidence_31["metrics"]) != 31 or len(evidence_31["metric_evidence"]) != 31:
            raise QualificationError(f"{run_id} qualified 31-cell wrapper is incomplete")

        score = score_public_run(survival_evidence, format_evidence)
        if not score.rankable or score.overall is None or score.blockers:
            raise QualificationError(f"{run_id} shared scorer remains blocked: {score.blockers}")
        score_display = score.display()
        prepared.append(
            {
                **record,
                "qualified_measurement": qualified_measurement,
                "measurement_changes": changed,
                "observer": observer,
                "observer_sha": observer_sha,
                "qualified_family": qualified_family,
                "family_sha": family_sha,
                "qualified_manifest": qualified_manifest,
                "manifest_sha": manifest_sha,
                "format_evidence": format_evidence,
                "survival_evidence": survival_evidence,
                "evidence_31": evidence_31,
                "score": score,
                "score_display": score_display,
            }
        )

    aggregate = aggregate_public_configuration([item["score"] for item in prepared])
    # No immutable public bundle or independent reproduction receipt exists at
    # this stage.  The shared aggregator therefore intentionally reports
    # partially_verified even though the private per-run scores are complete.
    if aggregate.verification != "partially_verified" or not all(item["score"].rankable for item in prepared):
        raise QualificationError("private cohort aggregation did not retain the expected gate")
    totals = [item["score"].overall for item in prepared]
    assert all(value is not None for value in totals)
    private_mean = sum(totals) / 3
    private_range = (min(totals), max(totals))
    aggregate_display = aggregate.display()
    cohort_summary = {
        "schema_version": "session-bench-claude-desktop-qualified-cohort-v1",
        "cohort_id": "+".join(run_id for run_id, _rep in COHORT),
        "configuration_id": "claude-desktop",
        "repetitions": [
            {
                "run_id": item["run_id"],
                "repetition": item["repetition"],
                "score": item["score_display"],
                "measurement_changes": item["measurement_changes"],
                "qualified_output": "capture/qualified-private-v1",
            }
            for item in prepared
        ],
        "stable_root": {
            "id": "stable-root-proof.json",
            "sha256": stable_proof_sha,
            "state": "measured",
            "repetition_count": 3,
        },
        "persistent_family": {
            "state": "complete",
            "required_roles": sorted(REQUIRED_FILES),
            "optional_ephemeral_companions": "validated when present; not required",
        },
        "private_score": {
            "public_score": False,
            "mean_overall": str(float(private_mean)) if private_mean.denominator != 1 else str(private_mean.numerator),
            "range_overall": [
                str(float(value)) if value.denominator != 1 else str(value.numerator)
                for value in private_range
            ],
            "shared_aggregator": aggregate_display,
            "verification": aggregate_display["verification"],
        },
        "policy_observations": {
            "revision.r1": "contradiction preserved from source measurement; prompt wrapper difference is visible in evidence",
            "attribution.usage": "unresolved source absence resolved to native_absent after complete persistent-family proof",
            "attribution.token_semantics": "unresolved source absence resolved to native_absent after complete persistent-family proof",
            "attribution.reconciliation": "native_absent preserved from source measurement",
            "work.actions_results": "3 native actions and 3 native results remain against the fixed 4-action workload denominator",
        },
        "claim_limit": "private qualification evidence only; no public rank or vendor claim",
    }

    results: list[dict[str, Any]] = []
    for item in prepared:
        output = item["run_root"] / "capture" / OUTPUT_NAME
        # All source validation and scoring are complete before the first write.
        stable_sha = _write(output / "stable-root-proof.json", stable_proof)
        if stable_sha != stable_proof_sha:
            raise QualificationError("stable-root proof digest changed during write")
        _write(output / "family-validation-qualified.json", item["qualified_family"])
        _write(output / "observer-qualified.json", item["observer"])
        _write(output / "native-manifest-qualified.json", item["qualified_manifest"])
        measurement_sha = _write(output / "measurement-qualified.json", item["qualified_measurement"])
        format_sha = _write(output / "format-evidence-qualified.json", item["format_evidence"])
        survival_sha = _write(output / "survival-evidence-qualified.json", item["survival_evidence"])
        evidence_sha = _write(output / "evidence-31-qualified.json", item["evidence_31"])
        score_document = {
            "schema_version": "session-bench-claude-desktop-private-score-v1",
            "run_id": item["run_id"],
            "configuration_id": "claude-desktop",
            "repetition": item["repetition"],
            "public_score": False,
            "rankable_private": True,
            "score": item["score_display"],
            "inputs": {
                "measurement": {"id": "measurement-qualified.json", "sha256": measurement_sha},
                "format_evidence": {"id": "format-evidence-qualified.json", "sha256": format_sha},
                "survival_evidence": {"id": "survival-evidence-qualified.json", "sha256": survival_sha},
                "evidence_31": {"id": "evidence-31-qualified.json", "sha256": evidence_sha},
            },
            "claim_limit": "private diagnostic score; no public rank or vendor claim",
        }
        score_sha = _write(output / "private-score.json", score_document)
        cohort_sha = _write(output / "cohort-qualification.json", cohort_summary)
        hashes = {
            "schema_version": "session-bench-claude-desktop-qualified-artifact-hashes-v1",
            "run_id": item["run_id"],
            "repetition": item["repetition"],
            "artifacts": [],
        }
        for path in sorted(output.rglob("*")):
            if path.is_file() and path.name != "artifact-hashes.json":
                hashes["artifacts"].append(
                    {
                        "path": path.relative_to(output).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha(path),
                    }
                )
        hashes_sha = _write(output / "artifact-hashes.json", hashes)
        result = {
            "schema_version": "session-bench-claude-desktop-qualified-run-v1",
            "run_id": item["run_id"],
            "configuration_id": "claude-desktop",
            "repetition": item["repetition"],
            "output": f"capture/{OUTPUT_NAME}",
            "status": "scorer_complete_private",
            "public_score": False,
            "overall": item["score_display"]["overall"],
            "categories": {
                category: row["score"]
                for category, row in item["score_display"]["categories"].items()
            },
            "revision_r1_state": next(
                row["state"] for row in item["qualified_measurement"]["metrics"] if row["id"] == "revision.r1"
            ),
            "attribution_states": {
                metric_id: next(row["state"] for row in item["qualified_measurement"]["metrics"] if row["id"] == metric_id)
                for metric_id in (
                    "attribution.usage",
                    "attribution.token_semantics",
                    "attribution.reconciliation",
                )
            },
            "stable_root_state": "measured",
            "measurement_sha256": measurement_sha,
            "format_evidence_sha256": format_sha,
            "survival_evidence_sha256": survival_sha,
            "evidence_31_sha256": evidence_sha,
            "private_score_sha256": score_sha,
            "cohort_qualification_sha256": cohort_sha,
            "artifact_hashes_sha256": hashes_sha,
        }
        # The result itself is printed; the canonical result is also bound by
        # the hash ledger through the files above.  Do not add a second mutable
        # result file whose hash would create a cycle.
        results.append(result)
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        help="repeat only to spell the exact three-run cohort; omitted uses the canonical cohort",
    )
    args = parser.parse_args()
    try:
        if args.run_ids is None:
            pairs = COHORT
        else:
            expected = [run_id for run_id, _rep in COHORT]
            if args.run_ids != expected:
                raise QualificationError("--run-id must list the exact cohort in repetition order")
            pairs = COHORT
        qualify(pairs)
    except Exception as exc:
        print(f"qualify invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
