---
id: TASK-95
title: 'check/config: library mode, public roots, dynamic-access allowlists'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 19:20'
labels:
  - check
  - config
  - visibility
  - m5-visibility
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Visibility and dead-code rules are dangerous for libraries: external consumers are invisible. Add project-level config: mode (app|library), public API roots (exports under these barrels are sacred), decorator allowlists (symbols carrying them count as externally called), and dynamic-access heuristics (getattr/globals proximity downgrades confidence).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Config parsed from [tool.pypeeker] via the project module; library mode exempts configured public roots from demotion/dead-code findings
- [x] #2 Decorator allowlist suppresses unused/over-exposed findings for decorated symbols; dynamic-access proximity downgrades finding confidence
- [x] #3 unused-public-symbol, test-only, and visibility rules consume these; tests per behavior
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add VisibilityConfig frozen dataclass + load_visibility_config to project.py (owns [tool.pypeeker.visibility] parsing)
2. CheckConfig gains visibility field; load_config injects parsed section into every rule options dict under reserved key "visibility"
3. Consume in unused-public-symbol, over-exposed-module-symbol, over-exposed-export, test-only-production-code: library-mode public-roots exemption via existing barrel detection; merged global allow-decorators; decorator exemption added to unused-public-symbol and test-only
4. Dynamic-access proximity: builtin getattr/globals/vars/locals refs in defining module append low-confidence suffix to messages
5. Tests per behavior + regression defaults; ruff + pytest green
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Read current implementations; engine looks up rule_options.get(name, {}) so per-rule injection in load_config reaches every enabled rule.
- Design: VisibilityConfig (frozen) + parse/load/coerce in project.py; CheckConfig.visibility field; load_config injects raw [tool.pypeeker.visibility] dict into each enabled rule's options under reserved key "visibility" (only when section present, preserving exact defaults).
- Shared helpers (_has_allowed_decorator, _dynamic_access_modules, _public_root_protected, suffix constant) live in check/rules.py; visibility.py and test_only import them.

- Implemented VisibilityConfig + parse/load/coerce in project.py; CheckConfig.visibility field; load_config injects raw visibility table into each enabled rule's options under reserved "visibility" key (only when section present — defaults are byte-identical).
- Shared helpers in check/rules.py (_public_root_protected, _dynamic_access_modules, _has_allowed_decorator, _merged_allow_decorators, _DYNAMIC_ACCESS_SUFFIX); consumed by unused-public-symbol, over-exposed-module-symbol, over-exposed-export, test-only-production-code. visibility.py's duplicate decorator helper removed in favour of the shared one.
- 38 new tests in tests/test_visibility_config.py; full suite: 981 passed, 2 failures pre-existing from parallel task-94/planner work (verified by stashing my files — failures persist without them).
- Note: for unused-public-symbol / over-exposed-module-symbol / test-only, barrel-exported symbols were ALREADY unconditionally exempt in app mode, so "app mode flags a barrel-exported symbol" is only observable on over-exposed-export; the explicit protected-set check is kept in the other three as the documented library contract.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added project-level [tool.pypeeker.visibility] config so visibility/dead-code rules stop being dangerous for libraries, whose external consumers are invisible to the index.

Config schema (one shared section, parsed by pypeeker.project which owns [tool.pypeeker] access):
- mode = "app" (default) | "library" — unknown values tolerantly fall back to "app".
- public-roots = dotted package/module prefixes whose barrel-exported names are sacred API. Default in library mode: every top-level package (all barrels protected — the safe default); explicit list overrides and narrows. Ignored in app mode.
- allow-decorators = global decorator fnmatch patterns marking symbols as externally called; merged with each rule's own allow-decorators option.

Changes:
- project.py: frozen VisibilityConfig dataclass with is_library / effective_public_roots, plus parse_visibility_config, load_visibility_config, coerce_visibility.
- check/config.py: CheckConfig gains a visibility field; load_config injects the raw visibility table into every enabled rule's options under the reserved "visibility" key (engine only hands rules rule_options[name], and CheckContext was out of scope). Injection happens only when the section exists, so existing projects see identical rule options.
- check/rules.py: shared helpers (_public_root_protected, _dynamic_access_modules, _has_allowed_decorator, _merged_allow_decorators, low-confidence suffix constant) implemented once; unused-public-symbol gains allow-decorators support, library-mode public-root exemption, and the dynamic-access suffix.
- check/builtin/visibility.py: over-exposed-module-symbol merges the global decorator list and honours public roots; over-exposed-export skips exports of barrels under a public root in library mode (the behavioural change for published API) and suffixes findings whose barrel module uses getattr/globals/vars/locals; duplicate decorator helper replaced by the shared one. under-exposed-access unchanged (not a demotion/dead-code rule).
- check/builtin/test_only_production_code.py: gains allow-decorators (merged with global), public-root exemption, and the dynamic-access suffix.
- Dynamic-access proximity: modules referencing builtin getattr/globals/vars/locals downgrade — not suppress — findings for symbols defined there via a " (low confidence: dynamic access present in module)" message suffix. Structured confidence field is a separate task; this suffix should migrate to it.

Tests: tests/test_visibility_config.py (38 tests) covers parsing/defaults/unknown-mode tolerance, effective-root resolution, config injection + no-section regression, library vs app behaviour across all four rules, explicit-roots override, global + per-rule decorator merging, and suffix presence/absence/non-suppression. Full suite: 981 passed; the 2 failures (test_hierarchy, test_preconditions) are pre-existing from parallel task-94/planner work in the shared tree, verified independent of this change. Ruff clean on all touched files.

Caveat: for unused-public-symbol, over-exposed-module-symbol and test-only-production-code, barrel-exported symbols were already unconditionally exempt in app mode, so the library-mode protection is only observable on over-exposed-export today; the explicit checks in the other three document the contract and survive if the blanket barrel skip ever becomes conditional.
<!-- SECTION:FINAL_SUMMARY:END -->
