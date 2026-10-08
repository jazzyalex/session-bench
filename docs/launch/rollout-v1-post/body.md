{{STYLE}}

We ran the same two-prompt bug fix through twelve coding-agent harnesses,
three times each, and kept only the session files they wrote. The file being
fixed is 223 bytes long. Pi recorded the whole session in 19 KB. OpenClaw
recorded it in 697 KB, and 3% of that is the session.

Size is the cheap finding. The one that matters is what you can get back.
For every run we rebuilt the session from its files alone and checked 31
facts against an independent record of what happened: both prompts and
replies, every tool call with its result and exit code, the file before and
after the edit, the model, the token counts, the timestamps, and whether the
files open at all without the vendor's own code. That is
**[Session-Bench v1]({{ '/bench/v1/' | relative_url }})**: 100 points, twelve
harnesses, 36 runs, and every score recomputes from the published session
files with one command per run.

[The August bench]({{ '/blog/session-bench-launch/' | relative_url }}) asked
twenty pass/fail questions of each format, answered mostly from our own corpus
and weekly drift checks. v1 is a different instrument. It runs the harness,
keeps an outside witness, and replays the result. The two do not share a
scale, and the order changed.

{{FIG:scorecard}}

<div class="sb1 sb1-tl" markdown="1">

**The short version**

- All twelve keep the conversation. Prompts, replies, tool calls and results
  come back in every run, with one partial exception.
- The spread is 78 to 97, and it comes from storage, not memory: how many
  times each event is written, whether the cost is recorded, and how much of
  the file is the session at all.
- Pi and Hermes write each event once. Four harnesses never do.
- Four of twelve record token usage that adds up to a stated total. One
  records none.
- DeepSeek Harness is first at 96.9, Cursor CLI last at 78.1. The model
  inside is not what is graded: the twelve rows did not run the same one.

</div>

## How a run is scored

<div class="sb1 sb1-steps">
<div class="sb1-step"><b>1 · Same task</b><span>Two prompts against a small Python project: run a check that fails, then take a correction, edit one file and run the check again. Four tool calls are scored.</span></div>
<div class="sb1-step"><b>2 · An outside witness</b><span>The harness's live output stream where it has one, a ledger that the check script writes each time it runs, and hashes of the project files record what really happened. None of it comes from the session files.</span></div>
<div class="sb1-step"><b>3 · Files only</b><span>A decoder reads the session files the harness left on disk, and nothing else.</span></div>
<div class="sb1-step"><b>4 · 31 comparisons</b><span>Each fact in the files is matched to the witness. Missing scores zero. Wrong scores zero. Present but unreadable scores zero.</span></div>
</div>

The 31 metrics add up to 100 points in five groups: record fidelity (30),
causality and context (20), usage and attribution (15), portability and
openness (20), durability and signal (15). Each harness gets the mean of
three runs.

## Everyone keeps the conversation

The least surprising result is the one that matters most if you worry about
losing work. Eleven of twelve harnesses score 30 of 30 on record fidelity and
20 of 20 on causality in every run: both prompts, both replies, all four tool
calls, their results and exit codes, which edit followed which correction, and
which result belongs to which call. Claude Desktop is the exception, and the cause is
the run rather than the format: its model wrote the file from inside a shell
command instead of calling an edit tool, so the edit has no result of its own
to match, and one result in four goes missing.

So if the chat window loses a session, the file still has it.
[Where each agent keeps those files]({{ '/blog/where-agents-store-history/' | relative_url }})
and [how to get a lost session back]({{ '/blog/recovering-a-lost-session/' | relative_url }})
are earlier posts. The 19 points between first place and last come from
everything else.

## The same fix, 19 KB to 697 KB

{{FIG:bytes}}

The session itself is small everywhere: between 15 KB and 108 KB of prompts,
replies, tool calls and results. What varies by a factor of 37 is the total.
The rest is what the harness writes around the session: its instructions and
tool definitions, repeated copies, snapshots and bookkeeping.

This is not a disk-space problem. It is a context problem. The moment you
paste a raw session file into another model, to summarize it, to hand the
work to a different agent, or to ask what went wrong, you pay for every byte.
At a rough four bytes per token, Pi's record is under 5,000 tokens. Claude
Desktop's is about 117,000 and OpenClaw's about 174,000, for the same
two-prompt fix. [Handover between sessions]({{ '/blog/the-handover-problem/' | relative_url }})
is hard enough without paying 35 times over for the transcript.

One caveat on the longest bar. OpenClaw ran on its Codex backend in our setup,
and 72% of its 697 KB is that backend's own files for the session, written in
a home the plugin owns. They land on your disk for the same work, so they
count. OpenClaw's own store is about 190 KB.

## How many times it says each thing

{{FIG:copies}}

