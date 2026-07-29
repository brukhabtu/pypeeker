---
id: TASK-111
title: 'fix: unused-imports false-positives on forward-ref and side-effect imports'
status: To Do
assignee: []
created_date: '2026-07-29 17:36'
labels:
  - bug
  - check
  - binder
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The unused-imports rule flags imports as dead when they are (a) referenced only inside string/forward-ref annotations — e.g. 'node: "Node | None"' with 'from tree_sitter import Node' under TYPE_CHECKING — because the binder does not record references that appear inside string annotations; and (b) imported purely for a side effect — e.g. 'import pypeeker.check.builtin' in engine.py, which drives rule self-registration and is referenced by no name. ruff's F401 correctly treats both as used. Auto-removing them (which RemoveUnusedImportFix would do) silently breaks forward-ref annotations and rule discovery. Root cause of (a) is in the binder; (b) needs a convention (e.g. treat bare 'import pkg.sub' side-effect imports as used, or a documented marker).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Imports referenced only inside forward-ref string annotations are not flagged unused (binder records those references, or the rule accounts for them).
- [ ] #2 Side-effect-only module imports (e.g. engine.py's 'import pypeeker.check.builtin') are not flagged/auto-removed.
- [ ] #3 Regression tests cover both the forward-ref-annotation and side-effect-import cases.
<!-- AC:END -->
