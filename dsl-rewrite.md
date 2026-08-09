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
- *(substrate fix, 2026-08-04, phase 2)* `binder/scopes.py` now binds typed
  variadic parameters (`*args: T` / `**kwargs: T`), whose identifier
  tree-sitter nests inside `list_splat_pattern`/`dictionary_splat_pattern`;
  untyped variadics always bound correctly. This shifts the **frozen
  oracle's observable output** without touching a frozen path:
  `no-argument-mutation` (and any rule reading parameter symbols) now fires
  on typed-variadic cases it silently missed. Both engines read the same
  binder, so the differential harness is blind to the shift by construction —
  this entry records that the reference behavior moved, and why: the old
  output was a binder bug, not rule semantics. Phase 3 ports are graded
  against the fixed substrate.
- *(divergence, phase 3b)* `test-only-production-code` message drops the
  reference count. The old wording is ``'<id>' is referenced only from tests
  (N test references)``; the port emits ``'<id>' is referenced only from
  tests``. A count is not a fact about the row — it is an aggregate over the
  reference set the rule quantified over — and a `DslRule` message is a
  `str.format` template over the row's visible fields by design (fork #9: a
  callable message would put rule semantics where the derivation tree cannot
  describe them). Adding an annotate stage purely to carry an integer into one
  string was rejected as a new stage type bought for one string. Sanctioned by
  forks #6 and #13: baseline identity re-keys to `(rule_id, anchor_id)`, so
  message wording is freely improvable. Declared in
  `scripts/parity-manifest.toml` as `kind = "message"` and **unexercised by
  the oracle** — the rule reports zero findings on target `self`, because the
  materialized target holds only `src/`, so no test path is in the corpus. The
  exact new wording is pinned by `tests/test_dsl_visibility_rules.py` over a
  fixture corpus that makes the rule actually fire.
- *(divergence, phase 3b)* `born-private` does not self-seed the symbol
  baseline. The old rule writes every current public symbol id into
  `.pypeeker/check-baseline.json` on the first run against an unseeded project
  and returns no violations; the read half of the DSL has no mutation
  terminals, so the port reads the baseline and cannot create it. **The
  divergence is the write, and only the write — the findings agree in every
  baseline state**, because the port ports the early return as well as the
  exemption: `in_set(Const("symbols"), BASELINE_NAMESPACES)` is the head
  conjunct of `born_private`, reproducing `has_symbol_baseline` down to its
  documented edge (a seeded-empty `"symbols": []` is armed, an absent namespace
  is not), so an unseeded project yields nothing on both sides, a seeded one is
  compared against the same recorded set on both sides. The rule is therefore
  **claimed**. An earlier draft of this entry left it unclaimed on the grounds
  that its 0-vs-0 came from `run_old_engine` seeding the file `run_new_engine`
  then reads; expressing the gate is what removes that dependency, and the
  claim rests on agreement under either run order rather than on the harness's
  current one. Without the gate the port reports **9 findings on this
  repository where the old engine reports 0** — that measurement is what the
  gate exists to answer, not a divergence being tolerated. What remains for
  phase 4's mutation terminals is seeding itself: a first run leaves the
  ratchet unarmed here where the old engine arms it, so a *second* run of the
  old engine flags newly-public symbols that a second run of the new engine
  still will not. Unobservable to the oracle, which materializes a fresh target
  per run.
- *(divergence, phase 3b)* `test-only-production-code` excludes definition
  sites by `ReferenceKind.DEFINITION` alone. The old rule's
  `_is_definition_site` *also* discards a non-`DEFINITION` reference located at
  the symbol's own start position — a test correlated with the candidate row,
  which a pointwise predicate over the references universe cannot express (the
  reference set is projected once for the whole corpus, before any candidate is
  known). Where such a reference exists in a production file the old engine
  ignores it and may fire; the port counts it as production use and stays
  silent, so the port under-fires rather than over-fires. Unexercised on this
  repository (the rule reports zero findings on both sides). The rest of the
  inversion is exact, not approximate: `references_to_definition(id)` is
  defined as every reference whose `resolve_reference` equals
  `resolve_definition(id)`, which is the port's forward map read backwards.
