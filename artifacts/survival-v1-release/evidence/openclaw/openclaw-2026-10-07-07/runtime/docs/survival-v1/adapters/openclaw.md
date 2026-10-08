# OpenClaw

Scope: OpenClaw `2026.9.8 (fc23bc8)`, macOS, the owner's agent `main` with the
`codex` harness plugin, the owner's normal OpenClaw state, the owner's default
model (`openai` / `gpt-5.6-terra`). Controller and capture:
`session_bench/openclaw_state_capture.py`, `scripts/capture_openclaw_survival.py`.

Two routes reach the same agent and the same stores:

| Route | Launch | Observer | Tool events |
|---|---|---|---|
| `local` | two `openclaw agent --local` processes | `session_bench/openclaw_envelope_observer.py` (stdout envelope) | none: a count and names per turn |
| `acp` | a foreground gateway and one `openclaw acp` process | `session_bench/openclaw_acp_observer.py` (ACP stream) | each call: id, arguments, status, exit code, order |

The route `acp` exists because the route `local` gives the observer no tool
events (see *Tool-event channels that were tried*). The owner approved it on
2026-10-07: run the gateway in the foreground, install no service.

**Status (2026-10-07).** Three captures on the route `acp` are decoded and
scored: `openclaw-2026-10-07-04`, `-07`, `-06` (repetitions 1, 2, 3). All 31
metrics are resolved in each run (reconciliation as `native_absent`, see
*Metrics*). Decoder contract:
`openclaw-agent-sqlite-session-rows-v1` (`session_bench/openclaw_session_rows.py`).
Private packets: `artifacts/v1-expanded-preparation/openclaw-score-replay-v6`
(`scripts/build_openclaw_score_replays.py`). Public candidates:
`openclaw-public-candidates-v6` (`scripts/sanitize_openclaw_score_packets.py`).
Set v1 was rejected by the independent review
(`openclaw-independent-public-review-v1/rejection.md`): its inventories held a
file name that is a digest of the account id and user id of the Codex login.
The scores of v1 were confirmed and are unchanged in v2. Set v3 loses the 3
reconciliation points (outside review of candidate v38, owner decision
2026-10-08); the other 30 metric rows equal v2. Set v4 zeroes the digests of
the digested subtrees (see *Sanitizer*); all 31 rows equal v3. Set v5 and
set v6 change only this document: v6 lists the machine values that stay in the
inventories from the public data (see *Machine values that stay*); the packet
data and all 31 rows equal v4. Never publish `openclaw-public-candidates-v1` to
`-v5`. The row is ranked in v1 (owner
decision 2026-10-08; see *Replaced attempts*).

**Superseded.** `scripts/run_openclaw_survival.py`,
`scripts/run_openclaw_preflight.py` and
`session_bench/openclaw_shared_owner_plan.py` (with their tests) pinned OpenClaw
2026.9.5 and a fully isolated state. That route never got a usable login. The
files stay in the tree; do not use them for a new capture.

## What the row is

One OpenClaw session with two turns. OpenClaw does not run the model loop
itself here: its `codex` harness plugin starts a Codex app server with a Codex
home that the plugin owns, inside the OpenClaw home
(`agents/main/agent/codex-home`). So one run writes two kinds of native state:

- OpenClaw state: the session in the agent's SQLite store
  (`agents/main/agent/openclaw-agent.sqlite`) and the shared state store
  (`state/openclaw.sqlite`). OpenClaw 2026.9.8 writes no session file named by
  the session id; `agents/main/sessions/` holds only older transcripts.
- Codex state of the plugin: one rollout file per thread
  (`sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl`), a shell snapshot and
  a lock named by the thread id, and rows in `state_5.sqlite` and
  `thread_history_1.sqlite`.

The row is "OpenClaw as the owner uses it", not "Codex CLI". The Codex files
are part of the record because OpenClaw's plugin wrote them for this session.

## Why the state is not isolated

- An isolated state (`openclaw agent exec --state-dir <own dir>`) gets a new,
  empty Codex home with no login. The turn fails with `401 Unauthorized`
  (probes 03 and 04).
- Copying the credentials into a second store is not allowed: the refresh token
  rotates, and a copy can log the owner out.
- `--model` is rejected by the owner's model policy unless the model is on the
  allow list (probe 01). The controller passes no model. It records the provider
  and model that the stdout envelope reports and stops when turn 2 differs.
- The agent's workspace is fixed by the owner's configuration
  (`agents.defaults.workspace`). `OPENCLAW_WORKSPACE_DIR` is ignored (probes 04
  and 05: the agent did not find the fixture).

So the capture runs in the owner's normal state, and the fixture goes into the
owner's workspace for the time of the run.

## Launch

```
openclaw agent --local --session-id <uuid> --json --timeout 300 \
  --message-file <run directory>/observer/prompt-r<N>.txt
```

- Both turns use the same `--session-id`. The controller makes the UUID when it
  prepares the plan. This is the launch of probe 06.
- cwd is the agent workspace. stdin is `/dev/null`.
- Environment: `PATH`, `HOME`, `LANG`, `TMPDIR`, `SB_SURVIVAL_V1_RUN_CANARY`.
  The controller refuses to prepare or run when any `OPENCLAW_` or `CLAWDBOT_`
  variable is set.
- No `--model`, no `--verbose`, no `--thinking`, no `--agent`, no delivery flag.
- No retry and no fallback. The controller waits 420 s for one turn; the CLI
  gets `--timeout 300`.

The controller stops the capture when a turn: exits non-zero; shows an
authentication or quota message on stderr; does not print one envelope of a
completed turn; reports another session id, another workspace, another harness
than `codex`, a fallback or a reroute; lacks the response canary; or (turn 2)
reports another provider, model or Codex thread than turn 1.

### Preflight (no model call)

1. `openclaw --version` must print exactly `OpenClaw 2026.9.8 (fc23bc8)`
   (`--expect-version` changes the value).
2. `openclaw config get agents.defaults.workspace` gives the workspace path. It
   must print exactly one line with an absolute path (or `~/…`). The
   configuration file itself is never read.
3. `ps -axo pid=,ppid=,command=` must show no other OpenClaw process (the CLI,
   the gateway, `openclaw.mjs` under node). The controller keeps only the pid
   and a kind (`gateway`, `agent`, `other`) of a match, never a command line.
   No private file is read for this check. A gateway or a second agent would
   write to the same stores during the capture.
4. The workspace must be an ordinary directory outside the OpenClaw home.
5. `<workspace>/fixture_project` must not exist (a link with that name counts
   as existing). Otherwise nothing is placed and no model call is made.

These commands have not run under the controller yet (see *The first live
capture must confirm*).

## Route `acp`: gateway and ACP bridge

```
OPENCLAW_SKIP_CHANNELS=1 OPENCLAW_SKIP_CRON=1 OPENCLAW_SKIP_GMAIL_WATCHER=1 \
OPENCLAW_SKIP_BROWSER_CONTROL_SERVER=1 OPENCLAW_SKIP_CANVAS_HOST=1 \
OPENCLAW_DISABLE_BONJOUR=1 OPENCLAW_NO_AUTO_UPDATE=1 openclaw gateway run
openclaw acp --session agent:main:explicit:<uuid> --no-prefix-cwd
```

