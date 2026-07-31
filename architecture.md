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
  `refactor` (today only `refactor`/`app` consume it; `check` gains it when
  `Violation.remedy` lands)
- `check` → `models`, `project`, `storage`, `resolve`, `treebuild`, `analysis`, `query`
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

Key operations: rename, extract (variable/method), inline, visibility changes
(promote/demote/privatize), and batch, plus the five planners behind `check`'s
remedies — `delete-symbol` (`delete.py`), `remove-import` and
`rewrite-star-import` (`imports_ops.py`), `tuplify` (`literals.py`), and
`rename-docstring-param` (`docstring_ops.py`) — which share the hash-verified
re-anchoring discipline in `text_anchor.py`. `replace-text` (`text_ops.py`) is
the ported reference text-anchored op: no rule attaches it, and it keeps the
weaker text-only guarantee of the fix it ports (unique-occurrence re-anchoring,
no index-freshness gate). `move` and `change signature` are
roadmap items, not yet implemented.

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

## Target architecture: the four-noun model (ASPIRATIONAL)

> **Status: aspirational.** This section describes where the codebase is agreed to be
> heading (2026-07), not where it is. Sections above describe the current state. As each
> phase lands, fold the corresponding claims into the sections above and shrink this one;
> delete the section when it is fully true. The migration is tracked in Backlog
> (`unify fixes into the refactor layer` task family).

### The four nouns

Everything the system does reduces to a pipeline over four concepts:

| Noun | Question it answers | Produced by | Lives in |
|---|---|---|---|
| **Model** | what *is* the code? | `bind` | `models/` (Symbol, Scope, Reference, FileIndex) |
| **Trait** | what can we *say* about it? | analysis | `analysis/` — always `(value, confidence)` |
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

1. **`intents/` becomes a leaf package** holding `Intent`, `Footprint`, `Effect`
   (moved from `refactor/`) plus a small `Anchor` union
   (`SymbolAnchor | RangeAnchor | EdgeAnchor`) shared by findings and intents so
   `remap` works uniformly and a violation's remedy survives earlier renames in the
   same batch. Both `check` and `refactor` may import `intents`; **`check` still never
   imports `refactor`** — rules say *what* should change, only planners know *how*.
2. ~~**The `Fix` protocol dies.**~~ **Landed (TASK-124).** `check/fixes.py` and
   `check/protocols.py` are deleted along with `FixIntent`; each fix is a planner in
   `refactor/` and `Violation.remedy: Intent | None` is how a rule proposes a repair.
   See "The `check` framework / rule library split" above for the current state.
3. **Everything is a batch.** The direct-planner execution path is removed: a single
   refactor is a batch of one. Every mutating entry point — `rename`, `inline`,
   `extract`, `privatize`, `check --fix` — is sugar for *submit intents → schedule →
   materialize → one transaction*, giving one pipeline for conflicts, ordering,
   apply, and rollback. The uniform CLI grammar (`--plan` to plan-only, `apply`,
   `rollback` working identically everywhere) falls out of this rather than being a
   separate project.
4. **One registration idiom.** `@register_planner(IntentKind)` replaces
   `batch._materialize`'s isinstance dispatch, mirroring `@register_rule`. Adding a
   capability always means dropping in a module that registers itself — rules, planners,
   and (later) trait providers all follow the same pattern.
5. **One refusal vocabulary.** `PreconditionResult` is the atom of "why not."
   Batch's `DropReason.PRECONDITION_FAILED` carries *which named precondition* failed;
   `ORPHANED`/`CONFLICT_DROPPED` remain. The four refusal slugs the remedy planners
   raise today (and `check --fix` reports) map onto existing preconditions
   (`file-missing`→`FileExists`, `stale-index`→`FileFresh`,
   `ambiguous`→`SymbolResolvesUniquely`, `text-mismatch`→anchor verification).
6. **One home for confidence (later phase).** `Trait = (value, confidence, provenance)`
   registered per primitive kind; rules quantify traits, preconditions verify them
   pointwise, and the scattered `visibility_confidence` / `import_confidence` /
   `TypeAnnotation.confidence` fields migrate into it. A fix's real precondition
   becomes "the rule still fires here" — re-run the predicate, not a file-hash check.

### Walls this makes visible (pre-existing, to lift during migration)

- `flatten_batch` refuses created/deleted/renamed files — blocks a future `move-symbol`
  planner; the batch engine must learn file birth/death.
- Scheduling is single-pass (`MAX_PLAN_ATTEMPTS_PER_INTENT = 1`) — cascading remedies
  (remove import → symbol becomes unused → delete symbol) need a fixpoint or a re-run.
- The temp-dir mirror is a stopgap for planners reading bytes off disk; once planners
  read through the store, `OverlayIndexStore` replaces the mirror and batch-of-one
  costs nothing.

### Migration order

1. Extract `intents/` leaf (+ `Anchor`), update import-boundaries. Behavior-preserving.
2. `@register_planner` registry; `_materialize` becomes a lookup. Behavior-preserving.
3. Everything-is-a-batch: single-op CLI commands route through the batch engine.
4. ~~Convert the five fixes to intents+planners; `Violation.remedy`; delete the Fix
   protocol and `FixIntent`.~~ **Done (TASK-124).**
5. Refusal-vocabulary unification (`PreconditionResult` everywhere).
6. Uniform CLI grammar (`--plan`/`apply`/`rollback`).
7. Traits foundation (value+confidence+provenance; migrate a first rule/precondition
   pair as proof).

Each phase lands green (pytest + ruff + self-lint) and is independently shippable.

## LLM Integration

Simple CLI tool that LLMs call directly. No SDK or protocol complexity.

```
pypeeker <command> [args]
```

**Implemented commands:**

- `index <path>` - index a codebase
- `check` - run linting rules (configured under `[tool.pypeeker]`)
- `symbol <name>` - get symbol info + references
- `refs <symbol-id>` - find all references
- `tree [symbol-id]` - browse the cross-file symbol tree
- `purity <symbol-id>` - report a function's purity and side effects
- `scope <file:line>` - what's visible at this location
- `plan-rename <symbol-id> <new-name>` - preview rename
- `plan-extract-variable <file> <start> <end> <name>` - preview extract variable
- `plan-extract-method <file> <start> <end> <name>` - preview extract method
- `plan-inline-variable <symbol-id>` - preview inline variable
- `plan-batch <spec>` - preview a batch of refactorings from one intent spec
- `promote <symbol-id>` - preview making a symbol public
- `demote <symbol-id>` - preview making a symbol private
- `privatize <symbol-id>` - preview privatizing an unused public symbol
- `apply <tx-id>` - execute a planned refactoring
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

### Output contract (stable, additive-only)

Two consumer-facing contracts are treated as **frozen** — they evolve
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
