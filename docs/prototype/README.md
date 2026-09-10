# Bounded offline prototype (O1–O5)

Implementation authorized on 2026-09-10. This is a measurement-system prototype, not a v1 benchmark edition or a vendor result. v0.4 remains unchanged and current. No live acquisition, personal stores, purchases, or benchmark release is authorized by this milestone. The user separately authorized committing/pushing this prototype for Oracle Sol Pro review.

Local acceptance: **104 tests passed**, including all 24 historical tests; **ten copied-bundle cases** reproduced identically. See [acceptance metadata](acceptance.json). Oracle Sol Pro review is pending.

The prototype validates evidence, decodes two **constructed** storage families, evaluates independently specified assertions, and renders a report with field-level findings and native locators. Both families encode the same synthetic work. They are intentionally named `constructed-jsonl-v1` and `constructed-sqlite-v1`; neither is an adapter for Codex, Claude, Goose, or another product.

## Run locally

Run from the repository root. The prototype runtime uses only the Python standard library. The verified runtime is recorded in the [reproduction receipt](evidence-attachment-fix/reproduction-receipt.json); legacy tests still use the existing `requirements.txt` dependencies. No installation or network is required for these commands.

```sh
python3 scripts/session-bench make-fixture --format constructed-jsonl-v1 --out /tmp/sb-example
python3 scripts/session-bench validate-bundle /tmp/sb-example
python3 scripts/session-bench decode /tmp/sb-example/native --out /tmp/sb-decoded
python3 scripts/session-bench evaluate /tmp/sb-example --out /tmp/sb-evaluated
python3 scripts/session-bench render /tmp/sb-evaluated/results.json --out /tmp/sb-rendered
python3 scripts/session-bench validate-registry registry/prototype.json
python3 -m session_bench.reproduce --out /tmp/sb-reproduction
python3 -m pytest tests/ -q
```

Choose unused output directories. Commands refuse to overwrite existing evaluations or write results inside evidence inputs. `collect` always rejects the request: no live controller is implemented. CLI validation errors exit 2; a valid evaluation containing measured failures exits 0, while invalid capture exits 2. A missing/tampered delivered bundle is rejected before scoring. Its absence is not a zero-event pass.

`decode`, `evaluate`, and the reproduction command require the tested macOS `sandbox-exec` backend. Other platforms fail closed; the pure decoder/evaluator library tests can run elsewhere, but do not establish OS isolation there. Python 3.14.7 is the verified interpreter, not a claim of compatibility with every Python release. No third-party prototype runtime dependencies need a lockfile.

## Implemented contract and evidence

| Gate | Implementation | Acceptance evidence |
|---|---|---|
| O1 Historical boundary | SHA-256 receipt for historical source/data/evidence and a separate generator comparison | [Preservation receipt](../prototype-history.json), `tests/test_v1_history.py`; old leaderboard byte equality |
| O2 Registry and schemas | Closed JSON schemas and dependency-free validator for the explicitly used subset; distinct surface, artifact, assertion, result, run/capture/evaluation identities | `schemas/v1/`, `registry/prototype.json`, `tests/test_v1_validation.py` |
| O3 Constructed fixtures | Equivalent JSONL and SQLite + attachment packs; executable known-defect workload with before/after snapshots, fail/edit/pass test; two sessions, three accepted turns, explicit tool/helper mappings, branches | `session_bench/fixtures.py`, `tests/test_v1_fixtures.py`, [fixture packs](../../fixtures/v1/README.md) |
| O4 Decoder/evaluator | Native-only process; strict field comparisons, provenance locators, field outcomes, declared assertion denominators; native omission separated from reader omission | `tests/test_v1_evaluation.py`, decoder/fixture/validation tests; positive and derived negative controls |
| O5 Offline reproduction | Copied source and copied bundles, Python `-I -S`, OS denial probes, semantic-output equality | [Receipt](evidence-attachment-fix/reproduction-receipt.json), `tests/test_v1_offline.py` |

The source launcher and `python3 -m session_bench` expose the same commands. A result’s `evaluation_id` binds its manifest, decoded output, evaluator result, and an implementation digest covering Python modules and schemas. Results retain the implementation and decoded digests separately. The manifest inventory binds observations and frozen assertions; a new evaluation never silently changes an old output directory.

