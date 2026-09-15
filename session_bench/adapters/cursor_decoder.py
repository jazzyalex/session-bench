"""Native-only decoder for the retained Cursor CLI survival-v1 transcript.

The Cursor CLI capture used by the survival-v1 calibration is a small, public
JSONL derivative.  It is intentionally decoded separately from
``adapters.cursor``: that module describes acquisition, while this module only
reads one caller-supplied copied bundle.  It never discovers a Cursor profile,
opens the SQLite store, starts a process, consults observer truth, or calls a
network service.

The decoder keeps absence and uncertainty distinct.  ``absent`` means that a
valid, decoded transcript contains no record of the requested kind.  ``unknown``
means that the transcript or its declared boundary is not sufficient to decide.
Neither state is turned into a fabricated event.  In particular, a missing
tool call is never replaced with a guessed ``tool987``/``queuegz`` record.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Final

from ..survival_metrics import METRICS


FORMAT_VERSION: Final = "cursor-cli-native-jsonl-v1"
DECODER_CONTRACT_VERSION: Final = "cursor-cli-decoder-v1"
DESKTOP_FORMAT_VERSION: Final = "cursor-desktop-agent-transcript-jsonl-v1"
DESKTOP_DECODER_CONTRACT_VERSION: Final = "cursor-desktop-transcript-decoder-v1"
TRANSCRIPT_NAME: Final = "cursor-session.jsonl"
RECEIPT_NAME: Final = "sanitization-receipt.json"
DESKTOP_COMPANION_NAME: Final = "session-companions.jsonl"
MAX_TRANSCRIPT_BYTES: Final = 16 * 1024 * 1024
MAX_RECORD_BYTES: Final = 1024 * 1024
MAX_RECORDS: Final = 10_000

SURVIVAL_METRIC_IDS: tuple[str, ...] = tuple(METRICS)


class FactState(str, Enum):
    """State of one native semantic fact.

    ``KNOWN`` is used only when the source record contains the required value.
    ``ABSENT`` is a positive absence in an otherwise valid decoded stream.
    ``UNKNOWN`` covers malformed input, an incomplete boundary, and fields that
    cannot be established from the retained native format.
    """

    KNOWN = "known"
    ABSENT = "absent"
    UNKNOWN = "unknown"


KNOWN: Final = FactState.KNOWN.value
ABSENT: Final = FactState.ABSENT.value
UNKNOWN: Final = FactState.UNKNOWN.value


class CursorDecoderError(ValueError):
    """The supplied path is not an isolated copied Cursor bundle."""


class CursorBundleError(CursorDecoderError):
    """The copied bundle violates the decoder's closed package contract."""


class CursorTranscriptError(CursorDecoderError):
    """The transcript cannot be opened or is outside the supported shape."""


@dataclass(frozen=True)
class CursorDecodeResult(Mapping[str, Any]):
    """Mapping-compatible native decode result.

    The mapping form keeps this seam convenient for the existing JSON-oriented
    evaluators, while properties provide stable access for focused callers.
    ``data`` contains only relative locators and copied bytes' digests; it does
    not expose the machine-local bundle path.
    """

    data: Mapping[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.data, ensure_ascii=False))

    @property
    def records(self) -> list[dict[str, Any]]:
        return self.data["records"]

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.data["events"]

    @property
    def turns(self) -> list[dict[str, Any]]:
        return self.data["turns"]

    @property
    def responses(self) -> list[dict[str, Any]]:
        return self.data["responses"]

    @property
    def actions(self) -> list[dict[str, Any]]:
        return self.data["actions"]

    @property
    def results(self) -> list[dict[str, Any]]:
        return self.data["results"]

    @property
    def file_changes(self) -> list[dict[str, Any]]:
        return self.data["file_changes"]

    @property
    def relations(self) -> list[dict[str, Any]]:
        return self.data["relations"]

    @property
    def facts(self) -> Mapping[str, Any]:
        return self.data["facts"]

    @property
    def metric_facts(self) -> Mapping[str, Any]:
        return self.data["metrics"]

    @property
    def metrics(self) -> Mapping[str, Any]:
        return self.data["metrics"]

    @property
    def diagnostics(self) -> list[dict[str, Any]]:
        return self.data["diagnostics"]

    @property
    def semantic_sha256(self) -> str:
        return self.data["semantic_sha256"]

    @property
    def semantic_digest(self) -> str:
        """Compatibility spelling for callers that use digest terminology."""

        return self.data["semantic_sha256"]


@dataclass(frozen=True)
class CursorCliNativeDecoder:
    """Stateless object wrapper for dependency-injection callers."""

    def decode(self, bundle: str | Path) -> CursorDecodeResult:
        return decode_cursor_bundle(bundle)

    def decode_bundle(self, bundle: str | Path) -> CursorDecodeResult:
        return decode_cursor_bundle(bundle)


@dataclass(frozen=True)
class CursorDesktopTranscriptDecoder:
    """Decode the copied Desktop transcript without claiming complete-root coverage."""

    def decode(self, bundle: str | Path) -> CursorDecodeResult:
        return decode_cursor_desktop_bundle(bundle)

    def decode_bundle(self, bundle: str | Path) -> CursorDecodeResult:
        return decode_cursor_desktop_bundle(bundle)


