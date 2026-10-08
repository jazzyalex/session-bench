import json
import os
from pathlib import Path

import pytest

from scripts import capture_claude_desktop_source_locations as capture


RUN_MARKER = "SESSION_BENCH_RUN_MARKER_0123456789"


@pytest.fixture
def roots(tmp_path):
    base = tmp_path.resolve()
    transcript_root = base / "claude-projects"
    desktop_root = base / "claude-desktop-sessions"
    workspace = base / "workspace"
    snapshots = base / "private-snapshots"
    receipts = base / "receipts"
    for path in (transcript_root, desktop_root, workspace, snapshots, receipts):
        path.mkdir()
    unrelated_transcript = transcript_root / "private-old.jsonl"
    unrelated_desktop = desktop_root / "private-old.json"
    unrelated_transcript.write_bytes(b"PRIVATE_TRANSCRIPT_CONTENT_MUST_NOT_BE_READ")
    unrelated_desktop.write_bytes(b"PRIVATE_DESKTOP_CONTENT_MUST_NOT_BE_READ")
    return {
        "transcript_root": transcript_root,
        "desktop_root": desktop_root,
        "workspace": workspace,
        "temp_parent": snapshots,
        "receipt_path": receipts / "source-location-private.json",
        "unrelated": (unrelated_transcript, unrelated_desktop),
    }


def successful_action(paths, *, extra_change=False, wrong_workspace=False):
    def run():
        workspace = paths["workspace"]
        transcript = paths["transcript_root"] / "fixture-run" / "cli-session-1.jsonl"
        transcript.parent.mkdir()
        transcript.write_text(json.dumps({
            "type": "user",
            "sessionId": "cli-session-1",
            "cwd": str(workspace if not wrong_workspace else workspace.parent),
            "message": {"content": f"execute {RUN_MARKER}"},
        }) + "\n", encoding="utf-8")
        desktop = paths["desktop_root"] / "local_desktop_1.json"
        desktop.write_text(json.dumps({
            "sessionId": "local_desktop_1",
            "cliSessionId": "cli-session-1",
            "bridgeSessionIds": ["bridge-1"],
            "cwd": str(workspace),
        }), encoding="utf-8")
        if extra_change:
            paths["unrelated"][0].write_bytes(b"CHANGED WITHOUT OPENING OLD CONTENT")
    return run


def invoke(paths, action, *, receipt=True):
    return capture.capture_claude_desktop_source_locations(
        transcript_root=paths["transcript_root"],
        desktop_root=paths["desktop_root"],
        workspace=paths["workspace"],
        run_id="claude-desktop-future-1",
        repetition=1,
        run_marker=RUN_MARKER,
        expected_desktop_session_id="local_desktop_1",
        run_action=action,
        receipt_path=paths["receipt_path"] if receipt else None,
        temp_parent=paths["temp_parent"],
        expected_roots={"transcript": paths["transcript_root"],
                        "desktop_metadata": paths["desktop_root"]},
    )


def test_capture_rejects_noncanonical_roots_before_action(roots):
    invoked = []
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="canonical Claude roots"):
        capture.capture_claude_desktop_source_locations(
            transcript_root=roots["transcript_root"], desktop_root=roots["desktop_root"],
            workspace=roots["workspace"], run_id="run-1", repetition=1,
            expected_desktop_session_id="local_desktop_1",
            run_marker=RUN_MARKER, run_action=lambda: invoked.append(True),
        )
    assert invoked == []


