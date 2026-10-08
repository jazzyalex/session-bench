"""Independent timestamp populations survive loss and duplicates in native rows."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from session_bench.format_timestamp_population import build_observer_timestamp_evidence
from session_bench.v1_public_score import format_profile_document, validate_format_profile, validate_format_evidence

ROOT = Path(__file__).resolve().parents[1]


def observer():
    fields = [
        ("user_turn", {"text": "Inspect the test fixture", "role": "user"}),
        ("action", {"native_action_id": "native-action", "call_id": "call-1", "turn_id": "observed-user_turn", "argv": ["python3", "bench_check.py", "inspect"]}),
        ("result", {"native_result_id": "native-result", "call_id": "call-1", "action_id": "observed-action", "output": "result"}),
        ("file_change", {"native_change_id": "native-change", "action_id": "observed-action", "path": "fixture_project/checkout.py"}),
        ("assistant_response", {"text": "Finished SB_SURVIVAL_V1_RESPONSE_TEST", "turn_id": "observed-user_turn", "canary": "SB_SURVIVAL_V1_RESPONSE_TEST", "role": "assistant"}),
    ]
    return {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
            "scenario_id": "survival-v1-repair", "independent": True, "run_id": "test-run",
            "events": [{"id": "observed-" + kind, "kind": kind, "sequence": index,
                        "session_id": "observer-session", "population_role": "primary_scored",
                        "metric_ids": [], "fields": value} for index, (kind, value) in enumerate(fields, 1)], "relations": []}


def native(family):
    rows = [
        {"id": "native-turn", "kind": "submitted_turn", "turn_id": "native-turn", "text": "Inspect the test fixture", "role": "user"},
        {"id": "native-action", "kind": "action", "call_id": "call-1", "turn_id": "native-turn", "argv": ["python3", "bench_check.py", "inspect"]},
        {"id": "native-result", "kind": "result", "call_id": "call-1", "action_id": "native-action", "output": "result"},
        {"id": "native-change", "kind": "file_change", "action_id": "native-action", "path": "fixture_project/checkout.py"},
        {"id": "native-response", "kind": "response", "turn_id": "native-turn", "text": "Finished SB_SURVIVAL_V1_RESPONSE_TEST", "role": "assistant"},
    ]
    for index, row in enumerate(rows, 1):
        if family == "dsh":
            row["locator"] = {"line": index, "seq": index, "raw_sha256": "a" * 64, "physical_sha256": "b" * 64}
            row["timestamp_ms"] = 1789187830000 + index
        elif family == "opencode":
            row["locator"] = {"artifact": "opencode.db", "table": "message" if index in (1, 5) else "part", "row_id": row["id"]}
            row["time_created"] = 1789187830000 + index
        else:
            row["locator"] = {"artifact": "session.jsonl", "line": index}
            row["timestamp"] = f"2026-09-29T00:00:0{index}+02:00"
    value = {"supported": True, "diagnostics": []}
    if family in {"codex", "dsh"}:
        value["records"] = rows
    elif family == "claude":
        value["events"] = rows
    else:
        value.update(turns=[rows[0]], actions=[rows[1]], results=[rows[2]], file_changes=[rows[3]], responses=[rows[4]],
                     messages=[rows[0], rows[4]], parts=rows[1:4])
    return value


def build(value, family, *, document=None, complete=True):
    raw = json.dumps(observer() if document is None else document).encode()
    return build_observer_timestamp_evidence(value, family=family, observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()},
                                             run_id="test-run", observer_document=raw, complete_record_family=complete)


def metric(detail):
    profile = format_profile_document(run_id="test-run", configuration_id="codex-cli", repetition=1)
    profile["broad_evidence"]["broad.event_timestamps"] = detail
    return next(row for row in validate_format_profile(profile)["metrics"] if row["id"] == "broad.event_timestamps")


def remove(value, family, event_id):
    # Delete both the authoritative native row and every normalized projection
    # of that event, rather than deleting an incidental array view alone.
    for rows in value.values():
        if isinstance(rows, list):
            rows[:] = [row for row in rows if row.get("id") != event_id]


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_complete_exact_native_witnesses_cover_fixed_observer_population(family):
    value = native(family)
    before = copy.deepcopy(value)
    detail = build(value, family)
    assert detail["event_ids"] == [row["id"] for row in observer()["events"]]
    result = metric(detail)
    assert result["state"] == "measured"
    assert result["correct"] == result["observed_eligible"] == result["decoded_eligible"] == 5
    assert value == before


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
@pytest.mark.parametrize("event_id", ["native-turn", "native-response", "native-action", "native-result", "native-change"])
def test_selected_event_loss_cannot_reduce_expected_timestamp_population(family, event_id):
    value = native(family)
    remove(value, family, event_id)
    result = metric(build(value, family))
    assert result["state"] in {"measured", "native_absent"}
    assert result["observed_eligible"] == 5
    assert result["correct"] < 5 and result["decoded_eligible"] <= 4


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_duplicate_native_occurrences_are_penalized(family):
    value = native(family)
    if family in {"codex", "claude", "dsh"}:
        rows = value["records" if family in {"codex", "dsh"} else "events"]
        rows.append(copy.deepcopy(rows[-1]))
    else:
        # Repeated source and projection are two views of one additional
        # occurrence, not two extra copies in the output denominator.
        value["messages"].append(copy.deepcopy(value["messages"][-1]))
        value["responses"].append(copy.deepcopy(value["responses"][-1]))
    result = metric(build(value, family))
    assert result["correct"] == result["observed_eligible"] == 5
    assert result["decoded_eligible"] == 6


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
@pytest.mark.parametrize("bad_time", [None, True, "not-a-time", "2026-09-29T00:00:01", float("nan"), float("inf")])
def test_malformed_or_missing_timestamp_is_failure_with_complete_family(family, bad_time):
    value = native(family)
    row = value["messages"][0] if family == "opencode" else value["records" if family in {"codex", "dsh"} else "events"][0]
    row["timestamp_ms" if family == "dsh" else "time_created" if family == "opencode" else "timestamp"] = bad_time
    result = metric(build(value, family))
    assert result["state"] == "measured" and result["correct"] == 4
    assert result["observed_eligible"] == result["decoded_eligible"] == 5


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_incomplete_family_missing_observer_or_missing_stream_cannot_score(family):
    value = native(family)
    assert metric(build(value, family, complete=False))["state"] == "unresolved"
    assert metric(build({}, family))["state"] == "unresolved"
    detail = build_observer_timestamp_evidence(value, family=family, observer={"id": "observer", "sha256": "a" * 64}, run_id="test-run", complete_record_family=True)
    assert metric(detail)["state"] == "unresolved"


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_unscored_and_metadata_events_do_not_change_primary_denominator(family):
    value = native(family)
    observed = observer()
    extra = copy.deepcopy(observed["events"][1]);extra.update(id="unscored-action", sequence=6, population_role="unscored")
    observed["events"].append(extra)
    assert metric(build(value, family, document=observed))["observed_eligible"] == 5


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_exact_locator_witness_is_required(family):
    value = native(family)
    if family == "opencode":
        value["responses"][0] = copy.deepcopy(value["responses"][0])
        value["responses"][0]["locator"]["row_id"] = "different-row"
    else:
        value["records" if family in {"codex", "dsh"} else "events"][0].pop("locator")
    result = metric(build(value, family))
    assert result["correct"] == 4 and result["observed_eligible"] == 5


@pytest.mark.parametrize("family", ["codex", "claude", "opencode", "dsh"])
def test_error_diagnostics_and_malformed_projection_are_unresolved(family):
    value = native(family)
    value["diagnostics"] = [{"severity": "error", "code": "broken-root"}]
    assert metric(build(value, family))["state"] == "unresolved"
    value = native(family);value["responses"] = [None]
    assert metric(build(value, family))["state"] == "unresolved"


def test_observer_requires_semantic_identities_and_exact_digest():
    value = observer();value["events"][2]["fields"] = {"output": "result"}
    assert metric(build(native("codex"), "codex", document=value))["state"] == "unresolved"
    with pytest.raises(ValueError, match="digest"):
        build_observer_timestamp_evidence(native("codex"), family="codex", observer={"id": "observer", "sha256": "a" * 64}, run_id="test-run", observer_document=json.dumps(observer()).encode(), complete_record_family=True)


@pytest.mark.parametrize("kind,key,bad", [("action", "call_id", "other-call"), ("result", "output", "other-result"), ("file_change", "path", "other.py"), ("assistant_response", "text", "Altered SB_SURVIVAL_V1_RESPONSE_TEST")])
def test_same_turn_or_timestamp_cannot_override_native_identity_contradiction(kind, key, bad):
    value = native("codex")
    row = next(row for row in value["records"] if row["kind"] == ("response" if kind == "assistant_response" else kind))
    row[key] = bad
    assert metric(build(value, "codex"))["correct"] < 5


def test_opencode_builder_joins_independent_observer_to_exact_sqlite_row_times(monkeypatch):
    import session_bench.opencode_format_evidence as module
    package = ROOT / "artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction"
    raw = (package / "observer.json").read_bytes()
    observed = json.loads(raw)
    required = [row["id"] for row in observed["events"] if row["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"} and row["population_role"] == "primary_scored"]
    document = module.build_opencode_format_evidence(package, collected_on="2026-09-29", complete_record_family=True, observer_document=raw)
    detail = document["profile"]["broad_evidence"]["broad.event_timestamps"]
    assert detail["event_ids"] == required
    baseline = metric(detail)
    assert baseline["correct"] == baseline["observed_eligible"] == baseline["decoded_eligible"] == len(required) == 13
    original = module._read
    def mutate(path):
        decoded = original(path)
        if path.name == "decoded.json":
            decoded["responses"].pop()
        return decoded
    monkeypatch.setattr(module, "_read", mutate)
    detail = module.build_opencode_format_evidence(package, collected_on="2026-09-29", complete_record_family=True, observer_document=raw)["profile"]["broad_evidence"]["broad.event_timestamps"]
    result = metric(detail)
    assert detail["event_ids"] == required
    assert result["correct"] == result["decoded_eligible"] == 12 and result["observed_eligible"] == 13


def test_codex_builder_uses_fixed_fixture_observer_and_exact_raw_line_witnesses():
    from session_bench.adapters.codex_cli_decoder import FROZEN_SURVIVAL_V1, decode_codex_cli_bundle
    from session_bench.codex_format_evidence import build_codex_format_evidence
    # The fixture input contract and these literal observed outputs are fixed
    # before decoding; neither the expected IDs nor prose use surviving rows.
    observed = observer();observed["run_id"] = FROZEN_SURVIVAL_V1.run_id
    observed["events"] = []
    responses = [("Observed baseline result. SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂", "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"),
                 ("Changed delivery handling and final check passed. SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ", "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ")]
    for index, (turn_id, text) in enumerate(FROZEN_SURVIVAL_V1.turns, 1):
        observed["events"].extend([
            {"id": turn_id, "kind": "user_turn", "sequence": index * 2 - 1, "session_id": "fixture-observer", "population_role": "primary_scored", "metric_ids": [], "fields": {"text": text, "role": "user"}},
            {"id": f"response-r{index}", "kind": "assistant_response", "sequence": index * 2, "session_id": "fixture-observer", "population_role": "primary_scored", "metric_ids": [], "fields": {"text": responses[index - 1][0], "canary": responses[index - 1][1], "turn_id": turn_id, "role": "assistant"}},
        ])
    raw = json.dumps(observed, ensure_ascii=False).encode()
    decoded = decode_codex_cli_bundle(ROOT / "tests/fixtures/codex-cli-native-0154")
    def evaluate():
        value = build_codex_format_evidence(decoded, observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()}, native_manifest={"id": "manifest", "sha256": "a" * 64}, build="0.154.0", collected_on="2026-09-29", result_id="synthetic-result", complete_record_family=True, observer_document=raw)
        return next(row for row in validate_format_evidence(value)["profile"]["metrics"] if row["id"] == "broad.event_timestamps")
    baseline = evaluate()
    assert baseline["correct"] == baseline["observed_eligible"] == baseline["decoded_eligible"] == 4
    # The normalized response still exists, but its native record is gone.
    decoded["records"][:] = [row for row in decoded["records"] if row.get("id") != "msg-assistant-r2"]
    result = evaluate()
    assert result["correct"] == 3 and result["observed_eligible"] == result["decoded_eligible"] == 4


def test_claude_builder_scores_bound_primary_times_and_preserves_deleted_event():
    from session_bench.claude_format_evidence import build_claude_format_evidence
    raw = json.dumps(observer()).encode()
    decoded = native("claude");decoded.update(format="claude-code-jsonl-v1", session_id="native-session")
    def evaluate():
        value = build_claude_format_evidence(decoded, observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()}, native_manifest={"id": "manifest", "sha256": "a" * 64}, run_id="test-run", configuration_id="claude-cli", repetition=1, build="synthetic-build", collected_on="2026-09-29", result_id="synthetic-result", complete_record_family=True, observer_document=raw)
        return next(row for row in validate_format_evidence(value)["profile"]["metrics"] if row["id"] == "broad.event_timestamps")
    baseline = evaluate()
    assert baseline["correct"] == baseline["observed_eligible"] == baseline["decoded_eligible"] == 5
    remove(decoded, "claude", "native-result")
    result = evaluate()
    assert result["correct"] == result["decoded_eligible"] == 4 and result["observed_eligible"] == 5


def test_claude_compound_semantic_actions_sharing_a_block_are_not_cross_duplicates():
    value = native("claude");observed = observer()
    extra = copy.deepcopy(value["events"][1]);extra.update(id="native-compound-action", call_id="compound-call")
    value["events"].append(extra)
    expected = copy.deepcopy(observed["events"][1]);expected.update(id="observed-compound-action", sequence=6)
    expected["fields"].update(native_action_id="native-compound-action", call_id="compound-call")
    observed["events"].append(expected)
    result = metric(build(value, "claude", document=observed))
    assert result["correct"] == result["observed_eligible"] == result["decoded_eligible"] == 6


def test_malformed_native_locator_cannot_launder_a_neighbor_timestamp():
    value = native("codex");value["records"][0]["locator"]["line"] = True
    result = metric(build(value, "codex"))
    assert result["correct"] == 4 and result["observed_eligible"] == result["decoded_eligible"] == 5


def test_an_observer_that_saw_only_the_canary_joins_a_response_that_carries_it():
    document = observer()
    document["events"][4]["fields"]["text"] = "SB_SURVIVAL_V1_RESPONSE_TEST"
    value = native("claude")
    value["events"][4]["text"] = "All three tests pass.\n\nSB_SURVIVAL_V1_RESPONSE_TEST"
    row = metric(build(value, "claude", document=document))
    assert (row["state"], row["correct"], row["observed_eligible"]) == ("measured", 5, 5)
    value["events"][4]["text"] = "All three tests pass."
    assert metric(build(value, "claude", document=document))["correct"] == 4


def test_an_edit_action_is_identified_by_tool_target_and_turn():
    document = observer()
    document["events"][1]["fields"] = {"name": "Edit", "target": "fixture_project/checkout.py", "turn_id": "observed-user_turn"}
    row = metric(build(native("claude"), "claude", document=document))
    assert row["state"] == "measured" and row["observed_eligible"] == 5
    document["events"][1]["fields"] = {"name": "Edit", "turn_id": "observed-user_turn"}
    assert metric(build(native("claude"), "claude", document=document))["state"] == "unresolved"
