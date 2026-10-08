"""Canonical JSON accounting for an explicitly bound Claude Desktop pair.

The persistent family is the CLI transcript plus native Desktop session
metadata. Transcript-only Desktop packages do not qualify this inventory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

from .claude_desktop_root import REQUIRED_FILES, validate_claude_desktop_family
from .native_density import (
    BYTE_ACCOUNTING_RULE, NativeDensityInventory, _claude_role,
    _strict_object, _unresolved, _USEFUL, _UNCLASSIFIED,
)
from .native_replay import _snapshot_tree
from .v1_public_score import CLASSIFIED_CONTENT_DENSITY_RULE


def inventory_claude_desktop_density(
    family: str | Path, *, session_id: str,
    expected_transcript_artifacts: Sequence[Mapping[str, str]] | None = None,
) -> NativeDensityInventory:
    """Inventory the exact two-file persistent family on a private snapshot.

    The decoded transcript digest and native cliSessionId join bind the pair.
    Every native JSON object retains its full canonical UTF-8 byte count,
    including Desktop metadata and unsupported transcript records. A caller
    still must independently qualify capture completeness before opting in.
    No observer truth, paths stored inside metadata, or live stores are read.
    """
    try:
        contents = _snapshot_tree(Path(family))
        if set(contents) != set(REQUIRED_FILES.values()):
            return _unresolved("Claude Desktop density requires exactly the persistent transcript/metadata pair")
        if sum(map(len, contents.values())) > 64 * 1024 * 1024:
            return _unresolved("Claude Desktop density family exceeds byte limit")
        transcript = contents[REQUIRED_FILES["transcript"]]
        transcript_sha = hashlib.sha256(transcript).hexdigest()
        if (not isinstance(session_id, str) or not session_id.strip()
                or expected_transcript_artifacts is None
                or {(row["id"], row["sha256"]) for row in expected_transcript_artifacts} != {("session", transcript_sha)}):
            return _unresolved("Claude Desktop family transcript differs from decoded artifact identity")
        with tempfile.TemporaryDirectory(prefix="session-bench-claude-density-") as temporary:
            root = Path(temporary)
            for name, raw in contents.items():
                destination = root / name;destination.parent.mkdir(parents=True, exist_ok=True);destination.write_bytes(raw)
            closure = validate_claude_desktop_family(root)
        if closure["cli_session_id"] != session_id:
            return _unresolved("Claude Desktop density family differs from decoded session identity")
        records, locators = [], []
        for relative, data in sorted(contents.items()):
            artifact_sha = hashlib.sha256(data).hexdigest()
            locators.append({"id": f"native-family:{relative}", "sha256": artifact_sha})
            lines = [data] if relative == REQUIRED_FILES["desktop_metadata"] else data.split(b"\n")
            for line_number, raw in enumerate(lines, 1):
                raw = raw.rstrip(b"\r\n")
                if not raw.strip():
                    continue
                if len(records) >= 100_000:
                    return _unresolved("Claude Desktop density exceeds record limit")
                obj = _strict_object(raw)
                if relative == REQUIRED_FILES["transcript"]:
                    if "sessionId" in obj and obj["sessionId"] != session_id:
                        return _unresolved("Claude Desktop density contains a foreign transcript session")
                    role = _claude_role(obj)
                else:
                    if obj.get("cliSessionId") != session_id:
                        return _unresolved("Claude Desktop metadata session binding is inconsistent")
                    role = "metadata"
                identity = f"family:{relative}:record-{line_number}"
                logical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                records.append({"record_id": identity, "record_kind": role, "logical_bytes": len(logical),
                                "classification": "useful" if role in _USEFUL else "unclassified" if role in _UNCLASSIFIED else "unknown"})
                locators.append({"id": identity, "sha256": hashlib.sha256(raw).hexdigest()})
        if not records:
            return _unresolved("Claude Desktop density family is empty")
        for module in (Path(__file__), Path(__file__).with_name("native_density.py")):
            locators.append({"id": f"rule:{BYTE_ACCOUNTING_RULE}:session_bench/{module.name}", "sha256": hashlib.sha256(module.read_bytes()).hexdigest()})
        return NativeDensityInventory({"evidence_complete": True, "classification_rule": CLASSIFIED_CONTENT_DENSITY_RULE, "records": records}, tuple(locators), ())
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, RecursionError) as error:
        return _unresolved(f"Claude Desktop density inventory failed: {error}")
