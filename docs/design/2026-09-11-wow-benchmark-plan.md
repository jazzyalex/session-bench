# Session-Bench v1.0: a deeper v0.4 report card

Status: accepted direction, 2026-09-11. The owner subsequently authorized bounded local
prototype work. OpenCode CLI now has three evaluated 19-metric Session Survival runs;
the full 31-metric cohort, publication, outreach, and changes to the historical v0.4
leaderboard remain outside the completed scope.

## Product decision

Keep the v0.4 product shape and question:

> **Your agent wrote the code. What did it record?**

V1.0 is an evolution of the vendor report card. It keeps a compact ranked leaderboard,
five clear angles, a gate matrix, per-vendor **To pass, fix** guidance, and the **If
you're building on session files** use-case table. It adds the depth v0.4 lacked: a
controlled two-turn coding task, independent observation, native-record locators,
copied-root reproduction, repeated runs, Desktop rows, verification badges, and
machine-citable recommendations.

The primary public output is one score out of 100 with five category bars:

| Public angle | Points | Reader question |
|---|---:|---|
| Record fidelity | 30 | Did the record preserve what actually happened? |
| Causality & context | 20 | Can a reader connect turns, actions, results, and explanations? |
| Usage & attribution | 15 | Can responses be attributed and usage interpreted honestly? |
| Portability & openness | 20 | Can another tool read and move the complete record? |
| Durability & signal | 15 | Is the format stable, versioned, duplicate-safe, and information-dense? |

These weights are frozen for the first v1.0 evaluation. The category profile is more
important than the ordinal rank. The report never hides an incomplete row behind a low
score.

## What v1.0 preserves and adds

| v0.4 strength or limit | v1.0 treatment |
|---|---|
| Compact ranking and five-angle report card | Preserve the familiar ranking, colored category bars, exact ties, and scan-friendly rows |
| Vendor card with failed gates and fixes | Generate a separate evidence-linked **To pass, fix** list for every surface |
| Gate matrix | Expand it to 31 stable metric IDs with observed, retained, and evidence states |
| “If you're building on session files” table | Expand it into citable user recommendations derived from declared rules |
| CLI-only scope | Add Codex Desktop and Claude Desktop Code (Local) as independently tested rows |
| Many manually supported gates | Require native locators, observer IDs, immutable manifests, and reproducible evaluation |
| Presence/absence checks | Add population coverage, causal joins, revision trace, and per-response attribution |
| Broad format traits | Retain them as 12 scored format metrics rather than replacing them with a survival-only score |
| `not run` honesty | Keep unresolved evidence null and block rank or recommendation |
| Crash gate reserved for 1.0 | Test it separately as a qualification; do not add crash points to the report card |

V0.4 remains a historical CLI field study with its own rubric and data. V1.0 does not
translate old verdicts into new points, overwrite the old leaderboard, or present rank
changes across editions as product regressions or improvements.

## First-edition surfaces

The target cohort is five separately identified configurations:

1. Codex CLI.
2. Codex Desktop.
3. Claude Code CLI.
4. Claude Desktop Code (Local).
5. OpenCode CLI.

Codex CLI/Desktop and Claude CLI/Desktop are paired surface controls. Cursor CLI/Desktop
remain paused historical attempts and are not silently substituted into this release cohort.
Every public row
names the provider, harness, surface, execution mode, OS, app or executable build, model,
configuration, protocol, workload, repetitions, collection dates, and result IDs. A CLI
result never stands in for Desktop, and a shared storage family does not establish shared
behavior.

All five surfaces receive the same workload, independent-observation fields, expected
populations, rubric, and state rules. Surface adapters may differ only in acquisition and
native locators.

## One leaderboard, four layers of detail

The report keeps v0.4's visual and editorial personality:

1. **Compact leaderboard.** Rank, surface/build, `/100`, three-run range, five category
   bars, and verification badge. CLI and Desktop are visible in the row label.
