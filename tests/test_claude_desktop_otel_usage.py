import hashlib
import io
import json
from pathlib import Path

import pytest

from session_bench.claude_desktop_otel_usage import (
    ClaudeDesktopOtelUsageCapture,
    ClaudeDesktopOtelUsageError,
    PrivateSessionIdAllowlist,
    join_claude_desktop_response_usage,
    read_otlp_http_body,
    read_private_session_id_file,
)


RUN = "claude-desktop-otel-test"
SESSION = "session-synthetic"
CANARIES = (
    "SB_SURVIVAL_V1_RESPONSE_R1_unique",
    "SB_SURVIVAL_V1_RESPONSE_R2_unique",
)


def test_http_body_reader_accepts_content_length_and_chunked_framing():
    body = b'{"resourceLogs":[]}'
    assert read_otlp_http_body(
        io.BytesIO(body), content_length=str(len(body)), transfer_encoding=None,
    ) == body

    chunks = (body[:5], body[5:])
    framed = b"".join(
        f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n"
        for chunk in chunks
    ) + b"0\r\n\r\n"
    assert read_otlp_http_body(
        io.BytesIO(framed), content_length=None, transfer_encoding="chunked",
    ) == body


def test_private_session_id_file_is_required_to_be_private_and_exact(tmp_path):
    path = tmp_path / "session-id"
    path.write_text(f"{SESSION}\n", encoding="utf-8")
    path.chmod(0o600)
    assert read_private_session_id_file(path.resolve()) == SESSION

    path.chmod(0o644)
    with pytest.raises(ClaudeDesktopOtelUsageError, match="private ordinary"):
        read_private_session_id_file(path.resolve())


@pytest.mark.parametrize("value", ["", "two\nlines\n", "space is invalid\n"])
def test_private_session_id_file_rejects_malformed_allowlist(tmp_path, value):
    path = tmp_path / "session-id"
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ClaudeDesktopOtelUsageError, match="malformed"):
        read_private_session_id_file(path.resolve())


def test_missing_session_file_keeps_allowlist_unarmed_and_capture_empty(tmp_path):
    allowlist = PrivateSessionIdAllowlist((tmp_path / "not-created").resolve())
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=None,
    )
    assert allowlist.refresh() is None
    assert capture.events == []
    with pytest.raises(ClaudeDesktopOtelUsageError, match="not armed"):
        capture.receipt()


def test_allowlist_arms_once_and_exact_session_events_join(tmp_path):
    path = (tmp_path / "session-id").resolve()
    allowlist = PrivateSessionIdAllowlist(path)
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=None,
    )
    assert allowlist.refresh() is None
    path.write_text(f"{SESSION}\n", encoding="utf-8")
    path.chmod(0o600)
    capture.arm_session_id(allowlist.refresh())
    capture.ingest(json.dumps(otlp_payload()).encode())
    result = join_claude_desktop_response_usage(
        capture.receipt(), run_id=RUN, session_id=SESSION,
        workload=workload(), gui_receipt=gui(),
    )
    assert set(result) == {"turn-r1", "turn-r2"}


def test_armed_allowlist_rejects_changed_or_missing_file(tmp_path):
    path = (tmp_path / "session-id").resolve()
    path.write_text(f"{SESSION}\n", encoding="utf-8")
    path.chmod(0o600)
    allowlist = PrivateSessionIdAllowlist(path)
    assert allowlist.refresh() == SESSION

    path.write_text("other-session\n", encoding="utf-8")
    with pytest.raises(ClaudeDesktopOtelUsageError, match="changed"):
        allowlist.refresh()

    path.write_text(f"{SESSION}\n", encoding="utf-8")
    path.unlink()
    with pytest.raises(ClaudeDesktopOtelUsageError, match="private ordinary"):
        allowlist.refresh()

