---
id: DRAFT-2
title: 'Gap A part 3: constructor inference, multi-hop chains, confidence-gated rename'
status: Draft
assignee: []
created_date: '2026-05-24 02:09'
labels:
  - analysis
  - index
dependencies: []
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Deferred from TASK-37 (annotated instance-receiver resolution, query-only). Remaining instance-receiver discovery gaps:

1. Constructor-assignment inference: x = Foo(); x.bar() -> infer x: Foo from the constructor call (no annotation needed). Needs simple assignment-type tracking in the resolver/analysis layer.
2. Multi-hop receiver chains: a.b.c and self.store.save() -> resolve intermediate attribute types (e.g. the declared type of the field self.store) rather than only single-hop receivers.
3. Confidence-gated rename: let plan-rename optionally use resolved instance-method usages, but only for high-confidence (DECLARED-annotation) receivers, never inferred ones, since annotation-based receiver resolution is best-effort and rename mutates code. Today rename stays on its exact-binding rule and ignores resolve_reference.

Motivation: after TASK-36/37 the dogfood dead-method candidate count is ~86 (from 94); the residual is dominated by self.attr-chained calls (multi-hop), constructor-typed locals, CLI Click callbacks, and test-only public API. (1) and (2) would close most of the real-code residual.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Constructor-assignment inference resolves x = Foo(); x.method() to Foo.method (query-only)
- [ ] #2 Multi-hop chains resolve via intermediate attribute/field types, including self.field.method()
- [ ] #3 plan-rename optionally cascades to resolved instance-method usages gated on DECLARED-confidence receivers only; default behavior unchanged
- [ ] #4 Full suite green; pypeeker check exits 0
<!-- AC:END -->
