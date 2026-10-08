# Session Bench v1 takeover handover

> Superseded on 2026-10-04 by `claude-handover-2026-10-04.md` (candidate v30, five ranked rows). The safeguards below stay in force. The all-31-metric gate and the observer rule for usage and model were changed by the owner on 2026-10-03.

- Date: 2026-10-02
- Repository: `session-bench` checkout root
- Checkout: `main`, HEAD `ca09b5cff6de218bd247220f87c3268be1154966`
- Purpose: resume the v1 benchmark without restarting completed work or repeating unproductive capture attempts.

## Mission and current decision

The v1 scope is fourteen separate configurations: all ten v0.4 harnesses, Codex Desktop, Claude Desktop, Cursor Desktop, and DeepSeek Harness CLI (`dsh`). The existing candidate v29 is still the newest assembled report. It has four ranked configurations: Pi 99.4, DeepSeek Harness CLI 91.5, OpenCode CLI 80.7, and Claude CLI 74.2. No new harness has been ranked. The candidate represents all fourteen surfaces, but it is not a finished fourteen-configuration benchmark and has not been published.

The owner accepts one evaluated run for a new configuration when that run closes the gaps. Keep the all-31-metric gate. A partial row, an `N/A`, or a score inferred from a subset is not an acceptable v1 rank. Existing independently reviewed n=3 rows remain n=3. New n=1 rows must prospectively designate repetition 1 before its score is known, resolve every one of the 31 metrics, have their own sanitized public bundle and independently trusted review hash, and be reproduced by the release builder. Show sample size in the report and make no repeatability or range claim for n=1.

Claude Desktop run15 is now a non-rankable completed attempt. The rubric and comparator do not allow its missing reconciliation evidence to be turned into a score by summing response records after capture. It also has only three independently observed action/result pairs against the frozen four-action minimum. The owner explicitly directed us to move to the next feasible harness rather than keep polishing run15.

The next distinct target is Codex CLI, but **offline observer feasibility comes before another live run**. Codex CLI is closest by current partial coverage, but its current stdout observer cannot independently observe response-scoped model and token usage. A repeat with the same observer would not close those gaps. Do not spend a designated run until a source-bound observer can resolve the attribution gaps without copying native facts or deriving response usage from cumulative totals.

## Worktree and safeguards

The checkout is heavily dirty with the v1 implementation, evidence, tests, and status artifacts uncommitted. Preserve all existing uncommitted work. In particular, preserve `codex-quota-forensics/` and unrelated files under `docs/reviews/`. No v1 commit, push, tag, or publication has occurred.

The release-attempt ledger `plans/survival-v1/expanded-release-status.json` and its numbered successors are not score approval. Do not use them as scoring authority. Rebuild scores from the pinned evidence and executable replays. The separately trusted review hashes for the already verified packets are recorded in `artifacts/v1-expanded-preparation/trusted-release-index-v2.json`; those pins and packet-directory existence were checked September 30.

The owner authorizes benchmark implementation and existing subscriptions/prepaid credit, roughly USD 5 of Kimi credit. No top-ups or new spending. Cursor is skipped for now. Use the cheapest supported model for any future harness capture; earlier steering specifically rejects Opus for benchmark sessions and requests Sonnet at Medium where the harness supports it. For complex subagents, use Sol Medium/High only; do not use Astra. Do not access or edit Agent Sessions/Codex-History except for the previously authorized read-only parser/sanitized-fixture work; no personal-history sweeps. Do not copy rotating OpenClaw refresh credentials into a competing store.

## What was tried and learned

### Claude Desktop run15

Run ID: `claude-desktop-v1-eval-20261002-15`. It completed the synthetic two-turn task using Claude Sonnet 5.5 at Medium. Its private finalization directory is:

`artifacts/survival-v1-runs/claude-desktop-v1-eval-20261002-15/capture/finalized-private-v1/`

The all-31 receipt `evidence-31.json` currently reports 20 measured, 2 native-absent, 3 contradictions, and 6 unresolved metrics; `score_eligible` is false. The unresolved metrics are `work.actions`, `work.results`, `causal.action_result`, `attribution.reconciliation`, `broad.readable_rationale`, and `broad.classified_content_density`. The contradictions are `attribution.model_config`, `attribution.usage`, and `attribution.token_semantics`.

The run did make real progress on capture provenance: metadata-only discovery found the synthetic transcript and Desktop metadata across Claude's canonical roots, the pair passed family validation, and the local clock/hook receipts captured timestamps. The packet is private and is not part of candidate v29.

