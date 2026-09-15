"""Native decoder for the Codex CLI 0.154 survival-v1 rollout format.

The current Codex CLI rollout is a JSONL stream whose semantic records are
split across ``response_item``, ``event_msg``, ``turn_context``, and
``token_usage_record`` envelopes.  This module decodes one *declared copied
bundle* only.  It does not discover a Codex home, open an observer/project
root, run Codex, read the answer key, or ask a model to fill gaps.

The decoder returns normalized records, explicit facts, and the existing
19-row survival-v1 measurement shape. A missing native field is represented
by a field state of ``"missing"``. Population metrics remain measurable when
the native record family is present, while the missing field reduces their
correct fraction; only a metric whose acquisition or decoder contract is
unproven is ``decoder_unsupported`` or ``unresolved``. Neither state is
silently converted to zero or to a value from the observer.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
import shlex
from typing import Any, Iterable, Mapping, Sequence

from ..survival_metrics import METRICS, SCHEMA_VERSION


DECODER_ID = "session-bench.codex-cli-native"
DECODER_VERSION = "0.154-survival-v1"
NATIVE_FORMAT = "codex-rollout-v1"

MEASURED = "measured"
NATIVE_ABSENT = "native_absent"
CONTRADICTION = "contradiction"
UNRESOLVED = "unresolved"
DECODER_UNSUPPORTED = "decoder_unsupported"
INVALID_CAPTURE = "invalid_capture"

_MISSING = object()
_ARTIFACT_KEYS = {"id", "path", "sha256", "size_bytes", "depends_on"}
_MAX_ARTIFACTS = 256
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_RECORDS = 100_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HELPER = re.compile(r"SB_SURVIVAL_V1_HELPER_(?P<phase>[A-Z]+)_(?P<nonce>[A-Za-z0-9_-]+)")
_RUN_CANARY_PREFIX = "SB_SURVIVAL_V1_RUN_"
_RUN_CANARY_RE = re.compile(r"^SB_SURVIVAL_V1_RUN_[A-Za-z0-9_-]+$")


def _expected_run_canary(workload: FrozenWorkload) -> str | None:
    candidate = getattr(workload, "run_canary", None)
    if isinstance(candidate, str) and _RUN_CANARY_RE.fullmatch(candidate):
        return candidate
    run_id = getattr(workload, "run_id", None)
    if isinstance(run_id, str) and run_id:
        derived = run_id if _RUN_CANARY_RE.fullmatch(run_id) else _RUN_CANARY_PREFIX + run_id
        if _RUN_CANARY_RE.fullmatch(derived):
            return derived
    return None


def _strip_optional_run_canary(command_part: str, expected_canary: str | None) -> str | None:
    """Remove one exact run-canary flag without weakening canonical matching."""

    if not isinstance(command_part, str) or not command_part.strip():
        return None
    try:
        tokens = shlex.split(command_part)
    except (ValueError, AttributeError):
        return None
    if any(token.startswith("--run-canary=") for token in tokens):
        return None
    flag_count = sum(token == "--run-canary" for token in tokens)
    if flag_count == 0:
        return command_part.strip()
    if flag_count != 1 or expected_canary is None or not _RUN_CANARY_RE.fullmatch(expected_canary):
        return None
    flag = tokens.index("--run-canary")
    if flag + 1 >= len(tokens) or tokens[flag + 1] != expected_canary:
        return None
    stripped = tokens[:flag] + tokens[flag + 2:]
    if not stripped or any(token == "--run-canary" or token.startswith("--run-canary=") for token in stripped):
        return None
    return shlex.join(stripped)


# Codex serializes apply_patch bodies inside a JavaScript string, so the
# separator after a path may still be the two characters ``\\n`` rather than
# an actual newline.  Stop at either representation and at the closing quote.
_UPDATE_FILE = re.compile(r"(?:\*\*\*\s+)?Update File:\s*(?P<path>[^\r\n\\\"]+)")
_CMD_LITERAL = re.compile(r"(?:^|\b)cmd:\s*\"(?P<cmd>(?:\\.|[^\"])*)\"")
_WORKDIR_LITERAL = re.compile(r"(?:^|\b)workdir:\s*\"(?P<cwd>(?:\\.|[^\"])*)\"")
_JSON_CMD_LITERAL = re.compile(r"\"cmd\"\s*:\s*\"(?P<cmd>(?:\\.|[^\"])*)\"")
_JSON_WORKDIR_LITERAL = re.compile(r"\"workdir\"\s*:\s*\"(?P<cwd>(?:\\.|[^\"])*)\"")
_CODEX_DELEGATION_INPUT = re.compile(
    r"\A\s*<codex_delegation>\s*<source_thread_id>(?P<source>[^<]+)</source_thread_id>\s*"
    r"<input>(?P<input>.*?)</input>\s*</codex_delegation>\s*\Z",
    re.DOTALL,
)


class CodexCLIDecodeError(ValueError):
    """The copied native package is not a valid declared Codex bundle."""


@dataclass(frozen=True)
class ExpectedAction:
    """One action population member from the frozen survival-v1 workload."""

    id: str
    turn_id: str
    kind: str
    command: str | None
    target: str
    helper_nonce: str | None


@dataclass(frozen=True)
class FrozenWorkload:
    """Input-side workload facts allowed to be used for native matching.

    These are submitted-input expectations, not observer answers.  They let
    the decoder distinguish R1/R2 and the four required action identities while
    keeping every value derived from the native bundle separately locatable.
    """

    run_id: str
    turns: tuple[tuple[str, str], ...]
    response_canaries: tuple[tuple[str, str], ...]
    actions: tuple[ExpectedAction, ...]
    run_canary: str | None = None

    @property
    def turn_text(self) -> dict[str, str]:
        return dict(self.turns)

    @property
    def canary_by_turn(self) -> dict[str, str]:
        return dict(self.response_canaries)

    @property
    def action_by_id(self) -> dict[str, ExpectedAction]:
        return {item.id: item for item in self.actions}


FROZEN_SURVIVAL_V1 = FrozenWorkload(
    run_id="survival-v1-fixture-0001",
    run_canary="SB_SURVIVAL_V1_RUN_fixture_0001",
    turns=(
        (
            "turn-r1",
            "This is a synthetic Session-Bench survival-v1 coding task. Work only inside fixture_project. Do not access personal history, credentials, network, other projects, plugins, or agents. Preserve bench_check.py and .survival-observer.jsonl. Requirement R1: checkout(items) must total each unit_price * quantity and add delivery of 5. First run python3 bench_check.py inspect, then python3 bench_check.py baseline. Do not edit checkout.py yet. Explain the observed baseline result and stop. End your visible response with exactly SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂. Context marker: SB_SURVIVAL_V1_CONTEXT_cafe_🙂. Run canary: SB_SURVIVAL_V1_RUN_fixture_0001.",
        ),
        (
            "turn-r2",
            "Correction R2 supersedes only the delivery condition: delivery is free when subtotal is at least 50 and remains 5 below 50. Keep quantity handling from R1. Edit only fixture_project/checkout.py. Do not modify bench_check.py or .survival-observer.jsonl. Run python3 bench_check.py final. Explain the actual change and test outcome. End your visible response with exactly SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ. Context marker: SB_SURVIVAL_V1_CONTEXT_cafe_🙂. Run canary: SB_SURVIVAL_V1_RUN_fixture_0001.",
        ),
    ),
    response_canaries=(
        ("turn-r1", "SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂"),
        ("turn-r2", "SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ"),
    ),
    actions=(
        ExpectedAction(
            "action-inspect", "turn-r1", "inspect", "python3 bench_check.py inspect",
            "fixture_project/checkout.py", "inspect-fixture-0001",
        ),
        ExpectedAction(
            "action-baseline", "turn-r1", "test", "python3 bench_check.py baseline",
            "fixture_project/checkout.py", "baseline-fixture-0001",
        ),
        ExpectedAction(
            "action-edit", "turn-r2", "edit", None,
            "fixture_project/checkout.py", None,
        ),
        ExpectedAction(
            "action-final", "turn-r2", "test", "python3 bench_check.py final",
            "fixture_project/checkout.py", "final-fixture-0001",
        ),
    ),
)


@dataclass(frozen=True)
class _Artifact:
    id: str
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int
    depends_on: tuple[str, ...]


def _strict_json(value: bytes | str, source: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise CodexCLIDecodeError(f"{source}: duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(token: str) -> Any:
        raise CodexCLIDecodeError(f"{source}: non-finite JSON number {token}")

    try:
        return json.loads(value, object_pairs_hook=reject_duplicate, parse_constant=reject_constant)
    except CodexCLIDecodeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexCLIDecodeError(f"{source}: invalid JSON: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CodexCLIDecodeError("artifact path must be a non-empty relative POSIX path")
    path = Path(value)
    if (
        path.is_absolute()
        or path == Path(".")
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        raise CodexCLIDecodeError("artifact path escapes copied package")
    return value


def _reject_symlinks(root: Path, relative: str) -> None:
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise CodexCLIDecodeError(f"symlink is not allowed in copied package: {relative}")


def _validate_package(package: Path) -> tuple[dict[str, Any], tuple[_Artifact, ...]]:
    package = Path(package)
    if not package.is_dir() or package.is_symlink():
        raise CodexCLIDecodeError("copied native package must be a real directory")
    package = package.resolve()
    manifest_path = package / "decode.json"
    _reject_symlinks(package, "decode.json")
    if not manifest_path.is_file():
        raise CodexCLIDecodeError("copied native package is missing decode.json")
    manifest = _strict_json(manifest_path.read_bytes(), "decode.json")
    if not isinstance(manifest, dict) or set(manifest) != {"format", "artifacts"}:
        raise CodexCLIDecodeError("decode.json must contain exactly format and artifacts")
    if manifest["format"] != NATIVE_FORMAT:
        raise CodexCLIDecodeError(f"unsupported Codex native format: {manifest.get('format')!r}")
    raw_artifacts = manifest["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise CodexCLIDecodeError("decode.json artifacts must be a non-empty array")
    if len(raw_artifacts) > _MAX_ARTIFACTS:
        raise CodexCLIDecodeError(f"copied package exceeds {_MAX_ARTIFACTS} artifacts")

    artifacts: list[_Artifact] = []
    by_id: dict[str, _Artifact] = {}
    for raw in raw_artifacts:
        if not isinstance(raw, dict) or set(raw) != _ARTIFACT_KEYS:
            raise CodexCLIDecodeError("artifact declaration has unexpected fields")
        artifact_id = raw["id"]
        if not isinstance(artifact_id, str) or not artifact_id or artifact_id in by_id:
            raise CodexCLIDecodeError("artifact ids must be unique non-empty strings")
        relative = _safe_relative(raw["path"])
        digest = raw["sha256"]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise CodexCLIDecodeError(f"invalid SHA-256 for artifact {artifact_id}")
        size = raw["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > _MAX_ARTIFACT_BYTES:
            raise CodexCLIDecodeError(f"invalid size for artifact {artifact_id}")
        depends_on = raw["depends_on"]
        if not isinstance(depends_on, list) or any(not isinstance(item, str) for item in depends_on):
            raise CodexCLIDecodeError(f"invalid dependencies for artifact {artifact_id}")
        if relative == "decode.json":
            raise CodexCLIDecodeError("decode.json cannot be a declared native artifact")
        _reject_symlinks(package, relative)
        artifact = _Artifact(artifact_id, package / relative, relative, digest, size, tuple(depends_on))
        by_id[artifact_id] = artifact
        artifacts.append(artifact)

    for artifact in artifacts:
        if any(item not in by_id for item in artifact.depends_on):
            raise CodexCLIDecodeError(f"artifact {artifact.id} has an undeclared dependency")
        if not artifact.path.is_file():
            raise CodexCLIDecodeError(f"declared artifact is missing: {artifact.relative_path}")
        if artifact.path.stat().st_size != artifact.size_bytes or _sha256(artifact.path) != artifact.sha256:
            raise CodexCLIDecodeError(f"artifact digest or size mismatch: {artifact.id}")

    declared = {"decode.json", *(artifact.relative_path for artifact in artifacts)}
    for candidate in package.rglob("*"):
        relative = candidate.relative_to(package).as_posix()
        _reject_symlinks(package, relative)
        if candidate.is_file() and relative not in declared:
            raise CodexCLIDecodeError(f"undeclared copied-package file: {relative}")

    if not any(artifact.path.suffix.lower() == ".jsonl" for artifact in artifacts):
        raise CodexCLIDecodeError("Codex package has no declared JSONL rollout")
    return manifest, tuple(artifacts)


def _diagnostic(code: str, detail: str, locator: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "detail": detail}
    if locator is not None:
        result["locator"] = dict(locator)
    return result


def _locator(artifact: _Artifact, line: int, byte_start: int, byte_end: int, raw: bytes, ordinal: Any) -> dict[str, Any]:
    return {
        "artifact_id": artifact.id,
        "artifact_sha256": artifact.sha256,
        "record_location": f"{artifact.relative_path}:line-{line}",
        "line": line,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "record_sha256": hashlib.sha256(raw.rstrip(b"\r\n")).hexdigest(),
        "ordinal": ordinal,
    }


def _text_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts) if parts else None
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return None


def _canonical_submission_text(value: str) -> str:
    """Undo Codex Desktop's lossless Markdown escaping for input matching.

    The Desktop composer persists literal underscores in canaries as ``\\_``.
    This is a rendering serialization detail, not a changed submitted prompt.
    Normalize only Markdown punctuation escapes; do not otherwise transform the
    submitted text before comparing it to the frozen workload.
    """

    return re.sub(r"\\([_`*\\])", r"\1", value)


def _metadata_turn_id(payload: Mapping[str, Any]) -> str | None:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("turn_id"), str):
        return metadata["turn_id"]
    return None


def _field_state(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and value == 0:
        return "zero"
    return "value"


def _normalize_cwd(value: Any, *, workspace_root: str | None = None) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value:
        return None, "missing"
    text = value.removeprefix("file://")
    prefix = "$RUN_ROOT/project/"
    if text == "$RUN_ROOT/project":
        return ".", "value"
    if text.startswith(prefix):
        return text[len(prefix):] or ".", "value"
    # Codex Desktop task API runs directly in the project workspace rather
    # than the CLI controller's ``$RUN_ROOT/project`` child.
    direct_prefix = "$RUN_ROOT/"
    if text.startswith(direct_prefix):
        return text[len(direct_prefix):] or ".", "value"
    if isinstance(workspace_root, str) and workspace_root and text.startswith(workspace_root.rstrip("/") + "/"):
        return text[len(workspace_root.rstrip("/")) + 1:] or ".", "value"
    if isinstance(workspace_root, str) and workspace_root and text.rstrip("/") == workspace_root.rstrip("/"):
        # The live CLI writes the exact workspace root instead of a path
        # relative to the controller's run root.  Preserve the workload's
        # stable fixture label so an unrelated command recorded as ``.`` is
        # not accidentally counted as a workload action.
        return Path(workspace_root.rstrip("/")).name, "value"
    return text, "value"


def _decode_js_string(raw: str) -> str | None:
    try:
        value = json.loads('"' + raw + '"')
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, str) else None


def _tool_input(payload: Mapping[str, Any], *, workspace_root: str | None = None) -> dict[str, Any]:
    """Extract the bounded command/workdir/patch facts from Codex's input text."""

    raw = payload.get("input", _MISSING)
    result: dict[str, Any] = {
        # Keep the input itself out of the normalized public facts.  It can
        # contain arbitrary tool arguments; the parsed fields below are the
        # only facts needed by survival-v1 and are bounded to the workload.
        "input": None,
        "input_state": _field_state(raw),
        "command": None,
        "command_state": "missing",
        "workdir": None,
        "workdir_state": "missing",
        "patch_target": None,
        "patch_target_state": "missing",
    }
    if not isinstance(raw, str):
        return result
    command_match = _CMD_LITERAL.search(raw) or _JSON_CMD_LITERAL.search(raw)
    if command_match:
        command = _decode_js_string(command_match.group("cmd"))
        if command is not None:
            result["command"] = command
            result["command_state"] = "value"
    cwd_match = _WORKDIR_LITERAL.search(raw) or _JSON_WORKDIR_LITERAL.search(raw)
    if cwd_match:
        cwd = _decode_js_string(cwd_match.group("cwd"))
        if cwd is not None:
            result["workdir"], result["workdir_state"] = _normalize_cwd(cwd, workspace_root=workspace_root)
    patch_match = _UPDATE_FILE.search(raw)
    if patch_match:
        path = patch_match.group("path").strip().rstrip("\";")
        path = path.removeprefix("$RUN_ROOT/project/").removeprefix("$RUN_ROOT/")
        if isinstance(workspace_root, str) and workspace_root:
            path = path.removeprefix(workspace_root.rstrip("/") + "/")
        result["patch_target"] = path
        result["patch_target_state"] = "value"
    return result


