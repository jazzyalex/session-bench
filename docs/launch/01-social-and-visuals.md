# Social copy and visual briefs

Status: **UNPUBLISHED v1 launch kit — review-candidate only.**
Do not post, publish, or distribute.
Scores and order below are review-candidate facts, not a public leaderboard.
No publication, Oracle approval, crash results, or final rankings are claimed.

## Review-candidate facts (use verbatim for quotes)

Review-candidate order (not yet a public leaderboard):

| Review-candidate order | Score | Range (3-run avg) | Fidelity / Causality / Usage / Portability / Durability |
|---|---|---|---|
| Codex CLI | 87.0 | 86.9–87.0 | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| Codex Desktop | 87.0 | 87.0–87.0 | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| OpenCode CLI | 83.3 | 3-run avg | 20.3 / 20.0 / 12.0 / 18.0 / 13.0 |
| Claude Desktop | 82.8 | 3-run avg | 25.0 / 14.8 / 12.0 / 18.0 / 13.0 |
| Claude CLI | 81.0 | 3-run avg | 30.0 / 20.0 / 0.0 / 18.0 / 13.0 |

- All 15 runs: 31/31 metrics resolved.
- Local copied-root replay / equality / loss controls: present in all runs.
- Public evidence index: exactly 5 configs, 177 files, zero privacy findings.
- Exact model labels: Codex gpt-5.6-sol; Claude CLI `claude-sonnet-5[1m]`; Claude Desktop `claude-opus-5`; OpenCode `opencode/muse-spark-1.3-contributor-free`.
- Generated review-candidate recommendations select Claude CLI for audit-ready records, Codex CLI + Desktop for portable archives, and the Codex CLI/Desktop pair for cross-surface consistency. Three other use cases emit `no_recommendation`; all remain unpublished.
- Native public replay for OpenCode remains source-attested (raw SQLite/runtime withheld). No full independent native reproduction is claimed.

## Primary post (short, quotable)

Your agent wrote the code. What did it record?

Unpublished review-candidate: same correction task on Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, Claude CLI.
Five angles. Three runs each. 31/31 metrics resolved in all 15 runs.

Review-candidate: Codex CLI and Desktop tie at 87.0 with nearly identical five-bar profiles.

[REPORT_URL]

## X version (evidence link last, no thread required)

Unpublished review-candidate:
Codex CLI 87.0 (86.9–87.0) ties Codex Desktop 87.0 (87.0–87.0).
Then OpenCode CLI 83.3, Claude Desktop 82.8, Claude CLI 81.0.
15 runs, 31/31 metrics resolved. Not a public leaderboard yet.

[REPORT_URL]

Alt X (finding-led):

Claude CLI kept Fidelity 30/30 and Causality 20/20 but scored Usage 0/15 in this review-candidate kit.
Native model label strips [1m] vs observed claude-sonnet-5[1m]; native usage contradicts observed CLI stream counts.

[EVIDENCE_URL]

## LinkedIn version (evidence link last)

Your agent wrote the code. What did it record?

This is an unpublished v1 review-candidate kit, not a launch announcement.

We ran the same correction task — inspect, fail, explain, receive correction, edit, pass, explain — on five surfaces:

- Codex CLI: 87.0 (86.9–87.0) — 21.0 / 20.0 / 12.0 / 20.0 / 14.0
- Codex Desktop: 87.0 (87.0–87.0) — 21.0 / 20.0 / 12.0 / 20.0 / 14.0
- OpenCode CLI (`opencode/muse-spark-1.3-contributor-free`): 83.3 — 20.3 / 20.0 / 12.0 / 18.0 / 13.0
- Claude Desktop (`claude-opus-5`): 82.8 — 25.0 / 14.8 / 12.0 / 18.0 / 13.0
- Claude CLI (`claude-sonnet-5[1m]`): 81.0 — 30.0 / 20.0 / 0.0 / 18.0 / 13.0

What this means, descriptively:

- Desktop is a measured surface here. Codex Desktop ties its CLI. Claude Desktop scores 1.8 above Claude CLI overall, with lower fidelity/causality because its native record batches observed actions.
- Claude CLI leads Fidelity/Causality but loses all Usage points on the two gaps above.
- OpenCode holds Causality 20/20, with public semantic/score verification; native public replay stays source-attested.

Scope: 15 runs, 31/31 resolved metrics each, local copied-root controls. Evidence index: 5 configs, 177 files, zero privacy findings. Generated local recommendations are evidence-linked and unpublished.

[REPORT_URL]

## Hacker News title + body (draft only, do not post)

Title: Session-Bench v1 review-candidate: what five coding-agent surfaces record (unpublished kit)

Body:

Unpublished review-candidate kit. Not a public leaderboard. No publication claim.

Same two-turn correction task on Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, Claude CLI. Scored Fidelity (30) / Causality (20) / Usage (15) / Portability (20) / Durability (15).

Review-candidate scores:

- Codex CLI 87.0 (86.9–87.0): 21.0/20.0/12.0/20.0/14.0
- Codex Desktop 87.0 (87.0–87.0): 21.0/20.0/12.0/20.0/14.0
- OpenCode CLI 83.3: 20.3/20.0/12.0/18.0/13.0
- Claude Desktop 82.8: 25.0/14.8/12.0/18.0/13.0
- Claude CLI 81.0: 30.0/20.0/0.0/18.0/13.0

Models: Codex gpt-5.6-sol; Claude CLI claude-sonnet-5[1m]; Claude Desktop claude-opus-5; OpenCode opencode/muse-spark-1.3-contributor-free.

