# READY — bounded offline O1–O5

**Source access confirmed.** I read the repository through connected GitHub and reviewed exact head commit **`20919793425ededb408969d4c709443855ebc20d`** (`fix: require evidence references and consistent result outcomes`) against **`c4807214ffeedffb404cd84ff73ceab82c615322`**. GitHub reports the range as two commits because `f99ae687…` sits between the requested base and head; I reviewed the complete `c480721… → 209197…` range and the relevant surrounding files at the pinned head.

This session is **GPT-5.6 Sol, not GPT-5.6 Pro**. I do not get a separate runtime telemetry field exposing the client-side effort label, so I cannot independently attest that the UI flag says “Extra High.”

## Actionable findings

**None.** I found no P0–P3 acceptance defect in this patch for the bounded constructed/offline O1–O5 milestone.

### Prior P1 — resolved

The original counterexample no longer works.

At `session_bench/result_contract.py:53-69`, result locators are now structurally checked rather than accepted merely because the locator array is nonempty. At `result_contract.py:97-120`, the contract now enforces:

* a passing row has at least one structurally valid native locator;
* a passing row has an observation reference;
* `retained_and_reconstructed` implies `state == pass`;
* `state == unresolved` implies `outcome == unresolved`;
* `retained_decoder_incomplete` / `verified_absent` imply `state == fail`;
* those causal failure outcomes require observation and inspection-evidence references;
* `retained_decoder_incomplete` additionally requires a native locator.

So the concrete prior attack—delete `locators` or `observation_ids`, recompute `evaluation_id`, recompute the semantic receipt, then render—is rejected semantically before rendering. The new regression explicitly performs that recomputation before expecting rejection at `tests/test_v1_result_contract.py:121-130`. Reverse state/outcome attacks are similarly exercised at `:133-156`, and `{}` / artifact-ID-only fake locators are rejected at `:177-184`.

The division between evaluation-time and standalone-render verification is also sound for the approved scope. `evaluate_bundle` validates the bundle, decodes native-only input, calls `validate_sources` against the actual native bytes, validates the bundle again for mutation, and only then constructs the result.  `validate_sources` resolves the native locator and requires the referenced record to equal the decoded event.  Meanwhile bundle validation binds assertion observation IDs to the observer ledger and requires inspection evidence to point to provenance artifacts.

Accordingly, I **do not** consider it a defect that standalone `render()` cannot independently reopen a source bundle it was not given. Its job is result-schema/semantic consistency plus adjacent semantic-receipt verification, and it calls `validate_result` before emitting anything. `session_bench/__main__.py:26-32`.

### Prior P3 — resolved

The stale “no external contact” wording is gone. `docs/prototype/steward-research-pack.md:87` now explicitly carves out the already-linked willingness/access invitation while continuing to say that no live run, collection, publication, or broad campaign has occurred. That is internally consistent with the beginning of the same document.

### Same-size attachment hardening — confirmed

This is now a useful digest-specific control rather than a size-plus-digest control. The baseline writes:

`value = 2\n`

and the `attachment_payload` mutation writes:

`value = 9\n`

Both are 10 bytes. The native event retains the original digest while the copied attachment changes bytes, so detection cannot rely on length. `session_bench/fixtures.py:237-239, 277-279`.

## Verification status

The repository records implementation SHA256 **`76dc38d7d65f37ecb881665b389bca46517b28825ec1cf1b223c798c89f8c72f`**, 138 local macOS passes, 24 historical tests, 10 copied cases, zero live runs, and explicitly says the copied recomputation is not an independent-person reproduction.  The reproduction receipt records all ten cases as semantically identical under copied-process recomputation while denying network, original bundle, and original code access.

There is also now **exact-head CI** that was not yet reflected in the committed `acceptance.json`: GitHub Actions run `34544053358` checked SHA `20919793425ededb408969d4c709443855ebc20d` and completed successfully.  Its log reports **136 passed, 2 skipped** on Ubuntu 24.04 / Python 3.12.14. The two-skip difference is consistent with the repository's Darwin-only isolation tests; it does not contradict the reported 138 local macOS passes. The workflow also successfully regenerated the historical leaderboard and required byte identity.

I **did not personally execute** pytest or the copied macOS reproduction in this review. My execution evidence is therefore the exact-head GitHub Actions run plus inspection of the committed receipts/code, not an additional independent run. Likewise, I am not treating hashes as proof of honest acquisition, and the same-host copied-process run is not an independent person's attestation.

**Acceptance conclusion: READY for the specifically approved bounded offline O1–O5 constructed prototype.** The final P1 and P3 from the `c480721…` review are resolved, the added hardening is internally consistent with the evaluator/bundle path, and I found no patch-introduced acceptance blocker.

**This READY verdict does not authorize L0/F0 live collection, vendor collection, access to private sessions, purchases, paid runs, release authorization, or any other live-evidence activity.**
