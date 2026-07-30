---
id: TASK-117
title: 'check: run the full rule suite against pypeeker via committed baseline'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-30 23:32'
updated_date: '2026-07-30 23:33'
labels:
  - check
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The self-lint (CI + Claude pre-commit hook) ran only 12 of pypeeker's 22 builtin rules. Enable all 22 and run them via 'pypeeker check --baseline' so every rule is dogfooded and only NEW violations block. 15 rules are zero-finding hard gates; 7 fire on deliberate design patterns and are baseline-gated with a documented per-rule rationale. Commit the baseline (.pypeeker/check-baseline.json) as the tracked ratchet-of-record.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 All 22 builtin rules are enabled in [tool.pypeeker].rules; check --baseline passes on the current tree (0 new) and fails on any new violation.
- [x] #2 The baseline file is committed (un-ignored in .gitignore) and the Claude pre-commit hook + CI self-lint both run check --baseline.
- [x] #3 Every baseline-gated rule has a documented good reason (architecture.md Self-lint rule adoption) for being grandfathered rather than code-fixed; tests asserting rules were opt-in are updated to assert they are enabled.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Inventory all 22 rules vs the 12 enabled; measure findings of the 10 unenabled against pypeeker.
2. Enable all 22; seed + commit the baseline; un-ignore check-baseline.json.
3. Point the hook + CI + CLAUDE.md at check --baseline.
4. Document per-rule rationale (hard-gate vs baseline-gated) in architecture.md.
5. Flip the 7 opt-in tests to assert enabled; verify teeth (new violation fails).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
22 builtin rules exist; only 12 were enabled. Enabled all 22 via check --baseline. 15 are zero-finding hard gates (nothing in the baseline, so any hit is new); 7 fire on deliberate patterns and are baseline-gated: no-argument-mutation (binder mutable-accumulator + Click ctx.obj), under-exposed-access (package-internal _helpers shared across sibling modules), over-exposed-export (curated barrels), prefer-tuple (advisory; some JSON-output lists), unused-return-value (convenience returns), no-hidden-global-mutation (register_rule registry side-effect), born-private (stateful ratchet). Rationale table added to architecture.md. Baseline = 226 findings, committed as the one tracked file under .pypeeker/. Teeth verified: clean tree exit 0 (0 new); injected public undocumented fn -> exit 1 (new require-docstrings + unused-public-symbol). 7 opt-in tests flipped to assert-enabled. Gate: 1398 pytest passed, ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Run the full builtin rule suite against pypeeker itself, via the committed baseline ratchet.

Problem: the self-lint (CI + Claude pre-commit hook) exercised only 12 of 22 builtin rules. The other 10 were opt-in because they fire on the codebase.

Change: enable all 22 rules and switch the gate to `pypeeker check --baseline`. 15 rules are zero-finding hard gates; the other 7 fire on deliberate design patterns and are baseline-gated (226 pre-existing findings grandfathered, new regressions blocked). The baseline (.pypeeker/check-baseline.json) is committed as the one tracked file under .pypeeker/ via a gitignore negation; the Claude hook, CI, and CLAUDE.md all run check --baseline.

Good-reason requirement: architecture.md gains a Self-lint rule adoption section with a per-rule table explaining why each baseline-gated rule reflects an intentional pattern (binder mutable accumulator, package-internal protected helpers, curated barrels, decorator registry, advisory style, convenience returns) rather than a defect to fix. A new finding that does not match one of those reasons is a real regression, not a baseline candidate.

Tests: seven tests that asserted these rules were opt-in now assert they are enabled. Teeth verified (new violation exits 1). 1398 pytest passed, ruff clean, check --baseline exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
