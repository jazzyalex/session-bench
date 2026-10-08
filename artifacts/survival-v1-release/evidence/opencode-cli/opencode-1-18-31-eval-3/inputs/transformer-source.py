#!/usr/bin/env python3
"""Build invariant OpenCode candidates with all physical SQLite bytes retained.

This bounded transformer aliases home usernames only in non-SQLite inputs.
Any such string in a DB/WAL/SHM refuses the operation: SQL UPDATE cannot remove
private bytes from obsolete pages. No privacy approval or publication occurs.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets
from session_bench.score_replay import build_score_replay_package, replay_score_package, verify_score_packet_tamper_controls
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA
from session_bench.release_score import score_release_run

SQLITE_SUFFIXES = (".db", ".db-wal", ".db-shm")
HOMES = re.compile(rb"/Users/[A-Za-z0-9_-]+")
PRIVATE_EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@(?:gmail|icloud|outlook|hotmail)\.com")
SECRET = re.compile(rb"(?:(?:sk-ant-|sk-proj-|github_pat_|ghp_)[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def sha(data): return hashlib.sha256(data).hexdigest()
def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream: stream.write(data)


def transform_documents(original):
    aliases = {}
    for name, data in original.items():
        if PRIVATE_EMAIL.search(data) or SECRET.search(data):
            raise ValueError("account email or credential requires a separately reviewed transform")
        homes = set(HOMES.findall(data))
        if name.endswith(SQLITE_SUFFIXES) and homes:
            raise ValueError("private home string in physical SQLite family; preserve or rebuild all pages explicitly")
        for home in homes:
            aliases[home] = b"/Users/" + b"x" * (len(home) - 7)
    base = {}
    for name, data in original.items():
        if not name.endswith(SQLITE_SUFFIXES):
            for old, new in aliases.items(): data = data.replace(old, new)
        base[name] = data
    transformed = dict(base)
    for _ in range(len(original) + 1):
        digest_aliases = {sha(original[name]).encode(): sha(data).encode()
                          for name, data in transformed.items() if original[name] != data}
        revised = {}
        for name, data in base.items():
            if not name.endswith(SQLITE_SUFFIXES):
                for old, new in digest_aliases.items(): data = data.replace(old, new)
            revised[name] = data
        if revised == transformed: break
        transformed = revised
    else: raise ValueError("digest dependency graph did not converge")
    if any(len(original[name]) != len(data) for name, data in transformed.items()):
        raise ValueError("transformation changed physical byte counts")
    if any(original[name] != data for name, data in transformed.items() if name.endswith(SQLITE_SUFFIXES)):
        raise ValueError("physical SQLite bytes changed")
    if any(old != new and old in data for data in transformed.values() for old, new in aliases.items()):
        raise ValueError("private alias survived")
    return transformed, {"home_alias_count": len(aliases), "sqlite_files_unchanged": sum(name.endswith(SQLITE_SUFFIXES) for name in original)}


def public_survival(packet, actual, repetition, environment):
    observer = json.loads((packet / "inputs/observer.json").read_bytes())
    native = json.loads((packet / "native/decode.json").read_bytes())
    responses = [event for event in observer["events"] if event["kind"] == "assistant_response"]
    models = {event["fields"]["model_id"] for event in responses}
    configs = {event["fields"]["configuration"] for event in responses}
    if models != {"opencode/muse-spark-1.3-contributor-free"} or models != configs:
        raise ValueError("unstable captured model route")
    refs = []
    for metric in actual["measurement"]["metrics"]:
        key = metric["id"]
        ids = [event["id"] for event in observer["events"] if key in event.get("metric_ids", [])]
        if key == "revision.final_after_r2":
            ids += [relation["id"] for relation in observer["relations"] if relation["kind"] == "final_after"]
        if not ids: raise ValueError("missing independent observer locator " + key)
        refs.append({"metric_id": key, "observer_ids": ids, "native_locators": [
            {"artifact_id": "native:" + artifact["path"], "artifact_sha256": artifact["sha256"],
             "record_location": "complete selected session; copied SQLite DB/WAL/SHM; comparator joins exact native message/part IDs"}
            for artifact in native["artifacts"]]})
    profile = actual["format_evidence"]
    result = {"schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION, "protocol_version": "1.0-survival",
              "workload_version": "1.0-survival-workload", "rubric_version": "1.0-survival-rubric",
              "run_id": actual["run_id"], "configuration_id": "opencode-cli", "repetition": repetition,
              "capture_id": packet.name, "evaluation_id": "native-score-replay:" + packet.name,
              "observer": profile["observer"], "native_manifest": profile["native_manifest"],
              "decoder": {"id": "opencode-sqlite-v1", "sha256": sha((packet / "runtime/session_bench/adapters/opencode_decoder.py").read_bytes())},
              "identity": {"provider": "opencode", "harness": "opencode", "surface": "cli", "execution_mode": "headless",
                           "os": environment["os_name"], "build": profile["build"], "model": next(iter(models)),
                           "configuration": next(iter(configs)), "observer_schema_version": observer["schema_version"]},
              "measurement": actual["measurement"], "metric_evidence": refs}
    score_release_run(result, profile)
    return result


DIGEST_ALLOWLIST = {}


def build(source, output):
    receipts, packets = [], []
    source, output = Path(source), Path(output)
    if output.exists() or output.is_symlink(): raise ValueError("new destination required")
    original_trees = {n: _snapshot_tree(source / f"opencode-1-18-31-eval-{n}") for n in (1, 2, 3)}
    if platform.system() != "Darwin": raise ValueError("same-host macOS provenance requires this capture operator host")
    environment = {"schema_version": "session-bench-operator-environment-attestation-v1", "operator_id": "cohort_manifest",
                   "observed_at": datetime.now(timezone.utc).isoformat(), "os_name": "macOS", "platform_system": platform.system(),
                   "scope": "post-capture observation on the same host used by this ongoing operator session for these three fresh captures",
                   "capture_attempts": [f"opencode-1-18-31-eval-{n}" for n in (1, 2, 3)],
                   "claim_limit": "not a capture-time machine receipt or independently reacquired OS identity"}
    source_bytes = Path(__file__).read_bytes()
    summary, pairs = [], []
    for rep in (1, 2, 3):
        parent = source / f"opencode-1-18-31-eval-{rep}"; tree = original_trees[rep]
        parent_pin = sha(tree["manifest.json"])
        original = {name: data for name, data in tree.items() if name.startswith(("native/", "inputs/"))}
        transformed, counts = transform_documents(original)
        # The in-packet receipt carries no hash of a private original: with a
        # short alias, such a hash confirms a guessed name.
        private_receipt = {"schema_version": "session-bench-private-alias-transformation-v1", "parent_manifest_sha256": parent_pin,
                           "files": [{"path": name, "original_sha256": sha(original[name]), "transformed_sha256": sha(data)} for name, data in sorted(transformed.items())]}
        receipt = {"schema_version": "session-bench-public-alias-transformation-v2",
                   "original_is_private": True, "privacy_approval": "pending_independent_review",
                   "description": "Explicit sanitized derivative. Equal UTF-8 length home username alias in launch/proof JSON only; all native SQLite DB/WAL/SHM and root-proof SQLite physical bytes unchanged, every logical row/schema and canonical record byte retained. Transitive digest references updated.",
                   "aliases": counts, "transformer_source_sha256": sha(source_bytes),
                   "files": [{"path": name, "transformed_sha256": sha(data), "size_bytes": len(data)} for name, data in sorted(transformed.items())]}
        with tempfile.TemporaryDirectory(prefix="opencode-public-native-") as folder:
            native = Path(folder).resolve()
            for name, data in transformed.items():
                if name.startswith("native/"): write(native / name[7:], data)
            support = {name[7:]: data for name, data in transformed.items() if name.startswith("inputs/") and name not in ("inputs/workload.json", "inputs/observer.json", "inputs/context.json")}
            support.update({"public-transformation.json": canonical(receipt), "transformer-source.py": source_bytes,
                            "operator-environment.json": canonical(environment)})
            packet = output / parent.name
            build_score_replay_package(native, packet, workload_document=transformed["inputs/workload.json"],
                observer_document=transformed["inputs/observer.json"], context_document=transformed["inputs/context.json"],
                supporting_documents=support, source_root=parent / "runtime")
        pin = sha((packet / "manifest.json").read_bytes())
        after = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
        before = replay_score_package(parent, expected_manifest_sha256=parent_pin, os_sandboxed=True)
        if canonical(before["diagnostics"]["intact"]["metrics"]) != canonical(after["diagnostics"]["intact"]["metrics"]):
            raise ValueError("sanitization changed metric results")
        controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
        if controls["status"] != "passed": raise ValueError("candidate tamper controls failed")
        write(output / f"{parent.name}.producer-replay.json", canonical(after))
        receipts.append(after); packets.append(packet)
        write(output / f"{parent.name}.tamper.json", canonical(controls))
        write(output / f"{parent.name}.private-transformation.json", canonical(private_receipt))
        intact = after["diagnostics"]["intact"]
        pairs.append({"survival": public_survival(packet, intact, rep, environment), "format": intact["format_evidence"]})
        # A distributable handoff is a complete exact copy, not a semantic-only export.
        handoff = output / "public-handoff" / packet.name
        handoff.parent.mkdir(exist_ok=True)
        shutil.copytree(packet, handoff, symlinks=False)
        if _snapshot_tree(packet) != _snapshot_tree(handoff): raise ValueError("complete handoff differs")
        all_public = _snapshot_tree(packet)
        if any(str(Path.home()).encode() in data or PRIVATE_EMAIL.search(data) or SECRET.search(data) for data in all_public.values()):
            raise ValueError("private marker remains in complete candidate packet")
        row = {"run_id": parent.name, "repetition": rep, "packet": packet.name, "manifest_sha256": pin,
               "parent_manifest_sha256": parent_pin, "diagnostics_sha256": after["diagnostics_sha256"],
               "all_31_metric_rows_unchanged": True, "sqlite_physical_files_unchanged": counts["sqlite_files_unchanged"],
               "complete_handoff": "public-handoff/" + packet.name, "privacy_review_pending": True}
        summary.append(row); print(json.dumps(row), flush=True)
    bundle = canonical({"schema_version": PUBLIC_BUNDLE_SCHEMA, "configuration_id": "opencode-cli", "pairs": pairs})
    write(output / "public-inputs-candidate.json", bundle)
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason. No allowlist is needed.
    check_public_packets(packets, receipts=receipts, extra_files=[output / "public-inputs-candidate.json"], allowlist=DIGEST_ALLOWLIST)
    result = {"schema_version": "session-bench-sanitized-candidates-v1", "runs": summary,
              "public_bundle_sha256": sha(bundle), "public_safe": False, "independent_reproduction": False, "publication_eligible": False}
    write(output / "summary.json", canonical(result))
    for rep, original in original_trees.items():
        if original != _snapshot_tree(source / f"opencode-1-18-31-eval-{rep}"): raise ValueError("private parent changed")
    print(json.dumps(result), flush=True)
    return result

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(); build(args.source, args.output)
