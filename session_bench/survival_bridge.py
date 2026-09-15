"""Conservatively project prototype captures into survival-v1 input.

The files under ``artifacts/prototype-v1/captures`` were collected before the
survival-v1 workload, observer boundary, and rubric were frozen.  This module
keeps them useful for an offline discrimination/preflight report without
turning them into vendor results.

Only an explicitly supplied capture directory is read.  The existing
prototype decoder performs the native parsing and manifest checks; this bridge
then emits the exact 19-row document accepted by :mod:`survival_metrics`.
Because the old captures do not prove the frozen survival-v1 contract (and
their declared native roots are incomplete), every projected row remains a
blocking state.  In particular, an old missing fact is never called
``native_absent``: absence requires a complete declared root and a supported
decoder.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .prototype_metrics import _decode, _validated_metadata, analyze_capture
from .survival_metrics import (
    BLOCKING_STATES,
    METRICS,
    SCHEMA_VERSION,
    score_run,
    validate_input,
)


PRETEST_SCHEMA_VERSION = "session-bench-survival-pretest-v1"
SURVIVAL_PROTOCOL_VERSION = "1.0-survival"
SURVIVAL_WORKLOAD_VERSION = "1.0-survival-workload"

CANONICAL_METRIC_IDS = tuple(METRICS)

# The prototype's checks are retained in the pretest record as supporting
# evidence.  They are deliberately not treated as survival-v1 measurements:
# the two editions have different populations, canaries, and boundaries.
PROTOTYPE_CHECKS = (
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "C1",
    "C2",
    "C3",
    "U1",
    "U2",
    "U3",
    "U4",
    "A1",
    "A2",
    "A3",
    "A4",
    "S1",
    "S2",
)

# These are the only old facts that can supply a useful population hint.  A
# hint is carried into ``observed_eligible``/``decoded_eligible`` for the
# report, but the row remains unresolved or unexercised and therefore scores
# no points.  The values are intentionally lower-bound-like: the prototype
# denominator is not silently promoted to a v1 event population.
_POPULATION_HINTS = {
    "work.submitted_turns": "W1",
    "revision.r1": "W1",
    "revision.r2": "W1",
    "revision.r1_r2_order": "W1",
    "work.visible_responses": None,
    "attribution.model_config": None,
    "attribution.usage": None,
    "attribution.token_semantics": None,
}

_UNEXERCISED_METRICS = frozenset(
    {
        "work.actions",
        "work.results",
        "work.changed_files",
        "causal.action_result",
        "causal.turn_response",
        "revision.final_after_r2",
        "attribution.reconciliation",
        "portable.isolated_decode",
        "portable.canonical_equality",
    }
)


def _capture_root(capture_dir: str | Path) -> Path:
    """Resolve one explicit capture directory without discovering siblings."""

    root = Path(capture_dir)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("capture_dir must be an explicit real directory")
    return root


def native_decode_digest(capture_dir: str | Path) -> str:
    """Hash the prototype native-only decode for copy and observer-blindness controls.

    This helper intentionally omits the observer and the answer key.  It is a
    control for the evidence pipeline, not a survival score and not a public
    native-format API.  It reads only the declared native artifacts from one
    explicit capture root and rejects an integrity failure before hashing.
    """

    root = _capture_root(capture_dir)
    _metadata, native, integrity_problems = _validated_metadata(root)
    if integrity_problems:
        raise ValueError("native capture integrity failed: " + "; ".join(integrity_problems))
    events, identity, usage, composition, problems, supported = _decode(native)
    if problems:
        raise ValueError("native decode failed: " + "; ".join(problems))
    value = {
        "events": events,
        "identity": identity,
        "usage": usage,
        "composition": composition,
        "supported": supported,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _legacy_measurements(analysis: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rows = analysis.get("measurements")
    if not isinstance(rows, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("id"), str):
            # Keep only JSON-shaped public evidence.  In particular, do not
            # leak ``evidence_path`` or native payloads into the pretest.
            result[row["id"]] = {
                key: row.get(key)
                for key in ("id", "state", "score", "numerator", "denominator", "summary")
            }
    return result


def _redact_limitation(value: str, root: Path) -> str:
    """Keep diagnostics while removing machine-local absolute paths."""

    text = value.replace(str(root), "<capture>")
    # Native decoder diagnostics should normally use relative locators.  This
    # fallback prevents a malformed legacy metadata note from leaking a home,
    # temporary, or other machine path into a pretest artifact.
    text = re.sub(r"(?<![A-Za-z0-9_])/(?:Users|home|private|tmp|var)/[^\s,;)]*", "<local-path>", text)
    return text


def _hint_counts(
    metric_id: str,
    legacy: Mapping[str, Mapping[str, Any]],
    *,
    observer_present: bool,
) -> tuple[int, int, int]:
    """Return conservative counts for an unresolved v1 row.

    ``analyze_capture`` exposes the old observer population as numerator and
    denominator but not a canonical decoded population.  We therefore use the
    old denominator as a bounded display hint only, never as a successful v1
    comparison.  Rows without a directly related old population stay at zero.
    """

    check_id = _POPULATION_HINTS.get(metric_id)
    if check_id is None or not observer_present:
        return 0, 0, 0
    old = legacy.get(check_id, {})
    denominator = old.get("denominator")
    if not isinstance(denominator, int) or isinstance(denominator, bool) or denominator < 1:
        return 0, 0, 0
    # The v1 contract has a fixed two-turn population.  A legacy count larger
    # than that is retained as a candidate count only up to the contract size;
    # it cannot inflate a future denominator or create rankability.
    observed = min(2, denominator)
    return 0, observed, observed


def _row(
    metric_id: str,
    state: str,
    *,
    correct: int = 0,
    observed_eligible: int = 0,
    decoded_eligible: int = 0,
) -> dict[str, Any]:
    if state not in BLOCKING_STATES:
        raise AssertionError(f"bridge emitted a non-blocking state: {state}")
    return {
        "id": metric_id,
        "state": state,
        "correct": correct,
        "observed_eligible": observed_eligible,
        "decoded_eligible": decoded_eligible,
    }


def _project_rows(
    analysis: Mapping[str, Any],
    *,
    observer_present: bool,
) -> list[dict[str, Any]]:
    """Project one legacy analysis into all required, deliberately blocked rows."""

    legacy = _legacy_measurements(analysis)
    invalid = analysis.get("status") == "invalid"
    rows: list[dict[str, Any]] = []
    for metric_id in CANONICAL_METRIC_IDS:
        if invalid:
            state = "invalid_capture"
        elif metric_id in {"portable.complete_root", "portable.companions"}:
            # The existing packages explicitly declare an incomplete native
            # boundary.  We cannot claim either completeness or absence.
            state = "unresolved"
        elif metric_id in _UNEXERCISED_METRICS:
            state = "unexercised"
        else:
            # Old wording, response markers, and observer boundaries differ
            # from the frozen v1 contract.  A candidate match cannot resolve
            # that ambiguity.
            state = "unresolved"
        correct, observed, decoded = _hint_counts(
            metric_id, legacy, observer_present=observer_present
        )
        rows.append(
            _row(
                metric_id,
                state,
                correct=correct,
                observed_eligible=observed,
                decoded_eligible=decoded,
            )
        )
    if not rows or not all(row["state"] in BLOCKING_STATES for row in rows):
        raise AssertionError("survival bridge must produce only blocking legacy rows")
    return rows


def build_survival_input(
    capture_dir: str | Path,
    *,
    repetition: int = 1,
) -> dict[str, Any]:
    """Build the exact canonical 19-row input for one old prototype capture.

    The returned object is accepted by :func:`validate_input`, but is
    intentionally not rankable.  ``repetition`` is a display/ledger identity;
    it does not turn a pilot capture into an evaluated repetition.
    """

    root = _capture_root(capture_dir)
    analysis = analyze_capture(root)
    run_id = analysis.get("run_id")
    configuration_id = analysis.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("prototype analysis did not provide a run_id")
    if not isinstance(configuration_id, str) or not configuration_id:
        raise ValueError("prototype analysis did not provide a configuration id")
    if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
        raise ValueError("repetition must be a positive integer")
    document = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
            "metrics": _project_rows(
                analysis,
                observer_present=(root / "observer.json").is_file(),
            ),
    }
    validated = validate_input(document)
    # Defense in depth: this bridge is for the pretest only, so a future
    # prototype decoder change cannot quietly create a leaderboard row.
    result = score_run(validated)
    if result.rankable or result.overall is not None:
        raise AssertionError("prototype survival bridge unexpectedly became rankable")
    return validated


# Friendly aliases for callers that describe this operation as a bridge.
survival_input_from_capture = build_survival_input
bridge_capture = build_survival_input


def build_pretest_record(
    capture_dir: str | Path,
    *,
    repetition: int = 1,
) -> dict[str, Any]:
    """Return a redacted, machine-readable offline pretest record."""

    root = _capture_root(capture_dir)
    analysis = analyze_capture(root)
    document = build_survival_input(root, repetition=repetition)
    scored = score_run(document)
    record = {
        "capture_id": root.name,
        "configuration_id": document["configuration_id"],
        "run_id": document["run_id"],
        "protocol_version": SURVIVAL_PROTOCOL_VERSION,
        "workload_version": SURVIVAL_WORKLOAD_VERSION,
        "canonical_input": document,
        "survival": scored.display(),
        "publishable": False,
        "rankable": False,
        "legacy_prototype": {
            "status": analysis.get("status"),
            "coverage": analysis.get("coverage"),
            "rankable": bool(analysis.get("rankable")),
            "measurements": _legacy_measurements(analysis),
        },
        "limitations": [
            "This capture predates the frozen survival-v1 workload and observer boundary.",
            "The declared native artifact boundary is incomplete; missing facts remain unresolved.",
            "This offline bridge is a discrimination/preflight aid, not a vendor result.",
            *[
                _redact_limitation(item, root)
                for item in analysis.get("limitations", [])
                if isinstance(item, str)
            ],
        ],
    }
    # Avoid duplicate limitations while preserving order.
    record["limitations"] = list(dict.fromkeys(record["limitations"]))
    if record["survival"]["rankable"] or record["survival"]["overall"] is not None:
        raise AssertionError("pretest record cannot contain a publishable survival score")
    return record


def build_pretest_report(capture_dirs: Iterable[str | Path]) -> dict[str, Any]:
    """Build a no-ranking report from explicitly supplied prototype captures."""

    records = [build_pretest_record(path) for path in capture_dirs]
    if any(record["rankable"] for record in records):
        raise AssertionError("pretest report cannot contain a rankable record")
    return {
        "schema_version": PRETEST_SCHEMA_VERSION,
        "edition": "survival-v1 offline bridge",
        "status": "provisional",
        "publishable": False,
        "leaderboard_eligible": False,
        "headline": "Prototype captures are structural probes, not Session Survival Scores.",
        "purpose": "Exercise the new canonical scorer against explicitly packaged legacy captures without making a vendor claim.",
        "capture_count": len(records),
        "rankable_count": 0,
        "captures": records,
        "limitations": [
            "No live vendor sessions, personal stores, network calls, or vendor executables were used by this bridge.",
            "A public leaderboard requires fresh survival-v1 captures with complete roots, independent boundaries, and three qualified repetitions.",
        ],
    }


def write_pretest_report(
    capture_dirs: Iterable[str | Path],
    output: str | Path,
) -> Path:
    """Write a redacted pretest report, refusing paths inside the captures."""

    paths = [_capture_root(path) for path in capture_dirs]
    report_path = Path(output).resolve()
    capture_roots = [path.resolve() for path in paths]
    if any(report_path == root or report_path.is_relative_to(root) for root in capture_roots):
        raise ValueError("output must be outside explicitly supplied capture roots")
    if report_path.exists() and report_path.is_dir():
        raise ValueError("output must be a file")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(build_pretest_report(paths), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report_path
