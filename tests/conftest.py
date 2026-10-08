"""Skip tests that need the private capture artifacts when they are absent.

The captures, private packets and review working sets live under `artifacts/`,
are ignored by git and are never published. In a clean checkout the tests named
in `private-artifact-tests.txt` cannot run; every other test must pass.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "artifacts/v1-expanded-preparation"


def pytest_collection_modifyitems(config, items):
    if PRIVATE.is_dir():
        return
    listed = set((Path(__file__).with_name("private-artifact-tests.txt")).read_text().split("\n"))
    skip = pytest.mark.skip(reason="needs the private capture artifacts, which are not published")
    for item in items:
        if item.nodeid in listed:
            item.add_marker(skip)
