---
id: TASK-81
title: >-
  check: visibility-detection rules v1 (over-exposed symbols, exports, private
  reach-ins)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 18:38'
labels:
  - check
  - visibility
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Minimal-visibility principle, detection only: compute observed usage scope per symbol from resolved references and compare to declared visibility. Three rules: over-exposed-module-symbol (public, never referenced outside its module), over-exposed-export (barrel export no other package consumes), under-exposed-access (_private symbols referenced from outside their module, incl. tests).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 over-exposed-module-symbol flags public module-level symbols with zero cross-module references (dunder/main/dynamic-decorator allowlist exempt)
- [ ] #2 over-exposed-export flags __init__ re-exports never consumed outside the package
- [ ] #3 under-exposed-access flags cross-module references to single-underscore symbols, with test paths reported distinctly
- [ ] #4 All three opt-in with allow options; tests per rule; dogfood run over pypeeker recorded in notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Shared core in NEW src/pypeeker/check/builtin/visibility.py: map file_path->module via each index MODULE symbol; one pass over all references resolving each via context.resolver.resolve_reference to build canonical-def -> set(origin modules); package = leading dotted segments.
2. Rule over-exposed-module-symbol: public module-level FUNCTION/CLASS (kinds option, VARIABLE optional) with no reference originating outside its own module; exempt dunders, main, __main__.py, allow-decorators matches, barrel-exported defs, allow fnmatch.
3. Rule over-exposed-export: IMPORT symbols in __init__.py whose canonical definition is a real in-package definition; flag when every reference to that definition originates inside the package; allow fnmatch.
4. Rule under-exposed-access: iterate references; resolve target; if target visibility PROTECTED/PRIVATE (skip DUNDER) and origin module != defining module, flag at ref site; classify origin via test-globs option (default tests/**, test_*.py, *_test.py) and word message "accessed from tests" vs prod; allow fnmatch.
5. All three @register_rule(..., scope="project"), opt-in (not added to pyproject rules).
6. NEW tests/test_rule_visibility.py covering happy + exempt paths per rule using indexed_project + CheckContext (model: TestUnusedPublicSymbol).
7. Dogfood: copy src+tests to /tmp, pypeeker index, run the three rules via CheckContext; record findings in notes.
8. uv run pytest -q green.
<!-- SECTION:PLAN:END -->