def _command_line(payload: Mapping[str, Any]) -> tuple[str | None, str]:
    command = payload.get("command", _MISSING)
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        if len(command) >= 3 and command[-2] == "-lc":
            return command[-1], "value"
        try:
            return shlex.join(command), "value"
        except (TypeError, ValueError):
            return None, "invalid"
    if isinstance(command, str):
        return command, "value"
    return None, _field_state(command)


def _split_commands(command: str | None) -> tuple[str, ...]:
    if not command:
        return ()
    # The frozen workload commands are simple shell snippets.  Keep quoted
    # strings intact when possible; an unparseable compound command stays one
    # opaque command instead of being guessed into multiple actions.
    parts = tuple(part.strip() for part in re.split(r"\s*&&\s*", command) if part.strip())
    return parts if len(parts) > 1 else (command.strip(),)


def _native_path(value: str, *, workspace_root: str | None = None) -> str:
    text = value.replace("\\", "/")
    text = text.removeprefix("$RUN_ROOT/project/")
    text = text.removeprefix("file://$RUN_ROOT/project/")
    text = text.removeprefix("$RUN_ROOT/")
    text = text.removeprefix("file://$RUN_ROOT/")
    if isinstance(workspace_root, str) and workspace_root:
        root = workspace_root.rstrip("/")
        text = text.removeprefix(root + "/")
        text = text.removeprefix("file://" + root + "/")
    return text


