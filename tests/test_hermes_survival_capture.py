from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from session_bench.hermes_state_evidence import verify_hermes_state_capture
from session_bench.hermes_survival_capture import (
    HermesCaptureError,
    continue_hermes_capture_after_r1,
    execute_hermes_capture,
    prepare_hermes_capture,
)


REPO = Path(__file__).resolve().parents[1]
SID = "20260929_123456_abcdef123456"
OTHER = "20260901_090000_fedcba654321"


def _repo_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    workload_root = repository / "fixtures/scenarios/survival-v1/workload"
    workload_root.parent.mkdir(parents=True)
    shutil.copytree(REPO / "fixtures/scenarios/survival-v1/workload", workload_root)
    return repository


def _hermes_home(tmp_path: Path) -> Path:
    """A synthetic Hermes home: one older session, the shared store, shared files, an install checkout."""
    home = tmp_path / "hermes-home"
    for name, data in ((f"sessions/session_{OTHER}.json", b"private old session"), ("logs/agent.log", b"old log\n"),
                       (".hermes_history", b"old history\n"), ("auth.json", b"private token"),
                       ("hermes-agent/hermes_cli/main.py", b"# install\n")):
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_bytes(data)
    connection = sqlite3.connect(home / "state.db")
    connection.executescript(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, model TEXT);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id), role TEXT, content TEXT);")
    connection.execute("INSERT INTO sessions VALUES (?, 'old-model')", (OTHER,))
    connection.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', 'other private prompt')", (OTHER,))
    connection.commit()
    connection.close()
    return home


def _hermes_writes(home: Path, prompt: str, turn: int) -> None:
    """What one Hermes turn leaves in its home."""
    connection = sqlite3.connect(home / "state.db")
    connection.execute("INSERT OR IGNORE INTO sessions VALUES (?, 'gpt-5.5')", (SID,))
    connection.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'user', ?)", (SID, prompt))
    connection.execute("INSERT INTO messages (session_id, role, content) VALUES (?, 'assistant', ?)", (SID, f"ack {turn}"))
    connection.commit()
    connection.close()
    (home / f"sessions/session_{SID}.json").write_text(json.dumps({"id": SID, "turns": turn}))
    with (home / "logs/agent.log").open("a") as log:
        log.write(f"turn {turn}\n")
    with (home / ".hermes_history").open("a") as history:
        history.write(f"turn {turn}\n")


_SETTLE = {"interval_seconds": 0, "sleep": lambda _seconds: None}


def _prepared(tmp_path: Path):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/hermes-controller-test"
    destination.parent.mkdir(parents=True)
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    source = tmp_path / "hermes-source"
    (source / "hermes_cli").mkdir(parents=True)
    (source / "hermes_cli/main.py").write_text("# offline fixture\n")
    plan = prepare_hermes_capture(destination, repository=repository, repetition=1,
                                  python=python, source_root=source, hermes_home=_hermes_home(tmp_path))
    return repository, destination, plan


def _stream(text, *, session=SID, model="gpt-5.5", exit_code=0):
    """A minimal ``stream-json`` stdout: init, one text delta, result."""
    rows = [{"type": "system", "subtype": "init", "model": model, "session_id": session},
            {"type": "text", "text": text},
            {"type": "result", "session_id": session, "exit_code": exit_code, "text": text,
             "tokens": {"input": 1, "output": 1, "total": 2, "cache_read": 0, "cache_write": 0}, "duration_ms": 5}]
    return "".join(json.dumps({**row, "timestamp": 1791347136000 + index}, ensure_ascii=False) + "\n" for index, row in enumerate(rows))


