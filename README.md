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

## Current leaderboard — v0.3, corrected 2026-08-12

| # | Harness | Version | Gates cleared |
|---|---------|---------|---------------|
| 1 | Pi | 0.83.0 | 18 / 19 |
| 2† | OpenClaw | 2026.6.11 | 17 / 18 |
| 3 | Claude Code | 2.1.220 | 12 / 18 |
| 3 | Codex | 0.146.0 | 12 / 18 |
| 5† | OpenCode | 1.18.11 | 11 / 17 |
| 6† | Kimi Code | 0.31.1 | 11 / 18 |
| 7† | Hermes | 0.17.0 | 10 / 17 |
| 8 | Copilot CLI | 1.0.77 | 10 / 18 |
| 9 | Antigravity | 1.1.1 | 9 / 18 |
| 10 | Cursor Agent | 2026.7.20 | 7 / 18 |

† A measurement could not be taken (broken headless runtime, auth failure,
no comparable store-size figure, or an observation window too short to
judge stability); that gate is *not run*, drops out of the denominator,
and the rank is provisional within a stated best/worst range.

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

## Scope and honesty notes (v0.3)

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

The **bench version** (v0.3) is the rubric — gates, thresholds, scoring
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

## License

MIT. See [LICENSE](LICENSE).
