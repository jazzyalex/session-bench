# survival-v1 workload fixture

This directory is the frozen constructed workload for Session-Bench survival-v1
Phase 1. It contains no vendor output, account data, credentials, or personal
history.

* `workload.json` is the task, action, relation, helper, and file-boundary contract.
* `response-canaries.json` is the one canary set shared by all five target surfaces.
* `filesystem.json` binds the defective and corrected `checkout.py` snapshots.
* `observer-truth.json` is an independent positive observer fixture.
* `workload.schema.json` and `observer.schema.json` are closed schemas for those
  two machine-readable contracts.
* `fixture_project/bench_check.py` is deterministic and appends a generated
  `.survival-observer.jsonl` ledger when run. The generated ledger is intentionally
  absent from version control.

For a local control run, copy `fixture_project/` to a fresh temporary directory,
run `python3 bench_check.py inspect`, then `baseline`, replace `checkout.py` with
`snapshots/checkout.after.py`, and run `final`. Expected exits are `0`, `1`, and
`0`. Do not run the helper in a personal project or point it at an existing store.

The checked-in fixture is a constructed decoder/evaluator control. Its observer
truth cannot establish that any vendor surface writes the same events.
