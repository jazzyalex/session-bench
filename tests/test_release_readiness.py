import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("v1_release_status", ROOT / "scripts/v1_release_status.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
LEDGER = ROOT / "plans/survival-v1/expanded-release-status.json"


def test_checked_in_readiness_keeps_all_rows_and_grants_no_scores():
    result = module.build_status(LEDGER)
    assert result["scoped_rows"] == 14
    ledger = json.loads(LEDGER.read_text())["rows"]
    assert result["attempted_rows"] == sum(row["state"] != "unattempted" for row in ledger)
    assert result["incomplete_rows"] == sum(row["state"] == "incomplete" for row in ledger)
    assert result["unattempted_rows"] == sum(row["state"] == "unattempted" for row in ledger)
    assert result["attempted_rows"] + result["unattempted_rows"] == 14
    assert result["rankable_rows"] == 0
    assert not result["publication_eligible"]
    assert not result["release_goal_complete"]
    assert result["evidence_sha256"]


@pytest.mark.parametrize("reference", ["../other-repo/README.md", "/tmp/anything", "does-not-exist.md"])
def test_readiness_rejects_outside_or_missing_evidence(tmp_path, reference):
    data = json.loads(LEDGER.read_text())
    next(row for row in data["rows"] if row["evidence_refs"])["evidence_refs"] = [reference]
    ledger = tmp_path / "status.json"
    ledger.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="evidence"):
        module.build_status(ledger)


def test_status_cannot_promote_a_row_without_evaluated_scores(tmp_path):
    data = json.loads(LEDGER.read_text())
    row = next(row for row in data["rows"] if row["attempt_ids"])
    row.update(state="rankable", reason_ids=[])
    ledger = tmp_path / "status.json"
    ledger.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="rankability"):
        module.build_status(ledger)
