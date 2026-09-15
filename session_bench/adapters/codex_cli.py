"""Bounded Codex CLI acquisition for the survival-v1 calibration surface.

This adapter has one deliberately narrow live route::

    CODEX_HOME=<fresh-isolated-root> codex exec --ignore-user-config --json ...

It never adds ``--ephemeral`` (that flag disables the native record this
surface is meant to measure), never discovers the normal Codex home, and never
copies credentials from a normal profile.  Tests inject a runner that writes a
synthetic rollout and emits stdout; the default module import performs no
process or filesystem acquisition.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import select
import subprocess
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..surface_capture import (
    ATTEMPT_STATUSES,
    AttemptManifest,
    ArgvEnvAllowlist,
    CaptureError,
    IndependentStdoutObserver,
    InventoryEntry,
    IsolatedRoots,
    QuiescenceReceipt,
    ResultManifest,
    build_result_manifest,
    canonical_bytes,
    copy_verified_artifacts,
    inventory_diff,
    inventory_tree,
    sha256_json,
    validate_attempt_manifest,
)


DEFAULT_EXECUTABLE = "/opt/homebrew/bin/codex"
CODEX_CLI_SURFACE_ID = "codex-cli"
CODEX_CLI_CONFIGURATION = "survival-v1-isolated-exec-json"
CODEX_REQUIRED_ARGV = ("exec", "--ignore-user-config", "--json")
CODEX_FORBIDDEN_ARGV = ("--ephemeral",)
CODEX_RUN_CANARY_ENV = "SB_SURVIVAL_V1_RUN_CANARY"
CODEX_ALLOWED_ENV = ("CODEX_HOME", CODEX_RUN_CANARY_ENV)
CODEX_REQUIRED_ENV = ("CODEX_HOME",)
CODEX_FORBIDDEN_ENV = ("HOME", "USERPROFILE", "CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_AUTH_TOKEN")


class CodexCLIError(RuntimeError):
    """The bounded Codex CLI route cannot produce an accepted attempt."""


class AuthenticationRequired(CodexCLIError):
    """An explicit isolated authentication prerequisite was not supplied."""


class SubprocessCodexRunner:
    """Stream one declared Codex command without inheriting a normal home."""

    def __init__(self, *, timeout_seconds: float = 300.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        on_stdout: Callable[[bytes], None],
    ) -> "RunnerResult":
        """Stream stdout once; keep only the isolated home and fixed PATH."""

        if set(env) - {"CODEX_HOME", CODEX_RUN_CANARY_ENV} or not env.get("CODEX_HOME"):
            raise CodexCLIError("subprocess runner requires only isolated CODEX_HOME and the synthetic run canary")
        run_canary = env.get(CODEX_RUN_CANARY_ENV)
        if run_canary is not None and (
            not run_canary.startswith("SB_SURVIVAL_V1_RUN_")
            or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for char in run_canary)
        ):
            raise CodexCLIError("subprocess runner received an invalid synthetic run canary")
        child_env = {
            "CODEX_HOME": env["CODEX_HOME"],
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
        }
        if run_canary is not None:
            child_env[CODEX_RUN_CANARY_ENV] = run_canary
        try:
            process = subprocess.Popen(
                list(argv), cwd=str(cwd), env=child_env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CodexCLIError(f"cannot start declared Codex command: {exc}") from exc
        assert process.stdout is not None
        pending = b""
        deadline = time.monotonic() + self.timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(list(argv), self.timeout_seconds)
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                if not ready:
                    raise subprocess.TimeoutExpired(list(argv), self.timeout_seconds)
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    on_stdout(line + b"\n")
            if pending:
                on_stdout(pending)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(argv), self.timeout_seconds)
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise CodexCLIError("declared Codex command timed out") from exc
        finally:
            process.stdout.close()
        return RunnerResult(returncode=returncode)


@dataclass(frozen=True)
class AuthenticationPrerequisite:
    """A credential-free proof that the owner has prepared isolated auth.

    ``proof_id`` is an opaque reference (for example ``fixture-auth-1``); it
    must never contain a token, credential, home path, or credential bytes.
    The adapter does not provision, copy, or inspect the credential itself.
    ``source="existing-account"`` is permitted when the owner explicitly
    verifies the product's supported account path.  That path is still
    metadata-discovered only until one new rollout is proven.
    """

    verified: bool
    source: str = ""
    proof_id: str = ""
    personal_credentials_copied: bool = False

    @property
    def ready(self) -> bool:
        source = self.source.lower()
        banned = ("normal", "personal", "profile", "home", "credential", "token", "secret")
        return bool(
            self.verified
            and self.source
            and self.proof_id
            and not self.personal_credentials_copied
            and not any(part in source for part in banned)
        )

    def require(self) -> None:
        if not self.ready:
            raise AuthenticationRequired(
                "Codex CLI acquisition requires an explicit isolated authentication "
                "prerequisite; normal-profile credentials are never copied"
            )

    @classmethod
    def fixture(cls, proof_id: str = "fixture-auth") -> "AuthenticationPrerequisite":
        return cls(True, "fixture", proof_id, False)

    @classmethod
    def existing_account(cls, proof_id: str = "existing-account-auth") -> "AuthenticationPrerequisite":
        return cls(True, "existing-account", proof_id, False)


AuthPrerequisite = AuthenticationPrerequisite
CodexCLIAuth = AuthenticationPrerequisite


def _normalize_auth(value: AuthenticationPrerequisite | Mapping[str, Any] | None) -> AuthenticationPrerequisite:
    if isinstance(value, AuthenticationPrerequisite):
        return value
    if isinstance(value, Mapping):
        return AuthenticationPrerequisite(
            verified=value.get("verified") is True,
            source=value.get("source", ""),
            proof_id=value.get("proof_id", value.get("evidence_id", "")),
            personal_credentials_copied=value.get("personal_credentials_copied", False) is True,
        )
    return AuthenticationPrerequisite(False)


@dataclass(frozen=True)
class CodexCLIIdentity:
    """Exact identity fields bound to every attempt configuration."""

    build: str
    model: str
    protocol_version: str = "1.0-survival"
    workload_version: str = "1.0-survival-workload"
    observer_schema_version: str = "1.0-survival-observer"
    rubric_version: str = "1.0-survival-rubric"
    os_name: str = ""
    configuration: str = CODEX_CLI_CONFIGURATION

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": "openai",
            "harness": "codex-cli",
            "surface": "cli",
            "execution_mode": "exec-json",
            "os": self.os_name or platform.platform(),
            "build": self.build,
            "model": self.model,
            "configuration": self.configuration,
            "protocol_version": self.protocol_version,
            "workload_version": self.workload_version,
            "observer_schema_version": self.observer_schema_version,
            "rubric_version": self.rubric_version,
        }


@dataclass(frozen=True)
class CodexCLIRunRoots:
    """Convenience constructor for the five non-overlapping isolated roots."""

    roots: IsolatedRoots

    @classmethod
    def create(cls, run_root: Path) -> "CodexCLIRunRoots":
        run_root = Path(run_root).expanduser().resolve()
        home = Path.home().resolve()
        if run_root == home or home in run_root.parents:
            raise CodexCLIError("run root cannot be the user's home or a child of it")
        if run_root.exists():
            if not run_root.is_dir():
                raise CodexCLIError("run root is not a directory")
            if any(run_root.iterdir()):
                raise CodexCLIError("run root must be fresh and empty")
        else:
            run_root.mkdir(parents=True, exist_ok=False)
        native = run_root / "codex-home"
        project = run_root / "project"
        observer = run_root / "observer"
        capture = run_root / "capture"
        for path in (native, project, observer, capture):
            path.mkdir(mode=0o700)
        roots = IsolatedRoots(run_root, project, native, observer, capture)
        roots.validate()
        return cls(roots)

    @classmethod
    def for_existing_account(cls, run_root: Path, native_root: Path) -> "CodexCLIRunRoots":
        """Prepare isolated capture/observer roots around an authorized CODEX_HOME.

        This constructor does not inspect ``native_root`` and does not copy
        credentials.  The caller must supply ``AuthenticationPrerequisite``
        with ``source="existing-account"`` to use it.
        """

        run_root = Path(run_root).expanduser().resolve()
        home = Path.home().resolve()
        if run_root == home or home in run_root.parents:
            raise CodexCLIError("run root cannot be the user's home or a child of it")
        native_root = Path(native_root).expanduser().resolve()
        if run_root.exists():
            if not run_root.is_dir() or any(run_root.iterdir()):
                raise CodexCLIError("run root must be a fresh empty directory")
        else:
            run_root.mkdir(parents=True, exist_ok=False)
        project = run_root / "project"
        observer = run_root / "observer"
        capture = run_root / "capture"
        for path in (project, observer, capture):
            path.mkdir(mode=0o700)
        roots = IsolatedRoots(run_root, project, native_root, observer, capture, native_isolated=False)
        roots.validate()
        return cls(roots)


def _allowlist() -> ArgvEnvAllowlist:
    return ArgvEnvAllowlist(
        required_argv=CODEX_REQUIRED_ARGV,
        forbidden_argv=CODEX_FORBIDDEN_ARGV,
        allowed_env=CODEX_ALLOWED_ENV,
        required_env=CODEX_REQUIRED_ENV,
        forbidden_env=CODEX_FORBIDDEN_ENV,
    )


@dataclass(frozen=True)
class CodexCLILaunch:
    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    allowlist: ArgvEnvAllowlist

    def validate(self) -> "CodexCLILaunch":
        self.allowlist.validate(self.argv, self.env)
        if "--ephemeral" in self.argv:
            raise CodexCLIError("--ephemeral is forbidden for survival-v1 native capture")
        if not Path(self.cwd).is_absolute():
            raise CodexCLIError("Codex CLI cwd must be an isolated absolute project root")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "argv": list(self.argv),
            "env": dict(self.env),
            "cwd": str(Path(self.cwd).resolve()),
            "argv_env_allowlist": self.allowlist.to_dict(),
        }


@dataclass(frozen=True)
class RunnerResult:
    """Minimal injected-runner return value."""

    returncode: int = 0
    stdout: bytes | str = b""


class CodexRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        on_stdout: Callable[[bytes | str], None],
    ) -> RunnerResult | Mapping[str, Any] | int: ...


def build_codex_argv(
    project_root: Path,
    prompt: str,
    *,
    executable: str = DEFAULT_EXECUTABLE,
) -> tuple[str, ...]:
    """Build the only supported Codex CLI route for this adapter."""

    if not isinstance(prompt, str) or not prompt:
        raise CodexCLIError("survival-v1 prompt must be non-empty text")
    project_root = Path(project_root).resolve()
    if not project_root.is_absolute():  # pragma: no cover - resolve currently guarantees this
        raise CodexCLIError("project root must be absolute")
    if not isinstance(executable, str) or not executable:
        raise CodexCLIError("Codex executable must be a non-empty path")
    argv = (executable, "exec", "--ignore-user-config", "--json", "-C", str(project_root), prompt)
    _allowlist().validate(argv, {"CODEX_HOME": "<isolated-root>"})
    return argv


def build_codex_launch(roots: IsolatedRoots, prompt: str, *, executable: str = DEFAULT_EXECUTABLE) -> CodexCLILaunch:
    roots.validate()
    launch = CodexCLILaunch(
        argv=build_codex_argv(roots.project_root, prompt, executable=executable),
        env={"CODEX_HOME": str(Path(roots.native_root).resolve())},
        cwd=Path(roots.project_root).resolve(),
        allowlist=_allowlist(),
    )
    return launch.validate()


make_launch = build_codex_launch


def _normalize_runner_result(value: RunnerResult | Mapping[str, Any] | int | None) -> RunnerResult:
    if value is None:
        return RunnerResult()
    if isinstance(value, RunnerResult):
        result = value
    elif type(value) is int:
        result = RunnerResult(returncode=value)
    elif isinstance(value, Mapping):
        returncode = value.get("returncode", value.get("exit_code", 0))
        stdout = value.get("stdout", value.get("visible_text", b""))
        if type(returncode) is not int or not isinstance(stdout, (bytes, str)):
            raise CodexCLIError("injected runner result has an invalid shape")
        result = RunnerResult(returncode, stdout)
    else:
        raise CodexCLIError("injected runner result has an invalid type")
    if type(result.returncode) is not int:
        raise CodexCLIError("runner returncode must be an integer")
    if not isinstance(result.stdout, (bytes, str)):
        raise CodexCLIError("runner stdout must be bytes or text")
    return result


def _empty_quiescence() -> dict[str, Any]:
    return {"checks": 0, "interval_seconds": 0.0, "stable": False, "observed": []}


def _safe_reason(prefix: str, exc: BaseException | None = None) -> str:
    if exc is None:
        return prefix
    detail = str(exc).strip().replace("\n", " ")
    return f"{prefix}: {type(exc).__name__}" + (f" ({detail})" if detail else "")


class CodexCLIAdapter:
    """Acquire one bounded Codex CLI attempt through an injected runner."""

    def __init__(
        self,
        runner: CodexRunner,
        *,
        executable: str = DEFAULT_EXECUTABLE,
        max_files: int = 256,
        max_file_bytes: int = 16 * 1024 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
        quiescence_checks: int = 2,
        quiescence_interval_seconds: float = 0.0,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.executable = executable
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.quiescence_checks = quiescence_checks
        self.quiescence_interval_seconds = quiescence_interval_seconds
        self.clock_ns = clock_ns
        self.sleep = sleep
        if max_files < 1 or max_file_bytes < 0 or max_total_bytes < 0:
            raise ValueError("invalid capture limits")
        if quiescence_checks < 2 or quiescence_interval_seconds < 0:
            raise ValueError("quiescence requires at least two checks")

    @staticmethod
    def candidate_path(path: str) -> bool:
        name = Path(path).name
        return name.startswith("rollout-") and name.endswith(".jsonl")

    def calibration_plan(
        self,
        roots: IsolatedRoots,
        prompt: str,
        *,
        identity: CodexCLIIdentity,
    ) -> dict[str, Any]:
        """Return a dry launch plan without checking auth or invoking a runner."""

        launch = build_codex_launch(roots, prompt, executable=self.executable)
        config = identity.to_dict()
        config.update({
            "native_root_role": "CODEX_HOME",
            "native_capture_family": "codex-rollout-jsonl",
            "ephemeral": False,
            "argv_env_allowlist": launch.allowlist.to_dict(),
        })
        return {
            "surface_id": CODEX_CLI_SURFACE_ID,
            "config_identity": config,
            "config_sha256": sha256_json(config),
            "launch": launch.to_dict(),
            "forbidden": ["--ephemeral", "HOME", "normal-profile credential copy"],
        }

    def _invoke(self, launch: CodexCLILaunch, observer: IndependentStdoutObserver) -> RunnerResult:
        run = getattr(self.runner, "run", None)
        if not callable(run):
            raise CodexCLIError("injected runner must provide run(argv, env, cwd, on_stdout)")
        value = run(launch.argv, env=launch.env, cwd=launch.cwd, on_stdout=observer.observe)
        result = _normalize_runner_result(value)
        if result.stdout:
            observer.observe(result.stdout)
        return result

    def _base_manifest(
        self,
        *,
        attempt_id: str,
        result_id: str,
        identity: Mapping[str, Any],
        launch: CodexCLILaunch,
        roots: IsolatedRoots,
        before: Sequence[InventoryEntry],
        after: Sequence[InventoryEntry],
        observer: Mapping[str, Any],
        artifacts: Sequence[Mapping[str, Any]],
        status: str,
        reason: str,
        started_ns: int,
        ended_ns: int | None,
        quiescence: Mapping[str, Any],
    ) -> AttemptManifest:
        value = {
            "schema_version": "1.0-survival-surface-capture",
            "kind": "attempt",
            "attempt_id": attempt_id,
            "result_id": result_id,
            "surface_id": CODEX_CLI_SURFACE_ID,
            "config_identity": dict(identity),
            "config_sha256": sha256_json(identity),
            "argv": list(launch.argv),
            "env": dict(launch.env),
            "argv_env_allowlist": launch.allowlist.to_dict(),
            "roots": roots.as_dict(),
            "before_inventory": [entry.to_dict() for entry in before],
            "after_inventory": [entry.to_dict() for entry in after],
            "quiescence": dict(quiescence),
            "observer": dict(observer),
            "artifacts": [dict(item) for item in artifacts],
            "limits": {
                "max_files": self.max_files,
                "max_file_bytes": self.max_file_bytes,
                "max_total_bytes": self.max_total_bytes,
            },
            "status": status,
            "reason": reason,
            "started_ns": started_ns,
            "ended_ns": ended_ns,
        }
        return AttemptManifest(value)

    def acquire(
        self,
        *,
        roots: IsolatedRoots,
        auth: AuthenticationPrerequisite | Mapping[str, Any] | None,
        prompt: str,
        attempt_id: str,
        result_id: str,
        identity: CodexCLIIdentity | Mapping[str, Any],
        ledger: Any | None = None,
    ) -> tuple[AttemptManifest, ResultManifest]:
        """Run one injected acquisition and return immutable attempt/result records.

        ``ledger`` may be an ``ImmutableAttemptLedger``.  When supplied, the
        terminal attempt is appended exactly once, including blocked or
        invalid attempts.  No process is invoked until authentication and all
        isolated roots/launch checks pass.
        """

        roots.validate()
        prerequisite = _normalize_auth(auth)
        identity_value = identity.to_dict() if isinstance(identity, CodexCLIIdentity) else dict(identity)
        if not identity_value:
            raise CodexCLIError("exact configuration identity is required")
        launch = build_codex_launch(roots, prompt, executable=self.executable)
        started_ns = self.clock_ns()
        observer = IndependentStdoutObserver(max_bytes=self.max_total_bytes)
        before: tuple[InventoryEntry, ...] = ()
        after: tuple[InventoryEntry, ...] = ()
        artifacts: tuple[dict[str, Any], ...] = ()
        quiescence: Mapping[str, Any] = _empty_quiescence()
        status = "blocked"
        reason = "authentication prerequisite was not verified"
        try:
            prerequisite.require()
            # The native root is the isolated CODEX_HOME.  Inventory is
            # metadata-only and includes every regular file for a complete
            # before/after write boundary.
            before = inventory_tree(roots.native_root)
            execution = self._invoke(launch, observer)
            after = inventory_tree(roots.native_root)
            new_entries = inventory_diff(before, after, started_ns=started_ns)
            candidate_entries = tuple(
                entry for entry in new_entries if self.candidate_path(entry.relative_path)
            )
            if len(candidate_entries) != 1:
                raise CaptureError(
                    f"expected exactly one new Codex rollout; found {len(candidate_entries)}"
                )
            if len(new_entries) != len(candidate_entries):
                extras = [entry.relative_path for entry in new_entries if entry not in candidate_entries]
                raise CaptureError(f"unexpected new native files: {extras}")
            receipt = self._quiesce(roots.native_root, candidate_entries)
            quiescence = receipt.to_dict()
            # Freeze the independent stream before copying native bytes.
            observer_receipt = observer.freeze(roots.observer_root / f"{attempt_id}.stdout.jsonl")
            artifacts = copy_verified_artifacts(
                roots.native_root,
                roots.capture_root,
                candidate_entries,
                max_files=self.max_files,
                max_file_bytes=self.max_file_bytes,
                max_total_bytes=self.max_total_bytes,
                roles={candidate_entries[0].relative_path: "native-primary"},
            )
            status = "captured"
            reason = f"codex exec exited with {execution.returncode}"
        except AuthenticationRequired as exc:
            # Do not inventory or invoke a runner without explicit isolated
            # auth.  Empty frozen observer metadata retains the blocked attempt.
            reason = _safe_reason("authentication prerequisite required", exc)
            observer_receipt = observer.freeze()
        except Exception as exc:
            status = "invalid"
            reason = _safe_reason("capture rejected", exc)
            try:
                observer_receipt = observer.freeze()
            except Exception as freeze_exc:  # pragma: no cover - defensive
                reason = _safe_reason(reason, freeze_exc)
                observer_receipt = {
                    "method": "runner-stdout",
                    "independent": True,
                    "frozen": True,
                    "event_count": 0,
                    "size_bytes": 0,
                    "sha256": sha256_json(""),
                    "artifact": None,
                }
        ended_ns = self.clock_ns()
        if ended_ns < started_ns:
            ended_ns = started_ns
        attempt = self._base_manifest(
            attempt_id=attempt_id,
            result_id=result_id,
            identity=identity_value,
            launch=launch,
            roots=roots,
            before=before,
            after=after,
            observer=observer_receipt,
            artifacts=artifacts,
            status=status,
            reason=reason,
            started_ns=started_ns,
            ended_ns=ended_ns,
            quiescence=quiescence,
        )
        if ledger is not None:
            ledger.append(attempt)
        result = build_result_manifest(attempt, status=status, reason=reason)
        return attempt, result

    calibrate = acquire
    run = acquire

    def _quiesce(self, root: Path, entries: Sequence[InventoryEntry]) -> QuiescenceReceipt:
        from ..surface_capture import wait_for_quiescence

        return wait_for_quiescence(
            root,
            entries,
            sleep=self.sleep,
            interval_seconds=self.quiescence_interval_seconds,
            checks=self.quiescence_checks,
        )


CodexCLI = CodexCLIAdapter


__all__ = [
    "AuthPrerequisite", "AuthenticationPrerequisite", "AuthenticationRequired", "CODEX_ALLOWED_ENV",
    "CODEX_CLI_CONFIGURATION", "CODEX_CLI_SURFACE_ID", "CODEX_FORBIDDEN_ARGV", "CODEX_FORBIDDEN_ENV",
    "CODEX_REQUIRED_ARGV", "CODEX_REQUIRED_ENV", "CodexCLI", "CodexCLIAdapter", "CodexCLIAuth",
    "CodexCLIError", "CodexCLIIdentity", "CodexCLILaunch", "CodexCLIRunRoots", "CodexRunner", "SubprocessCodexRunner",
    "DEFAULT_EXECUTABLE", "RunnerResult", "build_codex_argv", "build_codex_launch", "make_launch",
]
