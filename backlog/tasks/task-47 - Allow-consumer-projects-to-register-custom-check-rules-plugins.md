---
id: TASK-47
title: Allow consumer projects to register custom check rules (plugins)
status: To Do
assignee: []
created_date: '2026-05-25 02:08'
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
- [ ] #1 A public registration API lets external code register a rule by name (Rule = (FileIndex, options) -> list[Violation]); Rule and Violation are importable from pypeeker.check
- [ ] #2 A config key (e.g. [tool.pypeeker].plugins) lists importable modules; CheckEngine imports them so their registrations populate the rule registry before running
- [ ] #3 A consumer rule enabled via [tool.pypeeker].rules runs with its [tool.pypeeker.<rule>] options; built-in rules and existing behavior are unchanged; unknown/failed plugin import reports a clear error
- [ ] #4 Tests cover registering+running a custom rule via config; full suite green; pypeeker check exits 0
<!-- AC:END -->
