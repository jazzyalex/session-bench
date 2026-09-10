import copy
import json

import pytest

from session_bench.bundle import canonical, digest
from session_bench.decoders import decode_native
from session_bench.evaluate import evaluate_bundle
from session_bench.fixtures import build_fixture
from session_bench.__main__ import render
from session_bench.result_contract import (
    expected_evaluation_id,
    semantic_sha256,
    validate_result_contract,
)


def _result(tmp_path):
    result, _ = evaluate_bundle(build_fixture(tmp_path / "bundle"), decoder=decode_native)
    return result


def test_baseline_contract_and_stable_id(tmp_path):
    result = _result(tmp_path)
    assert validate_result_contract(result) is result
    assert result["evaluation_id"] == expected_evaluation_id(result)
    assert semantic_sha256(result) == digest(canonical(result))


@pytest.mark.parametrize("mutation", ["omit_row", "omit_metric", "omit_failed_membership", "duplicate_membership", "missing_metric"])
def test_metric_partition_rejects_population_mutations(tmp_path, mutation):
    result = _result(tmp_path)
    broken = copy.deepcopy(result)
    if mutation == "omit_row":
        broken["rows"].pop()
    elif mutation == "omit_metric":
        broken["metrics"].pop()
    elif mutation == "omit_failed_membership":
        metric = broken["metrics"][0]
        failed_id = metric["assertion_ids"][-1]
        failed_row = next(row for row in broken["rows"] if row["id"] == failed_id)
        failed_row["state"] = "fail"
        failed_row["outcome"] = "unresolved"
        failed_row["findings"] = ["synthetic_failure"]
        removed = metric["assertion_ids"].pop()
        metric["denominator"] -= 1
        metric["numerator"] = sum(row["state"] == "pass" for row in broken["rows"]
                                   if row["id"] in metric["assertion_ids"])
        metric["state"] = "fail" if any(row["state"] == "fail" for row in broken["rows"]
                                          if row["id"] in metric["assertion_ids"]) else "unresolved"
    elif mutation == "duplicate_membership":
        broken["metrics"][1]["assertion_ids"].append(broken["metrics"][1]["assertion_ids"][0])
    else:
        broken["metrics"] = []
    broken["evaluation_id"] = expected_evaluation_id(broken)
    with pytest.raises(ValueError, match="metric|partition|population"):
        validate_result_contract(broken)


def test_metric_state_precedence_and_pass_row_safety(tmp_path):
    result = _result(tmp_path)
    broken = copy.deepcopy(result)
    broken["rows"][0]["state"] = "fail"
    broken["rows"][0]["outcome"] = "unresolved"
    broken["rows"][0]["findings"] = ["synthetic_failure"]
    broken["metrics"] = [copy.deepcopy(metric) for metric in result["metrics"]]
    broken["evaluation_id"] = expected_evaluation_id(broken)
    with pytest.raises(ValueError, match="population/count/state"):
        validate_result_contract(broken)

    broken = copy.deepcopy(result)
    broken["rows"][0]["findings"] = ["unexpected_native_event"]
    broken["evaluation_id"] = expected_evaluation_id(broken)
    with pytest.raises(ValueError, match="passes with blocking findings"):
        validate_result_contract(broken)


def test_empty_result_only_all_zero_unresolved_is_allowed(tmp_path):
    result = _result(tmp_path)
    empty = copy.deepcopy(result)
    empty["rows"] = []
    empty["metrics"] = [{"scope": "all", "unit": "assertions", "numerator": 0,
                          "denominator": 0, "state": "unresolved", "assertion_ids": []}]
    empty["evaluation_id"] = expected_evaluation_id(empty)
    assert validate_result_contract(empty) is empty
    empty["metrics"][0]["state"] = "pass"
    empty["evaluation_id"] = expected_evaluation_id(empty)
    with pytest.raises(ValueError, match="empty result"):
        validate_result_contract(empty)


