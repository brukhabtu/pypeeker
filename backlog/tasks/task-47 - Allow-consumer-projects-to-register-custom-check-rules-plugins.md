---
id: TASK-47
title: Allow consumer projects to register custom check rules (plugins)
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 02:08'
updated_date: '2026-05-25 02:15'
labels:
  - check
  - architecture
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
pypeeker check rules are currently a hardcoded REGISTRY. Consumers should be able to add project-specific lint rules. Add a plugin mechanism: a public rule registration API (Rule type + register decorator + Violation already exported) and a config key listing rule modules to import, so a consuming project can define rules in its own code, enable them in [tool.pypeeker].rules, and pass options via [tool.pypeeker.<rule>]. Built-in rules keep working unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A public registration API lets external code register a rule by name (Rule = (FileIndex, options) -> list[Violation]); Rule and Violation are importable from pypeeker.check
- [x] #2 A config key (e.g. [tool.pypeeker].plugins) lists importable modules; CheckEngine imports them so their registrations populate the rule registry before running
- [x] #3 A consumer rule enabled via [tool.pypeeker].rules runs with its [tool.pypeeker.<rule>] options; built-in rules and existing behavior are unchanged; unknown/failed plugin import reports a clear error
- [x] #4 Tests cover registering+running a custom rule via config; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
rules.py: _REGISTERED dict + register_rule(name) decorator + get_rule(name) (built-in precedence). config.py: CheckConfig.plugins tuple; load_config parses [tool.pypeeker].plugins, excludes from rule_options. engine.py: ensure project_root on sys.path, import each plugin module (clear error on failure), resolve via get_rule. __init__: export register_rule, Rule. Tests: custom rule via temp module+config runs+flags; bad plugin errors clearly. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented a plugin mechanism for pypeeker check:
- check/rules.py: register_rule(name) decorator + get_rule(name) (built-in precedence over registered); Rule type already existed.
- check/config.py: CheckConfig.plugins tuple; load_config parses [tool.pypeeker].plugins (excluded from rule_options).
- check/engine.py: _load_plugins() puts project_root on sys.path and imports each plugin module (so register_rule fires), raising CheckConfigError with the module name on import failure; rules resolve via get_rule (built-ins + registered).
- check/__init__.py: exports register_rule, Rule, CheckConfigError (Violation already exported).

Consumer usage: define a rule (FileIndex, options) -> list[Violation], decorate with @register_rule("name"), list the module in [tool.pypeeker].plugins, enable in [tool.pypeeker].rules, pass options via [tool.pypeeker.<name>].

Verified end-to-end: a temp project with lint_rules.py + pyproject plugins/rules flagged a custom no-todo-prefix violation. self-check surfaced (and I fixed) a walrus-in-comprehension and a public nested decorator in my own new code. 446 tests pass; pypeeker check exits 0; ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Let consumer projects define their own pypeeker check rules. New public API in pypeeker.check: a register_rule(name) decorator and the Rule type (Violation was already exported). A new [tool.pypeeker].plugins config key lists importable modules; CheckEngine imports them (with the project root on sys.path, so in-repo rule modules work too) before running, so their registrations populate the rule registry. Built-in rules keep precedence; an unimportable plugin raises CheckConfigError naming the module.

Consumer flow: write a rule (FileIndex, options) -> list[Violation], decorate @register_rule("my-rule"), add its module to [tool.pypeeker].plugins, enable "my-rule" in [tool.pypeeker].rules, and pass options via [tool.pypeeker.my-rule].

Verified end-to-end with a sample project (custom no-todo-prefix rule fires via pyproject config). 446 tests pass incl. new plugin tests (registration, plugin rule runs, options passthrough, clear error on bad plugin); pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
