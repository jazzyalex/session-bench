#!/usr/bin/env python3
"""Qualify a completed two-turn Hermes capture without another model call.

This verifier is separate from the retained continuation verifier so its source
hash and the September qualification-v2 packet remain unchanged. A successful
receipt qualifies acquisition only; the exact-session JSON export is not a
complete copy of Hermes' shared SQLite root or a public score replay.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.qualify_hermes_capture import (
    EXPECTED_CODES, MODEL, PHASES, PROVIDER, ROOT, QualificationError,
    file_hash, load, require_turn_response_canaries, sha, tree_inventory,
    verify_ledger,
)


SCHEMA = "session-bench-hermes-complete-capture-qualification-v2"
CAPTURE_ID = "hermes-codex-2026-10-02-02"
FIXTURE_FILES = {
    "checkout.py", "bench_check.py", "snapshots/checkout.before.py",
    "snapshots/checkout.after.py",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationError(message)


def _argv_value(argv: list[str], flag: str) -> str:
    if argv.count(flag) != 1:
        raise QualificationError(f"missing or duplicated {flag}")
    index = argv.index(flag)
    if index + 1 >= len(argv):
        raise QualificationError(f"{flag} has no value")
    return argv[index + 1]


def verify_complete_capture(capture: Path) -> dict:
    if capture.is_symlink() or not capture.is_dir() or capture.name != CAPTURE_ID:
        raise QualificationError("only the retained ordinary Hermes repetition-2 capture is accepted")
    capture = capture.resolve()
    plan = load(capture / "plan.json")
    workload = load(capture / "workload-instance.json")
    result = load(capture / "capture-result.json")
    state = load(capture / "controller-state.json")
    preflight = load(capture / "preflight.json")
    _require(plan.get("schema_version") == "session-bench-hermes-survival-capture-v1"
             and plan.get("attempt_id") == CAPTURE_ID and plan.get("repetition") == 2
             and plan.get("status") == "prepared" and plan.get("model_submissions") == 0
             and plan.get("configuration_id") == "hermes" and plan.get("provider") == PROVIDER
             and plan.get("model") == MODEL and plan.get("no_session_list") is True
             and plan.get("no_unrelated_session_reads") is True,
             "plan identity, repetition, or route changed")
    _require(sha((capture / "workload-template.json").read_bytes()) == plan.get("workload_sha256")
             and workload.get("run_canary") == f"SB_SURVIVAL_V1_RUN_{CAPTURE_ID}"
             and isinstance(workload.get("turns"), list) and len(workload["turns"]) == 2,
             "workload bytes or run identity changed")
    _require(result.get("schema_version") == plan["schema_version"]
             and result.get("attempt_id") == CAPTURE_ID
             and result.get("status") == "captured_pending_qualification"
             and result.get("model_submissions") == 2
             and result.get("provider") == PROVIDER and result.get("model") == MODEL
             and result.get("independent_reproduction") is False
             and result.get("score_eligible") is False
             and result.get("normal_home_only") is True
             and result.get("no_unrelated_session_reads") is True
             and isinstance(result.get("session_id"), str)
             and result["session_id"]
             and isinstance(result.get("turns"), list) and len(result["turns"]) == 2,
             "controller result does not prove two completed bounded turns")
    _require(state.get("attempt_id") == CAPTURE_ID and state.get("model_submissions") == 2
             and state.get("session_id") == result["session_id"]
             and state.get("turns") == result["turns"],
             "controller state differs from final result")
    version = preflight.get("version", "").splitlines()[0]
    _require(preflight.get("ready") is True and preflight.get("model_submission") is False
             and version.startswith("Hermes Agent v0.21.5") and ".dirty" in version,
             "preflight does not preserve the observed Hermes installation")

    before, _ = tree_inventory(capture / "workspaces/before/fixture_project")
    _require(set(before) == FIXTURE_FILES and before == plan.get("protected_sha256"),
             "pre-run fixture differs from the frozen plan")
    native_decoder = ROOT / "session_bench/adapters/agent_session_native_decoder.py"
    sys.path.insert(0, str(ROOT))
    from session_bench.adapters.agent_session_native_decoder import decode_agent_native_bytes

    expected_prompts = [turn["text"] for turn in workload["turns"]]
    canaries = [turn["response_canary"] for turn in workload["turns"]]
    session_id = result["session_id"]
    tree_hashes = {}
    native_messages = {}
    usage_reports = {}
    ended_ns = []
    for turn in (1, 2):
        prefix = f"turn-r{turn}"
        receipt = result["turns"][turn - 1]
        launch = load(capture / prefix / "launch.json")
        exit_receipt = load(capture / prefix / "exit.json")
        usage = load(capture / prefix / "usage.json")
        export_receipt = load(capture / prefix / "native/receipt.json")
        export_launch = load(capture / prefix / "native/launch.json")
        native_path = capture / prefix / "native/session.jsonl"
        native_bytes = native_path.read_bytes()
        decoded = decode_agent_native_bytes("hermes", native_bytes)
        stdout = (capture / prefix / "stdout.txt").read_bytes()
        stderr = (capture / prefix / "stderr.txt").read_bytes()
        _require(receipt.get("turn") == turn and receipt.get("status") == "completed"
                 and receipt.get("returncode") == 0
                 and receipt.get("stdout_sha256") == sha(stdout)
                 and receipt.get("stderr_sha256") == sha(stderr)
                 and receipt.get("usage_sha256") == file_hash(capture, f"{prefix}/usage.json")
                 and exit_receipt.get("returncode") == 0
                 and exit_receipt.get("stdout_sha256") == sha(stdout)
                 and exit_receipt.get("stderr_sha256") == sha(stderr)
                 and isinstance(exit_receipt.get("ended_ns"), int),
                 f"R{turn} process receipts differ from retained output")
        ended_ns.append(exit_receipt["ended_ns"])
        _require(not stderr and stdout.decode("utf-8").rstrip().endswith(canaries[turn - 1]),
                 f"R{turn} output is incomplete or reports a failure")
        _require(usage.get("session_id") == session_id
                 and usage.get("provider") == PROVIDER and usage.get("model") == MODEL
                 and usage.get("completed") is True and usage.get("failed") is False
                 and usage.get("interrupted") is False,
                 f"R{turn} usage receipt does not bind the completed route")
        usage_reports[turn] = usage
        _require(decoded.status == "complete" and decoded.session_id == session_id
                 and decoded.header.get("model") == MODEL,
                 f"R{turn} native export identity or decoder differs")
        messages = decoded.header["messages"]
        native_messages[turn] = messages
        _require([message.get("content") for message in messages if message.get("role") == "user"]
                 == expected_prompts[:turn],
                 f"R{turn} native prompts differ")
        assistant_text = [event.content for event in decoded.events
                          if event.role == "assistant" and isinstance(event.content, str)]
        require_turn_response_canaries(assistant_text, canaries, turn)
        final = [message["content"] for message in messages
                 if message.get("role") == "assistant"
                 and isinstance(message.get("content"), str)
                 and message["content"].rstrip().endswith(canaries[turn - 1])]
        _require(len(final) == 1 and stdout.decode("utf-8").strip() == final[0].strip(),
                 f"R{turn} native final response differs from process stdout")
        _require(export_receipt.get("session_id") == session_id
                 and export_receipt.get("sha256") == sha(native_bytes)
                 and export_receipt.get("size_bytes") == len(native_bytes)
                 and export_receipt.get("scope") == "single exact session ID; no session listing or DB copy"
                 and export_launch.get("session_id") == session_id
                 and export_launch.get("exact_session_only") is True,
                 f"R{turn} official exact-session export receipt differs")
        export_argv = export_launch.get("argv")
        export_destination = (Path(export_argv[-1]) if isinstance(export_argv, list)
                              and export_argv and isinstance(export_argv[-1], str) else Path("."))
        expected_export_tail = Path("artifacts/v1-expanded-preparation/live-captures") / CAPTURE_ID / prefix / "native/session.jsonl"
        _require(isinstance(export_argv, list)
                 and "--ignore-user-config" in export_argv
                 and "sessions" in export_argv and "export" in export_argv
                 and "list" not in export_argv
                 and _argv_value(export_argv, "--session-id") == session_id
                 and _argv_value(export_argv, "--format") == "jsonl"
                 and export_destination.is_absolute()
                 and export_destination.parts[-len(expected_export_tail.parts):] == expected_export_tail.parts,
                 f"R{turn} native export command is not scoped to the session")
        argv = launch.get("argv")
        _require(isinstance(argv, list) and argv[-1] == expected_prompts[turn - 1]
                 and _argv_value(argv, "--provider") == PROVIDER
                 and _argv_value(argv, "--model") == MODEL
                 and _argv_value(argv, "--toolsets") == "terminal"
                 and _argv_value(argv, "--in") == plan["workspace"]
                 and _argv_value(argv, "-z") == expected_prompts[turn - 1]
                 and "--ignore-user-config" in argv and "--ignore-rules" in argv
                 and "--no-restore-cwd" in argv
                 and argv[:5] == [plan["python"], "-I", "-B", "-c", plan["entrypoint_code"]]
                 and argv[5] == plan["source_root"]
                 and launch.get("prompt_sha256") == sha(expected_prompts[turn - 1].encode())
                 and launch.get("provider") == PROVIDER and launch.get("model") == MODEL
                 and launch.get("config_bypass") is True,
                 f"R{turn} launch lost its route or prompt")
        if turn == 1:
            _require("--resume" not in argv and launch.get("session_id") is None,
                     "R1 unexpectedly resumed a prior session")
        else:
            _require(_argv_value(argv, "--resume") == session_id
                     and launch.get("session_id") == session_id,
                     "R2 did not resume the exact R1 session")
        prompt_path = capture / "observer" / f"prompt-r{turn}.txt"
        _require(prompt_path.read_bytes() == expected_prompts[turn - 1].encode(),
                 f"R{turn} submitted-input witness differs")
        workspace, contents = tree_inventory(capture / prefix / "workspace/fixture_project")
        _require(set(workspace) == FIXTURE_FILES | {".survival-observer.jsonl"},
                 f"R{turn} copied workspace has missing or unexpected files")
        for relative in FIXTURE_FILES - {"checkout.py"}:
            _require(workspace[relative] == before[relative],
                     f"R{turn} protected fixture file changed: {relative}")
        _require((workspace["checkout.py"] == before["checkout.py"]) == (turn == 1),
                 f"R{turn} checkout edit state differs")
        ledger = verify_ledger(contents[".survival-observer.jsonl"], workload,
                               ("inspect", "baseline") if turn == 1 else PHASES, f"R{turn}")
        _require(ledger[-1]["checkout_sha256"] == workspace["checkout.py"],
                 f"R{turn} helper ledger differs from copied checkout")
        tree_hashes[str(turn)] = {"files": workspace,
                                  "canonical_tree_sha256": sha(json.dumps(workspace, sort_keys=True).encode())}
    _require(ended_ns[0] < ended_ns[1], "R1 and R2 process completion order changed")
    _require(native_messages[2][:len(native_messages[1])] == native_messages[1],
             "R2 export does not preserve the exact R1 native messages")

    inventory = {}
    for path in sorted(capture.rglob("*")):
        if path.is_symlink():
            raise QualificationError("capture contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise QualificationError("capture contains a special file")
        relative = path.relative_to(capture).as_posix()
        if relative == "qualification-v2.json":
            continue
        inventory[relative] = sha(path.read_bytes())
    required = {"plan.json", "workload-template.json", "workload-instance.json",
                "capture-result.json", "controller-state.json", "preflight.json",
                "observer/prompt-r1.txt", "observer/prompt-r2.txt"}
    for turn in (1, 2):
        prefix = f"turn-r{turn}/"
        required.update(prefix + name for name in (
            "stdout.txt", "stderr.txt", "exit.json", "launch.json", "usage.json",
            "native/session.jsonl", "native/receipt.json", "native/launch.json",
            "workspace/fixture_project/checkout.py",
            "workspace/fixture_project/.survival-observer.jsonl"))
    _require(required <= inventory.keys(), "complete capture inventory lacks required files")
    _require(inventory["workload-template.json"] == plan["workload_sha256"],
             "inventory differs from frozen workload")
    inventory["session_bench/adapters/agent_session_native_decoder.py"] = sha(native_decoder.read_bytes())
    verifier = Path(__file__)
    return {
        "schema_version": SCHEMA,
        "configuration_id": "hermes", "capture_id": CAPTURE_ID, "repetition": 2,
        "status": "qualified_capture_pending_independent_reproduction",
        "capture_complete": True, "same_session_continuation": True,
        "native_decode_status": "complete", "provider": PROVIDER, "model": MODEL,
        "session_id": session_id, "installed_version": version,
        "helper_receipts_verified": {"r1": ["inspect:0", "baseline:1"],
                                     "r2": ["inspect:0", "baseline:1", "final:0"]},
        "estimated_cost_usd": sum(float(usage_reports[n].get("estimated_cost_usd") or 0)
                                  for n in (1, 2)),
        "api_calls": sum(int(usage_reports[n].get("api_calls") or 0) for n in (1, 2)),
        "score_eligible": False, "publication_eligible": False,
        "independent_reproduction": False, "independent_native_to_score_replay": False,
        "native_export_kind": "exact_session_sqlite_export",
        "complete_shared_sqlite_root": False,
        "capture_root_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
        "verifier": {"path": "scripts/qualify_hermes_complete_capture.py",
                     "sha256": sha(verifier.read_bytes())},
        "evidence_files": dict(sorted(inventory.items())),
        "workspace_trees": tree_hashes,
        "limits": [
            "Offline acquisition qualification by the implementation operator and host only.",
            "Official JSON export contains the exact session, not the full shared SQLite root or sidecars.",
            "No response-scoped usage evidence, public 31-metric replay, or independent reproduction is claimed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out if args.out.is_absolute() else ROOT / args.out
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refusing to overwrite qualification receipt: {output}")
    capture = args.capture if args.capture.is_absolute() else ROOT / args.capture
    receipt = verify_complete_capture(capture)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"{receipt['status']}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
