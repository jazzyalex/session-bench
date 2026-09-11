# L0 harness implementation record

Status: constructed implementation complete and hardened after implementation review; executable preflight correctly stopped before launch on the quota gate.

The bounded Codex CLI F0 harness now includes:

- versioned JSON Schemas and semantic validators for the run plan, attempt ledger, quotas, retry limits, and capture identity;
- deterministic construction of the exact Codex override argv and its digest;
- names-only MCP inventory, resolved feature/MCP checks, and an executable Codex sandbox probe;
- recursive stat-only rollout discovery that never opens or hashes pre-existing files, proves new path and filesystem identity, checks creation time where available, requires two stable quiescence observations, and refuses ambiguous new candidates;
- frozen C01/C02 observer inputs, PTY ledger freezing, hard counters, copied-package construction, and local privacy scanning;
- an explicit-package `codex-rollout-v1` decoder with no home discovery;
- frozen positive-control selection, deterministic damaged-copy support, and an intact-pass-to-damaged-loss proof under byte-identical observer and expectation files;
- bounded `native_live` validation that joins the plan, resolved configuration, scenario run, attempt, native session, capture, and ledger through semantic checks and immutable digests;
- public constructed Codex-shaped C01/C02 records under `fixtures/l0/codex-cli-0.154.0/`.

The decoder shapes were checked against OpenAI's `rust-v0.154.0` [rollout recorder tests](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/rollout/src/recorder_tests.rs) and [response-item models](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/protocol/src/models.rs) before acceptance. In particular, the implementation carries the session identifier from `session_meta.payload.id`, handles `agent_message` and `local_shell_call`, and joins tool results through `call_id` rather than assuming every event repeats a thread identifier.

The executable command has two modes:

```text
python3 -m session_bench l0-preflight --plan <plan> --scratch <path> --mcp-name <name> --dry-run
python3 -m session_bench l0-preflight --plan <plan> --scratch <path> --sibling <path> --quota-used-percent <value> --quota-observed-at <timestamp>
```

The first mode is pure plan/argv rendering. The second performs only non-model inspection and sandbox probes. It does not launch a session. A live scenario can start only after the second mode succeeds with a current quota observation.

On 2026-09-11 at `00:47:30Z`, the current weekly usage reading was 15%. The checked-in plan's baseline is 10% and its absolute stop is 13%. The executable preflight returned `stop-quota-threshold` with exit code 2 before running Codex inspection commands or launching a session. No reset or credit was redeemed. A separate non-model diagnostic of Codex's built-in `:workspace` sandbox had already demonstrated scratch-write success, undeclared sibling-write denial, and direct-network denial; it is not recorded as a complete preflight because the later quota gate prevented the full ordered run.

The implementation distinguishes a valid measured product failure from invalid evidence. The F0 gate succeeds only when an intact copied bundle reconstructs an independently observed fact and a derived damaged copy loses that same fact under unchanged observer expectations.

The controller implementation is exercised through an injected live environment so its tests cannot launch Codex. It owns the pre-launch and per-submission quota checks, attempt and turn reservations, retained ledger states, PTY observations, quiescence, capture limits, explicit copying, native decoding, and failure persistence. A fresh controller reconstructs all counters from the retained ledger before a retry. No live attempt has run: the quota gate remains above its absolute stop, and the installed Codex configuration cannot currently disable every plugin-managed MCP using the reviewed non-mutating override vector.
