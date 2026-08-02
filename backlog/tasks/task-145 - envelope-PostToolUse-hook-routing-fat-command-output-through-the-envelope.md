---
id: TASK-145
title: 'envelope: PostToolUse hook routing fat command output through the envelope'
status: To Do
assignee: []
created_date: '2026-08-02 16:55'
labels: []
dependencies:
  - TASK-143
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Generalises the envelope to commands we do not own. A PostToolUse hook returns hookSpecificOutput.updatedToolOutput to replace a tool result with an envelope, which is what makes sed and git diff output tractable: those two families alone account for 2342k approximate tokens of the measured baseline. The hook rewrites output for every matching call, so blast radius is the dominant design concern: it must engage only above the configured threshold, only for registered command families, fail open on any internal error by passing the original output through untouched, and honour a kill switch.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A PostToolUse hook replaces qualifying tool output with an envelope via hookSpecificOutput.updatedToolOutput
- [ ] #2 The hook reads its threshold, command registry, and kill switch from the same YAML config the library uses
- [ ] #3 Any hook error passes the original tool output through unchanged rather than failing the call
- [ ] #4 Output below the threshold or from unregistered commands is passed through untouched
- [ ] #5 Hook behaviour is covered by tests driven from the fixture corpus
<!-- AC:END -->
