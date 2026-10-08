"""Offline qualification of one explicitly selected Copilot survival capture.

This validates a frozen CLI capture and its observer/helper boundary. It does
not discover Copilot homes, execute the harness, or grant independent scoring
or publication status.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .adapters.agent_session_native_decoder import NativeDecodeError, decode_agent_native_bytes
from .workload_instance import instantiate_workload


class CopilotQualificationError(ValueError):
    """The explicit capture is incomplete or internally inconsistent."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CopilotQualificationError("duplicate JSON key")
        result[key] = value
    return result


def _json_bytes(data: bytes, label: str) -> Any:
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, CopilotQualificationError):
            raise
        raise CopilotQualificationError(f"{label} is invalid JSON") from error


def _file(root: Path, relative: str) -> bytes:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise CopilotQualificationError(f"missing or unsafe capture file: {relative}")
    return path.read_bytes()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_hash(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("sha256"), str):
        return value["sha256"]
    return None


def _read_json(root: Path, relative: str) -> Any:
    return _json_bytes(_file(root, relative), relative)


def _stream(data: bytes, label: str) -> list[Mapping[str, Any]]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CopilotQualificationError(f"{label} is not UTF-8") from error
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        row = _json_bytes(line.encode(), f"{label}:{number}")
        if not isinstance(row, Mapping):
            raise CopilotQualificationError(f"{label}:{number} is not an object")
        rows.append(row)
    if not rows:
        raise CopilotQualificationError(f"{label} has no JSONL records")
    return rows


def _tool_ids(rows: list[Mapping[str, Any]]) -> tuple[list[str], list[str], list[str], bool]:
    requested: list[str] = []
    started: list[str] = []
    completed: list[str] = []
    completions_succeeded = True
    for row in rows:
        kind = row.get("type")
        data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
        if kind == "assistant.message":
            requests = data.get("toolRequests")
            if requests is not None and not isinstance(requests, list):
                raise CopilotQualificationError("Copilot toolRequests is not an array")
            for request in requests or []:
                if not isinstance(request, Mapping) or not isinstance(request.get("toolCallId"), str):
                    raise CopilotQualificationError("Copilot tool request has no call identity")
                requested.append(request["toolCallId"])
        elif kind == "tool.execution_start":
            if not isinstance(data.get("toolCallId"), str):
                raise CopilotQualificationError("Copilot execution start has no call identity")
            started.append(data["toolCallId"])
        elif kind == "tool.execution_complete":
            if not isinstance(data.get("toolCallId"), str):
                raise CopilotQualificationError("Copilot execution completion has no call identity")
            completed.append(data["toolCallId"])
            completions_succeeded = completions_succeeded and data.get("success") is True
    return requested, started, completed, completions_succeeded


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _validate_tool_path_scope(rows: list[Mapping[str, Any]], workspace: Path) -> None:
    allowed = workspace.resolve()
    absolute_path = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s\"'`;&|<>]+)")
    checked: set[str] = set()
    for row in rows:
        if row.get("type") == "assistant.message":
            data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
            arguments = [item.get("arguments") for item in data.get("toolRequests", []) if isinstance(item, Mapping)]
        elif row.get("type") == "tool.execution_start":
            data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
            arguments = [data.get("arguments")]
        else:
            continue
        for argument in arguments:
            for text in _strings(argument):
                for match in absolute_path.finditer(text):
                    candidate = match.group(0).rstrip(".,:)]}")
                    # Copilot's benign scope-inspection command emits the
                    # shell redirection ``2>/dev/null``. That is a file
                    # descriptor sink, not a tool target or filesystem read.
                    # Keep other /dev/null mentions subject to the normal
                    # absolute-path scope check.
                    if candidate == "/dev/null" and match.start() > 0 and text[match.start() - 1] == ">":
                        continue
                    # ``**/*`` is a relative glob. The ``/*`` after a wildcard
                    # is a pattern separator, not a path from the root.
                    if match.start() > 0 and text[match.start() - 1] == "*":
                        continue
                    if candidate in checked:
                        continue
                    checked.add(candidate)
                    path = Path(candidate)
                    if path.is_absolute():
                        try:
                            path.resolve(strict=False).relative_to(allowed)
                        except ValueError as error:
                            raise CopilotQualificationError("native tool arguments reference a path outside the isolated fixture workspace") from error


