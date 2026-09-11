# Campaign-plan contract

The campaign-plan contract turns a proposed multi-surface collection into a
machine-checkable schedule and budget before any vendor process is launched. It
is a preparation artifact only. Validation performs no discovery, collection,
network request, account action, or store access.

The first checked-in plan is
[`plans/v1/l1-calibration.proposed.json`](../../plans/v1/l1-calibration.proposed.json).
It names the three proposed calibration configurations:

| Target | Surface and launch mode | Artifact track | Access/isolation |
|---|---|---|---|
| Codex interactive CLI | CLI / `interactive_local` | `native_local` candidate | blocked by the recorded F0 quota and MCP-isolation gates |
| Claude Desktop Code | desktop / `desktop_local` | `native_local` candidate | unconfirmed |
| Goose interactive CLI | CLI / `interactive_local` | documented SQLite family candidate | unconfirmed |

No row is an availability, installed-version, format, or writer-behavior result.
The plan is `proposed`, its execution authority is `none_preparation_only`, and
its completed-live-run count is zero. It cannot become
`ready_for_authorization` until every target has exact build, OS, model,
configuration, artifact, access, account/profile, isolation, root, synthetic
project, and quota-source evidence.
Readiness also requires fixture, observer, decoder, and assertion-set identities
that resolve in the repository-owned offline implementation registry; native
session join keys; and correctly shaped SHA-256 references for target identity,
access, isolation, artifact identity, and the quota observation. Plan validation
proves the references are present, not that referenced evidence exists or binds
the claims. Each quota observation
must be timezone-aware, no older than its declared freshness window when the
plan is authorized, and no later than the independently supplied authorization
time. Structural plan validation cannot prove current freshness by itself.
The closed registry is
[`registries/campaign/v1/implemented.json`](../../registries/campaign/v1/implemented.json);
each entry must resolve to an implementation file inside this repository.

## Scheduled units

The proposed calibration schedules C01 and C02 once on each target, plus one
separately scheduled branch-profile scenario run on Codex:

- 7 scheduled scenario runs;
- at most 14 attempts, because one bounded retry is charged to every scheduled
  run;
- at most 14 native sessions for the current one-session scenarios.

Scenario runs, attempts, and native sessions are separate units. The validator
derives all three totals from profiles and schedules, then requires exact declared
totals. A C04 scenario declares two native sessions per run; its test proves both
sessions are charged again for every possible retry.

The proposed hard envelope is zero incremental USD spend, at most three quota
percentage points per target once a fresh baseline and authoritative source are
available, 240 operator minutes, 480 wall-clock minutes, 80 submitted turns,
500,000 observable tokens, 2,048 captured files, 16 MiB per file, and 1 GiB in
total. These are ceilings, not estimates or authorization. Unreadable quota
before first launch stops without a live attempt; unreadable quota after launch
allows only the current attempt to finish and forbids a retry.

## Validate offline

```sh
python3 -m session_bench validate-campaign-plan \
    plans/v1/l1-calibration.proposed.json
```

A plan marked `ready_for_authorization` additionally requires an independently
supplied timestamp, for example `--as-of 2026-09-11T04:35:30Z`. The later live
controller must obtain that time independently and resolve, hash, and verify that
every referenced identity, access, isolation, artifact, and quota receipt binds
the corresponding recorded claims before launch.

The command prints the plan ID, state, target count, derived run/attempt/session
totals, and SHA-256 of canonical plan JSON. It exits with status 2 when schema,
identity, references, readiness, track separation, schedule arithmetic, quota,
retry, or forbidden-operation rules fail.

The schema lives outside `schemas/v1` at
[`schemas/campaign/v1/campaign_plan.schema.json`](../../schemas/campaign/v1/campaign_plan.schema.json).
The accepted O1-O5/F0 manifest and result schemas stay unchanged. Campaign and
atlas changes are outside the frozen evaluator dependency set, so they cannot
create a misleading new evaluation ID.

## Readiness and later execution

`ready_for_authorization` means required identities resolve, evidence digest
references have the required shape, and quota values are fresh at the supplied
authorization time. It does not validate receipt contents or grant execution
authority. A separate
explicit live authorization and suitable controller are still required. The
contract permanently forbids private-history collection, credential inspection,
purchases or credit redemption, publication, cross-repository access, and
unapproved target expansion within this plan.
