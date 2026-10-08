import json
from pathlib import Path
import shutil

import pytest

from session_bench import opencode_expanded_capture as capture

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / "artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction"
SESSION = "ses_f6c184ee6ffe3jZXsnk8LxRS1F"


@pytest.fixture
def prepared(tmp_path):
    destination = tmp_path / "new-eval-1"
    plan = capture.prepare_expanded_opencode_capture(destination, repository=ROOT, repetition=1)
    yield destination, plan
    shutil.rmtree(plan["scratch"])


def test_fresh_plan_has_no_git_answers_account_or_payment_environment(prepared, monkeypatch):
    destination, plan = prepared
    workspace = Path(plan["workspace"])
    assert ROOT not in workspace.parents
    assert {p.name for p in (workspace / "fixture_project").iterdir()} == {"bench_check.py", "checkout.py"}
    assert plan["native_empty_before_preflight"] and plan["home_xdg_empty_before_preflight"]
    assert plan["config"]["model"] == plan["config"]["small_model"] == capture.MODEL
    assert plan["config"]["share"] == "disabled" and plan["config"]["plugin"] == []
    assert not plan["auth_copied"]
    assert not any("KEY" in key or "TOKEN" in key for key in plan["environment"])
    with pytest.raises(ValueError, match="exists"):
        capture.prepare_expanded_opencode_capture(destination, repository=ROOT, repetition=1)


@pytest.mark.parametrize("change", ["model", "small_model", "HOME", "OPENCODE_DB", "API_KEY"])
def test_tampered_route_refused_before_submission(prepared, change):
    destination, plan = prepared
    if change == "model": plan["model"] = "paid/model"
    elif change == "small_model": plan["config"][change] = "paid/model"
    elif change == "API_KEY": plan["environment"]["OPENAI_API_KEY"] = "should not propagate"
    else: plan["environment"][change] = str(ROOT)
    (destination / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError):
        capture.execute_expanded_opencode_capture(destination, runner=lambda *a, **k: pytest.fail("model invoked"))


def test_changed_version_stops_before_model(prepared):
    destination, _ = prepared
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: "1.18.30", runner=lambda *a, **k: pytest.fail("model invoked"))
    assert result["status"] == "invalid" and result["model_submissions"] == 0


@pytest.mark.parametrize("error", ["Authentication required", "Quota exceeded", "Network error", "Invalid API key"])
def test_first_turn_runtime_stop_preserved_without_r2_or_retry(prepared, error):
    destination, plan = prepared
    calls = []
    def run(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(argv); assert timeout == 180
        stdout.write_text(""); stderr.write_text(error); return 1
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: capture.VERSION, runner=run)
    assert result["status"] == "invalid" and len(calls) == 1
    assert (destination / "observer/r1.launch.json").is_file()
    assert (destination / "observer/r1.exit.json").is_file()
    assert not (destination / "observer/r2.launch.json").exists()
    with pytest.raises(ValueError, match="single-use"):
        capture.execute_expanded_opencode_capture(destination, runner=run)


def fake_success_runner(plan, *, create_family=True):
    calls = []
    def run(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(argv); turn = len(calls)
        assert env["HOME"] == plan["environment"]["HOME"] and "--pure" in argv
        if turn == 2: assert argv[argv.index("--session") + 1] == SESSION
        canary = "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂" if turn == 1 else "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
        stdout.write_text(json.dumps({"type": "text", "sessionID": SESSION, "part": {"text": "done " + canary}}, ensure_ascii=False) + "\n"
                          + json.dumps({"type": "step_finish", "sessionID": SESSION, "part": {"reason": "stop"}}) + "\n")
        stderr.write_text("")
        fixture = cwd / "fixture_project"
        ledger = [{"phase": "inspect", "exit_code": 0}, {"phase": "baseline", "exit_code": 1}]
        if turn == 2:
            ledger.append({"phase": "final", "exit_code": 0})
            (fixture / "checkout.py").write_text("def checkout(items):\n    subtotal=sum(p*q for p,q in items)\n    return subtotal+(0 if subtotal>=50 else 5)\n")
            if create_family:
                for member in (RETAINED / "native-bundle").iterdir():
                    shutil.copyfile(member, Path(env["OPENCODE_DB"]).parent / member.name)
        (fixture / ".survival-observer.jsonl").write_text("".join(json.dumps(row) + "\n" for row in ledger))
        return 0
    return run, calls


def test_full_capture_preserves_family_bytes_and_both_exact_launch_proofs(prepared, monkeypatch):
    destination, plan = prepared
    # Acquisition test supplies synthetic observer projection; native SQLite
    # inventory, hashes, singleton binding and decoder are actually exercised.
    monkeypatch.setattr(capture, "build_opencode_live_observer", lambda **kwargs: {"independent": True, "streams": sorted(kwargs["stdout_by_turn"])})
    run, calls = fake_success_runner(plan)
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: capture.VERSION, runner=run)
    assert result["status"] == "captured_pending_qualification" and len(calls) == 2
    assert result["score_eligible"] is False
    for source in (RETAINED / "native-bundle").iterdir():
        assert source.read_bytes() == (destination / "native-bundle" / source.name).read_bytes()
    for turn in (1, 2):
        launch = json.loads((destination / f"observer/r{turn}.launch.json").read_bytes())
        assert launch["environment"] == plan["environment"] and launch["auth_copied"] is False


def test_missing_native_family_cannot_be_successful_capture(prepared):
    destination, plan = prepared
    run, calls = fake_success_runner(plan, create_family=False)
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: capture.VERSION, runner=run)
    assert result["status"] == "invalid" and len(calls) == 2
    assert not (destination / "native-manifest.json").exists()


def test_helper_mutation_stops_before_r2(prepared):
    destination, plan = prepared
    base, calls = fake_success_runner(plan)
    def run(*args, **kwargs):
        code = base(*args, **kwargs)
        (kwargs["cwd"] / "fixture_project/bench_check.py").write_text("modified")
        return code
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: capture.VERSION, runner=run)
    assert result["status"] == "invalid" and len(calls) == 1


def test_canary_without_completion_boundary_does_not_start_r2(prepared):
    destination, plan = prepared
    base, calls = fake_success_runner(plan)
    def run(*args, **kwargs):
        code = base(*args, **kwargs)
        lines = kwargs["stdout"].read_text().splitlines()
        kwargs["stdout"].write_text(lines[0] + "\n")
        return code
    result = capture.execute_expanded_opencode_capture(destination, preflight=lambda *a: capture.VERSION, runner=run)
    assert result["status"] == "invalid" and len(calls) == 1


def test_batch_never_prepares_more_than_three_or_continues_after_failure(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("expanded_capture_script", ROOT / "scripts/run_expanded_opencode_captures.py")
    script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)
    monkeypatch.setattr(script, "ROOT", tmp_path)
    (tmp_path / "artifacts/v1-expanded-preparation").mkdir(parents=True)
    calls = []
    def prepare(destination, **kwargs):
        assert "1.18.31" not in destination.name  # run IDs satisfy workload slug rules
        calls.append(kwargs["repetition"])
    monkeypatch.setattr(script, "prepare_expanded_opencode_capture", prepare)
    monkeypatch.setattr(script, "execute_expanded_opencode_capture", lambda p: {"attempt_id": p.name, "status": "invalid", "model_submissions": 1, "reason_ids": ["quota"]})
    monkeypatch.setattr("sys.argv", ["run", "--execute"])
    assert script.main() == 1 and calls == [1]
    calls.clear()
    monkeypatch.setattr("sys.argv", ["run"])
    assert script.main() == 0 and calls == [1, 2, 3]
