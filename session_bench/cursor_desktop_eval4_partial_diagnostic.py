"""Private, capture-bound 31-metric diagnostic for Cursor Desktop eval4.

This is an additive diagnostic over the retained private decoder replay.  It
does not reinterpret decoder-native facts as observer truth: a deep metric is
measured only when a separate retained project/observer artifact witnesses it.
The capture remains incomplete, private, unscored, and non-rankable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .v1_public_score import PUBLIC_METRICS


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "artifacts/survival-v1-runs/cursor-desktop-eval-4"
REPLAY = ROOT / "artifacts/v1-expanded-preparation/cursor-desktop-eval4-private-replay-v1"
OUTPUT = ROOT / "artifacts/v1-expanded-preparation/cursor-desktop-eval4-private-partial-diagnostic-v1"
SCHEMA = "session-bench-cursor-desktop-eval4-private-partial-diagnostic-v1"

DEEP = tuple(metric for metric, spec in PUBLIC_METRICS.items() if spec.source == "survival")
BROAD = tuple(metric for metric, spec in PUBLIC_METRICS.items() if spec.source == "format_profile")

SOURCES = {
    "attempt.json": "21210caedcfd750d3492c80a1ab679caaf73612e7b7f419330d30dac8f43dd98",
    "workload-instance.json": "33a2caa0bffd14a66d252cf14a9b5b544634f15ffef4fc9120301255b4db80f1",
    "observer/pre-run-project-manifest.json": "e9db6a5e781e5ad7fe3dd637bff7431f5d203a0e5d33552636d56dd028075c41",
    "project/fixture_project/.survival-observer.jsonl": "891cf0392f67ab1ef6fc8b26252ea85673e6f44f7c66c0a1a75c58956f55b8c6",
    "project/fixture_project/checkout.py": "a020043db82bec2df47204c03e11ab40c5275139e0b122723810304d81e7050f",
}
REPLAY_SOURCES = {
    "decoded.private.json": "7832c950544d3aec28fd6c3163914f2d4a3a7fde039cd609a89748c46afb5dc7",
    "replay-report.private.json": "63f9ea54c42716e96bd3f011827780b71ec757937830e561fd6ba7fed2e67474",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, name: str, expected: str) -> bytes:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"retained diagnostic source missing: {name}")
    raw = path.read_bytes()
    if _sha(raw) != expected:
        raise ValueError(f"retained diagnostic source changed: {name}")
    return raw


def _json(raw: bytes, name: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key in {name}: {key}")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _locator(path: str, digest: str) -> dict[str, str]:
    return {"path": path, "sha256": digest}


def build(capture: Path = CAPTURE, replay: Path = REPLAY) -> dict[str, Any]:
    if capture.is_symlink() or capture.resolve() != CAPTURE.resolve():
        raise ValueError("only the exact retained Cursor Desktop eval4 capture is accepted")
    if replay.is_symlink() or replay.resolve() != REPLAY.resolve():
        raise ValueError("only the exact retained Cursor Desktop eval4 replay is accepted")

    source_raw = {name: _read(capture, name, digest) for name, digest in SOURCES.items()}
    replay_raw = {name: _read(replay, name, digest) for name, digest in REPLAY_SOURCES.items()}
    attempt = _json(source_raw["attempt.json"], "attempt.json")
    workload = _json(source_raw["workload-instance.json"], "workload-instance.json")
    manifest = _json(source_raw["observer/pre-run-project-manifest.json"], "pre-run-project-manifest.json")
    decoded = _json(replay_raw["decoded.private.json"], "decoded.private.json")
    replay_report = _json(replay_raw["replay-report.private.json"], "replay-report.private.json")
    ledger = [
        _json(line, f"helper ledger line {index}")
        for index, line in enumerate(
            source_raw["project/fixture_project/.survival-observer.jsonl"].splitlines(), 1
        )
        if line.strip()
    ]

    observed = attempt.get("observed", {})
    before = workload.get("filesystem", {}).get("before_sha256")
    after = SOURCES["project/fixture_project/checkout.py"]
    if (
        attempt.get("attempt_id") != CAPTURE.name
        or attempt.get("score_eligible") is not False
        or observed.get("native_root_complete") is not False
        or workload.get("run_id") != CAPTURE.name
        or manifest.get("attempt_id") != CAPTURE.name
        or manifest.get("files", {}).get("fixture_project/checkout.py", {}).get("sha256") != before
        or observed.get("checkout_sha256_after_r1") != before
        or observed.get("checkout_sha256_after_r2") != after
        or observed.get("helper_ledger_sha256") != SOURCES["project/fixture_project/.survival-observer.jsonl"]
    ):
        raise ValueError("retained attempt, workload, or project witness identity changed")
    if (
        [row.get("phase") for row in ledger] != ["inspect", "baseline", "final"]
        or [row.get("exit_code") for row in ledger] != [0, 1, 0]
        or ledger[0].get("checkout_sha256") != before
        or ledger[1].get("checkout_sha256") != before
        or ledger[2].get("checkout_sha256") != after
        or any(row.get("run_canary") != workload.get("run_canary") for row in ledger)
    ):
        raise ValueError("helper ledger no longer brackets the retained file change")
    if (
        replay_report.get("attempt_id") != CAPTURE.name
        or replay_report.get("score_eligible") is not False
        or replay_report.get("public_safe") is not False
        or replay_report.get("native_family_complete") is not False
        or replay_report.get("derived", {}).get("decoded_sha256") != REPLAY_SOURCES["decoded.private.json"]
        or set(decoded.get("metrics", {})) != set(DEEP)
    ):
        raise ValueError("private decoder replay identity or incomplete status changed")

    native_change = decoded.get("file_changes", [])
    if (
        len(native_change) != 1
        or native_change[0].get("after_sha256") != after
        or not str(native_change[0].get("path", "")).endswith("/fixture_project/checkout.py")
    ):
        raise ValueError("decoder replay no longer agrees with the witnessed file change")

    evidence = {
        "workload": _locator("capture/workload-instance.json", SOURCES["workload-instance.json"]),
        "attempt": _locator("capture/attempt.json", SOURCES["attempt.json"]),
        "manifest": _locator(
            "capture/observer/pre-run-project-manifest.json",
            SOURCES["observer/pre-run-project-manifest.json"],
        ),
        "ledger": _locator(
            "capture/project/fixture_project/.survival-observer.jsonl",
            SOURCES["project/fixture_project/.survival-observer.jsonl"],
        ),
        "final_file": _locator(
            "capture/project/fixture_project/checkout.py",
            SOURCES["project/fixture_project/checkout.py"],
        ),
        "decoded": _locator("replay/decoded.private.json", REPLAY_SOURCES["decoded.private.json"]),
        "report": _locator(
            "replay/replay-report.private.json", REPLAY_SOURCES["replay-report.private.json"]
        ),
    }

    measured = {
        "work.changed_files": (
            "The pre-run manifest, R1 checkpoint hash, final helper-ledger hash, and retained final file "
            "independently bracket one checkout.py change; the native write record agrees with the after hash."
        ),
        "broad.readable_rationale": (
            "Both decoded visible responses contain non-empty ordered explanatory text; this is a private "
            "format observation, not an independent claim that the UI displayed it."
        ),
        "broad.thread_structure": (
            "The copied transcript decodes two ordered user turns and two ordered response records in one "
            "retained session projection."
        ),
        "broad.standard_tools_readable": (
            "The closed copied derivative is JSONL/JSON and was parsed offline with the Python standard library."
        ),
        "broad.declared_format_version": (
            "The retained composer object carries machine-readable native format version 3, bound to the "
            "verified companion row."
        ),
    }
    unresolved = {
        "work.submitted_turns": "No separately retained submitted-input bytes witness both UI submissions.",
        "work.visible_responses": "No independent UI or process-output artifact witnesses both visible responses.",
        "work.actions": "The helper ledger witnesses three commands, but no separate observer record witnesses the edit action itself.",
        "work.results": "The helper ledger witnesses three command results, but no separate observer result exists for the edit action.",
        "causal.action_result": "Observer evidence does not cover the complete four-action result population.",
        "causal.turn_response": "Turn-response links are decoder-derived and lack a separate observer witness.",
        "revision.r1": "The native R1 text has no separately retained submitted-input witness.",
        "revision.r2": "The native R2 text has no separately retained submitted-input witness.",
        "revision.r1_r2_order": "Native order and controller summary exist, but separate submitted-input bytes were not retained.",
        "revision.final_after_r2": "The final helper result is retained, but no separate R2 submission and response-order witness closes the chain.",
        "attribution.model_config": "Model/configuration is native-only and lacks response-scoped observer attribution.",
        "attribution.usage": "No response-scoped usage records or observer witnesses are retained.",
        "attribution.token_semantics": "No retained evidence defines response-scoped token and cache semantics.",
        "attribution.reconciliation": "No response usage exists to reconcile against a native session total.",
        "portable.complete_root": "The retained capture explicitly marks the native root/family incomplete.",
        "portable.companions": "Only selected session-keyed global-store rows were retained, not the complete companion family.",
        "portable.isolated_decode": "The replay decoded a copied derivative, but no separate observer witness establishes complete isolated-root replay.",
        "portable.canonical_equality": "No ordinary-root versus copied-root canonical comparison is retained.",
        "broad.documented_format": "No retained native-format document covers containers, record types, identities, joins, and version semantics.",
        "broad.self_contained_identity": "The derivative does not self-contain complete harness, surface, record-family, and unhashed session identity.",
        "broad.event_timestamps": "Complete timestamp population with declared units and time zones is not retained.",
        "broad.honest_version_signal": "Native version 3 has no retained compatibility contract tied to the decoder contract.",
        "broad.observed_schema_stability": "One capture cannot establish a build/date compatibility window.",
        "broad.stable_root_location": "Three complete-root repetitions and isolated root discovery are absent.",
        "broad.naive_reader_duplicate_safety": "No retained deduplication rule proves exact-once forward reading.",
        "broad.classified_content_density": "The native record family is incomplete, so full-family logical-byte density is unresolved.",
    }
    deep_observer = [evidence["manifest"], evidence["attempt"], evidence["ledger"], evidence["final_file"]]
    rows = []
    for metric in PUBLIC_METRICS:
        is_measured = metric in measured
        row_evidence = [evidence["workload"], evidence["decoded"], evidence["report"]]
        if metric == "work.changed_files":
            row_evidence.extend(deep_observer)
        rows.append(
            {
                "id": metric,
                "state": "measured" if is_measured else "unresolved",
                "reason": measured.get(metric, unresolved.get(metric)),
                "evidence": row_evidence,
            }
        )
    if len(rows) != 31 or {row["id"] for row in rows} != set(PUBLIC_METRICS):
        raise AssertionError("31-state diagnostic inventory is incomplete")
    if any(not row["reason"] for row in rows):
        raise AssertionError("every diagnostic row must explain its state")
    observer_paths = {item["path"] for item in deep_observer}
    for row in rows:
        if row["id"] in DEEP and row["state"] == "measured":
            if not observer_paths.intersection(item["path"] for item in row["evidence"]):
                raise AssertionError("deep measurements require separate observer witnesses")

    return {
        "schema_version": SCHEMA,
        "scope": "private_partial_diagnostic",
        "capture_id": CAPTURE.name,
        "decoder_report_sha256": REPLAY_SOURCES["replay-report.private.json"],
        "additional_to_decoder_report": [
            "observer-bound changed-file diagnostic",
            "twelve-row broad-format state inventory",
        ],
        "metric_count": len(rows),
        "metrics": rows,
        "measured_count": sum(row["state"] == "measured" for row in rows),
        "unresolved_count": sum(row["state"] == "unresolved" for row in rows),
        "native_family_complete": False,
        "observer_independent": False,
        "independent_reproduction": False,
        "score_eligible": False,
        "score": None,
        "rank": None,
        "public_safe": False,
    }


def write_new(output: Path, capture: Path = CAPTURE, replay: Path = REPLAY) -> dict[str, Any]:
    if output.exists() or output.is_symlink():
        raise ValueError("private output directory must be new")
    result = build(capture, replay)
    output.mkdir(parents=True)
    raw = json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    (output / "diagnostic.private.json").write_bytes(raw)
    return result


if __name__ == "__main__":
    result = write_new(OUTPUT)
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "metric_count": result["metric_count"],
                "measured_count": result["measured_count"],
                "score_eligible": False,
                "public_safe": False,
            },
            sort_keys=True,
        )
    )
