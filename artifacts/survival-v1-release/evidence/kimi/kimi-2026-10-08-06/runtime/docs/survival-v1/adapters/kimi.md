# Kimi Code adapter

Status: three isolated two-turn captures, a decoder, a stream observer, private
score replay packets and sanitized public candidates. The producer does not
review its own packets. This document asserts no rank.

Code: `session_bench/kimi_survival_capture.py` (controller),
`session_bench/kimi_stream_observer.py` (observer),
`session_bench/kimi_wire_records.py` (decoder, duplicate safety, density),
`session_bench/kimi_format_evidence.py` (broad evidence),
`session_bench/kimi_score_inputs.py` (packet checks),
`scripts/build_kimi_score_replays.py`, `scripts/sanitize_kimi_score_packets.py`,
`scripts/build_kimi_public_inputs.py`.

## Route

- Harness: Kimi Code CLI `kimi` 2.1.1 (`@moonshot-ai/kimi-code`), headless:
  `kimi -p <prompt> --output-format stream-json -m moonshot-ai/kimi-k2.7-code
  --skills-dir <empty directory>`. Turn 2 adds `-S <session id>`.
- Provider: Moonshot API (`api.moonshot.ai`), model `kimi-k2.7-code`, model alias
  `moonshot-ai/kimi-k2.7-code`. The key is read in process from the operator's
  normal config and passed to the child in one environment variable. It is in no
  file of a capture.
- Isolation: each attempt makes a new scratch directory with four new
  directories: `kimi` (`KIMI_CODE_HOME`), `home` (`HOME`), `empty-skills` and
  `workspace` (a copy of the workload fixture). Before turn 1 the Kimi home holds
  one file, a config file that the controller writes (no key in it).
- One CLI call for each turn. Turn 2 starts 65 s after turn 1: the account allows
  3 model requests a minute. The turn time limit is 600 s.
- After each turn the controller lists the whole Kimi home (path, size and
  SHA-256 of each file), copies the session directory, lists the copy, and
  compares the two lists.

## Captures

| Attempt | Use | Result |
|---|---|---|
| `kimi-2026-10-08-01` | repetition 1 | captured; 14 model requests, 7 of them failed with a rate limit and were tried again by Kimi |
| `kimi-2026-10-08-02` | repetition 2 | captured; 7 model requests |
| `kimi-2026-10-08-06` | repetition 3 | captured; 6 model requests |
| `kimi-2026-10-07-01` | not used | turn 1 complete; turn 2 exit code 1. The controller forced one attempt for each step (`KIMI_LOOP_MAX_ATTEMPTS_PER_STEP=1`), so the first provider rate limit ended the turn |
| `kimi-2026-10-07-02` | not used | the same fault as `-01` |
| `kimi-2026-10-08-03` | not used | turn 1 exit code 1 after 13 model requests: the account limit of 3 requests a minute lasted longer than the 10 attempts of Kimi |
| `kimi-2026-10-08-04` | not used | turn 1 complete; turn 2 exit code 1 after the same limit (15 model requests in all) |
| `kimi-2026-10-08-05` | not used | turn 1 exit code 0, but the final response does not end with the required canary; the controller stopped (2 model requests) |

The row uses every capture that completed both turns. No completed capture was
left out. The five attempts of 2026-09-29 used an earlier controller and never
completed two turns (see `expanded-preparation-status.md`).

The three runs are the first, second and sixth attempt of one day. The attempts
between them failed and were replaced. The rubric rule "Do not replace a
scheduled run with calibration or a corrective attempt" is waived for v1 by owner
decision of 2026-10-08, and the waiver is disclosed in the report. The plans hold
no repetition number; the builder gives the numbers 1, 2, 3 in the order of the
attempts.

Retries. When the provider answers with a rate limit, Kimi waits and sends the
request again (up to 10 attempts). The failed request stays in the native record
as a step of its own: `step.begin`, `llm.request`, `step.end` with `finishReason`
`error`, and a `turn.step.retrying` record with the error text. These records
hold no message, no tool call and no result. The decoder counts them
(`retried_requests`) and takes no fact from them. They state no event, so they
are no duplicates. In density they are `metadata` (unclassified). A failed step
that holds content is outside the contract and makes the decode unclean. The
stdout stream shows a retry as a `meta` line; the observer counts it and takes no
event from it. Repetition 1 has 7 such requests, all after the last tool call
of turn 2.