_RESPONSE_MARKER = re.compile(r"SB_SURVIVAL_V1_RESPONSE_[^\s<`]+")
_RUN_MARKER = re.compile(r"SB_SURVIVAL_V1_RUN_[^\s<`]+")
_CONTEXT_MARKER = re.compile(r"SB_SURVIVAL_V1_CONTEXT_[^\s<`]+")
_REVISION_MARKER = re.compile(r"\bR([12])\b", re.IGNORECASE)
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL)
_TIMESTAMP = re.compile(r"<timestamp>.*?</timestamp>\s*", re.DOTALL)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_NORMAL_CURSOR_ROOTS = (
    Path.home() / ".cursor",
    Path.home() / "Library" / "Application Support" / "Cursor",
    Path.home() / ".config" / "cursor",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_json(raw: bytes, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ValueError(f"{label}: non-finite JSON number {value}")

    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_constant,
    )


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _reject_normal_cursor_path(path: Path) -> None:
    resolved = path.resolve(strict=False)
    for candidate in _NORMAL_CURSOR_ROOTS:
        normal = candidate.resolve(strict=False)
        if resolved == normal or _is_inside(resolved, normal):
            raise CursorBundleError("normal or personal Cursor roots are not decoder inputs")


def _validate_bundle_root(bundle: str | Path, *, desktop: bool = False) -> tuple[Path, Path]:
    if not isinstance(bundle, (str, Path)):
        raise CursorBundleError("copied Cursor bundle must be a path")
    raw = Path(bundle).expanduser()
    if not raw.is_absolute():
        raise CursorBundleError("copied Cursor bundle must be an absolute path")
    if raw.is_symlink():
        raise CursorBundleError("copied Cursor bundle may not be a symlink")
    root = raw.resolve(strict=False)
    _reject_normal_cursor_path(root)
    if not root.is_dir() or root.is_symlink():
        raise CursorBundleError("copied Cursor bundle must be an existing real directory")

    # A decoder input is the copied native package itself, not the surrounding
    # retained attempt.  Requiring the transcript at the package root prevents
    # accidental reads of project, observer, config, or private-native siblings.
    transcript = root / TRANSCRIPT_NAME
    if not transcript.is_file() or transcript.is_symlink():
        raise CursorBundleError(
            f"copied Cursor bundle must contain only a root-level {TRANSCRIPT_NAME}"
        )
    allowed = {TRANSCRIPT_NAME, RECEIPT_NAME}
    if desktop:
        allowed.add(DESKTOP_COMPANION_NAME)
    for entry in root.iterdir():
        if entry.is_symlink():
            raise CursorBundleError(f"symlinks are not allowed in copied bundle: {entry.name}")
        if entry.is_dir() or entry.name not in allowed:
            raise CursorBundleError(
                f"copied bundle contains an undeclared sibling: {entry.name}"
            )
    return root, transcript


def _validate_transcript_file(transcript: Path) -> tuple[Path, Path]:
    if not transcript.is_absolute():
        raise CursorTranscriptError("Cursor transcript must be an absolute path")
    if transcript.is_symlink():
        raise CursorTranscriptError("Cursor transcript may not be a symlink")
    path = transcript.resolve(strict=False)
    _reject_normal_cursor_path(path)
    if not path.is_file():
        raise CursorTranscriptError("Cursor transcript is not an existing regular file")
    parent = path.parent
    for entry in parent.iterdir():
        if entry.is_symlink():
            raise CursorTranscriptError("Cursor transcript parent contains a symlink")
    return parent, path


def _locator(line: int, byte_start: int, byte_end: int, raw: bytes) -> dict[str, Any]:
    return {
        "artifact": TRANSCRIPT_NAME,
        "line": line,
        "byte_start": byte_start,
        "byte_end": byte_end,
        "record_sha256": _sha256_bytes(raw),
    }


def _fact(
    state: str,
    value: Any = None,
    *,
    reason: str | None = None,
    locators: Sequence[Mapping[str, Any]] = (),
    partial: Any = None,
) -> dict[str, Any]:
    if state not in {KNOWN, ABSENT, UNKNOWN}:
        raise ValueError(f"unsupported native fact state: {state}")
    result: dict[str, Any] = {
        "state": state,
        "value": value if state == KNOWN else None,
        "locators": [dict(item) for item in locators],
    }
    if reason:
        result["reason"] = reason
    if state == UNKNOWN and partial is not None:
        result["partial"] = partial
    return result


def _marker(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:)]}")


def _markers(pattern: re.Pattern[str], text: str) -> list[str]:
    values: list[str] = []
    for match in pattern.finditer(text):
        value = match.group(0).rstrip(".,;:)]}")
        if value not in values:
            values.append(value)
    return values


def _text_from_block(block: Mapping[str, Any]) -> str | None:
    value = block.get("text")
    return value if isinstance(value, str) else None


def _content_blocks(message: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str | None]:
    content = message.get("content")
    if isinstance(content, str):
        return ([{"type": "text", "text": content}], None)
    if not isinstance(content, list):
        return ([], "message content is missing or not an array")
    blocks: list[Mapping[str, Any]] = []
    for index, item in enumerate(content):
        if not isinstance(item, Mapping):
            return (blocks, f"content block {index} is not an object")
        blocks.append(dict(item))
    return blocks, None


def _extract_query(text: str) -> tuple[str, bool]:
    match = _USER_QUERY.search(text)
    if match:
        return match.group(1).strip(), True
    return _TIMESTAMP.sub("", text).strip(), False


def _nearest_turn(turns: Sequence[Mapping[str, Any]], sequence: int) -> str | None:
    selected: Mapping[str, Any] | None = None
    for turn in turns:
        if turn.get("sequence", 0) < sequence:
            selected = turn
        else:
            break
    value = selected.get("id") if selected else None
    return value if isinstance(value, str) else None


def _action_kind(name: Any) -> str:
    if not isinstance(name, str):
        return "unknown"
    value = name.lower().replace("-", "_")
    return {
        "shell": "shell",
        "bash": "shell",
        "read": "read",
        "write": "write",
        "glob": "glob",
        "grep": "search",
        "search": "search",
    }.get(value, "unknown")


