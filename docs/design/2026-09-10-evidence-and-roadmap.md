# Evidence contract and implementation roadmap

Status: proposed, awaiting approval for a bounded local synthetic prototype; 2026-09-10. Companion to [v1 design](2026-09-10-v1-proposal.md). Paths and commands below describe planned interfaces, not existing implementations. This roadmap does not supersede or rescore v0.4; any transition notice is deferred until an implemented v1 is publicly released.

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
| Identity | Separate live `run_id`, immutable `capture_id`, and `evaluation_id`; protocol/scenario/manifest versions; subject harness distribution, application build, surface/mode, OS, provider/model/configuration; known schema version or explicit unknown. A captured bundle may support multiple evaluations. |
| Acquisition | Origin class; native-local versus explicit-export track; capture time/window; actual launch evidence; operator/interventions; acquisition adapter and observer versions; stopping/capture procedure. |
| Artifact set | Artifact ID, root role, relative path, media type, byte length, SHA-256, writer/capture role, companion dependencies, and session join keys where known. |
| Workload | Versioned inputs and helper identity; seed; planned event classes; accepted configuration; repetition index; lifecycle barrier; attempt history. |
| Observation | Evidence source for every expected event class, blind spots, precision, sequence/causal relations, completeness of observer capture, and hashes of observer files. |
| Expectation | Assertion ID, subject/layer, expected population, matching rule, accepted canonicalization, applicability, units, and exact failure/unresolved conditions. |
| Decoder | Version/commit, dependency lock, native locator scheme, unknown-event accounting, decode diagnostics, and declared limitations. |
| Metrics | Numerator, denominator, event class, run population, unit, scope, rounding rule, and assertion references; no bare percentage. |
| Privacy and licensing | Synthetic provenance, fixture redistribution license, third-party material assessment, scan/review status, and transformation manifest if any. No credentials included. |
| Integrity | File digest inventory; external manifest digest in result/attestation; no circular self-hash requirement. Detached attestations refer to the manifest digest. |
| Reproduction | Exact offline command/environment contract, semantic result digest, acquisition recipe for live reproduction, and limitations. Recomputed and live-reproduced claims are independent attestations, each naming its subject, actor, inputs, and date; they are not levels in an evidence ladder. |

Hashes demonstrate identity, not truthful acquisition. Capture validity, native corruption, decoder correctness, and evaluator correctness are separate assertions. No attestation proves universal behavior across all configurations.

Native locators must survive recomputation: JSONL byte range/line plus record digest; SQLite captured database digest, table, stable row key and relevant column; attachment ID and digest. If a table lacks a stable key, use a captured-snapshot-specific locator and expose that limitation. Text normalization is restricted to declared protocol equivalences; original bytes always remain available. A normalized comparison event cannot exist without native source references, except explicit diagnostics about absence.

Expected assertions are authored from the workload and independent observation, then frozen before decoding. The decoder has native-only input: captured native files, declared native companions, and decoder configuration. It must not receive observer transcripts, expected tool outcomes, the scenario answer key, or facts copied into the manifest. The evaluator receives decoded output plus independent observer records and frozen expectations; it must not silently ask the decoder to supply missing facts. Runtime-derived truth such as a randomly generated assistant response can be captured independently, but the decoder may not supply it. A human-confirmed expectation requires its procedure, evidence, and reviewer identity.

The measurement-system qualification suite includes constructed and mutated fixtures. It removes a scored fact from every native source, changes an exit status, duplicates a logical event, breaks a dependency, and removes a delivered evidence bundle. The evaluator must report the corresponding loss, mismatch, duplicate, unresolved join, or invalid-evidence state. Correctly captured absence of all native records is a different valid observation. These are `constructed` or `derived_mutation` decoder/evaluator results, never live writer results. Changing the observer/expected answer while holding native inputs fixed must leave decoded output unchanged.