2. **Vendor report card.** The five angles, the 31-cell gate matrix, evidence links, and
   an automatically selected **To pass, fix** list.
3. **Observed-vs-recorded timeline.** Requirement → inspection → failing test → response
   → correction → edit → passing test → final response, with each fact marked retained,
   partial, absent, or unresolved.
4. **Use-case recommendations.** Evidence-generated answers for audit-ready records,
   portable archives, usage accounting, CLI/Desktop consistency, lean complete records,
   and long-term archives.

The methodology and native evidence sit behind the readable report card. Every public
cell and recommendation exposes its metric IDs, result IDs, and evidence locators.

## Evidence model: 19 deep metrics plus 12 broad metrics

The 19-metric Session Survival audit is the event-level evidence layer. It asks whether
the two submitted turns, two visible responses, four actions, four results, changed-file
boundary, causal links, corrected requirement, response attribution, usage, and portable
copy can be reconstructed. It drives the task timeline and contributes to four public
angles.

Twelve broad format metrics restore the report-card breadth:

1. per-event timestamps;
2. readable rationale or summary;
3. thread structure;
4. standard-tools readability;
5. documented format;
6. self-contained identity;
7. declared format version;
8. honest version signal;
9. observed schema stability;
10. stable root or location;
11. naive-reader duplicate safety;
12. classified content density.

The [rubric](../survival-v1/rubric.md) maps all 31 stable IDs into the five fixed budgets.
The deep audit is not a second leaderboard and the broad checks are not an appendix.
They are complementary evidence for the same public score.

## Frozen workload

Use one isolated synthetic checkout task:

1. Create a fresh project and run canary.
2. Submit R1 with a declared context marker.
3. Observe inspection, a deterministic failing test, and the completed R1 response.
4. Submit R2, which explicitly supersedes one R1 rule.
5. Observe one file edit, a deterministic final test, and the completed R2 response.
6. Record accepted turns, visible response boundaries, actions, arguments, outputs, exit
   states, helper nonces, timestamps, and before/after hashes independently.
7. Quiesce the writer, capture the complete declared native root, copy it, and evaluate
   the copy with original roots, vendor executable, and network unavailable.

Coding success is reported but unscored. A wrong final edit can still leave a faithful
record. A skipped required operation is `unexercised`; it does not shrink the expected
population or become a format failure.

## Scoring and missing evidence

Each metric produces a measured fraction or fixed assertion. Event populations first
select the four frozen actions/results/relations by independently observed native and call
identity; exploratory events remain supporting evidence. Within that frozen population,
metrics use `correct / max(observed eligible, decoded eligible)`, so missing facts,
duplicates, and dangling records cannot improve a score. Fixed assertions pass or fail
only when the required evidence is complete. Calculate at full precision and round
displayed scores to one decimal using round-half-up.

| State | Public effect |
|---|---|
| `measured` | Contributes its measured value |
| `native_absent` | Complete evidence proves the fact was not retained; contributes zero |
| `contradiction` | Retained data conflicts with observation; contributes zero |
| `unresolved` or `decoder_unsupported` | Null; blocks total, rank, badge, and recommendation |
| `unexercised` or `invalid_capture` | Null; run is unranked |
| `not_applicable` | Not permitted for a required v1.0 metric |

Three scheduled evaluated repetitions receive equal weight. A configuration is ranked
when all 31 metrics resolve in all three repetitions and the complete evidence package
recomputes deterministically from its copied immutable bundle. A resolved portability
failure contributes zero to its metric; it does not suppress the rest of the report card.
Exact ties use competition ranking. All attempted rows remain visible. Fewer than three
qualified rows yields unranked report cards rather than a leaderboard. A missing complete
CLI/Desktop pair blocks only the CLI/Desktop consistency recommendation.

Incomplete categories show known quality, evidence coverage, and possible range. They do
not display a partial total as though missing checks failed or passed.

