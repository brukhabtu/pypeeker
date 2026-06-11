---
id: TASK-92
title: 'fix: privatize-dead-API (demote unused/test-only public symbols)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:51'
labels:
  - fix
  - visibility
  - m4-program-fixes
dependencies:
  - TASK-79
  - TASK-81
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Findings from unused-public-symbol and test-only-production-code get a mechanized fix: rename name -> _name across the project, drop barrel exports/__all__ entries, transactionally via the batch planner.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Fix plans demotion renames incl. barrel/__all__ updates for unused-public and test-only findings
- [x] #2 Symbols with heuristic-confidence findings (dynamic access nearby) are excluded from auto-fix
- [x] #3 End-to-end batch demotion test over a fixture package; dogfood plan over pypeeker recorded (not applied) in notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. NEW src/pypeeker/refactor/privatize.py: demote_candidates(store, entries, skip_heuristic) pre-filters plain symbol ids (or (id, confidence) pairs) into DemoteCandidate/SkippedSymbol with reasons: not-found/ambiguous, already-private, dunder-or-main, heuristic-confidence (AC#2), hierarchy-unsafe (analysis.hierarchy overrides/overridden_by/mro_unknown), protected-public-api (library-mode public roots, minimally replicated from VisibilityPlanner with comment), name-collision (existing _name in scope) and pending-collision (deterministic first-wins among the batch).
2. demote_intents(candidates): RenameIntent name->_name per candidate with include_exports mirroring plan_demote (barrel-exported => rewrite export + consumers); keep_export deliberately out of batch scope (single-symbol CLI), documented.
3. plan_privatize(store, tx_store, entries, ...): run_batch on a temp mirror, flatten_batch, stamp operation "privatize", persist; returns PrivatizeOutcome(summary, executed, dropped, skipped, warnings). API documented for TASK-97 reuse (no check imports; callers pass (symbol_id, confidence) extracted from violations).
4. NEW tests/test_privatize.py: per-reason pre-filter inventory incl. HEURISTIC exclusion + deterministic pending-collision; end-to-end fixture package (plain + barrel-exported + base-overriding method) -> plan -> apply -> renames incl. barrel rewrite, hierarchy skip, files still compile + re-index clean, rollback restores bytes.
5. Dogfood: index a /tmp copy of src/pypeeker, run plan_privatize over the four known test-only ids + unused-public ids from task-81 notes; record planned (not applied) outcome in notes.
6. uv run pytest -q green; ruff clean.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented NEW src/pypeeker/refactor/privatize.py (3-layer reuse API, documented for TASK-97):
- demote_candidates(store, entries, skip_heuristic=True): entries are plain symbol ids or (symbol_id, confidence_str) pairs — the cross-layer contract (refactor may not import check; callers reduce each violation to the pair). Skip reasons: not-found, ambiguous, dunder-or-main (checked before already-private so dunders report precisely), already-private, heuristic-confidence (AC#2), hierarchy-unsafe (analysis.hierarchy overrides/overridden_by + conservative mro_unknown of the owning class), protected-public-api (library-mode public roots; minimal replica of VisibilityPlanner._refuse_if_public_root_protected with comment — the helper is a private planner method needing a tx store), name-collision (_name already bound in scope), pending-collision (first entry wins deterministically; covers duplicates and shadowed $N re-definitions).
- demote_intents(candidates): RenameIntent name->_name, include_exports mirrors plan_demote app-mode (barrel-exported => rewrite export + consumers); keep_export never set — batch is export-rewrite mode only; keep-export stays single-symbol via the demote CLI (documented: RenameIntent could carry it, but mixing surface-preserving and surface-changing demotions in one mass batch makes the public API a function of batch composition).
- plan_privatize(store, tx_store, entries, ...): run_batch on a temp mirror -> mirror post-pass rewriting stale __all__ entries ("name"->"_name" in barrel __init__s and the defining file; literal list/tuple only, same limits as visibility_ops) -> flatten_batch -> header stamped operation "privatize" -> persisted. Returns PrivatizeOutcome(summary, executed, dropped, skipped, warnings), mirroring the plan-batch CLI report shape.

Found + closed during implementation: the rename engine (and thus single-symbol plan_demote) rewrites the barrel import line but NOT __all__ string literals — __all__ = ["helper"] went stale after demotion. plan_privatize closes this for batch demotion via the mirror post-pass (entry rewritten to the private name, consistent with export-rewrite mode: the import line now binds _name, so star-import consumers keep working). Single-symbol plan_demote still has the gap (visibility_ops not mine to touch this wave).

Tests: NEW tests/test_privatize.py, 26 tests — pre-filter inventory (one per skip reason incl. heuristic exclusion + opt-in, deterministic pending-collision for duplicates AND shadowed $2 definitions, same-name-different-scopes allowed, input order preserved), intent lifting, end-to-end fixture package (plain + barrel-exported + base-overriding method): plan -> apply -> plain and barrel symbols renamed everywhere incl. barrel `from pkg.core import _exported` rewrite and app consumer, override method skipped hierarchy-unsafe, files compile, re-index introduces no new unresolved refs (CrossModuleResolver before/after set comparison), rollback restores bytes byte-identically; __all__ entries follow the demotion in barrel + defining module; all-skipped => no transaction persisted; heuristic finding never reaches the transaction. Full suite 1330 passed, ruff clean.

Dogfood (AC#3, planned only — NOT applied to the real tree): indexed a /tmp copy of src/pypeeker (82 files), fed plan_privatize 13 entries — the four known test-only ids (pypeeker.models.capabilities:Capability, pypeeker.models.symbol_id:unresolved_attr_id/strip_shadow/shadow_suffix, declared confidence) + 8 unused-public/over-exposed ids from the TASK-81 dogfood notes (binder declare_import, visit_with_item, receiver_metadata, ScopeEntry, visit_module, visit_node, BinderState; adapters LanguageAdapter) + one duplicate (visit_node) tagged heuristic.

Result: 12 executed, 0 dropped, 1 skipped (the heuristic visit_node entry — AC#2 in action; the untagged submission of the same id executed). One flattened tx (op=privatize, 13 edits over 13 files): defining files + consumers (check/builtin/no_hidden_global_mutation.py and refactor/footprint.py rewrote their strip_shadow/shadow_suffix uses — those two of the "test-only" ids have since grown production consumers; the rename followed them correctly). 3 barrel warnings: visit_module/visit_node/BinderState are barrel-exported by pypeeker.binder — planned __init__ rewrite: `from pypeeker.binder.binder import bind, _visit_module, _visit_node` / `from pypeeker.binder.state import _BinderState` AND `__all__ = ["_BinderState", "bind", "_visit_module", "_visit_node"]` (the __all__ post-pass). Validation on the throwaway copy: tx applied cleanly (0 reindex failures), every file still compiles. Copy deleted; real tree untouched (git status: only the new privatize.py).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added batch demotion of over-exposed public symbols (name -> _name) as NEW src/pypeeker/refactor/privatize.py: findings from unused-public-symbol / test-only-production-code (and the TASK-81 over-exposure rules) now have a mechanized, transactional fix routed through the TASK-88/89 batch machinery, producing ONE flattened pending transaction that the existing apply/rollback execute unchanged.

Changes:
- demote_candidates: pre-filters plain symbol ids (or (symbol_id, confidence) pairs) into candidates vs machine-readable skips — not-found/ambiguous, dunder-or-main, already-private, heuristic-confidence (dynamic-access findings excluded from auto-fix, opt-in via skip_heuristic=False), hierarchy-unsafe (overrides/overridden_by/mro_unknown via analysis.hierarchy), protected-public-api (library-mode public roots), name-collision, and deterministic pending-collision among the batch itself (duplicates and shadowed re-definitions; first entry wins).
- demote_intents: one RenameIntent per candidate with include_exports mirroring plan_demote app-mode barrel handling; batch is export-rewrite mode only (keep-export stays a single-symbol CLI decision, documented).
- plan_privatize: run_batch on a temp mirror, a mirror post-pass that rewrites stale __all__ entries (barrel __init__s + defining module; the rename engine does not touch string literals — gap found and closed here for the batch path), flatten_batch, persisted with operation "privatize"; returns PrivatizeOutcome (summary + executed/dropped/skipped/warnings, plan-batch CLI report shape).
- Reuse contract for TASK-97 documented in docstrings: refactor never imports check; callers extract (symbol_id, confidence_str) from violations and compose the three layers exactly as plan_privatize does.

Tests: NEW tests/test_privatize.py (26) — full skip-reason inventory, intent lifting, end-to-end fixture package (plain + barrel-exported + base-overriding method) with apply/compile/re-index-no-new-unresolved-refs/rollback-byte-identity, __all__ follow-through, heuristic exclusion end-to-end. Full suite 1330 passed; ruff clean.

Dogfood: planned (not applied) over a /tmp copy of pypeeker — 12 demotions executed into one 13-edit transaction incl. the pypeeker.binder barrel + __all__ rewrite, heuristic-tagged entry excluded, tx applied cleanly on the throwaway copy and all files compiled; recorded in notes.

Risks/follow-ups: single-symbol plan_demote still leaves __all__ string entries stale (visibility_ops untouched this wave); __all__ rewrite assumes a literal list/tuple assignment (same documented limit as visibility_ops); TASK-97 wires this module to actual check findings and a CLI surface.
<!-- SECTION:FINAL_SUMMARY:END -->
