# Cursor survival-v1 adapter

Two surfaces: Cursor CLI (`cursor-cli`, scored, this document) and Cursor Desktop
(`cursor-desktop`, not yet scored, last section).

# Cursor CLI native capture contract

Scope: Cursor CLI (`agent`) build `2026.10.01-e373342`, macOS, headless
(`--print --output-format stream-json`), model route `auto`. Three runs on
2026-10-06 (local date; the session creation time is 2026-10-07 UTC):
`cursor-cli-2026-10-06-r1`, `-r2`, `-r3` (repetitions 1, 2, 3). Packet set v3.
Set v2 was rejected for one privacy defect: the public shared-store extract held
18 short vendor digests that are not bound to public bytes.
Set v1 was rejected by the independent review: it called the root complete, but
the CLI also writes to a shared store outside the two isolated directories.

## Runs and disclosed attempts

The designated runs are the first three attempts of the day with this build. This
was fixed before any score was computed. The helper ledger is clean in all three
(inspect 0, baseline 1, final 0, no retry).

Older attempts, not used:

- `cursor-cli-2026-09-30-r1`, `-r2`, `-r3`: build `2026.09.28-64d2043`, isolated
  roots. Retained, never scored.
- `cursor-cli-2026-10-02-r1`: the same older build, normal signed-in root.
- `cursor-cli-eval-1`, `cursor-cli-eval-2` and its three correction attempts,
  `cursor-cli-cal-1`, `cursor-cli-setup-2`: early attempts before the decoder
  existed. `eval-2` and `cal-1` are invalid (a steered turn; a model that the
  plan does not allow).

## Capture scope

The controller (`session_bench/cursor_live_capture.py`, as it was at capture
time) refuses an existing run directory. It creates `cursor-config` and
`cursor-data` inside the new run directory and passes them as `CURSOR_CONFIG_DIR`
and `CURSOR_DATA_DIR`. `HOME` stays the operator's home: the CLI logs in from
there and reads the operator's rules and skills from there. The controller runs
turn 1, then turn 2 with `--resume <session-id>`. After turn 2 it copies the
session family and records its hashes.

The controller of these runs took no inventory. The two directories are retained
whole and the packet builder lists them (`root-inventory.json`): every entry with
size, SHA-256, birth time and modification time. The validator
(`qualify_isolated_root`) requires:

- the plan is a prepared isolated capture and the environment binds both
  directories inside the run directory;
- two turns, exit code 0, and the stdout hashes of the controller state equal the
  packet streams;
- every entry of both directories was born after turn 1 started and last changed
  before turn 2 ended;
- the session files sit at the two paths that follow from the workspace path and
  the session id (see *Root location*);
- their hashes equal the controller manifest and the packet bytes;
- nothing else is in the two directories except `cli-config.json` and
  `statsig-cache.json`.

### Outside the two directories

The CLI also writes to the shared Cursor home `~/.cursor`. Known store:
`~/.cursor/ai-tracking/ai-code-tracking.db` (SQLite, `user_version` 0). It holds
rows of every AI edit of every session of the operator, so it cannot be copied
whole. Tables: `ai_code_hashes`, `scored_commits`, `tracking_state`,
`conversation_summaries`, `tracked_file_content`, `ai_deleted_files`.

On 2026-10-06 the owner allowed reading only the rows of the three test
sessions. The coordinator copied the store to a temporary directory, selected the
rows that hold a test session id or the workspace path of a run, saved them
beside each run (`shared-store-extract/ai-code-tracking-rows.json`) and deleted
the copy. The read was made after the captures from the live store. It is not
hash-bound to capture time; it is bound by content. Each packet carries the
extract (`inputs/capture/shared-store-extract/ai-code-tracking-rows.json`), bound
by the capture assertion.

What the rows are, per run:

- `ai_code_hashes`, 6 rows: `hash` (up to 8 hex characters, a 32-bit vendor
  digest per changed line; its preimage is unknown and differs between runs),
  `source` (`cli`), `fileExtension`, `fileName` (the edited file), `requestId`
  (the call id of the native Write step), `conversationId` (the session id),
  `timestamp`, `createdAt` (milliseconds), `model` (`default`, the route).
