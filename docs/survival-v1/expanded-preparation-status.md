# Expanded v1 preparation — 2026-09-29

## 2026-10-08: four packet sets rebuilt after the second outside review

Findings 3, 4, 8, 9 and 10 of the list below. No review of the new sets exists.
Never publish the sets they replace.

| Row | New private set | New public set | Replaces | Change |
|---|---|---|---|---|
| OpenClaw | `openclaw-score-replay-v4` | `openclaw-public-candidates-v4` | v3 | every digest of a digested subtree is zeros; the word `changed` where two inventories differ; 31 rows equal |
| Kimi | `kimi-score-replay-v3` | `kimi-public-candidates-v3` | v2 | `localDate` blanked; the observer keeps the edit arguments and the replay checks them against the native record; 31 rows equal |
| Copilot | `copilot-score-replay-v6` | `copilot-public-candidates-v10` | v9 | the search index cell and the `view` completion count as restatements; duplicate safety 7/25, 8/26, 6/27 becomes 7/28, 8/29, 6/30; scores 96.07, 96.16, 95.87 become 95.98, 96.07, 95.80 |
| OpenCode | `opencode-1.18.31-native-score-v7` | `opencode-1.18.31-public-candidates-v6` | v5 | a running state with the full output counts as a result statement; statements 79, 62, 72; scores unchanged |

## 2026-10-08: Kimi set v2 after the rejection of v1

Review `kimi-independent-public-review-v1` rejected set v1 on one defect: the
record type `turn.step.interrupted` (a turn that ended with an error) was not
in the decoder contract, so four unused attempts of the same build and days
did not decode. Fix: the type is in the contract (`metadata`, no event); all
eight attempts of 2026-10-07 and 2026-10-08 decode clean; a test and a
sanitized fixture of a failed session exist; the adapter document states the
schema-stability window. Sets `kimi-score-replay-v2` and
`kimi-public-candidates-v2`; the 31 rows of each run equal v1 (91.2). Never
publish `kimi-public-candidates-v1`: it is superseded. No review of v2 exists.

## 2026-10-08: Kimi row built; not reviewed, not in a candidate

Runs `kimi-2026-10-08-01`, `-02`, `-06` (repetitions 1, 2, 3). Decoder, stream
observer, replay branch (`kimi` in `session_bench/score_replay.py`), sanitizer
and public inputs: see `adapters/kimi.md`. Private packets
`kimi-score-replay-v1`; public packets `kimi-public-candidates-v1`. All 31
metrics resolve in each run: 29 measured, 2 `native_absent`
(`attribution.reconciliation`, `broad.naive_reader_duplicate_safety`).
Producer scores 91.22, 91.24, 91.24; mean 91.2. The read is `wire.jsonl` and
`state.json` (rubric table of reads). No review exists; the row is in no
trusted index and in no release candidate.

Open for the owner: the largest judgment calls are the exit code 0 of a
successful shell call (about 8.3 points) and the root (the session directory;
two index and cache files of the isolated home were listed and not copied).
Never publish `kimi-score-replay-v1`, and beside
the public packets `summary.json` and `*-private-transformation.json`.

## 2026-10-08: v1 release

Owner decision after the fourth outside review: "Publish as is. Agree with all
ur recommendations." This confirms the kept vendor fragments (Kimi, Copilot,
Pi, Claude Code) and publication without Cursor Desktop.

The release is `artifacts/survival-v1-release/` (tracked). It is candidate v42:
candidate v41 with two changes. (1) `REPRODUCE.md` now gives the replay command
with `-B`; without it Python wrote bytecode into the packet and the runner
refused the packet ("inventory is not closed"), so the published command did
not work. All 36 commands of the file were run on a copy and pass. (2) The
Reviews limit names the fourth review. No packet, score or review changed.

Also for publication: `tests/conftest.py` and
`tests/private-artifact-tests.txt` skip the 330 tests that need the private
capture artifacts when `artifacts/v1-expanded-preparation` is absent (a clean
export passes: 2035 passed, 371 skipped); CI uses Python 3.14; README and
CHANGELOG have the v1 section. The hosted report card (Agent Sessions
repository) is not changed.

## 2026-10-08: fourth outside review (candidate v41): SHIP

ChatGPT web, GPT-6.1 Sol, Extra High, through Repo MCP; run
`d1e19cbc-6524-4890-b0a3-ffb59db4c3f5` (completed, ship); HEAD 4fb8666, diff
base ce382b3. Transcribed result: `artifacts/v1-expanded-preparation/
chatgpt-review-v41-2026-10-08.md`. The reviewer found all seven findings on v40
fixed, no new defect that changes a score or rank, falsifies a material report
claim or exposes an undisclosed private value, and wrote: "Candidate v41 is fit
to publish as a v1 that states its limits."

The verdict question was set by the producer for this round: SHIP if no
confirmed defect remains that makes a published score, a rank or a stated claim
wrong, or that leaks a private value in the existing packets; checker
hardening is non-blocking. The earlier rounds had no such bar. The reviewer's
limits: static review through Repo MCP only; no tests or replays run; binary
SQLite files not queried; a phase freeze, not a content-verified candidate; not
a proof that no packet holds a private value.

Non-blocking ideas it listed: SQLite proof coverage for multi-page schemas and
WITHOUT ROWID tables (omissions fail closed), page fuzzing, a minimum length for
the Copilot substring rule, the `kimi.md` wording "until set v3".

State: candidate v41 is the reviewed candidate. It is on local branch
`v1-candidate-v30`, not pushed, not published. Push and publication need the
owner's yes. Open owner points: confirm the kept vendor fragments (Kimi,
Copilot, Pi, Claude Code); Cursor Desktop stays unranked (Free-plan limit).
The report text says three outside reviews were run; the fourth is recorded
here and can be added to the notes at publication.

## 2026-10-08: candidate v41

Candidate v41 is at `artifacts/v1-expanded-release-candidate-v41/` and replaces
v40. Never publish candidates v1–v40. Same twelve ranks and scores. Build
inputs: `plans/survival-v1/trusted-release-index-v17.json`,
`expanded-release-status-v23.json`, `expanded-partial-coverage-v22.json`,
`--notes plans/survival-v1/release-notes-v1.json`.

Answer to the third outside review (commits 29b7cfc, 573b146). Gate: the SQLite
framing rule now proves a run only from a parsed cell (an overflow cell only
from its local part); repeated JSON keys are kept; inputs are materialized
once. Current sets and reviews changed since v40: `kimi-public-candidates-v4`
/ `kimi-independent-public-review-v4` (the edit check fails on missing
arguments and when no edit was compared); `openclaw-public-candidates-v6` /
`openclaw-independent-public-review-v6` (set v5 was rejected for two false
sentences on kept machine values; the section is now two tables computed from
the public set, with a test); `opencode-1.18.31-public-candidates-v8` /
`opencode-independent-public-review-v8` (current status text; one to five
update rows per part). No metric row moved. Report notes: three outside
reviews named; kept machine values let a reader link packets of one host.

Residuals: OpenCode `inputs/operator-environment.json` gets a new `observed_at`
at each rebuild; `kimi.md` wording "until set v3"; the kept machine values and
the kept vendor fragments are owner points.

## 2026-10-08: third outside review (candidate v40): NO-SHIP, seven findings

ChatGPT web, GPT-6.1 Sol, Extra High, through Repo MCP; run
`390fc93e-ee23-4046-a20a-157f42b1fdaf` (completed, no-ship); HEAD ce382b3, diff
base 572a5b6. Transcribed findings: `artifacts/v1-expanded-preparation/
chatgpt-review-v40-2026-10-08.md`. Of its 12 findings on v39 it found 7 fixed,
2 fixed for the shipped packets with a guard residual, 3 partly open. No finding
concerns a score or a rank. Findings:

1. P1. The new SQLite framing rule of the hex gate accepts a fabricated record
   header inside a BLOB; it must be bound to a parsed cell.
2. P2. Repeated JSON keys hide a short value under a digest-like key.
3. P2. `check_public_packets` given an iterator loses the name check.
4. P2. The Kimi edit check skips an observed edit without `input`.
5. P2. The OpenClaw adapter doc says inode and times are blanked; the report
   says kept machine details identify "not an account or a person" (stable
   values allow correlation between packets).
6. P3. The report says one outside review was run; there were two.
7. P3. The OpenCode adapter doc still opens with "remains unscored".

The reviewer judged that the kept OpenClaw metadata, the Kimi guard and the
Copilot substring rule can stand as disclosed limits for the frozen packets.

## 2026-10-08: candidate v40

Candidate v40 is at `artifacts/v1-expanded-release-candidate-v40/` and replaces
v39. Never publish candidates v1–v39. Same twelve ranks and one-decimal scores
as v39. Build inputs: `plans/survival-v1/trusted-release-index-v16.json`,
`expanded-release-status-v22.json`, `expanded-partial-coverage-v21.json`,
`--notes plans/survival-v1/release-notes-v1.json`.

Answer to the second outside review. Gate holes closed (whole-value proof,
Cursor keys, names, short values under digest-like keys; commit 7c12930); the
home name removed from five tracked files (it stays in the history of
`origin/main`); report notes corrected (what stays readable, the four kept
vendor fragments, what "verified" means, the gate is a filter, action
arguments are not compared). Four sets rebuilt and approved by delta review:
`openclaw-public-candidates-v4` (subtree digests zeroed; 88.1),
`kimi-public-candidates-v3` (`localDate` blanked; edit arguments checked in the
replay; 91.2), `copilot-public-candidates-v10` (three more statements per run;
run scores 95.98, 96.07, 95.80; mean 95.95, shown as 96.0),
`opencode-1.18.31-public-candidates-v6` (statements 79, 62, 72; 94.4).
Reviews: `openclaw-independent-public-review-v4`,
`kimi-independent-public-review-v3`, `copilot-independent-public-review-v10`,
`opencode-independent-public-review-v6`. Never publish the sets they replace.

Residuals from those reviews, not acted on: OpenClaw keeps inode, times and
entry counts of eight digested roots and 64 classified directories, and its
adapter doc says they are blanked; the Kimi edit check passes silently if an
observed edit has no `input` (should require at least one compared edit); the
Copilot substring rule has no minimum length; the OpenCode adapter doc opens
with a stale status line. Owner points open: the Kimi and Copilot kept vendor
lines (the producer listed them in the report as disclosed exceptions); the
Copilot `turns` table in the read.

## 2026-10-08: second outside review (candidate v39): NO-SHIP

Reviewer: ChatGPT web, GPT-6.1 Sol, Extra High, through Repo MCP in review mode;
coordinator: this producer session, with the `repo-mcp-review` skill and the
`chatgpt-run` journal (run `e028f97e-272a-4579-814f-837216156c81`, completed,
outcome no-ship). HEAD 572a5b6, diff base 3f1b357. Findings as transcribed by
the coordinator: `artifacts/v1-expanded-preparation/
chatgpt-review-v39-2026-10-08.md` (private, ignored by git). The reviewer's
limits: static read-only review, no execution; Repo MCP gives a phase freeze,
not a content-verified candidate.

Of the 13 earlier findings: 2, 3, 10, 12 fixed; 1 and 13 disclosed owner
choices; 4, 5, 6, 8 disclosed limits; 7 withdrawn by the reviewer; 9 and 11
still partly open. New or remaining findings:

1. P1. Hex gate: `_PrefixIndex` keeps 24 characters, so a public digest prefix
   plus a private suffix passes; a longer run that holds one public digest is
   accepted whole.
2. P1. The Cursor encoded-JSON proof does not check dictionary keys.
3. P2. OpenClaw packets keep `entries_sha256` and `directory_sha256` of the
   npm directory digest (preimages hold private paths, inodes, times).
4. P2. Kimi: the observer drops the edit arguments and the comparator does not
   compare them.
