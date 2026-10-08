# Antigravity CLI native capture contract

Scope: Google Antigravity CLI (`agy`) 1.2.17, macOS, headless (`-p`,
`--output-format stream-json`). Requested model `claude-sonnet-4-6` with `--model`,
no effort flag, no sandbox flag (owner decision of 2026-10-05). Three runs on
2026-10-05: `antigravity-2026-10-05-02`, `-03`, `-04` (repetitions 1, 2, 3). The
runs used the operator's normal profile. Packet set v4. Earlier sets: v2 left two
rows open before the shared-store rows were read; v3 was rejected for three
small defects (a duplicate over-count, the wrong build in this document, one
link-target hash).

Build: the three scored runs are build 1.2.17 (`capture-start.json`,
`capture-result.json`, packet context). The CLI updated itself from 1.2.16 to
1.2.17 during the stopped attempt `-01` of 2026-10-05, which recorded 1.2.16. In
run 1 the updater still rewrote `bin/webm_encoder`. The attempts of 2026-10-04
were build 1.2.16.

## Runs and disclosed attempts

The designated runs are the first three captures that the whole-state controller
completed. This was decided before any score was computed.

- 2026-10-04, `-01` to `-05`: the capture listed `brain/` only and passed
  `--sandbox`. Packet set v1 (`antigravity-public-candidates-v1`, runs `-02`, `-03`,
  `-04`) was rejected by the independent review
  (`antigravity-independent-public-review-v1/rejection.md`): the CLI keeps the
  conversation in `conversations/<id>.db`, which was not captured, so the root was
  not complete and eight absence rows were unproven. These packets must not be
  published.
- 2026-10-05, `-01`: turn 1 ran, then the capture stopped closed. The vendor touched
  the empty directory `crashes/` and the controller had no rule for it. A
  controller gap, not a result.
- 2026-10-05, `-02`, `-03`, `-04`: valid. The helper ledger is clean (inspect 0,
  baseline 1, final 0, no retry).

## Capture scope (receipt `antigravity-state-root-v1`)

The controller lists the whole state directory `~/.gemini/antigravity-cli/` by
metadata before the first turn and after each turn. It opens no old file and
follows no link. After a turn it waits for two equal listings. Every entry that is
new, changed or removed must fall into one class (`classify_state_changes` in
`session_bench/antigravity_root_evidence.py`):

- **Session-owned.** A file whose path carries the new conversation id. Copied
  byte for byte with size, SHA-256 and inode identity. SQLite files are copied as
  bytes and never opened in place.
- **Run-owned.** A new `log/cli-*.log` or `implicit/<uuid>.pb` born during the
  capture. Copied, `private_only`.
- **Shared store.** An old file or link outside the per-conversation areas whose
  metadata changed, a sidecar beside it, or an old empty directory that was only
  touched. Path, kind, size and a metadata digest are recorded. The content is not
  read: it can hold other conversations.
- **Directory.** Only a container.

Anything else stops the capture.

## What each captured file holds

| File | Content | Session content? |
|---|---|---|
| `conversations/<id>.db` | SQLite, `user_version` 1. Every step with times, tool call ids, usage, response ids; one row per model call with the model name; the request configuration; workspace and repository of the conversation. | Yes. The primary record. |
| `brain/<id>/.system_generated/logs/transcript.jsonl`, `transcript_full.jsonl`, `chunks/…` | The steps again as JSON lines, without call ids, model, or sub-second times. | A mirror. |
| `brain/<id>/.system_generated/steps/<n>/output.txt` | The result text of step `<n>` again. | A mirror. |
| `brain/<id>/.system_generated/messages/*.json`, `read.json` | The system notice of turn 2 again; a read flag. | Mirror; metadata. |
| `annotations/<id>.pbtxt` | One line: a generated title. | Derived index. |
| `presence/<id>.lock` | Empty. | No. |
| `log/cli-*.log` (run-owned) | Process log of each `agy` process. It names the conversation id and the model and holds the account e-mail. | No record of steps. Private; not in packets. |
| `implicit/<uuid>.pb` (run-owned) | About 635 bytes, not readable as protobuf or text. | Unknown. Private; not in packets. |

