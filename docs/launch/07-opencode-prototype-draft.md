# OpenCode prototype launch draft

Status: **local and unpublished**. This copy is a concrete preview of the v1 voice and
result shape. It is not authorized for posting because the full 31-metric score,
cross-product cohort, sanitized public bundles, and independent reproduction are not
complete.

## Primary social post

Your coding agent wrote the code. What did it actually record?

In three isolated two-turn runs, OpenCode CLI 1.18.30 produced a perfectly portable
archive — but only an **81/100 reconstructable session**.

- Portable archive: 10/10
- Causal links: 20/20
- Work reconstruction: 22.75/35
- Revision trace: 11.25/15
- Usage and attribution: 17/20

The missing points are the interesting part: incomplete action detail, edit fragments
without whole-file before/after hashes, no explicit final-after-correction chain, and
response usage that did not reconcile to session totals.

[REPORT_URL]

Scope: OpenCode CLI 1.18.30 with
`opencode/muse-spark-1.3-contributor-free`; Session Survival lens; three isolated runs;
no cross-product rank. **Do not publish this draft:** the original historical packages
lacked the pinned decoder runtime. Their successor correction packages now replay locally
from their own bytes, but still lack independent reproduction and the unresolved broad
metric evidence required for a public score.

## Short version

In a local calibration, OpenCode's session archive was **100% portable — and 81/100
reconstructable**.

Session-Bench replayed three isolated two-turn coding sessions from copied SQLite + WAL
SHM bundles. Run-specific measurement packages differ by run_id/repetition; their semantic
  score/category content matches across the three local runs after excluding run identity fields.
  The successor correction package contains its pinned replay runtime, but remains local and
unranked pending independent reproduction and full broad-metric evidence. No leaderboard
yet.

[REPORT_URL]

## Explanatory thread

1. Session-Bench asks a simple question: after your agent finishes, how much of the work
   can another tool faithfully reconstruct?
2. The task has two turns: inspect and run a known failure; receive a corrected
   requirement; edit; run the final check; explain the outcome.
3. OpenCode retained both prompts and responses, all four results, every action-to-result
   link, revision order, exact model identity, response usage, and a portable copied
   bundle.
4. It did not satisfy four exact assertions: only one of four actions retained every
   required field; the edit stored fragments rather than whole-file hash pairs; the
   native record lacked an explicit final-after-R2 link; response usage did not equal the
   session totals.
5. That produces 81/100 on the 19-check Session Survival lens in each of three runs.
6. The successor correction package verifies hashes and replays observer + decoder +
   portability receipt through the comparator. A forged precomputed measurement is rejected.
7. This is one surface/build/model result. The full v1 benchmark remains a five-surface,
   31-metric comparison with Codex CLI/Desktop, Claude Code CLI/Desktop Code (Local),
   and OpenCode CLI.

## Press-release lead

**Session-Bench v1 prototype finds a portable OpenCode archive can still lose critical
reconstruction detail**

Session-Bench today prepared a local, unreleased prototype calibration for OpenCode CLI
1.18.30. Across three isolated two-turn coding sessions using
`opencode/muse-spark-1.3-contributor-free`, copied SQLite, WAL, and SHM bundles recorded
the same 81.0/100 Session Survival Score. The archive received full marks for
portability and causal links while exposing exact gaps in action detail, file-state
proof, revision lineage, and usage reconciliation.

The successor correction package restores the pinned decoder runtime and passes a local
standalone replay, but it cannot be released until independent reproduction and the full
31-metric evidence gate are complete. The result is evidence for one declared
configuration, not a product-wide score or cross-product rank. Session-Bench v1 will
publish a leaderboard only after its cohort, desktop, broad-metric, sanitization,
runtime-provenance, and independent-reproduction gates pass.

## Blog opening

### A session can be portable without being complete

The first strict Session-Bench v1 prototype produced the kind of result the benchmark is
meant to make visible. OpenCode CLI's native session could be copied, isolated, and decoded
without the original root. Run-specific measurement packages differ by run_id/repetition;
the recomputed survival-lens score and category values match across the three local runs after
excluding run identity fields.
That earned 10/10 for portability. The same record scored 81/100 overall because
portability alone did not prove that every detail of the work survived.

Across three runs, the record preserved the conversation, result outputs, causal links,
revision order, model identity, and response-level usage. It preserved only one of four
actions with the complete ordered-arguments/target assertion; its edit row stored text
fragments rather than whole-file before/after hashes; and it lacked an explicit native
chain proving that final work followed the corrected requirement. Session totals also
included more usage than the two response-linked records, so reconciliation correctly
scored zero.

That distinction is the point of v1: keep v0.4's clear ranking and five colorful angles,
then put a reproducible evidence chain behind every point and every zero.

## Promotion sequence after release gates pass

1. Lead with the 100%-portable / 81%-reconstructable scorecard.
2. Follow with the observed-versus-recorded action timeline and one native locator.
3. Publish the copied-bundle reproduction command and independent reproduction receipt.
4. Add public cross-product findings only after all five release configurations qualify.
5. Publish generated builder recommendations only when their cohort and rule thresholds
   resolve; otherwise show `no_recommendation`.

The three deterministic local result IDs are:

- repetition 1: `survival-result-60a378c4b619c46ed2665f89`
- repetition 2: `survival-result-836a7f6a83d97a22261b58bc`
- repetition 3: `survival-result-61094130a74df561a1faa9a8`

Regenerate this list from `artifacts/survival-v1-live-result/result.json` whenever the
decoder, comparator, evidence wrapper, or retained packages change.
