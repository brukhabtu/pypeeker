---
id: TASK-80
title: 'check: unused-return-value rule (+ binder call-result-used fact)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 18:50'
labels:
  - check
  - binder
  - m1-advisory
dependencies:
  - TASK-74
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
A function with a declared non-None return type whose result is discarded at every call site is a procedure pretending to be a function, or every caller is buggy. Needs one new binder fact: whether a CALL reference is a bare expression statement. Scope conservatively to declared non-None returns.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Binder records result_used (or equivalent) on CALL references — false when the call is a bare expression statement
- [ ] #2 Project rule flags FUNCTION/METHOD symbols with a declared non-None return annotation whose every project call site discards the result
- [ ] #3 Index serialization forward-compatible; tests cover used/discarded/mixed and None-returning exclusion; opt-in
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add result_used: bool = True to Reference (models/references.py) with docstring; default keeps old indexes deserializing via from_dict defaults
2. Add _call_result_discarded(call_node) helper in binder/references.py: parent is expression_statement, or parent is await whose parent is expression_statement; wire into visit_call (bare identifier) and visit_attribute_call (method calls, call node = attr_node.parent)
3. New builtin project rule src/pypeeker/check/builtin/unused_return_value.py: @register_rule("unused-return-value", scope="project"); candidates = FUNCTION/METHOD with declared non-None return annotation, skip dunders; one pass resolving project CALL refs via ctx.resolver; flag when >=1 call and all result_used=False; allow option (fnmatch); opt-in, pyproject untouched
4. tests/test_rule_unused_return_value.py: binder fact tests (bare/method/await discarded; assignment/return/argument/comparison used), serialization round-trip, rule tests (flagged/mixed/None-returning/unannotated/zero-call/allow/registration/opt-in)
5. uv run pytest -q; dogfood on /tmp copy of repo
<!-- SECTION:PLAN:END -->
