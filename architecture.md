# AST-Based Parser & Refactoring Tool Architecture

## Overview

A semantic code intelligence system designed to give LLMs and developers reliable tools for understanding codebases, linting, and performing large-scale refactorings safely.

## Core Architecture

Three layers, each with clear responsibilities:

### Layer 1: Language Adapters

A language "adapter" is a package boundary, not a single class. The Python
adapter — the only one implemented — spans three modules:

- `adapters/python_adapter.py` — tree-sitter parsing and visibility
  conventions (the slice consumers call directly; its surface is exactly
  `language_name`, `parse`, `get_visibility`)
- `binder/` — walks the Python CST into the language-agnostic `FileIndex`
  (deliberately hardcodes tree-sitter-python node types)
- `refactor/cst.py` — Python-CST edit helpers that turn nodes into
  byte-precise edits for refactoring

The real language-agnostic contract is `FileIndex` (Layer 2): everything
downstream of the binder consumes it and never touches language-specific
code. Supporting a second language means supplying equivalents of all three
modules that emit the same `FileIndex` shape — not merely reimplementing a
single module or interface. Capability declarations and language-specific
import resolution are roadmap items, not part of the current adapter surface.

### Layer 2: Unified Semantic Model

Language-agnostic representation containing:
- **Symbols** - named entities (functions, classes, variables, etc.)
- **Scopes** - nested containers that hold symbols
- **References** - usages of symbols (reads, writes, calls, imports)
- **Confidence levels** - how reliable each piece of info is (declared, inferred, heuristic, unknown)

This is what all consumers query against. They don't need to know which language they're working with.

**Traits (TASK-127, TASK-128).** A **Trait** (`analysis/traits.py`) is `(value, confidence,
provenance)`: a derived fact about one anchor, paired with a `Confidence` level and a
human-readable string naming which analyzer derived it and from what facts. Trait
providers self-register under a stable string name via `@register_trait(name)` /
`get_trait_provider(name)`, mirroring `refactor.registry.register_planner`
(last-import-wins on a name clash) — the same "drop in a module that registers itself"
idiom used for rules and planners, now covering trait providers too. Unlike
`check.rules.register_rule`, there is no separate builtin registry here: a builtin
provider (e.g. `analysis/variable_mutation.py`'s `variable-mutation`) can be silently
replaced by a consumer project's re-registration, which `register_rule` does not permit
for builtin rules. That is a real gap, not just a wording one, because a trait can now
back a refactor precondition — see `analysis/traits.py`'s `register_trait` docstring.
Traits live in `analysis/` specifically because that package is importable
by both `check` and `refactor` under `import-boundaries`, which is what lets a **rule**
quantify a trait across every candidate in a file (∀ — "find all") and a **precondition**
verify the same trait pointwise for one symbol just before a plan commits (one check —
"is this one still true"), without either package duplicating the analysis that produces
the value. **Two pairs are proven.**

1. `analysis/variable_mutation.py`'s `variable-mutation` trait (`has_write_ref` /
   `mutator_call` / `escaping_read`, `DECLARED` confidence, derived from a symbol's
   WRITE/READ/CALL references) is consumed by `check.rules.prefer_tuple` (∀ over
   candidate list-literal locals — none of the three may hold) and
   `refactor.preconditions.NotReassigned` (pointwise — `has_write_ref` alone, not the
   full mutation union, since a `.append()` call doesn't "reassign" a binding the way
   inlining means it; see that module's docstring for why the two consumers get
   different booleans off the same trait rather than a forced single value).
2. `analysis/type_annotation.py`'s `type-annotation` trait (value = the annotation's raw
   text or `None`, confidence = the annotation's own level — `DECLARED` for an explicit
   `x: list`, `INFERRED` for one the binder derived from the right-hand side, `UNKNOWN`
   when there is no annotation or no such symbol) is consumed by `prefer_tuple` again (∀
   — `is_inferred_list` selects the candidates) and by
   `refactor.preconditions.InferredListBinding` (pointwise, on a *freshly reloaded*
   index, immediately before `TuplifyPlanner` writes bytes). Both quantifiers exist
   because the derivation used to be written twice verbatim —
   `raw == "list" and confidence is INFERRED`, once in `check` and once in `refactor`,
   two packages that may not import each other. `is_inferred_list` is now the single
   home of that predicate.

Yes, both pairs quantify from `prefer_tuple`, and that is not double-counting: the
second pair's *verifying* side is a different precondition in a different planner, the
fact comes from a different part of the model (a type annotation, not a reference set),
and it is the first pair where the ∀ and the pointwise check guard **the same remedy
end-to-end** — the rule attaches the `TuplifyIntent`, the precondition re-verifies the
identical fact before that intent commits. The `type-annotation` pair is also what
*validated* the provider signature `(FileIndex, symbol_id) -> Trait`: it was adopted
unchanged by a second, independently-motivated fact rather than being generalized to fit
one. Nothing prescribes a universal anchor type beyond that; a future trait needing
project-wide context is free to define its own shape and key.

**Provenance format convention.** Every builtin provider writes three parts as prose —
`"<provider module dotted path>: <the facts read> for '<anchor id>' in <file path>"`,
i.e. producer / evidence / anchor. Not standardized: no structured type, no parser, no
machine format, no schema version. And one hard guardrail: **provenance is never
serialized into CLI JSON, a `Violation.message`, or a refusal `reason`** — the moment it
reaches an output surface it becomes a frozen contract every future provider must honour
byte-for-byte, which is exactly the over-engineering the loose convention avoids. It is a
debugging aid, not data. Conformance is asserted by one test that iterates the private
`traits._REGISTRY` (private on purpose: a public accessor whose only consumer lived in
`tests/` would trip the gated `unused-public-symbol` rule, which indexes `src` only).

Which of the remaining scattered `*_confidence` computations become traits — and which
deliberately do not — is decided by the promotion rule and inventory in structural item 6
below.

**Ids are not injective — over files, or within one.** Two files (a module/package
name clash, colliding source roots, …) can bind the same module id, and a single
file can bind the same symbol id twice (a re-entered `<comprehension>` scope, a
redeclaration, `@typing.overload`); the `$N` suffix disambiguates shadowed
scope-owning names only. A structure keyed by a symbol or module id — whether it
spans files or lives inside one `FileIndex` — is safe only if it is row-shaped
(one row per occurrence, id as a label — `dsl/sweeps.py`'s `import_rows`/`unit_rows`,
`query.SemanticQueryEngine._module_to_indexes`), accumulating (a union that never
drops a row), or a documented deterministic election; `table[id] = value` standing
in for every colliding row is not. Full contract, the collision generators, and the
audited consumers — including the three election conventions that coexist today:
`storage-transaction-architecture.md` → "Symbol IDs" → "Module ids are not
injective over files."

### Layer 3: Consumer APIs

Built on top of the semantic model:
- **Query interface** - find symbols, get references, traverse scopes
- **Linting** - visitors that accumulate diagnostics
- **Refactoring** - plan/validate/execute with transactional changes
- **LLM tools** - high-level operations like "what breaks if I change this"

## Key Design Decisions

1. **CST not AST** - preserve formatting for refactoring fidelity
2. **Capability-based** *(roadmap)* - adapters would declare what they can provide and consumers would check before relying on it; capability-gating remains a multi-language roadmap concept with no code artifact today, while `Confidence` is used throughout
3. **Confidence tracking** - distinguish between explicit declarations, inference, heuristics, and unknowns
4. **Separation of parsing and semantics** - adapters handle language quirks, consumers work with unified abstractions
5. **Extension points** - language-specific data preserved but typed loosely, so you don't lose information that doesn't fit the unified model

## Module Layering

Package boundaries are enforced by the tool itself, via the `import-boundaries`
rule in `pypeeker check` (configured under `[tool.pypeeker.import-boundaries]`).
Each top-level package declares the packages it may import; an internal import
outside that allow-list fails `check`. The current layering, bottom-up:

- `models`, `paths`, `project` — leaves (no internal deps)
- `adapters` → `models`
- `binder` → `adapters`, `models`, `paths`
- `storage` → `models`; `resolve` → `models`
- `treebuild` → `models`, `storage`, `paths`
- `query` → `models`, `storage`, `treebuild`, `resolve`
- `analysis` → `models`, `storage`, `query`, `resolve`
- `indexer` → `adapters`, `binder`, `paths`, `project`, `storage`
- `intents` → `models`, `query`, `storage` — the shared change vocabulary
  (Intent, Footprint, Effect), a near-leaf importable by both `check` and
  `refactor` (`refactor`/`app` consume it since TASK-122; `check` consumes
  it too, since `Violation.remedy` landed in TASK-124)