def _target_from_input(value: Mapping[str, Any]) -> str | None:
    for key in ("path", "target", "working_directory", "target_directory"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _receipt_diagnostics(root: Path, transcript: Path, line_count: int) -> tuple[list[dict[str, Any]], bool]:
    receipt = root / RECEIPT_NAME
    if not receipt.exists():
        return [{"code": "receipt_missing", "detail": "copied transcript has no sanitization receipt"}], False
    try:
        value = _strict_json(receipt.read_bytes(), label=RECEIPT_NAME)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [{"code": "invalid_receipt", "detail": str(exc)}], False
    if not isinstance(value, Mapping):
        return [{"code": "invalid_receipt", "detail": "receipt is not an object"}], False
    findings: list[dict[str, Any]] = []
    expected_digest = value.get("sanitized_sha256")
    if not isinstance(expected_digest, str) or not _HEX64.fullmatch(expected_digest):
        findings.append({"code": "invalid_receipt", "detail": "sanitized_sha256 is missing"})
    elif expected_digest != _sha256_file(transcript):
        findings.append({"code": "receipt_digest_mismatch", "detail": "transcript digest differs from receipt"})
    expected_lines = value.get("line_count")
    if type(expected_lines) is not int or expected_lines != line_count:
        findings.append({"code": "receipt_line_count_mismatch", "detail": "transcript line count differs from receipt"})
    companion = root / DESKTOP_COMPANION_NAME
    if not companion.exists() and ("companion_sha256" in value or "companion_row_count" in value):
        findings.append({"code": "companion_missing", "detail": "declared Desktop companion is absent"})
    if companion.exists():
        expected_companion = value.get("companion_sha256")
        if not isinstance(expected_companion, str) or not _HEX64.fullmatch(expected_companion):
            findings.append({"code": "invalid_receipt", "detail": "companion_sha256 is missing"})
        elif expected_companion != _sha256_file(companion):
            findings.append({"code": "companion_digest_mismatch", "detail": "companion digest differs from receipt"})
        expected_count = value.get("companion_row_count")
        if type(expected_count) is not int or expected_count != len(companion.read_bytes().splitlines()):
            findings.append({"code": "companion_row_count_mismatch", "detail": "companion row count differs from receipt"})
    return findings, not findings


def _read_records(transcript: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        size = transcript.stat().st_size
    except OSError as exc:
        raise CursorTranscriptError("cannot stat Cursor transcript") from exc
    if size > MAX_TRANSCRIPT_BYTES:
        raise CursorTranscriptError("Cursor transcript exceeds the decoder byte bound")

    records: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    byte_start = 0
    try:
        handle = transcript.open("rb")
    except OSError as exc:
        raise CursorTranscriptError("cannot open Cursor transcript") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, 1):
            byte_end = byte_start + len(raw_line)
            raw = raw_line.rstrip(b"\r\n")
            byte_start = byte_end
            if not raw.strip():
                continue
            if len(raw) > MAX_RECORD_BYTES:
                diagnostics.append({"code": "record_too_large", "line": line_number})
                continue
            location = _locator(line_number, byte_end - len(raw_line), byte_end, raw)
            try:
                value = _strict_json(raw, label=f"line {line_number}")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                diagnostics.append({"code": "malformed_record", "line": line_number, "detail": str(exc)})
                continue
            if not isinstance(value, Mapping):
                diagnostics.append({"code": "malformed_record", "line": line_number, "detail": "record is not an object"})
                continue
            record: dict[str, Any] = {
                "sequence": line_number,
                "id": value.get("id") if isinstance(value.get("id"), str) else None,
                "role": value.get("role") if isinstance(value.get("role"), str) else None,
                "type": value.get("type") if isinstance(value.get("type"), str) else None,
                "locator": location,
            }
            role = record["role"]
            if role in {"user", "assistant", "tool", "system"}:
                message = value.get("message")
                if not isinstance(message, Mapping):
                    diagnostics.append({"code": "missing_message", "line": line_number})
                    record["kind"] = "message"
                    record["content"] = []
                    record["complete"] = False
                else:
                    blocks, error = _content_blocks(message)
                    if error:
                        diagnostics.append({"code": "malformed_content", "line": line_number, "detail": error})
                    record["kind"] = "message"
                    record["content"] = [dict(block) for block in blocks]
                    record["complete"] = error is None
            elif record["type"] == "turn_ended":
                record["kind"] = "turn_ended"
                record["status"] = value.get("status") if isinstance(value.get("status"), str) else None
                record["complete"] = True
            else:
                diagnostics.append({"code": "unknown_record", "line": line_number})
                record["kind"] = "unknown"
                record["complete"] = False
            records.append(record)
            if len(records) > MAX_RECORDS:
                raise CursorTranscriptError("Cursor transcript exceeds the decoder record bound")
    return records, diagnostics


def _decode_semantics(
    records: Sequence[Mapping[str, Any]],
    *,
    parse_diagnostics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    turns: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    next_turn = 1

    for record in records:
        sequence = int(record["sequence"])
        locator = record["locator"]
        role = record.get("role")
        if record.get("kind") == "turn_ended":
            events.append({"id": f"record-{sequence}", "kind": "turn_ended", "sequence": sequence, "locator": locator})
            continue
        if record.get("kind") != "message":
            continue
        blocks = record.get("content")
        if not isinstance(blocks, list):
            continue
        text_parts = [block["text"] for block in blocks if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)]
        text = "\n".join(text_parts)
        turn_id: str | None = None
        if role == "user":
            query, query_complete = _extract_query(text)
            revisions = _markers(_REVISION_MARKER, query)
            revision = revisions[0].lower() if revisions else None
            turn_id = f"turn-{next_turn}"
            next_turn += 1
            turn = {
                "id": turn_id,
                "ordinal": len(turns) + 1,
                "sequence": sequence,
                "revision": revision,
                "query": query,
                "query_complete": query_complete,
                "context_marker": _marker(_CONTEXT_MARKER, query),
                "run_canary": _marker(_RUN_MARKER, query),
                "response_canary": _marker(_RESPONSE_MARKER, query),
                "locator": locator,
                "fields": {
                    "query": _fact(KNOWN if query_complete else UNKNOWN, query if query_complete else None, reason=None if query_complete else "user_query wrapper is absent", locators=(locator,), partial=query),
                    "revision": _fact(KNOWN if revision else UNKNOWN, revision, reason=None if revision else "R1/R2 revision marker is absent", locators=(locator,)),
                    "context_marker": _fact(KNOWN if _marker(_CONTEXT_MARKER, query) else ABSENT, _marker(_CONTEXT_MARKER, query), reason=None if _marker(_CONTEXT_MARKER, query) else "context marker is absent", locators=(locator,)),
                    "run_canary": _fact(KNOWN if _marker(_RUN_MARKER, query) else ABSENT, _marker(_RUN_MARKER, query), reason=None if _marker(_RUN_MARKER, query) else "run canary is absent", locators=(locator,)),
                },
            }
            turns.append(turn)
            events.append({"id": turn_id, "kind": "user_turn", "sequence": sequence, "turn_id": turn_id, "locator": locator})

        nearest = _nearest_turn(turns, sequence)
        for content_index, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str) and role == "assistant":
                response_markers = _markers(_RESPONSE_MARKER, block["text"])
                if response_markers:
                    response_id = f"response-{sequence}-{content_index}"
                    response = {
                        "id": response_id,
                        "sequence": sequence,
                        "turn_id": nearest,
                        "text": block["text"],
                        "canaries": response_markers,
                        "response_canary": response_markers[-1],
                        "locator": locator,
                        "fields": {
                            "text": _fact(KNOWN, block["text"], locators=(locator,)),
                            "turn": _fact(KNOWN if nearest else UNKNOWN, nearest, reason=None if nearest else "no preceding user turn", locators=(locator,)),
                            "canary": _fact(KNOWN, response_markers[-1], locators=(locator,)),
                        },
                    }
                    responses.append(response)
                    events.append({"id": response_id, "kind": "visible_response", "sequence": sequence, "turn_id": nearest, "locator": locator})
                    if nearest:
                        relations.append({"id": f"turn-response-{nearest}-{response_id}", "kind": "turn_response", "from_id": nearest, "to_id": response_id, "sequence": sequence, "state": KNOWN})
            if block_type == "tool_use":
                name = block.get("name") if isinstance(block.get("name"), str) else None
                raw_input = block.get("input")
                action_input = dict(raw_input) if isinstance(raw_input, Mapping) else None
                action_id = block.get("id") if isinstance(block.get("id"), str) else f"action-{sequence}-{content_index}"
                call_id = block.get("tool_call_id") if isinstance(block.get("tool_call_id"), str) else None
                target = _target_from_input(action_input) if action_input is not None else None
                action = {
                    "id": action_id,
                    "sequence": sequence,
                    "content_index": content_index,
                    "turn_id": nearest,
                    "tool_name": name,
                    "action_kind": _action_kind(name),
                    "input": action_input,
                    "target": target,
                    "call_id": call_id,
                    "locator": locator,
                    "fields": {
                        "name": _fact(KNOWN if name else UNKNOWN, name, reason=None if name else "tool name is absent", locators=(locator,)),
                        "arguments": _fact(KNOWN if action_input is not None else UNKNOWN, action_input, reason=None if action_input is not None else "tool input is absent", locators=(locator,)),
                        "target": _fact(KNOWN if target else ABSENT, target, reason=None if target else "tool input has no target-like field", locators=(locator,)),
                        "turn": _fact(KNOWN if nearest else UNKNOWN, nearest, reason=None if nearest else "no preceding user turn", locators=(locator,)),
                        "call_id": _fact(KNOWN if call_id else ABSENT, call_id, reason=None if call_id else "native tool call id is absent", locators=(locator,)),
                    },
                }
                actions.append(action)
                events.append({"id": action_id, "kind": "action", "sequence": sequence, "turn_id": nearest, "locator": locator})
                if action["action_kind"] == "write":
                    path_value = action_input.get("path") if isinstance(action_input, Mapping) else None
                    contents = action_input.get("contents") if isinstance(action_input, Mapping) else None
                    path_state = KNOWN if isinstance(path_value, str) and path_value else UNKNOWN
                    content_state = KNOWN if isinstance(contents, str) else UNKNOWN
                    change = {
                        "id": f"file-change-{action_id}",
                        "sequence": sequence,
                        "turn_id": nearest,
                        "path": path_value if path_state == KNOWN else None,
                        "after_sha256": _sha256_bytes(contents.encode("utf-8")) if content_state == KNOWN else None,
                        "before_sha256": None,
                        "locator": locator,
                        "fields": {
                            "path": _fact(path_state, path_value, reason=None if path_state == KNOWN else "write path is absent", locators=(locator,)),
                            "before_sha256": _fact(ABSENT, reason="native Write record does not retain the pre-edit hash", locators=(locator,)),
                            "after_sha256": _fact(content_state, _sha256_bytes(contents.encode("utf-8")) if content_state == KNOWN else None, reason=None if content_state == KNOWN else "write contents are absent", locators=(locator,)),
                        },
                    }
                    file_changes.append(change)
                    events.append({"id": change["id"], "kind": "file_change", "sequence": sequence, "turn_id": nearest, "locator": locator})
            if block_type == "tool_result":
                result_id = block.get("id") if isinstance(block.get("id"), str) else f"result-{sequence}-{content_index}"
                call_id = block.get("toolCallId", block.get("tool_call_id", block.get("call_id")))
                result = {
                    "id": result_id,
                    "sequence": sequence,
                    "turn_id": nearest,
                    "call_id": call_id if isinstance(call_id, str) else None,
                    "status": block.get("status") if isinstance(block.get("status"), str) else None,
                    "exit_code": block.get("exit_code") if type(block.get("exit_code")) is int else None,
                    "output": block.get("output") if isinstance(block.get("output"), str) else None,
                    "locator": locator,
                }
                results.append(result)
                events.append({"id": result_id, "kind": "result", "sequence": sequence, "turn_id": nearest, "locator": locator})

    # Only join results when the native stream gives both sides a stable call
    # identity.  Ordered proximity is not a causal join.
    actions_by_call = {item["call_id"]: item for item in actions if item.get("call_id")}
    for result in results:
        action = actions_by_call.get(result.get("call_id"))
        if action is not None:
            relations.append({"id": f"action-result-{action['id']}-{result['id']}", "kind": "action_result", "from_id": action["id"], "to_id": result["id"], "sequence": result["sequence"], "state": KNOWN})

    return {
        "turns": turns,
        "responses": responses,
        "actions": actions,
        "results": results,
        "file_changes": file_changes,
        "relations": relations,
        "events": events,
        "parse_damage": bool(parse_diagnostics),
    }


