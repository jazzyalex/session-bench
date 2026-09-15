"""Build and validate an immutable public bundle for one configuration.

The v1 scorer already knows how to aggregate three :class:`PublicRunScore`
objects.  This module supplies the missing provenance boundary around those
scores.  It accepts only JSON-compatible, already-sanitized run records; it
does not discover native roots, read session stores, or claim that a local
replay was an independent reproduction.

The input contract is intentionally small and surface-neutral.  A run record
contains a public result projection, its stable identity, and three explicit
evidence records for replay, canonical equality, and privacy.  The builder
requires all three repetitions and derives both the bundle and receipt hashes
from canonical JSON.  The returned ``configuration_evidence`` is directly
usable as the ``configuration_evidence=`` argument to the v1 public scorer.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "session-bench-configuration-bundle-v1"
RECEIPT_SCHEMA_VERSION = "session-bench-reproduction-receipt-v1"

_RUN_FIELDS = frozenset(
    {
        "configuration_id",
        "run_id",
        "repetition",
        "result_id",
        "evaluation_id",
        "collected_on",
        "identity",
        "result",
        "replay",
        "canonical_equality",
        "privacy",
    }
)
_IDENTITY_FIELDS = frozenset(
    {
        "provider",
        "harness",
        "surface",
        "execution_mode",
        "os",
        "build",
        "model",
        "configuration",
        "observer_schema_version",
    }
)
_RESULT_FIELDS = frozenset({"resolved", "metric_ids", "metrics", "categories", "overall"})
_REPLAY_FIELDS = frozenset(
    {
        "evidence_id",
        "verified",
        "offline",
        "original_root_denied",
        "vendor_executable_denied",
        "network_denied",
        "runtime_sha256",
    }
)
_EQUALITY_FIELDS = frozenset(
    {"evidence_id", "verified", "ordinary_sha256", "isolated_sha256"}
)
_PRIVACY_FIELDS = frozenset(
    {
        "evidence_id",
        "verified",
        "credentials_absent",
        "account_data_absent",
        "personal_history_absent",
        "absolute_paths_absent",
        "raw_native_withheld",
        "public_derivative_sha256",
    }
)


class ConfigurationBundleError(ValueError):
    """Raised when a run set cannot earn a public bundle binding."""


@dataclass(frozen=True)
class ConfigurationBundle:
    """The deterministic bundle and report binding for one configuration.

    ``independent_reproduction`` is deliberately always false here.  The
    builder proves a closed local replay receipt; a second operator or
    environment must issue a separate receipt before that badge is earned.
    """

    configuration_id: str
    bundle_id: str
    bundle_sha256: str
    receipt_id: str
    receipt_sha256: str
    result_ids: tuple[str, ...]
    document: Mapping[str, Any]
    configuration_evidence: Mapping[str, Any]
    independent_reproduction: bool = False

    @property
    def report_input(self) -> Mapping[str, Any]:
        """Return the strict object accepted by ``aggregate_public_configuration``."""

        return deepcopy(dict(self.configuration_evidence))

    def display(self) -> dict[str, Any]:
        """Return a JSON-safe copy suitable for an evidence index."""

        return deepcopy(dict(self.document))


def canonical_json(value: Any) -> bytes:
    """Encode JSON deterministically and reject non-JSON/non-finite values."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationBundleError("bundle input must be finite JSON") from exc