5. P2. A file or directory named by an exact 64-hex value escapes both gates.
6. P2. Hex values of 6 to 11 characters under digest-like keys are not gated.
7. P2. Tracked files hold the home user name (`plans/survival-v1/
   campaign.json`, `scripts/run_claude_survival.py`,
   `scripts/make_opencode_public_derivative.py`,
   `docs/survival-v1/surface-preflight.md`, `tests/test_native_sanitize.py`).
8. P2. Kimi keeps `localDate` and blanks `timeZone`: the date bounds the UTC
   offset.
9. P2. Copilot's duplicate counter misses two restatements inside its read
   (small overstatement).
10. P3. OpenCode statement counts are 79/62/72, not 76/59/69 (no score change).
11. P3. The report names only Pi as keeping vendor text; Kimi keeps one
    sentence and Copilot its operating-system line.
12. Report sentences not supported: "All other operator values are aliased,
    blanked or zeroed"; machine details "hold no account or personal value";
    "fully verified" and "publication-ready".

## 2026-10-08: candidate v39, twelve ranked rows

Candidate v39 is at `artifacts/v1-expanded-release-candidate-v39/` and replaces
v38. Never publish candidates v1–v38. Build inputs:
`plans/survival-v1/trusted-release-index-v15.json`,
`expanded-release-status-v21.json`, `expanded-partial-coverage-v20.json`, and
`--notes plans/survival-v1/release-notes-v1.json` (notes on waived and corrected
rules and known limits; by owner wish the ranking table itself carries no
marks).

| Rank | Configuration | Score | Packets | Review |
|---:|---|---:|---|---|
| 1 | DeepSeek Harness CLI | 96.9 | `dsh-public-score-preparation-v13` | `dsh-independent-public-review-v13` |
| 2 | Pi | 96.4 | `pi-public-score-preparation-v7` | `pi-independent-public-review-v7` |
| 3 | Copilot CLI | 96.0 | `copilot-public-candidates-v9` | `copilot-independent-public-review-v9` |
| 4 | OpenCode CLI | 94.4 | `opencode-1.18.31-public-candidates-v5` | `opencode-independent-public-review-v5` |
| 5 | Kimi Code | 91.2 | `kimi-public-candidates-v2` | `kimi-independent-public-review-v2` |
| 6 | Claude Code CLI | 89.3 | `claude-cli-public-candidates-v9` | `claude-cli-independent-public-review-v6` |
| 7 | OpenClaw | 88.1 | `openclaw-public-candidates-v3` | `openclaw-independent-public-review-v3` |
| 8 | Codex CLI (Desktop shares the row) | 87.5 | `codex-cli-public-candidates-v8` | `codex-cli-independent-public-review-v8` |
| 9 | Hermes | 86.4 | `hermes-public-candidates-v3` | `hermes-independent-public-review-v3` |
| 10 | Claude Desktop | 85.6 | `claude-desktop-public-candidates-v3` | `claude-desktop-independent-public-review-v3` |
| 11 | Antigravity | 82.7 | `antigravity-public-candidates-v4` | `antigravity-independent-public-review-v4` |
| 12 | Cursor CLI | 78.1 | `cursor-cli-public-candidates-v3` | `cursor-cli-independent-public-review-v3` |

Not ranked: Cursor Desktop (paused on the Free-plan limit).

Changes since v38. Four sets rebuilt and re-reviewed (all approved by delta
review): OpenCode v5 (first read restored, 94.4), OpenClaw v3 (reconciliation
`native_absent`, 88.1), Copilot v9 (61 request hashes zeroed), Codex v8 (root
fingerprint and total size zeroed). Kimi added: set v1 was rejected (the
decoder did not know the `turn.step.interrupted` record of failed turns), set
v2 approved (review.json sha256
`1682fdf909c840a6900319c9e276fcf78c6f626e169c80f5d0246eb253e31118`); captures
`kimi-2026-10-08-01`, `-02`, `-06`; five unused attempts are listed in
`adapters/kimi.md`; three valid runs, so the three-run rule needed no waiver.
Never publish: OpenCode v4, OpenClaw v1/v2, Copilot v7/v8, Codex v6/v7, Kimi v1
as current sets, any `*-score-replay-*` or `*native-score*` private set, and
the sidecars.

Open points. Reviewer notes not yet acted on: OpenClaw packets hold allowlisted
digests of metadata listings of the OpenClaw home (same class as the Codex
fingerprint); OpenCode's declared statement counts are 3 too low per run (no
score effect); the Codex `filesystem_id` string and historical file counts stay
readable because the replay reads them; Kimi's `scan.json` and query cache were
listed and not copied; the kept first sentence of the Kimi system prompt needs
the owner's confirmation. The home user name stood as a sample value in
`tests/test_public_digest_check.py` and is replaced; older tracked files that
are already on `origin/main` still hold it in example paths.

## 2026-10-08: Kimi captures; owner overrides the three-run rule for Kimi

Kimi controller fix: the environment variable `KIMI_LOOP_MAX_ATTEMPTS_PER_STEP=1`
was still set, so Kimi never retried after a rate limit. It is removed; a rate
limit that Kimi retried past is no stop; later turns start after a 65 s pause;
the turn timeout is 600 s. Attempts of 2026-10-08, isolated Kimi home, Moonshot
API, `kimi-k2.7-code`: `-01` captured (14 requests), `-02` captured (7),
`-03` and `-04` stopped by the account limit of 3 requests a minute; more
attempts are running. The attempts of 2026-10-07 (`-01`, `-02`) failed by the
controller setting.

Owner decisions: "we can live fine with two captures"; asked whether a row with
two runs is ranked: "i override it . rank anyway. we can print footnotes
explaining where rules were corrected". So Kimi is ranked with the runs it has
(two if no third capture succeeds), and the report carries footnotes for every
rule that was waived or corrected for v1: the no-replacement rule (OpenClaw,
Claude Desktop, Kimi), the three-run rule (Kimi), the OpenCode read.

A Repo MCP review from the producer's own session was tested once on the Kimi
controller diff (mechanism test, not independent): two low findings, no test
for the new stop rule and a timeout that the plan and the code state twice.

## 2026-10-08: owner decisions on the outside review

Asked about findings 1 to 4 of the ChatGPT review, the owner answered
"1. unranked. 2. 94.4 3. limits 4. remove":

1. OpenClaw and Claude Desktop are shown with their scores and are NOT ranked:
   their repetitions include replaced or corrective attempts, which the rubric
   forbids.
2. OpenCode is scored with the read of its first scoring (the `event` table is
   in the read): about 94.4, not 97.4.
3. Findings 4, 5 and 6 (comparator trusts the decoder's reconciliation flag;
   response matching ignores the full observer text; usage can join by turn
   alone) are disclosed as known limits of v1. No comparator rebuild.
4. OpenClaw loses the 3 reconciliation points.

Later the same day the owner reversed point 1: "rank OpenClaw and Claude
Desktop; skip the no-replacement rule for v1". Both rows stay ranked. The
rubric gets a dated waiver note under the rule, and the report must disclose
the waiver and that it was made after the results were known.

Producer's findings on the rest. Finding 7 is not a defect: the sandboxed
replay runs on a copy of the packet in a fresh temporary directory
(`replay_score_package`), so the parent that the profile opens holds only that
copy. Findings 9, 11, 12 are fixed in `session_bench/public_digest_check.py`
(commit c0f66f6): proved allowlist rules, a gate from 12 hex characters,
provider ids only in reviewed fields. Finding 10 is fixed in
`codex-cli-public-candidates-v7`. The hardened gate found 61 request hashes in
Copilot v8 (six are hashes of prompt text with a private temp path); they are
zeroed in `copilot-public-candidates-v9`. Never publish Copilot v8 or Codex v6.
Findings 8 and 13 go into the report as a limit and as disclosed waivers.

## 2026-10-07 late: outside review of candidate v38 by ChatGPT: NO-SHIP

Reviewer: GPT-5.6 Sol, Extra High, through Repo MCP in review mode (started by
the producer with `oracle --engine browser`; the owner asked for this route).
It read the tracked tree and candidate v38. Full text (private, ignored by
git): `artifacts/v1-expanded-preparation/chatgpt-review-v38-2026-10-07.md`.
Verdict NO-SHIP. Findings, not yet resolved:

1. Rubric "Do not replace a scheduled run with calibration or a corrective
   attempt": OpenClaw (`-03`→`-04`, `-05`→`-07`) and Claude Desktop (two
   corrective runs) break it; owner acceptance does not amend a frozen rule.
2. OpenCode's read was changed after first scoring (event table left out):
   97.4 against 94.4 under the first read; this decides rank 1.
3. OpenClaw reconciliation credit (3 points) without per-response usage.
4. The comparator trusts the decoder's reconciliation boolean.
5. Response matching ignores the observer's full response text.
6. Usage attribution can join by turn alone.
7. The replay sandbox lets the packet's parent directory be read and written.
8. Duplicate safety depends on the choice of read (source shopping).
9. Long-hex allowlist rules match a context, not exact values.
10. Codex packets publish `inventory_metadata_sha256` of the operator's normal
    Codex root (a stable fingerprint of private state).
11. Hex values of 6 to 31 characters are not gated; `short_digest_warnings` is
    not part of `check_public_packets`.
12. The provider-id exemption is lexical (`call_<any hex>` passes).
13. The Codex packets hold the GitHub owner name; the report does not say that
    the owner waived this, nor the waiver of replaced attempts.

Also: the report presents the ranking more firmly than a method whose rules
changed after captures supports.

## 2026-10-07 late: candidate v38, Copilot rebuilt for a private-path hash

Candidate v38 is at `artifacts/v1-expanded-release-candidate-v38/` and replaces
v37. Same eleven rows, ranks and scores. Inputs:
`plans/survival-v1/trusted-release-index-v13.json`,
`expanded-release-status-v19.json`, `expanded-partial-coverage-v19.json`.
Never publish candidates v1–v37 or `copilot-public-candidates-v7`.

Long hex runs. A Sonnet agent judged the hits of `long_hex_runs` in the five
older sets (`artifacts/v1-expanded-preparation/
legacy-hex-run-judgment-2026-10-07.md`). Safe: Cursor CLI (the chat directory
name is the MD5 of the public aliased workspace path; blob ids; hex of a public
row), Codex CLI (a commit of this repository), Hermes (random uids),
Antigravity (encoded store rows without private values). Leak: the Copilot
`files` key of `rewind-file-snapshots/index.json` was SHA-256 (first 32) of a
private temp path. Copilot was rebuilt as `copilot-public-candidates-v8` with
the key taken from the public path; the 31 rows of each run equal v7 (96.0).
Review: `copilot-independent-public-review-v8` (approve; review.json sha256
`4c06a3249d3dc3e37b826c57ee5bda9a53aee5bfa3a38a7bdd95dbbddf1d5a31`). The checker
now has written rules per configuration (`CONFIGURATION_HEX_ALLOWLIST`), no
legacy table, and all eleven ranked sets pass with zero unlisted runs.

Owner decisions: the GitHub owner name in the `repository_url` of the Codex
rollouts is accepted as public ("acccept"); ChatGPT may read the public packet
sets through Repo MCP ("yes, I want").

Repo MCP policy for `session-bench-7dfdd60ea344` now exposes the tracked source
tree and `artifacts/v1-expanded-release-candidate-v38/` only. Other artifacts,
`codex-quota-forensics/` and `docs/reviews/` stay excluded.

## 2026-10-07 night: candidate v37, OpenClaw ranked

