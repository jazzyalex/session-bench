"""Composite-call receipts never inflate subprocesses into Claude actions."""

import json

import pytest

from session_bench.claude_desktop_composite_observer import build_composite_call_evidence
from session_bench.claude_desktop_gui_event_clock import EVENT_PROVENANCE


RUN = "claude-desktop-v1-eval-test"
SESSION = "session-1"
WORKSPACE = "/synthetic/fixture_project"
CANARY = "SB_SURVIVAL_V1_RUN_" + RUN


def _encode(rows):
    return ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode()


def _inputs():
    hooks = []
    for number, status in ((1, "failure"), (2, "success")):
        for kind in ("PreToolUse", "PostToolUseFailure" if status == "failure" else "PostToolUse"):
            hooks.append({
                "schema": "claude-desktop-hook-observer-v1", "run_id": RUN,
                "session_id": SESSION, "workspace": WORKSPACE,
                "tool_use_id": f"call-{number}", "event_type": kind,
                "tool_name": "Bash", "command_sha256": str(number) * 64,
                "hook_observed_at": f"2026-10-02T20:4{number}:0{0 if kind == 'PreToolUse' else 1}.000000Z",
                "timestamp_provenance": "local_hook_receipt_clock",
                "result_status": "pending" if kind == "PreToolUse" else status,
                "result_sha256": None if kind == "PreToolUse" else str(number + 2) * 64,
            })
    gui = []
    for number, (event_id, kind) in enumerate((
        ("turn-r1", "user_turn"), ("response-r1", "assistant_response"),
        ("turn-r2", "user_turn"), ("response-r2", "assistant_response")), 1):
        gui.append({"schema": "claude-desktop-gui-event-clock-v1", "run_id": RUN,
                    "event_id": event_id, "event_kind": kind,
                    "gui_observed_at": f"2026-10-02T20:4{1 if number < 3 else 2}:{number:02}.000000Z",
                    "timestamp_provenance": EVENT_PROVENANCE[event_id]})
    helper = [{"phase": phase, "run_canary": CANARY,
               "argv": ["python3", "bench_check.py", phase], "cwd": "fixture_project",
               "output": f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_nonce",
               "checkout_sha256": "a" * 64, "exit_code": code}
              for phase, code in (("inspect", 0), ("baseline", 1), ("final", 0))]
    return {"hook_jsonl": _encode(hooks), "gui_clock_rows": gui, "helper_rows": helper,
            "run_id": RUN, "session_id": SESSION, "workspace": WORKSPACE,
            "run_canary": CANARY}


def test_two_real_calls_and_three_unbound_helper_phases():
    evidence = build_composite_call_evidence(**_inputs())
    assert evidence["tool_call_count"] == len(evidence["tool_calls"]) == 2
    assert [call["result_status"] for call in evidence["tool_calls"]] == ["failure", "success"]
    assert len({call["call_id"] for call in evidence["tool_calls"]}) == 2
    assert [row["phase"] for row in evidence["helper_phases"]] == ["inspect", "baseline", "final"]
    assert {row["parent_call_id"] for row in evidence["helper_phases"]} == {None}
    assert len(evidence["gui_boundaries"]) == 4


@pytest.mark.parametrize("mutator", [
    lambda rows: rows.pop(),
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows[1].update(command_sha256="0" * 64),
    lambda rows: rows[1].update(result_sha256="bad"),
    lambda rows: rows[0].update(run_id="other"),
    lambda rows: rows[2].update(hook_observed_at="2026-10-02T20:40:00.000000Z"),
])
def test_hook_mutations_rejected(mutator):
    args = _inputs()
    rows = [json.loads(line) for line in args["hook_jsonl"].splitlines()]
    mutator(rows)
    args["hook_jsonl"] = _encode(rows)
    with pytest.raises(ValueError):
        build_composite_call_evidence(**args)


def test_helper_phase_cannot_claim_parent_call_without_capture_binding():
    args = _inputs()
    args["helper_rows"][0]["parent_call_id"] = "invented"
    with pytest.raises(ValueError, match="parent-call claim"):
        build_composite_call_evidence(**args)


def test_gui_gap_rejected():
    args = _inputs()
    args["gui_clock_rows"].pop()
    with pytest.raises(ValueError):
        build_composite_call_evidence(**args)


