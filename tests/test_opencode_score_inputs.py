import json
from pathlib import Path
import pytest
from session_bench.native_replay import _snapshot_tree, canonical
from session_bench.opencode_score_inputs import validate_opencode_capture_assertion, build_opencode_replay_evidence, sha

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-native-score-v2/opencode-1-18-31-eval-1"


def inputs():
    contents = _snapshot_tree(PACKET)
    return contents, json.loads(contents["inputs/context.json"]), json.loads(contents["native/decode.json"])


def refresh(contents, path, value):
    """Repair integrity references, so controls exercise semantic validation."""
    contents[path] = canonical(value)
    proof = json.loads(contents["inputs/root-proofs.json"])
    for repetition in proof["repetitions"]:
        for member in repetition["files"]:
            if member["path"] == path:
                member.update(sha256=sha(contents[path]), size_bytes=len(contents[path]))
    contents["inputs/root-proofs.json"] = canonical(proof)
    assertion = json.loads(contents["inputs/capture-assertion.json"])
    assertion["root_proofs_sha256"] = sha(contents["inputs/root-proofs.json"])
    contents["inputs/capture-assertion.json"] = canonical(assertion)


def test_closed_three_fresh_root_proof_validates_original_immutable_bytes():
    contents, context, native = inputs()
    assert validate_opencode_capture_assertion(contents, context, native) == (True, True)
    assert contents == _snapshot_tree(PACKET)


@pytest.mark.parametrize("change", ["paid_small_model", "personal_home", "preexisting_native", "missing_version", "wrong_resume", "changed_stdout"])
def test_rehashed_launch_proof_cannot_launder_changed_capture_semantics(change):
    contents, context, native = inputs()
    path = "inputs/root-proof/1/plan.json"; plan = json.loads(contents[path])
    if change == "paid_small_model":
        config = json.loads(plan["environment"]["OPENCODE_CONFIG_CONTENT"]); config["small_model"] = "opencode/paid"
        plan["environment"]["OPENCODE_CONFIG_CONTENT"] = json.dumps(config); refresh(contents, path, plan)
    elif change == "personal_home":
        plan["environment"]["HOME"] = "/Users/personal"; refresh(contents, path, plan)
    elif change == "preexisting_native":
        plan["native_empty_before_preflight"] = False; refresh(contents, path, plan)
    elif change == "missing_version":
        del contents["inputs/root-proof/1/version.stdout.txt"]
    elif change == "wrong_resume":
        path = "inputs/root-proof/1/observer/r2.launch.json"; launch = json.loads(contents[path]); launch["argv"][launch["argv"].index("--session")+1] = "other-session"; refresh(contents, path, launch)
    else:
        path = "inputs/root-proof/1/observer/r1.stdout.jsonl"; contents[path] += b"\n"
    with pytest.raises(ValueError): validate_opencode_capture_assertion(contents, context, native)


def test_three_root_metric_rows_must_match_validated_source_repetitions():
    contents, context, native = inputs(); context["root_repetitions"][0]["personal_history_scanned"] = True
    with pytest.raises(ValueError, match="root repetitions"):
        validate_opencode_capture_assertion(contents, context, native)


def test_fresh_broad_regeneration_is_stable_and_has_only_retained_locator_bindings():
    contents, context, _ = inputs(); instance = json.loads(contents["inputs/workload.json"])
    common = {"observer_document": contents["inputs/observer.json"], "observer": {"id": "closed-replay-observer", "sha256": sha(contents["inputs/observer.json"])},
              "native_manifest": {"id": "native/decode.json", "sha256": sha(contents["native/decode.json"])}}
    first = build_opencode_replay_evidence(PACKET, PACKET / "native", instance, context, common)
    second = build_opencode_replay_evidence(PACKET, PACKET / "native", instance, context, common)
    assert canonical(first) == canonical(second)
    doc = first[2]
    assert doc["native_manifest"] == common["native_manifest"]
    assert doc["profile"]["broad_evidence"]["broad.classified_content_density"]["evidence_complete"]
    assert not any(loc["id"] == "runtime:manifest" for metric in doc["metric_evidence"] for loc in metric["native_locators"])


