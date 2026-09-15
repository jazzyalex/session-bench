"""Exact, storage-neutral scoring for the Session-Bench survival-v1 rubric.

The scorer accepts only a canonical measurement document.  It does not discover or
decode native stores, inspect private data, or invoke a vendor executable.  JSONL and
SQLite are merely two encodings of the same versioned document.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "session-bench-survival-input-v1"

TARGET_CONFIGURATIONS = (
    "codex-cli",
    "codex-desktop",
    "cursor-cli",
    "cursor-desktop",
    "opencode-cli",
)
CLI_DESKTOP_PAIRS = (
    frozenset({"codex-cli", "codex-desktop"}),
    frozenset({"cursor-cli", "cursor-desktop"}),
)

RESOLVED_STATES = frozenset({"measured", "native_absent", "contradiction"})
BLOCKING_STATES = frozenset(
    {"unresolved", "decoder_unsupported", "unexercised", "invalid_capture"}
)
ALLOWED_STATES = RESOLVED_STATES | BLOCKING_STATES


@dataclass(frozen=True)
class MetricSpec:
    category: str
    points: Fraction
    minimum_observed: int


METRICS: dict[str, MetricSpec] = {
    "work.submitted_turns": MetricSpec("work_reconstruction", Fraction(7), 2),
    "work.visible_responses": MetricSpec("work_reconstruction", Fraction(7), 2),
    "work.actions": MetricSpec("work_reconstruction", Fraction(7), 4),
    "work.results": MetricSpec("work_reconstruction", Fraction(7), 4),
    "work.changed_files": MetricSpec("work_reconstruction", Fraction(7), 1),
    "causal.action_result": MetricSpec("causal_links", Fraction(10), 4),
    "causal.turn_response": MetricSpec("causal_links", Fraction(10), 2),
    "revision.r1": MetricSpec("revision_trace", Fraction(15, 4), 1),
    "revision.r2": MetricSpec("revision_trace", Fraction(15, 4), 1),
    "revision.r1_r2_order": MetricSpec("revision_trace", Fraction(15, 4), 1),
    "revision.final_after_r2": MetricSpec("revision_trace", Fraction(15, 4), 1),
    "attribution.model_config": MetricSpec("response_attribution", Fraction(5), 2),
    "attribution.usage": MetricSpec("response_attribution", Fraction(8), 2),
    "attribution.token_semantics": MetricSpec("response_attribution", Fraction(4), 2),
    "attribution.reconciliation": MetricSpec("response_attribution", Fraction(3), 1),
    "portable.complete_root": MetricSpec("portable_archive", Fraction(5, 2), 1),
    "portable.companions": MetricSpec("portable_archive", Fraction(5, 2), 1),
    "portable.isolated_decode": MetricSpec("portable_archive", Fraction(5, 2), 1),
    "portable.canonical_equality": MetricSpec("portable_archive", Fraction(5, 2), 1),
}

CATEGORY_POINTS: dict[str, Fraction] = {
    "work_reconstruction": Fraction(35),
    "causal_links": Fraction(20),
    "revision_trace": Fraction(15),
    "response_attribution": Fraction(20),
    "portable_archive": Fraction(10),
}

WEIGHT_VECTORS: dict[str, dict[str, int]] = {
    "baseline": {
        "work_reconstruction": 35,
        "causal_links": 20,
        "revision_trace": 15,
        "response_attribution": 20,
        "portable_archive": 10,
    },
    "equal": {
        "work_reconstruction": 20,
        "causal_links": 20,
        "revision_trace": 20,
        "response_attribution": 20,
        "portable_archive": 20,
    },
    "reconstruction_heavy": {
        "work_reconstruction": 45,
        "causal_links": 15,
        "revision_trace": 15,
        "response_attribution": 15,
        "portable_archive": 10,
    },
    "portability_heavy": {
        "work_reconstruction": 25,
        "causal_links": 15,
        "revision_trace": 15,
        "response_attribution": 20,
        "portable_archive": 25,
    },
}


@dataclass(frozen=True)
class CategoryScore:
    category: str
    known_points: Fraction
    known_possible_points: Fraction
    possible_min: Fraction
    possible_max: Fraction
    resolved_metrics: int
    total_metrics: int
    score: Fraction | None

    @property
    def complete(self) -> bool:
        return self.resolved_metrics == self.total_metrics

    @property
    def coverage(self) -> Fraction:
        return Fraction(self.resolved_metrics, self.total_metrics)

    @property
    def known_quality(self) -> Fraction | None:
        if self.known_possible_points == 0:
            return None
        return self.known_points / self.known_possible_points

    @property
    def fraction(self) -> Fraction | None:
        maximum = CATEGORY_POINTS[self.category]
        return None if self.score is None else self.score / maximum

    def display(self) -> dict[str, str | int | None]:
        return {
            "score": display_decimal(self.score),
            "known_points": display_decimal(self.known_points),
            "known_possible_points": display_decimal(self.known_possible_points),
            "known_quality_percent": display_decimal(
                self.known_quality * 100 if self.known_quality is not None else None
            ),
            "coverage": f"{self.resolved_metrics}/{self.total_metrics}",
            "possible_min": display_decimal(self.possible_min),
            "possible_max": display_decimal(self.possible_max),
            "possible_percent_min": display_decimal(
                self.possible_min * 100 / CATEGORY_POINTS[self.category]
            ),
            "possible_percent_max": display_decimal(
                self.possible_max * 100 / CATEGORY_POINTS[self.category]
            ),
        }


@dataclass(frozen=True)
class RunScore:
    run_id: str
    configuration_id: str
    repetition: int
    metrics: Mapping[str, Fraction | None]
    categories: Mapping[str, CategoryScore]
    overall: Fraction | None
    portable_gate: bool
    rankable: bool
    blockers: tuple[str, ...]

    def display(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "configuration_id": self.configuration_id,
            "repetition": self.repetition,
            "overall": display_decimal(self.overall),
            "rankable": self.rankable,
            "portable_gate": self.portable_gate,
            "metrics": {key: display_decimal(value * 100 if value is not None else None)
                        for key, value in self.metrics.items()},
            "categories": {key: value.display() for key, value in self.categories.items()},
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class ConfigurationScore:
    configuration_id: str
    runs: tuple[RunScore, ...]
    categories: Mapping[str, Fraction | None]
    category_ranges: Mapping[str, tuple[Fraction, Fraction] | None]
    overall: Fraction | None
    overall_range: tuple[Fraction, Fraction] | None
    sensitivity: Mapping[str, Fraction] | None
    portable_gate: bool
    rankable: bool
    blockers: tuple[str, ...]

    def display(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "overall": display_decimal(self.overall),
            "range": None
            if self.overall_range is None
            else [display_decimal(value) for value in self.overall_range],
            "rankable": self.rankable,
            "portable_gate": self.portable_gate,
            "categories": {key: display_decimal(value) for key, value in self.categories.items()},
            "category_ranges": {
                key: None
                if value is None
                else [display_decimal(point) for point in value]
                for key, value in self.category_ranges.items()
            },
            "sensitivity": None
            if self.sensitivity is None
            else {key: display_decimal(value) for key, value in self.sensitivity.items()},
            "blockers": list(self.blockers),
        }


def display_decimal(value: Fraction | None) -> str | None:
    """Render one decimal with round-half-up; calculations stay rational."""
    if value is None:
        return None
    with localcontext() as context:
        context.prec = max(50, len(str(abs(value.numerator))) + len(str(value.denominator)) + 4)
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")


def _strict_json(data: bytes, source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"{source}: duplicate JSON key {key!r}")
            value[key] = item
        return value

    def constant(value: str) -> Any:
        raise ValueError(f"{source}: non-finite number {value}")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source}: invalid JSON: {exc}") from exc


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"{label}: wrong fields (missing={missing}, extra={extra})")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_input(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy the strict survival-v1 canonical input document."""
    if not isinstance(document, Mapping):
        raise ValueError("survival input must be an object")
    _exact_keys(
        document,
        {"schema_version", "run_id", "configuration_id", "repetition", "metrics"},
        "survival input",
    )
    if document["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}")
    for field in ("run_id", "configuration_id"):
        if not isinstance(document[field], str) or not document[field]:
            raise ValueError(f"{field} must be a non-empty string")
    if not _is_int(document["repetition"]) or document["repetition"] < 1:
        raise ValueError("repetition must be a positive integer")
    rows = document["metrics"]
    if not isinstance(rows, list):
        raise ValueError("metrics must be an array")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, row in enumerate(rows):
        label = f"metrics[{index}]"
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} must be an object")
        _exact_keys(
            row,
            {"id", "state", "correct", "observed_eligible", "decoded_eligible"},
            label,
        )
        metric_id = row["id"]
        if metric_id not in METRICS:
            raise ValueError(f"{label}.id is unknown: {metric_id!r}")
        if metric_id in ids:
            raise ValueError(f"duplicate metric row: {metric_id}")
        ids.add(metric_id)
        state = row["state"]
        if state == "not_applicable":
            raise ValueError("not_applicable is forbidden for survival-v1 scored metrics")
        if state not in ALLOWED_STATES:
            raise ValueError(f"{label}.state is invalid: {state!r}")
        counts = (row["correct"], row["observed_eligible"], row["decoded_eligible"])
        if any(not _is_int(value) or value < 0 for value in counts):
            raise ValueError(f"{label} counts must be nonnegative integers")
        correct, observed, decoded = counts
        denominator = max(observed, decoded)
        if correct > min(observed, decoded):
            raise ValueError(f"{label}.correct cannot exceed either eligible population")
        if state in RESOLVED_STATES and denominator == 0:
            raise ValueError(f"{label}: a resolved metric needs a positive denominator")
        if state in RESOLVED_STATES and observed < METRICS[metric_id].minimum_observed:
            raise ValueError(
                f"{label}: resolved metric observed population is below the protocol minimum"
            )
        if state in {"native_absent", "contradiction"} and correct != 0:
            raise ValueError(f"{label}: {state} must contribute zero correct records")
        normalized.append(dict(row))
    missing = set(METRICS) - ids
    if missing:
        raise ValueError(f"missing required metric rows: {sorted(missing)}")
    response_population = {
        next(row["observed_eligible"] for row in normalized if row["id"] == metric_id)
        for metric_id in (
            "work.visible_responses",
            "attribution.model_config",
            "attribution.usage",
            "attribution.token_semantics",
        )
    }
    if len(response_population) != 1:
        raise ValueError("all response-scoped metrics must use the same observed population")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": document["run_id"],
        "configuration_id": document["configuration_id"],
        "repetition": document["repetition"],
        "metrics": normalized,
    }


