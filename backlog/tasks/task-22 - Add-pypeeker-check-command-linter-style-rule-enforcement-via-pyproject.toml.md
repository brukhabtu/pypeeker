---
id: TASK-22
title: 'Add pypeeker check command: linter-style rule enforcement via pyproject.toml'
status: To Do
assignee: []
created_date: '2026-05-12 12:51'
labels:
  - linter
  - check
dependencies:
  - TASK-21
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose pypeeker's semantic index as a linter. Users declare rules in [tool.pypeeker] in pyproject.toml (ruff/mypy UX) and run 'pypeeker check' to get violations. Self-validation is the first real-world use case: pypeeker's own pyproject.toml will declare its own rules. Depends on TASK-21 so the no-unresolved-refs rule produces signal instead of hundreds of builtin false positives on its first CI run.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 src/pypeeker/check/ package with config.py (CheckConfig + load_config via tomllib), models.py (Violation dataclass), rules.py (require-docstrings, no-unresolved-refs), engine.py (CheckEngine), __init__.py
- [ ] #2 load_config parses [tool.pypeeker] section into CheckConfig with src, rules, rule_options (subsection options like [tool.pypeeker.require-docstrings])
- [ ] #3 require-docstrings rule: flags symbols matching configured kinds (default: function, method, class) and visibility (default: public) where docstring is None
- [ ] #4 no-unresolved-refs rule: flags references where resolved=False and symbol_id does not start with '<unresolved>.'
- [ ] #5 Violation dataclass: rule, file_path, line, message; __str__ returns 'path:line: [rule] message' to match ruff/mypy format; line numbers are 1-indexed in output even though stored 0-indexed internally
- [ ] #6 CheckEngine.run() iterates store.list_indexed_files() filtered by config.src prefixes, runs enabled rules from the registry, returns violations sorted by (file_path, line, rule)
- [ ] #7 pypeeker check CLI subcommand prints each violation and exits 1 if any are present
- [ ] #8 pyproject.toml has [tool.pypeeker] self-validation enabling both rules with visibility=['public']
- [ ] #9 .github/workflows/check.yml runs pypeeker index src/ then pypeeker check on push/PR (note: workflow files must be added manually due to GitHub App workflows permission)
- [ ] #10 Unit tests cover: config parsing (4+ cases), each rule's positive/negative paths and options, engine src filter and sort order, CLI exit codes and output format
<!-- AC:END -->
