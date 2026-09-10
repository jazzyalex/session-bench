# Evidence contract and implementation roadmap

Status: proposed, awaiting approval; 2026-09-10. Companion to [v1 design](2026-09-10-v1-proposal.md). Paths and commands below describe planned interfaces, not existing implementations.

## Evidence bundle contract

Every public scored result must resolve to one immutable bundle and explicit assertion records. Fixtures have an origin class: `constructed`, `native_live`, or `derived_mutation`. Only `native_live` evidence can establish observed writer behavior. Derived fixtures name the source digest and exact transformation. Synthetic describes the workload, not permission to reshape native output.

Proposed layout:

```text
bundle/
  manifest.json
  workload/                 # synthetic task, seed, helper definitions, inputs
  observer/                 # independent interaction/tool/process observations
  capture/                  # capture protocol, changed paths, barriers, diagnostics
  native/                   # original captured format and required companions
  expected/                 # independently specified assertions, not decoder output
  decoded/                  # derived comparison representation + native locators
  results/                  # counts, assertions, diagnostics, summaries
  provenance/               # environment, commands, versions, transformations
  attestations/             # separate recomputation/live reproduction statements
```

Artifacts may be represented as relative paths within a preserved root map. Never resolve them against a reproducer's home directory. Safe decoding rejects path traversal, escaping symlinks, executable payloads, and undeclared external dependencies. The default offline evaluator does not execute bundled commands or vendor binaries. Any SQLite extraction query is trusted adapter code, not executable SQL accepted from an arbitrary submission.

| Required manifest group | Required contents and invariants |
|---|---|
| Identity | Run ID; protocol/scenario/manifest versions; subject harness distribution, application build, surface/mode, OS, provider/model/configuration; known schema version or explicit unknown. |
| Acquisition | Origin class; native-local versus explicit-export track; capture time/window; actual launch evidence; operator/interventions; acquisition adapter and observer versions; stopping/capture procedure. |
| Artifact set | Artifact ID, root role, relative path, media type, byte length, SHA-256, writer/capture role, companion dependencies, and session join keys where known. |
| Workload | Versioned inputs and helper identity; seed; planned event classes; accepted configuration; repetition index; lifecycle barrier; attempt history. |
| Observation | Evidence source for every expected event class, blind spots, precision, sequence/causal relations, completeness of observer capture, and hashes of observer files. |
| Expectation | Assertion ID, subject/layer, expected population, matching rule, accepted canonicalization, applicability, units, and exact failure/unresolved conditions. |
| Decoder | Version/commit, dependency lock, native locator scheme, unknown-event accounting, decode diagnostics, and declared limitations. |
| Metrics | Numerator, denominator, event class, run population, unit, scope, rounding rule, and assertion references; no bare percentage. |
| Privacy and licensing | Synthetic provenance, fixture redistribution license, third-party material assessment, scan/review status, and transformation manifest if any. No credentials included. |
| Integrity | File digest inventory; external manifest digest in result/attestation; no circular self-hash requirement. Detached attestations refer to the manifest digest. |
| Reproduction | Exact offline command/environment contract, semantic result digest, acquisition recipe for live reproduction, trust status, and limitations. |

Hashes demonstrate identity, not truthful acquisition. Maintainer review checks acquisition evidence; external recomputation checks decoding/evaluation; independent live reproduction checks observed behavior in another environment. No level proves universal behavior across all configurations.

Native locators must survive recomputation: JSONL byte range/line plus record digest; SQLite captured database digest, table, stable row key and relevant column; attachment ID and digest. If a table lacks a stable key, use a captured-snapshot-specific locator and expose that limitation. Text normalization is restricted to declared protocol equivalences; original bytes always remain available. A normalized comparison event cannot exist without native source references, except explicit diagnostics about absence.

Expected assertions are authored from the workload and independent observation, then frozen before decoding. Runtime-derived truth such as a randomly generated assistant response can be captured independently, but the decoder may not supply it. A human-confirmed expectation requires its procedure, evidence, and reviewer identity; it is not automatically a fully automated result.

