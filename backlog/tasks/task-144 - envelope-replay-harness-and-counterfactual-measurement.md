---
id: TASK-144
title: 'envelope: replay harness and counterfactual measurement'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 16:55'
updated_date: '2026-08-03 13:23'
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
- [x] #1 A harness replays the fixture corpus through the envelope and reports before and after token totals per format family and overall
- [x] #2 Results include a sensitivity band showing net saving under stated drill-in rates, not just raw reduction
- [x] #3 Findings are recorded alongside TOKEN-COSTS.md so the baseline and the counterfactual sit together
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-envelope, bundled with TASK-142 and TASK-143; orchestrator re-runs the harness independently to verify the counterfactual.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Negative result, reported plainly. At committed scope the envelope saves 0.2-0.4 percent of tool-result cost; uv run pytest is a net loss at r=1.13. git is the real lever at r=0.17 (11-14 percent if wrapped alone). 37.2 percent of baseline flows through harness-native Read/Grep/Edit and is unreachable by a command wrapper at any adoption level. Harness v2 also corrected a v1 methodology error that projected largest-first corpus ratios onto the population and overstated every scope by ~3x.
<!-- SECTION:FINAL_SUMMARY:END -->
