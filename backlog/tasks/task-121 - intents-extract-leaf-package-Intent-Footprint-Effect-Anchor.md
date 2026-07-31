---
id: TASK-121
title: 'intents: extract leaf package (Intent/Footprint/Effect + Anchor)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 14:34'
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
- [x] #1 All importers (refactor, app, tests) updated; cross-package imports go through the intents barrel (barrel-only clean).
- [x] #2 import-boundaries: intents declared as a near-leaf (models/query/storage); refactor allowed to import intents; check unchanged (gains intents in phase 4).
- [x] #3 Behavior-preserving: full gate green (pytest, ruff, pypeeker index+check); no Anchor yet — an unconsumed export would trip the unused-public-symbol gate, so Anchor lands in phase 4 with its consumer.
- [x] #4 src/pypeeker/intents/ exists with intents.py, footprint.py and a curated __all__ barrel (17 names, the union of both modules); refactor/intents.py and refactor/footprint.py are gone (git mv, history preserved).
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create src/pypeeker/intents/ (intents.py, footprint.py moved; anchors.py new; curated barrel).
2. Update every importer to go through the intents barrel; fix intra-package imports.
3. pyproject import-boundaries: declare intents; allow refactor->intents (and app->intents).
4. Anchor union + tests; run full gate.
5. Adversarial multi-lens review (opus) on the diff; apply confirmed findings; re-gate.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented via the phase1-intents-leaf-v2 workflow (sonnet implementer, haiku gates, 3 opus adversarial review lenses, per the no-fable directive). First launch aborted on a transient tool-permission failure (implementer blocked, zero work); a probe confirmed recovery and the hardened relaunch (tool-failure escape hatches + BLOCKED early-abort + gate existence check) ran clean: gate green first try, 5 review findings, 0 must-fix.

Orchestrator applied 3 advisory findings before commit: barrel docstring tense (check does not import intents yet), stale layering xrefs in privatize.py/demotion.py now point at import-boundaries config, architecture.md Module Layering table gained the intents row. Also pruned 4 stale worktrees left by an earlier aborted workflow. Independently re-verified: 1444 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Extract the shared change vocabulary into a new near-leaf package pypeeker.intents (phase 1 of the four-noun target architecture).

What changed:
- refactor/intents.py -> intents/intents.py and refactor/footprint.py -> intents/footprint.py (git mv), with a curated barrel re-exporting the union of both __all__ lists (17 names).
- refactor/__init__ no longer re-exports intent names (no cross-package laundering); batch.py, privatize.py, app/batch_intents.py and three test files import from the pypeeker.intents barrel; docstring xrefs updated.
- import-boundaries: intents = [models, query, storage]; refactor and app gained intents; check unchanged (gains it in phase 4 with Violation.remedy).
- Anchor union deferred to phase 4: an export with no src consumer would trip the unused-public-symbol gate.

Behavior-preserving: 1444 pytest passed, ruff clean, pypeeker check exit 0. Adversarially reviewed (3 opus lenses): 0 must-fix findings; 3 doc-drift advisories applied before merge.
<!-- SECTION:FINAL_SUMMARY:END -->
