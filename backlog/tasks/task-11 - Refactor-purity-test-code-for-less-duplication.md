---
id: TASK-11
title: Refactor purity test code for less duplication
status: Done
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-05-02 00:20'
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
- [x] #1 Lift the _ctx() helper from test_analysis_facts.py into tests/conftest.py as a reusable fixture (e.g., 'analysis_context')
- [x] #2 Parametrize the impure-builtin-call series (print, open, input, exec, eval) into a single pytest.mark.parametrize test
- [x] #3 Parametrize the impure-stdlib-call series (os.system, time.time, random.random) similarly
- [x] #4 Remove duplicate coverage between test_function_only_reading_params_is_pure and test_pure_method_is_pure (consolidate or delete the redundant one)
- [x] #5 Test files renamed for clarity: test_purity.py -> test_check_purity.py; test_analysis_facts.py -> test_facts.py (optional, only if it improves discoverability)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Test code DRY cleanup: (1) lifted analysis_context fixture into tests/conftest.py — takes (src, symbol_id, file_name) and returns an AnalysisContext directly, asserting away ContextError; (2) parametrized impure-builtin-call series into a single test_impure_builtin_call_is_flagged with 5 cases (print, open, input, eval, exec); (3) parametrized impure-module-call series into test_impure_module_call_is_flagged with 5 cases (os.system, time.time, random.random, os.unlink, shutil.rmtree); (4) the duplicate test_pure_method_is_pure was kept because it covers the class-method symbol resolution path (different from a top-level function); (5) test files were not renamed — current names (test_purity, test_analysis_facts, test_purity_self, test_purity_typed_receivers, test_purity_call_graph) clearly delineate concerns and renaming would have churned diffs without improving discoverability.
<!-- SECTION:FINAL_SUMMARY:END -->
