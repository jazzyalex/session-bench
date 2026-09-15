# Blog template — concrete unpublished v1 review-candidate draft

Status: **UNPUBLISHED editorial draft. Do not publish.**
Lead with review-candidate results. Placeholder URLs preserved until final release.
No publication, independent reproduction, Oracle approval, crash, or final-rank claim.

## Your agent wrote the code. What did it record?

Claude CLI kept everything about the work but lost all Usage points. Codex CLI and Desktop tied at the top.

That split is from an unpublished v1 review-candidate kit: the same two-turn correction task on Codex CLI, Codex Desktop, OpenCode CLI, Claude Desktop, and Claude CLI. All 15 runs have 31/31 metrics resolved with local copied-root replay/equality/loss controls. This order is not yet a public leaderboard.

I compared five exact surfaces using the same small task: inspect a project, run a failing test, respond to a corrected requirement, edit the code, and rerun the test.

Review-candidate leaderboard (review-candidate, not final ranks):

| Review-candidate order | Score | Fidelity / Causality / Usage / Portability / Durability | Model label |
|---|---|---|---|
| Codex CLI | 87.0 (86.9–87.0) | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 | gpt-5.6-sol |
| Codex Desktop | 87.0 (87.0–87.0) | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 | gpt-5.6-sol |
| OpenCode CLI | 83.3 | 20.3 / 20.0 / 12.0 / 18.0 / 13.0 | opencode/muse-spark-1.3-contributor-free |
| Claude Desktop | 82.8 | 25.0 / 14.8 / 12.0 / 18.0 / 13.0 | claude-opus-5 |
| Claude CLI | 81.0 | 30.0 / 20.0 / 0.0 / 18.0 / 13.0 | claude-sonnet-5[1m] |

### Five angles, one report card

V1.0 scores Record fidelity (30), Causality & context (20), Usage & attribution (15), Portability & openness (20), and Durability & signal (15). Nineteen deep metrics reconstruct the task. Twelve broad metrics cover timestamps, rationale, thread structure, readability, documentation, identity, versioning, observed stability, root stability, duplicate safety, and classified content density.

### What the task record shows

Descriptive finding 1 — tie at top: Codex CLI 87.0 (86.9–87.0) and Codex Desktop 87.0 (87.0–87.0) share the same five-bar profile 21.0/20.0/12.0/20.0/14.0. Native locators and result IDs are in the evidence index (5 configs, 177 files, zero privacy findings). [EVIDENCE_URL]

Descriptive finding 2 — fidelity vs usage split: Claude CLI holds Fidelity 30/30 and Causality 20/20, the highest fidelity in the cohort, but Usage/Attribution 0/15. Two stated gaps: (a) native model label strips `[1m]` versus observed `claude-sonnet-5[1m]`; (b) native usage contradicts independently observed CLI stream counts. Zero here means failed join/reconciliation, not “no usage fields.” [EVIDENCE_URL]

Descriptive finding 3 — desktop as measured surface: Codex Desktop ties its CLI at 87.0. Claude Desktop at 82.8 scores 1.8 above Claude CLI at 81.0, with lower fidelity (25.0 vs 30.0) and causality (14.8 vs 20.0) because its native record batches observed actions. [EVIDENCE_URL]

Usage/portability note: OpenCode CLI at 83.3 holds Causality 20/20 and Usage 12.0, Portability 18.0. It has public semantic/score verification, but native public replay remains source-attested because raw SQLite/runtime are withheld. Local copied-root replay/equality/loss controls are present; full independent native reproduction is not claimed. Distinguish zero (failed check), missing (absent with complete boundary), estimated (not billed), and billed (provider provenance, not inferred). [EVIDENCE_URL]

### If you're building on session files

Generated review-candidate recommendations select Claude CLI for audit-ready records,
Codex CLI plus Desktop for portable archives, and the Codex CLI/Desktop pair for
cross-surface consistency. Usage accounting, lean complete records, and long-term
archives return `no_recommendation`. These outputs remain unpublished.

This draft makes no use-case recommendation until the generated recommendation records are attached. The final kit will insert the generated use-case table with rule version, surface/build/date, cohort, objective, result IDs, and evidence link per row — or state `no_recommendation` where thresholds do not resolve.

These are scoped recommendations among tested configurations only, when they exist. Vendor **To pass, fix** items appear separately because product selection and format improvement answer different questions. No **To pass, fix** item is quoted here beyond the two Claude CLI Usage gaps above unless a generated record supports it.

### What changed since v0.4

V0.4 established the recognizable leaderboard, five-angle cards, matrix, vendor fixes, and builder use cases. V1.0 preserves that structure while adding event-level coverage, causal joins, correction history, CLI/Desktop pairs, repeated runs, native evidence, copied-root reproduction, and verification badges. The editions are not score-comparable.

### Method and limits

Same controlled two-turn workload across five surfaces; independent observer; three scheduled repetitions per surface (15 runs total); 31/31 metrics resolved in every run; local copied-root replay/equality/loss controls; public evidence index of 5 configs and 177 files with zero privacy findings; placeholder report/evidence/correction URLs until final release.

The benchmark measures the saved record, not code quality. Three repetitions describe the declared surfaces/builds and do not establish universal reliability. No crash qualification is reported here. No Oracle approval is claimed. Codex model label is gpt-5.6-sol. OpenCode raw SQLite/runtime remain withheld, so native public replay is source-attested. Unresolved or untested areas: final ranges for OpenCode/Claude rows, final badge/receipt IDs, final generated recommendations, and any independent-reproduction receipt remain pending final package.

Report [REPORT_URL] · Evidence [EVIDENCE_URL] · Corrections [CORRECTION_URL]

### Final-release substitution checklist (keep until release)

- [ ] Replace `[REPORT_URL]`, `[EVIDENCE_URL]`, `[CORRECTION_URL]` with final immutable URLs.
- [ ] Confirm 3-run ranges for OpenCode CLI, Claude Desktop, Claude CLI from final result JSON.
- [ ] Insert final result IDs, evaluation IDs, badge states, and native locators.
- [ ] Insert generated recommendation table or confirm `no_recommendation` with rule version.
- [ ] Remove unpublished/review-candidate banners only at authorized release.
- [ ] Do not add publication, independent-reproduction, Oracle-approval, crash, or final-rank claims unless supported.
