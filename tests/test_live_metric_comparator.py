"""Constructed controls for the offline survival observer/native join."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from session_bench.live_metric_comparator import compare_survival_run
from session_bench.survival_metrics import METRICS, score_run


ROOT = Path(__file__).resolve().parents[1]
OBSERVER_PATH = ROOT / "fixtures/scenarios/survival-v1/workload/observer-truth.json"


def _observer() -> dict:
    value = json.loads(OBSERVER_PATH.read_text(encoding="utf-8"))

    def replace(item):
        if isinstance(item, str):
            return (
                item.replace("fixture_0001", "attempt_42")
                .replace("cafe_🙂", "cafe_attempt_42")
                .replace("correction_Δ", "correction_attempt_42")
            )
        if isinstance(item, list):
            return [replace(child) for child in item]
        if isinstance(item, dict):
            return {key: replace(child) for key, child in item.items()}
        return item

    return replace(value)


def _native(observer: dict) -> dict:
    events = {event["id"]: event for event in observer["events"]}
    turns = [
        {
            "id": "native-turn-r1",
            "turn_id": "turn-r1",
            "role": "user",
            "revision": "r1",
            "text": events["turn-r1"]["fields"]["text"],
            "sequence": 1,
        },
        {
            "id": "native-turn-r2",
            "turn_id": "turn-r2",
            "role": "user",
            "revision": "r2",
            "text": events["turn-r2"]["fields"]["text"],
            "sequence": 2,
        },
    ]
    responses = []
    for response_id, native_id, sequence in (
        ("response-r1", "native-response-r1", 10),
        ("response-r2", "native-response-r2", 19),
    ):
        fields = events[response_id]["fields"]
        responses.append(
            {
                "id": native_id,
                "turn_id": fields["turn_id"],
                "role": "assistant",
                "status": "completed",
                "text": fields["text"],
                "canary": fields["canary"],
                "model_id": fields["model_id"],
                "configuration": fields["configuration"],
                "sequence": sequence,
            }
        )
    actions = []
    results = []
    for action_id in ("action-inspect", "action-baseline", "action-edit", "action-final"):
        fields = events[action_id]["fields"]
        native_id = f"native-{action_id}"
        actions.append({"id": native_id, **{key: fields[key] for key in ("action_kind", "argv", "cwd", "target", "turn_id")}})
        result_id = action_id.replace("action", "result")
        result_fields = events[result_id]["fields"]
        results.append(
            {
                "id": f"native-{result_id}",
                "action_id": native_id,
                "status": result_fields["status"],
                "exit_code": result_fields["exit_code"],
                "helper_nonce": result_fields.get("helper_nonce"),
            }
        )
    relations = []
    for relation in observer["relations"]:
        if relation["kind"] not in {"action_result", "turn_response",
                                    "final_after"}:
            continue
        prefix = "native-"
        from_id = relation["from_id"]
        to_id = relation["to_id"]
        if relation["kind"] == "action_result":
            from_id = prefix + from_id
            to_id = prefix + to_id
        elif relation["kind"] == "turn_response":
            from_id = from_id
            to_id = "native-" + to_id
        elif relation["kind"] == "final_after":
            # The tightened comparator accepts only an explicit native
            # final_after relation; facts.revisions.final_after_r2 is ignored.
            from_id = from_id
            to_id = to_id
        relations.append({"id": prefix + relation["id"], "kind": relation["kind"], "from_id": from_id, "to_id": to_id})
    file_fields = events["file-change-checkout"]["fields"]
    usage_total = events["usage-total"]["fields"]
    return {
        "format": "constructed-native-v1",
        "supported": True,
        "turns": turns,
        "responses": responses,
        "actions": actions,
        "results": results,
        "file_changes": [{key: file_fields[key] for key in ("path", "before_sha256", "after_sha256")}],
        "relations": relations,
        "usage": [
            {
                "id": "native-usage-r1",
                "turn_id": "turn-r1",
                "usage": {
                    key: value
                    for key, value in events["response-r1"]["fields"]["usage"].items()
                    if key.endswith("_tokens")
                },
            },
            {
                "id": "native-usage-r2",
                "turn_id": "turn-r2",
                "usage": {
                    key: value
                    for key, value in events["response-r2"]["fields"]["usage"].items()
                    if key.endswith("_tokens")
                },
            },
        ],
        "facts": {
            "reconciliation": {"matches_session_totals": True},
            "usage": {
                "session_totals": {
                    "input": usage_total["input_tokens"],
                    "output": usage_total["output_tokens"],
                    "reasoning": usage_total.get("reasoning_tokens", 0),
                    "cache_read": usage_total["cache_read_tokens"],
                    "cache_write": usage_total["cache_write_tokens"],
                }
            },
        },
    }


def _receipt(**updates) -> dict:
    value = {
        "complete_root": True,
        "companions_present": True,
        "isolated_decode": True,
        "canonical_equality": True,
    }
    value.update(updates)
    return value


def _rows(document: dict) -> dict[str, dict]:
    return {row["id"]: row for row in document["metrics"]}


def test_instantiated_canaries_join_semantic_native_facts_and_score() -> None:
    observer = _observer()
    document = compare_survival_run(observer, _native(observer), _receipt(), configuration_id="cursor-cli", repetition=2)
    rows = _rows(document)

    assert set(rows) == set(METRICS)
    assert all(row["state"] == "measured" for row in rows.values())
    assert score_run(document).rankable
    assert score_run(document).overall == 100


def test_native_absence_requires_a_declared_complete_boundary() -> None:
    observer = _observer()
    empty_boundary = compare_survival_run(
        observer,
        {"format": "constructed-native-v1", "supported": True, "events": []},
        _receipt(),
        configuration_id="cursor-cli",
        repetition=1,
    )
    no_boundary = compare_survival_run(observer, {}, _receipt(), configuration_id="cursor-cli", repetition=1)
    format_only = compare_survival_run(
        observer,
        {"format": "constructed-native-v1", "supported": True},
        _receipt(),
        configuration_id="cursor-cli",
        repetition=1,
    )

    assert _rows(empty_boundary)["work.actions"]["state"] == "native_absent"
    assert _rows(no_boundary)["work.actions"]["state"] == "unresolved"
    assert _rows(format_only)["work.actions"]["state"] == "unresolved"


def test_aggregates_never_infer_actions_results_or_causal_relations() -> None:
    observer = _observer()
    native = {
        "format": "constructed-native-v1",
        "supported": True,
        "events": [],
        "facts": {
            "turns": {"submitted": 2, "responses": 2},
            "actions": {"count": 4, "completed_results": 4},
            "revisions": {"r1": True, "r2": True, "final_after_r2": True},
        },
    }
    document = compare_survival_run(observer, native, _receipt(), configuration_id="cursor-cli", repetition=1)
    rows = _rows(document)

    assert rows["work.actions"]["state"] == "native_absent"
    assert rows["work.results"]["state"] == "native_absent"
    assert rows["causal.action_result"]["state"] == "native_absent"
    assert rows["work.actions"]["decoded_eligible"] == 0
    assert rows["work.results"]["decoded_eligible"] == 0


def test_extra_same_turn_action_does_not_become_a_contradiction() -> None:
    observer = _observer()
    native = {
        "format": "constructed-native-v1",
        "supported": True,
        "actions": [
            {
                "id": "native-unrelated",
                "action_kind": "shell",
                "argv": ["bash", "-lc", "echo unrelated"],
                "cwd": "/tmp/other-worktree",
                "target": "other.txt",
                "turn_id": "turn-r1",
            }
        ],
    }
    document = compare_survival_run(observer, native, _receipt(), configuration_id="cursor-cli", repetition=1)

    row = _rows(document)["work.actions"]
    assert row["state"] == "unresolved"
    assert row["correct"] == 0
    assert row["decoded_eligible"] == 1


def test_unsupported_decoder_cannot_resolve_matching_records() -> None:
    observer = _observer()
    native = _native(observer)
    native["supported"] = False
    document = compare_survival_run(observer, native, _receipt(), configuration_id="cursor-cli", repetition=1)

    rows = _rows(document)
    assert rows["work.submitted_turns"]["state"] == "decoder_unsupported"
    assert rows["work.actions"]["state"] == "decoder_unsupported"


def test_response_canary_uses_the_supplied_attempt_and_rejects_a_stale_fixture_value() -> None:
    observer = _observer()
    native = _native(observer)
    native["responses"][0]["canary"] = "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"  # stale fixture value
    native["responses"][0]["text"] = "different prose\n" + native["responses"][0]["canary"]
    document = compare_survival_run(observer, native, _receipt(), configuration_id="cursor-cli", repetition=1)

    row = _rows(document)["work.visible_responses"]
    assert row["state"] == "measured"
    assert row["correct"] == 1
    assert row["decoded_eligible"] == 2


def test_portability_rows_use_only_explicit_receipt_values() -> None:
    observer = _observer()
    native = _native(observer)
    document = compare_survival_run(
        observer,
        native,
        {"complete_root": True, "companions_present": True, "isolated_decode": True},
        configuration_id="cursor-cli",
        repetition=1,
    )
    rows = _rows(document)

    assert rows["portable.complete_root"]["state"] == "measured"
    assert rows["portable.isolated_decode"]["state"] == "measured"
    assert rows["portable.canonical_equality"]["state"] == "unresolved"


def test_model_and_usage_must_match_observer_values() -> None:
    observer = _observer()
    native = _native(observer)
    native["responses"][0]["model_id"] = "wrong/model"
    native["usage"][0]["usage"]["input_tokens"] = 999

    document = compare_survival_run(
        observer,
        native,
        _receipt(),
        configuration_id="cursor-cli",
        repetition=1,
    )
    rows = _rows(document)

    assert rows["attribution.model_config"]["state"] == "measured"
    assert rows["attribution.model_config"]["correct"] == 1
    assert rows["attribution.usage"]["state"] == "measured"
    assert rows["attribution.usage"]["correct"] == 1


def test_usage_presence_and_response_join_are_measured_when_gui_observer_has_no_token_values() -> None:
    observer = _observer()
    for event in observer["events"]:
        if event["kind"] == "assistant_response":
            event["fields"].pop("usage", None)
            event["fields"].pop("usage_id", None)
    native = _native(_observer())

    document = compare_survival_run(
        observer,
        native,
        _receipt(),
        configuration_id="claude-desktop",
        repetition=1,
    )
    rows = _rows(document)

    assert rows["attribution.usage"] == {
        "id": "attribution.usage",
        "state": "measured",
        "correct": 2,
        "observed_eligible": 2,
        "decoded_eligible": 2,
    }
    assert rows["attribution.token_semantics"]["state"] == "measured"
    assert rows["attribution.token_semantics"]["correct"] == 2


def test_changed_file_fragments_do_not_substitute_for_whole_file_hashes() -> None:
    # The tightened comparator never substitutes edit fragments for exact
    # whole-file before_sha256/after_sha256.
    observer = _observer()
    event = next(item for item in observer["events"] if item["id"] == "file-change-checkout")
    event["fields"]["before_fragment"] = "old exact fragment"
    event["fields"]["after_fragment"] = "new exact fragment"
    native = _native(observer)
    native["file_changes"] = [{
        "path": "fixture_project/checkout.py",
        "oldString": "old exact fragment",
        "newString": "new exact fragment",
    }]

    document = compare_survival_run(
        observer, native, _receipt(), configuration_id="cursor-cli", repetition=1
    )

    assert _rows(document)["work.changed_files"] == {
        "id": "work.changed_files",
        "state": "native_absent",
        "correct": 0,
        "observed_eligible": 1,
        "decoded_eligible": 1,
    }


def test_reconciliation_requires_exact_native_session_totals() -> None:
    observer = _observer()
    native = _native(observer)
    native["facts"]["usage"]["session_totals"]["output"] += 1

    document = compare_survival_run(
        observer, native, _receipt(), configuration_id="cursor-cli", repetition=1
    )

    row = _rows(document)["attribution.reconciliation"]
    assert row["state"] == "contradiction"
    assert row["correct"] == 0


def test_fragment_mismatch_is_still_absent_but_hash_mismatch_is_contradiction() -> None:
    # Fragments alone never resolve, and a true whole-file hash mismatch
    # is a contradiction.
    observer = _observer()
    event = next(item for item in observer["events"] if item["id"] == "file-change-checkout")
    event["fields"]["before_fragment"] = "old exact fragment"
    event["fields"]["after_fragment"] = "new exact fragment"
    native = _native(observer)
    native["file_changes"] = [{
        "path": "fixture_project/checkout.py",
        "oldString": "different old fragment",
        "newString": "new exact fragment",
    }]

    document = compare_survival_run(
        observer, native, _receipt(), configuration_id="cursor-cli", repetition=1
    )

    row = _rows(document)["work.changed_files"]
    assert row["state"] == "native_absent"
    assert row["correct"] == 0

    native_hash_mismatch = _native(_observer())
    native_hash_mismatch["file_changes"][0]["after_sha256"] = "f" * 64
    mismatch_document = compare_survival_run(
        _observer(), native_hash_mismatch, _receipt(),
        configuration_id="cursor-cli", repetition=1,
    )
    mismatch_row = _rows(mismatch_document)["work.changed_files"]
    assert mismatch_row["state"] == "contradiction"
    assert mismatch_row["correct"] == 0