- `tracked_file_content`, 1 row: `gitPath`, `content` (the whole file after the
  edit), `conversationId`, `model`, `fileExtension`, `createdAt`.
- No row in the other four tables. No token count of any kind.

How the packet uses them (`shared_store_findings`): the absence rows are accepted
only when the extract names this session and the six tables, every row has
exactly the columns above, no column names a count, and the rows describe the
captured store: the session id, the call id and the path of the native edit, a
time inside the native edit step, and content whose SHA-256 is the native
post-edit hash. An unknown table or column could be a count; then the three usage
rows stay `unresolved`.

What else is known about `~/.cursor`. A metadata inventory of the shared home
exists from 449 seconds after run 3 ended
(`cursor-desktop-2026-10-06-r1/shared-home-before.json`, 19,672 entries, no
content read). By modification and change time:

- inside the run 1 window: 0 entries; inside the run 2 window: 0 entries;
- inside the run 3 window: 2 entries, the store above and its directory;
- between the windows: 0 entries;
- after run 3 and before the inventory: 154 entries (117 under `projects/`,
  37 under `skills-cursor/`), the first one 357 seconds after run 3 ended. They
  belong to a later process.

Limits of this check: an inventory shows only the last change of an entry. A
write of run 1 or run 2 to the store is hidden by the write of run 3 (the rows
prove those writes). A write of a run to one of the 154 later entries would be
hidden too. No path of the inventory holds a session id.

Never inventoried: every place outside `~/.cursor` and the two directories, for
example `~/Library/Application Support`, caches, temporary directories and the
keychain. Other writes there cannot be ruled out.

Judgment, by the standard of the ranked rows. Codex CLI and Antigravity also ran
in the operator's home and inventoried only the vendor state directory; places
outside it were never listed, and their rows are resolved with this limit
disclosed. So the unlisted places leave no row of this row unresolved. The
weaker point here is that the vendor home was checked after the fact and not
bracketed. This is disclosed. The controller now brackets it for future
captures (`session_bench/cursor_shared_home.py`): a metadata inventory of the
whole shared home before turn 1 and after turn 2; every change must be
session-owned (the path carries the session id), a known shared store, or a
directory above one; anything else stops the capture.

## What each file holds

| File | Content | Session record? |
|---|---|---|
| `cursor-config/chats/<workspace-hash>/<session-id>/store.db` | SQLite, `user_version` 1. The whole conversation as a content-addressed blob graph. | Yes. The primary record. |
| `…/<session-id>/meta.json` | `schemaVersion` 1, creation and update time (ms), `hasConversation`, `cwd`. | Yes. A sidecar. |
| `cursor-data/projects/<workspace-key>/agent-transcripts/<session-id>/<session-id>.jsonl` | Prompts, assistant text and tool calls as JSON lines. No tool result, no id, no model, no record time. | A second copy. In the read (the absence scan opens it). |
| `~/.cursor/ai-tracking/ai-code-tracking.db` (shared, outside both directories) | Rows per AI edit: line hashes and the file after the edit, with session id and call id. | Rows of the session. Not copied whole; the rows are bound as an extract. Outside the read. |
| `cursor-config/cli-config.json` | CLI settings, the selected route (`auto`), the account identity. | No. It holds no session id, no run canary and no prompt. Not in the packet. |
| `cursor-config/statsig-cache.json` | A cache of remote feature settings and the account context. Its fetch time is before the session creation time. | No. Not in the packet. |

The family is the first three files: every file whose path carries the session
id. It is complete and hash-bound. The root is not complete, because rows of the
session sit in the shared store. The two harness files are listed in
`root-inventory.json` with size and hash; in the public packet their hashes are
zero.

## Primary record: the chat store

Tables: `meta` (one row, key `0`; the value is hex text of a JSON object with
`agentId` = the session id, `latestRootBlobId`, `name`, `createdAt`, `mode`,
`isRunEverything`, `blobEncryptionKey`) and `blobs` (`id`, `data`). A blob id is
the SHA-256 of the blob bytes. Blobs name each other by id.

Every blob is one of six classes. The decoder names the class by the place of the
blob in the graph. In the three runs every blob has a class (58, 64 and 72 blobs).

