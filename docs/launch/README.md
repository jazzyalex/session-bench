# Session-Bench v1.0 launch kit — review-candidate (unpublished)

Status: **UNPUBLISHED v1 launch kit — review-candidate only.**
No publication, outreach, scheduling, or distribution is authorized.
Scores and order are review-candidate facts, not a public leaderboard.
No publication, full independent native reproduction, Oracle approval, crash results, or final rankings are claimed.

Planning templates frozen 2026-09-11. This kit turns them into concrete review-candidate copy grounded only in the verified facts below. Placeholder URLs are preserved where a final release URL/DOI is unavailable.

## Verified review-candidate facts (quote these)

Review-candidate order (not yet a public leaderboard):

| Review-candidate order | Score | Range | Fidelity / Causality / Usage / Portability / Durability |
|---|---|---|---|
| Codex CLI | 87.0 | 86.9–87.0 | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| Codex Desktop | 87.0 | 87.0–87.0 | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| OpenCode CLI | 83.3 | 83.3–83.3 | 20.3 / 20.0 / 12.0 / 18.0 / 13.0 |
| Claude Desktop | 82.8 | 82.8–82.8 | 25.0 / 14.8 / 12.0 / 18.0 / 13.0 |
| Claude CLI | 81.0 | 81.0–81.0 | 30.0 / 20.0 / 0.0 / 18.0 / 13.0 |

- All 15 runs: 31/31 metrics resolved, with local copied-root replay/equality/loss controls.
- Public evidence index: exactly 5 configs, 177 files, zero privacy findings.
- Exact model labels: Codex gpt-5.6-sol; Claude CLI `claude-sonnet-5[1m]`; Claude Desktop `claude-opus-5`; OpenCode `opencode/muse-spark-1.3-contributor-free`.
- Strong findings: (1) Codex CLI/Desktop tie at 87.0, nearly identical bars; (2) Claude CLI leads Fidelity 30/30 + Causality 20/20 but Usage 0/15 (native label strips `[1m]` vs observed `claude-sonnet-5[1m]`; native usage contradicts observed CLI stream counts); (3) Desktop is measured: Codex Desktop ties CLI, Claude Desktop +1.8 over Claude CLI with lower fidelity/causality from batched actions; (4) OpenCode 83.3, Causality 20/20, public semantic/score verification, native public replay source-attested (raw SQLite/runtime withheld).
- Generated review-candidate recommendations: Claude CLI for audit-ready records; Codex CLI + Desktop for portable archives; Codex CLI/Desktop for cross-surface consistency. Usage accounting, lean complete records, and long-term archives emit `no_recommendation`. These remain unpublished local outputs.

## Public promise (review-candidate wording)

> **Your agent wrote the code. What did it record?** (Unpublished review-candidate — not a public leaderboard.)

One matched coding task is scored across:

| Angle | Weight |
|---|---:|
| Record fidelity | 30 |
| Causality & context | 20 |
| Usage & attribution | 15 |
| Portability & openness | 20 |
| Durability & signal | 15 |

The 19-metric survival audit supplies the deep task evidence. Twelve broad format metrics keep the report useful for readers choosing storage, audit, migration, and tooling foundations. No crash qualification is reported in this kit.

## Required report structure (review-candidate)

1. Compact review-candidate table with exact CLI/Desktop surface, build, date, `/100`, range, category bars, local-controls note, and result ID slots.
2. Generated **If you're building on session files** table — pending/`no_recommendation` in this kit, with rule version and reason.
3. Vendor report cards with the gate matrix and separate generated **To pass, fix** items only.
4. Observed-vs-recorded task timeline and native locators.
5. Method, local evidence index (5 configs / 177 files), machine-readable results, and correction history.

Recommendations are not editorial badges. They follow the frozen rubric, cite scoped surface/build/date/result IDs, and emit no recommendation when evidence is unresolved.

Read the launch set in order:

1. [Social and visuals](01-social-and-visuals.md) — primary post, X, LinkedIn, Hacker News title/body, press email, finding variants, visuals
2. [Press and outreach](02-press-and-outreach.md) — release draft, short pitch/press email, maintainer note
3. [Blog template](03-blog-template.md) — concrete review-candidate blog draft
4. [Promotion strategy](04-promotion-strategy.md) — message order, claim-to-asset contract, 7-day draft sequence (not scheduled; no Reddit)
5. [Product specification](05-product-spec.md)
6. [Implementation plan](06-implementation-plan.md)
7. [OpenCode prototype launch draft](07-opencode-prototype-draft.md)

Every quantitative statement requires a result ID slot, denominator, exact tested scope, and matching evidence. V0.4 and v1.0 use different rubrics; cross-edition rank changes do not prove product change.

## Launch checklist (unpublished — keep until authorized release)

- [ ] `01-social-and-visuals.md`: concrete review-candidate copy done; X / LinkedIn / HN / press-email versions present; evidence link last; no Reddit.
- [ ] `02-press-and-outreach.md`: release + pitch + maintainer note drafted with review-candidate numbers; placeholders kept; no send.
- [ ] `03-blog-template.md`: concrete draft with leaderboard, five angles, four descriptive findings, pending recommendations, limits.
- [ ] `04-promotion-strategy.md`: message order, claim contract, 7-day draft sequence present; planning-only, no scheduling.
- [ ] Placeholders `[REPORT_URL]`, `[EVIDENCE_URL]`, `[METHOD_URL]`, `[CORRECTION_URL]`, `[DATE]`, `[NAME]` preserved where final values unavailable.
- [ ] No publication / independent-reproduction / Oracle-approval / crash / final-rank claims present.
- [x] Generated recommendations match the frozen rule output and remain labelled unpublished local results.

## Final-release substitution checklist (keep until release)

- [ ] Replace `[REPORT_URL]` with final immutable report URL/DOI.
- [ ] Replace `[EVIDENCE_URL]` with final evidence index URL (confirm 5 configs / 177 files or final counts).
- [ ] Replace `[METHOD_URL]` / `[CORRECTION_URL]` / `[DATE]` with final values.
- [ ] Replace review-candidate order language with final ranks only from final badge/result IDs.
- [ ] Fill final 3-run ranges for OpenCode CLI, Claude Desktop, Claude CLI from final result JSON.
- [ ] Fill final result IDs, evaluation IDs, badge states, native locators.
- [ ] Confirm generated recommendation + **To pass, fix** records (or keep `no_recommendation`) with rule version.
- [ ] Remove unpublished/review-candidate/do-not-post banners only at authorized release.
