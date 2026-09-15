# The Patch Passed. A Week Later, Nobody Could Explain It.

## Vibe coding needs a work trail, not just working code

**Dek:** Session-Bench v1 asks whether another tool can reconstruct an agent’s work after the chat window is gone. Its first five-configuration study is small, but the failures it exposes are already useful.

The feature shipped on Friday. On Wednesday, a customer finds the edge case.

You open the project and see a plausible patch: a price calculation changed, a delivery rule moved, and three tests pass. What you cannot see is why the agent made those choices. Did it inspect the failing output? Did it misunderstand the first instruction and then correct course? Which command produced the result it relied on? Was the final edit based on the latest test run or an earlier one?

The code is still there. The reasoning needed to work safely with that code is scattered across a chat transcript, tool results, local databases, sidecar files, or nowhere at all.

This is a common weak point in vibe coding. Generating a patch is fast. Understanding it later can be slow, especially when the person returning to the code did not write it line by line. A useful agent record has to recover the sequence from request to diagnosis to action to result to correction. It should survive a move to another machine, identify the model and configuration, and be honest when usage information cannot be reconciled.

That makes the agent’s work trail part of the source record. You need it for the same reasons you need a readable diff, a test log, or a useful commit message: to debug, resume, review, and hand off the work without inventing a story after the fact.

Session-Bench v1 measures that trail.

## A reconstruction test, not another model contest

The benchmark asks one practical question:

> **How much of an agent’s work can another tool faithfully reconstruct from the record it leaves behind?**

It does not score whether the generated code is good, whether the model is smart, or whether one vendor is faster or cheaper. Session-Bench looks at what remains after the work: user turns, agent responses, actions, results, edits, causal links, usage attribution, timestamps, identities, and the files needed to decode the session elsewhere.

The current v1 study covers five configurations:

- Codex CLI
- Codex Desktop
- Claude Code CLI
- Claude Desktop Code (Local)
- OpenCode CLI

Each configuration ran the same controlled two-turn correction task three times. The task required an agent to inspect a small project, diagnose a failure, receive a correction, edit the implementation, and run the checks again. Each run was measured across 31 cells. The evaluator then worked from copied artifacts and checked whether a selected loss was detected.

The five headline categories add to 100 points: **Record fidelity (30), Causality & context (20), Usage & attribution (15), Portability & openness (20), and Durability & signal (15).** They make a dense evidence matrix readable without hiding where the points came from.

Here is the unpublished local review candidate:

| Review-candidate order | Configuration | Score | Fidelity / Causality / Usage / Portability / Durability |
|---|---|---:|---|
| 1 (tie) | Codex CLI | **87.0** | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| 1 (tie) | Codex Desktop | **87.0** | 21.0 / 20.0 / 12.0 / 20.0 / 14.0 |
| 3 | OpenCode CLI | **83.3** | 20.3 / 20.0 / 12.0 / 18.0 / 13.0 |
| 4 | Claude Desktop | **82.8** | 25.0 / 14.8 / 12.0 / 18.0 / 13.0 |
| 5 | Claude CLI | **81.0** | 30.0 / 20.0 / 0.0 / 18.0 / 13.0 |

Ranks and ties use the displayed one-decimal scores. Codex CLI and Codex Desktop therefore share first place at 87.0 even though their underlying unrounded means differ slightly.

These numbers are **locally reproduced and unpublished**. Full independent native reproduction has not been completed: `independent_native_reproduction=false`. This is a review candidate, not a public leaderboard.

## The totals are close. The failures are not.

All five scores fall within six points, which might suggest that the products preserve roughly the same thing. The category profiles show why that reading is wrong.

Codex CLI and Codex Desktop reach 20/20 for Causality & context and 20/20 for Portability & openness. In the tested setup, the relationships in their records could be followed and the required record could be copied and decoded. Their 21/30 Fidelity scores show that some observed actions, results, or changed-file facts were not preserved in the required form. Their 12/15 Usage scores leave part of the attribution picture incomplete.

OpenCode CLI also reaches 20/20 for Causality & context. Its 20.3/30 Fidelity and 18/20 Portability scores expose a different tradeoff: strong action-to-result structure, with gaps around changed-file and final-after-correction evidence. For a session browser, that perfect causality bar may matter more than the four-point gap from the top.

Claude Desktop lands at 82.8 with 25/30 Fidelity, 14.8/20 Causality, 12/15 Usage, 18/20 Portability, and 13/15 Durability. Its record preserved more of the tested event population than Codex, but batching of some actions and results made parts of the causal chain less precise.

Claude CLI produces the most instructive result. It scores a perfect 30/30 for Fidelity and 20/20 for Causality, yet finishes fifth because Usage & attribution is 0/15. The native record preserved the tested work sequence extremely well, but its usage population and model or configuration attribution did not reconcile with the independent observation. The evaluator records that mismatch as a contradiction instead of guessing.