The frozen observer has three primary actions, three results, three action-result links, and zero `usage_total` events. The native decoder found four actions/results, including an additional R2 `Read`; do not hide that native candidate or split the compound R1 Bash call into synthetic scored actions. The frozen rubric requires four independently observed action/result boundaries. This is not repairable from the three hook lifecycles retained for run15.

The OTel receipt has direct request IDs for the two displayed assistant responses. Their response-scoped usage matches the native response usage exactly:

- R1 `req_011CfekEf1diApXp7na3bxWj`: input 2, output 403, cache-read 67,618, cache-write 1,015.
- R2 `req_011CfekNjmMMAB94c95CEtza`: input 2, output 343, cache-read 69,671, cache-write 713.

The current join groups every API request sharing the prompt ID, including prompt-suggestion traffic, and creates false contradictions. A correct response-to-request join is a bounded code repair, but it does not solve reconciliation. The frozen native family and observer contain no declared session-usage total and no observed `usage_total`; the comparator therefore leaves `attribution.reconciliation` unresolved. Do not sum the two responses after capture and present that sum as a declared session total.

Two potentially repairable format gaps were identified but are not a reason to keep working on run15 indefinitely: the finalizer does not pass the captured Desktop family into the density builder, and it does not pass the exact observer document to rationale evidence. A direct density-inventory helper test returned a complete 66-record inventory for the frozen family. Any later use must remain source-bound and must not be used to overclaim action or reconciliation evidence.

The bounded Sol Medium reconciliation audit concluded that the frozen rubric requires a declared session total; current run15 cannot resolve this metric. Codex Desktop was also assessed and is farther from a one-run score. The specific audit answers are captured in the task history; no changes were made by those read-only reviews.

### Codex CLI

Candidate v29's latest private coverage is 24 measured, 1 contradiction, and 6 unresolved per retained run (25/31 states resolved). The six unresolved IDs are:

- `attribution.model_config`
- `attribution.usage`
- `attribution.token_semantics`
- `attribution.reconciliation`
- `broad.declared_format_version`
- `broad.honest_version_signal`

`portable.complete_root` remains a contradiction. Separate private closures improved changed-file, timestamp, and stable-root-location diagnostics; they did not make the configuration rankable or create public trusted evidence. Use `artifacts/v1-expanded-release-candidate-v29/coverage/codex-cli.json` for the latest assembled coverage, and read the three private diagnostic directories below for the additive closures:

- `artifacts/v1-expanded-preparation/codex-cli-changed-file-private-diagnostic-v1/`
- `artifacts/v1-expanded-preparation/codex-cli-root-private-diagnostic-v1/`
- `artifacts/v1-expanded-preparation/codex-cli-timestamp-private-diagnostic-v1/`

The retained `codex exec --json` streams expose cumulative usage at `turn.completed`, not response-scoped usage attached to each displayed answer. The observer intentionally records this as diagnostic-only. Do not difference cumulative thread totals, assign them to individual responses, or borrow native usage/model values as independent truth. The installed `codex exec --help` was checked offline: it supports `--json` and explicit `--model`, but help output adds no response-usage source. No provider request was made during that check.

### Other partial or blocked configurations

| Configuration | Latest useful evidence | Main reason it is not the next live run |
|---|---|---|
| Copilot CLI | Corrected private v6 replays resolve 18/31, 17/31, and 18/31. | Per-response usage, token semantics, reconciliation, complete root/companions, and multiple broad-format populations remain unresolved. Corrected v2 packets must be used; v1 root claims were unsupported. |
| Codex Desktop | 15 measured, 1 contradiction, 15 unresolved in each retained run. | Independent action/result/edit and usage populations, complete root/density, and several broad metrics are missing. A Sol Medium audit found that simply running it again with current instrumentation cannot close all 31. |
| Antigravity | 10 measured, 21 unresolved in each retained replay. | Call/result causal IDs, actual model identity, usage/token semantics/reconciliation, root companions, and most broad-format metrics remain open. A recent capture path improves root inventories and response-usage projection, but not enough for a one-run score. |
| Hermes | Two qualified two-turn captures; each current diagnostic resolves only 9/31. | Compound terminal calls do not prove four distinct actions/results; usage is turn/session-scoped; the exact-session SQLite export is not the complete database/sidecar root; isolation and broad-format evidence are incomplete. |
| Kimi Code | Attempt 05 repaired the fixture but its final response hit organization RPM3. | No completed qualified two-turn run; installed Kimi CLI 2.1.1 has no supported inner-request pacing option. Do not blindly retry or top up. |
| OpenClaw | Isolated controller prepared. | Usable OAuth lives in its shared state DB; access-only feasibility remains unfinished. Do not clone rotating refresh credentials. |
| Cursor CLI/Desktop | Historical captures exist. | Explicitly skipped by the owner for now. |
| Pi | Existing verified three-run score is 99.4 and is ranked first. | Owner has since said Pi is authenticated; no rerun is needed to advance a new configuration. Older status text about `refresh_token_reused` is stale for current auth state. |

