"""Personal instruction text is removed from Codex rollouts at equal byte length."""

from __future__ import annotations

import json

import pytest

import scripts.sanitize_codex_score_packets as module
from scripts.sanitize_codex_score_packets import redact_codex_rollout

PERSONAL_RULES = "# Global Agent Instructions\n\n- Never touch the \"tennis\" scraper — ask first.\n"
SKILLS = "\n## Skills\n- tennis-usta-auth: sign in to a private account (file: /Users/someone/.codex/skills/x)\n"


def _line(record: dict) -> bytes:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _message(role: str, text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                 "content": [{"type": "input_text", "text": text}]}}


def _rollout() -> bytes:
    return b"\n".join(_line(record) for record in (
        {"type": "session_meta", "payload": {"session_id": "s", "cli_version": "0.154.0"}},
        {"type": "world_state", "timestamp": "2026-09-15T02:17:00.139Z", "payload": {"full": True, "state": {
            "agents_md": {"text": PERSONAL_RULES}, "host_skills": {"body": SKILLS}, "model": "model",
            "approved": [["python3", "scripts/usta/update_reports.py"], ["rm", "-rf", "private-folder"]]}}},
        _message("developer", "<skills_instructions>" + SKILLS + "</skills_instructions>"),
        _message("user", "# AGENTS.md instructions\n\n<INSTRUCTIONS>\n" + PERSONAL_RULES + "</INSTRUCTIONS>"),
        _message("user", "This is the synthetic task prompt with a tennis word the user typed."),
        _message("developer", "<permissions instructions>\nvendor boilerplate\n</permissions instructions>"),
    )) + b"\n"


def test_personal_instruction_text_is_gone() -> None:
    redacted, count = redact_codex_rollout(_rollout())

    assert b"Global Agent Instructions" not in redacted
    assert b"tennis-usta-auth" not in redacted
    assert b"scraper" not in redacted


def test_every_string_in_the_world_state_is_blanked_but_its_keys_and_envelope_stay() -> None:
    redacted, _ = redact_codex_rollout(_rollout())
    record = json.loads(redacted.splitlines()[1])

    assert b"update_reports" not in redacted and b"private-folder" not in redacted
    assert record["type"] == "world_state"
    assert record["timestamp"] == "2026-09-15T02:17:00.139Z"
    assert set(record["payload"]["state"]) == {"agents_md", "host_skills", "model", "approved"}
    assert record["payload"]["state"]["model"] != "model"


def test_every_line_keeps_its_byte_length_and_stays_json() -> None:
    original = _rollout()
    redacted, _ = redact_codex_rollout(original)

    before, after = original.splitlines(), redacted.splitlines()
    assert [len(line) for line in after] == [len(line) for line in before]
    assert all(isinstance(json.loads(line), dict) for line in after)


def test_block_markers_and_other_messages_are_kept() -> None:
    redacted, _ = redact_codex_rollout(_rollout())
    records = [json.loads(line) for line in redacted.splitlines()]
    texts = [record["payload"]["content"][0]["text"] for record in records if record["type"] == "response_item"]

    assert texts[0].startswith("<skills_instructions>\n")
    assert texts[1].startswith("# AGENTS.md instructions\n")
    assert texts[2] == "This is the synthetic task prompt with a tennis word the user typed."
    # A developer-role instruction message is vendor text: blanked with the vendor marker at equal length.
    vendor = "<permissions instructions>\nvendor boilerplate\n</permissions instructions>"
    assert texts[3].startswith("[vendor instruction text removed") and set(texts[3][54:]) == {"x"}
    assert len(json.dumps(texts[3])) == len(json.dumps(vendor))  # equal bytes in the file


def test_a_rollout_without_personal_blocks_is_unchanged() -> None:
    data = _line({"type": "session_meta", "payload": {"session_id": "s"}}) + b"\n"

    assert redact_codex_rollout(data) == (data, 0)


def test_ascii_escaped_text_is_blanked_at_equal_length_too() -> None:
    record = _message("user", "# AGENTS.md instructions\n\nrègle privée \"citée\"\n")
    data = json.dumps(record, ensure_ascii=True).encode("utf-8") + b"\n"

    redacted, count = redact_codex_rollout(data)

    assert count == 1 and len(redacted) == len(data)
    assert json.loads(redacted)["payload"]["content"][0]["text"].startswith("# AGENTS.md instructions\n")
    assert b"priv" not in redacted


def test_a_line_with_duplicate_keys_is_refused() -> None:
    data = b'{"type":"world_state","payload":{"state":{"a":"x","a":"y"}}}\n'

    with pytest.raises(ValueError, match="cannot align"):
        redact_codex_rollout(data)


def test_carried_receipt_digests_are_zeroed_at_equal_length_and_other_values_stay():
    import json
    receipt = {'intact_decoded_sha256': 'a' * 64, 'offline_decoded_sha256': 'a' * 64, 'intact_measurement_sha256': 'b' * 64,
               'offline_measurement_sha256': 'b' * 64, 'damaged_measurement_sha256': 'c' * 64,
               'public_sanitization': {'sanitized_sha256': 'd' * 64}, 'native_sha256': 'e' * 64}
    data = json.dumps(receipt).encode()
    for name in ('codex-cli-eval-1/inputs/capture-assertion.json', 'codex-cli-eval-1/inputs/original-capture-receipt.json',
                 'codex-cli-eval-2/inputs/root-evidence/repetition-1/calibration-receipt.json'):
        changed = module.zero_carried_receipt_digests(name, data)
        value = json.loads(changed)
        assert len(changed) == len(data) and value['native_sha256'] == 'e' * 64
        assert all(value[field] == '0' * 64 for field in module.CARRIED_RECEIPT_DIGESTS) and value['public_sanitization']['sanitized_sha256'] == '0' * 64
    assert module.zero_carried_receipt_digests('codex-cli-eval-1/inputs/observer.json', data) == data