## Primary record: the conversation database

The primary read is `conversations/<id>.db`. Reasons: it is the only container
with the keys a reader needs (tool call id, response id, model, usage), the stream
is built from it, and the second process resumes from it. The decoder reads only
this file. The files under `brain/<id>/` are mirrors. The mirror is proved by
content: every transcript line must state the same text, call and result as the
database step with the same index (`mirror_differences`).

Tables: `trajectory_meta` (one row; `cascade_id` is the conversation id), `steps`
(`idx`, `step_type`, `status`, `step_format`, `metadata`, `step_payload`),
`gen_metadata` (one row per model call), `executor_metadata`,
`trajectory_metadata_blob`, `parent_references`, `battle_mode_infos`. The BLOB
columns hold protobuf messages. The vendor publishes no schema. Fields are named
here by number:

- step `metadata`: 1 = creation time (1 seconds, 2 nanoseconds, UTC); 4 = the tool
  call a tool step answers (1 call id, 2 name, 3 JSON arguments); 9 = usage of a
  model step (2 input, 3 output, 5 cache-read, 7 response id, 11 provider request
  id).
- `step_payload`: `step_type` 14 user input (19.2 text); 15 model step (20.1 text,
  20.3 thinking, 20.6 response id, 20.7 tool calls: 1 id, 2 name, 3 JSON
  arguments); 132 tool step (140.2.1 result text); 101 system message (114.4.4
  text).
- `gen_metadata.data`: 1.4.7 response id; 1.19 model name; 1.20 labels (1 key,
  2 value; `model_enum`); 1.2 the messages sent to the model (18 step index,
  2 role, 3 text, 11 thinking text, 6 tool call, 7 tool call id, 4 an estimated
  size of the message); 1.9.10 context-size estimates; 1.1 system prompt; 1.8 tool
  definitions; 1.16 prompt sections (1 name, 2 body).

Joins: a tool step names its call (`metadata` 4.1 = the id in 20.7.1 of a model
step). A `gen_metadata` row names its response (1.4.7 = 20.6). A step belongs to
the latest user step before it. `steps.idx` equals the step index of the stream.

Decoder contract `antigravity-conversation-db-v1`
(`session_bench/antigravity_conversation_db.py`,
`decode_antigravity_conversation`): `user_version` 1, the seven tables, step types
14, 15, 101 and 132, `status` 3 and `step_format` 0. Anything else makes the
decode unsupported.

## How each group of metrics is decided

**Work, causal, revision.** The observer is the stdout stream of each turn, the
helper ledger and the file hashes. The observer response is the text of the
streamed step that holds the canary, without the one line feed the stream adds.

- *Actions 4/4.* The stream and the database share no call id. Rule for unscored
  native actions: in one turn, for one group of native calls with equal name,
  arguments and target, the observer saw n scored and m unscored actions and the
  database holds k. min(k, n+m) are paired; a call paired with an unscored action
  (`ls`, `view_file`) is outside the population; only calls above n+m are
  duplicates. The fresh runs have no duplicate.
- *Results 4/4.* The observer names the stream step, which is `steps.idx`. Status,
  exit code, helper nonce and output must agree. Output transform: the text after
  `Output:`, without the one line feed of the result template. A completed edit is
  a success with exit code 0 on both sides.
- *Action-result relation 4/4.* From the native call id only. A call id that names
  two calls gives no relation.
- *Changed file.* Pre-image: a native complete file read (line and byte counts
  checked). Post-image: that text with the native whole-file diff of the replace
  result. A `write_to_file` edit uses the one write call and its native completion.
- *Final after R2.* Rising `steps.idx` of R2, edit call, edit completion, final
  call, final result (exit 0) and final response inside the R2 turn.
- No compound shell call occurred.