Failed turns. When the attempts are used up (or only one is allowed), the turn
ends with an error. Kimi then writes one `turn.step.interrupted` record
(`turnId`, `step`, `reason`, `message` = the error text), `turn.ended` with
`reason` `failed` and an `error` object, and `agent.turn.ended` with `outcome`
`failed`. The turn has no response. The records before the error stay as they
are. `turn.step.interrupted` states no event; it is `metadata` and no
duplicate. No scored run has a failed turn. Four unused attempts have one
(`kimi-2026-10-07-01`, `-02`, `kimi-2026-10-08-03`, `-04`).

## Native format

Kimi writes one directory for each session:
`KIMI_CODE_HOME/sessions/<workspace-id>/<session-id>/`. The workspace id is
`wd_<name of the working directory>_<first 12 hex of the SHA-256 of its path>`.
The session id is `session_<uuid>`.

| File | Content |
|---|---|
| `agents/main/wire.jsonl` | the wire log: one JSON object for each line, written in order, never rewritten |
| `state.json` | one JSON object: `id` (session id), `version` (2), `cwd`, `createdAt`, `updatedAt`, `lastTurnReason` |
| `logs/kimi-code.log` | one text line for each model request (times, token count, errors) |
| `notify/state.json` | `{"enabled":false}` |
| `agents/main/file-history/<key>@v<n>` | a whole copy of an edited file; the key is the SHA-256 of the relative path |

Every wire record has `type` and `time` (Unix milliseconds, UTC). The first
record is `metadata` with `protocol_version` (`1.5`). Record types of the read:

- `turn.prompt`: a submitted prompt: `turnId` (a number from 0), `promptId`,
  `input` (text parts), `origin.kind` `user`.
- `context.append_message`: a message put into the model context. With
  `origin.kind` `user` it is the prompt again. With `origin.kind` `injection` it
  is text that the harness adds; that field marks it, and it is not a prompt.
- `context.append_loop_event`: one event of the agent loop, in `event`:
  `step.begin` and `step.end` (`uuid` of the step, `turnId`, `step`; the end
  holds `finishReason`, `usage` and the provider `messageId`), `content.part`
  (`think` or `text`, with `stepUuid`), `tool.call` (`toolCallId`, `name`,
  `args`, `stepUuid`, `uuid`) and `tool.result` (`toolCallId`, `parentUuid` =
  the `uuid` of its call, `result.output`, and `result.isError: true` only on a
  failure).
- `llm.request`: one model request: `turnStep` (`<turnId>.<step>`), `model`,
  `modelAlias`, `provider`.
- `agent.message.appended`: written when a turn ends: each message of the turn
  again (the prompt; each assistant message with its think and text parts and
  its tool calls; each tool result).
- `file_history.tracked`, `file_history.checkpoint`: the path of an edited file
  (relative to the session `cwd`) and the `contentHash` (SHA-256) of its copy
  before the first edit of the turn and at the end of the turn.
- `turn.step.retrying`, `turn.step.interrupted`: a model request that Kimi
  tries again, and a turn that ended with an error (see "Captures").
- `usage.record`: the usage of one model request (`model`, `usage`,
  `usageScope`). `usageScope` is `turn` in every record of every capture. It
  names the scope that the request belongs to. The record is not a total: each
  one equals the `usage` of one `step.end`.
- `token_counting.measured` (input + output + cache read of one request) and
  `token_counting.turn_recorded` (the last such value of the turn: the size of
  the context, not a sum of usage).
- `turn.ended` (`reason` `completed` or `failed`), `llm.tools_snapshot` (the
  tool definitions), `profile.bind` (the system prompt and the `cwd`), and
  bookkeeping records (`runtime.set_binding`, `permission.set_mode`,
  `plugin.session_start`, `agent.turn.started`, `agent.turn.ended`,
  `prompt.completed`).

Decoder contract. The contract is the 22 record types above (`RECORD_TYPES`)
and the five loop event types. A record of another type makes the decode
`unsupported`.

## Family, root and read

Decided on 2026-10-08, after the files of the captures had been seen and before
any score was computed.

