"""Source-bound DSH closure and retrospective three-root provenance validation.

Root evidence combines retained capture receipts/header identities, full isolated
home inventories, launcher/source binding, and the named operator's post-capture
host attestation. It is not a contemporaneous environment receipt or independent
root reacquisition. Sanitized derivatives name a pinned private replay parent and
carry per-record canonical-byte invariance receipts; their privacy approval is
always pending until a separate reviewer approves the complete handoff.
"""
import hashlib
import json
from datetime import datetime

from .dsh_live import DSHSemanticError
from .dsh_live import strict_json
from .dsh_observer import bind_dsh_stdout_usage
from .native_replay import canonical

# Digests of the captured parent manifests with their observer digest zeroed.
# The original observer can be rebuilt from public data with a guessed home
# name. Its digest, and any digest computed over it (the private manifest
# digest included), must therefore never appear in source or in a packet.
PUBLIC_PARENTS = {
    "dsh-cal-20260929-2": "fefaabf923b520070c49ddb2ed85f2dc564420e5432aa4be2605e199a6fc3fa8",
    "dsh-eval-20260929-1": "200a2d173fded5bbcb6db8be429e3526a6862b70d2a43eb7579f62b715967a82",
    "dsh-eval-20260929-2": "dca70ff89ae53a3531dc00e6b4cbd3c09d6c2273d51fc1cd6d61bb8f3876d194",
}


def _earlier_public_parent(key):
    """The pin of the earlier public form, known only to the repository verifier.

    Packets built before 2026-10-05 name it in their root evidence. The pins
    live in a module that is not copied into a packet, so a new packet neither
    holds nor accepts them.
    """
    try:
        from .dsh_earlier_public_parents import EARLIER_PUBLIC_PARENTS
    except ImportError:
        return None
    return EARLIER_PUBLIC_PARENTS.get(key)


# Captured files whose bytes are published unchanged. Every other captured
# file is public except for one low-entropy private value (a home name, an
# account balance), so its digest confirms a guess. The compressed native file
# is not published either (the public packet holds its plain derivative), so
# its digest is not a digest of public bytes and is zeroed too.
_PUBLIC_PARENT_FILES = ("bench_check.py", "helper-ledger.jsonl", "workload.json")


def public_parent_manifest(data: bytes) -> bytes:
    """The captured parent manifest with every guess-confirming digest zeroed at equal length."""
    value = json.loads(data)
    native = value.get("native_sha256")
    private = [row["sha256"] for row in value["files"] if row["path"] not in _PUBLIC_PARENT_FILES]
    private.append(value["parent_manifest_sha256"])
    for digest in dict.fromkeys(private):
        if set(digest) == {"0"}:
            continue
        # The native digest is stated twice: in the file list and as ``native_sha256``.
        if data.count(digest.encode()) != (2 if digest == native else 1):
            raise DSHSemanticError("DSH parent manifest digest is ambiguous")
        data = data.replace(digest.encode(), b"0" * 64)
    if isinstance(native, str) and set(native) != {"0"} and native.encode() in data:
        raise DSHSemanticError("DSH parent manifest still states the private native digest")
    return data


