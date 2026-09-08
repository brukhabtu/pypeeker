---
id: TASK-163
title: 'config coercion: one loud implementation (folds in TASK-162''s coercion half)'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-08-08 19:59'
updated_date: '2026-09-08 04:12'
labels:
  - dsl
  - cleanup
dependencies:
  - TASK-157
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Config-option coercion in the DSL is silent where it should be loud, and duplicated where it should be one implementation. Reproduced on main after the flip (2026-09-07): a project with rules=["require-docstrings"] gets 3 findings; adding an empty [tool.pypeeker.visibility] table drops that to 0 with exit 0, because the injected visibility table empties the rule's enum option set through a silent _enum_set drop. Any consumer declaring that table has require-docstrings silently disabled. A bare-string rules="prefer-tuple" now refuses (the flip made unknown rule names loud) but with a misleading message about unknown expression 'r' — the string is iterated as characters before the refusal. Unparseable enum option values are dropped silently in general.

The same coercion is written five times: dsl/config.py as_str_list, dsl/visibility.py _as_str_list and _selected_kinds, dsl/rules.py _enum_set, dsl/sweeps.py _naming_kinds, beside project.py _as_str_tuple. These were sanctioned copies while the differential oracle was live (the new side could not execute old-engine code); that rationale expired with TASK-157, so TASK-162's coercion half is folded in here: one implementation, in project/ (or a config leaf dsl may import), reached through barrels, and loud. Each finding change gets a divergence-ledger entry in dsl-rewrite.md.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A project declaring [tool.pypeeker.visibility] gets the same require-docstrings findings as one without it (the reproduced 3-vs-0 case is pinned by a test)
- [x] #2 A bare-string rules value and an unparseable enum option value refuse with a structured message naming the option and the accepted values, never a character-split or a silent drop
- [x] #3 String-list and enum-set coercion has exactly one implementation, imported through barrels; the five dsl-local copies are gone
- [x] #4 Every finding change is recorded in dsl-rewrite.md's divergence ledger
- [x] #5 Full gate green
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Full design, steps and risks: backlog/docs "TASK-163 coercion plan" (scout+plan run wf_e0e4b3e8-72c, 2026-09-07).

Root cause of the reproduced 3-vs-0: a KEY COLLISION, not only lax coercion. dsl.read_config injects the project-wide [tool.pypeeker.visibility] table into every rule's options under the key "visibility", which is also require-docstrings' own enum option name; _enum_set then drops every dict key silently and the rule's visibility set becomes empty. Two more bugs of the reported character-split kind were found while probing: src = "pkg" (0 findings, exit 0) and plugins = "lint_rules" (imports module 'l').

Design: one home in project.py (the only module every consumer can reach; project imports nothing). ConfigOptionError(ValueError) with option/value/expected, rendered by cli as click.UsageError (a usage error, not a DSL expression error; JSON envelopes untouched). The injected table moves to a reserved key PROJECT_VISIBILITY_KEY = "project-visibility" that no rule option can collide with; read_config refuses a rule table that writes that key. Three entry points over one core: coerce_str_list(option, raw, allow_scalar=True) — bare string is one value for rule options (frozen convenience) but refused for the top-level list keys rules/plugins/src; coerce_enum_set(option, raw, enum_cls, default, choices) — empty falls back to default, any unparseable value refuses naming the accepted values, written order; coerce_visibility_table(raw) — shared by project._parse_visibility_config and dsl.visibility._visibility_table, refusing non-tables, unknown mode, and unknown keys. Deletes six copies (dsl/config.as_str_list, visibility._as_str_list/_selected_kinds, rules._enum_set, sweeps._naming_kinds, project._as_str_tuple).

Steps 1-16 in the doc: project.py scaffolding; three coercers; rewire dsl/config, dsl/visibility, dsl/rules, dsl/sweeps, dsl/impurity, dsl/mutation_rules, app/privatize (via a dsl barrel re-export of the key); cli catches ConfigOptionError on check and privatize (and demote/promote, see decisions); tests pin the repro and the three refusals, ~25 test sites rename the injected key (two must NOT: they use the rule's own option); ledger entries L1-L6; gate.

Decisions for approval: (a) refuse unknown keys in [tool.pypeeker.visibility] (catches public_roots vs public-roots; the one change no AC demands); (b) demote/promote now refuse on a typo'd visibility table where they previously ran; (c) refusal messages name the option key, not the rule id (rule-id enrichment recorded as a follow-up).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-07: plan approved. Decisions: [tool.pypeeker.visibility] refuses unknown keys; demote/promote refuse on a bad visibility table like check does (cli gains the usage-error catch there too); refusal messages name the option key and accepted values, rule-id enrichment is a follow-up. Executing via task-pipeline full mode on claude/task-163-coercion.

FINAL SUMMARY (PR opened 2026-09-08 from claude/task-163-coercion):
Root cause was a key collision: the injected project-wide visibility table shared the key "visibility" with require-docstrings' own enum option, so the rule's set was coerced from dict keys and went empty. The table now rides under PROJECT_VISIBILITY_KEY (dsl/config.py; kept out of project.py because import-boundaries resolves re-export chains and app may not import project). project.py owns coercion via coerce_str_list, coerce_enum_set and coerce_visibility_table; ConfigOptionError is rendered as a usage error by a group-level CLI catch plus explicit catches on check/privatize/demote/promote. Six copies deleted; two extra character-split sites fixed (src on the indexer path, plugins). Six ledger entries. Gate: 3845 tests, ruff, self-lint zero-baseline; repro 3-vs-3 and seven refusals verified by the orchestrator. Status stays In Progress until merge.
<!-- SECTION:NOTES:END -->
