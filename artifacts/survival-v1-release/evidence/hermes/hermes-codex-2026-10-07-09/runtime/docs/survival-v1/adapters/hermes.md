# Hermes

Scope: Hermes Agent `v0.21.5+4668.gdccb84b.dirty (2026.9.24)`, macOS,
single-query mode (`hermes chat -q … --format stream-json`), provider
`openai-codex`, model `gpt-5.5`, the operator's normal Hermes home. Controller:
`session_bench/hermes_survival_capture.py`. Observer:
`session_bench/hermes_stream_observer.py`. Decoder contract:
`hermes-state-db-session-rows-v1` (`session_bench/hermes_store_rows.py`).

**Status.** All 31 metrics are resolved in the three runs of 2026-10-07 (packet
sets `hermes-score-replay-v3`, `hermes-public-candidates-v3`). The sets `-v1` and
`-v2` were built from the captures of 2026-10-06; `-v2` was rejected by the
independent review (`hermes-independent-public-review-v2/rejection.md`). Both are
superseded.

## Launch

The controller prepares the frozen two-turn workload offline. It starts Hermes
through the pinned Hermes Python runtime and `hermes_cli.main` (`runpy`), not
through the shell launcher, and never sets `HERMES_HOME`. The route is the
normal-home `openai-codex` OAuth provider with an explicit provider and model.
Each turn is one process:

```
<hermes python> -I -B -c <runpy entry with the path of hermes-agent> chat \
  --ignore-user-config --ignore-rules --no-restore-cwd --in <scratch>/workspace \
  --provider openai-codex --model gpt-5.5 --toolsets terminal,file --yolo \
  --format stream-json [--resume <session id of turn 1>] -q <prompt>
```

- **Toolsets `terminal,file`.** `terminal` is `terminal` and `process_manage`;
  `file` is `read_file`, `write_file`, `patch` and `search_files` (Hermes source
  `toolsets.py`). This is what a coding session needs for the task. The web,
  browser, delegation, memory, skills, cron and messaging toolsets stay off: they
  reach the network, other projects or the operator's stores, and the task needs
  none of them. The other rows are not restricted more than this: Copilot runs
  with `--allow-all-tools`, Codex with `workspace-write`, Antigravity without
  `--sandbox`, and each has its file edit tool.
- **`--yolo`.** A single-query run has no user to answer an approval prompt, and
  the approval gate then denies by default. `--yolo` is the documented flag that
  answers it. The earlier `-z` mode set the same switch itself.
- **`--format stream-json`.** Stdout is one JSON object per line: the tool calls,
  the tool results and the final result of the turn. It is the independent record
  of the tool events (see *Observer*).
- **Session id.** From the `system`/`init` event and the `result` event of the
  stream. The second turn resumes that id and must report it again.
  `--usage-file` has no effect outside `-z` and is not used.

- **Entry code.** The `-c` code puts the Hermes source root on `sys.path` and
  runs `hermes_cli.main`. The root is written into the code; `sys.argv` holds only
  the Hermes arguments and the code does not change it. Reason: on import,
  `hermes_bootstrap` can re-execute the process under Hermes' managed interpreter
  (`venv_sync.prepare_launch`, `relaunch_command`). It sets `sys.argv` to the argv
  of the first process and runs the same `-c` code again. The earlier entry code
  popped the root from `sys.argv`; run a second time, it popped one Hermes
  argument more.

Any auth, quota, route, timeout, stream, session identity, canary, native state,
exporter or helper-ledger failure stops the attempt. There is no retry and no
fallback. `python3 scripts/capture_hermes_survival.py <attempt> --repetition N
--print-argv` prints the argv of both turns and starts nothing.

## Observer

The observer is built from the stdout stream of each turn, the helper ledger
that the frozen helper writes itself, and the SHA-256 of the workload file
before and after. It never reads a native row.

Stream events (Hermes source `hermes_cli/stream_json.py`; each has `type` and
`timestamp` in Unix milliseconds):