# --- native projection: final_after chain, helper commands, changed-file hashes ---

import copy
import hashlib

from session_bench.adapters.opencode_decoder import decode_opencode_bundle, read_opencode_event_order
from session_bench.live_metric_comparator import compare_survival_run
from session_bench.opencode_score_inputs import project_opencode_native

LIVE = ROOT / "artifacts/v1-expanded-preparation/opencode-1.18.31-live-v1"
PORTABLE = {"complete_root": True, "companions_present": True, "isolated_decode": True, "canonical_equality": True}


def live(tmp_path, repetition=1):
    """Decode a private copy of one retained capture; observer is used only by the comparator."""
    run = LIVE / f"opencode-1-18-31-eval-{repetition}"
    bundle = tmp_path / f"bundle-{repetition}"; bundle.mkdir()
    for name in ("opencode.db", "opencode.db-wal", "opencode.db-shm"):
        (bundle / name).write_bytes((run / "native-bundle" / name).read_bytes())
    decoded = decode_opencode_bundle(bundle)
    order = read_opencode_event_order(bundle, session_id=decoded["session_id"])
    return decoded, order, json.loads((run / "observer.json").read_text())


def rows(observer, projected, repetition=1):
    measurement = compare_survival_run(observer, projected, PORTABLE, configuration_id="opencode-cli", repetition=repetition)
    return {row["id"]: (row["state"], row["correct"], row["observed_eligible"], row["decoded_eligible"]) for row in measurement["metrics"]}


def final_relations(projected):
    return [item for item in projected["relations"] if item["kind"] == "final_after"]


def action(projected, tool, phase=None):
    return next(item for item in projected["actions"] if item["tool"] == tool
                and (phase is None or f"bench_check.py {phase} " in item["input"].get("command", "")))


@pytest.mark.parametrize("repetition", [1, 2, 3])
def test_native_projection_measures_final_after_actions_and_changed_file(tmp_path, repetition):
    decoded, order, observer = live(tmp_path, repetition)
    before = copy.deepcopy(decoded)
    projected = project_opencode_native(decoded, order)
    assert decoded == before  # the decoder result is not changed in place
    baseline = rows(observer, decoded, repetition)
    assert baseline["revision.final_after_r2"][0] == "native_absent"
    assert baseline["work.actions"] == ("measured", 1, 4, 4)
    assert baseline["work.changed_files"][0] == "native_absent"
    scored = rows(observer, projected, repetition)
    assert scored["revision.final_after_r2"] == ("measured", 1, 1, 1)
    assert scored["work.actions"] == ("measured", 4, 4, 4)
    assert scored["work.changed_files"] == ("measured", 1, 1, 1)
    assert {key: value for key, value in scored.items() if value != baseline[key]}.keys() == {
        "revision.final_after_r2", "work.actions", "work.changed_files"}


def test_final_after_relation_is_native_r2_message_to_native_final_test_part(tmp_path):
    decoded, order, _ = live(tmp_path)
    relation, = final_relations(project_opencode_native(decoded, order))
    assert relation["from_id"] == "msg_0f009426a0014SY6jez0dweo8d"
    assert relation["to_id"] == "prt_0f0096916001SVCionoDFOaCFY"


def test_final_after_is_absent_without_native_event_order_even_if_timestamps_are_ordered(tmp_path):
    decoded, _, _ = live(tmp_path)
    assert final_relations(project_opencode_native(decoded, {"state": "absent", "messages": {}, "parts": {}})) == []
    assert final_relations(project_opencode_native(decoded, None)) == []


