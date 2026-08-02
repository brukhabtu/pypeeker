---
id: TASK-135
title: >-
  Adopt insights-report suggestions: docs conventions, audit skill, verify
  script, hook fixup, CI docs audit
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-02 00:26'
updated_date: '2026-08-02 00:27'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The /insights usage report surfaced concrete friction: a stop-hook false positive on GitHub squash-merge commits, doc claims verified ad hoc instead of via a standing procedure, and no canonical one-shot verification command. Adopt all its suggestions as repo conventions and tooling so future sessions start from accurate context.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CLAUDE.md documents the git/PR squash-merge convention (GitHub-authored merge commits are expected and Verified; never amend them; reset branch onto origin/main after merge)
- [ ] #2 CLAUDE.md documents a doc-maintenance standard and the workflow-versioning retro loop, with /insights reports named as a standing retro input
- [ ] #3 A repo skill .claude/skills/audit-claude-md/SKILL.md exists that verifies every factual CLAUDE.md claim against the repo
- [ ] #4 scripts/verify-repo.sh runs the full gate (pytest, ruff, self-lint) with a compact per-step summary and is referenced in CLAUDE.md Commands
- [ ] #5 The SessionStart hook idempotently patches the environment stop hook so GitHub squash-merge commits (committer noreply@github.com) are not flagged Unverified
- [ ] #6 A docs-audit GitHub Actions workflow exists (manual dispatch plus schedule) that runs a headless report-only doc audit and skips cleanly when no API key secret is configured
- [ ] #7 Full gate green: pytest, ruff, and pypeeker self-lint all pass
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Launch task-pipeline v3 (mode full) on branch claude/insights-adoption with a probe-grounded spec covering: CLAUDE.md conventions (git/PR squash-merge, doc maintenance, versioning retro loop with /insights as retro input), the audit-claude-md skill, scripts/verify-repo.sh, the session-start.sh idempotent stop-hook fixup, and the docs-audit CI workflow.
2. Orchestrator (outside pipeline): patch the live ~/.claude/stop-hook-git-check.sh copy, verify the gate independently, do backlog bookkeeping, commit and push.
<!-- SECTION:PLAN:END -->
