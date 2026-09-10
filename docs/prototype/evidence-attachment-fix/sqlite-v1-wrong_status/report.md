# Constructed measurement-system report

**No vendor result or qualification.**

Evaluation: `eval-a6287642512f281bc20c21e8fd0f2c3081b48c6da42854430ae89d9428842bb6`

Capture: `constructed-sqlite-v1-wrong_status-capture`; evidence: **valid**; origin: **derived_mutation**.

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
| event.tr-2 | fail | unresolved | contradicted |
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
