import copy
import hashlib
import json
from pathlib import Path

import pytest

from session_bench.codex_stdout_observer import build_codex_stdout_observer, UNOBSERVED

ROOT = Path(__file__).resolve().parents[1]


def inputs(number=1):
    run = ROOT / f"artifacts/survival-v1-runs/codex-cli-eval-{number}"
    return json.loads((run / "workload-instance.json").read_bytes()), dict(
        stdout_by_turn={i: (run / f"observer/turn-r{i}.stdout.jsonl").read_bytes() for i in (1, 2)},
        receipts_by_turn={i: (run / f"capture/observer-r{i}.json").read_bytes() for i in (1, 2)},
        helper_ledger=(run / "project/fixture_project/.survival-observer.jsonl").read_bytes(),
        checkout_before=(run / "project/fixture_project/snapshots/checkout.before.py").read_bytes(),
        checkout_after=(run / "project/fixture_project/checkout.py").read_bytes(),
        capture_receipt=(run / "capture/calibration-receipt.json").read_bytes(),
        helper_source=(run / "project/fixture_project/bench_check.py").read_bytes(),
        frozen_helper_source=(ROOT / "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py").read_bytes(),
    )


def mutate_stream(values, number, mutate):
    rows = [json.loads(line) for line in values["stdout_by_turn"][number].splitlines()]
    mutate(rows)
    raw = b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + b"\n"
    values["stdout_by_turn"][number] = raw
    receipt = json.loads(values["receipts_by_turn"][number])
    receipt.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), event_count=len(rows))
    values["receipts_by_turn"][number] = json.dumps(receipt).encode()


@pytest.mark.parametrize("number", [1, 2, 3])
def test_actual_stdout_helper_and_filesystem_population(number):
    workload, values = inputs(number)
    result = build_codex_stdout_observer(workload, **values)
    primary = [row for row in result["events"] if row["population_role"] == "primary_scored"]
    assert len(primary) == 13
    assert [row["id"] for row in primary if row["kind"] == "action"] == ["action-inspect", "action-baseline", "action-edit", "action-final"]
    edit = next(row for row in primary if row["id"] == "result-edit")
    assert "exit_code" not in edit["fields"]
    assert all("call_id" not in row["fields"] for row in primary)
    responses = [row for row in primary if row["kind"] == "assistant_response"]
    assert all("usage" not in row["fields"] and "model" not in row["fields"] for row in responses)
    assert all("unscored_turn_usage_trace" in row["fields"] for row in responses)
    assert not any(row["kind"] == "usage_total" for row in result["events"])
    assert set(UNOBSERVED) == {"attribution.model_config", "attribution.usage", "attribution.token_semantics", "attribution.reconciliation"}


@pytest.mark.parametrize("field", ["helper_ledger", "checkout_before", "checkout_after", "helper_source"])
def test_independent_retained_inputs_bound(field):
    workload, values = inputs()
    values[field] += b" "
    with pytest.raises(ValueError):
        build_codex_stdout_observer(workload, **values)


def test_stdout_rejects_receipt_mismatch():
    workload, values = inputs()
    values["stdout_by_turn"][1] += b" "
    with pytest.raises(ValueError, match="receipt"):
        build_codex_stdout_observer(workload, **values)


def test_duplicate_stdout_completion_rejected():
    workload, values = inputs()
    mutate_stream(values, 1, lambda rows: rows.insert(5, copy.deepcopy(rows[4])))
    with pytest.raises(ValueError, match="duplicate stdout completion"):
        build_codex_stdout_observer(workload, **values)


def test_fake_call_identity_cannot_be_native_identity():
    workload, values = inputs()
    mutate_stream(values, 1, lambda rows: [row["item"].update(call_id="forged-native-call") for row in rows if "item" in row])
    result = build_codex_stdout_observer(workload, **values)
    assert all("call_id" not in row["fields"] for row in result["events"])


def test_missing_edit_completion_cannot_be_supplemented_from_filesystem():
    workload, values = inputs()
    mutate_stream(values, 2, lambda rows: rows.__delitem__(6))
    with pytest.raises(ValueError):
        build_codex_stdout_observer(workload, **values)


def test_final_check_before_edit_cannot_supply_final_after_relation():
    workload, values = inputs()
    def reorder(rows):
        rows[5:9] = rows[7:9] + rows[5:7]
    mutate_stream(values, 2, reorder)
    with pytest.raises(ValueError, match="final check must start after edit completion"):
        build_codex_stdout_observer(workload, **values)


def test_final_check_started_during_edit_cannot_supply_final_after_relation():
    workload, values = inputs()
    def reorder(rows):
        rows[6], rows[7] = rows[7], rows[6]
    mutate_stream(values, 2, reorder)
    with pytest.raises(ValueError, match="final check must start after edit completion"):
        build_codex_stdout_observer(workload, **values)


def test_baseline_before_inspect_rejects_inconsistent_helper_order():
    workload, values = inputs()
    def reorder(rows):
        rows[3:7] = rows[5:7] + rows[3:5]
    mutate_stream(values, 1, reorder)
    with pytest.raises(ValueError, match="baseline must follow inspect completion"):
        build_codex_stdout_observer(workload, **values)
