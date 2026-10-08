#!/usr/bin/env python3
"""Build one review-pending Claude Desktop n=1 public replay candidate.

The input is an already closed private score-replay packet for prospectively
designated repetition 1.  This command never discovers a Claude root.  It
creates a new, explicitly transformed successor, pins the complete private
parent by content inventory, reruns the scorer and tamper controls, and emits
the singleton public-input schema required by the n=1 release verifier.

The output is only a candidate.  Public safety and independent reproduction
remain false until a separate operator reviews the exact output bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from session_bench.native_replay import canonical, _snapshot_tree  # noqa: E402
from session_bench.release_evidence import RELEASE_EVIDENCE_SCHEMA_VERSION  # noqa: E402
from session_bench.release_replay import PUBLIC_BUNDLE_N1_SCHEMA  # noqa: E402
from session_bench.release_score import score_release_run  # noqa: E402
from session_bench.score_replay import (  # noqa: E402
    build_score_replay_package,
    replay_score_package,
    verify_score_packet_tamper_controls,
)


HOME = re.compile(rb"/Users/[A-Za-z0-9._-]+")
EMAIL = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UUID = re.compile(
    rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    rb"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
PRIVATE_TEMP = re.compile(rb"/(?:private/(?:tmp|var)|var/folders)/[A-Za-z0-9._/+\-=@%-]+")
SECRET = re.compile(
    rb"(?:sk-ant-|sk-proj-|github_pat_|gh[op]_)[A-Za-z0-9_-]{12,}"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    rb"|\b(?:Bearer|Basic) [A-Za-z0-9._~+/=-]{12,}",
    re.IGNORECASE,
)
SECRET_FIELD = re.compile(
    rb'"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|secret)"\s*:\s*"(?!")',
    re.IGNORECASE,
)
RAW_OTEL_MARKER = re.compile(
    rb'"(?:resourceLogs|scopeLogs|logRecords|resourceSpans)"\s*:|gen_ai\.(?:prompt|response)',
    re.IGNORECASE,
)
VENDOR_EMAILS = {b"noreply@anthropic.com"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _token(seed: bytes, size: int) -> bytes:
    alphabet = b"abcdefghijklmnopqrstuvwxyz0123456789"
    digest = hashlib.sha256(seed).digest()
    return bytes(alphabet[digest[index % len(digest)] % len(alphabet)] for index in range(size))


def _same_size_email(value: bytes) -> bytes:
    suffix = b"@example.test"
    if len(value) <= len(suffix):
        raise ValueError("private email is too short for a same-size public alias")
    return _token(value, len(value) - len(suffix)) + suffix


def _same_size_uuid(value: bytes) -> bytes:
    digest = hashlib.sha256(value).hexdigest().encode()
    return digest[:8] + b"-" + digest[8:12] + b"-4" + digest[13:16] + b"-8" + digest[17:20] + b"-" + digest[20:32]


def _aliases(documents: Mapping[str, bytes]) -> dict[bytes, bytes]:
    combined = b"\n".join(documents.values())
    if SECRET.search(combined) or SECRET_FIELD.search(combined):
        raise ValueError("credential-like material requires a separately reviewed transform")
    if RAW_OTEL_MARKER.search(combined):
        raise ValueError("raw OTel prompt/response envelope must not enter a public packet")
    for name, data in documents.items():
        if ("otel" in name.lower()
                and re.search(rb'"(?:body|text|prompt|response)"\s*:\s*"(?!")', data, re.IGNORECASE)):
            raise ValueError("OTel text must not enter a public packet")

    aliases: dict[bytes, bytes] = {}
    homes = set(HOME.findall(combined))
    for value in homes:
        aliases[value] = b"/Users/" + _token(value, len(value) - len(b"/Users/"))
        username = value.rsplit(b"/", 1)[-1]
        if username:
            aliases[username] = _token(b"username:" + username, len(username))
    for value in set(EMAIL.findall(combined)) - VENDOR_EMAILS:
        aliases[value] = _same_size_email(value)
    for value in set(UUID.findall(combined)):
        aliases[value] = _same_size_uuid(value)
    for value in set(PRIVATE_TEMP.findall(combined)):
        prefix = next(prefix for prefix in (b"/private/tmp/", b"/private/var/", b"/var/folders/") if value.startswith(prefix))
        aliases[value] = prefix + _token(b"temp:" + value, len(value) - len(prefix))
    if any(len(source) != len(target) for source, target in aliases.items()):
        raise AssertionError("privacy aliases must preserve physical byte length")
    return aliases


def transform_documents(original: Mapping[str, bytes]) -> tuple[dict[str, bytes], dict[str, int]]:
    """Alias private literals and transitively repair embedded SHA-256 values."""
    aliases = _aliases(original)
    base: dict[str, bytes] = {}
    ordered = sorted(aliases.items(), key=lambda item: -len(item[0]))
    for name, source_data in original.items():
        data = source_data
        for source, target in ordered:
            data = data.replace(source, target)
        base[name] = data

    transformed = dict(base)
    for _ in range(len(original) + 2):
        digest_aliases = {
            sha(original[name]).encode(): sha(data).encode()
            for name, data in transformed.items()
            if original[name] != data
        }
        revised: dict[str, bytes] = {}
        for name, source_data in base.items():
            data = source_data
            for source, target in digest_aliases.items():
                data = data.replace(source, target)
            revised[name] = data
        if revised == transformed:
            break
        transformed = revised
    else:
        raise ValueError("digest dependency graph did not converge")

    if any(len(original[name]) != len(data) for name, data in transformed.items()):
        raise ValueError("privacy transformation changed a source document byte count")
    if any(source in data for source in aliases for data in transformed.values()):
        raise ValueError("private literal survived the transformation")
    combined = b"\n".join(transformed.values())
    if SECRET.search(combined) or SECRET_FIELD.search(combined) or RAW_OTEL_MARKER.search(combined):
        raise ValueError("forbidden private material survived the transformation")
    return transformed, {
        "home_alias_count": len(set(HOME.findall(b"\n".join(original.values())))),
        "email_alias_count": len(set(EMAIL.findall(b"\n".join(original.values()))) - VENDOR_EMAILS),
        "uuid_alias_count": len(set(UUID.findall(b"\n".join(original.values())))),
        "temporary_path_alias_count": len(set(PRIVATE_TEMP.findall(b"\n".join(original.values())))),
    }


def _packet_inventory(tree: Mapping[str, bytes]) -> tuple[list[dict[str, object]], str]:
    rows = [
        {"path": name, "sha256": sha(data), "size_bytes": len(data)}
        for name, data in sorted(tree.items())
    ]
    return rows, sha(canonical(rows))


def _public_survival(packet: Path, actual: Mapping[str, object]) -> dict[str, object]:
    observer = json.loads((packet / "inputs/observer.json").read_bytes())
    responses = [event for event in observer["events"] if event.get("kind") == "assistant_response"]
    models = {event.get("fields", {}).get("model_id") for event in responses}
    configurations = {event.get("fields", {}).get("configuration") for event in responses}
    if len(models) != 1 or None in models or len(configurations) != 1 or None in configurations:
        raise ValueError("captured Desktop model identity is missing or unstable")
    profile = actual["format_evidence"]
    native = json.loads((packet / "native/decode.json").read_bytes())
    artifacts = native.get("artifacts", [])
    if not artifacts:
        raise ValueError("public packet has no native artifact inventory")
    profile_evidence = {row["metric_id"]: row for row in profile["metric_evidence"]}
    evidence = []
    for metric in actual["measurement"]["metrics"]:
        metric_id = metric["id"]
        observer_ids = [
            event["id"] for event in observer["events"]
            if metric_id in event.get("metric_ids", [])
        ]
        observer_ids.extend(
            relation["id"] for relation in observer.get("relations", [])
            if metric_id in relation.get("metric_ids", [])
        )
        if not observer_ids:
            observer_ids = profile_evidence.get(metric_id, {}).get("observer_ids", [])
        observer_ids = list(dict.fromkeys(observer_ids or ["closed-replay-observer"]))
        locators = [
            {
                "artifact_id": "native/" + artifact["path"],
                "artifact_sha256": artifact["sha256"],
                "record_location": "complete sanitized Claude Desktop session; replay joins stable event identities",
            }
            for artifact in artifacts
        ]
        evidence.append({"metric_id": metric_id, "observer_ids": observer_ids, "native_locators": locators})
    decoder_path = packet / "runtime/session_bench/adapters/claude_code_decoder.py"
    survival = {
        "schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION,
        "protocol_version": "1.0-survival",
        "workload_version": "1.0-survival-workload",
        "rubric_version": "1.0-survival-rubric",
        "run_id": actual["run_id"],
        "configuration_id": "claude-desktop",
        "repetition": 1,
        "capture_id": packet.name,
        "evaluation_id": "native-score-replay:" + packet.name,
        "observer": profile["observer"],
        "native_manifest": profile["native_manifest"],
        "decoder": {"id": "claude-code-jsonl-v1", "sha256": sha(decoder_path.read_bytes())},
        "identity": {
            "provider": "anthropic",
            "harness": "claude",
            "surface": "desktop",
            "execution_mode": "gui",
            "os": "macOS",
            "build": profile["build"],
            "model": next(iter(models)),
            "configuration": next(iter(configurations)),
            "observer_schema_version": observer["schema_version"],
        },
        "measurement": actual["measurement"],
        "metric_evidence": evidence,
    }
    score_release_run(survival, profile)
    return survival


def build(source_packet: Path, output: Path) -> dict[str, object]:
    source = Path(source_packet).resolve(strict=True)
    destination = Path(output)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("source packet must be one ordinary closed directory")
    if destination.exists() or destination.is_symlink():
        raise ValueError("new destination required")

    source_tree = _snapshot_tree(source)
    parent_manifest = json.loads(source_tree.get("manifest.json", b"null"))
    if (not isinstance(parent_manifest, dict)
            or parent_manifest.get("configuration_id") != "claude-desktop"
            or parent_manifest.get("repetition") != 1):
        raise ValueError("n=1 candidate requires one Claude Desktop repetition-1 score packet")
    inventory, parent_packet_sha = _packet_inventory(source_tree)
    parent_manifest_sha = sha(source_tree["manifest.json"])
    original = {
        name: data for name, data in source_tree.items()
        if name.startswith(("native/", "inputs/"))
    }
    required = {"native/decode.json", "inputs/workload.json", "inputs/observer.json", "inputs/context.json"}
    if not required <= set(original):
        raise ValueError("source packet lacks required replay inputs")
    sensitive_originals = set(_aliases(original))
    transformed, counts = transform_documents(original)
    source_bytes = Path(__file__).read_bytes()
    transformation = {
        "schema_version": "session-bench-claude-desktop-public-transformation-n1-v1",
        "parent_manifest_sha256": parent_manifest_sha,
        "parent_packet_inventory_sha256": parent_packet_sha,
        "parent_packet_inventory": inventory,
        "original_is_private": True,
        "privacy_approval": "pending_independent_review",
        "description": "Same-size aliases for private paths, account strings, opaque UUIDs, and temporary paths; raw OTel envelopes and credential-like material are rejected; embedded digest references are rebound transitively.",
        "aliases": counts,
        "transformer_source_sha256": sha(source_bytes),
        "files": [
            {"path": name, "original_sha256": sha(original[name]), "transformed_sha256": sha(data), "size_bytes": len(data)}
            for name, data in sorted(transformed.items())
        ],
    }

    packet = destination / source.name
    with tempfile.TemporaryDirectory(prefix="claude-desktop-n1-public-") as folder:
        native = Path(folder).resolve()
        for name, data in transformed.items():
            if name.startswith("native/"):
                _write(native / name.removeprefix("native/"), data)
        supporting = {
            name.removeprefix("inputs/"): data
            for name, data in transformed.items()
            if name.startswith("inputs/") and name not in {
                "inputs/workload.json", "inputs/observer.json", "inputs/context.json",
            }
        }
        supporting["public-transformation.json"] = canonical(transformation)
        supporting["transformer-source.py"] = source_bytes
        build_score_replay_package(
            native,
            packet,
            workload_document=transformed["inputs/workload.json"],
            observer_document=transformed["inputs/observer.json"],
            context_document=transformed["inputs/context.json"],
            supporting_documents=supporting,
            source_root=source / "runtime",
        )

    pin = sha((packet / "manifest.json").read_bytes())
    before = replay_score_package(source, expected_manifest_sha256=parent_manifest_sha, os_sandboxed=True)
    after = replay_score_package(packet, expected_manifest_sha256=pin, os_sandboxed=True)
    if canonical(before["diagnostics"]["intact"]["metrics"]) != canonical(after["diagnostics"]["intact"]["metrics"]):
        raise ValueError("sanitization changed metric results")
    controls = verify_score_packet_tamper_controls(packet, expected_manifest_sha256=pin)
    if controls.get("status") != "passed":
        raise ValueError("candidate tamper controls failed")
    if source_tree != _snapshot_tree(source):
        raise ValueError("private parent changed during candidate construction")

    public_tree = _snapshot_tree(packet)
    transformable_public = {name: data for name, data in public_tree.items() if name.startswith(("native/", "inputs/"))}
    # Re-running discovery must find none of the exact private literals.  The
    # deterministic same-shape aliases are intentionally permitted.
    combined = b"\n".join(transformable_public.values())
    if (any(value in combined for value in sensitive_originals)
            or SECRET.search(combined) or SECRET_FIELD.search(combined)
            or RAW_OTEL_MARKER.search(combined)):
        raise ValueError("private material remains in the complete public candidate")
    remaining_emails = set(EMAIL.findall(combined)) - VENDOR_EMAILS
    if any(not value.endswith(b"@example.test") for value in remaining_emails):
        raise ValueError("private account identifier remains in the public candidate")

    actual = after["diagnostics"]["intact"]
    survival = _public_survival(packet, actual)
    bundle = canonical({
        "schema_version": PUBLIC_BUNDLE_N1_SCHEMA,
        "configuration_id": "claude-desktop",
        "pairs": [{"survival": survival, "format": actual["format_evidence"]}],
    })
    derived_receipts = canonical(after) + b"\n" + canonical(controls) + b"\n" + bundle
    if (any(value in derived_receipts for value in sensitive_originals)
            or SECRET.search(derived_receipts) or SECRET_FIELD.search(derived_receipts)
            or RAW_OTEL_MARKER.search(derived_receipts)):
        raise ValueError("private material remains in a public receipt or bundle")
    handoff = destination / "public-handoff" / packet.name
    handoff.parent.mkdir(parents=True)
    shutil.copytree(packet, handoff, symlinks=False)
    if _snapshot_tree(packet) != _snapshot_tree(handoff):
        raise ValueError("complete public handoff differs from the replayed candidate")

    _write(destination / f"{packet.name}.producer-replay.json", canonical(after))
    _write(destination / f"{packet.name}.tamper.json", canonical(controls))
    _write(destination / "public-inputs-candidate.json", bundle)
    result = {
        "schema_version": "session-bench-claude-desktop-sanitized-candidate-n1-v1",
        "configuration_id": "claude-desktop",
        "run_id": actual["run_id"],
        "repetition": 1,
        "packet": packet.name,
        "manifest_sha256": pin,
        "parent_manifest_sha256": parent_manifest_sha,
        "parent_packet_inventory_sha256": parent_packet_sha,
        "diagnostics_sha256": after["diagnostics_sha256"],
        "public_bundle_schema": PUBLIC_BUNDLE_N1_SCHEMA,
        "public_bundle_sha256": sha(bundle),
        "complete_handoff": "public-handoff/" + packet.name,
        "all_31_metric_rows_unchanged": True,
        "public_safe": False,
        "privacy_review_pending": True,
        "independent_reproduction": False,
        "publication_eligible": False,
    }
    _write(destination / "summary.json", canonical(result))
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    build(arguments.source_packet, arguments.output)
