# Codex CLI adapter status

Status: **four current calibration attempts retained as invalid/N/A; lane stopped; unscored** on
2026-09-14.

The current adapter uses `/opt/homebrew/bin/codex` `0.154.0` through a fresh,
device-authenticated `CODEX_HOME`. It starts R1 with `codex exec --ignore-user-config
--json --sandbox workspace-write --model …` and continues only by the observed native
thread ID with `codex exec resume --ignore-user-config --json --model …`. The resume
route deliberately carries neither `--sandbox` nor `-C`: the CLI inherits both from the
first session and rejects those flags on `resume`.

The evidence boundary is `CODEX_HOME/sessions`. The credential-bearing parent is neither
inventoried nor copied. A calibration may proceed only when that session root is empty,
one new JSONL rollout appears after R1, no undeclared companion appears after R2, and a
hash-verified copied package decodes without the original root. The controller then
removes every native R2 response representation in a derived copy and requires the
decoder to lose that response.

The three current attempts are visible in the
[attempt ledger](../attempt-ledger.md#current-prospective-codex-cli-calibration-attempts):
two controller defects and two matching incomplete workloads. They demonstrate that the controller
records a stopped run without converting a response canary, a native file, or an account
failure into a persistence score. None is a calibration pass or an evaluated result.

The remaining gate is a changed declared configuration or workload condition, followed by
one complete workload run with the required inspect, baseline, edit, and final helper
outcomes. It must then pass copied-root equality and selected-loss checks before Codex CLI
gets an evaluated-run slot.
