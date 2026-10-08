# Reproduce this candidate

Obtain the independent review hashes from a trusted channel, then verify them against replay-index.json. Do not treat a hash shipped beside its own data as independent approval.

The release manifest hashes every shipped file. Each packet manifest separately pins its complete native, observer, workload, and scoring-source inventory.

For each packet, verify its manifest SHA-256 against the independent review, inspect the bundled source, then run from this directory:

```sh
python3 -I -B evidence/claude-cli/claude-cli-eval-1/runtime/scripts/replay_score_package.py evidence/claude-cli/claude-cli-eval-1
python3 -I -B evidence/claude-cli/claude-cli-eval-2/runtime/scripts/replay_score_package.py evidence/claude-cli/claude-cli-eval-2
python3 -I -B evidence/claude-cli/claude-cli-eval-3/runtime/scripts/replay_score_package.py evidence/claude-cli/claude-cli-eval-3
python3 -I -B evidence/opencode-cli/opencode-1-18-31-eval-1/runtime/scripts/replay_score_package.py evidence/opencode-cli/opencode-1-18-31-eval-1
python3 -I -B evidence/opencode-cli/opencode-1-18-31-eval-2/runtime/scripts/replay_score_package.py evidence/opencode-cli/opencode-1-18-31-eval-2
python3 -I -B evidence/opencode-cli/opencode-1-18-31-eval-3/runtime/scripts/replay_score_package.py evidence/opencode-cli/opencode-1-18-31-eval-3
python3 -I -B evidence/deepseek-harness-cli/dsh-cal-20260929-2/runtime/scripts/replay_score_package.py evidence/deepseek-harness-cli/dsh-cal-20260929-2
python3 -I -B evidence/deepseek-harness-cli/dsh-eval-20260929-1/runtime/scripts/replay_score_package.py evidence/deepseek-harness-cli/dsh-eval-20260929-1
python3 -I -B evidence/deepseek-harness-cli/dsh-eval-20260929-2/runtime/scripts/replay_score_package.py evidence/deepseek-harness-cli/dsh-eval-20260929-2
python3 -I -B evidence/pi/pi-openai-codex-2026-10-02-10/runtime/scripts/replay_score_package.py evidence/pi/pi-openai-codex-2026-10-02-10
python3 -I -B evidence/pi/pi-openai-codex-2026-10-02-11/runtime/scripts/replay_score_package.py evidence/pi/pi-openai-codex-2026-10-02-11
python3 -I -B evidence/pi/pi-openai-codex-2026-10-02-12/runtime/scripts/replay_score_package.py evidence/pi/pi-openai-codex-2026-10-02-12
python3 -I -B evidence/codex-cli/codex-cli-eval-1/runtime/scripts/replay_score_package.py evidence/codex-cli/codex-cli-eval-1
python3 -I -B evidence/codex-cli/codex-cli-eval-2/runtime/scripts/replay_score_package.py evidence/codex-cli/codex-cli-eval-2
python3 -I -B evidence/codex-cli/codex-cli-eval-3/runtime/scripts/replay_score_package.py evidence/codex-cli/codex-cli-eval-3
python3 -I -B evidence/claude-desktop/claude-desktop-eval-1-correction-1/runtime/scripts/replay_score_package.py evidence/claude-desktop/claude-desktop-eval-1-correction-1
python3 -I -B evidence/claude-desktop/claude-desktop-eval-2-correction-1/runtime/scripts/replay_score_package.py evidence/claude-desktop/claude-desktop-eval-2-correction-1
python3 -I -B evidence/claude-desktop/claude-desktop-eval-3/runtime/scripts/replay_score_package.py evidence/claude-desktop/claude-desktop-eval-3
python3 -I -B evidence/copilot/copilot-2026-10-04-01/runtime/scripts/replay_score_package.py evidence/copilot/copilot-2026-10-04-01
python3 -I -B evidence/copilot/copilot-2026-10-04-02/runtime/scripts/replay_score_package.py evidence/copilot/copilot-2026-10-04-02
python3 -I -B evidence/copilot/copilot-2026-10-04-03/runtime/scripts/replay_score_package.py evidence/copilot/copilot-2026-10-04-03
python3 -I -B evidence/antigravity/antigravity-2026-10-05-02/runtime/scripts/replay_score_package.py evidence/antigravity/antigravity-2026-10-05-02
python3 -I -B evidence/antigravity/antigravity-2026-10-05-03/runtime/scripts/replay_score_package.py evidence/antigravity/antigravity-2026-10-05-03
python3 -I -B evidence/antigravity/antigravity-2026-10-05-04/runtime/scripts/replay_score_package.py evidence/antigravity/antigravity-2026-10-05-04
python3 -I -B evidence/cursor-cli/cursor-cli-2026-10-06-r1/runtime/scripts/replay_score_package.py evidence/cursor-cli/cursor-cli-2026-10-06-r1
python3 -I -B evidence/cursor-cli/cursor-cli-2026-10-06-r2/runtime/scripts/replay_score_package.py evidence/cursor-cli/cursor-cli-2026-10-06-r2
python3 -I -B evidence/cursor-cli/cursor-cli-2026-10-06-r3/runtime/scripts/replay_score_package.py evidence/cursor-cli/cursor-cli-2026-10-06-r3
python3 -I -B evidence/hermes/hermes-codex-2026-10-07-08/runtime/scripts/replay_score_package.py evidence/hermes/hermes-codex-2026-10-07-08
python3 -I -B evidence/hermes/hermes-codex-2026-10-07-09/runtime/scripts/replay_score_package.py evidence/hermes/hermes-codex-2026-10-07-09
python3 -I -B evidence/hermes/hermes-codex-2026-10-07-10/runtime/scripts/replay_score_package.py evidence/hermes/hermes-codex-2026-10-07-10
python3 -I -B evidence/openclaw/openclaw-2026-10-07-04/runtime/scripts/replay_score_package.py evidence/openclaw/openclaw-2026-10-07-04
python3 -I -B evidence/openclaw/openclaw-2026-10-07-07/runtime/scripts/replay_score_package.py evidence/openclaw/openclaw-2026-10-07-07
python3 -I -B evidence/openclaw/openclaw-2026-10-07-06/runtime/scripts/replay_score_package.py evidence/openclaw/openclaw-2026-10-07-06
python3 -I -B evidence/kimi/kimi-2026-10-08-01/runtime/scripts/replay_score_package.py evidence/kimi/kimi-2026-10-08-01
python3 -I -B evidence/kimi/kimi-2026-10-08-02/runtime/scripts/replay_score_package.py evidence/kimi/kimi-2026-10-08-02
python3 -I -B evidence/kimi/kimi-2026-10-08-06/runtime/scripts/replay_score_package.py evidence/kimi/kimi-2026-10-08-06
```

The runner recomputes all 31 metric states and a selected-response loss control. Never pass --record-expected when verifying an existing packet.

The -B option stops Python from writing bytecode files into the packet; the runner refuses a packet that holds a file its manifest does not list. The OpenClaw packets need Python 3.14 for the standard Zstandard module. Python isolated mode is not an operating-system sandbox. The independent review receipts separately record actual macOS sandbox-exec denial probes and hash/tamper controls. Use the repository's replay_score_package(..., expected_manifest_sha256=..., os_sandboxed=True) API to repeat those checks on macOS.

DeepSeek packets require the exact declared libzstd platform dependency; the library is not bundled. An unavailable or mismatched dependency is a reproduction failure, not a format score. See the packet manifest for its version and hash.

These are explicitly sanitized synthetic-session derivatives. Transformation receipts pin the private originals and record changes. Privacy review and original capture provenance remain operator attestations; deterministic replay does not independently reacquire a live session.