def _scored_inputs():
    import hashlib
    args = _inputs()
    commands = [
        f"python3 bench_check.py inspect --run-canary {CANARY}; python3 bench_check.py baseline --run-canary {CANARY}",
        f"apply_patch checkout.py; python3 bench_check.py final --run-canary {CANARY}",
    ]
    rows = [json.loads(line) for line in args["hook_jsonl"].splitlines()]
    projections = []
    args["helper_rows"][2]["checkout_sha256"] = "b" * 64
    args["gui_clock_rows"][0]["gui_observed_at"] = "2026-10-02T20:40:00.000000Z"
    args["gui_clock_rows"][1]["gui_observed_at"] = "2026-10-02T20:41:30.000000Z"
    args["gui_clock_rows"][2]["gui_observed_at"] = "2026-10-02T20:41:40.000000Z"
    args["gui_clock_rows"][3]["gui_observed_at"] = "2026-10-02T20:42:30.000000Z"
    for number, command in enumerate(commands, 1):
        digest = hashlib.sha256(command.encode()).hexdigest()
        rows[(number - 1) * 2]["command_sha256"] = digest
        rows[(number - 1) * 2 + 1]["command_sha256"] = digest
        projections.append({
            "schema": "claude-desktop-command-projection-v2", "run_id": RUN,
            "session_id": SESSION, "call_id": f"call-{number}", "turn": f"r{number}",
            "tool_name": "Bash", "command_sha256": digest, "cwd": "fixture_project",
            "action_kind": "inspect" if number == 1 else "test",
            "argv": ["python3", "bench_check.py", "inspect" if number == 1 else "final"],
            "target": "fixture_project/checkout.py",
            "helper_phases": [{"phase": phase, "argv": ["python3", "bench_check.py", phase], "run_canary": CANARY}
                              for phase in (("inspect", "baseline") if number == 1 else ("final",))],
            "compound_edit": number == 2,
            "compound_edit_target": "fixture_project/checkout.py" if number == 2 else None,
            "result_status": "failure" if number == 1 else "success",
            "result_sha256": str(number + 2) * 64,
            "action_observed_at": rows[(number - 1) * 2]["hook_observed_at"],
            "result_observed_at": rows[(number - 1) * 2 + 1]["hook_observed_at"],
        })
    args["hook_jsonl"] = _encode(rows)
    response = ["SB_SURVIVAL_V1_RESPONSE_R1", "SB_SURVIVAL_V1_RESPONSE_R2"]
    workload = {"schema_version": "1.0-survival-workload", "protocol_version": "1.0-survival",
                "scenario_id": "survival-v1-repair", "run_id": RUN, "run_canary": CANARY,
                "turns": [{"id": f"turn-r{number}", "sequence": number, "revision": f"r{number}",
                           "text": f"Task {CANARY} end {response[number - 1]}",
                           "response_canary": response[number - 1]}
                          for number in (1, 2)],
                "response_canaries": [{"id": f"response-r{number}", "turn_id": f"turn-r{number}",
                                       "value": response[number - 1]} for number in (1, 2)]}
    gui = {"run_id": RUN, "configuration": "claude-desktop", "model_id": "claude-sonnet-5-5",
           "observations": {"r1_canary_visible": response[0], "r2_canary_visible": response[1],
                            "r2_edit_visible": True, "final_table_rows": 3}}
    args.update(workload=workload, command_projections=projections, gui_receipt=gui,
                before_checkout_sha256="a" * 64, after_checkout_sha256="b" * 64)
    args.pop("run_id")
    args.pop("run_canary")
    return args


def test_scored_observer_has_two_bash_calls_and_one_proven_compound_edit():
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    from session_bench.live_metric_comparator import _validate_observer
    observer = build_scored_composite_observer(**_scored_inputs())
    _validate_observer(observer)
    actions = [row for row in observer["events"] if row["kind"] == "action"]
    results = [row for row in observer["events"] if row["kind"] == "result"]
    changes = [row for row in observer["events"] if row["kind"] == "file_change"]
    assert [row["fields"]["name"] for row in actions] == ["Bash", "Bash", "Edit"]
    assert len(results) == 3 and len(changes) == 1
    assert len([row for row in observer["events"] if row["kind"] == "helper"]) == 3
    assert actions[-1]["fields"]["compound_parent_call_id"] == "call-2"
    assert all("call_id" not in row["fields"] and "observed_at" not in row["fields"] for row in observer["events"])
    assert actions[-1]["fields"]["hook_call_ref"] == "call-2"


