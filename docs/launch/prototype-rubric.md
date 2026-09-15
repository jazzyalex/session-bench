# Session-Bench v1 prototype pilot rubric

Finalized for this bounded pilot report on 2026-09-10. It is the working rubric
used to interpret calibration captures, not the freeze for evaluated runs. This
additive prototype does not change the historical evaluator, capture code, or v0.4 results. It reads only
capture directories explicitly passed by the caller. It performs no discovery,
account access, private-history reading, or model calls.

## Capture contract

Each capture directory contains `metadata.json`, `observer.json`, and the
declared native files. `metadata.json` has this shape:

```json
{
  "schema_version": "session-bench-prototype-capture-v1",
  "id": "stable-configuration-id",
  "name": "Exact configuration label",
  "surface": "cli-noninteractive",
  "version": "exact build",
  "model": "exact model/configuration",
  "run_id": "unique-attempt-id",
  "captured_at": "2026-09-10T12:00:00Z",
  "native_files": [
    {
      "path": "native/rollout.jsonl",
      "sha256": "64-lowercase-hex-characters",
      "bytes": 1234,
      "kind": "codex-rollout-jsonl"
    }
  ],
  "required_native_files": ["native/rollout.jsonl"],
  "artifact_set_complete": true,
  "physical_bytes": 4096,
  "physical_measurement": "incremental allocated bytes across declared roots at the quiescent capture barrier",
  "status": "pilot"
}
```

`id` identifies the configuration and is shared by its repetitions. `run_id`
identifies one attempt and is unique within the configuration. Supported native
kinds are `codex-rollout-jsonl`, `opencode-messages-json`, `opencode-sqlite`, and
`opencode-sqlite-companion`. The SQLite adapter requires the native OpenCode
`session`, `message`, and `part` tables and treats database/companion bytes as
structural rather than pretending JSON payload fragments partition the database.
Paths are relative,
non-symlink paths below the capture. Declared byte counts and SHA-256 digests must
match before measurements can establish native facts. `artifact_set_complete`
means the capture process established the full relevant native root and required
companions at one coherent barrier; it is false when companion scope is unclear.
An optional `limitations` string array records acquisition-specific caveats and
is copied into the report without turning those caveats into scores.
`physical_bytes` and `physical_measurement` are optional for pilots but required
to score S1. They describe incremental on-disk allocation at the same capture
barrier; copied file length or `native_files[].bytes` is not substituted.

The observer file is independent task/action evidence:

```json
{
  "schema_version": "session-bench-prototype-observer-v1",
  "run_id": "unique-attempt-id",
  "artifact_manifest_sha256": "sha256-of-canonical-sorted-native-files-array",
  "independent": true,
  "observer_method": "submitted prompts, disk ledger, and separate live CLI stream",
  "events": [
    {
      "id": "submit-1",
      "kind": "user_message",
      "source": "submitted_input",
      "text": "exact submitted text",
      "context_marker": true,
      "expected_native": true
    }
  ]
}
```

The manifest binding is SHA-256 of UTF-8 JSON for `native_files`, sorted by
`path`, with object keys sorted and separators `,` and `:`. Duplicate observer
IDs invalidate observer-dependent checks. Set `context_marker: true` only for a
marker predeclared before collection; otherwise C2 is unexercised. Accepted evidence sources are
`submitted_input` for user messages/corrections; `disk_ledger` for deterministic
commands, results, failures, and file changes; and `pty`, `ui`, or
`live_cli_stream` for visible assistant text. A `live_cli_stream` is eligible only
when the observer method explicitly says it observed a live stream. Unobserved
assistant text is unresolved, never presumed absent.

## Eighteen checks and fixed weights

Checks within a category have equal weight. Work history is 30%, context
visibility 15%, usage transparency 20%, access and portability 25%, and storage
efficiency 10%.

| ID | Operational measurement |
|---|---|
| W1 | One-to-one exact recovery of independently submitted user messages and corrections. |
| W2 | One-to-one recovery of observed tool names, arguments, and results. |
| W3 | Recovery of independently observed nonzero exits or exact failure evidence. |
| W4 | Recovery of independently observed changed-file paths from native edit evidence. |
| W5 | Unique native action/result IDs, one-to-one joins, and no duplicate IDs. |
| C1 | Native surface, build/version, and model identity matching declared configuration fields. Missing native fields stay unresolved within the fixed three-field denominator. |
| C2 | Exact recovery of submitted instruction/context text. |
| C3 | Recovery of plan/explanation text that was independently visible in UI, PTY, or a separate live CLI stream. |
| U1 | Usage records joined by stable native response/message identity to independently observed assistant responses. Record count or snapshot density cannot substitute for joins. |
| U2 | Presence of separately named input, output, and cache token semantics. |
| U3 | Monotonic internal reconciliation across at least two cumulative native usage snapshots. This is an internal-consistency result, not a billing-accuracy claim. |
| U4 | Native price/cost/billing provenance or an explicit native unknown. Subscription billing is not inferred from estimated cost. |
| A1 | Native session identity and project/cwd identity, one half each. |
| A2 | Independent parsing of at least one event from hash-verified declared native artifacts. |
| A3 | All predeclared required companions plus unique, complete action/result joins. |
| A4 | Complete declared artifact boundary decoded entirely from the copied capture. |
| S1 | Predeclared incremental physical bytes scored with fixed logarithmic anchors: at or below 64 KiB = 100; at or above 4 MiB = 0; monotonic log interpolation between. |
| S2 | Descriptive full-record byte composition. JSONL records are assigned by record role; SQLite database and companion bytes remain structural container bytes. This pilot does not score S2 because those representations are not cross-container comparable. |

Observed populations are deduplicated by unique observer ID and matched
one-to-one to native records. A missing expected event cannot shrink a
denominator. Empty populations are `unexercised`, not passes. Category scores
retain the frozen check denominator: unresolved/unexercised checks contribute no
displayed points and coverage shows how many checks were measured. Repetitions
are aggregated with equal weight; a longer run gets no extra influence.

## Evidence states and ranking rule

- `measured`: the decoder and bound evidence establish the result.
- `native_absent`: eligible independent observation plus a complete native
  boundary establish that a supported fact was not recorded.
- `decoder_unsupported`: the artifact exists but is malformed or outside the
  implemented native shape; this is not a product absence.
- `unresolved`: acquisition, completeness, observer, or identity evidence cannot
  decide presence versus absence.
- `unexercised`: the run did not contain the predeclared population needed for
  the check.
- `invalid_capture` or `contradiction`: integrity/join evidence failed.

Every category publishes its available score and coverage. An overall weighted
score is emitted only for `evaluated` captures with a complete hash-verified
boundary, valid independent observer binding, supported decoding, and all 18
checks measured. `pilot`, `unavailable`, `invalid`, and incomplete evaluated
captures always have a null overall score. The prototype emits no ranks. A pilot
therefore supplies useful category facts while remaining visibly unranked.
Any native size or hash mismatch invalidates the capture and suppresses every
artifact-derived score, even when the changed bytes still parse.

Storage composition classifies captured file bytes by whole-record role and is
reported alongside `native_bytes`. It is descriptive, not a logical-payload
partition, and S2 remains unresolved. S1 uses the separately declared
`physical_bytes`. Neither measure is cold initialization, runtime cost, token
count, or evidence that omitted history had no value.
