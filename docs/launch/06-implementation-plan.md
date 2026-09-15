# Session-Bench v1.0 implementation plan

Originally frozen as planning only. On 2026-09-11 the owner authorized bounded calibration
for the historical five configurations in `plans/survival-v1/campaign.json`. The active
release cohort is the prospective Claude campaign in `plans/survival-v1/campaign-prospective-claude-cohort.json`. OpenCode CLI has since
completed its calibration and three evaluated Session Survival repetitions; the other
configurations, the broad 12-metric layer, independent reproduction, publication, and
changes to v0.4 still require the gates below.

## 1. Freeze one contract

Make the protocol, rubric, result schema, report labels, badge state machine,
recommendation rules, vendor-fix rules, and launch templates agree on the 31 metrics and
30/20/15/20/15 weights. Add contract validation that rejects unknown/missing metric IDs,
wrong totals, hand-authored badge states, and recommendations without citations.

## 2. Extend the evidence model

Keep the existing 19-metric survival observer/decoder separation. Add the 12 broad probes
with pinned evidence sources, version/build/date windows, logical-byte classification, and
native/documentation locators. Preserve complete-root manifests, companions, hashes,
copied-root isolation, and canonical comparison.

Build positive, negative, partial, duplicate, missing-companion, schema-drift,
original-root-dependency, and unresolved controls. Equivalent JSONL and SQLite semantic
fixtures must score identically.

## 3. Implement surface adapters

Close complete-root, process identity, visible-response observation, and native-locator
contracts for Codex CLI/Desktop, Claude Code CLI/Desktop Code (Local), and OpenCode CLI. Keep each surface a
separate result. Desktop requires actual GUI observation. Calibration must prove OpenCode
read/edit permission.

No adapter may scan personal history, use an easier workload, change expected populations,
or receive observer truth during decoding.

## 4. Implement scoring and outputs

Score each run, aggregate three repetitions equally, calculate ranges, and block totals on
null states. Generate from one result object:

- compact leaderboard and category bars;
- vendor report card and 31-cell matrix;
- observed-vs-recorded timeline;
- verification badge;
- six builder-use-case recommendations;
- up to three **To pass, fix** items per vendor;
- storage diagnostics and separate crash fields;
- JSON, CSV, SVG/PNG, and accessible text tables.

Recommendation outputs must include rule version, scope, thresholds, objectives, cohort
result IDs, metric IDs, evidence locators, and reason IDs. Exercise single winner,
qualified set, no qualifier, and unresolved paths.

## 5. Calibrate, then freeze

Retain one visible calibration attempt per surface. Use it to repair acquisition and
observer feasibility only. Before evaluated collection, freeze workload, identities,
metric rules, weights, thresholds, matching, classification, badges, recommendations, and
selection rules. Preserve every attempt.

## 6. Collect and qualify

Only after calibration qualifies a surface, run its three scheduled repetitions. Keep
invalid and corrective attempts linked and visible. Run crash qualification separately on
the actual writer and keep its fields out of scoring.

If fewer than three rows or no CLI/Desktop pair qualifies, produce unranked report cards.
Do not weaken the rubric or omit inconvenient surfaces.

## 7. Verify the candidate

Verify arithmetic, denominators, duplicate penalties, category totals, missing-evidence
blocking, identity binding, copied-root equality, recommendation thresholds/ties,
vendor-fix ordering, evidence links, privacy, placeholder absence, accessible mobile
rendering, and historical v0.4 byte-identical regeneration.

Prepare the immutable evidence package, offline reproduction command, deterministic recomputation receipt
slot, and correction record. Publication and external messaging remain outside this plan.
