"""Bounded L0 controller seams.

All external effects are behind an injected runner.  The module is therefore
safe to exercise with constructed inputs without launching Codex or reading a
session store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Protocol, Sequence

from .bundle import canonical
from .live_ledger import validate_capture_evidence, validate_live_ledger
from .live_plan import plan_sha256, validate_live_plan
from .l0_preflight import EffectiveConfig, build_effective_config, identify_single_new_candidate, quota_decision, stat_inventory, verify_resolved_config


class Runner(Protocol):
    def enumerate_mcp_names(self, argv: Sequence[str]) -> Mapping[str, bool]: ...
    def resolve_features(self, argv: Sequence[str]) -> Mapping[str, bool]: ...
    def sandbox_probe(self, argv: Sequence[str], scratch: Path, sibling: Path) -> Mapping[str, bool]: ...


class LocalCodexRunner:
    """Run only bounded, non-model Codex inspection and sandbox-probe commands."""

    def __init__(self, executable: str = "/opt/homebrew/bin/codex", timeout: int = 30):
        self.executable = executable
        self.timeout = timeout

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(tuple(command), capture_output=True, text=True, timeout=self.timeout)
        if result.returncode:
            raise RuntimeError(f"preflight command failed with exit {result.returncode}: {result.stderr.strip()}")
        return result

    def enumerate_mcp_names(self, argv: Sequence[str]) -> Mapping[str, bool]:
        result = self._run((self.executable, "mcp", "list", "--json", *argv))
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex MCP output is not JSON") from exc
        if not isinstance(rows, list):
            raise ValueError("Codex MCP output schema changed")
        names: dict[str, bool] = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or type(row.get("enabled")) is not bool:
                raise ValueError("Codex MCP output schema changed")
            if row["name"] in names:
                raise ValueError("duplicate Codex MCP name")
            names[row["name"]] = row["enabled"]
        return names

    def resolve_features(self, argv: Sequence[str]) -> Mapping[str, bool]:
        result = self._run((self.executable, "features", "list", *argv))
        wanted = {
            "apps", "browser_use", "browser_use_external", "computer_use", "hooks",
            "image_generation", "in_app_browser", "memories", "multi_agent", "plugins",
            "skill_search", "tool_suggest", "workspace_dependencies", "skip_host_skill_discovery",
        }
        resolved: dict[str, bool] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if not parts or parts[0] not in wanted:
                continue
            if parts[-1] not in {"true", "false"}:
                raise ValueError("Codex feature output schema changed")
            resolved[parts[0]] = parts[-1] == "true"
        if set(resolved) != wanted:
            raise ValueError("Codex feature output omitted an expected name")
        return resolved

    def sandbox_probe(self, argv: Sequence[str], scratch: Path, sibling: Path) -> Mapping[str, bool]:
        marker = Path(scratch) / "sb-probe-ok"
        if marker.exists() or Path(sibling).exists():
            raise ValueError("sandbox probe paths must not pre-exist")
        probe = (
            "import errno,pathlib,socket,sys;"
            "scratch=pathlib.Path(sys.argv[1]);sibling=pathlib.Path(sys.argv[2]);"
            "(scratch/'sb-probe-ok').write_text('ok');denied=False\n"
            "try:sibling.write_text('bad')\n"
            "except PermissionError:denied=True\n"
            "s=socket.socket();rc=s.connect_ex(('1.1.1.1',443));s.close();"
            "print(__import__('json').dumps({'scratch_write_allowed':(scratch/'sb-probe-ok').read_text()=='ok','undeclared_sibling_write_denied':denied,'outbound_network_denied':rc in (errno.EPERM,errno.EACCES)}))"
        )
        try:
            result = self._run((self.executable, "sandbox", *argv, "-P", ":workspace", "-C", str(scratch),
                                "--sandbox-state-disable-network", "--", "/usr/bin/python3", "-c", probe,
                                str(scratch), str(sibling)))
        finally:
            marker.unlink(missing_ok=True)
            Path(sibling).unlink(missing_ok=True)
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("sandbox probe output is not JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("sandbox probe output schema changed")
        return value


@dataclass
class HardCounters:
    attempts: int = 0
    submitted_turns: int = 0
    native_sessions: int = 0
    captured_files: int = 0
    captured_bytes: int = 0
    observable_tokens: int = 0
    spend_usd: float = 0
    operator_minutes: float = 0
    wall_clock_minutes: float = 0

    def check(self, plan: Mapping[str, Any]) -> None:
        limits = plan["limits"]
        if self.attempts >= limits["attempts_total"]:
            raise RuntimeError("attempt limit reached")
        if self.submitted_turns >= limits["submitted_turns"]:
            raise RuntimeError("submitted-turn limit reached")
        if self.native_sessions >= limits["native_sessions"]:
            raise RuntimeError("native-session limit reached")
        if self.captured_files >= limits["artifact_files"] or self.captured_bytes >= limits["artifact_total_bytes"]:
            raise RuntimeError("capture artifact limit reached")
        if self.observable_tokens >= limits["tokens_total_when_observable"]:
            raise RuntimeError("observable-token limit reached")
        if self.spend_usd > limits["incremental_spend_usd"]:
            raise RuntimeError("spend limit reached")
        if self.operator_minutes >= limits["operator_minutes"] or self.wall_clock_minutes >= limits["wall_clock_minutes"]:
            raise RuntimeError("time limit reached")

    def reserve(self, plan: Mapping[str, Any], **increments: float) -> None:
        limits = plan["limits"]
        mapping = {
            "attempts": "attempts_total", "submitted_turns": "submitted_turns",
            "native_sessions": "native_sessions", "captured_files": "artifact_files",
            "captured_bytes": "artifact_total_bytes", "observable_tokens": "tokens_total_when_observable",
            "spend_usd": "incremental_spend_usd", "operator_minutes": "operator_minutes",
            "wall_clock_minutes": "wall_clock_minutes",
        }
        for field_name, amount in increments.items():
            if field_name not in mapping or amount < 0:
                raise ValueError("invalid counter reservation")
            if getattr(self, field_name) + amount > limits[mapping[field_name]]:
                raise RuntimeError(f"{field_name.replace('_', '-')} limit reached")
        for field_name, amount in increments.items():
            setattr(self, field_name, getattr(self, field_name) + amount)


@dataclass(frozen=True)
class PTYEvent:
    sequence: int
    kind: str
    text: str
    status: str | None = None


@dataclass
class PTYObserver:
    events: list[PTYEvent] = field(default_factory=list)
    frozen: bool = False

    def record(self, kind: str, text: str, status: str | None = None) -> PTYEvent:
        if self.frozen:
            raise RuntimeError("observer ledger is frozen")
        event = PTYEvent(len(self.events) + 1, kind, text, status)
        self.events.append(event)
        return event

    def freeze(self) -> tuple[PTYEvent, ...]:
        self.frozen = True
        return tuple(self.events)


def exact_argv(config: EffectiveConfig, scratch: Path, executable: str = "/opt/homebrew/bin/codex") -> tuple[str, ...]:
    return (executable, *config.argv, "--no-alt-screen", "-C", str(Path(scratch)),
            "--sandbox", "workspace-write", "--ask-for-approval", "never")


def explicit_decode_package(source: Path, output: Path, paths: Sequence[str]) -> tuple[str, ...]:
    """Copy only explicitly authorized native paths; reject traversal and symlinks."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError("decode output must be outside the immutable native source")
    output.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    for relative in paths:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("decode path escapes explicit source")
        source_path = source / rel
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError("decode path is not an ordinary file")
        target = output / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        copied.append(rel.as_posix())
    return tuple(sorted(copied))


