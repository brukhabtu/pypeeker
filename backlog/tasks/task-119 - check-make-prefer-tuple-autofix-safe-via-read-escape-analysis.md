---
id: TASK-119
title: 'check: make prefer-tuple autofix safe via read-escape analysis'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 01:50'
updated_date: '2026-07-31 01:50'
labels:
  - check
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
check --fix applied prefer-tuple conversions that broke 52 tests: it tuplified lists that escape their scope (returned, passed to mutating callees like heapq.heappush, aliased). Add a Reference.escapes signal computed by the binder — False only in provably tuple-equivalent positions (iteration, membership, subscript-element read, truthiness) — and have prefer-tuple skip any list with an escaping read. Result: the autofix only converts genuinely-local lists and can no longer change behavior.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Reference gains an escapes flag the binder computes for reads: False only in tuple-equivalent positions (for/comprehension iterable, in/not-in membership, subscript-element read, if/while/assert/not truthiness); True everywhere else.
- [x] #2 prefer-tuple excludes any candidate with an escaping read (in addition to mutation), so its attached fix only converts genuinely-local lists.
- [x] #3 Running check --fix with prefer-tuple enabled on pypeeker breaks zero tests (previously 52); an integration test encodes that only local lists convert while returned and heappush-mutated lists are untouched.
- [x] #4 Comprehensive tests: 31 binder-level escape cases (escaping/local/mixed/serialization round-trip) plus 15 rule-level cases.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Root cause: prefer-tuple flagged never-locally-mutated lists without escape analysis (its docstring admitted this), so check --fix tuplified lists that (a) are passed to stdlib mutators like heapq.heappush, or (b) are returned/aliased and depended on as lists downstream. Neither is fixed by purity rules (stdlib is out of scope; return-contract is not a mutation).

Fix: binder computes Reference.escapes for each simple-name READ via _read_escapes(node), walking the immediate syntactic role (with paren unwrap). Only for/comprehension iterable, in/not-in membership right operand, subscript-value element read, and if/while/assert/not truthiness are non-escaping; default is True (also the missing-key deserialization default, so old indexes stay safe). prefer-tuple now excludes candidates with any escaping read.

Proof: enabling prefer-tuple + check --fix on pypeeker applied 5 safe conversions, 0 declined, and broke 0 tests (was 52). Full gate: 1444 pytest, ruff clean, self-lint exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Make the prefer-tuple autofix safe so check --fix can never change behavior.

Problem: prefer-tuple flagged any never-locally-mutated list, and check --fix tuplified it — but a list that escapes its scope cannot be safely tuplified. Enabling the fix on pypeeker broke 52 tests: lists passed to heapq.heappush (stdlib in-place mutation), and lists returned/aliased then used as lists. Purity rules do not cover this (stdlib is out of scope; a returned tuple-vs-list is a contract change, not a mutation).

Fix: add Reference.escapes, computed by the binder for each simple-name read. It is False only in positions where a tuple behaves identically and the value does not leave its local, read-only lifetime — the iterable of a for/comprehension, the right operand of in/not in, a subscript element read (x[i]), and pure truthiness (if/while/assert/not). Every other position (return, yield, call argument, alias, binary/compare, attribute access) is escaping. The field defaults to True, which is also the missing-key deserialization default, so older indexes never look falsely local. prefer-tuple now excludes any candidate with an escaping read in addition to the existing mutation checks; a flagged list is provably safe to tuplify.

Proof: with prefer-tuple enabled, check --fix on pypeeker applied 5 safe conversions and broke 0 tests (was 52). An integration test locks this in — a local list converts while a returned list and a heappush-mutated list are left untouched.

Tests: 46 new (31 binder-level escape classification incl. serialization round-trip and mixed uses; 15 rule-level). Full gate: 1444 pytest passed, ruff clean, self-lint exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
