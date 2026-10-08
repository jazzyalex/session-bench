# DeepSeek Harness CLI adapter

Status: live two-turn captures and private diagnostics implemented; public
ranking and independent full-score reproduction are not yet qualified.

## Captured identity

The installed `dsh` is DeepSeek Harness `0.2.0-rc.2`. The shipped headless
profile selects `deepseek-official/deepseek-flash`. Each run starts with
`DSH_HOME=<fresh-run>/dsh-home`, a copied synthetic workspace and the workload's
run canary. Authentication is loaded in process for DeepSeek only; credentials
are not included in evidence packages. No top-up is performed.

The controller invokes `dsh --profile headless --json <task>`, then resumes R2
with the session ID observed on R1 stdout using `--session-id`. Before resuming,
it verifies the ID against the copied native header and checks that the R1
checkout and protected helper remain unchanged. The controller has a per-turn
timeout and a between-turn credit-consumption stop. This is not a hard provider
billing cap; unrelated concurrent usage can also affect the balance delta.

Installed-source references are `@deepseek-ai/dsh-headless/README.md`,
`@deepseek-ai/dsh-home-paths/lib/index.js` and
`@deepseek-ai/dsh-session-persistence-jsonl/README.md`. The authorized AS
references are `DeepSeekHarnessFormatTypes.swift`,
`DeepSeekHarnessSessionParser.swift` and `DeepSeekHarnessZstdFrameReader.swift`.
AS is a format reference, not benchmark measurement evidence.

## Native boundary and version

The retained 0.2.0-rc.2 homes also contain a same-session persisted checkpoint at
`storages/session_projcache/sessions/<session-id>.json`. The installed
`dsh-session-projection-cache` source declares domain version 7 and a complete
`record.identity` plus `record.rows` object. The cache is disposable derived data,
but its persisted bytes are included in the complete native scoring closure and
logical density denominator. The full object is counted once as metadata;
unknown projection-unit values are retained.

The supported `tokenUsage` checkpoint has unit version 2, an event-sequence
watermark, and `val.totals` plus `val.last`. The installed `dsh-token-meter`
projection maps `inputTokens` to `uncachedInputTokens` and folds settled model
steps into four named buckets. The reader checks native-header lifecycle,
watermark, all native settled-step totals and last-step identity. An unknown,
malformed or stale cache cannot supply aggregate credit. Reasoning is never
invented. Reconciliation is native-attested: the settled native step usage must
sum to the persisted totals in every bucket. The observer's sum of the two
final-response usage records is not compared, because it covers only two of the
model steps.

DSH stdout places final-step usage before the `completed` turn boundary and
`final` text. A stdout-only observer binds the uniquely delimited final step only
when it contains the exact displayed final text and no tools. Four observed
buckets can form a response sum; missing reasoning remains omitted. Provider
`totalTokens` and internal model-step sums are not substituted for this expectation.

The current generation is
`DSH_HOME/sessions/<project-key>/<session-id>/session.v4.jsonl.zstd` by default;
plain `session.v4.jsonl` is supported too. An empty `session.lock` is allowed as
a non-session-bearing advisory lock. Unexpected files, symlinks, ambiguous
session generations and wrong observer/native IDs are rejected.

The header declares type `session`, integer version `4`, session `id`,
`createdAt`, `isSeeded`, `delegationDepth` and optional cwd/parent metadata.
Subsequent records carry dense integer `seq`, millisecond `time`, string `type`
and object `data`. The reader retains every raw record and its source hash.
The 59-name v4 envelope vocabulary is inventoried; this does not imply that
all payload semantics are supported. Seeded/subagent captures, unknown required
or ignorable semantics, compaction, retries and unsupported rewrites keep the
semantic decoder unsupported.

The bounded Zstandard reader requires ordinary concatenated frames, a single
header record in the first frame, and complete JSONL boundaries. It rejects
truncated frames, invalid checksums, trailing garbage and excessive decoded
size/expansion. Runtime dependency: the host's `libzstd`; an independent replay
package must bind that dependency rather than claiming a Python-only closure.

## Semantic and observer rules