| `type` | Fields |
|---|---|
| `system` (`subtype` `init`) | `model`, `session_id`. First line. |
| `text` | `text`: one delta of model output. |
| `tool_use` | `name`, `input` (the arguments). `tool_call_id` only when the agent passes one; the tool executor of this build does not. |
| `tool_result` | `name`, `output` (text, cut after 5000 characters), `duration_ms`, `is_error`. `tool_call_id` as above. |
| `result` | `session_id`, `exit_code`, `text` (final response), `tokens` (turn totals), `duration_ms`, `error` on failure. Last line. |

What the observer sees: each tool call with its name and arguments; each tool
result with its output and error flag; for the `terminal` tool the exit code
(the result text is JSON with `output` and `exit_code`); the final response; the
model of the process.

What it does not see, and what it does then:

- No call id. A result is paired with its call by order. Two open calls without
  ids stop the observer. Its action ids are stream positions (`r1-call-1`), so
  the join with a native call is by name, arguments and turn, not by id.
- A result longer than 5000 characters is cut and is no longer JSON. For a
  terminal call that holds a scored helper the observer then stops: the exit code
  is not observed.
- No model per response and no usage per request.
- The result of a file tool is opaque text. Its status is the `is_error` flag.

Scored actions: a frozen helper invocation, and the edit of the workload file by
`write_file` or `patch`. A terminal call with two or more scored segments is
observed as one action per segment; the helper ledger gives the exit code and
the line, and the line must be in the stream output of the call. When several
calls edit the file, the last successful one is the scored edit. Every other
call is an unscored action. An edit by a script inside a shell call is not an
observed edit action; the observer then stops, because the four scored actions
are not all observed.

## Attempt `hermes-codex-2026-10-07-07` (stopped before any model request)

The first attempt with the new launch. The controller counts a submission when
it starts the turn process, so its result says "1 model submissions". No request
reached the model: stdout is empty (not even the `init` event), the exit code is
2, and stderr is the argument parser's message that `stream-json` is not a
command. Cause: the entry code of that attempt popped the source root from
`sys.argv`. Hermes' bootstrap re-executed the process and ran the code again; the
second pop removed `chat`. The top-level parser then read `stream-json` as the
subcommand. The entry code is fixed (see *Launch*). The attempt is not used.

The same re-execution can have happened in the `-z` captures. There the popped
argument was `--ignore-user-config`. The controller also sets
`HERMES_IGNORE_USER_CONFIG=1` in the environment, which Hermes reads for the
same bypass (`hermes_cli/config.py`), so the user configuration was ignored
either way. The retained evidence does not show whether the re-execution
happened in those runs.

## Captures of 2026-10-06 (superseded)

`hermes-codex-2026-10-06-04`, `-05` and `-06` ran `hermes -z` with
`--toolsets terminal`. Two controller faults follow from that:

- The file tools of Hermes were off. The model could change the file only by a
  script inside a shell call. The packets then scored "no edit action", "no
  changed file" and "no final-after chain". That showed the controller's
  restriction, not the format.
- `-z` prints only the final text. The observer took the edit from workspace
  copies and the helpers from the ledger. That is not an observation of tool
  events, which the Tier A rows need.

What these captures did show stays valid as format knowledge: the native record
is the rows of the session in `state.db`; the schema version is 31; the system
prompt sits in `system_prompts` and is named by its SHA-256; the store cannot be
copied whole. One finding of the review corrected the text of that time: the session row
holds a usage record of one request (`_usage_anchor`, see *Metrics*). The earlier
statement that no such record exists was wrong.

## Capture scope (receipt `hermes-state-root-v1`)

A Hermes session has no file of its own. Its native record is its rows in the
shared store `~/.hermes/state.db`.

The controller lists the whole Hermes home by metadata before the first turn and
after each turn: path, kind, size, times, inode. It opens no old file and follows
no link. After a turn it waits for two equal listings. Every entry that is new,
changed or removed must fall into one class (`classify_hermes_changes` in
`session_bench/hermes_state_evidence.py`):

- **Session-owned.** A file whose path carries the session id. Copied.
- **Run-owned.** A new file under `logs/` born during the capture. Copied,
  private only.
