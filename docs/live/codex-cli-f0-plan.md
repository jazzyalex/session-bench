# Codex interactive CLI F0 run plan

Status: harness implemented; the first executable preflight stopped before launch because the observed weekly usage was 15%, above the predeclared 13% absolute stop. The plan was READY for harness implementation after a Sol Extra High recheck of commit `59e6a753401f1f0fdb4db252fd02007bbd7649b1`. The first F0 live launch may proceed automatically only after the constructed adapter/controller tests and the specified preflight pass. This plan is limited to the one-CLI C01+C02 feasibility gate. It does not authorize L1 expansion, publication, purchases, private-history inspection, or reuse of unrelated sessions.

## Subject and access

| Field | Planned value | Evidence/status |
|---|---|---|
| Harness | Codex CLI | `/opt/homebrew/bin/codex` |
| Installed build | `codex-cli 0.154.0` | `codex --version`, checked 2026-09-10 |
| Surface / launch mode | CLI / interactive local TUI | The controller supplies the bounded override set below, followed by `--no-alt-screen -C <scratch> --sandbox workspace-write --ask-for-approval never` |
| Authentication | Existing ChatGPT login | `codex login status`; no credential contents inspected or copied |
| Configuration | Default model with a measured, bounded tool/context configuration | Model/provider are recorded from the new session evidence; no model override |
| OS | macOS 15.7.9 | Existing O1-O5 acceptance environment |
| Persistence family | Codex dated rollout JSONL under the Codex session root | Discovery shape is `*/sessions/YYYY/MM/DD/rollout-*.jsonl`; exact new path must be established by metadata-only before/after inventory |
| Current account envelope | 10% of the observable weekly Codex window used; no paid credits | Read-only Codex usage query on 2026-09-10; do not redeem resets |

The operator may collect relative path, filesystem identity, birth/creation time where available, ctime, mtime, and size under the expected dated session directory before and after each run solely to identify newly created files. This inventory is stat-only: it must never open or hash a pre-existing file. Only after exactly one candidate has been proven newly created by the current attempt may the controller open, hash, and copy it. Apply the same rule to companions. If the candidate set is ambiguous, stop without opening any candidate and retain the attempt as invalid.

## Effective configuration preflight

Omission does not disable inherited capabilities. Before every launch, the controller builds one explicit override vector and records its canonical SHA-256. It leaves the model unset, while setting:

```text
-c web_search="disabled"
-c sandbox_workspace_write.network_access=false
-c sandbox_workspace_write.writable_roots=[]
-c hooks={}
-c project_doc_max_bytes=0
--disable apps --disable browser_use --disable browser_use_external
--disable computer_use --disable hooks --disable image_generation
--disable in_app_browser --disable memories --disable multi_agent
--disable plugins --disable skill_search --disable tool_suggest
--disable workspace_dependencies --enable skip_host_skill_discovery
```

The current CLI merges `mcp_servers` tables, so `-c mcp_servers={}` is insufficient. The controller runs `codex mcp list --json`, keeps only server names and enabled states, and discards transport, URL, environment, and authentication fields. For every configured name it appends a separately argument-encoded `-c mcp_servers.<name>.enabled=false` override; names are validated against the CLI's accepted dotted-key component grammar before use. It then runs `codex features list` and `codex mcp list --json` with the complete vector. Every named feature above must resolve to the requested state and every MCP server must resolve disabled. Unknown names, parse failures, enabled external tools, or changed output schemas stop before launch.

The controller also probes the workspace-write sandbox before launch: the scratch root must be writable, an undeclared sibling must be denied, and outbound network must be denied. It records only pass/fail, command identity, exit status, and timestamps. C01 and C02 must use the same override fingerprint and resolved feature/MCP/sandbox state. The native session must record one consistent model/provider identity; any mid-gate model, provider, endpoint, or effective-configuration change stops F0 incomplete.

## Hard limits

| Resource | Limit |
|---|---:|
| Scheduled scenario runs | 2: one C01 and one C02 |
| Attempts | 4 total; at most 2 per scenario |
| Native sessions | 4 total, counted only when actually created |
| Submitted user turns | 8 total |
| Observable aggregate tokens | 100,000 input plus output |
| Incremental spend / purchases | USD 0; no credits, resets, upgrades, API keys, or paid fallback |
| Weekly account consumption | Baseline 10% used at `2026-09-11T00:00:00Z`; absolute stop at 13% used |
| Operator time | 30 active minutes |
| Wall clock | 90 minutes from first launch |
| Captured evidence | 256 files, 16 MiB each, 256 MiB total |
| Decoded records | 100,000 total |

Stop at the first applicable limit. The controller checks the weekly value before every submission and retry. If it is unreadable before the first launch, stop without consuming a live attempt. If it becomes unreadable after launch, record it as unknown and allow no retry; the current attempt may finish under the submitted-turn, wall-clock, token-when-observable, and zero-purchase limits. One retry may address a clearly corrected harness/capture defect while all preflight data remain readable. Repeated auth, isolation, discovery, or capture failure ends F0 incomplete. Wall clock begins immediately before the first CLI launch; active operator time includes setup, observation, capture, adjudication, and retry work from that point.

