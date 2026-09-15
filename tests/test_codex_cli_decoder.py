"""Focused tests for the copied Codex CLI 0.154 native decoder."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import shutil

import pytest

from session_bench.adapters.codex_cli_decoder import (
    CodexCLIDecodeError,
    FROZEN_SURVIVAL_V1,
    decode_codex_cli_bundle,
)
from session_bench.survival_metrics import validate_input


ROOT = Path(__file__).resolve().parents[1]
SETUP_BUNDLE = ROOT / "artifacts/survival-v1-runs/codex-cli-setup-2/capture/public-native"
CALIBRATION_BUNDLE = ROOT / "artifacts/survival-v1-runs/codex-cli-cal-1/capture/public-native"


def _metric(decoded: dict, metric_id: str) -> dict:
    return next(row for row in decoded["metrics"] if row["id"] == metric_id)


def _copy_declared_bundle(source: Path, destination: Path) -> Path:
    manifest = json.loads((source / "decode.json").read_text())
    destination.mkdir()
    shutil.copy2(source / "decode.json", destination / "decode.json")
    for artifact in manifest["artifacts"]:
        target = destination / artifact["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / artifact["path"], target)
    return destination


def _refresh_manifest(package: Path) -> None:
    manifest = json.loads((package / "decode.json").read_text())
    for artifact in manifest["artifacts"]:
        path = package / artifact["path"]
        data = path.read_bytes()
        artifact["size_bytes"] = len(data)
        artifact["sha256"] = hashlib.sha256(data).hexdigest()
    (package / "decode.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n")


def test_current_0154_bundle_recovers_survival_facts_without_fabrication() -> None:
    decoded = decode_codex_cli_bundle(SETUP_BUNDLE)

    assert decoded["status"] == "ok"
    assert decoded["identity"]["cli_version"] == "0.154.0"
    assert decoded["identity"]["source"] == "exec"
    assert [row["turn_id"] for row in decoded["facts"]["submitted_turns"]] == ["turn-r1", "turn-r2"]
    assert {row["turn_id"] for row in decoded["facts"]["visible_responses"]} == {"turn-r1", "turn-r2"}
    assert {row["expected_id"] for row in decoded["facts"]["actions"]} == {
        "action-inspect", "action-baseline", "action-edit", "action-final",
    }
    assert {row["expected_action_id"] for row in decoded["facts"]["results"]} == {
        "action-inspect", "action-baseline", "action-edit", "action-final",
    }

    assert _metric(decoded, "causal.action_result")["state"] == "measured"
    assert _metric(decoded, "causal.turn_response")["state"] == "measured"
    assert _metric(decoded, "revision.final_after_r2")["state"] == "measured"
    assert _metric(decoded, "attribution.usage")["state"] == "measured"
    assert _metric(decoded, "attribution.reconciliation")["state"] == "measured"
    assert _metric(decoded, "attribution.model_config")["state"] == "measured"
    assert _metric(decoded, "attribution.model_config")["correct"] == 0
    assert _metric(decoded, "work.actions")["state"] == "measured"
    assert _metric(decoded, "work.actions")["correct"] == 1
    assert _metric(decoded, "work.results")["state"] == "measured"
    assert _metric(decoded, "work.results")["correct"] == 3
    assert _metric(decoded, "work.changed_files")["state"] == "measured"
    assert _metric(decoded, "work.changed_files")["correct"] == 0
    assert _metric(decoded, "portable.complete_root")["state"] == "unresolved"
    assert _metric(decoded, "portable.companions")["state"] == "unresolved"
    assert validate_input(decoded["measurement"])["configuration_id"] == "codex-cli"
    json.dumps(decoded)


def test_decoding_a_declared_copy_does_not_read_neighboring_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "copied-native")
    (tmp_path / "observer.jsonl").write_text('{"answer_key":"must-not-be-read"}\n')
    monkeypatch.chdir(tmp_path)

    decoded = decode_codex_cli_bundle(copied)

    assert decoded["status"] == "ok"
    assert decoded["package"]["artifacts"][0]["path"].endswith(".jsonl")
    assert all("answer_key" not in json.dumps(fact) for fact in decoded["facts"].values())


def test_removed_native_response_is_not_reconstructed(tmp_path: Path) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "damaged-native")
    manifest = json.loads((copied / "decode.json").read_text())
    rollout = copied / manifest["artifacts"][0]["path"]
    kept = []
    for line in rollout.read_bytes().splitlines(keepends=True):
        if b"SB_SURVIVAL_V1_RESPONSE_R2_correction" not in line:
            kept.append(line)
    rollout.write_bytes(b"".join(kept))
    _refresh_manifest(copied)

    decoded = decode_codex_cli_bundle(copied, complete_root=True)

    assert not any(row.get("canary") == "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ" for row in decoded["facts"]["visible_responses"])
    final_after_r2 = _metric(decoded, "revision.final_after_r2")
    assert final_after_r2["decoded_eligible"] == 0
    assert final_after_r2["state"] == "native_absent"
    assert final_after_r2["state"] != "measured"


def test_malformed_and_undeclared_copied_artifacts_are_rejected(tmp_path: Path) -> None:
    malformed_manifest = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "malformed-manifest")
    (malformed_manifest / "decode.json").write_text('{"format":"codex-rollout-v1"}\n')
    with pytest.raises(CodexCLIDecodeError):
        decode_codex_cli_bundle(malformed_manifest)

    undeclared = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "undeclared")
    (undeclared / "observer.jsonl").write_text('{"type":"observer-truth"}\n')
    with pytest.raises(CodexCLIDecodeError):
        decode_codex_cli_bundle(undeclared)

    malformed_record = _copy_declared_bundle(CALIBRATION_BUNDLE, tmp_path / "malformed-record")
    manifest = json.loads((malformed_record / "decode.json").read_text())
    (malformed_record / manifest["artifacts"][0]["path"]).open("ab").write(b"not-json\n")
    _refresh_manifest(malformed_record)
    decoded = decode_codex_cli_bundle(malformed_record)
    assert decoded["status"] == "partial"
    assert any(item["code"] == "malformed_record" for item in decoded["diagnostics"])


def _make_synthetic_bundle(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    rollout = destination / "rollout.jsonl"
    rollout.write_bytes(b"")
    data = rollout.read_bytes()
    manifest = {
        "format": "codex-rollout-v1",
        "artifacts": [
            {
                "id": "rollout",
                "path": "rollout.jsonl",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "depends_on": [],
            }
        ],
    }
    (destination / "decode.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n")
    return destination


def _write_records_bundle(destination: Path, records: list[dict]) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    data = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()
    (destination / "rollout.jsonl").write_bytes(data)
    (destination / "decode.json").write_text(
        json.dumps(
            {
                "format": "codex-rollout-v1",
                "artifacts": [{
                    "id": "rollout",
                    "path": "rollout.jsonl",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "depends_on": [],
                }],
            },
            separators=(",", ":"),
        ) + "\n"
    )
    return destination


def test_default_configuration_id_remains_codex_cli(tmp_path: Path) -> None:
    package = _make_synthetic_bundle(tmp_path / "synthetic-default")

    decoded = decode_codex_cli_bundle(package)

    assert decoded["measurement"]["configuration_id"] == "codex-cli"
    assert validate_input(decoded["measurement"])["configuration_id"] == "codex-cli"
    assert _metric(decoded, "portable.isolated_decode")["state"] == "unresolved"

    proven = decode_codex_cli_bundle(package, isolated_decode_proven=True)
    assert _metric(proven, "portable.isolated_decode")["state"] == "measured"


def test_explicit_codex_desktop_configuration_id_is_emitted(tmp_path: Path) -> None:
    package = _make_synthetic_bundle(tmp_path / "synthetic-desktop")

    default_decoded = decode_codex_cli_bundle(package)
    desktop_decoded = decode_codex_cli_bundle(package, configuration_id="codex-desktop")

    assert desktop_decoded["measurement"]["configuration_id"] == "codex-desktop"
    assert validate_input(desktop_decoded["measurement"])["configuration_id"] == "codex-desktop"
    assert desktop_decoded["metrics"] == default_decoded["metrics"]
    assert desktop_decoded["decoder"] == default_decoded["decoder"]


def test_invalid_configuration_id_is_rejected_before_decoding(tmp_path: Path) -> None:
    package = _make_synthetic_bundle(tmp_path / "synthetic-invalid")

    with pytest.raises(CodexCLIDecodeError):
        decode_codex_cli_bundle(package, configuration_id="codex-gui")

    with pytest.raises(CodexCLIDecodeError):
        decode_codex_cli_bundle(tmp_path / "does-not-exist", configuration_id="bogus")


def test_explicit_repetition_is_bound_and_invalid_values_fail() -> None:
    decoded = decode_codex_cli_bundle(SETUP_BUNDLE, repetition=3)
    assert decoded["measurement"]["repetition"] == 3
    with pytest.raises(CodexCLIDecodeError, match="repetition"):
        decode_codex_cli_bundle(SETUP_BUNDLE, repetition=0)


def test_codex_desktop_delegation_outputs_are_bounded_submitted_turns(tmp_path: Path) -> None:
    package = tmp_path / "desktop-delegations"
    package.mkdir()
    rollout = package / "rollout.jsonl"
    records = []
    for ordinal, (turn_id, text) in enumerate(FROZEN_SURVIVAL_V1.turns, 1):
        records.append(
            {
                "timestamp": f"2026-09-12T00:00:0{ordinal}Z",
                "type": "event_msg",
                "ordinal": ordinal,
                "payload": {
                    "type": "item_completed",
                    "turn_id": f"native-{turn_id}",
                    "item": {
                        "type": "FunctionCallOutput",
                        "id": f"delegation-{ordinal}",
                        "namespace": "codex_app",
                        "name": "create_thread" if ordinal == 1 else "send_message_to_thread",
                        "output": (
                            "<codex_delegation><source_thread_id>source</source_thread_id>"
                            f"<input>{html.escape(text)}</input></codex_delegation>"
                        ),
                    },
                },
            }
        )
    records.append(
        {
            "timestamp": "2026-09-12T00:00:03Z",
            "type": "response_item",
            "ordinal": 3,
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": FROZEN_SURVIVAL_V1.turn_text["turn-r2"]}],
                "internal_chat_message_metadata_passthrough": {"turn_id": "native-turn-r1"},
            },
        }
    )
    data = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()
    rollout.write_bytes(data)
    manifest = {
        "format": "codex-rollout-v1",
        "artifacts": [
            {
                "id": "rollout",
                "path": "rollout.jsonl",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "depends_on": [],
            }
        ],
    }
    (package / "decode.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n")

    decoded = decode_codex_cli_bundle(package, configuration_id="codex-desktop")

    assert [(row["turn_id"], row["state"]) for row in decoded["facts"]["submitted_turns"] if row["state"] == "present"] == [
        ("turn-r1", "present"),
        ("turn-r2", "present"),
    ]
    assert decoded["facts"]["submitted_turns"][-1]["state"] == "unknown"
    assert _metric(decoded, "work.submitted_turns")["state"] == "measured"
    assert _metric(decoded, "work.submitted_turns")["decoded_eligible"] == 2


def test_codex_desktop_direct_task_windows_bind_escaped_input_and_responses(tmp_path: Path) -> None:
    records = []
    ordinal = 1
    for logical_turn, text in FROZEN_SURVIVAL_V1.turns:
        native_turn = f"native-{logical_turn}"
        stored_text = text.replace("_", "\\_") if logical_turn == "turn-r1" else text
        records.extend([
            {
                "timestamp": "2026-09-14T00:00:00Z", "type": "event_msg", "ordinal": ordinal,
                "payload": {"type": "task_started", "turn_id": native_turn},
            },
            {
                "timestamp": "2026-09-14T00:00:01Z", "type": "response_item", "ordinal": ordinal + 1,
                "payload": {"type": "message", "role": "user", "content": [{"text": stored_text}]},
            },
            {
                "timestamp": "2026-09-14T00:00:02Z", "type": "event_msg", "ordinal": ordinal + 2,
                "payload": {"type": "item_completed", "turn_id": native_turn,
                            "item": {"type": "UserMessage", "id": f"input-{logical_turn}"}},
            },
            {
                "timestamp": "2026-09-14T00:00:03Z", "type": "response_item", "ordinal": ordinal + 3,
                "payload": {"type": "message", "role": "assistant", "phase": "final_answer",
                            "content": [{"text": f"done {FROZEN_SURVIVAL_V1.canary_by_turn[logical_turn]}"}]},
            },
            {
                "timestamp": "2026-09-14T00:00:04Z", "type": "event_msg", "ordinal": ordinal + 4,
                "payload": {"type": "task_complete", "turn_id": native_turn},
            },
        ])
        ordinal += 5

    decoded = decode_codex_cli_bundle(
        _write_records_bundle(tmp_path / "desktop-direct", records),
        configuration_id="codex-desktop",
    )

    assert [(row["turn_id"], row["state"]) for row in decoded["facts"]["submitted_turns"]] == [
        ("turn-r1", "present"), ("turn-r2", "present"),
    ]
    assert {row["turn_id"] for row in decoded["facts"]["visible_responses"] if row["state"] == "present"} == {
        "turn-r1", "turn-r2",
    }
    assert _metric(decoded, "work.submitted_turns")["state"] == "measured"
    assert _metric(decoded, "work.visible_responses")["state"] == "measured"


def test_current_desktop_json_tool_arguments_bind_direct_workspace_actions(tmp_path: Path) -> None:
    native_turn = "native-turn-r1"
    records = [
        {
            "timestamp": "2026-09-14T00:00:00Z", "type": "response_item", "ordinal": 1,
            "payload": {"type": "message", "role": "user", "content": [{"text": FROZEN_SURVIVAL_V1.turn_text["turn-r1"]}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-14T00:00:01Z", "type": "response_item", "ordinal": 2,
            "payload": {"type": "custom_tool_call", "id": "call-inspect", "call_id": "inspect", "name": "exec",
                        "input": 'const r = await tools.exec_command({"cmd":"python3 bench_check.py inspect","workdir":"$RUN_ROOT/fixture_project"}); text(r.output);',
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-14T00:00:02Z", "type": "event_msg", "ordinal": 3,
            "payload": {"type": "item_completed", "turn_id": native_turn,
                        "item": {"type": "CommandExecution", "id": "exec-inspect",
                                 "command": ["/bin/zsh", "-lc", "python3 bench_check.py inspect"],
                                 "cwd": "file://$RUN_ROOT/fixture_project", "status": "completed", "exit_code": 0,
                                 "stdout": "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001", "stderr": ""}},
        },
    ]

    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "desktop-json-tool", records))
    action = next(row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect")
    assert action["workdir"] == "fixture_project"
    assert action["state"] == "present"
    assert next(row for row in decoded["facts"]["results"] if row["expected_action_id"] == "action-inspect")["state"] == "present"


def test_absolute_live_workspace_root_is_canonicalized_without_counting_dot_commands(tmp_path: Path) -> None:
    workspace = "/private/synthetic/session-bench/fixture_project"
    native_turn = "native-turn-r1"
    records = [
        {
            "timestamp": "2026-09-14T00:00:00Z",
            "type": "session_meta",
            "ordinal": 1,
            "payload": {
                "session_id": "synthetic-session",
                "cli_version": "0.154.0",
                "cwd": workspace,
            },
        },
        {
            "timestamp": "2026-09-14T00:00:01Z",
            "type": "response_item",
            "ordinal": 2,
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"text": FROZEN_SURVIVAL_V1.turn_text["turn-r1"]}],
                "internal_chat_message_metadata_passthrough": {"turn_id": native_turn},
            },
        },
        {
            "timestamp": "2026-09-14T00:00:02Z",
            "type": "response_item",
            "ordinal": 3,
            "payload": {
                "type": "custom_tool_call",
                "id": "call-inspect-absolute",
                "call_id": "inspect-absolute",
                "name": "exec",
                "input": json.dumps({
                    "cmd": "python3 bench_check.py inspect",
                    "workdir": workspace,
                }),
                "internal_chat_message_metadata_passthrough": {"turn_id": native_turn},
            },
        },
        {
            "timestamp": "2026-09-14T00:00:03Z",
            "type": "event_msg",
            "ordinal": 4,
            "payload": {
                "type": "item_completed",
                "turn_id": native_turn,
                "item": {
                    "type": "CommandExecution",
                    "id": "execution-inspect-absolute",
                    "command": ["/bin/zsh", "-lc", "python3 bench_check.py inspect"],
                    "cwd": f"file://{workspace}",
                    "status": "completed",
                    "exit_code": 0,
                    "stdout": "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001",
                    "stderr": "",
                },
            },
        },
    ]

    decoded = decode_codex_cli_bundle(_write_records_bundle(tmp_path / "absolute-workspace", records))

    actions = [row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect"]
    assert len(actions) == 1
    assert actions[0]["workdir"] == "fixture_project"
    assert _metric(decoded, "work.actions")["decoded_eligible"] == 1


def test_one_desktop_tool_call_can_join_file_change_and_execution(tmp_path: Path) -> None:
    package = tmp_path / "desktop-combined-call"
    package.mkdir()
    native_turn = "native-turn-r2"
    records = [
        {
            "timestamp": "2026-09-12T00:00:01Z",
            "type": "event_msg",
            "ordinal": 1,
            "payload": {
                "type": "item_completed",
                "turn_id": "native-turn-r1",
                "item": {
                    "type": "FunctionCallOutput", "id": "delegation-r1",
                    "namespace": "codex_app", "name": "create_thread",
                    "output": "<codex_delegation><source_thread_id>source</source_thread_id>"
                              f"<input>{html.escape(FROZEN_SURVIVAL_V1.turn_text['turn-r1'])}</input>"
                              "</codex_delegation>",
                },
            },
        },
        {
            "timestamp": "2026-09-12T00:00:02Z",
            "type": "event_msg",
            "ordinal": 2,
            "payload": {
                "type": "item_completed",
                "turn_id": native_turn,
                "item": {
                    "type": "FunctionCallOutput", "id": "delegation-r2",
                    "namespace": "codex_app", "name": "send_message_to_thread",
                    "output": "<codex_delegation><source_thread_id>source</source_thread_id>"
                              f"<input>{html.escape(FROZEN_SURVIVAL_V1.turn_text['turn-r2'])}</input>"
                              "</codex_delegation>",
                },
            },
        },
        {
            "timestamp": "2026-09-12T00:00:03Z",
            "type": "response_item",
            "ordinal": 3,
            "payload": {
                "type": "custom_tool_call",
                "id": "call-record",
                "call_id": "call-r2",
                "name": "exec",
                "input": (
                    'const patch = "*** Update File: $RUN_ROOT/project/fixture_project/checkout.py\\n"; '
                    'const r = await tools.exec_command({cmd:"python3 bench_check.py final"});'
                ),
                "internal_chat_message_metadata_passthrough": {"turn_id": native_turn},
            },
        },
        {
            "timestamp": "2026-09-12T00:00:04Z",
            "type": "event_msg",
            "ordinal": 4,
            "payload": {
                "type": "item_completed",
                "turn_id": native_turn,
                "item": {
                    "type": "FileChange",
                    "id": "change-r2",
                    "status": "completed",
                    "changes": {"fixture_project/checkout.py": {"type": "update"}},
                },
            },
        },
        {
            "timestamp": "2026-09-12T00:00:05Z",
            "type": "event_msg",
            "ordinal": 5,
            "payload": {
                "type": "item_completed",
                "turn_id": native_turn,
                "item": {
                    "type": "CommandExecution",
                    "id": "execution-r2",
                    "command": ["/bin/zsh", "-lc", "python3 bench_check.py final"],
                    "cwd": "file://$RUN_ROOT/project/fixture_project",
                    "status": "completed",
                    "exit_code": 0,
                    "stdout": "SB_SURVIVAL_V1_HELPER_FINAL_final-fixture-0001",
                    "stderr": "",
                },
            },
        },
    ]
    data = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records).encode()
    (package / "rollout.jsonl").write_bytes(data)
    (package / "decode.json").write_text(
        json.dumps(
            {
                "format": "codex-rollout-v1",
                "artifacts": [{
                    "id": "rollout",
                    "path": "rollout.jsonl",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "depends_on": [],
                }],
            },
            separators=(",", ":"),
        ) + "\n"
    )

    decoded = decode_codex_cli_bundle(package, configuration_id="codex-desktop")

    actions = {row["expected_id"]: row for row in decoded["facts"]["actions"]}
    assert actions["action-edit"]["native_file_change_id"] == "change-r2"
    assert actions["action-final"]["native_execution_id"] == "execution-r2"
    assert actions["action-final"]["workdir"] == "fixture_project"
    results = {row["expected_action_id"]: row for row in decoded["facts"]["results"]}
    assert results["action-edit"]["locator"]["ordinal"] == 4
    assert results["action-final"]["locator"]["ordinal"] == 5

    without_change_records = [record for record in records if record.get("payload", {}).get("item", {}).get("type") != "FileChange"]
    without_change = _write_records_bundle(tmp_path / "desktop-no-change", without_change_records)
    damaged = decode_codex_cli_bundle(without_change, configuration_id="codex-desktop")
    damaged_results = {row["expected_action_id"]: row for row in damaged["facts"]["results"]}
    assert damaged_results["action-edit"]["state"] == "unknown"
    assert damaged_results["action-final"]["state"] == "present"


def test_compound_and_predecessor_result_is_derived_as_success(tmp_path: Path) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "compound-results")

    decoded = decode_codex_cli_bundle(copied)

    results = {row["expected_action_id"]: row for row in decoded["facts"]["results"]}
    inspect = results["action-inspect"]
    assert inspect["exit_code"] == 0
    assert inspect["derivation"] in {"native_event", "shell_and_predecessor_executed"}


def test_commentary_messages_do_not_inflate_visible_response_population(tmp_path: Path) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "commentary-population")
    manifest = json.loads((copied / "decode.json").read_text())
    rollout = copied / manifest["artifacts"][0]["path"]
    rows = rollout.read_text().splitlines()
    final = next(
        json.loads(line) for line in rows
        if json.loads(line).get("type") == "response_item"
        and json.loads(line).get("payload", {}).get("type") == "message"
        and json.loads(line).get("payload", {}).get("role") == "assistant"
        and json.loads(line).get("payload", {}).get("phase") == "final_answer"
    )
    commentary = json.loads(json.dumps(final))
    commentary["ordinal"] = max(json.loads(line).get("ordinal", 0) for line in rows) + 1
    commentary["payload"]["id"] = "extra-commentary"
    commentary["payload"]["phase"] = "commentary"
    commentary["payload"]["content"] = [{"type": "output_text", "text": "working"}]
    rollout.write_text("\n".join([*rows, json.dumps(commentary, ensure_ascii=False)]) + "\n")
    _refresh_manifest(copied)

    decoded = decode_codex_cli_bundle(copied)

    metric = _metric(decoded, "work.visible_responses")
    assert metric["correct"] == 2
    assert metric["decoded_eligible"] == 2


def test_duplicate_final_answer_fails_one_logical_response_slot_closed(tmp_path: Path) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "duplicate-final")
    manifest = json.loads((copied / "decode.json").read_text())
    rollout = copied / manifest["artifacts"][0]["path"]
    rows = rollout.read_text().splitlines()
    final = next(
        json.loads(line) for line in rows
        if json.loads(line).get("type") == "response_item"
        and json.loads(line).get("payload", {}).get("type") == "message"
        and json.loads(line).get("payload", {}).get("role") == "assistant"
        and json.loads(line).get("payload", {}).get("phase") == "final_answer"
    )
    duplicate = json.loads(json.dumps(final))
    duplicate["ordinal"] = max(json.loads(line).get("ordinal", 0) for line in rows) + 1
    duplicate["payload"]["id"] = "duplicate-final"
    rollout.write_text("\n".join([*rows, json.dumps(duplicate, ensure_ascii=False)]) + "\n")
    _refresh_manifest(copied)

    decoded = decode_codex_cli_bundle(copied)

    metric = _metric(decoded, "work.visible_responses")
    assert metric["correct"] == 1
    assert metric["decoded_eligible"] == 2
    assert any(row["code"] == "ambiguous_final_response" for row in decoded["diagnostics"])


def test_duplicate_finals_in_both_slots_remain_resolved_contradictions(tmp_path: Path) -> None:
    copied = _copy_declared_bundle(SETUP_BUNDLE, tmp_path / "duplicate-both-finals")
    manifest = json.loads((copied / "decode.json").read_text())
    rollout = copied / manifest["artifacts"][0]["path"]
    rows = rollout.read_text().splitlines()
    finals = [
        json.loads(line) for line in rows
        if json.loads(line).get("type") == "response_item"
        and json.loads(line).get("payload", {}).get("type") == "message"
        and json.loads(line).get("payload", {}).get("role") == "assistant"
        and json.loads(line).get("payload", {}).get("phase") == "final_answer"
    ]
    next_ordinal = max(json.loads(line).get("ordinal", 0) for line in rows) + 1
    duplicates = []
    for index, final in enumerate(finals):
        duplicate = json.loads(json.dumps(final))
        duplicate["ordinal"] = next_ordinal + index
        duplicate["payload"]["id"] = f"duplicate-final-{index}"
        duplicates.append(json.dumps(duplicate, ensure_ascii=False))
    rollout.write_text("\n".join([*rows, *duplicates]) + "\n")
    _refresh_manifest(copied)

    decoded = decode_codex_cli_bundle(copied)

    metric = _metric(decoded, "work.visible_responses")
    assert metric["state"] == "contradiction"
    assert metric["correct"] == 0
    assert metric["decoded_eligible"] == 2


def test_desktop_delegations_from_multiple_source_threads_fail_closed(tmp_path: Path) -> None:
    records = []
    for ordinal, (turn_id, text) in enumerate(FROZEN_SURVIVAL_V1.turns, 1):
        records.append({
            "timestamp": f"2026-09-12T00:00:0{ordinal}Z",
            "type": "event_msg",
            "ordinal": ordinal,
            "payload": {
                "type": "item_completed",
                "turn_id": f"native-{turn_id}",
                "item": {
                    "type": "FunctionCallOutput",
                    "id": f"delegation-{ordinal}",
                    "namespace": "codex_app",
                    "name": "create_thread" if ordinal == 1 else "send_message_to_thread",
                    "output": (
                        "<codex_delegation>"
                        f"<source_thread_id>source-{ordinal}</source_thread_id>"
                        f"<input>{html.escape(text)}</input></codex_delegation>"
                    ),
                },
            },
        })
    package = _write_records_bundle(tmp_path / "mixed-source", records)

    decoded = decode_codex_cli_bundle(package, configuration_id="codex-desktop")

    assert _metric(decoded, "work.submitted_turns")["correct"] == 0
    assert any(row["code"] == "ambiguous_codex_delegation" for row in decoded["diagnostics"])


@pytest.mark.parametrize(("later_marker", "expected_exit", "expected_derivation"), [
    (False, 1, "native_event"),
    (True, 0, "shell_and_predecessor_executed"),
])
def test_and_predecessor_requires_evidence_that_later_command_ran(
    tmp_path: Path,
    later_marker: bool,
    expected_exit: int,
    expected_derivation: str,
) -> None:
    native_turn = "native-turn-r1"
    r1 = FROZEN_SURVIVAL_V1.turn_text["turn-r1"]
    stdout = "SB_SURVIVAL_V1_HELPER_BASELINE_baseline-fixture-0001" if later_marker else ""
    records = [
        {
            "timestamp": "2026-09-12T00:00:01Z", "type": "response_item", "ordinal": 1,
            "payload": {"type": "message", "role": "user", "content": [{"text": r1}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-12T00:00:02Z", "type": "response_item", "ordinal": 2,
            "payload": {"type": "custom_tool_call", "id": "call", "call_id": "call-r1", "name": "exec",
                        "input": 'const r = await tools.exec_command({cmd:"python3 bench_check.py inspect && python3 bench_check.py baseline"});',
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-12T00:00:03Z", "type": "event_msg", "ordinal": 3,
            "payload": {"type": "item_completed", "turn_id": native_turn,
                        "item": {"type": "CommandExecution", "id": "execution", "command": ["/bin/zsh", "-lc", "python3 bench_check.py inspect && python3 bench_check.py baseline"],
                                 "cwd": "file://$RUN_ROOT/project/fixture_project", "status": "failed", "exit_code": 1, "stdout": stdout, "stderr": ""}},
        },
    ]
    package = _write_records_bundle(tmp_path / f"and-{later_marker}", records)

    decoded = decode_codex_cli_bundle(package)

    inspect = next(row for row in decoded["facts"]["results"] if row["expected_action_id"] == "action-inspect")
    assert inspect["exit_code"] == expected_exit
    assert inspect["derivation"] == expected_derivation


def _helper_records(native_turn: str, turn_text: str, command: str) -> list[dict]:
    return [
        {
            "timestamp": "2026-09-12T00:00:01Z", "type": "response_item", "ordinal": 1,
            "payload": {"type": "message", "role": "user", "content": [{"text": turn_text}],
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-12T00:00:02Z", "type": "response_item", "ordinal": 2,
            "payload": {"type": "custom_tool_call", "id": "call", "call_id": "call-r1", "name": "exec",
                        "input": f'const r = await tools.exec_command({{cmd:"{command}"}});',
                        "internal_chat_message_metadata_passthrough": {"turn_id": native_turn}},
        },
        {
            "timestamp": "2026-09-12T00:00:03Z", "type": "event_msg", "ordinal": 3,
            "payload": {"type": "item_completed", "turn_id": native_turn,
                         "item": {"type": "CommandExecution", "id": "execution",
                                  "command": ["/bin/zsh", "-lc", command],
                                  "cwd": "file://$RUN_ROOT/project/fixture_project", "status": "completed",
                                  "exit_code": 0,
                                  "stdout": "SB_SURVIVAL_V1_HELPER_INSPECT_inspect-fixture-0001", "stderr": ""}},
        },
    ]


def test_helper_accepts_correct_run_canary_flag_and_strips_to_canonical(tmp_path: Path) -> None:
    canary = "SB_SURVIVAL_V1_RUN_fixture_0001"
    command = f"python3 bench_check.py inspect --run-canary {canary}"
    package = _write_records_bundle(
        tmp_path / "canary-correct",
        _helper_records("native-turn-r1", FROZEN_SURVIVAL_V1.turn_text["turn-r1"], command),
    )

    decoded = decode_codex_cli_bundle(package)

    actions = [row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect"]
    assert len(actions) == 1
    assert actions[0]["command"] == "python3 bench_check.py inspect"
    assert actions[0]["state"] == "present"


def test_helper_rejects_wrong_run_canary_flag(tmp_path: Path) -> None:
    command = "python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_wrong_0001"
    package = _write_records_bundle(
        tmp_path / "canary-wrong",
        _helper_records("native-turn-r1", FROZEN_SURVIVAL_V1.turn_text["turn-r1"], command),
    )

    decoded = decode_codex_cli_bundle(package)

    assert not [row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect"]


def test_helper_rejects_duplicate_and_malformed_run_canary_flags(tmp_path: Path) -> None:
    canary = "SB_SURVIVAL_V1_RUN_fixture_0001"
    for name, command in [
        ("duplicate", f"python3 bench_check.py inspect --run-canary {canary} --run-canary {canary}"),
        ("missing-value", "python3 bench_check.py inspect --run-canary"),
        ("equals-form", f"python3 bench_check.py inspect --run-canary={canary}"),
    ]:
        package = _write_records_bundle(
            tmp_path / f"canary-{name}",
            _helper_records("native-turn-r1", FROZEN_SURVIVAL_V1.turn_text["turn-r1"], command),
        )
        decoded = decode_codex_cli_bundle(package)
        assert not [row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect"], name


def test_helper_accepts_dynamic_instantiated_run_canary(tmp_path: Path) -> None:
    from session_bench.adapters.codex_cli_decoder import ExpectedAction, FrozenWorkload

    run_id = "codex-cli-eval-2"
    canary = "SB_SURVIVAL_V1_RUN_codex-cli-eval-2"
    r1 = FROZEN_SURVIVAL_V1.turn_text["turn-r1"].replace(
        "SB_SURVIVAL_V1_RUN_fixture_0001", canary
    )
    workload = FrozenWorkload(
        run_id=run_id,
        run_canary=canary,
        turns=(("turn-r1", r1),),
        response_canaries=(("turn-r1", "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"),),
        actions=(
            ExpectedAction(
                "action-inspect", "turn-r1", "inspect", "python3 bench_check.py inspect",
                "fixture_project/checkout.py", "inspect-fixture-0001",
            ),
        ),
    )
    command = f"python3 bench_check.py inspect --run-canary {canary}"
    package = _write_records_bundle(tmp_path / "canary-dynamic", _helper_records("native-turn-r1", r1, command))

    decoded = decode_codex_cli_bundle(package, workload=workload)

    actions = [row for row in decoded["facts"]["actions"] if row["expected_id"] == "action-inspect"]
    assert len(actions) == 1
    assert actions[0]["command"] == "python3 bench_check.py inspect"

    wrong = _write_records_bundle(
        tmp_path / "canary-dynamic-wrong",
        _helper_records("native-turn-r1", r1, "python3 bench_check.py inspect --run-canary SB_SURVIVAL_V1_RUN_fixture_0001"),
    )
    refused = decode_codex_cli_bundle(wrong, workload=workload)
    assert not [row for row in refused["facts"]["actions"] if row["expected_id"] == "action-inspect"]