def _helper_rows(data: bytes) -> list[Mapping[str, Any]]:
    rows = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = _json_bytes(line.encode(), f"helper observer:{number}")
        if not isinstance(row, Mapping):
            raise CopilotQualificationError(f"helper observer:{number} is not an object")
        rows.append(row)
    return rows


def qualify_copilot_capture(capture_root: Path | str) -> dict[str, Any]:
    """Verify one captured Copilot run against its instantiated workload.

    The caller chooses ``capture_root`` explicitly. The verifier reads only
    declared stdout, native, workload, observer, and workspace files.
    """
    root = Path(capture_root)
    if root.is_symlink() or not root.is_dir():
        raise CopilotQualificationError("capture root must be an ordinary directory")

    attempt = _read_json(root, "attempt.json")
    template = _read_json(root, "workload_template.json")
    workload = _read_json(root, "workload_instance.json")
    if not isinstance(attempt, Mapping) or not isinstance(template, Mapping) or not isinstance(workload, Mapping):
        raise CopilotQualificationError("attempt, workload template, and instance must be objects")
    run_id = workload.get("run_id")
    run_canary = workload.get("run_canary")
    if not isinstance(run_id, str) or not isinstance(run_canary, str) or not run_canary.startswith("SB_SURVIVAL_V1_RUN_"):
        raise CopilotQualificationError("workload run identity is invalid")
    try:
        expected_workload, _ = instantiate_workload(template, run_id)
    except ValueError as error:
        raise CopilotQualificationError("workload template cannot instantiate this run") from error
    if workload != expected_workload:
        raise CopilotQualificationError("workload instance differs from frozen template")
    fixture_template = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload/workload.json"
    if _sha(_file(root, "workload_template.json")) != _sha(fixture_template.read_bytes()):
        raise CopilotQualificationError("workload template differs from checked-in v1 workload")
    if attempt.get("status") != "completed" or attempt.get("exit_code") != 0:
        raise CopilotQualificationError("capture attempt did not complete successfully")

    turns = workload.get("turns")
    if not isinstance(turns, list) or len(turns) != 2:
        raise CopilotQualificationError("survival-v1 workload must contain exactly two turns")
    response_canaries = [turn.get("response_canary") for turn in turns if isinstance(turn, Mapping)]
    if len(response_canaries) != 2 or not all(isinstance(value, str) for value in response_canaries):
        raise CopilotQualificationError("workload response canaries are invalid")

    native_dir = root / "native"
    if native_dir.is_symlink() or not native_dir.is_dir():
        raise CopilotQualificationError("native root must be an ordinary directory")
    native_files = [path for path in native_dir.rglob("events.jsonl") if path.is_file() and not path.is_symlink()]
    if len(native_files) != 1:
        raise CopilotQualificationError("capture must contain exactly one scoped Copilot events.jsonl")
    native_relative = native_files[0].relative_to(root).as_posix()
    native_bytes = _file(root, native_relative)
    try:
        decoded = decode_agent_native_bytes("copilot", native_bytes)
    except NativeDecodeError as error:
        raise CopilotQualificationError(f"native Copilot decode failed: {error}") from error
    if decoded.status != "complete" or decoded.session_id != attempt.get("session_id"):
        raise CopilotQualificationError("native Copilot session is unsupported or has mismatched identity")
    if decoded.version != attempt.get("cli_version"):
        raise CopilotQualificationError("native Copilot version differs from attempt metadata")

    native_rows = _stream(native_bytes, native_relative)
    if native_rows[0].get("type") != "session.start":
        raise CopilotQualificationError("native Copilot header is not session.start")
    starts = [row for row in native_rows if row.get("type") == "session.start"]
    resumes = [row for row in native_rows if row.get("type") == "session.resume"]
    users = [row for row in native_rows if row.get("type") == "user.message"]
    if len(starts) != 1 or len(resumes) != 1 or len(users) != 2:
        raise CopilotQualificationError("native session must show one start, one resume, and two user turns")
    for row, turn in zip(users, turns):
        content = (row.get("data") or {}).get("content") if isinstance(row.get("data"), Mapping) else None
        if content != turn.get("text"):
            raise CopilotQualificationError("native submitted prompt differs from the frozen workload")
    if not all(run_canary in str(row.get("data", {}).get("content", "")) for row in users):
        raise CopilotQualificationError("native user turns do not carry the run canary")

    stdout_hashes = {}
    stdout_messages = []
    for index, revision in enumerate(("r1", "r2")):
        stdout = _file(root, f"capture/{revision}.stdout")
        stderr = _file(root, f"capture/{revision}.stderr")
        if stderr:
            raise CopilotQualificationError(f"Copilot {revision} stderr is non-empty")
        stream_rows = _stream(stdout, f"capture/{revision}.stdout")
        assistant = [
            row.get("data", {}).get("content")
            for row in stream_rows
            if row.get("type") == "assistant.message" and isinstance(row.get("data"), Mapping)
            and isinstance(row.get("data", {}).get("content"), str)
        ]
        if not assistant or not assistant[-1].endswith(response_canaries[index]):
            raise CopilotQualificationError(f"visible {revision} response does not end with its exact canary")
        if not any(row.get("type") == "result" and row.get("exitCode") == 0 for row in stream_rows):
            raise CopilotQualificationError(f"Copilot {revision} stdout lacks a successful result event")
        stdout_messages.append(assistant[-1])
        stdout_hashes[revision] = {"sha256": _sha(stdout), "size_bytes": len(stdout)}

    requested, started, completed, successes = _tool_ids(native_rows)
    execution_workspace = attempt.get("execution_workspace_path")
    scope_root = Path(execution_workspace) if isinstance(execution_workspace, str) else root / "workspace/fixture_project"
    _validate_tool_path_scope(native_rows, scope_root)
    if not requested or len(requested) != len(set(requested)):
        raise CopilotQualificationError("native tool requests are empty or have duplicate IDs")
    if len(started) != len(set(started)) or len(completed) != len(set(completed)):
        raise CopilotQualificationError("native tool lifecycle has duplicate call IDs")
    if set(requested) != set(started) or set(started) != set(completed):
        raise CopilotQualificationError("native tool request/start/completion populations do not join")

    helper_bytes = _file(root, "workspace/fixture_project/.survival-observer.jsonl")
    helper = _helper_rows(helper_bytes)
    helper = [row for row in helper if row.get("run_canary") == run_canary]
    expected = {"inspect": ("inspect-fixture-0001", 0), "baseline": ("baseline-fixture-0001", 1), "final": ("final-fixture-0001", 0)}
    if len(helper) != len(expected) or {row.get("phase") for row in helper} != set(expected):
        raise CopilotQualificationError("helper observer does not contain exactly inspect, baseline, and final")
    helper_summaries = {}
    for row in helper:
        phase = row["phase"]
        nonce, exit_code = expected[phase]
        if row.get("helper_nonce") != nonce or row.get("exit_code") != exit_code:
            raise CopilotQualificationError(f"helper {phase} nonce or exit code differs from workload")
        output = row.get("output")
        prefix = f"SB_SURVIVAL_V1_HELPER_{phase.upper()}_{nonce} "
        if not isinstance(output, str) or not output.startswith(prefix):
            raise CopilotQualificationError(f"helper {phase} output marker is invalid")
        body = _json_bytes(output[len(prefix):].encode(), f"helper {phase} output")
        if phase == "inspect":
            fixture_checkout = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload/fixture_project/checkout.py"
            expected_checkout = _sha(fixture_checkout.read_bytes())
            if not isinstance(body, Mapping) or body.get("checkout_sha256") != expected_checkout:
                raise CopilotQualificationError("inspect helper does not identify the initial checkout bytes")
        elif phase == "baseline":
            tests = body.get("tests") if isinstance(body, Mapping) else None
            expected_baseline = [(20, 30), (30, 50), (54, 54)]
            if not isinstance(tests, list) or [
                (test.get("actual"), test.get("expected")) for test in tests if isinstance(test, Mapping)
            ] != expected_baseline or [test.get("passed") for test in tests if isinstance(test, Mapping)] != [False, False, True]:
                raise CopilotQualificationError("baseline helper outcomes differ from the frozen initial checkout")
        elif phase == "final":
            tests = body.get("tests") if isinstance(body, Mapping) else None
            if not isinstance(tests, list) or [
                (test.get("actual"), test.get("expected")) for test in tests if isinstance(test, Mapping)
            ] != [(30, 30), (50, 50), (54, 54)] or any(
                not isinstance(test, Mapping) or test.get("passed") is not True for test in tests
            ):
                raise CopilotQualificationError("final helper results do not pass every declared case")
        helper_summaries[phase] = {"exit_code": exit_code, "nonce": nonce}

    initial = _read_json(root, "filesystem/initial-state.json")
    after = _read_json(root, "filesystem/after-state.json")
    initial_files = initial.get("files") if isinstance(initial, Mapping) else None
    after_files = after.get("files") if isinstance(after, Mapping) else None
    if not isinstance(initial_files, Mapping) or not isinstance(after_files, Mapping):
        raise CopilotQualificationError("workspace before/after manifests are invalid")
    expected_after = set(initial_files) - {".survival-observer.jsonl"} | {".survival-observer.jsonl"}
    if set(after_files) != expected_after or initial_files.get(".survival-observer.jsonl", {}).get("existed_before_first_turn") is not False:
        raise CopilotQualificationError("workspace file population differs from declared capture")
    workspace = root / "workspace/fixture_project"
    if workspace.is_symlink() or not workspace.is_dir():
        raise CopilotQualificationError("fixture workspace must be an ordinary directory")
    actual_files = {}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise CopilotQualificationError("fixture workspace contains a symlink")
        if path.is_file():
            actual_files[path.relative_to(workspace).as_posix()] = _sha(path.read_bytes())
    if set(actual_files) != set(after_files) or any(
        actual_files[name] != _manifest_hash(after_files[name]) for name in actual_files
    ):
        raise CopilotQualificationError("after-state manifest differs from the actual fixture workspace")
    fixture_root = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload/fixture_project"
    fixture_files = {
        path.relative_to(fixture_root).as_posix(): _sha(path.read_bytes())
        for path in fixture_root.rglob("*") if path.is_file() and not path.is_symlink()
    }
    declared_initial = {name for name in initial_files if name != ".survival-observer.jsonl"}
    if declared_initial != set(fixture_files) or any(
        _manifest_hash(initial_files[name]) != fixture_files[name] for name in fixture_files
    ):
        raise CopilotQualificationError("initial-state manifest differs from the frozen workload fixture")
    changed = []
    for name in sorted(initial_files):
        if name == ".survival-observer.jsonl":
            continue
        before_sha = _manifest_hash(initial_files[name])
        after_sha = _manifest_hash(after_files.get(name))
        if before_sha != after_sha:
            changed.append(name)
    if changed != ["checkout.py"]:
        raise CopilotQualificationError("workspace changed paths are not exactly checkout.py")
    helper_source = fixture_root / "bench_check.py"
    if _sha(helper_source.read_bytes()) != _manifest_hash(after_files.get("bench_check.py")):
        raise CopilotQualificationError("workspace helper differs from the frozen fixture helper")

    native_call_count = sum(event.kind == "tool_call" for event in decoded.events)
    native_result_count = sum(event.kind == "tool_result" for event in decoded.events)
    native_error_count = sum(event.kind == "error" for event in decoded.events)
    if native_call_count != len(requested) or native_result_count + native_error_count != len(completed):
        raise CopilotQualificationError("native normalization duplicates or loses tool lifecycle records")

    hashes = {
        native_relative: {"sha256": _sha(native_bytes), "size_bytes": len(native_bytes)},
        "workload_instance.json": {"sha256": _sha(_file(root, "workload_instance.json")), "size_bytes": len(_file(root, "workload_instance.json"))},
        "workspace/fixture_project/.survival-observer.jsonl": {"sha256": _sha(helper_bytes), "size_bytes": len(helper_bytes)},
    }
    return {
        "schema_version": "session-bench-copilot-capture-qualification-v1",
        "configuration_id": "copilot",
        "run_id": run_id,
        "session_id": decoded.session_id,
        "cli_version": decoded.version,
        "model_route": (starts[0].get("data") or {}).get("selectedModel"),
        "native_format": decoded.format_id,
        "native_event_count": len(native_rows),
        "submitted_turns": len(users),
        "visible_response_canaries": list(response_canaries),
        "native_tool_calls": native_call_count,
        "native_tool_results": native_result_count,
        "native_tool_errors": native_error_count,
        "helper_phases": helper_summaries,
        "workspace_changed_paths": changed,
        "stdout": stdout_hashes,
        "hashes": hashes,
        "scope": "offline_native_observer_capture_consistency",
        "score_eligible": False,
        "independent_reproduction": False,
        "public_safe": False,
    }
