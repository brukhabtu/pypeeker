---
id: TASK-145
title: 'envelope: PostToolUse hook routing fat command output through the envelope'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 16:55'
updated_date: '2026-08-04 00:53'
labels: []
dependencies:
  - TASK-143
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Generalises the envelope to commands we do not own, via a PostToolUse hook returning hookSpecificOutput.updatedToolOutput. SCOPE REPOINTED BY TASK-144's counterfactual: target git first and pytest never. Measured population ratios say git reduces at r=0.17 on 1188k of baseline (wrapping git alone saves 11-14 percent of total tool-result cost, roughly 30x the entire pypeeker-command scope), while uv run pytest is a NET LOSS at r=1.13 -- the envelope exceeds the output it replaces across two thirds of that family's tokens, and a third of grep/rg is net-loss too. The envelope only pays above roughly 4 KB, so the allowlist must be evidence-driven rather than broad. Blast radius is the dominant design concern: the hook rewrites output for every matching call.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A PostToolUse hook replaces qualifying tool output with an envelope via hookSpecificOutput.updatedToolOutput
- [x] #2 The hook reads its threshold, command registry, and kill switch from the same YAML config the library uses
- [x] #3 Any hook error passes the original tool output through unchanged rather than failing the call
- [x] #4 Output below the threshold or from unregistered commands is passed through untouched
- [x] #5 Hook behaviour is covered by tests driven from the fixture corpus
- [x] #6 The hook allowlist targets git and explicitly excludes pytest and any other family measured as net-loss
- [ ] #7 After adoption, a measure-tool-costs re-run reports the before/after against the 6403k baseline
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 in /home/user/pypeeker-wt145, parallel with TASK-152 (disjoint paths). Lands before the phase-3 fan-out so those runs adopt the hook and produce its before/after naturally.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Built by pipeline run wf_7132dd93-ced (13/15 agents; final gate passed, 2706 tests, four-step gate PASS).
- Shipped INERT: hook script, envelope.yaml (git-only allowlist, 4KB threshold, pytest excluded), envl config kill switch, 50-test suite committed; the .claude/settings.json PostToolUse wiring was deliberately withheld. Two safety-classifier blocks flagged wiring an output-rewriting hook into the agent harness during an unsupervised window; activation is a one-line settings.json edit reserved for explicit user go.
- AC #1 (hook replaces output via updatedToolOutput) left unchecked pending activation; AC #7 (measure re-run after adoption) blocked on the same.
- Removed duplicate ACs 7-11 (verbatim copies of 1-5).
<!-- SECTION:NOTES:END -->
