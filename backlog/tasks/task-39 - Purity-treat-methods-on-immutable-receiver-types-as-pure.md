---
id: TASK-39
title: 'Purity: treat methods on immutable receiver types as pure'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-24 03:39'
updated_date: '2026-05-24 03:42'
labels:
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Dogfooding finding. The purity checker flags module_path_from as impure because of path.replace(...) where path: str. In _filtered_attribute_method_calls, a PARAMETER receiver flags any tracked method as a caller-visible mutation, even when the receiver type is known to be immutable (receiver_type=str). str.replace/.strip/.split/etc. return new values and are pure. The available receiver_type is used only to consult the impure allow-set (TYPE_IMPURE_METHODS), never to suppress methods on immutable types. Fix: when the receiver type is a known immutable builtin (str, bytes, int, float, bool, complex, tuple, frozenset, NoneType), the method call is pure.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Methods on receivers with a known immutable type (str/bytes/int/float/bool/complex/tuple/frozenset/NoneType) are not flagged impure, regardless of receiver kind
- [x] #2 module_path_from is classified pure; existing impure detections (Path/IO/Logger I/O, collection mutations on params, module I/O) are unchanged
- [x] #3 Unannotated receivers keep their conservative classification (no type info => unchanged behavior)
- [x] #4 Full suite green incl. purity tests; pypeeker check exits 0; a test covers str.replace on a parameter being pure
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
purity.py: add IMMUTABLE_RECEIVER_TYPES; in _filtered_attribute_method_calls, skip (pure) when call.receiver_type in IMMUTABLE_RECEIVER_TYPES before the PARAMETER fallback. Test: param: str; s.replace() -> pure. Verify module_path_from pure. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
purity.py: added IMMUTABLE_RECEIVER_TYPES (str/bytes/int/float/bool/complex/tuple/frozenset/NoneType); _filtered_attribute_method_calls now skips (pure) when receiver_type is immutable, before the PARAMETER fallback. Verified module_path_from is now pure; purity suite + full suite pass. Tests added: str param .replace().strip() pure; tuple param .index() pure.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fix a purity false positive: methods on known-immutable receiver types are pure. The checker flagged module_path_from impure for path.replace(...) where path: str, because a PARAMETER receiver flagged any tracked method as a caller-visible mutation — ignoring that receiver_type=str is immutable and str.replace returns a new value. Added IMMUTABLE_RECEIVER_TYPES (str, bytes, int, float, bool, complex, tuple, frozenset, NoneType); _filtered_attribute_method_calls now treats calls on those receivers as pure before the receiver-kind fallback. Path/IO/Logger I/O, collection mutations on params, and unannotated-receiver conservatism are unchanged. Dogfood-validated: module_path_from is now pure. 428 tests pass (new: str/tuple param pure-method tests); pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