## Verification badges

Badges describe evidence status, not product quality:

| Badge | Machine rule |
|---|---|
| **Fully reproduced** | Three scheduled runs; all 31 metrics resolved; complete public immutable evidence packages; deterministic offline recomputation receipts cover the exact result package, including any measured failures |
| **Independently reproduced** | Fully reproduced, plus a second operator or environment recomputed the exact package and published its receipt |
| **Partially verified** | At least one valid native bundle and evidence-linked metric is measured, but repetitions, metrics, public bundle, or deterministic recomputation are incomplete; no overall rank or user recommendation |
| **Unranked** | The workload was unavailable, unexercised, invalidly captured, or has no valid evidence-bound metric result |

Every badge cites `surface_id`, build, collection date or date range, `result_id`,
`evaluation_id`, and reproduction receipt ID. A badge without that tuple is not shown.

## Citable user recommendations

Recommendations expand v0.4's **If you're building on session files** table. They are
generated from final result data, not written as promotional awards.

Common eligibility requires a **Fully reproduced** badge, three complete repetitions,
all recommendation inputs resolved, public evidence IDs, and the declared minimum below.
If the top two eligible results are separated by less than five normalized percentage
points on the recommendation objective, the table reports a qualified set rather than a
single winner. If no row qualifies, it emits `no_recommendation` with reason IDs.

| Recommendation key | Minimum qualification | Selection objective |
|---|---|---|
| `audit_ready` | Record fidelity ≥ 27/30 and Causality & context ≥ 18/20 | Highest normalized mean of those two categories |
| `portable_archives` | Portability & openness ≥ 18/20 and all four deep portable assertions pass in 3/3 runs | Highest Portability & openness |
| `usage_accounting` | Usage & attribution ≥ 13.5/15; per-response usage, token semantics, and reconciliation all pass in 3/3 runs | Highest Usage & attribution |
| `cli_desktop_consistency` | Complete vendor CLI/Desktop pair; each row qualifies; pair differs by no more than five normalized points overall and in every category | Smallest mean normalized category distance; exclusive winner requires a two-point advantage over the next pair |
| `lean_complete_record` | Record fidelity ≥ 27/30, Causality & context ≥ 18/20, Portability & openness ≥ 18/20, and classified-byte coverage ≥ 95% | Lowest median marginal physical KiB per recovered required semantic fact; exclusive winner requires at least 10% lower value |
| `long_term_archives` | Durability & signal ≥ 13.5/15, Portability & openness ≥ 18/20, at least two observed builds, and a declared observation window of at least 30 days | Highest normalized mean of Durability & signal and Portability & openness |

Each generated recommendation contains the key, status, scoped surface/build/date tuple,
result IDs, objective values, threshold values, compared cohort IDs, evidence locators,
and rule version. No recommendation is emitted from unresolved evidence. Wording must say
“among these tested configurations” and cannot become a product-wide claim.

## Vendor improvement recommendations

User selection and vendor improvement are separate outputs. Each vendor card emits up to
three **To pass, fix** items chosen deterministically:

1. validity or unresolved blockers first;
2. failed metrics in the lowest normalized category next;
3. within a category, the largest available point recovery first, then metric ID.

Each item names the failed or unresolved metric, the observed consequence, the smallest
evidence-supported format change, and the exact observer/native locators. The text does
not infer implementation intent and does not compare a vendor with a preferred product.

## Crash qualification is separate and unscored

After the score contract is frozen, run one declared actual-writer interruption per
configuration. Publish three fields: pre-recovery record preservation, relaunch mutation
or recovery, and native continuation. Each field carries process identity, interruption
method, barrier, pre/post hashes, and evidence IDs.

Crash qualification contributes zero points and does not alter rank. Publish it as a
prominent `passed`, `failed`, or `not yet run` diagnostic. One attempt supports a scoped
qualification result, not a reliability percentage or a naming gate.

