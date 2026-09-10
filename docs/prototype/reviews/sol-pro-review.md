## 1. Verdict

# **NOT READY for O1–O5 acceptance**

Connected GitHub access is working. I read the exact pinned commit `98ab5ab213398e7efcee98fd459a5d818ae4e3ac`; GitHub reports it as exactly one commit ahead of baseline `d8e344bb24ae86ac6ac0b268e3abc2e7906147f6`. The historical v0.4 files were not modified by this diff—the only modifications are the three design documents, while the prototype implementation and evidence are additions.

The reason for **NOT READY is not missing live evidence**. Live writers, vendor adapters, private stores, paid runs, and publication correctly remain gated. The blockers are inside the offline measurement contract:

1. A required assertion can be removed, or an unexpected native event added, while the evaluator still reports a complete pass.
2. Contradictory observer evidence can be cherry-picked into a synthetic “truth” that no observation actually established.
3. Unverified inspection locators can be presented as proof that a decoder—not the writer—lost a fact.
4. A modified result packet can retain its old `evaluation_id` and be rendered as all-pass.

O1 historical preservation appears ready. The fixture construction, answer-key isolation, safe staging, locator mechanics, genuine WAL test, and narrowly stated O5 copied-process reproduction are otherwise solid. O2/O4 need bounded corrections before accepting the combined milestone.

I did **not** execute the test suite. The 98 tests and eight reproduction cases remain reported project evidence, exactly as requested.

---

## 2. Prioritized actionable findings

### P1 — The scored population is not closed in either direction

**Verified source facts.** Bundle validation walks from assertions to observations, but never requires the independently observed scored population to be represented by assertions. The evaluator then iterates only `expected["assertions"]`, and its metrics are constructed only from the resulting rows. The decoder diagnoses duplicates only when the same `(session_id, event_id)` repeats; an additional event with a fresh ID is not accounted for by the evaluator.

Relevant code:

* [`session_bench/bundle.py:L214-L251`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L214-L251)

* [`session_bench/evaluate.py:L57-L70`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L57-L70)

* [`session_bench/evaluate.py:L134-L147`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L134-L147)