def _fake_runner(home=None, extra=None):
    calls = []
    prompts = []

    def runner(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(list(argv))
        if "-q" in argv:
            turn = 1 if "RESPONSE_R1" in argv[-1] else 2
            prompt = argv[-1]
            prompts.append(prompt)
            if home is not None:
                _hermes_writes(home, prompt, turn)
                if extra is not None:
                    extra(home, turn)
            fixture = cwd / "fixture_project"
            if turn == 1:
                for phase in ("inspect", "baseline"):
                    subprocess.run(["python3", "bench_check.py", phase], cwd=fixture, env=dict(env),
                                   capture_output=True, check=False)
            else:
                (fixture / "checkout.py").write_text(
                    "def checkout(items):\n"
                    "    subtotal = sum(price * quantity for price, quantity in items)\n"
                    "    return subtotal + (0 if subtotal >= 50 else 5)\n")
                subprocess.run(["python3", "bench_check.py", "final"], cwd=fixture, env=dict(env),
                               capture_output=True, check=False)
            canary = "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂" if turn == 1 else "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"
            stdout.write_text(_stream(f"Done. {canary}"))
            stderr.write_text(f"\nsession_id: {SID}\n")
            return 0

        session_id = argv[argv.index("--session-id") + 1]
        assert session_id == SID
        output = Path(argv[-1])
        messages = []
        for index, prompt in enumerate(prompts):
            messages.extend([{"role": "user", "content": prompt},
                             {"role": "assistant", "content": f"ack {index}"}])
        output.write_text(json.dumps({"session_id": SID, "messages": messages}) + "\n")
        stdout.write_text(f"Exported 1 session to {output}\n")
        stderr.write_text("")
        return 0

    return runner, calls


def _ready_preflight(_plan, _env):
    return {"ready": True, "version": "Hermes Agent v0.21.5-test", "returncode": 0}


def test_hermes_capture_resumes_exact_session_and_copies_full_evidence(tmp_path):
    _, destination, plan = _prepared(tmp_path)
    runner, calls = _fake_runner(Path(plan["hermes_home"]))
    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=runner, state_settle=_SETTLE)
    assert result["status"] == "captured_pending_qualification", result
    assert result["model_submissions"] == 2
    assert result["session_id"] == SID
    assert plan["configuration_id"] == "hermes"
    assert plan["no_session_list"] is True
    assert len([call for call in calls if "-q" in call]) == 2
    first = next(call for call in calls if "-q" in call and "RESPONSE_R1" in call[-1])
    second = next(call for call in calls if "-q" in call and "RESPONSE_R2" in call[-1])
    assert second[second.index("--resume") + 1] == SID and "--resume" not in first
    assert "--ignore-user-config" in second and "--ignore-rules" in second and "-z" not in second and "--usage-file" not in second
    # The file tools stay enabled, the stdout is the event stream, and the approval gate is answered by the documented flag.
    assert second[second.index("--toolsets") + 1] == "terminal,file" and second[second.index("--format") + 1] == "stream-json"
    assert "--yolo" in second and second[second.index("chat") - 1] == plan["entrypoint_code"] and second[-2] == "-q"
    assert plan["source_root"] not in second and repr(plan["source_root"]) in plan["entrypoint_code"]
    assert plan["toolsets"] == ["terminal", "file"] and plan["approval"] == "--yolo" and plan["stdout_format"] == "stream-json"
    assert plan["schema_version"] == "session-bench-hermes-survival-capture-v2"
    for turn in (1, 2):
        root = destination / f"turn-r{turn}"
        assert (root / "native/session.jsonl").is_file()
        receipt = json.loads((root / "stream-receipt.json").read_text())
        assert receipt["session_id"] == SID and receipt["model"] == "gpt-5.5" and receipt["exit_code"] == 0
        assert json.loads((root / "launch.json").read_text())["toolsets"] == ["terminal", "file"]
        assert (root / "workspace/fixture_project/.survival-observer.jsonl").is_file()
        assert (root / "workspace/fixture_project/snapshots/checkout.before.py").is_file()
        assert (root / "stdout.jsonl").is_file() and (root / "stderr.txt").is_file() and not (root / "usage.json").exists()
        # The exporter file stays, labelled as a derived projection.
        derived = json.loads((root / "native/receipt.json").read_text())
        assert derived["derived"] is True and derived["native_record"] is False
        # The native record: whole-home receipt, session files, session rows of the store.
        receipt = json.loads((destination / f"r{turn}-native-receipt.json").read_text())
        before = json.loads((destination / "state-before.json").read_text())
        after = json.loads((destination / f"r{turn}-state-after.json").read_text())
        assert verify_hermes_state_capture(receipt, destination / f"r{turn}-native", before=before, after=after) is True
        assert receipt["schema_version"] == "hermes-state-root-v1" and receipt["session_id"] == SID and receipt["turn"] == turn
        assert receipt["build"] == "Hermes Agent v0.21.5-test" and receipt["attempt_id"] == destination.name
        assert receipt["classes"]["session_owned"] == [f"sessions/session_{SID}.json"]
        rows = (destination / f"r{turn}-native/session-store-rows.json").read_text()
        assert OTHER not in rows and "other private prompt" not in rows
        messages = next(table for table in json.loads(rows)["stores"][0]["tables"] if table["table"] == "messages")
        assert len(messages["rows"]) == 2 * turn
        assert not (destination / f".r{turn}-native-store-copy").exists()
        assert result["turns"][turn - 1]["native_session_store_rows"] == 2 * turn + 1
    assert "whole Hermes home" in result["native_scope"] and "exporter" in result["derived_scope"]
    assert not list(destination.glob("r*-refusal.json"))


