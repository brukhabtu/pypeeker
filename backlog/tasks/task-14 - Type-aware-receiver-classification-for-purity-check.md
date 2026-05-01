---
id: TASK-14
title: Type-aware receiver classification for purity check
status: To Do
assignee: []
created_date: '2026-05-01 23:29'
updated_date: '2026-05-01 23:29'
labels: []
dependencies:
  - TASK-13
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When a receiver root has a type annotation (e.g., 'def f(path: Path)'), use it to refine the receiver-kind dispatch. Today a parameter with type Path falls into the generic PARAMETER bucket and gets all denylist entries flagged. With type info we can match against type-specific denylists ('pathlib.Path.write_text' fires when the receiver is annotated as Path) and avoid flagging unrelated objects with the same method name. Also enables matching for typed local variables (currently treated as generic VARIABLE without type info).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Symbol.type_annotation.raw is parsed to extract the bare type name (e.g., 'Path' from 'Path | None', 'pathlib.Path' from 'pathlib.Path')
- [ ] #2 AnalysisContext caches a map of {symbol_id -> bare_type_name} for symbols inside the function
- [ ] #3 Receiver classification uses the type name when available: receiver root is PARAMETER with type 'Path' -> match leaf against pathlib.Path.* denylist exactly, not the generic IO_METHOD_NAMES set
- [ ] #4 Type-keyed denylist module: TYPE_IMPURE_METHODS = {'Path': frozenset({'write_text', 'unlink', 'mkdir', ...}), 'IO': frozenset({'write', 'read', ...})}
- [ ] #5 Adds tests for typed-parameter case (def f(p: Path): p.write_text(x) -> impure with PATH_METHOD evidence) and typed-local case (p: Path = ...; p.write_text(x))
- [ ] #6 Untyped receivers continue to use the existing structural fallback (no regressions in the 232-test suite)
<!-- AC:END -->
