# Methodology — Session-Bench v0.3

Session-Bench scores each harness's CLI session format on **19 scored
pass/fail gates** (one further gate is defined but unscored). No partial
credit. Every verdict cites evidence in `data/verdicts.yml` or is computed
by `scripts/evaluate.py` from `data/measurements.json`.

## States

- **pass / fail** — scored, evidence-backed.
- **not run** — the measurement could not be taken (broken headless
  runtime, auth failure, no comparable metric, or an observation window
  too short to judge). Excluded from that harness's denominator: an
  authentication error is not evidence about a format.
- **not applicable** — T3 when T2 fails (see below).
- **unscored** — the gate is defined for everyone but not yet tested
  (P2, crash tolerance).

## Ranking

By exact fraction of scored gates cleared (competition ranking; ties share
a rank). Harnesses with a not-run gate get a provisional rank with an
exhaustively enumerated best/worst range (every pass/fail assignment of
the missing cells is ranked).

## The gates

### Signal — is the log your work, or the harness's paperwork?
- **S1 Lean session** (computed): the identical one-line probe task adds
  ≤ 10 KB (incremental bytes; database cold-start allocation reported
  separately, not scored).
- **S2 Content-key share** (computed): strings under a published list of
  content-like keys ≥ 25% of stored bytes (corpus measurement). The
  heuristic counts work product and any bookkeeping stored under those
  keys; known inflation is noted per-agent. Per-event classifiers are a
  1.0 milestone.
- **S3 No fixed-cost dumps** (computed): no single per-session
  bookkeeping record over 25 KB (system-prompt dumps, tool-catalog
  snapshots, context snapshots).

### Completeness — can you audit what happened and what it cost?
- **C1** per-event timestamps · **C2** model attributable per assistant
  message · **C3** token usage recorded · **C4** dollar cost recorded
  (premium-request counters do not pass) · **C5** tool calls AND outputs
  · **C6** readable rationale (raw thinking or a stored summary; sealing
  may be vendor policy — the gate scores the record, not the policy) ·
  **C7** thread/subagent structure recoverable.

### Stability — will your archive still parse after the next update?
- **T1** no breaking schema change while under observation (windows
  differ per harness and are stated per cell; a too-short window scores
  not run).
- **T2** declares a **format** version — a schema/protocol version an
  external reader can dispatch on. An application/CLI version identifies
  the writer, not the schema, and does not pass (tightened in v0.3).
- **T3** the declared version matches shipped reality. Scored only when
  T2 passes; absence is already penalized once by T2.

### Openness — can you read your own history with standard tools?
- **O1** readable at rest (text editor or sqlite3; protobuf/hex fails) ·
  **O2** one live-tailable artifact holds the full record · **O3** the
  vendor publishes documentation for the session format · **O4** no
  wholesale storage-layout migration while observed.

### Tooling — will scripts and history browsers survive its quirks?
- **P1** a naive reader gets every message exactly once · **P2** crash
  tolerant (UNSCORED until a cross-harness truncation experiment exists)
  · **P3** no sidecar join needed for title/cwd/model.

## The probe

One identical prompt — "List the files in the current directory, then say
hello in one sentence." — attempted headless through every harness; each
resulting artifact measured byte for byte, each failed run recorded as
exactly that. Probe 2026-08-04; store queries 2026-08-05; documentation
review 2026-08-12.

## Rubric versioning

Comparability-breaking changes bump the bench version:
- v0.1 → v0.2: T3 conditional on T2; per-harness observation windows;
  two Codex verdicts corrected on pinned evidence.
- v0.2 → v0.3: T2 restricted to true format versions.

Full history: [CHANGELOG.md](CHANGELOG.md).
