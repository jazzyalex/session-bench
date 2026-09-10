"""Semantic result-contract checks independent of bundle/schema validation."""

from __future__ import annotations

import copy


def _canonical(value):
    # Keep this import local: bundle validation imports this module in the
    # integrated path, so importing bundle at module load would cycle.
    from .bundle import canonical

    return canonical(value)


def _digest(data):
    from .bundle import digest

    return digest(data)


def expected_evaluation_id(result: dict) -> str:
    """Return the stable ID for *result* using its stored implementation hashes."""
    pending = copy.deepcopy(result)
    pending["evaluation_id"] = "pending"
    implementation_sha256 = pending.get("implementation_sha256")
    decoded_sha256 = pending.get("decoded_sha256")
    if not isinstance(implementation_sha256, str) or not isinstance(decoded_sha256, str):
        raise ValueError("result lacks stored implementation/decoded hashes")
    return "eval-" + _digest(_canonical({
        "result": pending,
        "implementation_sha256": implementation_sha256,
        "decoded_sha256": decoded_sha256,
    }))


def semantic_sha256(result: dict) -> str:
    """Hash the canonical result bytes used by an adjacent receipt."""
    return _digest(_canonical(result))


def _field_matches(field: dict) -> bool:
    actual, expected = field.get("actual"), field.get("expected")
    comparison = field.get("comparison")
    if comparison in {"exact", "json"}:
        return _canonical(actual) == _canonical(expected)
    if comparison == "text_lf":
        return (isinstance(actual, str) and isinstance(expected, str)
                and actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n"))
    return False


def _validate_locator_shape(locator: dict) -> None:
    def sha(value):
        return isinstance(value, str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
    if not isinstance(locator, dict) or not isinstance(locator.get('artifact_id'), str) or not locator['artifact_id']:
        raise ValueError('native source locator requires artifact identity')
    if not sha(locator.get('sha256')) or not sha(locator.get('record_sha256')):
        raise ValueError('native source locator requires artifact and record digests')
    if 'line' in locator:
        values=[locator.get(key) for key in ('line','byte_start','byte_end')]
        if any(type(value) is not int for value in values) or values[0]<1 or not 0<=values[1]<values[2]:
            raise ValueError('native source locator requires valid JSONL coordinates')
    elif locator.get('table')=='events' and locator.get('column')=='payload' and type(locator.get('row_key')) is int:
        dependencies=locator.get('dependency_sha256')
        if not isinstance(dependencies,list) or not all(sha(value) for value in dependencies):
            raise ValueError('native source locator requires SQLite dependency digests')
    else:
        raise ValueError('native source locator requires supported coordinates')


def _row_state(row: dict) -> str:
    row_state = row.get("state")
    fields = row.get("fields")
    findings = row.get("findings")
    if not isinstance(fields, list) or not isinstance(findings, list):
        raise ValueError(f"row {row.get('id', '<unknown>')} has malformed fields/findings")
    for locator in row.get("locators", []):
        _validate_locator_shape(locator)
    field_states = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError(f"row {row.get('id', '<unknown>')} has malformed field")
        field_state = field.get("state")
        if field_state not in {"pass", "fail", "unresolved"}:
            raise ValueError(f"row {row.get('id', '<unknown>')} has invalid field state")
        if not isinstance(field.get("actual_present"), bool):
            raise ValueError(f"row {row.get('id', '<unknown>')} has invalid actual_present")
        if field_state == "pass":
            if not field["actual_present"]:
                raise ValueError(f"row {row.get('id', '<unknown>')} passes an absent field")
            if not _field_matches(field):
                raise ValueError(f"row {row.get('id', '<unknown>')} has forged passing field")
        elif field_state == "fail" and field["actual_present"] and _field_matches(field):
            raise ValueError(f"row {row.get('id', '<unknown>')} has forged failing field")
        field_states.append(field_state)
    if row_state == "pass":
        if not fields or any(value != "pass" for value in field_states):
            raise ValueError(f"row {row.get('id', '<unknown>')} passes without all fields passing")
        if findings:
            raise ValueError(f"row {row.get('id', '<unknown>')} passes with blocking findings")
        if row.get("outcome") != "retained_and_reconstructed":
            raise ValueError(f"row {row.get('id', '<unknown>')} pass has inconsistent outcome")
    outcome = row.get("outcome")
    if row_state == "pass":
        if not row.get("locators"):
            raise ValueError("pass row requires native source locator")
        if not row.get("observation_ids"):
            raise ValueError("pass row requires observation reference")
    if outcome == "retained_and_reconstructed" and row_state != "pass":
        raise ValueError("retained-and-reconstructed outcome requires pass state")
    if row_state == "unresolved" and outcome != "unresolved":
        raise ValueError("unresolved row requires unresolved outcome")
    if outcome in {"retained_decoder_incomplete", "verified_absent"}:
        if row_state != "fail":
            raise ValueError("causal failure outcome requires fail state")
        if not row.get("observation_ids") or not row.get("inspection_evidence_ids"):
            raise ValueError("causal failure outcome requires observation and inspection evidence")
        if outcome == "retained_decoder_incomplete" and not row.get("locators"):
            raise ValueError("retained decoder failure requires native locator")
    if "fail" in field_states and row_state != "fail":
        raise ValueError(f"row {row.get('id', '<unknown>')} has a failing field but is not fail")
    if "unresolved" in field_states and row_state == "pass":
        raise ValueError(f"row {row.get('id', '<unknown>')} has an unresolved field but passes")
    if row_state not in {"pass", "fail", "unresolved"}:
        raise ValueError(f"row {row.get('id', '<unknown>')} has invalid state")
    return row_state


def _metric_state(rows: list[dict]) -> str:
    states = {_row_state(row) for row in rows}
    if "fail" in states:
        return "fail"
    if "unresolved" in states:
        return "unresolved"
    return "pass"


def validate_result_contract(result: dict) -> dict:
    """Validate semantic result identity, row populations, and metric partition."""
    rows = result.get("rows")
    metrics = result.get("metrics")
    if not isinstance(rows, list) or not isinstance(metrics, list):
        raise ValueError("result rows and metrics must be arrays")
    row_ids = [row.get("id") for row in rows]
    if any(not isinstance(row_id, str) or not row_id for row_id in row_ids):
        raise ValueError("every result row requires an ID")
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("duplicate result row IDs")
    by_id = dict(zip(row_ids, rows))
    for row in rows:
        _row_state(row)
        if row.get("state") == "pass":
            if result.get("capture_status") != "valid" or result.get("evidence_state") != "valid":
                raise ValueError("pass row requires valid capture")
            if row.get("execution") != "valid":
                raise ValueError("pass row requires valid execution")
            if row.get("applicability") not in {"required", "optional_supported"}:
                raise ValueError("pass row has unsupported applicability")

    if any(metric.get("unit") != "assertions" for metric in metrics):
        raise ValueError("result metrics must use assertion units")
    if not rows:
        if len(metrics) != 1:
            raise ValueError("empty result requires exactly one aggregate metric")
        metric = metrics[0]
        if (metric.get("scope"), metric.get("numerator"), metric.get("denominator"),
                metric.get("state"), metric.get("assertion_ids")) != (
                    "all", 0, 0, "unresolved", []):
            raise ValueError("empty result must be unresolved 0/0")
    else:
        scenarios = {row["scenario"] for row in rows}
        seen_ids: list[str] = []
        seen_scopes: set[str] = set()
        for metric in metrics:
            if metric.get("unit") != "assertions":
                raise ValueError("result metrics must use assertion units")
            scope = metric.get("scope")
            ids = metric.get("assertion_ids")
            if scope not in scenarios:
                raise ValueError("metric scope has no result rows")
            if scope in seen_scopes:
                raise ValueError("duplicate metric scope")
            if not isinstance(ids, list) or len(set(ids)) != len(ids):
                raise ValueError("duplicate metric assertion IDs")
            selected = [by_id.get(row_id) for row_id in ids]
            if any(row is None for row in selected):
                raise ValueError("metric references unknown result row")
            if any(row["scenario"] != scope for row in selected):
                raise ValueError("metric mixes scenario populations")
            expected_state = _metric_state(selected)
            expected_numerator = sum(row["state"] == "pass" for row in selected)
            if (metric.get("denominator"), metric.get("numerator"), metric.get("state")) != (
                    len(selected), expected_numerator, expected_state):
                raise ValueError("metric population/count/state mismatch")
            seen_scopes.add(scope)
            seen_ids.extend(ids)
        if seen_scopes != scenarios:
            raise ValueError("result scenarios do not have exactly one metric")
        if sorted(seen_ids) != sorted(row_ids):
            raise ValueError("metrics do not partition result rows exactly once")

    expected_id = expected_evaluation_id(result)
    if result.get("evaluation_id") != expected_id:
        raise ValueError("stale or inconsistent evaluation_id")
    return result


# Stable integration names used by the evaluator and bundle validator.
evaluation_id = expected_evaluation_id
validate_result_semantics = validate_result_contract
