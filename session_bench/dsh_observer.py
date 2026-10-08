"""DSH stdout-only response usage binding; native records are never inputs."""
from __future__ import annotations
import copy
import hashlib

from .dsh_live import strict_json, DSHSemanticError, stdout_projection

BUCKETS = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
           "cacheReadTokens": "cache_read_tokens", "cacheWriteTokens": "cache_write_tokens"}


def bind_dsh_stdout_usage(observer, *, stdout_by_turn):
    """Bind final-step usage before final; sum only independently known buckets.

    DSH emits text -> step_end -> completed turn_end -> final, unlike the generic
    OpenCode observer ordering. A selected step must contain exactly the displayed
    final text and no tools. Four named buckets suffice; missing reasoning remains
    omitted. totalTokens is not treated as a session total or a semantic bucket.
    """
    result = copy.deepcopy(observer)
    if any(event.get("kind") == "usage_total" for event in result["events"]):
        raise DSHSemanticError("DSH stdout binder requires an observer without inferred usage totals")
    bound = []
    for number in (1, 2):
        raw = stdout_by_turn[number]
        data = raw.encode() if isinstance(raw, str) else raw
        stdout_projection(data.decode())  # strict independent stream/session/tool boundaries
        rows = [strict_json(line) for line in data.splitlines()]
        responses = [event for event in result["events"] if event.get("kind") == "assistant_response" and event["id"] == f"response-r{number}"]
        if len(responses) != 1 or not rows or rows[-1].get("type") != "final" or rows[-1].get("text") != responses[0]["fields"].get("text"):
            raise DSHSemanticError("DSH stdout usage lacks exact independent displayed response binding")
        endings = [(index, row) for index, row in enumerate(rows) if row.get("type") == "status" and row.get("phase") == "turn_end"]
        if len(endings) != 1 or endings[0][0] != len(rows) - 2 or endings[0][1].get("turn") != number or endings[0][1].get("reason") != {"kind": "completed"}:
            raise DSHSemanticError("DSH stdout usage lacks completed final turn boundary")
        index = len(rows) - 3
        finish = rows[index]
        if finish.get("type") != "status" or finish.get("phase") != "step_end" or finish.get("turn") != number or type(finish.get("step")) is not int:
            raise DSHSemanticError("DSH stdout final usage step boundary is ambiguous")
        if len([row for row in rows if row.get("type") == "status" and row.get("phase") == "step_end" and row.get("turn") == number and row.get("step") == finish["step"]]) != 1:
            raise DSHSemanticError("DSH stdout final step completion is duplicated")
        starts = [(position, row) for position, row in enumerate(rows[:index]) if row.get("type") == "status" and row.get("phase") == "step_start" and row.get("turn") == number and row.get("step") == finish["step"]]
        if len(starts) != 1:
            raise DSHSemanticError("DSH stdout final step is not uniquely delimited")
        segment = rows[starts[0][0] + 1:index]
        text = [row.get("text") for row in segment if row.get("type") == "text"]
        if text != [rows[-1]["text"]] or any(row.get("type") not in {"thinking", "text"} for row in segment):
            raise DSHSemanticError("DSH usage step is not solely the displayed final response")
        usage = finish.get("usage")
        if not isinstance(usage, dict):
            continue
        aliases = dict(BUCKETS)
        if "reasoningTokens" in usage:
            aliases["reasoningTokens"] = "reasoning_tokens"
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in aliases):
            continue  # malformed or partial usage cannot establish a total
        tokens = {field: usage[key] for key, field in aliases.items()}
        response = responses[0]
        response["fields"].update(usage_id=f"usage-r{number}", usage=tokens,
            usage_source={"stdout_sha256": hashlib.sha256(data).hexdigest(), "record_line": index + 1,
                          "turn": number, "step": finish["step"], "scope": "displayed_final_response_step"})
        bound.append((response, tokens))
    if len(bound) == 2:
        common = set(bound[0][1]) & set(bound[1][1])
        event = {"id": "usage-total", "kind": "usage_total", "boundary": "harness_received",
                 "population_role": "supporting", "source": "usage_trace",
                 "sequence": max(event["sequence"] for event in result["events"]) + 1,
                 "session_id": bound[0][0]["session_id"], "metric_ids": ["attribution.reconciliation"],
                 "fields": {"usage_ids": [response["fields"]["usage_id"] for response, _ in bound],
                            **{field: sum(tokens[field] for _, tokens in bound) for field in sorted(common)},
                            "reconciliation": "sum of two independently observed displayed-response usage records; known common buckets only"}}
        result["events"].append(event)
    result["method"] += "; DSH final-step usage is bound from stdout before final; unknown reasoning omitted, no model-step or provider total substituted"
    return result
