---
id: TASK-142
title: 'envelope: fixture corpus extracted from pipeline transcripts'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-02 16:54'
updated_date: '2026-08-03 13:22'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The subagent transcripts hold 9307 real tool outputs totalling 6403k approximate tokens (see .claude/workflows/TOKEN-COSTS.md). They live outside the repo in an ephemeral container and are lost when it is reclaimed. Extract a sampled, size-capped corpus into the repo as test fixtures so the envelope can be developed and measured against real output shapes rather than invented ones, and so the 2026-08-02 baseline stays reproducible.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A sampled fixture corpus lives under tests/fixtures/envelope/ covering each format family: json, diff, pytest, plain text, and search output
- [x] #2 A manifest records for each fixture its originating command family, detected format, and original size
- [x] #3 Total committed fixture size is capped and documented; the sampler favours the largest and most representative outputs per family
- [x] #4 An extractor script can regenerate or extend the corpus from a transcript directory
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Task-pipeline v3 in worktree /home/user/pypeeker-envelope, bundled with TASK-143 and TASK-144 as one build-and-prove arc.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
43-fixture corpus under tests/fixtures/envelope with manifest, extracted from pipeline transcripts before the container reclaims them; scripts/extract-envelope-fixtures.py regenerates or extends it. Note the corpus is sampled largest-first, so its reduction ratios run below population ratios — it serves as the adapter regression corpus, not as a projection basis (see TASK-144).
<!-- SECTION:FINAL_SUMMARY:END -->