def _metric(
    state: str,
    value: Any = None,
    *,
    reason: str | None = None,
    locators: Sequence[Mapping[str, Any]] = (),
    eligible_count: int = 0,
    partial: Any = None,
) -> dict[str, Any]:
    item = _fact(state, value, reason=reason, locators=locators, partial=partial)
    item["eligible_count"] = eligible_count
    return item


def _metric_facts(decoded: Mapping[str, Any], *, invalid_boundary: bool, complete_native_boundary: bool = True) -> dict[str, dict[str, Any]]:
    turns = decoded["turns"]
    responses = decoded["responses"]
    actions = decoded["actions"]
    results = decoded["results"]
    changes = decoded["file_changes"]
    relations = decoded["relations"]
    parse_damage = bool(decoded["parse_damage"] or invalid_boundary)
    action_result_relations = [item for item in relations if item.get("kind") == "action_result"]
    turn_response_relations = [item for item in relations if item.get("kind") == "turn_response"]
    r1 = [item for item in turns if item.get("revision") == "r1"]
    r2 = [item for item in turns if item.get("revision") == "r2"]
    all_action_fields_known = all(
        all(item.get("fields", {}).get(name, {}).get("state") == KNOWN for name in ("name", "arguments", "target", "turn"))
        for item in actions
    )
    all_change_fields_known = all(
        all(item.get("fields", {}).get(name, {}).get("state") == KNOWN for name in ("path", "before_sha256", "after_sha256"))
        for item in changes
    )

    def no_record_state(reason: str) -> str:
        return UNKNOWN if parse_damage or not complete_native_boundary else ABSENT

    metrics: dict[str, dict[str, Any]] = {}
    metrics["work.submitted_turns"] = _metric(
        UNKNOWN if parse_damage else (KNOWN if turns and all(item.get("query_complete") and item.get("revision") for item in turns) else UNKNOWN if turns else ABSENT),
        turns,
        reason="native record damage prevents a complete turn population" if parse_damage else None,
        locators=[item["locator"] for item in turns],
        eligible_count=len(turns),
    )
    response_state = UNKNOWN if parse_damage else (KNOWN if responses and all(item.get("turn_id") and item.get("response_canary") for item in responses) else UNKNOWN if responses or any(record.get("role") == "assistant" for record in decoded.get("records", ())) else ABSENT)
    metrics["work.visible_responses"] = _metric(response_state, responses, reason="assistant response records are present but a complete response boundary cannot be established" if response_state == UNKNOWN else None, locators=[item["locator"] for item in responses], eligible_count=len(responses))
    action_state = UNKNOWN if parse_damage else (KNOWN if actions and all_action_fields_known else UNKNOWN if actions else ABSENT)
    metrics["work.actions"] = _metric(action_state, actions, reason="one or more native action fields are absent" if action_state == UNKNOWN else None, locators=[item["locator"] for item in actions], eligible_count=len(actions), partial=actions if action_state == UNKNOWN else None)
    result_state = no_record_state("no native tool-result records were retained") if not results else UNKNOWN if parse_damage or any(item.get("call_id") is None for item in results) else KNOWN
    metrics["work.results"] = _metric(result_state, results, reason="no native tool-result records were retained" if not results else "one or more tool results lack stable call identity", locators=[item["locator"] for item in results], eligible_count=len(results))
    change_state = UNKNOWN if parse_damage else (KNOWN if changes and all_change_fields_known else UNKNOWN if changes else ABSENT)
    metrics["work.changed_files"] = _metric(change_state, changes, reason="before/after file hashes are not both retained" if changes and not all_change_fields_known else None, locators=[item["locator"] for item in changes], eligible_count=len(changes), partial=changes if change_state == UNKNOWN else None)
    metrics["causal.action_result"] = _metric(no_record_state("no native action-result relations were retained") if not action_result_relations else KNOWN if not parse_damage else UNKNOWN, action_result_relations, reason="native tool-result records are absent" if not action_result_relations else None, locators=[item["locator"] for item in action_result_relations], eligible_count=len(action_result_relations))
    metrics["causal.turn_response"] = _metric(UNKNOWN if parse_damage else (KNOWN if turn_response_relations and len(turn_response_relations) == len(responses) else UNKNOWN if responses else ABSENT), turn_response_relations, reason="one or more visible responses cannot be joined to a preceding user turn" if responses and len(turn_response_relations) != len(responses) else None, locators=[item["sequence"] and next(response["locator"] for response in responses if response["id"] == item["to_id"]) for item in turn_response_relations], eligible_count=len(turn_response_relations))
    metrics["revision.r1"] = _metric(UNKNOWN if parse_damage else (KNOWN if len(r1) == 1 and r1[0].get("query_complete") else ABSENT if not r1 else UNKNOWN), r1, reason="R1 is absent from the native transcript" if not r1 else None, locators=[item["locator"] for item in r1], eligible_count=len(r1))
    metrics["revision.r2"] = _metric(UNKNOWN if parse_damage else (KNOWN if len(r2) == 1 and r2[0].get("query_complete") else ABSENT if not r2 else UNKNOWN), r2, reason="R2 is absent from the native transcript" if not r2 else None, locators=[item["locator"] for item in r2], eligible_count=len(r2))
    order_known = len(r1) == 1 and len(r2) == 1 and r1[0]["sequence"] < r2[0]["sequence"] and "supersedes" in r2[0].get("query", "").lower()
    metrics["revision.r1_r2_order"] = _metric(UNKNOWN if parse_damage else (KNOWN if order_known else UNKNOWN if turns else ABSENT), {"r1_before_r2": order_known}, reason="revision order or superseding scope is not established" if not order_known else None, locators=[item["locator"] for item in (*r1, *r2)], eligible_count=1 if turns else 0)
    downstream = bool(r2 and changes and responses and all(item["sequence"] > r2[0]["sequence"] for item in (*changes, *responses)))
    metrics["revision.final_after_r2"] = _metric(UNKNOWN if parse_damage or not results else KNOWN if downstream else UNKNOWN, {"r2": r2, "changes": changes, "results": results, "responses": responses}, reason="final result is absent, so downstream completion cannot be proven" if not results else None, locators=[item["locator"] for item in (*changes, *results, *responses)], eligible_count=1 if turns else 0)
    model_fact = decoded["facts"]["model"]
    config_fact = decoded["facts"]["configuration"]
    usage_fact = decoded["facts"]["usage"]
    token_fact = decoded["facts"]["token_semantics"]
    metrics["attribution.model_config"] = _metric(UNKNOWN if parse_damage else model_fact["state"] if model_fact["state"] != KNOWN else config_fact["state"] if config_fact["state"] != KNOWN else KNOWN, {"model": model_fact, "configuration": config_fact}, reason="native model/configuration fields are absent" if model_fact["state"] == ABSENT or config_fact["state"] == ABSENT else None, locators=model_fact.get("locators", []) + config_fact.get("locators", []), eligible_count=len(responses))
    metrics["attribution.usage"] = _metric(UNKNOWN if parse_damage else usage_fact["state"], usage_fact, reason="native response usage records are absent" if usage_fact["state"] == ABSENT else None, locators=usage_fact.get("locators", []), eligible_count=len(responses))
    metrics["attribution.token_semantics"] = _metric(UNKNOWN if parse_damage else token_fact["state"], token_fact, reason="native token/cache semantics are absent" if token_fact["state"] == ABSENT else None, locators=token_fact.get("locators", []), eligible_count=len(responses))
    metrics["attribution.reconciliation"] = _metric(UNKNOWN, {"usage": usage_fact, "session_total": decoded["facts"]["session_total_usage"]}, reason="numeric reconciliation of response usage against a native session total has not been established", locators=usage_fact.get("locators", []), eligible_count=1)
    metrics["portable.complete_root"] = _metric(UNKNOWN, decoded["facts"]["bundle_integrity"], reason="the copied public derivative has no complete-root inventory manifest", locators=[], eligible_count=1)
    metrics["portable.companions"] = _metric(UNKNOWN, decoded["facts"]["companions"], reason="required Cursor sidecar coverage is not declared in the copied derivative", locators=[], eligible_count=1)
    metrics["portable.isolated_decode"] = _metric(KNOWN if not parse_damage else UNKNOWN, {"copy_only": True}, reason="decode completed from the supplied copied bundle" if not parse_damage else "copy contains malformed native records", locators=[], eligible_count=1)
    metrics["portable.canonical_equality"] = _metric(UNKNOWN, None, reason="ordinary-root comparison is intentionally outside copied-bundle decoding", locators=[], eligible_count=1)
    if not complete_native_boundary:
        for metric in metrics.values():
            if metric["state"] == ABSENT:
                metric["state"] = UNKNOWN
                metric["reason"] = "not found in supplied transcript; Desktop companion closure is incomplete"
    return metrics


