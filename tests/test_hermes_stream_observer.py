"""Hermes stream observer: tool events from the ``stream-json`` stdout, never from native rows."""
import hashlib
import json

import pytest

from session_bench.hermes_stream_observer import (
    HermesStreamError, build_hermes_stream_observer, edits_target, paired_calls, parse_stream, stream_summary, terminal_result,
)
from session_bench.hermes_survival_capture import planned_argv, turn_arguments

SID = "20261007_101500_abc123"
RUN = "SB_SURVIVAL_V1_RUN_hermes-test"
R1, R2 = "SB_SURVIVAL_V1_RESPONSE_R1_cafe", "SB_SURVIVAL_V1_RESPONSE_R2_fix"
HELPER = "python3 bench_check.py {} --run-canary " + RUN
LINE = {phase: f'SB_SURVIVAL_V1_HELPER_{phase.upper()}_{phase}-fixture-0001 {{"phase":"{phase}"}}' for phase in ("inspect", "baseline", "final")}
EXIT = {"inspect": 0, "baseline": 1, "final": 0}
WORKLOAD = {"run_id": "hermes-test", "run_canary": RUN,
            "turns": [{"id": "turn-r1", "text": "Requirement R1 " + RUN, "response_canary": R1},
                      {"id": "turn-r2", "text": "Correction R2 " + RUN, "response_canary": R2}]}
LAUNCHES = [{"model": "gpt-5.5", "provider": "openai-codex"}] * 2
LEDGER = "".join(json.dumps({"phase": phase, "helper_nonce": f"{phase}-fixture-0001", "argv": ["python3", "bench_check.py", phase],
                             "exit_code": EXIT[phase], "output": LINE[phase]}) + "\n" for phase in LINE).encode()


def stream(*events, text, session=SID, model="gpt-5.5", exit_code=0, error=None):
    """A ``stream-json`` stdout as ``hermes_cli/stream_json.py`` writes it: init, events, result."""
    rows = [{"type": "system", "subtype": "init", "model": model, "session_id": session}, *events,
            {"type": "text", "text": text},
            {"type": "result", "session_id": session, "exit_code": exit_code, "text": text,
             "tokens": {"input": 10, "output": 5, "total": 15, "cache_read": 0, "cache_write": 0}, "duration_ms": 9,
             **({"error": error} if error else {})}]
    return "".join(json.dumps({**row, "timestamp": 1791400000000 + index}, ensure_ascii=False) + "\n" for index, row in enumerate(rows)).encode()


def terminal(command, output, exit_code=0, call_id=None):
    identity = {"tool_call_id": call_id} if call_id else {}
    return [{"type": "tool_use", "name": "terminal", **identity, "input": {"command": command, "workdir": "/tmp/w/fixture_project", "timeout": 120}},
            {"type": "tool_result", "name": "terminal", **identity, "output": json.dumps({"output": output, "exit_code": exit_code, "error": None}),
             "duration_ms": 3, "is_error": exit_code != 0}]


def file_tool(name, arguments, output='{"bytes_written": 90}', is_error=False):
    return [{"type": "tool_use", "name": name, "input": arguments},
            {"type": "tool_result", "name": name, "output": output, "duration_ms": 1, "is_error": is_error}]


def observe(first, second):
    return build_hermes_stream_observer(workload=WORKLOAD, stdout_by_turn={1: first, 2: second}, helper_document=LEDGER,
                                        before_sha256="a" * 64, after_sha256="b" * 64, launches=LAUNCHES)


def normal_run():
    first = stream(*terminal(HELPER.format("inspect"), LINE["inspect"]), *terminal(HELPER.format("baseline"), LINE["baseline"], 1),
                   text="Baseline fails. " + R1)
    second = stream(*file_tool("read_file", {"path": "fixture_project/checkout.py"}, output="def checkout(items): ..."),
                    *file_tool("patch", {"path": "/tmp/w/fixture_project/checkout.py", "old_string": "a", "new_string": "b"}),
                    *terminal(HELPER.format("final"), LINE["final"]), text="Fixed. " + R2)
    return first, second


def by_kind(observer, kind, role="primary_scored"):
    return [row for row in observer["events"] if row["kind"] == kind and row["population_role"] == role]


