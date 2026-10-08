# OpenCode CLI acquisition adapter

Status (2026-10-08): the row is scored. Three fresh two-turn captures of OpenCode
1.18.31 (`opencode-1-18-31-eval-1` to `-3`) are decoded with the contract
`opencode-sqlite-session-v1`. All 31 metrics resolve in each run. The read includes
the `event` table (see *Duplicate safety and density*). The row scores 94.4. The
private packets are `opencode-1.18.31-native-score-v9` and the public packets are
`opencode-1.18.31-public-candidates-v8`. Never publish the earlier public sets
`opencode-1.18.31-public-candidates-v1` to `-v7`.

History. On 2026-09-11 this document described a bounded adapter plus a retained
calibration capture. The correction captured one session and its DB/WAL/SHM
bundle. It stayed unscored until the SQLite decoder existed. The sections
*Isolation and authentication*, *Independent stdout observer*, *Quiescent SQLite
capture* and *Retained calibration* describe that adapter and that capture. They
are still true for the adapter.

OpenCode is a separate `opencode-cli` surface. The adapter uses the pure JSON CLI path:

```text
opencode run --pure --format json --dir <fresh-project> <prompt>
```

Every call is made through an injected runner. Importing or constructing the adapter
does not invoke OpenCode.

## Isolation and authentication

The caller creates a new run root and scratch project for every attempt. The default
calibration launch binds the native database explicitly:

```text
OPENCODE_DB=<run-root>/opencode.db
```

The database path must be new before launch. The adapter never scans the normal
`~/.local/share/opencode` data root, opens its database, reads its `auth.json`, or copies
any file from it. OpenCode's supported default auth lookup may therefore remain in
place; only the native database is redirected for this bounded route.

An explicitly isolated auth setup remains available when the owner requires it. In that
mode, an approved provider writes the credential into exactly
`<run-root>/xdg-data/opencode/auth.json`, for example through
`provision_isolated_auth(run_root, writer)`, and the launch also sets
`XDG_DATA_HOME=<run-root>/xdg-data`. The writer receives only the new target path.
`validate_isolated_auth` checks target identity and file type without reading the
credential. A normal-profile path, an unlabeled credential, a symlink, an empty target,
or a missing target blocks an explicitly isolated launch and leaves an invalid attempt
record.

The adapter does not infer authentication from a successful command, open or copy
credential material, or silently retry with another account/provider. Callers may add a
PATH entry to the injected runner, but may not replace `OPENCODE_DB` (or the fresh data
home when explicit isolated auth is selected).

## Independent stdout observer

`observe_stdout` consumes the exact stdout stream from `--format json`. It accepts JSONL
text or an injected iterable of JSON fixture objects and rejects blank-only, malformed,
non-object, or non-JSON output. It extracts session identifiers from declared
`sessionID`/`session_id`-style fields and requires exactly one identifier that was not in
the caller's pre-run set. The same marker repeated on later events is one session, while
zero or multiple new markers is an observer-binding failure.

The returned observer retains raw lines, ordered parsed events, response text candidates,
response canaries, and the selected marker. `bind_observer_to_native_session` is a
separate gate: the selected marker must occur in the copied native session set and may
not be a pre-existing session. The observer therefore cannot be replaced by a later
database query or by personal history. A stream can prove the visible response boundary;
it does not supply the native record or the evaluator's answer key.

## Quiescent SQLite capture

The native root is the explicit database plus both required companions:

```text
opencode.db
opencode.db-wal
opencode.db-shm
```

`capture_sqlite_bundle` requires an injected barrier. The barrier must report a
quiescent writer (`True`, `{"quiescent": true}`, or
`{"writer_alive": false}`); a live/unknown writer is refused. At the barrier the
adapter:

1. requires all three files to be ordinary, non-symlink files;
2. reads and hashes each file;
3. copies the three bytes into a new destination; and
4. re-stats, re-reads, and re-hashes every source and copied file.

If a DB, WAL, or SHM file is missing, replaced, or changed during this interval, the
whole capture fails closed. The destination is removed on a copy-integrity failure.
The result carries one manifest entry per file with relative name, size, and SHA-256;
absolute source paths remain acquisition metadata and are not native record locators.
The copied database can then be passed to a narrow, injected session-ID reader. Reads
for decoding happen from the copy, never by reopening the only source evidence.

## Retained calibration

This section is history of 2026-09-11. The calibration capture is not one of the
three scored runs.

`run_calibration` composes the explicit launch, injected runner, stdout observer,
quiescent capture, native-session binding, and a read/edit permission probe. The probe
must provide successful read and edit evidence plus distinct before/after file hashes;
optional read/edit markers make the evidence easier to inspect. A successful result has
all of the following:

- the auth target is explicit and isolated;
- the runner used the exact pure JSON command and isolated environment;
- stdout identified one new session;
- the captured DB, WAL, and SHM files are coherent and hash-bound;
- the stdout marker occurs in the copied native session set; and
- the calibration project proved that OpenCode could read and edit it.

`AttemptLedger` is append-only. Runner refusals, auth/setup failures, nonzero exits,
observer mismatches, missing/changed companions, and failed permission probes each
produce an `invalid` attempt with a reason and transition history. No failed calibration
is replaced by a later success, and a corrective setup attempt must retain its own
attempt ID under the frozen attempt ledger.

The adapter's tests use fake runner, stream, SQLite, barrier, and permission fixtures.
The retained live correction used the same isolated database contract, bound both
response canaries to one new session, recorded exactly three helper events, and copied
the quiescent DB/WAL/SHM set. No existing OpenCode database, auth file, or history was
opened.

## Native schema version and decoder contract

