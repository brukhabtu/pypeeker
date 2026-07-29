---
id: TASK-111
title: 'fix: unused-imports false-positives on forward-ref and side-effect imports'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-29 17:36'
updated_date: '2026-07-29 18:42'
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
- [x] #1 Imports referenced only inside forward-ref string annotations are not flagged unused (binder records those references, or the rule accounts for them).
- [x] #2 Side-effect-only module imports (e.g. engine.py's 'import pypeeker.check.builtin') are not flagged/auto-removed.
- [x] #3 Regression tests cover both the forward-ref-annotation and side-effect-import cases.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Probe: x: Node records a ref, x: "Node" records none (binder skips strings); import a.b.c has a dotted symbol name uses do not bind to.
2. Fix in the rule (AC permits): skip dotted-name imports; skip imports whose name appears in a quoted annotation substring.
3. Regression tests + dogfood the 4 previously-false-flagged files.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Chose the rule-level fix over binder surgery: parsing string annotations with correct file offsets + resolution-fixup interaction is heavy/risky on the tool's most critical component, and AC1 explicitly allows the rule to account for forward refs. A full binder forward-ref-annotation feature (also helps no-unresolved-refs) is a reasonable separate follow-up.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
unused-imports had two false-positive classes that RemoveUnusedImportFix would have acted on destructively: forward-ref string annotations (x: "Node | None" — binder does not descend into strings) and side-effect dotted imports (import pypeeker.check.builtin). The rule now skips dotted-name imports and any import whose name appears inside a quoted annotation substring (whole-string and nested list["Foo"]). Three regression tests; dogfood confirms engine.py/extract.py/inline.py/simulate.py are clean. Fixed at the rule level (AC-permitted) to avoid risky binder changes; a binder forward-ref feature remains a possible follow-up.
<!-- SECTION:FINAL_SUMMARY:END -->
