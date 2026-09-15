import json

import pytest

from session_bench.native_sanitize import sanitize_json_document, sanitize_jsonl_paths


def test_sanitizer_rewrites_nested_paths_and_binds_raw_digest(tmp_path) -> None:
    source = tmp_path / "raw.jsonl"
    source.write_text(json.dumps({"cwd": "/Users/alexm/run", "nested": ["/Users/alexm/x"], "/Users/alexm/key": 1}) + "\n")
    target = tmp_path / "public" / "session.jsonl"
    receipt = sanitize_jsonl_paths(source, target, replacements={"/Users/alexm": "$BENCH_HOME"})
    assert "/Users/" not in target.read_text()
    assert "$BENCH_HOME/run" in target.read_text()
    assert receipt["replacement_count"] == 3
    assert receipt["raw_publication_state"] == "withheld"


def test_sanitizer_rejects_unhandled_private_marker(tmp_path) -> None:
    source = tmp_path / "raw.jsonl"
    source.write_text(json.dumps({"secret": "Authorization: Bearer x"}) + "\n")
    with pytest.raises(ValueError, match="forbidden markers"):
        sanitize_jsonl_paths(source, tmp_path / "public.jsonl", replacements={"/Users/alexm": "$HOME"})


def test_json_document_sanitizer_binds_raw_and_canonical_derivative(tmp_path) -> None:
    source = tmp_path / "raw.json"
    source.write_text(json.dumps({"cwd": "/Users/alexm/run", "nested": ["/Users/alexm/x"]}), encoding="utf-8")
    target = tmp_path / "public" / "record.json"

    receipt = sanitize_json_document(source, target, replacements={"/Users/alexm": "$BENCH_HOME"})

    assert "/Users/" not in target.read_text()
    assert json.loads(target.read_text()) == {"cwd": "$BENCH_HOME/run", "nested": ["$BENCH_HOME/x"]}
    assert receipt["raw_publication_state"] == "withheld"
    assert receipt["replacement_count"] == 2
