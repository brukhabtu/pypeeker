# Pipeline guidance (living doc — read by the conductor)

The task-pipeline conductor reads this file before deciding a run's shape. Each
version retro updates it; the frozen retros live in `versions/RETRO-v*.md`.
Last updated: after v3 (runs: TASK-135–141, 146, and the 142/143/144 arc; see
RETRO-v3.md).

**This file reaches the conductor only.** It governs a run's *shape* — model,
plan review, split, lenses, test policy, re-review. A rule about how an agent
should *behave* (what to read, which commands to prefer) is inert here, because
no implementer, scout, or lens ever sees this file. Behaviour rules belong in
the `SPEC` constant in `task-pipeline.js`, which is prepended to every agent
prompt. Put shape here, behaviour there.

## Implementer model

- **sonnet** for mechanical, well-specified work executing a tight plan. Evidence:
  the zero-must-fix slices (store-read port, traits, 133's two stages) were sonnet
  with precise plans.
- **opus** for judgment-heavy work: contract-preserving cutovers, deletions,
  cross-cutting migrations, anything where the implementer must make calls the plan
  cannot fully pin (134's shim retirement, 132's refusal-surface work).

## Plan review

Arm it when the task is L/XL, touches many frozen contracts, or the plan carries
risky claims the scout could not fully verify. Skip it — with a stated reason — when
the scout already validated the change end-to-end in a scratch copy or the plan's
claims are all probe-verified. A reasoned skip is a feature, not a shortcut.

## Split

Only for >2 genuinely distinct concerns. Each split stage costs a full implementer
context; non-final stages run targeted tests only (the gates run the full suite).

## Lenses

This is where quality is bought — every consequential catch came from a lens with a
SPECIFIC hunting ground: name the exact contracts, files, and failure classes to
attack, and instruct executing code in /tmp scratch projects. Generic lenses find
style. History's best catches: a data-loss window (external edit mid-loop), silent
star-import breakage, a fixer-introduced double-import (re-review round). 3 lenses
is the default; 4 for wide-blast-radius tasks; 2 for small ones.

## Test policy

"frozen" unless the task deletes/replaces an API ("port") or deliberately breaks a
pinned contract ("migrate"). Sanctioned exceptions must be enumerated in advance —
mid-run discoveries that a frozen test "is wrong" mean the implementation is wrong.

## Re-review

The script auto-triggers a focused re-review when must-fix ≥ 2. Arm
`re_review_on_fix` yourself when the task borders frozen contracts even if you
expect few findings — the fixer is otherwise the pipeline's only unreviewed writer.

## Cost envelope (observed)

Measured across v3 runs, in completion tokens and dollars at list Opus/Sonnet
rates:

| shape | agents | tokens | ≈ cost | example |
|---|---:|---:|---:|---|
| lean (no split, 2–3 lenses) | 6 | 374k | $8 | TASK-146 |
| standard | 8–14 | 0.6–1.6M | $13–35 | TASK-140, 141, 138 |
| heavy (split + 4 lenses + re-review) | 19 | 2.3M | $50 | 142/143/144 arc |

Split × lenses is the cost driver, and there is a **floor of roughly $8 per run**
— every pipeline pays a scout to read the repo before it knows whether the task
is hard. That floor is trivial against a data-loss bug and pure overhead against
a file move. Choose the smallest shape that protects quality; scale up for blast
radius, not for comfort.

A dependent group of tasks is cheaper as one arc than as N runs — the 142/143/144
bundle shared a single scout instead of paying three to re-derive the same
context.

## Standing rules (script-enforced, do not relax)

- Pipeline agents never edit `backlog/` — bookkeeping is the orchestrator's, after
  independent verification (an implementer once recorded wrong gate numbers).
- Scouts probe; they never modify the repo tree.
- The full gate (pytest + ruff + self-lint) is non-negotiable at gate stages and the
  final split stage.

## Parallel-worktree extraction runs (added 2026-09-05)

Evidence: the Sept 1–3 2026 architecture-review fix run — eight packages, each
implemented in its own worktree, reviewed by a per-package skeptic, then serially
merged with the full gate after every merge. Every gate stayed green; two later
review rounds still found three behavior regressions. The rules below are for the
conductor to write into each reviewer's brief, since reviewers never read this file.

- **A "pure extraction, no behavior change" claim is verified against `main` and
  against what `pypeeker check` observes — never against the implementer's diff.**
  All three escapes sat on shared substrate: `analysis/symbols.py`'s symbol-id map
  flipped `analysis/calls.py` and `analysis/writes.py` from last-wins to first-wins
  (now an explicit `last_wins=` parameter), `SemanticQueryEngine` index loading was
  memoized against a test that pinned live reads (`all_indexes()` re-reads again),
  and `paths.is_barrel_path` was tightened from `endswith("__init__.py")` to a
  basename test. The oracle was blind by construction to all three.
- **Name the frozen-substrate probe and require it by name.** Before signing off on
  any change under `binder/`, `analysis/`, `query/`, `paths.py` or `storage/`, the
  reviewer runs: duplicate symbol ids in one file (which binding wins), a file named
  `pkg/x__init__.py` (barrel or not), walrus and comprehension scopes, and engine
  freshness (write to the store, re-query the same engine). A green full gate is not
  evidence for any of these.
- **Reviewers diff against `main`, not the segment base.** A second-round reviewer
  compared against its segment base and filed two false "no ledger entry" findings
  on a change the base already carried and had since reverted.
- **An existing test is never rewritten to the opposite contract.** If the
  implementation needs the inverse of what a test pins, that is an escalation with a
  ledger or decision entry, exactly as with frozen-test collisions.
- **Scratch filenames are per-agent.** Two agents wrote a same-named helper into the
  shared session scratchpad and one ran the other's, applying edits against a
  different package's worktree; only a hardcoded path prevented corruption. Prefix
  every scratch file with the agent key.
- **Budget for report delivery.** Subagent final results truncate near 4,000
  characters and subagents cannot write report files, so long reviews come back as
  chunked messages on request; ask for a bounded report up front. The forked
  `/code-review` skill also stalled twice waiting on finders that had already
  finished — poll rather than block.

Keep: per-package skeptics that re-run the full gate on the committed branch; serial
integration with a gate after each merge (zero merge failures across eight branches);
a final completeness critic (it caught stale doc references, an unnoted indent
assumption, and function-body deep imports the sweep missed); and implementers that
refuse a suggested unification after disproving it — one proved two id builders
diverge and pinned both with a test.
