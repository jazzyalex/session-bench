# Constructed measurement-system report

**No vendor result or qualification.**

Evaluation: `eval-346c3c841dfe2dd2cdad8a2a31a72f9a652607a394fb0f28354e75a5006fe3ff`

Capture: `constructed-sqlite-v1-remove_fact-capture`; evidence: **valid**; origin: **derived_mutation**.

| Scenario | Reconstructed assertions / declared assertions | State |
|---|---:|---|
| C01 | 5 / 5 | pass |
| C02 | 8 / 9 | fail |
| C04 | 5 / 5 | pass |

| Assertion | Result | Outcome | Findings |
|---|---|---|---|
| event.session-1 | pass | retained_and_reconstructed |  |
| event.session-2 | pass | retained_and_reconstructed |  |
| event.branch-main | pass | retained_and_reconstructed |  |
| event.branch-fork | pass | retained_and_reconstructed |  |
| event.m-1 | pass | retained_and_reconstructed |  |
| event.m-2 | pass | retained_and_reconstructed |  |
| event.tc-1 | pass | retained_and_reconstructed |  |
| event.tr-1 | pass | retained_and_reconstructed |  |
| event.tc-2 | pass | retained_and_reconstructed |  |
| event.tr-2 | fail | verified_absent | loss |
| event.tc-3 | pass | retained_and_reconstructed |  |
| event.tr-3 | pass | retained_and_reconstructed |  |
| event.tc-4 | pass | retained_and_reconstructed |  |
| event.tr-4 | pass | retained_and_reconstructed |  |
| event.m-correction | pass | retained_and_reconstructed |  |
| event.m-3 | pass | retained_and_reconstructed |  |
| event.attachment-1 | pass | retained_and_reconstructed |  |
| event.m-4 | pass | retained_and_reconstructed |  |
| relationship.tool-envelope-helper | pass | retained_and_reconstructed |  |

Unknown records: 0. Decoder diagnostics: 0.

Field values, observation references, source locators, and integrity digests are in results.json.
Native continuation, cancellation, crash recovery, and vendor compatibility have not been tested.
