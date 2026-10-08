"""Bounded OpenCode CLI acquisition for the survival-v1 calibration.

The adapter deliberately has no implicit native-data discovery path.  A caller
supplies a fresh run root, a scratch project, and a runner.  The supported
default-auth route keeps normal credential lookup in place without the adapter
opening or copying credential material; an explicitly provisioned isolated auth
file is also supported.  The runner is the only seam that can start OpenCode;
the module does not read the normal OpenCode data directory, auth file, or
history.

There are three independent pieces in this module:

* :func:`build_launch` describes the isolated ``opencode run`` invocation;
* :func:`observe_stdout` turns the exact JSON stream into an independent
  observer and binds it to one new session marker; and
* :func:`capture_sqlite_bundle` copies the database and its WAL/SHM companions
  only after an injected quiescent barrier.

The higher-level :func:`run_calibration` composes those pieces and records a
failed attempt as carefully as a successful one.  It is intentionally usable
with a fake runner and fake SQLite files in unit tests; no live model call is
made by importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import quote


OPENCODE_CONFIGURATION_ID = "opencode-cli"
OPENCODE_EXECUTABLE = "opencode"
OPENCODE_DEFAULT_MODEL = "opencode/muse-spark-1.3-contributor-free"
OPENCODE_DATABASE_NAME = "opencode.db"
OPENCODE_COMPANION_SUFFIXES = ("-wal", "-shm")
OPENCODE_AUTH_RELATIVE_PATH = Path("opencode") / "auth.json"
OBSERVER_SCHEMA_VERSION = "1.0-survival-opencode-cli-observer"
CAPTURE_SCHEMA_VERSION = "1.0-survival-opencode-cli-capture"


class OpenCodeAdapterError(ValueError):
    """Base error for a refused or incomplete OpenCode acquisition."""


class IsolationError(OpenCodeAdapterError):
    """The proposed run would use an unbounded or pre-existing root."""


class ObserverBindingError(OpenCodeAdapterError):
    """The stdout stream cannot be bound to exactly one new native session."""


class CaptureError(OpenCodeAdapterError):
    """The native database could not be captured coherently."""


class CalibrationError(OpenCodeAdapterError):
    """The calibration contract is incomplete."""


def _resolved(path: Path | str) -> Path:
    """Resolve a caller-declared path without following it for file reads."""

    return Path(path).expanduser().resolve()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _path_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_model_id(model: str) -> str:
    """Validate an explicit OpenCode ``--model`` ID.

    Accepts only ``provider/model``-shaped IDs.  Rejects empty,
    whitespace-padded, option-like (starting with ``-``), or provider-less
    values so a launch can never silently fall back to the CLI default.
    """

    if not isinstance(model, str) or not model:
        raise ValueError("OpenCode model must be a non-empty provider/model ID")
    if model != model.strip():
        raise ValueError("OpenCode model must not have leading or trailing whitespace")
    if any(char.isspace() for char in model):
        raise ValueError("OpenCode model must not contain whitespace")
    if model.startswith("-"):
        raise ValueError("OpenCode model must not look like a CLI option")
    if "/" not in model:
        raise ValueError("OpenCode model must be a provider/model ID")
    parts = model.split("/")
    if any(not part for part in parts):
        raise ValueError("OpenCode model must have non-empty provider and model parts")
    return model


@dataclass(frozen=True)
class OpenCodeIdentity:
    """Identity recorded before a calibration submission."""

    executable: str = OPENCODE_EXECUTABLE
    version: str = "unknown"
    build: str | None = None
    model: str = OPENCODE_DEFAULT_MODEL

    def __post_init__(self) -> None:
        validate_model_id(self.model)


@dataclass(frozen=True)
class ExplicitAuth:
    """An auth file already provisioned inside the fresh run root.

    ``source`` is descriptive provenance only.  It is never interpreted as a
    filesystem path and the adapter never copies from a normal profile.
    """

    path: Path
    source: str = "explicit-isolated-provision"
    provisioned: bool = True

    @property
    def explicit(self) -> bool:
        return bool(self.source.strip()) and self.source != "normal-profile-copy"


def isolated_auth_path(run_root: Path | str) -> Path:
    """Return the only auth location accepted by the isolated route."""

    root = _resolved(run_root)
    return root / "xdg-data" / OPENCODE_AUTH_RELATIVE_PATH


def proposed_environment(run_root: Path | str) -> Mapping[str, str]:
    """Return the default-auth isolation override for OpenCode.

    The supported default auth lookup is intentionally left alone: the adapter
    does not open, copy, or inspect the normal credential material.  The native
    database is still pinned to a new explicit path.  An isolated auth setup can
    opt into a fresh ``XDG_DATA_HOME`` with ``isolated_data_home=True``.

    The mapping is intentionally small and deterministic.  A caller may add a
    PATH entry for its injected process runner, but must not replace
    ``OPENCODE_DB`` or the optional fresh data-home value.
    """

    root = _resolved(run_root)
    return {"OPENCODE_DB": str(root / OPENCODE_DATABASE_NAME)}


def isolated_environment(run_root: Path | str) -> Mapping[str, str]:
    """Return the opt-in fresh-data-home route for explicitly provisioned auth."""

    root = _resolved(run_root)
    return {
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "OPENCODE_DB": str(root / OPENCODE_DATABASE_NAME),
    }


def validate_isolated_auth(
    run_root: Path | str,
    auth: ExplicitAuth | Path | str | None,
    *,
    require_existing: bool = True,
) -> ExplicitAuth:
    """Validate explicit isolated auth without reading its contents.

    The accepted path is exactly ``<run-root>/xdg-data/opencode/auth.json``.
    A path in ``~/.local/share/opencode`` or any other pre-existing profile is
    rejected, even when the caller labels it as explicit.  Provisioning is the
    caller's responsibility; this function only checks the target metadata.
    """

    root = _resolved(run_root)
    if auth is None:
        raise IsolationError("OpenCode authentication must be explicitly provisioned in the fresh run root")
    value = auth if isinstance(auth, ExplicitAuth) else ExplicitAuth(_resolved(auth))
    if not value.explicit or not value.provisioned:
        raise IsolationError("OpenCode authentication is not explicitly isolated")
    target = _resolved(value.path)
    expected = isolated_auth_path(root)
    if target != expected:
        raise IsolationError("OpenCode auth must be the isolated xdg-data/opencode/auth.json target")
    normal_data = _resolved(Path.home() / ".local" / "share" / "opencode")
    if _path_inside(target, normal_data) and not _path_inside(normal_data, root):
        raise IsolationError("normal OpenCode auth/history path is forbidden")
    if require_existing:
        try:
            info = target.lstat()
        except FileNotFoundError as exc:
            raise IsolationError("isolated OpenCode auth target is not provisioned") from exc
        if not target.is_file() or target.is_symlink() or info.st_size <= 0:
            raise IsolationError("isolated OpenCode auth target is not a regular non-empty file")
    return replace(value, path=target)


def provision_isolated_auth(
    run_root: Path | str,
    provisioner: Callable[[Path], Any],
) -> ExplicitAuth:
    """Ask an approved auth provider to write the isolated target.

    This helper deliberately accepts a writer callback rather than a source
    path or source bytes.  In particular, it cannot copy the user's normal
    OpenCode auth file by accident.
    """

    if not callable(provisioner):
        raise TypeError("provisioner must be callable")
    target = isolated_auth_path(run_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise IsolationError("isolated auth target already exists")
    provisioner(target)
    return validate_isolated_auth(run_root, ExplicitAuth(target), require_existing=True)


@dataclass(frozen=True)
class OpenCodeLaunch:
    """Fully declared command and environment for one OpenCode attempt."""

    run_root: Path
    project_dir: Path
    prompt: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    database: Path
    xdg_data_home: Path
    auth: ExplicitAuth | None = None
    identity: OpenCodeIdentity = field(default_factory=OpenCodeIdentity)

    @property
    def command(self) -> tuple[str, ...]:
        return self.argv

    def require_isolated_auth(self, *, require_existing: bool = True) -> ExplicitAuth:
        if self.auth is None:
            raise IsolationError("this launch uses default auth; no isolated auth target is available")
        return validate_isolated_auth(self.run_root, self.auth, require_existing=require_existing)

    def require_auth(self, *, require_existing: bool = True) -> ExplicitAuth | None:
        """Accept supported default auth without touching its credential material."""

        if self.auth is None:
            return None
        return validate_isolated_auth(self.run_root, self.auth, require_existing=require_existing)


def _validate_launch_roots(run_root: Path, project_dir: Path) -> None:
    if not run_root.is_absolute() or not project_dir.is_absolute():
        raise IsolationError("OpenCode run and project roots must be absolute")
    if run_root == Path("/") or project_dir == Path("/"):
        raise IsolationError("OpenCode roots are too broad")
    if not project_dir.exists() or not project_dir.is_dir() or project_dir.is_symlink():
        raise IsolationError("OpenCode project directory must be an existing ordinary directory")


def build_launch(
    run_root: Path | str,
    project_dir: Path | str,
    prompt: str,
    *,
    auth: ExplicitAuth | Path | str | None = None,
    executable: str = OPENCODE_EXECUTABLE,
    model: str | None = None,
    identity: OpenCodeIdentity | None = None,
    extra_environment: Mapping[str, str] | None = None,
    validate_auth: bool = False,
) -> OpenCodeLaunch:
    """Build the exact pure JSON OpenCode CLI route.

    Every launch pins an explicit ``--model`` value and never relies on the
    OpenCode CLI default.  ``model=None`` selects
    :data:`OPENCODE_DEFAULT_MODEL`; pass an explicit alternative ID to measure
    a different configured model.  The selected model is bound into
    :class:`OpenCodeIdentity` and a mismatch is rejected.

    With no ``auth`` argument the supported default auth lookup remains in
    place and only ``OPENCODE_DB`` is redirected.  Supplying ``auth`` selects
    the explicit isolated-auth route and adds a fresh ``XDG_DATA_HOME``.
    ``validate_auth=False`` keeps this constructor usable while preparing a
    fresh target before the approved auth provider writes it.  Any operation
    that could submit a prompt (including :func:`run_calibration`) validates an
    explicit auth target before invoking the runner.
    """

    if not isinstance(prompt, str) or not prompt:
        raise ValueError("OpenCode prompt must be a non-empty string")
    if not isinstance(executable, str) or not executable or executable.startswith("-"):
        raise ValueError("OpenCode executable must be a non-empty command")
    selected_model = OPENCODE_DEFAULT_MODEL if model is None else validate_model_id(model)
    root = _resolved(run_root)
    project = _resolved(project_dir)
    _validate_launch_roots(root, project)
    environment = dict(isolated_environment(root) if auth is not None else proposed_environment(root))
    if extra_environment:
        for key, value in extra_environment.items():
            if key in environment and value != environment[key]:
                raise IsolationError(f"cannot override isolated environment key {key}")
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("extra environment keys and values must be strings")
            environment[key] = value
    db = Path(environment["OPENCODE_DB"])
    data_home = Path(environment.get("XDG_DATA_HOME", root / "xdg-data"))
    if db.exists():
        raise IsolationError("explicit OpenCode DB already exists; the run root is not fresh")
    value = auth
    if value is not None and not isinstance(value, ExplicitAuth):
        value = ExplicitAuth(_resolved(value))
    launch_identity = identity or OpenCodeIdentity(executable=executable, model=selected_model)
    if launch_identity.executable != executable:
        raise ValueError("OpenCode identity executable does not match launch executable")
    if launch_identity.model != selected_model:
        raise ValueError("OpenCode identity model does not match launch model")
    argv = (
        executable,
        "run",
        "--model",
        selected_model,
        "--pure",
        "--format",
        "json",
        "--dir",
        str(project),
        prompt,
    )
    launch = OpenCodeLaunch(
        run_root=root,
        project_dir=project,
        prompt=prompt,
        argv=argv,
        environment=environment,
        database=db,
        xdg_data_home=data_home,
        auth=value,
        identity=launch_identity,
    )
    if validate_auth:
        launch.require_isolated_auth()
    return launch


def calibration_command(
    launch_or_run_root: OpenCodeLaunch | Path | str,
    project_dir: Path | str | None = None,
    prompt: str | None = None,
    *,
    auth: ExplicitAuth | Path | str | None = None,
    model: str | None = None,
) -> tuple[str, ...]:
    """Return the declared calibration command without running it."""

    if isinstance(launch_or_run_root, OpenCodeLaunch):
        if model is None:
            return launch_or_run_root.argv
        selected = validate_model_id(model)
        argv = launch_or_run_root.argv
        positions = [index for index, value in enumerate(argv) if value == "--model"]
        if len(positions) != 1:
            raise ValueError("calibration command model pin is missing or ambiguous")
        flag = positions[0]
        if flag + 1 >= len(argv):
            raise ValueError("calibration command model pin has no value")
        if argv[flag + 1] != selected:
            raise ValueError("calibration command model does not match launch model pin")
        return argv
    if project_dir is None or prompt is None:
        raise ValueError("project_dir and prompt are required when building a calibration command")
    return build_launch(launch_or_run_root, project_dir, prompt, auth=auth, model=model).argv


@dataclass(frozen=True)
class StreamEvent:
    sequence: int
    raw: str
    payload: Mapping[str, Any]
    session_ids: tuple[str, ...]
    response_text: str | None = None


@dataclass(frozen=True)
class StreamObservation:
    """Independent stdout observation for one CLI run."""

    schema_version: str
    raw_lines: tuple[str, ...]
    events: tuple[StreamEvent, ...]
    session_id: str
    session_ids: tuple[str, ...]
    response_canaries: tuple[str, ...]
    returncode: int | None = None

    @property
    def response_boundaries(self) -> tuple[int, ...]:
        return tuple(event.sequence for event in self.events if event.response_text is not None)

    @property
    def complete(self) -> bool:
        return bool(self.session_id and self.response_boundaries)


_SESSION_KEYS = {
    "session",
    "session_id",
    "sessionid",
    "session_id_",
}
_TEXT_KEYS = {
    "text",
    "content",
    "message",
    "output",
    "delta",
}


def _session_key(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return value.replace("-", "_").replace(" ", "").lower() in _SESSION_KEYS


def _collect_session_ids(value: object, found: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _session_key(key) and isinstance(child, str) and child.strip():
                found.add(child.strip())
            _collect_session_ids(child, found)
    elif isinstance(value, list):
        for child in value:
            _collect_session_ids(child, found)


def _collect_text(value: object, found: list[str], *, key_hint: str | None = None) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            hint = str(key).lower()
            if isinstance(child, str) and (hint in _TEXT_KEYS or hint.endswith("text")):
                found.append(child)
            else:
                _collect_text(child, found, key_hint=hint)
    elif isinstance(value, list):
        for child in value:
            _collect_text(child, found, key_hint=key_hint)


def _stream_lines(stream: str | bytes | Iterable[str | bytes | Mapping[str, Any]]) -> Iterable[str]:
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8")
    if isinstance(stream, str):
        yield from stream.splitlines()
        return
    for item in stream:
        if isinstance(item, Mapping):
            yield json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(item, bytes):
            yield item.decode("utf-8").rstrip("\r\n")
        elif isinstance(item, str):
            yield item.rstrip("\r\n")
        else:
            raise ObserverBindingError("OpenCode stdout fixture contains a non-text item")


def observe_stdout(
    stream: str | bytes | Iterable[str | bytes | Mapping[str, Any]],
    *,
    existing_session_ids: Iterable[str] = (),
    expected_session_id: str | None = None,
    response_canaries: Iterable[str] = (),
    returncode: int | None = None,
) -> StreamObservation:
    """Parse strict JSONL stdout and bind it to exactly one new session ID.

    The stream is the observer input.  It is never reconstructed by reading a
    native database or an old transcript.  An empty/malformed stream, a
    pre-existing session marker, or more than one new marker fails closed.
    """

    existing = {str(value) for value in existing_session_ids if str(value)}
    lines: list[str] = []
    events: list[StreamEvent] = []
    all_session_ids: set[str] = set()
    canary_values = tuple(str(value) for value in response_canaries)
    for raw in _stream_lines(stream):
        raw = raw.strip()
        if not raw:
            continue
        lines.append(raw)
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ObserverBindingError("OpenCode --format json emitted a non-JSON stdout line") from exc
        if not isinstance(payload, Mapping):
            raise ObserverBindingError("OpenCode stdout JSON event must be an object")
        ids: set[str] = set()
        _collect_session_ids(payload, ids)
        all_session_ids.update(ids)
        text_values: list[str] = []
        _collect_text(payload, text_values)
        response_text = "\n".join(value for value in text_values if value) or None
        events.append(StreamEvent(len(events) + 1, raw, dict(payload), tuple(sorted(ids)), response_text))
    if not events:
        raise ObserverBindingError("OpenCode stdout observer received no JSON events")
    new_ids = sorted(all_session_ids - existing)
    if not new_ids and all_session_ids and all_session_ids <= existing:
        raise ObserverBindingError("OpenCode stdout is bound only to a pre-existing native session")
    if len(new_ids) != 1:
        raise ObserverBindingError(
            "OpenCode stdout must identify exactly one new native session marker "
            f"(found {len(new_ids)})"
        )
    session_id = new_ids[0]
    if expected_session_id is not None and expected_session_id != session_id:
        raise ObserverBindingError("OpenCode stdout session marker differs from the declared native session")
    found_canaries = tuple(canary for canary in canary_values if canary in "\n".join(lines))
    return StreamObservation(
        schema_version=OBSERVER_SCHEMA_VERSION,
        raw_lines=tuple(lines),
        events=tuple(events),
        session_id=session_id,
        session_ids=tuple(sorted(all_session_ids)),
        response_canaries=found_canaries,
        returncode=returncode,
    )


def bind_observer_to_native_session(
    observation: StreamObservation,
    native_session_ids: Iterable[str],
    *,
    existing_session_ids: Iterable[str] = (),
) -> str:
    """Require the observed marker to occur in the captured native root."""

    native = {str(value) for value in native_session_ids if str(value)}
    existing = {str(value) for value in existing_session_ids if str(value)}
    if observation.session_id in existing:
        raise ObserverBindingError("stdout observer bound to a pre-existing native session")
    if observation.session_id not in native:
        raise ObserverBindingError("stdout observer session marker is absent from the captured native root")
    return observation.session_id


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    size_bytes: int
    inode: int
    device: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class CapturedArtifact:
    artifact_id: str
    relative_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class SQLiteCapture:
    schema_version: str
    source_database: Path
    destination: Path
    artifacts: tuple[CapturedArtifact, ...]
    session_ids: tuple[str, ...] = ()
    quiescent: bool = True

    @property
    def database(self) -> CapturedArtifact:
        return self.artifacts[0]

    @property
    def companions(self) -> tuple[CapturedArtifact, ...]:
        return self.artifacts[1:]

    @property
    def artifact_paths(self) -> tuple[Path, ...]:
        return tuple(self.destination / artifact.relative_path for artifact in self.artifacts)


def _safe_snapshot(path: Path, relative_path: str) -> tuple[FileSnapshot, bytes]:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise CaptureError(f"required OpenCode native file is missing: {relative_path}") from exc
    if path.is_symlink() or not path.is_file():
        raise CaptureError(f"required OpenCode native path is not an ordinary file: {relative_path}")
    data = path.read_bytes()
    after = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise CaptureError(f"OpenCode native file changed type while reading: {relative_path}")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise CaptureError(f"OpenCode native file changed while reading: {relative_path}")
    snapshot = FileSnapshot(
        relative_path=relative_path,
        size_bytes=len(data),
        inode=before.st_ino,
        device=before.st_dev,
        mtime_ns=before.st_mtime_ns,
        sha256=_sha256_bytes(data),
    )
    return snapshot, data


def _barrier_is_quiescent(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        if value.get("quiescent") is False or value.get("writer_alive") is True:
            return False
        if "writer_alive" in value and value.get("writer_alive") is not False:
            return False
        if "quiescent" in value and value.get("quiescent") is not True:
            return False
        return True
    # A barrier callback returning None is a conventional successful barrier.
    return value is None


def _run_barrier(barrier: Callable[[], object] | None) -> None:
    if barrier is None:
        raise CaptureError("SQLite capture requires an injected quiescent barrier")
    try:
        result = barrier()
    except Exception as exc:  # keep the public error independent of runner type
        raise CaptureError("OpenCode writer did not reach the quiescent capture barrier") from exc
    if not _barrier_is_quiescent(result):
        raise CaptureError("OpenCode SQLite capture refused while the writer or companions are live")


def capture_sqlite_bundle(
    database: Path | str,
    destination: Path | str,
    *,
    barrier: Callable[[], object] | None,
    expected_companions: Sequence[str] = (),
    session_ids: Iterable[str] = (),
) -> SQLiteCapture:
    """Capture ``opencode.db`` and its required WAL/SHM files coherently.

    A barrier is mandatory.  By default the adapter requires both
    ``opencode.db-wal`` and ``opencode.db-shm``; passing an explicit companion
    sequence is supported for a fixture with a different declared DB basename,
    but an empty sequence is never treated as a successful SQLite capture.
    Every source is read and hashed before copying, and all source metadata and
    hashes are checked again after the copy.  A missing, live, replaced, or
    changed companion therefore invalidates the whole capture.
    """

    source_db = _resolved(database)
    target = _resolved(destination)
    if source_db.name != OPENCODE_DATABASE_NAME:
        raise CaptureError(f"OpenCode database must be named {OPENCODE_DATABASE_NAME}")
    if source_db == target or target == source_db.parent or _path_inside(source_db, target):
        raise CaptureError("SQLite capture destination must be outside the native DB directory")
    names = tuple(expected_companions) or tuple(source_db.name + suffix for suffix in OPENCODE_COMPANION_SUFFIXES)
    if names != tuple(dict.fromkeys(names)) or any(
        not isinstance(name, str) or not name.startswith(source_db.name + "-") for name in names
    ):
        raise CaptureError("SQLite companion declaration is invalid")
    _run_barrier(barrier)
    source_paths = (source_db, *(source_db.parent / name for name in names))
    relative_names = (source_db.name, *names)
    before: list[tuple[FileSnapshot, bytes]] = []
    for path, relative in zip(source_paths, relative_names):
        before.append(_safe_snapshot(path, relative))
    target.mkdir(parents=True, exist_ok=False)
    artifacts: list[CapturedArtifact] = []
    try:
        for (snapshot, data), relative in zip(before, relative_names):
            output = target / relative
            output.write_bytes(data)
            artifacts.append(CapturedArtifact(relative, relative, len(data), snapshot.sha256))
        after: list[tuple[FileSnapshot, bytes]] = []
        for path, relative in zip(source_paths, relative_names):
            after.append(_safe_snapshot(path, relative))
        for (original, _), (current, _) in zip(before, after):
            if original != current:
                raise CaptureError(f"OpenCode native file changed during capture: {original.relative_path}")
        for artifact, (_, data) in zip(artifacts, before):
            copied = (target / artifact.relative_path).read_bytes()
            if copied != data or _sha256_bytes(copied) != artifact.sha256:
                raise CaptureError(f"copied OpenCode artifact failed integrity check: {artifact.relative_path}")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return SQLiteCapture(
        schema_version=CAPTURE_SCHEMA_VERSION,
        source_database=source_db,
        destination=target,
        artifacts=tuple(artifacts),
        session_ids=tuple(sorted({str(value) for value in session_ids if str(value)})),
        quiescent=True,
    )


def read_sqlite_session_ids(
    database: Path | str,
    *,
    connection_factory: Callable[..., sqlite3.Connection] = sqlite3.connect,
) -> tuple[str, ...]:
    """Read session IDs from a declared copied SQLite bundle.

    This is a narrow convenience reader for fake and future OpenCode schema
    fixtures.  It inspects only tables whose columns are explicitly named like
    session identifiers and never scans a normal data root.  Production schema
    adapters may inject a stricter reader into :func:`run_calibration`.
    """

    path = _resolved(database)
    if not path.is_file() or path.is_symlink():
        raise CaptureError("declared copied SQLite database is not an ordinary file")
    uri = f"file:{quote(str(path))}?mode=ro"
    connection = connection_factory(uri, uri=True)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        values: set[str] = set()
        for (table_name,) in rows:
            if not isinstance(table_name, str) or '"' in table_name:
                continue
            columns = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            candidates = [str(row[1]) for row in columns if len(row) > 1 and _session_key(row[1])]
            for column in candidates:
                result = connection.execute(
                    f'SELECT DISTINCT "{column}" FROM "{table_name}" WHERE "{column}" IS NOT NULL'
                ).fetchall()
                for (value,) in result:
                    if isinstance(value, str) and value.strip():
                        values.add(value.strip())
    except sqlite3.DatabaseError as exc:
        raise CaptureError("copied OpenCode SQLite schema could not be inspected") from exc
    finally:
        connection.close()
    return tuple(sorted(values))


@dataclass(frozen=True)
class ReadEditPermission:
    """Evidence that the retained calibration could read and edit the fixture."""

    read_ok: bool
    edit_ok: bool
    read_marker: str | None = None
    edit_marker: str | None = None
    before_sha256: str | None = None
    after_sha256: str | None = None
    source: str = "injected-calibration-probe"

    @property
    def qualified(self) -> bool:
        return (
            self.read_ok
            and self.edit_ok
            and self.before_sha256 is not None
            and self.after_sha256 is not None
            and self.before_sha256 != self.after_sha256
        )

    def require_qualified(self) -> None:
        if not self.qualified:
            raise CalibrationError("OpenCode calibration did not prove independent read and edit permission")


def normalize_permission_probe(value: ReadEditPermission | Mapping[str, Any]) -> ReadEditPermission:
    if isinstance(value, ReadEditPermission):
        return value
    if not isinstance(value, Mapping):
        raise CalibrationError("read/edit permission probe returned an invalid shape")
    return ReadEditPermission(
        read_ok=value.get("read_ok", value.get("read") is True) is True,
        edit_ok=value.get("edit_ok", value.get("edit") is True) is True,
        read_marker=value.get("read_marker"),
        edit_marker=value.get("edit_marker"),
        before_sha256=value.get("before_sha256", value.get("before")),
        after_sha256=value.get("after_sha256", value.get("after")),
        source=str(value.get("source", "injected-calibration-probe")),
    )


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    state: str
    reason_ids: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    exit_code: int | None = None
    native_session_id: str | None = None
    captured_artifacts: tuple[str, ...] = ()
    permission_qualified: bool = False
    error: str | None = None
    transitions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "state": self.state,
            "reason_ids": list(self.reason_ids),
            "command": list(self.command),
            "environment": dict(self.environment),
            "exit_code": self.exit_code,
            "native_session_id": self.native_session_id,
            "captured_artifacts": list(self.captured_artifacts),
            "permission_qualified": self.permission_qualified,
            "error": self.error,
            "transitions": list(self.transitions),
        }


class AttemptLedger:
    """Small append-only sink suitable for the campaign ledger adapter."""

    def __init__(self) -> None:
        self.records: list[AttemptRecord] = []

    def append(self, record: AttemptRecord | Mapping[str, Any]) -> None:
        if isinstance(record, AttemptRecord):
            self.records.append(record)
            return
        if not isinstance(record, Mapping) or not isinstance(record.get("attempt_id"), str):
            raise ValueError("attempt ledger record must contain an attempt_id")
        self.records.append(
            AttemptRecord(
                attempt_id=str(record["attempt_id"]),
                state=str(record.get("state", "invalid")),
                reason_ids=tuple(str(value) for value in record.get("reason_ids", ())),
                command=tuple(str(value) for value in record.get("command", ())),
                environment=dict(record.get("environment", {})),
                exit_code=record.get("exit_code"),
                native_session_id=record.get("native_session_id"),
                captured_artifacts=tuple(str(value) for value in record.get("captured_artifacts", ())),
                permission_qualified=record.get("permission_qualified") is True,
                error=record.get("error"),
                transitions=tuple(str(value) for value in record.get("transitions", ())),
            )
        )

    @property
    def attempts(self) -> tuple[AttemptRecord, ...]:
        return tuple(self.records)


@dataclass(frozen=True)
class CalibrationResult:
    attempt: AttemptRecord
    launch: OpenCodeLaunch
    observation: StreamObservation | None = None
    capture: SQLiteCapture | None = None
    permission: ReadEditPermission | None = None

    @property
    def calibration_ready(self) -> bool:
        return (
            self.attempt.state == "complete"
            and self.observation is not None
            and self.capture is not None
            and self.permission is not None
            and self.permission.qualified
        )

    def require_ready(self) -> None:
        if not self.calibration_ready:
            reasons = ", ".join(self.attempt.reason_ids) or "opencode.calibration_incomplete"
            raise CalibrationError(f"OpenCode CLI calibration is blocked: {reasons}")


@dataclass(frozen=True)
class RunnerResult:
    returncode: int
    stdout: str | bytes | Iterable[str | bytes | Mapping[str, Any]]
    stderr: str = ""


class OpenCodeRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
    ) -> RunnerResult | subprocess.CompletedProcess[str] | Mapping[str, Any]: ...


class SubprocessOpenCodeRunner:
    """Production seam; callers must explicitly choose to invoke it."""

    def __init__(self, *, timeout_seconds: float = 300.0) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path) -> RunnerResult:
        result = subprocess.run(
            tuple(argv),
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        return RunnerResult(result.returncode, result.stdout, result.stderr)


def _invoke_runner(
    runner: OpenCodeRunner | Callable[..., Any],
    launch: OpenCodeLaunch,
) -> RunnerResult:
    if hasattr(runner, "run"):
        result = runner.run(launch.argv, env=launch.environment, cwd=launch.project_dir)  # type: ignore[attr-defined]
    elif callable(runner):
        result = runner(launch.argv, env=launch.environment, cwd=launch.project_dir)
    else:
        raise TypeError("runner must expose run(argv, env=, cwd=) or be callable")
    if isinstance(result, RunnerResult):
        return result
    if isinstance(result, subprocess.CompletedProcess):
        return RunnerResult(int(result.returncode), result.stdout or "", result.stderr or "")
    if isinstance(result, Mapping):
        if "returncode" not in result or "stdout" not in result:
            raise CalibrationError("injected OpenCode runner result is missing returncode/stdout")
        return RunnerResult(int(result["returncode"]), result["stdout"], str(result.get("stderr", "")))
    if isinstance(result, tuple) and len(result) >= 2:
        return RunnerResult(int(result[0]), result[1], str(result[2]) if len(result) > 2 else "")
    raise CalibrationError("injected OpenCode runner returned an unsupported result")


def _append_attempt(ledger: AttemptLedger | Callable[[AttemptRecord], Any] | Any | None, record: AttemptRecord) -> None:
    if ledger is None:
        return
    if callable(ledger) and not hasattr(ledger, "append"):
        ledger(record)
        return
    if hasattr(ledger, "append"):
        ledger.append(record)
        return
    raise TypeError("attempt ledger must expose append(record) or be callable")


def run_calibration(
    launch: OpenCodeLaunch,
    *,
    attempt_id: str,
    runner: OpenCodeRunner | Callable[..., Any],
    destination: Path | str,
    barrier: Callable[[], object] | None,
    permission_probe: Callable[..., ReadEditPermission | Mapping[str, Any]] | ReadEditPermission | Mapping[str, Any],
    existing_session_ids: Iterable[str] = (),
    expected_companions: Sequence[str] = (),
    response_canaries: Iterable[str] = (),
    native_session_reader: Callable[[Path], Iterable[str]] | None = None,
    ledger: AttemptLedger | Callable[[AttemptRecord], Any] | Any | None = None,
) -> CalibrationResult:
    """Run one injected calibration and retain every terminal attempt state.

    The function may invoke the supplied runner, but importing it or building
    a launch never does.  A runner refusal, observer mismatch, missing native
    companion, or failed read/edit probe yields an ``invalid`` record with a
    reason and returns it to the caller; it is never silently retried or
    replaced by a successful attempt.
    """

    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")
    current_state = "started"
    transitions = [current_state]
    observation: StreamObservation | None = None
    capture: SQLiteCapture | None = None
    permission: ReadEditPermission | None = None
    reason_ids: list[str] = []
    error_message: str | None = None
    exit_code: int | None = None
    native_session_id: str | None = None
    try:
        launch.require_auth(require_existing=True)
        current_state = "submitted"
        transitions.append(current_state)
        process = _invoke_runner(runner, launch)
        exit_code = process.returncode
        observation = observe_stdout(
            process.stdout,
            existing_session_ids=existing_session_ids,
            response_canaries=response_canaries,
            returncode=process.returncode,
        )
        if process.returncode != 0:
            raise CalibrationError(f"OpenCode runner exited with {process.returncode}")
        native_ids: tuple[str, ...] = ()
        capture = capture_sqlite_bundle(
            launch.database,
            destination,
            barrier=barrier,
            expected_companions=expected_companions,
            session_ids=native_ids,
        )
        transitions.append("captured")
        native_ids: tuple[str, ...]
        if native_session_reader is not None:
            native_ids = tuple(
                str(value)
                for value in native_session_reader(capture.destination / capture.database.relative_path)
                if str(value)
            )
        else:
            native_ids = ()
        if not native_ids:
            native_ids = capture.session_ids
        native_session_id = bind_observer_to_native_session(
            observation,
            native_ids,
            existing_session_ids=existing_session_ids,
        )
        if callable(permission_probe) and not isinstance(permission_probe, (ReadEditPermission, Mapping)):
            try:
                probe_value = permission_probe(launch.project_dir)
            except TypeError:
                probe_value = permission_probe()
        else:
            probe_value = permission_probe
        permission = normalize_permission_probe(probe_value)
        permission.require_qualified()
        transitions.append("complete")
        record = AttemptRecord(
            attempt_id=attempt_id,
            state="complete",
            reason_ids=(),
            command=launch.argv,
            environment=launch.environment,
            exit_code=exit_code,
            native_session_id=native_session_id,
            captured_artifacts=tuple(item.relative_path for item in capture.artifacts),
            permission_qualified=True,
            transitions=tuple(transitions),
        )
        _append_attempt(ledger, record)
        return CalibrationResult(record, launch, observation, capture, permission)
    except Exception as exc:
        error_message = str(exc) or type(exc).__name__
        if isinstance(exc, IsolationError):
            reason_ids.append("opencode.auth_or_isolation")
        elif isinstance(exc, ObserverBindingError):
            reason_ids.append("opencode.stdout_session_binding")
        elif isinstance(exc, CaptureError):
            reason_ids.append("opencode.native_capture")
        elif isinstance(exc, CalibrationError):
            reason_ids.append("opencode.calibration_permission")
        else:
            reason_ids.append("opencode.runner_failure")
        current_state = "invalid"
        transitions.append(current_state)
        record = AttemptRecord(
            attempt_id=attempt_id,
            state=current_state,
            reason_ids=tuple(reason_ids),
            command=launch.argv,
            environment=launch.environment,
            exit_code=exit_code,
            native_session_id=native_session_id,
            captured_artifacts=tuple(item.relative_path for item in capture.artifacts) if capture else (),
            permission_qualified=permission.qualified if permission else False,
            error=error_message,
            transitions=tuple(transitions),
        )
        _append_attempt(ledger, record)
        return CalibrationResult(record, launch, observation, capture, permission)


# Integration-friendly aliases for callers that prefer an explicit ``spec``
# name.  They point at the same implementation and do not create another
# process or inspect any state.
build_launch_spec = build_launch
make_launch = build_launch
capture_native_bundle = capture_sqlite_bundle
parse_stdout = observe_stdout


__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "OBSERVER_SCHEMA_VERSION",
    "OPENCODE_AUTH_RELATIVE_PATH",
    "OPENCODE_COMPANION_SUFFIXES",
    "OPENCODE_CONFIGURATION_ID",
    "OPENCODE_DATABASE_NAME",
    "OPENCODE_DEFAULT_MODEL",
    "OPENCODE_EXECUTABLE",
    "AttemptLedger",
    "AttemptRecord",
    "CalibrationError",
    "CalibrationResult",
    "CapturedArtifact",
    "CaptureError",
    "ExplicitAuth",
    "FileSnapshot",
    "IsolationError",
    "OpenCodeAdapterError",
    "OpenCodeIdentity",
    "OpenCodeLaunch",
    "OpenCodeRunner",
    "ReadEditPermission",
    "RunnerResult",
    "SQLiteCapture",
    "StreamEvent",
    "StreamObservation",
    "SubprocessOpenCodeRunner",
    "bind_observer_to_native_session",
    "build_launch",
    "build_launch_spec",
    "calibration_command",
    "capture_native_bundle",
    "capture_sqlite_bundle",
    "isolated_environment",
    "isolated_auth_path",
    "make_launch",
    "normalize_permission_probe",
    "observe_stdout",
    "parse_stdout",
    "proposed_environment",
    "provision_isolated_auth",
    "read_sqlite_session_ids",
    "run_calibration",
    "validate_isolated_auth",
    "validate_model_id",
]
