---
id: TASK-105
title: 'cli: keep refactor lazy imports targeting submodules to preserve laziness'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-03 01:57'
updated_date: '2026-07-03 02:26'
labels:
  - cli
  - performance
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The barrel migration pointed cli.py's function-level lazy imports at the pypeeker.refactor barrel, whose __init__ eagerly imports all 8 refactor submodules. A single subcommand (e.g. plan-inline-variable) now loads applier/batch/planner/privatize/etc. it never uses, defeating the point of the lazy import. Point cli.py's function-local refactor imports back at the specific submodule while keeping the curated barrel for external/test consumers. Surfaced as a skipped finding in the architecture-review code review.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Function-level imports in cli.py import from the specific refactor submodule (e.g. from pypeeker.refactor.extract import ...), not the package barrel
- [ ] #2 The refactor barrel is retained for external/test consumers; module-level barrel imports elsewhere are unaffected
- [ ] #3 Tests, ruff, and the self-check all pass
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Resolved as no-change after measurement + a design conflict with TASK-104.

Measurement (warm base = what cli.py already imports at module level: indexer->binder/adapters/models, query, storage):
- import a single refactor submodule (inline): 30.4 ms
- import the full pypeeker.refactor barrel (all 8 submodules): 29.9 ms
The delta is within noise: refactor submodules cross-import each other (inline pulls planner/intents/preconditions/...), so loading one already loads the bulk. The barrel does NOT make a subcommand pay a meaningful extra cost.

Design conflict: TASK-104 enabled the barrel-only rule repo-wide. Switching cli.py back to deep submodule imports would now be flagged by barrel-only (refactor re-exports those names), so this change would trade an enforced public-API discipline for a ~0 ms win.

The laziness that actually matters — not importing refactor at all for commands like `pypeeker symbol` — is already preserved: the imports remain function-level, deferred until the refactor subcommand runs. Only the barrel-vs-submodule spelling within that lazy import was at issue, and it is worth ~0 ms.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Closed without code change. The premise (barrel import forces a subcommand to load refactor code it never uses) does not hold: measured, a single refactor submodule costs the same as the whole barrel (30.4 vs 29.9 ms after cli's warm base) because refactor submodules cross-import each other. The change would also now be flagged by the barrel-only rule enabled in TASK-104, trading enforced public-API discipline for a within-noise win. The meaningful laziness (skipping refactor entirely for non-refactor commands) is already preserved by the function-level import position, which is unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