def test_cli_derives_canonical_roots_and_rejects_caller_override(roots, monkeypatch):
    canonical = {"transcript": roots["transcript_root"],
                 "desktop_metadata": roots["desktop_root"]}
    monkeypatch.setattr(capture, "canonical_claude_desktop_roots", lambda: canonical)
    captured = []

    def fake_capture(**kwargs):
        captured.append(kwargs)
        return {"selected_absolute_paths": []}

    monkeypatch.setattr(capture, "capture_claude_desktop_source_locations", fake_capture)
    arguments = ["--workspace", str(roots["workspace"]), "--run-id", "run-1",
                 "--expected-desktop-session-id", "local_desktop_1",
                 "--repetition", "1", "--run-marker", RUN_MARKER,
                 "--receipt", str(roots["receipt_path"]), "--", "true"]
    assert capture.main(arguments) == 0
    assert captured[0]["transcript_root"] == canonical["transcript"]
    assert captured[0]["desktop_root"] == canonical["desktop_metadata"]
    assert captured[0]["expected_desktop_session_id"] == "local_desktop_1"
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="canonical Claude root"):
        capture.main(["--transcript-root", str(roots["workspace"]), *arguments])
    assert len(captured) == 1


def test_acquires_pair_without_opening_unrelated_contents_and_removes_snapshot(roots, monkeypatch):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)
    result = invoke(roots, successful_action(roots))

    selected = result["selected_absolute_paths"]
    assert len(selected) == 2
    assert all(Path(path).is_absolute() for path in selected)
    assert set(map(str, roots["unrelated"])).isdisjoint(opened)
    assert set(selected).issubset(set(opened))
    assert list(roots["temp_parent"].iterdir()) == []
    assert roots["receipt_path"].stat().st_mode & 0o077 == 0
    saved = json.loads(roots["receipt_path"].read_text())
    assert saved == result["private_receipt"]
    encoded = json.dumps(result, sort_keys=True)
    assert "private-old.jsonl" not in encoded
    assert "private-old.json" not in encoded
    assert result["private_receipt"]["cli_session_id"] == "cli-session-1"
    assert result["private_receipt"]["desktop_session_id"] == "local_desktop_1"


def test_acquires_unique_pair_when_unrelated_preexisting_entry_disappears(roots, monkeypatch):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)

    def action():
        roots["unrelated"][0].unlink()
        successful_action(roots)()

    result = invoke(roots, action)
    assert len(result["selected_absolute_paths"]) == 2
    assert set(result["selected_absolute_paths"]).issubset(opened)
    assert set(map(str, roots["unrelated"])).isdisjoint(opened)
    assert "private-old" not in json.dumps(result)
    assert list(roots["temp_parent"].iterdir()) == []


def test_pairs_unique_session_filename_with_extra_changed_transcript_and_other(roots, monkeypatch):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)

    def action():
        successful_action(roots)()
        unrelated = roots["transcript_root"] / "another-project" / "private-new.jsonl"
        unrelated.parent.mkdir()
        unrelated.write_text("PRIVATE_NEW_TRANSCRIPT_MUST_NOT_BE_READ")
        (roots["desktop_root"] / "private-extra.txt").write_text("PRIVATE_OTHER_CONTENT")

    result = invoke(roots, action)
    selected = result["selected_absolute_paths"]
    assert len(selected) == 2
    assert str(roots["transcript_root"] / "another-project" / "private-new.jsonl") not in opened
    assert str(roots["desktop_root"] / "private-extra.txt") not in opened
    assert set(selected).issubset(opened)
    encoded = json.dumps(result)
    assert "private-new" not in encoded
    assert "private-extra" not in encoded


def test_selects_expected_ui_session_despite_three_changed_metadata_files(roots, monkeypatch):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)
    unrelated_new = roots["desktop_root"] / "local_other.json"
    unrelated_other = roots["desktop_root"] / "other.txt"

    def action():
        successful_action(roots)()
        roots["unrelated"][1].write_bytes(b"CHANGED PRIVATE HISTORY MUST NOT BE READ")
        unrelated_new.write_bytes(b"NEW PRIVATE HISTORY MUST NOT BE READ")
        unrelated_other.write_bytes(b"OTHER PRIVATE CONTENT MUST NOT BE READ")

    result = invoke(roots, action)
    source_opens = {path for path in opened
                    if path.startswith(str(roots["transcript_root"]))
                    or path.startswith(str(roots["desktop_root"]))}
    assert source_opens == set(result["selected_absolute_paths"])
    assert result["private_receipt"]["desktop_session_id"] == "local_desktop_1"
    summary = next(item for item in result["private_receipt"]["root_summaries"]
                   if item["root_id"] == "claude-desktop-sessions")
    assert (summary["before_entry_count"], summary["after_entry_count"],
            summary["unrelated_entry_count"]) == (1, 4, 3)
    encoded = json.dumps(result)
    assert all(str(path) not in encoded
               for path in (*roots["unrelated"], unrelated_new, unrelated_other))
    assert list(roots["temp_parent"].iterdir()) == []


