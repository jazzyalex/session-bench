# Optional steward research pack

Status: draft invitation and response form; ready for user-selected recipients. A tailored invitation covering fx and Devin CLI was sent to their shared steward, @thedavidweng, in [Agent Sessions PR #63](https://github.com/jazzyalex/agent-sessions/pull/63#issuecomment-5626554122). This document remains the reusable pack; no live run has been requested without a prior bounded plan. This pack is optional research coordination, not a collection authorization, implementation claim, or production readiness statement.

Session-Bench currently has a constructed offline prototype. Constructed JSONL/SQLite fixtures can qualify the decoder, evaluator, mutation controls, and reporting contract. They cannot establish that a vendor writer preserves a real session. Observed-writer proof requires an authorized live run, an independently recorded trigger, a coherent native capture, and the exact build/configuration identity. The current prototype has no production suite or vendor adapter ready to claim.

See the [repository README](../../README.md), [v1 proposal](../design/2026-09-10-v1-proposal.md), [evidence and roadmap](../design/2026-09-10-evidence-and-roadmap.md), and [coverage plan](../design/2026-09-10-coverage-and-sources.md). The historical v0.4 edition remains separate and is not superseded by this pack.

## Optional invitation

> We are preparing a bounded, local Session-Bench research run for agent-session persistence. Would you like to contribute public documentation clues or an authorized synthetic session for a named surface? A response is useful even when the answer is “unknown,” “not available,” or “opt out.”
>
> The first gate is one CLI configuration running C01/C02: an ordinary conversation/correction and a deterministic inspect → failing test → edit/retry → passing test slice. The purpose is to validate measurement of preservation and loss, not to award a product pass. Broader three-configuration calibration is considered only after that gate and a separate approved plan. No broad mandatory campaign is requested.
>
> Do not send private histories, credentials, backend exports, or unrelated logs. Do not install software, purchase runs, or use paid quota without an explicit approved plan. A surface with no available session can still return the structured “missing data” form below.

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

## Evidence checklist for an explicitly approved run

The steward does not authorize a run by replying to this pack. A later run plan must name the exact subject, build, launch mode, account/profile isolation, and limits before collection starts.

### Run identity and limits

- [ ] Surface, launch mode, exact version/build, OS, model/provider, and default persistence settings recorded.
- [ ] Synthetic fixture project and harmless unique markers recorded.
- [ ] Spend cap, quota units/source, operator-time cap, wall-time cap, and stop rule recorded.
- [ ] Start and stop times, all attempts, interruptions, retries, and invalid attempts retained.
- [ ] No install, purchase, private-store access, backend/auth leakage, or scope expansion beyond the approved plan.

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

### Later C04 expansion

C04 is a later discovery/portable-bundle expansion, not a requirement of the initial one-CLI C01+C02 gate. When exercised, the native fixture must record two sessions and the copied bundle must enumerate and attribute both without original paths or backend access.

### Donation and licensing

- [ ] No credentials, private history, account identifiers, backend tokens, or unrelated machine logs are included.
- [ ] Synthetic workload, native artifacts, observer ledger, expectations, and transformation history have a redistribution/license decision.
- [ ] Any redaction that changes bytes, IDs, joins, or measured properties is documented and causes affected claims to be rerun or marked unsupported.

## Interpretation boundaries

The constructed prototype can prove evaluator behavior, such as detecting a removed event, wrong status, duplicate, dangling join, malformed record, unknown event, or missing companion. It cannot prove writer behavior. An observed writer result must remain tied to its own run, capture, and evaluation IDs.

Archive and recovery are separate qualifications. A readable or portable archive may be reported without native continuation or crash evidence. Lifecycle recovery requires C05/C06 evidence; C06 has no product-specific exemption. If the persistence process cannot be isolated, the recovery qualification remains incomplete rather than passing by exception. Process-termination evidence is not a power-loss guarantee.

This pack is a reusable draft for recipients selected by the user. It records an invitation and an evidence contract only; no invitation, live run, external contact, publication, or broad campaign has occurred.