- **Shared.** An old file or link that changed, a `-wal`, `-shm` or `-journal`
  file beside it, a lock, pid or marker file that appeared or disappeared, an old
  log under a new name, or an old directory that was only touched. Metadata
  before and after is recorded. The content is not read.
- **Digested subtree.** `hermes-agent/`, `tools/`, `installs/`, `cache/`,
  `audio_cache/`, `image_cache/` are walked completely, but each is one entry
  with a digest of all entries inside. A change inside is a shared change and is
  reported by path.
- **Directory.** Only a container.

Anything else stops the capture with a refusal listing. The rule table is `RULES`
in that module. Each receipt stores the table it used.

### Rows of the session in `state.db`

The owner permits (2026-10-06) reading only the rows of the test session from the
store, never the rows of another session. After each turn the controller copies
`state.db` and its sidecars as bytes to a private temporary directory, opens the
copy read-only, and exports:

1. every row, in every table, with a column whose value is exactly the session id;
2. one level of rows that reference such a row by a declared foreign key, or by a
   column named `<table>_id` whose values all exist as keys of that table.

It writes the rows to `r{N}-native/session-store-rows.json` (column name → value,
BLOB as hex, rowid and keys kept) and the schema of the store (pragmas and
`sqlite_master`, no row) to `r{N}-native/session-store-schema.json`. It then
deletes the copy. For other sessions only row counts are read. Per table the
receipt also counts the rows that hold the id anywhere inside a value.

## The native family and the read

Decided before scoring.

**Family.** The exported rows of the session (one `sessions` row, two
`session_model_usage` rows, 12 `messages` rows; 16 in run 2) and the one
`system_prompts` row that the session row names. In all three runs the receipts
show:

- no session-owned file and no run-owned file anywhere in the home;
- in every table the count of rows that hold the id anywhere equals the count of
  exact matches. So no unread row names the session inside a longer value;
- every table was scanned and no join candidate was left out
  (`compression_locks.session_id` was followed and has no row).

**Read** (containers the decoder takes a scored fact from): the tables
`sessions`, `messages`, `session_model_usage` and `system_prompts`. One row is
one record. From the prompt row the decoder takes one fact: the name of the
harness in its first sentence.

**Outside the read.**

- The file of `hermes sessions export`. It is a projection made by Hermes' own
  exporter. It is kept in the capture directory only to cross-check the rows. It
  is not in the packets.
- The search index (`messages_fts`, `messages_fts_trigram` and their shadow
  tables). The triggers in the bound schema copy the content, the tool name and
  the tool calls of each message into both indexes. The index rows are keyed by
  the message row id, not by the session id. The decoder never opens them and
  they were not read. They are outside duplicate safety, as the membership rule
  says, and outside density (see *Limits*).

## Later reads

Made after the captures, by owner permission (store metadata and rows of the
test sessions only), each from a private temporary copy of the live store that
was deleted. They are bound by content, not by a file hash of the capture.

1. **The row of `schema_version`**: one row, `version` 31
   (`hermes-store-version-extract-v1/state-db-schema-version.json`, made by
   `scripts/extract_hermes_store_version.py` on 2026-10-06, checked again on
   2026-10-07). It is in each packet as a capture document. The packet validator
   accepts it only when its schema digest (pragmas and `sqlite_master` rows)
   equals the schema digest of the capture; it does in the three runs. The
   decoder accepts only the value 31. Limit: a schema migration between a capture
   and the read cannot be excluded, except by this digest equality. A migration
   that changed the number without changing any table, index or trigger would
   not show.
2. **The `system_prompts` row of each session** (one per run; 9,773 characters).
   It is in the native family as `capture/system-prompt-row.json`. The validator
   and the decoder accept it only when its `hash` equals
   `sessions.system_prompt_hash` of the captured session row and is the SHA-256 of
   its `prompt`. This holds in the three runs.
