"""Native-only timestamp populations remain diagnostic, including after event loss."""

import json
from pathlib import Path

import pytest

from session_bench.v1_public_score import validate_format_evidence


ROOT = Path(__file__).resolve().parents[1]


def document(family, complete_family, delete_event, monkeypatch):
    if family == "codex":
        from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle
        from session_bench.codex_format_evidence import build_codex_format_evidence
        decoded = decode_codex_cli_bundle(ROOT / "tests/fixtures/codex-cli-native-0154")
        initial = len(decoded["records"])
        if delete_event:
            decoded["records"].pop()
        value = build_codex_format_evidence(
            decoded, observer={"id": "observer", "sha256": "a" * 64},
            native_manifest={"id": "manifest", "sha256": "b" * 64}, build="synthetic-build",
            collected_on="2026-09-29", result_id="synthetic-result",
            complete_record_family=complete_family,
        )
    elif family == "claude":
        from session_bench.claude_format_evidence import build_claude_format_evidence
        decoded = {"format": "claude-code-jsonl-v1", "session_id": "synthetic-session", "events": [
            {"id": "user", "kind": "submitted_turn", "text": "synthetic input", "timestamp": "2026-09-29T00:00:00Z"},
            {"id": "response", "kind": "response", "text": "synthetic response", "timestamp": "2026-09-29T00:00:01Z"},
        ]}
        initial = len(decoded["events"])
        if delete_event:
            decoded["events"].pop()
        value = build_claude_format_evidence(
            decoded, observer={"id": "observer", "sha256": "a" * 64},
            native_manifest={"id": "manifest", "sha256": "b" * 64},
            run_id="synthetic-run", configuration_id="claude-cli", repetition=1,
            build="synthetic-build", collected_on="2026-09-29", result_id="synthetic-result",
            complete_record_family=complete_family,
        )
    else:
        import session_bench.opencode_format_evidence as module
        package = ROOT / "artifacts/survival-v1-runs/opencode-cli-eval-1/evaluation-correction"
        decoded = json.loads((package / "decoded.json").read_text())
        timed = [index for index, row in enumerate(decoded["events"]) if isinstance(row.get("time_created"), int)]
        initial = len(timed)
        if delete_event:
            decoded["events"].pop(timed[-1])
        original_read = module._read
        monkeypatch.setattr(module, "_read", lambda path: decoded if path.name == "decoded.json" else original_read(path))
        value = module.build_opencode_format_evidence(
            package, collected_on="2026-09-29", complete_record_family=complete_family,
        )
    return value, initial - int(delete_event)


@pytest.mark.parametrize("family", ["codex", "claude", "opencode"])
@pytest.mark.parametrize("complete_family", [False, True])
@pytest.mark.parametrize("delete_event", [False, True])
def test_timestamp_diagnostics_never_become_scored_native_population(family, complete_family, delete_event, monkeypatch):
    value, expected_records = document(family, complete_family, delete_event, monkeypatch)
    detail = value["profile"]["broad_evidence"]["broad.event_timestamps"]
    assert not detail["evidence_complete"]
    assert len(detail["records"]) == expected_records > 0
    assert len(detail["event_ids"]) == expected_records
    metric = next(row for row in validate_format_evidence(value)["profile"]["metrics"] if row["id"] == "broad.event_timestamps")
    assert metric["state"] == "unresolved"
    assert metric["correct"] == metric["observed_eligible"] == metric["decoded_eligible"] == 0
