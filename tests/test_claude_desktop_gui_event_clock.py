import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from session_bench.claude_desktop_gui_event_clock import (
    EVENT_PROVENANCE,
    EVENT_KINDS,
    EVENT_ORDER,
    PROVENANCE,
    SCHEMA,
    load_gui_event_clock,
    record_gui_event,
    record_user_prompt_submit_event,
)


START = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


def _record(path, event_id, index, *, run_id="run-1", event_kind=None):
    return record_gui_event(
        run_id=run_id, event_id=event_id,
        event_kind=EVENT_KINDS[event_id] if event_kind is None else event_kind,
        ledger_path=path, clock=lambda: START + timedelta(seconds=index),
    )


def _complete(path):
    return [_record(path, event_id, index) for index, event_id in enumerate(EVENT_ORDER)]


def test_records_four_private_boundary_times_and_loads_exact_run(tmp_path):
    path = tmp_path / "clock.jsonl"
    rows = _complete(path)
    raw, loaded = load_gui_event_clock(path, run_id="run-1")
    assert loaded == rows
    assert len(raw.splitlines()) == 4
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert [row["gui_observed_at"] for row in rows] == [
        f"2026-10-02T12:00:0{index}.000000Z" for index in range(4)
    ]
    assert all(row["schema"] == SCHEMA and
               row["timestamp_provenance"] == EVENT_PROVENANCE[row["event_id"]]
               for row in rows)
    assert all(set(row) == {"schema", "run_id", "event_id", "event_kind",
                            "gui_observed_at", "timestamp_provenance"} for row in rows)


def test_rejects_wrong_mapping_duplicate_order_and_clock_regression(tmp_path):
    path = tmp_path / "clock.jsonl"
    with pytest.raises(ValueError):
        _record(path, "turn-r1", 0, event_kind="assistant_response")
    assert not path.exists()
    _record(path, "turn-r1", 0)
    baseline = path.read_bytes()
    for event_id, index in (("turn-r1", 1), ("turn-r2", 1), ("response-r1", -1)):
        with pytest.raises(ValueError):
            _record(path, event_id, index)
        assert path.read_bytes() == baseline
    with pytest.raises(ValueError):
        _record(path, "response-r1", 1, run_id="other-run")
    assert path.read_bytes() == baseline
    with pytest.raises(ValueError, match="incomplete"):
        load_gui_event_clock(path, run_id="run-1")
    assert len(load_gui_event_clock(path, run_id="run-1", require_complete=False)[1]) == 1


@pytest.mark.parametrize("damage", [
    lambda rows: rows[0].update(event_kind="assistant_response"),
    lambda rows: rows[1].update(event_id="turn-r1"),
    lambda rows: rows[2].update(run_id="other"),
    lambda rows: rows[2].update(timestamp_provenance="native_transcript"),
    lambda rows: rows[2].update(gui_observed_at="2026-10-02T12:00:00.000000Z"),
    lambda rows: rows[2].update(gui_observed_at="2026-02-30T12:00:02.000000Z"),
    lambda rows: rows[2].update(secret="unexpected"),
])
def test_loader_rejects_damaged_rows(tmp_path, damage):
    path = tmp_path / "clock.jsonl"
    rows = _complete(path)
    damage(rows)
    path.write_bytes(b"".join((json.dumps(row) + "\n").encode() for row in rows))
    with pytest.raises(ValueError):
        load_gui_event_clock(path, run_id="run-1")


def test_loader_rejects_duplicate_json_keys_and_truncated_line(tmp_path):
    path = tmp_path / "clock.jsonl"
    _complete(path)
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"run_id":"run-1"', b'"run_id":"run-1","run_id":"run-1"', 1))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_gui_event_clock(path, run_id="run-1")
    path.write_bytes(original[:-1])
    with pytest.raises(ValueError, match="incomplete"):
        load_gui_event_clock(path, run_id="run-1")


def test_refuses_public_or_symlink_ledger(tmp_path):
    path = tmp_path / "clock.jsonl"
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        _record(path, "turn-r1", 0)
    assert path.read_bytes() == b""
    path.unlink()
    outside = tmp_path / "outside.jsonl"
    outside.write_text("")
    path.symlink_to(outside)
    with pytest.raises(OSError):
        _record(path, "turn-r1", 0)
    assert outside.read_bytes() == b""


