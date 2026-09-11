"""Constructed Session-Bench v1 fixture bundles.

These fixtures model semantic events for decoder/evaluator tests.  They are
constructed data and make no claim about any vendor's native format.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import shutil
from pathlib import Path
from typing import Any


_JSONL_FORMAT = "constructed-jsonl-v1"
_SQLITE_FORMAT = "constructed-sqlite-v1"
_FORMATS = {_JSONL_FORMAT, _SQLITE_FORMAT}
_MUTATIONS = {
    None,
    "remove_fact",
    "wrong_status",
    "duplicate",
    "missing_join",
    "malformed_tail",
    "unknown_event",
    "missing_companion",
    "empty_native",
    "captured_corruption",
    "attachment_payload",
    "branch_dangling",
    "branch_cycle",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_BEFORE_SOURCE = "def answer():\n    return 1\n"
_AFTER_SOURCE = "def answer():\n    return 2\n"


def _rows(before_sha256: str, after_sha256: str) -> list[dict[str, Any]]:
    """Return the independent semantic workload, not decoder output."""
    return [
        {"id": "session-1", "session_id": "session-1", "kind": "session", "fields": {
            "project": "fixture-project", "title": "unicode correction", "surface": "constructed",
        }},
        {"id": "session-2", "session_id": "session-2", "kind": "session", "fields": {
            "project": "fixture-project", "title": "second session", "surface": "constructed",
        }},
        {"id": "branch-main", "session_id": "session-1", "kind": "branch", "fields": {
            "name": "main", "parent_id": None, "status": "active",
        }},
        {"id": "branch-fork", "session_id": "session-1", "kind": "branch", "fields": {
            "name": "repair", "parent_id": "branch-main", "status": "active",
        }},
        {"id": "m-1", "session_id": "session-1", "kind": "message", "fields": {
            "role": "user", "text": "Inspect target.txt and fix the failing test. café 🙂", "turn": 1,
        }},
        {"id": "m-2", "session_id": "session-1", "kind": "message", "fields": {
            "role": "assistant", "text": "I found the defect.\n```python\nreturn value  \n```", "turn": 1,
        }},
        {"id": "tc-1", "session_id": "session-1", "kind": "tool_call", "fields": {
            "name": "shell", "action_kind": "inspect", "project_relative_target": "fixture_project/target.py",
            "arguments": {"command": "inspect fixture_project/target.py"},
            "helper_ids": ["h-1", "h-2"], "attempt": 1,
        }},
        {"id": "tr-1", "session_id": "session-1", "kind": "tool_result", "fields": {
            "tool_call_id": "tc-1", "action_kind": "inspect", "project_relative_target": "fixture_project/target.py",
            "status": "success", "stdout": _BEFORE_SOURCE, "exit_code": 0, "outcome": "inspected",
            "helper_ids": ["h-1", "h-2"],
        }},
        {"id": "tc-2", "session_id": "session-1", "kind": "tool_call", "fields": {
            "name": "shell", "action_kind": "test", "project_relative_target": "fixture_project/test_target.py",
            "arguments": {"command": "regression_test.py"},
            "helper_ids": ["h-3"], "attempt": 2,
        }},
        {"id": "tr-2", "session_id": "session-1", "kind": "tool_result", "fields": {
            "tool_call_id": "tc-2", "action_kind": "test", "project_relative_target": "fixture_project/test_target.py",
            "status": "failure", "stdout": "synthetic regression failure: expected 2, got 1\n", "exit_code": 1, "outcome": "failed",
            "helper_ids": ["h-3"],
        }},
        {"id": "tc-3", "session_id": "session-1", "kind": "tool_call", "fields": {
            "name": "shell", "action_kind": "edit", "project_relative_target": "fixture_project/target.py",
            "arguments": {"command": "edit fixture_project/target.py"},
            "helper_ids": ["h-4"], "attempt": 3,
        }},
        {"id": "tr-3", "session_id": "session-1", "kind": "tool_result", "fields": {
            "tool_call_id": "tc-3", "action_kind": "edit", "project_relative_target": "fixture_project/target.py",
            "status": "success", "stdout": "edited fixture_project/target.py\n", "exit_code": 0,
            "before_file_sha256": before_sha256, "after_file_sha256": after_sha256, "outcome": "edited",
            "helper_ids": ["h-4"],
        }},
        {"id": "tc-4", "session_id": "session-1", "kind": "tool_call", "fields": {
            "name": "shell", "action_kind": "test", "project_relative_target": "fixture_project/test_target.py",
            "arguments": {"command": "regression_test.py"},
            "helper_ids": ["h-5"], "attempt": 4,
        }},
        {"id": "tr-4", "session_id": "session-1", "kind": "tool_result", "fields": {
            "tool_call_id": "tc-4", "action_kind": "test", "project_relative_target": "fixture_project/test_target.py",
            "status": "success", "stdout": "synthetic regression passed\n", "exit_code": 0, "outcome": "passed",
            "helper_ids": ["h-5"],
        }},
        {"id": "m-correction", "session_id": "session-1", "kind": "message", "fields": {
            "role": "user", "text": "Correction: preserve the two spaces inside the code block.", "turn": 2,
        }},
        {"id": "m-3", "session_id": "session-1", "kind": "message", "fields": {
            "role": "assistant", "text": "The corrected regression now passes.", "turn": 2,
        }},
        {"id": "attachment-1", "session_id": "session-1", "kind": "attachment", "fields": {
            "path": "attachments/target.txt", "sha256": "", "size_bytes": 0,
        }},
        {"id": "m-4", "session_id": "session-2", "kind": "message", "fields": {
            "role": "user", "text": "List the files in the second session.", "turn": 1,
        }},
    ]


def _observer(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected = []
    for row in rows:
        if row["kind"] in {"session", "message", "tool_call", "tool_result", "branch", "attachment"}:
            expected.append({"id": row["id"], "session_id": row["session_id"], "kind": row["kind"]})
    events = []
    for sequence, row in enumerate(rows, 1):
        fields = {"id": row["id"], "session_id": row["session_id"], "kind": row["kind"]}
        fields.update({f"fields.{key}": value for key, value in row["fields"].items()})
        events.append({"id": f"obs-{sequence:03d}", "population_role": "primary_scored", "boundary": "constructed", "event_id": row["id"],
                       "session_id": row["session_id"], "fields": fields,
                       "source": "constructed-independent-spec", "sequence": sequence})
    extra = [
        ("obs-h1", "helper_invoked", "tc-1", {"helper_id": "h-1", "fields.helper_ids": ["h-1", "h-2"]}),
        ("obs-h2", "helper_invoked", "tc-1", {"helper_id": "h-2", "fields.helper_ids": ["h-1", "h-2"]}),
        ("obs-h3", "helper_emitted", "tc-2", {"helper_id": "h-3", "status": "failure"}),
        ("obs-h4", "helper_emitted", "tc-3", {"helper_id": "h-4", "status": "success"}),
        ("obs-h5", "displayed", "tc-4", {"helper_id": "h-5", "status": "success"}),
        ("obs-file-inspect", "file_observed", "tc-1", {"action": "inspect", "path": "fixture_project/target.py", "outcome": "observed"}),
        ("obs-file-edit", "file_observed", "tc-3", {"action": "edit", "path": "fixture_project/target.py", "outcome": "success"}),
        ("obs-test-fail", "file_observed", "tc-2", {"action": "test", "path": "fixture_project/test_target.py", "exit_code": 1, "outcome": "failure"}),
        ("obs-test-pass", "file_observed", "tc-4", {"action": "test", "path": "fixture_project/test_target.py", "exit_code": 0, "outcome": "success"}),
    ]
    for ident, boundary, event_id, fields in extra:
        events.append({"id": ident, "population_role": "supporting", "boundary": boundary, "event_id": event_id, "session_id": "session-1",
                       "fields": fields, "source": "constructed-independent-spec", "sequence": len(events) + 1})
    return {"schema_version": "1.0-prototype", "events": events}


def _expectations(rows: list[dict[str, Any]], mutation: str | None = None) -> dict[str, Any]:
    scenario = {"session": "C04", "message": "C01", "tool_call": "C02", "tool_result": "C02",
                "branch": "C04", "attachment": "C04"}
    assertions = []
    for row in rows:
        fields = [
            {"name": "id", "expected": row["id"], "comparison": "exact"},
            {"name": "session_id", "expected": row["session_id"], "comparison": "exact"},
            {"name": "kind", "expected": row["kind"], "comparison": "exact"},
        ]
        for name, value in sorted(row["fields"].items()):
            comparison = "text_lf" if isinstance(value, str) else "json" if isinstance(value, (dict, list)) else "exact"
            fields.append({"name": f"fields.{name}", "expected": value, "comparison": comparison})
        assertions.append({"id": f"event.{row['id']}", "assertion_role": "primary", "scenario": scenario.get(row["kind"], "C04"),
                           "subject": "decoder_correctness", "applicability": "required", "execution": "valid",
                           "observation_ids": [f"obs-{rows.index(row) + 1:03d}"], "session_id": row["session_id"],
                           "event_id": row["id"], "fields": fields,
                           "inspection": {"state": "uninspected", "locators": [], "evidence_ids": []},
                           "boundary": "constructed", "reason": "independently specified constructed event"})
    if mutation == "remove_fact":
        target = next(assertion for assertion in assertions if assertion["id"] == "event.tr-2")
        target["inspection"] = {"state": "absent", "locators": [], "evidence_ids": ["provenance-mutation.json"]}
        target["reason"] = "derived mutation removes the native fact; decoder must not infer absence"
    assertions.append({"id": "relationship.tool-envelope-helper", "assertion_role": "relationship", "scenario": "C02", "subject": "decoder_correctness",
                       "applicability": "required", "execution": "valid",
                       "observation_ids": ["obs-h1", "obs-h2"],
                       "session_id": "session-1", "event_id": "tc-1", "fields": [
                           {"name": "fields.helper_ids", "expected": ["h-1", "h-2"], "comparison": "json"}],
                       "inspection": {"state": "uninspected", "locators": [], "evidence_ids": []},
                       "boundary": "helper_invoked", "reason": "constructed N:M helper to envelope correspondence"})
    return {"schema_version": "1.0-prototype", "assertions": assertions}


def _apply_mutation(rows: list[dict[str, Any]], mutation: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = [json.loads(_json(row)) for row in rows]
    detail: dict[str, Any] = {"name": mutation, "transformation": "none"}
    if mutation == "remove_fact":
        result = [row for row in result if row["id"] != "tr-2"]
        detail["transformation"] = "remove row id tr-2 from selected native representation"
    elif mutation == "wrong_status":
        next(row for row in result if row["id"] == "tr-2")["fields"]["status"] = "success"
        detail["transformation"] = "change tr-2 status failure -> success"
    elif mutation == "duplicate":
        result.append(json.loads(_json(next(row for row in result if row["id"] == "tr-2"))))
        detail["transformation"] = "duplicate logical row tr-2"
    elif mutation == "missing_join":
        next(row for row in result if row["id"] == "tr-2")["fields"]["tool_call_id"] = "missing-call"
        detail["transformation"] = "replace tr-2 tool_call_id with missing-call"
    elif mutation == "branch_dangling":
        next(row for row in result if row["id"] == "branch-fork")["fields"]["parent_id"] = "missing-branch"
        detail["transformation"] = "replace branch-fork parent with missing-branch"
    elif mutation == "branch_cycle":
        next(row for row in result if row["id"] == "branch-main")["fields"]["parent_id"] = "branch-fork"
        detail["transformation"] = "make branch-main and branch-fork cyclic"
    elif mutation == "unknown_event":
        result.append({"id": "unknown-1", "session_id": "session-1", "kind": "unknown_event", "fields": {"x": 1}})
        detail["transformation"] = "append unknown native event kind"
    elif mutation == "empty_native":
        result = []
        detail["transformation"] = "remove all native event rows"
    return result, detail


def build_fixture(root: Path, format: str = _JSONL_FORMAT, mutation: str | None = None) -> Path:
    """Build and return a complete constructed fixture bundle at *root*."""
    root = Path(root)
    if format not in _FORMATS:
        raise ValueError(f"unsupported fixture format: {format}")
    if mutation not in _MUTATIONS:
        raise ValueError(f"unsupported fixture mutation: {mutation}")
    if root.exists() and any(root.iterdir()):
        raise ValueError("fixture root must be new or empty")
    root.parent.mkdir(parents=True, exist_ok=True)
    baseline_manifest_sha = None
    if mutation is not None:
        with tempfile.TemporaryDirectory(dir=str(root.parent), prefix=f".{root.name}-baseline-") as temp:
            baseline_bundle = build_fixture(Path(temp) / "bundle", format=format, mutation=None)
            baseline_manifest_sha = _sha256(baseline_bundle / "manifest.json")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("native", "observer", "expected", "workload", "provenance", "results", "attachments"):
        (root / name).mkdir(exist_ok=True)

    attachment = root / "native" / "attachments" / "target.txt"
    attachment.parent.mkdir(exist_ok=True)
    attachment.write_text("value = 2\n", encoding="utf-8")
    project = root / "workload" / "fixture_project"
    snapshots = project / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    (project / "target.py").write_text(_BEFORE_SOURCE, encoding="utf-8")
    (project / "test_target.py").write_text("import os\nimport sys\nsys.path.insert(0, os.path.dirname(__file__))\nfrom target import answer\n\nif answer() != 2:\n    raise SystemExit(f'synthetic regression failure: expected 2, got {answer()}')\n", encoding="utf-8")
    (snapshots / "target.before.py").write_text(_BEFORE_SOURCE, encoding="utf-8")
    (snapshots / "target.after.py").write_text(_AFTER_SOURCE, encoding="utf-8")
    before_source_sha = _sha256(project / "target.py")
    after_source_sha = hashlib.sha256(_AFTER_SOURCE.encode("utf-8")).hexdigest()
    baseline_rows = _rows(before_source_sha, after_source_sha)
    attachment_row = next(row for row in baseline_rows if row["id"] == "attachment-1")
    attachment_row["fields"].update({"sha256": _sha256(attachment), "size_bytes": attachment.stat().st_size})
    rows, detail = _apply_mutation(baseline_rows, mutation)
    native_path = root / "native" / ("session.jsonl" if format == _JSONL_FORMAT else "session.sqlite")
    if format == _JSONL_FORMAT:
        native_path.write_bytes(b"".join((_json(row) + "\n").encode("utf-8") for row in rows))
    else:
        con = sqlite3.connect(native_path)
        con.execute("CREATE TABLE events (row_key INTEGER PRIMARY KEY, payload TEXT)")
        for key, row in enumerate(rows, 1):
            con.execute("INSERT INTO events(row_key, payload) VALUES (?, ?)", (key, _json(row)))
        con.commit()
        con.close()
    if mutation == "malformed_tail":
        if format == _JSONL_FORMAT:
            with native_path.open("ab") as fh:
                fh.write(b'{"id":"malformed-tail"')
        else:
            con = sqlite3.connect(native_path)
            con.execute("INSERT INTO events(row_key, payload) VALUES (?, ?)", (9999, '{"id":"malformed-tail"'))
            con.commit()
            con.close()
        detail["transformation"] = "append malformed JSON payload"
    if mutation == "captured_corruption":
        data = native_path.read_bytes()
        native_path.write_bytes((b"not-a-sqlite-database\x00" + data[16:]) if format == _SQLITE_FORMAT else data.replace(b'{', b'[', 1))
        detail["transformation"] = "corrupt native header or first JSON record after capture"
    if mutation == "attachment_payload":
        attachment.write_bytes(b"value = 9\n")
        detail["transformation"] = "replace same-size attachment bytes while retaining original native reference digest"
    if mutation == "missing_companion":
        attachment.unlink()
        detail["transformation"] = "remove declared attachment companion"

    (root / "observer" / "events.json").write_text(_json(_observer(baseline_rows)) + "\n", encoding="utf-8")
    (root / "expected" / "assertions.json").write_text(_json(_expectations(baseline_rows, mutation)) + "\n", encoding="utf-8")
    (root / "workload" / "workload.json").write_text(_json({"origin": "constructed", "project": "fixture-project", "rows": baseline_rows}) + "\n", encoding="utf-8")
    detail.update({"baseline_sha256": hashlib.sha256(_json(baseline_rows).encode("utf-8")).hexdigest(), "source": "constructed-native-baseline"})
    (root / "provenance" / "mutation.json").write_text(_json(detail) + "\n", encoding="utf-8")

    # The native decoder index is relative to native/, and intentionally does
    # not index itself.  The manifest inventory below contains every file.
    native_entries = [
        {"id": "native-session", "path": native_path.relative_to(root / "native").as_posix(), "sha256": _sha256(native_path), "size_bytes": native_path.stat().st_size, "depends_on": []},
        {"id": "attachment-target", "path": "attachments/target.txt", "sha256": _sha256(attachment) if attachment.exists() else hashlib.sha256(b"value = 2\n").hexdigest(), "size_bytes": attachment.stat().st_size if attachment.exists() else len(b"value = 2\n"), "depends_on": ["native-session"]},
    ]
    (root / "native" / "decode.json").write_text(_json({"format": format, "artifacts": native_entries}) + "\n", encoding="utf-8")

    roles = {"native": "native", "observer": "observer", "expected": "expected", "provenance": "provenance", "workload": "workload"}
    inventory = []
    native_entry_by_path = {"native/" + entry["path"]: entry for entry in native_entries}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rel = path.relative_to(root).as_posix()
        top = rel.split("/", 1)[0]
        if rel == "native/decode.json":
            item = {"id": "native-index", "role": "native", "path": rel, "sha256": _sha256(path),
                    "size_bytes": path.stat().st_size, "depends_on": []}
        elif rel in native_entry_by_path:
            entry = native_entry_by_path[rel]
            item = {**entry, "role": "native", "path": rel}
        else:
            item = {"id": rel.replace("/", "-"), "role": roles.get(top, "native"), "path": rel,
                    "sha256": _sha256(path), "size_bytes": path.stat().st_size, "depends_on": []}
        inventory.append(item)
    if mutation == "missing_companion":
        entry = native_entry_by_path["native/attachments/target.txt"]
        inventory.append({**entry, "role": "native", "path": "native/attachments/target.txt"})
    # Point the mutation assertion at the manifest-stable provenance artifact.
    for assertion in json.loads((root / "expected/assertions.json").read_text())["assertions"]:
        if assertion["id"] == "event.tr-2.absence":
            assertion["inspection"]["evidence_ids"] = ["provenance-mutation.json"]
    (root / "expected" / "assertions.json").write_text(_json(json.loads((root / "expected/assertions.json").read_text())) + "\n", encoding="utf-8")
    source_manifest_sha256 = baseline_manifest_sha
    identity = f"constructed-{format.removeprefix('constructed-')}-{mutation or 'baseline'}"
    manifest = {
        "schema_version": "1.0-prototype", "protocol_version": "1.0-prototype",
        "run_id": identity, "capture_id": identity + "-capture",
        "origin": "constructed" if mutation is None else "derived_mutation", "track": "native_local",
        "subject": {"harness": "constructed", "version": "1", "surface": "cli", "mode": "offline-fixture", "os": "synthetic", "model": "none", "configuration": "default", "artifact_family": format, "schema_version": "1"},
        "capture": {"status": "invalid" if mutation == "missing_companion" else "valid", "reason": "declared companion removed by derived mutation" if mutation == "missing_companion" else "constructed fixture", "roots_complete": mutation != "missing_companion", "procedure": "constructed", "captured_at": "2026-09-10"},
        "artifacts": inventory,
        "decoder": {"format": format, "version": "0.1.0"},
        "provenance": {"workload": "synthetic-coding-v1", "observer": "constructed-ledger-v1", "license": "MIT", "privacy_review": "public-by-construction", "source_manifest_sha256": source_manifest_sha256, "transformation": detail["transformation"] if mutation else None},
        "execution": {"state": "valid", "reason": "constructed", "attempt": 1, "scenario_runs": 1, "native_sessions": 2},
        "observation": {"complete": True, "blind_spots": []},
        "expectations_path": "expected/assertions.json", "observer_path": "observer/events.json", "native_package": "native",
    }
    (root / "manifest.json").write_text(_json(manifest) + "\n", encoding="utf-8")
    return root


def damage_codex_rollout(source: Path, destination: Path, event_id: str,
                         mutation: str = "remove") -> tuple[Path, dict[str, Any]]:
    """Create a deterministic damaged copy of an explicit Codex package.

    Only the declared rollout JSONL is changed.  The source package is never
    modified, and the copied package's decode manifest is rehashed so the
    decoder can verify it as an independent derived input.
    """
    source = Path(source)
    destination = Path(destination)
    if mutation not in {"remove", "wrong_status"}:
        raise ValueError("unsupported Codex damage mutation")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("damage destination must be new or empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    manifest_path = destination / "decode.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "codex-rollout-v1":
        raise ValueError("damage source must be a codex-rollout-v1 package")
    changed = 0
    for item in manifest.get("artifacts", []):
        path = destination / item["path"]
        if path.suffix.lower() != ".jsonl":
            continue
        output: list[bytes] = []
        for raw in path.read_bytes().splitlines(keepends=True):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                output.append(raw)
                continue
            payload = obj.get("payload") if isinstance(obj, dict) else None
            nested = payload.get("payload") if isinstance(payload, dict) and isinstance(payload.get("payload"), dict) else payload
            matches = isinstance(nested, dict) and any(
                nested.get(key) == event_id for key in ("id", "message_id", "call_id")
            )
            if matches and mutation == "remove":
                changed += 1
                continue
            if matches and mutation == "wrong_status":
                if isinstance(nested, dict):
                    nested["status"] = "success" if nested.get("status") != "success" else "failure"
                encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                changed += 1
                if raw.endswith(b"\n"):
                    encoded += b"\n"
                output.append(encoded)
            else:
                output.append(raw)
        path.write_bytes(b"".join(output))
        item["sha256"] = _sha256(path)
        item["size_bytes"] = path.stat().st_size
    if changed != 1:
        raise ValueError(f"Codex damage target must match exactly one record, found {changed}")
    manifest_path.write_text(_json(manifest) + "\n", encoding="utf-8")
    return destination, {"event_id": event_id, "mutation": mutation, "changed_records": changed}


def damage_codex_fact(source: Path, destination: Path, fact: str,
                      replacement: str = "[SB_DAMAGED]") -> tuple[Path, dict[str, Any]]:
    """Replace every exact native string occurrence of a selected positive fact."""
    if not fact or fact == replacement:
        raise ValueError("damage fact and replacement must be distinct non-empty strings")
    source = Path(source)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("damage destination must be new or empty")
    shutil.copytree(source, destination, dirs_exist_ok=True)
    manifest_path = destination / "decode.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "codex-rollout-v1":
        raise ValueError("damage source must be a codex-rollout-v1 package")
    occurrences = 0

    def replace(value: Any) -> Any:
        nonlocal occurrences
        if isinstance(value, str):
            count = value.count(fact)
            occurrences += count
            return value.replace(fact, replacement)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    changed_artifacts: list[str] = []
    for item in manifest.get("artifacts", []):
        path = destination / item["path"]
        if path.suffix.lower() != ".jsonl":
            continue
        output: list[str] = []
        changed = False
        for raw in path.read_text(encoding="utf-8").splitlines(keepends=True):
            obj = json.loads(raw)
            before = occurrences
            obj = replace(obj)
            line_changed = occurrences != before
            changed = changed or line_changed
            if line_changed:
                encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
                output.append(encoded + ("\n" if raw.endswith("\n") else ""))
            else:
                output.append(raw)
        if changed:
            path.write_text("".join(output), encoding="utf-8")
            changed_artifacts.append(item["id"])
        item["sha256"] = _sha256(path)
        item["size_bytes"] = path.stat().st_size
    if occurrences < 1:
        raise ValueError("selected positive fact is absent from the native package")
    manifest_path.write_text(_json(manifest) + "\n", encoding="utf-8")
    return destination, {
        "fact_sha256": hashlib.sha256(fact.encode("utf-8")).hexdigest(),
        "replacement": replacement, "occurrences_changed": occurrences,
        "changed_artifact_ids": changed_artifacts,
    }
