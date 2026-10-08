# Session Bench v1 ranking policy: one-run edition

Policy ID: `session-bench-v1-ranking-n1`

This policy defines minimum-one rank eligibility for the v1 candidate. It changes
only the configuration-level repetition requirement for new single-run rows. The existing 31 metrics,
metric states, scoring weights, workload, and per-run evidence rules remain
unchanged.

## Rank eligibility

A new `n=1` ranked configuration contributes exactly one prospectively designated
evaluated run: repetition 1. The run must be designated before its score is known.
A calibration, corrective attempt, or best-scoring run chosen after inspection
cannot replace the designated run. An already trusted `n=3` configuration keeps
its exact three reviewed repetitions and their existing aggregate score.

All 31 metrics must resolve in every included run. `measured`, `native_absent`, and
`contradiction` are resolved states and retain their existing score behavior.
Any null or incomplete state blocks the configuration's overall score and rank.

An `n=1` row's ranked score is the designated run's score. The report must state
the sample size on each row. An `n=1` row must not publish a range, variance,
repeatability claim, or other multi-run statistic. An `n=3` row retains its
existing aggregate score and ranges.

## Evidence and review gates

The designated run must satisfy the existing configuration identity, native
replay, tamper-control, public-safety, and independent-review gates. Its public
evidence must be an immutable, deterministic, offline-recomputable bundle with
the exact run, result, manifest, and evidence identities bound by hash.

Each `n=1` configuration requires its own reviewed single-run bundle and trusted
review hash. A review of a different bundle or an `n=3` aggregate cannot approve
the `n=1` configuration, even when the selected run appeared in that older
bundle.

For `broad.stable_root_location`, one complete run is sufficient when its
session root is documented or deterministically discovered by the accepted
isolated or metadata-safe method, without personal-history scanning. The root
evidence must be bound to that same designated run. This establishes location
for the scored run; it does not establish repeatability across runs.

## Comparability and history

The candidate may rank trusted `n=3` rows together with newly reviewed `n=1`
rows. Each row declares its sample size and uses the verifier selected by its
public bundle schema. The ranking compares their scores while preserving the
sample-size difference in every row and limiting range or repeatability claims
to `n=3` rows. The `n=1` selection rule is identical for every new single-run row.

Existing `n=3` scores, reviews, and ranks remain historical results under their
original methodology and evidence hashes. This policy does not rewrite or
invalidate them, and it does not modify the frozen v0.4 rubric.

## Amendment, 2026-10-03

The owner changed two points. Candidate v30 and later use them.

A configuration with an unresolved metric is shown as a provisional row. The row
shows the points already measured and the best possible score as a range. A
provisional row has no rank.

Attribution, portable and broad metrics are scored from native bytes. The
independent observer is required for work, causal and revision metrics only. See
`rubric.md`, "Evidence tiers".

All five ranked rows in candidate v30 are `n=3`. No `n=1` row is ranked.
