---
id: TASK-23
title: 'Binder: skip keyword-argument names in calls'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-12 15:42'
updated_date: '2026-05-12 20:40'
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
- [x] #1 Keyword arguments (`func(kwarg=value)`) do not produce identifier references for the keyword name itself
- [x] #2 The value expression of a keyword argument is still visited (e.g. `func(x=other_name)` should still reference `other_name`)
- [x] #3 Test with @dataclass(frozen=True), Field(name='x'), and nested kwargs
- [x] #4 pypeeker check on its own source no longer reports unresolved refs for kwarg names like 'frozen', 'line', 'method', 'qualified_name'
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Inspect tree-sitter parse for `func(frozen=True)` to confirm the kwarg structure (likely keyword_argument node with name + value fields).
2. Add a visit_keyword_argument handler in binder/references.py (or wire into the binder.py dispatch) that marks the name identifier as a declaration (so visit_identifier skips it) and recurses into the value expression normally.
3. Add tests: @dataclass(frozen=True), Field(name=...), nested kwargs func(a=b, c=other_call(x=y)).
4. Re-index pypeeker source and confirm no more `frozen`/`line`/`method`/`qualified_name` false positives in pypeeker check.
5. Commit, push, PR, merge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added visit_keyword_argument in binder/references.py: marks the name identifier as a declaration (so visit_identifier skips it) and recurses into the value via visit_node.
- Wired keyword_argument into binder.py dispatch.
- 8 new tests covering: simple kwarg name dropped, dataclass(frozen=True), multiple kwargs, nested kwargs, kwarg value identifier still resolves, kwarg value call still tracked, kwarg value attribute chain visited, same name on both sides only produces the read.
- Full suite 342 passed.
- Re-indexed pypeeker source: pypeeker check went from 313 violations to ~98 (the rest are TASK-24 forward refs + one separate comprehension-variable gap).
- Confirmed: no more `frozen`, `qualified_name`, `method`, or other kwarg-name false positives.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Drop keyword-argument names from the reference index.

## Why

In calls like \`@dataclass(frozen=True)\` or \`Field(name='foo')\`, the binder was treating the keyword name (\`frozen\`, \`name\`) as an identifier reference. They'd land in the index as unresolved refs and trigger every downstream consumer that filters on resolution status — including the \`no-unresolved-refs\` linter rule, which surfaced this by reporting them on pypeeker's own source.

Keyword names are syntax, not expressions. They shouldn't produce references.

## What

- New \`visit_keyword_argument\` visitor in \`binder/references.py\`:
  - Marks the name identifier as a declaration so \`visit_identifier\` won't fire on it.
  - Recurses into the value expression normally so anything inside it (\`foo(arg=other_call(x=y))\` — the \`other_call\` and \`y\`) is still tracked.
- Wired \`keyword_argument\` into the binder dispatch in \`binder/binder.py\`.

Uses tree-sitter's \`child_by_field_name("name"|"value")\` rather than positional walking so it's robust to grammar changes around the \`=\` token.

## Tests

8 new in \`tests/test_binder_kwargs.py\`:
- Simple kwarg name not in references.
- \`@dataclass(frozen=True)\` doesn't flag \`frozen\` as unresolved.
- Multiple kwargs — all names dropped.
- Nested kwargs — both names dropped.
- Kwarg value identifier still resolves to a read.
- Kwarg value call is a call reference.
- Kwarg value attribute chain is visited.
- \`foo(x=x)\` — name dropped, value still produces exactly one READ ref to x.

Full suite: 342 passed.

## End-to-end

Re-indexing pypeeker's own source and running \`pypeeker check\`: \`no-unresolved-refs\` violations went from 313 to ~98. Zero remaining hits on kwarg names (\`frozen\`, \`qualified_name\`, \`method\`, \`line\` as a kwarg). The remaining noise is TASK-24 (forward references to module-level symbols) plus one separate comprehension-variable binding gap not covered by either task.
<!-- SECTION:FINAL_SUMMARY:END -->