def _load_jsonl(path: Path) -> dict[str, Any]:
    rows: list[Any] = []
    for number, raw in enumerate(path.read_bytes().splitlines(), 1):
        if not raw.strip():
            raise ValueError(f"{path}: blank JSONL line {number}")
        rows.append(_strict_json(raw, f"{path}:{number}"))
    if not rows:
        raise ValueError(f"{path}: empty JSONL input")
    header = rows[0]
    if not isinstance(header, Mapping):
        raise ValueError("JSONL header must be an object")
    _exact_keys(
        header,
        {"record_type", "schema_version", "run_id", "configuration_id", "repetition"},
        "JSONL header",
    )
    if header["record_type"] != "run":
        raise ValueError("first JSONL row must be record_type 'run'")
    metrics = []
    for index, row in enumerate(rows[1:], 2):
        if not isinstance(row, Mapping):
            raise ValueError(f"JSONL line {index} must be an object")
        _exact_keys(
            row,
            {
                "record_type",
                "id",
                "state",
                "correct",
                "observed_eligible",
                "decoded_eligible",
            },
            f"JSONL line {index}",
        )
        if row["record_type"] != "metric":
            raise ValueError(f"JSONL line {index} must be record_type 'metric'")
        metrics.append({key: value for key, value in row.items() if key != "record_type"})
    return validate_input(
        {
            "schema_version": header["schema_version"],
            "run_id": header["run_id"],
            "configuration_id": header["configuration_id"],
            "repetition": header["repetition"],
            "metrics": metrics,
        }
    )


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    if table not in {"run", "metrics"}:
        raise ValueError("unexpected SQLite table")
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]