def test_hermes_unexplained_home_change_stops_after_r1_with_a_refusal_listing(tmp_path):
    _, destination, plan = _prepared(tmp_path)

    def unknown_file(home, _turn):
        (home / "pastes").mkdir(exist_ok=True)
        (home / "pastes/unknown.bin").write_bytes(b"?")

    runner, calls = _fake_runner(Path(plan["hermes_home"]), unknown_file)
    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=runner, state_settle=_SETTLE)
    assert result["status"] == "capture_incomplete" and result["model_submissions"] == 1
    assert "refused" in result["failure"] and "pastes/unknown.bin" in result["failure"]
    assert len(calls) == 1      # no second turn, no exporter run, no retry
    refusal = json.loads((destination / "r1-refusal.json").read_text())
    assert {row["relative_path"]: row["change"] for row in refusal["unexplained"]} == {"pastes": "new", "pastes/unknown.bin": "new"}
    assert refusal["session_id"] == SID and refusal["turn"] == 1 and "RULES" in refusal["how_to_extend"]
    assert (destination / "r1-state-refused.json").is_file() and (destination / "state-before.json").is_file()
    assert not (destination / "r1-native").exists() and not (destination / "r1-native-receipt.json").exists()
    assert not (destination / ".r1-native-store-copy").exists()


def test_hermes_session_missing_from_store_stops_with_a_refusal(tmp_path):
    _, destination, plan = _prepared(tmp_path)

    def forget(home, _turn):
        connection = sqlite3.connect(home / "state.db")
        connection.execute("DELETE FROM messages WHERE session_id = ?", (SID,))
        connection.execute("DELETE FROM sessions WHERE id = ?", (SID,))
        connection.commit()
        connection.close()

    runner, calls = _fake_runner(Path(plan["hermes_home"]), forget)
    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=runner, state_settle=_SETTLE)
    assert result["status"] == "capture_incomplete" and "in no row of state.db" in result["failure"]
    assert len(calls) == 1 and json.loads((destination / "r1-refusal.json").read_text())["unexplained"] == []
    assert not (destination / "r1-native").exists() and not (destination / ".r1-native-store-copy").exists()