## Capture procedure

1. Preflight an isolated benchmark account/profile and synthetic project. Confirm exact product build and surface. Authorize only intended roots; record the root-discovery method and blind spots. If the product cannot be isolated without touching personal history, stop that candidate pending a separately approved approach.
2. Record initial inventories of authorized roots, before launching the scenario. Do not package authentication state, unrelated sessions, telemetry, or machine-wide logs. Observe all writes within declared benchmark roots; root discovery must be validated during calibration rather than assuming known roots are exhaustive.
3. Run the actual surface with independent observation and capture all attempts/interventions. Workloads use predeclared harmless content and deterministic helper effects. Built-in tools without external observation receive bounded claims.
4. For ordinary capture, quiesce the isolated writer and obtain a coherent raw bundle. For databases, retain required journal/WAL/SHM companions when present. Record how consistency was established. A logical backup may be an additional derived artifact; it does not replace physical bytes for footprint or crash analysis.
5. For crash capture, kill the identified writer at a documented event barrier and preserve the immediate post-crash files before launching recovery. Retain pre-recovery and post-recovery snapshots separately. Do not vacuum, checkpoint, repair, or reopen the only evidence copy. Use an isolated clone for reads that can create companions or trigger recovery.
6. Hash the acquired files and record any mutation between acquisition and packaging. If capture coherence cannot be established, invalidate the affected assertions rather than reporting an empty/small successful record.
7. Prefer public-by-construction runs to post-hoc sanitization. Review for account identifiers, unintended paths, and vendor-injected content. If redaction changes content lengths, IDs, joins, crash bytes, or another measured property, recompute only supported derived metrics or rerun cleanly; retain the transformation record. Publication permission is separate from collection permission.
8. Generate decoded/results artifacts into a different directory, validate dependency closure, and reproduce offline with the original roots unavailable. This is a required test, not an aspirational property.

Raw artifact integrity and capture consistency are separate assertions. A hash of a torn multi-file copy does not make it a coherent snapshot. Conversely a crash-produced damaged record may be valid evidence when acquired consistently after the crash.

## Proposed implementation architecture

| Component | Responsibility | Must not do |
|---|---|---|
| Registry | Validated harness/surface/artifact identities and capabilities | Infer desktop behavior from a CLI result. |
| Workload/controller | Deterministic inputs, launch actions, barriers, attempt ledger | Modify native records to make them conform. |
| Observer | Independent evidence with declared visibility | Read the native log as the sole truth source. |
| Capture adapters | Coherent authorized bundle acquisition | Scan personal stores or silently omit companions. |
| Native decoders | Loss-aware semantic extraction with locators | Fill gaps using guessed model behavior. |
| Assertion engine | Match frozen expectations, compute counts and states | Run vendor clients/network calls during offline evaluation. |
| Result renderer | Atlas, scenario profiles, provenance, coverage, history | Turn missing data into a better rating. |

Prefer Python and the current lightweight workflow initially. Use strict JSON schemas for manifests and assertions, stable serialization for outputs, and a locked dependency set for releases. Keep JSONL/SQLite helpers where their behavior is appropriate, with explicit malformed-input diagnostics and safe snapshot semantics. A new data-driven registry replaces hard-coded harness additions to scoring code. Native formats remain first-class; universal conversion is not a publication prerequisite.

Planned command contracts:

```text
session-bench validate-bundle <bundle>     # offline, nonzero on invalid evidence
session-bench decode <bundle> --out <dir>  # offline, diagnostic-rich
session-bench evaluate <bundle> --out <dir>
session-bench render <results> --out <dir>
session-bench collect <approved-run-plan>  # separately enabled live acquisition
```

These commands are design targets only. `collect` must reject a missing approved run plan, enforce the declared run/time/spend ceiling, and retain interrupted attempts. Offline commands never require it.

## Phased implementation backlog