Read a session top to bottom the way a script or a model would, with no
knowledge of the format, and count how often each prompt, reply, tool call and
result is stated. Pi and Hermes state each one once. OpenCode, Kimi Code,
OpenClaw and Antigravity never do: every event appears at least twice, and no
field marks the later copies as copies. Codex stores 3 of 44 events once across its three runs.

The causes differ. OpenCode writes every message part again, one to five
times, into an `event` table as the part updates. Kimi Code's wire log states
each prompt three times and each reply, tool call and result twice: once as it
happens and once more in a summary record when the turn ends. Codex logs each
prompt, reply, command, result and edit twice, as a UI event and as the wire
item. OpenClaw stores each tool call in two forms and repeats the events in a
trace table. Claude Code writes each prompt twice and repeats an edit's
arguments in its result.

Two things follow. A tool that counts messages or tool calls straight from the
file over-counts by the factor in the chart. And a model that reads the raw
file reads the same command output two or three times, which is the previous
chart again.

## What did it cost

Nine of twelve write token counts with separate input, output, cache-read and
cache-write numbers. Four of the nine also write a total that the per-reply
records add up to: DeepSeek Harness, Copilot CLI, OpenCode and Codex. Those
are the rows where you can check the arithmetic from the files alone.

The other five have numbers and nothing to check them against. Pi's six
per-message records add up exactly, but the file states no total, and Kimi
Code, Claude Code and Claude Desktop are in the same position. OpenClaw's own
store writes the token total of a run, twice, and no per-request numbers.

Then the three with gaps. Hermes keeps session totals and the usage record of
the last request only, so one of the two replies has numbers of its own.
Antigravity records input, output and cache-read counts and has no cache-write
field. Cursor CLI stores no token count of any kind in the session.

If you want to know what a feature cost you, that is the buying guide.

## What you can get back, row by row

{{FIG:grid}}

**DeepSeek Harness, 96.9.** A Zstandard-compressed JSONL file per session
with a declared format version, usage on every step that adds up to a stated
total, and timestamps on 12 of 13 events. It loses its points on repetition:
each prompt and each tool call is written twice, so 63% of events appear once.
It also keeps the most actual session of the twelve, 108 KB.

**Pi, 96.4.** One plain `session.jsonl`. 19 KB, every event written once, 79%
of the bytes are the session. It records usage and cost per message and states
no total, which is the 3 points it drops. The smallest record in the set is
also the cleanest, as it was in August.

**Copilot CLI, 96.0.** An event log per session plus a SQLite store that holds
one usage row per model call. The shutdown event states cumulative totals and
the rows add up to them in every run. The same store keeps second copies of
prompts and replies, so 43% of events appear once.

**OpenCode, 94.4.** One SQLite database that `sqlite3` reads without help,
usage that adds up, a migration ledger that serves as a version. Then the
`event` table: no event is stored only once, and 15% of 235 KB is the session.

**Kimi Code, 91.2.** A wire log that keeps everything, including the requests
it had to retry after a rate limit. Prompts three times, replies and results
twice, and 8% of 215 KB is the session. It writes an exit code only when a
command fails. Usage on every request, no total.

**Claude Code, 89.3.** One JSONL transcript per session, 81% of events once,
usage on every reply. It writes no format version of any kind, and 10 of 13
events carry a usable timestamp.

**OpenClaw, 88.1.** Its own SQLite stores plus the files of the backend it
ran on: 697 KB, 3% session, 3.1 copies of each event. Events above a size
limit are Zstandard-compressed inside the database, the first prompt among
them. Usage is a run total.

**Codex, 87.5.** One rollout JSONL per session with complete usage that adds
up. Everything in it is written twice: 3 of 44 events appear once across the
three runs, and 14% of the bytes are the session. No storage format version. Codex Desktop writes the
same format and shares the row.

**Hermes, 86.4.** Rows in one SQLite database: 36 KB, each event once, 64%
session, a schema version. It is the second-leanest record here, and it scores
5.5 of 15 on usage because it keeps the numbers of one request only.

**Claude Desktop, 85.6.** The Claude Code transcript format plus a metadata
file, at four times the bytes: 469 KB, 4% session. The transcript embeds your
instruction files, skill list and connector instructions along with the work.

**Antigravity, 82.7.** A SQLite database per conversation whose columns hold
protobuf messages with no published schema. Standard tools open the database
and cannot read the session. No event is stored only once.

**Cursor CLI, 78.1.** The whole conversation is a graph of content-addressed
blobs in a SQLite file: JSON messages and protobuf records with no published
schema. No token count of any kind, and 2.7 copies of each event.

## Using this if you build with agents every day

