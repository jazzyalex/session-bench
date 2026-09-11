# Session-Bench format atlas

Snapshot date: **2026-09-10**. Release: **draft_unreleased**. Edition: **documentation_only**.

**Unreleased candidate documentation inventory. It contains no vendor result or qualification.**

Public format and surface inventory; not a conformance result.

Machine-readable source: [`atlas/v1/index.json`](../../atlas/v1/index.json). Contract: [`schemas/v1/atlas.schema.json`](../../schemas/v1/atlas.schema.json).

Reproduce this snapshot:

```sh
python3 -m session_bench validate-atlas atlas/v1/index.json
python3 -m session_bench render-atlas atlas/v1/index.json --as-of 2026-09-10 --out docs/atlas/README.md
```

| Surface | Candidate configuration | Artifact family | Source review | Freshness | Live test |
|---|---|---|---|---|---|
| desktop | Claude Desktop Code tab / desktop_code_local | unknown | 2026-09-10 | current; due 2026-10-10 | not tested |
| cli | Codex interactive CLI / interactive_local | unknown | 2026-09-10 | current; due 2026-10-10 | not tested |
| ide | GitHub Copilot in VS Code / vscode_agent_session | unknown | 2026-09-10 | current; due 2026-10-10 | not tested |

## Claude Desktop Code tab

Entry ID: `claude-desktop-code-local`. Status: **documented_candidate**.

Identity: `desktop` / `desktop_code_local` / `macOS`; version: `unknown`; provider/model: `unknown`.

| Claim partition | State | Evidence | Statement |
|---|---|---|---|
| surface_documentation | documented | public_documentation | Public documentation identifies Claude Desktop and its Code/local execution surface; this entry keeps desktop execution distinct from CLI execution. |
| artifact_documentation | unknown | none | The native artifact family is unknown in this documentation-only entry. |
| writer_behavior | not_tested | none | Writer behavior is not tested. |
| decoder_correctness | not_tested | none | No vendor decoder qualification is claimed. |
| reproduction | not_tested | none | No independent live or native-bundle reproduction is claimed. |

Sources:

- [Claude Desktop](https://code.claude.com/docs/en/desktop) — Code / Local execution surface (inspected 2026-09-10)

Open format questions:

- Native desktop roots and companion artifacts were not inspected.
- Desktop UI execution, surface markers, retention, joins, and local availability require separate authorized measurement.

## Codex interactive CLI

Entry ID: `codex-interactive-cli-local`. Status: **documented_candidate**.

Identity: `cli` / `interactive_local` / `macOS`; version: `unknown`; provider/model: `unknown`.

| Claim partition | State | Evidence | Statement |
|---|---|---|---|
| surface_documentation | documented | public_documentation | Public documentation identifies Codex developer commands and built-in slash commands; this entry records the proposed interactive local CLI surface only. |
| artifact_documentation | unknown | none | The native artifact family is unknown in this documentation-only entry. |
| writer_behavior | not_tested | none | Writer behavior is not tested. |
| decoder_correctness | not_tested | none | No vendor decoder qualification is claimed. |
| reproduction | not_tested | none | No independent live or native-bundle reproduction is claimed. |

Sources:

- [ChatGPT developer commands: built-in slash commands](https://learn.chatgpt.com/docs/developer-commands#built-in-slash-commands) — built-in slash commands (inspected 2026-09-10)

Open format questions:

- Native local roots and companion artifacts were not inspected.
- Physical encoding, session identifiers, joins, retention, and surface markers require authorized native capture.

## GitHub Copilot in VS Code

Entry ID: `github-copilot-vscode`. Status: **documented_candidate**.

Identity: `ide` / `vscode_agent_session` / `macOS`; version: `unknown`; provider/model: `unknown`.

| Claim partition | State | Evidence | Statement |
|---|---|---|---|
| surface_documentation | documented | public_documentation | Public VS Code documentation identifies agent sessions as an IDE surface; this entry keeps the IDE execution context distinct from CLI and desktop rows. |
| artifact_documentation | unknown | none | The native artifact family is unknown in this documentation-only entry. |
| writer_behavior | not_tested | none | Writer behavior is not tested. |
| decoder_correctness | not_tested | none | No vendor decoder qualification is claimed. |
| reproduction | not_tested | none | No independent live or native-bundle reproduction is claimed. |

Sources:

- [VS Code agent sessions](https://code.visualstudio.com/docs/agents/run/sessions/manage-sessions) — Manage sessions (inspected 2026-09-10)

Open format questions:

- Native VS Code and extension roots, companions, and export boundaries were not inspected.
- Provider boundary, session identifiers, retention, and local portability require separate authorized measurement.

## Corrections and maintenance

Each entry records its owner, source-inspection date, live-test date, and next review date in the machine-readable atlas. Disputes should cite an entry or evaluation ID and contradictory evidence; corrections retain the prior identity and link any superseding entry or evaluation.

The atlas is maintained independently of conformance releases. A documented capability does not establish writer behavior, decoder correctness, or independent reproduction.
