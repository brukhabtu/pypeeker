# Pipeline guidance (living doc — read by the conductor)

The task-pipeline conductor reads this file before deciding a run's shape. Each
version retro updates it; the frozen retros live in `versions/RETRO-v*.md`.
Last updated: after v2 (runs: TASK-133, 132 ∥ 134; see RETRO-v2.md).

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

A comparable-scope task ran 11 agents / ~1.2M tokens with a lean shape and
19 agents / ~2M with a heavy one (3-way split + 4 lenses + re-review). Split ×
lenses is the cost driver. Choose the smallest shape that protects quality; scale
up only for blast radius, not for comfort.

## Standing rules (script-enforced, do not relax)

- Pipeline agents never edit `backlog/` — bookkeeping is the orchestrator's, after
  independent verification (an implementer once recorded wrong gate numbers).
- Scouts probe; they never modify the repo tree.
- The full gate (pytest + ruff + self-lint) is non-negotiable at gate stages and the
  final split stage.
