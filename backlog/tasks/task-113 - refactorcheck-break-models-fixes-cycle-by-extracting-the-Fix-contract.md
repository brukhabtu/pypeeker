---
id: TASK-113
title: 'refactor(check): break models<->fixes cycle by extracting the Fix contract'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-30 22:37'
updated_date: '2026-07-30 22:43'
labels:
  - refactor
  - check
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
check/models.py (Violation.fix: Fix) and check/fixes.py (with_fix, concrete fixes) import each other's types under TYPE_CHECKING — a real intra-package import cycle hidden by the guard. Extract the fix *contract* (Fix protocol, FixPlan, FixDeclined, DeclineReason) into a new leaf module check/protocols.py that both can depend on, inverting the dependency (DIP). This removes the cycle and the TYPE_CHECKING guards, and advances the framework/library split the architecture doc describes.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The Fix protocol, FixPlan, FixDeclined, and DeclineReason live in a new check/protocols.py that imports neither check.models nor check.fixes.
- [x] #2 check.models imports Fix from check.protocols at runtime (no TYPE_CHECKING guard); check.fixes imports the contract from check.protocols and Violation from check.models at runtime; no import cycle remains.
- [x] #3 The check barrel and all existing importers of Fix/FixPlan/FixDeclined/DeclineReason keep working (re-exported); full suite green, ruff clean, self-lint exits 0.
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Broke the hidden check.models <-> check.fixes import cycle by extracting the fix contract (Fix protocol, FixPlan, FixDeclined, DeclineReason) into a new leaf module check/protocols.py that imports neither. Dependency Inversion: models -> protocols, fixes -> models + protocols, all one-way. Both TYPE_CHECKING guards became plain runtime imports (the cycle would surface as a circular import if it remained; verified it does not). Barrel and all importers unchanged (fixes re-exports the contract). Advances the framework/library split. Suite green (1390), ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
