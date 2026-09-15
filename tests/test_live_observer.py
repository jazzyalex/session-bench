"""Constructed in-memory controls for the OpenCode live observer builder."""

from __future__ import annotations

import copy
import json

import pytest

from session_bench.live_metric_comparator import compare_survival_run
from session_bench.live_observer import LiveObserverError, build_opencode_live_observer

SESSION = "ses_live_observer_01"
MODEL = "opencode/muse-spark-1.3-contributor-free"
CONFIG = "opencode-cli"
RUN_SLUG = "attempt-42"
RUN_CANARY = f"SB_SURVIVAL_V1_RUN_{RUN_SLUG}"
R1_CANARY = "SB_SURVIVAL_V1_RESPONSE_R1_cafe_attempt_42"
R2_CANARY = "SB_SURVIVAL_V1_RESPONSE_R2_correction_attempt_42"
NONCE_INSPECT = "inspect-attempt-42"
NONCE_BASELINE = "baseline-attempt-42"
NONCE_FINAL = "final-attempt-42"
BEFORE = "a" * 64
AFTER = "b" * 64


def _workload() -> dict:
    return {
        "schema_version": "1.0-survival-workload",
        "protocol_version": "1.0-survival",
        "scenario_id": "survival-v1-repair",
        "fixture_id": "survival-v1-workload-fixture-0001",
        "run_id": RUN_SLUG,
        "run_canary": RUN_CANARY,
        "context_marker": "SB_SURVIVAL_V1_CONTEXT_cafe_attempt_42",
        "turns": [
            {
                "id": "turn-r1",
                "sequence": 1,
                "revision": "r1",
                "text": f"Task R1 {RUN_CANARY} run inspect then baseline. End with {R1_CANARY}.",
                "response_canary": R1_CANARY,
                "context_marker": "SB_SURVIVAL_V1_CONTEXT_cafe_attempt_42",
                "run_canary": RUN_CANARY,
            },
            {
                "id": "turn-r2",
                "sequence": 2,
                "revision": "r2",
                "text": f"Task R2 {RUN_CANARY} edit checkout then final. End with {R2_CANARY}.",
                "response_canary": R2_CANARY,
                "context_marker": "SB_SURVIVAL_V1_CONTEXT_cafe_attempt_42",
                "run_canary": RUN_CANARY,
            },
        ],
        "response_canaries": [
            {"id": "response-r1", "turn_id": "turn-r1", "value": R1_CANARY},
            {"id": "response-r2", "turn_id": "turn-r2", "value": R2_CANARY},
        ],
    }


def _controller() -> dict:
    return {
        "model": MODEL,
        "configuration": CONFIG,
        "turns": {
            "1": {"session_id": SESSION, "status": "ok"},
            "2": {"session_id": SESSION, "status": "ok"},
        },
    }


def _tool_row(call, tool, command=None, file_path=None, output=None, exit_code=None,
              completed=True):
    row: dict = {
        "type": "tool_use",
        "sessionID": SESSION,
        "callID": call,
        "tool": tool,
    }
    payload: dict = {}
    if command is not None:
        payload["command"] = command
        payload["workdir"] = "fixture_project"
    if file_path is not None:
        payload["filePath"] = file_path
    row["input"] = payload
    state: dict = {"status": "completed" if completed else "running"}
    if output is not None:
        state["output"] = output
    if exit_code is not None:
        state["metadata"] = {"exit": exit_code}
    row["state"] = state
    return row


