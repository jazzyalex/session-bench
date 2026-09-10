## Source access and reviewed revision

Connected GitHub access is confirmed. I reviewed the exact pushed commit:

`c4807214ffeedffb404cd84ff73ceab82c615322`

GitHub identifies it as **“fix: close offline measurement acceptance gaps”** and names `b2803f40ce5e22a97c3a91cb1cda0f2054c0aa8b` as its direct parent. The connected comparison reports that `c4807214…` is two commits ahead of the previously reviewed `98ab5ab…`, so the attachment-reference correction is included in the reviewed tree.

The prompt described CI as pending, but by the time of this review, the GitHub Actions run for `c4807214…` had completed successfully. Its successful steps included the v0.4 byte-identical regeneration check and `pytest`. This was Ubuntu CI, not the macOS isolation run.

Model disclosure: this session is running **GPT-5.6 Sol Pro**. The runtime did not expose a switch to Extra High, so this is not an Extra High execution.

# **NOT READY for bounded offline O1–O5 acceptance**

The result is much closer. Population closure, observer conflicts, persisted locator verification, metric partitioning, failure precedence, constructed-only identity enforcement, the C04 placement, and attachment payload verification are materially fixed.

One consequential result-contract hole remains: a result can retain a passing state while all native locators and independent observation references are deleted. After recomputing the content-derived evaluation ID and receipt, the standalone renderer accepts and labels the packet verified. This is an internal semantic-validity defect, not a demand that hashes prove acquisition or authorship.

There is also one non-blocking research-pack documentation contradiction.

---

## Actionable findings

### P1 — The result contract accepts an evidence-free passing row

**Confirmed source defect; counterexample inferred from the exact source, not executed against a checkout.**

`session_bench/result_contract.py:53-93` validates passing field values, findings, and the one-way condition that a `pass` row must have `outcome="retained_and_reconstructed"`. It never examines `locators`, `observation_ids`, or `inspection_evidence_ids`. The later pass checks at `result_contract.py:117-128` add capture, execution, and applicability requirements, but still do not require any evidence reference.

The result schema allows all three arrays to be empty because they have no `minItems` constraint.

The public `render` path calls this validator and then accepts any adjacent receipt whose semantic digest matches the edited result. It does not have another source-evidence check. `session_bench/__main__.py:26-32`.

#### Concrete counterexample

Start with the all-pass hardened JSONL result and make only these edits to `event.m-1`:

```python
row["locators"] = []
row["observation_ids"] = []
```

Leave all fields, `state="pass"`, `outcome="retained_and_reconstructed"`, and the C01 5/5 metric unchanged. Then recompute:

```python
result["evaluation_id"] = expected_evaluation_id(result)
receipt["semantic_sha256"] = semantic_sha256(result)
```

By inspection of the exact implementation:

* all fields still validate as passing;
* the metric remains an exact partition and stays 5/5;
* the new evaluation ID is internally consistent;
* the new receipt matches the edited packet;
* `render(result, receipt)` reports a verified all-pass result;
* the passing row has no native source locator and no independent observation reference.

This can result from deliberate packet editing or from a serialization/export regression that accidentally drops evidence arrays. It is not merely a stale-ID attack.

A second form of the same incomplete row semantics is that a row with `state="fail"` or `state="unresolved"` can still carry `outcome="retained_and_reconstructed"`. The validator enforces the mapping only in the pass-to-outcome direction. Yet the design defines “Retained and reconstructed” as native evidence containing the fact and the decoder correctly reconstructing the scored fields.

#### Smallest safe fix

For the current `1.0-prototype` result contract:

```python
if row_state == "pass":
    if not row["locators"]:
        raise ValueError("pass row requires native source locator")
    if not row["observation_ids"]:
        raise ValueError("pass row requires observation reference")

outcome = row["outcome"]

if outcome == "retained_and_reconstructed" and row_state != "pass":
    raise ValueError("retained-and-reconstructed outcome requires pass state")

if row_state == "unresolved" and outcome != "unresolved":
    raise ValueError("unresolved row requires unresolved outcome")

if outcome in {"retained_decoder_incomplete", "verified_absent"} and row_state != "fail":
    raise ValueError("causal failure outcome requires fail state")
```

For the current generated fixtures, every legitimate passing primary or relationship row already has both native locators and observation IDs, so this should not require a design change.

#### Regression tests

Add tests to `tests/test_v1_result_contract.py` that:

1. clear `locators` from a pass row;
2. clear `observation_ids` from a pass row;
3. set a failed row’s outcome to `retained_and_reconstructed`;
4. set an unresolved row’s outcome to `verified_absent`.

Each test should recompute the evaluation ID—and, for the render case, the receipt—before expecting rejection. That prevents the stale-ID check from masking the actual semantic-contract test. The current result-contract tests cover row/metric deletion, duplicate membership, stale IDs, invalid evidence, and pass rows with findings, but not these evidence-reference or reverse outcome invariants. `tests/test_v1_result_contract.py:23-134`.

---

### P3 — The research pack contradicts itself about external contact

**Confirmed documentation regression; not by itself an O1–O5 technical blocker.**

At `docs/prototype/steward-research-pack.md:3`, the status correctly states that a tailored FX/Devin invitation was sent to the shared steward.

At approximately line 87, the closing sentence still says:

> no invitation, live run, external contact, publication, or broad campaign has occurred

That contradicts both the opening status and the checked-in outreach text.

Smallest fix:

> Other than the linked willingness/access invitation, no live run, collection, publication, or broad campaign has occurred.

The earlier C04 problem itself **is resolved**: C04 is no longer in the required C01+C02 first-gate checklist and now appears in a separate “Later C04 expansion” section.

---

## Verification of the original findings

