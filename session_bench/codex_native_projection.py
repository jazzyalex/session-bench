"""Normalize Codex decoder facts for the generic survival comparator.

Only native facts enter this projection. A tool relation needs explicit equal
native call IDs on both decoded endpoints. final_after needs the submitted R2,
edit, final check and final response in strict native locator order. Workload
logical labels already assigned by the native decoder remain semantic labels;
no independent observer fields, stdout item IDs, tokens or hashes are copied.

Two facts are joined from native records only.  A response takes the model of
the one ``turn_context`` that shares its native turn id.  A changed file takes
whole-file hashes from the source printed by the native inspect command and
from that source with the native ``FileChange`` hunk applied.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from .workload_instance import inspect_checkout_source

_HUNK = re.compile(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@.*\n")


def _responses_with_model(facts: Mapping[str, Any]) -> list[dict]:
    models: dict[str, set[str]] = {}
    for context in facts.get("model_contexts", []):
        model = context.get("fields", {}).get("model")
        if isinstance(context.get("turn_id"), str) and isinstance(model, str) and model:
            models.setdefault(context["turn_id"], set()).add(model)
    responses = []
    for row in facts["visible_responses"]:
        response = dict(row)
        turn_models = models.get(row.get("native_turn_id"), set())
        if len(turn_models) == 1:
            # Codex records no configuration identity beside the turn model.
            response["model_id"] = response["configuration"] = next(iter(turn_models))
        responses.append(response)
    return responses


def _apply_single_hunk(before: str, diff: Any) -> str | None:
    """Apply one exact unified hunk; None when the patch is ambiguous or does not fit."""
    if not isinstance(diff, str) or not diff.endswith("\n"):
        return None
    lines = diff.splitlines(keepends=True)
    match = _HUNK.fullmatch(lines[0]) if lines else None
    if match is None:
        return None
    old_start, old_count, new_start, new_count = map(int, match.groups())
    old, new = [], []
    for line in lines[1:]:
        if line[:1] not in {" ", "-", "+"}:
            return None
        if line[0] != "+":
            old.append(line[1:])
        if line[0] != "-":
            new.append(line[1:])
    source = before.splitlines(keepends=True)
    start = old_start - 1
    if old_start != new_start or len(old) != old_count or len(new) != new_count or source[start:start + old_count] != old:
        return None
    return "".join(source[:start] + new + source[start + old_count:])


def _inspect_source(decoded: Mapping[str, Any]) -> str | None:
    """The checkout source printed by the one native inspect command, if it self-hashes."""
    executions = {row.get("native_execution_id") for row in decoded["facts"]["actions"]
                  if row.get("expected_id") == "action-inspect" and row.get("state") == "present"}
    records = [row for row in decoded.get("records", [])
               if row.get("kind") == "command_execution" and row.get("id") in executions and row.get("id")]
    if len(records) != 1:
        return None
    return inspect_checkout_source(records[0].get("fields", {}).get("aggregated_output"))


def _changed_files_with_hashes(decoded: Mapping[str, Any]) -> list[dict]:
    before = _inspect_source(decoded)
    changes = []
    for row in decoded["facts"]["changed_files"]:
        change = dict(row)
        records = [item for item in decoded.get("records", []) if item.get("kind") == "file_change" and item.get("id") == row.get("id")]
        paths = row.get("paths")
        if before is not None and len(records) == 1 and isinstance(paths, list) and len(paths) == 1:
            detail = records[0].get("fields", {}).get("changes", {}).get(paths[0])
            after = _apply_single_hunk(before, detail.get("unified_diff") if isinstance(detail, Mapping) else None)
            if after is not None:
                change.update(path=paths[0], before_sha256=hashlib.sha256(before.encode("utf-8")).hexdigest(),
                              after_sha256=hashlib.sha256(after.encode("utf-8")).hexdigest())
        changes.append(change)
    return changes


def project_codex_native(decoded: Mapping[str, Any]) -> dict:
    facts = decoded["facts"]
    projected = dict(decoded)
    projected.update({"turns": facts["submitted_turns"], "responses": _responses_with_model(facts),
                      "actions": facts["actions"], "results": facts["results"], "file_changes": _changed_files_with_hashes(decoded)})
    relations = []
    for action in facts["actions"]:
        call = action.get("call_id")
        if action.get("state") != "present" or not isinstance(call, str) or not call:
            continue
        matches = [result for result in facts["results"] if result.get("state") == "present"
                   and result.get("action_id") == action.get("id") and result.get("call_id") == call]
        if len(matches) != 1:
            continue
        result = matches[0]
        relations.append({"id": f"native-relation-{action['id']}", "kind": "action_result",
                          "from_id": action["id"], "to_id": result["id"], "call_id": call,
                          "locator": action.get("locator", {}), "endpoint_locators": [action.get("locator", {}), result.get("locator", {})]})
    required = (
        [row for row in facts["submitted_turns"] if row.get("turn_id") == "turn-r2" and row.get("state") == "present"],
        [row for row in facts["actions"] if row.get("expected_id") == "action-edit" and row.get("turn_id") == "turn-r2" and row.get("state") == "present"],
        [row for row in facts["actions"] if row.get("expected_id") == "action-final" and row.get("turn_id") == "turn-r2" and row.get("state") == "present"],
        [row for row in facts["visible_responses"] if row.get("turn_id") == "turn-r2" and row.get("state") == "present"],
    )
    if all(len(rows) == 1 for rows in required):
        endpoints = [rows[0] for rows in required]
        locators = [row.get("locator", {}) for row in endpoints]
        ordinals = [locator.get("ordinal") for locator in locators]
        families = {(locator.get("artifact_id"), locator.get("artifact_sha256")) for locator in locators}
        if (len(families) == 1 and all(all(isinstance(v, str) and v for v in family) for family in families)
                and all(type(value) is int for value in ordinals) and all(a < b for a, b in zip(ordinals, ordinals[1:]))):
            relations.append({"id": "native-final-after-r2", "kind": "final_after", "from_id": "turn-r2",
                              "to_id": endpoints[2]["id"], "locator": locators[2], "endpoint_locators": locators})
    projected["relations"] = relations
    # facts.turn_response_relations remain consumed by the common comparator.
    return projected
