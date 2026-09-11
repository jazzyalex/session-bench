# Session-Bench Backlog

Planned benchmark work. Items here do not change the published v0.4 scores until their
method, evidence requirements, and comparability impact are approved and released under
a new benchmark version.

## P0 — Define cross-surface local persistence

### Establish the unit of comparison
- Keep the benchmark limited to local records written to disk. Vendor backends, cloud
  sync, and account-hosted history remain out of scope.
- Distinguish three concepts that v0.4 currently collapses:
  1. **Harness:** Claude Code, Codex, Cursor, and so on.
  2. **Surface:** CLI, Desktop, IDE/plugin, or another user-facing execution surface.
  3. **Artifact family:** a physically distinct transcript, database, sidecar, archive,
     or log format.
- Do not duplicate all format gates when two surfaces demonstrably write the same
  artifact family. Add a surface sub-row only when the root, format, companion artifacts,
  event vocabulary, or retention behavior differs.
- Treat a different storage root as a meaningful result even when the record schema is
  otherwise identical: discoverability and portability depend on knowing every root.

### Publish a surface-storage manifest
Add a versioned manifest with one row per observed surface containing:
- harness and tested version;
- surface and launch path;
- primary and companion local roots;
- physical formats and tables/record families;
- session ID and cross-artifact join keys;
- explicit surface marker, or `unknown` when none exists;
- project/task attribution fields;
- side/fork/subagent storage;
- archive and retention behavior;
- probe ID, observation date, and artifact receipt.

An [unreleased executable atlas draft](docs/atlas/README.md) now establishes the
candidate identity, source, maintenance, freshness, and claim-separation
contract across one CLI, desktop, and IDE row. Its format fields remain
explicitly unknown until authorized native inspection supplies evidence. This
draft does not satisfy the observed-surface manifest or publish an A0 edition.

The [offline campaign-plan contract](docs/campaign/README.md) now separates
targets, launch modes, artifact tracks, scheduled runs, attempts, native
sessions, access/isolation readiness, and hard resource caps. Its first
three-configuration plan remains proposed and carries no execution authority.

## P1 — Controlled paired probes

Start with the three harnesses for which local evidence already shows cross-surface
complexity.

### Claude
- Run the same minimal task in CLI, Desktop Code, and Cowork/local-agent.
- Compare `~/.claude/projects`, `claude-code-sessions`, and
  `local-agent-mode-sessions`.
- Measure whether the primary JSONL schema is shared, which records are surface-specific,
  and what information exists only in sidecars.
- Test whether every session remains enumerable and readable when a companion sidecar is
  missing.

### Codex
- Run matched ordinary sessions in CLI and Desktop and compare rollout envelopes and
  surface markers.
- Run a matched `/side` probe on every surface that exposes it.
- Determine whether side conversations are stored only in `logs_*.sqlite`, also produce
  rollouts, or vary by surface/version.
- Score surface attribution only when the local artifact records it; do not infer it from
  a shared root.

### Cursor
- Run matched Cursor CLI/Agent and Cursor Desktop tasks.
- Attribute writes across `~/.cursor/projects`, `~/.cursor/chats`, workspace
  `state.vscdb`, and global `state.vscdb`.
- Determine which Desktop modes use the JSONL/`store.db` family and which use
  `ItemTable`/`cursorDiskKV` conversation records.
- Test whether the artifact families share stable IDs and whether combining them creates
  duplicate sessions.

### Probe controls
- Snapshot candidate roots before and after each run and retain a changed-file manifest.
- Use the same small task, project, and observation window across paired surfaces.
- Archive sanitized immutable fixtures before scoring.
- Record unavailable surfaces as `not_run`, never as failures.
- Separate observed evidence from hypotheses about SDK or app architecture.

## P1 — Proposed surface-coverage gates

These gates require fixtures and an evaluator design before adoption. Equal weighting is
not assumed.

1. **Local materialization:** the completed session has a durable local record.
2. **Enumerability:** sessions can be listed from local storage without the vendor UI or
   backend.
3. **Root discoverability:** all required roots are documented or deterministically
   discoverable.
4. **Artifact completeness:** transcript, metadata, tools, reasoning/rationale, and usage
   are recoverable across the declared artifact set.
5. **Stable joins:** companion files and databases carry stable session identifiers.
6. **Surface attribution:** the artifact identifies CLI, Desktop, IDE/plugin, or states
   that the surface is unknown.
7. **Project/task attribution:** the record can be assigned to the originating project
   and task without path heuristics alone.
8. **Side-session retention:** side conversations, forks, and subagents are retained and
   linked when the product presents them as part of the work.
9. **Orphan resilience:** a missing companion artifact degrades explicitly instead of
   silently hiding the entire session.
10. **Cross-surface portability:** equivalent records can be consumed without a
    surface-specific proprietary application.

## P2 — Scoring and publication design

- Decide whether surface coverage becomes a new scored category or a separate companion
  report. Do not silently add it to the v0.4 denominator.
- Publish both views:
  - a harness-level report card summarizing local coverage across tested surfaces;
  - an artifact-family matrix for parser and audit-tool authors.
- Mark untested surfaces and short observation windows prominently.
- Generate surface summaries and coverage warnings mechanically from the manifest.
- Add corrections and rubric changes to `CHANGELOG.md`; bump the benchmark version for
  any comparability-breaking scoring change.
- Keep the current CLI leaderboard available as a frozen historical view when the
  cross-surface version launches.

## P2 — Expand beyond the first three harnesses

- Inventory Desktop and IDE/plugin surfaces for every harness in the benchmark.
- Add a surface only when a local execution path and local artifact can be tested.
- Do not infer Desktop support from an SDK, shared binary, or marketing claim.
- Prioritize harnesses with multiple official surfaces or known companion stores.

## P3 — Public evidence and regression monitoring

- Publish sanitized fixtures and changed-file manifests for every scored surface.
- Extend extraction tooling to SQLite tables, sidecars, and multi-root artifact sets.
- Add fixture tests for joins, missing companions, duplicate IDs, and side sessions.
- Fingerprint schemas per artifact family and surface after vendor upgrades.
- Record when two previously distinct surfaces converge, or one shared format splits.

## Open questions

- Should root discoverability and format quality be scored together or reported as two
  independent dimensions?
- How should a harness be summarized when one surface is complete and another is not
  locally enumerable?
- When multiple surfaces share a store, what minimum evidence proves they share the same
  format rather than merely the same directory?
- Should ephemeral side conversations fail local materialization when the product labels
  them temporary, or should retention be reported as declared behavior rather than a
  universal pass/fail rule?
