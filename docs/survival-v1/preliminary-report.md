# Session-Bench v1 preliminary report

**Local draft · unreleased · review-candidate facts only, no public ranked v1 leaderboard.**
The fixed release cohort is Codex CLI, Codex Desktop, Claude Code CLI, Claude
Desktop Code (Local), and OpenCode CLI. Cursor CLI/Desktop remain paused and
historical. The historical v0.4 report remains a separate edition.

## The quotable result, with its boundary

> Fifteen valid evaluated runs exist, 3 per configuration, each with 31/31
> resolved metric cells and local copied-root replay/equality/loss evidence.
> Sanitized public packets (unpublished review candidates): Codex CLI 87.0
> range 86.9-87.0; Codex Desktop 87.0 range 87.0-87.0; OpenCode CLI 83.3 range
> 83.3-83.3; Claude Desktop Code (Local) 82.8 range 82.8-82.8; Claude Code CLI
> 81.0 range 81.0-81.0. The public evidence index references exactly five
> configs, scans 177 public files, reports zero privacy findings, and publishes
> no rank. The report remains local/unreleased.

Category points are Fidelity / Causality / Usage / Portability / Durability:
Codex CLI and Codex Desktop 21.0 / 20.0 / 12.0 / 20.0 / 14.0; OpenCode CLI
20.3 / 20.0 / 12.0 / 18.0 / 13.0; Claude Desktop 25.0 / 14.8 / 12.0 / 18.0 /
13.0; Claude CLI 30.0 / 20.0 / 0.0 / 18.0 / 13.0. Models are honestly recorded
as Codex `gpt-5.6-sol`, Claude Code CLI `claude-sonnet-5[1m]`, Claude
Desktop `claude-opus-5`, OpenCode `opencode/muse-spark-1.3-contributor-free`.

The OpenCode public semantic/score packet was independently recomputed in a
fresh sandbox copy. Native SQLite decode and damaged-native replay remain
source-attested because raw bytes/runtime are withheld from the public packet;
that is not full native reproduction. A deterministic unpublished review
candidate now exists at `artifacts/survival-v1-review-candidate`; it is explicitly
ineligible for publication. No full independent native replay, crash result,
Oracle approval, commit/push, or publication is claimed.

Generated review-candidate recommendations select Claude CLI for audit-ready
records; Codex CLI and Codex Desktop as the qualified portable-archive set; and
the Codex CLI/Desktop pair for cross-surface consistency. Usage accounting,
lean complete records, and long-term archives emit `no_recommendation`. These
are deterministic local outputs from the frozen rubric, not published buying advice.

| Surface | Evidence now | Preliminary status |
|---|---|---|
| Codex CLI (`gpt-5.6-sol`) | Three evaluated runs, each 31/31 resolved cells with local copied-root replay/equality/loss evidence; sanitized packet 87.0 range 86.9-87.0; categories 21.0 / 20.0 / 12.0 / 20.0 / 14.0. | Measured review candidate, unpublished; no public rank. |
| Codex Desktop (`gpt-5.6-sol`) | Three evaluated runs, each 31/31 resolved cells with local copied-root replay/equality/loss evidence; sanitized packet 87.0 range 87.0-87.0; categories 21.0 / 20.0 / 12.0 / 20.0 / 14.0. | Measured review candidate, unpublished; no public rank. |
| Claude Code CLI 2.1.270 (`claude-sonnet-5[1m]`) | Three evaluated runs, each 31/31 resolved cells with local copied-root replay/equality/loss evidence; sanitized packet 81.0 range 81.0-81.0; categories 30.0 / 20.0 / 0.0 / 18.0 / 13.0. | Measured review candidate, unpublished; no public rank. |
| Claude Desktop Code (Local) (`claude-opus-5`) | Three evaluated runs, each 31/31 resolved cells with local copied-root replay/equality/loss evidence; sanitized packet 82.8 range 82.8-82.8; categories 25.0 / 14.8 / 12.0 / 18.0 / 13.0. | Measured review candidate, unpublished; no public rank. |
| OpenCode CLI 1.18.30, `opencode/muse-spark-1.3-contributor-free` | Three evaluated runs, each 31/31 resolved cells with local copied-root replay/equality/loss evidence; sanitized packet 83.3 range 83.3-83.3; categories 20.3 / 20.0 / 12.0 / 18.0 / 13.0. Public semantic/score packet independently recomputed in a fresh sandbox copy; native SQLite decode and damaged-native replay remain source-attested. | Measured review candidate, unpublished; no public rank and no full native reproduction claimed. |

