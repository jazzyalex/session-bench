"""Shared check of every 64-hex digest in a public packet set.

A public packet may hold a SHA-256 digest only if it is:

1. ``public_bytes``: the digest of bytes that are in the public set (a file, a
   line, a canonical JSON document or one of its member values, a database
   page, a text or BLOB value of a database row, a decompressed Zstandard
   member) of this packet or of another packet of the same set;
2. ``replay_output``: a digest that the closed replay itself recomputes from
   public bytes (the caller passes the replay receipts);
3. ``allowlisted``: a digest in a field that the sanitizer names, with a
   written reason why its preimage is high-entropy private data.

Any other digest is ``unbound``. A digest of private bytes that differ from
the public bytes only by a guessable value (home name, e-mail) confirms a
guess, also through a chain of documents. The check fails on any unbound
digest.

Shorter hex values are gated the same way (``long_hex_runs``, called by
``check_public_packets``): every hex run of 12 or more characters in any file,
file name or decompressed member must be zeros, a number, a reviewed shape (a
random UUID, a provider id in a reviewed field, an OpenCode time id), derivable
from public bytes, or accepted by a written rule that proves the value itself.
It fails by default.

A hex value is derivable only as a whole: ALL its characters must be the start of one complete digest of public
bytes. A public digest with other hex next to it is not derivable (a format rule may prove the neighbours, see
``_sqlite_frame``). Names are gated like contents: every path component of every file, directory and link. A hex value
of 6 to 11 characters is gated when it is the value of a digest-like JSON key. Not gated: a hex value of 6 to 11
characters under any other key, and a hex value below 6 characters.
"""
from __future__ import annotations

import bisect
import collections
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

HEX64 = re.compile(rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_PAGE_SIZES = (4096, 1024, 8192, 16384, 32768, 65536, 512, 2048)


class UnboundDigestError(ValueError):
    """A public set holds a digest that is not bound to public bytes."""


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).hexdigest().encode()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class _Members(dict):
    """A JSON object that keeps every member. ``dict`` keeps the last member of a repeated key, so a value under the
    first one would be hidden from a reader of the parsed document; ``pairs`` has them all."""

    def __init__(self, pairs: list[tuple[str, Any]]) -> None:
        super().__init__(pairs)
        self.pairs = list(pairs)


