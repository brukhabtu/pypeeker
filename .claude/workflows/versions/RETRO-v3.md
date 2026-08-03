# Retro: task-pipeline v3 (the guidance-driven version)

Frozen alongside `task-pipeline-v3.js`. Evidence base: nine v3 runs — TASK-135,
136, 137, 138, 139, 140, 141, 146, and the bundled 142/143/144 envelope arc —
plus the first quantitative evidence we have ever had about what these runs
actually cost (`TOKEN-COSTS.md`, `ENVELOPE-COUNTERFACTUAL.md`).

## What worked — keep in every future version

- **Probe-first scouting is still the highest-value stage, and it kept finding
  more than it was sent for.** TASK-138's scout built a nine-shape probe matrix
  and *executed the moved code* to prove a `NameError`, turning a filed
  hypothesis into a confirmed correctness bug that also bypassed a guard built
  two tasks earlier. TASK-140's scout found an escaping-rename path that
  silently moved files outside the project — nobody asked about it. TASK-141's
  scout classified every raw decode in `src/` with structural arguments rather
  than sampling. The envelope scout built a scratch package skeleton and ran the
  real gate binaries against it rather than reasoning about what the rules would
  do.
- **The honesty instruction works, and it is worth writing into every spec.**
  TASK-144 was told "if the answer is bad, say so plainly — a negative result is
  a valid outcome." It returned one: the envelope saves 0.2–0.4% at the
  committed scope, and `uv run pytest` is a *net loss*. It also caught and
  corrected its own methodology error — v1 of the harness projected
  largest-first corpus ratios onto the population and overstated every scope by
  ~3×. Without that instruction we would have shipped a flattering number.
- **Adversarial lenses kept earning their cost.** Across the UTF-8 family they
  found the third and fourth crash sites; in TASK-141's re-review they caught a
  residual `move.py` importer span the fixer had missed; in TASK-138 they found
  three binder-blind binding vectors (match captures, PEP 695 type params,
  unpacking `as`-targets) that would have mis-attributed imports.
- **Escalating frozen-test collisions rather than resolving them.** TASK-137's
  gate flagged a modified frozen test for orchestrator adjudication instead of
  quietly inverting it. The test in question had documented the exact fix it was
  deferring — a judgment call that belonged to a human-facing decision, and the
  pipeline correctly refused to make it alone.
- **Bundling a dependent arc into one run.** 142/143/144 shared a single scout
  instead of paying three to re-derive the same context. On measured per-run
  costs that is roughly $25 of avoided duplication.

## What to improve — applied in v4

1. **Tool-output discipline is absent from `SPEC`, and it is the largest
   measured cost in the system.** `TOKEN-COSTS.md`: 57% of all tool-result
   tokens are file reading — whole-file `Read` at 3,806 tokens average, plus
   1,177 `sed -n 1,220p` calls doing `Read`'s job through the shell at 1,008
   each. `grep` is the healthy contrast at 412. Nothing in any agent prompt says
   a word about this. → v4's `SPEC` gains a reading rule: locate with `grep`,
   read a range with `offset`/`limit`, never read a large file whole, never
   shell out to print lines.
2. **`git diff` is re-run unscoped.** 580 calls, 1,733 tokens average, agents
   re-inspecting their own working diff. → v4's `SPEC` asks for `--stat` first,
   a path argument when the question is about one file, and `-U1` for narrow
   hunks.
3. **`roadmap-plans.md` is the hottest file in the repo** — 208k tokens across
   31 reads, for a document CLAUDE.md explicitly labels "history, not pending
   work." → v4's `SPEC` names it as read-on-demand-only.
4. **Guidance reaches the conductor only, and that was implicit.** The conductor
   reads `PIPELINE-GUIDANCE.md` to choose the run's *shape*; `SPEC` is what
   every agent actually sees. A per-agent behaviour rule placed in the guidance
   doc is inert. This nearly sent the reading rule to the wrong file. → the
   split is now stated at the top of `PIPELINE-GUIDANCE.md`: shape decisions
   there, agent behaviour in `SPEC`.
5. **The cost envelope in guidance was stale and understated.** It quoted 11
   agents/~1.2M against 19/~2M. Observed across v3: TASK-146 at 6 agents/374k,
   TASK-141 at 14/1.56M, the envelope arc at 19/2.29M. → refreshed, with the
   per-run dollar figure so the conductor is choosing against a real number.

## Watch items — not changed in v4, revisit next retro

- **Per-lens hit-rate stats.** Carried from the v2 retro and still uncollected.
  Lens keys have been attributed since v3; nothing aggregates them, so lens
  defaults remain set by judgment rather than by measured yield.
- **Worktree lifecycle is still manual orchestrator work** — add, fence, merge,
  combined gate, remove. Five runs used it this cycle; it is now routine enough
  to be worth scripting.
- **Plan JSON size in prompts.** Unchanged from v2 and still working; the
  envelope arc's plan was the largest yet without incident.
- **No triage rule for pipeline-vs-inline.** TASK-146 spent ~374k tokens (≈$8)
  on a `git mv` plus a `SKILL.md` — work that was inline-sized. Every run pays a
  scout that reads the repo before it knows whether the task is hard. A stated
  rule ("if you can name the exact diff up front, do it inline") belongs
  somewhere, but it governs the *orchestrator*, not the pipeline, so it is not
  a script change and is deliberately left out of v4.
