import hashlib
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from scripts import finalize_claude_desktop_runs as finalizer
from session_bench.claude_desktop_hook_observer import observe_hook
from session_bench.claude_desktop_gui_event_clock import EVENT_ORDER, record_gui_event
from session_bench.workload_instance import instantiate_workload


@pytest.fixture
def receipt(tmp_path):
    workspace = tmp_path / "workspace"
    fixture = workspace / "fixture_project"
    fixture.mkdir(parents=True)
    (fixture / "checkout.py").write_text("synthetic\n")
    path = tmp_path / "hooks.jsonl"
    context = dict(run_id="run-1", session_id="session-1", workspace=workspace,
                   fixture_root=fixture, receipts_path=path,
                   clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    for kind in ("PreToolUse", "PostToolUse"):
        event = dict(hook_event_name=kind, session_id="session-1", cwd=str(workspace),
                     tool_use_id="tool-1", tool_name="Read",
                     tool_input={"file_path": "fixture_project/checkout.py"})
        if kind == "PostToolUse":
            event["tool_response"] = "synthetic"
        observe_hook(json.dumps(event).encode(), **context)
    return path, dict(run_id="run-1", session_id="session-1", workspace=str(workspace), fixture_root=fixture)


def rewrite(path, change):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    change(rows)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_hook_validation_retains_exact_bytes_and_separate_clock(receipt):
    path, identity = receipt
    raw, validated = finalizer._validate_hook_receipt(path, **identity)
    assert raw == path.read_bytes()
    assert validated["receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert validated["completed_tool_count"] == 1
    assert validated["timestamp_provenance"] == "local_hook_receipt_clock"
    assert validated["native_source_time_observed"] is False
    assert validated["score_eligible"] is False


@pytest.mark.parametrize("change", [
    {"run_id": "other"}, {"session_id": "other"}, {"workspace": "/other"},
    {"fixture_relative_target": "../checkout.py"},
    {"fixture_relative_target": "/checkout.py"},
    {"fixture_relative_target": "./checkout.py"},
    {"fixture_relative_target": "missing.py"},
    {"timestamp_provenance": "native_transcript"},
    {"hook_observed_at": "2026-10-01T00:00:00"},
    {"result_sha256": "bad"}, {"duration_ms": True}, {"tool_name": ""},
    {"unsupported_raw_response": "private"},
])
def test_malformed_receipts_fail_closed(receipt, change):
    path, identity = receipt
    rewrite(path, lambda rows: rows[-1].update(change))
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_hook_receipt(path, **identity)


@pytest.mark.parametrize("change", [
    lambda rows: rows.reverse(),
    lambda rows: rows.append(rows[-1].copy()),
    lambda rows: rows.pop(),
    lambda rows: rows[-1].update(hook_observed_at="2026-09-30T00:00:00Z"),
    lambda rows: rows[-1].update(tool_name="Edit"),
])
def test_lifecycle_and_clock_order_fail_closed(receipt, change):
    path, identity = receipt
    rewrite(path, change)
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_hook_receipt(path, **identity)


def test_nonprivate_symlink_duplicate_keys_and_truncation_fail_closed(receipt):
    path, identity = receipt
    original = path.read_bytes()
    path.chmod(0o644)
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_hook_receipt(path, **identity)
    path.chmod(0o600)
    link = path.with_name("link.jsonl")
    link.symlink_to(path)
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_hook_receipt(link, **identity)
    for raw in (original[:-1], b"", b'{"schema":"one","schema":"two"}\n'):
        path.write_bytes(raw)
        with pytest.raises(finalizer.FinalizeError):
            finalizer._validate_hook_receipt(path, **identity)


def test_symlink_fixture_path_is_rejected(receipt):
    path, identity = receipt
    target = identity["fixture_root"] / "checkout.py"
    target.unlink()
    target.symlink_to(path)
    with pytest.raises(finalizer.FinalizeError, match="symlink"):
        finalizer._validate_hook_receipt(path, **identity)


def test_command_failure_receipt_is_supplemental(receipt):
    path, identity = receipt
    def changes(rows):
        for row in rows:
            row.pop("fixture_relative_target")
            row["command_sha256"] = hashlib.sha256(b"synthetic command").hexdigest()
        rows[-1].update(event_type="PostToolUseFailure", result_status="failure")
    rewrite(path, changes)
    _, validated = finalizer._validate_hook_receipt(path, **identity)
    assert validated["completed_tool_count"] == 1
    assert validated["score_eligible"] is False


def test_capture_time_projection_is_digest_bound_and_never_stores_raw_command(receipt, tmp_path):
    _, identity = receipt
    workspace = Path(identity["workspace"])
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    command = f"python3 bench_check.py inspect --run-canary {canary}"
    hooks = tmp_path / "capture-hooks.jsonl"
    projections = tmp_path / "capture-projections.jsonl"
    context = {
        "run_id": "run-1", "session_id": "session-1",
        "workspace": workspace, "fixture_root": identity["fixture_root"],
        "receipts_path": hooks, "projection_receipts_path": projections,
        "run_canary": canary, "clock": lambda: datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    for kind in ("PreToolUse", "PostToolUse"):
        value = dict(hook_event_name=kind, session_id="session-1",
                     cwd=str(workspace), tool_use_id="call-1",
                     tool_name="Bash", tool_input={"command": command})
        if kind == "PostToolUse":
            value["tool_response"] = {"exit_code": 0}
        observe_hook(json.dumps(value).encode(), **context)
    raw_hooks, _ = finalizer._validate_hook_receipt(
        hooks, run_id="run-1", session_id="session-1",
        workspace=str(workspace), fixture_root=identity["fixture_root"],
    )
    raw_projection, merged = finalizer._validate_command_projection_receipt(
        projections, raw_hooks=raw_hooks, run_id="run-1", session_id="session-1",
        workspace=str(workspace), run_canary=canary,
    )
    assert command.encode() not in raw_projection
    assert len(merged) == 1
    assert merged[0]["schema"] == "claude-desktop-command-projection-v2"
    assert merged[0]["command_sha256"] == hashlib.sha256(command.encode()).hexdigest()
    assert merged[0]["helper_phases"] == [{
        "phase": "inspect", "argv": ["python3", "bench_check.py", "inspect"],
        "run_canary": canary,
    }]


def test_projection_receipt_mutation_cannot_change_hook_command_identity(receipt, tmp_path):
    _, identity = receipt
    workspace = Path(identity["workspace"])
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    command = f"python3 bench_check.py inspect --run-canary {canary}"
    hooks = tmp_path / "capture-hooks.jsonl"
    projections = tmp_path / "capture-projections.jsonl"
    context = {
        "run_id": "run-1", "session_id": "session-1",
        "workspace": workspace, "fixture_root": identity["fixture_root"],
        "receipts_path": hooks, "projection_receipts_path": projections,
        "run_canary": canary, "clock": lambda: datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    for kind in ("PreToolUse", "PostToolUse"):
        value = dict(hook_event_name=kind, session_id="session-1",
                     cwd=str(workspace), tool_use_id="call-1",
                     tool_name="Bash", tool_input={"command": command})
        if kind == "PostToolUse":
            value["tool_response"] = {"exit_code": 0}
        observe_hook(json.dumps(value).encode(), **context)
    raw_hooks, _ = finalizer._validate_hook_receipt(
        hooks, run_id="run-1", session_id="session-1",
        workspace=str(workspace), fixture_root=identity["fixture_root"],
    )
    rows = [json.loads(line) for line in projections.read_text().splitlines()]
    rows[0]["command_sha256"] = "0" * 64
    projections.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(finalizer.FinalizeError, match="digest"):
        finalizer._validate_command_projection_receipt(
            projections, raw_hooks=raw_hooks, run_id="run-1", session_id="session-1",
            workspace=str(workspace), run_canary=canary,
        )


@pytest.fixture
def projected_bash(tmp_path):
    workspace = tmp_path / "workspace"
    fixture = workspace / "fixture_project"
    fixture.mkdir(parents=True)
    (fixture / "checkout.py").write_text("synthetic\n")
    hooks = tmp_path / "hooks.jsonl"
    projections = tmp_path / "projections.jsonl"
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    context = {
        "run_id": "run-1", "session_id": "session-1", "workspace": workspace,
        "fixture_root": fixture, "receipts_path": hooks,
        "projection_receipts_path": projections, "run_canary": canary,
        "clock": lambda: datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    for kind in ("PreToolUse", "PostToolUse"):
        event = dict(hook_event_name=kind, session_id="session-1", cwd=str(workspace),
                     tool_use_id="call-1", tool_name="Bash",
                     tool_input={"command": f"python3 bench_check.py inspect --run-canary {canary}"})
        if kind == "PostToolUse":
            event["tool_response"] = {"exit_code": 0}
        observe_hook(json.dumps(event).encode(), **context)
    raw_hooks, _ = finalizer._validate_hook_receipt(
        hooks, run_id="run-1", session_id="session-1",
        workspace=str(workspace), fixture_root=fixture,
    )
    kwargs = dict(raw_hooks=raw_hooks, run_id="run-1", session_id="session-1",
                  workspace=str(workspace), run_canary=canary)
    return hooks, projections, kwargs


@pytest.mark.parametrize("change", [
    {"action_kind": "test"},
    {"argv": ["python3", "bench_check.py", "final"]},
    {"target": None},
    {"cwd": "other"},
    {"compound_edit": True, "compound_edit_target": "fixture_project/checkout.py"},
    {"helper_phases": [{"phase": "final", "argv": ["python3", "bench_check.py", "final"],
                         "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}]},
    {"helper_phases": []},
    {"unsupported_reason": "ignored"},
    {"new_semantic_field": "ignored"},
])
def test_projected_semantic_tampering_fails_with_unchanged_command_digest(projected_bash, change):
    _, projections, kwargs = projected_bash
    original_digest = json.loads(projections.read_text().splitlines()[0])["command_sha256"]
    rewrite(projections, lambda rows: rows[0].update(change))
    assert json.loads(projections.read_text().splitlines()[0])["command_sha256"] == original_digest
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_command_projection_receipt(projections, **kwargs)


def test_projection_requires_capture_time_semantic_binding(projected_bash):
    hooks, projections, kwargs = projected_bash
    rewrite(hooks, lambda rows: (rows[0].pop("command_projection_schema"),
                                  rows[0].pop("command_projection_sha256")))
    raw_hooks, _ = finalizer._validate_hook_receipt(
        hooks, run_id="run-1", session_id="session-1", workspace=kwargs["workspace"],
        fixture_root=hooks.parent / "workspace/fixture_project",
    )
    kwargs["raw_hooks"] = raw_hooks
    with pytest.raises(finalizer.FinalizeError, match="digest"):
        finalizer._validate_command_projection_receipt(projections, **kwargs)


@pytest.mark.parametrize("change", [
    {"action_kind": "inspect"},
    {"argv": ["python3", "bench_check.py", "final"]},
    {"target": "fixture_project/other.py"},
    {"cwd": "other"},
    {"compound_edit": True, "compound_edit_target": "fixture_project/checkout.py"},
    {"helper_phases": [{"phase": "final", "argv": ["python3", "bench_check.py", "final"],
                         "run_canary": "SB_SURVIVAL_V1_RUN_run-1"}]},
    {"command_sha256": "0" * 64},
])
def test_edit_projection_rejects_semantic_tampering(tmp_path, change):
    workspace = tmp_path / "workspace"
    fixture = workspace / "fixture_project"
    fixture.mkdir(parents=True)
    (fixture / "checkout.py").write_text("synthetic\n")
    hooks, projections = tmp_path / "hooks.jsonl", tmp_path / "projections.jsonl"
    canary = "SB_SURVIVAL_V1_RUN_run-1"
    context = dict(run_id="run-1", session_id="session-1", workspace=workspace,
                   fixture_root=fixture, receipts_path=hooks,
                   projection_receipts_path=projections, run_canary=canary,
                   clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    for kind in ("PreToolUse", "PostToolUse"):
        event = dict(hook_event_name=kind, session_id="session-1", cwd=str(workspace),
                     tool_use_id="call-1", tool_name="Edit",
                     tool_input={"file_path": "fixture_project/checkout.py"})
        if kind == "PostToolUse":
            event["tool_response"] = {"success": True}
        observe_hook(json.dumps(event).encode(), **context)
    raw_hooks, _ = finalizer._validate_hook_receipt(
        hooks, run_id="run-1", session_id="session-1", workspace=str(workspace),
        fixture_root=fixture,
    )
    rewrite(projections, lambda rows: rows[0].update(change))
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_command_projection_receipt(
            projections, raw_hooks=raw_hooks, run_id="run-1", session_id="session-1",
            workspace=str(workspace), run_canary=canary,
        )


def test_source_locations_bind_exact_selected_paths_and_digests(tmp_path):
    transcript = tmp_path / "selected.jsonl"
    desktop = tmp_path / "selected.json"
    transcript.write_bytes(b"synthetic transcript\n")
    desktop.write_bytes(b"{}\n")
    receipt = finalizer._source_location_receipt(transcript, desktop)
    assert receipt["provenance"] == "caller-selected source path"
    assert [source["resolved_path"] for source in receipt["sources"]] == [str(transcript.resolve()), str(desktop.resolve())]
    assert [source["sha256"] for source in receipt["sources"]] == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (transcript, desktop)]
    assert receipt["location_metric_resolved"] is False
    assert receipt["root_completeness_proven"] is False
    assert receipt["source_root_scanned"] is False


def test_otel_receipt_rejects_unselected_response_content(tmp_path):
    path = tmp_path / "otel-usage.json"
    path.write_text(json.dumps({
        "capture_scope": "single synthetic Claude Desktop Code (Local) run",
        "collector": {
            "listener": "127.0.0.1",
            "raw_prompt_content_persisted": False,
            "raw_response_content_persisted": False,
            "raw_api_body_content_persisted": False,
            "unselected_attributes_persisted": False,
        },
        "events": [{"kind": "assistant_response", "response": "private response text"}],
    }))
    path.chmod(0o600)
    with pytest.raises(finalizer.FinalizeError, match="unselected event fields"):
        finalizer._validate_otel_usage_receipt(
            path, run_id="run-1", session_id="session-1",
            workload={}, gui_receipt={},
        )


@pytest.mark.parametrize("supply_hooks,supply_discovery,supply_gui_clock,supply_otel", [
    (False, False, False, False),
    (False, True, False, False),
    (True, False, False, False),
    (True, True, False, False),
    (True, True, True, False),
    (True, True, True, True),
])
def test_finalizer_preserves_optional_receipts_without_score_claims(tmp_path, monkeypatch, supply_hooks, supply_discovery, supply_gui_clock, supply_otel):
    original_repo = finalizer.REPO
    repo = tmp_path / "repo"
    scenario = Path("fixtures/scenarios/survival-v1/workload")
    shutil.copytree(original_repo / scenario, repo / scenario)
    decoder = Path("session_bench/adapters/claude_code_decoder.py")
    (repo / decoder).parent.mkdir(parents=True)
    shutil.copyfile(original_repo / decoder, repo / decoder)
    monkeypatch.setattr(finalizer, "REPO", repo)
    run_id = "offline-hooks"
    run_root = repo / "artifacts/survival-v1-runs" / run_id
    fixture = run_root / "final-project-private"
    shutil.copytree(repo / scenario / "fixture_project", fixture)
    (fixture / ".survival-observer.jsonl").unlink(missing_ok=True)
    workload, _ = instantiate_workload(json.loads((repo / scenario / "workload.json").read_text()), run_id)
    (fixture / "snapshots").mkdir(exist_ok=True)
    shutil.copyfile(fixture / "checkout.py", fixture / "snapshots/checkout.before.py")
    for phase in ("inspect", "baseline", "final"):
        if phase == "final":
            (fixture / "checkout.py").write_text("def checkout(items):\n    subtotal = sum(price * quantity for price, quantity in items)\n    return subtotal + (5 if subtotal < 50 else 0)\n")
        result = subprocess.run([sys.executable, str(fixture / "bench_check.py"), phase, "--run-canary", workload["run_canary"]], capture_output=True)
        assert result.returncode == (1 if phase == "baseline" else 0)
    transcript = tmp_path / "selected.jsonl"
    rows = []
    for ordinal, turn in enumerate(workload["turns"], 1):
        rows.extend([
            {"type": "user", "uuid": f"u{ordinal}", "sessionId": "session-1", "timestamp": f"2026-09-01T00:00:0{ordinal}Z", "message": {"role": "user", "content": turn["text"]}},
            {"type": "assistant", "uuid": f"a{ordinal}", "sessionId": "session-1", "timestamp": f"2026-09-01T00:00:0{ordinal + 2}Z", "message": {"role": "assistant", "content": [{"type": "text", "text": turn["response_canary"]}]}},
        ])
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
    workspace = tmp_path / "original-workspace"
    desktop = tmp_path / "desktop.json"
    desktop.write_text(json.dumps(dict(cliSessionId="session-1", bridgeSessionIds=["bridge"], sessionId="local_test", cwd=str(workspace), model="synthetic", toolSurfaceSnapshot={"cliVersion": "test"})))
    gui = tmp_path / "gui.json"
    gui.write_text(json.dumps({"run_id": run_id, "observations": {"r1_canary_visible": workload["turns"][0]["response_canary"], "r2_canary_visible": workload["turns"][1]["response_canary"], "r1_response_boundary_visible": True, "r2_response_boundary_visible": True, "edit_visible": True, "final_table_rows": 3}}))
    hooks = tmp_path / "hooks.jsonl"
    if supply_hooks:
        independent = finalizer._build_observer(
            workload=workload, session_id="session-1", model="synthetic",
            helper_raw=finalizer._helper_ledger(fixture, run_canary=workload["run_canary"])[0],
            helper=finalizer._helper_ledger(fixture, run_canary=workload["run_canary"])[1],
            gui=json.loads(gui.read_text()), before_sha="a" * 64, after_sha="b" * 64,
            run_id=run_id, repetition=1,
        )
        hooks.write_bytes(hook_bytes(independent, workspace=str(workspace)))
        hooks.chmod(0o600)
    discovery = tmp_path / "discovery.json"
    if supply_discovery:
        discovery.write_text(json.dumps(discovery_document(transcript, desktop, run_id=run_id)))
        monkeypatch.setattr(finalizer, "canonical_claude_desktop_roots", lambda: {
            "transcript": transcript.parent, "desktop_metadata": desktop.parent,
        })
    gui_clock = tmp_path / "gui-event-clock.jsonl"
    if supply_gui_clock:
        for index, event_id in enumerate(EVENT_ORDER):
            record_gui_event(
                run_id=run_id, event_id=event_id,
                event_kind={"turn-r1": "user_turn", "response-r1": "assistant_response",
                            "turn-r2": "user_turn", "response-r2": "assistant_response"}[event_id],
                ledger_path=gui_clock,
                clock=lambda index=index: datetime(2026, 10, 2, 12, 0, index, tzinfo=timezone.utc),
            )
    otel_receipt = tmp_path / "otel-usage.json"
    if supply_otel:
        events = []
        for turn_number, turn in enumerate(workload["turns"], 1):
            request_id = f"request-{turn_number}"
            prompt_id = f"prompt-{turn_number}"
            usage = {
                "input_tokens": 100 + turn_number,
                "output_tokens": 30 + turn_number,
                "cache_read_tokens": 0,
                "cache_write_tokens": 5 + turn_number,
            }
            if turn_number == 1:
                events.append({
                    "kind": "api_request", "session_id": "session-1",
                    "prompt_id": prompt_id, "request_id": "request-1-tool",
                    "model": "synthetic", "query_source": None, "effort": "medium",
                    "event_sequence": 0,
                    "event_timestamp": "2026-10-02T12:00:00Z",
                    "usage": {
                        "input_tokens": 7, "output_tokens": 3,
                        "cache_read_tokens": 2, "cache_write_tokens": 1,
                    },
                })
            events.extend([
                {
                    "kind": "api_request", "session_id": "session-1",
                    "prompt_id": prompt_id, "request_id": request_id,
                    "model": "synthetic", "query_source": None, "effort": "medium",
                    "event_sequence": turn_number * 2 - 1,
                    "event_timestamp": f"2026-10-02T12:00:0{turn_number}Z",
                    "usage": usage,
                },
                {
                    "kind": "assistant_response", "session_id": "session-1",
                    "prompt_id": prompt_id, "request_id": request_id,
                    "model": "synthetic", "query_source": None, "effort": "medium",
                    "event_sequence": turn_number * 2,
                    "event_timestamp": f"2026-10-02T12:00:0{turn_number + 1}Z",
                    "response_sha256": "c" * 64,
                    "response_length": 100,
                    "matched_response_canaries": [turn["response_canary"]],
                    "message_uuid": None,
                },
            ])
        otel_receipt.write_text(json.dumps({
            "schema_version": "session-bench-claude-desktop-otel-usage-v1",
            "run_id": run_id,
            "capture_scope": "single synthetic Claude Desktop Code (Local) run",
            "collector": {
                "listener": "127.0.0.1",
                "raw_prompt_content_persisted": False,
                "raw_response_content_persisted": False,
                "raw_api_body_content_persisted": False,
                "unselected_attributes_persisted": False,
            },
            "events": events,
        }))
        otel_receipt.chmod(0o600)
    result = finalizer.finalize(
        run_id=run_id, repetition=1, transcript=transcript, desktop_metadata=desktop,
        gui_receipt=gui, hook_receipt=hooks if supply_hooks else None,
        source_discovery_receipt=discovery if supply_discovery else None,
        gui_event_clock_receipt=gui_clock if supply_gui_clock else None,
        otel_usage_receipt=otel_receipt if supply_otel else None,
    )
    output = run_root / result["output"]
    assert result["score_eligible"] is False
    attempt = json.loads((output / "attempt-finalized.json").read_text())
    assert attempt["state"] == "captured_unscored"
    assert attempt["hook_receipt"]["supplied"] is supply_hooks
    observer = json.loads((output / "observer.json").read_text())
    assert observer["hook_receipt"] == attempt["hook_receipt"]
    assert all("hook_observed_at" not in event.get("fields", {}) for event in observer["events"])
    assert result["measurement_states"]["work.actions"] == "native_absent"
    assert result["format_states"]["broad.stable_root_location"] == ("measured" if supply_discovery else "unresolved")
    if supply_otel:
        receipt_binding = attempt["otel_usage_receipt"]
        copied_receipt = output / receipt_binding["path"]
        assert copied_receipt.read_bytes() == otel_receipt.read_bytes()
        assert copied_receipt.stat().st_mode & 0o077 == 0
        assert receipt_binding["sha256"] == hashlib.sha256(copied_receipt.read_bytes()).hexdigest()
        responses = [event for event in observer["events"] if event["kind"] == "assistant_response"]
        assert [event["fields"]["usage_request_id"] for event in responses] == ["request-1", "request-2"]
        assert [event["fields"]["usage_request_ids"] for event in responses] == [
            ["request-1-tool", "request-1"], ["request-2"],
        ]
        assert [event["fields"]["usage_request_count"] for event in responses] == [2, 1]
        assert [event["fields"]["usage"] for event in responses] == [
            {"input_tokens": 108, "output_tokens": 34, "cache_read_tokens": 2, "cache_write_tokens": 7},
            {"input_tokens": 102, "output_tokens": 32, "cache_read_tokens": 0, "cache_write_tokens": 7},
        ]
        assert all("response" not in row for row in json.loads(copied_receipt.read_text())["events"])
    hashes = {row["path"]: row["sha256"] for row in json.loads((output / "artifact-hashes.json").read_text())["artifacts"]}
    location = json.loads((output / "source-location-private.json").read_text())
    if supply_discovery:
        copied = output / "source-discovery-private.json"
        assert copied.read_bytes() == discovery.read_bytes()
        assert copied.stat().st_mode & 0o077 == 0
        binding = {"path": copied.name, "sha256": hashes[copied.name]}
        assert location["source_discovery_receipt"] == binding
        assert json.loads((output / "native-manifest.json").read_text())["source_discovery_receipt"] == binding
        assert location["location_metric_resolved"] is True
    else:
        assert "source_discovery_receipt" not in location
    assert location["sources"][0]["resolved_path"] == str(transcript.resolve())
    assert (output / "source-location-private.json").stat().st_mode & 0o077 == 0
    assert attempt["source_location_receipt"]["sha256"] == hashes["source-location-private.json"]
    if supply_hooks:
        timestamp = json.loads((output / "timestamp-observer.json").read_text())
        stripped = copy.deepcopy(timestamp)
        for event in stripped["events"]:
            if event["kind"] in {"action", "result", "file_change"}:
                assert event["fields"].pop("call_id").startswith("independent-")
            if supply_gui_clock and "observed_at" in event["fields"]:
                assert event["fields"].pop("observed_at")
                assert event["fields"].pop("timestamp_provenance")
        assert stripped == observer
        format_doc = json.loads((output / "format-evidence.json").read_text())
        assert format_doc["observer"] == {"id": "timestamp-observer.json", "sha256": hashes["timestamp-observer.json"]}
        times = format_doc["profile"]["broad_evidence"]["broad.event_timestamps"]
        assert times["evidence_complete"] is True
        # Only native user/assistant records survive in this fixture. The
        # observer clock is supplementary; score timestamps stay native.
        assert len(times["event_ids"]) == 13
        assert len(times["records"]) < len(times["event_ids"])
        assert all(row["timestamp"] not in {"2026-10-01T00:00:00.000000Z", "2026-10-02T12:00:00.000000Z"} for row in times["records"])
        assert (output / "hook-receipt-private.jsonl").read_bytes() == hooks.read_bytes()
        assert (output / "hook-receipt-private.jsonl").stat().st_mode & 0o077 == 0
        assert attempt["hook_receipt"]["sha256"] == hashes["hook-receipt-private.jsonl"]
        if supply_gui_clock:
            copied_clock = output / "gui-event-clock-private.jsonl"
            clock_binding = {"path": copied_clock.name, "sha256": hashes[copied_clock.name]}
            assert copied_clock.read_bytes() == gui_clock.read_bytes()
            assert copied_clock.stat().st_mode & 0o077 == 0
            assert json.loads((output / "native-manifest.json").read_text())["gui_event_clock_receipt"]["sha256"] == clock_binding["sha256"]
            assert json.loads((output / "replay-receipt.json").read_text())["gui_event_clock_receipt"]["sha256"] == clock_binding["sha256"]
            assert attempt["gui_event_clock_receipt"]["sha256"] == clock_binding["sha256"]
            assert all(
                "observed_at" in event["fields"]
                for event in json.loads((output / "timestamp-observer.json").read_text())["events"]
                if event["population_role"] == "primary_scored" and event["kind"] in {"user_turn", "assistant_response", "action", "result", "file_change"}
            )
    else:
        assert attempt["hook_receipt"]["state"] == "missing_unscored"
        assert not (output / "hook-receipt-private.jsonl").exists()


def hook_bytes(observer, workspace="/synthetic"):
    rows = []
    for index, action in enumerate(row for row in observer["events"] if row["kind"] == "action"):
        fields = action["fields"]
        result = next(row for row in observer["events"] if row["kind"] == "result" and row["fields"]["action_id"] == action["id"])
        status = result["fields"]["status"]
        target = ({"command_sha256": hashlib.sha256(fields["input"]["command"].encode()).hexdigest()}
                  if "command" in fields["input"] else {"fixture_relative_target": "checkout.py"})
        for kind in ("PreToolUse", "PostToolUse" if status == "success" else "PostToolUseFailure"):
            rows.append({"schema": "claude-desktop-hook-observer-v1", "run_id": observer["run_id"],
                         "session_id": action["session_id"], "workspace": workspace,
                         "tool_use_id": f"independent-{index}", "tool_name": fields["name"], **target,
                         "event_type": kind, "hook_observed_at": "2026-10-01T00:00:00.000000Z",
                         "timestamp_provenance": "local_hook_receipt_clock",
                         "result_status": "pending" if kind == "PreToolUse" else status,
                         "result_sha256": None if kind == "PreToolUse" else "a" * 64})
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


@pytest.fixture
def identity_observer():
    events = [
        {"id": "edit", "kind": "action", "fields": {"name": "Edit", "input": {"file_path": "fixture_project/checkout.py"}, "target": "fixture_project/checkout.py"}},
        {"id": "edit-result", "kind": "result", "fields": {"action_id": "edit", "status": "success", "output": "independent output"}},
        {"id": "change", "kind": "file_change", "fields": {"action_id": "edit", "path": "fixture_project/checkout.py", "before_sha256": "a" * 64, "after_sha256": "b" * 64}},
        {"id": "check", "kind": "action", "fields": {"name": "Bash", "input": {"command": "python3 bench_check.py final"}, "target": "fixture_project/checkout.py"}},
        {"id": "check-result", "kind": "result", "fields": {"action_id": "check", "status": "success"}},
    ]
    for event in events:
        event["session_id"] = "session-1"
    return {"run_id": "run-1", "events": events}


def test_identity_bridge_only_adds_independent_call_ids(identity_observer):
    original = copy.deepcopy(identity_observer)
    enriched = finalizer._hook_timestamp_observer(identity_observer, hook_bytes(identity_observer))
    assert identity_observer == original
    assert [row["fields"]["call_id"] for row in enriched["events"]] == ["independent-0"] * 3 + ["independent-1"] * 2
    for row in enriched["events"]:
        row["fields"].pop("call_id")
    assert enriched == original


@pytest.mark.parametrize("mutation", [
    lambda value: value["events"].append({**copy.deepcopy(value["events"][0]), "id": "duplicate-edit"}),
    lambda value: value["events"][0]["fields"].update(name="Read"),
    lambda value: value["events"][3]["fields"]["input"].update(command="python3  bench_check.py final"),
    lambda value: value["events"][1]["fields"].update(status="failure"),
    lambda value: value["events"][1]["fields"].update(action_id="unknown"),
    lambda value: value["events"][2]["fields"].update(action_id="check"),
    lambda value: value["events"][2]["fields"].update(path="fixture_project/other.py"),
    lambda value: value["events"][0]["fields"].update(call_id="borrowed"),
    lambda value: value["events"][0].update(session_id="other"),
])
def test_identity_bridge_rejects_ambiguous_or_conflicting_evidence(identity_observer, mutation):
    raw = hook_bytes(identity_observer)
    mutation(identity_observer)
    with pytest.raises(finalizer.FinalizeError):
        finalizer._hook_timestamp_observer(identity_observer, raw)


def test_identity_bridge_rejects_incomplete_lifecycle(identity_observer):
    raw = hook_bytes(identity_observer)
    with pytest.raises(finalizer.FinalizeError, match="incomplete"):
        finalizer._hook_timestamp_observer(identity_observer, b"\n".join(raw.splitlines()[:-1]) + b"\n")


def test_completed_bash_lifecycle_allows_expected_baseline_failure(identity_observer):
    baseline = identity_observer["events"][3]
    baseline["fields"]["input"]["command"] = "python3 bench_check.py baseline"
    result = identity_observer["events"][4]
    result["fields"].update(status="failure", exit_code=1)
    rows = [json.loads(line) for line in hook_bytes(identity_observer).splitlines()]
    for row in rows:
        if row["event_type"] == "PostToolUseFailure":
            row.update(event_type="PostToolUse", result_status="success")
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    bound = finalizer._hook_timestamp_observer(identity_observer, raw)
    assert bound["events"][4]["fields"] == {**result["fields"], "call_id": "independent-1"}
    assert bound["events"][3]["fields"]["call_id"] == "independent-1"


@pytest.mark.parametrize("exit_code", [None, 0, True, "1"])
def test_completed_bash_failure_needs_independent_nonzero_exit(identity_observer, exit_code):
    raw = hook_bytes(identity_observer)
    identity_observer["events"][4]["fields"].update(status="failure", exit_code=exit_code)
    with pytest.raises(finalizer.FinalizeError, match="lifecycle"):
        finalizer._hook_timestamp_observer(identity_observer, raw)


def test_failed_tool_lifecycle_rejects_successful_observer(identity_observer):
    rows = [json.loads(line) for line in hook_bytes(identity_observer).splitlines()]
    rows[-1].update(event_type="PostToolUseFailure", result_status="failure")
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    with pytest.raises(finalizer.FinalizeError, match="lifecycle"):
        finalizer._hook_timestamp_observer(identity_observer, raw)


def sign_discovery(value):
    body = {key: val for key, val in value.items() if key != "proof_sha256"}
    value["proof_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return value


def discovery_document(transcript, desktop, run_id="run-1"):
    artifacts, roots = [], []
    for role, root_id, path in (("transcript", "claude-projects", transcript), ("desktop_metadata", "claude-desktop-sessions", desktop)):
        info = path.stat()
        root_hash = hashlib.sha256(str(path.parent.resolve()).encode()).hexdigest()
        artifacts.append(dict(role=role, source_path=str(path.resolve()), relative_path=path.name,
                              source_root_sha256=root_hash, filesystem_id_sha256="f" * 64,
                              device=info.st_dev, inode=info.st_ino, size_bytes=info.st_size,
                              ctime_ns=info.st_ctime_ns, mtime_ns=info.st_mtime_ns,
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        roots.append(dict(root_id=root_id, source_root_sha256=root_hash, filesystem_id_sha256="f" * 64,
                          before_entry_count=0, after_entry_count=1, unrelated_entry_count=0,
                          unrelated_inventory_sha256=hashlib.sha256(b"[]").hexdigest()))
    return sign_discovery(dict(schema_version="session-bench-claude-desktop-source-discovery-v1",
                              run_id=run_id, repetition=1, cli_session_id="session-1", desktop_session_id="local_test",
                              metadata_only_discovery=True, personal_history_content_read=False,
                              unrelated_content_read=False, complete_inventories=True, isolated_pair=True,
                              selected_artifacts=artifacts, root_summaries=roots))


@pytest.fixture
def discovery_validation(tmp_path):
    transcript = tmp_path / "claude/projects/session.jsonl"
    desktop = tmp_path / "Library/Application Support/Claude/claude-code-sessions/session.json"
    transcript.parent.mkdir(parents=True)
    desktop.parent.mkdir(parents=True)
    transcript.write_bytes(b"synthetic transcript\n")
    desktop.write_bytes(b"{}\n")
    family = tmp_path / "family"
    (family / "transcript").mkdir(parents=True)
    (family / "desktop").mkdir()
    shutil.copyfile(transcript, family / "transcript/session.jsonl")
    shutil.copyfile(desktop, family / "desktop/session.json")
    return tmp_path / "discovery.json", discovery_document(transcript, desktop), dict(
        run_id="run-1", repetition=1, cli_session_id="session-1", desktop_session_id="local_test",
        source_location=finalizer._source_location_receipt(transcript, desktop), family_package=family,
        expected_roots={"transcript": transcript.parent, "desktop_metadata": desktop.parent},
    )


def test_discovery_rejects_consistently_resigned_noncanonical_roots(discovery_validation):
    path, document, kwargs = discovery_validation
    path.write_text(json.dumps(document))
    kwargs["expected_roots"] = {
        "transcript": kwargs["expected_roots"]["transcript"].parent / "other-projects",
        "desktop_metadata": kwargs["expected_roots"]["desktop_metadata"],
    }
    with pytest.raises(finalizer.FinalizeError, match="outside the canonical Claude root"):
        finalizer._validate_source_discovery_receipt(path, **kwargs)


@pytest.mark.parametrize("change", [
    lambda d: d.update(run_id="other"), lambda d: d.update(repetition=2),
    lambda d: d.update(repetition=True), lambda d: d.update(cli_session_id="other"),
    lambda d: d.update(desktop_session_id="local_other"), lambda d: d.update(metadata_only_discovery=False),
    lambda d: d.update(unrelated_content_read=True), lambda d: d.update(personal_history_content_read=True),
    lambda d: d.update(complete_inventories=False), lambda d: d.update(isolated_pair=False),
    lambda d: d.update(extra="unsupported"), lambda d: d["selected_artifacts"][0].update(source_path="/different/session.jsonl"),
    lambda d: d["selected_artifacts"][0].update(sha256="0" * 64),
    lambda d: d["selected_artifacts"][0].update(relative_path="../session.jsonl"),
    lambda d: d["selected_artifacts"][0].update(size_bytes=999),
    lambda d: d["root_summaries"][0].update(source_root_sha256="0" * 64),
    lambda d: d["root_summaries"][0].update(unrelated_entry_count=2),
    lambda d: d["selected_artifacts"].pop(), lambda d: d["root_summaries"].pop(),
])
def test_discovery_rejects_resigned_mismatch(discovery_validation, change):
    path, document, kwargs = discovery_validation
    change(document)
    path.write_text(json.dumps(sign_discovery(document)))
    with pytest.raises(finalizer.FinalizeError):
        finalizer._validate_source_discovery_receipt(path, **kwargs)


def test_discovery_preserves_bytes_and_rejects_hash_and_packaged_tamper(discovery_validation):
    path, document, kwargs = discovery_validation
    path.write_text(json.dumps(document, indent=3) + "\n")
    assert finalizer._validate_source_discovery_receipt(path, **kwargs) == path.read_bytes()
    document["proof_sha256"] = "0" * 64
    path.write_text(json.dumps(document))
    with pytest.raises(finalizer.FinalizeError, match="proof hash"):
        finalizer._validate_source_discovery_receipt(path, **kwargs)
    path.write_text(json.dumps(sign_discovery(document)))
    (kwargs["family_package"] / "transcript/session.jsonl").write_text("changed")
    with pytest.raises(finalizer.FinalizeError, match="hash or size"):
        finalizer._validate_source_discovery_receipt(path, **kwargs)


def test_discovery_accepts_hash_matching_frozen_copy_after_metadata_drift(discovery_validation, tmp_path):
    path, document, kwargs = discovery_validation
    path.write_text(json.dumps(document))
    original_desktop = Path(document["selected_artifacts"][1]["source_path"])
    frozen_desktop = tmp_path / "capture/native-family-private/desktop/session.json"
    frozen_desktop.parent.mkdir(parents=True)
    shutil.copyfile(original_desktop, frozen_desktop)
    shutil.copyfile(frozen_desktop, kwargs["family_package"] / "desktop/session.json")
    kwargs["source_location"] = finalizer._source_location_receipt(
        Path(document["selected_artifacts"][0]["source_path"]), frozen_desktop,
    )

    original_desktop.write_bytes(b'{"lastFocusedAt":"after-capture"}\n')

    assert finalizer._validate_source_discovery_receipt(path, **kwargs) == path.read_bytes()


def test_discovery_rejects_tampered_frozen_copy_after_metadata_drift(discovery_validation, tmp_path):
    path, document, kwargs = discovery_validation
    path.write_text(json.dumps(document))
    original_desktop = Path(document["selected_artifacts"][1]["source_path"])
    frozen_desktop = tmp_path / "capture/native-family-private/desktop/session.json"
    frozen_desktop.parent.mkdir(parents=True)
    frozen_desktop.write_bytes(b'{"tampered":true}\n')
    shutil.copyfile(frozen_desktop, kwargs["family_package"] / "desktop/session.json")
    kwargs["source_location"] = finalizer._source_location_receipt(
        Path(document["selected_artifacts"][0]["source_path"]), frozen_desktop,
    )
    original_desktop.write_bytes(b'{"lastFocusedAt":"after-capture"}\n')

    with pytest.raises(finalizer.FinalizeError, match="hash or size"):
        finalizer._validate_source_discovery_receipt(path, **kwargs)
