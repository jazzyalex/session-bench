# Offline acceptance hardening after Sol Pro review

The [review](sol-pro-review.md) inspected commit `98ab5ab213398e7efcee98fd459a5d818ae4e3ac` through connected GitHub and returned **NOT READY** for O1–O5. It did not execute tests. Its findings concern the offline measurement contract; they do not require live collection. This response records the changes submitted for a focused Sol Extra High verification.

| Finding | Change | Regression evidence |
|---|---|---|
| Scored population could omit observations or ignore extra native events | Explicit primary/supporting/unscored observer roles, primary/relationship assertions, exactly one primary assertion per primary event/boundary; unexpected known events create failed population checks | `tests/test_v1_population.py` deleted-loss-assertion, extra-event and extra-session cases |
| Conflicting observations could supply whichever field matched | Reject conflicts among primary observations and among referenced values for each scored field | `tests/test_v1_population.py` conflicting and cross-field observation cases |
| Present-inspection locators were not verified | Shared native resolver verifies bytes, digests, coordinates, identity and scored fields for persisted inspection claims; mismatch results use verified inspection locators | `tests/test_v1_inspections.py` malformed locator, wrong-event, wrong-field and actual decoder omission cases |
| Results could retain an old ID after editing rows/totals | Stable identity recomputation, exact scenario metric partition, semantic row checks; standalone render validates adjacent receipt and labels receiptless results | `tests/test_v1_result_contract.py` |
| Relationship uncertainty could erase a confirmed field failure | Fail takes precedence over unresolved in rows and metrics; uncertainty remains in findings and causal outcome | `tests/test_v1_population.py` exact fail assertions for missing join, dangling branch and cycle |
| Constructed subjects could be relabelled as measured vendors | Only the two exact constructed subject profiles are accepted; registry cannot claim measured/live status or dates | `tests/test_v1_population.py` vendor/family/date relabelling controls |
| C04 appeared in first-gate research checklist | C01+C02 first gate is separate from later C04 expansion | `docs/prototype/steward-research-pack.md` |

The earlier attachment correction at `b2803f40ce5e22a97c3a91cb1cda0f2054c0aa8b` also needs verification: delivered companion bytes must match the native reference digest and size, including same-size substitutions. Both constructed formats have a negative reproduction case.

The checked-in synthetic packs are regenerated with explicit population roles. Prior commits and reproduction receipt directories remain historical evidence tied to their own implementation digests. New copied-bundle evidence must bind the final hardening implementation digest. v0.4 source, data, historical artifacts and historical tests remain unchanged.

Limits remain explicit: unscored populations are declared, not inferred; hashes establish identity rather than honest acquisition; copied reproduction uses the same local host/interpreter, not an independent person. No vendor adapter, private-store access, live run, purchase, or benchmark publication is part of this patch. O1–O5 acceptance remains pending verification of this revision, and L0/F0 remains separately gated.
