# Optional steward research pack

Status: response-only; capture recipe pending. A tailored invitation covering fx and Devin CLI was sent to their shared steward, @thedavidweng, in [Agent Sessions PR #63](https://github.com/jazzyalex/agent-sessions/pull/63#issuecomment-5626554122). This document remains the reusable pack; no live run has been requested without a prior bounded plan. It supports willingness, access, and public-documentation replies; it is not an executable vendor adapter or capture recipe. This pack is optional research coordination, not a collection authorization, implementation claim, or production readiness statement.

Session-Bench currently has a constructed offline prototype. Constructed JSONL/SQLite fixtures can qualify the decoder, evaluator, mutation controls, and reporting contract. They cannot establish that a vendor writer preserves a real session. Observed-writer proof requires an authorized live run, an independently recorded trigger, a coherent native capture, and the exact build/configuration identity. The current prototype has no production suite or vendor adapter ready to claim.

See the [repository README](../../README.md), [v1 proposal](../design/2026-09-10-v1-proposal.md), [evidence and roadmap](../design/2026-09-10-evidence-and-roadmap.md), and [coverage plan](../design/2026-09-10-coverage-and-sources.md). The historical v0.4 edition remains separate and is not superseded by this pack.

## Optional invitation

> We are preparing a bounded, local Session-Bench research run for agent-session persistence. Would you like to contribute public documentation clues or an authorized synthetic session for a named surface? A response is useful even when the answer is “unknown,” “not available,” or “opt out.”
>
> The first gate is one CLI configuration running C01/C02: an ordinary conversation/correction and a deterministic inspect → failing test → edit/retry → passing test slice. The purpose is to validate measurement of preservation and loss, not to award a product pass. Broader three-configuration calibration is considered only after that gate and a separate approved plan. No broad mandatory campaign is requested.
>
> Do not send private histories, credentials, backend exports, or unrelated logs. Do not install software, purchase runs, or use paid quota without an explicit approved plan. A surface with no available session can still return the structured “missing data” form below.

## Candidate targets

The request covers two separate candidate targets. Product identity, build, launch mode, persistence root, and access are unknown until the steward responds; this table makes no availability or measurement claim.

| Target | Candidate identity | Candidate launch mode | Response should confirm |
|---|---|---|---|
| FX | `fx` (vercel-labs) | CLI; exact mode unknown | exact package/build, command, persistence root and format clues, and whether a harmless synthetic run is locally accessible |
| Devin | Devin CLI | CLI; exact mode unknown | exact build, command or documented launch path, persistence/export boundary, and whether a harmless synthetic run is locally accessible |

These targets remain separate result identities. Information about one cannot establish availability, format, or behavior for the other.

## Phase-one response form

Copy this section into a response and fill only what is known.

```text
recipient_label:
status: available | partial | missing_data | opt_out
harness:
surface:
launch_mode: interactive_cli | one_shot_cli | desktop | ide | other | unknown
version_or_build:
os_and_version:
access_path: local | documented_local | unavailable | unknown
public_docs:
  urls:
  format_clues:             # JSONL, SQLite, sidecars, export, unknown, etc.
  documented_roots:
  resume_or_archive_notes:
default_configuration_known: yes | no | unknown
diagnostic_configuration_available: yes | no | unknown
willing_to_contribute_observed_synthetic_run: yes | no | later | unknown
notes_or_questions:
```

A `missing_data` or `opt_out` response is complete and requires no follow-up. Public documentation may be contributed without granting access to a live session.

### Non-mandatory return route

Return the completed form, a short “unknown,” `missing_data`, or `opt_out` reply in the public thread where the invitation was received: [Agent Sessions PR #63 comment](https://github.com/jazzyalex/agent-sessions/pull/63#issuecomment-5626554122). A reply is optional, and “not now” is complete. Do not attach raw session files, credentials, private histories, or machine logs to that thread. If a different route is agreed with the steward, record it in the later run plan before any collection.

## Evidence checklist for an explicitly approved run

The steward does not authorize a run by replying to this pack. A later run plan must name the exact subject, build, launch mode, account/profile isolation, and limits before collection starts.

### Run identity and limits

- [ ] Surface, launch mode, exact version/build, OS, model/provider, and default persistence settings recorded.
- [ ] Synthetic fixture project and harmless unique markers recorded.
- [ ] Spend cap, quota units/source, operator-time cap, wall-time cap, and stop rule recorded.
- [ ] Start and stop times, all attempts, interruptions, retries, and invalid attempts retained.
- [ ] No install, purchase, private-store access, backend/auth leakage, or scope expansion beyond the approved plan.

### Identity ledger and immutable receipt

The later run plan must use separate identities for each layer:

```text
research_run_id -> scenario_run_id -> attempt_id -> native_session_id
                  -> capture_id -> evaluation_id
```

- [ ] One ledger row exists for every scenario run and every attempt, including failed, interrupted, cancelled, ambiguous, and retried attempts; an invalid attempt is retained rather than replaced.
- [ ] Each native session, capture, and evaluation is linked to exactly the applicable attempt, while retries receive distinct attempt IDs and do not become extra scenario runs unless the plan says so.
- [ ] The retained receipt is an append-only, read-only record of the run plan, resolved configuration, observation ledger, expectations, capture procedure, tool/build identity, timestamps, and every delivered artifact.
- [ ] The receipt lists each artifact’s relative path, type, size, SHA-256, companion relationship, source-versus-derived status, and the IDs and digests that bind it to the identity chain above.
- [ ] Native source bytes are preserved before decoding or transformation; any deliberately damaged copy has a separate transformation receipt that binds it to the intact capture and leaves observations/expectations unchanged.

Hashes establish artifact identity but do not independently prove honest acquisition. Corrections must append a new receipt and link the corrected evaluation to the original; they must not rewrite the original evidence.

### Required first-gate scenarios

- [ ] C01: three accepted turns including a correction; accepted user text and visible assistant blocks captured independently.
- [ ] C02: inspect a known defect; run the deterministic failing test; perform an edit; retry/rerun to a passing result when the operation occurs.
- [ ] C02 actions identify inspect/edit/test, project-relative target, arguments, outcomes, and relevant before/after file digests.
- [ ] All performed operations and failures are retained; skipped operations are marked unexercised rather than invented.

### Capture and independence

- [ ] Independent observations are frozen before decoding: accepted prompts, visible response blocks, helper boundaries, file observations, test outcomes, and lifecycle events.
- [ ] Native capture is coherent and includes every declared companion, including WAL/journal/SHM where present.
- [ ] Native files are captured without modifying, repairing, vacuuming, reopening, or silently omitting them.
- [ ] Decoder input is native-only; observer records and expected answers enter only the evaluator.
- [ ] Default configuration is distinguished from enhanced logging/diagnostic settings.
- [ ] Artifact paths, hashes, IDs, and capture procedure are recorded; public-by-construction synthetic data is preferred.

### Pre-share privacy gate

- [ ] The outgoing directory and every declared companion have been inventoried, including attachments, SQLite WAL/journal/SHM files, sidecars, and metadata; no undeclared file or symlink is included.
- [ ] The disposable fixture contains only purpose-made synthetic content. A review found no credentials, tokens, account identifiers, private prompts/history, environment or shell logs, hostnames, absolute personal paths, unrelated files, or backend responses.
- [ ] The contributor recorded the exact files and digests intended for sharing and obtained explicit permission for those files; raw files are not pasted into the public request thread.
- [ ] Any redaction or export transformation is recorded before sharing and is treated as a new derived artifact whose affected claims must be rerun or marked unsupported.
- [ ] If the local prototype privacy scanner is used, its command/version and findings are recorded. The scanner is a limited heuristic (it checks a small marker set), so a clean result is not proof that private data is absent.

### Later C04 expansion

C04 is a later discovery/portable-bundle expansion, not a requirement of the initial one-CLI C01+C02 gate. When exercised, the native fixture must record two sessions and the copied bundle must enumerate and attribute both without original paths or backend access.

### Donation and licensing

- [ ] No credentials, private history, account identifiers, backend tokens, or unrelated machine logs are included.
- [ ] Synthetic workload, native artifacts, observer ledger, expectations, and transformation history have a redistribution/license decision.
- [ ] Any redaction that changes bytes, IDs, joins, or measured properties is documented and causes affected claims to be rerun or marked unsupported.

## Interpretation boundaries

The constructed prototype can prove evaluator behavior, such as detecting a removed event, wrong status, duplicate, dangling join, malformed record, unknown event, or missing companion. It cannot prove writer behavior. An observed writer result must remain tied to its own run, capture, and evaluation IDs.

Archive and recovery are separate qualifications. A readable or portable archive may be reported without native continuation or crash evidence. Lifecycle recovery requires C05/C06 evidence; C06 has no product-specific exemption. If the persistence process cannot be isolated, the recovery qualification remains incomplete rather than passing by exception. Process-termination evidence is not a power-loss guarantee.

This pack is a reusable draft for recipients selected by the user. It records an invitation and an evidence contract only; other than the linked willingness/access invitation, no live run, collection, publication, or broad campaign has occurred.
