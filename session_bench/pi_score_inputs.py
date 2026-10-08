"""Offline Pi diagnostics from caller-supplied capture bytes.

Observer reconstruction never reads native transcripts. Native projection accepts
only transcript bytes and never imports the observer's expectations or hashes.
These APIs establish neither a complete persistence root nor public eligibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Mapping

from .adapters.agent_session_native_decoder import decode_agent_native_bytes
from .live_metric_comparator import _validate_observer, compare_survival_run
from .pi_session_stats import validate_pi_session_stats_observation
from .v1_public_score import FORMAT_METRICS, validate_format_evidence, validate_format_profile
from .workload_instance import inspect_checkout_source

CAPTURE_SCHEMA = "session-bench-pi-survival-capture-v1"
UNOBSERVED = (
    "portable.complete_root", "portable.companions",
    "portable.isolated_decode", "portable.canonical_equality",
)
# None means unproved, not a measured failed assertion.
PORTABILITY = {"complete_root": None, "companions_present": None,
               "isolated_decode": None, "canonical_equality": None}
PRINT_UNOBSERVED = ("attribution.usage", "attribution.token_semantics", "work.actions", "work.results", "causal.action_result")
_PHASES = ("inspect", "baseline", "final")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label}: duplicate key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label}: invalid JSON") from error


def _get(documents, name):
    value = documents.get(name)
    if not isinstance(value, bytes):
        raise ValueError(f"missing copied Pi document: {name}")
    return value


def _object(documents, name):
    value = _json(_get(documents, name), name)
    if not isinstance(value, dict):
        raise ValueError(f"{name}: expected object")
    return value


def _check(condition, message):
    if not condition:
        raise ValueError(message)


def _independent_capture(documents):
    """Validate independent receipts without opening/decoding native bytes."""
    plan = _object(documents, "plan.json")
    result = _object(documents, "capture-result.json")
    workload = _object(documents, "workload-instance.json")
    capture_environment = plan.get("capture_environment")
    systems = {"Darwin": ("macOS", "darwin"), "Windows": ("Windows", "win32"),
               "Linux": ("Linux", "linux")}
    _check(isinstance(capture_environment, dict)
           and set(capture_environment) == {"schema_version", "os_name", "system", "python_platform"}
           and capture_environment.get("schema_version") == "session-bench-pi-capture-host-v1"
           and systems.get(capture_environment.get("system")) ==
               (capture_environment.get("os_name"), capture_environment.get("python_platform"))
           and result.get("capture_environment") == capture_environment,
           "Pi capture operating-system identity is missing or inconsistent")
    run_id, session_id = plan.get("attempt_id"), plan.get("session_id")
    _check(isinstance(run_id, str) and run_id and isinstance(session_id, str) and session_id,
           "missing Pi run/session identity")
    _check(plan.get("schema_version") == result.get("schema_version") == CAPTURE_SCHEMA
           and plan.get("configuration_id") == "pi"
           and result.get("attempt_id") == workload.get("run_id") == run_id
           and result.get("session_id") == session_id
           and result.get("status") == "captured_pending_qualification"
           and result.get("model_submissions") == 2, "Pi capture identity/completion mismatch")
    _check(type(plan.get("repetition")) is int and plan["repetition"] in (1, 2, 3), "invalid Pi repetition")
    _check(workload.get("run_canary") == "SB_SURVIVAL_V1_RUN_" + run_id, "Pi workload canary mismatch")
    _check(sha(_get(documents, "workload-template.json")) == plan.get("workload_sha256"),
           "Pi workload template digest mismatch")
    turns, receipts = workload.get("turns"), result.get("turns")
    _check(isinstance(turns, list) and len(turns) == 2 and isinstance(receipts, list)
           and len(receipts) == 2, "Pi requires two captured turns")
    protected = plan.get("protected_sha256")
    _check(isinstance(protected, dict) and "bench_check.py" in protected and "checkout.py" in protected,
           "missing frozen Pi workspace bindings")
    before = _get(documents, "workspaces/before/fixture_project/checkout.py")
    _check(sha(before) == protected["checkout.py"], "Pi initial checkout digest mismatch")
    ledgers, finals, launches = [], [], []
    for number, (turn, receipt) in enumerate(zip(turns, receipts), 1):
        prefix = f"turn-r{number}"
        launch = _object(documents, prefix + "/launch.json")
        exit_receipt = _object(documents, prefix + "/exit.json")
        prompt = _get(documents, f"observer/prompt-r{number}.txt")
        stdout, stderr = (_get(documents, prefix + "/" + name) for name in ("stdout.txt", "stderr.txt"))
        _check(turn.get("id") == f"turn-r{number}" and turn.get("revision") == f"r{number}"
               and isinstance(turn.get("text"), str) and prompt == turn["text"].encode(),
               "Pi submitted prompt mismatch")
        _check(launch == receipt.get("launch") and launch.get("session_id") == session_id
               and launch.get("provider") == plan.get("provider") == result.get("provider")
               and launch.get("model") == plan.get("model") == result.get("model")
               and isinstance(launch.get("model"), str) and launch["model"]
               and isinstance(launch.get("provider"), str) and launch["provider"]
               and launch.get("cwd") == plan.get("workspace")
               and launch.get("prompt_sha256") == sha(prompt), "Pi launch binding mismatch")
        argv = launch.get("argv")
        _check(isinstance(argv, list) and argv[-2:] == ["--", turn["text"]], "Pi launch prompt argument mismatch")
        session_dir = plan.get("session_dir")
        _check(isinstance(session_dir, str) and os.path.isabs(session_dir)
               and argv.count("--session-dir") == 1
               and argv[argv.index("--session-dir") + 1] == session_dir,
               "Pi launch does not bind the explicit session directory")
        for flag, value in (("--session-id", session_id), ("--provider", plan["provider"]),
                            ("--model", plan["model"])):
            _check(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv)
                   and argv[argv.index(flag) + 1] == value, "Pi launch identity argument mismatch")
        _check(receipt.get("turn") == number and receipt.get("status") == "completed"
               and type(receipt.get("returncode")) is int and receipt["returncode"] == 0
               and type(exit_receipt.get("returncode")) is int and exit_receipt["returncode"] == 0,
               "Pi unsuccessful launch")
        for name, raw in (("stdout", stdout), ("stderr", stderr)):
            _check(receipt.get(name + "_sha256") == exit_receipt.get(name + "_sha256") == sha(raw),
                   "Pi stream digest mismatch")
        if "--mode" in argv:
            _check(argv.count("--mode") == 1 and argv[argv.index("--mode")+1] == "json", "unsupported Pi stdout mode")
            stream = parse_pi_json_stdout(stdout, session_id=session_id, prompt=turn["text"],
                                          response_canary=turn["response_canary"], workspace=plan["workspace"])
            text = stream["final"]["text"]
        else:
            text = stdout.decode("utf-8")
            # --print adds one terminal newline; no other trimming is authorized.
            text = text[:-1] if text.endswith("\n") else text
        canary = turn.get("response_canary")
        _check(isinstance(canary, str) and canary.startswith("SB_SURVIVAL_V1_RESPONSE_")
               and text.endswith(canary) and text.count(canary) == 1, "Pi final stdout canary mismatch")
        finals.append(text)
        launches.append(launch)
        for name, digest in protected.items():
            _check(isinstance(name, str) and not name.startswith("/") and ".." not in name.split("/"),
                   "invalid protected workspace path")
            if name != "checkout.py":
                _check(sha(_get(documents, prefix + "/workspace/fixture_project/" + name)) == digest,
                       "Pi protected workspace changed")
        helper = _get(documents, prefix + "/workspace/fixture_project/bench_check.py")
        _check(sha(helper) == workload.get("helper", {}).get("sha256"), "Pi workload helper digest mismatch")
        ledger = _get(documents, prefix + "/workspace/fixture_project/.survival-observer.jsonl")
        rows = [_json(line, "Pi helper ledger") for line in ledger.splitlines() if line.strip()]
        _check(len(rows) == (2 if number == 1 else 3), "Pi missing/extra helper execution")
        for row, phase in zip(rows, _PHASES):
            nonce = workload["helper"]["nonces"].get(phase)
            _check(isinstance(nonce, str) and nonce and isinstance(row, dict)
                   and row.get("schema_version") == "1.0-survival-helper-ledger"
                   and row.get("phase") == phase and row.get("helper_nonce") == nonce
                   and row.get("id") == f"helper-{phase}-{nonce}"
                   and row.get("run_canary") == workload["run_canary"]
                   and row.get("argv") == ["python3", "bench_check.py", phase]
                   and row.get("cwd") == "fixture_project"
                   and type(row.get("exit_code")) is int
                   and row["exit_code"] == (1 if phase == "baseline" else 0), "Pi helper identity/outcome mismatch")
            marker = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} "
            _check(isinstance(row.get("output"), str) and row["output"].startswith(marker), "Pi helper output marker mismatch")
            payload = _json(row["output"][len(marker):].encode(), "Pi helper output")
            _check(isinstance(payload, dict) and payload.get("phase") == phase, "Pi helper output phase mismatch")
            if phase == "final":
                cases = payload.get("tests")
                _check(isinstance(cases, list) and len(cases) == 3
                       and all(isinstance(case, dict) and case.get("passed") is True
                               and case.get("actual") == case.get("expected") for case in cases),
                       "Pi final helper did not pass all declared cases")
        ledgers.append(rows)
    _check(ledgers[0] == ledgers[1][:2], "Pi helper prefix changed across turns")
    r1 = _get(documents, "turn-r1/workspace/fixture_project/checkout.py")
    after = _get(documents, "turn-r2/workspace/fixture_project/checkout.py")
    _check(r1 == before and r1 != after, "Pi checkout revision boundary mismatch")
    for row in ledgers[1]:
        _check(row.get("checkout_sha256") == sha(after if row["phase"] == "final" else before),
               "Pi helper does not bind actual captured checkout bytes")
    return plan, workload, ledgers[1], finals, launches, before, after


def _validate_pi_root_capture(documents, *, plan, workload, result, native):
    """Verify the full before/after inventory for a run-owned Pi root."""
    root_names = {name for name in documents if name.startswith("native-root/")}
    if not root_names:
        return None
    root_path = "native-root/root-capture.json"
    _check(root_path in documents, "Pi root snapshot is missing its capture receipt")
    receipt = _object(documents, root_path)
    expected_receipt_keys = {"schema_version", "attempt_id", "session_id", "session_root",
                             "discovery_mode", "pre_run_empty", "personal_history_scanned",
                             "required_companions", "snapshots"}
    _check(set(receipt) == expected_receipt_keys
           and receipt.get("schema_version") == "session-bench-pi-native-root-capture-v1"
           and receipt.get("attempt_id") == workload["run_id"]
           and receipt.get("session_id") == plan["session_id"]
           and receipt.get("session_root") == plan.get("session_dir")
           and receipt.get("discovery_mode") == "isolated"
           and receipt.get("pre_run_empty") is True
           and receipt.get("personal_history_scanned") is False
           and receipt.get("required_companions") == [],
           "Pi root capture receipt does not bind an empty, isolated session root")
    snapshots = receipt.get("snapshots")
    phases = ("before-r1", "after-r1", "after-r2")
    _check(isinstance(snapshots, list) and len(snapshots) == len(phases),
           "Pi root capture lacks its three ordered inventory snapshots")
    expected_documents = {root_path}
    entries_by_phase = {}
    for item, phase in zip(snapshots, phases, strict=True):
        _check(isinstance(item, dict) and set(item) == {"phase", "inventory_path", "inventory_sha256"}
               and item["phase"] == phase, "Pi root snapshot order or shape is invalid")
        inventory_path = f"native-root/{phase}/inventory.json"
        _check(item["inventory_path"] == inventory_path and inventory_path in documents
               and item["inventory_sha256"] == sha(documents[inventory_path]),
               "Pi root inventory digest or path mismatch")
        expected_documents.add(inventory_path)
        inventory = _object(documents, inventory_path)
        _check(set(inventory) == {"schema_version", "attempt_id", "session_id", "phase",
                                  "source_root", "captured_at_ns", "entries"}
               and inventory.get("schema_version") == "session-bench-pi-native-root-snapshot-v1"
               and inventory.get("attempt_id") == workload["run_id"]
               and inventory.get("session_id") == plan["session_id"]
               and inventory.get("phase") == phase
               and inventory.get("source_root") == plan.get("session_dir")
               and type(inventory.get("captured_at_ns")) is int
               and inventory["captured_at_ns"] > 0,
               "Pi root inventory identity or capture time is invalid")
        entries = inventory.get("entries")
        _check(isinstance(entries, list), "Pi root inventory entries are malformed")
        entry_paths = []
        for entry in entries:
            _check(isinstance(entry, dict)
                   and set(entry) == {"path", "filesystem_id", "size_bytes", "sha256"},
                   "Pi root file inventory entry is malformed")
            relative = entry["path"]
            parsed = PurePosixPath(relative) if isinstance(relative, str) else None
            _check(parsed is not None and relative and "\\" not in relative
                   and not parsed.is_absolute() and ".." not in parsed.parts
                   and parsed.as_posix() == relative
                   and isinstance(entry["filesystem_id"], str) and bool(entry["filesystem_id"])
                   and type(entry["size_bytes"]) is int and entry["size_bytes"] >= 0
                   and isinstance(entry["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]),
                   "Pi root file inventory contains an unsafe path or invalid digest")
            copy_path = f"native-root/{phase}/files/{relative}"
            raw = documents.get(copy_path)
            _check(isinstance(raw, bytes) and len(raw) == entry["size_bytes"]
                   and sha(raw) == entry["sha256"],
                   "Pi root file copy does not match its full inventory")
            expected_documents.add(copy_path)
            entry_paths.append(relative)
        _check(entry_paths == sorted(set(entry_paths)),
               "Pi root file inventory paths are duplicated or unordered")
        entries_by_phase[phase] = entries
    _check(root_names == expected_documents, "Pi root copy contains omitted or unindexed files")
    before, after_r1, after_r2 = (entries_by_phase[phase] for phase in phases)
    _check(not before and len(after_r1) == len(after_r2) == 1
           and after_r1[0]["path"] == after_r2[0]["path"]
           and after_r1[0]["path"].endswith(".jsonl")
           and after_r1[0]["filesystem_id"] == after_r2[0]["filesystem_id"],
           "Pi isolated root contains an unqualified file family")
    r1_native = _get(documents, "turn-r1/native/session.jsonl")
    r2_native = _get(documents, "turn-r2/native/session.jsonl")
    for phase, expected in (("after-r1", r1_native), ("after-r2", r2_native)):
        row = entries_by_phase[phase][0]
        copy_path = f"native-root/{phase}/files/{row['path']}"
        _check(documents[copy_path] == expected and row["sha256"] == sha(expected),
               "Pi full-root snapshot differs from the selected session bytes")
    native_receipt = _object(documents, "turn-r2/native/receipt.json")
    _check(native_receipt.get("relative_path") == after_r2[0]["path"],
           "Pi selected session path differs from the complete root inventory")
    integrity = result.get("evidence_integrity", {}).get("native_root", {})
    _check(integrity.get("complete") is True and integrity.get("root_files") == 1
           and integrity.get("root_capture_sha256") == sha(documents[root_path]),
           "Pi controller did not confirm the copied complete-root inventory")
    return {"complete_root": True, "complete_record_family": True,
            "required_companions": [],
            "root_locator": "inputs/capture/plan.json:session_dir passed explicitly as Pi --session-dir",
            "discovery_mode": "isolated", "personal_history_scanned": False}


def qualify_capture_documents(documents):
    """Bind retained native copies and independent documents; never upgrade root."""
    plan, workload, *_ = _independent_capture(documents)
    capture_environment = plan["capture_environment"]
    result = _object(documents, "capture-result.json")
    native = []
    for number, turn in enumerate(result["turns"], 1):
        raw = _get(documents, f"turn-r{number}/native/session.jsonl")
        receipt = _object(documents, f"turn-r{number}/native/receipt.json")
        _check(sha(raw) == turn.get("native_sha256") == receipt.get("sha256")
               and len(raw) == receipt.get("size_bytes"), "Pi native digest mismatch")
        decoded = decode_agent_native_bytes("pi", raw)
        _check(decoded.session_id == plan["session_id"] and decoded.status == "complete",
               "Pi native identity/decoder mismatch")
        native.append(raw)
    _check(native[1].startswith(native[0]) and len(native[1]) > len(native[0]), "Pi native R1 prefix changed")
    if "observer/pi-session-stats.json" in documents:
        stats = _object(documents, "observer/pi-session-stats.json")
        _check("observer/pi-session-stats.stdout.jsonl" in documents
               and "observer/pi-session-stats.stderr.txt" in documents,
               "Pi session-stats raw RPC streams are missing")
        native_receipt = _object(documents, "turn-r2/native/receipt.json")
        filename = native_receipt.get("relative_path", "").rsplit("/", 1)[-1]
        validate_pi_session_stats_observation(
            stats, stdout=_get(documents, "observer/pi-session-stats.stdout.jsonl"),
            stderr=_get(documents, "observer/pi-session-stats.stderr.txt"),
            session_bytes=native[1], session_filename=filename,
            attempt_id=workload["run_id"],
        )
    elif any(name.startswith("observer/pi-session-stats.") for name in documents):
        raise ValueError("Pi session-stats streams lack their normalized receipt")
    root_proof = _validate_pi_root_capture(documents, plan=plan, workload=workload,
                                           result=result, native=native)
    return {"run_id": workload["run_id"], "session_id": plan["session_id"],
            "repetition": plan["repetition"], "workload": workload,
            "capture_environment": capture_environment,
            "complete_record_family": bool(root_proof), "complete_root": bool(root_proof),
            "root_proof": root_proof,
            "native_sha256": sha(native[1]), "score_eligible": False,
            "document_sha256": {name: sha(raw) for name, raw in sorted(documents.items())}}


def observer_from_capture_documents(documents):
    """Reconstruct independent observer events; native documents are ignored."""
    plan, workload, ledger, finals, launches, before, after = _independent_capture(documents)
    if all("--mode" in launch["argv"] for launch in launches):
        return _json_observer(documents, plan, workload, before, after)
    _check(all("--mode" not in launch["argv"] for launch in launches), "mixed Pi stdout modes")
    events, relations = [], []
    def event(kind, identifier, fields, source):
        events.append({"id": identifier, "sequence": len(events) + 1,
                       "population_role": "primary_scored", "kind": kind,
                       "session_id": plan["session_id"], "fields": fields,
                       "metric_ids": [], "source": source})
    def relation(kind, source, target):
        relations.append({"id": f"relation-{len(relations)+1}", "sequence": len(relations)+1,
                          "kind": kind, "from_id": source, "to_id": target})
    for number, turn in enumerate(workload["turns"], 1):
        event("user_turn", turn["id"], {"turn_id": turn["id"], "revision": turn["revision"],
              "role": "user", "text": turn["text"], "run_canary": workload["run_canary"]}, "copied_submitted_prompt")
        for row in ledger[:2] if number == 1 else ledger[2:]:
            phase = row["phase"]
            event("action", "action-"+phase, {"turn_id": turn["id"], "argv": row["argv"],
                  "cwd": row["cwd"], "action_kind": "inspect" if phase == "inspect" else "test"}, "copied_helper_ledger")
            event("result", "result-"+phase, {"action_id": "action-"+phase,
                  "helper_nonce": row["helper_nonce"], "output": row["output"], "exit_code": row["exit_code"],
                  "status": "success" if row["exit_code"] == 0 else "failure"}, "copied_helper_ledger")
            relation("action_result", "action-"+phase, "result-"+phase)
        if number == 2:
            event("file_change", "change-checkout", {"path": "fixture_project/checkout.py",
                  "before_sha256": sha(before), "after_sha256": sha(after)}, "copied_workspace_bytes")
        event("assistant_response", f"response-r{number}", {"turn_id": turn["id"], "role": "assistant",
              "status": "completed", "text": finals[number-1], "canary": turn["response_canary"],
              "model_id": launches[number-1]["model"], "configuration": {"provider": launches[number-1]["provider"]}},
              "copied_stdout_and_launch")
        relation("turn_response", turn["id"], f"response-r{number}")
    relation("supersedes", "turn-r1", "turn-r2")
    relation("final_after", "turn-r2", "action-final")
    value = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
             "scenario_id": "survival-v1-repair", "run_id": workload["run_id"], "independent": True,
             "events": events, "relations": relations}
    _validate_observer(value)
    return value


def _message_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block["text"] for block in content
                       if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
    return ""


def project_pi_native(raw: bytes, *, before_checkout: bytes | None = None,
                      after_checkout: bytes | None = None, workspace: str | None = None):
    """Project only native facts, with user-root ancestry and distinct call IDs.

    Compound shell calls remain single native actions. No helper subcalls,
    whole-file hashes, successful shell exit codes, or provider token truth are invented.
    """
    decoded = decode_agent_native_bytes("pi", raw)
    _check(decoded.status == "complete" and decoded.version == 3, "unsupported Pi native transcript")
    rows = [_json(line, "Pi native row") for line in raw.splitlines() if line.strip()]
    roots, nodes, calls = {}, {}, {}
    turns, responses, actions, results, relations = [], [], [], [], []
    for sequence, row in enumerate(rows[1:], 2):
        identifier, parent = row.get("id"), row.get("parentId")
        _check(isinstance(identifier, str) and identifier and identifier not in nodes, "Pi duplicate/missing native row ID")
        _check(parent is None or isinstance(parent, str) and parent in nodes, "Pi dangling/forward native parent")
        message = row.get("message", {}) if row.get("type") == "message" else {}
        role = message.get("role")
        root = identifier if role == "user" else roots.get(parent)
        nodes[identifier], roots[identifier] = row, root
        text = _message_text(message)
        if role == "user":
            revision_match = re.search(r"\b(?:Requirement|Correction) R([12])\b", text)
            turns.append({"id": identifier, "turn_id": identifier, "sequence": sequence,
                          "role": "user", "text": text,
                          "revision": "r"+revision_match.group(1) if revision_match else None})
        content = message.get("content", [])
        blocks = content if isinstance(content, list) else []
        tool_blocks = [block for block in blocks if isinstance(block, dict) and block.get("type") == "toolCall"]
        if role == "assistant" and text and message.get("stopReason") == "stop" and not tool_blocks:
            _check(root is not None, "Pi final response has no user ancestor")
            response = {"id": identifier, "turn_id": root, "sequence": sequence, "text": text,
                        "role": "assistant", "status": "completed", "finish": "stop"}
            if isinstance(message.get("model"), str) and isinstance(message.get("provider"), str):
                response.update(model_id=message["model"], configuration={"provider": message["provider"]})
            if isinstance(message.get("usage"), dict):
                response["usage"] = _usage(message["usage"])
            if isinstance(message.get("responseId"), str):
                response["provider_response_id"] = message["responseId"]
            responses.append(response)
            relations.append({"kind": "turn_response", "from_id": root, "to_id": identifier})
        for block in tool_blocks if role == "assistant" else []:
            call_id, name, arguments = block.get("id"), block.get("name"), block.get("arguments")
            _check(isinstance(call_id, str) and call_id and call_id not in calls and root is not None
                   and isinstance(arguments, dict), "Pi invalid/duplicate native call")
            action = {"id": call_id, "call_id": call_id, "turn_id": root, "sequence": sequence,
                      "name": name, "arguments": arguments, "native_message_id": identifier}
            if name == "bash" and isinstance(arguments.get("command"), str):
                action["argv"] = ["bash", "-c", arguments["command"]]
            if isinstance(arguments.get("path"), str):
                action["target"] = arguments["path"]
            calls[call_id] = action
            actions.append(action)
        if role == "toolResult":
            call_id = message.get("toolCallId")
            _check(call_id in calls and calls[call_id]["turn_id"] == root
                   and not any(item["call_id"] == call_id for item in results), "Pi unjoined/duplicate native result")
            result = {"id": call_id+":result", "call_id": call_id, "action_id": call_id,
                      "turn_id": root, "sequence": sequence, "output": text,
                      "native_message_id": identifier, "native_parent_id": parent}
            if type(message.get("isError")) is bool:
                result["status"] = "failure" if message["isError"] else "success"
            # isError is a tool status, not a numeric process exit receipt.
            results.append(result)
            relations.append({"kind": "action_result", "from_id": call_id, "to_id": result["id"]})

    # Native edit calls retain the complete replacement text, and the native
    # inspect result retains the complete pre-edit source with its own digest.
    # Both whole-file hashes come from those native records only. No captured
    # workspace snapshot enters the native fact.
    native_sources = {source for source in (inspect_checkout_source(item["output"]) for item in results) if source is not None}
    native_before = next(iter(native_sources)) if len(native_sources) == 1 else None
    file_changes = []
    if before_checkout is not None and after_checkout is not None and workspace is not None:
        _check(isinstance(before_checkout, bytes) and isinstance(after_checkout, bytes),
               "Pi changed-file snapshots must be bytes")
        _check(isinstance(workspace, str) and workspace and os.path.isabs(workspace),
               "Pi changed-file workspace must be absolute")
        r2 = next((turn for turn in turns if turn.get("revision") == "r2"), None)
        edits = [action for action in actions if action.get("turn_id") == (r2 or {}).get("id")
                 and action.get("name") == "edit"]
        if len(edits) == 1 and native_before is not None:
            action = edits[0]
            arguments = action["arguments"]
            target = arguments.get("path")
            expected_target = os.path.normpath(os.path.join(workspace, "fixture_project", "checkout.py"))
            _check(isinstance(target, str) and os.path.normpath(target) == expected_target,
                   "Pi native edit target is outside the frozen checkout")
            patch_rows = arguments.get("edits")
            _check(isinstance(patch_rows, list) and bool(patch_rows),
                   "Pi native edit lacks replacement text")
            try:
                current = native_before
                for patch in patch_rows:
                    _check(isinstance(patch, dict) and set(patch) >= {"oldText", "newText"}
                           and isinstance(patch["oldText"], str) and isinstance(patch["newText"], str),
                           "Pi native edit replacement is malformed")
                    _check(current.count(patch["oldText"]) == 1,
                           "Pi native edit does not identify one exact preimage")
                    current = current.replace(patch["oldText"], patch["newText"], 1)
                native_after = current.encode("utf-8")
            except UnicodeError as error:
                raise ValueError("Pi changed-file snapshots must be UTF-8") from error
            edit_result = next((item for item in results if item.get("call_id") == action["call_id"]), None)
            if edit_result is not None and edit_result.get("status") == "success":
                relative = os.path.relpath(target, workspace).replace(os.sep, "/")
                _check(relative == "fixture_project/checkout.py",
                       "Pi native edit did not resolve to the frozen project path")
                file_changes.append({"id": "change-checkout", "path": relative,
                                     "before_sha256": sha(native_before.encode("utf-8")),
                                     "after_sha256": sha(native_after),
                                     "sequence": action["sequence"], "turn_id": action["turn_id"]})

    # Prove the frozen final-after chain from native parent IDs and ordered
    # call/result/response records. Timestamps alone never establish it.
    r2 = next((turn for turn in turns if turn.get("revision") == "r2"), None)
    final_actions = [action for action in actions
                     if action.get("turn_id") == (r2 or {}).get("id")
                     and action.get("name") == "bash"
                     and re.search(r"\bbench_check\.py\s+final\b",
                                   action.get("arguments", {}).get("command", ""))]
    final_responses = [response for response in responses
                       if response.get("turn_id") == (r2 or {}).get("id")]
    if len(final_actions) == 1 and len(final_responses) == 1:
        final_action = final_actions[0]
        final_result = next((item for item in results
                             if item.get("call_id") == final_action["call_id"]), None)
        response_entry = nodes.get(final_responses[0]["id"])
        result_entry = next((nodes.get(item["native_message_id"]) for item in results
                             if item.get("call_id") == final_action["call_id"]), None)
        if (final_result is not None and final_result.get("status") == "success"
                and result_entry is not None
                and result_entry.get("parentId") == final_action["native_message_id"]
                and response_entry is not None
                and response_entry.get("parentId") == final_result["native_message_id"]
                and final_action["sequence"] < final_result["sequence"] < final_responses[0]["sequence"]):
            relations.append({"kind": "final_after", "from_id": r2["id"],
                              "to_id": final_action["call_id"]})

    # The Pi session file declares no session total.  An RPC stats receipt is
    # harness output, not native bytes, so it is not a reconciliation fact.
    return {"status": "ok", "format": decoded.format_id, "session_id": decoded.session_id,
            "version": decoded.version, "diagnostics": [], "boundary_complete": False,
            "unsupported_types": list(decoded.unsupported_types),
            "turns": turns, "responses": [{key: value for key, value in response.items() if key != "usage"}
                                           for response in responses], "actions": actions, "results": results,
            "file_changes": file_changes, "relations": relations,
            "usage": [{"id": response["id"]+":usage", "response_id": response["id"],
                       "turn_id": response["turn_id"], "usage": response["usage"]}
                      for response in responses if "usage" in response],
            "reconciliation": []}


def apply_unresolved_policy(measurement, *, json_stdout=False):
    """Copy a measurement and prevent unsupported Pi claims acquiring credit."""
    return {**measurement, "metrics": [dict(row, state="unresolved", correct=0)
            if not json_stdout and row["id"] in PRINT_UNOBSERVED else dict(row)
            for row in measurement["metrics"]]}


def compare_pi_capture(documents, *, native_raw=None):
    identity = qualify_capture_documents(documents)
    observer = observer_from_capture_documents(documents)
    session_bytes = _get(documents, "turn-r2/native/session.jsonl") if native_raw is None else native_raw
    before = _get(documents, "workspaces/before/fixture_project/checkout.py")
    after = _get(documents, "turn-r2/workspace/fixture_project/checkout.py")
    plan = _object(documents, "plan.json")
    projected = project_pi_native(session_bytes, before_checkout=before,
                                  after_checkout=after, workspace=plan.get("workspace"))
    _check(projected["session_id"] == identity["session_id"], "Pi projected session mismatch")
    json_stdout = all("--mode" in _object(documents, f"turn-r{n}/launch.json")["argv"] for n in (1, 2))
    if not json_stdout:
        # Print-only helper evidence has fewer than four observed actions;
        # do not pass a partially measured population below the protocol minimum.
        projected = {**projected, "actions": [], "results": [],
                     "relations": [r for r in projected["relations"] if r["kind"] != "action_result"]}
    measurement = compare_survival_run(observer, projected, PORTABILITY,
                                      configuration_id="pi", repetition=identity["repetition"])
    return apply_unresolved_policy(measurement, json_stdout=json_stdout)


def build_pi_format_evidence(projected, *, context, common):
    """Build broad Pi facts only from the pinned capture and bundle-local docs."""
    observer = _json(common["observer_document"], "Pi format observer")
    run_id, events, _ = _validate_observer(observer)
    _check(context["run_id"] == run_id and context["configuration_id"] == "pi", "Pi format identity mismatch")
    expected = [event for event in events if event["kind"] == "assistant_response" and event["population_role"] == "primary_scored"]
    records = []
    for event in expected:
        fields = event["fields"]
        matches = [response for response in projected["responses"] if response["text"] == fields.get("text")]
        if len(matches) == 1:
            records.append({"id": event["id"], "ordered_text": matches[0]["text"]})
    broad_complete = context.get("complete_root") is True
    native_package = common.get("native_package")
    native_bytes = (Path(native_package) / "session.jsonl").read_bytes() if native_package else b""
    native_rows = [_json(line, "Pi format record") for line in native_bytes.splitlines() if line.strip()]
    semantic_turns = []
    for ordinal, row in enumerate(native_rows[1:], 2):
        message = row.get("message") if row.get("type") == "message" else None
        role = message.get("role") if isinstance(message, dict) else None
        profile_role = {"user": "user", "assistant": "assistant", "toolResult": "tool"}.get(role)
        if profile_role is not None and isinstance(row.get("id"), str):
            semantic_turns.append({"id": row["id"], "role": profile_role,
                                   "ordinal": ordinal, "parent_id": row.get("parentId")})
    timestamp_ids, timestamp_records = [], []
    for row in native_rows:
        if isinstance(row.get("id"), str):
            timestamp_ids.append(row["id"])
            stamp = row.get("timestamp")
            if isinstance(stamp, str):
                timestamp_records.append({"id": row["id"], "timestamp": stamp,
                                          "unit": "rfc3339", "time_zone": "UTC"})

    documents = common.get("pi_format_documents", {})
    doc_index = doc_package = session_doc = message_doc = None
    documentation_valid = False
    documentation_reference = None
    if isinstance(documents, Mapping):
        try:
            doc_index = _json(documents["index.json"], "Pi documentation index")
            doc_package = _json(documents["package.json"], "Pi package metadata")
            session_doc = documents["docs/session-format.md"].decode("utf-8", "strict")
            message_doc = documents["docs/message-types.md"].decode("utf-8", "strict")
            doc_entries = doc_index["documents"]
            actual = {"package.json": documents["package.json"],
                      "docs/session-format.md": documents["docs/session-format.md"],
                      "docs/message-types.md": documents["docs/message-types.md"]}
            documentation_valid = (
                set(documents) == set(actual) | {"index.json"}
                and set(doc_index) == {"schema_version", "package_name", "pi_version", "documents"}
                and doc_index.get("schema_version") == "session-bench-pi-format-documents-v1"
                and doc_index.get("package_name") == "@earendil-works/pi-coding-agent"
                and doc_package.get("name") == doc_index["package_name"]
                and doc_package.get("version") == doc_index.get("pi_version")
                and doc_index.get("pi_version") == "1.0.0"
                and isinstance(doc_entries, list)
                and {item.get("path"): item.get("sha256") for item in doc_entries if isinstance(item, dict)}
                    == {name: sha(raw) for name, raw in actual.items()}
                and "Sessions are stored as JSONL" in session_doc
                and all(token in session_doc for token in ("Session Version", "Version 1", "Version 2", "Version 3", "parentId"))
                and all(token in message_doc for token in ("ToolCall", "ToolResultMessage", "Usage"))
            )
            if documentation_valid:
                documentation_reference = {"id": "inputs/pi-format/index.json",
                    "sha256": sha(documents["index.json"])}
        except (KeyError, UnicodeError, TypeError, ValueError, AttributeError):
            documentation_valid = False

    root_repetitions = context.get("root_repetitions")
    root_repetitions_valid = (isinstance(root_repetitions, list) and len(root_repetitions) == 3
        and {item.get("repetition") for item in root_repetitions if isinstance(item, Mapping)} == {1, 2, 3}
        and all(isinstance(item, Mapping) and item.get("isolated_discovery") is True
                and item.get("personal_history_scanned") is False
                and isinstance(item.get("root_locator"), str) and item["root_locator"].strip()
                for item in root_repetitions))

    density_evidence = {"evidence_complete": False,
                        "classification_rule": "logical-record-role-v1", "records": []}
    density_locators = []
    artifacts = common.get("native_artifacts")
    if native_package and isinstance(artifacts, list):
        from .native_density import inventory_native_jsonl_density
        density = inventory_native_jsonl_density(native_package, family="pi",
                    session_id=projected["session_id"], expected_artifacts=artifacts)
        if density.evidence["evidence_complete"] and broad_complete:
            density_evidence = density.evidence
            density_locators = list(density.native_locators)

    version_matches_docs = (documentation_valid and doc_index is not None
                            and doc_index.get("pi_version") == common.get("pi_version"))
    unsupported = projected.get("unsupported_types", [])
    decoded_cleanly = projected.get("status") == "ok" and not unsupported
    observations = []
    if broad_complete and root_repetitions_valid and version_matches_docs and decoded_cleanly:
        observations = [{"build": f"Pi {common['pi_version']} / native v{projected['version']}",
                         "observed_on": context["collected_on"],
                         "decoder_contract": "pi-session-jsonl-v1/native-v3",
                         "decoded": True}]

    event_ids = [row["id"] for row in native_rows[1:]
                 if isinstance(row.get("id"), str)]
    duplicate_records = [{"event_id": event_id, "occurrence_id": f"jsonl-entry-{ordinal}",
                          "state": "active"}
                         for ordinal, event_id in enumerate(event_ids, 1)]
    docs_mapping = {"containers": "docs/session-format.md: JSONL, one JSON object per line",
        "record_types": "docs/session-format.md: entry types; docs/message-types.md: AgentMessage roles and tool blocks",
        "identities": "session header id and entry id",
        "joins": "entry parentId; toolCall id to toolResult toolCallId",
        "version_semantics": "docs/session-format.md: native session versions 1, 2, and 3"}
    broad = {
        "broad.readable_rationale": {"evidence_complete": bool(records), "response_ids": [event["id"] for event in expected], "records": records},
        "broad.thread_structure": {"evidence_complete": broad_complete and bool(semantic_turns), "session_id": projected["session_id"], "turns": semantic_turns, "explicit_parentage": True},
        "broad.standard_tools_readable": {"evidence_complete": True, "container": "jsonl", "parser": "Python strict JSON on retained Pi v3 bytes", "vendor_binary_required": False, "account_required": False, "backend_required": False, "network_required": False},
        "broad.documented_format": {"evidence_complete": documentation_valid, "document_id": "inputs/pi-format/index.json" if documentation_valid else "", "mapping": docs_mapping if documentation_valid else {}},
        "broad.self_contained_identity": {"evidence_complete": bool(projected["session_id"] and common.get("native_manifest")), "session_id": projected["session_id"], "harness": "pi", "surface": "cli", "record_family": "pi-session-jsonl-v3", "external_lookup_required": False, "absolute_path_required": False},
        "broad.declared_format_version": {"evidence_complete": True, "format_version": str(projected["version"]), "machine_readable": True, "bundle_binding": common["native_manifest"]["id"]},
        "broad.event_timestamps": {"evidence_complete": broad_complete, "event_ids": timestamp_ids, "records": timestamp_records},
        "broad.honest_version_signal": {"evidence_complete": documentation_valid and decoded_cleanly, "declared_version": str(projected["version"]), "decoder_contract_version": "pi-session-jsonl-v1/native-v3", "incompatible_schema_distinguished": documentation_valid, "matches_decoder_contract": documentation_valid and projected["version"] == 3 and decoded_cleanly},
        "broad.observed_schema_stability": {"evidence_complete": bool(observations), "advertised_contract": "pi-session-jsonl-v1/native-v3" if observations else "", "observations": observations, "exceptions": []},
        "broad.stable_root_location": {"evidence_complete": broad_complete and root_repetitions_valid, "repetitions": root_repetitions if root_repetitions_valid else []},
        "broad.naive_reader_duplicate_safety": {"evidence_complete": broad_complete and bool(event_ids), "event_ids": event_ids, "forward_records": duplicate_records, "deduplication": {"documented": documentation_valid, "rule": "one forward JSONL record per unique entry id; parentId is retained as ancestry" if documentation_valid else ""}},
        "broad.classified_content_density": density_evidence,
    }
    profile = {"schema_version": "session-bench-format-profile-v1", "run_id": run_id,
               "configuration_id": "pi", "repetition": context["repetition"], "broad_evidence": broad}
    validate_format_profile(profile)
    root_locators = common.get("pi_root_evidence_locators", [])
    result = {"schema_version": "session-bench-format-evidence-v1", "run_id": run_id,
              "configuration_id": "pi", "repetition": context["repetition"], "build": context["build"],
              "collected_on": context["collected_on"], "result_id": context["result_id"],
              "observer": common["observer"], "native_manifest": common["native_manifest"], "profile": profile,
              "metric_evidence": [{"metric_id": metric,
                                   "observer_ids": (["pi-root-repetition-capture"]
                                                    if metric == "broad.stable_root_location" and root_locators
                                                    else [common["observer"]["id"]]),
                                   "native_locators": (root_locators if metric == "broad.stable_root_location" and root_locators
                                                       else [documentation_reference] if metric == "broad.documented_format" and documentation_reference
                                                       else density_locators if metric == "broad.classified_content_density" and density_locators
                                                       else [common["native_manifest"]])} for metric in FORMAT_METRICS]}
    validate_format_evidence(result)
    return result


def _usage(value):
    """Preserve named counters, without summing overlapping total/reasoning fields."""
    _check(isinstance(value, dict), "Pi usage must be an object")
    mapping = {"input": "input_tokens", "output": "output_tokens", "cacheRead": "cache_read_tokens",
               "cacheWrite": "cache_write_tokens", "reasoning": "reasoning_tokens"}
    result = {}
    for native, normalized in mapping.items():
        if native == "reasoning" and native not in value:
            continue
        count = value.get(native)
        _check(type(count) is int and count >= 0, "Pi usage counter missing/invalid")
        result[normalized] = count
    return result


def parse_pi_json_stdout(raw, *, session_id, prompt, response_canary, workspace):
    """Read completed message_end records only; updates and end summaries repeat data.

    This observes the harness event stream, not audited provider token truth.
    Final-response usage remains scoped to the visible response; a separate
    list preserves usage attached to every completed assistant API message so
    session totals can reconcile without omitting tool-use model steps.
    """
    _check(isinstance(raw, bytes) and len(raw) <= 16*1024*1024, "Pi stdout exceeds byte limit")
    rows = [_json(line, "Pi JSON stdout") for line in raw.splitlines() if line.strip()]
    _check(rows and isinstance(rows[0], dict) and rows[0].get("type") == "session"
           and rows[0].get("id") == session_id and rows[0].get("version") == 3
           and rows[0].get("cwd") == workspace, "Pi stdout session header mismatch")
    kinds = {"session", "agent_start", "agent_end", "agent_settled", "turn_start", "turn_end",
             "message_start", "message_update", "message_end", "tool_execution_start",
             "tool_execution_update", "tool_execution_end"}
    _check(all(isinstance(row, dict) and row.get("type") in kinds for row in rows), "unsupported Pi stdout event")
    _check(sum(row["type"] == "session" for row in rows) == 1
           and sum(row["type"] == "agent_start" for row in rows) == 1
           and sum(row["type"] == "agent_end" for row in rows) == 1
           and rows[-1]["type"] == "agent_settled", "Pi stdout lifecycle incomplete")
    observed, calls, result_ids, response_ids, finals, assistant_usage = [], {}, set(), set(), [], []
    started, ended = set(), {}
    user_seen = False
    for ordinal, row in enumerate(rows, 1):
        if row["type"] == "tool_execution_start":
            call_id = row.get("toolCallId")
            _check(user_seen and not finals and call_id in calls and call_id not in started,
                   "Pi unjoined/duplicate execution start")
            action = calls[call_id]
            _check(row.get("toolName") == action["name"] and row.get("args") == action["arguments"],
                   "Pi execution start differs from completed assistant call")
            started.add(call_id)
            observed.append(action)
            continue
        if row["type"] == "tool_execution_end":
            call_id = row.get("toolCallId")
            _check(call_id in started and call_id not in ended and not finals
                   and row.get("toolName") == calls[call_id]["name"]
                   and isinstance(row.get("result"), dict) and type(row.get("isError")) is bool,
                   "Pi unjoined/duplicate execution end")
            result = {"kind": "result", "call_id": call_id, "output": _message_text(row["result"]),
                      "status": "failure" if row["isError"] else "success", "ordinal": ordinal}
            ended[call_id] = result
            observed.append(result)
            continue
        if row["type"] != "message_end":
            continue
        message = row.get("message")
        _check(isinstance(message, dict), "invalid completed Pi stdout message")
        role, text = message.get("role"), _message_text(message)
        if role == "system":
            continue
        if role == "user":
            _check(not user_seen and text == prompt, "Pi stdout prompt mismatch/duplicate")
            user_seen = True
            continue
        _check(user_seen and not finals, "Pi completed message outside submitted turn")
        if role == "assistant":
            response_id = message.get("responseId")
            _check(isinstance(response_id, str) and response_id and response_id not in response_ids,
                   "Pi missing/duplicate completed assistant response ID")
            response_ids.add(response_id)
            _check(message.get("stopReason") in {"toolUse", "stop"}, "Pi incomplete/failed assistant message")
            if "usage" in message:
                assistant_usage.append({"response_id": response_id, "usage": _usage(message["usage"])})
            content = message.get("content", [])
            _check(isinstance(content, list), "Pi assistant content must be blocks")
            blocks = [block for block in content if isinstance(block, dict) and block.get("type") == "toolCall"]
            if message["stopReason"] == "toolUse":
                _check(bool(blocks), "Pi tool completion lacks calls")
                for block in blocks:
                    call_id, arguments = block.get("id"), block.get("arguments")
                    _check(isinstance(call_id, str) and call_id and call_id not in calls
                           and isinstance(arguments, dict), "Pi duplicate/invalid observed call")
                    action = {"kind": "action", "call_id": call_id, "name": block.get("name"),
                              "arguments": arguments, "ordinal": ordinal}
                    calls[call_id] = action
            else:
                _check(not blocks and text.endswith(response_canary) and text.count(response_canary) == 1,
                       "Pi stdout final canary mismatch")
                _check(isinstance(message.get("provider"), str) and isinstance(message.get("model"), str),
                       "Pi final model provenance missing")
                final = {"text": text, "provider_response_id": response_id, "model_id": message["model"],
                         "configuration": {"provider": message["provider"]}, "ordinal": ordinal}
                if "usage" in message:
                    final["usage"] = _usage(message["usage"])
                finals.append(final)
        elif role == "toolResult":
            call_id = message.get("toolCallId")
            _check(call_id in calls and call_id not in result_ids and type(message.get("isError")) is bool,
                   "Pi unjoined/duplicate observed tool result")
            _check(message.get("toolName") == calls[call_id]["name"], "Pi observed tool result name mismatch")
            _check(call_id in ended and ended[call_id]["output"] == text
                   and ended[call_id]["status"] == ("failure" if message["isError"] else "success"),
                   "Pi completed tool result differs from execution receipt")
            result_ids.add(call_id)
        else:
            raise ValueError("unsupported completed Pi stdout role")
    _check(user_seen and len(finals) == 1 and result_ids == set(calls) == started == set(ended), "Pi stdout missing final/call result")
    return {"final": finals[0], "work": observed, "assistant_usage": assistant_usage}


def _json_observer(documents, plan, workload, before, after):
    events, relations = [], []
    def event(kind, identifier, fields):
        events.append({"id": identifier, "sequence": len(events)+1, "population_role": "primary_scored",
                       "kind": kind, "session_id": plan["session_id"], "fields": fields,
                       "metric_ids": [], "source": "independent_json_stdout"})
    def relation(kind, source, target):
        relations.append({"id": f"relation-{len(relations)+1}", "sequence": len(relations)+1,
                          "kind": kind, "from_id": source, "to_id": target})
    all_calls, all_response_ids, all_usage = set(), set(), []
    for number, turn in enumerate(workload["turns"], 1):
        stream = parse_pi_json_stdout(_get(documents, f"turn-r{number}/stdout.txt"),
                    session_id=plan["session_id"], prompt=turn["text"],
                    response_canary=turn["response_canary"], workspace=plan["workspace"])
        final = stream["final"]
        _check(final["model_id"] == plan["model"] and final["configuration"]["provider"] == plan["provider"],
               "Pi stdout model differs from pinned launch")
        event("user_turn", turn["id"], {"turn_id": turn["id"], "revision": turn["revision"],
              "role": "user", "text": turn["text"], "run_canary": workload["run_canary"]})
        for item in stream["assistant_usage"]:
            _check(item["response_id"] not in all_response_ids,
                   "Pi assistant response ID reused across observed turns")
            all_response_ids.add(item["response_id"])
            all_usage.append(item["usage"])
        for item in stream["work"]:
            call_id = item["call_id"]
            if item["kind"] == "action":
                _check(call_id not in all_calls, "Pi call ID reused across observed turns")
                all_calls.add(call_id)
                fields = {"turn_id": turn["id"], "call_id": call_id, "native_action_id": call_id}
                arguments = item["arguments"]
                if item["name"] == "bash" and isinstance(arguments.get("command"), str):
                    fields["argv"] = ["bash", "-c", arguments["command"]]
                if isinstance(arguments.get("path"), str):
                    fields["target"] = arguments["path"]
                event("action", "action-"+call_id, fields)
                if number == 2 and item["name"] == "bash" and re.search(r"\bbench_check\.py\s+final\b", arguments.get("command", "")):
                    relation("final_after", turn["id"], "action-"+call_id)
            else:
                event("result", "result-"+call_id, {"action_id": "action-"+call_id, "call_id": call_id,
                      "native_result_id": call_id+":result", "output": item["output"], "status": item["status"]})
                relation("action_result", "action-"+call_id, "result-"+call_id)
        if number == 2:
            event("file_change", "change-checkout", {"path": "fixture_project/checkout.py",
                  "before_sha256": sha(before), "after_sha256": sha(after)})
            events[-1]["source"] = "copied_workspace_bytes"
        fields = {key: value for key, value in final.items() if key != "ordinal"}
        fields.update(turn_id=turn["id"], role="assistant", status="completed", canary=turn["response_canary"])
        event("assistant_response", f"response-r{number}", fields)
        relation("turn_response", turn["id"], f"response-r{number}")
    relation("supersedes", "turn-r1", "turn-r2")
    if all_usage:
        buckets = {"input_tokens": "input_tokens", "output_tokens": "output_tokens",
                   "cache_read_tokens": "cache_read_tokens", "cache_write_tokens": "cache_write_tokens"}
        if all(type(row.get(key)) is int and row[key] >= 0
               for row in all_usage for key in buckets.values()):
            events.append({"id": "usage-total", "sequence": len(events)+1,
                           "population_role": "supporting", "kind": "usage_total",
                           "session_id": plan["session_id"],
                           "fields": {name: sum(row[source] for row in all_usage)
                                      for name, source in buckets.items()},
                           "metric_ids": ["attribution.reconciliation"],
                           "source": "independent_json_stdout_assistant_message_usage"})
    value = {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
             "scenario_id": "survival-v1-repair", "run_id": workload["run_id"], "independent": True,
             "events": events, "relations": relations}
    _validate_observer(value)
    return value
