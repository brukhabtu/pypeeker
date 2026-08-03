---
id: TASK-146
title: 'skill: measure-tool-costs as a reusable slash command'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 16:55'
updated_date: '2026-08-02 17:18'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The tool-cost measurement script currently sits in .claude/workflows/ as a one-off. Package it as its own skill so it is invocable as a slash command and can be called by a future review skill without that skill owning it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A skill directory under .claude/skills/ holds SKILL.md and the measurement script
- [x] #2 The skill description triggers on requests to measure or analyse tool output token costs
- [x] #3 The script is removed from .claude/workflows/ and references to it are updated
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-wt146, parallel with the envelope arc.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Pipeline run wf_7f993051-518 (6 agents, lean). Scout verified the move surface exactly: only two path references existed (script docstring, TOKEN-COSTS.md run instruction) plus one implicit prose mention in Caveats.
- Implemented as git mv so the 100755 mode and rename detection survive.
- Orchestrator fix beyond task scope: DEFAULT_DIR hardcoded one project AND one session UUID, so the default would be wrong in every future session — bad for a script whose purpose is reuse. Replaced with discovery of the most recently active workflow-transcript dir; explicit arg still overrides. SKILL.md corrected to match.
- All three invocation paths verified (auto-discover, explicit, bad path). Shipped as PR #114.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Tool-cost measurement packaged as the measure-tool-costs skill; shipped as PR #114 (squash-merged).

- .claude/skills/measure-tool-costs/ holds SKILL.md and the script, following the audit-claude-md format; git mv preserved the exec bit.
- TOKEN-COSTS.md references updated.
- Default transcript directory is now discovered rather than hardcoded to one project and session UUID, so the skill works in any session.

Tests: full gate green standalone and combined; all three invocation paths exercised.
<!-- SECTION:FINAL_SUMMARY:END -->
