# Evidence: current limits and the path to receipts

Every verdict in `data/verdicts.yml` carries an evidence line: a
measurement, a vendor document, a fixture in the
[agent-sessions](https://github.com/jazzyalex/agent-sessions) repo, or an
entry in its format-drift monitoring ledger
(`docs/agent-support/` there, fingerprinting each format roughly weekly
since it entered monitoring).

## What is public today

- The measured inputs (`data/measurements.json`) and every evidence line.
- The evaluator and tests — the scoring is fully reproducible from the
  versioned inputs, byte-for-byte.
- One pinned receipt with full SHA-256 hashes:
  [receipts-2026-08-12-codex-c6c7.md](receipts-2026-08-12-codex-c6c7.md),
  with its executable query beside it:
  [receipt_codex_c6c7.py](receipt_codex_c6c7.py).

## What is not public yet

- Raw probe artifacts (session files) — they are personal local session
  data; snapshots matching the receipt hashes are retained privately.
- Corpus query transcripts behind the corpus statistics (e.g. per-store
  content-share numbers).

An observation is therefore documented — identity-pinned and
query-published — but not yet independently reproducible end to end. **v1.0** closes that gap:
sanitized immutable probe fixtures, archived extraction outputs tied to
hashes, and per-event classifiers. Until then, this benchmark describes
itself as: scores mechanically generated from published measurements and
checklist verdicts, with some underlying raw artifacts not yet public.