@pytest.mark.parametrize("change", ["edit_after_final", "response_before_final_result", "r2_after_edit", "missing_completion", "id_order_disagrees"])
def test_final_after_is_absent_when_strict_native_order_is_not_proven(tmp_path, change):
    decoded, order, _ = live(tmp_path)
    edit, final, response, r2 = "prt_0f0095fdd001faHjoBOu7MTpss", "prt_0f0096916001SVCionoDFOaCFY", "msg_0f009732f001NJUKFEHw2i2qZH", "msg_0f009426a0014SY6jez0dweo8d"
    if change == "edit_after_final": order["parts"][edit] = {"first": 122, "completed": 123}
    elif change == "response_before_final_result": order["messages"][response] = 121
    elif change == "r2_after_edit": order["messages"][r2] = 105
    elif change == "missing_completion": order["parts"][final]["completed"] = None
    else:
        for item in decoded["actions"]:
            if item["id"] == edit: item["id"] = "prt_zzzz_after_final"
        order["parts"]["prt_zzzz_after_final"] = order["parts"].pop(edit)
    assert final_relations(project_opencode_native(decoded, order)) == []


@pytest.mark.parametrize("member", ["edit", "final_test", "response"])
def test_final_after_is_absent_when_a_chain_member_lacks_the_r2_parent(tmp_path, member):
    decoded, order, _ = live(tmp_path)
    r1 = decoded["turns"][0]["id"]
    if member == "response":
        decoded["responses"][-1]["turn_id"] = r1
    else:
        target = action(decoded, "edit") if member == "edit" else action(decoded, "bash", "final")
        target["turn_id"] = r1
    assert final_relations(project_opencode_native(decoded, order)) == []


def test_final_after_is_absent_when_final_test_did_not_succeed(tmp_path):
    decoded, order, _ = live(tmp_path)
    final = action(decoded, "bash", "final")
    final["exit_code"] = 1
    assert final_relations(project_opencode_native(decoded, order)) == []


def test_helper_commands_are_normalized_from_native_command_text_only(tmp_path):
    decoded, order, _ = live(tmp_path)
    projected = project_opencode_native(decoded, order)
    canary = "SB_SURVIVAL_V1_RUN_opencode-1-18-31-eval-1"
    for phase in ("inspect", "baseline", "final"):
        item = action(projected, "bash", phase)
        assert item["argv"] == ["python3", "bench_check.py", phase, "--run-canary", canary]
        assert item["target"] == "fixture_project/checkout.py"
        assert item["helper_phase"] == phase
    assert "argv" not in action(projected, "edit") and "argv" not in action(projected, "read")


@pytest.mark.parametrize("command,workdir", [
    ("python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_x; rm checkout.py", "/w/fixture_project"),
    ("cat bench_check.py final", "/w/fixture_project"),
    ("python3 other.py final --run-canary SB_SURVIVAL_V1_RUN_x", "/w/fixture_project"),
    ("python3 bench_check.py final", "/w/fixture_project"),
    ("python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_x", "/w/elsewhere"),
    ("python3 bench_check.py final --run-canary SB_SURVIVAL_V1_RUN_x", None),
])
def test_non_helper_or_unbound_commands_are_not_normalized(tmp_path, command, workdir):
    decoded, order, _ = live(tmp_path)
    final = action(decoded, "bash", "final")
    final["input"] = final["arguments"] = {"command": command, **({"workdir": workdir} if workdir else {})}
    final["cwd"] = workdir
    projected = project_opencode_native(decoded, order)
    item = next(row for row in projected["actions"] if row["id"] == final["id"])
    assert "argv" not in item and "helper_phase" not in item and item["target"] is None
    assert final_relations(projected) == []


