# Claude Code adapter status

Status: **preflight only, unscored**. This note records decoder and
calibration boundaries only. It authorizes no evaluated repetition and
supplies no score.

## Distinct configurations, shared decoder only

`claude-cli` (Claude Code CLI) and `claude-desktop` (Claude Desktop Code
tab, Local) are distinct configurations with separate calibration slots,
capture adapters, root manifests, observers, and report rows.

They may share only the declared copied JSONL decoder,
`claude-code-jsonl-v1`, for the declared project-keyed transcript file.
A shared parser must not merge the two score rows. Desktop Chat and
Cowork are outside this Code (Local) target.

Sources: `plans/survival-v1/claude-expansion.md`,
`docs/survival-v1/shared-format-assessment.md`.

## Calibration state

- CLI: `claude-cli-cal-1` and `claude-cli-cal-2` are both invalid/N/A
  calibrations. Each refused the synthetic workload before tool use,
  file edit, helper event, or canary. There is no successful isolated
  two-turn CLI workload capture yet.
- Desktop: `claude-desktop-cal-2` completed R1 and R2 and resolves the
  workload-isolation and visible-continuation calibration gate. It does
  not resolve the native evidence gate: one newly created project-keyed
  transcript copied offline and decoded two turns, two responses, four
  actions, and four results; a rehashed copied package with R2 removed
  lost the selected response. That single-file control does not prove the
  complete cross-root companion family. The result is unscored.
  `claude-desktop-cal-1` remains an invalid controller-isolation
  calibration.

Neither surface has an evaluated score.

## Still required before evaluated collection

- Complete cross-root companion-family proof for both surfaces.
- Independent GUI evidence for Desktop.
- A complete copied root and canonical copied representation.
- Bind the existing 12-row broad-format wrapper to each accepted copied package, then
  combine it with that surface's 19-row Survival evidence.
- A successful isolated two-turn CLI calibration, followed by
  companion-family closure, offline decode of the declared family, and
  a loss control that removes a selected native fact.

No normal-profile discovery is permitted to satisfy these gates.

## Decoder package boundary

Implementation: `session_bench/adapters/claude_code_decoder.py`.
Tests: `tests/test_claude_code_decoder.py`.

The decoder accepts one explicitly declared copied package only. It
never searches the normal CLI or Desktop profile locations and cannot
silently attach companions.

A valid package is an ordinary non-symlink directory containing exactly:

- `decode.json` with `format == "claude-code-jsonl-v1"`.
- `session.jsonl`, declared as exactly one session artifact with
  matching `sha256` and `size_bytes`.

The decoder expands tool blocks so one JSONL message cannot collapse
benchmark populations: string user content counts as a submitted turn,
assistant text counts as a response, `tool_use` blocks count as
actions, and `tool_result` blocks count as results, with `tool_use_id`
linkage preserved.

## Rejection behavior

The decoder fails closed with `ClaudeCodeDecoderError` on:

- Missing `decode.json` or `session.jsonl`, symlink package or member,
  or any undeclared file in the copied package.
- Unsupported format, extra manifest keys, or a session artifact
  declaration that is not exactly one entry with the expected fields.
- Digest or size mismatch for the copied session bytes.
- Duplicate JSON keys, invalid JSON, or a record whose message role or
  session ID is invalid.
- Anything other than exactly one session ID in the copied session,
  including second-session companion rows.

No private session or credential store was opened for this assessment.

## Broad-format wrapper

`session_bench.claude_format_evidence.build_claude_format_evidence` converts only an
already-decoded declared Claude JSONL package plus explicit immutable observer and manifest
identities into the shared 12-row format-evidence schema. It measures readable responses,
thread ordering, JSONL readability, documented decoding semantics, timestamps, and classified
logical content. It deliberately leaves root closure, self-contained harness identity,
machine-readable format version, version honesty, observed stability, and duplicate safety
unresolved until their independent contracts exist. The wrapper is tested with a synthetic
copied bundle and is not a score or an accepted live calibration.
