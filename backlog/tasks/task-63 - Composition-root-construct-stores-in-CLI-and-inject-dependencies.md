---
id: TASK-63
title: 'Composition root: construct stores in CLI and inject dependencies'
status: To Do
assignee: []
created_date: '2026-06-11 15:46'
labels:
  - query
  - analysis
  - cli
dependencies:
  - TASK-62
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The CLI group is supposed to be the composition root, but SemanticQueryEngine.get_tree constructs its own TreeStore from store.project_root, AnalysisContext.for_function constructs a fresh SemanticQueryEngine per call, and refactor/cst.py + applier construct PythonAdapter directly. Components constructing their own dependencies hides cost and makes cache invalidation unreasonable. Rule: stores/engines are constructed at the composition root and passed down.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 SemanticQueryEngine no longer constructs TreeStore internally (injected or provided by CLI)
- [ ] #2 AnalysisContext.for_function accepts an injected engine/store rather than self-assembling a new engine per call
- [ ] #3 CLI builds all stores once in the group callback and passes them down; no layer constructs a store it could receive
- [ ] #4 Full test suite passes
<!-- AC:END -->