def test_stale_id_rejected_after_result_tamper(tmp_path):
    result = _result(tmp_path)
    tampered = copy.deepcopy(result)
    tampered["capture_id"] = "tampered-capture"
    with pytest.raises(ValueError, match="stale"):
        validate_result_contract(tampered)


def test_render_receipt_is_verified_and_tampering_is_rejected(tmp_path):
    result = _result(tmp_path)
    receipt = {"semantic_sha256": semantic_sha256(result)}
    assert "Receipt: **verified" in render(result, receipt)
    assert "receiptless; semantic result unverified" in render(result)

    tampered = copy.deepcopy(result)
    tampered["capture_id"] = "tampered-capture"
    tampered["evaluation_id"] = expected_evaluation_id(tampered)
    with pytest.raises(ValueError, match="receipt semantic digest mismatch"):
        render(tampered, receipt)


def test_render_rejects_stale_result_even_when_receiptless(tmp_path):
    result = _result(tmp_path)
    stale = copy.deepcopy(result)
    stale["capture_id"] = "tampered-capture"
    with pytest.raises(ValueError, match="stale"):
        render(stale)


@pytest.mark.parametrize("evidence_key", ["locators", "observation_ids"])
def test_pass_row_requires_evidence_arrays(tmp_path, evidence_key):
    result = _result(tmp_path)
    broken = copy.deepcopy(result)
    row = next(row for row in broken["rows"] if row["state"] == "pass")
    row[evidence_key] = []
    broken["evaluation_id"] = expected_evaluation_id(broken)
    receipt = {"semantic_sha256": semantic_sha256(broken)}
    with pytest.raises(ValueError, match="evidence|locator|observation"):
        render(broken, receipt)


def test_fail_row_cannot_claim_retained_reconstruction(tmp_path):
    result = _result(tmp_path)
    broken = copy.deepcopy(result)
    row = next(row for row in broken["rows"] if row["state"] == "pass")
    row["state"] = "fail"
    row["outcome"] = "retained_and_reconstructed"
    row["findings"] = ["synthetic_failure"]
    broken["evaluation_id"] = expected_evaluation_id(broken)
    receipt = {"semantic_sha256": semantic_sha256(broken)}
    with pytest.raises(ValueError, match="outcome|state"):
        render(broken, receipt)


def test_unresolved_row_cannot_claim_verified_absence(tmp_path):
    result = _result(tmp_path)
    broken = copy.deepcopy(result)
    row = next(row for row in broken["rows"] if row["state"] == "pass")
    row["state"] = "unresolved"
    row["outcome"] = "verified_absent"
    row["findings"] = ["synthetic_uncertainty"]
    broken["evaluation_id"] = expected_evaluation_id(broken)
    receipt = {"semantic_sha256": semantic_sha256(broken)}
    with pytest.raises(ValueError, match="outcome|state"):
        render(broken, receipt)


def test_invalid_evidence_cannot_have_passing_rows(tmp_path):
    result = _result(tmp_path)
    result['evidence_state'] = 'invalid'
    result['evaluation_id'] = expected_evaluation_id(result)
    with pytest.raises(ValueError, match='valid capture'):
        validate_result_contract(result)


def test_empty_metric_still_requires_assertion_units(tmp_path):
    result = _result(tmp_path)
    result['rows'] = []
    result['metrics'] = [{'scope':'all','unit':'events','numerator':0,'denominator':0,
                          'state':'unresolved','assertion_ids':[]}]
    result['evaluation_id'] = expected_evaluation_id(result)
    with pytest.raises(ValueError, match='assertion units'):
        validate_result_contract(result)


@pytest.mark.parametrize('locator',[{}, {'artifact_id':'native-session'}])
def test_nonempty_but_invalid_locator_cannot_support_pass(tmp_path,locator):
    from session_bench.__main__ import render
    result = _result(tmp_path)
    result['rows'][0]['locators'] = [locator]
    result['evaluation_id'] = expected_evaluation_id(result)
    with pytest.raises(ValueError, match='native source locator'):
        render(result, {'semantic_sha256':semantic_sha256(result)})
