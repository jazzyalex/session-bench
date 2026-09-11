"""Fail-closed, side-effect-free helpers for the bounded Codex CLI F0 controller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Callable, Mapping, Sequence


_MCP_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_DISABLED_FEATURES = (
    "apps", "browser_use", "browser_use_external", "computer_use", "hooks",
    "image_generation", "in_app_browser", "memories", "multi_agent", "plugins",
    "skill_search", "tool_suggest", "workspace_dependencies",
)
_ENABLED_FEATURES = ("skip_host_skill_discovery",)
_SCALAR_OVERRIDES = (
    'web_search="disabled"',
    "sandbox_workspace_write.network_access=false",
    "sandbox_workspace_write.writable_roots=[]",
    "hooks={}",
    "project_doc_max_bytes=0",
)


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class EffectiveConfig:
    """The exact argv fragment passed to every Codex inspection and run command."""

    model: None
    argv: tuple[str, ...]
    disabled_mcps: tuple[str, ...]
    override_fingerprint: str


def build_effective_config(*, disable_mcps: Sequence[str] = ()) -> EffectiveConfig:
    """Build deterministic Codex argv with no shell interpolation."""
    names = tuple(sorted(set(disable_mcps)))
    if any(not isinstance(name, str) or not _MCP_NAME.fullmatch(name) for name in names):
        raise ValueError("MCP name cannot be represented safely as a Codex config key")
    argv: list[str] = []
    for value in _SCALAR_OVERRIDES:
        argv.extend(("-c", value))
    for feature in _DISABLED_FEATURES:
        argv.extend(("--disable", feature))
    for feature in _ENABLED_FEATURES:
        argv.extend(("--enable", feature))
    for name in names:
        argv.extend(("-c", f"mcp_servers.{name}.enabled=false"))
    vector = tuple(argv)
    return EffectiveConfig(None, vector, names, _fingerprint({"model": None, "argv": vector}))


def verify_resolved_config(config: EffectiveConfig, *, features: Mapping[str, bool], mcps: Mapping[str, bool]) -> str:
    """Validate resolved reports and return the combined configuration identity."""
    expected_features = {
        **{name: False for name in _DISABLED_FEATURES},
        **{name: True for name in _ENABLED_FEATURES},
    }
    if set(features) != set(expected_features):
        raise ValueError("resolved feature schema or population changed")
    for name, expected in expected_features.items():
        if features.get(name) is not expected:
            raise ValueError(f"feature {name} is enabled or unresolved")
    if set(mcps) != set(config.disabled_mcps):
        raise ValueError("resolved MCP population differs from enumerated population")
    if any(value is not False for value in mcps.values()):
        raise ValueError("an MCP server is enabled or unresolved")
    resolved = {
        "override_fingerprint": config.override_fingerprint,
        "features": dict(sorted(expected_features.items())),
        "mcps": dict(sorted(mcps.items())),
    }
    return _fingerprint(resolved)


@dataclass(frozen=True)
class CandidateStat:
    relative_path: str
    device: int
    inode: int
    size: int
    birth_ns: int | None
    ctime_ns: int
    mtime_ns: int


def stat_inventory(root: Path) -> tuple[CandidateStat, ...]:
    """Recursively inventory rollout candidates without opening or hashing files."""
    root = Path(root)
    found: list[CandidateStat] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False) and entry.name.startswith("rollout-") and entry.name.endswith(".jsonl"):
                    birth = getattr(info, "st_birthtime", None)
                    found.append(CandidateStat(
                        path.relative_to(root).as_posix(), info.st_dev, info.st_ino,
                        info.st_size, int(birth * 1_000_000_000) if birth is not None else None,
                        info.st_ctime_ns, info.st_mtime_ns,
                    ))

    visit(root)
    return tuple(sorted(found, key=lambda item: item.relative_path))


def identify_single_new_candidate(before: Sequence[CandidateStat], after: Sequence[CandidateStat], *, attempt_started_ns: int | None = None) -> CandidateStat:
    """Require one path and filesystem identity unseen before the attempt."""
    old_paths = {item.relative_path for item in before}
    old_identities = {(item.device, item.inode) for item in before}
    candidates = [item for item in after
                  if item.relative_path not in old_paths
                  and (item.device, item.inode) not in old_identities
                  and (attempt_started_ns is None or item.birth_ns is None or item.birth_ns >= attempt_started_ns)]
    if len(candidates) != 1:
        raise ValueError(f"expected exactly one new rollout candidate; found {len(candidates)}")
    return candidates[0]


def verify_candidate_identity(root: Path, candidate: CandidateStat) -> None:
    """Re-stat a quiesced candidate immediately before any open or copy."""
    path = Path(root) / candidate.relative_path
    info = path.stat(follow_symlinks=False)
    if not path.is_file() or path.is_symlink():
        raise ValueError("rollout candidate is no longer a regular file")
    birth = getattr(info, "st_birthtime", None)
    birth_ns = int(birth * 1_000_000_000) if birth is not None else None
    current = (info.st_dev, info.st_ino, info.st_size, birth_ns, info.st_ctime_ns, info.st_mtime_ns)
    expected = (candidate.device, candidate.inode, candidate.size, candidate.birth_ns, candidate.ctime_ns, candidate.mtime_ns)
    if current != expected:
        raise ValueError("rollout candidate identity changed after quiescence")


def candidate_stat(root: Path, relative_path: str) -> CandidateStat:
    """Re-stat one already selected path without reading its contents."""
    path = Path(root) / relative_path
    info = path.stat(follow_symlinks=False)
    if path.is_symlink() or not path.is_file() or not path.name.startswith("rollout-") or not path.name.endswith(".jsonl"):
        raise ValueError("selected rollout is no longer an ordinary candidate")
    birth = getattr(info, "st_birthtime", None)
    return CandidateStat(relative_path, info.st_dev, info.st_ino, info.st_size,
                         int(birth * 1_000_000_000) if birth is not None else None,
                         info.st_ctime_ns, info.st_mtime_ns)


def wait_for_candidate_quiescence(root: Path, candidate: CandidateStat, *,
                                  sleep: Callable[[float], None] = time.sleep,
                                  stable_interval_seconds: float = 1.0,
                                  stable_checks: int = 2) -> CandidateStat:
    """Require repeated unchanged metadata, then return the stat used for copying."""
    if stable_checks < 2 or stable_interval_seconds < 0:
        raise ValueError("invalid quiescence policy")
    current = candidate
    for _ in range(stable_checks):
        sleep(stable_interval_seconds)
        observed = candidate_stat(root, candidate.relative_path)
        if observed != current:
            raise ValueError("rollout candidate changed during quiescence")
        current = observed
    verify_candidate_identity(root, current)
    return current


@dataclass(frozen=True)
class QuotaSnapshot:
    source: str | None
    observed_at: str | None
    used_percent: float | None
    baseline_used_percent: float | None
    monotonic_started: float | None
    monotonic_observed: float | None


def quota_decision(snapshot: QuotaSnapshot, *, plan: Mapping[str, object], now_monotonic: float, live_attempt_started: bool = False) -> str:
    """Return proceed, finish-current-only, or a pre-submission stop reason."""
    required = (snapshot.source, snapshot.observed_at, snapshot.used_percent, snapshot.baseline_used_percent, snapshot.monotonic_started, snapshot.monotonic_observed)
    if any(value is None for value in required):
        return "finish-current-only" if live_attempt_started else "stop-quota-unreadable"
    assert snapshot.used_percent is not None
    assert snapshot.baseline_used_percent is not None
    assert snapshot.monotonic_started is not None
    assert snapshot.monotonic_observed is not None
    from .live_plan import validate_live_plan
    validate_live_plan(plan)
    limits = plan["limits"]
    contract = limits["quota"]
    if snapshot.source != contract.get("source") or snapshot.baseline_used_percent != contract.get("baseline_used_percent"):
        return "stop-quota-contract-mismatch"
    threshold = contract.get("absolute_stop_used_percent")
    if not isinstance(threshold, (int, float)):
        return "stop-quota-contract-mismatch"
    try:
        observed = datetime.fromisoformat(snapshot.observed_at.replace("Z", "+00:00"))
        baseline_at = datetime.fromisoformat(str(contract["observed_at"]).replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError):
        return "stop-quota-contract-mismatch"
    if observed < baseline_at or snapshot.used_percent < snapshot.baseline_used_percent:
        return "stop-quota-contract-mismatch"
    if snapshot.used_percent >= threshold:
        return "stop-quota-threshold"
    if now_monotonic - snapshot.monotonic_started > limits["wall_clock_minutes"] * 60:
        return "stop-wall-clock"
    if not snapshot.monotonic_started <= snapshot.monotonic_observed <= now_monotonic:
        return "stop-invalid-time-anchor"
    if now_monotonic - snapshot.monotonic_observed > 60:
        return "stop-quota-stale"
    return "proceed"
