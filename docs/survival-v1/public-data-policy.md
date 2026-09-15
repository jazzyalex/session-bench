# Session-Bench v1.0 public-data policy

Status: evidence policy, 2026-09-11. It authorizes no live collection or publication and
does not change v0.4 or prototype evidence.

## Boundary

The reproducible package begins with a fresh synthetic project and ends with an immutable
sanitized evidence bundle:

```text
frozen workload → exact surface run → independent observer
→ complete native root and companions → copied-root evaluation
→ 31-metric result, badge, recommendations, and citations
```

Personal histories, account data, credentials, unrelated paths, provider dashboards, and
cloud backends are outside the boundary.

## Allowed and prohibited content

Allowed content includes the synthetic workload, exact benchmark turns/canaries,
deterministic actions/results, synthetic paths and hashes, authorized sanitized native
records, complete inventories, native locators, pinned documentation citations, metric
states, scores, badges, recommendation records, vendor-fix records, and reproduction
receipts.

Never include personal sessions, private prompts/code, home-directory listings, tokens,
cookies, account/billing data, unrelated logs, hidden telemetry, or a transformed native
record presented as raw evidence. If private material cannot be separated without
changing a scored property, the capture is withheld and the affected result is unresolved.

## Evidence and claim rules

Constructed fixtures prove evaluator behavior only. A surface/build claim requires an
authorized native run plus independent observation. Every public cell cites the full
surface/build/date/result tuple, observer ID where applicable, artifact digest, native or
documentation locator, decoder/evaluator version, and evidence state.

Unresolved, unsupported, unexercised, invalid, unavailable, and redacted states remain
visible. They do not become zero, pass, or a smaller denominator. Usage fields preserve
their provenance as observed, estimated, billed, unknown, or absent.

## Generated recommendation records

The **If you're building on session files** table is generated from rubric rules. Each
record contains:

```text
recommendation_key, status, rule_version, scope[], cohort_result_ids[],
thresholds{}, objectives{}, metric_ids[], evidence_locators[], reason_ids[]
```

Allowed statuses are `recommended`, `qualified_set`, and `no_recommendation`. A candidate
with unresolved evidence cannot be recommended. Copy may summarize this record only when
it keeps “among these tested configurations” plus the exact surface/build/date scope and
result citation.

Vendor **To pass, fix** records are separate. Each contains the scoped result ID, metric
ID/state, observed consequence, acceptance condition, and observer/native locators. They
must not be converted into user-selection recommendations.

## Badges, review, and correction

**Fully reproduced**, **Partially verified**, and **Unranked** badges are generated only
from the rubric state machine. A badge cites its result/evaluation IDs and reproduction
receipt. Graphic or prose labels without those fields are invalid.

Before a package is reviewable, verify inventory completeness, companions, observer
separation, actual Desktop provenance, decoder blindness, privacy, pinned documentation,
the canonical copied-root comparison result and its evidence, scoring arithmetic, badge logic, recommendation logic,
and every public link.

Release artifacts are immutable. Corrections create a new record, mark the prior result
superseded, and retain the old identity and reason. V0.4 data, evidence, and correction
history remain separate and unchanged.

Crash evidence has its own unscored record and cannot be cited as leaderboard points or a
reliability percentage. Physical storage diagnostics identify their exact byte population;
they cannot be described as tokens, cost, or semantic completeness.
