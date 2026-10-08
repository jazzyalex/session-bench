"""The public Copilot derivative keeps the metric rows and publishes no private value or digest."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from session_bench.copilot_session_store import read_session_store, rewrite_wal_checksums, wal_valid_frames

builder = load("build_copilot_score_replays")
sanitizer = load("sanitize_copilot_score_packets")
sha = lambda data: hashlib.sha256(data).hexdigest()
TEMP = b"/private/var/folders/ab/cdefghijklmnopqrstuvwxyz012345/T/run/fixture_project/checkout.py"


def test_temporary_directory_id_is_aliased_in_plain_and_hex_form():
    documents = {"a/native/events.jsonl": b'{"cwd":"' + TEMP + b'"}\n',
                 "a/native/index.json": b'{"data":"' + TEMP.hex().encode() + b'"}'}
    transformed, counts, _ = sanitizer.sanitize(documents)
    assert counts["temporary_directory_alias_count"] == 1
    assert all(len(transformed[name]) == len(data) for name, data in documents.items())
    assert b"cdefghij" not in transformed["a/native/events.jsonl"] and b"/var/folders/xx/" + b"x" * 30 + b"/T/run" in transformed["a/native/events.jsonl"]
    decoded = bytes.fromhex(json.loads(transformed["a/native/index.json"])["data"])
    assert decoded == json.loads(transformed["a/native/events.jsonl"])["cwd"].encode()


def test_receipt_digests_of_files_outside_the_packet_are_zeroed_and_packet_digests_follow():
    events = b'{"cwd":"' + TEMP + b'"}\n'
    private, name_digest = sha(b"account configuration"), sha(b"account bound name")
    vendor = sha(b"vendor package file")
    receipt = {"copilot_home_inventory_after": {
        "config.json": {"sha256": private}, f"managed-settings/{name_digest}.json": {"sha256": private},
        "session-state/s/events.jsonl": {"sha256": sha(events)}, "session-state/s/lock": {"sha256": sha(b"")},
        "Library/Caches/copilot/pkg/app.js": {"sha256": vendor}}}
    launch = {"safe_environment": {"PATH": "/Users/someone/bin:/usr/bin", "HOME": "/tmp/h"}}
    documents = {"a/native/events.jsonl": events, "a/inputs/capture/root-end.json": json.dumps(receipt, indent=2).encode(),
                 "a/inputs/capture/capture/r1.launch.json": json.dumps(launch, indent=2).encode()}
    transformed, counts, blanked = sanitizer.sanitize(documents)
    inventory = json.loads(transformed["a/inputs/capture/root-end.json"])["copilot_home_inventory_after"]
    assert inventory["config.json"]["sha256"] == "0" * 64 and f'managed-settings/{"0" * 64}.json' in inventory
    assert inventory["session-state/s/events.jsonl"]["sha256"] == sha(transformed["a/native/events.jsonl"]) != sha(events)
    assert inventory["session-state/s/lock"]["sha256"] == sha(b"") and inventory["Library/Caches/copilot/pkg/app.js"]["sha256"] == vendor
    public = b"".join(transformed.values())
    assert private.encode() not in public and name_digest.encode() not in public and sha(events).encode() not in public
    assert (counts["private_inventory_digests_zeroed"], counts["private_inventory_names_zeroed"]) == (1, 1)
    path = json.loads(transformed["a/inputs/capture/capture/r1.launch.json"])["safe_environment"]["PATH"]
    assert path.startswith("[personal text removed") and len(path) == len(launch["safe_environment"]["PATH"]) and "someone" not in path
    assert blanked == {"a/inputs/capture/capture/r1.launch.json": 1}


def test_private_marker_scan_finds_a_home_path_and_a_real_temporary_id():
    assert sanitizer.private_markers(b'{"cwd":"/var/folders/xx/' + b"x" * 30 + b'/T"}') == []
    assert "temporary directory id" in sanitizer.private_markers(TEMP)
    assert "home path" in sanitizer.private_markers(b"PATH=/Users/someone/bin")
    assert "credential" in sanitizer.private_markers(b"token ghp_" + b"a" * 36)


@pytest.fixture(scope="module")
def candidates(tmp_path_factory):
    base = tmp_path_factory.mktemp("copilot-public")
    builder.build(base / "private")
    return base / "private", base / "public", sanitizer.build(base / "private", base / "public")


def _forms(data):
    out = {sha(data), sha(data.rstrip(b"\n")), sha(data + b"\n")}
    try:
        text = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        out |= {sha(text), sha(text + b"\n")}
    except ValueError:
        pass
    return out


def test_public_candidates_keep_metric_rows_and_leak_no_private_value_or_digest(candidates):
    private, public, summary = candidates
    assert [row["all_31_metric_rows_unchanged"] for row in summary["runs"]] == [True, True, True]
    bundle = public / "public-inputs-candidate.json"
    assert sha(bundle.read_bytes()) == summary["public_bundle_sha256"]
    document = json.loads(bundle.read_bytes())
    assert document["configuration_id"] == "copilot" and [pair["survival"]["repetition"] for pair in document["pairs"]] == [1, 2, 3]
    files = {path.relative_to(public).as_posix(): path.read_bytes() for path in public.rglob("*") if path.is_file()
             and not path.name.endswith("-private-transformation.json") and path.name != "summary.json"}
    home = Path.home().name.encode()
    for name, data in files.items():
        assert sanitizer.private_markers(data) == [], name
    public_hex = set(re.findall(rb"[0-9a-f]{64}", b"\n".join(files.values())))
    # No digest of a private original file, line or canonical document is public.
    originals = set()
    for run in sanitizer.RUNS:
        for path in (private / run).rglob("*"):
            if not path.is_file():
                continue
            data, twin = path.read_bytes(), files.get(f"{run}/{path.relative_to(private / run).as_posix()}")
            if twin == data:
                continue
            originals |= _forms(data)
            kept = set((twin or b"").split(b"\n"))
            for line in data.split(b"\n"):
                if line and line not in kept:
                    originals |= _forms(line)
        receipt = json.loads((private / f"{run}-receipt.json").read_bytes())
        originals |= {receipt["manifest_sha256"], receipt["diagnostics_sha256"]}
    assert originals and not {digest for digest in originals if digest.encode() in public_hex}
    # A reader who knows the home name, the PATH value and the temporary
    # directory id reverses every reversible change in the public bytes. No
    # digest of a rebuilt file, line, JSON document or JSON string is public.
    def strings(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key; yield from strings(item)
        elif isinstance(value, list):
            for item in value: yield from strings(item)
        elif isinstance(value, str):
            yield value
    reversals, exercised = {}, {"home": 0, "temp": 0}
    for run in sanitizer.RUNS:
        for path in (private / run).rglob("*"):
            if not path.is_file(): continue
            name = f"{run}/{path.relative_to(private / run).as_posix()}"
            data, twin = path.read_bytes(), files.get(name)
            if twin is None or twin == data: continue
            pairs = []
            for match in sanitizer._TEMP_ID.finditer(data):
                old = match.group(0); new = b"/var/folders/" + b"x" * len(match.group(1)) + b"/" + b"x" * len(match.group(2))
                pairs += [(new, old), (new.hex().encode(), old.hex().encode())]
            if name.endswith(".launch.json"):
                real = json.loads(data)["safe_environment"]["PATH"]; blank = json.loads(twin)["safe_environment"]["PATH"]
                assert home.decode() in real and home.decode() not in blank
                pairs.append((json.dumps(blank).encode(), json.dumps(real).encode()))
                exercised["home"] += 1
            reversals[name] = (twin, list(dict.fromkeys(pairs)))
    rebuilt = set()
    for name, (twin, pairs) in reversals.items():
        variants = set()
        for mask in range(1, 2 ** min(len(pairs), 4)):
            candidate = twin
            for bit, (new, old) in enumerate(pairs[:4]):
                if mask >> bit & 1: candidate = candidate.replace(new, old)
            if candidate != twin: variants.add(candidate)
        for candidate in variants:
            exercised["temp"] += 1
            rebuilt |= _forms(candidate)
            for old, new in zip(twin.split(b"\n"), candidate.split(b"\n")):
                if old != new: rebuilt |= _forms(new)
            try:
                # Only strings the reversal changed; an unchanged string is public as it is.
                rebuilt |= {sha(text.encode()) for text in set(strings(json.loads(candidate))) - set(strings(json.loads(twin)))}
            except ValueError:
                pass
    assert exercised["home"] == 18 and exercised["temp"] > 100 and len(rebuilt) > 500
    assert not {digest for digest in rebuilt if digest.encode() in public_hex}
    # The public store is a normal SQLite database with the same rows.
    for run in sanitizer.RUNS:
        before = read_session_store((private / run / "native/session-store.db").read_bytes(), (private / run / "native/session-store.db-wal").read_bytes())
        after = read_session_store((public / run / "native/session-store.db").read_bytes(), (public / run / "native/session-store.db-wal").read_bytes())
        assert before["wal_valid_frames"] == after["wal_valid_frames"] > 0
        assert {name: len(rows) for name, rows in before["tables"].items()} == {name: len(rows) for name, rows in after["tables"].items()}
        numeric = lambda store: [[value for value in row.values() if not isinstance(value, (str, bytes))] for row in store["tables"]["assistant_usage_events"]]
        assert numeric(before) == numeric(after)
        assert b"/var/folders/xx/" in (public / run / "native/session-store.db-wal").read_bytes()
    # Every native record and key stays; only string bytes change at equal length.
    for run in sanitizer.RUNS:
        before = [json.loads(line) for line in (private / run / "native/events.jsonl").read_bytes().splitlines()]
        after = [json.loads(line) for line in (public / run / "native/events.jsonl").read_bytes().splitlines()]
        assert len(before) == len(after) and (private / run / "native/events.jsonl").stat().st_size == (public / run / "native/events.jsonl").stat().st_size
        def keys(value):
            if isinstance(value, dict): return {key: keys(item) for key, item in value.items()}
            if isinstance(value, list): return [keys(item) for item in value]
            return type(value).__name__
        assert [keys(row) for row in before] == [keys(row) for row in after]
        assert [row["id"] for row in before] == [row["id"] for row in after]
        assert not (public / run / "inputs/public-transformation.json").read_bytes().count(b"original_sha256")


def test_store_wal_is_rechecksummed_after_the_alias_and_a_broken_wal_is_refused(tmp_path):
    import sqlite3
    source = tmp_path / "store"; source.mkdir()
    connection = sqlite3.connect(source / "session-store.db")
    connection.execute("PRAGMA journal_mode=WAL"); connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT)")
    connection.execute("INSERT INTO sessions VALUES (?, ?)", ("s", TEMP.decode()))
    connection.commit()
    # Snapshot while the writer is open: closing would checkpoint the WAL.
    documents = {"a/native/session-store.db": (source / "session-store.db").read_bytes(),
                 "a/native/session-store.db-wal": (source / "session-store.db-wal").read_bytes()}
    connection.close()
    frames = len(wal_valid_frames(documents["a/native/session-store.db-wal"])[0])
    assert frames > 0 and TEMP[:40] in documents["a/native/session-store.db-wal"]
    transformed, counts, _ = sanitizer.sanitize(documents)
    wal = transformed["a/native/session-store.db-wal"]
    assert len(wal) == len(documents["a/native/session-store.db-wal"]) and b"cdefghij" not in wal
    assert len(wal_valid_frames(wal)[0]) == frames == counts["session_store_wal_frames_rechecksummed"]
    rows = read_session_store(transformed["a/native/session-store.db"], wal)["tables"]["sessions"]
    assert rows == [{"id": "s", "cwd": "/private/var/folders/xx/" + "x" * 30 + "/T/run/fixture_project/checkout.py"}]
    # Without the checksum rewrite SQLite would silently drop the changed frames.
    naive = documents["a/native/session-store.db-wal"].replace(b"cdefghijklmnopqrstuvwxyz012345", b"x" * 30)
    assert len(wal_valid_frames(naive)[0]) < frames


def test_vendor_system_prompt_is_blanked_at_equal_length_and_every_other_event_stays():
    module = sanitizer
    rows = [
        {"type": "session.start", "id": "e1", "data": {"sessionId": "s", "version": 1, "producer": "copilot-agent", "copilotVersion": "1.0.91"}},
        {"type": "system.message", "id": "e2", "data": {"role": "system", "content": "You are the GitHub Copilot CLI. " * 60 + "\n* Operating System: macos\n* Available tools: git\n",
                                                     "contentBlocks": [{"content": "You are the GitHub Copilot CLI. " * 30, "isStatic": True},
                                                                       {"content": "<version_information>Version number: 1.0.91</version_information>" * 9, "isStatic": False}]}},
        {"type": "user.message", "id": "e3", "data": {"content": "Requirement R1 … 🙂", "transformedContent": "<current_datetime>now</current_datetime>\n\nRequirement R1 … 🙂"}},
        {"type": "assistant.message", "id": "e4", "data": {"content": "Done.", "model": "gpt-6-luna", "toolRequests": [{"toolCallId": "c", "name": "bash", "arguments": {"command": "ls", "description": "list"}}]}},
    ]
    data = b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + b"\n"
    changed, count = module.redact_copilot_events(data)
    after = [json.loads(line) for line in changed.splitlines()]
    marker = "[vendor instruction text removed at equal byte length]"
    assert len(changed) == len(data) and count == 3
    system = after[1]["data"]
    assert system["role"] == "system" and system["content"].startswith(marker) and [block["isStatic"] for block in system["contentBlocks"]] == [True, False]
    assert all(block["content"].startswith(marker) for block in system["contentBlocks"])
    # One line stays: the public inputs builder reads the operating system of the capture from it.
    assert system["content"].count("* Operating System: macos\n") == 1 and "Available tools" not in system["content"] and "GitHub" not in system["content"]
    assert set(system["content"].replace(marker, "").replace("* Operating System: macos\n", "")) == {"x"}
    assert [after[0], after[2], after[3]] == [rows[0], rows[2], rows[3]]
    assert module.redact_copilot_events(changed)[0] == changed


def test_server_instructions_in_the_raw_stream_copy_are_blanked_and_the_guard_sees_a_missed_copy():
    instructions = "The GitHub MCP Server provides tools to interact with GitHub platform. Tool selection guidance: use list tools for broad retrieval."
    stream = [
        {"type": "session.mcp_servers_loaded", "id": "s1", "data": {"servers": [{"name": "github-mcp-server", "status": "connected", "source": "builtin",
                                                                              "displayName": "GitHub MCP Server", "serverMetadata": {"instructions": instructions}}]}},
        {"type": "user.message", "id": "s2", "data": {"content": "Requirement R1"}},
        {"type": "tool.execution_start", "id": "s3", "data": {"toolCallId": "c", "toolName": "bash", "arguments": {"command": "ls", "description": "list files"}}},
    ]
    data = b"\n".join(json.dumps(row).encode() for row in stream) + b"\n"
    originals = []
    changed, count = sanitizer.redact_copilot_events(data, originals)
    after = [json.loads(line) for line in changed.splitlines()]
    server = after[0]["data"]["servers"][0]
    assert len(changed) == len(data) and count == 1 and originals == [instructions]
    assert server["serverMetadata"]["instructions"].startswith("[vendor instruction text removed at equal byte length]")
    assert {key: server[key] for key in ("name", "status", "source", "displayName")} == {key: stream[0]["data"]["servers"][0][key] for key in ("name", "status", "source", "displayName")}
    assert after[1:] == stream[1:]
    # The sanitizer applies the rule to the stream copies of a packet, and its guard fails on a copy in any other file.
    phrases = sanitizer.instruction_phrases(originals)
    assert sanitizer.require_no_instruction_phrase({"a/inputs/capture/capture/r1.stdout": changed}, phrases) == len(phrases)
    with pytest.raises(ValueError, match="r2.stdout"):
        sanitizer.require_no_instruction_phrase({"a/inputs/capture/capture/r2.stdout": data}, phrases)
