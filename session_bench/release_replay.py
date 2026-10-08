"""Executable release aggregate verification, with an explicit external trust root.

The caller must obtain the review digest and reviewer/producer identities through
a trusted channel separate from the proposed public bundle. A pinned review is an
operator attestation of identity, locator meaning and public safety, not a claim
that software can infer those facts. Native scoring and OS isolation are actually
executed here. Same-host review is permitted and must be disclosed by the receipt.
Unresolved metrics and missing identities retain the frozen scorer's blockers.
"""
from __future__ import annotations

import hashlib
from dataclasses import fields, is_dataclass
from fractions import Fraction
from pathlib import Path
from typing import Mapping

from .native_replay import canonical, _snapshot_tree
from .score_replay import _json, _SHA, replay_score_package, verify_score_packet_tamper_controls
from .v1_public_score import (
    CONFIGURATION_EVIDENCE_N1_SCHEMA_VERSION, CONFIGURATION_EVIDENCE_SCHEMA_VERSION,
    aggregate_public_configuration,
    aggregate_public_configuration_n1,
)

PUBLIC_BUNDLE_SCHEMA = "session-bench-release-replay-input-v1"
REVIEW_SCHEMA = "session-bench-release-replay-review-v1"
PUBLIC_BUNDLE_N1_SCHEMA = "session-bench-release-replay-input-n1-v1"
REVIEW_N1_SCHEMA = "session-bench-release-replay-review-n1-v1"
_VERIFIED: set[str] = set()


