---
id: TASK-128
title: >-
  Traits load-bearing: second rule/precondition pair (type-annotation) +
  confidence inventory
status: Done
assignee: []
created_date: '2026-07-31 22:25'
updated_date: '2026-07-31 22:58'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Plan C in roadmap-plans.md (normative, incl. sequencing adjustments). Make the trait abstraction load-bearing: a second provider (type-annotation) unifying prefer_tuple and InferredListBinding — the purity pair was evaluated and rejected for cause — plus a decided inventory of every scattered confidence computation with a promotion rule (cross-boundary AND anchor-shaped), and the Trait.provenance format convention. Behavior byte-identical everywhere; single PR; independent of the other roadmap plans.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All acceptance criteria in roadmap-plans.md Plan C are met, including: no existing test modified, the three-command gate green, the list-annotation predicate existing in exactly one src location, override tests proving both consumers route through the registry, and the documented inventory verdicts.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Stage 1 (sonnet, this session): oracle-first proof tests against unmodified src.
1. Add TestPreferTuple cases: explicit `a: list` annotation not flagged; non-list local not flagged.
2. Add TestTuplifyPlanner case: fresh-index VARIABLE bound to a non-list value raises TuplifyError(code=text-mismatch, exact refusal wording).
3. Add TestTuplifyRefusalVocabulary cases: same scenario through submit_intent (SubmitError.precondition/code/detail) and run_batch (DroppedIntent.precondition/detail).
4. Run full suite; must be green with zero src changes.

Stage 2 (sonnet, this session): add analysis/type_annotation.py (TYPE_ANNOTATION trait provider + is_inferred_list predicate), wire into analysis/__init__ barrel. No check/ or refactor/ changes yet — those are cutover, done by a later agent.

Gate: pytest + ruff + pypeeker self-lint after each stage.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Stage 1 done (proof tests, no src changes): added 2 tests to TestPreferTuple (explicit `a: list` DECLARED annotation not flagged; non-list local not flagged), 1 to TestTuplifyPlanner (non-list-bound fresh-index VARIABLE -> TuplifyError code=text-mismatch, exact refusal wording), 2 to TestTuplifyRefusalVocabulary (same scenario via submit_intent/SubmitError and run_batch/DroppedIntent). Full suite green at 1589 (1584 baseline + 5) with zero src changes -- confirms current behavior is the oracle.

Stage 2 done: added src/pypeeker/analysis/type_annotation.py -- TYPE_ANNOTATION trait provider `_type_annotation(file_index, symbol_id) -> Trait` (value=ann.raw or None, confidence=ann.confidence or UNKNOWN, never raises on missing symbol) and `is_inferred_list(trait)` predicate (the one place the two-clause form is written). Wired into analysis/__init__.py barrel (__all__ gains TYPE_ANNOTATION, is_inferred_list). Added 7 derivation tests to tests/test_traits.py::TestTypeAnnotationTrait (inferred list, explicit DECLARED annotation, non-list inferred constructor type, no annotation/UNKNOWN, unknown symbol id/UNKNOWN, provenance) plus a registry-registration test, mirroring TestVariableMutationTrait. check/ and refactor/ NOT touched -- consumer cutover is a later stage.

Gate green: pytest 1596 (1589 + 7), ruff clean, `pypeeker index src && pypeeker check` zero findings (same 11 pre-existing hidden low-confidence heuristics, none new -- unused-public-symbol did not flag is_inferred_list because it is barrel-re-exported, which the rule already exempts). git diff -- tests/ is additions-only (verified via grep). git add -A run; no commit made.

Stage 3 done (consumer cutover, byte-identical): check/rules.py prefer_tuple now obtains the type-annotation provider via get_trait_provider(TYPE_ANNOTATION) through the pypeeker.analysis barrel, guarded by the same assert idiom used for VARIABLE_MUTATION, and filters with is_inferred_list; the candidate loop was reordered so the cheap kind/scope_kind tests precede the trait call (both are pure filters, so the flagged set and its file_index.symbols order are unchanged). The Violation keeps its default Confidence.DECLARED -- the trait confidence is never propagated. refactor/preconditions.py InferredListBinding became __init__(name, symbol, index) (mirroring NotReassigned) reading the same trait; name="inferred-list-binding", slug="text-mismatch" and the refusal wording are untouched. Its one construction site refactor/literals.py:342 now passes index_fresh.index. Both local copies of the three-clause predicate are gone; the now-unused Confidence import was dropped from preconditions.py. Added 12 tests to tests/test_traits.py: TestPreferTupleAnnotationParity (baseline, negative override empties the finding set, positive override makes a non-list a candidate, DECLARED override is not a candidate, finding confidence stays DECLARED) and TestInferredListBindingParity (pass/fail wording, override flips both directions, frozen name/slug), each restoring the real provider in a finally.