Both run with cwd = the agent workspace and the environment `PATH`, `HOME`,
`LANG`, `TMPDIR`, `SB_SURVIVAL_V1_RUN_CANARY`; the gateway gets the seven
variables in addition. No `--model`, no `--port`, no `--force`, no token flag.

**The switches are mandatory.** The owner's configuration has a live message
channel and a timed heartbeat that writes to the owner. A gateway without
`OPENCLAW_SKIP_CHANNELS=1` would connect the channel; without
`OPENCLAW_SKIP_CRON=1` it would run timed jobs. `start_gateway` refuses to
start anything when the plan lacks one of the two, or when the argv is not
`gateway run`. The controller never runs `gateway install`, `start` or
`restart`: no service is installed. In the captures the gateway log says
"skipping channel start" and "scheduled heartbeats are disabled because the
cron scheduler is disabled".

Order of one capture:

1. Preflight as on the route `local` (it runs before the gateway starts, so the
   process check needs no exemption for the controller's own gateway). In
   addition port 18789 must be closed. An open port means another gateway:
   nothing is placed and nothing is started.
2. Fixture placement, then the first inventory of the home. The bracket so
   covers the start and the stop of the gateway.
3. `openclaw gateway run` in its own process group. The controller waits until
   the port opens (about 10 s in the captures; limit 90 s).
4. One `openclaw acp` process. JSON-RPC lines on stdio: `initialize`
   (`protocolVersion` 1, no client file or terminal capability), `session/new`
   (`cwd`, `mcpServers: []`), then one `session/prompt` per turn in the same ACP
   session. A `session/request_permission` is answered with the `allow_once`
   option (else another allow option); request and answer are in the stream.
   No capture has seen such a request yet.
5. Every line sent and received goes raw into `turn-r<N>/acp-stream.jsonl` as
   `{"t_ns", "dir", "raw"}`. A received line has its arrival time. The lines of
   `initialize` and `session/new` are in the file of turn 1.
6. After each turn: fixture snapshot, `turn_summary` of the stream (the turn
   must end with `stopReason: end_turn`, every tool call must have ended, the
   session key in the stream must be the requested one), canary, helper ledger.
7. The ACP process is closed. The gateway gets `SIGTERM` (its process group),
   then `SIGKILL` after 20 s. The controller checks that the process is gone and
   that the port is closed (`gateway-stop.json`). This also runs on a failure
   and on an interrupt. When the gateway does not stop, the result has
   `GATEWAY_NOT_STOPPED: <pid>` and the script exits with code 4.
8. Only then the home is bracketed, **once**, for both turns
   (`r2-native-receipt.json`; there is no receipt of turn 1 on this route).

The gateway output is written to temporary files outside the run directory.
The kept copies (`gateway-stdout.redacted.txt`, `gateway-stderr.redacted.txt`,
`acp-stderr.redacted.txt`) have every run of 24 or more token characters cut
out, and the home path replaced: a gateway can print its access token. The
controller never reads or passes the token; `openclaw acp` takes it from the
owner's configuration itself. The streams of the two captures hold no token.

### Ids on this route (found by `openclaw-2026-10-07-03` and `-04`)

| Id | Where it is seen | Value in `-04` |
|---|---|---|
| Session key | made by the controller: `agent:main:explicit:<uuid>`; echoed in `session_info_update._meta.sessionKey` | `agent:main:explicit:aac1d9ac-…` |
| ACP session id | result of `session/new`; `sessionId` of every stream message | `0c70bc49-…` |
| OpenClaw session id | **not in the stream.** It is a new id, not the uuid of the key. The row of the session key in `session_nodes` (agent store) gives it (`session_key_lookup`) | `32a77241-…` |
| Codex thread id | not in the stream. The name of the one new rollout file gives it | `01a117ed-…` |

- Both prompts land in the same OpenClaw session and the same Codex thread: one
  new rollout file, and `thread_turns` has two rows.
- The gateway keeps replay rows under the ACP session id
  (`acp_replay_sessions`, `acp_replay_events` in `state/openclaw.sqlite`). So the
  ACP session id is a fourth exact identity of the row export, and a
  `session_id` column may hold it.
- The uuid inside the key (`key_id`) and the ACP session id also count for
  session-owned paths. No path carried them.
- Provider and model: not in the stream. The gateway prints one start line,
  `[gateway] agent model: openai/gpt-5.6-terra (thinking=medium, fast=off)`;
  the controller keeps it in `gateway-stop.json` (`agent_model`). It is one model
  per gateway process, not per response. The native rows name the model
  (`session_windows.model`, `threads.model`, and inside `transcript_events`).
- Usage: the stream has only `usage_update` (`used`, `size`; the context size,
  marked approximate). Token counts per request are only in native rows
  (inside `transcript_events`, `trajectory_runtime_events`, `session_nodes`).

### What the ACP stream holds per tool call

| | `bash` | `apply_patch` |
|---|---|---|
| Start (`tool_call`) | `toolCallId`, `title`, `kind: execute`, `status: in_progress`, `rawInput: {command, cwd}`; the command is `/bin/zsh -lc '<command>'` | `toolCallId`, `title`, `kind: other`, `rawInput.changes: [{path, kind, stat, diff}]`, `locations` |
| End (`tool_call_update`) | `status` `completed` or `failed`, `rawOutput: {status, exitCode, durationMs}` | `status`, `rawOutput: {status, changes: [{path, kind}]}`; no exit code |
| Missing | the output text (stdout, stderr) | the file content before and after |

Order and time come from the stream. A failed helper (exit code 1) has status
`failed`. The model may put two helpers into one call (`-04`, turn 1); the
call then has one exit code.

### Observer of the route `acp`

`build_openclaw_acp_observer` reads the two streams, the helper ledger after
each turn and the two hashes of `checkout.py`. No native row.

- User turns: the prompt text of `session/prompt`.
- Responses: the joined `agent_message_chunk` texts; the canary must end the text.
- Scored actions: each helper invocation and the last successful `apply_patch`
  with a change of `checkout.py`. The action has the call id, the tool, the
  inner command (or the changes with their diff) and the turn.
- Results: status and exit code from the stream. The output line of a helper
  comes from the helper ledger (`output_source: helper_ledger`): the stream has
  no output text. The ledger row is bound to the call by the helper phase in the
  command, the turn and the exit code. A helper that is not the last segment of
  a compound call takes its exit code from the ledger (compound shell call rule,
  as in the Hermes observer).
- Other calls (for example `sed -n '1,160p' checkout.py`) are unscored actions
  with their arguments.
- `tool_events_observed: true`. The builder raises unless all four scored
  actions are tool calls of the stream.
- The observer session id is the session key. The stream does not name the
  OpenClaw session id; the join to the native rows is by `session_key`.
- Model: only when the gateway start line is passed in (`gateway_model`); else
  none, and the rubric rule for an observer channel without a model applies.

It fails closed on: a turn without `end_turn`; a tool call without an end; a
message kind it does not know; a message of another ACP session; another
session key; another prompt than the workload; a shell call without exit code
or with a status that differs from it; a helper in another turn, twice, or
with another exit code than the ledger; a missing edit or helper call.

### A refused bracket on this route

The home is bracketed once at the end, so a refusal would cost both turns.
`--rebracket` takes the native state again with the present rule table and
code, without a model call. Conditions: the capture failed only on the native
refusal; the gateway stopped; the fixture left; a new inventory under the
rules of the refusal equals `r2-state-refused.json` (the home did not change);
the digested subtrees are the same. The first result and the refusal stay;
`rebracket-result.json` is added. `openclaw-2026-10-07-03` was completed this
way (see below). A reviewer can reject such a capture; `-04` needed none.

## The fixture in the owner's workspace

- The controller creates `<workspace>/fixture_project` with `mkdir` (it fails
  when the entry exists), writes the four frozen files, and records the
  directory identity and the file list in `fixture-placement.json`.
- After each turn it copies the fixture into `turn-r<N>/workspace/`.
- At the end, on success, on any failure and on an interrupt, it copies the
  fixture into `workspaces/after/` and removes the directory. It then checks
  that the entry is gone (`fixture-removal.json`).
- It removes only the directory that it placed (same device and inode). When
  the entry is something else, or the removal fails, the result has
  `fixture_removed: false` and `FIXTURE_NOT_REMOVED: <path>`, the status is
  `capture_incomplete`, and the script prints the path and exits with code 3.
  Remove the directory by hand then.
- A `kill -9` of the controller skips the removal. `plan.json` and
  `fixture-placement.json` name the path. The next capture refuses to start
  while the directory exists.
- The controller lists the workspace by metadata before the placement and after
  the removal. Entries outside `fixture_project` that are new, changed or
  removed are named in `fixture-removal.json` with path, kind and size. No
  content is read, and the full listing is not written. This shows whether the
  agent wrote elsewhere in the workspace (for example a memory note).

## Capture scope (receipt `openclaw-state-root-v1`)

The controller lists the whole OpenClaw home by metadata before turn 1 and
after each turn (two equal reads: the tree is at rest). No old file is opened
and no link is followed. Every entry that is new, changed or removed must fall
into one class. Anything else stops the capture and leaves
`r<N>-refusal.json` with each unexplained path and the rule table.

| Class | Rule | What is kept |
|---|---|---|
| Session-owned | The path carries the OpenClaw session id or the Codex thread id (rollout file, shell snapshot, thread lock); on the route `acp` also the uuid of the session key or the ACP session id. No such path may exist before turn 1 | Byte-exact copy |
| Run-owned | A new file, born during the capture, that matches `run_owned`: `tmp/plugin-captures/<id>/owner.sqlite` (and sidecars), `tmp/plugin-captures/<id>/native/admission-*/content/package.json`, `codex-home/tmp/arg0/codex-arg0*/.lock`, `logs/<file>` | Copy, `private_only: true` |
| Shared | An old file that changed (the SQLite stores, the quarantine store, `logs_2.sqlite`, configuration and its backups, any other old file); a `-wal`/`-shm`/`-journal` file beside an old file; a lock, pid, `.tmp` or temporary file that appeared or disappeared (`shared_transient`); a temporary directory that disappeared (`transient_directories`); a digested subtree (`npm`, `browser`, `backups`, `bin`, `completions`, `canvas`, `codex-home/.tmp`, `codex-home/skills`) | Metadata only. Content is not read |
| Refused | An old entry inside a session area (`agents/main/sessions`, `codex-home/sessions`) that changed, except `sessions.json`: another session was active. A new rollout file of another thread. A removed directory that is not temporary. Anything no rule names | The capture stops |

The table is `RULES` in `session_bench/openclaw_state_capture.py`. The receipt
stores the table it used and its digest. `verify_openclaw_state_capture`
checks a receipt offline against the two inventories, the copies and the row
export.

### The Codex thread id

The stdout envelope gives it: `meta.agentMeta.terminalReceipt.assistantTranscriptIdempotencyKey`
is `codex-app-server:<thread id>:<turn id>:assistant`, and the turn id equals
`terminalReceipt.turnId`. In probes 05 and 06 the thread id is the same in both
turns. The capture uses the id only when a new rollout file carries it in its
name. Without an id on stdout, exactly one new rollout file gives the id. No
confirming rollout file, or two candidates: the capture stops.

`meta.agentMeta.sessionId` is the `--session-id` value (probes 04, 05, 06). It is
not the Codex thread id.

### Rows of the test session in the shared stores

One exception to "content is not read", by owner permission for the rows of
the test session only. For each store in this list the controller copies the
database with its `-wal` and `-shm` files to a private temporary directory,
opens the COPY read-only, exports, and deletes the copy (and checks that it is
gone). The original is never opened with SQLite.

| Store | Must hold the session | Tables that held it in probe 03 (isolated state, same build) |
|---|---|---|
| `agents/main/agent/openclaw-agent.sqlite` | yes | `session_nodes`, `session_windows`, `session_entry_snapshots`, `transcript_events`, `transcript_event_identities`, `session_transcript_*`, `transcript_rewrite_watermarks`, `trajectory_runtime_events` |
| `codex-home/state_5.sqlite` | yes | `threads` |
| `codex-home/thread_history_1.sqlite` | yes | `thread_turns`, `thread_items`, `thread_history_projection_state` |
| `state/openclaw.sqlite` | no | none by exact match; two rows of `plugin_state_entries` hold the ids inside JSON text |

Selection, in every table that is not denied:

1. Every row with a column whose value is exactly the session id, the session
   key (`agent:main:explicit:<session id>`, used only when it carries the
   session id) or the Codex thread id.
2. One level of child rows: by a declared single-column foreign key, or by a
   column named `<table>_id` with zero orphan values in the whole table.
3. A row that holds an id only inside a longer value is counted
   (`rows_that_contain_an_id`), not exported.
4. For all other rows only the count per table is read.

Guard: the export fails when an exported row has another value in a
`session_id`, `current_session_id` or `thread_id` column, a `session_key`
without the session id, or another id as the key of a `threads` or `sessions`
table.

**Deny rule.** A table is never queried (no row, no count, no search, not as a
join target) when its name, or the name of one of its columns, holds one of
the words `auth`, `oauth`, `profile`, `token`, `credential`, `secret`, `key`,
`password`, `passwd`, `apikey`, `bearer`, `cookie` (also as a plural). This
also holds when a row names the test session. Only the table and column names
stay in the schema export. Column names that hold such a word and are not
secrets are listed in `benign_columns`: `*session_key`, `*idempotency_key`,
`entry_key`, `tokens_used`, `token_budget`, `<kind>_tokens`.

The rule is strict on purpose. On the schema of probe 03 it denies
`auth_profile_state` and `auth_profile_store` in the agent store, and 47 tables
of `state/openclaw.sqlite` (device tokens, secret store, OAuth stores, and
every table with a `*_key` column). It also denies three tables that can hold
session data and no secret: `session_state_events` (`dedupe_key`) and
`task_runs` (`owner_key`) in `state/openclaw.sqlite`, and `thread_attachments`
(`identity_key`) in `state_5.sqlite`. The owner can add such a column name to
`benign_columns`.

`logs_2.sqlite` of the Codex home has rows with the thread id (log lines). It
is not in the list and is not read.

## What is read and what never is

Read:

- Metadata of every entry of the OpenClaw home (names, sizes, times, inode
  numbers; link targets only as a hash).
- Content of files named by the session id or the Codex thread id, and of the
  run-owned files of the table above.
- From private copies of the four stores: the schema, the rows of the test
  session, and row counts.
- Metadata of the workspace (kept in memory; only changed entries outside the
  fixture are written).
- The fixture directory that the controller placed.
- The controller's own files: stdout and stderr of each turn, receipts.

Never read:

- The configuration file, `.env`, `credentials/`, `identity/`, `devices/`,
  `cron/`, `exec-approvals.json`, backups, logs that existed before the run.
- Any session file or rollout file of another session or thread.
- Any row of another session; any table on the deny rule.
- `state/openclaw-quarantine.sqlite`, `logs_2.sqlite`, `queue_1.sqlite`,
  `goals_1.sqlite`, `memories_1.sqlite` (metadata only).
- Any file content in the owner's workspace outside `fixture_project`.
- Command lines of other processes are not stored.

The stdout envelope of a turn holds the workspace path, the names of the
injected workspace files and the names of the owner's skills
(`meta.systemPromptReport`). The run directory is private.

## Observer of the route `local`

The observer is built from the two stdout envelopes, the helper ledger after
each turn and the hashes of `checkout.py`. It reads no native row.

What the envelope proves (probe 06, `r1.stdout.json` and `r2.stdout.json`):

- The final reply (`payloads[0].text`, equal to `meta.finalAssistantVisibleText`).
- Session id, session key, workspace.
- Provider, model, harness id, credential source kind, no fallback.
- Token counts of the turn: `input`, `output`, `cacheRead`, `cacheWrite`,
  `reasoningTokens`, `total`.
- Tools only as a summary: `meta.toolSummary` is
  `{"calls": 2, "tools": ["bash"], "failures": 1}` in turn 1 and
  `{"calls": 2, "tools": ["apply_patch", "bash"], "failures": 0}` in turn 2;
  `terminalReceipt.successfulToolNames` lists names.

What it does not hold: any single tool call. No arguments, no command text, no
result, no exit code, no call id, no order. `payloads` has one entry, the
reply. stderr has four log lines per turn (SQLite maintenance, a cleanup
warning, the run id and stop reason) and no tool event.

So the observer has no tool events. Its three helper actions and results come
from the helper ledger, and its edit action comes from the file hashes. Each
has `tool_event_observed: false`. By the ruling of the Hermes review
(`hermes-independent-public-review-v2/rejection.md`, D3) and the protocol text
("the helper ledger supports process truth but cannot prove the harness
received or displayed a result"), that is not enough for the Tier A rows. The
observer says so itself: `tool_events_observed: false` and
`unobserved_metrics: ["work.actions", "work.results", "causal.action_result"]`
(`ENVELOPE_UNOBSERVED`). A scorer must keep these three unresolved for this
observer, as for Pi print mode. The row would then be provisional.

The observer fails closed when: an envelope is not one completed turn; it has
more than one payload (this could be tool notes of a verbose session, which
this observer does not know); the two turns differ in session, provider, model
or harness; a turn with a helper reports zero tool calls; the ledger of turn 1
is not the start of the ledger of turn 2; `checkout.py` did not change; a
response lacks its canary.

### Tool-event channels that were tried

| Channel | Result |
|---|---|
| `--json` envelope of `agent --local` | a count and names per turn; no single call |
| `--verbose on` with `--local` | no tool events (tried 2026-10-07) |
| `OPENCLAW_RAW_STREAM=1` with `--local` | no tool events (tried 2026-10-07) |
| Plugin capture (`tmp/plugin-captures/<id>/owner.sqlite`) | about the plugin package; no table in the isolated probe 01 |
| ACP bridge to a foreground gateway | each tool call with arguments, status and exit code (probe 07; route `acp`) |

## The native family and the read

Decided on 2026-10-07, after the containers of the captures had been seen and
before any score was computed. The read is in the table of `rubric.md`
(*Duplicate-safety read*). This is the judgment a reviewer should attack first.

**Family** (what the capture bound for the session):

- every exported row of the four shared stores (167, 159 and 194 rows in the
  three runs; see *Rows of the test session in the shared stores*);
- the three files named by the Codex thread id: the rollout JSONL (about
  245 kB), the shell snapshot (about 250 kB) and the thread lock (0 bytes).

Not in the family: the temporary run files (three `arg0` lock files of 0 bytes
and one plugin-capture `owner.sqlite` of 4096 bytes without a table). They are
process state, not records of the session. This is a judgment.

Known gaps of the family, from the receipts:

- tables that the deny rule never reads (13 in the agent store, 48 in
  `state/openclaw.sqlite`, 2 in `state_5.sqlite`). A row of the session there
  is unknown;
- two rows of `plugin_state_entries` hold an id only inside JSON text (the
  thread binding of the `codex` plugin). They are counted, not exported;
- the receipt does not prove that an unread shared file holds no record of the
  session.

**Read** (containers the decoder takes a scored fact from), all in
`openclaw-agent.sqlite`:

| Table | Facts taken |
|---|---|
| `transcript_events` | prompts, responses, tool calls, tool results, order (`seq`, `parentId`), times, model and provider, usage, the format version of the transcript |
| `trajectory_runtime_events` | the exit code of a shell call (`tool.result`), the token totals of a run (`model.completed`), the product name (`traceSchema`) |
| `session_windows` | the session id, the agent harness id, the chat type |

One row is one record. Everything in the read counts for duplicate safety and
density.

Why these three:

- The row measures OpenClaw. The transcript and the trace are the record that
  OpenClaw itself keeps for a session, keyed by the OpenClaw session id, for
  every backend.
- The transcript alone is not enough. It has `isError` on a tool result and no
  exit code; it has no declared total; it does not name the product. The trace
  table holds these three facts. So the decoder opens the trace table, and all
  trace rows then count: the trace restates almost every event (see *Duplicate
  safety*). Opening the trace costs about 1.9 points of duplicate safety and
  earns the exit codes, the run totals and the product name. Leaving it out
  was not an honest option: reconciliation could then be neither measured nor
  called absent, because the trace table holds the totals.

**Outside the read** (never opened by the decoder; their bytes count for density):

| Container | Why outside |
|---|---|
| The Codex rollout JSONL, `state_5.sqlite` (`threads`), `thread_history_1.sqlite` (`thread_turns`, `thread_items`) | This is the record of the Codex backend, in Codex's own format, in a Codex home that the plugin owns. OpenClaw mirrors it into its transcript (every mirrored row says `mirrorOrigin: codex-app-server`). The decoder takes no fact from it. The benchmark has a Codex CLI row for that format. Cost of leaving it out: the per-request usage records and the thread totals of the rollout earn nothing here. Gain: its copies of every event do not count as duplicates. With the read as it is, duplicate safety is already 0, so the gain is zero points |
| `acp_replay_events`, `acp_replay_sessions` (`state/openclaw.sqlite`) | The replay buffer of the ACP bridge: one row per `session/update` that the gateway sent to the ACP client, kept so that a client can load the session again. It exists only because the capture used the route `acp`; the capture `-02` on the route `local` has no row in `state/openclaw.sqlite`. It is native bytes (OpenClaw wrote it into its own store), but it is a record of the transport, not of the session format. The decoder takes no fact from it. Counting it would score the capture route |
| `session_transcript_fts*`, `session_transcript_active_events`, `session_transcript_index_state`, `transcript_event_identities` | Search index and position index of the transcript, as the Hermes row treats `messages_fts*` |
| `session_nodes`, `session_entry_snapshots`, `session_participants`, `transcript_rewrite_watermarks`, `audit_events`, `worker_session_placements` | Session index row, snapshots of the skills catalog and of the workspace file list, audit trail, placement row. No fact is taken |
| The shell snapshot and the thread lock | Files of the Codex backend |

The row export of the packet holds all these rows; the decoder skips them by
table name and counts each by its size. The three files of the Codex thread
are not in the packets; the decoder takes their sizes from the receipt.

## What the rows hold

| Table | Columns used | Meaning |
|---|---|---|
| `transcript_events` | `session_id`, `seq`, `event_json`, `event_zstd`, `event_utf8_bytes` | One row per event. `event_json` is JSON text. An event above a size limit is Zstandard-compressed JSON in `event_zstd` and `event_json` is NULL (in the three runs: the first prompt, about 2.2 kB). The first event is the session header: `type: session`, `version: 4`, the session id. Every other event is a `message` with an `id`, a `parentId` (the event before it), a `timestamp` (RFC 3339, UTC) and a `message` object |
| | `message` with `role: user` | `content` is the exact prompt. `__openclaw.upstreamUserText` holds the text that went to the backend (the prompt behind a sender line) |
| | `message` with `role: assistant` | One content item: `text`, or `toolCall` (`id`, `name`, `arguments`). Also `provider`, `model`, `api`, `usage` (`input`, `output`, `cacheRead`, `cacheWrite`, …), `stopReason`. The last message of a run has `__openclaw.runTerminal: true` |
| | `message` with `role: toolResult` | `toolCallId`, `toolName`, `isError`, text content. `__openclaw.toolOutput.source` is `execution` or `provider-response` |
| `trajectory_runtime_events` | `session_id`, `seq`, `run_id`, `event_json` | One row per trace event of a run: `session.started`, `context.compiled`, `prompt.submitted`, `tool.call`, `tool.result`, `model.completed`, `session.ended`. Each has `traceSchema: openclaw-trajectory`, `schemaVersion: 1`, `runId`, `ts`. `tool.result` has `data.toolCallId` and `data.result.exitCode`. `model.completed` has `data.usage` and a copy of every message of the run (`messagesSnapshot`) |
| `session_windows` | `session_id`, `agent_harness_id`, `chat_type`, `created_at` | The session row |

**Two forms of one tool call.** The backend runs in Codex code mode: the model
calls the tool `exec` with a JavaScript source, and the script runs a command
or a patch. The transcript holds both: the *script form* (`exec`, call id
`call_…`, the source in `arguments.input`) and the *execution form* (`bash`
with `command` and `cwd`, or `apply_patch` with `changes`; call id `exec-…`).
Each form has its own result row. The native field
`__openclaw.toolOutput.source` on the result tells them apart: `execution`
or `provider-response`. No key links a script-form call to its execution-form
call.

The decoder takes actions and results from the execution form. Its call ids
are the ids that the ACP stream shows, so observed and native calls join by
id. A script-form call is joined to the nearest earlier execution-form call
of its turn that is not yet taken, and then checked by content: it must hold
the inner command and the directory, or the same file and the same removed and
added lines (a line that a patch removes and adds back unchanged is not
compared). Then it is a second statement of that call, as the Codex CLI row
treats `custom_tool_call` and `CommandExecution`. A script-form call that
matches nothing would be its own action; no run has one.

A shell command is stored inside a shell wrapper: `/bin/zsh -lc '<command>'`
in runs 1 and 3, `/bin/zsh -c '<command>'` in run 2 (`-07`). The decoder takes
the inner command of exactly these two forms (a shell, then `-lc` or `-c`,
then one word).

## Metrics

In runs 1 and 2 the model ran both helpers in one command in turn 1
(`inspect && baseline`), then patched the file and ran the final helper. In
run 3 it ran the two helpers in two commands and read the file once before
the patch (`sed -n '1,120p' checkout.py`; an unscored action that the observer
saw too, so it is outside the population).

**Record fidelity (30 of 30).**

- *Submitted turns 2/2, R1, R2, order.* `message.content` of the two `user`
  rows equals the prompts byte for byte. The first prompt is the compressed
  row. Order is `seq`.
- *Visible responses 2/2.* The `assistant` text row with `runTerminal` of each
  run, with the canary at its end.
- *Actions 4/4.* The execution-form calls, joined to the observed calls by call
  id. A compound command is two helper actions (compound shell call rule); each
  segment keeps the call id. `apply_patch` with one change of `checkout.py` is
  the edit action.
- *Results 4/4.* Output: the helper line in the text of the result row. Status:
  `isError`. Exit code: `data.result.exitCode` of the trace row `tool.result`
  with the same `toolCallId`. In a compound call the call's exit code counts
  for the last helper; the first helper has exit 0 by the `&&` chain to the
  second, whose line is in the result. The edit result has a status and no
  exit code; its output is compared as key-sorted JSON of the result object on
  both sides (the stream gives an object, the row gives indented JSON text).
  *Weak point:* the exit code comes from the trace table, not from the result
  row. The decoder fails the decode when `isError` of the two rows differs.
- *Changed file 1/1.* Pre-image: the file source in the output of the inspect
  helper (a native `bash` result of turn 1). Edit: the unified `diff` in
  `changes` of the `apply_patch` call. The diff must apply to the pre-image
  and the result row must say success. Both hashes are computed from these
  native texts and equal the observer's pair in the three runs.
- *Final after R2.* Rising `seq` on the `parentId` chain inside the R2 turn:
  user row, patch call, patch result (success), final call, final result (exit
  0), response. Each result is bound to its call by `toolCallId`. No time is used.

**Causality and context (20 of 20).**

- *Action-result 4/4.* `toolCallId` of the result row.
- *Turn-response 2/2.* The `parentId` chain from the response back to its user
  message.
- *Readable rationale 2/2.* The response text is plain text in the row.
- *Thread structure.* Parents are explicit (`parentId`), one linear chain.

**Usage and attribution (12 of 15).** This category holds the weakest judgments.

- *Model and configuration 2/2.* `model` and `provider` on the response row.
  The observer has the model from the start line of the gateway.
- *Usage 2/2.* The `usage` object on the response row has `input` and `output`.
  *Weak point:* its counts are the totals of the run (all model requests of
  the turn), not of the request that produced the response. The rubric accepts
  a turn scope for native-attested usage.
- *Token semantics 2/2.* The same object has `cacheRead` and `cacheWrite`
  under their own keys; `cacheWrite` is an explicit 0. *Weak point:* every
  other assistant message of the run (commentary, tool calls) has a usage
  object with zeros in all four fields. These zeros are placeholders, not
  measured zeros. They are not on a displayed response, so the metric does not
  read them.
- *Reconciliation 0/1, `native_absent`.* The store declares a total per run:
  `data.usage` of the trace row `model.completed`. The rubric asks that the
  per-response usage records of the native record sum to that total. The store
  writes the total of a run twice (the trace row and the last assistant message
  of the run) and writes zeros in all four fields on the other assistant
  messages (4 and 6 messages per run; 6 and 8 in run 3). The sum of the records
  equals the total only because one record is the total. These are not
  per-response records. The decoder states this: `run_totals` holds
  `records_with_counts` (1 in every run), and a reconciliation fact is made only
  when the total is split over more than one record with counts. Here it is not,
  so the decoder states none. *State.* `native_absent`, as for Hermes, whose
  rows hold no per-response usage record that sums to a total. It is not a
  measured failure (`contradiction`): no native value conflicts with another, and
  the property (records that sum to a total) is absent. The rubric accepts
  `native_absent` without a complete root when the bound rows of every shared
  store that names the session hold no such field; the capture assertion proves
  that the row export holds the rows of the session and of no other (replay hook
  `apply_openclaw_reconciliation_absence`). Until 2026-10-08 this row scored
  1/1 on the equality of the sum and the total. An outside review of candidate
  v38 named this as credit without per-response records; the owner decided on
  2026-10-08 to remove the 3 points. The usage and token-semantics credit is
  unchanged. The session row (`session_nodes.entry_json`) holds the counts of
  the last run only; it is outside the read and no session total exists.

**Portability and openness (17 of 20).**

- *Complete root: contradiction, 0 of 3.* The stores hold other sessions and
  cannot be copied whole. Scored as for Hermes.
- *Companions.* The schema export is bound and in the packet. *Judgment:* the
  files of the Codex thread are bound by the receipt and copied in the private
  capture, but they are not in the packets and are not counted as required
  companions: the decoder needs none of them.
- *Isolated decode, canonical equality.* The closed replay decodes the packet
  in an OS sandbox.
- *Standard tools readable.* SQLite, JSON text, and Zstandard for a large
  event. The decoder uses `sqlite3`, `json` and `compression.zstd` of the
  Python standard library (3.14). *Judgment:* Zstandard counts as a commodity
  parser, as for the DeepSeek Harness row. In these runs the compressed event
  is the first prompt; without Zstandard a reader loses it.
- *Documented format.* This file.
- *Self-contained identity.* Session id from `session_windows`; surface from
  `agent_harness_id` and `chat_type` (`agent harness codex; chat type
  direct`); harness `openclaw` from `traceSchema` (`openclaw-trajectory`).
  *Weak point:* no row says "OpenClaw" as a product field. The schema name of
  the trace is the only value that names it (the transcript has it only in key
  names such as `__openclaw`). If a reviewer rejects this, the row loses 2 points.
- *Declared format version.* `version: 4` in the session header of the
  transcript, `schemaVersion: 1` on every trace row, and `PRAGMA user_version`
  24 of the store (in the bound schema export).

**Durability and signal (9.1 of 15).**

- *Event timestamps 13/13.* `timestamp` of the transcript row of each event
  (RFC 3339, `Z`). A result gets time credit only when every observed value is
  in the native result.
- *Honest version signal.* The decoder contract names the three values and
  refuses any other. One value of each was observed; that does not show how
  the vendor changes them.
- *Observed schema stability.* The three captures decode without an exception.
- *Stable root location.* The session is found by its key: the controller
  makes the key, and the row of that key gives the session id. Two
  metadata-only inventories of the whole home; no other session is read.
- *Duplicate safety: 0.* The forward read is `transcript_events` by `seq`,
  then `trajectory_runtime_events` by `seq`. Every event has two or more
  statements: a prompt in its transcript row and in the trace rows
  `context.compiled`, `prompt.submitted` and `model.completed`; a response in
  its row and in `model.completed`; a call in its execution form, in its
  script form, in `tool.call` and in `model.completed`; a result likewise.
  The script-form result of the patch (`Script completed …`) is its own event
  and is stated again by `model.completed`. No field marks a copy as
  superseded. Result: 0 of 13 events with 40 statements (runs 1 and 2), 0 of
  17 with 54 (run 3). Without the trace table 8 of 13 events (8 of 17 in run 3)
  would have one statement.
- *Density: 0.031, 0.031, 0.037.* Useful bytes: the first row that states each
  event (about 21 kB; 26 kB in run 3). All bytes: every row of the row export
  (about 190 kB) and the three files of the Codex thread (about 495 kB).
  *Judgment:* the Codex files count because the capture bound them as files of
  the session. They are 72% of the denominator, and they are bound by size
  only: the files are not in the packets, the sizes come from the receipt, and
  a reader of the public set cannot check them against bytes. Without them
  density would be about 0.11 (+0.25 points).

**Per run:** 88.09, 88.09, 88.11. Mean 88.1 (91.09, 91.09, 91.11 and 91.1 before reconciliation was removed).

| Metric | State | Result |
|---|---|---|
| The 19 deep metrics except `portable.complete_root` and `attribution.reconciliation` | measured | full credit |
| `attribution.reconciliation` | native_absent | 0 of 3 |
| `portable.complete_root` | contradiction | 0 of 3 |
| The 12 broad metrics except the two below | measured | full credit |
| `broad.naive_reader_duplicate_safety` | measured as 0 (the scorer prints `native_absent` for a zero count) | 0 of 3 |
| `broad.classified_content_density` | measured | 0.09, 0.09, 0.11 of 3 |

## Judgments for the reviewer

Each can be rejected. The effect on the score is given where it is known.

1. **The read.** Three tables of the agent store; the Codex rollout and the
   ACP replay buffer are outside. With the rollout in the read nothing would
   change in points (duplicate safety is 0 already; density counts the rollout
   bytes already). With the trace table outside the read, duplicate safety
   would rise to about 1.85 (1.41 in run 3), and results, reconciliation and identity would
   lose up to 8.75 points or stay unresolved.
2. **Reconciliation** is `native_absent` (0 of 3): the sum of one record and
   zeros is not a check of per-response records. Removed on 2026-10-08.
3. **Usage scope** is a run, not a request (−5 and −4 if a reviewer requires
   per-request records; the rubric text does not).
4. **Harness name** from `traceSchema` (−2 if rejected).
5. **Zstandard** as a commodity parser (−2 if rejected).
6. **Two forms of one call** are one event with two statements, not two
   actions. The observer shows only the execution form. If the script-form
   calls counted as extra native actions, actions, results and action-result
   relations would be 4 of 7 (about −7.3). The Codex CLI row uses the same rule.
7. **Exit code from the trace table**, joined by `toolCallId` (−3.75 and the
   result times if rejected).
8. **Companions** do not include the files of the Codex thread (−2 if rejected).
9. **Complete family** is claimed with the gaps listed above. Density and
   duplicate safety need it.
10. **Edit result text** is compared after one declared transform (key-sorted
    JSON of the result object).
11. **The model** is the owner's default; the observer knows it from one line
    of the gateway log, per process.
12. **Two scheduled attempts were replaced** (`-03` by `-04`, `-05` by `-07`;
    see *Replaced attempts*). The rubric rule is waived for v1.
13. **Shared code.** `session_bench/score_replay.py` got the `openclaw`
    branches and one sandbox rule (read access to the installed libzstd).
    Older packets carry their own copy of that file and replay from it.

## Sanitizer (`scripts/sanitize_openclaw_score_packets.py`)

Changed, always at equal byte length of the file:

- the home user name and the home path, in every file;
- in the tables of the read: tool descriptions and schema descriptions in the
  trace rows `context.compiled` (vendor marker; 34 per run), the account
  e-mail in `authProfileId` (2 per run), `mirrorSourceFingerprint` (zeroed
  everywhere; see below). Every row, column and key stays;
- in every table outside the read: every text value is blanked and every BLOB
  zeroed, except a value that is exactly an id of the test session. These
  tables hold the owner's skills catalog, the list of the owner's workspace
  files with fingerprints, the account ids of the Codex login, the address and
  commit of the workspace repository, and copies of the events. The decoder
  never parses them; density counts them by size, which does not change;
- in the ACP streams: names, descriptions and hints of the listed slash
  commands (the list can name the owner's skills);
- in the capture documents: device, inode, entry count and listing digest of
  the workspace; size and digest of the raw gateway output;
- in the two inventories and the receipt: as for Hermes (link target hashes
  zeroed; class lists and metadata digests recomputed). An entry that is in no
  receipt class gets an aliased name and blanked size, inode and times
  (`birth_ns`, `ctime_ns`, `mtime_ns`), except the entries that the sanitizer
  keeps by name: the directories above a classified entry, the digested roots,
  the session-store files and the links of a receipt class. What stays for each
  kind of entry is in *Machine values that stay* below. The digest and file
  identity of every copied file are blanked. The sizes of the three Codex files
  stay;
- the digests of a digested subtree in the two inventories (`entries_sha256` and
  every value of `directory_sha256`; the npm directory is one such subtree). The
  preimage of each is a metadata listing of the owner's home: paths, inode
  numbers and nanosecond times. So no such digest is published. Every value is
  64 zeros. Where a digest differs between `state-before` and `state-after`, the
  later inventory holds the state word `changed`, padded with underscores to 64
  characters, instead of zeros. The receipt check (`verify_state_receipt`)
  classifies every entry of the home from the two inventories by comparing
  entries, so it needs to know whether a digest differs, not its value. The
  receipt (`before.entries_sha256` and `after.entries_sha256` of each shared
  entry, `changed_directories`) is recomputed from the public inventories and
  holds the same zeros and words. The allowlist of the sanitizer has no entry for
  them any more. The remaining allowlisted digests are `schema_sha256` of the
  stores (the digest of the vendor schema, which a reader recomputes from the
  public schema export with `store_schema_sha256`; no operator value). The
  digests `before_inventory_sha256`, `after_inventory_sha256` and
  `metadata_sha256` are digests of the public inventories and are recomputed by
  the replay;
- a path component with a hex run of 32 or more characters is aliased also
  when the receipt classifies its entry. Set v1 kept two such names: the Codex
  cache files `cache/codex_apps_tools/<40 hex>.json` and
  `cache/codex_apps_server_info/<40 hex>.json`. The name is the SHA-1 of a JSON
  document with the account id and the user id of the Codex login, so it
  identified the account. The directory name under `cache/control-ui-assets/`
  (64 hex) is aliased by the same rule.

Not in a packet at all: the Codex rollout, the shell snapshot (it holds the
owner's shell environment), the lock, the temporary run files, the gateway
output.

**Machine values that stay.** This section is read from the public inventories
(`state-before.json`, `r2-state-after.json`) and receipts of the three packets,
not from the intent of the sanitizer. `scripts/tabulate_openclaw_machine_values.py`
writes the tables below, and a test compares them with the public set. `all`
means every entry of the kind keeps the number, `none` means every entry has it
blanked, `n/a` means the entry has no such field. A number counts as blanked when
it is a 1 followed by zeros: a blanked number keeps its digit count, so a blanked
size keeps its order of magnitude. `entry_count` is never blanked. Where the
count of entries differs between runs, the six counts are given in the order run
04 before, run 04 after, run 06 before, run 06 after, run 07 before, run 07
after. `root_identity` (device and inode of the home directory) is real.

| Entry in the inventory | Entries | `device` | `inode` | `size_bytes` | `entry_count` | `mtime_ns` | `ctime_ns` | `birth_ns` |
|---|---|---|---|---|---|---|---|---|
| digested root, in a receipt class | 2 | all | all | all | all | all | all | all |
| digested root, in no receipt class | 6 | all | all | all | all | all | all | all |
| directory in a receipt class | 36 | all | all | all | n/a | all | all | all |
| directory above a classified entry, in no class | 28 | all | all | all | n/a | all | all | all |
| link in a receipt class | 11 | all | all | all | n/a | all | all | all |
| link in no class | 4 | all | none | none | n/a | none | none | none |
| file in a receipt class | 120/122/119/121/120/122 | all | none | none | n/a | all | all | all |
| session-store file in no class | 2 | all | none | none | n/a | all | all | all |
| every other unclassified entry | 9409/9409/9414/9414/9415/9415 | all | none | none | n/a | none | none | none |

| Receipt row (`classes.shared_changed`, before and after) | Values | `size_bytes` | `entry_count` |
|---|---|---|---|
| directory | 25/2/25/2/25/2 | all | n/a |
| directory-digest | 2/2/2/2/2/2 | all | all |
| file | 120/115/119/114/120/115 | none | n/a |
| symlink | 11/11/11/11/11/11 | all | n/a |

Read the first table as follows.

- `device` is one value for every entry.
- The 8 digested roots, the 36 classified directories, the 28 directories above
  a classified entry that are in no class, and the 11 links of a receipt class
  keep inode, size and the three times. Only the digests of a digested root are
  zeros or the word `changed`.
- A file of a receipt class keeps the three times; its inode and size are
  blanked. The 2 session-store files that are in no class (`state_5.sqlite` and
  `thread_history_1.sqlite` of the Codex home) keep the three times too; their
  inode and size are blanked.
- The 4 links that are in no class and every other unclassified entry keep only
  `device`.
- In the receipt, a row of `classes.shared_changed` keeps `size_bytes` for
  directories, links and digested roots, and blanks it for files. `entry_count`
  stays only for the 2 digested roots in that class. The lists `session_owned`,
  `run_owned` and `directories_changed` hold paths only.

These values are stable for the host. The same entry has the same `inode` and
`birth_ns` in the three packets (checked for all 31 kept entries that have both
values in all three; `mtime_ns` differs for 13 of them), so a reader can tell
that packets come from the same machine and the same home directory. A reader
can also compare them with another capture of this host. They hold no content,
no account id and no name. They stay as a disclosed limit of v1.

**`mirrorSourceFingerprint`.** The value is the first 32 hex of a SHA-256 of
the mirrored message. For an assistant row the preimage is the compact JSON of
`role` and `content`; for a tool result row it also has `toolCallId`,
`toolName` and `isError` (the reviewer of set v1 found both rules). A value
over content with the home path would confirm a guessed home name. For a user
row no rule was found from public or private bytes, so its preimage cannot be
shown to be public. All values are zeroed.

**The compressed transcript event** (the first prompt) holds one such value
of a user row. Set v1 left it in place. Now the sanitizer decompresses the
BLOB, zeroes the value (the text keeps its length, so `event_utf8_bytes` stays
right), compresses the text again, and appends a Zstandard skippable frame of
zeros so that the BLOB has its old byte length (`recompress_event`). The new
data frame is 27 to 31 bytes shorter than the old BLOB in the three runs; the skippable frame
fills the difference. A Zstandard reader skips that frame. The public BLOB is
so not the vendor's bytes: it is a transformed frame with the same event text
except the 32 zeroed characters. Row size, density and all 31 rows do not
change. The sanitizer also fails when the event holds the home path, the home
name or an e-mail address.

**Never published.** `summary.json` and `*-private-transformation.json` beside
the packets, and the whole private set `openclaw-score-replay-v6`. The private
transformation files hold digests of the private files; with the short alias
of the home name such a digest confirms a guess.

The owner's workspace files (AGENTS, SOUL, HEARTBEAT and others), the heartbeat
prompt and message channel ids are not in the tables of the read. The system
prompt is not stored (`[Oversized diagnostic JSON redacted]` in the trace).
The name of the workspace directory (`clawd`) stays: it is in every path of
the fixture.

Checks at the end: the 31 metric rows are equal before and after; tamper
controls pass; no home name (in six encodings), no e-mail address, no account
id, no repository address in any file; no phrase of a blanked instruction text
in any file (`require_no_instruction_phrase`); no short hex value under a
digest-like key that public bytes do not yield; `check_public_packets` passes,
including its check of long hex runs: a hex run of 32 or more characters that
is not a 64-hex digest must be zeros, derivable from public bytes, a provider
id (`call_…`, `msg_…`), or on the written list `HEX_ALLOWLIST` (one rule: the
bytes of the rewritten Zstandard frame).

## Probes and captures of 2026-10-07

All in `artifacts/v1-expanded-preparation/live-captures/`. None is scored.

| Probe | State | What happened |
|---|---|---|
| `openclaw-2026-10-07-probe-01` | isolated (`agent exec --state-dir`) | No model turn. `--model` rejected by the owner's model policy |
| `-probe-02` | isolated | No model turn. The `codex` plugin did not start (app server timeout; missing plugin SDK export) |
| `-probe-03` | isolated | No model turn. The plugin ran with its own empty Codex home: `401 Unauthorized`. The isolated state shows the store schemas and the rollout file name |
| `-probe-04` | isolated, then owner state | `r1`: `401` again. `r1c`: one model turn in the owner's state (session `5a020166-…`); the fixture was not in the agent workspace |
| `-probe-05` | owner state | Two model turns in one session (`77f57624-…`); `OPENCLAW_WORKSPACE_DIR` was ignored, the fixture was not found |
| `-probe-06` | owner state | Two model turns in one session (`9686dc79-…`) with the fixture in the workspace. Complete run: helper `inspect` 0, `baseline` 1, `final` 0; both canaries |

| `-probe-07-acp` | owner state, gateway + ACP | One model turn. Proof that the ACP stream has `tool_call` and `tool_call_update` |

Captures with the controller:

| Capture | Route | Model turns | Outcome |
|---|---|---|---|
| `openclaw-2026-10-07-02` | `local` | 2 | Complete in one pass. 142 rows. No tool events for the observer |
| `openclaw-2026-10-07-03` | `acp` | 2 | Both turns and both streams complete; gateway stopped; fixture removed. The native bracket was refused: the row export met `acp_replay_events`, whose `session_id` is the ACP session id, and the guard took it for another session. After the fix (the ACP session id is an identity) `--rebracket` took the native state from the unchanged home: 204 rows, 3 session-owned files. No extra model turn |
| `openclaw-2026-10-07-04` | `acp` | 2 | Complete in one pass with the fixed controller: 167 rows, 3 session-owned files, receipt verified, observer built with four scored tool calls. Repetition 1 |
| `openclaw-2026-10-07-05` | `acp` | 1 | Invalid: turn 1 ended with no `agent_message_chunk`. Not used; replaced by `-07` |
| `openclaw-2026-10-07-06` | `acp` | 2 | Complete in one pass. Repetition 3 |
| `openclaw-2026-10-07-07` | `acp` | 2 | Complete in one pass. Repetition 2 |

### Replaced attempts

Two scheduled attempts on the route `acp` are not in the row; each was
replaced by a later run. The rubric rule "Do not replace a scheduled run with
calibration or a corrective attempt" (and `protocol.md`) forbids this. The
owner accepted the replacements on 2026-10-07. An outside review of candidate
v38 named the breach. On 2026-10-08 the owner waived the rule for v1: the row
is ranked. The waiver was made after the results were known, and it is
disclosed in the report.

- `-03` (scheduled as repetition 1) was replaced by `-04`. Both turns and both
  streams were complete. The native bracket was refused by a fault of a
  controller guard, and the native state was taken afterwards by `--rebracket`
  from the unchanged home. The reviewer of set v1 decoded its private rows
  with an own decoder: the same facts as run 3 (17 events, 26485 useful
  bytes), so about 91.11 at that time (88.11 now). Leaving it out does not raise the score.
- `-05` (scheduled as repetition 2) was replaced by `-07`, which ran after
  `-06`. In `-05` turn 1 ran both helpers and ended with `end_turn` and no
  message chunk: the visible response was missing on the ACP stream. The
  controller's canary check stopped the capture before any score. Its native
  state was not taken, so the native record of that session is unknown. So one
  of four ACP sessions of the fixed controller lost its visible response.

What stays in the owner's stores after these runs: the sessions and Codex
threads of probes 04 to 07 and of the three captures (rows in the agent store
and the Codex stores, one rollout file and one shell snapshot per thread); on
the route `acp` also the ACP replay rows, audit rows and one worker placement
row per session in `state/openclaw.sqlite`; gateway log files. A later capture
does not read them: their rows are counted, and their rollout files are old
files that must not change. Nothing is installed: no service, no LaunchAgent.

## The first live capture had to confirm (all held in `openclaw-2026-10-07-02`)

1. `openclaw config get agents.defaults.workspace` prints only the path on
   stdout, and writes nothing that matters (it runs before the first inventory).
2. `openclaw --version` prints exactly `OpenClaw 2026.9.8 (fc23bc8)`.
3. The process check finds no OpenClaw process when none should run, and no
   false match.
4. `~/.openclaw` is an ordinary directory, not a link (else pass
   `--openclaw-home` with the real path).
5. The rollout file name carries the thread id of the envelope (seen in the
   isolated probe 03, not yet in the owner's state).
6. The rule table explains every changed entry of the real home. A refusal
   costs the model turns of the run; `r<N>-refusal.json` then lists what to add.
7. The agent store and the two Codex stores hold exact rows of the session in
   the owner's state, as they did in the isolated state.
8. The metadata walk of the home and of the workspace is fast enough, and the
   home comes to rest within eight reads (0.5 s apart).

## Limits

- Not isolated: the run uses the owner's configuration, skills, workspace
  files and memory. The system prompt holds the owner's injected workspace
  files. The result describes this install, not a clean one.
- The model is the owner's default, not a pin of the benchmark.
- Route `local`: no tool events in the observer. Route `acp`: tool events, but
  no tool output text, no model per response and no token counts per request
  in the stream.
- Route `acp`: one native receipt for both turns. A gateway of the owner's
  install runs for about one minute; it loads the owner's plugins. Channels,
  timed jobs, mail watcher, browser and canvas hosts, announcement and update
  are switched off by environment variables, and the gateway log confirms the
  first two. The capture cannot prove that no plugin did anything else.
- Route `acp`: the session id comes from a native row (the row of the
  controller's own session key). The observer does not use it.
- A shared entry that changed may hold records of the session. Its content was
  not read, so a receipt does not prove that it holds none. The deny rule can
  hide session rows in a table whose column names look like secrets.
- Rows that hold an id only inside JSON text are counted, not exported.
- The session and its Codex thread stay in the owner's stores after the run.
- The temporary store copies are deleted, not wiped.
- The scored record is OpenClaw's own store. The Codex rollout of the thread is
  not decoded and not compared with the transcript.
- The family has known gaps: tables that the deny rule never reads, and rows
  that hold an id only inside a longer value (see *The native family and the
  read*). Only `attribution.reconciliation` is resolved as an absence, and it
  rests on the rows of the session, not on these gaps; density misses their
  bytes.
- One model, one build, one day, one machine.