def _fingerprint(value) -> str:
    # Exact fractions and every nested run/proof: rendered decimals round values.
    def exact(item):
        if isinstance(item, Fraction):
            return {"numerator": item.numerator, "denominator": item.denominator}
        if is_dataclass(item):
            return {field.name: exact(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, Mapping):
            return {key: exact(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [exact(child) for child in item]
        return item
    return hashlib.sha256(canonical(exact(value))).hexdigest()


def is_verified_release_configuration(value) -> bool:
    """Check the exact aggregate including mutable nested evidence, not a boolean."""
    return _fingerprint(value) in _VERIFIED


def verify_release_configuration(
    public_bundle_document: bytes, independent_review_document: bytes, *,
    trusted_review_sha256: str, expected_reviewer_id: str, expected_producer_id: str,
    packets: Mapping[str, tuple[Path, str]],
    public_packets: Mapping[str, Path],
):
    """Freshly reproduce three native packets and seal their exact public aggregate.

    Review receipts cannot approve missing metrics. The strict public bundle and
    receipt are actual exact bytes: fabricated bundle/receipt SHA placeholders and
    caller-created score dataclasses never enter the qualified cohort.
    """
    if not isinstance(trusted_review_sha256, str) or not _SHA.fullmatch(trusted_review_sha256) or hashlib.sha256(independent_review_document).hexdigest() != trusted_review_sha256:
        raise ValueError("independent review differs from trusted digest")
    if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in (expected_reviewer_id, expected_producer_id)) or expected_reviewer_id == expected_producer_id:
        raise ValueError("independent reviewer and producer must be separately trusted identities")
    bundle = _json(public_bundle_document, "public replay bundle")
    review = _json(independent_review_document, "independent release review")
    bundle_sha = hashlib.sha256(public_bundle_document).hexdigest()
    if not isinstance(bundle, dict) or set(bundle) != {"schema_version", "configuration_id", "pairs"} or bundle["schema_version"] != PUBLIC_BUNDLE_SCHEMA:
        raise ValueError("unsupported public replay bundle")
    fields = {"schema_version", "reviewer_id", "producer_id", "public_bundle_sha256", "scope", "checks", "runs", "limitations"}
    if not isinstance(review, dict) or set(review) != fields or review["schema_version"] != REVIEW_SCHEMA:
        raise ValueError("unsupported independent release review")
    checks = {"independent_operator_execution", "public_safety_review", "complete_replay_packet_public_safety_review", "run_identity_review", "metric_locator_binding_review", "native_to_score_semantics_review"}
    if (review["reviewer_id"] != expected_reviewer_id or review["producer_id"] != expected_producer_id or review["public_bundle_sha256"] != bundle_sha
            or review["scope"] != "native-to-score-and-exact-public-inputs" or not isinstance(review["checks"], dict)
            or set(review["checks"]) != checks or any(value is not True for value in review["checks"].values())):
        raise ValueError("independent review does not approve exact public inputs and full scoring scope")
    if not isinstance(review["limitations"], list) or not review["limitations"] or any(not isinstance(value, str) or not value.strip() for value in review["limitations"]):
        raise ValueError("independent review must disclose its environment and acquisition limitations")
    if not isinstance(bundle["pairs"], list) or len(bundle["pairs"]) != 3 or not isinstance(review["runs"], list) or len(review["runs"]) != 3:
        raise ValueError("release verification requires exactly three reviewed repetitions")
    run_fields = {"run_id", "configuration_id", "repetition", "manifest_sha256", "diagnostics_sha256"}
    if any(not isinstance(row, dict) or set(row) != run_fields for row in review["runs"]):
        raise ValueError("independent review has malformed native run bindings")
    from .release_score import score_release_run
    scored, expected_review = [], []
    seen = set()
    for pair in bundle["pairs"]:
        if not isinstance(pair, dict) or set(pair) != {"survival", "format"}:
            raise ValueError("invalid public replay pair")
        survival, format_document = pair["survival"], pair["format"]
        run = score_release_run(survival, format_document)
        if run.configuration_id != bundle["configuration_id"] or run.run_id in seen or run.run_id not in packets or run.run_id not in public_packets:
            raise ValueError("public pair differs from complete pinned run population")
        seen.add(run.run_id)
        path, pin = packets[run.run_id]
        handoff = public_packets[run.run_id]
        if Path(handoff).resolve() == Path(path).resolve() or _snapshot_tree(Path(handoff)) != _snapshot_tree(Path(path)):
            raise ValueError("public native/source handoff must contain the exact complete replay packet")
        receipt = replay_score_package(path, expected_manifest_sha256=pin, os_sandboxed=True)
        controls = verify_score_packet_tamper_controls(path, expected_manifest_sha256=pin)
        actual = receipt["diagnostics"]["intact"]
        if (run.run_id, run.configuration_id, run.repetition) != (actual["run_id"], actual["configuration_id"], actual["repetition"]):
            raise ValueError("public identity differs from native score packet")
        if (canonical(survival["measurement"]) != canonical(actual["measurement"])
                or canonical(format_document) != canonical(actual["format_evidence"])
                or survival["observer"]["sha256"] != actual["observer_sha256"]
                or survival["native_manifest"]["sha256"] != actual["format_evidence"]["native_manifest"]["sha256"]):
            raise ValueError("public evidence differs from fresh native-to-score replay")
        if not receipt["os_sandboxed"] or controls["status"] != "passed":
            raise ValueError("release verification requires executed OS isolation and tamper controls")
        expected_review.append({"run_id": run.run_id, "configuration_id": run.configuration_id, "repetition": run.repetition,
                                "manifest_sha256": pin, "diagnostics_sha256": receipt["diagnostics_sha256"]})
        scored.append(run)
    if set(packets) != seen or set(public_packets) != seen or canonical(sorted(review["runs"], key=lambda row: row.get("run_id", ""))) != canonical(sorted(expected_review, key=lambda row: row["run_id"])):
        raise ValueError("independent review does not bind exact reproduced native packets")
    results = [run.result_id for run in scored]
    evidence = {"schema_version": CONFIGURATION_EVIDENCE_SCHEMA_VERSION, "configuration_id": bundle["configuration_id"],
                "bundle": {"id": "public-inputs:" + bundle_sha, "sha256": bundle_sha, "immutable": True, "public": True, "result_ids": results},
                "reproduction_receipt": {"id": "review:" + trusted_review_sha256, "sha256": trusted_review_sha256, "bundle_sha256": bundle_sha,
                                         "offline_recomputed": True, "verified": True, "result_ids": results}}
    aggregate = aggregate_public_configuration(scored, configuration_evidence=evidence)
    _VERIFIED.add(_fingerprint(aggregate))
    return aggregate


def verify_release_configuration_n1(
    public_bundle_document: bytes, independent_review_document: bytes, *,
    trusted_review_sha256: str, expected_reviewer_id: str, expected_producer_id: str,
    packets: Mapping[str, tuple[Path, str]],
    public_packets: Mapping[str, Path],
):
    """Freshly reproduce one prospective native packet and seal its aggregate."""
    if (not isinstance(trusted_review_sha256, str)
            or not _SHA.fullmatch(trusted_review_sha256)
            or hashlib.sha256(independent_review_document).hexdigest() != trusted_review_sha256):
        raise ValueError("independent review differs from trusted digest")
    if (any(not isinstance(value, str) or not value.strip() or value != value.strip()
            for value in (expected_reviewer_id, expected_producer_id))
            or expected_reviewer_id == expected_producer_id):
        raise ValueError("independent reviewer and producer must be separately trusted identities")

    bundle = _json(public_bundle_document, "public n=1 replay bundle")
    review = _json(independent_review_document, "independent n=1 release review")
    bundle_sha = hashlib.sha256(public_bundle_document).hexdigest()
    if (not isinstance(bundle, dict)
            or set(bundle) != {"schema_version", "configuration_id", "pairs"}
            or bundle["schema_version"] != PUBLIC_BUNDLE_N1_SCHEMA):
        raise ValueError("unsupported public n=1 replay bundle")
    review_fields = {
        "schema_version", "reviewer_id", "producer_id", "public_bundle_sha256",
        "scope", "checks", "runs", "limitations",
    }
    if (not isinstance(review, dict) or set(review) != review_fields
            or review["schema_version"] != REVIEW_N1_SCHEMA):
        raise ValueError("unsupported independent n=1 release review")
    checks = {
        "independent_operator_execution", "public_safety_review",
        "complete_replay_packet_public_safety_review", "run_identity_review",
        "metric_locator_binding_review", "native_to_score_semantics_review",
    }
    if (review["reviewer_id"] != expected_reviewer_id
            or review["producer_id"] != expected_producer_id
            or review["public_bundle_sha256"] != bundle_sha
            or review["scope"] != "native-to-score-and-exact-public-inputs"
            or not isinstance(review["checks"], dict)
            or set(review["checks"]) != checks
            or any(value is not True for value in review["checks"].values())):
        raise ValueError("independent review does not approve exact public inputs and full scoring scope")
    if (not isinstance(review["limitations"], list) or not review["limitations"]
            or any(not isinstance(value, str) or not value.strip()
                   for value in review["limitations"])):
        raise ValueError("independent review must disclose its environment and acquisition limitations")
    if (not isinstance(bundle["pairs"], list) or len(bundle["pairs"]) != 1
            or not isinstance(review["runs"], list) or len(review["runs"]) != 1):
        raise ValueError("n=1 release verification requires exactly one reviewed repetition")
    run_fields = {
        "run_id", "configuration_id", "repetition", "manifest_sha256",
        "diagnostics_sha256",
    }
    if any(not isinstance(row, dict) or set(row) != run_fields for row in review["runs"]):
        raise ValueError("independent review has malformed native run bindings")
    if len(packets) != 1 or len(public_packets) != 1 or set(packets) != set(public_packets):
        raise ValueError("n=1 release verification requires one exact pinned packet and public handoff")

    pair = bundle["pairs"][0]
    if not isinstance(pair, dict) or set(pair) != {"survival", "format"}:
        raise ValueError("invalid public replay pair")
    survival, format_document = pair["survival"], pair["format"]
    from .release_score import score_release_run
    run = score_release_run(survival, format_document)
    if run.repetition != 1:
        raise ValueError("n=1 release requires prospectively designated repetition 1")
    if (run.configuration_id != bundle["configuration_id"]
            or run.run_id not in packets or run.run_id not in public_packets):
        raise ValueError("public pair differs from complete pinned run population")

    path, pin = packets[run.run_id]
    handoff = public_packets[run.run_id]
    if (Path(handoff).resolve() == Path(path).resolve()
            or _snapshot_tree(Path(handoff)) != _snapshot_tree(Path(path))):
        raise ValueError("public native/source handoff must contain the exact complete replay packet")
    receipt = replay_score_package(path, expected_manifest_sha256=pin, os_sandboxed=True)
    controls = verify_score_packet_tamper_controls(path, expected_manifest_sha256=pin)
    actual = receipt["diagnostics"]["intact"]
    if ((run.run_id, run.configuration_id, run.repetition)
            != (actual["run_id"], actual["configuration_id"], actual["repetition"])):
        raise ValueError("public identity differs from native score packet")
    if (canonical(survival["measurement"]) != canonical(actual["measurement"])
            or canonical(format_document) != canonical(actual["format_evidence"])
            or survival["observer"]["sha256"] != actual["observer_sha256"]
            or survival["native_manifest"]["sha256"]
            != actual["format_evidence"]["native_manifest"]["sha256"]):
        raise ValueError("public evidence differs from fresh native-to-score replay")
    if not receipt["os_sandboxed"] or controls["status"] != "passed":
        raise ValueError("release verification requires executed OS isolation and tamper controls")

    expected_review = [{
        "run_id": run.run_id,
        "configuration_id": run.configuration_id,
        "repetition": run.repetition,
        "manifest_sha256": pin,
        "diagnostics_sha256": receipt["diagnostics_sha256"],
    }]
    if canonical(review["runs"]) != canonical(expected_review):
        raise ValueError("independent review does not bind exact reproduced native packets")

    results = [run.result_id]
    evidence = {
        "schema_version": CONFIGURATION_EVIDENCE_N1_SCHEMA_VERSION,
        "configuration_id": bundle["configuration_id"],
        "bundle": {
            "id": "public-inputs:" + bundle_sha,
            "sha256": bundle_sha,
            "immutable": True,
            "public": True,
            "result_ids": results,
        },
        "reproduction_receipt": {
            "id": "review:" + trusted_review_sha256,
            "sha256": trusted_review_sha256,
            "bundle_sha256": bundle_sha,
            "offline_recomputed": True,
            "verified": True,
            "result_ids": results,
        },
    }
    aggregate = aggregate_public_configuration_n1(run, configuration_evidence=evidence)
    _VERIFIED.add(_fingerprint(aggregate))
    return aggregate