- **Family and root.** The session directory. The packet holds every file of it
  as copied after turn 2 (6 files). The copy equals the source by SHA-256, and the
  wire log of turn 2 starts with the bytes of the wire log of turn 1.
- **The read.** Two containers: `wire.jsonl` (every event, order, relation,
  model, usage, changed file) and `state.json` (the session id; the wire log does
  not hold it). The decoder opens no other file.
- **Outside the read**, never opened by the decoder: `logs/kimi-code.log`,
  `notify/state.json` and the two file copies. Density counts each as one record
  of its size with the role `metadata`. They restate no call and no result in
  full (a file copy holds one text argument of the edit, not both).
- **Other choices and their effect.** Without `state.json` the row has no session
  id: `broad.self_contained_identity` (2) and `broad.thread_structure` (3) fail.
  `state.json` states no event, so it changes no duplicate count. There is no
  leaner read that avoids the duplicates: the first statement and the copy of
  every event are in the same file.

Files of the isolated home outside the session directory. The controller listed
them (size and SHA-256) and did not copy them.

| Files | What the packet proves |
|---|---|
| `session_index.jsonl`, `workspaces.json`, `file-history/<workspace-id>` | Rebuilt from values of the capture (session id, paths, times) and proved by the SHA-256 of the listing (`inputs/home-index.json`). They hold ids, paths and times only |
| `sessions/.index-dirty/<session-id>.<time>` and the unused `db.wal` files | Size 0 in the listing |
| `sessions/.index-cache/scan.json`, `cache/query-store/*` | Not copied; names and sizes in `home_files_not_copied` of the capture assertion. In a whole-home copy of the same build (`kimi-2026-09-29-01`, not a row run) they hold a copy of the `state.json` fields (id, workspace, `cwd`, times, `lastTurnReason`) and a session counter |
| `config.toml`, `device_id`, `logs/kimi-code.log`, `updates/*` | Not copied. Config of the controller, a random device id, a start log, the update check |

## Observer

Inputs: the `-p` argument of each launch receipt (the prompt), the stdout stream
of each turn, the helper ledger `.survival-observer.jsonl` that the frozen helper
writes, and the workspace copies of `checkout.py`. No native file.

The stream is JSON lines. What it holds:

- each tool call: `id`, name, arguments (JSON text), in order;
- each tool result: the call id and the result text;
- the final response text (the last assistant line);
- `meta` lines: the CLI version, a retry notice, and the session id.

What it lacks: a status field and an exit code field (a failed shell call ends
with the text line `Command failed with exit code: N.`; a successful call has no
such line); times; the model; usage; reasoning text; the prompt.

Rules:

- A helper action: a `Bash` call whose command is one frozen helper invocation.
  Arguments, directory and call id come from the stream. Its result takes the
  output line, the nonce and the exit code from the helper ledger. The stream
  text must hold that line and must agree with the exit status, else the observer
  fails.
- The edit: one `Edit` or `Write` call on `checkout.py` in turn 2. The stream
  has no status; the observed status `success` rests on the changed hash of the
  workspace file. More than one such call has no rule and fails. The observer
  keeps the arguments of the edit (path, old and new text, or the whole new
  content) in the field `input`. The shared comparator relates an observed action
  to a native one by id, turn, shell argv, path, working directory and kind. For
  no row does it compare edit text or other action arguments. The observers of
  Hermes, Cursor CLI, Codex CLI and OpenClaw (envelope) keep only id, target,
  kind and turn for an edit, as the Kimi observer did. The observers of OpenClaw
  (ACP: `changes`), OpenCode and Antigravity (`before_fragment` and
  `after_fragment` of the file change) keep edit text, and nothing compares it
  with the native text. The same holds for the other rows that use the comparator
  (Claude CLI, Claude Desktop, Copilot, DeepSeek Harness, Pi). So
  `kimi_score_inputs` makes the check itself: `check_observed_edit_arguments` fails the replay when the
  stream arguments of an edit differ from the arguments of the native tool call
  with the same call id, when that call id is not in the native record, or when
  the observed edit has no arguments (a missing or empty `input` fails; the
  check skipped such an edit until set v3). The replay also fails when the check
  compared no edit although the workload instance has a scored edit. A test
  changes one argument, removes the arguments and removes the edit, and shows
  each failure. The check changes no metric row of the real captures.
