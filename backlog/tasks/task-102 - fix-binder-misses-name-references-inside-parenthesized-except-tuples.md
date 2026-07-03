---
id: TASK-102
title: 'fix: binder misses name references inside parenthesized except tuples'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 22:15'
updated_date: '2026-07-03 03:32'
labels:
  - fix
  - binder
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found by the TASK-97 privatize dogfood: references in 'except (A, B) as e:' clauses are not recorded by the binder, while bare 'except A as e:' references are. Consequences observed: over-exposed-module-symbol falsely flagged pypeeker.refactor.extract:ExtractVariableError (its only cross-module uses sit in except tuples in batch.py and cli.py), and the rename engine rewrote the definition and imports but left the except-tuple use sites untouched, producing a NameError at runtime. A pyproject allow entry for that symbol documents the workaround; remove it when fixing this.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 References inside parenthesized except tuples are indexed like bare except references
- [ ] #2 Renaming a symbol used in an except tuple rewrites the use site
- [ ] #3 The pyproject over-exposed-module-symbol allow entry for ExtractVariableError is removed and the self-lint/privatize plan stays clean
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fixed the binder gap where names inside a parenthesized except tuple with 'as' were never referenced. visit_except_clause's as_pattern branch only visited a bare identifier exception type, so 'except (A, B) as e:' and 'except mod.Err as e:' recorded no references to A/B/mod (bare 'except A' and un-'as' tuples worked). Now it visits any exception-type expression (identifier, attribute, or tuple), not just the identifier case. Removed the now-obsolete [tool.pypeeker.over-exposed-module-symbol] allow workaround for ExtractVariableError in pyproject. Added binder regression tests for both the tuple-with-as and attribute-with-as forms. 1393 passed; ruff clean; self-check exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
