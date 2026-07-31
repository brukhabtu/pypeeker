---
id: TASK-125
title: 'refactor: unify refusal vocabulary on PreconditionResult'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 18:03'
labels:
  - refactor
  - architecture
dependencies:
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5: PreconditionResult is the atom of refusal; DropReason.PRECONDITION_FAILED carries the named failing precondition; decline paths report the same shape everywhere.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Batch drops and plan refusals carry the named precondition; CLI refusal output unified; full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Convert the five phase-4 planners refusal checks into named Precondition objects (reuse FileExists/FileFresh/SymbolResolvesUniquely; add anchor/scan preconditions in refactor/preconditions.py), evaluated via evaluate_in_order like the classic planners.
2. Each precondition carries its legacy slug; MaterializeError.code derives from the failing precondition, so the frozen check --fix report reasons stay byte-identical.
3. DroppedIntent/SubmitError gain the failing precondition name additively; drop details unchanged.
4. New tests for named-precondition surfacing; oracles unmodified. Fold aspirational item 5 into current-state docs.
5. Workflow: sonnet implement, haiku gate, 3 opus lenses, fixer; orchestrator verifies + merges.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented: converted the five phase-4 planners (DeleteSymbolPlanner, RemoveImportPlanner, RewriteStarImportPlanner, TuplifyPlanner, ReplaceTextPlanner) from ad-hoc raise sites to named Precondition classes evaluated via evaluate_in_order, mirroring the classic planners.

- Added ~24 new Precondition classes to refactor/preconditions.py, each carrying a `slug: ClassVar[str]` matching its legacy check --fix refusal code (file-missing/stale-index/text-mismatch/ambiguous). Reused preconditions where wording matched exactly (none of the existing FileExists/FileFresh fit byte-for-byte, since their messages differ from the ported fix-protocol wording — kept those reserved for the classic planners and added AnchorFileExists/AnchorIndexFresh with the exact ported wording instead). Generic SymbolMatchUnambiguous/SymbolMatchFound/AnchorTextMatches are shared across delete/remove-import/rewrite-star-import/tuplify.
- Each *Error class (DeleteSymbolError, RemoveImportError, RewriteStarImportError, TuplifyError, ReplaceTextError) now derives `.code` from the failing precondition's `.slug` at the raise site (never hardcoded) and carries `.precondition` naming the failing precondition.
- MaterializeError, DroppedIntent, and SubmitError all gained an additive `precondition: str | None = None` field; batch.py's drop() and submit.py's submit_intent() populate it via getattr(outcome, "precondition", None).
- Classic planners (RenamePlanError, InlineVariableError, ExtractVariableError, ExtractMethodError, VisibilityOpError) now also populate `.precondition` from evaluate_in_order's failing precondition, and their materializers wrap it into MaterializeError.
- New tests/test_refusal_vocabulary.py covers one refusal path per converted planner plus one classic-planner (rename) case, asserting precondition name + derived slug + unchanged detail message on both SubmitError and DroppedIntent.
- architecture.md: folded aspirational item 5 ("One refusal vocabulary") into a new current-state paragraph under "Refactoring Model"; struck the aspirational bullet and migration-order entry as Done (TASK-125).

Gate: uv run pytest -q -> 1547 passed; uv run ruff check src tests -> clean; uv run pypeeker index src && uv run pypeeker check -> exit 0, no gated findings. Existing oracle tests (test_planner_ports.py, test_preconditions.py) unmodified and passing.

Review follow-up (post-review finding): converted the sixth phase-4 remedy planner, DocstringParamRenamePlanner (rename-docstring-param, refactor/docstring_ops.py), to the same named-Precondition/evaluate_in_order discipline as the other five — it had been missed in the original pass, leaving architecture.md's "Landed"/"Done" claims overstated by one planner.

- Added DocstringStillPresent, ParamsSectionPresent, DocumentedParamDriftSingle, DocumentedParamDriftMatches, DocstringScopeLocated, DocstringTextFound/Unique, DocstringTokenFound/Unique to refactor/preconditions.py (reusing SymbolMatchUnambiguous/SymbolMatchFound/AnchorFileExists/AnchorIndexFresh where wording matched exactly); DocstringParamRenameError.code now derives from the failing precondition's slug and carries .precondition, matching the other five *Error classes.
- All raise-site wording is byte-identical to the pre-change code (verified against tests/test_planner_ports.py::TestDocstringParamRenamePlanner, unmodified).
- text_anchor.current_state/AnchorStateError were only ever consumed by docstring_ops.py after the first five planners moved off them; converting docstring_ops.py made them dead code, flagged by the self-lint gate's over-exposed-module-symbol/unused-public-symbol rules. Removed both from text_anchor.py and updated its module docstring to point at AnchorFileExists/AnchorIndexFresh instead.
- architecture.md: updated the "One refusal vocabulary" paragraph, the struck migration-order item 5, and the phase-4 planner list to say six planners (added rename-docstring-param / docstring_ops.py) instead of five, so the "Landed"/"Done (TASK-125)" claims now hold for every phase-4 remedy planner, not five of six.

Gate: uv run pytest -q -> 1547 passed; uv run ruff check src tests -> clean; uv run pypeeker index src && uv run pypeeker check -> exit 0, no gated findings. tests/test_planner_ports.py and tests/test_refusal_vocabulary.py unmodified and passing.

Orchestrator wrap-up: review round produced 10 findings, 1 must-fix — the discovery that a SIXTH remedy planner (DocstringParamRenamePlanner in docstring_ops.py) had been missed by both the spec and the implementer. Fixer converted it to the precondition discipline, deleted the then-dead AnchorStateError/current_state helpers (surfaced by pypeeker own self-lint), and corrected docs to six planners. Independently verified: no hardcoded refusal slugs at any raise site (prose mentions only), 1547 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Unify the refusal vocabulary on PreconditionResult (phase 5).

What changed:
- All six phase-4 remedy planners (delete-symbol, remove-import, rewrite-star-import, tuplify, replace-text, rename-docstring-param) now validate through named Precondition objects evaluated via evaluate_in_order, exactly like the classic planners — ~25 new precondition classes in refactor/preconditions.py with failure wording preserved byte-for-byte.
- Precondition gained slug (ClassVar): the legacy check --fix refusal code lives on the precondition class, never at a raise site; planner errors derive code from the failing precondition and carry its name.
- Additive structured refusals: MaterializeError.precondition, DroppedIntent.precondition, SubmitError.precondition; classic planner errors also carry .precondition through their materializers.
- The sixth planner was caught by adversarial review (must-fix): the spec and implementer both missed docstring_ops.py; its conversion also let the dead AnchorStateError/current_state helpers be deleted (flagged by self-lint).
- Docs: aspirational item 5 folded into current-state text.

Contract: check --fix report byte-identical (slugs derived, wording unchanged); all pre-existing tests unmodified. Gate: 1547 pytest passed (+11 new in test_refusal_vocabulary.py), ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