def test_scored_observer_accepts_capture_time_projection_tags_on_hook_starts():
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    from session_bench.claude_desktop_hook_observer import PROJECTION_SCHEMA

    args = _scored_inputs()
    rows = [json.loads(line) for line in args["hook_jsonl"].splitlines()]
    for row in rows:
        if row["event_type"] == "PreToolUse":
            row["command_projection_schema"] = PROJECTION_SCHEMA
            row["command_projection_sha256"] = "f" * 64
    args["hook_jsonl"] = _encode(rows)
    args["helper_rows"] = {row["phase"]: row for row in args["helper_rows"]}

    observer = build_scored_composite_observer(**args)
    actions = [row for row in observer["events"] if row["kind"] == "action"]
    assert [row["fields"]["name"] for row in actions] == ["Bash", "Bash", "Edit"]


@pytest.mark.parametrize("change", [
    lambda args: args["command_projections"][0].update(command="echo forged"),
    lambda args: args["command_projections"][0]["helper_phases"].pop(),
    lambda args: args["command_projections"][0].update(command_sha256="0" * 64),
    lambda args: args["helper_rows"][2].update(checkout_sha256="a" * 64),
    lambda args: args["command_projections"][1].update(compound_edit=False),
    lambda args: args["command_projections"][1].update(compound_edit_target="other.py"),
    lambda args: args["gui_receipt"]["observations"].update(r2_canary_visible="wrong"),
])
def test_scored_observer_rejects_unsupported_projection(change):
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    args = _scored_inputs()
    change(args)
    with pytest.raises(ValueError):
        build_scored_composite_observer(**args)


def _separate_inputs():
    import hashlib
    args = _scored_inputs()
    args["command_projections"] = []
    rows = []
    specs = [
        ("inspect", "Bash", "success", "2026-10-02T20:41:00.000000Z", "2026-10-02T20:41:01.000000Z"),
        ("baseline", "Bash", "failure", "2026-10-02T20:41:10.000000Z", "2026-10-02T20:41:11.000000Z"),
        ("edit", "Edit", "success", "2026-10-02T20:42:00.000000Z", "2026-10-02T20:42:01.000000Z"),
        ("final", "Bash", "success", "2026-10-02T20:42:10.000000Z", "2026-10-02T20:42:11.000000Z"),
    ]
    for number, (phase, tool, status, start, finish) in enumerate(specs, 1):
        command = f"python3 bench_check.py {phase} --run-canary {CANARY}" if tool == "Bash" else None
        digest = hashlib.sha256(command.encode()).hexdigest() if command else None
        for kind, stamp in (("PreToolUse", start),
                            ("PostToolUseFailure" if status == "failure" else "PostToolUse", finish)):
            row = {"schema": "claude-desktop-hook-observer-v1", "run_id": RUN,
                   "session_id": SESSION, "workspace": WORKSPACE,
                   "tool_use_id": f"call-{number}", "event_type": kind,
                   "tool_name": tool, "hook_observed_at": stamp,
                   "timestamp_provenance": "local_hook_receipt_clock",
                   "result_status": "pending" if kind == "PreToolUse" else status,
                   "result_sha256": None if kind == "PreToolUse" else str(number + 2) * 64}
            if digest:
                row["command_sha256"] = digest
            else:
                row["fixture_relative_target"] = "checkout.py"
            rows.append(row)
        projection = {"schema": "claude-desktop-command-projection-v2", "run_id": RUN,
                      "session_id": SESSION, "call_id": f"call-{number}",
                      "tool_name": tool, "result_status": status,
                      "result_sha256": str(number + 2) * 64,
                      "action_observed_at": start, "result_observed_at": finish,
                      "cwd": "fixture_project", "compound_edit": False,
                      "compound_edit_target": None,
                      "helper_phases": [] if tool == "Edit" else [
                          {"phase": phase, "argv": ["python3", "bench_check.py", phase], "run_canary": CANARY}],
                      "action_kind": "edit" if tool == "Edit" else "inspect" if phase == "inspect" else "test",
                      "argv": ["replace_function", "fixture_project/checkout.py"] if tool == "Edit" else
                              ["python3", "bench_check.py", phase],
                      "target": "fixture_project/checkout.py"}
        if digest:
            projection["command_sha256"] = digest
        args["command_projections"].append(projection)
    args["hook_jsonl"] = _encode(rows)
    return args


def test_separate_helper_calls_and_direct_edit_are_counted_once():
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    from session_bench.live_metric_comparator import _validate_observer
    observer = build_scored_composite_observer(**_separate_inputs())
    _validate_observer(observer)
    actions = [row for row in observer["events"] if row["kind"] == "action"]
    results = [row for row in observer["events"] if row["kind"] == "result"]
    assert [row["fields"]["name"] for row in actions] == ["Bash", "Bash", "Edit", "Bash"]
    assert len(results) == 4
    change = next(row for row in observer["events"] if row["kind"] == "file_change")
    assert change["fields"]["action_id"] == actions[2]["id"]
    assert change["fields"]["hook_call_ref"] == "call-3"


