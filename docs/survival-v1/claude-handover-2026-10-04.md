# Session Bench v1 handover — candidates v30 and v31

> Update 2026-10-06: candidate v33 ranks eight rows. Read `expanded-preparation-status.md`, first section, for the current table, packet sets, owner decisions and the never-publish rule. The tables below describe v30 and v31 and are history.

> Update, same day: candidate v31 adds Claude Desktop at rank 6 (86.8). See `expanded-preparation-status.md`, first section. Build inputs: `trusted-release-index-v6.json`, `expanded-release-status-v12.json`, `expanded-partial-coverage-v14.json`. Old Desktop packet sets `claude-desktop-public-candidates-v1` and all `claude-desktop-root-score-replay-*` directories must never be published.

- Date: 2026-10-04
- Repository: `session-bench` checkout root
- Supersedes: `claude-takeover-handover-2026-10-02.md` (its safeguards section stays in force)
- Full status: `expanded-preparation-status.md`, section "2026-10-04: candidate v30, five ranked rows"

## State

Candidate v30 is at `artifacts/v1-expanded-release-candidate-v30/` (ignored by git).
It ranks five configurations, each n=3, all 31 metrics resolved, each with a
sanitized public packet set and a review by a separate Opus reviewer agent.

| Rank | Configuration | Score |
|---:|---|---:|
| 1 | OpenCode CLI | 97.4 |
| 2 | DeepSeek Harness CLI | 97.1 |
| 3 | Pi | 96.4 |
| 4 | Codex CLI (Codex Desktop shares the row) | 90.6 |
| 5 | Claude Code CLI | 90.2 |

Provisional, not ranked: Claude Desktop 67.9–71.9, Copilot 57.9–98.2,
Antigravity 33.7–97.7. Visible with blockers: Hermes, Kimi, OpenClaw, Cursor CLI,
Cursor Desktop.

Build command:

```
python3 scripts/build_expanded_release.py \
  --trusted-index plans/survival-v1/trusted-release-index-v5.json \
  --statuses plans/survival-v1/expanded-release-status-v11.json \
  --partial-coverage plans/survival-v1/expanded-partial-coverage-v13.json \
  --output artifacts/v1-expanded-release-candidate-v30
```

Packets and reviews (under `artifacts/v1-expanded-preparation/`):

| Row | Public packets | Review |
|---|---|---|
| Claude CLI | `claude-cli-public-candidates-v5` | `claude-cli-independent-public-review-v3` |
| OpenCode | `opencode-1.18.31-public-candidates-v3` | `opencode-independent-public-review-v3` |
| DeepSeek | `dsh-public-score-preparation-v8` | `dsh-independent-public-review-v8` |
| Pi | `pi-public-score-preparation-v7` | `pi-independent-public-review-v7` |
| Codex | `codex-cli-public-candidates-v3` | `codex-cli-independent-public-review-v3` |

Bundle and review hashes are in `plans/survival-v1/trusted-release-index-v5.json`.
Producer id `claude-takeover-root`; reviewer ids `opus-independent-reviewer-<config>`.

## Owner decisions

2026-10-03:

- Tier A metrics (work, causal, revision) need the independent observer. Tier B
  metrics (attribution, portable, broad) are scored from native bytes.
- A row with unresolved metrics is shown as provisional with a score range. It
  is not ranked.
- A Desktop surface shares its CLI row only when the session format is the same.
  Codex CLI and Desktop share one row. Claude CLI and Desktop do not.
- The producer never writes its own review.

2026-10-04:

- DeepSeek repetition 1 (`dsh-cal-20260929-2`, named as a calibration) stays in
  the row with the disclosure in `adapters/deepseek-harness.md`. Reason: it
  scores 97.2 against a row score of 97.1, so it does not move the rank.
- Accepted: OpenCode gets version credit from its migration ledger. Codex
  `multi_agent_version` is a feature version, so Codex scores zero on both
  version metrics.
- A ChatGPT xhigh review through the Codex `oracle` skill is deferred to a later
  round.
- Commit to a branch is approved. Push and publication are not yet approved.

## Never publish

- Beside the packets: `summary.json`, `*private-transformation.json`,
  `receipts/*.private.json`, `private/`. The release builder does not copy them.
- Old packet directories. They hold digests that confirm a guessed home user name
  or account balance: `claude-cli-public-candidates-v2/v3/v4`,
  `codex-cli-public-candidates-v1/v2`, `dsh-public-score-preparation-v1..v7`,
  `opencode-1.18.31-public-candidates-v1/v2`, and release candidates v1–v29.
- Raw Codex rollouts. They embed the owner's agent instructions, skill list and
  approved commands.

Rule for sanitizers: do not publish, in a packet or in source, a digest of bytes
that differ from the public bytes only by a low-entropy private value.

