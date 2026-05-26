---
id: TASK-55
title: 'Binder: fix flaky reference recording from id(node) reuse'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-26 14:09'
updated_date: '2026-05-26 14:10'
labels:
  - binder
  - bug
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The binder tracks already-handled declaration nodes in state.declaration_nodes keyed by Python id(node). tree-sitter Node wrappers are ephemeral (re-created per access and garbage-collected), so id() values get reused: a later identifier wrapper can collide with a freed declaration node id and be wrongly skipped as a declaration. This nondeterministically drops references - e.g. the read in "return e" (a bare name whose declaration wrapper was GC-d) is sometimes not recorded, which corrupts find_references / purity / data-flow. Fix: key declaration_nodes on the stable (start_byte, end_byte) span instead of id(node).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 state.declaration_nodes is keyed by a stable node identity (start_byte, end_byte); all add/lookup sites use it
- [x] #2 Bare "return <local>" and similar record the read deterministically; a regression test covers it
- [x] #3 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
helpers.node_key(node)->(start_byte,end_byte); state.declaration_nodes: set[tuple[int,int]]; replace all id(X) in declaration_nodes add/lookups across scopes/imports/references/assignments/binder with node_key(X). Regression test: return <local> records read. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause: state.declaration_nodes keyed on id(node); tree-sitter Node wrappers are ephemeral and id() is reused across GC, so a later identifier wrapper could collide with a freed declaration node id and be wrongly skipped - nondeterministically dropping references (e.g. bare "return e" recorded no read depending on memory reuse). Fix: added helpers.node_key(node)=(start_byte,end_byte) (stable per node) and keyed declaration_nodes on it everywhere (state type set[tuple[int,int]]; ~20 add/lookup sites across scopes/imports/references/assignments/binder). Discovered while building extract-method (outputs detection needs return reads). 481 + regression tests pass; pypeeker check exits 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fix flaky reference recording caused by id(node) reuse. The binder remembered already-handled declaration nodes by Python id(node), but tree-sitter wrappers are ephemeral and ids are reused as they are GC-d, so identifier reads could be nondeterministically skipped (notably the read in a bare "return <local>"). Keyed declaration_nodes on a stable (start_byte, end_byte) span (helpers.node_key) instead. This corrects find_references / purity / data-flow for affected names. Regression test added; full suite + pypeeker check green.
<!-- SECTION:FINAL_SUMMARY:END -->