def test_requires_aware_clock_and_absolute_path(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        record_gui_event(run_id="run-1", event_id="turn-r1", event_kind="user_turn",
                         ledger_path=tmp_path / "clock.jsonl",
                         clock=lambda: datetime(2026, 10, 2))
    with pytest.raises(ValueError, match="absolute"):
        record_gui_event(run_id="run-1", event_id="turn-r1", event_kind="user_turn",
                         ledger_path="relative.jsonl", clock=lambda: START)


def test_standalone_cli_records_one_boundary(tmp_path):
    path = tmp_path / "clock.jsonl"
    _record(path, "turn-r1", 0)
    script = Path(__file__).resolve().parents[1] / "scripts/record_claude_desktop_gui_event.py"
    result = subprocess.run(
        [sys.executable, str(script), "--run-id", "run-1", "--event-id", "response-r1",
         "--event-kind", "assistant_response", "--ledger", str(path)],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout == ""
    rows = load_gui_event_clock(path, run_id="run-1", require_complete=False)[1]
    assert len(rows) == 2 and rows[-1]["timestamp_provenance"] == PROVENANCE


def test_user_prompt_submit_hook_stamps_exact_turn_without_retaining_prompt(tmp_path):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    workload = {
        "run_id": "run-1",
        "turns": [
            {"id": "turn-r1", "text": "synthetic first prompt"},
            {"id": "turn-r2", "text": "synthetic correction"},
        ],
    }
    event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "session-1",
        "cwd": str(workspace),
        "prompt": "synthetic first prompt",
    }
    ledger = tmp_path / "clock.jsonl"
    session_id_file = tmp_path / "otel-session-id.txt"
    row = record_user_prompt_submit_event(
        json.dumps(event).encode(), run_id="run-1", workload=workload,
        workspace=workspace, ledger_path=ledger, session_id_file=session_id_file,
        clock=lambda: START,
    )
    assert row["event_id"] == "turn-r1"
    assert row["gui_observed_at"] == "2026-10-02T12:00:00.000000Z"
    assert row["timestamp_provenance"] == EVENT_PROVENANCE["turn-r1"]
    raw = ledger.read_text()
    assert "synthetic first prompt" not in raw
    assert session_id_file.read_text() == "session-1\n"
    assert os.stat(session_id_file).st_mode & 0o777 == 0o600


def test_user_prompt_submit_hook_records_wrapped_r1_then_plain_r2(tmp_path):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    workload = {
        "run_id": "run-1",
        "turns": [
            {"id": "turn-r1", "text": "synthetic first prompt"},
            {"id": "turn-r2", "text": "synthetic correction"},
        ],
    }
    ledger = tmp_path / "clock.jsonl"
    session_id_file = tmp_path / "otel-session-id.txt"
    wrapped = {
        "hook_event_name": "UserPromptSubmit", "session_id": "session-1",
        "cwd": str(workspace),
        "prompt": '\n\n<pasted_context id="9687">\nsynthetic first prompt\n</pasted_context>\n',
    }
    plain = {
        "hook_event_name": "UserPromptSubmit", "session_id": "session-1",
        "cwd": str(workspace), "prompt": "synthetic correction",
    }
    first = record_user_prompt_submit_event(
        json.dumps(wrapped).encode(), run_id="run-1", workload=workload,
        workspace=workspace, ledger_path=ledger, session_id_file=session_id_file,
        clock=lambda: START,
    )
    response = record_gui_event(
        run_id="run-1", event_id="response-r1", event_kind="assistant_response",
        ledger_path=ledger, clock=lambda: START + timedelta(seconds=1),
    )
    second = record_user_prompt_submit_event(
        json.dumps(plain).encode(), run_id="run-1", workload=workload,
        workspace=workspace, ledger_path=ledger, session_id_file=session_id_file,
        clock=lambda: START + timedelta(seconds=2),
    )
    assert [first["event_id"], second["event_id"]] == ["turn-r1", "turn-r2"]
    rows = load_gui_event_clock(ledger, run_id="run-1", require_complete=False)[1]
    assert response["event_id"] == "response-r1"
    assert [row["event_id"] for row in rows] == [
        "turn-r1", "response-r1", "turn-r2",
    ]
    assert "pasted_context" not in ledger.read_text()
    assert session_id_file.read_text() == "session-1\n"


def test_user_prompt_submit_hook_rejects_session_switch_before_clock_or_allowlist_changes(tmp_path):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    workload = {
        "run_id": "run-1",
        "turns": [
            {"id": "turn-r1", "text": "synthetic first prompt"},
            {"id": "turn-r2", "text": "synthetic correction"},
        ],
    }
    ledger = tmp_path / "clock.jsonl"
    session_id_file = tmp_path / "otel-session-id.txt"
    first = {"hook_event_name": "UserPromptSubmit", "session_id": "session-1",
             "cwd": str(workspace), "prompt": "synthetic first prompt"}
    second = {"hook_event_name": "UserPromptSubmit", "session_id": "session-2",
              "cwd": str(workspace), "prompt": "synthetic correction"}
    record_user_prompt_submit_event(
        json.dumps(first).encode(), run_id="run-1", workload=workload,
        workspace=workspace, ledger_path=ledger, session_id_file=session_id_file,
        clock=lambda: START,
    )
    with pytest.raises(ValueError, match="already bound to another session"):
        record_user_prompt_submit_event(
            json.dumps(second).encode(), run_id="run-1", workload=workload,
            workspace=workspace, ledger_path=ledger, session_id_file=session_id_file,
            clock=lambda: START + timedelta(seconds=1),
        )
    assert len(load_gui_event_clock(ledger, run_id="run-1", require_complete=False)[1]) == 1
    assert session_id_file.read_text() == "session-1\n"


@pytest.mark.parametrize("prompt", [
    '<pasted_context id="9687">\nsynthetic first prompt\n</pasted_context>\n',
    '\n<pasted_context id="abc">\nsynthetic first prompt\n</pasted_context>\n',
    '\n<pasted_context id="9687">synthetic first prompt</pasted_context>\n',
    ('\n<pasted_context id="9687">\n'
     '<pasted_context id="1">\nsynthetic first prompt\n</pasted_context>\n'
     '</pasted_context>\n'),
    ('\n<pasted_context id="9687">\nsynthetic first prompt\n</pasted_context>\n'
     '\n<pasted_context id="1">\nsynthetic correction\n</pasted_context>\n'),
])
def test_user_prompt_submit_hook_rejects_malformed_nested_or_multiple_wrappers(
    tmp_path, prompt,
):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "session-1",
             "cwd": str(workspace), "prompt": prompt}
    ledger = tmp_path / "clock.jsonl"
    with pytest.raises(ValueError, match="pasted-context wrapper"):
        record_user_prompt_submit_event(
            json.dumps(event).encode(), run_id="run-1",
            workload={"run_id": "run-1", "turns": [
                {"id": "turn-r1", "text": "synthetic first prompt"},
                {"id": "turn-r2", "text": "synthetic correction"},
            ]},
            workspace=workspace, ledger_path=ledger, clock=lambda: START,
        )
    assert not ledger.exists()


def test_user_prompt_submit_hook_rejects_unmatched_prompt(tmp_path):
    workspace = tmp_path / "fixture_project"
    workspace.mkdir()
    event = {"hook_event_name": "UserPromptSubmit", "session_id": "session-1",
             "cwd": str(workspace), "prompt": "unexpected"}
    with pytest.raises(ValueError, match="does not uniquely match"):
        record_user_prompt_submit_event(
            json.dumps(event).encode(), run_id="run-1",
            workload={"run_id": "run-1", "turns": [
                {"id": "turn-r1", "text": "expected"},
                {"id": "turn-r2", "text": "correction"},
            ]},
            workspace=workspace, ledger_path=tmp_path / "clock.jsonl",
            session_id_file=tmp_path / "otel-session-id.txt",
            clock=lambda: START,
        )
    assert not (tmp_path / "clock.jsonl").exists()
    assert not (tmp_path / "otel-session-id.txt").exists()
