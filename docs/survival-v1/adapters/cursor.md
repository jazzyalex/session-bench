# Cursor survival-v1 adapter

Status: Cursor research paused after a Cursor Free usage-limit rejection. Cursor CLI has
one captured unscored correction and a native transcript decoder. Cursor Desktop has a
clean two-turn calibration and one completed evaluated capture, but remains unscored
because the full native record spans two roots and extra SQLite rows are not qualified. No normal
native history or credential material was copied into the evidence package.

The implementation is [`session_bench/adapters/cursor.py`](../../../session_bench/adapters/cursor.py).
It has no default process launcher and no native-store reader. A controller must
inject a static identity probe. A runner is required separately for an explicitly
authorized CLI calibration, so importing or preflighting the module cannot start
a model.

## Cursor CLI

`preflight_cli(run_root, workspace, identity_probe=...)` constructs a
surface-bound `CursorCliLaunchPlan`. Both roots and the synthetic workspace must
be absolute children of a fresh run root. The plan supplies only these Cursor
overrides:

```text
CURSOR_DATA_DIR=<run_root>/cursor-data
CURSOR_CONFIG_DIR=<run_root>/cursor-config
```

Its exact command vector is:

```text
agent --print --output-format stream-json --workspace <workspace>
```

The prompt is appended only when the injected runner is called. The adapter does
not copy credentials or normal Cursor roots. The retained run used supported keychain
account lookup with isolated data/config roots. It rejects known default locations, nested/shared isolation roots,
relative paths, and identity-probe fields that look like credentials or
history.

`run_cli` accepts a fake or explicitly supplied runner and observes its declared
stream through `observe_cli_stream`. The observer is independent of native
capture and accepts a response only when a response-shaped JSON event ends with
the exact workload canary. Input is bounded by event, line, and byte limits.
Seeing a canary in a tool argument does not count as a displayed response.

The stream observer is a seam for `surface_capture.py`; it does not inspect
`CURSOR_DATA_DIR` or claim that a stream event was persisted. A future collector
must separately prove the complete fresh native project root, quiesce the
writer, preserve companions, and bind native IDs to these observed boundaries
before producing survival-v1 evidence.

## Cursor Desktop

`preflight_desktop(run_root, workspace, identity_probe=...)` emits a safe launch
plan without opening Cursor:

```text
Cursor --user-data-dir <run_root>/cursor-desktop-user-data \
       --extensions-dir <run_root>/cursor-desktop-extensions <workspace>
```

The Desktop plan is always `rankable=False`. With no independent evidence it is
blocked. A caller may supply `DesktopObservation` only after an actual GUI
observer has recorded accessibility text, reviewed the visual output, and
established the complete native transcript-root set. The resulting state can be
`ready_for_calibration`, but it
still cannot become a ranked result in this adapter. `require_desktop_calibration_ready`
raises until every required observation is present, and
`reject_desktop_ranked_result` is an explicit guard against treating readiness
as a score.

The short-root preflight launched a separate Cursor process with `/tmp/sbcd4/ud` and
`/tmp/sbcd4/ext`, and Computer Use bound to its `project` window. The original auth
failure was `N/A`. After sign-in, the clean `cal-6` run preserved two turns and one
fresh project-keyed transcript. It also exposed a contract gap: Cursor writes that
transcript under `~/.cursor/projects/<project-key>/agent-transcripts/`, outside the
isolated `--user-data-dir`. Session-related SQLite rows occur inside the isolated root.
The selected session-keyed SQLite companion adds the tool-result bubbles absent from
the transcript-only decode. In `eval-1`, a copied transcript and selected companion
recovered two responses, eight actions, eight results, and ten relations; an offline
replay with original roots and network denied matched the ordinary semantic digest.
The closure scan found six more `agentKv`/`inlineDiff` session-bearing rows, retained
privately with a public hash/size inventory. They are not yet semantically qualified or
included in the copied public bundle. The current preflight guard still rejects this
split-root layout. `eval-2` hit the Cursor Free usage limit before an R1 response and is
invalid/N/A, not a persistence failure. No further Cursor model submissions are planned
while this lane is paused.

Desktop accessibility/visual evidence and native roots belong to separate
capture/evaluation stages. The adapter never scans or copies them. A missing
root, an unobserved GUI, or an incomplete root declaration remains blocked rather
than becoming a zero. The transcript-plus-companion decoder does not remove that guard.

## Surface and evidence boundaries

`cursor-cli` and `cursor-desktop` have distinct identity values, launch plans,
isolation roots, and observer methods. `assert_surface_separation` rejects
shared native roots or a mismatched identity. The adapter does not import the
pending `session_bench.surface_capture` module; a future integration can pass
these small plan/observation objects into that capture layer.

The checked-in tests use temporary paths, fake identity mappings, and fake
JSONL. They do not invoke `agent`, open Cursor, inspect a normal user-data
directory, or handle credentials.
