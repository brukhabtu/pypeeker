---
id: TASK-16
title: Refine purity attribute denylist to reduce evidence noise
status: Done
assignee: []
created_date: '2026-05-01 23:29'
updated_date: '2026-05-02 00:04'
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
- [x] #1 Audit IO_METHOD_NAMES for names that are commonly overloaded in non-IO domains (bind, send, accept, listen — all socket-specific but hit anything that defines those methods)
- [x] #2 Remove 'bind' from IO_METHOD_NAMES (overloaded in many domains)
- [x] #3 Document remaining over-matchers in the denylist module docstring as known heuristic limitations
- [x] #4 Self-test on pypeeker src no longer reports 'bind' as evidence for _reindex_files (verdict stays IMPURE for the correct reason: read_bytes)
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Audited IO_METHOD_NAMES and removed names that over-match outside their stdlib I/O context: bind (binder.bind, click.bind), accept (visitor pattern), listen (event listeners), connect (signals/DB drivers), shutdown (executors), replace (str.replace is the dominant Python idiom). These cases are caught either by MODULE_IMPURE_NAMES when the receiver root resolves to an import, or will be caught by TASK-14 when receiver type info is available. Moved 'remove' from IO_METHOD_NAMES to COLLECTION_MUTATION_NAMES — list.remove() and set.remove() are pure-local mutations; os.remove and Path.unlink are still caught via MODULE_IMPURE_NAMES. Documented all over-matchers in the denylist module docstring as known heuristic limitations.

Added 4 regression tests in tests/test_purity.py::TestDenylistOverMatchRegressions covering: local str.replace -> pure, local custom-object .bind() -> pure, list.remove on local -> pure, list.remove on parameter -> impure (caller-visible mutation).

Self-test on pypeeker's own src still passes 10/10. Verdicts unchanged (functions previously flagged IMPURE remain IMPURE for correct reasons: read_bytes, write_text, mkdir, etc.); evidence noise is reduced (no more spurious 'bind', 'replace', 'remove' items pointing at non-I/O code paths). Full suite 236/236 passing.
<!-- SECTION:FINAL_SUMMARY:END -->