**Model identity: measured.** `gen_metadata` names the model (`claude-sonnet-4-6`)
and the label `model_enum` for each response id. The observer reports no model, so
the native identity is accepted.

**Usage: measured.** The model step holds input, output and cache-read counts on
the response record.

**Token semantics: `native_absent` (0 of 2, 2 decoded).** The usage record holds no
cache-write key. A protobuf writer drops a zero, so a missing key is not a zero.
No other row of the database and no shared-store row of the conversation holds
one. The usage record stays in the decoded population.

**Reconciliation: `native_absent`.** No file of the family and no shared-store row
of the conversation holds a turn or session total. `gen_metadata` 1.9.10 and 1.2.4
are estimates of the context size and of each message, not usage totals; they
equal no sum of the usage counts.

**Complete root: contradiction (0). Companions: measured.** The standard is the
one of the ranked rows. Codex CLI is a contradiction because rows of the thread
live in shared SQLite stores outside the copied sessions root. Copilot is
measured because its store sat in an isolated home and was copied whole. Here
derived summary rows of the conversation live in shared stores that also hold the
operator's other conversations. Those stores cannot be copied whole and were not
hash-bound at capture time. So the root is not complete: the Codex case. The
family (every file whose path carries the conversation id) is complete and
hash-bound; the companion set is every family file except the database, as for
Codex.

Shared stores that changed during each run: `conversation_summaries.db`,
`jetbox_summaries_proto.pb`, `cache/last_conversations.json`,
`cache/onboarding.json`, the link `cli.log`, `bin/agentapi`, `bin/webm_encoder`
(run 1), and the touched `crashes/`.

### Shared-store rows of the conversation

On 2026-10-05 the owner allowed reading only the rows of the three test
conversations. The coordinator read them from the live stores after the captures
and saved them beside each capture (`shared-store-extract/extract.json`). They are
not hash-bound to the capture-time metadata. Rows of other conversations were not
exported. Each packet carries the extract in canonical form
(`inputs/capture/shared-store-extract.json`), bound by the capture assertion.

What was read:

- `conversation_summaries.db` (`user_version` 3), table `conversation_summaries`,
  the one row of the conversation. Columns: `conversation_id`, `title`, `preview`,
  `step_count`, `last_modified_time`, `workspace_uris`, `status`, `source`,
  `project_id`, `agent_name`, `parent_conversation_id`, `nesting_depth`, `battle_id`,
  `winning_conversation_id`, `not_fully_idle`, `killed`, `last_user_input_time`,
  `last_user_input_step_index`, `app_data_dir`, `raw_summary` (protobuf), `group_id`.
- `jetbox_summaries_proto.pb`: the one entry with the conversation id (field 1 = the
  id, field 2 = the same summary message).

Summary message, every field in all three runs (29 leaves; path, wire kind,
meaning): 1 bytes title; 2 varint step count; 3.1, 3.2 varint last modified
(seconds, nanoseconds); 4 bytes trajectory id; 5 varint status enum; 7.1, 7.2
varint created; 9.1 bytes workspace URI; 9.2 bytes repository root URI; 9.3.1
bytes repository name; 9.3.2 bytes repository URL; 9.4 bytes branch; 10.1, 10.2
varint last user input; 15.1 bytes title; 16 varint step index of the last user
input; 17.1.1, 17.1.2, 17.1.3.1, 17.1.3.2, 17.1.4 the workspace block again;
17.2.1, 17.2.2 varint created; 17.3 bytes workspace id; 17.6 bytes conversation
id; 17.7 bytes workspace URI; 17.18 bytes project id; 22 varint trajectory type
enum.

What it showed: a derived summary. No token count of any kind (no total, no
cache-write), no model name, no call id, no format version of the conversation.

How the packet uses it (`shared_store_findings`): the absence is accepted only
when the extract names this conversation, the row has exactly the columns above,
every protobuf field is in the map above with the listed wire kind, and the
summary describes the captured database (conversation id, trajectory id, step
count, last user step). An unknown column or field could be a count; then both
rows stay `unresolved`.