Comparators use field-level deterministic contracts. Event identity, role, content bytes, normalized presentation text, arguments, result bytes, exit status, timestamps, and causal/parent relationships each declare their own equivalence and uncertainty rules. Raw values remain available. Helper invocation, helper emission, harness-received result, and displayed result are distinct observation boundaries; cardinality is compared only after an explicit mapping between the observed boundary and native record population. A native tool envelope containing several helper actions is not required to decode as several native tool calls.

## Capture procedure

1. Preflight an isolated benchmark account/profile and synthetic project. Confirm exact product build and surface. Authorize only intended roots; record the root-discovery method and blind spots. If the product cannot be isolated without touching personal history, stop that candidate pending a separately approved approach.
2. Record initial inventories of authorized roots, before launching the scenario. Do not package authentication state, unrelated sessions, telemetry, or machine-wide logs. Observe all writes within declared benchmark roots; root discovery must be validated during calibration rather than assuming known roots are exhaustive.
3. Run the actual surface with independent observation and capture all attempts/interventions. Workloads use predeclared harmless content and deterministic helper effects. Built-in tools without external observation receive bounded claims.
4. For ordinary capture, quiesce the isolated writer and obtain a coherent raw bundle. For databases, retain required journal/WAL/SHM companions when present. Record how consistency was established. A logical backup may be an additional derived artifact; it does not replace physical bytes for footprint or crash analysis.
5. For crash capture, kill the identified writer at a documented event barrier and preserve the immediate post-crash files before launching recovery. Retain pre-recovery and post-recovery snapshots separately. Do not vacuum, checkpoint, repair, or reopen the only evidence copy. Use an isolated clone for reads that can create companions or trigger recovery.
6. Hash the acquired files and record any mutation between acquisition and packaging. Mark capture `invalid` only when the procedure cannot establish what was acquired, its declared roots, or its required companions. A consistently acquired but damaged or corrupt post-termination native record remains valid evidence and is evaluated as a product outcome; corruption is not a capture-invalid escape hatch.
7. Prefer public-by-construction runs to post-hoc sanitization. Review for account identifiers, unintended paths, and vendor-injected content. If redaction changes content lengths, IDs, joins, crash bytes, or another measured property, recompute only supported derived metrics or rerun cleanly; retain the transformation record. Publication permission is separate from collection permission.
8. Generate decoded/results artifacts into a different directory, validate dependency closure, and reproduce offline with the original roots unavailable. This is a required test, not an aspirational property.

Raw artifact integrity and capture consistency are separate assertions. A hash of a torn multi-file copy does not make it a coherent snapshot. Conversely a crash-produced damaged record may be valid evidence when acquired consistently after the crash.

The scored workload includes an ordinary deterministic coding slice in C02: inspect a file containing a known defect, run the deterministic test to expose its failure, perform a small edit, then correct/retry and rerun the test. The observer records actual invocations, file changes and test outcomes independently. A skipped failing attempt is unexercised, not invented. A final failing test may still be faithfully preserved. This measures the action trail, not coding performance or full working-tree backup. C03-A archive survival and C03-B native continuation are separate assertions; unsupported continuation does not block an archive result. C06 has no product-specific exemption: inability to isolate the writer/recovery boundary is a measurement limitation and leaves any qualification requiring C06 incomplete.

## Proposed implementation architecture

| Component | Responsibility | Must not do |
|---|---|---|
| Registry | Validated harness/surface/artifact identities and capabilities | Infer desktop behavior from a CLI result. |
| Workload/controller | Deterministic inputs, launch actions, barriers, attempt ledger | Modify native records to make them conform. |
| Observer | Independent evidence with declared visibility | Read the native log as the sole truth source or pass observer facts into decoding. |
| Capture adapters | Coherent authorized bundle acquisition | Scan personal stores or silently omit companions. |
| Native decoders | Loss-aware semantic extraction with locators from native-only input | Read observer transcripts, expected outcomes, or guessed model behavior to fill gaps. |
| Assertion engine | Match decoded records to frozen expectations and independent observations; compute counts and states | Run vendor clients/network calls during offline evaluation or treat decoder output as independent truth. |
| Result renderer | Atlas, scenario profiles, provenance, coverage, history | Turn missing data into a better rating or make atlas publication depend on conformance. |

