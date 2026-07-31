---
id: TASK-120
title: 'check: drop ruff-covered rules from the self-lint gate'
status: Done
assignee:
  - '@claude'
created_date: '2026-07-31 03:35'
updated_date: '2026-07-31 03:36'
labels:
  - check
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
unused-imports (F401), star-imports (F403/F405), naming-conventions (N8xx), and require-docstrings (D101-103) duplicate checks ruff already runs in CI and the pre-commit hook. Remove them from [tool.pypeeker].rules to stop double-linting, and enable the matching ruff selectors (N + D101-103, scoped to src) so coverage is preserved. The rules stay available as builtins for consumer projects.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 unused-imports, star-imports, naming-conventions, require-docstrings are removed from [tool.pypeeker].rules (gate is now 11 rules); the rules remain registered and available to consumers.
- [x] #2 ruff config is extended to preserve coverage: N (pep8-naming, N818 ignored to match prior scope) and D101/D102/D103 (public docstring presence, src only) added to select; F already covered unused/star. ruff check src tests passes clean.
- [x] #3 Docs updated (CLAUDE.md, architecture.md) with a 'covered by ruff' rule group; membership tests flipped to assert the rules are not gated.
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Verified ruff currently ran only E/F/W, so F401/F403/F405 (unused/star) were already caught but N and D were NOT — removing naming/docstrings without adding ruff N/D would have silently dropped coverage. Added N + D101-103. ruff N surfaced 2 N818 findings (BatchAborted, _SpliceMismatch missing Error suffix) that pypeeker naming-conventions never checked; ignored N818 to keep scope parity and avoid a public exception rename (noted as a separate opt-in improvement). D101-103 scoped to src via a tests/** per-file-ignore, matching require-docstrings scope. Also updated the stale prefer-tuple row in architecture.md (its autofix is now safe post-#76). Gate: 1444 pytest, ruff clean, self-lint 11 rules exit 0.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Stop double-linting: drop the four rules ruff already covers from pypeeker self-lint gate, and wire ruff to preserve their coverage.

Removed from [tool.pypeeker].rules (now 11 rules): unused-imports, star-imports, naming-conventions, require-docstrings. These duplicate ruff checks that already run in CI and the pre-commit hook, so gating them in pypeeker too was pure redundancy. The rules stay registered and available for consumer projects that do not run ruff.

Coverage preserved by extending ruff: F already caught unused-imports (F401) and star-imports (F403/F405); added N (pep8-naming) and D101/D102/D103 (public class/method/function docstring presence, src only via a tests glob). ruff N surfaced two N818 exception-suffix findings pypeeker naming-conventions never checked; N818 is ignored to keep scope parity and avoid a breaking public rename (left as a separate opt-in improvement).

The gated set is now exactly the rules ruff and mypy cannot express — cross-module resolution, purity, visibility, boundary and cycle analysis — which is pypeeker reason to exist.

Docs: CLAUDE.md and architecture.md gain a covered-by-ruff rule group; the stale prefer-tuple row is corrected (its autofix is now safe). Membership tests flipped to assert the four are not gated. Gate: 1444 pytest passed, ruff clean, self-lint (11 rules) exit 0.
<!-- SECTION:FINAL_SUMMARY:END -->
