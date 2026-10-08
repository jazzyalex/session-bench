"""Privacy-preserving source discovery for future Claude Desktop captures.

The caller inventories the two normal Claude Desktop roots without opening file
contents, then passes those metadata snapshots here.  This module performs no
filesystem access.  It proves one isolated transcript/desktop-metadata pair and
emits full paths only for those two selected artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence


INVENTORY_SCHEMA = "session-bench-claude-desktop-source-inventory-v1"
SELECTION_SCHEMA = "session-bench-claude-desktop-source-selection-v1"
DISCOVERY_SCHEMA = "session-bench-claude-desktop-source-discovery-v1"
STABLE_SCHEMA = "session-bench-claude-desktop-stable-source-v1"

_ROOTS = {
    "transcript": "claude-projects",
    "desktop_metadata": "claude-desktop-sessions",
}


def canonical_claude_desktop_roots(
    *, home: Path | None = None, environ: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Return the two normal Claude session roots without inspecting them."""
    home = Path.home() if home is None else Path(home)
    settings = os.environ if environ is None else environ
    claude_home = Path(settings.get("CLAUDE_HOME") or home / ".claude").expanduser()
    if not home.is_absolute() or not claude_home.is_absolute():
        _fail("Claude home must be an absolute path")
    return {
        "transcript": claude_home / "projects",
        "desktop_metadata": home / "Library/Application Support/Claude/claude-code-sessions",
    }
_SHA256 = re.compile(r"[0-9a-f]{64}")
_INVENTORY_KEYS = {
    "schema_version", "run_id", "repetition", "phase", "captured_at_ns",
    "metadata_only", "personal_history_content_read",
    "unrelated_content_read", "complete", "root_count", "entry_count",
    "roots",
}
_ROOT_KEYS = {
    "root_id", "source_root", "resolved_root", "filesystem_id", "complete",
    "aliases_detected", "entry_count", "entries",
}
_ENTRY_KEYS = {
    "source_path", "resolved_path", "device", "inode", "size_bytes",
    "ctime_ns", "mtime_ns", "kind", "is_regular_file", "is_symlink",
}
_SELECTION_KEYS = {
    "schema_version", "role", "run_id", "repetition", "cli_session_id",
    "desktop_session_id", "source_path", "device", "inode", "size_bytes",
    "ctime_ns", "mtime_ns", "sha256", "synthetic_fixture",
}


class ClaudeDesktopSourceDiscoveryError(ValueError):
    """The caller-provided discovery evidence is unsafe or inconclusive."""


