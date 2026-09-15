# Session-Bench v1.0 product specification

Accepted documentation direction, 2026-09-11. No live collection or publication is
authorized. V0.4 remains unchanged.

## Reader outcome

A reader can rank verified surface/build configurations, see why they differ, choose a
record format for a concrete use case, inspect the evidence, and see the smallest format
improvements for each vendor.

## Product hierarchy

| Layer | Required behavior |
|---|---|
| Leaderboard | Compact v0.4-style rows: rank, surface/build, `/100`, range, five bars, badge, result ID |
| Builder use cases | Six machine-generated recommendation rows with scope, rule, objective, and evidence citation |
| Vendor report card | Five categories, 31-cell matrix, coverage/range, and separate **To pass, fix** list |
| Task evidence | Observed-vs-recorded timeline and native locators for the two-turn task |
| Method/evidence | Identities, attempts, immutable bundles, offline command, machine JSON/CSV, corrections |
| Diagnostics | Physical storage and separate unscored crash qualification |

## Score contract

The five budgets are fixed: Record fidelity 30, Causality & context 20, Usage &
attribution 15, Portability & openness 20, Durability & signal 15. The 19 survival metrics
and 12 broad metrics in `../survival-v1/rubric.md` are the complete first-edition matrix.

Score each of three scheduled repetitions independently and average them equally. Any
unresolved required metric blocks total and rank. Show known category quality, coverage,
possible range, and reason instead. Require all five release-cohort rows and one complete
CLI/Desktop pair for the ranked leaderboard.

## Identity and citation contract

Every visible score, badge, recommendation, fix, timeline fact, and excerpt resolves to:

```text
surface_id + build + collection_date/range + result_id + evaluation_id
+ metric_id(s) + evidence_locator(s) + rule_version
```

**Fully reproduced**, **Partially verified**, and **Unranked** follow the rubric state
machine. Badge text is not hand-authored.

## Recommendation contract

Generate audit-ready, portable archives, usage accounting, CLI/Desktop consistency, lean
complete record, and long-term archives from the frozen thresholds/objectives. Output
`recommended`, `qualified_set`, or `no_recommendation`. Unresolved evidence cannot yield a
recommendation.

Generate vendor **To pass, fix** separately: blockers first, then the lowest normalized
category and largest recoverable points. Each item cites its metric and acceptance
condition. Do not turn vendor guidance into a selection award.

## Visual acceptance

The first screen must look and read like an evolved v0.4 report card. Rank and five bars
remain visually dominant. CLI/Desktop labels, ranges, badges, and result IDs must remain
legible on a phone. Color is redundant with text/state symbols. Every graphic has alt text
and a matching text table.

No chart compares incompatible byte populations. Storage appears as a diagnostic with
physical/logical labels, unknown bytes, and range. Crash qualification appears outside
the score and recommendation bars.

## Release-candidate acceptance

- All five categories and 31 metrics are implemented from one result schema.
- Three qualified rows satisfy ranking rules; a CLI/Desktop pair gates only its
  consistency recommendation.
- All five attempted surface rows and all attempts remain visible.
- Every public value and sentence can be regenerated or checked against result data.
- Badge, recommendation, tie, no-result, and vendor-fix paths are deterministic.
- Copied bundles reproduce without original roots, vendor executables, or network.
- V0.4 regeneration and files remain unchanged.
- Crash qualification is published separately as an unscored diagnostic.
- No live claim or external action exists without a final authorized evidence package.
