---
id: TASK-6
title: Fix incorrect/weak assertions in purity & fact tests
status: Done
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-05-02 00:19'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Three concrete defects in the purity test suite that allow regressions to slip through.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 tests/test_analysis_facts.py::test_finds_print_call: replace circular 'line=facts[0].line' self-comparison with a hardcoded expected line number
- [x] #2 tests/test_analysis_facts.py::test_local_variable_ids_excludes_parameters: replace substring matching ('x' in sid, 'a' in sid.split(...)) with exact symbol_id equality checks
- [x] #3 tests/test_analysis_facts.py::test_finds_self_attribute_write: replace startswith/endswith pair with exact equality 'facts[0].target == "<unresolved>.value"'
- [x] #4 All 33 purity-related tests still pass after the fixes
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed three concrete defects in tests/test_analysis_facts.py: (1) test_finds_print_call replaced circular self-comparison with hardcoded line=1; (2) test_local_variable_ids_excludes_parameters now uses exact symbol_id equality ('mod.py:f:x' in ctx.local_variable_ids) instead of substring matching, and verifies the parameter is in local_symbol_ids but not local_variable_ids; (3) test_finds_self_attribute_write replaced startswith/endswith pair with exact equality target == '<unresolved>.value'. Full suite still passes.
<!-- SECTION:FINAL_SUMMARY:END -->