def root_observations(document, *, source_document, adapter_document, environment_document):
    proof = json.loads(document)
    environment = json.loads(environment_document)
    if (proof.get("schema_version") != "session-bench-dsh-three-root-evidence-v1"
            or proof.get("capture_source_sha256") != hashlib.sha256(source_document).hexdigest()
            or proof.get("adapter_source_sha256") != hashlib.sha256(adapter_document).hexdigest()
            or proof.get("operator_environment_sha256") != hashlib.sha256(environment_document).hexdigest()
            or environment.get("schema_version") != "session-bench-operator-environment-attestation-v1"
            or environment.get("os_name") != "macOS" or environment.get("platform_system") != "Darwin"
            or set(environment.get("capture_attempts", [])) != set(PUBLIC_PARENTS)):
        raise DSHSemanticError("DSH root proof lacks source and same-host operator provenance")
    # The actual source bindings are retained for reviewer inspection. Verify
    # the critical fresh-root launch/data boundary in the copied source too.
    if b"run.mkdir(parents=True, exist_ok=False)" not in source_document or b"build_headless_launch_plan(run, workspace" not in source_document or b"run_headless(plan," not in source_document:
        raise DSHSemanticError("DSH capture source does not establish the isolated launch boundary")
    if b"isolated DSH_HOME must be fresh and absent before launch" not in adapter_document or b'env={"DSH_HOME": str(dsh_home)}' not in adapter_document:
        raise DSHSemanticError("DSH adapter source lacks fresh isolated home enforcement")
    captures = proof.get("captures")
    if not isinstance(captures, list) or len(captures) != 3 or {row.get("attempt_id") for row in captures} != set(PUBLIC_PARENTS):
        raise DSHSemanticError("DSH root proof does not cover exactly three retained captures")
    result = []
    sessions = set()
    for repetition, row in enumerate(captures, 1):
        key = row["attempt_id"]
        if type(row.get("repetition")) is not int or row.get("repetition") != repetition or row.get("public_parent_capture_manifest_sha256") not in {PUBLIC_PARENTS[key], _earlier_public_parent(key)} or "parent_capture_manifest_sha256" in row:
            raise DSHSemanticError("DSH root repetition is not bound to its pinned capture")
        start, end, header = row["capture_start"], row["capture_result"], row["native_header"]
        if (start.get("attempt_id") != key or end.get("attempt_id") != key or end.get("status") != "captured_pending_qualification"
                or start.get("started_at") != end.get("started_at") or header.get("version") != 4
                or header.get("cwd") != row.get("workspace") or header.get("id") in sessions
                or header.get("isSeeded") is not False or header.get("delegationDepth") != 0):
            raise DSHSemanticError("DSH root capture/header lifecycle mismatch")
        sessions.add(header["id"])
        try:
            first = datetime.fromisoformat(start["started_at"]).timestamp() * 1000
            last = datetime.fromisoformat(end["finished_at"]).timestamp() * 1000
        except (KeyError, TypeError, ValueError) as error:
            raise DSHSemanticError("DSH root capture timestamps are malformed") from error
        if type(header.get("createdAt")) is not int or not first <= header["createdAt"] <= last:
            raise DSHSemanticError("DSH native header predates or exceeds its fresh capture window")
        turns = end.get("turns", [])
        if len(turns) != 2 or [turn.get("turn_id") for turn in turns] != ["turn-r1", "turn-r2"] or any(type(turn.get("exit_code")) is not int or turn.get("exit_code") != 0 or turn.get("session_ids") != [header["id"]] or turn.get("canary_present") is not True for turn in turns):
            raise DSHSemanticError("DSH root capture did not finish two observed turns")
        inventory = row.get("home_inventory", [])
        paths = [item.get("path") for item in inventory]
        logs = [item for item in inventory if item.get("path", "").endswith("/session.v4.jsonl.zstd")]
        locks = [item for item in inventory if item.get("path", "").endswith("/session.lock")]
        caches = [item for item in inventory if item.get("path") == f'storages/session_projcache/sessions/{header["id"]}.json']
        if len(paths) != len(set(paths)) or len(logs) != 1 or len(locks) != 1 or locks[0].get("size_bytes") != 0 or len(caches) != 1 or header["id"] not in logs[0]["path"] or logs[0].get("sha256") != row.get("native_sha256") or caches[0].get("sha256") != row.get("cache_sha256"):
            raise DSHSemanticError("DSH root home inventory is incomplete or foreign-session-bearing")
        if any("sessions/" in path and path not in {logs[0]["path"], locks[0]["path"], caches[0]["path"]} for path in paths):
            raise DSHSemanticError("DSH root inventory includes an unqualified session-bearing family")
        runtime = {".anonymous-user-id", "profiles/headless/cordis.yml", "profiles/headless/cordis.patch.yml", "profiles/headless/package.json", "profiles/headless/pnpm-workspace.yaml"}
        if set(paths) != runtime | {logs[0]["path"], locks[0]["path"], caches[0]["path"]}:
            raise DSHSemanticError("DSH root inventory includes an unknown persistent family")
        result.append({"repetition": repetition, "root_locator": f'capture:{key}:DSH_HOME/sessions',
                       "isolated_discovery": True, "personal_history_scanned": False})
    return result


