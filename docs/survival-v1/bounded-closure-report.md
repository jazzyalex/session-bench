# Session-Bench v1 bounded closure report

Status: **unpublished local review candidate**. Scope froze on 2026-09-15.

No new captures, desktop exploration, repetitions, crash qualification, or
surface implementation are scheduled. The candidate uses the existing 15
evaluated runs: three each for Codex CLI, Codex Desktop, Claude Code CLI,
Claude Desktop Code (Local), and OpenCode CLI.

## Candidate

The generated candidate is in `artifacts/survival-v1-review-candidate`.

- Codex CLI and Codex Desktop: tied #1 at 87.0
- OpenCode CLI: #3 at 83.3
- Claude Desktop: #4 at 82.8
- Claude CLI: #5 at 81.0
- ties use competition ranking at the displayed one-decimal precision
- 31 metrics resolved in each of 15 runs
- report SHA-256: `48cabe0d3c8348f52284fb60c57d5510870dd2540cf788abb7df9bbc28f6d033`
- artifact-manifest SHA-256: `bcc780611ab76e7d49f204b76ef6105fbe10295480f7ab7d98b495e292a15e62`

Generated review-candidate recommendations select Claude CLI for the **best
preserved work trail under this task** (`audit_ready`), Codex CLI and Codex
Desktop as the portable-archive qualified set, and the Codex CLI/Desktop pair
for cross-surface consistency. The `audit_ready` rule uses Fidelity and
Causality only; Usage is outside that rule, so Claude CLI can qualify with
Usage 0/15. Usage accounting, lean complete records, and long-term archives
emit `no_recommendation`.

## Verification

- `python3 -m pytest -q`: 695 passed
- repeated generation with the same explicit timestamp: byte-identical output
- artifact manifest: every listed size and SHA-256 verified
- candidate privacy scan: zero local-path, email, or credential-pattern findings
- visual inspection: five rows are legible and the published-precision tie is
  rendered as `#1, #1, #3, #4, #5`

Muse was used on a sanitized staging copy for the bounded packaging task. The
model was `opencode/muse-spark-1.3-contributor-free`, session
`ses_f5cbebd00ffeftpKnmYgB4f4MZ`, recorded cost 0. The OpenCode deny-first edit
rules rejected the staging path twice because `/tmp` normalized to
`/private/tmp`; the run was stopped and no Muse changes were accepted. The
requested final canary was therefore absent. Export verification confirmed the
exact model and zero recorded cost.

## Publication boundary

`review-candidate.json` sets `published=false` and `eligible=false`. Full
independent native reproduction is recorded as
`independent_native_reproduction_incomplete`. Local packet reproduction and an
independent recomputation of OpenCode's sanitized semantic/score packet do not
clear that gate.

Publication remains a separate future decision. It requires reopening the
release phase, clearing the independent-native gate, final review, and an
explicit owner release instruction.