VENDOR = b"[vendor instruction text removed at equal byte length]"


def test_vendor_instruction_text_is_blanked_at_equal_length_and_scored_text_stays():
    part = lambda kind, text: {"type": "input_text", "text": text}
    message = lambda role, kinds, texts: {"type": "response_item", "payload": {"type": "message", "id": "m", "role": role,
        "content": [part(kind, text) for kind, text in zip(kinds, texts)],
        "internal_chat_message_metadata_passthrough": {"turn_id": "t", "content_item_kinds": list(kinds)}}}
    rows = [
        {"type": "session_meta", "payload": {"session_id": "s", "cli_version": "0.154.0", "originator": "codex_exec",
                                             "base_instructions": {"text": "You are Codex, an agent. " + "Follow the rules. " * 40}}},
        message("developer", ["host_skills.instructions", "permissions.instructions"],
                ["<skills_instructions>\n" + PERSONAL_RULES, "<permissions instructions>\nFilesystem sandboxing defines which files can be read. " * 4]),
        message("developer", ["multi_agent.usage_hint"], ["You are `/root`, the primary agent in a team of agents. " * 10]),
        message("user", ["plugins.recommendations", "agents_md.instructions", "environments.environment_context"],
                ["<recommended_plugins>\nHere is a list of plugins. " * 20, "# AGENTS.md instructions\n" + PERSONAL_RULES, "<environment_context>\n  <cwd>/w</cwd>\n</environment_context>"]),
        message("user", ["user.text"], ["Requirement R1: fix checkout. " * 30]),
        {"type": "response_item", "payload": {"type": "message", "id": "a", "role": "assistant", "content": [{"type": "output_text", "text": "Done. " * 200}]}},
        {"type": "turn_context", "payload": {"turn_id": "t", "model": "gpt-5.6-sol"}},
    ]
    data = b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows)
    changed, count = redact_codex_rollout(data)
    assert len(changed) == len(data)
    before, after = [json.loads(line) for line in data.split(b"\n")], [json.loads(line) for line in changed.split(b"\n")]
    text = lambda row, index: row["payload"]["content"][index]["text"]
    marker = VENDOR.decode()
    assert after[0]["payload"]["base_instructions"]["text"].startswith(marker) and after[0]["payload"]["cli_version"] == "0.154.0"
    # A personal block keeps its tag line and the personal marker; a vendor block gets the vendor marker.
    assert text(after[1], 0).startswith("<skills_instructions>\n[personal text removed") and text(after[1], 1).startswith(marker)
    assert text(after[2], 0).startswith(marker)
    assert text(after[3], 0).startswith(marker) and text(after[3], 1).startswith("# AGENTS.md instructions\n[personal text removed")
    assert text(after[3], 2) == text(before[3], 2)  # the environment of the run is not instruction text
    # Scored text, roles, kinds and every other value stay.
    assert after[4:] == before[4:] and count == 6
    for old, new in zip(before, after):  # every string keeps its byte length in the file
        assert [len(json.dumps(part["text"], ensure_ascii=False).encode()) for part in old["payload"].get("content", [])] == [
            len(json.dumps(part["text"], ensure_ascii=False).encode()) for part in new["payload"].get("content", [])]
    assert after[3]["payload"]["internal_chat_message_metadata_passthrough"] == before[3]["payload"]["internal_chat_message_metadata_passthrough"]
    assert redact_codex_rollout(changed)[0] == changed


def test_guard_finds_a_copy_of_blanked_instruction_text_in_any_file_of_the_packet():
    prompt = "The server provides tools to interact with the platform. Use list tools for broad retrieval and pagination of all items."
    phrases = module.instruction_phrases([prompt, "/private/tmp/run-1234/project/fixture_project/checkout.py and more path text here", "short"])
    # Prose windows only: a path is a run fact that many files state, and a short string proves nothing.
    assert phrases and all("/" not in phrase and len(phrase) == 48 for phrase in phrases)
    clean = {"run/native/events.jsonl": b'{"type":"system.message","data":{"content":"[vendor instruction text removed at equal byte length]xxxx"}}\n',
             "run/inputs/receipt.json": b'{"cwd":"/private/tmp/run-1234/project/fixture_project/checkout.py and more path text here"}'}
    assert module.require_no_instruction_phrase(clean, phrases) == len(phrases)
    for name, data in {
        "run/inputs/capture/r1.stdout": json.dumps({"type": "session.mcp_servers_loaded", "data": {"servers": [{"serverMetadata": {"instructions": prompt}}]}}).encode() + b"\n",
        "run/inputs/observer.json": json.dumps({"events": [{"fields": {"raw": json.dumps({"instructions": "x\n" + prompt})}}]}).encode(),  # nested JSON text
        "run/inputs/notes.txt": b"plain text copy: " + prompt.encode(),
    }.items():
        with pytest.raises(ValueError, match=name.rsplit("/", 1)[1]):
            module.require_no_instruction_phrase({**clean, name: data}, phrases)
