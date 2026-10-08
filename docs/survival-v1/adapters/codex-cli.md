# Codex CLI adapter status

Status: **four current calibration attempts retained as invalid/N/A; lane stopped; unscored** on
2026-09-14.

The current adapter uses `/opt/homebrew/bin/codex` `0.154.0` through a fresh,
device-authenticated `CODEX_HOME`. It starts R1 with `codex exec --ignore-user-config
--json --sandbox workspace-write --model …` and continues only by the observed native
thread ID with `codex exec resume --ignore-user-config --json --model …`. The resume
route deliberately carries neither `--sandbox` nor `-C`: the CLI inherits both from the
first session and rejects those flags on `resume`.

The evidence boundary is `CODEX_HOME/sessions`. The credential-bearing parent is neither
inventoried nor copied. A calibration may proceed only when that session root is empty,
one new JSONL rollout appears after R1, no undeclared companion appears after R2, and a
hash-verified copied package decodes without the original root. The controller then
removes every native R2 response representation in a derived copy and requires the
decoder to lose that response.

The three current attempts are visible in the
[attempt ledger](../attempt-ledger.md#current-prospective-codex-cli-calibration-attempts):
two controller defects and two matching incomplete workloads. They demonstrate that the controller
records a stopped run without converting a response canary, a native file, or an account
failure into a persistence score. None is a calibration pass or an evaluated result.

The remaining gate is a changed declared configuration or workload condition, followed by
one complete workload run with the required inspect, baseline, edit, and final helper
outcomes. It must then pass copied-root equality and selected-loss checks before Codex CLI
gets an evaluated-run slot.

## In-packet scoring, 2026-10-03

The three retained evaluated runs now resolve all 31 metrics inside the closed
packet replay (`scripts/build_codex_stdout_score_replays.py`). The separate
private diagnostics for changed files, event timestamps and root location are no
longer needed for these packets.

- **Usage, model and reconciliation** are scored from the native rollout. Each
  `token_usage_record` carries a `response_id`, per-request token fields, and
  the turn and thread totals. The stdout stream reports no model and only
  cumulative usage, and it is not compared.
- **Model identity** comes from the one `turn_context` that shares the native
  turn id of the response. Codex records no configuration identity beside the
  model.
- **Changed file** hashes come from native records only: the source printed by
  the native inspect command, and that source with the native `FileChange` hunk
  applied. They are compared with the independent workspace snapshots.
- **Event timestamps** are read from the exact hashed rollout line of each of
  the 13 observed events.
- **Root location** uses the three retained metadata-only normal-root receipts,
  carried in each packet under `inputs/root-evidence/`.
- **Format version** is `native_absent`. `session_meta.payload.cli_version` is
  the application build. `turn_context.payload.multi_agent_version` names the
  collaboration feature set of a turn; it is treated as a runtime feature
  version, not a storage schema version. This is a judgment: the vendor does not
  document the field.
- `event_msg` / `thread_settings_applied` is accepted as an opaque settings
  record, like the outer envelope of the same name.
- `portable.complete_root` stays a contradiction: the capture copied one rollout
  from a shared root and did not inventory the whole root.

## Duplicate safety and density, 2026-10-05

The read is the rollout JSONL of the session. The count is made on its raw
records, not on decoded facts.

- A prompt is written twice: `response_item` (role `user`) and `event_msg` /
  `item_completed` (`UserMessage`), with the same text.
- An agent message is written twice: `event_msg` / `item_completed`
  (`AgentMessage`) and `response_item`, with the same `msg_…` id and text. The
  final message of a turn is written a third time in `task_complete`
  (`last_agent_message`).
- A command call is written twice: `response_item` / `custom_tool_call` holds the
  command and its directory in the call input, and `item_completed`
  (`CommandExecution`) holds both again. `CommandExecution` does not hold the
  time and output limits of the call; a limit is not text and is not compared.
- A command result is written twice: `custom_tool_call_output` holds the full
  output text that `CommandExecution` holds.
- An edit call is written twice: the patch in the `custom_tool_call` input and
  the same hunk (same file, same removed and added lines) in `item_completed`
  (`FileChange`).
- An edit result is written once. `FileChange` holds the text of the patch tool
  (`Success. Updated …`). The result of the call is `custom_tool_call_output`
  (`{}`). Neither holds the full output of the other.
- Messages are joined by turn, role and exact text. An `item_completed` record is
  joined to the open call before it (the call whose output is not yet written)
  and is then checked by content. If it holds neither all arguments nor the full
  output, it is its own event.
- No field marks one copy as superseded. Only the edit result passes: 1 of 33,
  1 of 29 and 1 of 29 in the three runs.
- A user-role message that Codex marks as context (`content_item_kinds` without
  `user.text`: plugin list, AGENTS.md, environment) is not a prompt.
- Density: the first record that states an event keeps its role. A later record
  that only repeats stated events is a `snapshot`. In these rollouts the
  snapshots are `UserMessage`, the `response_item` agent messages, the
  `custom_tool_call_output` of a command, `FileChange` and `task_complete`.

## Public derivative

Codex rollouts embed the operator's personal agent instructions, the installed
skill list, and an approved-command list. `scripts/sanitize_codex_score_packets.py`
blanks every string under `world_state.payload.state`, and the bodies of the
`<skills_instructions>` and `# AGENTS.md instructions` messages, at equal UTF-8
byte length. It also aliases the home user name. Every native record is kept,
and the replay must return identical metric rows before and after.

It also zeroes `current_root_metadata.inventory_metadata_sha256` of every normal-root
receipt (public set v7). That digest covers the paths, sizes and times of every file of
the operator's normal Codex root and is equal in every receipt, so it was a stable
fingerprint of private machine state. The closed replay does not read it. The sanitizer
now allowlists no digest.

Set v8 zeroes the plain machine identifiers of the same receipts, at equal byte length.
A number becomes `0` padded with spaces to its original length (valid JSON, same type).
Zeroed: `current_root_metadata.file_count` and `total_size_bytes` (the file count and size
of the operator's normal Codex root); `device`, `inode` and `birth_ns` of the selected
rollout file in `quiescence.observed`; and `current_selected_metadata.inventory_metadata_sha256`
(a digest over those values, which would confirm a guess). The closed replay
(`codex_cli_root_evidence`) reads none of them. It does read, and the packets keep,
`filesystem_id` (it must equal `historical_proof.selected_filesystem_id`; its text is
`device:inode`, so it still shows both numbers), `current_selected_metadata`
(`file_count`, `total_size_bytes`), `historical_proof` (including the root file counts
before and after the runs) and `size_bytes`. The 31 metric rows of every run equal v7.

Vendor instruction text is blanked too (owner decision 2026-10-06), with the
marker `[vendor instruction text removed at equal byte length]`:
`session_meta.payload.base_instructions.text`, every text part of a
developer-role message (permissions, apps, plugin and multi-agent instructions)
and the plugin list of the injected user-role context message. The
`<environment_context>` part of that message stays: it holds the run
environment (directory, shell, date), not instruction text. Roles,
`content_item_kinds`, ids and byte counts stay, so duplicate safety and density
do not change. No metric reads a blanked string; identity comes from
`cli_version`, `originator` and `source`.
