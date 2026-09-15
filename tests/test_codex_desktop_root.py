import json
import sqlite3
from pathlib import Path

import pytest

from session_bench.codex_desktop_root import (
    CodexDesktopRootError,
    export_codex_desktop_session_family,
)


THREAD_ID = "thread-synthetic-1"


def _inputs(tmp_path: Path, *, extra_state: str = "", duplicate_index: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(
        json.dumps({"type": "session_meta", "thread_id": THREAD_ID}) + "\n"
        + json.dumps({"type": "response", "thread_id": THREAD_ID, "text": "synthetic"}) + "\n",
        encoding="utf-8",
    )
    shell = tmp_path / "shell.sh"
    shell.write_text("#!/bin/sh\nprintf synthetic\n", encoding="utf-8")

    state = tmp_path / "state_5.sqlite"
    connection = sqlite3.connect(state)
    connection.executescript(
        """
        CREATE TABLE threads (
          thread_id TEXT,
          title TEXT NOT NULL,
          cwd TEXT
        );
        CREATE TABLE thread_artifacts (
          artifact_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          path TEXT
        );
        CREATE TABLE dynamic_tools (
          tool_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          name TEXT
        );
        CREATE TABLE spawn_edges (
          edge_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          child_id TEXT
        );
        CREATE TABLE realtime (
          event_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          event TEXT
        );
        INSERT INTO threads VALUES ('thread-synthetic-1', 'Synthetic', '/synthetic/project');
        INSERT INTO threads VALUES ('thread-other', 'Other', '/synthetic/other');
        INSERT INTO thread_artifacts VALUES ('artifact-1', 'thread-synthetic-1', 'rollout.jsonl');
        INSERT INTO dynamic_tools VALUES ('tool-1', 'thread-synthetic-1', 'shell');
        INSERT INTO spawn_edges VALUES ('edge-1', 'thread-synthetic-1', 'child-synthetic');
        INSERT INTO realtime VALUES ('event-1', 'thread-synthetic-1', 'turn-complete');
        """
        + extra_state
    )
    connection.commit()
    connection.close()

    history = tmp_path / "thread_history_1.sqlite"
    connection = sqlite3.connect(history)
    connection.executescript(
        """
        CREATE TABLE thread_turns (
          turn_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL
        );
        CREATE TABLE thread_items (
          item_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          turn_id TEXT NOT NULL,
          body TEXT
        );
        CREATE TABLE realtime (
          event_id TEXT PRIMARY KEY,
          thread_id TEXT NOT NULL,
          event TEXT
        );
        INSERT INTO thread_turns VALUES ('turn-1', 'thread-synthetic-1', 1);
        INSERT INTO thread_items VALUES ('item-1', 'thread-synthetic-1', 'turn-1', 'hello');
        INSERT INTO realtime VALUES ('event-2', 'thread-synthetic-1', 'visible');
        """
    )
    connection.commit()
    connection.close()

    index = tmp_path / "session_index.jsonl"
    records = [
        {"id": THREAD_ID, "title": "Synthetic", "cwd": "/synthetic/project"},
        {"id": "thread-other", "title": "Other", "cwd": "/synthetic/other"},
    ]
    if duplicate_index:
        records.insert(1, {"thread_id": THREAD_ID, "title": "Duplicate"})
    index.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    return rollout, shell, state, history, index


def _export(tmp_path: Path, **kwargs):
    paths = _inputs(tmp_path, **kwargs)
    return export_codex_desktop_session_family(*paths, THREAD_ID)


def test_exports_one_closed_per_thread_document_and_hashes_inputs(tmp_path: Path):
    rollout, shell, state, history, index = _inputs(tmp_path)
    result = export_codex_desktop_session_family(rollout, shell, state, history, index, THREAD_ID)

    assert result["schema_version"] == "session-bench.codex-desktop-family-v1"
    assert result["thread_id"] == THREAD_ID
    assert result["classification"]["raw_sources"] == "private_shared_stores"
    assert result["classification"]["byte_for_byte_native_database_copy"] is False
    assert result["thread"]["thread_id"] == THREAD_ID
    assert len(result["thread_turns"]) == 1
    assert len(result["thread_items"]) == 1
    assert len(result["thread_artifacts"]) == 1
    assert len(result["realtime"]) == 2
    assert result["inputs"]["rollout"]["sha256"] == __import__("hashlib").sha256(rollout.read_bytes()).hexdigest()
    assert result["inputs"]["shell_snapshot"]["sha256"] == __import__("hashlib").sha256(shell.read_bytes()).hexdigest()
    assert result["sources"]["state_5.sqlite"]["table_names"] == [
        "dynamic_tools", "realtime", "spawn_edges", "thread_artifacts", "threads"
    ]
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert json.loads(encoded)["thread_id"] == THREAD_ID
    assert str(tmp_path) not in encoded
    assert "thread-other" not in encoded


def test_writes_a_closed_export_once(tmp_path: Path):
    paths = _inputs(tmp_path)
    output = tmp_path / "export.json"
    result = export_codex_desktop_session_family(*paths, THREAD_ID, output_path=output)
    assert json.loads(output.read_text(encoding="utf-8"))["thread_id"] == THREAD_ID
    with pytest.raises(CodexDesktopRootError, match="already exists"):
        export_codex_desktop_session_family(*paths, THREAD_ID, output_path=output)
    assert result["thread_id"] == THREAD_ID


def test_rejects_zero_or_multiple_index_identities(tmp_path: Path):
    paths = _inputs(tmp_path)
    paths[-1].write_text(json.dumps({"id": "other"}) + "\n", encoding="utf-8")
    with pytest.raises(CodexDesktopRootError, match="exactly one matching"):
        export_codex_desktop_session_family(*paths, THREAD_ID)

    paths = _inputs(tmp_path / "duplicate", duplicate_index=True)
    with pytest.raises(CodexDesktopRootError, match="exactly one matching"):
        export_codex_desktop_session_family(*paths, THREAD_ID)


def test_rejects_duplicate_thread_rows_and_foreign_thread_data(tmp_path: Path):
    paths = _inputs(tmp_path, extra_state="INSERT INTO threads VALUES ('thread-synthetic-1', 'duplicate', '/x');")
    with pytest.raises(CodexDesktopRootError, match="exactly one thread identity"):
        export_codex_desktop_session_family(*paths, THREAD_ID)


def test_rejects_unexpected_schema_and_missing_required_tables(tmp_path: Path):
    paths = _inputs(tmp_path, extra_state="CREATE TABLE unrelated (id TEXT);")
    with pytest.raises(CodexDesktopRootError, match="unexpected tables"):
        export_codex_desktop_session_family(*paths, THREAD_ID)

    paths = _inputs(tmp_path / "missing")
    connection = sqlite3.connect(paths[3])
    connection.execute("DROP TABLE thread_items")
    connection.commit()
    connection.close()
    with pytest.raises(CodexDesktopRootError, match="missing required tables"):
        export_codex_desktop_session_family(*paths, THREAD_ID)


@pytest.mark.parametrize("position", [0, 1, 2, 3, 4])
def test_rejects_symlink_input(tmp_path: Path, position: int):
    paths = list(_inputs(tmp_path))
    source = paths[position]
    linked = source.with_name(source.name + ".link")
    source.rename(linked)
    source.symlink_to(linked)
    with pytest.raises(CodexDesktopRootError, match="symlink"):
        export_codex_desktop_session_family(*paths, THREAD_ID)


def test_rejects_malformed_rollout_or_index_json(tmp_path: Path):
    paths = list(_inputs(tmp_path))
    paths[0].write_text('{"ok": 1}\nnot-json\n', encoding="utf-8")
    with pytest.raises(CodexDesktopRootError, match="malformed JSON"):
        export_codex_desktop_session_family(*paths, THREAD_ID)

    paths = list(_inputs(tmp_path / "bad-index"))
    paths[-1].write_text("not-json\n", encoding="utf-8")
    with pytest.raises(CodexDesktopRootError, match="malformed JSON"):
        export_codex_desktop_session_family(*paths, THREAD_ID)


def test_rejects_nested_foreign_thread_id_in_selected_row(tmp_path: Path):
    paths = _inputs(tmp_path)
    connection = sqlite3.connect(paths[2])
    connection.execute("ALTER TABLE thread_artifacts ADD COLUMN source_thread_id TEXT")
    connection.execute("UPDATE thread_artifacts SET source_thread_id = ?", ("thread-other",))
    connection.commit()
    connection.close()
    with pytest.raises(CodexDesktopRootError, match="another thread ID"):
        export_codex_desktop_session_family(*paths, THREAD_ID)
