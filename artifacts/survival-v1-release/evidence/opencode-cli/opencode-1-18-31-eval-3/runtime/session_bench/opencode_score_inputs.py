"""Validate source-bound fresh OpenCode roots and regenerate format evidence.

Capture provenance is supplied by retained acquisition receipts. Verification
checks their closed bytes and semantic consistency; it does not independently
witness collection or confer public/privacy/reproduction approval.
"""
import copy
import hashlib
import json
from pathlib import Path
import posixpath
import re
import tempfile

from .native_replay import canonical
from .adapters.opencode_decoder import decode_opencode_bundle, read_opencode_event_order, _strict_json
from .workload_instance import inspect_checkout_source
from .adapters.opencode_cli import read_sqlite_session_ids
from .opencode_format_evidence import build_opencode_format_evidence

ASSERTION_SCHEMA = "session-bench-expanded-opencode-qualified-v1"
MODEL = "opencode/muse-spark-1.3-contributor-free"
VERSION = "1.18.31"
NATIVE_FILES = {"opencode.db", "opencode.db-wal", "opencode.db-shm"}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def read(raw): return _strict_json(raw, "OpenCode source proof")


def validate_opencode_capture_assertion(contents, context, native_inventory):
    assertion = read(contents[context["capture_assertion_path"]])
    if (assertion.get("schema_version") != ASSERTION_SCHEMA or context["configuration_id"] != "opencode-cli"
            or assertion.get("run_id") != context["run_id"] or assertion.get("repetition") != context["repetition"]
            or assertion.get("native_inventory_sha256") != sha(contents["native/decode.json"])):
        raise ValueError("OpenCode capture assertion identity/inventory mismatch")
    for name in ("workload.json", "observer.json"):
        if assertion.get("input_sha256", {}).get(name) != sha(contents["inputs/" + name]):
            raise ValueError("OpenCode source input digest mismatch")
    raw = contents.get("inputs/root-proofs.json", b"")
    if assertion.get("root_proofs_sha256") != sha(raw): raise ValueError("OpenCode root proof digest mismatch")
    proof = read(raw)
    if proof.get("schema_version") != "session-bench-opencode-three-fresh-roots-v1": raise ValueError("OpenCode root proof schema")
    repetitions = proof.get("repetitions")
    if not isinstance(repetitions, list) or {r.get("repetition") for r in repetitions} != {1, 2, 3} or len(repetitions) != 3:
        raise ValueError("OpenCode requires three root repetitions")
    roots = set(); sessions = set(); selected = None
    for record in repetitions:
        rep = record["repetition"]; prefix = f"inputs/root-proof/{rep}/"
        files = record.get("files")
        if not isinstance(files, list): raise ValueError("OpenCode root proof inventory missing")
        indexed = {}
        for entry in files:
            path = entry.get("path")
            if not isinstance(path, str) or not path.startswith(prefix) or ".." in Path(path).parts or path in indexed:
                raise ValueError("OpenCode root proof path invalid")
            data = contents.get(path)
            if data is None or sha(data) != entry.get("sha256") or len(data) != entry.get("size_bytes"):
                raise ValueError("OpenCode root proof member mismatch")
            indexed[path] = data
        required = {prefix + n for n in ("plan.json", "capture-result.json", "native-manifest.json", "workload.json", "observer.json",
                    "version.stdout.txt", "resolved-config.stdout.txt",
                    "observer/r1.launch.json", "observer/r2.launch.json", "observer/r1.exit.json", "observer/r2.exit.json",
                    "observer/r1.stdout.jsonl", "observer/r2.stdout.jsonl", "observer/helper-ledger-r2.jsonl")}
        required |= {prefix + "native-bundle/" + n for n in NATIVE_FILES}
        if not required <= indexed.keys(): raise ValueError("OpenCode root proof omits required member")
        plan = read(indexed[prefix + "plan.json"]); result = read(indexed[prefix + "capture-result.json"])
        if (plan.get("version_pin") != VERSION or result.get("actual_version") != VERSION or plan.get("repetition") != rep
                or result.get("status") != "captured_pending_qualification" or result.get("model_submissions") != 2
                or plan.get("native_empty_before_preflight") is not True or plan.get("home_xdg_empty_before_preflight") is not True
                or plan.get("auth_copied") is not False):
            raise ValueError("OpenCode capture does not prove fresh completed roots")
        scratch = plan["scratch"]; workspace = plan["workspace"]; environment = plan["environment"]
        if scratch in roots or result["session_id"] in sessions: raise ValueError("OpenCode repetitions reuse root/session")
        roots.add(scratch); sessions.add(result["session_id"])
        config = read(environment["OPENCODE_CONFIG_CONTENT"])
        if indexed[prefix + "version.stdout.txt"].decode().strip() != VERSION:
            raise ValueError("OpenCode actual version probe differs from pin")
        resolved = read(indexed[prefix + "resolved-config.stdout.txt"])
        if any(resolved.get(key) != config.get(key) for key in ("model", "small_model", "share", "autoupdate", "enabled_providers", "plugin")):
            raise ValueError("OpenCode resolved source configuration mismatch")
        if (config.get("model") != MODEL or config.get("small_model") != MODEL or config.get("share") != "disabled"
                or config.get("plugin") != [] or config.get("enabled_providers") != ["opencode"]
                or workspace != scratch + "/workspace" or environment.get("OPENCODE_DB") != scratch + "/native/opencode.db"):
            raise ValueError("OpenCode isolation/model configuration changed")
        for name, suffix in (("HOME", "home"), ("XDG_CONFIG_HOME", "xdg-config"), ("XDG_DATA_HOME", "xdg-data"),
                             ("XDG_CACHE_HOME", "xdg-cache"), ("XDG_STATE_HOME", "xdg-state")):
            if environment.get(name) != scratch + "/" + suffix: raise ValueError("OpenCode HOME/XDG isolation changed")
        if any("KEY" in key or "TOKEN" in key for key in environment): raise ValueError("OpenCode proof inherits account keys")
        workload = read(indexed[prefix + "workload.json"])
        for turn in (1, 2):
            launch = read(indexed[prefix + f"observer/r{turn}.launch.json"])
            exited = read(indexed[prefix + f"observer/r{turn}.exit.json"])
            argv = launch.get("argv", [])
            if (launch.get("environment") != environment or launch.get("cwd") != workspace or launch.get("auth_copied") is not False
                    or "--pure" not in argv or "--share" in argv or "--attach" in argv
                    or "--model" not in argv or argv[argv.index("--model") + 1] != MODEL
                    or argv[-1] != workload["turns"][turn-1]["text"] or exited.get("returncode") != 0
                    or exited.get("stdout_sha256") != sha(indexed[prefix + f"observer/r{turn}.stdout.jsonl"])):
                raise ValueError("OpenCode source-bound launch proof invalid")
            if turn == 2 and ("--session" not in argv or argv[argv.index("--session") + 1] != result["session_id"]):
                raise ValueError("OpenCode second turn does not bind native session")
        manifest = read(indexed[prefix + "native-manifest.json"])
        if manifest.get("exact_native_family") is not True or manifest.get("quiescent") is not True: raise ValueError("OpenCode native capture family incomplete")
        if {r["relative_path"] for r in manifest["files"]} != NATIVE_FILES: raise ValueError("OpenCode native companions missing")
        with tempfile.TemporaryDirectory(prefix="bench-opencode-root-proof-") as temporary:
            clone = Path(temporary)
            for name in NATIVE_FILES:
                data = indexed[prefix + "native-bundle/" + name]
                if next(r["sha256"] for r in manifest["files"] if r["relative_path"] == name) != sha(data):
                    raise ValueError("OpenCode native root digest mismatch")
                (clone / name).write_bytes(data)
            if read_sqlite_session_ids(clone / "opencode.db") != (result["session_id"],): raise ValueError("OpenCode native session singleton mismatch")
        if record["run_id"] == context["run_id"]:
            selected = record
            if result["repetition"] != context["repetition"]: raise ValueError("OpenCode selected repetition mismatch")
            for name in NATIVE_FILES:
                if indexed[prefix + "native-bundle/" + name] != contents["native/" + name]: raise ValueError("OpenCode selected root differs from native packet")
            for name in ("workload.json", "observer.json"):
                if indexed[prefix + name] != contents["inputs/" + name]: raise ValueError("OpenCode selected observer/workload differs from proof")
    if selected is None: raise ValueError("OpenCode root proof lacks selected run")
    expected = [{"repetition": n, "root_locator": f"isolated-opencode-run-{n}/native/opencode.db", "isolated_discovery": True, "personal_history_scanned": False} for n in (1, 2, 3)]
    if context["root_repetitions"] != expected: raise ValueError("OpenCode broad root repetitions differ from source proof")
    return True, True


