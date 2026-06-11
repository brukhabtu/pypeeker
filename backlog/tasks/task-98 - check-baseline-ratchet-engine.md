---
id: TASK-98
title: 'check: baseline/ratchet engine'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-06-11 18:28'
updated_date: '2026-06-11 19:07'
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
- [ ] #1 check --baseline write/compare workflow: baseline file records current violations; subsequent runs fail only on new ones; --update-baseline refreshes
- [ ] #2 Violation identity survives unrelated edits (line shifts) via symbol/file anchoring; removals shrink the baseline on update
- [ ] #3 Tests cover new-violation detection, line-drift stability, and baseline update
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read current check CLI + engine + Violation model
2. Implement src/pypeeker/check/baseline.py: line-independent identity (rule, file_path, normalized message with "(line N)" stripped), counted identities, sorted JSON under {"violations": {...}} at .semantic-tool/check-baseline.json; write_baseline/load_baseline/delta API
3. Add --baseline / --update-baseline flags to the check command in cli.py (mutually exclusive; default behavior unchanged)
4. tests/test_baseline.py: round-trip, line-drift stability, new-violation detection, duplicate counts, shrink-on-update, CLI flows via CliRunner with require-docstrings
5. uv run pytest -q + ruff on touched files
<!-- SECTION:PLAN:END -->
