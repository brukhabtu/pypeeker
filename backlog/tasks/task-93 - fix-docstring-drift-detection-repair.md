---
id: TASK-93
title: 'fix: docstring-drift detection + repair'
status: Done
assignee:
  - '@claude'
created_date: '2026-06-11 18:27'
updated_date: '2026-06-11 21:47'
labels:
  - fix
  - m4-program-fixes
dependencies:
  - TASK-84
  - TASK-89
priority: low
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Documented params vs actual signature drift (darglint is dead, ruff coverage shallow). Detect param-name mismatches in google/numpy/sphinx styles from symbols+docstrings; fix renames or flags stale entries. Also: rename plans update :func:/:class: docstring cross-references to renamed symbols.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Rule flags docstring params absent from the signature and signature params absent from a params section, per configurable style
- [x] #2 Fix rewrites renameable drift (param renamed -> docstring follows); ambiguous drift is report-only
- [x] #3 Rename planner optionally updates docstring cross-references to the renamed symbol (flag-gated); tests for both halves
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. New builtin rule module src/pypeeker/check/builtin/docstring_drift.py: file-scope, opt-in rule "docstring-drift" parsing google/numpy/sphinx param sections (autodetect by earliest marker, forceable via style option); flags documented-but-absent params always, present-but-undocumented gated by require-complete (only when a section exists); allow option fnmatch on symbol_id.
2. DocstringParamRenameFix in the same module (Fix protocol from pypeeker.check.fixes): attached only when exactly one ghost + exactly one undocumented param; plan() re-reads file+index with STALE_INDEX discipline, re-derives the drift, locates the docstring inside the scope span, requires a unique name-token occurrence, emits one REPLACE EditEntry.
3. RenamePlanner.plan gains update_docstrings: bool = False; when set, regex-scan affected files plus indexed files whose symbol docstrings mention the old name for :func:/:class:/:meth: role forms (optional dotted qualification, optional ~) and append text-verified REPLACE edits on the name token. Precondition set untouched.
4. Tests: tests/test_rule_docstring_drift.py (parsers per style, stars normalization, style forcing, require-complete gating, allow, fix end-to-end via check --fix, decline paths) + append-only planner tests in tests/test_planner.py (flag on rewrites cross-file role refs, skips plain-text mentions; flag off untouched).
5. uv run pytest -q, ruff check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added src/pypeeker/check/builtin/docstring_drift.py: file-scope opt-in rule docstring-drift with google/numpy/sphinx params-section parsers (autodetect = earliest marker, style option forces), stars/backslash normalization, leading self/cls skipped; documented-but-absent always flagged, present-but-undocumented gated by require-complete and only when a section exists; allow fnmatch option.
- DocstringParamRenameFix lives in the same module (check/fixes.py untouched): attached only for the 1-ghost/1-undocumented rename shape; plan() uses the STALE_INDEX discipline via the shared _current_state helper, re-derives the drift from the current index, anchors the docstring text uniquely inside the scope span, requires a unique name-token occurrence, emits one REPLACE edit; all plural/changed cases decline AMBIGUOUS/TEXT_MISMATCH.
- RenamePlanner.plan gained update_docstrings (default False): regex over :func:/:class:/:meth: role forms (optional ~, optional dotted qualification ending in .old) across affected files + indexed files whose symbol docstrings mention the old name; every hit text-verified against current bytes with fresh hashes; precondition set untouched.
- Tests: tests/test_rule_docstring_drift.py (29 cases incl. check --fix CLI e2e) + 3 appended planner tests; full suite 1329 passed, ruff clean.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added docstring-drift detection and conservative repair (param-name drift only), plus flag-gated rename-time docstring cross-reference updates.

Changes:
- NEW src/pypeeker/check/builtin/docstring_drift.py — opt-in file-scope rule `docstring-drift`. Parses documented parameter names from google ("Args:" with `name: desc` / `name (type): desc`), numpy ("Parameters" + dashed underline, `name`/`name : type` at the margin), and sphinx (`:param name:` / `:param type name:`) sections; style autodetected per docstring (earliest marker wins) or forced via the `style` option. Documented `*args`/`**kwargs` normalize to bare names; a leading self/cls is skipped on the signature side. Flags documented-but-absent params always; present-but-undocumented only behind `require-complete` (default false) and only when a section already exists (no-section cases stay require-docstrings' turf). `allow` fnmatch patterns exempt symbols.
- DocstringParamRenameFix (same module; check/fixes.py untouched) — attached only to the unambiguous rename shape (exactly one stale documented name + exactly one undocumented signature param). plan() follows the index-anchored fix discipline: hash-verified index (STALE_INDEX), drift re-derived from current state, docstring re-anchored uniquely inside the scope span, single name-token occurrence required; one REPLACE edit on the token. Zero/plural undocumented params, repeated tokens, or style mismatch decline (report-only).
- src/pypeeker/refactor/planner.py — RenamePlanner.plan gains `update_docstrings: bool = False`. When set, rewrites Sphinx-role docstring cross-references (`:func:`/`:class:`/`:meth:`, optional `~`, optional dotted path ending in the old name) by scanning affected files plus indexed files whose symbol docstrings mention the old name; every hit is text-verified against current bytes with fresh hashes, so even index-stale candidates apply safely. Plain-text mentions are never touched; the enumerable precondition set is unchanged.

Tests:
- NEW tests/test_rule_docstring_drift.py — parser units per style (incl. numpy next-header edge, stars, first-marker autodetect, forcing), rule gating (require-complete, allow, self/cls), fix attachment rules, repair end-to-end (google + sphinx, content asserted), decline paths (ambiguous token, stale index, drift-shape/style mismatch), and check --fix CLI end-to-end.
- tests/test_planner.py — appended: flag on rewrites role refs in another file (incl. a docstring-only nominee) and skips prose mentions / near-miss names; flag off leaves docstrings untouched; precondition-set test stays identical.

Known limits (documented in the module): numpy multi-name entries (`x, y : int`) are not parsed as params; role forms in comments also match the planner scan (accepted, flag-gated); type drift and returns/raises sections are out of scope.

Verification: uv run pytest -q — 1329 passed; ruff clean.
<!-- SECTION:FINAL_SUMMARY:END -->