Vendor instruction text (owner decision 2026-10-06). Every row blanks vendor
instruction text in its public packets, at equal UTF-8 byte length, with the
marker `[vendor instruction text removed at equal byte length]` (the personal
marker stays for operator text). Blanked: the system prompt, injected
instruction messages, tool descriptions and schema descriptions. Kept: every
record and key, tool names, prompts, responses, tool calls, results, model and
version fields, ids, timestamps, usage. Two fragments are kept because the
public inputs builder reads the operating system from them: the line
`* Operating System: macos` of the Copilot system prompt, and
`snapshot.platform` of a Claude CLI environment attachment. The 31 metric rows
and the duplicate-safety and density evidence must be equal before and after.
The rule covers every file of a public packet, not only the native files: raw
stream copies, observers, receipts, caches and root-evidence copies. Each of the
four sanitizers ends with a guard (`require_no_instruction_phrase` in
`scripts/sanitize_codex_score_packets.py`): it takes prose phrases from every
string it blanked and fails when any file of the public packet still holds one.
OpenCode stores no system prompt and no tool description. Pi stores four tool
definitions with short descriptions in its one system record; that record is
not blanked yet.

Digest rule (2026-10-05). A public packet may hold a 64-hex digest only if it is
one of these:

1. The SHA-256 of bytes that are in the public set: a file, a line, a canonical
   JSON document or one of its member values, a database page. The bytes can be
   in this packet or in another packet of the same set. The published benchmark
   workload fixture counts as public bytes.
2. An output digest that the closed replay recomputes from public bytes
   (diagnostics, manifest pins, metric rows).
3. A digest on the allowlist of the sanitizer. Each allowlisted field is named
   in the sanitizer script (`DIGEST_ALLOWLIST`) with a written reason. There are
   two kinds of reason. (a) The preimage is high-entropy private data that a
   reader cannot rebuild from public bytes plus low-entropy guesses: a
   screenshot, a metadata inventory over thousands of files, a document that
   holds the digest of a private file with blanked operator text. (b) The
   preimage is public data outside the set that holds no operator value: a
   vendor package file, a system library, repository source at capture time, a
   provider model catalog. The before and after digests of the workload file
   that the public observer document states are allowed everywhere: the file is
   a short public source file and the edit is in the public native record.

Every other digest is zeroed at equal length. A digest of a capture-time file
that is not in the packet is such a digest when that file holds the digest of a
private file or a home name: it confirms a guess through a chain of documents.

One function checks this: `check_public_packets` in
`session_bench/public_digest_check.py`. Every sanitizer calls it on the set it
wrote and fails on a digest that is in none of the three classes.
`tests/test_public_digest_check.py` runs it on every current public set.

## Known gaps the reviewers accepted but did not close

1. Codex `portable.companions` is measured with an empty companion list, although
   a CLI thread has rows in shared SQLite stores outside `CODEX_HOME/sessions`.
2. The Claude CLI observer reports cache-read 0 in every turn; native reports
   47k–55k. The stdout stream was not retained.
3. `token_semantics` is a field-presence test only.
4. `claude_live._edit_result_hashes` does not bind `filePath` and does not check
   a multi-edit chain.
5. `claude_live.py` has a `model-unreported` placeholder that could pass when the
   observer reports no model.
6. The DeepSeek public packet no longer proves that stdout, observer, capture
   receipts and cache are the captured ones. The digests were removed for privacy.
7. Claude and Codex packets publish vendor system-prompt text verbatim. This is a
   licensing decision for the owner before publication.
8. Packet manifests still say `public_safe: false`.
9. The OpenCode capture config sets `"snapshot": false`
   (`session_bench/opencode_expanded_capture.py:60`), so the benchmark may have
   disabled OpenCode's own file snapshots.
10. The reviewers are agent sessions on the same host, not a second operator. The
    rule changes post-date the captures. The report states both.
11. A comment in `session_bench/live_metric_comparator.py` (near the
    `final_after` block) still says the OpenCode decoder emits no chain. It now
    does. The comment was left alone because the comparator source is embedded in
    the reviewed packets.

12. Closed: the `&&` chain rule is now in the rubric row "Compound shell call"
    and both the Codex and Claude decoders apply it. No score changed.
13. Claude Desktop runs 1 and 2 are corrective attempts of invalid captures. The
    owner's acceptance is not yet on record.

## Next work

1. Done in v31: Claude Desktop ranked. Original note: Claude Desktop to ranked. It needs `broad.event_timestamps` and
   `broad.stable_root_location`. Options: a native-attested fallback for these
   two Tier B metrics from the retained runs, or one fresh run with the run15
   instrumentation. Run15 itself is blocked: 3 observed actions against a minimum
   of 4, the density builder is not given the Desktop family, and the rationale
   evidence is not given the exact observer document.
2. Copilot and Antigravity: the retained captures did not copy the complete
   native root, so most broad metrics cannot close offline. Each needs three
   fresh live runs (owner approval needed), then the Tier B re-score.
3. Hermes: two qualified captures of three, no score replay yet.
4. Before publication: owner decision on items 7 and 8 above, then push.