## Isolation and capture

1. Create a fresh temporary Git repository containing only the synthetic files below. Record hashes and a recursive metadata inventory before launch.
2. Use the existing authenticated Codex installation without reading its configuration or credentials. Apply and verify the complete effective-configuration preflight above. Grant only workspace-write access to the scratch repository; do not add directories.
3. Record the PTY stream independently, including submitted input, displayed output, timestamps, and terminal exit. The observation ledger is frozen before native decoding.
4. Use metadata-only before/after inventory to identify the single new dated rollout file. Do not use `--last`, a session picker, broad content search, or any pre-existing transcript.
5. Exit the CLI cleanly, wait for the new file to quiesce, hash and copy only that new file plus companions that share its new session identity. Never reopen, repair, truncate, or migrate the native source.
6. Decode and evaluate a copied bundle with network, the original session path, observer ledger, and expectations unavailable to the decoder. The evaluator receives the frozen observer and assertions separately.
7. Select a positive control from the frozen candidate order below, make a second copy, remove or alter every native representation of that selected fact through an adapter-defined transformation, and bind the derived bundle to the intact capture digest. The original and the observer/expectation data remain immutable.

The identity chain is `gate_id -> scenario_run_id -> attempt_id -> native_session_id -> capture_id -> evaluation_id`. Every invalid or interrupted attempt remains recorded. Scenario runs, attempts, and native sessions are reported separately; a damaged copy adds no live run or native session.

## Synthetic project and scripts

The scratch repository contains:

```text
fixture_project/target.py
fixture_project/test_target.py
fixture_project/README.txt
```

`target.py` initially returns `1`. `test_target.py` imports `answer()` and exits with `synthetic regression failure: expected 2, got 1` unless the answer is `2`. `README.txt` contains the unique public marker `SB_F0_<run_id>_cafe_accent_é_🙂`. The controller records all file bytes and hashes before the run.

### C01 — accepted conversation and correction

Submit three turns in one fresh session:

1. `Remember this marker exactly: SB_F0_<run_id>_C01_café_🙂. Reply with the marker on its own line, then say READY.`
2. `Correction: preserve the marker's accents and emoji exactly; reply with the marker on its own line, then say CORRECTED.`
3. `Now repeat the marker exactly once and say DONE.`

Score accepted prompt bytes, roles, order, and visible final blocks. Do not predeclare assistant prose beyond the requested marker and status words.

### C02 — inspect, failure, edit, retry

Submit one turn in a separate fresh session:

`Open fixture_project/target.py, inspect it, run the deterministic test with: python3 fixture_project/test_target.py, make the smallest edit so it passes, then run the same test again. Do not change test_target.py or README.txt. Report the observed failure and final pass.`

The independent controller records the inspect, failing test, edit, and rerun boundaries; exact arguments; relative targets; exit codes/output; and before/after hashes. The expected project transition is `target.py: return 1 -> return 2`, first test exit `1`, second test exit `0`, with no changes to the test or marker file. Skipped steps remain unexercised. A wrong agent result is a measured product failure, not invalid evidence.

### Positive-control selection

Freeze this candidate order before decoding:

1. C02 failing-test status and exit code;
2. C02 inspect target and returned source bytes;
3. C02 accepted prompt bytes;
4. C01 correction prompt bytes;
5. C01 first prompt marker bytes.

Choose the first candidate that was independently observed, exists in the intact native capture, and was correctly reconstructed by the decoder. If none qualifies, F0 is incomplete. The damage receipt names the selected assertion, all native locations changed or removed, source manifest digest, transformation, and derived digest. The derived evaluation must use the unchanged frozen observations and expectations.

## Gate decision

F0 succeeds as a measurement-system gate only if both baseline scenario runs produce valid, coherent native captures; the intact copy reconstructs at least one independently observed positive fact; offline evaluation cannot access original roots or answer keys; and the damaged copy no longer reports the removed or altered fact as correctly recovered. The Codex product may pass or fail other assertions.

F0 is incomplete if authentication, observation, discovery, quiescence, or isolation cannot be established. It fails if the decoder reads observer/expectation data, the original capture is mutated, an unexplained native session is mixed into the bundle, or the damaged fact still passes. F0 establishes no desktop, IDE, C03-C06, recovery, or publication claim.

## Implementation before execution

The constructed implementation now admits bounded `native_live` evidence and includes the Codex adapter and capture-controller seams. Before launching a live session, the checked-in implementation and tests must continue to provide:

- a versioned live run-plan schema and per-attempt ledger;
- a Codex rollout JSONL decoder that accepts an explicit copied package and never discovers the user's home;
- frozen C01/C02 assertion templates and PTY observer import;
- metadata-only new-file identification and copy logic that refuses ambiguity;
- native-live bundle validation, privacy scanning for the public synthetic marker set, and deterministic damage transformation;
- dry-run and constructed Codex-shaped fixtures for all controller/adapter paths.

Implementation tests use constructed records only. The first actual Codex launch is the start of F0 and consumes the limits above. See [the implementation record](l0-harness-implementation.md).
