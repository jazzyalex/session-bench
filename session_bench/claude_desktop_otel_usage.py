"""Loopback-only, content-minimizing Claude Code OTel usage capture.

Only the documented api_request and assistant_response log attributes are
retained. API errors and exhausted retries invalidate the capture without
retaining their content. Prompt text and response text are processed in memory;
response text is reduced to a digest and matches against the run's synthetic
response canaries, then discarded. Resource identity and unrelated attributes
are ignored.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any, Mapping


RECEIPT_SCHEMA = "session-bench-claude-desktop-otel-usage-v1"
MAX_OTLP_BODY_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 20_000
_RUN_ID = re.compile(r"[A-Za-z0-9_-]+\Z")
_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_ATTRIBUTES = frozenset({
    "event.name", "event.timestamp", "event.sequence", "session.id",
    "prompt.id", "request_id", "client_request_id", "model", "query_source",
    "effort", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_creation_tokens", "success", "response", "response_length",
    "message.uuid",
})
_USAGE_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_tokens",
    "cache_creation_tokens": "cache_write_tokens",
}


class ClaudeDesktopOtelUsageError(ValueError):
    """The OTel data is malformed or cannot support a closed usage join."""


def _strict_json(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ClaudeDesktopOtelUsageError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ClaudeDesktopOtelUsageError(f"non-finite JSON value {value}")
            ),
        )
    except ClaudeDesktopOtelUsageError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaudeDesktopOtelUsageError("OTLP body is not valid JSON") from exc


def read_otlp_http_body(
    stream: Any, *, content_length: str | None, transfer_encoding: str | None,
) -> bytes:
    """Read a bounded OTLP/HTTP body with Content-Length or chunked framing.

    Claude Code releases before v2.1.212 used chunked transfer encoding for
    HTTP exporters. Supporting it keeps the loopback receiver compatible
    without logging request framing or body content.
    """
    if transfer_encoding is not None:
        if content_length is not None or transfer_encoding.strip().lower() != "chunked":
            raise ClaudeDesktopOtelUsageError("unsupported or ambiguous HTTP transfer framing")
        chunks: list[bytes] = []
        total = 0
        chunk_count = 0
        while True:
            size_line = stream.readline(64)
            if not size_line.endswith(b"\r\n") or len(size_line) > 63:
                raise ClaudeDesktopOtelUsageError("malformed HTTP chunk size")
            size_text = size_line[:-2]
            if not re.fullmatch(rb"(?:0|[1-9A-Fa-f][0-9A-Fa-f]*)", size_text):
                raise ClaudeDesktopOtelUsageError("malformed HTTP chunk size")
            size = int(size_text, 16)
            if size == 0:
                trailer_bytes = 0
                while True:
                    line = stream.readline(8193)
                    trailer_bytes += len(line)
                    if (not line.endswith(b"\r\n") or len(line) > 8192
                            or trailer_bytes > 65_536):
                        raise ClaudeDesktopOtelUsageError("malformed HTTP chunk trailers")
                    if line == b"\r\n":
                        break
                    if b":" not in line[:-2] or b"\x00" in line:
                        raise ClaudeDesktopOtelUsageError("malformed HTTP chunk trailers")
                body = b"".join(chunks)
                if not body:
                    raise ClaudeDesktopOtelUsageError("OTLP body is empty")
                return body
            chunk_count += 1
            if chunk_count > MAX_EVENTS or total + size > MAX_OTLP_BODY_BYTES:
                raise ClaudeDesktopOtelUsageError("chunked OTLP body is oversized")
            chunk = stream.read(size)
            if len(chunk) != size or stream.read(2) != b"\r\n":
                raise ClaudeDesktopOtelUsageError("short or malformed HTTP chunk")
            chunks.append(chunk)
            total += size
    if content_length is None or not re.fullmatch(r"\d+", content_length.strip()):
        raise ClaudeDesktopOtelUsageError("HTTP Content-Length is missing or invalid")
    length = int(content_length)
    if length <= 0 or length > MAX_OTLP_BODY_BYTES:
        raise ClaudeDesktopOtelUsageError("OTLP body is empty or oversized")
    raw = stream.read(length)
    if len(raw) != length:
        raise ClaudeDesktopOtelUsageError("short OTLP request body")
    return raw


def _integer(value: Any, label: str, *, allow_missing: bool = False) -> int | None:
    if value is None and allow_missing:
        return None
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        return int(value)
    raise ClaudeDesktopOtelUsageError(f"{label} must be a non-negative integer")


def _any_value(value: Any) -> Any:
    if not isinstance(value, Mapping) or len(value) != 1:
        return None
    key, item = next(iter(value.items()))
    if key == "stringValue" and isinstance(item, str):
        return item
    if key == "boolValue" and type(item) is bool:
        return item
    if key == "intValue":
        return _integer(item, "OTLP intValue")
    return None


def _selected_attributes(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if key not in _ALLOWED_ATTRIBUTES:
            continue
        if key in result:
            raise ClaudeDesktopOtelUsageError(f"duplicate OTel attribute {key!r}")
        result[key] = _any_value(item.get("value"))
    return result


def _routing_attributes(value: Any) -> dict[str, Any]:
    """Read only fields needed to decide whether a record may be inspected."""
    if not isinstance(value, list):
        return {}
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        key = item.get("key")
        if key not in {"event.name", "session.id"}:
            continue
        if key in result:
            raise ClaudeDesktopOtelUsageError(f"duplicate OTel attribute {key!r}")
        result[key] = _any_value(item.get("value"))
    return result


def _event_session_id(
    attrs: Mapping[str, Any], resource_attrs: Mapping[str, Any],
) -> str:
    """Resolve one unambiguous, syntactically valid session identity."""
    identities: list[str] = []
    for source in (resource_attrs, attrs):
        if "session.id" not in source:
            continue
        value = source["session.id"]
        if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
            raise ClaudeDesktopOtelUsageError("OTel event has malformed session.id")
        identities.append(value)
    if not identities:
        raise ClaudeDesktopOtelUsageError("OTel event has no session.id")
    if len(set(identities)) != 1:
        raise ClaudeDesktopOtelUsageError("OTel event has conflicting session.id")
    return identities[0]


def read_private_session_id_file(
    path: Path, *, missing_ok: bool = False,
) -> str | None:
    """Read an exact-session allowlist from a private, caller-created file."""
    path = Path(path)
    if not path.is_absolute():
        raise ClaudeDesktopOtelUsageError("session-id file path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ClaudeDesktopOtelUsageError(
            "session-id file must be an existing private ordinary file"
        ) from None
    except OSError as exc:
        raise ClaudeDesktopOtelUsageError(
            "session-id file must be an existing private ordinary file"
        ) from exc
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) & 0o077):
            raise ClaudeDesktopOtelUsageError(
                "session-id file must be an existing private ordinary file"
            )
        raw = os.read(fd, 1025)
        if len(raw) > 1024:
            raise ClaudeDesktopOtelUsageError("session-id file is malformed")
    finally:
        os.close(fd)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaudeDesktopOtelUsageError("session-id file is malformed") from exc
    session_id = text[:-1] if text.endswith("\n") else text
    if not _SESSION_ID.fullmatch(session_id):
        raise ClaudeDesktopOtelUsageError("session-id file is malformed")
    return session_id


class PrivateSessionIdAllowlist:
    """Latch one private session ID, then reject disappearance or mutation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ClaudeDesktopOtelUsageError(
                "session-id file path must be absolute"
            )
        self._latched_session_id: str | None = None
        self._lock = threading.RLock()

    @property
    def latched_session_id(self) -> str | None:
        return self._latched_session_id

    def refresh(self) -> str | None:
        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> str | None:
        if self._latched_session_id is None:
            try:
                candidate = read_private_session_id_file(
                    self.path, missing_ok=True,
                )
            except ClaudeDesktopOtelUsageError:
                # Before arming, an absent or not-yet-private file carries no
                # authority. Keep dropping exports until a valid file exists.
                return None
            if candidate is None:
                return None
            self._latched_session_id = candidate
            return candidate

        candidate = read_private_session_id_file(self.path)
        if candidate != self._latched_session_id:
            raise ClaudeDesktopOtelUsageError(
                "session-id file changed after exact-session allowlist was armed"
            )
        return self._latched_session_id