def _build_result(
    root: Path,
    transcript: Path,
    *,
    format_version: str = FORMAT_VERSION,
    decoder_contract_version: str = DECODER_CONTRACT_VERSION,
    complete_native_boundary: bool = True,
) -> CursorDecodeResult:
    records, diagnostics = _read_records(transcript)
    receipt_findings, receipt_ok = _receipt_diagnostics(root, transcript, len(records))
    semantic_diagnostics = diagnostics + [item for item in receipt_findings if item["code"] != "receipt_missing"]
    diagnostics.extend(receipt_findings)
    decoded = _decode_semantics(records, parse_diagnostics=semantic_diagnostics)
    text_values: list[tuple[str, str]] = []
    model_values: list[str] = []
    configuration_values: list[str] = []
    usage_values: list[Mapping[str, Any]] = []
    session_total_values: list[Mapping[str, Any]] = []
    token_values: list[Mapping[str, Any]] = []
    for record in records:
        for block in record.get("content", []):
            if not isinstance(block, Mapping):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_values.append((block["text"], record["locator"]))
            if isinstance(block.get("model"), str):
                model_values.append(block["model"])
            if isinstance(block.get("configuration"), str):
                configuration_values.append(block["configuration"])
            if isinstance(block.get("usage"), Mapping):
                usage_values.append(dict(block["usage"]))
                token_values.append(dict(block["usage"]))
            if block.get("type") in {"usage_total", "session_usage"} and isinstance(block.get("value"), Mapping):
                session_total_values.append(dict(block["value"]))
    all_locators = [item["locator"] for item in records]
    native_session_ids = [item.get("session_id") for item in records if isinstance(item.get("session_id"), str)]
    session_state = KNOWN if native_session_ids and len(set(native_session_ids)) == 1 else UNKNOWN if len(set(native_session_ids)) > 1 else ABSENT
    model_state = KNOWN if model_values and len(set(model_values)) == 1 else UNKNOWN if len(set(model_values)) > 1 else ABSENT
    config_state = KNOWN if configuration_values and len(set(configuration_values)) == 1 else UNKNOWN if len(set(configuration_values)) > 1 else ABSENT
    usage_state = KNOWN if usage_values else ABSENT
    token_state = KNOWN if token_values else ABSENT
    session_total_state = KNOWN if session_total_values and len(session_total_values) == 1 else UNKNOWN if len(session_total_values) > 1 else ABSENT
    facts = {
        "session_id": _fact(session_state, native_session_ids[0] if session_state == KNOWN else None, reason="native Cursor JSONL derivative has no session identifier" if session_state == ABSENT else "multiple native session identifiers were observed" if session_state == UNKNOWN else None, locators=all_locators),
        "model": _fact(model_state, model_values[0] if model_state == KNOWN else None, reason="native model identity is absent" if model_state == ABSENT else "conflicting model identities were observed" if model_state == UNKNOWN else None, locators=all_locators),
        "configuration": _fact(config_state, configuration_values[0] if config_state == KNOWN else None, reason="native configuration identity is absent" if config_state == ABSENT else "conflicting configuration identities were observed" if config_state == UNKNOWN else None, locators=all_locators),
        "usage": _fact(usage_state, usage_values if usage_state == KNOWN else None, reason="native response usage records are absent" if usage_state == ABSENT else None, locators=all_locators),
        "token_semantics": _fact(token_state, token_values if token_state == KNOWN else None, reason="native token/cache fields are absent" if token_state == ABSENT else None, locators=all_locators),
        "session_total_usage": _fact(session_total_state, session_total_values[0] if session_total_state == KNOWN else None, reason="native session usage total is absent" if session_total_state == ABSENT else "multiple session usage totals were observed" if session_total_state == UNKNOWN else None, locators=all_locators),
        "native_format_version": _fact(ABSENT, reason="Cursor JSONL derivative declares no native schema version", locators=all_locators),
        "bundle_integrity": _fact(KNOWN if receipt_ok else UNKNOWN, {"receipt_present": (root / RECEIPT_NAME).exists(), "receipt_verified": receipt_ok}, reason="sanitization receipt is missing or does not match copied transcript" if not receipt_ok else None, locators=(), partial={"receipt_present": (root / RECEIPT_NAME).exists(), "receipt_verified": receipt_ok} if not receipt_ok else None),
        "companions": _fact(UNKNOWN, None, reason="copied public derivative does not declare required sidecars", locators=()),
        "copy_only": _fact(KNOWN, True),
        "canonical_equality": _fact(UNKNOWN, None, reason="ordinary-root comparison is outside the decoder boundary", locators=()),
    }
    if not complete_native_boundary:
        for fact in facts.values():
            if fact["state"] == ABSENT:
                fact["state"] = UNKNOWN
                fact["reason"] = "not found in supplied transcript; Desktop companion closure is incomplete"
    decoded["records"] = [dict(item) for item in records]
    decoded["facts"] = facts
    receipt_corrupt = any(item["code"] != "receipt_missing" for item in receipt_findings)
    decoded["metrics"] = _metric_facts(decoded, invalid_boundary=receipt_corrupt, complete_native_boundary=complete_native_boundary)
    decoded["diagnostics"] = [dict(item) for item in diagnostics]
    decoded["format"] = format_version
    decoded["decoder_contract_version"] = decoder_contract_version
    decoded["copy_only"] = True
    decoded["source"] = {"artifact": TRANSCRIPT_NAME, "sha256": _sha256_file(transcript), "size_bytes": transcript.stat().st_size}
    decoded["semantic_sha256"] = _sha256_bytes(_canonical_bytes({key: value for key, value in decoded.items() if key != "semantic_sha256"}))
    return CursorDecodeResult(decoded)