def test_normal_run_gives_four_observed_actions_with_results_and_relations():
    observer = observe(*normal_run())
    assert observer["independent"] is True and "no native input" in observer["method"]
    actions = by_kind(observer, "action")
    assert [(row["id"], row["fields"]["action_kind"], row["fields"]["turn_id"]) for row in actions] == [
        ("r1-call-1", "inspect", "turn-r1"), ("r1-call-2", "test", "turn-r1"), ("r2-call-2", "edit", "turn-r2"), ("r2-call-3", "test", "turn-r2")]
    assert actions[0]["fields"]["argv"] == ["python3", "bench_check.py", "inspect", "--run-canary", RUN] and actions[0]["source"] == "harness_stdout"
    assert actions[2]["fields"] == {"name": "file_edit", "tool": "patch", "target": "fixture_project/checkout.py", "action_kind": "edit", "turn_id": "turn-r2"}
    results = {row["fields"]["action_id"]: row["fields"] for row in by_kind(observer, "result")}
    # The exit status comes from the terminal result of the stream: baseline failed with exit code 1.
    assert [(results[row["id"]].get("exit_code"), results[row["id"]]["status"]) for row in actions] == [
        (0, "success"), (1, "failure"), (None, "success"), (0, "success")]
    assert results["r1-call-2"]["output"] == LINE["baseline"] and results["r1-call-2"]["helper_nonce"] == "baseline-fixture-0001"
    links = [(row["from_id"], row["to_id"]) for row in observer["relations"] if row["kind"] == "action_result"]
    assert links == [(row["id"], row["id"] + ":result") for row in actions]
    assert [(row["kind"], row["from_id"], row["to_id"]) for row in observer["relations"] if row["kind"] in ("final_after", "supersedes", "turn_response")] == [
        ("turn_response", "turn-r1", "response-r1"), ("turn_response", "turn-r2", "response-r2"),
        ("final_after", "turn-r2", "r2-call-3"), ("supersedes", "turn-r1", "turn-r2")]
    # The read of the file is observed and unscored. The change joins the observed edit.
    assert [(row["id"], row["fields"]["name"], row["fields"]["target"]) for row in by_kind(observer, "action", "unscored")] == [
        ("r2-call-1", "read_file", "fixture_project/checkout.py")]
    change = by_kind(observer, "file_change")[0]["fields"]
    assert change == {"path": "fixture_project/checkout.py", "before_sha256": "a" * 64, "after_sha256": "b" * 64, "turn_id": "turn-r2", "action_id": "r2-call-2"}
    responses = by_kind(observer, "assistant_response")
    assert [row["fields"]["text"] for row in responses] == ["Baseline fails. " + R1, "Fixed. " + R2]
    assert responses[0]["fields"]["model_id"] == "gpt-5.5" and responses[0]["fields"]["configuration"] == {"provider": "openai-codex"}
    assert {row["session_id"] for row in observer["events"]} == {SID} and all(type(row.get("observed_at")) is int for row in actions)
    assert [row["fields"]["phase"] for row in observer["events"] if row["kind"] == "helper"] == ["inspect", "baseline", "final"]


def test_compound_shell_call_is_observed_as_one_action_per_helper_segment():
    command = HELPER.format("inspect") + " && " + HELPER.format("baseline")
    first = stream(*terminal(command, LINE["inspect"] + "\n" + LINE["baseline"], 1, call_id="call_a"), text="Baseline fails. " + R1)
    observer = observe(first, normal_run()[1])
    actions = by_kind(observer, "action")[:2]
    assert [(row["id"], row["fields"]["segment_index"], row["fields"]["command"]) for row in actions] == [
        ("r1-call-1:segment-0", 0, HELPER.format("inspect")), ("r1-call-1:segment-1", 1, HELPER.format("baseline"))]
    # When the stream carries a call id, it is kept for the join with the native call.
    assert {row["fields"]["stream_call_id"] for row in actions} == {"call_a"}
    results = [row["fields"] for row in by_kind(observer, "result")[:2]]
    # Per segment the helper ledger gives the exit code and the line; the line must be in the stream output of the call.
    assert [(row["exit_code"], row["output"]) for row in results] == [(0, LINE["inspect"]), (1, LINE["baseline"])]
    assert all(row["source"] == "helper_ledger" for row in by_kind(observer, "result")[:2])
    missing = stream(*terminal(command, LINE["inspect"], 1), text="Baseline fails. " + R1)
    with pytest.raises(HermesStreamError, match="ledger line is not in the stream output"):
        observe(missing, normal_run()[1])


