import json

import pytest

from scripts import qualify_claude_desktop_cohort as cohort


def _attempt(tmp_path, run_id, repetition, *, configuration_id="claude-desktop", workload_id=None):
    root = tmp_path / "artifacts" / "survival-v1-runs" / run_id
    root.mkdir(parents=True)
    value = {
        "attempt_id": run_id,
        "configuration_id": configuration_id,
        "repetition": repetition,
    }
    if workload_id is not None:
        value["workload_id"] = workload_id
    (root / "attempt.json").write_text(json.dumps(value))
    return root


def test_explicit_cohort_uses_source_repetitions_and_selected_proof_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cohort, "REPO", tmp_path)
    for run_id, repetition in (("fresh-c", 3), ("fresh-a", 1), ("fresh-b", 2)):
        _attempt(tmp_path, run_id, repetition, workload_id="survival-v1")

    selected = cohort._select_cohort(["fresh-c", "fresh-a", "fresh-b"])

    assert selected == (("fresh-a", 1), ("fresh-b", 2), ("fresh-c", 3))
    records = [
        {"run_id": run_id, "repetition": repetition, "root_locator": cohort.ROOT_LOCATOR,
         "isolated_discovery": True, "personal_history_scanned": False,
         "before": {}, "after": {}, "family_validation_sha256": "abc"}
        for run_id, repetition in selected
    ]
    proof = cohort._build_stable_root_proof(records, selected)
    assert proof["cohort_id"] == "fresh-a+fresh-b+fresh-c"
    assert [row["repetition"] for row in proof["repetitions"]] == [1, 2, 3]


def test_one_run_root_discovery_qualifies_its_own_repetition():
    record = {"run_id": "fresh-c", "repetition": 3,
              "root_locator": "normal-root/projects/project-c/session-c.jsonl",
              "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False,
              "before": {"sha256": "a" * 64}, "after": {"sha256": "b" * 64},
              "family_validation_sha256": "c" * 64}

    assert cohort._build_root_repetitions([record]) == [{
        "repetition": 3, "root_locator": record["root_locator"],
        "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False,
    }]
    proof = cohort._build_stable_root_proof([record])
    assert proof["cohort_id"] == "fresh-c"
    assert len(proof["repetitions"]) == 1
    assert proof["repetitions"][0]["root_locator"] == record["root_locator"]


def test_three_run_root_proof_accepts_distinct_physical_paths():
    records = [
        {"run_id": f"fresh-{number}", "repetition": number,
         "root_locator": f"normal-root/projects/project-{number}/session-{number}.jsonl",
         "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False,
         "before": {"sha256": f"{number}" * 64},
         "after": {"sha256": f"{number + 3}" * 64},
         "family_validation_sha256": f"{number + 6}" * 64}
        for number in (1, 2, 3)
    ]

    proof = cohort._build_stable_root_proof(records)
    assert proof["same_root_locator_across_repetitions"] is False
    assert [row["root_locator"] for row in proof["repetitions"]] == [
        record["root_locator"] for record in records
    ]
    assert [row["discovery_mode"] for row in proof["repetitions"]] == [
        "metadata_safe_normal_root"
    ] * 3


def test_root_proof_rejects_unsafe_discovery_and_mismatched_run_identity():
    record = {"run_id": "fresh-c", "repetition": 3, "root_locator": "root/session-c",
              "discovery_mode": "metadata_safe_normal_root", "personal_history_scanned": False,
              "before": {}, "after": {}, "family_validation_sha256": "abc"}
    with pytest.raises(cohort.QualificationError, match="identities"):
        cohort._build_stable_root_proof([record], (("other-run", 3),))
    record["personal_history_scanned"] = True
    with pytest.raises(cohort.QualificationError, match="privacy"):
        cohort._build_root_repetitions([record])


@pytest.mark.parametrize("repetitions", [(1, 1, 3), (1, 2, 4), (True, 2, 3)])
def test_rejects_invalid_repetitions(tmp_path, monkeypatch, repetitions):
    monkeypatch.setattr(cohort, "REPO", tmp_path)
    for run_id, repetition in zip(("a", "b", "c"), repetitions):
        _attempt(tmp_path, run_id, repetition)
    with pytest.raises(cohort.QualificationError):
        cohort._select_cohort(["a", "b", "c"])


def test_rejects_duplicate_non_desktop_and_workload_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(cohort, "REPO", tmp_path)
    for run_id, repetition in (("a", 1), ("b", 2), ("c", 3)):
        _attempt(tmp_path, run_id, repetition, workload_id="same")
    with pytest.raises(cohort.QualificationError, match="distinct"):
        cohort._select_cohort(["a", "a", "c"])

    attempt = tmp_path / "artifacts/survival-v1-runs/b/attempt.json"
    value = json.loads(attempt.read_text())
    value["configuration_id"] = "claude-code"
    attempt.write_text(json.dumps(value))
    with pytest.raises(cohort.QualificationError, match="identity"):
        cohort._select_cohort(["a", "b", "c"])

    value["configuration_id"] = "claude-desktop"
    value["workload_id"] = "different"
    attempt.write_text(json.dumps(value))
    with pytest.raises(cohort.QualificationError, match="workload IDs"):
        cohort._select_cohort(["a", "b", "c"])


def test_rejects_existing_output_and_run_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(cohort, "REPO", tmp_path)
    for run_id, repetition in (("a", 1), ("b", 2), ("c", 3)):
        _attempt(tmp_path, run_id, repetition)
    output = tmp_path / "artifacts/survival-v1-runs/b/capture/qualified-private-v1"
    output.mkdir(parents=True)
    with pytest.raises(cohort.QualificationError, match="already has"):
        cohort._select_cohort(["a", "b", "c"])
    with pytest.raises(cohort.QualificationError, match="invalid Claude Desktop run ID"):
        cohort._select_cohort(["../a", "b", "c"])


def test_default_cohort_keeps_historical_names():
    assert cohort.COHORT == (
        ("claude-desktop-eval-1-correction-1", 1),
        ("claude-desktop-eval-2-correction-1", 2),
        ("claude-desktop-eval-3", 3),
    )
