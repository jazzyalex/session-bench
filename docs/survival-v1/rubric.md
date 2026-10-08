# Session-Bench v1.0 rubric

Status: product contract, 2026-09-11. This rubric is additive to v0.4 and supersedes the
survival-only v1 draft. It authorizes no live collection or publication.

The public result is one `/100` score with five category bars. Its evidence is a fixed
31-metric matrix: 19 deep Session Survival metrics plus 12 broad format metrics. A metric
ID is stable and cannot be renamed or omitted by an adapter.

## Category budgets

| Category | Points | Evidence role |
|---|---:|---|
| Record fidelity | 30 | Deep recovery of turns, responses, actions, results, edits, and correction history |
| Causality & context | 20 | Deep causal joins plus readable rationale and navigable thread structure |
| Usage & attribution | 15 | Response identity, usage joins, token semantics, and reconciliation |
| Portability & openness | 20 | Complete copied archives plus independent readability, documentation, and identity |
| Durability & signal | 15 | Timestamps, version honesty, observed stability, root stability, duplicate safety, and density |

## Nineteen deep Session Survival metrics

These metrics use the controlled two-turn coding task and its independent observer.

| Category | Metric ID | Points | Population or assertion |
|---|---|---:|---|
| Record fidelity | `work.submitted_turns` | 4 | Exact R1 and R2 accepted turns in order; fixture denominator 2 |
| Record fidelity | `work.visible_responses` | 4 | Completed visible R1/R2 responses with turn and canary; denominator 2 |
| Record fidelity | `work.actions` | 5 | Observed action name, arguments, target, and turn; denominator 4 |
| Record fidelity | `work.results` | 5 | Observed output/status and exit state; denominator 4 |
| Record fidelity | `work.changed_files` | 4 | Project-relative path and before/after hashes; denominator 1 |
| Record fidelity | `revision.r1` | 2 | Exact original requirement retained; assertion 1 |
| Record fidelity | `revision.r2` | 2 | Exact correction retained; assertion 1 |
| Record fidelity | `revision.r1_r2_order` | 2 | R1 precedes R2 and R2 preserves its explicit superseding scope; assertion 1 |
| Record fidelity | `revision.final_after_r2` | 2 | Final edit, result, and response remain downstream of R2; assertion 1 |
| Causality & context | `causal.action_result` | 7 | Stable, ordered action→result relations; denominator 4 |
| Causality & context | `causal.turn_response` | 6 | Stable, ordered turn→response relations; denominator 2 |
| Usage & attribution | `attribution.model_config` | 3 | Each displayed response joins to model/config identity; denominator 2 |
| Usage & attribution | `attribution.usage` | 5 | Each displayed response joins to its usage record; denominator 2 |
| Usage & attribution | `attribution.token_semantics` | 4 | Input/output/cache fields are explicit and distinguish zero, null, missing, estimated, and billed; denominator 2 |
| Usage & attribution | `attribution.reconciliation` | 3 | Per-response values reconcile to the declared session total; assertion 1 |
| Portability & openness | `portable.complete_root` | 3 | Complete declared native root is inventory- and hash-verified; assertion 1 |
| Portability & openness | `portable.companions` | 2 | Every required sidecar, journal, and companion is bound and copied; assertion 1 |
| Portability & openness | `portable.isolated_decode` | 4 | Native-only decode succeeds without original roots, vendor executable, or network; assertion 1 |
| Portability & openness | `portable.canonical_equality` | 3 | Isolated and ordinary canonical reconstructions match; assertion 1 |

## Twelve broad format metrics

The broad checks keep v0.4's format-quality view inside the main score. They use the same
captured roots and public evidence but may also use declared documentation and a versioned
observation ledger.

