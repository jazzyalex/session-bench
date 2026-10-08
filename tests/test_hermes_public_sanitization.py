"""Hermes public candidates: equal length, every row kept, no private value and no digest oracle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

from session_bench.hermes_score_inputs import AFTER, BEFORE, RECEIPT, verify_state_receipt
from session_bench.hermes_state_evidence import inventory_sha256
from session_bench.hermes_store_rows import PROMPT_FILE, ROWS_FILE, SCHEMA_FILE, decode_hermes_native, read_prompt_row, read_rows, usage_anchor
from session_bench.public_digest_check import HEX64, check_public_packets, public_byte_digests, read_public_set, short_digest_warnings
from session_bench.release_score import score_release_run
from session_bench.score_replay import replay_score_package, verify_score_packet_tamper_controls

ROOT = Path(__file__).resolve().parents[1]
RUNS = ("hermes-codex-2026-10-07-08", "hermes-codex-2026-10-07-09", "hermes-codex-2026-10-07-10")
PRIVATE = ROOT / "artifacts/v1-expanded-preparation/hermes-score-replay-v3"
PUBLIC = ROOT / "artifacts/v1-expanded-preparation/hermes-public-candidates-v3"
BUNDLE_SHA256 = "fd1ac439d6ade134cb975417e4734bca2763eb14b748cb144a693a44aa0a6b08"
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("sanitize_hermes_under_test", ROOT / "scripts/sanitize_hermes_score_packets.py")
sanitizer = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(sanitizer)
from test_hermes_score_replay import FILE_STEPS, PROMPT, PROMPT_HASH, SID, exports, make_store, prompt_row  # noqa: E402
from test_hermes_state_capture import bracket, capture, turn  # noqa: E402

real = pytest.mark.skipif(not (PUBLIC / "summary.json").is_file(), reason="Hermes public candidates are not in this checkout")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_row_export_keeps_every_row_and_key_at_equal_length_and_blanks_digests_and_instruction_text(tmp_path):
    def with_prompt(connection, insert, message):
        connection.execute("UPDATE sessions SET system_prompt = ? WHERE id = ?", ("You are the vendor agent. Follow these vendor rules in every answer you give today.", SID))
    rows, _ = exports(make_store(tmp_path / "state.db", extra=with_prompt))
    texts = []
    public, counts = sanitizer.sanitize_rows(rows, texts)
    assert len(public) == len(rows) and counts == {"digests_zeroed": 4 + 8, "encrypted_payloads_blanked": 1, "instruction_texts_blanked": 1}
    # With a bound prompt row the session hash is rebound to the digest of the public prompt, not zeroed.
    rebound, found = sanitizer.sanitize_rows(rows, [], {PROMPT_HASH: "9" * 64})
    assert found["digests_rebound"] == 1 and found["digests_zeroed"] == 3 + 8 and PROMPT_HASH.encode() not in rebound and b'"system_prompt_hash": "' + b"9" * 64 in rebound
    before, after = json.loads(rows), json.loads(public)
    shape = lambda document: [(table["table"], table["columns"], [(row["rowid"], sorted(row["values"])) for row in table["rows"]]) for table in document["stores"][0]["tables"]]
    assert shape(before) == shape(after)
    session = next(table for table in after["stores"][0]["tables"] if table["table"] == "sessions")["rows"][0]["values"]
    assert session["system_prompt_hash"] == session["tool_names"] == "0" * 64 and session["id"] == SID
    assert session["system_prompt"].startswith("[vendor instruction text removed at equal byte length]") and len(texts) == 1
    assert json.loads(session["model_config"])["_usage_anchor"] == {"prompt_tokens": 9, "base_last_fp": "0" * 64, "base_prefix_fp": "0" * 64}
    messages = next(table for table in after["stores"][0]["tables"] if table["table"] == "messages")["rows"]
    assert {row["values"]["display_identity"]["blob_hex"] for row in messages} == {"0" * 64}
    assert b"gAAAAsecretpayload" not in public and b'encrypted_content\\": \\"xxxxxxxxxxxxxxxxxx' in public
    # Prompts, responses, tool calls and results, ids and times are not touched.
    kept = lambda document: [[row["values"][key] for key in ("id", "role", "content", "tool_calls", "tool_call_id", "timestamp", "message_uid")]
                             for table in document["stores"][0]["tables"] if table["table"] == "messages" for row in table["rows"]]
    assert kept(before) == kept(after)
    phrases = sanitizer.instruction_phrases(texts)
    assert phrases and sanitizer.require_no_instruction_phrase({"rows": public}, phrases) == len(phrases)
    with pytest.raises(ValueError, match="blanked instruction text"):
        sanitizer.require_no_instruction_phrase({"rows": rows}, phrases)
    with pytest.raises(ValueError, match="exporter serialization"):
        sanitizer.sanitize_rows(rows.replace(b"\n ", b"\n", 1), [])


def test_usage_record_key_is_recomputed_over_the_public_row_and_kept(tmp_path):
    rows, schema = exports(make_store(tmp_path / "state.db", r2_steps=FILE_STEPS, anchor=True))
    public, counts = sanitizer.sanitize_rows(rows, [])
    before, after = read_rows(rows, schema)[0], read_rows(public, schema)[0]
    old, new = usage_anchor(before["sessions"][0]["values"], before["messages"]), usage_anchor(after["sessions"][0]["values"], after["messages"])
    # The key still binds the record to its message row in the public file, and it is the digest of public bytes.
    assert new is not None and new["response_row"] == old["response_row"] and new["usage"] == {"input_tokens": 900, "output_tokens": 40}
    member = {key: after["messages"][-2]["values"][key] for key in ("content", "role", "tool_call_id")}
    assert new["base_last_fp"] == sanitizer.sha(json.dumps(member, sort_keys=True, separators=(",", ":")).encode()) and counts["digests_rebound"] == 1
    config = json.loads(after["sessions"][0]["values"]["model_config"])["_usage_anchor"]
    assert config["base_prefix_fp"] == "0" * 64 and config["prompt_tokens"] == 900 and len(public) == len(rows)


def test_system_prompt_row_keeps_the_harness_sentence_and_its_hash_is_the_digest_of_the_public_text():
    text = PROMPT + "Skill: ünïcode notes of the operator, kept across many sessions and never to be shown.\n"
    private = prompt_row(text, rowid=118)
    texts = []
    public, old, new = sanitizer.sanitize_prompt_row(private, texts)
    row = json.loads(public)["rows"][0]
    assert len(public) == len(private) and old == sha(text.encode()) and new == sha(row["prompt"].encode()) == row["hash"] != old
    assert row["prompt"].startswith("You are Hermes Agent, built by Test Lab.[vendor instruction text removed at equal byte length]")
    assert row["_rowid"] == 100 and json.loads(public)["session_system_prompt_hash"] == new and old.encode() not in public
    assert b"operator" not in public and b"garden" not in public and texts == [text[len("You are Hermes Agent, built by Test Lab."):]]
    # The public row still verifies as the row that a session row with the new hash names.
    assert read_prompt_row(public, new)["prompt"] == row["prompt"]
    phrases = sanitizer.instruction_phrases(texts)
    assert phrases and sanitizer.require_no_instruction_phrase({"row": public}, phrases) == len(phrases)
    with pytest.raises(ValueError, match="needs a rule"):
        sanitizer.sanitize_prompt_row(prompt_row("Be direct. No harness sentence here.\n"), [])
    with pytest.raises(ValueError, match="needs a rule"):
        sanitizer.sanitize_prompt_row(prompt_row(PROMPT, digest="1" * 64), [])


def test_inventories_alias_every_unclassified_name_and_the_receipt_still_verifies(tmp_path, monkeypatch):
    from session_bench import hermes_score_inputs
    monkeypatch.setattr(hermes_score_inputs, "STATE_ROOT_SUFFIX", "/hermes-home")   # the synthetic home of the capture tests
    root, connection, before, detail, started = bracket(tmp_path)
    turn(root, connection)
    receipt, after = capture(root, tmp_path / "r2-native", before, started, before_detail=detail, turn=2)
    documents = {RECEIPT: sanitizer._compact(receipt), BEFORE: sanitizer._compact(before), AFTER: sanitizer._compact(after)}
    public, foreign, kept = sanitizer.alias_inventories(documents)
    assert all(len(public[name]) == len(data) for name, data in documents.items())
    new_receipt, new_before, new_after = (json.loads(public[name]) for name in (RECEIPT, BEFORE, AFTER))
    verify_state_receipt(new_receipt, new_before, new_after)
    assert new_receipt["before_inventory_sha256"] == inventory_sha256(new_before) != receipt["before_inventory_sha256"]
    names = {entry["relative_path"] for entry in new_before["entries"]}
    # A changed entry keeps its name; the operator's other entries do not.
    assert {"state.db", "logs/agent.log", "sessions/sessions.json", "cache", "hermes-agent"} <= names and {"auth.json", "config.yaml"} <= foreign
    assert not [name for name in names if "fed456" in name or name in ("auth.json", ".env", "config.yaml")]
    assert "sessions" in kept and "auth.json" not in kept
    old = {entry["relative_path"]: entry for entry in before["entries"]}
    blanked = next(entry for entry in new_before["entries"] if entry["relative_path"] not in old and entry["kind"] == "file")
    assert str(blanked["inode"])[0] == "1" and set(str(blanked["inode"])[1:]) <= {"0"} and set(str(blanked["mtime_ns"])[1:]) == {"0"}
    # A file that keeps its name (the store, a log) has its size and inode blanked too; its times still show the change.
    for name in ("state.db", "logs/agent.log"):
        kept_entry = next(entry for entry in new_after["entries"] if entry["relative_path"] == name)
        assert set(str(kept_entry["size_bytes"])[1:]) <= {"0"} and set(str(kept_entry["inode"])[1:]) <= {"0"} and kept_entry["mtime_ns"] == next(
            entry for entry in after["entries"] if entry["relative_path"] == name)["mtime_ns"]
    assert all(set(str(row[side]["size_bytes"])[1:]) <= {"0"} for row in new_receipt["classes"]["shared_changed"] if row["kind"] == "file"
               for side in ("before", "after") if row[side])
    assert {entry["target_sha256"] for entry in new_before["entries"] if entry["kind"] == "symlink"} == {"0" * 64}
    # Inside a digested subtree every name is aliased, and a digest stays only where it shows a change.
    install = next(entry for entry in new_before["entries"] if entry["relative_path"] == "hermes-agent")
    assert set(install["directory_sha256"].values()) == {"0" * 64} and install["entries_sha256"] == "0" * 64
    assert not [name for name in install["directory_sha256"] if "hermes_cli" in name or ".git" in name]
    cache = next(row for row in new_receipt["classes"]["shared_changed"] if row["relative_path"] == "cache")
    assert cache["changed_directories"] == ["cache"] and all(re.fullmatch(r"cache/x*[0-9a-zA-Z]", path) for path in cache["changed_paths"])
    assert cache["before"]["entries_sha256"] != cache["after"]["entries_sha256"] != "0" * 64
    # The store copy and the row totals of the operator's store are blanked.
    store = new_receipt["session_store"]["stores"][0]
    assert store["copied_files"][0]["sha256"] == "0" * 64 and set(store["copied_files"][0]["filesystem_id"]) <= set("0:")
    original = receipt["session_store"]["stores"][0]
    assert [row["selected_rows"] for row in store["tables"]] == [row["selected_rows"] for row in original["tables"]]
    assert all(str(row["total_rows"])[0] == "1" and set(str(row["total_rows"])[1:]) <= {"0"} for row in store["tables"])


def test_home_name_encodings_and_private_marker_scan():
    forms = sanitizer.encodings("operatorname")
    assert b"operatorname" in forms and b"operatorname".hex().encode() in forms and "operatorname".encode("utf-16-le") in forms
    import base64
    for prefix in (b"", b"a", b"ab"):
        coded = base64.b64encode(prefix + b"/Users/operatorname/.hermes")
        assert any(form in coded for form in forms), prefix
    assert sanitizer.private_markers(b"path /Users/someone/.hermes") == ["home path"]
    assert sanitizer.private_markers(b"path /Users/xxxxx/.hermes and the tail") == []
    assert sanitizer.private_markers(b"mail someone@gmail.com") == ["e-mail"]
    assert sanitizer.private_markers(b"key sk-abcdefghijklmnopqrstuvwxyz") == ["credential"]
    assert sanitizer.private_markers(b"zzz b3BlcmF0b3JuYW1l zzz", forms) == ["home user name in an encoding or a digest of private text"]


@pytest.fixture(scope="module")
def candidates():
    summary = json.loads((PUBLIC / "summary.json").read_bytes())
    receipts = [json.loads((PUBLIC / f"{run}-receipt.json").read_bytes()) for run in RUNS]
    return summary, receipts, read_public_set([PUBLIC / run for run in RUNS], [PUBLIC / "public-inputs-candidate.json"])


@real
def test_public_candidates_keep_the_31_metric_rows_and_replay(candidates):
    summary, receipts, _ = candidates
    assert sha((PUBLIC / "public-inputs-candidate.json").read_bytes()) == summary["public_bundle_sha256"] == BUNDLE_SHA256
    for row, public, run in zip(summary["runs"], receipts, RUNS):
        private = json.loads((PRIVATE / f"{run}-receipt.json").read_bytes())
        assert private["diagnostics"]["intact"]["metrics"] == public["diagnostics"]["intact"]["metrics"] and len(public["diagnostics"]["intact"]["metrics"]) == 31
        assert public["os_sandboxed"] is True and public["manifest_sha256"] == row["manifest_sha256"]
        # Duplicate safety and density evidence are equal record by record.
        for metric in ("broad.naive_reader_duplicate_safety", "broad.classified_content_density", "broad.event_timestamps"):
            assert (private["diagnostics"]["intact"]["format_evidence"]["profile"]["broad_evidence"][metric]
                    == public["diagnostics"]["intact"]["format_evidence"]["profile"]["broad_evidence"][metric])
    row = summary["runs"][0]
    replayed = replay_score_package(PUBLIC / RUNS[0], expected_manifest_sha256=row["manifest_sha256"], os_sandboxed=True)
    assert replayed["diagnostics_sha256"] == row["diagnostics_sha256"]
    assert verify_score_packet_tamper_controls(PUBLIC / RUNS[0], expected_manifest_sha256=row["manifest_sha256"])["status"] == "passed"
    bundle = json.loads((PUBLIC / "public-inputs-candidate.json").read_bytes())
    # All 31 metrics are resolved, so each run has an overall score.
    shown = []
    for pair in bundle["pairs"]:
        display = score_release_run(pair["survival"], pair["format"]).display()
        assert not [item for item in display["blockers"] if item.endswith(":unresolved")]
        shown.append(display["overall"])
    assert shown == ["86.4", "86.5", "86.3"]


@real
def test_every_file_keeps_its_byte_length_and_the_rows_keep_every_row_and_key(candidates):
    for run in RUNS:
        private = {path.relative_to(PRIVATE / run).as_posix(): path for path in (PRIVATE / run).rglob("*") if path.is_file()}
        public = {path.relative_to(PUBLIC / run).as_posix(): path for path in (PUBLIC / run).rglob("*") if path.is_file()}
        native = lambda files: sorted(name for name in files if name.startswith(("native/", "inputs/")) and name != "inputs/public-transformation.json")
        assert native(private) == native(public) and [private[name].stat().st_size for name in native(private)] == [public[name].stat().st_size for name in native(public)]
        tables = lambda packet: read_rows(*((packet / "native/capture" / name).read_bytes() for name in (ROWS_FILE, SCHEMA_FILE)))[0]
        before, after = tables(PRIVATE / run), tables(PUBLIC / run)
        assert {name: [(row["rowid"], sorted(row["values"])) for row in rows] for name, rows in before.items()} == {
            name: [(row["rowid"], sorted(row["values"])) for row in rows] for name, rows in after.items()}
        same = ("id", "role", "content", "tool_calls", "tool_call_id", "tool_name", "timestamp", "finish_reason", "reasoning", "message_uid")
        assert [[row["values"][key] for key in same] for row in before["messages"]] == [[row["values"][key] for key in same] for row in after["messages"]]
        assert before["session_model_usage"] == after["session_model_usage"]
        assert (PRIVATE / run / "native/capture" / SCHEMA_FILE).read_bytes() == (PUBLIC / run / "native/capture" / SCHEMA_FILE).read_bytes()
        assert decode_hermes_native(PUBLIC / run / "native", run_canary="SB_SURVIVAL_V1_RUN_" + run)["status"] == "ok"
        session = after["sessions"][0]["values"]
        assert session["system_prompt"] is None and session["tool_names"] == "0" * 64
        # The prompt row: only the harness sentence stays, and the hash on both rows is the SHA-256 of the public text.
        row = read_prompt_row((PUBLIC / run / "native/capture" / PROMPT_FILE).read_bytes(), session["system_prompt_hash"])
        old = read_prompt_row((PRIVATE / run / "native/capture" / PROMPT_FILE).read_bytes(), before["sessions"][0]["values"]["system_prompt_hash"])
        assert row["hash"] == sha(row["prompt"].encode()) != old["hash"] and row["rowid"] == 100 != old["rowid"]
        # The usage record still binds to its message row in the public rows, with the same counts.
        bound, private_bound = usage_anchor(session, after["messages"]), usage_anchor(before["sessions"][0]["values"], before["messages"])
        assert bound is not None and bound["usage"] == private_bound["usage"] and bound["response_row"] == private_bound["response_row"]
        assert json.loads(session["model_config"])["_usage_anchor"]["base_prefix_fp"] == "0" * 64
        # No public document states the size or the inode of the operator's store.
        for name in (BEFORE, AFTER):
            store_entry = next(entry for entry in json.loads((PUBLIC / run / "inputs/capture" / name).read_bytes())["entries"] if entry["relative_path"] == "state.db")
            assert set(str(store_entry["size_bytes"])[1:]) <= {"0"} and set(str(store_entry["inode"])[1:]) <= {"0"}
        for turn in (1, 2):
            stream = (PUBLIC / run / f"inputs/capture/turn-r{turn}/stdout.jsonl").read_bytes()
            assert len(stream) == (PRIVATE / run / f"inputs/capture/turn-r{turn}/stdout.jsonl").stat().st_size and Path.home().name.encode() not in stream
        assert row["prompt"].startswith("You are Hermes Agent, built by Nous Research.[vendor instruction text removed at equal byte length]....")
        assert set(row["prompt"][len("You are Hermes Agent, built by Nous Research.[vendor instruction text removed at equal byte length]"):]) == {"."}
        assert len(json.dumps(row["prompt"], ensure_ascii=False).encode()) == len(json.dumps(old["prompt"], ensure_ascii=False).encode())
        extract = (PUBLIC / run / "inputs/capture/shared-store-extract/state-db-schema-version.json").read_bytes()
        assert extract == (PRIVATE / run / "inputs/capture/shared-store-extract/state-db-schema-version.json").read_bytes() and json.loads(extract)["rows"] == [{"version": 31}]
        receipt, before_inventory, after_inventory = (json.loads((PUBLIC / run / "inputs/capture" / name).read_bytes()) for name in (RECEIPT, BEFORE, AFTER))
        verify_state_receipt(receipt, before_inventory, after_inventory)


def _private_values():
    """Values of the private packets that must not be public, in the forms a reader could search for."""
    values = set(sanitizer.encodings(Path.home().name)) | {str(Path.home()).encode()}
    kept = set()
    for run in RUNS:
        receipt, before, after = (json.loads((PRIVATE / run / "inputs/capture" / name).read_bytes()) for name in (RECEIPT, BEFORE, AFTER))
        kept |= sanitizer.kept_paths(before, after, receipt)
    public_parts = {part for path in kept for part in path.split("/")}
    for run in RUNS:
        rows = json.loads((PRIVATE / run / "native/capture" / ROWS_FILE).read_bytes())
        for table in rows["stores"][0]["tables"]:
            for row in table["rows"]:
                item = row["values"]
                values.update(str(item.get(key)).encode() for key in ("system_prompt_hash", "tool_names") if item.get(key))
                if isinstance(item.get("display_identity"), dict):
                    values.update((item["display_identity"]["blob_hex"].encode(), bytes.fromhex(item["display_identity"]["blob_hex"])))
                values.update(match.encode() for match in re.findall(r'"encrypted_content": "([^"]{40})', item.get("codex_reasoning_items") or ""))
                values.update(match.encode() for match in re.findall(r'base_prefix_fp": "([0-9a-f]{64})"', item.get("model_config") or ""))
        # The private system prompt: its text in windows, its JSON-escaped text, and its digest.
        prompt = json.loads((PRIVATE / run / "native/capture" / PROMPT_FILE).read_bytes())["rows"][0]
        rest = prompt["prompt"][len("You are Hermes Agent, built by Nous Research."):]
        values.add(prompt["hash"].encode())
        for start in range(0, len(rest) - 40, 97):
            window = rest[start:start + 40]
            values.update((window.encode(), json.dumps(window, ensure_ascii=False)[1:-1].encode()))
        receipt = json.loads((PRIVATE / run / "inputs/capture" / RECEIPT).read_bytes())
        values.update(row["sha256"].encode() for store in receipt["session_store"]["stores"] for row in store["copied_files"])
        values.update((receipt["before_inventory_sha256"].encode(), receipt["after_inventory_sha256"].encode()))
        inventory = json.loads((PRIVATE / run / "inputs/capture" / BEFORE).read_bytes())
        for entry in inventory["entries"]:
            names = [entry["relative_path"], *entry.get("directory_sha256", {})]
            values.update(part.encode() for name in names for part in name.split("/") if len(part) >= 12 and part not in public_parts)
            if entry["kind"] == "symlink":
                values.add(entry["target_sha256"].encode())
    return {value for value in values if value}


@real
def test_public_set_holds_no_private_value_no_other_session_and_no_short_digest(candidates):
    _, receipts, public = candidates
    forbidden = _private_values()
    assert len(forbidden) > 1000
    captured = {json.loads(public[f"{run}/inputs/capture/capture-result.json"])["session_id"] for run in RUNS}
    names = "\n".join(public).encode()
    everything = dict(public, **{"file names": names}, **{f"receipt-{index}": json.dumps(receipt).encode() for index, receipt in enumerate(receipts)})
    listings = {name: data for name, data in everything.items() if name.endswith((RECEIPT, BEFORE, AFTER))}
    # A long name of another entry of the home must be gone from the listings; every other value from every file.
    long_names = {value for value in forbidden if not HEX64.fullmatch(value) and len(value) >= 12 and re.fullmatch(rb"[\x21-\x7e]+", value)}
    exact = forbidden - long_names
    assert sorted({name for name, data in everything.items() for token in exact if token in data}) == []
    parts = set()
    for data in listings.values():
        document = json.loads(data)
        found = [entry["relative_path"] for entry in document.get("entries", [])] + [name for entry in document.get("entries", []) for name in entry.get("directory_sha256", {})]
        for row in document.get("classes", {}).get("shared_changed", []):
            found += [*row.get("changed_directories", []), *row.get("changed_paths", [])]
        parts.update(part.encode() for name in found for part in name.split("/"))
    assert sorted(long_names & parts) == []
    assert all(sanitizer.private_markers(data, sanitizer.encodings(Path.home().name)) == [] for data in everything.values())
    # No session id of another session of the operator, in any file.
    joined = b"\n".join(everything.values())
    assert {match.decode() for match in re.findall(rb"(?<![0-9])\d{8}_\d{6}_[0-9a-f]{6}(?![0-9a-f])", joined)} == captured
    assert short_digest_warnings(public) == []
    transformation = json.loads((PUBLIC / RUNS[0] / "inputs/public-transformation.json").read_bytes())
    assert transformation["aliases"]["system_prompt_rows_blanked"] == 3 and transformation["aliases"]["digests_rebound"] == 6
    assert transformation["aliases"]["instruction_phrases_checked"] > 100 and transformation["aliases"]["encrypted_payloads_blanked"] > 0
    # The phrase guard with the real phrases of the three prompts, on every public file.
    texts = [json.loads((PRIVATE / run / "native/capture" / PROMPT_FILE).read_bytes())["rows"][0]["prompt"][len("You are Hermes Agent, built by Nous Research."):] for run in RUNS]
    phrases = sanitizer.instruction_phrases(texts)
    assert len(phrases) > 100 and sanitizer.require_no_instruction_phrase(public, phrases) == len(phrases)
    with pytest.raises(ValueError, match="blanked instruction text"):
        sanitizer.require_no_instruction_phrase({"private": (PRIVATE / RUNS[0] / "native/capture" / PROMPT_FILE).read_bytes()}, phrases)


@real
def test_public_set_holds_only_bound_digests(candidates):
    _, receipts, _ = candidates
    result = check_public_packets([PUBLIC / run for run in RUNS], receipts=receipts, extra_files=[PUBLIC / "public-inputs-candidate.json"],
                                  allowlist=sanitizer.DIGEST_ALLOWLIST)
    assert result["unbound"] == [] and 0 < result["classes"]["allowlisted"] < 100
    with pytest.raises(ValueError, match="unbound"):
        check_public_packets([PUBLIC / run for run in RUNS], receipts=receipts, extra_files=[PUBLIC / "public-inputs-candidate.json"], allowlist={})


@real
def test_no_digest_of_a_private_original_is_in_the_public_set(candidates):
    """Depth 1 with the true originals: every digest a private file yields, unless public bytes yield it too."""
    _, _, public = candidates
    computable = set()
    for name, data in public.items():
        computable |= public_byte_digests(data, name)
    private = set()
    for run in RUNS:
        for path in (PRIVATE / run).rglob("*"):
            if path.is_file() and path.relative_to(PRIVATE / run).parts[0] in ("native", "inputs"):
                private |= public_byte_digests(path.read_bytes(), path.name)
    oracles = private - computable
    assert len(oracles) > 100
    joined = b"\n".join(public.values())
    # Every public file is UTF-8 text, so a digest can only be there as hex. The hex tokens are compared as a set.
    assert all(data.decode("utf-8") is not None for data in public.values())
    assert not oracles & set(HEX64.findall(joined))


@real
def test_putting_the_home_name_back_confirms_no_digest_to_depth_three(candidates):
    """A reader who guesses the home name rebuilds candidate private bytes from the public bytes.

    Depth 1: digests of those bytes (file, line, JSON member). Depth 2 and 3:
    the same after the digests of the depth before are put into the documents
    that name them. No such digest may be in the public set, unless public
    bytes yield it as well.
    """
    _, _, public = candidates
    home = Path.home().name.encode()
    alias = b"x" * len(home)

    def put_back(data):
        return re.sub(rb"(?<=/Users/)" + alias + rb"(?![x])", home, data)

    assert any(put_back(data) != data for data in public.values())
    computable = set()
    for name, data in public.items():
        computable |= public_byte_digests(data, name)
    joined = b"\n".join(public.values()) + b"\n" + "\n".join(public).encode()
    mapping = {}
    for depth in (1, 2, 3):
        guesses, following = set(), {}
        for name, data in public.items():
            value = put_back(data)
            for old, new in mapping.items():
                value = value.replace(old, new)
            if value == data:
                continue        # unchanged bytes yield only digests that are computable from the public set
            guesses |= public_byte_digests(value, name)
            following[sha(data).encode()] = sha(value).encode()
            if name.endswith((BEFORE, AFTER)):
                # The receipt binds an inventory by the digest of its canonical JSON, not of its file.
                following[inventory_sha256(json.loads(data)).encode()] = inventory_sha256(json.loads(value)).encode()
        mapping = {old: new for old, new in following.items() if old != new}
        oracles = guesses - computable
        assert depth > 1 or len(oracles) > 50, "the test must build real guesses"
        hits = oracles & set(HEX64.findall(joined))
        assert not hits, f"depth {depth}: a guessed private digest is in the public set"
    assert HEX64.search(joined)