* [`session_bench/decoders.py:L159-L177`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/decoders.py#L159-L177)

**Trigger and inferred failure A: denominator shrink.** Start with `jsonl-v1-remove_fact`, delete only the `event.tr-2` assertion, update the expected-artifact hash/size in the manifest, and evaluate. `obs-010` may remain in the observer ledger, but no reverse-coverage check notices it. The missing native failure event now has no assertion. By inspection of the deterministic code path, C02 becomes **8/8 pass** and the complete result becomes all-pass under a new, structurally valid manifest/evaluation identity.

The manifest hash changing does not cure this: it identifies the shrunken bundle, but nothing says that bundle omitted a mandatory assertion. This contradicts the stated frozen-assertion and declared-denominator contract.

**Trigger and inferred failure B: unexpected native event.** Start with the intact pack, append a copy of `tr-2` as `tr-2-copy`, retain its valid `tool_call_id`, and update native/index/manifest hashes. It is a known kind, has a unique ID, and has a valid relationship, so it produces no duplicate or unknown diagnostic. All original 19 assertions still pass; the extra native event disappears from the scoring population.

The same problem allows an extra native `session` record to coexist with `execution.native_sessions == 2`, because the latter is checked against observer session IDs rather than the decoded native population.

**Smallest safe fix.**

Add an explicit primary scored population to the frozen observer/protocol contract. For example:

* Mark observer records as `primary_scored`, `supporting`, or `unscored`.
* Require every `primary_scored` observation key to map to exactly one primary assertion.
* Require every required assertion to map back to that population.
* Under complete capture, require every decoded known primary event to be mapped or explicitly classified as unexpected/unsupported.
* Keep relationship assertions separate so the existing helper-envelope assertion does not look like a second event assertion.

A repository-owned `assertion_set_id` or digest can additionally freeze the official constructed profile, but reverse coverage is the minimal functional correction.

**Regression tests.**

1. Delete `event.tr-2` from a remove-fact pack, rehash it, and require bundle validation to fail.
2. Add a known event with a fresh ID to an intact pack and require a non-pass population result.
3. Add an extra native session and require native-session population disagreement.
4. Preserve the existing supporting helper/file observations by explicitly marking them unscored.

---

### P1 — Contradictory observations can be cherry-picked field by field

**Verified source fact.** For every expected field, validation gathers values from all referenced observations and accepts the assertion when **any** candidate equals the expected value. It does not detect disagreement among observations, and it does not require all expected fields to come from one coherent observation or an explicit aggregation rule.

[`session_bench/bundle.py:L229-L239`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L229-L239)

**Concrete triggering input and inferred failure.**

For the same `tr-2` boundary/session/event, supply:

* Observation A: `status="failure"`, `exit_code=0`
* Observation B: `status="success"`, `exit_code=1`
* Assertion: `status="failure"`, `exit_code=1`

No observation established the asserted pair. Nevertheless, each expected field matches one candidate, so the bundle validates. Native data containing the synthetic combination then passes.

Even a simpler `failure` versus `success` disagreement is silently resolved in favor of whichever value the assertion author selected.

**Smallest safe fix.**

For each scored field:

* Collect all referenced observations that actually define it.
* If their canonical values disagree, reject the bundle or mark that field’s observer truth unresolved.
* Permit composition only when the assertion explicitly names a per-field source observation or a versioned aggregation rule.

**Regression test.**

Reference two observations with conflicting `fields.status`, choose either value in the assertion, and require validation failure or an explicitly unresolved observer-conflict state. Add a second case where different fields form a combination present in neither observation.

---

### P1 — `inspection.state="present"` is supported by unverified locators

**Verified source facts.**

For inspection locators, bundle validation checks only that `artifact_id` names a native artifact. It does not validate digest, coordinates, record hash, SQLite row, or whether the referenced record contains the asserted event. The strong locator verifier is called only for events returned by the decoder. If the decoder has no matching event, the evaluator trusts the assertion-supplied locator and emits `retained_decoder_incomplete`.

Relevant code:

* [`session_bench/bundle.py:L240-L250`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L240-L250)

* [`session_bench/evaluate.py:L77-L108`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L77-L108)

* [`session_bench/locators.py:L10-L87`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/locators.py#L10-L87)

**Concrete triggering input and inferred failure.**

Add a grounded observer/assertion for event `ghost`, set:

```json
"inspection": {
  "state": "present",
  "locators": [{"artifact_id": "native-session"}],
  "evidence_ids": ["provenance-mutation.json"]
}
```

There is no native `ghost` record. The bundle passes inspection validation because the artifact exists and is native. The evaluator has no match and reports:

```text
state = fail
outcome = retained_decoder_incomplete
finding = decoder_omission
```

That launders a malformed locator into a positive retention claim and incorrectly assigns responsibility to the decoder.

There is a second source-location problem in the mismatch branch: when a decoded record is wrong but inspection says a correct record exists elsewhere, the result retains the decoded record’s locators rather than switching to the independently inspected record’s locators.

**Smallest safe fix.**

Refactor locator verification into a resolver that can validate either:

* a decoded event locator, or
* an inspection locator plus the expected event identity/fields.

An inspection locator may support `present` only if it resolves to immutable bytes and those bytes establish the asserted session, event, kind, and relevant fields. Otherwise reject the bundle or leave the causal outcome unresolved. When the decoder misreads one record and inspection locates another, publish the verified inspection locator.

**Regression tests.**

1. Persist—not merely inject in memory—an inspection locator missing its digest/coordinates and require rejection.
2. Point a valid locator at the wrong native event and require rejection.
3. Omit the target from an injected decoder while supplying a fully verified locator to the correct raw record; only that case may yield `retained_decoder_incomplete`.

The existing injected-decoder test proves the intended classification, but it does not exercise persisted inspection-locator validation.

---

### P1 — Result validation and `render` do not preserve result identity or metric completeness

**Verified source facts.**

`validate_result` verifies each metric only against the rows that the metric itself names. It does not require:

* every row to appear in exactly one scenario metric,
* one metric per actual scenario,
* metric populations to equal all rows of that scenario,
* a nonempty metric set for nonempty rows,
* semantic consistency among row state, field states, findings, and outcome,
* recomputation of `evaluation_id`.

The evaluator initially computes a content-derived ID, but later validation never checks it. The standalone `render` command accepts arbitrary result JSON and invokes only `validate_result`.

Relevant code:

* [`session_bench/bundle.py:L116-L135`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L116-L135)

* [`session_bench/evaluate.py:L148-L157`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L148-L157)

* [`session_bench/__main__.py:L24-L39`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/__main__.py#L24-L39)

* [`session_bench/__main__.py:L83-L84`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/__main__.py#L83-L84)

**Concrete triggering input and inferred failure.**

Take the committed remove-fact result, which correctly contains a failed `event.tr-2` and C02 `8/9 fail`.

Then:

1. Delete the `event.tr-2` row.
2. Remove it from the C02 `assertion_ids`.
3. Change C02 to `8/8 pass`.
4. Leave the original `evaluation_id` unchanged.

`validate_result` accepts this packet by inspection of its logic. `render` then produces an all-pass report under the stale evaluation identity. A milder variant keeps the failed row but omits it from the metric, producing a passing headline with a contradictory row lower in the report.

**Smallest safe fix.**

* Require metrics to be an exact partition of rows by scenario.
* Require exactly one metric for every represented scenario and no extra scopes.
* Permit the sole `all/0/0/unresolved` metric only when rows are empty.
* Factor the evaluation-ID calculation into one function and recompute it in `validate_result`.
* Enforce basic row invariants—for example, `state="pass"` requires all fields pass and no blocking finding.
* For standalone rendering, verify the adjacent receipt’s semantic digest when one is present, or clearly label receipt-less input as unverified.

**Regression tests.**

Starting from a real negative result, test omitted metric IDs, omitted rows, duplicate cross-metric membership, missing metrics, inconsistent row/field state, and any content mutation under an unchanged `evaluation_id`. Every case should be rejected by `render`.

---

### P2 — Confirmed field failures are overwritten by unresolved joins and cycles

**Verified source fact.** Field disagreement first sets the row to `fail`. The relationship checks then unconditionally replace that with `unresolved` when a join cannot resolve or a branch cycle exists. Scenario metrics also give `unresolved` precedence over confirmed failures.

[`session_bench/evaluate.py:L101-L130`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L101-L130)
[`session_bench/evaluate.py:L138-L147`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/evaluate.py#L138-L147)

**Concrete triggering input and inferred failure.**

The built-in `missing_join` mutation changes `tr-2.fields.tool_call_id` from the independently expected `tc-2` to `missing-call`. That is already a confirmed exact-field contradiction.

The evaluator therefore:

1. marks the field `fail`,
2. sets the row to `fail` with `contradicted`,
3. discovers that `missing-call` has no target,
4. overwrites the row to `unresolved`.

The generic mutation test accepts either `fail` or `unresolved`, so it cannot detect this regression.

**Smallest safe fix.**

Use severity precedence:

```text
fail > unresolved > pass
```

An unresolved relationship may demote a pass to unresolved, but must not erase an already established contradiction. The row can remain `fail`, retain `outcome="unresolved"` if causal attribution is unclear, and include both `contradicted` and `unresolved_join`. Scenario metrics should likewise be `fail` if any member fails; only otherwise should unresolved dominate pass.

**Regression test.**

Assert the exact state of `event.tr-2` under `missing_join`, and exact states for `branch_dangling` and `branch_cycle`; do not accept a set of alternative outcomes.

---

### P2 — Prototype registry and subject labels are not an enforcement boundary

**Verified source facts.**

`validate_registry` considers a row `measured` whenever `live_tested_at` is merely non-null. The schema accepts any nonempty string and requires no run, capture, evaluation, or native-live evidence reference. Separately, `validate_bundle` never checks that `manifest.subject` corresponds to either constructed registry entry, nor that `subject.artifact_family == decoder.format`.

[`session_bench/bundle.py:L107-L113`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L107-L113)
[`session_bench/bundle.py:L137-L214`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L137-L214)

**Concrete triggering inputs and inferred failures.**

* Change a registry row to `status="measured"` and `live_tested_at="unknown"`. It validates despite having no measured evidence.
* Change an intact manifest’s `subject.harness` to `Codex` and its `artifact_family` to a vendor-looking value while retaining the constructed JSONL decoder. Rehash only the manifest. The bundle still evaluates successfully.

The generated report continues to say “no vendor qualification,” which limits the damage, but the registry/manifest can still be cited or consumed under a false subject label.

**Smallest safe fix.**

For `1.0-prototype`:

* Permit only `constructed_only` registry rows.
* Require constructed and derived manifests to match one of the two exact constructed subject profiles.
* Require `subject.artifact_family == decoder.format`.

Introduce `candidate` and `measured` only in the separately versioned live extension, where `measured` requires run/capture/evaluation references and a validated timestamp.

**Regression tests.**

Reject a measured prototype registry row, arbitrary date strings, vendor relabeling, and an artifact-family/decoder mismatch.

---

## 3. Acceptance evidence versus intentionally deferred capabilities

### Missing acceptance evidence after these findings

The existing reported suite does not establish resistance to:

* deletion of one mandatory assertion while other assertions remain;
* an unexpected known event with a fresh ID;
* extra native sessions outside the observer/assertion population;
* contradictory observations or cross-observation synthetic field combinations;
* malformed, wrong-record, or incomplete inspection locators in a persisted valid bundle;
* metric omission or result-row deletion under a stale `evaluation_id`;
* exact failure precedence for `missing_join`, branch dangling, and cycles;
* prototype registry/subject relabeling.

Those are the targeted regressions needed before O1–O5 should be marked accepted.

### Evidence that is already credible within its stated scope

* The baseline comparison supports the claim that v0.4 was not rewritten.
* Native-live input is explicitly rejected, and constructed origins/formats are clearly documented. [`session_bench/bundle.py:L137-L154`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/bundle.py#L137-L154)
* The decoder staging excludes expectations, observers, workload files, and fixture-generation code.
* JSONL and SQLite decoded locators are verified against source bytes.
* The WAL test is no longer bogus: it checkpoints the main database, commits a subsequent WAL-only row, verifies that the main file alone contains zero rows, copies the companions before closing the writer, and then requires recovery of that row. [`tests/test_v1_decoders.py:L82-L117`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/tests/test_v1_decoders.py#L82-L117)
* The O5 code and receipt accurately describe same-host, same-interpreter, copied-source/copied-bundle process reproduction—not independent-person or vendor reproduction. [`session_bench/reproduce.py:L15-L110`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/session_bench/reproduce.py#L15-L110)

The GitHub connector exposed the SQLite fixture only as truncated base64. I confirmed the SQLite header and reviewed the generator, manifests, decoder, locator logic, and WAL test, but I did not independently inspect every raw byte of the committed SQLite blobs.

### Correctly and intentionally deferred

These are **not** O1–O5 acceptance defects:

Vendor adapters and observed-writer claims; real collection/controller code; private session-store access; installations or paid sessions; three-configuration calibration; native continuation; cancellation and crash capture; C05/C06 recovery; compaction/subagent/attachment-lifecycle qualification; frozen production repetition sets; Linux/Windows isolation; independent-person reproduction; atlas/result publication; and benchmark release.

Archive, portable reproduction, continuation, and recovery should remain separate qualifications. Nothing in this review suggests giving C06 a product exemption.

---

## 4. Steward research pack

The pack is appropriately optional and polite. It asks first for `access_path` and willingness, accepts `missing_data` and `opt_out` as complete replies, warns against private histories/credentials/paid quota, and explicitly says that replying does **not** authorize a live run. That satisfies the desired willingness/access step before any bounded FX or Devin work.

One correction is needed: under “Required first-gate scenarios,” it requires “two sessions for C04,” even though the named first gate is only C01+C02. That line could cause a steward to invent or perform an unapproved C04 operation. Move it to a later C04 checklist or delete it from the first-gate section.

[`docs/prototype/steward-research-pack.md:L55-L61`](https://github.com/jazzyalex/session-bench/blob/98ab5ab213398e7efcee98fd459a5d818ae4e3ac/docs/prototype/steward-research-pack.md#L55-L61)

---

## 5. Recommended next step before L0/F0

Make one bounded O2/O4 hardening patch covering:

1. scored-population closure and unexpected decoded events;
2. observer-conflict handling;
3. full verification of inspection locators;
4. complete result-metric and evaluation-ID validation;
5. fail-over-unresolved precedence;
6. constructed-only registry/subject enforcement.

Add the focused regression cases above, regenerate the committed fixture/evidence outputs and reproduction receipt, rerun the expanded local suite, and perform one more read-only review. After that is clean, prepare the separately authorized one-CLI C01+C02 adapter/observer/capture plan with caps. No live collection is needed—or authorized—to resolve these findings.
