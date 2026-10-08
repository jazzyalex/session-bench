# Copilot CLI native capture contract

Scope: GitHub Copilot CLI 1.0.91, model route `auto` (resolved to `gpt-6-luna`),
macOS, headless (`-p`, `--output-format json`). Three runs on 2026-10-04:
`copilot-2026-10-04-01`, `-02`, `-03` (repetitions 1, 2, 3). Each run used a new
empty `HOME` and `COPILOT_HOME`.

## Native format

The CLI writes one directory per session: `COPILOT_HOME/session-state/<session-id>/`.
The system message of the session names this folder. It also writes a session store
for all sessions of the home: `COPILOT_HOME/session-store.db`, a WAL-mode SQLite
database with `session-store.db-wal`. The scored native root is the session
directory plus these two store files. The directory holds:

- `events.jsonl`: the event log. Each line is one JSON object with `type`, `data`,
  `id`, `timestamp` (RFC3339, UTC) and `parentId` (the event before it).
- `workspace.yaml`: flat `key: value` lines (`id`, `cwd`, `client_name`, `name`, times).
- `checkpoints/index.md`: the checkpoint table.
- `rewind-file-snapshots/index.json` (`"schema": 1`): one snapshot per user message
  that changed files, with the pre-image and post-image SHA-256 of each file.
- `rewind-file-snapshots/backups/<sha256>`: the pre-image bytes; the name is the hash.
- `rewind-file-snapshots/tracking.json`, `.workspace-fork.lock`.
- `files/` and `research/`: empty in these runs.

The session store has its own schema version (table `schema_version`, value `8`).
In these runs the main file is one empty page and every row is in the WAL. Tables:
`sessions`, `turns` (one row per finished user turn, with the prompt and the final
response text), `assistant_usage_events` (one row per model call), `session_files`,
`checkpoints`, and an FTS5 search index over the turn text. The store holds one
session, because `COPILOT_HOME` was new.

`session.start.data` names `sessionId`, `producer` (`copilot-agent`), `version` (the
event log schema version, `1`), `copilotVersion` (the application build) and
`selectedModel`. `session.resume` starts the second process of the same session.

Identities and joins:

- A user message has `messageId`. Each `assistant.message` names it in
  `originatingMessageId`.
- A tool call has `toolCallId`. It is written twice: as a request in
  `assistant.message.toolRequests` and as `tool.execution_start`. Both hold the
  name and the arguments. `tool.execution_complete` joins by `toolCallId` and holds
  the result text, `success` and, for a shell call, `shellExecution.exitCode`.
- A rewind snapshot joins to its user message by `eventId` = event `id`.
- `assistant.message.model` is the response model. `selectedModel` is the route
  (`auto`). They stay separate.
- A usage row (`assistant_usage_events`) has `session_id`, `turn_index` and an
  autoincrement `id`. It has no event id. `turn_index` is the index of the user turn
  (0 for R1, 1 for R2). The rows of one `turn_index`, in `id` order, are the
  `assistant.message` events of that user turn, in log order. The decoder joins a
  turn only when the counts are equal, the position equals
  `assistant.message.data.turnId`, the models are equal and `finish_reason` agrees
  (`tool_calls` with tool requests, `stop` without). Timestamps are not a join key.

## How each group of metrics is decided

**Work, causal, revision (need the independent observer).** The observer is the
`--output-format json` stream of each turn, the helper ledger
`.survival-observer.jsonl`, and the workspace hashes before and after. The model
ran inspect, baseline, edit and final as four separate calls in every run. No
compound shell call occurred, so the compound rule is not used. A `view` or `glob`
call is observed but not scored.

**Changed file.** The snapshot index holds explicit pre-image and post-image
hashes. The decoder accepts them only when the snapshot joins the R2 user event,
the edit call succeeded, the parent chain from R2 to the edit result is unbroken,
and the backup file hashes to the pre-image value. It also applies the native
`apply_patch` text to the backup and records whether that gives the post-image
hash (`edit_recomputed_from_backup`; true in all three runs). No workspace or
observer value enters the native fact.

**Model identity.** Native per response: `assistant.message.model` and the selected
route.

**Usage, token semantics, reconciliation (native-attested, from the session store).**

- `assistant.message` in `events.jsonl` holds no token counts. The usage record of a
  model call is its `assistant_usage_events` row: `input_tokens`, `output_tokens`,
  `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`, plus
  `token_details_json` (billed counts per token type). The columns are nullable
  integers. The decoder keeps `0` as `0` and `NULL` as null; a null count gets no
  credit. `input_tokens` includes the cached tokens; `token_details_json` holds the
  uncached input count.
- The runs have 7, 7 and 8 rows, equal to the number of `assistant.message` events.
  Every row joins. The two displayed responses are the last call of each user turn
  (`finish_reason` = `stop`). Only their usage is scored, as for every
  configuration: the population is the displayed responses. The rows of the
  tool-call responses are kept as `usage_records` and are used for reconciliation.
