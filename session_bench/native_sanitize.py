"""Deterministic JSONL path sanitization for public synthetic evidence derivatives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


FORBIDDEN_PUBLIC_MARKERS = (
    "/Users/",
    "BEGIN PRIVATE KEY",
    "Authorization: Bearer",
    "api_key=",
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _replace(value: Any, replacements: tuple[tuple[str, str], ...]) -> tuple[Any, int]:
    if isinstance(value, str):
        count = 0
        result = value
        for source, target in replacements:
            seen = result.count(source)
            if seen:
                result = result.replace(source, target)
                count += seen
        return result, count
    if isinstance(value, list):
        result, count = [], 0
        for item in value:
            changed, additions = _replace(item, replacements)
            result.append(changed)
            count += additions
        return result, count
    if isinstance(value, dict):
        result, count = {}, 0
        for key, item in value.items():
            changed_key, key_additions = _replace(key, replacements)
            if changed_key in result:
                raise ValueError("sanitization produced duplicate object keys")
            changed, additions = _replace(item, replacements)
            result[changed_key] = changed
            count += key_additions + additions
        return result, count
    return value, 0


def sanitize_jsonl_paths(
    source: Path,
    destination: Path,
    *,
    replacements: Mapping[str, str],
) -> dict[str, Any]:
    """Write a labeled derivative while retaining a receipt for the withheld raw file."""

    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("sanitized destination already exists")
    pairs = tuple(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))
    if not pairs or any(not source_text or not target for source_text, target in pairs):
        raise ValueError("sanitization replacements must be non-empty")
    raw = source.read_bytes()
    output: list[str] = []
    replacement_count = 0
    for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"native JSONL line {number} is invalid") from exc
        changed, count = _replace(value, pairs)
        replacement_count += count
        output.append(json.dumps(changed, ensure_ascii=False, separators=(",", ":")))
    encoded = ("\n".join(output) + "\n").encode("utf-8")
    text = encoded.decode("utf-8")
    findings = [marker for marker in FORBIDDEN_PUBLIC_MARKERS if marker in text]
    if findings:
        raise ValueError(f"sanitized derivative retains forbidden markers: {findings}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    return {
        "kind": "sanitized_native_derivative",
        "raw_publication_state": "withheld",
        "raw_sha256": _digest(raw),
        "sanitized_sha256": _digest(encoded),
        "line_count": len(output),
        "replacement_count": replacement_count,
        "rules": [{"source": key, "target": value} for key, value in pairs],
    }


def sanitize_json_document(
    source: Path,
    destination: Path,
    *,
    replacements: Mapping[str, str],
) -> dict[str, Any]:
    """Write one canonical, labeled JSON derivative with a raw-byte receipt."""
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("sanitized destination already exists")
    pairs = tuple(sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True))
    if not pairs or any(not source_text or not target for source_text, target in pairs):
        raise ValueError("sanitization replacements must be non-empty")
    raw = source.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("native JSON document is invalid") from exc
    changed, replacement_count = _replace(value, pairs)
    encoded = (json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    findings = [marker for marker in FORBIDDEN_PUBLIC_MARKERS if marker in encoded.decode("utf-8")]
    if findings:
        raise ValueError(f"sanitized derivative retains forbidden markers: {findings}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    return {
        "kind": "sanitized_json_derivative",
        "raw_publication_state": "withheld",
        "raw_sha256": _digest(raw),
        "sanitized_sha256": _digest(encoded),
        "replacement_count": replacement_count,
        "rules": [{"source": key, "target": value} for key, value in pairs],
    }