| Class | Encoding | Content |
|---|---|---|
| `message` | JSON | One message as it is sent to the model: `role` system, user, assistant or tool. An assistant message has parts `reasoning` (an encrypted `signature`, and `providerOptions.cursor.modelName`), `text` and `tool-call` (`toolCallId`, `toolName`, `args`). A tool message has `tool-result` (`toolCallId`, `toolName`, `result`). |
| `root` | protobuf | The state after one write. `meta.latestRootBlobId` names the current root. Older roots stay in the table as checkpoints. |
| `turn` | protobuf | One user turn: its user message and its ordered steps. |
| `user_message` | protobuf | The prompt as typed, its message id, its creation time. |
| `step` | protobuf | One of: text; tool call with its arguments and its result; thinking text. Each with its own times. |
| `file_content` | raw bytes | A workspace file before or after an edit. |

The vendor publishes no schema for the protobuf blobs. Fields by number
(`session_bench/cursor_cli_store.py`, `SCHEMA`):

- root: 1 message blobs in order; 4 a pending assistant message as JSON text;
  5 an estimate of the context-window size (1 tokens, 2 window, 3 the same by
  part); 8 turn blobs in order; 9 workspace URI; 15 file states (1 path, 2.1 the
  current content blob, 2.2 the first content blob); 18 a touched file path;
  21 repository path and branch; 22 the surface (`cli`); 26 start time (ms);
  27 time zone; 38 user message ids.
- turn: 1.1 the user message blob; 1.2 step blobs in order; 1.3 request id;
  1.4 an opaque vendor token; 1.9 tool names; 1.10 user message id.
- user message: 1 text; 2 message id; 10 the root at the start of the turn;
  25 and 26 creation time (ms).
- step: 1 text (1 text, 2 time); 3 thinking (1 text, 2 duration, 3 start, 4 end);
  2 tool call (57 call id, 59 start, 60 end, and one of 1 Shell, 4 Glob, 8 Read,
  12 Write; each with 1 arguments and 2 result). A Shell result is a success
  (5 output; exit code 0 is not stored) or a failure (3 exit code, 5 output). A
  Write result holds the whole file before (6) and after (7) the edit.

Opaque values, kept as bytes and named as such: `reasoning.signature` (encrypted
model reasoning), turn field 1.4 (a vendor token) and `blobEncryptionKey`. The
blobs of this build are stored in clear.

The store states the conversation twice. The turn tree (root field 8) holds the
prompt as typed, the steps, the call ids and the times. The message list (root
field 1) holds the same events as JSON, with the model name and the tool names.
Only the JSON message list wraps a prompt in `<timestamp>` and `<user_query>`.

Joins: `meta.latestRootBlobId` to the root; root to turns; a turn to its user
message and steps by blob id; the call id of a step equals `toolCallId` of the
JSON tool call and tool result. A text step has no id in the JSON list. It is
bound to its JSON text part by position in the turn and equal text.

Decoder contract `cursor-cli-chat-store-v1` (`decode_cursor_cli_native`):
`user_version` 1, sidecar `schemaVersion` 1, the two tables, the six classes, the
field maps and the four tool kinds. An unknown field, an unknown part, a blob
without a class, a blob that differs from its id, or a turn whose two statements
differ makes the decode unsupported.

## The read

The decoder takes every scored fact from `store.db` (tables `meta` and `blobs`)
and the declared version from `meta.json`. Only the store has tool results, call
ids, record times and the model; the second process resumes from it.

The transcript is in the read too. The decoder takes no fact from it, but the
absence scan for usage (`usage_key_findings`) opens it and lists its key names.
A container that is opened counts: its lines are records for duplicate safety
and density. (Set v1 kept it outside and still parsed it; that was not
consistent.)

The shared store `ai-code-tracking.db` is outside the read. The decoder takes no
fact from it and it is not a file of the family. Its `tracked_file_content` row
states the edited file again; this is not counted as a duplicate. The validator
reads the bound rows only to prove the absence, as for Antigravity.

The decoder never opens `cli-config.json` or `statsig-cache.json`.

## How each group of metrics is decided

