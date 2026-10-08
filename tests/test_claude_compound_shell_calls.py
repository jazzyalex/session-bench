"""One split rule for compound shell calls in the Claude decoder and projection.

A native shell call that holds several command segments is the native record
of each segment. No decoded or projected fact may hold text that is not in the
native bytes.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from session_bench.adapters.claude_code_decoder import (
    decode_claude_code_bundle, helper_segment_results, split_shell_segments,
)
from session_bench.claude_live import ClaudeLiveError, native_facts_from_claude_session
from session_bench.live_metric_comparator import compare_survival_run

CANARY = "SB_SURVIVAL_V1_RUN_compound-test"
WORKSPACE = "/ws"
BEFORE = "def checkout(items):\n    subtotal = sum(price for price, quantity in items)\n    return subtotal + 5\n"
AFTER = "def checkout(items):\n    subtotal = sum(price * quantity for price, quantity in items)\n    return subtotal\n"
BEFORE_SHA = hashlib.sha256(BEFORE.encode()).hexdigest()
AFTER_SHA = hashlib.sha256(AFTER.encode()).hexdigest()
INSPECT_LINE = "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-1 " + json.dumps(
    {"checkout_sha256": BEFORE_SHA, "checkout_source": BEFORE, "phase": "inspect"}, sort_keys=True, separators=(",", ":"))
BASELINE_LINE = 'SB_SURVIVAL_V1_HELPER_BASELINE_baseline-1 {"phase":"baseline","tests":[]}'
FINAL_LINE = 'SB_SURVIVAL_V1_HELPER_FINAL_final-1 {"phase":"final","tests":[]}'
COMMAND_1 = (f"cd {WORKSPACE}/fixture_project && python3 bench_check.py inspect --run-canary {CANARY}; echo \"exit=$?\"; "
             f"python3 bench_check.py baseline --run-canary {CANARY}; echo \"exit=$?\"")
COMMAND_2 = f"cat > checkout.py <<'EOF'\n{AFTER}EOF\npython3 bench_check.py final --run-canary {CANARY}; echo \"exit=$?\""
DIFF = {"files": [{"filePath": f"{WORKSPACE}/fixture_project/checkout.py", "hunks": [{
    "oldStart": 1, "oldLines": 3, "newStart": 1, "newLines": 3,
    "lines": [" def checkout(items):", "-    subtotal = sum(price for price, quantity in items)", "-    return subtotal + 5",
              "+    subtotal = sum(price * quantity for price, quantity in items)", "+    return subtotal"]}]}]}


def _rows(*, command_1=COMMAND_1, output_1=None, command_2=COMMAND_2, output_2=None, diff=DIFF, error_2=False):
    output_1 = f"{INSPECT_LINE}\nexit=0\n{BASELINE_LINE}\nexit=1" if output_1 is None else output_1
    output_2 = f"{FINAL_LINE}\nexit=0" if output_2 is None else output_2
    common = {"sessionId": "session-1"}
    return [
        {**common, "type": "user", "uuid": "u1", "timestamp": "2026-09-15T00:00:01Z",
         "message": {"role": "user", "content": "Requirement R1. Inspect and run the baseline."}},
        {**common, "type": "assistant", "uuid": "a1", "timestamp": "2026-09-15T00:00:02Z", "cwd": WORKSPACE,
         "message": {"id": "m1", "role": "assistant", "model": "vendor-model-5",
                     "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": command_1}}]}},
        {**common, "type": "user", "uuid": "r1", "timestamp": "2026-09-15T00:00:03Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": output_1, "is_error": False}]},
         "toolUseResult": {"stdout": output_1}},
        {**common, "type": "assistant", "uuid": "a2", "timestamp": "2026-09-15T00:00:04Z",
         "message": {"id": "m2", "role": "assistant", "model": "vendor-model-5",
                     "content": [{"type": "text", "text": "Baseline fails.\n\nSB_SURVIVAL_V1_RESPONSE_R1_x"}]}},
        {**common, "type": "user", "uuid": "u2", "timestamp": "2026-09-15T00:00:05Z",
         "message": {"role": "user", "content": "Correction R2 supersedes the delivery rule."}},
        {**common, "type": "assistant", "uuid": "a3", "timestamp": "2026-09-15T00:00:06Z", "cwd": f"{WORKSPACE}/fixture_project",
         "message": {"id": "m3", "role": "assistant", "model": "vendor-model-5",
                     "content": [{"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": command_2}}]}},
        {**common, "type": "user", "uuid": "r2", "timestamp": "2026-09-15T00:00:07Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": output_2, "is_error": error_2}]},
         "toolUseResult": {"stdout": output_2, **({"bashEditDiff": diff} if diff is not None else {})}},
        {**common, "type": "assistant", "uuid": "a4", "timestamp": "2026-09-15T00:00:08Z",
         "message": {"id": "m4", "role": "assistant", "model": "vendor-model-5",
                     "content": [{"type": "text", "text": "All tests pass.\n\nSB_SURVIVAL_V1_RESPONSE_R2_x"}]}},
    ]


def _session(**options) -> bytes:
    return "\n".join(json.dumps(row) for row in _rows(**options)).encode("utf-8")


def _facts(**options):
    return native_facts_from_claude_session(_session(**options), run_canary=CANARY, workspace=WORKSPACE, before_sha256="", after_sha256="")


def _package(tmp_path, session: bytes):
    package = tmp_path / "package"
    package.mkdir(parents=True)
    (package / "session.jsonl").write_bytes(session)
    (package / "decode.json").write_text(json.dumps({"format": "claude-code-jsonl-v1", "artifacts": [{
        "id": "session", "path": "session.jsonl", "sha256": hashlib.sha256(session).hexdigest(), "size_bytes": len(session), "depends_on": []}]}))
    return package


def _strings(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


# --- the segment splitter -------------------------------------------------

def test_splitter_keeps_quotes_and_heredoc_bodies_inside_their_segment():
    segments = split_shell_segments(COMMAND_2)

    assert [segment["text"] for segment in segments] == [
        "cat > checkout.py <<'EOF'", f"python3 bench_check.py final --run-canary {CANARY}", 'echo "exit=$?"']
    assert segments[0]["heredocs"] == [{"delimiter": "EOF", "quoted": True, "strip_tabs": False, "body": AFTER}]
    assert [segment["text"] for segment in split_shell_segments("echo 'a; b' && echo \"c || d\"; echo e")] == [
        "echo 'a; b'", 'echo "c || d"', "echo e"]


@pytest.mark.parametrize("command", [
    "(cd x && python3 bench_check.py final)", "echo $(date); echo x", "echo `date`; echo x", "sleep 1 & echo x",
    "if true; then echo x; fi", "echo 'unterminated; echo x", "cat <<EOF\nno end marker", "for f in a b; do echo $f; done",
])
def test_splitter_refuses_commands_it_cannot_segment_safely(command):
    assert split_shell_segments(command) is None


def test_helper_result_takes_its_own_output_line_and_the_exit_echo_that_follows():
    segments = split_shell_segments(COMMAND_1)

    assert helper_segment_results(segments, f"{INSPECT_LINE}\nexit=0\n{BASELINE_LINE}\nexit=1", False) == {
        1: {"output": INSPECT_LINE, "exit_code": 0}, 3: {"output": BASELINE_LINE, "exit_code": 1}}


def test_helper_result_without_an_exit_echo_has_an_exit_code_only_when_it_is_the_last_segment():
    command = f"python3 bench_check.py inspect --run-canary {CANARY}; python3 bench_check.py baseline --run-canary {CANARY}"
    segments = split_shell_segments(command)

    assert helper_segment_results(segments, f"{INSPECT_LINE}\n{BASELINE_LINE}", True) == {
        0: {"output": INSPECT_LINE, "exit_code": None}, 1: {"output": BASELINE_LINE, "exit_code": 1}}
    # An exit echo exists in the command, but its line does not follow the helper line: never guess.
    echoed = split_shell_segments(command.replace("; python3", '; echo "exit=$?"; python3') + "; echo done")
    assert helper_segment_results(echoed, f"{INSPECT_LINE}\nnoise\nexit=0\n{BASELINE_LINE}\ndone", False) == {
        0: {"output": INSPECT_LINE, "exit_code": None}, 2: {"output": BASELINE_LINE, "exit_code": None}}


def test_an_and_chain_proves_exit_zero_only_for_a_helper_directly_before_a_helper_that_ran():
    INSPECT, BASELINE = (f"python3 bench_check.py {phase} --run-canary {CANARY}" for phase in ("inspect", "baseline"))
    chained = split_shell_segments(f"{INSPECT} && {BASELINE}")
    assert helper_segment_results(chained, f"{INSPECT_LINE}\n{BASELINE_LINE}", True) == {
        0: {"output": INSPECT_LINE, "exit_code": 0}, 1: {"output": BASELINE_LINE, "exit_code": 1}}
    assert helper_segment_results(chained, INSPECT_LINE, True) == {0: {"output": INSPECT_LINE, "exit_code": None}}
    indirect = split_shell_segments(f"{INSPECT} && echo ok; {BASELINE}")
    assert helper_segment_results(indirect, f"{INSPECT_LINE}\nok\n{BASELINE_LINE}", False)[0]["exit_code"] is None
    unbroken = split_shell_segments(f"{INSPECT} && echo ok && {BASELINE}")
    assert helper_segment_results(unbroken, f"{INSPECT_LINE}\nok\n{BASELINE_LINE}", False)[0]["exit_code"] == 0


def test_helper_result_is_absent_when_the_output_lines_do_not_pair_with_the_segments():
    segments = split_shell_segments(COMMAND_1)

    assert helper_segment_results(segments, f"{INSPECT_LINE}\nexit=0\n{INSPECT_LINE}\nexit=0\nexit=1", False) == {}


# --- the native projection ------------------------------------------------

def test_compound_call_projects_one_action_and_one_result_per_helper_segment():
    facts = _facts()
    actions = {action["id"]: action for action in facts["actions"]}

    assert list(actions) == ["toolu_1:segment-1", "toolu_1:segment-3", "toolu_2:segment-0", "toolu_2:segment-1"]
    inspect, baseline = actions["toolu_1:segment-1"], actions["toolu_1:segment-3"]
    assert inspect["argv"] == ["python3", "bench_check.py", "inspect"] and baseline["argv"] == ["python3", "bench_check.py", "baseline"]
    assert inspect["parent_call_id"] == baseline["parent_call_id"] == "toolu_1"
    assert inspect["turn_id"] == baseline["turn_id"] == "u1" and inspect["tool_name"] == "Bash"
    results = {result["id"]: result for result in facts["results"]}
    assert list(results) == ["toolu_1:segment-1:result", "toolu_1:segment-3:result", "toolu_2:segment-1:result"]
    assert results["toolu_1:segment-1:result"] | {"sequence": 0} == {
        "id": "toolu_1:segment-1:result", "native_result_id": "toolu_1:segment-1:result", "action_id": "toolu_1:segment-1",
        "call_id": "toolu_1:segment-1", "parent_call_id": "toolu_1", "turn_id": "u1", "output": INSPECT_LINE, "exit_code": 0,
        "status": "success", "helper_nonce": "inspect-1", "output_prefix": "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-1", "sequence": 0}
    assert (results["toolu_1:segment-3:result"]["exit_code"], results["toolu_1:segment-3:result"]["status"]) == (1, "failure")
    assert results["toolu_1:segment-3:result"]["output"] == BASELINE_LINE
    relations = [(row["from_id"], row["to_id"]) for row in facts["relations"] if row["kind"] == "action_result"]
    assert relations == [("toolu_1:segment-1", "toolu_1:segment-1:result"), ("toolu_1:segment-3", "toolu_1:segment-3:result"),
                         ("toolu_2:segment-1", "toolu_2:segment-1:result")]


def test_shell_write_segment_is_an_edit_action_without_a_result():
    facts = _facts()
    edit = next(action for action in facts["actions"] if action["id"] == "toolu_2:segment-0")

    assert edit["action_kind"] == "edit" and edit["tool_name"] == "Bash" and edit["target"] == "fixture_project/checkout.py"
    assert edit["turn_id"] == "u2" and "argv" not in edit
    assert not any(result["action_id"] == edit["id"] for result in facts["results"])
    assert not any(edit["id"] in (row["from_id"], row["to_id"]) for row in facts["relations"] if row["kind"] == "action_result")
    final_after = next(row for row in facts["relations"] if row["kind"] == "final_after")
    assert final_after["to_id"] == "toolu_2:segment-1"


def test_no_projected_or_decoded_text_is_absent_from_the_native_bytes(tmp_path):
    session = _session()
    native_text = "".join(_strings([json.loads(line) for line in session.decode().splitlines()]))
    allowed = {"fixture_project", "fixture_project/checkout.py", "python3", "bench_check.py", "inspect", "baseline", "final",
               "success", "failure", "edit", "r1", "r2", "user", "assistant", "completed", "final_answer", "action_result",
               "turn_response", "supersedes", "final_after", "claude-code-jsonl-v1", "native_preimage_and_shell_write",
               "action", "result", "response", "submitted_turn", "session.jsonl", "session", CANARY, BEFORE_SHA, AFTER_SHA}
    facts = _facts()
    decoded = decode_claude_code_bundle(_package(tmp_path, session))
    for value in list(_strings(facts)) + list(_strings(decoded["events"])):
        assert "compound edit" not in value
        # Identifiers are built from native ids; everything else is native text or a fixed vocabulary word.
        derived = value.startswith(("toolu_", "relation-", "file-change-", "usage-")) or re.fullmatch(r"[uar]\d-block-\d+(-segment-\d+)?", value)
        assert value in allowed or value in native_text or derived or len(value) == 64, value
    outputs = [result["output"] for result in facts["results"]] + [event["text"] for event in decoded["events"]]
    assert all(output in native_text for output in outputs)


def test_shell_write_changed_file_hashes_come_from_the_native_pre_image_and_the_native_edit():
    change = _facts()["file_changes"][0]

    assert change == {"id": "file-change-toolu_2:segment-0", "path": "fixture_project/checkout.py", "action_id": "toolu_2:segment-0",
                      "before_sha256": BEFORE_SHA, "after_sha256": AFTER_SHA, "hash_source": "native_preimage_and_shell_write"}


@pytest.mark.parametrize("options", [
    {"diff": None},  # the native result does not confirm the write
    {"diff": {"files": []}},
    {"diff": {"files": [{**DIFF["files"][0], "hunks": [{**DIFF["files"][0]["hunks"][0],
                                                       "lines": [" def other(items):"] + DIFF["files"][0]["hunks"][0]["lines"][1:]}]}]}},
    {"output_1": f"{BASELINE_LINE}\nexit=1", "command_1": f"python3 bench_check.py baseline --run-canary {CANARY}; echo \"exit=$?\""},  # no inspect pre-image
    {"command_2": COMMAND_2.replace("<<'EOF'", "<<EOF")},  # an unquoted heredoc may expand: its body is not the file
    {"command_2": COMMAND_2.replace("cat > checkout.py", "cat >> checkout.py")},
])
def test_shell_write_without_both_native_images_yields_no_hashes(options):
    change = _facts(**options)["file_changes"][0]

    assert change["before_sha256"] == change["after_sha256"] == "" and "hash_source" not in change


def test_wrong_run_canary_in_one_segment_is_refused():
    with pytest.raises(ClaudeLiveError, match="wrong run canary"):
        _facts(command_1=COMMAND_1.replace(f"baseline --run-canary {CANARY}", "baseline --run-canary SB_SURVIVAL_V1_RUN_other"))


def test_single_command_call_projects_as_before():
    command = f'cd fixture_project && python3 bench_check.py inspect --run-canary {CANARY}; echo "exit=$?"'
    output = f"{INSPECT_LINE}\nexit=0"
    facts = _facts(command_1=command, output_1=output)

    assert facts["actions"][0] == {"id": "toolu_1", "call_id": "toolu_1", "tool_name": "Bash", "name": "Bash", "input": {"command": command},
                                  "turn_id": "u1", "sequence": 2, "cwd": "fixture_project", "argv": ["python3", "bench_check.py", "inspect"],
                                  "target": "fixture_project/checkout.py"}
    assert facts["results"][0] == {"id": "toolu_1:result", "native_result_id": "toolu_1:result", "action_id": "toolu_1", "call_id": "toolu_1",
                                  "turn_id": "u1", "status": "success", "exit_code": 0, "output": output, "sequence": 3,
                                  "helper_nonce": "inspect-1", "output_prefix": "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-1"}


def test_command_that_cannot_be_segmented_stays_one_call():
    command = f"(python3 bench_check.py inspect --run-canary {CANARY}; python3 bench_check.py baseline --run-canary {CANARY})"
    facts = _facts(command_1=command)

    assert [action["id"] for action in facts["actions"]][:1] == ["toolu_1"]
    assert [result["id"] for result in facts["results"]][:1] == ["toolu_1:result"]


# --- the comparator -------------------------------------------------------

def _observer():
    def event(identifier, kind, sequence, fields):
        return {"id": identifier, "kind": kind, "sequence": sequence, "session_id": "observer-session",
                "population_role": "primary_scored", "metric_ids": [], "fields": fields}

    def helper(phase, turn):
        return {"action_kind": "inspect" if phase == "inspect" else "test", "cwd": "fixture_project", "name": "Bash",
                "argv": ["python3", "bench_check.py", phase, "--run-canary", CANARY], "target": "fixture_project/checkout.py", "turn_id": turn}

    events = [
        event("turn-r1", "user_turn", 1, {"role": "user", "revision": "r1", "text": "Requirement R1. Inspect and run the baseline."}),
        event("action-t1-1", "action", 2, helper("inspect", "turn-r1")),
        event("action-t1-2", "action", 3, helper("baseline", "turn-r1")),
        event("response-r1", "assistant_response", 4, {"role": "assistant", "status": "completed", "turn_id": "turn-r1",
                                                       "canary": "SB_SURVIVAL_V1_RESPONSE_R1_x", "text": "SB_SURVIVAL_V1_RESPONSE_R1_x"}),
        event("turn-r2", "user_turn", 5, {"role": "user", "revision": "r2", "text": "Correction R2 supersedes the delivery rule."}),
        event("action-t2-1", "action", 6, {"action_kind": "edit", "cwd": "fixture_project", "name": "Edit",
                                           "input": {"file_path": "fixture_project/checkout.py"},
                                           "target": "fixture_project/checkout.py", "turn_id": "turn-r2"}),
        event("action-t2-2", "action", 7, helper("final", "turn-r2")),
        event("file-change-checkout", "file_change", 8, {"action_id": "action-t2-1", "path": "fixture_project/checkout.py",
                                                         "before_sha256": BEFORE_SHA, "after_sha256": AFTER_SHA}),
        event("response-r2", "assistant_response", 9, {"role": "assistant", "status": "completed", "turn_id": "turn-r2",
                                                       "canary": "SB_SURVIVAL_V1_RESPONSE_R2_x", "text": "SB_SURVIVAL_V1_RESPONSE_R2_x"}),
        event("result-t1-1", "result", 10, {"action_id": "action-t1-1", "exit_code": 0, "status": "success", "output": INSPECT_LINE, "helper_nonce": "inspect-1"}),
        event("result-t1-2", "result", 11, {"action_id": "action-t1-2", "exit_code": 1, "status": "failure", "output": BASELINE_LINE, "helper_nonce": "baseline-1"}),
        event("result-t2-1", "result", 12, {"action_id": "action-t2-1", "exit_code": 0, "status": "success", "output": "compound edit completed"}),
        event("result-t2-2", "result", 13, {"action_id": "action-t2-2", "exit_code": 0, "status": "success", "output": FINAL_LINE, "helper_nonce": "final-1"}),
    ]
    relations = [{"id": f"relation-{index}", "kind": "action_result", "sequence": index, "from_id": f"action-{name}", "to_id": f"result-{name}"}
                 for index, name in enumerate(("t1-1", "t1-2", "t2-1", "t2-2"), 1)]
    relations += [{"id": "relation-order", "kind": "supersedes", "sequence": 5, "from_id": "turn-r1", "to_id": "turn-r2"},
                  {"id": "relation-final", "kind": "final_after", "sequence": 6, "from_id": "turn-r2", "to_id": "action-t2-2"}]
    return {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival", "scenario_id": "survival-v1-repair",
            "independent": True, "run_id": "compound-test", "events": events, "relations": relations}


def _metrics(facts):
    measurement = compare_survival_run(_observer(), facts, {"complete_root": True, "companions": True, "isolated_decode": True,
                                                           "canonical_equality": True}, configuration_id="claude-desktop", repetition=1)
    return {row["id"]: (row["state"], row["correct"], row["observed_eligible"]) for row in measurement["metrics"]}


def test_observer_edit_matches_the_shell_write_by_kind_target_and_turn_and_its_placeholder_result_stays_unmatched():
    metrics = _metrics(_facts())

    assert metrics["work.actions"] == ("measured", 4, 4)
    assert metrics["work.results"] == ("measured", 3, 4)
    assert metrics["causal.action_result"] == ("measured", 3, 4)
    assert metrics["work.changed_files"] == ("measured", 1, 1)
    assert metrics["revision.final_after_r2"] == ("measured", 1, 1)


def test_helper_result_with_an_unknown_exit_code_is_not_credited():
    command = f"python3 bench_check.py inspect --run-canary {CANARY}; python3 bench_check.py baseline --run-canary {CANARY}; echo \"exit=$?\""
    facts = _facts(command_1=command, output_1=f"{INSPECT_LINE}\n{BASELINE_LINE}\nexit=1")
    inspect = next(result for result in facts["results"] if result["action_id"] == "toolu_1:segment-0")

    assert "exit_code" not in inspect and "status" not in inspect
    assert _metrics(facts)["work.results"] == ("measured", 2, 4)


# --- the decoder and the timestamp evidence ------------------------------

def test_decoder_emits_one_event_per_scored_segment_with_the_locator_of_its_native_line(tmp_path):
    decoded = decode_claude_code_bundle(_package(tmp_path, _session()))
    events = [event for event in decoded["events"] if event["kind"] in {"action", "result"}]

    assert [(event["kind"], event["tool_use_id"], event["locator"]["line"], event["timestamp"]) for event in events] == [
        ("action", "toolu_1:segment-1", 2, "2026-09-15T00:00:02Z"), ("action", "toolu_1:segment-3", 2, "2026-09-15T00:00:02Z"),
        ("result", "toolu_1:segment-1", 3, "2026-09-15T00:00:03Z"), ("result", "toolu_1:segment-3", 3, "2026-09-15T00:00:03Z"),
        ("action", "toolu_2:segment-0", 6, "2026-09-15T00:00:06Z"), ("action", "toolu_2:segment-1", 6, "2026-09-15T00:00:06Z"),
        ("result", "toolu_2:segment-1", 7, "2026-09-15T00:00:07Z")]
    assert len({event["id"] for event in decoded["events"]}) == len(decoded["events"])
    assert all(event["parent_tool_use_id"] in {"toolu_1", "toolu_2"} for event in events)
    assert decoded["counts"] == {"submitted_turns": 2, "responses": 2, "actions": 4, "results": 3}
    edit = events[4]
    assert (edit["tool_name"], edit["action_kind"], edit["target"]) == ("Bash", "edit", "checkout.py")
    assert [event["text"] for event in events if event["kind"] == "result"] == [INSPECT_LINE, BASELINE_LINE, FINAL_LINE]


def test_each_credited_event_gets_the_timestamp_of_its_own_native_line(tmp_path):
    from session_bench.format_timestamp_population import build_observer_timestamp_evidence

    decoded = decode_claude_code_bundle(_package(tmp_path, _session()))
    raw = json.dumps(_observer()).encode()
    evidence = build_observer_timestamp_evidence(
        decoded, family="claude", observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()},
        run_id="compound-test", observer_document=raw, complete_record_family=True)

    assert evidence["evidence_complete"] is True
    assert {row["id"]: row["timestamp"] for row in evidence["records"]} == {
        "turn-r1": "2026-09-15T00:00:01Z", "action-t1-1": "2026-09-15T00:00:02Z", "action-t1-2": "2026-09-15T00:00:02Z",
        "result-t1-1": "2026-09-15T00:00:03Z", "result-t1-2": "2026-09-15T00:00:03Z", "response-r1": "2026-09-15T00:00:04Z",
        "turn-r2": "2026-09-15T00:00:05Z", "action-t2-1": "2026-09-15T00:00:06Z", "action-t2-2": "2026-09-15T00:00:06Z",
        "result-t2-2": "2026-09-15T00:00:07Z", "response-r2": "2026-09-15T00:00:08Z"}
    assert len(evidence["records"]) == 11  # the placeholder edit result and the file change have no native witness
