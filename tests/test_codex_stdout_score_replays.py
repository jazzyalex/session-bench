"""The retained Codex CLI runs score all 31 metrics inside the closed packet replay."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_codex_stdout_score_replays import build  # noqa: E402
from session_bench import score_replay  # noqa: E402
from session_bench.adapters.codex_cli_decoder import decode_codex_cli_bundle  # noqa: E402
from session_bench.codex_format_evidence import codex_stdout_event_timestamps  # noqa: E402

RETAINED = ROOT / "artifacts/v1-expanded-preparation/codex-stdout-score-replay-v2/codex-cli-eval-2"

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="packet replay needs macOS sandbox-exec")


@pytest.fixture(scope="module")
def packets(tmp_path_factory) -> Path:
    output = tmp_path_factory.mktemp("codex-stdout") / "packets"
    build(output)
    return output


def _states(packets: Path, repetition: int) -> dict[str, str]:
    receipt = json.loads((packets / f"codex-cli-eval-{repetition}-receipt.json").read_bytes())
    return {row["id"]: row["state"] for row in receipt["diagnostics"]["intact"]["metrics"]}


@pytest.mark.parametrize("repetition", (1, 2, 3))
def test_every_metric_resolves_in_the_packet_replay(packets: Path, repetition: int) -> None:
    states = _states(packets, repetition)

    assert len(states) == 31
    assert sorted(metric for metric, state in states.items() if state == "unresolved") == []


@pytest.mark.parametrize("repetition", (1, 2, 3))
def test_native_attested_usage_and_model_are_measured(packets: Path, repetition: int) -> None:
    states = _states(packets, repetition)

    assert {states[metric] for metric in (
        "attribution.model_config", "attribution.usage",
        "attribution.token_semantics", "attribution.reconciliation",
    )} == {"measured"}


@pytest.mark.parametrize("repetition", (1, 2, 3))
def test_missing_schema_version_and_incomplete_root_score_zero_as_resolved_states(packets: Path, repetition: int) -> None:
    states = _states(packets, repetition)

    assert states["broad.declared_format_version"] == "native_absent"
    assert states["broad.honest_version_signal"] == "native_absent"
    assert states["portable.complete_root"] == "contradiction"


def _retained_decode_and_observer() -> tuple[dict, bytes]:
    context = json.loads((RETAINED / "inputs/context.json").read_bytes())
    instance = json.loads((RETAINED / "inputs/workload.json").read_bytes())
    observer_bytes, _ = score_replay._observer(RETAINED, instance, context)
    decoded = decode_codex_cli_bundle(
        RETAINED / "native", workload=score_replay._workload(instance), complete_root=context["complete_root"],
        required_companions=context["required_companions"], configuration_id="codex-cli", repetition=context["repetition"])
    return decoded, observer_bytes


def test_stdout_observer_events_take_timestamps_from_their_exact_native_lines() -> None:
    decoded, observer_bytes = _retained_decode_and_observer()

    evidence = codex_stdout_event_timestamps(decoded, observer_document=observer_bytes, native_package=RETAINED / "native")

    assert evidence["evidence_complete"] is True
    assert len(evidence["event_ids"]) == 13
    assert [record["id"] for record in evidence["records"]] == evidence["event_ids"]
    assert {record["unit"] for record in evidence["records"]} == {"rfc3339"}


def test_stdout_observer_timestamps_are_withheld_when_one_native_event_is_missing() -> None:
    decoded, observer_bytes = _retained_decode_and_observer()
    decoded["facts"]["visible_responses"] = [
        row for row in decoded["facts"]["visible_responses"] if row.get("turn_id") != "turn-r2"]

    evidence = codex_stdout_event_timestamps(decoded, observer_document=observer_bytes, native_package=RETAINED / "native")

    assert evidence is None
