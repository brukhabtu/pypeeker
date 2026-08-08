---
id: TASK-164
title: 'architecture.md: document the two homes for derived facts (traits vs facts)'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-08-08 20:01'
labels:
  - docs
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Post-DSL, derived facts have two homes: the per-file trait registry ((FileIndex, symbol_id) -> Trait, the trait-promotion rule) and the corpus-wide fact tier (dsl/facts.py + sweeps, per fork #11). The split is principled but undocumented as doctrine; add a paragraph to architecture.md telling the next contributor which home a new derived fact belongs in, keyed to the promotion rule's clause (b).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 architecture.md states the decision rule for trait vs fact placement
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the post-DSL doctrine paragraph to architecture.md beside the trait promotion rule: clause (b) is now the decision rule between the two homes — anchor-shaped facts go in the trait registry, corpus-shaped facts are hand-written primitives in the fact tier (dsl/facts.py + sweeps, fork #11), neither emulated in the other; _dynamic_access_confidence cited as the worked example of a (b)-failing candidate resolving into the fact tier.
<!-- SECTION:FINAL_SUMMARY:END -->