def _stdout() -> dict[int, str]:
    r1 = [
        _tool_row("call-inspect", "bash",
                  command="python3 bench_check.py inspect",
                  output=f"SB_SURVIVAL_V1_HELPER_INSPECT_{NONCE_INSPECT} ok",
                  exit_code=0),
        _tool_row("call-baseline", "bash",
                  command="python3 bench_check.py baseline",
                  output=f"SB_SURVIVAL_V1_HELPER_BASELINE_{NONCE_BASELINE} fail",
                  exit_code=1),
        _tool_row("call-read", "read",
                  file_path="fixture_project/checkout.py",
                  output="checkout source"),
        {"type": "text", "sessionID": SESSION,
         "text": f"Baseline fails as observed.\n{R1_CANARY}"},
        {"type": "step_finish", "sessionID": SESSION, "reason": "stop",
         "tokens": {"input": 28, "output": 14, "reasoning": 1,
                    "cache_read": 0, "cache_write": 0}},
    ]
    r2 = [
        _tool_row("call-edit", "edit",
                  file_path="fixture_project/checkout.py",
                  output="edited ok"),
        _tool_row("call-final", "bash",
                  command="python3 bench_check.py final",
                  output=f"SB_SURVIVAL_V1_HELPER_FINAL_{NONCE_FINAL} ok",
                  exit_code=0),
        {"type": "text", "sessionID": SESSION,
         "text": f"Applied delivery threshold; final passes.\n{R2_CANARY}"},
        {"type": "step_finish", "sessionID": SESSION, "reason": "stop",
         "tokens": {"input": 34, "output": 17, "reasoning": 2,
                    "cache_read": 2, "cache_write": 0}},
    ]
    return {
        1: "\n".join(json.dumps(row, ensure_ascii=False) for row in r1),
        2: "\n".join(json.dumps(row, ensure_ascii=False) for row in r2),
    }