| Prior finding                                                          | Verification at `c4807214…`                                                                                                                                                                                                                                                                                                                                                           | Status                                                               |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **Primary population could shrink by deleting an assertion**           | `bundle.py:218-237` constructs the primary observation membership and requires each primary observation to be owned by exactly one primary assertion. The previous deleted-`tr-2` counterexample is reproduced in `test_v1_population.py:24-31` and is expected to fail validation.                                                                                                   | **Resolved**                                                         |
| **Extra known native events or sessions could disappear from metrics** | `evaluate.py:149-174` compares decoded known events against primary plus explicitly unscored observer populations. Unmatched events create failed `extended` rows; new sessions additionally receive `native_session_population_mismatch`. Tests cover both a copied tool result with a fresh ID and a third session.                                                                 | **Resolved**                                                         |
| **Conflicting observations could be cherry-picked**                    | `bundle.py:238-268` rejects conflicts among primary observations and among all referenced candidates for each scored field. The test includes both a direct status conflict and the prior cross-field “Frankenstein” combination.                                                                                                                                                     | **Resolved**                                                         |
| **Persisted inspection locators were trusted without resolution**      | `locators.py:90-176` verifies artifact hash, JSONL coordinates or SQLite row/companions, record hash, strict event shape, event/session identity, and every scored field. Tests cover a missing record digest, wrong record, correct identity with wrong field, and a genuine injected decoder omission.                                                                              | **Resolved**                                                         |
| **Result rows and metrics could be removed under an old ID**           | `result_contract.py:22-34` recomputes the evaluation ID; approximately lines 105-173 require one metric per scenario and an exact once-only partition of every result row. Render invokes this validation and checks an adjacent semantic receipt when present. Focused tests exercise omitted rows, omitted metrics, omitted failed membership, duplicate membership, and stale IDs. | **Core attack resolved; row-evidence semantics still blocked by P1** |
| **Relationship uncertainty erased a confirmed failure**                | Join and branch uncertainty now preserve an existing failure, and scenario aggregation uses `fail > unresolved > pass`. Exact-state tests cover missing tool joins, dangling branches, and cycles. `evaluate.py:121-140, 167-174`; `test_v1_population.py:70-84`.                                                                                                                     | **Resolved**                                                         |
| **Constructed fixtures could be relabeled as measured vendors**        | `bundle.py:107-123` permits only the exact two constructed subject profiles, `constructed_only` registry status, and null inspection/live dates. Bundle validation also binds the subject’s artifact family to the decoder format. Tests cover both vendor/family relabeling and a fabricated measured date.                                                                          | **Resolved**                                                         |
| **C04 appeared as an initial C01+C02 requirement**                     | C04 is now explicitly separated as a later expansion.                                                                                                                                                                                                                                                                                                                                 | **Resolved**, apart from the unrelated contact-status sentence       |

---

## Attachment correction verification

The attachment correction is present and correctly closes the original gap.

At `session_bench/evaluate.py:124-132`, an attachment event resolves its declared path to exactly one inventoried native artifact and compares both referenced `sha256` and `size_bytes` against the delivered artifact metadata. Bundle validation has already checked that those metadata match the actual delivered bytes. A mismatch converts the row to `fail` with `attachment_payload_mismatch`.

The focused test covers both constructed formats and two replacements:

* a different-size payload;
* `b"value = 9\n"`, which is the **same 10-byte length** as the original `b"value = 2\n"`.

The same-size case therefore proves that digest comparison—not merely size comparison—is active. `tests/test_v1_evaluation.py:132-157`.

The new copied-process receipt contains ten cases, including attachment-payload negative controls for both JSONL and SQLite. Both produce one failed assertion, retain identical semantic output across the copied run, and record network/original-source/original-bundle denial. The receipt and acceptance metadata both name implementation digest:

`5fb20aaa3c2d44aed0ada28fac2c8907d3b61587c7645091c9c32e0eb5556a52`

### Optional evidence improvement

The reusable `attachment_payload` fixture mutation used in the ten-case copied reproduction replaces the attachment with a larger payload. The digest-only same-size case exists in the focused unit test, not as a separate copied reproduction case. This is sufficient for O4 code correctness, but the O5 receipt would be stronger if the fixture mutation itself used a same-size replacement—or if the copied suite added an explicit same-size case. `session_bench/fixtures.py:276-279`; `tests/test_v1_evaluation.py:132-157`.

This is not an additional acceptance blocker.

---

## Evidence and verification limits

The repository reports a local macOS Python 3.14.7 run of **132 tests**, including 24 historical tests, and ten copied reproduction cases. I did not independently execute that local suite or the macOS `sandbox-exec` reproduction.

GitHub CI for the exact reviewed commit has now succeeded. It ran on Ubuntu, performed the byte-identical v0.4 generation check, and ran the test suite. The OS-isolation tests explicitly skip outside Darwin, so that CI result does not independently establish the macOS sandbox claim.

I verified that the hardening acceptance metadata and reproduction receipt agree on the implementation digest, but I did not independently recompute that digest from all module and schema bytes.

The connected GitHub view did not provide a convenient queryable form of the binary SQLite fixture. I checked its generator, manifests, decoder/locator paths, WAL tests, and the reported SQLite copied cases rather than independently querying the committed binary database.

No repository changes, comments, new external contact, vendor access, or collection were performed.

## Required next step

Add the two narrow result-contract invariants—evidence references for pass rows and bidirectional state/outcome consistency—plus focused tests that recompute the ID and receipt before asserting rejection. Correct the stale final sentence in the research pack, rerun the bounded local and copied suites, and then perform another source-only verification. This work remains entirely inside O1–O5 and implies no L0/F0 or live authorization.
