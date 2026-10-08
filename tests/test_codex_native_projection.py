"""Constructed controls for native-only Codex facts added by the projection."""

from __future__ import annotations

import hashlib
import json

from session_bench.codex_native_projection import project_codex_native


BEFORE = "def checkout(items):\n    total = 0\n    return total + 5\n"
AFTER = "def checkout(items):\n    total = 0\n    return total\n"
DIFF = "@@ -2,2 +2,2 @@\n     total = 0\n-    return total + 5\n+    return total\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decoded(*, diff: str = DIFF, declared_sha256: str | None = None, contexts: list | None = None) -> dict:
    inspect_json = json.dumps({"checkout_sha256": declared_sha256 or _sha(BEFORE), "checkout_source": BEFORE, "phase": "inspect"})
    return {
        "records": [
            {"kind": "command_execution", "id": "exec-inspect",
             "fields": {"aggregated_output": f"SB_SURVIVAL_V1_HELPER_INSPECT_nonce {inspect_json}\n"}},
            {"kind": "file_change", "id": "exec-change",
             "fields": {"changes": {"fixture_project/checkout.py": {"type": "update", "unified_diff": diff}}}},
        ],
        "facts": {
            "submitted_turns": [],
            "visible_responses": [{"id": "msg-final", "turn_id": "turn-r1", "native_turn_id": "native-turn-1",
                                   "phase": "final_answer", "state": "present"}],
            "actions": [{"id": "action-inspect", "expected_id": "action-inspect", "state": "present",
                         "native_execution_id": "exec-inspect"}],
            "results": [],
            "changed_files": [{"id": "exec-change", "paths": ["fixture_project/checkout.py"],
                               "before_sha256": None, "after_sha256": None}],
            "model_contexts": contexts if contexts is not None else [
                {"kind": "turn_context", "turn_id": "native-turn-1", "fields": {"model": "vendor-model-5"}}],
        },
    }


def test_response_takes_the_model_of_its_native_turn_context() -> None:
    response = project_codex_native(_decoded())["responses"][0]

    assert response["model_id"] == "vendor-model-5"
    assert response["configuration"] == "vendor-model-5"


def test_response_stays_without_a_model_when_its_turn_has_two_different_models() -> None:
    contexts = [{"kind": "turn_context", "turn_id": "native-turn-1", "fields": {"model": "vendor-model-5"}},
                {"kind": "turn_context", "turn_id": "native-turn-1", "fields": {"model": "vendor-model-6"}}]

    response = project_codex_native(_decoded(contexts=contexts))["responses"][0]

    assert "model_id" not in response


def test_changed_file_hashes_come_from_the_native_inspect_source_and_patch() -> None:
    change = project_codex_native(_decoded())["file_changes"][0]

    assert change["path"] == "fixture_project/checkout.py"
    assert change["before_sha256"] == _sha(BEFORE)
    assert change["after_sha256"] == _sha(AFTER)


def test_changed_file_stays_unhashed_when_the_native_patch_does_not_fit_the_inspect_source() -> None:
    wrong = "@@ -2,2 +2,2 @@\n     total = 1\n-    return total + 5\n+    return total\n"

    change = project_codex_native(_decoded(diff=wrong))["file_changes"][0]

    assert change["before_sha256"] is None
    assert change["after_sha256"] is None


def test_changed_file_stays_unhashed_when_the_inspect_source_fails_its_own_hash() -> None:
    change = project_codex_native(_decoded(declared_sha256="0" * 64))["file_changes"][0]

    assert change["before_sha256"] is None
    assert change["after_sha256"] is None


def test_projection_does_not_mutate_the_decoded_facts() -> None:
    decoded = _decoded()

    project_codex_native(decoded)

    assert "model_id" not in decoded["facts"]["visible_responses"][0]
    assert decoded["facts"]["changed_files"][0]["before_sha256"] is None