def test_edit_by_the_file_tool_and_the_rules_for_several_or_failed_edits():
    first, _ = normal_run()
    write = file_tool("write_file", {"path": "checkout.py", "content": "def checkout(items):\n    return 1\n"})
    failed = file_tool("patch", {"path": "checkout.py", "old_string": "x", "new_string": "y"}, output='{"error": "no match"}', is_error=True)
    second = stream(*failed, *write, *file_tool("patch", {"path": "fixture_project/checkout.py", "old_string": "1", "new_string": "2"}),
                    *terminal(HELPER.format("final"), LINE["final"]), text="Fixed. " + R2)
    observer = observe(first, second)
    edit = next(row for row in by_kind(observer, "action") if row["fields"]["action_kind"] == "edit")
    # The last successful edit of the workload file is the scored one; the failed and the earlier edit are unscored.
    assert edit["id"] == "r2-call-3" and edit["fields"]["tool"] == "patch"
    assert [(row["id"], row["fields"]["name"]) for row in by_kind(observer, "action", "unscored")] == [("r2-call-1", "patch"), ("r2-call-2", "write_file")]
    assert edits_target({"path": "/tmp/w/fixture_project/checkout.py"}) and edits_target({"path": "checkout.py"})
    assert not edits_target({"path": "fixture_project/snapshots/checkout.py"}) and not edits_target({"path": "bench_check.py"}) and not edits_target({})
    # An edit by a script inside a shell call is not an observed edit action: the population is incomplete.
    script = "python3 - <<'PY'\nopen('checkout.py','w').write('x')\nPY\n" + HELPER.format("final")
    with pytest.raises(HermesStreamError, match="no observed edit"):
        observe(first, stream(*terminal(script, LINE["final"]), text="Fixed. " + R2))
    with pytest.raises(HermesStreamError, match="no observed edit"):
        observe(first, stream(*failed, *terminal(HELPER.format("final"), LINE["final"]), text="Fixed. " + R2))


def test_helper_result_must_agree_with_the_helper_ledger():
    first, second = normal_run()
    wrong_code = stream(*terminal(HELPER.format("inspect"), LINE["inspect"]), *terminal(HELPER.format("baseline"), LINE["baseline"], 0),
                        text="Baseline fails. " + R1)
    with pytest.raises(HermesStreamError, match="differs from the helper ledger"):
        observe(wrong_code, second)
    other_line = stream(*terminal(HELPER.format("inspect"), "something else"), *terminal(HELPER.format("baseline"), LINE["baseline"], 1),
                        text="Baseline fails. " + R1)
    with pytest.raises(HermesStreamError, match="differs from the helper ledger"):
        observe(other_line, second)
    twice = stream(*terminal(HELPER.format("inspect"), LINE["inspect"]), *terminal(HELPER.format("inspect"), LINE["inspect"]),
                   *terminal(HELPER.format("baseline"), LINE["baseline"], 1), text="Baseline fails. " + R1)
    with pytest.raises(HermesStreamError, match="differs from the helper ledger"):
        observe(twice, second)
    with pytest.raises(HermesStreamError, match="helper population incomplete"):
        build_hermes_stream_observer(workload=WORKLOAD, stdout_by_turn={1: first, 2: second}, helper_document=LEDGER.splitlines()[0] + b"\n",
                                     before_sha256="a" * 64, after_sha256="b" * 64, launches=LAUNCHES)


def test_missing_canary_failed_turn_other_session_or_model_fail_closed():
    first, second = normal_run()
    with pytest.raises(HermesStreamError, match="lacks the exact canary"):
        observe(first, stream(*json.loads(b"[" + b",".join(second.splitlines()[1:-2]) + b"]"), text="Fixed, no marker."))
    with pytest.raises(HermesStreamError, match="lacks the exact canary"):
        observe(stream(text=R1 + " and more text after it"), second)
    with pytest.raises(HermesStreamError, match="failed turn"):
        observe(first, stream(text="Fixed. " + R2, exit_code=1, error="Interrupted"))
    with pytest.raises(HermesStreamError, match="different sessions"):
        observe(first, second.replace(SID.encode(), b"20261007_111111_fff999"))
    with pytest.raises(HermesStreamError, match="another model"):
        observe(first, second.replace(b'"model": "gpt-5.5"', b'"model": "other"'))


@pytest.mark.parametrize("damage, reason", [
    (lambda raw: raw + b"this line is not json\n", "is not JSON"),
    (lambda raw: raw + b'["a list"]\n', "not a stream-json event"),
    (lambda raw: raw.replace(b'"type": "text"', b'"type": "surprise"'), "not a stream-json event"),
    (lambda raw: raw.replace(b'"timestamp": 1791400000001', b'"timestamp": "now"'), "not a stream-json event"),
    (lambda raw: b"\n".join(raw.splitlines()[:-1]) + b"\n", "close with one result event"),
    (lambda raw: b"\n".join(raw.splitlines()[1:]) + b"\n", "open with one init event"),
    (lambda raw: raw + raw.splitlines()[-1] + b"\n", "close with one result event"),
    (lambda raw: raw[:-3] + b"\xff\n", "not UTF-8"),
])
def test_malformed_stream_line_fails_closed(damage, reason):
    first, second = normal_run()
    with pytest.raises(HermesStreamError, match=reason):
        observe(damage(first), second)


