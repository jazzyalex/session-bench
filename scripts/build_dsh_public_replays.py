#!/usr/bin/env python3
"""Prepare source-bound DSH private baselines and invariant public candidates.

No live acquisition, publication, or independent privacy approval is performed.
Original captures are read only; every output is a new explicitly named derivative.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from session_bench.native_replay import canonical, _snapshot_tree
from session_bench.public_digest_check import check_public_packets, read_public_set
from sanitize_codex_score_packets import instruction_phrases, require_no_instruction_phrase
from session_bench.dsh_native import read_physical
from session_bench.dsh_closure import PUBLIC_PARENTS, public_parent_manifest, root_observations
from session_bench.dsh_observer import bind_dsh_stdout_usage
from session_bench.dsh_sanitizer import aliases_for_home, sanitize
from session_bench.score_replay import SCHEMA, build_dsh_score_replay_package, replay_score_package, verify_score_packet_tamper_controls
from session_bench.release_replay import PUBLIC_BUNDLE_SCHEMA


# Digests that are not digests of bytes of the set, each with its reason.
DIGEST_ALLOWLIST = {
    "parent-capture-manifest.json": {"source_files": "digests of repository source files at capture time; public code, no operator data"},
    "manifest.json": {"platform_dependencies": "digest of the system Zstandard library that the reviewer installs; a public library file, no operator data"},
}


_MARKERS = ("[vendor instruction text removed", "[redacted unscored context]")


def obscured_strings(before, after, found):
    """Collect the original text of every string that the sanitizer replaced by a marker."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before:
            obscured_strings(before[key], after.get(key), found)
    elif isinstance(before, list) and isinstance(after, list):
        for old, new in zip(before, after):
            obscured_strings(old, new, found)
    elif isinstance(before, str) and isinstance(after, str) and before != after and after.startswith(tuple(marker[:max(1, min(len(marker), len(after)))] for marker in _MARKERS)):
        found.append(before)


def obscured(value, aliases, found):
    """Sanitize one document and record what was obscured, for the final guard."""
    result = sanitize(value, aliases=aliases)
    obscured_strings(value, result, found)
    return result


def encoded(value): return canonical(value) + b"\n"
def sha(data): return hashlib.sha256(data).hexdigest()
def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def root_proof():
    environment = (ROOT / "artifacts/v1-expanded-preparation/operator-environment-attestation.json").read_bytes()
    source = (ROOT / "scripts/run_deepseek_survival.py").read_bytes()
    adapter = (ROOT / "session_bench/adapters/deepseek_harness.py").read_bytes()
    captures = []
    caches = {}
    for repetition, key in enumerate(PUBLIC_PARENTS, 1):
        run = ROOT / "artifacts/survival-v1-runs" / key
        capture = run / "qualification-v5"
        if sha(public_parent_manifest((capture / "manifest.json").read_bytes())) != PUBLIC_PARENTS[key]:
            raise ValueError("captured parent changed")
        native = capture / "native/session.v4.jsonl.zstd"
        raw, physical = read_physical(native)
        header = json.loads(raw.splitlines()[0])
        home = _snapshot_tree(run / "dsh-home")
        cache = home[f'storages/session_projcache/sessions/{header["id"]}.json']
        caches[key] = cache
        start = json.loads((run / "capture-start.json").read_bytes())
        end = json.loads((run / "capture-result.json").read_bytes())
        # Financial/account details are not evidence of root discovery. Retain
        # the exact capture parent pin and only the necessary receipt fields.
        start = {name: start[name] for name in ("attempt_id", "started_at", "version", "before_sha256")}
        end = {name: end[name] for name in ("attempt_id", "started_at", "finished_at", "version", "status", "turns", "before_sha256", "after_sha256")}
        # The original stdout and cache differ from their published forms only
        # by the home name, so their digests would confirm a guessed name.
        end["turns"] = [{name: value for name, value in turn.items() if name != "stdout_sha256"} for turn in end["turns"]]

        captures.append({"attempt_id": key, "repetition": repetition, "public_parent_capture_manifest_sha256": PUBLIC_PARENTS[key],
                         "capture_start": start, "capture_result": end, "native_header": header, "workspace": header["cwd"],
                         # The home files are not published (the native file only as a plain
                         # derivative), so no digest of them is: only an empty file keeps its digest.
                         "native_sha256": "0" * 64, "cache_sha256": "0" * 64,
                         "home_inventory": [{"path": path, "sha256": sha(data) if not data else "0" * 64, "size_bytes": len(data)} for path, data in sorted(home.items())]})
    proof = {"schema_version": "session-bench-dsh-three-root-evidence-v1", "captures": captures,
             "capture_source_sha256": sha(source), "adapter_source_sha256": sha(adapter), "operator_environment_sha256": sha(environment),
             "provenance_limit": "retrospective source/capture/full-home inventory and operator-witnessed same-host evidence; no capture-time machine receipt or independently reacquired root"}
    data = encoded(proof)
    observations = root_observations(data, source_document=source, adapter_document=adapter, environment_document=environment)
    return data, observations, source, environment, caches