@pytest.mark.parametrize("change", ["absent", "ambiguous"])
def test_expected_ui_metadata_must_be_unique_before_any_source_read(roots, monkeypatch, change):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)

    def action():
        successful_action(roots)()
        selected = roots["desktop_root"] / "local_desktop_1.json"
        if change == "absent":
            selected.rename(roots["desktop_root"] / "local_other.json")
        else:
            duplicate = roots["desktop_root"] / "duplicate" / selected.name
            duplicate.parent.mkdir()
            duplicate.write_bytes(b"AMBIGUOUS CONTENT MUST NOT BE READ")

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="expected UI session"):
        invoke(roots, action)
    assert not any(path.startswith(str(roots["transcript_root"]))
                   or path.startswith(str(roots["desktop_root"])) for path in opened)
    assert not roots["receipt_path"].exists()
    assert list(roots["temp_parent"].iterdir()) == []


@pytest.mark.parametrize("session_id", ["", "local_", "other", "local_../escape", "local_foo\\bar"])
def test_rejects_invalid_expected_ui_identity_before_action(roots, session_id):
    invoked = []
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="expected Desktop session identity"):
        capture.capture_claude_desktop_source_locations(
            transcript_root=roots["transcript_root"], desktop_root=roots["desktop_root"],
            workspace=roots["workspace"], run_id="run-1", repetition=1,
            run_marker=RUN_MARKER, expected_desktop_session_id=session_id,
            run_action=lambda: invoked.append(True),
        )
    assert invoked == []
    assert list(roots["temp_parent"].iterdir()) == []


def test_selected_metadata_contents_must_match_expected_ui_identity(roots):
    def action():
        successful_action(roots)()
        selected = roots["desktop_root"] / "local_desktop_1.json"
        value = json.loads(selected.read_text())
        value["sessionId"] = "local_other"
        selected.write_text(json.dumps(value))

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="Desktop session identity"):
        invoke(roots, action)
    assert not roots["receipt_path"].exists()
    assert list(roots["temp_parent"].iterdir()) == []


def invoke_with_id_file(roots, action, identity_file):
    return capture.capture_claude_desktop_source_locations(
        transcript_root=roots["transcript_root"], desktop_root=roots["desktop_root"],
        workspace=roots["workspace"], run_id="claude-desktop-future-1", repetition=1,
        run_marker=RUN_MARKER, run_action=action,
        expected_desktop_session_id_file=identity_file,
        receipt_path=roots["receipt_path"], temp_parent=roots["temp_parent"],
        expected_roots={"transcript": roots["transcript_root"],
                        "desktop_metadata": roots["desktop_root"]},
    )


def test_reads_ui_identity_file_only_after_run_action_completes(roots, monkeypatch):
    identity_file = roots["workspace"].parent / "ui-session-id.txt"
    original_read = capture._read_expected_desktop_session_id_file
    order = []

    def read_identity(path):
        assert order == ["action complete"]
        order.append("identity read")
        return original_read(path)

    monkeypatch.setattr(capture, "_read_expected_desktop_session_id_file", read_identity)

    def action():
        assert not identity_file.exists()
        successful_action(roots)()
        identity_file.write_text("local_desktop_1\n")
        identity_file.chmod(0o600)
        order.append("action complete")

    result = invoke_with_id_file(roots, action, identity_file)
    assert order == ["action complete", "identity read"]
    assert result["private_receipt"]["desktop_session_id"] == "local_desktop_1"
    assert str(identity_file) not in json.dumps(result)
    assert list(roots["temp_parent"].iterdir()) == []