def build_codex_decode_package(source_rollout: Path, output: Path, companions: Sequence[Path] = ()) -> Path:
    """Copy one proven-new rollout into a complete explicit decoder package."""
    source_rollout = Path(source_rollout)
    output = Path(output)
    companion_paths = tuple(Path(path) for path in companions)
    if any(path.parent.resolve() != source_rollout.parent.resolve() for path in companion_paths):
        raise ValueError("companions must share the proven rollout source directory")
    names = (source_rollout.name, *(path.name for path in companion_paths))
    explicit_decode_package(source_rollout.parent, output, names)
    artifacts = []
    for index, name in enumerate(names):
        copied = output / name
        data = copied.read_bytes()
        artifacts.append({
            "id": "rollout" if index == 0 else f"companion-{index}", "path": name,
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
            "depends_on": [] if index == 0 else ["rollout"],
        })
    decode = {"format": "codex-rollout-v1", "artifacts": artifacts}
    (output / "decode.json").write_bytes(canonical(decode) + b"\n")
    findings = privacy_scan(output)
    if findings:
        raise ValueError(f"copied package failed privacy scan: {findings}")
    return output


def privacy_scan(root: Path) -> tuple[str, ...]:
    findings: list[str] = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        for marker in (b"BEGIN PRIVATE KEY", b"api_key=", b"Authorization: Bearer", b"/Users/"):
            if marker in data:
                findings.append(f"{path.relative_to(root)}:{marker.decode('ascii', 'replace')}")
    return tuple(findings)


