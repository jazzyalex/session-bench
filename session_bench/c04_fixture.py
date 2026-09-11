"""Constructed C04 portability fixtures with two physical session artifacts.

These files exercise the existing offline package, decoder, and evaluator
contracts.  They are purpose-built synthetic evidence, not vendor artifacts or
writer-behavior evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


_FORMAT = "constructed-jsonl-v1"
_RUN_ID = "c04-portability-run-1"
_ATTEMPT_ID = "c04-portability-attempt-1"
_SESSION_A = "c04-session-a"
_SESSION_B = "c04-session-b"
_TOKEN = "C04_PORTABILITY_TOKEN_cafe"
_MUTATIONS = {"damage_session_b_continuation", "missing_session_b_companion"}


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _session_rows(session_id: str, *, continuation_from: str | None = None) -> list[dict]:
    session_fields = {
        "scenario_run_id": _RUN_ID,
        "attempt_id": _ATTEMPT_ID,
        "title": "C04 constructed portability source",
    }
    if continuation_from is not None:
        session_fields["continuation_from_session_id"] = continuation_from
    rows = [
        {"id": "session", "session_id": session_id, "kind": "session", "fields": session_fields},
        {"id": "anchor", "session_id": session_id, "kind": "message", "fields": {
            "token": _TOKEN, "turn": 1,
        }},
    ]
    if continuation_from is not None:
        rows.append({"id": "continuation", "session_id": session_id, "kind": "message", "fields": {
            "token": _TOKEN,
            "continuation_from_session_id": continuation_from,
            "turn": 2,
        }})
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_canonical(row) + b"\n" for row in rows))


def _native_index(root: Path) -> dict:
    entries = [
        {"id": "native-session-a", "path": "session-a.jsonl", "sha256": _sha256(root / "native/session-a.jsonl"),
         "size_bytes": (root / "native/session-a.jsonl").stat().st_size, "depends_on": []},
        {"id": "native-session-b", "path": "session-b.jsonl", "sha256": _sha256(root / "native/session-b.jsonl"),
         "size_bytes": (root / "native/session-b.jsonl").stat().st_size, "depends_on": ["session-b-companion"]},
        {"id": "session-b-companion", "path": "session-b.companion",
         "sha256": _sha256(root / "native/session-b.companion"),
         "size_bytes": (root / "native/session-b.companion").stat().st_size, "depends_on": []},
    ]
    return {"format": _FORMAT, "artifacts": entries}


def _observer(rows: list[dict]) -> dict:
    events = []
    for sequence, row in enumerate(rows, 1):
        fields = {"id": row["id"], "session_id": row["session_id"], "kind": row["kind"]}
        fields.update({f"fields.{key}": value for key, value in row["fields"].items()})
        events.append({
            "id": f"obs-{sequence:03d}", "population_role": "primary_scored", "boundary": "constructed",
            "event_id": row["id"], "session_id": row["session_id"], "fields": fields,
            "source": "constructed-c04-portability-spec", "sequence": sequence,
        })
    return {"schema_version": "1.0-prototype", "events": events}


def _expectations(rows: list[dict]) -> dict:
    assertions = []
    for sequence, row in enumerate(rows, 1):
        fields = [
            {"name": "id", "expected": row["id"], "comparison": "exact"},
            {"name": "session_id", "expected": row["session_id"], "comparison": "exact"},
            {"name": "kind", "expected": row["kind"], "comparison": "exact"},
        ]
        for key, value in sorted(row["fields"].items()):
            fields.append({"name": f"fields.{key}", "expected": value,
                           "comparison": "json" if isinstance(value, (dict, list)) else "exact"})
        assertions.append({
            "id": f"c04.{row['session_id']}.{row['id']}", "assertion_role": "primary", "scenario": "C04",
            "subject": "decoder_correctness", "applicability": "required", "execution": "valid",
            "observation_ids": [f"obs-{sequence:03d}"], "session_id": row["session_id"],
            "event_id": row["id"], "fields": fields,
            "inspection": {"state": "uninspected", "locators": [], "evidence_ids": []},
            "boundary": "constructed", "reason": "constructed C04 physical-session portability fixture",
        })
    return {"schema_version": "1.0-prototype", "assertions": assertions}


def _write_manifest(root: Path, *, origin: str, source_manifest_sha256: str | None,
                    transformation: str | None, mutation: str | None = None,
                    companion_missing: bool = False) -> None:
    index = json.loads((root / "native/decode.json").read_text(encoding="utf-8"))
    native_by_path = {f"native/{entry['path']}": entry for entry in index["artifacts"]}
    inventory = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"):
        relative = path.relative_to(root).as_posix()
        if relative == "native/decode.json":
            inventory.append({"id": "native-index", "role": "native", "path": relative,
                              "sha256": _sha256(path), "size_bytes": path.stat().st_size, "depends_on": []})
        elif relative in native_by_path:
            entry = native_by_path[relative]
            inventory.append({**entry, "role": "native", "path": relative})
        else:
            role = {"observer": "observer", "expected": "expected", "provenance": "provenance"}[relative.split("/", 1)[0]]
            inventory.append({"id": relative.replace("/", "-"), "role": role, "path": relative,
                              "sha256": _sha256(path), "size_bytes": path.stat().st_size, "depends_on": []})
    if companion_missing:
        entry = native_by_path["native/session-b.companion"]
        inventory.append({**entry, "role": "native", "path": "native/session-b.companion"})
    manifest = {
        "schema_version": "1.0-prototype", "protocol_version": "1.0-prototype",
        "run_id": _RUN_ID if mutation is None else f"{_RUN_ID}-{mutation}",
        "capture_id": "c04-portability-capture-1" if mutation is None else f"c04-portability-capture-1-{mutation}",
        "origin": origin,
        "track": "native_local",
        "subject": {"harness": "constructed", "version": "1", "surface": "cli", "mode": "offline-fixture",
                    "os": "synthetic", "model": "none", "configuration": "default",
                    "artifact_family": _FORMAT, "schema_version": "1"},
        "capture": {"status": "invalid" if companion_missing else "valid",
                    "reason": "declared session-B companion removed" if companion_missing else "constructed C04 fixture",
                    "roots_complete": not companion_missing, "procedure": "constructed", "captured_at": "2026-09-11"},
        "artifacts": inventory, "decoder": {"format": _FORMAT, "version": "0.1.0"},
        "provenance": {"workload": "synthetic-c04-portability-v1", "observer": "constructed-c04-ledger-v1",
                       "license": "MIT", "privacy_review": "public-by-construction",
                       "source_manifest_sha256": source_manifest_sha256, "transformation": transformation},
        "execution": {"state": "valid", "reason": "one constructed C04 scenario run and attempt",
                      "attempt": 1, "scenario_runs": 1, "native_sessions": 2},
        "observation": {"complete": True, "blind_spots": []},
        "expectations_path": "expected/assertions.json", "observer_path": "observer/events.json", "native_package": "native",
    }
    _write_json(root / "manifest.json", manifest)


def build_c04_portability_fixture(root: Path) -> Path:
    """Create an intact, two-physical-session C04 constructed fixture."""
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ValueError("fixture root must be new or empty")
    root.mkdir(parents=True, exist_ok=True)
    rows_a = _session_rows(_SESSION_A)
    rows_b = _session_rows(_SESSION_B, continuation_from=_SESSION_A)
    _write_jsonl(root / "native/session-a.jsonl", rows_a)
    _write_jsonl(root / "native/session-b.jsonl", rows_b)
    (root / "native/session-b.companion").write_bytes(b"constructed C04 session-B companion\n")
    _write_json(root / "native/decode.json", _native_index(root))
    rows = rows_a + rows_b
    _write_json(root / "observer/events.json", _observer(rows))
    _write_json(root / "expected/assertions.json", _expectations(rows))
    _write_json(root / "provenance/mutation.json", {"name": None, "transformation": "none"})
    _write_manifest(root, origin="constructed", source_manifest_sha256=None, transformation=None)
    return root


def derive_c04_portability_fixture(source: Path, destination: Path, mutation: str) -> Path:
    """Copy an intact C04 fixture and apply one auditable negative control."""
    if mutation not in _MUTATIONS:
        raise ValueError("unsupported C04 fixture mutation")
    source, destination = Path(source), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("derived fixture destination must be new or empty")
    source_manifest_sha256 = _sha256(source / "manifest.json")
    source_index = json.loads((source / "native/decode.json").read_text(encoding="utf-8"))
    source_entries = {entry["id"]: entry for entry in source_index["artifacts"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    companion_missing = mutation == "missing_session_b_companion"
    if mutation == "damage_session_b_continuation":
        path = destination / "native/session-b.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        continuation = next(row for row in rows if row["id"] == "continuation")
        continuation["fields"].update({"token": "C04_DAMAGED_TOKEN", "continuation_from_session_id": "missing-session"})
        _write_jsonl(path, rows)
        _write_json(destination / "native/decode.json", _native_index(destination))
        transformation = "replace session-B continuation token and source-session join"
        changed_native_locations = [{
            "artifact_id": "native-session-b", "path": "native/session-b.jsonl",
            "before_sha256": source_entries["native-session-b"]["sha256"],
            "after_sha256": _sha256(destination / "native/session-b.jsonl"),
        }]
    else:
        (destination / "native/session-b.companion").unlink()
        transformation = "remove declared session-B companion from copied native package"
        changed_native_locations = [{
            "artifact_id": "session-b-companion", "path": "native/session-b.companion",
            "before_sha256": source_entries["session-b-companion"]["sha256"], "after_sha256": None,
        }]
    _write_json(destination / "provenance/mutation.json", {
        "name": mutation, "transformation": transformation,
        "source_manifest_sha256": source_manifest_sha256,
        "changed_native_locations": changed_native_locations,
    })
    _write_manifest(destination, origin="derived_mutation", source_manifest_sha256=source_manifest_sha256,
                    transformation=transformation, mutation=mutation, companion_missing=companion_missing)
    return destination
