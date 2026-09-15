# Session-Bench survival-v1 attempt ledger

Status: bounded calibration ran on 2026-09-11. OpenCode then completed one isolated
two-turn calibration and three evaluated repetitions with the explicitly pinned
`opencode/muse-spark-1.3-contributor-free` model. Codex-account quota is rechecked before
every Codex-account model submission against the owner-set 95% stop line. Reset
credits are never consumed without explicit owner confirmation. This ledger fixes the
campaign slots before calibration and prevents a failed or favorable extra run from
silently replacing a scheduled repetition.

Each attempt record must add the exact surface build, model/configuration, OS, protocol
and workload digests, run and capture IDs, start/end times, quota baseline, stop threshold,
observer method, declared native roots, outcome, and evidence receipt. `scheduled` means
there is no result. It is never a pass, failure, or zero.

| Attempt ID | Configuration | Kind | Repetition | State |
|---|---|---|---:|---|
| `codex-cli-cal-1` | Codex CLI | calibration | — | invalid: resume sandbox flag misplaced |
| `codex-desktop-cal-1` | Codex Desktop | discovery calibration | — | blocked: host denies direct Computer Use observation |
| `cursor-cli-cal-1` | Cursor CLI | calibration | — | invalid: Free plan rejected named model |
| `cursor-desktop-cal-1` | Cursor Desktop | discovery calibration | — | invalid: controller IPC path too long |
| `opencode-cli-cal-1` | OpenCode CLI | calibration | — | invalid: controller used wrong DB filename |
| `codex-cli-eval-1` | Codex CLI | evaluated | 1 | scheduled |
| `codex-cli-eval-2` | Codex CLI | evaluated | 2 | scheduled |
| `codex-cli-eval-3` | Codex CLI | evaluated | 3 | scheduled |
| `codex-desktop-eval-1` | Codex Desktop | evaluated | 1 | scheduled |
| `codex-desktop-eval-2` | Codex Desktop | evaluated | 2 | scheduled |
| `codex-desktop-eval-3` | Codex Desktop | evaluated | 3 | scheduled |
| `cursor-cli-eval-1` | Cursor CLI | evaluated | 1 | captured, unscored; native transcript decoded but score gate open |
| `cursor-cli-eval-2` | Cursor CLI | evaluated | 2 | invalid, unscored; stopped after R1 on user steering |
| `cursor-cli-eval-3` | Cursor CLI | evaluated | 3 | scheduled |
| `cursor-desktop-eval-1` | Cursor Desktop | evaluated | 1 | captured, unscored; two-turn copied transcript plus selected SQLite companion decoded, full-root closure open |
| `cursor-desktop-eval-2` | Cursor Desktop | evaluated | 2 | invalid/N/A; Cursor Free usage limit before completed R1; no R2 submitted |
| `cursor-desktop-eval-3` | Cursor Desktop | evaluated | 3 | scheduled, Cursor research paused |
| `opencode-cli-eval-1` | OpenCode CLI | evaluated | 1 | local 81.0 survival record; immutable recheck provenance blocked |
| `opencode-cli-eval-2` | OpenCode CLI | evaluated | 2 | local 81.0 survival record; immutable recheck provenance blocked |
| `opencode-cli-eval-3` | OpenCode CLI | evaluated | 3 | local 81.0 survival record; immutable recheck provenance blocked |
| `codex-cli-crash-1` | Codex CLI | crash qualification | — | scheduled |
| `codex-desktop-crash-1` | Codex Desktop | crash qualification | — | scheduled |
| `cursor-cli-crash-1` | Cursor CLI | crash qualification | — | scheduled |
| `cursor-desktop-crash-1` | Cursor Desktop | crash qualification | — | scheduled |
| `opencode-cli-crash-1` | OpenCode CLI | crash qualification | — | scheduled |

### Current prospective Codex CLI calibration attempts

These are fresh isolated `CODEX_HOME` attempts under the current v1 cohort. They are
not evaluated repetitions and none is score eligible.

| Attempt ID | Model | Submitted turns | State | Evidence-based reason |
|---|---|---:|---|---|
| `codex-cli-cal-2026-09-14-1` | `gpt-5.6-luna` | 1 | invalid/N/A | The controller passed `-C` to `codex exec resume`; this CLI subcommand rejects it before R2 output. |
| `codex-cli-cal-2026-09-14-2` | `gpt-5.6-luna` then default | 2 | invalid/N/A | Resume selected the account default model instead of the recorded Luna model. The controller now pins the model on resume. |
| `codex-cli-cal-2026-09-14-3` | `gpt-5.6-luna` | 2 | invalid/N/A | Both response canaries and native continuation were present, but the required edit and final helper outcome were absent. This is a workload failure, not a session-format score. |
| `codex-cli-cal-2026-09-14-4` | `gpt-5.6-luna` | 2 | invalid/N/A | The same complete R2 workload condition failed again: inspect/baseline were present, while the required edit and final helper were absent. The lane stops pending changed conditions. |
| `codex-desktop-manual-01` | Codex Desktop task | 2 | invalid/N/A | Both native response canaries and the three workspace helper phases were retained, but the manually pasted R1/R2 prompt texts differed from the frozen workload. It is a direct-task format probe only; see its [attempt record](../../artifacts/survival-v1-runs/codex-desktop-manual-01/attempt.json). |
| `codex-desktop-manual-03` | Codex Desktop task | 2 | captured, unscored | Fresh task-API R1/R2 prompts matched the instantiated workload; native decode, synthetic final helper, copied shell snapshot, and selected-R2 loss control succeeded. The helper ledger retained the static fixture canary, so this is a workload-binding discovery only and cannot qualify or score. [Attempt record](../../artifacts/survival-v1-runs/codex-desktop-manual-03/attempt.json). |
| `codex-desktop-manual-04` | Codex Desktop task | 2 | captured, unscored | Fresh task-API R1/R2 prompts used the repaired explicit per-attempt helper-canary flag. The copied rollout, shell snapshot, ledger, and selected-R2 loss control all succeeded. The complete native root and canonical-copy proof remain open, so it cannot qualify or score. [Attempt record](../../artifacts/survival-v1-runs/codex-desktop-manual-04/attempt.json). |

