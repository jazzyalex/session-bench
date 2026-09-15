# Session Bench v1 prototype

This is the earlier local three-surface pilot for Codex CLI, Codex Desktop, and OpenCode
CLI. The current five-surface release contract adds Claude Code CLI and Claude
Desktop Code (Local) under [`docs/survival-v1`](../survival-v1/README.md); Cursor
CLI/Desktop remain historical paused attempts. This historical pilot does not change the
public v0.4 CLI-only edition and must not be read as the current campaign.

Open the [completed local report](../../artifacts/prototype-v1/report/index.html), [share-card SVG](../../artifacts/prototype-v1/report/scorecard.svg), or [machine-readable results](../../artifacts/prototype-v1/report/report.json). These ignored local files exist on the collection machine; they are not public URLs.

Generate the local report after the capture set is complete:

```sh
python3 scripts/build_prototype.py \
  --captures artifacts/prototype-v1/captures \
  --out artifacts/prototype-v1/report
```

The generator does not launch models. It reads each capture's `metadata.json`, builds the five-angle report, copies the evidence directories, and writes `index.html`, `report.json`, `scorecard.svg`, and `composition.svg`. The output directory must be new or empty and must remain outside `captures`.

## Current pilot status

- **Codex CLI**: captured pilot `codex-cli-pilot-01`, runtime `codex-cli 0.154.0`, model `gpt-5.6-luna`, using noninteractive `codex exec` with explicit feature/config isolation. Native rollout and independent observer records are present; metadata still marks the artifact set incomplete.
- **Codex Desktop**: captured native task-API pilot `codex-desktop-pilot-01`, runtime `0.153.4`, app build unverified, model `gpt-5.6-luna`. Display and manual GUI behavior were not observed. One directory-mismatch setup turn is preserved before the two workload turns.
- **OpenCode CLI**: captured pilot `opencode-cli-pilot-01`, version `1.18.30`, model `openai/gpt-5.6-luna`, using `opencode run --pure --format json`. The dedicated SQLite DB and WAL/SHM companions are captured. The controller's file-permission pattern blocked editing, so the final task remained failing (one of three tests passed). This is a setup limitation, not evidence of poor coding ability. One corrective turn completed in the native record but its CLI stream timed out.

## Setup and evidence

Codex CLI's exact isolation flags are recorded in [`invocation.json`](../../artifacts/prototype-v1/captures/codex-cli-pilot-01/invocation.json). Desktop uses the supported native task API; its invocation and limitation record are in [`invocation.json`](../../artifacts/prototype-v1/captures/codex-desktop-pilot-01/invocation.json) and [`metadata.json`](../../artifacts/prototype-v1/captures/codex-desktop-pilot-01/metadata.json). OpenCode's dedicated DB, isolated config, disabled plugins/MCP, share-disabled setting, deny-by-default permissions, and narrow workload allowances are recorded in [`runtime-config.json`](../../artifacts/prototype-v1/captures/opencode-cli-pilot-01/runtime-config.json) and [`preflight.json`](../../artifacts/prototype-v1/captures/opencode-cli-pilot-01/preflight.json). Its preserved path-correction prompts are in [`actual-prompts.json`](../../artifacts/prototype-v1/captures/opencode-cli-pilot-01/actual-prompts.json).

The independent observer records submitted prompts, helper test results, and file-change facts. Native Codex artifacts are the per-pilot `native/rollout.jsonl`; OpenCode's native artifact is the dedicated workspace DB after the writer stops. CLI captures use noninteractive command modes. Desktop display content remains unmeasured.

Treat each evidenced point as a lower bound: a populated category has at least one supporting native or observer fact. Completeness is a separate question controlled by required-artifact presence, observer coverage, and the metadata `artifact_set_complete` field. Pending, absent, or unresolved evidence stays N/A/unresolved and must not become zero or a filled-in score.

These are single live pilots with setup differences and no controlled repeated-run sample. The report must not fabricate an overall ranking or general coding-quality claim. Storage values are descriptive for these captures. The package is local, unsanitized, and unreleased; do not publish it or collect private history. The execution boundaries are in [`prototype-execution.md`](../launch/prototype-execution.md), and the historical v0.4 contract remains unchanged.

Further live runs stopped when the shared Codex usage observation reached 40%, above this prototype's 37% collection ceiling. The observed account movement includes concurrent account activity and is not a measured per-benchmark cost. Offline reporting and validation continue without further vendor workload requests.

The raw artifacts and generated reports are git-ignored to prevent accidental publication of unsanitized local evidence. Source, tests, and planning documentation remain ordinary repository changes.

## Verified prototype outcome

All three pilots retained their independently observed failing test results: Codex CLI 1/1, Codex Desktop 1/1, OpenCode CLI 2/2. This is a scoped record-preservation finding, not a coding-success rate. The report contains 18 check objects per run, five category views, source excerpts, and downloadable SVG/JSON assets. Storage remains descriptive and unscored: whole-record JSONL roles and SQLite container bytes are not comparable useful-payload measurements.

Validation: `python3 -m pytest -q` passed **265 tests**. `python3 scripts/verify_prototype.py` copied all three bundles and reproduced identical measurements under macOS sandboxing. Original-capture reads and networking were denied and explicitly probed. Original native SHA-256 values remained unchanged, including SQLite companions. A separately rebound damaged copy lost its expected failure marker and W3 changed from 1/1 measured to 0/1 unresolved. This is local offline reproduction, not independent-person review. The [verification receipt](../../artifacts/prototype-v1/verification.json) records the result.

The next bounded step is to resolve the OpenCode permission pattern, predeclare context/visible-explanation observations, and decide a defensible storage comparison before collecting ranked repetitions. The prototype does not justify a winner today. Keep the existing five-angle product and launch copy; do not expand into additional recovery scenarios, atlas coverage, or publication infrastructure to address these calibration gaps.
