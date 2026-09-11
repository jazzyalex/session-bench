# Constructed Codex CLI L0 records

These public JSONL files model the documented Codex `rust-v0.154.0` rollout envelopes used by the L0 decoder tests. They are constructed examples, not vendor sessions and not evidence that Codex preserves the represented facts.

- `rollout-c01.jsonl` covers session metadata, accepted user input, and visible assistant output.
- `rollout-c02.jsonl` covers a local shell call, its joined output, a file-change call, and the final assistant report.

The files deliberately contain only the Session-Bench marker and synthetic fixture paths. Tests copy them into integrity-bound explicit decode packages before invoking the decoder.
