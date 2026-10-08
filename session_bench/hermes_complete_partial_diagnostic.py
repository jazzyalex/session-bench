"""Private 31-state projection for the completed Hermes repetition-2 capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.qualify_hermes_complete_capture import CAPTURE_ID, ROOT, verify_complete_capture
from session_bench.hermes_partial_diagnostic import BROAD, DEEP


CAPTURE = ROOT / "artifacts/v1-expanded-preparation/live-captures" / CAPTURE_ID
QUALIFICATION_SHA256 = "8da7bd57088c84c3d77c18c3a65c2bca55b6c0a17d598253983dd24fc0de4b9a"
SCHEMA = "session-bench-hermes-complete-private-partial-diagnostic-v1"

MEASURED = {
    "work.submitted_turns": "Two exact submitted-input prompt files and controller receipts bind ordered R1/R2 turns.",
    "work.visible_responses": "Separate process stdout files exactly match the two canary-bearing native final responses.",
    "work.changed_files": "Qualified R1/R2 workspace copies bind before/after hashes of project-relative checkout.py.",
    "revision.r1": "The R1 submitted-input witness exactly matches the first native user turn.",
    "revision.r2": "The R2 submitted-input witness exactly matches the second native user turn.",
    "revision.r1_r2_order": "R2 resumes the exact R1 session after its process completes, and both prompts retain their order.",
    "causal.turn_response": "Each canary-bearing native response follows its exact user turn and matches that turn's process stdout.",
    "broad.thread_structure": "The exact-session export retains one ordered user/assistant/tool message sequence.",
    "broad.standard_tools_readable": "The exact-session export is a UTF-8 JSON object readable with a standard JSON parser.",
}

UNRESOLVED = {
    "work.actions": "Two compound terminal calls do not establish four independently observed actions.",
    "work.results": "Two terminal result messages do not establish four separate result boundaries.",
    "causal.action_result": "Two native call/result IDs do not establish the required four-action causal population.",
    "revision.final_after_r2": "The compound R2 call does not establish separate edit and final-helper result boundaries.",
    "attribution.model_config": "Session model and turn usage receipts lack response-scoped configuration joins.",
    "attribution.usage": "The turn/session usage receipts do not provide response-scoped usage records.",
    "attribution.token_semantics": "Session token totals do not prove per-response input/output/cache semantics.",
    "attribution.reconciliation": "No response-scoped usage values exist to reconcile to session totals.",
    "portable.complete_root": "The native file is an exact-session SQLite export, not the complete shared database root.",
    "portable.companions": "SQLite sidecars and journal family were not inventoried or copied.",
    "portable.isolated_decode": "No OS-isolated native root replay is retained for this capture.",
    "portable.canonical_equality": "No isolated-versus-ordinary canonical reconstruction comparison was performed.",
    "broad.readable_rationale": "No independent response-by-response rationale population is established.",
    "broad.documented_format": "The copied native export has no complete documented container/identity/join/version contract.",
    "broad.self_contained_identity": "Native export identifies session/model but not the complete harness/surface/record-family tuple.",
    "broad.declared_format_version": "No machine-readable native export schema version is established.",
    "broad.event_timestamps": "Native message timestamps exist but their unit/time-zone contract and required-event population are unestablished.",
    "broad.honest_version_signal": "No schema-version compatibility contract is retained in this exact-session export.",
    "broad.observed_schema_stability": "The full declared build/date observation window has not been inventoried.",
    "broad.stable_root_location": "The complete shared native root was not discovered for this synthetic session.",
    "broad.naive_reader_duplicate_safety": "Exact-once semantic reconstruction of compound terminal calls remains unresolved.",
    "broad.classified_content_density": "The frozen logical-record-role classifier has not been applied to the complete native root.",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build(capture: Path = CAPTURE) -> dict:
    if capture.is_symlink() or capture.resolve() != CAPTURE.resolve():
        raise ValueError("only the exact retained Hermes repetition-2 capture is accepted")
    qualification_raw = (capture / "qualification-v2.json").read_bytes()
    if _sha(qualification_raw) != QUALIFICATION_SHA256:
        raise ValueError("Hermes repetition-2 qualification changed")
    qualification = json.loads(qualification_raw)
    if verify_complete_capture(capture) != qualification:
        raise ValueError("Hermes repetition-2 qualification no longer re-verifies")
    inventory = qualification["evidence_files"]

    def locator(name: str) -> dict[str, str]:
        raw = (capture / name).read_bytes()
        if _sha(raw) != inventory[name]:
            raise ValueError(f"qualified Hermes member changed: {name}")
        return {"path": name, "sha256": inventory[name]}

    base = [locator("workload-instance.json"), locator("turn-r2/native/session.jsonl")]
    witnesses = {
        "work.submitted_turns": ["observer/prompt-r1.txt", "observer/prompt-r2.txt", "capture-result.json"],
        "work.visible_responses": ["turn-r1/stdout.txt", "turn-r2/stdout.txt", "capture-result.json"],
        "work.changed_files": ["turn-r1/workspace/fixture_project/checkout.py",
                               "turn-r2/workspace/fixture_project/checkout.py"],
        "revision.r1": ["observer/prompt-r1.txt", "turn-r1/launch.json"],
        "revision.r2": ["observer/prompt-r2.txt", "turn-r2/launch.json"],
        "revision.r1_r2_order": ["turn-r1/exit.json", "turn-r2/launch.json", "capture-result.json"],
        "causal.turn_response": ["observer/prompt-r1.txt", "observer/prompt-r2.txt",
                                 "turn-r1/stdout.txt", "turn-r2/stdout.txt"],
    }
    rows = []
    for metric in DEEP + BROAD:
        measured = metric in MEASURED
        rows.append({"id": metric, "state": "measured" if measured else "unresolved",
                     "reason": (MEASURED if measured else UNRESOLVED)[metric],
                     "evidence": [*base, *[locator(name) for name in witnesses.get(metric, [])]]})
    if len(rows) != 31 or len(MEASURED) != 9 or len(UNRESOLVED) != 22:
        raise AssertionError("Hermes metric inventory is incomplete")
    return {"schema_version": SCHEMA, "scope": "private_partial_diagnostic",
            "capture_id": CAPTURE_ID, "repetition": 2,
            "qualification_v2_sha256": QUALIFICATION_SHA256,
            "session_id": qualification["session_id"],
            "model": qualification["model"],
            "native_export_kind": qualification["native_export_kind"],
            "qualified_repetitions_in_packet": 1,
            "compound_calls_count_as_extra_actions": False,
            "response_scoped_usage_inferred": False,
            "metrics": rows, "metric_count": len(rows),
            "score": None, "rank": None, "public_safe": False,
            "independent_reproduction": False}


def write_new(output: Path, capture: Path = CAPTURE) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("private output directory must be new")
    result = build(capture)
    output.mkdir(parents=True)
    (output / "diagnostic.json").write_bytes(
        json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode() + b"\n")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = write_new(args.output)
    print(json.dumps({"capture_id": result["capture_id"],
                      "measured": sum(row["state"] == "measured" for row in result["metrics"]),
                      "output": str(args.output)}, sort_keys=True))
