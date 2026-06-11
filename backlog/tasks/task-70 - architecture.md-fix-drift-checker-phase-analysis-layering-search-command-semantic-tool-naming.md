---
id: TASK-70
title: >-
  architecture.md: fix drift (checker phase, analysis layering, search command,
  semantic-tool naming)
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 15:53'
labels:
  - docs
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
architecture.md is load-bearing, so drift matters: the pipeline diagram shows a Checker (type info) phase that does not exist; the layering list omits resolve from analysis deps while pyproject allows it; 'search <query>' is documented as a core command but unimplemented; docs and the storage dir still say semantic-tool while the product is pypeeker. Decide and record the naming question; align the rest.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Pipeline diagram matches the implemented phases
- [x] #2 Module layering section matches pyproject [tool.pypeeker.import-boundaries.allow]
- [x] #3 Unimplemented commands (search) are marked roadmap or removed
- [x] #4 semantic-tool vs pypeeker naming decision recorded (doc note or decision record); no silent inconsistency
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Verify drift against code: cli.py commands, pyproject import-boundaries, index_store.py SEMANTIC_TOOL_DIR (done)
2. Fix architecture.md pipeline diagram: drop nonexistent Checker phase; show tree-sitter lexer/parser, Binder -> per-file FileIndex, semantic model = per-file indexes + cross-file tree + on-demand CrossModuleResolver; note `pypeeker check` is a consumer
3. Cross-check every Module Layering line against [tool.pypeeker.import-boundaries.allow]; fix analysis (add resolve), check (add project), make indexer/refactor lines explicit
4. LLM Integration: change `semantic-tool <command>` to `pypeeker <command>`; replace command list with actual CLI commands; move `search` (and other unimplemented ideas) to a short Roadmap subsection
5. storage-transaction-architecture.md: add explicit note that the storage dir is `.semantic-tool/` while the product is pypeeker, rename to `.pypeeker/` is an open decision; update `semantic-tool` CLI examples to `pypeeker`
6. Run uv run pytest -q to confirm nothing broke (docs only)
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Verified drift against code: cli.py click commands (index, check, symbol, refs, tree, scope, plan-rename, plan-extract-variable, plan-extract-method, plan-inline-variable, apply), pyproject [tool.pypeeker.import-boundaries.allow], and SEMANTIC_TOOL_DIR=".semantic-tool" in src/pypeeker/storage/index_store.py
- architecture.md: pipeline diagram now shows tree-sitter Lexer+Parser -> Binder (per-file FileIndex) -> Semantic Model (per-file indexes + cross-file tree + on-demand CrossModuleResolver); added note that there is no checker phase and `pypeeker check` is a Layer-3 consumer
- architecture.md: Module Layering cross-checked line-by-line against pyproject; fixed check (+project), analysis (+resolve), made indexer/refactor allow-lists explicit; noted pyproject as enforced source of truth
- architecture.md: LLM Integration now uses `pypeeker <command>`, lists actual implemented commands, and moves `search <query>` to a Roadmap (not implemented) subsection; removed nonexistent `lint` command
- storage-transaction-architecture.md: added explicit naming note recording that the storage dir is `.semantic-tool/` while the product is pypeeker, with rename to `.pypeeker/` an open decision (needs migration story); updated `semantic-tool` CLI examples to `pypeeker`
- Kept edits surgical; did not touch the Layer-1/adapter section (reserved for TASK-69)
- uv run pytest -q: 495 passed, 10 skipped (docs-only change; count above 486 baseline due to concurrent tasks adding tests)
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed documented-vs-implemented drift in architecture.md and recorded the semantic-tool naming inconsistency in storage-transaction-architecture.md. Docs-only change; no source code touched.

Changes:
- architecture.md pipeline diagram: removed the nonexistent "Checker -> Type Info + Diagnostics" phase; now shows tree-sitter Lexer+Parser -> Binder (per-file FileIndex) -> Semantic Model (per-file indexes + cross-file symbol tree + on-demand CrossModuleResolver), with an explicit note that `pypeeker check` is a Layer-3 consumer over the model, not a pipeline stage, and type checking is not implemented.
- architecture.md Module Layering: cross-checked every line against pyproject [tool.pypeeker.import-boundaries.allow] (the enforced truth). Fixed `check` (added `project`), `analysis` (added `resolve`), replaced the vague "indexer, refactor -> ... as needed" line with the exact allow-lists, and noted pyproject as the source of truth.
- architecture.md LLM Integration: examples now use `pypeeker <command>` (was `semantic-tool`); command list replaced with the actual CLI commands from src/pypeeker/cli.py (index, check, symbol, refs, tree, scope, plan-rename, plan-extract-variable, plan-extract-method, plan-inline-variable, apply); unimplemented `search <query>` moved to a "Roadmap (not implemented)" subsection; nonexistent `lint` removed in favor of the real `check`.
- storage-transaction-architecture.md: added an explicit naming note that the on-disk dir is `.semantic-tool/` (SEMANTIC_TOOL_DIR in src/pypeeker/storage/index_store.py) while the product is pypeeker, and that a rename to `.pypeeker/` is an open decision pending a migration story — recorded, not silent drift. CLI examples updated from `semantic-tool` to `pypeeker`. The directory itself was intentionally NOT renamed in code.
- Layer-1/adapter section left untouched (TASK-69 owns it).

Tests: uv run pytest -q -> 495 passed, 10 skipped.
<!-- SECTION:FINAL_SUMMARY:END -->
