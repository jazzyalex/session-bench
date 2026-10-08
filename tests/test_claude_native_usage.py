import hashlib
import json

from session_bench.claude_live import native_facts_from_claude_session


def test_claude_native_usage_maps_cache_read_and_thinking_fields(tmp_path):
    usage = {
        "input_tokens": 113,
        "output_tokens": 29,
        "cache_read_input_tokens": 71,
        "cache_creation_input_tokens": 13,
        "output_tokens_details": {"thinking_tokens": 7},
    }
    rows = [
        {
            "type": "user",
            "uuid": "user-r1",
            "sessionId": "session-1",
            "message": {"role": "user", "content": "R1 prompt"},
        },
        {
            "type": "assistant",
            "uuid": "assistant-r1",
            "sessionId": "session-1",
            "message": {
                "id": "message-r1",
                "role": "assistant",
                "model": "claude-test-model",
                "content": [{"type": "text", "text": "Answer SB_SURVIVAL_V1_RESPONSE_R1"}],
                "usage": usage,
            },
        },
    ]
    session = "".join(json.dumps(row) + "\n" for row in rows).encode()
    facts = native_facts_from_claude_session(
        session,
        run_canary="SB_SURVIVAL_V1_RUN_test",
        workspace=tmp_path,
        before_sha256=hashlib.sha256(b"before").hexdigest(),
        after_sha256=hashlib.sha256(b"after").hexdigest(),
        usage_mode="full",
    )

    assert facts["usage"][0]["usage"] == {
        "input_tokens": 113,
        "output_tokens": 29,
        "reasoning_tokens": 7,
        "cache_read_tokens": 71,
        "cache_write_tokens": 13,
    }


def test_missing_optional_usage_fields_remain_unknown_in_native_facts(tmp_path):
    rows = [
        {
            "type": "user",
            "uuid": "user-r1",
            "sessionId": "session-1",
            "message": {"role": "user", "content": "R1 prompt"},
        },
        {
            "type": "assistant",
            "uuid": "assistant-r1",
            "sessionId": "session-1",
            "message": {
                "id": "message-r1",
                "role": "assistant",
                "model": "claude-test-model",
                "content": [{"type": "text", "text": "Answer SB_SURVIVAL_V1_RESPONSE_R1"}],
                "usage": {"input_tokens": 113, "output_tokens": 29},
            },
        },
    ]
    facts = native_facts_from_claude_session(
        ("".join(json.dumps(row) + "\n" for row in rows)).encode(),
        run_canary="SB_SURVIVAL_V1_RUN_test",
        workspace=tmp_path,
        before_sha256=hashlib.sha256(b"before").hexdigest(),
        after_sha256=hashlib.sha256(b"after").hexdigest(),
    )

    assert facts["usage"][0]["usage"] == {
        "input_tokens": 113,
        "output_tokens": 29,
    }