Stage 4 done (provenance convention + inventory + docs): Trait.provenance docstring now states the three-part convention (provider module dotted path / facts read / anchor + file), what is deliberately NOT standardized (no type, parser, machine format, schema version), and the hard guardrail that provenance is never serialized into CLI JSON, a Violation.message, or a refusal reason. TestProvenanceConvention iterates the private traits._REGISTRY (public accessor avoided: its only consumer would be tests/, which the src-only unused-public-symbol gate would flag), selects providers whose __module__ is under pypeeker.analysis., asserts at least both proven pairs are present (no vacuous pass), and checks non-empty provenance opening with the provider module path, naming the anchor id, with non-empty evidence between; a second test asserts prefer_tuple findings leak no provenance. architecture.md: the Traits paragraph is retitled (TASK-127, TASK-128) with the two proven pairs enumerated, the "same rule twice" objection answered out loud, the signature-validation point, and the provenance convention; structural item 6 now carries the promotion rule (cross-boundary AND anchor-shaped), the rejected head-count alternative, the full six-row inventory table with a verdict per entry (TypeAnnotation.confidence migrated; _dynamic_access_confidence local/strongest future candidate; _impurity_confidence local; single-rule confidences local; import_confidence local; visibility_confidence dead with zero src readers), and the purity-pair rejection with all four reasons. The walls list is untouched.

Gate green after Stages 3-4: pytest 1608 passed (1596 + 12), ruff clean, pypeeker index src && check exit 0 with the same 11 pre-existing hidden heuristic findings and no new ones. git diff -- tests/ is additions-only (verified). git add -A run; no commit.

Orchestrator wrap-up: workflow ran clean — gate green first try (1608 pytest incl. 24 new tests: 5 oracle proofs written and verified passing BEFORE any src change, 7 derivation tests, 12 parity/override tests), 7 review findings, 0 must-fix. Applied two factual-doc advisories before merge (a false allow-list enforcement claim in type_annotation.py, a six-vs-seven binder-site count in the inventory). Additions-only test policy held: 330 inserted lines, zero deletions in tests/. Highlight: the cutover pinned a previously-untested invariant — prefer_tuple Violation confidence stays DECLARED and never inherits the trait confidence — and the DECLARED-list override test proves confidence (not raw text) is load-bearing in the predicate.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Make traits load-bearing (Plan C / roadmap): second provider + decided confidence inventory.

What changed:
- analysis/type_annotation.py: the type-annotation trait provider ((FileIndex, symbol_id) -> Trait; value = raw annotation text, confidence = the annotation own level, UNKNOWN-safe on missing symbols); is_inferred_list is now the single home of the raw==list AND INFERRED predicate (grep-verified unique).
- Both consumers cut over through the analysis barrel: check.rules.prefer_tuple quantifies (find all candidates), refactor.preconditions.InferredListBinding verifies pointwise on a fresh index before TuplifyPlanner writes — the first pair where the forall and the pointwise check guard the same remedy end-to-end.
- Oracle-first test policy executed as specified: 5 proof tests pinning current behavior passed against the untouched tree before any src edit; 12 parity/override tests prove both consumers genuinely route through the registry (provider swaps flip behavior both directions, DECLARED-list override proves confidence is load-bearing, Violation confidence pinned DECLARED).
- architecture.md: promotion rule recorded (cross-boundary AND anchor-shaped, with the rejected alternative and its reason), six-row confidence inventory with per-entry verdicts (incl. visibility_confidence recorded as dead: written at seven binder sites, read nowhere), provenance three-part prose convention with the never-serialize guardrail and a conformance test.

Review: 7 findings, 0 must-fix; two factual-doc advisories applied. Gate: 1608 pytest passed, ruff clean, self-lint exit 0; tests/ additions-only (330 insertions, 0 deletions).
<!-- SECTION:FINAL_SUMMARY:END -->
