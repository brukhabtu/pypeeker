---
id: TASK-23
title: 'Binder: skip keyword-argument names in calls'
status: To Do
assignee: []
created_date: '2026-05-12 15:42'
labels:
  - binder
  - linter
dependencies:
  - TASK-22
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
In code like `dataclass(frozen=True)` or `Field(name='foo')`, the binder treats the keyword name (`frozen`, `name`) as an identifier reference, which then shows up as unresolved. Keyword names are not expressions — they are syntactic markers — and should not produce a reference.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Keyword arguments (`func(kwarg=value)`) do not produce identifier references for the keyword name itself
- [ ] #2 The value expression of a keyword argument is still visited (e.g. `func(x=other_name)` should still reference `other_name`)
- [ ] #3 Test with @dataclass(frozen=True), Field(name='x'), and nested kwargs
- [ ] #4 pypeeker check on its own source no longer reports unresolved refs for kwarg names like 'frozen', 'line', 'method', 'qualified_name'
<!-- AC:END -->
