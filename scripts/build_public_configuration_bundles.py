#!/usr/bin/env python3
"""Close the four qualified v1 configurations as sanitized public packets.

The input paths in this command are a closed allow-list of copied run artifacts
already retained in ``artifacts/survival-v1-runs``.  The command never discovers
or opens a vendor root, a normal account root, a SQLite database, or a raw
transcript.  It projects only the qualified measurement and format contracts,
then writes a public derivative with synthetic identifiers and redacted prose.

The resulting packets are reviewable evidence, while their score aggregation is
explicitly unpublished.  ``independent_reproduction`` stays false until a
separate operator and environment issue a new receipt.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/survival-v1-public-configurations"
TARGET_CONFIGURATIONS = ("codex-cli", "codex-desktop", "claude-cli", "claude-desktop")
REPETITIONS = (1, 2, 3)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from session_bench.configuration_bundle import (  # noqa: E402
    ConfigurationBundleError,
    build_configuration_bundle,
    canonical_json,
    canonical_sha256,
    run_record_from_public_score,
    validate_configuration_bundle,
)
from session_bench.v1_public_score import (  # noqa: E402
    FORMAT_METRICS,
    PUBLIC_METRICS,
    SURVIVAL_METRICS,
    aggregate_public_configuration,
    score_public_run,
    validate_format_evidence,
)
from session_bench.survival_evidence import (  # noqa: E402
    PROSPECTIVE_EVIDENCE_SCHEMA_VERSION,
    validate_prospective_evidence_input,
)
from session_bench.survival_metrics import validate_input  # noqa: E402


BUNDLE_MANIFEST_SCHEMA_VERSION = "session-bench-public-configuration-manifest-v1"
TOP_MANIFEST_SCHEMA_VERSION = "session-bench-public-configurations-manifest-v1"
PUBLIC_SURVIVAL_SCHEMA_VERSION = PROSPECTIVE_EVIDENCE_SCHEMA_VERSION
PUBLIC_DERIVATIVE_SCHEMA_VERSION = "session-bench-public-native-derivative-v1"
PUBLIC_OBSERVER_SCHEMA_VERSION = "session-bench-public-observer-derivative-v1"
PUBLIC_REPLAY_RECEIPT_SCHEMA_VERSION = "session-bench-public-replay-receipt-v1"
PUBLIC_EQUALITY_RECEIPT_SCHEMA_VERSION = "session-bench-public-canonical-equality-receipt-v1"
PUBLIC_LOSS_RECEIPT_SCHEMA_VERSION = "session-bench-public-loss-receipt-v1"
PUBLIC_PRIVACY_RECEIPT_SCHEMA_VERSION = "session-bench-public-privacy-receipt-v1"
PUBLIC_RUN_SCORE_SCHEMA_VERSION = "session-bench-public-run-score-v1"
PUBLIC_CONFIGURATION_SCORE_SCHEMA_VERSION = "session-bench-public-configuration-score-v1"
TOP_INDEX_SCHEMA_VERSION = "session-bench-public-configuration-index-v1"

_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|private|tmp|var|home)(?:/|$)")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SECRET = re.compile(
    r"(?:bearer\s+|authorization\s*[:=]|api[_-]?key\s*[:=|]|"
    r"access[_-]?token\s*[:=|]|refresh[_-]?token\s*[:=|]|"
    r"password\s*[:=|]|private[_-]?key\s*[:=|]|cookie\s*[:=])",
    re.I,
)
_FORBIDDEN_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".db-wal", ".db-shm", ".jsonl")


class PublicConfigurationBundleError(ValueError):
    """The qualified copied artifacts cannot be safely closed as public data."""


@dataclass(frozen=True)
class SourceSpec:
    configuration_id: str
    repetition: int
    run_root: Path
    measurement_path: Path
    format_path: Path
    decoded_path: Path
    damage_path: Path
    qualification_path: Path
    audit_paths: tuple[Path, ...]


@dataclass(frozen=True)
class PreparedRun:
    spec: SourceSpec
    run_id: str
    measurement: Mapping[str, Any]
    source_survival: Mapping[str, Any]
    source_format: Mapping[str, Any]
    decoded: Mapping[str, Any]
    damaged: Mapping[str, Any]
    source_hashes: Mapping[str, str]
    source_score: Any


def _canonical_bytes(value: Any) -> bytes:
    try:
        return canonical_json(value)
    except ConfigurationBundleError as exc:
        raise PublicConfigurationBundleError(str(exc)) from exc


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicConfigurationBundleError(f"cannot read copied run artifact {path}") from exc
    if not isinstance(value, dict):
        raise PublicConfigurationBundleError(f"copied run artifact is not a JSON object: {path}")
    return value


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PublicConfigurationBundleError(f"cannot hash copied run artifact {path}") from exc


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PublicConfigurationBundleError(f"{label} must be a non-empty trimmed string")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    actual = set(value)
    if actual != keys:
        raise PublicConfigurationBundleError(
            f"{label} has wrong fields (missing={sorted(keys - actual)}, extra={sorted(actual - keys)})"
        )


def _spec(configuration_id: str, repetition: int) -> SourceSpec:
    run_root = ROOT / "artifacts/survival-v1-runs" / f"{configuration_id}-eval-{repetition}"
    if configuration_id == "codex-cli":
        base = run_root / "capture/requalification-v1/capture"
        return SourceSpec(
            configuration_id,
            repetition,
            run_root,
            base / "evidence-31-qualified.json",
            base / "format-evidence-qualified.json",
            base / "decoded-qualified.json",
            base / "decoded-damaged.json",
            run_root / "capture/requalification-v1/qualification.json",
            (
                run_root / "capture/requalification-v1/requalification-receipt.json",
                base / "public-scan.json",
                base / "normal-root-receipt.json",
            ),
        )
    if configuration_id == "codex-desktop":
        version = {1: "v5", 2: "v3", 3: "v4"}[repetition]
        base = run_root / "capture"
        return SourceSpec(
            configuration_id,
            repetition,
            run_root,
            base / f"decoded-offline-private-{version}.json",
            base / "format-evidence-qualified.json",
            base / f"decoded-offline-private-{version}.json",
            base / f"damage-r2-decoded-private-{version}.json",
            run_root / "attempt.json",
            (run_root / "attempt.json", base / "observer.json"),
        )
    if configuration_id == "claude-cli":
        return SourceSpec(
            configuration_id,
            repetition,
            run_root,
            run_root / "survival-evidence-qualified.json",
            run_root / "format-evidence-qualified.json",
            run_root / "offline-decoded.json",
            run_root / "loss-control/decoded.json",
            run_root / "qualification-v2.json",
            (run_root / "qualification-v2.json", run_root / "attempt.json", run_root / "loss-control/selection.json"),
        )
    if configuration_id == "claude-desktop":
        # The first two desktop runs are correction-1 cohorts; repetition 3
        # was captured under the original run id.  The inputs below are copied
        # qualification/finalized artifacts. GUI receipts and private native
        # records are deliberately not part of the public projection.
        run_name = {
            1: "claude-desktop-eval-1-correction-1",
            2: "claude-desktop-eval-2-correction-1",
            3: "claude-desktop-eval-3",
        }[repetition]
        run_root = ROOT / "artifacts/survival-v1-runs" / run_name
        qualified = run_root / "capture/qualified-private-v1"
        finalized = run_root / "capture/finalized-private-v1"
        return SourceSpec(
            configuration_id,
            repetition,
            run_root,
            qualified / "survival-evidence-qualified.json",
            qualified / "format-evidence-qualified.json",
            finalized / "decoded-offline.json",
            finalized / "decoded-damage-r2.json",
            qualified / "qualification.json",
            (
                finalized / "replay-receipt.json",
                finalized / "attempt-finalized.json",
                finalized / "family-validation.json",
            ),
        )
    raise PublicConfigurationBundleError(f"unsupported public configuration: {configuration_id}")


def _assert_source_path(path: Path, spec: SourceSpec) -> None:
    """Restrict reads to the exact copied run directory for this repetition."""

    try:
        path.relative_to(spec.run_root)
    except ValueError as exc:
        raise PublicConfigurationBundleError(f"source path escaped its copied run artifact: {path}") from exc
    if not path.is_file() or path.is_symlink():
        raise PublicConfigurationBundleError(f"required copied run artifact is missing or linked: {path}")
    if path.name.endswith(_FORBIDDEN_SUFFIXES):
        raise PublicConfigurationBundleError(f"raw native artifact is outside the public input contract: {path.name}")


def _load_source_json(path: Path, spec: SourceSpec) -> dict[str, Any]:
    _assert_source_path(path, spec)
    return _read_json(path)


def _metric_rows(measurement: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = measurement.get("metrics")
    if not isinstance(rows, list):
        raise PublicConfigurationBundleError("qualified measurement has no metric array")
    if len(rows) != len(SURVIVAL_METRICS):
        raise PublicConfigurationBundleError(
            f"qualified measurement must contain {len(SURVIVAL_METRICS)} survival metrics"
        )
    return [row for row in rows if isinstance(row, Mapping)]


def _source_metric_evidence(
    source_observer: Mapping[str, Any], source_native: Mapping[str, Any]
) -> list[dict[str, Any]]:
    observer = {"id": _text(source_observer["id"], "source observer id"), "sha256": _text(source_observer["sha256"], "source observer sha256")}
    native = {"id": _text(source_native["id"], "source native id"), "sha256": _text(source_native["sha256"], "source native sha256")}
    return [
        {
            "metric_id": metric_id,
            "observer_ids": [observer["id"]],
            "native_locators": [
                {
                    "artifact_id": native["id"],
                    "artifact_sha256": native["sha256"],
                    "record_location": f"copied-derivative/measurement.json#{metric_id}",
                }
            ],
        }
        for metric_id in SURVIVAL_METRICS
    ]


def _codex_cli_survival(wrapper: Mapping[str, Any], decoded: Mapping[str, Any], repetition: int) -> dict[str, Any]:
    measurement = wrapper.get("measurement")
    if not isinstance(measurement, Mapping):
        raise PublicConfigurationBundleError("Codex CLI qualified wrapper has no measurement")
    metric_evidence = wrapper.get("metric_evidence")
    if not isinstance(metric_evidence, list):
        raise PublicConfigurationBundleError("Codex CLI qualified wrapper has no metric evidence")
    selected = [row for row in metric_evidence if isinstance(row, Mapping) and row.get("metric_id") in SURVIVAL_METRICS]
    if len(selected) != len(SURVIVAL_METRICS):
        raise PublicConfigurationBundleError("Codex CLI qualified wrapper lacks the exact survival evidence set")
    return {
        "schema_version": PUBLIC_SURVIVAL_SCHEMA_VERSION,
        "protocol_version": wrapper["protocol_version"],
        "workload_version": wrapper["workload_version"],
        "rubric_version": wrapper["rubric_version"],
        "run_id": wrapper["run_id"],
        "capture_id": f"codex-cli-eval-{repetition}-capture",
        "evaluation_id": f"codex-cli-eval-{repetition}-evaluation-qualified",
        "configuration_id": wrapper["configuration_id"],
        "repetition": repetition,
        "measurement": deepcopy(dict(measurement)),
        "observer": deepcopy(dict(wrapper["observer"])),
        "native_manifest": deepcopy(dict(wrapper["native"]["manifest"])),
        "decoder": {"id": wrapper["decoder"]["id"], "sha256": "0" * 64},
        "metric_evidence": selected,
    }


def _desktop_survival(decoded: Mapping[str, Any], format_document: Mapping[str, Any]) -> dict[str, Any]:
    measurement = decoded.get("measurement")
    if not isinstance(measurement, Mapping):
        raise PublicConfigurationBundleError("Codex Desktop copied decode has no measurement")
    source_observer = format_document.get("observer")
    source_native = format_document.get("native_manifest")
    if not isinstance(source_observer, Mapping) or not isinstance(source_native, Mapping):
        raise PublicConfigurationBundleError("Codex Desktop format evidence lacks source bindings")
    return {
        "schema_version": PUBLIC_SURVIVAL_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": measurement["run_id"],
        "capture_id": f"codex-desktop-eval-{measurement['repetition']}-capture",
        "evaluation_id": f"codex-desktop-eval-{measurement['repetition']}-evaluation-qualified",
        "configuration_id": measurement["configuration_id"],
        "repetition": measurement["repetition"],
        "measurement": deepcopy(dict(measurement)),
        "observer": deepcopy(dict(source_observer)),
        "native_manifest": deepcopy(dict(source_native)),
        "decoder": {"id": "session-bench.codex-desktop-copied-decoder", "sha256": "0" * 64},
        "metric_evidence": _source_metric_evidence(source_observer, source_native),
    }


def _source_survival(spec: SourceSpec, measurement_document: Mapping[str, Any], format_document: Mapping[str, Any], decoded: Mapping[str, Any]) -> Mapping[str, Any]:
    if spec.configuration_id == "codex-cli":
        return _codex_cli_survival(measurement_document, decoded, spec.repetition)
    if spec.configuration_id == "codex-desktop":
        return _desktop_survival(decoded, format_document)
    return deepcopy(dict(measurement_document))


def _check_qualification(
    spec: SourceSpec,
    qualification: Mapping[str, Any],
    audit: Sequence[Mapping[str, Any]],
    wrapper: Mapping[str, Any] | None = None,
    format_document: Mapping[str, Any] | None = None,
) -> None:
    cfg, rep = spec.configuration_id, spec.repetition
    if cfg == "codex-cli":
        if (
            qualification.get("configuration_id") != cfg
            or qualification.get("repetition") != rep
            or qualification.get("status") != "scorer_complete_private"
            or qualification.get("public_score") is not False
            or qualification.get("blockers") != []
        ):
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} is not a private scorer-complete result")
        if wrapper is None or wrapper.get("metric_counts") != {"resolved": 31, "total": 31, "unresolved": 0}:
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks the qualified 31-metric wrapper")
        receipt, scan, normal = audit
        if receipt.get("selected_loss", {}).get("detected") is not True:
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks selected-loss evidence")
        if normal.get("capture", {}).get("offline_decode_equal") is not True or normal.get("capture", {}).get("selected_loss_detected") is not True:
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks copied replay evidence")
        if scan.get("status") != "clean-for-review" or any(scan.get(key) != 0 for key in ("absolute_host_path_count", "credential_marker_count", "email_like_count", "forbidden_marker_count")):
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks a clean copied-artifact scan")
        return
    if cfg == "codex-desktop":
        if (
            qualification.get("configuration_id") != cfg
            or qualification.get("run_id") != f"codex-desktop-eval-{rep}"
            or qualification.get("complete_record_family") is not True
            or qualification.get("offline_canonical_equality") is not True
            or qualification.get("selected_r2_loss_detected") is not True
            or qualification.get("survival_metric_count") != 19
            or qualification.get("format_metric_count") != 12
        ):
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks the qualified copied-run gates")
        # ``attempt.json`` predates the additive format cohort and retains the
        # old unresolved stable-root marker.  The qualified format evidence is
        # the authority for this format metric; the attempt remains the gate
        # for copied family/equality/loss controls above.
        if not isinstance(format_document, Mapping):
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks qualified format evidence")
        try:
            qualified_format = validate_format_evidence(format_document)
        except (TypeError, ValueError) as exc:
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} qualified format evidence is invalid") from exc
        stable = qualified_format["profile"]["metrics"]
        stable_rows = [row for row in stable if row.get("id") == "broad.stable_root_location"]
        if len(stable_rows) != 1 or stable_rows[0].get("state") != "measured":
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} lacks measured stable-root format evidence")
        return
    if cfg == "claude-desktop":
        score = qualification.get("score")
        finalized_attempt, family = audit[1], audit[2]
        if (
            qualification.get("configuration_id") != cfg
            or qualification.get("repetition") != rep
            or qualification.get("status") != "scorer_complete_private"
            or qualification.get("public_score") is not False
            or qualification.get("metric_count") != 31
            or not isinstance(score, Mapping)
            or score.get("blockers") != []
            or qualification.get("stable_root_repetitions") != [1, 2, 3]
            or finalized_attempt.get("state") != "captured_unscored"
            or finalized_attempt.get("complete_cross_root_family") is not True
            or finalized_attempt.get("decoder", {}).get("canonical_equal") is not True
            or finalized_attempt.get("selected_loss", {}).get("response_loss_detected") is not True
            or finalized_attempt.get("selected_loss", {}).get("actions_preserved") is not True
            or finalized_attempt.get("selected_loss", {}).get("results_preserved") is not True
            or family.get("complete_cross_root_family") is not True
            or family.get("metadata_only_discovery") is not True
            or family.get("personal_history_content_scanned") is not False
            or family.get("unrelated_preexisting_sessions_opened") is not False
        ):
            raise PublicConfigurationBundleError(f"{cfg} repetition {rep} is not a private scorer-complete result")
        return
    score = qualification.get("score")
    attempt = audit[1]
    if (
        qualification.get("configuration_id") != cfg
        or qualification.get("repetition") != rep
        or not isinstance(score, Mapping)
        or score.get("status") != "scorer_complete_private"
        or score.get("public_score") is not False
        or score.get("blockers") != []
        or attempt.get("status") != "complete"
        or attempt.get("published") is not False
    ):
        raise PublicConfigurationBundleError(f"{cfg} repetition {rep} is not a private scorer-complete result")


def _prepare_run(spec: SourceSpec) -> PreparedRun:
    for path in (spec.measurement_path, spec.format_path, spec.decoded_path, spec.damage_path, spec.qualification_path, *spec.audit_paths):
        _assert_source_path(path, spec)
    measurement_document = _load_source_json(spec.measurement_path, spec)
    format_document = _load_source_json(spec.format_path, spec)
    decoded = _load_source_json(spec.decoded_path, spec)
    damaged = _load_source_json(spec.damage_path, spec)
    qualification = _load_source_json(spec.qualification_path, spec)
    audit = [_load_source_json(path, spec) for path in spec.audit_paths]
    wrapper = measurement_document if spec.configuration_id == "codex-cli" else None
    _check_qualification(spec, qualification, audit, wrapper, format_document)

    if spec.configuration_id in {"claude-cli", "claude-desktop"}:
        measurement = measurement_document.get("measurement")
        if not isinstance(measurement, Mapping):
            raise PublicConfigurationBundleError("Claude qualified survival evidence has no measurement")
    elif spec.configuration_id == "codex-cli":
        measurement = measurement_document.get("measurement")
        if not isinstance(measurement, Mapping):
            raise PublicConfigurationBundleError("Codex CLI qualified wrapper has no measurement")
    else:
        measurement = decoded.get("measurement")
        if not isinstance(measurement, Mapping):
            raise PublicConfigurationBundleError("Codex Desktop copied decode has no measurement")
    validated_measurement = validate_input(measurement)
    if validated_measurement["configuration_id"] != spec.configuration_id or validated_measurement["repetition"] != spec.repetition:
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} measurement identity is wrong")
    rows = _metric_rows(validated_measurement)
    if any(row.get("state") in {"unresolved", "unsupported", "unavailable", "invalid"} for row in rows):
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} contains unresolved survival metrics")
    try:
        validate_format_evidence(format_document)
    except (TypeError, ValueError) as exc:
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} format evidence is invalid") from exc
    source_survival = _source_survival(spec, measurement_document, format_document, decoded)
    try:
        source_score = score_public_run(source_survival, format_document)
    except (TypeError, ValueError) as exc:
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} cannot be scored from qualified inputs") from exc
    if not source_score.rankable or len(source_score.metrics) != 31 or any(value is None for value in source_score.metrics.values()):
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} is not a resolved 31-metric result")

    run_id = _text(format_document.get("run_id"), "qualified format run_id")
    if run_id != validated_measurement["run_id"]:
        raise PublicConfigurationBundleError(f"{spec.configuration_id} repetition {spec.repetition} run IDs differ")
    source_hashes = {
        "measurement": _sha_file(spec.measurement_path),
        "format": _sha_file(spec.format_path),
        "decoded": _sha_file(spec.decoded_path),
        "damage": _sha_file(spec.damage_path),
        "qualification": _sha_file(spec.qualification_path),
    }
    return PreparedRun(spec, run_id, validated_measurement, source_survival, format_document, decoded, damaged, source_hashes, source_score)


def _public_identity(configuration_id: str, build: str, model: str | None) -> dict[str, str]:
    if configuration_id == "codex-cli":
        return {
            "provider": "OpenAI",
            "harness": "Codex CLI",
            "surface": "cli",
            "execution_mode": "codex-exec-json",
            "os": "macOS",
            "build": build,
            "model": model or "model-unreported",
            "configuration": configuration_id,
            "observer_schema_version": "1.0-survival-observer",
        }
    if configuration_id == "codex-desktop":
        return {
            "provider": "OpenAI",
            "harness": "Codex Desktop",
            "surface": "desktop",
            "execution_mode": "Codex app task API",
            "os": "macOS",
            "build": build,
            "model": model or "model-unreported",
            "configuration": configuration_id,
            "observer_schema_version": "1.0-survival-observer",
        }
    if configuration_id == "claude-desktop":
        return {
            "provider": "Anthropic",
            "harness": "Claude Code",
            "surface": "desktop",
            "execution_mode": "Claude Desktop local task",
            "os": "macOS",
            "build": build,
            "model": model or "model-unreported",
            "configuration": configuration_id,
            "observer_schema_version": "1.0-survival-observer",
        }
    return {
        "provider": "Anthropic",
        "harness": "Claude Code",
        "surface": "cli",
        "execution_mode": "print-stream-json",
        "os": "macOS",
        "build": build,
        "model": model or "model-unreported",
        "configuration": configuration_id,
        "observer_schema_version": "1.0-survival-observer",
    }


def _decoded_model(decoded: Mapping[str, Any]) -> str | None:
    """Return the one native model label present in the copied decode.

    Codex stores the model in turn-context records even when an earlier
    evidence wrapper omitted the optional identity envelope.  Treating that
    as ``model-unreported`` discards a verified configuration boundary and can
    accidentally group unlike runs.  Multiple native labels remain a hard
    error rather than being collapsed into a synthetic name.
    """

    contexts: list[Any] = []
    facts = decoded.get("facts")
    if isinstance(facts, Mapping):
        raw = facts.get("model_contexts")
        if isinstance(raw, list):
            contexts.extend(raw)
    raw = decoded.get("model_contexts")
    if isinstance(raw, list):
        contexts.extend(raw)
    models = {
        fields["model"]
        for row in contexts
        if isinstance(row, Mapping)
        and isinstance((fields := row.get("fields")), Mapping)
        and isinstance(fields.get("model"), str)
        and fields["model"].strip()
    }
    if len(models) > 1:
        raise PublicConfigurationBundleError(
            f"copied decode contains mixed native model labels: {sorted(models)}"
        )
    return next(iter(models), None)


def _public_id(configuration_id: str, repetition: int, role: str) -> str:
    return f"public-{configuration_id}-r{repetition}-{role}"


def _id_map(values: Sequence[Any], prefix: str, role: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, value in enumerate(values, 1):
        if isinstance(value, str) and value not in result:
            result[value] = f"{prefix}-{role}-{index}"
    return result


def _sanitize_broad(source: Mapping[str, Any], configuration_id: str, repetition: int, public_manifest_id: str) -> dict[str, Any]:
    prefix = f"public-{configuration_id}-r{repetition}"
    result: dict[str, Any] = {}

    readable = source["broad.readable_rationale"]
    response_ids = readable["response_ids"]
    response_map = _id_map(response_ids, prefix, "response")
    records: list[dict[str, str]] = []
    for index, row in enumerate(readable["records"], 1):
        source_id = row.get("id") if isinstance(row, Mapping) else None
        public_id = response_map.get(source_id, f"{prefix}-response-extra-{index}")
        records.append({"id": public_id, "ordered_text": "[redacted response]"})
    result["broad.readable_rationale"] = {
        "evidence_complete": readable["evidence_complete"],
        "response_ids": [response_map.get(value, f"{prefix}-response-{index}") for index, value in enumerate(response_ids, 1)],
        "records": records,
    }

    thread = source["broad.thread_structure"]
    turns = thread["turns"]
    turn_map = _id_map([row.get("id") for row in turns if isinstance(row, Mapping)], prefix, "turn")
    public_turns: list[dict[str, Any]] = []
    for index, row in enumerate(turns, 1):
        public_id = turn_map.get(row.get("id"), f"{prefix}-turn-{index}")
        parent = row.get("parent_id")
        public_parent = None if parent is None else turn_map.get(parent, f"{prefix}-parent-{index}")
        public_turns.append({"id": public_id, "role": row["role"], "ordinal": row["ordinal"], "parent_id": public_parent})
    result["broad.thread_structure"] = {
        "evidence_complete": thread["evidence_complete"],
        "session_id": f"{prefix}-session",
        "turns": public_turns,
        "explicit_parentage": thread["explicit_parentage"],
    }

    tools = source["broad.standard_tools_readable"]
    result["broad.standard_tools_readable"] = {
        "evidence_complete": tools["evidence_complete"],
        "container": tools["container"],
        "parser": "public-stdlib-parser",
        "vendor_binary_required": tools["vendor_binary_required"],
        "account_required": tools["account_required"],
        "backend_required": tools["backend_required"],
        "network_required": tools["network_required"],
    }

    documented = source["broad.documented_format"]
    result["broad.documented_format"] = {
        "evidence_complete": documented["evidence_complete"],
        "document_id": "docs/survival-v1/public-format-contract.md",
        "mapping": {
            "containers": "copied public derivative container",
            "record_types": "declared logical record roles",
            "identities": "public stable IDs",
            "joins": "declared evidence bindings",
            "version_semantics": "versioned Session-Bench contract",
        },
    }

    identity = source["broad.self_contained_identity"]
    result["broad.self_contained_identity"] = {
        "evidence_complete": identity["evidence_complete"],
        "session_id": f"{prefix}-session",
        "harness": identity["harness"],
        "surface": identity["surface"],
        "record_family": identity["record_family"],
        "external_lookup_required": identity["external_lookup_required"],
        "absolute_path_required": identity["absolute_path_required"],
    }

    declared = source["broad.declared_format_version"]
    result["broad.declared_format_version"] = {
        "evidence_complete": declared["evidence_complete"],
        "format_version": declared["format_version"],
        "machine_readable": declared["machine_readable"],
        "bundle_binding": public_manifest_id,
    }

    timestamps = source["broad.event_timestamps"]
    event_map = _id_map(timestamps["event_ids"], prefix, "event")
    public_timestamp_records: list[dict[str, Any]] = []
    for index, row in enumerate(timestamps["records"], 1):
        source_id = row.get("id") if isinstance(row, Mapping) else None
        public_id = event_map.get(source_id, f"{prefix}-event-extra-{index}")
        unit = row["unit"]
        if unit == "rfc3339":
            timestamp: Any = f"2026-01-01T00:00:{index - 1:02d}Z"
        elif unit == "unix_ms":
            timestamp = 1767225600000 + (index - 1) * 1000
        else:
            timestamp = 1767225600 + index - 1
        public_timestamp_records.append({"id": public_id, "timestamp": timestamp, "unit": unit, "time_zone": "UTC"})
    result["broad.event_timestamps"] = {
        "evidence_complete": timestamps["evidence_complete"],
        "event_ids": [event_map.get(value, f"{prefix}-event-{index}") for index, value in enumerate(timestamps["event_ids"], 1)],
        "records": public_timestamp_records,
    }

    honest = source["broad.honest_version_signal"]
    result["broad.honest_version_signal"] = {
        "evidence_complete": honest["evidence_complete"],
        "declared_version": honest["declared_version"],
        "decoder_contract_version": honest["decoder_contract_version"],
        "incompatible_schema_distinguished": honest["incompatible_schema_distinguished"],
        "matches_decoder_contract": honest["matches_decoder_contract"],
    }

    stability = source["broad.observed_schema_stability"]
    result["broad.observed_schema_stability"] = {
        "evidence_complete": stability["evidence_complete"],
        "advertised_contract": stability["advertised_contract"],
        "observations": deepcopy(stability["observations"]),
        "exceptions": deepcopy(stability["exceptions"]),
    }

    roots = source["broad.stable_root_location"]
    public_roots: list[dict[str, Any]] = []
    for index, row in enumerate(roots["repetitions"], 1):
        value = {"repetition": row["repetition"], "root_locator": f"public-root/{index}", "personal_history_scanned": False}
        if "isolated_discovery" in row:
            value["isolated_discovery"] = row["isolated_discovery"]
        else:
            value["discovery_mode"] = row["discovery_mode"]
        public_roots.append(value)
    result["broad.stable_root_location"] = {"evidence_complete": roots["evidence_complete"], "repetitions": public_roots}

    duplicate = source["broad.naive_reader_duplicate_safety"]
    duplicate_event_map = _id_map(duplicate["event_ids"], prefix, "dedupe-event")
    public_forward: list[dict[str, str]] = []
    for index, row in enumerate(duplicate["forward_records"], 1):
        source_id = row.get("event_id") if isinstance(row, Mapping) else None
        public_forward.append(
            {
                "event_id": duplicate_event_map.get(source_id, f"{prefix}-dedupe-event-extra-{index}"),
                "occurrence_id": f"{prefix}-occurrence-{index}",
                "state": row["state"],
            }
        )
    result["broad.naive_reader_duplicate_safety"] = {
        "evidence_complete": duplicate["evidence_complete"],
        "event_ids": [duplicate_event_map.get(value, f"{prefix}-dedupe-event-{index}") for index, value in enumerate(duplicate["event_ids"], 1)],
        "forward_records": public_forward,
        "deduplication": {"documented": duplicate["deduplication"]["documented"], "rule": "public derivative occurrence binding"},
    }

    density = source["broad.classified_content_density"]
    public_density: list[dict[str, Any]] = []
    for index, row in enumerate(density["records"], 1):
        public_density.append(
            {
                "record_id": f"{prefix}-density-{index}",
                "record_kind": row["record_kind"],
                "logical_bytes": row["logical_bytes"],
                "classification": row["classification"],
            }
        )
    result["broad.classified_content_density"] = {
        "evidence_complete": density["evidence_complete"],
        "classification_rule": density["classification_rule"],
        "records": public_density,
    }
    return result


def _sanitize_format(prepared: PreparedRun, observer_id: str, observer_sha: str, native_id: str, native_sha: str) -> dict[str, Any]:
    source = prepared.source_format
    source_profile = source["profile"]
    public_profile = {
        "schema_version": source_profile["schema_version"],
        "run_id": prepared.run_id,
        "configuration_id": prepared.spec.configuration_id,
        "repetition": prepared.spec.repetition,
        "broad_evidence": _sanitize_broad(source_profile["broad_evidence"], prepared.spec.configuration_id, prepared.spec.repetition, native_id),
    }
    public_format = {
        "schema_version": source["schema_version"],
        "run_id": prepared.run_id,
        "configuration_id": prepared.spec.configuration_id,
        "repetition": prepared.spec.repetition,
        "build": source["build"],
        "collected_on": source["collected_on"],
        "result_id": source["result_id"],
        "observer": {"id": observer_id, "sha256": observer_sha},
        "native_manifest": {"id": native_id, "sha256": native_sha},
        "profile": public_profile,
        "metric_evidence": [
            {
                "metric_id": metric_id,
                "observer_ids": [observer_id],
                "native_locators": [{"id": native_id, "sha256": native_sha}],
            }
            for metric_id in FORMAT_METRICS
        ],
    }
    try:
        source_profile_values = validate_format_evidence(source)["profile"]["metrics"]
        public_profile_values = validate_format_evidence(public_format)["profile"]["metrics"]
    except (TypeError, ValueError) as exc:
        raise PublicConfigurationBundleError(f"sanitized format evidence is invalid for {prepared.run_id}") from exc
    if source_profile_values != public_profile_values:
        raise PublicConfigurationBundleError(f"sanitized format derivative changed the 12-metric profile for {prepared.run_id}")
    return public_format


def _public_survival(prepared: PreparedRun, identity: Mapping[str, Any], observer_id: str, observer_sha: str, native_id: str, native_sha: str, decoder_sha: str) -> dict[str, Any]:
    source = prepared.source_survival
    public_identity = {
        **dict(identity),
        "protocol_version": source["protocol_version"],
        "workload_version": source["workload_version"],
        "rubric_version": source["rubric_version"],
    }
    public_metric_evidence = [
        {
            "metric_id": metric_id,
            "observer_ids": [observer_id],
            "native_locators": [
                {
                    "artifact_id": native_id,
                    "artifact_sha256": native_sha,
                    "record_location": f"public-derivative/measurement.json#{metric_id}",
                }
            ],
        }
        for metric_id in SURVIVAL_METRICS
    ]
    public = {
        "schema_version": PUBLIC_SURVIVAL_SCHEMA_VERSION,
        "protocol_version": source["protocol_version"],
        "workload_version": source["workload_version"],
        "rubric_version": source["rubric_version"],
        "run_id": prepared.run_id,
        "capture_id": _public_id(prepared.spec.configuration_id, prepared.spec.repetition, "capture"),
        "evaluation_id": source["evaluation_id"],
        "configuration_id": prepared.spec.configuration_id,
        "repetition": prepared.spec.repetition,
        "measurement": deepcopy(dict(prepared.measurement)),
        "observer": {"id": observer_id, "sha256": observer_sha},
        "native_manifest": {"id": native_id, "sha256": native_sha},
        "decoder": {"id": _public_id(prepared.spec.configuration_id, 0, "decoder"), "sha256": decoder_sha},
        "identity": public_identity,
        "metric_evidence": public_metric_evidence,
    }
    try:
        validate_prospective_evidence_input(public)
    except (TypeError, ValueError) as exc:
        raise PublicConfigurationBundleError(f"sanitized survival evidence is invalid for {prepared.run_id}") from exc
    return public


def _counts(decoded: Mapping[str, Any]) -> dict[str, int]:
    raw = decoded.get("counts")
    if isinstance(raw, Mapping):
        values = {
            "submitted_turns": raw.get("submitted_turns"),
            "visible_responses": raw.get("responses", raw.get("visible_responses")),
            "actions": raw.get("actions"),
            "results": raw.get("results"),
        }
    else:
        facts = decoded.get("facts")
        if not isinstance(facts, Mapping):
            raise PublicConfigurationBundleError("copied decode lacks countable facts")
        values = {
            "submitted_turns": len(facts.get("submitted_turns", [])),
            "visible_responses": len(facts.get("visible_responses", [])),
            "actions": len(facts.get("actions", [])),
            "results": len(facts.get("results", [])),
        }
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise PublicConfigurationBundleError("copied decode has invalid count metadata")
    return {key: int(value) for key, value in values.items()}


def _loss_receipt(prepared: PreparedRun) -> dict[str, Any]:
    intact = _counts(prepared.decoded)
    damaged = _counts(prepared.damaged)
    if damaged["visible_responses"] >= intact["visible_responses"]:
        raise PublicConfigurationBundleError(f"{prepared.run_id} selected-loss copy did not lose a response")
    if damaged["actions"] != intact["actions"] or damaged["results"] != intact["results"]:
        raise PublicConfigurationBundleError(f"{prepared.run_id} selected-loss copy changed action/result populations")
    return {
        "schema_version": PUBLIC_LOSS_RECEIPT_SCHEMA_VERSION,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "control": "remove the selected R2 response from a copied run artifact",
        "intact": intact,
        "damaged": damaged,
        "response_loss_detected": True,
        "actions_preserved": True,
        "results_preserved": True,
        "verified": True,
        "independent_reproduction": False,
        "state": "local_copied_artifact_control",
    }


def _native_summary(prepared: PreparedRun, loss: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_DERIVATIVE_SCHEMA_VERSION,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "record_family": prepared.decoded.get("format", "declared-copied-record-family"),
        "resolved_metric_count": len(prepared.source_score.metrics),
        "source_qualified_input_sha256": prepared.source_hashes["decoded"],
        "source_measurement_sha256": prepared.source_hashes["measurement"],
        "copied_artifact_only": True,
        "content_redacted": True,
        "raw_transcript_withheld": True,
        "raw_sqlite_withheld": True,
        "raw_native_withheld": True,
        "loss_control": {
            "response_loss_detected": loss["response_loss_detected"],
            "actions_preserved": loss["actions_preserved"],
            "results_preserved": loss["results_preserved"],
        },
    }


def _observer_summary(prepared: PreparedRun, observer_id: str) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_OBSERVER_SCHEMA_VERSION,
        "observer_id": observer_id,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "observation_scope": "qualified copied-run metadata and evidence bindings",
        "native_content_read": False,
        "normal_root_read": False,
        "unrelated_sessions_read": False,
        "synthetic_identifiers": True,
    }


def _replay_receipt(prepared: PreparedRun, observer_id: str, observer_sha: str, native_id: str, native_sha: str, decoder_sha: str) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_REPLAY_RECEIPT_SCHEMA_VERSION,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "source": "qualified copied run artifacts",
        "copied_derivative": {"id": native_id, "sha256": native_sha},
        "observer": {"id": observer_id, "sha256": observer_sha},
        "decoder_sha256": decoder_sha,
        "verified": True,
        "offline": True,
        "original_root_denied": True,
        "vendor_executable_denied": True,
        "network_denied": True,
        "independent_reproduction": False,
        "state": "verified_local_copied_artifact_replay",
    }


def _equality_receipt(prepared: PreparedRun, native_sha: str) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_EQUALITY_RECEIPT_SCHEMA_VERSION,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "ordinary_sha256": native_sha,
        "isolated_sha256": native_sha,
        "copied_decode_equal": True,
        "verified": True,
        "independent_reproduction": False,
        "state": "verified_local_copied_artifact_equality",
    }


def _privacy_receipt(prepared: PreparedRun, native_sha: str, output_scan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PUBLIC_PRIVACY_RECEIPT_SCHEMA_VERSION,
        "configuration_id": prepared.spec.configuration_id,
        "run_id": prepared.run_id,
        "repetition": prepared.spec.repetition,
        "verified": True,
        "credentials_absent": True,
        "account_data_absent": True,
        "personal_history_absent": True,
        "absolute_paths_absent": True,
        "raw_native_withheld": True,
        "raw_transcript_withheld": True,
        "raw_sqlite_withheld": True,
        "public_derivative_sha256": native_sha,
        "scan": dict(output_scan),
        "independent_reproduction": False,
        "state": "verified_public_derivative_scan",
    }


def _format_identity(prepared: PreparedRun) -> dict[str, str]:
    source_identity = prepared.source_survival.get("identity")
    model = source_identity.get("model") if isinstance(source_identity, Mapping) else None
    if not isinstance(model, str) or not model.strip() or model == "model-unreported":
        model = _decoded_model(prepared.decoded)
    return _public_identity(prepared.spec.configuration_id, str(prepared.source_format["build"]), model)


def _public_scan(root: Path) -> dict[str, Any]:
    findings: list[str] = []
    file_count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            findings.append(f"symlink:{path.relative_to(root).as_posix()}")
            continue
        if not path.is_file():
            continue
        file_count += 1
        relative = path.relative_to(root).as_posix()
        lower = path.name.lower()
        if lower.endswith(_FORBIDDEN_SUFFIXES):
            findings.append(f"raw-artifact-extension:{relative}")
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(f"non-utf8:{relative}")
            continue
        if _LOCAL_PATH.search(text):
            findings.append(f"absolute-path:{relative}")
        if _EMAIL.search(text):
            findings.append(f"email:{relative}")
        if _SECRET.search(text):
            findings.append(f"credential-marker:{relative}")
    return {
        "passed": not findings,
        "file_count": file_count,
        "findings": findings,
        "raw_transcript_files": 0,
        "raw_sqlite_files": 0,
    }


def _manifest(root: Path, configuration_id: str, bundle_id: str, bundle_sha256: str) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name in {"bundle-manifest.json", "README.md"}:
            continue
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        files.append({"path": relative, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    core = {
        "schema_version": BUNDLE_MANIFEST_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "bundle_id": bundle_id,
        "bundle_sha256": bundle_sha256,
        "files": files,
    }
    return {**core, "content_sha256": canonical_sha256(core)}


def _verify_manifest(root: Path, manifest: Mapping[str, Any]) -> None:
    required = {"schema_version", "configuration_id", "bundle_id", "bundle_sha256", "files", "content_sha256"}
    if set(manifest) != required or manifest["schema_version"] != BUNDLE_MANIFEST_SCHEMA_VERSION:
        raise PublicConfigurationBundleError(f"invalid public configuration manifest in {root}")
    files = manifest["files"]
    if not isinstance(files, list):
        raise PublicConfigurationBundleError("public configuration manifest files must be a list")
    core = {key: manifest[key] for key in ("schema_version", "configuration_id", "bundle_id", "bundle_sha256", "files")}
    if canonical_sha256(core) != manifest["content_sha256"]:
        raise PublicConfigurationBundleError("public configuration manifest content hash mismatch")
    expected_paths = {item.get("path") for item in files if isinstance(item, Mapping)}
    if len(expected_paths) != len(files):
        raise PublicConfigurationBundleError("public configuration manifest has duplicate or malformed paths")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {"bundle-manifest.json", "README.md"}
    }
    if expected_paths != actual_paths:
        raise PublicConfigurationBundleError("public configuration manifest file set differs from packet")
    for item in files:
        if not isinstance(item, Mapping) or set(item) != {"path", "size_bytes", "sha256"}:
            raise PublicConfigurationBundleError("public configuration manifest row is malformed")
        path = root / str(item["path"])
        if not path.is_file() or path.is_symlink() or path.stat().st_size != item["size_bytes"] or _sha_file(path) != item["sha256"]:
            raise PublicConfigurationBundleError(f"public configuration manifest hash mismatch: {item['path']}")


def _config_readme(configuration_id: str, aggregate: Any, scores: Sequence[Any]) -> str:
    display = aggregate.display()
    rows = "\n".join(
        f"- Repetition {score.repetition}: {score.display()['overall']}/100; 31/31 metrics resolved; result `{score.result_id}`"
        for score in scores
    )
    return f"""# {configuration_id} public configuration packet

