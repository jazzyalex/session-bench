"""Bounded Cursor adapters for the survival-v1 calibration seam.

This module deliberately contains no default process launcher and no native-store
reader.  A controller supplies a static identity probe and, only when an
authorized live calibration is being run, an injected runner.  That keeps the
preflight path useful in offline tests while making it impossible for a normal
Cursor profile, credentials, or personal history to be discovered accidentally.

The CLI and Desktop routes are separate identities.  A CLI plan uses the Cursor
Agent environment overrides and the machine-readable stream.  A Desktop plan
only describes a safe isolated Electron launch.  Desktop output remains
unrankable even after a caller supplies the evidence needed to say that a
calibration may proceed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias


PROTOCOL_VERSION = "1.0-survival"
CLI_SURFACE = "cursor-cli"
DESKTOP_SURFACE = "cursor-desktop"
SURFACES = frozenset({CLI_SURFACE, DESKTOP_SURFACE})

DEFAULT_CLI_EXECUTABLE = "agent"
DEFAULT_DESKTOP_EXECUTABLE = "Cursor"

# These names intentionally describe the synthetic run roots.  They are never
# looked up in the normal Cursor profile.
CLI_DATA_DIR_NAME = "cursor-data"
CLI_CONFIG_DIR_NAME = "cursor-config"
DESKTOP_USER_DATA_DIR_NAME = "cursor-desktop-user-data"
DESKTOP_EXTENSIONS_DIR_NAME = "cursor-desktop-extensions"

_MAX_STREAM_BYTES = 4 * 1024 * 1024
_MAX_STREAM_EVENTS = 512
_MAX_STREAM_LINE_BYTES = 512 * 1024


class CursorAdapterError(RuntimeError):
    """Base error for a rejected or incomplete Cursor adapter operation."""


class CursorPreflightError(CursorAdapterError, ValueError):
    """Raised when an isolated, surface-specific preflight cannot be proven."""


class SurfaceMismatchError(CursorPreflightError):
    """Raised when identity or a plan belongs to the other Cursor surface."""


class DesktopBlockedError(CursorPreflightError):
    """Raised when Desktop evidence is insufficient for any capture action."""


class CursorIdentityProbe(Protocol):
    """Static, injected identity probe.

    Implementations may inspect executable metadata supplied by the test or
    controller.  They must not read a Cursor data/config root or run a model.
    """

    def probe(self, *, surface: str, executable: str) -> Mapping[str, Any]: ...


class CursorRunner(Protocol):
    """Injected runner used only by an explicitly authorized calibration."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
    ) -> Any: ...