- Second witness for the two displayed responses: `session.usage_checkpoint` in
  `events.jsonl` holds `prompt_tokens`, `cache_read`, `cache_write` of the last call,
  joined by `model_call_id` = `apiCallId`. They equal the joined usage row in all six
  cases. This is a check, not the join. When the two disagree, the response gets no
  usage.
- Reconciliation: `session.shutdown` declares cumulative session totals. The sum of
  the usage rows logged before a shutdown must equal it, for every field: request
  count, `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`,
  `reasoningTokens`, `totalNanoAiu`, and the four `tokenDetails` counts. All 11
  fields are equal for both shutdowns of all three runs.
- `native_absent` is used only when the complete root is proved, the store is read
  under its contract, and it holds no usage row. A store whose rows do not join
  leaves the metrics `unresolved`. So does an unproved root.
- The stdout stream is not native evidence.

**Duplicate safety and density (rule of 2026-10-05).** The read is every container
the decoder takes a scored fact from: `events.jsonl`, the session store (usage,
token semantics, reconciliation), the rewind snapshot index with its backups
(changed file) and `workspace.yaml` (identity). The count is made on raw records.

- A tool call is written twice in `events.jsonl`: as a request in
  `assistant.message` and as `tool.execution_start`, with the same `toolCallId`,
  name and arguments.
- The store `turns` row holds the R1 prompt and the R1 response again (same turn
  index, same text).
- The store row of the search index content (`search_index_content`) holds the
  complete R1 prompt and the complete R1 response in one cell. Any text cell of a
  store table other than `turns` that holds the complete text of a message is an
  occurrence of that message.
- The rewind snapshot index holds the R2 prompt again: `userMessage`, joined by
  `eventId` to the user event, same text.
- A rewind backup file holds the pre-image of the edited file. It holds the full
  output of the `view` result that showed that file, so it restates that result.
- A `tool.execution_complete` record restates its call when it holds all text
  arguments of the call: in run 3 the `glob` completion holds the pattern and the
  path. The only argument of a `view` is a path. The completion restates it when
  it also states the tool name; it does so in its telemetry
  (`toolTelemetry.properties.command` is `view`), and the text of the result is
  not counted as that evidence. So each `view` completion is one more occurrence
  of its call.
- No field marks a copy as superseded. So the calls, the two prompts, the R1
  response and the `view` result do not pass: 7 of 28, 8 of 29 and 6 of 30
  statements. Until 2026-10-08 the counts were 7 of 25, 8 of 26 and 6 of 27: the
  search index cell (two statements) and the `view` completion (one) were not
  counted. The set of events that pass did not change.
- Density: the request in `assistant.message` is the call. `tool.execution_start`,
  the store `turns` row, the search index content row and the rewind index are
  unclassified (`metadata` or `snapshot`). The `view` completion states its result
  first, so it keeps `tool_result`.
- The other choice was to never open the store. Usage, token semantics and
  reconciliation would then be `native_absent` (12 points) for a gain of about
  0.3 to 0.4 points in duplicate safety (9 of 24, 10 of 25, 8 of 26). The store stays in the read.

**Complete root and companions.** `root-start.json` proves that `HOME` and
`COPILOT_HOME` were empty. The launch receipts bind both directories and the
session id (`--session-id`, then `--resume`). The exit receipts and `root-end.json`
hold hash inventories of `COPILOT_HOME`. The validator requires: one session
directory in the root; equal hashes of that directory after R1 and before R2;
equal hashes after R2, after the copy, and in the copy. The same receipts hold the
hashes of `session-store.db` and `session-store.db-wal`; they must be equal after R2
and at the end, and the packet files must equal them. The companion set is every
file of the session directory except `events.jsonl`, plus the two store files.

The store files were not copied by the controller. They were retrieved on the same
day from the retained temporary root (`native-store/retrieval-receipt.json`) and
equal the capture-time hashes. `session-store.db-shm` is not part of the root: a
read-only open before the retrieval rewrote it. It is a rebuildable index of the
WAL and holds no row content. The decoder never opens the store files in place. It
copies both to a temporary directory and opens the copy; SQLite rebuilds the index
there.

Limit: `COPILOT_HOME` also holds `config.json`, `settings.json` and `logs/`. The
receipts list their hashes. They hold no session records and were not copied.

**Thread structure.** Parents come from explicit native joins
(`originatingMessageId`, `toolCallId`). If one join is missing, no parent is claimed
and the linear recovery rule applies.

**Self-contained identity.** Session id from `session.start` and `workspace.yaml`
(they must agree), harness from `producer`, surface from `client_name`.

**Format version.** `session.start.data.version` (1) is read from the native line
and the store version (8) from its `schema_version` table. The declared version is
`events=1;session-store=8`. The decoder contract names both and refuses another
value. Judgment: both are integer schema versions separate from the application
build, and builds 1.0.89 and 1.0.91 declare the same values with the same event,
companion and table shapes. Only one value of each was observed.

**Schema stability.** Every event type is in the decoder contract, every companion
matches the shapes above, and the store has the contract tables and columns and
holds only this session, for build 1.0.91 on 2026-10-04.

**Event timestamps.** Each of the 13 observed events takes the timestamp of its own
native record: the event line, or for the file change the `timestamp` of its
snapshot in the index.