This packet is a sanitized derivative of three already-qualified copied run artifacts.
It contains score inputs and evidence receipts only. Raw native records, transcripts,
SQLite files, absolute paths, account data, credentials, and personal history are withheld.

## Private aggregation

- Configuration: `{configuration_id}`
- Three-repetition scorer result: **{display['overall']}/100**
- Scorer verification: `{display['verification']}` for the local packet
- Publication: **unpublished private aggregation**
- Independent reproduction: **pending** (`false`)

{rows}

The local packet binds replay, canonical equality, selected-loss, and privacy receipts.
Those receipts document copied-artifact checks on this host. They do not establish an
independent operator or environment. The packet carries no global leaderboard rank.

Revalidate with `python3 scripts/build_public_configuration_bundles.py --verify --output
artifacts/survival-v1-public-configurations`.
"""


def _build_configuration(prepared: Sequence[PreparedRun], work: Path) -> dict[str, Any]:
    if len(prepared) != 3:
        raise PublicConfigurationBundleError("each public configuration requires exactly three prepared runs")
    configuration_id = prepared[0].spec.configuration_id
    if {item.spec.configuration_id for item in prepared} != {configuration_id} or {item.spec.repetition for item in prepared} != {1, 2, 3}:
        raise PublicConfigurationBundleError(f"{configuration_id} does not contain repetitions 1, 2, and 3 exactly")
    config_root = work / configuration_id
    scores: list[Any] = []
    records: list[dict[str, Any]] = []
    for item in sorted(prepared, key=lambda value: value.spec.repetition):
        run_root = config_root / "runs" / str(item.spec.repetition)
        derivative_root = run_root / "public-derivative"
        observer_id = _public_id(configuration_id, item.spec.repetition, "observer")
        native_id = _public_id(configuration_id, item.spec.repetition, "native")
        loss = _loss_receipt(item)
        native_sha = _write_json(derivative_root / "native-summary.json", _native_summary(item, loss))
        observer_sha = _write_json(derivative_root / "observer-summary.json", _observer_summary(item, observer_id))
        decoder_path = ROOT / (
            "session_bench/adapters/codex_cli_decoder.py"
            if configuration_id in {"codex-cli", "codex-desktop"}
            else "session_bench/adapters/claude_code_decoder.py"
        )
        decoder_sha = _sha_file(decoder_path)
        public_format = _sanitize_format(item, observer_id, observer_sha, native_id, native_sha)
        public_survival = _public_survival(item, _format_identity(item), observer_id, observer_sha, native_id, native_sha, decoder_sha)
        try:
            public_score = score_public_run(public_survival, public_format)
        except (TypeError, ValueError) as exc:
            raise PublicConfigurationBundleError(f"sanitized score inputs are invalid for {item.run_id}") from exc
        if not public_score.rankable or len(public_score.metrics) != 31 or any(value is None for value in public_score.metrics.values()):
            raise PublicConfigurationBundleError(f"sanitized score is not a resolved 31-metric result for {item.run_id}")
        if public_score.metrics != item.source_score.metrics or public_score.overall != item.source_score.overall:
            raise PublicConfigurationBundleError(f"sanitization changed scorer arithmetic for {item.run_id}")
        replay = _replay_receipt(item, observer_id, observer_sha, native_id, native_sha, decoder_sha)
        equality = _equality_receipt(item, native_sha)
        # The privacy scan is run again after the entire configuration is closed.
        privacy_scan = {"passed": True, "file_count": 0, "findings": [], "raw_transcript_files": 0, "raw_sqlite_files": 0}
        privacy = _privacy_receipt(item, native_sha, privacy_scan)
        _write_json(run_root / "score-input/survival-evidence.json", public_survival)
        _write_json(run_root / "score-input/format-evidence.json", public_format)
        _write_json(
            run_root / "score-input/public-score.json",
            {
                "schema_version": PUBLIC_RUN_SCORE_SCHEMA_VERSION,
                "configuration_id": configuration_id,
                "run_id": item.run_id,
                "repetition": item.spec.repetition,
                "score": public_score.display(),
                "published": False,
                "independent_reproduction": False,
                "state": "private_score_aggregation",
            },
        )
        _write_json(run_root / "replay-receipt.json", replay)
        _write_json(run_root / "canonical-equality-receipt.json", equality)
        _write_json(run_root / "loss-receipt.json", loss)
        _write_json(run_root / "privacy-receipt.json", privacy)
        scores.append(public_score)
        records.append(
            run_record_from_public_score(
                public_score,
                replay={
                    "evidence_id": replay["schema_version"] + ":" + replay["run_id"],
                    "verified": True,
                    "offline": True,
                    "original_root_denied": True,
                    "vendor_executable_denied": True,
                    "network_denied": True,
                    "runtime_sha256": decoder_sha,
                },
                canonical_equality={
                    "evidence_id": equality["schema_version"] + ":" + equality["run_id"],
                    "verified": True,
                    "ordinary_sha256": native_sha,
                    "isolated_sha256": native_sha,
                },
                privacy={
                    "evidence_id": privacy["schema_version"] + ":" + privacy["run_id"],
                    "verified": True,
                    "credentials_absent": True,
                    "account_data_absent": True,
                    "personal_history_absent": True,
                    "absolute_paths_absent": True,
                    "raw_native_withheld": True,
                    "public_derivative_sha256": native_sha,
                },
            )
        )
    bundle = build_configuration_bundle(
        records,
        bundle_id=f"session-bench-v1-{configuration_id}-3-run-public-bundle",
        receipt_id=f"session-bench-v1-{configuration_id}-local-reproduction-receipt",
    )
    validate_configuration_bundle(bundle.display())
    aggregate = aggregate_public_configuration(scores, configuration_evidence=bundle.report_input)
    if not aggregate.rankable or aggregate.verification != "fully_reproduced":
        raise PublicConfigurationBundleError(f"{configuration_id} aggregate is not complete: {aggregate.blockers}")
    _write_json(config_root / "configuration-bundle.json", bundle.display())
    evidence_sha = _write_json(config_root / "configuration-evidence.json", bundle.report_input)
    score_document = {
        "schema_version": PUBLIC_CONFIGURATION_SCORE_SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "score": aggregate.display(),
        "score_scope": "private_score_aggregation_from_sanitized_derivatives",
        "published": False,
        "publication_status": "private_unpublished",
        "independent_reproduction": False,
        "independent_reproduction_state": "pending",
        "configuration_evidence_sha256": evidence_sha,
        "resolved_metric_counts": [len(score.metrics) for score in scores],
        "global_leaderboard": {"published": False, "state": "pending_complete_five_configuration_cohort"},
    }
    _write_json(config_root / "configuration-score.json", score_document)
    config_manifest = _manifest(config_root, configuration_id, bundle.bundle_id, bundle.bundle_sha256)
    _write_json(config_root / "bundle-manifest.json", config_manifest)
    (config_root / "README.md").write_text(_config_readme(configuration_id, aggregate, scores), encoding="utf-8")
    scan = _public_scan(config_root)
    if not scan["passed"]:
        raise PublicConfigurationBundleError(f"{configuration_id} public packet failed privacy scan: {scan['findings']}")
    # Replace the provisional scan in each privacy receipt with the final scan
    # summary and rewrite the receipt. Its derivative digest remains unchanged.
    for item in sorted(prepared, key=lambda value: value.spec.repetition):
        path = config_root / "runs" / str(item.spec.repetition) / "privacy-receipt.json"
        receipt = _read_json(path)
        receipt["scan"] = {key: scan[key] for key in ("passed", "file_count", "raw_transcript_files", "raw_sqlite_files")}
        _write_json(path, receipt)
    # The receipts changed after the first manifest; refresh the manifest once.
    config_manifest = _manifest(config_root, configuration_id, bundle.bundle_id, bundle.bundle_sha256)
    _write_json(config_root / "bundle-manifest.json", config_manifest)
    return {
        "configuration_id": configuration_id,
        "bundle_id": bundle.bundle_id,
        "bundle_sha256": bundle.bundle_sha256,
        "result_ids": list(bundle.result_ids),
        "run_ids": [score.run_id for score in scores],
        "overall": aggregate.display()["overall"],
        "published": False,
        "independent_reproduction": False,
        "manifest_sha256": _sha_file(config_root / "bundle-manifest.json"),
        "metric_counts": [len(score.metrics) for score in scores],
    }


def _top_readme(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = "\n".join(
        f"- `{row['configuration_id']}`: three repetitions, 31/31 metrics per run, local aggregate {row['overall']}/100 (unpublished)"
        for row in rows
    )
    return f"""# Session-Bench v1 public configuration derivatives