def _merge_desktop_companions(result: CursorDecodeResult, root: Path) -> CursorDecodeResult:
    """Add only session-indexed facts from a copied, sanitized SQLite row export.

    The composer header is the native ordering/index authority. Each tool bubble
    contains its request and result under one stable bubble ID, so no positional
    action/result guess from the separate transcript is needed.
    """

    path = root / DESKTOP_COMPANION_NAME
    if not path.exists():
        return result
    if path.stat().st_size > 8 * 1024 * 1024:
        raise CursorBundleError("Desktop companion exceeds the decoder byte bound")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line_number, raw in enumerate(path.read_bytes().splitlines(), 1):
        if line_number > 500 or len(raw) > MAX_RECORD_BYTES:
            raise CursorBundleError("Desktop companion exceeds the record bound")
        row = _strict_json(raw, label=f"companion line {line_number}")
        if not isinstance(row, dict) or set(row) != {"store", "key", "value"}:
            raise CursorBundleError("Desktop companion row shape is invalid")
        if row["store"] not in {"global.cursorDiskKV", "workspace.ItemTable"} or not isinstance(row["key"], str) or not isinstance(row["value"], str):
            raise CursorBundleError("Desktop companion row type is invalid")
        key = (row["store"], row["key"])
        if key in rows:
            raise CursorBundleError("Desktop companion contains duplicate keys")
        if row["store"] == "global.cursorDiskKV" and row["key"].startswith(("composerData:", "bubbleId:", "checkpointId:")):
            value = _strict_json(row["value"].encode(), label=f"companion value {line_number}")
            if not isinstance(value, dict):
                raise CursorBundleError("Desktop companion value is not an object")
        else:
            value = row["value"]
        rows[key] = {"value": value, "locator": {"artifact": DESKTOP_COMPANION_NAME, "line": line_number, "record_sha256": _sha256_bytes(raw)}}
    composer_key = ("global.cursorDiskKV", "composerData:$SESSION_ID")
    composer_row = rows.get(composer_key)
    if composer_row is None:
        raise CursorBundleError("Desktop companion lacks session composer index")
    composer = composer_row["value"]
    headers = composer.get("fullConversationHeadersOnly")
    if not isinstance(headers, list) or not headers:
        raise CursorBundleError("Desktop companion header index is missing")
    ids = [header.get("bubbleId") for header in headers if isinstance(header, dict)]
    if len(ids) != len(headers) or len(set(ids)) != len(ids) or not all(isinstance(value, str) and value for value in ids):
        raise CursorBundleError("Desktop companion header IDs are invalid")
    expected = {("global.cursorDiskKV", f"bubbleId:$SESSION_ID:{bubble_id}") for bubble_id in ids}
    actual = {key for key in rows if key[0] == "global.cursorDiskKV" and key[1].startswith("bubbleId:$SESSION_ID:")}
    if expected != actual:
        raise CursorBundleError("Desktop companion bubble index does not close")
    checkpoint_ids: set[str] = set()
    actions: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = [dict(item) for item in result.relations if item.get("kind") != "action_result"]
    model_names: list[str] = []
    companion_response_canaries: list[str] = []
    turn_ordinal = 0
    version_values: set[str] = set()
    for sequence, bubble_id in enumerate(ids, 1):
        entry = rows[("global.cursorDiskKV", f"bubbleId:$SESSION_ID:{bubble_id}")]
        bubble = entry["value"]
        locator = entry["locator"]
        if bubble.get("bubbleId") != bubble_id:
            raise CursorBundleError("Desktop companion bubble ID conflicts with index")
        if bubble.get("_v") is not None:
            version_values.add(str(bubble["_v"]))
        if bubble.get("checkpointId"):
            checkpoint_ids.add(str(bubble["checkpointId"]))
        if bubble.get("type") == 1:
            turn_ordinal += 1
            info = bubble.get("modelInfo")
            if isinstance(info, dict) and isinstance(info.get("modelName"), str):
                model_names.append(info["modelName"])
        elif bubble.get("type") == 2 and isinstance(bubble.get("text"), str):
            marker = _marker(_RESPONSE_MARKER, bubble["text"])
            if marker:
                companion_response_canaries.append(marker)
        tool = bubble.get("toolFormerData")
        if not isinstance(tool, dict):
            continue
        try:
            params = _strict_json(tool.get("params", "{}").encode(), label="Desktop tool params")
            output_value = _strict_json(tool.get("result", "{}").encode(), label="Desktop tool result")
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CursorBundleError("Desktop tool request/result is malformed") from exc
        if not isinstance(params, dict) or not isinstance(output_value, dict):
            raise CursorBundleError("Desktop tool request/result is not an object")
        tool_name = tool.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            raise CursorBundleError("Desktop tool name is missing")
        target = next((params.get(key) for key in ("relativeWorkspacePath", "targetFile", "targetDirectory", "cwd") if isinstance(params.get(key), str)), None)
        action_id = f"bubble-action-{bubble_id}"
        result_id = f"bubble-result-{bubble_id}"
        turn_id = f"turn-{turn_ordinal}" if turn_ordinal else None
        call_id = tool.get("toolCallId") if isinstance(tool.get("toolCallId"), str) else None
        actions.append({"id": action_id, "sequence": sequence, "turn_id": turn_id, "tool_name": tool_name, "action_kind": _action_kind(tool_name), "input": params, "target": target, "call_id": call_id, "locator": locator, "fields": {"name": _fact(KNOWN, tool_name, locators=(locator,)), "arguments": _fact(KNOWN, params, locators=(locator,)), "target": _fact(KNOWN if target else ABSENT, target, locators=(locator,)), "turn": _fact(KNOWN if turn_id else UNKNOWN, turn_id, locators=(locator,)), "call_id": _fact(KNOWN if call_id else ABSENT, call_id, locators=(locator,))}})
        status = tool.get("status") if isinstance(tool.get("status"), str) else None
        exit_code = output_value.get("exitCode") if type(output_value.get("exitCode")) is int else None
        output = output_value.get("output") if isinstance(output_value.get("output"), str) else None
        results.append({"id": result_id, "sequence": sequence, "turn_id": turn_id, "call_id": call_id, "status": status, "exit_code": exit_code, "output": output, "value": output_value, "locator": locator})
        relations.append({"id": f"bubble-action-result-{bubble_id}", "kind": "action_result", "from_id": action_id, "to_id": result_id, "sequence": sequence, "state": KNOWN, "locator": locator})
    checkpoint_keys = {("global.cursorDiskKV", f"checkpointId:$SESSION_ID:{checkpoint_id}") for checkpoint_id in checkpoint_ids}
    if not checkpoint_keys.issubset(rows):
        raise CursorBundleError("Desktop companion checkpoint closure is incomplete")
    if turn_ordinal != len(result.turns):
        raise CursorBundleError("Desktop companion user-turn count conflicts with transcript")
    if companion_response_canaries != [item["response_canary"] for item in result.responses]:
        raise CursorBundleError("Desktop companion response boundaries conflict with transcript")
    if len(actions) != len(result.actions):
        raise CursorBundleError("Desktop companion action count conflicts with transcript")
    config = composer.get("modelConfig")
    if isinstance(config, dict) and isinstance(config.get("modelName"), str):
        model_names.append(config["modelName"])
    data = result.as_dict()
    data["actions"] = actions
    data["results"] = results
    data["relations"] = relations
    data["desktop_companion"] = {"artifact": DESKTOP_COMPANION_NAME, "sha256": _sha256_file(path), "rows": len(rows), "bubble_index_count": len(ids), "workspace_row_count": sum(key[0] == "workspace.ItemTable" for key in rows)}
    model_state = KNOWN if model_names and len(set(model_names)) == 1 else UNKNOWN
    data["facts"]["model"] = _fact(model_state, model_names[0] if model_state == KNOWN else None, reason="conflicting or missing model identities in Desktop companion" if model_state == UNKNOWN else None, locators=(composer_row["locator"],))
    data["facts"]["configuration"] = _fact(KNOWN if isinstance(config, dict) else UNKNOWN, config if isinstance(config, dict) else None, locators=(composer_row["locator"],))
    data["facts"]["native_format_version"] = _fact(KNOWN if len(version_values) == 1 else UNKNOWN, next(iter(version_values)) if len(version_values) == 1 else None, locators=(composer_row["locator"],))
    data["facts"]["companions"] = _fact(KNOWN, {"indexed_bubbles": len(ids), "selected_rows": len(rows)}, locators=(composer_row["locator"],))
    data["metrics"] = _metric_facts(data, invalid_boundary=bool(data["parse_damage"] or data["facts"]["bundle_integrity"]["state"] != KNOWN), complete_native_boundary=False)
    data["metrics"]["portable.companions"] = _metric(KNOWN, data["facts"]["companions"], locators=(composer_row["locator"],), eligible_count=1)
    data["semantic_sha256"] = _sha256_bytes(_canonical_bytes({key: value for key, value in data.items() if key != "semantic_sha256"}))
    return CursorDecodeResult(data)


