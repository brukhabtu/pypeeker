---
id: TASK-72
title: 'Query output: include resolution confidence in refs/symbol JSON'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:40'
labels:
  - query
  - resolve
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The capability+confidence model exists so consumers (LLMs) can calibrate trust, but refs --all output cannot distinguish a DECLARED match from one that relied on constructor-inferred receiver types. The declared_only machinery in CrossModuleResolver already knows the difference — surface it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 find_all_references / refs --all output marks each match with how it resolved (direct, import-alias, barrel, receiver-declared, receiver-inferred)
- [x] #2 Rename's declared_only gating reuses the same classification rather than a parallel code path
- [x] #3 JSON shape documented; tests cover at least declared vs inferred receiver matches
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. resolve.py: add ResolutionKind(str, Enum) with values direct, import_alias, barrel, receiver_declared, receiver_inferred; add frozen dataclass ResolvedReference(reference, via); add CrossModuleResolver.find_all_references_classified(symbol_id) and a private _classify helper. Receiver matches are classified by re-resolving with declared_only=True (matches -> receiver_declared, else receiver_inferred); non-receiver matches use _resolve_chain length (1 hop -> direct) and crosses_barrel (barrel vs import_alias).
2. Reimplement find_all_references as a thin filter over find_all_references_classified (declared_only=True keeps via != receiver_inferred) so classification is the single code path deciding declared-only semantics; planner keeps calling find_all_references(declared_only=True) and thereby reuses it (AC#2) - return type unchanged.
3. engine.py: expose find_all_references_classified delegating to the resolver.
4. cli.py refs --all: emit each reference dict plus a "resolution" field with the kind string; document the values in the refs command docstring (AC#3). Non---all output unchanged.
5. Tests: test_resolve.py classification cases (direct, import_alias, barrel, receiver_declared, receiver_inferred, declared_only filter equivalence); test_query_engine.py engine wrapper; test_cli.py refs --all resolution field present and non---all unchanged. Run uv run pytest -q and uv run pypeeker check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added ResolutionKind (direct, import_alias, barrel, receiver_declared, receiver_inferred) and frozen ResolvedReference dataclass plus CrossModuleResolver.find_all_references_classified/_classify in src/pypeeker/resolve.py. Receiver matches classified by re-resolving with declared_only=True (one extra bounded walk per receiver match); non-receiver matches via _resolve_chain length + crosses_barrel.
- find_all_references is now a thin filter over the classified results (declared_only drops receiver_inferred), so classification is the single code path deciding declared-only semantics; the planner keeps calling find_all_references(declared_only=True) and thereby reuses it (comment updated in refactor/planner.py).
- SemanticQueryEngine.find_all_references_classified exposes it; cli.py refs --all adds a "resolution" field per item and the command docstring documents the five values; non---all output unchanged.
- Discovery while testing: self.run() inside the same class is bound directly by the binder (no receiver walk), so it classifies as direct - test captures that.
- Tests: 10 new in test_resolve.py (all five kinds incl. self-field declared, constructor-inferred instance attribute, declared_only filter equivalence, engine wrapper) and 3 new in test_cli.py (resolution field values, import kinds, help documents values). Full suite: 651 passed, 10 skipped, 0 failed; uv run pypeeker check green.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Surfaced resolution confidence in refs --all JSON so consumers can tell HOW each reference matched, and unified rename's declared-only gating onto the same classification.

Changes:
- src/pypeeker/resolve.py: new ResolutionKind enum (direct, import_alias, barrel, receiver_declared, receiver_inferred) and frozen ResolvedReference(reference, via) dataclass; new CrossModuleResolver.find_all_references_classified(symbol_id). Receiver-walk matches are classified by re-resolving with declared_only=True - if the declared-only walk reaches the same canonical definition the match is receiver_declared, otherwise receiver_inferred (one extra bounded walk per receiver match). Non-receiver matches: resolve chain of length 1 -> direct; crossing an __init__ re-export -> barrel; otherwise import_alias.
- find_all_references keeps its signature/return type but is now a thin filter over the classified results (declared_only=True drops receiver_inferred), making classification the single code path that defines "declared only". Rename's planner (refactor/planner.py) keeps calling find_all_references(declared_only=True) and so reuses the classification; only the comment changed, behavior verified unchanged by test_planner.py/test_rename_cli.py.
- src/pypeeker/query/engine.py: SemanticQueryEngine.find_all_references_classified delegates to the resolver.
- src/pypeeker/cli.py: refs --all items each gain a "resolution" field with the kind string; the refs docstring (command help) documents all five values. Non---all refs output unchanged.

Tests:
- tests/test_resolve.py: classification tests for all five kinds (incl. self-field declared-annotation walk, constructor-inferred instance attribute, and same-class self.run() binding directly), declared_only filter equivalence, engine wrapper.
- tests/test_cli.py: refs --all shows resolution values (receiver_declared vs receiver_inferred; direct/barrel/import_alias), plain refs unchanged, help documents the values.
- uv run pytest -q: 651 passed, 10 skipped, 0 failed (baseline 638+13 new). uv run pypeeker check: green.

Risks/notes: declared_only semantics are now "default resolution matches AND declared-only resolution matches"; these differ from the old single declared-only pass only in pathological cases (a self/cls parameter carrying a conflicting inferred annotation), which cannot occur with the current binder.
<!-- SECTION:FINAL_SUMMARY:END -->
