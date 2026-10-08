import copy
import json
from pathlib import Path

import pytest

from session_bench.dsh_cache import read_dsh_cache
from session_bench.dsh_closure import root_observations
from session_bench.dsh_live import DSHSemanticError, decode_dsh_native
from session_bench.dsh_native import read_physical
from session_bench.dsh_sanitizer import aliases_for_home, sanitize
from session_bench.native_replay import canonical

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "artifacts/v1-expanded-preparation/dsh-public-score-preparation-v13/private/dsh-cal-20260929-2"


def cache_inputs():
    native = PACKET / "native/session.v4.jsonl.zstd"
    raw, _ = read_physical(native)
    return json.loads((PACKET / "inputs/native-companions/session-projcache.json").read_bytes()), json.loads(raw.splitlines()[0]), decode_dsh_native(native)


def test_complete_persisted_cache_keeps_unknown_metadata_and_all_step_totals(tmp_path):
    value, header, native = cache_inputs()
    value["record"]["rows"]["opaque_future_projection"] = {"ver": 999, "seq": 0, "val": {"opaque": "retained"}}
    path = tmp_path / "cache.json"
    path.write_bytes(canonical(value))
    result = read_dsh_cache(path, header=header, decoded=native)
    assert result["density_record"]["classification"] == "unclassified"
    assert result["density_record"]["logical_bytes"] == len(canonical(value))
    totals = result["reconciliation"][0]["session_totals"]
    assert totals["input"] == 12256 and totals["output"] == 4318
    assert "reasoning" not in totals


@pytest.mark.parametrize("mode", ["schema", "lifecycle", "stale", "unit_version", "total", "last", "bool"])
def test_cache_malformed_unknown_or_unbound_values_fail_closed(tmp_path, mode):
    value, header, native = cache_inputs()
    usage = value["record"]["rows"]["tokenUsage"]
    if mode == "schema": value["version"] = 8
    elif mode == "lifecycle": value["record"]["identity"]["createdAt"] += 1
    elif mode == "stale": usage["seq"] -= 1
    elif mode == "unit_version": usage["ver"] = 3
    elif mode == "total": usage["val"]["totals"]["outputTokens"] += 1
    elif mode == "last": usage["val"]["last"]["step"] += 1
    else: usage["val"]["totals"]["cacheWriteTokens"] = False
    path = tmp_path / "cache.json"
    path.write_bytes(canonical(value))
    with pytest.raises(DSHSemanticError): read_dsh_cache(path, header=header, decoded=native)


def root_inputs():
    return (json.loads((PACKET / "inputs/root-evidence.json").read_bytes()),
            {"source_document": (PACKET / "inputs/capture-source.py").read_bytes(),
             "adapter_document": (PACKET / "runtime/session_bench/adapters/deepseek_harness.py").read_bytes(),
             "environment_document": (PACKET / "inputs/operator-environment.json").read_bytes()})


def test_three_roots_bind_actual_capture_home_inventories_and_explicit_operator_environment():
    value, inputs = root_inputs()
    rows = root_observations(canonical(value), **inputs)
    assert [row["repetition"] for row in rows] == [1, 2, 3]
    assert all(row["isolated_discovery"] and not row["personal_history_scanned"] for row in rows)


@pytest.mark.parametrize("mode", ["source", "foreign_session", "unknown_family", "old_header", "duplicate_turn", "environment", "parent"])
def test_root_provenance_cannot_be_promoted_from_loose_booleans(mode):
    value, inputs = root_inputs()
    row = value["captures"][0]
    if mode == "source": inputs["source_document"] += b"altered"
    elif mode == "foreign_session": row["home_inventory"].append({"path": "sessions/foreign/history.json", "sha256": "0" * 64, "size_bytes": 3})
    elif mode == "unknown_family": row["home_inventory"].append({"path": "unknown-session-bearing.json", "sha256": "0" * 64, "size_bytes": 3})
    elif mode == "old_header": row["native_header"]["createdAt"] = 1
    elif mode == "duplicate_turn": row["capture_result"]["turns"][1]["turn_id"] = "turn-r1"
    elif mode == "environment": inputs["environment_document"] += b"\n"
    else: row["public_parent_capture_manifest_sha256"] = "0" * 64
    with pytest.raises(DSHSemanticError): root_observations(canonical(value), **inputs)


def test_fixed_byte_context_redaction_preserves_required_messages_and_native_types():
    value = {"system": {"role": "system", "content": [{"type": "text", "text": 'private 🙂 "quoted"\ncontext'}]},
             "assistant": {"role": "assistant", "content": [{"type": "text", "text": "required explanation and canary"}]},
             "user": {"role": "user", "source": {"kind": "user"}, "content": [{"type": "text", "text": "required task"}]}}
    result = sanitize(value, aliases=aliases_for_home(Path.home()))
    assert len(canonical(result)) == len(canonical(value))
    assert result["assistant"] == value["assistant"] and result["user"] == value["user"]
    assert result["system"]["content"][0]["text"] != value["system"]["content"][0]["text"]
    assert result["system"]["content"][0]["type"] == "text"


