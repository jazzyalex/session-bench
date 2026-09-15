# Session-Bench v1 finish plan

Status: executable local plan, 2026-09-14. This plan finishes the **bright five-angle
v1 benchmark**: v0.4-style ranking and recommendations, backed by 31 evidence-bound
measurements. It does not turn archive recovery or crash behavior into the whole product.

The release cohort is five separate configurations:

```text
Codex CLI · Codex Desktop · Claude Code CLI · Claude Desktop Code (Local) · OpenCode CLI
```

Cursor CLI/Desktop remain in the historical attempt ledger and paused campaign. They are
not silently substituted for Claude and they do not delay this release cohort.

## 2026-09-15 bounded-closure freeze

The owner froze the live evidence set after the 15 evaluated runs. There will be no
additional live captures, desktop exploration, replacement repetitions, or new surface
implementation in this pass. Remaining work is limited to deterministic repair and
verification of already-captured evidence, report generation, privacy checks, and final
claim review. If an existing package cannot satisfy independent replay without new live
evidence, the candidate records that limitation; it does not schedule another run or
weaken the public gate.

## Live execution tracker

Update this table only after the named acceptance evidence exists. Independent implementation
and capture work may run in parallel, but no later release gate can waive an earlier acceptance
condition.

| Order | Step | State | Acceptance evidence / blocker |
|---:|---|---|---|
| 0 | Preserve historical truth and stop stale promotion | Complete | Current reports label all five sanitized packets as local/unreleased review candidates; no public score, rank, push, or post exists. |
| 1.1a | Build the OpenCode replay-runtime source snapshot | Complete | Synthetic controls passed for the fixed decoder/comparator/scorer closure, manifest determinism, symlink refusal, and source-copy mutation refusal. |
| 1.1b | Add the closed package verifier and standalone recheck runner | Complete | The copied runtime starts its standalone recheck command without checkout imports; its manifest hashes decoder, comparator, scorer, evidence validator, package verifier, and runner. |
| 1.2 | Build three corrected OpenCode packages | Complete | `evaluation-correction` exists for each repetition; every manifest binds the historical package digest, native DB/WAL/SHM hashes, and `missing_decoder_runtime`. |
| 1.3 | Recheck corrected OpenCode packages and damage controls | Complete | Three `correction-replay-receipt.json` records show temporary-copy replay using only the embedded runtime; each copied damage control deleted the observed R2 native part and reduced visible-response accuracy from 2/2 to 1/2. |
| 1.4 | Create sanitized OpenCode hand-off derivatives | Complete | Each `public-derivative` has a raw-to-sanitized receipt and passed the private-root/credential/account-marker scan. Raw SQLite and replay runtimes remain withheld. |
| 2 | Complete 31-metric adapter contracts | Complete | All 31 metric paths validated; all 15 evaluated runs resolve 31/31 cells with observer IDs plus native/documentation locators and local copied-root replay/equality/loss evidence. |
| 3 | Qualify Codex CLI/Desktop, Claude CLI/Desktop, and OpenCode CLI | Complete | All five release configurations met calibration gates and froze evaluated inputs: Codex CLI / Codex Desktop (`gpt-5.6-sol`), Claude Code CLI (`claude-sonnet-5[1m]`), Claude Desktop Code (Local) (`claude-opus-5`), OpenCode CLI (`opencode/muse-spark-1.3-contributor-free`). Cursor CLI/Desktop remain paused/historical. |
| 4 | Collect three evaluated runs per qualified configuration | Complete | 15 valid evaluated runs exist, 3 per configuration. Every run has 31/31 resolved metric cells and local copied-root replay/equality/loss evidence. |
| 5 | Generate `/100`, rank, recommendation, and report candidate | **Complete · unpublished** | `artifacts/survival-v1-review-candidate` is generated only from the five sanitized packets. It contains the HTML report, SVG scorecard, JSON, CSV, evidence index, gate record, and artifact manifest. Codex CLI/Desktop tie at published 87.0; OpenCode CLI 83.3; Claude Desktop 82.8; Claude CLI 81.0. Generated recommendation rows are included. |
| 6 | Independent reproduction and release review | **Closed for this pass · publication blocked** | Local packet validation, deterministic report generation, and candidate privacy scan completed. Full independent native reproduction did not qualify from the frozen packages and is recorded as `independent_native_reproduction_incomplete`. The cost freeze ends this phase without new captures or broad replay work. Final publication review remains required only if release work is reopened. |
| 7 | Publish | **Not authorized** | Candidate metadata sets `published=false` and `eligible=false`. No scores have been published as a v1 leaderboard. |

## Non-negotiable release rule

A configuration receives a public `/100`, rank, badge, or recommendation only when three
scheduled evaluated runs resolve all 31 metrics; each run has a complete copied-root
package, a closed replay runtime, and an independent reproduction receipt. A calibration,
format observation, quota refusal, or one successful run is never a score.

The public page keeps every attempted row visible. A blocked or incomplete row says why;
it is never converted to zero or hidden to make the ranking cleaner.

