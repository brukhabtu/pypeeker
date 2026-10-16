---
id: TASK-173
title: >-
  fix: no-argument-mutation tests its shared clauses before the ones that select
  its rows (the cost no-hidden-global-mutation had before #148)
status: To Do
assignee: []
created_date: '2026-10-16 13:31'
labels:
  - bug
  - check
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
no-argument-mutation's three reference parts are built on `_argument_base` (src/pypeeker/dsl/mutation_rules.py): `references().where(_in_a_function()).where(not_(allow_by_id(allow)))`, then each part's own clauses (receiver kind, attribute access, mutator call). So every reference row in the corpus runs the in-a-function test and an fnmatch against every allow pattern before the clauses that pick out the few rows the part is about.

no-hidden-global-mutation had the same shape (`_global_base`) until #148, which moved its selecting clauses first and the two shared clauses last (`_in_unallowed_function`). On bh-02's root corpus (224 files) that rule went from 43.6 s to 6.3 s with the same findings; fnmatch calls on the test corpus went from 3,288 to 168. no-argument-mutation was not changed then, because no bh-02 gate enables it, so its cost has not been measured; it is expected to be the same order of waste wherever it is enabled on a large project.

Why the order may change: every clause is a pure predicate and `where` joins them with AND, so the matched rows and the meet of their confidence are the same in any order; fork #3 ("written order, no optimizer", dsl-rewrite.md) binds the engine, not a rule author. The only observable difference is the order of a match's derivations under `query --why`. `_argument_base`'s docstring calls the order "frozen" from the port, so the fix says why it may now differ, as #148 did for the global rule.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 no-argument-mutation evaluates each part's selecting clauses before the in-a-function and allow clauses (or shares that base across its parts), and _argument_base's docstring says why the order may differ from the port's
- [ ] #2 Its findings are unchanged: a test pins them on each of the rule's shapes under several allow lists, and a count of allow-pattern tests (or the rows the base walks) pins the cheaper evaluation, as tests/test_dsl_rules_mutation.py does for no-hidden-global-mutation
- [ ] #3 Its time before and after on a large corpus is recorded in the notes
<!-- AC:END -->