def decode_cursor_bundle(bundle: str | Path) -> CursorDecodeResult:
    """Decode one explicit copied public Cursor bundle.

    The accepted directory must contain ``cursor-session.jsonl`` at its root
    and may contain only the matching sanitization receipt.  This strict shape
    is the isolation control: passing the retained attempt root, a Cursor
    profile, or a directory containing observer/project/config siblings fails
    before any transcript bytes are read.
    """

    root, transcript = _validate_bundle_root(bundle)
    return _build_result(root, transcript)


def decode_cursor_desktop_bundle(bundle: str | Path) -> CursorDecodeResult:
    """Decode one copied Cursor Desktop agent transcript.

    The observed Desktop transcript uses the same JSONL record shape as the
    Cursor CLI derivative. This entry point keeps the surface identity separate.
    It does not infer tool results or completeness from the live UI or stores
    outside the supplied flat bundle.
    """

    root, transcript = _validate_bundle_root(bundle, desktop=True)
    transcript_result = _build_result(
        root,
        transcript,
        format_version=DESKTOP_FORMAT_VERSION,
        decoder_contract_version=DESKTOP_DECODER_CONTRACT_VERSION,
        complete_native_boundary=False,
    )
    return _merge_desktop_companions(transcript_result, root)


def decode_cursor_transcript(transcript: str | Path) -> CursorDecodeResult:
    """Decode a transcript file without discovering sibling roots.

    This helper is useful for a caller that has already copied exactly one
    transcript.  The parent must be a flat copied package containing only the
    transcript and optional receipt, just like :func:`decode_cursor_bundle`.
    """

    parent, path = _validate_transcript_file(Path(transcript).expanduser())
    # Reuse the same closed-package check so a direct path cannot smuggle in a
    # normal profile or an unbounded surrounding directory.
    root, validated = _validate_bundle_root(parent)
    if validated != path:
        raise CursorBundleError("transcript path is not the copied bundle's declared transcript")
    return _build_result(root, validated)


# Naming aliases keep the decoder easy to discover from the existing native
# decoder vocabulary while retaining an explicit Cursor-specific entry point.
decode_cursor_cli = decode_cursor_bundle
decode_cursor_cli_native = decode_cursor_bundle
decode_cursor_native = decode_cursor_transcript
decode_native = decode_cursor_bundle
CursorNativeDecoder = CursorCliNativeDecoder


__all__ = [
    "ABSENT",
    "DECODER_CONTRACT_VERSION",
    "DESKTOP_DECODER_CONTRACT_VERSION",
    "DESKTOP_FORMAT_VERSION",
    "FORMAT_VERSION",
    "FactState",
    "KNOWN",
    "MAX_RECORDS",
    "SURVIVAL_METRIC_IDS",
    "UNKNOWN",
    "CursorBundleError",
    "CursorCliNativeDecoder",
    "CursorDesktopTranscriptDecoder",
    "CursorDecodeResult",
    "CursorDecoderError",
    "CursorNativeDecoder",
    "CursorTranscriptError",
    "decode_cursor_bundle",
    "decode_cursor_desktop_bundle",
    "decode_cursor_cli",
    "decode_cursor_cli_native",
    "decode_cursor_native",
    "decode_cursor_transcript",
    "decode_native",
]
