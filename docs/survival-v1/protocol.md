# Session-Bench v1.0 protocol

Status: product and evidence contract, 2026-09-11. This protocol authorizes no live
vendor execution, personal-store collection, publication, or change to v0.4.

## Scope

V1.0 asks the same broad question as v0.4—what useful record did the harness preserve—
with stronger proof. It evaluates Codex CLI, Codex Desktop, Cursor CLI, Cursor Desktop,
and OpenCode CLI as separate surface/build rows. Every row uses the same workload,
observer, expected populations, 31 metric IDs, category weights, and state rules.

Every result binds this tuple before collection:

```text
provider → harness → surface → execution mode → OS → harness/app build
→ model → configuration → protocol → workload → observer → rubric
→ run → capture → evaluation
```

A shared runtime or storage family does not make CLI and Desktop equivalent. Actual
Desktop output requires independent accessibility text plus reviewed visual evidence.
CLI output requires the exact declared stdout/PTY stream.

## Frozen identities

`protocol_version`, `workload_version`, `observer_schema_version`, `rubric_version`,
`recommendation_rule_version`, `run_id`, `capture_id`, and `evaluation_id` are immutable.
Every public score, badge, recommendation, and correction names them.

The native decoder receives only the declared copied native package and decoder
configuration. It never receives observer truth, the answer key, original project root,
or network. The evaluator joins decoder output to the observer in a separate workspace.
Changing observer answers while native bytes remain fixed must not change decoder output.

## Frozen workload and ground truth

The workload is under `fixtures/scenarios/survival-v1/workload/` and contains a synthetic
checkout defect, deterministic helper, response canaries, filesystem truth, and a
constructed positive observer. It contains no vendor transcript or personal data.

Two exact turns are submitted:

1. R1 supplies a requirement, context marker, and fresh run marker. The agent inspects
   the target, runs the known failing baseline, explains the result, and stops.
2. R2 explicitly supersedes only the delivery rule. The agent edits
   `fixture_project/checkout.py`, preserves quantity handling, runs the final helper, and
   explains the result.

The required sequence is R1 accepted; inspect action/result; baseline action/failure;
R1 response displayed; R2 accepted; edit action/file boundary/result; final test/pass;
R2 response displayed. The fixed deep populations are two user turns, two visible
responses, four actions, four results, one changed file, four action-result relations,
and two turn-response relations.

All surfaces use the fixed response canaries:

```text
SB_SURVIVAL_V1_RESPONSE_R1_cafe_🙂
SB_SURVIVAL_V1_RESPONSE_R2_correction_Δ
```

and the context/run prefixes:

```text
SB_SURVIVAL_V1_CONTEXT_cafe_🙂
SB_SURVIVAL_V1_RUN_
```

Each attempt instantiates a unique suffix, substitutes only that declared run-canary
token in the two prompts, and passes the same value to the unchanged helper through
`SB_SURVIVAL_V1_RUN_CANARY`.

The canary proves an independently observed response boundary, not hidden reasoning,
complete deltas, or billing accuracy. The helper ledger supports process truth but cannot
prove the harness received or displayed a result. File hashes prove the edit boundary but
cannot prove a tool call was shown. The checked-in after snapshot is one reference valid
solution; evaluated success comes from the final helper's semantic cases, not an exact
after-file hash.

Coding outcome is reported separately and unscored. Skipping a required boundary yields
`unexercised`; it never reduces the denominator.

## Broad format observation

The same run captures the 12 broad properties required by the rubric: per-event
timestamps, readable rationale/summary, thread structure, standard-tools readability,
documented format, self-contained identity, declared version, honest version signal,
observed schema stability, stable root/location, naive-reader duplicate safety, and
classified content density.

Each broad assertion has a declared evidence source. Documentation claims cite a pinned
document/version. Stability claims include the exact builds and observation dates.
Content density uses non-overlapping logical-byte classifiers, with unknown bytes in the
denominator. Physical allocation remains a diagnostic.

## Run and capture procedure

For each scheduled repetition:

1. Create a fresh isolated project/native root and verify frozen input digests.
2. Record the complete identity tuple, launch evidence, observer method, and run canary.
3. Observe accepted/displayed boundaries, actions, arguments, results, exit states,
   helper nonces, timestamps, and before/after file hashes as they happen.
4. Preserve setup failures, retries, refusals, interventions, and incomplete attempts in
   the ledger; none can replace a scheduled repetition.
5. Quiesce the writer, enumerate every file and companion in the declared native root,
   and hash the inventory.
6. Decode a copy with original roots, vendor executable, and network unavailable; compare
   its canonical reconstruction with ordinary decoding.
7. Run broad probes, generate the 31-cell record, and bind every cell to observer IDs and
   native/documentation locators.

The collector may read only fresh benchmark roots and frozen inputs. It may not discover
old history, copy personal databases, use credentials during offline evaluation, or query
a vendor backend. A missing root/companion, changed helper, observer conflict, or
unverified Desktop boundary is invalid or unresolved, never a convenient zero.

## Result and report contract

Three scheduled repetitions receive equal weight. A ranked row requires all 31 metrics
resolved in all three runs, copied-root equality, and complete identity/evidence binding.
All five attempted rows remain visible. A leaderboard requires at least three qualified
rows. A complete CLI/Desktop pair is required only for the CLI/Desktop consistency
recommendation.

The public report preserves v0.4's compact leaderboard, five colored category bars,
vendor cards, gate matrix, **To pass, fix** list, and **If you're building on session
files** table. It adds the task timeline, exact three-run range, verification badge,
Desktop label, result citations, and downloadable evidence.

User recommendations and vendor fixes follow the exact rules in `rubric.md`. No unresolved
evidence can yield a recommendation. Every output is scoped to surface, build, collection
date/range, result IDs, evidence locators, and rule version.

## Crash qualification

After ordinary evaluation, run one separate actual-writer interruption at the declared
post-failure barrier. Capture before reopening. Decode one clone; relaunch only another.
Report pre-recovery preservation, relaunch mutation/recovery, and native continuation,
with process identity, interruption method, barrier, and pre/post hashes.

Crash qualification is unscored and cannot change rank, category points, badge,
recommendation, or the edition name. Publish `passed`, `failed`, or `not yet run`.
One run does not establish a reliability rate.

## Pre-collection gates

Before evaluated repetition 1, controls must prove all 31 metric paths, positive and negative
cases, JSONL/SQLite semantic equivalence, mutation sensitivity, duplicate penalties,
decoder blindness, copied-root isolation, badge states, recommendation/tie/no-result
states, deterministic vendor fixes, and independent response observation on every
surface. OpenCode must prove actual read/edit permission in its retained calibration.

Calibration may repair setup. It may not retune weights, thresholds, workload,
populations, state rules, or recommendation logic after evaluated collection begins.
