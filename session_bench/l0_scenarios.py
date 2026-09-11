"""Frozen C01/C02 observer inputs for L0 construction tests."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


FROZEN_POSITIVE_CONTROL_ORDER = (
    "C02.failing_test_status_and_exit_code",
    "C02.inspect_target_and_source_bytes",
    "C02.accepted_prompt_bytes",
    "C01.correction_prompt_bytes",
    "C01.first_prompt_marker_bytes",
)


@dataclass(frozen=True)
class ObserverInput:
    scenario: str
    observation_id: str
    sequence: int
    kind: str
    value: str


@dataclass(frozen=True)
class PositiveControl:
    assertion_id: str
    native_locations: tuple[str, ...]
    selection_evidence_sha256: str | None = None
    selection_evidence_json: str | None = None


def select_positive_control(candidates: Sequence[Mapping[str, Any]]) -> PositiveControl:
    """Choose the first frozen candidate with all three required evidence facts."""
    by_id = {item.get("assertion_id", item.get("id")): item for item in candidates}
    for assertion_id in FROZEN_POSITIVE_CONTROL_ORDER:
        item = by_id.get(assertion_id)
        if not isinstance(item, Mapping):
            continue
        if (item.get("independently_observed") is True
                and item.get("native_present") is True
                and item.get("correctly_reconstructed") is True):
            locations = item.get("native_locations")
            if not isinstance(locations, (list, tuple)) or not locations:
                continue
            evidence_json = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            return PositiveControl(assertion_id, tuple(str(location) for location in locations),
                                   hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(), evidence_json)
    raise ValueError("no positive control is independently observed, native-present, and reconstructed")


def _sha256(value: bytes | Path) -> str:
    data = Path(value).read_bytes() if isinstance(value, Path) else value
    return hashlib.sha256(data).hexdigest()


def make_damage_receipt(*, selected_assertion: PositiveControl,
                        changed_native_locations: Sequence[str],
                        source_decode_manifest: bytes | Path,
                        derived_decode_manifest: bytes | Path,
                        transformation: str) -> dict[str, Any]:
    """Bind a derived damage operation to the selected intact positive fact."""
    locations = tuple(str(location) for location in changed_native_locations)
    if not locations or tuple(locations) != selected_assertion.native_locations:
        raise ValueError("damage locations must exactly match selected native locations")
    if not transformation:
        raise ValueError("damage transformation is required")
    if selected_assertion.selection_evidence_sha256 is None or selected_assertion.selection_evidence_json is None:
        raise ValueError("damage receipt requires frozen selection evidence")
    if hashlib.sha256(selected_assertion.selection_evidence_json.encode("utf-8")).hexdigest() != selected_assertion.selection_evidence_sha256:
        raise ValueError("damage selection evidence digest is invalid")
    return {
        "selected_assertion": selected_assertion.assertion_id,
        "selected_native_locations": list(selected_assertion.native_locations),
        "changed_native_locations": list(locations),
        "source_decode_manifest_sha256": _sha256(source_decode_manifest),
        "derived_decode_manifest_sha256": _sha256(derived_decode_manifest),
        "selection_evidence_sha256": selected_assertion.selection_evidence_sha256,
        "selection_evidence": (json.loads(selected_assertion.selection_evidence_json)
                               if selected_assertion.selection_evidence_json is not None else None),
        "transformation": transformation,
    }


def _tree_sha256(root: Path) -> str:
    hasher = hashlib.sha256()
    for path in sorted(item for item in Path(root).rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        hasher.update(len(relative).to_bytes(8, "big"))
        hasher.update(relative)
        hasher.update(len(data).to_bytes(8, "big"))
        hasher.update(data)
    return hasher.hexdigest()


def verify_damage_control(*, source_bundle: Path, derived_bundle: Path,
                          selected_assertion: PositiveControl,
                          changed_native_locations: Sequence[str],
                          source_result: Mapping[str, Any],
                          derived_result: Mapping[str, Any],
                          transformation: str) -> dict[str, Any]:
    """Prove intact-pass to damaged-loss under byte-identical answer keys."""
    source_bundle, derived_bundle = Path(source_bundle), Path(derived_bundle)
    from .bundle import validate_bundle
    from .result_contract import validate_result_semantics
    validate_bundle(source_bundle)
    validate_bundle(derived_bundle)
    validate_result_semantics(source_result)
    validate_result_semantics(derived_result)
    if selected_assertion.selection_evidence_sha256 is None or selected_assertion.selection_evidence_json is None:
        raise ValueError("positive control is not bound to frozen selection evidence")
    if hashlib.sha256(selected_assertion.selection_evidence_json.encode("utf-8")).hexdigest() != selected_assertion.selection_evidence_sha256:
        raise ValueError("positive-control selection evidence digest is invalid")
    for relative in ("observer/events.json", "expected/assertions.json"):
        if (source_bundle / relative).read_bytes() != (derived_bundle / relative).read_bytes():
            raise ValueError(f"damage control changed frozen {relative}")
    source_rows = {row["id"]: row for row in source_result["rows"]}
    derived_rows = {row["id"]: row for row in derived_result["rows"]}
    assertion_id = selected_assertion.assertion_id
    if source_rows.get(assertion_id, {}).get("state") != "pass":
        raise ValueError("selected positive control did not pass intact evaluation")
    if derived_rows.get(assertion_id, {}).get("state") == "pass":
        raise ValueError("damaged evaluation still reconstructs selected control")
    locations = tuple(str(item) for item in changed_native_locations)
    if locations != selected_assertion.native_locations:
        raise ValueError("damage locations differ from selected control")
    normalized_locations = set()
    for location in locations:
        path_text, separator, line_text = location.rpartition(":line-")
        if not separator or not line_text.isdigit():
            raise ValueError("damage location is not a file line locator")
        relative = path_text.split(":", 1)[-1]
        if not relative.startswith("native/"):
            relative = "native/" + relative
        normalized_locations.add(f"{relative}:line-{line_text}")
        line_number = int(line_text)
        source_lines = (source_bundle / relative).read_bytes().splitlines(keepends=True)
        derived_lines = (derived_bundle / relative).read_bytes().splitlines(keepends=True)
        source_line = source_lines[line_number - 1] if line_number <= len(source_lines) else None
        derived_line = derived_lines[line_number - 1] if line_number <= len(derived_lines) else None
        if source_line == derived_line:
            raise ValueError("declared native damage location did not change")
    source_files = {path.relative_to(source_bundle).as_posix(): path for path in (source_bundle / "native").rglob("*")
                    if path.is_file() and path.name != "decode.json"}
    derived_files = {path.relative_to(derived_bundle).as_posix(): path for path in (derived_bundle / "native").rglob("*")
                     if path.is_file() and path.name != "decode.json"}
    if set(source_files) != set(derived_files):
        raise ValueError("damage changed the native artifact population")
    actual_changed = set()
    for relative, source_path in source_files.items():
        source_lines = source_path.read_bytes().splitlines(keepends=True)
        remaining = Counter(derived_files[relative].read_bytes().splitlines(keepends=True))
        for line_number, raw in enumerate(source_lines, 1):
            if remaining[raw]:
                remaining[raw] -= 1
            else:
                actual_changed.add(f"{relative}:line-{line_number}")
        if sum(remaining.values()) > len(actual_changed):
            raise ValueError("damage added unexplained native records")
    if actual_changed != normalized_locations:
        raise ValueError("damage receipt does not exhaustively list changed native locations")
    return {
        "selected_assertion": assertion_id,
        "selected_native_locations": list(locations),
        "changed_native_locations": list(locations),
        "source_manifest_sha256": _sha256(source_bundle / "manifest.json"),
        "derived_manifest_sha256": _sha256(derived_bundle / "manifest.json"),
        "source_bundle_sha256": _tree_sha256(source_bundle),
        "derived_bundle_sha256": _tree_sha256(derived_bundle),
        "observer_sha256": _sha256(source_bundle / "observer/events.json"),
        "expectations_sha256": _sha256(source_bundle / "expected/assertions.json"),
        "selection_evidence_sha256": selected_assertion.selection_evidence_sha256,
        "source_evaluation_id": source_result["evaluation_id"],
        "derived_evaluation_id": derived_result["evaluation_id"],
        "source_state": "pass",
        "derived_state": derived_rows.get(assertion_id, {}).get("state", "missing"),
        "transformation": transformation,
    }


def c01_inputs(run_id: str) -> tuple[ObserverInput, ...]:
    marker = f"SB_F0_{run_id}_C01_café_🙂"
    prompts = (
        f"Remember this marker exactly: {marker}. Reply with the marker on its own line, then say READY.",
        "Correction: preserve the marker's accents and emoji exactly; reply with the marker on its own line, then say CORRECTED.",
        "Now repeat the marker exactly once and say DONE.",
    )
    return tuple(ObserverInput("C01", f"c01-submit-{i}", i, "accepted_prompt", prompt)
                 for i, prompt in enumerate(prompts, 1))


def c02_inputs() -> tuple[ObserverInput, ...]:
    values = (
        ("c02-inspect", "inspect", "fixture_project/target.py"),
        ("c02-test-fail", "test", "python3 fixture_project/test_target.py"),
        ("c02-edit", "edit", "fixture_project/target.py"),
        ("c02-test-pass", "test", "python3 fixture_project/test_target.py"),
    )
    return tuple(ObserverInput("C02", ident, seq, kind, value)
                 for seq, (ident, kind, value) in enumerate(values, 1))
