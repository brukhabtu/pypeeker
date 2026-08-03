---
id: TASK-150
title: >-
  cli/refs: unknown or malformed symbol IDs return empty with exit 0 instead of
  refusing
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 14:05'
updated_date: '2026-08-03 19:38'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-148 usage campaign. pypeeker refs pypeeker.totally.made.up:Nonexistent prints [] and exits 0 — byte-identical to a real symbol that genuinely has no references. The same happens for a well-formed ID in the wrong dialect: refs src/pypeeker/refactor/batch.py:DropReason (file-path form) returns [] while the canonical pypeeker.refactor.batch:DropReason returns 10. A caller cannot distinguish I asked wrong from the answer is zero, which matters most for the LLM-driven usage the CLI is designed for. Same family as the silent-failure bugs closed in TASK-136 through 141: the tool declines to refuse when it cannot answer. Check the other lookup commands (symbol, scope, tree) for the same shape.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 refs refuses with a structured error and non-zero exit when the symbol ID does not resolve to an indexed symbol
- [x] #2 A genuine zero-reference result remains distinguishable from an unresolved ID
- [x] #3 Sibling lookup commands are swept for the same behaviour and fixed or documented as safe
- [x] #4 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v4 arc with TASK-149 in worktree /home/user/pypeeker-wt149.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Same arc as TASK-149 (wf_b1d6d450-a39). refs, refs --all, tree, symbol now refuse with structured errors + candidates on unresolved ids; resolved-but-zero-refs stays [] exit 0. scope/purity/transactions verified already honest. No existing test pinned silent shapes — additions only. Shipped as PR #118.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Unresolved lookups are loud: structured error with candidates and non-zero exit across refs/refs --all/tree/symbol; genuine zero-reference results remain distinguishable. The file-path-dialect mistake self-repairs via candidates. Shipped as PR #118 (squash-merged); gate green.
<!-- SECTION:FINAL_SUMMARY:END -->