**Work, causal, revision.** The observer is the stdout stream of each turn, the
helper ledger and the file hashes.

- *Turns 2/2.* The text of the `user_message` blob equals the submitted prompt.
- *Responses 2/2.* A text step is a response. It is the final answer of its turn
  when no text or tool step follows it; an earlier text step is commentary.
- *Actions 4/4.* The stream and the store share the call id. A native call that
  pairs by id with an observed unscored action (`Glob`, `ls`, `Read`) is outside
  the population. A native call that the observer did not see would stay in.
- *Compound shell call.* In all three runs one Shell call runs
  `… inspect … && … baseline …`. The call is the native record of both helper
  segments. Each result is its own helper output line. The call exit code (1)
  counts for the last segment (baseline). Inspect returned 0: an unbroken `&&`
  chain leads from it to baseline, whose output is in the result. The observer
  takes the exit code and the output line of each helper from the helper ledger.
- *Results 4/4.* Status, exit code, helper nonce and output must agree.
- *Action-result relation 4/4.* From the native call id (and the segment index).
- *Changed file 1/1.* The Write step holds the whole file before and after the
  edit; both hashes are computed from these native texts. The current root also
  names two content blobs for the file. A blob id is the SHA-256 of the file
  bytes, so these are explicit native hashes. They must equal the computed pair.
- *Final after R2.* The R2 turn blob lists the edit step, the final test step
  and the final response step in this order, by blob id.

**Model identity: measured.** Each JSON assistant message names its model
(`cursor-grok-4.5-high`). The stream names only the route (`Auto`) once per
process and no model for a response, so the native identity is accepted. The
route is not in the store; it is in `cli-config.json`, which the decoder does not
read. The configuration of a response is its model name.

**Usage, token semantics, reconciliation: `native_absent`.** The stream reports
input, output and cache counts per turn. The family holds none: no key of any
JSON message, of the `meta` row, of the sidecar or of the transcript names a
token count, and the field maps cover every protobuf field. Root field 5 is an
estimate of the context size, not a usage record. No session or turn total
exists. The root is not complete, so the rows rest on the second rubric path:
the complete id-named family plus the bound rows of the shared store that names
the session (see *Outside the two directories*).

**Complete root: contradiction (0). Companions: measured.** The standard is the
one of Codex CLI and Antigravity: rows of the session live in a shared store
that holds other sessions and cannot be copied whole. The family is complete and
hash-bound; the companions are `meta.json` and the transcript.

**Thread structure.** Parents are explicit: a turn blob names its user message
and its steps.

**Standard tools readable: fails (0).** The container is SQLite and the message
blobs are JSON. But the root, the turns and the steps are protobuf: without a
protobuf wire reader a reader has no order, no time, no call record and no exact
prompt. Antigravity fails the same assertion for protobuf bodies in SQLite.

**Self-contained identity.** Session id from the `meta` row, harness from the
`providerOptions.cursor` namespace of the JSON messages, surface from root
field 22 (`cli`).

**Format version.** `meta.json` declares `schemaVersion` 1 and the store declares
`PRAGMA user_version` 1. The declared version is both. The decoder contract names
both and refuses another value. Judgment: both are integer schema versions
separate from the build. No version covers the protobuf field layout. Only one
value of each was observed.

**Schema stability.** All blobs of the three runs decode under the contract.

**Event timestamps 13/13.** Unix milliseconds from the record of each event: the
creation time of the user message, the time of the text step, the start time of
the tool step (action), its end time (result and changed file).

**Root location.** Three rows, one per run. The store directory is
`chats/<MD5 of the workspace path>/<session-id>/` and the transcript directory is
`projects/<workspace path with "/" as "-">/agent-transcripts/<session-id>/`. The
validator derives both from the plan and the stream session id and checks them
against the inventory of each run.

**Duplicate safety.** The read is the store, then the transcript. Store rows are
read in rowid order. Every prompt, visible text, call and result is stated at
least twice in the store (turn tree and message list); a pending message in an
older root and the first file content blob add statements; the transcript states
each prompt, text and call once more (bound by content, it has no ids). Older
checkpoint roots count: they are rows of the table. No field marks a statement
as superseded. Only the thinking steps are stated once (the message list holds
them encrypted): 6 of 23 events with 63 statements, 7 of 24 with 64, 8 of 27
with 71.

