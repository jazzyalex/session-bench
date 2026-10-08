"""Verifier contract controls; synthetic review attestations never publish data."""
import copy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import sys

import pytest

from session_bench.native_replay import canonical
from session_bench.release_replay import (
    PUBLIC_BUNDLE_N1_SCHEMA, PUBLIC_BUNDLE_SCHEMA, REVIEW_N1_SCHEMA, REVIEW_SCHEMA,
    is_verified_release_configuration, verify_release_configuration,
    verify_release_configuration_n1,
)
from session_bench.release_score import qualified_release_cohort, release_scorecard
from session_bench.release_scope import CONFIGURATION_IDS, load_release_scope

ROOT = Path(__file__).resolve().parents[1]
RUNS = (
    ("dsh-cal-20260929-2", "a722917a81e1f67975688a4bbff3a7d6dfeba7c739982423f032ae5bffba0e27"),
    ("dsh-eval-20260929-1", "6017f5640aef9a10b1d212f26ac795ecacce6d69b91924545977b9f90fbc5e4d"),
    ("dsh-eval-20260929-2", "c91a4f093edc47a74784987e0c5fd45bcd67b54f32ce87507189afcc366a4bff"),
)


def documents():
    pairs, reviewed, packets = [], [], {}
    for run_id, pin in RUNS:
        receipt = json.loads((ROOT / f"artifacts/v1-expanded-preparation/dsh-independent-verification-v1/{run_id}.replay.json").read_bytes())
        actual = receipt["diagnostics"]["intact"]
        survival = json.loads((ROOT / f"artifacts/survival-v1-runs/{run_id}/qualification-v5/survival-evidence.json").read_bytes())
        survival["measurement"] = actual["measurement"]
        survival["observer"] = actual["format_evidence"]["observer"]
        survival["native_manifest"] = actual["format_evidence"]["native_manifest"]
        pairs.append({"survival": survival, "format": actual["format_evidence"]})
        reviewed.append({"run_id": run_id, "configuration_id": actual["configuration_id"], "repetition": actual["repetition"],
                         "manifest_sha256": pin, "diagnostics_sha256": receipt["diagnostics_sha256"]})
        packets[run_id] = (ROOT / f"artifacts/v1-expanded-preparation/dsh-native-score-replay-v1/{run_id}", pin)
    bundle = {"schema_version": PUBLIC_BUNDLE_SCHEMA, "configuration_id": "deepseek-harness-cli", "pairs": pairs}
    review = {"schema_version": REVIEW_SCHEMA, "reviewer_id": "synthetic-test-reviewer", "producer_id": "synthetic-test-producer",
              "public_bundle_sha256": hashlib.sha256(canonical(bundle)).hexdigest(), "scope": "native-to-score-and-exact-public-inputs",
              "checks": {key: True for key in ("independent_operator_execution", "public_safety_review", "complete_replay_packet_public_safety_review", "run_identity_review", "metric_locator_binding_review", "native_to_score_semantics_review")},
              "runs": reviewed, "limitations": ["Synthetic attestation control only; never a real public safety approval."]}
    return bundle, review, packets


def documents_n1(run_index=0):
    bundle, review, packets = documents()
    pair = bundle["pairs"][run_index]
    reviewed = review["runs"][run_index]
    run_id = reviewed["run_id"]
    singleton = {
        "schema_version": PUBLIC_BUNDLE_N1_SCHEMA,
        "configuration_id": bundle["configuration_id"],
        "pairs": [pair],
    }
    singleton_review = {
        **review,
        "schema_version": REVIEW_N1_SCHEMA,
        "public_bundle_sha256": hashlib.sha256(canonical(singleton)).hexdigest(),
        "runs": [reviewed],
    }
    return singleton, singleton_review, {run_id: packets[run_id]}


def verify(bundle, review, packets, **options):
    with tempfile.TemporaryDirectory(prefix="release-review-test-") as directory:
        public = {}
        for run_id, (path, _) in packets.items():
            output = Path(directory).resolve() / run_id
            shutil.copytree(path, output)
            public[run_id] = output
        if options.get("omit_native"):
            next((public[RUNS[0][0]] / "native").glob("*.zstd")).unlink()
        return verify_release_configuration(canonical(bundle), canonical(review),
            trusted_review_sha256=options.get("pin", hashlib.sha256(canonical(review)).hexdigest()),
            expected_reviewer_id="synthetic-test-reviewer", expected_producer_id=options.get("producer", "synthetic-test-producer"), packets=packets, public_packets=public)