| Phase / task | Deliverable | Acceptance evidence | Dependency |
|---|---|---|---|
| D0 Design approval | Agreed core, pilot, evidence contract, and scope | User approval recorded; unresolved probe facts explicitly labeled | Current phase |
| O1 Historical boundary | Versioned v0.4 snapshot strategy and compatibility checks | Existing generated artifact remains byte-identical; no historical rescore | D0 |
| O2 Registry and schemas | Surface, artifact, assertion, result schemas; validation CLI | Reject unknown/missing required fields, duplicate IDs, dangling dependencies, invalid states/units, zero-denominator passes | D0 |
| O3 Offline fixtures | Constructed JSONL and SQLite+companion fixture packs | Known expected outcomes, malformed tails, missing joins, duplicate IDs, branch graphs, unknown events; origin labels enforced | O2 |
| O4 Decoder + evaluator prototype | Two format-family decoders and scenario assertion engine | Decoded items resolve to raw locators; inject a lost event, duplicate, incorrect pairing, and branch error and observe correct failures | O3 |
| O5 Offline reproduction | Bundle validation, deterministic evaluation and report | Clean environment with network/original roots unavailable yields identical semantic output; corrupt hash/path escape rejected | O4 |
| L0 Live authorization | Exact products/versions/accounts, observation approach, collection cap | User-approved access/cost envelope; no assumption that design approval purchases runs | O5 |
| L1 Small calibration | Two surfaces × C01/C02 × three runs = 12 scheduled runs | Actual UI evidence where relevant, helper truth captured, coherent bundle, per-run timing/usage ledger; record invalid attempts | L0 |
| L2 Protocol calibration | Freeze first pilot protocol and adjust feasibility | Resolve observer visibility, crash acknowledgement/barriers, isolation, retention, root discovery, artifact sizes; document any changed tests | L1 |
| L3 Eight-surface pilot | Eight surfaces × (five families × three runs + three C06 conditions × three runs) = 192 scheduled runs | 48 surface/family summaries (6×8); 64 evaluated condition cells (40 C01-C05 + 24 C06); all attempts visible, no implied conformance for incomplete rows | L2 |
| E1 Optional profiles | Selected branch, compaction, attachment, compatibility and efficiency evidence | Each profile has valid triggers, independent truth where claimed, explicit denominators and authorization | L3, separate run budget |
| R1 Atlas/report | Generated surface pages and dated scenario matrix | Every claim links to source/evidence and tested build; capability/doc-only entries differ visually from measured results | O5; measured rows after L3 |
| R2 External reproducibility | Independent recomputation and live reproduction attestations | At least two independently recomputed surface bundles spanning two physical format families; at least one independently rerun CLI/Desktop pair for broad v1 launch | L3, external participation authorized |
| R3 Release package | Protocol, evaluator, immutable dataset, citation metadata, correction workflow | Release criteria below pass; publication explicitly authorized | R1/R2 |

O1-O5 are the recommended first implementation authorization. They involve local code and synthetic fixtures, not live vendor sessions. L1 chooses one existing CLI and one actual desktop surface only after confirming approved access and isolation. Its 12 runs are calibration data, not automatically counted toward L3; altered protocols require new results. Constructed fixtures verify software, never fill live-writer leaderboard cells.

The eight pilot surfaces are (1) Codex CLI, (2) Codex Desktop local, (3) Claude Code CLI, (4) Claude Desktop Code tab/Local, (5) Cursor CLI, (6) Cursor Desktop/IDE local agent, (7) Gemini CLI, and (8) Goose CLI. Use interactive CLI mode for these paired comparisons; headless mode is a separate configuration. Cowork is a later distinct surface. Implement a run-plan invariant summing declared repetitions over surface/condition cells; for this uniform plan it equals `8 × (5 × 3 + 3 × 3) = 192`. Retries are separately enumerated and budgeted, never silently substituted for scheduled runs.

## Collection cost and maintenance

