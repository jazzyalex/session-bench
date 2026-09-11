import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from session_bench.decoders import decode_native


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(package: Path, fmt: str, entries: list[tuple[str, str, list[str]]]) -> None:
    artifacts = []
    for artifact_id, filename, dependencies in entries:
        path = package / filename
        artifacts.append({"id": artifact_id, "path": filename, "sha256": _digest(path) if path.exists() else "0" * 64, "size_bytes": path.stat().st_size if path.exists() else 0, "depends_on": dependencies})
    (package / "decode.json").write_text(json.dumps({"format": fmt, "artifacts": artifacts}), encoding="utf-8")


def test_jsonl_preserves_unknown_duplicate_and_relationship_diagnostics(tmp_path: Path) -> None:
    package = tmp_path / "jsonl"
    package.mkdir()
    native = package / "events.jsonl"
    rows = [
        {"id": "s", "session_id": "session", "kind": "session", "fields": {}},
        {"id": "b", "session_id": "session", "kind": "branch", "fields": {"parent_id": "missing"}},
        {"id": "b", "session_id": "session", "kind": "unknown_future", "fields": {}},
        {"id": "b", "session_id": "session", "kind": "message", "fields": {"text": "duplicate"}},
    ]
    native.write_bytes(b"\n".join(json.dumps(row).encode() for row in rows) + b"\n{" + b"broken\n")
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", [])])

    result = decode_native(package)

    assert result["format"] == "constructed-jsonl-v1"
    assert len(result["events"]) == 4
    assert result["unknown_records"] == 1
    codes = [item["code"] for item in result["diagnostics"]]
    assert "unknown_record" in codes
    assert "duplicate_id" in codes
    assert "missing_parent" in codes
    assert "malformed_record" in codes
    assert result["events"][0]["locator"]["line"] == 1
    assert result["events"][0]["locator"]["record_sha256"]


def test_jsonl_missing_companion_keeps_primary_events(tmp_path: Path) -> None:
    package = tmp_path / "jsonl"
    package.mkdir()
    native = package / "events.jsonl"
    native.write_text(json.dumps({"id": "m", "session_id": "s", "kind": "message", "fields": {}}) + "\n", encoding="utf-8")
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", ["attachment"]), ("attachment", "missing.bin", [])])
    result = decode_native(package)
    assert [event["id"] for event in result["events"]] == ["m"]
    assert any(item["code"] == "missing_dependency" for item in result["diagnostics"])
    assert any(item["code"] == "missing_artifact" for item in result["diagnostics"])


def test_package_rejects_bad_digest_and_symlink(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    native = package / "events.jsonl"
    native.write_text("{}\n", encoding="utf-8")
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", [])])
    manifest = json.loads((package / "decode.json").read_text())
    manifest["artifacts"][0]["sha256"] = "0" * 64
    (package / "decode.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        decode_native(package)

    symlink_package = tmp_path / "symlink"
    symlink_package.mkdir()
    (symlink_package / "events.jsonl").symlink_to(native)
    (symlink_package / "decode.json").write_text(json.dumps({"format": "constructed-jsonl-v1", "artifacts": [{"id": "events", "path": "events.jsonl", "sha256": _digest(native), "size_bytes": native.stat().st_size, "depends_on": []}]}))
    with pytest.raises(ValueError, match="symlink"):
        decode_native(symlink_package)


def test_sqlite_wal_rows_decode_and_source_files_remain_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source"
    package = tmp_path / "sqlite"
    source.mkdir()
    package.mkdir()
    database = source / "events.db"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE events (row_key INTEGER PRIMARY KEY, payload TEXT)")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps({"id": "one", "session_id": "s", "kind": "message", "fields": {"text": "wal"}}),))
    connection.commit()
    companions = [path for path in (source / "events.db-wal", source / "events.db-shm") if path.exists()]
    assert companions, "the writer must leave WAL/SHM evidence before capture"
    for path in [database, *companions]:
        target = package / path.name
        target.write_bytes(path.read_bytes())
    before = {path.name: _digest(path) for path in [database, *(package / path.name for path in companions)]}
    # The main database alone does not contain the uncheckpointed event.
    main_only_path = tmp_path / "main-only.db"
    main_only_path.write_bytes(database.read_bytes())
    main_only = sqlite3.connect(f"file:{main_only_path}?mode=ro", uri=True)
    assert main_only.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    main_only.close()
    _manifest(package, "constructed-sqlite-v1", [("db", "events.db", [])] + [(path.name, path.name, ["db"]) for path in companions])

    result = decode_native(package)

    assert [event["id"] for event in result["events"]] == ["one"]
    assert all(event["locator"]["table"] == "events" for event in result["events"])
    assert result["events"][0]["locator"]["dependency_sha256"]
    assert not [item for item in result["diagnostics"] if item["code"] == "decode_error"]
    assert {path.name: _digest(path) for path in [database, *(package / path.name for path in companions)]} == before
    connection.close()


def test_sqlite_unexpected_schema_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "sqlite"
    package.mkdir()
    database = package / "events.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE other (value TEXT)")
    connection.commit()
    connection.close()
    _manifest(package, "constructed-sqlite-v1", [("db", "events.db", [])])
    result = decode_native(package)
    assert not result["events"]
    assert any(item["code"] == "unsupported_schema" for item in result["diagnostics"])


def test_sqlite_trigger_is_rejected_by_schema_source_check(tmp_path: Path) -> None:
    package = tmp_path / "sqlite-trigger"
    package.mkdir()
    database = package / "events.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE events (row_key INTEGER PRIMARY KEY, payload TEXT)")
    connection.execute("CREATE TRIGGER write_event AFTER INSERT ON events BEGIN SELECT 1; END")
    connection.commit()
    connection.close()
    _manifest(package, "constructed-sqlite-v1", [("db", "events.db", [])])
    result = decode_native(package)
    assert not result["events"]
    assert any(item["code"] == "unsupported_schema" for item in result["diagnostics"])


