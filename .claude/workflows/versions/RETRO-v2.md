# Retro: task-pipeline v2 (the conductor version)

Frozen alongside `task-pipeline-v2.js`. Evidence base: the three conductor-managed
runs (TASK-133 solo; TASK-132 ∥ TASK-134 in parallel worktrees), the plan-mode smoke
test, and the eleven migration-era workflow runs the pipeline was distilled from.

## What worked — keep in every future version

- **Probe-first scouting.** Scouts that execute probes instead of reading beat every
  other quality lever: the smoke scout proved the tree.json write-through with a live
  probe; 133's scout probe-disproved its own predecessor's import design (F821 +
  no-import-cycles); 132's scout disproved advisory 4 with evidence and found the
  opposite bug. Scout prompts must keep the explicit "probes encouraged" clause.
- **The conductor genuinely adapts.** Three runs, three different shapes (sonnet/2-stage/
  no-plan-review; opus/3-stage/plan-review ×2), each matching the task's real risk.
  Its reasoned *skip* of plan review on 133 was as valuable as its arming decisions.
- **The re-review stage.** On its first triggered outing (132) it caught a must-fix the
  fixer's own diff introduced (guarded destination binding counted as "absent" → double
  import). The fixer is otherwise the only unreviewed writer in the pipeline.
- **Frozen-oracle test policies + the gate checking policy compliance**, not just
  command exit codes.
- **Worktree parallelism.** Two mutating pipelines ran concurrently and merged cleanly;
  the combined-state gate before the final PR is the necessary closing step.
- **Tool-failure escape hatches + BLOCKED early-abort.** Never needed again after the
  one transient failure, which is the point.

## What to improve — applied in v3

1. **Agents did bookkeeping, and it drifted.** 132's `records-versus-reality` lens
   caught the implementer writing *wrong gate numbers* into backlog notes. Bookkeeping
   belongs to the orchestrator after independent verification. → v3 SPEC bans pipeline
   agents from editing `backlog/`.
2. **Re-review was pre-authorized blind.** The conductor arms it before any findings
   exist. Evidence says its value tracks must-fix count (132: triggered after 3
   must-fix, caught a 4th; 133/134: zero must-fix, nothing to re-review). → v3
   auto-triggers re-review when must-fix ≥ 2, in addition to conductor arming.
3. **Full-lens re-review is diffuse.** 132's re-review re-ran all four lenses; the
   must-fix came from the lens family that had already fired, while the rest returned
   doc-nits. → v3 re-reviews only the lenses that produced must-fix findings, plus one
   dedicated fix-audit lens with fresh eyes on the fixer's diff.
4. **Split stages each ran the full gate.** Three implement stages × full suite +
   the haiku gate re-run made 132 a 2.3-hour, 19-agent, 2M-token run. The haiku gate
   and the orchestrator still run the full gate, so intermediate stages don't need to.
   → v3 tells non-final split stages to run targeted tests + ruff only; the final
   stage and every gate agent still run the full gate.
5. **Conductor guidance was static prose in the script.** It goes stale as evidence
   accumulates, and editing the script to update guidance conflates code and knowledge.
   → v3 has the conductor read `.claude/workflows/PIPELINE-GUIDANCE.md` first — a
   living doc each retro updates. This closes the improvement loop: retro → guidance →
   next run's conductor.
6. **No cost signal.** The conductor had no sense that split × lenses drives cost
   (132: 19 agents/2M tokens; 134: 11/1.2M for comparable scope). → v3's guidance
   includes the observed cost envelope and a "smallest shape that protects quality"
   instruction.

## Watch items — not changed in v3, revisit at the next retro

- **Plan JSON size in prompts.** 50–80KB plans are inlined into conductor + implementer
  prompts. It works and guarantees the plan is seen; if costs grow, move to a
  scratch-file reference (the TASK-133 prior-plan handoff proved that pattern works).
- **Worktree lifecycle is manual orchestrator work** (add, fence, merge, combined gate,
  remove). Scriptable if parallel runs become routine.
- **Advisory findings need systematic filing.** The 0-must-fix advisories seeded
  TASK-132/133/134 — valuable — but their filing depends on orchestrator discipline.
- **Lens-key attribution** (added in v3 for focused re-review) also enables per-lens
  hit-rate stats over time; collect before tuning lens defaults.
