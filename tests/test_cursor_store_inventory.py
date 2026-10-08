"""ACP graph and full blob accounting fail closed without interpreting tool payloads."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from session_bench.cursor_store_inventory import inventory_cursor_cli_store, protobuf_fields


def field(number, payload):
    assert len(payload) < 128
    return bytes([(number << 3) | 2, len(payload)]) + payload


def fixture(path):
    path.mkdir()
    blobs = {}
    def add(data):
        identity = hashlib.sha256(data).hexdigest();blobs[identity] = data
        return bytes.fromhex(identity)
    user = add(field(1, b"Independent fixture input"))
    response = add(field(1, b"Recorded assistant response"))
    assistant_step = add(field(1, response))
    opaque_tool = add(field(2, b"opaque-tool-payload"))
    agent_turn = field(1, user) + field(2, assistant_step) + field(2, opaque_tool)
    turn = add(field(1, agent_turn))
    root_id = add(field(8, turn)).hex()
    add(b"unreachable historical payload")
    connection = sqlite3.connect(path / "store.db")
    connection.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE blobs(id TEXT PRIMARY KEY,data BLOB)")
    connection.execute("INSERT INTO meta VALUES ('0',?)", (json.dumps({"agentId": "native-session", "latestRootBlobId": root_id}).encode().hex(),))
    connection.executemany("INSERT INTO blobs VALUES (?,?)", blobs.items());connection.commit();connection.close()
    (path / "meta.json").write_text(json.dumps({"schemaVersion": 1}))
    return blobs, user.hex()


def inventory(path):
    return inventory_cursor_cli_store(path, session_id="native-session")


def test_all_retained_blobs_are_counted_and_supported_text_graph_closes(tmp_path):
    path = tmp_path / "session";blobs, _ = fixture(path)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    value = inventory(path)
    assert value["capture_file_family_closed"] and value["known_graph_references_closed"]
    assert not value["complete_semantic_family"] and not value["density_qualified"]
    assert value["blob_count"] == len(blobs) == 7
    assert value["logical_blob_bytes"] == sum(map(len, blobs.values()))
    assert value["unclassified_blob_count"] == 1
    assert value["unsupported_payloads"] == {"tool-step": 1}
    assert [row["kind"] for row in value["text_records"]] == ["user_message", "assistant_message"]
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before


@pytest.mark.parametrize("mutation,reason", [
    (lambda con, user: con.execute("DELETE FROM blobs WHERE id=?", (user,)), "missing blob"),
    (lambda con, user: con.execute("UPDATE blobs SET data=? WHERE id=?", (b"altered", user)), "hash mismatch"),
    (lambda con, user: con.execute("CREATE TABLE extra(id TEXT)"), "table inventory"),
    (lambda con, user: con.execute("ALTER TABLE blobs ADD COLUMN extra TEXT"), "columns"),
    (lambda con, user: con.execute("INSERT INTO meta VALUES ('1','00')"), "unique"),
])
def test_broken_hash_reference_or_schema_cannot_qualify_graph(tmp_path, mutation, reason):
    path = tmp_path / "session";_, user = fixture(path)
    con = sqlite3.connect(path / "store.db");mutation(con, user);con.commit();con.close()
    with pytest.raises(ValueError, match=reason):
        inventory(path)


@pytest.mark.parametrize("mutation,reason", [
    (lambda path: (path / "meta.json").write_text('{"schemaVersion":1,"schemaVersion":1}'), "duplicate"),
    (lambda path: (path / "meta.json").write_text('{"schemaVersion":true}'), "schema"),
    (lambda path: (path / "extra.txt").write_text("extra"), "closure"),
    (lambda path: (path / "meta.json").unlink(), "closure"),
])
def test_file_family_and_sidecar_are_closed(tmp_path, mutation, reason):
    path = tmp_path / "session";fixture(path);mutation(path)
    with pytest.raises(ValueError, match=reason):
        inventory(path)


def test_session_identity_and_symlink_ancestors_are_rejected(tmp_path):
    path = tmp_path / "session";fixture(path)
    with pytest.raises(ValueError, match="identity"):
        inventory_cursor_cli_store(path, session_id="other-session")
    link = tmp_path / "link";link.symlink_to(path, target_is_directory=True)
    with pytest.raises(ValueError):
        inventory(link)
    (path / "meta.json").unlink();(path / "meta.json").symlink_to(tmp_path / "other.json")
    with pytest.raises(ValueError):
        inventory(path)


@pytest.mark.parametrize("raw", [b"\x00", b"\x0a\x03x", b"\x09x", b"\x0dxx", b"\x0b", b"\x80" * 10])
def test_malformed_protobuf_cannot_be_an_empty_field_population(raw):
    with pytest.raises(ValueError):
        protobuf_fields(raw)


@pytest.mark.parametrize("repetition", [1, 2])
def test_retained_cursor_cli_stores_have_bound_family_and_explicit_semantic_gaps(repetition):
    root = Path(__file__).resolve().parents[1]
    directory = next((root / f"artifacts/survival-v1-runs/cursor-cli-eval-{repetition}/cursor-config/chats").glob("*/*/store.db")).parent
    value = inventory_cursor_cli_store(directory, session_id=directory.name)
    assert value["blob_count"] == (57 if repetition == 1 else 30)
    assert value["known_graph_references_closed"]
    assert value["logical_blob_bytes"] > 0 and len(value["text_records"]) == (6 if repetition == 1 else 3)
    assert value["unsupported_payloads"] and not value["density_qualified"]
