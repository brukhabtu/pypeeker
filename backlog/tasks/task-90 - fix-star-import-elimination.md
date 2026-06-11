---
id: TASK-90
title: 'fix: star-import elimination'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:44'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
from x import * leaves unresolved references only cross-module resolution can attribute. Rule detects star imports; fix resolves which names the star supplies to this module's unresolved references and rewrites an explicit import list. The ruff-cannot-do-this flagship.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags star imports; fix rewrites them to explicit sorted name lists covering every name actually used
- [x] #2 Names the resolver cannot attribute leave the fix declined (report, no rewrite); confidence-gated
- [x] #3 End-to-end test through plan-batch incl. a module using names from two star imports
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Binder: handle the wildcard_import child in visit_import_from_statement (binder/imports.py) — record an IMPORT symbol with local name "*" and imported_from=<relative-resolved module> via the existing declare_import path (verified: declaring "*" is inert for name lookup since no identifier is ever "*").
2. New builtin project rule star-imports (check/builtin/star_imports.py): flag every star import; compute used names = unresolved bare-name references matching public module-level surface of the target module (first-star-wins attribution across multiple stars; multi-star findings HEURISTIC, single-star DECLARED; __all__ filtering documented as unsupported in v1).
3. RewriteStarImportFix in the same module (fixes.py is frozen): plan() re-reads file + hash-verified index (_current_state), re-derives used names from the current store, text-verifies the "*" token on a "from X import *" line, emits one REPLACE of "*" -> sorted name list. Declines: STALE_INDEX/FILE_MISSING via shared helper; AMBIGUOUS for zero used names (suggest deletion), unattributable unresolved names, multiple stars, unindexed target.
4. Tests (tests/test_rule_star_imports.py): binder fact incl. relative star + shadowing suffix, rule findings/used-name computation, check --fix e2e on tmp project (content + re-resolution asserted), zero-used and unattributable declines, multi-star HEURISTIC w/o fix, plan-batch e2e with a two-star module, registration + opt-in (__file__-anchored pyproject).
5. Run uv run pytest -q and ruff; verify existing binder/import tests unmodified and green.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Verified by binding a snippet: `from m import *` previously left NO IMPORT symbol (the wildcard_import child was skipped) and star-supplied names arrive as unresolved bare references — exactly as suspected.
- Binder fact: added a wildcard_import branch in visit_import_from_statement recording an IMPORT symbol with local name "*" and imported_from=<relative-resolved module>, via the existing declare_import path. Declaring "*" through scope_stack.declare is inert for name lookup (no identifier is ever spelled "*") and gives shadow-suffixed ids (m:*, m:*$2) for multiple stars for free — least invasive option, asserted in tests. No existing test or source contains a star import; tests/test_binder.py unchanged and green.
- New builtin project rule star-imports (opt-in, auto-discovered): used names = unresolved bare refs matching the target module's public module-level surface; first-star-wins attribution (documented vs Python's last-wins; multi-star findings HEURISTIC, no fix); __all__ filtering documented unsupported in v1 (index records __all__ only as a VARIABLE); unindexed targets reported HEURISTIC with names unknown.
- RewriteStarImportFix lives in the builtin module (fixes.py frozen this wave); reuses _current_state/_position_to_byte_offset from check.fixes for the hash-verified anchoring discipline. Replaces ONLY the "*" token after regex-verifying the "from <module> import *" line shape, so indentation, relative module spelling, and trailing comments survive. Declines: STALE_INDEX/FILE_MISSING (shared helper), AMBIGUOUS for zero used names (suggests deletion, no auto-delete), unattributable unresolved names (stricter than spec: every unresolved bare name must be attributed to a star target in the file), plan-time multi-star, unindexed target; TEXT_MISMATCH for moved/odd star lines.
- Known interaction (follow-up candidate): the opt-in unused-imports rule now sees the "*" IMPORT symbol (zero references bind to it) and would flag a star import as unused, offering line deletion; unused_imports.py was out of scope this wave.
- 16 new tests in tests/test_rule_star_imports.py incl. check --fix e2e (content + re-resolution after re-index asserted) and a plan-batch fix sweep where a two-star module is reported-but-untouched. Full suite: 1254 passed; ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented star-import elimination: a binder fact, a project-scoped star-imports rule, and a confidence-gated autofix that rewrites `from m import *` into an explicit sorted import list — the cross-module attribution ruff cannot do.

Changes:
- binder/imports.py: `from m import *` now records an IMPORT symbol with local name "*" and imported_from=<relative-resolved module> (wildcard_import was previously skipped, leaving no fact at all). Recorded via the existing declare_import path — inert for name resolution, shadow-suffixed ids for multiple stars.
- check/builtin/star_imports.py (new, auto-discovered, opt-in): flags every star import with "star import from 'm' — N names actually used: a, b, c", where used names are the importing module's unresolved bare references matched against the target module's public module-level surface. Multiple stars use first-star-wins attribution (documented deviation from Python's last-wins shadowing) and are HEURISTIC with no fix; single-star findings are DECLARED. __all__ filtering is documented unsupported in v1 (its contents are not recoverable from the index); unindexed targets report unknown names, HEURISTIC.
- RewriteStarImportFix (same module; check/fixes.py untouched): plan() re-reads the file through a hash-verified index entry, re-derives used names from the current store, verifies the "*" token and its `from <module> import *` line, and emits one REPLACE of the "*" with the sorted name list — preserving relative module spelling and trailing comments. Declines STALE_INDEX/FILE_MISSING per the standard discipline and AMBIGUOUS when zero names are used (suggests deletion; never auto-deletes), when any unresolved bare name is unattributable (the star might supply it transitively), when the file gained a second star, or when the target is unindexed.

Tests (tests/test_rule_star_imports.py, 16 tests):
- binder fact incl. relative stars and $2-suffixed ids; existing binder tests untouched and green
- rule attribution, zero-used/unindexed/multi-star/__all__ behavior, registration + opt-in
- fix rewrite (relative import + comment preserved) and all decline paths
- check --fix e2e: star line becomes the explicit sorted list and the previously-unresolved references resolve after the applier's re-index
- plan-batch fix sweep e2e incl. a module using names from two star imports: the single-star module is rewritten, the two-star module is reported-but-untouched (AC #3)

Risks/follow-ups: the opt-in unused-imports rule now sees the "*" IMPORT fact and would flag a never-attributable star as an unused import with a line-deletion fix; teaching it to skip "*" bindings is a small follow-up (unused_imports.py was out of scope this wave).

Verification: uv run pytest -q — 1254 passed; ruff clean.
<!-- SECTION:FINAL_SUMMARY:END -->