def _helper() -> str:
    rows = [
        {"id": "helper-inspect-1", "phase": "inspect", "run_canary": RUN_CANARY,
         "argv": ["python3", "bench_check.py", "inspect"],
         "cwd": "fixture_project",
         "output": f"SB_SURVIVAL_V1_HELPER_INSPECT_{NONCE_INSPECT} ok",
         "exit_code": 0},
        {"id": "helper-baseline-1", "phase": "baseline", "run_canary": RUN_CANARY,
         "argv": ["python3", "bench_check.py", "baseline"],
         "cwd": "fixture_project",
         "output": f"SB_SURVIVAL_V1_HELPER_BASELINE_{NONCE_BASELINE} fail",
         "exit_code": 1},
        {"id": "helper-final-1", "phase": "final", "run_canary": RUN_CANARY,
         "argv": ["python3", "bench_check.py", "final"],
         "cwd": "fixture_project",
         "output": f"SB_SURVIVAL_V1_HELPER_FINAL_{NONCE_FINAL} ok",
         "exit_code": 0},
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def _build(**overrides):
    kwargs = {
        "workload": _workload(),
        "controller_state": _controller(),
        "stdout_by_turn": _stdout(),
        "helper_ledger_jsonl": _helper(),
        "before_checkout_sha256": BEFORE,
        "after_checkout_sha256": AFTER,
    }
    kwargs.update(overrides)
    return build_opencode_live_observer(**kwargs)


def _events_by_kind(observer, kind):
    return [event for event in observer["events"] if event["kind"] == kind]


def test_complete_two_turn_population() -> None:
    observer = _build()

    assert observer["schema_version"] == "1.0-survival-observer"
    assert observer["protocol_version"] == "1.0-survival"
    assert observer["scenario_id"] == "survival-v1-repair"
    assert observer["independent"] is True
    assert observer["run_id"] == RUN_SLUG
    for token in ("submitted input", "live OpenCode JSON stream",
                  "helper ledger", "filesystem hashes", "usage trace"):
        assert token in observer["method"]

    turns = _events_by_kind(observer, "user_turn")
    assert [event["id"] for event in turns] == ["turn-r1", "turn-r2"]
    assert turns[0]["fields"]["text"].endswith(R1_CANARY) is False  # full prompt text
    assert RUN_CANARY in turns[0]["fields"]["text"]

    actions = [e for e in _events_by_kind(observer, "action")
               if e["population_role"] == "primary_scored"]
    # Exactly four frozen workload actions are scored.
    assert len(actions) == 4
    kinds = sorted(action["fields"]["action_kind"] for action in actions)
    assert kinds == ["edit", "inspect", "test", "test"]
    # The extra discovery read remains present but unscored.
    all_actions = _events_by_kind(observer, "action")
    assert len(all_actions) == 5
    unscored_actions = [e for e in all_actions
                        if e["population_role"] != "primary_scored"]
    assert len(unscored_actions) == 1
    read = unscored_actions[0]
    assert read["fields"]["action_kind"] == "read"
    assert "target" in read["fields"]
    assert read["fields"]["turn_id"] == "turn-r1"

    results = [e for e in _events_by_kind(observer, "result")
               if e["population_role"] == "primary_scored"]
    assert len(results) == 4
    baseline = next(r for r in results
                    if r["fields"].get("helper_nonce") == NONCE_BASELINE)
    assert baseline["fields"]["status"] == "failure"
    assert baseline["fields"]["exit_code"] == 1
    inspect = next(r for r in results
                   if r["fields"].get("helper_nonce") == NONCE_INSPECT)
    assert inspect["fields"]["status"] == "success"
    edit = next(r for r in results if r["id"] == "result-t2-1")
    assert edit["fields"]["exit_code"] == 0
    # The extra discovery read result remains present but unscored.
    all_results = _events_by_kind(observer, "result")
    assert len(all_results) == 5
    unscored_results = [e for e in all_results
                        if e["population_role"] != "primary_scored"]
    assert len(unscored_results) == 1
    assert unscored_results[0]["fields"]["action_id"] == read["id"]

    responses = _events_by_kind(observer, "assistant_response")
    assert len(responses) == 2
    assert responses[0]["fields"]["text"].endswith(R1_CANARY)
    assert responses[1]["fields"]["text"].endswith(R2_CANARY)
    assert responses[0]["fields"]["model_id"] == MODEL
    assert responses[0]["fields"]["configuration"] == MODEL
    assert responses[0]["fields"]["usage"]["input_tokens"] == 28
    assert responses[1]["fields"]["usage"]["cache_read_tokens"] == 2

    helpers = _events_by_kind(observer, "helper")
    assert len(helpers) == 6

    change = _events_by_kind(observer, "file_change")
    assert len(change) == 1
    assert change[0]["fields"]["path"] == "fixture_project/checkout.py"
    assert change[0]["fields"]["before_sha256"] == BEFORE
    assert change[0]["fields"]["after_sha256"] == AFTER

    total = _events_by_kind(observer, "usage_total")
    assert len(total) == 1
    assert total[0]["fields"]["input_tokens"] == 62
    assert total[0]["fields"]["output_tokens"] == 31
    assert total[0]["fields"]["reasoning_tokens"] == 3
    assert total[0]["fields"]["cache_read_tokens"] == 2
    assert "reconciliation" in total[0]["fields"]
    assert total[0]["fields"]["reconciliation"] == (
        "sum of 2 response-linked usage records"
    )

    portable = _events_by_kind(observer, "portable")
    assert len(portable) == 1
    for key in ("complete_root", "companions_present", "isolated_decode",
                "canonical_equality"):
        assert portable[0]["fields"][key] == "unknown"

    kinds_present = {r["kind"] for r in observer["relations"]}
    assert {"action_result", "turn_response", "supersedes", "final_after",
            "helper_for"} <= kinds_present
    final_after = [r for r in observer["relations"] if r["kind"] == "final_after"]
    assert len(final_after) == 1
    final_action = final_after[0]["to_id"]
    final_event = next(e for e in observer["events"] if e["id"] == final_action)
    assert final_event["fields"]["action_kind"] == "test"
    assert "final" in " ".join(final_event["fields"].get("argv", []))
    assert final_after[0]["from_id"] == "turn-r2"

    ids = [e["id"] for e in observer["events"]]
    assert len(set(ids)) == len(ids)
    sequences = [e["sequence"] for e in observer["events"]]
    assert sorted(sequences) == list(range(1, len(sequences) + 1))
    assert all(e["session_id"] == SESSION for e in observer["events"])

    # The observer joins against a native projection of itself.
    native = _native_from_observer(observer)
    document = compare_survival_run(
        observer, native,
        {"complete_root": True, "companions_present": True,
         "isolated_decode": True, "canonical_equality": True},
        configuration_id=CONFIG, repetition=1,
    )
    rows = {row["id"]: row for row in document["metrics"]}
    assert rows["work.actions"]["correct"] == 4
    assert rows["work.actions"]["decoded_eligible"] == 4
    assert rows["work.results"]["correct"] == 4
    assert rows["work.results"]["decoded_eligible"] == 4
    assert rows["causal.action_result"]["correct"] == 4
    assert rows["causal.action_result"]["decoded_eligible"] == 4
    assert rows["revision.final_after_r2"]["state"] == "measured"
    assert rows["work.changed_files"]["state"] == "measured"


def _native_from_observer(observer: dict) -> dict:
    by_id = {event["id"]: event for event in observer["events"]}
    turns = [
        {"id": "native-turn-r1", "turn_id": "turn-r1", "role": "user",
         "revision": "r1", "text": by_id["turn-r1"]["fields"]["text"],
         "sequence": 1},
        {"id": "native-turn-r2", "turn_id": "turn-r2", "role": "user",
         "revision": "r2", "text": by_id["turn-r2"]["fields"]["text"],
         "sequence": 2},
    ]
    responses = []
    for response_id, native_id, order in (("response-r1", "native-r1", 1),
                                         ("response-r2", "native-r2", 2)):
        fields = by_id[response_id]["fields"]
        responses.append({
            "id": native_id, "turn_id": fields["turn_id"], "role": "assistant",
            "status": "completed", "text": fields["text"],
            "canary": fields["canary"], "model_id": fields["model_id"],
            "configuration": fields["configuration"], "sequence": order,
        })
    actions = []
    results = []
    for event in observer["events"]:
        if event["kind"] != "action":
            continue
        fields = event["fields"]
        actions.append({"id": f"native-{event['id']}",
                        **{k: fields[k] for k in
                           ("action_kind", "argv", "cwd", "target", "turn_id",
                            "call_id")
                           if k in fields}})
    for event in observer["events"]:
        if event["kind"] != "result":
            continue
        fields = event["fields"]
        action_native = f"native-{fields['action_id']}"
        record = {"id": f"native-{event['id']}", "action_id": action_native,
                  "status": fields["status"]}
        if "exit_code" in fields:
            record["exit_code"] = fields["exit_code"]
        if "helper_nonce" in fields:
            record["helper_nonce"] = fields["helper_nonce"]
        if "output" in fields:
            record["output"] = fields["output"]
        if "call_id" in fields:
            record["call_id"] = fields["call_id"]
        results.append(record)
    response_native_ids = {"response-r1": "native-r1",
                           "response-r2": "native-r2"}
    relations = []
    for relation in observer["relations"]:
        if relation["kind"] == "action_result":
            relations.append({"id": f"native-{relation['id']}",
                              "kind": "action_result",
                              "from_id": f"native-{relation['from_id']}",
                              "to_id": f"native-{relation['to_id']}"})
        elif relation["kind"] == "turn_response":
            relations.append({"id": f"native-{relation['id']}",
                              "kind": "turn_response",
                              "from_id": relation["from_id"],
                              "to_id": response_native_ids.get(
                                  relation["to_id"],
                                  f"native-{relation['to_id']}")})
        elif relation["kind"] == "final_after":
            # The tightened comparator accepts only an explicit native
            # final_after relation; facts.revisions.final_after_r2 is ignored.
            relations.append({"id": f"native-{relation['id']}",
                              "kind": "final_after",
                              "from_id": relation["from_id"],
                              "to_id": relation["to_id"]})
    file_fields = by_id["file-change-checkout"]["fields"]
    usage = []
    for response_id, turn_id in (("response-r1", "turn-r1"),
                                ("response-r2", "turn-r2")):
        fields = by_id[response_id]["fields"]
        if "usage" not in fields:
            continue
        usage.append({"id": f"native-{fields['usage_id']}", "turn_id": turn_id,
                      "usage": {k: v for k, v in fields["usage"].items()
                                if k.endswith("_tokens")}})
    usage_total = by_id["usage-total"]["fields"]
    return {
        "format": "constructed-native-v1", "supported": True, "turns": turns,
        "responses": responses, "actions": actions, "results": results,
        "file_changes": [{k: file_fields[k] for k in
                          ("path", "before_sha256", "after_sha256")}],
        "relations": relations, "usage": usage,
        "facts": {"reconciliation": {"matches_session_totals": True},
                  "usage": {"session_totals": {
                      "input": usage_total["input_tokens"],
                      "output": usage_total["output_tokens"],
                      "reasoning": usage_total.get("reasoning_tokens", 0),
                      "cache_read": usage_total["cache_read_tokens"],
                      "cache_write": usage_total["cache_write_tokens"],
                  }}},
    }


def test_exact_result_output_mismatch_reduces_correct_results() -> None:
    observer = _build()
    native = _native_from_observer(observer)
    for record in native["results"]:
        if record.get("helper_nonce") == NONCE_FINAL:
            record["output"] = "tampered final output"
            break
    document = compare_survival_run(
        observer, native,
        {"complete_root": True, "companions_present": True,
         "isolated_decode": True, "canonical_equality": True},
        configuration_id=CONFIG, repetition=1,
    )
    rows = {row["id"]: row for row in document["metrics"]}
    assert rows["work.results"]["state"] == "measured"
    assert rows["work.results"]["correct"] == 3
    assert rows["work.results"]["observed_eligible"] == 4


def test_exact_action_identity_mismatch_reduces_correct_actions() -> None:
    observer = _build()
    native = _native_from_observer(observer)
    for record in native["actions"]:
        if record["id"] == "native-action-t2-2":
            assert record["call_id"] == "call-final"
            record["argv"] = ["python3", "bench_check.py", "wrong"]
            break
    document = compare_survival_run(
        observer, native,
        {"complete_root": True, "companions_present": True,
         "isolated_decode": True, "canonical_equality": True},
        configuration_id=CONFIG, repetition=1,
    )
    rows = {row["id"]: row for row in document["metrics"]}
    assert rows["work.actions"]["state"] == "measured"
    assert rows["work.actions"]["correct"] == 3
    assert rows["work.actions"]["observed_eligible"] == 4


def test_malformed_jsonl_is_rejected() -> None:
    stdout = _stdout()
    stdout[1] = '{"type": "tool_use", "sessionID": "' + SESSION + '", '
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn=stdout)


