---
id: TASK-84
title: 'cli: check --fix with non-conflicting application + first three autofixes'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:18'
labels:
  - cli
  - check
  - m2-fixes
dependencies:
  - TASK-82
  - TASK-83
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
MVP fix application without the composite planner: collect fixes from violations, drop overlapping/conflicting edit sets (byte-range overlap per file), apply the rest as one hash-verified transaction, report applied vs skipped. Prove on three easy fixes: prefer-tuple literal rewrite, unused-private-code deletion, unused-import removal.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 check --fix applies non-overlapping fixes in one transaction (preview via plan id; reuses apply/rollback machinery) and reports applied/skipped/declined
- [x] #2 prefer-tuple, an unused-private-symbol delete fix, and an unused-import removal fix ship and are exercised end-to-end
- [x] #3 Only confidence-certain fixes auto-apply; conflicting fixes are skipped deterministically; tests cover conflict skipping and rollback
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read fix protocol, transaction machinery, check engine/CLI, baseline helpers (done)
2. fixes.py: add index-anchored fixes PreferTupleFix (bracket-byte rewrite with conservative byte scanner), RemoveUnusedImportFix (line/name-token deletion), DeleteUnusedSymbolFix (scope-span deletion, decline decorated)
3. rules.py: attach PreferTupleFix in prefer-tuple; add also-private option to unused-public-symbol attaching DeleteUnusedSymbolFix to private findings only
4. NEW check/builtin/unused_imports.py: file-scoped unused-imports rule (skip __init__.py, __future__, _names) with RemoveUnusedImportFix attached
5. cli.py: check --fix (DECLARED-confidence gate, plan fixes, deterministic overlap drop, one check-fix transaction via TransactionStore + TransactionApplier, re-run check, JSON report); refuse --fix with baseline flags; wire symbol-namespace reseed into --update-baseline (clear symbols namespace so enabled born-private run re-seeds via write_symbol_baseline)
6. check/__init__.py exports; baseline.py gains clear_symbol_baseline helper
7. tests/test_check_fix.py: end-to-end per fix, decline paths, conflict determinism, rollback, confidence gate, flag conflicts, update-baseline symbols refresh
8. uv run pytest -q + ruff check
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added three index-anchored fixes to check/fixes.py: PreferTupleFix (byte-level bracket scanner, no refactor/adapter imports per layering; single-element lists close with ",)"), RemoveUnusedImportFix (whole-line or name-entry deletion), DeleteUnusedSymbolFix (scope-span deletion eating trailing blank lines). All verify index freshness (hash) and re-locate via the current index; STALE_INDEX/TEXT_MISMATCH/AMBIGUOUS declines on anything unsafe.
- rules.py: prefer-tuple attaches its fix; unused-public-symbol gained also-private option (deletion fix attaches ONLY to protected/private findings).
- NEW builtin rule unused-imports (verified no pre-existing rule via grep); skips __init__.py, __all__ files, __future__, _names; HEURISTIC confidence under dynamic access.
- cli.py: check --fix with DECLARED-confidence gate, deterministic (file,start,fix_id) conflict drop, one check-fix transaction via TransactionStore+TransactionApplier, re-run + residual report, JSON output; UsageError with baseline flags.
- TASK-99 follow-up wired: --update-baseline clears the symbols namespace (new baseline.clear_symbol_baseline) so an enabled born-private run re-seeds it with the current public surface.
- tests/test_check_fix.py: 31 tests; full suite 1179 passed; ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Shipped check --fix with non-conflicting one-transaction application plus the first three autofixes (prefer-tuple rewrite, unused-import removal, unused-private-symbol deletion), and wired the TASK-99 --update-baseline symbol-namespace follow-up.

Changes:
- check/fixes.py: three new index-anchored Fix implementations sharing one anchoring strategy — plan() re-reads the file, refuses on index-hash mismatch (STALE_INDEX), re-locates the target from the current index, and text-verifies before emitting fresh-hash EditEntries. PreferTupleFix replaces only the two bracket bytes via a conservative byte scanner (handles nested brackets, comments, plain/raw/byte strings; declines AMBIGUOUS on f-strings, triple quotes, unterminated strings; single-element lists become "(x,)"). RemoveUnusedImportFix deletes the whole line for single-name imports and just the name entry + adjacent comma on multi-name lines (declines parenthesized/continued lists). DeleteUnusedSymbolFix deletes the def/class scope span plus trailing blank lines, anchored on the header text (declines decorated symbols and trailing same-line code).
- check/rules.py: prefer-tuple violations now carry PreferTupleFix; unused-public-symbol gained an also-private option (default false) that additionally reports unreferenced protected/private module-level symbols — the deletion fix attaches ONLY to those (pruning public API stays human-decided). Public-finding messages are byte-identical to before.
- NEW check/builtin/unused_imports.py: file-scoped unused-imports rule (no prior rule existed); skips __init__.py barrels, files binding __all__, __future__ imports, and underscore-prefixed bindings; findings in getattr/globals/vars/locals files carry HEURISTIC confidence so they are never auto-fixed.
- cli.py: check --fix plans every fix on a DECLARED-confidence violation, drops overlapping fixes deterministically (sorted by file/offset/fix_id, first wins, one fix per file region across rules), writes ONE "check-fix" transaction and applies it via TransactionApplier (tx remains for rollback/transactions show), re-runs check, and prints JSON {applied, skipped_conflicts, declined, residual_violations, tx_id}; exits non-zero when violations remain. Refuses combination with --baseline/--update-baseline (UsageError). --update-baseline now also re-records the born-private accepted-public set: it clears the symbols namespace (new baseline.clear_symbol_baseline, preserving sibling namespaces) so the enabled rule re-seeds it via write_symbol_baseline.
- check/__init__.py exports the new fixes; stale "follow-up pending" docstrings in born_private.py/baseline.py updated.

Tests:
- NEW tests/test_check_fix.py (31 tests): each fix end-to-end with post-apply file content asserted; decline paths (f-string bracket scan, decorated symbol, file mutated between detect and plan, parenthesized imports, missing file); deterministic conflict skipping (delete beats nested tuple rewrite); rollback of a check-fix transaction restores exact bytes; HEURISTIC fixes excluded from --fix; flag-conflict errors; --update-baseline seeds/refreshes the symbols namespace with born-private and preserves it without.
- uv run pytest -q: 1179 passed; ruff clean.

Risks/limits (deliberate conservatism, all decline rather than guess): bracket scanner refuses f-strings/triple-quoted strings; import fix refuses parenthesized/multi-line lists and entries it cannot match 1:1; symbol deletion refuses decorated defs.
<!-- SECTION:FINAL_SUMMARY:END -->