def _fail(message: str) -> None:
    raise ClaudeDesktopSourceDiscoveryError(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_object(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    _require(set(value) == keys, f"{label} schema mismatch")
    return value


def _absolute_path(value: Any, label: str) -> str:
    _require(isinstance(value, str) and value.startswith("/"), f"{label} must be an absolute path")
    path = PurePosixPath(value)
    _require(str(path) == value and value != "/" and ".." not in path.parts,
             f"{label} is non-canonical")
    return value


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    _require(type(value) is int and (value >= 0 if allow_zero else value > 0),
             f"{label} is invalid")
    return value


def _entry_identity(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(entry[key] for key in (
        "device", "inode", "size_bytes", "ctime_ns", "mtime_ns", "kind",
    ))


def _validate_inventory(
    value: Mapping[str, Any], *, phase: str, run_id: str, repetition: int,
) -> dict[str, Any]:
    inventory = _strict_object(value, _INVENTORY_KEYS, f"{phase} inventory")
    _require(
        inventory["schema_version"] == INVENTORY_SCHEMA
        and inventory["run_id"] == run_id
        and inventory["repetition"] == repetition
        and inventory["phase"] == phase,
        f"{phase} inventory identity mismatch",
    )
    _positive_int(inventory["captured_at_ns"], f"{phase} capture time")
    _require(
        inventory["metadata_only"] is True
        and inventory["personal_history_content_read"] is False
        and inventory["unrelated_content_read"] is False
        and inventory["complete"] is True,
        f"{phase} inventory is unsafe or incomplete",
    )
    roots = inventory["roots"]
    _require(isinstance(roots, list) and inventory["root_count"] == len(roots) == 2,
             f"{phase} root population mismatch")
    parsed: dict[str, Any] = {}
    all_paths: set[str] = set()
    all_file_ids: set[tuple[int, int]] = set()
    total = 0
    for root_index, candidate in enumerate(roots):
        root = _strict_object(candidate, _ROOT_KEYS, f"{phase} root {root_index}")
        root_id = root["root_id"]
        _require(root_id in set(_ROOTS.values()) and root_id not in parsed,
                 f"{phase} root identity is missing or duplicated")
        source_root = _absolute_path(root["source_root"], f"{phase} {root_id} root")
        resolved_root = _absolute_path(root["resolved_root"], f"{phase} {root_id} resolved root")
        _require(source_root == resolved_root and root["aliases_detected"] is False,
                 f"{phase} {root_id} root is aliased")
        _require(root["complete"] is True, f"{phase} {root_id} root is incomplete")
        filesystem_id = root["filesystem_id"]
        _require(isinstance(filesystem_id, str) and filesystem_id,
                 f"{phase} {root_id} filesystem identity is missing")
        entries = root["entries"]
        _require(isinstance(entries, list) and root["entry_count"] == len(entries),
                 f"{phase} {root_id} entry population mismatch")
        parsed_entries: dict[str, Mapping[str, Any]] = {}
        ordered_paths: list[str] = []
        prefix = source_root + "/"
        for entry_index, candidate_entry in enumerate(entries):
            entry = _strict_object(candidate_entry, _ENTRY_KEYS,
                                   f"{phase} {root_id} entry {entry_index}")
            source_path = _absolute_path(entry["source_path"], f"{phase} entry path")
            resolved_path = _absolute_path(entry["resolved_path"], f"{phase} resolved entry path")
            _require(source_path.startswith(prefix), f"{phase} entry escapes declared root")
            _require(source_path == resolved_path and entry["is_symlink"] is False,
                     f"{phase} entry is aliased")
            _require(entry["is_regular_file"] is True, f"{phase} entry is not a regular file")
            for key in ("device", "inode", "ctime_ns", "mtime_ns"):
                _positive_int(entry[key], f"{phase} entry {key}")
            _positive_int(entry["size_bytes"], f"{phase} entry size", allow_zero=True)
            _require(entry["kind"] in {"transcript", "desktop_metadata", "other"},
                     f"{phase} entry kind is invalid")
            file_id = (entry["device"], entry["inode"])
            _require(source_path not in all_paths and file_id not in all_file_ids,
                     f"{phase} inventory contains duplicate paths or file aliases")
            all_paths.add(source_path)
            all_file_ids.add(file_id)
            ordered_paths.append(source_path)
            parsed_entries[source_path] = entry
        _require(ordered_paths == sorted(ordered_paths),
                 f"{phase} {root_id} entries are not canonically ordered")
        total += len(entries)
        parsed[root_id] = {
            "source_root": source_root,
            "filesystem_id": filesystem_id,
            "entries": parsed_entries,
        }
    _require(set(parsed) == set(_ROOTS.values()), f"{phase} required roots differ")
    _require(inventory["entry_count"] == total, f"{phase} aggregate entry count mismatch")
    return parsed


def _validate_selection(
    value: Mapping[str, Any], *, role: str, run_id: str, repetition: int,
    cli_session_id: str, desktop_session_id: str,
) -> Mapping[str, Any]:
    selection = _strict_object(value, _SELECTION_KEYS, f"{role} selection")
    _require(
        selection["schema_version"] == SELECTION_SCHEMA
        and selection["role"] == role
        and selection["run_id"] == run_id
        and selection["repetition"] == repetition
        and selection["cli_session_id"] == cli_session_id
        and selection["desktop_session_id"] == desktop_session_id,
        f"{role} selection identity mismatch",
    )
    _require(selection["synthetic_fixture"] is True,
             f"{role} selection is not fixture-scoped")
    _absolute_path(selection["source_path"], f"{role} selected path")
    for key in ("device", "inode", "ctime_ns", "mtime_ns"):
        _positive_int(selection[key], f"{role} selection {key}")
    _positive_int(selection["size_bytes"], f"{role} selection size", allow_zero=True)
    _require(isinstance(selection["sha256"], str) and _SHA256.fullmatch(selection["sha256"]) is not None,
             f"{role} selection digest is invalid")
    return selection


def verify_claude_desktop_source_discovery(
    *,
    run_id: str,
    repetition: int,
    cli_session_id: str,
    desktop_session_id: str,
    before_inventory: Mapping[str, Any],
    after_inventory: Mapping[str, Any],
    selected_transcript: Mapping[str, Any],
    selected_desktop_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify one caller-provided, metadata-only source discovery.

    The selected artifact digests are treated as caller-provided identities;
    this function never opens the source files to recompute them.
    """
    _require(isinstance(run_id, str) and run_id, "run identity is missing")
    _positive_int(repetition, "repetition")
    _require(isinstance(cli_session_id, str) and cli_session_id,
             "CLI session identity is missing")
    _require(isinstance(desktop_session_id, str) and desktop_session_id.startswith("local_"),
             "Desktop session identity is invalid")
    before = _validate_inventory(before_inventory, phase="before", run_id=run_id, repetition=repetition)
    after = _validate_inventory(after_inventory, phase="after", run_id=run_id, repetition=repetition)
    _require(after_inventory["captured_at_ns"] > before_inventory["captured_at_ns"],
             "inventory chronology mismatch")
    for root_id in _ROOTS.values():
        _require(
            before[root_id]["source_root"] == after[root_id]["source_root"]
            and before[root_id]["filesystem_id"] == after[root_id]["filesystem_id"],
            f"{root_id} identity changed between inventories",
        )

    selected = {
        "transcript": _validate_selection(
            selected_transcript, role="transcript", run_id=run_id,
            repetition=repetition, cli_session_id=cli_session_id,
            desktop_session_id=desktop_session_id,
        ),
        "desktop_metadata": _validate_selection(
            selected_desktop_metadata, role="desktop_metadata", run_id=run_id,
            repetition=repetition, cli_session_id=cli_session_id,
            desktop_session_id=desktop_session_id,
        ),
    }
    selected_paths = {item["source_path"] for item in selected.values()}
    _require(len(selected_paths) == 2, "selected pair reuses a source path")

    changed: dict[str, Mapping[str, Any]] = {}
    for root_id in _ROOTS.values():
        old = before[root_id]["entries"]
        new = after[root_id]["entries"]
        for path, entry in new.items():
            if path not in old or _entry_identity(entry) != _entry_identity(old[path]):
                changed[path] = entry
    _require(selected_paths <= set(changed),
             "selected pair is not among new or changed entries")
    matching_desktop_metadata = [
        path for path, entry in changed.items()
        if entry["kind"] == "desktop_metadata"
        and PurePosixPath(path).name == f"{desktop_session_id}.json"
    ]
    _require(len(matching_desktop_metadata) == 1
             and matching_desktop_metadata[0] == selected["desktop_metadata"]["source_path"],
             "selected Desktop metadata is not the unique changed Desktop session filename")
    _require(
        PurePosixPath(selected["desktop_metadata"]["source_path"]).name
        == f"{desktop_session_id}.json",
        "selected Desktop metadata filename does not match its session identity",
    )
    matching_transcripts = [
        path for path, entry in changed.items()
        if entry["kind"] == "transcript"
        and PurePosixPath(path).name == f"{cli_session_id}.jsonl"
    ]
    _require(len(matching_transcripts) == 1
             and matching_transcripts[0] == selected["transcript"]["source_path"],
             "selected transcript is not the unique changed CLI session filename")

    public_selected = []
    selected_root_ids: dict[str, str] = {}
    for role, selection in selected.items():
        root_id = _ROOTS[role]
        entry = changed.get(selection["source_path"])
        _require(entry is not None and entry["kind"] == role,
                 f"{role} selection is in the wrong root or has the wrong kind")
        _require(selection["source_path"] in after[root_id]["entries"],
                 f"{role} selection is outside its required root")
        for key in ("device", "inode", "size_bytes", "ctime_ns", "mtime_ns"):
            _require(selection[key] == entry[key], f"{role} selection metadata mismatch")
        source_root = after[root_id]["source_root"]
        relative_path = selection["source_path"][len(source_root) + 1:]
        _require(relative_path and not relative_path.startswith("/"),
                 f"{role} relative path is invalid")
        selected_root_ids[role] = root_id
        public_selected.append({
            "role": role,
            "source_path": selection["source_path"],
            "relative_path": relative_path,
            "source_root_sha256": hashlib.sha256(source_root.encode("utf-8")).hexdigest(),
            "filesystem_id_sha256": hashlib.sha256(after[root_id]["filesystem_id"].encode("utf-8")).hexdigest(),
            "device": selection["device"],
            "inode": selection["inode"],
            "size_bytes": selection["size_bytes"],
            "ctime_ns": selection["ctime_ns"],
            "mtime_ns": selection["mtime_ns"],
            "sha256": selection["sha256"],
        })

    root_summaries = []
    for root_id in sorted(_ROOTS.values()):
        selected_in_root = {
            item["source_path"] for item in public_selected
            if selected_root_ids[item["role"]] == root_id
        }
        unrelated = []
        for path, entry in after[root_id]["entries"].items():
            if path not in selected_in_root:
                unrelated.append({
                    "path_sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
                    "metadata_sha256": _digest(_entry_identity(entry)),
                })
        root_summaries.append({
            "root_id": root_id,
            "source_root_sha256": hashlib.sha256(after[root_id]["source_root"].encode("utf-8")).hexdigest(),
            "filesystem_id_sha256": hashlib.sha256(after[root_id]["filesystem_id"].encode("utf-8")).hexdigest(),
            "before_entry_count": len(before[root_id]["entries"]),
            "after_entry_count": len(after[root_id]["entries"]),
            "unrelated_entry_count": len(unrelated),
            "unrelated_inventory_sha256": _digest(unrelated),
        })

    body = {
        "schema_version": DISCOVERY_SCHEMA,
        "run_id": run_id,
        "repetition": repetition,
        "cli_session_id": cli_session_id,
        "desktop_session_id": desktop_session_id,
        "metadata_only_discovery": True,
        "personal_history_content_read": False,
        "unrelated_content_read": False,
        "complete_inventories": True,
        "isolated_pair": True,
        "selected_artifacts": public_selected,
        "root_summaries": root_summaries,
    }
    return {**body, "proof_sha256": _digest(body)}


def qualify_claude_desktop_stable_sources(
    discoveries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Qualify stable source locations from exactly three verified discoveries."""
    _require(isinstance(discoveries, Sequence) and not isinstance(discoveries, (str, bytes))
             and len(discoveries) == 3, "stable-source proof requires exactly three discoveries")
    expected_keys = {
        "schema_version", "run_id", "repetition", "cli_session_id",
        "desktop_session_id", "metadata_only_discovery",
        "personal_history_content_read", "unrelated_content_read",
        "complete_inventories", "isolated_pair", "selected_artifacts",
        "root_summaries", "proof_sha256",
    }
    runs: set[str] = set()
    cli_sessions: set[str] = set()
    desktop_sessions: set[str] = set()
    paths = {role: set() for role in _ROOTS}
    root_proofs: dict[str, set[tuple[str, str]]] = {role: set() for role in _ROOTS}
    observed = []
    for discovery in discoveries:
        receipt = _strict_object(discovery, expected_keys, "discovery receipt")
        body = {key: receipt[key] for key in expected_keys - {"proof_sha256"}}
        _require(receipt["schema_version"] == DISCOVERY_SCHEMA
                 and receipt["proof_sha256"] == _digest(body),
                 "discovery receipt integrity mismatch")
        _require(receipt["metadata_only_discovery"] is True
                 and receipt["personal_history_content_read"] is False
                 and receipt["unrelated_content_read"] is False
                 and receipt["complete_inventories"] is True
                 and receipt["isolated_pair"] is True,
                 "discovery receipt is unsafe or incomplete")
        _require(receipt["repetition"] in {1, 2, 3}, "discovery repetition is invalid")
        _require(receipt["run_id"] not in runs
                 and receipt["cli_session_id"] not in cli_sessions
                 and receipt["desktop_session_id"] not in desktop_sessions,
                 "stable-source proof reuses a run or session")
        runs.add(receipt["run_id"])
        cli_sessions.add(receipt["cli_session_id"])
        desktop_sessions.add(receipt["desktop_session_id"])
        artifacts = receipt["selected_artifacts"]
        _require(isinstance(artifacts, list) and len(artifacts) == 2,
                 "discovery selected pair is malformed")
        by_role = {item.get("role"): item for item in artifacts if isinstance(item, Mapping)}
        _require(set(by_role) == set(_ROOTS), "discovery selected roles differ")
        summaries = receipt["root_summaries"]
        _require(isinstance(summaries, list) and len(summaries) == 2,
                 "discovery root summaries are malformed")
        summary_by_root = {
            item.get("root_id"): item for item in summaries if isinstance(item, Mapping)
        }
        _require(set(summary_by_root) == set(_ROOTS.values()),
                 "discovery root summary population differs")
        for root_id, summary in summary_by_root.items():
            _require(set(summary) == {
                "root_id", "source_root_sha256", "filesystem_id_sha256",
                "before_entry_count", "after_entry_count",
                "unrelated_entry_count", "unrelated_inventory_sha256",
            }, f"{root_id} summary schema mismatch")
            _require(all(isinstance(summary[key], str) and _SHA256.fullmatch(summary[key])
                         for key in ("source_root_sha256", "filesystem_id_sha256",
                                     "unrelated_inventory_sha256")),
                     f"{root_id} summary digest is invalid")
            _require(all(type(summary[key]) is int and summary[key] >= 0
                         for key in ("before_entry_count", "after_entry_count",
                                     "unrelated_entry_count")),
                     f"{root_id} summary counts are invalid")
        for role, artifact in by_role.items():
            _require(set(artifact) == {
                "role", "source_path", "relative_path", "source_root_sha256",
                "filesystem_id_sha256", "device", "inode", "size_bytes",
                "ctime_ns", "mtime_ns", "sha256",
            }, f"{role} stable artifact schema mismatch")
            _absolute_path(artifact["source_path"], f"{role} stable source path")
            suffix = "/" + artifact["relative_path"]
            _require(artifact["source_path"].endswith(suffix),
                     f"{role} stable relative path mismatch")
            _require(all(isinstance(artifact[key], str) and _SHA256.fullmatch(artifact[key])
                         for key in ("source_root_sha256", "filesystem_id_sha256", "sha256")),
                     f"{role} stable digest is invalid")
            source_root = artifact["source_path"][:-len(suffix)]
            summary = summary_by_root[_ROOTS[role]]
            _require(hashlib.sha256(source_root.encode("utf-8")).hexdigest()
                     == artifact["source_root_sha256"] == summary["source_root_sha256"]
                     and artifact["filesystem_id_sha256"] == summary["filesystem_id_sha256"],
                     f"{role} stable source root binding mismatch")
            _require(artifact["source_path"] not in paths[role],
                     f"{role} stable proof reuses a selected path")
            paths[role].add(artifact["source_path"])
            root_proofs[role].add((artifact["source_root_sha256"], artifact["filesystem_id_sha256"]))
        observed.append({
            "repetition": receipt["repetition"],
            "run_id": receipt["run_id"],
            "selected_artifacts": artifacts,
        })
    _require({item["repetition"] for item in observed} == {1, 2, 3},
             "stable-source proof repetitions differ")
    _require(all(len(values) == 1 for values in root_proofs.values()),
             "source root or filesystem location is not stable")
    stable_roots = []
    for role in sorted(_ROOTS):
        root_sha, filesystem_sha = next(iter(root_proofs[role]))
        stable_roots.append({
            "role": role,
            "root_id": _ROOTS[role],
            "source_root_sha256": root_sha,
            "filesystem_id_sha256": filesystem_sha,
            "observed_repetitions": 3,
        })
    return {
        "schema_version": STABLE_SCHEMA,
        "evidence_complete": True,
        "metadata_only_discovery": True,
        "personal_history_scanned": False,
        "stable_roots": stable_roots,
        "observations": sorted(observed, key=lambda item: item["repetition"]),
    }


__all__ = [
    "ClaudeDesktopSourceDiscoveryError",
    "DISCOVERY_SCHEMA",
    "INVENTORY_SCHEMA",
    "SELECTION_SCHEMA",
    "STABLE_SCHEMA",
    "canonical_claude_desktop_roots",
    "qualify_claude_desktop_stable_sources",
    "verify_claude_desktop_source_discovery",
]