3. **The six rows of `state_meta`**
   (`hermes-store-version-extract-v1/state-meta-rows.json`). Keys:
   `ghost_session_prune_v1`, `orphaned_compression_finalize_v1`,
   `fts_optimize_available`, `fts_tool_full_content_high_water`,
   `db_file_generation`, `message_uid_backfill`. No key names the harness or a
   version, and no metric reads them. They are not in the packets:
   `db_file_generation` identifies the operator's store.

## What the rows hold

| Table | Columns used | Meaning |
|---|---|---|
| `sessions` | `id`, `source`, `model`, `billing_provider`, `model_config`, `started_at`, token and cost columns, `system_prompt_hash` | Identity of the session. `source` is the entry point (`oneshot`). The token columns are totals of all requests. `model_config` is JSON; its `_usage_anchor` is the usage record of the last request. `system_prompt` is NULL. |
| `messages` | `id`, `session_id`, `role`, `content`, `tool_calls`, `tool_call_id`, `tool_name`, `timestamp`, `finish_reason`, `reasoning`, `active`, `compacted` | One row per prompt (`user`), per model message (`assistant`) and per tool result (`tool`). `id` is the order of writing. `timestamp` is Unix seconds as a real number, UTC. `tool_calls` is a JSON list; a call has an id, a name and JSON arguments. A `tool` row names its call by `tool_call_id`. The result of `terminal` is JSON with `output` and `exit_code`; of `patch`, JSON with `success` and a unified `diff`; of `read_file`, JSON with numbered `content`. `token_count` is NULL. |
| `session_model_usage` | `session_id`, `model`, `task`, `api_call_count`, the five token columns | One row per model and task (the main task and `title_generation`). Totals of all requests. |
| `system_prompts` | `hash`, `prompt` | One row per distinct system prompt. `hash` is the SHA-256 of `prompt`. The first sentence is `You are Hermes Agent, built by Nous Research.` |

Every value a fact needs is an INTEGER, a REAL or TEXT (JSON text for calls,
results and the request configuration). The one BLOB column (`display_identity`)
and the encrypted reasoning text carry no required fact.

## How the observer joins to the native rows

The stream has no call id, so no join is by id.

- **Turn, response.** By the exact prompt text and by the response canary, as in
  every row.
- **Scored action.** By turn, kind and arguments (`_match_action`): a helper by
  its argv (the command of the stream against the command of the native call;
  the run canary flag must be equal); the edit by kind `edit` and the target
  file. A terminal call with two helper segments is one action per segment on
  both sides.
- **Result.** Through the matched action (the native result is bound to its
  call by `tool_call_id`), then status, exit code, helper nonce and output must be
  equal. For a helper segment the output is its own line; for a single call it is
  the `output` member of the terminal result; for the edit it is the whole result
  text of the `patch` call.
- **Action-result relation.** Native: `tool_call_id` of the tool row (and the
  segment index inside a compound call).
- **Unscored native action** (`scored_pool`). In one turn, an observed unscored
  call pairs with the first native call of the same tool name and the same
  arguments that no earlier observed call took. The paired native call, its
  result and their relation leave the scored population. This removed the
  `read_file` call of each run and the two `search_files` calls of run 2. A
  native call that the observer did not see would stay and enlarge the
  denominator; there was none.
- **Changed file.** By path and both hashes.
- **Final after R2.** The observer relation points at the final helper action.

## Metrics

In turn 1 of each run the model ran both helpers in one terminal call
(`inspect && baseline`). In turn 2 it read the file (`read_file`), changed it
(`patch`) and ran the final helper (`terminal`). Run 2 also searched the
workspace twice in turn 1 (`search_files`).

**Measured at full credit** (26 metrics): all nine work and revision metrics,
both causal metrics, model identity, companions, isolated decode, canonical
equality, and eleven broad metrics.

- *Actions 4/4.* The compound call is two helper actions (compound shell call
  rule). The `patch` call is the native edit action.
- *Results 4/4.* First helper: exit 0 by the `&&` chain to the second helper,
  whose line is in the result. Second helper: the call's own exit code 1. Edit:
  `success` in the result row of the `patch` call. Final helper: exit 0.
