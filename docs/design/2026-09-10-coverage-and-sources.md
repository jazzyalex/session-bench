# Coverage, related work, and adoption

Status: proposed candidate matrix; public research checked 2026-09-10. No installed product versions, local stores, account access, or live persistence behavior were inspected. Documentation establishes candidate capabilities, not conformance. Older v0.4 measurements are historical observations, not current-build validation.

## Prioritized coverage

P0 is the proposed eight-surface live pilot, after offline implementation and collection authorization. All P0 rows require exact builds, launch modes, credentials/access, isolation, and storage contracts to be established at preflight. A product name alone does not define a tested configuration.

| Priority | Surface row | Rationale and public basis | Facts still requiring a probe |
|---|---|---|---|
| P0 | Codex CLI, interactive local | Historical v0.4 CLI family; current official commands include resume and desktop continuation. [Commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands). | Exact writer/configuration, roots, observer stream, artifact family. Headless is a different mode and remains historical until separately run. |
| P0 | Codex Desktop, local project execution | Pair the same task with the actual desktop client; official documentation supports CLI-to-desktop continuation. [Commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands). | Exact app identity/build; local versus remote execution; surface markers and companions. Product naming in docs must not substitute for app identity. |
| P0 | Claude Code CLI, interactive local | Historical CLI baseline and resume-capable workflow. [CLI reference](https://code.claude.com/docs/en/cli-reference). | Current artifacts, configuration and observer behavior. |
| P0 | Claude Desktop, Code tab, Local environment | Official desktop reference distinguishes Chat, Cowork and Code and describes local sessions. [Desktop reference](https://code.claude.com/docs/en/desktop). | CLI/Desktop joins, local roots, retention, and side-thread behavior. Cowork is not part of this row. |
| P0 | Cursor CLI, interactive local | Historical CLI baseline; vendor documents CLI separately. [CLI overview](https://cursor.com/docs/cli/overview). | Persistent artifact set, live output observation, shared-prefix/session identity. |
| P0 | Cursor Desktop/IDE, local agent mode | Existing backlog identifies a paired-storage investigation. [Local backlog](../../BACKLOG.md). | Current SQLite/transcript/binary companions, session identity and retention. Older vendor history URL now redirects to the docs index; do not treat its search snippet as a current storage contract. |
| P0 | Gemini CLI, interactive local | Official docs describe automatic history with tools/usage and project-specific storage. [Session management](https://geminicli.com/docs/cli/session-management/). | Actual current schema, automatic-history versus manual-checkpoint distinction, retention, and observer completeness. |
| P0 | Goose CLI, interactive local | Official docs describe SQLite sessions and migration from JSONL; useful migration and database diversity. [Session management](https://goose-docs.ai/docs/guides/sessions/session-management/). | Exact database/companions, model configuration, coherent capture and native event coverage. |
| P1 | Qwen Code CLI | Disk recording and session configuration merit a new family investigation. [Settings](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md). | Current schema, retention/configuration, authorization and live availability; no private Qwen access assumed. |
| P1 | Droid CLI | Documented session resume and headless execution make it a candidate. [CLI reference](https://docs.factory.ai/droid-cli/cli-reference). | Native local materialization, cloud-sync boundary, lineage, actual file schema. |
| P1 | Grok Build CLI, official `grok` distribution | Official x.ai docs identify interactive/headless/ACP use and session commands. [Overview](https://docs.x.ai/build/overview), [CLI reference](https://docs.x.ai/build/cli/reference). | Match the exact vendor/distribution/build; do not inherit evidence from community grok-cli projects or old 'Grok' corpus labels without identity verification. |
| P1 | GitHub Copilot in VS Code, local agent session | VS Code documents session management and JSON export. [Manage sessions](https://code.visualstudio.com/docs/agents/run/sessions/manage-sessions). | Native store versus export, extension/build identity, local versus remote session provider; CLI result does not transfer. |
| P1 | Cline in VS Code | Official task and IDE workflows provide a candidate for extension task/checkpoint behavior. [Tasks](https://docs.cline.bot/core-workflows/task-management), [IDE](https://docs.cline.bot/usage/ide). | Current extension native records, checkpoints versus conversations, exact persistence/restore contract. |
| P2 | Claude Cowork; Goose Desktop; other official IDE surfaces | Distinct modes warrant later paired evidence. Goose docs explicitly describe Desktop/CLI resume across a shared database; that is a documentation claim to test, not inherited measured equivalence. | Distinct adapters, roots, lifecycle triggers, observation availability, and workload fit. |

The remaining historical CLI families—Pi, OpenClaw, Kimi Code, OpenCode, Hermes, Copilot CLI and Antigravity—remain atlas entries and v0.4 results while their new-scenario adapters are prioritized. Codex, Claude and Cursor are already in P0. No existing family is silently dropped or awarded v1 status. Refresh order should use consumer demand and artifact diversity measured during implementation; current-build validation is not implied.

Gemini and Goose are the two proposed new CLI families in the initial pilot because they provide documented session-management behavior and a database/migration case. Qwen, Droid and Grok Build are the next expansion wave, not rejected candidates. If access prevents a P0 run, show it as not run; any substitution changes the declared campaign plan before execution rather than hiding the unavailable row.

Initial IDE coverage is Cursor as an IDE product. Claims about VS Code extensions require the P1 extension rows; do not market the pilot as broad extension coverage. Windows/Linux expansion uses new OS-specific results, not inferred platform equivalence.

## Related work and the proposed distinction

| Work | Supported scope | Relationship to Session-Bench |
|---|---|---|
| [Agent Trace specification](https://agent-trace.dev/) | Code-contribution attribution; storage mechanism is implementation-defined. | Reuse provenance links where available; do not treat code attribution as proof of full session retention. |
| [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai) | Interoperable generative-AI telemetry vocabulary. | Consider optional mappings of model/tool/usage events, pinned to a specific convention revision. Telemetry cannot silently replace native persistence evidence. |
| [TraceLab paper](https://arxiv.org/abs/2606.30560) | Coding-agent workload characterization for serving research. | Useful dataset precedent and possible later licensed field corpus; its objective differs from paired native-session reconstruction and lifecycle conformance. |
| [ACM artifact guidance](https://www.acm.org/publications/policies/artifact-review-and-badging) | Artifact availability, evaluation, and reproducibility guidance. | Adopt clear inventory and reproduction expectations as internal criteria, without claiming ACM endorsement or badges. Source identified by research subagent; direct primary-agent fetch failed. |
| [Zenodo software metadata](https://help.zenodo.org/docs/github/describe-software/) and [DOI versioning](https://zenodo.org/help/versioning) | Release description and distinction between version-specific and project-wide identifiers. | Archive exact protocol/evaluator/dataset releases; cite version identifiers for numerical results and a project identifier for the evolving benchmark. |

The proposed contribution is a public native-session corpus with independently specified reconstructions and controlled persistence experiments across surfaces. This research pass does not establish that no comparable project exists. Use a bounded related-work statement, not 'first', 'only', or 'the standard'.

Mappings to Agent Trace or OTel should be optional, incremental integrations after core validity. Both mapped records and native evidence retain original source locators and hashes. Do not redesign v1 around multiple interchange formats before an independent consumer needs them.

## Adoption and governance plan

The atlas serves concrete queries: where a tested product writes sessions, which companions are needed, how to join tool results, what happens to branches, which older versions parse, and how to decode offline. Each page links sample native artifacts and precise versioned outcomes. These are useful even before a user cares about a ranking.

The validator and fixtures serve parser maintainers: deterministic positive and negative cases, expected reconstruction, compatibility regressions, and provenance. Publish a small documented integration example that runs without vendor credentials. Agent Sessions is one potential consumer, not the reference oracle, mandatory dependency, or privileged implementation. No code or fixture copying from its repository is authorized by this design.

Research releases include a protocol description, related-work comparison, limitations, dataset card, expected outputs, native artifact manifest, license, reproduction guide, and citation metadata. A DOI cannot compensate for incomplete evidence. Prefer version-specific citations for reported metrics. Protocol, evaluator and dataset versions remain distinct even if packaged together for convenience.

Proposed governance uses evidence-based public corrections, transparent ownership/conflicts, vendor right of reply without veto, and independent review of disputed results. A correction references the old and new assertion IDs, cause, impact, and reviewer. Preserve historical snapshots; do not silently alter cited rankings. Submitted adapters meet the same acceptance criteria regardless of contributor affiliation.

After publication is authorized, lead with concrete experiments: native history after compaction, reconstruction with a missing sidecar, differences between CLI and desktop, or migration survival. Each finding should be reproducible and narrowly scoped to builds and configurations. Do not contact maintainers, solicit participation, or publish announcements during this design phase.

Pilot success is evidence completeness and feasible maintenance, followed by independent recomputation. Adoption success is third-party validator use, contributed fixtures/adapters, independent live reproduction, citations, and product fixes linked to benchmark evidence. Stars and traffic are supporting signals, not proof of a standard.

## Research and review ledger

Three `gpt-5.6-luna` subagents handled bounded assignments and were reused: baseline audit then related work; coverage then source clarification; scenarios then independent design critique. The primary agent synthesized the contracts, inspected consequential source/code claims, and owns acceptance. No premium external review was used.

Primary-agent verification corrected several preliminary claims: read-only SQLite does not categorically omit WAL rows; a current all-not-run division-by-zero path was not established; Claude Code and Cowork cannot share a pilot row; the old Cursor history documentation link redirects; assistant output observation must not read the tested transcript; and different crash barriers are distinct conditions requiring their own repeats.

Accepted critique changes: explicit live output observation rules, predeclared resume locator, process-tree/capture contract, fixed quiet-prefix versus immediate-loss distinction, per-condition repetitions, unsupported-capability evidence, and diagnostic metrics alongside strict core designation. The quiet interval is explicitly provisional until calibration; it is not a documented vendor guarantee.

Final design review found no remaining consequential approval blockers after clarifying family summaries versus condition cells. The primary agent checked run arithmetic, local document links, whitespace, source caveats, and repository scope. This is design validation only: no benchmark tests or live collection were executed. Observable output capture, isolated persistence processes, capture coherence, and independent participant availability remain explicit implementation/collection gates.

All product persistence assertions beyond historical repository data remain hypotheses or documentation claims pending separately authorized probes. No account readiness, rate limit, current installed version, current local root, or live safety claim is made here.