def assertion(inputs, native_data, cache_data, key, repetition, mode):
    return encoded({"schema_version": "session-bench-dsh-score-closure-v1", "run_id": key, "repetition": repetition,
                    "mode": mode, "native_sha256": sha(native_data), "cache_sha256": sha(cache_data),
                    "input_sha256": {name: sha(data) for name, data in sorted(inputs.items())}})


def build(output):
    if output.exists(): raise ValueError("output exists; use a new successor root")
    output.mkdir(parents=True)
    proof, observations, capture_source, environment, caches = root_proof()
    aliases = aliases_for_home(Path.home())
    results, pairs, receipts, blanked_texts = [], [], [], []
    for repetition, key in enumerate(PUBLIC_PARENTS, 1):
        capture = ROOT / "artifacts/survival-v1-runs" / key / "qualification-v5"
        native = capture / "native/session.v4.jsonl.zstd"
        native_data = native.read_bytes()
        raw, _ = read_physical(native)
        original_rows = [json.loads(line) for line in raw.splitlines()]
        state = json.loads((capture / "capture-result.json").read_bytes())
        context = {"schema_version": SCHEMA, "configuration_id": "deepseek-harness-cli", "repetition": repetition, "run_id": key,
                   "build": state["version"], "collected_on": state["started_at"][:10], "result_id": "dsh-result:" + key,
                   "observer_kind": "canonical", "complete_record_family": True, "complete_root": True,
                   "required_companions": [], "root_repetitions": observations, "capture_assertion_path": "inputs/capture-assertion.json", "claude_projection": None}
        inputs = {"parent-capture-manifest.json": (capture / "manifest.json").read_bytes(),
                  "parent-observer.json": (capture / "observer.json").read_bytes(),
                  "native-companions/session-projcache.json": caches[key], "root-evidence.json": proof,
                  "capture-source.py": capture_source, "operator-environment.json": environment,
                  **{name: (capture / name).read_bytes() for name in ("helper-ledger.jsonl", "bench_check.py", "r1.stdout.jsonl", "r2.stdout.jsonl")}}
        observer = encoded(bind_dsh_stdout_usage(json.loads(inputs["parent-observer.json"]), stdout_by_turn={n: inputs[f"r{n}.stdout.jsonl"] for n in (1, 2)}))
        inputs["capture-assertion.json"] = assertion(inputs, native_data, caches[key], key, repetition, "captured_original")
        private = output / "private" / key
        build_dsh_score_replay_package(native, private, workload_document=(capture / "workload.json").read_bytes(), observer_document=observer,
                                      context_document=encoded(context), supporting_documents=inputs)
        private_pin = sha((private / "manifest.json").read_bytes())
        baseline = replay_score_package(private, expected_manifest_sha256=private_pin, os_sandboxed=True)
        write(output / "receipts" / f"{key}.private.json", encoded(baseline))
        derived_rows = [obscured(row, aliases, blanked_texts) for row in original_rows]
        derived_native = b"".join(canonical(row) + b"\n" for row in derived_rows)
        cache = obscured(json.loads(caches[key]), aliases, blanked_texts)
        derived_cache = encoded(cache)
        derived = dict(inputs)
        del derived["capture-assertion.json"]
        derived["native-companions/session-projcache.json"] = derived_cache
        derived["parent-observer.json"] = encoded(obscured(json.loads(inputs["parent-observer.json"]), aliases, blanked_texts))
        derived["parent-capture-manifest.json"] = public_parent_manifest(inputs["parent-capture-manifest.json"])
        derived["root-evidence.json"] = encoded(obscured(json.loads(proof), aliases, blanked_texts))
        for n in (1, 2):
            rows = [json.loads(line) for line in inputs[f"r{n}.stdout.jsonl"].splitlines()]
            derived[f"r{n}.stdout.jsonl"] = b"".join(canonical(obscured(row, aliases, blanked_texts)) + b"\n" for row in rows)
        transform = {"schema_version": "session-bench-dsh-sanitized-derivative-v1", "privacy_approval": "pending_independent_review",
                     # No digest of a private file that a reader could rebuild from
                     # public bytes plus a guessed name, at any depth.
                     "public_parent_capture_manifest_sha256": PUBLIC_PARENTS[key],
                     # The private compressed native file is not published; its digest is not either.
                     "parent_native_sha256": "0" * 64,
                     "derived_observer_base_sha256": sha(derived["parent-observer.json"]),
                     "serialization": "decompress captured Zstandard; preserve every native record/order/type/identity; canonical plain JSONL derivative",
                     "canonical_record_bytes_unchanged": True, "scored_metric_values_unchanged": True,
                     "metric_rows_sha256": sha(canonical(baseline["diagnostics"]["intact"]["metrics"])),
                     # No hash of an original alias source or native record: with a
                     # short name, such a hash confirms a guess.
                     "aliases": [{"replacement": target, "utf8_bytes": len(source.encode())} for source, target in aliases.items()],
                     "unscored_context_redaction": "explicit system and runtime-context/skill-catalog textual leaves only; fixed encoded JSON string byte length",
                     "transformer_source_sha256": sha((ROOT / "session_bench/dsh_sanitizer.py").read_bytes()),
                     "native_records": [{"line": n, "derived_canonical_sha256": sha(canonical(after)),
                                         "original_canonical_bytes": len(canonical(before)), "derived_canonical_bytes": len(canonical(after))} for n, (before, after) in enumerate(zip(original_rows, derived_rows), 1)],
                     "cache_original_canonical_bytes": len(canonical(json.loads(caches[key]))), "cache_derived_canonical_bytes": len(canonical(cache))}
        derived["transformation.json"] = encoded(transform)
        derived["transformer-source.py"] = (ROOT / "session_bench/dsh_sanitizer.py").read_bytes()
        derived_observer = encoded(bind_dsh_stdout_usage(json.loads(derived["parent-observer.json"]), stdout_by_turn={n: derived[f"r{n}.stdout.jsonl"] for n in (1, 2)}))
        derived["capture-assertion.json"] = assertion(derived, derived_native, derived_cache, key, repetition, "sanitized_derivative")
        public = output / "public-candidates" / key
        with tempfile.TemporaryDirectory(prefix="dsh-public-source-") as directory:
            plain = Path(directory).resolve() / "session.v4.jsonl"
            plain.write_bytes(derived_native)
            build_dsh_score_replay_package(plain, public, workload_document=(capture / "workload.json").read_bytes(), observer_document=derived_observer,
                                          context_document=encoded(context), supporting_documents=derived)
        public_pin = sha((public / "manifest.json").read_bytes())
        receipt = replay_score_package(public, expected_manifest_sha256=public_pin, os_sandboxed=True)
        if receipt["diagnostics"]["intact"]["metrics"] != baseline["diagnostics"]["intact"]["metrics"]:
            raise ValueError("public derivative changed a scored metric")
        privacy_files = _snapshot_tree(public)
        if any(Path.home().name.encode() in data or str(Path.home()).encode() in data for data in privacy_files.values()):
            raise ValueError("personal path/name literal remains in public candidate")
        controls = verify_score_packet_tamper_controls(public, expected_manifest_sha256=public_pin)
        write(output / "receipts" / f"{key}.candidate.json", encoded(receipt))
        receipts.append(receipt)
        write(output / "receipts" / f"{key}.tamper.json", encoded(controls))
        actual = receipt["diagnostics"]["intact"]
        survival = json.loads((capture / "survival-evidence.json").read_bytes())
        survival.update(measurement=actual["measurement"], observer=actual["format_evidence"]["observer"], native_manifest=actual["format_evidence"]["native_manifest"],
                        decoder={"id": "dsh-native-v4-with-projection-cache-v7", "sha256": sha((public / "runtime/session_bench/dsh_live.py").read_bytes())})
        survival["identity"]["os"] = json.loads(environment)["os_name"]
        for evidence in survival["metric_evidence"]:
            row = next(row for row in actual["measurement"]["metrics"] if row["id"] == evidence["metric_id"])
            evidence["observer_ids"] = ([event["id"] for event in json.loads(derived_observer)["events"] if row["id"] in event.get("metric_ids", [])]
                                         or [relation["id"] for relation in json.loads(derived_observer)["relations"] if row["id"] == "revision.final_after_r2" and relation["kind"] == "final_after"]
                                         or [actual["format_evidence"]["observer"]["id"]])
            evidence["native_locators"] = [{"artifact_id": "native:session.v4.jsonl", "artifact_sha256": sha(derived_native), "record_location": "complete-native-v4-generation"}]
            if row["id"] == "attribution.reconciliation":
                evidence["native_locators"].append({"artifact_id": "native-companion:session-projcache.json", "artifact_sha256": sha(derived_cache), "record_location": "record.rows.tokenUsage.val.totals"})
        pairs.append({"survival": survival, "format": actual["format_evidence"]})
        results.append({"run_id": key, "repetition": repetition, "private_manifest_sha256": private_pin, "public_candidate_manifest_sha256": public_pin,
                        "candidate_diagnostics_sha256": receipt["diagnostics_sha256"], "metric_rows_sha256": transform["metric_rows_sha256"],
                        "resolved_metrics": sum(row["state"] != "unresolved" for row in actual["metrics"]), "privacy_approval": "pending_independent_review"})
    bundle = encoded({"schema_version": PUBLIC_BUNDLE_SCHEMA, "configuration_id": "deepseek-harness-cli", "pairs": pairs})
    write(output / "public-inputs-candidate.json", bundle)
    # No file of a public packet may still hold a phrase of the text the sanitizer obscured.
    require_no_instruction_phrase(read_public_set([output / "public-candidates" / key for key in PUBLIC_PARENTS], [output / "public-inputs-candidate.json"]),
                                  instruction_phrases(blanked_texts))
    # Every digest of the set must be public bytes, replay output or allowlisted with a reason.
    check_public_packets([output / "public-candidates" / key for key in PUBLIC_PARENTS], receipts=receipts,
                         extra_files=[output / "public-inputs-candidate.json"], allowlist=DIGEST_ALLOWLIST)
    summary = {"schema_version": "session-bench-dsh-public-preparation-v1", "runs": results, "public_bundle_sha256": sha(bundle),
               "publication_eligible": False, "independently_reproduced": False, "privacy_approval": "pending_independent_review"}
    write(output / "summary.json", encoded(summary))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output.resolve())
