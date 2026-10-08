# Session Bench v1 release candidate

Fourteen configurations: all ten v0.4 harnesses, Codex Desktop, Claude Desktop, Cursor Desktop, and DeepSeek Harness CLI (`dsh`).

Scores measure recovery of this synthetic two-turn workload from retained session files. They are not coding-quality, price, speed, or vendor reliability scores. A Desktop surface is a separate row unless its session format is the same as its CLI; then it is shown as sharing that row.

An overall score and rank require three complete, independently reviewed 31-metric replays.
A provisional row shows two numbers: the points its resolved metrics earn with every unresolved metric counted as zero, and the best total it could reach if every unresolved metric earned full credit. Provisional rows are not ranked: their private inputs are not public-safe or independently reproduced. Capture-only and blocked entries show their actual milestone or blocker without inventing metric values.

The ranked cohort has 12 configurations with three captures each and executable replays for each sanitized packet. Reviews use a separate agent session on the same host and account; retained capture provenance is not a fresh independent acquisition.

## Ranking

| Rank | Configuration | Score | Runs |
|---:|---|---:|---:|
| 1 | DeepSeek Harness CLI | 96.9 | 3 |
| 2 | Pi | 96.4 | 3 |
| 3 | Copilot CLI | 96.0 | 3 |
| 4 | OpenCode CLI | 94.4 | 3 |
| 5 | Kimi Code | 91.2 | 3 |
| 6 | Claude Code CLI | 89.3 | 3 |
| 7 | OpenClaw | 88.1 | 3 |
| 8 | Codex CLI | 87.5 | 3 |
| 9 | Hermes | 86.4 | 3 |
| 10 | Claude Desktop Code (Local) | 85.6 | 3 |
| 11 | Antigravity | 82.7 | 3 |
| 12 | Cursor CLI | 78.1 | 3 |

## Notes on waived and corrected rules

Each waiver below is an owner decision made after the results were known.

- Replaced attempts. The rubric says: do not replace a scheduled run with a calibration or corrective attempt. This rule is waived for v1 for OpenClaw (two attempts replaced: one controller fault, one turn without a visible response), Claude Desktop (two corrective runs after invalid captures) and Kimi (five attempts stopped by a controller setting, the provider rate limit, or a reply without the required marker). No replaced attempt had a score. Each one is listed with its reason in the adapter document of the row.
- OpenCode native record. The first scoring read the `event` table, in which every part has one to five update rows; duplicate safety is then zero. On 2026-10-05 a read without that table was tried and gave 97.4. It was withdrawn on 2026-10-08, because the rubric fixes the read before scoring. The score shown uses the first read.
- OpenClaw reconciliation. The store holds the run token total twice and no usage per request. An earlier candidate gave 3 points for reconciliation; they are removed.
- Codex packets keep the address of this repository, which names the operator's GitHub account, by owner choice. The home name, home paths, e-mail, account ids and tokens are aliased, blanked or zeroed in every packet; the limits below list what stays readable.

## Known limits

