# CLI/Desktop native-format assessment

Inspected 2026-09-13. This is a format and capture-boundary assessment, not a
vendor score. The active prototype target is Codex CLI, Codex Desktop, Claude
Code CLI, Claude Desktop Code (Local), and OpenCode CLI. The original frozen
five-surface campaign still records Cursor CLI/Desktop attempts; those attempts
remain paused and are not retroactively assigned to Claude.

| Pair | Evidence | Decoder decision | Remaining qualification |
|---|---|---|---|
| Codex CLI/Desktop | The retained synthetic CLI and Desktop rollouts have the same six top-level record families: `session_meta`, `event_msg`, `response_item`, `world_state`, `turn_context`, and `token_usage_record`. Their envelope keys match; the payload keys overlap, with small surface-specific extras. The current decoder accepts both `codex-cli` and `codex-desktop`, and tests cover Desktop-specific response and action shapes. | Reuse the versioned `codex-rollout-v1` decoder. Keep separate configuration IDs and surface results. | The Desktop calibration copied one rollout and thread-keyed shell snapshot, but the complete Desktop persistence root and canonical copied representation are not yet proven. No evaluated Desktop score. |
| Claude Code CLI/Desktop Code (Local) | Anthropic documents the same underlying coding engine and separate session histories. Fresh project-keyed CLI and Desktop JSONL files now each decode with the same closed-package parser. The successful Desktop calibration reconstructs two human turns, two responses, four actions, and four tool results; its damaged copied package loses the selected R2 response. The CLI calibration authenticates and persists JSONL but refused R1 before tool use. | Reuse `claude-code-jsonl-v1` only for the declared project-keyed transcript file. Keep separate capture adapters and configuration IDs. | The observed transcript directory contained one new file in each calibration, but a complete cross-root companion-family proof is still open. The CLI needs a successful workload capture; neither surface has an evaluated score. Desktop Chat and Cowork are outside this Code (Local) target. |

The Codex record-family comparison uses only the retained synthetic, filtered
captures at `artifacts/survival-v1-runs/codex-cli-cal-1/capture/public-native/`
and `artifacts/survival-v1-runs/codex-desktop-cal-2/capture/public-native/`.
It does not imply equal retention, continuation behavior, observer quality, or
score across surfaces. The installed Claude versions inspected were CLI
`2.1.270` and Desktop `1.52386.3`; no private Claude session or credential
store was opened.

Anthropic's [Desktop reference](https://code.claude.com/docs/en/desktop)
states the shared-engine/separate-history relationship. Its
[session reference](https://code.claude.com/docs/en/sessions) documents CLI
JSONL location, configuration-based isolation, and the separate Desktop
retention setting. OpenAI says the
[Codex app picks up CLI session history](https://openai.com/index/introducing-the-codex-app/);
the retained synthetic record comparison above is the stronger local evidence
for this particular decoder decision.

For Claude, the next admissible test is a successful isolated two-turn CLI
calibration, followed by complete companion-family closure for both surfaces.
Compare record families, join keys, tool-call/result links, usage fields, and
any app-specific database or metadata rows. Copy the declared family, decode
offline, and remove a selected native fact to prove the loss control. A shared
parser must not merge the two score rows.

## Row decision, 2026-10-03

Owner rule: a Desktop surface shares its CLI row only when the session format is
the same. Otherwise it keeps its own row.

**Codex CLI and Codex Desktop: same format, one row.** A key-structure diff of
the three retained CLI rollouts and the three retained Desktop rollouts found the
same record kinds in both. The only differences are optional settings fields
(permission profile, reasoning summary, effort) and one tool-output record kind
that depends on which tool the agent chose. One decoder reads both. A keyed
lookup in a retained copy of the shared stores found a CLI thread (`source:
exec`) with a row in `state_5.sqlite` and with turns and items in
`thread_history_1.sqlite`, the same as a Desktop thread. Two gaps remain: only
the Desktop thread appeared in `session_index.jsonl`, and the CLI captures did
not inventory shell snapshots. The Codex row uses the CLI captures, because their
stdout observer sees actions, results and the file change.

**Claude Code CLI and Claude Desktop Code (Local): different, separate rows.**
Both use the same transcript family and decoder, but the retained Desktop
transcripts have six record kinds that the CLI transcripts lack
(`file-history-snapshot`, `file-history-delta`, `bridge-session`, `custom-title`,
`agent-name`, `system/stop_hook_summary`), and Desktop writes a separate metadata
file in another root. The CLI captures ran headless (`-p`) on an older build, so
part of the gap may come from mode or version.