| Category | Metric ID | Points | Operational rule |
|---|---|---:|---|
| Causality & context | `broad.readable_rationale` | 4 | Each displayed response has a reader-visible rationale or summary recoverable as ordered text without an LLM judge; denominator is displayed responses |
| Causality & context | `broad.thread_structure` | 3 | Session, turn, role, order, and parent/child boundaries are explicit or deterministically recoverable without title/timestamp guessing; recovery mode requires an ordered user-rooted sequence where each assistant/tool turn belongs to the nearest preceding user; assertion 1 |
| Portability & openness | `broad.standard_tools_readable` | 2 | The declared native record can be read using a documented commodity parser for JSON, JSONL, SQLite, or text, without vendor binary, account, backend, or network; assertion 1 |
| Portability & openness | `broad.documented_format` | 2 | Public or bundle-local documentation maps containers, record types, identities, joins, and version semantics sufficiently to rebuild the required facts; assertion 1 |
| Portability & openness | `broad.self_contained_identity` | 2 | The copied bundle itself identifies the session, harness, surface, and record family without original absolute paths or external lookup; assertion 1 |
| Portability & openness | `broad.declared_format_version` | 2 | A machine-readable format/schema version is present in or immutably bound to the native bundle; assertion 1 |
| Durability & signal | `broad.event_timestamps` | 2 | Required native events carry timezone-aware RFC3339 or numeric Unix seconds/milliseconds timestamps with declared units/time zone and recoverable order; denominator is required recovered events |
| Durability & signal | `broad.honest_version_signal` | 2 | The declared version distinguishes incompatible schemas/semantics and matches the observed decoder contract; assertion 1 |
| Durability & signal | `broad.observed_schema_stability` | 3 | All captured records in the current declared build/date observation window decode under the advertised contract; each observed build/date and exception is included. This stable ID measures captured-window decoder-contract compatibility, not longitudinal stability; assertion 1 |
| Durability & signal | `broad.stable_root_location` | 2 | The complete root for the synthetic session is documented or deterministically discoverable from one run by isolated or metadata-safe discovery, without personal-history scanning; assertion 1. Each additional supplied run must satisfy the same discovery and privacy assertions and have a sequential repetition number. Run-specific root paths may differ; the retained source evidence establishes the discovery pattern. A second run tests repeatability; it is not required to score the first run. |
| Durability & signal | `broad.naive_reader_duplicate_safety` | 3 | One documented forward read yields each required semantic event once, or supersession/tombstone fields make exact deduplication possible without vendor-specific heuristics; denominator is required events. The read and the counting rule are in *Duplicate-safety read* below |
| Durability & signal | `broad.classified_content_density` | 3 | `useful logical bytes / all in-scope logical record bytes` under the frozen `logical-record-role-v1` classifier; unknown and unclassified bytes remain in the denominator; score is the uncapped fraction from 0 to 1 |

The points sum exactly to `30 + 20 + 15 + 20 + 15 = 100`.

### Frozen logical-content classifier

`broad.classified_content_density` uses the closed `logical-record-role-v1` rule. Each
logical record declares a `record_kind`; the scorer derives and checks its classification
from that role. The useful roles are `user_message`, `correction`, `assistant_message`,
`tool_call`, `tool_result`, `failure`, `file_change`, `plan`, and `explanation`.
`metadata`, `index`, `snapshot`, `session`, and `system` are `unclassified`, while
`unknown` is `unknown`. Any other role or a classification that disagrees with this table
is invalid. Classification follows decoded record function, never filename, physical byte
size, or a hand-authored content-key guess. Unknown and unclassified logical bytes stay in
the denominator.

### Duplicate-safety read (amended 2026-10-05)

One rule applies to every row.

- **Containers.** A container is a file, or a table of a database. A record type inside
  one file or one table is not a container.
- **Membership.** The read is every container from which the decoder takes a scored fact:
  an event, an order, a relation, an identity or a usage count. If the decoder opens a
  container for one metric, its records count for duplicate safety too. The read is fixed
  before scoring. It is the same for all metrics.
- **Keeping a container outside.** The decoder must then never open it. The row earns
  only what the other containers prove.
- **Events.** The events are the user prompts, assistant messages, tool calls and tool
  results that the read states. They include every required event of the workload.
  Context that the harness injects is not a prompt when a native field marks it.
- **Occurrences.** Every record of the read that states an event is one occurrence:
  message text, call name and arguments, or result output. The record type does not
  matter. The count is made on the raw records, not on decoded facts. A copy in another
  container of the read is proved by its join key and by content comparison, not by a
  file name. A shared id does not remove a copy. Only a native supersession or tombstone
  field does.
- **Restating a call or a result.** A record restates a call when it holds all of the
  call's arguments, with or without the tool name. A record that holds only some of the
  arguments (for example a result that names the file path of a read) does not restate the
  call. A record restates a result when it holds the full result output. The check is made
  on the text of the arguments: a number or a flag (a time limit, a switch) is not compared.
  A call whose only arguments name its target (a file path) is restated only by a record
  that also holds the tool name. When a record names a call id, it restates only that call.
