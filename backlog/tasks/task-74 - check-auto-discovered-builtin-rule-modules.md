---
id: TASK-74
title: 'check: auto-discovered builtin rule modules'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:25'
updated_date: '2026-06-11 18:31'
labels:
  - check
  - m1-advisory
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
rules.py is a monolith; parallel rule development needs drop-in modules. Add src/pypeeker/check/builtin/ whose __init__ auto-imports submodules (pkgutil), each self-registering via the existing register_rule decorator. Engine imports the package once. Unblocks every M1 rule landing as its own file with zero shared-file edits.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A rule module dropped into check/builtin/ is discovered and runnable by name with no registry edits
- [x] #2 Existing REGISTRY built-ins and plugin path unchanged; name-clash precedence documented
- [x] #3 Unit test covers discovery; full suite green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read current registry/engine code
2. Add check/builtin package with pkgutil auto-import
3. Engine imports it before resolving rules
4. Discovery unit test
5. Full suite + self-lint
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
check/builtin auto-imports submodules via pkgutil; engine imports it lazily in run(). Precedence: legacy REGISTRY dicts > registered, last import wins among registered. Builtin modules must import concrete check modules (not pypeeker.check) to avoid an import cycle. Follow-up noted: binder does not resolve module dunder globals (__name__ et al) — worked around via self-import.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added check/builtin auto-discovery package so each builtin rule lands as a drop-in module with zero shared-file edits; engine imports the package before resolving rule names. End-to-end temp-package discovery test; suite and self-lint green.
<!-- SECTION:FINAL_SUMMARY:END -->
