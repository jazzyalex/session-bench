"""Surface-specific acquisition adapters.

The shared manifest and capture contract lives in :mod:`session_bench.surface_capture`;
adapters only supply bounded launch and native-root rules.
"""

from .codex_cli import (
    AuthPrerequisite,
    AuthenticationPrerequisite,
    AuthenticationRequired,
    CodexCLI,
    CodexCLIAdapter,
    CodexCLIAuth,
    CodexCLIError,
    CodexCLIIdentity,
    CodexCLILaunch,
    CodexCLIRunRoots,
    RunnerResult,
    build_codex_argv,
    build_codex_launch,
)

__all__ = [
    "AuthPrerequisite",
    "AuthenticationPrerequisite",
    "AuthenticationRequired",
    "CodexCLI",
    "CodexCLIAdapter",
    "CodexCLIAuth",
    "CodexCLIError",
    "CodexCLIIdentity",
    "CodexCLILaunch",
    "CodexCLIRunRoots",
    "RunnerResult",
    "build_codex_argv",
    "build_codex_launch",
]
