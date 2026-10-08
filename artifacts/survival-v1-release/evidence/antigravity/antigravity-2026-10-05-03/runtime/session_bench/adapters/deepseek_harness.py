"""Bounded DeepSeek Harness (DSH) capture preflight and native inventory.

This module does not launch DSH by itself and does not semantically decode a
session. Callers supply static executable identity and an injected runner. The
inventory stage accepts one fresh v4 plain JSONL or independently framed
Zstandard artifact. Unrecognized event types remain explicit unsupported
states; semantic qualification is performed separately by dsh_live.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Protocol, TypeAlias


SURFACE = "deepseek-harness-cli"
DEFAULT_EXECUTABLE = "dsh"
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_DISCOVERED_ENTRIES = 10_000

# Released v4 envelope vocabulary, verified against AS FormatTypes.swift and
# installed DSH. Admission here inventories envelopes, not payload semantics.
FIXTURE_V4_EVENT_TYPES = frozenset(
    {
        "turn/start",
        "step/start",
        "system/message",
        "user/message",
        "request/header",
        "session-log-deepseek/delivery-accepted",
        "assistant/message",
        "tool/call",
        "tool/result",
        "step/end",
        "turn/end",
        "agent-preset/selected", "agent/inbox/spliced", "approval/asked", "approval/decided",
        "approval/policy", "assistant/attempt", "command/done", "command/run",
        "compaction/end", "compaction/prune", "compaction/start", "compaction/summary",
        "deliverables/presented", "feedback/message-delete", "feedback/message-put",
        "feedback/record", "goal/change", "hook/invoked", "hook/result", "image/offload",
        "llm/retry", "llm/retry-started", "model/selection", "permission/preset", "plan/mode",
        "request/context", "sandbox/mode", "schedule/change", "session/end-seed", "session/title",
        "session/title-llm-request", "subagent/catalog", "subagent/descriptor",
        "subagent/model-selection-policy", "team/member", "team/message/delivered",
        "team/message/queued", "team/task", "todo/write", "tool-workflow/agent-end",
        "tool-workflow/agent-start", "tool-workflow/run-end", "tool-workflow/run-start",
        "tool/ptc-dispatch", "tool/ptc-dispatch-start", "web/deepseek-search-llm-request",
        "workspace/changes", "developer/message",
    }
)


class DeepSeekHarnessError(RuntimeError):
    """Base error for a refused DSH adapter operation."""


class PreflightError(DeepSeekHarnessError, ValueError):
    """Raised when isolation or static product identity is insufficient."""


class NativeInventoryError(DeepSeekHarnessError, ValueError):
    """Raised when native bytes are malformed or violate inventory bounds."""


class StaticIdentityProbe(Protocol):
    def probe(self, *, executable: str) -> Mapping[str, Any]: ...


IdentityProbeInput: TypeAlias = Mapping[str, Any] | Callable[..., Mapping[str, Any]] | StaticIdentityProbe


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path) -> Any: ...


@dataclass(frozen=True)
class DSHIdentity:
    product: str
    version: str
    executable: str = DEFAULT_EXECUTABLE


@dataclass(frozen=True)
class HeadlessLaunchPlan:
    identity: DSHIdentity
    run_root: Path
    workspace: Path
    dsh_home: Path
    native_root: Path
    argv: tuple[str, ...]
    env: Mapping[str, str]
    model_started: bool = False


@dataclass(frozen=True)
class NativeEventBoundary:
    sequence: int
    time: int
    event_type: str
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class NativeInventory:
    status: str
    surface: str
    relative_path: str | None
    size_bytes: int | None
    sha256: str | None
    header: Mapping[str, Any] | None
    event_boundaries: tuple[NativeEventBoundary, ...]
    raw_records: tuple[Mapping[str, Any], ...]
    issues: tuple[str, ...]


def _absolute(path: Path | str, label: str) -> Path:
    if not isinstance(path, (Path, str)) or not str(path) or "\x00" in str(path):
        raise PreflightError(f"{label} must be a non-empty path")
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise PreflightError(f"{label} must be absolute")
    lexical = Path(os.path.abspath(value))
    resolved = value.resolve(strict=False)
    if lexical != resolved:
        raise PreflightError(f"{label} may not traverse a symlink")
    return lexical


def _is_child(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _probe_identity(source: IdentityProbeInput | None) -> DSHIdentity:
    if source is None:
        raise PreflightError("a static DSH identity probe is required")
    if isinstance(source, Mapping):
        raw: Mapping[str, Any] = source
    elif hasattr(source, "probe"):
        raw = source.probe(executable=DEFAULT_EXECUTABLE)
    elif callable(source):
        raw = source(executable=DEFAULT_EXECUTABLE)
    else:
        raise PreflightError("identity probe must be a mapping, callable, or probe object")
    if not isinstance(raw, Mapping):
        raise PreflightError("static identity probe must return a mapping")
    sensitive = ("token", "secret", "credential", "password", "cookie", "history", "prompt")
    if any(any(marker in str(key).lower() for marker in sensitive) for key in raw):
        raise PreflightError("static identity may not include credentials, history, or prompt data")
    product = raw.get("product", "DeepSeek Harness")
    version = raw.get("version")
    if not isinstance(product, str) or not product.strip():
        raise PreflightError("static identity is missing product")
    if not isinstance(version, str) or not version.strip():
        raise PreflightError("static identity is missing version")
    return DSHIdentity(product.strip(), version.strip())


def build_headless_launch_plan(
    run_root: Path | str,
    workspace: Path | str,
    *,
    identity_probe: IdentityProbeInput | None,
    executable: str = DEFAULT_EXECUTABLE,
) -> HeadlessLaunchPlan:
    """Build an isolated launch plan without invoking DSH or a model.

    ``run_root`` must be a real directory containing the synthetic workspace. DSH_HOME and its
    ``sessions`` root must not exist yet. The caller is responsible for
    provisioning any approved authentication inside the isolated DSH_HOME.
    """

    if executable != DEFAULT_EXECUTABLE:
        raise PreflightError("the DSH executable name must be the declared dsh command")
    root = _absolute(run_root, "run_root")
    home = Path.home().resolve(strict=False)
    if root == Path(root.anchor) or root == home or _is_child(home, root):
        raise PreflightError("run_root cannot be a filesystem root or contain the normal user home")
    if root.is_symlink() or not root.is_dir():
        raise PreflightError("run_root must be an existing ordinary directory")
    project = _absolute(workspace, "workspace")
    if not _is_child(project, root) or project.is_symlink() or not project.is_dir():
        raise PreflightError("workspace must be an existing ordinary directory inside run_root")

    dsh_home = root / "dsh-home"
    native_root = dsh_home / "sessions"
    if dsh_home.exists() or dsh_home.is_symlink():
        raise PreflightError("isolated DSH_HOME must be fresh and absent before launch")
    identity = _probe_identity(identity_probe)
    return HeadlessLaunchPlan(
        identity=identity,
        run_root=root,
        workspace=project,
        dsh_home=dsh_home,
        native_root=native_root,
        argv=(executable, "--profile", "headless", "--json"),
        env={"DSH_HOME": str(dsh_home)},
    )


def run_headless(
    plan: HeadlessLaunchPlan, task: str, *, runner: Runner | Callable[..., Any],
    resume_session_id: str | None = None,
) -> Any:
    """Invoke the injected runner, optionally continuing an observed R1 session.

    The caller must obtain a resume ID from the independent first-turn observer
    and verify it against the native inventory. It must never guess an ID or
    treat a second new session as R2. This function makes no such observation.
    """

    if not isinstance(plan, HeadlessLaunchPlan) or plan.model_started:
        raise PreflightError("a valid static DSH launch plan is required")
    if not isinstance(task, str) or not task.strip():
        raise PreflightError("task must be non-empty")
    if task.lstrip().startswith("-"):
        raise PreflightError("task must not begin with an option prefix")
    root = _absolute(plan.run_root, "run_root")
    workspace = _absolute(plan.workspace, "workspace")
    dsh_home = _absolute(plan.dsh_home, "dsh_home")
    if not _is_child(workspace, root) or dsh_home != root / "dsh-home" or plan.native_root != dsh_home / "sessions":
        raise PreflightError("launch plan isolation paths changed")
    if plan.argv != (DEFAULT_EXECUTABLE, "--profile", "headless", "--json") or dict(plan.env) != {"DSH_HOME": str(dsh_home)}:
        raise PreflightError("launch plan command or environment changed")
    resume = ()
    if resume_session_id is not None:
        if not isinstance(resume_session_id, str) or not resume_session_id or len(resume_session_id) > 512 or resume_session_id.startswith("-") or any(char.isspace() or ord(char) < 32 for char in resume_session_id):
            raise PreflightError("resume session id must be an observed nonempty identifier")
        resume = ("--session-id", resume_session_id)
    argv = plan.argv + resume + (task,)
    if hasattr(runner, "run"):
        return runner.run(argv, env=dict(plan.env), cwd=plan.workspace)
    if callable(runner):
        return runner(argv, env=dict(plan.env), cwd=plan.workspace)
    raise PreflightError("runner must be injected")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NativeInventoryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_line(line: bytes, line_number: int) -> Mapping[str, Any]:
    try:
        decoded = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                NativeInventoryError(f"non-finite JSON number: {value}")
            ),
        )
    except NativeInventoryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NativeInventoryError(f"invalid JSONL at line {line_number}") from error
    if not isinstance(decoded, dict):
        raise NativeInventoryError(f"JSONL line {line_number} is not an object")
    return decoded


def _unsupported_inventory(path: str | None, *, size: int | None, digest: str | None, issue: str) -> NativeInventory:
    return NativeInventory(
        status="unsupported",
        surface=SURFACE,
        relative_path=path,
        size_bytes=size,
        sha256=digest,
        header=None,
        event_boundaries=(),
        raw_records=(),
        issues=(issue,),
    )


def _read_bounded_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise NativeInventoryError("native artifact could not be opened without following links") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NativeInventoryError("native artifact is not a regular file")
        if before.st_size < 0 or before.st_size > MAX_ARTIFACT_BYTES:
            raise NativeInventoryError("native artifact exceeds byte limit")
        chunks: list[bytes] = []
        remaining = MAX_ARTIFACT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise NativeInventoryError("native artifact exceeds byte limit")
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ) or len(data) != before.st_size:
            raise NativeInventoryError("native artifact changed while being inventoried")
        return data
    finally:
        os.close(descriptor)


def _parse_v4_plain(path: Path, relative_path: str, data: bytes) -> NativeInventory:
    if len(data) > MAX_ARTIFACT_BYTES:
        raise NativeInventoryError("native artifact exceeds byte limit")
    if not data or not data.endswith(b"\n"):
        raise NativeInventoryError("native JSONL is empty or has a torn final line")
    lines = data.splitlines()
    if not lines or len(lines) > MAX_RECORDS:
        raise NativeInventoryError("native record count is out of bounds")
    if any(not line for line in lines):
        raise NativeInventoryError("native JSONL contains a blank record")
    records = tuple(_decode_line(line, index + 1) for index, line in enumerate(lines))
    header = records[0]
    if header.get("type") != "session":
        raise NativeInventoryError("first record is not a DSH session header")
    version = header.get("version")
    if type(version) is not int or version != 4:
        return _unsupported_inventory(
            relative_path,
            size=len(data),
            digest=hashlib.sha256(data).hexdigest(),
            issue=f"unsupported_header_version:{version!r}",
        )
    if not isinstance(header.get("id"), str) or not header["id"]:
        raise NativeInventoryError("v4 header is missing a session id")
    if type(header.get("createdAt")) is not int or header["createdAt"] < 0:
        raise NativeInventoryError("v4 header has invalid createdAt")
    if type(header.get("isSeeded")) is not bool or type(header.get("delegationDepth")) is not int or header['delegationDepth'] < 0:
        raise NativeInventoryError("v4 header is missing required header fields")

    boundaries: list[NativeEventBoundary] = []
    issues: list[str] = []
    for expected_sequence, record in enumerate(records[1:]):
        event_type = record.get("type")
        sequence = record.get("seq")
        timestamp = record.get("time")
        if not isinstance(event_type, str) or not event_type:
            raise NativeInventoryError(f"event {expected_sequence} has no type")
        if type(sequence) is not int or sequence != expected_sequence:
            raise NativeInventoryError(
                f"event sequence is not dense at index {expected_sequence}: {sequence!r}"
            )
        if type(timestamp) is not int or timestamp < 0:
            raise NativeInventoryError(f"event {expected_sequence} has invalid time")
        if not isinstance(record.get("data"), dict):
            raise NativeInventoryError(f"event {expected_sequence} has invalid data")
        if event_type not in FIXTURE_V4_EVENT_TYPES:
            disposition = "ignorable" if record.get("ignorable") is True else "required_or_unclassified"
            issues.append(f"unsupported_event_type:{event_type}:{disposition}:{sequence}")
        boundaries.append(
            NativeEventBoundary(sequence, timestamp, event_type, record)
        )
    return NativeInventory(
        status="unsupported" if issues else "inventory_only",
        surface=SURFACE,
        relative_path=relative_path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        header=header,
        event_boundaries=tuple(boundaries),
        raw_records=records,
        issues=tuple(issues),
    )


def inventory_native_root(
    native_root: Path | str,
    *,
    run_root: Path | str,
    expected_session_id: str | None = None,
) -> NativeInventory:
    """Inventory one fresh DSH generation below the explicitly supplied root.

    Discovery is limited to ``project-key/session-key/<artifact>`` and refuses
    symlinks or unexpected native files. A compressed v4 artifact is reported
    as ``decoder_unsupported``; it is never skipped or treated as absence.
    """

    root = _absolute(run_root, "run_root")
    sessions = _absolute(native_root, "native_root")
    expected_root = root / "dsh-home" / "sessions"
    if sessions != expected_root:
        raise NativeInventoryError("native_root must be the declared isolated DSH_HOME/sessions root")
    if not _is_child(sessions, root) or sessions.is_symlink():
        raise NativeInventoryError("native_root is outside the isolated run root or is a symlink")
    if not sessions.is_dir():
        raise NativeInventoryError("native_root is missing or is not a directory")

    discovered: list[tuple[Path, str]] = []
    entry_count = 0
    for project in os.scandir(sessions):
        entry_count += 1
        if entry_count > MAX_DISCOVERED_ENTRIES:
            raise NativeInventoryError("native root entry limit exceeded")
        if project.is_symlink():
            raise NativeInventoryError("symlink found in native project inventory")
        if not project.is_dir(follow_symlinks=False):
            raise NativeInventoryError("unexpected non-directory in native project inventory")
        for session in os.scandir(project.path):
            entry_count += 1
            if entry_count > MAX_DISCOVERED_ENTRIES:
                raise NativeInventoryError("native root entry limit exceeded")
            if session.is_symlink():
                raise NativeInventoryError("symlink found in native session inventory")
            if not session.is_dir(follow_symlinks=False):
                raise NativeInventoryError("unexpected non-directory in native session inventory")
            for artifact in os.scandir(session.path):
                entry_count += 1
                if entry_count > MAX_DISCOVERED_ENTRIES:
                    raise NativeInventoryError("native root entry limit exceeded")
                item = Path(artifact.path)
                if artifact.is_symlink() or not artifact.is_file(follow_symlinks=False):
                    raise NativeInventoryError("native session contains a symlink or non-regular file")
                relative = item.relative_to(sessions).as_posix()
                if item.name in {"session.v4.jsonl", "session.v4.jsonl.zstd"}:
                    discovered.append((item, relative))
                elif item.name == 'session.lock' and _read_bounded_regular_file(item) == b'':
                    # Shipped advisory lock file has no session-bearing bytes.
                    pass
                else:
                    raise NativeInventoryError(f"unsupported native artifact is present: {relative}")

    if len(discovered) != 1:
        raise NativeInventoryError(f"expected exactly one fresh v4 artifact, found {len(discovered)}")
    artifact, relative = discovered[0]
    data = _read_bounded_regular_file(artifact)
    if artifact.name.endswith(".zstd"):
        from session_bench.dsh_native import decompress_frames, DSHPhysicalError
        try:
            logical, _ = decompress_frames(data)
        except DSHPhysicalError as error:
            raise NativeInventoryError(str(error)) from error
        result = _parse_v4_plain(artifact, relative, logical)
        result = replace(result, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    else:
        result = _parse_v4_plain(artifact, relative, data)
    if expected_session_id is not None and result.header is not None:
        if result.header.get("id") != expected_session_id:
            raise NativeInventoryError("native header session id does not match expected observer id")
    return result