One setup-correction slot per configuration may be appended only after its scheduled
calibration attempt records a concrete setup defect. It must use `<configuration>-setup-2`,
link to the failed calibration attempt, and preserve both records. The hard campaign cap
is 30 vendor sessions: the 25 rows above plus at most five setup corrections.

| Setup correction | Corrects | State | Evidence-based reason |
|---|---|---|---|
| `codex-cli-setup-2` | `codex-cli-cal-1` | invalid | Both turns and native rollout were captured, but an operator post-check appended a fourth protected-ledger event. At the time of that attempt the decoder also left most 0.154 records unknown; the later decoder work does not make the mutated attempt valid. |
| `codex-desktop-setup-2` | `codex-desktop-cal-1` | complete calibration, unscored | The supported app task API independently recorded both response boundaries; one exact new Desktop rollout and its shell snapshot were copied and decoded offline. Removing the R2 final-answer record prevented reconstruction of that fact; the state remains unresolved because complete-root proof is open. Complete-root and canonical-equality proof remain open before evaluated runs. |
| `cursor-cli-setup-2` | `cursor-cli-cal-1` | captured, unscored | Both canaries, one native session, and the three-event helper ledger were captured; a current native decoder now exists, but this calibration is not a score. |
| `cursor-desktop-setup-2` | `cursor-desktop-cal-1` | blocked before model · N/A | A short-root isolated app was proved and Computer Use was bound to its window; response observation was not exercised before authentication failed. The clean profile remained logged out and the WorkOS sign-in page timed out in Safari and Chrome before any prompt or native session. |
| `opencode-cli-setup-2` | `opencode-cli-cal-1` | complete | The corrected isolated database path, exact Muse model pin, two-turn continuation, helper ledger, copied DB/WAL/SHM family, native decoder, and damage control all passed. |

The three OpenCode evidence packages recorded 81.0/100 under the then-frozen Session
Survival comparator. The stored decoded representations remain compatible with the current
decoder, but strict package rechecks are blocked because the exact decoder source pinned by
the evidence was not retained. This is one local survival-lens record, not the 31-metric
product score or a cross-product leaderboard row.

Cursor discovery work that invokes a model or materializes a session consumes its
calibration row. Static help, bundle, executable, and isolated-auth inspection consumes
no row. The current authority, quota contract, and exact build identities are frozen in
`plans/survival-v1/campaign.json`; the previous prototype ceiling does not carry forward.

The later Cursor Desktop preflights consumed no model session. The long profile path in
`cursor-desktop-cal-2` exceeded Electron's IPC socket limit; `cal-3` proved a short-root
launch but did not bind the observer; and `cal-4` bound Computer Use to the isolated
window. The clean profile then failed authentication before prompt submission because
the WorkOS page timed out in both Safari and Chrome. These are retained as controller or
authentication failures and are `N/A`, never evidence of poor session persistence.

| Later Cursor Desktop attempt | Vendor sessions | State | Evidence-based reason |
|---|---:|---|---|
| `cursor-desktop-cal-5` | 1 | invalid · unscored | Both model turns completed, but an operator post-check invoked the protected helper and appended a fourth ledger event. The exact fresh native transcript is retained for format discovery only. |
| `cursor-desktop-cal-6` | 1 | captured · unscored | A fresh `/tmp` fixture and the signed-in isolated app produced two visible response canaries, one continued chat, three helper events (`inspect`, `baseline`, `final`), the intended edit, and one fresh 12-record native transcript. Selected session-keyed SQLite bubbles preserved nine tool results and decoded with the transcript under original-root/network denial. Five additional session-bearing rows remain unqualified. |
| `cursor-desktop-eval-1` | 1 | captured · unscored | Fresh two-turn evaluated task retained both response canaries, one continued chat, the required edit, protected helper outcomes, and a copied decode with eight actions, eight results, and ten relations. Denied-original-root replay matched the ordinary digest. Six extra `agentKv`/`inlineDiff` session-bearing rows are inventoried but not yet qualified, so the full root and 31-metric score gates remain open. |
| `cursor-desktop-eval-2` | 1 | invalid/N/A | The exact R1 prompt was submitted once; the UI returned “You've hit your usage limit” before a completed R1 response. No R1 response canary, helper event, or R2 submission occurred. [Attempt record](../../artifacts/survival-v1-runs/cursor-desktop-eval-2/attempt.json). This is a Cursor Free runtime stop, not measured session-format loss. |

These four model sessions count against the campaign's 30-session ceiling. The earlier
Cursor Desktop calibration and setup rows consumed no model sessions. `eval-3` remains
scheduled, with all Cursor research paused at the owner's request. One completed
evaluated run cannot support a three-repetition score or ranking.
