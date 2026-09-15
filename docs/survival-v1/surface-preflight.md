# Five-surface static preflight

Status: Phase P-2 static preflight plus bounded calibration updates through 2026-09-13.
The original static pass did not open a personal session store or launch a model. The
later Desktop attempts are called out explicitly below. A route described as *plausible*
is an input to calibration, not evidence that the product writes the proposed records.

## Decision

| Surface | Installed evidence | Isolated launch route | Local native-record root | Independent response observer | P-2 conclusion |
|---|---|---|---|---|---|
| Codex CLI | `codex-cli 0.154.0` | Plausible with metadata-only discovery in the authenticated root | `$CODEX_HOME` | `codex exec --json` | Conditional: prove one new rollout and never open pre-existing session content. |
| Codex Desktop | Codex is integrated in `/Applications/ChatGPT.app`, version `26.908.40834` (build `8881`, bundle `com.openai.codex`); bundled `codex-cli 0.154.0-alpha.6.2` | Metadata-selected fresh task in the normal authenticated root; copied decode is isolated | Exact new rollout plus thread-keyed shell snapshot captured; complete-root proof remains open | Supported Codex app task API records submitted turns and bounded visible responses; direct Computer Use remains denied | Two-turn calibration passed; unscored until complete-root/canonical-copy proof and evaluated repetitions. |
| Cursor CLI | Cursor Agent `2026.09.08-6caf4ff` | Plausible | `CURSOR_DATA_DIR`, default `~/.cursor` | `agent --print --output-format stream-json` | Conditional: prove isolated project records and account-keychain separation. |
| Cursor Desktop | Cursor `3.12.30` (`63a2996a10d9e476b6c28e951dd7691d9c0cf480`) | Short `/tmp` user-data and extensions; unique synthetic `/tmp` project | Fresh project-keyed transcript under `~/.cursor/projects/` plus session-related records in isolated user-data SQLite stores; selected companion decoded, extra session-bearing rows unqualified | Computer Use observed both turns in the isolated instance | One evaluated capture completed; second was invalid at the Cursor Free usage limit. Paused and unscored. |
| OpenCode CLI | `1.18.30` | Plausible with default auth plus isolated DB | `OPENCODE_DB`, default data-root DB | `opencode run --format json` | Conditional: prove isolated DB capture and preserve WAL companions. |

“Conditional” means the static evidence supports a bounded, isolated calibration. It does not qualify a benchmark run. “Blocked” means this preflight does not establish both a readable native record and an independent response observer.

## Method and limits

The commands below used only executable metadata, `--help`, static package or application resources, and repository documentation. They did not call a model or read a session/history root.

```sh
codex --version; codex --help; codex exec --help
opencode --version; opencode --help; opencode run --help; opencode db --help
agent --version; agent --help
cursor --version
ps -ax -o pid=,comm=  # filtered to Codex/OpenAI/ChatGPT application paths
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  /Applications/ChatGPT.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
  /Applications/ChatGPT.app/Contents/Info.plist
/Applications/ChatGPT.app/Contents/Resources/codex --version
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
  /Applications/Cursor.app/Contents/Info.plist
jq '{nameShort, version, commit, dataFolderName}' \
  /Applications/Cursor.app/Contents/Resources/app/product.json
rg -a -o 'CODEX_HOME|sessions/[A-Za-z0-9_./-]+' <installed-codex-bundle>
rg -a -o 'OPENCODE_DB|opencode\.db|XDG_DATA_HOME' <installed-opencode-binary>
rg -a -o 'CURSOR_DATA_DIR|user-data-dir|workspaceStorage|globalStorage' \
  <installed-cursor-agent-or-app-bundle>
```

The original standard-location check looked for a separate `Codex.app`. A process-path
inventory established that the active Codex Desktop surface is integrated into
`/Applications/ChatGPT.app`; its bundle metadata and bundled Codex executable were then
inspected statically. No GUI automation or session content was used.

## Surface findings

### Codex CLI

`/opt/homebrew/bin/codex --version` reported `codex-cli 0.154.0`. `codex exec --help` exposes `--json`, which emits events to stdout as JSONL, and `--ephemeral`, which explicitly prevents session-file persistence. The latter must **not** be used for a record-survival run. The same help states that configuration is loaded from `$CODEX_HOME/config.toml`, and that `--ignore-user-config` still uses `CODEX_HOME` for authentication. Static strings in the bundled native executable include both `CODEX_HOME` and the `sessions/` module paths.

A plausible future controller vector is:

```sh
CODEX_HOME="$RUN_ROOT/codex-home" \
  codex exec --ignore-user-config --json -C "$RUN_PROJECT" "$PROMPT"
```