The eight-surface pilot has 192 scheduled core runs, not 192 guaranteed successes. C06 has three repetitions at each of three conditions, or nine runs per surface; optional profiles and infrastructure retries are additional and must fit a declared cap. Stop rather than silently exceed it. Record actual provider usage, available billed cost, estimated cost with rate provenance, wall time, operator minutes, and invalid-attempt fraction. Do not invent dollar estimates before calibration. The protocol can be implemented without authorizing this full collection budget; the 12-run calibration is a separate spending decision.

After the 12-run calibration, estimate the pilot as the sum of per-surface measured costs × scheduled repetitions, plus explicitly budgeted recovery/invalid-run allowance. Publish uncertainty where desktop costs or usage are unavailable. Configuration changes and expensive compaction workloads need their own envelope. Restrict background/network activity to the synthetic workload during timed/footprint measurements, documenting unavoidable activity.

Use Luna for independent inventory, docs/source verification, fixture annotation, focused reviews, and narrow implementation tasks after approval. Primary agent owns contracts and result adjudication. Use Terra only for bounded complex decoder/controller work where needed. At most three concurrent subagents; disjoint write ownership; no recursive delegation. Keep task reports concise and reuse agents for follow-ups. Parallelism does not justify duplicate collection or conflicting edits.

Proposed operating cadence after launch: low-cost offline fixture CI on changes; bounded public release/documentation review monthly; paid/live remeasurement only under a standing approved budget and on meaningful changes. Mark a result historical when a newer writer is known; do not expire valid historical evidence. Update docs and live-result dates separately. New schema fingerprints create review candidates, not automatic score changes. No recurring automation is being created by this design.

Scale based on measured upkeep: per-surface adapter maintenance time, public evidence readiness, invalid-run rate, and consumer demand. New candidates first enter the atlas; verified results require the same contract as incumbents. Do not sustain a large headline agent count with unmaintained parsers.

## Release acceptance criteria

The protocol can release separately from a broad result edition. A product with a verified failing result remains eligible for publication; failures are useful findings. Do not require every product to pass, but do require complete and honest evidence for every published assertion.

1. Offline installation/evaluation works from the release package with no private paths, original stores, vendor UI, or required network. Dependencies and supported runtime are pinned.
2. Every primary measured result includes a public native-live bundle, independent observation where the claim needs it, raw locators, exact build/configuration identity, all attempts, and recomputable assertions. Unresolved claims remain unresolved.
3. Core coverage is fixed and missing/unsupported/invalid cells remain visible. No untested surface inherits another surface's badge; no optional success compensates for core loss.
4. Crash testing uses the actual writer and preserves pre-recovery evidence. Report process-crash scope and barrier policy. Constructed truncation tests are labeled decoder tests.
5. Semantic accounting includes unknown records, duplicate logical events, missing companions, content provenance, and declared lossy transformations; no silent dropping in metrics.
6. Cross-format fairness is checked with equivalent constructed JSONL and SQLite bundles; changing physical layout alone cannot change a semantic conformance result.
7. Independent recomputation spans two format families; independent live reproduction of at least one CLI/Desktop pair supports the broad v1 result launch. If that participation is unavailable, publish only a clearly labeled candidate edition after authorization; do not claim the milestone achieved.
8. Versioned protocol, evaluator, adapter and dataset identities are separate. Corrections create superseding result IDs and explanations; cited old results remain retrievable.
9. Citation metadata identifies the exact release/dataset and archive; licensing and redistribution are reviewed for bundled native material. Hashes are verified and publication explicitly authorized.
10. Historical v0.4 regeneration remains checked independently of v1. No modification of its scores to simulate comparability with v1.

## Success measures

Track independent bundle recomputations, external live reproductions, third-party fixture/validator integrations, contributed format adapters, vendor fixes linked to evidence, published citations, and time needed to add or update a format. Traffic and stars are secondary. Set numerical adoption targets after first external pilots; do not fabricate demand or promise participation.

The distinctive contribution proposed here is a native-format corpus with independent expected reconstructions and lifecycle experiments. It complements code-provenance specifications, telemetry conventions, and workload-trace datasets. A standards claim must follow external use and governance, not precede it.