- Rules changed after captures. The tiered evidence rule (2026-10-03), the single duplicate rule (2026-10-05) and the privacy rules were written after the first captures and after some first scores. The ranking is a careful comparison, not a pre-registered experiment.
- Duplicate safety depends on the native record that the decoder reads. A store that the decoder must open for one fact brings all its repeats into the count. Rows whose facts sit in one lean file do better than rows that spread facts over several stores.
- Reconciliation. The comparator accepts the decoder's statement that per-response usage records sum to the declared total; it does not add them up itself.
- Response text. A native response matches the observed one by turn, role and response marker. The full response text is not compared.
- Usage attribution. A usage record can be joined to its response by turn when the format gives no response id.
- Action arguments. The comparator matches an observed action to a native one by id, turn, kind, path and shell command line. It does not compare the text of an edit. Kimi has its own check of the edit text; the other rows have none.
- Rules written for these packets. The Kimi edit-text check and the Copilot rule that finds a message text inside a store cell were written for these packets. The Copilot rule has no minimum text length; the shortest message here has more than 200 characters.
- Reviews. Each public packet set was checked by a separate agent session on the same host and account, not by a second operator. Three outside model reviews were run, of candidates v38, v39 and v40. Each returned NO-SHIP with findings. Their findings are answered in the notes above and in these limits. A fourth review, of candidate v41, found no defect that makes a score, a rank or a stated claim wrong; it was a static reading, without replays. This release differs from candidate v41 only in this sentence and in the replay command of REPRODUCE.md.
- Machine details that stay readable. Some packets keep device and inode numbers, file counts and capture times of the capture host in their inventories and receipts (in Codex also a `device:inode` string and three file counts of the operator's Codex folder that the replay reads). The values are stable for the capture host, so a reader can tell that packets came from the same machine and folder. They hold no account id, name or content.
- Vendor instruction text (system prompts, tool descriptions) is blanked at equal length in the public packets, with these exceptions: Pi keeps four short tool descriptions; Kimi keeps the first sentence of its system prompt, from which the harness name is read; Copilot keeps the operating-system line of its system prompt; Claude Code keeps the platform field of one environment record.
- Privacy gate. Every packet passes an automatic check of digests and hex values from 12 characters, and of shorter values under digest-named keys. A short hex value of 6 to 11 characters under an ordinary key is not checked. The check is a filter, not a proof that no private value remains.
- What 'verified' means here. A verified row has three replays that recompute all 31 metric states from the public packet, and an approving review by a separate agent session. It does not mean that every judgment in the adapter document is beyond dispute; each adapter document lists its judgment calls and their point effect.

## All configurations

| Configuration | Evidence | Sample size | Current coverage | Overall score | Rank | Remaining work | Evidence file |
|---|---|---:|---|---:|---:|---|---|
| Pi | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 96.4 | 2 |  | [Coverage](coverage/pi.json) |
| OpenClaw | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 88.1 | 7 |  | [Coverage](coverage/openclaw.json) |
| Claude Code CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 89.3 | 6 |  | [Coverage](coverage/claude-cli.json) |
| Codex CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 87.5 | 8 |  | [Coverage](coverage/codex-cli.json) |
| Kimi Code | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 91.2 | 5 |  | [Coverage](coverage/kimi.json) |
| OpenCode CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 94.4 | 4 |  | [Coverage](coverage/opencode-cli.json) |
| Hermes | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 86.4 | 9 |  | [Coverage](coverage/hermes.json) |
| Copilot CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 96.0 | 3 |  | [Coverage](coverage/copilot.json) |
| Antigravity | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 82.7 | 11 |  | [Coverage](coverage/antigravity.json) |
| Cursor CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 78.1 | 12 |  | [Coverage](coverage/cursor-cli.json) |
| Codex Desktop | Shared session format | — | Same session format as Codex CLI: the retained Desktop and CLI rollouts share all record kinds and one decoder, and a CLI thread has rows in the same state and history stores. Scored on the Codex CLI row. | See shared row | Not ranked | same session format as codex cli, scored on the codex cli row | [Coverage](coverage/codex-desktop.json) |
| Claude Desktop Code (Local) | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 85.6 | 10 |  | [Coverage](coverage/claude-desktop.json) |
| Cursor Desktop | Capture qualification incomplete | — | An isolated signed-in Desktop profile and a capture bracket exist. Two attempts on 2026-10-06 gave no valid run: one prompt went to the wrong window, and the Free-plan usage limit stopped the other. Earlier runs in the normal profile stay private and unqualified. The row resumes when the plan limit resets. (0/3 qualified repetitions) | Not scored | Not ranked | eval 2 cursor free quota stopped before r1 completion, eval 3 two turn capture private and native root unqualified, eval 4 r1 r2 two turn run completed private, eval 4 exact global rows captured but other native families unqualified, native family qualification incomplete, three score qualified repetitions incomplete, eval4 private decoder replay completed but unscored | [Coverage](coverage/cursor-desktop.json) |
| DeepSeek Harness CLI | Verified score | 3 | 3/3 verified runs; 31/31 metric states in each | 96.9 | 1 |  | [Coverage](coverage/deepseek-harness-cli.json) |

Detailed scored-run records, categories, sensitivity rankings, exact build/model/date identities, and public packet hashes in [scorecard.json](scorecard.json) cover the 12 verified configurations. Partial coverage records contain state counts and source pins; private diagnostic details are withheld.

Limited-cohort score publication gate: **met**. 12 configurations with three replayed and reviewed runs; 14/14 surfaces have attempt or capture evidence.

All fourteen configurations are represented in this coverage view; full fourteen-configuration scoring remains **incomplete**. This full-scope candidate is **past the automatic release checks** while partial diagnostic inputs await public-safety review, independent reproduction, and replay. The separate limited-cohort score gate is reported above.

Evidence is frozen by SHA-256. Verify reviews using independently obtained pins before trusting the replay index. Each complete-score packet includes its scoring source and standalone replay script. Platform dependencies, where required, are declared in the packet manifest.
