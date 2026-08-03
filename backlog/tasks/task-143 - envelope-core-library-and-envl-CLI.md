---
id: TASK-143
title: 'envelope: core library and envl CLI'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 16:54'
updated_date: '2026-08-03 13:22'
labels: []
dependencies:
  - TASK-142
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The extractable envelope package. A sibling top-level package under src/ with zero imports from pypeeker, so lifting it into another project is a move rather than a rewrite. Wraps any command output in a valid-JSON envelope that truncates the payload structurally, caches the full output as an immutable content-addressed blob outside the repo, and carries the metadata a model needs to drill in: shape, counts, truncation markers, blob path, and format-appropriate recipes. Format awareness matters because jq only helps for JSON: diffs need per-file stats and path-scoped recipes, text needs line ranges and grep. Library and CLI land together because the CLI is the library public API consumer that keeps unused-public-symbol satisfied, and because a library nothing calls cannot be verified.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A sibling top-level package under src/ contains the envelope library and imports nothing from pypeeker
- [x] #2 Envelope output is always valid JSON, including when the payload is truncated; truncation is structural rather than string slicing
- [x] #3 Format adapters cover json, diff, pytest, and plain text, each emitting drill-in recipes appropriate to that format
- [x] #4 Full output is cached as a content-addressed blob outside the repo, with an append-only manifest recording command, cwd, exit code, size, and capture time
- [x] #5 A YAML config controls the enable switch, size threshold, cache location and limits, and the command registry with per-command format and truncation settings
- [x] #6 Output below the size threshold passes through untouched
- [x] #7 An envl CLI runs a command, captures and caches it, and emits the envelope
- [x] #8 Unit tests run against the fixture corpus; full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-envelope, bundled with TASK-142 and TASK-144.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
src/envl: sibling top-level package, zero pypeeker imports, extractable by move. Format adapters (json/diff/pytest/text), structural truncation always emitting valid JSON, content-addressed blob cache outside the repo, YAML config with threshold and command registry, envl -- <command> CLI with entry point. Mechanism verified: 50.9 percent reduction across 2442 replayed above-threshold results, every one valid JSON, none exceeding the envelope ceiling.
<!-- SECTION:FINAL_SUMMARY:END -->
