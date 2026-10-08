import copy
import json
from pathlib import Path

import pytest

from session_bench.dsh_live import DSHSemanticError, decode_dsh_native
from session_bench.dsh_observer import bind_dsh_stdout_usage
from session_bench.live_metric_comparator import compare_survival_run

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "artifacts/survival-v1-runs/dsh-cal-20260929-2/qualification-v5"


def inputs():
    return json.loads((RUN / "observer.json").read_bytes()), {number: (RUN / f"r{number}.stdout.jsonl").read_bytes() for number in (1, 2)}


def change(stream, operation):
    rows = [json.loads(line) for line in stream.splitlines()]
    operation(rows)
    return b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + b"\n"


def test_four_known_stdout_buckets_bind_final_responses_and_honest_absent_native_reconciliation():
    observer, stdout = inputs()
    original = copy.deepcopy(observer)
    result = bind_dsh_stdout_usage(observer, stdout_by_turn=stdout)
    assert observer == original
    responses = [row for row in result["events"] if row["kind"] == "assistant_response"]
    assert [row["fields"]["usage"]["input_tokens"] for row in responses] == [349, 184]
    assert all("reasoning_tokens" not in row["fields"]["usage"] for row in responses)
    total = next(row for row in result["events"] if row["kind"] == "usage_total")
    assert total["fields"]["input_tokens"] == 533
    assert total["fields"]["output_tokens"] == 1681
    assert "reasoning_tokens" not in total["fields"]
    assert "totalTokens" not in total["fields"]
    native = decode_dsh_native(RUN / "native/session.v4.jsonl.zstd")
    rows = {row["id"]: row for row in compare_survival_run(result, native, json.loads((RUN / "portability.json").read_bytes()), configuration_id="deepseek-harness-cli", repetition=1)["metrics"]}
    assert rows["attribution.usage"]["correct"] == rows["attribution.token_semantics"]["correct"] == 2
    assert rows["attribution.reconciliation"] == {"id": "attribution.reconciliation", "observed_eligible": 1, "decoded_eligible": 0, "correct": 0, "state": "native_absent"}


@pytest.mark.parametrize("mode", ["missing", "null", "negative", "bool"])
def test_invalid_or_missing_usage_never_produces_a_total(mode):
    observer, stdout = inputs()
    def mutate(rows):
        usage = rows[-3]["usage"]
        if mode == "missing": del usage["inputTokens"]
        elif mode == "null": usage["reasoningTokens"] = None
        elif mode == "negative": usage["outputTokens"] = -1
        else: usage["cacheWriteTokens"] = True
    stdout[2] = change(stdout[2], mutate)
    result = bind_dsh_stdout_usage(observer, stdout_by_turn=stdout)
    assert not any(row["kind"] == "usage_total" for row in result["events"])
    assert "usage" not in next(row for row in result["events"] if row["id"] == "response-r2")["fields"]


@pytest.mark.parametrize("mode", ["aborted", "duplicate_end", "mismatched_text", "tool_step", "wrong_turn"])
def test_ambiguous_response_step_cannot_bind_usage(mode):
    observer, stdout = inputs()
    def mutate(rows):
        if mode == "aborted": rows[-2]["reason"] = {"kind": "aborted"}
        elif mode == "duplicate_end": rows.insert(-3, copy.deepcopy(rows[-3]))
        elif mode == "mismatched_text": rows[-4]["text"] = "different visible response"
        elif mode == "tool_step": rows.insert(-3, {"type": "unknown_payload"})
        else: rows[-3]["turn"] = 1
    stdout[2] = change(stdout[2], mutate)
    with pytest.raises(DSHSemanticError): bind_dsh_stdout_usage(observer, stdout_by_turn=stdout)
