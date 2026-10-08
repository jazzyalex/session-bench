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

## Duplicate safety and density, 2026-10-05

This section replaces the duplicate-safety statement of the section above. For Claude
Code CLI the read is the transcript JSONL of the session. For Claude Desktop the read
is the transcript JSONL and the Desktop metadata JSON: the family check and the root
evidence take the session identity from the metadata, so it is in the read. The count
is made on raw records, not on decoded events.

- A prompt is written twice: a `queue-operation` record with `operation` =
  `enqueue` holds the full prompt text, and the `user` record after it holds the
  same text. No field marks one as superseded. So no prompt passes.
- An assistant text block and a `tool_result` block are each written once. A
  compound shell call is one call record.
- An Edit call is written twice: the `tool_use` block, and the `toolUseResult`
  of its result record, which holds all four arguments (`filePath`, `oldString`,
  `newString`, `replaceAll`) without the tool name. The result record of a Read
  holds only the file path, which names the target; it does not restate the
  call. A Bash result holds no command.
- `last-prompt` holds only the first 200 characters of a prompt. `ai-title` and
  `custom-title` hold other text. Attachments hold context. None of them states
  an event.
- Result: 13 of 19 in each CLI run and 6 of 10 in each Desktop run (the Desktop
  runs hold no Edit call).
- Density: the first record that states an event keeps its role. The `enqueue`
  record is the first record with the prompt, so it is `user_message`. The `user`
  record that repeats it is a `snapshot`.
- The Desktop metadata JSON is in the read. It holds no prompt, no response text,
  no call argument and no result output of these runs (checked by content
  comparison), so it adds no occurrence. It is one `metadata` record of density.

## Vendor instruction text in the public CLI packet, 2026-10-06

`scripts/sanitize_claude_score_packets.py` now blanks the context records of the
transcript at equal byte length, with the marker
`[vendor instruction text removed at equal byte length]`. The rule is the rule of
the Desktop row for the same record kinds: every string of an `attachment`
record except its `type` (the prompt snapshot with `systemPrompt[]`, tool
descriptions and schema descriptions; output style, reminders, model, date,
environment, session context) and its `rendered` text. One value is kept that
the Desktop rule blanks: `snapshot.platform` of an `environment` attachment. The
public inputs builder reads the operating system of the capture from it. Keys,
numbers, prompts, responses, tool calls and results stay. No metric reads an
attachment.

## Claude Desktop row, 2026-10-04

The Claude Desktop row uses the three retained runs `claude-desktop-eval-1-correction-1`,
`claude-desktop-eval-2-correction-1` and `claude-desktop-eval-3` (CLI 2.1.270). Runs 1 and 2
are corrective attempts. Their originals were invalid captures: the controller did not keep
the per-run filesystem observer. The originals gave no score, so no scored run was replaced.
The report discloses this.

Two metrics were unresolved until this date.

`broad.event_timestamps`. The timestamp join had two rules that were stricter than the
comparator. It needed the exact response text, and it needed an argv on every action. The
Desktop observer sees only the response canary, and an edit action has no argv. The join now
accepts a response whose text ends with the observed canary, and an edit action that has a
tool name, a target file and a turn. The denominator is still every required observer event.
Result: 11 of 13. Each credited event has the timestamp of its own native line. The two
events without a native witness are the observer's placeholder edit result and the file
change (the decoder emits no file-change event).

`broad.stable_root_location`. The retained inventories did not record the source paths of
the two selected files. `session_bench/claude_desktop_root_evidence.py` returns a row only
when all of these hold for each run:

- the metadata-only before and after inventories of the two normal roots show exactly two
  new files, with no personal-history content read;
- the transcript holds one session id, and the Desktop metadata names the same session;
- the Desktop `cwd` equals `originCwd` and the first transcript `cwd`;
- every native timestamp lies inside that run's inventory window;
- the three inventory windows do not overlap.

The locator is a derivation rule over native fields, not an observed path. Later private
Desktop captures (CLI 2.1.286) that did record the paths match the rule. This evidence is
weaker than the observed-path receipts of the Codex CLI and Claude CLI rows.

Compound shell calls (after the independent review of 2026-10-04). In each run the model
made two shell calls. Call 1 runs the inspect and the baseline helper, each followed by
`echo "exit=$?"`. Call 2 writes `checkout.py` with a quoted heredoc and then runs the final
helper. There is no `Edit` tool call. An earlier decoder invented an `Edit` action and a
result with a fixed text for call 2. That text is not in the native bytes. The decoder and
the projection no longer create it.

They now use the compound shell call rule of `rubric.md` (Matching table). The same code
path serves the Claude CLI row; its 31 metric rows did not change.

- Each helper segment is one action. Its result is its own output line, with the exit code
  from the `exit=N` line that follows it.
- The heredoc write is one edit action on `checkout.py`. The observer's `Edit` action
  matches it by action kind, target and turn. It has no result of its own, so the
  observer's placeholder edit result stays unmatched.
- `work.changed_files`: the pre-image is the file source in the native inspect result. The
  post-image is the heredoc body. The `bashEditDiff` of the same native result must turn the
  first into the second. Both hashes come from native bytes and equal the observer pair.
- `broad.readable_rationale`: the rationale and the timestamp evidence share one response
  text rule, so a canary-only observer joins both native responses.

Result in all three runs: `work.actions` 4/4, `work.results` 3/4, `causal.action_result`
3/4, `work.changed_files` 1/1, `broad.readable_rationale` 2/2, `broad.event_timestamps`
11/13. 28 metrics are measured and 3 are `native_absent` (`attribution.reconciliation`,
`broad.declared_format_version`, `broad.honest_version_signal`). Per-run score 86.8.

Limits of the rule. A lone shell write (one scored segment) stays a plain shell call. An
edit by a script or an in-place editor is not a redirection write and gets no edit action.
A helper segment that is piped or wrapped is not split.

Public packets. A Desktop transcript embeds the operator's instruction files, skill list,
connector instructions, hook output, account identifiers and prompt snapshot. The Desktop
metadata embeds the connector configuration. `scripts/sanitize_claude_desktop_score_packets.py`
blanks those strings at equal byte length. It replaces the keys of the connector
configuration (tool toggles, tool objects, tool input schemas) with distinct aliases of
equal byte length, and it gives the remote-control (bridge) session identifier one alias in
every file. Every record stays and every JSON document keeps its shape. Vendor field names
stay readable, including the schemas of the vendor built-in tools in the transcript prompt
snapshot. The 31 metric rows are equal before and after.
