# Native scoring and release verification

`session_bench.score_replay.replay_score_package(path, expected_manifest_sha256=pin,
os_sandboxed=True)` validates an externally pinned complete packet before executing
its copied source. Packets bind the exact workload, independent observer, native
bytes, capture assertions, format builders, comparator, scorer and standalone
runner. The runner freshly decodes native records and produces all 31 diagnostic
metrics. It also removes a selected native response while holding the independent
observer population constant. Host tamper controls reject altered workload,
observer, context, native, scorer and runner bytes before execution.

The optional OS backend requires macOS `sandbox-exec`; unsupported platforms fail
closed. Actual outside-read, outside-write and loopback-connect probes must all be
denied. The worker can read its copied packet, temporary work area, interpreter
installation and required installed libraries. It cannot read the original packet,
repository or home directory. Literal ancestor-directory access supports the
no-follow input reader. Every pinned packet file is checked unchanged after replay.
The temporary work area is writable, so this is input/output isolation rather than
a claim that the copied packet is physically read-only. Python/stdlib and OS remain
external dependencies; DSH additionally pins the installed libzstd version and
binary SHA, checking its SHA before loading. Library replacement during loading is
detected by a second read; this is not a hostile-host execution guarantee.

Historical Codex Desktop observers establish submitted turns and visible responses only.
The retained CLI stdout supports additional independently observed tool events.
Since 2026-10-03 (see `rubric.md`, "Evidence tiers"), model identity, usage,
token semantics and reconciliation are scored from native bytes; the observer's
token values are not compared. A changed file gets native credit when the native
record holds explicit hashes, or a native pre-image plus the native edit. This
rule is the same for every configuration. Claude Desktop density requires its complete bound transcript
and metadata pair. Capture completeness and acquisition authenticity remain the
retained operator assertions, not facts newly witnessed during replay. No private
replay receipt alone grants public ranking or an independent publication badge.

`session_bench.release_replay.verify_release_configuration` accepts exact public
bundle bytes, exact independent review bytes, a separately trusted review SHA and
reviewer/producer IDs, three pinned native packets, and three actual copied public
handoff directories. The bundle schema is `session-bench-release-replay-input-v1`
with exactly `schema_version`, `configuration_id`, and three `pairs`; every pair
contains strict `survival` and `format` evidence documents.

The review schema is `session-bench-release-replay-review-v1`, with exactly:

- `reviewer_id`, `producer_id`, `public_bundle_sha256`, and `scope`, which must be
  `native-to-score-and-exact-public-inputs`;
- `checks`: successful independent operator execution, public input safety review,
  complete replay packet public safety review, run identity review, metric locator
  binding review, and native-to-score semantics review;
- `runs`: exact run/configuration/repetition, manifest SHA and diagnostic SHA for
  each of the three native packets;
- `limitations`: nonempty disclosures of environment and acquisition limits.

The expected reviewer and producer must differ and come from a trusted channel
outside these documents. A same-host separate operator may qualify; their receipt
must disclose that fact. These are explicit operator attestations: software cannot
prove a person's independence, privacy review or original acquisition authenticity
from a JSON boolean. A receipt approving only private replay is rejected.

Verification actually reruns each pinned packet under OS isolation, rechecks
tamper controls, and compares the public measurement, entire format evidence,
observer digest and native-manifest digest to the fresh output. Every public
handoff must contain byte-for-byte the whole replayed packet, including native,
observer and source files. A redacted semantic derivative or score-only folder
cannot substitute. Sanitization requires a new closed successor packet, reviewed
for provenance and scoring semantics; private originals remain untouched.

The verifier constructs configuration evidence from the actual public bundle and
review receipt hashes, then seals the exact aggregate, including exact fractions
and nested proofs. Missing metrics and identities retain their existing blockers.
The raw release scorer remains diagnostic and unranked. Public ranking requires
three complete sealed configurations. Publication additionally requires every
displayed score to be sealed and all 14 scoped statuses to be visible. Completing
the full measurement goal requires all 14 configurations to qualify. A limited
cohort can meet the publication gate while that larger goal remains incomplete. A forged or subsequently mutated dataclass is rejected.

The current public candidates for Claude CLI, OpenCode CLI and DeepSeek Harness
CLI each have three complete 31-metric native replays and separate operator
reviews. The release builder consumes their separately trusted review hashes
and repeats OS-isolated verification before ranking. The expanded candidate
shows an explicit evidence state for all fourteen scoped configurations instead
of using N/A placeholders; unresolved measurements remain unscored and cannot
be converted to zeros or borrowed from another surface. See
[expanded-preparation-status.md](expanded-preparation-status.md) for the current
candidate and remaining work.