[CLI/Desktop format assessment](shared-format-assessment.md) distinguishes
shared Codex-rollout and declared Claude-transcript decoders from each
surface's separate capture and evidence gates. Sharing a native format does
not make the CLI and Desktop benchmark rows interchangeable.

[Independent-reproduction instructions](independent-reproduction.md) now define
the hand-off packet and receipt needed before any local copied-package result
can receive that badge.

### Historical Cursor records

Cursor CLI `2026.09.08-6caf4ff` and Cursor Desktop `3.12.30` remain preserved,
unscored historical attempts. Cursor CLI has one retained evaluated capture and an
interrupted next attempt. Cursor Desktop has one evaluated two-turn capture whose
transcript plus selected SQLite rows decode two responses, eight actions, eight results,
and ten relations; a later attempt stopped at the Free usage limit. Those facts remain in
the [attempt ledger](attempt-ledger.md), but Cursor is paused and cannot enter this v1
release cohort or its leaderboard.

The Cursor Desktop finding is useful despite the pause. Its project-keyed transcript
contains tool calls but omits tool results. Selected session-keyed SQLite bubble rows
recover the results, and the copied transcript-plus-companion decoder can replay them.
The closure scan also found five additional session-bearing rows in calibration `cal-6`
and six in evaluated `eval-1`, under `agentKv`/`inlineDiff`. They have private exact
captures and public hash/size inventories, but no qualified semantic decode or complete
sanitized public bundle. The live quota rejection is classified **invalid/N/A**, never
as a session-format failure. [Attempt ledger](attempt-ledger.md) and
[Desktop adapter notes](adapters/cursor.md) retain the exact limits.

## What a vibe coder can use today

The provisional lesson is to check whether an agent's history can be reopened from a
**copied bundle** and whether the saved record explains both the tool call and its
result. A transcript that looks complete on screen may depend on a second local store.
Across the five measured configurations, copied-bundle replay, equality, and
selected-loss controls all passed locally; the unpublished review-candidate gaps
differ by configuration and are visible in the category points above. These are
configuration-specific observations, not product buying advice or a public
cross-vendor recommendation. All scores remain unpublished review candidates.

The public scorer now keeps a complete-but-unidentified or mixed-model three-run
configuration unranked, and the serialized report checks the same identity gate.
Resolved measurements remain visible without turning the missing identity into zero.

The intended public v1 retains v0.4's understandable ranking and five angles, the
bright benchmark goal: Record fidelity (30), Causality and context (20),
Durability and signal (15), Portability and openness (20), and Usage and
attribution (15). Its 31 underlying measurements, three valid repetitions per
configuration, complete native bundles, negative controls, and independent
reproduction are the evidence behind future recommendations. Crash recovery is a
separate diagnostic. Fifteen valid evaluated runs with 31/31 cells now exist in
a rendered local review candidate. Full independent native reproduction remains
the named publication blocker. Crash qualification and further model review are
outside this bounded closure pass. The public release gate remains unsatisfied.

## Bounded closure status

All five configurations have three evaluated runs with 31/31 resolved cells and
local copied-root replay/equality/loss evidence. Sanitized public packets and the
five-config, 177-file public evidence index with zero privacy findings exist as
local review candidates publishing no rank.

The local candidate contains `index.html`, `scorecard.svg`, `report.json`,
`leaderboard.csv`, `evidence-index.json`, a candidate gate record, and an
artifact manifest. Its privacy scan reports zero findings, and its publication
metadata is fail-closed.

Full independent native reproduction remains the sole evidence blocker recorded
by the candidate. The owner froze further captures, repetitions, desktop
exploration, crash qualification, and broad implementation. Those activities
are not scheduled. Cursor remains paused. Publication would require reopening
the release phase, satisfying the independent-native gate, final review, and an
explicit owner release instruction.
