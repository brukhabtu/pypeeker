---
id: TASK-143
title: 'envelope: core library and envl CLI'
status: To Do
assignee: []
created_date: '2026-08-02 16:54'
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
- [ ] #1 A sibling top-level package under src/ contains the envelope library and imports nothing from pypeeker
- [ ] #2 Envelope output is always valid JSON, including when the payload is truncated; truncation is structural rather than string slicing
- [ ] #3 Format adapters cover json, diff, pytest, and plain text, each emitting drill-in recipes appropriate to that format
- [ ] #4 Full output is cached as a content-addressed blob outside the repo, with an append-only manifest recording command, cwd, exit code, size, and capture time
- [ ] #5 A YAML config controls the enable switch, size threshold, cache location and limits, and the command registry with per-command format and truncation settings
- [ ] #6 Output below the size threshold passes through untouched
- [ ] #7 An envl CLI runs a command, captures and caches it, and emits the envelope
- [ ] #8 Unit tests run against the fixture corpus; full gate green
<!-- AC:END -->
