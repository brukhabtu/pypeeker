---
id: TASK-146
title: 'skill: measure-tool-costs as a reusable slash command'
status: To Do
assignee: []
created_date: '2026-08-02 16:55'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The tool-cost measurement script currently sits in .claude/workflows/ as a one-off. Package it as its own skill so it is invocable as a slash command and can be called by a future review skill without that skill owning it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A skill directory under .claude/skills/ holds SKILL.md and the measurement script
- [ ] #2 The skill description triggers on requests to measure or analyse tool output token costs
- [ ] #3 The script is removed from .claude/workflows/ and references to it are updated
<!-- AC:END -->