@pytest.mark.parametrize("content_length,transfer_encoding,wire", [
    ("4", "chunked", b"0\r\n\r\n"),
    (None, "gzip", b""),
    (None, "chunked", b"800001\r\n"),
    (None, "chunked", b"4\r\nab"),
])
def test_http_body_reader_rejects_ambiguous_or_unbounded_framing(
    content_length, transfer_encoding, wire,
):
    with pytest.raises(ClaudeDesktopOtelUsageError):
        read_otlp_http_body(
            io.BytesIO(wire), content_length=content_length,
            transfer_encoding=transfer_encoding,
        )


def kv(key, value):
    if isinstance(value, bool):
        encoded = {"boolValue": value}
    elif isinstance(value, int):
        encoded = {"intValue": str(value)}
    else:
        encoded = {"stringValue": value}
    return {"key": key, "value": encoded}


def log_event(kind, *, turn, request_id, sequence, text=None,
              response_length=None, omit_usage=(), session_id=SESSION,
              prompt_id=None, model="claude-sonnet-5-5", effort="medium"):
    attrs = [
        kv("event.name", f"claude_code.{kind}"),
        kv("event.timestamp", f"2026-10-02T12:00:{sequence:02d}.000Z"),
        kv("event.sequence", sequence),
        kv("session.id", session_id),
        kv("prompt.id", prompt_id or f"prompt-{turn}"),
        kv("request_id", request_id),
        kv("model", model),
        kv("effort", effort),
        kv("query_source", "repl_main_thread"),
    ]
    if kind == "api_request":
        usage = {
            "input_tokens": 101 + turn,
            "output_tokens": 31 + turn,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 12 + turn,
        }
        attrs.extend(
            kv(key, value) for key, value in usage.items()
            if key not in omit_usage
        )
    else:
        assert isinstance(text, str)
        attrs.extend([
            kv("response", text),
            kv("response_length", len(text) if response_length is None else response_length),
            kv("message.uuid", f"message-{turn}"),
        ])
    return {"attributes": attrs}


def otlp_payload(*, include_unrelated=True, session_id=SESSION):
    records = []
    for turn, canary in enumerate(CANARIES, 1):
        request_id = f"req-{turn}"
        records.extend([
            log_event(
                "api_request", turn=turn, request_id=request_id,
                sequence=turn * 3, session_id=session_id,
            ),
            log_event(
                "assistant_response", turn=turn, request_id=request_id,
                sequence=turn * 3 + 1,
                text=f"Synthetic answer for turn {turn}. {canary}",
                session_id=session_id,
            ),
        ])
    if include_unrelated:
        records.extend([
            {
                "attributes": [
                    kv("event.name", "claude_code.user_prompt"),
                    kv("event.timestamp", "2026-10-02T12:00:00.000Z"),
                    kv("prompt", "PRIVATE SYNTHETIC PROMPT MUST NOT BE RETAINED"),
                ],
                "body": {"stringValue": "PRIVATE RAW BODY MUST NOT BE RETAINED"},
            },
            {
                "attributes": [
                    kv("event.name", "claude_code.api_response_body"),
                    kv("body", "PRIVATE RAW API BODY MUST NOT BE RETAINED"),
                ],
            },
        ])
    return {
        "resourceLogs": [{
            "resource": {"attributes": [
                kv("session.id", session_id),
                kv("user.email", "private@example.invalid"),
                kv("service.name", "Claude Code"),
            ]},
            "scopeLogs": [{"logRecords": records}],
        }],
    }


def make_capture(*, session_id=SESSION, omit_usage=()):
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES,
        expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=True, session_id=session_id)
    if omit_usage:
        for record in payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]:
            if any(item.get("value", {}).get("stringValue") == "claude_code.api_request"
                   for item in record.get("attributes", [])):
                for item in record["attributes"]:
                    if item.get("key") in omit_usage:
                        record["attributes"].remove(item)
    capture.ingest(json.dumps(payload).encode())
    return capture


def workload():
    return {
        "turns": [
            {"id": "turn-r1", "response_canary": CANARIES[0]},
            {"id": "turn-r2", "response_canary": CANARIES[1]},
        ],
    }


