# Session Bench — public corrections and rescoring

The bench treats its own record the way it grades others': every score
change is public, dated, and attributed.

## 2026-08-13 — repository published
Methodology, data, evaluator, and tests extracted to this standalone
repository. The website remains the readable report card.

## 2026-08-12 — v0.3: T2 restricted to true format versions
An application/CLI version identifies the writer, not the schema, and no
longer passes T2. Claude Code, Codex, Copilot, and OpenCode lost the gate
(their T3 became not-applicable); Pi, OpenClaw, and Kimi, which stamp
real format/protocol versions, kept it. Copilot moved below Kimi and
Hermes. Evidence for Codex C6/C7 pinned to hashed artifact receipts;
counts corrected the same day from substring matching to structured
record matching (sub_agent_activity records: 164; parent_thread_id-keyed
records: 3).

## 2026-08-12 — v0.2: Codex rescored +2; scoring states hardened
External review showed Codex was underscored: real rollouts carry
plaintext reasoning summaries (~36% of reasoning records) and
subagent-thread records, flipping C6 and C7 to pass. Rubric changes: T3
scored only when T2 passes (v0.1 wrongly awarded honest-version points to
harnesses with no version at all); per-harness observation windows, with
Kimi's ~10 days scored not run; Antigravity's P1 flipped to pass (awkward
encoding is not message loss); pick-by-need guidance generated from the
matrix.

## 2026-08-12 — O3 correction: four vendors do document their formats
The launch claim that no vendor documents its session format was false.
Pi publishes a full session-format specification (in its tree since April
2026); OpenClaw documents its session store schema and transcript event
structure; Hermes documents its persistence layers and stored fields;
Copilot documents its storage layout and persisted event types (weakest
pass — no single official cross-surface link). Kimi documents layout but
not the wire-record schema; OpenCode's tables exist in source, not
documentation; Claude Code, Codex, Antigravity, and Cursor publish
nothing. Four cells flipped.

## 2026-08-06 — v0.1 published
Initial 19-scored-gate leaderboard at
https://jazzyalex.github.io/agent-sessions/bench/.