`dsh_live.py` decodes native bytes without observer text or filesystem hashes.
User input is restricted to native `source.kind=user`; runtime and skill context
remain retained records. Tool results join on native turn, step and call ID.
The final response follows DSH headless's documented last-nonempty committed
assistant text at the completed turn boundary. Other assistant text remains
classified as commentary. Missing response text is never restored from prompts.

Native helper results retain nonces and exit-display wrappers. Only literal
helper commands and recognized exit wrappers are normalized; other compound
shell commands remain unchanged. Whole-file hashes come from native records only: the
pre-edit source printed in the native inspect result, and that source with the
native edit call applied. A native hash command and its output are the fallback.
Controller before/after hashes are never imported as native facts.

The observer separately consumes submitted prompts, captured stdout, the
protected helper ledger and filesystem hashes. DSH stdout itself is a vendor
projection of committed session events, not instrumentation independent of that
producer. Truncated stdout is rejected. Missing reasoning-token values are not
invented as zero. The final helper must actually pass.

Broad evidence binds the physical native hash, native schema version, exact
observer bytes and the complete logical JSON record inventory. Logical-byte
accounting uses canonical compact sorted UTF-8 JSON, identical to other JSONL
adapters. Metadata/context records remain in the density denominator. Naive
reader duplicate safety counts both assistant tool-call blocks and their
corresponding tool/call records rather than silently deduplicating them.

Duplicate safety and density (rule of 2026-10-05). The read is the session file
`session.v4.jsonl` and the projection cache: reconciliation is scored from the
cache, so the cache is in the read. The count is made on raw records.

- A tool call is written twice: as a `tool-call` block of `assistant/message` and
  as a `tool/call` record with the same turn, step, call id, name and arguments.
- A prompt is written twice: an `agent/inbox/spliced` record holds the whole
  queued message (same id, same content) before its `user/message` record.
- The cache unit `titleInput` holds the exact text of the R1 prompt again.
- `session/title-llm-request` holds the R1 prompt inside a longer title request.
  That is another message, not the prompt; it is not counted.
- A `tool/result` record restates its call when it holds all text arguments of
  the call: the result of a successful `edit` holds the path, the old text and
  the new text in `meta.diffs`. It restates only the call it names (`callId`).
- No field marks a copy as superseded. So calls and prompts do not pass.
- The public packet holds no digest of the private compressed native file and no
  digest of a file of the private home, except the empty lock file.

Vendor instruction text in the public packet (owner decision 2026-10-06). The
sanitizer already obscures the system prompt and the runtime-context and
skill-catalog messages. It now also obscures, with the marker
`[vendor instruction text removed at equal byte length]`, every `description`
string of a tool definition in `request/header` (`data.header.tools[]`, schema
descriptions included) and the `system` string of `session/title-llm-request`.
Tool names, keys and canonical byte counts stay. The title request message
stays: it is the workload prompt inside one wrapper sentence. The projection
cache holds no prompt of the vendor.
- Density: the first record that states an event keeps its role. The inbox record
  is `user_message`; the `user/message` record, the `tool/call` records and the
  cache record are `snapshot`.
- The other choice was to never open the cache. Reconciliation would then be
  `native_absent` (3 points) for one occurrence less. The cache stays in the read.

## Commands and retained attempts

`python3 scripts/run_deepseek_survival.py --attempt-id <fresh-id>` captures one
bounded two-turn attempt. `python3 scripts/qualify_deepseek_capture.py
--attempt-id <id> --repetition <1|2|3>` creates a new private successor packet
with observer, decoded native records, 19 survival cells, 12 broad cells and
source-bound diagnostics. Existing packets are never overwritten.

`dsh-cal-20260929-1` completed R1 but the original plain-only capture reader
rejected the compressed boundary. It remains an invalid/unqualified attempt.
`dsh-cal-20260929-2`, `dsh-eval-20260929-1` and `dsh-eval-20260929-2` completed
both turns. Their private artifacts live under `artifacts/survival-v1-runs/`.
Copied-file equality does not establish isolated runtime replay, public-safe
redaction or independent reproduction. `dsh-cal-20260929-2` was named a
calibration attempt and is used as repetition 1; the run set was not designated
in the attempt ledger before scoring. This document asserts no score: the score
comes from the reviewed public packets.
