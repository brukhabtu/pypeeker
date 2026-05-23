# AST-Based Parser & Refactoring Tool Architecture

## Overview

A semantic code intelligence system designed to give LLMs and developers reliable tools for understanding codebases, linting, and performing large-scale refactorings safely.

## Core Architecture

Three layers, each with clear responsibilities:

### Layer 1: Language Adapters

Each language implements an adapter that:
- Parses source to CST (preserving whitespace/comments for refactoring)
- Extracts symbols, scopes, and references
- Maps language-specific concepts to unified model
- Declares its capabilities (what semantic info it can reliably provide)
- Handles language-specific import resolution

The adapter owns the CST and knows how to modify it for refactoring operations.

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
2. **Capability-based** - adapters declare what they can provide, consumers check before relying on it
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
- `tree` → `models`, `storage`, `paths`
- `check` → `models`, `storage`
- `query` → `models`, `storage`, `tree`, `resolve`
- `analysis` → `models`, `storage`, `query`
- `indexer`, `refactor` → binder/adapters/storage/query as needed
- `cli` — composition root, unconstrained

The rule uses each file's `MODULE` symbol (its dotted module path) and its
`IMPORT` symbols, mapping both to their package under the project root, so
layering violations and regressions surface in CI rather than in review.

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

**Capabilities** - adapters declare what they can provide:
- VISIBILITY, STATIC_TYPES, TYPE_INFERENCE, INTERFACES, GENERICS, MUTABILITY, NULLABILITY, IMPORT_RESOLUTION, CALL_GRAPH

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
┌─────────┐
│  Lexer  │ → Token Stream (with trivia for CST)
└─────────┘
    │
    ▼
┌─────────┐
│ Parser  │ → CST (Concrete Syntax Tree)
└─────────┘
    │
    ▼
┌─────────┐
│ Binder  │ → Symbol Table + Scope Tree
└─────────┘
    │
    ▼
┌─────────┐
│ Checker │ → Type Info + Diagnostics
└─────────┘
    │
    ▼
┌─────────────────────────────────────┐
│          Semantic Model             │
│  (queryable, the thing LLMs use)    │
└─────────────────────────────────────┘
```

## Refactoring Model

Transactional approach inspired by Rope (Python refactoring library):

1. **Plan** - analyse what would change, identify affected symbols
2. **Validate** - check for naming conflicts, scope issues, breaking changes
3. **Execute** - apply changes atomically
4. **Rollback** - undo if needed

Key operations: rename, move, extract function, inline, change signature

**Re-exports are a public API surface.** A package barrel (`__init__.py`
re-export) deliberately exposes a name to the outside world, so "rename the
definition" and "rename the public export" are genuinely different intents.
Renaming `pkg.lib:X` need not change the public name `pkg.X` — keeping the
export stable via `from pkg.lib import NewName as X` is a valid outcome. The
`--include-exports` flag today conflates these: it rewrites the export to the
new name. The intended split is to keep `--include-exports` for "propagate the
rename through barrels (and their consumers)" and add a separate
alias-preserving mode for "rename the definition but hold the public name",
rather than overloading one flag. Transitive barrel-consumer updates are only
sound when the barrel itself is updated, so they are gated on the same flag:
without `--include-exports` a barrel consumer is left untouched; with it, the
definition, the `__init__` re-export, and the consumer's import and call sites
are all rewritten. A still-open follow-up is the alias-preserving mode.

## LLM Integration

Simple CLI tool that LLMs call directly. No SDK or protocol complexity.

```
semantic-tool <command> [args]
```

**Core commands:**

- `index <path>` - index a codebase
- `symbol <name>` - get symbol info + references
- `refs <symbol-id>` - find all references
- `scope <file:line>` - what's visible at this location
- `plan-rename <symbol-id> <new-name>` - preview rename
- `apply <plan-id>` - execute a planned refactoring
- `lint [rules]` - run linting rules
- `search <query>` - semantic symbol search

Output as JSON for easy parsing. LLM calls CLI, parses response, reasons, calls another command if needed.

Benefits:
- Testable independently
- Usable by humans directly
- No protocol overhead
- Works with any LLM tool-use implementation

## References

- **Rope** (Python) - semantic model and refactoring architecture inspiration
- **ts-morph** (TypeScript) - rich type-aware semantic model
- **rust-analyzer** (Rust) - incremental, IDE-grade analysis
- **libcst** (Python) - CST preservation for formatting fidelity
- **tree-sitter** - fast, incremental, multi-language parsing
