import json

from scripts.package_claude_desktop_capture import _remove_unique_canary


def test_selected_loss_removes_only_assistant_canary_record(tmp_path):
    canary = "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "damaged.jsonl"
    rows = [
        {"type": "queue-operation", "content": canary},
        {"type": "user", "message": {"role": "user", "content": f"End with {canary}"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": canary}]}},
    ]
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    assert _remove_unique_canary(source, destination, canary) == 1
    remaining = [json.loads(line) for line in destination.read_text().splitlines()]
    assert [row["type"] for row in remaining] == ["queue-operation", "user"]


def test_selected_loss_rejects_duplicate_assistant_canaries(tmp_path):
    canary = "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "damaged.jsonl"
    row = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": canary}]}}
    source.write_text(json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False) + "\n")

    try:
        _remove_unique_canary(source, destination, canary)
    except ValueError as exc:
        assert "found 2" in str(exc)
    else:
        raise AssertionError("duplicate assistant canaries must be rejected")