def gui():
    return {"observations": {
        "r1_canary_visible": CANARIES[0],
        "r2_canary_visible": CANARIES[1],
    }}


def test_receiver_keeps_only_allowlisted_hash_bound_events_and_discards_text():
    capture = make_capture()
    receipt = capture.receipt()
    encoded = json.dumps(receipt, sort_keys=True)

    assert len(receipt["events"]) == 4
    assert "PRIVATE SYNTHETIC PROMPT" not in encoded
    assert "PRIVATE RAW BODY" not in encoded
    assert "PRIVATE API BODY" not in encoded
    assert "private@example.invalid" not in encoded
    assert "Synthetic answer for turn 1" not in encoded
    response = next(row for row in receipt["events"] if row["kind"] == "assistant_response")
    assert response["matched_response_canaries"] == [CANARIES[0]]
    assert response["response_sha256"] == hashlib.sha256(
        f"Synthetic answer for turn 1. {CANARIES[0]}".encode()
    ).hexdigest()


def test_exact_prompt_request_and_canary_join_supplies_explicit_per_request_usage():
    capture = make_capture()
    result = join_claude_desktop_response_usage(
        capture.receipt(), run_id=RUN, session_id=SESSION,
        workload=workload(), gui_receipt=gui(),
    )

    assert set(result) == {"turn-r1", "turn-r2"}
    assert result["turn-r1"]["request_id"] == "req-1"
    assert result["turn-r1"]["prompt_id"] == "prompt-1"
    assert result["turn-r1"]["model"] == "claude-sonnet-5-5"
    assert result["turn-r1"]["usage"] == {
        "input_tokens": 102,
        "output_tokens": 32,
        "cache_read_tokens": 0,
        "cache_write_tokens": 13,
    }
    assert result["turn-r1"]["token_semantics"]["missing_is_zero"] is False


def test_foreign_session_content_is_discarded_before_record_and_target_still_joins(
    monkeypatch,
):
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    foreign = otlp_payload(include_unrelated=False, session_id="foreign-session")
    foreign_records = foreign["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    for record in foreign_records:
        record["attributes"].append(kv("prompt", "FOREIGN PRIVATE PROMPT"))
        record["attributes"].append(kv("response", "FOREIGN PRIVATE RESPONSE"))
    target = otlp_payload(include_unrelated=False)
    payload = {"resourceLogs": foreign["resourceLogs"] + target["resourceLogs"]}

    recorded_sessions = []
    original_record = capture._record

    def record_target_only(name, attrs, resource_attrs):
        recorded_sessions.append(attrs.get("session.id") or resource_attrs.get("session.id"))
        return original_record(name, attrs, resource_attrs)

    monkeypatch.setattr(capture, "_record", record_target_only)
    assert capture.ingest(json.dumps(payload).encode()) == 4
    assert recorded_sessions == [SESSION] * 4
    encoded = json.dumps(capture.receipt(), sort_keys=True)
    assert "foreign-session" not in encoded
    assert "FOREIGN PRIVATE PROMPT" not in encoded
    assert "FOREIGN PRIVATE RESPONSE" not in encoded

    joined = join_claude_desktop_response_usage(
        capture.receipt(), run_id=RUN, session_id=SESSION,
        workload=workload(), gui_receipt=gui(),
    )
    assert set(joined) == {"turn-r1", "turn-r2"}


def test_receipt_refuses_foreign_session_even_if_internal_events_are_tampered():
    capture = make_capture()
    capture.events[0]["session_id"] = "foreign-session"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="exact-session allowlist"):
        capture.receipt()


def multi_request_capture(*, extra_kwargs=None, omit_usage=()):
    payload = otlp_payload()
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    extra = log_event(
        "api_request", turn=1, request_id="req-1-tool", sequence=2,
        omit_usage=omit_usage, **(extra_kwargs or {}),
    )
    records.insert(0, extra)
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    capture.ingest(json.dumps(payload).encode())
    return capture


def join(capture):
    return join_claude_desktop_response_usage(
        capture.receipt(), run_id=RUN, session_id=SESSION,
        workload=workload(), gui_receipt=gui(),
    )


