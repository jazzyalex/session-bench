"""Fail-closed planning for the Codex Desktop survival-v1 surface.

Codex Desktop is a distinct benchmark configuration.  The supported app task API is
the observer channel.  Native capture may use an isolated profile or the normal root,
provided normal-root discovery is metadata-only and no pre-existing session content is
opened.  Complete-root and canonical-copy proof remain controller responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class CodexDesktopIdentity:
    app_path: str
    bundle_identifier: str
    version: str
    build: str
    bundled_cli_version: str


@dataclass(frozen=True)
class DesktopObserverContract:
    method: str
    independent_of_native_root: bool
    records_response_boundaries: bool
    records_visible_text: bool

    @property
    def qualified(self) -> bool:
        return (
            self.independent_of_native_root
            and self.records_response_boundaries
            and self.records_visible_text
        )


@dataclass(frozen=True)
class DesktopRootContract:
    roots: tuple[str, ...]
    isolated_from_personal_profile: bool
    complete_root_proven: bool
    newly_created_session_proven: bool
    metadata_only_before_after: bool = False
    preexisting_content_opened: bool = False

    @property
    def safe_capture_boundary(self) -> bool:
        return self.isolated_from_personal_profile or (
            self.metadata_only_before_after and not self.preexisting_content_opened
        )

    @property
    def qualified(self) -> bool:
        return (
            bool(self.roots)
            and self.safe_capture_boundary
            and self.complete_root_proven
            and self.newly_created_session_proven
        )


@dataclass(frozen=True)
class CodexDesktopPreflight:
    identity: CodexDesktopIdentity
    observer: DesktopObserverContract
    native: DesktopRootContract
    launch_isolated: bool
    reason_ids: tuple[str, ...]

    @property
    def calibration_ready(self) -> bool:
        return (
            self.observer.qualified
            and bool(self.native.roots)
            and self.native.newly_created_session_proven
        )

    @property
    def evaluation_ready(self) -> bool:
        # A normal authenticated Desktop root is admissible when the native
        # contract proves metadata-only before/after selection of a new session.
        return self.calibration_ready and self.native.qualified

    def require_calibration_ready(self) -> None:
        if not self.calibration_ready:
            reasons = ", ".join(self.reason_ids) or "desktop.contract_unresolved"
            raise RuntimeError(f"Codex Desktop calibration is blocked: {reasons}")

    def require_evaluation_ready(self) -> None:
        if not self.evaluation_ready:
            reasons = ", ".join(self.reason_ids) or "desktop.portability_contract_unresolved"
            raise RuntimeError(f"Codex Desktop evaluated collection is blocked: {reasons}")


def blocked_current_preflight(identity: CodexDesktopIdentity) -> CodexDesktopPreflight:
    """Return the retained 2026-09-11 historical boundary."""

    return CodexDesktopPreflight(
        identity=identity,
        observer=DesktopObserverContract(
            method="computer-use denied by host safety boundary",
            independent_of_native_root=True,
            records_response_boundaries=False,
            records_visible_text=False,
        ),
        native=DesktopRootContract(
            roots=(),
            isolated_from_personal_profile=False,
            complete_root_proven=False,
            newly_created_session_proven=False,
        ),
        launch_isolated=False,
        reason_ids=(
            "codex_desktop.gui_observer_denied",
            "codex_desktop.isolated_launch_unproven",
            "codex_desktop.complete_root_unproven",
        ),
    )


def task_api_normal_root_preflight(
    identity: CodexDesktopIdentity,
    *,
    roots: tuple[str, ...],
    complete_root_proven: bool,
    newly_created_session_proven: bool,
    preexisting_content_opened: bool = False,
) -> CodexDesktopPreflight:
    """Describe the supported task-API plus metadata-only normal-root route."""

    observer = DesktopObserverContract(
        method="Codex app task API",
        independent_of_native_root=True,
        records_response_boundaries=True,
        records_visible_text=True,
    )
    native = DesktopRootContract(
        roots=roots,
        isolated_from_personal_profile=False,
        complete_root_proven=complete_root_proven,
        newly_created_session_proven=newly_created_session_proven,
        metadata_only_before_after=True,
        preexisting_content_opened=preexisting_content_opened,
    )
    reasons = []
    if not roots:
        reasons.append("codex_desktop.native_roots_missing")
    if not complete_root_proven:
        reasons.append("codex_desktop.complete_root_unproven")
    if not newly_created_session_proven:
        reasons.append("codex_desktop.new_session_unproven")
    if preexisting_content_opened:
        reasons.append("codex_desktop.preexisting_content_opened")
    return CodexDesktopPreflight(identity, observer, native, False, tuple(reasons))


def proposed_environment(run_root: Path) -> Mapping[str, str]:
    """Describe a prospective isolated runtime root; this does not prove app isolation."""

    root = Path(run_root).resolve()
    return {"CODEX_HOME": str(root / "codex-home")}


def calibration_command(_: CodexDesktopPreflight) -> tuple[str, ...]:
    """The app task API is controller-owned and has no local command vector."""

    raise RuntimeError(
        "Codex Desktop has no local calibration command; use the supported app task API "
        "with a separately verified observer and native capture"
    )
