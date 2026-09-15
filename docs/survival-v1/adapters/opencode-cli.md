# OpenCode CLI acquisition adapter

Status: bounded adapter plus retained calibration capture, 2026-09-11. The correction
captured one session and its DB/WAL/SHM bundle but remains unscored until the current
SQLite decoder exists.

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

## Integration boundary

This module supplies launch, observation, and native acquisition primitives for the
survival-v1 surface-capture layer. It does not edit `surface_capture.py`, decode
OpenCode's evolving application schema, score metrics, or write the campaign ledger
format. The caller should pass the copied native manifest and observer binding to the
separate evidence/evaluation stages. Calibration remains unranked until the complete
protocol, workload, observer, and evidence gates are satisfied.
