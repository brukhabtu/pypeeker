---
id: TASK-169
title: 'import-boundaries: nested package units for src/<pkg>/ layouts'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-22 18:21'
updated_date: '2026-08-22 18:50'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Consumers on src/<pkg>/ layouts report that import-boundaries cannot police a layer below the first segment under `root`. `_package_under` returns exactly one segment, so a boundary unit is always a single package: a dotted key such as "domain.orders" in [tool.pypeeker.import-boundaries.allow] matches no unit and is SILENTLY inert, while the bare parent is still reported undeclared under strict. The silence is the dangerous half - pypeeker accepts the config and reports nothing, so the project believes a boundary is enforced when it is not.

The rule lives in the frozen oracle path (check/rules.py), so the capability lands in the new dsl/ engine and the silence is closed outside the frozen tree.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A dotted unit key in allow/unconstrained/dependency lists is rejected with an actionable error by the shipping CLI path instead of being silently ignored
- [x] #2 The dsl engine resolves a module to the longest DECLARED unit prefix under root, falling back to the first segment when none is declared
- [x] #3 Single-segment configs produce byte-identical findings in the dsl engine (differential parity preserved, no divergence entry needed)
- [x] #4 Nested units are policed in both directions: as importer and as dependency
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. dsl/sweeps.py: derive a unit vocabulary from the allow table + unconstrained list; resolve a module to its longest DECLARED prefix under root, falling back to the first segment (byte-identical for single-segment configs).
2. Thread the vocabulary through the origin resolution so a nested unit is policed as dependency as well as importer.
3. app/boundary_config.py (non-frozen): detect dotted unit names the frozen engine cannot honor; cli.py raises an actionable UsageError instead of silently ignoring them.
4. Ledger note in dsl-rewrite.md recording the capability gap and why no divergence entry is needed.
5. Tests: dsl nested-unit policing + parity of single-segment configs + the CLI guard.
6. scripts/verify-repo.sh
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Root cause: `_package_under` returns `rest[0]` — a unit is always one segment beneath `root`. Dotted keys matched nothing and were silently inert.
- Capability landed in `dsl/sweeps.py` (`_unit_under`, longest declared prefix; `_unit_vocabulary` over all three config positions). `_package_under` retained for `barrel-only`, which has its own root and no allow-table.
- Silence closed outside the frozen tree in `app/boundary_config.py`, raised as a UsageError from `cli.py`.
- Parity confirmed: `import-boundaries` 13-vs-13 on the boundaries target, 0-vs-0 elsewhere; `barrel-only` unchanged. No divergence entry needed.
- Consumer-reported `ui.widgets`/`ui.app` case pinned as a regression test.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Adds nested package units to `import-boundaries`, so a `src/<pkg>/` project can police a layer below the first segment under `root`.

**Problem.** A boundary unit was one segment beneath `root` (`check.rules._package_under`), so `ui/widgets/bar.py` reported as `ui` and a boundary like "ui.widgets must not import ui.app" was inexpressible. Worse, a dotted key was not rejected — it matched no unit, charged no import, and the run came back clean, so a project could believe a boundary was enforced when nothing was. Verified in the wild against 0.1.0, where the constraint was worked around by moving the rule out to ast-grep.

**Changes.**
- `dsl/sweeps.py`: `_unit_under` resolves a module to the longest *declared* prefix of its path beneath `root`, falling back to the first segment. `_unit_vocabulary` collects unit names from all three positions a table names one in (allow key, dependency value, unconstrained entry), so a nested unit is enforceable as dependency as well as importer. `_beneath_root` factors the shared root test.
- `barrel-only` keeps `_package_under`: different rule, own `root`, no allow-table — flat is its whole notion of a package, not a limitation.
- `app/boundary_config.py` (new, outside the frozen tree): refuses a dotted unit name with a message naming every offender and what it prevented. Wired into `cli.py check` as a UsageError. Deleted at the phase-5 flip with the engine it guards.
- Ledger entry in `dsl-rewrite.md`.

**Why no divergence entry.** The fallback makes the nested resolver equal to the flat one whenever no dotted name is declared, and no oracle target declares one. Differential grades identical on all five targets (`import-boundaries` 13-vs-13 on `boundaries`).

**Tests.** 14 nested-unit tests including the reported `ui.widgets` case and a parametrized reduction-to-flat proof; 11 guard tests. `scripts/verify-repo.sh`: pytest, ruff, self-lint, differential all PASS.

**Follow-up.** Consumers on the old engine get the refusal, not the capability, until the flip.
<!-- SECTION:FINAL_SUMMARY:END -->