def _load_sqlite(path: Path) -> dict[str, Any]:
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables != {"run", "metrics"}:
            raise ValueError(f"SQLite input must contain exactly run and metrics tables: {tables}")
        run_columns = ["schema_version", "run_id", "configuration_id", "repetition"]
        metric_columns = [
            "position",
            "id",
            "state",
            "correct",
            "observed_eligible",
            "decoded_eligible",
        ]
        if _sqlite_columns(connection, "run") != run_columns:
            raise ValueError("SQLite run table has the wrong columns or order")
        if _sqlite_columns(connection, "metrics") != metric_columns:
            raise ValueError("SQLite metrics table has the wrong columns or order")
        run_rows = connection.execute(
            "SELECT schema_version, run_id, configuration_id, repetition FROM run"
        ).fetchall()
        if len(run_rows) != 1:
            raise ValueError("SQLite run table must contain exactly one row")
        raw_metrics = connection.execute(
            "SELECT position, id, state, correct, observed_eligible, decoded_eligible "
            "FROM metrics ORDER BY position"
        ).fetchall()
        if [row[0] for row in raw_metrics] != list(range(1, len(raw_metrics) + 1)):
            raise ValueError("SQLite metric positions must be contiguous and one-based")
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"invalid SQLite survival input: {exc}") from exc
    finally:
        connection.close()
    header = run_rows[0]
    return validate_input(
        {
            "schema_version": header[0],
            "run_id": header[1],
            "configuration_id": header[2],
            "repetition": header[3],
            "metrics": [
                {
                    "id": row[1],
                    "state": row[2],
                    "correct": row[3],
                    "observed_eligible": row[4],
                    "decoded_eligible": row[5],
                }
                for row in raw_metrics
            ],
        }
    )


