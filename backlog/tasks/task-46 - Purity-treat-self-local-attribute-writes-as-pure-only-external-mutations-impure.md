---
id: TASK-46
title: >-
  Purity: treat self/local attribute writes as pure (only external mutations
  impure)
status: Done
assignee:
  - '@claude'
created_date: '2026-05-25 02:08'
updated_date: '2026-05-25 02:11'
labels:
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Make purity internally consistent and match the OO mental model: modifying your own object is fine; reaching outside it is impure. Today self.items.append() is pure-local (receiver-kind policy) but self.x = y is flagged impure, because attribute_writes flags every attribute write regardless of receiver. Fix: attribute_writes reports the receiver kind (like attribute_method_calls/module_calls), and the purity layer flags an attribute write as impure only when the receiver is a parameter (caller-visible) or an imported module (global state); self/cls and local variables are pure-local. This is the external-side-effects notion of purity the receiver-kind policy already implements.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 AttributeWrite carries the receiver kind; attribute_writes classifies the receiver (self/cls, parameter, variable, import, unknown)
- [x] #2 Purity flags an attribute write as impure only for parameter or import receivers; self/cls and local-variable attribute writes are pure-local
- [x] #3 self.x = y and self.items.append() are both pure; param.x = y stays impure; existing purity tests updated to the new semantics with a param-write impurity test added
- [x] #4 Full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
writes.py: AttributeWrite += receiver_kind; classify via _classify_receiver. purity.py: _filtered_attribute_writes yields only PARAMETER/IMPORT receivers; use it in _iter_observations. Update self-attr-write tests to pure; add param-write impure test. suite+check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
writes.py: AttributeWrite gains receiver_kind; attribute_writes classifies via classify_receiver (renamed public in calls.py). purity.py: _filtered_attribute_writes yields only PARAMETER/IMPORT-receiver writes (self/cls/local are pure-local), matching the attribute-method-call policy. Updated the two tests that encoded the old self-write-is-impure behavior; added a param-attr-write impurity test. Verified: self.x=v PURE, self.items.append() PURE, other.x=v IMPURE. 442 tests pass; pypeeker check exits 0 (added docstring to the now-public classify_receiver).
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Make self-mutation pure: an instance method modifying its own object is pure-local; only mutations that escape the object (a parameter's attributes, or an imported module's) are impure. This removes an inconsistency where self.items.append() was already pure-local but self.x = y was flagged impure. attribute_writes now reports the receiver kind (classify_receiver, made public), and the purity layer flags an attribute write only for parameter/import receivers - mirroring the existing attribute-method-call policy. Plain-language rule: changing your own object is fine; reaching outside it is impure. 442 tests pass; pypeeker check exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