Candidate v37 is at `artifacts/v1-expanded-release-candidate-v37/` and replaces
v36. It ranks eleven configurations: the ten of v36 with the same scores, and
OpenClaw at 91.1 (rank 5, between Copilot CLI and Claude Code CLI). Inputs:
`plans/survival-v1/trusted-release-index-v12.json`,
`expanded-release-status-v18.json`, `expanded-partial-coverage-v19.json`.
Never publish candidates v1–v36.

| Rank | Configuration | Score |
|---:|---|---:|
| 1 | OpenCode CLI | 97.4 |
| 2 | DeepSeek Harness CLI | 96.9 |
| 3 | Pi | 96.4 |
| 4 | Copilot CLI | 96.0 |
| 5 | OpenClaw | 91.1 |
| 6 | Claude Code CLI | 89.3 |
| 7 | Codex CLI (Codex Desktop shares the row) | 87.5 |
| 8 | Hermes | 86.4 |
| 9 | Claude Desktop | 85.6 |
| 10 | Antigravity | 82.7 |
| 11 | Cursor CLI | 78.1 |

OpenClaw: captures `openclaw-2026-10-07-04`, `-07`, `-06` (ACP route,
`openai/gpt-5.6-terra`, `codex` harness, normal OpenClaw home). Packets
`openclaw-public-candidates-v2`; review `openclaw-independent-public-review-v2`
(review.json sha256
`2ccb6055ab48b1a9d08b9ef1e0085894fca101ac868d24f6cda48121e279bd69`). Never
publish `openclaw-public-candidates-v1`, `openclaw-score-replay-*`, or the
sidecars. Judgment calls the reviewer accepted and their effect if refused are
in the review's `limitations` and in `adapters/openclaw.md` (reconciliation −3;
usage as a run total −5, −4, −4; two forms of one tool call about −7.3; exit
code from the trace table −3.75). The adapter doc copy inside the packets still
says the owner decision on the replaced attempts is open; the owner accepted.

Open before publication: the new long-hex check
(`session_bench/public_digest_check.py`, `long_hex_runs`) has recorded,
unjudged hits in five ranked sets (`RECORDED_LEGACY_HEX_RUNS`): Cursor CLI
(32-hex chat directory name, maybe a hash of the workspace path), Copilot
(32-hex key in `rewind-file-snapshots/index.json`), Codex CLI (git commit id),
Hermes (32-hex message and tool-call uids), Antigravity (hex-encoded store
rows). Each must be judged, and aliased if it is derived from a private value.

Repo MCP: session-bench is registered (`session-bench-7dfdd60ea344`, read-only
policy that excludes `artifacts/`, `codex-quota-forensics/`, `docs/reviews/`),
with one review task in phase `review`. The ChatGPT review must be started by
the owner in ChatGPT; no result yet.

## 2026-10-07 night: OpenClaw review v1 rejected; owner accepts the replaced attempts

Packets `openclaw-public-candidates-v1` (producer scores 91.09, 91.09, 91.11;
mean 91.1) were rejected by `opus-independent-reviewer-openclaw`
(`openclaw-independent-public-review-v1/rejection.md`). The reviewer confirmed
the scores and accepted every scoring judgment. Blocking defect: a 40-hex cache
file name in the inventories is the SHA-1 of the owner's account id and ChatGPT
user id; the digest checks read only 64-hex values. Never publish set v1. The
same file name is in none of the ten ranked sets. Fixes are in progress for set
v2.

Owner decision, asked about the replaced attempts `-03` (controller guard
fault) and `-05` (no visible response in turn 1): "i accept".

The workload fixture project (`fixtures/scenarios/survival-v1/workload/
fixture_project/`, four synthetic files) was ignored by git; it is tracked from
commit 8750657. Before that a clean checkout could not bind the fixture digests
of any row.

## 2026-10-07 night: OpenClaw ACP route, three captures

Owner decision: "Try for a rank. Run the gateway in the foreground (no service
install) and read tool events from its WebSocket."

Route (`--route acp` of `scripts/capture_openclaw_survival.py`; see
`adapters/openclaw.md`): the controller starts `openclaw gateway run` in the
foreground with `OPENCLAW_SKIP_CHANNELS=1`, `OPENCLAW_SKIP_CRON=1` and five
more switches, sends both prompts through one `openclaw acp` process, keeps the
raw ACP stream, stops the gateway and checks that port 18789 is closed. The two
switches are mandatory: the owner's config has a Telegram bot channel and a
12-hour heartbeat. No service was installed. The ACP stream holds each tool
call: id, command or patch, status, exit code. It holds no output text, no
model name per response and no token counts; those are in the native rows.

Attempts, all `openai/gpt-5.6-terra` through the `codex` harness:

| Attempt | Repetition | Result |
|---|---|---|
| `openclaw-2026-10-07-probe-07-acp` | probe | one turn by hand; proved the stream |
| `openclaw-2026-10-07-03` | 1 | turns fine; the native bracket was refused by a guard fault and retaken with `--rebracket`; not used |
| `openclaw-2026-10-07-04` | 1 | captured |
| `openclaw-2026-10-07-05` | 2 | invalid: turn 1 ended with no visible response (no message chunk in the stream); 1 model turn; replaced |
| `openclaw-2026-10-07-07` | 2 | captured |
| `openclaw-2026-10-07-06` | 3 | captured |

Row candidates: `-04`, `-07`, `-06`. Not yet decoded or scored. Two more probe
turns (`--verbose on`, `OPENCLAW_RAW_STREAM=1`, both `--local`) showed no tool
events. The sessions of every attempt stay in the owner's OpenClaw store.

## 2026-10-07 evening: OpenClaw first capture; no tool events for the observer

Owner statement: "i dont' need gateway. dont use openclaw at all- its a
bloatware. not even sure we need it session bench honeslty. … but ok". The
gateway service was not installed.

Controller: `session_bench/openclaw_state_capture.py`,
`session_bench/openclaw_envelope_observer.py`,
`scripts/capture_openclaw_survival.py`; see `adapters/openclaw.md`.

- `openclaw-2026-10-07-01`: stopped in preflight, no model call (the owner's TUI
  was open).
- `openclaw-2026-10-07-02`: captured, 2 model turns, `openai` /
  `gpt-5.6-terra` through the `codex` harness. Every changed entry of the home
  was classified; no refusal. Native: the Codex rollout JSONL of the thread, two
  shell snapshots, the thread lock, and 142 rows of the session from four
  stores. Tool summary: turn 1 `bash`, `message` (3 calls, 2 failures); turn 2
  `apply_patch`, `bash` (3 calls). The fixture was removed; nothing else in the
  workspace changed. Status `captured_pending_qualification`.