- *(divergence, phase 3b)* `test-only-production-code` matches test globs
  against the indexed path as recorded. The old `_is_test_path` first rewrites
  `\` to `/`; `Expr.matches` is plain `fnmatchcase` and does not. The binder
  records indexed paths with forward slashes on every platform (the same
  property `dsl/differential.py` relies on for its JSON `path` field), so the
  two agree wherever pypeeker actually runs; the normalization was defence
  against a path shape the model does not produce.
- *(spec note, phase 3b)* The family's projected sets drop ids that resolve
  outside the corpus, where the old engine's inline sets keep them:
  `follow("definition")` yields a row only when `Corpus.locate` finds the
  target, while `resolve_definition`/`resolve_reference` return an id whether
  or not anything in scope declares it. No membership decision changes,
  because every key tested against these sets is an in-corpus symbol id and a
  dropped id can never equal one. Measured on this repository at the change's
  own head (the diff adds 29 barrel exports of its own, so pre-change numbers
  do not reproduce): the barrel export set is **308 ids on both sides,
  set-identical**; the referenced set is 9027 old vs 8561 new, and all 466
  extra ids fail `Corpus.locate` (outside the corpus).
- *(spec note, phase 3b)* The library-mode `protected` clause
  (`check.rules._public_root_protected`) is omitted from the four symbol-side
  rules of the visibility family — `unused-public-symbol`,
  `over-exposed-module-symbol`, `born-private`,
  `test-only-production-code` — and implemented in `over-exposed-export`,
  which is the only one it can reach. `protected` is a subset of the barrel
  export set by construction (both are `resolve_definition` of the `IMPORT`
  symbols in an `__init__.py`; `protected` merely filters those barrels by
  public root), and all four test barrel membership unconditionally first, so
  the clause can never decide anything there. The frozen docstring says so
  itself: "today subsumed by the unconditional barrel exemption above". This
  is a proof rather than a measurement — this repository is app mode, so the
  clause is empty on both sides here and the oracle grades none of it — and it
  is recorded because it stops holding the moment the barrel exemption becomes
  conditional.
- *(spec note, phase 3b, reconciled TASK-165)* Files with no MODULE symbol: the
  frozen visibility-family rules skip such files outright (`module_id is None
  → continue`), while `dsl/universes.py`'s `_Env.of` substitutes
  `index.file_path` for the missing module id, so DSL rows from such a file
  carry a file path in `row.module` and reach the candidate clauses.
  `dsl/columns.py`'s `_modules_by_file` ports the None-skip correctly, so the
  port currently disagrees with itself on this edge. Unreachable on this
  repository (every indexed file has a MODULE symbol; the oracle cannot grade
  it) and unobserved on any target. Reconciled in TASK-165 by adding
  `dsl/visibility.py`'s `MODULE_FILES` — a `ProjectedSet` of the file paths
  bearing a MODULE symbol — and testing candidate rows against it with
  `in_set(row.file_path, MODULE_FILES)` at the exact position of the frozen
  `module_id is None → continue`: second clause of the shared
  `_candidate_clauses` prefix (right after the `__main__.py` exclusion) and
  second clause of `over_exposed_export`'s own list (right after its
  `__init__.py` test). `_Env.of`'s `index.file_path` fallback is kept
  deliberately, not dropped — it is a display value that keeps every
  `symbols()` row's `module` field uniform for every consumer, module-less or
  not, and dropping it would instead flip a module-less file's top-level
  symbols to `is_module_level = True`, pushing them *into* the candidate
  clauses. Parity-neutral on all four differential targets, verified by a
  full `scripts/differential-check.py` run with unchanged per-rule counts on
  every target (the edge is still unreachable on real corpora, so the
  differential exercises the unaffected majority path, not the fix itself);
  the reconciliation is instead locked by
  `tests/test_dsl_visibility_rules.py::test_every_rule_in_the_family_wires_module_files_into_its_candidate_clauses`
  — the structural check that each family rule carries the `MODULE_FILES`
  semi-join. The behavioural module-less-`FileIndex` tests (built on a
  root-level `__init__.py`, whose module path collapses to `""`) pass even
  without the fix on today's binder, because `is_module_level` already masks
  those rows (`env.module` is a file path, `parent_scope_id` is `""`); they
  pin the *outcome*, while the structural test is what fails if the clause
  is removed.
- *(substrate fix, 2026-08-08, phase 3c)* `binder/scopes.py` and
  `binder/scope_stack.py` now bind PEP 695 inline type parameters —
  `def f[T]`, a method's own `[T]`, `class C[T]`, and `type X[T] = ...` — as
  `SymbolKind.TYPE_PARAMETER` symbols in the defining function's/class's own
  scope (a `ScopeStack.resolve` class-scope skip now makes a
  TYPE_PARAMETER-only exception so a class's type parameters stay visible
  from nested method bodies, matching PEP 695's implicit annotation scope),
  and a `type X[T] = ...` statement — which owns no scope of its own — gets
  an explicit `ScopeKind.TYPE_PARAMS` scope plus a `SymbolKind.VARIABLE`
  symbol for the alias name itself. A generic definition's *header* (bounds,
  base-class list, return annotation, alias value) keeps binding in the
  **enclosing** scope, with the type parameters overlaid, so no existing
  reference moves scope: PEP 695 annotation scopes see the enclosing class
  namespace, and `analysis/hierarchy.py` still identifies a base-class
  reference by `ref.in_scope_id == class_symbol.parent_scope_id`. This shifts
  the **frozen oracle's
  observable output** without touching a frozen path: `no-unresolved-refs`
  stops firing on PEP 695 code it used to flag (previously worked around in
  `dsl/corpus.py`'s `memo` method with a module-level `TypeVar`, now restored
  to `def memo[T](...)`), the model gains TYPE_PARAMETER symbols and a
  TYPE_PARAMS scope kind that did not exist before, and the visibility family
  can now see a module-level generic type alias as a public VARIABLE where it
  previously saw nothing. Both engines read the same binder, so the
  differential harness is blind to the shift by construction — this entry
  records that the reference behavior moved, and why: the old output was a
  binder gap (tree-sitter-python already exposed the `type_parameters` field;
  the binder never walked it), not rule semantics. Measured on this
  repository: `scripts/differential-check.py` reports PASS on every
  target/rule pair with identical old/new counts, and the self-lint gate
  (`pypeeker check` over `src/`) stays at zero findings.
