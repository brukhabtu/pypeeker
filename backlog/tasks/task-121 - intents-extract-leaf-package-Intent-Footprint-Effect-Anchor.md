---
id: TASK-121
title: 'intents: extract leaf package (Intent/Footprint/Effect + Anchor)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 04:53'
labels:
  - refactor
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 1 of the four-noun target architecture (architecture.md). Move refactor/intents.py and refactor/footprint.py into a new intents/ leaf package importable by both check and refactor; add the Anchor union (SymbolAnchor | RangeAnchor | EdgeAnchor); curated barrel; update import-boundaries. Behavior-preserving.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 src/pypeeker/intents/ exists with intents.py, footprint.py, anchors.py and a curated __all__ barrel; refactor/intents.py and refactor/footprint.py are gone.
- [ ] #2 All importers (refactor, app, tests) updated; cross-package imports go through the intents barrel (barrel-only clean).
- [ ] #3 import-boundaries: intents declared as a near-leaf (models/query/storage); refactor allowed to import intents; check unchanged (gains intents in phase 4).
- [ ] #4 Behavior-preserving: full gate green (pytest, ruff, pypeeker index+check); no Anchor yet — an unconsumed export would trip the unused-public-symbol gate, so Anchor lands in phase 4 with its consumer.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create src/pypeeker/intents/ (intents.py, footprint.py moved; anchors.py new; curated barrel).
2. Update every importer to go through the intents barrel; fix intra-package imports.
3. pyproject import-boundaries: declare intents; allow refactor->intents (and app->intents).
4. Anchor union + tests; run full gate.
5. Adversarial multi-lens review (opus) on the diff; apply confirmed findings; re-gate.
<!-- SECTION:PLAN:END -->
