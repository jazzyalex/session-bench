#!/usr/bin/env python3
"""Independently verify a retained Hermes survival-v1 two-turn capture offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "openai-codex"
MODEL = "gpt-5.5"
PHASES = ("inspect", "baseline", "final")
EXPECTED_CODES = {"inspect": 0, "baseline": 1, "final": 0}


class QualificationError(RuntimeError):
    pass


def require_turn_response_canaries(assistant_text: list[str], response_canaries: list[str],
                                  turn: int) -> None:
    """Require each completed turn's canary as that turn's assistant suffix."""
    matched_positions = []
    for index in range(turn):
        canary = response_canaries[index]
        positions = [position for position, text in enumerate(assistant_text)
                     if text.rstrip().endswith(canary)]
        if not positions:
            raise QualificationError(f"R{turn} native transcript lacks the R{index + 1} assistant response canary")
        matched_positions.append(positions[-1])
    if matched_positions != sorted(matched_positions):
        raise QualificationError(f"R{turn} assistant response canaries are out of turn order")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise QualificationError(f"duplicate JSON key in {path}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise QualificationError(f"cannot parse {path}: {exc}") from exc


def file_hash(root: Path, relative: str) -> str:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise QualificationError(f"required regular file missing: {relative}")
    return sha(path.read_bytes())


def tree_inventory(root: Path) -> tuple[dict[str, str], dict[str, bytes]]:
    if root.is_symlink() or not root.is_dir():
        raise QualificationError(f"workspace is not an ordinary directory: {root}")
    hashes: dict[str, str] = {}
    contents: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise QualificationError(f"symlink in copied workspace: {path.relative_to(root)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise QualificationError(f"special workspace file: {path.relative_to(root)}")
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        hashes[rel] = sha(raw)
        contents[rel] = raw
    if len(contents) > 1000:
        raise QualificationError("workspace file-count limit exceeded")
    return hashes, contents


def verify_ledger(raw: bytes, workload: dict, expected_phases: tuple[str, ...], label: str) -> list[dict]:
    if not raw or len(raw) > 1024 * 1024:
        raise QualificationError(f"{label} helper ledger is empty or oversized")
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualificationError(f"{label} helper ledger contains invalid JSON") from exc
        if not isinstance(row, dict):
            raise QualificationError(f"{label} helper ledger row is not an object")
        rows.append(row)
    if len(rows) != len(expected_phases):
        raise QualificationError(f"{label} helper phase count mismatch")
    canary = workload["run_canary"]
    nonces = workload["helper"]["nonces"]
    bodies = {}
    for row, phase in zip(rows, expected_phases, strict=True):
        nonce = nonces[phase]
        prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} "
        output = row.get("output")
        if (row.get("schema_version") != "1.0-survival-helper-ledger"
                or row.get("id") != f"helper-{phase}-{nonce}"
                or row.get("phase") != phase or row.get("run_canary") != canary
                or row.get("helper_nonce") != nonce
                or row.get("argv") != ["python3", "bench_check.py", phase]
                or row.get("cwd") != "fixture_project"
                or row.get("exit_code") != EXPECTED_CODES[phase]
                or not isinstance(output, str) or not output.startswith(prefix)):
            raise QualificationError(f"{label} helper row does not prove expected {phase} outcome")
        try:
            bodies[phase] = json.loads(output[len(prefix):])
        except json.JSONDecodeError as exc:
            raise QualificationError(f"{label} helper {phase} body is invalid JSON") from exc
    if bodies["inspect"].get("phase") != "inspect" or not bodies["inspect"].get("checkout_source"):
        raise QualificationError(f"{label} inspect row lacks the original checkout source")
    baseline_tests = bodies["baseline"].get("tests")
    if (not isinstance(baseline_tests, list) or not baseline_tests
            or all(test.get("passed") is True for test in baseline_tests)):
        raise QualificationError(f"{label} baseline did not retain an expected failing case")
    if "final" in expected_phases:
        final_tests = bodies["final"].get("tests")
        if (not isinstance(final_tests, list) or not final_tests
                or any(test.get("passed") is not True for test in final_tests)):
            raise QualificationError(f"{label} final helper did not pass every declared case")
    return rows


def verify_capture(capture: Path) -> dict:
    capture = capture.resolve()
    if not capture.is_dir() or capture.is_symlink():
        raise QualificationError("capture root must be an ordinary directory")
    plan = load(capture / "plan.json")
    workload = load(capture / "workload-instance.json")
    original = load(capture / "capture-result.json")
    continuation_receipt = load(capture / "continuation-receipt.json")
    continuation = load(capture / "continuation-result.json")
    preflight = load(capture / "preflight.json")
    continuation_preflight = load(capture / "continuation-preflight.json")
    if plan.get("configuration_id") != "hermes" or plan.get("model") != MODEL or plan.get("provider") != PROVIDER:
        raise QualificationError("plan does not bind Hermes to the declared route")
    if plan.get("status") != "prepared" or plan.get("no_session_list") is not True:
        raise QualificationError("plan is not the bounded, no-list Hermes capture")
    if (original.get("status") != "capture_incomplete"
            or original.get("failure") != "hermes: expected session_id and messages fields"
            or original.get("model_submissions") != 1):
        raise QualificationError("original first-turn decoder failure does not match the pinned correction lineage")
    if (continuation.get("status") != "captured_pending_qualification"
            or continuation.get("model_submissions_before_continuation") != 1
            or continuation.get("model_submissions_in_continuation") != 1
            or continuation.get("independent_reproduction") is not False):
        raise QualificationError("same-session continuation is not complete and pending qualification")
    if preflight.get("ready") is not True or continuation_preflight.get("ready") is not True:
        raise QualificationError("installed entry point version check failed")
    version = preflight.get("version", "").splitlines()[0]
    if not version.startswith("Hermes Agent v0.21.5") or ".dirty" not in version:
        raise QualificationError("installed Hermes version/dirty provenance is not recorded as observed")
    if continuation_preflight.get("version") != preflight.get("version"):
        raise QualificationError("Hermes install/version changed between R1 and R2")

    r1 = capture / "turn-r1"
    r2 = capture / "turn-r2"
    native_decoder = ROOT / "session_bench/adapters/agent_session_native_decoder.py"
    sys.path.insert(0, str(ROOT))
    from session_bench.adapters.agent_session_native_decoder import decode_agent_native_bytes

    expected_files = {
        "checkout.py", "bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py",
        ".survival-observer.jsonl",
    }
    workload_template_hash = sha((capture / "workload-template.json").read_bytes())
    if workload_template_hash != plan.get("workload_sha256"):
        raise QualificationError("workload template hash differs from prepared plan")
    if workload.get("run_canary") != f"SB_SURVIVAL_V1_RUN_{capture.name}":
        raise QualificationError("instantiated workload run canary does not bind this capture ID")

    inventory: dict[str, str] = {}
    expected_turn_messages = [turn["text"] for turn in workload["turns"]]
    response_canaries = [turn["response_canary"] for turn in workload["turns"]]
    decoded = {}
    usage_reports = {}
    tree_hashes = {}
    for turn, root in ((1, r1), (2, r2)):
        prefix = f"turn-r{turn}/"
        for rel in ("stdout.txt", "stderr.txt", "exit.json", "launch.json", "usage.json", "native/session.jsonl"):
            inventory[prefix + rel] = file_hash(capture, prefix + rel)
        launch = load(root / "launch.json")
        exit_receipt = load(root / "exit.json")
        usage = load(root / "usage.json")
        native = (root / "native/session.jsonl").read_bytes()
        decoder_result = decode_agent_native_bytes("hermes", native)
        expected_code = exit_receipt.get("returncode")
        sid = usage.get("session_id")
        if (expected_code != 0 or usage.get("completed") is not True or usage.get("failed") is not False
                or usage.get("provider") != PROVIDER or usage.get("model") != MODEL
                or not isinstance(sid, str) or sid != continuation.get("session_id")
                or decoder_result.status != "complete" or decoder_result.session_id != sid):
            raise QualificationError(f"R{turn} usage, exit, and native identity do not agree")
        if launch.get("provider") != PROVIDER or launch.get("model") != MODEL:
            raise QualificationError(f"R{turn} launch does not pin expected provider/model")
        argv = launch.get("argv")
        if not isinstance(argv, list) or "--ignore-user-config" not in argv or "--ignore-rules" not in argv:
            raise QualificationError(f"R{turn} launch does not disable user configuration/rules")
        for flag, value in (("--provider", PROVIDER), ("--model", MODEL), ("--toolsets", "terminal")):
            if flag not in argv or argv[argv.index(flag) + 1] != value:
                raise QualificationError(f"R{turn} launch lost pinned {flag}")
        if "-z" not in argv or argv[-1] != expected_turn_messages[turn - 1]:
            raise QualificationError(f"R{turn} launch does not match the frozen exact prompt")
        if turn == 2 and ("--resume" not in argv or argv[argv.index("--resume") + 1] != sid):
            raise QualificationError("R2 does not resume the exact R1 session")
        stdout = (root / "stdout.txt").read_text(encoding="utf-8")
        if not stdout.rstrip().endswith(response_canaries[turn - 1]):
            raise QualificationError(f"R{turn} stdout response canary is absent")
        # Hermes retains its decoded native session object in `header`; the
        # shared decoder exposes normalized rows through `events`.
        messages = decoder_result.header["messages"]
        user_prompts = [message.get("content") for message in messages if message.get("role") == "user"]
        if user_prompts != expected_turn_messages[:turn]:
            raise QualificationError(f"R{turn} native transcript does not preserve exact turn prompts")
        assistant_text = [event.content for event in decoder_result.events
                          if event.role == "assistant" and isinstance(event.content, str)]
        require_turn_response_canaries(assistant_text, response_canaries, turn)
        decoded[turn] = decoder_result
        usage_reports[turn] = usage

        workspace_hashes, workspace_contents = tree_inventory(root / "workspace/fixture_project")
        if set(workspace_contents) != expected_files:
            raise QualificationError(f"R{turn} workspace copy has missing or unexpected files")
        for relative in ("bench_check.py", "snapshots/checkout.before.py", "snapshots/checkout.after.py"):
            if workspace_hashes[relative] != plan["protected_sha256"][relative]:
                raise QualificationError(f"R{turn} protected workspace bytes changed: {relative}")
        if turn == 1 and workspace_hashes["checkout.py"] != plan["protected_sha256"]["checkout.py"]:
            raise QualificationError("R1 checkout differs from frozen starting state")
        if turn == 2 and workspace_hashes["checkout.py"] == plan["protected_sha256"]["checkout.py"]:
            raise QualificationError("R2 did not retain a checkout edit")
        ledger_rows = verify_ledger(workspace_contents[".survival-observer.jsonl"], workload,
                                    ("inspect", "baseline") if turn == 1 else PHASES, f"R{turn}")
        tree_hashes[turn] = {"files": workspace_hashes,
                             "canonical_tree_sha256": sha(json.dumps(workspace_hashes, sort_keys=True).encode())}
        inventory[prefix + "workspace-tree.json"] = tree_hashes[turn]["canonical_tree_sha256"]
        inventory[prefix + "workspace/fixture_project/.survival-observer.jsonl"] = workspace_hashes[".survival-observer.jsonl"]
        if turn == 1:
            r1_ledger_sha = workspace_hashes[".survival-observer.jsonl"]
            r1_tree_sha = sha(json.dumps(workspace_hashes, sort_keys=True).encode())
            if (continuation_receipt.get("r1_ledger_sha256") != r1_ledger_sha
                    or continuation_receipt.get("r1_workspace_sha256") != r1_tree_sha
                    or continuation_receipt.get("r1_native_sha256") != inventory[prefix + "native/session.jsonl"]
                    or continuation_receipt.get("r1_usage_sha256") != inventory[prefix + "usage.json"]):
                raise QualificationError("append-only continuation receipt does not pin the copied R1 evidence")
        if turn == 2:
            if not (root / "native/receipt.json").is_file():
                raise QualificationError("R2 native export receipt missing")
            receipt = load(root / "native/receipt.json")
            if receipt.get("session_id") != sid or receipt.get("sha256") != inventory[prefix + "native/session.jsonl"]:
                raise QualificationError("R2 native receipt does not bind exact-session bytes")

    if usage_reports[1]["session_id"] != usage_reports[2]["session_id"]:
        raise QualificationError("R1/R2 did not use one continued session")
    original_result_hash = sha((capture / "capture-result.json").read_bytes())
    if original_result_hash != continuation_receipt.get("original_capture_result_sha256"):
        raise QualificationError("original failed receipt was changed after continuation pinning")
    if continuation_receipt.get("no_prior_capture_or_r1_evidence_modified") is not True:
        raise QualificationError("continuation did not assert preservation of original R1 evidence")

    for relative in ("plan.json", "workload-template.json", "workload-instance.json", "preflight.json",
                     "continuation-preflight.json", "capture-result.json", "continuation-receipt.json",
                     "continuation-result.json", "controller-state.json", "continuation-state.json",
                     "observer/prompt-r1.txt", "observer/prompt-r2-continuation.txt"):
        inventory[relative] = file_hash(capture, relative)
    inventory["session_bench/adapters/agent_session_native_decoder.py"] = sha(native_decoder.read_bytes())
    inventory["fixtures/scenarios/survival-v1/workload/workload.json"] = sha(
        (ROOT / "fixtures/scenarios/survival-v1/workload/workload.json").read_bytes())
    inventory["fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"] = sha(
        (ROOT / "fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py").read_bytes())
    verifier_hash = sha(Path(__file__).read_bytes())
    receipt = {
        "schema_version": "session-bench-hermes-offline-capture-qualification-v1",
        "configuration_id": "hermes",
        "capture_id": capture.name,
        "status": "qualified_capture_pending_independent_reproduction",
        "capture_complete": True,
        "same_session_continuation": True,
        "native_decode_status": "complete",
        "helper_receipts_verified": {"r1": ["inspect:0", "baseline:1"],
                                      "r2": ["inspect:0", "baseline:1", "final:0"]},
        "provider": PROVIDER,
        "model": MODEL,
        "session_id": usage_reports[1]["session_id"],
        "installed_version": version,
        "estimated_cost_usd": sum(float(item.get("estimated_cost_usd") or 0.0)
                                   for item in usage_reports.values()),
        "api_calls": sum(int(item.get("api_calls") or 0) for item in usage_reports.values()),
        "score_eligible": False,
        "publication_eligible": False,
        "independent_reproduction": False,
        "independent_native_to_score_replay": False,
        "capture_root_sha256": sha(json.dumps(inventory, sort_keys=True).encode()),
        "verifier": {"path": "scripts/qualify_hermes_capture.py", "sha256": verifier_hash},
        "evidence_files": dict(sorted(inventory.items())),
        "workspace_trees": tree_hashes,
        "limits": [
            "Offline artifact qualification only; the implementation operator and host remain the same.",
            "Hermes JSONL is an exact-session export from SQLite, not raw shared database pages.",
            "This receipt does not replay the public 31-metric score or establish independent reproduction.",
        ],
    }
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    capture = args.capture if args.capture.is_absolute() else ROOT / args.capture
    output = args.out if args.out.is_absolute() else ROOT / args.out
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refusing to overwrite qualification receipt: {output}")
    receipt = verify_capture(capture)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"{receipt['status']}: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
