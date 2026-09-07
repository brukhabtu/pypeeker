---
id: TASK-157
title: 'dsl: the flip — gate cutover, CLI rewiring, frozen-path deletion'
status: Done
assignee:
  - '@claude'
created_date: '2026-08-03 18:11'
updated_date: '2026-09-07 01:38'
labels: []
dependencies:
  - TASK-156
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Phase 5 of the DSL rewrite (dsl-rewrite.md is normative; test policy is migrate plus port — the program's one big-bang PR). Precondition: all 22 rules at differential parity. The self-lint gate switches to the new engine; CLI commands re-wire to named expressions; fix_id becomes purely derived; baseline identity re-keys to rule_id plus anchor_id; the frozen paths (src/pypeeker/check/**, app/check_fixes.py, app/privatize.py) are DELETED in this same PR along with the freeze guards (settings deny, bash hook, CI guard) and the differential harness; old-engine tests are ported scenario-by-scenario per the port policy; CLAUDE.md and architecture.md updated; dsl-rewrite.md flips to historical record. After this PR no package name carries a version scar.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The self-lint gate runs the new engine at zero findings with no baseline
- [x] #2 All old-engine test scenarios have a one-for-one home or a ledger entry
- [x] #3 Frozen paths, freeze guards, and the differential harness are deleted in the same PR
- [x] #4 CLAUDE.md and architecture.md describe the new architecture accurately
- [x] #5 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Full design, steps and risks: backlog/docs/doc-1 (TASK-157 flip plan), from scout+plan run wf_c3e650a9-136 on 2026-09-06. Pre-flip evidence re-verified: oracle PASS on 8 targets, 22/22 rules claimed, new engine at zero DECLARED/INFERRED findings on src.

Organizing constraint: the oracle drives the CLI for its old side, so CLI rewiring and oracle deletion must land in one tree state. Two phases.

PHASE A (additive; verify-repo.sh incl. oracle green after every step): A1 record final oracle output. A2 storage/baseline.py (identity -> rule::anchor_id; export resolve_storage_root). A3 Finding.anchor_id (compare=False). A4 analysis/star_imports.py shared trait. A5 dsl/config.py on pypeeker.project (dsl allow += project). A6 delete dsl/visibility.py storage-root/baseline copies. A7 register_dsl_rule overlay. A8 dsl born_private_surface() tested against the frozen seed. A9 app/fix_run.py single pass (app allow += dsl). A10 port the fixpoint (SIMULATION_UNSAFE_RULES -> mutation-declaring rules). A11 app/check_run2.py run service (plugins, loud unknown rule, sorted output). A12 app/privatize_run.py (raw visibility mapping; no apply_plan; public report). A13 refactor/privatize.py drops the three pointwise skips now on DEMOTE. A14 gate; oracle output identical to A1.

PHASE B (cutover in one motion; gate = pytest + ruff + self-lint): B1 rewire cli check/--fix, report keys byte-identical. B2 rewire privatize/demote; remove --include-heuristic; intent ids cli:demote:<id> and <rule>:demote:<id>. B3 batch_intents on the new engine. B4 delete check/**, app/check_fixes.py, app/privatize.py, oracle scripts, manifest, guards, harness tests; rename check_run2/privatize_run. B5 allow-table: drop check; app = dsl,intents,models,refactor,storage. B6 rename dsl/mutation.py -> mutation_rules.py, differential.py -> engine.py, differential_fix.py -> repairs.py. B7 docstring sweep. B8 port tests via run_dsl_rule fixture; ledger line per retired scenario. B9 dsl-rewrite.md -> historical record + flip ledger entries. B10 architecture.md + CLAUDE.md targeted edits. B11 full gate, no baseline. B12 naming sweep.

Decisions needing explicit approval: remove privatize --include-heuristic; unknown configured rule refuses loudly (was silently skipped); custom rules kept via register_dsl_rule. Execute with task-pipeline mode full in sequential segments (A2-A8, A9-A13, B1-B7, B8, B9-B12), gate between each.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
The 2026-09-01 architecture review deferred seven items to this phase-5 flip; they are recorded in dsl-rewrite.md's phase-5 paragraph. PR #140 (squash-merged as 25a0d31) landed the review fixes that were NOT deferred; only the seven deferred items remain outstanding here.

2026-09-06: plan approved by user. Decisions: remove privatize --include-heuristic (ledger entry); unknown configured rule name refuses loudly (ledger entry); custom rules kept via register_dsl_rule. Execution: task-pipeline full mode in five sequential segments (A2-A8, A9-A13, B1-B7, B8, B9-B12), full gate between each, one PR.

2026-09-06: all five segments landed on claude/task-157-flip (head 25ec168), each gate-green; PR opened. Final oracle evidence recorded before the CLI moved: PASS on 8 targets, old == new on every rule. Gate on head: 3831 tests, ruff, self-lint with no baseline; --strict shows only two pre-existing heuristic findings. Status stays In Progress until the PR is merged.

FINAL SUMMARY (PR #142; this CLI build has no --final-summary flag):
Phase 5 of the DSL rewrite: the self-lint gate runs the DSL engine, every CLI command that ran the old check engine now runs the new one, and the frozen paths, the freeze guards and the differential oracle are deleted in the same PR. Old-engine tests are ported scenario by scenario; every scenario without a new home has a ledger line; dsl-rewrite.md is a historical record.
Changes: storage/baseline.py keyed on (rule, anchor_id) with position-free reference anchors; dsl.Finding.anchor_id; analysis/star_imports.py; dsl/config.py on pypeeker.project; register_dsl_rule overlay; dsl.born_private_surface(); app/check_run.py, app/fix_run.py (fixpoint ported), app/privatize.py; refactor/privatize.py pointwise skips on DEMOTE; allow table without check, app = dsl+intents+models+refactor+storage; dsl renames (mutation_rules, engine, repairs); architecture.md and CLAUDE.md corrected by targeted edits.
Approved behavior changes, all ledgered: unknown configured rule refuses loudly; privatize --include-heuristic removed; delete-symbol fix ids derived; demote intent ids; baseline re-key; duplicate-fix-id and intents-invalid refusals.
Tests: 3831 passing, ruff clean, self-lint zero-baseline; --strict shows only two pre-existing heuristic findings. Final oracle run before the cutover: PASS on 8 targets, old == new on every rule.
Left open, ledgered: fix_run's duplicated planning pass; the treebuild self-lint exemption; dsl/registry.py as the physical framework split. TASK-170/171 depend on this.
<!-- SECTION:NOTES:END -->
