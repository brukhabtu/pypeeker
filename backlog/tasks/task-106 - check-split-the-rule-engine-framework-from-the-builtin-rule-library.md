---
id: TASK-106
title: 'check: split the rule-engine framework from the builtin rule library'
status: To Do
assignee: []
created_date: '2026-07-03 01:57'
labels:
  - architecture
  - roadmap
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The check package holds both the generic rule-running framework (engine, context, config, registries, baseline) and the concrete pypeeker rule library (builtin/*, rules.py). They are ~30% of the codebase together. If a second consumer of the engine ever appears, the framework should be extractable without dragging the Python-specific rules along. Roadmap item: only worth doing when a second consumer lands; captured so the coupling is tracked.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Framework vs rule-library boundary is documented in architecture.md, identifying exactly which modules are generic engine vs pypeeker-specific rules
- [ ] #2 No circular coupling from the framework modules back into the concrete rule library (verify with the import graph)
<!-- AC:END -->