Prefer Python and the current lightweight workflow initially. Use strict JSON schemas for manifests and assertions, stable serialization for outputs, and a locked dependency set for releases. Keep JSONL/SQLite helpers where their behavior is appropriate, with explicit malformed-input diagnostics and safe snapshot semantics. A new data-driven registry replaces hard-coded harness additions to scoring code. Native formats remain first-class; universal conversion is not a publication prerequisite.

Planned command contracts:

```text
session-bench validate-bundle <bundle>     # offline, nonzero on invalid evidence
session-bench decode <native-only-package> --out <dir>  # no observer/answer-key access
session-bench evaluate <bundle> --out <dir>
session-bench render <results> --out <dir>
session-bench collect <approved-run-plan>  # separately enabled live acquisition
```

These commands are design targets only. `collect` must reject a missing approved run plan, enforce declared run/spend/quota/operator-time ceilings, and retain interrupted attempts. Quota caps state concrete units (tokens, requests or provider quota where observable), measurement source and a conservative stop rule when remaining usage cannot be established. Offline commands never require live access or an expansion campaign plan.

## Phased implementation backlog

| Phase / task | Deliverable | Acceptance evidence | Dependency |
|---|---|---|---|
| D0 Bounded prototype approval | Agreed measurement contracts and local synthetic scope | User approval recorded; no full expansion access plan required; unresolved probe facts explicitly labeled | Current phase |
| O1 Historical boundary | Versioned v0.4 snapshot strategy and compatibility checks | Existing generated artifact remains byte-identical; no historical rescore | D0 |
| O2 Registry and schemas | Surface, artifact, assertion, result schemas; validation CLI | Reject unknown/missing required fields, duplicate IDs, dangling dependencies, invalid states/units, zero-denominator passes | D0 |
| O3 Offline fixtures | Constructed JSONL and SQLite+companion fixture packs | Known expected outcomes, malformed tails, missing joins, duplicate IDs, branch graphs, unknown events; origin labels enforced | O2 |
| O4 Decoder + evaluator prototype | Two format-family decoders and scenario assertion engine | Native-only decoder input; evaluator receives decoded output plus observer/expectations; source locators and no-answer-key-leakage tests; negative controls removing facts from all native sources produce correct loss/unresolved results; altered status, duplicate and pairing mutations detected | O3 |
| O5 Offline reproduction | Bundle validation, deterministic evaluation and report | Clean environment with network/original roots unavailable yields identical semantic output; corrupt hash/path escape rejected; capture-invalid and captured-corruption states remain distinct | O4 |
| L0 Live authorization | Exact one-CLI feasibility subject first, then staged target surfaces/launch modes | User-approved access, confirmed isolation, and explicit time/spend/quota/operator envelope for F0; later campaign authorization remains separate; design approval alone does not authorize live collection | O5 |
| F0 Feasibility gate | One-CLI C01+C02 end-to-end gate, including copied offline decoding and a deliberately damaged fixture | Minimum 1 valid C01 and 1 valid C02 baseline live session; copied decoding detects the deliberate loss/corruption correctly. This is measurement-system feasibility, not a product pass; derived copies add no live run | L0 |
| L1 Three-configuration calibration | Three contrasting configurations × C01/C02 × one baseline repetition = 6 live baseline sessions, plus one separately budgeted advanced-profile live attempt (7 minimum) | F0 passed as a measurement gate; authorized default configurations, actual desktop evidence, helper truth, coherent capture; advanced trigger exercised or the limitation kept unresolved | F0 plus three-configuration access/usage plan |
| L2 Protocol calibration | Freeze first evaluated edition and adjust feasibility | Resolve observer visibility, crash barriers, isolation, retention, root discovery, artifact sizes, and target launch modes; document changed tests and operator burden | L1 |
| L3 First evaluated edition | Three configurations × four C01-C04 scenarios × three repetitions = 36 live sessions | Excludes all F0/L1 runs and retries; archive/portable/continuation results separate, recovery explicitly untested; all attempts visible and no implied qualification for incomplete rows | L2 plus edition run budget |
| L4 Recovery edition | C05: three configurations × three repetitions (9), plus C06: three configurations × three conditions × three repetitions (27) = 36 additional live sessions | C05/C06 recovery is separately budgeted and reported; C06 has three conditions × three repetitions per configuration; no product-specific exemption when isolation is unavailable | L3 |
| E1 Extended qualifications | Selected branch, compaction, attachment, compatibility and efficiency evidence | Fixed assertions and repetitions; independent truth, explicit denominators and authorization; early L1 calibration does not qualify the profile | L2 and selected-profile readiness, separate run budget |
| A0 Independent atlas release | Source-grounded pages with candidate status, owner, inspection and test dates | Documentation/source/link checks, actual maintenance owner, explicit unknowns; no decoder/conformance milestone dependency; publication authorization | Atlas content ready; independent of O5/L3/L4 |
| R1 Measured report cards | Dated scenario matrix and retained-versus-decoded findings | Exact build/evaluation/native evidence; archive and continuation independent of untested recovery | L3 for first edition; L4 only for recovery cards |
| R2 Independent reproduction | Offline recomputation first; live reproduction as a separate attestation | Another reader recomputes first-edition assertions from public-ready bundles; live reproduction never overwrites that evidence | L3; external participation authorized; L4 only for recovery claims |
| R3 Evaluated release package | Protocol, evaluator, dataset, citation metadata, correction workflow | First-edition release criteria pass without C05/C06; recovery publication separately satisfies its assertions; publication authorized | R1 and independent offline R2; L4 not prerequisite for archive edition |

