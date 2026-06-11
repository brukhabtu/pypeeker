---
id: TASK-82
title: 'check/fix: fix protocol — violations carry plannable fixes'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 19:13'
labels:
  - check
  - refactor
  - m2-fixes
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Rules emit Violations; refactors emit transactions; nothing connects them. Define a Fix protocol: a violation may carry a fix planner that, given current state, yields EditEntry objects (or declines). Registry plumbing so rules register fixes alongside detection. Foundation for check --fix and the composite planner's intents.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A rule can attach a fix planner to a violation; fix planners produce EditEntries against current file state and may decline (stale)
- [x] #2 Violations without fixes are unchanged; existing rules untouched
- [x] #3 Unit tests cover fix production, decline, and the no-fix path
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add Fix protocol module check/fixes.py: Fix (Protocol with fix_id/description/plan), FixPlan (list[EditEntry]), FixDeclined (machine-readable DeclineReason enum), ReplaceTextFix reference implementation (location-anchored, text-verified, re-resolves at plan time via IndexStore), with_fix() helper
2. Extend Violation with optional fix field (default None, compare=False, repr=False) preserving (file_path, line, rule, message) sort semantics
3. Export Fix/FixPlan/FixDeclined/DeclineReason/ReplaceTextFix/with_fix from check/__init__
4. tests/test_fix_protocol.py: plan->apply round-trip via TransactionStore/Applier, decline on text mismatch, re-plan after benign edit, ambiguity decline, no-fix violations + sort-order regression, str/repr unchanged
5. uv run pytest -q + ruff check on touched files
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/check/fixes.py: DeclineReason enum (stale-index/text-mismatch/ambiguous/file-missing), FixPlan (fix_id, description, list[EditEntry]), FixDeclined (fix_id, reason, detail), runtime-checkable Fix protocol (fix_id, description, plan(store: IndexStore) -> FixPlan | FixDeclined), ReplaceTextFix reference implementation, with_fix(violation, fix) helper (THE attachment idiom, dataclasses.replace on the frozen Violation).
- ReplaceTextFix re-resolves its anchor at plan time: exact (line, column, expected-text) match first (RenamePlanner._build_edits pattern), else unique-occurrence re-anchor (benign edits re-plan), else TEXT_MISMATCH/AMBIGUOUS decline. Offsets and file_hash always computed from bytes read at plan time, never cached from detection.
- Violation gained fix: Fix | None = field(default=None, compare=False, repr=False); compare=False keeps (file_path, line, rule, message) ordering, equality, and hashing byte-identical to before; repr=False keeps output unchanged. Docstring notes Violation is in-memory only (never serialized).
- Exported Fix/FixPlan/FixDeclined/DeclineReason/ReplaceTextFix/with_fix from check/__init__ (export lines only).
- tests/test_fix_protocol.py: 13 tests — plan->TransactionStore->TransactionApplier round-trip with fresh hashes, anchor-wins-over-duplicates, all three decline paths, re-plan after benign edit (and applying the re-plan), with_fix idiom, no-fix regression (eq/hash/str/repr), mixed-fix sort-order regression, dataclass field contract guard.
- ruff clean on touched files; full suite 921 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Defined the fix protocol connecting rule violations to plannable edits, the foundation for check --fix (TASK-84) and guarded fix intents (TASK-88).

Changes:
- New src/pypeeker/check/fixes.py: runtime-checkable Fix protocol (stable fix_id, description, plan(store: IndexStore) -> FixPlan | FixDeclined); FixPlan carries list[EditEntry] with byte offsets and file hashes computed from bytes read at plan time; FixDeclined carries a machine-readable DeclineReason (stale-index, text-mismatch, ambiguous, file-missing) plus a human detail string. Fixes are replannable by contract: no detection-time byte offsets are cached; anchors are re-resolved and re-verified against current file state on every plan() call, or the fix declines.
- ReplaceTextFix reference implementation: location+expected-text anchored (same verify-text-at-anchor pattern as RenamePlanner._build_edits), with unique-occurrence re-anchoring so benign unrelated edits re-plan cleanly while text mismatches and ambiguous duplicates decline.
- with_fix(violation, fix) is the documented single idiom for rules to attach fixes.
- Violation (check/models.py) gained an optional fix field with default None, compare=False, repr=False — equality, hashing, repr, str, and the engine's (file_path, line, rule, message) sort order are all byte-identical to before; existing rules and tests untouched. Violation remains in-memory only (nothing serializes it; documented in the docstring).
- check/__init__ exports Fix, FixPlan, FixDeclined, DeclineReason, ReplaceTextFix, with_fix.

Tests:
- tests/test_fix_protocol.py (13 tests): end-to-end plan -> TransactionStore -> TransactionApplier round-trip against a tmp indexed project with hash verification; all decline paths; re-plan-after-benign-edit including applying the re-planned edits; no-fix and mixed-fix sort-order regressions; field-contract guard.
- Full suite: 921 passed; ruff clean on touched files.

No engine/CLI changes (deferred to TASK-84 by design).
<!-- SECTION:FINAL_SUMMARY:END -->
