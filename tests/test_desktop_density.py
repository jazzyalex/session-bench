"""Full Claude Desktop persistent pairs use the same canonical JSON byte rule."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from session_bench.desktop_density import inventory_claude_desktop_density
from session_bench.native_density import BYTE_ACCOUNTING_RULE
from session_bench.v1_public_score import validate_format_evidence


def pair(path):
    (path / "transcript").mkdir(parents=True);(path / "desktop").mkdir()
    rows = [
        {"type": "user", "uuid": "u1", "sessionId": "s1", "message": {"role": "user", "content": "input Δ"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "s1", "message": {"role": "assistant", "content": "answer 🙂"}},
        {"type": "future_type", "sessionId": "s1", "opaque": "retain every field"},
    ]
    (path / "transcript/session.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, indent=0) .replace("\n", " ") + "\n" for row in rows))
    metadata = {"sessionId": "local_test", "cliSessionId": "s1", "bridgeSessionIds": ["bridge"], "cwd": "/synthetic/fixture", "model": "test-model", "opaque": "keep metadata"}
    (path / "desktop/session.json").write_text(json.dumps(metadata, indent=4))
    return rows + [metadata]


def inventory(path, digest=None):
    digest = digest or hashlib.sha256((path / "transcript/session.jsonl").read_bytes()).hexdigest()
    return inventory_claude_desktop_density(path, session_id="s1", expected_transcript_artifacts=[{"id": "session", "sha256": digest}])


def test_complete_pair_includes_full_metadata_and_unknown_records_in_same_byte_rule(tmp_path):
    path = tmp_path / "family";expected = pair(path)
    before = {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}
    value = inventory(path)
    assert value.evidence["evidence_complete"] and value.byte_accounting_rule == BYTE_ACCOUNTING_RULE
    rows = value.evidence["records"]
    assert len(rows) == 4
    assert sorted(row["logical_bytes"] for row in rows) == sorted(len(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) for row in expected)
    assert {row["record_kind"] for row in rows} == {"metadata", "user_message", "assistant_message", "unknown"}
    assert sum(row["classification"] == "useful" for row in rows) == 2
    assert {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()} == before
    assert any("desktop_density.py" in row["id"] for row in value.native_locators)
    assert any("native_density.py" in row["id"] for row in value.native_locators)


@pytest.mark.parametrize("mutation", [
    lambda p: (p / "desktop/session.json").unlink(),
    lambda p: (p / "extra.json").write_text('{}'),
    lambda p: (p / "desktop/session.json").write_text('{"cliSessionId":"other"}'),
    lambda p: (p / "transcript/session.jsonl").write_text('{"sessionId":"other"}\n'),
    lambda p: (p / "desktop/session.json").write_text('{"cliSessionId":"s1","cliSessionId":"s1"}'),
    lambda p: (p / "desktop/session.json").write_text('{"cliSessionId":"s1","opaque":NaN}'),
])
def test_missing_foreign_extra_or_malformed_family_is_unresolved(tmp_path, mutation):
    path = tmp_path / "family";pair(path);digest = hashlib.sha256((path / "transcript/session.jsonl").read_bytes()).hexdigest();mutation(path)
    assert not inventory(path, digest).evidence["evidence_complete"]


def test_digest_session_and_symlink_binding_cannot_be_bypassed(tmp_path):
    path = tmp_path / "family";pair(path)
    assert not inventory(path, "a" * 64).evidence["evidence_complete"]
    assert not inventory_claude_desktop_density(path, session_id="other", expected_transcript_artifacts=[{"id": "session", "sha256": hashlib.sha256((path / "transcript/session.jsonl").read_bytes()).hexdigest()}]).evidence["evidence_complete"]
    link = tmp_path / "link";link.symlink_to(path, target_is_directory=True)
    assert not inventory(link).evidence["evidence_complete"]


@pytest.mark.parametrize("complete", [False, True])
def test_claude_builder_requires_explicit_full_pair_and_capture_completeness(tmp_path, complete):
    from session_bench.adapters.claude_code_decoder import decode_claude_code_bundle
    from session_bench.claude_format_evidence import build_claude_format_evidence
    family = tmp_path / "family";pair(family)
    package = tmp_path / "native";package.mkdir();shutil.copyfile(family / "transcript/session.jsonl", package / "session.jsonl")
    data = (package / "session.jsonl").read_bytes()
    (package / "decode.json").write_text(json.dumps({"format": "claude-code-jsonl-v1", "artifacts": [{"id": "session", "path": "session.jsonl", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "depends_on": []}]}))
    decoded = decode_claude_code_bundle(package)
    kwargs = dict(observer={"id": "observer", "sha256": "a" * 64}, native_manifest={"id": "manifest", "sha256": "b" * 64}, run_id="test-run", configuration_id="claude-desktop", repetition=1, build="test-build", collected_on="2026-09-29", result_id="test-result", complete_record_family=complete, native_package=package)
    default = build_claude_format_evidence(decoded, **kwargs)
    assert not default["profile"]["broad_evidence"]["broad.classified_content_density"]["evidence_complete"]
    explicit = build_claude_format_evidence(decoded, desktop_family=family, **kwargs)
    detail = explicit["profile"]["broad_evidence"]["broad.classified_content_density"]
    assert detail["evidence_complete"] is complete
    row = next(row for row in validate_format_evidence(explicit)["profile"]["metrics"] if row["id"] == "broad.classified_content_density")
    assert row["state"] == ("measured" if complete else "unresolved")


@pytest.mark.parametrize("name", ["claude-desktop-eval-1-correction-1", "claude-desktop-eval-2-correction-1", "claude-desktop-eval-3"])
def test_retained_qualified_claude_desktop_pairs_close_and_account_all_objects(name):
    root = Path(__file__).resolve().parents[1] / "artifacts/survival-v1-runs" / name / "capture/finalized-private-v1/native-family-private"
    transcript = (root / "transcript/session.jsonl").read_bytes();metadata = json.loads((root / "desktop/session.json").read_bytes())
    value = inventory_claude_desktop_density(root, session_id=metadata['cliSessionId'], expected_transcript_artifacts=[{"id": "session", "sha256": hashlib.sha256(transcript).hexdigest()}])
    assert value.evidence["evidence_complete"]
    assert len(value.evidence["records"]) == len([line for line in transcript.splitlines() if line.strip()]) + 1


def test_user_record_that_repeats_the_queued_prompt_is_a_snapshot(tmp_path):
    path = tmp_path / "family";pair(path)
    transcript = path / "transcript/session.jsonl"
    transcript.write_text(json.dumps({"type": "queue-operation", "operation": "enqueue", "sessionId": "s1", "content": "input Δ"}, ensure_ascii=False) + "\n" + transcript.read_text())
    rows = {row["record_id"].rsplit(":", 2)[1] + ":" + row["record_id"].rsplit(":", 1)[1]: row["record_kind"] for row in inventory(path).evidence["records"]}
    assert rows == {"desktop/session.json:record-1": "metadata", "transcript/session.jsonl:record-1": "user_message",
                    "transcript/session.jsonl:record-2": "snapshot", "transcript/session.jsonl:record-3": "assistant_message",
                    "transcript/session.jsonl:record-4": "unknown"}