@pytest.mark.parametrize("stream, reason", [
    (lambda text: _stream(text, model="other-model"), "pinned model"),
    (lambda text: _stream(text, exit_code=1), "not a complete successful turn"),
    (lambda text: _stream(text).replace('"type": "result"', '"type": "surprise"'), "not a complete successful turn"),
    (lambda text: _stream(text) + "not json\n", "not a complete successful turn"),
    (lambda text: _stream(text.replace("SB_SURVIVAL_V1_RESPONSE", "SB_OTHER")), "lacks the exact canary"),
    (lambda text: "".join(_stream(text).splitlines(keepends=True)[:-1]), "not a complete successful turn"),
])
def test_hermes_stream_identity_or_completion_failure_stops_before_r2(tmp_path, stream, reason):
    _, destination, plan = _prepared(tmp_path)
    runner, calls = _fake_runner(Path(plan["hermes_home"]))

    def changed(argv, *, cwd, env, stdout, stderr, timeout):
        code = runner(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
        if "-q" in argv:
            stdout.write_text(stream(json.loads(stdout.read_text().splitlines()[-1])["text"]))
        return code

    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=changed)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 1
    assert len([call for call in calls if "-q" in call]) == 1
    assert reason in result["failure"]


def test_hermes_second_turn_must_report_the_session_of_the_first(tmp_path):
    _, destination, plan = _prepared(tmp_path)
    runner, calls = _fake_runner(Path(plan["hermes_home"]))

    def other_session(argv, *, cwd, env, stdout, stderr, timeout):
        code = runner(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
        if "-q" in argv and "--resume" in argv:
            stdout.write_text(stdout.read_text().replace(SID, OTHER))
        return code

    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=other_session, state_settle=_SETTLE)
    assert result["status"] == "capture_incomplete" and result["model_submissions"] == 2
    assert "resumed a different session" in result["failure"]


def test_hermes_version_preflight_failure_submits_nothing(tmp_path):
    _, destination, _ = _prepared(tmp_path)
    runner, calls = _fake_runner()
    result = execute_hermes_capture(
        destination,
        preflight=lambda _plan, _env: {"ready": False, "version": ""},
        runner=runner,
    )
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 0
    assert calls == []


