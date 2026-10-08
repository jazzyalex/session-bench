#!/usr/bin/env python3
"""Prepare one inert Claude Desktop survival-v1 capture directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from session_bench.workload_instance import instantiate_workload  # noqa: E402


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _quoted(path: Path | str) -> str:
    return shlex.quote(str(path))


def _wrapper_source(
    *, repo: Path, run_root: Path, workspace: Path, run_id: str, run_canary: str
) -> str:
    return f'''from __future__ import annotations
from pathlib import Path
import json
import sys
sys.path.insert(0, {str(repo)!r})
from session_bench.claude_desktop_hook_observer import observe_hook
from session_bench.claude_desktop_gui_event_clock import record_user_prompt_submit_event

RUN_ID = {run_id!r}
WORKSPACE = Path({str(workspace)!r})
FIXTURE = WORKSPACE
RECEIPT = WORKSPACE / ".session-bench-hooks.jsonl"
PROJECTION = WORKSPACE / ".session-bench-command-projections.jsonl"
RUN_CANARY = {run_canary!r}
WORKLOAD = Path({str(run_root / "workload-instance.json")!r})
GUI_CLOCK = Path({str(run_root / "capture/gui-event-clock.jsonl")!r})
OTEL_SESSION_ID = Path({str(run_root / "capture/otel-session-id.private.txt")!r})

raw = sys.stdin.buffer.read(2_000_001)
if not raw or len(raw) > 2_000_000:
    raise SystemExit(2)
event = json.loads(raw)
session_id = event.get("session_id")
if not isinstance(session_id, str) or not session_id:
    raise SystemExit(2)
if event.get("hook_event_name") == "UserPromptSubmit":
    workload = json.loads(WORKLOAD.read_text(encoding="utf-8"))
    record_user_prompt_submit_event(
        raw,
        run_id=RUN_ID,
        workload=workload,
        workspace=WORKSPACE,
        ledger_path=GUI_CLOCK,
        session_id_file=OTEL_SESSION_ID,
    )
    raise SystemExit(0)
observe_hook(
    raw,
    run_id=RUN_ID,
    session_id=session_id,
    workspace=WORKSPACE,
    fixture_root=FIXTURE,
    receipts_path=RECEIPT,
    projection_receipts_path=PROJECTION,
    run_canary=RUN_CANARY,
)
'''


def _readme(
    *, repo: Path, run_root: Path, workspace: Path, run_id: str
) -> str:
    capture = run_root / "capture"
    workload = run_root / "workload-instance.json"
    session_file = capture / "otel-session-id.private.txt"
    desktop_session_file = capture / "desktop-ui-session-id.private.txt"
    usage = capture / "otel-usage-private.json"
    source = capture / "source-discovery-private.json"
    clock = capture / "gui-event-clock.jsonl"
    gui = capture / "gui-receipt-private.json"
    final_project = run_root / "final-project-private"
    transcript = capture / "native-family-private/transcript/session.jsonl"
    desktop = capture / "native-family-private/desktop/session.json"
    return f"""# Claude Desktop synthetic run `{run_id}`: prepared, unscored

This directory contains a frozen synthetic fixture, a run-bound workload, and
per-project capture hooks. Preparation did not open Claude Desktop, submit a
prompt, start a receiver, contact a network service, or perform a capture.
`attempt.json` must remain `prepared_unscored` until a separate capture and
qualification workflow records otherwise.

Use `turns[].text` from `{workload}` verbatim and in order.

## 1. Enable the minimized OTel logs for this local run

Before creating the new local Claude Code session, set these values in Claude
Desktop's Local Environment Editor:

```text
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_LOGS_EXPORTER=otlp
OTEL_METRICS_EXPORTER=none
OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/json
OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://127.0.0.1:4318/v1/logs
OTEL_LOG_USER_PROMPTS=0
OTEL_LOG_ASSISTANT_RESPONSES=1
```

The Code session sends its OTel logs only to the loopback receiver below. User
prompt text, tool details, tool content, metrics, and raw API bodies stay
disabled. The receiver hashes response text in memory and retains no response
text. Remove these temporary values from the Local Environment Editor as soon
as the benchmark session has started; they remain active only in that already
started session. Use Claude Sonnet 5.5 at Medium effort for both turns.

## 2. Start source discovery before the first prompt

Start this command before submitting the first prompt. The collector reads the
UI identity file only after the command finishes and uses its exact metadata
filename without opening unrelated candidate files.

```sh
cd {_quoted(repo)}
python3 scripts/capture_claude_desktop_source_locations.py \\
  --workspace {_quoted(workspace)} \\
  --run-id {_quoted(run_id)} --repetition 1 \\
  --expected-desktop-session-id-file {_quoted(desktop_session_file)} \\
  --run-marker {_quoted('SB_SURVIVAL_V1_RUN_' + run_id)} \\
  --receipt {_quoted(source)} \\
  -- /bin/sh -c 'printf "Complete both Claude Desktop turns, then press Return here: "; read answer'
```

Keep that terminal open through both responses.

## 3. Start the loopback usage receiver before the first prompt

```sh
cd {_quoted(repo)}
python3 scripts/capture_claude_desktop_otel_usage.py \\
  --run-id {_quoted(run_id)} \\
  --workload-instance {_quoted(workload)} \\
  --session-id-file {_quoted(session_file)} \\
  --port 4318 \\
  --output {_quoted(usage)}
```

The exact-session file does not exist during preparation. The project hook
creates it only when a submitted prompt exactly matches this workload. Stop the
receiver with Ctrl-C only after the second response is complete so it writes
the minimized receipt.