def load_survival_input(path: str | Path) -> dict[str, Any]:
    """Load a strict canonical input from JSON, JSONL, or read-only SQLite."""
    path = Path(path)
    if path.suffix == ".jsonl":
        return _load_jsonl(path)
    if path.suffix in {".sqlite", ".db"}:
        return _load_sqlite(path)
    if path.suffix == ".json":
        value = _strict_json(path.read_bytes(), str(path))
        return validate_input(value)
    raise ValueError(f"unsupported survival input encoding: {path.suffix or '<none>'}")


def _metric_fraction(row: Mapping[str, Any]) -> Fraction | None:
    state = row["state"]
    if state in BLOCKING_STATES:
        return None
    if state in {"native_absent", "contradiction"}:
        return Fraction(0)
    return Fraction(row["correct"], max(row["observed_eligible"], row["decoded_eligible"]))


def score_run(document: Mapping[str, Any]) -> RunScore:
    """Score one canonical run using exact rational arithmetic."""
    value = validate_input(document)
    category_rows: dict[str, list[tuple[MetricSpec, Mapping[str, Any]]]] = {
        category: [] for category in CATEGORY_POINTS
    }
    metric_scores: dict[str, Fraction | None] = {}
    blockers: list[str] = []
    for row in value["metrics"]:
        spec = METRICS[row["id"]]
        category_rows[spec.category].append((spec, row))
        metric_scores[row["id"]] = _metric_fraction(row)
        if row["state"] in BLOCKING_STATES:
            blockers.append(f"{row['id']}:{row['state']}")

    categories: dict[str, CategoryScore] = {}
    for category, rows in category_rows.items():
        known_points = Fraction(0)
        known_possible = Fraction(0)
        resolved = 0
        for spec, row in rows:
            fraction = _metric_fraction(row)
            if fraction is not None:
                resolved += 1
                known_possible += spec.points
                known_points += spec.points * fraction
        total = len(rows)
        maximum = CATEGORY_POINTS[category]
        complete = resolved == total
        categories[category] = CategoryScore(
            category=category,
            known_points=known_points,
            known_possible_points=known_possible,
            possible_min=known_points,
            possible_max=known_points + maximum - known_possible,
            resolved_metrics=resolved,
            total_metrics=total,
            score=known_points if complete else None,
        )

    portable = categories["portable_archive"]
    portable_gate = portable.complete and portable.score == CATEGORY_POINTS["portable_archive"]
    complete = all(category.complete for category in categories.values())
    rankable = complete and portable_gate
    overall = (
        sum(
            (category.score for category in categories.values() if category.score is not None),
            Fraction(0),
        )
        if rankable
        else None
    )
    if complete and not portable_gate:
        blockers.append("portable_archive:hard_gate")
    return RunScore(
        run_id=value["run_id"],
        configuration_id=value["configuration_id"],
        repetition=value["repetition"],
        metrics=metric_scores,
        categories=categories,
        overall=overall,
        portable_gate=portable_gate,
        rankable=rankable,
        blockers=tuple(blockers),
    )


