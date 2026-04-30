---
id: TASK-10
title: Strengthen evidence-shape assertions across purity tests
status: To Do
assignee: []
created_date: '2026-04-30 03:59'
updated_date: '2026-04-30 04:03'
labels: []
dependencies:
  - TASK-6
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Most existing tests only check verdict + evidence-kind membership. This lets several classes of regression slip through silently: doubled evidence, wrong line numbers, wrong confidence, accidental false-positive evidence on pure cases.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Every test asserting PROBABLY_PURE also asserts result.evidence == [] (currently only one test does)
- [ ] #2 Every test asserting IMPURE also asserts an exact evidence count (e.g., 'assert len(result.evidence) == 1' or '== 3')
- [ ] #3 Every impure-call test asserts the line number of the evidence (not just kind+target)
- [ ] #4 Add at least one test that asserts result.confidence == Confidence.HEURISTIC (currently no test reads this field)
- [ ] #5 Add a 'multiple effects in one function' test: function with print + os.system + global write -> assert evidence list contains exactly 3 items, with correct kinds, lines, and targets, in source order
<!-- AC:END -->
