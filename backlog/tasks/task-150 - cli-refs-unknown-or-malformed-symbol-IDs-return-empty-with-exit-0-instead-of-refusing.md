---
id: TASK-150
title: >-
  cli/refs: unknown or malformed symbol IDs return empty with exit 0 instead of
  refusing
status: To Do
assignee: []
created_date: '2026-08-03 14:05'
updated_date: '2026-08-03 18:12'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-148 usage campaign. pypeeker refs pypeeker.totally.made.up:Nonexistent prints [] and exits 0 — byte-identical to a real symbol that genuinely has no references. The same happens for a well-formed ID in the wrong dialect: refs src/pypeeker/refactor/batch.py:DropReason (file-path form) returns [] while the canonical pypeeker.refactor.batch:DropReason returns 10. A caller cannot distinguish I asked wrong from the answer is zero, which matters most for the LLM-driven usage the CLI is designed for. Same family as the silent-failure bugs closed in TASK-136 through 141: the tool declines to refuse when it cannot answer. Check the other lookup commands (symbol, scope, tree) for the same shape.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 refs refuses with a structured error and non-zero exit when the symbol ID does not resolve to an indexed symbol
- [ ] #2 A genuine zero-reference result remains distinguishable from an unresolved ID
- [ ] #3 Sibling lookup commands are swept for the same behaviour and fixed or documented as safe
- [ ] #4 Full gate green
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
DSL rewrite program (dsl-rewrite.md): phase 2's evidence-typed anchor resolution makes loud unresolved lookups structural. Still valid as a standalone near-term fix (query/cli paths are NOT frozen); closes at the flip if not before.
<!-- SECTION:NOTES:END -->