def test_duplicate_json_keys_are_rejected() -> None:
    row = json.dumps({"type": "text", "sessionID": SESSION, "text": "hi"})
    dup = '{"type": "text", "type": "text", "sessionID": "' + SESSION + \
        '", "text": "x ' + R1_CANARY + '"}'
    stdout = {1: row + "\n" + dup,
              2: _stdout()[2]}
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn=stdout)


def test_nonfinite_numbers_are_rejected() -> None:
    row = '{"type": "text", "sessionID": "' + SESSION + '", "text": "x", "n": NaN}'
    stdout = {1: row, 2: _stdout()[2]}
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn=stdout)


def test_session_mismatch_is_rejected() -> None:
    stdout = _stdout()
    other = stdout[2].replace(SESSION, "ses_other")
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn={1: stdout[1], 2: other})


def test_controller_continuation_mismatch_is_rejected() -> None:
    controller = _controller()
    controller["turns"]["2"]["session_id"] = "ses_other"
    with pytest.raises(LiveObserverError):
        _build(controller_state=controller)


def test_missing_response_canary_is_rejected() -> None:
    stdout = _stdout()
    stdout[1] = stdout[1].replace(R1_CANARY, "no canary here")
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn=stdout)


def test_ambiguous_response_canary_is_rejected() -> None:
    stdout = _stdout()
    extra = json.dumps({"type": "text", "sessionID": SESSION,
                        "text": f"again {R1_CANARY}"})
    stdout[1] = stdout[1] + "\n" + extra
    with pytest.raises(LiveObserverError):
        _build(stdout_by_turn=stdout)