This is only a route proposal. Authentication must be provisioned into the isolated home through an approved, non-personal mechanism before any run; an empty `CODEX_HOME` is not expected to inherit the normal login. The only static root claim is `$CODEX_HOME`; the exact emitted session-file hierarchy, its completeness, and the relationship between its JSONL stream and the persisted record remain unverified. A controller that timestamps and hashes its own stdin plus the `--json` stdout can be an independent response observer, but must prove response boundaries and binding to the captured native root during calibration.

### Codex Desktop

The current Codex Desktop surface is integrated into `/Applications/ChatGPT.app` rather
than installed as a separate `Codex.app`. Its `Info.plist` reports version
`26.908.40834`, build `8881`, and bundle identifier `com.openai.codex`; the bundled
`Contents/Resources/codex` reports `codex-cli 0.154.0-alpha.6.2`. Static strings in that bundled
executable reference `CODEX_HOME` and session modules. This proves an installed Desktop
bundle and a related local persistence implementation, but it does not prove that the
complete Desktop record is confined to the CLI root or that an environment override
controls all Desktop-side artifacts.

Direct Computer Use access to the Codex app was denied again by the host safety boundary.
The supported task API was then used as a distinct observer channel. It recorded the two
submitted inputs, bounded visible responses, commands, and file change without reading the
native rollout. The calibration joined both exact canaries to one fresh Desktop session,
copied its rollout and thread-keyed shell snapshot, decoded the copied JSONL, and confirmed
that deleting the R2 final-answer record changes the decoded result to native loss. This is
task-API observation, not manual GUI behavior. Complete-root and canonical-equality proof
remain open.

## Isolated-auth check

Three fresh temporary roots were created and destroyed without model submission. A fresh
`CODEX_HOME` reported `Not logged in`. A fresh OpenCode `XDG_DATA_HOME` plus explicit
`OPENCODE_DB` reported zero credentials. Cursor Agent reported an authenticated account
even with fresh `CURSOR_DATA_DIR` and `CURSOR_CONFIG_DIR`, consistent with authentication
being held outside the session-data root. The practical acquisition boundary therefore
isolates session data where the product supports it and otherwise uses metadata-only
before/after discovery. It may use the product's normal account lookup, but it never opens,
copies, hashes, or publishes credential material or pre-existing session content.
The subsequent bounded calibration used these practical boundaries. Its retained outcomes
are recorded in [calibration-status.md](calibration-status.md).

**Remaining blocker:** demonstrate which Desktop and bundled-runtime roots comprise the
complete record and prove a canonical copied representation with the originals denied.
Do not schedule evaluated repetitions until those portability claims are written into the
run plan.

### Cursor CLI

The PATH `cursor` command is a shim. Its `--version` reports that it cannot find another Cursor IDE executable on PATH, but `cursor agent` delegates to the installed direct CLI at `/Users/alexm/.local/bin/agent`. That CLI reports version `2026.09.08-6caf4ff`; its help exposes `--print` and `--output-format stream-json`, plus `--workspace` and `--worktree`.

Static code in that installed CLI defines `CURSOR_DATA_DIR`, defaulting to `~/.cursor`, and a `projects` child root. It also defines `CURSOR_CONFIG_DIR`, defaulting to `XDG_CONFIG_HOME/cursor` or `~/.cursor`. The exact per-project directory key, the transcript files within it, and whether every response is persisted are unverified.

A plausible future vector is:

```sh
CURSOR_DATA_DIR="$RUN_ROOT/cursor-data" \
CURSOR_CONFIG_DIR="$RUN_ROOT/cursor-config" \
  agent --print --output-format stream-json --workspace "$RUN_PROJECT" "$PROMPT"
```

The retained calibration used the product's supported account/keychain lookup while
isolating `CURSOR_DATA_DIR` and `CURSOR_CONFIG_DIR`; credential material was never opened
or copied. The stream bound both response canaries to one isolated project transcript.
The native transcript decoder is now implemented. Its presence does not qualify the
retained CLI capture for a score; Cursor work is paused after the Free quota stop.

### Cursor Desktop

`/Applications/Cursor.app/Contents/Info.plist` reports version/build `3.12.30`; `product.json` reports `nameShort: Cursor`, `dataFolderName: .cursor`, and commit `63a2996a10d9e476b6c28e951dd7691d9c0cf480`. The packaged Electron main code accepts `--user-data-dir` and, on macOS, defaults a product user-data directory to `~/Library/Application Support/<nameShort>`, hence `~/Library/Application Support/Cursor`. The same static code identifies:

- `<user-data-dir>/User/workspaceStorage`;
- `<user-data-dir>/User/globalStorage/state.vscdb`; and
- `--extensions-dir`.

The Electron user-data root can be isolated without inspecting the normal profile:

```sh
open -na /Applications/Cursor.app --args \
  --user-data-dir "$RUN_ROOT/cursor-desktop-user-data" \
  --extensions-dir "$RUN_ROOT/cursor-desktop-extensions" \
  "$RUN_PROJECT"
```

The discovery calibration first exposed Electron's 103-character IPC socket-path limit.
A later preflight used `/tmp/sbcd4/ud` and `/tmp/sbcd4/ext`, launched a distinct Cursor
process, and bound Computer Use to its `project` window. The first sign-in attempt failed
before a prompt and remains `N/A`. After the owner signed into the isolated instance,
`cal-6` completed the synthetic two-turn task. Its fresh project-keyed JSONL transcript
was found under `~/.cursor/projects/tmp-sbcd6-project/agent-transcripts/`, outside
`--user-data-dir`; filesystem birth times place the project root and transcript
within this calibration window. The
isolated workspace and global SQLite stores also contained session-related rows.
Thus `--user-data-dir` alone is **not** a complete session-root boundary. The copied
sanitized transcript decoded offline with the original roots and network denied, as
recorded in the local `cal-6` copied-replay receipt. A later selected SQLite companion
capture recovered tool-result bubbles from `cal-6` and evaluated `eval-1`; the copied
`eval-1` transcript and selected companion replayed with original roots and network
denied. A closure scan also found five `cal-6` and six `eval-1` additional
session-bearing `agentKv`/`inlineDiff` rows. These have only a private exact capture and
public hash/size inventory, not a qualified semantic decode or complete sanitized
bundle. `eval-2` then stopped on the Cursor Free usage-limit message before a completed
R1 response. No Desktop score follows, and further Cursor research is paused.

### OpenCode CLI

`opencode --version` reported `1.18.30`. Its help exposes `opencode run --pure --format json`, `--dir`, `opencode db path`, and `opencode export`. Static binary code resolves an absolute `OPENCODE_DB` directly; absent that override it creates `opencode.db` under its data root. The code enables SQLite `journal_mode = WAL`. Its static auth-path logic uses `$XDG_DATA_HOME/opencode/auth.json`, falling back to `~/.local/share/opencode/auth.json`.

A plausible isolated vector is:

```sh
XDG_DATA_HOME="$RUN_ROOT/xdg-data" \
OPENCODE_DB="$RUN_ROOT/opencode.sqlite" \
  opencode run --pure --format json --dir "$RUN_PROJECT" "$PROMPT"
```

The retained setup correction proved that raw JSON output can be recorded independently,
both response canaries bind to one session ID, and the stopped writer's `opencode.db`,
`opencode.db-wal`, and `opencode.db-shm` can be copied coherently. Default account auth
was used without opening or copying credential material. The remaining P-3 gate is a
current SQLite native decoder.

## P-3 entry checks

1. Create only a new `$RUN_ROOT` and scratch project; record exact binary/app identity before the first prompt. Session data must be isolated when the product provides a separate data-root override; otherwise inventory the authenticated root by metadata only and open exactly one proven-new candidate.
2. Prove the response observer receives two response boundaries without consuming the candidate native record.
3. Quiesce the writer, inventory every file under the declared isolated root, and demonstrate that the expected run marker occurs in the native artifact set.
4. For SQLite, capture the database plus every live companion before copying; for directory stores, freeze a complete manifest before decoding.
5. Reject a route if it reads pre-existing session content, cannot prove one new session,
   or uses an unbounded daemon. Supported account/keychain lookup is allowed without
   credential inspection; it is not part of the evidence bundle.

## Source record

Local static sources inspected on 2026-09-11:

- Codex CLI help and `exec` help from `/opt/homebrew/bin/codex`; native bundle under `/opt/homebrew/lib/node_modules/@openai/codex/`.
- Codex Desktop process path, `/Applications/ChatGPT.app/Contents/Info.plist`, and bundled
  `/Applications/ChatGPT.app/Contents/Resources/codex`. No GUI or transcript content was
  inspected.
- OpenCode help, `run` help, and `db` help from `/opt/homebrew/bin/opencode`; static executable `/opt/homebrew/lib/node_modules/opencode-ai/bin/opencode.exe`.
- Cursor Agent help and static package under `/Users/alexm/.local/share/cursor-agent/versions/2026.09.08-6caf4ff/`.
- Cursor Desktop `Info.plist`, `product.json`, `out/main.js`, and `out/cli.js` under `/Applications/Cursor.app/Contents/`.
- Repository scope and v1 protocol: `README.md`, `docs/design/2026-09-11-wow-benchmark-plan.md`, and `docs/launch/prototype-execution.md`.

No external documentation was necessary for the conclusions above.
