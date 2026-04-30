---
id: TASK-6
title: Fix incorrect/weak assertions in purity & fact tests
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three concrete defects in the purity test suite that allow regressions to slip through.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 tests/test_analysis_facts.py::test_finds_print_call: replace circular 'line=facts[0].line' self-comparison with a hardcoded expected line number
- [ ] #2 tests/test_analysis_facts.py::test_local_variable_ids_excludes_parameters: replace substring matching ('x' in sid, 'a' in sid.split(...)) with exact symbol_id equality checks
- [ ] #3 tests/test_analysis_facts.py::test_finds_self_attribute_write: replace startswith/endswith pair with exact equality 'facts[0].target == "<unresolved>.value"'
- [ ] #4 All 33 purity-related tests still pass after the fixes
<!-- AC:END -->