- Verbose probe (one turn, a new session, `--verbose on --json`, prompt "run
  echo"): `payloads` holds only the reply and stderr holds no tool event. The
  file log under `/tmp/openclaw/` holds none either.

Result: `openclaw agent --local` gives the observer a tool summary (count and
tool names) and no single tool call. By the Hermes ruling the Tier A rows
`work.actions`, `work.results` and `causal.action_result` need observed tool
events, so with this route the row can be provisional only. A stream of tool
events may exist on the gateway WebSocket; that route was not tried.

## 2026-10-07 later: OpenClaw repaired, normal-state route approved

Owner statements: "why you cant run those terminal comands yourself?!"; "i can
help to sign in into openclaw if needed."; "yes, install the codex plugin";
"i dont use openclaw at all. so you can run w/o isolation".

Repair. The producer ran `openclaw update repair` and `openclaw doctor --fix`
on the owner's install. Effects: the default model `openai/gpt-5.4` became
`openai/gpt-5.6-terra` and was allowed in the model policy (old entries kept);
`doctor --fix` disabled the `gemini` plugin; config backup at
`~/.openclaw/openclaw.json.bak`. Update finalization still reports failed: the
`codex` and `llama-cpp` plugin upgrades are "unfinished", memory search is not
ready, and the gateway LaunchAgent is not loaded (`openclaw gateway status`:
"Service unit not found"; the restart log ends in July, so the gateway did not
run before the repair). The TUI needs the gateway; the capture route does not.

Probes (under `live-captures/`): `openclaw-2026-10-07-probe-03` (`agent exec`
with its own state directory: 401, no login in the isolated Codex home, no model
call); `probe-04` (`agent exec --state-dir ~/.openclaw`: 401; then
`openclaw agent --local --session-id … --json` ran, in the wrong workspace, 1
model turn); `probe-05` (fixture in `~/.openclaw/workspace`: 2 turns, the tool
did not find it); `probe-06` (fixture in `~/clawd`, which is
`agents.defaults.workspace`: the whole two-turn workload passed; ledger inspect
0, baseline 1, final 0; tools `bash`, `apply_patch`). Five model turns on the
owner's OpenAI sign-in. Each fixture copy was removed. Three probe sessions stay
in the owner's OpenClaw store.

Facts. Only `openclaw agent --local --session-id <uuid> --json --timeout 300
--message-file <file>` (same id for both turns) works, in the owner's normal
state. `OPENCLAW_WORKSPACE_DIR` is ignored. `--model` is rejected unless the
policy allows the model. Files that change in the home during a run:
`state/openclaw.sqlite`, `state/openclaw-quarantine.sqlite`,
`agents/main/agent/openclaw-agent.sqlite`, and under
`agents/main/agent/codex-home/` the rollout JSONL in `sessions/YYYY/MM/DD/`,
`state_5.sqlite`, `thread_history_1.sqlite`, `queue_1.sqlite`, `logs_2.sqlite`,
`goals_1.sqlite`, `memories_1.sqlite`, `shell_snapshots/`,
`thread-writer-locks/`, `tmp/arg0/`; and `tmp/plugin-captures/<id>/`.
`openclaw models status`: profile `openai:default` expired, the e-mail-named
OpenAI profile ok.

Isolated folder. `artifacts/ocp/` (ignored by git) holds an isolated state
directory with `@openclaw/codex@2026.9.7` installed from ClawHub and no
sign-in. It is not used: the row is captured in the normal state. Cost: the
session stores are shared, so the root metric is a contradiction, as for
Hermes.

Other notes of the day. Hermes: five empty sessions were made in the owner's
Hermes store by `--provider __none__` parse probes. Kimi: a pause of 65 s
between turns or a higher `max_attempts_per_step` was considered and not
implemented; the owner parked Kimi and Cursor Desktop.

## 2026-10-07: remaining rows need the owner

OpenClaw (installed 2026.9.8). Two probes with `openclaw agent exec
--state-dir <own> --cwd <fixture> --json` made no model call
(`live-captures/openclaw-2026-10-07-probe-01`, `-02`). The owner's model policy
rejects a model override. With the default model the agent runs through the
`codex` plugin, which fails: its data and settings upgrade is unfinished
(`openclaw update status` asks for `openclaw update repair`, then
`openclaw doctor --fix`), and the plugin SDK export it needs is missing. The
isolated state directory gets its own empty Codex home with no login; copying
credentials into it is not allowed. The row waits for the owner to repair the
OpenClaw install. `agent exec` has no documented way to continue a session, so
a two-turn run may also need `agent --local --session-id`.

Kimi. Two more isolated attempts (`kimi-2026-10-07-01`, `-02`) completed all the
work and stopped on the final response at the account limit of 3 requests per
minute. The controller no longer forces one attempt per step; Kimi's own retry
(10 attempts) is too fast for that limit. The row needs a higher account tier,
or a pause between requests that the CLI does not offer.

Cursor Desktop. Paused on the Free-plan limit; see `adapters/cursor.md`.

## 2026-10-07: candidate v36, Hermes ranked

Candidate v36 is at `artifacts/v1-expanded-release-candidate-v36/` and replaces
v35. It ranks ten configurations: the nine of v35 with the same scores, and
Hermes at 86.4 (rank 7, between Codex CLI and Claude Desktop). Inputs:
`plans/survival-v1/trusted-release-index-v11.json`,
`expanded-release-status-v17.json`, `expanded-partial-coverage-v18.json`.

Hermes uses three captures of 2026-10-07 (`hermes-codex-2026-10-07-08..10`;
`openai-codex` / `gpt-5.5`; normal Hermes home). Packets:
`hermes-public-candidates-v3`; review: `hermes-independent-public-review-v3`.
The native record is the session's rows in the shared `state.db`. By owner
permission only the rows of the test sessions, the store's schema-version row
and each session's own system-prompt row were read, from temporary copies. Set
v2 was rejected: the controller had removed the file-edit tools, the observer
had no tool stream, and a native usage record was scored as absent. The
controller now starts `hermes chat --toolsets terminal,file --format
stream-json`, and the observer is built from that stream. Usage is 1 of 2: the
store keeps the usage record of the last request only. Details and limits are in
`adapters/hermes.md`.

Not ranked: Cursor Desktop (paused on the Free-plan limit), Kimi (stops at the
account limit of 3 requests per minute, again on 2026-10-07), OpenClaw (not
started). Never publish candidates v1 to v35.

## 2026-10-06: candidate v35, Cursor CLI ranked

Candidate v35 is at `artifacts/v1-expanded-release-candidate-v35/` and replaces
v34. It ranks nine configurations: the eight of v34 with the same scores, and
Cursor CLI at 78.1 (rank 9). Inputs:
`plans/survival-v1/trusted-release-index-v10.json`,
`expanded-release-status-v16.json`, `expanded-partial-coverage-v17.json`.

Cursor CLI uses three fresh captures of 2026-10-06 (build `2026.10.01-e373342`,
isolated data and config directories). Packets:
`cursor-cli-public-candidates-v3`; review:
`cursor-cli-independent-public-review-v3`. Two earlier sets were rejected: the
CLI also writes to the shared store `~/.cursor/ai-tracking/ai-code-tracking.db`,
so the root was not proved complete; and the public copy of those rows held
short vendor hashes. By owner permission only the rows of the three test
sessions were read from that store. `portable.complete_root` is a contradiction,
as for Codex CLI and Antigravity. The usage rows are absent in the native
format. Details and limits are in `adapters/cursor.md`.

Cursor Desktop is paused: an isolated signed-in profile exists, but the Free
plan reached its limit before a valid run. Hermes, Kimi, OpenClaw and Cursor
Desktop are visible with their blockers.

A short-digest scan (`short_digest_warnings`) found vendor digests of 12 hex
characters in the ranked Copilot set (`schema_hash`, `hash` in prompt-cache
state). The Cursor CLI reviewer ruled that they can stay with written reasons:
they cover vendor tool schemas and prompt segments, or paths that hold a
30-character per-user temporary directory id. If a later Copilot capture runs
under the real home, these fields must be zeroed. Never publish candidates v1
to v34.

## 2026-10-06: candidate v34, vendor instruction text blanked

Candidate v34 is at `artifacts/v1-expanded-release-candidate-v34/`. It has the
same eight ranked rows and scores as v33 (table in the next section). It
replaces v33 as the publication candidate. Inputs:
`plans/survival-v1/trusted-release-index-v9.json`,
`expanded-release-status-v15.json`, `expanded-partial-coverage-v16.json`.

By owner decision, vendor instruction text (system prompts, tool and schema
descriptions, server instructions, injected vendor messages) is replaced at
equal byte length in every file of the public packets. Claude Desktop and
Antigravity already did this. Four sets were rebuilt; all 31 metric rows are
equal to the sets they replace. Each sanitizer ends with a guard that fails when
a phrase of a blanked string is left anywhere in the packet.

| Row | Public packets | Delta review (Sonnet) | Semantic review (Opus) |
|---|---|---|---|
| Copilot CLI | `copilot-public-candidates-v7` | `copilot-independent-public-review-v7` | `copilot-independent-public-review-v5` |
| Claude Code CLI | `claude-cli-public-candidates-v9` | `claude-cli-independent-public-review-v6` | `claude-cli-independent-public-review-v5` |
| Codex CLI | `codex-cli-public-candidates-v6` | `codex-cli-independent-public-review-v6` | `codex-cli-independent-public-review-v5` |
| DeepSeek Harness CLI | `dsh-public-score-preparation-v13` | `dsh-independent-public-review-v13` | `dsh-independent-public-review-v12` |

The other four rows keep the sets named in the v33 table. Kept on purpose: one
line or value that names the operating system (Copilot, Claude Code CLI), the
Codex environment block, and the DeepSeek title request. Pi stores about 2,000
characters of tool descriptions per run and is not blanked (open source, no
sanitizer). OpenCode stores no such text. Never publish candidates v1 to v33.

Cleanup before publication: the adapter-doc copies inside some packets are older
than the repository docs; manifests still say `public_safe: false`.

## 2026-10-06: candidate v33, eight ranked rows

Candidate v33 is at `artifacts/v1-expanded-release-candidate-v33/`. It ranks
eight configurations, each with three runs, all 31 metrics resolved, and an
approved independent review of the current packet set:

| Rank | Configuration | Score | Public packets | Review |
|---:|---|---:|---|---|
| 1 | OpenCode CLI | 97.4 | `opencode-1.18.31-public-candidates-v4` | `opencode-independent-public-review-v4` |
| 2 | DeepSeek Harness CLI | 96.9 | `dsh-public-score-preparation-v12` | `dsh-independent-public-review-v12` |
| 3 | Pi | 96.4 | `pi-public-score-preparation-v7` | `pi-independent-public-review-v7` |
| 4 | Copilot CLI | 96.0 | `copilot-public-candidates-v5` | `copilot-independent-public-review-v5` |
| 5 | Claude Code CLI | 89.3 | `claude-cli-public-candidates-v8` | `claude-cli-independent-public-review-v5` |
| 6 | Codex CLI (Codex Desktop shares the row) | 87.5 | `codex-cli-public-candidates-v5` | `codex-cli-independent-public-review-v5` |
| 7 | Claude Desktop | 85.6 | `claude-desktop-public-candidates-v3` | `claude-desktop-independent-public-review-v3` |
| 8 | Antigravity | 82.7 | `antigravity-public-candidates-v4` | `antigravity-independent-public-review-v4` |

Inputs: `plans/survival-v1/trusted-release-index-v8.json`,
`expanded-release-status-v14.json`, `expanded-partial-coverage-v16.json`.
No row is provisional. Hermes, Kimi, OpenClaw and both Cursor surfaces are
visible with their blockers. The full suite passed 2,037 tests.

Owner decisions, 2026-10-05:

- One duplicate-safety rule for every row. The reader reads every container
  (a file, or a database table) from which the decoder takes a scored fact.
  Inside it, each record that states an event is one occurrence. A record
  restates a call when it holds all of the call's arguments.
- Usage credit needs input and output counts. Token semantics also needs
  cache-read and cache-write keys.
- A native action that pairs with an observed unscored action is outside the
  population.
- Antigravity runs without `--sandbox`. Only the rows of the test conversations
  were read from its shared stores.

Effects: OpenCode no longer opens its `event` table and proves order from
`message` and `part` rows (row id and insertion order must agree). Codex fell
from 90.6 to 87.5 because its rollout states each event twice. Copilot is
scored from three fresh captures; its usage comes from `session-store.db`.
Antigravity is scored from three fresh whole-state captures; its primary record
is `conversations/<id>.db`.

Privacy: a public packet may hold a digest only of public bytes, of a replay
output, or of an allowlisted preimage. `session_bench/public_digest_check.py`
enforces this in every sanitizer. Reviewers found two e-mail oracles in Claude
CLI sets that an earlier review had approved. Never publish release candidates
v1 to v32, any packet set that the table above does not name, or the sidecar
files beside a packet set (`summary.json`, `*-receipt.json`,
`*private-transformation.json`, `private/`).

Owner decisions, 2026-10-06:

- Claude Desktop runs 1 and 2 (corrective attempts of invalid captures) are
  accepted, with the disclosure in `adapters/claude-code.md`.
- Copilot keeps the packaged duplicate count (96.04). It charges the store
  `turns` table; a table-level count gives about 96.35. The rank is the same.
  The report discloses it.
- Pi keeps its earlier packets and review; its counts do not change under the
  new rules.
- The Antigravity `cache/last_conversations.json` entry stays private evidence
  beside the captures. The reviewer accepted this as a stated limitation.
- Reviewers as agent sessions on the same host, and rules that post-date older
  captures, are accepted with disclosure.

Still open: vendor system-prompt text is public in the Claude Code CLI, Codex
and Copilot packets (see the adapter docs). Codex density counts an injected
context message as useful (about 0.17). Manifests still say
`public_safe: false`.

## 2026-10-04: candidate v32, Copilot ranked

Candidate v32 is at `artifacts/v1-expanded-release-candidate-v32/`. It ranks seven
configurations: OpenCode CLI 97.4, DeepSeek Harness CLI 97.1, Copilot CLI 96.4,
Pi 96.4, Codex CLI 90.6, Claude Code CLI 90.2, Claude Desktop 86.8. Antigravity
is provisional until its review ends. Inputs:
`plans/survival-v1/trusted-release-index-v7.json`,
`expanded-release-status-v13.json`, `expanded-partial-coverage-v15.json`
(written by `scripts/add_reviewed_configuration.py`).

The Copilot row uses three fresh isolated captures of 2026-10-04 (CLI 1.0.91).
A first review rejected packet set v1: usage was scored absent, but
`COPILOT_HOME/session-store.db` holds per-response usage and was not copied. The
store and its WAL were then copied from the retained temporary roots. Both
files equal the hashes in each run's `root-end.json`. The `-shm` index was
rewritten by a read-only open before the copy and is not part of the root.
Packet set `copilot-public-candidates-v2` scores usage and reconciliation from
the store; a second independent reviewer approved it
(`copilot-independent-public-review-v2/review.json`). Details are in
`adapters/copilot.md`.

Open points from that review: the decoder should refuse usage credit when the
prompt-cache witness disagrees (no effect on these runs); Copilot (96.37) and Pi
(96.4) are closer than three judgment calls (store `turns` row as a duplicate,
store rows in the density denominator, encrypted reasoning as useful content);
future Copilot captures must copy the store in the controller. Never publish
`copilot-public-candidates-v1` or the `copilot-score-replay-*` directories.

Antigravity has three fresh captures (agy 1.2.16, attempts 02, 03, 04; attempt
01 was blocked before any model call and attempt 05 is a disclosed spare) and a
fully resolved packet set at 67.7 per run, in independent review.

## 2026-10-04: candidate v31, Claude Desktop ranked

Candidate v31 is at `artifacts/v1-expanded-release-candidate-v31/`. It ranks six
configurations: OpenCode CLI 97.4, DeepSeek Harness CLI 97.1, Pi 96.4, Codex CLI
90.6, Claude Code CLI 90.2, Claude Desktop 86.8. Copilot (57.9–98.2) and
Antigravity (33.7–97.7) stay provisional. Inputs:
`plans/survival-v1/trusted-release-index-v6.json`,
`expanded-release-status-v12.json`, `expanded-partial-coverage-v14.json`.

The Claude Desktop row uses the three retained runs. A first independent review
rejected packet set v1 (readable connector schema keys; an invented "compound
edit" action and result; two false absences). Packet set
`claude-desktop-public-candidates-v2` fixed these and a second independent
reviewer approved it (`claude-desktop-independent-public-review-v2/review.json`).
The rules that changed are in `adapters/claude-code.md`, "Claude Desktop row",
and in the `rubric.md` Matching table, "Compound shell call".

Open points: runs 1 and 2 are corrective attempts and need the owner's
acceptance on record; root location is a derivation rule, weaker than the
observed-path rows; the `&&` chain exit rule that the reviewer found only in the Codex decoder is
now in the rubric and in the Claude decoder too; no score changed. The full suite
passed 1,907 tests.

## 2026-10-04: candidate v30, five ranked rows

Candidate v30 is at `artifacts/v1-expanded-release-candidate-v30/`. It ranks
five configurations, each with three runs, all 31 metrics resolved, a sanitized
public packet set, and a review by a separate Opus reviewer agent: OpenCode CLI
97.4, DeepSeek Harness CLI 97.1, Pi 96.4, Codex CLI 90.6, Claude Code CLI 90.2.
Claude Desktop (67.9–71.9), Copilot (57.9–98.2) and Antigravity (33.7–97.7) are
provisional and not ranked. Codex Desktop shares the Codex CLI row. Hermes,
Kimi, OpenClaw and both Cursor surfaces are visible with their blockers.

The owner changed the rules on 2026-10-03 (see `rubric.md`, "Evidence tiers"):
usage, model identity and reconciliation are scored from native bytes; an
unresolved metric makes a row provisional instead of hiding it; a Desktop
surface shares its CLI row only when the session format is the same. The
changed-file rule is now equal for every row: explicit hashes, or a native
pre-image plus the native edit.

The first review round rejected four of five rows. The fixes: equal
changed-file scoring (Claude, DeepSeek, OpenCode, Pi), OpenCode's final-after
chain, action matching and migration-ledger version, and privacy. Every
sanitizer had published digests of private originals that let a reader confirm
a guessed home user name, and DeepSeek's also exposed an account balance.
Public packets now carry no digest of a private file that can be rebuilt from
public bytes; `dsh_closure.py` pins only the public form of the parent
manifest. Codex rollouts embed personal agent instructions, a skill list and
approved commands; `scripts/sanitize_codex_score_packets.py` blanks them at
equal byte length.

Inputs: `plans/survival-v1/trusted-release-index-v5.json`,
`expanded-release-status-v11.json`, `expanded-partial-coverage-v13.json`. The
`summary.json`, `*private-transformation.json`, `receipts/*.private.json` and
`private/` files beside the packets must stay unpublished. The full suite
passed 1,854 tests and 20 subtests. The work was committed to branch
`v1-candidate-v30` on 2026-10-04. Nothing is pushed or published.

Never publish the older packet directories or release candidates v1–v29; they
hold digests that confirm a guessed home user name or balance. The list, the
owner's 2026-10-04 decisions, the reviewers' residual concerns and the next work
are in `claude-handover-2026-10-04.md`.

Open points for the owner: DeepSeek repetition 1 is a calibration-named run
(it no longer scores differently); OpenCode's honest-version credit and Codex's
`multi_agent_version` reading are judgments; the reviewers are agent sessions
on the same host, not a second operator; the rule changes post-date the
captures.

## 2026-10-02 continuation: candidate v29 and offline validation

Candidate v29 is the newest local assembly at
`artifacts/v1-expanded-release-candidate-v29/`. It includes all fourteen
configurations and preserves the same four scores and ranks: Pi 99.4, DeepSeek
Harness CLI 91.5, OpenCode CLI 80.7, and Claude CLI 74.2. Codex CLI now has
private, hash-pinned closures for `work.changed_files`, `broad.event_timestamps`,
and `broad.stable_root_location`; each retained run reports 24 measured, one
contradiction, and six unresolved metrics. Its remaining usage/model,
version-signal, and complete-root issues still prevent ranking. Hermes has two
qualified captures out of three but no score replay. Neither configuration
received a score from these offline closures.

A narrow Codex Desktop audit found its retained timestamp population cannot
close `broad.event_timestamps`: the independent observer identifies only the
two turns and final responses, while the purported broader population comes
from native rollout lines. A fresh observer capture must bind all required
actions, results, and the changed-file event before they can be scored. A
separate audit also found `work.changed_files` cannot be closed from the three
retained packets: native `FileChange` records and inspect output do not replace
an independent before/after workspace receipt. The retained observers contain
no changed path or before/after hashes. Each scoring repetition needs a
capture-time receipt bound to its workspace and run that independently records
the changed-path inventory and hashes; deriving the postimage from the native
diff would still borrow native evidence.

Claude Desktop now has capture-time timestamp and root-discovery instrumentation
wired through finalization. Eval 6 covers 8 of 13 required native timestamps;
Eval 8's old unsupported command receipts cannot be repaired. Both remain
unscored, and independent per-response usage/token semantics are still open.
The per-run root interpretation is retained: one run can satisfy that metric,
and each additional scored run must independently satisfy the same discovery
and privacy checks.

The full suite passed **1,652 tests and 20 subtests**; `compileall` and
`git diff --check` passed. Candidate v29 still has only four ranked rows and
`release_goal_complete=false`. No benchmark run or qualified capture, commit,
push, tag, or publication occurred; the accidental Kimi CLI prompt is recorded
below. `expanded-release-status-v10.json` remains an attempt ledger; the score
authority is the independently replayed packets.

## 2026-10-02 continuation: Claude Desktop capture-method status

The current Claude Desktop path now addresses the eval 3 timestamp and root
discovery gaps at capture time. A local clock records the four observed turn and
response boundaries; Claude Code hooks timestamp tool-call starts and results.
Finalization binds those receipts to the independent event population and keeps
native timestamps as the scored values. A metadata-only before/after inventory
recursively covers the two canonical Claude session roots and selects the
unique synthetic transcript and Desktop metadata by session, workspace, and run
marker. The root-location metric is per run; each supplied run must satisfy the
same discovery and privacy assertions, while a separate repetition is needed
only to test repeatability.

The method is wired through finalization and replay, but the retained runs do
not yet qualify the configuration. Eval 6 proves the path works and has a
complete root receipt, but native timestamps cover only 8 of 13 independently
observed required events. Eval 8 has root and clock receipts, but its old
capture-time command projections rejected two Bash observations; the parser
fix applies only to a fresh capture because raw command text was not retained.
Neither run is promoted or used to rewrite an earlier packet. The independent
per-response usage and token-semantics gap remains separate.

The full offline suite passed **1,647 tests and 20 subtests**. Candidate v27 is
still the last assembled candidate; it predates the latest private Codex CLI
closures and Hermes' second qualified capture. Those diagnostics do not change
any score or rank. No new live model call, commit, push, tag, or publication
occurred in this continuation.

## 2026-10-02 continuation: Hermes second repetition qualified offline

The already-retained October 2 Hermes capture now has a separate offline
qualification receipt. It binds two completed prompts and stdout responses,
same-session continuation, exact native exports, usage receipts, helper-ledger
events, and protected workspace hashes to the observed `openai-codex/gpt-5.5`
route. Its private replay resolves 9/31 metrics; the September qualified
capture also resolves 9/31. Neither is a score or rank. Hermes needs a third
qualified repetition and capture evidence for distinct action/result
boundaries, response-scoped usage, and the complete native SQLite root.
Eight focused Hermes tests passed; no model call was made in this offline pass.

## 2026-10-02 continuation: Codex CLI timestamp metric closed privately

A Codex CLI-only diagnostic now binds all 13 independently observed required
events in each of the three retained repetitions to exact hashed native
rollout lines. This changes only `broad.event_timestamps` from unresolved to
measured (1/1). Together with the separate stable-root diagnostic, each run has
23 measured, 1 contradiction, and 7 unresolved states. The diagnostic remains
private and non-rankable; it does not repair the `portable.complete_root`
contradiction or close usage, model, version, or changed-file evidence. Its
artifact is `artifacts/v1-expanded-preparation/codex-cli-timestamp-private-diagnostic-v1/diagnostic.json`.
Two focused tests passed; no live model call was made.

## 2026-10-02 continuation: Kimi CLI probe incident

An unprepared diagnostic invocation, `/opt/homebrew/bin/kimi -p --help`, was
interpreted by Kimi CLI 2.1.1 as a prompt and submitted one live provider
request. It was not a benchmark workload or qualified capture and is excluded
from all score evidence. The CLI's response exposed a new session identifier;
that identifier was not copied into this ledger, and the session/history was
not inspected. No retry was made. A separate `kimi --help` invocation showed
no supported inner-request pacing option, so a safe repeat of the failed RPM3
workload has not been established.

## 2026-10-02 continuation: Claude Desktop usage-source audit

An offline audit of the retained synthetic packets and current capture path
found no independent per-response usage source. The GUI observer records
visible responses without usage, and the composite observer does not add usage
fields; the finalizer reads `message.usage` from the native transcript only
after observer construction. The cumulative Usage panel cannot assign counts
to an individual displayed response. The existing v3 credit therefore remains
withdrawn. This is a capture-path gap, not evidence that Claude Desktop cannot
provide the data. A future capture needs a separate response- or
request-scoped usage source, a stable link to each displayed response, and
documented input/output/reasoning/cache semantics with missing distinct from
zero before native transcript ingestion.

An offline Copilot audit did not close a metric in the three selected runs.
Their native records have timestamps on 38/43/44 rows, but no retained
whole-root inventory proves those files are the complete synthetic sessions;
the two usage checkpoints per run are cumulative and cannot be differenced into
per-response values. Preserve the corrected packets. A future run needs
metadata-only before/after inventories of the full session root, quiescent
snapshots, exact session/workspace/run binding, and an independent per-response
usage source.

## 2026-10-02 continuation: Antigravity source-bound diagnostics and Claude capture readiness

An additive Codex CLI diagnostic now binds the three retained metadata-safe
normal-root receipts to the exact native files in their replay packets. It
changes only `broad.stable_root_location` from unresolved to measured; each run
has 22 measured, 1 contradiction, and 8 unresolved metric states. The diagnostic
is private and non-rankable, and does not change the retained
`portable.complete_root` contradiction, timestamp coverage, or release packets.
Its exact bindings and limits are at
`artifacts/v1-expanded-preparation/codex-cli-root-private-diagnostic-v1/diagnostic.json`.

The retained Antigravity R1/R2 stdout streams now have an offline response-usage
projection. It accepts only the unique terminal `DONE` response step whose
streamed text equals both the final result and independent observer response,
and binds the usage fields to the exact stdout bytes, step, and response hash.
The cumulative result usage is excluded, and a missing cache-write field stays
missing. This supplies private response-scoped usage evidence; token semantics,
model identity, reconciliation, and a complete native family remain unresolved,
so the existing three-run diagnostics and candidate scores are unchanged.

An offline Antigravity join audit could not close `causal.action_result`.
Native call and result steps have no shared invocation ID, while the independent
stdout-step ID identifies a completion only. Joining them by position would
infer causality from order. A future capture needs a stable ID shared by native
call, native result, and independent observation, or an explicit receipt bound
to those exact records.

Future Antigravity captures now use metadata-only before/after root inventories,
reject unexplained changes outside the newly created session directory, verify
whole-root quiescence, and bind every copied file by identity and hash. Its
bounded receipt does not establish external companions or `portable.complete_root`,
and cannot qualify retained captures that lack full after-root inventories.
The controller explicitly requests Claude Sonnet 4.6 at medium effort and
records that as a requested selection; the stream does not independently confirm
the actual model. No capture or model call was made.

Claude Desktop instrumentation is also present: the local clock stamps the four
submitted-turn/visible-response boundaries, hooks timestamp action and result
lifecycles, and a metadata-only before/after discovery selects the unique
synthetic-session transcript and Desktop metadata from the two canonical roots.
The rubric scores root location per run; another run tests repeatability. Eval 8
has these receipts but remains unscored because the capture-time parser rejected
two compound Bash observations. The corrected parser applies to a fresh capture;
the retained run was not rewritten. Claude Desktop's independent response-usage
and token-semantics gap remains separate.

The full suite passed **1,641 tests and 20 subtests**; `compileall` and
`git diff --check` passed. Candidate v27 remains unchanged with four ranked
configurations; full fourteen-configuration scoring is incomplete. No live
model call, commit, push, tag, or publication occurred.

## 2026-10-02 continuation: reject unsupported Claude Desktop usage credit

An audit of candidate v26 found that its Claude Desktop v3 replay changed
`claude_projection.usage_mode` from `none` to `full` while keeping the native
transcript and independent observer byte-identical. The v3 observer has no
response usage values or token scopes; the comparator then used native
`message.usage` as the matching evidence. That credit conflicts with the
independent-observer rule, so v3 remains retained for audit but is no longer
the selected partial-coverage source.

Candidate v27 uses the corrected Claude Desktop v2 receipts: 22 measured, 5
native-absent, and 4 unresolved states per repetition (27/31 resolved). The
corrected Copilot v6/v5 successors remain selected: they retain corrected v2
workload and observer hashes, leave all root/location metrics unresolved, and
resolve 18/31, 17/31, and 18/31 states through independently supported
successor evidence. Candidate v27 is at
`artifacts/v1-expanded-release-candidate-v27/`; it represents all 14
configurations and preserves the four existing scores, but full
fourteen-configuration scoring remains incomplete. The focused release tests
passed (10 tests), and an independent inventory check verified all 1,174
candidate-manifest entries against file sizes and SHA-256 hashes. No live model
call, commit, push, tag, or publication occurred.

## 2026-10-02 continuation: Claude Desktop eval 8 captured, not yet scoreable

Eval 8 ran in a fresh synthetic workspace on Claude Sonnet 5.5 at Medium
effort. The local GUI clock recorded all four submitted-turn/visible-response
boundaries, and the hook receipt recorded three complete tool-call lifecycles
with start/end timestamps. A metadata-only before/after inventory isolated the
session transcript and Desktop metadata in Claude's canonical roots and passed
the finalizer's source-discovery validation. Those receipts support a per-run
root claim and capture-time timestamps; they do not substitute observer times
for native timestamps, and they have not yet produced a scored replay.

Root-location scoring is per run: one complete deterministic discovery can
resolve that run's metric; another run is needed only to test repeatability.
The capture tool inventories metadata before and after the run, then isolates
the unique changed transcript and Desktop metadata file by session identity,
workspace, and run marker. It derives `CLAUDE_HOME/projects` (or
`~/.claude/projects`) and
`~/Library/Application Support/Claude/claude-code-sessions`, rejects
caller-supplied overrides, and verifies selected paths and root hashes. Eval 8
has such a private receipt, but finalization stopped before a score packet was
written: the capture-time semantic projection rejected two safe compound Bash
calls because they included simple parameter expansion in output-only `echo`
segments. The parser now permits only simple read-only parameter expansion in
`echo`/`printf` arguments; command substitution remains unsupported. Focused
tests cover both cases. A sanitized diagnostic confirms the revised grammar
recognizes eval 8's helper phases, but that post-run diagnostic is not used to
rewrite its unsupported capture-time receipt. A valid scored replay therefore
needs a fresh capture with the corrected instrumentation.

There is a separate usage gap. Corrected Claude Desktop v2 leaves
`attribution.usage` and `attribution.token_semantics` unresolved. The retained
v3 replay closes those only by using native `message.usage` as observer
matching evidence, so it is not scoreable under the rule against borrowing
native facts as independent observer truth. The app's visible Usage panel shows
cumulative context and plan limits, not per-response usage, and cannot supply
those two metrics. The all-31-metrics-across-three-repetitions score gate is
unchanged. The old v0.4 campaign rubric is preserved byte-for-byte at
`docs/survival-v1/rubric-v0.4-frozen.md` so its historical plan hashes remain
valid.

The hook projection keeps raw command text out of receipts and binds accepted
semantic fields to a capture-time digest. The finalizer checks action kind,
arguments, target, working directory, helper phases, and compound-edit claims
against that binding. The focused hook/finalizer/composite suite passed 137
tests after the parser adjustment; the full suite passed 1,625 tests and 20
subtests. Candidate v27 and existing scored packets are unchanged. Eval 8
remains private and unscored; no new candidate, commit, push, tag, or
publication was produced.

## 2026-10-02 continuation: Copilot changed-file closure and candidate v26

An additive, private Copilot successor replay uses the retained original
isolated-home snapshot source for capture 01. It resolves only
`work.changed_files`, raising that run from 17/31 to 18/31; the other 30
metric states match v5. Captures 03 and 06 remain 17/31 and 18/31. The new
packet is private, unreproduced, and unranked; v5 and all earlier packets remain
unchanged. The builder pins the exact source-family hashes and copied-event
equality, rejects a changed source inventory, and retains `rankable: false`.

Candidate v26 is at
`artifacts/v1-expanded-release-candidate-v26/`. It includes all 14
configurations, no `N/A` placeholders, and the same four verified scores and
ranks: Pi 99.4 (1), DeepSeek Harness CLI 91.5 (2), OpenCode CLI 80.7 (3), and
Claude CLI 74.2 (4). Copilot's private coverage now reads 18/31, 17/31, and
18/31. The candidate inventory has 1,174 files with no missing, extra, or
changed entries. The all-31-metrics-across-three-repetitions gate is unchanged;
the other ten configurations still have no overall score or rank, so the
fourteen-configuration scoring goal remains incomplete. No live run, commit,
push, tag, or publication occurred.

Bounded Sol audits found no offline score closure for Cursor Desktop or Claude
Desktop. Cursor eval3/eval4 remain 0/3 score-qualified: missing complete-root
and independent observer evidence, per-response usage, six eval4 result exit
codes, and a Desktop score adapter remain. A repeat UI run without capture
instrumentation would not repair these gaps. Claude Desktop's two unresolved
states remain event-time/action linkage and original storage location; the
retained hook receipt's local clock and caller-selected paths do not prove
either. Antigravity's retained native records have no exact action/result IDs,
so the causal join remains unresolved. Kimi's installed CLI has no supported
inner-request pacing option for the organization RPM3 interruption. Codex
Desktop remains 15 measured, one contradiction, and 15 unresolved per replay;
Hermes has one qualified capture; OpenClaw still lacks a verified usable route
and isolated read-only capture. No unresolved evidence was converted to zero.

The retained Hermes `qualification-v2` capture also now has an additive private
31-state diagnostic at
`artifacts/v1-expanded-preparation/hermes-private-partial-diagnostic-v1/`.
It reports 7 measured and 24 unresolved states from one qualified repetition;
five deep states bind separate submitted-prompt, process-stdout, or controller
witnesses, and two native-only broad-format facts are measured. Compound shell
calls are not split into extra actions, session-level usage is not assigned to
responses, and score/rank/public safety remain false. Its focused tests passed
(2 tests); the shared score gate and candidate v26 are unchanged. The full suite
passes **1,434 tests and 20 subtests** in 42.36 seconds after the additive
Copilot/Hermes diagnostics.

## 2026-10-01 continuation: Cursor eval4 private replay and candidate v25

Cursor Desktop eval4 is complete in the signed-in synthetic workspace. Its
retained, exact-session rows and two-turn transcripts now have an additive,
hash-bound private decoder replay at
`artifacts/v1-expanded-preparation/cursor-desktop-eval4-private-replay-v1/`.
The normalized replay decodes two turns, two responses, seven actions/results,
nine relations, and one file change. Six native tool results have no exit code;
usage accounting, complete native-root/family closure, independent observer
truth, 31-cell scoring, and public-safety review remain unresolved. The run is
still **0/3 score-qualified repetitions**; no new Cursor prompt was sent.

The local candidate v25 is at
`artifacts/v1-expanded-release-candidate-v25/`. Its 1,174-file manifest
inventory verifies with no missing, extra, or changed files. It represents all
14 configurations, includes the new Cursor replay digest, and retains four
verified ranks. The all-31-metrics-across-three-repetitions gate is unchanged;
full fourteen-configuration scoring remains incomplete and the candidate is
not publication-ready.

Claude Desktop now has an opt-in capture-time hook receipt path in its
finalizer. The private receipt validates run/session/workspace identity, safe
fixture targets, complete hook lifecycles, and receipt-clock ordering. A
separate private source-location receipt binds only the two caller-selected
files; it does not prove root completeness or resolve the location metric.
Existing captures remain unchanged, and hook facts do not enter the score
comparator. The full suite passed **1,432 tests and 20 subtests**;
`compileall` and `git diff --check` passed. No additional live model calls,
commit, push, tag, or publication occurred during this offline continuation.

## 2026-10-01 continuation: Cursor R2 and offline evidence closures

Cursor Desktop eval 4 resumed in the exact synthetic workspace and chat. The
existing Grok 4.6 Medium selection was visible. R2 was submitted once; the
helper ledger now records inspect exit 0, baseline exit 1, and final exit 0.
All three final cases passed. The final `checkout.py` SHA-256 is
`a020043db82bec2df47204c03e11ab40c5275139e0b122723810304d81e7050f`; the
pre-run `bench_check.py` hash is unchanged. The exact two-turn native transcript
is privately checkpointed at
`artifacts/survival-v1-runs/cursor-desktop-eval-4/capture/r2-transcript-checkpoint.private.jsonl`
with SHA-256 `e22493c7c1dce7348781c1badb0376a7451b9efaf989bd83380e86e82c8c74a6`.
A separate read-only, exact-session query retained 25 rows from one global
store: one composer, 20 bubbles, three checkpoints, and one `ofsContent` row.
The 20 bubble IDs and both referenced checkpoints close; one selected checkpoint
row is unreferenced. No other database, workspace, session, or unkeyed rows were
queried. Native-family/root coverage remains incomplete, and the run has 0/3
score-qualified repetitions.

Offline source-backed closures created additive, private successor diagnostics:
Antigravity resolves 10/31 in each retained run; Copilot v5 resolves 17/31,
17/31, and 18/31, including one additional changed-files state from run 06.
Copilot v5 is reproducible and retains the 31-metric/three-run gate; it remains
private and unreproduced. Codex CLI's retained replays resolve 21/31 with one
contradiction; its stdout observer now rejects a baseline before inspect and a
final check that starts before the edit completes. Claude Desktop's remaining
event-time linkage and original-location states cannot close from its retained
independent evidence. Cursor's retained `bubble.tokenCount` zeros are
initialized defaults; actual turn usage is separately stored and absent from
the retained composer, so usage metrics remain unresolved. Pinned OpenClaw
v2026.9.5 public docs support shared OAuth read-through for a fresh named agent
with its own workspace and agent directory, without copying refresh credentials.
No owner state or credentials were read.

The full suite passes **1,374 tests and 20 subtests**. The all-31-metrics-across-
three-repetitions gate is unchanged. Local candidate v24 is rebuilt from the
latest Copilot diagnostics and Cursor Desktop attempt ledger. Its 1,174-file
inventory verifies with no missing, extra, or changed files. Four configurations
have verified scores; all fourteen are represented and have attempt or capture
evidence, but the fourteen-configuration scoring goal and full-scope publication
remain incomplete. No commit, push, tag, or publication occurred.

## 2026-10-01 Cursor Desktop retry — completed, still unscored

After the owner closed the Cursor modal, a fresh synthetic Desktop run
(`cursor-desktop-eval-3`) completed both turns in the signed-in Cursor account
with the existing **Grok 4.6 Medium** selection. The prior Free-plan limit dialog
did not recur. R1's helper exited 0 for inspect and 1 for the expected baseline
failures; R2's final helper exited 0 with all three cases passing. The checkout
change is retained in the attempt workspace.

The exact session transcript and 37 session-keyed bubble/checkpoint rows were
copied into the private attempt package. Their indexed 32 bubbles and two
checkpoint references close within the selected global-store rows. The query
was limited to this run's session ID in one Cursor global database; no other
database files or sessions were queried. Seven additional rows in `agentKv` and
`inlineDiff` remain a hash/size inventory only. This was the normal signed-in
profile rather than an isolated profile, so it does not prove complete native
root closure.

The bounded Cursor Desktop decoder now reports 12 known and 7 unknown facts in
its 19-field registry, with two turns, two responses, 14 actions, 14 results,
and one file change. Usage, token semantics, reconciliation, complete-root
coverage, and canonical equality remain unresolved. These private diagnostics
are not the 31-metric scoring replay, public-safe evidence, independent
reproduction, or a numeric score/rank. The agent also wrote a self-reported
`.survival-observer.jsonl` at the workspace root, outside `fixture_project`; it
is retained but excluded as independent observer truth. Candidate v16 and the
all-31-metrics-across-three-repetitions score gate remain unchanged. No new
spending, commit, push, tag, or publication occurred.

## 2026-10-01 continuation: candidate v16 and Cursor login retry

Candidate v16 is assembled at
`artifacts/v1-expanded-release-candidate-v16/` from trusted release index v4,
status ledger v3, and partial-coverage index v2. It represents all fourteen
configurations without `N/A` placeholders and has four complete, independently
reviewed scores: Pi 99.4, DeepSeek Harness CLI 91.5, OpenCode CLI 80.7, and
Claude CLI 74.2. The other ten rows retain their measured partial coverage,
capture milestones, or specific blockers; they have no numeric score or rank.
The all-31-metrics-across-three-repetitions scoring gate is unchanged, so the
fourteen-configuration scoring goal remains incomplete. Partial diagnostic
inputs still await public-safety review and independent reproduction.

The candidate's 1,174-file inventory was verified with no missing, extra, or
changed files; `release-manifest.json` SHA-256 is
`fe33f211ca635b0cf4cb1a68ef8685201fc648778c045077b2b4ea68f362cc00`. The
release builder now derives verified-configuration counts from the scorecard
instead of hard-coding three. The full suite passed **1,336 tests and 20
subtests**; `compileall` and `git diff --check` passed.

After the owner asked to retry Cursor, the app reopened and displayed the
signed-in account. Its current workspace was an earlier calibration project;
that workspace and its prior session were left untouched, and no new workload
was submitted.
The Desktop capture/score path still needs closure before another repetition can
qualify. Pi already has the three full score replays in the current candidate,
so no additional Pi call was made. No commit, push, tag, or publication occurred.

## 2026-10-01 authenticated Pi and Cursor follow-up

The owner confirmed Pi and Cursor are signed in. Pi completed three fresh
two-turn JSON-event runs (`pi-openai-codex-2026-10-01-07` through `-09`). Each
retained a complete isolated root snapshot and complete JSONL record family;
read-only `get_session_stats` receipts reconcile all completed assistant
message usage, including tool-use steps. Successor private replay v7 resolves
all 31 metric states in all three repetitions. Each pinned replay passed under
macOS OS isolation, all outside-read/write/network probes were denied, and six
tamper controls passed per packet. These remain private diagnostic receipts:
the packets are not public-safe or independently reproduced, so Pi receives no
numeric overall score yet.

Cursor CLI also completed one fresh signed-in normal-root two-turn run
(`cursor-cli-2026-10-02-r1`). Metadata-only before/after receipts bind the exact
new project-keyed chat store and transcript families; only those new synthetic
files were copied. The retained transcript decodes two turns, two responses,
six actions, and one changed file, but no tool results. The ACP inventory
accounts for 64 blobs, with 43 outside its currently decoded known graph; tool
and thinking payload semantics, complete semantic-family closure, and
comparable density remain unresolved. This CLI run does not supply Cursor
Desktop evidence and does not qualify a Cursor score.

The all-31-metric score gate is unchanged. The fourteen-configuration candidate
continues to represent every scoped surface with explicit evidence coverage;
partial diagnostics are not converted into overall scores or ranks. No commit,
push, tag, or publication occurred.

Candidate v13 is at `artifacts/v1-expanded-release-candidate-v13/`. It has all
14 configurations, no `N/A` placeholders, and the three previously reviewed
scores. The 674-file release inventory verified with no missing, extra, or
changed files; manifest SHA-256 is
`2b250d550a2135db943d6ab7b994e412ec1fa9a2c53734ba10213986f892a25c`. Pi is
shown as three private 31/31 diagnostic replays, not a ranked score. The
limited-cohort score gate is met; partial-coverage publication readiness and
full-scope publication eligibility remain false, and the fourteen-configuration
scoring goal remains incomplete. The full suite passed **1,334 tests and 20
subtests**; `git diff --check` passed.

## 2026-10-01 Pi capture and replay follow-up

The owner confirmed Pi is logged in. Three fresh JSON-event captures
(`pi-openai-codex-2026-10-01-04` through `-06`) each completed both workload
turns. Their private native-score packages replayed under OS isolation, passed
tamper controls, and detected the selected-response-loss control. Each replay
resolved 15 of 31 metric states (15 measured, 16 unresolved, no contradictions
or native-absent states). Pi now has three completed diagnostic repetitions, but
no numeric score or rank: the full 31-metric gate, public-safety review, and
independent acquisition requirements remain unmet. The failed OAuth attempts
and earlier text-mode capture remain in the append-only attempt history.

The attempt ledger includes all eight Pi attempts and pins the capture results,
score packages, and replay receipts. The all-31-metric rule for numeric overall
scores is unchanged. The candidate keeps all fourteen configurations visible
with explicit evidence states and no `N/A` placeholders; only the three fully
verified configurations receive numeric scores. Full fourteen-configuration
scoring and publication eligibility remain incomplete.

Candidate v12 is the refreshed local snapshot at
`artifacts/v1-expanded-release-candidate-v12/`. Its 674-file manifest inventory
has no missing, extra, or changed files; the release-manifest SHA-256 is
`b08ccaa6f835957461aae47b5fa18346b7bac7b32078fd77c8a59b30a016f4cc`. It has
all fourteen coverage rows and no `N/A` placeholders. The three-score limited
cohort gate is met; partial-coverage publication readiness, full-scope
publication eligibility, and the fourteen-configuration scoring goal remain
false. The full suite passed 1,329 tests and 17 subtests, and `git diff --check`
passed. No commit, push, tag, or publication occurred.

## 2026-10-01 Claude Desktop replay correction

The retained corrected Claude Desktop v2 packets used `usage_mode: none` even
though the native transcript contains response-scoped usage with stable message
and turn joins. The projection also missed native `cache_read_input_tokens` and
`output_tokens_details.thinking_tokens`. The decoder now maps these names and
the v3 private successor replays use full native usage projection. Each of the
three repetitions now resolves 29/31 metric states, up from 27/31, with
`attribution.usage` and `attribution.token_semantics` measured. The decoder
correction is covered by a native-projection regression test, and the full suite
passed 1,329 tests and 17 subtests after this change.

The remaining two states are still open because the retained GUI observer did
not independently bind the native edit action ID/command for timestamps, and
the old inventories did not record each selected artifact's source location.
They cannot be filled from inferred paths or native timestamps alone. The
shared decoder correction was also replayed against all three retained Claude
CLI runs; their 31 metric states were unchanged.

## Latest continuation — 2026-09-30 login follow-up

The cheapest useful v1 remains a fourteen-row evidence report with the current
all-31-metric gate for every numeric overall score. That keeps the three reviewed
scores intact and shows the other eleven configurations by their verified
capture, diagnostic, or blocker state; it does not call those eleven fully scored.
Changing that gate to award partial overall scores would require a methodology
decision and new review rules. The owner has required all fourteen configurations
to appear in v1; the scoring-gate choice remains unresolved and no publication has
occurred.

- The first refreshed Pi attempt used the existing OpenAI Codex OAuth route.
  `pi auth check --no-refresh` reported ready and the pinned `gpt-5.5` model was
  available, but the first actual request failed with
  `refresh_token_reused_401`. The attempt stopped after one model submission;
  no retry or second turn was made.
- Cursor CLI now has three fresh-root repetitions in
  `artifacts/survival-v1-runs/cursor-cli-2026-09-30-r{1,2,3}`. All six turn
  submissions completed with the expected response boundaries, and each capture
  retained its transcript and session-specific ACP SQLite family. The bounded
  ACP inventory closed the copied files and known graph references, but tool and
  thinking payloads remain semantically unsupported, the full semantic family
  and comparable density are unqualified, and no Cursor native-to-score packet
  or public review exists. These captures therefore remain unscored.
- Astra medium recommends a fourteen-configuration evidence report while keeping
  the existing 31/31-by-three-run gate for numeric scores. The choices are
  compatible: every surface appears, only the three verified configurations are
  ranked, and no partial overall scores are introduced. Candidate v9 still needs
  public-safety preparation and bounded review/replay for the five partial
  diagnostic families before full-scope publication.
- Candidate v9 is in `artifacts/v1-expanded-release-candidate-v9/`. It represents
  all fourteen configurations without `N/A` placeholders, retains the three
  verified scores, and records the new Cursor and Pi evidence. The 674-file
  manifest verified with no missing, extra, or changed files; its SHA-256 is
  `532b96f8c92a7ab43a8b79c55a9614d30166aa6168fc1bcf6467ff6dc9cedde5`. The
  limited three-score gate is met; partial-coverage review remains incomplete,
  full-scope publication eligibility is false, and the full scoring goal remains
  incomplete. No commit, push, tag, or publication occurred.
- A display-only report correction now separates completed two-turn captures
  from score-qualified repetitions. The focused builder suite passed (5 tests),
  the release builder completed, and the 674-file candidate inventory was
  rechecked. The latest full suite remains the previously recorded 1,309 passing
  tests.

## Owner direction — 2026-09-30

The owner rejected N/A-only entries and requires all fourteen configurations in
v1. The candidate now gives every configuration a coverage record: complete
verified score, per-run partial diagnostic state counts, a qualified or
unqualified capture milestone, or the specific operational blocker. Overall
scores and ranks still require three independently reviewed 31-metric runs;
partial counts are never converted into overall scores or rank positions.

- Candidate v9 supersedes v8 in
  `artifacts/v1-expanded-release-candidate-v9/`. It has all fourteen rows and
  coverage receipts, preserves the three existing verified scores (DeepSeek
  Harness CLI 91.5, OpenCode CLI 80.7, Claude CLI 74.2), and includes no private
  raw partial receipts. Its full-scope publication flag is false while five
  partial diagnostic families await public-safety review, independent
  reproduction, and replay. The 674-file inventory verified with no missing,
  extra, or changed files; the release-manifest SHA-256 is
  `532b96f8c92a7ab43a8b79c55a9614d30166aa6168fc1bcf6467ff6dc9cedde5`.
- The five diagnostic families are Codex CLI (21 measured, 1 contradiction,
  9 unresolved per run), Codex Desktop (15, 1, 15), corrected Claude Desktop
  (22 measured, 5 native-absent, 4 unresolved), corrected Copilot (16, 15),
  and Antigravity (9, 22). Each row has three run-level states in the coverage
  JSON. These local counts are not public-safe score evidence and do not change
  the leaderboard.
- The Codex CLI checkpoint's 19/1/11 count belongs to stdout-v1. Astra's
  hash-pinned read-only audit confirmed stdout-v2 as the newer source, with
  21/1/9 per run; the newer replay resolved two additional metrics. Its
  receipts still declare `public_safe: false` and
  `independent_reproduction: false`.
- Hermes has one qualified two-turn capture. Cursor Desktop has one evaluated
  capture without native-family qualification. Cursor CLI has three completed
  private two-turn captures but no qualified score replay. Pi remains blocked
  after the refreshed attempt repeated `refresh_token_reused_401`. OpenClaw and
  Kimi retain their named blockers. The full scoring goal remains incomplete
  even though all fourteen configurations are represented.
- Astra's bounded v5 review confirmed the 14 coverage records, source hashes,
  current per-run states, and publication flags. It caught a stale Codex CLI
  blocker list and wording that overstated partial detail in `scorecard.json`;
  both were corrected before v6.
- Final validation: **1,309 tests passed**, the focused builder tests passed
  (**5 passed**), `compileall` passed, and candidate manifest integrity passed.

The earlier limited-cohort score gate remains met for the three already
verified configurations. The complete 14-row report is not yet publishable:
its five partial count summaries need public-safe derivatives and independent
reproduction. The public report card is hosted from the separate Agent Sessions
repository; this checkout-only work did not access it.

The public report card is hosted from the separate Agent Sessions repository.
This checkout-only continuation did not access or update that repository; a
cross-repository publication step still needs explicit owner authorization.

## Earlier paused checkpoint — 2026-09-29 (historical)

No further benchmark/model calls, review loops or scheduled continuation are authorized
while paused. Resume only when the owner decides the next scope.

- Publicly reviewed local scores: DeepSeek Harness 91.5, OpenCode CLI 80.7,
  Claude CLI 74.2. The latest assembled local candidate is v2; it predates the
  latest partial replay/status updates. No v1 publication, tag, push or commit.
- Private partial replays: Codex CLI 19 measured + one contradiction + 11 unresolved;
  Claude Desktop corrected v2 27/31 resolved; Codex Desktop 16/31;
  Copilot corrected v2 16/31; Antigravity 9/31. These cannot receive overall ranks.
- Hermes: one qualified same-session two-turn capture, qualification-v2.
- Kimi: corrected 05 reached the API, repaired the fixture, then failed its final
  response on organization RPM3. Stock CLI 2.1.1 has no supported inner-request
  pacing option. Existing credit was not shown exhausted; no further calls.
- OpenClaw: isolated two-turn controller prepared, but OAuth readiness fails.
  Shared refresh credentials were not copied. Access-only feasibility investigation
  was interrupted before a conclusion; no live call was made.
- Pi and Cursor need interactive login refresh. Other N/As include benchmark
  observation/provenance gaps, not demonstrated harness failures.
- Latest full suite: 1,298 passed before the subsequent desktop root correction
  and Antigravity integration. Their focused tests passed (45 and 33 respectively).
  A final full suite/package rebuild remains undone. Historical v0.4 regeneration
  was byte-identical. Unrelated untracked files remain preserved.

Unresolved release decision: whether to keep the strict all-31-metric overall
gate, publish explicit partial coverage, or continue further capture work.
All original and superseded evidence is preserved; use the corrected v2 desktop
and Copilot packets, not their superseded v1 root-completeness claims.

## 2026-09-29 live continuation (superseded by the latest checkpoint)

The requested full fourteen-configuration benchmark is **not complete**.
All fourteen surfaces have retained attempt or preflight evidence, but only
three configurations currently have complete independently reviewed public score
packets: DeepSeek Harness CLI (91.5), OpenCode CLI (80.7), Claude CLI (74.2).
These are v1 workload scores, not comparable percentages from v0.4.

The local candidate is `artifacts/v1-expanded-release-candidate-v2/REPORT.md`.
It contains nine verified runs and 279 resolved metric cells, full replay source,
explicit sanitized native derivatives, separate operator reviews and hash-bound
reproduction instructions. A separate operator on the same host is not a fresh
independent acquisition. The full measurement goal remains false even though
this smaller cohort meets the minimum-three publication gate. Nothing has been
published.

N/A is not a zero and does not establish native data loss. There are two types:

- Evidence incomplete: working captures exist, but observation, decoding,
  complete-family acquisition or public verification is unfinished.
- Run blocked: authentication, provider rate limits or a retired configured model
  prevented a qualified run.

Current evidence:

- Claude CLI: three reviewed 31-metric OS-isolated replays, including retained
  root provenance and explicitly transformed public copies.
- OpenCode CLI 1.18.31: three fresh two-turn captures with isolated HOME/XDG/DB
  launch proofs. All 31 metric states resolve. Resolved native loss or
  contradiction still scores as loss, not success.
- DeepSeek Harness CLI (`dsh`) 0.2.0-rc.2: three qualified two-turn captures;
  v3 public packets include the same-session persisted cache. Its aggregate
  usage conflict is measured honestly rather than hidden by observer inference.
- Codex CLI: each of three private replays has 19 measured metrics, one native
  root-completeness contradiction and 11 unresolved metrics. Retained stdout
  supports independent tool observations, but cumulative thread tokens cannot
  supply response-scoped usage. Model, timestamps, root provenance and some
  edit/causality evidence remain incomplete.
- Claude Desktop: three corrected v2 private replays resolve 27/31 metrics.
  Usage, token semantics, timestamps and original root location remain unresolved.
  The v1 root descriptor was derived from aliases, which did not establish
  source paths; it is superseded and cannot receive stable-root credit.
- Codex Desktop: 16/31 metrics resolve; missing independent tool/usage populations
  cannot be borrowed from native data or another surface.
- Copilot: three qualified workload captures (01, 03, 06), each now has an offline
  partial replay with 16/31 resolved metrics. Capture 06 retains companions but
  its original native-copy/quiescence proof is incomplete. The superseded v1
  replay overclaimed that boundary; corrected v2 leaves it unresolved. Cumulative
  usage does not supply response-scoped usage.
- Antigravity: three completed two-turn captures (02–04) now have closed private
  replays, each with nine measured and 22 unresolved metrics. Native result call
  joins, model, usage, complete family and broad format proofs remain unavailable.
- Hermes: R1 and same-session R2 completed on existing `openai-codex/gpt-5.5`.
  The append-only continuation preserves the original failed export receipt and
  original R1 evidence. The offline qualification-v2 receipt verifies both
  assistant canaries in turn order. Only one repetition exists; score closure remains open.
  The earlier launcher runtime-path change was restored and verified.
- Cursor CLI: actual prompt requests rejected the existing login despite a
  successful status command. Cursor Desktop has one evaluated capture, additional
  unqualified native SQLite rows and a prior Free-plan usage-limit rejection.
- Pi: its actual request failed with OAuth `401 refresh_token_reused`; interactive
  login refresh is required before another model attempt.
- Kimi: the corrected 05 run completed R1 and repaired the fixture in R2, but
  its final response hit organization RPM3 HTTP429. Both native families match
  source inventories and the exact key scan is clear. The 04 syntax rejection
  reached no provider request. Credit is not established as exhausted.
- OpenClaw: a saved `agent --local --session-id` two-turn controller now has
  offline checks, but isolated state cannot use the current shared OAuth store.
  The original `openai/gpt-5.4` route was retired; replacement route readiness
  fails before submission. Copying the refresh credential could compete with
  the ambient store, and legacy `codex/*` has no supported models.

The latest full test run passed 1,298 tests before the desktop root correction. A subsequent release-goal regression
and focused release-builder checks also passed. v0.4 leaderboard regeneration
was byte-identical. Final validation follows the remaining partial-observer work.

`plans/survival-v1/expanded-release-status.json` is an attempt ledger, not a
scoring authority. `scripts/build_expanded_release.py` independently executes
pinned reviewed packets before granting scores. It never publishes. Existing
subscriptions and prepaid credit are authorized; no new spending or top-ups are
needed for the current preparation work. Interactive Pi/Cursor login remains a
user action, separate from code and evidence gaps owned by this benchmark.

## Original preparation snapshot

Release verdict: **NO-SHIP**. This implementation pass reopens v1 for the
owner's 14-configuration scope. It does not publish, rank, or re-label the
historical five-row candidate as independently reproduced.

## Implemented

- Explicit 14-row manifest with the ten v0.4 aliases preserved and separate
  Codex, Claude, Cursor desktop and DeepSeek Harness CLI rows.
- Opt-in release evidence and score APIs; historical campaign/schema entry
  points remain unchanged. All scoped rows stay visible.
- Broad population scoring counts distinct required events; duplicate native
  occurrences remain in the denominator. Empty incomplete populations are
  unresolved, and access dependency failures are measured failures rather than
  claims of native absence.
- Codex schema-version evidence scans and hashes native records. Benchmark
  dispatch names and CLI build versions cannot earn schema-version credit.
- Codex/Claude CLI density can use an explicitly bound native JSONL package. Every
  logical record uses the same canonical UTF-8 JSON byte-counting rule, with
  metadata and unknown records retained in the denominator. Without a complete
  matching package, density stays unresolved. Desktop companion inventories and
  OpenCode SQLite accounting remain unresolved.
- Readable-rationale populations bind exact observer bytes and the run identity.
  Missing native responses cannot disappear from the expected population;
  incomplete capture boundaries remain unresolved.
- Timestamp evidence retains captured records for inspection, but scores stay
  unresolved until independent expected event populations are implemented.
- Release rows must match their manifest harness identity. Synthetic input and
  caller-provided reproduction flags cannot promote aggregate totals or ranks;
  the full independent native-to-score verifier is still required.
- Private closed native replay packages, with host-side inventory verification
  before bundled code executes and explicit limits on reproduction claims.
- DeepSeek Harness `0.2.0-rc.2` preflight, isolated `DSH_HOME`, plain-v4 native
  inventory and explicit session continuation for R2. No DSH model submission
  has been made; full semantics and compressed decoding remain unqualified.

## Retained evidence reused

Twelve retained Codex/Claude packages decoded successfully under packaged source
snapshots. The local receipts are in
`artifacts/v1-expanded-preparation/native-replay/summary.json` and
`receipts.jsonl`. They establish native decoding only, not full score replay,
public safety, OS-enforced isolation, independent reproduction or complete-root
acquisition. See [native-replay.md](native-replay.md).

The machine-readable readiness command is:

```sh
python3 scripts/v1_release_status.py --out artifacts/v1-expanded-preparation/readiness.json
```

It currently reports **14 scoped rows, 7 with retained attempts, 7 unattempted
under v1, and 0 cleared for release ranking**. Historical v0.4 results do not
change those v1 readiness counts.

## Remaining acceptance work

1. Finish corrected broad evidence, including independent timestamp populations
   and complete comparable native density. Desktop companion inventories and
   OpenCode's SQLite inventory still need complete session-bearing boundaries.
2. Rebuild successor evidence packages under the corrected source and bind the
   parent hashes. Include actual workload/observer inputs and selected-loss
   controls in full native-to-score replay.
3. Complete independent/public native package verification; caller-supplied
   verification booleans are insufficient.
4. Implement and qualify the remaining harness adapters, finish Cursor and
   DeepSeek, then obtain three valid evaluated repetitions per ranked row.
5. Review privacy, arithmetic, citations and generated release assets against
   the full target scope before publication.

The owner authorized read-only AS parser/fixture access for the remaining
harnesses and directed collection using existing subscriptions and roughly USD
5 of Kimi credit. No top-ups or additional spending are authorized. These are
no longer pending approval questions. Existing DSH credit was verified before
starting bounded synthetic captures; live progress is recorded separately from
the completed preparation pass below.

## Review limits

Validation: `python3 -m pytest -q` passed **933 tests** in 24.02 seconds.
`compileall` and `git diff --check` passed. Regenerating the v0.4 leaderboard
produced byte-identical output. The historical README was restored after its
preservation-receipt test rejected an added plan link; no receipt was rewritten.

Luna and Sol performed bounded implementation and adversarial source review.
Oracle Sol Pro browser attempts `bench-expanded-v1-pro` and
`bench-expanded-v1-pro-retry` both failed at model selection with
`promptSubmitted=false` and no verified model selection. There is no Oracle
verdict. The prompt and exact source packet were retained for retry after the
browser integration is working.

No live benchmark submissions, commits, pushes, release tags or publication
were performed during this preparation pass. Existing untracked
`codex-quota-forensics/` and `docs/reviews/` were preserved.