O1-O5 are the recommended first implementation authorization. They involve local code and synthetic fixtures, not live vendor sessions. The proposed first next authorization is this bounded local synthetic prototype. F0 is an earlier one-CLI feasibility gate that requires its own explicit access, cost, quota, and operator-time authorization after O1-O5; later live scope remains separately staged. F0's two minimum valid baseline sessions establish the earlier gate; copied offline decoding and damaged fixtures add no live run. To prove discrimination, retain an independently verified positive fact in the intact capture and remove/alter it across all native copies in the derived fixture: the intact fact must reconstruct, and the damaged fact must no longer be reported as correctly recovered. If no positive fact can be established, the gate remains incomplete; neither all-pass nor all-unresolved output is success. The product may still fail other assertions. Constructed fixtures verify software, never fill live-writer result cells.

The named candidates are Codex interactive CLI/local for F0, then Codex interactive CLI/local, Claude Desktop Code tab/Local, and Goose interactive CLI/local for L1/L3. These are proposals, not confirmed access. Before any broader campaign, name exact surfaces/launch modes/builds and default/diagnostic settings, confirm access and isolation, and approve explicit spend/quota/operator-time caps with stop rules. This expansion contract does not gate bounded prototype authorization. Retries are separately enumerated and budgeted, never silently substituted for scheduled runs.

## Collection cost and maintenance

The evaluated stages have 72 scheduled live sessions: 36 C01-C04 plus 36 C05/C06 recovery. This excludes both F0 sessions, all seven L1 calibration sessions (six baseline plus one advanced attempt), and retries/additional profiles. Thus the minimum planned path through those gates and both evaluated stages is 81 sessions before extra attempts, not 72. C06 contributes 27 of the 72. Stop at authorized caps even if a gate is incomplete. Record actual usage, billed/estimated cost with rate provenance, quota units, wall time, operator minutes and invalid-attempt fraction. No dollar estimate or access claim is invented; offline implementation needs no live budget authorization.

After the seven-session minimum calibration (six C01/C02 baseline sessions plus one advanced-profile attempt), estimate the 72-session editions as the sum of measured per-configuration costs × scheduled repetitions, plus explicitly budgeted recovery/invalid-run allowance. Publish uncertainty where desktop costs or usage are unavailable. Configuration changes and expensive compaction workloads need their own envelope. Restrict background/network activity to the synthetic workload during timed/footprint measurements, documenting unavoidable activity.