- **Density.** Density uses the same statements. The first record of the read that states
  an event keeps its role. A record that restates a stated event is a `snapshot`. A
  record that states no event is unclassified (`metadata`, or the `session`, `system`
  or `index` role of the frozen classifier). `unknown` is not used for a container the
  decoder knows; it stays for a record shape with no supported role. So a record that
  counts as a duplicate occurrence is never also counted as useful content.
- **Rows outside the read.** Density still counts every record of the complete root by
  its bytes. A container outside the read gets a fixed unclassified role and its payload
  is not parsed.

| Row | The read (containers the decoder takes a scored fact from) |
|---|---|
| Codex CLI | The rollout JSONL of the session (`rollout-*.jsonl`) |
| Claude Code CLI | The transcript JSONL of the session |
| Claude Desktop | The transcript JSONL and the Desktop metadata JSON (session identity). The metadata restates no event |
| Copilot CLI | `events.jsonl`, the session store database (usage), the rewind snapshot index and its backups (changed file), `workspace.yaml` (identity) |
| DeepSeek Harness CLI | The session file `session.v4.jsonl` and the projection cache (reconciliation) |
| OpenCode CLI | The `session`, `message`, `part` and `event` tables and the `migration` ledger of `opencode.db`. The `event` table is an update log that states every part again, so every statement in it counts (duplicate safety 0 of 79, 0 of 62, 0 of 72). A leaner read without `event` was tried on 2026-10-05 after first scoring and withdrawn on 2026-10-08 by owner decision; see the adapter document |
| Pi | The session JSONL |
| Antigravity CLI | The conversation database (`steps`, `gen_metadata`). The transcript files are outside |
| Cursor CLI | The chat store `store.db` (tables `meta` and `blobs`), its sidecar `meta.json` (declared version) and the agent transcript JSONL (opened by the absence scan). The sidecar restates no event. The shared store `ai-code-tracking.db` is outside: the decoder takes no fact from it, and its bound rows are read only to prove an absence |
| OpenClaw | The rows of the session in three tables of the shared agent store `openclaw-agent.sqlite`: `transcript_events` (all events, order, times, model, usage), `trajectory_runtime_events` (the exit code of a shell call, the token totals of a run, the product name) and `session_windows` (the session row), bound as a JSON row export. Every row of the three tables counts. Outside, never opened by the decoder: the other exported tables (search and position indexes, session index and snapshot rows, the ACP replay buffer `acp_replay_events` that exists only on the capture route `acp`, audit and placement rows) and the files and rows of the Codex backend (rollout JSONL, shell snapshot, lock, `state_5.sqlite`, `thread_history_1.sqlite`). Their bytes count for density. The read was chosen on 2026-10-07, after the containers of the captures had been seen and before any score was computed; see the adapter document for the point effect of each choice |
| Hermes | The rows of the session in the tables `sessions`, `messages` and `session_model_usage` of the shared store `state.db`, bound as a JSON row export, and the one `system_prompts` row that the session row names by hash (the name of the harness). The prompt row restates no event. The search index tables (`messages_fts*`) are outside: the decoder takes no fact from them and their rows are not in the export. The file of `hermes sessions export` is a derived projection and is outside |
| Kimi | The wire log `agents/main/wire.jsonl` of the session directory (all events, order, relations, model, usage, the changed file) and `state.json` (the session id). The state file restates no event. The wire log states every event again when the turn ends (`agent.message.appended`) and each prompt once more (`context.append_message`); every statement counts (duplicate safety 0 of 14 in each run). Outside, never opened by the decoder: the session log, the notify state and the file copies of the session directory. Their bytes count for density. The read was chosen on 2026-10-08, after the files of the captures had been seen and before any score was computed; see the adapter document |

## Matching and scoring

Population metrics score `points × correct / max(observed eligible, decoded eligible)`.
The observed population cannot shrink because the native record omitted an event. Extra
decoded duplicates or dangling candidates enlarge the denominator. Fixed assertions pass
or fail only after their evidence boundary is complete.

