---
id: TASK-22
title: 'Add pypeeker check command: linter-style rule enforcement via pyproject.toml'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-12 12:51'
updated_date: '2026-05-12 15:43'
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
- [x] #1 src/pypeeker/check/ package with config.py (CheckConfig + load_config via tomllib), models.py (Violation dataclass), rules.py (require-docstrings, no-unresolved-refs), engine.py (CheckEngine), __init__.py
- [x] #2 load_config parses [tool.pypeeker] section into CheckConfig with src, rules, rule_options (subsection options like [tool.pypeeker.require-docstrings])
- [x] #3 require-docstrings rule: flags symbols matching configured kinds (default: function, method, class) and visibility (default: public) where docstring is None
- [x] #4 no-unresolved-refs rule: flags references where resolved=False and symbol_id does not start with '<unresolved>.'
- [x] #5 Violation dataclass: rule, file_path, line, message; __str__ returns 'path:line: [rule] message' to match ruff/mypy format; line numbers are 1-indexed in output even though stored 0-indexed internally
- [x] #6 CheckEngine.run() iterates store.list_indexed_files() filtered by config.src prefixes, runs enabled rules from the registry, returns violations sorted by (file_path, line, rule)
- [x] #7 pypeeker check CLI subcommand prints each violation and exits 1 if any are present
- [x] #8 pyproject.toml has [tool.pypeeker] self-validation enabling both rules with visibility=['public']
- [x] #9 .github/workflows/check.yml runs pypeeker index src/ then pypeeker check on push/PR (note: workflow files must be added manually due to GitHub App workflows permission)
- [x] #10 Unit tests cover: config parsing (4+ cases), each rule's positive/negative paths and options, engine src filter and sort order, CLI exit codes and output format
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create src/pypeeker/check/ package: models.py (Violation), config.py (CheckConfig + load_config via tomllib), rules.py (require_docstrings, no_unresolved_refs + REGISTRY), engine.py (CheckEngine), __init__.py.
2. Add `pypeeker check` subcommand to cli.py wired to CheckEngine.
3. Add [tool.pypeeker] section to pyproject.toml for self-validation (both rules, visibility=public).
4. Add tests: test_check_config.py, test_check_rules.py, test_check_engine.py.
5. Run full pytest and pypeeker index + pypeeker check on pypeeker's own source to confirm output quality.
6. Commit, push, PR, merge. Workflow YAML included in commit (it failed last time due to GitHub App perms — may need to drop and document in summary).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- 22 new tests across config/rules/engine/CLI all green.
- Full suite 334 passed. Also fixed stale paths in test_purity_self.py left over from PR B (they pointed at storage/store.py which no longer exists; only surfaced once we re-indexed src/).
- End-to-end pypeeker check on own source produces ruff-format output. require-docstrings finds real missing docstrings in adapters/base.py and adapters/python_adapter.py. no-unresolved-refs surfaces two real binder bugs filed as TASK-23 (kwargs in calls treated as identifiers) and TASK-24 (forward refs to module-level symbols not resolved).
- TASK-21 confirmed satisfied: zero plain-builtin false positives. The remaining noise is genuine binder gaps, not builtins.
- Workflow file (.github/workflows/check.yml) included but may bounce on push — GitHub App lacks workflows permission; YAML preserved in Final Summary.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Add the \`pypeeker check\` semantic linter — rules declared in \`[tool.pypeeker]\` of \`pyproject.toml\`, output in ruff/mypy format.

## What
New \`src/pypeeker/check/\` package:
- **\`models.py\`** — frozen \`Violation\` dataclass with \`order=True\` for stable sorting; \`__str__\` returns \`path:line: [rule] message\`.
- **\`config.py\`** — \`CheckConfig\` + \`load_config(project_root)\` using stdlib \`tomllib\`. Returns defaults (empty rules) when the section is missing.
- **\`rules.py\`** — \`require_docstrings\` (kinds/visibility configurable, defaults: function|method|class × public) and \`no_unresolved_refs\` (skips \`<unresolved>.*\` chains; builtins land on \`<builtins>.*\` resolved=True so they don't fire). Pluggable \`REGISTRY\` for future rules.
- **\`engine.py\`** — \`CheckEngine\` iterates \`store.list_indexed_files()\` filtered by \`config.src\` prefixes, runs enabled rules, returns violations sorted by (file, line, rule).

## CLI
\`pypeeker check\` prints violations and exits 1 if any found.

## Self-validation
\`pyproject.toml\` enables both rules with \`visibility = ["public"]\`. \`.github/workflows/check.yml\` runs \`pypeeker index src/ && pypeeker check\` on push/PR.

## Tests
22 new across \`test_check_config.py\` / \`test_check_rules.py\` / \`test_check_engine.py\`:
- Config: missing pyproject, missing section, parses src+rules, parses rule_options.
- Rules: each rule's positive/negative paths, options, line-numbering, attribute-chain skip, builtin skip after TASK-21.
- Engine: empty rules → empty result, src filter, sort order, options pass-through, unknown rule names ignored, CLI exit codes.

Also fixed stale paths in \`tests/test_purity_self.py\` left over from PR B (the storage split renamed files; the tests only surfaced once we re-indexed for TASK-21).

Full suite: 334 passed, 0 skipped (purity_self tests now exercise instead of skipping).

## End-to-end
Running \`pypeeker index src/ && pypeeker check\` on the project itself produces real findings — missing docstrings on adapter base methods, plus two binder gaps now tracked as follow-ups.

## Follow-ups filed
- **TASK-23**: binder should skip keyword-argument names in calls (\`dataclass(frozen=True)\` shouldn't flag \`frozen\` as unresolved).
- **TASK-24**: binder should resolve forward references to module-level symbols (a function calling another defined later in the same file).

## Workflow file caveat
\`.github/workflows/check.yml\` is included but the GitHub App lacks \`workflows\` permission and may reject it on push. If so, add this content manually:

\`\`\`yaml
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
          python-version: "3.14"
      - uses: astral-sh/setup-uv@v3
      - run: uv pip install --system .
      - run: pypeeker index src/
      - run: pypeeker check
\`\`\`
<!-- SECTION:FINAL_SUMMARY:END -->