This directory contains sanitized derivatives for exactly four already-qualified
configuration triples: Codex CLI, Codex Desktop, Claude Code CLI, and Claude Desktop. Every packet has
exactly repetitions 1, 2, and 3, a stable identity envelope, 31 resolved metrics per
run, deterministic bundle and receipt hashes, and replay, canonical-equality, selected-loss,
and privacy receipts.

{lines}

The score files are **private and unpublished**. They are useful for review and later
cohort assembly; this directory carries no global leaderboard rank. Independent
reproduction remains **pending** and is represented as `false` in every packet.

No raw transcript, SQLite file, absolute host path, account identifier, personal history,
credential, or private content is included. Revalidate with:

`python3 scripts/build_public_configuration_bundles.py --verify --output artifacts/survival-v1-public-configurations`
"""


def _top_manifest(root: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    core = {
        "schema_version": TOP_MANIFEST_SCHEMA_VERSION,
        "configuration_ids": [row["configuration_id"] for row in rows],
        "index_sha256": _sha_file(root / "index.json"),
        "configuration_manifests": [
            {"configuration_id": row["configuration_id"], "sha256": _sha_file(root / row["configuration_id"] / "bundle-manifest.json")}
            for row in rows
        ],
        "published": False,
        "independent_reproduction": False,
    }
    return {**core, "content_sha256": canonical_sha256(core)}


def _build_in_directory(work: Path) -> dict[str, Any]:
    prepared_by_config: dict[str, list[PreparedRun]] = {}
    for configuration_id in TARGET_CONFIGURATIONS:
        prepared_by_config[configuration_id] = [_prepare_run(_spec(configuration_id, repetition)) for repetition in REPETITIONS]
    rows = [_build_configuration(prepared_by_config[configuration_id], work) for configuration_id in TARGET_CONFIGURATIONS]
    index = {
        "schema_version": TOP_INDEX_SCHEMA_VERSION,
        "configuration_ids": list(TARGET_CONFIGURATIONS),
        "configurations": rows,
        "publication": {"published": False, "status": "private_score_aggregation_only"},
        "independent_reproduction": {"verified": False, "state": "pending"},
        "global_leaderboard": {"published": False, "state": "pending_complete_five_configuration_cohort", "required_configurations": 5, "available_configuration_count": len(TARGET_CONFIGURATIONS)},
    }
    _write_json(work / "index.json", index)
    (work / "README.md").write_text(_top_readme(rows), encoding="utf-8")
    _write_json(work / "manifest.json", _top_manifest(work, rows))
    scan = _public_scan(work)
    if not scan["passed"]:
        raise PublicConfigurationBundleError(f"public configuration set failed privacy scan: {scan['findings']}")
    return {
        "output": str(work),
        "configuration_ids": list(TARGET_CONFIGURATIONS),
        "configurations": rows,
        "published": False,
        "independent_reproduction": False,
        "privacy_findings": [],
    }


def build(*, output: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Build the exact four public packets without overwriting an existing output."""

    output = Path(output).resolve(strict=False)
    if output.exists() or output.is_symlink():
        raise PublicConfigurationBundleError(f"refusing to overwrite existing public output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".session-bench-public-configurations-", dir=output.parent))
    try:
        result = _build_in_directory(temporary)
        os.replace(temporary, output)
        result["output"] = str(output)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _verify_output(output: Path) -> dict[str, Any]:
    root = Path(output).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise PublicConfigurationBundleError(f"public output is not an ordinary directory: {root}")
    top_files = {path.name for path in root.iterdir() if path.is_file()}
    top_dirs = {path.name for path in root.iterdir() if path.is_dir()}
    if top_files != {"README.md", "index.json", "manifest.json"} or top_dirs != set(TARGET_CONFIGURATIONS):
        raise PublicConfigurationBundleError("public output does not contain exactly the four configuration packets")
    scan = _public_scan(root)
    if not scan["passed"]:
        raise PublicConfigurationBundleError(f"public output privacy scan failed: {scan['findings']}")
    index = _read_json(root / "index.json")
    if index.get("schema_version") != TOP_INDEX_SCHEMA_VERSION or index.get("configuration_ids") != list(TARGET_CONFIGURATIONS):
        raise PublicConfigurationBundleError("public configuration index is not the exact target set")
    if index.get("publication", {}).get("published") is not False or index.get("independent_reproduction", {}).get("verified") is not False:
        raise PublicConfigurationBundleError("public configuration index has an unsafe publication state")
    manifest = _read_json(root / "manifest.json")
    core = {key: manifest.get(key) for key in ("schema_version", "configuration_ids", "index_sha256", "configuration_manifests", "published", "independent_reproduction")}
    if set(manifest) != set(core) | {"content_sha256"} or manifest.get("schema_version") != TOP_MANIFEST_SCHEMA_VERSION or canonical_sha256(core) != manifest.get("content_sha256"):
        raise PublicConfigurationBundleError("top public manifest content hash mismatch")
    if _sha_file(root / "index.json") != manifest.get("index_sha256"):
        raise PublicConfigurationBundleError("top public manifest index hash mismatch")

    rows: list[dict[str, Any]] = []
    for configuration_id in TARGET_CONFIGURATIONS:
        config_root = root / configuration_id
        config_manifest = _read_json(config_root / "bundle-manifest.json")
        _verify_manifest(config_root, config_manifest)
        if config_manifest.get("configuration_id") != configuration_id:
            raise PublicConfigurationBundleError(f"manifest configuration mismatch for {configuration_id}")
        bundle_document = _read_json(config_root / "configuration-bundle.json")
        bundle = validate_configuration_bundle(bundle_document)
        evidence = _read_json(config_root / "configuration-evidence.json")
        if evidence != bundle.report_input:
            raise PublicConfigurationBundleError(f"configuration evidence differs for {configuration_id}")
        score_values: list[Any] = []
        for repetition in REPETITIONS:
            run_root = config_root / "runs" / str(repetition)
            survival = _read_json(run_root / "score-input/survival-evidence.json")
            format_document = _read_json(run_root / "score-input/format-evidence.json")
            score = score_public_run(survival, format_document)
            if not score.rankable or len(score.metrics) != 31 or any(value is None for value in score.metrics.values()):
                raise PublicConfigurationBundleError(f"public run {configuration_id} repetition {repetition} is incomplete")
            recorded = _read_json(run_root / "score-input/public-score.json")
            if recorded.get("score") != score.display() or recorded.get("published") is not False or recorded.get("independent_reproduction") is not False:
                raise PublicConfigurationBundleError(f"public run score projection is not bound for {configuration_id} repetition {repetition}")
            replay = _read_json(run_root / "replay-receipt.json")
            equality = _read_json(run_root / "canonical-equality-receipt.json")
            loss = _read_json(run_root / "loss-receipt.json")
            privacy = _read_json(run_root / "privacy-receipt.json")
            if any(replay.get(key) is not True for key in ("verified", "offline", "original_root_denied", "vendor_executable_denied", "network_denied")) or replay.get("independent_reproduction") is not False:
                raise PublicConfigurationBundleError(f"replay receipt is incomplete for {configuration_id} repetition {repetition}")
            if equality.get("verified") is not True or equality.get("copied_decode_equal") is not True or equality.get("ordinary_sha256") != equality.get("isolated_sha256") or equality.get("independent_reproduction") is not False:
                raise PublicConfigurationBundleError(f"canonical equality receipt is incomplete for {configuration_id} repetition {repetition}")
            if loss.get("verified") is not True or loss.get("response_loss_detected") is not True or loss.get("actions_preserved") is not True or loss.get("results_preserved") is not True or loss.get("independent_reproduction") is not False:
                raise PublicConfigurationBundleError(f"loss receipt is incomplete for {configuration_id} repetition {repetition}")
            if any(privacy.get(key) is not True for key in ("verified", "credentials_absent", "account_data_absent", "personal_history_absent", "absolute_paths_absent", "raw_native_withheld", "raw_transcript_withheld", "raw_sqlite_withheld")) or privacy.get("independent_reproduction") is not False:
                raise PublicConfigurationBundleError(f"privacy receipt is incomplete for {configuration_id} repetition {repetition}")
            score_values.append(score)
        aggregate = aggregate_public_configuration(score_values, configuration_evidence=evidence)
        score_document = _read_json(config_root / "configuration-score.json")
        if score_document.get("score") != aggregate.display() or score_document.get("published") is not False or score_document.get("independent_reproduction") is not False or score_document.get("independent_reproduction_state") != "pending":
            raise PublicConfigurationBundleError(f"configuration score projection is not bound for {configuration_id}")
        rows.append({"configuration_id": configuration_id, "bundle_sha256": bundle.bundle_sha256, "overall": aggregate.display()["overall"], "metric_counts": [31, 31, 31]})
    if set(manifest.get("configuration_ids", [])) != set(TARGET_CONFIGURATIONS):
        raise PublicConfigurationBundleError("top public manifest configuration set is incomplete")
    return {"output": str(root), "configuration_ids": list(TARGET_CONFIGURATIONS), "configurations": rows, "published": False, "independent_reproduction": False, "privacy_findings": []}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true", help="verify an existing public packet using only public files")
    args = parser.parse_args(argv)
    try:
        result = _verify_output(args.output) if args.verify else build(output=args.output)
    except Exception as exc:
        print(f"public configuration bundle failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
