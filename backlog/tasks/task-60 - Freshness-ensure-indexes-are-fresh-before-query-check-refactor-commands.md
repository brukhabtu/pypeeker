---
id: TASK-60
title: 'Freshness: ensure indexes are fresh before query/check/refactor commands'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:46'
updated_date: '2026-06-11 15:57'
labels:
  - cli
  - storage
dependencies: []
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
storage-transaction-architecture.md promises 'before any operation, hash source files and re-index stale files', but only RenamePlanner._validate_files checks staleness. check, symbol, refs, scope, tree, and the extract/inline planners silently serve stale data; extract/inline are worst since they read current file bytes while trusting a stale index for scopes/references. Add a single ensure-fresh step (reuse index_path, which already skips unchanged files) applied by the CLI before serving queries, with an opt-out flag.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Query/check CLI commands (symbol, refs, scope, tree, check) re-index stale files before answering, or refuse with a clear staleness error
- [x] #2 extract-variable, extract-method, and inline-variable planners refuse or refresh stale indexes before planning
- [x] #3 An opt-out flag (e.g. --no-refresh) preserves the old fast path
- [x] #4 Behavior documented (README/architecture or command help)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add ensure_fresh(store, root, ...) -> IndexResult to src/pypeeker/indexer.py: iterate store.list_indexed_files(); remove index entries whose source file is gone; re-bind stale files (reusing the per-file logic shared with index_path); never-indexed projects are a no-op. Add a removed field to IndexResult.
2. In src/pypeeker/cli.py add a shared --no-refresh option + _refresh helper; call it at the start of check, symbol, refs, tree, scope, plan-extract-variable, plan-extract-method, plan-inline-variable, plan-rename. Document the refresh behavior in each command docstring (help text).
3. In src/pypeeker/refactor/extract.py add a stale-index backstop to ExtractVariablePlanner.plan and ExtractMethodPlanner.plan (error style matching inline.py: "File is stale or not indexed: ..."). inline.py already has the check.
4. Tests: new tests/test_cli_freshness.py (stale file re-indexed before refs/symbol/check answer; --no-refresh skips refresh; deleted file entry pruned; never-indexed project untouched; plan-extract-variable refuses stale with --no-refresh). Extend tests/test_indexer.py with ensure_fresh unit tests. Update test_extract_variable.py helper to index files so the new backstop passes.
5. Run full suite, check ACs, write final summary.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added ensure_fresh(store, root) to src/pypeeker/indexer.py: refreshes only files already in the index (store.list_indexed_files()), re-binding stale ones via a shared _index_file helper (also reused by index_path) and removing entries whose source file is gone; never-indexed projects are a strict no-op.
- cli.py: shared --no-refresh option (_no_refresh_option) + _refresh_index helper called at the start of check, symbol, refs, tree, scope, plan-extract-variable, plan-extract-method, plan-inline-variable, plan-rename. The index command is untouched (no double work). Help text on each command documents the refresh + opt-out.
- extract.py: ExtractVariablePlanner.plan and ExtractMethodPlanner.plan now refuse with "File is stale or not indexed: <path>" (same wording as InlineVariablePlanner) as a backstop when called with --no-refresh or via the library.
- Tests: new tests/test_cli_freshness.py (10 tests: refs/symbol/check see post-index edits; --no-refresh serves the stale fast path; deleted files pruned; never-indexed project untouched; extract planners refuse stale under --no-refresh; inline refreshes); TestEnsureFresh added to tests/test_indexer.py (5 unit tests); test_extract_variable.py helper now binds+saves indexes so the backstop passes.
- Full suite: 524 passed, 10 skipped; ruff clean on touched files.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Query, check, and refactor-planning commands now refresh stale index entries before answering, closing the gap where storage-transaction-architecture.md promised "hash source files and re-index stale files before any operation" but only RenamePlanner checked staleness.

Changes:
- src/pypeeker/indexer.py: new ensure_fresh(store, root, adapter=, src_roots=) -> IndexResult. Scope is strictly "files that already have an index entry": stale entries are re-bound, entries whose source file no longer exists are removed (new IndexResult.removed field), fresh entries are skipped via the existing hash check. A never-indexed project is a no-op, so commands keep their empty-result/error behavior instead of triggering a surprise full index. Per-file bind/save logic is now shared between index_path and ensure_fresh via _index_file.
- src/pypeeker/cli.py: shared --no-refresh flag and _refresh_index helper invoked at the start of check, symbol, refs, tree, scope, plan-extract-variable, plan-extract-method, plan-inline-variable, and plan-rename. The index command is deliberately untouched. Each command's help documents "Stale index entries are re-indexed first unless --no-refresh is given."
- src/pypeeker/refactor/extract.py: ExtractVariablePlanner.plan and ExtractMethodPlanner.plan refuse stale/unindexed files ("File is stale or not indexed: <path>", matching InlineVariablePlanner) as a planner-level backstop; previously they read current file bytes while trusting a possibly-stale index.

Tests:
- tests/test_cli_freshness.py (new, 10 tests): refs/symbol/check observe on-disk edits made after indexing; --no-refresh preserves the stale fast path; deleted files are pruned from the index; never-indexed projects stay untouched; extract planners refuse stale under --no-refresh; inline-variable plans against the refreshed index.
- tests/test_indexer.py: TestEnsureFresh (5 unit tests) covering refresh, prune, no-op, no-widening, and per-file error collection.
- tests/test_extract_variable.py: project helper now indexes files so the new backstop is satisfied.
- Full suite: 524 passed, 10 skipped (uv run pytest -q); ruff clean on touched files.

Risks/notes: commands now pay a hash-check per indexed file on every invocation; re-binding only happens for genuinely stale files, and --no-refresh restores the old behavior where that matters.
<!-- SECTION:FINAL_SUMMARY:END -->
