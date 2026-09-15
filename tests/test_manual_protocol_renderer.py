from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from session_bench.workload_instance import instantiate_workload


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/render_manual_survival_protocol.py"
WORKLOAD = ROOT / "fixtures/scenarios/survival-v1/workload/workload.json"


def test_manual_protocol_renderer_emits_the_exact_instantiated_turns() -> None:
    run_id = "codex_desktop_manual_02"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--run-id", run_id, "--json"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    rendered = json.loads(completed.stdout)
    expected, _ = instantiate_workload(json.loads(WORKLOAD.read_text()), run_id)
    assert rendered == {"run_id": expected["run_id"], "turns": expected["turns"]}
