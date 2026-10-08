"""Opt-in, capture-time Claude Desktop hook receipts.

The timestamp is observed when this hook process receives its stdin event. It
is not a timestamp emitted by Claude, a transcript, or the Desktop store.
Configure this module separately for PreToolUse, PostToolUse, and
PostToolUseFailure; each invocation appends one receipt to the same JSONL file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
from typing import Any, Callable

EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})
SCHEMA = "claude-desktop-hook-observer-v1"
PROJECTION_SCHEMA = "claude-desktop-command-projection-receipt-v1"
MAX_EVENT_BYTES = 2_000_000
_HELPER_PHASES = frozenset({"inspect", "baseline", "final"})
_SHELL_SEPARATORS = frozenset({";", "&&"})


_SIMPLE_OUTPUT_EXPANSION = re.compile(
    r"\$(?:[A-Za-z_][A-Za-z0-9_]*|[?@*#0-9])|\$\{[A-Za-z_][A-Za-z0-9_]*\}"
)


def _output_expansions_are_simple(argv: list[str]) -> bool:
    """Allow only read-only, simple parameter expansion in echo/printf args."""
    for token in argv[1:]:
        if "$" not in token:
            continue
        # Command/arithmetic substitution and parameter operators stay outside
        # the proof grammar.  These simple forms can only affect displayed
        # output; they cannot change which workload command executes.
        if "$" in _SIMPLE_OUTPUT_EXPANSION.sub("", token):
            return False
    return True


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError(f"nonfinite JSON number: {value}")


def _json_object(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_EVENT_BYTES:
        raise ValueError("hook event is empty or too large")
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs, parse_constant=_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed hook JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("hook event must be a JSON object")
    return value


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _target(tool_input: Any, workspace: Path, fixture_root: Path) -> dict[str, str]:
    if not isinstance(tool_input, dict):
        raise ValueError("tool_input must be an object")
    paths = [tool_input[key] for key in ("file_path", "path") if key in tool_input]
    if len(paths) > 1 and paths[0] != paths[1]:
        raise ValueError("conflicting tool paths")
    if paths:
        raw = _required(paths[0], "tool path")
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
        if not resolved.is_relative_to(fixture_root) or resolved == fixture_root:
            raise ValueError("tool path escapes fixture root")
        return {"fixture_relative_target": resolved.relative_to(fixture_root).as_posix()}
    commands = [tool_input[key] for key in ("command", "cmd") if key in tool_input]
    if len(commands) > 1 and commands[0] != commands[1]:
        raise ValueError("conflicting tool commands")
    if commands:
        return {"command_sha256": hashlib.sha256(_required(commands[0], "command").encode()).hexdigest()}
    raise ValueError("tool_input has no supported path or command")


def _validate_ledger(rows: list[dict[str, Any]], identity: dict[str, str]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("schema") != SCHEMA or any(row.get(k) != v for k, v in identity.items()):
            raise ValueError("receipt ledger identity or schema mismatch")
        kind, call = row.get("event_type"), row.get("tool_use_id")
        _required(call, "ledger tool_use_id")
        if kind == "PreToolUse":
            if call in calls or row.get("result_status") != "pending":
                raise ValueError("duplicate or malformed PreToolUse receipt")
            calls[call] = row
        elif kind in {"PostToolUse", "PostToolUseFailure"}:
            start = calls.get(call)
            if start is None or start.get("completed") or row.get("tool_name") != start.get("tool_name"):
                raise ValueError("out-of-order or duplicate completion receipt")
            if any(row.get(k) != start.get(k) for k in ("fixture_relative_target", "command_sha256")):
                raise ValueError("completion target differs from start")
            if row.get("result_status") != ("success" if kind == "PostToolUse" else "failure"):
                raise ValueError("completion status mismatch")
            calls[call] = {**start, "completed": True}
        else:
            raise ValueError("unsupported receipt event type")
    return calls


def _projection_for_input(
    tool_name: str, tool_input: Any, *, command_sha256: str | None,
    fixture_relative_target: str | None, run_canary: str,
) -> dict[str, Any]:
    """Project only safe workload semantics; never retain the submitted input."""
    base: dict[str, Any] = {
        "projection_state": "unsupported", "unsupported_reason": "unsupported_tool_or_input",
    }
    if tool_name == "Bash" and isinstance(tool_input, dict):
        command = tool_input.get("command", tool_input.get("cmd"))
        if not isinstance(command, str) or not command_sha256:
            return base
        # This is deliberately a small proof parser, not a shell interpreter.
        # Newlines, expansion, pipes, redirection and control flow can alter
        # which commands execute, so their projections remain unsupported.
        if any(char in command for char in "\r\n`\\"):
            return {**base, "unsupported_reason": "ambiguous_shell_input"}
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError:
            return {**base, "unsupported_reason": "unparseable_shell_input"}
        if any(token in {"|", "||", "&", "<", ">", "<<", ">>", "(", ")"} for token in tokens):
            return {**base, "unsupported_reason": "ambiguous_shell_input"}
        commands: list[list[str]] = []
        segment: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if not segment:
                    return {**base, "unsupported_reason": "ambiguous_shell_input"}
                commands.append(segment)
                segment = []
            else:
                segment.append(token)
        if tokens and not segment:
            return {**base, "unsupported_reason": "ambiguous_shell_input"}
        if segment:
            commands.append(segment)
        phases: list[str] = []
        edits: list[bool] = []
        for argv in commands:
            executable = argv[0]
            helper = None
            args = argv[1:]
            if executable in {"echo", "printf"}:
                if not _output_expansions_are_simple(argv):
                    return {**base, "unsupported_reason": "ambiguous_shell_input"}
            elif any("$" in token for token in argv):
                return {**base, "unsupported_reason": "ambiguous_shell_input"}
            if executable in {"python", "python3"} and args:
                helper, args = args[0], args[1:]
            elif executable in {"bench_check.py", "./bench_check.py"}:
                helper = executable
            if helper is not None:
                if helper not in {"bench_check.py", "./bench_check.py"} or not args or args[0] not in _HELPER_PHASES:
                    return {**base, "unsupported_reason": "ambiguous_shell_input"}
                phase = args[0]
                options = args[1:]
                canaries = []
                for index, value in enumerate(options):
                    if value == "--run-canary" and index + 1 < len(options):
                        canaries.append(options[index + 1])
                    elif value.startswith("--run-canary="):
                        canaries.append(value.split("=", 1)[1])
                if canaries != [run_canary]:
                    return {**base, "unsupported_reason": "helper_run_canary_unproven"}
                phases.append(phase)
                edits.append(False)
            elif executable == "apply_patch" and args == ["checkout.py"]:
                edits.append(True)
            elif executable in {"echo", "printf", "true", ":"}:
                edits.append(False)
            else:
                return {**base, "unsupported_reason": "ambiguous_shell_input"}
        # A quoted canary is one token. Keep the normalized value, never the
        # original command or its other arguments.
        helper_phases = [
            {"phase": phase, "argv": ["python3", "bench_check.py", phase],
             "run_canary": run_canary}
            for phase in phases
        ]
        compound = bool(phases and phases[-1] == "final" and any(edits))
        first = phases[0] if phases else None
        return {
            "projection_state": "supported",
            "command_sha256": command_sha256,
            "cwd": "fixture_project",
            "action_kind": ("inspect" if first == "inspect" else
                            "test" if first in {"baseline", "final"} else "shell"),
            "argv": ["python3", "bench_check.py", first] if first else None,
            "target": "fixture_project/checkout.py" if phases else None,
            "helper_phases": helper_phases,
            "compound_edit": compound,
            "compound_edit_target": "fixture_project/checkout.py" if compound else None,
        }
    if (tool_name in {"Edit", "Write"} and isinstance(tool_input, dict)
            and fixture_relative_target == "checkout.py"):
        return {
            "projection_state": "supported", "cwd": "fixture_project",
            "action_kind": "edit", "argv": ["replace_function", "fixture_project/checkout.py"],
            "target": "fixture_project/checkout.py", "helper_phases": [],
            "compound_edit": False, "compound_edit_target": None,
        }
    if tool_name not in {"Bash", "Edit", "Write"}:
        return {**base, "unsupported_reason": "unsupported_tool_name"}
    return base


def _validate_projection_ledger(
    rows: list[dict[str, Any]], identity: dict[str, str],
) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get("schema") != PROJECTION_SCHEMA
                or any(row.get(key) != value for key, value in identity.items())):
            raise ValueError("command projection receipt identity or schema mismatch")
        if row.get("timestamp_provenance") != "local_hook_receipt_clock":
            raise ValueError("command projection clock provenance mismatch")
        stamp = row.get("observed_at")
        if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", stamp):
            raise ValueError("command projection timestamp is malformed")
        try:
            observed = datetime.fromisoformat(stamp[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("command projection timestamp is invalid") from exc
        call, kind = _required(row.get("call_id"), "projection call_id"), row.get("event_type")
        tool = _required(row.get("tool_name"), "projection tool name")
        if kind == "PreToolUse":
            if call in calls or row.get("projection_state") not in {"supported", "unsupported"}:
                raise ValueError("duplicate or malformed command projection start")
            calls[call] = {**row, "completed": False, "_observed": observed}
        elif kind in {"PostToolUse", "PostToolUseFailure"}:
            start = calls.get(call)
            if start is None or start["completed"] or tool != start["tool_name"]:
                raise ValueError("command projection completion lacks its unique start")
            if observed < start["_observed"]:
                raise ValueError("command projection lifecycle clock moves backwards")
            expected_status = "success" if kind == "PostToolUse" else "failure"
            if row.get("result_status") != expected_status:
                raise ValueError("command projection result status mismatch")
            _required(row.get("result_sha256"), "projection result digest")
            if not re.fullmatch(r"[0-9a-f]{64}", row["result_sha256"]):
                raise ValueError("command projection result digest is malformed")
            start.update({"result_status": expected_status,
                          "result_sha256": row["result_sha256"],
                          "result_observed_at": stamp, "completed": True})
        else:
            raise ValueError("unsupported command projection event")
    return calls


def _append_projection(path: Path, row: dict[str, Any], identity: dict[str, str]) -> None:
    path = Path(path)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("command projection file must be private and regular")
        with os.fdopen(os.dup(fd), "rb") as stream:
            existing = stream.read()
        if existing and not existing.endswith(b"\n"):
            raise ValueError("incomplete command projection ledger")
        rows = [_json_object(line) for line in existing.splitlines()]
        calls = _validate_projection_ledger(rows, identity)
        call = row["call_id"]
        if row["event_type"] == "PreToolUse":
            if call in calls:
                raise ValueError("duplicate command projection call")
        else:
            start = calls.get(call)
            if start is None or start["completed"] or row["tool_name"] != start["tool_name"]:
                raise ValueError("command projection completion has no unique start")
            if row["observed_at"] < start["observed_at"]:
                raise ValueError("command projection completion precedes start")
            row["projection_state"] = start["projection_state"]
        encoded = (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        if os.write(fd, encoded) != len(encoded):
            raise OSError("short command projection write")
        os.fsync(fd)
    finally:
        os.close(fd)


def observe_hook(
    raw_event: bytes,
    *,
    run_id: str,
    session_id: str,
    workspace: Path,
    fixture_root: Path,
    receipts_path: Path,
    projection_receipts_path: Path | None = None,
    run_canary: str | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Validate one native hook event and atomically append a private receipt.

    The run ID is caller-supplied capture context; Claude's hook JSON carries
    the session ID and cwd, which must exactly match the caller's expectations.
    """
    observed = clock()  # before JSON parsing or any ledger I/O
    if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("clock must provide an aware datetime")
    timestamp = observed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    run_id = _required(run_id, "run_id")
    session_id = _required(session_id, "session_id")
    workspace = Path(workspace).resolve(strict=True)
    fixture_root = Path(fixture_root).resolve(strict=True)
    if not workspace.is_dir() or not fixture_root.is_dir() or not fixture_root.is_relative_to(workspace):
        raise ValueError("fixture root must be a directory inside workspace")
    event = _json_object(raw_event)
    kind = event.get("hook_event_name")
    if kind not in EVENTS:
        raise ValueError("unsupported hook event")
    if event.get("session_id") != session_id or event.get("cwd") != str(workspace):
        raise ValueError("hook session or workspace mismatch")
    if "run_id" in event and event["run_id"] != run_id:
        raise ValueError("hook run mismatch")
    call = _required(event.get("tool_use_id"), "tool_use_id")
    tool = _required(event.get("tool_name"), "tool_name")
    target = _target(event.get("tool_input"), workspace, fixture_root) if "tool_input" in event else None
    if kind == "PreToolUse" and target is None:
        raise ValueError("PreToolUse requires tool_input")
    if kind == "PreToolUse" and "duration_ms" in event:
        raise ValueError("PreToolUse cannot have duration_ms")
    duration = event.get("duration_ms")
    if "duration_ms" in event and (type(duration) not in (int, float) or not 0 <= duration < float("inf")):
        raise ValueError("invalid duration_ms")
    status = {"PreToolUse": "pending", "PostToolUse": "success", "PostToolUseFailure": "failure"}[kind]
    result = None
    if kind == "PostToolUse":
        if "tool_response" not in event:
            raise ValueError("PostToolUse requires tool_response")
        result = event["tool_response"]
    elif kind == "PostToolUseFailure":
        if "error" not in event:
            raise ValueError("PostToolUseFailure requires error")
        result = event["error"]
    identity = {"run_id": run_id, "session_id": session_id, "workspace": str(workspace)}
    receipt: dict[str, Any] = {
        "schema": SCHEMA, **identity, "tool_use_id": call, "event_type": kind,
        "tool_name": tool, "hook_observed_at": timestamp,
        "timestamp_provenance": "local_hook_receipt_clock",
        "result_status": status, "result_sha256": _digest(result) if kind != "PreToolUse" else None,
    }
    if target:
        receipt.update(target)
    if "duration_ms" in event:
        receipt["duration_ms"] = duration
    semantic_projection = None
    if projection_receipts_path is not None and kind == "PreToolUse":
        canary = _required(run_canary, "run_canary")
        semantic_projection = _projection_for_input(
            tool, event.get("tool_input"),
            command_sha256=(target or {}).get("command_sha256"),
            fixture_relative_target=(target or {}).get("fixture_relative_target"),
            run_canary=canary,
        )
        receipt["command_projection_schema"] = PROJECTION_SCHEMA
        receipt["command_projection_sha256"] = _digest(semantic_projection)
    path = Path(receipts_path)
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("receipt file must be private and regular")
        with os.fdopen(os.dup(fd), "rb") as stream:
            existing = stream.read()
        if existing and not existing.endswith(b"\n"):
            raise ValueError("incomplete receipt ledger")
        rows = [_json_object(line) for line in existing.splitlines()]
        calls = _validate_ledger(rows, identity)
        if kind == "PreToolUse":
            if call in calls:
                raise ValueError("duplicate tool_use_id")
        else:
            start = calls.get(call)
            if start is None or start.get("completed"):
                raise ValueError("completion without unique open PreToolUse")
            if tool != start["tool_name"]:
                raise ValueError("completion tool differs from start")
            start_target = {key: start[key] for key in ("fixture_relative_target", "command_sha256") if key in start}
            if target is not None and target != start_target:
                raise ValueError("completion target differs from start")
            receipt.update(start_target)
        encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        if os.write(fd, encoded) != len(encoded):
            raise OSError("short receipt write")
        os.fsync(fd)
    finally:
        os.close(fd)
    if projection_receipts_path is not None:
        canary = _required(run_canary, "run_canary")
        projection: dict[str, Any] = {
            "schema": PROJECTION_SCHEMA, **identity,
            "call_id": call, "event_type": kind, "tool_name": tool,
            "observed_at": timestamp,
            "timestamp_provenance": "local_hook_receipt_clock",
        }
        if kind == "PreToolUse":
            projection.update(semantic_projection)
        else:
            projection.update({"result_status": status,
                               "result_sha256": receipt["result_sha256"]})
        _append_projection(Path(projection_receipts_path), projection, identity)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append one Claude Desktop hook receipt from stdin")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--projection-receipts", type=Path)
    parser.add_argument("--run-canary")
    args = parser.parse_args(argv)
    if (args.projection_receipts is None) != (args.run_canary is None):
        parser.error("--projection-receipts and --run-canary must be supplied together")
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    observe_hook(raw, run_id=args.run_id, session_id=args.session_id,
                 workspace=args.workspace, fixture_root=args.fixture_root,
                 receipts_path=args.receipts,
                 projection_receipts_path=args.projection_receipts,
                 run_canary=args.run_canary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
