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
| Durability & signal | `broad.stable_root_location` | 2 | The complete root is documented or deterministically discoverable from the isolated run across all three repetitions, without personal-history scanning; assertion 1 |
| Durability & signal | `broad.naive_reader_duplicate_safety` | 3 | One documented forward read yields each required semantic event once, or supersession/tombstone fields make exact deduplication possible without vendor-specific heuristics; denominator is required events |
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

## Matching and scoring

Population metrics score `points × correct / max(observed eligible, decoded eligible)`.
The observed population cannot shrink because the native record omitted an event. Extra
decoded duplicates or dangling candidates enlarge the denominator. Fixed assertions pass
or fail only after their evidence boundary is complete.

| Field | Match rule |
|---|---|
| User turn | Exact UTF-8 after the declared line-ending transform; internal whitespace and Unicode remain significant |
| Visible response | Exact canary, boundary status, turn relation, and readable ordered text; eloquence is not graded |
| Action | Exact ordered arguments, target, action identity, and turn |
| Result | Exact result identity, status, exit code, helper nonce, and declared text transform |
| File change | Exact relative path and SHA-256 before/after pair |
| Relation | Native stable-key direction and partial order; no guessed title or timestamp link |
| Model/config | Exact observed model and declared configuration identity per response |
| Usage | Exact response join and named input/output/cache semantics; `0`, `null`, missing, estimated, and billed remain distinct |

No LLM judge, fuzzy match, cohort-relative size curve, byte-volume bonus, or undocumented
adapter heuristic is allowed. Native IDs are preserved. Decoder-derived occurrence IDs
must be distinct and identify their derivation rule.

## Evidence states

Every cell points to an observer ID where applicable and at least one native locator or a
declared documentation/version record for broad checks.

| State | Meaning and score behavior |
|---|---|
| `measured` | Complete evidence establishes a fraction or assertion; contributes points |
| `native_absent` | The event/property was independently expected and the complete native root proves absence; contributes zero |
| `contradiction` | Native content conflicts with independent truth; contributes zero |
| `unresolved` | Acquisition, identity, evidence, or interpretation cannot distinguish retention from loss; null |
| `decoder_unsupported` | Relevant native bytes exist but the decoder cannot classify them; null and not a writer failure |
| `unexercised` | Required workload trigger did not happen; null |
| `invalid_capture` | Bundle, root, observer binding, privacy, or process identity failed; null |
| `not_applicable` | Forbidden for a required v1.0 metric |

Any null metric blocks the run's category total, `/100`, rank, **Fully reproduced** badge,
and user recommendation. Incomplete cards show measured quality, metric coverage, possible
range, and reason IDs.

## Repetitions, ranking, and badges

Score each run independently. Aggregate three scheduled evaluated repetitions with equal
weight and show min–max. Do not replace a scheduled run with calibration or a corrective
attempt. Exact ties use competition ranking. Publish no leaderboard unless at least three
rows qualify; keep every attempted row visible. Require a complete CLI/Desktop pair only
for the CLI/Desktop consistency recommendation.

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