def test_changed_file_hashes_come_from_native_inspect_output_and_native_edit_input(tmp_path):
    decoded, order, observer = live(tmp_path)
    for event in observer["events"]:  # poison the observer: it must not reach a native fact
        if event["kind"] == "file_change":
            event["fields"]["before_sha256"] = "0" * 64; event["fields"]["after_sha256"] = "1" * 64
    change, = project_opencode_native(decoded, order)["file_changes"]
    edit = action(decoded, "edit")["input"]
    inspect = json.loads(action(decoded, "bash", "inspect")["output"].split(" ", 1)[1])
    source = inspect["checkout_source"]
    assert change["path"] == "fixture_project/checkout.py"
    assert change["before_sha256"] == inspect["checkout_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert change["after_sha256"] == hashlib.sha256(source.replace(edit["oldString"], edit["newString"]).encode()).hexdigest()
    assert change["after_sha256"] == "a020043db82bec2df47204c03e11ab40c5275139e0b122723810304d81e7050f"
    assert rows(observer, project_opencode_native(decoded, order))["work.changed_files"][0] == "contradiction"


def hashes(projected):
    return [(item.get("before_sha256"), item.get("after_sha256")) for item in projected["file_changes"]]


@pytest.mark.parametrize("change", ["ambiguous_old", "missing_old", "inspect_digest_mismatch", "inspect_after_edit", "no_event_order",
                                    "second_edit", "unknown_shell_command", "edit_failed", "outside_session_directory", "two_inspect_sources"])
def test_changed_file_hashes_are_absent_when_a_native_step_is_not_exact(tmp_path, change):
    decoded, order, _ = live(tmp_path)
    edit, inspect = action(decoded, "edit"), action(decoded, "bash", "inspect")
    result = lambda item: next(row for row in decoded["results"] if row["action_id"] == item["id"])
    if change == "ambiguous_old": edit["input"]["oldString"] = "price"
    elif change == "missing_old": edit["input"]["oldString"] = "not in the file"
    elif change == "inspect_digest_mismatch":
        inspect["output"] = inspect["output"].replace("sum(price", "sum( price"); result(inspect)["output"] = inspect["output"]
    elif change == "inspect_after_edit": order["parts"][inspect["id"]] = {"first": 109, "completed": 110}
    elif change == "no_event_order": order = None
    elif change == "second_edit":
        other = copy.deepcopy(edit); other["id"] = "prt_second_edit"; decoded["actions"].append(other)
    elif change == "unknown_shell_command":
        baseline = action(decoded, "bash", "baseline")
        baseline["input"] = baseline["arguments"] = {"command": "sed -i '' s/5/6/ checkout.py", "workdir": baseline["input"]["workdir"]}
    elif change == "edit_failed": decoded["results"].remove(result(edit))
    elif change == "outside_session_directory": edit["input"]["filePath"] = "/elsewhere/fixture_project/checkout.py"
    else:
        other = copy.deepcopy(inspect); other["id"] = "prt_other_inspect"
        payload = {"checkout_source": "x = 1\n", "checkout_sha256": hashlib.sha256(b"x = 1\n").hexdigest()}
        other["output"] = "SB_SURVIVAL_V1_HELPER_INSPECT_n " + json.dumps(payload); decoded["actions"].append(other)
        order["parts"]["prt_other_inspect"] = {"first": 50, "completed": 54}
    assert hashes(project_opencode_native(decoded, order)) == [(None, None)]


def test_changed_file_supports_the_native_replace_all_flag(tmp_path):
    decoded, order, _ = live(tmp_path)
    edit = action(decoded, "edit")
    edit["input"].update(oldString="price", newString="cost", replaceAll=True)
    source = json.loads(action(decoded, "bash", "inspect")["output"].split(" ", 1)[1])["checkout_source"]
    assert source.count("price") > 1
    change, = project_opencode_native(decoded, order)["file_changes"]
    assert change["after_sha256"] == hashlib.sha256(source.replace("price", "cost").encode()).hexdigest()
    edit["input"]["replaceAll"] = "yes"  # not a native boolean
    assert hashes(project_opencode_native(decoded, order)) == [(None, None)]


def test_replay_evidence_returns_the_native_projection_and_keeps_the_decoder_result():
    contents, context, _ = inputs(); instance = json.loads(contents["inputs/workload.json"])
    common = {"observer_document": contents["inputs/observer.json"], "observer": {"id": "closed-replay-observer", "sha256": sha(contents["inputs/observer.json"])},
              "native_manifest": {"id": "native/decode.json", "sha256": sha(contents["native/decode.json"])}}
    decoded, projected, _ = build_opencode_replay_evidence(PACKET, PACKET / "native", instance, context, common)
    assert final_relations(decoded) == [] and len(final_relations(projected)) == 1
    assert all(item.get("before_sha256") for item in projected["file_changes"])
