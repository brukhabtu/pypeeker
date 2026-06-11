---
id: TASK-67
title: 'Expose purity: pypeeker purity command (and rename is_pure to impurities)'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:04'
labels:
  - analysis
  - cli
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The largest, best-tested analysis in the codebase (is_pure, call graph, typed receivers) has no CLI command and no check rule — only tests and (partially) refactor/dataflow consume it. Also, is_pure returns truthy-for-impure, a trap its own docstring acknowledges. Expose it and fix the name in the same change.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 pypeeker purity <symbol-id> emits a JSON verdict plus the typed observations (kind, line, detail) including transitive impure calls
- [x] #2 is_pure is renamed to a name matching its semantics (e.g. impurities); all call sites and tests updated
- [x] #3 Unanalyzable symbols produce a structured error (not-found / not-a-function), exit non-zero
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Rename is_pure -> impurities in src/pypeeker/analysis/purity.py; rewrite module docstring naming discussion (truthy = impurities found).
2. Update src/pypeeker/analysis/__init__.py import/__all__.
3. Mechanical rename in tests/test_purity.py, tests/test_purity_typed_receivers.py, tests/test_purity_call_graph.py, tests/test_purity_self.py via word-boundary sed (test names like *_is_pure are untouched); dedupe import lines. conftest.py has no references.
4. New CLI command `pypeeker purity SYMBOL_ID` in src/pypeeker/cli.py: _refresh_index/--no-refresh pattern; calls AnalysisContext.for_function first to surface precise ContextError reason (not_found / not_a_function) as JSON + exit 1; otherwise impurities() and emit {symbol_id, pure, observations:[{kind: dataclass name, ...to_dict fields}]}.
5. New tests/test_purity_cli.py with CliRunner (pattern from test_rename_cli.py): pure verdict, impure with observation payload incl. transitive call, not-found exit 1, not-a-function exit 1.
6. Run uv run pytest -q.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Rename is_pure -> impurities completed earlier (purity.py, analysis/__init__.py, tests/test_purity*.py); refactor/dataflow.py is_pure is an unrelated honest bool field on RangeDataflow, left alone; conftest.py had no references.
- Added `pypeeker purity SYMBOL_ID` to src/pypeeker/cli.py with the _refresh_index/--no-refresh freshness pattern; calls AnalysisContext.for_function first to surface precise ContextError (reason/symbol_id/detail) as JSON + exit 1, then impurities(); observations serialized via models.serialize.to_dict with a "kind" discriminator (dataclass name).
- Added tests/test_purity_cli.py (7 tests, CliRunner): help, pure verdict, impure BareCall payload (name/line), transitive impure call (kind + callee), not_found exit 1, not_a_function exit 1, top-level help listing.
- Full suite: 531 passed, 10 skipped, 0 failures.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Exposed the purity analysis on the CLI and renamed its trap-prone entry point.

Changes:
- Renamed analysis.purity.is_pure -> impurities (same None / empty / non-empty Observations semantics, but the name now matches truthiness: truthy = impurities found). Updated analysis/__init__.py exports and the module docstring naming discussion; mechanically updated tests/test_purity.py, test_purity_call_graph.py, test_purity_typed_receivers.py, test_purity_self.py. refactor/dataflow.py untouched: its RangeDataflow.is_pure is an unrelated, honest bool field, and dataflow consumes observations(), not the renamed function.
- New CLI command `pypeeker purity SYMBOL_ID` in src/pypeeker/cli.py. Follows the _refresh_index/--no-refresh freshness pattern. Resolves via AnalysisContext.for_function first so unanalyzable symbols emit a structured JSON error ({error, reason: not_found|not_a_function, symbol_id, detail}) with exit 1, then emits {symbol_id, pure, observations} where each observation is to_dict(dataclass) plus a "kind" discriminator (OuterScopeWrite, AttributeWrite, BareCall, ModuleCall, AttributeMethodCall, TransitiveImpureCall). Docstring states the verdict semantics: pure=true means no impurity found by the configured policy, not provably pure.

Tests:
- New tests/test_purity_cli.py (CliRunner, 7 tests): pure verdict, impure observation payload, transitive impure call, not-found and not-a-function structured errors with non-zero exit, help listings.
- uv run pytest -q: 531 passed, 10 skipped, 0 failures.
<!-- SECTION:FINAL_SUMMARY:END -->