- Compound shell call rule: a `Bash` call with two or more scored segments is
  one action for each helper segment; exit code and output line of each come from
  the ledger. No run has such a call (repetition 3 sends two separate calls in
  one model step).
- Every other call (a `Read` in each run) is an unscored action with its call id.
- The call ids of the stream are the `toolCallId` values of the native record.
  The comparator pairs by id.
- Model: the `-m` argument of the launch receipt (model alias) and the plan
  (provider); the model id is the alias without the provider prefix.
- The observer document has no time: the stream has none.

Tier A needs observed tool events. The stream gives all four scored actions with
arguments and all four results in order, for each run.

## Metrics

Points for each run when the row holds. `m` measured, `a` native_absent.

| Metric | State | How it is decided |
|---|---|---|
| `work.submitted_turns` 4 | m 2/2 | `turn.prompt` text equals the submitted prompt |
| `work.visible_responses` 4 | m 2/2 | the `text` part of the step with `finishReason` `end_turn`; turn by `turnId`; status `completed` from `turn.ended`; response marker of the text |
| `work.actions` 5 | m 4/4 | `tool.call`: name, `args`, directory, `toolCallId`; paired with the observed call by id |
| `work.results` 5 | m 4/4 | `tool.result` of the same `toolCallId`: output line, nonce, status, exit code (see call 1) |
| `work.changed_files` 4 | m 1/1 | `file_history.tracked` and `file_history.checkpoint`: path and both SHA-256 values, explicit in the wire log |
| `revision.r1`, `r2`, `r1_r2_order` 2+2+2 | m | the two prompts and their line order |
| `revision.final_after_r2` 2 | m | rising line order inside turn 2: prompt, edit, edit result (success), final helper, its result (exit code 0), response; results bound by `toolCallId` |
| `causal.action_result` 7 | m 4/4 | `toolCallId`, and `parentUuid` = `uuid` of the call |
| `causal.turn_response` 6 | m 2/2 | `turnId` of the text part and of `turn.prompt` |
| `attribution.model_config` 3 | m 2/2 | `llm.request` of the response step, joined by `turnStep`: `model` and `modelAlias` equal the observed values |
| `attribution.usage` 5 | m 2/2 | `usage` of the `step.end` of the response step (same step uuid as the text part): `inputOther` and `output` |
| `attribution.token_semantics` 4 | m 2/2 | the same record: `inputCacheRead` and `inputCacheCreation`, each under its own key. `inputCacheCreation` is `0` in every record of every capture: the key is written, a cache write was never reported |
| `attribution.reconciliation` 3 | a | no record of the session directory declares a session total or a turn total of usage. `usage.record` holds the counts of one request; `token_counting.*` holds the size of the context |
| `portable.complete_root` 3 | m | the session directory: listed with SHA-256 in the isolated home, copied, equal (see call 6) |
| `portable.companions` 2 | m | the five other files of the session directory are in the packet |
| `portable.isolated_decode` 4, `canonical_equality` 3 | m | the replay decodes the packet copy with no vendor program and no network |
| `broad.readable_rationale` 4 | m 2/2 | native response text equals the observed text |
| `broad.thread_structure` 3 | m | explicit: each event names its turn by `turnId`, a result its call by `parentUuid` |
| `broad.standard_tools_readable` 2 | m | JSON lines and one JSON file |
| `broad.documented_format` 2 | m | this document |
| `broad.self_contained_identity` 2 | m | session id from `state.json`; harness and surface from the first sentence of the system prompt in `profile.bind` |
| `broad.declared_format_version` 2 | m | `protocol_version` of the first wire record and `version` of `state.json` |
| `broad.event_timestamps` 2 | m 13/13 | `time` of the wire record of each observed event; the changed file takes the time of its checkpoint record |
| `broad.honest_version_signal` 2 | m | the declared values equal the decoder contract (`1.5`, `2`) |
| `broad.observed_schema_stability` 3 | m | every record of the run has a type of the decoder contract. The window is build 2.1.1 on 2026-10-07 and 2026-10-08: all eight attempts of these days decode clean (see "Schema-stability window") |
| `broad.stable_root_location` 2 | m | isolated discovery: a new home, one session directory, the session id from the stdout stream; one row for each run |
| `broad.naive_reader_duplicate_safety` 3 | a 0/14 | 14 events, 30 statements: each prompt 3 times (`turn.prompt`, `context.append_message`, `agent.message.appended`), each response, call and result twice (loop record and `agent.message.appended`). No field marks a copy |
| `broad.classified_content_density` 3 | m | about 7.4 to 8.2 percent useful (0.22 to 0.24 points). The two tool snapshots and the system prompt are 163 kB of about 215 kB |