This is why the report’s `audit_ready` recommendation has a narrow meaning: **best preserved work trail under this task**. The rule considers Fidelity and Causality. Usage is outside it, so Claude CLI can win that recommendation while scoring 0/15 for Usage. It is a recommendation for reconstructing what happened, not an endorsement of its token accounting.

A single total cannot express these differences. Losing a usage join is unlike losing an edit. A transcript that reads well can still depend on a database stored elsewhere. A session with detailed events can still make it hard to tell which result belongs to which action.

## What a vibe coder can use now

The immediate value is a checklist for whether a coding setup will remain understandable after today.

If you expect to revisit generated code, ask:

- Can I recover every submitted turn and visible response?
- Can I connect an action to its result and the file it changed?
- Can I distinguish the original attempt from the corrected one?
- Does the saved record identify the model and explain its usage fields?
- Can I copy the complete session and decode it without the original path, app, account, or network?

The current results give those questions concrete examples. Codex CLI and Desktop are the strongest portable-archive choices in this candidate. Claude CLI preserves the fullest work trail, with a serious usage-attribution caveat. OpenCode’s structured causal record is valuable for external viewers even though it ranks third. Claude Desktop shows that sharing a vendor name with a CLI does not make the two surfaces equivalent.

That last point matters now that coding agents increasingly live in desktop applications. A desktop surface is not a cosmetic wrapper in a benchmark. It may write to different roots, depend on companion databases, batch events differently, or expose different identity and usage signals. Codex happened to show CLI/Desktop parity on this workload. Claude did not. The configurations still have to be tested separately.

## What v1 adds to the bright simplicity of v0.4

Session-Bench v0.4 already had the right public shape: a clear ranking, five colored `/100` bars, recommendations, vendor cards, and broad observations about session formats. It was easy to scan and easy to quote. Its weakness was depth. Many results described what appeared to be present without fully proving that the record could be copied, decoded in isolation, and checked against what actually happened.

V1 keeps the ranking and the five angles. Underneath them, it measures event populations, causal joins, correction order, changed-file evidence, model and usage attribution, complete roots and companion artifacts, isolated decoding, canonical equality, duplicate safety, and format stability. It distinguishes measured, missing, unresolved, invalid, and contradictory evidence. Authentication failures, quota stops, and incomplete captures do not quietly become low format scores.

The deeper method changes the provisional order among the three CLI products shared with v0.4. V0.4 placed Codex and Claude ahead of OpenCode. This v1 candidate places Codex ahead of OpenCode, then Claude. That does not show that a product improved or declined. The builds, models, workload, observation window, metrics, and weights changed. It shows why a benchmark with stronger evidence can reach a different conclusion from a broad format survey.

## Five configurations are enough to test the benchmark

Five configurations cannot support a market-wide buyer’s guide. They can show whether the benchmark separates failure modes that matter.

Across 15 evaluated runs, the method produced close totals from sharply different records. It found CLI/Desktop parity in one product family and divergence in another. It separated work-trail quality from usage attribution. It tested whether copied artifacts remained decodable. It kept paused or incomplete products outside the ranking instead of manufacturing a score.

That is also why this limited v1 is a useful basis for adding the rest of the products supported by Agent Sessions. The foundation is now explicit: one workload, a 31-cell metric contract, five stable public categories, evidence states, negative controls, copied-root checks, scoring rules, and a report format. Adding another agent should mean mapping its roots and companions, implementing its adapter, collecting qualified runs, and passing the same gates. It should not mean redesigning the benchmark to flatter each new format.

Coverage can therefore grow without losing the simple front page. Products such as Cursor can appear first in the Format Atlas while their records are being mapped, then enter the ranking only when their evidence qualifies. Unsupported, quota-blocked, privacy-unsafe, or incomplete configurations can remain visible as unscored. That makes absence informative without turning operational failure into a claim about session quality.

## The boundary of the result

This study covers one controlled workload, five configurations, three repetitions per configuration, and specific observed builds and models. It does not measure code quality, reasoning quality, latency, price, general reliability, or crash recovery. OpenCode’s public semantic and score packet was recomputed in a fresh sandbox copy, but its raw SQLite and decoder runtime remain withheld; that is not full native replay. The complete candidate is locally reproduced, unpublished, and still lacks independent native reproduction.

Those limits should stay next to the scores. They do not erase the finding that a coding session can preserve the patch while losing parts of the evidence needed to understand it.

For vibe coders, that distinction becomes more important as agents write a larger share of the project. The faster the code appears, the less likely its owner is to remember every rejected idea, correction, command, and result. A recoverable work trail turns that missing memory into an inspectable artifact.

The next useful benchmark question is no longer only, “Did the agent finish?” It is also:

> **When you have to change its code next week, will the record tell you what happened this week?**

Review candidate report: [REPORT_URL]

Evidence index: [EVIDENCE_URL]

Repository and methodology: [REPOSITORY_URL]