**Density.** One row or one transcript line is one record. The first record that
states an event keeps its role; a later one is a `snapshot`. A root with a
pending message has the role of that message, so a call that such a root states
first is useful there (set v1 left such a root unclassified; that was an error
against the first-record rule). The system message, the injected context message
(a user-role JSON message of about 45 KB that is in no turn), roots without a
pending message and turn blobs are unclassified. Useful share: 0.253, 0.265,
0.272. If the injected context message counted as a prompt, as Codex counts
its context message, density would be about one point higher. It is kept
unclassified: the turn tree is the native mark that it is not a prompt.

## Score

78.0, 78.1, 78.2 per run. Not at full credit: usage (0 of 5), token semantics
(0 of 4), reconciliation (0 of 3), complete root (0 of 3), standard tools
readable (0 of 2), duplicate safety (about 0.3 of 3), density (about 0.8 of 3).

## Public packets

`scripts/sanitize_cursor_cli_score_packets.py`. Changed at equal byte length: the
home user name in files and file names; the MD5 directory name (recomputed from
the public workspace path); the hashes of the two harness files; in the store the
system prompt, the injected context message (the fragment `OS Version: darwin`
is kept), the five short digests of `systemPromptFingerprint` (digests of the
private prompt, rules and MCP setup; a short digest of guessable text can
confirm a guess), the opaque token of each turn and `blobEncryptionKey`; the name
of an operator plugin that the model repeated in its thinking (runs 1 and 2), in
the store and in the stream copies. In the shared-store extract the home path is
aliased, and the `hash` values and the row ids are zeroed at equal length: the
hash is a short digest with an unknown preimage that may cover the private path,
and a row id shows how many rows the operator's store held. The validator needs
neither value. It requires the real form in a private packet and the zeroed form
in a public packet (a packet with `public-transformation.json`). A last guard of
the sanitizer fails on any hex value of 6 to 63 characters under a digest-like
key of public JSON that public bytes do not yield.

Kept public: session, request, message and call ids; encrypted reasoning
signatures; the sizes of the operator's rules, skills, MCP and subagent text in
root field 5 (numbers, not text); the time zone; repository and branch name.

A changed blob has a new id, so every blob that names it changes too, up to the
root and the `meta` row. The script computes the new ids to a fixed point and
builds a new SQLite file with stock SQLite: the same page size, schema text,
`user_version` and journal mode, every row at its old rowid, filled with zeroed
free pages to the private byte length. It requires the same classes and the same
references as the private graph under the id map, and that no id of a changed
private blob remains in any public file. A reader can verify the public file,
each id against its bytes and every reference. A reader cannot verify that the
private graph had the same shape. The page layout of the public store is new.

## Build

```
python3 scripts/build_cursor_cli_score_replays.py --output artifacts/v1-expanded-preparation/cursor-cli-score-replay-v3
python3 scripts/sanitize_cursor_cli_score_packets.py --source artifacts/v1-expanded-preparation/cursor-cli-score-replay-v3 \
  --output artifacts/v1-expanded-preparation/cursor-cli-public-candidates-v3
```

Never publish `cursor-cli-score-replay-v1`, `-v2`, `-v3`,
`cursor-cli-public-candidates-v1`, `-v2` or the sidecars beside the public
packets (`summary.json`, `*-receipt.json`, `*-private-transformation.json`).

## Open judgment calls

- The model of a response comes from a second statement of that response, bound
  by position and equal text, not by a key.
- The shared-store rows were read after the captures and are bound by content,
  not by a capture-time hash.
- The shared home was checked by metadata after the fact; places outside it were
  never listed (see *Outside the two directories*).
- The emptiness of the two directories at the start rests on the controller code
  and on birth times; the two harness files are rewritten in turn 2, so their
  birth times say nothing about turn 1.
- The injected context message is not counted as a prompt and not as useful
  content.
- The transcript is in the read because the absence scan opens it.
- The version rows rest on one observed value of each version.

## Cursor Desktop (not yet scored)