def test_sqlite_null_and_bytes_payloads_are_diagnostics(tmp_path: Path) -> None:
    package = tmp_path / "sqlite-payloads"
    package.mkdir()
    database = package / "events.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE events (row_key INTEGER PRIMARY KEY, payload TEXT)")
    connection.execute("INSERT INTO events(payload) VALUES (NULL)")
    connection.execute("INSERT INTO events(payload) VALUES (?)", (b'{"id":"bytes","session_id":"s","kind":"message","fields":{}}',))
    connection.commit()
    connection.close()
    _manifest(package, "constructed-sqlite-v1", [("db", "events.db", [])])
    result = decode_native(package)
    assert [event["id"] for event in result["events"]] == ["bytes"]
    assert any("NULL payload" in item["detail"] for item in result["diagnostics"])


def test_sqlite_without_primary_extension_is_explicitly_unsupported(tmp_path: Path) -> None:
    package = tmp_path / "sqlite-no-primary"
    package.mkdir()
    native = package / "native.bin"
    native.write_bytes(b"opaque")
    _manifest(package, "constructed-sqlite-v1", [("native", "native.bin", [])])
    result = decode_native(package)
    assert not result["events"]
    assert any(item["code"] == "unsupported_schema" for item in result["diagnostics"])


def test_empty_package_and_opaque_attachment_are_not_parsed(tmp_path: Path) -> None:
    package = tmp_path / "empty"
    package.mkdir()
    (package / "decode.json").write_text(json.dumps({"format": "constructed-jsonl-v1", "artifacts": []}), encoding="utf-8")
    assert decode_native(package) == {"format": "constructed-jsonl-v1", "events": [], "diagnostics": [], "unknown_records": 0}

    package = tmp_path / "attachment"
    package.mkdir()
    (package / "events.jsonl").write_bytes(b'{"id":"m","session_id":"s","kind":"message","fields":{}}\n')
    (package / "blob.bin").write_bytes(b'{"id":"fake","session_id":"s","kind":"message","fields":{}}\n')
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", ["blob"]), ("blob", "blob.bin", [])])
    result = decode_native(package)
    assert [event["id"] for event in result["events"]] == ["m"]


def test_strict_json_and_session_scoped_relationships(tmp_path: Path) -> None:
    package = tmp_path / "strict"
    package.mkdir()
    native = package / "events.jsonl"
    native.write_bytes(
        b'{"id":"same","session_id":"a","kind":"message","fields":{}}\n'
        b'{"id":"same","session_id":"b","kind":"message","fields":{}}\n'
        b'{"id":"r","session_id":"a","kind":"tool_result","fields":{"tool_call_id":"missing"}}\n'
        b'{"id":"bad","session_id":"a","kind":"message","fields":{"x":NaN}}\n'
        b'{"id":"dup","id":"other","session_id":"a","kind":"message","fields":{}}\n'
    )
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", [])])
    result = decode_native(package)
    assert len(result["events"]) == 3
    assert not any(item["code"] == "duplicate_id" for item in result["diagnostics"])
    assert any(item["code"] == "dangling_tool_result" for item in result["diagnostics"])
    assert sum(item["code"] == "malformed_record" for item in result["diagnostics"]) == 2


