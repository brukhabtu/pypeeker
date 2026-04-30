---
id: TASK-3
title: 'Purity checker: detect impure builtin/stdlib calls'
status: Done
assignee:
  - '@claude'
created_date: '2026-04-29 23:32'
updated_date: '2026-04-29 23:38'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Detect calls to known-impure functions (print, open, input, file/network/random/time IO). Match call references against the denylist.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 For each Reference with kind=CALL inside the function's scope subtree, check if its resolved name (or unresolved tail) matches the denylist
- [x] #2 Builtins matched directly (print, open, input, exec, eval)
- [x] #3 Stdlib module calls matched by prefix (os., sys., subprocess., shutil., random., time.time, datetime.now, requests., socket.)
- [x] #4 Adds Evidence(kind='calls_impure_builtin' or 'calls_impure_stdlib', name=..., line=...) to the result
- [x] #5 Verdict becomes IMPURE if any such evidence is found
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. In _classify_reference, handle ReferenceKind.CALL\n2. For each call ref, derive a name to match: if resolved, use the symbol's name; if unresolved (e.g., 'print', 'os.system'), use symbol_id directly\n3. Match against IMPURE_BUILTINS frozenset -> CALLS_IMPURE_BUILTIN\n4. Match against IMPURE_MODULE_PREFIXES -> CALLS_IMPURE_STDLIB\n5. Smoke test against print, os.system, time.time, random.random
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented call detection in PurityChecker._classify_call. Two denylists in impure_builtins.py: IMPURE_BUILTINS for exact bare-name matches (print, open, input, exec, eval, ...) and IMPURE_ATTRIBUTE_NAMES for tail-of-attribute matches on '<unresolved>.X' (system, popen, write, append, time, random, mkdir, ...). Pypeeker drops the base of attribute calls in symbol_id ('os.system' becomes '<unresolved>.system'), so attribute matching is by tail name only — heuristic. False-positive mitigation: when a CALL on '<unresolved>.X' has a same-line READ on a local VARIABLE (not parameter), the evidence is suppressed (e.g., 'x = []; x.append(1)' is treated as pure local mutation). Parameters still flag because mutating them is a side effect on caller state. Verified on 6 cases including pure functions, print(), os.system equivalents, mutating args, mutating local lists, and global writes.
<!-- SECTION:FINAL_SUMMARY:END -->