## Storage diagnostics

Physical size is reported beside, not inside, the score. Report cold initialization,
marginal allocated bytes, all files and companions, median and range, recovered semantic
facts, KiB per recovered fact, classified logical bytes, and unknown bytes. Never compare
JSONL record bytes with SQLite page allocation as though they were the same population.

`broad.classified_content_density` is the scored format property. It uses useful
classified logical bytes divided by all in-scope logical record bytes; unknown bytes stay
in the denominator. Physical footprint is used only by the qualified `lean_complete_record`
recommendation and diagnostic charts.

## Calibration gates

Before evaluated repetition 1:

1. Every one of the 31 metric IDs has positive and negative or partial controls.
2. Semantically equivalent JSONL and SQLite fixtures receive identical semantic scores.
3. Removing, swapping, duplicating, or de-linking controlled facts changes only the
   intended metrics or validity states.
4. Decoder output is byte-identical when observer answers change but native bytes do not.
5. Copied-root evaluation succeeds with original roots, vendor executable, and network
   unavailable; source hashes remain unchanged.
6. Each surface has a complete-root contract and independent visible-response observer.
7. OpenCode's retained calibration proves actual read/edit permission before evaluation.
8. Recommendation generation is tested for qualifier, tie, no-qualifier, and unresolved
   inputs.
9. Vendor **To pass, fix** output is traceable and deterministic.

The first Desktop operation may be a **discovery calibration** whose purpose is to
establish the GUI observer and complete-root contract. It remains visible and unranked.
Calibration may repair setup. It may not tune weights, thresholds, workload, populations,
or recommendation rules after evaluated collection begins.

## Delivery sequence

| Phase | Deliverable | Gate |
|---|---|---|
| 0. Lock contract | Protocol, 31-metric rubric, badges, recommendation rules, report wireframe | All public documents agree |
| 1. Freeze workload and truth | Task, canaries, observer schema, expected populations | Every fact observable on CLI and Desktop |
| 2. Build evaluator controls | Scorer, equivalence fixtures, mutations, recommendation generator | Calibration gates 1–5 and 8–9 pass |
| 3. Close adapters | Complete-root and native-locator adapters for five surfaces | No personal-state discovery; isolated decode works |
| 4. Retain calibration | One visible unranked calibration per surface | Observation and permissions proven |
| 5. Collect | Three scheduled repetitions per surface | All five release rows qualify; pair-specific recommendations remain gated separately |
| 6. Qualify crash behavior | One actual-writer interruption per included surface | Publish passed, failed, or not-yet-run diagnostic |
| 7. Build report | Leaderboard, vendor cards, matrix, timeline, recommendations, JSON/CSV and images | Arithmetic, citations, privacy, accessibility, and mobile layout verified |
| 8. Reproduce package | Immutable bundles, offline command, deterministic recomputation receipt, correction record | Candidate is reviewable; a second-operator badge remains separate |

## Definition of done

The v1.0 candidate is ready for review when:

- the five public angles use the exact 30/20/15/20/15 weights;
- all 19 deep and 12 broad metric contracts have executable controls;
- all five release-cohort rows have three fully resolved repetitions;
- every scored cell resolves to independent observation and immutable native locators;
- the compact leaderboard, vendor report cards, gate matrix, timeline, and use-case table
  are produced from the same result data;
- badges, user recommendations, and **To pass, fix** items are generated by the frozen
  rules and cite scoped result IDs;
- all attempts and incomplete states remain visible;
- copied public bundles reproduce without original stores, vendor binaries, or network;
- crash qualification is resolved separately for every included surface;
- the historical v0.4 evaluator, files, and results remain unchanged; and
- no output contains a live claim or publication statement unsupported by an authorized,
  immutable result package.

The benchmark's contribution is a broad report card with unusually deep evidence: what a
real work sequence preserved, how the format behaves as an archive, and what a reader or
vendor can do with that result.
