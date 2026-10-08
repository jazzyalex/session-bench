"""Independent response denominators survive deleted, duplicated or altered native text."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from session_bench.format_response_population import build_observer_rationale_evidence
from session_bench.v1_public_score import format_profile_document, validate_format_profile


def observer_document():
    events = []
    for repetition in (1, 2):
        events.extend([
            {"id": f"turn-{repetition}", "kind": "user_turn", "sequence": repetition * 2 - 1,
             "session_id": "observer-session", "population_role": "primary_scored", "metric_ids": [],
             "fields": {"text": f"perform revision {repetition}", "role": "user"}},
            {"id": f"response-{repetition}", "kind": "assistant_response", "sequence": repetition * 2,
             "session_id": "observer-session", "population_role": "primary_scored", "metric_ids": [],
             "fields": {"text": f"Revision {repetition} explanation SB_SURVIVAL_V1_TEST_R{repetition}",
                        "turn_id": f"turn-{repetition}", "canary": f"SB_SURVIVAL_V1_TEST_R{repetition}",
                        "role": "assistant", "status": "completed"}},
        ])
    return {"schema_version": "1.0-survival-observer", "protocol_version": "1.0-survival",
            "scenario_id": "survival-v1-repair", "independent": True,
            "run_id": "test-run", "events": events, "relations": []}


def native_document(family="codex"):
    expected = observer_document()["events"]
    turns = [{"id": f"native-turn-{index}", "kind": "submitted_turn", "role": "user",
              "text": expected[(index - 1) * 2]["fields"]["text"]} for index in (1, 2)]
    responses = [{"id": f"native-response-{index}", "kind": "response", "role": "assistant",
                  "turn_id": f"native-turn-{index}", "text": expected[index * 2 - 1]["fields"]["text"],
                  "status": "completed"} for index in (1, 2)]
    if family == "codex":
        return {"facts": {"submitted_turns": turns, "visible_responses": responses}}
    if family == "claude":
        return {"events": turns + responses}
    return {"turns": turns, "responses": responses}


def response_list(native, family):
    return native["facts"]["visible_responses"] if family == "codex" else native["events"] if family == "claude" else native["responses"]


def build(native, observed=None, *, complete_record_family=True):
    document = observer_document() if observed is None else observed
    raw = (json.dumps(document, indent=2) + "\n").encode()
    return build_observer_rationale_evidence(
        native, observer={"id": "observer-1", "sha256": hashlib.sha256(raw).hexdigest()},
        run_id="test-run", observer_document=raw,
        complete_record_family=complete_record_family,
    )


def metric(detail):
    profile = format_profile_document(run_id="test-run", configuration_id="codex-cli", repetition=1)
    profile["broad_evidence"]["broad.readable_rationale"] = detail
    return next(row for row in validate_format_profile(profile)["metrics"] if row["id"] == "broad.readable_rationale")


@pytest.mark.parametrize("family", ["codex", "claude", "opencode"])
def test_complete_observer_join_uses_observer_ids_and_matches_turn_aliases(family):
    native = native_document(family)
    snapshot = copy.deepcopy(native)
    detail = build(native)
    assert detail["response_ids"] == ["response-1", "response-2"]
    assert [row["id"] for row in detail["records"]] == ["response-1", "response-2"]
    result = metric(detail)
    assert result["state"] == "measured" and result["correct"] == result["observed_eligible"] == result["decoded_eligible"] == 2
    assert native == snapshot


@pytest.mark.parametrize("family", ["codex", "claude", "opencode"])
def test_deleted_native_response_cannot_shrink_observer_population(family):
    native = native_document(family)
    response_list(native, family).pop()
    result = metric(build(native))
    assert result["correct"] == result["decoded_eligible"] == 1
    assert result["observed_eligible"] == 2


@pytest.mark.parametrize("family", ["codex", "claude", "opencode"])
def test_duplicate_native_occurrence_remains_in_denominator(family):
    native = native_document(family)
    candidates = response_list(native, family)
    candidates.append(copy.deepcopy(candidates[-1]))
    result = metric(build(native))
    assert result["correct"] == result["observed_eligible"] == 2
    assert result["decoded_eligible"] == 3


@pytest.mark.parametrize("mutation", [
    lambda native: native["facts"]["visible_responses"][0].update(text="Different explanation SB_SURVIVAL_V1_TEST_R1"),
    lambda native: native["facts"]["visible_responses"][0].update(turn_id="native-turn-2"),
    lambda native: native["facts"]["visible_responses"][0].update(text="Same role alone does not bind a response"),
    lambda native: native["facts"]["visible_responses"][0].update(state="unknown"),
])
def test_matching_canary_role_or_id_cannot_override_text_turn_or_state_contradiction(mutation):
    native = native_document()
    mutation(native)
    result = metric(build(native))
    assert result["correct"] == 1
    assert result["observed_eligible"] == result["decoded_eligible"] == 2


@pytest.mark.parametrize("family", ["codex", "claude", "opencode"])
def test_an_observer_that_saw_only_the_canary_joins_a_response_that_ends_with_it(family):
    observed = observer_document()
    for row in observed["events"]:
        if row["kind"] == "assistant_response":
            row["fields"]["text"] = row["fields"]["canary"]
    native = native_document(family)
    detail = build(native, observed)
    assert [row["id"] for row in detail["records"]] == ["response-1", "response-2"]
    # The record keeps the native text, not the observer's canary.
    assert detail["records"][0]["ordered_text"] == "Revision 1 explanation SB_SURVIVAL_V1_TEST_R1"
    result = metric(detail)
    assert (result["state"], result["correct"], result["observed_eligible"]) == ("measured", 2, 2)
    # The canary must close the native response.
    response_list(native, family)[0]["text"] = "SB_SURVIVAL_V1_TEST_R1 then more prose"
    assert metric(build(native, observed))["correct"] == 1


def test_rationale_and_timestamp_builders_share_one_response_text_rule():
    from session_bench import format_response_population, format_timestamp_population

    assert format_timestamp_population.observed_response_text_matches is format_response_population.observed_response_text_matches
    match = format_response_population.observed_response_text_matches
    canary = {"text": "SB_SURVIVAL_V1_TEST_R1", "canary": "SB_SURVIVAL_V1_TEST_R1"}
    assert match(canary, "Explanation.\n\nSB_SURVIVAL_V1_TEST_R1\n") and match(canary, "SB_SURVIVAL_V1_TEST_R1")
    assert not match(canary, "SB_SURVIVAL_V1_TEST_R1 and more") and not match(canary, None)
    prose = {"text": "Observed prose SB_SURVIVAL_V1_TEST_R1", "canary": "SB_SURVIVAL_V1_TEST_R1"}
    assert match(prose, "Observed prose SB_SURVIVAL_V1_TEST_R1") and not match(prose, "Different prose SB_SURVIVAL_V1_TEST_R1")
    assert not match({"text": "", "canary": ""}, "anything") and not match({"canary": "X"}, "X")


@pytest.mark.parametrize("missing", ["text", "identity"])
def test_missing_observer_content_or_semantic_identity_is_unresolved(missing):
    observer = observer_document()
    fields = observer["events"][1]["fields"]
    if missing == "text":
        fields.pop("text")
    else:
        fields.pop("canary")
        fields.pop("turn_id")
    detail = build(native_document(), observer)
    assert detail["response_ids"] == ["response-1", "response-2"]
    assert metric(detail)["state"] == "unresolved"


def test_digest_without_observer_bytes_cannot_claim_complete_population():
    detail = build_observer_rationale_evidence(native_document(), observer={"id": "observer", "sha256": "a" * 64}, run_id="test-run")
    assert metric(detail)["state"] == "unresolved"


def test_wrong_digest_run_and_independence_are_rejected():
    raw = json.dumps(observer_document()).encode()
    with pytest.raises(ValueError, match="digest"):
        build_observer_rationale_evidence(native_document(), observer={"id": "observer", "sha256": "a" * 64}, run_id="test-run", observer_document=raw)
    with pytest.raises(ValueError, match="run_id"):
        build_observer_rationale_evidence(native_document(), observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()}, run_id="other-run", observer_document=raw)
    observer = observer_document()
    observer["independent"] = False
    with pytest.raises(ValueError, match="independent"):
        build(native_document(), observer)


def test_native_id_relabeling_is_not_the_source_of_required_ids():
    native = native_document()
    for row in native["facts"]["visible_responses"]:
        row["id"] = "identical-writer-id"
    result = metric(build(native))
    assert result["correct"] == result["observed_eligible"] == result["decoded_eligible"] == 2


def test_all_native_responses_missing_keeps_required_population_and_scores_absence():
    native = native_document()
    native["facts"]["visible_responses"] = []
    result = metric(build(native))
    assert result["state"] == "native_absent"
    assert result["observed_eligible"] == 2 and result["correct"] == result["decoded_eligible"] == 0


def test_unsupported_native_decode_is_unresolved_even_with_complete_observer():
    native = native_document()
    native["supported"] = False
    assert metric(build(native))["state"] == "unresolved"


@pytest.mark.parametrize("native", [{}, {"responses": None}, {"responses": [None]}])
def test_missing_or_malformed_native_response_stream_cannot_prove_absence(native):
    assert metric(build(native))["state"] == "unresolved"


def test_incomplete_family_cannot_become_scored_absence_or_pass():
    native = native_document()
    assert metric(build(native, complete_record_family=False))["state"] == "unresolved"
    native["facts"]["visible_responses"] = []
    assert metric(build(native, complete_record_family=False))["state"] == "unresolved"


def test_severe_diagnostics_override_nominal_ok_status():
    native = native_document()
    native.update(status="ok", diagnostics=[{"code": "malformed_record"}])
    assert metric(build(native))["state"] == "unresolved"


def test_one_native_occurrence_cannot_cover_ambiguous_observer_responses():
    observer = observer_document()
    observer["events"][3]["fields"] = copy.deepcopy(observer["events"][1]["fields"])
    detail = build(native_document(), observer)
    result = metric(detail)
    assert result["correct"] == 0 and result["observed_eligible"] == result["decoded_eligible"] == 2


def _builder_metric(document):
    from session_bench.v1_public_score import validate_format_evidence
    return next(row for row in validate_format_evidence(document)["profile"]["metrics"]
                if row["id"] == "broad.readable_rationale")


def test_claude_builder_uses_bound_observer_population_after_native_response_loss():
    from session_bench.claude_format_evidence import build_claude_format_evidence
    decoded = native_document("claude")
    decoded.update(format="claude-code-jsonl-v1", session_id="observer-session")
    decoded["events"].pop()
    raw = json.dumps(observer_document()).encode()
    kwargs = dict(observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()},
                  native_manifest={"id": "manifest", "sha256": "a" * 64},
                  run_id="test-run", configuration_id="claude-cli", repetition=1,
                  build="synthetic-build", collected_on="2026-09-29", result_id="synthetic-result",
                  complete_record_family=True)
    default = build_claude_format_evidence(decoded, **kwargs)
    assert _builder_metric(default)["state"] == "unresolved"
    explicit = build_claude_format_evidence(decoded, observer_document=raw, **kwargs)
    result = _builder_metric(explicit)
    assert result["observed_eligible"] == 2 and result["correct"] == result["decoded_eligible"] == 1


def test_codex_builder_uses_bound_observer_population_after_native_response_loss():
    from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle
    from session_bench.codex_format_evidence import build_codex_format_evidence
    root = Path(__file__).resolve().parents[1]
    decoded = decode_codex_cli_bundle(root / "tests/fixtures/codex-cli-native-0154")
    # Independent synthetic observer values are fixed by the fixture contract,
    # never projected from the response list that this control deletes.
    observed = observer_document()
    observed["run_id"] = "survival-v1-fixture-0001"
    for index, (text, canary) in enumerate([
        ("Observed baseline result. SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂", "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"),
        ("Changed delivery handling and final check passed. SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ", "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"),
    ], 1):
        observed["events"][index * 2 - 1]["fields"].update(text=text, canary=canary, turn_id=f"turn-r{index}")
    raw = json.dumps(observed, ensure_ascii=False).encode()
    decoded["facts"]["visible_responses"].pop()
    document = build_codex_format_evidence(
        decoded, observer={"id": "observer", "sha256": hashlib.sha256(raw).hexdigest()},
        native_manifest={"id": "manifest", "sha256": "a" * 64}, build="0.154.0",
        collected_on="2026-09-29", result_id="synthetic-format-result",
        complete_record_family=True, observer_document=raw,
    )
    result = _builder_metric(document)
    assert result["observed_eligible"] == 2 and result["correct"] == result["decoded_eligible"] == 1


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_opencode_builder_preserves_independent_expected_population(mutation, monkeypatch):
    import session_bench.opencode_format_evidence as module
    root = Path(__file__).resolve().parents[1]
    package = root / "artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction"
    original_read = module._read
    def mutated_decode(path):
        result = original_read(path)
        if path.name == "decoded.json":
            if mutation == "missing":
                result["responses"].pop()
            else:
                result["responses"].append(copy.deepcopy(result["responses"][-1]))
        return result
    monkeypatch.setattr(module, "_read", mutated_decode)
    document = module.build_opencode_format_evidence(
        package, collected_on="2026-09-29", complete_record_family=True,
        observer_document=(package / "observer.json").read_bytes(),
    )
    result = _builder_metric(document)
    assert result["observed_eligible"] == 2
    assert result["correct"] == (1 if mutation == "missing" else 2)
    assert result["decoded_eligible"] == (1 if mutation == "missing" else 3)
