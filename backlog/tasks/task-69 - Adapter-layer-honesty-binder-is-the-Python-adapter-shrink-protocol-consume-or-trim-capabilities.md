---
id: TASK-69
title: >-
  Adapter layer honesty: binder is the Python adapter; shrink protocol; consume
  or trim capabilities
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 15:47'
updated_date: '2026-06-11 16:25'
labels:
  - adapters
  - binder
  - docs
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
LanguageAdapter's structural methods (capabilities, is_scope_node, is_declaration_node, is_reference_node, extract_name) are never called in src; the binder type-hints concrete PythonAdapter and hardcodes tree-sitter-python node types; refactor/cst.py owns CST edits the doc assigns to adapters. Make the architecture honest: the language-agnostic seam is FileIndex, and the binder+cst utilities ARE the Python adapter.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The protocol is shrunk to what a second language would actually provide (parse/bind to FileIndex + CST edit helpers), or unused methods are removed; no dead protocol surface remains
- [x] #2 Binder/cst placement or documentation makes 'binder = Python adapter' explicit (move under adapters/python or equivalent doc + layering update)
- [x] #3 Capability enum is either consumed by at least one real consumer or trimmed; decision recorded
- [x] #4 architecture.md Layer 1 section matches the implementation; import-boundaries allow-list updated if files move
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Grep-verify adapter method consumption (done: only parse, get_visibility, language_name consumed in src; capabilities, is_scope_node, is_declaration_node, is_reference_node, extract_name, get_type_annotation are dead/test-only)
2. Shrink LanguageAdapter Protocol in adapters/base.py to language_name + parse + get_visibility; delete dead methods from PythonAdapter (incl. _SCOPE_TYPES/_DECLARATION_TYPES constants and Capability import)
3. Capabilities decision: delete capabilities property (zero consumers); keep Capability enum in models/capabilities.py untouched (out of scope), record as reserved-for-roadmap in notes
4. Update tests/test_python_adapter.py: drop tests for deleted methods, keep parse/visibility/language_name coverage
5. Document binder=Python adapter: docstrings in adapters/__init__.py, adapters/base.py, binder/__init__.py, refactor/cst.py; record deferred physical move (binder under adapters/python/) as follow-up note
6. Rewrite architecture.md Layer 1 + Key Design Decisions capability bullet to match implementation (capability table = roadmap)
7. Run uv run pypeeker check and uv run pytest -q
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Grep-verified consumption across src/ and tests: only parse, get_visibility, language_name are consumed (indexer, applier, refactor/cst, binder, binder visitors, binder.bind). is_scope_node, is_declaration_node, is_reference_node, extract_name, capabilities, and get_type_annotation had ZERO src consumers (exercised only by tests/test_python_adapter.py) - deleted from both the LanguageAdapter Protocol and PythonAdapter, including _SCOPE_TYPES/_DECLARATION_TYPES constants; their tests removed.

DECISION (AC#3, capabilities): chose deletion over wiring a synthetic consumer. The capabilities property is removed from protocol and adapter. The Capability enum itself stays in models/capabilities.py (file outside this task scope) reserved for the multi-language roadmap; architecture.md now marks capability-gating as roadmap. Confidence enum is heavily used and untouched.

DECISION (AC#2, binder = Python adapter): documented via module docstrings rather than a physical move. adapters/__init__.py and adapters/base.py state the Python adapter is the package boundary {adapters.python_adapter + binder + refactor.cst} and FileIndex is the language-agnostic contract; binder/__init__.py says it is the Python-specific binding half (hardcoded tree-sitter-python node types by design); refactor/cst.py notes it is the Python-CST-specific editing third.

FOLLOW-UP (deferred): the honest end-state is likely a physical move of binder/ (and possibly refactor/cst.py) under adapters/python/. Deferred this round because other agents are concurrently editing cli.py/analysis/check and file moves would conflict; a follow-up task should perform the move and update the [tool.pypeeker.import-boundaries] allow-list accordingly. No allow-list changes were needed now (no files moved).

Verification: uv run pypeeker check exit 0; uv run pytest -q -> 609 passed, 10 skipped; ruff check clean on touched files.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Made the adapter layer honest: the LanguageAdapter protocol now covers only what consumers actually call, and prose throughout states that the Python adapter is a package boundary, with FileIndex as the real language-agnostic seam.

Changes:
- adapters/base.py: shrunk LanguageAdapter Protocol to language_name + parse + get_visibility; removed dead surface (capabilities, is_scope_node, is_declaration_node, is_reference_node, extract_name, get_type_annotation - all had zero src consumers). Module docstring now defines the adapter as the boundary {adapters.python_adapter + binder + refactor.cst} with FileIndex as the contract.
- adapters/python_adapter.py: deleted the same dead methods plus _SCOPE_TYPES/_DECLARATION_TYPES constants and the Capability import; docstring states it is one third of the Python adapter.
- adapters/__init__.py, binder/__init__.py, refactor/cst.py: docstrings make binder = Python-specific binding half and cst.py = Python-CST editing third explicit.
- architecture.md: Layer 1 rewritten to the actual contract (parse + bind to FileIndex + CST edit helpers; second language supplies all three); Key Design Decisions #2 and the Capability section marked roadmap.
- tests/test_python_adapter.py: dropped tests for deleted methods; kept parse/visibility/language_name coverage.

Decisions:
- capabilities property deleted (zero consumers); Capability enum kept in models/capabilities.py as reserved-for-roadmap (file out of scope this round).
- Physical move of binder/ under adapters/python/ recorded as deferred follow-up (concurrent agents editing cli/analysis/check made moves unsafe); import-boundaries allow-list unchanged since no files moved.

Tests: uv run pypeeker check exit 0; uv run pytest -q -> 609 passed, 10 skipped; ruff clean.
<!-- SECTION:FINAL_SUMMARY:END -->