def test_timestamp_view_joins_each_observation_to_capture_time_hook_and_gui_clocks():
    from scripts.finalize_claude_desktop_runs import _composite_timestamp_observer
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer

    args = _scored_inputs()
    observer = build_scored_composite_observer(**args)
    stamped = _composite_timestamp_observer(
        observer, args["hook_jsonl"], args["gui_clock_rows"],
    )
    by_id = {row["id"]: row for row in stamped["events"]}
    starts = {
        json.loads(line)["tool_use_id"]: json.loads(line)["hook_observed_at"]
        for line in args["hook_jsonl"].splitlines()
        if json.loads(line)["event_type"] == "PreToolUse"
    }
    assert by_id["action-t1-1"]["fields"]["observed_at"] == starts["call-1"]
    assert by_id["action-t1-1"]["fields"]["call_id"] == "call-1"
    assert by_id["action-t2-1-compound-edit"]["fields"]["observed_at"] == starts["call-2"]
    assert by_id["action-t2-1-compound-edit"]["fields"]["call_id"] == "call-2:compound-edit"
    assert by_id["turn-r1"]["fields"]["observed_at"] == args["gui_clock_rows"][0]["gui_observed_at"]
    assert by_id["response-r2"]["fields"]["observed_at"] == args["gui_clock_rows"][3]["gui_observed_at"]
    assert by_id["file-change-checkout"]["fields"]["observed_at"] == next(
        json.loads(line)["hook_observed_at"] for line in args["hook_jsonl"].splitlines()
        if json.loads(line)["tool_use_id"] == "call-2"
        and json.loads(line)["event_type"] == "PostToolUse"
    )

    stripped = json.loads(json.dumps(stamped))
    for event in stripped["events"]:
        for key in ("call_id", "observed_at", "timestamp_provenance"):
            event["fields"].pop(key, None)
    assert stripped == observer


@pytest.mark.parametrize("mutator", [
    lambda args: args["command_projections"][1]["helper_phases"].clear(),
    lambda args: args["command_projections"][1]["helper_phases"].append(
        {"phase": "inspect", "argv": ["python3", "bench_check.py", "inspect"], "run_canary": CANARY}),
    lambda args: args["command_projections"][3]["helper_phases"][0].update(phase="baseline"),
    lambda args: args["gui_clock_rows"][1].update(gui_observed_at="2026-10-02T20:41:00.500000Z"),
    lambda args: args["command_projections"][2].update(target="other.py"),
])
def test_separate_capture_rejects_missing_ambiguous_or_out_of_window_calls(mutator):
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    args = _separate_inputs()
    mutator(args)
    with pytest.raises(ValueError):
        build_scored_composite_observer(**args)


def test_composite_baseline_before_inspect_rejected():
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    args = _scored_inputs()
    first = args["command_projections"][0]
    first["helper_phases"].reverse()
    first["action_kind"] = "test"
    first["argv"] = ["python3", "bench_check.py", "baseline"]
    with pytest.raises(ValueError, match="helper phase order"):
        build_scored_composite_observer(**args)


def test_separate_inspect_after_baseline_rejected():
    from session_bench.claude_desktop_composite_observer import build_scored_composite_observer
    args = _separate_inputs()
    rows = [json.loads(line) for line in args["hook_jsonl"].splitlines()]
    # Move the baseline lifecycle before inspect, with monotonic capture clocks.
    rows = rows[2:4] + rows[0:2] + rows[4:]
    rows[0]["hook_observed_at"] = "2026-10-02T20:41:00.000000Z"
    rows[1]["hook_observed_at"] = "2026-10-02T20:41:01.000000Z"
    rows[2]["hook_observed_at"] = "2026-10-02T20:41:10.000000Z"
    rows[3]["hook_observed_at"] = "2026-10-02T20:41:11.000000Z"
    args["hook_jsonl"] = _encode(rows)
    projections = args["command_projections"]
    args["command_projections"] = [projections[1], projections[0], *projections[2:]]
    for projection, start in zip(args["command_projections"][:2], ("00", "10")):
        projection["action_observed_at"] = f"2026-10-02T20:41:{start}.000000Z"
        projection["result_observed_at"] = f"2026-10-02T20:41:{int(start) + 1:02}.000000Z"
    with pytest.raises(ValueError, match="helper phase order"):
        build_scored_composite_observer(**args)