- *Action-result relation 4/4.* By `tool_call_id`.
- *Changed file 1/1.* Pre-image: the file source in the output of the inspect
  helper (a native terminal result of turn 1). Edit: `old_string` and
  `new_string` of the `patch` call; `old_string` occurs exactly once. The unified
  `diff` in the result row of the same call must turn the pre-image into the same
  post-image, and the row must say `success`. Both hashes are computed from these
  native texts and equal the observer's pair. The `read_file` result is not used
  (its content is numbered and has no final line feed).
- *Final after R2.* Rising `messages.id` inside the R2 turn: user row, `patch`
  call, `patch` result (`success`), final call, final result (exit 0), response.
  Each result row is bound to its call by `tool_call_id`. No time is used.
- *Turn-response relation.* No key links a model row to its user row. The turn of
  a row is the latest user row before it in `messages.id` order.
- *Model identity.* A message row has no model column. The model of a response is
  `sessions.model`, joined by the declared key `messages.session_id`. The decoder
  takes it only when every `session_model_usage` row of the session names that
  same model. The configuration is the provider (`billing_provider`). This is a
  judgment: the identity is stated once per session, not per response.
- *Thread structure.* By the frozen linear recovery rule; there are no parent keys.
- *Event timestamps 13/13.* `messages.timestamp` of the row of each event: the
  assistant row for a call, the tool row for a result and for the changed file. A
  result gets time credit only when every observed value (output, status, exit
  code, nonce) is in the native result, the same test as `work.results`.
- *Duplicate safety.* 12 events and 12 statements (16 in run 2). No row restates
  another, and the system prompt row holds no prompt of a user row.
- *Self-contained identity.* Session id from `sessions.id`, surface from
  `sessions.source` (`oneshot`), harness `Hermes Agent` from the first sentence of
  the system prompt row, as the Antigravity row reads its product statement.
- *Declared version, honest version signal.* `state.db schema_version=31`, from
  the bound row of `schema_version`. Both metrics cite the version extract file.
  The decoder contract names 31 and refuses any other value. One value was
  observed; as in the other ranked rows, that does not show how the vendor
  changes the number.
- *Companions.* The schema export and the system prompt row are both bound.

**Not at full credit:**

| Metric | State | Result | Reason |
|---|---|---|---|
| `attribution.usage` | measured | 1/2 | See *Usage* below. |
| `attribution.token_semantics` | native_absent | 0/2 | The usage record has no cache-read and no cache-write key. The cache keys exist only on the session totals. |
| `attribution.reconciliation` | native_absent | 0/1 | One request has a usage record, the session had six or eight. Nothing sums to the session total. |
| `portable.complete_root` | contradiction | 0/1 | The store holds other sessions and cannot be copied whole. |
| `broad.classified_content_density` | measured | 0.618, 0.675, 0.613 | Useful bytes of the message rows over all bytes of the bound rows. The system prompt row is one `system` record of about 10 kB. |

The two absences rest on the bound rows, on the schema of the store and on the
receipt (`absence_findings`).

**Per run:** run 1 86.4, run 2 86.5, run 3 86.3.

### Usage

`sessions.model_config` holds `_usage_anchor`: `prompt_tokens` and
`completion_tokens` of the last model request of the session, and the message
before that request: `base_count` (its number among the message rows),
`base_last_role`, and `base_last_fp`, the SHA-256 of the canonical JSON of that
row's `content`, `role` and `tool_call_id`. In the six row exports of the three
runs (after turn 1 and after turn 2) the three values name the last tool row, and
the next row is the response of that request. The decoder checks them and gives
that response one usage fact with an input and an output count.

| Run | Record after turn 2 (prompt / completion) | Session totals (input / output / cache read; requests) |
|---|---|---|
| 1 | 6559 / 94 | 6379 / 649 / 28672; 6 |
| 2 | 6663 / 103 | 11321 / 752 / 34816; 8 |
| 3 | 6430 / 97 | 8067 / 569 / 26624; 6 |

