# Local v1 prototype execution

Historical authorization on 2026-09-10 covered a three-surface pilot for Codex CLI,
Codex Desktop, and OpenCode CLI. The historical bounded campaign is recorded in `plans/survival-v1/campaign.json`. The
active release cohort is `plans/survival-v1/campaign-prospective-claude-cohort.json`:
Codex CLI/Desktop, Claude Code CLI/Desktop Code (Local), and OpenCode CLI.

The report is labeled **v1 prototype**, not a public 1.0 release. Existing historical scores and the README's public-release promise remain unchanged.

## Boundaries for this execution

- Local files and a local report only. No publishing, outbound promotion, purchases, credit redemption, account/provider fallback after auth errors, or private-history collection.
- Start with one pilot per configuration, at most one correction attempt each. Expand to three evaluated repetitions only after metric contracts and capture feasibility are established. Retain every attempt. Fifteen attempts is the outer proposed ceiling; it is not a target.
- Codex CLI uses the supported noninteractive `exec`/exact-ID `resume` surface with cheap explicit model and isolated configuration; label it precisely. It is not an interactive TUI test.
- Codex Desktop uses a supported native app task interface if available. Computer Use denied access to the Codex app; do not bypass that denial. Without independent display observation, displayed-text and UI-usability claims remain unmeasured. App task API behavior must not be called manual GUI behavior.
- OpenCode uses an isolated project and supported configuration controls. Resolve the provider/model before a live call; do not read authentication secrets or copy an existing personal database.
- A fresh read-only Codex quota observation at the start of this execution showed 34% weekly consumed. This prototype's live collection must stop at 37% used, or earlier if unreadable or exhausted. Model orchestration also consumes the shared account and may exhaust this small collection envelope. Do not raise it silently. Existing F0's absolute 13% stop stays intact in its historical plan.
- Per pilot: two submitted workload turns, at most 10 minutes wall time, 16 MiB per captured artifact. Model failure and absence of a supported capture route are reportable outcomes, not excuses to invent data.

## Useful completion

A working locally viewable report with the three named configurations, actual measured pilot data where captured, explicit unavailable/unresolved cells, five-angle category results, evidence examples, storage data, and a reproducible local generation command. A pilot does not receive a publication-ready overall rank. Attempt the requested configurations rather than substituting unrequested products for a fuller chart.

The orchestrator owns direction, claim adjudication, integration, and scope cuts. Sol High owns the bounded measurement core. Luna Max owns capture inventory and report rendering. No recursive delegation. Preserve user work and legacy tests.

## Recorded pilot interventions

Codex CLI used two workload turns. Desktop used one setup turn that stopped at a directory mismatch, then two workload turns within its new task directory. OpenCode used an initial wrong-workdir turn, a corrective baseline turn, and the requirement-correction turn. Its corrective stream was stopped at the 240-second controller timeout after helper/native evidence showed model completion. Its edit permission pattern prevented the final edit. All setup and controller effects remain explicit limitations; no additional corrective collection is attempted after the account quota reading reached 40% (above the 37% collection stop).

No overall rank is established by these three pilots. The local prototype demonstrates real acquisition, measured category facts, report rendering, and inspectable evidence for the requested lineup.