## 4. Record displayed response boundaries

Immediately after visually confirming each complete response, run the matching
command. Do not stamp a response that was not observed.

```sh
python3 {_quoted(repo / 'scripts/record_claude_desktop_gui_event.py')} --run-id {_quoted(run_id)} --event-id response-r1 --event-kind assistant_response --ledger {_quoted(clock)}
python3 {_quoted(repo / 'scripts/record_claude_desktop_gui_event.py')} --run-id {_quoted(run_id)} --event-id response-r2 --event-kind assistant_response --ledger {_quoted(clock)}
```

The `UserPromptSubmit` project hook records `turn-r1` and `turn-r2` before
Claude processes each exact prompt. The same hook latches the exact session ID
for the receiver. Bash/Edit/Write hooks record tool starts, results, and safe
semantic projections in the fixture workspace.

## 5. Preserve the selected native family and GUI evidence

After both responses, copy the `local_<uuid>` ID from the active Claude Code URL
in Claude Desktop. In a separate terminal, paste that observed ID when prompted
to create the private UI identity file:

```sh
(
  umask 077
  printf 'Paste the active Desktop URL session ID: '
  IFS= read -r desktop_session_id
  printf '%s\\n' "$desktop_session_id" > {_quoted(desktop_session_file)}
)
```

Then press Return in the source-discovery terminal. Copy the two exact paths
printed by that command to:

- `{transcript}`
- `{desktop}`

Preserve the completed fixture as `{final_project}` and prepare the real GUI
receipt at `{gui}`. These destinations are expectations for a future capture;
their mention is not evidence that the files exist.

## 6. Finalize only after every named input exists

```sh
cd {_quoted(repo)}
python3 scripts/finalize_claude_desktop_runs.py \\
  --run-id {_quoted(run_id)} --repetition 1 \\
  --transcript {_quoted(transcript)} \\
  --desktop-metadata {_quoted(desktop)} \\
  --gui-receipt {_quoted(gui)} \\
  --hook-receipt {_quoted(workspace / '.session-bench-hooks.jsonl')} \\
  --command-projection-receipt {_quoted(workspace / '.session-bench-command-projections.jsonl')} \\
  --source-discovery-receipt {_quoted(source)} \\
  --gui-event-clock-receipt {_quoted(clock)} \\
  --otel-usage-receipt {_quoted(usage)} \\
  --project-root {_quoted(final_project)}
```

Successful finalization writes a private replay under
`{capture / 'finalized-private-v1'}`. It does not by itself grant a score,
rank, public-safety approval, or independent reproduction.
"""


def prepare_run(
    run_id: str,
    *,
    repo_root: Path = REPOSITORY,
    python_executable: str = sys.executable,
) -> Path:
    """Create one new prepared run without touching Claude Desktop."""
    repo = Path(repo_root).resolve(strict=True)
    template_path = repo / "fixtures/scenarios/survival-v1/workload/workload.json"
    fixture_source = repo / "fixtures/scenarios/survival-v1/workload/fixture_project"
    if not template_path.is_file() or not fixture_source.is_dir():
        raise ValueError("frozen survival-v1 workload fixture is unavailable")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    workload, _environment = instantiate_workload(template, run_id)

    runs_root = repo / "artifacts/survival-v1-runs"
    if not runs_root.is_dir():
        raise ValueError("artifacts/survival-v1-runs must already exist")
    run_root = runs_root / run_id
    if run_root.exists() or run_root.is_symlink():
        raise ValueError("run_id is already in use")
    if run_root.parent != runs_root:
        raise ValueError("run_id must resolve directly under the survival run root")

    run_root.mkdir()
    capture = run_root / "capture"
    capture.mkdir()
    workspace = run_root / "workspace/fixture_project"
    workspace.parent.mkdir()
    shutil.copytree(fixture_source, workspace)

    hook_dir = workspace / ".claude/hooks"
    hook_dir.mkdir(parents=True)
    wrapper = hook_dir / "record-session-bench-hook.py"
    wrapper.write_text(
        _wrapper_source(
            repo=repo,
            run_root=run_root,
            workspace=workspace,
            run_id=run_id,
            run_canary=workload["run_canary"],
        ),
        encoding="utf-8",
    )
    command = f"{_quoted(python_executable)} {_quoted(wrapper)}"
    hook = {"hooks": [{"type": "command", "command": command}]}
    settings = {
        "hooks": {
            "UserPromptSubmit": [hook],
            "PreToolUse": [{**hook, "matcher": "Bash|Edit|Write"}],
            "PostToolUse": [{**hook, "matcher": "Bash|Edit|Write"}],
            "PostToolUseFailure": [{**hook, "matcher": "Bash|Edit|Write"}],
        }
    }
    _write_json(workspace / ".claude/settings.local.json", settings)
    _write_json(run_root / "workload-instance.json", workload)
    _write_json(
        run_root / "attempt.json",
        {
            "schema_version": "session-bench-claude-desktop-attempt-v1",
            "attempt_id": run_id,
            "configuration_id": "claude-desktop",
            "repetition": 1,
            "run_canary": workload["run_canary"],
            "state": "prepared_unscored",
            "score_eligible": False,
            "public_score_eligible": False,
            "claim_limit": (
                "prepared synthetic run only; no capture, score, or rank until "
                "execution, finalization, review, and reproduction"
            ),
        },
    )
    (run_root / "README.md").write_text(
        _readme(repo=repo, run_root=run_root, workspace=workspace, run_id=run_id),
        encoding="utf-8",
    )
    return run_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        destination = prepare_run(args.run_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