| Field | Match rule |
|---|---|
| User turn | Exact UTF-8 after the declared line-ending transform; internal whitespace and Unicode remain significant |
| Visible response | Exact canary, boundary status, turn relation, and readable ordered text; eloquence is not graded. The rationale and timestamp evidence use one text rule: the native text equals the observed text, or the observer saw only the canary and the native text ends with it |
| Action | Exact ordered arguments, target, action identity, and turn |
| Unscored native action | A native action that pairs with an observed unscored action (a retry, a read, an exploration command) is outside the population of `work.actions` and `broad.event_timestamps`. The pair is made by native id when one exists. Without ids: in one turn, for one group of calls with equal name, arguments and target, the observer saw n scored and m unscored actions and the native record holds k. Then min(k, n+m) are paired. Only the k − (n+m) native calls above that are duplicates and enlarge the denominator |
| Result | Exact result identity, status, exit code, helper nonce, and declared text transform |
| File change | Exact relative path and SHA-256 before/after pair. The native record supplies the pair either as explicit hashes or as a native pre-image plus the native edit, from which a reader recomputes both hashes. For a shell write, the pre-image is the file source in an earlier native inspect result, the post-image is the body of a quoted heredoc that replaces the file, and the native diff of the same call must turn the first into the second. A workspace snapshot or observer value never enters the native fact |
| Compound shell call | One native shell call can hold several command segments (separated by `;`, `&&`, `||` or a newline). When it holds two or more scored segments, it is the native record of each one. A frozen helper invocation (`python3 bench_check.py <phase> --run-canary <canary>`) is one action. Its result is its own helper output line in the native tool result. Its exit code is the `exit=N` line directly after that line, when the command echoes `exit=$?` directly after the segment. Without that echo, the call's own status counts only for the last segment. A segment returned zero when an unbroken `&&` chain leads from it to a later scored segment whose own output is in the native result, because the shell runs that segment only after a zero exit. Otherwise the exit code stays absent. A segment that writes the workload file by shell redirection is one edit action and has no result of its own. Action-result relations exist only for helper segments. Each segment keeps the parent call id, the turn, and the line and timestamp of the native call. A call with one scored segment, and a command that cannot be split safely (subshell, command substitution, background job, shell keyword), stays one call. The decoder never creates an action, a result or a relation that the native bytes do not hold |
| Relation | Native stable-key direction and partial order; no guessed title or timestamp link |
| Model/config | Exact observed model and declared configuration identity per response; a trailing selector variant such as the `[1m]` in `model[1m]` is ignored. When the observer channel reports no model, the identity on the joined native response is accepted |
| Usage | `attribution.usage`: a native usage record sits on the response record, or joins to it by a stable key, and holds an input count and an output count as non-negative integers. `attribution.token_semantics`: the same record also holds a cache-read count and a cache-write count, each under its own key as a non-negative integer. An explicit `0` shows that no cache write happened. A missing key is not a zero and earns no credit. There is no partial credit per field. When the usage record exists and a complete root holds no such key, token semantics is `native_absent` and the record stays in the decoded population; without a complete root it is `unresolved`. Usage is never `native_absent` when a joined record with input and output counts exists. `0`, `null`, missing, estimated, and billed remain distinct. Token values are native-attested: observer token values are not compared, because observers report different scopes (a whole turn or one request) |
| Reconciliation | Native-attested: the native record declares a session or turn total, and its own per-response usage records sum to that total. Observer totals are not compared. A total that exists only in a harness call or stream, and not in the native bytes, is `native_absent` |

No LLM judge, fuzzy match, cohort-relative size curve, byte-volume bonus, or undocumented
adapter heuristic is allowed. Native IDs are preserved. Decoder-derived occurrence IDs
must be distinct and identify their derivation rule.

## Evidence states

Every cell points to an observer ID where applicable and at least one native locator or a
declared documentation/version record for broad checks.

| State | Meaning and score behavior |
|---|---|
| `measured` | Complete evidence establishes a fraction or assertion; contributes points |
| `native_absent` | The event/property was independently expected and the complete native root proves absence; contributes zero. A row may also be `native_absent` without a complete root when the complete family of files named by the session id, and the bound rows of every shared store that names the session, hold no such field |
| `contradiction` | Native content conflicts with independent truth; contributes zero |
| `unresolved` | Acquisition, identity, evidence, or interpretation cannot distinguish retention from loss; null |
| `decoder_unsupported` | Relevant native bytes exist but the decoder cannot classify them; null and not a writer failure |
| `unexercised` | Required workload trigger did not happen; null |
| `invalid_capture` | Bundle, root, observer binding, privacy, or process identity failed; null |
| `not_applicable` | Forbidden for a required v1.0 metric |

Any null metric blocks the run's category total, `/100`, rank, **Fully reproduced** badge,
and user recommendation. Incomplete cards show measured quality, metric coverage, possible
range, and reason IDs.

A configuration with a null metric is reported as **provisional**: its row shows the points
its resolved metrics earn with each null metric counted as zero, and the best total it could
reach. A provisional row is visible but never ranked.