def test_calls_pair_by_order_without_ids_and_by_id_with_ids_and_a_cut_result_is_refused():
    use = lambda name, call=None: {"type": "tool_use", "name": name, "input": {}, "timestamp": 1, **({"tool_call_id": call} if call else {})}
    done = lambda name, call=None, output="x": {"type": "tool_result", "name": name, "output": output, "is_error": False, "duration_ms": 1,
                                                 "timestamp": 2, **({"tool_call_id": call} if call else {})}
    assert [(row["use"]["name"], row["result"]["name"]) for row in paired_calls([use("a"), done("a"), use("b"), done("b")])] == [("a", "a"), ("b", "b")]
    # Parallel calls pair only by id.
    parallel = paired_calls([use("a", "1"), use("a", "2"), done("a", "2", "second"), done("a", "1", "first")])
    assert [(row["call_id"], row["result"]["output"]) for row in parallel] == [("1", "first"), ("2", "second")]
    for rows in ([use("a"), use("a"), done("a"), done("a")], [use("a"), done("b")], [use("a")], [done("a")],
                 [use("a", "1"), done("a")], [{"type": "tool_use", "name": "a", "timestamp": 1}]):
        with pytest.raises(HermesStreamError):
            paired_calls(rows)
    # Hermes cuts a tool result after 5000 characters. Then the exit code is not observed and the observer refuses.
    cut = json.dumps({"output": "y" * 6000, "exit_code": 0, "error": None})[:5000] + "..."
    with pytest.raises(HermesStreamError, match="cut at the output limit"):
        terminal_result({"output": cut})
    with pytest.raises(HermesStreamError, match="without output or exit code"):
        terminal_result({"output": json.dumps({"output": "y"})})
    assert terminal_result({"output": json.dumps({"output": "y", "exit_code": 3, "error": None})}) == ("y", 3)
    summary = stream_summary(normal_run()[0])
    assert summary["session_id"] == SID and summary["model"] == "gpt-5.5" and summary["text"].endswith(R1) and summary["tokens"]["input"] == 10
    assert [row["type"] for row in parse_stream(normal_run()[0])] == ["system", "tool_use", "tool_result", "tool_use", "tool_result", "text", "result"]


def test_planned_argv_keeps_the_file_tools_and_the_stream_format():
    assert turn_arguments("/w", "prompt") == ["chat", "--ignore-user-config", "--ignore-rules", "--no-restore-cwd", "--in", "/w",
                                               "--provider", "openai-codex", "--model", "gpt-5.5", "--toolsets", "terminal,file", "--yolo",
                                               "--format", "stream-json", "-q", "prompt"]
    first, second = planned_argv(python="/py", source_root="/src", workspace="/w", prompts=["one", "two"], session_id="S")
    assert first[:4] == ["/py", "-I", "-B", "-c"] and first[5] == "chat" and "/src" not in first and first[-2:] == ["-q", "one"] and "--resume" not in first
    assert second[second.index("--resume") + 1] == "S" and second[-2:] == ["-q", "two"]
    assert "-z" not in first and "--usage-file" not in first and hashlib.sha256(b"").hexdigest()


def test_entry_code_gives_the_same_argv_when_hermes_bootstrap_runs_it_again(monkeypatch):
    """Hermes can re-execute the process: it sets sys.argv to the first argv and runs the ``-c`` code again."""
    import sys
    import types
    from session_bench.hermes_survival_capture import LEGACY_ENTRYPOINT_CODE, entrypoint_code

    seen = []
    monkeypatch.setitem(sys.modules, "runpy", types.SimpleNamespace(run_module=lambda name, run_name: seen.append((name, list(sys.argv)))))
    monkeypatch.setattr(sys, "path", list(sys.path))
    argv = planned_argv(python="/py", source_root="/src", workspace="/w", prompts=["one", "two"])[0]
    code, hermes_arguments = argv[4], argv[5:]
    assert code == entrypoint_code("/src") and "sys.argv.pop" not in code and "'/src'" in code
    for _ in range(2):      # the first process, then the relaunched one
        monkeypatch.setattr(sys, "argv", ["-c", *hermes_arguments])
        exec(code, {})
    assert seen == [("hermes_cli.main", ["-c", *hermes_arguments])] * 2 and seen[1][1][1] == "chat" and sys.path[0] == "/src"
    # The old entry code popped one more argument in the relaunched process: with ``chat`` first it lost the
    # subcommand, and ``stream-json`` then stood where the top-level parser reads a command (attempt 07).
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["-c", "/src", *hermes_arguments])
    exec(LEGACY_ENTRYPOINT_CODE, {})
    monkeypatch.setattr(sys, "argv", list(seen[0][1]))
    exec(LEGACY_ENTRYPOINT_CODE, {})
    assert seen[0][1][1] == "chat" and seen[1][1][1] == "--ignore-user-config" and "chat" not in seen[1][1]
    positional = [item for index, item in enumerate(seen[1][1][1:], 1) if not item.startswith("-")
                  and seen[1][1][index - 1] not in ("--in", "--provider", "--model", "--toolsets", "-q")]
    assert positional == ["stream-json"]