def test_helper_mismatch_is_rejected() -> None:
    helper = _helper().replace('"exit_code": 1', '"exit_code": 0', 1)
    with pytest.raises(LiveObserverError):
        _build(helper_ledger_jsonl=helper)


def test_helper_nonce_mismatch_is_rejected() -> None:
    helper = _helper().replace(NONCE_FINAL, "other-nonce")
    with pytest.raises(LiveObserverError):
        _build(helper_ledger_jsonl=helper)


def test_invalid_hashes_are_rejected() -> None:
    with pytest.raises(LiveObserverError):
        _build(before_checkout_sha256="ZZZ")
    with pytest.raises(LiveObserverError):
        _build(before_checkout_sha256=AFTER, after_checkout_sha256=AFTER)


def test_missing_usage_is_allowed_and_omitted() -> None:
    stdout = _stdout()
    lines = stdout[1].splitlines()
    lines = [line for line in lines if "step_finish" not in line]
    stdout[1] = "\n".join(lines)
    observer = _build(stdout_by_turn=stdout)
    response = next(e for e in observer["events"] if e["id"] == "response-r1")
    assert "usage" not in response["fields"]
    assert "usage_id" not in response["fields"]
    total = next(e for e in observer["events"] if e["id"] == "usage-total")
    assert total["fields"]["usage_ids"] == ["usage-r2"]
    assert total["fields"]["input_tokens"] == 34


