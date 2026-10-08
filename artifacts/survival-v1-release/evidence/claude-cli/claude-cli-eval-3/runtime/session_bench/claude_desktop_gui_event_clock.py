"""Private capture-time clock for four Claude Desktop turn/response boundaries.

User turns are stamped by a local ``UserPromptSubmit`` hook before Claude
processes the submitted prompt. Displayed responses are stamped immediately
after a local accessibility observation shows completion. Neither clock comes
from Claude's native session timestamps, and prompt/response text is not kept.
"""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Callable

SCHEMA = "claude-desktop-gui-event-clock-v1"
TURN_PROVENANCE = "local_user_prompt_submit_hook_clock"
RESPONSE_PROVENANCE = "local_accessibility_response_observation_clock"
PROVENANCE = RESPONSE_PROVENANCE
EVENT_KINDS = {
    "turn-r1": "user_turn",
    "response-r1": "assistant_response",
    "turn-r2": "user_turn",
    "response-r2": "assistant_response",
}
EVENT_PROVENANCE = {
    "turn-r1": TURN_PROVENANCE,
    "response-r1": RESPONSE_PROVENANCE,
    "turn-r2": TURN_PROVENANCE,
    "response-r2": RESPONSE_PROVENANCE,
}
EVENT_ORDER = tuple(EVENT_KINDS)
MAX_LEDGER_BYTES = 16_384
_RUN_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_PASTED_CONTEXT = re.compile(
    r'\A\n+<pasted_context id="[0-9]+">\n(?P<body>.*?)\n</pasted_context>\n+\Z',
    re.DOTALL,
)
_FIELDS = frozenset({"schema", "run_id", "event_id", "event_kind",
                     "gui_observed_at", "timestamp_provenance"})


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse_row(raw: bytes) -> dict[str, Any]:
    try:
        row = json.loads(raw, object_pairs_hook=_pairs,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed GUI clock JSON") from exc
    if not isinstance(row, dict):
        raise ValueError("GUI clock row must be an object")
    return row


def _run_id(value: str) -> str:
    if not isinstance(value, str) or not _RUN_ID.fullmatch(value):
        raise ValueError("invalid run_id")
    return value


def _utc_stamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _prompt_body(prompt: str) -> str:
    """Remove one exact Claude Desktop pasted-context transport envelope."""
    if "<pasted_context" not in prompt and "</pasted_context>" not in prompt:
        return prompt
    match = _PASTED_CONTEXT.fullmatch(prompt)
    if match is None:
        raise ValueError("submitted prompt has a malformed pasted-context wrapper")
    body = match.group("body")
    if "<pasted_context" in body or "</pasted_context>" in body:
        raise ValueError("submitted prompt has nested or multiple pasted-context wrappers")
    return body


def _validate_rows(rows: list[dict[str, Any]], run_id: str, *, require_complete: bool) -> None:
    if not rows or len(rows) > len(EVENT_ORDER):
        raise ValueError("GUI clock ledger has an invalid row count")
    if require_complete and len(rows) != len(EVENT_ORDER):
        raise ValueError("GUI clock ledger is incomplete")
    previous: datetime | None = None
    for index, row in enumerate(rows):
        event_id = EVENT_ORDER[index]
        if set(row) != _FIELDS or row.get("schema") != SCHEMA or row.get("run_id") != run_id:
            raise ValueError("GUI clock row schema, fields, or run identity mismatch")
        if row.get("event_id") != event_id or row.get("event_kind") != EVENT_KINDS[event_id]:
            raise ValueError("GUI clock event is duplicate, out of order, or has wrong kind")
        if row.get("timestamp_provenance") != EVENT_PROVENANCE[event_id]:
            raise ValueError("GUI clock timestamp provenance mismatch")
        stamp = row.get("gui_observed_at")
        if not isinstance(stamp, str) or not _STAMP.fullmatch(stamp):
            raise ValueError("GUI clock timestamp is not canonical UTC")
        try:
            observed = datetime.fromisoformat(stamp[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("invalid GUI clock timestamp") from exc
        if previous is not None and observed < previous:
            raise ValueError("GUI clock moves backwards")
        previous = observed


def _open_private(path: Path, *, create: bool) -> int:
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("GUI clock ledger path must be absolute")
    flags = (os.O_RDWR | os.O_APPEND | os.O_CREAT) if create else os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("GUI clock ledger must be a private ordinary file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _latch_private_session_id(path: Path, session_id: str) -> None:
    """Create or verify a private exact-session allowlist for the OTel receiver."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("session-id file path must be absolute")
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        raise ValueError("invalid session_id")
    encoded = (session_id + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, read_flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 1024):
                raise ValueError("session-id file must be a private ordinary file")
            existing = os.read(fd, 1025)
            if existing != encoded:
                raise ValueError("session-id file is already bound to another session")
        finally:
            os.close(fd)
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("session-id file must be a private ordinary file")
        if os.write(fd, encoded) != len(encoded):
            raise OSError("short session-id file write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_locked(fd: int, run_id: str, *, require_complete: bool) -> tuple[bytes, list[dict[str, Any]]]:
    info = os.fstat(fd)
    if info.st_size > MAX_LEDGER_BYTES:
        raise ValueError("GUI clock ledger is too large")
    os.lseek(fd, 0, os.SEEK_SET)
    raw = os.read(fd, MAX_LEDGER_BYTES + 1)
    if not raw or len(raw) > MAX_LEDGER_BYTES or not raw.endswith(b"\n"):
        raise ValueError("GUI clock ledger is empty, too large, or incomplete")
    rows = [_parse_row(line) for line in raw.splitlines()]
    _validate_rows(rows, run_id, require_complete=require_complete)
    return raw, rows


def record_gui_event(
    *, run_id: str, event_id: str, event_kind: str, ledger_path: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, str]:
    """Append exactly one observed boundary; capture the clock before validation/I/O."""
    stamp = _utc_stamp(clock())
    run_id = _run_id(run_id)
    if event_id not in EVENT_KINDS or event_kind != EVENT_KINDS[event_id]:
        raise ValueError("unsupported GUI event ID or kind")
    row = {"schema": SCHEMA, "run_id": run_id, "event_id": event_id,
           "event_kind": event_kind, "gui_observed_at": stamp,
           "timestamp_provenance": EVENT_PROVENANCE[event_id]}
    fd = _open_private(ledger_path, create=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if os.fstat(fd).st_size:
            _, rows = _read_locked(fd, run_id, require_complete=False)
        else:
            rows = []
        if len(rows) >= len(EVENT_ORDER) or event_id != EVENT_ORDER[len(rows)]:
            raise ValueError("GUI event is duplicate or out of order")
        if rows and stamp < rows[-1]["gui_observed_at"]:
            raise ValueError("GUI clock moves backwards")
        encoded = (json.dumps(row, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if os.write(fd, encoded) != len(encoded):
            raise OSError("short GUI clock ledger write")
        os.fsync(fd)
    finally:
        os.close(fd)
    return row


def record_user_prompt_submit_event(
    raw_event: bytes, *, run_id: str, workload: dict[str, Any],
    workspace: Path, ledger_path: Path, session_id_file: Path | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, str]:
    """Stamp the exact synthetic prompt at Claude's pre-processing hook."""
    stamp = clock()
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    try:
        event = _parse_row(raw_event)
    except ValueError as exc:
        raise ValueError("malformed prompt-submit hook event") from exc
    workspace = Path(workspace).resolve(strict=True)
    if (event.get("hook_event_name") != "UserPromptSubmit"
            or event.get("cwd") != str(workspace)
            or not isinstance(event.get("session_id"), str)
            or not event["session_id"]):
        raise ValueError("prompt-submit hook identity mismatch")
    if workload.get("run_id") != run_id:
        raise ValueError("prompt-submit workload identity mismatch")
    prompt = event.get("prompt")
    turns = workload.get("turns")
    if not isinstance(prompt, str) or not isinstance(turns, list) or len(turns) != 2:
        raise ValueError("prompt-submit event or workload turns are malformed")
    prompt = _prompt_body(prompt)
    matched = [turn for turn in turns if isinstance(turn, dict) and turn.get("text") == prompt]
    if len(matched) != 1:
        raise ValueError("submitted prompt does not uniquely match the synthetic workload")
    turn_id = matched[0].get("id")
    if turn_id not in {"turn-r1", "turn-r2"}:
        raise ValueError("submitted workload turn ID is unsupported")
    if session_id_file is not None:
        _latch_private_session_id(session_id_file, event["session_id"])
    return record_gui_event(
        run_id=run_id, event_id=turn_id, event_kind="user_turn",
        ledger_path=ledger_path, clock=lambda: stamp,
    )


def load_gui_event_clock(
    ledger_path: Path, *, run_id: str, require_complete: bool = True,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Read and verify a private ledger for one run; return raw bytes and rows."""
    run_id = _run_id(run_id)
    fd = _open_private(ledger_path, create=False)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        return _read_locked(fd, run_id, require_complete=require_complete)
    finally:
        os.close(fd)
