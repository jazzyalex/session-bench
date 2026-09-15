import json
from pathlib import Path

import pytest

from session_bench.workload_instance import RUN_CANARY_ENV, instantiate_workload


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "scenarios" / "survival-v1" / "workload" / "workload.json"


def test_instantiation_changes_only_run_identity_and_prompt_canary() -> None:
    source = json.loads(TEMPLATE.read_text())
    value, environment = instantiate_workload(source, "codex-cli-eval-2")
    canary = "SB_SURVIVAL_V1_RUN_codex-cli-eval-2"
    assert value["run_id"] == "codex-cli-eval-2"
    assert value["run_canary"] == canary
    assert environment == {RUN_CANARY_ENV: canary}
    assert all(turn["run_canary"] == canary and canary in turn["text"] for turn in value["turns"])
    assert value["actions"] == source["actions"]
    assert value["results"] == source["results"]
    assert value["relations"] == source["relations"]
    assert value["filesystem"] == source["filesystem"]
    assert source["run_canary"] == "SB_SURVIVAL_V1_RUN_fixture_0001"


@pytest.mark.parametrize("slug", ["", "bad slug", "../escape", "emoji-🙂"])
def test_instantiation_rejects_unsafe_run_slug(slug: str) -> None:
    with pytest.raises(ValueError, match="run_slug"):
        instantiate_workload(json.loads(TEMPLATE.read_text()), slug)


def test_instantiated_prompts_bind_each_helper_to_dynamic_run_canary() -> None:
    source = json.loads(TEMPLATE.read_text())
    value, _ = instantiate_workload(source, "codex-cli-eval-2")
    canary = "SB_SURVIVAL_V1_RUN_codex-cli-eval-2"
    by_id = {turn["id"]: turn for turn in value["turns"]}
    assert f"python3 bench_check.py inspect --run-canary {canary}" in by_id["turn-r1"]["text"]
    assert f"python3 bench_check.py baseline --run-canary {canary}" in by_id["turn-r1"]["text"]
    assert f"python3 bench_check.py final --run-canary {canary}" in by_id["turn-r2"]["text"]
    assert "SB_SURVIVAL_V1_RUN_fixture_0001" not in by_id["turn-r1"]["text"]
    assert "SB_SURVIVAL_V1_RUN_fixture_0001" not in by_id["turn-r2"]["text"]
    assert value["actions"] == source["actions"]
    for action in value["actions"]:
        for token in action.get("argv", []):
            assert token != "--run-canary"
    assert all("--run-canary" not in " ".join(action.get("argv", [])) for action in value["actions"])