def weighted_total(
    category_fractions: Mapping[str, Fraction], vector: str = "baseline"
) -> Fraction:
    """Apply one frozen sensitivity vector to complete category fractions."""
    if vector not in WEIGHT_VECTORS:
        raise ValueError(f"unknown weight vector: {vector}")
    if set(category_fractions) != set(CATEGORY_POINTS):
        raise ValueError("category fractions must contain the exact five categories")
    for category, value in category_fractions.items():
        if not isinstance(value, Fraction) or value < 0 or value > 1:
            raise ValueError(f"{category} fraction must be a Fraction between zero and one")
    return sum(
        (
            category_fractions[category] * weight
            for category, weight in WEIGHT_VECTORS[vector].items()
        ),
        Fraction(0),
    )


def aggregate_configuration(runs: Sequence[RunScore]) -> ConfigurationScore:
    """Equal-weight exactly three evaluated repetitions for one configuration."""
    if len(runs) != 3:
        raise ValueError("a configuration requires exactly three evaluated repetitions")
    configuration_ids = {run.configuration_id for run in runs}
    if len(configuration_ids) != 1:
        raise ValueError("all runs must belong to one configuration")
    repetitions = [run.repetition for run in runs]
    if set(repetitions) != {1, 2, 3}:
        raise ValueError("configuration repetitions must be exactly 1, 2, and 3")
    if len({run.run_id for run in runs}) != 3:
        raise ValueError("configuration run_id values must be unique")
    ordered = tuple(sorted(runs, key=lambda run: run.repetition))
    category_means: dict[str, Fraction | None] = {}
    category_ranges: dict[str, tuple[Fraction, Fraction] | None] = {}
    for category in CATEGORY_POINTS:
        values = [run.categories[category].score for run in ordered]
        exact_values = [value for value in values if value is not None]
        category_means[category] = (
            None
            if any(value is None for value in values)
            else sum(exact_values, Fraction(0)) / 3
        )
        category_ranges[category] = (
            None
            if len(exact_values) != 3
            else (min(exact_values), max(exact_values))
        )
    portable_gate = all(run.portable_gate for run in ordered)
    rankable = portable_gate and all(run.rankable for run in ordered)
    blockers = tuple(
        f"repetition {run.repetition}: {blocker}"
        for run in ordered
        for blocker in run.blockers
    )
    if rankable:
        overall_values = [run.overall for run in ordered]
        exact_overalls = [value for value in overall_values if value is not None]
        assert len(exact_overalls) == 3
        overall = sum(exact_overalls, Fraction(0)) / 3
        overall_range = (min(exact_overalls), max(exact_overalls))
        exact_categories = {
            category: value
            for category, value in category_means.items()
            if value is not None
        }
        assert len(exact_categories) == len(CATEGORY_POINTS)
        category_fractions = {
            category: exact_categories[category] / CATEGORY_POINTS[category]
            for category in CATEGORY_POINTS
        }
        sensitivity = {
            vector: weighted_total(category_fractions, vector) for vector in WEIGHT_VECTORS
        }
    else:
        overall = None
        overall_range = None
        sensitivity = None
    return ConfigurationScore(
        configuration_id=next(iter(configuration_ids)),
        runs=ordered,
        categories=category_means,
        category_ranges=category_ranges,
        overall=overall,
        overall_range=overall_range,
        sensitivity=sensitivity,
        portable_gate=portable_gate,
        rankable=rankable,
        blockers=blockers,
    )