@pytest.mark.parametrize("invalid", ["missing", "public", "symlink", "oversized", "malformed"])
def test_rejects_invalid_late_ui_identity_file_before_candidate_reads(roots, monkeypatch, invalid):
    identity_file = roots["workspace"].parent / "ui-session-id.txt"
    original_read = capture._read_selected
    reads = []

    def tracked_read(entry):
        reads.append(entry["source_path"])
        return original_read(entry)

    monkeypatch.setattr(capture, "_read_selected", tracked_read)

    def action():
        successful_action(roots)()
        if invalid == "missing":
            return
        if invalid == "symlink":
            identity_file.symlink_to(roots["desktop_root"] / "local_desktop_1.json")
            return
        identity_file.write_text("x" * 257 if invalid == "oversized"
                                 else "local_desktop_1\nlocal_other\n" if invalid == "malformed"
                                 else "local_desktop_1\n")
        identity_file.chmod(0o644 if invalid == "public" else 0o600)

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="expected Desktop session"):
        invoke_with_id_file(roots, action, identity_file)
    assert all(path == str(identity_file) for path in reads)
    assert not roots["receipt_path"].exists()
    assert list(roots["temp_parent"].iterdir()) == []


def test_cli_accepts_late_ui_identity_file(roots, monkeypatch):
    canonical = {"transcript": roots["transcript_root"],
                 "desktop_metadata": roots["desktop_root"]}
    monkeypatch.setattr(capture, "canonical_claude_desktop_roots", lambda: canonical)
    captured = []
    monkeypatch.setattr(capture, "capture_claude_desktop_source_locations",
                        lambda **kwargs: captured.append(kwargs) or {"selected_absolute_paths": []})
    identity_file = roots["workspace"].parent / "ui-session-id.txt"
    arguments = ["--workspace", str(roots["workspace"]), "--run-id", "run-1",
                 "--expected-desktop-session-id-file", str(identity_file),
                 "--repetition", "1", "--run-marker", RUN_MARKER,
                 "--receipt", str(roots["receipt_path"]), "--", "true"]
    assert capture.main(arguments) == 0
    assert captured[0]["expected_desktop_session_id"] is None
    assert captured[0]["expected_desktop_session_id_file"] == identity_file


@pytest.mark.parametrize("both", [False, True])
def test_requires_exactly_one_ui_identity_input_before_action(roots, both):
    invoked = []
    identity = {
        "expected_desktop_session_id": "local_desktop_1",
        "expected_desktop_session_id_file": roots["workspace"].parent / "ui-session-id.txt",
    } if both else {}
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="exactly one expected Desktop"):
        capture.capture_claude_desktop_source_locations(
            transcript_root=roots["transcript_root"], desktop_root=roots["desktop_root"],
            workspace=roots["workspace"], run_id="run-1", repetition=1,
            run_marker=RUN_MARKER, run_action=lambda: invoked.append(True), **identity,
        )
    assert invoked == []


def test_deletions_alone_do_not_select_pair_or_open_old_contents(roots, monkeypatch):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)

    def action():
        for path in roots["unrelated"]:
            path.unlink()

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError) as error:
        invoke(roots, action)
    assert "changed counts: transcript=0, desktop_metadata=0, other=0" in str(error.value)
    assert "private-old" not in str(error.value)
    assert set(map(str, roots["unrelated"])).isdisjoint(opened)
    assert list(roots["temp_parent"].iterdir()) == []
    assert not roots["receipt_path"].exists()


def test_raw_snapshot_is_removed_when_run_action_fails(roots):
    def fail():
        raise RuntimeError("capture stopped")

    with pytest.raises(RuntimeError, match="capture stopped"):
        invoke(roots, fail)
    assert list(roots["temp_parent"].iterdir()) == []
    assert not roots["receipt_path"].exists()


