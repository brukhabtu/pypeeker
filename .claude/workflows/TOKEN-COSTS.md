# Tool-output token costs (measured)

Where agent context actually goes, measured rather than guessed. Re-run
`python3 .claude/skills/measure-tool-costs/measure-tool-costs.py` (the
`measure-tool-costs` skill) after any change here and update this file; the
numbers below are the baseline to beat.

**Baseline: 2026-08-02**, seven task-pipeline v3 runs (TASK-135 through 141,
plus 136/137 and 140/141 in parallel worktrees). **6,403k approximate tokens**
of tool-result payload, 9,307 tool calls.

## What this measures, and what it does not

Counted: the serialized `tool_result` payload of every completed tool call, in
approximate tokens (characters / 4). That is the quantity a prompt or tooling
change can move, which is why it is the thing tracked.

Not counted, and not to be confused with it: billed tokens. A tool result enters
the agent's context and is re-sent on every later turn of that agent, so the
billed cost of a fat early result is some multiple of what appears here — while
prompt caching absorbs much of that repeat. Treat these as **relative weights
between call sites**, not as an invoice. The `subagent_tokens` a workflow reports
is a different axis (completion tokens) and is not comparable to these figures.

## The headline

Grouped by what the call was *for*, rather than which tool ran it:

| purpose | ktok | share |
|---|---:|---:|
| **Reading file content** — `Read` 2,265k + `sed/awk` 1,187k + `cat/head/tail` 214k | **3,666** | **57%** |
| **Inspecting git state** — `git diff` 1,005k of it | 1,155 | 18% |
| **Searching** — `grep`/`rg` 713k + `ls`/`find` 68k | 781 | 12% |
| **Our own tooling output** — `pypeeker` 115k + `pytest` 87k + ruff/verify-repo.sh | ~210 | **3%** |

**Our scripts' output is 3% of the cost.** That was the surprise, and it
redirects effort: the envelope work is worth doing as a product feature (an
LLM-facing CLI is pypeeker's thesis, and the pattern is meant to be extracted),
but it is not the token lever. The lever is *how agents read files and diffs*.

## Lever 1 — file reading (57%)

- **Whole-file reads: 368 calls, avg 3,806 tokens** — 39% of `Read` calls, 62%
  of `Read` tokens. The worst single reads are 13–16k each (`refactor/batch.py`
  whole ×9 = 116k; `preconditions.py` whole ×4 = 64k).
- **`sed -n 1,220p` is the same act in shell clothing** — 1,177 calls, 1,187k
  tokens, the single largest Bash family. Agents reach for `sed`/`awk`/`head` to
  print a line range instead of `Read` with `offset`/`limit`.
- **`grep` is the healthy contrast**: 1,731 calls — the most-called tool of all —
  for 412 tokens each. Locating is cheap; ingesting is not.
- **`roadmap-plans.md` is the hottest file in the repo**: 208k tokens across 31
  reads, averaging 6,718 each — for a document CLAUDE.md explicitly labels
  "history, not pending work."

The rule the data supports: **locate with `grep`, then read a range; never read a
large file whole, and never shell out to `sed`/`head` to do a `Read`'s job.**

## Lever 2 — git inspection (18%)

`git diff` alone: **580 calls, 1,005k tokens, avg 1,733**. Agents re-inspect
their own working diff repeatedly and unscoped. `git status` is fine by contrast
(224 calls at 212 each). Cheaper shapes: `--stat` before the full diff, `-U1`
for narrower hunks, and a path argument when the question is about one file.

## Lever 3 — our tooling (3%)

Small, but the cheapest to fix and the only part we fully control:

- `pypeeker index src` emits a ~100-entry `skipped` array on every run (4.3KB,
  ~1,090 tokens) that no caller has ever needed.
- `pypeeker check` is already exemplary at 61 bytes when clean.
- `pytest -q` is 2.3KB on success — but **unbounded on failure**, and fix-loops
  are exactly where agents live. Broad-and-cheap first (`--tb=no`), then
  narrow-and-deep on the specific failure (`--lf --tb=short`).
- `scripts/verify-repo.sh` streams every step's output even when the step
  passes, though its own design record specifies capture-and-print-on-failure.
  The implementation diverged from its plan and a full pipeline with adversarial
  lens rounds did not catch it — a small live instance of the problem this
  document exists to measure.

## Caveats

Characters / 4 is an approximation. The command-family bucketing in the
measure-tool-costs skill's script attributes by the first binary after any
`cd` prefix, so a compound command is
credited entirely to its first tool. One family (`timeout`, 67k) is a wrapper
whose real cost belongs to whatever it ran. Worktree paths are normalized so the
same file measured in `pypeeker-wt137` and the primary checkout aggregates.

## v4 datapoint (2026-08-03) — did the reading discipline work?

Pipeline v4 put a reading-discipline block in every agent prompt (locate with
Grep, Read with offset/limit, no sed/head for line ranges, scope git diff).
First two v4 task-pipeline runs (TASK-149/150 arc, TASK-151), measured in
isolation via a symlink dir: **393k tokens, 719 calls**.

Per-call profile against the v3-era baseline — the honest comparison, since
task mix confounds totals:

| metric | v3 baseline | v4 (2 runs) |
|---|---:|---:|
| `sed/awk` share of all tool tokens | 18.5% | **5.0%** |
| `sed/awk` calls per run (avg) | ~168 | 17 |
| `git` avg tokens per call | 1,733 (diff) / 1,107 (family) | **504** |
| `cat/head/tail` avg per call | 651 | 283 |
| Bash avg per call (all) | 580 | **263** |
| ranged share of Read calls | ~61% | 67% |
| Read avg per call | 2,408 | 1,899 |

The shell-side discipline landed hard: agents largely stopped printing line
ranges through `sed` and stopped taking unscoped diffs. The `Read` side moved
less — whole-file reads persist at 44 calls averaging 3,923 tokens (baseline
3,806), i.e. the *rate* of whole-file reading dropped but the reads that
remain are as fat as ever. If a next lever is wanted, it is there. Caveats:
n=2 runs, different task mix from the baseline, and these runs read
`dsl-rewrite.md` (normative, deliberately) which inflates whole-file reads.

## v4 datapoint 2 (2026-08-04) — the TASK-152 run in isolation

The largest single pipeline run so far (19 agents, 2.5h, the DSL read half)
measured alone: **470k tokens of tool-result payload, 832 calls** — a fifth of
the 2,200k completion tokens the same run spent, and a fraction of what a
v3-era run of this size would have ingested.

| metric | v3 baseline | v4 run 1 (n=2) | v4 TASK-152 |
|---|---:|---:|---:|
| `sed/awk` share | 18.5% | 5.0% | 9.0% |
| `git` avg tokens per call | 1,733 (diff) | 504 | **385** |
| Bash avg per call | 580 | 263 | 355 |
| Read avg per call | 2,408 | 1,899 | **1,454** |
| whole-file Read avg | 3,806 | 3,923 | **1,861** |

The whole-file average halved — but read the cause honestly: the hot files
were the run's own new `dsl/` modules, which are small. The discipline holds
on the shell side (git at 385/call is the best yet); `sed/awk` crept back up
from 5% to 9%, worth watching in the phase-3 runs. Rules unchanged, no new
lever taken.