OpenCode 1.18 does not use `PRAGMA user_version` (it is `0`). The database declares its
schema through the `migration` table: one row per applied schema migration
(`id`, `time_completed`). The declared format version is the ordered ledger: the
migration IDs in ascending ID order. The evidence reports the last ID, the row count, and
the SHA-256 of the IDs joined by newlines, and binds them to the SHA-256 of `opencode.db`.

The decoder contract `opencode-sqlite-session-v1` is bound to the ledgers listed in
`OPENCODE_SUPPORTED_SCHEMA_LEDGERS` (currently one: 38 migrations, last
`20260622202450_simplify_session_input`). The decoder refuses any other non-empty ledger
with the `unsupported_schema_ledger` diagnostic; every metric is then
`decoder_unsupported`. Scoring rules:

- `broad.declared_format_version` is measured when the ledger has at least one row. An
  empty or missing ledger in a complete copied family is `native_absent`.
- `broad.honest_version_signal` is measured only when the ledger is one the decoder
  contract names and the session decoded under it. An unknown ledger is `unresolved`.
- A database with no ledger is still decoded (older fixtures); it earns no version credit.

## Native projection for the observer comparison

`project_opencode_native` adds three facts to the decoder result. Each one uses native
rows only; no observer, stdout, helper-ledger, or workspace value is read.

- **Helper commands.** A `bash` tool part whose `command` is exactly
  `python3 bench_check.py <inspect|baseline|final> --run-canary <canary>` and whose
  `workdir` ends in `fixture_project` gets that `argv` and the target
  `fixture_project/checkout.py`. Any other shell text is left unchanged.
- **`final_after`.** The relation R2 message → final test part is emitted only when one
  R2 user message, its edit part(s), one successful `final` helper part (exit `0`), and
  one visible response all carry `parentID` equal to the R2 message, and the native
  order is strict. The order is the `seq` of the `event` table (first appearance of a
  message or part, and the first `completed` state of a part), and the native ids must
  agree with it: R2 message < edit started and finished < final test started < final
  test finished < response message. Timestamps are not used.
- **Whole-file hashes.** The pre-image is the `checkout_source` that the native output of
  the `inspect` helper carries, accepted only when it matches its own `checkout_sha256`
  and the inspect completed (event `seq`) before the edit started. The post-image is that source with the
  native `edit` input applied (`oldString` must occur exactly once, or at least once
  when `replaceAll` is `true`). The session must hold exactly one mutating tool part and
  no shell command other than the helper; otherwise the hashes stay absent.

## Duplicate safety and density

**The read includes the `event` table (owner decision 2026-10-08).** The decoder
reads the `event` table. It takes the `seq` order and the completion status of
each part from it, for `revision.final_after_r2` and for the order of the
inspect result and the edit in `work.changed_files`. So the read is the
`session`, `message`, `part` and `event` tables and the `migration` ledger
(`OPENCODE_READ_TABLES`). This is the read of the first scoring.

A leaner read was tried on 2026-10-05, after first scoring: the decoder stopped
opening `event` and proved order from the row id and the SQLite `rowid` of the
`message` and `part` rows. Duplicate safety then passed (29 of 29, 22 of 22,
26 of 26) and the row scored 97.4. An outside review of candidate v38 named this
as a read chosen after scoring. On 2026-10-08 the owner withdrew the leaner read
and decided that v1 uses the read of the first scoring. The row scores 94.4
(97.4 with the leaner read).

The `event` table is an update log. Each `message.part.updated` row holds a
whole part: text, tool name and arguments, output. Every part is in `part` and has
1 to 5 update rows in `event` (counted in the public databases: 5 rows for three
`bash` parts, 4 for the `edit` part, 3 for `read` parts, 2 for most `reasoning`
parts, 1 for `step-start`, `step-finish` and some `text` parts; the same in all
three runs), with no supersession field. With `event` in the
read, no event is stated once. Duplicate safety is 0 of 79, 0 of 62 and 0 of 72
statements (29, 22 and 26 distinct events), and the row loses 3 points.

- Duplicate safety is counted on raw rows. A text part is one message. A tool
  part is one call once its state holds the arguments, and one result once it
  ended, or once its `metadata.output` holds the complete final output of the
  call (a running shell state does this: the status does not matter). A `pending` tool state has empty arguments: it names the tool and does
  not restate the call. The same rule applies to `part` and to `event` rows
  (`part` rows hold no pending state). A `session`, `message` or `event` row that
  holds the exact text of a part restates that message. Statement counts were
  76, 59 and 69 until 2026-10-08: the running shell states that already hold the
  full output (3 per run) were not counted. No score changed.
- Density counts every row of the database by its bytes. The first record of the
  read that states an event keeps its role. A record that restates a stated
  event is a `snapshot`. A record that states no event is `metadata`. The part
  rows come first, so every `event` row is a `snapshot` (it restates a part) or
  `metadata` (it states nothing, for example `message.updated`, `session.updated`
  and empty text starts). Both are unclassified. The density value is the same
  as with the leaner read, because the event rows were unclassified before as
  well.
- The functions `read_opencode_event_order` and `project_opencode_native` (event
  `seq` first appearance and completion) are the ones of the first scoring.

## Integration boundary

This module supplies launch, observation, and native acquisition primitives for the
survival-v1 surface-capture layer. It does not edit `surface_capture.py`, decode
OpenCode's evolving application schema, score metrics, or write the campaign ledger
format. The caller should pass the copied native manifest and observer binding to the
separate evidence/evaluation stages. The decoder is `session_bench/adapters/opencode_decoder.py`
and the packet inputs are built by `session_bench/opencode_score_inputs.py`; the scored
row is described at the top of this document.

The calibration capture of 2026-09-11 is not a scored run: it stays unranked, as a
record of the adapter. The scored runs are the three captures named at the top.