Comparators preserve original field values. `text_lf` only equates CRLF and LF. It does not trim code, normalize Unicode, flatten whitespace, or repair terminal wrapping. `json` compares object values without key-order sensitivity while preserving array order, scalar types, and missing versus null. `presentation_unknown` remains unresolved. Full assertion recovery requires all its scored fields, not merely a matching ID. Current aggregates count **assertions**, not helpers, events, sessions, or benchmark scenario runs; field results remain individually available. Missing, unsupported, and unexercised assertions remain in declared denominators.

Native tool envelopes and externally specified helper invocations are distinct populations. The constructed correspondence explicitly maps two helper observations to one envelope. An emitted result is never promoted to received/displayed status by the evaluator. The workload files are trusted synthetic test assets; the decoder/evaluator never executes bundled workload commands. Running the synthetic regression test is a separate acceptance test, not evidence that a vendor performed the work.

JSONL locators bind file digest, line/byte position, and raw record digest. SQLite locators bind database digest, table, row key, payload digest, and physical companion digests. A dedicated test creates committed **WAL-only rows**, captures physical files before closing the writer, demonstrates their absence from the main file alone, recovers them from the copied bundle, and verifies source hashes are unchanged. SQLite reads occur on a disposable copy using a fixed query and closed schema; no submitted SQL or extension runs.

## Controls and interpretation

- Intact facts reconstruct; removal from all native representations does not get repaired from observer data.
- Changed status yields a contradicted field and failed assertion. A located fact omitted by an injected faulty decoder yields `retained_decoder_incomplete`, rather than a writer-loss claim.
- Duplicate logical records fail; dangling tool/branch references and cyclic lineage remain unresolved.
- Attachment references are checked against the delivered companion digest and size, including same-size payload changes.
- Unknown records and malformed payloads are accounted for explicitly. Correctly inventoried corrupt data can remain valid capture evidence with unresolved reconstruction.
- Undelivered companions, missing bundles, digest mismatch, path escape, undeclared files, and symlinks are evidence errors. Correctly represented empty native capture is valid evidence and does not pass absent assertions automatically.
- Observer/answer-key changes leave native decoding byte-identical. OS tests also deny answer-key file reads and network access. The decoder worker receives only staged `__init__.py`/`decoders.py` and native artifacts; fixture-generation source is excluded because it contains constructed answers.

Independent inspection metadata is a supplied evidence claim, not an oracle. A constructed absence control includes a transformation receipt and baseline manifest digest; arbitrary real-world absence still needs an independently reviewed coverage/inspection procedure. Hashes establish identity, not honest acquisition.

## Reproduction boundaries and remaining work

The [initial eight-case receipt](evidence/reproduction-receipt.json) is retained unchanged; the current ten-case receipt adds the attachment-payload negative control and a new implementation identity.

The recorded reproduction is a fresh local process using copied source and evidence, with no site packages and denied access to original source/evidence paths and network. It uses the same host OS/interpreter. It is **not** an independent person's reproduction, a second operating-system validation, or a live-writer attestation. The macOS backend runs decoder and evaluator as separate sandboxed stages because nested sandbox initialization is not supported by this execution path. Each case records denial probes and equality of the entire canonical result, including locators and evaluation identity.

Prototype limits are 256 artifacts, 16 MiB per file, and 100,000 decoded records. The isolated decoder also has CPU/file-descriptor/file-size and wall-time bounds. The implementation is a bounded local fixture tool, not a hardened multi-tenant submission service. The allowlist includes system/interpreter libraries; native bytes are never treated as executable instructions. Inputs must remain quiescent while an evaluation runs. Secure no-follow staging and repeated integrity validation protect the normal evaluation path, but there is no claim of protection against a malicious process controlling the same user account.

Not implemented or qualified: vendor adapters; native-live manifests; a real observer/capture/controller; UI/terminal presentation emulation; native continuation; cancellation/crash collection; compaction/subagent/attachment lifecycle qualifications; frozen production repetition sets; Linux/Windows OS isolation; independent-person recomputation; atlas or result publication. Constructed attachment bytes and branch fixtures test decoder mechanics only. The current schemas are a strict prototype contract; live acquisition metadata and production profiles require an explicitly versioned extension.

Stop before **L0/F0**. The next live gate still needs a named CLI/configuration, isolated access plan, and spend/quota/operator caps. Before collecting, extend the offline prototype with the actual authorized adapter and observation/capture contract. Scenario runs, attempts, and native sessions must be budgeted separately: C04 creates two native sessions per scenario run. Nothing here authorizes a live campaign or changes v0.4’s status.
