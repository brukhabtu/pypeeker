---
name: measure-tool-costs
description: Measure where an agent's context is actually spent — tool-result payload size broken down by tool, by Bash command family, and by file read — from Claude Code subagent transcripts. Use when asked to measure or analyse tool output token costs, agent context spend, which tools or files are eating the most context, or where the tokens are going in pipeline or workflow runs.
---

# Measure tool costs

This skill runs `measure-tool-costs.py`, which reads the subagent transcripts
Claude Code writes per workflow run and reports tool-result payload size,
broken down by tool, by Bash command family, and by file read. It answers
"where is an agent's context actually going" with evidence instead of
intuition. Findings from the first run are recorded in
`.claude/workflows/TOKEN-COSTS.md`, which holds the current baseline to beat.

## When to run it

- After changing pipeline prompts or `.claude/workflows/PIPELINE-GUIDANCE.md`.
- After changing the shape of any tool's output.
- Before proposing a token-saving change, so the claim is evidence-backed
  rather than a guess.
- Whenever asked where context is going, which tools or files are the cost
  centers, or how a pipeline run's token spend breaks down.

Always compare fresh numbers against the `TOKEN-COSTS.md` baseline, and update
that file when the numbers move meaningfully.

## Running it

```
python3 .claude/skills/measure-tool-costs/measure-tool-costs.py [TRANSCRIPT_DIR]
```

The script is stdlib-only, so it needs no venv and no particular working
directory — `uv run python .claude/skills/measure-tool-costs/measure-tool-costs.py`
works identically. `TRANSCRIPT_DIR` is optional: with no argument the script
discovers the most recently active workflow-transcript directory in any
project, which is almost always the session you are in. Pass an explicit path
to measure an older session.

## Pointing it at a transcript directory

Subagent transcripts live at
`~/.claude/projects/<project-path-with-slashes-as-dashes>/<session-uuid>/subagents/workflows/`,
whose children are `wf_*` run directories, each holding `agent-*.jsonl` files.

The argument must be a directory whose **immediate children** are run
directories — the script globs `<dir>/*/agent-*.jsonl`. Passing a single
`wf_*` directory measures nothing, because its children are `.jsonl` files,
not further subdirectories. To scope the measurement to one run, make a
scratch directory and symlink that run into it:

```
mkdir /tmp/onerun && ln -s /path/to/.../subagents/workflows/wf_abc123 /tmp/onerun/run1
python3 .claude/skills/measure-tool-costs/measure-tool-costs.py /tmp/onerun
```

Flat `agent-*.jsonl` files that sit directly under `subagents/` (outside
`workflows/`) belong to non-workflow subagents and are deliberately not
counted.

The script exits 1 with a message on a missing directory
(`no such transcript directory: ...`) or one with no tool results at all
(`no tool results found under ...`).

## Reading the output

The output opens with the transcript directory and a total:

```
transcripts: <dir>
TOTAL tool-result payload: Nk approx tokens
```

Then three top-12 tables, sharing the same columns:

- **KTOK** — thousands of approximate tokens for that row.
- **CALLS** — number of tool calls contributing to the row.
- **AVG** — approximate tokens per call (KTOK-scale total divided by calls).
- **SHARE** — percent of the grand total.

The tables:

- **BY TOOL** — which tool (`Read`, `Bash`, `Grep`, ...) is the overall cost
  center.
- **BASH BY COMMAND FAMILY** — shell commands bucketed by the binary they
  actually run, after stripping a leading `cd ... &&`. This is where
  `sed`/`cat`/`git diff` acting as a substitute for `Read` shows up.
- **READ BY FILE** — each row prefixed `WHOLE` or `range` (whole means the
  `Read` call had neither `offset` nor `limit`) followed by the right-hand
  slice of the file's path. Worktree path prefixes (e.g. `pypeeker-wt146`) are
  normalized away, so the same file measured from a worktree and from the
  primary checkout aggregate into one row.

Finally a **READ SHAPE** footer contrasts whole-file reads against ranged
reads (call count, total ktok, average tokens per call). A high whole-file
average relative to ranged reads is the classic finding — it means agents are
reading entire files where a `grep` + ranged `Read` would do.

## What it counts, and what it does not

It counts the serialized `tool_result` payload of every completed tool call,
in approximate tokens (characters divided by four). That is the quantity a
prompt or tooling change can actually move.

It is **not** billed tokens. A tool result stays in the agent's context and is
re-sent on every later turn of that same agent, so the real billed cost of a
fat early result is some multiple of what this script reports — while prompt
caching absorbs much of that repeat cost in the other direction. Treat these
numbers as relative weights between call sites, never as an invoice.

Two more shape caveats worth knowing before trusting a number:

- **First-binary attribution** — a compound Bash command is credited entirely
  to its first tool (after stripping any leading `cd ... &&`), so a wrapper
  like `timeout` absorbs the cost of whatever it ran underneath.
- **Pairing by `tool_use_id`** — a tool call whose result never arrived (e.g.
  the transcript was cut off) is simply not counted.