def test_hermes_auth_failure_preserves_stream_and_workspace_before_export(tmp_path):
    _, destination, _ = _prepared(tmp_path)
    calls = []

    def auth_failure(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(list(argv))
        stdout.write_text("")
        stderr.write_text("OAuth refresh failed (401)\n")
        return 1

    result = execute_hermes_capture(destination, preflight=_ready_preflight, runner=auth_failure)
    assert result["status"] == "capture_incomplete"
    assert result["model_submissions"] == 1
    assert len(calls) == 1
    prefix = destination / "turn-r1"
    assert (prefix / "stdout.jsonl").is_file()
    assert (prefix / "workspace/fixture_project/snapshots/checkout.before.py").is_file()
    assert not (prefix / "native/session.jsonl").exists()
    assert "no retry or fallback" in result["failure"]


def test_hermes_decoder_correction_continues_exact_preserved_r1_without_rewriting_it(tmp_path):
    _, destination, plan = _prepared(tmp_path)
    workload = json.loads((destination / "workload-instance.json").read_text())
    r1 = destination / "turn-r1"
    native_dir = r1 / "native"
    native_dir.mkdir()
    (r1 / "workspace/fixture_project").parent.mkdir(parents=True)
    shutil.copytree(Path(plan["fixture"]), r1 / "workspace/fixture_project")
    fixture = r1 / "workspace/fixture_project"
    env = {"SB_SURVIVAL_V1_RUN_CANARY": workload["run_canary"]}
    for phase in ("inspect", "baseline"):
        subprocess.run(["python3", "bench_check.py", phase], cwd=fixture, env=env,
                       capture_output=True, check=False)
    live_ledger = Path(plan["fixture"]) / ".survival-observer.jsonl"
    live_ledger.write_bytes((fixture / ".survival-observer.jsonl").read_bytes())
    r1_stdout = f"Observed baseline. {workload['turns'][0]['response_canary']}\n"
    (r1 / "stdout.txt").write_text(r1_stdout)
    (r1 / "stderr.txt").write_text("")
    (r1 / "exit.json").write_text(json.dumps({"returncode": 0}) + "\n")
    usage = {"session_id": SID, "provider": "openai-codex", "model": "gpt-5.5",
             "api_calls": 2, "completed": True}
    (r1 / "usage.json").write_text(json.dumps(usage) + "\n")
    native = {"id": SID, "messages": [
        {"role": "user", "content": workload["turns"][0]["text"]},
        {"role": "assistant", "content": r1_stdout.strip()},
    ]}
    r1_native = (json.dumps(native, ensure_ascii=False) + "\n").encode()
    (native_dir / "session.jsonl").write_bytes(r1_native)
    failed_receipt = {"status": "capture_incomplete", "failure": "hermes: expected session_id and messages fields",
                      "model_submissions": 1, "session_id": SID, "scratch_retained": True}
    (destination / "capture-result.json").write_text(json.dumps(failed_receipt) + "\n")
    old_capture_result = (destination / "capture-result.json").read_bytes()
    old_r1_sha = __import__("hashlib").sha256(r1_native).hexdigest()

    calls = []
    def continuation_runner(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append(list(argv))
        if "-z" in argv:
            assert argv[argv.index("--resume") + 1] == SID
            project = cwd / "fixture_project"
            (project / "checkout.py").write_text(
                "def checkout(items):\n"
                "    subtotal = sum(price * quantity for price, quantity in items)\n"
                "    return subtotal + (0 if subtotal >= 50 else 5)\n")
            subprocess.run(["python3", "bench_check.py", "final"], cwd=project, env=dict(env),
                           capture_output=True, check=False)
            usage_path = Path(argv[argv.index("--usage-file") + 1])
            usage_path.write_text(json.dumps({**usage, "api_calls": 1}) + "\n")
            stdout.write_text(f"Fixed checkout. {workload['turns'][1]['response_canary']}\n")
            stderr.write_text("")
            return 0
        output = Path(argv[-1])
        output.write_text(json.dumps({"id": SID, "messages": native["messages"] + [
            {"role": "user", "content": workload["turns"][1]["text"]},
            {"role": "assistant", "content": workload["turns"][1]["response_canary"]},
        ]}, ensure_ascii=False) + "\n")
        stdout.write_text(f"Exported 1 session to {output}\n")
        stderr.write_text("")
        return 0

    result = continue_hermes_capture_after_r1(destination, preflight=_ready_preflight,
                                              runner=continuation_runner)
    assert result["status"] == "captured_pending_qualification", result
    assert result["model_submissions_before_continuation"] == 1
    assert result["model_submissions_in_continuation"] == 1
    assert len([call for call in calls if "-z" in call]) == 1
    assert (destination / "capture-result.json").read_bytes() == old_capture_result
    assert __import__("hashlib").sha256((r1 / "native/session.jsonl").read_bytes()).hexdigest() == old_r1_sha
    receipt = json.loads((destination / "continuation-receipt.json").read_text())
    assert receipt["r1_native_sha256"] == old_r1_sha
    assert (destination / "turn-r2/native/session.jsonl").is_file()


def test_hermes_rejects_home_override_before_capture(tmp_path, monkeypatch):
    repository = _repo_fixture(tmp_path)
    destination = repository / "artifacts/v1-expanded-preparation/live-captures/hermes-home-test"
    destination.parent.mkdir(parents=True)
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    source = tmp_path / "source"
    (source / "hermes_cli").mkdir(parents=True)
    (source / "hermes_cli/main.py").touch()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "must-not-be-used"))
    with pytest.raises(HermesCaptureError, match="HERMES_HOME override"):
        prepare_hermes_capture(destination, repository=repository, repetition=1,
                               python=python, source_root=source, hermes_home=_hermes_home(tmp_path))
