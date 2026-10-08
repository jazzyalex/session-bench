#!/usr/bin/env python3
"""Prepare a Pi public replay candidate from the three pinned private packets.

This copies each complete packet byte-for-byte and derives public score inputs
from its retained replay receipt. It does not approve privacy or independence;
those remain explicit external review steps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA
from session_bench.release_score import score_release_run


HOME_PATH = re.compile(rb"(?<![A-Za-z0-9._-])(?:/Users/[A-Za-z0-9_-]+|/home/[A-Za-z0-9_-]+)")
PRIVATE_EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@(?:gmail|icloud|outlook|hotmail)\.com", re.I)
SECRET = re.compile(rb"(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return canonical(value) + b"\n"


RESOLVED_STATES = frozenset({"measured", "native_absent", "contradiction"})


def _all_resolved(metrics: list[dict]) -> bool:
    """A rankable run resolves all 31 metrics; a resolved state may score zero."""
    return len(metrics) == 31 and all(row["state"] in RESOLVED_STATES for row in metrics)


def _events(observer: dict, kind: str) -> list[str]:
    return [row["id"] for row in observer["events"] if row["kind"] == kind]


def _metric_observer_ids(observer: dict, metric_id: str) -> list[str]:
    events = observer["events"]
    relations = observer["relations"]
    by_kind = {kind: _events(observer, kind) for kind in
               ("user_turn", "assistant_response", "action", "result", "file_change", "usage_total")}
    relation_ids = lambda kind: [row["id"] for row in relations if row["kind"] == kind]
    mapping = {
        "work.submitted_turns": by_kind["user_turn"],
        "work.visible_responses": by_kind["assistant_response"],
        "work.actions": by_kind["action"],
        "work.results": by_kind["result"],
        "work.changed_files": by_kind["file_change"],
        "causal.action_result": relation_ids("action_result"),
        "causal.turn_response": relation_ids("turn_response"),
        "revision.r1": ["turn-r1", "response-r1"],
        "revision.r2": ["turn-r2", "response-r2"],
        "revision.r1_r2_order": relation_ids("supersedes"),
        "revision.final_after_r2": relation_ids("final_after"),
        "attribution.model_config": by_kind["assistant_response"],
        "attribution.usage": by_kind["assistant_response"],
        "attribution.token_semantics": by_kind["assistant_response"],
        "attribution.reconciliation": by_kind["usage_total"],
    }
    if metric_id.startswith("portable."):
        # This ID denotes the separate root-capture receipt, not a transcript
        # event. The public evidence also carries its exact file hash locator.
        return ["pi-native-root-capture-v1", "closed-replay-observer"]
    values = mapping.get(metric_id)
    if not values or len(values) != len(set(values)):
        raise ValueError(f"missing or ambiguous independent observer locator: {metric_id}")
    event_ids = {row["id"] for row in events}
    relation_ids_all = {row["id"] for row in relations}
    if any(value not in event_ids | relation_ids_all for value in values):
        raise ValueError(f"observer locator does not exist: {metric_id}")
    return values


def _native_locators(packet: Path, metric_id: str, native_bytes: bytes) -> list[dict]:
    native = {"artifact_id": "native:session.jsonl", "artifact_sha256": sha(native_bytes),
              "record_location": "complete selected Pi session.jsonl; native message and tool-call joins"}
    if not metric_id.startswith("portable."):
        return [native]
    capture = packet / "inputs/capture/native-root"
    names = ["root-capture.json", "before-r1/inventory.json",
             "after-r1/inventory.json", "after-r2/inventory.json"]
    locators = []
    for name in names:
        relative = f"inputs/capture/native-root/{name}"
        raw = (capture / name).read_bytes()
        locators.append({"artifact_id": relative, "artifact_sha256": sha(raw),
                         "record_location": f"{relative}: complete captured root boundary and ordered inventory"})
    # Every portable metric is a claim about the native session file itself.
    locators.insert(0, native)
    return locators


def _survival(packet: Path, actual: dict, repetition: int) -> dict:
    observer = json.loads((packet / "inputs/observer.json").read_bytes())
    plan = json.loads((packet / "inputs/capture/plan.json").read_bytes())
    capture_result = json.loads((packet / "inputs/capture/capture-result.json").read_bytes())
    capture_environment = plan.get("capture_environment")
    if (not isinstance(capture_environment, dict)
            or capture_result.get("capture_environment") != capture_environment
            or capture_environment.get("schema_version") != "session-bench-pi-capture-host-v1"
            or not isinstance(capture_environment.get("os_name"), str)
            or not capture_environment["os_name"]):
        raise ValueError("Pi acquisition operating-system identity is absent or inconsistent")
    native_manifest = json.loads((packet / "native/decode.json").read_bytes())
    native_bytes = (packet / "native/session.jsonl").read_bytes()
    artifact = native_manifest["artifacts"][0]
    if (artifact["path"] != "session.jsonl" or artifact["sha256"] != sha(native_bytes)
            or artifact["size_bytes"] != len(native_bytes)):
        raise ValueError("Pi public candidate native file differs from its exact manifest")
    responses = [row for row in observer["events"] if row["kind"] == "assistant_response"]
    models = {row["fields"].get("model_id") for row in responses}
    configurations = {canonical(row["fields"].get("configuration")).decode("utf-8") for row in responses}
    providers = {row["fields"].get("configuration", {}).get("provider") for row in responses}
    if (len(responses) != 2 or len(models) != 1 or None in models
            or len(configurations) != 1 or "null" in configurations
            or providers != {"openai-codex"}):
        raise ValueError("Pi response identity is incomplete or unstable")

    metric_evidence = []
    for row in actual["measurement"]["metrics"]:
        metric_id = row["id"]
        metric_evidence.append({
            "metric_id": metric_id,
            "observer_ids": _metric_observer_ids(observer, metric_id),
            "native_locators": _native_locators(packet, metric_id, native_bytes),
        })

    profile = actual["format_evidence"]
    decoder = packet / "runtime/session_bench/adapters/agent_session_native_decoder.py"
    survival = {
        "schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": actual["run_id"],
        "configuration_id": "pi",
        "repetition": repetition,
        "capture_id": actual["run_id"],
        "evaluation_id": "native-score-replay:" + actual["run_id"],
        "observer": profile["observer"],
        "native_manifest": profile["native_manifest"],
        "decoder": {"id": "pi-agent-session-jsonl-v1", "sha256": sha(decoder.read_bytes())},
        "identity": {
            "provider": "openai-codex",
            "harness": "pi",
            "surface": "cli",
            "execution_mode": "headless",
            "os": capture_environment["os_name"],
            "build": profile["build"],
            "model": next(iter(models)),
            "configuration": next(iter(configurations)),
            "observer_schema_version": observer["schema_version"],
        },
        "measurement": actual["measurement"],
        "metric_evidence": metric_evidence,
    }
    score_release_run(survival, profile)
    return survival


def _privacy_screen(files: dict[str, bytes]) -> dict:
    findings = []
    scanned = 0
    for name, data in files.items():
        scanned += 1
        for label, pattern in (("home_path", HOME_PATH), ("personal_email", PRIVATE_EMAIL),
                               ("credential_marker", SECRET)):
            if pattern.search(data):
                findings.append({"file": name, "kind": label})
    return {"schema_version": "session-bench-pi-public-screen-v1", "files_scanned": scanned,
            "findings": findings, "review_state": "pending_independent_public_safety_review"}


# Digests that are not digests of bytes of the set, each with its reason.
_CATALOG = "digest of the provider model catalog that the harness printed at preflight; public vendor data, no operator data"
DIGEST_ALLOWLIST = {
    "preflight.json": {"model_catalog_sha256": _CATALOG},
    "capture-result.json": {"preflight.model_catalog_sha256": _CATALOG},
    "controller-state.json": {"preflight.model_catalog_sha256": _CATALOG},
}


def build(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError("Pi public candidate output must be a new directory outside the source packets")
    pairs, rows, receipts, packets = [], [], [], []
    output.mkdir(parents=True)
    for repetition in (1, 2, 3):
        packet = source / f"repetition-{repetition}"
        receipt_path = source / "replay-results" / f"repetition-{repetition}-coverage-receipt.json"
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        manifest_bytes = (packet / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        if (receipt["configuration_id"] != "pi" or receipt["repetition"] != repetition
                or receipt["manifest_sha256"] != sha(manifest_bytes)
                or receipt["diagnostics_sha256"] != sha(canonical(receipt["diagnostics"]))
                or manifest["configuration_id"] != "pi" or manifest["repetition"] != repetition
                or receipt["public_safe"] is not False or receipt["independent_reproduction"] is not False):
            raise ValueError("retained Pi receipt/packet pins or scope do not match")
        actual = receipt["diagnostics"]["intact"]
        if (actual["run_id"] != receipt["run_id"] or actual["repetition"] != repetition
                or not _all_resolved(actual["metrics"])):
            raise ValueError("Pi packet does not contain a complete 31-metric diagnostic")
        survival = _survival(packet, actual, repetition)
        pairs.append({"survival": survival, "format": actual["format_evidence"]})
        destination = output / "public-candidates" / actual["run_id"]
        shutil.copytree(packet, destination)
        receipts.append(receipt); packets.append(destination)
        copied = _snapshot_tree(destination)
        if copied != _snapshot_tree(packet):
            raise ValueError("complete Pi candidate copy differs from its pinned source packet")
        screen = _privacy_screen(copied)
        if screen["findings"]:
            raise ValueError(f"Pi candidate privacy screen found material requiring a reviewed transform: {screen['findings']}")
        (output / "privacy-screens").mkdir(exist_ok=True)
        (output / "privacy-screens" / f"repetition-{repetition}.json").write_bytes(encoded(screen))
        rows.append({"run_id": actual["run_id"], "repetition": repetition,
                     "manifest_sha256": sha(manifest_bytes),
                     "diagnostics_sha256": receipt["diagnostics_sha256"],
                     "resolved_metrics": len(actual["metrics"]), "packet_copy": "byte_identical",
                     "public_safety_review": "pending", "independent_reproduction": "pending"})
    bundle = encoded({"schema_version": PUBLIC_BUNDLE_SCHEMA, "configuration_id": "pi", "pairs": pairs})
    (output / "public-inputs-candidate.json").write_bytes(bundle)
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    check_public_packets(packets, receipts=receipts, extra_files=[output / "public-inputs-candidate.json"], allowlist=DIGEST_ALLOWLIST)
    summary = {"schema_version": "session-bench-pi-public-preparation-v1", "runs": rows,
               "public_bundle_sha256": sha(bundle), "public_safe": False,
               "independent_reproduction": False,
               "publication_eligible": False,
               "review_state": "pending_independent_public_safety_and_native_score_replay"}
    (output / "summary.json").write_bytes(encoded(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "artifacts/v1-expanded-preparation/pi-json-score-replay-v7")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
