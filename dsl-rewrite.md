# DSL rewrite — decision record and execution plan

**Status: complete.** Phase 5 — the flip — landed 2026-09-06 under TASK-157,
and this document is now the historical record of the program rather than a
live plan. It keeps the decision, the settled fork resolutions, the freeze and
the differential oracle that guarded the transition, and the phased plan as
executed. The divergence ledger at the bottom is no longer a change-control
mechanism: it is the permanent record of every behavior the rewrite moved, and
the reason the ported code has the shape it has. History of how these
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

The old check layer was **frozen, not evolved** — it was the executable spec
the new engine was graded against, and editing the spec while porting it would
have destroyed the oracle. The frozen paths were:

```
src/pypeeker/check/**
src/pypeeker/app/check_fixes.py
src/pypeeker/app/privatize.py
```

Enforcement was layered (outermost authoritative). Each artifact is named here
so a `git log` search can still find it; all three were retired at the flip:
1. **CI guard** (`scripts/check-frozen-paths.sh`, pull requests only): failed
   any PR that touched a frozen path unless the same PR also modified this file
   (i.e. carried a ledger entry). This was the real enforcement. Deleted at the
   flip, along with its `.github/workflows/ci.yml` step.
2. **Claude settings** (`.claude/settings.json` permission deny): `Edit`/`Write`/
   `NotebookEdit` were denied on frozen paths for every session and pipeline
   agent. Reads stayed allowed — the port tasks needed the spec — but agents
   were told to prefer the old engine's *output* over its source, and ranged
   reads over whole files. The deny entries were removed at the flip.
3. **Bash guard hook** (`.claude/hooks/frozen-paths-guard.sh`): best-effort
   block of shell write patterns (`sed -i`, redirection, `rm`/`mv`/`tee`)
   targeting frozen paths. Fail-open by design; CI was the backstop. Deleted at
   the flip, with its `PreToolUse` registration.

**Exception process (while the freeze held):** a genuine bug in the oracle (the
panel found candidates) could be fixed on a frozen path only together with a
ledger entry below stating what the oracle got wrong and how the differential
comparison accounted for it.

The frozen paths themselves were deleted in the cutover's second segment.
`src/pypeeker/check/`, `app/check_fixes.py` and `app/privatize.py` no longer
exist; `app/privatize.py`'s name was re-taken by the new privatize service.

## The differential oracle

`scripts/differential-check.py` (built in phase 1, with
`scripts/parity-manifest.toml`, `scripts/dsl-engine.py` and
`scripts/dsl-fix-engine.py`) ran the old engine and the new engine over this
repository and the fixture corpora, and compared findings **per rule** against
the parity manifest — the list of rules the new engine claimed at that moment.
Phase 4 added a second **fix pass**, grading the repairs each engine planned
for `check --fix`: fix ids, descriptions, violation lines, both refusal buckets
and the byte-level edits. CI failed on any claimed rule whose findings differed
from the old engine's, except where a ledger entry declared the divergence.
Parity on all 22 rules was the precondition for the flip, and it held on the
harness's last run. The old `check` remained the self-lint gate for the entire
window; the new engine was graded by the thing it replaced.

The oracle was retired in the cutover's first segment, before the CLI was
rewired — its "old side" *was* the CLI (`run_old_engine` shelled out to
`pypeeker check --strict`), so a rewired `cli.py:check` would have made it
compare the new engine against itself and report a vacuous PASS. See
`### The flip (TASK-157)`'s opening paragraph below, which records the
retirement.

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

