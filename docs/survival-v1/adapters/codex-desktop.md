# Codex Desktop adapter status

Status: **two-turn calibration complete, unscored** on 2026-09-12.

The installed surface is `/Applications/ChatGPT.app`, bundle `com.openai.codex`, version
`26.908.40834` build `8881`, with bundled `codex-cli 0.154.0-alpha.6.2`. These identifiers establish
the product under test. They do not establish its complete record boundary.

The host still denies direct Computer Use access to `com.openai.codex`. The supported Codex
app task API supplied a separate observer channel for submitted turns, response boundaries,
visible response text, commands, and file changes. It did not read the declared native
rollout or shell-snapshot files. The observer must be described as task-API observation;
it is not manual GUI observation.

The retained calibration completed both turns with exact canaries, changed only the declared
fixture file, passed the final helper, copied exactly one newly created rollout plus its
thread-keyed shell snapshot, decoded the copy as `codex-desktop`, and detected loss after
the R2 final-answer record was removed. It receives no score because it is one calibration.

Evaluated collection stays blocked until the remaining portability conditions are proved:

1. the complete Desktop and bundled-runtime artifact roots;
2. a canonical copied representation for that complete root.

The staged copied-bundle decoder already passed with the original package and network
denied. That proof applies to the retained filtered copy; it does not establish that the
copy contains every Desktop persistence artifact.

The raw rollout and shell snapshot remain withheld. A filtered, path-sanitized derivative
also remains withheld pending privacy review. No personal Desktop history was opened.

## Direct-task format probe, 2026-09-14

`codex-desktop-manual-01` is retained as an **invalid/N/A harness probe**, not a
calibration result. A newly created Desktop task in the staged synthetic workspace
completed the two requested turns, both response canaries, the intended edit, and the
three helper phases. Its copied private rollout also established a current direct-task
shape: Desktop stores `response_item` messages without a turn ID, while the enclosing
`task_started`, `UserMessage`, and `task_complete` event envelopes retain the native turn
ID. The decoder now has a bounded, tested task-window join for that shape and normalizes
Desktop's lossless Markdown underscore escaping in submitted canaries.

The prompts pasted into this probe were not byte-identical to the frozen workload, so the
turns cannot be matched to the declared population. The attempt is therefore invalid and
unscored even though the workspace outcome was successful. A replacement manual run must
paste the two exact instantiated strings from the frozen workload, with no paraphrasing.

## Fresh task-API capture, 2026-09-14

`codex-desktop-manual-03` is the replacement fresh synthetic task, created and continued
through the Codex app task API in the repository workspace. Its two submitted turns match
the instantiated R1/R2 workload, both response canaries are present in the copied rollout,
the model made the permitted `checkout.py` correction, and the final helper passed. The
private copy also includes the exact session-keyed shell snapshot and a copied helper ledger;
offline decoding reconstructs the two turns, responses, four action-result links, revision
order, and final-after-R2 relation. Removing the selected R2 response record from a derived
copy prevents that response from being reconstructed.

It remains **captured and unscored**, rather than a calibration qualification. The helper
commands in workload 1.0 do not carry the instantiated per-run canary, so the ledger records
the static fixture canary. That is a workload binding defect to repair before freeze, not an
observed product loss. The Desktop complete-root and canonical-copy contracts are also still
open. See the [attempt record](../../../artifacts/survival-v1-runs/codex-desktop-manual-03/attempt.json).

## Corrected fresh calibration, 2026-09-14

`codex-desktop-manual-04` repeats the synthetic two-turn task after the pre-freeze workload
repair. The R1 and R2 helper commands each pass the exact unique run canary, and all three
copied ledger rows bind to that canary. The copied native rollout reconstructs the exact
inputs, both visible responses, four action-result links, revision order, and final helper;
the selected-loss control removes one R2 visible-response record and leaves R2 unreconstructed.
It remains unscored while the complete Desktop root and canonical copied representation are
unproved. See the [attempt record](../../../artifacts/survival-v1-runs/codex-desktop-manual-04/attempt.json).

The same attempt has a path-sanitized public derivative with a raw-to-redacted receipt and a
clean scan for absolute-home paths and credential markers. Its local offline decode equals the
private measurement. This verifies redaction fidelity for the declared filtered package only;
it does not turn the filtered package into a complete-root claim.

## Broad-format wrapper

`session_bench.codex_format_evidence.build_codex_format_evidence` now produces the
12 broad-format evidence rows from an already-decoded, copied Codex package and explicit
immutable observer/manifest identities. It can resolve only bundle-local claims such as
JSONL readability, documented decoding semantics, declared bundle format, timestamps,
and classified logical density. It leaves self-contained identity, stable root, honest
version signal, schema stability, and duplicate safety unresolved until their separate
contracts exist. The wrapper is evidence plumbing, not a score or a public claim.
