"""Independent Codex CLI stdout/helper observer for retained two-turn captures.

This opt-in successor reads no native evidence. Item IDs join only a stdout
start/completion within one turn; they are never asserted to be native call IDs.
Helper phase exits, nonces, cwd and filesystem hashes come from the separately
retained helper ledger and snapshots, bound by the original capture receipt.
The stdout turn.completed usage is retained as a diagnostic only: these captures
show cumulative thread totals, not returned response-scoped usage. No requested
model, missing patch body, edit exit code, timestamp or token bucket is invented.
"""
from __future__ import annotations

import hashlib
import json
import shlex
from typing import Any, Mapping

UNOBSERVED = ("attribution.model_config", "attribution.usage", "attribution.token_semantics", "attribution.reconciliation")


def _json(data: bytes, label: str) -> Any:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"{label}: duplicate JSON key {key}")
            out[key] = value
        return out
    def constant(value):
        raise ValueError(f"{label}: nonfinite JSON {value}")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _records(data: bytes, label: str) -> list[dict]:
    rows = [_json(line, label) for line in data.splitlines()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}: expected nonblank object records")
    return rows


def _commands(command: str) -> list[list[str]]:
    """Accept explicit simple argv or one known shell -lc wrapper; never eval."""
    tokens = shlex.split(command)
    if len(tokens) == 3 and tokens[0] in {"/bin/zsh", "/bin/bash"} and tokens[1] == "-lc":
        lexer = shlex.shlex(tokens[2], posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        parts = list(lexer)
        commands, current = [], []
        for item in parts:
            if item == "&&":
                if not current:
                    raise ValueError("empty compound stdout command")
                commands.append(current)
                current = []
            elif item in {";", "|", "||", "&", "<", ">", ">>"}:
                return []  # Unmapped shell semantics cannot supply primary facts.
            else:
                current.append(item)
        if current:
            commands.append(current)
        return commands
    return [tokens]


def build_codex_stdout_observer(workload: Mapping[str, Any], *, stdout_by_turn: Mapping[int, bytes],
                                receipts_by_turn: Mapping[int, bytes], helper_ledger: bytes,
                                checkout_before: bytes, checkout_after: bytes,
                                capture_receipt: bytes, helper_source: bytes,
                                frozen_helper_source: bytes) -> dict:
    """Build exact observer populations solely from independently retained inputs."""
    if set(stdout_by_turn) != {1, 2} or set(receipts_by_turn) != {1, 2}:
        raise ValueError("exact two stdout streams and receipts required")
    capture = _json(capture_receipt, "capture receipt")
    if (capture.get("schema_version") != "1.0-codex-cli-calibration" or
            capture.get("configuration_id") != "codex-cli" or capture.get("run_id") != workload.get("run_id") or
            capture.get("helper_ledger_sha256") != _sha(helper_ledger) or
            capture.get("checkout_before_sha256") != _sha(checkout_before) or
            capture.get("checkout_after_sha256") != _sha(checkout_after) or
            capture.get("turn_returncodes") != [0, 0]):
        raise ValueError("independent helper/filesystem inputs differ from captured receipt")
    if helper_source != frozen_helper_source:
        raise ValueError("retained protected helper differs from frozen synthetic helper")
    helpers = _records(helper_ledger, "helper ledger")
    if [row.get("phase") for row in helpers] != ["inspect", "baseline", "final"]:
        raise ValueError("helper ledger must contain exact inspect/baseline/final population")
    phase_rows = {}
    for row in helpers:
        phase = row["phase"]
        expected_hash = _sha(checkout_after if phase == "final" else checkout_before)
        if (row.get("schema_version") != "1.0-survival-helper-ledger" or row.get("run_canary") != workload["run_canary"] or
                row.get("argv") != ["python3", "bench_check.py", phase] or row.get("cwd") != "fixture_project" or
                row.get("checkout_sha256") != expected_hash or type(row.get("exit_code")) is not int or
                not isinstance(row.get("helper_nonce"), str) or not isinstance(row.get("output"), str)):
            raise ValueError("malformed independently observed helper facts")
        prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{row['helper_nonce']}"
        if not row["output"].startswith(prefix + " "):
            raise ValueError("helper nonce/output mismatch")
        phase_rows[phase] = row
    events, relations, session_ids, observed_phases = [], [], set(), set()
    edit_id = None
    edit_completed_line = None
    def event(identifier, kind, fields, *, primary=True, source="independent_stdout"):
        events.append({"id": identifier, "sequence": len(events) + 1,
                       "population_role": "primary_scored" if primary else "supporting", "kind": kind,
                       "session_id": "pending", "fields": fields, "metric_ids": [], "source": source})
    def relation(identifier, kind, from_id, to_id):
        relations.append({"id": identifier, "kind": kind, "from_id": from_id, "to_id": to_id,
                          "sequence": len(relations) + 1})
    for number, turn in enumerate(workload["turns"], 1):
        raw = stdout_by_turn[number]
        receipt = _json(receipts_by_turn[number], "stdout receipt")
        if (receipt.get("sha256") != _sha(raw) or receipt.get("size_bytes") != len(raw) or
                receipt.get("method") != "runner-stdout" or receipt.get("independent") is not True or receipt.get("frozen") is not True):
            raise ValueError("stdout receipt does not bind exact independently frozen stream")
        rows = _records(raw, "stdout")
        if receipt.get("event_count") != len(rows):
            raise ValueError("stdout event count mismatch")
        if rows[0].get("type") != "thread.started" or rows[1].get("type") != "turn.started" or rows[-1].get("type") != "turn.completed":
            raise ValueError("stdout lacks exact completed turn boundaries")
        if sum(row.get("type") == "turn.completed" for row in rows) != 1 or sum(row.get("type") == "thread.started" for row in rows) != 1:
            raise ValueError("duplicate stdout turn/session boundary")
        session_ids.add(rows[0].get("thread_id"))
        event(turn["id"], "user_turn", {"turn_id": turn["id"], "revision": turn["revision"], "role": "user",
                                            "text": turn["text"], "run_canary": workload["run_canary"]}, source="submitted_input")
        started, started_lines, completed = {}, {}, set()
        finals = []
        for line_number, row in enumerate(rows[2:-1], 3):
            if row.get("type") not in {"item.started", "item.completed"} or not isinstance(row.get("item"), dict):
                raise ValueError("unsupported stdout event shape")
            item = row["item"]
            identifier, kind = item.get("id"), item.get("type")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("missing stdout item identity")
            if row["type"] == "item.started":
                if identifier in started or identifier in completed:
                    raise ValueError("duplicate stdout item start")
                started[identifier] = item
                started_lines[identifier] = line_number
                continue
            if identifier in completed:
                raise ValueError("duplicate stdout completion")
            completed.add(identifier)
            if kind == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise ValueError("malformed stdout assistant message")
                if turn["response_canary"] in item["text"]:
                    finals.append(item)
                continue
            initial = started.pop(identifier, None)
            started_line = started_lines.pop(identifier, None)
            if initial is None or initial.get("type") != kind or initial.get("status") != "in_progress":
                raise ValueError("stdout completion lacks matching started item")
            locator = {"stream_sha256": _sha(raw), "line": line_number, "item_id": identifier}
            if kind == "command_execution":
                if (not isinstance(item.get("command"), str) or initial.get("command") != item["command"] or
                        not isinstance(item.get("aggregated_output"), str) or type(item.get("exit_code")) is not int or
                        item.get("status") != ("completed" if item["exit_code"] == 0 else "failed")):
                    raise ValueError("malformed completed stdout command")
                for argv in _commands(item["command"]):
                    if len(argv) != 5 or argv[:2] != ["python3", "bench_check.py"] or argv[2] not in phase_rows or argv[3:] != ["--run-canary", workload["run_canary"]]:
                        continue  # Exploration stays outside required workload population.
                    phase = argv[2]
                    if phase in observed_phases or (number == 1) != (phase != "final"):
                        raise ValueError("duplicate or wrong-turn primary helper invocation")
                    if phase == "baseline" and "inspect" not in observed_phases:
                        raise ValueError("stdout baseline must follow inspect completion")
                    if phase == "final" and (edit_completed_line is None or started_line <= edit_completed_line):
                        raise ValueError("stdout final check must start after edit completion")
                    helper = phase_rows[phase]
                    if helper["output"] not in item["aggregated_output"]:
                        raise ValueError("stdout does not contain exact independent helper result")
                    observed_phases.add(phase)
                    action_id, result_id = f"action-{phase}", f"result-{phase}"
                    event(action_id, "action", {"turn_id": turn["id"], "action_kind": "inspect" if phase == "inspect" else "test",
                                                "argv": helper["argv"], "cwd": helper["cwd"], "stdout_locator": locator})
                    event(result_id, "result", {"action_id": action_id, "turn_id": turn["id"], "exit_code": helper["exit_code"],
                                                "status": "completed" if helper["exit_code"] == 0 else "failed",
                                                "helper_nonce": helper["helper_nonce"], "stdout_locator": locator})
                    relation(f"relation-{phase}", "action_result", action_id, result_id)
            elif kind == "file_change":
                changes = item.get("changes")
                if (number != 2 or edit_id is not None or item.get("status") != "completed" or
                        initial.get("changes") != changes or not isinstance(changes, list) or len(changes) != 1 or
                        changes[0].get("kind") != "update" or not isinstance(changes[0].get("path"), str) or
                        not changes[0]["path"].endswith("/fixture_project/checkout.py")):
                    raise ValueError("unsupported/ambiguous stdout file change")
                edit_id = "action-edit"
                edit_completed_line = line_number
                target = "fixture_project/checkout.py"
                event(edit_id, "action", {"turn_id": turn["id"], "action_kind": "edit", "target": target, "stdout_locator": locator})
                event("result-edit", "result", {"action_id": edit_id, "turn_id": turn["id"], "status": "completed", "stdout_locator": locator})
                relation("relation-edit", "action_result", edit_id, "result-edit")
                event("change-checkout", "file_change", {"path": target, "before_sha256": _sha(checkout_before),
                                                         "after_sha256": _sha(checkout_after), "action_id": edit_id}, source="independent_filesystem_snapshot")
            else:
                raise ValueError("unmapped stdout item family")
        if started or len(finals) != 1 or rows[-2].get("item") != finals[0]:
            raise ValueError("stdout final response is not a unique completed turn ending")
        event(f"response-r{number}", "assistant_response", {"turn_id": turn["id"], "role": "assistant", "status": "completed",
                                                          "canary": turn["response_canary"], "text": finals[0]["text"],
                                                          "unscored_turn_usage_trace": rows[-1].get("usage")})
        relation(f"relation-response-{number}", "turn_response", turn["id"], f"response-r{number}")
    if observed_phases != set(phase_rows) or edit_id is None or len(session_ids) != 1 or not all(isinstance(x, str) and x for x in session_ids) or capture.get("thread_id") != next(iter(session_ids)):
        raise ValueError("stdout/helper session or required workload population incomplete")
    for event_row in events:
        event_row["session_id"] = next(iter(session_ids))
    for phase, helper in phase_rows.items():
        event(f"helper-{phase}", "helper", {"phase": phase, "helper_nonce": helper["helper_nonce"], "action_id": f"action-{phase}"},
              primary=False, source="independent_helper_ledger")
        # Supporting events are appended after setting the primary session IDs.
        events[-1]["session_id"] = next(iter(session_ids))
        relation(f"relation-helper-{phase}", "helper_for", f"helper-{phase}", f"action-{phase}")
    relation("relation-r1-r2", "supersedes", workload["turns"][0]["id"], workload["turns"][1]["id"])
    relation("relation-final-after-r2", "final_after", workload["turns"][1]["id"], "action-final")
    from .live_metric_comparator import _validate_observer
    document = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival", "scenario_id": "survival-v1-repair",
                "run_id": workload["run_id"], "independent": True, "events": events, "relations": relations}
    _validate_observer(document)
    return document
