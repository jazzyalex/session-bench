# Session-Bench

**The coding-agent session-format benchmark.**

A vendor report card for how useful, inspectable, stable, and open
coding-agent session records are.

SWE-bench measures whether the agent completed the work. Session-Bench
measures what the harness preserved after the work was done: the session
files written to disk that determine whether work can be searched, audited,
priced, resumed, migrated, and consumed by tools outside the original
harness.

Readable report card: **https://jazzyalex.github.io/agent-sessions/bench/**
This repository is the technical source: methodology, data, evaluator,
evidence notes, and the public correction history.

The report card is temporarily hosted by Agent Sessions. Its Pages repository
vendors `data/leaderboard.yml` byte-for-byte and supplies only the Jekyll view;
scores and evidence are changed here first. Moving the report card to separate
hosting later will not change the benchmark data or evaluation workflow.

Planned work, including cross-surface local-storage coverage, is tracked in
[BACKLOG.md](BACKLOG.md).

## Current leaderboard — v0.4, corrected 2026-08-23

| # | Harness | Version | Gates cleared |
|---|---------|---------|---------------|
| 1† | Pi | 0.83.0 | 18 / 19 |
| 2† | OpenClaw | 2026.6.11 | 17 / 18 |
| 3† | Claude Code | 2.1.220 | 12 / 18 |
| 3† | Codex | 0.146.0 | 12 / 18 |
| 5† | Kimi Code | 0.31.1 | 11 / 18 |
| 5† | OpenCode | 1.18.11 | 11 / 18 |
| 7† | Hermes | 0.17.0 | 10 / 17 |
| 8† | Copilot CLI | 1.0.77 | 10 / 18 |
| 9† | Antigravity | 1.1.1 | 9 / 18 |
| 10† | Cursor Agent | 2026.7.20 | 7 / 18 |

† A measurement could not be taken (broken headless runtime, auth failure,
no comparable store-size figure, an observation window too short to judge
stability, or no evaluated collapse rule for the superseded-share gate);
that gate is *not run*, drops out of the denominator, and the rank is
provisional within a stated best/worst range.

Regenerate from the versioned inputs (clean clone):

```
git clone https://github.com/jazzyalex/session-bench
cd session-bench
python3 -m pip install -r requirements.txt
python3 scripts/evaluate.py \
    --measurements data/measurements.json \
    --checklist data/verdicts.yml \
    --out data/leaderboard.yml
python3 -m pytest tests/ -q
```

`data/leaderboard.yml` is generated — never hand-edited. To dispute a
score, dispute a measurement or an evidence line and re-run the evaluator.

To refresh the temporarily hosted report card after reviewing and testing a
change here, run the Agent Sessions import command against this generated file:

```
cd /path/to/agent-sessions
python3 scripts/sync_session_bench.py \
    /path/to/session-bench/data/leaderboard.yml
```

The importer rejects artifacts with old Agent Sessions provenance and copies
the accepted file without rewriting it. GitHub Pages therefore builds from a
reviewed, checked-in snapshot rather than fetching another repository during
deployment.

Checklist cells may carry `source_url` (HTTP/S) and quoted `observed_at`
(YYYY-MM-DD) together. The evaluator preserves these under each agent’s
`sources[gate_id]`; the live matrix links cited cells and shows the check date.
A check date is not a measurement date or a score update. For failed gates,
the link identifies inspected documentation, not proof of universal absence.

## Scope and honesty notes (v0.4)

- **CLI session stores only.** Desktop apps and IDE plugins can use
  different stores; they are candidates for their own rows later.
- **Format quality, not harness quality.** Nothing here measures whether
  the agent writes good code.
- **Some observations are not publicly reproducible yet.** The evaluator
  reproduces the scoring from versioned inputs; raw probe artifacts and
  corpus query transcripts are private local session data. Sanitized
  fixtures are planned for v1.0. See [evidence/README.md](evidence/README.md).
- **Copilot's documented-schema pass is disputed** (the weakest pass on
  the board): its CLI storage docs and SDK event docs match our measured
  artifact's event vocabulary, but no single official page ties the two
  surfaces together.
- **Observation windows differ per harness** (2026-03-31 for the
  longest-observed; Kimi from 2026-07-25). Every stability verdict states
  its own window.
- **S4 is final-state-lossless, not "provably lossless."** The qualifying
  collapse rule keeps the newest snapshot per message; the timestamps of
  superseded intermediate snapshots are lost, which is why the stronger
  wording would make S4 and C1 unsatisfiable together.
- **Waste outside the session store does not score.** Codex's measured
  freelist waste sits in a separate tracing store, so its S4 is *not run*
  rather than a fail: one `PRAGMA incremental_vacuum` on the reader's own
  machine would change the number with no vendor change.

## Challenging or updating a result

Open an issue with one of:

1. **A disputed measurement** — say which number in
   `data/measurements.json` and provide the artifact or query that
   contradicts it.
2. **A disputed verdict** — say which cell in `data/verdicts.yml` and
   provide the evidence (a document, an artifact, a version).
3. **A vendor change** — new documentation, a format version field, a
   schema change. A fixed gate flips on the next re-score and the change
   is recorded in [CHANGELOG.md](CHANGELOG.md); the first harness to
   clear every scored gate gets named on the report card.

Every correction so far came from exactly this kind of challenge. The
[CHANGELOG](CHANGELOG.md) is the public record.

## Versioning

The **bench version** (v0.4) is the rubric — gates, thresholds, scoring
rules — and bumps only on comparability-breaking changes. The **data
dates** identify when observations were taken. **v1.0** is reserved for
end-to-end reproducibility: harness execution to archived artifacts to
extraction, a tested crash-tolerance gate, and per-event content
classifiers.

## Relationship to Agent Sessions

Session-Bench is maintained by the author of
[Agent Sessions](https://github.com/jazzyalex/agent-sessions), a macOS
browser that parses all ten of these formats in production — that parsing
work is where the gate evidence comes from. The benchmark's purpose is
independent: establishing what a responsible coding-agent work record
looks like.

Agent Sessions owns the local observation systems: production parsers,
sanitized fixtures, format-drift monitoring, and the private session corpus.
Session-Bench owns the published measurement aggregates, verdicts, evidence,
scoring code, tests, generated leaderboard, and correction history. The
benchmark can be regenerated from a clean clone without an Agent Sessions
checkout or access to private sessions.

## License

MIT. See [LICENSE](LICENSE).
