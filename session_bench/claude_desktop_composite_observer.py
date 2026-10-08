"""Integrity-preserving Claude Desktop evidence for composite tool calls.

This builder reads only caller-supplied local hook, GUI-clock, and synthetic
helper receipts. A helper phase is a subprocess fact, never another Claude tool
call. The receipts do not bind helper phases to a parent call or contain Bash
command/output text, so this is an evidence fragment, not a complete scored
observer or a substitute for the native transcript.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping, Sequence

from session_bench.claude_desktop_gui_event_clock import _validate_rows as _validate_gui_rows
from session_bench.claude_desktop_hook_observer import (
    _json_object,
    _validate_ledger,
    PROJECTION_SCHEMA as HOOK_PROJECTION_SCHEMA,
    SCHEMA as HOOK_SCHEMA,
)

SCHEMA = "claude-desktop-composite-call-evidence-v1"
_HOOK_FIELDS = frozenset({
    "schema", "run_id", "session_id", "workspace", "tool_use_id", "event_type",
    "tool_name", "hook_observed_at", "timestamp_provenance", "result_status",
    "result_sha256", "command_sha256", "fixture_relative_target", "duration_ms",
    "command_projection_schema", "command_projection_sha256",
})
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_PHASES = ("inspect", "baseline", "final")


def _stamp(value: Any) -> datetime:
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise ValueError("hook timestamp must be canonical UTC with microseconds")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid hook timestamp") from exc


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def build_composite_call_evidence(
    *,
    hook_jsonl: bytes,
    gui_clock_rows: Sequence[Mapping[str, Any]],
    helper_rows: Sequence[Mapping[str, Any]],
    run_id: str,
    session_id: str,
    workspace: str,
    run_canary: str,
) -> dict[str, Any]:
    """Return an independently captured fragment with exact tool-call count.

    No assignment of helper phases to a Bash call or GUI turn is made. The
    caller must obtain a separate capture-time parent-call binding to make
    either association. This function never reads native session content.
    """
    if not all(isinstance(value, str) and value and value == value.strip()
               for value in (run_id, session_id, workspace, run_canary)):
        raise ValueError("invalid capture identity")
    if not isinstance(hook_jsonl, bytes) or not hook_jsonl or not hook_jsonl.endswith(b"\n") or len(hook_jsonl) > 2_000_000:
        raise ValueError("hook ledger is empty, oversized, or incomplete")
    rows = [_json_object(line) for line in hook_jsonl.splitlines()]
    identity = {"run_id": run_id, "session_id": session_id, "workspace": workspace}
    previous: datetime | None = None
    pre: dict[str, dict[str, Any]] = {}
    post: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not set(row) <= _HOOK_FIELDS or row.get("schema") != HOOK_SCHEMA or any(row.get(k) != v for k, v in identity.items()):
            raise ValueError("hook schema, fields, or capture identity mismatch")
        if row.get("timestamp_provenance") != "local_hook_receipt_clock":
            raise ValueError("hook clock provenance mismatch")
        observed = _stamp(row.get("hook_observed_at"))
        if previous is not None and observed < previous:
            raise ValueError("hook receipt clock moves backwards")
        previous = observed
        if row.get("tool_name") != "Bash":
            raise ValueError("this composite builder requires Bash hook calls")
        if "fixture_relative_target" in row or "command_sha256" not in row:
            raise ValueError("composite Bash call requires only a command digest")
        _sha(row["command_sha256"], "command digest")
        call = row.get("tool_use_id")
        if row.get("event_type") == "PreToolUse":
            if row.get("result_status") != "pending" or row.get("result_sha256") is not None or "duration_ms" in row:
                raise ValueError("malformed start receipt")
            pre[call] = row
        else:
            if row.get("event_type") not in {"PostToolUse", "PostToolUseFailure"}:
                raise ValueError("unsupported hook event")
            if row.get("result_status") != ("success" if row["event_type"] == "PostToolUse" else "failure"):
                raise ValueError("completion status mismatch")
            _sha(row.get("result_sha256"), "result digest")
            post[call] = row
    _validate_ledger(rows, identity)
    if len(pre) != 2 or set(pre) != set(post) or len(rows) != 4:
        raise ValueError("expected exactly two complete tool lifecycles")
    ordered = [row for row in rows if row["event_type"] == "PreToolUse"]
    calls = []
    for number, start in enumerate(ordered, 1):
        finish = post[start["tool_use_id"]]
        if _stamp(finish["hook_observed_at"]) < _stamp(start["hook_observed_at"]):
            raise ValueError("tool completion precedes its start")
        calls.append({
            "sequence": number, "call_id": start["tool_use_id"], "tool_name": "Bash",
            "action_observed_at": start["hook_observed_at"],
            "result_observed_at": finish["hook_observed_at"],
            "timestamp_provenance": "local_hook_receipt_clock",
            "command_sha256": start["command_sha256"],
            "result_sha256": finish["result_sha256"],
            "result_status": finish["result_status"],
            "completion_event_type": finish["event_type"],
        })
    if calls[0]["result_observed_at"] > calls[1]["action_observed_at"]:
        raise ValueError("composite calls overlap or are reordered")
    gui = [dict(row) for row in gui_clock_rows]
    _validate_gui_rows(gui, run_id, require_complete=True)
    if len(helper_rows) != 3:
        raise ValueError("expected exactly three helper phases")
    phases = []
    for expected, source in zip(_PHASES, helper_rows, strict=True):
        if not isinstance(source, Mapping) or any(key in source for key in ("parent_call_id", "tool_use_id", "call_id")):
            raise ValueError("helper phase has an unsupported parent-call claim")
        if source.get("phase") != expected:
            raise ValueError("helper phases must be inspect, baseline, final")
        if source.get("run_canary") != run_canary or source.get("argv") != ["python3", "bench_check.py", expected] or source.get("cwd") != "fixture_project":
            raise ValueError("helper phase identity mismatch")
        if not isinstance(source.get("output"), str) or not source["output"].startswith(f"SB_SURVIVAL_V1_HELPER_{expected.upper()}_"):
            raise ValueError("helper output identity mismatch")
        if type(source.get("exit_code")) is not int or source["exit_code"] < 0:
            raise ValueError("invalid helper exit code")
        phases.append({"phase": expected, "exit_code": source["exit_code"],
                       "checkout_sha256": _sha(source.get("checkout_sha256"), "helper checkout digest"),
                       "output": source["output"], "parent_call_id": None,
                       "parent_binding": "unobserved"})
    return {
        "schema": SCHEMA, **identity,
        "tool_call_count": 2, "tool_calls": calls,
        "gui_boundaries": gui, "helper_phases": phases,
        "claim_limit": "Tool calls have independent hook IDs, hashes, and clocks. Helper phases have no observed parent-call binding; this fragment is not a complete scored observer.",
    }



def _ordered_hook_calls(hook_jsonl: bytes, *, run_id: str, session_id: str, workspace: str) -> list[dict[str, Any]]:
    """Validate a variable-size hook ledger without touching native data."""
    if not isinstance(hook_jsonl, bytes) or not hook_jsonl or not hook_jsonl.endswith(b"\n") or len(hook_jsonl) > 2_000_000:
        raise ValueError("hook ledger is empty, oversized, or incomplete")
    rows = [_json_object(line) for line in hook_jsonl.splitlines()]
    identity = {"run_id": run_id, "session_id": session_id, "workspace": workspace}
    starts: dict[str, dict[str, Any]] = {}
    finishes: dict[str, dict[str, Any]] = {}
    previous: datetime | None = None
    for row in rows:
        if not set(row) <= _HOOK_FIELDS or row.get("schema") != HOOK_SCHEMA or any(row.get(k) != v for k, v in identity.items()):
            raise ValueError("hook schema, fields, or identity mismatch")
        projection_schema = row.get("command_projection_schema")
        projection_sha256 = row.get("command_projection_sha256")
        if (projection_schema is None) != (projection_sha256 is None):
            raise ValueError("incomplete hook command projection binding")
        if projection_schema is not None and (
            row.get("event_type") != "PreToolUse"
            or projection_schema != HOOK_PROJECTION_SCHEMA
            or not isinstance(projection_sha256, str)
            or not _SHA.fullmatch(projection_sha256)
        ):
            raise ValueError("invalid hook command projection binding")
        if row.get("timestamp_provenance") != "local_hook_receipt_clock":
            raise ValueError("hook clock provenance mismatch")
        stamp = _stamp(row.get("hook_observed_at"))
        if previous is not None and stamp < previous:
            raise ValueError("hook clock moves backwards")
        previous = stamp
        tool = row.get("tool_name")
        if tool not in {"Bash", "Edit", "Write"}:
            raise ValueError("unsupported tool name")
        if tool == "Bash":
            if "fixture_relative_target" in row:
                raise ValueError("Bash hook has a fixture target")
            _sha(row.get("command_sha256"), "command digest")
        else:
            if "command_sha256" in row or row.get("fixture_relative_target") != "checkout.py":
                raise ValueError("direct edit target is not synthetic checkout.py")
        call_id = row.get("tool_use_id")
        if row.get("event_type") == "PreToolUse":
            if row.get("result_status") != "pending" or row.get("result_sha256") is not None or "duration_ms" in row:
                raise ValueError("malformed hook start")
            starts[call_id] = row
        elif row.get("event_type") in {"PostToolUse", "PostToolUseFailure"}:
            if row.get("result_status") != ("success" if row["event_type"] == "PostToolUse" else "failure"):
                raise ValueError("hook completion status mismatch")
            _sha(row.get("result_sha256"), "result digest")
            finishes[call_id] = row
        else:
            raise ValueError("unsupported hook event")
    _validate_ledger(rows, identity)
    if not starts or set(starts) != set(finishes) or len(rows) != 2 * len(starts):
        raise ValueError("hook ledger has incomplete or duplicate lifecycles")
    calls = []
    for start in (row for row in rows if row["event_type"] == "PreToolUse"):
        finish = finishes[start["tool_use_id"]]
        if _stamp(finish["hook_observed_at"]) < _stamp(start["hook_observed_at"]):
            raise ValueError("tool completion precedes start")
        calls.append({"call_id": start["tool_use_id"], "tool_name": start["tool_name"],
                      "command_sha256": start.get("command_sha256"),
                      "fixture_relative_target": start.get("fixture_relative_target"),
                      "action_observed_at": start["hook_observed_at"],
                      "result_observed_at": finish["hook_observed_at"],
                      "result_sha256": finish["result_sha256"],
                      "result_status": finish["result_status"]})
    for earlier, later in zip(calls, calls[1:]):
        if earlier["result_observed_at"] >= later["action_observed_at"]:
            raise ValueError("tool lifecycles overlap")
    return calls


def build_scored_composite_observer(
    *, workload: Mapping[str, Any], hook_jsonl: bytes,
    gui_clock_rows: Sequence[Mapping[str, Any]],
    helper_rows: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    command_projections: Sequence[Mapping[str, Any]],
    gui_receipt: Mapping[str, Any], session_id: str, workspace: str,
    before_checkout_sha256: str, after_checkout_sha256: str,
) -> dict[str, Any]:
    """Build a standard observer from capture-time receipts only.

    The GUI turn clock must precede each call, and the response clock must
    follow it. The parent shell command itself, never a native transcript,
    proves compound helper and edit projections.
    """
    from session_bench.live_observer import _validate_workload

    validated = _validate_workload(workload)
    run_id, run_canary = validated["run_id"], validated["run_canary"]
    turns = validated["turns"]
    gui = [dict(row) for row in gui_clock_rows]
    _validate_gui_rows(gui, run_id, require_complete=True)
    clock = {row["event_id"]: row for row in gui}
    calls = _ordered_hook_calls(hook_jsonl, run_id=run_id, session_id=session_id, workspace=workspace)
    if not isinstance(command_projections, Sequence) or len(command_projections) != len(calls):
        raise ValueError("projection count differs from hook lifecycles")
    if not isinstance(gui_receipt, Mapping) or gui_receipt.get("run_id") != run_id:
        raise ValueError("GUI/config receipt run mismatch")
    model, configuration = gui_receipt.get("model_id"), gui_receipt.get("configuration")
    visible = gui_receipt.get("observations")
    if not isinstance(model, str) or not model.strip() or configuration != "claude-desktop" or not isinstance(visible, Mapping):
        raise ValueError("GUI/config model, configuration, or observations missing")
    for number, turn in enumerate(turns, 1):
        if visible.get(f"r{number}_canary_visible") != turn["response_canary"]:
            raise ValueError("GUI response canary mismatch")
    if visible.get("r2_edit_visible") is not True or visible.get("final_table_rows") != 3:
        raise ValueError("GUI receipt lacks visible edit or final tests")
    before, after = _sha(before_checkout_sha256, "before digest"), _sha(after_checkout_sha256, "after digest")
    if before == after:
        raise ValueError("checkout did not change")
    if isinstance(helper_rows, Mapping):
        ordered_helpers = [helper_rows.get(phase) for phase in _PHASES]
    elif isinstance(helper_rows, Sequence):
        ordered_helpers = list(helper_rows)
    else:
        ordered_helpers = []
    if len(ordered_helpers) != 3:
        raise ValueError("expected exactly three helper phases")
    helpers: dict[str, dict[str, Any]] = {}
    for phase, raw in zip(_PHASES, ordered_helpers, strict=True):
        if not isinstance(raw, Mapping) or raw.get("phase") != phase or any(k in raw for k in ("parent_call_id", "tool_use_id", "call_id")):
            raise ValueError("helper phase identity or unverified parent claim")
        if raw.get("run_canary") != run_canary or raw.get("argv") != ["python3", "bench_check.py", phase] or raw.get("cwd") != "fixture_project":
            raise ValueError("helper identity mismatch")
        if not isinstance(raw.get("output"), str) or not raw["output"].startswith(f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_"):
            raise ValueError("helper output mismatch")
        if type(raw.get("exit_code")) is not int or raw["exit_code"] < 0:
            raise ValueError("invalid helper exit code")
        helpers[phase] = {"phase": phase, "exit_code": raw["exit_code"],
                          "checkout_sha256": _sha(raw.get("checkout_sha256"), "helper checkout digest"),
                          "output": raw["output"]}
    if helpers["inspect"]["checkout_sha256"] != before or helpers["baseline"]["checkout_sha256"] != before or helpers["final"]["checkout_sha256"] != after:
        raise ValueError("fixture hashes disagree with helper ledger")

    phase_parent: dict[str, str] = {}
    classified: list[dict[str, Any]] = []
    edit_candidates: list[str] = []
    for call, raw_projection in zip(calls, command_projections, strict=True):
        if not isinstance(raw_projection, Mapping) or raw_projection.get("schema") != "claude-desktop-command-projection-v2":
            raise ValueError("unsupported command projection")
        projection = dict(raw_projection)
        for key in ("run_id", "session_id", "call_id", "tool_name", "result_status", "result_sha256", "action_observed_at", "result_observed_at"):
            expected = {"run_id": run_id, "session_id": session_id, **call}.get(key)
            if projection.get(key) != expected:
                raise ValueError(f"command projection {key} differs from hook")
        start, finish = _stamp(call["action_observed_at"]), _stamp(call["result_observed_at"])
        windows = [number for number in (1, 2)
                   if _stamp(clock[f"turn-r{number}"]["gui_observed_at"]) < start
                   and finish < _stamp(clock[f"response-r{number}"]["gui_observed_at"])]
        if len(windows) != 1:
            raise ValueError("tool lifecycle is outside a unique GUI turn/response window")
        number = windows[0]
        if projection.get("turn") not in (None, f"r{number}"):
            raise ValueError("projected turn contradicts GUI window")
        declared, tool = projection.get("helper_phases"), call["tool_name"]
        if not isinstance(declared, list):
            raise ValueError("helper phase projection is missing")
        if projection.get("cwd") != "fixture_project":
            raise ValueError("command projection cwd mismatch")
        if tool == "Bash":
            # The local hook writer parses the command in memory and stores
            # only this structured projection, bound to its command digest.
            if "command" in projection or projection.get("command_sha256") != call["command_sha256"]:
                raise ValueError("structured Bash projection digest mismatch")
            phases = []
            for item in declared:
                if not isinstance(item, Mapping):
                    raise ValueError("malformed helper projection")
                phases.append(item.get("phase"))
            canonical_kind = "inspect" if phases[:1] == ["inspect"] else "test" if phases[:1] in (["baseline"], ["final"]) else "shell"
            canonical_argv = ["python3", "bench_check.py", phases[0]] if phases else None
            if projection.get("action_kind") != canonical_kind or projection.get("argv") != canonical_argv:
                raise ValueError("Bash action kind or argv differs from helper projection")
            if projection.get("target") != ("fixture_project/checkout.py" if phases else None):
                raise ValueError("Bash projected target mismatch")
            compound = projection.get("compound_edit")
            if type(compound) is not bool:
                raise ValueError("compound edit recognition missing")
            if projection.get("compound_edit_target") != ("fixture_project/checkout.py" if compound else None):
                raise ValueError("compound edit target mismatch")
        else:
            if ("command" in projection or projection.get("command_sha256") not in (None, "")
                    or projection.get("target") != "fixture_project/checkout.py"
                    or projection.get("action_kind") != "edit"
                    or projection.get("argv") != ["replace_function", "fixture_project/checkout.py"]):
                raise ValueError("direct edit projection shape mismatch")
            if projection.get("compound_edit") is not False or projection.get("compound_edit_target") is not None or declared:
                raise ValueError("direct edit cannot contain helper or compound call")
            phases, compound = [], False
        if len(declared) != len(phases):
            raise ValueError("helper phase projection count mismatch")
        for phase, item in zip(phases, declared, strict=True):
            if phase not in _PHASES or not isinstance(item, Mapping) or item.get("phase") != phase or item.get("argv") != ["python3", "bench_check.py", phase] or item.get("run_canary") != run_canary:
                raise ValueError("helper projection phase or argv mismatch")
            if phase in phase_parent or (number == 1 and phase == "final") or (number == 2 and phase != "final"):
                raise ValueError("helper phase has ambiguous or wrong-turn binding")
            phase_parent[phase] = call["call_id"]
        if tool in {"Edit", "Write"} or compound:
            if number != 2 or call["result_status"] != "success":
                raise ValueError("edit must complete successfully in R2")
            edit_candidates.append(call["call_id"])
        classified.append({**call, "turn_number": number, "helper_phases": phases,
                           "compound_edit": compound})
    if set(phase_parent) != set(_PHASES):
        raise ValueError("helper phase binding incomplete")
    observed_phases = [phase for call in classified for phase in call["helper_phases"]]
    if observed_phases != list(_PHASES):
        raise ValueError("helper phase order must be inspect, baseline, then final")
    if len(edit_candidates) != 1:
        raise ValueError("file change requires exactly one observed or projected edit")
    if [row["turn_number"] for row in classified] != sorted(row["turn_number"] for row in classified):
        raise ValueError("tool lifecycles are out of turn order")

    events: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    def event(event_id: str, kind: str, boundary: str, role: str, source: str, fields: dict[str, Any], metrics: list[str]) -> None:
        fields = dict(fields)
        call_ref = fields.pop("call_id", None)
        fields.pop("observed_at", None)
        fields.pop("timestamp_provenance", None)
        if call_ref is not None:
            fields["hook_call_ref"] = call_ref.split(":compound-edit", 1)[0]
            if call_ref.endswith(":compound-edit"):
                fields["projected_compound_edit"] = True
        events.append({"id": event_id, "sequence": len(events) + 1, "boundary": boundary,
                       "population_role": role, "kind": kind, "session_id": session_id,
                       "source": source, "fields": fields, "metric_ids": metrics})
    def relation(kind: str, source: str, target: str) -> None:
        relations.append({"id": f"relation-{kind}-{source}", "sequence": len(relations) + 1,
                          "kind": kind, "from_id": source, "to_id": target})
    action_by_call: dict[str, str] = {}
    edit_action_id: str | None = None
    final_action_id: str | None = None
    for number in (1, 2):
        turn = turns[number - 1]
        turn_id = turn["id"]
        revision = f"r{number}"
        event(turn_id, "user_turn", "accepted", "primary_scored", "submitted_input",
              {"turn_id": turn_id, "revision": revision, "role": "user", "text": turn["text"],
               "run_canary": run_canary, "response_canary": turn["response_canary"],
               "observed_at": clock[f"turn-{revision}"]["gui_observed_at"],
               "timestamp_provenance": clock[f"turn-{revision}"]["timestamp_provenance"]},
              ["work.submitted_turns", f"revision.{revision}", "revision.r1_r2_order"])
        per_turn = 0
        for call in (row for row in classified if row["turn_number"] == number):
            per_turn += 1
            tool = call["tool_name"]
            action_id, result_id = f"action-t{number}-{per_turn}", f"result-t{number}-{per_turn}"
            action_by_call[call["call_id"]] = action_id
            phases = call["helper_phases"]
            phase = phases[0] if phases else None
            kind = ("edit" if tool in {"Edit", "Write"} else
                    "inspect" if phase == "inspect" else "test" if phase in {"baseline", "final"} else "shell")
            fields: dict[str, Any] = {"action_kind": kind, "name": tool, "turn_id": turn_id,
                                      "call_id": call["call_id"],
                                      "observed_at": call["action_observed_at"],
                                      "timestamp_provenance": "local_hook_receipt_clock"}
            if tool == "Bash":
                fields["command_sha256"] = call["command_sha256"]
                fields["cwd"] = "fixture_project"
                if phase:
                    fields["argv"] = ["python3", "bench_check.py", phase]
                    fields["target"] = "fixture_project/checkout.py"
            else:
                fields.update({"target": "fixture_project/checkout.py", "cwd": "fixture_project",
                               "argv": ["replace_function", "fixture_project/checkout.py"]})
                edit_action_id = action_id
            event(action_id, "action", "harness_received", "primary_scored", "local_hook_receipt",
                  fields, ["work.actions", "causal.action_result"])
            event(result_id, "result", "harness_received", "primary_scored", "local_hook_receipt",
                  {"action_id": action_id, "call_id": call["call_id"], "status": call["result_status"],
                   "result_sha256": call["result_sha256"], "observed_at": call["result_observed_at"],
                   "timestamp_provenance": "local_hook_receipt_clock"},
                  ["work.results", "causal.action_result"])
            relation("action_result", action_id, result_id)
            if "final" in phases:
                final_action_id = action_id
            if call["compound_edit"]:
                edit_action_id = f"{action_id}-compound-edit"
                compound_result = f"{result_id}-compound-edit"
                synthetic_call = f"{call['call_id']}:compound-edit"
                event(edit_action_id, "action", "harness_received", "primary_scored", "command_projection",
                      {"action_kind": "edit", "name": "Edit", "turn_id": turn_id,
                       "call_id": synthetic_call, "compound_parent_call_id": call["call_id"],
                       "argv": ["replace_function", "fixture_project/checkout.py"],
                       "target": "fixture_project/checkout.py", "cwd": "fixture_project",
                       "observed_at": call["action_observed_at"],
                       "timestamp_provenance": "local_hook_receipt_clock"},
                      ["work.actions", "causal.action_result"])
                event(compound_result, "result", "harness_received", "primary_scored", "command_projection",
                      {"action_id": edit_action_id, "call_id": synthetic_call, "status": "success",
                       "observed_at": call["result_observed_at"],
                       "timestamp_provenance": "local_hook_receipt_clock"},
                      ["work.results", "causal.action_result"])
                relation("action_result", edit_action_id, compound_result)
        event(f"response-{revision}", "assistant_response", "displayed", "primary_scored", "desktop_accessibility",
              {"turn_id": turn_id, "role": "assistant", "status": "completed",
               "text": visible[f"r{number}_canary_visible"], "canary": turn["response_canary"],
               "model_id": model, "configuration": configuration,
               "observed_at": clock[f"response-{revision}"]["gui_observed_at"],
               "timestamp_provenance": clock[f"response-{revision}"]["timestamp_provenance"]},
              ["work.visible_responses", "causal.turn_response", "attribution.model_config"])
        relation("turn_response", turn_id, f"response-{revision}")
    assert edit_action_id is not None and final_action_id is not None
    edit_call = next(row for row in classified if row["call_id"] == edit_candidates[0])
    event("file-change-checkout", "file_change", "file_observed", "primary_scored", "filesystem_snapshot",
          {"path": "fixture_project/checkout.py", "before_sha256": before,
           "after_sha256": after, "action_id": edit_action_id,
           "call_id": f"{edit_call['call_id']}:compound-edit" if edit_call["compound_edit"] else edit_call["call_id"],
           "observed_at": edit_call["result_observed_at"],
           "timestamp_provenance": "local_hook_receipt_clock"}, ["work.changed_files"])
    relation("supersedes", turns[0]["id"], turns[1]["id"])
    relation("final_after", turns[1]["id"], final_action_id)
    for phase in _PHASES:
        helper_id, action_id = f"helper-{phase}", action_by_call[phase_parent[phase]]
        row = helpers[phase]
        event(helper_id, "helper", "helper_emitted", "supporting", "helper_ledger",
              {"phase": phase, "argv": ["python3", "bench_check.py", phase],
               "exit_code": row["exit_code"], "output": row["output"],
               "checkout_sha256": row["checkout_sha256"],
               "parent_call_id": phase_parent[phase], "action_id": action_id}, [])
        relation("helper_for", helper_id, action_id)
    return {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
            "scenario_id": "survival-v1-repair", "run_id": run_id, "independent": True,
            "method": "capture-time Desktop hook command projection, GUI/config receipt, synthetic helper ledger, and fixture hashes; no native transcript",
            "events": events, "relations": relations}
