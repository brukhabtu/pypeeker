---
id: TASK-12
title: 'Binder: preserve receiver root + chain on attribute access refs'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-01 22:26'
updated_date: '2026-05-01 22:30'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add two fields to Reference and populate them in the binder so attribute calls keep the structural information needed for principled purity analysis. Without this every os.system / random.random / pathlib call collapses to <unresolved>.<method> and we can't distinguish module access from method-on-local.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reference model gains receiver_root_symbol_id: str | None — the resolved symbol_id of the leftmost name in the receiver chain, None if the chain is dynamic
- [x] #2 Reference model gains receiver_chain: list[str] | None — names from root to but excluding the leaf (e.g. ['os','path'] for os.path.join)
- [x] #3 Binder populates both fields on every attribute-access reference (READ/CALL with is_attribute_access=True)
- [x] #4 Dynamic receivers (f().bar, lst[0].bar) leave both fields None
- [x] #5 Binder tests verify all three cases: import-rooted (os.system), local-rooted (path.write_text), dynamic (f().bar)
- [x] #6 All 205 existing tests still pass (additive change)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added two new fields to Reference: receiver_root_symbol_id (the resolved symbol_id of the leftmost name in an attribute receiver chain) and receiver_chain (list of names from root to second-to-last, e.g. ['os','path'] for os.path.join). Both fields are populated by a new _receiver_metadata helper in the binder that walks left from an attribute node, accumulating attribute names through nested 'attribute' AST nodes until it hits an identifier (resolves the root) or a dynamic node like a call/subscript (returns None,None for both fields). Wiring covers _visit_attribute_call, _visit_attribute, and _resolve_self_attribute paths so resolved self.x and unresolved obj.x cases all carry the metadata. Added 12 tests in tests/test_binder_receiver_chain.py covering import-rooted (os.system, os.path.join), local-rooted (path.write_text on parameter, p.write_text on local variable), dynamic (f().bar, lst[0].method), unresolved-root (unknown.bar leaves root_id=None but keeps chain=['unknown']), self.method, and parametrized smoke tests for time/random/os.path. Full suite 217/217 passing. Re-indexed pypeeker's own src/ to populate the new fields on real data.
<!-- SECTION:FINAL_SUMMARY:END -->