def validate_dsh_closure(contents, context, native_inventory):
    assertion = json.loads(contents[context["capture_assertion_path"]])
    key = context["run_id"]
    parent_bytes = contents.get("inputs/parent-capture-manifest.json", b"")
    if (assertion.get("mode") == "sanitized_derivative") != (b'"sha256": "' + b"0" * 64 in parent_bytes or b'"sha256":"' + b"0" * 64 in parent_bytes):
        raise DSHSemanticError("DSH sanitized derivative must carry the parent manifest without its observer digest")
    if key not in PUBLIC_PARENTS or hashlib.sha256(public_parent_manifest(parent_bytes) if parent_bytes else b"").hexdigest() != PUBLIC_PARENTS[key] or assertion.get("run_id") != key or assertion.get("repetition") != context["repetition"]:
        raise DSHSemanticError("DSH closure differs from pinned captured parent")
    parent = json.loads(parent_bytes)
    indexed = {row["path"]: row for row in parent["files"]}
    mode = assertion.get("mode")
    if hashlib.sha256(contents["inputs/workload.json"]).hexdigest() != indexed["workload.json"]["sha256"]:
        raise DSHSemanticError("DSH closure observer/workload parents are not captured bytes")
    if mode == "captured_original" and hashlib.sha256(contents["inputs/parent-observer.json"]).hexdigest() != indexed["observer.json"]["sha256"]:
        raise DSHSemanticError("DSH original observer base differs from captured bytes")
    if contents["inputs/bench_check.py"] != contents["runtime/fixtures/scenarios/survival-v1/workload/fixture_project/bench_check.py"] or hashlib.sha256(contents["inputs/helper-ledger.jsonl"]).hexdigest() != indexed["helper-ledger.jsonl"]["sha256"]:
        raise DSHSemanticError("DSH protected helper provenance changed")
    for name, digest in assertion.get("input_sha256", {}).items():
        if hashlib.sha256(contents.get("inputs/" + name, b"")).hexdigest() != digest:
            raise DSHSemanticError("DSH closed supporting input digest mismatch")
    observer = bind_dsh_stdout_usage(json.loads(contents["inputs/parent-observer.json"]), stdout_by_turn={number: contents[f"inputs/r{number}.stdout.jsonl"] for number in (1, 2)})
    if json.loads(contents["inputs/observer.json"]) != observer:
        raise DSHSemanticError("DSH canonical observer differs from stdout-only derivation")
    if len(native_inventory["artifacts"]) != 1 or native_inventory["artifacts"][0]["sha256"] != assertion.get("native_sha256") or hashlib.sha256(contents.get("inputs/native-companions/session-projcache.json", b"")).hexdigest() != assertion.get("cache_sha256"):
        raise DSHSemanticError("DSH native/cache closure digest mismatch")
    observations = root_observations(contents["inputs/root-evidence.json"], source_document=contents["inputs/capture-source.py"],
                                     adapter_document=contents["runtime/session_bench/adapters/deepseek_harness.py"], environment_document=contents["inputs/operator-environment.json"])
    if observations != context["root_repetitions"]:
        raise DSHSemanticError("DSH root rows differ from verified retained evidence")
    if mode == "captured_original":
        if assertion["native_sha256"] != parent["native_sha256"] or any(hashlib.sha256(contents[f"inputs/r{number}.stdout.jsonl"]).hexdigest() != indexed[f"r{number}.stdout.jsonl"]["sha256"] for number in (1, 2)):
            raise DSHSemanticError("DSH original closure changed captured native/stdout")
    elif mode == "sanitized_derivative":
        transform = json.loads(contents["inputs/transformation.json"])
        if transform.get("schema_version") != "session-bench-dsh-sanitized-derivative-v1" or transform.get("privacy_approval") != "pending_independent_review" or transform.get("parent_native_sha256") != parent["native_sha256"] or transform.get("canonical_record_bytes_unchanged") is not True or transform.get("scored_metric_values_unchanged") is not True:
            raise DSHSemanticError("DSH derivative lacks explicit parent and invariance provenance")
        if "parent_observer_sha256" in transform or transform.get("derived_observer_base_sha256") != hashlib.sha256(contents["inputs/parent-observer.json"]).hexdigest():
            raise DSHSemanticError("DSH observer alias derivative is not bound to captured parent")
        artifact = native_inventory["artifacts"][0]
        if artifact["path"] != "session.v4.jsonl":
            raise DSHSemanticError("DSH sanitized derivative must be explicitly serialized plain JSONL")
        records = [strict_json(line) for line in contents["native/" + artifact["path"]].splitlines()]
        proofs = transform.get("native_records", [])
        if len(proofs) != len(records):
            raise DSHSemanticError("DSH derivative did not preserve native record population")
        for number, (record, evidence) in enumerate(zip(records, proofs), 1):
            raw = canonical(record)
            if evidence.get("line") != number or evidence.get("original_canonical_bytes") != len(raw) or evidence.get("derived_canonical_bytes") != len(raw) or evidence.get("derived_canonical_sha256") != hashlib.sha256(raw).hexdigest():
                raise DSHSemanticError("DSH derivative changed a canonical native record byte count")
        cache = json.loads(contents["inputs/native-companions/session-projcache.json"])
        if transform.get("cache_original_canonical_bytes") != len(canonical(cache)) or transform.get("cache_derived_canonical_bytes") != len(canonical(cache)):
            raise DSHSemanticError("DSH derivative changed canonical native companion bytes")
    else:
        raise DSHSemanticError("DSH closure must distinguish original and sanitized derivative")
    return True, True
