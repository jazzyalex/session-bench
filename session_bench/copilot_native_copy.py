"""Fail-closed capture-time copy proof for one Copilot native session.

This module does not discover sessions.  A controller must supply the exact
session ID that it launched, then retain the returned receipt beside the
capture.  The receipt proves only that the complete selected session
directory was stable and copied byte-for-byte.
"""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any


RECEIPT_SCHEMA = "session-bench-copilot-native-copy-v1"
_SESSION_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def _tree_inventory(root: Path) -> dict[str, dict[str, Any]]:
    """Hash every ordinary file under *root* and reject ambiguous entries."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Copilot native session root is not an ordinary directory")
    result: dict[str, dict[str, Any]] = {}

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    raise ValueError("Copilot native session contains a symlink")
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    data = path.read_bytes()
                    result[path.relative_to(root).as_posix()] = {
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data),
                    }
                else:
                    raise ValueError("Copilot native session contains a special entry")

    visit(root)
    if not result:
        raise ValueError("Copilot native session is empty")
    return dict(sorted(result.items()))


def _inventory_sha256(inventory: dict[str, dict[str, Any]]) -> str:
    encoded = json.dumps(
        inventory, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def copy_quiescent_copilot_session(
    copilot_home: Path,
    session_id: str,
    destination_root: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
    stable_interval_seconds: float = 1.0,
    stable_checks: int = 2,
    copy_tree: Callable[[Path, Path], object] = shutil.copytree,
) -> dict[str, Any]:
    """Copy one explicitly selected session after proving bounded quiescence.

    The source is inventoried repeatedly before copying, once again after the
    copy, and compared with a fresh destination inventory.  Any change,
    symlink, special file, partial copy, or pre-existing destination fails the
    operation instead of producing a completeness receipt.
    """
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValueError("Copilot session ID is not a lowercase UUID")
    if stable_checks < 2 or stable_interval_seconds < 0:
        raise ValueError("invalid Copilot native quiescence policy")

    copilot_home = Path(copilot_home)
    session_state = copilot_home / "session-state"
    source = session_state / session_id
    destination_root = Path(destination_root)
    destination = destination_root / session_id
    if copilot_home.is_symlink() or not copilot_home.is_dir():
        raise ValueError("COPILOT_HOME is not an ordinary directory")
    if session_state.is_symlink() or not session_state.is_dir():
        raise ValueError("Copilot session-state root is not an ordinary directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Copilot native copy destination already exists")

    observations = [_tree_inventory(source)]
    for _ in range(stable_checks):
        sleep(stable_interval_seconds)
        observed = _tree_inventory(source)
        if observed != observations[-1]:
            raise ValueError("Copilot native session changed during quiescence")
        observations.append(observed)

    pre_copy = observations[-1]
    destination_root.mkdir(parents=True, exist_ok=True)
    copy_tree(source, destination)
    post_copy = _tree_inventory(source)
    copied = _tree_inventory(destination)
    if post_copy != pre_copy:
        raise ValueError("Copilot native session changed during copy")
    if copied != pre_copy:
        raise ValueError("Copilot native session copy differs from source")

    digest = _inventory_sha256(pre_copy)
    return {
        "schema_version": RECEIPT_SCHEMA,
        "session_id": session_id,
        "source_relative_path": f"session-state/{session_id}",
        "destination_relative_path": f"native/{session_id}",
        "quiescence_policy": {
            "stable_checks": stable_checks,
            "stable_interval_seconds": stable_interval_seconds,
        },
        "quiescence_observation_sha256": [
            _inventory_sha256(item) for item in observations
        ],
        "source_inventory_before_copy_sha256": digest,
        "source_inventory_after_copy_sha256": digest,
        "copied_inventory_sha256": digest,
        "source_unchanged_during_copy": True,
        "copy_matches_source": True,
        "complete_selected_session_directory": True,
        "files": pre_copy,
    }