Each turn overwrites the record. The copy taken after turn 1 held the record of
the R1 response; the final store does not. The score is taken from the final
store: the R2 response joins a usage record, the R1 response does not. So
`attribution.usage` is 1/2.

## Exporter cross-check

The builder compares the exporter file of turn 2 with the rows of turn 2 and
writes the result to the private summary. In all three runs: the session id is
equal; the same number of messages on both sides; role, content, tool call ids
and tool result ids are equal for every message. The exporter adds one derived
session key (`timings`). It omits three message columns (`_compressed_summary`,
`display_identity`, `display_order`). No fact of the decoder differs.

## Sanitizer (`scripts/sanitize_hermes_score_packets.py`)

All changes keep the byte length. Every row, column and key stays.

- The home user name is aliased in every file, the stdout streams included.
- System prompt row: the first sentence stays (`You are Hermes Agent, built by
  Nous Research.`); a metric reads it. The rest is blanked with the vendor
  marker. The prompt is one string that mixes vendor instruction text with text
  built from the operator's environment. The two cannot be told apart safely, so
  the whole rest is blanked under one marker. `hash` is the SHA-256 of the
  prompt, so it is set to the SHA-256 of the public prompt, in the prompt row and
  in `sessions.system_prompt_hash`. The join still verifies and no digest of the
  private text is public. The row id in the operator's store is blanked.
- Usage record: `base_last_fp` is the digest of a message row that is in the
  same public file. The sanitizer recomputes it over the public row and keeps
  it, so the join of the usage record stays. `base_prefix_fp` has no known
  preimage and can cover the private prompt; it is zeroed. (The sets `-v1` and
  `-v2` zeroed both, with a wrong reason for the first.)
- Row export: `sessions.tool_names` is zeroed (a digest of the tool list, which is
  not in the packet). `messages.display_identity` is zeroed (a 32-byte digest
  with an unknown preimage). `encrypted_content` of the provider's reasoning
  items is blanked (nobody can inspect it).
- Home inventories: every entry that the receipt does not classify gets aliased
  path components and blanked size, times and inode. Every name inside a digested
  subtree is aliased. A digest inside a digested subtree stays only where it
  shows a change. Link target hashes are zeroed. A file that keeps its name (the
  store, a log) has its size and inode blanked too.
- Receipt: class lists and metadata digests are recomputed from the public
  inventories. The size, identity and digest of the store copy are blanked. The
  row totals of the operator's store are blanked; no check reads them. So no
  public document states the size or the file identity of the store.
- The version extract is not changed: it holds the version, the two pragmas and
  the schema digest.
- Kept: message ids, tool call ids, provider response ids, the session id, the
  schema of the store, the names of the entries that changed, the scratch
  workspace path (a random temporary directory).

The sanitizer fails on: a changed metric row, a home name in any encoding, an
e-mail, a credential, a name of another entry of the home, a session id of
another session, an unbound 64-hex digest (`check_public_packets`), an unbound
short digest (`short_digest_warnings`), a digest of a private system prompt, or
a phrase of a blanked text (the phrases of the three prompts are checked in every
file of the public set).

Allowlisted digests (with reasons in the script): digests of metadata listings of
the home, where they show a change; the schema digest of the store, which a
reader recomputes from the public schema; and `base_last_fp`, which a reader
recomputes from the public message row.

## Runs and build

| Repetition | Capture | State |
|---|---|---|
| 1 | `hermes-codex-2026-10-07-08` | used |
| 2 | `hermes-codex-2026-10-07-09` | used |
| 3 | `hermes-codex-2026-10-07-10` | used |

These are the first three captures that the corrected controller completed. The
choice was made before scoring.

Disclosed, not used:

- `hermes-codex-2026-10-07-07`: stopped before any model request (see above).
- `hermes-codex-2026-10-06-04`, `-05`, `-06`: superseded (see above).
- `hermes-codex-2026-10-06-03`. Turn 1 ran. The capture then stopped closed: five
  directories of the home were touched without a change of any entry inside, and
  the controller had no rule for that. The rule (`touched_children_unchanged`)
  was added before the next attempt and before any scoring.
