"""Private, capture-bound Hermes diagnostic; never constructs a score packet.

Only the named, qualification-v2 Hermes capture is accepted. This deliberately
does not promote the official exact-session JSON export to a complete SQLite
root, split compound terminal commands into actions, or allocate session usage
to individual responses.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "artifacts/v1-expanded-preparation/live-captures/hermes-codex-2026-09-29-01"
QUALIFICATION_SHA256 = "935a97bb1d6e34aed043a854ab6991370cdfd8e813a7476a166864e492a27da9"
SCHEMA = "session-bench-hermes-private-partial-diagnostic-v2"

DEEP = (
    "work.submitted_turns", "work.visible_responses", "work.actions", "work.results",
    "work.changed_files", "causal.action_result", "causal.turn_response",
    "revision.r1", "revision.r2", "revision.r1_r2_order", "revision.final_after_r2",
    "attribution.model_config", "attribution.usage", "attribution.token_semantics",
    "attribution.reconciliation", "portable.complete_root", "portable.companions",
    "portable.isolated_decode", "portable.canonical_equality",
)
BROAD = (
    "broad.readable_rationale", "broad.thread_structure", "broad.standard_tools_readable",
    "broad.documented_format", "broad.self_contained_identity",
    "broad.declared_format_version", "broad.event_timestamps",
    "broad.honest_version_signal", "broad.observed_schema_stability",
    "broad.stable_root_location", "broad.naive_reader_duplicate_safety",
    "broad.classified_content_density",
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, name: str, digest: str) -> bytes:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"qualified capture member missing: {name}")
    raw = path.read_bytes()
    if _sha(raw) != digest:
        raise ValueError(f"qualified capture member changed: {name}")
    return raw


def _json(raw: bytes, name: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key in {name}: {key}")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(value, dict):
        raise ValueError(f"{name} is not an object")
    return value


def build(capture: Path = CAPTURE) -> dict:
    if capture.resolve() != CAPTURE.resolve() or capture.is_symlink():
        raise ValueError("only the exact retained qualified Hermes capture is accepted")
    qualification = _json(_read(capture, "qualification-v2.json", QUALIFICATION_SHA256), "qualification-v2")
    if (qualification.get("capture_id") != CAPTURE.name
            or qualification.get("status") != "qualified_capture_pending_independent_reproduction"
            or qualification.get("capture_complete") is not True
            or qualification.get("same_session_continuation") is not True
            or qualification.get("independent_native_to_score_replay") is not False
            or qualification.get("publication_eligible") is not False):
        raise ValueError("qualification-v2 has an unexpected identity or status")
    inventory = qualification["evidence_files"]
    needed = ["workload-instance.json", "turn-r1/native/session.jsonl",
              "turn-r2/native/session.jsonl", "turn-r1/stdout.txt", "turn-r2/stdout.txt",
              "turn-r1/usage.json", "turn-r2/usage.json",
              "observer/prompt-r1.txt", "observer/prompt-r2-continuation.txt",
              "capture-result.json", "continuation-result.json", "continuation-receipt.json"]
    documents = {name: _read(capture, name, inventory[name]) for name in needed}
    workload = _json(documents["workload-instance.json"], "workload")
    exports = [_json(documents[f"turn-r{n}/native/session.jsonl"], f"R{n} native export") for n in (1, 2)]
    for n, export in enumerate(exports, 1):
        messages = export.get("messages")
        if export.get("id") != qualification["session_id"] or not isinstance(messages, list):
            raise ValueError(f"R{n} native identity or messages changed")
        expected = [turn["text"] for turn in workload["turns"][:n]]
        users = [m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "user"]
        if users != expected:
            raise ValueError(f"R{n} user-turn sequence changed")
    final_messages = exports[1]["messages"]
    canaries = [turn["response_canary"] for turn in workload["turns"]]
    responses = [m for m in final_messages if isinstance(m, dict) and m.get("role") == "assistant"
                 and isinstance(m.get("content"), str) and any(m["content"].rstrip().endswith(c) for c in canaries)]
    response_canaries = [next(c for c in canaries if m["content"].rstrip().endswith(c)) for m in responses]
    if response_canaries != canaries or len(responses) != 2:
        raise ValueError("native final-response canaries changed")
    # The export has no explicit turn ID on assistant messages. For this
    # capture, the exact observer stdout and ordered native user/assistant
    # messages form an unambiguous two-turn join.
    turn_response_ids = []
    for n, canary in enumerate(canaries, 1):
        user_positions = [i for i, message in enumerate(final_messages)
                          if isinstance(message, dict) and message.get("role") == "user"
                          and message.get("content") == workload["turns"][n - 1]["text"]]
        response_positions = [i for i, message in enumerate(final_messages)
                              if isinstance(message, dict) and message.get("role") == "assistant"
                              and isinstance(message.get("content"), str)
                              and message["content"].rstrip().endswith(canary)]
        if len(user_positions) != 1 or len(response_positions) != 1:
            raise ValueError("native turn/response join is ambiguous")
        turn_response_ids.append((user_positions[0], response_positions[0]))
    if not (turn_response_ids[0][0] < turn_response_ids[0][1]
            < turn_response_ids[1][0] < turn_response_ids[1][1]):
        raise ValueError("native turn/response order changed")
    calls = [(m, c) for m in final_messages if isinstance(m, dict) for c in (m.get("tool_calls") or [])
             if isinstance(c, dict)]
    results = [m for m in final_messages if isinstance(m, dict) and m.get("role") == "tool"]
    if len(calls) != 2 or len(results) != 2:
        raise ValueError("native terminal-call inventory changed")
    usage = [_json(documents[f"turn-r{n}/usage.json"], f"R{n} usage") for n in (1, 2)]
    if any(u.get("session_id") != qualification["session_id"] for u in usage):
        raise ValueError("usage receipt identity changed")

    # Submitted-input and process-output witnesses are separate from the
    # session export. The qualification-v2 inventory binds their exact bytes.
    prompts = [documents["observer/prompt-r1.txt"].decode("utf-8"),
               documents["observer/prompt-r2-continuation.txt"].decode("utf-8")]
    if prompts != [turn["text"] for turn in workload["turns"]]:
        raise ValueError("observer submitted prompts differ from the workload")
    stdout = [documents[f"turn-r{n}/stdout.txt"].decode("utf-8") for n in (1, 2)]
    if any(stdout[n].strip() != final_messages[turn_response_ids[n][1]]["content"].strip()
           for n in (0, 1)):
        raise ValueError("independent process stdout differs from native response")
    changed_path = "checkout.py"
    workspace_hashes = qualification.get("workspace_trees")
    if not isinstance(workspace_hashes, dict) or set(workspace_hashes) != {"1", "2"}:
        raise ValueError("qualified workspace trees are missing")
    changed_file_witnesses = []
    for n in (1, 2):
        name = f"turn-r{n}/workspace/fixture_project/{changed_path}"
        expected = workspace_hashes[str(n)].get("files", {}).get(changed_path)
        if not isinstance(expected, str):
            raise ValueError("qualified checkout hash is missing")
        _read(capture, name, expected)
        changed_file_witnesses.append({"path": name, "sha256": expected})
    if changed_file_witnesses[0]["sha256"] == changed_file_witnesses[1]["sha256"]:
        raise ValueError("qualified R1/R2 checkout did not change")
    first = _json(documents["capture-result.json"], "R1 controller result")
    continuation = _json(documents["continuation-result.json"], "R2 controller result")
    receipt = _json(documents["continuation-receipt.json"], "continuation receipt")
    if (first.get("attempt_id") != CAPTURE.name or first.get("session_id") != qualification["session_id"]
            or first.get("model_submissions") != 1
            or continuation.get("attempt_id") != CAPTURE.name
            or continuation.get("session_id") != qualification["session_id"]
            or continuation.get("model_submissions_before_continuation") != 1
            or continuation.get("model_submissions_in_continuation") != 1
            or receipt.get("original_capture_result_sha256") != inventory["capture-result.json"]
            or receipt.get("r1_stdout_sha256") != inventory["turn-r1/stdout.txt"]
            or receipt.get("r1_native_sha256") != inventory["turn-r1/native/session.jsonl"]
            or receipt.get("r1_session_id") != qualification["session_id"]):
        raise ValueError("controller or continuation witness does not bind the two turns")

    native_locator = {"path": "turn-r2/native/session.jsonl", "sha256": inventory["turn-r2/native/session.jsonl"]}
    workload_locator = {"path": "workload-instance.json", "sha256": inventory["workload-instance.json"]}
    def locator(name: str) -> dict:
        return {"path": name, "sha256": inventory[name]}
    deep_witnesses = {
        "work.submitted_turns": [locator("observer/prompt-r1.txt"),
                                 locator("observer/prompt-r2-continuation.txt"),
                                 locator("capture-result.json"), locator("continuation-result.json")],
        "work.visible_responses": [locator("turn-r1/stdout.txt"), locator("turn-r2/stdout.txt"),
                                   locator("continuation-receipt.json")],
        "work.changed_files": changed_file_witnesses,
        "causal.turn_response": [locator("observer/prompt-r1.txt"),
                                 locator("observer/prompt-r2-continuation.txt"),
                                 locator("turn-r1/stdout.txt"), locator("turn-r2/stdout.txt")],
        "revision.r1": [locator("observer/prompt-r1.txt"), locator("capture-result.json")],
        "revision.r2": [locator("observer/prompt-r2-continuation.txt"), locator("continuation-result.json")],
        "revision.r1_r2_order": [locator("observer/prompt-r1.txt"),
                                 locator("observer/prompt-r2-continuation.txt"),
                                 locator("capture-result.json"), locator("continuation-result.json"),
                                 locator("continuation-receipt.json")],
    }
    measured = {
        "work.submitted_turns": "Two separately retained submitted-input prompt files match the ordered native user turns; controller receipts establish both submissions.",
        "work.visible_responses": "Separate R1/R2 process stdout retains each response canary, matching the native final responses.",
        "work.changed_files": "The qualified R1 and R2 copied workspaces independently hash the same project-relative checkout.py path before and after its edit.",
        "revision.r1": "The original submitted-input prompt file exactly matches the first native user message.",
        "revision.r2": "The retained correction prompt file exactly matches the second native user message.",
        "revision.r1_r2_order": "Submitted prompt files and continuation controller receipts establish R1 before R2 in one session; native order agrees.",
        "causal.turn_response": "Each exact submitted prompt precedes one canary-bearing native final response in the continued session; independent R1/R2 stdout matches each response byte-for-byte after whitespace trim.",
        "broad.thread_structure": "The export retains one session identity and an ordered user/assistant/tool message sequence.",
        "broad.standard_tools_readable": "The exact-session export is a UTF-8 JSON object read by the standard JSON parser.",
    }
    reasons = {
        "work.actions": "Two native terminal calls contain compound shell work; they do not establish four distinct observed actions.",
        "work.results": "Two terminal result messages cannot establish four separate result boundaries.",
        "causal.action_result": "Two call/result IDs survive; the four-action causal population is not established.",
        "revision.final_after_r2": "A compound R2 shell call does not expose separate edit, final-helper result, and response links.",
        "attribution.model_config": "The session header and usage receipts name the model, without response-scoped configuration joins.",
        "attribution.usage": "Usage is reported by session/turn receipt, without a response-scoped usage record.",
        "attribution.token_semantics": "Session token fields cannot establish response-scoped token and cache semantics.",
        "attribution.reconciliation": "No response-scoped usage values exist to reconcile to the session totals.",
        "portable.complete_root": "The retained file is an exact-session SQLite export; complete shared SQLite root was not captured.",
        "portable.companions": "Required SQLite sidecars or journal family cannot be inferred from this export.",
        "portable.isolated_decode": "JSON parsing succeeds, but an isolated root replay was not performed by this diagnostic.",
        "portable.canonical_equality": "No isolated-versus-ordinary canonical reconstruction comparison was performed.",
        "broad.readable_rationale": "No independent response-by-response rationale population is established.",
        "broad.documented_format": "The exact retained capture contains no format documentation sufficient for this assertion.",
        "broad.self_contained_identity": "Session ID and model survive, but complete harness/surface/record-family identity is not established inside the export.",
        "broad.declared_format_version": "No machine-readable export schema version is established in the retained native object.",
        "broad.event_timestamps": "Message timestamps exist, but native time units and complete required-event population are not established here.",
        "broad.honest_version_signal": "No export schema-version compatibility contract is retained in the capture.",
        "broad.observed_schema_stability": "One qualified capture does not establish a complete build/date observation window.",
        "broad.stable_root_location": "Three complete-root repetitions and an isolated root locator are absent.",
        "broad.naive_reader_duplicate_safety": "One forward read is possible, but exact-once semantic reconstruction of compound calls is unresolved.",
        "broad.classified_content_density": "The frozen logical-record-role classifier has not been applied to this export.",
    }
    rows = [{"id": metric, "state": "measured" if metric in measured else "unresolved",
             "reason": measured.get(metric, reasons.get(metric)),
             "evidence": [workload_locator, native_locator, *deep_witnesses.get(metric, [])]}
            for metric in DEEP + BROAD]
    if len(rows) != 31 or any(not row["reason"] for row in rows):
        raise AssertionError("31-state diagnostic inventory is incomplete")
    return {
        "schema_version": SCHEMA, "scope": "private_partial_diagnostic", "capture_id": CAPTURE.name,
        "qualified_repetitions": 1, "qualification_v2_sha256": QUALIFICATION_SHA256,
        "session_id": qualification["session_id"], "native_export_kind": "exact_session_sqlite_export",
        "native_terminal_calls": len(calls), "native_terminal_results": len(results),
        "compound_calls_count_as_extra_actions": False, "response_scoped_usage_inferred": False,
        "metric_count": len(rows), "metrics": rows,
        "score": None, "rank": None, "public_safe": False,
        "independent_reproduction": False,
    }


def write_new(output: Path, capture: Path = CAPTURE) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("private output directory must be new")
    result = build(capture)
    output.mkdir(parents=True)
    raw = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    (output / "diagnostic.json").write_bytes(raw)
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = write_new(args.output)
    print(json.dumps({"capture_id": summary["capture_id"], "metric_count": summary["metric_count"],
                      "measured": sum(row["state"] == "measured" for row in summary["metrics"]),
                      "output": str(args.output)}, sort_keys=True))