## Phase 0 — Preserve the truth already collected

**Goal:** make local evidence safe to keep and impossible to overclaim.

1. Keep v0.4 and all historical attempts unchanged.
2. Keep the existing OpenCode 81.0 result labelled as an unreproduced local Survival
   calibration. Do not use its current local render, social draft, or score as a release
   asset.
3. Maintain the three current state documents together:
   [preliminary report](../../docs/survival-v1/preliminary-report.md),
   [calibration status](../../docs/survival-v1/calibration-status.md), and
   [attempt ledger](../../docs/survival-v1/attempt-ledger.md).
4. Record every refusal, quota stop, operator intervention, malformed bundle, and
   privacy failure as `invalid`, `blocked`, or `unresolved`—never as writer quality.

**Exit evidence:** the public scorer rejects every current candidate for a full `/100`;
the local report and social drafts say `unreleased` and `unranked`.

## Phase 1 — Repair the OpenCode evidence chain offline

**Goal:** turn the three existing local OpenCode captures into new correction packages
that can be replayed from their own bytes and code.

1. Implement the closed runtime contract in
   [reproducibility-correction.md](../../docs/survival-v1/reproducibility-correction.md):
   bundled decoder, comparator, scorer, standalone recheck runner, and hash manifest.
2. Create one correction package per historical package. Preserve the original package
   unchanged and bind its digest, native DB/WAL/SHM hashes, and correction reason
   `missing_decoder_runtime`.
3. Deny the original package, vendor executable, network, and regular repository imports
   during the correction runtime replay.
4. Require the correction decode and measurement to equal the retained historical
   versions. Re-run the selected damage control in that same runtime.
5. Build a separate local replay receipt for each corrected package. It must remain
   `independent_reproduction: false`.
6. Produce a labelled sanitized derivative for public hand-off; do not publish raw SQLite
   or native-derived records containing absolute workspace paths.

**Exit evidence:** three new correction-package manifests, three successful standalone
rechecks, three successful damage controls, raw-to-sanitized receipts, and no dependency
on the working checkout. This phase creates no new vendor session.

## Phase 2 — Finish the 31-metric adapter contract

**Goal:** one shared scoring contract, with evidence adapters that remain genuinely
surface-specific.

1. Add the 12 broad measurements to every adapter beside the existing 19 Survival
   measurements: timestamps, rationale, thread structure, standard readability,
   documentation, self-contained identity, version signal, observed stability, stable
   root, duplicate safety, and classified content density.
2. Require every metric to have its observer IDs plus native or documentation locators.
3. Add positive, selected-loss, duplicate, missing-companion, malformed, and
   original-root-dependency controls for each format family.
4. Generate the five category bars from the one 31-cell record:

   | Category | Points |
   |---|---:|
   | Record fidelity | 30 |
   | Causality & context | 20 |
   | Usage & attribution | 15 |
   | Portability & openness | 20 |
   | Durability & signal | 15 |

5. Confirm that no decoder receives observer truth or answer keys and no broad metric is
   inferred from physical file size.

**Exit evidence:** deterministic unit controls for all 31 metric paths and an
evidence-schema validation that rejects an unbound metric, an unknown metric, a hand-made
badge, or a recommendation without locators.

## Phase 3 — Qualify each configuration before scoring it

**Goal:** convert a calibration into permission for three evaluated runs, one surface at a
time.

| Configuration | Current evidence | Required calibration gate |
|---|---|---|
| OpenCode CLI | Three local Survival records; correction runtime missing | Finish Phase 1, then validate the broad 12 cells from copied bundles. |
| Codex CLI | Invalid two-turn attempt | Current-record decoder, clean non-mutating observer path, complete root, and copied-root loss control. |
| Codex Desktop | Core two-turn calibration | Complete native-root and companion inventory, canonical copied representation, GUI observation, and full 31-cell wrapper. |
| Claude Code CLI | Two pre-tool refusals | A successful two-turn synthetic run, complete project-keyed family inventory, decoder and damage control. Refusals stay invalid/N/A. |
| Claude Desktop Code (Local) | Core two-turn calibration and selected loss control | Complete cross-root companion proof, GUI observation evidence, canonical copied representation, and full 31-cell wrapper. |

For every calibration:

1. Create a fresh synthetic project and declare the native capture boundary. An isolated root
   is preferred; an authenticated normal root is allowed when before/after discovery is
   metadata-only, pre-existing bytes remain unopened, and only exact newly created synthetic
   artifacts are copied.
2. Bind exact build, model/configuration, OS, launch mode, observer channel, workload
   digests, and fresh run canary before R1.
3. Observe R1/R2 boundaries independently. Claude Desktop retains reviewed visual evidence
   and accessibility text. Codex Desktop uses the supported app task API, which records task,
   turn, response, command, and file-change boundaries independently of the copied rollout.
   CLI surfaces retain the exact declared stream.
4. Inventory every fresh root/companion before decoding. Copy it and deny originals,
   vendor executable, and network for the offline decode.
5. Delete or damage one selected native fact only in a copied control and confirm the
   evaluator reports that loss.

