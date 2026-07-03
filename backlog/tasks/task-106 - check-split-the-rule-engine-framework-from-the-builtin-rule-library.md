---
id: TASK-106
title: 'check: split the rule-engine framework from the builtin rule library'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 01:57'
updated_date: '2026-07-03 02:29'
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
- [x] #1 Framework vs rule-library boundary is documented in architecture.md, identifying exactly which modules are generic engine vs pypeeker-specific rules
- [x] #2 No circular coupling from the framework modules back into the concrete rule library (verify with the import graph)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Documented the framework-vs-rule-library boundary inside the check package in architecture.md, and verified the coupling contract from the import graph.

Findings:
- Framework (rule-agnostic): engine, context, config, models, baseline, the registry in rules.py (Rule/ProjectRule, register_rule, REGISTRY/PROJECT_REGISTRY, get_rule/get_project_rule), and the Fix protocol in fixes.py (Fix, FixPlan, with_fix).
- Rule library (Python-specific): builtin/*, demotion.py, the six concrete rule functions in rules.py, and the concrete Fix subclasses in fixes.py.
- Coupling is one-directional (library -> framework). The discovery seam is a deliberate side-effect import: engine.py does 'import pypeeker.check.builtin' at run time for self-registration and takes no static dependency on any concrete rule. No concrete rule imports the engine, so the framework is acyclic wrt the library.
- The split is logical, not yet physical: rules.py co-locates the registry with six concrete rules (dragging in analysis/query/resolve/project) and fixes.py co-locates the Fix protocol with its subclasses. Extracting the registry and fix protocol into framework-only modules is the work a second engine consumer would trigger.

No code change (roadmap/analysis task). Gate unchanged: 1391 passed, ruff clean, self-check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