All 15 runs have 31/31 resolved metrics with local copied-root replay/equality/loss controls. Evidence index: 5 configs, 177 files, zero privacy findings. OpenCode native public replay is source-attested (raw SQLite/runtime withheld). No full independent native reproduction, Oracle approval, crash results, or final rankings claimed. Recommendations remain pending.

Happy to answer on method, locators, and limits.

[REPORT_URL]

## Finding variants (one per post, evidence link last)

**Tie at top:** Review-candidate Codex CLI 87.0 (86.9–87.0) ties Codex Desktop 87.0 (87.0–87.0). Same five-bar profile: 21.0/20.0/12.0/20.0/14.0.

[EVIDENCE_URL]

**Usage accounting:** Review-candidate Claude CLI scores Usage/Attribution 0/15. Native model label strips [1m] vs observed `claude-sonnet-5[1m]`; native usage contradicts independently observed CLI stream counts. Fidelity 30/30, Causality 20/20 retained.

[EVIDENCE_URL]

**Desktop as measured surface:** Review-candidate Codex Desktop ties its CLI at 87.0. Claude Desktop at 82.8 scores 1.8 above Claude CLI at 81.0, with lower fidelity/causality because its native record batches observed actions.

[EVIDENCE_URL]

**Portable archive:** Review-candidate OpenCode CLI 83.3 holds Causality 20/20, with public semantic/score verification. Native public replay remains source-attested because raw SQLite/runtime are withheld.

[EVIDENCE_URL]

**Coverage:** All 15 runs: 31/31 metrics resolved with local copied-root replay/equality/loss controls. Public evidence index: 5 configs, 177 files, zero privacy findings.

[EVIDENCE_URL]

Use recommendation variants only with the exact generated statuses: Claude CLI for audit-ready; Codex CLI + Desktop for portable archives and cross-surface consistency; `no_recommendation` for usage accounting, lean complete records, and long-term archives. Keep every variant unpublished until release is authorized.

## Explanatory thread (draft, 7 posts max, mobile-friendly)

1. Question + review-candidate table above. Label: review-candidate, not a public leaderboard.
2. Task: inspect, fail, explain, receive correction, edit, pass, explain. Same task on all five surfaces.
3. Angles: Fidelity 30, Causality 20, Usage 15, Portability 20, Durability 15. Total 100.
4. Tie: Codex CLI/Desktop 87.0, identical bars 21.0/20.0/12.0/20.0/14.0. Ranges 86.9–87.0 and 87.0–87.0.
5. Split: Claude CLI 30/20 but 0/15 Usage for the two stated gaps. Claude Desktop 82.8 overall, lower fidelity/causality from batched actions.
6. OpenCode 83.3: 20.3/20.0/12.0/18.0/13.0. Strong causality; public replay source-attested.
7. Scope/limits: 15 runs 31/31, local copied-root controls, 5 configs / 177 files / zero privacy findings. Recommendations pending. No publication, independent reproduction, Oracle approval, crash, or final-rank claim.

[REPORT_URL]

## Press email (draft, do not send)

Subject: Unpublished review-candidate: what five coding-agent surfaces record

Hi [NAME],

Session-Bench v1 review-candidate (unpublished, not a public leaderboard) compares Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, and Claude CLI on one controlled correction task.

Review-candidate order: Codex CLI 87.0 (86.9–87.0) and Codex Desktop 87.0 (87.0–87.0) tie; then OpenCode CLI 83.3, Claude Desktop 82.8, Claude CLI 81.0. All 15 runs have 31/31 resolved metrics with local copied-root controls. Evidence index: 5 configs, 177 files, zero privacy findings.

Full method, IDs, and limits are in the draft package. No publication, independent reproduction, Oracle approval, crash, or final-rank claim.

[REPORT_URL]

Alexander

## Visual assets

| Asset | Content (review-candidate) |
|---|---|
| Hero scorecard | Rank-labeled-as-review-candidate rows, five bars with numbers, ranges (Codex CLI 86.9–87.0; Codex Desktop 87.0–87.0), result IDs, verification note: local copied-root controls only |
| Vendor card | Five-angle numbers above, 31-metric matrix, evidence links, separate **To pass, fix** (generated only) |
| Builder table | Pending state: `no_recommendation` / generated-pending; show rule version and reason; no winners |
| Task timeline | Observed vs retained counts; native locator; no new claims |
| Storage diagnostic | Byte labels only; no score claim |

Keep labels readable on a phone. Do not rely on color alone. Provide alt text plus a text table. Title small-difference assets: “What each coding-agent surface records (review-candidate).”

## Final-release substitution checklist (keep until release)

- [ ] Replace `[REPORT_URL]` with final immutable report URL.
- [ ] Replace `[EVIDENCE_URL]` with final evidence index URL (5 configs / 177 files).
- [ ] Replace `[METHOD_URL]` / `[CORRECTION_URL]` where used.
- [ ] Replace review-candidate labels with final badge/result IDs only after final authorized evidence package.
- [ ] Confirm ranges for OpenCode CLI, Claude Desktop, Claude CLI from final machine-readable result.
- [ ] Confirm generated recommendation records (or keep `no_recommendation`) from final rule version.
- [ ] Remove “unpublished / review-candidate / do not post” banners only at authorized release.
- [ ] Do not add publication, independent-reproduction, Oracle-approval, crash, or final-rank claims unless the final package supports them.
