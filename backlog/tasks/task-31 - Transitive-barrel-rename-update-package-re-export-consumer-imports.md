---
id: TASK-31
title: 'Transitive barrel rename: update package re-export consumer imports'
status: To Do
assignee: []
created_date: '2026-05-23 22:50'
labels:
  - refactor
  - index
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Follow-up discovered while implementing TASK-30. When a definition is re-exported through a package __init__ (barrel) and a consumer imports it via the package (from pkg import X) rather than the defining module (from pkg.lib import X), rename does not update the consumer's import statement: find_import_symbols matches only the direct module path (imported_from pkg.lib.X), so the barrel consumer's import (imported_from pkg.X) is missed. TASK-30 keeps such modules internally consistent by also leaving their call sites alone, but the consumer is still left importing the old name from the barrel. Closing this requires resolver-based import discovery (find all IMPORT symbols whose resolve_definition == the canonical def, following re-export chains) and feeding those import ids into the rename binding set so both the import and its call sites cascade. DESIGN FORK to resolve first: updating a barrel consumer's 'from pkg import X' to the new name is only valid if pkg actually re-exports the new name, i.e. the __init__ re-export was also updated (today gated behind --include-exports). Decide whether transitive barrel-consumer import updates should be gated on --include-exports (treated as part of the re-export chain) or always applied, and how to detect that a given import resolved through a re-export vs directly.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 find_importers/resolver-based discovery returns all IMPORT symbols that resolve to the canonical definition, including those routed through __init__ barrels
- [ ] #2 Renaming a barrel-re-exported definition (with the agreed --include-exports semantics) updates barrel-consumer import statements AND their call sites, leaving consumer modules valid and runnable
- [ ] #3 The --include-exports gating decision for transitive barrel consumers is implemented and documented; direct-import and existing __init__ behavior is unchanged
- [ ] #4 Tests cover a barrel-consumer end-to-end rename producing runnable code; full suite green; pypeeker check exits 0
<!-- AC:END -->
