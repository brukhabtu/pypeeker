---
id: TASK-94
title: 'analysis: class hierarchy facts (bases, overrides)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 19:21'
labels:
  - analysis
  - m5-visibility
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The model binds superclass references but builds no hierarchy: privatizing or renaming a method that overrides a base / implements a Protocol / is overridden breaks contracts invisibly. Add hierarchy facts: per class, resolved base ids (via CrossModuleResolver); per method, overrides/overridden-by. Rename gains a safety check; visibility demotion requires it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Hierarchy query resolves base classes cross-module (unresolvable/external bases marked unknown) and computes overrides/overridden-by per method
- [x] #2 Rename planner warns or refuses (flag-gated) when renaming only one side of an override pair
- [x] #3 Tests cover single inheritance, cross-module bases, Protocol implementation, and unknown external bases
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Bind a scratch snippet to inspect how base-class references appear (scope, span, kinds)
2. Implement analysis/hierarchy.py: Hierarchy.build(indexes, resolver) with bases(), methods_overriding(), overridden_by(), mro_unknown(); document discriminator
3. Export from analysis/__init__.py
4. Add MethodOverrideSafe precondition wired into RenamePlanner._iter_preconditions with allow_override_rename flag (lazy Hierarchy build for METHOD symbols)
5. Tests: tests/test_hierarchy.py + appended planner tests
6. ruff + pytest, check ACs, final summary
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Bound a scratch snippet first: base refs live in the class PARENT scope with locations inside the class scope span (binder walks superclasses before pushing the class scope); metaclass= values and subscript args (int in Base[int]) are indistinguishable by location alone, so header text is re-read from source to split positional bases from keywords/dynamic segments.
- analysis/hierarchy.py: Hierarchy.build(indexes, resolver, read_source) + Hierarchy.from_store(store); BaseRef(text, class_id|None); bases(), overrides(), overridden_by(), mro_unknown(); depth cap 32, path-based cycle detection (diamonds OK), name-mangled privates excluded from override pairing; every failure mode degrades to unknown, never a wrong known edge.
- planner.py: MethodOverrideSafe precondition (name method-override-safe), yielded in _iter_preconditions only for METHOD symbols (hierarchy built lazily in evaluate); new allow_override_rename=False flag on plan()/preconditions(); messages distinguish overrides X / is overridden by Y / hierarchy incomplete.
- Conditional yield keeps the non-method rename precondition set unchanged (tests/test_preconditions.py asserts the exact list; not editable in this task).
- uv run pytest -q: 991 passed; ruff clean on all touched files.
<!-- SECTION:NOTES:END -->
