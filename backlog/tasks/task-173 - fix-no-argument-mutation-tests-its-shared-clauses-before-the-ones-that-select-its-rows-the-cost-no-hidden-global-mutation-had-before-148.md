---
id: TASK-173
title: >-
  fix: no-argument-mutation tests its shared clauses before the ones that select
  its rows (the cost no-hidden-global-mutation had before #148)
status: In Progress
assignee:
  - '@claude'
created_date: '2026-10-16 13:31'
updated_date: '2026-10-16 22:19'
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
- [x] #1 no-argument-mutation evaluates each part's selecting clauses before the in-a-function and allow clauses (or shares that base across its parts), and _argument_base's docstring says why the order may differ from the port's
- [x] #2 Its findings are unchanged: a test pins them on each of the rule's shapes under several allow lists, and a count of allow-pattern tests (or the rows the base walks) pins the cheaper evaluation, as tests/test_dsl_rules_mutation.py does for no-hidden-global-mutation
- [x] #3 Its time before and after on a large corpus is recorded in the notes
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Tests first (tests/test_dsl_rules_mutation.py), run on the unchanged rule to see the structural and count tests fail and the findings test pass:
   - the three argument parts close with the in-a-function and allow-by-id clauses (replaces the "opens with" test);
   - findings of the three shapes (plus a method and the self exclusion) under several allow lists, incl. a module-only pattern that must not silence the argument rule;
   - count of fnmatchcase calls per part on a small corpus vs the same plus 40 noise modules must not move.
2. mutation_rules.py: replace _argument_base(options) with a helper applied after each part's selecting clauses (as _in_unallowed_function does for the global rule), and rewrite its docstring: why the order may differ from the frozen port (pure clauses, AND, fork #3 binds the engine, meet order-independent, only query --why sees derivation order).
3. Rerun the tests: all pass. Check the module/test docstrings that say the argument parts open with these clauses.
4. Time the rule in-process on bh-02 root corpus, pinned pypeeker vs this branch, interleaved median of 3 (shared machine), with no-argument-mutation enabled in a scratch copy of bh-02's root pyproject; also compare findings. Record in notes.
5. scripts/verify-repo.sh once. Risks: the count test must really fail on the old order; findings must be identical on bh-02 corpus before/after.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
What changed
- src/pypeeker/dsl/mutation_rules.py: `_argument_base(options)` (a base every part started from) is replaced by `_in_unallowed_function_by_id(selection, options)`, which each of the three argument parts applies after its own selecting clauses, as `_in_unallowed_function` does for the global rule since #148. Renamed because a "base" applied last would be a false name; `by_id` says what differs from the global helper (the allow list tests the function id only, one fnmatch per pattern). Its docstring says why the order may differ from the frozen port: the order was a cost order and never a meaning; fork #3 binds the engine, not a rule author; every clause is a pure predicate joined by AND, so rows and the meet of their confidence (fork #4) are the same; only the order of a match's derivations moves, which only `query --why` prints for a named library expression, and none of this rule's selections is one. Not shared through the corpus memo: the three parts would still pay the allow tests on every reference once.
- tests/test_dsl_rules_mutation.py: (a) the "argument parts open with" structural test is now "close with" (last two where-clauses are the in-a-function and allow-by-id clauses, and the part has a selecting clause of its own); (b) `test_each_argument_shape_is_reported_once_and_silenced_only_by_its_own_allow_pattern`: 10 allow lists over a module with the three shapes, a method (quoted as "method", `self` never reported) and a function of locals, including a bare module path (silences nothing: the argument rule's allow is by id only), `shapes:*`, `*`, and a pattern that matches nothing; (c) `test_the_argument_allow_patterns_are_tested_only_against_rows_the_part_could_report`: counts `fnmatchcase` calls per part over the shapes module and over it plus 40 noise modules; the count must be above zero and must not move. Module docstring updated.

Seen failing on the old order (tests written first, run on the unchanged rule): 6 failed, 48 passed. The 3 structural tests failed; the 3 count tests failed with `assert 1671 == 111` for each part (the count grows with the noise modules). The 10-case findings test passed on the old order and passes on the new one, which is its job: same rows, cheaper evaluation. After the change: 54 passed in tests/test_dsl_rules_mutation.py; counts are 6 / 3 / 3 fnmatch calls for the mutator-call / attribute-write / subscript-write parts, the same small and large (were 111 small, 1,671 large, for each).

Time on bh-02's root corpus (criterion 3)
Method, as TASK-0111 and the global rule's #148 measurement did it: in-process, one rule over the 224-file root corpus (226 index entries, refreshed beforehand, 24 findings), every index loaded outside the timer, `no-argument-mutation` enabled in a scratch copy of bh-02's root pyproject.toml (never committed; the bh-02 tree was untouched, git status clean), before = pypeeker 8e42118 (identical src to the pinned 67b0a1b: `diff -r` against /home/user/bh-02/.venv's pypeeker is empty) from a `git archive`, after = this branch, same interpreter (Python 3.15.0b4), each run its own process, interleaved before/after, 3 pairs, median. THE MACHINE WAS SHARED: 4 cores, two other workers running, load average 1.0 to 1.7, so absolute seconds are noisy; before and after were run back to back.

| config | before (s) | after (s) | median before | median after |
|---|---|---|---|---|
| as the root pyproject has it (no allow list) | 16.48, 16.59, 16.71 | 5.82, 6.16, 6.25 | 16.59 | 6.16 |
| with a 3-pattern allow list (the global rule's, as bh-02 configures that rule) | 27.81, 27.37, 27.02 | 5.72, 5.51, 6.04 | 27.37 | 5.72 |

A warm-up pair before these gave 16.10 and 5.89 (no allow list). With the 3-pattern list, fnmatchcase calls over the corpus went from 616,437 to 72 (a separate counting run). Findings: 24 in every run, the full sorted finding text byte-identical before and after in all 7 pairs (sha 68695f0993ab). The remaining ~6 s is the walk of every reference row and its cheap clauses (the global rule's remainder after #148 is the same, 6.3 s); the rule is gated nowhere in bh-02, so no gate gets faster from this, but anywhere it is enabled on a project this size it goes from 16 to 6 s with no allow list and from 27 to 6 s with three patterns.

Proof run: scripts/verify-repo.sh, once at the end: pytest 3891 passed, 11 skipped; ruff passed; self-lint passed (OVERALL: PASS).
<!-- SECTION:NOTES:END -->
