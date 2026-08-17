---
id: TASK-168
title: >-
  dsl phase 3f: port the impurity pair (pure-decorator-contracts,
  import-time-side-effects)
status: Done
assignee:
  - '@claude'
created_date: '2026-08-09 02:33'
updated_date: '2026-08-17 05:32'
labels:
  - dsl
dependencies:
  - TASK-158
  - TASK-167
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Both drive analysis.impurities through the same tier as the already-ported IMPURITY sweep (dsl/sweeps.py). pure-decorator-contracts flags impure @cache/@property/dunders; import-time-side-effects needs receiver_chain as well (check/builtin/import_time_side_effects.py:204) so this depends on phase 3e's universe extension. Port both at parity with fixture targets; ledger discipline as always.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Both rules claimed at parity
- [x] #2 Full gate green
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ported the impurity pair (pure-decorator-contracts, import-time-side-effects) to the DSL engine at byte parity — the manifest now claims ALL 22 of 22 rules, over 8 targets. The rule-library port (phase 3) is complete; the flip (TASK-157) is unblocked.

Changes:
- New DSL capability: fact_of(...).about(expr) reads a fact about an entity a row names (an import-time call site is a reference; impurity is a fact about the function it resolves to) — the one substrate gap the port surfaced, extended deliberately and ledgered.
- References universe grew two more fields (import-time scope flag, module scope id), 23 -> 25.
- New dsl/impurity.py family: pure-decorator-contracts as a DslRule, import-time-side-effects as a 3-part MultiPartRule reproducing the frozen fall-through partition (builtin arm -> qualified denylist -> impure project function), incl. both confidence tiers on the builtin arm via an any_of partition rather than a sixth Weaken (the dynamic-access weakening inventory stays closed at five, ledgered).
- New tests/fixtures/parity/impurity corpus — the only target grading either rule (both are in the gated self-lint set, so self is 0-vs-0 by construction): 11 findings each, covering all three call shapes, both tiers, decorator-beats-dunder, +N-more truncation, DEFAULT_ALLOW precedence (mutation-verified), and the qualified-arm fall-through.
- Five ledger entries; no [[divergence]] — byte parity reached. The refusal test now uses an invented id since no unported builtin remains (unknown and unported are literally the same RULES.get miss).
- Post-lens hand-fixes: about() now applies the fork-#9 expression guard (cycle-safe inline of _require_expression); a None-anchor fact read outside a selection now refuses loudly instead of silently answering; corrected the false make_handle-purity rationale in manifest+fixture to the measured mechanism; added the missed unused-return-value impurity count. The pipeline fixer separately made the DEFAULT_ALLOW fixture line non-vacuous with a mutation-verified oracle failure.

Gate: verify-repo.sh PASS on all four steps (3618 pytest, ruff, baseline-free self-lint, differential oracle over 8 targets x 22 rules), run independently after all fixes.
<!-- SECTION:FINAL_SUMMARY:END -->
