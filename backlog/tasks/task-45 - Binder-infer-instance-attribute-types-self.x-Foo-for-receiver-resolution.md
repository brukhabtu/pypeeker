---
id: TASK-45
title: >-
  Binder: infer instance-attribute types (self.x = Foo()) for receiver
  resolution
status: In Progress
assignee:
  - '@claude'
created_date: '2026-05-24 12:29'
updated_date: '2026-05-24 12:29'
labels:
  - binder
  - analysis
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Reliability improvement (the dominant OO pattern). self.x = Foo() in __init__ created no symbol, so self.x.method() and obj.x.method() could not resolve - the field had no member or type. The binder now declares self.<name> (and cls.<name>) assignments as members of the enclosing class, with a declared type (self.x: T = ...) or constructor-inferred type (self.x = Foo()). Deduped: the first/most-specific declaration wins; class-level fields take precedence. This fixed the find_importers/import_crosses_barrel dead-code false positives (self._engine.find_importers()).

Interaction fixed: making self.x writes resolve to the new member shifted them from attribute_writes (keyed on <unresolved>.) to outer_scope_writes, changing the purity observation type. Unified: attribute writes are now identified by is_attribute_access (resolved or not) and excluded from outer_scope_writes, so the purity verdict and AttributeWrite classification are preserved.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 self.<name> = ... (and cls.) inside a method declares <name> as a member of the enclosing class, with declared or constructor-inferred type; deduped so the first/class-level declaration wins
- [ ] #2 self.field.method() and obj.field.method() resolve through the field's inferred/declared type (query-only)
- [ ] #3 attribute_writes detects writes by is_attribute_access (resolved or unresolved); outer_scope_writes excludes attribute writes; purity verdicts unchanged
- [ ] #4 Reliability: find_importers/import_crosses_barrel resolve; the seeded dead-code OTHER bucket drops 13->3; full suite green; pypeeker check exits 0
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
assignments.py: _self_attribute_target + declare_instance_attribute (declare self.X under enclosing class, dedup via class_entry.lookup_local, constructor/declared type). writes.py: attribute_writes by is_attribute_access; outer_scope_writes excludes attribute writes; _leaf_name helper. Tests: instance-attr member created; self.field.method resolves; purity self-attr-write still impure. suite+check.
<!-- SECTION:PLAN:END -->