def capture_candidate(before: Sequence[Any], after: Sequence[Any]) -> Any:
    return identify_single_new_candidate(before, after)


@dataclass
class L0Controller:
    plan: Mapping[str, Any]
    runner: Runner
    counters: HardCounters = field(default_factory=HardCounters)
    observer: PTYObserver = field(default_factory=PTYObserver)

    def __post_init__(self) -> None:
        validate_live_plan(self.plan)

    def preflight(self, scratch: Path, sibling: Path, *, quota: Any, now_monotonic: float) -> dict[str, Any]:
        decision = quota_decision(
            quota,
            plan=self.plan,
            now_monotonic=now_monotonic,
        )
        if decision != "proceed":
            raise RuntimeError(decision)
        names = self.runner.enumerate_mcp_names(())
        if not isinstance(names, Mapping) or any(not isinstance(k, str) for k in names):
            raise ValueError("MCP enumeration must contain names only")
        config = build_effective_config(disable_mcps=tuple(names))
        features = self.runner.resolve_features(config.argv)
        resolved_mcp = self.runner.enumerate_mcp_names(config.argv)
        resolved_fingerprint = verify_resolved_config(config, features=features, mcps=resolved_mcp)
        sandbox = self.runner.sandbox_probe(config.argv, Path(scratch), Path(sibling))
        required = ("scratch_write_allowed", "undeclared_sibling_write_denied", "outbound_network_denied")
        if any(sandbox.get(key) is not True for key in required):
            raise RuntimeError("sandbox preflight failed")
        return {"argv": exact_argv(config, scratch), "override_fingerprint": config.override_fingerprint,
                "resolved_fingerprint": resolved_fingerprint, "mcp_names": tuple(sorted(names)),
                "sandbox": dict(sandbox), "plan_sha256": plan_sha256(self.plan)}

    def capture_evidence(self, before: Sequence[Any], after: Sequence[Any], *, opened: Sequence[str], companions: Sequence[str] = (), attempt_id: str = "attempt-1", native_session_ids: Sequence[str] = ("native-1",), source_mutated: bool = False) -> dict[str, Any]:
        candidate_paths = [item.relative_path for item in after if item.relative_path not in {x.relative_path for x in before}]
        as_stat = lambda item: {"relative_path": item.relative_path, "filesystem_id": f"{item.device}:{item.inode}",
                                "birth_time": item.birth_ns, "ctime": item.ctime_ns, "mtime": item.mtime_ns, "size": item.size}
        evidence = {"attempt_id": attempt_id, "native_session_ids": list(native_session_ids),
                    "before_stats": [as_stat(item) for item in before], "after_stats": [as_stat(item) for item in after],
                    "primary_candidate_path": candidate_paths[0] if len(candidate_paths) == 1 else None,
                    "companion_paths": list(companions), "candidate_paths": candidate_paths, "opened_paths": list(opened),
                    "preexisting_file_hashing": False, "ambiguous": len(candidate_paths) != 1,
                    "source_mutated": source_mutated, "observer_frozen": self.observer.frozen}
        attempt = {"state": "captured", "attempt_id": attempt_id, "native_session_ids": list(native_session_ids)}
        validate_capture_evidence(evidence, self.plan, attempt=attempt)
        return evidence

    def ledger(self, attempts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        ledger = {"schema_version": "1.0-live-ledger", "gate_id": self.plan["gate_id"],
                  "plan_sha256": plan_sha256(self.plan), "attempts": list(attempts)}
        validate_live_ledger(ledger, self.plan)
        return ledger


def dry_run(plan: Mapping[str, Any], mcp_names: Sequence[str], scratch: Path) -> dict[str, Any]:
    validate_live_plan(plan)
    config = build_effective_config(disable_mcps=mcp_names)
    return {"plan_sha256": plan_sha256(plan), "argv": exact_argv(config, scratch),
            "override_fingerprint": config.override_fingerprint, "mcp_names": tuple(sorted(set(mcp_names)))}
