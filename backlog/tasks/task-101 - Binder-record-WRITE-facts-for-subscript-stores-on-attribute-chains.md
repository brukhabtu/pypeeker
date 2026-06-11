---
id: TASK-101
title: 'Binder: record WRITE facts for subscript stores on attribute chains'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:44'
updated_date: '2026-06-11 18:56'
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
- [x] #1 Subscript assignment whose root is an attribute chain records a WRITE reference with receiver_root/chain metadata
- [x] #2 os.environ['X'] = v is visible to no-hidden-global-mutation and purity attribute-write facts
- [x] #3 Existing binder tests unaffected; new tests cover attr-chain subscript stores incl. augmented assignment
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add _subscript_root_node walker in binder/assignments.py that walks nested subscripts to the root value node
2. Extend _record_subscript_mutation: identifier root keeps existing behavior; attribute root delegates to new _record_attribute_subscript_mutation that mirrors visit_attribute (mark nodes in declaration_nodes, READ on identifier receiver root, resolve_self_attribute for self/cls, else <unresolved>.<leaf> WRITE with receiver_metadata)
3. Augmented assignment subscript branch reuses the same path (already calls _record_subscript_mutation)
4. New tests/test_binder_subscript_writes.py: attr-chain WRITE w/ receiver metadata, os.environ via analysis.writes.attribute_writes (IMPORT), self.cache (SELF), augmented, nested subscript, bare-name regression, no duplicate refs
5. Run full pytest suite
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Generalized _subscript_root_identifier into _subscript_root_node (walks nested subscripts, returns whatever anchors the chain).
- _record_subscript_mutation now branches: identifier root keeps the exact previous behavior; attribute root delegates to new _record_attribute_subscript_mutation.
- _record_attribute_subscript_mutation mirrors visit_attribute for the a.b = x write case: marks attribute + object-identifier nodes in declaration_nodes (no duplicate READs), emits READ on the receiver-root identifier, tries resolve_self_attribute with kind=WRITE for self/cls (receiver metadata attached via dataclasses.replace), otherwise emits <unresolved>.<leaf> WRITE with is_attribute_access=True and receiver_metadata root/chain. Non-identifier/non-attribute object nodes are visited normally; dynamic roots (g()[k] = v) record no fact, unchanged.
- Augmented assignment needed no extra wiring: its subscript branch already routes through _record_subscript_mutation.
- New tests/test_binder_subscript_writes.py: 13 tests covering binder-level shape, receiver metadata, deep chains, nested subscripts, augmented stores, dynamic roots, duplicate-ref guard, bare-name regression, and analysis-level visibility (os.environ -> IMPORT, self.cache -> SELF via attribute_writes).
- Full suite: 802 passed; ruff clean on both files.
- Note: no_hidden_global_mutation.py docstring still says os.environ['X'] is a known gap — now stale, but that file is owned by another work stream this wave; follow-up doc tweak needed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Binder now records WRITE facts for subscript stores whose root is an attribute chain, closing the mutation-analysis blind spot found while dogfooding (TASK-76): obj.attr[k] = v, os.environ['X'] = v, and self.cache[k] = v previously emitted only READ refs.

Changes (src/pypeeker/binder/assignments.py):
- _subscript_root_node walks nested subscripts to the anchoring node (identifier, attribute, or dynamic).
- Identifier roots keep the existing bare-name WRITE behavior verbatim.
- Attribute roots get a WRITE shaped exactly like the binder's other attribute writes: symbol_id <unresolved>.<leaf>, is_attribute_access=True, receiver_root_symbol_id + receiver_chain from receiver_metadata; self/cls receivers resolve through resolve_self_attribute to the class member. The receiver-root identifier still emits a READ (matching visit_attribute on a.b = x), and declaration_nodes marking prevents duplicate refs at the write site. Augmented stores (obj.attr[k] += v) flow through the same path; nested subscripts (obj.attr[i][j] = v) walk to the attribute root; dynamic roots (g()[k] = v) record no fact, as before.

Impact: analysis.writes.attribute_writes and classify_receiver consume the new facts with zero changes — os.environ['X'] = v now surfaces as an IMPORT-rooted attribute write (visible to no-hidden-global-mutation shape 3 and purity), self.cache[k] = v as SELF.

Tests: new tests/test_binder_subscript_writes.py (13 tests: binder shape + receiver metadata, deep/nested chains, augmented, dynamic-root and bare-name regression guards, duplicate-ref guard, analysis-level IMPORT/SELF visibility). Full suite 802 passed; ruff clean.

Follow-up: the \"known gap\" note in no_hidden_global_mutation.py's docstring is now stale (file owned by another work stream this wave).
<!-- SECTION:FINAL_SUMMARY:END -->