def _helper_markers(*values: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        text = _text_content(value)
        if not text:
            continue
        for match in _HELPER.finditer(text):
            result[match.group("phase").lower()] = match.group("nonce")
    return result


def _record(kind: str, record_id: str | None, turn_id: str | None, timestamp: Any,
            fields: Mapping[str, Any], locator: Mapping[str, Any]) -> dict[str, Any]:
    def public(value: Any) -> Any:
        if value is _MISSING:
            return None
        if isinstance(value, Mapping):
            return {key: public(item) for key, item in value.items()}
        if isinstance(value, list):
            return [public(item) for item in value]
        if isinstance(value, tuple):
            return [public(item) for item in value]
        return value

    return {
        "kind": kind,
        "id": record_id,
        "turn_id": turn_id,
        "timestamp": timestamp,
        "fields": public(fields),
        "locator": public(locator),
    }


def _row(metric_id: str, state: str, correct: int = 0, observed: int = 0, decoded: int = 0,
         *, facts: Sequence[str] = (), reason: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": metric_id,
        "state": state,
        "correct": max(0, correct),
        "observed_eligible": max(0, observed),
        "decoded_eligible": max(0, decoded),
        "fact_ids": list(facts),
    }
    if reason:
        value["reason"] = reason
    return value


_POPULATIONS = {
    "work.submitted_turns": 2,
    "work.visible_responses": 2,
    "work.actions": 4,
    "work.results": 4,
    "work.changed_files": 1,
    "causal.action_result": 4,
    "causal.turn_response": 2,
    "revision.r1": 1,
    "revision.r2": 1,
    "revision.r1_r2_order": 1,
    "revision.final_after_r2": 1,
    "attribution.model_config": 2,
    "attribution.usage": 2,
    "attribution.token_semantics": 2,
    "attribution.reconciliation": 1,
    "portable.complete_root": 1,
    "portable.companions": 1,
    "portable.isolated_decode": 1,
    "portable.canonical_equality": 1,
}


def _terminal_state(*, present: bool, complete_root: bool, supported: bool = True,
                    contradiction: bool = False) -> str:
    if contradiction:
        return CONTRADICTION
    if not present:
        return NATIVE_ABSENT if complete_root else UNRESOLVED
    if not supported:
        return DECODER_UNSUPPORTED
    return MEASURED


def _usage_fields(usage: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "input_tokens", "output_tokens", "cached_input_tokens",
        "cache_write_input_tokens", "reasoning_output_tokens", "total_tokens",
    )
    result: dict[str, Any] = {}
    for key in keys:
        raw = usage.get(key, _MISSING)
        result[key] = None if raw is _MISSING else raw
        result[f"{key}_state"] = _field_state(raw)
    return result


def _sum_usage(rows: Iterable[Mapping[str, Any]]) -> dict[str, int] | None:
    keys = (
        "input_tokens", "output_tokens", "cached_input_tokens",
        "cache_write_input_tokens", "reasoning_output_tokens", "total_tokens",
    )
    totals = {key: 0 for key in keys}
    for row in rows:
        usage = row.get("usage")
        if not isinstance(usage, Mapping) or any(
            isinstance(usage.get(key, _MISSING), bool)
            or not isinstance(usage.get(key, _MISSING), int)
            for key in keys
        ):
            return None
        for key in keys:
            totals[key] += usage[key]
    return totals


def decode_codex_cli_bundle(
    package: str | Path,
    *,
    workload: FrozenWorkload = FROZEN_SURVIVAL_V1,
    complete_root: bool = False,
    required_companions: Sequence[str] | None = None,
    isolated_decode_proven: bool = False,
    canonical_equality_proven: bool = False,
    configuration_id: str = "codex-cli",
    repetition: int = 1,
) -> dict[str, Any]:
    """Decode one copied Codex JSONL package into survival-v1 native facts.

    ``package`` must itself contain ``decode.json`` and only the files declared
    there.  The optional portability arguments are decoder configuration: they
    are never inferred from a neighboring run directory.  In particular,
    ``complete_root`` must be supplied by a capture controller that has already
    proven its inventory boundary.
    """

    if configuration_id not in ("codex-cli", "codex-desktop"):
        raise CodexCLIDecodeError(f"unsupported configuration_id: {configuration_id!r}")
    if type(repetition) is not int or repetition not in (1, 2, 3):
        raise CodexCLIDecodeError("repetition must be 1, 2, or 3")
    manifest, artifacts = _validate_package(Path(package))
    diagnostics: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    submissions: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    usage_records: list[dict[str, Any]] = []
    task_starts: list[dict[str, Any]] = []
    task_completes: list[dict[str, Any]] = []
    identity: dict[str, Any] = {}
    session_id: str | None = None
    unknown_records = 0
    record_count = 0

    for artifact in artifacts:
        if artifact.path.suffix.lower() != ".jsonl":
            diagnostics.append(_diagnostic("ignored_artifact", f"non-JSONL artifact ignored: {artifact.relative_path}"))
            continue
        byte_start = 0
        with artifact.path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                byte_end = byte_start + len(raw)
                content = raw.rstrip(b"\r\n")
                byte_start = byte_end
                if not content.strip():
                    continue
                record_count += 1
                if record_count > _MAX_RECORDS:
                    raise CodexCLIDecodeError(f"rollout exceeds {_MAX_RECORDS} records")
                locator = _locator(artifact, line_number, byte_start - len(raw), byte_end, raw, None)
                try:
                    obj = _strict_json(content.decode("utf-8"), f"{artifact.relative_path}:line-{line_number}")
                except CodexCLIDecodeError as exc:
                    diagnostics.append(_diagnostic("malformed_record", str(exc), locator))
                    continue
                if not isinstance(obj, dict):
                    diagnostics.append(_diagnostic("malformed_record", "rollout envelope is not an object", locator))
                    continue
                locator["ordinal"] = obj.get("ordinal")
                outer_type = obj.get("type")
                payload = obj.get("payload")
                if not isinstance(outer_type, str) or not isinstance(payload, dict):
                    diagnostics.append(_diagnostic("unsupported_envelope", "missing string type or object payload", locator))
                    unknown_records += 1
                    continue
                timestamp = obj.get("timestamp")

                if outer_type == "session_meta":
                    candidate = payload.get("session_id") or payload.get("id")
                    if isinstance(candidate, str) and candidate:
                        if session_id is not None and session_id != candidate:
                            diagnostics.append(_diagnostic("session_id_conflict", "multiple session IDs in one copied rollout", locator))
                        session_id = candidate
                    for key in ("cli_version", "source", "originator", "model_provider", "cwd", "history_mode"):
                        if key in payload:
                            identity[key] = payload[key]
                    identity["session_id"] = session_id
                    records.append(_record("session", session_id, None, timestamp, {
                        key: payload[key] for key in ("cli_version", "source", "originator", "model_provider", "cwd", "history_mode") if key in payload
                    }, locator))
                    continue

                if outer_type == "turn_context":
                    turn_id = payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None
                    fields = {
                        key: payload[key] for key in ("model", "model_provider", "cwd", "approval_policy", "sandbox_policy") if key in payload
                    }
                    context = _record("turn_context", None, turn_id, timestamp, fields, locator)
                    contexts.append(context)
                    records.append(context)
                    continue

                if outer_type == "response_item":
                    item_type = payload.get("type")
                    turn_id = _metadata_turn_id(payload)
                    record_id = payload.get("id") if isinstance(payload.get("id"), str) else None
                    if item_type == "message":
                        role = payload.get("role") if isinstance(payload.get("role"), str) else None
                        text = _text_content(payload.get("content", _MISSING))
                        fields = {
                            "role": role,
                            "phase": payload.get("phase", _MISSING),
                            "text": text,
                            "text_state": _field_state(text if text is not None else _MISSING),
                            "content_state": _field_state(payload.get("content", _MISSING)),
                        }
                        message = _record("message", record_id, turn_id, timestamp, fields, locator)
                        records.append(message)
                        if role == "user":
                            submissions.append(message)
                        elif role == "assistant":
                            responses.append(message)
                        continue
                    if item_type == "custom_tool_call":
                        call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else None
                        call = _record("tool_call", record_id, turn_id, timestamp, {
                            "call_id": call_id,
                            "name": payload.get("name", _MISSING),
                            "arguments": payload.get("arguments", _MISSING),
                            "arguments_state": _field_state(payload.get("arguments", _MISSING)),
                            **_tool_input(payload, workspace_root=identity.get("cwd") if isinstance(identity.get("cwd"), str) else None),
                        }, locator)
                        calls.append(call)
                        records.append(call)
                        continue
                    if item_type == "custom_tool_call_output":
                        call_id = payload.get("call_id") if isinstance(payload.get("call_id"), str) else None
                        text = _text_content(payload.get("output", _MISSING))
                        output = _record("tool_result", record_id, turn_id, timestamp, {
                            "call_id": call_id,
                            "output": text,
                            "output_state": _field_state(text if text is not None else _MISSING),
                        }, locator)
                        outputs.append(output)
                        records.append(output)
                        continue
                    if item_type in {"reasoning", "compaction"}:
                        continue
                    diagnostics.append(_diagnostic("unsupported_response_item", f"unsupported response_item type: {item_type!r}", locator))
                    unknown_records += 1
                    continue

                if outer_type == "token_usage_record":
                    turn_id = payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None
                    usage = payload.get("usage")
                    fields = {
                        "thread_id": payload.get("thread_id", _MISSING),
                        "session_id": payload.get("session_id", _MISSING),
                        "response_id": payload.get("response_id", _MISSING),
                        "usage": usage if isinstance(usage, dict) else None,
                        "usage_state": _field_state(usage if isinstance(usage, dict) else _MISSING),
                        "usage_fields": _usage_fields(usage) if isinstance(usage, dict) else {},
                        "turn_token_usage": payload.get("turn_token_usage", _MISSING),
                        "thread_token_usage": payload.get("thread_token_usage", _MISSING),
                    }
                    usage_record = _record("usage", payload.get("response_id") if isinstance(payload.get("response_id"), str) else None, turn_id, timestamp, fields, locator)
                    usage_records.append(usage_record)
                    records.append(usage_record)
                    continue

                if outer_type == "event_msg":
                    event_type = payload.get("type")
                    turn_id = payload.get("turn_id") if isinstance(payload.get("turn_id"), str) else None
                    item = payload.get("item")
                    if event_type == "task_started":
                        task_start = _record("task_started", None, turn_id, timestamp, {
                            key: payload[key] for key in ("started_at", "model_context_window", "collaboration_mode_kind") if key in payload
                        }, locator)
                        task_starts.append(task_start)
                        records.append(task_start)
                        continue
                    if event_type == "task_complete":
                        task_complete = _record("task_complete", None, turn_id, timestamp, {
                            key: payload[key] for key in ("started_at", "completed_at", "duration_ms", "time_to_first_token_ms") if key in payload
                        }, locator)
                        task_completes.append(task_complete)
                        records.append(task_complete)
                        continue
                    if event_type == "token_count":
                        # token_usage_record is the authoritative per-turn
                        # source.  Keep token_count as a harmless metadata
                        # record without duplicating its cumulative values.
                        records.append(_record("token_count", None, None, timestamp, {
                            "total_token_usage": payload.get("info", {}).get("total_token_usage") if isinstance(payload.get("info"), dict) else None,
                            "last_token_usage": payload.get("info", {}).get("last_token_usage") if isinstance(payload.get("info"), dict) else None,
                        }, locator))
                        continue
                    if event_type == "item_completed" and isinstance(item, dict):
                        item_type = item.get("type")
                        item_id = item.get("id") if isinstance(item.get("id"), str) else None
                        if (
                            item_type == "FunctionCallOutput"
                            and item.get("namespace") == "codex_app"
                            and item.get("name") in {"create_thread", "send_message_to_thread"}
                            and isinstance(item.get("output"), str)
                        ):
                            match = _CODEX_DELEGATION_INPUT.fullmatch(item["output"])
                            if match is None:
                                diagnostics.append(_diagnostic(
                                    "malformed_codex_delegation",
                                    "Codex Desktop delegation output has no bounded input envelope",
                                    locator,
                                ))
                                continue
                            message = _record("message", item_id, turn_id, timestamp, {
                                "role": "user",
                                "phase": "delegated_input",
                                "text": html.unescape(match.group("input")),
                                "text_state": "value",
                                "content_state": "value",
                                "delegation_source_thread_id": html.unescape(match.group("source")),
                            }, locator)
                            submissions.append(message)
                            records.append(message)
                            continue
                        if item_type == "CommandExecution":
                            command, command_state = _command_line(item)
                            cwd, cwd_state = _normalize_cwd(item.get("cwd"), workspace_root=identity.get("cwd") if isinstance(identity.get("cwd"), str) else None)
                            execution = _record("command_execution", item_id, turn_id, timestamp, {
                                "command": item.get("command", _MISSING),
                                "command_state": command_state,
                                "command_line": command,
                                "cwd": cwd,
                                "cwd_state": cwd_state,
                                "status": item.get("status", _MISSING),
                                "status_state": _field_state(item.get("status", _MISSING)),
                                "exit_code": item.get("exit_code", _MISSING),
                                "exit_code_state": _field_state(item.get("exit_code", _MISSING)),
                                "stdout": item.get("stdout", _MISSING),
                                "stderr": item.get("stderr", _MISSING),
                                "aggregated_output": item.get("aggregated_output", _MISSING),
                            }, locator)
                            executions.append(execution)
                            records.append(execution)
                            continue
                        if item_type == "FileChange":
                            changes = item.get("changes")
                            normalized_changes: dict[str, Any] = {}
                            if isinstance(changes, dict):
                                for path, change in changes.items():
                                    normalized_path = _native_path(
                                        path,
                                        workspace_root=identity.get("cwd") if isinstance(identity.get("cwd"), str) else None,
                                    )
                                    if normalized_path == "checkout.py":
                                        normalized_path = "fixture_project/checkout.py"
                                    normalized_changes[normalized_path] = change
                            change = _record("file_change", item_id, turn_id, timestamp, {
                                "changes": normalized_changes,
                                "changes_state": _field_state(changes if isinstance(changes, dict) else _MISSING),
                                "status": item.get("status", _MISSING),
                                "status_state": _field_state(item.get("status", _MISSING)),
                                "stdout": item.get("stdout", _MISSING),
                                "stderr": item.get("stderr", _MISSING),
                            }, locator)
                            file_changes.append(change)
                            records.append(change)
                            continue
                        if item_type in {"UserMessage", "AgentMessage", "Reasoning"}:
                            # response_item is the canonical duplicate-safe
                            # visible/message source.  Keep only a lightweight
                            # duplicate marker so the native record family is
                            # still represented without counting it twice.
                            records.append(_record("item_completed", item_id, turn_id, timestamp, {
                                "item_type": item_type,
                            }, locator))
                            continue
                    diagnostics.append(_diagnostic("unsupported_event_msg", f"unsupported event_msg payload: {event_type!r}", locator))
                    unknown_records += 1
                    continue

                if outer_type in {"world_state", "reasoning", "thread_settings_applied"}:
                    # Deliberately ignore opaque policy/encrypted-reasoning
                    # payloads; they are not required by any survival metric.
                    continue
                diagnostics.append(_diagnostic("unsupported_envelope", f"unsupported rollout envelope: {outer_type!r}", locator))
                unknown_records += 1

    # A Desktop task stores response_item records without their turn ID, while
    # the surrounding task_started/task_complete and item_completed envelopes
    # retain it.  Infer only a non-overlapping native task window; this is a
    # native ordering join and is retained through each record's locator.
    if configuration_id == "codex-desktop":
        complete_by_turn = {
            item["turn_id"]: item
            for item in task_completes
            if isinstance(item.get("turn_id"), str)
        }
        task_windows = []
        for start in task_starts:
            native_turn = start.get("turn_id")
            end = complete_by_turn.get(native_turn)
            start_ordinal = start["locator"].get("ordinal")
            end_ordinal = end["locator"].get("ordinal") if end else None
            if (
                isinstance(native_turn, str)
                and isinstance(start_ordinal, int)
                and isinstance(end_ordinal, int)
                and start_ordinal < end_ordinal
            ):
                task_windows.append((start_ordinal, end_ordinal, native_turn))
        for collection in (submissions, responses, calls, outputs):
            for item in collection:
                if item.get("turn_id") is not None:
                    continue
                ordinal = item["locator"].get("ordinal")
                matches = [
                    native_turn for start, end, native_turn in task_windows
                    if isinstance(ordinal, int) and start < ordinal < end
                ]
                if len(matches) == 1:
                    item["turn_id"] = matches[0]
                    item["direct_task_window"] = True

    # Pair response-item calls with the nearest preceding execution/file edit
    # in the same native turn.  The 0.154 format does not put call_id on the
    # CommandExecution/FileChange item, so this ordering join is the only
    # available native relation; it is recorded as such in each fact.
    by_turn_calls: dict[str | None, list[dict[str, Any]]] = {}
    for call in calls:
        call["execution"] = None
        call["file_change"] = None
        by_turn_calls.setdefault(call["turn_id"], []).append(call)
    for event in sorted((*executions, *file_changes), key=lambda item: item["locator"].get("ordinal", 0)):
        candidates = [
            call for call in by_turn_calls.get(event["turn_id"], [])
            if call["locator"].get("ordinal", -1) < event["locator"].get("ordinal", -1)
            and (
                (event["kind"] == "command_execution" and call["execution"] is None)
                or (event["kind"] == "file_change" and call["file_change"] is None)
            )
        ]
        if not candidates:
            diagnostics.append(_diagnostic("unjoined_execution", "native execution/file change has no preceding custom_tool_call", event["locator"]))
            continue
        call = candidates[-1]
        if event["kind"] == "command_execution":
            call["execution"] = event
        else:
            call["file_change"] = event
    output_by_call: dict[str, list[dict[str, Any]]] = {}
    for output in outputs:
        call_id = output["fields"].get("call_id")
        if isinstance(call_id, str):
            output_by_call.setdefault(call_id, []).append(output)
        else:
            diagnostics.append(_diagnostic("tool_result_missing_call_id", "custom_tool_call_output has no call_id", output["locator"]))

    # Match exact input-side turn text and build the native-turn map used for
    # every downstream action/response relation.
    expected_turn_text = workload.turn_text
    native_turn_to_logical: dict[str, str] = {}
    turn_facts: list[dict[str, Any]] = []
    for submission in submissions:
        fields = submission["fields"]
        text = fields.get("text")
        canonical_text = _canonical_submission_text(text) if isinstance(text, str) else None
        matched = next((turn_id for turn_id, expected in expected_turn_text.items() if canonical_text == expected), None)
        marker_match = next((turn_id for turn_id, expected in expected_turn_text.items() if isinstance(canonical_text, str) and workload.run_id in canonical_text and (expected.split(".", 1)[0] in canonical_text)), None)
        if matched is None and marker_match is not None:
            diagnostics.append(_diagnostic("turn_text_mismatch", "native user text carries the workload marker but is not an exact frozen turn", submission["locator"]))
        if matched is None and marker_match is None:
            # Native CLI bundles also contain injected instructions and
            # environment messages.  They are outside the frozen workload
            # population and must not inflate submitted-turn eligibility.
            continue
        if matched is not None and isinstance(submission.get("turn_id"), str):
            native_turn_to_logical[submission["turn_id"]] = matched
        turn_facts.append({
            "id": submission["id"],
            "turn_id": matched,
            "native_turn_id": submission["turn_id"],
            "text": text,
            "delegation_source_thread_id": fields.get("delegation_source_thread_id"),
            "direct_task_window": bool(submission.get("direct_task_window")),
            "state": "present" if matched is not None else "unknown",
            "locator": submission["locator"],
        })

    delegated_turns = [
        row for row in turn_facts
        if isinstance(row.get("delegation_source_thread_id"), str)
        and row.get("turn_id") is not None
    ]
    delegation_valid = False
    if delegated_turns:
        delegated_sources = {row["delegation_source_thread_id"] for row in delegated_turns}
        delegated_counts = {
            turn_id: sum(row.get("turn_id") == turn_id for row in delegated_turns)
            for turn_id, _ in workload.turns
        }
        delegation_valid = len(delegated_sources) == 1 and all(
            count == 1 for count in delegated_counts.values()
        )
        if not delegation_valid:
            diagnostics.append(_diagnostic(
                "ambiguous_codex_delegation",
                "Desktop workload delegations must contain each turn exactly once from one source thread",
            ))
            rejected_native_turns = {row["native_turn_id"] for row in delegated_turns}
            for native_turn in rejected_native_turns:
                native_turn_to_logical.pop(native_turn, None)
            for row in delegated_turns:
                row["turn_id"] = None
                row["state"] = "unknown"
    if configuration_id == "codex-desktop":
        direct_turns = [
            row for row in turn_facts
            if row.get("direct_task_window") and row.get("delegation_source_thread_id") is None
        ]
        direct_counts = {
            turn_id: sum(row.get("turn_id") == turn_id for row in direct_turns)
            for turn_id, _ in workload.turns
        }
        direct_native_turns = {row.get("native_turn_id") for row in direct_turns}
        direct_valid = (
            len(direct_native_turns) == len(workload.turns)
            and None not in direct_native_turns
            and all(count == 1 for count in direct_counts.values())
        )
        if delegation_valid and direct_valid:
            diagnostics.append(_diagnostic(
                "ambiguous_codex_desktop_input",
                "Desktop bundle contains both complete delegated and direct workload turn sets",
            ))
            native_turn_to_logical.clear()
            for row in turn_facts:
                if row.get("turn_id") is not None:
                    row["turn_id"] = None
                    row["state"] = "unknown"
        elif delegation_valid:
            native_turn_to_logical = {
                row["native_turn_id"]: row["turn_id"]
                for row in delegated_turns
            }
            for row in turn_facts:
                if row.get("delegation_source_thread_id") is None and row.get("turn_id") is not None:
                    row["turn_id"] = None
                    row["state"] = "unknown"
        elif direct_valid:
            native_turn_to_logical = {
                row["native_turn_id"]: row["turn_id"]
                for row in direct_turns
            }
            for row in turn_facts:
                if not row.get("direct_task_window") and row.get("turn_id") is not None:
                    row["turn_id"] = None
                    row["state"] = "unknown"
        else:
            diagnostics.append(_diagnostic(
                "missing_codex_desktop_turn_binding",
                "Codex Desktop requires one complete validated delegated or direct task-turn set",
            ))
            native_turn_to_logical.clear()

    response_facts: list[dict[str, Any]] = []
    for response in responses:
        fields = response["fields"]
        text = fields.get("text")
        logical_turn = native_turn_to_logical.get(response["turn_id"])
        canary = workload.canary_by_turn.get(logical_turn) if logical_turn else None
        suffix_exact = isinstance(text, str) and isinstance(canary, str) and text.endswith(canary) and text.count(canary) == 1
        phase = fields.get("phase")
        is_visible = phase == "final_answer" and suffix_exact and logical_turn is not None
        response_facts.append({
            "id": response["id"],
            "turn_id": logical_turn,
            "native_turn_id": response["turn_id"],
            "text": text,
            "canary": canary,
            "suffix_exact": suffix_exact,
            "phase": phase,
            "state": "present" if is_visible else ("unknown" if logical_turn is None else "contradiction"),
            "locator": response["locator"],
        })

    # Convert the current native tool records into workload action candidates.
    action_facts: list[dict[str, Any]] = []
    actions_by_id: dict[str, list[dict[str, Any]]] = {}
    expected_actions = workload.action_by_id
    for call in calls:
        logical_turn = native_turn_to_logical.get(call["turn_id"])
        input_fields = call["fields"]
        native_execution = call.get("execution")
        native_change = call.get("file_change")
        execution_fields = native_execution["fields"] if native_execution else {}
        change_fields = native_change["fields"] if native_change else {}
        command = input_fields.get("command")
        workdir = input_fields.get("workdir") or execution_fields.get("cwd")
        patch_target = input_fields.get("patch_target")
        candidates: list[tuple[str, str]] = []
        expected_canary = _expected_run_canary(workload)
        if isinstance(command, str):
            for command_part in _split_commands(command):
                stripped = _strip_optional_run_canary(command_part, expected_canary)
                if stripped is None:
                    continue
                for expected in workload.actions:
                    if (
                        expected.command == stripped
                        and expected.turn_id == logical_turn
                        and workdir == "fixture_project"
                    ):
                        candidates.append((expected.id, stripped))
        if patch_target:
            for expected in workload.actions:
                patch_matches_expected = expected.target == patch_target or (
                    expected.kind == "edit"
                    and patch_target == Path(expected.target).name
                )
                if expected.kind == "edit" and patch_matches_expected and expected.turn_id == logical_turn:
                    candidates.append((expected.id, "<patch>"))
        if isinstance(command, str):
            split_command = tuple(
                stripped
                for stripped in (
                    _strip_optional_run_canary(part, expected_canary)
                    for part in _split_commands(command)
                )
                if stripped is not None
            )
        else:
            split_command = ()
        for expected_id, matched_command in candidates:
            expected = expected_actions[expected_id]
            occurrence = len(actions_by_id.get(expected_id, ())) + 1
            output_rows = output_by_call.get(input_fields.get("call_id"), [])
            output_text = "\n".join(
                text for row in output_rows
                for text in [_text_content(row["fields"].get("output"))]
                if text
            )
            markers = _helper_markers(output_text, (call.get("execution") or {}).get("fields", {}).get("stdout") if call.get("execution") else None)
            target_present = expected.kind == "edit" and (
                patch_target == expected.target
                or patch_target == Path(expected.target).name
            )
            action = {
                "id": expected_id if occurrence == 1 else f"{expected_id}#{occurrence}",
                "expected_id": expected_id,
                "occurrence": occurrence,
                "kind": expected.kind,
                "turn_id": expected.turn_id,
                "native_turn_id": call["turn_id"],
                "call_id": input_fields.get("call_id"),
                "command": matched_command if matched_command != "<patch>" else None,
                "command_state": "value" if matched_command != "<patch>" else "missing",
                "workdir": workdir,
                "workdir_state": input_fields.get("workdir_state"),
                "target": expected.target if target_present else None,
                "target_state": "value" if target_present else "missing",
                "helper_nonce": markers.get(expected.kind if expected.kind != "test" else ("final" if expected_id == "action-final" else "baseline")) if expected.kind != "edit" else None,
                "helper_nonce_state": "value" if (markers or expected.kind == "edit") else "missing",
                "native_execution_id": native_execution.get("id") if native_execution else None,
                "native_file_change_id": native_change.get("id") if native_change else None,
                "compound_command": len(_split_commands(command)) > 1 if isinstance(command, str) else False,
                "compound_index": split_command.index(matched_command) if matched_command in split_command else None,
                "compound_count": len(split_command) if matched_command in split_command else None,
                "later_helper_phases": [
                    (
                        "final" if later.id == "action-final"
                        else later.id.removeprefix("action-")
                    )
                    for later in workload.actions
                    if later.turn_id == expected.turn_id
                    and later.command in split_command[
                        split_command.index(matched_command) + 1:
                    ]
                ] if matched_command in split_command else [],
                "locator": (
                    native_change["locator"]
                    if matched_command == "<patch>" and native_change
                    else native_execution["locator"]
                    if native_execution
                    else call["locator"]
                ),
                "state": "present",
            }
            action_facts.append(action)
            actions_by_id.setdefault(expected_id, []).append(action)

    # Recognized result facts are tied to recognized action candidates.  The
    # FileChange event carries no exit code or content hashes in 0.154, so the
    # missing fields remain missing in the result.
    result_facts: list[dict[str, Any]] = []
    for action in action_facts:
        native_execution = None
        native_change = None
        for call in calls:
            if call["fields"].get("call_id") == action.get("call_id"):
                native_execution = call.get("execution")
                native_change = call.get("file_change")
                break
        execution_fields = native_execution["fields"] if native_execution else {}
        change_fields = native_change["fields"] if native_change else {}
        output_rows = output_by_call.get(action.get("call_id"), [])
        output_text = "\n".join(
            text for row in output_rows
            for text in [_text_content(row["fields"].get("output"))]
            if text
        )
        helper_phase = "final" if action["expected_id"] == "action-final" else action["expected_id"].removeprefix("action-")
        helper_nonce = _helper_markers(output_text, execution_fields.get("stdout"), execution_fields.get("aggregated_output")).get(helper_phase)
        relevant_native = native_change if action["kind"] == "edit" else native_execution
        relevant_fields = change_fields if action["kind"] == "edit" else execution_fields
        status = relevant_fields.get("status", _MISSING) if relevant_native else _MISSING
        exit_code = relevant_fields.get("exit_code", _MISSING) if relevant_native else _MISSING
        result_derivation = "native_event"
        all_markers = _helper_markers(
            output_text,
            execution_fields.get("stdout"),
            execution_fields.get("aggregated_output"),
        )
        if (
            native_execution
            and isinstance(action.get("compound_index"), int)
            and isinstance(action.get("compound_count"), int)
            and action["compound_index"] < action["compound_count"] - 1
            and any(phase in all_markers for phase in action.get("later_helper_phases", ()))
        ):
            # In an ``&&`` chain, observing a later command proves each
            # predecessor returned zero even when the aggregate event reports
            # only the final command's non-zero exit.
            status = "completed"
            exit_code = 0
            result_derivation = "shell_and_predecessor_executed"
        result = {
            "id": f"result-{action['expected_id'].removeprefix('action-')}" if action["occurrence"] == 1 else f"result-{action['expected_id'].removeprefix('action-')}#{action['occurrence']}",
            "expected_action_id": action["expected_id"],
            "action_id": action["id"],
            "turn_id": action["turn_id"],
            "call_id": action.get("call_id"),
            "status": None if status is _MISSING else status,
            "status_state": _field_state(status),
            "exit_code": None if exit_code is _MISSING else exit_code,
            "exit_code_state": _field_state(exit_code),
            "helper_nonce": helper_nonce,
            "helper_nonce_state": "value" if helper_nonce is not None else "missing",
            "output": output_text or None,
            "output_state": "value" if output_text else "missing",
            "native_execution_id": native_execution.get("id") if native_execution else None,
            "native_file_change_id": native_change.get("id") if native_change else None,
            "derivation": result_derivation,
            "locator": relevant_native.get("locator", action["locator"]) if relevant_native else action["locator"],
            "state": "present" if relevant_native else "unknown",
        }
        result_facts.append(result)

    # Turn-scoped usage: the last record's cumulative turn usage is the stable
    # aggregate; per-record usage is retained for reconciliation.
    usage_by_turn: dict[str, list[dict[str, Any]]] = {}
    for row in usage_records:
        if isinstance(row["turn_id"], str):
            usage_by_turn.setdefault(row["turn_id"], []).append(row)
    usage_facts: list[dict[str, Any]] = []
    for logical_turn, expected_text in workload.turns:
        native_turns = [native for native, logical in native_turn_to_logical.items() if logical == logical_turn]
        rows = [row for native in native_turns for row in usage_by_turn.get(native, [])]
        rows.sort(key=lambda row: row["locator"].get("ordinal", 0))
        final = rows[-1] if rows else None
        final_fields = final["fields"] if final else {}
        turn_total = final_fields.get("turn_token_usage", _MISSING)
        per_record_total = _sum_usage(row["fields"] for row in rows)
        reconciliation = isinstance(turn_total, Mapping) and per_record_total is not None and all(
            turn_total.get(key) == per_record_total.get(key)
            for key in per_record_total
        )
        usage_facts.append({
            "id": f"usage-{logical_turn}",
            "turn_id": logical_turn,
            "native_turn_id": next(iter(native_turns), None),
            "record_count": len(rows),
            "usage": final_fields.get("usage"),
            "usage_fields": final_fields.get("usage_fields", {}),
            "turn_token_usage": turn_total if isinstance(turn_total, Mapping) else None,
            "thread_token_usage": final_fields.get("thread_token_usage") if isinstance(final_fields.get("thread_token_usage"), Mapping) else None,
            "reconciles": reconciliation,
            "state": "present" if final is not None else "unknown",
            "locator": final["locator"] if final else None,
        })

    visible_responses: list[dict[str, Any]] = []
    decoded_response_slots = 0
    ambiguous_response_slots = 0
    for logical_turn, _ in workload.turns:
        candidates = [
            row for row in response_facts
            if row.get("phase") == "final_answer" and row.get("turn_id") == logical_turn
        ]
        if candidates:
            decoded_response_slots += 1
        if len(candidates) == 1 and candidates[0]["state"] == "present":
            visible_responses.append(candidates[0])
        elif len(candidates) > 1:
            ambiguous_response_slots += 1
            diagnostics.append(_diagnostic(
                "ambiguous_final_response",
                f"multiple final_answer records map to {logical_turn}",
            ))
            for row in candidates:
                row["state"] = "contradiction"
    exact_turns = [row for row in turn_facts if row["state"] == "present"]
    action_complete = all(
        action.get("target_state") == "value"
        and action.get("command_state") == "value"
        and action.get("workdir_state") == "value"
        for action in action_facts
    ) and len(action_facts) == len(workload.actions)
    result_complete = len(result_facts) == len(workload.actions) and all(
        result.get("status_state") == "value"
        and result.get("exit_code_state") in {"value", "zero"}
        and result.get("output_state") == "value"
        for result in result_facts
    )
    action_relations = [
        action for action in action_facts
        if isinstance(action.get("call_id"), str)
        and any(result.get("action_id") == action.get("id") and result.get("state") == "present" for result in result_facts)
    ]
    turn_response_relations = [
        response for response in visible_responses
        if response.get("turn_id") in {turn_id for turn_id, _ in workload.turns}
    ]
    r1 = next((row for row in exact_turns if row.get("turn_id") == "turn-r1"), None)
    r2 = next((row for row in exact_turns if row.get("turn_id") == "turn-r2"), None)
    r1_line = r1["locator"].get("ordinal") if r1 else None
    r2_line = r2["locator"].get("ordinal") if r2 else None
    r1_before_r2 = r1 is not None and r2 is not None and isinstance(r1_line, int) and isinstance(r2_line, int) and r1_line < r2_line
    r2_action = next((row for row in action_facts if row["expected_id"] == "action-edit"), None)
    final_action = next((row for row in action_facts if row["expected_id"] == "action-final"), None)
    r2_response = next((row for row in visible_responses if row.get("turn_id") == "turn-r2"), None)
    final_after_r2 = bool(
        r2_action and final_action and r2_response
        and all(isinstance(row["locator"].get("ordinal"), int) for row in (r2_action, final_action, r2_response))
        and r2_action["locator"]["ordinal"] < final_action["locator"]["ordinal"] < r2_response["locator"]["ordinal"]
    )
    if final_after_r2:
        final_after_r2_state = MEASURED
    elif r2_action and final_action and r2_response:
        # All relation endpoints are present but their native order conflicts
        # with the workload relation.  This is resolved negative evidence.
        final_after_r2_state = CONTRADICTION
    elif complete_root:
        # A complete copied root proves that the missing endpoint was absent;
        # do not turn a zero decoded population into a measured zero.
        final_after_r2_state = NATIVE_ABSENT
    else:
        final_after_r2_state = UNRESOLVED
    model_config_supported = bool(contexts) and all(
        isinstance(context["fields"].get("model"), str)
        and isinstance(context["fields"].get("configuration"), str)
        for context in contexts
        if context["turn_id"] in native_turn_to_logical
    )
    usage_complete = len(usage_facts) == len(workload.turns) and all(row["state"] == "present" for row in usage_facts)
    token_semantics_complete = usage_complete and all(
        all(row["usage_fields"].get(f"{key}_state") in {"value", "zero", "null"} for key in (
            "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"
        )) for row in usage_facts
    )
    usage_reconciles = usage_complete and all(row["reconciles"] for row in usage_facts)
    package_decode_ok = not any(item["code"] == "malformed_record" for item in diagnostics)

    metrics: list[dict[str, Any]] = []
    metrics.append(_row("work.submitted_turns", _terminal_state(present=bool(exact_turns), complete_root=complete_root), len(exact_turns), 2, len(exact_turns), facts=[row["id"] for row in exact_turns]))
    visible_response_state = CONTRADICTION if ambiguous_response_slots else _terminal_state(
        present=bool(visible_responses), complete_root=complete_root
    )
    metrics.append(_row("work.visible_responses", visible_response_state, len(visible_responses), 2, decoded_response_slots, facts=[row["id"] for row in visible_responses]))
    # These populations are classified successfully even when individual
    # required fields are absent.  Missing native target/exit fields reduce
    # the fraction; they are writer observations, not decoder failures.
    metrics.append(_row("work.actions", MEASURED if action_facts else _terminal_state(present=False, complete_root=complete_root), sum(1 for row in action_facts if row.get("target_state") == "value"), 4, len(action_facts), facts=[row["id"] for row in action_facts], reason=None if action_complete else "0.154 shell execution records do not retain a target path for inspect/test actions"))
    metrics.append(_row("work.results", MEASURED if result_facts else _terminal_state(present=False, complete_root=complete_root), sum(1 for row in result_facts if row.get("status_state") == "value" and row.get("exit_code_state") in {"value", "zero"} and row.get("output_state") == "value"), 4, len(result_facts), facts=[row["id"] for row in result_facts], reason=None if result_complete else "FileChange has no native exit_code in Codex 0.154"))
    changed = [
        row for row in file_changes
        if row["fields"].get("changes_state") == "value"
        and any(
            path in {"fixture_project/checkout.py", "checkout.py"}
            for path in row["fields"].get("changes", {})
        )
    ]
    metrics.append(_row("work.changed_files", MEASURED if changed else _terminal_state(present=False, complete_root=complete_root), 0, 1, len(changed), facts=[row["id"] for row in changed], reason=None if not changed else "native FileChange carries a diff but no before/after SHA-256 pair"))
    metrics.append(_row("causal.action_result", MEASURED if len(action_relations) == 4 else _terminal_state(present=bool(action_relations), complete_root=complete_root), len(action_relations), 4, len(action_relations), facts=[row["id"] for row in action_relations]))
    metrics.append(_row("causal.turn_response", MEASURED if len(turn_response_relations) == 2 else _terminal_state(present=bool(turn_response_relations), complete_root=complete_root), len(turn_response_relations), 2, len(turn_response_relations), facts=[row["id"] for row in turn_response_relations]))
    metrics.append(_row("revision.r1", _terminal_state(present=r1 is not None, complete_root=complete_root), 1 if r1 else 0, 1, 1 if r1 else 0, facts=[r1["id"]] if r1 else []))
    metrics.append(_row("revision.r2", _terminal_state(present=r2 is not None, complete_root=complete_root), 1 if r2 else 0, 1, 1 if r2 else 0, facts=[r2["id"]] if r2 else []))
    metrics.append(_row("revision.r1_r2_order", MEASURED if r1_before_r2 else (_terminal_state(present=bool(r1 or r2), complete_root=complete_root, contradiction=bool(r1 and r2))), 1 if r1_before_r2 else 0, 1, 1 if r1 and r2 else 0, facts=[row["id"] for row in (r1, r2) if row]))
    metrics.append(_row("revision.final_after_r2", final_after_r2_state, 1 if final_after_r2 else 0, 1, 1 if final_after_r2 else 0, facts=[row["id"] for row in (r2_action, final_action, r2_response) if row]))
    metrics.append(_row("attribution.model_config", MEASURED if contexts else _terminal_state(present=False, complete_root=complete_root), 2 if model_config_supported else 0, 2, 2 if contexts else 0, facts=[row["id"] for row in contexts], reason=None if model_config_supported else "turn_context has model but no configuration identity"))
    metrics.append(_row("attribution.usage", MEASURED if usage_complete else _terminal_state(present=usage_complete, complete_root=complete_root), sum(1 for row in usage_facts if row["state"] == "present"), 2, len(usage_facts), facts=[row["id"] for row in usage_facts if row["state"] == "present"]))
    metrics.append(_row("attribution.token_semantics", MEASURED if token_semantics_complete else (DECODER_UNSUPPORTED if usage_complete else _terminal_state(present=usage_complete, complete_root=complete_root)), sum(1 for row in usage_facts if row["state"] == "present"), 2, len(usage_facts), facts=[row["id"] for row in usage_facts], reason=None if token_semantics_complete else "usage field names are incomplete or missing"))
    metrics.append(_row("attribution.reconciliation", MEASURED if usage_reconciles else (DECODER_UNSUPPORTED if usage_complete else _terminal_state(present=usage_complete, complete_root=complete_root)), 1 if usage_reconciles else 0, 1, 1 if usage_complete else 0, facts=[row["id"] for row in usage_facts if row["reconciles"]], reason=None if usage_reconciles else "per-turn token usage does not reconcile to the retained cumulative total"))
    root_state = MEASURED if complete_root else UNRESOLVED
    metrics.append(_row("portable.complete_root", root_state, 1 if complete_root else 0, 1, 1 if complete_root else 0, reason=None if complete_root else "decode.json does not prove the complete Codex home inventory"))
    companion_state = MEASURED if required_companions is not None and all((Path(package) / item).is_file() for item in required_companions) else UNRESOLVED
    metrics.append(_row("portable.companions", companion_state, 1 if companion_state == MEASURED else 0, 1, 1 if companion_state == MEASURED else 0, reason=None if companion_state == MEASURED else "required companion set was not supplied by the copied-package contract"))
    isolated_state = (
        MEASURED if isolated_decode_proven and package_decode_ok
        else INVALID_CAPTURE if not package_decode_ok
        else UNRESOLVED
    )
    metrics.append(_row("portable.isolated_decode", isolated_state, 1 if isolated_state == MEASURED else 0, 1, 1 if isolated_state == MEASURED else 0, reason=None if isolated_state == MEASURED else "source-denied copied-package decode was not proven"))
    metrics.append(_row("portable.canonical_equality", MEASURED if canonical_equality_proven else UNRESOLVED, 1 if canonical_equality_proven else 0, 1, 1 if canonical_equality_proven else 0, reason=None if canonical_equality_proven else "no ordinary decode was supplied to compare with this copied-package decode"))

    facts = {
        "identity": identity,
        "submitted_turns": turn_facts,
        "visible_responses": response_facts,
        "actions": action_facts,
        "results": result_facts,
        "changed_files": [{
            "id": row["id"],
            "paths": sorted(row["fields"].get("changes", {})),
            "before_sha256": None,
            "before_sha256_state": "missing",
            "after_sha256": None,
            "after_sha256_state": "missing",
            "locator": row["locator"],
            "state": "unknown",
        } for row in changed],
        "action_result_relations": action_relations,
        "turn_response_relations": turn_response_relations,
        "revisions": {"r1": r1, "r2": r2, "ordered": r1_before_r2},
        "usage": usage_facts,
        "model_contexts": contexts,
        "portable": {
            "complete_root": {"state": "present" if complete_root else "unknown"},
            "companions": {"state": "present" if companion_state == MEASURED else "unknown", "required": list(required_companions or ())},
            "isolated_decode": {"state": "present" if isolated_decode_proven and package_decode_ok else "unknown"},
            "canonical_equality": {"state": "present" if canonical_equality_proven else "unknown"},
        },
    }
    measurement = {
        "schema_version": SCHEMA_VERSION,
        "run_id": workload.run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
        "metrics": [
            {
                key: row[key]
                for key in ("id", "state", "correct", "observed_eligible", "decoded_eligible")
            }
            for row in metrics
        ],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "format": NATIVE_FORMAT,
        "decoder": {"id": DECODER_ID, "version": DECODER_VERSION},
        "package": {
            "format": manifest["format"],
            "artifacts": [
                {"id": artifact.id, "path": artifact.relative_path, "sha256": artifact.sha256, "size_bytes": artifact.size_bytes}
                for artifact in artifacts
            ],
        },
        "identity": identity,
        "session_id": session_id,
        "records": records,
        "facts": facts,
        "metrics": metrics,
        "measurement": measurement,
        "diagnostics": diagnostics,
        "unknown_records": unknown_records,
        "status": "ok" if package_decode_ok else "partial",
    }


decode = decode_codex_cli_bundle
decode_codex_cli = decode_codex_cli_bundle


__all__ = [
    "CONTRADICTION", "DECODER_ID", "DECODER_UNSUPPORTED", "DECODER_VERSION",
    "FROZEN_SURVIVAL_V1", "FrozenWorkload", "ExpectedAction", "INVALID_CAPTURE",
    "MEASURED", "NATIVE_ABSENT", "NATIVE_FORMAT", "UNRESOLVED", "CodexCLIDecodeError",
    "decode", "decode_codex_cli", "decode_codex_cli_bundle",
]