### Evidence tiers (amended 2026-10-03)

The independent observer is required for the work, causal, and revision metrics: they
compare native records with facts observed outside the session file. The usage, portable,
and broad format metrics are native-attested: they are scored from the copied native bytes,
the capture receipts, and the deterministic decoder. An observer limit alone does not make
a native-attested metric unresolved.

## Repetitions, ranking, and badges

Score each run independently. Aggregate three scheduled evaluated repetitions with equal
weight and show min–max. Do not replace a scheduled run with calibration or a corrective
attempt. Exact ties use competition ranking. Publish no leaderboard unless at least three
rows qualify; keep every attempted row visible. Require a complete CLI/Desktop pair only
for the CLI/Desktop consistency recommendation.

*Waiver, 2026-10-08.* For v1 the rule "Do not replace a scheduled run with calibration or a
corrective attempt" is waived by owner decision for OpenClaw (two replaced attempts),
Claude Desktop (two corrective runs) and Kimi (five attempts stopped by a controller setting,
the provider rate limit, or a reply without the required marker). Each replaced attempt and its reason is listed in the
adapter document. The waiver was made after the results were known.

| Badge | Required result state |
|---|---|
| **Fully reproduced** | Three complete runs; all 31 metrics resolved; immutable public evidence packages; deterministic offline recomputation receipt for the exact package, including any measured failures |
| **Independently reproduced** | Fully reproduced plus a second operator or environment recomputed the exact package and published its receipt |
| **Partially verified** | Valid native evidence supports at least one measured metric, but the full repetition/metric/public/recomputation contract is incomplete; no rank or recommendation |
| **Unranked** | No valid evidence-bound metric result because the surface was unavailable, unexercised, or invalidly captured |

Badge data must include surface/build/date scope plus `result_id`, `evaluation_id`, and
reproduction receipt ID.

## User recommendation rules

These rows are generated into **If you're building on session files**. Common eligibility
is **Fully reproduced**, all inputs resolved, three complete repetitions, and public
evidence. Category objectives are normalized to 0–100 before comparison. Less than a
five-point top-two margin yields a qualified set rather than a single preferred row.

| Key | Minimum | Objective |
|---|---|---|
| `audit_ready` | Fidelity ≥ 27/30; Causality ≥ 18/20 | Highest mean normalized Fidelity and Causality |
| `portable_archives` | Portability ≥ 18/20; all deep portable metrics perfect in 3/3 | Highest normalized Portability |
| `usage_accounting` | Usage ≥ 13.5/15; usage join, semantics, and reconciliation perfect in 3/3 | Highest normalized Usage |
| `cli_desktop_consistency` | Complete qualified pair; overall and each category differ by ≤ 5 normalized points | Lowest mean category distance; exclusive pair needs a two-point advantage |
| `lean_complete_record` | Fidelity ≥ 27/30; Causality ≥ 18/20; Portability ≥ 18/20; classified-byte coverage ≥ 95% | Lowest median marginal physical KiB per recovered required semantic fact; exclusive row needs ≥ 10% advantage |
| `long_term_archives` | Durability ≥ 13.5/15; Portability ≥ 18/20; at least two builds across ≥ 30 days | Highest mean normalized Durability and Portability |

Output one of `recommended`, `qualified_set`, or `no_recommendation`. Every record includes
rule version, threshold and objective values, cohort result IDs, scoped surface/build/date,
metric IDs, evidence locators, and reason IDs. Unresolved evidence always produces
`no_recommendation` for the affected candidate.

## Vendor “To pass, fix” rules

Vendor improvement guidance is generated separately. Select at most three items by:

1. invalid or unresolved blockers;
2. failed metrics in the lowest normalized category;
3. largest recoverable point value, then lexical metric ID.

Each item includes metric ID, observed consequence, evidence state, observer/native
locators, and the smallest format-level acceptance condition that would pass the metric.
It is guidance for the scoped surface/build, not a claim about vendor intent.

## Crash qualification and physical storage

Crash qualification is unscored. Report pre-recovery preservation, relaunch mutation,
and native continuation with process/barrier/hash evidence as `passed`, `failed`, or
`not yet run`. It never changes the `/100` score or the edition name.

Physical footprint, cold initialization, sidecar allocation, range, and KiB per recovered
fact are diagnostics. Only the qualified `lean_complete_record` recommendation uses the
physical ratio. The category score uses classified logical-content density and cannot
reward a container merely for allocating fewer bytes.
