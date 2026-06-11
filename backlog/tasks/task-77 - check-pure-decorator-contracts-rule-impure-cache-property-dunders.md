---
id: TASK-77
title: 'check: pure-decorator-contracts rule (impure @cache/@property/dunders)'
status: To Do
assignee: []
created_date: '2026-06-11 18:25'
labels:
  - check
  - analysis
  - m1-advisory
dependencies:
  - TASK-74
priority: high
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Memoizing or caching an impure function is a bug (@lru_cache on time/I-O); @property doing I/O and impure __eq__/__hash__/__repr__/__len__ violate implicit contracts. Symbols carry decorators and impurities() exists — compose them. Configurable decorator list and dunder list.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Rule flags functions with configured decorators (default cache/lru_cache/cached_property/property) whose impurities() is non-empty
- [ ] #2 Rule flags configured dunders (default eq/hash/repr/len/str) that are impure
- [ ] #3 Violation message names the impurity observations; tests cover decorated/dunder pure+impure; opt-in
<!-- AC:END -->