def _canonical_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ClaudeDesktopOtelUsageError("OTel event timestamp is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ClaudeDesktopOtelUsageError("OTel event timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClaudeDesktopOtelUsageError("OTel event timestamp has no time zone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    suffix = value.rsplit(".", 1)[-1]
    return suffix if suffix in {
        "api_request", "assistant_response", "api_error", "api_retries_exhausted",
    } else None


def _response_digest_and_canaries(
    response: Any, response_canaries: tuple[str, str],
) -> tuple[str | None, list[str]]:
    if not isinstance(response, str) or response == "<REDACTED>" or "[TRUNCATED" in response:
        return None, []
    digest = hashlib.sha256(response.encode("utf-8")).hexdigest()
    matched = [canary for canary in response_canaries if canary in response]
    return digest, matched


def _utf16_code_unit_length(value: str) -> int:
    """Count the UTF-16 units used by JavaScript string length APIs."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def _safe_invalid_reason_code(exc: BaseException) -> str:
    """Reduce parser errors to bounded codes; never log exported values."""
    message = str(exc)
    exact = {
        "OTLP body is not valid JSON": "invalid_json",
        "OTLP body must be an object": "root_not_object",
        "OTLP body has no resourceLogs array": "missing_resource_logs",
        "resourceLogs entry is malformed": "malformed_resource_logs_entry",
        "OTel event has no session.id": "missing_session_id",
        "OTel event has malformed session.id": "malformed_session_id",
        "OTel event has conflicting session.id": "conflicting_session_id",
        "OTel event has no prompt.id": "missing_prompt_id",
        "OTel event has no request_id": "missing_request_id",
        "OTel event has no model": "missing_model",
        "OTel event timestamp is missing": "missing_event_timestamp",
        "OTel event timestamp is invalid": "invalid_event_timestamp",
        "OTel event timestamp has no time zone": "timezone_missing_from_event_timestamp",
        "assistant response text is not a string": "assistant_response_not_string",
        "assistant response length is missing": "assistant_response_length_missing",
        "assistant response is redacted or truncated": "assistant_response_redacted_or_truncated",
        "assistant response length is inconsistent": "assistant_response_length_mismatch",
        "OTel event population is oversized": "oversized_event_population",
        "OTel API error event makes usage coverage incomplete": "api_error_incomplete_coverage",
        "OTel API retries exhausted event makes usage coverage incomplete": "api_retries_exhausted_incomplete_coverage",
        "OTLP intValue must be a non-negative integer": "invalid_otlp_integer_value",
    }
    if message in exact:
        return exact[message]
    if message.startswith("duplicate JSON key"):
        return "duplicate_json_key"
    if message.startswith("duplicate OTel attribute"):
        return "duplicate_otel_attribute"
    if message.startswith("non-finite JSON value"):
        return "non_finite_json_value"
    if message.startswith("event.sequence must"):
        return "invalid_event_sequence"
    if message.startswith("input_tokens must"):
        return "invalid_input_tokens"
    if message.startswith("output_tokens must"):
        return "invalid_output_tokens"
    if message.startswith("cache_read_tokens must"):
        return "invalid_cache_read_tokens"
    if message.startswith("cache_creation_tokens must"):
        return "invalid_cache_creation_tokens"
    if message.startswith("response_length must"):
        return "invalid_response_length"
    if message.startswith("OTLP body"):
        return "invalid_otlp_body_shape"
    if message.startswith("resourceLogs"):
        return "invalid_resource_logs_shape"
    if message.startswith("OTel event"):
        return "invalid_event_shape"
    return "invalid_otlp_schema"


class ClaudeDesktopOtelUsageCapture:
    """Collect a minimized receipt from OTLP/HTTP JSON log exports."""

    def __init__(
        self, *, run_id: str, response_canaries: tuple[str, str],
        expected_session_id: str | None,
    ) -> None:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ClaudeDesktopOtelUsageError("invalid run_id")
        if (not isinstance(response_canaries, tuple) or len(response_canaries) != 2
                or any(not isinstance(item, str) or not item for item in response_canaries)
                or len(set(response_canaries)) != 2):
            raise ClaudeDesktopOtelUsageError("exactly two distinct response canaries are required")
        if (expected_session_id is not None
                and (not isinstance(expected_session_id, str)
                     or not _SESSION_ID.fullmatch(expected_session_id))):
            raise ClaudeDesktopOtelUsageError("invalid expected session.id")
        self.run_id = run_id
        self.response_canaries = response_canaries
        self.expected_session_id = expected_session_id
        self.events: list[dict[str, Any]] = []
        self.invalid_reason: str | None = None
        self.invalid_category: str | None = None
        self.invalid_reason_code: str | None = None
        self._lock = threading.RLock()

    def arm_session_id(self, session_id: str) -> None:
        """Arm exactly once; a different later identity is a hard failure."""
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ClaudeDesktopOtelUsageError("invalid expected session.id")
        with self._lock:
            if self.expected_session_id is None:
                self.expected_session_id = session_id
            elif self.expected_session_id != session_id:
                raise ClaudeDesktopOtelUsageError(
                    "exact-session allowlist changed after capture was armed"
                )

    def ingest(self, raw: bytes) -> int:
        with self._lock:
            return self._ingest_locked(raw)

    def reject_export(self, category: str = "http_export_rejected") -> None:
        """Latch an HTTP export rejected before its body reached ``ingest``."""
        with self._lock:
            if self.invalid_reason is None:
                # Keep request framing details and any client-controlled values
                # out of the receipt lifecycle and diagnostics.
                self.invalid_reason = "HTTP export rejected before successful ingestion"
                self.invalid_category = category
                self.invalid_reason_code = category

    def _ingest_locked(self, raw: bytes) -> int:
        if self.invalid_reason is not None:
            raise ClaudeDesktopOtelUsageError("capture already contains a rejected OTLP export")
        if self.expected_session_id is None:
            raise ClaudeDesktopOtelUsageError(
                "exact-session allowlist is not armed"
            )
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_OTLP_BODY_BYTES:
            error = ClaudeDesktopOtelUsageError("OTLP body is empty or oversized")
            self.invalid_reason = str(error)
            self.invalid_category = "invalid_otlp_payload"
            self.invalid_reason_code = "empty_or_oversized_body"
            raise error
        try:
            payload = _strict_json(raw)
            if not isinstance(payload, Mapping):
                raise ClaudeDesktopOtelUsageError("OTLP body must be an object")
            resource_logs = payload.get("resourceLogs")
            if not isinstance(resource_logs, list):
                raise ClaudeDesktopOtelUsageError("OTLP body has no resourceLogs array")
            batch: list[dict[str, Any]] = []
            for resource_log in resource_logs:
                if not isinstance(resource_log, Mapping):
                    raise ClaudeDesktopOtelUsageError("resourceLogs entry is malformed")
                resource_routing_attrs = _routing_attributes(
                    (resource_log.get("resource") or {}).get("attributes")
                    if isinstance(resource_log.get("resource"), Mapping) else None
                )
                scope_logs = resource_log.get("scopeLogs")
                if not isinstance(scope_logs, list):
                    continue
                for scope_log in scope_logs:
                    if not isinstance(scope_log, Mapping):
                        continue
                    records = scope_log.get("logRecords")
                    if not isinstance(records, list):
                        continue
                    for record in records:
                        if not isinstance(record, Mapping):
                            continue
                        routing_attrs = _routing_attributes(record.get("attributes"))
                        name = _event_name(routing_attrs.get("event.name"))
                        if name is None:
                            continue
                        session_id = _event_session_id(
                            routing_attrs, resource_routing_attrs,
                        )
                        if session_id != self.expected_session_id:
                            # Do not select, retain, hash, or validate any
                            # prompt/response attributes for foreign sessions.
                            continue
                        attrs = _selected_attributes(record.get("attributes"))
                        resource_attrs = _selected_attributes(
                            (resource_log.get("resource") or {}).get("attributes")
                            if isinstance(resource_log.get("resource"), Mapping) else None
                        )
                        # The monitoring contract emits api_error only after
                        # failed retries are terminal. Neither failure event
                        # has complete usage, and request_id can be absent.
                        # Reject the entire batch before retaining attributes.
                        if name == "api_error":
                            raise ClaudeDesktopOtelUsageError(
                                "OTel API error event makes usage coverage incomplete"
                            )
                        if name == "api_retries_exhausted":
                            raise ClaudeDesktopOtelUsageError(
                                "OTel API retries exhausted event makes usage coverage incomplete"
                            )
                        batch.append(self._record(name, attrs, resource_attrs))
                        if len(self.events) + len(batch) > MAX_EVENTS:
                            raise ClaudeDesktopOtelUsageError("OTel event population is oversized")
            self.events.extend(batch)
            return len(batch)
        except Exception as exc:
            self.invalid_reason = f"{type(exc).__name__}: {exc}"
            self.invalid_category = "invalid_otlp_payload"
            self.invalid_reason_code = _safe_invalid_reason_code(exc)
            raise

    def _record(
        self, name: str, attrs: Mapping[str, Any],
        resource_attrs: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = _event_session_id(attrs, resource_attrs)
        prompt_id = attrs.get("prompt.id")
        request_id = attrs.get("request_id")
        model = attrs.get("model")
        if session_id != self.expected_session_id:
            raise ClaudeDesktopOtelUsageError("OTel event escaped exact-session filter")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ClaudeDesktopOtelUsageError("OTel event has no prompt.id")
        if not isinstance(request_id, str) or not request_id:
            raise ClaudeDesktopOtelUsageError("OTel event has no request_id")
        if not isinstance(model, str) or not model:
            raise ClaudeDesktopOtelUsageError("OTel event has no model")
        sequence = _integer(attrs.get("event.sequence"), "event.sequence")
        timestamp = _canonical_timestamp(attrs.get("event.timestamp"))
        row: dict[str, Any] = {
            "kind": name,
            "session_id": session_id,
            "prompt_id": prompt_id,
            "request_id": request_id,
            "model": model,
            "query_source": attrs.get("query_source") if isinstance(attrs.get("query_source"), str) else None,
            "effort": attrs.get("effort") if isinstance(attrs.get("effort"), str) else None,
            "event_sequence": sequence,
            "event_timestamp": timestamp,
        }
        if name == "api_request":
            success = attrs.get("success")
            if "success" in attrs and type(success) is not bool:
                raise ClaudeDesktopOtelUsageError("OTel request success field is invalid")
            usage: dict[str, int | None] = {}
            for source, target in _USAGE_FIELDS.items():
                usage[target] = _integer(
                    attrs.get(source), source, allow_missing=source not in attrs,
                )
            row["usage"] = usage
            row["success"] = success
        else:
            response = attrs.get("response")
            response_length = _integer(
                attrs.get("response_length"), "response_length",
                allow_missing="response_length" not in attrs,
            )
            if not isinstance(response, str):
                raise ClaudeDesktopOtelUsageError("assistant response text is not a string")
            if response_length is None:
                raise ClaudeDesktopOtelUsageError("assistant response length is missing")
            if response == "<REDACTED>" or "[TRUNCATED" in response:
                raise ClaudeDesktopOtelUsageError("assistant response is redacted or truncated")
            # The export calls this a character count. Accept Unicode code
            # points and UTF-16 code units because both are common string
            # length conventions; never accept a count that matches neither.
            codepoint_length = len(response)
            utf16_length = _utf16_code_unit_length(response)
            if response_length == codepoint_length:
                response_length_semantics = "unicode_codepoints"
            elif response_length == utf16_length:
                response_length_semantics = "utf16_code_units"
            else:
                raise ClaudeDesktopOtelUsageError(
                    "assistant response length is inconsistent"
                )
            response_sha256, matched = _response_digest_and_canaries(
                response, self.response_canaries,
            )
            if response_sha256 is None:
                raise ClaudeDesktopOtelUsageError("assistant response is redacted or truncated")
            row.update({
                "response_sha256": response_sha256,
                "response_length": response_length,
                "response_length_semantics": response_length_semantics,
                "matched_response_canaries": matched,
                # This version-specific transcript UUID is retained only as a
                # diagnostic; the usage join never depends on it.
                "message_uuid": attrs.get("message.uuid") if isinstance(attrs.get("message.uuid"), str) else None,
            })
        return row

    def receipt(self) -> dict[str, Any]:
        with self._lock:
            if self.expected_session_id is None:
                raise ClaudeDesktopOtelUsageError(
                    "exact-session allowlist is not armed"
                )
            if any(
                not isinstance(row, Mapping)
                or row.get("session_id") != self.expected_session_id
                for row in self.events
            ):
                raise ClaudeDesktopOtelUsageError(
                    "capture contains an event outside the exact-session allowlist"
                )
            events = sorted(
                self.events,
                key=lambda row: (
                    row["session_id"], row["event_timestamp"],
                    row["event_sequence"], row["kind"], row["request_id"],
                ),
            )
            return {
                "schema_version": RECEIPT_SCHEMA,
                "run_id": self.run_id,
                "capture_scope": "single synthetic Claude Desktop Code (Local) run",
                "collector": {
                    "listener": "127.0.0.1",
                    "raw_prompt_content_persisted": False,
                    "raw_response_content_persisted": False,
                    "raw_api_body_content_persisted": False,
                    "unselected_attributes_persisted": False,
                },
                "events": events,
            }

    def write_receipt(self, path: Path) -> bytes:
        with self._lock:
            return self._write_receipt_locked(path)

    def _write_receipt_locked(self, path: Path) -> bytes:
        if self.invalid_reason is not None:
            raise ClaudeDesktopOtelUsageError(
                "refusing to write a receipt after a rejected OTLP export"
            )
        path = Path(path)
        if not path.is_absolute():
            raise ClaudeDesktopOtelUsageError("receipt path must be absolute")
        if path.exists() or path.is_symlink():
            raise ClaudeDesktopOtelUsageError("refusing to overwrite OTel receipt")
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise ClaudeDesktopOtelUsageError("receipt parent must be an ordinary existing directory")
        encoded = (json.dumps(
            self.receipt(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ClaudeDesktopOtelUsageError("OTel receipt is not an ordinary file")
            if os.write(fd, encoded) != len(encoded):
                raise OSError("short OTel receipt write")
            os.fsync(fd)
        except BaseException:
            os.close(fd)
            path.unlink(missing_ok=True)
            raise
        else:
            os.close(fd)
        return encoded


def join_claude_desktop_response_usage(
    receipt: Mapping[str, Any], *, run_id: str, session_id: str,
    workload: Mapping[str, Any], gui_receipt: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bind each visible canary to all complete requests in its prompt."""
    if not isinstance(receipt, Mapping) or set(receipt) != {
        "schema_version", "run_id", "capture_scope", "collector", "events",
    }:
        raise ClaudeDesktopOtelUsageError("OTel receipt has an unexpected schema")
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("run_id") != run_id:
        raise ClaudeDesktopOtelUsageError("OTel receipt schema or run identity mismatch")
    collector = receipt.get("collector")
    if not isinstance(collector, Mapping) or collector.get("listener") != "127.0.0.1":
        raise ClaudeDesktopOtelUsageError("OTel receipt was not collected on loopback")
    if any(collector.get(key) is not False for key in (
        "raw_prompt_content_persisted", "raw_response_content_persisted",
        "raw_api_body_content_persisted", "unselected_attributes_persisted",
    )):
        raise ClaudeDesktopOtelUsageError("OTel receipt persisted disallowed content")
    rows = receipt.get("events")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_EVENTS:
        raise ClaudeDesktopOtelUsageError("OTel receipt event population is empty or oversized")
    turns = workload.get("turns")
    observations = gui_receipt.get("observations") if isinstance(gui_receipt, Mapping) else None
    if not isinstance(turns, list) or len(turns) != 2 or not isinstance(observations, Mapping):
        raise ClaudeDesktopOtelUsageError("workload or GUI receipt lacks the two turns")
    turn_ids = [turn.get("id") if isinstance(turn, Mapping) else None for turn in turns]
    if any(not isinstance(value, str) or not value for value in turn_ids) or len(set(turn_ids)) != 2:
        raise ClaudeDesktopOtelUsageError("workload turn IDs must be two distinct nonempty strings")
    session_ids: set[str] = set()
    requests: list[Mapping[str, Any]] = []
    responses: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ClaudeDesktopOtelUsageError("OTel receipt event is malformed")
        kind = row.get("kind")
        if kind not in {"api_request", "assistant_response"}:
            raise ClaudeDesktopOtelUsageError("OTel receipt contains an unsupported event")
        if not isinstance(row.get("session_id"), str) or not row["session_id"]:
            raise ClaudeDesktopOtelUsageError("OTel receipt event lacks session identity")
        session_ids.add(row["session_id"])
        if row["session_id"] != session_id:
            raise ClaudeDesktopOtelUsageError("OTel receipt contains a different Claude session")
        for key in ("prompt_id", "request_id", "model", "event_timestamp"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ClaudeDesktopOtelUsageError(f"OTel receipt event lacks {key}")
        _canonical_timestamp(row["event_timestamp"])
        _integer(row.get("event_sequence"), "event_sequence")
        (requests if kind == "api_request" else responses).append(row)
    if session_ids != {session_id}:
        raise ClaudeDesktopOtelUsageError("OTel receipt does not isolate one session")
    result: dict[str, dict[str, Any]] = {}
    used_request_ids: set[str] = set()
    request_ids = [row["request_id"] for row in requests]
    if len(request_ids) != len(set(request_ids)):
        raise ClaudeDesktopOtelUsageError("OTel API request IDs are duplicated")
    used_prompt_ids: set[str] = set()
    for index, turn in enumerate(turns, 1):
        if not isinstance(turn, Mapping):
            raise ClaudeDesktopOtelUsageError("workload turn is malformed")
        canary = turn.get("response_canary")
        response_key = f"r{index}_canary_visible"
        if not isinstance(canary, str) or observations.get(response_key) != canary:
            raise ClaudeDesktopOtelUsageError(f"GUI receipt does not prove {response_key}")
        matched_responses = [
            row for row in responses
            if canary in row.get("matched_response_canaries", [])
        ]
        if len(matched_responses) != 1:
            raise ClaudeDesktopOtelUsageError(
                f"expected one OTel assistant response for {response_key}, found {len(matched_responses)}"
            )
        response = matched_responses[0]
        response_hash = response.get("response_sha256")
        if not isinstance(response_hash, str) or not _SHA256.fullmatch(response_hash):
            raise ClaudeDesktopOtelUsageError("matched OTel response content was redacted or incomplete")
        request_id = response["request_id"]
        prompt_id = response["prompt_id"]
        if prompt_id in used_prompt_ids:
            raise ClaudeDesktopOtelUsageError("one OTel prompt was assigned to multiple displayed responses")
        used_prompt_ids.add(prompt_id)
        matched_requests = [
            row for row in requests
            if row.get("prompt_id") == prompt_id
            and row.get("session_id") == session_id
        ]
        if not matched_requests or request_id not in {row["request_id"] for row in matched_requests}:
            raise ClaudeDesktopOtelUsageError(
                f"missing request-scoped usage event for {response_key}"
            )
        matched_requests.sort(key=lambda row: (row["event_timestamp"], row["event_sequence"], row["request_id"]))
        response_request = next(row for row in matched_requests if row["request_id"] == request_id)
        model = response_request["model"]
        effort = response_request.get("effort")
        if not isinstance(effort, str) or not effort:
            raise ClaudeDesktopOtelUsageError("OTel request has missing or invalid effort")
        if response["model"] != model or (response.get("effort") is not None and response["effort"] != effort):
            raise ClaudeDesktopOtelUsageError("OTel response and usage model or effort differ")
        usage = {key: 0 for key in _USAGE_FIELDS.values()}
        for request in matched_requests:
            if request["request_id"] in used_request_ids:
                raise ClaudeDesktopOtelUsageError("one API request was assigned to multiple displayed responses")
            used_request_ids.add(request["request_id"])
            if request["model"] != model or request.get("effort") != effort:
                raise ClaudeDesktopOtelUsageError("OTel prompt has mixed request model or effort")
            if request.get("success") is False:
                raise ClaudeDesktopOtelUsageError("OTel prompt contains a failed API request")
            if request.get("success") is not None and type(request["success"]) is not bool:
                raise ClaudeDesktopOtelUsageError("OTel request success field is invalid")
            request_usage = request.get("usage")
            if not isinstance(request_usage, Mapping) or set(request_usage) != set(usage):
                raise ClaudeDesktopOtelUsageError("OTel request usage fields are malformed")
            if any(type(request_usage[key]) is not int or request_usage[key] < 0 for key in usage):
                raise ClaudeDesktopOtelUsageError("OTel request has missing or invalid usage fields")
            for key in usage:
                usage[key] += request_usage[key]
        result[str(turn.get("id"))] = {
            "turn_id": turn.get("id"),
            "response_canary": canary,
            "session_id": session_id,
            "prompt_id": prompt_id,
            "request_id": request_id,
            "request_ids": [request["request_id"] for request in matched_requests],
            "request_count": len(matched_requests),
            "model": model,
            "query_source": response_request.get("query_source"),
            "effort": effort,
            "response_sha256": response_hash,
            "response_event_timestamp": response["event_timestamp"],
            "usage_event_timestamp": matched_requests[-1]["event_timestamp"],
            "usage": usage,
            "token_semantics": {
                "source": "Claude Code OpenTelemetry api_request event",
                "unit": "tokens",
                "input_tokens": "API usage input_tokens; excludes cache read and cache creation",
                "output_tokens": "API usage output_tokens",
                "cache_read_tokens": "API usage cache_read_tokens",
                "cache_write_tokens": "API usage cache_creation_tokens",
                "missing_is_zero": False,
                "estimated": False,
                "billed": False,
            },
        }
    return result


__all__ = [
    "ClaudeDesktopOtelUsageCapture", "ClaudeDesktopOtelUsageError",
    "PrivateSessionIdAllowlist", "RECEIPT_SCHEMA",
    "join_claude_desktop_response_usage",
    "read_private_session_id_file",
]
