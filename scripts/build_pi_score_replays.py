#!/usr/bin/env python3
"""Package private Pi JSON-observer/native score diagnostics from retained captures."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.pi_score_inputs import observer_from_capture_documents, qualify_capture_documents
from session_bench.score_replay import build_score_replay_package, canonical


def _pi_format_documents(expected_version: str) -> dict[str, bytes]:
    executable = shutil.which("pi")
    if not executable:
        raise ValueError("Pi executable is unavailable for bundle-local format documentation")
    package_root = Path(executable).resolve().parents[2]
    package_raw = (package_root / "package.json").read_bytes()
    package = json.loads(package_raw)
    if (package.get("name") != "@earendil-works/pi-coding-agent"
            or package.get("version") != expected_version):
        raise ValueError("installed Pi package differs from the completed capture version")
    documents = {"package.json": package_raw,
                 "docs/session-format.md": (package_root / "docs/session-format.md").read_bytes(),
                 "docs/message-types.md": (package_root / "docs/message-types.md").read_bytes()}
    index = {"schema_version": "session-bench-pi-format-documents-v1",
             "package_name": package["name"], "pi_version": expected_version,
             "documents": [{"path": name, "sha256": hashlib.sha256(raw).hexdigest()}
                           for name, raw in sorted(documents.items())]}
    documents["index.json"] = canonical(index) + b"\n"
    return documents


def _files(root: Path) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Pi capture root must be an ordinary directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Pi capture contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Pi capture contains a special file")
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    if not result:
        raise ValueError("Pi capture is empty")
    return result


def build_pi_replay(capture_root: Path, destination: Path, *, root_repetitions=None,
                    repetition_captures=None) -> dict:
    documents = _files(capture_root)
    identity = qualify_capture_documents(documents)
    plan = json.loads(documents["plan.json"])
    result = json.loads(documents["capture-result.json"])
    workload_document = documents["workload-instance.json"]
    observer_document = canonical(observer_from_capture_documents(documents))
    native_bytes = documents["turn-r2/native/session.jsonl"]
    if hashlib.sha256(native_bytes).hexdigest() != identity["native_sha256"]:
        raise ValueError("Pi final native transcript differs from qualification digest")

    inventory = {"format": "pi-native-session-jsonl-v3", "artifacts": [{
        "id": "pi-session", "path": "session.jsonl", "sha256": hashlib.sha256(native_bytes).hexdigest(),
        "size_bytes": len(native_bytes), "depends_on": [],
    }]}
    assertion = {
        "schema_version": "session-bench-pi-score-capture-v1",
        "attempt_id": identity["run_id"], "session_id": identity["session_id"],
        "repetition": identity["repetition"], "capture_environment": identity["capture_environment"],
        "capture_documents": [{"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
                              for name, raw in sorted(documents.items())],
    }
    started_ns = result["turns"][0]["launch"]["started_ns"]
    collected_on = datetime.fromtimestamp(started_ns / 1_000_000_000, timezone.utc).date().isoformat()
    pi_version = result.get("preflight", {}).get("version")
    format_documents = _pi_format_documents(pi_version)
    root_proof = identity.get("root_proof") or {}
    context = {
        "schema_version": "session-bench-native-score-replay-v1",
        "configuration_id": "pi", "repetition": identity["repetition"],
        "run_id": identity["run_id"], "build": str(plan["executable"]) + " " + str(result["preflight"]["version"]),
        "collected_on": collected_on, "result_id": identity["run_id"] + "/capture-result.json",
        "observer_kind": "pi-json-capture-v1", "complete_record_family": identity["complete_record_family"],
        "complete_root": identity["complete_root"], "required_companions": root_proof.get("required_companions", []), "root_repetitions": root_repetitions,
        "capture_assertion_path": "inputs/pi-capture-assertion.json", "claude_projection": None,
    }
    supporting = {"pi-capture-assertion.json": canonical(assertion)}
    supporting.update({"pi-format/" + name: raw for name, raw in format_documents.items()})
    supporting.update({"capture/" + name: raw for name, raw in documents.items()})
    if root_repetitions is not None:
        if not isinstance(repetition_captures, dict) or set(repetition_captures) != {1, 2, 3}:
            raise ValueError("Pi stable-root scoring requires all three qualified repetition captures")
        root_index = {"schema_version": "session-bench-pi-root-repetitions-v1", "runs": []}
        for repetition in (1, 2, 3):
            run_documents = repetition_captures[repetition]
            run_plan = json.loads(run_documents["plan.json"])
            run_result = json.loads(run_documents["capture-result.json"])
            run_identity = qualify_capture_documents(run_documents)
            run_started_ns = run_result["turns"][0]["launch"]["started_ns"]
            run_index = {"repetition": repetition, "attempt_id": run_identity["run_id"],
                "session_id": run_identity["session_id"],
                "root_capture_sha256": hashlib.sha256(run_documents["native-root/root-capture.json"]).hexdigest(),
                "collected_on": datetime.fromtimestamp(run_started_ns / 1_000_000_000, timezone.utc).date().isoformat(),
                "pi_version": run_result["preflight"]["version"],
                "capture_documents": [{"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
                                      for name, raw in sorted(run_documents.items())]}
            root_index["runs"].append(run_index)
            if repetition != identity["repetition"]:
                supporting.update({f"root-repetitions/repetition-{repetition}/capture/{name}": raw
                                   for name, raw in run_documents.items()})
        supporting["pi-root-repetitions.json"] = canonical(root_index)
    scratch_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="bench-pi-score-source-", dir=scratch_parent) as temporary:
        native_root = Path(temporary) / "native"
        native_root.mkdir()
        (native_root / "session.jsonl").write_bytes(native_bytes)
        (native_root / "decode.json").write_bytes(canonical(inventory) + b"\n")
        manifest = build_score_replay_package(
            native_root, destination,
            workload_document=workload_document,
            observer_document=observer_document,
            context_document=canonical(context), supporting_documents=supporting,
            source_root=ROOT,
        )
    return {"attempt_id": identity["run_id"], "repetition": identity["repetition"],
            "package": str(destination), "manifest_sha256": hashlib.sha256((destination / "manifest.json").read_bytes()).hexdigest(),
            "expected_diagnostics_sha256": manifest["expected_diagnostics_sha256"],
            "capture_documents": len(documents), "complete_root": identity["complete_root"], "score_eligible": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attempt_ids", nargs="+", help="retained Pi capture attempt IDs")
    parser.add_argument("--output-root", type=Path,
                        default=ROOT / "artifacts/v1-expanded-preparation/pi-json-score-replay-v1")
    args = parser.parse_args()
    summaries = []
    captures = {}
    attempt_ids = set()
    for attempt_id in args.attempt_ids:
        if not attempt_id.startswith("pi-") or Path(attempt_id).name != attempt_id:
            raise SystemExit("attempt IDs must be bounded pi-* path components")
        if attempt_id in attempt_ids:
            raise SystemExit("duplicate Pi attempt ID")
        attempt_ids.add(attempt_id)
        capture = ROOT / "artifacts/v1-expanded-preparation/live-captures" / attempt_id
        documents = _files(capture)
        identity = qualify_capture_documents(documents)
        repetition = identity["repetition"]
        if repetition in captures:
            raise SystemExit("duplicate Pi repetition")
        captures[repetition] = (capture, documents, identity)
    complete_repetitions = (set(captures) == {1, 2, 3}
                            and all(captures[n][2]["complete_root"] for n in (1, 2, 3)))
    root_repetitions = None
    repetition_captures = None
    if complete_repetitions:
        repetition_captures = {n: captures[n][1] for n in (1, 2, 3)}
    for repetition in sorted(captures):
        capture, _documents, _identity = captures[repetition]
        destination = args.output_root / f"repetition-{repetition}"
        if complete_repetitions:
            root_repetitions = [{"repetition": n,
                "root_locator": (f"inputs/capture/plan.json:session_dir passed as Pi --session-dir"
                                 if n == repetition else
                                 f"inputs/root-repetitions/repetition-{n}/capture/plan.json:session_dir passed as Pi --session-dir"),
                "isolated_discovery": True, "personal_history_scanned": False}
                for n in (1, 2, 3)]
        summaries.append(build_pi_replay(capture, destination,
            root_repetitions=root_repetitions, repetition_captures=repetition_captures))
    print(json.dumps({"schema_version": "session-bench-pi-score-replay-build-v1", "runs": summaries},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