def competition_ranks(
    configurations: Iterable[ConfigurationScore], vector: str = "baseline"
) -> dict[str, int]:
    """Return exact competition ranks for rankable configurations only."""
    if vector not in WEIGHT_VECTORS:
        raise ValueError(f"unknown weight vector: {vector}")
    scored: list[tuple[str, Fraction]] = []
    for configuration in configurations:
        if configuration.rankable and configuration.sensitivity is not None:
            scored.append((configuration.configuration_id, configuration.sensitivity[vector]))
    if len(scored) < 2:
        return {}
    scored.sort(key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int] = {}
    previous: Fraction | None = None
    rank = 0
    for position, (configuration_id, score) in enumerate(scored, 1):
        if score != previous:
            rank = position
            previous = score
        ranks[configuration_id] = rank
    return ranks


def leaderboard_ranks(
    configurations: Iterable[ConfigurationScore], vector: str = "baseline"
) -> dict[str, int]:
    """Rank only an eligible first-edition cohort.

    Public ranking requires at least three qualified frozen configurations and at
    least one qualified CLI/Desktop pair from Codex or Cursor. Unknown or duplicate
    configuration IDs are contract errors rather than silently omitted rows.
    """
    if vector not in WEIGHT_VECTORS:
        raise ValueError(f"unknown weight vector: {vector}")
    qualified = _qualified_leaderboard_cohort(configurations)
    if qualified is None:
        return {}
    return competition_ranks(qualified, vector)


def _qualified_leaderboard_cohort(
    configurations: Iterable[ConfigurationScore],
) -> list[ConfigurationScore] | None:
    """Validate frozen identities and return the rankable public cohort, if eligible."""
    values = list(configurations)
    ids = [configuration.configuration_id for configuration in values]
    unknown = sorted(set(ids) - set(TARGET_CONFIGURATIONS))
    if unknown:
        raise ValueError(f"unknown target configuration IDs: {unknown}")
    if len(ids) != len(set(ids)):
        raise ValueError("leaderboard configuration IDs must be unique")
    qualified = [configuration for configuration in values if configuration.rankable]
    qualified_ids = {configuration.configuration_id for configuration in qualified}
    if len(qualified) < 3:
        return None
    if not any(pair <= qualified_ids for pair in CLI_DESKTOP_PAIRS):
        return None
    return qualified


def sensitivity_winners(
    configurations: Iterable[ConfigurationScore],
) -> tuple[dict[str, tuple[str, ...]], bool]:
    """Report vector winners and whether the same winner set survives all vectors."""
    values = list(configurations)
    winners: dict[str, tuple[str, ...]] = {}
    for vector in WEIGHT_VECTORS:
        ranks = competition_ranks(values, vector)
        winners[vector] = tuple(sorted(key for key, rank in ranks.items() if rank == 1))
    nonempty = [winner for winner in winners.values() if winner]
    stable = bool(nonempty) and len(nonempty) == len(winners) and len(set(nonempty)) == 1
    return winners, stable


def leaderboard_sensitivity_winners(
    configurations: Iterable[ConfigurationScore],
) -> tuple[dict[str, tuple[str, ...]], bool]:
    """Return public sensitivity winners only for one eligible frozen cohort."""
    qualified = _qualified_leaderboard_cohort(configurations)
    if qualified is None:
        return {vector: () for vector in WEIGHT_VECTORS}, False
    return sensitivity_winners(qualified)