<div class="sb1 sb1-picks">
<div class="sb1-pick"><b>You want to know what a feature cost</b><span><strong>DeepSeek Harness, Copilot CLI, OpenCode and Codex</strong> write token counts per reply and a total they add up to. Pi adds a cost figure per message. With Cursor CLI the session file cannot tell you.</span></div>
<div class="sb1-pick"><b>You paste sessions into another model</b><span><strong>Pi and Hermes</strong> give you each event once and little else. For Claude Desktop, OpenClaw, Kimi Code and OpenCode the raw file is mostly not your session: export or filter before you paste.</span></div>
<div class="sb1-pick"><b>You script over your history</b><span>Expect repeats everywhere except Pi and Hermes, and de-duplicate by id. Check for a format version before you parse: <strong>Claude Code, Claude Desktop and Codex</strong> write none.</span></div>
<div class="sb1-pick"><b>You want to read it with what you have</b><span><code>jq</code> or <code>sqlite3</code> is enough for ten of the twelve, with <code>zstd</code> for DeepSeek Harness and OpenClaw. <strong>Antigravity and Cursor CLI</strong> store protobuf, so you need a decoder.</span></div>
<div class="sb1-pick"><b>You attach a session to a bug report</b><span>A raw session file carries your home path and often account ids, your instruction files and hashes of local paths. Read the next section first.</span></div>
<div class="sb1-pick"><b>You only need the transcript</b><span>Any of the twelve. That part works.</span></div>
</div>

## What we got wrong on the way

The August post had a section like this one, and a bench that grades other
people's records has to keep its own.

**OpenCode was first, then it was fourth.** Our first scoring read OpenCode's
`event` table, and duplicate safety came out at zero. We then switched to a
leaner reading that left the table out. That was worth exactly 3 points and
put OpenCode first at 97.4. An outside review caught it: the rubric fixes what
the reader opens before any score is computed. We put the first reading back.
OpenCode is at 94.4.

**OpenClaw had 3 points it had not earned.** We credited its token total as
adding up. The store writes one run total twice and no per-request numbers, so
there is nothing to add. The credit is gone.

**We replaced failed runs, against our own rule.** The rubric says a scheduled
run is not replaced. For OpenClaw, Claude Desktop and Kimi Code we replaced
attempts that died on a controller fault, a provider rate limit or a missing
reply marker. None of them had a score. We kept the rows, waived the rule for
v1, and say so in the report.

**Our published evidence leaked, more than once.** Session files carry more
of your machine than you would guess. Reviewers found, in files we had already
cleaned: a cache file named by a hash of an account id, an index keyed by a
hash of a private temp path, a fingerprint of a home folder listing, and a
local date that gave away the time zone. All of it is fixed, and the report
lists what still stays readable. If you are about to attach a raw session file
to a public issue, that list is for you.

Four rounds of outside review went into this release. The first three said
do not ship.

## What this does not show

One small synthetic task: two prompts, four tool calls, three runs per harness,
one machine. Nothing here covers long sessions, compaction, sub-agents,
crashes or resumes, which is where session formats get tested in real work.

Each harness ran at one version with one model, and not the same model
across rows, so this is not a model comparison and a later version can score
differently.
Some scoring rules were written after the first captures. The repeat count
depends on which stores a reader has to open, which favors formats that keep
everything in one lean file.

Five rows (OpenClaw, Codex, Hermes, Antigravity and Cursor CLI) keep part of a
session in stores shared with other sessions. We do not read other sessions'
data, so their root could not be verified as complete, and that costs each of
them 3 portability points.

The checker compares the path and id of an edit, not its text. Packet reviews
were done by separate AI agent sessions on the same machine, with the outside
model reviews on top; no second person repeated the captures. Cursor Desktop
is not scored yet.

## Replay it

Every score can be recomputed. The release holds the sanitized session files
of all 36 runs, the scoring source, and one command per run that replays the
31 metrics from those files.

- [The bench page]({{ '/bench/v1/' | relative_url }}): the table, the waivers and the limits
- [The report](https://github.com/jazzyalex/session-bench/blob/main/artifacts/survival-v1-release/REPORT.md)
- [Replay instructions](https://github.com/jazzyalex/session-bench/blob/main/artifacts/survival-v1-release/REPRODUCE.md)
- [The rubric](https://github.com/jazzyalex/session-bench/blob/main/docs/survival-v1/rubric.md) and
  [how each row was decided](https://github.com/jazzyalex/session-bench/tree/main/docs/survival-v1/adapters),
  with every judgment call and what it is worth

If you think a row is wrong, its document lists the judgment calls and their
point values. Dispute one and the score follows. Session-Bench lives in its
own repository, [github.com/jazzyalex/session-bench](https://github.com/jazzyalex/session-bench),
and disputes are welcome there as issues.
[Agent Sessions](https://jazzyalex.github.io/agent-sessions/?campaign=blog&ref=session-bench-v1)
is a free, local-only macOS browser for these session stores; reading them
every day is how the bench got built.
