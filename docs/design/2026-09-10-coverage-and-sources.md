# Coverage, related work, and adoption

Status: O1-O5 approved for bounded local synthetic prototype and v0.4 bookkeeping; L0/F0 live access remains separately gated. Public sources inspected 2026-09-10. A source inspection date is separate from a live test date: no row below is a measured result unless it names a completed run and evaluation. No installed product versions, local stores, account access, or live persistence behavior were inspected in preparing this document. Product names and public documentation establish candidate capabilities only. The v0.4 measurements remain the current historical edition and are not current-build validation.

## Product framing and result identity

The public report card is the primary product. It should answer practical developer questions: which tested configuration leaves a useful independently readable record, what survives restart or interruption, what can be reconstructed offline, what is missing or ambiguous, and what evidence supports each answer. The atlas, native artifacts, fixtures, and executable evaluator make that comparison trustworthy; they are not a substitute for it.

The technical question remains: how much of the observed work can an independent tool faithfully reconstruct from the record a tested surface leaves behind? A result card must keep retention and reader capability separate. For each assertion, use these public states:

| Result-card state | Meaning |
|---|---|
| `retained + reconstructed` | Independent inspection establishes the fact in the declared native bundle and the tested decoder reconstructs it correctly. |
| `retained + decoder incomplete` | Independent inspection establishes the fact in the native bundle, but the tested decoder does not yet reconstruct it. |
| `verified absent` | The event was independently observed, the declared bundle was checked coherently, and the fact is established as absent. |
| `unresolved` | Available evidence cannot distinguish writer omission from acquisition or interpretation limits. |

The live run, captured bundle, and evaluation have separate IDs. A decoder update evaluates the same capture under a new evaluation ID; it does not imply a new live run or vendor behavior change. Cards show configuration, source inspection date, live test date when present, observer/comparator/fixture versions, capture hashes and multiple scoped attestations. Candidate rows are visibly different. Incorrect retained values receive an explicit field-level contradiction finding; an ID match with a wrong exit status is not recovery. Verified absence is scoped to the established capture and observation boundary, not a universal product absence claim.

The public configuration baseline is the ordinary, documented default configuration for the tested surface and launch mode. Enhanced retention, verbose logging, debug, or diagnostic settings are separate diagnostic rows and never silently replace the default baseline. Observation instrumentation that could change product behavior is disclosed. Interactive CLI, one-shot/headless CLI, and desktop execution remain separate configurations.

## First feasibility and first evaluated edition

After the offline prototype, require a one-CLI C01+C02 feasibility gate before three-configuration calibration. The proposed target is **Codex interactive CLI, local**, as an existing historical baseline family; current feasibility/access is unverified. Obtain one valid C01 and one valid C02 session, copy their native bundles, decode offline without original paths, and evaluate a deliberately damaged derived fixture. Success means correctly distinguishing preservation from loss, not a passing product. Positive/negative measurement controls cannot be filled from observer answers. This narrow gate needs its own bounded live envelope, not the full expansion plan; local prototype authorization does not require broader account access.

The first evaluated edition uses three contrasting configurations:

| Configuration | Why it is included | Default launch mode | Status before collection |
|---|---|---|---|
| Codex interactive CLI, local | Recommended first CLI feasibility baseline and a documented CLI-to-desktop continuation family. [Commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands) | Interactive CLI | Candidate; no access or result implied |
| Claude Desktop, Code tab, Local environment | Genuine desktop observation path with a distinct local Code surface. [Desktop reference](https://code.claude.com/docs/en/desktop) | Desktop Code / Local | Candidate; no access or result implied |
| Goose interactive CLI, local | Contrasting CLI persistence/database family; public docs describe SQLite sessions and JSONL migration. [Session management](https://goose-docs.ai/docs/guides/sessions/session-management/) | Interactive CLI | Candidate; no access or result implied |

These are the recommended named defaults for the first plan. Substituting Codex Desktop local, Claude Code interactive CLI, Cursor CLI or desktop/IDE local agent, Gemini CLI, another Goose surface, or another launch mode requires a concrete revised plan and a change to the approved scope before collection. A product name alone does not define a tested configuration.

The first edition evaluates C01-C04 at three repetitions per configuration: 4 × 3 × 3 = **36 scheduled scenario runs**. Each C04 scenario run creates two native sessions. Recovery adds C05/C06's 36 scheduled scenario runs, for **72 evaluated-stage scenario runs**. Excluded are the 2-run feasibility gate, all 7 minimum calibration runs (six C01/C02 baseline runs plus one advanced-profile attempt), and retries/extra profiles; the minimum full staged path is therefore 81 scheduled scenario runs before extra attempts. Invalid attempts consume the budget and remain recorded; they are not free replacements. C06 retains three conditions with three repetitions each per configuration. Native-session totals for calibration and recovery are recorded only after actual captures.

At least one advanced profile must be exercised during the three-configuration calibration, after the earlier one-CLI gate and before the first evaluated edition. Branches, subagents, compaction and attachments are alternative candidates. Name the selected trigger, independent observations and separate time/usage allowance before execution; one unexercised attempt does not satisfy calibration. Budget extra attempts explicitly or stop the gate incomplete. Calibration never silently counts as first-edition evidence.

The original eight-surface pilot is conditional expansion backlog, not a committed first release. It may proceed only after the three-configuration machinery has produced reproducible offline results and an approved expansion plan names every surface, launch mode, exact build, isolation method, access path, spend/quota ceiling, operator cap, and retry policy. No access, availability, or authorization claim is made now. Qwen, Droid, Grok Build, IDE/extension surfaces, Cursor, Gemini, Codex Desktop, Claude Code, and other candidates remain candidates or atlas entries; none is implied unavailable, approved, or measured.

## Coverage matrix

| Tier | Surface/configuration | Public basis and intended use | Measurement status and required probe |
|---|---|---|---|
| First-edition default | Codex interactive CLI, local | Recommended CLI feasibility baseline. [Commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands) | Candidate until an exact build, launch evidence, observer stream, artifact family, and isolated capture are recorded. |
| First-edition default | Claude Desktop, Code tab, Local | Desktop Code/local candidate. [Desktop reference](https://code.claude.com/docs/en/desktop) | Candidate until app identity/build, UI evidence, local roots, retention, and side-thread behavior are measured. |
| First-edition default | Goose interactive CLI, local | SQLite/database migration contrast. [Session management](https://goose-docs.ai/docs/guides/sessions/session-management/) | Candidate until exact database/companions, model configuration, coherent capture, and native event coverage are measured. |
| Conditional expansion | Codex Desktop, local project execution | Pairing candidate with CLI continuation. [Commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands) | Requires revised approved plan, exact app/build, execution boundary, and surface markers. |
| Conditional expansion | Claude Code CLI, interactive local | CLI candidate. [CLI reference](https://code.claude.com/docs/en/cli-reference) | Requires revised approved plan, exact artifacts/configuration, observer behavior, and access. |
| Conditional expansion | Cursor CLI or Cursor Desktop/IDE local agent | CLI and IDE candidates; the IDE is a distinct surface. [Local backlog](../../BACKLOG.md) | Requires separate rows, current build identity, native store/companions, and access. |
| Conditional expansion | Gemini CLI, interactive local | Automatic-history and project-storage candidate. [Session management](https://geminicli.com/docs/cli/session-management/) | Requires current schema, retention, and observer completeness probe. |
| Conditional expansion | Qwen Code CLI | Session configuration candidate. [Settings](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md) | Candidate only; access, current schema, authorization, and live availability require confirmation. |
| Conditional expansion | Droid CLI | Resume/headless candidate. [CLI reference](https://docs.factory.ai/droid-cli/cli-reference) | Requires local-materialization, cloud boundary, lineage, and access probe. |
| Conditional expansion | Grok Build CLI, official `grok` distribution | Interactive/headless/ACP candidate. [Overview](https://docs.x.ai/build/overview), [CLI reference](https://docs.x.ai/build/cli/reference) | Requires exact vendor distribution/build identity; community projects and old corpus labels do not transfer. |
| Conditional expansion | GitHub Copilot in VS Code; Cline in VS Code | Extension/IDE candidates with distinct stores and export paths. [VS Code sessions](https://code.visualstudio.com/docs/agents/run/sessions/manage-sessions), [Cline tasks](https://docs.cline.bot/core-workflows/task-management) | Requires extension/build identity, local/remote provider boundary, native records, and approved access. |
| Atlas or later profile | Claude Cowork, Goose Desktop, other official IDE surfaces | Distinct modes and paired storage claims remain useful atlas entries. | Documentation claims are not measured equivalence; each later row requires its own adapter and approved plan. |

Historical CLI families such as Pi, OpenClaw, Kimi Code, OpenCode, Hermes, Copilot CLI, and Antigravity remain atlas entries and v0.4 results while new-scenario adapters are considered. No existing family is silently dropped or awarded first-edition status. Windows and Linux require new OS-specific results rather than inferred platform equivalence.

## Scenario coverage and practical comparison

The first edition uses C01-C04 to establish ordinary work-record usefulness before recovery is added. C02 inspects a small deterministic project's known defect, runs a failing regression test, edits the file, then corrects/retries and reruns the test. The observer establishes actual invocations, file changes and test outcomes. Skipped steps remain unexercised, and a final failed test may be faithfully recorded. This measures action-trail preservation, not coding quality or repository-backup completeness.

C03-A archive preservation and C03-B native continuation are independent assertion groups. A complete archive without resume differs from working resume with missing tool outcomes. C04 is the copied native-bundle demonstration without original application/backend/paths. Report fixed Readable archive, Portable bundle, Session continuation, Lifecycle recovery and named Extended history subprofiles. No product-specific C06 exemption exists: isolation difficulty is a measurement limitation, and every designation requiring C06 remains incomplete until its evidence exists.

The required run contracts remain in the v1 proposal: independent live observation; decoder receives only the native bundle and declared metadata; evaluator compares decoded records with frozen independent observations; helper action boundaries are not confused with native tool-call envelopes; coherent ordinary and crash captures retain required WAL/journal companions; and captured product failures remain valid outcomes when acquisition was sound. A result is invalid only when collection failed to establish what was collected.

## Related work and the proposed distinction

| Work | Supported scope | Relationship to Session-Bench |
|---|---|---|
| [Agent Trace specification](https://agent-trace.dev/) | Code-contribution attribution; storage mechanism is implementation-defined. | Reuse provenance vocabulary where useful; it does not prove session retention or recovery. |
| [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai) | Interoperable generative-AI telemetry vocabulary. | Optional mapping after core validity; native evidence and source locators remain primary. |
| [TraceLab paper](https://arxiv.org/abs/2606.30560) | Coding-agent workload characterization for serving research. | Dataset precedent with a different objective from native-session reconstruction and lifecycle conformance. |
| [ACM artifact guidance](https://www.acm.org/publications/policies/artifact-review-and-badging) | Artifact availability, evaluation, and reproducibility guidance. | Internal reproducibility criteria only; no ACM endorsement or badge claim. |
| [Zenodo software metadata](https://help.zenodo.org/docs/github/describe-software/) and [DOI versioning](https://zenodo.org/help/versioning) | Release description and version/project identifiers. | Archive exact protocol/evaluator/dataset releases and cite version-specific results. |
| [ACP v1 session setup](https://agentclientprotocol.com/protocol/v1/session-setup) | Protocol-level session load/replay and resume capabilities; official page inspected during this revision. | These protocol operations do not themselves establish independently readable copied native artifacts. Pin the specific contract revision if later used in an adapter. |

The proposed contribution is a practical report card backed by a public native-session corpus, independent observations, reproducible decoders, and controlled persistence experiments. This research pass does not establish that no comparable project exists. Use a bounded related-work statement, not “first,” “only,” or “the standard.”

## Atlas publication and governance

The atlas may publish independently of conformance results or completed decoder implementation. It can record documented candidate configurations, artifact families, sources, inspection dates, open questions and measured rows when present. Proposed accountable owner is Alexander Malakhov, the author named in [CITATION.cff](../../CITATION.cff), or an explicitly named delegate per entry. Record actual ownership before publication; this proposal does not assign work externally. Its release gate requires source/content review, local links, correct candidate/measurement labels and date/owner fields, not conformance passes. Publishing review documents is separate from authorization to ship the atlas or a result edition.

Each entry carries `maintenance_owner`, `source_inspected_at`, `live_tested_at` (null when untested), and `next_inspection_due`. Proposed source review cadence is monthly; overdue entries are visibly stale. Tests run only under their authorized budgets; a planned next test is never labeled an actual test date. Public openness fields include documentation, decoder license/version/dependencies and any application/auth/backend requirement.

Measured rows require exact configuration/test date, independent observations, bundle hashes, decoder/evaluation IDs, field outcomes and visible attempts. Candidate rows cannot inherit a qualification. The result-level dispute process uses repository issues referencing evaluation/assertion IDs and contradictory evidence; the maintenance owner triages capture, writer, decoder, comparator or documentation responsibility, records reviewer and disposition, and links any corrected evaluation to the original. Disputes can be substantiated, rejected with evidence, or unresolved; disputed status stays visible while open. A decoder correction keeps the capture identity. Historical snapshots remain retrievable and vendor right of reply does not veto evidence.

The v0.4 publication remains the current historical rubric and edition. This proposal does not supersede or rescore it. Add a transition notice only when a v1 release is actually public, with the exact protocol, evaluator, dataset, and result identifiers.

## Adoption and exact revised path

The revised path is:

1. Qualify the offline measurement system with constructed positive, mutated, missing-event, duplicate, pairing, branch, and path-safety fixtures; prove that decoder/evaluator changes produce the intended states.
2. Pass the earlier one-CLI C01+C02 feasibility gate, proposed Codex interactive CLI, including copied-bundle offline decoding and deliberate loss/damage detection. Measurement correctness, not product success, is the gate.
3. Calibrate the three named defaults: Codex interactive CLI, Claude Desktop Code/Local, and Goose interactive CLI. Calibrate at least one branch/subagent, compaction, or attachment profile early and record its separate scope.
4. Freeze C01-C04 and run the first edition’s 36 scheduled runs. Publish candidate versus measured rows and per-assertion report cards only after offline reproduction succeeds.
5. Add the recovery qualification’s additional 36 runs, preserving quiet-prefix, helper-start, and helper-result conditions and all invalid attempts.
6. Consider the original eight-surface expansion only through a concrete revised surface/launch plan with confirmed access, explicit spend/quota and operator caps, and an approved change in scope.
7. Maintain the atlas independently, then publish a v1 result edition only after its own release authorization and evidence criteria are met.

Expansion is a backlog decision, not a present access claim. The practical success test is that a developer can compare tested configurations and inspect the evidence packet behind a missing, reconstructed, or unresolved fact.

## Research and review ledger

The attached review was incorporated as a scope and product-framing review. It covered the pasted proposal and baseline at `49b7c0a`, not unseen companion documents, and did not execute tests or live collection. It is not current product/version verification. The primary agent integrated it and the addendum with three Luna subagents used for bounded document revision, measurement-contract advice and consistency review. Final review found no remaining design-approval blockers; local links, whitespace, dependencies and run arithmetic were checked. At that design-review stage only documents changed. Subsequently, O1-O5 implementation was explicitly approved and the bounded offline prototype was accepted; see [current acceptance evidence](../prototype/README.md). Live collection and a benchmark release remain separately gated.
