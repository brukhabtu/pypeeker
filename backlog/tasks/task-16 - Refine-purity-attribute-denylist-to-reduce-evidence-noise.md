---
id: TASK-16
title: Refine purity attribute denylist to reduce evidence noise
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
After TASK-13 some impure verdicts contain misleading evidence items because attribute-name matching is intrinsically ambiguous. Concrete examples surfaced in self-validation: TransactionApplier._reindex_files lists 'bind' as evidence (it is binder.bind(tree.root_node), not socket.bind). The verdict is correct but the evidence is noisy. Audit the denylist and remove names that almost never indicate I/O outside their stdlib context, or move them under TASK-14's type-aware matching once that lands.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Audit IO_METHOD_NAMES for names that are commonly overloaded in non-IO domains (bind, send, accept, listen — all socket-specific but hit anything that defines those methods)
- [ ] #2 Remove 'bind' from IO_METHOD_NAMES (overloaded in many domains)
- [ ] #3 Document remaining over-matchers in the denylist module docstring as known heuristic limitations
- [ ] #4 Self-test on pypeeker src no longer reports 'bind' as evidence for _reindex_files (verdict stays IMPURE for the correct reason: read_bytes)
<!-- AC:END -->
