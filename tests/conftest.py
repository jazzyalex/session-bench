"""Skip tests that need the private capture artifacts when they are absent.

The captures, private packets and review working sets live under `artifacts/`,
are ignored by git and are never published. In a clean checkout the tests named
in `private-artifact-tests.txt` cannot run; every other test must pass.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "artifacts/v1-expanded-preparation"


# These four tests need macOS file birth times, inode reuse rules or the system SQLite page layout.
MACOS_ONLY = {
    "tests/test_antigravity_public_sanitization.py::test_database_rewrite_blanks_private_blobs_removes_stale_pages_and_keeps_rows_and_size",
    "tests/test_cursor_cli_public_sanitization.py::test_a_private_store_with_free_pages_is_filled_to_the_same_length_with_zeroed_free_pages",
    "tests/test_normal_root_capture.py::test_explicit_shared_root_mode_tolerates_unrelated_old_file_removal",
    "tests/test_openclaw_state_capture.py::test_a_log_file_born_before_the_capture_is_not_copied",
}


def pytest_collection_modifyitems(config, items):
    if sys.platform != "darwin":
        macos = pytest.mark.skip(reason="written for macOS, where the captures were made")
        for item in items:
            if item.nodeid in MACOS_ONLY:
                item.add_marker(macos)
    if PRIVATE.is_dir():
        return
    listed = set((Path(__file__).with_name("private-artifact-tests.txt")).read_text().split("\n"))
    skip = pytest.mark.skip(reason="needs the private capture artifacts, which are not published")
    for item in items:
        if item.nodeid in listed:
            item.add_marker(skip)
