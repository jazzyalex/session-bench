# v1 report-card prototype

The local v1 renderer keeps the compact v0.4 report-card shape and adds a
second layer of evidence. Its first screen is a ranked table with the five v1
angles:

1. Record fidelity
2. Causality & context
3. Usage & attribution
4. Portability & openness
5. Durability & signal

Every configuration row carries an exact `CLI` or `Desktop` surface badge, a
verification badge, a three-run range, and a score only when the producer has
marked the row `ranking_eligible` and `evidence_complete`. A row with a useful
partial category is still shown, but its overall score is withheld and its rank
is `UNRANKED`.

The report card continues the v0.4 “To pass, fix” prompt and the
“If you’re building on session files” use-case table. Below those familiar
sections, each row can open a gate matrix, an observed-versus-recorded
timeline, and locator snippets. The left timeline lane comes from the
independent observer; the right lane comes from the native artifact. A locator
ID is retained so a reader can cite the exact evidence boundary.

## Constructed control data

`fixtures/scenarios/survival-v1/public-report-demo.json` is deliberately marked
`CONSTRUCTED · OFFLINE CONTROL`. Its rows are fictional `Fixture Alpha`,
`Fixture Beta`, and `Fixture Gamma` labels. The numeric values exist to exercise
the renderer and ranking rules; they are not vendor results, product scores,
or a recommendation about a live tool. No network, private session, or live
collection path is used by this renderer-only demo.

The authoritative path is separate. `build_authoritative_report()` accepts only
validated `PublicConfigurationScore` objects from
`session_bench.v1_public_score` and a `RecommendationOutputs` value from
`session_bench.recommendations`. It requires all five attempted target surfaces,
the exact frozen 31 metric IDs on every row, and category points normalized by
`PUBLIC_CATEGORY_POINTS`. It derives the verification badge from scored facts,
immutable public bundle/receipt fields, and bound result identities. It derives
rankings only when the public cohort gate passes: all five fully reproduced
release-cohort rows. A complete CLI/Desktop pair gates only its consistency recommendation. A display string such as `Fully
reproduced` cannot make a row rankable.

For an offline control of that strict path, the CLI scores the constructed
survival and format-profile controls through the real public scorer:

```sh
python3 scripts/build_v1_report.py \
  --authoritative-control \
  --out /tmp/session-bench-v1-authoritative
```

The resulting `report.json` uses the
`session-bench-v1-authoritative-report` schema and contains all five target
surface rows. It is visibly marked `CONSTRUCTED CONTROL`; it is a scorer and
renderer control, not a live product report.

Each authoritative metric repetition preserves the scorer's typed
`PublicMetricEvidence`: its canonical state, observer IDs, and complete native
locator objects (artifact ID, SHA-256, and record location when present). Each
configuration also preserves the typed immutable public bundle and independent
reproduction receipt, including their digests, flags, and exact three-result
bindings. The validator cross-checks recommendation citations and repair
locators against those serialized rows and the scorer objects used to build
them.

The recommendation panel does not infer a winner from the largest number. In
the renderer-only fixture it accepts only the explicitly marked fixture
control record and checks its target/citation IDs. In the authoritative path,
the panel is translated from the versioned `RecommendationOutputs.display()`
document, including use-case status, rule version, thresholds, objective,
result IDs, observer IDs, native locators, and reason IDs. An arbitrary
`qualifies` flag or prose recommendation is rejected by the strict validator.
The constructed control lets `RecommendationOutputs` feature-detect those
typed scorer-retained evidence rows directly; it does not provide a separate
hand-authored metric-evidence catalog.

## Build a local report

From the repository root:

```sh
python3 scripts/build_v1_report.py \
  --input fixtures/scenarios/survival-v1/public-report-demo.json \
  --out /tmp/session-bench-v1-report
```

To render an existing strict authoritative document, use `--authoritative`:

```sh
python3 scripts/build_v1_report.py \
  --authoritative \
  --input /path/to/authoritative-report.json \
  --out /tmp/session-bench-v1-authoritative
```

The output directory contains exactly:

```text
index.html       # self-contained responsive report
scorecard.svg    # self-contained compact leaderboard snapshot
report.json      # exact supplied payload, preserved for citation and tooling
```

Open `index.html` directly from disk. CSS, SVG, and all interaction use native
HTML or inline assets; there are no remote fonts, scripts, images, or network
requests. Native `<details>` elements provide the expandable gate matrix, so
the report remains usable without JavaScript.

## Public input contract

The renderer expects a JSON object with `categories` and `configurations`.
Configuration rows should supply:

* `surface`: exactly `CLI` or `Desktop` for the surface badge;
* `verification`, `status`, and `ranking_eligible`;
* `evidence_complete`, `runs`, `scheduled_runs`, `run_range`, and `score`;
* per-category `score` values;
* a non-empty `gate_matrix` and `evidence` list for a ranked row.

The renderer checks these fields before assigning a rank. Three repeated runs,
a complete run range, every category score, measured gate states, and evidence
locators are required. Missing or unresolved fields remain visible and keep the
row out of the ranking. This rule protects the denominator when a capture is
partial.

Use the `recommendations` field only for the renderer-only control payload. Its
records need `qualifies: true`, a `target_id`, and `citation_ids`; the cited IDs
must exist in that target's evidence list. A missing citation, incomplete target,
or `qualifies: false` record produces no recommendation card. Authoritative
reports instead carry the exact serialized `RecommendationOutputs` document and
are validated against its versioned schema.

## Scope boundary

This artifact is a presentation prototype and a constructed control. It does
not replace the survival-v1 protocol, evaluator, observer contract, or live
capture gates. It does not edit the historical v0.4 inputs under `data/`, and
it does not turn incomplete evidence into a rank, recommendation, or claim
about a vendor.