Phase 4 — **Mutation terminals** (frozen policy, done): `dsl/terminals.py`'s
named mutation values, each carrying its own confidence floor and its own
pointwise preconditions (fork #2), refusing at construction any kind
`refactor` has no planner for (fork #10); `Selection.apply(mutation)` — one
argument, no options — yielding flat unordered intents into the existing batch
machinery, footprint/effect algebra and planners all unchanged;
`dsl/demotion.py`'s two selections over the one shared `DEMOTE` value, where an
evidence-typed CLI anchor (fork #12) clears the floor a heuristic nomination
does not; all five frozen remedies restored on the ported rules with fork #5's
derived `intent_id`; and `check --fix` driven through the new engine —
`app/intent_fixes.py` for the plan/de-conflict/transaction pass,
`scripts/dsl-fix-engine.py` for the composition, and a second **fix pass** in
`scripts/differential-check.py` grading fix ids, descriptions, violation lines,
both refusal buckets and the byte-level edits against the frozen
`check --fix --plan` on every target. That fix pass is opt-in per manifest —
fabricated self-test manifests need it off — so on *this* repo's
`scripts/parity-manifest.toml` a missing `fix-engine` is exit 2 rather than a
PASS that graded zero repairs, the same guard shape (and the same escape,
`--allow-no-fix-engine`) phase 3 gave an empty `claimed`. The old engine is
still live and still gating; `dsl` gained `intents` in its layering allow-list
and still imports neither `check` nor `refactor`.

Phase 5 — **The flip** (TASK-157, landed 2026-09-06, migrate + port policy,
executed in five segments): the self-lint gate switched to the new engine; the
CLI commands re-wired to named expressions; baseline keying changed to
`(rule_id, anchor_id)`; the frozen paths were **deleted in the same PR**; the
old-engine tests were ported scenario-by-scenario per the `port` policy; and
CLAUDE.md, architecture.md and this document were reconciled against the tree
that remained. No scar remains: package names carry no version.

Also discharged at the flip — items the 2026-09-01 architecture review
deferred. The first four touched frozen paths; the last two were `dsl` naming
changes held so the surviving names were chosen once:

- `app/privatize.py`: delete the `apply_plan` parameter of `run_privatize` and
  make the return type public. **Discharged** — the successor service is
  `app/privatize.py:run_privatize(store, transaction_store, root, rules=()) ->
  PrivatizeReport`, with no `apply_plan` parameter and a public report type in
  the `app` barrel.
- `storage/__init__.py`: export `resolve_storage_root` from the barrel and
  delete `dsl/visibility.py`'s `_storage_root` copy (with its
  `_STORAGE_DIR` / `_LEGACY_STORAGE_DIR` constants). Was blocked because the
  frozen `check/baseline.py` deep-imported the name, and `barrel-only` flags a
  deep import as soon as the barrel re-exports it. **Discharged** — see the
  `pyproject.toml` spec-note entry in the ledger's cutover-part-2 section.
- `check/rules.py`: the hard-wired `REGISTRY` / `PROJECT_REGISTRY` of six
  concrete rules co-located with `register_rule`. **Discharged** — both went
  with the file; the successor is the closed `dsl.RULES` table plus the
  `register_dsl_rule` overlay.
- The star-import attribution helpers duplicated between
  `refactor/imports_ops.py` and `check/builtin/star_imports.py`
  (`_star_symbols`, `_module_indexes`, `_public_surface`,
  `_unresolved_bare_names`, `_attribute_names`). **Discharged** — folded into
  `analysis/star_imports.py`, which `refactor` and `dsl` both import.
- The `plan-batch` docstrings in `app/check_fixes.py` (`auto_fixable`) and
  `check/models.py` (`Violation.remedy`): the CLI command is `batch`.
  **Discharged for both named sites** — the two modules were deleted. One
  `plan-batch` spelling survives outside them, in
  `tests/test_app_batch_intents.py:3`, and is **left uncorrected**: that file
  predates the rewrite and this task's test policy is additions-only, so a
  cosmetic docstring edit to a frozen pre-existing test is not sanctioned here.
  A one-word follow-up, not a scar in shipped code.
- `dsl/mutation.py` holds the `no-argument-mutation` /
  `no-hidden-global-mutation` rule family while the `Mutation` value lives in
  `dsl/terminals.py`. **Discharged** — renamed to `dsl/mutation_rules.py`.
- `dsl/differential.py` / `dsl/differential_fix.py` are the new engine's
  runnable surface (JSON findings and repairs for the oracle), not the oracle;
  the names collided with `scripts/differential-check.py` and lost their
  meaning when the oracle was deleted. **Discharged** — renamed to
  `dsl/engine.py` and `dsl/repairs.py`; see the rename entry in the ledger.

Notes: TASK-149 and TASK-150 were carried as small standalone fixes to the
*surviving* CLI paths (neither touched a frozen file); both are Done.
TASK-145/147 (envelope) were unaffected by the program and stayed parked on
their own merits.

## Divergence ledger

Deliberate behavioral divergences between the old engine and the new one, and
sanctioned oracle fixes. **Append-only; every entry needs the rule, the
difference, and the reason.**

**Validation was one-directional.** While the oracle ran,
`scripts/differential-check.py` checked manifest → ledger: every
`[[divergence]]` / `[[fix-divergence]]` in `scripts/parity-manifest.toml` had
to resolve to an entry below (whitespace-normalized substring match on its
`ledger` anchor) and to a claimed rule. The converse, ledger → manifest — that
every entry below describing a live divergence was backed by a manifest
declaration, and that each spec note described what the port actually did — was
always prose, checked by reading and never mechanically.

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
- *(divergence, phase 4)* The confidence floor now decides whether a remedy
  **exists**, where the frozen engine attaches one and then discards it. Every
  frozen `with_remedy` call site attaches unconditionally on the rule's own
  terms (`unused-imports` and `prefer-tuple` to every finding; `star-imports`,
  `docstring-drift` and `unused-public-symbol` behind a guard of their own),
  and the DECLARED gate lives downstream, in `app/check_fixes.py`'s
  `auto_fixable` — so a HEURISTIC finding carries a `Violation.remedy` that
  nothing will ever plan. Both of the only two readers of `.remedy`
  (`auto_fixable` itself and `app/batch_intents.py`'s comprehension) apply that
  gate, so the discarded remedy is unobservable. Fork #2 puts the floor on the
  shared mutation value, which means it is consulted once, before the intent is
  built: below the floor there is no `Remediation` for the row at all. The
  difference is in *effect* rather than in output, and both oracles are blind to
  it by construction — the findings oracle compares five fields that never
  included a remedy, and the fix oracle compares the repairs that survive the
  gate on both sides. Same class as `born-private`'s missing self-seed.
- *(shape note, phase 4)* `Finding` gains a sixth field, `remedy`, spelled
  exactly as the frozen engine spells it on `check.models.Violation`:
  `remedy: Intent | None = field(default=None, compare=False, repr=False)`.
  This discharges the phase-3 docstring's own promise that a remedy "arrives in
  phase 4 with the mutation terminals", and mirroring the frozen field —
  including its `compare=False` — is what makes the two engines' currencies
  directly comparable rather than merely analogous.

  `compare=False` is the load-bearing half, and it is why the phase-3d entry's
  "add a terminal, not a field" is honoured rather than contradicted. The
  *terminal* (`REWRITE_STAR_IMPORT` and its two siblings) is what decides a
  repair; no row source, sweep or evidence rule moved to accommodate one. The
  field is only where the decision rides, and because it is excluded from
  equality, two findings that say the same thing about the same row still
  compare equal whether or not one of them happened to be repairable — which is
  what keeps every whole-object `Finding(...)` assertion in the read half's
  tests meaningful and the findings oracle's five-field payload unchanged.
  `check.models.Violation` made the identical call for the identical reason.

  `dsl/rules.py`'s `Remediation` (`finding`, `intent`, and a `fix_id` property
  reading the id back off the intent) survives alongside it, produced by
  `DslRule.remediations` / `MultiPartRule.remediations`. Its job is now
  narrower and sharper: it is the pairing whose `intent` is **non-optional**, so
  a fix consumer — `dsl/differential_fix.py` is the one that matters — never
  writes `if f.remedy is not None` and never has to decide what a repair with no
  intent would mean. Both halves come from one pass over the rows: `_render`
  builds each finding with its decided remedy inside, `findings` hands the list
  back, `remediations` filters it.

  One pre-existing test changed as a direct consequence, and only one:
  `tests/test_dsl_rules_crossfile.py::test_the_remediable_shape_is_reported_without_a_remedy`
  existed to pin the phase-3 *absence* this entry removes, and its docstring
  said so ("the read half has no mutation terminals"). It is ported in place,
  same corpus and same rule, to
  `test_the_remediable_shape_is_reported_with_a_remedy`: it now asserts the
  six-field shape, that `remedy` is excluded from equality, that the star's
  remedy is a `rewrite-star-import` intent, and that its derived id is
  `star-imports:rewrite:user:*` — the frozen id character for character. The
  scenario gained assertions; nothing was deleted.
- *(spec note, phase 4)* Fork #5's purely-derived `fix_id` **lands here**, not
  at the flip, discharging the standing *(planned, lands at flip)* entry above
  as a near-no-op. `Mutation.intent_id` spells `<origin>:<mutation>:<anchor>`
  with no override anywhere — `Remediation.fix_id` is a property reading it back
  off the intent, so there is no second copy to disagree — and with the mutation
  names `remove`, `rewrite`, `rename-param` and `tuplify` the derivation
  reproduces four of the five frozen ids **byte for byte**:
  `unused-imports:remove:<sid>`, `star-imports:rewrite:<sid>`,
  `docstring-drift:rename-param:<sid>:<ghost>` (because `drift_rows` already
  anchors on `<function id>:<param>`) and `prefer-tuple:tuplify:<sid>`. The new
  fix oracle compares them exactly, which is the strongest grading available
  and the reason for landing the change in the last phase where both engines
  exist. Renaming any of those four mutations is what would make this a real
  break. The fifth id is the one genuine change: `unused-public-symbol`'s
  `DeleteSymbolIntent` is spelled `unused-symbol:delete:<sid>` in
  `check/rules.py`, naming a rule that does not exist, and derives as
  `unused-public-symbol:delete:<sid>`. It is unobservable on every differential
  target, because the frozen rule attaches that remedy only to a **non-public**
  symbol and only its `also-private` option reaches one, which no corpus sets.
- *(divergence, phase 4)* `demote` and `privatize` are now two selections over
  ONE mutation value (`dsl/terminals.py`'s `DEMOTE`, reached from
  `dsl/demotion.py`'s `demote_selection` and `privatize_selections`), where the
  frozen pair had two of everything. **Intent kind**: the CLI's
  `ChangeVisibilityIntent` (kind `change-visibility`) survives;
  `refactor/privatize.py:_demote_intents`' hand-built `RenameIntent` with a
  computed `"_" + name` does not — the operation *is* the intent's identity, so
  a row needs no name arithmetic and `include_exports` is subsumed by what
  `VisibilityPlanner` already does for a demote. **Floor**: one floor,
  `INFERRED`, which is privatize's `skip_heuristic` restated on the lattice and
  is **not identical to it** — the frozen `_is_heuristic` is `== "heuristic"`,
  so an UNKNOWN nomination passes the frozen filter and a rank floor rejects it;
  a rank floor is what fork #2 asks for, and admitting the bottom of the lattice
  into an automatic rewrite was never the intent. `app/check_fixes.py`'s
  DECLARED gate, the other of the two frozen floors, is likewise gone.
  **Preconditions**: `DEMOTE` carries the two skips that are pointwise
  properties of a name, in the frozen order (`dunder-or-main` before
  `already-private`, as `_demote_candidates` comments). Its other four —
  `not-found`, `ambiguous`, `hierarchy-unsafe`, `protected-public-api` — are
  not pointwise (they need the query engine, the class hierarchy, or the
  project's library-mode configuration) and its fifth, `pending-collision`, is a
  batch-level dedupe that cannot be pointwise at all; all five land at the flip,
  four of them re-validated by `VisibilityPlanner`'s own preconditions when the
  intent is planned. **Nomination**: the symbol id comes from the row's anchor,
  deleting `check/demotion.py`'s three message-parsing regexes as a category.
  None of this is observable in phase 4 — neither `cli.py:demote` nor
  `cli.py:privatize` nor `app/privatize.py` is rewired — and the intent ids
  (`cli:demote:<id>` for the typed path, `<rule>:demote:<id>` for a nomination)
  differ from the frozen `"demote"` and `demote:<id>`; both land at the flip.
- *(scope note, phase 4)* Only the **single pass** of `check --fix` is ported.
  `app/intent_fixes.py`'s `plan_intent_fixes` mirrors `_plan_pass` plus the
  `max_iterations == 1` branch of `apply_check_fixes` clause for clause — the
  scratch transaction store, the per-intent submit, the `(file, first edit
  start, intent_id)` ordering, the byte-range de-conflict, the one `check-fix`
  transaction. `--fix-until-clean`'s bounded fixpoint (the overlay, the re-run
  of the rules against simulated state, the flatten, the `reverted` bucket) is a
  superset path over that same pass and is not ported; it lands at the flip.
  Nor is a `residual_violations` count, which is a second whole-engine run
  rather than a property of the repair. The port is a **re-implementation, not
  a call**, for the reason `dsl/differential.py:_read_config` already records:
  the new side must never execute frozen-path code, or the oracle grades a
  thing against itself.
- *(closes the phase-3d star-imports entry, phase 4)* `star-imports`' remedy is
  restored, and restoring it added a **terminal, not a field**, exactly as that
  entry predicted. The terminal is `dsl/terminals.py`'s `REWRITE_STAR_IMPORT`,
  carried by all four of the rule's message partitions; `sweeps.star_import_rows`
  is unchanged, because the row source already published everything the repair
  needs — the star's own symbol id as the row's anchor, `imported_from` as the
  module, and the file's lone-star `DECLARED` / multi-star `HEURISTIC` evidence
  intrinsic to the row. The frozen guard `names and file_confidence is
  DECLARED` therefore splits with no clause of its own: `floor=DECLARED` rejects
  the multi-star and unindexed rows through the evidence meet, and `names` is
  the mutation's two preconditions. The oracle now grades it: on target
  `crossfile` both engines plan `star-imports:rewrite:xf.stars.user:*` and no
  other, with the same description, the same violation line and the same single
  byte-range edit.

- *(substrate fix, 2026-09-02, phase 4)* `binder/assignments.py` now
  records `parent_scope_id` for a walrus target bound inside a comprehension
  (`[(hit := f(x)) for x in xs]`), the one binding site that previously left
  it `None` while still declaring the symbol into the enclosing function or
  module scope. Found when the five declare-into-scope tails were folded into
  one `_bind_into` helper; the old shape was an omission, not a rule. This
  shifts the **frozen oracle's observable output** without touching a frozen
  path: `no-hidden-global-mutation` gates on `_is_module_scope(parent_scope_id)`,
  so a module-level walrus target that a function later rebinds or mutates is
  now a finding where it was silently skipped, and `born-private`,
  `SemanticQueryEngine.members` and the DSL scope universe now see the target
  as a member of its scope. Both engines read the same binder, so the
  differential harness is blind to the shift by construction; this entry
  records that the reference behavior moved. `tests/test_binder.py::TestBindInto`
  pins the parenting.
- *(divergence, phase 5 substrate, 2026-09-06)* `dsl.register_dsl_rule` lets a
  custom rule **shadow a builtin of the same id**; the frozen
  `check.rules.get_rule` consults its builtin registry first, so there a
  builtin wins the clash. The new engine follows `analysis.traits.register_trait`
  and `refactor.registry.register_planner` (last import wins, custom over
  builtin), on the reasoning that a consumer who re-registers a builtin id
  means it. Unobservable by the oracle (it grades builtin rules only);
  `tests/test_dsl_rule_registry.py` pins the precedence.

### The flip (TASK-157) — the CLI now runs the new engine

Everything below landed in the cutover segment. The oracle
(`scripts/differential-check.py`, `scripts/parity-manifest.toml`,
`scripts/dsl-engine.py`, `scripts/dsl-fix-engine.py`) and the frozen-paths
guard (`scripts/check-frozen-paths.sh`) were retired in the same working tree,
because `cli.py:check` running the new engine makes a comparison of the CLI
against the new engine vacuous. `src/pypeeker/check/**`, `app/check_fixes.py`
and `app/privatize.py` are still on disk — 36 test files import them and their
scenario-by-scenario port is the next segment — but nothing the CLI runs
reaches them any more.

- *(discharged, 2026-09-06)* The standing *(planned, lands at flip)* `fix_id`
  entry above: `check --fix` now reports `unused-public-symbol:delete:<sid>`
  where the frozen engine reported `unused-symbol:delete:<sid>`. The frozen
  literal named a rule id that never existed; the new id is derived
  `<rule>:<mutation>:<anchor>` with no override anywhere. 13 CLI assertions
  migrated across `tests/test_check_fix.py`,
  `tests/test_check_fix_until_clean.py` and `tests/test_submit_one_path.py`;
  the two deliberate frozen-vs-new comparisons in `tests/test_app_fix_run.py`
  and `tests/test_dsl_restored_remedies.py` keep the frozen spelling.
- *(discharged, 2026-09-06)* The standing *(planned, lands at flip)* baseline
  entry above: `check --baseline` / `--update-baseline` now go through
  `storage/baseline.py`, keyed `(rule_id, anchor_id)` rather than by normalized
  message text. The file name and path are unchanged
  (`.pypeeker/check-baseline.json`), so a pre-flip baseline is read without
  error but matches nothing — every finding in it reads as new. None are known
  in the wild; pypeeker's own gate is baseline-free by design.
- *(divergence, flip, 2026-09-06)* An **unknown rule name in
  `[tool.pypeeker].rules`** now refuses: `run_dsl_check` raises
  `UnknownExpressionError` and `cli.py:check` re-raises it as a
  `click.UsageError` naming every known id (exit 2). `CheckEngine.run` skipped
  an unresolvable name in silence, so a typo read as a clean run.
  `tests/test_check_cli_config.py` pins the refusal, the
  refuse-before-any-partial-answer property and the resolvable control.
- *(divergence, flip, 2026-09-06)* `check --fix` gained the refusal code
  **`duplicate-fix-id`**. `app/intent_fixes.py:_require_unique_ids` raises
  `DuplicateIntentIdError` where the frozen `check_fixes._plan_pass` had no
  such guard; the CLI catches it and emits the standard error envelope rather
  than letting a traceback out, since CLI envelopes are frozen and a traceback
  is not one. Reachable only on a state the derived-id scheme forbids.
- *(spec note, flip, 2026-09-06)* Neither the frozen `_plan_pass` nor
  `app/fix_run.py:_plan_pass` (the `--fix-until-clean` fixpoint's copy) carries
  a duplicate-`fix_id` guard, and they are left agreeing. Only the single pass,
  which routes through `plan_intent_fixes`, inherits the guard above. No guard
  was added to the fixpoint: that would move behaviour away from the frozen
  code this segment is graded against.
- *(divergence, flip, 2026-09-06)* **`privatize --include-heuristic` is
  removed** (the flag is now `No such option`, exit 2). The `heuristic-confidence`
  skip reason stays reachable and is reported exactly as before; it is simply no
  longer waivable, because the confidence floor is an attribute of the one
  shared `DEMOTE` mutation rather than a per-invocation flag.
  `tests/test_privatize_cli.py` migrated: the removal is asserted, and the
  sibling default-skip test still proves the reason fires.
- *(divergence, flip, 2026-09-06)* Demote intent ids are now `cli:demote:<id>`
  (typed `pypeeker demote`, from the literal `"demote"`) and
  `<rule>:demote:<id>` (nominated by a rule through `privatize`, from
  `demote:<id>`), because both paths share `DEMOTE` and its origin.
  **Visible in `privatize`'s JSON**: `executed[].id` is the intent id, so a
  `privatize` report's ids change. `demote`'s report does not change —
  `TransactionSummary` carries no intent id. `tests/test_privatize_cli.py` and
  `tests/test_promote_demote.py` migrated.
- *(divergence, flip, 2026-09-06)* **`pypeeker demote` on a symbol named
  `main` now refuses** with code `dunder-or-main`, where the frozen planner
  demoted it. This is fork #2 working as specified: the preconditions are
  attributes of the mutation value, and the typed path shares `DEMOTE` rather
  than carrying a second, weaker demote. A **dunder** keeps the frozen
  `already-private` code and wording (every dunder starts with an underscore,
  which is precisely what the frozen planner reported). Two new tests in
  `tests/test_promote_demote.py`.
- *(divergence, flip, 2026-09-06)* `demote`'s `not-found` and `ambiguous`
  refusals keep the frozen codes and message text, which **discards** the
  structured context the DSL anchor errors carry (`UnresolvedAnchorError`'s
  "indexed but outside the source roots" diagnosis, `AmbiguousAnchorError`'s
  candidate list beyond the ids already named in the message). CLI refusal
  envelopes are frozen, so adding keys was out of scope. The candidate **order**
  is frozen too: `_matches` was changed from a `set` to an order-preserving
  dedup so `AmbiguousAnchorError.candidates` arrives in first-declaration order
  — the order the frozen planner's `[s.symbol_id for s in find_symbol(...)]`
  produced — and the CLI no longer sorts it. Sorting diverged whenever sorted
  order and declaration order disagree (`mod:Alpha.zeta` sorts before the
  module-level `mod:zeta` declared above it), which would have reworded a frozen
  refusal; `tests/test_promote_demote.py` now pins that message on exactly that
  shape. The outside-source-roots diagnosis is unreachable for `demote` in any case:
  its corpus is built with no source-root filter, so a typed id reaches every
  indexed file exactly as the frozen planner's query engine did.
- *(divergence, flip, 2026-09-06)* **A `batch` `fix` entry naming an unknown
  rule now refuses by name** with the frozen `{"error", "code":
  "intents-invalid"}` envelope, where the frozen `CheckEngine` skipped the
  unresolvable name in silence and the entry expanded to zero intents (the CLI
  then reported `no-intents`). `_expand_fix_rule` catches `dsl_rule`'s
  `UnknownExpressionError` — which is a `DslError`, not a `ValueError`, and so
  would have escaped `cli.batch`'s handler as a traceback — and re-raises it as
  the entry-naming `ValueError` `build_batch_intents` documents for any
  malformed input. Same reasoning as the `check` path's loud refusal of an
  unknown configured rule name, and a traceback is not one of this CLI's output
  shapes. New tests in `tests/test_app_batch_intents.py` and
  `tests/test_batch_cli.py`.
- *(spec note, flip, 2026-09-06)* The baseline keys identity on
  `rule::anchor_id`, and a REFERENCE anchor's id embeds `@<file>:<line>:<col>`
  so a use site is distinguishable from its neighbours. `Finding.anchor_id`
  therefore carries `Anchor.baseline_id`, the **positionless projection** of
  that id (`<symbol-id>@<file>`), not the raw one: `storage/baseline.py` and
  `cli.py`'s `--baseline` docstring both promise a line-independent identity,
  and keying on the raw id made every ratchet run after an unrelated edit
  report false new *and* false fixed violations for every reference-anchored
  rule (`no-unresolved-refs`, `under-exposed-access`). Repeats of one identity
  in one file are absorbed by the baseline's per-identity counting, exactly as
  the frozen `rule::file::message` scheme absorbed them. This is **not** the
  rejected "anchor at the definition id" alternative in the fact-anchor entry
  above: the projection keeps `@<file>`, so two call sites in two files stay two
  identities, and only the frozen scheme's own file-level granularity is
  reproduced. The full positional id
  is untouched elsewhere, so `--why` and the derived
  `<rule>:<mutation>:<anchor>` fix ids still name the exact use site. Tests in
  `tests/test_dsl_anchors.py` and `tests/test_baseline.py`.
- *(spec note, flip, 2026-09-06)* `over-exposed-module-symbol` gained an
  `allow` entry for `pypeeker.app.check_fixes:auto_fixable` in `pyproject.toml`.
  `app/batch_intents.py` was its last cross-module consumer and now runs the
  new engine, which orphans it into a DECLARED finding on a baseline-free gate;
  `app/check_fixes.py` is frozen this segment and cannot be edited. The
  exemption is deleted together with that file in the next segment.
- *(retired, flip, 2026-09-06)* `tests/test_dsl_differential_harness.py`,
  `tests/test_dsl_differential_fix_harness.py` and
  `tests/test_dsl_differential_empty_claim.py` tested the oracle itself — the
  harness's target materialization, its grading, its fix pass, and the
  empty-claimed-list guard. They have no post-flip home and are deleted with
  the harness.
- *(retired/split, flip, 2026-09-06)*
  `tests/test_dsl_differential_runner.py` **split** rather than died. Its eight
  `read_config` scenarios moved verbatim to `tests/test_dsl_config.py` — that
  reader survives as the sole config reader for `check`, `privatize` and a
  `batch` file's `fix` entry, and those were its only coverage, including the
  `src = []`-is-not-`src/` distinction. Its four `_NoIndexError` scenarios,
  plus the `src = []` behavioural one and the empty-rule-tuple one (a run that
  requests no rule must not fall back to running every rule), moved to
  `tests/test_dsl_engine.py`, since `dsl/engine.py`'s guard and rule loop
  survive the flip. Only the harness payload contract (`schema`, the
  exactly-five compared keys, JSON-serializability, path relativity) was
  retired.
- *(retired, flip, 2026-09-06)* Eight test functions asserting "this rule is
  claimed by the parity manifest" / "this fixture is a graded target" were
  deleted from five **live** rule-test files —
  `tests/test_dsl_rules.py`, `tests/test_dsl_rules_crossfile.py` (two),
  `tests/test_dsl_rules_mutation.py` (two), `tests/test_dsl_crossfile_rules.py`
  and `tests/test_dsl_visibility_rules.py` — together with the
  `_MANIFEST_PATH` / `tomllib` module lines only they used. Every behavioural
  test in those files is untouched. `tests/fixtures/parity/**` stays: several
  live rule tests still use those corpora.
- *(spec note, flip, 2026-09-06)* `tests/test_check_fix_until_clean.py`'s
  `custom_rule` fixture and four monkeypatch seams were migrated, not changed:
  the custom rules now register through `dsl.register_dsl_rule` (the frozen
  `check.rules.register_rule` is no longer consulted by the CLI) and the
  monkeypatches target `app/fix_run.py` instead of `app/check_fixes.py`. All
  nine scenarios — the five pathological rules and the four seams — are
  preserved verbatim in intent.
- *(rename, flip, 2026-09-06)* `dsl/mutation.py` → `dsl/mutation_rules.py`,
  `dsl/differential.py` → `dsl/engine.py`, `dsl/differential_fix.py` →
  `dsl/repairs.py`. Names only; no behaviour. `dsl/engine.py`'s `_NoIndexError`
  docstring is reframed off the oracle onto what it actually protects — a run
  over a target holding no index, where an empty result would mean "read
  nothing" rather than "found nothing". It is **not** described as protecting
  `pypeeker check`, which builds its corpus directly and never calls
  `open_corpus`.
- *(retired, flip, 2026-09-06)* **`tests/test_builtin_discovery.py`, whole
  file.** Both scenarios describe a mechanism the DSL does not have:
  `check/builtin/__init__.py:_import_submodules` walking a package so each
  rule module self-registers on import, and `get_rule` finding the rule that
  registration left behind. `pypeeker.dsl.RULES` is a closed
  `MappingProxyType` built by data construction, with no discovery pass and no
  import side effect to test; the registration half's successor,
  `register_dsl_rule`, is covered scenario-for-scenario by
  `tests/test_dsl_rule_registry.py` (reachable by id, runs over a corpus,
  returns its argument, last-wins, shadows a builtin, unknown id refuses).
- *(retired, flip, 2026-09-06)* **The frozen registry assertions in the twelve
  ported `tests/test_rule_*.py` files.** `get_rule(X) is <fn>` /
  `get_project_rule(X) is <fn>` / `X not in REGISTRY` / `X not in
  PROJECT_REGISTRY` and the `import pypeeker.check.builtin  # noqa: F401`
  side-effect lines each became `assert X in pypeeker.dsl.RULES`. Three
  distinctions die with them and have no DSL analogue: the file-scoped vs
  project-scoped rule split (a `PortedRule` is one shape), the always-on dict
  literal vs registered-rules layer split (one closed table), and identity of
  the callable behind an id (a rule is now a value, not a function object).
  `tests/test_rule_no_argument_mutation.py::test_not_in_builtin_registries`
  is the one function deleted rather than rewritten, being *only* the second
  distinction; every `... not in pyproject.toml rules` opt-in assertion in
  those files survives verbatim.
- *(retired, flip, 2026-09-06)* **`with_remedy`**, the frozen attachment idiom
  (`check/models.py`), and its two scenarios in the file now called
  `tests/test_finding_remedy.py` (renamed from `test_violation_remedy.py`).
  A mutation terminal decides a repair while the row is rendered, so
  `Finding.remedy` is a construction argument and there is nothing to attach
  after the fact. The field *contract* the helper existed to protect —
  `default=None, compare=False, repr=False`, and therefore equality, hash,
  `str`, `repr` and report order all blind to the remedy — is ported verbatim
  and still asserted, over `finding_order` where the frozen file relied on
  `Violation`'s dataclass ordering.
- *(retired, flip, 2026-09-06)* **`_rename_pair`** and its three scenarios in
  `tests/test_rule_naming_conventions.py`. The frozen rule exposed a
  `Violation -> (symbol_id, suggested_name)` extractor for the refactor-side
  converter to consume; the DSL's rename terminal builds the intent itself, so
  no such projection exists. Two of the three scenarios were **not** lost:
  the underscore-prefix case and the symbol-id/suggestion pairs are observable
  in the message's ``— suggested name: '...'`` tail and are asserted there
  (`TestSnakeCaseSuggestions::test_underscore_prefix_is_preserved`,
  `TestSuggestedNames`). Only `test_other_rules_yield_none` — a property of the
  extractor alone — is gone.
- *(retired/relocated, flip, 2026-09-06)* **`_to_snake_case` / `_to_pascal_case`
  as unit subjects.** `tests/test_rule_naming_conventions.py`'s
  `TestToSnakeCase` / `TestToPascalCase` called the frozen module-level helpers
  directly; the DSL's converters are private to `pypeeker.dsl.sweeps` and
  reach a reader only as the suggested-name tail. All eleven cases —
  `HTTPServer`, `getHTTPResponse`, `getValue`, `BadName`, `parseHTML2Text`,
  `getHTTP2`, `get_Value`, `already_snake`, `bad_class`, `http_server`,
  `HTTP_server`, `badClass`, `foo_2d` — were re-expressed one for one against
  the rule's own message in `TestSnakeCaseSuggestions` /
  `TestPascalCaseSuggestions`. The identity case (`already_snake`) becomes
  "the rule does not fire", which is the same fact one level out.
- *(divergence, flip, 2026-09-06)* **A per-rule finding list is no longer
  sorted; the run service sorts once.** The frozen `Violation` was
  `order=True` and several rules sorted their own output. `Finding` is not
  orderable and `pypeeker.app.check_run.finding_order` is the single owner of
  report order, so a `MultiPartRule` emits part by part: `star-imports` yields
  its "1 name used" row before its "2 names used" row regardless of import
  order, and `no-hidden-global-mutation` yields the attribute-write row before
  the mutator-call row on an earlier line. Two ported assertions
  (`tests/test_rule_star_imports.py::test_multi_star_first_wins_heuristic_no_remedy`,
  `tests/test_rule_no_hidden_global_mutation.py::test_findings_are_deduplicated_and_report_in_service_order`)
  now sort by `finding_order` before comparing, which is the order the CLI
  prints; deduplication, being a rule-level property, is asserted unchanged.
  No sort was added to the `run_dsl_rule_on_store` test helper: that would
  duplicate `finding_order`'s job and hide exactly this divergence.
- *(divergence, flip, 2026-09-06)* **`Finding.confidence` joins the value
  identity; `Violation.confidence` did not** (it carried `compare=False`), and
  `Finding` gives it no default where `Violation` defaulted to `DECLARED`.
  Four scenarios in `tests/test_confidence.py` change sign accordingly:
  equality and hash are now tier-sensitive, and omitting the tier is a
  `TypeError` rather than a silent DECLARED. The invariant the frozen
  `compare=False` existed to protect — a row that changes tier must not churn
  the baseline — survives where it now lives, on
  `pypeeker.storage.baseline_identity`'s `(rule, anchor_id)` key, and is
  asserted there (`test_baseline_identity_still_ignores_confidence`).
- *(retired, flip, 2026-09-06)* **`coerce_visibility`** and
  `tests/test_visibility_config.py::TestCoerceVisibility` (three scenarios).
  The function's last consumer was the frozen `check/config.py`; it was deleted
  in the cutover's source segment. Its permissiveness is also actively
  *unwanted* now: `dsl/visibility.py:_visibility_table` accepts only the raw
  `[tool.pypeeker.visibility]` mapping and raises `TypeError` on a parsed
  `VisibilityConfig`, because a parsed config cannot legally cross into `dsl`
  (`project` is outside its import boundary). That refusal is pinned by
  `tests/test_dsl_visibility_rules.py`, which is why
  `test_rules_accept_parsed_visibility_config_instance` — the frozen scenario
  asserting the opposite — is deleted rather than ported. `parse_visibility_config`
  survives as `pypeeker.project._parse_visibility_config`, and its seven
  parsing scenarios are unchanged.
- *(relocated, flip, 2026-09-06)* **`CheckConfig` / `load_config` scenarios in
  `tests/test_visibility_config.py`.** `dsl.read_config` returns a
  `(src, rules, plugins, options)` tuple rather than a `CheckConfig` value, so
  the three injection scenarios read the tuple's options map and the
  `cfg == CheckConfig()` default-shape assertion becomes
  `read_config(tmp_path) == (("src",), (), (), {})`. The one scenario with no
  tuple home — `CheckConfig.visibility` being a *parsed* field — moves onto
  `pypeeker.project.load_visibility_config`, which is where parsing now lives;
  nothing between the raw table and the rules parses it any more.
- *(relocated, flip, 2026-09-06)* **The born-private ratchet's seeding
  scenarios** (`tests/test_rule_born_private.py`). The frozen rule seeded
  itself mid-run; the ported rule never writes, and
  `app/check_run.py:_seed_born_private` owns the write. The file's
  `run_born_private` fixture composes the two halves in the order a real
  `pypeeker check` composes them, so all nineteen scenarios — first-run
  silence, empty-project-counts-as-seeded, no-auto-extend, the ratchet, the
  five exemptions and the two-namespace round trip — are preserved end to end.
- *(closes the phase-3b born-private entry, flip, 2026-09-06)* `born-private`'s
  **self-seed is restored, at the application layer**. That entry's remaining
  divergence was the write and only the write: the ported rule read the ratchet
  and could not create it, so a first run left it unarmed where the frozen rule
  armed it. `dsl/visibility.py:born_private_surface(options)` now expresses the
  seed as the *same* candidate prefix the rule's own clauses use — the
  `MODULE_FILES` semi-join and every exemption — minus the
  `in_set(Const("symbols"), BASELINE_NAMESPACES)` gate (the seed is what runs
  when that gate is shut) and minus the `RECORDED_PUBLIC_SYMBOLS` negation
  (there is nothing recorded yet), projecting `symbol_id`.
  `app/check_run.py:_seed_born_private`, called from `run_check` on an unseeded
  project and again after `clear_symbol_baseline` on `--update-baseline`, owns
  the write. Expressing the seed as the same selection the rule reads is what
  stops the surface and the ratchet drifting apart — the old pairing was a rule
  body and a hand-maintained exemption list. The rule itself still never
  writes, which is what let `CheckRun.mutating_rules()` replace
  `SIMULATION_UNSAFE_RULES` structurally rather than by name (see the
  `SIMULATION_UNSAFE_RULES` retirement entry below).
  `tests/test_dsl_born_private_seed.py` pins the surface against six per-option
  seed sets recorded from the frozen rule while both engines still existed.
- *(spec note, flip, 2026-09-06)* `tests/conftest.py`'s `run_rule_on_store` /
  `run_rule` are replaced by `run_dsl_rule_on_store` / `run_dsl_rule`, taking a
  rule **id** rather than a rule function and building a `Corpus` rather than a
  `CheckContext`. The twin calls `install_expressions()` (idempotent, and
  required: `prefer-tuple` reads the `tuple-candidate` composed trait, which
  `tests/test_dsl_traits.py` unregisters) and returns findings **unsorted**, as
  the frozen helper did. `install_expressions` touches only `tuple-candidate`,
  so `tests/test_traits.py`'s provider-override proofs over `variable-mutation`
  and `type-annotation` still work through the helper unchanged.

#### The flip, cutover part 2 — the frozen engine is deleted

The entries above landed while `src/pypeeker/check/**`, `app/check_fixes.py` and
`app/privatize.py` were still on disk. This block is the segment that removed
them, together with the test files that could only speak to them.

- *(deleted, flip, 2026-09-06)* **`src/pypeeker/check/**` (24 files),
  `app/check_fixes.py`, `app/privatize.py` and the frozen `app/check_run.py`.**
  `app/check_run2.py` → `app/check_run.py` and `app/privatize_run.py` →
  `app/privatize.py` took the vacated paths. `STOP_REASONS`,
  `CheckFixApplyError` and `CheckFixSimulationError` moved verbatim from
  `check_fixes.py` into `app/fix_run.py` — same names, same message text, same
  `code` values (`simulation-failed` / `flatten-failed` / `tree-changed`) —
  because they are the `--fix` service's own vocabulary and it is the service
  that survived. `CheckConfigError` needed no move: the new run service already
  had it.
- *(rename, flip, 2026-09-06)* **The temporary `Dsl` / `dsl_` prefixes are
  gone**, the two-engine interregnum they marked being over:
  `run_dsl_check` → `run_check`, `update_dsl_baseline` → `update_check_baseline`,
  `dsl_baseline_delta` → `check_baseline_delta`, `DslCheckRun` → `CheckRun`,
  `DslBaselineUpdate`/`DslBaselineDelta` → `BaselineUpdate`/`BaselineDelta`,
  `run_dsl_privatize` → `run_privatize`, `DslFixOutcome` → `FixOutcome`,
  `plan_dsl_fixes` → **`plan_check_fixes`**. The last name was a choice between
  two candidates and the scheme is recorded in `app/__init__.py`'s docstring:
  `plan_<what feeds it>_fixes` / `<What>FixOutcome`, so `plan_check_fixes` +
  `FixOutcome` sit beside `plan_intent_fixes` + `IntentFixOutcome`. The frozen
  spelling `apply_check_fixes` was rejected: `plan_only=True` makes it plan
  only, so its verb contradicted its own parameter. `cli.py`'s private
  `_apply_check_fixes` keeps its name — it describes the *command's* default
  behaviour and is CLI-internal. `tests/test_app_check_run2.py` →
  `tests/test_app_check_run.py` and `tests/test_app_privatize_run.py` →
  `tests/test_app_privatize.py` for the same reason a `2`-suffixed module was
  not allowed to outlive the flip.
- *(deleted, flip, 2026-09-06)* **A13's remaining deletions in
  `refactor/privatize.py`**, now that `app/privatize.py`'s frozen caller is
  gone: `_is_heuristic`, `_demote_intents`, the old `plan_privatize` entry
  point, and `_demote_candidates`'s three pointwise branches
  (`heuristic-confidence`, `dunder-or-main`, `already-private`) with their
  `skip_heuristic` / `pointwise_guards` parameters. Four further deletions the
  code forced rather than the plan naming: `pointwise_guards` had no remaining
  `False` caller, `CandidateEntry` and `_normalize_entries` existed only to
  carry the cross-layer `(symbol_id, confidence)` pair from `check` (so
  `CandidateEntry` left the `refactor` barrel too), `_DemoteCandidate.confidence`
  had no reader, and `_rewrite_barrel_all_entries`'s `by_intent is None` branch
  became unreachable. `plan_privatize_intents` took the vacated name
  `plan_privatize`: one demote entry point, not two, and no alias.
- *(retired, flip, 2026-09-06)* **`SIMULATION_UNSAFE_RULES`**, the frozen
  fixpoint's hand-maintained deny-list of rules that write during a run. It has
  no successor constant because it needs none: `CheckRun.mutating_rules()`
  narrows the loop to the rules that declare a mutation, and `born-private`
  declares none, so it drops out structurally instead of by being named.
  `tests/test_check_fix_until_clean.py::TestWriteSafety`'s
  `test_simulation_unsafe_rules_names_the_rule_that_writes` was rewritten to
  assert the property the constant encoded; the narrowing mechanism itself is
  pinned in `tests/test_app_fix_run.py::TestMutatingRuleNarrowing`.
- *(retired, flip, 2026-09-06)* **`tests/test_app_check_fixes.py`, whole file**
  (11 scenarios). `TestPlanOnly` / `TestOrderingAndConflicts` / `TestDeclinedFix`
  are covered scenario-for-scenario by `tests/test_app_intent_fixes.py`, which
  tests the successor pass. Three scenarios were **not** covered and were ported
  before the file was deleted rather than after: the `_BadHashIntent` seam
  producing `CheckFixApplyError` and the applier-result-kept-whole assertion
  moved to `tests/test_app_fix_run.py` (`TestApplyFailure`,
  `TestApply::test_the_applier_result_is_kept_whole_not_collapsed_to_a_bool`),
  and the `file-missing` decline joined its two sibling refusal codes in
  `tests/test_app_intent_fixes.py::TestDeclined`. `TestConfidenceGate`'s pair
  (a heuristic row never auto-fixes; a row with no remedy is ignored) is now
  structural — the floor is an attribute of the mutation value — and is asserted
  in `tests/test_dsl_terminals.py`.
- *(retired, flip, 2026-09-06)* **`tests/test_check_engine.py`, whole file** (20
  scenarios), the frozen `CheckEngine`'s own oracle. Where each went: the five
  engine-mechanics scenarios — no rules, the src-root filter, per-rule options,
  the sort, and require-docstrings end to end — are in
  `tests/test_app_check_run.py` (three of them ported in this segment, under
  "the engine mechanics"); `test_engine_ignores_unknown_rule_names` is already
  **inverted** by the refusal entry above and pinned in
  `tests/test_check_cli_config.py`; the two CLI exit-code scenarios moved to
  `tests/test_check_cli_config.py::TestExitCodes`; the registry and plugin
  scenarios are `tests/test_dsl_rule_registry.py`'s and
  `tests/test_app_check_run.py`'s (a plugin module registering a rule, and a
  plugin import failure). Three have **no successor** and are gone:
  `test_register_rule_rejects_unknown_scope` (the DSL has no file/project scope
  split — a rule is a rule over a corpus), `test_engine_skips_context_when_no_project_rule`
  (there is no `CheckContext` to skip building; a `Corpus` is always the
  substrate), and `test_engine_plugin_rule_receives_options` (the composition of
  two facts each now asserted alone).
- *(retired, flip, 2026-09-06)* **`tests/test_check_config.py`, whole file** (5
  scenarios). Three were already in `tests/test_dsl_config.py`; the two that
  were not — `read_config` returning the configured `rules` tuple, and
  `DEFAULT_SRC` being `pypeeker.project.DEFAULT_SRC_ROOTS` rather than a second
  literal — were added there before this file was deleted.
- *(retired, flip, 2026-09-06)* **`tests/test_app_baseline_delta_parity.py`,
  whole file**, per its own docstring: it existed only to grade
  `storage.baseline.delta` against the frozen `check.baseline.delta`, and the
  frozen operand is gone. Its one scenario about the new delta alone — that an
  **unsorted** input attributes the surplus to different rows, making the
  caller's sort obligation executable rather than only documented — moved into
  `tests/test_baseline.py` as
  `test_unsorted_findings_attribute_the_surplus_to_different_rows`.
- *(retired, flip, 2026-09-06)* **`tests/test_app_privatize.py`, the frozen
  service's file**, superseded wholesale by the renamed
  `tests/test_app_privatize_run.py`, which already asserts the frozen service's
  captured report field for field on all five fixtures.
- *(divergence, flip, 2026-09-06)* **`tests/test_baseline.py`'s identity
  scenarios are rewritten**, the visible half of the `(rule, anchor_id)` key
  recorded above. `test_identity_strips_volatile_line_fragments` became
  `test_identity_ignores_the_message_entirely`: the frozen key normalized
  `(line N)` fragments out of the message so a drifting impurity finding stayed
  baselined, and keying on the anchor subsumes that — the message is not in the
  key at all, so two rows about one anchor now collide however far apart their
  wording drifts. `test_identity_distinguishes_rule_file_and_message` became
  `..._rule_and_anchor` for the same reason. A new scenario pins the invariant
  the frozen `Violation.confidence`'s `compare=False` used to protect and
  `Finding` no longer can: re-tiering a rule does not churn the baseline,
  because the tier is not in the identity.
- *(retired, flip, 2026-09-06)* **`demote_entry`** and
  `tests/test_privatize_cli.py::TestDemoteEntry` (6 scenarios). The frozen
  extraction parsed a `(symbol_id, confidence)` pair back out of a rendered
  violation *message*, per rule, with a `None` return for format drift. The DSL
  reads the symbol id off the finding's typed anchor and never renders it to
  parse it back, so both the parse and its drift failure mode cease to exist.
  The successor path — anchor to intent, at `DECLARED` because fork #12 makes a
  typed id declared — is `tests/test_dsl_demotion.py`'s.
- *(retired, flip, 2026-09-06)* **Five `tests/test_privatize.py`
  `TestDemoteCandidates` scenarios**, one per branch A13 deleted:
  `test_already_private`, `test_dunder_and_main`,
  `test_heuristic_confidence_excluded_by_default`,
  `test_heuristic_confidence_included_when_opted_in` and
  `test_declared_confidence_passes_and_is_echoed`. All five now live on
  `DEMOTE`'s floor and preconditions in `tests/test_dsl_terminals.py`, which
  also pins the guard *order* (`dunder-or-main` before `already-private`, since
  a dunder also starts with an underscore). One replacement scenario was added
  in their place, `test_a_plain_public_symbol_is_a_candidate`, to keep the
  control the confidence-echo test used to provide.
- *(retired, flip, 2026-09-06)* **`tests/test_privatize.py::TestDemoteIntents`**
  (2 scenarios), which tested `_demote_intents`' lifting of candidates into
  `RenameIntent`s. There is no lifting left: `plan_privatize` is *handed*
  `ChangeVisibilityIntent`s, and `VisibilityPlanner.plan_demote` derives
  `include_exports` itself from the real barrel exports rather than being told.
  `tests/test_refactor_privatize_intents.py::TestBatchOfChangeVisibilityIntents`
  asserts exactly that, naming nothing.
  `tests/test_privatize.py::test_heuristic_finding_never_reaches_the_transaction`
  is retired with them: no intent exists for a below-floor row, so there is
  nothing left to keep out of the transaction.
  `test_all_skipped_yields_no_transaction` survives, retargeted onto two
  non-pointwise skips, and a new sibling
  (`test_a_skipped_symbol_leaves_the_others_and_the_tree_alone`) keeps the
  mixed skip-and-execute coverage the retired heuristic test provided.
- *(divergence, flip, 2026-09-06)* **`plan_privatize`'s eight skip codes are
  six.** `tests/test_refactor_privatize_intents.py::TestFrozenEntryPointUnchanged`
  asserted all eight through the old entry point; it is now
  `TestEverySkipCodeIsReachable` over five in one batch (`not-found`,
  `ambiguous`, `hierarchy-unsafe`, `protected-public-api`, `name-collision`)
  plus `pending-collision` asserted separately, since that one needs two
  submissions of one symbol. `TestPointwiseGuardsAreOff::test_the_pointwise_branches_are_still_live_by_default`
  is retired outright rather than ported: it called
  `_demote_candidates(..., pointwise_guards=False)` and asserted the
  `already-private` row, and neither the parameter nor the row exists. Its
  sibling — a row the mutation should have refused raises loudly rather than
  being planned into `__name` — survives, and is what stops a gap in the
  mutation's guards from being laundered into a silent double-underscore
  rename.
- *(spec note, flip, 2026-09-06)* **Five frozen-vs-new comparisons kept their
  teeth by recording the frozen answer as a literal** before the frozen engine
  was deleted, rather than by dropping the frozen operand and leaving a
  tautology. `tests/test_dsl_born_private_seed.py` (six per-option seed sets),
  `tests/test_dsl_restored_remedies.py` (`RECORDED_REMEDY_IDS`, four rules,
  violation line → fix id, `None`s included), `tests/test_app_check_run.py` (the
  frozen report on `_TWO_FILES`, the frozen sort order, the born-private seed),
  `tests/test_app_fix_run.py` (`FROZEN_OS_FIX` / `FROZEN_SYS_CONFLICT` and the
  cascade, conflict-cascade and `declined` reports verbatim, key order included)
  and `tests/test_app_privatize.py` (`FROZEN_REPORTS`, all five scenarios). One
  operand was **not** captured: `test_app_check_run.py`'s frozen
  `run_check(store, root).violations == []` for an unknown rule name, which is
  the frozen side of an already-ledgered divergence and would have become
  `assert [] == []`; the `pytest.raises(UnknownExpressionError)` half is the
  whole test now.
- *(deleted, flip, 2026-09-06)* **`tests/fixtures/parity/**` (101 files, seven
  corpora), and a correction.** The entry above says "`tests/fixtures/parity/**`
  stays: several live rule tests still use those corpora." That is **false as
  written**, and it was written from the docstring citations rather than from
  load sites: `tests/conftest.py`'s `bind_fixture` is the suite's only fixture
  loader and no caller passes a `parity/...` name — the live DSL rule tests build
  their corpora inline through `indexed_project` and a local `corpus_of`. With
  the oracle that graded against them gone, nothing reads the corpora at all, and
  a directory named for a comparison that no longer exists is exactly the version
  scar B12 forbids. The ~12 prose citations in `tests/test_dsl_rules_*.py`,
  `tests/test_module_id_collisions.py`, `analysis/star_imports.py` and
  `dsl/sweeps.py` were restated as historical measurements: the numbers are real
  and worth keeping (the `boundaries` twin-module collision, the mutation pair's
  10-and-9), so they are attributed to "the retired `boundaries` parity corpus"
  rather than to a path a reader would go looking for.
- *(divergence, flip, 2026-09-06)* **`CheckRun` carries no engine object.** The
  frozen run record handed back a live `CheckEngine`; the new one carries the
  run's *configuration* (`src`, `rules`, `options`) so a caller can re-run
  without re-reading `pyproject.toml` and risking a different rule set — which
  is what `check --fix`'s fixpoint needs, since it re-runs against a simulation
  overlay rather than the store the original run saw.
  `tests/test_app_services_cli_thin.py::TestRunCheck`'s `run.engine is not None`
  became an assertion over `run.src` and `run.rules`, and its `.violations`
  reads became `.findings`.
- *(spec note, flip, 2026-09-06)* `pyproject.toml`'s
  `[tool.pypeeker.import-boundaries.allow]` lost the `check` row and `"check"`
  from `app`, leaving `app = ["dsl", "intents", "models", "refactor",
  "storage"]`, and `[tool.pypeeker.over-exposed-module-symbol]`'s
  `pypeeker.app.check_fixes:auto_fixable` allowance went with the module its own
  comment said it would. `resolve_storage_root` is exported from the `storage`
  barrel now that `check/baseline.py` was its last out-of-package deep importer,
  discharging the second deferred item. One allowance was **added**, not
  removed: `treebuild.build_tree` became module-local when `check/context.py`
  died (the DSL `Corpus` deliberately keeps no tree), and the honest fix is
  `_build_tree` — but that would edit `tests/test_treebuild.py`, which
  references no deleted API and is therefore frozen this segment. The narrow
  allowance is an exemption with the decision deliberately left open, not a fix.
- *(spec note, flip, 2026-09-06)* `fix_run.py`'s "two copies of one pass"
  duplication — whose own docstring scheduled its resolution for "the segment
  that deletes the frozen paths", i.e. this one — is **not** discharged here.
  Lifting `_plan_pass` into `intent_fixes.py` is real work outside this
  segment's scope, so the docstring was restated as a standing known
  duplication pinned by `tests/test_app_fix_run.py::TestPassDuplicationPin`
  rather than quietly dropped.
