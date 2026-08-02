---
id: TASK-144
title: 'envelope: replay harness and counterfactual measurement'
status: To Do
assignee: []
created_date: '2026-08-02 16:55'
labels: []
dependencies:
  - TASK-142
  - TASK-143
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The acceptance criterion for the whole envelope effort. Replays the fixture corpus through the envelope and reports what it would have saved against the measured 2026-08-02 baseline of 6403k approximate tokens. Raw reduction overstates the benefit because a truncated envelope sometimes forces a follow-up drill-in call, so the harness must report a sensitivity band rather than a single headline number.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A harness replays the fixture corpus through the envelope and reports before and after token totals per format family and overall
- [ ] #2 Results include a sensitivity band showing net saving under stated drill-in rates, not just raw reduction
- [ ] #3 Findings are recorded alongside TOKEN-COSTS.md so the baseline and the counterfactual sit together
<!-- AC:END -->