**Stop conditions:** Codex stays stopped at the 95% quota threshold; two matching Claude
pre-tool refusals stop further Claude CLI submissions; an incomplete root, a privacy leak,
or missing GUI proof invalidates the attempt; Cursor remains paused until explicitly
reopened. None of these produces a format score.

**Exit evidence:** one accepted calibration packet per configuration with all collection
gates green. Only then freeze that configuration's evaluated inputs.

## Phase 4 — Collect evaluated repetitions

**Goal:** collect exactly three comparable runs for each qualified configuration.

1. Freeze the workload, metric rules, decoder/runtime version, roots, model/configuration,
   evidence schema, and report generator before repetition 1.
2. Run repetitions 1, 2, and 3 in fresh isolated roots or the accepted metadata-safe normal-root
   route. Do not substitute calibrations, retries, or partial attempts.
3. For every run, create a closed sanitized evidence package, complete root/companion
   inventory, offline copied-root replay, canonical-equality receipt, 31-cell matrix,
   selected damage control, and reproducible runtime receipt.
4. Record crash qualification once per configuration after ordinary collection. It remains
   visible and unscored.
5. If a run is invalid, preserve it and schedule only the corresponding originally frozen
   correction process; never silently renumber a retry as a valid repetition.

**Exit evidence:** 15 complete evaluated packages—three for each release configuration—
or an honest unranked report naming the exact configuration and blocker.

## Phase 5 — Score, rank, and generate the product

**Goal:** make the v1 result as easy to quote as v0.4 without weakening its proof.

1. Recompute each run with its bundled runtime, then aggregate each configuration with an
   equal-weight three-run mean and min–max range.
2. Allow `/100`, rank, **Fully reproduced**, and recommendations only for configurations
   with all 31 cells resolved in all three runs.
3. Require all five qualified release configurations for any public leaderboard. Require
   a qualified CLI/Desktop pair for the CLI/Desktop consistency recommendation.
4. Generate from the scored records only:
   - the compact v0.4-style leaderboard and five colored bars;
   - the 31-cell report card and observed-versus-recorded timeline;
   - **If you're building on session files** recommendations;
   - **To pass, fix** vendor cards;
   - a plain-language result card and social/press/blog templates;
   - JSON, CSV, SVG/PNG, evidence download index, citations, and correction ledger.
5. Verify that every visible sentence resolves to a result ID, metric IDs, and evidence
   locators. A missing result produces `Unranked`, never a flattering estimate.

**Exit evidence:** a deterministic release candidate regenerated from only its declared
packages, with arithmetic, scope, badge, recommendation, mobile-accessibility, and
v0.4-preservation checks passing.

## Phase 6 — Independent reproduction and release review

**Goal:** make the benchmark citable rather than merely locally impressive.

1. Give a second operator or isolated environment the sanitized correction/evaluation
   package and its closed runtime. They must have no original root, vendor executable,
   network, or implementation output beyond the package.
2. Require them to verify the package/runtime manifests, recompute the decode, measurement,
   score, and damage control, then write a bound receipt.
3. Repeat for every row intended for the ranked leaderboard. A local replay stays local.
4. Run privacy review over every public byte: no credentials, account data, personal
   history, absolute home paths, private source, or raw native derivative presented as
   untouched evidence.
5. Run an Oracle Sol Extra High review against the pushed release-candidate commit through
   connected GitHub. Resolve material findings and rerun the affected checks.
6. Create citation metadata, immutable release assets, DOI archive, correction process,
   reproducibility instructions, and vendor dispute channel.

**Exit evidence:** independently reproduced receipts, Sol review record, public-data
review, and a release candidate that can be regenerated from the archive.

## Phase 7 — Publish and maintain

**Goal:** release a memorable benchmark with a correction path.

1. Publish the report, machine-readable results, packages, report-card images, blog post,
   press note, and social posts from the reviewed release candidate.
2. Lead with the quotable `/100` and five bars; immediately scope it to
   surface/build/model/date and link the evidence card.
3. Publish blocked rows alongside ranks, plus the rules for corrections and vendor
   submissions.
4. Reinspect each supported configuration on a fixed freshness schedule. New build,
   storage drift, or root change opens a new observation; it never edits the old result.

Publishing, a Git push, a DOI upload, or external posts are final external actions and
need the owner's explicit release instruction after Phase 6 has produced a reviewable
candidate.

## Work allocation

Use `opencode/muse-spark-1.3-contributor-free` only for bounded public/synthetic work:
fixture controls, standalone runtime scaffolding, adapter tests, schema/report mechanics,
and deterministic repairs. Pin the model on every invocation; isolate its OpenCode state;
never supply a personal session, credential, private root, or unsanitized native capture.

The main session owns benchmark policy, scoring, evidence acceptance, privacy/redaction,
surface qualification, live collection decisions, and final review. Computer Use is used
for Claude Desktop GUI evidence; Codex Desktop uses the native task APIs. A Muse task that
fails twice on the same issue is escalated to the main session.