def verify_n1(bundle, review, packets, **options):
    with tempfile.TemporaryDirectory(prefix="release-review-n1-test-") as directory:
        public = {}
        for run_id, (path, _) in packets.items():
            output = Path(directory).resolve() / run_id
            shutil.copytree(path, output)
            public[run_id] = output
        return verify_release_configuration_n1(
            canonical(bundle), canonical(review),
            trusted_review_sha256=options.get(
                "pin", hashlib.sha256(canonical(review)).hexdigest(),
            ),
            expected_reviewer_id="synthetic-test-reviewer",
            expected_producer_id=options.get("producer", "synthetic-test-producer"),
            packets=packets,
            public_packets=public,
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="actual OS backend is macOS only")
def test_actual_three_native_replays_seal_partial_aggregate_without_resolving_missing_metrics():
    bundle, review, packets = documents()
    aggregate = verify(bundle, review, packets)
    assert is_verified_release_configuration(aggregate)
    assert not aggregate.rankable and aggregate.overall is None
    assert qualified_release_cohort([aggregate]) == ()
    assert "independent_native_score_replay_not_verified" not in aggregate.blockers
    assert any("broad.stable_root_location" in value for value in aggregate.blockers)
    assert any("attribution.reconciliation" in value for value in aggregate.blockers)
    statuses = [{"configuration_id": key, "state": "blocked", "attempt_ids": ["test-attempt:" + key],
                 "evidence_refs": ["test-control.json"], "reason_ids": ["test.control"]} for key in CONFIGURATION_IDS]
    report = release_scorecard(load_release_scope(), statuses, [aggregate])
    assert not report["publication_eligible"] and not report["release_goal_complete"]
    assert report["publication_blockers"] == ["fewer_than_three_rankable_rows"]
    assert not is_verified_release_configuration(replace(aggregate, overall=Fraction(100), rankable=True))
    run = aggregate.runs[0]
    altered = replace(run, metrics={**run.metrics, "work.visible_responses": Fraction(1) - Fraction(1, 10**15)})
    assert not is_verified_release_configuration(replace(aggregate, runs=(altered, *aggregate.runs[1:])))


@pytest.mark.skipif(sys.platform != "darwin", reason="actual OS backend is macOS only")
def test_actual_singleton_native_replay_seals_n1_aggregate_and_preserves_n3_schema():
    bundle, review, packets = documents_n1()
    aggregate = verify_n1(bundle, review, packets)

    assert len(aggregate.runs) == 1
    assert aggregate.runs[0].repetition == 1
    assert is_verified_release_configuration(aggregate)
    assert PUBLIC_BUNDLE_SCHEMA == "session-bench-release-replay-input-v1"
    assert REVIEW_SCHEMA == "session-bench-release-replay-review-v1"
    n3_bundle, n3_review, n3_packets = documents()
    n3_aggregate = verify(n3_bundle, n3_review, n3_packets)
    assert len(n3_aggregate.runs) == 3
    assert is_verified_release_configuration(n3_aggregate)


@pytest.mark.parametrize("mode", ["rep2", "missing_run", "extra_run", "untrusted_review"])
def test_n1_rejects_nonprospective_or_inexact_singleton_inputs(mode, monkeypatch):
    bundle, review, packets = documents_n1(run_index=1 if mode == "rep2" else 0)
    options = {}
    if mode == "missing_run":
        packets = {}
    elif mode == "extra_run":
        packets["extra-run"] = next(iter(packets.values()))
    elif mode == "untrusted_review":
        options["pin"] = "0" * 64

    monkeypatch.setattr(
        "session_bench.release_replay.replay_score_package",
        lambda *args, **kwargs: pytest.fail("invalid n=1 input executed native source"),
    )
    with pytest.raises(ValueError):
        verify_n1(bundle, review, packets, **options)


@pytest.mark.parametrize("mode", ["review_pin", "self_review", "public_bytes", "no_safety", "native_not_public_reviewed", "missing_native_handoff", "wrong_scope", "private_receipt", "malformed_run"])
def test_review_trust_boundary_rejects_unbound_or_self_attested_inputs(mode, monkeypatch):
    bundle, review, packets = documents()
    options = {}
    if mode == "review_pin": options["pin"] = "0" * 64
    elif mode == "self_review": options["producer"] = "synthetic-test-reviewer"
    elif mode == "public_bytes": bundle["pairs"][0]["survival"]["identity"]["model"] = "forged-model"
    elif mode == "no_safety": review["checks"]["public_safety_review"] = False
    elif mode == "native_not_public_reviewed": review["checks"]["complete_replay_packet_public_safety_review"] = False
    elif mode == "missing_native_handoff": options["omit_native"] = True
    elif mode == "wrong_scope": review["scope"] = "decode-only"
    elif mode == "private_receipt": review["schema_version"] = "session-bench-independent-packet-review-v1"
    else: review["runs"][0] = None
    monkeypatch.setattr("session_bench.release_replay.replay_score_package", lambda *a, **k: pytest.fail("invalid review executed native source"))
    with pytest.raises(ValueError): verify(bundle, review, packets, **options)


@pytest.mark.parametrize("mode", ["count", "format", "observer", "packet_identity", "diagnostics_pin", "extra_packet"])
def test_public_counts_profiles_and_exact_native_bindings_are_verified(mode, monkeypatch):
    bundle, review, packets = documents()
    if mode == "count": bundle["pairs"][0]["survival"]["measurement"]["metrics"][0]["correct"] = 1
    elif mode == "format": bundle["pairs"][0]["format"]["build"] = "forged-build"
    elif mode == "observer": bundle["pairs"][0]["survival"]["observer"]["sha256"] = "0" * 64
    elif mode == "packet_identity": bundle["pairs"][0]["survival"]["run_id"] = "forged-run"
    elif mode == "diagnostics_pin": review["runs"][0]["diagnostics_sha256"] = "0" * 64
    else: packets["extra"] = packets[RUNS[0][0]]
    review["public_bundle_sha256"] = hashlib.sha256(canonical(bundle)).hexdigest()
    def retained(path, **kwargs):
        result = json.loads((ROOT / f"artifacts/v1-expanded-preparation/dsh-independent-verification-v1/{path.name}.replay.json").read_bytes())
        result["os_sandboxed"] = True  # arithmetic negative control only; actual backend positive tested above
        return result
    monkeypatch.setattr("session_bench.release_replay.replay_score_package", retained)
    with pytest.raises(ValueError): verify(bundle, review, packets)