- `hermes-codex-2026-09-29-01`, `hermes-codex-2026-10-02-02`. They hold only the
  exporter projection.

The old verifiers `scripts/qualify_hermes_capture.py` and
`scripts/qualify_hermes_complete_capture.py` are superseded too. Each accepts
only its one retained exporter-only capture by name, and the retained
qualification receipts pin their source by hash, so the files are not changed.
A whole-home capture is checked by `scripts/build_hermes_score_replays.py`: both
whole-home receipts, the bound rows, then the sandboxed score replay.

```
python3 scripts/build_hermes_score_replays.py \
  --version-extract artifacts/v1-expanded-preparation/hermes-store-version-extract-v1/state-db-schema-version.json \
  --output artifacts/v1-expanded-preparation/hermes-score-replay-v3
python3 scripts/sanitize_hermes_score_packets.py \
  --source artifacts/v1-expanded-preparation/hermes-score-replay-v3 \
  --output artifacts/v1-expanded-preparation/hermes-public-candidates-v3
```

The private packets, `summary.json`, the `*-private-transformation.json` files
and the capture directories are never published.

**Dates.** `collected_on` of a packet is the UTC date of the native session start
(`sessions.started_at`): 2026-10-07. The session ids carry the operator's local
time (`20261006_23…`). The run ids are labels of the operator.

## Judgments for the reviewer

1. **Usage 1/2.** The final store holds the usage record of the last request
   only. Other readings: the copy after turn 1 proves that a record of the R1
   response existed (then 2/2, 2.5 points more per run); or a record without
   cache keys and for one request of several is too weak to count (then 0/2, 2.5
   points less).
2. **Model identity** from the session row (see *Metrics*). If a per-response
   field is required: 0/2, 3 points less.
3. **Unread shared files in the absence proof.** The two absences
   (`token_semantics`, `reconciliation`) accept these changed files unread: the
   process logs under `logs/`, and three files by name:
   `runtime/active_sessions.json` (15 bytes before and after, so it cannot hold a
   session id), `skills/.bundled_manifest` (2727 bytes before and after) and
   `spawn-ledger.json` (about 500 bytes). They are process state of the harness.
   Their content was not read. A read that tells whether one of them names the
   session would settle it. If they are not accepted, both rows are unresolved
   and the row is provisional.
4. **Density and the search index.** The index rows are outside the read and
   outside density, because they were not read and their bytes are not in the
   bundle. The OpenCode row counts its unread `event` table in density, because
   that table is in its copied root. If both index copies of each message
   counted as `index` records, density would be about 0.44 to 0.49 (computed from
   the trigger text, not from read bytes): 0.5 to 0.6 points less per run.
5. **Root location.** `personal_history_scanned` is false: the session id comes
   from the stream, the store has a fixed path and the rows are found by key. To
   prove that no other row names the session, the exporter also compared every
   value of every table with the id, inside SQLite, on the private copy, and
   wrote counts only.
6. **One marker for the system prompt** in the public packets (see *Sanitizer*).

## Limits

- **Unread shared entries.** `logs/agent.log` grew by about 15 kB per run and
  `logs/errors.log` by about 1 kB. The digested subtrees `cache/`, `installs/`
  and (run 1) `tools/` changed. `cron`, `hooks`, `logs/curator`, `memories`,
  `pairing`, `sessions` and `skills` were only touched. `kanban.db` did not
  change. The receipts do not prove that an unread entry holds no record of the
  session.
- **Later reads.** The version row and the prompt rows were read after the
  captures (see *Later reads*).
- **Observer.** Without call ids the join is by turn, tool name and arguments. Two
  equal calls in one turn pair in order.
- **Model behaviour.** The counts follow from how the model called the tools in
  these runs. A model that edits by a script inside a shell call would give no
  observed edit action, and the observer would refuse the capture.
- **Build.** The version string ends in `.dirty`: the install is a git checkout
  with local changes. The observed format and stream are those of this checkout,
  not of a tagged release.
- `--continue-after-r1` is the old repair path for one decoder failure of the
  `-z` captures. It is not used for the stream captures.