def test_tool_turn_aggregates_all_requests_in_prompt_with_distinct_ids():
    result = join(multi_request_capture())
    first = result["turn-r1"]
    assert first["request_id"] == "req-1"
    assert first["request_ids"] == ["req-1-tool", "req-1"]
    assert first["request_count"] == 2
    assert first["usage"] == {
        "input_tokens": 204,
        "output_tokens": 64,
        "cache_read_tokens": 0,
        "cache_write_tokens": 26,
    }
    assert result["turn-r2"]["request_ids"] == ["req-2"]


def test_incomplete_tool_request_rejects_whole_prompt_group():
    with pytest.raises(ClaudeDesktopOtelUsageError, match="missing or invalid usage"):
        join(multi_request_capture(omit_usage=("cache_read_tokens",)))


@pytest.mark.parametrize("extra_kwargs", [
    {"model": "claude-opus-other"},
    {"effort": "high"},
    {"effort": ""},
])
def test_mixed_or_missing_request_model_config_rejects_group(extra_kwargs):
    with pytest.raises(ClaudeDesktopOtelUsageError, match="model or effort|invalid effort"):
        join(multi_request_capture(extra_kwargs=extra_kwargs))


def test_duplicate_or_cross_prompt_request_id_rejects_group():
    receipt = multi_request_capture().receipt()
    extra = next(row for row in receipt["events"] if row["request_id"] == "req-1-tool")
    extra["request_id"] = "req-2"
    extra["prompt_id"] = "prompt-2"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="IDs are duplicated"):
        join_claude_desktop_response_usage(
            receipt, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )


def test_failed_tool_request_rejects_incomplete_coverage():
    capture = multi_request_capture()
    receipt = capture.receipt()
    extra = next(row for row in receipt["events"] if row["request_id"] == "req-1-tool")
    extra["success"] = False
    with pytest.raises(ClaudeDesktopOtelUsageError, match="failed API request"):
        join_claude_desktop_response_usage(
            receipt, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )


def test_malformed_success_value_is_rejected_without_persisting_content():
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    request = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    request["attributes"].append(kv("success", "PRIVATE RAW API BODY"))
    with pytest.raises(ClaudeDesktopOtelUsageError, match="success field is invalid"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.events == []
    assert "PRIVATE RAW API BODY" not in json.dumps(capture.receipt())


def test_multi_request_receipt_and_join_do_not_persist_private_content():
    capture = multi_request_capture()
    encoded = json.dumps({"receipt": capture.receipt(), "join": join(capture)})
    for private in (
        "PRIVATE SYNTHETIC PROMPT", "PRIVATE RAW BODY", "PRIVATE API BODY",
        "private@example.invalid", "Synthetic answer for turn",
    ):
        assert private not in encoded


@pytest.mark.parametrize("kind,reason_code", [
    ("api_error", "api_error_incomplete_coverage"),
    ("api_retries_exhausted", "api_retries_exhausted_incomplete_coverage"),
])
def test_terminal_api_failure_without_request_id_invalidates_capture(
    kind, reason_code, tmp_path,
):
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    # A failed call may have no server request ID, and its error message can
    # contain private response details. Neither is needed to reject coverage.
    records.append({"attributes": [
        kv("event.name", f"claude_code.{kind}"),
        kv("session.id", SESSION),
        kv("prompt.id", "prompt-1"),
        kv("error", "PRIVATE RAW API BODY"),
    ]})
    with pytest.raises(ClaudeDesktopOtelUsageError, match="usage coverage incomplete"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.events == []
    assert capture.invalid_reason_code == reason_code
    assert "PRIVATE RAW API BODY" not in json.dumps(capture.receipt())
    with pytest.raises(ClaudeDesktopOtelUsageError, match="rejected OTLP export"):
        capture.write_receipt(tmp_path / "must-not-exist.json")


def test_terminal_api_error_after_valid_batch_still_blocks_receipt(tmp_path):
    capture = multi_request_capture()
    payload = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"attributes": [
        kv("event.name", "claude_code.api_error"),
        kv("session.id", SESSION),
        kv("prompt.id", "prompt-1"),
        kv("error", "PRIVATE RAW API BODY"),
    ]}]}]}]}
    with pytest.raises(ClaudeDesktopOtelUsageError, match="usage coverage incomplete"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.invalid_reason_code == "api_error_incomplete_coverage"
    assert "PRIVATE RAW API BODY" not in json.dumps(capture.receipt())
    with pytest.raises(ClaudeDesktopOtelUsageError, match="rejected OTLP export"):
        capture.write_receipt(tmp_path / "must-not-exist.json")


def test_missing_cache_usage_stays_unknown_and_cannot_close_the_join():
    capture = make_capture(omit_usage=("cache_creation_tokens",))
    request = next(row for row in capture.receipt()["events"]
                   if row["kind"] == "api_request")
    assert request["usage"]["cache_write_tokens"] is None
    with pytest.raises(ClaudeDesktopOtelUsageError, match="missing or invalid usage"):
        join_claude_desktop_response_usage(
            capture.receipt(), run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )


def test_session_and_prompt_ids_must_match_exact_response_request_pair():
    capture = make_capture()
    receipt = capture.receipt()
    receipt["events"][0]["prompt_id"] = "other-prompt"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="request-scoped"):
        join_claude_desktop_response_usage(
            receipt, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )

    receipt = make_capture().receipt()
    receipt["events"][0]["session_id"] = "other-session"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="different Claude session"):
        join_claude_desktop_response_usage(
            receipt, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )


