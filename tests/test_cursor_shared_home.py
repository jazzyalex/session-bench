"""Metadata-only bracket of the shared Cursor home on synthetic trees; no Cursor process and no real home."""
import json
from pathlib import Path
import shutil

import pytest

from session_bench.cursor_live_capture import execute_cursor_capture, prepare_cursor_capture
from session_bench.cursor_shared_home import SharedHomeError, classify_shared_home_changes, inventory_shared_home
from test_cursor_live_capture import SESSION, runner


def shared_home(tmp_path):
    home = tmp_path / "shared-home"
    (home / "ai-tracking").mkdir(parents=True)
    (home / "ai-tracking/ai-code-tracking.db").write_bytes(b"rows of other sessions")
    (home / "projects/other-project").mkdir(parents=True)
    (home / "projects/other-project/notes.json").write_text("{}")
    (home / "a-link").symlink_to(home / "projects")
    return home


def test_inventory_reads_no_content_and_follows_no_link(tmp_path):
    home = shared_home(tmp_path)
    snapshot = inventory_shared_home(home)
    kinds = {row["relative_path"]: row["kind"] for row in snapshot["entries"]}
    assert kinds["a-link"] == "link" and kinds["ai-tracking/ai-code-tracking.db"] == "file" and "a-link/other-project" not in kinds
    assert snapshot["metadata_only"] is True and all("sha256" not in row and "content" not in row for row in snapshot["entries"])


def test_changes_are_session_owned_shared_store_or_a_directory_above_them(tmp_path):
    home = shared_home(tmp_path)
    before = inventory_shared_home(home)
    (home / "ai-tracking/ai-code-tracking.db").write_bytes(b"rows of other sessions and one more row")
    (home / "ai-tracking/ai-code-tracking.db-journal").write_bytes(b"")
    (home / "projects/other-project" / SESSION).mkdir()
    (home / "projects/other-project" / SESSION / "note.txt").write_text("session")
    receipt = classify_shared_home_changes(before, inventory_shared_home(home), session_id=SESSION)
    assert [row["relative_path"] for row in receipt["shared_stores_changed"]] == ["ai-tracking/ai-code-tracking.db", "ai-tracking/ai-code-tracking.db-journal"]
    assert len(receipt["session_owned"]) == 2 and receipt["unexplained"] == 0
    assert set(receipt["directories_touched"]) <= {"ai-tracking", "projects/other-project"}


@pytest.mark.parametrize("change", ["new_file", "changed_file", "removed_file"])
def test_an_unexplained_change_fails_closed_and_the_error_names_no_path(tmp_path, change):
    home = shared_home(tmp_path)
    before = inventory_shared_home(home)
    target = home / "projects/other-project/notes.json"
    if change == "new_file":
        (home / "projects/other-project/secret-name.json").write_text("{}")
    elif change == "changed_file":
        target.write_text('{"changed": true}')
    else:
        target.unlink()
    with pytest.raises(SharedHomeError) as caught:
        classify_shared_home_changes(before, inventory_shared_home(home), session_id=SESSION)
    assert "other-project" not in str(caught.value) and "secret-name" not in str(caught.value) and len(caught.value.paths) >= 1


def prepared(tmp_path, home):
    repository = tmp_path / "repo"; destination = repository / "artifacts/survival-v1-runs"; destination.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload"
    shutil.copytree(source, repository / "fixtures/scenarios/survival-v1/workload")
    root = destination / "cursor-live-test"
    plan = prepare_cursor_capture(root, attempt_id=root.name, repetition=1, repository=repository, shared_home=home)
    assert plan["shared_home"] == str(home)
    return root


def tracking_runner(home, *, stray=False):
    inner = runner([])

    def execute(argv, **options):
        code = inner(argv, **options)
        (home / "ai-tracking/ai-code-tracking.db").write_bytes(b"rows of other sessions and a row of this session")
        if stray:
            (home / "projects/other-project/notes.json").write_text('{"touched": true}')
        return code
    return execute


def test_controller_brackets_the_shared_home_and_writes_a_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False); monkeypatch.delenv("CURSOR_API_ENDPOINT", raising=False)
    home = shared_home(tmp_path)
    root = prepared(tmp_path, home)
    state = execute_cursor_capture(root, runner=tracking_runner(home))
    assert state["state"] == "captured_private_unqualified"
    receipt = json.loads((root / "capture/shared-home-receipt.private.json").read_bytes())
    assert [row["relative_path"] for row in receipt["shared_stores_changed"]] == ["ai-tracking/ai-code-tracking.db"]
    assert (root / "observer/shared-home-before.private.json").is_file() and (root / "observer/shared-home-after.private.json").is_file()


def test_controller_fails_closed_on_an_unexplained_change_of_the_shared_home(tmp_path, monkeypatch):
    monkeypatch.delenv("CURSOR_API_KEY", raising=False); monkeypatch.delenv("CURSOR_API_ENDPOINT", raising=False)
    home = shared_home(tmp_path)
    root = prepared(tmp_path, home)
    state = execute_cursor_capture(root, runner=tracking_runner(home, stray=True))
    assert state["state"] == "invalid" and "unexplained" in state["reason_ids"][0]
    assert not (root / "capture/native-private").exists()


def test_the_bracket_is_only_for_an_isolated_capture(tmp_path):
    repository = tmp_path / "repo"; (repository / "artifacts/survival-v1-runs").mkdir(parents=True)
    shutil.copytree(Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload", repository / "fixtures/scenarios/survival-v1/workload")
    with pytest.raises(ValueError, match="isolated"):
        prepare_cursor_capture(repository / "artifacts/survival-v1-runs/x", attempt_id="x", repetition=1, repository=repository,
                               normal_root=tmp_path, shared_home=tmp_path)