Use Luna for independent inventory, docs/source verification, fixture annotation, focused reviews, and narrow implementation tasks after approval. Primary agent owns contracts and result adjudication. Use Terra only for bounded complex decoder/controller work where needed. At most three concurrent subagents; disjoint write ownership; no recursive delegation. Keep task reports concise and reuse agents for follow-ups. Parallelism does not justify duplicate collection or conflicting edits.

Proposed accountable atlas maintainer: Alexander Malakhov, the current author named in [CITATION.cff](../../CITATION.cff); an explicitly named delegate may own an entry. Before an atlas release, record the responsible person, `source_inspected_at`, `live_tested_at` (null for untested rows), and `next_inspection_due`; never present a planned test date as a completed test. Proposed cadence is monthly source review, offline CI on changes, and live remeasurement only under an approved budget. Atlas updates are independent of conformance. Stale source checks get visible labels; a new writer version does not invalidate the identity of old evidence. Disputes reference result/assertion IDs, evidence, reviewer, disposition and any superseding evaluation; retain prior results. No scheduled automation or external assignment is created now.

Scale based on measured upkeep: per-surface adapter maintenance time, public evidence readiness, invalid-run rate, and consumer demand. New candidates first enter the atlas; verified results require the same contract as incumbents. Do not sustain a large headline agent count with unmaintained parsers.

## Release acceptance criteria

These criteria govern evaluated releases, not the independent A0 atlas publication path. The protocol can release separately from result editions. A verified failing product is eligible for publication; require honest evidence, not green products. The 36-run C01-C04 edition must prominently state partial scenario coverage and cannot claim lifecycle recovery. C05/C06 criteria apply when recovery results are published, not as a prerequisite to the first archive edition.

1. Offline installation/evaluation works from the release package with no private paths, original stores, vendor UI, or required network. Dependencies and supported runtime are pinned.
2. Every primary measured result includes a public native-live bundle, independent observation where the claim needs it, raw locators, exact build/configuration identity, all attempts, and recomputable assertions. Unresolved claims remain unresolved.
3. Each named qualification has a fixed assertion/repetition set. Missing/unsupported/invalid assertions remain visible; no all-purpose Core conformant badge, transferred surface badge or optional compensation for required loss.
4. Crash testing uses the actual writer and preserves pre-recovery evidence. Report process-crash scope and barrier policy. Constructed truncation tests are labeled decoder tests.
5. Semantic accounting includes unknown records, duplicate logical events, missing companions, content provenance, and declared lossy transformations; no silent dropping in metrics.
6. Cross-format fairness is checked with equivalent constructed JSONL and SQLite bundles; changing physical layout alone cannot change a semantic conformance result.
7. First-edition independent offline recomputation spans two artifact families and identifies the reader, evaluation and capture. Without that evidence, publish only a candidate edition after authorization. Independent live reproduction is a separate future attestation and broader maturity objective; its absence does not block an explicitly scoped independently recomputed archive edition. It never replaces recomputation.
8. Versioned protocol, evaluator, adapter and dataset identities are separate. Corrections and disputes create result-level records with superseding result IDs and explanations; cited old results remain retrievable.
9. Citation metadata identifies the exact release/dataset and archive; licensing and redistribution are reviewed for bundled native material. Hashes are verified and publication explicitly authorized.
10. Historical v0.4 regeneration remains checked independently of v1. No modification of its scores to simulate comparability with v1.

## Success measures

Track independent bundle recomputations, external live reproductions, third-party fixture/validator integrations, contributed format adapters, vendor fixes linked to evidence, published citations, and time needed to add or update a format. Traffic and stars are secondary. Set numerical adoption targets after first external pilots; do not fabricate demand or promise participation.

The distinctive contribution proposed here is a native-format corpus with independent expected reconstructions and lifecycle experiments. It complements code-provenance specifications, telemetry conventions, and workload-trace datasets. A standards claim must follow external use and governance, not precede it.
