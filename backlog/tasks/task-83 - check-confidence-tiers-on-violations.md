---
id: TASK-83
title: 'check: confidence tiers on violations'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:26'
updated_date: '2026-06-11 19:36'
labels:
  - check
  - m2-fixes
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Linters are binary; pypeeker can label every finding with how it was resolved (declared/direct vs inferred/heuristic), the antidote to false-positive fatigue. Violation gains a confidence field; rules set it; CLI default hides low-confidence findings, --strict shows all; fix application later gates on it.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Violation carries a confidence tier; existing rules label their findings (most are certain; receiver/inference-derived ones are not)
- [x] #2 check CLI: default omits low-confidence violations, --strict includes them, output marks the tier
- [x] #3 Sorting/format remains deterministic; tests cover tier filtering
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. models.py: add confidence field (reuse models.capabilities.Confidence, default DECLARED, compare=False); __str__ gains trailing " [tier]" marker for non-DECLARED only
2. rules.py: remove _DYNAMIC_ACCESS_SUFFIX; unused-public-symbol sets confidence=HEURISTIC for dynamic-access modules; add shared _impurity_confidence helper (HEURISTIC when every observation rests on an UNKNOWN receiver); apply in no-impure-functions
3. builtin/visibility.py + test_only_production_code.py: suffix -> confidence=HEURISTIC; pure_decorator_contracts.py uses _impurity_confidence; import_time_side_effects.py: unresolved bare-name matches -> HEURISTIC
4. cli.py check: --strict flag; default hides HEURISTIC/UNKNOWN with "N low-confidence hidden" note; baseline storage/delta computed on the FULL set regardless of --strict (documented in help)
5. tests/test_confidence.py: field default, eq/order regression, str marker, rule labeling, CLI default/strict, baseline invariance; update TASK-95 suffix assertions in test_visibility_config.py
6. uv run pytest -q; ruff; self-lint (index + check)
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added Violation.confidence (reuses models.capabilities.Confidence, default DECLARED, compare=False) and a trailing " [tier]" __str__ marker for non-DECLARED tiers only; eq/order/hash byte-identical to before
- Migrated the TASK-95 message suffix: _DYNAMIC_ACCESS_SUFFIX removed; unused-public-symbol, over-exposed-module-symbol, over-exposed-export, test-only-production-code now set confidence=HEURISTIC via the new _dynamic_access_confidence helper
- Purity-derived rules: new _impurity_confidence helper labels verdicts HEURISTIC when every observation rests on an UNKNOWN receiver; applied in no-impure-functions, pure-decorator-contracts, and import-time-side-effects (which also labels unresolved bare-name policy matches HEURISTIC vs builtin DECLARED)
- BareCall observations cannot distinguish builtin vs unresolved-name origin inside no-impure-functions/pure-decorator-contracts (the observation carries only the name), so those stay DECLARED — noted in the helper docstring rather than contorting the analysis layer
- CLI check: --strict flag; default hides HEURISTIC/UNKNOWN with "N low-confidence violation(s) hidden (use --strict to show)" note; DECLARED+INFERRED always show; baseline storage and delta always use the FULL set (only the display of new violations honors the filter), documented in --help
- baseline.py untouched: identity uses the raw message, and the removed suffix only shrinks identities; no interaction broke
- Tests: new tests/test_confidence.py (24 tests); TestDynamicAccessProximity in test_visibility_config.py rewritten to assert the structured field (new run_rule_violations fixture)
- uv run pytest -q: 1013 passed; ruff clean; self-lint (pypeeker index src && pypeeker check) exits 0, --strict also clean
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added structured confidence tiers to check violations, superseding the TASK-95 low-confidence message suffix.

Changes:
- Violation (check/models.py) gained confidence: Confidence = DECLARED (reuses pypeeker.models.capabilities.Confidence; compare=False so equality/ordering/hashing are unchanged). __str__ appends a trailing " [tier]" marker only for non-DECLARED tiers, so output for certain findings is byte-identical.
- Removed _DYNAMIC_ACCESS_SUFFIX and its message decoration; the dynamic-access proximity heuristic now sets confidence=HEURISTIC via a shared _dynamic_access_confidence helper in unused-public-symbol, over-exposed-module-symbol, over-exposed-export, and test-only-production-code.
- New _impurity_confidence helper: purity verdicts resting solely on UNKNOWN-receiver observations are HEURISTIC; applied in no-impure-functions, pure-decorator-contracts, and import-time-side-effects. import-time-side-effects additionally labels unresolved bare-name policy matches HEURISTIC (builtin-resolved and import-rooted matches stay DECLARED).
- check CLI: new --strict flag. Default omits HEURISTIC/UNKNOWN findings from output and exit code, printing "N low-confidence violation(s) hidden (use --strict to show)"; DECLARED/INFERRED always show. --update-baseline records and --baseline compares the FULL violation set regardless of --strict (only the display of new violations is filtered), documented in the command help.

User impact: heuristic findings no longer fail default check runs or pollute output, but remain visible via --strict and are marked with their tier; baselines never churn with the filter.

Tests: tests/test_confidence.py (field semantics, str marker, rule labeling, CLI default/strict, baseline invariance); test_visibility_config.py dynamic-access tests migrated to the structured field. Full suite 1013 passed, ruff clean, self-lint (index + check, and check --strict) exits 0.

Known imprecision (documented in code): BareCall observations do not record builtin vs unresolved-name origin, so no-impure-functions / pure-decorator-contracts cannot cheaply downgrade unresolved bare-call matches and leave them DECLARED.
<!-- SECTION:FINAL_SUMMARY:END -->