def test_duplicate_response_canary_is_ambiguous_and_fails_closed():
    capture = make_capture()
    receipt = capture.receipt()
    duplicate = next(row.copy() for row in receipt["events"]
                     if row["kind"] == "assistant_response")
    receipt["events"].append(duplicate)
    with pytest.raises(ClaudeDesktopOtelUsageError, match="one OTel assistant response"):
        join_claude_desktop_response_usage(
            receipt, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )


def test_response_length_mismatch_rejects_batch_without_partial_events():
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    response = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]
    for item in response["attributes"]:
        if item["key"] == "response":
            item["value"]["stringValue"] += " truncated"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="length is inconsistent"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.events == []
    assert capture.invalid_reason_code == "assistant_response_length_mismatch"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="rejected OTLP export"):
        capture.write_receipt(Path("/tmp/never-written-otel-receipt.json"))


def test_response_length_accepts_documented_utf16_code_unit_count_for_emoji():
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]
    text = f"Synthetic answer 🙂. {CANARIES[0]}"
    for item in record["attributes"]:
        if item["key"] == "response":
            item["value"]["stringValue"] = text
        elif item["key"] == "response_length":
            item["value"]["intValue"] = str(len(text.encode("utf-16-le")) // 2)

    capture.ingest(json.dumps(payload, ensure_ascii=False).encode())
    response = next(row for row in capture.events if row["kind"] == "assistant_response")
    assert response["response_length_semantics"] == "utf16_code_units"
    assert response["matched_response_canaries"] == [CANARIES[0]]


@pytest.mark.parametrize(
    "remove_response,remove_length,expected",
    [(True, False, "assistant_response_not_string"),
     (False, True, "assistant_response_length_missing")],
)
def test_invalid_assistant_response_has_safe_specific_reason_code(
    remove_response, remove_length, expected,
):
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]
    record["attributes"] = [
        item for item in record["attributes"]
        if not ((remove_response and item["key"] == "response")
                or (remove_length and item["key"] == "response_length"))
    ]
    with pytest.raises(ClaudeDesktopOtelUsageError):
        capture.ingest(json.dumps(payload).encode())
    assert capture.invalid_reason_code == expected