This section is older text about the Desktop launch plan and its first captures. Cursor
Desktop has no score replay and no packet set. Nothing here is a result of the CLI row.

`preflight_desktop(run_root, workspace, identity_probe=...)` emits a safe launch
plan without opening Cursor:

```text
Cursor --user-data-dir <run_root>/cursor-desktop-user-data \
       --extensions-dir <run_root>/cursor-desktop-extensions <workspace>
```

The Desktop plan is always `rankable=False`. With no independent evidence it is
blocked. A caller may supply `DesktopObservation` only after an actual GUI
observer has recorded accessibility text, reviewed the visual output, and
established the complete native transcript-root set. The resulting state can be
`ready_for_calibration`, but it
still cannot become a ranked result in this adapter. `require_desktop_calibration_ready`
raises until every required observation is present, and
`reject_desktop_ranked_result` is an explicit guard against treating readiness
as a score.

The short-root preflight launched a separate Cursor process with `/tmp/sbcd4/ud` and
`/tmp/sbcd4/ext`, and Computer Use bound to its `project` window. The original auth
failure was `N/A`. After sign-in, the clean `cal-6` run preserved two turns and one
fresh project-keyed transcript. It also exposed a contract gap: Cursor writes that
transcript under `~/.cursor/projects/<project-key>/agent-transcripts/`, outside the
isolated `--user-data-dir`. Session-related SQLite rows occur inside the isolated root.
The selected session-keyed SQLite companion adds the tool-result bubbles absent from
the transcript-only decode. In `eval-1`, a copied transcript and selected companion
recovered two responses, eight actions, eight results, and ten relations; an offline
replay with original roots and network denied matched the ordinary semantic digest.
The closure scan found six more `agentKv`/`inlineDiff` session-bearing rows, retained
privately with a public hash/size inventory. They are not yet semantically qualified or
included in the copied public bundle. The current preflight guard still rejects this
split-root layout. `eval-2` hit the Cursor Free usage limit before an R1 response and is
invalid/N/A, not a persistence failure. The later Cursor CLI repetitions used a separate
command surface and fresh isolated data roots; they do not qualify or alter this Desktop
capture.

Desktop accessibility/visual evidence and native roots belong to separate
capture/evaluation stages. The adapter never scans or copies them. A missing
root, an unobserved GUI, or an incomplete root declaration remains blocked rather
than becoming a zero. The transcript-plus-companion decoder does not remove that guard.

## Surface and evidence boundaries

`cursor-cli` and `cursor-desktop` have distinct identity values, launch plans,
isolation roots, and observer methods. `assert_surface_separation` rejects
shared native roots or a mismatched identity. The adapter does not import the
pending `session_bench.surface_capture` module; a future integration can pass
these small plan/observation objects into that capture layer.

The checked-in tests use temporary paths, fake identity mappings, and fake
JSONL. They do not invoke `agent`, open Cursor, inspect a normal user-data
directory, or handle credentials.

## Cursor Desktop capture state, 2026-10-06 (paused)

The Desktop row is not scored. An isolated profile exists at `artifacts/cdp/`
(`ud` = user-data-dir, `ext` = extensions-dir; ignored by git; signed in by the
owner). `scripts/cursor_desktop_capture.py before|after --attempt-id <id>`
brackets one run: it prepares a workspace named `SESSION-BENCH-TEST`, snapshots
the whole isolated profile before and after with Cursor at rest, inventories the
shared `~/.cursor` root by metadata only, and copies the files Cursor wrote for
this workspace under `~/.cursor/projects/<project-key>/`.

Two attempts were made and neither delivered a valid run:

- `cursor-desktop-2026-10-06-r1`: void. The prompt was pasted into a window of
  the normal profile (old run `cursor-desktop-eval-3`, same folder name).
- `cursor-desktop-2026-10-06-r2`: stopped. The account is on the Free plan and
  reached its usage limit. Only `Cursor Grok 4.6 Medium` is offered on that plan.

To resume: wait for the limit to reset (or change the plan), start a new attempt
id, open the workspace with the isolated profile, and have the operator paste
each prompt. The desktop automation tools grant an IDE click-only access, so the
agent cannot type or paste into Cursor.