The three already verified n=3 configurations besides Pi are DeepSeek Harness CLI 91.5, OpenCode CLI 80.7, and Claude CLI 74.2. Keep their exact reviewed packets and hashes; do not rebuild them without a concrete defect.

## Release and verification state

`docs/survival-v1/ranking-policy-n1.md` records the accepted one-run policy. Current uncommitted code includes n=1 score/replay paths in `session_bench/v1_public_score.py`, `session_bench/release_replay.py`, and `scripts/build_expanded_release.py`. Candidate v29 predates a new n=1 scored packet and still describes its four ranked rows as three-run rows. Do not lower the 31-metric gate or select a best run after seeing its score.

The latest full-suite checkpoint in the status history is 1,652 tests plus 20 subtests, before later Desktop/Antigravity corrections. Recent focused finalizer/comparator tests passed 102 tests. The full suite, `compileall`, `git diff --check`, final candidate rebuild, and full candidate-manifest verification still need to be rerun after the current changes. v0.4 regeneration was previously byte-identical. No new live harness run, commit, push, tag, or publication was performed while preparing this handover.

## Concrete next actions

1. Read these sources first: `docs/survival-v1/expanded-preparation-status.md` (newest checkpoint is at the top; “Original preparation snapshot” is historical), `plans/survival-v1/expanded-release-status.json` (attempt ledger only), `docs/survival-v1/native-score-replay.md`, `docs/survival-v1/rubric-v0.4-frozen.md`, and `docs/survival-v1/ranking-policy-n1.md`.
2. Do one bounded offline source-feasibility pass for Codex CLI. Look for a supported, independently observable response-level model/config and input/output/reasoning/cache usage source plus a legitimate reconciliation path. Inspect retained synthetic packets, the current stdout observer, decoder, and supported local CLI/documentation surfaces. Do not inspect broad personal session history, subtract cumulative totals, or infer token semantics.
3. If that source cannot be established, do not run Codex CLI. Report that no remaining harness is ready for a one-run score under current instrumentation, and choose the next method repair only after comparing evidence gaps. Copilot is the next coverage contender, but its usage plus broad/root gaps make it substantially farther away.
4. If the observer source is established, write focused source-bound tests and a frozen capture receipt first. Verify the native-root and version signals. Only then prospectively designate Codex CLI repetition 1 and run the two-turn synthetic workload once with the cheapest supported model. Freeze the native family, observer, usage trace, version/config evidence, and workspace hashes at capture time.
5. Finalize the n=1 run only if every metric resolves. Sanitize it, bind a public manifest, have the separate reviewer inspect that exact immutable bundle, record its separately trusted hash, replay it offline, and rebuild candidate v30. Keep the sample size visible. Do not publish; publication and cross-repository report-card work were not authorized.
6. After the chosen path is complete, run the full test suite and release validations, explain any count changes from the 1,652+20 checkpoint, and leave all unrelated files and the uncommitted implementation intact.

## Useful exact paths

- Latest report: `artifacts/v1-expanded-release-candidate-v29/REPORT.md`
- Claude Desktop run15 all-31 state: `artifacts/survival-v1-runs/claude-desktop-v1-eval-20261002-15/capture/finalized-private-v1/evidence-31.json`
- Claude Desktop run15 observer and native facts: same directory, `observer.json` and `native-facts.json`
- Claude Desktop frozen native pair: `artifacts/survival-v1-runs/claude-desktop-v1-eval-20261002-15/capture/native-family-private/`
- Claude Desktop OTel receipt: `artifacts/survival-v1-runs/claude-desktop-v1-eval-20261002-15/capture/otel-usage-private.json`
- Codex CLI response observer: `session_bench/codex_stdout_observer.py`
- Codex CLI decoder: `session_bench/adapters/codex_cli_decoder.py`
- Score/replay code for n=1 and n=3: `session_bench/v1_public_score.py`, `session_bench/release_replay.py`
- Candidate assembly: `scripts/build_expanded_release.py`
- Latest preparation narrative: `docs/survival-v1/expanded-preparation-status.md`
