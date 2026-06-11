---
id: TASK-94
title: 'analysis: class hierarchy facts (bases, overrides)'
status: Done
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

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added class-hierarchy facts (bases + override edges) and a flag-gated rename safety check so renaming one side of an override pair is no longer silent breakage.

Changes:
- New src/pypeeker/analysis/hierarchy.py: Hierarchy built from indexes + CrossModuleResolver (Hierarchy.build / Hierarchy.from_store, overlay-aware source reads). Per class: bases() -> list[BaseRef] with the canonical project class id or an unknown marker for external/stdlib/dynamic bases (metaclass= keywords excluded, Base[T] stripped to Base); mro_unknown() true on any unknown base, declared-base cycle, or chain past the depth cap (32). Per method: overrides()/overridden_by() via cycle-safe, depth-capped walks; name-mangled privates never pair. Base refs are found via a documented discriminator: refs in the class parent scope whose location falls inside the class scope span, matched against the header argument list re-read from source (the binder visits superclasses before pushing the class scope). All parse failures degrade to unknown, never to a wrong known edge.
- analysis/__init__.py exports Hierarchy and BaseRef.
- refactor/planner.py: new MethodOverrideSafe precondition (method-override-safe) yielded in _iter_preconditions only for METHOD symbols, building the hierarchy lazily in evaluate(); RenamePlanner.plan/preconditions gain allow_override_rename=False. Refusals name the related method ids and distinguish "overrides X" / "is overridden by Y" / "hierarchy incomplete" (conservative refusal when the owning class has unresolved bases).

Tests:
- tests/test_hierarchy.py (24 tests): single inheritance, cross-module and barrel-imported bases, project-Protocol implementation edges, external/builtin/dynamic unknown bases, metaclass/subscript/multiline-header discrimination, cross-module cycle safety, depth cap, mangled privates, source-unavailable degradation.
- tests/test_planner.py (+8 appended): refusal in both override directions, allow_override_rename override, incomplete-hierarchy refusal + override, non-method and plain-class-method renames unaffected, precondition listed in the enumerable set.
- Full suite: uv run pytest -q -> 991 passed; ruff clean.

Follow-ups/risks: implementing an external (typing.Protocol-based) protocol is undetectable by design — such classes surface as mro_unknown and rename stays conservative. TASK-96 (promote/demote) can consume Hierarchy directly.
<!-- SECTION:FINAL_SUMMARY:END -->