- `check` → `models`, `project`, `storage`, `resolve`, `treebuild`, `analysis`, `query`, `intents`
- `refactor` → `adapters`, `analysis`, `binder`, `intents`, `models`, `paths`, `project`, `query`, `storage`
- `app` → `check`, `intents`, `models`, `refactor`, `storage` — application-service layer
  between `cli` and the domain packages, the one place allowed to import both
  `check` and `refactor` (composes workflows neither package may compose on
  its own, e.g. planning a fix found by `check` through `refactor`'s applier)
- `cli` — composition root; unconstrained (listed in `unconstrained`, not the
  allow-list); delegates its non-trivial workflows (check-fix apply,
  plan-batch intent parsing, privatize orchestration) to `app` and keeps only
  Click parsing, JSON output, and exit codes

The allow-list in `pyproject.toml` is the enforced source of truth; this
section mirrors it for orientation.

The rule uses each file's `MODULE` symbol (its dotted module path) and its
`IMPORT` symbols, mapping both to their package under the project root, so
layering violations and regressions surface in CI rather than in review.

Enforcement is hardened against the ways an import can dodge a naive
literal-text check:

- **Origin resolution through re-exports.** Each import is charged to the
  package that *actually defines* the imported name, resolved through any
  barrel (`__init__`) re-export chain by the shared `CrossModuleResolver` — not
  the literal `imported_from` text. This closes re-export laundering (reaching
  `refactor` through a `storage` barrel that re-exports it) and
  symbol-vs-package misattribution (`from pypeeker import Sym` names a symbol,
  charged to `Sym`'s origin package). When the resolved package differs from
  the literal one the finding says so (`… via re-export '…'`). Resolution falls
  back to the literal package for external / unindexed targets.
- **Dynamic imports.** `importlib.import_module("pkg.mod")` and
  `__import__("pkg.mod")` with a string-literal argument are recovered by the
  binder as `IMPORT` symbols and enforced, but their findings carry
  `HEURISTIC` confidence (the binding is best-effort). Non-literal arguments
  (variables, f-strings) name no static module and stay unflagged.
- **Strict mode** (`strict = true`) requires every top-level unit under the
  root that appears in the index to be declared in `allow` or listed under
  `unconstrained` (e.g. the `cli` composition root); an undeclared new package
  fails `check` instead of silently escaping enforcement.
- **Unused-allowance reporting** (`report-unused-allowances = true`) flags
  `allow` entries that no real import exercises, so the layering table doesn't
  rot as dependencies are removed. Function-level imports count as exercising
  an allowance.

## The Semantic Richness Problem

Languages vary wildly in what semantic information is available:

| Concept | Always Available | Sometimes/Partial | Rarely/Never |
|---------|------------------|-------------------|--------------|
| Symbol names | ✓ All languages | | |
| Symbol locations | ✓ All languages | | |
| Scope nesting | ✓ All languages | | |
| Function parameters | ✓ All languages | | |
| Class/struct definitions | ✓ Most languages | | |
| **Visibility** | | Explicit (Rust, TS), convention (Python), absent (some) | |
| **Types** | | Full (TS, Rust), partial (Python hints), inference needed (Go) | JS, dynamic langs |
| **Interfaces/traits** | | TS, Rust, Go, Java | Python (runtime), JS |
| **Generics** | | TS, Rust, Java | Go (limited), Python (runtime) |
| **Mutability** | | Rust (explicit) | Most languages |

### Solution: Capability + Confidence Model

Rather than lowest-common-denominator or nullable fields everywhere:

**Capabilities** *(roadmap)* - adapters would declare what they can provide:
- VISIBILITY, STATIC_TYPES, TYPE_INFERENCE, INTERFACES, GENERICS, MUTABILITY, NULLABILITY, IMPORT_RESOLUTION, CALL_GRAPH
- This capability set is a roadmap concept, not a code artifact today; it is
  reserved for when a second language makes capability-gating meaningful

**Confidence levels** - how reliable each piece of info is:
- DECLARED - explicitly in source
- INFERRED - derived by analysis
- HEURISTIC - best guess
- UNKNOWN - can't determine

This lets consumers make appropriate decisions. An LLM can say "I'm less confident about this refactoring in Python because visibility is by convention" rather than silently doing the wrong thing.

## Pipeline

```
Source Text
    │
    ▼
┌──────────────────┐
│ Lexer + Parser   │ → CST (Concrete Syntax Tree)
│  (tree-sitter)   │
└──────────────────┘
    │
    ▼
┌─────────┐
│ Binder  │ → per-file FileIndex (symbols, scopes, references)
└─────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│               Semantic Model                    │
│  per-file indexes + cross-file symbol tree      │
│  + on-demand CrossModuleResolver                │
│  (queryable, the thing LLMs use)                │
└─────────────────────────────────────────────────┘
```

There is no separate checker phase in the pipeline. `pypeeker check` is a
linter that runs *over* the semantic model — a consumer (Layer 3), not a
pipeline stage — and type checking is not implemented.

### `check`: rule-engine framework vs rule library

The `check` package holds two separable concerns. They are cleanly layered
today (the framework never statically depends on a concrete rule), but two
files still co-locate both, so a physical split is deferred until a second
consumer of the engine actually exists.

**Framework** — the generic, rule-agnostic machinery that could run any rule
set:

- `engine.py` — `CheckEngine`: loads config, runs the resolved rules over the
  indexes, applies baseline filtering
- `context.py` — `CheckContext` (indexes, cross-module resolver, symbol tree)
  passed to project-scoped rules
- `config.py` — `CheckConfig` parsed from `[tool.pypeeker]`
- `models.py` — `Violation`
- `baseline.py` — the baseline ratchet
- the **registry** in `rules.py` — the `Rule`/`ProjectRule` types,
  `register_rule`, `REGISTRY`/`PROJECT_REGISTRY`, `get_rule`/`get_project_rule`
- the **remedy attachment idiom** in `models.py` — `Violation.remedy: Intent | None`
  and `with_remedy`

**Rule library** — the concrete, Python-specific rules:

- `builtin/*` — every auto-discovered rule (`import-boundaries` lives in
  `rules.py` for now; the rest, including `barrel-only`, are here)
- the concrete rule functions in `rules.py` (`require_docstrings`,
  `no_unresolved_refs`, `import_boundaries`, `prefer_tuple`,
  `unused_public_symbol`, `no_impure_functions`)
- `demotion.py`

**Remedies are intents, not fixes.** A rule that knows how to repair what it
flagged attaches the `Intent` describing the repair (`with_remedy`), never code
that produces bytes: `prefer-tuple` → `TuplifyIntent`, `unused-imports` →
`RemoveImportIntent`, `unused-public-symbol` (private findings only) →
`DeleteSymbolIntent`, `star-imports` → `RewriteStarImportIntent`,
`docstring-drift` → `RenameDocstringParamIntent`. Every one of them is
symbol-anchored, which is what lets the planner behind it re-derive the repair
from the *current* index and refuse `stale-index` rather than re-anchoring by
text; `ReplaceTextIntent`/`replace-text` is the ported reference text op and is
attached by no rule. The intent's `intent_id` is the rule's
stable repair id (`"<rule>:<operation>:<anchor>"`), which `check --fix` reports
as `fix_id`. `check` may import `intents` (a leaf) but still **never** imports
`refactor`: the planner registered for the intent's `kind` is what re-validates
preconditions against current bytes and emits edits, so every repair goes
through the same plan/validate/execute machinery a CLI refactor does, and
refuses with the same vocabulary (`stale-index`, `text-mismatch`, `ambiguous`,
`file-missing`).

**Coupling contract.** The dependency is one-directional: the library imports
the framework, never the reverse. The discovery seam is a deliberate
side-effect import — `engine.py` does `import pypeeker.check.builtin` at run
time so the builtin modules self-register via `register_rule`; it takes no
static dependency on any concrete rule. No concrete rule imports the engine,
so the framework is acyclic with respect to the library. The two things that
keep the split *logical* rather than *physical*: `rules.py` co-locates the
registry with six concrete rules (so importing the registry drags in
`analysis`, `query`, `resolve`, and `project`). Extracting the registry into a
framework-only module (e.g. `check/registry.py`) would make the framework
independently importable — the work a second engine consumer would trigger.

## Refactoring Model

Transactional approach inspired by Rope (Python refactoring library):

1. **Plan** - analyse what would change, identify affected symbols
2. **Validate** - check for naming conflicts, scope issues, breaking changes
3. **Execute** - apply changes atomically
4. **Rollback** - undo if needed

Key operations: rename, extract (variable/method), inline, move-symbol,
visibility changes (promote/demote/privatize), and batch, plus the five
planners behind `check`'s remedies — `delete-symbol` (`delete.py`),
`remove-import` and `rewrite-star-import` (`imports_ops.py`), `tuplify`
(`literals.py`), and `rename-docstring-param` (`docstring_ops.py`) — which
share the hash-verified re-anchoring discipline in `text_anchor.py`.
`replace-text` (`text_ops.py`) is the ported reference text-anchored op: no
rule attaches it, and it keeps the weaker text-only guarantee of the fix it
ports (unique-occurrence re-anchoring, no index-freshness gate).
`change signature` is a roadmap item, not yet implemented.

**`move-symbol` (`move.py`, TASK-131)** is the operation the file-lifecycle
work existed for, and the one that exercises the whole stack at once: it
deletes a top-level FUNCTION/CLASS from one module (delete-symbol's span
discipline verbatim), creates or extends another, and rewrites every
`from … import` that bound it — one transaction, rollback-exact, including
the destination module's birth. Four decisions shape it:

- **A move is a rename in id space.** `MoveSymbolIntent.predicted_effect`
  declares `renamed={symbol_id: dest_module:leaf}`, so prefix descent,
  `Effect.then`, and every pending intent's `remap` work on moves with no new
  symbol-remap machinery. Only the *file* half of `Effect` needed new fields.
- **The destination is declared unconditionally.**
  `MoveSymbolIntent.footprint` always names the destination path (derived from
  the store's own module→path mapping via `intents.module_file_path`, since
  `intents` may not import `project`); only the predicted `files_created` is
  existence-gated. `batch._order_key` reads `sorted(writes_files | reads_files)[0]`,
  so a footprint that changed shape with the filesystem would make the
  schedule depend on it.
- **Rename's *collection* is reused; rename's *edit builder* is not.**
  `RenamePlanner._build_edits` silently drops a location whose text does not
  match — right for a rename, dangling for a move — so every collected import
  edge either produces an edit or refuses by name.
- **Barrel re-exports are rewritten unconditionally; barrel *consumers* are
  not.** A move does not change the exported name, so repairing
  `from .old import X` in a package `__init__` is repair, not the cascade
  rename gates behind `--include-exports`; a consumer reached *through* that
  repaired barrel is already correct and gets no edit.

Its v1 scope is drawn by refusals rather than by cleverness, each a named
`Precondition` with `slug = None` surfacing under the uniform `plan-refused`
code: a body whose free names would stay behind (`moved-body-closed`, which
reads quoted annotations as names too, since a string annotation produces no
reference and is resolved wherever the definition lands), a definition that is
only conditionally bound (`unconditional-definition` — `if TYPE_CHECKING:`
opens no scope, so `parent_scope_id` cannot tell it from a real top-level
`def` and only the span's column can), a source module still using the symbol
itself (`source-module-free`) or still exporting it through `__all__`
(`source-export-list-clean` — an export entry is a string, so no reference
points at it and the reference-based check is structurally blind to it), a
name already bound at the destination (`no-destination-name-collision`, which
counts IMPORT bindings), a destination whose dotted path is not a legal module
location (`destination-path-unobstructed` — an ancestor segment that is a
`.py` module, or a package directory already wearing the destination's name,
either of which would give birth to a file Python cannot import), and the
qualified `import m` + `m.name` form (`move-qualified-use-unsupported` —
rewriting receivers is not an import-line edit).

**Star imports refuse on both sides**, for one reason stated twice: what a
star supplies is not enumerable. At the destination that is a collision that
cannot be ruled out (`no-destination-name-collision`). At the *source* it is
an import edge that cannot even be collected — the binder records
`from m import *` as an IMPORT symbol bound to the local name `*`, so
`find_importers` yields nothing and "every collected edge produces an edit or
refuses" would hold vacuously while the name goes unbound. That side gets its
own check (`source-star-import-opaque`) precisely because the general contract
cannot see it.

The declined alternative in each case is synthesizing a back-import or
rewriting a public-API declaration: both are semantic cascades, and both are
named follow-ups rather than v1.

**Hardening (TASK-132).** Five properties of the v1 move, each either fixed or
recorded here as accepted:

**Carried imports are carried *unguarded*, so a guarded one refuses.** The
imports the moved body needs are re-expressed at the destination as plain
module-level statements, which silently changes what a conditional binding
means: an `if TYPE_CHECKING:` import exists precisely so the module is *not*
imported at run time, and reproducing it flat undoes the guard's whole purpose.
The index cannot see the guard — `if` and `try` open no scope, so a
guarded module-level import still has `parent_scope_id == module` and lands in
`moved-body-closed`'s carried set — so `carried-imports-unconditional` reads the
CST instead, refusing when a carried binding's `enclosing_statement` is not
parented by `module`. It is the import-side twin of `unconditional-definition`,
which refuses a conditionally-*bound* `def` for the same reason and by the same
signal. It quantifies over what the move would actually *write*
(`destination-imports-compatible`'s missing set), not over everything the body
uses, because a binding the destination already provides *unconditionally* is
never written and introduces no unguarded import. The declined alternative is carrying the guard
verbatim: it needs a `from typing import TYPE_CHECKING` binding synthesized at
the destination (itself subject to `destination-imports-compatible`), a
placement rule for guard *blocks* relative to the plain import block on both the
create and the extend path, and it generalizes to no other condition — a
`try/except ImportError` fallback or a `sys.version_info` gate has no
convention fixing its meaning. Carrying the `if TYPE_CHECKING:` shape
specifically remains a **named follow-up**: it is the one guard whose meaning
*is* fixed by convention, and the generality objection does not reach it.

**Dotted-module imports (`import a.b`) are attributed by spelled path, not by
symbol id** (TASK-138). `binder/imports.py` records `import a.b` as a symbol
*named* `"a.b"` — the only name the index has room for — while Python binds
only the root `a` in the importing module; `a.b` (and anything else `a`
exposes) is reached purely through attribute access on that root. So a moved
body using `a.b.thing(...)` produces an *unresolved* bare reference to `a`
plus attribute references, never a reference whose `symbol_id` is `"a.b"`.
`moved-body-closed`'s original check — `module_level.get(ref.symbol_id)` — is
keyed by symbol id, so it structurally could not see a dotted import: the
dependency was silently dropped, not refused, the one outcome carried-import
handling exists to rule out. The fix attributes a dotted IMPORT symbol in two
tiers, both derived from what `import a.b` actually binds: (1) *precision* —
every attribute-access reference whose receiver root is not shadowed, and
every quoted chain, contributes its full spelled path to a `spelled` set, and
a dotted import is carried when its name equals, or is a dotted-prefix of,
some spelled path; (2) *fallback* — for an otherwise-unbound root the body
does reference (`import a.b` also binds bare `a`, so `a.other(...)` depends
on it too, even though it never spells `a.b`), *one* dotted import rooted
there — the first in source order — is carried, reproducing the source's
binding of that root exactly. Exactly one, never all of them: rooted dotted
imports are interchangeable for binding the root (each binds the same package
object under the same name), and a second is not the free "unused import at
the destination" it looks like — it drags in a submodule the destination
never asked for, which when that submodule imports the destination is a fresh
`ImportError` cycle in code the move never named. With the fallback there is no undecidable residue, so no
new precondition was added: once a dotted import reaches the carried set,
`carried-imports-unconditional` and `import-binding-reproducible` already
judge it correctly (`move._import_statement` already reconstructs the dotted
form), which is why a guarded `if TYPE_CHECKING: import a.b` in the source
now refuses under `carried-imports-unconditional` instead of being dropped.

Two asymmetries between what a dotted import is *named* and what it *binds*
had to be closed for that to hold, and both are cases where the spelled form
and the bound form diverge.

*Tier 1 reads bindings, not spelling.* `Reference.receiver_chain` is the text
of the chain, so `def helper(a): return a.b.thing()` spells `['a', 'b']` off
a parameter that has nothing to do with a module-level `import a.b`.
Attributing it by spelling is wrong in both directions: it writes an import
the moved body never required — which, when `a.b` imports the destination
module, is a fresh `ImportError` cycle in code the move never named, not
merely an unused line — and, when the source's `import a.b` is guarded, it
converts a correct move into a spurious `carried-imports-unconditional`
refusal. The binder already supplies the discriminator:
`Reference.receiver_root_symbol_id` is `None` exactly when the root resolves
to nothing (the tell of a dotted import) and is the shadowing binding's
symbol id otherwise. Tier 1 admits a chain only when that field is `None` or
names a module-level symbol — the second clause keeping `import a` +
`import a.b` working, where the root does resolve, to the plain import. A
shadowed root reaches tier 2 no more than tier 1, since `roots` is fed only
by *unresolved* bare references.

*"Unresolved" is only as good as the binder's binding coverage.* Three
binding forms leave no symbol behind — `match`/`case` capture patterns, PEP
695 type parameters, and an unpacking `as`-target (`with cm as (a, b)`) — so
a body whose local `a` comes from one of them looks unresolved to both tiers
and would take the spurious carry (measured: a new `ImportError` cycle at the
destination) or, with a guarded source import, the spurious
`carried-imports-unconditional` refusal. `preconditions._binder_blind_bindings`
closes the gap on the `moved-body-closed` side rather than in the binder,
whose symbol set is consumed by `query`, `check`, and `rename` and is not
this task's to redefine: it scans the already-parsed definition span for
those three forms and records, per name, the *region* it is live in — the
nearest enclosing `def`/`class`/`lambda` for a capture or unpacking target,
the whole declaring definition for a type parameter — so a capture inside a
nested `def` cannot suppress the enclosing body's genuine use of the same
root. Both tiers consult it before treating a bare root as a dotted import's.

*A dotted import's bound name is its root, and `destination-imports-compatible`
is keyed on that.* The collision check looked up `symbol.name` — `"a.b"` —
so a destination already binding `a` to something else was invisible, and the
move wrote bytes where the plain form (`from a import b` into a destination
binding `b`) refuses. That is not a policy difference: the appended
`import a.b` is the *second* binding of `a`, so the moved body's
`a.b.thing(...)` raises `AttributeError` under a destination `a = 5`, and a
destination whose own `from x import a` it shadows starts returning a module
from a function the move never touched. A dotted import is therefore checked
twice — under its own name (the unchanged de-duplication and guard-evidence
path) and under its root, which refuses on anything but a destination binding
that root to the same package via its own `import a`.

**A move leaves the source's now-unused imports alone — on purpose.** Moving a
definition out strands whatever the source imported only for it, exactly as
`delete-symbol` does. This project's answer to that class of debt is the
`unused-imports` rule plus `check --fix`, not a planner that cleans up after
itself; it is the same "repairs reveal repairs" composition the fixpoint below
exists to iterate, and it works end to end today (a move is followed by
`unused-imports` reporting the stranded binding and `check --fix` removing it in
one transaction). The planner deliberately does not compose `RemoveImportPlanner`
itself: that would delete lines the user never named, and an import with no
remaining reference is not always dead — side-effect imports and
re-export-by-import in a module with no `__all__` both look identical to it.
Two operational consequences, stated plainly rather than left to be discovered.
A `fix` entry in the *same* `batch` does **not** see the debt:
`app/batch_intents.py:_expand_fix_rule` expands the rule against the pre-batch
store, so the remedy is a *following* `check --fix`, not a same-batch entry. And
a project that gates `unused-imports` in `[tool.pypeeker].rules` will see that
gate go red immediately after a move until it runs the fix — the debt is real
and deferred, not absent. (pypeeker itself is unaffected: it leaves
`unused-imports` to ruff.)

**Carried imports join the destination's top import block.** Extending an
existing destination writes the imports at the end of its *header run* — the
maximal prefix of module-level nodes made of comments, an optional leading
module docstring, and import statements *each ending its own line*, with the
first node outside that set ending it. That is the deterministic rule a text
heuristic could not supply:
`__future__` lines keep their position because insertion is *after* the last
header import, and an `if TYPE_CHECKING:` block terminates the run rather than
confusing it, since it is not an import statement. Comments are traversed but
never *set* the anchor, so a comment introducing the first `def` is not orphaned
above generated imports.

The anchor is a *statement* boundary, not a line boundary, and the distinction
is not pedantic. The two coincide for every header written in the ordinary way
and diverge on exactly one shape: a semicolon-joined module-level line. Ending
the run at the end of the *line* holding the last header import puts the
carried import inside whatever statement shares that line, and when that
statement is a multi-line one (`import sys; VALUES = (`) inside its
continuation — a destination that does not parse at all, written with exit 0
and an empty `files_reindex_failed`. So a header node another statement shares
a line with ends the run *before* itself, which places carried imports above
the joined line: still inside the header, still ahead of any code, and the
milder same-cause case (`import sys; VERSION = 1`) stops producing the `E402`
this rule exists to eliminate. "Shares a line" means a real statement, not a
trailing `# noqa`, which is traversed like any other comment.

The split into two edits is skipped — falling back to
the old single edit above the appended definition — when the destination cannot
be parsed, and when the header run reaches the file's trailing whitespace (a
destination that is only a header, where "above the def" already *is* "after the
last import"). That second guard is load-bearing rather than cosmetic: both
edit splicers, `TransactionApplier._apply_edits_to_content` and
`refactor.batch._splice`, sort descending by start with a stable sort and splice
bottom-to-top, so an insert and a replace sharing a start offset would be
applied in list order and the replace's `old`-text check would read bytes the
insert had just moved.

**Rollback undoes the destination's *directories* too.** The advisory that a
rolled-back move leaves an empty conjured directory behind does not hold:
`apply` persists exactly the directories it conjured on
`TransactionHeader.created_dirs` (`TransactionStore.mark_applied`, in the same
write that marks the transaction `applied`), and `TransactionApplier._remove_dirs`
consumes that recorded list on both `rollback` and the mid-apply failure path
(see `storage-transaction-architecture.md` → "Directories count as mutation").
Because the list is persisted rather than reconstructed, rollback no longer
needs to guess by walking up from the removed file and deleting whatever is
empty — so a directory that existed but was empty *before* the move is no
longer indistinguishable from one the move created, and rollback now leaves it
alone. The accepted asymmetry moved to a narrower case: a header written by a
build that predates `created_dirs` (`None`) carries no directory evidence, so
its rollback removes nothing, which can leave a PEP 420-importable empty
directory behind — residue rather than destruction of a directory the
transaction never made, and only for transactions `applied` before the
upgrade.

**The destination's guards count too, so its bindings are read from bytes.**
The refusal above covers the source side of one symmetry; this is the other,
and it was the silent half. `destination-imports-compatible` compared local
name and `imported_from` against the *index*, which records a guarded
module-level import exactly like a plain one — so a destination whose matching
binding sat under its own `if TYPE_CHECKING:` was declared compatible, nothing
was written, and the move exited 0 having produced a module whose moved body
raised `NameError` the moment it ran. The check now reads the destination's
bytes and counts a match as present only when the CST *proves* it is a plain
module-level statement.

A match the CST proves **guarded** then **refuses by name** whenever the
source's own binding is proven top-level — the combination where every
available set of bytes is wrong. Skipping the name is the original silent
`NameError`. Writing a plain import beside the destination's own guard is
worse, not better: the guard exists precisely to stop that import from running,
and the ordinary reason a module guards an import is that the imported module
imports *it*, so the plain import closes the cycle and `import <destination>`
starts raising `ImportError` — the whole destination module, and everything
importing it, broken by a move that never named them. (A tempting
justification for writing anyway — "a duplicated identical import is inert" —
is materially false in exactly this case: the duplicate is what executes the
import the guard prevented. Its milder, always-present form is a second
binding of one name, ruff `F811`.) So the third option is taken, consistent
with this project's standing preference for a refusal that names the problem
over output that is silently wrong in either direction. The message names the
guard line and points at promoting it in the destination.

A guarded match whose *source* binding is itself guarded or unprovable is
counted **absent** instead, which routes it into the carried set and hands it
to `carried-imports-unconditional`: nothing can be written for it either way,
and the source-side message names the guard the user actually has to promote.
A match the CST cannot place at all is counted absent too — justified not by
inertness but by what `unproven` means here, namely that the module-level
statement carrying the binding does not parse, so the destination cannot be
imported and has no run-time guard semantics left to defeat. A match proven
top-level is still skipped, so the ordinary case writes nothing, and a match to
a *different* origin still refuses as a collision rather than becoming a write.

**Guard evidence is scoped to a statement, never to a file.** Both checks above
ask `_guard_evidence` a three-valued question — proven top-level, proven
guarded, or unproven — and the third value is the point. Reading a whole-file
`has_error` cannot answer it in either direction. It is not a proxy for
"invalid Python": tree-sitter-python rejects constructs CPython 3.14 accepts (a
PEP 696 type-parameter default, `def f[T = int](x: T) -> T`, is enough), so
failing *open* on it drops the guard on a perfectly readable `if TYPE_CHECKING:`
block elsewhere in the same file — the original behavior, and a silent one —
while failing *closed* on it would refuse every such file. Nor does
`moved-body-closed` cover the gap: it parses only the moved definition's own
span, so a parse error anywhere outside the body never reaches it. The verdict
is therefore taken from the module-level statement the binding actually lands
in, and is `unproven` when that statement carries a parse error, when the walk
never reaches `module`, or when the position resolves to no node. `unproven`
refuses under `carried-imports-unconditional` with its own wording — the move
writes carried imports as plain statements and will not do so on a guess.
Residual and accepted: where tree-sitter's error recovery *reparents* a guarded
import to the top level, the evidence reads `top-level` and the move proceeds.
That needs a file no Python parses at all, which cannot be imported and so has
no guard semantics left to preserve.

**Scheduling is single-pass; deriving the work is not (TASK-130).** The batch
engine runs each schedule once — its intents come from a caller who already
decided what to do. `check --fix` is the one place the work is *derived from
the state*, and repairs reveal repairs (delete a dead private helper → the
import only it used becomes unused), so `check --fix --fix-until-clean` adds a
**bounded fixpoint** in `app/check_fixes.py`. It keeps ONE persistent
`OverlayIndexStore` over the real store and, per iteration: re-runs the
configured rules against that simulated state (minus
`check.SIMULATION_UNSAFE_RULES` — a **write-safety** guard, since an overlay's
`project_root` is the *real* root and `born-private` writes a baseline
through it), plans the surviving remedies through `app.submit.submit_intent`
(whose per-call overlay nests over the loop's, so a planner reads the previous
iteration's bytes), splices the kept set in as ONE batch, and re-binds the
touched files. It always terminates and always says why: `stop_reason` is
`quiescent` / `max-iterations` / `repeated-fix` / `cycle` (tree-hash
oscillation), never silent — there is no monotonicity argument to appeal to.
The real tree is read-only until a single apply at the end: the overlay's net
change is flattened through `refactor.flatten_store` into ONE `check-fix`
transaction, so one `rollback <tx_id>` restores the pre-loop bytes. Because
that window is a whole multi-iteration run, the flatten re-verifies the
overlay's pre-image record (`verify_preimages`) — a file edited on disk while
the loop ran is refused as `tree-changed` instead of being overwritten by a
splice anchored to bytes read *after* the edit, which is the fail-closed
behavior plain `--fix` gets for free from the applier's hash pre-flight.
Plain `check --fix` keeps the single pass, byte-for-byte.

**Re-exports are a public API surface.** A package barrel (`__init__.py`
re-export) deliberately exposes a name to the outside world, so "rename the
definition" and "rename the public export" are genuinely different intents.
Renaming `pkg.lib:X` need not change the public name `pkg.X` — keeping the
export stable via `from pkg.lib import NewName as X` is a valid outcome. The
`--include-exports` flag today conflates these: it rewrites the export to the
new name. Two flags separate the two intents. `--include-exports` propagates
the rename through barrels (and their consumers): the definition, the
`__init__` re-export, and each barrel consumer's import and call sites are all
rewritten to the new name. `--keep-export` is the alias-preserving mode — it
renames the definition but holds the public export name, rewriting the
`__init__` re-export to `from pkg.lib import NewName as X` and leaving pure
barrel consumers (those that only reach the name through the barrel)
untouched. The two are mutually exclusive. Transitive barrel-consumer updates
are only sound when the barrel itself is updated, which is why they ride on
`--include-exports`; without either flag a barrel consumer is left untouched.

**One refusal vocabulary (TASK-125).** `PreconditionResult` (`refactor/
preconditions.py`) is the single atom of "why not" for every planner. The
classic planners (rename, extract-variable, extract-method, inline-variable,
promote/demote) already validated their prerequisites this way (TASK-85); the
six phase-4 remedy planners — `delete-symbol`, `remove-import`,
`rewrite-star-import` (`imports_ops.py`), `tuplify`, `replace-text`, and
`rename-docstring-param` (`docstring_ops.py`) — now do too, each evaluating
an ordered `Precondition` set via
`evaluate_in_order` instead of raising ad-hoc `*Error(code, message)` at
scattered call sites. Every precondition relevant to the `check --fix` report
carries its legacy refusal slug (`"file-missing"` / `"stale-index"` /
`"text-mismatch"` / `"ambiguous"`) as a `Precondition.slug` class attribute —
one precondition class, one fixed slug, even when two checks happen to share
a slug — and a planner's `*Error.code` is always *derived* from the failing
precondition's `slug`, never hardcoded at the raise site; the frozen
`check --fix` report stays byte-identical because the mapping (`file-missing`
→ `AnchorFileExists`, `stale-index` → `AnchorIndexFresh`, `ambiguous`/
`text-mismatch` → the rest) now lives in exactly one place. The failing
precondition's own `name` (e.g. `"scannable-literal"`, `"import-line-surgery-
safe"`) is additive metadata carried alongside the code: `MaterializeError`,
`refactor.batch.DroppedIntent`, and `app.submit.SubmitError` each gained an
optional `precondition: str | None = None` field naming it, with no change to
any existing serialized shape (`check --fix`'s JSON reads only `reason`/
`detail`, both unchanged). Preconditions with no legacy slug — every
rename/extract/inline check — leave `slug` at its default `None`.

**UTF-8 decode refusals are scoped to what the planner actually decodes
(TASK-136).** `SourceIsUtf8` (`refactor/preconditions.py`) turns a raw
`UnicodeDecodeError` into the same `PreconditionResult` refusal shape as
every other precondition, but its callers scope it differently on
purpose. Extract-method line-splits the *whole* file to build its edits, so
it guards the whole file at its one decode site. Extract-variable only ever
decodes the selected expression and its statement's indent run, so it guards
exactly those two spans — an ASCII selection inside an otherwise
undecodable file stays extractable, which a whole-file guard would break.
Remove-import (TASK-139) is the third: it decodes only the bytes it records
as `EditEntry.old` — the whole physical line when the line binds one name,
otherwise just the comma-separated entry being deleted — so an ASCII entry
on a line whose trailing comment is latin-1 stays removable. Its guard is
the first remedy-planner refusal whose precondition carries no legacy slug, so
it travels with `code=None` and surfaces under the CLI's generic
`plan-refused` with the undecodable byte named in `detail`.
Delete-symbol (TASK-141) is the fourth and last *remedy* planner to join —
it guards exactly the deletion span it records as `EditEntry.old` (the
definition's lines plus the trailing blank run), so an ASCII dead definition
in a file holding undecodable bytes on some other line stays deletable, and
its refusal also travels with `code=None` under `plan-refused`. Three
CLI-only planners were guarded in the same sweep: inline-variable scopes two
spans (the RHS expression it splices in, and the assignment statement it
deletes); move-symbol guards the *whole* existing destination file, on
purpose, because `_extend_destination_edits` `rstrip`s and re-splices all of
it, and span-scopes the importer half (the rewritten import entry and the
deletion span around it, via its own `_decoded_span`); and
`MovedBodyClosed` guards the moved definition's span from *inside*
its own `evaluate()`, because that precondition computes the span itself and
hoisting the guard out would mean duplicating that computation a third time.
That last one is the family's one asymmetry and is deliberate: its refusal is
reported under `precondition: "moved-body-closed"`, not `"source-is-utf8"`,
while the message wording stays uniform because the sub-guard supplies it.
`byte_offset` (keyword-only, default `0`) keeps a region guard's reported
byte file-absolute rather than relative to the region. Flattening a
simulation (`flatten_store`) hits the same problem from the write side: a
transaction records `EditEntry`/`FileCreateEntry`/`FileDeleteEntry` content
as `str`, so a file or changed region that does not decode as UTF-8 cannot
be expressed in one at all; `flatten_store` refuses with the existing
`FlattenError` (`flatten-failed`), naming the offending path, rather than
raising `UnicodeDecodeError` out of the seam.

**Raw-decode sweep (TASK-141) — this table is the sweep of record; the
family is closed.** Four separate lens rounds each rediscovered one more
`bytes.decode("utf-8")` that could reach a real CLI invocation and crash it
with a bare traceback. Rather than wait for a fifth, every byte→str decode
in `src/pypeeker/` was enumerated and classified — and the enumeration
promptly turned up the fifth itself, in the *indirect* form the four rounds
had all missed: not a decode in `refactor/`, but a splice handed back to the
binder (bucket 5). Adding a new raw decode over file bytes, **or a new path
that binds spliced content**, means adding a row here. Keyed by
**file:function**, because line numbers rot.

*1 — Guarded by `SourceIsUtf8` at the decode site.* Each names the span it
guards and why that scope:

| Site | Scope | Why |
| --- | --- | --- |
| `refactor/extract.py` (extract-method) | whole file | line-splits the entire file to build edits |
| `refactor/extract.py` (extract-variable) | the selection + its indent run | ASCII selection in an undecodable file stays extractable |
| `refactor/imports_ops._decoded_span` | the recorded import line or comma-entry | ASCII entry beside a latin-1 comment stays removable |
| `refactor/delete.py:DeleteSymbolPlanner.plan` | the recorded deletion span | ASCII dead def in an otherwise undecodable file stays deletable |
| `refactor/inline._decoded_span` | the RHS expression; the deleted assignment statement | same span-scoping, two call sites |
| `refactor/move._extend_destination_edits` | whole destination file | `rstrip`s and re-splices all of it |
| `refactor/preconditions.MovedBodyClosed.evaluate` | the moved definition span | internal sub-guard; reported under `moved-body-closed` (see above) |
| `refactor/move._importer_edits` | a multi-name import line's recorded entry, and the deletion span around it | ASCII entry beside a latin-1 comment stays rewritable |
| `refactor/batch._flatten_text` | the flattened file/region | refuses as `FlattenError` (`flatten-failed`), TASK-136 |

**A retracted "structurally safe" argument, kept because it is the instructive
one.** The comma-separated import segment was first classified bucket 4 on
this reasoning: `imports_ops._import_name_segments` derives each segment's
`bound_name` from the *same* `strip()`ed slice it records, so a trailing
comment lands inside that slice, the bound-name match then fails, and a
segment that matched the target provably cannot carry one. That holds only
for the plain `from m import x` form, where `bound_name` **is** the whole
slice. The aliased form takes a *substring* of it —
`stripped.rsplit(b" as ", 1)[1]` — so `from m import a, target as y  # café
as y` matches on `y` with the comment inside the recorded span, and
`move-symbol` crashed there with a bare traceback. (`import a.b`'s
`stripped.split(b".", 1)[0]` is a second substring case; `ImportEdgeRewritable`'s
`from ` requirement is what keeps it off move's path, not the argument above.)
`imports_ops` was never exposed — its `_decoded_span` guards this span
regardless — but `move._importer_edits` was, and is now guarded too. The
lesson for future rows: "the matched key is derived from the recorded slice"
is only a safety argument when the derivation is the *identity*; any
`split`/`rsplit`/`partition` in between reopens the hole.

*2 — Lossy on purpose (`errors="replace"`).* The decode only builds a human
message, so a U+FFFD beats a traceback: `refactor/batch._splice`,
`refactor/applier` (its two hash/text-mismatch messages),
`analysis/hierarchy.py`, and — added by this sweep —
`preconditions.DestinationImportsCompatible._guarded_destination_detail` and
`preconditions.CarriedImportsUnconditional.evaluate`. The second of those
was additionally able to raise out of an `evaluate()`, which the module
contract forbids; it no longer can.

*3 — Decode eliminated, not guarded.* Two sites decoded a span purely to
`!=`-compare it against a known `str`. Comparing `content[a:b]` against
`name.encode("utf-8")` is exactly equivalent for every input that decodes,
and for input that does not it yields "no match, skip" — the branch a
shifted anchor already takes — instead of a crash:
`refactor/planner.py`'s rename anchor verification, and `refactor/inline.py`'s
reference-text check. Removing a decode beats adding a refusal to the
most-used command for bytes it never needed to read as text.

*4 — Structurally cannot see undecodable bytes.* Each with its argument, at
the bar TASK-139 set (an identifier cannot lex non-UTF-8):

- `refactor/cst.node_text` / `cst.indent_of` — leaf helpers with no policy of
  their own. After buckets 1 and 3, every caller (extract, inline) passes a
  span already proven decodable by a guard or by a byte equality.
- `refactor/move.py`'s alias token (the pre-`as` half of an aliased import),
  its dotted module part, and its indent run
  (`body[:len(body) - len(body.lstrip())]`, ASCII whitespace by construction).
- `refactor/move._deletion_edit`'s span — a superset of `MovedBodyClosed`'s
  guarded definition span by trailing lines that `bytes.strip()` empties
  (ASCII whitespace), and `MovedBodyClosed` runs first, sharing the identical
  `start` (`line_starts[scope.span.start.line]`).
- `preconditions._expression_dotted_chains` — decodes only `identifier`
  nodes (an identifier's own token and, when it roots a dotted chain, each
  enclosing `attribute` node's `attribute`-field token) and returns `[]`
  when the re-parse `has_error`.
- `preconditions._binder_blind_bindings` — decodes only `identifier` nodes
  (`case` captures, PEP 695 type parameters, unpacking `as`-targets), over a
  span `MovedBodyClosed` has already parsed without error.
- `binder/**` — ~40 `node.text.decode("utf-8")` sites. These *are* reachable
  (a latin-1 docstring, a dynamic `import_module` argument), but every
  disk-facing entry into `bind()` already funnels the failure into a
  structured channel: `indexer._index_file`'s per-file `except Exception`
  → the `index` report's `errors[]` with **exit 0**, and
  `applier._reindex`'s → `files_reindex_failed[]`. A different family with
  its own channel, deliberately not converted to a refusal here. The third
  such entry, `simulate.rebind_source`, had no channel at all — bucket 5.
- `read_text()` equivalents in `storage/` and `check/baseline.py` read
  `.pypeeker/` JSON the tool itself wrote; `cli.py`'s `--intents` file is
  control input, not source bytes.

*5 — The fifth site, and why "unreachable" was the wrong answer.*
`refactor/simulate.rebind_source` is the third `bind()` caller and the only
one with no such `except`. The first draft of this sweep excused it as
unreachable from `check --fix` — `app.submit.submit_intent` runs a batch of
*one* with `rebind_final=False`, and `batch.py` re-binds only on
`rebind_final or bool(pending)`, both False for a batch of one — and added
that it "can only ever bind bytes that already bound once plus UTF-8 splice
text". **Both halves were false**, and the sweep is the wrong place to be
wrong, so the correction is recorded here rather than quietly fixed:

- *Reachable.* `app/check_fixes._run_fixpoint` does not go through
  `submit_intent`; it calls `batch.apply_to_overlay` directly, whose re-bind
  is unconditional. `check --fix --fix-until-clean` therefore reached it, and
  crashed with a bare `UnicodeDecodeError` traceback and empty stdout —
  precisely the symptom this family exists to close. `run_batch` reached it
  too, for every intent of a multi-intent batch but the last.
- *Not bytes-safe either.* "Already bound once" does not survive a splice. A
  latin-1 string literal is a legal expression statement the binder never
  decodes; delete a dead `def` sitting above it and the literal is *promoted*
  into module-docstring position, which `binder._module_docstring` does
  decode. Same bytes, newly fatal — and no guard on the edit could see it,
  because an edit verifies and records only the span it rewrites.

The guard is therefore an **actual bind, performed in phase 1** of
`_apply_to_overlay` (before any `write_file`), refusing as `_RebindFailed`
(`code="rebind-failed"`). No cheaper test is equivalent: a whole-file UTF-8
check would refuse the far commoner file whose undecodable bytes live in a
*comment*, which binds and splices fine. Both callers already had a channel
for an `_ApplyRefused`, so nothing new reaches the CLI: `run_batch` reports a
`PRECONDITION_FAILED` drop carrying the code, and `apply_to_overlay` raises
`OverlayApplyError`, which the fixpoint already converts to
`CheckFixSimulationError(code="simulation-failed")` — a flat error envelope
naming the file, with the tree untouched.

Fail-closed, not skip-and-continue, because the simulation *reads its index
back*: the next iteration's rules and preconditions plan against it, so a
silently stale entry would anchor edits to offsets that had moved. The
guarantee a refusal carries is over the overlay's **bytes** (nothing is
spliced, so nothing can be flattened or applied); paths bound before the
failing one keep a fresh `FileIndex` over unspliced bytes, which `is_stale`
reports — fail-closed again. Two paths deliberately keep their old answer:
`rebind_final=False` (the batch of one behind `submit_intent`, and so plain
`check --fix`) skips the bind entirely, because nothing reads that index —
it applies, and the *applier's* re-index reports `files_reindex_failed`, the
`binder/**` channel from bucket 4. That divergence between `check --fix` and
`check --fix --fix-until-clean` on identical input is intentional and pinned
by tests on both halves.

**The batch simulation substrate is an in-memory overlay (TASK-129).**
`refactor/batch.py:run_batch` layers an `OverlayIndexStore` over the caller's
store rather than copying the indexed project into a temp-dir mirror. Planners
read source bytes and hashes through the store surface (`read_file` /
`file_exists` / `file_hash`), so a path the batch has spliced serves the
simulated bytes while an untouched path *reads through* to the real tree, and
`flatten_batch` diffs the overlay's own write/tombstone record instead of
walking a directory. Because the overlay's `project_root` **is** the real
project root, nothing in the simulation loop may construct a path from it, and
`run_batch` takes an explicit `tx_store`: callers pass a scratch store under a
temp directory so the per-intent re-plans' intermediate transactions never
reach the user's `.pypeeker/transactions/`. The caller's store may itself be a
simulation store — overlays nest, and `read_file`/`file_exists` delegate
through the base store's own surface — and that does not weaken the rule: a
nested overlay's `project_root` is still the real root, so `tx_store` is still
never derived from it.

One construction *did* build a path from `project_root` without going through
this rule: `SemanticQueryEngine`'s default tree store fell back to
`TreeStore(store.project_root)` when none was injected, which would have
persisted a simulated tree into the real `.pypeeker/tree.json` on the first
`get_tree()`/`members()` call reached from a planner or rule running under an
overlay. The default is now asked of the store instead —
`IndexStore.default_tree_store()` returns the real, disk-backed `TreeStore`
(byte-identical to the old inline construction), while
`OverlayIndexStore.default_tree_store()` returns a cached, non-persisting
`InMemoryTreeStore`, so a simulation's `get_tree()` neither reads nor writes
the real tree artifact. An audit at the time of the fix found this was the
only such gap: every other engine method reads through `self._store` and is
overlay-correct by construction, and `check` never consults the persisted
tree at all — `check/context.py` builds its own with `treebuild.build_tree`
rather than going through a `TreeStore`. The gap was latent, not observed:
nothing in `refactor/`, `intents/`, `analysis/`, or `check/` called
`get_tree`/`members` as of this writing, and reads were never stale either
way, because `treebuild._reconcile_tree` gates cache reuse on a per-file hash
manifest built from whichever indexes the engine sees.

**With the overlay in place, batch-of-one costs nothing, so there is exactly
one execution path (TASK-129).** `app.submit.submit_intent` no longer looks a
materializer up and calls it against the real store; it runs
`run_batch([intent], …, policy=ALL_OR_NOTHING)` and is handed the caller's
*real* transaction store, so the planner persists its own transaction exactly
as a direct `plan()` call would. `run_batch` carries the per-intent planner
outcome out whole — `ExecutedIntent.summary`/`.warnings` from `Materialized`,
`DroppedIntent.code` from `MaterializeError` — which is what lets a batch of
one still return the planner-native `TransactionSummary` (`operation` is
`rename`/`promote`/…, never `batch`) and still raise the planner's own refusal
code. `submit_intents` keeps its return-type discrimination (`Materialized`
for one, `BatchResult` for many), but that now selects only the caller-facing
contract, not a second engine. The residual cost is one in-memory splice into
a throwaway overlay: that splice is kept because it re-verifies every
`edit.old` against the bytes the planner just read, so `_SpliceMismatch` stays
reachable on the submit path exactly as it is in a real batch (it is
unreachable *in practice* — the planner anchored to those very bytes — but the
check is not what costs anything).

What a batch of one does **not** pay for is the loop's per-intent **re-bind**.
Parsing and binding each spliced file back into the overlay exists so the
*next* intent plans against the previous one's output; the last intent's
re-bind serves only whoever reads `BatchResult.store` afterwards, and
`submit_intent` reads nothing from it. So `run_batch` takes `rebind_final`
(default `True`, i.e. a fully coherent simulated index) and `submit_intent`
passes `False`. This is a real cost, not a micro-optimization: `check --fix`
submits a batch of one *per remedy* against the same store, so an
unconditional re-bind would re-parse the whole (growing) file once per fix and
turn its per-remedy loop quadratic. Nothing else moves — overlay bytes, the
overlay's mutation record, the splice verification, and every
`ExecutedIntent`/`DroppedIntent` field are identical either way, and
`flatten_store` is a pure byte diff that never reads the simulated index, so
"flatten needs the final state" is satisfied by the writes alone.

Read-through moves two outcomes in the drop vocabulary. Both are deliberate,
both make the batch engine agree with what a direct planner call always did
(plan against the real store) — which, since the fast-path collapse, is the
only thing `app.submit.submit_intent` does — and both are pinned by tests in
`tests/test_batch.py::TestReadThroughVocabulary`:

- **A file present on disk but absent from the index is now visible to the
  simulation.** The mirror held only indexed files, so an intent naming an
  unindexed path used to hit `FileNotFoundError` and drop
  `precondition-failed`; the overlay reads it through, so the intent executes
  and the flattened transaction carries an edit against a file with no index
  entry. The edit is still hash-anchored to the real plan-time bytes, so the
  applier's guard is unchanged — but a machine-readable drop became a real
  transaction, and a caller that relied on `batch` refusing to touch unindexed
  files must now gate on the index itself.
- **An index entry whose source file is gone from disk stays visible.**
  `materialize_mirror` skipped an unreadable file *and* its index entry, so the
  orphan vanished from the simulation and a rename whose references lived in
  that file planned as though the file had never been indexed. The overlay's
  `list_indexed_files()` reads through to the base store, so the orphan entry
  survives and the `affected-files-fresh` precondition drops the whole intent
  with `File '<path>' is stale or not indexed. Run 'pypeeker index' first.`
  Reporting the stale index is the honest outcome — the alternative plans
  against an index the tree no longer backs. It is only reachable when the
  pruning refresh is skipped:
  `batch --no-refresh` / `privatize --no-refresh` (the `main` callback's
  `ensure_fresh` removes entries whose source file is gone), or a library caller
  of `run_batch` / `submit_intents` / `plan_privatize`, none of which refresh.

**Tuple-compat shims: retired (TASK-134).**
TASK-131 grew two transaction-shaped records out of what had been plain tuples:
`storage/transaction_store.py:LoadedTransaction` (`header`, `edits`, `file_rename`,
plus the new `creates`/`deletes`) and `refactor/batch.py:_FlattenedTransaction`
(`header`, `edits`, plus `creates`/`deletes`), the latter returned by the exported
`flatten_store` and `flatten_batch`. Both kept an `__iter__`/`__getitem__` shim that
still destructured to the old arity — 3-element and 2-element respectively — so the
1608 pre-existing tests passed unmodified. That was recorded at the time as a
deliberate, disclosed, *temporary* deviation. TASK-134 completed it.

The inventory decided it. A function-scoped AST probe over `src/` and `tests/` found
**zero** destructure or index sites left in `src/`: all seven original ones
(`applier.py` ×2, `cli.py` ×3, `refactor/registry.py`, `refactor/visibility_ops.py`)
had already migrated to attribute access, so the shims had no production consumers at
all. Every remaining site — 72 for `LoadedTransaction` across 13 files, 11 for
`_FlattenedTransaction` across 3 — lived in `tests/`, and the only tests exercising
the tuple shape *as such* were the ones written to cover the shim. A compatibility
layer whose sole surviving consumers are its own compatibility tests is not
compatibility; it is a ratchet, and the migration is 83 mechanical, mostly-simplifying
test edits that the suite verifies line by line.

The two shims were also not equally safe, and the asymmetry made keeping
`_FlattenedTransaction`'s indefensible. `LoadedTransaction`'s hazard was closed:
its header comes back from disk at `version = 2` whenever it carries file entries,
which is exactly what `TransactionStore.save` refuses to write back without both
`creates` and `deletes` — a lossy read-modify-write failed loudly. A flattened
transaction's header is minted fresh at the default `version = 1`, so that guard
could never fire for it, and a 2-element destructure followed by
`save(header, edits)` would have persisted a transaction that edits files without
creating the ones its edits assume, with no refusal anywhere. Its docstring's claim of
"the same shape, and the same reasoning, as `LoadedTransaction`" was true only of the
shape. Retiring the shim removes the reachable route.

What did **not** change: `TransactionStore.save`'s file-lifecycle version guard and
its regression test, which never depended on the shim — an attribute-style
read-modify-write that forgets `creates`/`deletes` is just as lossy, so the test was
retargeted to reach the refusal that way rather than deleted. The retirement is
itself pinned, not merely snapshotted: `tests/test_file_lifecycle_transactions.py`
and `tests/test_batch_file_lifecycle.py` each assert `pytest.raises(TypeError)` on a
destructure and on an index, so restoring either shim fails the suite.

Two consequences worth naming rather than burying. First, `LoadedTransaction` is
exported in `pypeeker.storage.__all__`, so a public class lost public (if never
documented-as-stable) behavior; pypeeker has no external consumers and no CLI JSON
payload depends on the arity, but it is the one genuinely user-facing shape change
here. Second, the guard asymmetry above is a *latent* defect that this change only
documents: `flatten_store`'s header is still minted at `version = 1` regardless of the
`creates`/`deletes` it carries, so `save`'s guard remains structurally unable to
protect a flattened transaction. Fixing that is a follow-up, not a silent edit inside a
hygiene change — as is renaming `_FlattenedTransaction` to drop its underscore despite
its being the return type of two exported functions.

## Target architecture: the four-noun model (mostly landed — remaining lifts below)

> **Status: mostly landed.** This section described where the codebase was agreed to be
> heading (2026-07); as of TASK-127 all four nouns exist as described (Model in `models/`,
> Trait in `analysis/traits.py` — see "Traits (TASK-127, TASK-128)" above, Intent in `intents/`,
> Transaction in `storage/`) and every role (Rule, Precondition, Planner, Batch, Violation)
> is implemented, and as of TASK-131 the last two open items closed: `EdgeAnchor` completes
> the anchor union (structural item 1) and the batch engine's file birth/death lifted the
> `flatten_batch` wall. Nothing below is outstanding work. The section is retained as the
> **record** of the decisions the migration made — the four-noun model, the trait promotion
> rule and its verdict table, the rejected purity unification, and why flatten's two
> remaining refusals are rules rather than walls. Sections above describe the current,
> load-bearing state.

### The four nouns

Everything the system does reduces to a pipeline over four concepts:

| Noun | Question it answers | Produced by | Lives in |
|---|---|---|---|
| **Model** | what *is* the code? | `bind` | `models/` (Symbol, Scope, Reference, FileIndex) |
| **Trait** | what can we *say* about it? | analysis | `analysis/` — always `(value, confidence, provenance)` |
| **Intent** | what do we *want to change*? | rules or CLI | `intents/` (anchor + params + footprint/effect) |
| **Transaction** | what *did* change? | planners | `storage/` (edits + lifecycle) |

```
source ──bind──▶ Model ──derive──▶ Traits ──quantify──▶ Findings
                                                            │ remedy
apply ◀── Transaction ◀──plan── Intents ◀───────────────────┘
  └────────▶ source   (loop closes: re-index, re-check)
```

Everything else is a **role** over these nouns, not a new concept:

- **Rule** — a ∀-query over traits → findings (`check/`)
- **Precondition** — a pointwise trait check guarding a plan (`refactor/`)
- **Planner** — Intent → Transaction; the *only* code in the system that writes bytes
- **Batch** — scheduler over intents, via the footprint/effect algebra
- **Violation** — a finding: (anchor, violated expectation, confidence, optional
  remedy **Intent**)

A proposed feature that cannot be phrased as one of these roles over the four nouns is
suspect by construction.

### Structural changes from today

1. ~~**`intents/` is a leaf package**~~ **Landed in full (TASK-131 completed it).**
   `intents/` holds `Intent`, `Footprint`, `Effect` and is consumed by both `check` and
   `refactor` (**`check` still never imports `refactor`** — rules say *what* should
   change, only planners know *how*). The anchor union is now all three shapes:
   `Anchor = SymbolAnchor | RangeAnchor | EdgeAnchor`. `EdgeAnchor(source_id, target_id,
   kind)` anchors on a *relationship* rather than an endpoint; its one kind is `"import"`,
   the edge a `from <module> import <name>` statement creates between an importing
   module's local binding and the definition it names. It shipped in the same PR as its
   consumer — `move-symbol`, whose importer half *is* a set of edges, and whose refusals
   name the edge they could not rewrite — because an unconsumed export trips the
   `unused-public-symbol` self-lint gate. Remap semantics: both endpoints follow
   `Effect.remap_id`, and a deleted endpoint orphans the edge under the existing
   `OrphanReason.ANCHOR_DELETED` (no new enum member for a distinction nothing consumes).
2. ~~**The `Fix` protocol dies.**~~ **Landed (TASK-124).** `check/fixes.py` and
   `check/protocols.py` are deleted along with `FixIntent`; each fix is a planner in
   `refactor/` and `Violation.remedy: Intent | None` is how a rule proposes a repair.
   See "The `check` framework / rule library split" above for the current state.
3. ~~**Everything is a batch.**~~ **Landed (TASK-126), literally true since TASK-129.**
   The direct-planner execution path is gone: every mutating entry point (`rename`,
   `inline-variable`, `extract-variable`, `extract-method`, `demote`, `promote`,
   `privatize`, `check --fix`) submits through `app.submit` — *submit intents →
   schedule → simulate on the overlay → one transaction* — via `cli.py`'s shared
   `_submit_and_finish` tail. TASK-126 left one exemption behind: a lone intent still
   skipped the engine and materialized directly, because on the temp-dir mirror a batch
   of one would have copied the whole indexed project. The overlay removed that cost and
   TASK-129 removed the exemption, so a batch of one is a batch — see "Refactoring
   Model" above for how it still returns the planner's own summary and refusal codes.
   See "Output contract" below for the grammar this gives every mutating command.
4. ~~**One registration idiom.**~~ **Landed.** `@register_planner(IntentKind)` replaced
   `batch._materialize`'s isinstance dispatch, mirroring `@register_rule`; TASK-127 added
   `@register_trait(name)` as the third instance of the same idiom (see "Traits
   (TASK-127)" above) — adding a capability always means dropping in a module that
   registers itself.
5. ~~**One refusal vocabulary.**~~ **Landed (TASK-125).** `PreconditionResult`
   is the atom of "why not" everywhere, including all six phase-4 remedy
   planners; batch's `DropReason.PRECONDITION_FAILED` drops (and
   `SubmitError`) now name the failing precondition alongside the legacy
   code. See "Refactoring Model" above ("One refusal vocabulary (TASK-125)")
   for the current state.
6. ~~**One home for confidence.**~~ **Mechanism landed (TASK-127); migration scope
   decided (TASK-128).** `Trait = (value, confidence, provenance)` exists, registered per
   name via `@register_trait`, with two proven rule/precondition pairs —
   `variable-mutation` (`prefer_tuple` / `NotReassigned`) and `type-annotation`
   (`prefer_tuple` / `InferredListBinding`). See "Traits (TASK-127, TASK-128)" above for
   the mechanism, the two pairs, and the provenance convention.

   **The remaining work is no longer an open-ended "migrate them all one at a time".** It
   is a decided set, governed by a promotion rule:

   > A confidence computation becomes a registered trait provider only when **both**
   > hold. **(a) Cross-boundary**: the fact is derived independently in `check` *and* in
   > `refactor` — a rule quantifies it and a precondition or planner verifies it. Sharing
   > among several `check` rules is already solved by a private helper in
   > `check/rules.py`; a registry entry there de-duplicates nothing and costs a publicly
   > overridable seam (`register_trait` has no builtin guard, so a plugin re-registering
   > the name changes behavior). **(b) Anchor-shaped**: it is derivable from one
   > already-loaded `FileIndex` plus one `symbol_id`. A fact needing the store, the
   > resolver, or a project-wide sweep does not fit `TraitProvider`, and inventing a
   > second provider shape for a fact with no cross-boundary consumer is speculative
   > generality.

   **Two homes for derived facts (post-DSL doctrine).** Since the DSL landed, a fact
   that fails clause (b) is no longer merely "stay local" — it has a designated second
   home: the **fact tier** (`dsl/facts.py` + `dsl/sweeps.py`, fork #11 of
   `dsl-rewrite.md`). The decision rule for a new derived fact is clause (b) itself:
   *anchor-shaped* — derivable from one loaded `FileIndex` plus one `symbol_id` —
   goes in the trait registry (`analysis/`, overridable, quantified by `trait_of` and
   verified pointwise by preconditions); *corpus-shaped* — needing the store, the
   resolver, a graph fixpoint, or a project-wide sweep — is a hand-written primitive
   fact, memoized per corpus via `Corpus.memo`, exposed to expressions through
   `fact_of`/`fact_source`, and declaring its own confidence. Neither home may be
   emulated in the other: a PROJECT-reach expression cannot register as a trait
   (`dsl.trait()` refuses it), and wrapping a pointwise fact in a corpus sweep buys
   nothing but a cache key. `check/rules.py:_dynamic_access_confidence` in the table
   below is the worked example: it failed (b) for years as "stay local — strongest
   future candidate", and the DSL port resolved it as a projected set
   (`dsl/visibility.py:DYNAMIC_MODULES`) — the fact tier is where that candidacy was
   always heading.

   The rejected alternative was "any computation with ≥2 consumers becomes a trait" —
   that promotes on a head-count, turning `analysis/` into a dumping ground of
   check-internal helpers and exporting overridable seams for facts no refactor ever
   verifies.

   Applying it to every confidence computation outside `models/`:

   | Computation | Verdict | Reason |
   |---|---|---|
   | `TypeAnnotation.confidence` (`prefer_tuple` / `InferredListBinding`) | **Migrated (TASK-128)** | (a) and (b) both hold; the derivation was literally duplicated across the `check`/`refactor` boundary. The model field stays where the binder writes it (`binder/assignments.py`); only the consumer-side interpretation — value-plus-confidence read as one fact — moved onto `Trait`. Two further readers (`builtin/unused_return_value.py`, `resolve.py`) can adopt the same trait later without a new provider |
   | `check/rules.py:_dynamic_access_confidence` | Stay local — **strongest future candidate** | Fails (b): needs the project-wide `_dynamic_access_modules` sweep over every index. Its *result* already crosses the boundary as a value string via `check/demotion.py:demote_entry` → `refactor/privatize.py:_is_heuristic`, not by re-derivation; making privatize re-derive it would also change behavior for explicitly-passed symbol ids. Revisit if a project-scoped provider shape is ever justified on its own merits. **TASK-149 added a deliberately duplicated, *pointwise* re-derivation** in `refactor/visibility_ops.py:_dynamic_access_module` (one anchor, one index load) that feeds the CLI `demote` advisory only — no trait promotion (the DSL freeze bars `check/**` from adopting one), and no behavior change to `privatize`, to `check --fix`, or to demote's refusal set. The DSL rewrite unifies the two derivations |
   | `check/rules.py:_impurity_confidence` | Stay local | Fails (a) — 3 consumers, all in `check` — and (b): it takes `Observations`, not an anchor. The underlying purity analysis is already shared through `analysis/purity.py` |
   | `builtin/star_imports.py` file-confidence, `builtin/unused_imports.py`, `builtin/import_time_side_effects.py` | Stay local | Single-rule, single-use |
   | `Symbol.import_confidence` readers (`rules.py`, `builtin/barrel_only.py`, `builtin/unused_imports.py`) | Stay local | 3 consumers, all in `check`; a plain model-field read, not a derivation |
   | `Symbol.visibility_confidence` | **Do not migrate — recorded as dead** | Written at seven binder sites and read *nowhere* in `src/`. A serialized-only field with zero consumers; a provider for it would have no consumer either, which both the `unused-public-symbol` gate and the mechanism's own rule forbid |

   **The purity pair was evaluated as the second unification and rejected**, for four
   independent reasons — recorded here so it is not re-proposed. (1) *Anchor*:
   `check.rules.no_impure_functions` anchors on a FUNCTION/METHOD `symbol_id` and needs
   an `IndexStore` plus a shared `SemanticQueryEngine`; `refactor.preconditions.
   MultiUseValuePure` anchors on a **line range** via `refactor/dataflow.py:analyze_range`
   and never resolves a symbol at all. (2) *Depth*: the rule is transitive (`call_graph`
   + `functions_reachable_from` + a fixpoint); a line range has no callee set, so
   `analyze_range` is direct-only by construction — unifying would either make
   inline-variable start refusing values whose call chain is impure, or make the rule's
   findings disappear. (3) *Policy*: the rule builds a configurable `PurityPolicy` from
   `[tool.pypeeker.no-impure-functions]`; `analyze_range` is pinned to `DEFAULT_POLICY`.
   (4) *Verdict type*: the rule needs the observation evidence plus a confidence tier, the
   precondition needs one bool. Decisively, the shared derivation is **already factored** —
   both bottom out in `purity._iter_observations` — so a Trait would add registry
   indirection over an already-DRY seam while unifying nothing. `analysis/purity.py` is
   the right home for that fact, with or without traits.

   With `TypeAnnotation.confidence` migrated, `visibility_confidence` dead, and
   `import_confidence` check-internal, the confidence-migration line of this section is
   closed; the walls list below is what remains.

### Walls this makes visible (pre-existing, to lift during migration)

**This list is now empty**; both entries below are struck through, and nothing here is
outstanding work. It is kept as the record of what the migration set out to lift.

- ~~`flatten_batch` refuses created/deleted files — blocks a future `move-symbol`
  planner; the batch engine must learn file birth/death.~~ **Lifted (TASK-131).**
  `Effect.files_created`/`files_deleted`, `Materialized.files_created`/`files_deleted`,
  and `FileCreateEntry`/`FileDeleteEntry` emission landed in PR1/PR2, and `move-symbol`
  (PR3) is the planner that consumes them — see "Refactoring Model" above. What refuses
  today is not a wall but two deliberate rules: an executed *file rename* still cannot be
  flattened (a transaction holds at most one, and the applier resolves edits against
  pre-rename paths), and a birth or death **no executed intent's `Effect` declared** is
  refused as an under-declared effect. Overlay writes are explicit calls, so an unclaimed
  newborn means a planner wrote a file it did not take responsibility for; refusing is
  what keeps the effect algebra load-bearing rather than advisory.
- ~~Scheduling is single-pass (`MAX_PLAN_ATTEMPTS_PER_INTENT = 1`) — cascading remedies
  (remove import → symbol becomes unused → delete symbol) need a fixpoint or a re-run.~~
  **Lifted for `check --fix` (TASK-130)**, where the work is *derived* from the state:
  `check --fix --fix-until-clean` runs the bounded fixpoint in `app/check_fixes.py` (see
  "Refactoring model" below). The batch *scheduler* stays single-pass by design — its
  intents come from a caller who already knows what it wants done — so
  `MAX_PLAN_ATTEMPTS_PER_INTENT = 1` is unchanged and is no longer a wall.

### Migration order

1. ~~Extract `intents/` leaf (+ `Anchor`), update import-boundaries. Behavior-preserving.~~
   **Done**, `EdgeAnchor` included since TASK-131 (see structural item 1 above).
2. ~~`@register_planner` registry; `_materialize` becomes a lookup. Behavior-preserving.~~
   **Done.**
3. ~~Everything-is-a-batch: single-op CLI commands route through the batch engine.~~
   **Done (TASK-126).**
4. ~~Convert the five fixes to intents+planners; `Violation.remedy`; delete the Fix
   protocol and `FixIntent`.~~ **Done (TASK-124).**
5. ~~Refusal-vocabulary unification (`PreconditionResult` everywhere).~~ **Done (TASK-125).**
6. ~~Uniform CLI grammar (`--plan`/`apply`/`rollback`).~~ **Done (TASK-126).**
   See "Output contract" below — this phase is the sanctioned, deliberate
   pre-1.0 break: old `plan-*` command names are gone, no aliases.
7. ~~Traits foundation (value+confidence+provenance; migrate a first rule/precondition
   pair as proof).~~ **Done (TASK-127).**

Each phase lands green (pytest + ruff + self-lint) and is independently shippable. Every
phase is now done, the walls list above is empty, and the confidence migration is closed
(structural item 6). What survives in this section is the *record* of decided design —
the trait promotion rule and its verdict table, the rejected purity unification, the
authorization rule behind flatten's remaining refusals — not outstanding work.

## LLM Integration

Simple CLI tool that LLMs call directly. No SDK or protocol complexity.

```
pypeeker <command> [args]
```

**Implemented commands:**

- `index <path>` - index a codebase
- `check` - run linting rules (configured under `[tool.pypeeker]`), or `check --fix` to apply autofixes (`--fix --plan` to preview them)
- `symbol <name>` - get symbol info + references
- `refs <symbol-id>` - find all references
- `tree [symbol-id]` - browse the cross-file symbol tree
- `purity <symbol-id>` - report a function's purity and side effects
- `scope <file:line>` - what's visible at this location

Mutating commands (TASK-126: uniform grammar) plan AND apply immediately;
pass `--plan` to only write the PENDING transaction:

- `rename <symbol-id> <new-name>` - rename a symbol
- `extract-variable <file> <start> <end> <name>` - extract an expression into a variable
- `extract-method <file> <start> <end> <name>` - extract a statement range into a function
- `inline-variable <symbol-id>` - inline a local variable into its uses
- `move-symbol <symbol-id> <dest-module>` - move a top-level function/class to another
  module, creating it if absent, rewriting every importer (barrel re-exports included)
- `batch <spec>` - run a batch of refactorings from one intent spec as ONE transaction
- `promote <symbol-id>` - make a symbol public
- `demote <symbol-id>` - make a symbol private
- `privatize` - mass-demote unused public symbols driven by check findings
- `check --fix` - apply every autofix attached to a certain finding as ONE transaction
  (`--fix-until-clean` repeats that against simulated post-fix state until quiescence
  or a bound, still ONE transaction; `--fix-max-iterations N` caps it, default 10)

Transaction lifecycle (unchanged by TASK-126):

- `apply <tx-id>` - execute a PENDING transaction (only needed after `--plan`)
- `rollback <tx-id>` - undo an applied refactoring (marks it `ROLLED_BACK`)
- `transactions list` - list stored transactions
- `transactions show <tx-id>` - show a transaction's edits and status
- `transactions cancel <tx-id>` - delete a pending transaction

**Roadmap (not implemented):**

- `search <query>` - semantic symbol search

Output as JSON for easy parsing. LLM calls CLI, parses response, reasons, calls another command if needed.

Benefits:
- Testable independently
- Usable by humans directly
- No protocol overhead
- Works with any LLM tool-use implementation

### Output contract (stable, additive-only — except one deliberate break)

Three consumer-facing contracts are treated as **frozen** — they evolve
additively (new optional keys, new commands) but existing shapes are not
renamed or restructured, so a driving LLM or script can rely on them:

- **CLI JSON envelope.** A command either prints its success payload (a JSON
  object, or an array for the list-returning commands) *or* a single flat error
  object `{"error": <human message>, "code": <stable-machine-slug>, …context}`
  and exits non-zero. Every error carries a `code`; the presence of the `error`
  key is the success/failure discriminator. (`check` is the one deliberate
  exception: it emits ruff-style `path:line: [rule] message` text, not JSON.)
- **Symbol-ID grammar** (`module.path:Scope.Chain:local$N`, owned by
  `models/symbol_id.py`) — see the storage doc. New sentinel prefixes may be
  added; the separators and shape are stable.
- **`TransactionSummary` shape** (`tx_id`, `operation`, `symbol_id`,
  `old_name`, `new_name`, `edit_count`, `created_at`, `files_affected`) and
  the refusal vocabulary (`PreconditionResult`/`SubmitError`/error `code`
  slugs) — unchanged by TASK-126 below; a fix or a rename's success payload
  reads exactly as it always has, whichever way the transaction reached
  disk.

**Mutation grammar (TASK-126 — a deliberate pre-1.0 break).** Everything
above is stable, but the *command names and default plan/apply behavior*
were never part of that guarantee, and this phase intentionally breaks
them, once, before 1.0: the old `plan-rename` / `plan-extract-variable` /
`plan-extract-method` / `plan-inline-variable` / `plan-batch` command names
are **removed outright, with no aliases** — Migration order item 6
("uniform CLI grammar", above) lands as a clean break rather than a
deprecation cycle, on the reasoning that a pre-1.0 tool has no installed
base to keep compatible and a permanent alias would just be one more shape
to keep frozen forever. The replacement grammar, uniform across every mutating
command (`rename`, `inline-variable`, `extract-variable`, `extract-method`,
`batch`, and the already-`plan-*`-prefix-free `move-symbol`/`demote`/
`promote`/`privatize`):

- **No flag: plan AND apply, immediately.** Planning is command-specific —
  `rename`/`inline-variable`/`extract-variable`/`extract-method`/`demote`/
  `promote` each submit one intent through `app.submit`; `batch` schedules
  and flattens a whole intents file (unchanged plumbing); `privatize` runs
  the demotion-feeding rules and plans one batch demotion — but every one
  of them, once a transaction is persisted, applies it through the exact
  same `TransactionApplier` the standalone `apply` command uses, via one
  shared tail (`cli.py`'s `_finish_mutation`) so the apply half of the
  grammar cannot drift per-command. The JSON output is the command's usual
  payload (`TransactionSummary` fields, or `batch`'s/`privatize`'s own
  report shape) plus `"applied": true` — the precedent `privatize --apply`
  set before this phase, now the default everywhere instead of opt-in.
- **`--plan`: today's old plan-only behavior, unchanged.** The transaction
  is written PENDING and nothing is applied; the JSON output is exactly the
  pre-TASK-126 plan-only payload (no `"applied"` key), inspectable via
  `transactions show <tx-id>` and executed later with `apply <tx-id>`.
- **Refusal-to-plan is unaffected by the flag.** A `SubmitError` (bad
  symbol, precondition failure, malformed batch input, …) is emitted the
  same way regardless of `--plan` — the apply step never runs because
  planning never succeeded.
- **Apply failure after a successful plan** emits the identical envelope a
  manual `apply <tx-id>` failure would — code `"apply-failed"`, `tx_id`
  included, exit 1 — and leaves the files exactly as they were. The
  transaction's resulting *status* is whatever `TransactionApplier` left,
  because `_finish_mutation` never touches it, and that status depends on
  the failure phase (`applier.py`'s two phases, unchanged by this task):
  a **pre-flight** refusal (plan-time hash mismatch, missing file) leaves it
  PENDING and re-appliable once the conflict is resolved, while a
  **mid-apply** failure (I/O error during the write/swap) restores the
  original bytes and marks it **FAILED**, which is terminal — `apply`
  ("is not pending"), `rollback` ("is not applied") and
  `transactions cancel` ("only pending transactions can be cancelled") all
  refuse it. Recovery from FAILED is to re-run the command: nothing was
  written, so re-planning is safe. Note the default (no-`--plan`) path plans
  and applies microseconds apart in ONE process, so pre-flight conflicts are
  near-unreachable there and mid-apply/FAILED is the realistic outcome —
  the opposite of the `--plan` → later-`apply` route, where an external edit
  in between makes pre-flight/PENDING the common one.
- **Success reports what the apply did to the index.** The applied payload
  is the command's usual JSON plus `"applied": true` *and* the applier's own
  `files_modified` / `files_reindexed` / `files_reindex_failed`. A re-index
  failure does not raise — the edits are on disk and only the index entry is
  stale — so collapsing the applier's result to a bool would silently hide
  it; under apply-by-default the very next mutating command would plan off
  that stale entry and write immediately, with no human apply step in
  between. It is therefore reported in the payload (the exit code stays 0:
  the refactoring itself succeeded).

`check --fix` joins the grammar on both halves. Its apply half needed
nothing — it already applied by default, which is *why* it was the
precedent this grammar generalizes — but it had no plan half, leaving the
command that rewrites the most files at once as the only unpreviewable one;
it now takes `--plan` like everything else (writing its single `check-fix`
transaction PENDING for `transactions show` / `apply`). Its report keeps
`skipped_conflicts` / `declined` / `residual_violations` / `tx_id`, and the
list of repairs is named **`fixes`** — *not* `applied`, which in this
grammar is the boolean every mutating command emits. The two must not share
a key: a driver branching on `if result.get("applied")` would read an empty
fix list as falsy and a non-empty one as truthy by accident.

`--fix-until-clean`'s report (TASK-130) is the additive rule taken at its
strictest: every existing key keeps its name, type and meaning (`applied`
stays a bool, `tx_id` stays a scalar, `fixes` stays the flat ordered union of
the repairs that landed), and the five loop keys — `reverted`, `iterations`
(per-iteration `{iteration, fixes, reverted, skipped_conflicts, declined}`
counts), `iterations_run`, `quiescent`, `stop_reason` — plus the `iteration`
on each fix entry appear
**only when the caller asked for the behavior they describe**. Plain
`check --fix` therefore emits byte-identical JSON on every path, which is
enforced structurally: `apply_check_fixes` branches to the loop before any of
it exists. `skipped_conflicts` and `declined` are deduped by `fix_id` across
iterations keeping the last verdict, and a repair that later lands leaves both
— so a conflict loser applied in iteration 2 appears once, under `fixes`.

Keeping `fixes` to its stated meaning under the loop needs one extra rule,
because the loop's transaction is the NET diff of every iteration rather than
a replay of them: a repair whose files all end the run byte-identical to the
real tree is listed under **`reverted`**, not `fixes`. That is what preserves
the invariant a driver actually depends on — `fixes` non-empty **iff** `tx_id`
is non-null, in both modes — for the case the `cycle` guard exists to catch,
where an A→B→A oscillation nets to nothing and there is no transaction to roll
back. The guarantee is per *file*, not per repair: inside one file the diff is
a single line-trimmed splice, so when a later iteration rewrites bytes an
earlier repair produced, only their combined result is expressible and both
repairs are reported as landed.

## Self-lint rule adoption

pypeeker gates itself with a **curated** set of its own rules — in CI and in the Claude
pre-commit hook — via plain `pypeeker check` (no baseline, no `--fix`). A rule earns a
place in `[tool.pypeeker].rules` only when its findings are *unambiguous* (a real bug or
hygiene problem) **and** currently zero on this codebase, so the gate stays clean and every
new finding is a genuine regression. There is deliberately **no baseline**: grandfathering
findings would hide exactly the regressions the gate exists to catch, so instead each
non-gated rule is either fixed to zero or excluded with a stated reason.

**Gated (hard) rules** — clean, unambiguous, block on any finding, and *not* already
covered by another tool in the pipeline: `no-unresolved-refs`, `import-boundaries`,
`barrel-only`, `import-time-side-effects`, `docstring-drift`, `pure-decorator-contracts`,
`test-only-production-code`, `no-import-cycles`, `no-impure-functions`,
`unused-public-symbol`, `over-exposed-module-symbol`. These are the checks ruff and mypy
can't express — they need cross-module resolution, purity analysis, or visibility
inference.

**Not gated — already covered by ruff** — four rules duplicate checks ruff runs (and CI
+ the pre-commit hook already run ruff), so gating them in pypeeker too is pure redundancy.
They stay available as builtin rules for consumer projects that don't run ruff:

| pypeeker rule | ruff equivalent |
|---|---|
| `unused-imports` | `F401` |
| `star-imports` | `F403` / `F405` |
| `naming-conventions` | `N8xx` (pep8-naming; `N818` exception-suffix left off to match the rule's prior scope) |
| `require-docstrings` | `D101` / `D102` / `D103` (public class/method/function, src only) |

**Not gated on pypeeker** — the remaining builtin rules stay available to consumer projects
but are not run against pypeeker itself, because their findings here are advisory,
architectural, or intrinsically stateful rather than defects. Excluding a rule needs a
reason; here they are:

| Rule | Findings on pypeeker | Why it is not a pypeeker gate |
|---|---|---|
| `no-argument-mutation` | ~90 `state`/`ctx` mutations | The binder and planners thread a **mutable accumulator** (`BinderState`, precondition `state`) through visitor functions by design; Click mandates writing `ctx.obj`. The rule can't distinguish a dedicated accumulator from a caller's collection, so it mis-fires on the architecture. |
| `under-exposed-access` | ~37 cross-module `_helper` accesses | pypeeker treats a leading `_` as **package-internal**, not module-private: sibling modules share protected helpers (`_make_name_reference` across `binder.*`, visibility helpers across `check.builtin.*`). The rule enforces stricter privacy than the project's convention. |
| `over-exposed-export` | ~31 barrel re-exports | Barrels **curate the public API surface** even when no *other src package* consumes an export (tests and external consumers are invisible to the rule). |
| `prefer-tuple` | never-mutated lists | Advisory style only. Its autofix *is* now safe (read-escape analysis means it only converts genuinely-local lists), but the suggestion is stylistic, not a defect, so it stays opt-in. |
| `unused-return-value` | 8 discarded returns | Idiomatic convenience returns (`IndexStore.save() -> Path`, `ScopeStack.pop() -> Scope`) that a caller *may* use; discarding one at a given site is valid. |
| `no-hidden-global-mutation` | 2 registry writes | The `@register_rule` decorator mutates the module-level registry dicts — the **documented self-registration mechanism** (see `check/rules.py`). |
| `born-private` | stateful | Intrinsically *prospective*: it records the public surface as a seed and flags only symbols that become public afterward, so it needs stored state and is not a fit for a stateless, baseline-free gate. |

The rule of thumb: gate a rule only when it is clean and its findings are real defects. If
running an excluded rule surfaces a finding that *is* a defect (not one of the reasons
above), fix the code and consider promoting the rule — never add a baseline to silence it.

## CI

The workflow lives at `.github/workflows/ci.yml` and is active. It runs on
pushes to `main` and on pull requests. A single
Linux job installs uv ([astral-sh/setup-uv](https://github.com/astral-sh/setup-uv)
with its built-in cache), pins Python via `uv python install 3.14`, then runs
`uv sync`, `uv run pytest -q`, `uv run ruff check src tests` (config in
`[tool.ruff]` in `pyproject.toml`), and the self-lint:
`uv run pypeeker index src && uv run pypeeker check` (see "Self-lint rule
adoption" above). The index+check pair is
the reference CI integration for consumer projects: index your sources, then
fail the build on rule violations.

## References

- **Rope** (Python) - semantic model and refactoring architecture inspiration
- **ts-morph** (TypeScript) - rich type-aware semantic model
- **rust-analyzer** (Rust) - incremental, IDE-grade analysis
- **libcst** (Python) - CST preservation for formatting fidelity
- **tree-sitter** - fast, incremental, multi-language parsing