Not read: the entry of `cache/last_conversations.json` (a list of recent
conversations that mentions each id), `cache/onboarding.json`, the binaries and
the log link.

**Thread structure: measured** by the linear recovery rule; the session id is
`trajectory_meta.cascade_id`.

**Standard-tools readable: 0.** The container is SQLite, but each record body is a
protobuf message without a published schema. A reader needs a wire-format decoder
and the field map above.

**Self-contained identity: measured.** Session: `cascade_id`. Harness: the prompt
section `identity` of a model call says "You are Antigravity". Surface: a tool step
stores the location of its output file under `antigravity-cli/brain/<id>/`.

**Format version, honest version signal: measured.** `PRAGMA user_version` is 1 and
`steps.step_format` is 0. The decoder refuses another `user_version`. Judgment:
only one version was observed.

**Schema stability: measured** for build 1.2.17 on 2026-10-05, mirrors included.

**Event timestamps: 13/13.** Each event takes the seconds of field 1 of its own
step row (Unix seconds, UTC).

**Root location: measured.** One row per run from the whole-state receipt.

**Naive-reader duplicate safety: 0.** The read is the database. A statement of an
event in any row is one occurrence. The last `gen_metadata` row holds a copy of
every message sent to the model, and that copy covers every step. So a user
message, a model message (text, or thinking text in field 11 of the copy) and a
result are stated in their step and in the copy; a tool call is stated in the model
step, in the tool step and in the copy. No event has one occurrence: 0 of 16 with
37 occurrences in run 1, 0 of 20 with 47 in runs 2 and 3. The system notice of
turn 2 is injected context that `step_type` 101 marks. It is not an event, in its
own row or in the copy. The transcript files are outside the read and are not
occurrences.

**Content density.** Every family file is in scope. A database row is one record:
compact JSON of its plain columns plus the raw length of its BLOBs. Roles: user
step, model step, tool step are useful; system step, `gen_metadata` and
`executor_metadata` rows are metadata; a `gen_metadata` row with the message copy
and every proved mirror file are `snapshot`; the annotation is `index`.

Result in all three runs: 26 measured, 4 `native_absent`, 1 contradiction. All 31
metrics are resolved. Score 82.7.

## Public derivative

`scripts/sanitize_antigravity_score_packets.py` builds the public packets. Changed,
at equal byte length:

- the home user name in every file, also where a stream text delta cuts it;
- the account name of the workspace repository, in every file;
- in the database: the two aliases; the installation-level session number and the
  opaque 404-byte client blob (equal in all runs); the system prompt, the tool
  definitions and every prompt section except `identity`; the request
  configuration (in `gen_metadata` and in each user step); all of
  `executor_metadata`. BLOBs are rewritten in place on a
  private copy, so every page stays where it was. Then every byte outside a live
  structure is set to zero: free pages, free blocks, the gap between the cell
  pointers and the cells, and the tail of the last overflow page of a value
  (`scrub_free_space`). Older copies of rows lived there. The file keeps its size
  and its page count. Every row is compared with the intended result;
- in the two inventories: every entry that did not change gets aliased names and
  blanked size and times; classified entries keep their names. The hash of every
  link target is zeroed (a target is a log file name with a time, which is easy
  to guess);
- in the shared-store extract: the two aliases, also inside its hex-encoded
  protobuf values. The title, times, branch, repository name, workspace id and
  project id stay; the database holds the same values;
- in the receipt: inventory digests and the metadata digests of the shared
  stores are recomputed from the public inventories; digests of the run-owned
  private files are zeroed.

Not in the packets: the run-owned logs and the implicit file.

Kept: every row and every decoded key; tool call ids, response ids and provider
request ids; conversation, trajectory and workspace ids; the thinking signatures;
the stream tool list; the branch name of the repository.

The script replays each packet before and after and stops when a metric row
differs. Private pins stay in `*-private-transformation.json` beside the packets.

No capture-time identity, public-safety approval, independent reproduction or
publication is inferred from successful offline decoding.
