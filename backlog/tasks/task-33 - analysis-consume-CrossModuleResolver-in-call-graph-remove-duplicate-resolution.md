---
id: TASK-33
title: >-
  analysis: consume CrossModuleResolver in call graph (remove duplicate
  resolution)
status: Done
assignee:
  - '@claude'
created_date: '2026-05-23 23:32'
updated_date: '2026-05-23 23:32'
labels:
  - analysis
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Audit follow-up (#1). analysis/graph.py had its own _resolve_import_target that reimplemented import->definition resolution for the call graph, weaker than the shared CrossModuleResolver (TASK-29): it string-matched imported_from against known function ids and did not follow __init__ barrel re-exports, so calls reached through a package barrel produced no edge. This consolidates onto the single resolver.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 call_graph resolves each call reference via CrossModuleResolver instead of the local _resolve_import_target; the duplicate helper and import_targets map are removed
- [x] #2 Calls routed through __init__ barrel re-exports now produce a correct call-graph edge to the canonical definition
- [x] #3 analysis->resolve is added to the import-boundaries allow-list (resolve is a low-level service below analysis); pypeeker check exits 0
- [x] #4 Existing call-graph and purity behavior is unchanged; a new test covers the barrel-routed edge; full suite green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Replace graph.py import resolution with CrossModuleResolver; load indexes once; resolve each CALL ref; drop _resolve_import_target+import_targets; allow analysis->resolve; add barrel edge test.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
graph.py now loads indexes once, builds a CrossModuleResolver, and sets callee = resolver.resolve_definition(ref.symbol_id) in the edge loop (subsuming the old import_targets map). _resolve_import_target removed. This picks up barrel-routed calls the string-matching version missed. Added analysis->resolve to the layering allow-list (resolve only depends on models; analysis sitting above it is sound). Updated graph.py docstring.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Consolidated cross-module call resolution onto the shared CrossModuleResolver. analysis/graph.py previously had a private _resolve_import_target that string-matched imported_from and ignored __init__ barrel re-exports; the call graph now resolves each call reference through the same resolver used by find_all_references and rename, so barrel-routed calls produce correct edges and there is one source of truth for import->definition.

Changes: call_graph loads indexes once, builds the resolver, and maps each CALL ref to its canonical definition; the duplicate helper and import_targets map are gone. analysis->resolve added to import-boundaries (resolve is a leaf below analysis). New test covers a barrel-routed call edge.

Tests: 404 pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