**Root location.** Three rows, one per run, bound to the fresh-root receipts of all
three runs (carried in each packet under `inputs/root-repetitions/`). The locator
names the session directory and the two store files under `COPILOT_HOME`.

**Naive-reader duplicate safety.** A forward reader meets every tool call twice
(request and `tool.execution_start`). No field marks one as superseded. Both stay
active, so each call fails. The store `turns` row repeats the R1 prompt and the R1
final response (same turn index, same text), so these two messages also have two
active occurrences and fail. The search index content row holds both texts in one
cell: it is a third occurrence of each. The other messages and all results pass.

**Content density.** Every file of the root is in scope. One `events.jsonl` line is
one record, counted as compact key-sorted JSON. A JSON companion is one record
counted the same way. A text companion is one record counted by raw bytes. Each
store row, and each `sqlite_master` row, is one record counted as compact key-sorted
JSON of its columns (a BLOB by its raw length), as for the OpenCode database.
Roles: user message, assistant message (text), tool call (`assistant.message` with
requests), tool result (`tool.execution_complete`) are useful.
`tool.execution_start`, turn markers, usage checkpoints, session events, the system
message, snapshots, backups and every store row are unclassified: the store is a
derived index and accounting table, and its `turns` row mixes a prompt and a
response. A record is classified as a whole, as for Pi: the encrypted reasoning
fields inside an assistant record are counted with it.

## Public derivative

`scripts/sanitize_copilot_score_packets.py` builds the public packets. The native
directory holds no home user name, e-mail, GitHub login or token: the runs were
isolated and `system.message` holds only the vendor prompt, the working directory
and the session folder.

The vendor system prompt is blanked (owner decision 2026-10-06), with the marker
`[vendor instruction text removed at equal byte length]`: `system.message`
`data.content` and every `data.contentBlocks[].content`, in `events.jsonl` and in
its copy under `inputs/capture/native/`. One line of `data.content` is kept:
`* Operating System: macos`. The public inputs builder reads the operating system
of the capture from it. No metric reads the prompt. The session store does not
hold it. The raw stream copies `inputs/capture/capture/r1.stdout` and `r2.stdout`
hold one more vendor instruction string: `serverMetadata.instructions` of the
`session.mcp_servers_loaded` event (the GitHub MCP Server guidance, 903
characters). It is blanked the same way. The observer does not read that event. `user.message.transformedContent` stays: it is the prompt with a
date-time tag.

Also changed, at equal byte length:

- the per-user macOS temporary directory id in every path, also hex-encoded in the
  snapshot index, and inside the store pages (`sessions.cwd`,
  `session_files.file_path` and their index entries);
- the key of each file under `files` in `rewind-file-snapshots/index.json`, in the
  native copy and in the capture copy. The vendor derives that key as the first 32 hex
  characters of SHA-256 of the file path, so the captured key was a hash of a private
  path (it holds the per-user temporary directory id). The sanitizer sets it to the
  first 32 hex characters of SHA-256 of the public aliased `path` stored beside it (same
  length), in every file where the old key stands. The new key is a prefix of the digest
  of a JSON string of the packet, so a reader recomputes it. No metric reads the key;
  the transformation receipt `aliases` count `rewind_snapshot_keys_rekeyed` records the
  change;
- the 12-character hashes of the request record (`session.usage_checkpoint`,
  `promptCacheBreakState`) that depend on per-run private text, zeroed to 12 zeros in
  every file and copy where the value stands (event log, `r1.stdout`, `r2.stdout`,
  native and capture copies): the `hash` of the `environment_context` and
  `workspace_context` system prompt segments (their text holds the temporary working
  directory and the session folder) and every `hash` of `conversation.points` (a hash of
  the messages sent so far). I reproduced the `environment_context` hash as the SHA-256
  prefix of the private segment text. The hashes of the other segments and of the tool
  schemas are equal in every packet and stay. No metric reads any of them; the
  transformation receipt `aliases` count `request_hashes_zeroed` records the change
  (public set v9);
- the home user name and the `PATH` value in the launch receipts;
- in the root, launch and exit receipts, every digest of a file that is not in the
  packet, not empty and not a vendor package file, and its digest-like path part
  (configuration, user cache, device id, logs, the `-shm` file, R1-state files).

Kept: every native record and key, tool call ids, per-call provider handles
(`apiCallId`, `request_id`, `github_request_id`, `interactionId`) and encrypted
reasoning. A reader cannot map them to an account. The vendor system prompt is
kept verbatim; publishing it is an owner decision.

A WAL frame carries a running checksum over its page bytes. After the change the
script recomputes the checksums of exactly the frames that were valid before. It
then opens a copy of the public store and requires a clean integrity check, the
same number of valid frames, and the same rows as the private store with the alias
applied. A normal SQLite reader accepts the public store. A reader can verify the
public bytes, checksums and rows; a reader cannot verify the private checksums.

The script replays each packet before and after and stops when a metric row
differs. Private pins stay in `*-private-transformation.json` beside the packets.

No capture-time identity, public-safety approval, independent reproduction or
publication is inferred from successful offline decoding.