def test_response_marked_truncated_is_rejected_even_when_its_length_matches():
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][1]
    text = f"[TRUNCATED: partial] {CANARIES[0]}"
    for item in record["attributes"]:
        if item["key"] == "response":
            item["value"]["stringValue"] = text
        elif item["key"] == "response_length":
            item["value"]["intValue"] = str(len(text))
    with pytest.raises(ClaudeDesktopOtelUsageError, match="redacted or truncated"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.invalid_reason_code == "assistant_response_redacted_or_truncated"


def test_rejected_export_batch_is_atomic_and_latches_capture_invalid():
    capture = ClaudeDesktopOtelUsageCapture(
        run_id=RUN, response_canaries=CANARIES, expected_session_id=SESSION,
    )
    payload = otlp_payload(include_unrelated=False)
    records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    bad_response = records[1]
    bad_response["attributes"] = [
        item for item in bad_response["attributes"] if item["key"] != "session.id"
    ]
    payload["resourceLogs"] = [
        {"resource": {}, "scopeLogs": [{"logRecords": [records[0]]}]},
        {"resource": {}, "scopeLogs": [{"logRecords": [bad_response]}]},
    ]
    with pytest.raises(ClaudeDesktopOtelUsageError, match="session.id"):
        capture.ingest(json.dumps(payload).encode())
    assert capture.invalid_reason_code == "missing_session_id"
    assert capture.events == []
    with pytest.raises(ClaudeDesktopOtelUsageError, match="rejected OTLP export"):
        capture.ingest(json.dumps(otlp_payload(include_unrelated=False)).encode())


def test_http_rejected_export_after_valid_events_prevents_receipt(tmp_path):
    capture = make_capture()
    capture.reject_export()

    destination = tmp_path / "must-not-be-written.json"
    with pytest.raises(ClaudeDesktopOtelUsageError, match="rejected OTLP export"):
        capture.write_receipt(destination)
    assert not destination.exists()


@pytest.mark.parametrize("turn_ids", [(None, "turn-r2"), ("same", "same")])
def test_workload_turn_ids_must_be_present_and_unique(turn_ids):
    capture = make_capture()
    malformed = workload()
    malformed["turns"][0]["id"], malformed["turns"][1]["id"] = turn_ids
    with pytest.raises(ClaudeDesktopOtelUsageError, match="distinct nonempty"):
        join_claude_desktop_response_usage(
            capture.receipt(), run_id=RUN, session_id=SESSION,
            workload=malformed, gui_receipt=gui(),
        )


def test_finalizer_attaches_only_canary_joined_request_usage_to_observer():
    from scripts.finalize_claude_desktop_runs import _attach_otel_usage

    capture = make_capture()
    joined = join_claude_desktop_response_usage(
        capture.receipt(), run_id=RUN, session_id=SESSION,
        workload=workload(), gui_receipt=gui(),
    )
    observer = {"events": [
        {"kind": "assistant_response", "population_role": "primary_scored",
         "fields": {"turn_id": turn_id, "canary": canary, "model_id": "display-model"}}
        for turn_id, canary in zip(("turn-r1", "turn-r2"), CANARIES, strict=True)
    ]}
    binding = {"path": "otel-usage-receipt-private.json", "sha256": "a" * 64}

    observed = _attach_otel_usage(observer, joined, receipt_binding=binding)
    first = observed["events"][0]["fields"]
    assert first["usage_id"] == "req-1"
    assert first["usage"] == joined["turn-r1"]["usage"]
    assert first["usage_source"] == "loopback_collected_claude_code_otel_api_request"
    assert first["model_id"] == "display-model"
    assert "Synthetic answer" not in json.dumps(observed)
    assert observed["capture_time_usage_receipt"] == binding


def test_finalizer_rejects_nonprivate_otel_receipt(tmp_path):
    from scripts.finalize_claude_desktop_runs import FinalizeError, _validate_otel_usage_receipt

    path = tmp_path / "otel.json"
    path.write_text(json.dumps(make_capture().receipt()))
    path.chmod(0o644)
    with pytest.raises(FinalizeError, match="private ordinary"):
        _validate_otel_usage_receipt(
            path, run_id=RUN, session_id=SESSION,
            workload=workload(), gui_receipt=gui(),
        )