def canonical_sha256(value: Any) -> str:
    """Hash one canonical JSON value."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationBundleError(f"{label} must be a non-empty trimmed string")
    return value


def _digest(value: Any, label: str) -> str:
    value = _text(value, label)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ConfigurationBundleError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _strict_fields(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != set(expected):
        raise ConfigurationBundleError(
            f"{label} has wrong fields (missing={sorted(set(expected) - actual)}, "
            f"extra={sorted(actual - set(expected))})"
        )


def _reject_absolute_paths(value: Any, label: str) -> None:
    """Reject host paths that would make a supposedly public bundle unsafe."""

    if isinstance(value, str):
        if value.startswith("/") or value.startswith("~/"):
            raise ConfigurationBundleError(f"{label} contains an absolute or home-relative path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_absolute_paths(key, f"{label}.key")
            _reject_absolute_paths(item, f"{label}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_absolute_paths(item, f"{label}[{index}]")


def _finite_number(value: Any, label: str) -> None:
    if isinstance(value, bool) or value is None:
        raise ConfigurationBundleError(f"{label} must be a finite number")
    if not isinstance(value, (int, float)):
        raise ConfigurationBundleError(f"{label} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigurationBundleError(f"{label} must be a finite number")


def _validate_identity(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} must be an object")
    _strict_fields(value, _IDENTITY_FIELDS, label)
    return {field: _text(value[field], f"{label}.{field}") for field in sorted(_IDENTITY_FIELDS)}


def _validate_result(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} must be an object")
    _strict_fields(value, _RESULT_FIELDS, label)
    if value["resolved"] is not True:
        raise ConfigurationBundleError(f"{label} must explicitly mark the run resolved")

    # Import lazily so the bundle helper stays usable by low-level tooling and
    # avoids making v1_public_score import this module during module startup.
    from .v1_public_score import PUBLIC_CATEGORY_POINTS, PUBLIC_METRICS

    metric_ids = value["metric_ids"]
    if not isinstance(metric_ids, list) or metric_ids != list(PUBLIC_METRICS):
        raise ConfigurationBundleError(f"{label}.metric_ids must be the frozen 31-metric list")
    metrics = value["metrics"]
    # Canonical JSON sorts object keys, so a serialized bundle is allowed to
    # return this mapping in lexical order.  The ordered ``metric_ids`` list
    # remains the frozen presentation order; validation of the mapping itself
    # must be set based or deterministic round-trips would fail closed.
    if not isinstance(metrics, Mapping) or set(metrics) != set(PUBLIC_METRICS):
        raise ConfigurationBundleError(f"{label}.metrics must contain the frozen 31-metric set")
    normalized_metrics: dict[str, float | int] = {}
    for metric_id in PUBLIC_METRICS:
        _finite_number(metrics[metric_id], f"{label}.metrics.{metric_id}")
        normalized_metrics[metric_id] = metrics[metric_id]

    categories = value["categories"]
    if not isinstance(categories, Mapping) or set(categories) != set(PUBLIC_CATEGORY_POINTS):
        raise ConfigurationBundleError(f"{label}.categories must contain the five public categories")
    normalized_categories: dict[str, float | int] = {}
    for category in PUBLIC_CATEGORY_POINTS:
        _finite_number(categories[category], f"{label}.categories.{category}")
        normalized_categories[category] = categories[category]
    _finite_number(value["overall"], f"{label}.overall")
    if value["overall"] < 0 or value["overall"] > 100:
        raise ConfigurationBundleError(f"{label}.overall must be within 0..100")
    return {
        "resolved": True,
        "metric_ids": list(metric_ids),
        "metrics": normalized_metrics,
        "categories": normalized_categories,
        "overall": value["overall"],
    }


def _validate_replay(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} is missing")
    _strict_fields(value, _REPLAY_FIELDS, label)
    for field in ("verified", "offline", "original_root_denied", "vendor_executable_denied", "network_denied"):
        if value[field] is not True:
            raise ConfigurationBundleError(f"{label}.{field} must be true")
    return {
        **{field: value[field] for field in sorted(_REPLAY_FIELDS - {"runtime_sha256"})},
        "runtime_sha256": _digest(value["runtime_sha256"], f"{label}.runtime_sha256"),
    }


def _validate_equality(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} is missing")
    _strict_fields(value, _EQUALITY_FIELDS, label)
    if value["verified"] is not True:
        raise ConfigurationBundleError(f"{label}.verified must be true")
    ordinary = _digest(value["ordinary_sha256"], f"{label}.ordinary_sha256")
    isolated = _digest(value["isolated_sha256"], f"{label}.isolated_sha256")
    if ordinary != isolated:
        raise ConfigurationBundleError(f"{label} ordinary and isolated canonical hashes differ")
    return {
        "evidence_id": _text(value["evidence_id"], f"{label}.evidence_id"),
        "verified": True,
        "ordinary_sha256": ordinary,
        "isolated_sha256": isolated,
    }


def _validate_privacy(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} is missing")
    _strict_fields(value, _PRIVACY_FIELDS, label)
    for field in (
        "verified",
        "credentials_absent",
        "account_data_absent",
        "personal_history_absent",
        "absolute_paths_absent",
        "raw_native_withheld",
    ):
        if value[field] is not True:
            raise ConfigurationBundleError(f"{label}.{field} must be true")
    return {
        **{field: value[field] for field in sorted(_PRIVACY_FIELDS - {"public_derivative_sha256"})},
        "public_derivative_sha256": _digest(
            value["public_derivative_sha256"], f"{label}.public_derivative_sha256"
        ),
    }


def _validate_run(value: Any, index: int) -> dict[str, Any]:
    label = f"run {index}"
    if not isinstance(value, Mapping):
        raise ConfigurationBundleError(f"{label} must be an object")
    _strict_fields(value, _RUN_FIELDS, label)
    configuration_id = _text(value["configuration_id"], f"{label}.configuration_id")
    run_id = _text(value["run_id"], f"{label}.run_id")
    result_id = _text(value["result_id"], f"{label}.result_id")
    evaluation_id = _text(value["evaluation_id"], f"{label}.evaluation_id")
    repetition = value["repetition"]
    if type(repetition) is not int or repetition not in {1, 2, 3}:
        raise ConfigurationBundleError(f"{label}.repetition must be 1, 2, or 3")
    collected_on = value["collected_on"]
    if not isinstance(collected_on, str) or len(collected_on) != 10:
        raise ConfigurationBundleError(f"{label}.collected_on must be YYYY-MM-DD")
    try:
        date.fromisoformat(collected_on)
    except ValueError as exc:
        raise ConfigurationBundleError(f"{label}.collected_on must be YYYY-MM-DD") from exc
    identity = _validate_identity(value["identity"], f"{label}.identity")
    if identity["provider"] == "":  # defensive; _text already enforces this
        raise ConfigurationBundleError(f"{label}.identity.provider is missing")
    result = _validate_result(value["result"], f"{label}.result")
    replay = _validate_replay(value["replay"], f"{label}.replay")
    equality = _validate_equality(value["canonical_equality"], f"{label}.canonical_equality")
    privacy = _validate_privacy(value["privacy"], f"{label}.privacy")
    normalized = {
        "configuration_id": configuration_id,
        "run_id": run_id,
        "repetition": repetition,
        "result_id": result_id,
        "evaluation_id": evaluation_id,
        "collected_on": collected_on,
        "identity": identity,
        "result": result,
        "replay": replay,
        "canonical_equality": equality,
        "privacy": privacy,
    }
    # The input is a public/sanitized record.  This check catches an unsafe
    # record even when a caller accidentally supplied a permissive privacy flag.
    _reject_absolute_paths(normalized, label)
    return normalized


def _bundle_preimage(
    *,
    bundle_id: str,
    configuration_id: str,
    result_ids: Sequence[str],
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "id": bundle_id,
        "configuration_id": configuration_id,
        "immutable": True,
        "public": True,
        "result_ids": list(result_ids),
        "runs": [deepcopy(dict(run)) for run in runs],
    }


def _receipt_preimage(
    *,
    receipt_id: str,
    bundle_id: str,
    bundle_sha256: str,
    result_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "id": receipt_id,
        "bundle_id": bundle_id,
        "bundle_sha256": bundle_sha256,
        "offline_recomputed": True,
        "verified": True,
        "result_ids": list(result_ids),
    }


def validate_configuration_bundle(document: Mapping[str, Any]) -> ConfigurationBundle:
    """Recompute and validate a previously built bundle document.

    This function performs no filesystem or network access.  It is suitable
    for an independent process that receives the JSON bundle itself.
    """

    expected = {
        "schema_version",
        "configuration_id",
        "bundle",
        "runs",
        "reproduction_receipt",
        "independent_reproduction",
    }
    if not isinstance(document, Mapping):
        raise ConfigurationBundleError("configuration bundle must be an object")
    _strict_fields(document, frozenset(expected), "configuration bundle")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ConfigurationBundleError("unsupported configuration bundle schema version")
    configuration_id = _text(document["configuration_id"], "configuration bundle configuration_id")
    if document["independent_reproduction"] is not False:
        raise ConfigurationBundleError("local bundle builder cannot claim independent reproduction")
    raw_runs = document["runs"]
    if not isinstance(raw_runs, list) or len(raw_runs) != 3:
        raise ConfigurationBundleError("configuration bundle requires exactly three run records")
    runs = [_validate_run(value, index) for index, value in enumerate(raw_runs, 1)]
    if {run["configuration_id"] for run in runs} != {configuration_id}:
        raise ConfigurationBundleError("bundle runs must identify one configuration")
    if {run["repetition"] for run in runs} != {1, 2, 3}:
        raise ConfigurationBundleError("bundle runs must contain repetitions 1, 2, and 3 exactly")
    for field in ("run_id", "result_id", "evaluation_id"):
        values = [run[field] for run in runs]
        if len(set(values)) != 3:
            raise ConfigurationBundleError(f"bundle runs must have unique {field} values")
    stable_fields = tuple(sorted(_IDENTITY_FIELDS))
    first_identity = runs[0]["identity"]
    if any(run["identity"] != first_identity for run in runs[1:]):
        differing = [field for field in stable_fields if any(run["identity"][field] != first_identity[field] for run in runs[1:])]
        raise ConfigurationBundleError(f"bundle identity differs across repetitions: {differing}")

    raw_bundle = document["bundle"]
    if not isinstance(raw_bundle, Mapping):
        raise ConfigurationBundleError("configuration bundle binding must be an object")
    _strict_fields(raw_bundle, frozenset({"id", "sha256", "immutable", "public", "result_ids"}), "configuration bundle binding")
    bundle_id = _text(raw_bundle["id"], "configuration bundle id")
    bundle_sha256 = _digest(raw_bundle["sha256"], "configuration bundle sha256")
    if raw_bundle["immutable"] is not True or raw_bundle["public"] is not True:
        raise ConfigurationBundleError("configuration bundle must be immutable and public")
    result_ids = tuple(sorted(run["result_id"] for run in runs))
    if raw_bundle["result_ids"] != list(result_ids):
        raise ConfigurationBundleError("configuration bundle result IDs do not match the three runs")
    expected_bundle_sha = canonical_sha256(
        _bundle_preimage(
            bundle_id=bundle_id,
            configuration_id=configuration_id,
            result_ids=result_ids,
            runs=sorted(runs, key=lambda item: item["repetition"]),
        )
    )
    if bundle_sha256 != expected_bundle_sha:
        raise ConfigurationBundleError("configuration bundle hash does not match its canonical contents")

    raw_receipt = document["reproduction_receipt"]
    if not isinstance(raw_receipt, Mapping):
        raise ConfigurationBundleError("reproduction receipt must be an object")
    _strict_fields(
        raw_receipt,
        frozenset({"id", "sha256", "bundle_sha256", "offline_recomputed", "verified", "result_ids"}),
        "reproduction receipt",
    )
    receipt_id = _text(raw_receipt["id"], "reproduction receipt id")
    receipt_sha256 = _digest(raw_receipt["sha256"], "reproduction receipt sha256")
    receipt_bundle_sha = _digest(raw_receipt["bundle_sha256"], "reproduction receipt bundle sha256")
    if receipt_bundle_sha != bundle_sha256:
        raise ConfigurationBundleError("reproduction receipt is bound to a different bundle")
    if raw_receipt["offline_recomputed"] is not True or raw_receipt["verified"] is not True:
        raise ConfigurationBundleError("reproduction receipt must verify an offline recomputation")
    if raw_receipt["result_ids"] != list(result_ids):
        raise ConfigurationBundleError("reproduction receipt result IDs do not match the three runs")
    expected_receipt_sha = canonical_sha256(
        _receipt_preimage(
            receipt_id=receipt_id,
            bundle_id=bundle_id,
            bundle_sha256=bundle_sha256,
            result_ids=result_ids,
        )
    )
    if receipt_sha256 != expected_receipt_sha:
        raise ConfigurationBundleError("reproduction receipt hash does not match its canonical contents")

    configuration_evidence = {
        "schema_version": "session-bench-configuration-evidence-v1",
        "configuration_id": configuration_id,
        "bundle": deepcopy(dict(raw_bundle)),
        "reproduction_receipt": deepcopy(dict(raw_receipt)),
    }
    return ConfigurationBundle(
        configuration_id=configuration_id,
        bundle_id=bundle_id,
        bundle_sha256=bundle_sha256,
        receipt_id=receipt_id,
        receipt_sha256=receipt_sha256,
        result_ids=result_ids,
        document=deepcopy(dict(document)),
        configuration_evidence=configuration_evidence,
        independent_reproduction=False,
    )


def build_configuration_bundle(
    records: Sequence[Mapping[str, Any]],
    *,
    bundle_id: str | None = None,
    receipt_id: str | None = None,
) -> ConfigurationBundle:
    """Build a deterministic public bundle from exactly three resolved runs.

    ``records`` must be the public/sanitized run-record shape documented at the
    top of this module.  The function refuses one- or two-run sets, duplicate
    identities, unresolved result cells, replay/equality/privacy gaps, unsafe
    path content, and any partial/hand-edited provenance object.
    """

    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ConfigurationBundleError("records must be a sequence of exactly three run records")
    if len(records) != 3:
        raise ConfigurationBundleError("configuration bundle requires exactly three run records")
    runs = [_validate_run(value, index) for index, value in enumerate(records, 1)]
    configuration_ids = {run["configuration_id"] for run in runs}
    if len(configuration_ids) != 1:
        raise ConfigurationBundleError("all bundle runs must identify one configuration")
    configuration_id = next(iter(configuration_ids))
    if {run["repetition"] for run in runs} != {1, 2, 3}:
        raise ConfigurationBundleError("bundle runs must contain repetitions 1, 2, and 3 exactly")
    for field in ("run_id", "result_id", "evaluation_id"):
        values = [run[field] for run in runs]
        if len(set(values)) != 3:
            raise ConfigurationBundleError(f"bundle runs must have unique {field} values")
    stable_fields = tuple(sorted(_IDENTITY_FIELDS))
    first_identity = runs[0]["identity"]
    if any(run["identity"] != first_identity for run in runs[1:]):
        differing = [field for field in stable_fields if any(run["identity"][field] != first_identity[field] for run in runs[1:])]
        raise ConfigurationBundleError(f"bundle identity differs across repetitions: {differing}")
    ordered_runs = sorted(runs, key=lambda item: item["repetition"])
    result_ids = tuple(sorted(run["result_id"] for run in ordered_runs))

    safe_suffix = re.sub(r"[^a-z0-9]+", "-", configuration_id.lower()).strip("-") or "configuration"
    resolved_bundle_id = _text(bundle_id, "bundle_id") if bundle_id is not None else f"session-bench-v1-{safe_suffix}-3-run-bundle"
    resolved_receipt_id = _text(receipt_id, "receipt_id") if receipt_id is not None else f"session-bench-v1-{safe_suffix}-reproduction-receipt"
    bundle_preimage = _bundle_preimage(
        bundle_id=resolved_bundle_id,
        configuration_id=configuration_id,
        result_ids=result_ids,
        runs=ordered_runs,
    )
    bundle_sha256 = canonical_sha256(bundle_preimage)
    bundle = {
        "id": resolved_bundle_id,
        "sha256": bundle_sha256,
        "immutable": True,
        "public": True,
        "result_ids": list(result_ids),
    }
    receipt_preimage = _receipt_preimage(
        receipt_id=resolved_receipt_id,
        bundle_id=resolved_bundle_id,
        bundle_sha256=bundle_sha256,
        result_ids=result_ids,
    )
    receipt_sha256 = canonical_sha256(receipt_preimage)
    receipt = {
        "id": resolved_receipt_id,
        "sha256": receipt_sha256,
        "bundle_sha256": bundle_sha256,
        "offline_recomputed": True,
        "verified": True,
        "result_ids": list(result_ids),
    }
    document = {
        "schema_version": SCHEMA_VERSION,
        "configuration_id": configuration_id,
        "bundle": bundle,
        "runs": ordered_runs,
        "reproduction_receipt": receipt,
        "independent_reproduction": False,
    }
    return validate_configuration_bundle(document)


def run_record_from_public_score(
    run: Any,
    *,
    replay: Mapping[str, Any],
    canonical_equality: Mapping[str, Any],
    privacy: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one validated ``PublicRunScore`` into the bundle input shape.

    This helper only copies scorer output and supplied evidence attestations;
    it never infers replay, equality, or privacy status.  Missing score
    identity or any unresolved metric raises before a bundle can be built.
    """

    configuration_id = _text(getattr(run, "configuration_id", None), "public run configuration_id")
    run_id = _text(getattr(run, "run_id", None), "public run run_id")
    repetition = getattr(run, "repetition", None)
    result_id = _text(getattr(run, "result_id", None), "public run result_id")
    evaluation_id = _text(getattr(run, "survival_evaluation_id", None), "public run evaluation_id")
    collected_on = _text(getattr(run, "collected_on", None), "public run collected_on")
    identity_obj = getattr(run, "identity", None)
    if identity_obj is None:
        raise ConfigurationBundleError("public run identity is missing")
    identity = {
        field: getattr(identity_obj, "os_name", None) if field == "os" else getattr(identity_obj, field, None)
        for field in _IDENTITY_FIELDS
    }
    metrics = getattr(run, "metrics", None)
    categories_obj = getattr(run, "categories", None)
    overall = getattr(run, "overall", None)
    if getattr(run, "rankable", False) is not True or not isinstance(metrics, Mapping) or overall is None:
        raise ConfigurationBundleError("public run is not a resolved 31-metric result")
    from .v1_public_score import PUBLIC_CATEGORY_POINTS, PUBLIC_METRICS

    category_values: dict[str, Any] = {}
    if not isinstance(categories_obj, Mapping):
        raise ConfigurationBundleError("public run category scores are missing")
    for category in PUBLIC_CATEGORY_POINTS:
        category_score = categories_obj.get(category)
        score = getattr(category_score, "score", None)
        if score is None:
            raise ConfigurationBundleError(f"public run category {category} is unresolved")
        category_values[category] = float(score)
    metric_values: dict[str, Any] = {}
    for metric_id in PUBLIC_METRICS:
        value = metrics.get(metric_id)
        if value is None:
            raise ConfigurationBundleError(f"public run metric {metric_id} is unresolved")
        metric_values[metric_id] = float(value)
    return {
        "configuration_id": configuration_id,
        "run_id": run_id,
        "repetition": repetition,
        "result_id": result_id,
        "evaluation_id": evaluation_id,
        "collected_on": collected_on,
        "identity": identity,
        "result": {
            "resolved": True,
            "metric_ids": list(PUBLIC_METRICS),
            "metrics": metric_values,
            "categories": category_values,
            "overall": float(overall),
        },
        "replay": deepcopy(dict(replay)),
        "canonical_equality": deepcopy(dict(canonical_equality)),
        "privacy": deepcopy(dict(privacy)),
    }


__all__ = [
    "ConfigurationBundle",
    "ConfigurationBundleError",
    "RECEIPT_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_configuration_bundle",
    "canonical_json",
    "canonical_sha256",
    "run_record_from_public_score",
    "validate_configuration_bundle",
]