def test_missing_and_empty_ids_are_explicit_and_tool_call_id_joins(tmp_path: Path) -> None:
    package = tmp_path / "ids"
    package.mkdir()
    native = package / "events.jsonl"
    native.write_bytes(
        b'{"id":"wire","session_id":"s","kind":"tool_call","fields":{}}\n'
        b'{"id":"result","session_id":"s","kind":"tool_result","fields":{"tool_call_id":"wire"}}\n'
        b'{"id":"","session_id":"s","kind":"message","fields":{}}\n'
        b'{"session_id":"s","kind":"message","fields":{}}\n'
    )
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", [])])
    result = decode_native(package)
    assert [event["identity_origin"] for event in result["events"]] == ["native", "native", "derived"]
    assert sum(item["code"] == "missing_event_id" for item in result["diagnostics"]) == 2
    assert not any(item["code"] == "dangling_tool_result" for item in result["diagnostics"])

    # A field-level alias alone is not a native tool-call identity.
    native.write_bytes(b'{"id":"call","session_id":"s","kind":"tool_call","fields":{"tool_call_id":"wire"}}\n{"id":"result","session_id":"s","kind":"tool_result","fields":{"tool_call_id":"wire"}}\n')
    _manifest(package, "constructed-jsonl-v1", [("events", "events.jsonl", [])])
    result = decode_native(package)
    assert any(item["code"] == "dangling_tool_result" for item in result["diagnostics"])


def test_manifest_rejects_noncanonical_duplicate_and_boolean_paths(tmp_path: Path) -> None:
    package = tmp_path / "bad"
    package.mkdir()
    (package / "events.jsonl").write_text("", encoding="utf-8")
    digest = _digest(package / "events.jsonl")
    base = {"format": "constructed-jsonl-v1", "artifacts": [{"id": "a", "path": "./events.jsonl", "sha256": digest, "size_bytes": False, "depends_on": []}]}
    (package / "decode.json").write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError):
        decode_native(package)

    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    (duplicate / "events.jsonl").write_text("", encoding="utf-8")
    digest = _digest(duplicate / "events.jsonl")
    manifest = {"format": "constructed-jsonl-v1", "artifacts": [
        {"id": "a", "path": "events.jsonl", "sha256": digest, "size_bytes": 0, "depends_on": []},
        {"id": "b", "path": "events.jsonl", "sha256": digest, "size_bytes": 0, "depends_on": []},
    ]}
    (duplicate / "decode.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        decode_native(duplicate)


def test_codex_rollout_requires_explicit_package_and_decodes_c01_c02(tmp_path: Path):
    package = tmp_path / "codex"
    package.mkdir()
    rollout = package / "rollout.jsonl"
    rows = [
        {"timestamp": "2026-09-10T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/private/should-not-be-read", "cli_version": "0.154.0", "model_provider": "openai"}},
        {"timestamp": "2026-09-10T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "SB_F0_C01_café_🙂"}},
        {"timestamp": "2026-09-10T00:00:02Z", "type": "response_item", "payload": {"type": "local_shell_call", "id": "tc1", "call_id": "call-1", "status": "completed", "action": {"type": "exec", "command": ["python3", "fixture_project/test_target.py"], "working_directory": "."}}},
        {"timestamp": "2026-09-10T00:00:03Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "expected 2, got 1"}},
        {"timestamp": "2026-09-10T00:00:04Z", "type": "response_item", "payload": {"type": "agent_message", "content": [{"type": "output_text", "text": "done"}]}},
    ]
    rollout.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    _manifest(package, "codex-rollout-v1", [("rollout", "rollout.jsonl", [])])
    result = decode_native(package)
    assert result["format"] == "codex-rollout-v1"
    assert [event["kind"] for event in result["events"]] == ["session", "message", "tool_call", "tool_result", "message"]
    assert result["events"][1]["fields"]["text"] == "SB_F0_C01_café_🙂"
    assert result["events"][2]["fields"]["command"] == ["python3", "fixture_project/test_target.py"]
    assert result["events"][3]["fields"]["output"] == "expected 2, got 1"
    assert {event["session_id"] for event in result["events"]} == {"s1"}


def test_codex_rollout_unknown_and_malformed_records_are_diagnostics(tmp_path: Path):
    package = tmp_path / "codex"
    package.mkdir()
    rollout = package / "rollout.jsonl"
    rollout.write_bytes(b'{"type":"turn_context","payload":{"id":"ignored"}}\n{"payload":\n')
    _manifest(package, "codex-rollout-v1", [("rollout", "rollout.jsonl", [])])
    result = decode_native(package)
    assert not result["events"]
    assert {item["code"] for item in result["diagnostics"]} == {"unknown_record", "malformed_record"}


@pytest.mark.parametrize("name,session_id", [("rollout-c01.jsonl", "constructed-c01"), ("rollout-c02.jsonl", "constructed-c02")])
def test_public_codex_l0_records_decode_as_explicit_packages(tmp_path: Path, name: str, session_id: str):
    source = Path(__file__).resolve().parents[1] / "fixtures" / "l0" / "codex-cli-0.154.0" / name
    package = tmp_path / name.removesuffix(".jsonl")
    package.mkdir()
    rollout = package / "rollout.jsonl"
    rollout.write_bytes(source.read_bytes())
    _manifest(package, "codex-rollout-v1", [("rollout", "rollout.jsonl", [])])
    decoded = decode_native(package)
    assert decoded["events"]
    assert {event["session_id"] for event in decoded["events"]} == {session_id}
    assert not any(item["code"] == "dangling_tool_result" for item in decoded["diagnostics"])
