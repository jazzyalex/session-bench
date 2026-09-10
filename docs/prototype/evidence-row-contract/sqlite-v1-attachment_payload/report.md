# Constructed measurement-system report

**No vendor result or qualification.**

Evaluation: `eval-0d5ba52caeb0dcdf0760c438e669526567b0c1a1ad58bea2f29b68ac4bf3fbf9`

Receipt: **receiptless; semantic result unverified.**

Capture: `constructed-sqlite-v1-attachment_payload-capture`; evidence: **valid**; origin: **derived_mutation**.

| Scenario | Reconstructed assertions / declared assertions | State |
|---|---:|---|
| C01 | 5 / 5 | pass |
| C02 | 9 / 9 | pass |
| C04 | 4 / 5 | fail |

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
| event.tr-2 | pass | retained_and_reconstructed |  |
| event.tc-3 | pass | retained_and_reconstructed |  |
| event.tr-3 | pass | retained_and_reconstructed |  |
| event.tc-4 | pass | retained_and_reconstructed |  |
| event.tr-4 | pass | retained_and_reconstructed |  |
| event.m-correction | pass | retained_and_reconstructed |  |
| event.m-3 | pass | retained_and_reconstructed |  |
| event.attachment-1 | fail | unresolved | attachment_payload_mismatch |
| event.m-4 | pass | retained_and_reconstructed |  |
| relationship.tool-envelope-helper | pass | retained_and_reconstructed |  |

Unknown records: 0. Decoder diagnostics: 0.

Field values, observation references, source locators, and integrity digests are in results.json.
Native continuation, cancellation, crash recovery, and vendor compatibility have not been tested.
