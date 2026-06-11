---
id: TASK-98
title: 'check: baseline/ratchet engine'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 19:13'
labels:
  - check
  - m6-ratchets
dependencies: []
priority: medium
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Adopting any rule on a legacy codebase requires 'no NEW violations': record a baseline (violation identity robust to line drift — rule + symbol/file anchor), report only deltas, update baseline explicitly. Hash-aware index makes incremental baselining natural.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 check --baseline write/compare workflow: baseline file records current violations; subsequent runs fail only on new ones; --update-baseline refreshes
- [x] #2 Violation identity survives unrelated edits (line shifts) via symbol/file anchoring; removals shrink the baseline on update
- [x] #3 Tests cover new-violation detection, line-drift stability, and baseline update
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read current check CLI + engine + Violation model
2. Implement src/pypeeker/check/baseline.py: line-independent identity (rule, file_path, normalized message with "(line N)" stripped), counted identities, sorted JSON under {"violations": {...}} at .semantic-tool/check-baseline.json; write_baseline/load_baseline/delta API
3. Add --baseline / --update-baseline flags to the check command in cli.py (mutually exclusive; default behavior unchanged)
4. tests/test_baseline.py: round-trip, line-drift stability, new-violation detection, duplicate counts, shrink-on-update, CLI flows via CliRunner with require-docstrings
5. uv run pytest -q + ruff on touched files
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Read current cli.py (check has only --no-refresh; no --strict), Violation model, engine, require-docstrings rule
- Confirmed messages embed "(line N)" fragments only in purity-related rule summaries; regex strip is sufficient

- Implemented src/pypeeker/check/baseline.py: identity = "rule::file_path::normalized_message" (regex-strips volatile "(line N)" fragments); counted identities (identity -> count) so duplicates and line drift are both handled; JSON stored at .semantic-tool/check-baseline.json under a {"violations": {...}} namespace (sorted, indented, preserves sibling namespaces for TASK-99)
- API: baseline_path/violation_identity/load_baseline/write_baseline/delta; delta attributes over-count surplus to LAST occurrences in (file,line) order for determinism; fixed_identities lists identities whose count dropped
- cli.py check: added --baseline (print+fail only on new, one-line "N baselined, M new, K fixed" summary) and --update-baseline (rewrite namespace, shrinks on fixes, exit 0); combination is a click UsageError; default behavior unchanged; --no-refresh untouched
- tests/test_baseline.py: 20 tests — identity, round-trip, stable/sorted/namespaced JSON, line-drift, duplicate budgets, surplus determinism, shrink-on-update, and CLI ratchet flows via CliRunner with require-docstrings
- uv run pytest -q: 921 passed; ruff clean on touched files; repo self-lint (pypeeker check) exits 0
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added a baseline/ratchet engine to pypeeker check so rules can be adopted on legacy code with "no NEW violations" semantics.

Changes:
- New src/pypeeker/check/baseline.py: line-independent violation identity (rule + file_path + message with volatile "(line N)" fragments stripped), stored as counted identities so duplicate identical violations are budgeted rather than position-matched — pure line drift never re-fires, a genuinely new duplicate does. Documented tradeoff: a violation that moves AND changes message reads as fixed+new (acceptable ratchet semantics).
- Baseline file .semantic-tool/check-baseline.json is sorted/indented JSON under a {"violations": {...}} namespace; writers preserve sibling top-level namespaces so the born-private ratchet (TASK-99) can share the file.
- API: write_baseline / load_baseline / delta(violations, baseline) -> (new, fixed_identities); over-count surplus deterministically attributed to the last occurrences in (file, line) order; missing baseline file loads as empty.
- cli.py check: new --baseline flag (prints only NEW violations, one-line "N baselined, M new, K fixed" summary, exit 1 iff new) and --update-baseline (full run, rewrite baseline — shrinking on fixes — print summary, exit 0). Both together is a UsageError. Default check behavior and --no-refresh unchanged.

Tests:
- tests/test_baseline.py (20 tests): identity scheme, JSON round-trip/stability/namespacing, line-drift stability, new-violation detection, duplicate-count semantics, surplus determinism, shrink-on-update, and full CLI ratchet flows (CliRunner, require-docstrings in a tmp project, flag-conflict error).
- Full suite: 921 passed; ruff clean on touched files; repo self-lint (pypeeker check) still exits 0.
<!-- SECTION:FINAL_SUMMARY:END -->
