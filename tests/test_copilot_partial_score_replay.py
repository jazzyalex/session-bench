"""The Copilot successor keeps independent populations and missing proof apart."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from session_bench.score_replay import replay_score_package, verify_score_packet_tamper_controls
from session_bench.copilot_live import decode_copilot_native_bytes
from session_bench.copilot_score_inputs import observer_from_capture_documents, _add_native_final_after_chain, _add_native_snapshot_change


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("copilot_successor", ROOT / "scripts/build_copilot_partial_score_replays.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_copilot_partial_replay_has_exact_rows_loss_control_and_usage_guard(tmp_path):
    output = tmp_path / "successor"
    summary = builder.build(output)
    assert summary["overall_rank"] is None
    assert [row["resolved_metric_count"] for row in summary["runs"]] == [17, 17, 18]
    for index, suffix in enumerate(("01", "03", "06")):
        run = summary["runs"][index]
        packet = output / run["packet"]
        digest = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
        receipt = replay_score_package(packet, expected_manifest_sha256=digest)
        intact = receipt["diagnostics"]["intact"]
        loss = receipt["diagnostics"]["selected_loss"]
        assert len(intact["metrics"]) == len({row["id"] for row in intact["metrics"]}) == 31
        assert intact["score_diagnostics"]["overall"] is None
        assert intact["score_diagnostics"]["rankable"] is False
        assert loss["observer_denominator_unchanged"] and loss["response_correctness_reduced"]
        metrics = {row["id"]: row for row in intact["metrics"]}
        assert metrics["work.submitted_turns"]["observed_eligible"] == 2
        assert metrics["work.visible_responses"]["observed_eligible"] == 2
        assert metrics["revision.final_after_r2"]["correct"] == 1
        if suffix == "06":
            assert metrics["work.changed_files"]["correct"] == 1
        else:
            assert metrics["work.changed_files"]["state"] == "unresolved"
        for metric in ("attribution.usage", "attribution.token_semantics", "attribution.reconciliation"):
            assert metrics[metric]["state"] == "unresolved"
        for metric in ("portable.complete_root", "portable.companions"):
            assert metrics[metric]["state"] == "unresolved"
    packet = output / summary["runs"][2]["packet"]
    pin = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
    assert verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)["status"] == "passed"


def test_copilot_packet_rejects_captured_stdout_change_before_execution(tmp_path, monkeypatch):
    output = tmp_path / "successor"
    builder.build(output)
    packet = output / "copilot-2026-09-29-06"
    pin = hashlib.sha256((packet / "manifest.json").read_bytes()).hexdigest()
    stream = packet / "inputs/capture/capture/r2.stdout"
    stream.write_bytes(stream.read_bytes() + b"\n")
    monkeypatch.setattr("session_bench.score_replay.subprocess.run", lambda *args, **kwargs: pytest.fail("unverified packet code ran"))
    with pytest.raises(ValueError, match="inventory mismatch"):
        replay_score_package(packet, expected_manifest_sha256=pin)


def test_copilot_rejects_duplicate_independent_and_native_completions():
    capture = ROOT / "artifacts/v1-expanded-preparation/live-captures/copilot-2026-09-29-06"
    documents = {name: (capture / name).read_bytes() for name in builder.CAPTURE_DOCUMENTS}
    rows = [json.loads(line) for line in documents["capture/r2.stdout"].splitlines() if line]
    completion = next(row for row in rows if row.get("type") == "tool.execution_complete")
    rows.append(completion)
    documents["capture/r2.stdout"] = b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows)
    with pytest.raises(ValueError, match="duplicate stdout completion"):
        observer_from_capture_documents(documents)

    attempt = json.loads(documents["attempt.json"])
    native = (capture / "native" / attempt["session_id"] / "events.jsonl").read_bytes()
    native_rows = [json.loads(line) for line in native.splitlines() if line]
    native_completion = next(row for row in native_rows if row.get("type") == "tool.execution_complete")
    native_rows.append(native_completion)
    duplicate = b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in native_rows)
    with pytest.raises(ValueError, match="duplicate native tool completion"):
        decode_copilot_native_bytes(duplicate)


@pytest.mark.parametrize("mutation", ["broken_parent", "failed_edit", "failed_final", "missing_response", "reordered_result"])
def test_copilot_final_after_requires_entire_successful_native_chain(mutation):
    capture = ROOT / "artifacts/v1-expanded-preparation/live-captures/copilot-2026-09-29-06"
    attempt = json.loads((capture / "attempt.json").read_bytes())
    workload = json.loads((capture / "workload_instance.json").read_bytes())
    native = (capture / "native" / attempt["session_id"] / "events.jsonl").read_bytes()
    decoded = decode_copilot_native_bytes(native)
    edit = next(a for a in decoded["actions"] if a.get("name") == "apply_patch")
    final = next(a for a in decoded["actions"] if a.get("action_kind") == "test" and "final" in a.get("argv", []))
    if mutation == "broken_parent":
        decoded["records"][final["sequence"] - 1]["raw"]["parentId"] = "unrelated-event"
    elif mutation == "failed_edit":
        next(r for r in decoded["results"] if r["call_id"] == edit["call_id"])["status"] = "failure"
    elif mutation == "failed_final":
        next(r for r in decoded["results"] if r["call_id"] == final["call_id"])["exit_code"] = 1
    elif mutation == "missing_response":
        decoded["responses"] = [r for r in decoded["responses"] if r.get("canary") != workload["turns"][1]["response_canary"]]
    else:
        next(r for r in decoded["results"] if r["call_id"] == edit["call_id"])["sequence"] = final["sequence"] + 1
    _add_native_final_after_chain(decoded, workload)
    assert not any(r["kind"] == "final_after" for r in decoded["relations"])


@pytest.mark.parametrize("mutation", ["missing_postimage", "unjoined_event", "wrong_path", "bad_backup", "failed_edit", "broken_parent"])
def test_copilot_snapshot_change_requires_native_join_and_digest_proof(tmp_path, mutation):
    packet = ROOT / "artifacts/v1-expanded-preparation/copilot-partial-score-replays-v3/copilot-2026-09-29-06"
    decoded = decode_copilot_native_bytes((packet / "native/events.jsonl").read_bytes())
    index = json.loads((packet / "native/rewind-file-snapshots/index.json").read_bytes())
    snapshot = index["snapshots"][0]
    image = next(iter(snapshot["files"].values()))
    backup_name = image["preimage"]["backupFile"]
    backup = (packet / "native/rewind-file-snapshots/backups" / backup_name).read_bytes()
    if mutation == "missing_postimage":
        del image["postimage"]
    elif mutation == "unjoined_event":
        snapshot["eventId"] = "unrelated-turn"
    elif mutation == "wrong_path":
        image["postimage"]["resolvedPath"] = "/unrelated/checkout.py"
    elif mutation == "bad_backup":
        backup += b"\n"
    else:
        edit = next(a for a in decoded["actions"] if a.get("name") == "apply_patch")
        if mutation == "failed_edit":
            next(r for r in decoded["results"] if r["call_id"] == edit["call_id"])["status"] = "failure"
        else:
            decoded["records"][edit["sequence"] - 1]["raw"]["parentId"] = "unrelated-event"
    target = tmp_path / "rewind-file-snapshots"
    (target / "backups").mkdir(parents=True)
    (target / "index.json").write_text(json.dumps(index))
    (target / "backups" / backup_name).write_bytes(backup)
    _add_native_snapshot_change(decoded, tmp_path)
    assert decoded["file_changes"] == []


def test_copilot_snapshot_postimage_is_not_borrowed_from_observer(tmp_path):
    from session_bench.live_metric_comparator import compare_survival_run
    packet = ROOT / "artifacts/v1-expanded-preparation/copilot-partial-score-replays-v3/copilot-2026-09-29-06"
    decoded = decode_copilot_native_bytes((packet / "native/events.jsonl").read_bytes())
    index = json.loads((packet / "native/rewind-file-snapshots/index.json").read_bytes())
    image = next(iter(index["snapshots"][0]["files"].values()))
    image["postimage"]["contentHash"] = "0" * 64
    target = tmp_path / "rewind-file-snapshots"
    (target / "backups").mkdir(parents=True)
    (target / "index.json").write_text(json.dumps(index))
    backup_name = image["preimage"]["backupFile"]
    (target / "backups" / backup_name).write_bytes((packet / "native/rewind-file-snapshots/backups" / backup_name).read_bytes())
    _add_native_snapshot_change(decoded, tmp_path)
    assert decoded["file_changes"][0]["after_sha256"] == "0" * 64
    observer = json.loads((packet / "inputs/observer.json").read_bytes())
    compared = compare_survival_run(observer, decoded, {}, configuration_id="copilot", repetition=3)
    metric = next(row for row in compared["metrics"] if row["id"] == "work.changed_files")
    assert metric["state"] == "contradiction"
    assert metric["correct"] == 0
