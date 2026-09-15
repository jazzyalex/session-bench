# OpenCode local-evidence reproducibility correction

Status: **required before any OpenCode survival result is presented as reproducible,
released, or independently reproduced.** This is an offline correction procedure. It does
not launch OpenCode, create a vendor session, change the historical native bytes, or turn
the local 81.0 Survival records into a v1 `/100` score.

## Defect

The three retained `opencode-cli-eval-*` packages bind a SHA-256 for
`opencode-sqlite-decoder-v1`, but the package boundary does not include that exact source
or a closed runtime that can execute it. The maintained successor decoder produces the
same normalized decoded output on all three stored bundles, but its source SHA differs.
The normal package recheck therefore refuses the packages, correctly.

## Correction package contract

Create a new package for each historical package; never rewrite the old package. The new
package must have a new package ID and result ID, retain the original package digest and
native-artifact hashes as `supersedes`, and state `reason: missing_decoder_runtime`.

Its closed file inventory must contain:

```text
native-bundle/opencode.db
native-bundle/opencode.db-wal
native-bundle/opencode.db-shm
observer.json
decoded.json
portability-receipt.json
measurement.json
evidence.json
replay-runtime/recheck.py
replay-runtime/session_bench/adapters/opencode_decoder.py
replay-runtime/session_bench/live_metric_comparator.py
replay-runtime/session_bench/survival_metrics.py
replay-runtime/manifest.json
package-manifest.json
```

`replay-runtime/manifest.json` lists every runtime file with size and SHA-256. The package
manifest hashes the runtime manifest and every listed runtime file. `evidence.json` names
the decoder file path and its SHA-256. The runtime must use only the copied package and
the Python standard library; it must not import the repository checkout, discover a
profile, access a vendor executable, or contact a network service.

## Acceptance sequence

1. Copy each existing package to a new correction workspace and verify its historical
   manifest before reading native SQLite.
2. Decode the copied DB/WAL/SHM family with the correction runtime while the original
   package and any vendor roots are inaccessible.
3. Require canonical equality between the correction decode and the retained historical
   `decoded.json`; if unequal, stop and record a new incompatible-decoder investigation.
4. Re-run the frozen observer/native comparator and survival scorer inside the correction
   runtime. Require equality with the retained measurement and 81.0 display only as a
   historical-correction check.
5. Re-run the selected damaged-copy control inside the same runtime and require its
   declared loss outcome.
6. Generate new manifests, a correction receipt, and a local recheck receipt. Do not
   mark independent reproduction true.
7. Run the correction package in a separate local environment before asking a second
   operator to reproduce it.

## Public boundary

The historical local packages include absolute workspace paths in native-derived records.
They are private evidence inputs, not public downloads. A later public packet needs a
labeled sanitized derivative, a redaction receipt, and a fresh decoder/comparator replay
against that derivative. It cannot relabel transformed data as raw native evidence.

Only after the corrected package passes a second operator's isolated replay may its
reproduction state change. That still does not satisfy the five-surface, three-run,
31-metric public leaderboard gate.