def test_public_parent_manifest_keeps_only_digests_that_cannot_confirm_a_guess():
    # A captured file that is public except for one low-entropy private value
    # (a home name, an account balance) must not have its digest published.
    from session_bench.dsh_closure import public_parent_manifest
    files = {"observer.json": "a", "capture-start.json": "c", "r1.stdout.jsonl": "d",
             "workload.json": "b", "helper-ledger.jsonl": "e", "native/session.v4.jsonl.zstd": "f"}
    private = json.dumps({"parent_manifest_sha256": "9" * 64, "native_sha256": "f" * 64,
                          "files": [{"path": path, "sha256": letter * 64} for path, letter in files.items()]}).encode()
    public = public_parent_manifest(private)
    kept = {row["path"] for row in json.loads(public)["files"] if set(row["sha256"]) != {"0"}}
    assert len(public) == len(private)
    # The compressed native file is not published (the public packet holds the plain derivative),
    # so its digest is not the digest of public bytes: it is zeroed in both places.
    assert kept == {"workload.json", "helper-ledger.jsonl"}
    assert json.loads(public)["parent_manifest_sha256"] == "0" * 64 and json.loads(public)["native_sha256"] == "0" * 64
    assert public_parent_manifest(public) == public


def test_public_parent_pins_match_the_retained_capture_manifests():
    import hashlib
    from pathlib import Path
    from session_bench import dsh_closure
    root = Path(__file__).resolve().parents[1] / "artifacts/survival-v1-runs"
    for key in dsh_closure.PUBLIC_PARENTS:
        private = (root / key / "qualification-v5/manifest.json").read_bytes()
        assert hashlib.sha256(dsh_closure.public_parent_manifest(private)).hexdigest() == dsh_closure.PUBLIC_PARENTS[key]


def test_source_holds_no_digest_of_a_private_parent_manifest():
    import hashlib
    from pathlib import Path
    from session_bench import dsh_closure
    root = Path(__file__).resolve().parents[1]
    source = (root / "session_bench/dsh_closure.py").read_text() + (root / "scripts/build_dsh_public_replays.py").read_text()
    assert not hasattr(dsh_closure, "PARENTS")
    for key in dsh_closure.PUBLIC_PARENTS:
        private = (root / "artifacts/survival-v1-runs" / key / "qualification-v5/manifest.json").read_bytes()
        assert hashlib.sha256(private).hexdigest() not in source


def test_packets_built_before_the_native_digest_was_removed_still_validate():
    # The ranked packets name the earlier parent pin in their root evidence and must still replay.
    import hashlib
    from session_bench.score_replay import replay_score_package
    earlier = ROOT / "artifacts/v1-expanded-preparation/dsh-public-score-preparation-v8/public-candidates/dsh-cal-20260929-2"
    pin = hashlib.sha256((earlier / "manifest.json").read_bytes()).hexdigest()
    assert len(replay_score_package(earlier, expected_manifest_sha256=pin)["diagnostics"]["intact"]["metrics"]) == 31


def test_a_packet_runtime_does_not_carry_the_earlier_parent_pins():
    from session_bench import score_replay
    listed = set(score_replay.SOURCE_FILES) | set(score_replay.DSH_SOURCE_FILES) | set(score_replay.CURRENT_EXTRA_SOURCES["deepseek-harness-cli"])
    assert "session_bench/dsh_closure.py" in listed and "session_bench/dsh_earlier_public_parents.py" not in listed
    source = (ROOT / "session_bench/dsh_closure.py").read_text()
    from session_bench.dsh_earlier_public_parents import EARLIER_PUBLIC_PARENTS
    assert not any(pin in source for pin in EARLIER_PUBLIC_PARENTS.values())


def test_vendor_tool_descriptions_and_title_prompt_are_blanked_at_equal_canonical_length():
    aliases = aliases_for_home(Path("/Users/someone"))
    header = {"type": "request/header", "seq": 11, "data": {"header": {"config": {"model": "deepseek-flash"}, "tools": [
        {"name": "bash", "description": "Execute a bash command and return its output. " * 12,
         "parameters": {"type": "object", "properties": {"command": {"type": "string", "description": "The bash command to execute."},
                                                          "description": {"type": "string", "description": "What the command does — in “active” voice."}}}}]}}}
    title = {"type": "session/title-llm-request", "seq": 14, "data": {"system": "Create a concise title for an AI coding-assistant session.", "titleProvider": "p",
             "messages": [{"role": "user", "source": {"kind": "dsh-session-title-llm"}, "content": [{"type": "text", "text": "Generate the session title from: R1"}]}]}}
    call = {"type": "tool/call", "seq": 20, "data": {"turn": 1, "step": 1, "callId": "c", "name": "bash", "arguments": "{\"command\": \"ls\", \"description\": \"list files\"}"}}
    marker = "[vendor instruction text removed at equal byte length]"
    for row in (header, title, call):
        assert len(canonical(sanitize(row, aliases=aliases))) == len(canonical(row))
    tool = sanitize(header, aliases=aliases)["data"]["header"]["tools"][0]
    assert tool["name"] == "bash" and tool["description"].startswith(marker)
    properties = tool["parameters"]["properties"]
    # A schema description goes; the property that is itself named "description" stays as a key.
    assert list(properties) == ["command", "description"] and properties["command"]["type"] == "string"
    assert properties["command"]["description"] == marker[:len("The bash command to execute.")] and properties["description"]["description"].startswith(marker[:20])
    assert sanitize(header, aliases=aliases)["data"]["header"]["config"] == header["data"]["header"]["config"]
    changed = sanitize(title, aliases=aliases)["data"]
    assert changed["system"].startswith(marker) and changed["messages"] == title["data"]["messages"] and changed["titleProvider"] == "p"
    assert sanitize(call, aliases=aliases) == call  # a call argument named "description" is scored text
    # The sanitizer reports the strings it obscured, so a guard can search every public file for a copy.
    import importlib.util, sys
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("dsh_public_builder", ROOT / "scripts/build_dsh_public_replays.py")
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    originals = []
    for row in (header, title, call):
        assert builder.obscured(row, aliases, originals) == sanitize(row, aliases=aliases)
    assert header["data"]["header"]["tools"][0]["description"] in originals and title["data"]["system"] in originals and len(originals) == 4