# The frozen workload helper, exactly as the workload asks for it. Any other
# shell text (a compound command, another script, no run canary) is left as is.
_HELPER_COMMAND = re.compile(r"python3 bench_check\.py (inspect|baseline|final) --run-canary (SB_SURVIVAL_V1_RUN_[A-Za-z0-9_-]+)")
_HELPER_DIRECTORY = "fixture_project"
_HELPER_TARGET = "fixture_project/checkout.py"
_MUTATING_TOOLS = {"edit", "write", "apply_patch", "patch", "edit_file", "write_file", "multiedit"}
_READ_ONLY_TOOLS = {"read", "glob", "grep", "list"}


def _seq(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _increasing(*values):
    """Strict order of native keys; an absent key proves nothing."""
    return all(value is not None for value in values) and all(a < b for a, b in zip(values, values[1:]))


def project_opencode_native(decoded, event_order):
    """Add comparator facts that the native SQLite rows prove, and nothing else.

    Inputs are the decoder result and the native ``event.seq`` order of the
    same copied bundle. No observer, stdout, ledger, or workspace value is
    read here. A fact whose native derivation is not exact stays absent.
    """
    projected = copy.deepcopy(decoded)
    order = event_order if isinstance(event_order, dict) and event_order.get("state") == "present" else {"messages": {}, "parts": {}}
    message_seq = lambda identifier: _seq(order["messages"].get(identifier))
    part_first = lambda identifier: _seq((order["parts"].get(identifier) or {}).get("first"))
    part_done = lambda identifier: _seq((order["parts"].get(identifier) or {}).get("completed"))
    actions = [item for item in projected.get("actions", []) if isinstance(item, dict)]
    results = {item.get("action_id"): item for item in projected.get("results", []) if isinstance(item, dict)}
    succeeded = lambda item: item.get("status") == "completed" and results.get(item.get("id"), {}).get("status") == "success"

    # 1. Helper commands, from the native command text and native workdir only.
    for item in actions:
        arguments = item.get("input")
        command = arguments.get("command") if isinstance(arguments, dict) else None
        match = _HELPER_COMMAND.fullmatch(command) if item.get("tool") == "bash" and isinstance(command, str) else None
        cwd = item.get("cwd")
        if match is None or not isinstance(cwd, str) or posixpath.basename(posixpath.normpath(cwd)) != _HELPER_DIRECTORY:
            continue
        item.update(argv=["python3", "bench_check.py", match.group(1), "--run-canary", match.group(2)],
                    target=_HELPER_TARGET, helper_phase=match.group(1))
    helpers = lambda phase: [item for item in actions if item.get("helper_phase") == phase]
    mutations = [item for item in actions if item.get("tool") in _MUTATING_TOOLS]

    # 2. final_after: native parent IDs plus strict native order. The order is
    # event.seq, and the native IDs must agree with it. Timestamps are not used.
    r2_turns = [item for item in projected.get("turns", []) if isinstance(item, dict) and item.get("revision") == "r2"]
    if len(r2_turns) == 1:
        r2 = r2_turns[0]["id"]
        edits = [item for item in mutations if item.get("turn_id") == r2]
        finals = [item for item in helpers("final") if item.get("turn_id") == r2]
        responses = [item for item in projected.get("responses", []) if isinstance(item, dict) and item.get("turn_id") == r2]
        if edits and len(finals) == 1 and len(responses) == 1:
            final, response = finals[0], responses[0]
            proven = succeeded(final) and final.get("exit_code") == 0 and all(
                succeeded(edit)
                and _increasing(message_seq(r2), part_first(edit["id"]))
                and part_first(edit["id"]) <= (part_done(edit["id"]) or -1)
                and _increasing(part_done(edit["id"]), part_first(final["id"]))
                and r2 < str(edit.get("message_id")) <= str(final.get("message_id"))
                and edit["id"] < final["id"]
                for edit in edits
            ) and _increasing(part_first(final["id"]), message_seq(response["id"])) \
                and _increasing(part_done(final["id"]), message_seq(response["id"])) \
                and part_first(final["id"]) <= part_done(final["id"]) \
                and str(final.get("message_id")) < response["id"]
            if proven:
                projected["relations"].append({"id": f"final-after-{r2}-{final['id']}", "kind": "final_after",
                                               "from_id": r2, "to_id": final["id"], "locator": final.get("locator")})

    # 3. Whole-file hashes: the pre-image is the source that the native inspect
    # output carries with its own digest; the post-image is that source with
    # the one native edit applied. Every step must be exact.
    known = all(item.get("tool") in _READ_ONLY_TOOLS or item.get("tool") in _MUTATING_TOOLS or "helper_phase" in item for item in actions)
    directory = projected.get("session", {}).get("directory")
    for change in projected.get("file_changes", []):
        if not known or len(mutations) != 1 or not isinstance(directory, str) or not posixpath.isabs(directory):
            continue
        edit = mutations[0]
        arguments = edit.get("input")
        if edit.get("tool") != "edit" or edit.get("id") != change.get("action_id") or not succeeded(edit) or not isinstance(arguments, dict):
            continue
        target, old, new, replace_all = arguments.get("filePath"), arguments.get("oldString"), arguments.get("newString"), arguments.get("replaceAll", False)
        if not (isinstance(target, str) and posixpath.isabs(target) and isinstance(old, str) and old and isinstance(new, str) and isinstance(replace_all, bool)):
            continue
        target = posixpath.normpath(target)
        if not target.startswith(posixpath.normpath(directory) + "/") or posixpath.relpath(target, posixpath.normpath(directory)) != change.get("path"):
            continue
        first, done = part_first(edit["id"]), part_done(edit["id"])
        if first is None or done is None:
            continue
        sources = set()
        for inspect in helpers("inspect"):
            if _increasing(done, part_first(inspect["id"])):
                continue  # printed after the edit: not a pre-image
            exact = (succeeded(inspect) and inspect.get("exit_code") == 0 and _increasing(part_done(inspect["id"]), first)
                     and posixpath.normpath(posixpath.join(inspect["cwd"], "checkout.py")) == target)
            sources.add(inspect_checkout_source(inspect.get("output")) if exact else None)
        if len(sources) != 1 or None in sources:
            continue
        before = sources.pop()
        if before.count(old) < 1 or (not replace_all and before.count(old) != 1):
            continue
        after = before.replace(old, new)
        change.update(before_sha256=sha(before.encode("utf-8")), after_sha256=sha(after.encode("utf-8")),
                      hash_source="native_inspect_output_and_native_edit_input")
    return projected


def build_opencode_replay_evidence(root, native, instance, context, common):
    """Freshly decode exact SQLite bytes and build all twelve format metrics."""
    inventory = read((native / "decode.json").read_bytes())
    with tempfile.TemporaryDirectory(prefix="bench-opencode-score-input-") as temporary:
        package = Path(temporary); bundle = package / "native-bundle"; bundle.mkdir()
        files = []
        for artifact in inventory["artifacts"]:
            name = artifact["path"]
            if name not in NATIVE_FILES: raise ValueError("OpenCode native inventory outside known family")
            data = (native / name).read_bytes()
            if sha(data) != artifact["sha256"]: raise ValueError("OpenCode native physical digest mismatch")
            (bundle / name).write_bytes(data)
            files.append({"name": name, "sha256": sha(data), "size_bytes": len(data)})
        if {r["name"] for r in files} != NATIVE_FILES: raise ValueError("OpenCode SQLite companions incomplete")
        observer_bytes = common["observer_document"]
        session_id = read(observer_bytes)["events"][0]["session_id"]
        decoded = decode_opencode_bundle(bundle, session_id=session_id)
        decoded["bundle"]["path"] = "native-bundle"
        # Native facts only: the decoder result plus the native event sequence.
        projected = project_opencode_native(decoded, read_opencode_event_order(bundle, session_id=session_id))
        documents = {"decoded.json": decoded, "summary.json": {"attempt_id": context["run_id"], "session_id": session_id, "result_id": context["result_id"]},
                     "evidence.json": {"run_id": context["run_id"], "configuration_id": "opencode-cli", "repetition": context["repetition"], "identity": {"build": context["build"], "harness": "OpenCode CLI"}},
                     "native-manifest.json": {"files": files}}
        for name, value in documents.items(): (package / name).write_bytes(canonical(value))
        (package / "observer.json").write_bytes(observer_bytes)
        (package / "replay-runtime").mkdir(); (package / "replay-runtime/manifest.json").write_bytes(canonical({"schema_version": "fixed-opencode-score-source-v1"}))
        evidence = build_opencode_format_evidence(package, collected_on=context["collected_on"], complete_record_family=context["complete_record_family"],
                    root_repetitions=context["root_repetitions"], observer_document=observer_bytes, include_native_density=True)
        # Bind the emitted document to actual closed packet members. Temporary
        # staging metadata is merely an adapter input, not a retained locator.
        evidence["observer"] = common["observer"]
        evidence["native_manifest"] = common["native_manifest"]
        for metric in evidence["metric_evidence"]:
            metric["observer_ids"] = [common["observer"]["id"]]
            metric["native_locators"] = [common["native_manifest"] if locator["id"] == "runtime:manifest" else locator
                                          for locator in metric["native_locators"]]
        return decoded, projected, evidence