IdentityProbeInput: TypeAlias = Mapping[str, Any] | CursorIdentityProbe | Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class CursorIdentity:
    """The minimum executable/app identity needed to bind a surface plan."""

    surface: Literal["cursor-cli", "cursor-desktop"]
    product: str
    version: str
    build: str | None = None
    commit: str | None = None
    executable: str | None = None
    source: str = "static_probe"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly copy suitable for a run manifest."""

        return {
            "surface": self.surface,
            "product": self.product,
            "version": self.version,
            "build": self.build,
            "commit": self.commit,
            "executable": self.executable,
            "source": self.source,
        }


@dataclass(frozen=True)
class StaticIdentityProbe:
    """Adapt a fake/static metadata source to :class:`CursorIdentityProbe`.

    ``source`` may be a mapping or a callable.  A mapping is useful for unit
    tests and frozen package metadata.  The callable is intentionally passed
    only ``surface`` and ``executable``; it is never given a data root.
    """

    source: Mapping[str, Any] | Callable[..., Mapping[str, Any]]

    def probe(self, *, surface: str, executable: str) -> Mapping[str, Any]:
        if isinstance(self.source, Mapping):
            return dict(self.source)
        if not callable(self.source):
            raise CursorPreflightError("static identity source is not callable or a mapping")
        try:
            value = self.source(surface=surface, executable=executable)
        except TypeError:
            # Small fake probes often accept the two values positionally.  This
            # fallback still exposes no filesystem or credential material.
            try:
                value = self.source(surface, executable)
            except TypeError:
                value = self.source(executable)
        if not isinstance(value, Mapping):
            raise CursorPreflightError("static identity probe must return a mapping")
        return dict(value)


def _normal_abs(path: Path | str, label: str) -> Path:
    if not isinstance(path, (Path, str)):
        raise CursorPreflightError(f"{label} must be a path")
    raw = str(path)
    if not raw or "\x00" in raw:
        raise CursorPreflightError(f"{label} is empty or contains a NUL")
    value = Path(raw).expanduser()
    if not value.is_absolute():
        raise CursorPreflightError(f"{label} must be absolute")
    # ``strict=False`` means this validates only the lexical boundary; no
    # directory is enumerated and no normal profile is opened.
    return value.resolve(strict=False)


def _is_child(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _is_default_cursor_path(path: Path, *, kind: str) -> bool:
    """Recognise known normal-profile locations without touching them."""

    home = Path.home().resolve(strict=False)
    known: set[Path]
    if kind == "cli-data":
        known = {home / ".cursor"}
    elif kind == "cli-config":
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_root = Path(xdg).expanduser() if xdg else home / ".config"
        known = {config_root.resolve(strict=False) / "cursor", home / ".cursor"}
    elif kind == "desktop-data":
        known = {
            home / "Library" / "Application Support" / "Cursor",
            home / ".cursor",
        }
    elif kind == "desktop-extensions":
        known = {
            home / ".cursor" / "extensions",
            home / "Library" / "Application Support" / "Cursor" / "extensions",
        }
    else:  # pragma: no cover - private helper called with frozen labels only
        raise AssertionError(f"unknown Cursor path kind: {kind}")
    return path in {item.resolve(strict=False) for item in known}


def _validate_isolated_root(root: Path | str, *, label: str) -> Path:
    value = _normal_abs(root, label)
    if value == Path(value.anchor):
        raise CursorPreflightError(f"{label} cannot be a filesystem root")
    if value == Path.home().resolve(strict=False):
        raise CursorPreflightError(f"{label} cannot be the normal user home")
    return value


def _validate_run_layout(run_root: Path | str, paths: Mapping[str, tuple[Path | str, str]]) -> tuple[Path, dict[str, Path]]:
    root = _validate_isolated_root(run_root, label="run_root")
    checked: dict[str, Path] = {}
    for name, (candidate, kind) in paths.items():
        value = _validate_isolated_root(candidate, label=name)
        if not _is_child(value, root):
            raise CursorPreflightError(f"{name} must be inside the isolated run_root")
        if _is_default_cursor_path(value, kind=kind):
            raise CursorPreflightError(f"{name} points at Cursor's normal profile")
        checked[name] = value
    values = tuple(checked.values())
    if len(set(values)) != len(values):
        raise CursorPreflightError("isolated Cursor roots must be distinct")
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            if _is_child(left, right) or _is_child(right, left):
                raise CursorPreflightError("isolated Cursor roots must not be nested")
    return root, checked


def _validate_workspace(workspace: Path | str, *, run_root: Path) -> Path:
    value = _normal_abs(workspace, "workspace")
    if not _is_child(value, run_root):
        raise CursorPreflightError("workspace must be inside the isolated run_root")
    return value


_SENSITIVE_IDENTITY_KEYS = (
    "token", "secret", "cookie", "credential", "password", "auth", "history", "prompt",
)


def _validate_identity(raw: Mapping[str, Any], *, surface: str, executable: str) -> CursorIdentity:
    if surface not in SURFACES:
        raise CursorPreflightError(f"unsupported Cursor surface: {surface!r}")
    if not isinstance(raw, Mapping):
        raise CursorPreflightError("static identity probe must return a mapping")
    for key in raw:
        lowered = str(key).lower()
        if any(word in lowered for word in _SENSITIVE_IDENTITY_KEYS):
            raise CursorPreflightError("static identity probe may not return credentials or history")
    declared_surface = raw.get("surface")
    if declared_surface is not None and declared_surface != surface:
        raise SurfaceMismatchError(
            f"identity surface {declared_surface!r} does not match requested {surface!r}"
        )
    product = raw.get("product", raw.get("nameShort", raw.get("name", "Cursor")))
    version = raw.get("version", raw.get("app_version"))
    build = raw.get("build", raw.get("build_id"))
    commit = raw.get("commit", raw.get("revision"))
    if not isinstance(product, str) or not product.strip():
        raise CursorPreflightError("static identity is missing a product name")
    if not isinstance(version, str) or not version.strip():
        raise CursorPreflightError("static identity is missing an executable/app version")
    if build is not None and (not isinstance(build, str) or not build.strip()):
        raise CursorPreflightError("static identity build must be a non-empty string")
    if commit is not None and (not isinstance(commit, str) or not commit.strip()):
        raise CursorPreflightError("static identity commit must be a non-empty string")
    return CursorIdentity(
        surface=surface, product=product.strip(), version=version.strip(),
        build=build.strip() if isinstance(build, str) else None,
        commit=commit.strip() if isinstance(commit, str) else None,
        executable=executable,
    )


def _probe_identity(probe: IdentityProbeInput | None, *, surface: str, executable: str) -> CursorIdentity:
    if probe is None:
        raise CursorPreflightError("an injected static identity probe is required")
    if isinstance(probe, Mapping):
        raw: Mapping[str, Any] = probe
    elif hasattr(probe, "probe"):
        raw = probe.probe(surface=surface, executable=executable)  # type: ignore[union-attr]
    elif callable(probe):
        raw = StaticIdentityProbe(probe).probe(surface=surface, executable=executable)
    else:
        raise CursorPreflightError("identity probe must be a mapping, callable, or probe object")
    return _validate_identity(raw, surface=surface, executable=executable)


def _ensure_surface(value: str, expected: str) -> None:
    if value != expected:
        raise SurfaceMismatchError(f"expected {expected}, received {value}")


@dataclass(frozen=True)
class CursorCliLaunchPlan:
    """A complete, isolated Cursor Agent CLI launch description."""

    surface: Literal["cursor-cli"]
    executable: str
    argv: tuple[str, ...]
    env: Mapping[str, str]
    run_root: Path
    workspace: Path
    data_dir: Path
    config_dir: Path
    identity: CursorIdentity
    prompt: str | None = None
    model_started: bool = False

    @property
    def isolation_roots(self) -> tuple[Path, Path]:
        return self.data_dir, self.config_dir

    @property
    def command(self) -> tuple[str, ...]:
        """Compatibility name for integrations that call the argv a command."""

        return self.argv

    @property
    def environment(self) -> Mapping[str, str]:
        return self.env


@dataclass(frozen=True)
class CursorDesktopLaunchPlan:
    """A safe isolated Cursor Desktop launch description.

    Constructing this value does not launch Cursor and does not claim that the
    app writes a transcript beneath either root.
    """

    surface: Literal["cursor-desktop"]
    executable: str
    argv: tuple[str, ...]
    run_root: Path
    workspace: Path
    user_data_dir: Path
    extensions_dir: Path
    identity: CursorIdentity
    model_started: bool = False

    @property
    def isolation_roots(self) -> tuple[Path, Path]:
        return self.user_data_dir, self.extensions_dir

    @property
    def command(self) -> tuple[str, ...]:
        return self.argv


def build_cli_launch_plan(
    run_root: Path | str,
    workspace: Path | str,
    *,
    identity_probe: IdentityProbeInput | None,
    executable: str = DEFAULT_CLI_EXECUTABLE,
    prompt: str | None = None,
    data_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
) -> CursorCliLaunchPlan:
    """Build the exact isolated CLI vector without invoking Cursor Agent."""

    if not isinstance(executable, str) or not executable.strip() or "\x00" in executable:
        raise CursorPreflightError("CLI executable must be a non-empty safe string")
    if prompt is not None and (not isinstance(prompt, str) or not prompt.strip()):
        raise CursorPreflightError("Cursor CLI prompt must be non-empty when supplied")
    root = _validate_isolated_root(run_root, label="run_root")
    data = Path(data_dir) if data_dir is not None else root / CLI_DATA_DIR_NAME
    config = Path(config_dir) if config_dir is not None else root / CLI_CONFIG_DIR_NAME
    root, checked = _validate_run_layout(
        root,
        {
            "data_dir": (data, "cli-data"),
            "config_dir": (config, "cli-config"),
        },
    )
    project = _validate_workspace(workspace, run_root=root)
    identity = _probe_identity(identity_probe, surface=CLI_SURFACE, executable=executable)
    argv: tuple[str, ...] = (
        executable,
        "--print",
        "--output-format",
        "stream-json",
        "--workspace",
        str(project),
    )
    if prompt is not None:
        argv += (prompt,)
    env = {
        "CURSOR_DATA_DIR": str(checked["data_dir"]),
        "CURSOR_CONFIG_DIR": str(checked["config_dir"]),
    }
    return CursorCliLaunchPlan(
        surface=CLI_SURFACE,
        executable=executable,
        argv=argv,
        env=env,
        run_root=root,
        workspace=project,
        data_dir=checked["data_dir"],
        config_dir=checked["config_dir"],
        identity=identity,
        prompt=prompt,
    )


def build_desktop_launch_plan(
    run_root: Path | str,
    workspace: Path | str,
    *,
    identity_probe: IdentityProbeInput | None,
    executable: str = DEFAULT_DESKTOP_EXECUTABLE,
    user_data_dir: Path | str | None = None,
    extensions_dir: Path | str | None = None,
) -> CursorDesktopLaunchPlan:
    """Build the isolated Cursor Desktop vector without opening the app."""

    if not isinstance(executable, str) or not executable.strip() or "\x00" in executable:
        raise CursorPreflightError("Desktop executable must be a non-empty safe string")
    root = _validate_isolated_root(run_root, label="run_root")
    user_data = Path(user_data_dir) if user_data_dir is not None else root / DESKTOP_USER_DATA_DIR_NAME
    extensions = Path(extensions_dir) if extensions_dir is not None else root / DESKTOP_EXTENSIONS_DIR_NAME
    root, checked = _validate_run_layout(
        root,
        {
            "user_data_dir": (user_data, "desktop-data"),
            "extensions_dir": (extensions, "desktop-extensions"),
        },
    )
    project = _validate_workspace(workspace, run_root=root)
    identity = _probe_identity(identity_probe, surface=DESKTOP_SURFACE, executable=executable)
    argv = (
        executable,
        "--user-data-dir",
        str(checked["user_data_dir"]),
        "--extensions-dir",
        str(checked["extensions_dir"]),
        str(project),
    )
    return CursorDesktopLaunchPlan(
        surface=DESKTOP_SURFACE,
        executable=executable,
        argv=argv,
        run_root=root,
        workspace=project,
        user_data_dir=checked["user_data_dir"],
        extensions_dir=checked["extensions_dir"],
        identity=identity,
    )


def build_launch(
    run_root: Path | str,
    workspace: Path | str,
    prompt: str | None = None,
    *,
    identity_probe: IdentityProbeInput | None = None,
    identity: CursorIdentity | Mapping[str, Any] | None = None,
    executable: str = DEFAULT_CLI_EXECUTABLE,
    data_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
) -> CursorCliLaunchPlan:
    """Compatibility builder for the Cursor CLI calibration route.

    ``identity`` is a static test/app identity, never an auth or history
    source.  ``identity_probe`` takes precedence when both are supplied.
    """

    selected_probe = identity_probe
    if selected_probe is None and identity is not None:
        selected_probe = identity.as_dict() if isinstance(identity, CursorIdentity) else identity
    return build_cli_launch_plan(
        run_root,
        workspace,
        identity_probe=selected_probe,
        executable=executable,
        prompt=prompt,
        data_dir=data_dir,
        config_dir=config_dir,
    )


def assert_surface_separation(
    cli_plan: CursorCliLaunchPlan,
    desktop_plan: CursorDesktopLaunchPlan,
) -> None:
    """Reject attempts to reuse a CLI root or identity for Desktop."""

    _ensure_surface(cli_plan.surface, CLI_SURFACE)
    _ensure_surface(desktop_plan.surface, DESKTOP_SURFACE)
    if cli_plan.identity.surface != CLI_SURFACE or desktop_plan.identity.surface != DESKTOP_SURFACE:
        raise SurfaceMismatchError("Cursor CLI and Desktop identities must remain separate")
    if set(cli_plan.isolation_roots) & set(desktop_plan.isolation_roots):
        raise SurfaceMismatchError("Cursor CLI and Desktop isolation roots must remain separate")
    if cli_plan.workspace == desktop_plan.workspace:
        # Sharing a synthetic workspace can be intentional only if both routes
        # are separately captured.  The plans still remain surface-distinct;
        # do not reject it.  This branch documents the distinction explicitly.
        return


@dataclass(frozen=True)
class CursorCliPreflight:
    """CLI preflight result; no model process has run."""

    plan: CursorCliLaunchPlan
    surface: Literal["cursor-cli"] = CLI_SURFACE
    ready_for_calibration: bool = True
    model_started: bool = False
    rankable: bool = False
    reasons: tuple[str, ...] = ()

    @property
    def launch_plan(self) -> CursorCliLaunchPlan:
        return self.plan


def preflight_cli(
    run_root: Path | str,
    workspace: Path | str,
    *,
    identity_probe: IdentityProbeInput | None,
    executable: str = DEFAULT_CLI_EXECUTABLE,
    data_dir: Path | str | None = None,
    config_dir: Path | str | None = None,
) -> CursorCliPreflight:
    """Perform static CLI preflight and return a non-running launch plan."""

    plan = build_cli_launch_plan(
        run_root,
        workspace,
        identity_probe=identity_probe,
        executable=executable,
        data_dir=data_dir,
        config_dir=config_dir,
    )
    return CursorCliPreflight(plan=plan)


@dataclass(frozen=True)
class CursorStreamRecord:
    """One bounded machine-readable stream event observed by the harness."""

    sequence: int
    event_type: str | None
    payload: Mapping[str, Any]
    response_text: str | None = None
    response_boundary: bool = False


@dataclass(frozen=True)
class CursorCliObservation:
    """Independent CLI stream observation, kept separate from native capture."""

    surface: Literal["cursor-cli"]
    records: tuple[CursorStreamRecord, ...]
    responses: tuple[str, ...]
    matched_canaries: tuple[str, ...]
    return_code: int | None = None
    source: str = "visible_stream"


def _event_type(value: Mapping[str, Any]) -> str | None:
    for key in ("type", "event", "kind", "subtype"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip().lower()
    return None


_RESPONSE_EVENT_TYPES = frozenset({
    "assistant", "assistant_message", "agent_message", "response", "result",
    "completed", "complete", "final", "text", "message",
})


def _response_text(value: Any, *, event_type: str | None, depth: int = 0) -> str | None:
    """Extract text from known stream-json response shapes, bounded by depth."""

    if depth > 5:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        preferred = ("text", "content", "result", "message", "delta", "output")
        values = [value[key] for key in preferred if key in value]
        if not values and event_type in _RESPONSE_EVENT_TYPES:
            values = [candidate for key, candidate in value.items() if key not in {"type", "event", "kind", "subtype"}]
        pieces: list[str] = []
        for candidate in values:
            extracted = _response_text(candidate, event_type=event_type, depth=depth + 1)
            if extracted:
                pieces.append(extracted)
        if pieces:
            return "".join(pieces)
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        pieces = []
        for candidate in value:
            extracted = _response_text(candidate, event_type=event_type, depth=depth + 1)
            if extracted:
                pieces.append(extracted)
        return "".join(pieces) if pieces else None
    return None


def _iter_stream_lines(stream: str | bytes | Iterable[str | bytes | Mapping[str, Any]]) -> Iterable[str]:
    if isinstance(stream, bytes):
        yield from stream.decode("utf-8", errors="strict").splitlines()
    elif isinstance(stream, str):
        yield from stream.splitlines()
    else:
        for line in stream:
            if isinstance(line, bytes):
                yield line.decode("utf-8", errors="strict").rstrip("\r\n")
            elif isinstance(line, str):
                yield line.rstrip("\r\n")
            elif isinstance(line, Mapping):
                # Accept structured fake runner events while retaining one
                # canonical JSONL representation for the observer ledger.
                yield json.dumps(line, ensure_ascii=False, separators=(",", ":"))
            else:
                raise CursorAdapterError("Cursor stream runner yielded a non-text event")


def observe_cli_stream(
    stream: str | bytes | Iterable[str | bytes | Mapping[str, Any]],
    *,
    expected_canaries: Sequence[str] = (),
    return_code: int | None = None,
    max_events: int = _MAX_STREAM_EVENTS,
    max_bytes: int = _MAX_STREAM_BYTES,
) -> CursorCliObservation:
    """Parse a bounded fake/runner stream without reading any native store.

    A canary counts only when a response-shaped event ends with that exact
    canary.  Merely seeing a canary in an arbitrary tool argument is rejected
    as an observation boundary.
    """

    if max_events < 1 or max_bytes < 1:
        raise ValueError("stream bounds must be positive")
    canaries = tuple(expected_canaries)
    if len(set(canaries)) != len(canaries) or any(not isinstance(item, str) or not item for item in canaries):
        raise CursorAdapterError("expected response canaries must be unique non-empty strings")
    records: list[CursorStreamRecord] = []
    responses: list[str] = []
    consumed = 0
    for sequence, line in enumerate(_iter_stream_lines(stream), 1):
        if sequence > max_events:
            raise CursorAdapterError("Cursor stream event bound exceeded")
        encoded = line.encode("utf-8")
        consumed += len(encoded) + 1
        if len(encoded) > _MAX_STREAM_LINE_BYTES:
            raise CursorAdapterError("Cursor stream line bound exceeded")
        if consumed > max_bytes:
            raise CursorAdapterError("Cursor stream byte bound exceeded")
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CursorAdapterError(f"Cursor stream event {sequence} is not JSON") from exc
        if not isinstance(payload, Mapping):
            raise CursorAdapterError(f"Cursor stream event {sequence} is not an object")
        event_kind = _event_type(payload)
        text = _response_text(payload, event_type=event_kind)
        boundary = bool(
            text
            and event_kind in _RESPONSE_EVENT_TYPES
            and (event_kind in {"result", "completed", "complete", "final"} or any(text.endswith(item) for item in canaries))
        )
        if boundary and text is not None:
            responses.append(text)
        records.append(CursorStreamRecord(sequence, event_kind, dict(payload), text if boundary else None, boundary))
    matched = tuple(item for item in canaries if any(text.endswith(item) for text in responses))
    if set(matched) != set(canaries):
        missing = tuple(item for item in canaries if item not in matched)
        if missing:
            raise CursorAdapterError("CLI stream did not prove response boundary canaries: " + ", ".join(missing))
    return CursorCliObservation(
        surface=CLI_SURFACE,
        records=tuple(records),
        responses=tuple(responses),
        matched_canaries=matched,
        return_code=return_code,
    )


def _runner_output(result: Any) -> tuple[str | bytes | Iterable[str | bytes | Mapping[str, Any]], int | None]:
    if isinstance(result, (str, bytes)):
        return result, None
    if isinstance(result, Mapping):
        stream = result.get("stdout", result.get("stream", result.get("output")))
        if stream is None:
            raise CursorAdapterError("injected Cursor runner returned no stream")
        code = result.get("returncode", result.get("return_code"))
        if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
            raise CursorAdapterError("injected Cursor runner returned an invalid return code")
        return stream, code
    stream = getattr(result, "stdout", result)
    code = getattr(result, "returncode", getattr(result, "return_code", None))
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise CursorAdapterError("injected Cursor runner returned an invalid return code")
    return stream, code


def run_cli(
    plan: CursorCliLaunchPlan,
    prompt: str | None = None,
    *,
    runner: CursorRunner,
    expected_canaries: Sequence[str] = (),
) -> CursorCliObservation:
    """Run through an injected runner and observe only its declared stream.

    The module has no subprocess fallback.  Passing a real runner is therefore
    an explicit integration decision made outside preflight; tests use a fake.
    """

    _ensure_surface(plan.surface, CLI_SURFACE)
    selected_prompt = plan.prompt if prompt is None else prompt
    if not isinstance(selected_prompt, str) or not selected_prompt.strip():
        raise CursorAdapterError("Cursor CLI prompt must be non-empty")
    if plan.prompt is not None and prompt is not None and prompt != plan.prompt:
        raise CursorAdapterError("runner prompt differs from the prompt bound to the CLI launch plan")
    if runner is None or not hasattr(runner, "run"):
        raise CursorAdapterError("an injected Cursor CLI runner is required")
    command = plan.argv if plan.prompt is not None else plan.argv + (selected_prompt,)
    result = runner.run(command, env=dict(plan.env), cwd=plan.workspace)
    stream, return_code = _runner_output(result)
    return observe_cli_stream(stream, expected_canaries=expected_canaries, return_code=return_code)


@dataclass(frozen=True)
class DesktopObservation:
    """Caller-supplied proof inputs for Desktop calibration readiness.

    These are assertions from an independent GUI observer/static root probe,
    not values collected by this module.  ``native_transcript_roots`` are
    paths only; this module never opens or copies them.
    """

    gui_observed: bool = False
    observer_bound_to_isolated_process: bool = False
    accessibility_text: str = ""
    visual_evidence_reviewed: bool = False
    native_transcript_roots: tuple[Path, ...] = ()
    native_roots_complete: bool = False
    response_canaries: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DesktopObservation":
        roots_value = value.get("native_transcript_roots", value.get("transcript_roots", ()))
        if isinstance(roots_value, (str, bytes)) or not isinstance(roots_value, Sequence):
            raise DesktopBlockedError("native transcript roots must be a sequence of paths")
        roots = tuple(_normal_abs(item, "native transcript root") for item in roots_value)
        canaries = value.get("response_canaries", ())
        if isinstance(canaries, (str, bytes)) or not isinstance(canaries, Sequence):
            raise DesktopBlockedError("Desktop response canaries must be a sequence")
        return cls(
            gui_observed=value.get("gui_observed", value.get("actual_gui_observation", False)) is True,
            observer_bound_to_isolated_process=value.get(
                "observer_bound_to_isolated_process",
                value.get("isolated_process_selected", False),
            ) is True,
            accessibility_text=value.get("accessibility_text", value.get("ax_text", "")) if isinstance(value.get("accessibility_text", value.get("ax_text", "")), str) else "",
            visual_evidence_reviewed=value.get("visual_evidence_reviewed", value.get("visual_reviewed", False)) is True,
            native_transcript_roots=roots,
            native_roots_complete=value.get("native_roots_complete", value.get("roots_complete", False)) is True,
            response_canaries=tuple(item for item in canaries if isinstance(item, str)),
        )


@dataclass(frozen=True)
class CursorDesktopPreflight:
    """Fail-closed Desktop preflight and optional calibration readiness."""

    plan: CursorDesktopLaunchPlan
    surface: Literal["cursor-desktop"] = DESKTOP_SURFACE
    observation: DesktopObservation | None = None
    ready_for_calibration: bool = False
    rankable: bool = False
    reasons: tuple[str, ...] = (
        "actual_gui_observation_required",
        "isolated_gui_process_selection_required",
        "accessibility_text_required",
        "reviewed_visual_evidence_required",
        "complete_native_transcript_roots_required",
    )

    @property
    def launch_plan(self) -> CursorDesktopLaunchPlan:
        return self.plan


def _desktop_observation(value: DesktopObservation | Mapping[str, Any] | None) -> DesktopObservation | None:
    if value is None:
        return None
    if isinstance(value, DesktopObservation):
        return value
    if isinstance(value, Mapping):
        return DesktopObservation.from_mapping(value)
    raise DesktopBlockedError("Desktop observation must be a mapping or DesktopObservation")


def _validate_desktop_observation(
    observation: DesktopObservation | None,
    *,
    user_data_dir: Path,
) -> tuple[bool, tuple[str, ...]]:
    if observation is None:
        return False, (
            "actual_gui_observation_required",
            "isolated_gui_process_selection_required",
            "accessibility_text_required",
            "reviewed_visual_evidence_required",
            "complete_native_transcript_roots_required",
        )
    reasons: list[str] = []
    if observation.gui_observed is not True:
        reasons.append("actual_gui_observation_required")
    if observation.observer_bound_to_isolated_process is not True:
        reasons.append("isolated_gui_process_selection_required")
    if not observation.accessibility_text.strip():
        reasons.append("accessibility_text_required")
    if observation.visual_evidence_reviewed is not True:
        reasons.append("reviewed_visual_evidence_required")
    if observation.native_roots_complete is not True or not observation.native_transcript_roots:
        reasons.append("complete_native_transcript_roots_required")
    seen: set[Path] = set()
    for root in observation.native_transcript_roots:
        if root in seen:
            reasons.append("duplicate_native_transcript_root")
            continue
        seen.add(root)
        if not _is_child(root, user_data_dir):
            reasons.append("native_transcript_root_outside_isolated_user_data")
    return not reasons, tuple(dict.fromkeys(reasons))


def preflight_desktop(
    run_root: Path | str,
    workspace: Path | str,
    *,
    identity_probe: IdentityProbeInput | None,
    executable: str = DEFAULT_DESKTOP_EXECUTABLE,
    user_data_dir: Path | str | None = None,
    extensions_dir: Path | str | None = None,
    observation: DesktopObservation | Mapping[str, Any] | None = None,
) -> CursorDesktopPreflight:
    """Build a safe Desktop launch plan and remain unrankable.

    An absent or incomplete observation is represented as a blocked result.  A
    complete observation can make the route ``ready_for_calibration`` but never
    flips ``rankable``; the native roots and GUI evidence still require the
    separate capture/evaluation workflow.
    """

    plan = build_desktop_launch_plan(
        run_root,
        workspace,
        identity_probe=identity_probe,
        executable=executable,
        user_data_dir=user_data_dir,
        extensions_dir=extensions_dir,
    )
    normalized = _desktop_observation(observation)
    ready, reasons = _validate_desktop_observation(
        normalized,
        user_data_dir=plan.user_data_dir,
    )
    return CursorDesktopPreflight(
        plan=plan,
        observation=normalized,
        ready_for_calibration=ready,
        rankable=False,
        reasons=reasons,
    )


def proposed_environment(run_root: Path | str) -> Mapping[str, str]:
    """Return the CLI isolation overrides without probing or launching Cursor."""

    root = _validate_isolated_root(run_root, label="run_root")
    _, checked = _validate_run_layout(
        root,
        {
            "data_dir": (root / CLI_DATA_DIR_NAME, "cli-data"),
            "config_dir": (root / CLI_CONFIG_DIR_NAME, "cli-config"),
        },
    )
    return {
        "CURSOR_DATA_DIR": str(checked["data_dir"]),
        "CURSOR_CONFIG_DIR": str(checked["config_dir"]),
    }


def calibration_command(
    plan: CursorCliLaunchPlan | CursorDesktopLaunchPlan,
    *,
    prompt: str | None = None,
) -> tuple[str, ...]:
    """Return a CLI command or refuse a Desktop calibration command.

    Desktop has no supported noninteractive command/observer route.  Keeping
    this guard explicit prevents a caller from treating its launch plan as a
    rankable Desktop acquisition.
    """

    if isinstance(plan, CursorDesktopLaunchPlan) or plan.surface == DESKTOP_SURFACE:
        raise DesktopBlockedError(
            "Cursor Desktop calibration is blocked until an isolated GUI observer and complete roots are proven"
        )
    _ensure_surface(plan.surface, CLI_SURFACE)
    if prompt is None:
        return plan.argv
    if not isinstance(prompt, str) or not prompt.strip():
        raise CursorAdapterError("Cursor CLI prompt must be non-empty")
    return plan.argv + (prompt,)


def require_desktop_calibration_ready(result: CursorDesktopPreflight) -> CursorDesktopPreflight:
    """Raise unless independent GUI and complete-root evidence is present."""

    _ensure_surface(result.surface, DESKTOP_SURFACE)
    if not result.ready_for_calibration:
        raise DesktopBlockedError("Cursor Desktop remains blocked: " + ", ".join(result.reasons))
    return result


def reject_desktop_ranked_result(*_args: Any, **_kwargs: Any) -> None:
    """Explicitly prevent Desktop preflight evidence from becoming a score."""

    raise DesktopBlockedError(
        "Cursor Desktop has no rankable result until the independent capture/evaluation workflow completes"
    )


@dataclass
class CursorCliAdapter:
    """Small stateful facade for controllers that inject dependencies once."""

    identity_probe: IdentityProbeInput | None = None
    runner: CursorRunner | None = None
    executable: str = DEFAULT_CLI_EXECUTABLE

    def build_launch_plan(
        self,
        run_root: Path | str,
        workspace: Path | str,
        *,
        identity_probe: IdentityProbeInput | None = None,
        prompt: str | None = None,
        data_dir: Path | str | None = None,
        config_dir: Path | str | None = None,
    ) -> CursorCliLaunchPlan:
        return build_cli_launch_plan(
            run_root,
            workspace,
            identity_probe=self.identity_probe if identity_probe is None else identity_probe,
            executable=self.executable,
            prompt=prompt,
            data_dir=data_dir,
            config_dir=config_dir,
        )

    def preflight(
        self,
        run_root: Path | str,
        workspace: Path | str,
        *,
        identity_probe: IdentityProbeInput | None = None,
        data_dir: Path | str | None = None,
        config_dir: Path | str | None = None,
    ) -> CursorCliPreflight:
        return preflight_cli(
            run_root,
            workspace,
            identity_probe=self.identity_probe if identity_probe is None else identity_probe,
            executable=self.executable,
            data_dir=data_dir,
            config_dir=config_dir,
        )

    def run(
        self,
        plan: CursorCliLaunchPlan,
        prompt: str | None = None,
        *,
        runner: CursorRunner | None = None,
        expected_canaries: Sequence[str] = (),
    ) -> CursorCliObservation:
        selected = self.runner if runner is None else runner
        if selected is None:
            raise CursorAdapterError("an injected Cursor CLI runner is required")
        return run_cli(plan, prompt, runner=selected, expected_canaries=expected_canaries)


@dataclass
class CursorDesktopAdapter:
    """Stateful Desktop facade; no method can produce a ranked result."""

    identity_probe: IdentityProbeInput | None = None
    executable: str = DEFAULT_DESKTOP_EXECUTABLE

    def build_launch_plan(
        self,
        run_root: Path | str,
        workspace: Path | str,
        *,
        identity_probe: IdentityProbeInput | None = None,
        user_data_dir: Path | str | None = None,
        extensions_dir: Path | str | None = None,
    ) -> CursorDesktopLaunchPlan:
        return build_desktop_launch_plan(
            run_root,
            workspace,
            identity_probe=self.identity_probe if identity_probe is None else identity_probe,
            executable=self.executable,
            user_data_dir=user_data_dir,
            extensions_dir=extensions_dir,
        )

    def preflight(
        self,
        run_root: Path | str,
        workspace: Path | str,
        *,
        identity_probe: IdentityProbeInput | None = None,
        user_data_dir: Path | str | None = None,
        extensions_dir: Path | str | None = None,
        observation: DesktopObservation | Mapping[str, Any] | None = None,
    ) -> CursorDesktopPreflight:
        return preflight_desktop(
            run_root,
            workspace,
            identity_probe=self.identity_probe if identity_probe is None else identity_probe,
            executable=self.executable,
            user_data_dir=user_data_dir,
            extensions_dir=extensions_dir,
            observation=observation,
        )

    def require_ready(self, result: CursorDesktopPreflight) -> CursorDesktopPreflight:
        return require_desktop_calibration_ready(result)

    def reject_ranked_result(self, *_args: Any, **_kwargs: Any) -> None:
        return reject_desktop_ranked_result(*_args, **_kwargs)


# Common capitalization/function aliases keep the protocol easy to integrate
# with callers that spell CLI as an initialism.
CursorCLIAdapter = CursorCliAdapter
CursorCLIPlan = CursorCliLaunchPlan
CursorDesktopPlan = CursorDesktopLaunchPlan
CursorCLIObservation = CursorCliObservation
CursorDesktopObservation = DesktopObservation
CursorCliIdentity = CursorIdentity
CursorDesktopIdentity = CursorIdentity
CursorCliLaunch = CursorCliLaunchPlan
CursorDesktopLaunch = CursorDesktopLaunchPlan
CURSOR_CLI_CONFIGURATION_ID = CLI_SURFACE
CURSOR_DESKTOP_CONFIGURATION_ID = DESKTOP_SURFACE
CURSOR_CLI_EXECUTABLE = DEFAULT_CLI_EXECUTABLE
CURSOR_DESKTOP_EXECUTABLE = DEFAULT_DESKTOP_EXECUTABLE
preflight_cursor_cli = preflight_cli
preflight_cursor_desktop = preflight_desktop
build_cursor_cli_plan = build_cli_launch_plan
build_cursor_desktop_plan = build_desktop_launch_plan


__all__ = [
    "CLI_SURFACE", "DESKTOP_SURFACE", "PROTOCOL_VERSION", "SURFACES",
    "CursorAdapterError", "CursorPreflightError", "SurfaceMismatchError", "DesktopBlockedError",
    "CursorIdentity", "StaticIdentityProbe", "CursorIdentityProbe", "CursorRunner",
    "CursorCliLaunchPlan", "CursorDesktopLaunchPlan", "CursorCliPreflight", "CursorDesktopPreflight",
    "CursorStreamRecord", "CursorCliObservation", "DesktopObservation",
    "build_cli_launch_plan", "build_desktop_launch_plan", "build_launch", "preflight_cli", "preflight_desktop",
    "observe_cli_stream", "run_cli", "assert_surface_separation",
    "require_desktop_calibration_ready", "reject_desktop_ranked_result",
    "CursorCliAdapter", "CursorDesktopAdapter",
    "CursorCLIAdapter", "CursorCLIPlan", "CursorDesktopPlan", "CursorCLIObservation", "CursorDesktopObservation",
    "CursorCliIdentity", "CursorDesktopIdentity", "CursorCliLaunch", "CursorDesktopLaunch",
    "CURSOR_CLI_CONFIGURATION_ID", "CURSOR_DESKTOP_CONFIGURATION_ID", "CURSOR_CLI_EXECUTABLE", "CURSOR_DESKTOP_EXECUTABLE",
    "preflight_cursor_cli", "preflight_cursor_desktop", "build_cursor_cli_plan", "build_cursor_desktop_plan",
    "proposed_environment", "calibration_command",
]
