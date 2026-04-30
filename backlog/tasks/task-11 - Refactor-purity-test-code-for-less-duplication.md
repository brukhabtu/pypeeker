---
id: TASK-11
title: Refactor purity test code for less duplication
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-04-30 04:03'
labels: []
dependencies:
  - TASK-7
  - TASK-8
  - TASK-9
  - TASK-10
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Test code quality follow-up after the main coverage gaps are filled. Lower priority than the correctness/coverage tasks but worth doing once those land.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Lift the _ctx() helper from test_analysis_facts.py into tests/conftest.py as a reusable fixture (e.g., 'analysis_context')
- [ ] #2 Parametrize the impure-builtin-call series (print, open, input, exec, eval) into a single pytest.mark.parametrize test
- [ ] #3 Parametrize the impure-stdlib-call series (os.system, time.time, random.random) similarly
- [ ] #4 Remove duplicate coverage between test_function_only_reading_params_is_pure and test_pure_method_is_pure (consolidate or delete the redundant one)
- [ ] #5 Test files renamed for clarity: test_purity.py -> test_check_purity.py; test_analysis_facts.py -> test_facts.py (optional, only if it improves discoverability)
<!-- AC:END -->
