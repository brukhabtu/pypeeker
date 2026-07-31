---
id: TASK-127
title: 'analysis: traits foundation (value+confidence+provenance)'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 04:51'
updated_date: '2026-07-31 19:31'
labels:
  - analysis
  - architecture
dependencies:
  - TASK-124
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 7: introduce Trait carrying value+confidence+provenance, registered per primitive kind; migrate one rule/precondition pair (e.g. prefer-tuple's not-mutated/escapes predicate shared with NotReassigned) as proof; scattered *_confidence fields migrate over time.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Trait type + registry exist with tests; one rule and one precondition consume the same trait; full gate green.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. analysis/traits.py: Trait dataclass (value, confidence, provenance) + register_trait/get_trait registry mirroring register_rule/register_planner. analysis is importable by both check and refactor — the natural home per the four-noun table.
2. Proof migration: one shared variable-mutation/escape trait consumed by BOTH the prefer-tuple rule (quantify: find all) and the NotReassigned precondition (verify: check one) with byte-identical findings and refusal wording.
3. Tests: trait registry semantics + parity proofs both directions.
4. Docs: fold aspirational item 6; trim the aspirational section to the honest remainder (walls + gradual confidence-field migration).
5. Workflow: sonnet implement, haiku gate, 3 opus lenses, fixer; orchestrator verifies + merges.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented Trait foundation (analysis/traits.py: Trait(value, confidence, provenance) + register_trait/get_trait_provider, last-wins registration mirroring register_rule/register_planner) plus the proof provider analysis/variable_mutation.py (VARIABLE_MUTATION trait: has_write_ref/mutator_call/escaping_read, DECLARED confidence), extracted verbatim from prefer_tuple's old inline unsafe-set logic. Both exported through the analysis barrel.

Migrated check.rules.prefer_tuple (quantifies: unsafe = is_mutated or escaping_read over candidates) and refactor.preconditions.NotReassigned (verifies pointwise: has_write_ref alone, not the full mutation union) onto the same trait via get_trait_provider(VARIABLE_MUTATION). Kept has_write_ref and mutator_call as two separate booleans rather than one is_mutated field precomputed in the trait, because NotReassigned wants has_write_ref alone (a .append() call must not fail "not-reassigned", matching prior behavior) while prefer_tuple wants their union - documented as the one-vs-two-values judgment call in VariableMutation's docstring.

Added tests/test_traits.py (17 tests): registry semantics (register/lookup/last-wins/miss), trait derivation on clean/write/mutator-call/escaping fixtures with confidence+provenance checks, and two-quantifier parity proofs (prefer_tuple's exact finding set on a mixed fixture, NotReassigned's exact refusal wording, plus swap-the-registered-provider tests proving both consumers genuinely go through the registry rather than a private copy).

Docs: added a Traits paragraph to architecture.md's Layer 2 section (mechanism + the prefer_tuple/NotReassigned worked example); folded aspirational item 6 into current-state text with Done/Landed annotations across the four-noun section (structural changes list + migration order), rewrote the section banner from "aspirational" to "mostly landed - remaining lifts below" (walls list + gradual per-field confidence migration). Verified via cli.py that structural item 3 ("everything is a batch") is in fact landed (single-op commands already route through app.submit); left item 1 honestly short one detail (EdgeAnchor not yet added). CLAUDE.md: added one-line Traits convention bullet.

Gate: uv run pytest -q -> 1584 passed (was 1567; +17 new). uv run ruff check src tests -> clean. uv run pypeeker index src && uv run pypeeker check -> 0 violations (only the same 11 pre-existing --strict-only heuristic findings as before, confirmed via before/after --strict diff - no new findings introduced). All existing oracle tests (test_check_rules.py::TestPreferTuple, test_inline_variable.py::test_inline_reassigned_refused, test_preconditions.py::TestNotReassigned) pass unmodified.

Orchestrator wrap-up: review round 13 findings, 3 must-fix (all doc-accuracy: register_trait parity overclaim vs register_rule builtin-registry gap, four-noun table Trait row, stale intents-boundary claims in architecture.md/CLAUDE.md). The one-vs-two-values judgment call was made honestly: has_write_ref and mutator_call stay separate because NotReassigned means reassignment (write refs only) while prefer_tuple means mutation (their union) — folding them would have silently changed inline-variable refusals. Provider-swap tests prove both consumers genuinely route through the registry. Independently verified: single copy of the mutation/escape analysis (grep), 1584 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Traits foundation (phase 7, final phase of the four-noun migration): Trait(value, confidence, provenance) with a provider registry, proven by one real rule/precondition unification.

What changed:
- analysis/traits.py: frozen Trait dataclass + register_trait/get_trait_provider mirroring the established registration idiom (with its one honest divergence documented: no separate builtin registry, so plugins can override builtin providers).
- analysis/variable_mutation.py: the variable-mutation trait provider, extracted verbatim from prefer_tuple inline scan — one copy of the WRITE/escape/mutator analysis in the codebase.
- Both quantifiers migrated with byte-identical behavior: prefer_tuple quantifies (is_mutated or escaping_read) over candidates; NotReassigned verifies has_write_ref pointwise. Values kept separate where the consumers genuinely differ (append() mutates but does not reassign).
- 17 new tests: registry semantics, derivation fixtures with confidence/provenance, exact-finding-set and exact-wording parity proofs, and provider-swap tests proving real registry routing.
- Docs: aspirational item 6 folded; the target-architecture section banner now reads mostly-landed with only the honest remainder (EdgeAnchor + file birth/death wall, single-pass scheduling, mirror->overlay, gradual confidence-field migration).

Gate: 1584 pytest passed, ruff clean, self-lint exit 0. All seven phases of the migration are now complete.
<!-- SECTION:FINAL_SUMMARY:END -->