Density roles: `turn.prompt` is `user_message`; a `text` part `assistant_message`;
a `think` part `explanation`; `tool.call` and `tool.result` their roles; the two
`file_history` records `file_change`; `profile.bind`, `llm.tools_snapshot` and
injected messages `system`; `metadata` is `session`; `state.json` is `session`;
every copy (`agent.message.appended`, the context copy of a prompt) is
`snapshot`; all other records are `metadata`.

Schema-stability window. The window is one build (CLI 2.1.1, wire
`protocol_version` 1.5) on two days: 2026-10-07 and 2026-10-08. It holds eight
captured sessions: the three scored runs and the five unused attempts. The
decoder was run on every native copy of every turn of all eight (14 copies).
Each decodes with status `ok`, no diagnostic and no record of an unknown type;
a failed turn decodes as a turn without a response. A test repeats this check
(`test_every_capture_of_the_observation_window_decodes_under_the_contract`), and
a sanitized fixture of one failed session is in `tests/fixtures/kimi_failed_turn`.
The evidence of a packet holds one observation: its own run, on the day of its
session start. The unused sessions are in no packet. Set v1 did not have
`turn.step.interrupted` in the contract and was rejected for it; no row changed
with the fix. The two sessions of 2026-09-29 that hold a wire log state the same
`protocol_version` and are outside the window.

Response matching. The comparator matches a response by marker, turn, role and
status. The rationale and timestamp evidence also need the full observed text,
and it is equal in all six responses.

Usage join. The usage record is on the step of the response (step uuid), not
joined by turn alone. It is the usage of one model request. A turn has more
requests; their records are in the wire log and nothing sums them.

## Judgment calls

Each is a reading that a reviewer can refuse. Effect in points for each run.

1. **Exit code 0 of a successful shell call.** A failed call has `isError: true`
   and the line `Command failed with exit code: N.`; the decoder reads N and takes
   the text before that line as the output. A successful call has no `isError`
   and no code. The decoder reads it as status `success`, exit code 0. Cursor CLI
   and Claude Code are read the same way. If refused: inspect and final results
   lose credit: `work.results` −2.5, `causal.action_result` −3.5,
   `revision.final_after_r2` −2, `broad.event_timestamps` −0.3; total about −8.3.
2. **Input count.** `inputOther` is the count of input tokens that were not read
   from the cache. The decoder takes it as the input count. If refused:
   `attribution.usage` −5 and `attribution.token_semantics` −4.
3. **Response marker.** A native response gets its marker from its own text: the
   one `SB_SURVIVAL_V1_RESPONSE_` token, at the end. Without this field the
   comparator takes the first `SB_SURVIVAL_V1_` token of the text. In
   repetition 3 the final response names the run canary of a command before its
   marker, so that response would not match: about −13 in repetition 3, nothing in
   the other two.
4. **Version.** `protocol_version` is taken as the version of the wire format.
   Only one value was seen. If refused: `broad.declared_format_version` −2 and
   `broad.honest_version_signal` −2.
5. **Harness name.** The only place that names the harness is the first sentence
   of the system prompt. The sanitizer keeps that sentence (15 words). It is a
   kept fragment of vendor instruction text: the third one, beside the two that
   the handover document names (the operating system line of the Copilot system
   prompt, and `snapshot.platform` of a Claude CLI attachment). The reason is the
   same: one metric reads it. It is kept as a disclosed exception, listed in the
   report notes. If it were removed: `broad.self_contained_identity` −2.
6. **Root.** The root is the session directory. The home also holds index and
   cache files that name the session and were not copied. Three are proved by
   rebuild; `scan.json` and the query cache are not. If a reviewer counts them
   as part of the root: `portable.complete_root` −3, and
   `attribution.reconciliation` is then `unresolved` (the row becomes
   provisional), because the absence of a usage total rests on a complete root.
   The controller kept the scratch directories (`scratch_retained` in the plan).
   If they still exist, a copy of these files closes the gap.