@pytest.mark.parametrize(
    ("change", "expected_counts"),
    [
        ("missing_metadata", "transcript=1, desktop_metadata=0, other=0"),
        ("duplicate_match", "transcript=2, desktop_metadata=1, other=0"),
    ],
)
def test_ambiguous_inventory_reports_only_class_counts_and_removes_snapshot(
    roots, monkeypatch, change, expected_counts,
):
    opened = []
    original_open = os.open

    def tracked_open(path, flags, *args, **kwargs):
        opened.append(str(path))
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)

    def action():
        if change == "missing_metadata":
            (roots["transcript_root"] / "new.jsonl").write_text("private content")
            return
        successful_action(roots)()
        duplicate = roots["transcript_root"] / "another-project" / "cli-session-1.jsonl"
        duplicate.parent.mkdir()
        duplicate.write_text("private content")

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError) as error:
        invoke(roots, action)
    message = str(error.value)
    assert "did not isolate exactly one" in message
    assert expected_counts in message
    assert str(roots["transcript_root"]) not in message
    assert str(roots["desktop_root"]) not in message
    assert "private-old" not in message
    assert "private-extra" not in message
    source_opens = [path for path in opened
                    if path.startswith(str(roots["transcript_root"]))
                    or path.startswith(str(roots["desktop_root"]))]
    if change == "missing_metadata":
        assert not source_opens
    else:
        assert source_opens == [str(roots["desktop_root"] / "local_desktop_1.json")]
    assert list(roots["temp_parent"].iterdir()) == []
    assert not roots["receipt_path"].exists()


def test_rejects_wrong_workspace_after_opening_only_the_selected_pair(roots):
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="workspace identity"):
        invoke(roots, successful_action(roots, wrong_workspace=True))
    assert list(roots["temp_parent"].iterdir()) == []
    assert not roots["receipt_path"].exists()


def test_inventory_rejects_symlinks_hardlinks_and_specials(roots):
    alias = roots["transcript_root"] / "alias.jsonl"
    alias.symlink_to(roots["unrelated"][0])
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="symlink"):
        capture.inventory_claude_roots(
            transcript_root=roots["transcript_root"],
            desktop_root=roots["desktop_root"], run_id="run", repetition=1,
            phase="before",
        )
    alias.unlink()

    hardlink = roots["desktop_root"] / "hardlink.json"
    os.link(roots["unrelated"][0], hardlink)
    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="duplicate filesystem"):
        capture.inventory_claude_roots(
            transcript_root=roots["transcript_root"],
            desktop_root=roots["desktop_root"], run_id="run", repetition=1,
            phase="before",
        )


def test_selected_session_join_and_run_marker_are_required(roots):
    def wrong_join():
        workspace = roots["workspace"]
        transcript = roots["transcript_root"] / "cli-one.jsonl"
        transcript.write_text(json.dumps({
            "sessionId": "cli-one", "cwd": str(workspace),
            "message": {"content": "different marker"},
        }) + "\n")
        desktop = roots["desktop_root"] / "local_desktop_1.json"
        desktop.write_text(json.dumps({
            "sessionId": "local_desktop_1", "cliSessionId": "cli-two",
            "bridgeSessionIds": ["bridge"], "cwd": str(workspace),
        }))

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="did not isolate exactly one transcript"):
        invoke(roots, wrong_join)
    assert list(roots["temp_parent"].iterdir()) == []


def test_selected_transcript_must_contain_run_marker(roots):
    def action():
        successful_action(roots)()
        transcript = roots["transcript_root"] / "fixture-run" / "cli-session-1.jsonl"
        row = json.loads(transcript.read_text())
        row["message"]["content"] = "different task"
        transcript.write_text(json.dumps(row) + "\n")

    with pytest.raises(capture.ClaudeDesktopSourceCaptureError, match="run identity marker"):
        invoke(roots, action)
    assert list(roots["temp_parent"].iterdir()) == []
