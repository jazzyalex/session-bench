"""Offline controls using fabricated temp bytes, never a Cursor account store."""
import json
from pathlib import Path

import pytest

from scripts.build_cursor_desktop_eval4_private_replay import ATTEMPT, FILES, build, canonical, parse, sha

SESSION = "00000000-0000-0000-0000-000000000004"
COUNTS = {"bubbleId": 20, "checkpointId": 3, "composerData": 1, "ofsContent": 1}


def write(path: Path, value) -> None:
    path.write_bytes(canonical(value))


def refresh(source: Path) -> None:
    """Bind synthetic receipts to current synthetic bytes for semantic controls."""
    attempt = parse((source / FILES[0]).read_bytes())
    observed = attempt["observed"]
    for ordinal, raw_name, receipt_name in ((1, FILES[1], FILES[2]), (2, FILES[3], FILES[4])):
        raw = (source / raw_name).read_bytes()
        receipt = parse((source / receipt_name).read_bytes())
        receipt.update(transcript_sha256=sha(raw), size_bytes=len(raw), line_count=len(raw.splitlines()))
        observed[f"native_transcript_r{ordinal}_sha256"] = sha(raw)
        write(source / receipt_name, receipt)
    raw = (source / FILES[5]).read_bytes()
    receipt = parse((source / FILES[6]).read_bytes())
    receipt.update(private_rows_sha256=sha(raw), private_rows_size_bytes=len(raw))
    write(source / FILES[6], receipt)
    observed.update(global_store_rows_sha256=sha(raw), global_store_receipt_sha256=sha((source / FILES[6]).read_bytes()))
    write(source / FILES[0], attempt)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic-retained"
    (root / "capture").mkdir(parents=True)
    records, bubbles = [], []
    markers = ["SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂", "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"]
    for ordinal, marker in enumerate(markers, 1):
        prompt = f"Requirement R{ordinal}: synthetic only. Run canary: SB_SURVIVAL_V1_RUN_{ATTEMPT}"
        records.extend([
            {"role": "user", "message": {"content": [{"type": "text", "text": prompt}]}},
            {"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Shell", "input": {"command": "synthetic"}}]}},
            {"role": "assistant", "message": {"content": [{"type": "text", "text": marker}]}}])
        result = {"output": "synthetic output"}
        if ordinal == 1:
            result["exitCode"] = 1
        bubbles.extend([
            {"bubbleId": f"u{ordinal}", "type": 1, "text": prompt, "checkpointId": f"cp{ordinal}", "modelInfo": {"modelName": "synthetic-model"}},
            {"bubbleId": f"t{ordinal}", "type": 2, "toolFormerData": {"name": "run_terminal_command_v2", "status": "completed", "params": json.dumps({"command": "synthetic"}), "result": json.dumps(result)}},
            {"bubbleId": f"a{ordinal}", "type": 2, "text": marker}])
    bubbles.extend({"bubbleId": f"thinking-{i}", "type": 2} for i in range(14))
    rows = [{"store": "global.cursorDiskKV", "key": f"bubbleId:{SESSION}:{b['bubbleId']}", "value": json.dumps(b)} for b in bubbles]
    rows += [{"store": "global.cursorDiskKV", "key": f"checkpointId:{SESSION}:cp{i}", "value": "{}"} for i in (1, 2, 3)]
    rows += [
        {"store": "global.cursorDiskKV", "key": f"composerData:{SESSION}", "value": json.dumps({"composerId": SESSION, "fullConversationHeadersOnly": [{"bubbleId": b["bubbleId"]} for b in bubbles], "modelConfig": {"modelName": "synthetic-model"}})},
        {"store": "global.cursorDiskKV", "key": f"ofsContent:{SESSION}:file:///synthetic/private/path", "value": "opaque retained content"}]
    (root / FILES[1]).write_bytes(b"".join(canonical(r) for r in records[:3]))
    (root / FILES[3]).write_bytes(b"".join(canonical(r) for r in records))
    (root / FILES[5]).write_bytes(b"".join(canonical(r) for r in rows))
    for ordinal, name in ((1, FILES[2]), (2, FILES[4])):
        write(root / name, {"attempt_id": ATTEMPT, "schema_version": f"cursor-desktop-r{ordinal}-transcript-checkpoint-v1", "session_key_sha256": sha(SESSION.encode())})
    write(root / FILES[6], {"attempt_id": ATTEMPT, "schema_version": "cursor-desktop-exact-session-global-store-receipt-v1", "selected_row_count": 25, "key_family_counts": COUNTS, "session_key_sha256": sha(SESSION.encode()), "composer_header_count": 20, "referenced_checkpoint_count": 2, "unreferenced_checkpoint_row_count": 1, "native_family_complete": False, "score_eligible": False})
    write(root / FILES[0], {"attempt_id": ATTEMPT, "score_eligible": False, "observed": {"native_root_complete": False, "native_session_key_sha256": sha(SESSION.encode()), "global_store_selected_row_count": 25, "global_store_key_family_counts": COUNTS}})
    refresh(root)
    return root