7. **Changed file.** The two `contentHash` values are read as explicit SHA-256
   hashes of the file before and after. The decoder does not open the file
   copies; a test checks that the copies have these hashes. If refused: −4. A
   second native route exists (the file source in the inspect result and the
   `Edit` arguments) and is not used.
8. **Reasoning text.** A `think` part is `explanation` (useful). As `metadata`
   density falls by about 0.08 points.
9. **Model.** The model of a response is `llm.request` of its step
   (`turnStep`). `agent.message.appended` names only `agent-loop`. If the join
   by `turnStep` is refused: −3.
10. **Format document.** As for the other rows, the format document is this
    adapter document in the packet. If refused: −2.
11. **Retry records** are `metadata`, and the two `file_history` records are
    `file_change` (622 bytes; under 0.01 points).

Not a judgment call: duplicate safety is 0 of 14 under any reading, and
reconciliation is 0 points as `native_absent` or as a failure.

## Public packets

`scripts/sanitize_kimi_score_packets.py` writes the public set from the private
packets. Every file keeps its byte length. The 31 metric rows, the duplicate
evidence and the density records are equal before and after.

Changed (counts for the three packets):

- Home user name and home path: 3 places (the path of the operator's normal
  config file in each plan).
- Wire log, vendor instruction text blanked with
  `[vendor instruction text removed at equal byte length]`: the system prompt
  after its first sentence (3), every `description` of the tool definitions,
  schema descriptions included (660), every injected message (9).
- Wire log: `hash`, `systemPromptHash`, `toolsHash` zeroed (60). They are
  digests of the blanked text.
- Wire log: the time zone name and `localDate` of the injected date message
  blanked (3 each). The date, set against the UTC times of the records, would
  bound the UTC offset of the operator's clock. No metric reads either value.
- Organisation id and key id of the provider error messages aliased
  (`org-XXXX`, `ak-XXXX`): 42 values, in the wire log, the session log and the
  stdout stream of repetition 1. In a failed session the same text is also in
  `turn.step.interrupted` and in `error` of `turn.ended`; the sanitizer aliases
  it there too (no such session is in a packet).
- Plans: the digest of the operator's config file and of the config file of the
  isolated home zeroed (6).
- Launch and exit receipts: the digest of every listed file that is not in the
  packet, not a rebuilt index file and not empty is zeroed (180).
- A final guard takes prose phrases from every blanked text and fails when any
  file of the public set still holds one.

Digests and hex values. `check_public_packets` passes with no unlisted value.
Allowlisted with a reason: the SHA-256 of the vendor package file in each plan
(3). Proved by a rule: the provider message ids `chatcmpl-<24 hex>` (40): the
first 8 hex are a Unix time within a day of the `time` of the record. The other
16 hex are not random: 12 hex that repeat across requests and sessions (a
provider server) and a 4-hex counter. The rule proves the time part only. The
workspace id and the file-history key are derivable from public strings.

Machine identifiers. The receipts hold no device, inode, birth time or count of
a private root. What stays is of the isolated home only: file names and sizes,
the scratch directory name (random letters), the capture times.

Not in the packets: stderr and native files of the unused attempts; the files of
the home outside the session directory.

What stays readable: every wire record and key; the first sentence of the system
prompt; tool names and schemas without descriptions; prompts, reasoning text,
responses, tool calls, results; model, usage, times; step, prompt, tool call and
provider message ids; the state file; the session log; the two file copies; the
stdout streams, the helper ledgers, the workspace copies, the launch and exit
receipts and the controller source.

Never publish: `kimi-score-replay-v1` (private packets), and beside the public
packets `summary.json` and `*-private-transformation.json`.

## Limits

- One build, one day, one model. The version reading rests on one value.
- The observer and the native record come from the same process. The stream is
  a second output channel, not instrumentation outside the harness. The helper
  ledger and the workspace files are independent of it.
- The stream has no status field; the observed exit status of a helper is from
  the ledger and from the text line of the stream.
- The rules of the read, of the exit code and of the root were written after the
  captures existed and before scoring.
