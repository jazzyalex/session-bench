import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/sanitize_claude_desktop_n1_packet.py"
SPEC = importlib.util.spec_from_file_location("claude_desktop_n1_packet", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


def test_privacy_aliases_preserve_bytes_and_rebind_digest_chain():
    session_id = b"12345678-1234-1234-9234-123456789abc"
    native = (
        b'{"cwd":"/Users/homer/project","user":"homer",'
        b'"email":"person.name@gmail.com","session_id":"' + session_id +
        b'","scratch":"/private/tmp/claude-run-42"}'
    )
    decode = b'{"sha256":"' + hashlib.sha256(native).hexdigest().encode() + b'"}'
    proof = b'{"decode_sha256":"' + hashlib.sha256(decode).hexdigest().encode() + b'"}'

    changed, counts = module.transform_documents({
        "native/session.jsonl": native,
        "native/decode.json": decode,
        "inputs/proof.json": proof,
    })

    assert counts == {
        "home_alias_count": 1,
        "email_alias_count": 1,
        "uuid_alias_count": 1,
        "temporary_path_alias_count": 1,
    }
    assert all(len(changed[name]) == len(value) for name, value in {
        "native/session.jsonl": native,
        "native/decode.json": decode,
        "inputs/proof.json": proof,
    }.items())
    assert b"/Users/homer" not in changed["native/session.jsonl"]
    assert b"person.name@gmail.com" not in changed["native/session.jsonl"]
    assert session_id not in changed["native/session.jsonl"]
    assert b"/private/tmp/claude-run-42" not in changed["native/session.jsonl"]
    assert b"@example.test" in changed["native/session.jsonl"]
    assert hashlib.sha256(changed["native/session.jsonl"]).hexdigest().encode() in changed["native/decode.json"]
    assert hashlib.sha256(changed["native/decode.json"]).hexdigest().encode() in changed["inputs/proof.json"]


@pytest.mark.parametrize(
    "payload, message",
    [
        (b'{"authorization":"Bearer abcdefghijklmnop"}', "credential-like"),
        (b'{"resourceLogs":[{"scopeLogs":[{"logRecords":[]}]}]}', "raw OTel"),
        (b'{"api_key":"this-is-a-private-key"}', "credential-like"),
    ],
)
def test_transform_fails_closed_on_secrets_and_raw_otel_text(payload, message):
    with pytest.raises(ValueError, match=message):
        module.transform_documents({"inputs/private.json": payload})


def test_transform_rejects_text_in_an_otel_named_receipt():
    with pytest.raises(ValueError, match="OTel text"):
        module.transform_documents({
            "inputs/otel-usage-private.json": b'{"response":"verbatim assistant text"}',
        })


def test_vendor_attribution_is_not_treated_as_a_private_account():
    source = {"native/session.jsonl": b"Co-Authored-By: Claude <noreply@anthropic.com>"}
    changed, counts = module.transform_documents(source)
    assert changed == source
    assert counts["email_alias_count"] == 0


def test_builder_rejects_nonprospective_repetition_before_replay(tmp_path):
    source = tmp_path / "private-packet"
    source.mkdir()
    (source / "manifest.json").write_text(json.dumps({
        "configuration_id": "claude-desktop",
        "repetition": 2,
    }))

    with pytest.raises(ValueError, match="repetition-1"):
        module.build(source, tmp_path / "output")


def test_packet_inventory_hash_binds_paths_hashes_and_sizes():
    rows, digest = module._packet_inventory({"z": b"last", "a": b"first"})
    assert [row["path"] for row in rows] == ["a", "z"]
    assert rows[0]["sha256"] == hashlib.sha256(b"first").hexdigest()
    assert rows[0]["size_bytes"] == 5
    assert digest == hashlib.sha256(module.canonical(rows)).hexdigest()
