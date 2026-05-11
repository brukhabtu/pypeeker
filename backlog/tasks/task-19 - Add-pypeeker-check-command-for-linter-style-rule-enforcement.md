---
id: TASK-19
title: Add pypeeker check command for linter-style rule enforcement
status: Done
assignee:
  - '@claude'
created_date: '2026-05-11 12:27'
updated_date: '2026-05-11 12:32'
labels:
  - linter
  - check
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Expose pypeeker's semantic index as a linter via [tool.pypeeker] in pyproject.toml. Self-validation is the first real use case.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 src/pypeeker/check/ package created with config.py, models.py, rules.py, engine.py, __init__.py
- [x] #2 load_config() parses [tool.pypeeker] from pyproject.toml into CheckConfig dataclass (src, rules, rule_options)
- [x] #3 require-docstrings rule flags symbols matching configured kinds+visibility with docstring is None
- [x] #4 no-unresolved-refs rule flags references where resolved=False and symbol_id does not start with <unresolved>.
- [x] #5 Violation dataclass has rule/file_path/line/message; __str__ matches ruff/mypy 'path:line: [rule] message' format
- [x] #6 CheckEngine.run() iterates store.list_indexed_files() under config.src prefix, runs enabled rules, returns sorted violations
- [x] #7 pypeeker check CLI subcommand prints violations and exits 1 if any
- [x] #8 pyproject.toml has [tool.pypeeker] self-validation config enabling both rules
- [x] #9 .github/workflows/check.yml runs pypeeker index + pypeeker check on push/PR
- [x] #10 Unit tests cover config parsing, both rules, engine, and CLI output format
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create src/pypeeker/check/ package with models.py (Violation), config.py (CheckConfig + load_config), rules.py (require_docstrings, no_unresolved_refs), engine.py (CheckEngine), __init__.py.
2. Add check command in cli.py wired to CheckEngine.
3. Add [tool.pypeeker] section to pyproject.toml.
4. Add .github/workflows/check.yml.
5. Add unit tests under tests/test_check_*.py.
6. Run pytest and pypeeker check locally to confirm output.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- All 20 new tests pass (4 config + 9 rules + 7 engine/CLI).
- Full suite green: 192 passed.
- End-to-end pypeeker index src/ + pypeeker check produces ruff-style output on the project itself.
- End-to-end revealed false positives from binder marking Python builtins as unresolved; tracked as TASK-20 follow-up.
- Note: this branch (claude/install-backlog-md-OFhB8) is behind main (still uses pydantic + class Binder); ran tests on Python 3.11 because pydantic 2 is broken on 3.14.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add semantic linter \`pypeeker check\` driven by [tool.pypeeker] in pyproject.toml.

## What
- New package \`src/pypeeker/check/\` (~150 LOC across models, config, rules, engine, __init__).
- Two rules in the initial registry:
  - \`require-docstrings\` — flag symbols matching configured kinds+visibility where docstring is None.
  - \`no-unresolved-refs\` — flag references with resolved=False, skipping \`<unresolved>.*\` attribute chains.
- \`load_config(project_root)\` parses \`[tool.pypeeker]\` via stdlib \`tomllib\`; returns defaults when the section is missing.
- \`CheckEngine.run()\` iterates indexed files under \`config.src\` prefixes, runs enabled rules, and returns violations sorted by (file, line, rule).
- \`Violation\` is a frozen dataclass; \`__str__\` matches the ruff/mypy format \`path:line: [rule] message\` (1-indexed lines).

## CLI
- \`pypeeker check\` prints each violation and exits 1 when any are present.

## Self-validation
- \`pyproject.toml\` enables both rules with \`visibility = ["public"]\`.
- \`.github/workflows/check.yml\` runs \`pypeeker index src/\` then \`pypeeker check\` on push/PR.

## Tests
- 20 new tests across \`tests/test_check_config.py\`, \`tests/test_check_rules.py\`, \`tests/test_check_engine.py\` (CLI exit code, output format, src filter, rule options, line-numbering, sort order).
- Full suite: 192 passed on Python 3.11.

## Follow-up
- TASK-20: Binder should treat Python builtins as resolved (frozenset, property, len, ...) to reduce false positives from no-unresolved-refs.

## CI workflow (not committed: GitHub App lacks \`workflows\` permission)

Add this file manually as \`.github/workflows/check.yml\`:

```yaml
name: pypeeker-check

on:
  push:
    branches: [main]
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: astral-sh/setup-uv@v3
      - name: Install pypeeker
        run: uv pip install --system .
      - name: Index sources
        run: pypeeker index src/
      - name: Run pypeeker check
        run: pypeeker check
```
<!-- SECTION:FINAL_SUMMARY:END -->
