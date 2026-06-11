---
id: TASK-99
title: 'check: born-private ratchet (new symbols must justify visibility)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 20:53'
labels:
  - check
  - visibility
  - m6-ratchets
dependencies:
  - TASK-81
  - TASK-95
  - TASK-98
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Enforce 'private until needed' prospectively: flag newly-public symbols (vs baseline) whose observed usage scope does not justify public visibility — without relitigating legacy code.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags public symbols absent from the baseline whose references are all module-local (per visibility-detection scope computation)
- [x] #2 Respects library-mode public roots and decorator allowlists; opt-in
- [x] #3 Tests cover new-over-exposed flagged, new-justified-public passing, legacy untouched
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add a "symbols" namespace to check/baseline.py (additive): has_symbol_baseline / load_symbol_baseline / write_symbol_baseline, preserving the violations namespace via the existing read-merge-write pattern.
2. New project-scoped opt-in rule born-private in check/builtin/born_private.py: collect current public module-level function/class ids with the same exemptions as over-exposed-module-symbol (reusing its helpers + check.rules visibility helpers); self-seed the symbol baseline silently on first run; afterwards flag symbols absent from the baseline whose observed references are all module-local; HEURISTIC confidence near dynamic access; never auto-extend the baseline after seeding (wiring --update-baseline is a CLI follow-up).
3. tests/test_rule_born_private.py: seeding silence, second-run flagging, cross-module-justified pass, legacy untouched, namespace round-trip preservation, exemptions (decorator/barrel/library mode), opt-in + registration, heuristic confidence.
4. uv run pytest -q + ruff clean.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added a "symbols" namespace to check/baseline.py (additive): has_symbol_baseline / load_symbol_baseline / write_symbol_baseline, each preserving the sibling "violations" namespace via the same read-merge-write pattern; module docstring documents the auto-seeded namespace.
- has_symbol_baseline exists to distinguish "never seeded" from "seeded when the project had zero public symbols" (an empty recorded list must not trigger a second silent seed).
- New opt-in project rule born-private (check/builtin/born_private.py): reuses the usage-scope core and option helpers from check/builtin/visibility.py and check.rules (visibility options, decorator allowlists, library-mode public roots, dynamic-access HEURISTIC); the small barrel-exported computation is replicated locally with a comment because it is inline in over-exposed-module-symbol and visibility.py is owned by another work stream this wave.
- Seeding semantics shipped: first run with no "symbols" namespace records ALL current eligible public ids and returns [] (silent); that is the only baseline write the rule performs — later runs never auto-extend.
- FOLLOW-UP (cli.py off-limits this wave): wire the symbol namespace into `check --update-baseline` (one-liner: call write_symbol_baseline alongside write_baseline) so accepted newly-public symbols can be re-recorded; documented in both module docstrings.
- Tests: tests/test_rule_born_private.py (20 tests) — seeding silence + namespace contents, seeded-empty distinction, no rewrite on later runs, new module-local symbol flagged (exact message/line/confidence), cross-module-justified pass, legacy over-exposed untouched, HEURISTIC near getattr, exemptions (decorator, barrel, library-mode public root, main/dunder/__main__.py, allow), storage round-trip preserving both namespaces, registration + opt-in (pyproject anchored to __file__).
- uv run pytest -q: 1098 passed; ruff: clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added the born-private ratchet: an opt-in, project-scoped check rule enforcing "private until needed" prospectively — newly public module-level symbols must justify their visibility with a cross-module reference, while everything public at adoption time is grandfathered.

Changes:
- check/baseline.py (additive): new "symbols" namespace in .semantic-tool/check-baseline.json with has_symbol_baseline / load_symbol_baseline / write_symbol_baseline; each namespace writer preserves the other, so violation baselines and the symbol record share one file safely. has_symbol_baseline distinguishes "never seeded" from "seeded empty".
- check/builtin/born_private.py (new): self-registers via @register_rule(scope="project"). First run auto-seeds the symbol baseline with every current public function/class id and reports nothing (silent adoption); later runs flag public symbols absent from the record whose observed references are all module-local, with message "newly public 'X' is only used within its module — make it _X or record it (`check --update-baseline`)". Reuses the over-exposed-module-symbol usage-scope core and exemptions (dunders/main, __main__.py, barrel exports, allow / allow-decorators, library-mode public roots via the injected visibility options); findings near getattr/globals/vars/locals carry confidence=HEURISTIC. The rule never auto-extends the baseline after seeding.
- tests/test_rule_born_private.py (new, 20 tests): seeding semantics, ratchet flag/pass/legacy cases, all exemptions, heuristic labeling, namespace round-trip preservation, registration + opt-in.

Tests: uv run pytest -q — 1098 passed; ruff clean.

Follow-up: wiring the symbol namespace into `check --update-baseline` is a one-line cli.py change deliberately deferred (cli.py owned by a parallel work stream this wave); until then, accepting a flagged symbol means a cross-module use, an underscore, or an allow pattern.
<!-- SECTION:FINAL_SUMMARY:END -->
