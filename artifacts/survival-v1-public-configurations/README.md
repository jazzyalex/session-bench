# Session-Bench v1 public configuration derivatives

This directory contains sanitized derivatives for exactly four already-qualified
configuration triples: Codex CLI, Codex Desktop, Claude Code CLI, and Claude Desktop. Every packet has
exactly repetitions 1, 2, and 3, a stable identity envelope, 31 resolved metrics per
run, deterministic bundle and receipt hashes, and replay, canonical-equality, selected-loss,
and privacy receipts.

- `codex-cli`: three repetitions, 31/31 metrics per run, local aggregate 87.0/100 (unpublished)
- `codex-desktop`: three repetitions, 31/31 metrics per run, local aggregate 87.0/100 (unpublished)
- `claude-cli`: three repetitions, 31/31 metrics per run, local aggregate 81.0/100 (unpublished)
- `claude-desktop`: three repetitions, 31/31 metrics per run, local aggregate 82.8/100 (unpublished)

The score files are **private and unpublished**. They are useful for review and later
cohort assembly; this directory carries no global leaderboard rank. Independent
reproduction remains **pending** and is represented as `false` in every packet.

No raw transcript, SQLite file, absolute host path, account identifier, personal history,
credential, or private content is included. Revalidate with:

`python3 scripts/build_public_configuration_bundles.py --verify --output artifacts/survival-v1-public-configurations`
