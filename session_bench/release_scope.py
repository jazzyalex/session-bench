"""Explicit public-v1 release population, separate from frozen campaigns.

Scope membership authorizes neither collection nor publication and grants no
score. Coverage records describe attempts; rankable IDs must come separately
from the evidence evaluator. Historical v0.4 identities are aliases only,
never evidence transferable to a new scenario or another surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


RELEASE_SCOPE_PATH = Path(__file__).resolve().parent.parent / "registries" / "v1-release-scope.json"
SCHEMA_VERSION = "session-bench-release-scope-v1"
RELEASE_ID = "session-bench-v1-expanded"
HISTORICAL_IDS = {
    "pi": "pi", "openclaw": "openclaw", "claude-cli": "claude",
    "codex-cli": "codex", "kimi": "kimi", "opencode-cli": "opencode",
    "hermes": "hermes", "copilot": "copilot", "antigravity": "antigravity",
    "cursor-cli": "cursor",
}
CONFIGURATION_IDS = tuple(HISTORICAL_IDS) + (
    "codex-desktop", "claude-desktop", "cursor-desktop", "deepseek-harness-cli",
)
_ROW_FIELDS = {"configuration_id", "display_name", "harness", "surface", "historical_id", "identity_state"}
_STATUS_FIELDS = {"configuration_id", "state", "attempt_ids", "evidence_refs", "reason_ids"}
STATES = frozenset({"unattempted", "blocked", "incomplete", "rankable"})


def _strings(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{context} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{context} must be unique")
    return value


def validate_release_scope(scope: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the exact 14-row scope and preserved historical aliases offline."""
    if not isinstance(scope, Mapping) or set(scope) != {"schema_version", "release_id", "historical_source", "rows"}:
        raise ValueError("release scope has invalid fields")
    if scope["schema_version"] != SCHEMA_VERSION or scope["release_id"] != RELEASE_ID:
        raise ValueError("unknown release scope identity")
    if scope["historical_source"] != "data/measurements.json":
        raise ValueError("historical source identity changed")
    rows = scope["rows"]
    if not isinstance(rows, list) or len(rows) != len(CONFIGURATION_IDS):
        raise ValueError("release scope must retain exactly 14 rows")
    ids = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _ROW_FIELDS:
            raise ValueError("release scope row has invalid fields")
        configuration_id = row["configuration_id"]
        if not isinstance(configuration_id, str) or configuration_id not in CONFIGURATION_IDS:
            raise ValueError("unknown release configuration ID")
        ids.append(configuration_id)
        for field in ("display_name", "harness"):
            if not isinstance(row[field], str) or not row[field].strip():
                raise ValueError(f"release row requires {field}")
        historical_id = HISTORICAL_IDS.get(configuration_id)
        if row["historical_id"] != historical_id:
            raise ValueError("historical identity alias changed")
        expected_harness = historical_id or (
            "dsh" if configuration_id == "deepseek-harness-cli"
            else configuration_id.removesuffix("-desktop").removesuffix("-cli")
        )
        if row["harness"] != expected_harness:
            raise ValueError("release harness identity changed")
        expected_surface = "desktop" if configuration_id.endswith("-desktop") else "cli"
        if row["surface"] != expected_surface:
            raise ValueError("release surface identity changed")
        if row["identity_state"] != "scoped":
            raise ValueError("release identity state changed; collected identity belongs in evidence")
    if tuple(ids) != CONFIGURATION_IDS:
        raise ValueError("release configuration population or order changed")
    return scope


def load_release_scope(path: Path = RELEASE_SCOPE_PATH) -> dict[str, Any]:
    """Load the additive release manifest; do not consult or mutate campaigns."""
    scope = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_release_scope(scope)
    return scope


def validate_release_statuses(
    scope: Mapping[str, Any], statuses: Sequence[Mapping[str, Any]], *,
    rankable_configuration_ids: Iterable[str] = (),
) -> Sequence[Mapping[str, Any]]:
    """Require a visible status for every scoped row without cohort-wide gating.

    A blocked preflight may be an attempt without a model submission. Attempt
    IDs refer to an external attempt ledger; evidence_refs locate its receipts.
    This function checks coverage consistency, not the receipts or score proof.
    The independently evaluated rankable population can be any scoped subset.
    """
    validate_release_scope(scope)
    if not isinstance(statuses, (list, tuple)) or len(statuses) != len(CONFIGURATION_IDS):
        raise ValueError("release statuses must report all 14 rows")
    supplied_rankable = list(rankable_configuration_ids)
    if any(not isinstance(item, str) or item not in CONFIGURATION_IDS for item in supplied_rankable):
        raise ValueError("rankable evaluator population contains unknown configuration")
    if len(supplied_rankable) != len(set(supplied_rankable)):
        raise ValueError("rankable evaluator population contains duplicate configuration")
    rankable = set(supplied_rankable)
    seen: set[str] = set()
    attempts_seen: set[str] = set()
    for row in statuses:
        if not isinstance(row, Mapping) or set(row) != _STATUS_FIELDS:
            raise ValueError("release status has invalid fields")
        configuration_id = row["configuration_id"]
        if not isinstance(configuration_id, str) or configuration_id not in CONFIGURATION_IDS or configuration_id in seen:
            raise ValueError("release statuses contain unknown or duplicate configuration")
        seen.add(configuration_id)
        state = row["state"]
        if not isinstance(state, str) or state not in STATES:
            raise ValueError("unknown release status")
        attempts = _strings(row["attempt_ids"], "attempt IDs")
        evidence = _strings(row["evidence_refs"], "evidence references")
        reasons = _strings(row["reason_ids"], "reason IDs")
        if attempts_seen.intersection(attempts):
            raise ValueError("attempt ID is attributed to multiple configurations")
        attempts_seen.update(attempts)
        if state == "unattempted":
            if attempts or evidence or reasons:
                raise ValueError("unattempted row cannot claim attempt evidence")
        elif not attempts or not evidence:
            raise ValueError("attempted row requires attempt IDs and evidence references")
        if state in {"blocked", "incomplete"} and not reasons:
            raise ValueError("blocked or incomplete row requires reason IDs")
        if state == "rankable" and reasons:
            raise ValueError("rankable row cannot carry blocking reason IDs")
        if (state == "rankable") != (configuration_id in rankable):
            raise ValueError("row rankability differs from independently evaluated population")
    return statuses


def release_scope_summary(
    scope: Mapping[str, Any], statuses: Sequence[Mapping[str, Any]], *,
    rankable_configuration_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Count attempted and rankable rows separately and retain visible blockers."""
    validate_release_statuses(scope, statuses, rankable_configuration_ids=rankable_configuration_ids)
    by_id = {row["configuration_id"]: row for row in statuses}
    ordered = [by_id[configuration_id] for configuration_id in CONFIGURATION_IDS]
    return {
        "release_id": scope["release_id"],
        "scoped_rows": len(ordered),
        "attempted_rows": sum(row["state"] != "unattempted" for row in ordered),
        "rankable_rows": sum(row["state"] == "rankable" for row in ordered),
        "blocked_rows": sum(row["state"] == "blocked" for row in ordered),
        "incomplete_rows": sum(row["state"] == "incomplete" for row in ordered),
        "unattempted_rows": sum(row["state"] == "unattempted" for row in ordered),
        "rows": ordered,
    }
