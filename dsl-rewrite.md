# DSL rewrite — decision record and execution plan

**Status: active program.** This document is normative for the rewrite: the
decision, the fork resolutions, the freeze, the differential oracle, and the
phased plan the task pipeline executes. The divergence ledger at the bottom is
the only sanctioned way to change frozen-path behavior. History of how these
decisions were reached: the four-proposal UX panel (run `wf_db0f1672-e0e`) and
the preceding design conversation; this file records outcomes, not transcripts.

## The decision

pypeeker's rule/check layer is rewritten around an embedded Python DSL in which
**a rule is a selection expression, a fix is a mutation value, and the two
compose as a cross product** — replacing the current arrangement where every
rule is a hand-written query and every rule/fix pair is plumbing across the
`check`/`refactor` wall. Backwards compatibility is explicitly **not** a
constraint (nothing external consumes pypeeker's output today); behavioral
fidelity during the transition is guaranteed by a differential oracle instead.

**What is rewritten:** `check/` (engine, registry, `rules.py`, `builtin/`,
`baseline.py`, `demotion.py`) and the `app/` plumbing that joins findings to
fixes (`check_fixes.py`, `privatize.py`). **What is not:** the binder, `models/`,
`analysis/` (the trait registry is shared substrate the DSL registers into),
`intents/`, `refactor/` planners, the batch scheduler, overlay simulation,
`storage/`, and the transaction lifecycle. The four nouns stay; the roles get
re-expressed.

## Fork resolutions (settled — do not re-litigate)

| # | fork | resolution | reason |
|---|---|---|---|
| 1 | Adoption model | Full rewrite of the check layer; new packages beside frozen old ones; **no** `native()` lift, no ratchet | No external consumers; the differential oracle replaces incrementalism |
| 2 | Gate enforcement | Confidence floor lives on the shared mutation value; divergence is structurally unwritable (one operation = one mutation object = one intent kind) | The historical demote/privatize drift was two implementations of one operation |
| 3 | Optimizer | **None.** Clauses evaluate in written order | `register_trait` allows provider replacement, and order-dependent evaluation would make rule meaning depend on the runtime |
| 4 | Confidence composition | Meet (min) over **every** contribution on `DECLARED > INFERRED > HEURISTIC`, order-independent. Stated law: **reading `.confidence` is a DECLARED meta-read** | Without the meta-read law, `prefer-tuple` meets down to INFERRED and autofix silently dies |
| 5 | `intent_id` / `fix_id` | Purely derived (`<rule>:<mutation>:<anchor>`), no override | The override existed to preserve contracts nobody consumes |
| 6 | Baseline identity | Keyed on `(rule_id, anchor_id)`, **not** message text; message wording becomes freely improvable | Message-keyed baselines were a discovered fragility, not a design |
| 7 | Provenance | Exposed: `--why` returns the derivation tree as versioned, additive-only JSON | The product thesis is LLM consumers; an agent cannot attach a debugger |
| 8 | Universes | Five: symbols, references, imports, modules, scopes. `representative_file` and friends are primitive traits, not language features | Two-universe austerity cannot express `import-boundaries` or `_representative_file` |
| 9 | Escape hatch | `where()` rejects bare callables; the escape is a named wrapper with mandatory `reads=` declaration | Scheduling and inspectability survive only if opacity is declared |
| 10 | Composite mutations | v1 composites resolve to **existing** planner kinds, erroring at construction; `Intent` is designed so a composite is an intent (union footprint, conjoined preconditions), but no composite planner is built | `ExtractMethodPlanner` has no `self` handling; minting a kind before its planner exists is vaporware |
| 11 | Fixpoints | `in_cycle`-style constructs are primitive traits (hand-written Python declaring own confidence), same tier as model-reading primitives | Per-node expressions cannot express convergence |
| 12 | Anchors | Evidence-typed: a CLI-typed symbol id is DECLARED evidence; a finding-derived anchor carries the finding's confidence. Unresolved/ambiguous lookups are **loud structured errors**, never empty results | Dissolves TASK-149 rather than patching it; kills the silent-`[]` class (TASK-150) |
| 13 | Message templates | Free to change (consequence of #6) | — |

Convergences adopted wholesale from the panel: mutation as named top-level
value; confidence floor as an attribute of the mutation, never an option at the
application site; the application operator takes no options; scope derived from
the expression, never declared; the barrel exemption is a semi-join on one
projected id column, materialized once per run.

## The freeze

The old check layer is **frozen, not evolved** — it is the executable spec the
new engine is graded against, and editing the spec while porting it destroys
the oracle. Frozen paths:

```
src/pypeeker/check/**
src/pypeeker/app/check_fixes.py
src/pypeeker/app/privatize.py
```

Enforcement, in layers (outermost is authoritative):
1. **CI guard** (`scripts/check-frozen-paths.sh`, pull requests only): fails any
   PR that touches a frozen path unless the same PR also modifies this file
   (i.e. carries a ledger entry). This is the real enforcement.
2. **Claude settings** (`.claude/settings.json` permission deny): `Edit`/`Write`/
   `NotebookEdit` are denied on frozen paths for every session and pipeline
   agent. Reads remain allowed — the port tasks need the spec — but agents
   should prefer the old engine's *output* over its source, and ranged reads
   over whole files, per the standing reading discipline.
3. **Bash guard hook** (`.claude/hooks/frozen-paths-guard.sh`): best-effort
   block of shell write patterns (`sed -i`, redirection, `rm`/`mv`/`tee`)
   targeting frozen paths. Fail-open by design; CI is the backstop.

**Exception process:** a genuine bug in the oracle (the panel found candidates)
may be fixed on a frozen path only together with a ledger entry below stating
what the oracle got wrong and how the differential comparison accounts for it.

## The differential oracle

`scripts/differential-check.py` (built in phase 1) runs the old engine and the
new engine over this repository and the test fixtures, and compares findings
**per rule** against a parity manifest — the list of rules the new engine
currently claims. CI fails if any claimed rule's findings differ from the old
engine's, except where a ledger entry declares the divergence. Parity for all
22 rules is the precondition for the flip. The old `check` remains the
self-lint gate for the entire window; the new engine is graded by the thing it
replaces.

## Phased plan (executed via task-pipeline v4)

Phase 0 — **Freeze and guards** (this commit, done inline): frozen paths
declared, settings deny, bash guard, CI guard, this document.

Phase 1 — **Differential harness** (frozen test policy): the oracle exists
before anything it measures. Harness + parity manifest + CI wiring + empty
ledger verified to pass with zero claimed rules.

Phase 2 — **Read half** (one pipeline arc, frozen policy): the `dsl/` package —
five universes, `where`/`follow`/`project`, predicate grammar, evidence
lattice with the meta-read law, `reads=`-declared escape, derived scope;
traits as named expressions registering into the existing `analysis/traits.py`
registry; loud evidence-typed anchor resolution; versioned `--why` provenance.
No mutation terminals.

Phase 3 — **Rule library port** (three parallel worktree pipelines, frozen
policy, differential gate per rule): (a) the file-local registry/builtin rules;
(b) the visibility/reference-counting family including the barrel semi-join;
(c) the primitive-tier family — import-boundaries, cycles, purity. Every
divergence from the old engine lands in the ledger, not in review comments.

Phase 4 — **Mutation terminals** (frozen policy): mutation values with
confidence floors, application producing intents into the existing batch
machinery, `demote`/`privatize` as two selections over one shared mutation,
`check --fix` driven through the new engine behind the differential gate.

Phase 5 — **The flip** (migrate + port policy, the program's one big-bang
moment): the self-lint gate switches to the new engine; CLI commands re-wire
to named expressions; baseline keying changes to `(rule_id, anchor_id)`;
frozen paths are **deleted in the same PR**; old-engine tests are ported
scenario-by-scenario per the `port` policy; CLAUDE.md and architecture.md
updated. After this PR, no scar remains: package names carry no version.

Notes: TASK-149 and TASK-150 remain open as small standalone fixes to the
*surviving* CLI paths (neither touches a frozen file); phases 2 and 4 make
both structural, and they close at the flip if not before. TASK-145/147
(envelope) are unaffected and stay parked on their own merits.

## Divergence ledger

Deliberate behavioral divergences between the old engine and the new one, and
sanctioned oracle fixes. **Append-only; every entry needs the rule, the
difference, and the reason.**

- *(planned, lands at flip)* `fix_id` becomes purely derived; any current id
  that deviates from `<rule>:<mutation>:<anchor>` changes accordingly.
- *(planned, lands at flip)* Baseline identity re-keys from normalized message
  text to `(rule_id, anchor_id)`; existing baselines (none known in the wild)
  do not carry over.
- *(spec note, not a divergence)* `no-import-cycles` deferred-import semantics:
  an import is load-time iff **every** enclosing scope up to the module is a
  module or class body; `function`, `lambda`, **and `comprehension`** anywhere
  on the chain defer it. The port must preserve this exactly — the panel's
  strongest proposal got all three points wrong.
- *(spec note)* `prefer-tuple` reports at DECLARED via the meta-read law
  (fork #4); a port that meets to INFERRED is wrong, not divergent.
