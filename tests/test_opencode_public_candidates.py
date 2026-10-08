import importlib.util
from pathlib import Path
import sqlite3
import pytest
from session_bench.native_replay import canonical, _snapshot_tree

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("opencode_public_transform", ROOT / "scripts/sanitize_opencode_score_packets.py")
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def test_equal_size_alias_repairs_transitive_proof_digests_without_touching_native():
    raw = b'{"PATH":"/Users/person/bin:/usr/bin"}'
    proof = canonical({"sha256": module.sha(raw), "size_bytes": len(raw)})
    assertion = canonical({"proof_sha256": module.sha(proof)})
    original = {"inputs/plan.json": raw, "inputs/proof.json": proof, "inputs/assertion.json": assertion,
                "native/opencode.db": b"SQLite format 3\x00arbitrary-pages", "native/opencode.db-wal": b"arbitrary-wal", "native/opencode.db-shm": b"shared-index"}
    result, counts = module.transform_documents(original)
    assert result["inputs/plan.json"] == b'{"PATH":"/Users/xxxxxx/bin:/usr/bin"}'
    assert module.sha(result["inputs/plan.json"]).encode() in result["inputs/proof.json"]
    assert module.sha(result["inputs/proof.json"]).encode() in result["inputs/assertion.json"]
    assert all(len(data) == len(original[name]) for name, data in result.items())
    assert counts == {"home_alias_count": 1, "sqlite_files_unchanged": 3}
    assert all(result[name] == original[name] for name in original if name.startswith("native/"))


@pytest.mark.parametrize("suffix", module.SQLITE_SUFFIXES)
def test_private_home_even_in_dead_sqlite_bytes_refuses_transform(suffix):
    # SQLite free/WAL pages remain privacy-bearing physical bytes even when no
    # live logical SQL row exposes this string. A byte scan must refuse them.
    with pytest.raises(ValueError, match="physical SQLite"):
        module.transform_documents({"inputs/root-proof/1/native/opencode" + suffix: b"unused-page\x00/Users/person\x00"})


@pytest.mark.parametrize("data", [b"me@gmail.com", b"sk-ant-abcdefghijklmnopqrstuvwxyz12345"])
def test_email_and_credential_cannot_be_laundered_as_safe_home_alias(data):
    with pytest.raises(ValueError, match="email or credential"):
        module.transform_documents({"inputs/proof.json": data})


def test_real_three_root_families_keep_every_physical_byte_and_source_validates():
    from session_bench.opencode_score_inputs import validate_opencode_capture_assertion
    import json
    packet = ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-native-score-v2/opencode-1-18-31-eval-1"
    tree = _snapshot_tree(packet)
    original = {name: data for name, data in tree.items() if name.startswith(("inputs/", "native/"))}
    transformed, counts = module.transform_documents(original)
    assert counts["sqlite_files_unchanged"] == 12
    assert all(original[name] == data for name, data in transformed.items() if name.endswith(module.SQLITE_SUFFIXES))
    assert validate_opencode_capture_assertion(transformed, json.loads(transformed["inputs/context.json"]), json.loads(transformed["native/decode.json"])) == (True, True)
    assert _snapshot_tree(packet) == tree
