# Discrimination pre-test

Status: provisional offline analysis of the three existing prototype captures. This is
not a v1 result. The captures predate the v1 protocol, all declare incomplete artifact
boundaries, and the OpenCode pilot contains a controller permission failure.

Purpose: determine whether the proposed v1 structural measurements can produce different
observations before building new adapters or collecting an evaluated cohort.

## Inputs

| Capture | Native events | Observer events | Usage records | Complete boundary |
|---|---:|---:|---:|---|
| Codex CLI pilot 01 | 29 | 11 | 10 | No |
| Codex Desktop pilot 01 | 36 | 7 | 14 | No |
| OpenCode CLI pilot 01 | 30 | 12 | 13 | No |

Only the explicitly packaged synthetic captures under
`artifacts/prototype-v1/captures/` were read. No product-store discovery, personal
history, vendor execution, or network access was used.

## Computable structural probes

| Proposed v1 question | Codex CLI | Codex Desktop | OpenCode CLI | Separates? |
|---|---|---|---|---|
| Native call/result topology | 7 calls / 7 results; prototype W5 100 | 7 calls / 10 results; prototype W5 70 | 10 calls / 5 results; prototype W5 50 | Yes |
| Per-response usage join | Unresolved: usage snapshots lack stable response joins | Unexercised: no independent visible-response population | 5/5 observed response joins in the prototype | Yes, with observer caveat |
| Model/surface/config agreement | Prototype C1 3/3 | Prototype C1 3/3 | Prototype C1 2/3 contradiction | Yes |
| Submitted correction recovery | Prototype W1 2/2 | Prototype W1 2/2 | Unresolved on incomplete boundary | Inconclusive |
| Portable archive | Unresolved | Unresolved | Unresolved | Not exercised |

The decoded Codex records also contain repeated assistant-message IDs: four unique IDs
appear as eight decoded assistant events in the CLI capture, and five unique IDs appear
as ten events in Desktop. The proposed precision-sensitive denominator would expose this
unless the v1 decoder proves that the duplicate envelopes are internal representations
and classifies them out under one frozen cross-format rule. It must not silently
deduplicate them only for Codex.

## Decision

**Proceed with the offline v1 foundation.** Causal topology, response attribution, and
identity agreement already yield different structural observations. They are stronger
than saturated presence checks and justify implementing the atomic scorer and equivalent
format controls.

Do not assign Session Survival Scores to these captures. They cannot exercise the shared
response-boundary canaries, complete-root gate, exact revision assertions, or portable
archive. OpenCode's edit path was blocked by the controller, and Desktop lacks an
independent visible-response observer. Their only role is to test whether the proposed
questions have discriminatory potential.