def test_replay_is_deterministic_private_and_preserves_missing_exit(source, tmp_path):
    before = {name: (source / name).read_bytes() for name in FILES}
    first, second = tmp_path / "one", tmp_path / "two"
    report = build(source, first)
    assert build(source, second) == report
    for path in first.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (second / path.relative_to(first)).read_bytes()
    assert before == {name: (source / name).read_bytes() for name in FILES}
    decoded = parse((first / "decoded.private.json").read_bytes())
    assert [r["exit_code"] for r in decoded["results"]] == [1, None]
    assert report["missing_result_exit_codes"] == ["bubble-result-t2"]
    assert report["counts"]["turns"] == report["counts"]["responses"] == 2
    assert report["score"] is None and report["score_eligible"] is False
    assert report["public_safe"] is report["native_family_complete"] is report["observer_independent"] is False
    assert decoded["facts"]["usage"]["state"] != "known"
    assert decoded["metrics"]["portable.complete_root"]["state"] != "known"
    assert report["sources"][FILES[5]]["sha256"] == sha(before[FILES[5]])
    assert report["derived"]["decoded_sha256"] == sha((first / "decoded.private.json").read_bytes())
    with pytest.raises(ValueError, match="output already exists"):
        build(source, first)


@pytest.mark.parametrize("name", FILES[1:])
def test_source_tampering_fails_without_output(source, tmp_path, name):
    path = source / name
    if name in (FILES[2], FILES[4]):
        receipt = parse(path.read_bytes())
        receipt["transcript_sha256"] = "0" * 64
        write(path, receipt)
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises((ValueError, KeyError)):
        build(source, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("damage", ["foreign", "duplicate", "composer", "closure", "response", "checkpoint", "tool-count"])
def test_receipt_bound_semantic_damage_fails(source, tmp_path, damage):
    rows = [parse(line) for line in (source / FILES[5]).read_bytes().splitlines()]
    if damage == "foreign":
        rows[0]["key"] = rows[0]["key"].replace(SESSION, "10000000-0000-0000-0000-000000000004")
    elif damage == "duplicate":
        rows[0] = rows[1]
    else:
        index = 23 if damage in {"composer", "closure"} else (2 if damage == "response" else 0 if damage == "checkpoint" else 1)
        value = parse(rows[index]["value"])
        if damage == "composer": value["composerId"] = "wrong"
        if damage == "closure": value["fullConversationHeadersOnly"][0]["bubbleId"] = "missing"
        if damage == "response": value["text"] = "wrong response"
        if damage == "checkpoint": value["checkpointId"] = "missing"
        if damage == "tool-count": del value["toolFormerData"]
        rows[index]["value"] = json.dumps(value)
    (source / FILES[5]).write_bytes(b"".join(canonical(row) for row in rows))
    refresh(source)
    with pytest.raises(ValueError):
        build(source, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    assert not list(tmp_path.glob(".eval4-private-replay-*"))


def test_session_receipt_and_prefix_controls(source, tmp_path):
    receipt = parse((source / FILES[2]).read_bytes())
    receipt["session_key_sha256"] = "0" * 64
    write(source / FILES[2], receipt)
    with pytest.raises(ValueError, match="session hash mismatch"):
        build(source, tmp_path / "wrong-session")
    receipt["session_key_sha256"] = sha(SESSION.encode())
    write(source / FILES[2], receipt)
    raw = (source / FILES[1]).read_bytes().replace(b"synthetic only", b"changed prompt")
    (source / FILES[1]).write_bytes(raw)
    refresh(source)
    with pytest.raises(ValueError, match="exact prefix"):
        build(source, tmp_path / "wrong-prefix")


def test_source_symlink_is_rejected(source, tmp_path):
    path = source / FILES[1]
    copied = tmp_path / "external-transcript"
    copied.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copied)
    with pytest.raises(ValueError, match="escapes retained root"):
        build(source, tmp_path / "rejected")


def test_in_root_source_parent_symlink_is_rejected(source, tmp_path):
    capture = source / "capture"
    retained = source / "retained-capture"
    capture.rename(retained)
    capture.symlink_to(retained, target_is_directory=True)
    assert (capture / "r1-transcript-checkpoint.private.jsonl").resolve().is_relative_to(source)
    with pytest.raises(ValueError, match="source ancestor may not be a symlink"):
        build(source, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