def _items(value: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Every member of a parsed JSON object, also those of a repeated key."""
    return value.pairs if isinstance(value, _Members) else list(value.items())


def _loads(text: str | bytes) -> Any:
    return json.loads(text, object_pairs_hook=_Members)


def _members(value: Any, found: set[bytes], depth: int = 0) -> None:
    """Digests of every object, array and string inside a JSON value."""
    if isinstance(value, (dict, list)):
        try:
            raw = _canonical(value)
            found.add(_sha(raw)); found.add(_sha(raw + b"\n"))
        except ValueError:
            pass
        for item in (value.values() if isinstance(value, dict) else value):
            _members(item, found, depth + 1)
    elif isinstance(value, str):
        found.add(_sha(value.encode("utf-8")))
        start = 0 if value[:1] in "{[" else value.find("{")
        if start >= 0 and depth < 12:
            try:
                _members(json.loads(value[start:]), found, depth + 1)
            except ValueError:
                pass


def public_byte_digests(data: bytes, name: str = "") -> set[bytes]:
    """Every digest that a reader can compute from one public file alone."""
    found = {_sha(data)}
    views = [data]
    if name.endswith((".zst", ".zstd")):
        try:
            import compression.zstd as zstd
            views.append(zstd.decompress(data))
        except Exception:  # an unreadable member has only its file digest
            pass
    for view in views:
        found.add(_sha(view))
        try:
            _members(json.loads(view), found)
        except ValueError:
            pass
        for line in view.split(b"\n"):
            raw = line.rstrip(b"\r")
            if not raw:
                continue
            found.update((_sha(raw), _sha(line), _sha(raw + b"\n"), _sha(line + b"\n")))
            if raw[:1] in b"{[":
                try:
                    _members(json.loads(raw), found)
                except ValueError:
                    pass
        if view[:16] == b"SQLite format 3\x00" or name.endswith(("-wal", ".db")):
            for size in _PAGE_SIZES:
                for offset in range(0, len(view) - size + 1, size):
                    found.add(_sha(view[offset:offset + size]))
        if view[:16] == b"SQLite format 3\x00":
            found |= _database_value_digests(view)
    return found


def _database_value_digests(database: bytes) -> set[bytes]:
    """Digests of every text and BLOB value of a public SQLite file (a content-addressed store names a row by one).

    The bytes are copied to a new temporary directory and the copy is opened.
    An unreadable database has only its file and page digests.
    """
    import sqlite3
    import tempfile
    found: set[bytes] = set()
    try:
        with tempfile.TemporaryDirectory(prefix="bench-public-digest-") as directory:
            clone = Path(directory) / "public.db"
            clone.write_bytes(database)
            connection = sqlite3.connect(clone)
            try:
                connection.execute("PRAGMA query_only=ON")
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                for table in tables:
                    quoted = table.replace('"', '""')
                    for row in connection.execute(f'SELECT * FROM "{quoted}"'):
                        for value in row:
                            if isinstance(value, bytes):
                                found.add(_sha(value))
                            elif isinstance(value, str):
                                found.add(_sha(value.encode("utf-8")))
            finally:
                connection.close()
    except (sqlite3.Error, OSError, UnicodeError):
        pass
    return found


def _tokens(data: bytes, name: str) -> set[bytes]:
    found = set(HEX64.findall(data))
    if name.endswith((".zst", ".zstd")):
        try:
            import compression.zstd as zstd
            found |= set(HEX64.findall(zstd.decompress(data)))
        except Exception:
            pass
    return found


def _allowed(path: str, data: bytes, allowlist: Mapping[str, Mapping[str, str]]) -> set[bytes]:
    """Digests under the allowlisted JSON fields of one file.

    ``allowlist`` maps a file path suffix to ``{field path: reason}``. A field
    path is dot-separated keys; ``*`` matches every key or list item, and the
    path ``*`` alone is the whole file. Every digest inside the matched value
    is allowlisted.
    """
    found: set[bytes] = set()
    for suffix, fields in allowlist.items():
        if not (path == suffix or path.endswith("/" + suffix)):
            continue
        documents = []
        try:
            documents = [json.loads(data)]
        except ValueError:
            for line in data.split(b"\n"):
                try:
                    documents.append(json.loads(line))
                except ValueError:
                    pass
        for field, reason in fields.items():
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError(f"allowlisted digest field {suffix}:{field} needs a written reason")
            if field == "*" and not documents:
                found |= set(HEX64.findall(data))  # a file that is not JSON: every digest in it
            for document in documents:
                values = [document]
                for key in ([] if field == "*" else field.split(".")):
                    following = []
                    for value in values:
                        if key == "*":
                            following += list(value.values()) if isinstance(value, dict) else list(value) if isinstance(value, list) else []
                        elif isinstance(value, dict) and key in value:
                            following.append(value[key])
                    values = following
                for value in values:
                    found |= set(HEX64.findall(_canonical(value)))
    return found


def read_public_set(packets: Iterable[Path | str], extra_files: Iterable[Path | str] = ()) -> dict[str, bytes]:
    """All files of the packets (and extra public files) as ``{label/relative path: bytes}``."""
    contents: dict[str, bytes] = {}
    for packet in packets:
        packet = Path(packet)
        for path in sorted(packet.rglob("*")):
            if path.is_file() and not path.is_symlink():
                contents[f"{packet.name}/{path.relative_to(packet).as_posix()}"] = path.read_bytes()
    for path in extra_files:
        contents[Path(path).name] = Path(path).read_bytes()
    return contents


def public_entry_names(packets: Iterable[Path | str]) -> list[str]:
    """The names (``label/relative path``) of every directory and every link of the packets, empty directories included.
    A name is public text, as the content of a file is."""
    names: list[str] = []
    for packet in packets:
        packet = Path(packet)
        for path in sorted(packet.rglob("*")):
            if path.is_dir() or path.is_symlink():
                names.append(f"{packet.name}/{path.relative_to(packet).as_posix()}")
    return names


WORKLOAD_FILE_REASON = ("digest of the benchmark workload file before or after the model's edit, as the public observer "
                        "document states it; the file is a short public source file and the edit is in the public native record")


def benchmark_public_references() -> dict[str, bytes]:
    """The published workload fixture of the benchmark (repository files, no operator data)."""
    root = Path(__file__).resolve().parents[1] / "fixtures/scenarios/survival-v1/workload"
    return {"benchmark-fixture/" + path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _workload_file_digests(contents: Mapping[str, bytes]) -> set[bytes]:
    """before/after digests of file-change events in the public observer documents of the set."""
    found: set[bytes] = set()
    for name, data in contents.items():
        if not name.endswith("observer.json"):
            continue
        try:
            events = json.loads(data).get("events", [])
        except (ValueError, AttributeError):
            continue
        for event in events if isinstance(events, list) else []:
            fields = event.get("fields") if isinstance(event, dict) and event.get("kind") == "file_change" else None
            for key in ("before_sha256", "after_sha256"):
                value = fields.get(key) if isinstance(fields, dict) else None
                if isinstance(value, str) and HEX64.fullmatch(value.encode()):
                    found.add(value.encode())
    return found


def classify_public_digests(contents: Mapping[str, bytes], *, replay_outputs: Iterable[bytes] = (),
                            allowlist: Mapping[str, Mapping[str, str]] | None = None,
                            public_references: Mapping[str, bytes] | None = None,
                            extra_names: Iterable[str] = ()) -> dict[str, Any]:
    """Classify every 64-hex token of a public set; ``unbound`` lists what may not be published.

    The names are classified too: a 64-hex token in the path of a file, or in ``extra_names`` (the paths of
    directories and links), must be zeros, public bytes or a replay output; an allowlist names fields of file contents
    and gives a name no allowance.

    ``public_references`` are public bytes outside the set that count as class
    1; the default is the benchmark workload fixture. ``replay_outputs`` are
    the replay receipts of the packets: a digest in them, or the digest of one
    of their member documents, is class 2.
    """
    computable: set[bytes] = {_sha(b"")}
    for name, data in {**(benchmark_public_references() if public_references is None else public_references), **contents}.items():
        computable |= public_byte_digests(data, name)
    recomputed: set[bytes] = set()
    for output in replay_outputs:
        recomputed |= set(HEX64.findall(output))
        try:
            _members(json.loads(output), recomputed)
        except ValueError:
            pass
    workload = _workload_file_digests(contents)
    classes = {"public_bytes": 0, "replay_output": 0, "allowlisted": 0}
    unbound: list[dict[str, str]] = []
    for name in sorted({*contents, *extra_names}):
        for token in sorted(set(HEX64.findall(name.encode()))):
            if set(token) == {ord("0")} or token in computable:
                classes["public_bytes"] += 1
            elif token in recomputed:
                classes["replay_output"] += 1
            else:
                unbound.append({"file": name, "digest": token.decode(), "where": "name"})
    for name in sorted(contents):
        data = contents[name]
        tokens = _tokens(data, name)
        if not tokens:
            continue
        allowed = _allowed(name, data, allowlist or {})
        for token in sorted(tokens):
            if set(token) == {ord("0")} or token in computable:
                classes["public_bytes"] += 1
            elif token in recomputed:
                classes["replay_output"] += 1
            elif token in allowed or token in workload:
                classes["allowlisted"] += 1
            else:
                unbound.append({"file": name, "digest": token.decode()})
    return {"classes": classes, "unbound": unbound}


def require_bound_public_digests(contents: Mapping[str, bytes], *, replay_outputs: Iterable[bytes] = (),
                                 allowlist: Mapping[str, Mapping[str, str]] | None = None,
                                 public_references: Mapping[str, bytes] | None = None,
                                 extra_names: Iterable[str] = ()) -> dict[str, Any]:
    """Fail when the public set holds a digest that is not public bytes, replay output or allowlisted."""
    result = classify_public_digests(contents, replay_outputs=replay_outputs, allowlist=allowlist, public_references=public_references,
                                     extra_names=extra_names)
    if result["unbound"]:
        files = sorted({item["file"] for item in result["unbound"]})
        raise UnboundDigestError(f"{len(result['unbound'])} unbound digest(s) in public set: " + ", ".join(files[:12]))
    return result


# --- Hex runs that are not 64-hex digests ---
#
# The digest check above sees only tokens of exactly 64 lowercase hex characters. A digest can also stand in a file
# name, in a path, in an ordinary string field or in a binary file as a truncated hash: a 40-hex SHA-1, a 32-hex MD5, a
# 20-character prefix of a SHA-256, an upper-case digest. Every hex run of ``SHORT_HEX_MINIMUM`` or more characters that
# is not a 64-hex token must be of a reviewed shape, derivable from public bytes or proved by a written rule. Fail by
# default.
#
# Why 12 (and 6 under a digest-like key). A hex run of 12 characters has 48 bits. Shorter runs are indistinguishable from English words made of the
# letters a to f ("decade", "facade", "defaced"), from colour codes and from short git object names, and the gate cannot
# prove such a value. Measured on the eleven ranked sets (``tests/test_public_digest_check.py``): lowering the bound to 6
# adds thousands of hits that are words, colours, base62 ids and counters and none that a reader can derive or reject;
# the number is in the hardening report. A 48-bit prefix of a private digest is still a usable guess confirmation for
# a low-entropy preimage, so a bound lower than 12 would be better in principle; it is not usable without a reviewer.
SHORT_HEX_MINIMUM = 12
# A hex value of 6 to 11 characters is gated only when it is the value of a digest-like JSON key (see ``_DIGEST_KEY``).
SHORT_KEYED_MINIMUM = 6
# Key names that mark a value as a digest: hash, digest, sha, checksum, fingerprint, etag, signature, hmac, thumbprint, md5, crc.
_DIGEST_KEY = re.compile(r"hash|digest|fingerprint|checksum|sha|md5|crc|etag|signature|hmac|thumbprint", re.I)
_SHORT_HEX = re.compile(r"[0-9a-fA-F]{6,63}")
_HEX64_EXACT = re.compile(rb"[0-9a-f]{64}")
# An absolute path as a JSON string (``"/a/b"``): a reader can hash it as it stands.
_PATH_STRING = re.compile(rb'(?<=")/[^"\\\n]{3,}(?=")')
_OBJECT_START = re.compile(r'\{\s*"')

# Shapes. A shape is a form that a native id, a number or a constant has; none of them can hold a digest of a private
# value, and a reader can test each one on the bytes.
#
# Provider id: a model provider issues it for a message, a reasoning item, a response, a function call or a tool
# call: a fixed prefix, ``_`` and random hex of a fixed length. It is a random handle and a join key of the native
# record, not a digest of any content. The prefix and the lengths below were measured on the eleven current ranked
# sets. An id counts only inside a JSON string value, or a key, that is exactly the id (a label ending in ``:`` or ``|``
# may stand before it), under a field name that the configuration lists in ``PROVIDER_ID_FIELDS``.
PROVIDER_ID_SHAPES: dict[str, tuple[int, ...]] = {
    "msg": (32, 50), "rs": (24, 32, 50), "fc": (32, 50), "call": (32,), "resp": (50,), "ctc": (50,),
}
# Reviewed field names per configuration (a name with ``key:`` stands for the keys of a JSON object held under that
# field). Derived from the ids of the current public sets on 2026-10-07: for each configuration, the smallest set of
# fields that covers every id of the ranked set (each name was dropped in turn and kept only when an id then became
# unlisted). A field that is not listed makes its id unlisted. A field that holds JSON text is read through that text.
PROVIDER_ID_FIELDS: dict[str, tuple[str, ...]] = {
    "codex-cli": ("id", "event_id", "response_id"),
    "hermes": ("id", "event_id", "tool_call_id", "response_item_id"),
    "openclaw": ("event_id", "toolCallId", "idempotencyKey", "mirrorIdentity"),
    "opencode-cli": ("event_id", "call_id", "callID", "itemId"),
    "pi": ("id", "to_id", "native_action_id", "native_result_id", "observer_ids", "toolCallId", "responseId", "provider_response_id"),
}
_PROVIDER_VALUE = re.compile(r"(?:.{0,200}[:|])?(?P<id>(?P<prefix>[a-z]{2,4})_(?P<hex>[0-9a-f]{12,}))", re.S)
_PROVIDER_BEFORE = re.compile(rb"(?<![A-Za-z0-9])([a-z]{2,4})_\Z")
# OpenCode id: ``ses_``, ``msg_``, ``prt_``, ``evt_`` and 12 hex characters of a time, then 14 random base62 characters.
# The time is the low 36 bits of a millisecond clock (bit-inverted for the descending ids), shifted left by 12 bits.
# The shape counts only when that clock value is within two days of a millisecond timestamp that the public set holds.
_TIME_ID_BEFORE = re.compile(rb"(?:ses|msg|prt|evt)_\Z")
_TIME_ID_WINDOW_MS = 2 * 86_400_000
_MS_TIMESTAMP = re.compile(rb"(?<![0-9])1[6-9][0-9]{11}(?![0-9])")
_UUID_BEFORE = re.compile(rb"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[47][0-9A-Fa-f]{3}-[89abAB][0-9A-Fa-f]{3}-\Z")
# Compact calendar time ``YYYYMMDDhhmmss`` (the name of a database migration).
_COMPACT_TIME = re.compile(rb"20[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])(?:[01][0-9]|2[0-3])[0-5][0-9][0-5][0-9]")
_JSON_NUMBER_BEFORE = re.compile(rb"(?:[:\[,]\s*-?|[0-9]\.|(?:<=|>=|<|>|=|\bAND|\bBETWEEN)\s*)\Z", re.I)
_JSON_NUMBER_AFTER = re.compile(rb"(?:[,}\]\s.eE\\)]|\Z)")
# The value of one JSON field, as bytes: ``"field": "label:prefix_`` before a provider id and ``:suffix"`` after it. The
# quotes may be escaped (JSON text inside a JSON string).
_PROVIDER_FIELD_BEFORE = re.compile(rb'(?P<field>[A-Za-z_][A-Za-z0-9_]*)\\*"\s*:\s*\\*"(?:[^"\\\n]{0,200}[:|])?(?P<prefix>[a-z]{2,4})_\Z')
_PROVIDER_FIELD_AFTER = re.compile(rb'(?:[:|][^"\\\n]{0,200})?\\*"')
# Public constants: the alphabet of a hex digit.
_CONSTANTS = frozenset({b"0123456789abcdef", b"0123456789ABCDEF"})


class UnlistedHexRunError(ValueError):
    """A public set holds a hex run that is neither a reviewed shape, nor derivable from public bytes, nor proved by a rule."""


def _decompressed(name: str, data: bytes) -> bytes | None:
    if name.endswith((".zst", ".zstd")):
        try:
            import compression.zstd as zstd
            return zstd.decompress(data)
        except Exception:
            return None
    return None


def _documents(view: bytes) -> list[Any]:
    """The JSON documents of a file: the whole file, or each JSON object found inside it (a line, a cell of a database)."""
    try:
        return [_loads(view)]
    except ValueError:
        pass
    text = view.decode("utf-8", "surrogateescape")
    decoder = json.JSONDecoder(object_pairs_hook=_Members)
    found: list[Any] = []
    position = 0
    for match in _OBJECT_START.finditer(text):
        if match.start() < position:
            continue
        try:
            document, position = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        found.append(document)
    return found


def _strings(value: Any, field: str = "", depth: int = 0) -> Iterable[tuple[str, str]]:
    """``(field, string)`` of every string value and every key of a JSON value, also inside JSON text held in a string.

    A key is reported under ``key:<field of the object>``; a list item has the field of the list.
    """
    if isinstance(value, dict):
        for key, item in _items(value):
            yield "key:" + field, key
            yield from _strings(item, key, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, field, depth + 1)
    elif isinstance(value, str):
        yield field, value
        start = value.find("{") if value[:1] not in ("{", "[") else 0
        if value and start >= 0 and depth < 12:
            try:
                yield from _strings(_loads(value[start:]), field, depth + 1)
            except ValueError:
                pass


class _PrefixIndex:
    """Complete lower-case hex digests of one kind, searched by prefix.

    A token is found only when ALL of its characters are the start of one complete digest (a token longer than the digest
    is never found). The index keeps complete digests: a short key would accept a token whose tail is arbitrary.
    """

    def __init__(self) -> None:
        self.keys: set[bytes] = set()
        self._sorted: list[bytes] | None = None

    def add(self, digest: bytes) -> None:
        if digest not in self.keys:
            self.keys.add(digest)
            self._sorted = None

    def whole(self, token: bytes) -> bool:
        """The token is one complete digest."""
        return token.lower() in self.keys

    def has(self, token: bytes) -> bool:
        """The token is the start (or all) of one complete digest."""
        token = token.lower()
        if self._sorted is None:
            self._sorted = sorted(self.keys)
        at = bisect.bisect_left(self._sorted, token)
        return at < len(self._sorted) and self._sorted[at].startswith(token)


class _Public:
    """Everything that a reader computes from the public bytes of one set (and the published benchmark fixture)."""

    def __init__(self, contents: Mapping[str, bytes], references: Mapping[str, bytes]) -> None:
        self.contents = contents
        self.sha256, self.sha1, self.md5 = _PrefixIndex(), _PrefixIndex(), _PrefixIndex()
        self._documents: dict[str, list[Any]] = {}
        self._memo: dict[str, Any] = {}
        everything = {**references, **contents}
        timestamps: set[int] = set()
        for name, data in everything.items():
            for digest in public_byte_digests(data, name):
                self.sha256.add(digest)
            views = [data]
            plain = _decompressed(name, data)
            if plain is not None:
                views.append(plain)
            for view in views:
                for piece in [view, *view.split(b"\n"), *_PATH_STRING.findall(view)]:
                    self.sha1.add(hashlib.sha1(piece).hexdigest().encode()); self.md5.add(hashlib.md5(piece).hexdigest().encode())
                if name in contents:
                    for field, text in (pair for document in self.documents(name if view is data else name + "#zst", view) for pair in _strings(document)):
                        raw = text.encode("utf-8", "surrogateescape")
                        self.sha256.add(_sha(raw)); self.sha1.add(hashlib.sha1(raw).hexdigest().encode()); self.md5.add(hashlib.md5(raw).hexdigest().encode())
                    timestamps |= {int(value) for value in _MS_TIMESTAMP.findall(view)}
        self.clock = sorted({value % (1 << 36) for value in timestamps})
        self.blob = b"\0".join(contents.values())
        self.corpus = b"\0".join(data for data in everything.values() if not data.startswith(b"SQLite format 3\x00"))  # plain public bytes outside databases
        self.names = "\n".join(contents)

    def memo(self, key: str, build: Callable[[], Any]) -> Any:
        """A value that a proof computes once for the whole set."""
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    def documents(self, key: str, view: bytes) -> list[Any]:
        if key not in self._documents:
            self._documents[key] = _documents(view)
        return self._documents[key]

    def near_clock(self, value: int) -> bool:
        """The 36-bit clock value is within two days of a millisecond timestamp of the set (the clock wraps)."""
        for candidate in (value, value + (1 << 36), value - (1 << 36)):
            at = bisect.bisect_left(self.clock, candidate - _TIME_ID_WINDOW_MS)
            if at < len(self.clock) and self.clock[at] <= candidate + _TIME_ID_WINDOW_MS:
                return True
        return False


class _Run:
    """One hex run, with the bytes around it."""

    def __init__(self, name: str, view: bytes, match: re.Match[bytes], public: _Public, key: str) -> None:
        self.name, self.view, self.start, self.end, self.public, self.key = name, view, match.start(), match.end(), public, key
        self.token = match.group(0)

    def documents(self) -> list[Any]:
        return self.public.documents(self.key, self.view)


def _varint(data: bytes, position: int) -> tuple[int, int] | None:
    """A SQLite varint at ``position``: ``(value, next position)``, or ``None`` at the end of the data."""
    value = 0
    for index in range(9):
        if position + index >= len(data):
            return None
        byte = data[position + index]
        if index == 8:
            return (value << 8) | byte, position + 9
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position + index + 1
    return None


def _serial_size(serial: int) -> int:
    return {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8, 8: 0, 9: 0}.get(serial, (serial - 12 - serial % 2) // 2 if serial >= 12 else 0)


class _SqliteCells:
    """The cells of the b-tree pages of a SQLite file that are reachable from the schema, parsed as SQLite parses them.

    ``cells`` is a sorted list of ``(start, end, framing_end, fields)``: ``start`` to ``framing_end`` is the framing of
    the cell (a child page number, the payload length, the row id, the record header), and ``fields`` is
    ``[(serial type, start, end)]`` of its record. A cell is listed only when its page is a page of a table or an index
    b-tree that the schema reaches, its page is consistent (the cell pointers are inside the page and the cells do not
    overlap). Of a cell with an overflow page only the local part is read: the record header and the fields that lie whole
    in the local part are listed, the overflow page number and the overflow pages are never proof. Free space, freeblocks,
    unreachable pages and anything else that is not a parsed cell are not listed.
    """

    def __init__(self, view: bytes) -> None:
        self.cells: list[tuple[int, int, int, list[tuple[int, int, int]]]] = []
        self._starts: list[int] = []
        self._read(view)
        self.cells.sort()
        self._starts = [cell[0] for cell in self.cells]

    def _read(self, view: bytes) -> None:
        if not view.startswith(b"SQLite format 3\x00") or len(view) < 512 or view[56:60] != b"\x00\x00\x00\x01":
            return
        size = int.from_bytes(view[16:18], "big")
        size = 65536 if size == 1 else size
        usable = size - view[20]
        if size < 512 or size & (size - 1) or size > 65536 or usable < 480:
            return
        pages = len(view) // size
        seen: set[int] = set()
        roots = [(1, True)]
        walked = 0
        while roots and walked < 1_000_000:
            number, table = roots.pop()
            walked += 1
            if not 1 <= number <= pages or number in seen:
                continue
            seen.add(number)
            rows, children = self._page(view, number, size, usable, table)
            roots.extend((child, table) for child in children)
            if number == 1:  # the schema: ``(type, name, table name, root page, sql)`` of each row
                for fields in rows:
                    if len(fields) >= 4 and fields[0][0] == "text" and fields[3][0] == "int" and fields[3][1] > 1 and fields[0][1] in (b"table", b"index"):
                        roots.append((fields[3][1], fields[0][1] == b"table"))

    def _page(self, view: bytes, number: int, size: int, usable: int, table: bool) -> tuple[list[list[tuple[str, Any]]], list[int]]:
        """The schema rows (page 1) and the child pages of one page; the cells of a consistent page are listed."""
        base = (number - 1) * size
        head = base + (100 if number == 1 else 0)
        kind = view[head]
        if kind != (0x0D if table else 0x0A) and kind != (0x05 if table else 0x02):
            return [], []
        interior = kind in (0x05, 0x02)
        count = int.from_bytes(view[head + 3:head + 5], "big")
        array = head + (12 if interior else 8)
        limit = base + usable
        if array + 2 * count > limit:
            return [], []
        children = [int.from_bytes(view[head + 8:head + 12], "big")] if interior else []
        extents: list[tuple[int, int, int, list[tuple[int, int, int]]]] = []
        rows: list[list[tuple[str, Any]]] = []
        guards: list[tuple[int, int]] = []
        for index in range(count):
            start = base + int.from_bytes(view[array + 2 * index:array + 2 * index + 2], "big")
            if start < array + 2 * count or start >= limit:
                return [], []
            position = start
            if interior:
                children.append(int.from_bytes(view[position:position + 4], "big"))
                position += 4
            if table and interior:  # a table interior cell has a row id and no payload
                parsed = _varint(view, position)
                if parsed is None:
                    return [], []
                extents.append((start, parsed[1], parsed[1], []))
                guards.append((start, parsed[1]))
                continue
            length = _varint(view, position)
            if length is None:
                return [], []
            payload_size, position = length
            if table:
                rowid = _varint(view, position)
                if rowid is None:
                    return [], []
                position = rowid[1]
            maximum = usable - 35 if table else (usable - 12) * 64 // 255 - 23
            local = payload_size
            if payload_size > maximum:  # the payload goes on in an overflow page: only the local part is read
                minimum = (usable - 12) * 32 // 255 - 23
                local = minimum + (payload_size - minimum) % (usable - 4)
                local = local if local <= maximum else minimum
            fields = self._record(view, position, payload_size, local)
            if fields is None or position + local + (4 if local < payload_size else 0) > limit:
                return [], []
            extents.append((start, position + local, position + fields[0], fields[1]))
            guards.append((start, position + local + (4 if local < payload_size else 0)))  # the overflow page number is part of the cell, and no proof
            if number == 1:
                rows.append([("int", self._integer(view, *field)) if field[0] in (1, 2, 3, 4, 5, 6, 8, 9) else
                             ("text", view[field[1]:field[2]]) if field[0] >= 13 and field[0] % 2 else ("other", None) for field in fields[1]])
        extents.sort()
        guards.sort()
        if any(guards[at][1] > guards[at + 1][0] for at in range(len(guards) - 1)) or (guards and guards[-1][1] > limit):
            return [], []
        self.cells.extend(extent for extent in extents if extent[3] or extent[2] > extent[0])
        return rows, children

    @staticmethod
    def _record(view: bytes, start: int, size: int, local: int) -> tuple[int, list[tuple[int, int, int]]] | None:
        """``(header size, [(serial type, start, end)])`` of the record at ``start``, or ``None`` when it is not a record of ``size`` bytes.
        Only fields that lie whole in the first ``local`` bytes are listed; the header must lie there too."""
        head = _varint(view, start)
        if head is None or not 1 <= head[0] <= local:
            return None
        position, offset, fields = head[1], start + head[0], []
        while position < start + head[0]:
            parsed = _varint(view, position)
            if parsed is None or parsed[0] in (10, 11):
                return None
            length = _serial_size(parsed[0])
            if offset + length <= start + local:
                fields.append((parsed[0], offset, offset + length))
            offset += length
            position = parsed[1]
        if position != start + head[0] or offset != start + size:
            return None
        return head[0], fields

    @staticmethod
    def _integer(view: bytes, serial: int, start: int, end: int) -> int:
        return {8: 0, 9: 1}.get(serial, int.from_bytes(view[start:end], "big", signed=True))

    def covering(self, position: int) -> tuple[int, int, int, list[tuple[int, int, int]]] | None:
        """The parsed cell that holds the byte at ``position``."""
        at = bisect.bisect_right(self._starts, position) - 1
        return self.cells[at] if at >= 0 and position < self.cells[at][1] else None


def _sqlite_frame(run: _Run, window: int) -> bool:
    """Format rule for a hex run longer than 64 characters in a SQLite file.

    The 64 characters at ``window`` are exactly one whole TEXT field of a record in a parsed cell (``_SqliteCells``), and
    every other character of the run is a byte of a parsed cell that is not hex data: framing (a child page number, the
    payload length, the row id, the record header), a byte of a field with an integer serial type (1 to 6), a byte of
    a text or BLOB field (16 bytes or more) whose whole value is in the public bytes (``corpus``: the files of the set
    that are not databases, and the published fixture), or a byte of another TEXT field that is one whole public
    SHA-256 digest. A digest inside a BLOB field, inside free space or a freeblock, in a page that the schema does not
    reach, in the overflow part of a cell, or anywhere else that no parsed cell holds, is not covered.
    """
    cells = run.public.memo("sqlite-cells:" + run.key, lambda: _SqliteCells(run.view))
    holder = cells.covering(window)
    if holder is None or (141, window, window + 64) not in holder[3]:
        return False
    corpus_checked: dict[tuple[int, int], bool] = {}
    for position in [*range(run.start, window), *range(window + 64, run.end)]:
        cell = cells.covering(position)
        if cell is None:
            return False
        if position < cell[2]:
            continue
        for serial, start, end in cell[3]:
            if start <= position < end:
                size = end - start
                if 1 <= serial <= 6:
                    break
                if serial >= 12 and ((size >= 16 and corpus_checked.setdefault((start, end), run.view[start:end] in run.public.corpus))
                                     or (serial == 141 and run.public.sha256.whole(run.view[start:end].lower()))):
                    break
                return False
        else:
            return False
    return True


def _joined_derivable(run: _Run, low: bytes, top: bool) -> bool:
    """A run longer than 64 characters is derivable only as parts, and every part must pass on its own.

    Either a format rule proves the characters around a public SHA-256 digest (``_sqlite_frame``), or the run splits
    around a complete public SHA-256 digest into remainders that are each empty, zeros, or at least 12 characters that
    are the whole start of a public digest. A short remainder proves nothing and the run is unlisted.
    """
    for at in range(len(low) - 63):
        if run.public.sha256.whole(low[at:at + 64]):
            if top and _sqlite_frame(run, run.start + at):
                return True
            if all(not part or set(part) == {ord("0")} or (len(part) >= SHORT_HEX_MINIMUM and _whole_derivable(run, part, False))
                   for part in (low[:at], low[at + 64:])):
                return True
    return False


def _whole_derivable(run: _Run, low: bytes, top: bool = True) -> bool:
    """All supplied characters are the start of one complete SHA-256, SHA-1 or MD5 digest of the public bytes."""
    if len(low) > 64:
        return _joined_derivable(run, low, top)
    return run.public.sha256.has(low) or run.public.sha1.has(low) or run.public.md5.has(low)


def _decimal_shape(run: _Run) -> bool:
    """A run of decimal digits is a number, not a hex string: an unquoted JSON number, or a Unix time in s, ms, us or ns."""
    token = run.token
    before = run.view[max(0, run.start - 24):run.start]
    if _JSON_NUMBER_BEFORE.search(before) and _JSON_NUMBER_AFTER.match(run.view[run.end:run.end + 1]):
        return True
    return bool(_COMPACT_TIME.fullmatch(token)) or len(token) in (10, 13, 16, 19) and 10 ** (len(token) - 1) <= int(token) < 4_100_000_000 * 10 ** (len(token) - 10)


def _uuid_shape(run: _Run) -> bool:
    """The first 12 characters are the last group of a version 4 or 7 UUID. Up to 4 characters may follow that are bytes of
    a binary record next to it, or 8 that start the next UUID."""
    token = run.token
    if len(token) < 12 or not _UUID_BEFORE.search(run.view[max(0, run.start - 24):run.start]):
        return False
    rest = len(token) - 12
    return rest <= 4 or (rest == 8 and run.view[run.end:run.end + 1] == b"-")


def _time_id_shape(run: _Run) -> bool:
    before = run.view[max(0, run.start - 4):run.start]
    token = run.token
    if not _TIME_ID_BEFORE.search(before) or not 12 <= len(token) <= 26:
        return False
    value = int(token[:12], 16)
    mask = (1 << 48) - 1
    return any(run.public.near_clock(clock >> 12) for clock in (value, mask ^ value))


def _provider_budget(run: _Run, fields: frozenset[str]) -> collections.Counter:
    budget: collections.Counter = collections.Counter()
    for document in run.documents():
        for field, text in _strings(document):
            if field in fields:
                match = _PROVIDER_VALUE.fullmatch(text)
                if match and len(match["hex"]) in PROVIDER_ID_SHAPES.get(match["prefix"], ()):
                    budget[match["id"].encode().lower()] += 1
    return budget


# A rule is ``{"file", "before", "proof", "reason"}`` and, for a rule whose values are few, ``"pin"``:
#
# - ``file``: a file path suffix (``*`` for every file);
# - ``before``: a pattern that must match the bytes directly before the run (up to 64 bytes are given to it);
# - ``proof``: a function ``proof(run) -> bool`` that tests the value itself against public bytes (the value decodes to
#   the public string next to it, to a public JSON document, or to a protobuf whose strings are public; or it is a
#   value written in the rule). A run for which the proof fails is unlisted, however well the context matches;
# - ``pin`` (optional): ``{"count": n, "sha256": ...}``: the number of runs that the rule accepts in the set and the
#   (optionally) the SHA-256 of the sorted, distinct accepted values joined by newlines. When the set differs, the rule
#   accepts nothing;
# - ``reason``: a written reason (20 or more characters).
#
# A rule without a ``proof`` (the form before 2026-10-07) was a context match only and allowed any value. It is read but
# gives no allowance; ``long_hex_runs`` reports how many it ignored.
def _hex_rules(hex_allowlist: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    rules: list[dict[str, Any]] = []
    ignored = 0
    for rule in hex_allowlist:
        if (not isinstance(rule, Mapping) or not {"file", "before", "reason"} <= set(rule) or not set(rule) <= {"file", "before", "reason", "proof", "pin"}
                or not isinstance(rule["file"], str) or not isinstance(rule["before"], bytes)
                or not isinstance(rule["reason"], str) or len(rule["reason"].strip()) < 20):
            raise ValueError("a hex allowlist rule needs a file suffix, a pattern for the bytes before the run, a proof and a written reason")
        if "proof" not in rule:
            ignored += 1
            continue
        if not callable(rule["proof"]):
            raise ValueError("the proof of a hex allowlist rule must be a function")
        pin = rule.get("pin")
        if pin is not None and (not isinstance(pin, Mapping) or "count" not in pin or not set(pin) <= {"count", "sha256"} or not isinstance(pin["count"], int)):
            raise ValueError("a pin needs a count and may have a SHA-256 of the sorted values")
        rules.append({**rule, "pattern": re.compile(rb"(?:" + rule["before"] + rb")\Z", re.S)})
    return rules, ignored


def pinned_value(value: str) -> Callable[[_Run], bool]:
    """A proof: the run is exactly this value, which the rule writes down."""
    return lambda run: run.token.decode().lower() == value.lower()


def _hex_text_decodings(token: bytes) -> Iterable[bytes]:
    """The bytes a hex run can stand for, allowing up to three hex-like neighbour bytes at either end."""
    for lead in range(4):
        for trail in range(4):
            part = token[lead:len(token) - trail]
            if len(part) >= 2 and len(part) % 2 == 0:
                try:
                    yield bytes.fromhex(part.decode())
                except ValueError:
                    pass


def _record_path_proof(run: _Run) -> bool:
    """The run is the hex text of a path string that the same file holds in plain (a ``path`` or ``resolvedPath`` member)."""
    paths = {text for document in run.documents() for field, text in _strings(document) if field in ("path", "resolvedPath")}
    try:
        return bytes.fromhex(run.token.decode()).decode("utf-8") in paths
    except ValueError:
        return False


# Reviewed schema of the meta row of a Cursor chat store: exactly the keys of the meta rows in the three public Cursor
# packets (read 2026-10-08 from ``cursor-cli-public-candidates-v3``), each with the one kind of value it holds there.
# The row is flat: a nested object or array, a key outside this list and a repeated key are all refused.
#   agentId           random UUID version 4 that also names a directory of the set
#   latestRootBlobId  64-hex digest that the public bytes yield (the content id of a public blob)
#   name              a word of the reviewed vocabulary
#   createdAt         integer, a time in milliseconds (at most 14 digits)
#   mode              a word of the reviewed vocabulary
#   isRunEverything   boolean
#   blobEncryptionKey 64 zeros (the sanitizer zeroes it) or a digest that the public bytes yield
CURSOR_META_ROW_SCHEMA: dict[str, str] = {
    "agentId": "uuid", "latestRootBlobId": "digest", "name": "word", "createdAt": "time", "mode": "word",
    "isRunEverything": "flag", "blobEncryptionKey": "digest",
}


def _no_repeated_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("repeated key")
    return dict(pairs)


def _meta_value_free(kind: str, value: Any, run: _Run, vocabulary: frozenset[str]) -> bool:
    if kind == "flag":
        return isinstance(value, bool)
    if kind == "time":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 14
    if not isinstance(value, str):
        return False
    low = value.lower().encode()
    if kind == "word":
        return value in vocabulary
    if kind == "uuid":
        return bool(re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value)) and value in run.public.names
    return bool(re.fullmatch(rb"0{64}", low)) or (bool(re.fullmatch(rb"[0-9a-f]{64}", low)) and run.public.sha256.whole(low))


def _private_value_free(value: Any, run: _Run, vocabulary: frozenset[str]) -> bool:
    """A decoded meta row holds only keys of ``CURSOR_META_ROW_SCHEMA``, each once, and for each key a value of its kind:
    a number, a flag, a random UUID that names a directory of the set, zeros, a digest the public bytes yield, or a word
    of the reviewed vocabulary. Keys are checked as well as values."""
    return isinstance(value, dict) and all(key in CURSOR_META_ROW_SCHEMA and _meta_value_free(CURSOR_META_ROW_SCHEMA[key], item, run, vocabulary)
                                           for key, item in value.items())


def cursor_meta_row_proof(vocabulary: Iterable[str]) -> Callable[[_Run], bool]:
    """A proof: the run is the hex text of a JSON object that ``_private_value_free`` accepts (keys and values)."""
    allowed = frozenset(vocabulary)

    def proof(run: _Run) -> bool:
        for decoded in _hex_text_decodings(run.token):
            try:
                value = json.loads(decoded, object_pairs_hook=_no_repeated_key)
            except ValueError:
                continue
            if isinstance(value, dict) and value and _private_value_free(value, run, allowed):
                return True
        return False
    return proof


def _protobuf_strings(data: bytes, depth: int = 0) -> list[bytes] | None:
    """The printable strings of a protobuf message (nested messages read through); ``None`` when ``data`` is not a message
    or holds a binary leaf of more than eight bytes."""
    strings: list[bytes] = []
    position = 0
    while position < len(data):
        tag = 0
        for shift in range(0, 70, 7):
            if position >= len(data):
                return None
            byte = data[position]; position += 1
            tag |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        else:
            return None
        field, kind = tag >> 3, tag & 7
        if field == 0 or kind in (3, 4, 6, 7):
            return None
        if kind == 0:
            for _ in range(10):
                if position >= len(data):
                    return None
                byte = data[position]; position += 1
                if not byte & 0x80:
                    break
            else:
                return None
        elif kind in (1, 5):
            position += 8 if kind == 1 else 4
            if position > len(data):
                return None
        else:
            length = 0
            for shift in range(0, 35, 7):
                if position >= len(data):
                    return None
                byte = data[position]; position += 1
                length |= (byte & 0x7F) << shift
                if not byte & 0x80:
                    break
            else:
                return None
            payload = data[position:position + length]
            position += length
            if position > len(data):
                return None
            if not payload:
                continue
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and text.isprintable():
                strings.append(payload)
            elif depth < 6 and (inner := _protobuf_strings(payload, depth + 1)) is not None:
                strings += inner
            elif len(payload) > 8:
                return None
    return strings


def protobuf_public_strings_proof(extra_shapes: Iterable[re.Pattern[bytes]] = ()) -> Callable[[_Run], bool]:
    """A proof: the run is the hex text of a protobuf message whose printable strings are each in the public bytes
    (elsewhere in plain), uuid-shaped, or of a shape in ``extra_shapes``."""
    shapes = tuple(extra_shapes)
    uuid = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")

    def proof(run: _Run) -> bool:
        for decoded in _hex_text_decodings(run.token):
            strings = _protobuf_strings(decoded)
            if strings is None or not strings:
                continue
            if all(uuid.fullmatch(text) or any(shape.fullmatch(text) for shape in shapes) or text in run.public.blob
                   or _in_public_bytes(text.decode("utf-8"), run) for text in strings):
                return True
        return False
    return proof


def hermes_uid_proof(run: _Run) -> bool:
    """A Hermes uid: a UUID4 without dashes that sits in the row's own uid field. A ``message_uid`` is the field of a row
    that also has an integer ``id``; a ``tool_call_uid`` or a value of the ``tool_call_uids`` map is also the
    ``tool_call_uid`` of a row of the same file."""
    text = run.token.decode().lower()
    if not re.fullmatch(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", text):
        return False
    rows: list[dict[str, Any]] = []

    def collect(value: Any, depth: int = 0) -> None:
        if isinstance(value, dict):
            rows.append(value)
            for item in value.values():
                collect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                collect(item, depth + 1)

    for document in run.documents():
        collect(document)
    own_message = any(row.get("message_uid") == text and isinstance(row.get("id"), int) for row in rows)
    own_tool = any(row.get("tool_call_uid") == text for row in rows)
    return own_message or own_tool


def _in_public_bytes(text: str, run: _Run) -> bool:
    """The string is in the public bytes: as it stands, or as the content of a JSON string (escaped up to four times).
    A string of several lines counts when each line does."""
    if "\n" in text:
        return all(_in_public_bytes(line, run) for line in text.split("\n") if line)
    forms = [text]
    for ascii_only in (False, True):
        form = text
        for _ in range(4):  # JSON text inside a JSON string, up to four levels
            form = json.dumps(form, ensure_ascii=ascii_only)[1:-1]
            forms.append(form)
    return any(form.encode("utf-8", "surrogateescape") in run.public.blob for form in forms)


def _public_json(value: Any, run: _Run, depth: int = 0) -> bool:
    """A decoded JSON value adds nothing to the public bytes: every string is uuid-shaped, a calendar time, or in the
    public bytes in plain."""
    if isinstance(value, dict):
        return depth < 8 and all(_public_json(key, run) and _public_json(item, run, depth + 1) for key, item in value.items())
    if isinstance(value, list):
        return depth < 8 and all(_public_json(item, run, depth + 1) for item in value)
    if isinstance(value, str):
        return bool(_UUID_TEXT.fullmatch(value) or _ISO_TIME.fullmatch(value) or _in_public_bytes(value, run))
    return value is None or isinstance(value, (bool, int, float))


_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[47][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_ISO_TIME = re.compile(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,9})?Z")


def zstd_json_frame_proof(run: _Run) -> bool:
    """A proof: the run is the hex text of a Zstandard frame of one JSON document that adds nothing to the public bytes
    (``_public_json``). The frame is a compressed copy of public text; it is not a digest."""
    try:
        import compression.zstd as zstd
        document = json.loads(zstd.decompress(bytes.fromhex(run.token.decode())), object_pairs_hook=_no_repeated_key)
    except Exception:
        return False
    return _public_json(document, run)


def _per_packet_values(run: _Run, name: str) -> dict[str, dict[str, set[str]]]:
    """``{segment or tool name: {packet: {hash}}}`` of the Copilot request records of the set (``name`` is ``segment`` or ``tool``)."""
    found: dict[str, dict[str, set[str]]] = {}
    for file, data in run.public.contents.items():
        if not file.endswith(("events.jsonl", ".stdout")):
            continue
        for document in run.public.documents(file, data):
            stack = [document]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    if name == "segment" and isinstance(value.get("segment"), str) and isinstance(value.get("hash"), str):
                        found.setdefault(value["segment"], {}).setdefault(file.split("/")[0], set()).add(value["hash"])
                    if name == "tool" and isinstance(value.get("name"), str) and isinstance(value.get("schema_hash"), str):
                        found.setdefault(value["name"], {}).setdefault(file.split("/")[0], set()).add(value["schema_hash"])
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    return found


def copilot_vendor_hash_proof(run: _Run) -> bool:
    """A Copilot request record names the system prompt segments and the tool schemas with a 12-character hash. A hash is
    accepted when it is the same in every packet of the set for the same segment or tool name. A segment whose text
    holds a per-run value (the working directory, the session folder) has a hash that differs between the packets and
    is not accepted."""
    text = run.token.decode().lower()
    for kind in ("segment", "tool"):
        table = run.public.memo("copilot-" + kind, lambda kind=kind: _per_packet_values(run, kind))
        for packets in table.values():
            values = [value for hashes in packets.values() for value in hashes]
            if text in values and len(packets) >= 2 and all(hashes == next(iter(packets.values())) and len(hashes) == 1 for hashes in packets.values()) \
                    and len(packets) == len({file.split("/")[0] for file in run.public.contents if file.endswith("/manifest.json")}):
                return True
    return False


def length_prefixed_proof(run: _Run) -> bool:
    """The run is a protobuf string whose length byte (the byte before it) is the length of the run."""
    return run.start > 0 and run.view[run.start - 1] == len(run.token) and run.view[run.start - 2:run.start - 1] == b'"'


COPILOT_VENDOR_HASH_PIN: dict[str, Any] = {"count": 1008, "sha256": "51c18a4537f67f7e1a880386d82fa13b939c03cb17c65afd3861823e950faf63"}  # 25 tools, 17 segments
ANTIGRAVITY_REQUEST_HEX_PIN: dict[str, Any] = {"count": 25, "sha256": "6e08a1fe85da4aa30b0c92a126e46239aad412fe07f2b5167b77014d2e771157"}


# Written rules, per configuration, for hex runs of the public sets that were built before this check existed
# (judged 2026-10-07, ``artifacts/v1-expanded-preparation/legacy-hex-run-judgment-2026-10-07.md``, and proved again by the
# hardened gate after the outside review of candidate v38, findings 9, 11 and 12). The rules live
# here and not in the sanitizers, so that the sanitizer scripts of those rows stay unchanged and the ranked sets
# need no rebuild. ``check_public_packets`` adds the rules of the configuration of the set to the rules the caller
# passes. A rule matches a place and then proves the value (see above); a value that fails the proof is unlisted.
CONFIGURATION_HEX_ALLOWLIST: dict[str, list[dict[str, Any]]] = {
    "cursor-cli": [
        {"file": "store.db", "before": rb'\x04\x0f.\]', "proof": cursor_meta_row_proof(("New Agent", "default")),
         "reason": "hex text of the meta row JSON of a chat store; decoded, it holds only a random agent id that names a directory of the set, "
                   "a public blob id, a zeroed key, numbers, flags and two fixed words"},
    ],
    "copilot": [
        {"file": "rewind-file-snapshots/index.json", "before": rb'"encoding": "unix-bytes",\s*"data": "', "proof": _record_path_proof,
         "reason": "hex text of the public aliased file path that stands in the same record (path and resolvedPath)"},
        {"file": "*", "before": rb'"(?:schema_hash|hash)":"', "proof": copilot_vendor_hash_proof, "pin": COPILOT_VENDOR_HASH_PIN,
         "reason": "12-character hash of a vendor system prompt segment or tool schema; equal in every packet of the set, so it holds no per-run value"},
    ],
    "codex-cli": [
        {"file": ".jsonl", "before": rb'"commit_hash":"', "proof": pinned_value("ba333c530ea2a561b5eea3a3f8772c209405eeff"), "pin": {"count": 3},
         "reason": "commit of the session-bench repository itself (an ancestor of main); public with the repository"},
    ],
    "hermes": [
        {"file": "session-store-rows.json", "before": rb'(?:"message_uid": "|"tool_call_uid": "|call_[A-Za-z0-9]{0,40}\\+": \\+")', "proof": hermes_uid_proof,
         "pin": {"count": 68}, "reason": "random UUID4 handle issued by the Hermes store, in the field of its own row; no relation to content"},
    ],
    "antigravity": [
        {"file": "shared-store-extract.json", "before": rb'(?:"hex":"|"entries_with_this_id":\["|\[")', "proof": protobuf_public_strings_proof(),
         "reason": "hex text of a protobuf conversation summary; every string in it is in the public bytes or is a random UUID"},
        {"file": ".db", "before": rb'req_vrtx_[0-9A-Za-z]{24}"[\x0c-\x20]', "proof": length_prefixed_proof, "pin": ANTIGRAVITY_REQUEST_HEX_PIN,
         "reason": "15 or 16 character hex string (a 64-bit number) that follows the provider request id in the conversation database; "
                   "no hash of any string of the private parent matches it, the sanitizer leaves it as captured"},
    ],
    "openclaw": [
        {"file": "native/capture/session-store-rows.json", "before": rb'"blob_hex": "', "proof": zstd_json_frame_proof, "pin": {"count": 3},
         "reason": "bytes of a Zstandard frame of one transcript event (the first prompt), as hex; the decoded JSON holds only public strings"},
    ],
}


def _digest_keyed_short(documents: Iterable[Any]) -> set[str]:
    """Lower-case hex strings of 6 to 11 characters that are the value of a digest-like key in the JSON documents (also JSON
    text held in a string). A key passes its name to the values below it until an inner key with a digest-like name
    replaces it."""
    found: set[str] = set()

    def walk(value: Any, key: str, depth: int = 0) -> None:
        if isinstance(value, dict):
            for inner, item in _items(value):
                walk(item, inner if _DIGEST_KEY.search(str(inner)) else key, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, key, depth + 1)
        elif isinstance(value, str):
            if key and re.fullmatch(r"[0-9a-fA-F]{6,11}", value):
                found.add(value.lower())
            start = value.find("{") if value[:1] not in ("{", "[") else 0
            if value and start >= 0 and depth < 12:
                try:
                    walk(_loads(value[start:]), key, depth + 1)
                except ValueError:
                    pass

    for document in documents:
        walk(document, "")
    return found


def long_hex_runs(contents: Mapping[str, bytes], *, hex_allowlist: Iterable[Mapping[str, Any]] = (),
                  public_references: Mapping[str, bytes] | None = None, provider_id_fields: Iterable[str] = (),
                  minimum: int = SHORT_HEX_MINIMUM, extra_names: Iterable[str] = ()) -> dict[str, Any]:
    """Classify every hex run of ``minimum`` (12) or more characters of a public set that is not a 64-hex digest.

    The runs of every file, of a Zstandard file after decompression, and of every file name are classified; the name of
    a file is classified with its whole path, and ``extra_names`` are other names of the set (directories, links). In
    a name a run of exactly 64 characters is classified too. A hex value of 6 to ``minimum`` - 1 characters is classified
    only when it is the value of a digest-like JSON key (``_DIGEST_KEY``: hash, digest, sha, checksum, fingerprint, etag,
    signature and the like), and then it must be zeros, derivable, or proved by a rule; no shape accepts it. Classes:

    - ``zero``: only zeros (a zeroed digest);
    - ``decimal``: only decimal digits, as an unquoted JSON number or as a Unix time (10, 13, 16 or 19 digits);
    - ``constant``: the alphabet ``0123456789abcdef``;
    - ``derivable``: ALL characters of the run are the start of one complete SHA-256, SHA-1 or MD5 digest that the
      public bytes yield: of a file, a line, a JSON string value or key (also JSON text inside a string), or an absolute
      path string. A run longer than 64 characters is derivable only in parts: the format rule of ``_sqlite_frame``
      (a parsed SQLite cell whose TEXT field is exactly a public digest and whose other run characters are framing of
      parsed cells; a BLOB field, a freeblock and the overflow part of a cell give nothing), or a
      split around a complete public SHA-256 digest into remainders that each pass on their own (``_joined_derivable``);
    - ``uuid``: the last group of a version 4 or version 7 UUID (random UUIDs; a name-based UUID is a digest and is not accepted);
    - ``time_id``: the time part of an OpenCode id (``ses_``, ``msg_``, ``prt_``, ``evt_``) whose clock is near a public timestamp;
    - ``fragment``: a part of a run already accepted as ``time_id`` (a database index page keeps a key without its
      leading bytes);
    - ``provider_id``: the hex of a provider id (``PROVIDER_ID_SHAPES``) that is exactly a JSON string value or key,
      under a field of ``provider_id_fields``, with a label ending in ``:`` or ``|`` allowed before it;
    - ``allowlisted``: a rule of ``hex_allowlist`` matches the place and proves the value (``_hex_rules``);
    - ``unlisted``: anything else. Each is reported with its file, its length and the 24 bytes before it.

    A token of exactly 64 lowercase hex characters is not counted here: ``classify_public_digests`` decides it.
    """
    rules, ignored = _hex_rules(hex_allowlist)
    public = _Public(contents, benchmark_public_references() if public_references is None else public_references)
    fields = frozenset(provider_id_fields)
    scan = min(minimum, SHORT_KEYED_MINIMUM)
    pattern = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{%d,}(?![0-9A-Fa-f])" % scan)
    classes = {"zero": 0, "decimal": 0, "constant": 0, "derivable": 0, "uuid": 0, "time_id": 0, "provider_id": 0, "fragment": 0, "allowlisted": 0}
    unlisted: list[dict[str, Any]] = []
    shaped: set[bytes] = set()
    accepted: list[list[tuple[str, bytes]]] = [[] for _ in rules]
    for name in sorted({*contents, *extra_names}):
        data = contents.get(name, b"")
        views = [(name, name.encode(), name + "#name")]
        if name in contents:
            views.insert(0, (name, data, name))
            plain = _decompressed(name, data)
            if plain is not None:
                views.append((name, plain, name + "#zst"))
        for label, view, key in views:
            budget: collections.Counter | None = None
            keyed: set[str] | None = None
            for match in pattern.finditer(view):
                token = match.group(0)
                if _HEX64_EXACT.fullmatch(token) and not key.endswith("#name"):
                    continue  # a digest in a file: classify_public_digests decides it; in a name this gate decides it
                gated = len(token) < minimum  # 6 to 11 characters: only the value of a digest-like key
                if gated:
                    if keyed is None:
                        keyed = _digest_keyed_short(public.documents(key, view))
                    if token.lower().decode() not in keyed:
                        continue
                run = _Run(label, view, match, public, key)
                low = token.lower()
                kind = None
                if set(token) == {ord("0")}:
                    kind = "zero"
                elif not gated and token.isdigit() and _decimal_shape(run):
                    kind = "decimal"
                elif not gated and token in _CONSTANTS:
                    kind = "constant"
                elif _whole_derivable(run, low):
                    kind = "derivable"
                elif gated:
                    pass
                elif _uuid_shape(run):
                    kind = "uuid"
                elif _time_id_shape(run):
                    kind = "time_id"
                else:
                    prefix = _PROVIDER_BEFORE.search(view[max(0, match.start() - 8):match.start()])
                    if prefix and fields and len(token) in PROVIDER_ID_SHAPES.get(prefix.group(1).decode(), ()):
                        field = _PROVIDER_FIELD_BEFORE.search(view[max(0, match.start() - 260):match.start()])
                        if field and field["field"].decode() in fields and _PROVIDER_FIELD_AFTER.match(view[match.end():match.end() + 210]):
                            kind = "provider_id"  # the whole value of a reviewed field of a JSON object
                        else:  # the id is a list item or an object key: count it against the ids that the parsed JSON holds there
                            if budget is None:
                                budget = _provider_budget(run, fields)
                            identifier = prefix.group(1) + b"_" + token.lower()
                            if budget[identifier] > 0:
                                budget[identifier] -= 1
                                kind = "provider_id"
                if kind is not None:
                    classes[kind] += 1
                    if kind == "time_id":
                        shaped.add(low)
                    continue
                before = view[max(0, match.start() - 64):match.start()]
                for index, rule in enumerate(rules):
                    suffix = rule["file"]
                    if (suffix == "*" or name == suffix or name.endswith("/" + suffix) or name.endswith(suffix)) and rule["pattern"].search(before) and rule["proof"](run):
                        accepted[index].append((name, token))
                        kind = "rule"
                        break
                if kind is None:
                    unlisted.append({"file": name, "length": len(token), "before": before[-24:].decode("utf-8", "replace"), "_token": low})
    # A key that a database index page stores without its leading bytes: a part of a run that is already accepted as a time id.
    pool = b"\0".join(sorted(shaped))
    kept = []
    for item in unlisted:
        if item["_token"] in pool:
            classes["fragment"] += 1
        else:
            kept.append(item)
    unlisted = kept
    reasons: dict[str, int] = {}
    for rule, hits in zip(rules, accepted):
        pin = rule.get("pin")
        if pin is not None and (len(hits) != pin["count"] or ("sha256" in pin and hashlib.sha256(b"\n".join(sorted({token.lower() for _, token in hits}))).hexdigest() != pin["sha256"])):
            for name, token in hits:
                unlisted.append({"file": name, "length": len(token), "before": "(rule pin failed)", "_token": token.lower()})
            continue
        classes["allowlisted"] += len(hits)
        if hits:
            reasons[rule["reason"]] = reasons.get(rule["reason"], 0) + len(hits)
    unlisted.sort(key=lambda item: (item["file"], item["length"], item["before"]))
    for item in unlisted:
        item.pop("_token", None)
    return {"classes": classes, "allowlisted_by_reason": reasons, "unlisted": unlisted, "legacy_rules_ignored": ignored, "minimum": minimum}


def check_public_packets(packets: Iterable[Path | str], *, receipts: Iterable[Any] = (), extra_files: Iterable[Path | str] = (),
                         allowlist: Mapping[str, Mapping[str, str]] | None = None,
                         hex_allowlist: Iterable[Mapping[str, Any]] = (), hex_runs: str = "fail") -> dict[str, Any]:
    """The one check every sanitizer runs on the set it wrote; it raises on an unbound digest or an unlisted hex run.

    ``receipts`` are the replay receipts of the packets (the digests the
    closed replay recomputes). ``extra_files`` are other public files of the
    set, such as the public inputs bundle. ``hex_allowlist`` are the rules for
    hex runs of 12 or more characters that are not 64-hex digests
    (``long_hex_runs``). ``hex_runs`` is ``fail`` (the default: an unlisted run
    raises ``UnlistedHexRunError``) or ``report`` (the result lists them and
    nothing is raised; for a diagnosis only). A set whose packets are all of
    one configuration of ``CONFIGURATION_HEX_ALLOWLIST`` and ``PROVIDER_ID_FIELDS`` also uses the rules and the fields of it.
    """
    if hex_runs not in ("fail", "report"):
        raise ValueError("hex_runs must be fail or report")
    packets, extra_files = list(packets), list(extra_files)  # an iterator is read once here; the names are read from the packets again
    outputs = [json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode() for receipt in receipts]
    contents = read_public_set(packets, extra_files)
    names = public_entry_names(packets)
    result = require_bound_public_digests(contents, replay_outputs=outputs, allowlist=allowlist, extra_names=names)
    configurations = set()
    for name, data in contents.items():
        if name.count("/") == 1 and name.endswith("/manifest.json"):
            try:
                configurations.add(json.loads(data).get("configuration_id"))
            except (ValueError, AttributeError):
                configurations.add(None)
    configuration = next(iter(configurations)) if len(configurations) == 1 else None
    result["hex_runs"] = long_hex_runs(contents, hex_allowlist=[*hex_allowlist, *CONFIGURATION_HEX_ALLOWLIST.get(configuration, [])],
                                       provider_id_fields=PROVIDER_ID_FIELDS.get(configuration, ()), extra_names=names)
    found = result["hex_runs"]["unlisted"]
    if found and hex_runs == "fail":
        files = sorted({item["file"] for item in found})
        raise UnlistedHexRunError(f"{len(found)} unlisted hex run(s) of {SHORT_HEX_MINIMUM} or more characters in public set: " + ", ".join(files[:12]))
    return result


def short_digest_warnings(contents: Mapping[str, bytes]) -> list[dict[str, str]]:
    """Short hex values under digest-like keys of public JSON that public bytes do not yield.

    ``check_public_packets`` sees only 64-hex tokens. This list covers the
    shorter ones: a string of 6 to 63 hex characters (not all zeros; a 32-bit
    digest can print without its leading zeros) under a
    key whose name holds hash, digest, fingerprint, checksum, sha, md5 or crc,
    in a JSON file, a JSON line or JSON text inside a string. A value is
    derivable when it is the start of a SHA-256 digest that the public bytes
    yield, or the MD5 or SHA-1 digest of a public file or one of its lines.
    The result is a list for a reviewer, one row per file, key and value. It
    is a warning list; a sanitizer may choose to fail on it.
    """
    computable: set[bytes] = set()
    other: set[str] = set()
    for name, data in {**benchmark_public_references(), **contents}.items():
        computable |= public_byte_digests(data, name)
        for piece in [data, *data.split(b"\n")]:
            other.add(hashlib.md5(piece).hexdigest()); other.add(hashlib.sha1(piece).hexdigest())
    prefixes = sorted(value.decode() for value in computable)

    def derivable(token: str) -> bool:
        import bisect
        low = token.lower()
        at = bisect.bisect_left(prefixes, low)
        return set(low) == {"0"} or low in other or (at < len(prefixes) and prefixes[at].startswith(low))

    found: list[dict[str, str]] = []

    def walk(value: Any, key: str, name: str, depth: int = 0) -> None:
        if isinstance(value, dict):
            for inner, item in _items(value):
                walk(item, inner if _DIGEST_KEY.search(str(inner)) else key, name, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, key, name, depth + 1)
        elif isinstance(value, str):
            if key and _SHORT_HEX.fullmatch(value) and not derivable(value):
                found.append({"file": name, "key": key, "value": value})
            start = 0 if value[:1] in "{[" else -1
            if start == 0 and depth < 12:
                try:
                    walk(_loads(value), key, name, depth + 1)
                except ValueError:
                    pass

    for name in sorted(contents):
        data = contents[name]
        try:
            documents = [_loads(data)]
        except ValueError:
            documents = []
            for line in data.split(b"\n"):
                if line[:1] in (b"{", b"["):
                    try:
                        documents.append(_loads(line))
                    except ValueError:
                        pass
        for document in documents:
            walk(document, "", name)
    return found
