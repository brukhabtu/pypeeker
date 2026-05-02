---
id: TASK-14
title: Type-aware receiver classification for purity check
status: Done
assignee: []
created_date: '2026-05-01 23:29'
updated_date: '2026-05-02 00:08'
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
- [x] #1 Symbol.type_annotation.raw is parsed to extract the bare type name (e.g., 'Path' from 'Path | None', 'pathlib.Path' from 'pathlib.Path')
- [x] #2 AnalysisContext caches a map of {symbol_id -> bare_type_name} for symbols inside the function
- [x] #3 Receiver classification uses the type name when available: receiver root is PARAMETER with type 'Path' -> match leaf against pathlib.Path.* denylist exactly, not the generic IO_METHOD_NAMES set
- [x] #4 Type-keyed denylist module: TYPE_IMPURE_METHODS = {'Path': frozenset({'write_text', 'unlink', 'mkdir', ...}), 'IO': frozenset({'write', 'read', ...})}
- [x] #5 Adds tests for typed-parameter case (def f(p: Path): p.write_text(x) -> impure with PATH_METHOD evidence) and typed-local case (p: Path = ...; p.write_text(x))
- [x] #6 Untyped receivers continue to use the existing structural fallback (no regressions in the 232-test suite)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added type-aware receiver classification on top of TASK-13's structural dispatch.

New helper _bare_type_name in analysis/context.py normalizes Symbol.type_annotation.raw to a single bare type name. Handles common shapes: 'Path' -> 'Path', 'pathlib.Path' -> 'Path', 'Path | None' -> 'Path', 'Optional[Path]' -> 'Path', 'Union[Path, str]' -> 'Path' (first arg), 'list[int]' -> 'list'. Returns None for empty/unparseable.

AnalysisContext gains local_type_names: dict[str, str] mapping symbol_id -> bare type name for every parameter and local variable with a normalizable annotation. Built once in for_function() and reused by all extractors.

New TYPE_IMPURE_METHODS dict in _purity_denylists.py keyed by bare type name, with method denylists per type:
- Path: write_text, write_bytes, read_text, read_bytes, unlink, mkdir, etc.
- IO/TextIO/BinaryIO: write, writelines, read, readline, etc.
- Logger: debug, info, warning, error, critical, exception, log

ALL_TRACKED_METHOD_NAMES is the union of IO + COLLECTION + every type-specific method, used as a single coarse filter at the fact extractor; the check applies the precise per-type or per-kind policy.

AttributeMethodCall fact gains receiver_type: str | None, populated by find_attribute_method_calls from ctx.local_type_names. Purity check policy now prioritizes type-aware matching: if receiver_type is in TYPE_IMPURE_METHODS, match leaf against that type's exact set; otherwise fall back to the receiver-kind structural dispatch.

Added 21 tests in tests/test_purity_typed_receivers.py covering: _bare_type_name extraction (12 parametrized cases including PEP 604 unions, Optional/Union wrappers, generics, module prefixes), Path-typed parameter and local variable cases (write_text impure, with_suffix.name pure), Logger-typed parameter (logging.Logger.info impure), unknown type falls back to receiver-kind dispatch, AnalysisContext.local_type_names captures both parameters and locals.

Full suite 257/257 passing (236 -> 257, +21). No regressions in self-test or earlier purity/fact tests.
<!-- SECTION:FINAL_SUMMARY:END -->
