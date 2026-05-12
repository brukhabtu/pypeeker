---
id: TASK-21
title: 'Binder: treat Python builtins as resolved references'
status: Done
assignee:
  - '@claude'
created_date: '2026-05-12 12:51'
updated_date: '2026-05-12 13:03'
labels:
  - binder
  - linter
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
When pypeeker indexes Python source, references to builtins (frozenset, property, len, list, ValueError, ...) are marked resolved=False. This produces hundreds of false positives for any reference-resolution downstream (rename safety, no-unresolved-refs linting). The binder should resolve builtin names to a synthetic 'builtins' module, similar to how 'from __future__ import annotations' should not produce an unresolved reference for 'annotations'.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 from __future__ import annotations does not produce an unresolved reference for 'annotations'
- [x] #2 Tests cover at least: builtin function call (len), builtin type reference (list[int]), builtin exception in raise/except (raise ValueError), property decorator (@property), and __future__ annotations import
- [x] #3 Re-indexing pypeeker's own source and grepping for resolved=false produces no hits on plain builtin names
- [x] #4 Builtin names are introspected from the running interpreter via dir(builtins), filtered to drop dunder-prefixed names; the result is a frozenset built once at module load. No hardcoded list of builtin names anywhere in the binder.
- [x] #5 Any reference whose name appears in that introspected set is marked resolved=True in the index (e.g. len, list, dict, frozenset, property, ValueError, TypeError, OSError, True, False, None, NotImplemented).
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Find where the binder produces Reference objects and decides resolved vs. unresolved (likely binder/references.py).
2. Add a builtins set introspected from dir(builtins), filtered to drop dunder-prefixed names. Build once at module load.
3. In the reference-resolution step, after symbol-table / scope lookup fails, check the builtins set; if hit, mark resolved=True with a synthetic symbol_id like "<builtins>.<name>".
4. Handle from __future__ import annotations: the "annotations" name should not appear as an unresolved reference. Likely covered by the imports binder; verify.
5. Re-index pypeeker's own source and grep .semantic-tool/index for any remaining \"resolved\": false hits that are plain builtin names.
6. Write tests: builtin function call (len), builtin type ref (list[int]), builtin exception (raise ValueError), property decorator (@property), __future__ annotations import.
7. Full pytest run, commit, PR, merge.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- 13 new dedicated tests in test_binder_builtins.py covering: introspection set shape, len/print calls, list[int] subscript, dict annotation, raise ValueError, except KeyError, @property decorator, __future__ annotations import declaration, project symbols shadowing builtins.
- Updated test_binder.py::test_unresolved_reference (print is now resolved as builtin; switched to a genuinely undefined name).
- Updated bare_calls in analysis/calls.py to accept the new <builtins>.X symbol_id shape, otherwise purity tests broke (print/open/eval/etc. are still flagged as impure but via the new resolved-builtin path).
- Full suite: 302 passed, 10 skipped.
- Self-validation: re-indexed src/ and grepped for unresolved refs whose name is in dir(builtins); zero hits.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Resolve Python builtins to a synthetic ``<builtins>.X`` symbol in the index.

## Why
Previously every reference to a builtin (\`len\`, \`list\`, \`frozenset\`, \`ValueError\`, \`property\`, ...) was emitted as \`resolved=False\` because the binder only consulted the lexical scope stack. That made the index unusable for any downstream that filters on resolution status — including the future \`no-unresolved-refs\` linter rule, which would have surfaced hundreds of false positives on first run.

## What changed
- **Introspection, not hardcoding.** \`binder/helpers.py\` exposes \`BUILTIN_NAMES = frozenset(n for n in dir(builtins) if not n.startswith("_"))\` — built once at module load. The list moves with the interpreter; there is no hand-maintained table anywhere.
- **Single resolution policy.** \`references.py\` now has one helper \`_make_name_reference(state, name, kind, node)\` that walks scope → builtins → unresolved. The four call sites (\`visit_identifier\`, \`visit_call\`, both attribute visitors) delegate to it, so every bare identifier reaches the same answer.
- **Builtin refs carry \`symbol_id=<builtins>.<name>\` and \`resolved=True\`.** A local definition with the same name still wins; the builtin is only consulted after the scope-stack miss.
- **\`from __future__ import annotations\`.** Tree-sitter parses this as a \`future_import_statement\` (distinct node type with no \`module_name\` field). Added that type to the binder dispatch and a small \`elif\` in \`visit_import_from_statement\` to fill in \`module_name = "__future__"\`. \`annotations\` is now a proper IMPORT symbol.
- **Purity analysis updated.** \`bare_calls\` in \`analysis/calls.py\` now recognises both the new \`<builtins>.print\` shape and the legacy bare-name shape, so \`print\`/\`open\`/\`exec\`/\`eval\` are still flagged as impure.

## Tests
- 13 new tests in \`tests/test_binder_builtins.py\` (introspection invariants, function/type/exception/decorator resolution, future-annotations import, project-symbol shadowing).
- 1 existing binder test updated (\`print\` is no longer unresolved).
- Full suite: 302 passed, 10 skipped.

## End-to-end check
Re-indexing pypeeker's own source and grepping the JSON for unresolved references whose name is in \`dir(builtins)\` returns **0 hits** — confirms AC #3.

## Follow-up
Unblocks TASK-22 (\`pypeeker check\`): \`no-unresolved-refs\` will now produce signal instead of noise.
<!-- SECTION:FINAL_SUMMARY:END -->
