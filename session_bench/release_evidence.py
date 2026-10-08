"""Opt-in evidence contract for the expanded 14-row public-v1 release.

The original and prospective five-configuration contracts remain unchanged.
Changing a schema label is insufficient to make historical evidence rankable:
the strict metric bindings and captured identities remain. This validator checks
shape and bindings only; it cannot establish independent native-to-score replay.
"""

from __future__ import annotations

from typing import Any, Mapping

from .release_scope import CONFIGURATION_IDS
from .survival_evidence import _validate_evidence_input


RELEASE_EVIDENCE_SCHEMA_VERSION = "session-bench-survival-evidence-release-v1"
INDEPENDENT_NATIVE_SCORE_REPLAY_VERIFIED = False


def validate_release_evidence_input(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate bindings for diagnostics; input claims cannot prove independent replay."""
    validated = _validate_evidence_input(
        document,
        expected_schema_version=RELEASE_EVIDENCE_SCHEMA_VERSION,
        allowed_configurations=frozenset(CONFIGURATION_IDS),
    )
    # Internal-only state. It is deliberately not accepted from serialized input.
    validated["independent_native_score_replay_verified"] = INDEPENDENT_NATIVE_SCORE_REPLAY_VERIFIED
    return validated
