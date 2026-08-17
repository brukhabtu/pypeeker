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
- *(divergence, phase 3d)* `under-exposed-access` matches its test globs
  against the indexed path as recorded. The frozen rule first rewrites `\` to
  `/` (`check/builtin/visibility.py`'s `path = index.file_path.replace(...)`);
  `Expr.matches` is plain `fnmatchcase` and does not. Identical in substance and
  in reason to the `test-only-production-code` entry above: the binder records
  indexed paths with forward slashes on every platform, so the two agree
  wherever pypeeker actually runs, and the normalization was defence against a
  path shape the model does not produce. Graded rather than argued — the rule
  reports 77 findings on target `self`, 8 of them through the test-path branch,
  and the differential compares them all.
- *(spec note, phase 3d)* `under-exposed-access` resolves a reference's target
  through `Corpus.locate`, which elects the **first** colliding file in
  indexed-path order, where the frozen rule's `_symbols_by_id` dict
  comprehension elects the **last**. Observationally neutral, and the argument
  is worth recording because a count-based comparison could not see it fail:
  the port reads exactly two things off the target, its `name` and its
  `visibility`, and both are functions of the symbol id alone. The id's
  trailing segment *is* the name (`module:Scope.Chain:local`, `$N` shadow
  suffix included on both sides of a collision), and
  `adapters.python_adapter.get_visibility` is a pure function of the name. The
  third column the rule consults, `DEFINITION_KIND`, is used only as the
  locatability test `target is None -> continue`, which is true under either
  election because both candidates are in the corpus. Kind itself could differ
  between two colliding declarations and is never read. The two elections are
  deliberately not reconciled — `dsl/corpus.py:139` documents why both exist.
- *(spec note, phase 3d)* `under-exposed-access`'s dunder exclusion is spelled
  `all_of(startswith("__"), matches("*__"))` over the definition's name, and
  **not** the shorter `matches("__*__")`. The single glob needs four
  characters, and names of two and three underscores reach the clause:
  `get_visibility`'s `len(name) > 4` guard classifies `__` and `___` as
  `PRIVATE` rather than `DUNDER`, so they survive the visibility test one
  clause earlier, and the frozen `_is_dunder` — `startswith("__") and
  endswith("__")`, no length test — skips them. A port using the four-character
  glob would fire where the frozen engine is silent. Recorded as a spec note
  rather than left to the code, so that a later "simplification" to the single
  glob is expensive; it is also pinned by a test in
  `tests/test_dsl_crossfile_rules.py`.
- *(divergence, phase 3d)* `star-imports` emits no remedy. The frozen rule
  attaches a `RewriteStarImportIntent` (`check/builtin/star_imports.py`'s
  `with_remedy` call) to the subset of its findings that are safely
  rewritable — a file with exactly one star, so the tier is `DECLARED`, whose
  target is indexed and supplies at least one used name. The read half has no
  mutation terminals, by phase 2's definition, so the port emits the finding
  and not the repair. **The divergence is the remedy, and only the remedy**:
  all five compared fields — rule, path, line, message, confidence — agree on
  every finding, on `crossfile` (5 findings, including the one that would carry
  a remedy) and on `filelocal` (1, which would not). The oracle is blind to it
  by construction, since `dsl/rules.py`'s `Finding` deliberately holds no
  remedy and no fix id; this entry is the record that it is absent rather than
  overlooked. Same class as `born-private`'s missing self-seed above — a
  difference in *effect* rather than in output — and it closes in phase 4, when
  the mutation terminals arrive and `intent_id` becomes the derived
  `<rule>:<mutation>:<anchor>` of fork #5. Until then the row source carries
  everything the remedy needs (the star's own symbol id as the row's anchor,
  and `imported_from`), so restoring it adds a terminal rather than a field.
- *(spec note, phase 3e)* `no-hidden-global-mutation` collects its violations
  into a **`set`** (`violations: set[Violation]`, returned `sorted()`), where
  the port emits one row per match and the oracle compares multisets. Two
  violations identical in file, line, rule and message therefore collapse to
  one on the frozen side and stay two on the port's. It is reachable in
  principle — `CACHE[k] = CACHE[j] = v` is two module-scope subscript writes on
  one line producing one message, and so is `ITEMS.append(x); ITEMS.append(y)`
  — and it is absent from all seven differential targets as measured, including
  the `mutation` corpus, which deliberately does **not** contain the shape: a
  fixture whose only job is to make the two engines disagree would fail the
  gate it was added to. Not expressible as a ∀-query without a deduplicating
  stage, which would be a new stage type bought for a shape no corpus has. This
  is the same class of thing as the `star-imports` remedy note: a difference in
  *aggregation* rather than in which rows fire.
- *(spec note, phase 3e)* One `no-hidden-global-mutation` collapse is worse
  than a doubled row: shape 3 resolves its display name through a
  `(line, attribute) → imported name` map that is **last-wins**
  (`_import_attribute_write_names`), so two import-rooted writes sharing a
  line and an attribute while rooting at *different* imports (probed:
  `os.environ["A"] = pp.environ = "1"` with `import posixpath as pp`) produce
  **one** frozen finding naming the *last* writer's module — a name the other
  write is not rooted at — where the port emits one row per write, each naming
  its own root. So the frozen side both under-counts and mis-names; the
  previous entry's "identical messages collapse" covers the multiplicity but
  not the wrong surviving name, which is why this is recorded separately.
  Absent from all seven targets as measured; the port's behavior is the more
  correct one.
- *(spec note, phase 3e)* Both mutation rules resolve each function id through
  `SemanticQueryEngine.find_symbol`, which is **project-wide** and matches by
  exact id or by dotted suffix (`symbol_id.endswith("." + name)`). Whenever
  that resolution lands on a different file than the one being iterated, the
  frozen rules scan the *resolved* file's references and never the iterated
  file's own — so the iterated file's mutation sites are silently missed, and
  the resolved file's may be emitted once per iterated twin. Two layouts
  trigger it: a module-id collision (two indexed files sharing one module
  path, the shape `tests/fixtures/parity/boundaries` carries on purpose), and
  — needing no collision at all — a module whose dotted path is a suffix of
  another's (`b.mod` beside `a.b.mod`: `find_symbol("b.mod:f")` accepts
  `a.b.mod:f` via the suffix match, an ordinary consumer layout). On top of
  that, `no-argument-mutation` mixes symbol tables: its `symbols_by_id` comes
  from the file being **iterated** (`no_argument_mutation.py:84`) while the
  references come from the file the id **resolved to** (`:97`), so it
  classifies the resolved file's references against the iterated file's symbol
  table — a receiver root id present in both files is read as whatever kind
  the iterated file gave it. The frozen `no-hidden-global-mutation` does
  **not** have that mixed-table half — it rebuilds `symbols_by_id` from
  `ctx.file_index` (`:94`), so both halves come from the resolved file — but
  the missed-file/doubling half applies to it equally. The port's rows are
  same-file throughout — `dsl/universes.py`'s `_reference_record` resolves
  every symbol through the row's own `_Env` — so it cannot reproduce any of
  this, and reproducing it would mean porting a bug. Unreachable on all seven
  targets as measured (the `boundaries` twins contain no mutation of any
  shape, and no target carries a suffix pair), which also means the oracle can
  never catch a regression here; whoever adds either layout *with* mutations
  in it will see these fire.
- *(spec note, phase 3e)* The port reports `no-hidden-global-mutation` at the
  **reference's** file path, where all four frozen shapes report at
  `ctx.function_symbol.location.file_path`. Identical wherever the previous
  entry's precondition holds, because a reference and the function whose scope
  subtree contains it are in one index; they can only differ under the same
  project-wide resolution that entry describes. Recorded next to it so the two
  are read together.
- *(spec note, phase 3e)* The `references` universe gained eleven fields for
  this port — `receiver_chain`, four `receiver_root_*`, three `binding_*`, three
  `enclosing_function_*` — as a deliberate universe-surface extension rather
  than a bespoke row source. Each is a per-file model fact published raw, and
  the two parent-scope ids are published raw **specifically** rather than as an
  `is_module_level` boolean: the frozen `_is_module_scope` is `":" not in
  scope_id` while the symbols universe's `is_module_level` is
  `parent_scope_id == env.module`, and on a source root that is itself a package
  (the `purity` corpus) the first is true where the second is false. The
  reference's own symbol is published as `binding_*` and not `target_*` because
  `dsl/visibility.py` already derives `target_name`/`target_module`/
  `target_visibility` on these rows from the **project-wide** definition column,
  which names a different symbol whenever a reference crosses a file.
  Measured cost of the extension on the pre-existing eighteen rules over target
  `self`: 16.2s before, 16.4s after (three runs each), i.e. inside the noise —
  `_Env`'s symbol map and enclosing-function memo are built lazily, so a rule
  that reads none of the new fields pays for none of them, and a full
  seven-sweep row build over this repository's 38948 references spends 1.0s
  total of which 0.07s is the new work.
- *(spec note, phase 3f)* A fact may now be read **about an entity a row
  names**, not only about the row itself: `fact_of(SPEC,
  params).about(anchor_expr).value`. `FactRead` gained an optional `anchor`
  expression, declared as its **last** field so the positional construction
  `FactRead(spec, params, "value")` that `FactAccess.value` and the pre-existing
  tests use keeps binding the projection; `EvalContext.fact` forwards the third
  argument only when it is non-`None`, so a two-argument `FactResolver` (which
  `tests/test_dsl_facts.py` builds) still satisfies the protocol. The anchor is
  a real child node, so `Selection`'s `_field_reads` validation, `Expr.reach`,
  `fact_specs`' walk and `--why`'s derivation tree all see it with no further
  change, and its confidence meets into the read like any other operand. A
  non-string anchor value (`None`, `UNMATCHED`) reads as no entry — `None` at
  `UNKNOWN` — without consulting the table, so a lazy table pays nothing for a
  row whose anchor did not resolve. `import-time-side-effects`' shape 3 is what
  needs it: the finding is about the **call site** (its path, its line, its
  baseline identity) while "is it impure" is about the **function the call
  resolves to**. Three alternatives were rejected, each for a specific reason:
  a bespoke row source anchored at the definition id makes the anchor dishonest
  (two call sites to one function collapse onto one baseline key); a project
  column cannot carry the policy, because `COLUMNS` is closed and parameterless
  while the impurity fact is shaped by `extra-impure`; and computing the
  impurity inside a row source would pull the shape-1/shape-2 partition into
  the sweep, to avoid paying for rows those shapes own — exactly the
  rule-semantics-in-the-sweep leak the expression tier exists to prevent. A
  `follow` to the definition symbol was rejected too: it discards the call
  site the finding must report at. The extension is output-neutral — no
  existing expression carries an anchor, and the non-anchored derivation
  payload is byte-identical (`detail["anchor"]` is emitted only on anchored
  reads).
- *(spec note, phase 3f)* The `references` universe gained two more fields for
  this port: `runs_at_import` — the frozen
  `import_time_side_effects._import_time_scope_ids` predicate asked of one
  scope at a time — and `module_scope_id`, the frozen `_module_path`. Publishing
  the scope-walk *pointwise* rather than as a project-wide `ProjectedSet` of
  import-time scope ids is not a style choice: scope ids are `<module>:<chain>`
  and a module id is not injective over indexed files (the `boundaries` corpus
  carries twins under `src/app/twin/`), so a union of import-time scope ids
  across the project would admit one file's **function body** because its twin
  declares a class body of the same id. The frozen rule builds the set
  per-index and is immune; only a project-wide set would be wrong.
  `module_scope_id` is likewise **not** `row.module`: `module` is the MODULE
  *symbol*'s id with a **file-path fallback**, and `binder.binder`'s
  `_emit_module_symbol` emits no MODULE symbol at all when the module path is
  empty — the source-root-is-a-package shape the `purity` corpus carries — so
  `module` would hand an `allow` pattern a file path where the frozen
  `_module_path` hands it `""`. Both fields are per-file model facts computed
  behind `_Env`'s existing lazy `_cache`, so a rule reading neither pays for
  neither. One deviation from a faithful copy is deliberate and recorded on the
  method: the port's walk carries a `seen` cycle guard where the frozen
  recursion would `RecursionError`, unobservable on acyclic binder output.
- *(spec note, phase 3f)* `_qualified_call_name`'s `ref.receiver_chain is None`
  guard is deliberately **not** ported.
  `binder/references.py`'s `receiver_metadata` returns a non-`None` receiver
  root only together with a non-empty chain, so the guard can never decide
  anything the root test has not already decided; porting it would claim the
  rule asks a question it never asks. Recorded in the same spirit as phase 3e's
  unported `local_symbol_ids` clause. The two other frozen guards in that
  function — `receiver_root_symbol_id is None` and the `symbols_by_id` miss —
  are *subsumed* rather than dropped: both leave `row.receiver_root_kind` at
  `None`, which the `is not SymbolKind.IMPORT` test rejects, and the field comes
  from the same per-file `{s.symbol_id: s for s in index.symbols}` lookup the
  frozen rule builds.
- *(spec note, phase 3f)* `import-time-side-effects`' shape 3 elects its target
  definition differently from the frozen rule, in **two** ways. The frozen
  `_project_functions` builds a dict over **FUNCTION/METHOD symbols only**,
  last-wins (a plain comprehension into a dict); the port reads
  `column_of(DEFINITION_KIND)`, which goes through `Corpus.locate`, a table over
  **every symbol kind**, first-wins in sorted indexed-path order. So under a
  module-id collision two things can differ: (a) between two colliding
  *functions*, the port quotes the first file's `kind.value` where the frozen
  quotes the last file's; and (b) a **non-function** symbol declared in an
  earlier-sorted file can shadow a FUNCTION of the same id in a later one, at
  which point the port's `is_in(FUNCTION, METHOD)` clause fails and a frozen
  finding is dropped. Both directions require a corpus with colliding module ids
  *and* an import-time call resolving into the collision; no target carries that
  layout, so the oracle can never catch a regression here and the argument has
  to hold by inspection. It does for (a) — only `kind.value` is read from the
  elected symbol, and the two candidates are both FUNCTION/METHOD by
  construction of the frozen table — but (b) is a genuine behavioural gap,
  recorded here rather than papered over. Reconciling the two elections is the
  same trade `Corpus.locate`'s own docstring declines: unifying them would move
  frozen-engine-observable output for a case with no more-correct answer.
- *(spec note, phase 3f)* `import-time-side-effects`' shape-1 confidence tier is
  **not** spelled as a `weakened_when`, though it is exactly a weakening: report
  either way, label a bare unresolved name `HEURISTIC` and a resolved builtin
  `DECLARED`. `Weaken` is an inventoried node — `DYNAMIC_ACCESS_WEAKENED_RULES`
  enumerates the five visibility rules the frozen engine downgrades through
  `check.rules._dynamic_access_confidence`, and
  `tests/test_dsl_visibility_rules.py` asserts that exactly those five
  expressions carry one — so a rule reaching a tier by its own route must not
  claim membership. That is the call `sweeps.unused_import_rows` already makes
  for `unused-imports`, which computes the same dynamic-access tier inline in
  the frozen engine and carries it on the row rather than as a sixth `Weaken`.
  The port carries it on the lattice instead: `any_of` meets over the branches
  that hold, the two branches partition every row, and the levels ride on
  `Const`'s own `confidence`. Value is unconditionally `True`, so it filters
  nothing — `Weaken`'s contract, from primitives. Output-identical; the oracle
  grades both tiers on the `impurity` target and
  `tests/test_dsl_rules_impurity.py` pins them side by side.
