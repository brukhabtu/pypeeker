---
id: TASK-101
title: 'Binder: record WRITE facts for subscript stores on attribute chains'
status: To Do
assignee: []
created_date: '2026-06-11 18:44'
labels:
  - binder
  - analysis
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
x[i] = v on a bare name records a WRITE on the root, but obj.attr[k] = v (e.g. os.environ['X'] = v) emits only READ refs — no mutation fact exists, so no-hidden-global-mutation and purity analysis cannot see environment/attribute-container mutations. Extend the binder's subscript-mutation handling to roots that are attribute chains, recording a WRITE with receiver metadata.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Subscript assignment whose root is an attribute chain records a WRITE reference with receiver_root/chain metadata
- [ ] #2 os.environ['X'] = v is visible to no-hidden-global-mutation and purity attribute-write facts
- [ ] #3 Existing binder tests unaffected; new tests cover attr-chain subscript stores incl. augmented assignment
<!-- AC:END -->