def test_real_nested_event_shape_normalizes_workspace_and_run_canary_flag() -> None:
    workspace = "/tmp/session-bench-attempt/project"
    nested: dict[int, str] = {}
    for turn, raw in _stdout().items():
        wrapped = []
        for index, line in enumerate(raw.splitlines(), start=1):
            part = json.loads(line)
            payload = part.get("input")
            if isinstance(payload, dict):
                if payload.get("workdir") == "fixture_project":
                    payload["workdir"] = f"{workspace}/fixture_project"
                if payload.get("filePath") == "fixture_project/checkout.py":
                    payload["filePath"] = f"{workspace}/fixture_project/checkout.py"
                command = payload.get("command")
                if isinstance(command, str) and "bench_check.py" in command:
                    payload["command"] = (
                        f"{command} --run-canary {RUN_CANARY}"
                    )
            wrapped.append(
                json.dumps(
                    {
                        "type": str(part.get("type", "event")).replace("_", "-"),
                        "timestamp": 1_000_000 + index,
                        "sessionID": SESSION,
                        "part": part,
                    },
                    ensure_ascii=False,
                )
            )
        nested[turn] = "\n".join(wrapped)

    controller = _controller()
    controller["workspace"] = workspace
    observer = _build(controller_state=controller, stdout_by_turn=nested)
    actions = _events_by_kind(observer, "action")
    inspect = next(item for item in actions if item["fields"]["action_kind"] == "inspect")
    changed = _events_by_kind(observer, "file_change")[0]

    assert inspect["fields"]["cwd"] == "fixture_project"
    assert inspect["fields"]["argv"][-2:] == ["--run-canary", RUN_CANARY]
    assert changed["fields"]["path"] == "fixture_project/checkout.py"
    assert _events_by_kind(observer, "assistant_response")[0]["fields"][
        "configuration"
    ] == MODEL


def test_inputs_are_not_mutated() -> None:
    workload, controller, stdout = _workload(), _controller(), _stdout()
    helper = _helper()
    before = copy.deepcopy((workload, controller, stdout, helper))
    _build(workload=workload, controller_state=controller,
           stdout_by_turn=stdout, helper_ledger_jsonl=helper,
           before_checkout_sha256=BEFORE, after_checkout_sha256=AFTER)
    assert (workload, controller, stdout, helper) == before


def test_legacy_controller_turn_shape_is_rejected() -> None:
    controller = {"model": MODEL, "configuration": CONFIG,
                  "turns": {1: {"session_id": SESSION},
                            2: {"session_id": SESSION}}}
    with pytest.raises(LiveObserverError):
        _build(controller_state=controller)
